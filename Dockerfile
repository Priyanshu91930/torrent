# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install system dependencies for lxml / aiohttp
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy bot source code
COPY bot/ ./bot/

# Create runtime directories
RUN mkdir -p cache data logs

# Run as non-root user for security
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

# Health check (optional, for Docker Swarm / Kubernetes)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD python -c "import asyncio, aiohttp; asyncio.run(aiohttp.ClientSession().get('https://api.telegram.org').close())" || exit 1

CMD ["python", "-m", "bot.main"]
