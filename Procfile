# ─── Procfile — Railway / Heroku process definitions ─────────────────────────
#
# Railway runs ONE process per service deployment.
# Create separate Railway services for each process type.
#
# Service 1 (api):      Deploy with start command from railway.toml
# Service 2 (pipeline): Set startCommand to the 'pipeline' entry below
#
# To run the pipeline worker as a separate Railway service:
#   startCommand = "python signals/arbitrage/pipeline.py"

web: uvicorn api.routes:app --host 0.0.0.0 --port $PORT --workers 2 --loop asyncio
pipeline: python signals/arbitrage/pipeline.py
worker: python main.py
