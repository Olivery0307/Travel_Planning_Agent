"""Deterministic replan tools — no LLM, no hallucination.

Pipeline:
  resolve_slots   → find which itinerary lines match the affected slots
  find_candidates → cache-first place lookup for replacements
  apply_swap      → regex-patch the itinerary text and emit ItineraryDelta
"""

from __future__ import annotations

import json
import logging
import re
from agents import Runner, RunContextWrapper, function_tool
from pydantic import BaseModel

from backend.models.disruption import (
    AffectedSlot,
    DailyCost,
    DeltaSlot,
    DisruptionEvent,
    DisruptionRequest,
    ItineraryDelta,
)
from backend.tools.cache import get_cache

logger = logging.getLogger(__name__)

# ── staging store: passes data between tools without complex parameters ────────
# Keyed by session_id. Cleared at the start of each replan run by parse_disruption.
_replan_staging: dict[str, dict] = {}
# Structure: {
#   session_id: {
#     "resolved": [...],
#     "candidates": [[...], ...],
#     "opportunity_venue": str,   # set when disruption_type == "opportunity"
#   }
# }

# ── helpers ───────────────────────────────────────────────────────────────────

_PERIOD_EMOJI = {
    "morning":   ["🌅", "Morning"],
    "afternoon": ["🌇", "Afternoon"],
    "evening":   ["🌆", "Evening", "Dinner", "🍽"],
}

_PERIOD_RE = re.compile(
    r"^\s*[-•*]?\s*(?:🌅|🌇|🌆|🍽️?)?\s*"
    r"(?P<period>Morning|Afternoon|Evening|Lunch|Dinner)[:\s–—]+",
    re.IGNORECASE,
)

_DAY_RE = re.compile(r"^\*\*Day\s+(\d+)", re.MULTILINE)
_COST_RE = re.compile(r"~?\$[\d,]+(?:/person)?")
# Matches the budget breakdown line: 💰 Day total: ~$X/person (...)
_BUDGET_LINE_RE = re.compile(r"^\s*[-•*]?\s*💰\s*Day total:", re.IGNORECASE)
# Matches the Maps navigate line: [🗺 Navigate Day N on Google Maps](url)
_NAV_LINE_RE = re.compile(r"^\[🗺 Navigate Day \d+ on Google Maps\]", re.IGNORECASE)
# Matches the QR marker line: [QR_DAY_N](url)
_QR_LINE_RE = re.compile(r"^\[QR_DAY_\d+\]", re.IGNORECASE)
# Extracts Maps search addresses from slot lines: [📍 Maps](url?query=ADDR)
_MAPS_ADDR_RE = re.compile(
    r"\[📍 Maps\]\(https://www\.google\.com/maps/search/\?api=1&query=([^)]+)\)"
)


def _lines_for_day(text: str, day: int) -> list[tuple[int, str]]:
    """Return (line_index, line_content) for every line belonging to `day`."""
    lines = text.splitlines()
    result: list[tuple[int, str]] = []
    current_day = 0
    for i, line in enumerate(lines):
        m = _DAY_RE.match(line)
        if m:
            current_day = int(m.group(1))
            continue
        if current_day == day:
            result.append((i, line))
        elif current_day > day:
            break
    return result


def _period_of_line(line: str) -> str | None:
    """Detect which period (morning/afternoon/evening) a slot line belongs to."""
    low = line.lower()
    if any(e in line for e in ["🌅"]) or "morning" in low:
        return "morning"
    if any(e in line for e in ["🌇"]) or "afternoon" in low or "lunch" in low:
        return "afternoon"
    if any(e in line for e in ["🌆", "🍽"]) or "evening" in low or "dinner" in low:
        return "evening"
    return None


_PREFIX_RE = re.compile(r'^\w+\s+at\s+', re.IGNORECASE)  # "Dinner at ", "Shopping at ", etc.


def _strip_verb_prefix(name: str) -> str:
    """Strip descriptive prefixes like 'Dinner at', 'Shopping at', 'Dining at' from a name."""
    return _PREFIX_RE.sub('', name).strip()


def _extract_place_name(line: str) -> str:
    """Pull the venue name from a slot line (bold or plain text after period keyword)."""
    # Try **Bold Name** — strip any verb prefix that got bolded too
    m = re.search(r"\*\*([^*]+)\*\*", line)
    if m:
        return _strip_verb_prefix(m.group(1).strip())
    # Try text after period keyword
    m = re.search(
        r"(?:Morning|Afternoon|Evening|Lunch|Dinner)[:\s–—]+([^(~\[📍\n]+)",
        line, re.IGNORECASE,
    )
    if m:
        name = m.group(1).strip().rstrip(".,—–")
        return _strip_verb_prefix(name)
    return ""


def _extract_cost(line: str) -> float:
    m = _COST_RE.search(line)
    if m:
        raw = re.sub(r"[^0-9]", "", m.group())
        return float(raw) if raw else 0.0
    return 0.0


def _build_maps_link(name: str, place_id: str = "", city: str = "") -> str:
    if not name:
        return ""
    enc_name = name.replace(" ", "+").replace(",", "%2C")
    if place_id:
        return f"[📍 Maps](https://www.google.com/maps/search/?api=1&query={enc_name}&query_place_id={place_id})"
    suffix = f"+{city.replace(' ', '+')}" if city else ""
    return f"[📍 Maps](https://www.google.com/maps/search/?api=1&query={enc_name}{suffix})"


def _format_slot_line(period: str, place: dict, category: str) -> str:
    """Format a replacement slot line matching the itinerary style."""
    name = place.get("name", "")
    cost = place.get("estimated_cost_usd") or place.get("price_level", 0)
    website = place.get("website", "")
    maps = _build_maps_link(name, place.get("place_id", ""), place.get("city", ""))

    emoji_map = {"morning": "🌅", "afternoon": "🌇", "evening": "🌆"}
    emoji = emoji_map.get(period, "🌅")

    if category == "dining":
        label = "Lunch" if period == "afternoon" else "Dinner"
        cost_str = f"~${int(cost)}/person" if cost else ""
        line = f"- 🍽️ {label}: {name}"
        if cost_str:
            line += f" ({cost_str})"
        if maps:
            line += f" {maps}"
        return line
    elif category == "lodging":
        cost_str = f"~${int(cost)}/night" if cost else ""
        line = f"- 🏨 {name}"
        if cost_str:
            line += f" (all nights, {cost_str})"
        if website:
            line += f" [Book / Official Site]({website})"
        return line
    else:
        duration = place.get("duration_minutes", 90)
        cost_str = f"${int(cost)}/person" if cost else "free"
        booking_url = place.get("website", "") if place.get("booking_required") else ""
        line = f"- {emoji} {period.capitalize()}: {name} ({duration // 60}h, {cost_str})"
        if booking_url:
            line += f" [Book Tickets]({booking_url})"
        elif maps:
            line += f" {maps}"
        return line


def _rebuild_day_nav_and_budget(lines: list[str], day: int) -> None:
    """After slot patches, rebuild the 💰 budget line and 🗺 nav/QR lines for a day in-place."""
    day_line_indices = []
    current_day = 0
    for i, line in enumerate(lines):
        m = _DAY_RE.match(line)
        if m:
            current_day = int(m.group(1))
            continue
        if current_day == day:
            day_line_indices.append(i)
        elif current_day > day:
            break

    if not day_line_indices:
        return

    # ── Rebuild budget line ────────────────────────────────────────────────────
    # Sum costs from all slot lines in this day
    total_cost = 0.0
    for i in day_line_indices:
        line = lines[i]
        if _BUDGET_LINE_RE.match(line):
            continue  # skip existing budget line
        cost = _extract_cost(line)
        total_cost += cost

    budget_idx = next(
        (i for i in day_line_indices if _BUDGET_LINE_RE.match(lines[i])), None
    )
    if budget_idx is not None and total_cost > 0:
        # Replace just the leading cost figure, preserve the breakdown text if possible
        new_budget = f"- 💰 Day total: ~${int(total_cost)}/person"
        lines[budget_idx] = new_budget

    # ── Rebuild nav and QR lines ───────────────────────────────────────────────
    # Collect addresses from Maps links in slot lines (skip budget/nav/QR lines)
    addresses: list[str] = []
    for i in day_line_indices:
        line = lines[i]
        if _BUDGET_LINE_RE.match(line) or _NAV_LINE_RE.match(line) or _QR_LINE_RE.match(line):
            continue
        for enc_addr in _MAPS_ADDR_RE.findall(line):
            addresses.append(enc_addr)  # already URL-encoded

    nav_idx = next(
        (i for i in day_line_indices if _NAV_LINE_RE.match(lines[i])), None
    )
    qr_idx = next(
        (i for i in day_line_indices if _QR_LINE_RE.match(lines[i])), None
    )

    if addresses and nav_idx is not None:
        route_url = "https://www.google.com/maps/dir/" + "/".join(addresses) + "/"
        lines[nav_idx] = f"[🗺 Navigate Day {day} on Google Maps]({route_url})"
        if qr_idx is not None:
            lines[qr_idx] = f"[QR_DAY_{day}]({route_url})"


# ── module-level parser reference (set by build_replanner_agent) ─────────────
_disruption_parser_agent: "Any | None" = None


# ── tool 0: parse_disruption ──────────────────────────────────────────────────

@function_tool
async def parse_disruption(ctx: RunContextWrapper, user_message: str) -> str:
    """Parse a disruption message into a structured DisruptionRequest JSON.

    Call this FIRST before resolve_slots. Pass the user's raw message.
    Returns a JSON string with: disruption_type, affected_slots, locked_slot_keys,
      new_budget_per_day, special_instructions, reasoning.
    """
    if _disruption_parser_agent is None:
        return json.dumps({"error": "DisruptionParser not initialized"})

    # Append locked slots from context so the parser sees them
    message = user_message
    if ctx.context and ctx.context.locked_slots:
        locked_str = ", ".join(ctx.context.locked_slots)
        message += f"\n\nLocked slots: {locked_str}"

    try:
        result = await Runner.run(_disruption_parser_agent, message)
        parsed: DisruptionRequest = result.final_output
        # Clear staging for this session so previous replan data doesn't bleed in
        sid = ctx.context.session_id if ctx.context else "default"
        _replan_staging[sid] = {}

        # For opportunity disruptions, store the venue name so apply_swap can insert it
        # directly without a cache lookup
        if parsed.disruption_type.value == "opportunity":
            opp_venue = ""
            for slot in parsed.affected_slots:
                if slot.venue_name:
                    opp_venue = slot.venue_name
                    break
            if not opp_venue and parsed.special_instructions:
                opp_venue = parsed.special_instructions
            _replan_staging[sid]["opportunity_venue"] = opp_venue

        return parsed.model_dump_json()
    except Exception as e:
        logger.error("parse_disruption failed: %s", e)
        return json.dumps({"error": str(e)})


# ── tool 1: resolve_slots ─────────────────────────────────────────────────────

class _AffectedSlotInput(BaseModel):
    day_number: int
    period: str = ""
    venue_name: str = ""
    category: str = "activity"


@function_tool
def resolve_slots(
    ctx: RunContextWrapper,
    affected_slots: list[_AffectedSlotInput],
) -> str:
    """Find the actual itinerary lines matching each affected slot.

    Returns JSON string: list of resolved slot dicts with
      day_number, period, line_index, original_line, place_name, category, cost_usd.
    line_index=-1 means the slot wasn't found (still included so pipeline can handle it).
    """
    if not ctx.context or not ctx.context.itinerary_json:
        return json.dumps([{"error": "No itinerary in context"}])

    try:
        itin = json.loads(ctx.context.itinerary_json)
        text: str = itin.get("text", "")
    except Exception:
        return json.dumps([{"error": "Failed to parse itinerary JSON"}])

    resolved = []
    for slot in affected_slots:
        day = slot.day_number
        period = (slot.period or "").lower()
        venue_hint = (slot.venue_name or "").lower()
        category = slot.category or "activity"

        day_lines = _lines_for_day(text, day)
        best_idx = -1
        best_line = ""
        best_name = ""
        best_cost = 0.0

        # Normalize raw period keyword to match _period_of_line() output:
        # parse_disruption may return "dinner"/"lunch" but _period_of_line returns "evening"/"afternoon"
        _PERIOD_NORM = {"dinner": "evening", "lunch": "afternoon", "breakfast": "afternoon",
                        "hotel": "lodging"}
        period_canonical = _PERIOD_NORM.get(period, period)

        for line_idx, line in day_lines:
            if not line.strip().startswith(("-", "•", "*", "🌅", "🌇", "🌆", "🍽", "🏨")):
                continue
            line_period = _period_of_line(line)
            # Match by normalized period; lodging lines return None from _period_of_line
            # so match hotel slots by emoji instead
            period_match = (
                (not period)
                or (line_period == period_canonical)
                or (period_canonical == "lodging" and "🏨" in line)
            )
            # Strip verb prefix from venue_hint before comparing (handles "Dinner at Name")
            clean_hint = _strip_verb_prefix(venue_hint) if venue_hint else ""
            name_in_line = bool(
                (clean_hint and clean_hint in line.lower())
                or (venue_hint and venue_hint in line.lower())
            )
            if period_match:
                if name_in_line:
                    # Exact period+name match — take it immediately
                    best_idx = line_idx
                    best_line = line
                    best_name = _extract_place_name(line)
                    best_cost = _extract_cost(line)
                    break
                elif not venue_hint or best_idx == -1:
                    # Period match with no name hint, or no better match found yet — use as fallback
                    best_idx = line_idx
                    best_line = line
                    best_name = _extract_place_name(line)
                    best_cost = _extract_cost(line)

        resolved.append({
            "day_number": day,
            "period": period or _period_of_line(best_line) or "morning",
            "line_index": best_idx,
            "original_line": best_line,
            "place_name": best_name,
            "category": category,
            "cost_usd": best_cost,
        })
        logger.info(
            "resolve_slots day=%d period=%s venue_hint=%r → line=%d name=%r",
            day, period, venue_hint, best_idx, best_name,
        )

    sid = ctx.context.session_id if ctx.context else "default"
    _replan_staging.setdefault(sid, {})["resolved"] = resolved
    return json.dumps(resolved)


# ── tool 2: find_candidates_parallel ─────────────────────────────────────────


class _SlotQuery(BaseModel):
    """One slot's lookup spec — passed as part of find_candidates_parallel."""
    category: str = "activity"
    extra_exclude_names: list[str] = []


def _lookup_for_slot(
    ctx_context: "Any",
    city: str,
    category: str,
    global_exclude: list[str],
    extra_exclude: list[str],
    special_instructions: str,
    max_results: int,
) -> list[dict]:
    """Synchronous cache lookup for one slot. Called in parallel via gather."""
    cache = get_cache()
    cache_cat = {"activity": "attraction", "dining": "restaurant", "lodging": "lodging"}.get(
        category, "attraction"
    )

    query_parts = [city, cache_cat]
    instr_lower = special_instructions.lower()
    if "indoor" in instr_lower:
        query_parts.append("indoor museum gallery")
    elif "outdoor" in instr_lower:
        query_parts.append("outdoor park")
    if "vegetarian" in instr_lower:
        query_parts.append("vegetarian")
    query = " ".join(query_parts)

    exclude_lower = {n.lower() for n in (global_exclude + extra_exclude)}

    results = cache.search(city, cache_cat, query, max_results=max_results * 3)
    filtered = [p for p in results if p.get("name", "").lower() not in exclude_lower]

    if "indoor" in instr_lower:
        indoor = [p for p in filtered if not p.get("outdoor_suitable", True)]
        filtered = indoor if indoor else filtered
    if "outdoor" in instr_lower:
        outdoor = [p for p in filtered if p.get("outdoor_suitable", False)]
        filtered = outdoor if outdoor else filtered

    if len(filtered) < 2 and ctx_context:
        pool_key = {"activity": "activities", "dining": "dining", "lodging": "lodging"}.get(
            category, "activities"
        )
        pool = ctx_context.candidate_pool.get(pool_key, [])
        pool_filtered = [p for p in pool if p.get("name", "").lower() not in exclude_lower]
        filtered = pool_filtered or filtered

    return filtered[:max_results]


@function_tool
async def find_candidates_parallel(
    ctx: RunContextWrapper,
    city: str,
    resolved_slots_json: str,
    global_exclude_names: list[str],
    special_instructions: str = "",
    max_results: int = 5,
) -> str:
    """Find replacement candidates for ALL resolved slots in parallel.

    city: destination city (e.g. "Rome")
    resolved_slots_json: JSON string output of resolve_slots
    global_exclude_names: venue names already used anywhere in the itinerary
    special_instructions: constraints applied to all slots (e.g. "indoor only")
    max_results: candidates per slot

    Returns JSON string: list of candidate lists, one per slot.
    candidates_per_slot[i] = candidates for resolved_slots[i].
    Guarantees no two slots receive the same candidate as their first choice.
    """
    try:
        resolved_slots: list[dict] = json.loads(resolved_slots_json)
    except Exception as e:
        return json.dumps({"error": f"Failed to parse resolved_slots_json: {e}"})

    ctx_context = ctx.context

    # cache.search is pure sync/in-memory — no I/O, safe to call directly
    def _lookup(slot: dict) -> list[dict]:
        category = slot.get("category", "activity")
        extra_exclude = [slot.get("place_name", "")]
        return _lookup_for_slot(
            ctx_context, city, category,
            global_exclude_names, extra_exclude,
            special_instructions, max_results,
        )

    results_per_slot = [_lookup(s) for s in resolved_slots]

    # Dedup across slots: track names already assigned as first choice
    # so two dining slots don't get the same restaurant
    assigned: set[str] = set()
    deduplicated: list[list[dict]] = []

    for candidates in results_per_slot:
        # Find first candidate not already assigned
        available = [c for c in candidates if c.get("name", "").lower() not in assigned]
        if available:
            assigned.add(available[0].get("name", "").lower())
        deduplicated.append(available if available else candidates)

    logger.info(
        "find_candidates_parallel city=%r slots=%d → %s",
        city, len(resolved_slots),
        [len(c) for c in deduplicated],
    )
    sid = ctx.context.session_id if ctx.context else "default"
    _replan_staging.setdefault(sid, {})["candidates"] = deduplicated
    return f"Found candidates for {len(resolved_slots)} slot(s): {[len(c) for c in deduplicated]} options each. Call apply_swap next."


# ── tool 3: apply_swap ────────────────────────────────────────────────────────

@function_tool
def apply_swap(
    ctx: RunContextWrapper,
    disruption_type: str,
    reasoning: str,
    locked_slot_keys: list[str],
) -> str:
    """Patch the itinerary text and write ItineraryDelta to context.

    Reads resolved slots and candidates from the staging store populated by
    resolve_slots and find_candidates_parallel. Call those two tools first.

    disruption_type: from parse_disruption (e.g. "venue_closed", "health", "weather")
    reasoning: from parse_disruption
    locked_slot_keys: from parse_disruption — these slots are never touched

    Returns a plain-English summary of what was swapped.
    """
    if not ctx.context or not ctx.context.itinerary_json:
        return "Error: No itinerary in context."

    sid = ctx.context.session_id if ctx.context else "default"
    staging = _replan_staging.get(sid, {})
    resolved_slots: list[dict] = staging.get("resolved", [])
    candidates_per_slot: list[list[dict]] = staging.get("candidates", [])
    opportunity_venue: str = staging.get("opportunity_venue", "")

    if not resolved_slots:
        return "Error: resolve_slots must be called before apply_swap."

    # Pad candidates list if shorter than resolved_slots
    while len(candidates_per_slot) < len(resolved_slots):
        candidates_per_slot.append([])

    try:
        itin = json.loads(ctx.context.itinerary_json)
        text: str = itin.get("text", "")
        version: int = itin.get("version", 1)
    except Exception as e:
        return json.dumps({"success": False, "message": f"Failed to parse itinerary: {e}"})

    lines = text.splitlines()
    changed_slots: list[DeltaSlot] = []
    removed_slots: list[DeltaSlot] = []
    affected_days: set[int] = set()
    new_costs: list[DailyCost] = []

    locked = set(locked_slot_keys)
    used_names: set[str] = set()  # track names already assigned this run

    for slot, candidates in zip(resolved_slots, candidates_per_slot):
        day = slot.get("day_number", 1)
        period = slot.get("period", "morning")
        slot_key = f"day{day}_{period}"
        category = slot.get("category", "activity")

        if slot_key in locked:
            logger.info("apply_swap: skipping locked slot %s", slot_key)
            continue

        line_idx = slot.get("line_index", -1)
        orig_name = slot.get("place_name", "")
        orig_cost = slot.get("cost_usd", 0.0)

        # ── Opportunity: insert user-specified venue directly (once only) ───────
        if disruption_type == "opportunity" and opportunity_venue:
            # Guard: only insert the venue once even if parser produced multiple slots
            already_inserted = any(
                s.place_name == opportunity_venue for s in changed_slots
            )
            if already_inserted:
                logger.info("apply_swap: opportunity venue already inserted, skipping slot %s", slot_key)
                continue
            emoji_map = {"morning": "🌅", "afternoon": "🌇", "evening": "🌆"}
            emoji = emoji_map.get(period, "🌆")
            new_line = (
                f"- {emoji} {period.capitalize()}: {opportunity_venue} "
                f"(see your tickets for details)"
            )
            if orig_name:
                removed_slots.append(DeltaSlot(
                    day_number=day, period=period,
                    place_name=orig_name, category=category, cost_usd=orig_cost,
                ))
            if line_idx >= 0:
                lines[line_idx] = new_line
            else:
                for i, line in enumerate(lines):
                    m = _DAY_RE.match(line)
                    if m and int(m.group(1)) == day:
                        lines.insert(i + 1, new_line)
                        break
            changed_slots.append(DeltaSlot(
                day_number=day, period=period,
                place_name=opportunity_venue, category=category,
                notes="opportunity insertion",
            ))
            affected_days.add(day)
            continue

        # Record removal
        if orig_name:
            removed_slots.append(DeltaSlot(
                day_number=day,
                period=period,
                place_name=orig_name,
                category=category,
                cost_usd=orig_cost,
            ))

        # ── Health/sick day: lighten activity slots; keep dining slots intact ─
        if disruption_type == "health":
            if category == "dining":
                # Keep the meal — sick traveler still needs to eat
                removed_slots.pop()  # undo the removal we just recorded
                logger.info("apply_swap: health disruption — keeping dining slot %s", slot_key)
                continue
            if not candidates:
                # Replace activity with rest note
                if line_idx >= 0:
                    lines[line_idx] = f"- 🛌 {period.capitalize()}: Rest — light activity only"
                affected_days.add(day)
                continue

        if not candidates:
            logger.warning("apply_swap: no candidates for slot %s, keeping original", slot_key)
            continue

        # Pick first candidate not already used this replan run
        new_place = next(
            (c for c in candidates if c.get("name", "").lower() not in used_names),
            candidates[0],
        )
        used_names.add(new_place.get("name", "").lower())
        new_line = _format_slot_line(period, new_place, category)

        if line_idx >= 0:
            lines[line_idx] = new_line
        else:
            logger.warning("apply_swap: line_index=-1 for slot %s, appending", slot_key)
            # Find the day header and append after it
            for i, line in enumerate(lines):
                m = _DAY_RE.match(line)
                if m and int(m.group(1)) == day:
                    lines.insert(i + 1, new_line)
                    break

        changed_slots.append(DeltaSlot(
            day_number=day,
            period=period,
            place_name=new_place.get("name", ""),
            place_id=new_place.get("place_id", ""),
            category=category,
            address=new_place.get("address", ""),
            cost_usd=float(new_place.get("estimated_cost_usd") or new_place.get("price_level") or 0),
            booking_url=new_place.get("website", ""),
            duration_minutes=new_place.get("duration_minutes", 90),
            notes="replacement",
        ))
        affected_days.add(day)

    # ── Rebuild budget + nav/QR lines for every affected day ─────────────────
    for day in affected_days:
        _rebuild_day_nav_and_budget(lines, day)

    # Write patched text back to context
    updated_text = "\n".join(lines)
    ctx.context.itinerary_json = json.dumps({
        "text": updated_text,
        "version": version + 1,
    })

    # Build and store ItineraryDelta
    # Use the minimum day number across all resolved slots as the disruption day
    disruption_day = min(
        (s.get("day_number", 1) for s in resolved_slots), default=1
    )
    disruption_period = next(
        (s.get("period", "") for s in resolved_slots if s.get("day_number") == disruption_day),
        "",
    )

    # Always include the disruption's own day — even if its slot was locked/unchanged
    affected_days.add(disruption_day)

    delta = ItineraryDelta(
        disruption=DisruptionEvent(
            day_number=disruption_day,
            period=disruption_period,
            description=reasoning,
            disruption_type=disruption_type,
        ),
        affected_days=sorted(affected_days),
        changed_slots=changed_slots,
        removed_slots=removed_slots,
        reasoning=reasoning,
        new_daily_costs=new_costs,
    )

    ctx.context.pending_delta = delta.model_dump_json()
    ctx.context.save()

    # Clear staging
    _replan_staging.pop(sid, None)

    logger.info(
        "apply_swap: %d changed, %d removed, days=%s",
        len(changed_slots), len(removed_slots), sorted(affected_days),
    )

    if not changed_slots and not removed_slots:
        return "No slots were changed — all affected slots may be locked or had no candidates."

    changes = ", ".join(
        f"Day {s.day_number} {s.period}: {s.place_name}" for s in changed_slots
    )
    removals = ", ".join(
        f"Day {s.day_number} {s.period}: {s.place_name}" for s in removed_slots
    )
    return (
        f"Done. Swapped {len(changed_slots)} slot(s) across day(s) {sorted(affected_days)}. "
        f"Replaced: {removals}. New: {changes}."
    )
