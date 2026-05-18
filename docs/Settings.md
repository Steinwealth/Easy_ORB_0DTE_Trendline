# Settings and Configuration Guide
## Easy ORB Strategy - Complete Configuration Manual

**Last Updated**: May 15, 2026  
**Version**: Rev 00350+ baseline; **May 15, 2026** documents local pass **`BUILD_ID` `00349-20260515-may15-calibration-so-json-symbols`** (production **`easy-etrade-strategy-00330-zdt` until deploy): Trendline impulse calibration (no new `TRENDLINE_*` keys); optional execution env **`USE_MARKET_ORDERS`** (default `true`), **`ENABLE_SMART_EXECUTION`** (default `true`), **`EXEC_LAST_LOOK_MAX_SPREAD_PCT`** (default `2.5`) — smart limits active only when **`USE_MARKET_ORDERS=false`**; SO **`json`** ranking fix and **`_process_orb_signals` → bool** batch dedupe; **`CISCO`/`NEBIUS`** aliases in data manager / `name_to_ticker`. See [May 15 session](doc_elements/Sessions/2026/May15%20Session/SESSION_SUMMARY_MAY15_2026.md). **May 14, 2026** documents **Rev 00348** ORB SO ranking keys in **`configs/ORBSO.env`** (**`SO_CONTINUATION_MOMENTUM_WEIGHT`**, **`SO_EXHAUSTION_PENALTY_WEIGHT`**, **`SO_MAX_EXTENSION_SOFT_PENALTY`**, **`SO_ORB_RANGE_SOFT_PENALTY`**, **`SO_MOMENTUM_DECELERATION_PENALTY`**, plus existing **`SO_WINNER_*`**), ORB 0DTE **priority** env **`0DTE_PRIORITY_RANK_W_*`**, and Cloud grep token **`ORB_0DTE_SELECTOR_FULL_REPLAY`** (structured INFO on selector ladder / debit-spread width failures — not a separate env flag). **May 13** documents ORB 0DTE **pre-queue / skip-stage** grep tokens, **`0DTE_MIN_VIABILITY_THRESHOLD`** default **0.30** in **`modules/orb0dte_execution_defaults.py`** (explicit **`ORB0DTE.env`** / profile overrides win), **`ORB_0DTE_CHAIN_HEALTH_RELAX_MIN_ELIGIBILITY=0.76`**, **May 13** selector block in **`ORB0DTE.env`** (momentum skip ATM tiers, liquidity relax list, short-leg step mult, hard-gate spread relax), **`OPTION_STEALTH_ORB_*`** on ORB spreads in **`Shared.env`**, **`ORB_0DTE_LIFECYCLE_AUDIT`** / EOD **`flatten_status`** + aggregated exit alerts, ORB SO ETF **startup rehydrate** (mock / E*TRADE portfolio) and **`PrimeUnifiedTradeManager.close_position`** for broker-only stragglers. **May 11** documents ORB 0DTE **`ORB_0DTE_EXECUTION_PROFILE`** + profile-bundled Convex/priority/overextension defaults (`modules/orb0dte_execution_defaults.py`), **`ORB_0DTE_OVEREXTENSION_*`** / legacy alias mapping, **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`**, durability **`ORB_0DTE_EXIT_GRADE_MIN_GOOD_TICKS`** / **`ORB_0DTE_DURABILITY_RECONCILE_WAIT_SECONDS`**, and spread relief **`ORB_OPTIONS_SPREAD_*` / `ORB_SPREAD_*`**; Trendline score defaults **`modules/trendline_entry_defaults.py`** and **`TRENDLINE_POSITION_MONITOR_INTERVAL_SEC`** default **7** (code + `Shared.env`). **May 6** aligns ORB 0DTE docs with runtime debit-spread delta band and Convex min-score helper reads. **May 5** corrects **canonical env merge order** (seven files: `Data.env` → `Shared.env` → `ORBSO.env` → `ORB0DTE.env` → `Trendline0DTE.env` → `Risk.env` → `Alerts.env` per `modules/config_loader.py`; former `base.env` / `deployment.env` are merged into these). **May 4** adds **`SO_ETF_EOD_CLOSE_*`** documentation (three-path EOD flatten window + Cloud Scheduler alignment). **Apr 30** documents Trendline **strict pre-emit**, **lifetime**, **pressure/touch spacing**, **missed-win / bad-entry** telemetry env keys, **`TRENDLINE_POST_BREAK_SURVIVAL_BARS`**, and **selector-built** log/token alignment (**`TRENDLINE_SELECTOR_*`**, **`use_selector_built`**; snapshot **`source`** = **`selector_built`** \| **`classified`**). Apr 28: entry-mode expansion, outage-safe monitoring parity.  
**Purpose**: Complete user guide for configuring the Easy ORB Strategy system, covering all settings, configuration files, and deployment options for ORB Strategy, 0DTE Strategy, **Easy Trendline 0DTE**, and Easy Collector.

**⚠️ Note**: For sensitive deployment-specific information (API keys, account IDs, credentials), see [PrivateSecrets.md](PrivateSecrets.md). For project IDs, service URLs, and run scripts, see [CloudSecrets.md](CloudSecrets.md).

---

## 📋 **Table of Contents**

1. [Configuration Overview](#configuration-overview)
2. [Configuration File Structure](#configuration-file-structure)
3. [Core Configuration Files](#core-configuration-files)
4. [Strategy Configuration](#strategy-configuration)
5. [Risk Management Configuration](#risk-management-configuration)
6. [Position Sizing Configuration](#position-sizing-configuration)
7. [Broker Configuration](#broker-configuration)
8. [Alert Configuration](#alert-configuration)
9. [Deployment Configuration](#deployment-configuration)
10. [Environment Variables](#environment-variables)
11. [Secrets Management](#secrets-management)
12. [Configuration Best Practices](#configuration-best-practices)

---

## 🎯 **Configuration Overview**

The Easy ORB Strategy uses a **modular configuration system** with environment-style files stored in the `configs/` directory. The system supports:

- **ORB Strategy**: Trading signals for US market stocks and leveraged ETFs
- **0DTE Strategy**: Options trading signals for 0DTE options (7:30 batch path, Convex/Hard Gate)
- **Easy Trendline 0DTE**: Third path—structure-first trendlines on the **full 0DTE list**, post-7:30 event entries, **premium stealth** exits (`modules/prime_options_stealth_trailing_tp.py`); does not replace SO or ORB 0DTE execution
- **Easy Collector**: ML data collection service

### **Supported Brokers**

The Easy ORB Strategy and 0DTE Strategy are configurable for US equities and options trading with multiple brokers:

| Broker | Status | Implementation |
|--------|--------|----------------|
| **E*TRADE** | ✅ **Fully Developed** | Production-ready, fully tested, default broker |
| **Interactive Brokers (IB)** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **Robinhood** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **Alpaca** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **TastyTrade** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **Tradier** | 🟡 Ready for Implementation | Architecture ready, requires API integration |

**Current Status**: E*TRADE is fully developed, tested, and production-ready. The system architecture supports multi-broker configuration, but other brokers require broker-specific API integration.

**Trading Modes**:
- **Demo Mode**: Simulated trading with demo/simulated account (uses production E*TRADE API)
- **Live Mode**: Real trading with live account (uses production E*TRADE API)

**Important**: E*TRADE does not have separate sandbox/production APIs. Both Demo and Live modes use the **production E*TRADE API**. The difference is which account is used (demo account vs live account).

### **Configuration Hierarchy**

1. **Primary**: `configs/` directory (env-style files)
2. **Runtime Overrides**: Environment variables (Cloud Run / shell)
3. **Local Development Secrets**: `secretsprivate/` (gitignored)
4. **Production Secrets**: Google Secret Manager (Cloud Run deployments)

### **Configuration Loading Order**

1. Default values (hardcoded in code)
2. Configuration files (`configs/*.env`) — see **`modules/config_loader.py`** for merge order: **`Data.env`** → **`Shared.env`** → **`ORBSO.env`** → **`ORB0DTE.env`** → **`Trendline0DTE.env`** → **`Risk.env`** → **`Alerts.env`** (later files override earlier). Former `base.env` / `deployment.env` / `trading-parameters.env` slices were merged into these seven files (May 2026).
3. Environment variables (override config file values when `get_config_value` runs — checked **before** the in-memory config dict)
4. Secret Manager (production secrets, where applicable)

**Startup export:** After `load_app_config()` in `main.py`, the **merged** configuration dict is copied into **`os.environ`** (string values). That makes file-based keys available to modules that call `os.getenv` directly (e.g. `TRENDLINE_MAX_POSITION_PCT` in `TrendlineOptionsExecutor`).

---

## 📁 **Configuration File Structure**

```
configs/
├── README.md                        # Load order + single-source-of-truth map ⭐
├── CONFIG_AUDIT_ORB_0DTE_TRENDLINE.md
├── Data.env                         # Broker + data stack + paths + deploy slices (merged at runtime ⭐)
├── Shared.env                       # Cross-path orchestration
├── ORBSO.env                        # ORB Standard Order (ETF) path
├── ORB0DTE.env                      # ORB 0DTE options path
├── Trendline0DTE.env                # Trendline 0DTE path
├── Risk.env                         # Sizing + risk + slip
├── Alerts.env                       # Notifications + alert toggles
└── (former `configs/*.env.template` files removed May 2026 — keys live in the seven files; use `secretsprivate/etrade.env.template` for broker secrets)

modules/strategy_mode_presets.py     # advanced / quantum overlays (merged after the seven .env files)
```

**Note**: Broker consumer secrets use **`secretsprivate/etrade.env.template`** → **`etrade.env`** (not under `configs/`).

---

## ⚙️ **Core Configuration Files**

### **1. Broker Configuration** (`configs/Data.env`) ⭐ **PRIMARY**

**Purpose**: Centralized broker and account selection for all strategies

**Key Settings**:
```bash
# Primary broker selection
BROKER_TYPE=etrade  # etrade (default), ib, robinhood

# Strategy-specific broker overrides (optional)
# ORB_BROKER_TYPE=etrade
# 0DTE_BROKER_TYPE=etrade

# E*TRADE Account Configuration (see PrivateSecrets.md for actual values)
ETRADE_ORB_ACCOUNT_ID=your_orb_account_id_here      # ORB Strategy account
ETRADE_0DTE_ACCOUNT_ID=your_0dte_account_id_here    # 0DTE Strategy account
ETRADE_DEFAULT_ACCOUNT_ID=your_default_account_id   # Default account (fallback)

# E*TRADE Trading Mode
# Note: E*TRADE does not have separate sandbox/production APIs
# Both Demo and Live modes use production E*TRADE API
# The difference is which account is used (demo account vs live account)
# DEPLOYMENT_MODE and related deploy keys live in merged configs (typically Data.env / Shared.env)

# E*TRADE API Settings
ETRADE_ENABLED=true
ETRADE_REAL_TIME_QUOTES=true
ETRADE_BATCH_QUOTES=true
ETRADE_DAILY_CALL_LIMIT=1180
ETRADE_RATE_LIMIT_CALLS_PER_MINUTE=100
```

**Account Selection Priority**:
1. Strategy-specific account (e.g., `ETRADE_ORB_ACCOUNT_ID`)
2. Default account (e.g., `ETRADE_DEFAULT_ACCOUNT_ID`)
3. Auto-select first available active account

**Integration**: Used by `BrokerConfigManager` (`modules/broker_config_manager.py`) for centralized broker and account selection.

### **Execution policy (optional — May 15, 2026)**

**Purpose**: Order-time execution quality (fills, slippage, exit routing) without changing signal engines. Read via `get_config_value` / `os.getenv` in `modules/execution_routing.py` and wired paths.

| Key | Default | Role |
|-----|---------|------|
| `USE_MARKET_ORDERS` | `true` | When `true`, legacy **MARKET**-only behavior (same as pre–May 15). Set `false` to enable smart limit ladders. |
| `ENABLE_SMART_EXECUTION` | `true` | Smart path allowed only when `USE_MARKET_ORDERS=false`. |
| `EXEC_LAST_LOOK_MAX_SPREAD_PCT` | `2.5` | Last-look spread cap; opening profile can widen internally. |

**Modules:** `execution_telemetry.py`, `execution_routing.py`, `smart_equity_execution.py`, `execution_profiles.py`, `execution_fill_reconcile.py`. **Grep:** `EXECUTION_FILL_SUMMARY`, `EXECUTION_AGGRESSION_ESCALATED`, `EXECUTION_FILL_RECONCILED`. Full table: [0DTEORB.md](0DTEORB.md) and [May 15 session](doc_elements/Sessions/2026/May15%20Session/SESSION_SUMMARY_MAY15_2026.md).

### **2. Deployment / cloud shell** (`configs/Data.env` + `configs/Shared.env`)

**Purpose**: Broker defaults, GCP/Cloud Run–oriented keys, and cross-path feature flags (merged from former `deployment.env`; templates removed May 2026).

**Key Settings**:
```bash
# Deployment Mode
DEPLOYMENT_MODE=demo  # demo or live
AUTOMATION_ENABLED=true

# Broker Configuration
BROKER_TYPE=etrade
BROKER_DATA_ONLY=true  # All data from configured broker only

# Strategy Enablement
ENABLE_ORB_STRATEGY=true   # Always enabled
ENABLE_0DTE_STRATEGY=true  # Enable 0DTE Strategy

# Feature Flags
ENABLE_RISK_MANAGEMENT=true
ENABLE_PERFORMANCE_TRACKING=true
ENABLE_AUTO_TRADING=true
ENABLE_ALERT_MANAGER=true
ENABLE_PRIME_SYSTEM=true

# Google Cloud Configuration (see PrivateSecrets.md for actual values)
GCP_PROJECT_ID=your-project-id
GCP_REGION=us-central1
GCP_ZONE=us-central1-a
GCP_SERVICE_NAME=your-service-name  # See PrivateSecrets.md for actual value
GCS_BUCKET_NAME=your-project-id-data

# Container Configuration
CONTAINER_CPU=2
CONTAINER_MEMORY=4Gi
CONTAINER_MAX_INSTANCES=10
CONTAINER_MIN_INSTANCES=0  # Scale-to-zero enabled
CONTAINER_TIMEOUT=3600s
```

### **3. Base / app-wide configuration** (`configs/Shared.env`, `configs/Data.env`, `configs/Risk.env`)

**Purpose**: Core system settings merged into the seven canonical files. **`OPTION_STEALTH_*`** (generic) and cross-path orchestration live in **`Shared.env`** / **`Risk.env`**; Trendline-specific keys live in **`Trendline0DTE.env`** (see below).

**Key Settings** (representative):
```bash
# System Identity
PROJECT_NAME=Easy ORB Strategy
VERSION=2.0
ENVIRONMENT=development  # development or production

# Trading Mode
TRADING_MODE=demo  # demo or live

# Performance Optimization
MAX_WORKERS=8
BATCH_SIZE=20
CACHE_TTL_SECONDS=60
MEMORY_LIMIT_MB=2048
POLL_SECONDS=1.0

# Position Monitoring
POSITION_MONITORING_INTERVAL=30  # 30-second monitoring

# Market Hours
TZ=America/New_York
PREP_START_ET=08:30
RTH_OPEN_ET=09:30
RTH_CLOSE_ET=16:00
AFTER_HOURS_END_ET=20:00

# Holidays
HOLIDAYS_ENABLED=true
HOLIDAYS_CUSTOM_PATH=data/holidays_custom.json

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_TO_STDOUT=true
LOG_ROTATION=true
LOG_MAX_SIZE_MB=100
```

### **4. Strategy Configuration** (`configs/Shared.env` + path `.env` files)

**Purpose**: Cross-path orchestration in **`Shared.env`**; **capital %** and **ORB SO schedule** in **`ORBSO.env`**; **ORB 0DTE** in **`ORB0DTE.env`**; **Trendline 0DTE** in **`Trendline0DTE.env`**.

**Key Settings** (illustrative — live **`SO_CAPITAL_PCT` / `ORR_*` / `CASH_RESERVE_PCT`** are in **`ORBSO.env`**):
```bash
# Strategy Modes
STRATEGY_MODES=standard,advanced,quantum
DEFAULT_STRATEGY_MODE=standard
PRIMARY_SIGNAL_GENERATOR=orb

# Capital Allocation (CRITICAL: Must sum to 100%)
SO_CAPITAL_PCT=90.0      # Standard Order allocation (90%)
ORR_CAPITAL_PCT=0.0      # Opening Range Reversal (disabled)
CASH_RESERVE_PCT=10.0    # Cash reserve (auto-calculated)

# Standard Strategy Settings
STANDARD_TARGET_WEEKLY_RETURN=0.01
STANDARD_BASE_RISK_PER_TRADE=0.02
STANDARD_MAX_RISK_PER_TRADE=0.05
STANDARD_MIN_QUALITY_SCORE=60
STANDARD_MIN_CONFIDENCE_SCORE=0.9
STANDARD_POSITION_SIZE_PCT=10.0

# Advanced Strategy Settings
ADVANCED_TARGET_WEEKLY_RETURN=0.10
ADVANCED_BASE_RISK_PER_TRADE=0.05
ADVANCED_MAX_RISK_PER_TRADE=0.15
ADVANCED_MIN_QUALITY_SCORE=70
ADVANCED_POSITION_SIZE_PCT=20.0

# Quantum Strategy Settings
QUANTUM_TARGET_WEEKLY_RETURN=0.50
QUANTUM_BASE_RISK_PER_TRADE=0.10
QUANTUM_MAX_RISK_PER_TRADE=0.25
QUANTUM_MIN_QUALITY_SCORE=80
QUANTUM_POSITION_SIZE_PCT=30.0
```

**Capital Allocation Rules**:
- `SO_CAPITAL_PCT + ORR_CAPITAL_PCT + CASH_RESERVE_PCT = 100%`
- Current: 90% SO, 0% ORR, 10% Reserve
- Adjust `SO_CAPITAL_PCT` to change overall capital deployment

---

## 📊 **Strategy Configuration**

### **ORB Strategy Configuration**

**Location**: `configs/ORBSO.env`, `configs/ORB0DTE.env`, `configs/Trendline0DTE.env`, `configs/Shared.env`, `configs/Risk.env`

**Key Settings**:
- **Capital Allocation**: 90% SO, 10% Reserve (configurable)
- **ORB SO ETF @ 7:30**: up to **15** simultaneous SO executions / greedy sizing divisor — `MAX_CONCURRENT_TRADES` in **`configs/ORBSO.env`** (read by `prime_risk_manager` SO sizing). Not the same knob as ORB 0DTE or Trendline.
- **ORB 0DTE @ 7:30**: max **6** concurrent option positions — `0DTE_MAX_POSITIONS` in **`configs/ORB0DTE.env`** (also documented under [0DTEORB.md](0DTEORB.md)).
- **Trendline 0DTE**: max **5** rolling concurrent options — `TRENDLINE_MAX_OPEN_POSITIONS` + slot sizing (`TRENDLINE_SLOT_COUNT`, `TRENDLINE_ACCOUNT_ALLOCATION_PCT`) in **`configs/Trendline0DTE.env`**; see [0DTETrendline.md](0DTETrendline.md).
- **Combined options book** (ORB 0DTE + Trendline opens): `MAX_TOTAL_OPTION_POSITIONS` (**11**).
- **Risk manager ceiling** (count of tracked strategy positions, all paths): `MAX_OPEN_POSITIONS` — default **26** in **`configs/Risk.env`** so SO + 0DTE + Trendline maxima do not hit the generic limiter before path-specific caps.
- **Entry Window**: 7:15-7:30 AM PT (SO signals)
- **Execution Time**: 7:30 AM PT
- **Watchlist**: `data/watchlist/core_list.csv` (dynamic symbol count, fully scalable — leveraged ETFs plus any dual-listed spot names for ORB SO)
- **EOD flatten window (PT)** — in **`configs/ORBSO.env`** (single source for `_so_etf_eod_close_window_pt()`):
  - **`SO_ETF_EOD_CLOSE_START_PT`** / **`SO_ETF_EOD_CLOSE_END_PT`** — default **`12:55`** / **`12:56`** (end is exclusive in the main-loop check: flatten runs while **start ≤ now < end**).
  - Applies to **`flatten_all_paths_for_eod_scheduler()`**: ORB ETF demo/SO stealth batch close, ORB 0DTE `close_all_positions`, Trendline demo EOD close — same entry point as **`POST /api/end-of-day-report`** (dedupe skips a second full flatten in the **same process** on the same calendar day).

**Priority rank (current):**
- **ORB SO (`configs/ORBSO.env` + `calculate_so_priority_score`):** **Rev 00348** — **`SO_CONTINUATION_MOMENTUM_WEIGHT`** (default **0.32**) sets the continuation-quality share of the **base** score; remainder blends VWAP continuation, RS vs SPY, ORB volume, confidence, RSI, ORB range (**0.24 / 0.18 / 0.28 / 0.10 / 0.14 / 0.06** of the remainder). Soft layers: **`SO_EXHAUSTION_PENALTY_WEIGHT`**, **`SO_MAX_EXTENSION_SOFT_PENALTY`**, **`SO_ORB_RANGE_SOFT_PENALTY`**, **`SO_MOMENTUM_DECELERATION_PENALTY`**. Then **`quality_multiplier`** and **`SO_WINNER_*`** gate. Logs: **`SO_RANK_BREAKDOWN`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`**. (Legacy fixed v2.1 27/25/22/13/10/2 weights = research lineage, not the live base.)
- **ORB 0DTE (`configs/ORB0DTE.env` / profile, `_rank_signals_by_priority`):** **`0DTE_PRIORITY_RANK_W_BREAKOUT`**, **`0DTE_PRIORITY_RANK_W_ORB_RANGE`**, **`0DTE_PRIORITY_RANK_W_VOLUME`**, **`0DTE_PRIORITY_RANK_W_CONVEX`**, **`0DTE_PRIORITY_RANK_W_EARLY_MOMENTUM`** — defaults **0.14 / 0.10 / 0.22 / 0.20 / 0.34** in code / **`modules/orb0dte_execution_defaults.py`**.

### **0DTE Strategy Configuration**

**Location**: `easy0DTE/configs/0dte.env` (standalone package reference). **Integrated trading app:** execution caps and ORB 0DTE guardrails are set in **`configs/ORB0DTE.env`** (`0DTE_MAX_POSITIONS`, `0DTE_EXECUTION_*`, etc.); combined book cap `MAX_TOTAL_OPTION_POSITIONS` is in **`configs/Shared.env`**. `ConfigLoader` merge order resolves duplicate keys.

**Key Settings**:
```bash
# Enable 0DTE Strategy
ENABLE_0DTE_STRATEGY=true

# Trading Mode
ETRADE_MODE=demo  # demo or live
DEPLOYMENT_MODE=demo

# Convex Eligibility Filter
0DTE_CONVEX_VOLATILITY_PERCENTILE=0.80
0DTE_CONVEX_ORB_RANGE_MIN=0.35
0DTE_CONVEX_MOMENTUM_REQUIRED=true
0DTE_CONVEX_TREND_DAY_REQUIRED=true
0DTE_CONVEX_MIN_SCORE=0.75

# 0DTE priority ranking (Rev 00348 defaults; profile / ORB0DTE.env override)
# 0DTE_PRIORITY_RANK_W_BREAKOUT=0.14
# 0DTE_PRIORITY_RANK_W_ORB_RANGE=0.10
# 0DTE_PRIORITY_RANK_W_VOLUME=0.22
# 0DTE_PRIORITY_RANK_W_CONVEX=0.20
# 0DTE_PRIORITY_RANK_W_EARLY_MOMENTUM=0.34

# Position Limits
0DTE_MAX_POSITIONS=6  # Max concurrent ORB 0DTE options at 7:30 (integrated app: `configs/ORB0DTE.env`; standalone copy: `easy0DTE/configs/0dte.env`)

# Debit Spread Settings (current ORB0DTE.env baseline)
0DTE_DEBIT_SPREAD_TARGET_DELTA_MIN=0.30
0DTE_DEBIT_SPREAD_TARGET_DELTA_MAX=0.45

# Target Symbols
# Loaded dynamically from data/watchlist/0dte_list.csv (current full universe),
# not hardcoded in config.
# 0DTE_TARGET_SYMBOLS is optional override for special testing only.

# Account Configuration (Rev 00245)
# Managed by merged config (primarily Data.env + secretsprivate/secret manager)
# See PrivateSecrets.md for actual account ID values
# Backward compatible: Also checks 0DTE_ETRADE_ACCOUNT_ID

# Execution quality guardrails (Rev 00319)
0DTE_MIN_RISK_REWARD=0.20
0DTE_MIN_MAX_PROFIT_PER_SPREAD=0.15
0DTE_MAX_DEBIT_TO_WIDTH_PCT=0.92
```

**Priority Score Formula** (v1.1 - Rev 00246):
- Breakout: **35%** (↑ from 30%)
- Range: **30%** (↑ from 25%)
- Volume: **20%** (same)
- Eligibility: **15%** (same)
- RS vs SPY: **REMOVED** (not relevant for 0DTE options)
- Momentum: **REMOVED** (redundant with breakout score)

**Red Day Filtering** (current policy):
- **LONG (CALL) non-Tier-1 trades**: Rejected on Red Days
- **LONG (CALL) Tier-1 trades**: Allowed on Red Days
- **SHORT (PUT) trades**: Allowed and encouraged on Red Days

**Integration with ORB Strategy**:
1. 0DTE Strategy listens to ORB signal generation
2. Filters ORB signals using Convex Eligibility Filter
3. Generates options strategies (single-leg-primary: strong->long_call/long_put, moderate->lotto, weak->momentum_scalper fallback, else->itm_probability_spread fallback)
4. Executes options trades via E*TRADE Options API
5. Manages options exits independently

**0DTE selector threshold defaults** (env-driven loose-start profile):
```bash
0DTE_STRONG_MOMENTUM_MIN=70.0
0DTE_STRONG_BREAKOUT_DISTANCE_RATIO_MIN=0.08
0DTE_STRONG_MIN_CONFIDENCE=0.72
0DTE_STRONG_MIN_VOLUME_RATIO=1.05

0DTE_MODERATE_MOMENTUM_MIN=55.0
0DTE_MODERATE_BREAKOUT_DISTANCE_RATIO_MIN=0.02
0DTE_MODERATE_MIN_CONFIDENCE=0.58
0DTE_MODERATE_MIN_VOLUME_RATIO=0.90

0DTE_WEAK_MOMENTUM_MAX=50.0
0DTE_WEAK_BREAKOUT_DISTANCE_RATIO_MAX=0.015
0DTE_WEAK_MIN_CONFIDENCE=0.55
0DTE_NEAREST_EXPIRY_MAX_DAYS=10
```

**0DTE chain expiry behavior (live chain manager):**
- `SPY`, `QQQ`, `IWM` are currently treated as **0DTE-native** (strict same-day first, then nearest fallback).
- Non-native symbols can use nearest available expiry immediately when same-day contracts are unavailable.
- Runtime diagnostics:
  - `SYMBOL_CLASSIFICATION` (`0DTE_NATIVE` vs `NON_0DTE`)
  - `CHAIN_EXPIRY_SELECTION` (`0DTE`, `NEAREST`, `NONE`, `EXPLICIT`)

---

### **Easy Trendline 0DTE Configuration**

**Location**: Trendline signal/hold, option selection, and Trendline-specific stealth keys live in **`configs/Trendline0DTE.env`** (and **`OPTION_STEALTH_*`** where shared, in **`configs/Shared.env`** / **`configs/Risk.env`**). **Path enablement** and **Trendline operational caps** (`ENABLE_TRENDLINE_STRATEGY`, daily cap, slot sizing, `TRENDLINE_DEMO_STARTING_BALANCE`) live in **`configs/Trendline0DTE.env`**; ORB SO and ORB 0DTE toggles live in **`configs/ORBSO.env`** and **`configs/ORB0DTE.env`**. Override in Cloud Run only if needed. See **`configs/README.md`** for load order.

At runtime, `main.py` loads:

- `TrendlineConfig` → `easyTrendline.trendline_config_loader.load_trendline_config_from_env(get_config_value)`
- `TrendlineOptionSelectionConfig` → `load_trendline_option_selection_config(get_config_value)`
- `OptionStealthConfig` → `modules.prime_options_stealth_trailing_tp.load_option_stealth_config(get_config_value)`

**Enablement / universe**:
```bash
ENABLE_TRENDLINE_STRATEGY=true
TRENDLINE_USE_FULL_0DTE_LIST=true   # default: full 0dte_list / dte0_manager.target_symbols
```

- At **7:30 AM PT**, each symbol with ORB context gets **structure-first** setup selection (`ascending_support` + long put on breakdown, or `descending_resistance` + long call on breakout). No options fire at 7:30.
- **Intraday for the build:** **`get_batch_intraday_data(..., bars=1)`** (ORB-timed bar). **Quotes** for the same chunk are merged in-app so structure has **two distinct timestamps** (broker-only path: **`bars > 1`** does **not** return true multi-bar history—see `prime_data_manager.get_batch_intraday_data`). Fetches run in **chunks** with explicit **budgets** (defaults below). **`TRENDLINE_ENABLE_BUILD_DEGRADATION`:** `true` = partial build + deferred symbols; `false` = skip entire Trendline build if the post-ORB universe needs more chunk pairs than allowed. Full detail: [0DTETrendline.md](0DTETrendline.md).
- Near-equal **MSE** ties between both geometries log **`setup_tie_break`** and pick deterministically—symbols are not skipped for that alone.
- **ORB ETF** and **ORB 0DTE** lists and batch execution are unchanged.

**Trendline capacity + slot sizing** (also in `prime_trading_system` via `get_config_value`):
```bash
TRENDLINE_MAX_TRADES_PER_DAY=5
TRENDLINE_MAX_OPEN_POSITIONS=5
TRENDLINE_SLOT_COUNT=5
TRENDLINE_ACCOUNT_ALLOCATION_PCT=90
MAX_TOTAL_OPTION_POSITIONS=11            # ORB 0DTE + Trendline open options combined (6+5)
TRENDLINE_MAX_POSITION_PCT=0.12          # per-trade cap vs balance (executor)
TRENDLINE_DEMO_STARTING_BALANCE=5000.0    # Trendline demo ledger only
```

**7:30 build — broker request budgets** (paired **one intraday + one quote** call per chunk; defaults are tuned for the current `0dte_list.csv` scale at 25/chunk):

```bash
TRENDLINE_DATA_CHUNK_SIZE=25
TRENDLINE_MAX_INTRADAY_BATCH_CALLS_PER_BUILD=8
TRENDLINE_MAX_QUOTE_BATCH_CALLS_PER_BUILD=8
TRENDLINE_MAX_SYMBOLS_PER_BUILD=200
TRENDLINE_ENABLE_BUILD_DEGRADATION=true
TRENDLINE_MAX_BUILD_DURATION_MS=0
```

- `usable_capital = trendline_account_balance * (TRENDLINE_ACCOUNT_ALLOCATION_PCT / 100)`
- `slot_capital = usable_capital / TRENDLINE_SLOT_COUNT` (snapshot at 7:30 build)
- Runtime entry gate is **rolling open positions**: execute while `open_positions < TRENDLINE_MAX_OPEN_POSITIONS`; when a position exits, next ready signal can enter.

**Entry / hold / structure / momentum** (env → `TrendlineConfig` via `load_trendline_config_from_env`; canonical keys in **`configs/Trendline0DTE.env`** / **`Shared.env`**):

```bash
TRENDLINE_MIN_BREAK_PCT=0.001
TRENDLINE_USE_ATR_BREAK=true
TRENDLINE_ATR_BREAK_MULTIPLIER=0.25
TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES=3
TRENDLINE_REQUIRE_HOLD_AFTER_BREAK=true
TRENDLINE_HOLD_MODE=time_based              # or true_bar_based
TRENDLINE_MIN_HOLD_SECONDS=90
TRENDLINE_CONFIRM_SECONDS=10              # runtime override in signal engine, clamped to 1-30
TRENDLINE_MIN_ENTRY_DISTANCE=0.0007       # minimum normalized break distance for early-entry paths
TRENDLINE_CONTINUATION_MIN_BARS=2         # min pullback bars before continuation entry checks
TRENDLINE_CONTINUATION_MAX_BARS=8         # continuation timeout window
TRENDLINE_PULLBACK_STRENGTH_THRESHOLD=0.62
TRENDLINE_HOLD_BARS_REQUIRED=2
TRENDLINE_HOLD_BAR_INTERVAL=1m
TRENDLINE_MAX_BREAK_TO_HOLD_RETRACE_PCT=0.40
TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK=true
TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE=true
TRENDLINE_POST_BREAK_STRUCTURE_LOOKBACK_BARS=3
TRENDLINE_MIN_CONTINUATION_DISTANCE_PCT=0.0005
TRENDLINE_POST_CONTINUE_SETTLE_BARS=1
TRENDLINE_POST_CONTINUE_CHOP_BOX_GATE=true
TRENDLINE_POST_CONTINUE_FAST_FOLLOWTHROUGH_BARS=8
TRENDLINE_POST_CONTINUE_FAST_EXTENSION_PCT=0.0009
TRENDLINE_POST_CONTINUE_BOX_BREAK_BUFFER_PCT=0.0002
TRENDLINE_POST_CONTINUE_CHOP_MIN_PRIOR_BARS=3
TRENDLINE_NO_NEW_ENTRIES_AFTER_PT=11:30   # HH:MM Pacific — new Trendline entries stop; exits continue
TRENDLINE_MAX_BREAK_TO_CONFIRM_MINUTES=60 # 0 = no timeout
TRENDLINE_MAX_DISTANCE_FROM_BREAK_PCT=0.005
TRENDLINE_ALLOW_SECOND_BREAK_ATTEMPT=false
TRENDLINE_CONFIRMATION_WINDOW_BARS=3
TRENDLINE_MIN_FOLLOWTHROUGH_BARS=1
TRENDLINE_MIN_VELOCITY_PCT=0.0009
TRENDLINE_RANGE_EXPANSION_MULTIPLIER=1.18
TRENDLINE_MIN_BREAK_QUALITY_SCORE=0.38
TRENDLINE_STRONG_BREAKOUT_DISTANCE_MULT=1.2
TRENDLINE_BODY_EXPANSION_MULT=1.3
TRENDLINE_MIN_BREAKOUT_DISTANCE=0.0025
TRENDLINE_CLEAN_BREAKOUT_BYPASS_MOMENTUM=true
TRENDLINE_MIN_DRIFT_DISPLACEMENT=0.003   # drift net-move floor from break price
TRENDLINE_REQUIRE_DRIFT_CONFIRM=false    # optional strict mode: 4 directional candles for drift

# --- Trendline strict pre-emit, maturity, lifetime, pressure (Apr 2026; wired in trendline_signal_engine) ---
TRENDLINE_STRICT_MIN_BREAK_DISTANCE=0.0015
TRENDLINE_STRICT_MIN_BODY_RATIO=0.5
TRENDLINE_HIGH_PRESSURE_TOUCH_COUNT=3
TRENDLINE_PRESSURE_SCORE_MIN=2.0
TRENDLINE_TOUCH_TOLERANCE_PCT=0.0012
TRENDLINE_MIN_TOUCH_BAR_GAP=1
TRENDLINE_MIN_STRUCTURE_BARS=3
TRENDLINE_MIN_STRUCTURE_SECONDS=90
TRENDLINE_MAX_ACTIVE_MINUTES=180
TRENDLINE_POST_BREAK_SURVIVAL_BARS=3
TRENDLINE_MISSED_WIN_MOVE_PCT=0.005
TRENDLINE_BAD_ENTRY_MAX_FAVORABLE_PCT=0.002
TRENDLINE_BAD_ENTRY_DRAWDOWN_PCT=0.003
TRENDLINE_CONFIRM_PENDING_LOG_SEC=120
```

- **`time_based` hold**: price must stay on the **correct side** of the line until `TRENDLINE_MIN_HOLD_SECONDS` elapse after the break (pending state does not invalidate).
- **`TRENDLINE_CONFIRM_SECONDS`**: when set, the signal engine uses this as the runtime hold target for time-based confirmation and clamps it to **1-30 seconds** (default `10`); `TRENDLINE_MIN_HOLD_SECONDS` remains the base config key in `TrendlineConfig`.
- **`TRENDLINE_MIN_ENTRY_DISTANCE`**: blocks immediate/first-move entries when normalized break distance is too small (noise control).
- **Pullback continuation controls**: `TRENDLINE_PULLBACK_STRENGTH_THRESHOLD` gates anti-chop conversion to continuation candidate; `TRENDLINE_CONTINUATION_MIN_BARS` and `TRENDLINE_CONTINUATION_MAX_BARS` bound continuation entry timing and timeout.
- **`true_bar_based` hold**: requires `TRENDLINE_HOLD_BARS_REQUIRED` consecutive valid samples.
- **Post-continuation settle:** optional full **1m** bars after the continuation bar before structure + momentum (`0` = off). Logs `TRENDLINE_PIPELINE | stage=continuation_settle`.
- **Post-continuation chop / box gate:** when `TRENDLINE_POST_CONTINUE_CHOP_BOX_GATE=true`, after continuation (+ settle) the engine requires either **fast close-based extension** in the first `FAST_FOLLOWTHROUGH_BARS` bars or a **box low / box high** break (slow path). Disables like any bool env. Detail: [0DTETrendline.md](0DTETrendline.md).
- **`TRENDLINE_NO_NEW_ENTRIES_AFTER_PT`:** loaded into `TrendlineConfig` and used by `PrimeTradingSystem` to stop **new** Trendline entries after that **Pacific** clock; monitoring and exits continue.
- **Breakout quality tuning knobs**: `TRENDLINE_STRONG_BREAKOUT_DISTANCE_MULT`, `TRENDLINE_BODY_EXPANSION_MULT`, `TRENDLINE_MIN_BREAKOUT_DISTANCE` control clean/strong breakout sensitivity. `TRENDLINE_CLEAN_BREAKOUT_BYPASS_MOMENTUM` controls whether clean/strong breaks can bypass strict momentum rejection.
- **Drift-entry tuning knobs**: `TRENDLINE_MIN_DRIFT_DISPLACEMENT` filters weak sideways drift by requiring minimum net displacement from break price before drift entry is eligible. `TRENDLINE_REQUIRE_DRIFT_CONFIRM=true` optionally raises directional-candle confirmation from 3 to 4; default `false` preserves current behavior.
- **Final Trendline entry safeguard**: before `entry_ready`, the engine requires breakout follow-through (`distance_increasing && body_expanding`) and logs `TRENDLINE_ENTRY_FILTER`; failures log `reason=no_follow_through`.
- **Production visibility logs**: `TRENDLINE_CONFIRM_CONFIG`, `TRENDLINE_DRIFT_METRICS`, `TRENDLINE_BREAKOUT_QUALITY`, **`TRENDLINE_BREAK_QUALITY`**, `TRENDLINE_ENTRY_DECISION`, **`TRENDLINE_DECISION_SNAPSHOT`** (canonical outcome row, **≤ one per candidate per bar**; **`confidence_score`**, **`line_quality`**, **`source`** `selector_built`\|`classified`), **`TRENDLINE_DECISION_GEOMETRY_DETAIL`** (separate intrabar schema), **`TRENDLINE_SKIP_REASON`** (+ **`raw_reason`** when remapped), **`TRENDLINE_FALLBACK_USED`**, **`TRENDLINE_SELECTOR_STRUCTURE_ACTIVE`**, **`TRENDLINE_SELECTOR_LOW_CONFIDENCE`**, **`TRENDLINE_LINE_SELECTED`**, **`TRENDLINE_LEGACY_PATH_BLOCKED`**, **`TRENDLINE_CANDIDATE_EXPIRED`**, **`TRENDLINE_PRESSURE_TOUCH`** / **`TRENDLINE_PRESSURE_SCORE`**, **`TRENDLINE_FLOW_STAGE`**, **`TRENDLINE_PRE_ENTRY_CHECKPOINT`**, **`TRENDLINE_ENTRY_EVAL_ORDER`**, **`TRENDLINE_MISSED_WIN`**, **`TRENDLINE_MISSED_WIN_EARLY`**, **`TRENDLINE_BAD_ENTRY`**, **`TRENDLINE_ALERT`**, `TRENDLINE_ENTRY_TIMING`, `TRENDLINE_ENTRY_FILTERED`, `TRENDLINE_MISSED_OPPORTUNITY`, `TRENDLINE_SESSION_SUMMARY`, and `TRENDLINE_EXIT_SUMMARY`. (Legacy **`TRENDLINE_PREBUILT_*`** names are no longer emitted; use **`TRENDLINE_SELECTOR_*`** / pipeline **`use_selector_built`**.)
- **Normalized Trendline entry types** in telemetry: `strong_break`, `clean_break`, `drift`, `first_move`.

**0DTE contract selection** (Trendline path only):
```bash
TRENDLINE_OPTION_DELTA_MIN=0.20
TRENDLINE_OPTION_DELTA_MAX=0.35
TRENDLINE_DELTA_TOLERANCE=0.02
TRENDLINE_OPTION_STRIKE_MODE=otm_1_to_2
TRENDLINE_OPTION_LOTTO_MODE=true
TRENDLINE_OPTION_MAX_SPREAD_PCT=0.40
TRENDLINE_OPTION_MIN_OPEN_INTEREST=0
TRENDLINE_OPTION_MIN_VOLUME=0
```

Rejection reasons are logged as `TRENDLINE_PIPELINE | stage=contract_rejected | reason=...` (delta band, spread, liquidity, slot too small, etc.).
When `delta > TRENDLINE_OPTION_DELTA_MAX` but `delta <= TRENDLINE_OPTION_DELTA_MAX + TRENDLINE_DELTA_TOLERANCE`, contract selection can continue and logs `TRENDLINE_DELTA_TOLERANCE_USED`.

**Options stealth trailing** (Trendline exits; **not** ETF `prime_stealth_trailing_tp`). Canonical values live in **`configs/Shared.env`** / **`configs/Risk.env`** / **`configs/Trendline0DTE.env`** (see `configs/README.md` single-source table).

```bash
OPTION_STEALTH_BREAKEVEN_TRIGGER_PCT=0.25
OPTION_STEALTH_BREAKEVEN_LOCK_PCT=0.08
OPTION_STEALTH_EARLY_BE_LOCK_PCT=0.04
OPTION_STEALTH_DELTA_ITM_THRESHOLD=0.60
OPTION_STEALTH_DELTA_ATM_THRESHOLD=0.40
OPTION_STEALTH_BE_TRIGGER_ITM_PCT=0.15
OPTION_STEALTH_BE_TRIGGER_ATM_PCT=0.20
OPTION_STEALTH_BE_TRIGGER_OTM_PCT=0.28
OPTION_STEALTH_MIN_SECONDS_BEFORE_BE=60
OPTION_STEALTH_REQUIRE_NEW_HWM_FOR_BE=true
OPTION_STEALTH_TRAILING_TRIGGER_PCT=0.35
OPTION_STEALTH_BASE_TRAILING_PCT=0.22
OPTION_STEALTH_EXPLOSIVE_TRAILING_PCT=0.14
OPTION_STEALTH_MOON_TRAILING_PCT=0.10
OPTION_STEALTH_TIME_EXIT_MINUTES=12
OPTION_STEALTH_NO_PROGRESS_EXIT_MINUTES=10
OPTION_STEALTH_FAST_FAIL_ENABLE=true
OPTION_STEALTH_FAST_FAIL_MINUTES=5
OPTION_STEALTH_FAST_FAIL_MIN_PNL_PCT=0.05
OPTION_STEALTH_ADVERSE_GUARD_ENABLE=true
OPTION_STEALTH_ADVERSE_GUARD_MIN_MINUTES=6.0
OPTION_STEALTH_ADVERSE_GUARD_UNDERLYING_MOVE_PCT=0.004
OPTION_STEALTH_ADVERSE_GUARD_MAX_PNL_PCT=0.03
OPTION_STEALTH_ORB_BE_TRIGGER_MULT=0.75
OPTION_STEALTH_ORB_TRAILING_TRIGGER_MULT=0.85
OPTION_STEALTH_ORB_PROFIT_LOCK_TRIGGER_PCT=0.12
OPTION_STEALTH_ORB_PROFIT_LOCK_PCT=0.03
OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES=240
OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES=45
OPTION_STEALTH_TLINE_BE_TRIGGER_MULT=0.50
OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT=0.82
OPTION_STEALTH_TLINE_PROFIT_LOCK_TRIGGER_PCT=0.10
OPTION_STEALTH_TLINE_PROFIT_LOCK_PCT=0.012
OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT=0.10
OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA=true
OPTION_STEALTH_STRUCTURE_INVALIDATION_ENABLED=true
OPTION_STEALTH_DISABLE_TP_LADDER=true
OPTION_STEALTH_STRUCTURE_BUFFER_PCT=0.0005
OPTION_STEALTH_PREMIUM_MAX_JUMP_PCT=0.55
OPTION_STEALTH_MAX_STALE_SECONDS=2.0
OPTION_STEALTH_PREMIUM_JUMP_RECHECK_PCT=0.15
OPTION_STEALTH_FORCE_REEVAL_ON_PREMIUM_JUMP=true
OPTION_STEALTH_NO_DATA_OUTAGE_SECONDS=90
OPTION_STEALTH_PREMIUM_MAX_MULT=12.0
OPTION_STEALTH_PREMIUM_MIN_MULT=0.02
OPTION_STEALTH_EXPLOSIVE_PNL_THRESHOLD_PCT=0.50
OPTION_STEALTH_MOON_PNL_THRESHOLD_PCT=1.00

# Spread net / multi-leg (ORB 0DTE when position_type is debit_spread or credit_spread)
OPTION_STEALTH_SPREAD_BE_TRIGGER_PCT=0.18
OPTION_STEALTH_SPREAD_BE_LOCK_PCT=0.04
OPTION_STEALTH_SPREAD_TRAILING_TRIGGER_PCT=0.28
OPTION_STEALTH_SPREAD_BASE_TRAILING_PCT=0.12
OPTION_STEALTH_SPREAD_TIME_EXIT_MINUTES=9
OPTION_STEALTH_SPREAD_NO_PROGRESS_EXIT_MINUTES=6
```

- **Normalized options metadata:** Executors attach **`metadata["normalized_options"]`** (see `easy0DTE/modules/options_execution_normalize.py`). `PrimeTradingSystem` validates with **`validate_normalized_options_for_stealth`** before using it as the **primary** input to shared options stealth **`register_on_open`**; invalid or missing blobs fall back to legacy position fields. Cloud grep hints: `OPTIONS_STEALTH | stage=normalized_metadata_used`, `OPTIONS_STEALTH | stage=normalized_metadata_invalid`, `OPTIONS_STEALTH | stage=legacy_metadata_fallback`.

- **Delta-aware BE:** at open, \|delta\| maps to **ITM / ATM / OTM** buckets (`OPTION_STEALTH_DELTA_*_THRESHOLD`) and sets the PnL% threshold from `OPTION_STEALTH_BE_TRIGGER_*_PCT` (with `BREAKEVEN_TRIGGER_PCT` as a fallback if needed). BE arms only after `MIN_SECONDS_BEFORE_BE` and, when `REQUIRE_NEW_HWM_FOR_BE=true`, after a modest new high in effective premium vs entry. The locked floor uses `EARLY_BE_LOCK_PCT` when **positive**, else `BREAKEVEN_LOCK_PCT`. **Trendline single-leg** (`strategy_type=trendline_0dte`): the stored trigger is further scaled by **`OPTION_STEALTH_TLINE_BE_TRIGGER_MULT`** (floored in code).
- **ORB 0DTE overlays:** `OPTION_STEALTH_ORB_*` multipliers / profit-lock apply only when `strategy_type=orb_0dte` (shared engine).
- **Trendline 0DTE overlays:** `OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES` and `OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES` replace the global time/no-progress windows for **Trendline single-leg** positions when set **> 0**. **`OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT`:** while premium `pnl_pct` is at or above this, **time_exit** and **no_progress** are skipped so slow thesis grinds are not cut for being below the trailing gate. **`OPTION_STEALTH_TLINE_PROFIT_LOCK_*`:** when `pnl_pct` reaches the trigger, `breakeven_floor` is raised toward a small locked profit (`TRENDLINE_PIPELINE | stage=tline_profit_lock`). **`OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT`** scales the trailing **activation** threshold for Trendline singles.
- **Time / no-progress (global):** both apply only while PnL% is **below** the **effective** trailing threshold for that position (ORB and Trendline paths apply their own mults where configured).
- **Adverse guard:** optional early exit when underlying has moved against the option thesis and PnL remains weak—see `OPTION_STEALTH_ADVERSE_GUARD_*` keys above.
- **Fast-fail:** after `FAST_FAIL_MINUTES`, exits if PnL% is still **below** `FAST_FAIL_MIN_PNL_PCT` and the trade never saw a favorable move above entry.
- **Stale / jump:** `MAX_STALE_SECONDS` compares wall time since the **last stealth evaluation**; `PREMIUM_JUMP_RECHECK_PCT` flags large tick-to-tick premium moves; `FORCE_REEVAL_ON_PREMIUM_JUMP` adds `option_forced_reeval` logging on stale or jump events.

**Premium source priority (Trendline single-leg):**
- `option_mid` (bid/ask mid, preferred live source)
- `option_last` (when usable)
- `cache` (last valid quote under freshness window)
- `delta_estimate` (fallback proxy)

**Degraded/outage safety:** no forced exit while fallback data remains available. Forced exit only on true no-data outage when no live/fallback source is available past `OPTION_STEALTH_NO_DATA_OUTAGE_SECONDS`.

**Premium modeling:** The `delta_estimate` fallback uses underlying move, entry delta, and strike moneyness drift with jump clamps. Sensitivity control:

```bash
TRENDLINE_OPTION_SENSITIVITY=2.5
```

**Trendline-only fast open-position monitor** (does not change ORB ETF / ORB 0DTE / Trendline watch shared cadence):

```bash
TRENDLINE_POSITION_MONITOR_INTERVAL_SEC=7
```

- Runs only when there are open Trendline option positions.
- Calls only Trendline position monitoring (`_monitor_trendline_positions`) on this cadence.
- Uses a reentrancy guard + lock to prevent overlapping monitor runs between shared and fast loops.
- In each monitor tick, option chain fetches are deduped once per (`symbol`, `expiry_ymd`) and reused for matching positions.
- Fast-loop telemetry logs include `fast_monitor_tick`, `fast_monitor_reentrant_skip`, `chain_fetch` (cache hit/miss), and periodic `fast_monitor_metrics`.

**Fallback** if `trendline_options_stealth` is not initialized (legacy path only): fallback behavior remains in code, but `TRENDLINE_EXIT_TP_PCT` / `TRENDLINE_EXIT_SL_PCT` were removed from repo env cleanup because they were not actively referenced in the current Python path audit.

**Alerts / heartbeat** (Telegram monitor alert cadence only):
```bash
TRENDLINE_MONITOR_ALERT_ENABLED=false
TRENDLINE_MONITOR_ALERT_INTERVAL_SEC=300
```

**Open-position stealth cadence:** Trendline option exits run on dedicated `TRENDLINE_POSITION_MONITOR_INTERVAL_SEC` (default **7s**, aligned with `Shared.env` and `prime_trading_system` fallback) for open Trendline options, while `position_monitor_interval` remains the shared cadence for the rest of the app. This is independent of `TRENDLINE_MONITOR_ALERT_INTERVAL_SEC`.

**`TrendlineConfig` fields that still use dataclass defaults only** (not yet in `trendline_config_loader.py` unless you extend it): e.g. `chop_recent_bars`, `chop_max_crosses`, `chop_small_range_vs_orb_ratio`, `max_retrace_bars`, `require_volume_confirmation`, `volume_multiplier`, `first_break_only`, `second_break_attempt_max_bars`, `build_time_pt`, `expiration_time_pt`, **`use_retest_entry`** (not wired; overlap in spirit with **`post_continue_chop_box_gate`**). Most entry/momentum/continuation keys above **are** loaded from env—see [0DTETrendline.md](0DTETrendline.md) (section **Key Config Knobs**) for the full table.

**Reference doc**: [0DTETrendline.md](0DTETrendline.md) — module map, 7:30 **bars=1 + quote merge**, **chunked budgets**, ORB **extreme-bar** anchor times, post-continuation **settle** + **chop/box gate**, `build_context` / `build_summary` / `request_summary` / `build_degraded` / `build_bar_diagnostics`, setup tie-break, fast monitor, chain dedupe, premium resolution, delta-aware BE, **`OPTION_STEALTH_TLINE_*`**, fast-fail / adverse guard / stale-jump stealth, **Apr 30** selector-built **`source`** / **`TRENDLINE_SELECTOR_*`** logs, per-bar **`TRENDLINE_DECISION_SNAPSHOT`**, **`TRENDLINE_POST_BREAK_SURVIVAL_BARS`**, unified snapshots / canonical skip reasons / fallback & pressure diagnostics, calibration order.

**Deploy-visible defaults**: Add any **Apr 2026** keys you need to **`configs/Trendline0DTE.env`** or **`configs/Shared.env`** (engine falls back to code defaults if unset).


---

## 🛡️ **Risk Management Configuration**

**Location**: `configs/Risk.env` (position sizing + risk + slip; former `risk-management` / `position-sizing` slices merged May 2026)

### **Portfolio Risk Limits**

```bash
# Portfolio Risk
MAX_PORTFOLIO_RISK_PCT=80.0
MAX_SINGLE_POSITION_RISK_PCT=35.0
MAX_CORRELATED_POSITIONS=3

# Daily Risk Limits
MAX_DAILY_LOSS_PCT=5.0
MAX_DAILY_TRADES=200
MAX_DAILY_VOLUME_PCT=10.0

# Weekly Risk Limits
MAX_WEEKLY_LOSS_PCT=5.0
MAX_WEEKLY_LOSS_AMOUNT=5000.0
MAX_WEEKLY_TRADES=200

# Monthly Risk Limits
MAX_MONTHLY_LOSS_PCT=10.0
MAX_MONTHLY_LOSS_AMOUNT=20000.0
MAX_MONTHLY_TRADES=500
```

### **Position Risk Management**

```bash
# Stop Loss Settings
STOP_LOSS_ENABLED=true
STOP_LOSS_ATR_MULTIPLIER=1.5
STOP_LOSS_PCT=3.0

# Trailing Stop Settings
TRAILING_STOP_ENABLED=true
TRAILING_STOP_ATR_MULTIPLIER=2.0

# Take Profit Settings
TAKE_PROFIT_ENABLED=true
TAKE_PROFIT_ATR_MULTIPLIER=3.0
TAKE_PROFIT_PCT=5.0
```

### **Stealth Trailing Stop System** (65+ Configurable Settings)

**Optimized Parameters** (Rev 00196):
```bash
# Breakeven Settings
STEALTH_BREAKEVEN_THRESHOLD=0.0075  # 0.75% (optimized from 0.5%)
STEALTH_MIN_BREAKEVEN_ACTIVATION_MINUTES=6.4  # Optimized from 3.5 minutes
STEALTH_BREAKEVEN_OFFSET=0.002  # 0.2% offset above breakeven

# Trailing Stop Settings
STEALTH_BASE_TRAILING=0.015  # 1.5% base trailing distance
STEALTH_MIN_TRAILING=0.010  # 1.0% minimum trailing
STEALTH_MAX_TRAILING=0.025  # 2.5% maximum trailing
STEALTH_MIN_PROFIT_FOR_TRAILING=0.007  # 0.7% profit activates trailing (optimized from 0.5%)
STEALTH_MIN_TRAILING_ACTIVATION_MINUTES=6.4  # Optimized from 3.5 minutes

# Explosive Move Settings
STEALTH_EXPLOSIVE_TRAILING=0.040  # 4.0% for explosive moves
STEALTH_MOMENTUM_GAIN_THRESHOLD=0.003  # +0.3% in 15 min = explosive
STEALTH_MOMENTUM_LOOKBACK_MINUTES=15.0
STEALTH_TRENDING_TAKE_PROFIT=0.12  # 12% trending moves
```

**Volatility-Based Trailing Tiers**:
```bash
STEALTH_TRAILING_VOL_EXTREME=0.025   # 2.5% trailing for >6% volatility
STEALTH_TRAILING_VOL_HIGH=0.020      # 2.0% trailing for 3-6% volatility
STEALTH_TRAILING_VOL_MODERATE=0.0175 # 1.75% trailing for 2-3% volatility
STEALTH_TRAILING_VOL_LOW=0.015       # 1.5% trailing for <2% volatility
STEALTH_VOL_THRESHOLD_EXTREME=6.0
STEALTH_VOL_THRESHOLD_HIGH=3.0
STEALTH_VOL_THRESHOLD_MODERATE=2.0
```

**Profit-Based Trailing Tiers**:
```bash
STEALTH_TRAILING_PROFIT_MAX=0.015    # 1.5% trailing for 12%+ profit
STEALTH_TRAILING_PROFIT_HIGH=0.020   # 2.0% trailing for 7-12% profit
STEALTH_TRAILING_PROFIT_MEDIUM=0.025 # 2.5% trailing for 3-7% profit
STEALTH_TRAILING_PROFIT_LOW=0.030    # 3.0% trailing for <3% profit
```

**Portfolio Health Check Thresholds**:
```bash
STEALTH_HEALTH_CHECK_WIN_RATE_THRESHOLD=35.0  # <35% win rate = red flag
STEALTH_HEALTH_CHECK_AVG_PNL_THRESHOLD=-0.005 # <-0.5% avg P&L = red flag
STEALTH_HEALTH_CHECK_MOMENTUM_THRESHOLD=40.0  # <40% momentum = red flag
STEALTH_HEALTH_CHECK_PEAK_THRESHOLD=0.008     # <0.8% avg peak = red flag
```

**Rapid Exit Thresholds**:
```bash
STEALTH_RAPID_EXIT_NO_MOMENTUM_MINUTES=15.0  # 15 minutes
STEALTH_RAPID_EXIT_NO_MOMENTUM_PEAK=0.003     # <0.3% peak
STEALTH_RAPID_EXIT_IMMEDIATE_START=5.0        # 5 minutes
STEALTH_RAPID_EXIT_IMMEDIATE_END=10.0         # 10 minutes
STEALTH_RAPID_EXIT_IMMEDIATE_PNL=-0.005       # <-0.5% P&L
```

**Entry Bar Protection**:
```bash
STEALTH_ENTRY_BAR_PROTECTION_EXTREME=0.080  # 8% protection for >6% volatility
STEALTH_ENTRY_BAR_PROTECTION_HIGH=0.050     # 5% protection for 3-6% volatility
STEALTH_ENTRY_BAR_PROTECTION_MODERATE=0.030  # 3% protection for 2-3% volatility
STEALTH_ENTRY_BAR_PROTECTION_LOW=0.020      # 2% protection for <2% volatility
STEALTH_ENTRY_BAR_PROTECTION_MINUTES=30.0   # Protection duration
```

### **Holiday Filtering**

**Location**: `data/holidays_custom.json`

**Configuration**:
- **19 high-risk days per year** (10 bank holidays + 9 low-volume holidays)
- Prevents trading on holidays
- Automatic detection and alerting

**Settings**:
```bash
HOLIDAYS_ENABLED=true
HOLIDAYS_CUSTOM_PATH=data/holidays_custom.json
```

### **Red Day Filtering**

**Configuration**: Built into signal generation (Rev 00233)

**Settings**:
- **3-Pattern Detection**: Oversold, overbought, weak volume
- **3-Tier Override System**: Primary (MACD+RS), Secondary (Solo MACD), Tertiary (VWAP Distance)
- **Signal-Level Detection**: Two-layer protection (portfolio + signal level)
- **Direction-Aware** (0DTE): non-Tier-1 LONG rejected, Tier-1 LONG allowed, SHORT allowed on Red Days

---

## 💰 **Position Sizing Configuration**

**Location**: `configs/Risk.env` + path-specific caps in `configs/ORBSO.env`, `configs/ORB0DTE.env`, `configs/Trendline0DTE.env`

### **Base Position Sizing**

```bash
# Base Position Sizing
BASE_POSITION_SIZE_PCT=10.0  # 10% base position size
MAX_POSITION_SIZE_PCT=35.0   # Maximum position size after all boosts
MIN_POSITION_SIZE_PCT=1.0
MIN_POSITION_VALUE=50.0
```

### **Capital Allocation**

```bash
# Total Capital Allocation (CRITICAL: Must sum to 100%)
TOTAL_CAPITAL_ALLOCATION_PCT=90.0  # Total trading capital
CASH_RESERVE_PCT=10.0              # Cash reserve (must = 100% - TOTAL_CAPITAL_ALLOCATION_PCT)

# Strategy Allocation (must sum to TOTAL_CAPITAL_ALLOCATION_PCT)
SO_CAPITAL_PCT=90.0   # Standard Order allocation
ORR_CAPITAL_PCT=0.0   # Opening Range Reversal (disabled)
```

**Example Scenarios**:
- **Current**: 90% trading, 10% reserve → `TOTAL_CAPITAL_ALLOCATION_PCT=90.0`, `CASH_RESERVE_PCT=10.0`
- **Enable ORR**: 70% SO, 20% ORR, 10% reserve → `SO_CAPITAL_PCT=70.0`, `ORR_CAPITAL_PCT=20.0`
- **More Conservative**: 80% trading, 20% reserve → `TOTAL_CAPITAL_ALLOCATION_PCT=80.0`, `CASH_RESERVE_PCT=20.0`

### **Confidence-Based Boosting**

```bash
# Confidence Thresholds
ULTRA_HIGH_CONFIDENCE_THRESHOLD=0.95  # Lowered from 0.995 for more opportunities
HIGH_CONFIDENCE_THRESHOLD=0.90        # Lowered from 0.95
MEDIUM_CONFIDENCE_THRESHOLD=0.85      # Lowered from 0.90

# Confidence Multipliers
ULTRA_HIGH_CONFIDENCE_MULTIPLIER=2.5  # Increased from 1.5x
HIGH_CONFIDENCE_MULTIPLIER=2.0        # Increased from 1.2x
MEDIUM_CONFIDENCE_MULTIPLIER=1.0
```

### **Profit-Based Scaling**

```bash
# Profit Scaling Multipliers
PROFIT_SCALING_50_PCT_MULTIPLIER=2.5   # Moon moves (50%+): 2.5x boost
PROFIT_SCALING_25_PCT_MULTIPLIER=2.5   # Explosive moves (25%+): 2.5x boost
PROFIT_SCALING_12_PCT_MULTIPLIER=1.8   # Trending moves (12%+): 1.8x boost
PROFIT_SCALING_5_PCT_MULTIPLIER=1.0    # Base moves (5%+): 1.0x (no boost)
```

### **Position Limits**

Path-specific caps are owned by **`configs/ORBSO.env`** (`MAX_CONCURRENT_TRADES`), **`configs/ORB0DTE.env`** (`0DTE_MAX_POSITIONS`), **`configs/Trendline0DTE.env`** (`TRENDLINE_MAX_OPEN_POSITIONS`), and **`configs/Shared.env`** (`MAX_TOTAL_OPTION_POSITIONS` — combined options book). Generic ceilings live in **`configs/Risk.env`** (former `position-sizing` / `trading-parameters` slices merged May 2026).

```bash
# ORB SO ETF @ 7:30 — canonical in ORBSO.env (greedy divisor + max simultaneous SO batch)
MAX_CONCURRENT_TRADES=15

# Legacy: prime_models.TradingConfig still reads os.getenv("MAX_CONCURRENT_POSITIONS", "15") — keep aligned with SO intent or remove after code consolidation
MAX_CONCURRENT_POSITIONS=15

# Portfolio-wide tracked-position ceiling (configs/Risk.env; advanced/quantum: strategy_mode_presets)
MAX_OPEN_POSITIONS=26

POSITION_SPLITTING_ENABLED=true
POSITION_SPLITTING_METHOD=even
POSITION_REBALANCING_ENABLED=true

# Risk Controls
MAX_POSITION_RISK_PCT=35.0
POSITION_RISK_VALIDATION=true
OVERRIDE_RISK_LIMITS=false
```

---

## 🔧 **Broker Configuration**

**Location**: `configs/Data.env` (+ `secretsprivate/` locally / Secret Manager in production)

### **Supported Brokers**

The Easy ORB Strategy supports multiple brokers for US equities and options trading:

| Broker | Status | Notes |
|--------|--------|-------|
| **E*TRADE** | ✅ **Fully Developed** | Default broker, production-ready, fully tested |
| **Interactive Brokers (IB)** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **Robinhood** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **Alpaca** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **TastyTrade** | 🟡 Ready for Implementation | Architecture ready, requires API integration |
| **Tradier** | 🟡 Ready for Implementation | Architecture ready, requires API integration |

**Current Implementation**: E*TRADE is fully developed, tested, and production-ready. The system architecture supports multi-broker configuration, but other brokers require broker-specific API integration.

```bash
# Primary Broker
BROKER_TYPE=etrade  # etrade (default), ib, robinhood, alpaca, tastytrade, tradier

# Strategy-Specific Overrides (optional)
# Each strategy can use a different broker if needed
# ORB_BROKER_TYPE=etrade
# 0DTE_BROKER_TYPE=etrade
```

### **E*TRADE Configuration** ⭐ **Fully Developed**

**Trading Modes**:
- **Demo Mode**: Simulated trading only. **ORB** uses a **$1,000** mock sim balance; **0DTE** uses a **$5,000** mock sim balance (no real money). Configure E*TRADE Sim account ID for API calls (quotes, options chain); if the ID is invalid, the app uses the auto-selected Sim account.
- **Live Mode**: Real trading on the configured broker live account. ORB and 0DTE use account IDs from merged config (`Data.env` + secrets layer) for real orders and balance.

**Important**: E*TRADE does not have separate sandbox/production APIs. Both Demo and Live use the **production E*TRADE API**. Demo = Sim account + mock balances ($1k ORB, $5k 0DTE). Live = live account, real money.

```bash
# Account IDs (see PrivateSecrets.md for actual values)
# These are your actual E*TRADE account IDs - store in PrivateSecrets.md
ETRADE_ORB_ACCOUNT_ID=your_orb_account_id_here      # ORB Strategy account
ETRADE_0DTE_ACCOUNT_ID=your_0dte_account_id_here    # 0DTE Strategy account
ETRADE_DEFAULT_ACCOUNT_ID=your_default_account_id   # Default account (fallback)

# Account Names (optional, for reference only - not used by system)
ETRADE_ORB_ACCOUNT_NAME=Your Account Name
ETRADE_0DTE_ACCOUNT_NAME=Your Account Name

# API Settings
ETRADE_ENABLED=true
ETRADE_REAL_TIME_QUOTES=true
ETRADE_BATCH_QUOTES=true
ETRADE_DAILY_CALL_LIMIT=1180
ETRADE_RATE_LIMIT_CALLS_PER_MINUTE=100
```

**Account Selection Logic**:
1. Check strategy-specific account (e.g., `ETRADE_ORB_ACCOUNT_ID`)
2. If not set, check default account (e.g., `ETRADE_DEFAULT_ACCOUNT_ID`)
3. If not set, auto-select first available active account

**Account Configuration**:
- **ORB Strategy Account**: Separate account for ETF/stock trading (see PrivateSecrets.md)
- **0DTE Strategy Account**: Separate account for options trading (see PrivateSecrets.md)
- **Default Account**: Fallback account if strategy-specific not set (see PrivateSecrets.md)

**Broker Switching**:
1. Set `BROKER_TYPE` to new broker (e.g., `ib`, `robinhood`, `alpaca`, `tastytrade`, `tradier`)
2. Update broker-specific account IDs in `configs/Data.env` and secrets (`secretsprivate/` local or Secret Manager production)
3. Ensure broker credentials are configured (API keys, OAuth tokens) in Secret Manager
4. Update broker-specific API settings in `configs/Data.env`
5. Restart the system

**Note**: Currently only E*TRADE is fully developed and tested. Other brokers require broker-specific API integration.

### **Interactive Brokers Configuration** (Ready for Implementation)

```bash
# IB - ORB Strategy Account (see PrivateSecrets.md for actual values)
# IB_ORB_ACCOUNT_ID=your_ib_orb_account_id
# IB_ORB_ACCOUNT_NAME=Your Account Name

# IB - 0DTE Strategy Account (see PrivateSecrets.md for actual values)
# IB_0DTE_ACCOUNT_ID=your_ib_0dte_account_id
# IB_0DTE_ACCOUNT_NAME=Your Account Name

# IB API Settings
IB_ENABLED=false
IB_ENVIRONMENT=paper  # paper (demo) or live
```

### **Robinhood Configuration** (Ready for Implementation)

```bash
# Robinhood - ORB Strategy Account (see PrivateSecrets.md for actual values)
# RH_ORB_ACCOUNT_ID=your_rh_orb_account_id
# RH_ORB_ACCOUNT_NAME=Your Account Name

# Robinhood - 0DTE Strategy Account (see PrivateSecrets.md for actual values)
# RH_0DTE_ACCOUNT_ID=your_rh_0dte_account_id
# RH_0DTE_ACCOUNT_NAME=Your Account Name

# Robinhood API Settings
ROBINHOOD_ENABLED=false
ROBINHOOD_ENVIRONMENT=demo  # demo or live
```

### **Alpaca Configuration** (Ready for Implementation)

```bash
# Alpaca - ORB Strategy Account (see PrivateSecrets.md for actual values)
# ALPACA_ORB_ACCOUNT_ID=your_alpaca_orb_account_id
# ALPACA_ORB_ACCOUNT_NAME=Your Account Name

# Alpaca - 0DTE Strategy Account (see PrivateSecrets.md for actual values)
# ALPACA_0DTE_ACCOUNT_ID=your_alpaca_0dte_account_id
# ALPACA_0DTE_ACCOUNT_NAME=Your Account Name

# Alpaca API Settings
ALPACA_ENABLED=false
ALPACA_ENVIRONMENT=demo  # demo or live
```

### **TastyTrade Configuration** (Ready for Implementation)

```bash
# TastyTrade - ORB Strategy Account (see PrivateSecrets.md for actual values)
# TASTYTRADE_ORB_ACCOUNT_ID=your_tastytrade_orb_account_id
# TASTYTRADE_ORB_ACCOUNT_NAME=Your Account Name

# TastyTrade - 0DTE Strategy Account (see PrivateSecrets.md for actual values)
# TASTYTRADE_0DTE_ACCOUNT_ID=your_tastytrade_0dte_account_id
# TASTYTRADE_0DTE_ACCOUNT_NAME=Your Account Name

# TastyTrade API Settings
TASTYTRADE_ENABLED=false
TASTYTRADE_ENVIRONMENT=demo  # demo or live
```

### **Tradier Configuration** (Ready for Implementation)

```bash
# Tradier - ORB Strategy Account (see PrivateSecrets.md for actual values)
# TRADIER_ORB_ACCOUNT_ID=your_tradier_orb_account_id
# TRADIER_ORB_ACCOUNT_NAME=Your Account Name

# Tradier - 0DTE Strategy Account (see PrivateSecrets.md for actual values)
# TRADIER_0DTE_ACCOUNT_ID=your_tradier_0dte_account_id
# TRADIER_0DTE_ACCOUNT_NAME=Your Account Name

# Tradier API Settings
TRADIER_ENABLED=false
TRADIER_ENVIRONMENT=demo  # demo or live
```

---

## 📱 **Alert Configuration**

**Location**: `configs/Alerts.env`

### **Telegram Configuration**

```bash
# Enable Telegram Alerts
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here  # See PrivateSecrets.md
TELEGRAM_CHAT_ID=your_telegram_chat_id_here      # See PrivateSecrets.md

# Rate Limiting
TELEGRAM_MAX_MESSAGES_PER_MINUTE=20
TELEGRAM_RATE_LIMIT_ENABLED=true
TELEGRAM_ALERT_COOLDOWN_SECONDS=60

# Alert Types
TELEGRAM_ALERT_TYPES=entry,exit,error,performance,daily_summary,system_status
```

**Setup**: See [Alerts.md](Alerts.md) for complete Telegram setup guide.

### **Alert Types**

```bash
# Trading Alerts
ALERT_ON_SIGNAL_GENERATION=true
ALERT_ON_POSITION_OPENING=true
ALERT_ON_POSITION_CLOSING=true
ALERT_ON_STOP_LOSS_HIT=true
ALERT_ON_TAKE_PROFIT_HIT=true

# Performance Alerts
ALERT_ON_DAILY_PNL=true
ALERT_ON_WEEKLY_PNL=true
ALERT_ON_MONTHLY_PNL=true
ALERT_ON_TRADE_SUMMARY=true
ALERT_ON_PERFORMANCE_MILESTONES=true

# System Alerts
ALERT_ON_ERRORS=true
ALERT_ON_SYSTEM_STATUS=true
ALERT_ON_DATA_FAILURES=true
ALERT_ON_API_LIMITS=true
```

### **Alert Thresholds**

```bash
# Performance Thresholds
DAILY_PNL_ALERT_THRESHOLD_PCT=5.0
WEEKLY_PNL_ALERT_THRESHOLD_PCT=10.0
MONTHLY_PNL_ALERT_THRESHOLD_PCT=20.0
LOSS_THRESHOLD_ALERT_PCT=-5.0
GAIN_THRESHOLD_ALERT_PCT=10.0
DRAWDOWN_THRESHOLD_ALERT_PCT=5.0
```

### **Alert Timing**

```bash
# Alert Timing
ALERT_TIMEZONE=America/New_York
DAILY_ALERT_TIME=16:05  # 4:05 PM ET
WEEKLY_ALERT_TIME=16:00
MONTHLY_ALERT_TIME=16:00
ALERT_START_TIME=09:30
```

---

## 🚀 **Deployment Configuration**

**Location**: `configs/Data.env` and `configs/Shared.env` (GCP/container keys and deployment-oriented toggles)

### **Deployment Mode**

**Trading Modes**:
- **Demo Mode**: Simulated trading with demo/simulated account
  - Uses production E*TRADE API
  - Trades executed in demo/simulated account
  - No real money at risk
  - Perfect for testing and development
- **Live Mode**: Real trading with live account
  - Uses production E*TRADE API
  - Trades executed in live trading account
  - Real money at risk
  - Production trading

**Important**: Both Demo and Live modes use the **production E*TRADE API**. The difference is which account is used (demo account vs live account). E*TRADE does not have separate sandbox/production APIs.

```bash
# Deployment Mode
DEPLOYMENT_MODE=demo  # demo or live
AUTOMATION_ENABLED=true

# Trading Mode
ETRADE_MODE=demo  # demo or live (matches DEPLOYMENT_MODE)
# Note: Both demo and live use production E*TRADE API
# Difference is which account is used (demo account vs live account)
```

### **Strategy Enablement**

```bash
# Strategy Enablement
ENABLE_ORB_STRATEGY=true   # Always enabled
ENABLE_0DTE_STRATEGY=true  # Enable 0DTE Strategy (optional)
```

### **Google Cloud Configuration**

```bash
# GCP Project Settings (see PrivateSecrets.md for actual values)
GCP_PROJECT_ID=your-project-id
GCP_REGION=us-central1
GCP_ZONE=us-central1-a
GCP_SERVICE_NAME=your-service-name  # See PrivateSecrets.md for actual value
GCS_BUCKET_NAME=your-project-id-data

# Container Configuration
CONTAINER_CPU=2
CONTAINER_MEMORY=4Gi
CONTAINER_MAX_INSTANCES=10
CONTAINER_MIN_INSTANCES=0  # Scale-to-zero enabled
CONTAINER_TIMEOUT=3600s
```

### **Feature Flags**

```bash
# Feature Flags
ENABLE_ALERT_MANAGER=true
ENABLE_PRIME_SYSTEM=true
ENABLE_RISK_MANAGEMENT=true
ENABLE_PERFORMANCE_TRACKING=true
ENABLE_AUTO_TRADING=true

# Deployment Safety
DEPLOYMENT_SAFETY_CHECKS=true
PRE_DEPLOYMENT_TESTS=true
POST_DEPLOYMENT_VALIDATION=true
ROLLBACK_ENABLED=true
EMERGENCY_STOP_ENABLED=true
```

---

## 🔐 **Secrets Management**

**Location**: `secretsprivate/` (local) or Google Secret Manager (production)

### **Two-Tier Secrets Management System**

**Production/Deployment**: Google Secret Manager (GCP)  
**Local Development**: `secretsprivate/` folder (gitignored)

### **⚠️ CRITICAL: Secret Manager Cost Optimization**

**Secret Manager charges $0.06 per version per month.** To prevent excessive costs:

1. **Automatic Cleanup**: ✅ **DEPLOYED** (February 9, 2026)
   - **Firebase OAuth App**: `oauth_backend.py` automatically deletes old secret versions when tokens are renewed via web app
   - **Cloud Run Services**: `secret_manager_oauth.py` also includes cleanup (for direct token storage)
   - Both systems keep only the latest version (1 per secret)

2. **Best Practice**: Each secret should have only **1 version** (the latest). Monitor version counts monthly:
   ```bash
   for SECRET in etrade-oauth-prod; do
     COUNT=$(gcloud secrets versions list $SECRET --format='value(name)' | wc -l)
     echo "$SECRET: $COUNT versions (should be 1)"
   done
   ```

3. **Manual Cleanup**: If old versions accumulate, run:
   ```bash
   cd "0. Strategies and Automations/1. The Easy ORB Strategy"
   ./scripts/cleanup_secrets_optimized.sh
   ```

**Current Cost**: ~$1.20/month (20 billable versions across all projects × $0.06)  
**Expected Cost**: ~$0.78/month (13 secrets in easy-etrade-strategy × 1 version × $0.06)  
**Without Cleanup**: Could exceed $200/month if thousands of versions accumulate

**Implementation**: 
- **Firebase OAuth**: The `write_secret()` function in `ETradeOAuth/login/oauth_backend.py` automatically calls `_cleanup_old_secret_versions()` after creating a new secret version
- **Cloud Run**: The `store_tokens()` method in `ETradeOAuth/login/secret_manager_oauth.py` automatically calls `_cleanup_old_versions()` after creating a new secret version
- Both ensure only the latest version is kept

**All sensitive credentials are stored securely and never committed to Git.**

### **Local Development Setup**

1. **Create secrets files**:
   ```bash
   cp secretsprivate/etrade.env.template secretsprivate/etrade.env
   cp secretsprivate/telegram.env.template secretsprivate/telegram.env
   ```

2. **Fill in credentials**:
   - Edit `secretsprivate/etrade.env` with your E*TRADE keys/secrets
   - Edit `secretsprivate/telegram.env` with your Telegram bot token/chat ID

3. **Automatic loading**: `modules/config_loader.py` automatically loads from `secretsprivate/` when `ENVIRONMENT=development`

### **Production Deployment**

**Location**: Google Secret Manager  
**Access**: Via service account with Secret Manager permissions

**Secret Names** (see PrivateSecrets.md for actual values):
- `etrade-prod-consumer-key` (Production consumer key)
- `etrade-prod-consumer-secret` (Production consumer secret)
- `etrade-oauth-prod` (Production OAuth tokens - JSON format)
- `telegram-bot-token` (Telegram bot token)
- `telegram-chat-id` (Telegram chat ID)

**Note**: Both Demo and Live modes use the same production E*TRADE API credentials. The difference is which account is used (demo account vs live account). OAuth tokens are shared between demo and live modes since they use the same API.

**Automatic loading**: `modules/config_loader.py` automatically loads from Secret Manager when `ENVIRONMENT=production`

### **Security Best Practices**

- ✅ Store production secrets in Google Secret Manager
- ✅ Use `secretsprivate/` for local development only
- ✅ Keep template files (`.template`) in Git
- ✅ Never commit `secretsprivate/` folder to Git
- ✅ Never hardcode secrets in config files
- ✅ Rotate credentials regularly

---

## 🌍 **Environment Variables**

### **Runtime Environment Variables**

These can override configuration file settings:

```bash
# Trading Mode
ENVIRONMENT=production  # development or production
STRATEGY_MODE=standard  # standard, advanced, quantum
ETRADE_MODE=demo        # demo or live
SYSTEM_MODE=full_trading  # signal_only, scanner_only, full_trading, alert_only
CLOUD_MODE=true

# Strategy Enablement
ENABLE_ORB_STRATEGY=true
ENABLE_0DTE_STRATEGY=true

# Broker Configuration
BROKER_TYPE=etrade
BROKER_DATA_ONLY=true

# Logging
LOG_LEVEL=INFO
```

### **Cloud Run Environment Variables**

Set via `gcloud run deploy`:

```bash
# Replace YOUR_SERVICE_NAME with your actual service name (see PrivateSecrets.md)
gcloud run deploy YOUR_SERVICE_NAME \
    --set-env-vars="ENVIRONMENT=production,STRATEGY_MODE=standard,ETRADE_MODE=demo,SYSTEM_MODE=full_trading,CLOUD_MODE=true,ENABLE_0DTE_STRATEGY=true,LOG_LEVEL=INFO"
```

---

## 📝 **Configuration Best Practices**

### **1. Use Configuration Files**

- ✅ Store all settings in `configs/*.env` files
- ✅ For secrets, use `secretsprivate/*.env.template` as starting points
- ✅ Never hardcode values in code

### **2. Secrets Management**

- ✅ Store secrets in Secret Manager (production) or `secretsprivate/` (local)
- ✅ Never commit secrets to Git
- ✅ Use template files for documentation

### **3. Version Control**

- ✅ Commit only safe template files (for example `secretsprivate/*.env.template`)
- ✅ Gitignore actual config files with secrets
- ✅ Document configuration changes

### **4. Testing**

- ✅ Test configuration changes in demo mode first
- ✅ Validate configuration before deploying
- ✅ Use deployment safety checks

### **5. Documentation**

- ✅ Document all configuration changes
- ✅ Keep configuration files organized
- ✅ Use clear naming conventions

---

## 🔄 **Configuration Validation**

### **Capital Allocation Validation**

**Rules**:
- `SO_CAPITAL_PCT + ORR_CAPITAL_PCT + CASH_RESERVE_PCT = 100%`
- `SO_CAPITAL_PCT + ORR_CAPITAL_PCT = TOTAL_CAPITAL_ALLOCATION_PCT`

**Validation**:
```bash
# System automatically validates on startup
# Errors logged if validation fails
```

### **Account Selection Validation**

**Rules**:
- Strategy-specific account IDs must be valid E*TRADE account IDs
- Default account ID must be valid if strategy-specific not set
- System validates account IDs on startup

### **Risk Limits Validation**

**Rules**:
- `MAX_POSITION_SIZE_PCT <= MAX_POSITION_RISK_PCT`
- `BASE_POSITION_SIZE_PCT <= MAX_POSITION_SIZE_PCT`
- Daily/weekly/monthly limits must be consistent

### **Runtime Config Visibility Logs** (Apr 30 cleanup pass)

`modules/config_loader.py` now emits passive (non-blocking) config hygiene logs:

- `CONFIG_MISSING_KEY | key=<name> | fallback_used=true`
  - emitted when `get_config_value(...)` falls back to default because a key is absent from merged env
  - also emitted via passive `os.getenv` hook for config-like keys not present in runtime `os.environ`
- `CONFIG_DUPLICATE_KEY | key=<name> | sources=[...]`
  - emitted when the same key appears in multiple env files during merge

These are visibility-only logs; they do not change strategy behavior or startup flow.

---

## 🛠️ **Troubleshooting**

### **Configuration Not Loading**

**Issue**: Settings not taking effect
- ✅ Check configuration file path
- ✅ Verify environment variable overrides
- ✅ Check Secret Manager permissions (production)
- ✅ Review logs for configuration errors

### **Capital Allocation Errors**

**Issue**: Capital allocation doesn't sum to 100%
- ✅ Verify `SO_CAPITAL_PCT + ORR_CAPITAL_PCT + CASH_RESERVE_PCT = 100%`
- ✅ Check `TOTAL_CAPITAL_ALLOCATION_PCT` matches strategy allocation
- ✅ Review `configs/ORBSO.env`, `configs/ORB0DTE.env`, `configs/Trendline0DTE.env`, `configs/Shared.env`, and `configs/Risk.env`

### **Account Selection Issues**

**Issue**: Wrong account being used
- ✅ Check merged broker config (`configs/Data.env`) and secrets layer for account IDs
- ✅ Verify account IDs are correct (see PrivateSecrets.md)
- ✅ Check account selection priority logic

### **Secrets Not Loading**

**Issue**: Secrets not found in production
- ✅ Verify Secret Manager secrets exist
- ✅ Check service account permissions
- ✅ Verify secret names match configuration
- ✅ Review Secret Manager access logs

---

## 📚 **Additional Resources**

- **Alert System**: See [Alerts.md](Alerts.md) for alert configuration
- **OAuth System**: See [OAuth.md](OAuth.md) for OAuth token management
- **Cloud Deployment**: See [Cloud.md](Cloud.md) for cloud configuration
- **Strategy Details**: See [Strategy.md](Strategy.md) for strategy configuration
- **Sensitive Information**: See [PrivateSecrets.md](PrivateSecrets.md) for deployment-specific values

---

## 📝 **Revision History**

### **April 30, 2026 — Trendline docs + settings alignment**

- Easy Trendline: **selector-built** production path and snapshot **`source`** values; **`TRENDLINE_SELECTOR_*`** / **`use_selector_built`** log tokens replace deprecated **`TRENDLINE_PREBUILT_*`** names in code; **`TRENDLINE_POST_BREAK_SURVIVAL_BARS`**, per-bar **`TRENDLINE_DECISION_SNAPSHOT`**, **`TRENDLINE_MISSED_WIN_EARLY`**, flow/checkpoint logs documented in [0DTETrendline.md](0DTETrendline.md) and Easy Trendline section above.

### **May 13, 2026 — ORB 0DTE + ORB SO ETF operational alignment**

- ORB 0DTE: pre-queue / skip-stage logging, spot fallback, viability default **0.30** (`orb0dte_execution_defaults.py`), chain relax **0.76**, selector momentum + **`0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS`**, lifecycle / EOD flatten telemetry — see [0DTEORB.md](0DTEORB.md).
- ORB SO ETF: startup stealth rehydrate from mock (Demo) or E*TRADE portfolio (Live); **`PrimeUnifiedTradeManager.close_position`** for broker-only closes; **`ExitReason.MANUAL`** for shutdown — see May 13 session summary.
- Watchlists: **RGC** and **CGON** removed from `core_list` / `0dte_list` (strategy + collector).

### **May 6, 2026 — ORB 0DTE settings accuracy refresh**

- Updated ORB 0DTE debit-spread delta defaults to match `configs/ORB0DTE.env` (`0DTE_DEBIT_SPREAD_TARGET_DELTA_MIN=0.30`, `0DTE_DEBIT_SPREAD_TARGET_DELTA_MAX=0.45`).
- Added current reliability note alignment for ORB 0DTE runtime behavior (Convex min-score helper read + Cloud Run-first watchlist path handling).

### **Latest Updates (January 22, 2026 - Rev 00259)**

**Rev 00259 (Jan 22 - Cloud Cleanup Automation)**:
- ✅ Comprehensive settings documentation
- ✅ Complete configuration file reference
- ✅ All settings organized by category
- ✅ Sensitive information moved to PrivateSecrets.md

### **Previous Updates**

**Rev 00247 (Jan 20 - Critical Bug Fixes)**:
- ✅ ETrade API Batch Limit Fix
- ✅ 0DTE Import Path Fix
- ✅ Deployment Configuration fixes

**Rev 00246 (Jan 19 - 0DTE Strategy Improvements)**:
- ✅ 0DTE Priority Score Formula v1.1
- ✅ Direction-Aware Red Day Filtering
- ✅ Expanded Delta Selection (0.15-0.35)

**Rev 00245 (Jan 19 - Broker Configuration System)**:
- ✅ Multi-Broker Architecture
- ✅ Account Mapping (ORB and 0DTE)
- ✅ Broker Config Manager

**Rev 00233 (Jan 8 - Secrets Management)**:
- ✅ Two-tier secrets management system
- ✅ All sensitive credentials moved to `secretsprivate/`
- ✅ Config files cleaned (no hardcoded secrets)

---

**Settings and Configuration Guide - Complete and Ready for Use!** 🚀

*Last Updated: May 15, 2026*  
*Version: Rev 00350+ baseline; **May 15** execution env + Trendline calibration + SO fixes (deploy pending); **May 14** Rev 00348 SO / 0DTE ranking keys + **`ORB_0DTE_SELECTOR_FULL_REPLAY`** grep; May 13 ORB 0DTE + ORB SO ETF ops alignment; Apr 30 Trendline diagnostics env documentation*  
*Maintainer: Easy ORB Strategy Development Team*
