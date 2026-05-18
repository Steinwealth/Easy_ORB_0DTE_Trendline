"""
Easy Collector - US Intraday Data Cache
Prefetches and caches US market intraday data to avoid repeated API calls per snapshot.

Strategy:
- Prefetch once per day using yfinance batch download (period="2d" for historical lookback)
- Cache results in memory and optionally on disk
- Provide slice methods for indicator slabs and snapshot windows
"""

import logging
import pandas as pd
from datetime import datetime, timedelta, timezone, time as dt_time
from typing import Dict, Optional, List, Tuple, Any
from pathlib import Path
import pickle
import hashlib
from app.utils.time_utils import et_to_utc, utc_to_et, get_market_tz
from app.config import get_settings

log = logging.getLogger(__name__)


class USIntradayCache:
    """
    Cache for US market intraday OHLCV data.
    
    Keyed by (date, timeframe, symbol) -> DataFrame with columns: timestamp, open, high, low, close, volume
    """
    
    def __init__(self, enable_disk_cache: bool = True, cache_dir: Optional[Path] = None):
        """
        Initialize cache.
        
        Args:
            enable_disk_cache: If True, persist cache to disk
            cache_dir: Directory for disk cache (default: /tmp/easy_collector_cache)
        """
        self.memory_cache: Dict[str, pd.DataFrame] = {}  # Key: f"{date}_{timeframe}_{symbol}"
        self.enable_disk_cache = enable_disk_cache
        
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path("/tmp/easy_collector_cache")
        
        if self.enable_disk_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            log.info(f"✅ US Intraday Cache initialized (disk cache: {self.cache_dir})")
        else:
            log.info(f"✅ US Intraday Cache initialized (memory-only)")
    
    def _cache_key(self, date: datetime.date, timeframe: str, symbol: str) -> str:
        """Generate cache key"""
        date_str = date.strftime('%Y%m%d')
        return f"{date_str}_{timeframe}_{symbol}"
    
    def _disk_cache_path(self, cache_key: str) -> Path:
        """Get disk cache file path"""
        # Use hash to avoid long filenames
        key_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
        return self.cache_dir / f"{key_hash}.pkl"
    
    def _load_from_disk(self, cache_key: str) -> Optional[pd.DataFrame]:
        """Load cached data from disk"""
        if not self.enable_disk_cache:
            return None
        
        cache_path = self._disk_cache_path(cache_key)
        if cache_path.exists():
            try:
                with open(cache_path, 'rb') as f:
                    df = pickle.load(f)
                    log.debug(f"  ✅ Loaded from disk cache: {cache_key}")
                    return df
            except Exception as e:
                log.warning(f"  ⚠️ Failed to load disk cache {cache_key}: {e}")
        return None
    
    def _save_to_disk(self, cache_key: str, df: pd.DataFrame):
        """Save cached data to disk"""
        if not self.enable_disk_cache:
            return
        
        try:
            cache_path = self._disk_cache_path(cache_key)
            with open(cache_path, 'wb') as f:
                pickle.dump(df, f)
            log.debug(f"  💾 Saved to disk cache: {cache_key}")
        except Exception as e:
            log.warning(f"  ⚠️ Failed to save disk cache {cache_key}: {e}")
    
    def prefetch(
        self,
        date: datetime.date,
        symbols: List[str],
        timeframe: str = "5m",
        period: str = "2d",
        us_client: Optional[Any] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Prefetch intraday data for symbols on a given date.
        
        When us_client is provided (Polygon/Alpaca), uses get_ohlcv_many for 2 market days.
        Otherwise uses yfinance batch download (period=2d).
        
        Args:
            date: Target date (data will include this date and previous day)
            symbols: List of symbols to fetch
            timeframe: Bar timeframe ("5m", "1m", etc.)
            period: yfinance period ("2d") when us_client is None
            us_client: Optional US client with get_ohlcv_many (Polygon, Alpaca, or YFinance)
        
        Returns:
            Dict mapping symbol to DataFrame (empty DataFrame for failed symbols)
        """
        import yfinance as yf
        from app.utils.time_utils import ET_TZ
        
        # EXPLICIT LOGGING: Prefetch request details
        log.info(f"📥 PREFETCH REQUEST:")
        log.info(f"   - Symbols count: {len(symbols)}")
        log.info(f"   - Batch size: 5")
        log.info(f"   - Timeframe: {timeframe}")
        log.info(f"   - Period: {period}")
        log.info(f"   - Date: {date}")
        log.info(f"   - Method: yf.download(tickers=list, interval='{timeframe}', period='{period}', group_by='ticker')")
        
        results = {}
        
        # Check cache first
        cached_symbols = []
        uncached_symbols = []
        
        for symbol in symbols:
            cache_key = self._cache_key(date, timeframe, symbol)
            
            # Check memory cache
            if cache_key in self.memory_cache:
                results[symbol] = self.memory_cache[cache_key]
                cached_symbols.append(symbol)
                log.debug(f"  ✅ {symbol}: Using memory cache")
                continue
            
            # Check disk cache
            cached_df = self._load_from_disk(cache_key)
            if cached_df is not None and not cached_df.empty:
                self.memory_cache[cache_key] = cached_df
                results[symbol] = cached_df
                cached_symbols.append(symbol)
                log.debug(f"  ✅ {symbol}: Using disk cache")
                continue
            
            uncached_symbols.append(symbol)
        
        if cached_symbols:
            log.info(f"  ✅ {len(cached_symbols)} symbols found in cache")
        
        if not uncached_symbols:
            log.info(f"  ✅ All symbols cached - no API calls needed")
            return results

        # US client path (Polygon/Alpaca/YFinance) when us_client is provided
        if us_client is not None:
            mt = get_market_tz()
            
            # Adjust date if weekend/holiday: use last trading day
            target_date = date
            if target_date.weekday() >= 5:  # Saturday or Sunday
                # Go back to Friday
                while target_date.weekday() >= 5:
                    target_date -= timedelta(days=1)
                log.info(f"  📅 Adjusted weekend date {date} → {target_date} (last trading day)")
            
            start_d = target_date - timedelta(days=1)
            end_d = target_date
            start_et = mt.localize(datetime.combine(start_d, dt_time(9, 30, 0)))
            end_et = mt.localize(datetime.combine(end_d, dt_time(16, 0, 0)))
            start_utc = et_to_utc(start_et)
            end_utc = et_to_utc(end_et)
            # Do not request future bars: cap to now (market-hours prefetch)
            now_utc = datetime.now(timezone.utc)
            if end_utc > now_utc:
                end_utc = now_utc
            log.info(f"  📥 Fetching {len(uncached_symbols)} symbols via us_client ({start_utc} to {end_utc} UTC)")
            data = us_client.get_ohlcv_many(uncached_symbols, timeframe, start_utc, end_utc)
            for symbol in uncached_symbols:
                cache_key = self._cache_key(date, timeframe, symbol)
                df = data.get(symbol)
                if df is None or df.empty:
                    results[symbol] = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
                    continue
                df = df.copy()
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                df["volume"] = df["volume"].fillna(0).astype(int)
                df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
                self.memory_cache[cache_key] = df
                self._save_to_disk(cache_key, df)
                results[symbol] = df
                log.info(f"  ✅ {symbol}: Fetched {len(df)} bars (us_client)")
            successful_count = len([s for s, df in results.items() if not df.empty])
            log.info(f"✅ PREFETCH COMPLETE (us_client): {successful_count}/{len(symbols)} symbols")
            return results

        log.info(f"  📥 Fetching {len(uncached_symbols)} symbols from yfinance...")
        
        # Batch download uncached symbols.
        #
        # NOTE: yfinance/Yahoo can intermittently return empty/invalid responses when
        # requesting many tickers at once (especially with threaded fetching).
        # Use smaller batches + no threads, and fall back to per-symbol downloads
        # when a batch returns empty.
        batch_size = 5
        missing_symbols_count = 0
        for i in range(0, len(uncached_symbols), batch_size):
            batch_symbols = uncached_symbols[i:i + batch_size]
            batch_num = i//batch_size + 1
            log.info(f"  📦 BATCH {batch_num} FETCH:")
            log.info(f"     - Symbols: {len(batch_symbols)} ({', '.join(batch_symbols[:5])}{'...' if len(batch_symbols) > 5 else ''})")
            log.info(f"     - Timeframe: {timeframe}")
            log.info(f"     - Period: {period}")
            log.info(f"     - Method: yf.download(tickers=list, interval='{timeframe}', period='{period}')")
            
            try:
                # Use yfinance batch download
                data = yf.download(
                    tickers=batch_symbols,
                    interval=timeframe,
                    period=period,
                    prepost=False,
                    group_by="ticker",
                    threads=False,
                    timeout=30,
                    progress=False
                )
                
                # VERIFICATION: Check MultiIndex structure
                if data.empty:
                    log.error(f"  ❌ BATCH {batch_num}: Empty response from yfinance")
                    log.warning(f"  🔁 BATCH {batch_num}: Retrying per-symbol downloads (threads=False)...")
                    for symbol in batch_symbols:
                        try:
                            single = yf.download(
                                tickers=symbol,
                                interval=timeframe,
                                period=period,
                                prepost=False,
                                group_by="ticker",
                                threads=False,
                                timeout=30,
                                progress=False
                            )
                            if single is None or single.empty:
                                results[symbol] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                                missing_symbols_count += 1
                                continue

                            # Process as single-symbol response
                            data_single = single
                            cache_key = self._cache_key(date, timeframe, symbol)

                            if isinstance(data_single.index, pd.DatetimeIndex):
                                timestamps = data_single.index
                            else:
                                timestamps = pd.to_datetime(data_single.index)

                            df = pd.DataFrame({
                                'timestamp': timestamps,
                                'open': data_single['Open'].values,
                                'high': data_single['High'].values,
                                'low': data_single['Low'].values,
                                'close': data_single['Close'].values,
                                'volume': data_single['Volume'].values
                            })

                            # Normalize timestamps to UTC
                            if df['timestamp'].dtype.tz is None:
                                df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(ET_TZ).dt.tz_convert('UTC')
                            else:
                                df['timestamp'] = df['timestamp'].dt.tz_convert(ET_TZ).dt.tz_convert('UTC')

                            for col in ['open', 'high', 'low', 'close', 'volume']:
                                df[col] = pd.to_numeric(df[col], errors='coerce')

                            df.dropna(subset=['timestamp', 'open', 'high', 'low', 'close'], inplace=True)
                            df['volume'] = df['volume'].fillna(0).astype(int)
                            df = df.sort_values('timestamp').drop_duplicates(subset=['timestamp']).reset_index(drop=True)

                            if df.empty:
                                results[symbol] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                                missing_symbols_count += 1
                                continue

                            self.memory_cache[cache_key] = df
                            self._save_to_disk(cache_key, df)
                            results[symbol] = df
                            log.info(f"  ✅ {symbol}: Fetched {len(df)} bars (per-symbol retry)")
                        except Exception as e:
                            log.error(f"  ❌ Error processing {symbol} (per-symbol retry): {e}", exc_info=True)
                            results[symbol] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                            missing_symbols_count += 1
                    continue
                
                # Extract symbols from MultiIndex (handle both single and multi-symbol responses)
                symbols_received = []
                if hasattr(data.columns, 'levels') and len(data.columns.levels) > 0:
                    symbols_received = list(data.columns.levels[0])
                    log.info(f"  ✅ BATCH {batch_num}: Received {len(symbols_received)} symbols from yfinance")
                else:
                    # Single symbol or unexpected structure
                    if len(batch_symbols) == 1:
                        symbols_received = batch_symbols
                        log.info(f"  ✅ BATCH {batch_num}: Single symbol response")
                    else:
                        log.warning(f"  ⚠️ BATCH {batch_num}: Unexpected data structure (not MultiIndex)")
                
                # Process each symbol (handle missing symbols)
                for symbol in batch_symbols:
                    cache_key = self._cache_key(date, timeframe, symbol)
                    
                    try:
                        # Extract symbol data from batch result
                        if len(batch_symbols) == 1:
                            hist = data
                        else:
                            # Multi-level columns when group_by="ticker"
                            if hasattr(data.columns, 'levels') and len(data.columns.levels) > 0:
                                if symbol in data.columns.levels[0]:
                                    hist = data[symbol]
                                else:
                                    # SYMBOL MISSING FROM BATCH - mark and log
                                    log.warning(f"  ⚠️ {symbol}: MISSING_FROM_BATCH (not in MultiIndex)")
                                    log.warning(f"     - Available symbols: {symbols_received[:10]}{'...' if len(symbols_received) > 10 else ''}")
                                    results[symbol] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                                    missing_symbols_count += 1
                                    continue
                            else:
                                hist = None
                        
                        if hist is None or hist.empty:
                            log.warning(f"  ⚠️ {symbol}: No data in batch result (empty DataFrame)")
                            results[symbol] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                            missing_symbols_count += 1
                            continue
                        
                        # Convert to our format
                        if isinstance(hist.index, pd.DatetimeIndex):
                            timestamps = hist.index
                        else:
                            timestamps = pd.to_datetime(hist.index)
                        
                        df = pd.DataFrame({
                            'timestamp': timestamps,
                            'open': hist['Open'].values,
                            'high': hist['High'].values,
                            'low': hist['Low'].values,
                            'close': hist['Close'].values,
                            'volume': hist['Volume'].values
                        })
                        
                        # Normalize timestamps to UTC
                        if df['timestamp'].dtype.tz is None:
                            # yfinance intraday for US equities is effectively ET
                            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(ET_TZ).dt.tz_convert('UTC')
                        else:
                            df['timestamp'] = df['timestamp'].dt.tz_convert(ET_TZ).dt.tz_convert('UTC')
                        
                        # Numeric coercion
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                        
                        df.dropna(subset=['timestamp', 'open', 'high', 'low', 'close'], inplace=True)
                        df['volume'] = df['volume'].fillna(0).astype(int)
                        
                        # Sort and dedupe
                        df = df.sort_values('timestamp').drop_duplicates(subset=['timestamp']).reset_index(drop=True)
                        
                        # CACHE CORRECTNESS ASSERTIONS
                        # Assert: All timestamps are UTC-aware
                        if df['timestamp'].dtype.tz is None:
                            log.error(f"  ❌ {symbol}: Timestamps are timezone-naive (should be UTC-aware)")
                            raise ValueError(f"Timezone-naive timestamps in cache for {symbol}")
                        
                        # Assert: Timestamps are UTC
                        if str(df['timestamp'].dtype.tz) != 'UTC':
                            log.error(f"  ❌ {symbol}: Timestamps are not UTC (got {df['timestamp'].dtype.tz})")
                            raise ValueError(f"Non-UTC timestamps in cache for {symbol}")
                        
                        # Assert: Sorted ascending
                        if not df['timestamp'].is_monotonic_increasing:
                            log.error(f"  ❌ {symbol}: Timestamps are not sorted ascending")
                            raise ValueError(f"Unsorted timestamps in cache for {symbol}")
                        
                        # Assert: No duplicate timestamps
                        if df['timestamp'].duplicated().any():
                            log.error(f"  ❌ {symbol}: Duplicate timestamps found")
                            raise ValueError(f"Duplicate timestamps in cache for {symbol}")
                        
                        # Store in cache
                        self.memory_cache[cache_key] = df
                        self._save_to_disk(cache_key, df)
                        results[symbol] = df
                        
                        log.info(f"  ✅ {symbol}: Fetched {len(df)} bars (UTC-aware, sorted, deduped)")
                    except Exception as e:
                        log.error(f"  ❌ Error processing {symbol}: {e}", exc_info=True)
                        results[symbol] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

                # Log missing symbols count for this batch
                if missing_symbols_count > 0:
                    log.warning(f"  ⚠️ BATCH {batch_num}: {missing_symbols_count} symbols missing from batch result")
                
            except Exception as e:
                log.error(f"  ❌ Batch download failed: {e}", exc_info=True)
                # Initialize empty DataFrames for failed symbols
                for symbol in batch_symbols:
                    if symbol not in results:
                        results[symbol] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        successful_count = len([s for s, df in results.items() if not df.empty])
        log.info(f"✅ PREFETCH COMPLETE:")
        log.info(f"   - Successful: {successful_count}/{len(symbols)} symbols")
        log.info(f"   - Missing from batch: {missing_symbols_count} symbols")
        log.info(f"   - Cached: {len(cached_symbols)} symbols")
        log.info(f"   - Fetched: {len(uncached_symbols)} symbols")
        return results
    
    def get_slice(
        self,
        symbol: str,
        start_utc: datetime,
        end_utc: datetime,
        timeframe: str = "5m"
    ) -> pd.DataFrame:
        """
        Get cached data slice for a symbol within a time range.
        
        VERIFICATION: Ensures all returned data is UTC-aware, sorted, and deduped.
        
        Args:
            symbol: Symbol to get data for
            start_utc: Start time (UTC-aware)
            end_utc: End time (UTC-aware, exclusive)
            timeframe: Bar timeframe
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        # EXPLICIT LOGGING: Cache read (not API call)
        log.debug(f"  📖 CACHE READ: {symbol} (window: {start_utc} to {end_utc} UTC, timeframe: {timeframe})")
        log.debug(f"     - NO API CALL: Using cached data")
        
        # Ensure inputs are UTC-aware
        start_utc = self._ensure_utc(start_utc)
        end_utc = self._ensure_utc(end_utc)
        
        # Try to get from cache (check both today and yesterday)
        target_date = utc_to_et(end_utc).date()
        yesterday = target_date - timedelta(days=1)
        
        for date in [target_date, yesterday]:
            cache_key = self._cache_key(date, timeframe, symbol)
            
            if cache_key in self.memory_cache:
                df = self.memory_cache[cache_key]
                # Filter to requested range (end-exclusive)
                mask = (df['timestamp'] >= start_utc) & (df['timestamp'] < end_utc)
                slice_df = df[mask].copy()
                
                # CACHE CORRECTNESS ASSERTIONS
                if not slice_df.empty:
                    # Assert: UTC-aware
                    if slice_df['timestamp'].dtype.tz is None:
                        raise ValueError(f"Timezone-naive timestamps in slice for {symbol}")
                    if str(slice_df['timestamp'].dtype.tz) != 'UTC':
                        raise ValueError(f"Non-UTC timestamps in slice for {symbol}")
                    # Assert: Sorted
                    if not slice_df['timestamp'].is_monotonic_increasing:
                        slice_df = slice_df.sort_values('timestamp').reset_index(drop=True)
                    # Assert: No duplicates
                    if slice_df['timestamp'].duplicated().any():
                        slice_df = slice_df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
                
                log.debug(f"     - Retrieved {len(slice_df)} bars from cache")
                return slice_df
        
        # Not in cache - return empty
        log.warning(f"  ⚠️ No cached data for {symbol} (date: {target_date}, timeframe: {timeframe})")
        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        """Ensure datetime is UTC-aware"""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        if str(dt.tzinfo) != 'UTC':
            return dt.astimezone(timezone.utc)
        return dt
    
    def get_indicator_slab(
        self,
        symbol: str,
        snapshot_time_utc: datetime,
        lookback_bars: int = 120,
        timeframe: str = "5m"
    ) -> pd.DataFrame:
        """
        Get indicator lookback slab (last N bars ending at snapshot time).
        
        VERIFICATION: Ensures returned data is UTC-aware, sorted, deduped.
        
        Args:
            symbol: Symbol to get data for
            snapshot_time_utc: Snapshot timestamp (UTC-aware)
            lookback_bars: Number of bars to look back
            timeframe: Bar timeframe
        
        Returns:
            DataFrame with last N bars ending at snapshot_time_utc
        """
        # Ensure UTC-aware
        snapshot_time_utc = self._ensure_utc(snapshot_time_utc)
        
        # Calculate start time (lookback_bars bars before snapshot_time)
        timeframe_minutes = int(timeframe.replace('m', ''))
        lookback_minutes = lookback_bars * timeframe_minutes
        start_utc = snapshot_time_utc - timedelta(minutes=lookback_minutes)
        
        # EXPLICIT LOGGING: Indicator slab request (cache read, not API call)
        log.debug(f"  📖 INDICATOR SLAB REQUEST: {symbol}")
        log.debug(f"     - NO API CALL: Using cached data")
        log.debug(f"     - Window: {start_utc} to {snapshot_time_utc} UTC")
        log.debug(f"     - Lookback: {lookback_bars} bars ({lookback_minutes} minutes)")
        
        # Get slice from cache
        df = self.get_slice(symbol, start_utc, snapshot_time_utc, timeframe)
        
        # Take last N bars
        if len(df) > lookback_bars:
            df = df.tail(lookback_bars).reset_index(drop=True)
        
        # Final verification
        if not df.empty:
            # Assert: UTC-aware
            if df['timestamp'].dtype.tz is None or str(df['timestamp'].dtype.tz) != 'UTC':
                raise ValueError(f"Indicator slab for {symbol} has non-UTC timestamps")
            # Assert: Sorted
            if not df['timestamp'].is_monotonic_increasing:
                df = df.sort_values('timestamp').reset_index(drop=True)
            # Assert: No duplicates
            if df['timestamp'].duplicated().any():
                df = df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
        
        log.debug(f"     - Retrieved {len(df)} bars for indicator slab")
        return df
    
    def refresh_cache(
        self,
        symbols: List[str],
        timeframe: str = "5m",
        period: str = "1d"
    ) -> Dict[str, pd.DataFrame]:
        """
        Refresh cache with latest data (uses period="1d" for today's data only).
        
        Called at ~10:35 ET and ~15:50 ET to keep SIGNAL/OUTCOME bars current.
        
        Args:
            symbols: List of symbols to refresh
            timeframe: Bar timeframe
            period: yfinance period ("1d" for today only)
        
        Returns:
            Dict mapping symbol to updated DataFrame
        """
        today = datetime.now(get_market_tz()).date()
        log.info(f"🔄 Refreshing cache for {len(symbols)} symbols (period: {period})")
        
        # Prefetch with period="1d" (today's data)
        results = self.prefetch(today, symbols, timeframe, period)
        
        # Merge with existing cache (update today's data)
        for symbol, new_df in results.items():
            if not new_df.empty:
                cache_key = self._cache_key(today, timeframe, symbol)
                
                # Merge with existing cache if present
                if cache_key in self.memory_cache:
                    existing_df = self.memory_cache[cache_key]
                    # Combine and dedupe
                    combined = pd.concat([existing_df, new_df]).sort_values('timestamp')
                    combined = combined.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
                    self.memory_cache[cache_key] = combined
                    self._save_to_disk(cache_key, combined)
                    log.debug(f"  ✅ {symbol}: Merged refresh ({len(combined)} bars)")
                else:
                    # New entry
                    self.memory_cache[cache_key] = new_df
                    self._save_to_disk(cache_key, new_df)
                    log.debug(f"  ✅ {symbol}: Added refresh ({len(new_df)} bars)")
        
        log.info(f"✅ Cache refresh complete")
        return results


# Global cache instance
_cache_instance: Optional[USIntradayCache] = None


def get_cache() -> USIntradayCache:
    """Get global cache instance (singleton)"""
    global _cache_instance
    if _cache_instance is None:
        settings = get_settings()
        cache_dir = settings.resolved_local_storage_path / "us_intraday_cache"
        _cache_instance = USIntradayCache(enable_disk_cache=True, cache_dir=cache_dir)
    return _cache_instance
