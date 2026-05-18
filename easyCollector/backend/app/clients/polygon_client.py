"""
Easy Collector - Polygon.io US Equity Market Data Client
Reliable US intraday aggregates when yfinance is blocked or unreliable (e.g. Cloud Run).
Uses Polygon Aggregates API: /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
import httpx

from app.clients.base_client import BaseMarketDataClient
from app.config import get_settings
from app.utils.time_utils import get_market_tz, get_last_us_session_2h_window_utc

log = logging.getLogger(__name__)


def _in_us_market_hours(dt_et) -> bool:
    """True if dt_et (ET) is within US regular session Mon–Fri 9:30–16:00."""
    from datetime import time as dt_time
    if dt_et.weekday() >= 5:
        return False
    return dt_time(9, 30, 0) <= dt_et.time() <= dt_time(16, 0, 0)

POLYGON_AGGS_URL = "https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_ts}/{to_ts}"


def _ms(dt: datetime) -> int:
    """Datetime to Unix milliseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _tf_to_mult_timespan(timeframe: str) -> tuple:
    """Map timeframe to (multiplier, timespan) for Polygon. E.g. 5m -> (5, 'minute')."""
    m = (timeframe or "5m").lower()
    if m == "1m":
        return 1, "minute"
    if m == "5m":
        return 5, "minute"
    if m == "15m":
        return 15, "minute"
    if m == "1h":
        return 1, "hour"
    if m == "1d":
        return 1, "day"
    return 5, "minute"


class PolygonClient(BaseMarketDataClient):
    """Polygon.io client for US equity OHLCV. Implements get_ohlcv and get_ohlcv_many with bounded concurrency."""

    def __init__(self, api_key: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key or settings.polygon_api_key
        self.timeout = 20
        self._session: Optional[httpx.Client] = None
        self._max_workers = 15  # Bounded concurrency for get_ohlcv_many

    def _get_session(self) -> httpx.Client:
        if self._session is None or self._session.is_closed:
            self._session = httpx.Client(timeout=self.timeout)
        return self._session

    def close(self):
        if self._session and not self._session.is_closed:
            self._session.close()
            self._session = None

    def _fetch_aggs(self, symbol: str, mult: int, timespan: str, from_ms: int, to_ms: int) -> pd.DataFrame:
        """Fetch aggregates for one symbol. Returns DataFrame with timestamp, open, high, low, close, volume."""
        if not self.api_key:
            log.warning("PolygonClient: POLYGON_API_KEY not set")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        # SPX: Polygon uses $SPX or we use SPY as proxy for intraday (same as yfinance)
        ticker = "SPY" if symbol == "SPX" and timespan == "minute" else symbol

        url = POLYGON_AGGS_URL.format(
            ticker=ticker,
            multiplier=mult,
            timespan=timespan,
            from_ts=from_ms,
            to_ts=to_ms,
        )
        try:
            r = self._get_session().get(url, params={"apiKey": self.api_key, "adjusted": "true"})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("PolygonClient _fetch_aggs %s: %s", ticker, e)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        results = data.get("results") or []
        if not results:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        rows = []
        for b in results:
            t = b.get("t")  # epoch ms
            o, h, l, c = b.get("o"), b.get("h"), b.get("l"), b.get("c")
            v = b.get("v", 0)
            if t is None or o is None:
                continue
            ts = pd.Timestamp(t, unit="ms", tz="UTC")
            rows.append({"timestamp": ts, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": int(v) if v is not None else 0})

        df = pd.DataFrame(rows)
        df = df.sort_values("timestamp").reset_index(drop=True)

        if symbol == "SPX" and timespan == "minute":
            # Scale SPY to SPX (~10x)
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col] * 10.0

        return df

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        snapshot_type: Optional[str] = None,
    ) -> pd.DataFrame:
        start_utc = self.ensure_utc(start_utc, assume_tz=timezone.utc)
        end_utc = self.ensure_utc(end_utc, assume_tz=timezone.utc)
        # Do not request future bars: cap end to now
        now_utc = datetime.now(timezone.utc)
        if end_utc > now_utc:
            end_utc = now_utc
        mult, timespan = _tf_to_mult_timespan(timeframe)
        from_ms, to_ms = _ms(start_utc), _ms(end_utc)

        df = self._fetch_aggs(symbol, mult, timespan, from_ms, to_ms)
        if df.empty:
            return df

        df = df[(df["timestamp"] >= start_utc) & (df["timestamp"] < end_utc)]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
        df["volume"] = df["volume"].fillna(0).astype(int)
        return df.sort_values("timestamp").reset_index(drop=True)

    def get_ohlcv_many(
        self,
        symbols: List[str],
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        chunk_size: int = 25,
    ) -> Dict[str, pd.DataFrame]:
        start_utc = self.ensure_utc(start_utc, assume_tz=timezone.utc)
        end_utc = self.ensure_utc(end_utc, assume_tz=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        if end_utc > now_utc:
            end_utc = now_utc
        mult, timespan = _tf_to_mult_timespan(timeframe)
        from_ms, to_ms = _ms(start_utc), _ms(end_utc)

        out: Dict[str, pd.DataFrame] = {s: pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]) for s in symbols}

        def do_one(sym: str) -> tuple:
            df = self._fetch_aggs(sym, mult, timespan, from_ms, to_ms)
            return sym, df

        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(symbols))) as ex:
            futs = {ex.submit(do_one, s): s for s in symbols}
            for f in as_completed(futs):
                try:
                    sym, df = f.result()
                    if not df.empty:
                        df = df[(df["timestamp"] >= start_utc) & (df["timestamp"] < end_utc)]
                        for c in ["open", "high", "low", "close", "volume"]:
                            df[c] = pd.to_numeric(df[c], errors="coerce")
                        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                        df["volume"] = df["volume"].fillna(0).astype(int)
                        out[sym] = df.sort_values("timestamp").reset_index(drop=True)
                    time.sleep(0.05)  # Light pacing
                except Exception as e:
                    log.warning("PolygonClient get_ohlcv_many symbol %s: %s", futs.get(f), e)

        return out

    def get_last_price(self, symbol: str) -> float:
        # Polygon has a prior-close/last-quote API; minimal impl for healthcheck
        try:
            # Use a tiny range around now to get last bar
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=10)
            df = self.get_ohlcv(symbol, "1m", start, end)
            if not df.empty:
                return float(df["close"].iloc[-1])
        except Exception:
            pass
        return 0.0

    def get_symbol_metadata(self, symbol: str) -> Dict:
        return {"symbol": symbol, "market": "US", "tick_size": 0.01, "trading_hours": "09:30-16:00 ET", "currency": "USD", "source": "polygon"}

    def healthcheck(self) -> Dict:
        """Strict healthcheck: real 5m fetch. Healthy only if rows>=bars-2, no NaNs in OHLC, last bar age OK.
        Outside US market hours (9:30–16:00 ET Mon–Fri), uses the last trading session's 14:00–16:00 ET
        so the check can pass 24/7 when the key and API work."""
        try:
            settings = get_settings()
            sym = settings.us_provider_healthcheck_symbol
            bars = max(settings.us_provider_healthcheck_bars, 12)
            mt = get_market_tz()
            now_et = datetime.now(mt)

            if _in_us_market_hours(now_et):
                end = datetime.now(timezone.utc)
                start = end - timedelta(minutes=bars * 5 + 10)
            else:
                # Outside market hours: use last 2h of the most recent US session (14:00–16:00 ET)
                start, end = get_last_us_session_2h_window_utc()

            df = self.get_ohlcv(sym, "5m", start, end)
            n = len(df)
            if n < max(bars - 2, 10):
                return {"status": "unhealthy", "provider": "POLYGON", "message": f"Too few bars: {n}", "row_count": n}
            for col in ["open", "high", "low", "close"]:
                if col in df.columns and df[col].isna().any():
                    return {"status": "unhealthy", "provider": "POLYGON", "message": f"NaN in {col}", "row_count": n}
            last_ts = df["timestamp"].max()
            if pd.isna(last_ts):
                return {"status": "unhealthy", "provider": "POLYGON", "message": "No last timestamp", "row_count": n}
            last_utc = pd.Timestamp(last_ts)
            if last_utc.tzinfo is None:
                last_utc = last_utc.tz_localize("UTC")
            last_utc = last_utc.to_pydatetime()
            age_sec = (datetime.now(timezone.utc) - last_utc).total_seconds()
            # When outside market hours we used the last session's window; allow old bars. In-session: 30min.
            in_hours = _in_us_market_hours(now_et)
            max_age = 30 * 60 if in_hours else 7 * 86400  # 7d when using last-session window
            if age_sec > max_age:
                return {"status": "unhealthy", "provider": "POLYGON", "message": f"Last bar too old: {age_sec/60:.0f} min", "row_count": n, "last_bar_age_seconds": age_sec}
            mn, mx = df["timestamp"].min(), df["timestamp"].max()
            sample = df.tail(3)[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records") if n >= 3 else []
            for r in sample:
                if "timestamp" in r:
                    r["timestamp"] = str(r["timestamp"])
            return {
                "status": "healthy",
                "provider": "POLYGON",
                "message": "OK",
                "row_count": n,
                "min_timestamp": str(mn),
                "max_timestamp": str(mx),
                "sample_last_3": sample,
                "last_bar_age_seconds": age_sec,
            }
        except Exception as e:
            log.warning("PolygonClient healthcheck: %s", e)
            return {"status": "unhealthy", "provider": "POLYGON", "error": str(e)}
