# Deploying QuantEdge to Railway via GitHub

Follow these steps exactly. Takes about 10 minutes.

---

## Step 1 — Push code to GitHub

If you haven't already, create a GitHub repo and push this project:

```bash
# Inside the trading-platform folder
git init
git add .
git commit -m "Initial commit — QuantEdge AI Trading Platform"

# Create a new repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/quantedge-platform.git
git branch -M main
git push -u origin main
```

---

## Step 2 — Create Neon database

1. Go to **[neon.tech](https://neon.tech)** → Sign up (free)
2. Click **New Project** → give it a name → select a region
3. Once created, click **Connection Details**
4. Switch the toggle from **Direct** to **Pooled connection**
5. Copy the full connection string — it looks like:
   ```
   postgresql://neondb_owner:xxxx@ep-xxx-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
6. Save it — you'll paste it into Railway in Step 4

---

## Step 3 — Create Telegram Bot

1. Open Telegram → search **@BotFather** → start chat
2. Send `/newbot` → enter a name (e.g. `QuantEdge Alerts`) → enter a username
3. BotFather replies with your **bot token** — save it:
   ```
   1234567890:ABCdefGHIjklMNO-pqrSTUvwxYZ
   ```
4. Create a Telegram channel or group for your alerts
5. Add your bot to that channel/group as an **Admin**
6. Get the **chat ID**:
   - Forward any message from your channel to **@userinfobot**
   - It will reply with the chat ID (starts with `-100...` for channels)
   - Save that number

---

## Step 4 — Set up Railway

1. Go to **[railway.app](https://railway.app)** → sign up / log in
2. Click **New Project**
3. Select **Deploy from GitHub repo**
4. Connect your GitHub account if not already connected
5. Select your `quantedge-platform` repository
6. Railway will detect the `Dockerfile` and start building

---

## Step 5 — Add Redis to Railway

1. In your Railway project dashboard, click **+ New**
2. Select **Database** → **Add Redis**
3. Railway creates a Redis instance and automatically injects `REDIS_URL` into your service

---

## Step 6 — Add environment variables

In Railway dashboard → click your **web service** → **Variables** tab → add each one:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Your Neon **pooled** connection string from Step 2 |
| `TELEGRAM_BOT_TOKEN` | Your bot token from Step 3 |
| `TELEGRAM_CHAT_ID` | Your channel/group chat ID from Step 3 |
| `PLATFORM_ENV` | `production` |
| `LOG_LEVEL` | `INFO` |

> `PORT` and `REDIS_URL` are injected by Railway automatically — do NOT set them manually.

After adding variables, Railway will automatically redeploy.

---

## Step 7 — Verify deployment

1. Railway will show a **green checkmark** when healthy
2. Click **View Logs** to confirm you see:
   ```
   Neon connection pool ready
   Neon schema migrations applied
   Uvicorn running on http://0.0.0.0:XXXX
   ```
3. Click the **public URL** Railway generated (e.g. `https://quantedge-platform-production.up.railway.app`)
4. Visit `https://your-url.railway.app/docs` — you should see the Swagger UI

---

## Step 8 — Deploy the pipeline worker (optional but recommended)

The arbitrage signal pipeline runs as a separate background service:

1. In Railway dashboard → **+ New** → **GitHub Repo** → same repo
2. Click the new service → **Settings** → **Start Command**, set to:
   ```
   python signals/arbitrage/pipeline.py
   ```
3. Add the same environment variables as the API service (Step 6)
4. This worker will connect to exchanges via WebSocket and emit signals to Telegram

---

## After deployment

| What | Where |
|---|---|
| API docs | `https://your-url.railway.app/docs` |
| Health check | `https://your-url.railway.app/health` |
| Live spreads | `https://your-url.railway.app/scanner/arbitrage/live` |
| Arbitrage signals | `https://your-url.railway.app/signals/arbitrage` |
| Telegram alerts | Your Telegram channel (once pipeline is running) |

---

## Updating the app

Any `git push` to `main` triggers an automatic redeploy on Railway:

```bash
# Make changes, then:
git add .
git commit -m "Your change description"
git push origin main
# Railway detects the push and redeploys automatically
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails | Check **Build Logs** in Railway — usually a missing dependency |
| `DATABASE_URL` error | Make sure you're using the **Pooled** Neon URL, not the Direct one |
| No Telegram alerts | Confirm the bot is added as Admin to your channel; verify `TELEGRAM_CHAT_ID` starts with `-100` for channels |
| Health check failing | Check **Deploy Logs** — the `/health` endpoint must return 200 |
| `REDIS_URL not found` | Make sure you added the Redis plugin in Step 5 |
