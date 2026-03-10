"""
Whale Data Collectors — Phase 3

Sources:
  - Etherscan API   — large ETH transfers + ERC-20 (USDT, USDC, WBTC)
  - Blockchain.info — large BTC transfers (no API key needed)
  - Whale Alert API — multi-chain, optional (WHALE_ALERT_API_KEY)

All free tier. Etherscan free = 5 req/s, 100k req/day.
Set ETHERSCAN_API_KEY in Railway env vars for best results.
"""
import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

import aiohttp

from utils.logging import get_logger

log = get_logger("ingestion.whale")


# ─── Known exchange wallets (Ethereum) ───────────────────────────────────────

EXCHANGE_WALLETS: Dict[str, str] = {
    # Binance
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance",
    # Coinbase
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase",
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Kraken",
    "0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13": "Kraken",
    # OKX
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX",
    # Bybit
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit",
    # Bitfinex
    "0x1151314c646ce4e0efd76d1af4760ae66a9fe30f": "Bitfinex",
}

# ERC-20 token contracts to track
ERC20_TOKENS = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI",  18),
}


@dataclass
class RawTransaction:
    chain: str
    tx_hash: str
    from_address: str
    to_address: str
    asset: str
    amount: float
    amount_usd: float
    block_number: int
    timestamp: datetime


# ─── Etherscan Collector ──────────────────────────────────────────────────────

class EtherscanCollector:
    """
    Polls Etherscan for large ETH and ERC-20 transfers.
    Free API key: https://etherscan.io/register (100k req/day)
    Set ETHERSCAN_API_KEY env var.
    """

    BASE = "https://api.etherscan.io/api"

    # Rough USD prices — updated hourly from CoinGecko
    _prices: Dict[str, float] = {"ETH": 3500.0, "WBTC": 65000.0}

    def __init__(
        self,
        interval_s: int = 30,
        on_transaction: Optional[Callable] = None,
        min_usd: float = 1_000_000,
    ):
        self.interval_s = interval_s
        self._on_transaction = on_transaction
        self._min_usd = min_usd
        self._api_key = os.getenv("ETHERSCAN_API_KEY", "")
        self._last_eth_block = 0
        self._seen_hashes: set = set()
        self._running = False

        if not self._api_key:
            log.warning(
                "ETHERSCAN_API_KEY not set — using public rate limits (1 req/5s). "
                "Get a free key at https://etherscan.io/register"
            )

    async def start(self, session: aiohttp.ClientSession) -> None:
        self._running = True
        log.info("Etherscan collector started", min_usd=self._min_usd)

        # Update prices hourly
        asyncio.create_task(self._price_updater(session))

        while self._running:
            try:
                await self._poll_eth(session)
                await asyncio.sleep(2)
                await self._poll_erc20(session)
            except Exception as e:
                log.warning("Etherscan poll error", error=str(e))
            await asyncio.sleep(self.interval_s)

    async def stop(self):
        self._running = False

    def _params(self, extra: dict) -> dict:
        p = {"apikey": self._api_key} if self._api_key else {}
        p.update(extra)
        return p

    async def _poll_eth(self, session: aiohttp.ClientSession) -> None:
        """Fetch recent large ETH transfers."""
        params = self._params({
            "module": "account",
            "action": "txlist",
            "address": "0x0000000000000000000000000000000000000000",
            "startblock": max(self._last_eth_block, 0),
            "endblock": 99999999,
            "sort": "desc",
            "page": 1,
            "offset": 50,
        })

        # Etherscan's internal txlist doesn't support filtering by value,
        # so we use the tokentx endpoint for whale-level scans instead.
        # For ETH specifically, monitor top-value transactions via a different approach.
        await self._fetch_large_eth_txs(session)

    async def _fetch_large_eth_txs(self, session: aiohttp.ClientSession) -> None:
        """Use Etherscan's ETH transfer API to find large transfers."""
        params = self._params({
            "module": "account",
            "action": "txlist",
            "sort": "desc",
            "page": 1,
            "offset": 100,
        })

        # Poll known exchange wallets for outgoing large transfers
        for address, exchange_name in list(EXCHANGE_WALLETS.items())[:5]:
            try:
                p = dict(params)
                p["address"] = address
                async with session.get(
                    self.BASE, params=p,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                txs = data.get("result", [])
                if not isinstance(txs, list):
                    continue

                for tx in txs[:20]:
                    tx_hash = tx.get("hash", "")
                    if tx_hash in self._seen_hashes:
                        continue

                    value_wei = int(tx.get("value", "0"))
                    eth_amount = value_wei / 1e18
                    eth_price = self._prices.get("ETH", 3500.0)
                    usd_value = eth_amount * eth_price

                    if usd_value < self._min_usd:
                        continue

                    self._seen_hashes.add(tx_hash)
                    if len(self._seen_hashes) > 10000:
                        self._seen_hashes = set(list(self._seen_hashes)[-3000:])

                    raw_tx = RawTransaction(
                        chain="ethereum",
                        tx_hash=tx_hash,
                        from_address=tx.get("from", "").lower(),
                        to_address=tx.get("to", "").lower(),
                        asset="ETH",
                        amount=eth_amount,
                        amount_usd=usd_value,
                        block_number=int(tx.get("blockNumber", 0)),
                        timestamp=datetime.utcfromtimestamp(
                            int(tx.get("timeStamp", time.time()))
                        ),
                    )

                    log.info(
                        "Large ETH transfer detected",
                        amount_eth=round(eth_amount, 2),
                        amount_usd=round(usd_value, 0),
                        from_addr=raw_tx.from_address[:10],
                        to_addr=raw_tx.to_address[:10],
                    )

                    if self._on_transaction:
                        await self._on_transaction(raw_tx)

                await asyncio.sleep(0.5)  # rate limit

            except Exception as e:
                log.debug("ETH poll error", address=address[:10], error=str(e))

    async def _poll_erc20(self, session: aiohttp.ClientSession) -> None:
        """Fetch large ERC-20 token transfers (USDT, USDC, WBTC, DAI)."""
        for contract, (symbol, decimals) in ERC20_TOKENS.items():
            try:
                params = self._params({
                    "module": "account",
                    "action": "tokentx",
                    "contractaddress": contract,
                    "sort": "desc",
                    "page": 1,
                    "offset": 50,
                })

                async with session.get(
                    self.BASE, params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                txs = data.get("result", [])
                if not isinstance(txs, list):
                    continue

                for tx in txs[:20]:
                    tx_hash = tx.get("hash", "")
                    if tx_hash in self._seen_hashes:
                        continue

                    raw_value = int(tx.get("value", "0"))
                    token_amount = raw_value / (10 ** decimals)

                    # For stablecoins 1:1 USD; for WBTC use BTC price
                    if symbol in ("USDT", "USDC", "DAI"):
                        usd_value = token_amount
                    else:
                        usd_value = token_amount * self._prices.get("WBTC", 65000.0)

                    if usd_value < self._min_usd:
                        continue

                    self._seen_hashes.add(tx_hash)

                    raw_tx = RawTransaction(
                        chain="ethereum",
                        tx_hash=tx_hash,
                        from_address=tx.get("from", "").lower(),
                        to_address=tx.get("to", "").lower(),
                        asset=symbol,
                        amount=token_amount,
                        amount_usd=usd_value,
                        block_number=int(tx.get("blockNumber", 0)),
                        timestamp=datetime.utcfromtimestamp(
                            int(tx.get("timeStamp", time.time()))
                        ),
                    )

                    log.info(
                        "Large ERC-20 transfer detected",
                        asset=symbol,
                        amount=round(token_amount, 2),
                        amount_usd=round(usd_value, 0),
                        from_addr=raw_tx.from_address[:10],
                        to_addr=raw_tx.to_address[:10],
                    )

                    if self._on_transaction:
                        await self._on_transaction(raw_tx)

                await asyncio.sleep(0.5)

            except Exception as e:
                log.debug("ERC-20 poll error", symbol=symbol, error=str(e))

    async def _price_updater(self, session: aiohttp.ClientSession) -> None:
        """Update ETH/BTC prices from CoinGecko hourly."""
        while self._running:
            try:
                url = "https://api.coingecko.com/api/v3/simple/price"
                params = {"ids": "ethereum,bitcoin,wrapped-bitcoin", "vs_currencies": "usd"}
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._prices["ETH"] = data.get("ethereum", {}).get("usd", 3500.0)
                        self._prices["WBTC"] = data.get("wrapped-bitcoin", {}).get("usd", 65000.0)
                        self._prices["BTC"] = data.get("bitcoin", {}).get("usd", 65000.0)
                        log.info("Whale prices updated", eth=self._prices["ETH"], btc=self._prices["BTC"])
            except Exception as e:
                log.debug("Price update failed", error=str(e))
            await asyncio.sleep(3600)


# ─── Bitcoin Collector ────────────────────────────────────────────────────────

class BitcoinWhaleCollector:
    """
    Polls blockchain.info for large BTC transfers — no API key needed.
    Scans latest blocks for transactions exceeding min_btc threshold.
    """

    def __init__(
        self,
        interval_s: int = 60,
        on_transaction: Optional[Callable] = None,
        min_btc: float = 100.0,
    ):
        self.interval_s = interval_s
        self._on_transaction = on_transaction
        self._min_btc = min_btc
        self._seen_hashes: set = set()
        self._btc_price = 65000.0
        self._running = False

    async def start(self, session: aiohttp.ClientSession) -> None:
        self._running = True
        log.info("Bitcoin whale collector started", min_btc=self._min_btc)

        while self._running:
            try:
                await self._poll(session)
            except Exception as e:
                log.warning("Bitcoin poll error", error=str(e))
            await asyncio.sleep(self.interval_s)

    async def stop(self):
        self._running = False

    async def _poll(self, session: aiohttp.ClientSession) -> None:
        """Fetch latest BTC block and scan for large transactions."""
        try:
            # Get latest block hash
            async with session.get(
                "https://blockchain.info/latestblock",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return
                latest = await resp.json()
                block_hash = latest.get("hash", "")

            if not block_hash:
                return

            # Get block transactions
            async with session.get(
                f"https://blockchain.info/rawblock/{block_hash}",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return
                block = await resp.json()

            txs = block.get("tx", [])
            block_time = block.get("time", time.time())

            for tx in txs:
                tx_hash = tx.get("hash", "")
                if tx_hash in self._seen_hashes:
                    continue

                # Sum all outputs
                total_out_sat = sum(o.get("value", 0) for o in tx.get("out", []))
                total_btc = total_out_sat / 1e8

                if total_btc < self._min_btc:
                    continue

                self._seen_hashes.add(tx_hash)
                if len(self._seen_hashes) > 5000:
                    self._seen_hashes = set(list(self._seen_hashes)[-1000:])

                usd_value = total_btc * self._btc_price

                # Extract first input/output addresses
                inputs = tx.get("inputs", [])
                outputs = tx.get("out", [])
                from_addr = ""
                to_addr = ""

                if inputs:
                    prev_out = inputs[0].get("prev_out", {})
                    from_addr = prev_out.get("addr", "")
                if outputs:
                    to_addr = outputs[0].get("addr", "")

                raw_tx = RawTransaction(
                    chain="bitcoin",
                    tx_hash=tx_hash,
                    from_address=from_addr,
                    to_address=to_addr,
                    asset="BTC",
                    amount=total_btc,
                    amount_usd=usd_value,
                    block_number=block.get("height", 0),
                    timestamp=datetime.utcfromtimestamp(block_time),
                )

                log.info(
                    "Large BTC transfer detected",
                    amount_btc=round(total_btc, 2),
                    amount_usd=round(usd_value, 0),
                )

                if self._on_transaction:
                    await self._on_transaction(raw_tx)

        except Exception as e:
            log.debug("BTC block scan error", error=str(e))
