"""
Platform Configuration — Production Settings
Manage all env-based config in a single typed interface.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import os


@dataclass
class ExchangeConfig:
    name: str
    ws_url: str
    rest_url: str
    fee_maker: float
    fee_taker: float
    min_order_size: float
    supported_pairs: List[str]


@dataclass
class RedisConfig:
    host: str = os.getenv("REDIS_HOST", "localhost")
    port: int = int(os.getenv("REDIS_PORT", "6379"))
    db: int = int(os.getenv("REDIS_DB", "0"))
    password: Optional[str] = os.getenv("REDIS_PASSWORD")
    max_connections: int = 50
    socket_timeout: float = 5.0

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


@dataclass
class NeonConfig:
    """
    Neon serverless PostgreSQL configuration.
    Expects a single DATABASE_URL in the format:
      postgresql://user:password@ep-xxx.region.neon.tech/dbname?sslmode=require

    Neon provides two connection strings:
      - Direct:  use for migrations / admin tasks
      - Pooled:  use for application (PgBouncer, lower latency)

    Set DATABASE_URL to the *pooled* connection string from your Neon dashboard.
    """
    database_url: str = os.getenv("DATABASE_URL", "")
    pool_min_size: int = 2
    pool_max_size: int = 10        # Neon free tier: max 10 connections
    pool_max_inactive_lifetime: float = 300.0   # recycle idle connections
    statement_cache_size: int = 0  # must be 0 for PgBouncer pooling mode
    ssl: str = "require"           # Neon always requires SSL

    @property
    def is_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def dsn(self) -> str:
        """Return DSN, ensuring sslmode=require is present."""
        url = self.database_url
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set. "
                "Copy your Neon pooled connection string into the environment."
            )
        if "sslmode" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        return url


@dataclass
class KafkaConfig:
    bootstrap_servers: str = os.getenv("KAFKA_BROKERS", "localhost:9092")
    topics: Dict[str, str] = field(default_factory=lambda: {
        "price_ticks": "market.price.ticks",
        "orderbook": "market.orderbook.snapshots",
        "signals": "platform.signals.output",
        "alerts": "platform.alerts.dispatch",
        "whale_events": "onchain.whale.events",
        "sentiment": "social.sentiment.scores",
    })
    consumer_group: str = "trading-intelligence-platform"
    auto_offset_reset: str = "latest"
    session_timeout_ms: int = 30000


@dataclass
class TelegramConfig:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    alert_chat_id: str = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")


@dataclass
class ArbitrageConfig:
    min_profit_bps: float = 20.0          # Minimum profit in basis points
    min_confidence: float = 0.65          # Confidence threshold 0-1
    max_execution_time_ms: int = 500      # Max window before spread closes
    slippage_buffer_bps: float = 5.0      # Extra slippage buffer
    scan_interval_ms: int = 100           # Scan frequency
    symbols: List[str] = field(default_factory=lambda: [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
        "XRP/USDT", "ADA/USDT", "AVAX/USDT", "MATIC/USDT"
    ])


@dataclass
class SentimentConfig:
    model_name: str = "cardiffnlp/twitter-roberta-base-sentiment-latest"
    batch_size: int = 64
    min_mentions_threshold: int = 10
    surge_multiplier: float = 2.0         # Mention surge trigger
    lookback_window_minutes: int = 120
    confidence_threshold: float = 0.70


@dataclass
class LiquidityConfig:
    wall_size_multiplier: float = 10.0    # Wall = 10x average order size
    imbalance_threshold: float = 0.70     # 70% bid/ask imbalance triggers signal
    depth_levels: int = 20               # Order book depth to analyze
    refresh_interval_ms: int = 250


@dataclass
class WhaleConfig:
    min_btc_transfer: float = 100.0
    min_eth_transfer: float = 1000.0
    min_usd_value: float = 1_000_000     # $1M minimum whale threshold
    exchange_addresses_file: str = "config/exchange_wallets.json"
    etherscan_api_key: str = os.getenv("ETHERSCAN_API_KEY", "")
    blockchain_api_key: str = os.getenv("BLOCKCHAIN_API_KEY", "")


@dataclass
class PlatformConfig:
    env: str = os.getenv("PLATFORM_ENV", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = "json"
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    api_workers: int = int(os.getenv("API_WORKERS", "4"))
    metrics_port: int = int(os.getenv("METRICS_PORT", "9090"))
    dashboard_port: int = int(os.getenv("DASHBOARD_PORT", "3000"))


# ─── Exchange Registry ────────────────────────────────────────────────────────

EXCHANGES: Dict[str, ExchangeConfig] = {
    "binance": ExchangeConfig(
        name="Binance",
        ws_url="wss://stream.binance.com:9443/ws",
        rest_url="https://api.binance.com/api/v3",
        fee_maker=0.001,
        fee_taker=0.001,
        min_order_size=10.0,
        supported_pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
                         "XRP/USDT", "ADA/USDT", "AVAX/USDT", "MATIC/USDT"],
    ),
    "coinbase": ExchangeConfig(
        name="Coinbase Advanced",
        ws_url="wss://advanced-trade-ws.coinbase.com",
        rest_url="https://api.coinbase.com/api/v3/brokerage",
        fee_maker=0.004,
        fee_taker=0.006,
        min_order_size=1.0,
        supported_pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT"],
    ),
    "kraken": ExchangeConfig(
        name="Kraken",
        ws_url="wss://ws.kraken.com",
        rest_url="https://api.kraken.com/0",
        fee_maker=0.0016,
        fee_taker=0.0026,
        min_order_size=5.0,
        supported_pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
    ),
    "okx": ExchangeConfig(
        name="OKX",
        ws_url="wss://ws.okx.com:8443/ws/v5/public",
        rest_url="https://www.okx.com/api/v5",
        fee_maker=0.0008,
        fee_taker=0.001,
        min_order_size=5.0,
        supported_pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
                         "XRP/USDT", "ADA/USDT", "AVAX/USDT"],
    ),
    "bybit": ExchangeConfig(
        name="Bybit",
        ws_url="wss://stream.bybit.com/v5/public/spot",
        rest_url="https://api.bybit.com/v5",
        fee_maker=0.001,
        fee_taker=0.001,
        min_order_size=1.0,
        supported_pairs=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
                         "ADA/USDT", "AVAX/USDT", "MATIC/USDT"],
    ),
}

# ─── Singleton Accessors ──────────────────────────────────────────────────────

_platform = PlatformConfig()
_redis = RedisConfig()
_neon = NeonConfig()
_kafka = KafkaConfig()
_telegram = TelegramConfig()
_arbitrage = ArbitrageConfig()
_sentiment = SentimentConfig()
_liquidity = LiquidityConfig()
_whale = WhaleConfig()


def get_platform() -> PlatformConfig: return _platform
def get_redis() -> RedisConfig: return _redis
def get_neon() -> NeonConfig: return _neon
def get_kafka() -> KafkaConfig: return _kafka
def get_telegram() -> TelegramConfig: return _telegram
def get_arbitrage() -> ArbitrageConfig: return _arbitrage
def get_sentiment() -> SentimentConfig: return _sentiment
def get_liquidity() -> LiquidityConfig: return _liquidity
def get_whale() -> WhaleConfig: return _whale
def get_exchanges() -> Dict[str, ExchangeConfig]: return EXCHANGES


# ─── Developer Credits ────────────────────────────────────────────────────────

DEVELOPER = {
    "name": "Bilal Etudaiye-Muhtar",
    "linkedin": "https://www.linkedin.com/in/bilal-etudaiye-muhtar-2725a317a",
    "platform": "QuantEdge AI Trading Intelligence Platform",
}
