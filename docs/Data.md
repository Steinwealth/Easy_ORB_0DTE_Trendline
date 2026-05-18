# Data Management System
## Easy ORB Strategy - ORB Data Architecture

**Last Updated**: May 14, 2026  
**Version**: Rev 00350+ baseline; **May 14, 2026** aligns **ORB SO** docs with **Rev 00348** (`calculate_so_priority_score` in `modules/prime_trading_system.py`): **continuation-first** base blend (**`SO_CONTINUATION_MOMENTUM_WEIGHT`** in **`configs/ORBSO.env`**, default **0.32**), plateaued VWAP continuation subscore, **`quality_multiplier`**, soft penalties, **`SO_RANK_BREAKDOWN`**, and telemetry-only **`SO_CONTINUATION_VS_EXTENSION_BIAS`**. **ORB 0DTE** priority ranking docs updated for **Rev 00348** defaults in **`easy0DTE/modules/prime_0dte_strategy_manager.py`** (env **`0DTE_PRIORITY_RANK_W_*`**, **`early_momentum`** tie-break). Cloud grep: **`ORB_0DTE_SELECTOR_FULL_REPLAY`**. **May 6, 2026:** Trendline execution snapshot enrichment includes previous-day context fields (`prev_day_high`, `prev_day_low`, `price_vs_prev_day_*`, `prev_day_entry_gate_passed`) alongside prior live-chain readiness and expiry-fallback behavior. **May 5, 2026:** watchlist docs — **`core_list.csv`** is the ORB SO / shared-capture equity+ETF universe (cash **SPX** is **not** on this list; **SPX** remains on **`0dte_list.csv`** for 0DTE options). **`0dte_list.csv`** priority order follows CSV rows (**SPX** first when listed, then **`core_list.csv`** alignment for overlapping names); **`tier`** labels Red Day and tooling but does not imply the loader re-sorts symbols by tier. **May 4, 2026:** primary path docs state a **7s** baseline for ORB 0DTE / Trendline **open-position** option fast monitors (see `0DTEORB.md`, `0DTETrendline.md`); unrelated batch timings (e.g. SO prefetch **2–5s**) are unchanged. **Same-day doc pass:** Integration section — **`configs/Risk.env`** and **`configs/Data.env`** replace references to removed **`position-sizing.env`**, **`risk-management.env`**, and **`broker-config.env`** files (content merged per `config_loader.py`).

**Purpose**: Comprehensive documentation of the data management system for the Easy ORB Strategy (ORB ETF + ORB 0DTE + Trendline 0DTE). The system uses dynamic symbol lists with shared ORB capture and path-specific execution datasets. Cloud deployment is optimized for cost efficiency with scale-to-zero deployment. **All data comes from configured broker (E*TRADE default) - no third-party data sources.**

**Current Focus**: shared ORB capture + three concurrent strategy paths (ORR disabled - 0% allocation)  
**Status**: ✅ Production Ready - ETrade API Batch Limit Fix (Rev 00247), 0DTE Priority Formula v1.1 (Rev 00246), Direction-Aware Red Day (Rev 00246), Expanded Delta Selection (Rev 00246), Comprehensive Logging (Rev 00246), Broker Configuration System (Rev 00245), Enhanced Red Day Detection (Rev 00237), Broker-Only Data Source (Rev 00236), Configurable Broker Support (Rev 00236), Data Quality Fixes (Rev 00233), Signal-Level Red Day Detection (Rev 00233), Enhanced Data Validation (Rev 00233), Trade Persistence Fix (Rev 00203), Unified Configuration (Rev 00201-00202), Exit Settings Optimized (Rev 00196), Trade ID Shortening (Rev 00232)

---

## 📋 **Table of Contents**

1. [Data Architecture Overview](#data-architecture-overview)
2. [Prime Data Manager](#prime-data-manager)
3. [Data Sources & Providers](#data-sources--providers)
4. [Watchlist & Symbol Management](#watchlist--symbol-management)
5. [Real-Time Data Processing](#real-time-data-processing)
6. [ORB Data Capture](#orb-data-capture)
7. [Data Quality & Validation](#data-quality--validation)
8. [Performance Optimization](#performance-optimization)
9. [API Usage & Cost Analysis](#api-usage--cost-analysis)
10. [Data Storage & Persistence](#data-storage--persistence)
11. [Integration Guide](#integration-guide)

---

## ✅ **DEPLOYMENT STATUS (Rev 00237 - January 9, 2026)**

**Easy ORB Strategy Deployed & Operational - Three Strategy Paths:**
- ✅ **Core List**: Dynamic (current row count in `core_list.csv`; fully scalable without code changes - Rev 00058)
- ✅ **ORB Capture**: 6:30-6:45 AM PT window with dynamic batch processing
- ✅ **Validation open 7:00**: Cloud Scheduler job `validation-candle-700` captures 7:00 open (batches of 25, same as ORB); persisted to GCS for 7:15 prefetch when scale-to-zero.
- ✅ **SO Prefetch 7:15**: 7:00-7:15 AM candle via E*TRADE batch quotes (25/call, same as ORB); 7:15 close with skip_cache=True; optional scheduler job `prefetch-validation-715` for scale-to-zero.
- ✅ **SO Scanning**: 7:15-7:30 AM PT (continuous scanning every 30 sec - 15-minute window)
- ✅ **SO Execution**: 7:30 AM PT batch execution with **continuation-first** priority ranking (**Rev 00348** — `SO_CONTINUATION_MOMENTUM_WEIGHT` + plateau VWAP + context blend + `SO_RANK_BREAKDOWN`; historical v2.1 fixed-weight narrative superseded for runtime behavior)
- ✅ **Cloud Scheduler Keep-Alive**: 3 jobs ensure instance stays alive during trading hours ⭐ **CRITICAL**
- ✅ **ORB Capture Alert**: Sent at 6:45 AM PT (handles success and failure cases)
- ✅ **Trade Signal Collection Alert**: Sent at 7:30 AM PT (shows "6-15 signals")
- ✅ **Duplicate Prevention**: Same symbol can't execute twice per day
- ✅ **Prime Data Manager**: Batch quotes (25/call) for efficient data fetching - **BROKER-ONLY** (Rev 00236)
- ✅ **Prime Risk Manager**: Demo & Live modes with rank-based position sizing (Rev 00090)
- ✅ **Prime ORB Strategy Manager**: SO signal generation with validation rules
- ✅ **Prime Stealth Trailing**: Optimized trailing stops (Rev 00196: 0.7% @ 6.4 min, 1.5-2.5% distance)
- ✅ **Centralized Alerts**: All alerts in prime_alert_manager.py (single source of truth)
- ✅ **Mock Trading Executor**: Demo mode with EOD tracking
- ✅ **E*TRADE Integration**: Live mode ready (default broker)
- ✅ **Configurable Broker Support**: E*TRADE (default), Interactive Brokers, Robinhood (Rev 00236)
- ✅ **Broker-Only Data**: All data from configured broker - no third-party fallback (Rev 00236)
- ✅ **Multi-Factor Ranking**: **ORB SO** — **Rev 00348** continuation-first base (`SO_CONTINUATION_MOMENTUM_WEIGHT` + remainder blend; see **Cloud log grep**); **ORB 0DTE** — **Rev 00348** env weights `0DTE_PRIORITY_RANK_W_*` (defaults **0.14 / 0.10 / 0.22 / 0.20 / 0.34**). Legacy v2.1 fixed-weight narrative is research lineage only.
- ✅ **Unified configuration**: Seven canonical `configs/*.env` files merged by `config_loader.py` (see `configs/README.md`)
- ✅ **Trade Persistence**: GCS persistence working (Rev 00203)
- ✅ **Trade ID Formatting**: Shortened format (Rev 00232)
- ✅ **Data Quality System**: Enhanced validation with neutral defaults (Rev 00233)
- ✅ **Signal-Level Filtering**: Individual trade Red Day detection (Rev 00233)
- ✅ **Filter Consistency**: ORB and 0DTE filters aligned (Rev 00233)
- ✅ **Enhanced Red Day Detection**: Real SPY momentum and VIX level from E*TRADE (Rev 00237)

**Disabled/Archived Components:**
- ⏸️ **ORR Trades**: Disabled (0% capital allocation) - Will optimize separately
- ❌ **Dynamic Watchlist Builder**: PAUSED - Dynamic core_list.csv used (Rev 00058)
- ❌ **Symbol Selector**: ARCHIVED - All symbols from core_list.csv used
- ❌ **Multi-Strategy Manager**: ARCHIVED - ORB only
- ❌ **Historical Data Caching**: Not needed for ORB
- ❌ **Compound Engine**: Not needed (ORR disabled)

---

## 🏗️ **Data Architecture Overview**

The Easy ORB Strategy implements a **prime data management system** optimized for 24/7 operation with advanced caching, and broker-only data support. The system ensures consistent performance across symbol scanning, trading operations, and position monitoring.

### **Key Principles**
- **Shared capture, split execution**: one ORB capture feeds three strategy paths
- **Efficiency**: Batch processing and intelligent caching
- **Reliability**: Broker-only path with circuit breaker protection
- **Scalability**: Dynamic symbol list (add/remove without code changes)
- **Performance**: Optimized for low latency and high throughput

---

## 🚀 Prime Data Manager

### **System Consolidation**
- **Single Data Manager**: All data operations consolidated into `prime_data_manager.py`
- **Broker-Only Support**: E*TRADE (default) - all data from configured broker (Rev 00236)
- **No Third-Party Fallback**: System stops if broker fails (no silent fallback) (Rev 00236)
- **Advanced Caching**: Multi-tier caching with TTL-based cleanup
- **Data Quality Assessment**: Quality scoring and validation
- **Async Data Processor**: 70% faster data processing with connection pooling
- **Unified Models Integration**: PrimeSignal, PrimePosition, PrimeTrade data structures throughout
- **Real Market Data**: SPY momentum and VIX level retrieved from E*TRADE (Rev 00237)

### **Current Integration Status**
- **Data Manager**: ✅ **IMPLEMENTED** - `prime_data_manager.py` exists and ready
- **Main System Integration**: ✅ **ACTIVE AND FUNCTIONAL** - Trading thread operational
- **Scanner Integration**: ✅ **FULLY INTEGRATED** - Components connected and operational
- **ORB Strategy Integration**: ✅ **FULLY INTEGRATED** - ORB capture and SO signal generation operational

---

## 🏗️ Data Architecture

### **Data Organization Structure**
The system uses a structured approach to data management with specialized directories:

```
data/
├── 📋 watchlist/                    # Symbol Management
│   ├── core_list.csv                 # Core ORB symbols (dynamic row count) organized by leverage
│   └── 0dte_list.csv                # 0DTE symbols (if 0DTE strategy enabled)
├── 📊 score/                        # Performance Tracking
│   ├── symbol_scores.json           # Prime score data
│   └── symbol_scores_backup.json    # Backup data
└── ⚙️ System Files
    ├── holidays_custom.json         # Custom holiday calendar
    ├── state.json                   # System state file
    └── secret_manager_example.py    # Google Secret Manager integration
```

---

## 📋 Watchlist & Symbol Management

### **Core List (`core_list.csv`)** ⭐ **PRIMARY - Rev 00058**

**Current Status**:
- **Dynamic row count**; each row has **`leverage`** (1×–4×), **`category`**, and **`underlying`** metadata — file order is maintained for ORB SO / capture priority (not strictly “all 4× then all 3×”).
- **SPX**: **not** on `core_list.csv` (non-tradable cash index for SO); **SPX** stays on **`0dte_list.csv`** for 0DTE / ORB context where configured.
- **ORB Data Usage**: ORB high/low passed to stealth trailing for entry bar protection (Rev 00135)
- **Batch Sizing**: Rev 00089 - quantity_override ensures batch-sized quantities used exactly
- **Leverage column**: encodes 1×–4× product type per row (ETFs, single-stock leverage, indices) for risk and display
- **Spot equities (dual-listed)**: A small set of **`1x` equity** rows may appear when a name should trade **both** ORB SO and 0DTE (e.g. **HIMS**, **CRWD**). Symbols **only** on `0dte_list.csv` do **not** receive ORB SO execution until added here.
- **NVIDIA policy**: Underlying **NVDA** remains part of the 0DTE universe for options flow; ORB SO exposure follows the actively maintained `core_list.csv` universe.
- **Categories** (see CSV): `index`, `commodity`, `crypto`, `sector`, `equity`, `volatility`, etc. — maintained in-file, not implied by a single global sort key
- **Pre-Filtered**: Volatility (ATR), volume (5M+ daily), performance validated (historical selection criteria)
- **Production Ready**: Used for all ORB capture and SO trades
- **Fully Scalable**: Add/remove symbols without code changes (Rev 00058) ⭐ **KEY FEATURE**

### **0DTE Symbol List (`0dte_list.csv`)** ⭐ **0DTE STRATEGY - Rev 00209 + Rev 00327**

**Current Status**:
- **Dynamic symbol count**; each row has **`tier`** (1 = core daily proxies / indices; 2 = names) for **policy** (e.g. Red Day CALL rules), not for reordering symbols when the CSV is loaded
- **ORB Data Integration**: All 0DTE symbols included in ORB capture (Rev 00209)
- **Watchlist policy (Rev 00327)**: Single-stock **2×** daily ETFs are **not** primary 0DTE underlyings for options flow. **Remove** a 2× ticker **only** when the **underlying equity** is already on the list; if the underlying was missing, **add the underlying** (e.g. **LRCX, FLY, CRDO, APP**) before dropping the 2× product. Broad index/sector leverage funds are managed separately from this rule in `core_list.csv` (ORB SO path).
- **Tier Organization**:
  - **Tier 1** (dynamic count): Core Daily 0DTE index/ETF/commodity proxy set (current constituents are maintained directly in `0dte_list.csv`)
  - **Tier 2** (dynamic): Equities and sector ETFs (mega-cap, thematic, high-beta, miners, homebuilders, etc.) — see [easy0DTE/docs/Data.md](../easy0DTE/docs/Data.md) for a category breakdown
- **Production Ready**: Used for 0DTE options signal generation and execution
- **Fully Scalable**: Add/remove symbols without code changes
- **List order**: CSV **row order** is the processing priority (aligned with **`core_list.csv`** for overlapping symbols, **SPX** first on 0DTE when present); **`tier`** does not trigger a separate sort in `load_0dte_symbols` / collector loaders
- **Red Day policy (current)**: ORB longs are blocked; 0DTE LONG/CALL is blocked for non-Tier-1 symbols while Tier-1 LONG/CALL and SHORT/PUT paths remain eligible

**ORB Data Collection Integration** (Rev 00209):
- 0DTE symbols loaded from `data/watchlist/0dte_list.csv` during ORB capture
- Merged with ORB symbols (no duplicates - symbols already in ORB list are not duplicated)
- All symbols (ORB + 0DTE) captured in single batch operation (6:30-6:45 AM PT)
- ORB data used for 0DTE signal generation and eligibility filtering

**Example**:
- ORB list: dynamic row count from `core_list.csv`
- 0DTE list: dynamic row count from `0dte_list.csv`
- Combined: dynamic unique ORB-capture universe (union of both lists; duplicates are not double-fetched)
- ORB capture: All symbols processed in batch (typically a few seconds; batch size 25 per call)

**Sentiment / inverse mappings (Rev 00326):** `data/watchlist/complete_sentiment_mapping.json`, `sentiment_pairs_mapping.json`, and `orb_inverse_mapping.json` carry pair wiring; Ethereum bullish ETF is **ETHU** with inverse **ETHD** (legacy **ETHT** removed).

### **Cloud log grep — 0DTE path (Rev 00326)**

Useful tokens when tracing why a name dropped after signal collection: `0DTE_PIPELINE`, `0DTE_TARGET_FILTER`, `0DTE_DEDUPE`, `0DTE_PRIORITY_DROP`, `0DTE_HARD_GATE_REJECT`, `0DTE_EXEC_REJECT`, `0DTE_EXEC_STAGE`, `CONVEX_REJECT_DETAIL`, `0DTE_CONVEX_STAGE`, `0DTE_SELECTOR_DIAG`, `ITM_SPREAD_SELECTOR`, **`ORB_0DTE_SELECTOR_FULL_REPLAY`**, **`SO_PIPELINE`**, **`SO_RANK_BREAKDOWN`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`** (ORB SO), `CONVEX_FILTER`.

**ORB SO priority (Rev 00348 — runtime `calculate_so_priority_score`):**

- **`w_cont = SO_CONTINUATION_MOMENTUM_WEIGHT`** (clamped **0.18–0.42**, default **0.32**) × **`continuation_quality_score`** (momentum slope/accel + graded **MACD**).
- **`w_rem = 1 - w_cont`** × weighted sum of **`vwap_score`** (continuation-shaped), **`rs_score`**, **`orb_vol_score`**, **`conf_score`**, **`rsi_score`**, **`orb_range_score`** with fixed internal shares **0.24 / 0.18 / 0.28 / 0.10 / 0.14 / 0.06** within the remainder (see `modules/prime_trading_system.py`).
- **`base_priority_score`** then **`× quality_multiplier`** minus soft penalties (extension / exhaustion / deceleration / ORB-range soft).
- Telemetry: **`SO_RANK_BREAKDOWN`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`** (does not change rank).

**Historical v2.1 fixed-weight formula (research lineage — not current SO runtime):**
```python
# Legacy research snapshot (Nov 2025) — NOT the Rev 00348 SO runtime formula.
priority_score_legacy_v21 = (
    vwap_distance_score * 0.27
    + rs_vs_spy_score * 0.25
    + orb_vol_score * 0.22
    + confidence_score * 0.13
    + rsi_score * 0.10
    + orb_range_score * 0.02
)
```

**Data-driven SO layers (Rev 00347 + Rev 00348):**
- **Rev 00348 base score**: continuation-quality share **`SO_CONTINUATION_MOMENTUM_WEIGHT`** (default **0.32**, clamped **0.18–0.42**) plus remainder-weighted VWAP continuation / RS / ORB vol / confidence / RSI / ORB range (internal shares **0.24 / 0.18 / 0.28 / 0.10 / 0.14 / 0.06** within the remainder); then **`× quality_multiplier`** and soft penalties; logs **`SO_RANK_BREAKDOWN`** / **`SO_CONTINUATION_VS_EXTENSION_BIAS`**.
- **Rev 00347 quality multiplier** still boosts/penalizes winner-like vs weak participation profiles on top of that base.
- Ranked SO rows persist **`priority_base_score`**, **`priority_quality_multiplier`**, **`priority_score`** (final).
- Pre-execution **winner-profile quality gate** (`SO_WINNER_*` keys) runs before adaptive sizing.

**Evidence Base**:
- 89-field technical indicators tracked daily
- Trendline execution snapshot enriched with previous-day context (`prev_day_high`, `prev_day_low`, `price_vs_prev_day_high`, `price_vs_prev_day_low`, `price_vs_prev_day_range`, `prev_day_entry_gate_passed`)
- 3-day comprehensive data collection (Nov 4, 5, 6, 2025)
- Correlation analysis validated **historical v2.1** fixed-weight tuning (SO research lineage)
- Expected +10-15% better capital allocation vs v2.0 **under that research framing**; **Rev 00348** continuation-first base is the current SO runtime (see **ORB SO priority** above)

---

## 📊 Real-Time Data Processing

### **ORB Data Capture** ⭐ **CRITICAL**

**Timing**: 6:30-6:45 AM PT (9:30-9:45 AM ET) - First 15 minutes of trading

**Process**:
1. Market opens at 6:30 AM PT
2. System captures opening range for all symbols (dynamic merged ORB + 0DTE universe)
3. Batch processing: Dynamic batches based on symbol count (2-5 seconds total)
4. ORB data stored: High, Low, Open, Close, Volume, Range %
5. Data source: **E*TRADE batch quotes ONLY** (today's OHLC = ORB) - Rev 00236
6. **No Fallback**: System stops if broker fails (no third-party backup)

**Data Structure**:
```python
orb_data = {
    'symbol': 'QQQ',
    'orb_high': 385.50,
    'orb_low': 382.30,
    'orb_open': 383.10,
    'orb_close': 384.20,
    'orb_volume': 1250000,
    'orb_range_pct': 0.84,  # (high - low) / low * 100
    'timestamp': '2026-01-06T06:45:00-08:00'
}
```

**Uses for ORB Data**:
- **ORB Strategy**: Breakout detection (price > ORB high), entry bar protection, stop loss calculation, multi-factor ranking
- **0DTE Strategy**: ORB data used for Convex Eligibility Filter (ORB range ≥ 0.35%, ORB break confirmation), signal generation, and strategy selection
- **Trendline 0DTE Strategy**: ORB context + pre/post-7:30 intraday bars support trendline candidate build and event-driven break/hold/structure/momentum confirmation

### **Three-Path Data Routing**

After shared ORB capture and signal collection windows:

1. **ORB ETF path** uses SO confirmed symbols (`core_list` derived flow) for 7:30 execution and ETF monitoring/exits.
2. **ORB 0DTE path** uses 0DTE Signal Collection -> Convex -> Hard Gate queue for 7:30 options execution.
3. **Trendline 0DTE path** builds from the full 0DTE universe at 7:30, then continues intraday bar-driven monitoring for event-based execution.

The data pipeline stays shared; execution lifecycles and ledgers remain strategy-isolated.

---

### **SO Signal Collection** ⭐ **PRIMARY**

**Timing**: 7:15-7:30 AM PT (10:15-10:30 AM ET) - 15-minute collection window

**Process**:
1. **Prefetch** (7:15 AM PT): Fetch 7:00-7:15 AM candle for validation. **Full ORB + 0DTE symbol list** (no cap). Uses **two broker snapshots**: 7:00 open (from Cloud Scheduler job or GCS `daily_markers/validation_open_700/YYYY-MM-DD.json`) and 7:15 close (batch quotes). Same broker API as ORB (one snapshot at 6:45 for ORB; two snapshots 7:00 + 7:15 for validation).
2. **Scanning** (7:15-7:30 AM PT): Continuous validation every 30 seconds
3. **Validation**: 3 strict rules (price, volume color, previous candle)
4. **Collection**: 6-15 qualified signals from all symbols
5. **Ranking**: Multi-factor priority scoring (**Rev 00348** continuation-first SO; see **ORB SO priority** above)
6. **Selection**: Top 15 affordable signals pre-selected

**SO Validation Rules** (Bullish - All 3 Required):
1. **Current price ≥ ORB high × 1.001** (+0.1% buffer)
2. **Previous close > ORB high** (7:00-7:15 AM candle closed above range)
3. **Green candle** (7:15 AM close > 7:00 AM open = buying pressure)

**Data Collection**:
- Real-time price quotes (**E*TRADE batch quotes ONLY** - Rev 00236)
- Technical indicators (VWAP, RS vs SPY, RSI, MACD)
- Volume analysis
- Momentum indicators
- **All data from configured broker** - no third-party sources

**ORB vs validation candle (same broker API)**:
- **ORB candle** (first 15 min): **One** `get_batch_quotes()` at **6:45 AM PT**; quote’s today’s OHLC = first 15‑min bar.
- **Validation candle** (7:00–7:15): **Two** snapshots — 7:00 open (stored in memory or GCS `daily_markers/validation_open_700/YYYY-MM-DD.json`) and 7:15 close via `get_batch_quotes()`. Broker does not return “the 7:00–7:15 bar” in one call. **Definition:** Open = price at 7:00 AM PT only; close = price at 7:15 AM PT only. No proxy (e.g. market open) is used—using any other open would produce false Long signals.

---

## 🎯 Data Sources & Providers

### **Broker-Only Data Source** ⭐ **Rev 00236**

**Rev 00236 (Jan 9, 2026): All data MUST come from configured broker - no third-party data sources**

**Supported Brokers**:
- ✅ **E*TRADE** (default) - Fully implemented
- 🔄 **Interactive Brokers** - Placeholder (ready for implementation)
- 🔄 **Robinhood** - Placeholder (ready for implementation)

**Configuration**:
- `BROKER_TYPE=etrade` (default — set in **`configs/Data.env`**)
- `BROKER_DATA_ONLY=true` (required - no third-party fallback)
- **Primary file for broker keys**: **`configs/Data.env`** (Rev 00245 lineage)
- **Broker Config Manager**: `modules/broker_config_manager.py` (Rev 00245)

**Key Principles**:
1. **Broker Data Only**: All data comes directly from the configured broker
2. **No Third-Party Sources**: No yfinance, Alpha Vantage, or other third-party data
3. **Configurable**: Broker selection via `BROKER_TYPE` configuration
4. **Error Handling**: System stops trading if broker fails (no silent fallback)

---

### **Primary Provider: E*TRADE API** ⭐ **DEFAULT**

**Usage**:
- ORB capture (batch quotes - 25 symbols per call)
- Real-time price quotes
- SO prefetch (current price + today's OHLC)
- SO scanning (current prices)
- Account information
- Order execution (Live mode)

**Optimization**:
- Batch requests: Group multiple symbols in single API call (25 symbols per call) ⭐ **Rev 00247: Enforced 25 symbol limit**
- **Batch Limit Enforcement** (Rev 00247): Automatically splits large symbol lists into batches of 25 to prevent API error 1023
- Smart caching: Cache quotes for 1 second to reduce redundant calls
- Rate limiting: 100ms between calls to avoid throttling
- Connection reuse: Maintain persistent connections
- **Speed**: ~2-6 seconds at current merged-universe scale (vs 131.6s with third-party fallback)
- **Error Handling**: Graceful batch processing - continues with next batch if one fails

**Cost**: Included with E*TRADE account (no additional fees)

**Data Collection**:
- ✅ **ORB Capture**: Uses E*TRADE batch quotes (bars=1)
- ✅ **SO Prefetch**: Uses E*TRADE batch quotes (current price + today's OHLC)
- ✅ **SO Scanning**: Uses E*TRADE batch quotes (current prices)
- ✅ **All Data Collection**: E*TRADE batch quotes exclusively

---

### **Third-Party Providers: REMOVED** ⚠️ **Rev 00236**

**Rev 00236**: All third-party data sources removed:
- ❌ **yfinance**: Removed from all data collection paths
- ❌ **Alpha Vantage**: Removed from all data collection paths
- ❌ **Polygon**: Removed from all data collection paths
- ❌ **Emergency Fallback**: Disabled (system stops if broker fails)

**Rationale**:
- E*TRADE is the broker - must use broker data for accuracy and speed
- Broker data is authoritative and reliable
- No third-party dependencies or throttling issues
- Faster data collection (2-5 seconds vs 131.6 seconds)

---

### **Enhanced Red Day Detection Market Data** ⭐ **Rev 00237**

**Real-Time Market Data for Risk Assessment:**

**SPY Momentum Calculation**:
- **Source**: E*TRADE quote API (`get_quotes(['SPY'])`)
- **Method**: Uses `change_pct` from quote, with fallback to open vs previous close
- **Fallback Calculation**: Historical data (2 days) to calculate momentum if `change_pct` unavailable
- **Usage**: Enhanced Red Day Detection risk assessment
- **Replaces**: Hardcoded value (0.0%)

**VIX Level Retrieval**:
- **Source**: E*TRADE quote API (tries both `$VIX` and `VIX` symbols)
- **Method**: Uses `last_price` or `price` from quote
- **Fallback**: Defaults to 15.0 if unavailable
- **Usage**: Enhanced Red Day Detection volatility assessment
- **Replaces**: Hardcoded value (15.0)

**Benefits**:
- ✅ More accurate risk assessment using real market conditions
- ✅ Better Red Day detection with actual SPY momentum and VIX volatility
- ✅ Improved capital preservation through better risk analysis
- ✅ Real-time market data integration

**Code Location**:
- `modules/prime_trading_system.py` (Red Day / live-quote integration for SPY and VIX)
- Enhanced Red Day Detection section

---

## 📊 Data Quality & Validation

### **Quality Checks**

**ORB Data Validation**:
- ✅ All symbols captured (dynamic count)
- ✅ Valid price data (high > low, high > open, low < open)
- ✅ Volume > 0
- ✅ Range % calculated correctly
- ✅ Timestamp within 6:30-6:45 AM PT window

**SO Signal Validation**:
- ✅ Price above ORB high (with buffer)
- ✅ Previous candle closed above ORB high
- ✅ Green candle (buying pressure)
- ✅ Technical indicators available
- ✅ No duplicate symbols per day

**Data Quality Scoring**:
- **High Quality**: All checks pass, recent data (< 5 seconds old)
- **Medium Quality**: Most checks pass, slightly stale data (< 30 seconds old)
- **Low Quality**: Some checks fail, stale data (> 30 seconds old)

---

## ⚡ Performance Optimization

### **Caching Strategy**

**Multi-Tier Caching**:
- **L1 Cache**: In-memory (1 second TTL for quotes)
- **L2 Cache**: File-based (5 minutes TTL for indicators)
- **L3 Cache**: GCS persistence (daily for trade history)

**Cache Hit Rates**:
- **Quote Cache**: 90%+ hit rate
- **Indicator Cache**: 85%+ hit rate
- **ORB Data Cache**: 100% hit rate (cached for entire trading day)

### **Batch Processing**

**ORB Capture**:
- Batch size: 25 symbols per call
- Processing time: ~2-6 seconds at current merged-universe scale
- Parallel processing: Multiple batches processed concurrently

**SO Signal Collection**:
- Continuous scanning: Every 30 seconds
- Batch validation: All symbols validated together
- Efficient filtering: Only qualified signals processed

### **Performance Metrics**

**Real-World Performance** (Rev 00236 - Broker-Only):
| Operation | Symbol Count | Processing Time | Improvement |
|-----------|-------------|------------------|-------------|
| **ORB Capture** | dynamic merged universe | 2-6 seconds | E*TRADE batch quotes |
| **SO Prefetch** | dynamic merged universe | 2-4 seconds | E*TRADE batch quotes (was 131.6s with yfinance) |
| **SO Scanning** | dynamic merged universe | 1-3 seconds | E*TRADE batch quotes |
| **Signal Ranking** | 6-15 signals | < 100ms | Optimized |
| **Batch Execution** | Up to 15 trades | 2-3 seconds | Optimized |

**Memory Usage**:
- **Baseline**: 400-600MB
- **Peak (during trading)**: 800MB-1.2GB
- **After hours**: 300-500MB

---

## 💰 API Usage & Cost Analysis

### **E*TRADE API Usage** (Rev 00236 - Broker-Only)

**Daily Usage**:
- **ORB Capture**: dynamic batch-call count (`merged_symbol_count ÷ 25`, rounded up)
- **SO Prefetch**: dynamic batch-call count (full merged ORB+0DTE list ÷ 25, rounded up)
- **SO Scanning**: ~30 batch calls (every 30 seconds for 15 minutes)
- **Position monitoring**: **varies by path** — ETF / shared loops are often on **~30s**-class cadences; **open ORB 0DTE / Trendline** contracts use **fast** monitors (default **~7s** baseline, env-driven), which increases quote traffic versus a pure 30s assumption
- **Total**: order-of-magnitude **~1,200+** broker calls/day for baseline loops; **higher** when many options positions run on fast monitors (do not treat earlier single-number estimates as exact)

**Cost**: **$0** (included with E*TRADE account)

### **Third-Party API Usage** (Rev 00236)

**Daily Usage**:
- **yfinance**: **0 calls** (removed - Rev 00236)
- **Alpha Vantage**: **0 calls** (removed - Rev 00236)
- **Polygon**: **0 calls** (removed - Rev 00236)

**Cost**: **$0** (not used)

### **Total Monthly Cost**

**API Costs**: $0 (E*TRADE included - broker data only)  
**Cloud Infrastructure**: ~$11-15/month (Google Cloud Run - scale-to-zero)  
**Secret Manager**: ~$1.20/month (20 billable versions with automatic cleanup)  
**Total**: **~$12-16/month** (93-96% reduction from previous ~$155-355/month)

**Rev 00236 Benefits**:
- ✅ **Faster**: 2-5 seconds vs 131.6 seconds (SO prefetch)
- ✅ **Reliable**: Broker data is authoritative
- ✅ **Consistent**: All data from same source
- ✅ **No Dependencies**: No third-party throttling or errors

---

## 💾 Data Storage & Persistence

### **GCS Persistence** (Rev 00203) ⭐

**Trade History**:
- All closed trades persisted to GCS
- Trade history survives Cloud Run redeployments
- Automatic persistence on trade close

**Account Balance**:
- Demo account balance persists between deployments
- Closed trades update balance correctly (Rev 00145)
- Retry logic prevents balance reset on transient failures (Rev 00146)

**Mock Trading History**:
- Mock trading history persists across redeployments (Rev 00177)
- Trade persistence bug fixed (Rev 00203)

### **Local Storage**

**State Files**:
- `data/state.json`: System state (market hours, last update, etc.)
- `data/holidays_custom.json`: Custom holiday calendar
- `data/watchlist/core_list.csv`: ORB symbol list (dynamic count; 1 CSV header row)

**Score Files**:
- `data/score/symbol_scores.json`: Performance tracking
- `data/score/symbol_scores_backup.json`: Backup data

---

## 🔧 Integration Guide

### **Configuration** (seven-file merge) ⭐

**Unified configuration system**:
- **`configs/Data.env` → `Shared.env` → `ORBSO.env` → `ORB0DTE.env` → `Trendline0DTE.env` → `Risk.env` → `Alerts.env`** (later overrides earlier; see `modules/config_loader.py`)
- **Risk / sizing / stealth / slip guard** tunables live in **`configs/Risk.env`** (merged from former `position-sizing.env` + `risk-management.env` + slip-guard — comment header in file)
- **Broker defaults** (formerly `broker-config.env`) live in **`configs/Data.env`**

**Key path files**:
- `configs/ORBSO.env`: Capital allocation, `MAX_CONCURRENT_TRADES`, SO schedule and quality keys
- `configs/ORB0DTE.env` / `configs/Trendline0DTE.env`: path-scoped 0DTE / Trendline knobs and monitors
- `configs/Shared.env`: `MAX_TOTAL_OPTION_POSITIONS`, live-option hygiene, shared symbols

### **Environment Variables & Secrets Management** (Rev 00233) 🔒

**Local Development**:
- **E*TRADE Credentials**: Store in `secretsprivate/etrade.env` (gitignored)
- **Telegram Credentials**: Store in `secretsprivate/telegram.env` (gitignored)
- **Templates**: Use `secretsprivate/*.env.template` files as reference
- **Loading**: Automatically loaded by `modules/config_loader.py` when `ENVIRONMENT=development`

**Production Deployment**:
- **E*TRADE Credentials**: Store in Google Secret Manager
- **Telegram Credentials**: Store in Google Secret Manager
- **Loading**: Automatically loaded by `modules/config_loader.py` when `ENVIRONMENT=production`

**Configuration Files**:
- **No Hardcoded Secrets**: All sensitive credentials removed from `configs/*.env` files (Rev 00233)
- **Safe to Commit**: Template files (`.env.template`) are safe for version control

**For complete setup instructions, see the Secrets Management section in [docs/Settings.md](Settings.md).**

**Optional Settings**:
```bash
ENABLE_0DTE_STRATEGY=true  # Enable 0DTE options strategy
ETRADE_MODE=demo          # demo or live
```

---

## 🎯 Key Features

### **1. Dynamic Symbol Lists** ⭐ Rev 00058 + Rev 00209 + Rev 00327
- **ORB Symbol List**: dynamic symbols from `core_list.csv` (fully scalable)
- **0DTE Symbol List**: dynamic symbols from `0dte_list.csv` (fully scalable)
- **Organization**: ORB list by maintained **`core_list.csv`** order; 0DTE list by maintained **`0dte_list.csv`** order with **`tier`** as metadata; merged ORB capture universe is the **set union** (dynamic count)
- **Pre-Filtered**: Volatility, volume, performance validated
- **ORB Data Integration**: All 0DTE symbols included in ORB capture (Rev 00209)

### **2. Multi-Factor Ranking** ⭐ Rev 00348 (ORB SO) + Rev 00348 (ORB 0DTE)

- **ORB SO**: continuation-first pipeline — see **Cloud log grep** / **ORB SO priority** above and `configs/ORBSO.env` **`SO_CONTINUATION_MOMENTUM_WEIGHT`**.
- **ORB 0DTE**: env-driven weights **`0DTE_PRIORITY_RANK_W_BREAKOUT`**, **`0DTE_PRIORITY_RANK_W_ORB_RANGE`**, **`0DTE_PRIORITY_RANK_W_VOLUME`**, **`0DTE_PRIORITY_RANK_W_CONVEX`**, **`0DTE_PRIORITY_RANK_W_EARLY_MOMENTUM`** (defaults **0.14 / 0.10 / 0.22 / 0.20 / 0.34**) — see `modules/orb0dte_execution_defaults.py` / profile bundles.

### **3. Entry Bar Protection** ⭐ Rev 00135
- **Permanent Floor Stops**: Based on actual ORB volatility
- **Tiered Stops**: 2-8% based on volatility
- **Prevents**: 64% of immediate stop-outs
- **Real-World Validation**: Saved reversal trade example (+$7.84 profit)

### **4. Trade Persistence** ⭐ Rev 00203
- **GCS Persistence**: Trades persist immediately to GCS
- **Survives Deployments**: Trade history persists across redeployments
- **Account Balance**: Demo balance persists correctly

### **5. Unified configuration** ⭐ (`configs/README.md`)
- **Seven merged env files** under `configs/` for operator defaults
- **Single source of truth per key**: documented in `configs/README.md` (avoid duplicating the same key across files)

### **6. Data Quality System** ⭐ Rev 00233 **NEW**
- **Neutral Defaults**: RSI=50.0, Volume=1.0 instead of 0.0
- **Prevents False Positives**: No false Red Day detection from invalid data
- **Enhanced Validation**: Helper functions filter invalid values
- **Better Diagnostics**: Enhanced logging for data quality issues

### **7. Signal-Level Red Day Detection** ⭐ Rev 00233
- **Two-Layer Protection**: Portfolio-level + Signal-level filtering
- **Individual Trade Filtering**: Rejects losing trades even on good days
- **Criteria**: Weak volume + (Oversold RSI OR No momentum OR Negative VWAP)
- **Impact**: Prevents losing trades while allowing winning trades

### **8. Enhanced Red Day Detection with Real Market Data** ⭐ Rev 00237 **NEW**
- **Real SPY Momentum**: Calculated from E*TRADE quotes (replaces hardcoded 0.0%)
- **Real VIX Level**: Retrieved from E*TRADE quotes (replaces hardcoded 15.0)
- **Real-Time Risk Assessment**: Uses actual market conditions for better accuracy
- **Improved Capital Preservation**: More accurate risk analysis prevents losses
- **Data Source**: E*TRADE quote API (SPY and VIX symbols)
- **Fallback**: Graceful defaults if data unavailable (SPY: 0.0%, VIX: 15.0)

---

## 🎉 Bottom Line

The Easy ORB Strategy data management system provides:

✅ **Real-time data** with E*TRADE integration (broker-only)  
✅ **Cost-effective** operation at ~$11/month total  
✅ **Broker-only data** (no third-party fallback - Rev 00236)  
✅ **Configurable broker support** (E*TRADE default, IB/Robinhood ready - Rev 00236)  
✅ **Enhanced Red Day Detection** (real SPY momentum & VIX level - Rev 00237)  
✅ **High performance** with optimized data processing (2-5 seconds vs 131.6s)  
✅ **Professional monitoring** and quality assurance  
✅ **Scalable architecture** for future growth  
✅ **Dynamic symbol list** (add/remove without code changes)  
✅ **Multi-factor ranking** (**Rev 00348** SO + 0DTE; v2.1 fixed weights = research lineage only)  
✅ **Entry bar protection** (permanent floor stops)  
✅ **Trade persistence** (GCS integration)  
✅ **Unified configuration** (seven-file `configs/` merge)  
✅ **Dynamic ORB symbols** + **dynamic `0dte_list.csv` symbols** (dynamic merged capture universe)  
✅ **90%+ cache hit rate** for optimal performance  
✅ **88-90% capital deployment** guaranteed  

**Ready for 24/7 automated trading with institutional-grade data management!** 🚀

---

*For strategy details, see [Strategy.md](Strategy.md)*  
*For process flow, see [ProcessFlow.md](ProcessFlow.md)*  
*For risk management, see [Risk.md](Risk.md)*  
*For alert system, see [Alerts.md](Alerts.md)*  
*For configuration reference, see [Settings.md](Settings.md)*  
*For cloud project data and run scripts, see [CloudSecrets.md](CloudSecrets.md)*

---

*Last Updated: May 14, 2026*
*Version: Rev 00350+ baseline; May 14, 2026 — Rev 00348 SO + 0DTE ranking doc alignment, selector/SO telemetry grep tokens; May 6 snapshot note retained below.*
*Status: ✅ Production Ready — broker-only data, dynamic watchlists, structured 0DTE diagnostics in Cloud Logging*

