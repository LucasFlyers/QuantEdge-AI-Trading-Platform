FROM python:3.11-slim

# System deps for asyncpg (C extension) and curl (health check)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Pre-download FinBERT model at build time so first startup is instant.
# Model is cached to /app/.cache/huggingface and owned by quant user.
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from transformers import AutoTokenizer, AutoModelForSequenceClassification; \
    AutoTokenizer.from_pretrained('ProsusAI/finbert'); \
    AutoModelForSequenceClassification.from_pretrained('ProsusAI/finbert'); \
    print('FinBERT cached successfully')"

# Non-root user
RUN addgroup --system quant \
    && adduser --system --ingroup quant quant \
    && chown -R quant:quant /app
USER quant

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface
ENV HF_HOME=/app/.cache/huggingface
# PORT is injected by Railway — default for local dev only
ENV PORT=8000

EXPOSE $PORT

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["sh", "-c", "uvicorn api.routes:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop asyncio"]
