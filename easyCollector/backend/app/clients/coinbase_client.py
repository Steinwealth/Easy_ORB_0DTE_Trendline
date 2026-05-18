"""
Easy Collector - Coinbase Futures Market Data Client
Thin adapter for Coinbase public API with retry logic and rate limiting
Uses Coinbase Exchange public API for OHLCV data (no auth required)
"""

import logging
import aiohttp
import asyncio
import threading
import queue
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Set, Tuple
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.clients.base_client import BaseMarketDataClient
from app.config import get_settings

log = logging.getLogger(__name__)


class RetryableHTTPError(Exception):
    """Exception for retryable HTTP errors (429, 5xx)"""
    pass


class SymbolUnresolvable(Exception):
    """Raised when the requested symbol cannot be resolved to a Coinbase Exchange product id."""
    def __init__(self, symbol: str):
        self.symbol = symbol
        super().__init__(f"UNRESOLVABLE_SYMBOL: {symbol}")


class CoinbaseClient(BaseMarketDataClient):
    """Coinbase Futures market data client for crypto PERP symbols"""
    
    # Symbol normalization map (PERP symbols to Coinbase API format)
    SYMBOL_MAP = {
        'BTC-PERP': 'BTC-USD',
        'ETH-PERP': 'ETH-USD',
        'SOL-PERP': 'SOL-USD',
        'XRP-PERP': 'XRP-USD',
    }
    
    # Timeframe to granularity mapping (in seconds)
    GRANULARITY_MAP = {
        '1m': 60,
        '5m': 300,
        '15m': 900,
        '1h': 3600,
        '4h': 14400,
        '1d': 86400
    }
    
    # Coinbase candles endpoint limit
    MAX_CANDLES_PER_REQUEST = 300
    
    def __init__(self):
        """Initialize Coinbase client"""
        settings = get_settings()
        # Note: api_key/api_secret/sandbox stored but not used (public endpoints only)
        # Kept for future authenticated endpoints if needed
        self.api_key = settings.coinbase_api_key
        self.api_secret = settings.coinbase_api_secret
        self.sandbox = settings.coinbase_sandbox
        self.timeout = 20  # seconds
        self.max_retries = 3
        
        # Reusable aiohttp session (created lazily, closed on shutdown)
        self._session: Optional[aiohttp.ClientSession] = None
        # Valid product ids from GET /products (for resolve_product_id)
        self._valid_product_ids: Set[str] = set()
        try:
            self._run_async_safely(self._fetch_products())
        except Exception as e:
            log.warning("Could not load Coinbase /products: %s; resolve will use best-effort", e)
        
        log.info("✅ Coinbase client initialized (public Exchange API mode)")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Get or create aiohttp session (reused for performance).
        
        IMPORTANT: When running in a thread (via _run_async_safely), we create
        a new session in that thread's event loop to avoid "Event loop is closed" errors.
        """
        try:
            current_loop = asyncio.get_running_loop()
            
            # Check if we need a new session
            need_new_session = (
                self._session is None or 
                self._session.closed or
                not hasattr(self._session, '_loop') or
                self._session._loop is None or
                self._session._loop.is_closed() or
                self._session._loop != current_loop
            )
            
            if need_new_session:
                # Close old session if it exists and is not closed
                if self._session and not self._session.closed:
                    try:
                        await self._session.close()
                    except Exception:
                        pass  # Ignore errors closing old session
                
                # Create new session for current event loop
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                self._session = aiohttp.ClientSession(timeout=timeout)
            
            return self._session
            
        except RuntimeError:
            # No running loop (shouldn't happen in async context, but handle gracefully)
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session
    
    async def close(self):
        """Close aiohttp session (call from FastAPI lifespan event)"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def _fetch_products(self) -> None:
        """Load valid product ids from GET /products. Populates _valid_product_ids."""
        url = "https://api.exchange.coinbase.com/products"
        try:
            session = await self._get_session()
            async with session.get(url, headers={"Accept": "application/json"}) as r:
                if r.status != 200:
                    return
                data = await r.json()
                if isinstance(data, list):
                    self._valid_product_ids = {p["id"] for p in data if isinstance(p, dict) and p.get("id")}
                    log.info("Coinbase /products: %s valid product ids", len(self._valid_product_ids))
        except Exception as e:
            log.warning("_fetch_products: %s", e)
    
    def resolve_product_id(self, symbol: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Resolve requested symbol to a Coinbase Exchange product_id.
        Returns (product_id, None) or (None, "UNRESOLVABLE_SYMBOL").
        When _valid_product_ids is empty (e.g. /products failed), uses best-effort normalize_symbol.
        """
        s = (symbol or "").strip().upper()
        if not s:
            return None, "UNRESOLVABLE_SYMBOL"
        if self._valid_product_ids:
            if s in self._valid_product_ids:
                return s, None
            if s in self.SYMBOL_MAP:
                cand = self.SYMBOL_MAP[s]
                if cand in self._valid_product_ids:
                    return cand, None
            if "-PERP" in s:
                base = s.replace("-PERP", "").strip()
                cand = f"{base}-USD"
                if cand in self._valid_product_ids:
                    return cand, None
            if "-USD" in s and s in self._valid_product_ids:
                return s, None
            return None, "UNRESOLVABLE_SYMBOL"
        # Best-effort when /products not available
        return self.normalize_symbol(symbol), None
    
    def normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol to Coinbase API format (BTC-USD, ETH-USD, etc.)
        
        Args:
            symbol: Symbol in any format (e.g., 'BTC-PERP', 'BTC-PERP')
        
        Returns:
            Normalized symbol in Coinbase API format (e.g., 'BTC-USD')
        """
        symbol_upper = symbol.upper()
        if symbol_upper in self.SYMBOL_MAP:
            return self.SYMBOL_MAP[symbol_upper]
        # If already in correct format (BTC-USD), return as-is
        if '-USD' in symbol_upper:
            return symbol_upper
        # Try mapping PERP symbols
        if '-PERP' in symbol_upper:
            base = symbol_upper.replace('-PERP', '')
            return f"{base}-USD"
        return symbol_upper
    
    # Use base class ensure_utc instead of local _ensure_utc
    
    @staticmethod
    def _run_async_safely(coro):
        """
        Safely run async coroutine.
        Works in both sync contexts (scripts) and FastAPI threadpool contexts.
        Uses threading to run async code when called from a context with a running event loop.
        """
        # Check if we're in a running event loop
        # get_running_loop() raises RuntimeError if NO loop is running
        try:
            loop = asyncio.get_running_loop()
            # If we get here, there IS a running loop (e.g., uvloop in FastAPI)
            # We need to run in a separate thread with its own event loop
            result_queue = queue.Queue()
            exception_queue = queue.Queue()
            
            def run_in_thread():
                """Run coroutine in a new thread with its own event loop"""
                try:
                    # Create a new event loop for this thread
                    # Use asyncio.run() which creates and manages its own event loop
                    # This avoids issues with uvloop and nested event loops
                    result = asyncio.run(coro)
                    result_queue.put(result)
                except Exception as e:
                    exception_queue.put(e)
            
            thread = threading.Thread(target=run_in_thread, daemon=False)
            thread.start()
            thread.join(timeout=30)  # 30 second timeout
            
            if thread.is_alive():
                raise TimeoutError("Coinbase API call timed out after 30 seconds")
            
            if not exception_queue.empty():
                raise exception_queue.get()
            
            if not result_queue.empty():
                return result_queue.get()
            
            raise RuntimeError("Failed to get result from async operation")
            
        except RuntimeError:
            # RuntimeError from get_running_loop() means NO running loop
            # This happens when called from FastAPI's threadpool executor or sync scripts
            # Safe to use asyncio.run() directly
            return asyncio.run(coro)
    
    async def get_ohlcv_async(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime
    ) -> pd.DataFrame:
        """
        Get OHLCV data for a crypto symbol from Coinbase public API (async).
        
        Args:
            symbol: Crypto symbol (e.g., 'BTC-PERP', 'ETH-PERP')
            timeframe: Bar timeframe ('1m', '5m', '1h')
            start_utc: Start time in UTC
            end_utc: End time in UTC
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            product_id, err = self.resolve_product_id(symbol)
            if err == "UNRESOLVABLE_SYMBOL":
                raise SymbolUnresolvable(symbol)
            granularity = self.GRANULARITY_MAP.get(timeframe, 300)  # Default to 5m
            
            # Ensure timezone-aware UTC
            start_utc = self.ensure_utc(start_utc)
            end_utc = self.ensure_utc(end_utc)
            # Do not request future bars; avoid empty/inverted windows
            now_utc = datetime.now(timezone.utc)
            if end_utc > now_utc:
                end_utc = now_utc
            if end_utc <= start_utc:
                log.warning("Coinbase get_ohlcv: end_utc <= start_utc (empty window) for %s; reason=inverted_or_zero_window", symbol)
                return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            log.info(f"📊 Fetching Coinbase OHLCV: {symbol} → {product_id} ({timeframe}) from {start_utc} to {end_utc} UTC")
            log.debug(f"  resolve_product_id: {symbol} → {product_id}")
            log.debug(f"  Granularity: {granularity}s (timeframe: {timeframe})")
            
            log.debug(f"  Fetching candles with chunking (Coinbase limit: 300 candles/request)...")
            candles = await self._fetch_candles_windowed(product_id, granularity, start_utc, end_utc)
            
            if not candles:
                log.error(f"  ❌ No OHLCV data from Coinbase for {product_id} ({timeframe}) - API returned empty response")
                return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Convert to DataFrame
            # Coinbase returns: [time, low, high, open, close, volume]
            df = pd.DataFrame(candles, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
            
            # Convert timestamp from Unix seconds to datetime (UTC-aware)
            df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
            
            # Ensure numeric types
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Drop rows with invalid data
            df.dropna(subset=['timestamp', 'open', 'high', 'low', 'close'], inplace=True)
            
            # Reorder columns; keep volume as float for crypto (no int coercion)
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()
            df['volume'] = df['volume'].fillna(0.0)
            df = df.sort_values('timestamp').reset_index(drop=True)

            # Filter to requested time range (end-exclusive to match SnapshotService)
            df_before_filter = len(df)
            df = df[(df['timestamp'] >= start_utc) & (df['timestamp'] < end_utc)]
            log.debug(f"  Filtered DataFrame: {df_before_filter} → {len(df)} bars (range: {start_utc} to {end_utc})")
            
            if len(df) > 0:
                log.info(f"  ✅ Retrieved {len(df)} bars for {product_id} ({timeframe})")
                log.debug(f"    First bar: {df.iloc[0]['timestamp']} (O={df.iloc[0]['open']:.2f}, H={df.iloc[0]['high']:.2f}, L={df.iloc[0]['low']:.2f}, C={df.iloc[0]['close']:.2f})")
                log.debug(f"    Last bar: {df.iloc[-1]['timestamp']} (O={df.iloc[-1]['open']:.2f}, H={df.iloc[-1]['high']:.2f}, L={df.iloc[-1]['low']:.2f}, C={df.iloc[-1]['close']:.2f})")
            else:
                log.error(f"  ❌ Retrieved 0 bars for {product_id} after filtering to time range ({start_utc} to {end_utc} UTC)")
            
            return df
            
        except Exception as e:
            log.error(f"  ❌ Failed to get Coinbase OHLCV for {symbol}: {e}", exc_info=True)
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime
    ) -> pd.DataFrame:
        """
        Get OHLCV data (sync wrapper - works in both scripts and FastAPI threadpool contexts).
        
        This sync wrapper safely runs async code using threading when called from FastAPI.
        """
        try:
            return self._run_async_safely(
                self.get_ohlcv_async(symbol, timeframe, start_utc, end_utc)
            )
        except Exception as e:
            log.error(f"Sync wrapper failed for {symbol}: {e}", exc_info=True)
            # Return empty DataFrame to match async method behavior
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    async def _fetch_candles_windowed(
        self,
        symbol: str,
        granularity: int,
        start_utc: datetime,
        end_utc: datetime
    ) -> List[List]:
        """
        Fetch candles in chunks (Coinbase limit: 300 candles per request).
        
        Args:
            symbol: Normalized symbol (e.g., 'BTC-USD')
            granularity: Granularity in seconds
            start_utc: Start time (UTC-aware)
            end_utc: End time (UTC-aware)
        
        Returns:
            List of candle lists: [time, low, high, open, close, volume]
        """
        # Calculate max window size (granularity * MAX_CANDLES)
        max_window_seconds = granularity * self.MAX_CANDLES_PER_REQUEST
        step = timedelta(seconds=max_window_seconds)
        overlap = timedelta(seconds=granularity)  # 1 candle overlap to ensure boundary coverage
        
        cur = start_utc
        all_candles = []
        
        while cur < end_utc:
            nxt = min(cur + step, end_utc)
            # Fetch with overlap (except for first chunk) to ensure boundary coverage
            fetch_start = max(start_utc, cur - overlap) if cur > start_utc else cur
            
            try:
                chunk_candles = await self._fetch_ohlcv_async(symbol, granularity, fetch_start, nxt)
                if chunk_candles:
                    all_candles.extend(chunk_candles)
            except Exception as e:
                log.warning(f"Error fetching chunk for {symbol} ({fetch_start} to {nxt}): {e}")
            
            cur = nxt
            
            # Light pacing to avoid rate limits (120ms between requests)
            if cur < end_utc:
                await asyncio.sleep(0.12)
        
        # De-duplicate by timestamp (overlap ensures coverage, dedupe removes duplicates)
        by_timestamp = {}
        for candle in all_candles:
            # Validate candle shape: must be list with exactly 6 elements
            if isinstance(candle, list) and len(candle) == 6:
                timestamp = candle[0]
                by_timestamp[timestamp] = candle
        
        # Return sorted by timestamp
        return [by_timestamp[k] for k in sorted(by_timestamp.keys())]
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RetryableHTTPError, aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def _fetch_ohlcv_async(
        self,
        symbol: str,
        granularity: int,
        start_utc: datetime,
        end_utc: datetime
    ) -> List[List]:
        """Async helper to fetch OHLCV candles from Coinbase public API"""
        try:
            # Convert datetime to ISO format with Z suffix (UTC)
            start_iso = start_utc.isoformat().replace('+00:00', 'Z')
            end_iso = end_utc.isoformat().replace('+00:00', 'Z')
            
            # Coinbase Exchange API endpoint (public, no auth required)
            url = f"https://api.exchange.coinbase.com/products/{symbol}/candles"
            log.debug(f"Coinbase API request: {url} with params start={start_iso}, end={end_iso}, granularity={granularity}")
            
            params = {
                "start": start_iso,
                "end": end_iso,
                "granularity": granularity
            }
            headers = {
                "User-Agent": "EasyCollector/1.0",
                "Accept": "application/json"
            }
            
            # Get or create session (lazy initialization)
            session = await self._get_session()
            log.debug(f"Making Coinbase API request: GET {url}")
            async with session.get(url, params=params, headers=headers) as response:
                # Raise exception for retryable errors (429, 5xx)
                if response.status == 429:
                    error_text = await response.text()
                    # Honor Retry-After header if present (reduces rate-limit thrash)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            await asyncio.sleep(float(retry_after))
                        except Exception:
                            pass  # Ignore parse errors, tenacity will handle retry
                    raise RetryableHTTPError(f"429 Rate Limit: {error_text[:200]}")
                elif 500 <= response.status < 600:
                    error_text = await response.text()
                    raise RetryableHTTPError(f"{response.status} Server Error: {error_text[:200]}")
                
                if response.status == 200:
                    try:
                        data = await response.json()
                        if not data or not isinstance(data, list):
                            log.warning(f"Coinbase API returned invalid data format for {symbol}")
                            return []
                        
                        # Coinbase returns: [time, low, high, open, close, volume]
                        # Validate candle shape and sort by time (ascending)
                        candles = [c for c in data if isinstance(c, list) and len(c) == 6]
                        candles = sorted(candles, key=lambda x: x[0])
                        log.debug(f"✅ Fetched {len(candles)} candles from Coinbase for {symbol}")
                        return candles
                    except Exception as json_error:
                        error_text = await response.text()
                        log.warning(f"Coinbase API JSON parse error for {symbol}: {json_error}, response: {error_text[:200]}")
                        return []
                else:
                    error_text = await response.text()
                    log.error(f"❌ Coinbase API returned status {response.status} for {symbol}: {error_text[:200]}")
                    # For 404 (product not found), log more details
                    if response.status == 404:
                        log.error(f"   Product {symbol} not found on Coinbase Exchange API. Check symbol normalization.")
                    return []
                        
        except asyncio.TimeoutError:
            log.warning(f"Timeout fetching OHLCV for {symbol}")
            raise  # Re-raise so tenacity can retry
        except RetryableHTTPError:
            raise  # Re-raise so tenacity can retry
        except aiohttp.ClientError as e:
            log.error(f"Coinbase API client error for {symbol}: {e}", exc_info=True)
            raise  # Re-raise so tenacity can retry
        except Exception as e:
            log.error(f"Error fetching OHLCV for {symbol}: {e}", exc_info=True)
            return []  # Non-retryable error
    
    async def get_last_price_async(self, symbol: str) -> float:
        """
        Get last traded price for a crypto symbol from Coinbase public API (async).
        
        Args:
            symbol: Crypto symbol (normalized)
        
        Returns:
            Last price as float
        """
        try:
            normalized_symbol = self.normalize_symbol(symbol)
            price = await self._fetch_ticker_async(normalized_symbol)
            
            if price and price > 0:
                log.debug(f"✅ Got price from Coinbase for {normalized_symbol}: {price}")
                return float(price)
            
            log.warning(f"⚠️ No price data available for {normalized_symbol}")
            return 0.0
            
        except Exception as e:
            log.error(f"Failed to get Coinbase last price for {symbol}: {e}", exc_info=True)
            return 0.0
    
    def get_last_price(self, symbol: str) -> float:
        """
        Get last price (sync wrapper - use get_last_price_async() in FastAPI).
        
        This is a sync wrapper for scripts. In FastAPI/SnapshotService, use get_last_price_async() instead.
        """
        return self._run_async_safely(
            self.get_last_price_async(symbol)
        )
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((RetryableHTTPError, aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def _fetch_ticker_async(self, symbol: str) -> float:
        """Async helper to fetch ticker from Coinbase public API"""
        try:
            url = f"https://api.exchange.coinbase.com/products/{symbol}/ticker"
            headers = {"User-Agent": "EasyCollector/1.0"}
            
            session = await self._get_session()
            async with session.get(url, headers=headers) as response:
                # Raise exception for retryable errors
                if response.status == 429:
                    error_text = await response.text()
                    # Honor Retry-After header if present (reduces rate-limit thrash)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            await asyncio.sleep(float(retry_after))
                        except Exception:
                            pass  # Ignore parse errors, tenacity will handle retry
                    raise RetryableHTTPError(f"429 Rate Limit: {error_text[:200]}")
                elif 500 <= response.status < 600:
                    error_text = await response.text()
                    raise RetryableHTTPError(f"{response.status} Server Error: {error_text[:200]}")
                
                if response.status == 200:
                    data = await response.json()
                    price = float(data.get('price', 0))
                    return price
                else:
                    log.warning(f"Coinbase ticker API returned status {response.status} for {symbol}")
                    return 0.0
                        
        except asyncio.TimeoutError:
            log.warning(f"Timeout fetching ticker for {symbol}")
            raise  # Re-raise so tenacity can retry
        except RetryableHTTPError:
            raise  # Re-raise so tenacity can retry
        except aiohttp.ClientError as e:
            log.error(f"Coinbase API client error for {symbol}: {e}", exc_info=True)
            raise  # Re-raise so tenacity can retry
        except Exception as e:
            log.error(f"Error fetching ticker for {symbol}: {e}", exc_info=True)
            return 0.0
    
    def get_symbol_metadata(self, symbol: str) -> Dict:
        """
        Get symbol metadata (tick size, trading hours, etc.)
        
        Args:
            symbol: Crypto symbol (normalized)
        
        Returns:
            Dict with metadata
        """
        try:
            normalized_symbol = self.normalize_symbol(symbol)
            
            # TODO: Implement actual Coinbase API call
            # Use Coinbase Advanced Trade API /products/{product_id}
            
            log.warning(f"⚠️ Coinbase get_symbol_metadata stub called for {normalized_symbol}")
            
            # Return stub metadata
            return {
                "symbol": normalized_symbol,
                "market": "CRYPTO",
                "tick_size": 0.01,
                "trading_hours": "24/7",
                "currency": "USD",
                "type": "PERP"  # Perpetual futures
            }
            
        except Exception as e:
            log.error(f"Failed to get Coinbase metadata for {symbol}: {e}", exc_info=True)
            return {"symbol": symbol}
    
    async def healthcheck_async(self) -> Dict:
        """
        Check Coinbase API health status (async).
        
        Returns:
            Dict with status, rate_limit_info, etc.
        """
        try:
            # Test with BTC-USD ticker
            test_symbol = "BTC-USD"
            price = await self._fetch_ticker_async(test_symbol)
            
            if price and price > 0:
                return {
                    "status": "healthy",
                    "provider": "COINBASE",
                    "sandbox": self.sandbox,
                    "rate_limit_remaining": None,
                    "rate_limit_reset": None,
                    "message": "Coinbase API connected"
                }
            else:
                return {
                    "status": "degraded",
                    "provider": "COINBASE",
                    "sandbox": self.sandbox,
                    "message": "Coinbase API test failed"
                }
            
        except Exception as e:
            log.error(f"Coinbase health check failed: {e}", exc_info=True)
            return {
                "status": "unhealthy",
                "provider": "COINBASE",
                "error": str(e)
            }
    
    def healthcheck(self) -> Dict:
        """
        Check Coinbase API health status (sync wrapper).
        
        This is a sync wrapper for scripts. In FastAPI, use healthcheck_async() instead.
        """
        return self._run_async_safely(self.healthcheck_async())
