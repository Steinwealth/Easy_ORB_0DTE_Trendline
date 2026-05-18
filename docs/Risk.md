# 🛡️ Risk Management & Dynamic Position Sizing - Easy ORB Strategy

**Last Updated**: May 15, 2026  
**Version**: **Rev 00351** (ORB SO ranking refinement: continuation-quality layer, soft exhaustion/extension/deceleration penalties, reduced ORB-range overweighting, `SO_RANK_BREAKDOWN` observability). **May 15, 2026:** SO ranking **`json` closure** fix (`__import__("json").dumps` in `calculate_so_priority_score`); **`_process_orb_signals` → bool** — batch dedupe arms only on success (**`SO_EXECUTION_FAILED | batch_dedupe_not_armed`** on failure); execution policy layer (ORB SO equity smart exits when `USE_MARKET_ORDERS=false`). Production deploy pending — [May 15 session](doc_elements/Sessions/2026/May15%20Session/SESSION_SUMMARY_MAY15_2026.md). **May 14, 2026:** Doc alignment for **Rev 00348** — ORB SO priority row and summary now describe **continuation-first** base scoring (`SO_CONTINUATION_MOMENTUM_WEIGHT` + remainder blend in `calculate_so_priority_score`), not legacy fixed v2.1-only weights; ORB 0DTE priority row matches **`0DTE_PRIORITY_RANK_W_*`** defaults (**0.14 / 0.10 / 0.22 / 0.20 / 0.34**) + extension penalty + **`early_momentum`** tie-break. Cloud grep: **`ORB_0DTE_SELECTOR_FULL_REPLAY`**. **May 11:** ORB 0DTE **overextension** scoring (**`ORB_0DTE_OVEREXTENSION_*`**, legacy **`0DTE_EXTENSION_THRESHOLD_PCT`** → **`ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD`** alias logs), **chain-health fallback ladder**, **two-stage durability** / lifecycle **`ORB_0DTE_*`** tokens, and **ORB spread degraded-mark timeout relief** (**`ORB_OPTIONS_SPREAD_*`**, **`ORB_SPREAD_*`**; **`ORB_OPTIONS_EXIT_DEFERRED_AUDIT`**, **`ORB_OPTIONS_FORCED_DEGRADED_EXIT`**) — tune risk vs realism under partial quotes. Prior Rev 00349/00347/00328/00319/00318/00312 updates remain active.  
**Purpose**: Complete documentation of the multi-layered risk management system with unified configuration, optimized exit settings, and proven performance metrics.

**May 4, 2026 (broker/API cadence):** Open ORB 0DTE and Trendline 0DTE option positions use dedicated fast-monitor loops with a **7-second** configured baseline (`ORB_0DTE_POSITION_MONITOR_INTERVAL_SEC`, `TRENDLINE_POSITION_MONITOR_INTERVAL_SEC`). Intervals can **increase** under dynamic throttling, quote latency backoff, or hard-cap logic—see `docs/0DTEORB.md` and `docs/0DTETrendline.md`.

---

## Overview

The Easy ETrade Strategy implements a proven, multi-layered risk management system that ensures capital preservation while maximizing profit potential through greedy capital packing and ultra aggressive confidence boosting.

**Proven Performance (Rev 00196 - Data-Driven Exit Optimization):**
- ✅ **+73.69% weekly return** (validated on 11 days of historical data)
- ✅ **91% winning day consistency** (10/11 days profitable)
- ✅ **Max drawdown reduced 96%** (-21.68% → -0.84%)
- ✅ **Profit factor: 194.00** (vs 2.03 baseline)
- ✅ **Capital deployment: 88-90%** (post-rounding redistribution, Rev 00090)
- ✅ **Exit Settings Optimized** (Rev 00196: 0.75% breakeven, 0.7% trailing, 6.4 min activation)
- ✅ **Expected 85-90% profit capture** (vs 67% current - +18-23% improvement)
- ✅ **Batch position sizing** (6-step flow, Rev 00084-00090)
- ✅ **Unified configuration** (seven canonical `configs/*.env` files merged by `config_loader.py`; see `configs/README.md`)
- ✅ **All exit triggers working** (14/14 functional and verified)
- ✅ **100% timezone correctness** (34 bugs fixed, DST-aware, Rev 00075)
- ✅ **Holiday filter** (19 days/year skipped, Rev 00137)
- ✅ **GCS Persistence** (Trade history persists across deployments - Rev 00203)

---

## 🔐 Core Risk Management Principles

### 1. Capital Allocation Rule (Rev 00103 - UNIFIED SYSTEM) ⭐

**SINGLE SOURCE OF TRUTH (allocation %):** `configs/ORBSO.env` — `SO_CAPITAL_PCT`, `ORR_CAPITAL_PCT`, `CASH_RESERVE_PCT` block (validated: SO + ORR + Reserve = **100%**).

**Configurable Allocation - ORB Strategy:**
- `SO_CAPITAL_PCT` = 90.0 (Standard Order allocation - adjustable to 80%, 85%, etc.)
- `ORR_CAPITAL_PCT` = 0.0 (Opening Range Reversal - currently disabled)
- `CASH_RESERVE_PCT` = 10.0 (Cash reserve - auto-calculated as 100% − SO − ORR)
- `MAX_POSITION_SIZE_PCT` = 35.0 (maximum single-position size cap — **`configs/Risk.env`** merge; not the SO “15 trades” knob)

**ORB SO ETF concurrency (7:30 PT, separate from options):** `MAX_CONCURRENT_TRADES` in **`configs/ORBSO.env`** (default **15**) — maximum simultaneous **SO ETF/stock** executions in the 7:30 batch and the **greedy sizing divisor** in `prime_risk_manager` / `prime_demo_risk_manager`. **Not** `0DTE_MAX_POSITIONS` and **not** Trendline’s `TRENDLINE_MAX_OPEN_POSITIONS`.

**Legacy alias:** `MAX_CONCURRENT_POSITIONS` in **`configs/Risk.env`** is still read by **`prime_models.TradingConfig`** (`os.getenv`). Keep it aligned with SO intent (**15**) or consolidate code on `MAX_CONCURRENT_TRADES` only.

**Path-specific option caps:** `0DTE_MAX_POSITIONS` = **6** in **`configs/ORB0DTE.env`** (ORB 0DTE at 7:30); `TRENDLINE_MAX_OPEN_POSITIONS` = **5** in **`configs/Trendline0DTE.env`** (rolling Trendline); `MAX_TOTAL_OPTION_POSITIONS` = **11** in **`configs/Shared.env`** (combined open ORB 0DTE + Trendline options).

**Portfolio-wide position count ceiling:** `MAX_OPEN_POSITIONS` — default **26** in **`configs/Risk.env`** so tracked positions across SO + 0DTE + Trendline do not hit `_check_position_limits` before the path-specific gates above.

**Automatic Validation**: System validates SO + ORR + Reserve = 100% on startup.

---

### **Signal Collection → Order Execution (End-to-End)** ⭐

When **20 (or any number of) SO signals are confirmed**, the system runs these steps in order. This flow is implemented in `prime_trading_system` (selection/ranking) and `prime_risk_manager` / `prime_demo_risk_manager` (batch sizing). See also `docs/SignalCollectionToExecution.md`.

| # | Step | Where | Risk / config |
|---|------|--------|----------------|
| 1 | **Signal collection** | ORB + 7:00–7:15 validation candle (broker-only). Explicit 7:15 close passed into rules when prefetched (Rev 00279); validation candle persisted to GCS and loaded if scan runs on another instance. **Rev 00285–00287:** The confirmed SO + 0DTE list is persisted to GCS when the scan completes. **Rev 00293:** Merge-on-persist — scans merge new signals with existing GCS content (union by symbol); prevents 0-signal scans from overwriting valid signals when multiple Cloud Run instances run in parallel. **Rev 00293+:** At 7:30 execution, the instance **always loads from GCS** when enabled (latest merged state from all instances); no longer only when in-memory is empty. **Rev 00330:** At 7:30, a final breakout revalidation using fresh quotes keeps only symbols where `current_price_now >= orb_high * 1.001` (+0.1% buffer) before ranking/risk/execution. Only current-day lists are used (stale discarded; GCS payload used only when date = today). Each signal's orb_data is serialized to a dict so risk and 0DTE have orb_high/orb_low after load. **Rev 00289:** Signal append fixed. **Rev 00292:** Convex filter logs check-by-check failure counts when 0 pass; grep `CONVEX_FILTER | 0_eligible`. | SO signals only; ORR separate. See [SignalRulesChecklist.md](doc_elements/Sessions/2026/Feb24%20Session/SignalRulesChecklist.md), [Feb24 Session — Gaps and Fixes](doc_elements/Sessions/2026/Feb24%20Session/SIGNAL_COLLECTION_GAPS_AND_FIXES.md), [SESSION_SUMMARY_FEB27_2026.md](doc_elements/Sessions/2026/Feb27%20Session/SESSION_SUMMARY_FEB27_2026.md). |
| 2 | **Priority ranking (ORB SO only)** | `calculate_so_priority_score` → sort by score | **Rev 00348 — continuation-first base:** `w_cont = SO_CONTINUATION_MOMENTUM_WEIGHT` (default **0.32**, clamped **0.18–0.42**) × `continuation_quality_score` + `w_rem` × weighted mix (**vwap_score** 0.24, **rs** 0.18, **orb_vol** 0.28, **conf** 0.10, **rsi** 0.14, **orb_range** 0.06) within the remainder; then **`× quality_multiplier`** minus soft penalties (exhaustion, distance-from-ORB, ORB-range soft, deceleration). Logs **`SO_RANK_BREAKDOWN`**, telemetry **`SO_CONTINUATION_VS_EXTENSION_BIAS`**. (Legacy v2.1 27/25/22/13/10/2 narrative is research lineage, not the live additive base.) **ORB 0DTE:** `_rank_signals_by_priority` — env **`0DTE_PRIORITY_RANK_W_BREAKOUT`** **0.14**, **`0DTE_PRIORITY_RANK_W_ORB_RANGE`** **0.10**, **`0DTE_PRIORITY_RANK_W_VOLUME`** **0.22**, **`0DTE_PRIORITY_RANK_W_CONVEX`** **0.20**, **`0DTE_PRIORITY_RANK_W_EARLY_MOMENTUM`** **0.34** (defaults; profile / `ORB0DTE.env` can override) × extension penalty on high `breakout_score`; tie-break prefers higher **`early_momentum`**. |
| 3 | **Winner-profile quality gate** | Data-driven pre-filter before adaptive selection | Rejects loss-prone combinations (weak volume/confidence, overextended non-bull conditions, wide ORB + weak participation) with configurable floors and minimum keep count. |
| 4 | **Adaptive selection** | Expense-ratio filter; protected top 3 | Targets 15→12→10→8 (expense ratio ≤30%); fallback top 8 affordable. |
| 5 | **Assign `priority_rank`** | Before batch sizing (Rev 00099) | Rank 1..N so Risk Manager can apply correct multipliers. |
| 6 | **Batch position sizing** | `calculate_batch_position_sizes()` (Risk Manager) | **6 steps:** (1) Rank multipliers, (2) Max position cap 35%, (3) ADV cap 1% (Slip Guard), (4) Normalize to SO capital, (5) Whole-share rounding, (6) Post-rounding redistribution. |
| 7 | **Filter quantity = 0** | After batch sizing (Rev 00105) | Signals with 0 shares are dropped; only executable signals proceed. |
| 8 | **Execute** | `_batch_execute_live_signals` → E*TRADE | Orders sorted by `priority_rank` (best first); concurrency 3, 1.2s spacing. |

**Slip Guard in batch sizing:** Step 5 applies ADV caps (1% of ADV per symbol). Freed capital from capped positions is not reallocated in the current batch path; total deployment may be below SO capital when several symbols are ADV-capped. Config `SLIP_GUARD_REALLOCATION_ENABLED` exists for future use.

**Result:** Best-ranked trades get larger size (3.0×, 2.5×, 2.0× …), fit within 90% SO allocation and 35% per-trade cap, respect ADV, and execute in priority order.

### **SO Priority Summary (Signal Collection -> Execution)** ⭐

**Purpose**: enforce a ranked quality pipeline that favors top winners and blocks likely losers.

1. **SO list formation**: include only symbols passing all 3 SO LONG validation rules.
2. **7:30 breakout recheck**: remove symbols that no longer meet breakout threshold on fresh quote.
3. **Ranking score (Rev 00348 + 00347)**: continuation-first base from **`SO_CONTINUATION_MOMENTUM_WEIGHT`** + remainder blend (see table row 2); multiply by **`quality_multiplier`** (winner-profile shaping); subtract soft penalties; persist **`priority_base_score`**, **`priority_quality_multiplier`**, **`priority_score`**.
4. **Telemetry**: **`SO_RANK_BREAKDOWN`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`** (bias JSON does not change rank).
5. **Winner-profile quality gate**: configurable **`SO_WINNER_*`** thresholds before adaptive sizing.
6. **Adaptive selection + ranked execution**: keep best affordable candidates, size by rank/caps, execute highest-ranked first.

**Primary tuning keys**: `SO_CONTINUATION_MOMENTUM_WEIGHT` (and related soft-penalty keys in **`configs/ORBSO.env`**), `SO_WINNER_PROFILE_FILTER_ENABLED`, `SO_WINNER_MIN_VOLUME_RATIO`, `SO_WINNER_HARD_MIN_VOLUME_RATIO`, `SO_WINNER_MIN_CONFIDENCE`, `SO_WINNER_MAX_RSI_NON_BULL`, `SO_WINNER_MIN_VWAP_DISTANCE`, `SO_WINNER_MIN_KEEP_COUNT`.

**0DTE Options path (Long and Short):** 0DTE raw list = symbols that passed **full** Long or Short validation (same three rules as ORB / inverse — Rev 00309). Then: Convex (ORB range % = morning capture; if width 0% after recovery, SHORT may use breakdown extension to pass range gate) → priority (**Rev 00348** env weights **`0DTE_PRIORITY_RANK_W_*`**; ORB range is **one** alpha factor, not a standalone “30% range tier” in runtime defaults) → tiers → Hard Gate → **chain + strike selection** (optional **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`** attempts before final reject) → execution. **May 11 — distance from ORB:** extension uses **soft penalty + strong-continuation bypass + extreme-only hard reject** (**`ORB_0DTE_OVEREXTENSION_*`**), not a single legacy percent gate alone. **Current strategy routing is single-leg-first** for strongest setups (`lotto`, `long_call`, `long_put`), with spread structures (`momentum_scalper`, `itm_probability_spread`, `debit_spread`) as fallback. ITM probability spread selection remains strict and symmetric for PUT and LONG (no automatic delta-band widening). **Rev 00319 execution quality guardrails:** debit spreads must pass configured payoff checks (`0DTE_MIN_RISK_REWARD`, `0DTE_MIN_MAX_PROFIT_PER_SPREAD`, `0DTE_MAX_DEBIT_TO_WIDTH_PCT`) or are rejected with `poor_payoff_profile`. **Rev 00328 — before payoff checks:** if spread selectors return no valid structure, logs/alerts show **`spread_selection_failed`**; common causes include premium window, strike availability, or per-leg liquidity. **ORB 0DTE spread risk (May 11):** when quotes stay **`partial_leg`** / non–exit-grade, **timeout-class** exits may still fire under **conservative/forced relief** — correlate broker fills with **`ORB_OPTIONS_FORCED_DEGRADED_EXIT`**. **Selector forensics (May 14):** grep **`ORB_0DTE_SELECTOR_FULL_REPLAY`** for structured ladder / width-ladder rejects. **When Convex rejects on ORB range:** grep `CONVEX_REJECT` + symbol (e.g. flat capture before recovery). See [ProcessFlow.md](ProcessFlow.md#0dte-strategy--from-signal-collection-list-to-execution-and-monitoring), [Strategy.md](Strategy.md). **When 0 0DTE signals:** grep `CONVEX_REJECT`, `HARD_GATE_SUMMARY`, `0DTE_qualified_for_execution`.

**Trendline 0DTE path (event-driven):** Candidates are built at 7:30 from the full 0DTE universe, then executed only after trendline confirmation gates pass. **May 11:** composite **entry-quality score** and canonical break archetypes (**`IMPULSE_BREAK`**, **`CONTINUATION_DRIFT`**, **`EXHAUSTION_REVERSAL`**) with **`READY_TO_EXECUTE`** watch-loop rescoring; defaults for score keys in **`modules/trendline_entry_defaults.py`**. Before execution, Trendline signals are normalized into `ExecutionIntent` and gated by risk-manager `evaluate_intent` approval. Daily risk is constrained by:
- `TRENDLINE_MAX_TRADES_PER_DAY` (default 5, first valid confirmations only)
- `TRENDLINE_MAX_OPEN_POSITIONS` (default **5**, rolling concurrent cap — when one exits, the next ready signal can open)
- `TRENDLINE_ACCOUNT_ALLOCATION_PCT` and `TRENDLINE_SLOT_COUNT` (slot capital = allocated equity × allocation% / 100 ÷ slot count)
- `MAX_TOTAL_OPTION_POSITIONS` (combined ORB 0DTE + Trendline open options; default **11**)
- existing account safety checks (no overspend, position constraints, explicit sizing rejection logs)

**SO path forensics (Rev 00318):** Grep Cloud Logging for **`SO_PIPELINE`** to trace Standard Orders from signal collection through ranking, adaptive selection, batch sizing, and execution (diffs, rank snapshots, footers).

**0DTE chain policy:** Demo and Live modes both use live E*TRADE option chain/quote data in current deployment (`REQUIRE_LIVE_OPTION_DATA=true`, `0DTE_DEMO_SYNTHETIC_CHAIN=false`).

---

### Opening Bar Protection & ORB range {#opening-bar-protection--orb-range}

**Rev 00312:** When an ORB ETF position is added to stealth trailing, **market_data** includes **orb_range_pct** from **`ORBData`** (morning capture). **Entry bar volatility** for tiered stops (2% / 3% / 5% / 8%) uses that value when present — **same metric** as Convex and priority **Range** factors. If ORB data is missing, fallback remains estimated H/L from quote or intraday bar.

---

**Current Default (90/10):**
- **90% SO Trading**: Deployed to Standard Order trades (ORR disabled)
- **10% Cash Reserve**: Safety buffer that grows with account

### Three-Path Risk Scope

The platform now runs three concurrent strategy paths with shared risk principles and isolated execution/accounting:

1. **Easy ORB Strategy (ETF SO)**  
   Uses ORB capital/risk controls (`SO_CAPITAL_PCT`, position caps, stealth exit framework).

2. **Easy 0DTE Strategy (ORB Options)**  
   Uses 0DTE-specific options risk controls (Convex/Hard Gate, options stop/timeout/profit logic).

3. **Easy Trendline 0DTE Strategy (Options)**  
   Uses trendline-specific cap/sizing controls (`TRENDLINE_MAX_TRADES_PER_DAY`, slot-based allocation, trendline account isolation), while still respecting global account boundaries and safety guards.

All three share Red Day and safety philosophy, but execution and PnL tracking are separated per strategy path for clean diagnostics.

**Unified 0DTE options exits (ORB + Trendline):**
- Normal options exits run through one premium-based stealth engine in `modules/prime_options_stealth_trailing_tp.py`.
- Profile resolution controls behavior by path/archetype/position type (including MFE-retention drawdown and profit-floor behavior).
- Failsafe/emergency paths remain separate from this normal exit brain.

**Account Scaling Examples:**
| Account Size | SO Capital (90%) | Cash Reserve (10%) |
|--------------|------------------|---------------------|
| $1,000 | $900 | $100 |
| $2,500 | $2,250 | $250 |
| $5,000 | $4,500 | $500 |
| $10,000 | $9,000 | $1,000 |
| $50,000 | $45,000 | $5,000 |

**How to Adjust** (Rev 00103):
```bash
# To change from 90% to 80%:
# Edit configs/ORBSO.env (SINGLE SOURCE OF TRUTH)
SO_CAPITAL_PCT=80.0
CASH_RESERVE_PCT=20.0
# Restart application → Done!
```

**Key Features**:
- ✅ **Validated Automatically**: SO + ORR + Reserve MUST = 100%
- ✅ **Easy to Adjust**: Change in ONE place (`configs/ORBSO.env` for allocation %)
- ✅ **Applied Everywhere**: Trading System, Demo, Live Risk Managers
- ✅ **Error-Proof**: Validation at startup catches mistakes

---

### 2. ORB Strategy Capital Split

**90% SO / 0% ORR / 10% Reserve (Rev 00103 - UNIFIED SYSTEM):**

| Allocation | Purpose | Timing | Notes |
|------------|---------|--------|-------|
| **90% SO** | Standard Orders | 7:30 AM PT (10:30 AM ET) | Batch execution |
| **0% ORR** | Opening Range Reversals | DISABLED | Will optimize separately |
| **10% Reserve** | Safety buffer | Always maintained | Never deployed |

**Example ($1,000 account):**
- SO Capital: $900 (90% of account) ⭐ **INCREASED**
- ORR Capital: $0 (0% - DISABLED)
- Cash Reserve: $100 (10% safety)

**Rationale:**
- ✅ ORR trades need separate optimization before re-enabling
- ✅ 90% SO allocation maximizes profitable SO opportunities
- ✅ Maintains 10% cash reserve for safety
- ✅ Can execute up to **15** concurrent **SO ETF** trades at the 7:30 batch (`MAX_CONCURRENT_TRADES`) — separate caps apply to ORB 0DTE and Trendline options
- ✅ Better capital efficiency with proven strategy

---

### 3. Smart Integer Rounding (Rev 00038) 📈

**ETRADE WHOLE-SHARE OPTIMIZATION: Maximizes Capital Utilization**

ETrade requires whole shares only (no fractional shares). The system intelligently rounds share quantities to maximize capital deployment while respecting all safety limits.

**How It Works:**
- **Try Rounding UP First**: Calculate cost for quantity + 1 share
- **Safety Checks**: Only round up if safe (5% overage tolerance, 35% cap, available capital)
- **Fallback to Down**: If unsafe, round down (conservative)
- **Logs All Decisions**: Clear visibility into every rounding choice

**Example (7 Signals, $1,000 Account):**
```python
SOXL:
- Allocated: $108.00
- Price: $8.50
- Raw Quantity: 12.706 shares
- Round Down: 12 shares = $102.00 (loses $6.00)
- Round Up: 13 shares = $110.50 (+$2.50 over, 2.3% overage)
- Decision: Round UP ✅ (within 5% tolerance)
- Result: $110.50 deployed vs $102.00 (+$8.50 improvement)
```

**Capital Efficiency Improvement:**
```python
Before Rev 00038 (Always Round Down):
- 15 positions allocated: $900
- Actual deployed: $730-$770 (81-85%)
- Lost to rounding: $130-$170 per batch

After Rev 00038 (Smart Rounding):
- 15 positions allocated: $900
- Actual deployed: $810-$850 (90-94%)
- Lost to rounding: $50-$90 per batch
- Improvement: +$80-$100 (+9-13 percentage points!)

After Rev 00090 (Post-Rounding Redistribution):
- 15 positions allocated: $900
- Actual deployed: $880-$900 (88-90%)
- Lost to rounding: $20-$40 per batch
- Total improvement: +$150-$180 (+17-20 percentage points!)
```

---

## 🚀 Key Features

### **1. Slip Guard - ADV-Based Position Capping** 🛡️ ⭐

**Prevents Slippage at Any Account Size:**

Automatically caps position sizes at 1% of Average Daily Volume (ADV) to prevent slippage. In the current path, freed ADV-capped capital is not auto-redistributed unless a future reallocation path is enabled.

**How It Works:**
- Daily ADV refresh at 6:00 AM PT (90-day rolling average)
- Caps positions exceeding 1% of symbol's ADV
- In the current production path, freed capital from ADV-capped names is **not** redistributed automatically; deployment can be slightly below target when many names are capped (`SLIP_GUARD_REALLOCATION_ENABLED` remains future-facing).
- Maintains risk constraints at all account sizes

**Example ($500K Account):**
```
Example Symbol (Rank 3): $36.5K → Capped at $12K (1% of $1.2M ADV)
Freed: $24.5K (not auto-reallocated in current batch path—may remain undeployed)

Result:
✅ No slippage on the capped symbol (ADV limit respected)
✅ Uncapped symbols still sized via rank multipliers + normalization toward SO capital target
⚠️ Total batch deployment may fall slightly below 90% when many symbols hit ADV caps
```

**Benefits:**
- ✅ Prevents slippage (2-5% → <0.5%)
- ✅ Scales to $10M+ accounts safely
- ✅ **High capital deployment** when symbols are liquid relative to intended size
- ✅ Automatic liquidity management

---

### **2. Greedy Capital Packing with Adaptive Fair Share** ⭐ BREAKTHROUGH

**Maximizes Trading Opportunities:**

Dynamic trade selection that fits as many high-priority trades as possible within capital constraints. Automatically adapts to extreme cases (small accounts, many signals, expensive symbols).

**Algorithm (selection in trading system, sizing in Risk Manager):**
1. Adaptive Fair Share:
   - Start with target (min(signals, 15))
   - If >60% rejected → halve target and retry
   - Minimum 3 trades, fallback to top affordable
2. Filter expensive symbols (share price > 110% of fair share)
3. Recalculate fair share based on AFFORDABLE signals
4. Select top N from affordable signals (expense ratio ≤30%; protected top 3)
5. **Batch sizing (Risk Manager)** applies: rank multipliers (3.0x, 2.5x, 2.0x...), max position cap 35%, Slip Guard (1% ADV cap), normalize to 90%, whole-share rounding, post-rounding redistribution
6. Filter out signals with quantity = 0; execute remaining in priority order

**Adaptive System Handles:**
- **$500 account, 30 signals, 60% expensive** → 12 trades ✅
- **$500 account, 30 signals, 90% expensive** → 3 trades ✅
- **$1,000 account, 10 signals, 3 expensive** → 7 trades, 88% deployed ✅
- **$50,000 account, 15 signals, all affordable** → 15 trades, 90% deployed ✅

**Results:**
- **Up to 15 trades** from 30 signals (vs 7-10 with fixed caps)
- **Capital Efficiency**: 85-90% with whole shares
- **Diversification**: Multiple winners maximize portfolio performance
- **Scalability**: Works from $500 to $10M+ accounts

**Benefits:**
- ✅ 57% more opportunities captured
- ✅ Optimal capital utilization
- ✅ Automatic affordability handling
- ✅ Prioritizes best trades first
- ✅ **Handles extreme account/signal scenarios** (Rev 00050-00052)

---

### **3. Batch Position Sizing with Normalization** ⭐ Rev 00090 - Complete 6-Step Flow

**Complete Flow (Rev 00084-00090: Clean + Configurable + Redistribution):**

**6-Step Process (Handled by Risk Manager):**
1. **Apply Rank Multipliers** (3.0x, 2.5x, 2.0x, 1.71x, 1.5x, 1.2x, 1.0x)
2. **Apply Max Position Cap** (configurable, default 35% - from MAX_POSITION_SIZE_PCT)
3. **Apply ADV Limits** (Slip Guard - 1% ADV cap if enabled)
4. **Normalize to Target Allocation** (configurable, default 90% - from SO_CAPITAL_PCT)
5. **Constrained Sequential Rounding** (whole shares, maximize deployment)
6. **Post-Rounding Redistribution** ⭐ NEW - Redistributes unused capital to top signals

**Configuration** (Rev 00085/00086/00201):
Capital **percentages** (`SO_CAPITAL_PCT`, `ORR_CAPITAL_PCT`, `CASH_RESERVE_PCT`) and **`MAX_CONCURRENT_TRADES`** are in **`configs/ORBSO.env`**. **`0DTE_MAX_POSITIONS`** lives in **`configs/ORB0DTE.env`**; Trendline open/slot caps in **`configs/Trendline0DTE.env`**; combined options book **`MAX_TOTAL_OPTION_POSITIONS`** in **`configs/Shared.env`**. Per-trade **size caps**, stealth exits, slip guard, Red Day, health checks, and the legacy **`MAX_CONCURRENT_POSITIONS`** alias are in **`configs/Risk.env`** (merged former position-sizing + risk-management + slip-guard — see file header) (see [Settings.md](Settings.md)).

**Validation**: System automatically validates SO + ORR + Reserve = 100% on startup.

**Unified Steep Multipliers:**

| Priority Rank | Multiplier | Fair Share Example ($1K, 7 signals) |
|---------------|------------|--------------------------------------|
| **Rank 1** | 3.0x | $128.57 × 3.0 = $385 → **$190** (normalized) |
| **Rank 2** | 2.5x | $128.57 × 2.5 = $321 → **$150** (normalized) |
| **Rank 3** | 2.0x | $128.57 × 2.0 = $257 → **$120** (normalized) |
| **Rank 4-5** | 1.71x | $128.57 × 1.71 = $220 → **$103** (normalized) |
| **Rank 6-10** | 1.5x | $128.57 × 1.5 = $193 → **$90** (normalized) |
| **Rank 11-15** | 1.2x | $128.57 × 1.2 = $154 → **$72** (normalized) |

**Position Sizing Examples:**

| Account | Signals | Rank #1 | Rank #5 | Rank #15 | Deployed |
|---------|---------|---------|---------|----------|----------|
| **$1K** | 7 | $190 (19%) | $103 (10%) | - | $850-900 (85-90%) |
| **$1K** | 15 | $108 (11%) | $62 (6%) | $43 (4%) | $800-850 (80-85%) |
| **$5K** | 7 | $952 (19%) | $518 (10%) | - | $4,250-4,500 (85-90%) |
| **$5K** | 15 | $543 (11%) | $309 (6%) | $217 (4%) | $4,000-4,250 (80-85%) |
| **$50K** | 15 | $5,427 (11%) | $3,093 (6%) | $2,171 (4%) | $40,000-45,000 (80-90%) |

**Benefits:**
- ✅ **Clean, efficient flow** (single method handles everything)
- ✅ **88-90% deployment guaranteed** (post-rounding redistribution, Rev 00090)
- ✅ **Top signals rewarded** (Rank #1 gets 3x more capital + redistribution)
- ✅ **Scales automatically** across all account sizes
- ✅ **35% position cap** enforced
- ✅ **ADV caps respected** (Slip Guard integrated)
- ✅ **No redundancy** (one pass through, no re-normalization)
- ✅ **Tested on historical signals** (Oct 30, 31, 24: 100% pass rate)

---

### **4. Enhanced Red Day Detection & Emergency Exit System** 🚨 ⭐ Rev 00176 - DEPLOYED

**Multi-Layer Loss Prevention System:**

The system implements a comprehensive 3-layer approach to prevent and minimize losses on red days:

**Execution policy when Red Day triggers:**
- **ORB (SO) Long entries**: blocked
- **0DTE Long (CALL) non-Tier-1** entries: blocked
- **0DTE Long (CALL) Tier-1** entries: allowed
- **0DTE Short (PUT)** entries: allowed

#### **Layer 1: Pre-Execution Red Day Detection (7:30 AM PT)**
**Enhanced Pattern Detection with 3-Tier Override System** (Rev 00168/00169/00171/00172/00173):

**Detection Patterns**:
- **Pattern 1**: OVERSOLD (RSI <40) + WEAK VOLUME (<1.0x) - Original Nov 4 pattern
- **Pattern 2**: OVERBOUGHT (RSI >80) + WEAK VOLUME (<1.0x) - New Dec 5 pattern ⭐
  - **3-Tier Override System** (Rev 00171/00172/00173):
    - **Primary**: MACD > 0.0 AND RS vs SPY > 2.0 → Allow trading
    - **Secondary**: MACD > 10.0 AND (RS missing/zero) → Allow trading
    - **Tertiary**: VWAP Distance > 1.0% AND MACD > 0.0 → Allow trading
- **Pattern 3**: WEAK VOLUME ALONE (≥80%) - Strong signal regardless of RSI

**Complete Red Day metric set (logged/alerted each check):**
- `Signals analyzed`: e.g. `25`
- `Weak Volume (<1.0x)`: e.g. `100% (25/25)`
- `Oversold (RSI <40)`: e.g. `36% (9/25)`
- `Overbought (RSI >80)`: e.g. `0%`
- `Avg RSI`: e.g. `43.7`
- `Avg Volume`: e.g. `0.49x`
- `Avg MACD`: e.g. `0.219`
- `Avg RS vs SPY`: e.g. `0.00`
- `Avg VWAP distance`: e.g. `0.00%`

**Impact**: Would have prevented all 15 trades on Dec 5 ($13.53 saved), allows profitable days like Dec 8 and Dec 9

#### **Layer 2: Post-Execution Health Checks (Every 15 Minutes)**
**Emergency Exit System** (7:45 AM - 12:45 PM PT) - Rev 00168 Enhanced:

- **Frequency**: Every 15 minutes (~21 checks per day)
- **Red Flags** (Rev 00168 Enhanced):
  - Win rate <35%
  - Avg P&L <-0.5% (kept at -0.5% to avoid premature exits on recoverable days)
  - Low momentum <40%
  - Weak peaks <0.8%
  - **All positions losing (100% losers)** ⭐ NEW
- **Actions**: 
  - **EMERGENCY (3+ red flags)**: Close ALL positions immediately
  - **WARNING (2 red flags)**: Close weak positions (P&L < -0.5% - kept to avoid premature exits)
  - **OK (0-1 red flags)**: Continue normal trading

**Complete Portfolio Health Check metric set (15-minute checks):**
- `Win rate`: threshold `<35%`
- `Average P&L`: threshold `<-0.5%`
- `Momentum score`: threshold `<40%`
- `Peak strength`: threshold `<0.8%`
- `All positions losing`: trigger at `100%` losers

#### **Layer 3: Individual Position Protection (Permanent Floor Stops)**
**Entry Bar Protection** (Rev 00135):

- **Permanent Floor Stops**: Based on actual ORB volatility (2-8% stops)
- **Maintained for entire trade**: Breakeven and trailing can move up but NEVER below floor
- **Prevents early exits**: No 30-minute expiration, protection lasts full trade duration

**Impact**:
- **Red Day Filter**: $400-1,600/year (prevents 3-5 red days/month)
- **Portfolio Health Check**: $200-500/year (earlier exits on bad days)
- **Combined Annual Savings**: $600-2,100/year
- **Capital preservation**: Prevents execution on high-risk days before trades fire
- **Emergency protection**: Exits deteriorating positions early (-0.5% vs -1.5% avg, kept to avoid premature exits on recoverable days)
- **Floor stop protection**: Prevents premature exits on volatile but profitable trades
- **Holiday Integration**: Prevents trading on 19 high-risk days per year (Rev 00137)

---

### **5. Optimized Exit Settings** ⭐ Rev 00196 - DATA-DRIVEN OPTIMIZATION

**Exit Settings Optimized Based on Historical Data Analysis:**

#### **Breakeven Protection** (Rev 00196 - Optimized)
- **Activation**: +0.75% profit after 6.4 minutes (optimized from 2.0% and 3.5 min)
- **Locks**: +0.2% minimum profit
- **Based On**: Historical data analysis (median activation P&L and timing)
- **Expected Impact**: Better profit capture vs previous settings

#### **Trailing Stop** (Rev 00196 - Optimized)
- **Activation**: +0.7% profit after 6.4 minutes (optimized from 0.5% and 3.5 min)
- **Distance**: Dynamic 1.5-2.5% based on volatility and profit tiers
- **Uses**: WIDER of volatility/profit-based for maximum protection
- **Performance**: 91.1% profit capture vs 75.4% at 0.5% threshold
- **Expected Impact**: 85-90% profit capture vs 67% current (+18-23% improvement)

#### **14 Automatic Exit Triggers** (Rev 00075 - All Functional):

**Individual Position Exits** (12):
1. **Stop Loss**: Price hits current stop level (always active)
2. **Trailing Stop**: Price drops 1.5-2.5% from peak (after breakeven/TP)
3. **Breakeven Protection**: +0.75% activates after 6.4 min, locks +0.2% profit (Rev 00196)
4. **Take Profit**: At +3%, activates trailing (doesn't exit, lets winner run)
5. **Profit Timeout**: 2.5 hours if profitable and unprotected (Rev 00070: protection check fixed)
6. **Maximum Hold Time**: 4 hours hard limit (closes at 11:30 AM) (Rev 00072: timezone fixed)
7. **Rapid Exit - No Momentum**: After 15 min if peak <+0.3% (conditional)
8. **Rapid Exit - Immediate Reversal**: 5-10 min if down >-0.5% (Rev 00070: units fixed)
9. **Rapid Exit - Weak Position**: After 20 min if down >-0.3% AND peak <+0.2% (Rev 00070: units fixed)
10. **RSI Momentum Exit**: RSI <45 for 90 sec AND losing -0.375%+ (Rev 00070: RSI data fixed)
11. **Gap Risk**: >2% gap from highest price (flash crash protection)
12. **End of Day Close**: 12:55 PM PT auto-close all positions

**Portfolio-Level Health Checks** (2):
13. **Emergency Exit**: 3+ red flags → Close ALL positions (Rev 00044/00067: every 15 min)
14. **Weak Day Exit**: 2 red flags → Close losing positions (Rev 00044/00067: every 15 min)

**Operator tunables** (Rev 00201 lineage — keys now in **`configs/Risk.env`** among others):
- ✅ Stealth exits, Red Day, health checks, slip guard, and position caps via merged env (see **`configs/README.md`** for which file owns each key)
- ✅ Production overrides via `os.environ` / Cloud Run still win over repo defaults

---

### **6. Entry Bar Protection** 🛡️ ⭐ CRITICAL (Rev 00135 - PERMANENT FLOOR STOPS)

**Permanent Floor Stops Based on Actual ORB Volatility:**

Prevents premature stop-outs on high-volatility entries AND early exits at 30 minutes by using permanent floor stops, scaled to the actual entry bar volatility from ORB data.

**How It Works:**
- **ORB Data Collection**: Captures actual high/low from 6:30-6:45 AM PT
- **Volatility Calculation**: `(ORB_high - ORB_low) / ORB_low × 100`
- **Permanent Floor Stops** (maintained for ENTIRE trade - Rev 00135):
  - **9%+ volatility**: 8% EXTREME stop (permanent floor)
  - **6-9% volatility**: 8% EXTREME stop (permanent floor)
  - **3-6% volatility**: 5% HIGH stop (permanent floor)
  - **2-3% volatility**: 3% MODERATE stop (permanent floor)
  - **<2% volatility**: 2% LOW stop (permanent floor)
- **Key Innovation**: `initial_stop_loss` stored as permanent floor - breakeven and trailing can move up but NEVER below floor
- **No Time Limit**: Protection maintained for entire trade duration (prevents early exits at 30 minutes)

**Real-World Example (Oct 30, 2025 - archived symbol):**
```
Example Entry: $72.71
ORB High: $77.80, ORB Low: $71.28
Entry Bar Volatility: 9.15%
Protection: EXTREME (8% stop)
Stop: $67.62

9:00 AM Drop: $71.28 (-1.97%)
Margin Above Stop: $3.66 ✅ SURVIVED!

Without Entry Bar Protection (3% default):
Stop: $70.53
Margin: $0.75 (barely survived!)

11:00 AM Peak: $77.80 (+7.00%)
Exit: $76.63 (1.5% trailing)
P&L: +$7.84 (+5.39%)
```

**Benefits:**
- ✅ Prevents 64% of immediate stop-outs
- ✅ Saves reversal trades (documented example: +$7.84)
- ✅ Efficient stops for low-volatility entries
- ✅ Adaptive protection = better risk/reward

---

## 📊 Risk Management Configuration

### **Unified configuration** ⭐ Rev 00201 — **files May 2026**

Operator defaults are split across the seven canonical env files; below mirrors the **old** doc grouping with **current** paths.

#### **Capital Allocation** (`configs/ORBSO.env`):
```env
SO_CAPITAL_PCT=90.0                   # 90% for SO trading
ORR_CAPITAL_PCT=0.0                   # 0% for ORR (disabled)
CASH_RESERVE_PCT=10.0                 # 10% cash reserve (auto-calculated)
```

#### **Position sizing & legacy SO alias** (`configs/Risk.env`):
```env
MAX_POSITION_SIZE_PCT=35.0           # 35% max position cap
MAX_CONCURRENT_POSITIONS=15          # Legacy: TradingConfig os.getenv default; align with SO — prefer MAX_CONCURRENT_TRADES in ORBSO.env for SO batch/divisor
MIN_POSITION_VALUE=50.0               # $50 minimum
```

**Also split across path files (not duplicated here):** `MAX_CONCURRENT_TRADES=15` in **`ORBSO.env`** (ORB SO ETF @ 7:30); `0DTE_MAX_POSITIONS=6` in **`ORB0DTE.env`**; `MAX_TOTAL_OPTION_POSITIONS=11` in **`Shared.env`**; Trendline slot/open caps in **`Trendline0DTE.env`** — see [Settings.md](Settings.md) and [0DTEORB.md](0DTEORB.md).

#### **Exit settings (stealth / ETF)** (`configs/Risk.env`):
```env
# Breakeven Protection (Rev 00196 - Optimized)
STEALTH_BREAKEVEN_THRESHOLD=0.0075    # 0.75% activation
STEALTH_BREAKEVEN_TIME_MIN=6.4        # 6.4 minutes
STEALTH_BREAKEVEN_OFFSET=0.002        # 0.2% offset

# Trailing Stop (Rev 00196 - Optimized)
STEALTH_TRAILING_ACTIVATION_THRESHOLD=0.007  # 0.7% activation
STEALTH_TRAILING_ACTIVATION_TIME_MIN=6.4     # 6.4 minutes
STEALTH_BASE_TRAILING=0.015          # 1.5% base trailing
STEALTH_TRAILING_MIN=0.015           # 1.5% minimum
STEALTH_TRAILING_MAX=0.025           # 2.5% maximum

# Profit Timeout
STEALTH_PROFIT_TIMEOUT_HOURS=2.5     # 2.5 hours

# Maximum Hold Time
STEALTH_MAX_HOLD_TIME_HOURS=4.0      # 4 hours hard limit

# Rapid Exits
RAPID_EXIT_NO_MOMENTUM_THRESHOLD=0.003  # 0.3% peak threshold
RAPID_EXIT_REVERSAL_THRESHOLD=0.005     # 0.5% down threshold
RAPID_EXIT_WEAK_THRESHOLD=0.003         # 0.3% down threshold
RAPID_EXIT_WEAK_PEAK_THRESHOLD=0.002    # 0.2% peak threshold

# RSI Momentum Exit
RSI_MOMENTUM_EXIT_THRESHOLD=45        # RSI <45
RSI_MOMENTUM_EXIT_TIME_SEC=90         # 90 seconds
RSI_MOMENTUM_EXIT_LOSS_THRESHOLD=0.00375  # -0.375% loss

# Gap Risk
GAP_RISK_THRESHOLD=0.02               # 2% gap from highest price
```

#### **Slip Guard** (`configs/Risk.env`):
```env
SLIP_GUARD_ENABLED=true              # Enable ADV-based capping
SLIP_GUARD_ADV_PCT=1.0               # 1% of ADV limit
SLIP_GUARD_LOOKBACK_DAYS=90          # 90-day rolling average
SLIP_GUARD_REALLOCATION_ENABLED=false # Reserved for future reallocation path (current flow does not auto-redistribute ADV-capped capital)
```

#### **Red Day Filter** (`configs/Risk.env`):
```env
RED_DAY_FILTER_ENABLED=true          # Enable red day detection
RED_DAY_OVERSOLD_RSI_THRESHOLD=40    # RSI <40
RED_DAY_OVERBOUGHT_RSI_THRESHOLD=80  # RSI >80
RED_DAY_WEAK_VOLUME_THRESHOLD=1.0    # Volume <1.0x
RED_DAY_PATTERN_THRESHOLD=0.70       # 70% pattern match
```

#### **Health Check** (`configs/Risk.env`):
```env
HEALTH_CHECK_ENABLED=true            # Enable health checks
HEALTH_CHECK_FREQUENCY_MIN=15        # Every 15 minutes
HEALTH_CHECK_WIN_RATE_THRESHOLD=0.35  # <35% win rate
HEALTH_CHECK_AVG_PNL_THRESHOLD=-0.005 # <-0.5% avg P&L
HEALTH_CHECK_MOMENTUM_THRESHOLD=0.40  # <40% momentum
HEALTH_CHECK_WEAK_PEAKS_THRESHOLD=0.008  # <0.8% peaks
```

**Key Features**:
- ✅ Large tunable surface across **`Risk.env`** + path env files (Rev 00201 lineage)
- ✅ **Single source of truth per key** — follow **`configs/README.md`** to avoid duplicate definitions
- ✅ **Validated automatically** where enforced in loader / startup (e.g. SO + ORR + reserve = 100%)

---

## 🛡️ Safety Features

### Built-in Safeguards

1. **Position Isolation**: Only manages its own positions
2. **Drawdown Protection**: 10% maximum before Safe Mode
3. **Cash Reserve**: 10% maintained at all times
4. **Slip Guard Protection**: ADV-based position capping (Rev 00046) 🛡️
5. **Confidence Gates**: High confidence required for larger positions
6. **Greedy Packing**: Automatic affordability handling
7. **Spread Protection**: Prevents poor executions
8. **Stealth Trailing**: Dynamic stop loss management (Rev 00196 optimized)
9. **Time-Windowed Trading**: Only trades during optimal windows
10. **Red Day Filter**: Prevents trading on high-risk days (Rev 00176)
11. **Holiday Filter**: Prevents trading on 19 high-risk days per year (Rev 00137)
12. **Entry Bar Protection**: Permanent floor stops (Rev 00135)
13. **Health Checks**: Every 15 minutes (Rev 00067)

### Emergency Controls

```env
# Emergency Settings
EMERGENCY_STOP_ENABLED=true
EMERGENCY_STOP_LOSS_PCT=10.0
SAFE_MODE_ENABLED=true
SAFE_MODE_DRAWDOWN_THRESHOLD=10.0
```

---

## 📈 Performance Targets & Achievements

### Proven Results (Historical Validation - 11 Days)

**Overall Results:**
- **Weekly Return**: +73.69% (23% above +60% target)
- **Winning Days**: 10/11 (91% consistency)
- **Max Drawdown**: -0.84% (96% reduced from -21.68%)
- **Profit Factor**: 194.00 (vs 2.03 baseline)
- **Monthly Projection**: +508% (compounded)

**By Day Type Performance:**
| Type | Days | Baseline | Improved | Improvement |
|------|------|----------|----------|-------------|
| POOR | 3 | -49.75% | **+0.69%** | **+50.44%** |
| WEAK | 3 | -12.73% | **+3.08%** | **+15.81%** |
| GOOD | 3 | +57.12% | **+56.93%** | Preserved ✅ |

**Account Size Scaling:**
- **$1,000**: +73.69% weekly (validated)
- **$5,000**: +65-75% weekly (projected)
- **$50,000**: +60-70% weekly (projected)

### Risk-Adjusted Metrics

- **Win Rate**: 49.7% (typical days, realistic)
- **Profit Factor**: 194.00 (vs 2.03 baseline)
- **Max Drawdown**: -0.84% (well within 10% limit)
- **Sharpe Ratio**: Excellent (high return, low volatility)
- **Capital Efficiency**: 88-90% (greedy packing + redistribution)
- **Profit Capture**: Expected 85-90% with optimized settings (Rev 00196)

---

## ✅ Summary

The Easy ETrade Strategy achieves exceptional performance through:

**Core Strengths:**
- ✅ **Priority Score Ranking**: **Rev 00348** — ORB SO continuation-first; ORB 0DTE env-weighted alpha (`0DTE_PRIORITY_RANK_W_*`); legacy v2.1 / 35-30-20-15 narratives are historical unless profile-restored
- ✅ **Rank-Based Position Sizing**: Scales automatically from $1K to $100K+ accounts
- ✅ **Greedy Capital Packing**: 88-90% capital efficiency, up to 15 trades
- ✅ **Optimized Trailing Stop**: 0.7% activation, 1.5-2.5% distance (Rev 00196)
- ✅ **Optimized Breakeven**: 0.75% activation, locks +0.2% (Rev 00196)
- ✅ **Multi-Factor Ranking**: Confidence + volatility + volume + tier
- ✅ **Account Scaling**: Same % allocation, different $ amounts (automatic scaling)
- ✅ **Risk Management**: 10% reserve, 10% max drawdown, Safe Mode
- ✅ **Spread Protection**: Prevents poor executions
- ✅ **Position Isolation**: No interference with manual trades
- ✅ **Unified configuration**: seven-file `configs/` merge (Rev 00201 lineage)

**Proven Performance (Optimized):**
- ✅ **+73.69% weekly return** (exceeds +60% target by 23%)
- ✅ **91% winning day consistency** (10/11 days profitable)
- ✅ **Max drawdown -0.84%** (96% reduced from -21.68%)
- ✅ **Expected 85-90% profit capture** (vs 67% current - Rev 00196)

**System Status:**
- ✅ **Demo Mode**: Active and validated
- ✅ **Live Mode**: Ready for deployment
- ✅ **Both Risk Managers**: Identical logic, proven performance
- ✅ **ORB Strategy**: Optimized and profitable
- ✅ **Capital Constraints**: Automatically handled
- ✅ **Unified configuration**: seven-file `configs/` merge (Rev 00201 lineage)
- ✅ **Trade Persistence**: GCS persistence working (Rev 00203)

---

## 🔄 Revision History

### **Latest Updates (February 2026 - Rev 00289/00292)** ⭐

**Rev 00292 (Feb 26 - Convex filter 0-pass diagnosis):** Check-by-check failure counts when 0 eligible; grep-friendly `CONVEX_FILTER | 0_eligible` line; top per-symbol rejection details.

**Rev 00289 (Feb 26 - CRITICAL: Signal append fix):** Signal creation and append moved into `if orb_result.should_trade`; passing symbols now correctly appear in Signal Collection.

### **Previous Updates (February 2026 - Rev 00279/00280)** ⭐

**Rev 00280 (Feb - Diagnostics):** STEP 4 log records validation candle data source (PREFETCHED_IN_MEMORY / GCS_LOADED / FRESH_INTRADAY).

**Rev 00279 (Feb - Validation candle fix):** Explicit 7:15 close passed into rules when single prefetched bar exists; validation candle persisted to GCS after prefetch; scan loads from GCS when no in-memory prefetch (cross-instance). Ensures rule 3 uses same data as volume color and works when scan runs on different Cloud Run instance.

### **Previous Updates (January 6, 2026 - Rev 00231)** ⭐

**Rev 00231 (Jan 6 - Trade ID Shortening & Alert Formatting):**
- ✅ Trade ID shortening for cleaner format
- ✅ Enhanced alert formatting with bold key metrics
- ✅ Improved readability of trade information

### **Previous Updates (December 2025)**

**Rev 00203 (Dec 19 - Trade Persistence Fix):**
- ✅ Trade persistence fixed (trades persist immediately to GCS)
- ✅ Trade history survives Cloud Run redeployments

**Rev 00201-00202 (Dec 19 - Unified Configuration):**
- ✅ Centralized operator-tunable settings in `configs/` (evolved to **seven-file** merge per `config_loader.py` as of May 2026)
- ✅ Clean configuration architecture
- ✅ Single source of truth per key (see `configs/README.md`)

**Rev 00199-00200 (Dec 19 - Enhanced Logging & Exit Settings):**
- ✅ Enhanced logging (detailed stop update and exit trigger logging)
- ✅ Unified exit settings (all exit settings consistent)

**Rev 00196 (Dec 18 - Exit Settings Optimized):**
- ✅ Data-driven exit optimization (0.75% breakeven, 0.7% trailing, 6.4 min activation)
- ✅ Expected 85-90% profit capture vs 67% current (+18-23% improvement)
- ✅ Based on historical data analysis (median activation P&L and timing)

**Rev 00184 (Dec 12 - Exit Alert Formatting Fixes):**
- ✅ Aggregated Exit Alert Formatting Fixed
- ✅ EOD Report Formatting Fixed
- ✅ Trailing Stop Exit Fixed

**Rev 00180 (Dec 5 - Red Day Filter Enhanced):**
- ✅ 3-Pattern Detection (oversold, overbought, weak volume)
- ✅ 3-Tier Override System

**Rev 00137 (Nov - Holiday System Integrated):**
- ✅ Prevents trading on 19 high-risk days per year (bank + low-volume holidays)

**Rev 00138 (Oct - GCS Persistence):**
- ✅ Demo account balance persists between deployments
- ✅ Trade history persistence

---

*For implementation details:*
- **Demo Mode**: `modules/prime_demo_risk_manager.py`
- **Live Mode**: `modules/prime_risk_manager.py`
- **Position Monitoring**: `modules/prime_stealth_trailing_tp.py`
- **Trading System**: `modules/prime_trading_system.py`
- **ORB Strategy**: `modules/prime_orb_strategy_manager.py`
- **Configuration**: `configs/Data.env`, `configs/Shared.env`, `configs/ORBSO.env`, `configs/ORB0DTE.env`, `configs/Trendline0DTE.env`, `configs/Risk.env`, `configs/Alerts.env`

---

*Last Updated: May 15, 2026*  
*Version: Rev 00351+ — **May 15** SO `json`/batch-dedupe + execution policy (deploy pending); **May 14** aligns SO / 0DTE priority documentation with **Rev 00348** (`calculate_so_priority_score`, `_rank_signals_by_priority`, grep **`ORB_0DTE_SELECTOR_FULL_REPLAY`**). Prior Rev 00351 / May 11 ORB 0DTE overextension / chain fallback / durability / spread relief notes retained — see `docs/0DTEORB.md`.*
*Status: ✅ DEPLOYED - Active with optimized exit settings (Rev 00196), unified configuration (Rev 00201), and trade persistence (Rev 00203)*
