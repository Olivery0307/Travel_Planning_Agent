FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies only
RUN uv sync --frozen --no-dev

# Copy application code
COPY main.py ./
COPY backend/ backend/
COPY static/ static/

# Cloud Run injects PORT env var
ENV PORT=8080
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8080"]
