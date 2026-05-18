"""
Easy Collector - Alpaca Market Data Client (stub)
Placeholder for Alpaca bars API. Implement get_ohlcv / get_ohlcv_many when ALPACA_KEY/SECRET are used.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from app.clients.base_client import BaseMarketDataClient
from app.config import get_settings

log = logging.getLogger(__name__)


class AlpacaClient(BaseMarketDataClient):
    """Alpaca market data client. Stub: returns empty when no keys; implement when needed."""

    def __init__(self, key: Optional[str] = None, secret: Optional[str] = None):
        s = get_settings()
        self.key = key or s.alpaca_key
        self.secret = secret or s.alpaca_secret

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        snapshot_type: Optional[str] = None,
    ) -> pd.DataFrame:
        if not self.key or not self.secret:
            log.debug("AlpacaClient: no credentials, returning empty")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        # TODO: call Alpaca bars API (e.g. /v2/stocks/{symbol}/bars)
        log.warning("AlpacaClient.get_ohlcv not implemented")
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def get_ohlcv_many(
        self,
        symbols: List[str],
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        chunk_size: int = 25,
    ) -> Dict[str, pd.DataFrame]:
        if not self.key or not self.secret:
            return {s: pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]) for s in symbols}
        # TODO: implement with Alpaca multi-symbol or batch
        log.warning("AlpacaClient.get_ohlcv_many not implemented")
        return {s: pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]) for s in symbols}

    def get_last_price(self, symbol: str) -> float:
        return 0.0

    def get_symbol_metadata(self, symbol: str) -> Dict:
        return {"symbol": symbol, "market": "US", "source": "alpaca"}

    def healthcheck(self) -> Dict:
        if not self.key or not self.secret:
            return {"status": "unhealthy", "provider": "ALPACA", "message": "No ALPACA_KEY/SECRET"}
        # TODO: real bars fetch when implemented
        return {"status": "unhealthy", "provider": "ALPACA", "message": "Alpaca bars not implemented"}
