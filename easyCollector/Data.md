# Data Summary: Easy Collector Snapshot Data

**Purpose**: Reference for snapshot data scope, size, methods, and sources. Use this document to validate that the data solution is correct and to troubleshoot snapshot failures (e.g. Polygon key/403, yfinance empty/JSON, Coinbase validation).

**Last Updated**: January 2025

---

## 1. Scope of Snapshot Data Requests

### 1.1 US 0DTE

| Item | Value |
|------|-------|
| **Symbol source** | `data/watchlist/0dte_list.csv` (111 symbols). Fallback: `config.us_symbols` (~24) if CSV not found. |
| **Snapshot types** | ORB, SIGNAL, OUTCOME |
| **Session** | US market: 9:30–16:00 ET (or early close). |
| **Bar timeframe** | `config.timeframe` = **5m** |

**OHLCV window (per snapshot type)** — *`_get_us_ohlcv_window`*:

- **Start**: always **9:30 ET** (market open).
- **End** (exclusive):
  - **ORB**: 9:45 ET (`orb_end_dt`).
  - **SIGNAL**: 10:30 ET (`signal_dt`).
  - **OUTCOME**: 15:55 ET or `early_close - 5 min` (`outcome_dt`).

**Approximate bars per symbol per snapshot** (5m):

- ORB: 3 bars (9:30, 9:35, 9:40).
- SIGNAL: 12 bars (9:30 → 10:25).
- OUTCOME: ~78 bars (9:30 → 15:50, full session) or fewer on early-close days.

### 1.2 Crypto (PERP)

| Item | Value |
|------|-------|
| **Symbols** | `config.crypto_symbols`: **BTC-PERP, ETH-PERP, SOL-PERP, XRP-PERP** (4). Mapped to Coinbase: BTC-USD, ETH-USD, SOL-USD, XRP-USD. |
| **Snapshot types** | ORB, SIGNAL, OUTCOME |
| **Sessions** | LONDON (03:00 ET), US (08:00 ET), RESET (17:00 ET), ASIA (19:00 ET). |
| **Bar timeframe** | 5m → `granularity=300` (seconds). |

**OHLCV window (per session + snapshot type)** — *`_get_crypto_ohlcv_window`*:

- **Start**: session open (config: `crypto_london_open`, `crypto_us_open`, `crypto_reset_open`, `crypto_asia_open`).
- **End** (exclusive):
  - **ORB**: `orb_end_dt` = session open + 15 minutes.
  - **SIGNAL**: `signal_dt` = session open + 60 minutes.
  - **OUTCOME**: `outcome_dt` = next session open − 5 minutes.

**Indicator slab (crypto)** — one slab per symbol per snapshot:

- Slab size: `config.crypto_indicator_slab_bars` (default **180**). `slab_start_utc = snapshot_time_utc − (slab_bars × granularity_seconds)`.
- 5m, 180 bars → 180 × 300 s = **15 hours**. (300 bars = 25h when slab_bars=300.)
- End of slab: `snapshot_time_utc`.

---

## 2. Size of Data Requested

### 2.1 US — Prefetch (Cache)

| Item | Value |
|------|-------|
| **Method** | `USIntradayCache.prefetch(us_client=...)`. When `us_client` is set (Polygon or Alpaca): `us_client.get_ohlcv_many()` for 2 market days. When `us_client` is None: `yf.download(...)` (yfinance fallback). |
| **Batch size** | yfinance path: **5** symbols per batch. Polygon path: `get_ohlcv_many` over requested symbol list. |
| **Period / window** | 2 market days (9:30–16:00 ET); `end_utc` capped to now. |
| **Interval** | `interval=timeframe` (5m). |
| **Bars per symbol (approx)** | 2 days × ~78 bars/day (6.5h at 5m) ≈ **156 bars** (market hours only). |
| **On empty (yfinance path)** | Per-symbol `yf.download(tickers=symbol, ...)` with same params. |

### 2.2 US — Indicator Slab

| Item | Value |
|------|-------|
| **Lookback** | `config.indicator_lookback_bars` = **120** bars. |
| **Source** | Sliced from prefetched/cached data (no extra API call when cache hit). |
| **Time span (5m)** | 120 × 5 min = 600 min = 10 hours. |

### 2.3 US — Snapshot Window (from cache or direct)

- **ORB**: 3 bars.
- **SIGNAL**: 12 bars.
- **OUTCOME**: up to ~78 bars (or less on early close).

### 2.4 Crypto — Indicator Slab

| Item | Value |
|------|-------|
| **Candles** | `config.crypto_indicator_slab_bars` = **180** (default). Configurable; max 300 (Coinbase limit). |
| **Time span (5m)** | 180 × 5 min = **15 hours** (default). 300 bars = 25h when slab set to 300. |
| **Coinbase limit** | `MAX_CANDLES_PER_REQUEST = 300`; one request covers the slab when slab size ≤ 300. |

### 2.5 Crypto — Snapshot Window

- Sliced from the indicator slab (see §2.4); bar count depends on session and snapshot type (ORB ~3, SIGNAL ~12, OUTCOME variable).

---

## 3. Methods

### 3.1 US — Polygon (primary) and yfinance (fallback)

Provider selection: `us_provider_router.run_healthcheck()` picks the first healthy client in order: **Polygon** (default), Alpaca, yfinance. Prefetch and collection use that client.

| Path | Method | Notes |
|------|--------|-------|
| **Prefetch (Polygon)** | `PolygonClient.get_ohlcv_many(symbols, timeframe, start_utc, end_utc)` → Polygon.io `/v2/aggs/ticker/{ticker}/range/...`. | `us_intraday_cache.prefetch(us_client=...)` when `us_client` is Polygon. `end_utc` ≤ now. |
| **Prefetch (yfinance fallback)** | `yf.download(tickers=batch_symbols, interval=timeframe, period="2d", prepost=False, group_by="ticker", threads=False, timeout=30)`. | When `us_client` is None. Batch size 5. On empty: per-symbol retry. |
| **Direct / fallback** | `YFinanceClient.get_ohlcv(symbol, timeframe, start_utc, end_utc)` or client’s `get_ohlcv` with 5‑min buffer. | Used when cache misses; SPX→SPY for intraday. |

**Interval map**: `1m`→`1m`, `5m`→`5m`, `15m`→`15m`, `1h`→`1h`, `1d`→`1d` (in `polygon_client`, `yfinance_client`).

**SPX**: for intraday (`1m`, `5m`, `15m`), use **SPY** as proxy; prices scaled by 10×.

**Retries**: 3 attempts for `get_ohlcv`; exponential backoff on empty / `MarketDataUnavailable` / JSON-decode–type errors.

### 3.2 Crypto — Coinbase Exchange API

| Path | Method | Notes |
|------|--------|-------|
| **Candles** | `GET https://api.exchange.coinbase.com/products/{product_id}/candles?start=...&end=...&granularity=...` | `coinbase_client._fetch_ohlcv_async` (and `_fetch_candles_windowed`). |
| **Chunking** | `_fetch_candles_windowed`: window ≤ 300 candles; 1‑candle overlap at chunk boundaries; dedupe by timestamp. | Respects `MAX_CANDLES_PER_REQUEST=300`. |
| **Slab fetch** | One `get_ohlcv(symbol, timeframe, slab_start_utc, snapshot_time_utc)` per symbol per snapshot. | `crypto_indicator_slab_bars` (default 180) at 5m → 15h; 1 API call when slab ≤ 300. |

**Coinbase candle format**: `[time, low, high, open, close, volume]` → we store `timestamp, open, high, low, close, volume`.

**Granularity map**: `1m`→60, `5m`→300, `15m`→900, `1h`→3600, `4h`→14400, `1d`→86400 (seconds).

**Rate limiting**: **100 ms** (`delay=0.1`) between crypto symbols in a run. Tenacity retries on 429/5xx.

---

## 4. Data Sources

| Market | Primary source | Fallback / notes |
|--------|----------------|------------------|
| **US** | **Polygon** (Polygon.io; `POLYGON_API_KEY` from `secretsprivate/.env` or Secret Manager `polygon-api-key:latest`) | **Alpaca**, then **yfinance**. `us_data_provider=polygon`, `us_data_provider_fallbacks=alpaca,yfinance`. E*TRADE not used. |
| **Crypto** | **Coinbase Exchange** public API (`api.exchange.coinbase.com`) | No auth for candles/ticker. No alternate provider in code. |

---

## 5. Quick Validation Checklist

Use this to confirm the data design matches implementation:

- [ ] **US symbol count**: 111 from `0dte_list.csv` (or `us_symbols` if CSV missing).
- [ ] **US prefetch**: When Polygon: `PolygonClient.get_ohlcv_many`; when yfinance: `yf.download(..., period="2d", interval="5m")`, batch 5, `threads=False`.
- [ ] **US direct**: Client `get_ohlcv(start, end, interval)` with 5‑min buffer; SPX→SPY for intraday.
- [ ] **US windows**: start=9:30 ET; end=9:45 (ORB), 10:30 (SIGNAL), 15:55 or early_close−5m (OUTCOME).
- [ ] **Indicator slab (US)**: 120 bars from cache (no extra request when cached).
- [ ] **Crypto symbols**: 4 (BTC, ETH, SOL, XRP) mapped to `*-USD`.
- [ ] **Crypto slab**: `crypto_indicator_slab_bars` (default 180) candles, 15h at 5m; `slab_start_utc = snapshot_time_utc - slab_bars * 300` seconds.
- [ ] **Coinbase**: `/products/{id}/candles`, `granularity=300`, chunks of up to 300; 100 ms between symbols.
- [ ] **Crypto windows**: start=session open; end=orb_end, signal_dt, or outcome_dt by type.

---

## 6. Known Failure Modes and Relevant Levers

| Symptom | Likely cause | Levers in this design |
|---------|--------------|------------------------|
| **Polygon 403 / no key** | `POLYGON_API_KEY` missing or invalid. | Set in `secretsprivate/.env` (local) or Secret Manager `polygon-api-key` with `--set-secrets` on Cloud Run. `GET /debug/us/provider_smoke` to confirm. |
| **yfinance empty / "Expecting value" (JSON)** | Rate limiting or bad response from Yahoo (fallback path). | Use Polygon as primary. If yfinance: batch 5, `threads=False`, per-symbol fallback, retries. |
| **All US prefetch batches empty** | Polygon down and yfinance blocked; or wrong params. | Check Polygon health, `us_data_provider` and fallbacks; `period="2d"`, `interval="5m"` for yfinance path; per-symbol fallback in `us_intraday_cache`. |
| **Crypto `VolumeVWAPData` / `volume_delta`** | Type mismatch (float vs int) or NaN. | Coerce `volume_delta` to `int` and guard NaN: `int(round(vd)) if (vd is not None and vd == vd) else None` in `_build_crypto_snapshot` and `_build_us_snapshot`. |
| **Coinbase 429** | Rate limit. | 100 ms between symbols; tenacity retries; `Retry-After` honored when present. |
| **0 snapshots in Firestore** | Upstream: prefetch/direct US or crypto slab/window failing, or validation failing before write. | Confirm `GET /debug/us/provider_smoke` and `GET /debug/crypto/product_smoke`; Polygon key and health; crypto 300‑candle slab; Firestore + `dry_run` and run logs. |
| **Coinbase product_smoke OK but crypto snapshots fail** | Validation in snapshot build (e.g. `volume_delta`). | `./scripts/test_crypto_snapshot.sh [BASE_URL]`; ensure `volume_delta` coercion and NaN guard in `_build_crypto_snapshot`; check Cloud Run logs for Pydantic/`VolumeVWAPData` errors. |

---

## 7. File and Config References

| What | Where |
|------|-------|
| Timeframe, lookback, crypto slab | `backend/app/config.py`: `timeframe="5m"`, `indicator_lookback_bars=120`, `crypto_indicator_slab_bars=180` |
| US provider, Polygon key | `config.py`: `us_data_provider="polygon"`, `us_data_provider_fallbacks`, `polygon_api_key` (env `POLYGON_API_KEY`) |
| US symbol list | `config.load_0dte_symbols()`; CSV: `data/watchlist/0dte_list.csv` or `{project_root}/data/watchlist/0dte_list.csv` |
| Crypto symbols | `config.crypto_symbols`; Coinbase map: `coinbase_client` / `resolve_product_id` |
| US provider router | `clients/us_provider_router.run_healthcheck` (Polygon→Alpaca→yfinance) |
| US prefetch | `storage/us_intraday_cache.USIntradayCache.prefetch(us_client=...)` |
| US clients | `clients/polygon_client.PolygonClient`, `clients/yfinance_client.YFinanceClient` |
| US windows | `services/snapshot_service.SnapshotService._get_us_ohlcv_window` |
| Crypto slab + window | `services/snapshot_service.SnapshotService._run_crypto_snapshots` (slab from `crypto_indicator_slab_bars` + `_get_crypto_ohlcv_window`) |
| Crypto client | `clients/coinbase_client.CoinbaseClient` (`get_ohlcv`, `_fetch_candles_windowed`, `_fetch_ohlcv_async`) |
| Session bounds | `snapshot_service._get_session_bounds_us`, `_get_session_bounds_crypto`; `time_utils`: `get_us_orb_time`, `get_us_signal_time`, `get_us_outcome_time`, `get_crypto_orb_time`, `get_crypto_signal_time`, `get_crypto_outcome_time` |

---

## 8. Related documentation

- **Data sources (Polygon / Coinbase)**: `docs/DATA_SOURCES_TROUBLESHOOTING.md` — `ensure_data_sources_ready.sh`, Secret Manager, IAM, redeploy.
- **Deploy checklist**: `docs/Sessions/Jan 23 Session/DEPLOYMENT_READINESS.md` (secrets, 0dte_list, smoke checks, `./deploy-collector.sh`).
- **Jan 23 status / 0‑snapshot troubleshooting**: `docs/Sessions/Jan 23 Session/COLLECTOR_STATUS_REPORT.md` (yfinance failures, mitigations: Polygon as primary, `volume_delta` fix).
