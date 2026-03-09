FROM python:3.11-slim

# System deps for asyncpg (C extension) and curl (health check)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies — slim requirements only (no torch/ML)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Non-root user
RUN addgroup --system quant \
    && adduser --system --ingroup quant quant \
    && chown -R quant:quant /app
USER quant

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# PORT is injected by Railway — default for local dev only
ENV PORT=8000

EXPOSE $PORT

# Health check — Railway also does this via railway.toml healthcheckPath
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["sh", "-c", "uvicorn api.routes:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop asyncio"]
