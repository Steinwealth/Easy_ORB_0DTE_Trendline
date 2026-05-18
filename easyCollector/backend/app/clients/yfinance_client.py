"""
Easy Collector - YFinance US Market Data Client
Thin adapter for Yahoo Finance (yfinance) with retry logic and rate limiting.
E*TRADE is not used; US OHLCV is from yfinance only. Use Polygon or Alpaca as primary in Cloud Run.
"""

import logging
import time
from datetime import datetime, timedelta, time as dt_time, timezone
from typing import Dict, Optional, List
import pandas as pd
import pytz
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.clients.base_client import BaseMarketDataClient
from app.config import get_settings
from app.utils.time_utils import et_to_utc, utc_to_et, get_us_orb_time, ensure_tz, get_market_tz

log = logging.getLogger(__name__)

ET_TZ = pytz.timezone("America/New_York")


def _normalize_hist_index_to_utc(hist: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize yfinance DataFrame index from ET (or tz-naive) to UTC.
    """
    if hist is None or hist.empty:
        return hist
    idx = hist.index
    if idx.tz is None:
        idx = idx.tz_localize(ET_TZ)
    else:
        idx = idx.tz_convert(ET_TZ)
    hist = hist.copy()
    hist.index = idx.tz_convert("UTC")
    return hist


def _in_us_market_hours(dt_et: datetime) -> bool:
    """True if dt_et (ET) is within US regular session Mon–Fri 9:30–16:00."""
    if dt_et.weekday() >= 5:
        return False
    t = dt_et.time()
    return dt_time(9, 30, 0) <= t <= dt_time(16, 0, 0)


class MarketDataUnavailable(Exception):
    """Exception for when market data is unavailable (should trigger retry)"""
    pass


class YFinanceClient(BaseMarketDataClient):
    """YFinance market data client for US 0DTE symbols. No E*TRADE."""

    _circuit_breaker_tripped = False
    _circuit_breaker_until = None
    _last_batch_time = None
    _consecutive_failures = 0

    def __init__(self):
        settings = get_settings()
        self.timeout = 15
        self.max_retries = 3
        log.info("YFinance client initialized (US OHLCV only; no E*TRADE)")

    def _check_orb_integrity(
        self,
        df: pd.DataFrame,
        snapshot_type: str,
        timeframe: str,
        timestamp_et: datetime
    ) -> bool:
        """Check data integrity for ORB snapshots (ORB window slice)."""
        if snapshot_type != "ORB" or timeframe not in ['1m', '5m']:
            return True
        orb_time = get_us_orb_time(timestamp_et)
        if abs((timestamp_et - orb_time).total_seconds()) > 300:
            return True
        timeframe_minutes = int(timeframe.replace('m', ''))
        expected_bars = 15 // timeframe_minutes
        market_open_et = ensure_tz(timestamp_et.replace(hour=9, minute=30, second=0, microsecond=0), get_market_tz())
        orb_end_et = orb_time
        start_utc = et_to_utc(market_open_et)
        end_utc = et_to_utc(orb_end_et)
        orb_df = df[(df['timestamp'] >= start_utc) & (df['timestamp'] < end_utc)]
        if len(orb_df) < expected_bars:
            log.warning("ORB integrity failed: expected %s bars, got %s", expected_bars, len(orb_df))
            return False
        return True

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        snapshot_type: Optional[str] = None
    ) -> pd.DataFrame:
        """Get OHLCV from yfinance for a US symbol."""
        start_utc = self.ensure_utc(start_utc)
        end_utc = self.ensure_utc(end_utc)

        import yfinance as yf

        interval_map = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '1d': '1d'}
        yf_interval = interval_map.get(timeframe, '5m')
        fetch_start_utc = start_utc - timedelta(minutes=5)
        fetch_end_utc = end_utc + timedelta(minutes=5)

        actual_symbol = symbol
        use_spy_proxy = False
        if symbol == "SPX" and yf_interval in ['1m', '5m', '15m']:
            actual_symbol = "SPY"
            use_spy_proxy = True

        max_retries = 3
        hist = None
        for attempt in range(max_retries):
            try:
                ticker = yf.Ticker(actual_symbol)
                if yf_interval in ['1m', '5m', '15m']:
                    try:
                        hist = ticker.history(
                            start=fetch_start_utc, end=fetch_end_utc,
                            interval=yf_interval, prepost=False, timeout=self.timeout, raise_errors=False
                        )
                    except Exception:
                        hist = ticker.history(period="1d", interval=yf_interval, prepost=False, timeout=self.timeout, raise_errors=False)
                    if hist is not None and not hist.empty:
                        hist = _normalize_hist_index_to_utc(hist)
                        hist = hist[(hist.index >= start_utc) & (hist.index < end_utc)]
                        if not hist.empty:
                            break
                else:
                    start_str = fetch_start_utc.strftime('%Y-%m-%d')
                    end_str = fetch_end_utc.strftime('%Y-%m-%d')
                    hist = ticker.history(start=start_str, end=end_str, interval=yf_interval, prepost=False, timeout=self.timeout, raise_errors=False)
                    if hist is not None and not hist.empty:
                        hist = _normalize_hist_index_to_utc(hist)
                        hist = hist[(hist.index >= start_utc) & (hist.index < end_utc)]
                        if not hist.empty:
                            break
                if (hist is None or hist.empty) and attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                    hist = None
                elif hist is None or hist.empty:
                    raise MarketDataUnavailable(f"yfinance empty for {symbol} {timeframe} after {max_retries} attempts")
            except MarketDataUnavailable:
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                    hist = None
                else:
                    raise MarketDataUnavailable(f"yfinance error for {symbol}: {e}")

        if hist is None or hist.empty:
            raise MarketDataUnavailable(f"No OHLCV from yfinance for {symbol} ({timeframe})")

        if isinstance(hist.index, pd.DatetimeIndex):
            timestamps = hist.index
        else:
            timestamps = pd.to_datetime(hist.index)

        if use_spy_proxy:
            scale = 10.0
            open_vals = hist['Open'].values * scale
            high_vals = hist['High'].values * scale
            low_vals = hist['Low'].values * scale
            close_vals = hist['Close'].values * scale
        else:
            open_vals = hist['Open'].values
            high_vals = hist['High'].values
            low_vals = hist['Low'].values
            close_vals = hist['Close'].values

        df = pd.DataFrame({
            'timestamp': timestamps, 'open': open_vals, 'high': high_vals, 'low': low_vals,
            'close': close_vals, 'volume': hist['Volume'].values
        })
        if df['timestamp'].dtype.tz is None:
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(ET_TZ).dt.tz_convert('UTC')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert(ET_TZ).dt.tz_convert('UTC')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['timestamp', 'open', 'high', 'low', 'close'], inplace=True)
        df['volume'] = df['volume'].fillna(0).astype(int)
        df = df[(df['timestamp'] >= start_utc) & (df['timestamp'] < end_utc)]
        df = df.sort_values('timestamp').reset_index(drop=True)
        if snapshot_type:
            self._check_orb_integrity(df, snapshot_type, timeframe, utc_to_et(end_utc))
        return df

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((MarketDataUnavailable, ConnectionError, TimeoutError, ValueError))
    )
    def get_ohlcv_with_retry(self, symbol: str, timeframe: str, start_utc: datetime, end_utc: datetime, snapshot_type: Optional[str] = None) -> pd.DataFrame:
        return self.get_ohlcv(symbol, timeframe, start_utc, end_utc, snapshot_type)

    def get_ohlcv_many(
        self,
        symbols: List[str],
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        chunk_size: int = 25
    ) -> Dict[str, pd.DataFrame]:
        """Batch OHLCV via yf.download. Circuit breaker and adaptive batching same as before."""
        import yfinance as yf

        if YFinanceClient._circuit_breaker_tripped and YFinanceClient._circuit_breaker_until and datetime.now(timezone.utc) < YFinanceClient._circuit_breaker_until:
            return {s: pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']) for s in symbols}
        if YFinanceClient._circuit_breaker_tripped and (not YFinanceClient._circuit_breaker_until or datetime.now(timezone.utc) >= YFinanceClient._circuit_breaker_until):
            YFinanceClient._circuit_breaker_tripped = False
            YFinanceClient._circuit_breaker_until = None
            YFinanceClient._consecutive_failures = 0

        start_utc = self.ensure_utc(start_utc)
        end_utc = self.ensure_utc(end_utc)
        interval_map = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '1d': '1d'}
        yf_interval = interval_map.get(timeframe, '5m')
        fetch_start_utc = start_utc - timedelta(minutes=5)
        fetch_end_utc = end_utc + timedelta(minutes=5)

        results = {s: pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']) for s in symbols}
        valid = [s for s in symbols if s and isinstance(s, str) and s.strip()]
        if not valid:
            return results

        adaptive_batch_sizes = [chunk_size, 10, 5, 1]
        batch_size_index = 0
        remaining = valid.copy()
        base_delay = 2.0 if yf_interval in ['1m', '5m', '15m'] else 15.0

        while remaining and batch_size_index < len(adaptive_batch_sizes):
            sz = adaptive_batch_sizes[batch_size_index]
            chunks = [remaining[i:i + sz] for i in range(0, len(remaining), sz)]
            batch_delay = base_delay * (batch_size_index + 1)

            for chunk_symbols in chunks:
                batch_symbols = []
                spx_map = {}
                for s in chunk_symbols:
                    if s == "SPX" and yf_interval in ['1m', '5m', '15m']:
                        batch_symbols.append("SPY")
                        spx_map[s] = True
                    else:
                        batch_symbols.append(s)
                        spx_map[s] = False

                start_str = fetch_start_utc.strftime('%Y-%m-%d %H:%M:%S')
                end_str = fetch_end_utc.strftime('%Y-%m-%d %H:%M:%S')
                data = None
                for _ in range(5):
                    try:
                        data = yf.download(tickers=batch_symbols, interval=yf_interval, start=start_str, end=end_str,
                                           prepost=False, group_by="ticker", threads=True, timeout=self.timeout * 2, progress=False)
                        if data is not None and not data.empty:
                            break
                    except Exception as e:
                        log.warning("yfinance batch error: %s", e)
                        time.sleep(2)
                if data is None or data.empty:
                    for s in chunk_symbols:
                        if s in remaining:
                            remaining.remove(s)
                    continue

                for symbol in chunk_symbols:
                    dl = "SPY" if spx_map.get(symbol) else symbol
                    if len(chunk_symbols) == 1:
                        hist = data
                    else:
                        if hasattr(data.columns, 'levels') and len(data.columns.levels) > 0 and dl in data.columns.levels[0]:
                            hist = data[dl]
                        else:
                            continue
                    if hist is None or hist.empty:
                        continue
                    if isinstance(hist.index, pd.DatetimeIndex):
                        ts = hist.index
                    else:
                        ts = pd.to_datetime(hist.index)
                    scale = 10.0 if spx_map.get(symbol) else 1.0
                    df = pd.DataFrame({
                        'timestamp': ts,
                        'open': hist['Open'].values * scale, 'high': hist['High'].values * scale,
                        'low': hist['Low'].values * scale, 'close': hist['Close'].values * scale,
                        'volume': hist['Volume'].values
                    })
                    if df['timestamp'].dtype.tz is None:
                        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(ET_TZ).dt.tz_convert('UTC')
                    else:
                        df['timestamp'] = df['timestamp'].dt.tz_convert(ET_TZ).dt.tz_convert('UTC')
                    for c in ['open', 'high', 'low', 'close', 'volume']:
                        df[c] = pd.to_numeric(df[c], errors='coerce')
                    df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
                    df['volume'] = df['volume'].fillna(0).astype(int)
                    df = df[(df['timestamp'] >= start_utc) & (df['timestamp'] < end_utc)].sort_values('timestamp').reset_index(drop=True)
                    results[symbol] = df

                for s in chunk_symbols:
                    if s in remaining:
                        remaining.remove(s)
                time.sleep(batch_delay)

            if not remaining:
                break
            batch_size_index += 1

        return results

    def get_last_price(self, symbol: str) -> float:
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = getattr(ticker, 'fast_info', None)
            price = getattr(info, 'lastPrice', 0) if info else 0
            return float(price) if price and price > 0 else 0.0
        except Exception:
            return 0.0

    def get_symbol_metadata(self, symbol: str) -> Dict:
        return {"symbol": symbol, "market": "US", "tick_size": 0.01, "trading_hours": "09:30-16:00 ET", "currency": "USD", "source": "yfinance"}

    def healthcheck(self) -> Dict:
        """
        Strict healthcheck: real 5m fetch for SPY. Returns healthy only if:
        - rows >= bars - 2
        - no NaNs in open/high/low/close
        - last bar timestamp within 30 min (market hours) or 4h (outside)
        """
        try:
            settings = get_settings()
            sym = getattr(settings, 'us_provider_healthcheck_symbol', 'SPY')
            bars = max(getattr(settings, 'us_provider_healthcheck_bars', 20), 12)
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=bars * 5 + 30)
            df = self.get_ohlcv(sym, "5m", start, end, snapshot_type=None)
            n = len(df)
            if n < max(bars - 2, 10):
                return {"status": "unhealthy", "provider": "YFINANCE", "message": f"Too few bars: {n}", "row_count": n}
            for col in ['open', 'high', 'low', 'close']:
                if col in df.columns and df[col].isna().any():
                    return {"status": "unhealthy", "provider": "YFINANCE", "message": f"NaN in {col}", "row_count": n}
            last_ts = df["timestamp"].max()
            if pd.isna(last_ts):
                return {"status": "unhealthy", "provider": "YFINANCE", "message": "No last timestamp", "row_count": n}
            last_utc = last_ts if hasattr(last_ts, 'tzinfo') and last_ts.tzinfo else pd.Timestamp(last_ts, tz="UTC")
            if hasattr(last_utc, 'to_pydatetime'):
                last_utc = last_utc.to_pydatetime()
            age_sec = (datetime.now(timezone.utc) - last_utc).total_seconds()
            now_et = datetime.now(get_market_tz())
            max_age = 30 * 60 if _in_us_market_hours(now_et) else 4 * 3600
            if age_sec > max_age:
                return {"status": "unhealthy", "provider": "YFINANCE", "message": f"Last bar too old: {age_sec/60:.0f} min", "row_count": n, "last_bar_age_seconds": age_sec}
            sample = df.tail(3)[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records") if n >= 3 else []
            for r in sample:
                if "timestamp" in r:
                    r["timestamp"] = str(r["timestamp"])
            return {
                "status": "healthy",
                "provider": "YFINANCE",
                "message": "OK",
                "row_count": n,
                "min_timestamp": str(df["timestamp"].min()),
                "max_timestamp": str(df["timestamp"].max()),
                "sample_last_3": sample,
                "last_bar_age_seconds": age_sec,
            }
        except Exception as e:
            log.warning("YFinanceClient healthcheck: %s", e)
            return {"status": "unhealthy", "provider": "YFINANCE", "error": str(e)}
