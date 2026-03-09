FROM python:3.11-slim

# System dependencies for asyncpg (C extension) and curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Non-root user for security
RUN addgroup --system quant \
    && adduser --system --ingroup quant quant \
    && chown -R quant:quant /app
USER quant

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000

EXPOSE $PORT

# Railway overrides CMD via railway.toml startCommand
CMD ["uvicorn", "api.routes:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--loop", "asyncio"]
