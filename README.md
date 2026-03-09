# QuantEdge AI Trading Intelligence Platform

> Real-time crypto market intelligence platform generating actionable trading signals across arbitrage, sentiment, liquidity, whale tracking, and anomaly detection.

**Developer:** Bilal Etudaiye-Muhtar — [LinkedIn](https://www.linkedin.com/in/bilal-etudaiye-muhtar-2725a317a)

---

## Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.11, asyncio |
| API | FastAPI + Uvicorn |
| Database | **Neon** (serverless PostgreSQL) |
| Cache | Redis (Railway plugin / Upstash) |
| WebSockets | aiohttp |
| Alerts | Telegram Bot API, Discord Webhooks |
| Deployment | **Railway** |

---

## Deploy to Railway

### Prerequisites

Before deploying, you need accounts/tokens for:

| Service | What you need | Where to get it |
|---|---|---|
| **Neon** | `DATABASE_URL` pooled connection string | [neon.tech](https://neon.tech) → New Project → Connection Details → Pooled |
| **Telegram** | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Message [@BotFather](https://t.me/botfather) → /newbot |
| **Railway** | Account + project | [railway.app](https://railway.app) |
| **Redis** | Auto-provided by Railway plugin | Railway dashboard → Add plugin → Redis |

### Step 1 — Set up Neon

1. Go to [neon.tech](https://neon.tech) and create a free account
2. Create a new project (pick the region closest to your Railway deploy region)
3. Click **Connection Details** → toggle to **Pooled connection**
4. Copy the connection string — it looks like:
   ```
   postgresql://user:password@ep-xxx-xxx.region.aws.neon.tech/neondb?sslmode=require
   ```
5. Save this as `DATABASE_URL` — you'll need it in step 3

> The schema is created automatically on first startup. No manual SQL needed.

### Step 2 — Create Telegram Bot

1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` → follow the prompts → copy your **bot token**
3. Add your bot to the channel/group where you want alerts
4. Get the **chat ID**: forward a message from the group to [@userinfobot](https://t.me/userinfobot)
5. Save `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

### Step 3 — Deploy on Railway

```bash
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Login
railway login

# 3. Create project from your repo
railway init

# 4. Add Redis plugin
railway add redis
# Railway will auto-inject REDIS_URL
```

Then in the Railway dashboard → your service → **Variables**, add:

```
DATABASE_URL        = postgresql://...your neon pooled URL...
TELEGRAM_BOT_TOKEN  = 1234567890:ABCdef...
TELEGRAM_CHAT_ID    = -1001234567890
PLATFORM_ENV        = production
LOG_LEVEL           = INFO
```

```bash
# 5. Deploy
railway up
```

Railway will:
- Build from the Dockerfile
- Run schema migrations against Neon on startup
- Start the API server on the auto-assigned PORT
- Expose your public URL

### Step 4 — Deploy the Pipeline Worker (separate service)

The arbitrage pipeline runs as a background worker — deploy it as a **second Railway service** from the same repo:

1. Railway dashboard → **New Service** → same GitHub repo
2. Set the **Start Command** to:
   ```
   python signals/arbitrage/pipeline.py
   ```
3. Add the same environment variables as the API service
4. Deploy

You'll have two services:
- `api` → public URL (e.g. `https://quantedge-api.railway.app`)
- `pipeline` → background worker (no public port needed)

---

## Local Development

```bash
# Clone repo and install deps
pip install -r requirements.txt

# Copy env file
cp .env.example .env
# Edit .env with your DATABASE_URL, TELEGRAM_BOT_TOKEN, etc.

# Start Redis locally
docker-compose up redis -d

# Run the API
uvicorn api.routes:app --reload --port 8000

# Run the arbitrage pipeline (separate terminal)
python signals/arbitrage/pipeline.py
```

Or run everything with Docker Compose:
```bash
docker-compose up
```

---

## What's Still Needed Before Full Production

The following items need to be completed or configured before the platform is fully production-ready:

### Required from you

| Item | Status | Action |
|---|---|---|
| `DATABASE_URL` from Neon | ⏳ Pending | Create project at neon.tech |
| `TELEGRAM_BOT_TOKEN` | ⏳ Pending | Message @BotFather |
| `TELEGRAM_CHAT_ID` | ⏳ Pending | Add bot to your channel |
| Railway account | ⏳ Pending | Sign up at railway.app |
| Redis (Railway plugin) | ⏳ Pending | Add in Railway dashboard |

### Optional but recommended

| Item | Notes |
|---|---|
| `DISCORD_WEBHOOK_URL` | Add for Discord alert channel |
| `ETHERSCAN_API_KEY` | Needed for Phase 5 whale tracking |
| Custom domain | Configure in Railway → Settings → Domains |
| Railway paid plan | Free tier sleeps after inactivity — upgrade for 24/7 uptime |

### Platform phases not yet wired to Neon persistence

Signal engines are built but their `on_signal` callbacks don't yet call `SignalRepository.save_*()`. To persist signals to Neon, wire the repository into each pipeline's signal callback. Example for arbitrage pipeline (`signals/arbitrage/pipeline.py`):

```python
from data.storage.neon import get_repository

async def _on_arbitrage_signal(self, signal: ArbitrageSignal) -> None:
    # ... existing log + alert dispatch ...
    repo = get_repository()
    await repo.save_arbitrage(signal)   # ← add this line
```

---

## API Reference

Base URL: `https://your-service.railway.app`

```
GET /                              Platform info + developer credits
GET /health                        Health check (used by Railway)
GET /docs                          Interactive Swagger UI

GET /signals/arbitrage             Recent arbitrage signals
    ?limit=50 &symbol=BTC/USDT &min_confidence=0.7

GET /signals/sentiment             Recent sentiment signals
    ?limit=50 &token=ETH

GET /signals/liquidity             Order book wall / imbalance signals
    ?symbol=BTC/USDT &exchange=binance

GET /signals/whales                On-chain whale transfer signals
    ?asset=BTC &min_usd=5000000

GET /signals/all                   All signals, all types
    ?signal_type=arbitrage &limit=100

GET /scanner/arbitrage/live        Live cross-exchange spread scanner
GET /engine/stats                  Pipeline performance metrics
```

---

## Architecture

```
Exchange WebSockets (Binance, Coinbase, Kraken, OKX, Bybit)
          │
          ▼
    ExchangeConnectors
    (async WS, auto-reconnect, exponential backoff)
          │
          ▼
    ArbitrageEngine
    (O(n²) pair scan, 4-factor confidence model)
          │
          ├──▶ Neon PostgreSQL  (persistent signal history)
          ├──▶ Redis            (live price cache, deduplication)
          └──▶ AlertDispatcher
               ├── Telegram Bot
               └── Discord Webhook
          │
          ▼
    FastAPI REST API
    (signal queries, live scanner, health check)
```

---

## Directory Structure

```
trading-platform/
├── config/settings.py              Typed config — NeonConfig, TelegramConfig, etc.
├── core/models.py                  Domain models — PriceTick, ArbitrageSignal, etc.
├── data/
│   ├── connectors/
│   │   └── exchange_connectors.py  Binance, Coinbase, Kraken WS connectors
│   └── storage/
│       └── neon.py                 Neon async pool, migrations, repositories
├── signals/
│   ├── arbitrage/
│   │   ├── engine.py               Spread detection + confidence scoring
│   │   └── pipeline.py             Phase 1 MVP orchestrator
│   ├── sentiment/engine.py         Social mention + NLP pipeline
│   └── liquidity_whale.py          Order book + on-chain signal engines
├── alerts/dispatcher.py            Telegram + Discord alert routing
├── api/routes.py                   FastAPI REST endpoints
├── dashboard/TradingDashboard.jsx  React real-time dashboard
├── utils/logging.py                JSON structured logging
├── main.py                         Full platform orchestrator
├── Dockerfile                      Railway/Docker build
├── railway.toml                    Railway deployment config
├── Procfile                        Process definitions
├── .env.example                    Environment variable reference
└── docker-compose.yml              Local dev (Redis, optional Kafka)
```
