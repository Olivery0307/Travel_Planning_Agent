"""Utilities for generating Google Maps route URLs and QR codes from itinerary slots."""

from __future__ import annotations

import base64
import io
from urllib.parse import quote


def build_maps_route_url(stop_names: list[str]) -> str:
    """Build a Google Maps multi-stop directions URL from an ordered list of place names.

    Returns a URL like:
      https://www.google.com/maps/dir/Stop+A/Stop+B/Stop+C/
    which opens a navigable route on desktop and mobile.
    """
    if not stop_names:
        return ""
    encoded = "/".join(quote(name, safe="") for name in stop_names)
    return f"https://www.google.com/maps/dir/{encoded}/"


def build_place_maps_url(place_id: str) -> str:
    """Build a Google Maps URL for a specific place by its place_id."""
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def qr_code_base64(url: str, box_size: int = 4, border: int = 2) -> str:
    """Generate a QR code PNG for *url* and return it as a base64-encoded data URI.

    The returned string is ready to use directly in an <img src="..."> tag.
    Requires the Pillow package (included in project dependencies).
    """
    try:
        import qrcode  # type: ignore
        from qrcode.image.pil import PilImage  # type: ignore

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box_size,
            border=border,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(image_factory=PilImage)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""
