"""
Easy Collector - Base Market Data Client Interface
Common interface for all market data providers
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Optional, List
import pandas as pd
import pytz


class BaseMarketDataClient(ABC):
    """Base interface for market data clients"""
    
    @staticmethod
    def ensure_utc(dt: datetime, assume_tz: Optional[timezone] = None) -> datetime:
        """
        Normalize datetime to timezone-aware UTC.
        
        Strict mode (recommended for production):
        - If dt is aware: convert to UTC.
        - If dt is naive: raise ValueError (prevents silent timezone bugs)
        
        Fallback mode (for convenience in scripts/tests):
        - If dt is naive and assume_tz is provided: treat dt as that tz and convert to UTC
        
        Args:
            dt: Datetime (naive or timezone-aware)
            assume_tz: Optional timezone to assume if dt is naive (e.g., timezone.utc, pytz.timezone("America/New_York"))
        
        Returns:
            Timezone-aware UTC datetime
        
        Raises:
            ValueError: If dt is naive and assume_tz is None
        """
        if dt.tzinfo is None:
            if assume_tz is None:
                raise ValueError(
                    "Naive datetime passed to ensure_utc(). "
                    "Provide tz-aware datetime or pass assume_tz parameter."
                )
            # Support both datetime.timezone and pytz/zoneinfo tz
            if hasattr(assume_tz, "localize"):  # pytz
                dt = assume_tz.localize(dt)
            else:
                dt = dt.replace(tzinfo=assume_tz)
        return dt.astimezone(timezone.utc)
    
    def normalize_symbol(self, symbol: str) -> str:
        """
        Optional: Normalize symbol format for provider-specific requirements.
        Default implementation returns symbol as-is.
        
        Args:
            symbol: Trading symbol in any format
        
        Returns:
            Normalized symbol format for this provider
        """
        return symbol
    
    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime
    ) -> pd.DataFrame:
        """
        Get OHLCV data for a symbol within a time range.
        
        Time window convention: [start_utc, end_utc) - start inclusive, end exclusive.
        This prevents double-counting candles at window boundaries.
        
        Args:
            symbol: Trading symbol (e.g., 'SPY', 'BTC-PERP')
            timeframe: Bar timeframe (e.g., '1m', '5m', '1h')
            start_utc: Start time in UTC (inclusive, will be normalized via ensure_utc)
            end_utc: End time in UTC (exclusive, will be normalized via ensure_utc)
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
            
            Schema guarantees:
            - timestamp: timezone-aware UTC datetime column (not index)
            - open, high, low, close: numeric (float)
            - volume: numeric (int, nullable with 0 fill)
            - Sorted ascending by timestamp
            - Filtered: timestamp >= start_utc AND timestamp < end_utc (end-exclusive)
            - All numeric columns coerced to numeric types
            - Rows with invalid OHLC data dropped (timestamp, open, high, low, close must be valid)
        """
        pass
    
    def get_ohlcv_many(
        self,
        symbols: List[str],
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime
    ) -> Dict[str, pd.DataFrame]:
        """
        Optional: Batch OHLCV fetch for multiple symbols.
        Default implementation calls get_ohlcv() per symbol sequentially.
        
        Clients can override this to implement optimized batch fetching
        (e.g., Coinbase async batch, yfinance yf.download()).
        
        Args:
            symbols: List of trading symbols
            timeframe: Bar timeframe (e.g., '1m', '5m', '1h')
            start_utc: Start time in UTC (inclusive)
            end_utc: End time in UTC (exclusive, [start, end) convention)
        
        Returns:
            Dict mapping symbol to DataFrame (same schema as get_ohlcv)
        """
        return {s: self.get_ohlcv(s, timeframe, start_utc, end_utc) for s in symbols}
    
    @classmethod
    def finalize_ohlcv_df(
        cls,
        df: pd.DataFrame,
        start_utc: datetime,
        end_utc: datetime
    ) -> pd.DataFrame:
        """
        Enforce schema + numeric coercion + time filtering.
        Shared helper to ensure all clients output identical schema.
        
        Uses [start_utc, end_utc) convention (start inclusive, end exclusive).
        
        Args:
            df: Raw DataFrame with timestamp and OHLCV columns
            start_utc: Start time in UTC (inclusive)
            end_utc: End time in UTC (exclusive)
        
        Returns:
            Finalized DataFrame with:
            - timestamp: UTC-aware datetime column
            - open, high, low, close: numeric (float)
            - volume: numeric (int, 0-filled)
            - Filtered to [start_utc, end_utc)
            - Sorted by timestamp ascending
        """
        # Normalize timezone (allow assume_tz=timezone.utc for convenience)
        start_utc = cls.ensure_utc(start_utc, assume_tz=timezone.utc)
        end_utc = cls.ensure_utc(end_utc, assume_tz=timezone.utc)
        
        if df is None or df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        # Ensure timestamp is UTC-aware datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        
        # Numeric coercion
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        # Drop rows with invalid OHLC data (keep volume optional)
        df.dropna(subset=["timestamp", "open", "high", "low", "close"], inplace=True)
        
        # Fill volume NaNs with 0 and convert to int
        if "volume" in df.columns:
            df["volume"] = df["volume"].fillna(0).astype(int)
        
        # Filter to [start_utc, end_utc) - end-exclusive convention
        df = df[(df["timestamp"] >= start_utc) & (df["timestamp"] < end_utc)]
        
        # Sort by timestamp ascending
        df = df.sort_values("timestamp").reset_index(drop=True)
        
        return df
    
    @abstractmethod
    def get_last_price(self, symbol: str) -> float:
        """
        Get last traded price for a symbol
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Last price as float (0.0 if unavailable)
        """
        pass
    
    @abstractmethod
    def get_symbol_metadata(self, symbol: str) -> Dict:
        """
        Get symbol metadata (tick size, trading hours, etc.)
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Dict with metadata (tick_size, trading_hours, etc.)
        """
        pass
    
    @abstractmethod
    def healthcheck(self) -> Dict:
        """
        Check client health status
        
        Returns:
            Dict with standardized keys:
            - status: "healthy" | "degraded" | "unhealthy"
            - provider: Provider identifier (e.g., "ETRADE", "COINBASE")
            - sandbox: bool (whether using sandbox/test environment)
            - message: str (human-readable status message)
            - error: Optional[str] (error message if unhealthy)
            - rate_limit_remaining: Optional[int] (remaining API calls)
            - rate_limit_reset: Optional[datetime] (when rate limit resets)
        """
        pass
