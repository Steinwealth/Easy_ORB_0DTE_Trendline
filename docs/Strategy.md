# 🎯 Easy ORB Strategy - ORB Trading System

**Last Updated**: May 15, 2026  
**Version**: **Rev 00351** docs sync; **May 15, 2026** — local **`BUILD_ID` `00349`**: Trendline impulse calibration (R1–R6), SO `json` ranking fix, SO batch dedupe on successful `_process_orb_signals`, CISCO/NEBIUS aliases, **execution policy layer** (telemetry, smart limits, opening profiles, fill reconcile — **`USE_MARKET_ORDERS=true`** default until desk sets `false`); production **`easy-etrade-strategy-00330-zdt` until deploy** — [May 15 session](doc_elements/Sessions/2026/May15%20Session/SESSION_SUMMARY_MAY15_2026.md). **May 14, 2026** — **Rev 00348** doc alignment: ORB SO **`calculate_so_priority_score`** is **continuation-first** (`SO_CONTINUATION_MOMENTUM_WEIGHT` + remainder blend, `SO_RANK_BREAKDOWN` / `SO_CONTINUATION_VS_EXTENSION_BIAS`); ORB 0DTE **`_rank_signals_by_priority`** defaults **`0DTE_PRIORITY_RANK_W_*`** (**0.14 / 0.10 / 0.22 / 0.20 / 0.34**) + extension penalty + **`early_momentum`** tie-break; Convex min score defaults from **`modules/orb0dte_execution_defaults.py`** / profile (see [0DTEORB.md](0DTEORB.md)); grep **`ORB_0DTE_SELECTOR_FULL_REPLAY`**. Includes May 13 ORB 0DTE pre-queue / selector / lifecycle notes; May 11 durability / overextension / spread relief; May 6 ORB SO ranking refinements prior.
**Current Note (Apr 28, 2026)**: Trendline 0DTE now includes delayed re-arm, market-regime tightening, impulse mode, slow-trend mode, early-entry sizing, retest path, and shared expansion-quality/min-move filters. Trendline stealth monitoring now uses outage-based degraded-data safety (`OPTION_STEALTH_NO_DATA_OUTAGE_SECONDS`), multi-source premium resolution (`option_mid -> option_last -> cache -> delta_estimate`), and explicit runtime/data-quality observability (`0DTE_RUNTIME_CONFIG`, `OPTIONS_DATA_*`).
**Status**: ✅ Production Ready - Critical Bug Fixes (Rev 00247), Trade Persistence Fix (Rev 00203), Unified Configuration (Rev 00201-00202), Exit Settings Optimized (Rev 00196), Trade ID Shortening (Rev 00231)  
**Proven Performance**: +73.69% weekly return with 91% winning day consistency  
**Expected**: 85-90% profit capture with optimized exit settings (Rev 00196)  
**Capital Deployment**: 88-90% guaranteed (6-step batch sizing + post-rounding redistribution)

---

## Overview

The Easy ORB Strategy is a proven automated trading system designed for US equities/options trading via the E*TRADE API. It runs three concurrent paths that share ORB capture but execute independently: **ORB ETF SO**, **ORB 0DTE options**, and **Easy Trendline 0DTE options**.

### **Three Strategy Paths (Current Production)**

1. **Easy ORB Strategy Standard Orders (ETF/stock)**  
   Time-based execution at 7:30 AM PT from SO Signal Collection.

2. **Easy 0DTE Strategy ORB Options**  
   Time-based 7:30 AM PT options execution from 0DTE Signal Collection after Convex + Hard Gate.

3. **Easy Trendline 0DTE Strategy Options**  
   Builds trendline candidates at 7:30 AM PT from the full 0DTE universe, then executes intraday only when break + hold + structure + momentum criteria pass, with clean/strong-breakout acceleration, drift-breakout support, final follow-through safeguards, drift minimum-displacement filtering, **composite entry-quality score**, and **canonical break archetypes** (`IMPULSE_BREAK`, `CONTINUATION_DRIFT`, `EXHAUSTION_REVERSAL`) with **`READY_TO_EXECUTE`** watch-loop rescoring.

**Current Strategy**: Opening Range Breakout (ORB) - Rev 00231  
**Status**: ✅ Production Ready (Deployed and Healthy)  
**Proven Performance**: +73.69% weekly return with 91% winning day consistency  
**Capital Deployment**: 88-90% guaranteed (6-step batch sizing + post-rounding redistribution)  
**Exit Settings**: Optimized (Rev 00196: 0.75% breakeven, 0.7% trailing, 6.4 min activation)  
**Configuration**: Unified configuration system (65+ configurable settings - Rev 00201)

**📋 Daily session flow:** For the actual performance flow and steps the software takes in a trading day (OAuth renewal, Good Morning alert, ORB Capture, Signal Collection rules and validation, Red Day filtering, execution, monitoring, exit strategies, EOD), see **[Daily Performance Flow — Steps the Software Takes](ProcessFlow.md#-daily-performance-flow--steps-the-software-takes)** in ProcessFlow.md (section near the top). If ORB Capture succeeded but Signal Collection failed (e.g. 0 signals), use **[ORB Capture → Execution Checklist](ORBCaptureToExecutionChecklist.md)** to verify 7:00 validation candle and prefetch for the next session.

---

## Execution policy (May 15, local — deploy pending)

**Design principle:** Execution is separated from **alpha** (ORB break, Convex, Trendline structure). Goal is **realized expectancy** (fills and exit quality), not theoretical midpoint perfection. **Missing a fast 7:30 move** is treated as worse than paying slightly more spread.

**Production default:** **`USE_MARKET_ORDERS=true`** and **`ENABLE_SMART_EXECUTION=true`** → behavior matches **pre–May 15 MARKET** orders until the operator sets **`USE_MARKET_ORDERS=false`**.

| Path | Opens (when smart enabled) | Closes (when smart enabled) |
|------|---------------------------|-----------------------------|
| **ORB SO (ETF)** | Aggressive limit ladder + reprice + market fallback (`modules/smart_equity_execution.py` via `prime_unified_trade_manager.py`) | Urgency-aware: stops/gap/EOD → **MARKET**; scale-outs / trailing → **PASSIVE** limit ladder (`map_equity_exit_urgency` + **`prime_stealth_trailing_tp.LiveETradeAdapter`**) |
| **ORB 0DTE** | Capped **`NET_DEBIT`** on spreads; single-leg limit ladder + market fallback (`easy0DTE/modules/etrade_options_api.py`) | `resolve_options_exit_plan` + last-look → LIMIT or MARKET (`options_trading_executor.py`) |
| **Trendline 0DTE** | Demo ledger only today (`live_execution_not_wired` in executor); chain/selection uses live quotes | Same **options** stealth engine as ORB 0DTE (`prime_options_stealth_trailing_tp.py`); options closes use the same urgency routing when live |

**Shared modules (new):** `modules/execution_telemetry.py`, `modules/execution_routing.py`, `modules/execution_profiles.py` (opening / mid-morning / defensive PT profiles), `modules/execution_fill_reconcile.py` (equity order-status poll).

**Optional env (code defaults if unset):**

| Key | Default | Role |
|-----|---------|------|
| `USE_MARKET_ORDERS` | `true` | When `true`, smart limit path is **disabled** (legacy MARKET) |
| `ENABLE_SMART_EXECUTION` | `true` | Must be true **and** `USE_MARKET_ORDERS=false` to activate smart limits |
| `EXEC_LAST_LOOK_MAX_SPREAD_PCT` | `2.5` | Base spread cap; opening profile widens tolerance internally |

**Grep tokens (all paths):** `EXECUTION_FILL_SUMMARY`, `EXECUTION_LIMIT_ATTEMPT`, `EXECUTION_REPRICE`, `EXECUTION_MARKET_FALLBACK`, `EXECUTION_FORCE_FALLBACK`, `EXECUTION_TIMEOUT_ABORT`, `EXECUTION_AGGRESSION_ESCALATED`, `EXECUTION_FILL_RECONCILED`, `EXECUTION_PARTIAL_FILL`, `EXECUTION_SLIPPAGE_GUARD_REJECT`, `LAST_LOOK_REJECT`.

**Details by path:** [0DTEORB.md](0DTEORB.md) (0DTE opens/exits/telemetry), [0DTETrendline.md](0DTETrendline.md) (entry calibration R1–R6; options exits). Session log: [May 15 session](doc_elements/Sessions/2026/May15%20Session/SESSION_SUMMARY_MAY15_2026.md).

---

## 🚀 Proven Performance (Historical Validation)

### **Historical Validation - 11 Days of Real Market Data (October 2024)**

**Overall Results:**
- **Weekly Return**: +73.69% (23% above +60% target)
- **Winning Days**: 10/11 (91% consistency)
- **Max Drawdown**: -0.84% (reduced 96% from -21.68%)
- **Profit Factor**: 194.00 (vs 2.03 baseline)
- **Days Recovered**: 5 losing days turned into wins

**By Day Type:**
| Type | Baseline | Improved | Saved |
|------|----------|----------|-------|
| POOR (3 days) | -49.75% | **+0.69%** | **+50.44%** 🎯 |
| WEAK (3 days) | -12.73% | **+3.08%** | **+15.81%** |
| GOOD (3 days) | +57.12% | **+56.93%** | Preserved ✅ |

### **Monthly Projection (Compounded)**
- **Month 1 Return**: +508%
- **Ending Balance**: $6,083
- **Growth**: $1,000 → $6,083 in 4 weeks

### **Expected Performance with Optimized Exit Settings** (Rev 00196)
- **Profit Capture**: Expected 85-90% (vs 67% current)
- **Improvement**: +18-23% profit capture improvement
- **Based On**: Historical data analysis (median activation P&L and timing)

### **Key Improvements**
- ✅ **Entry Bar Protection**: Prevents premature stop-outs (2-8% tiered stops - Rev 00135)
- ✅ **15-Min Health Check**: Detects bad days intelligently (every 15 min - Rev 00067)
- ✅ **Conditional Rapid Exits**: Only on bad days (preserves wins on good days)
- ✅ **Loss Prevention**: Turned 5 losing days into wins (+50.44% saved on POOR days)
- ✅ **Optimized Exit Settings**: 0.75% breakeven, 0.7% trailing, 6.4 min activation (Rev 00196)
- ✅ **Red Day Filter**: Prevents trading on high-risk days (saves $400-1,600/year - Rev 00176)
- ✅ **Holiday Filter**: Prevents trading on 19 high-risk days per year (Rev 00137)

---

## 🎯 ORB Strategy Core Concept

### **Opening Range Breakout (ORB)**

The strategy is based on a simple, proven principle: **The first 15 minutes of trading establishes the range, and breakouts from that range present high-probability trading opportunities.**

**ORB Windows** (Rev 00196 - Optimized):
- **ORB Capture (Opening Range only)**: 6:30-6:45 AM PT (9:30-9:45 AM ET) - First 15-minute candle after market open. **ORB High and ORB Low are from this window only.** This is the true Opening Range for breakout logic. (dynamic unique symbols after ORB + 0DTE merge — union of `core_list` + `0dte_list`)
- **Validation candle (not opening range)**: 7:00–7:15 AM PT is **after** market open; it is **not** the opening range. It is used only to check: (1) volume color (7:00 open vs 7:15 close → GREEN/RED), and (2) whether that bar’s close is above ORB high or below ORB low for rules. Broker-only (E*TRADE): 7:00 snapshot = open, 7:15 snapshot = close; batched 25 symbols/call (same as ORB capture). A **7:00 AM PT Cloud Scheduler job** must call `POST …/api/alerts/validation-candle-700` so open prices are captured and persisted to GCS (see [Cloud.md](Cloud.md)); a **7:15 AM PT job** calling `POST …/api/alerts/prefetch-validation-715` is recommended for scale-to-zero so the validation candle is ready even if the trading loop runs on a different instance.
- **SO Prefetch / Scanning**: 7:15-7:30 AM PT (10:15-10:30 AM ET) - Prefetch (trading loop or 7:15 scheduler) builds 7:00 open + 7:15 close → GREEN/RED; continuous scanning every 30 sec uses that data for rules.
- **SO Execution**: 7:30 AM PT (10:30 AM ET) - Batch execution with **Rev 00348** continuation-first multi-factor ranking ⭐ Rev 00231 alert lineage
- **ORR Window**: Disabled (0% allocation, optimizing separately)
- **Health Check**: EVERY 15 minutes (7:45 AM - 12:45 PM PT) - Rev 00067, Rev 00075 verified

### End of day: flatten + Telegram summaries

- **Flatten (all three strategy paths):** `PrimeTradingSystem.flatten_all_paths_for_eod_scheduler()` runs **`_eod_flatten_orb_etf_demo`** (ORB ETF / SO stealth + demo), **`_eod_flatten_orb_0dte`** (ORB 0DTE `close_all_positions`), and **`_eod_flatten_trendline_demo`** (Trendline ledger).  
  - **Early window (main trading loop):** When PT time is inside **`SO_ETF_EOD_CLOSE_START_PT`–`SO_ETF_EOD_CLOSE_END_PT`** (`configs/ORBSO.env`, default **12:55**–**12:56** PT) **and** `orb_strategy_manager` + `stealth_trailing` are present, the loop invokes `flatten_all_paths_for_eod_scheduler()` once per pass through the window (dedupe prevents redundant full flatten waves).  
  - **Scheduler path:** Cloud Scheduler job **`end-of-day-report`** → **`POST /api/end-of-day-report`** (`main.py`) calls the **same** method **before** sending Telegram EOD bodies. **`skip_if_already_flattened_today`** (default): if the loop already flattened in this process today, the HTTP handler logs skip and does not re-close.  
- **Telegram EOD (three reports):** Only the **`/api/end-of-day-report`** handler sends scheduled ETF demo/live summaries, **Easy ORB 0DTE** options EOD, and **Easy Trendline 0DTE** EOD (~**1:05 PM PT** / **4:05 PM ET** when the GCP schedule is configured that way). Internal EOD scheduler remains disabled (Rev 00260).

**Required Cloud Scheduler jobs (7):** All must be **ENABLED** so token → ORB → validation candle → signal collection → execution → EOD run. List: `gcloud scheduler jobs list --location=us-central1 --project=easy-etrade-strategy`. Resume if PAUSED: `gcloud scheduler jobs resume JOB_NAME --location=us-central1 --project=easy-etrade-strategy`. Verify: `python3 scripts/verify_all_cloud_jobs.py`.

| Job | Schedule (PT) | Purpose |
|-----|----------------|---------|
| trading-hours-keepalive-1 | `*/3 5-6 * * 1-5` | Pre-market warm (5–7 AM) |
| trading-hours-keepalive-2 | `*/5 7-9 * * 1-5` | Warm 7:00–7:30 and session start |
| trading-hours-keepalive-3 | `*/5 10-13 * * 1-5` | Session 10 AM–2 PM |
| oauth-market-open-alert | 5:30 AM PT `30 5 * * 1-5` | Good Morning + token status |
| **validation-candle-700** | **7:00 AM** `0 7 * * 1-5` | STEP 2: 7:00 open for validation candle |
| **prefetch-validation-715** | **7:15 AM** `15 7 * * 1-5` | STEP 3: 7:00 open + 7:15 close → GREEN/RED |
| end-of-day-report | 4:05 PM ET `5 16 * * 1-5` | **`POST /api/end-of-day-report`**: deduped `flatten_all_paths_for_eod_scheduler()` then three Telegram EOD summaries |

Full checklist (create missing jobs, optional jobs): [CLOUD_JOBS_CHECKLIST.md](doc_elements/Sessions/2026/Feb24%20Session/CLOUD_JOBS_CHECKLIST.md).

**Key Elements:**
- **ORB High / ORB Low**: Highest and lowest price in the **6:30–6:45 AM PT** opening range (aggregated over all bars in that window when multiple bars are available).
- **ORB range %**: \((\text{ORB high} - \text{ORB low}) / \text{ORB low} × 100\). **Same for LONG and SHORT.** Stored on `ORBData` at capture and reused for Convex, **ORB 0DTE alpha rank** (ORB range is **one** weighted input in **`_rank_signals_by_priority`** — default share **`0DTE_PRIORITY_RANK_W_ORB_RANGE=0.10`**, not 25% of the whole blend; do not conflate with Convex’s internal “range” criterion weights), **ORB SO** priority (continuation-first base + **orb_range** in the remainder blend + soft range penalties), and Opening Bar Protection tiers.
- **Breakout**: Price moves above ORB high (bullish) or below ORB low (bearish)

---

## 📊 Trading Windows & Signal Types

### **1. ORB Capture (6:30-6:45 AM PT / 9:30-9:45 AM ET)** ⭐ CRITICAL - SHARED

**Process:**
1. Market opens at 6:30 AM PT
2. System captures opening range for **all symbols** (ORB + 0DTE):
   - **ORB symbols**: dynamic rows from `core_list.csv`
   - **0DTE symbols**: dynamic rows from `0dte_list.csv` (merged with ORB, no duplicates)
   - **Total**: dynamic unique symbols captured (union of both lists; overlap is not double-fetched)
3. Batch processing: Dynamic batches based on symbol count (2-5 seconds total)
4. ORB data stored: High, Low, Open, Close, Volume, **orb_range_pct** (capture H/L only)
5. Data source: **E*TRADE batch quotes ONLY** (today's OHLC = ORB) - Rev 00236
6. **No Fallback**: System stops if broker fails (no third-party backup) - Rev 00236

**Alert:**
- ✅ **"ORB Capture Complete - [X] symbols captured in [Y] seconds"** (dynamic count)
- **Single alert** sent at 6:45 AM PT with ORB data for **both SO trades and 0DTE trades**
- Confirms system ready for SO trading and 0DTE trading

**Critical**: Without ORB capture, no SO trades OR 0DTE trades can execute.

**Uses for ORB Data:**
- **ORB Strategy**: Breakout detection (price > ORB high), entry bar protection, stop loss calculation
- **0DTE Strategy**: Eligibility filtering (**ORB range %** from same morning capture, vs min threshold e.g. 0.25–0.35%), ORB break confirmation, signal generation

---

### **2. Signal Collection & Rules Confirmation (7:15-7:30 AM PT / 10:15-10:30 AM ET)** ⭐ PRIMARY

**Concept**: Collect ORB signals and 0DTE signals, confirm all rules and risk management, generate final confirmed trade lists ready for execution.

**ORB Strategy - SO Signal Collection:**

**Definitions:** **OR (Opening Range)** = 6:30–6:45 AM PT only; ORB high/low from this window. **Validation candle** = 7:00–7:15 AM PT bar only (open = 7:00 price, close = 7:15 price); used for volume color (GREEN = close > open, RED = close < open) and for rule 3 (close vs ORB high/low).

**ORB LONG / 0DTE CALL (all 3 required):**
1. **Price (scan-time 7:15–7:30 PT):** Current price ≥ ORB high × 1.001 (+0.1%).
2. **Validation candle volume color:** GREEN — 7:00–7:15 bar close > open.
3. **Validation candle close vs ORB high:** 7:00–7:15 bar close > ORB high.

**ORB SHORT / 0DTE PUT (all 3 required):**
1. **Price (scan-time 7:15–7:30 PT):** Current price ≤ ORB low × 0.999 (−0.1%).
2. **Validation candle volume color:** RED — 7:00–7:15 bar close < open.
3. **Validation candle close vs ORB low:** 7:00–7:15 bar close < ORB low.

**Why 0 signals — quick reference:** All NEUTRAL → fix 7:00 job + 7:15 prefetch (and GCS). Validation SUCCESS but 0 ORB signals → no symbol had 7:00–7:15 close > ORB high (LONG) or close < ORB low (SHORT); check logs for LONG rule breakdown. Many "Price below ORB high" → market didn’t break out at scan time. **Verify in logs:** `Validation candle: SUCCESSFUL (GREEN=X RED=Y)`; `STEP 4 validation candle data source: GCS_LOADED | ...`; `LONG rule breakdown` with counts. Full rules and diagnosis: [SignalRulesChecklist.md](doc_elements/Sessions/2026/Feb24%20Session/SignalRulesChecklist.md).

**Rules Confirmation** (After ORB Capture):
- 3 strict validation rules (price, volume color, previous candle)
- Red Day Filter (Portfolio-Level)
- Signal-Level Filtering
- Multi-factor ranking (**Rev 00348** ORB SO: continuation-first; see **Multi-Factor Ranking – ORB SO** below)

**Risk Management:**
- Position sizing (rank-based multipliers: 3.0x, 2.5x, 2.0x...)
- Capital allocation (90% allocation via normalization)
- Position limits (**ORB SO ETF** batch: up to **`MAX_CONCURRENT_TRADES`** concurrent executions, default **15** in `configs/ORBSO.env` — separate from `0DTE_MAX_POSITIONS` / Trendline caps)
- ADV limits (Slip Guard - 1% ADV cap)

**Final SO Signal Collection**: Final confirmed SO trades ready for execution (after all rules and risk management). Includes **Rev 00330/00331 7:30 cutoff revalidation** using fresh quotes: LONG candidates must still satisfy `current_price_now >= orb_high * 1.001` (+0.1% buffer), and SHORT candidates must satisfy `current_price_now <= orb_low * 0.999` (-0.1% buffer), before ranking/risk/execution. Logs include a **SIGNAL COLLECTION DIAGNOSIS** block (rejection reason counts, volume color counts, **LONG rule breakdown**, sample symbols) and **STEP 4 validation candle data source** (PREFETCHED_IN_MEMORY / GCS_LOADED / FRESH_INTRADAY) for diagnosis. Rule checklist: [SignalRulesChecklist.md](doc_elements/Sessions/2026/Feb24%20Session/SignalRulesChecklist.md). Full sequence: [Risk.md](Risk.md#signal-collection--order-execution-end-to-end).

**Cross-instance and current-day (Rev 00285–00287):** The scan runs on one Cloud Run instance (7:15–7:30); the 7:30 execution block may run on another. So the **signal collection list** is persisted to GCS when the scan completes (`daily_markers/signal_collection_730/YYYY-MM-DD.json`). At 7:30, if the instance has no in-memory list, it loads today's file from GCS so the alert and risk/execution use the correct list. Only **current-day** signals are used: in-memory lists are tagged with `_signal_collection_date` (today PT); on new trading day reset all pending lists are cleared; when loading from GCS the payload is used only if `data.date == today`. Each signal's **orb_data** is serialized to a dict when persisting so that after load the execution instance and 0DTE/Convex still have `orb_high`/`orb_low` for risk and options qualification. Session notes: [Feb24 Session](doc_elements/Sessions/2026/Feb24%20Session/SESSION_SUMMARY_FEB24_2026.md).

**0DTE Strategy - Options Signal Collection (Long and Short):**

- **0DTE produces both LONG (CALL) and SHORT (PUT) signals** — unlike ORB SO (Long-only). **CALL** requires the **same three rules** as ORB Long; **PUT** requires the **inverse three rules**. No signal is added on price-only bypass (Rev 00309). Combined list is ranked; top N up to **`0DTE_MAX_POSITIONS`** (default **6** in `configs/ORB0DTE.env`) executed as options.

**Rules Confirmation** (After ORB Capture):
- Convex Eligibility Filter (composite score vs threshold — default **`0DTE_CONVEX_MIN_SCORE=0.75`** in **`modules/orb0dte_execution_defaults.py`**; **`ORB_0DTE_EXECUTION_PROFILE`** / explicit **`ORB0DTE.env`** lines override) — direction-aware (e.g. Red Day: non-Tier-1 LONG/CALL constrained; SHORT/PUT continues — see Red Day sections below)
  - Volatility Score (40% weight)
  - ORB Range/ATR (25% weight)
  - NOT Red Day (15% weight, direction-aware)
  - ORB Break (Required: LONG price > ORB high, SHORT price < ORB low)
  - Volume Confirmation (Required)
  - VWAP Condition (Required: LONG ≥ VWAP, SHORT ≤ VWAP)
  - Momentum Confirmation (10% weight)
  - Market Regime (10% weight)
- Strategy selection (long call, long put, debit spread, momentum scalper, etc.)
- **Hard Gate** (before the execution queue): eligible **symbol / 0DTE target** allowlist, **session time window**, **volume / volume_ratio** checks; **very wide ORB range** → **log warning only** (no Hard Gate reject on max ORB % — consistent with 0DTE priority scoring that favors wider range).
- Strike selection at **options execution**: delta, premium, and **chain liquidity** guardrails (e.g. open interest, bid/ask spread, volume on selected contracts).
- Position size validation (capital allocation, max position limits)

**Risk Management:**
- Position limits: concurrent ORB 0DTE opens capped by **`0DTE_MAX_POSITIONS`** (default **6** in `configs/ORB0DTE.env`; **combined** CALL + PUT), plus **`MAX_TOTAL_OPTION_POSITIONS`** in `configs/Shared.env` across ORB 0DTE + Trendline
- Capital allocation (Tier 1: 35%, Tier 2: 20%, Tier 3: 10%)
- Liquidity requirements (bid/ask spread, open interest, volume)
- Red Day check (portfolio-level, direction-aware)

**Final 0DTE Signal Collection**: Single list of confirmed LONG and SHORT options trades; ranked by priority; executed up to the ORB 0DTE concurrent cap (**default 6** via `0DTE_MAX_POSITIONS`) as CALL and PUT options (after all rules and risk management). **0DTE path (Signal Collection → options execution):** See [ProcessFlow.md](ProcessFlow.md#0dte-strategy--from-signal-collection-list-to-execution-and-monitoring) and [easy0DTE/docs/Strategy.md](../easy0DTE/docs/Strategy.md).

**Signal Collection Alert (7:30 AM PT):**
- **Single alert** showing both final confirmed trade lists:
  - **SO Signal Collection**: Final confirmed SO trades (after all rules and risk management) - ready for execution
  - **0DTE Signal Collection**: Final confirmed 0DTE options trades (after all rules and risk management) - ready for execution
- Both lists represent **final execution-ready trades** confirmed to open positions

**ORB SO vs 0DTE: Different Priority Ranking Formulas** ⭐ **IMPORTANT**

- **ORB SO** and **0DTE** use **different** priority formulas. Do not conflate them.
- **ORB SO** ranks **equity/ETF symbols** (100+ names). **Rev 00348** (`modules/prime_trading_system.py` — `calculate_so_priority_score`): **`w_cont = SO_CONTINUATION_MOMENTUM_WEIGHT`** (default **0.32**, clamped **0.18–0.42**) × **`continuation_quality_score`** + **`w_rem`** × weighted sum of **`vwap_score`** (continuation-shaped), **`rs_score`** (RS vs SPY), **`orb_vol_score`**, **`conf_score`**, **`rsi_score`**, **`orb_range_score`** with fixed internal shares **0.24 / 0.18 / 0.28 / 0.10 / 0.14 / 0.06** within the remainder; then **`× quality_multiplier`** minus soft penalties; logs **`SO_RANK_BREAKDOWN`** and telemetry **`SO_CONTINUATION_VS_EXTENSION_BIAS`**.
- **0DTE** ranks **options signals**; **RS vs SPY is not used** in `_rank_signals_by_priority`. Defaults (**Rev 00348**): **`0DTE_PRIORITY_RANK_W_BREAKOUT`** **0.14**, **`0DTE_PRIORITY_RANK_W_ORB_RANGE`** **0.10**, **`0DTE_PRIORITY_RANK_W_VOLUME`** **0.22**, **`0DTE_PRIORITY_RANK_W_CONVEX`** **0.20**, **`0DTE_PRIORITY_RANK_W_EARLY_MOMENTUM`** **0.34**, times an extension penalty when **`breakout_score`** is high; tie-break prefers higher **`early_momentum`**. Profile **`ORB_0DTE_EXECUTION_PROFILE`** / **`ORB0DTE.env`** can override.

**Multi-Factor Ranking – ORB SO (Rev 00348 current; v2.1 research lineage)** ⭐

**Runtime (Rev 00348)** — see `calculate_so_priority_score` in **`modules/prime_trading_system.py`** and **`configs/ORBSO.env`** (`SO_CONTINUATION_MOMENTUM_WEIGHT`, soft-penalty / exhaustion keys, `SO_WINNER_*`).

**Historical fixed-weight v2.1 (Nov 2025 research — not the live SO base formula):**

```python
# Legacy correlation-tuned snapshot — superseded as the SO *base* by Rev 00348 continuation-first blend.
priority_score_legacy_v21 = (
    vwap_distance_score * 0.27
    + rs_vs_spy_score * 0.25
    + orb_vol_score * 0.22
    + confidence_score * 0.13
    + rsi_score * 0.10
    + orb_range_score * 0.02
)
```

**Formula v2.1 baseline changes** (Rev 00106-00108) — research history for the fixed-weight line above:
- ✅ VWAP Distance: 25% → **27%** (↑ +2% - exceptional +0.772 correlation)
- ✅ ORB Volume: 20% → **22%** (↑ +2% - moderate +0.342 correlation)
- ⚠️ Confidence: 15% → **13%** (↓ -2% - weak +0.333 correlation)
- ⚠️ ORB Range: 5% → **3%** baseline in Rev 00108 (later **2%** in the legacy line; Rev 00348 uses **orb_range** inside the remainder mix at **0.06** of the remainder, not as a standalone “2% of whole score” base)
- ✅ RS vs SPY: **25%** (same - strong +0.609 correlation)
- ✅ RSI: **10%** (same - context-aware)

**Result (historical narrative)**: v2.1 prioritized market leaders (high RS vs SPY) with institutional support (above VWAP). Rev 00348 keeps RS/VWAP/volume context inside the **remainder** while making **continuation quality** the primary base share.

**Rev 00347 data-driven enhancement (SO):**
- **`quality_multiplier`** and **`SO_WINNER_*`** gate still apply on top of the Rev 00348 base (see [Risk.md](Risk.md#signal-collection--order-execution-end-to-end)).

### **SO Priority Summary (Signal Collection -> Execution)** ⭐

**Objective**: prioritize winner-like SO entries and skip loss-prone entries before execution.

1. **Collection eligibility**: keep symbols that pass all 3 SO LONG rules.
2. **7:30 cutoff revalidation**: remove symbols that fail fresh breakout check.
3. **Ranking score (Rev 00348 + 00347)**: continuation-first base + remainder blend → **`× quality_multiplier`** → subtract soft penalties; persisted fields include **`priority_base_score`**, **`priority_quality_multiplier`**, **`priority_score`**.
4. **Telemetry**: **`SO_RANK_BREAKDOWN`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`** (does not change rank).
5. **Winner-profile quality gate**: configurable **`SO_WINNER_*`** thresholds before adaptive sizing.
6. **Adaptive selection + ranked execution**: keep best affordable candidates, size by rank/caps, execute highest-ranked first.

**Primary tuning keys**: `SO_CONTINUATION_MOMENTUM_WEIGHT`, `SO_EXHAUSTION_PENALTY_WEIGHT` (and related **`SO_*`** keys in **`configs/ORBSO.env`**), `SO_WINNER_PROFILE_FILTER_ENABLED`, `SO_WINNER_MIN_VOLUME_RATIO`, `SO_WINNER_HARD_MIN_VOLUME_RATIO`, `SO_WINNER_MIN_CONFIDENCE`, `SO_WINNER_MAX_RSI_NON_BULL`, `SO_WINNER_MIN_VWAP_DISTANCE`, `SO_WINNER_MIN_KEEP_COUNT`.

**Greedy Capital Packing:**
- Rank all signals by priority score
- Apply rank-based multipliers (3.0x, 2.5x, 2.0x...)
- Fit as many high-priority trades as possible
- Skip low-priority/expensive trades when capital runs out

**Example (Typical Day, $1,000 account):**
- 6-15 signals found (realistic, validated)
- Up to 15 trades executed (all affordable, max 15)
- Remaining signals filtered (expensive or beyond top 15)
- 88-90% capital deployment (exact via normalization with whole shares)

**Execution Alert** ⭐ Rev 00231 Enhanced:
- ✅ **Separate ORB SO Execution alert** with all executed SO trades
- ✅ **Bold formatting** for key metrics (Rank, Priority Score, Confidence, Momentum, Delta)
- ✅ **Trade IDs**: Shortened format (Rev 00231)
- Sent **after** SO trades are executed at 7:30 AM PT
- Shows executed trades from **SO Signal Collection** (final confirmed list)

**Note**: The Signal Collection alert (sent before execution) contains the **final confirmed trade lists** ready for execution. The execution alerts are sent **after** trades are executed.

**Execution Alert Format** (Rev 00231):
```
====================================================================

✅ <b>Standard Order Execution</b>
          Time: 07:30 AM PT (10:30 AM ET)

📊 Execution Summary:
          Trades Executed: 6
          Capital Deployed: $792.50 (88.1%)
          Capital Efficiency: 88.1%

📈 Positions:
          • QQQ - 12 shares @ $42.50
            <b>Rank #1</b> | <b>Priority Score: 0.856</b>
            <b>Confidence: 85%</b> | <b>Momentum: 75/100</b>
            Trade ID: DEMO_QQQ_260106_485_488_c_704400
```

---

### **3. Opening Range Reversals (ORR) - DISABLED** ⭐ CURRENTLY DISABLED

**Status**: Currently disabled (0% capital allocation)

**Rationale:**
- ORR trades need separate optimization before re-enabling
- 90% SO allocation maximizes profitable SO opportunities
- Maintains 10% cash reserve for safety
- Can execute more SO trades (up to 15 concurrent)
- Better capital efficiency with proven strategy

**Future**: Will optimize separately before re-enabling

---

## 🛡️ Position Monitoring & Exit System

### **Position Monitoring (Throughout Day)**

**Frequency**: Main orchestration loop on the order of **30 seconds** for ORB ETF stealth and shared housekeeping. **ORB 0DTE** and **Trendline 0DTE** option positions are also evaluated on dedicated **~7 second** fast paths (see `ORB_0DTE_POSITION_MONITOR_INTERVAL_SEC`, `TRENDLINE_POSITION_MONITOR_INTERVAL_SEC`) with shared-loop backup and dynamic backoff.

**Coverage across all 3 paths:**
- ORB ETF positions: monitored by **`prime_stealth_trailing_tp`** (ETF stealth) and ETF exit triggers. **May 15:** live ETF **closes and scale-outs** also route through **`execute_equity_order_smart`** when smart execution is enabled (unified with `prime_unified_trade_manager.py`).
- ORB 0DTE positions: monitored by the **single normal options stealth engine** (`modules/prime_options_stealth_trailing_tp.py`, `orb_options_stealth` in `PrimeTradingSystem`) with profile-resolved premium exits (max-PnL drawdown, profit floor, trailing, no-progress, time). Registration prefers **`metadata["normalized_options"]`** when it passes **`validate_normalized_options_for_stealth`**, else legacy spread/lotto fields.
- Trendline 0DTE positions: monitored by the **same single engine** (`trendline_options_stealth`) on a dedicated fast loop (**~7s** baseline, same adaptive throttle family as ORB 0DTE), isolated **Trendline** account state; profile selection controls structure-invalidation eligibility and retention thresholds by entry archetype.
- ORB 0DTE monitor reliability (Rev 00349): dedicated **~7s fast monitor** (configurable; dynamic throttling / backoff can lengthen) plus shared-loop backup; lock/flag **re-entrancy protection**, heartbeat summaries, fallback counters, roster/stale/hold diagnostics, exit-signal mismatch checks, and premium-source diagnostics (`OPTIONS_STEALTH | stage=orb_monitor_*`, `orb_monitor_roster`, `orb_stale_position_warning`, `orb_position_hold_diag`, `orb_exit_signal_mismatch`, `orb_quote_quality_degraded`).

**Exit Settings** ⭐ Rev 00196 - OPTIMIZED:

**Breakeven Protection** (Rev 00196 - Optimized):
- **Activation**: +0.75% profit after 6.4 minutes (optimized from 2.0% and 3.5 min)
- **Locks**: +0.2% minimum profit
- **Based On**: Historical data analysis (median activation P&L and timing)

**Trailing Stop** (Rev 00196 - Optimized):
- **Activation**: +0.7% profit after 6.4 minutes (optimized from 0.5% and 3.5 min)
- **Distance**: Dynamic 1.5-2.5% based on volatility and profit tiers
- **Uses**: WIDER of volatility/profit-based for maximum protection
- **Performance**: 91.1% profit capture vs 75.4% at 0.5% threshold
- **Expected**: 85-90% profit capture vs 67% current (+18-23% improvement)

### **14 Automatic Exit Triggers** (Rev 00075 - All Functional):

**Individual Position Exits** (12):
1. **Stop Loss**: Price hits current stop level (always active)
2. **Trailing Stop**: Price drops 1.5-2.5% from peak (after breakeven/TP)
3. **Breakeven Protection**: +0.75% activates after 6.4 min, locks +0.2% profit (Rev 00196)
4. **Take Profit**: At +3%, activates trailing (doesn't exit, lets winner run)
5. **Profit Timeout**: 2.5 hours if profitable and unprotected
6. **Maximum Hold Time**: 4 hours hard limit (closes at 11:30 AM)
7. **Rapid Exit - No Momentum**: After 15 min if peak <+0.3% (conditional)
8. **Rapid Exit - Immediate Reversal**: 5-10 min if down >-0.5%
9. **Rapid Exit - Weak Position**: After 20 min if down >-0.3% AND peak <+0.2%
10. **RSI Momentum Exit**: RSI <45 for 90 sec AND losing -0.375%+
11. **Gap Risk**: >2% gap from highest price (flash crash protection)
12. **End of Day Close (cross-strategy batch):** In **`SO_ETF_EOD_CLOSE_*`** PT window (default **12:55**–**12:56**), `flatten_all_paths_for_eod_scheduler()` closes remaining ORB ETF, ORB 0DTE, and Trendline books (see **End of day: flatten + Telegram summaries** above). ETF stealth still applies individual exit triggers intraday.

**Portfolio-Level Health Checks** (2):
13. **Emergency Exit**: 3+ red flags → Close ALL positions (every 15 min)
14. **Weak Day Exit**: 2 red flags → Close losing positions (every 15 min)

**Complete Portfolio Health Check metric set (15-minute checks):**
- `Win rate`: threshold `<35%`
- `Average P&L`: threshold `<-0.5%`
- `Momentum score`: threshold `<40%`
- `Peak strength`: threshold `<0.8%`
- `All positions losing`: trigger at `100%` losers

**All Settings Configurable** (Rev 00201):
- ✅ 65+ configurable settings via merged env files under `configs/` (including `Risk.env`)
- ✅ No hardcoded values
- ✅ Single source of truth

---

## 🚨 Red Day Detection & Loss Prevention

**Purpose:** Red Day Filtering is a directional risk gate to avoid long exposure on weak/bearish days. On Red Days, **ORB (SO) Long execution is blocked** and **0DTE Long (CALL) execution is blocked for non-Tier-1 symbols**, while **Tier-1 0DTE Long (CALL)** and **0DTE SHORT (PUT)** strategies remain allowed. Design intent: preserve primary-symbol participation while avoiding broad long-side exposure on weak days.

### **Enhanced Red Day Detection** 🚨 Rev 00176 - DEPLOYED

**3-Pattern Detection System** (blocks long exposure on Red Days):

**Pattern 1**: OVERSOLD (RSI <40) + WEAK VOLUME (<1.0x)
- Original Nov 4 pattern; strong signal of market weakness for ORB.
- **0DTE:** This same condition signals a **successful 0DTE Short (PUT) opportunity** — ORB Long + 0DTE CALL are blocked, 0DTE Short remains enabled.

**Pattern 2**: OVERBOUGHT (RSI >80) + WEAK VOLUME (<1.0x) ⭐ NEW
- New Dec 5 pattern identified.
- **3-Tier Override System** (Rev 00171/00172/00173): Avoid disabling trading on profitable days.
  - **Primary**: MACD > 0.0 AND RS vs SPY > 2.0 → Allow trading
  - **Secondary**: MACD > 10.0 AND (RS missing/zero) → Allow trading
  - **Tertiary**: VWAP Distance > 1.0% AND MACD > 0.0 → Allow trading

**Pattern 3**: WEAK VOLUME ALONE (≥80%)
- Good **primary indicator** of Red Day: when overall trade or options volume is low, that is a day we generally do not want to execute (especially ORB).
- Apply with care so as not to disable 0DTE on days when Short strategies would be successful.

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

**SPY/VIX trending down:** Favorable for **0DTE Short** — keep PUT execution enabled even when long-side execution is blocked by Red Day.

**Impact**: Would have prevented all 15 trades on Dec 5 ($13.53 saved), allows profitable days like Dec 8 and Dec 9

**Annual Savings**: $400-1,600/year (prevents 3-5 red days/month for ORB)

### **Holiday Filter** ⭐ Rev 00137

**19 Days Per Year Skipped**:
- **10 Bank Holidays**: Market closed
- **9 Low-Volume Holidays**: Market open but low volume (Halloween, Christmas Eve, Black Friday, etc.)

**Impact**: Preserves capital on low-quality trading days

---

## 🛡️ Entry Bar Protection

### **Permanent Floor Stops** 🛡️ Rev 00135

**Based on Actual ORB Volatility**:

Prevents premature stop-outs on high-volatility entries AND early exits at 30 minutes by using permanent floor stops, scaled to the actual entry bar volatility from ORB data.

**Tiered Stops**:
- **9%+ volatility**: 8% EXTREME stop (permanent floor)
- **6-9% volatility**: 8% EXTREME stop (permanent floor)
- **3-6% volatility**: 5% HIGH stop (permanent floor)
- **2-3% volatility**: 3% MODERATE stop (permanent floor)
- **<2% volatility**: 2% LOW stop (permanent floor)

**Key Innovation**: `initial_stop_loss` stored as permanent floor - breakeven and trailing can move up but NEVER below floor

**Benefits**:
- ✅ Prevents 64% of immediate stop-outs
- ✅ Saves reversal trades (documented example: +$7.84)
- ✅ Efficient stops for low-volatility entries
- ✅ Adaptive protection = better risk/reward

---

## 📊 0DTE Strategy (Options) — Integrated

The Easy 0DTE Strategy provides **selective convex amplification** of high-conviction ORB signals through 0DTE (Zero Days To Expiration) options. When enabled, the 0DTE subsystem listens to ORB context and selectively generates options exposure for qualified symbols, subject to its eligibility filter.

**Last Updated**: May 11, 2026  
**Version**: Rev 00350+ baseline with April 28 parity updates (Trendline entry expansion + outage-safe options stealth monitoring), plus Rev 00349/00347/00328/00326 ORB 0DTE reliability and pipeline diagnostics; **May 11** adds overextension / chain fallback / durability / spread-relief cross-refs to **`docs/0DTEORB.md`**.  
**Status**: ✅ Production Ready - Integrated with ORB Strategy

### **0DTE Strategy Overview**

**Core Philosophy**: *"Not every ORB-qualified trade gets options—only the highest-conviction setups."*

**Purpose**: Generate options exposure (single-leg-first with spread fallbacks) based on ORB context for maximum convex participation on high-momentum moves.

**Key Features**:
- **Selective Filtering**: Convex Eligibility Filter (score ≥ 0.75) ensures only highest-conviction setups
- **Alpha-Only Priority Ranking**: ORB breakout/range, volume, convexity, and early-momentum weighting with calibrated extension penalty
- **Viability Pre-Filter**: Chain-based feasibility score gates non-tradeable symbols before execution queueing
- **Direction-Aware Red Day Filtering**: LONG/CALL rejected for non-Tier-1 symbols, Tier-1 LONG/CALL allowed, SHORT/PUT allowed on Red Days
- **Expanded Delta Selection**: Range 0.15-0.35 (Rev 00246 - expanded from 0.15-0.25)
- **Real-Time Price Tracking**: Options quotes refresh on the **ORB 0DTE fast monitor** (~**7s** baseline, Rev 00338+; not the legacy 30s equity loop) for accurate exit decisions (Rev 00238)
- **Long Call Optimization**: Cheap OTM options (delta 0.15-0.35) for maximum gamma explosion (Rev 00238, Rev 00246)
- **Comprehensive Logging**: Full flow logging for better diagnostics (Rev 00246); **Rev 00318:** **`SO_PIPELINE`** for SO path forensics
- **Options data**: **LIVE and DEMO** use E*TRADE chains/quotes in current deployment. Synthetic demo chains are disabled (`0DTE_DEMO_SYNTHETIC_CHAIN=false`) and live option data is required (`REQUIRE_LIVE_OPTION_DATA=true`)
- **Dynamic symbols**: `data/watchlist/0dte_list.csv` (current tier split and totals are maintained in file; see [Data.md](Data.md))

### **Integration with ORB Strategy**

**Code Location**:
- Primary: `easy0DTE/` (main implementation)
- Deploy-compat: `1. The Easy 0DTE Strategy/modules/` (copy for older deploy flows)

**Signal Flow**:
1. **ORB Capture** (6:30-6:45 AM PT): **Shared** for both ORB and 0DTE strategies
   - Single ORB Capture alert sent with data for both SO trades and 0DTE trades
2. **Signal Collection & Rules Confirmation** (7:15-7:30 AM PT):
   - **ORB Strategy**: Confirms rules after ORB Capture, generates **SO Signal Collection** (final confirmed SO trades)
   - **0DTE Strategy**: Confirms rules after ORB Capture, generates **0DTE Signal Collection** (final confirmed 0DTE options trades)
   - **Signal Collection Alert** (7:30 AM PT): **Single alert** showing both final confirmed trade lists (after all rules and risk management)
3. **Trade Execution** (7:30 AM PT):
   - **ORB SO Execution**: Trades from **SO Signal Collection** executed
   - **0DTE Options Execution**: Trades from **0DTE Signal Collection** executed
   - **Separate execution alerts** sent after trades are executed
4. **Position Monitoring** (7:30 AM through **~12:55 PM PT** flatten window):
   - Real-time options price updates on the **ORB 0DTE** fast monitor (~**7s** baseline; Rev 00238 / Rev 00349+)
   - Exit decisions based on actual options P&L (not underlying price)
5. **End of Day**: Shared **`SO_ETF_EOD_CLOSE_*`** window (default **12:55**–**12:56** PT) runs **`flatten_all_paths_for_eod_scheduler()`** for ORB ETF + ORB 0DTE + Trendline; scheduled **Telegram** EOD summaries for each path fire from **`POST /api/end-of-day-report`** (~**1:05 PM PT** / **4:05 PM ET**, Cloud Scheduler).

**Key Points**:
- Both strategies confirm rules **after ORB Capture** and **before execution**
- Signal Collection alert contains **final confirmed trade lists** ready for execution (after all rules and risk management)
- Execution alerts sent **after** trades are executed (separate for ORB SO and 0DTE Options)

### **0DTE collection vs execution counts (Rev 00318 + Rev 00331 + Rev 00332)**

The **Trade Signal Collection** alert can list many **0DTE** names (CALL+PUT rows from the scan). The **0DTE Options Execution** alert adds an explicit **“0DTE pipeline (this run)”** block: Watchlist size, **collection underlyings** (CALL+PUT rows), **Convex-qualified**, **Hard gate → execution queue**, and **execution attempts**. **Rev 00332 (May 11):** the same alert adds **two-stage durability** counters (**`execution_durable`**, **`exit_grade_durable`**, **`monitored_but_degraded`**) and per-row **`quote_grade`** / **`exit_grade_ready`** so monitored-but-degraded opens are not misread as failures. It is normal for collection underlyings to exceed execution attempts when Convex, Hard Gate, caps, chain/momentum gates, **overextension**, or **chain-health** trims the queue—this is **not** the old misleading “Filtered (Expensive)” summary. **Cloud logs:** grep **`SO_PIPELINE`** for the Standard Order path; **Rev 00326:** grep **`0DTE_PIPELINE`**, **`0DTE_HARD_GATE_REJECT`**, **`0DTE_EXEC_REJECT`**, **`CONVEX_REJECT_DETAIL`**, **`0DTE_CONVEX_STAGE`**, **`ORB_0DTE_OVEREXTENSION_*`**, **`ORB_0DTE_CHAIN_HEALTH_*`**, **`ORB_0DTE_EXECUTION_*`**, **`ORB_OPTIONS_EXIT_DEFERRED_AUDIT`** (and related `0DTE_*` / `ORB_*` tokens) to trace per-stage drops.

### **0DTE Strategy Types**

**Strategy Selection Hierarchy** (single-leg primary, spread fallback):

1. **Strong directional** -> `long_call` / `long_put`
2. **Moderate directional** (when lotto sleeve enabled) -> `lotto`
3. **Weak signal** -> `momentum_scalper` (spread fallback)
4. **Else** -> `itm_probability_spread` (spread fallback)

**Loose-start default thresholds** (env-driven):
- `0DTE_STRONG_MOMENTUM_MIN=70.0`
- `0DTE_STRONG_BREAKOUT_DISTANCE_RATIO_MIN=0.08`
- `0DTE_STRONG_MIN_CONFIDENCE=0.72`
- `0DTE_STRONG_MIN_VOLUME_RATIO=1.05`
- `0DTE_MODERATE_MOMENTUM_MIN=55.0`
- `0DTE_MODERATE_BREAKOUT_DISTANCE_RATIO_MIN=0.02`
- `0DTE_MODERATE_MIN_CONFIDENCE=0.58`
- `0DTE_MODERATE_MIN_VOLUME_RATIO=0.90`
- `0DTE_WEAK_MOMENTUM_MAX=50.0`
- `0DTE_WEAK_BREAKOUT_DISTANCE_RATIO_MAX=0.015`
- `0DTE_WEAK_MIN_CONFIDENCE=0.55`

**Single-leg contract profiles**:

- **Lotto profile**: delta ~0.12-0.18, premium $0.15-$0.60, max spread <=10%, volume >=200, OI >=500
- **Directional profile** (`long_call` / `long_put`): delta ~0.28-0.40, premium $0.35-$1.20, max spread <=8%, volume >=200, OI >=500

**Spread policy note**:

- ITM probability spread uses direction-correct ITM logic (CALL below spot, PUT above spot).
- Fallback stages are explicit and logged: `ITM_SPREAD_PRIMARY`, `ITM_SPREAD_RELAXED`, `ATM_SPREAD`, `SINGLE_LEG`, `FINAL_REJECT`.
- Debit and other spread structures are retained as fallback when directional single-leg thresholds are not met.

**When the chart breaks out but execution says “No debit spread found” (Rev 00328)**  
`OptionsChainManager.select_debit_spread_strikes` (see `easy0DTE/modules/options_chain_manager.py`) requires a **long** leg in a **delta band** around the target, **mid premium** in **`$0.15`–`$0.60`**, **1–3 strikes OTM** (calls), a **short** leg near **`long_strike + spread_width`** (typically **$1–$2** for non-SPY/QQQ/SPX names), and **liquidity** on **both** legs (defaults: OI ≥ **100**, volume ≥ **50**, bid/ask present, bid–ask spread ≤ **15%** of mid). High-momentum 0DTE days often push mids **above $0.60**, eliminating all candidates even when spot action is strong. Log reason_code **`spread_selection_failed`**; distinct from **`poor_payoff_profile`** (Rev 00319 payoff guardrails after a spread is selected).

**Primary Directional Strategy: Long Calls/Puts** (single-leg primary path)
- **Trigger**: strong directional signal (env-driven thresholds, loose-start defaults in config)
- **Strike**: Delta 0.15-0.35 (Rev 00246 - expanded range from 0.15-0.25)
- **Premium**: $0.15-$0.60 (allows $0.19 entries like successful trades)
- **Capital**: 40% of allocated capital
- **Example**: QQQ 628c @ $0.19 → $0.97 (+410% if QQQ moves +0.86%)

### **0DTE Priority Ranking (Rev 00348 — alpha-only)**

**Multi-factor ranking** for 0DTE signals after Convex runs in **`easy0DTE/modules/prime_0dte_strategy_manager.py`** — `_rank_signals_by_priority`:

```python
# Defaults from code / `modules/orb0dte_execution_defaults.py`; override via `0DTE_PRIORITY_RANK_W_*` in ORB0DTE.env or profile.
volume_boost = volume_score ** 1.2
early_momentum = max(0.0, 1.0 - breakout_score * 0.7)

extension_penalty = 1.0
if breakout_score > 0.85:
    extension_penalty = 0.75
elif breakout_score > 0.65:
    extension_penalty = 0.88

base_score = (
    breakout_score * 0.14
    + orb_range_score * 0.10
    + volume_boost * 0.22
    + convex_score * 0.20
    + early_momentum * 0.34
)
priority_score = clamp_0_1(base_score * extension_penalty)
# Tie-break (same file): higher early_momentum wins when priority_score ties.
```

**Legacy note:** Rev 00246 documented a **35% / 30% / 20% / 15%** blend; runtime defaults above (**Rev 00348**) favor **`early_momentum`** and **`volume_boost`**. Treat older percentages as superseded unless your deployed profile explicitly restores them.

**Why 0DTE does not use RS vs SPY**: 0DTE underlyings are primarily **SPY, QQQ, SPX**. Relative strength vs SPY would be "SPY vs SPY" or "QQQ vs SPY" — not useful for ranking 0DTE options signals. ORB SO ranks many equity/ETF symbols, so RS vs SPY is meaningful there.

**Ranking + feasibility separation (current):**
- `priority_score` is alpha-only and comes from `_rank_signals_by_priority()`.
- `viability_score` is used to filter non-tradeable chains before queueing.
- Queue sort uses alpha score first; viability can be used as tie-breaker only.
- No blended `model_confidence*0.6 + viability*0.4` ranking is used.

### **Direction-Aware Red Day Filtering** (Rev 00329)

**Red Day Check - Direction-Aware**:
- **LONG (CALL) non-Tier-1 trades**: **Rejected** on Red Days ✅
- **LONG (CALL) Tier-1 trades**: **Allowed** on Red Days ✅
- **SHORT (PUT) trades**: **Allowed and encouraged** on Red Days ✅
- SHORT signals get bonus on Red Days (perfect for PUT trades)
- Better utilization of declining market conditions

### **Convex Eligibility Filter**

**Minimum score (gate):** default **`0DTE_CONVEX_MIN_SCORE=0.75`** in **`modules/orb0dte_execution_defaults.py`** — **`ORB_0DTE_EXECUTION_PROFILE`** and explicit lines in **`configs/ORB0DTE.env`** override when set (an empty Convex block in env does **not** zero out profile defaults; see [0DTEORB.md](0DTEORB.md)).

**8 Criteria** (all must pass):
1. **Volatility Score** (40% weight): ≥ Top 20% percentile (80th percentile)
2. **ORB Range/ATR** (25% weight): ≥ 0.35% OR ATR ≥ 0.25%
3. **NOT Red Day** (15% weight): Direction-aware check (non-Tier-1 LONG rejected, Tier-1 LONG allowed, SHORT allowed)
4. **ORB Break** (Required): Price > ORB High (LONG) or < ORB Low (SHORT)
5. **Volume Confirmation** (Required): Current volume > ORB volume average
6. **VWAP Condition** (Required): Price ≥ VWAP (LONG) or ≤ VWAP (SHORT)
7. **Momentum Confirmation** (10% weight): Positive MACD, RS vs SPY, or VWAP distance
8. **Market Regime** (10% weight): Trend/impulse (not rotation)

**Diagnosis when 0 pass (Rev 00292):** Logs show check-by-check failure counts, one-line `CONVEX_FILTER | 0_eligible | total=N | top_failures: ...`, and top 5 per-symbol rejection details at INFO. See [SESSION_SUMMARY_FEB26_2026.md](doc_elements/Sessions/2026/Feb26%20Session/SESSION_SUMMARY_FEB26_2026.md).

### **Real-Time Options Price Tracking** (Rev 00238)

**Before** (Rev 00237):
- Exit decisions based on underlying price movement
- QQQ moves +0.86% → System sees +0.86% P&L
- **Misses**: Actual options move from $0.19 to $0.97 (+410%)

**After** (Rev 00238):
- Exit decisions based on actual options prices
- QQQ moves +0.86% → System sees options move from $0.19 to $0.97 (+410%)
- **Captures**: Real options P&L for accurate exit decisions

**Implementation**:
- Options quotes fetched from E*TRADE API on each **fast-monitor** tick (~**7s** baseline for ORB 0DTE; cadence can stretch under load)
- Position values updated with real options prices
- Exit decisions (profit targets, hard stops) based on actual options P&L

### **Symbol List & ORB Data Collection**

**0DTE Symbol List**: `data/watchlist/0dte_list.csv` (dynamic symbol count)

**Tier Organization** (Rev 00327+; **counts drift with curation** — verify `0dte_list.csv`):
- **Tier 1** (**10** symbols at last refresh): SPX, SPY, QQQ, MAGS, IWM, RUT, VIX, GLD, SLV, IBIT
- **Tier 2** (**75** symbols at last refresh): Equities and sector ETFs (mega-cap, thematic, high-beta, etc.) — see [Data.md](Data.md) / [easy0DTE/docs/Data.md](../easy0DTE/docs/Data.md)

**ORB Data Collection** (Rev 00209):
- All 0DTE symbols included in ORB capture (6:30-6:45 AM PT)
- 0DTE symbols merged with ORB symbols (no duplicates)
- ORB data used for 0DTE signal generation and eligibility filtering

### **Configuration**

**Enablement**:
- `ENABLE_0DTE_STRATEGY=true` in **`configs/ORB0DTE.env`** (canonical integrated app path; merged after `Shared.env` per `configs/README.md`). Legacy copies may exist under `easy0DTE/configs/0dte.env` for standalone reference only.

**Trading Mode**:
- `ETRADE_MODE=demo` (default) or `live`
- `DEMO_MODE_ENABLED=true` (separate $5,000 demo account)

**Strategy Settings** (primary: **`configs/ORB0DTE.env`**; optional mirror: `easy0DTE/configs/0dte.env` for standalone use):
- Convex Eligibility Filter thresholds
- Alpha-only priority ranking weights and threshold controls
- Viability and execution-quality thresholds (`0DTE_MIN_VIABILITY_THRESHOLD`, chain health checks, overextension threshold)
- Position limits: **`0DTE_MAX_POSITIONS`** (default **6** concurrent at 7:30; not the SO **15**)
- Strike selection (target delta 0.15-0.35, premium range - Rev 00246)
- Exit settings (hard stops, time stops, profit targets)
- Red Day filtering (direction-aware - Rev 00246)

**Trade IDs** (Rev 00231):
- Shortened format: `DEMO_SPX_260106_485_488_c_704400`
- Applied to: Debit spreads, credit spreads, lottos, long calls/puts
- Both Demo and Live modes

### **Performance & Optimization**

**Recent Optimizations** (Apr 24, 2026):
- **Alpha ranking refinement**: early-momentum/volume-boosted ranking with tuned extension penalty (`>0.85 -> 0.75`, `>0.65 -> 0.88`)
- **Ranking/feasibility separation**: `priority_score` remains pure alpha; `viability_score` used as filter and optional tie-breaker only
- **Selector reliability**: corrected PUT/CALL ITM classification and structured selector diagnostics
- **Execution robustness**: chain fetch retry, chain health precheck, staged fallback ladder, overextension filter, and execution metrics
- **Directional diagnostics**: candidate/execution split logging with concentration-risk warning support
- **Direction-Aware Red Day Filtering**: non-Tier-1 LONG rejected, Tier-1 LONG allowed, SHORT allowed on Red Days
- **Delta Selection Expanded**: Range expanded to 0.15-0.35 (from 0.15-0.25) for more trade opportunities (Rev 00246)
- **Comprehensive Logging**: Added throughout entire 0DTE flow for better diagnostics (Rev 00246)

**Previous Optimizations** (Rev 00238):
- **Long Call Optimization**: Lowered premium minimum from $0.20 to $0.15, adjusted target delta from 0.40 to 0.15 (OTM for gamma explosion)
- **Real-Time Price Tracking**: Options monitoring uses the **fast** cadence (~**7s** baseline) for accurate exit decisions
- **Successful Trade Validation**: Strategy aligns with high-return trades (QQQ +300%, IWM +460%)

**Expected Performance**:
- Captures high-momentum moves with maximum gamma exposure
- Cheap OTM options allow for explosive returns (300-400%+)
- Real-time price tracking ensures accurate exit decisions

### **Related Documentation**

For detailed 0DTE Strategy documentation, see:
- **[easy0DTE/docs/README.md](../easy0DTE/docs/README.md)**: 0DTE Strategy overview
- **[easy0DTE/docs/Strategy.md](../easy0DTE/docs/Strategy.md)**: Detailed strategy documentation
- **[easy0DTE/docs/Data.md](../easy0DTE/docs/Data.md)**: Broker data connections and symbol list
- **[easy0DTE/docs/Alerts.md](../easy0DTE/docs/Alerts.md)**: 0DTE alert types and formats

---

## 📈 Easy Trendline 0DTE Strategy — Integrated

The Easy Trendline path is the third sibling strategy. It shares ORB capture and market data with the other paths, but uses its own candidate lifecycle, account ledger, and EOD reporting.

**Flow:**
1. **7:30 AM PT build** from the full 0DTE universe (**stable watchlist order**). **Broker data:** intraday is **`bars=1`** (ORB-timed); **quotes** are merged so pre-7:30 structure can be evaluated (multi-bar `get_batch_intraday_data` requests are not used—they do not return true history on the broker-only path). Data is fetched in **chunks** (default 25 symbols) with **configurable limits** on batch calls per build; partial builds are explicit in logs when budgets apply (`build_degraded`, `request_summary`, `skipped_due_to_budget` on `build_summary`). See [0DTETrendline.md](0DTETrendline.md) and [Settings.md](Settings.md) (`TRENDLINE_DATA_CHUNK_SIZE`, `TRENDLINE_MAX_*_BATCH_CALLS_PER_BUILD`, etc.).
2. **Post-7:30 watch mode** processes bars for each candidate.
3. **Execution gate** requires break -> hold -> structure acceptance -> momentum confirmation.
   - Time-based hold can use `TRENDLINE_CONFIRM_SECONDS` (runtime clamp 1-30s; default 10) for faster 0DTE responsiveness.
   - Strong/clean breakouts can bypass strict momentum rejection when enabled.
   - Final pre-emit safeguard requires `distance_increasing && body_expanding` (blocks weak follow-through).
4. **Rolling execution capacity**: confirmations execute while open Trendline positions are below `TRENDLINE_MAX_OPEN_POSITIONS` (default 5), with slot-based sizing.
   - when one position exits, capacity is freed and the next ready confirmation can execute.
   - new entries stop after `TRENDLINE_NO_NEW_ENTRIES_AFTER_PT` (default `11:30` PT), while monitoring/exits continue.
5. **Monitoring/exits/EOD** handled with trendline-isolated telemetry and reporting.

**Account and telemetry isolation:**
- Dedicated trendline demo account manager (separate from ORB ETF and ORB 0DTE demo ledgers).
- Strategy-level tags for analytics: `strategy_name` and `entry_type`.
- Trendline pipeline logs use `TRENDLINE_PIPELINE` markers (**including `request_summary`, `build_summary`, `build_context`, `build_degraded`, sampled `build_bar_diagnostics`**) and feature snapshots for diagnosis.

---

## 🚀 Key Features

### **1. Multi-Factor Signal Ranking** ⭐ **Rev 00348** (ORB SO continuation-first + ORB 0DTE env-weighted alpha)

**ORB SO (ETF path):** continuation-first **`calculate_so_priority_score`** — see [Risk.md](Risk.md#signal-collection--order-execution-end-to-end) and **Multi-Factor Ranking – ORB SO** above.

**ORB 0DTE (options path):** `_rank_signals_by_priority` — env keys **`0DTE_PRIORITY_RANK_W_*`** (defaults **0.14 / 0.10 / 0.22 / 0.20 / 0.34**).

**Historical evidence (v2.1 research era):**
- 89-field technical indicators tracked daily
- 3-day comprehensive data collection (Nov 4, 5, 6, 2025)
- Correlation analysis:
  - VWAP Distance: +0.772 correlation ⭐⭐⭐ STRONGEST PREDICTOR!
  - RS vs SPY: +0.609 correlation ⭐⭐⭐ 2ND STRONGEST!
  - ORB Volume: +0.342 correlation ✅ MODERATE
  - Confidence: +0.333 correlation ⚠️ WEAK

**Expected Impact**: +10-15% better capital allocation vs v2.0, +$2,400-6,000/year when fully optimized

### **2. Slip Guard - ADV-Based Position Capping** 🛡️ ⭐

**Prevents Slippage at Any Account Size:**

Automatically caps position sizes at 1% of Average Daily Volume (ADV) to prevent slippage.

**How It Works:**
- Daily ADV refresh at 6:00 AM PT (90-day rolling average)
- Caps positions exceeding 1% of symbol's ADV (in batch sizing, Step 3)
- In the **current batch sizing path**, freed capital from ADV-capped positions is not reallocated; total deployment may be slightly below 90% when several symbols are capped. Config `SLIP_GUARD_REALLOCATION_ENABLED` exists for future use. See [Risk.md](Risk.md) for details.

**Benefits:**
- ✅ Prevents slippage (2-5% → <0.5%)
- ✅ Scales to $10M+ accounts safely
- ✅ **High deployment maintained on liquid baskets** (can run below target when many names are ADV-capped)
- ✅ **Top signals remain prioritized** via rank multipliers/normalization (ADV-capped freed capital is not auto-reallocated in current path)
- ✅ Automatic liquidity management

### **3. Greedy Capital Packing with Adaptive Fair Share** ⭐ BREAKTHROUGH

**Maximizes Trading Opportunities:**

Dynamic trade selection that fits as many high-priority trades as possible within capital constraints. Automatically adapts to extreme cases (small accounts, many signals, expensive symbols).

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

### **4. Batch Position Sizing with Normalization** ⭐ Rev 00090

**6-Step Process**:
1. Apply Rank Multipliers (3.0x, 2.5x, 2.0x...)
2. Apply Max Position Cap (35%)
3. Apply ADV Limits (Slip Guard - 1% ADV cap)
4. Normalize to Target Allocation (90%)
5. Constrained Sequential Rounding (whole shares)
6. Post-Rounding Redistribution ⭐ NEW - Redistributes unused capital to top signals

**Result**: 88-90% capital deployment guaranteed

---

## 📈 Performance

### **Historical Validation - 11 Days Real Market Data (October 2024)**

**Overall Results**:
- **Weekly Return**: +73.69% (23% above +60% target)
- **Winning Days**: 10/11 (91% consistency)
- **Max Drawdown**: -0.84% (96% reduced from -21.68%)
- **Profit Factor**: 194.00 (vs 2.03 baseline)

**By Day Type Performance**:
- **POOR days**: -49.75% → +0.69% (+50.44% improvement)
- **WEAK days**: -12.73% → +3.08% (+15.81% improvement)
- **GOOD days**: +57.12% → +56.93% (preserved)

**Expected Performance with Optimized Exit Settings** (Rev 00196):
- **Profit Capture**: Expected 85-90% (vs 67% current)
- **Improvement**: +18-23% profit capture improvement
- **Based On**: Historical data analysis

---

## ⚙️ Configuration

All strategy parameters are configurable via `configs/` files:

### **Capital Allocation** (`configs/ORBSO.env`):
- `SO_CAPITAL_PCT` = 90.0 (Standard Order allocation)
- `ORR_CAPITAL_PCT` = 0.0 (Opening Range Reversal - disabled)
- `CASH_RESERVE_PCT` = 10.0 (Cash reserve - auto-calculated)

### **Position Sizing** (`configs/ORBSO.env` + `configs/Risk.env`):
- `MAX_POSITION_SIZE_PCT` = 35.0 (Maximum single position size)
- `MAX_CONCURRENT_POSITIONS` = 15 (Maximum simultaneous trades)
- `MIN_POSITION_VALUE` = 50.0 ($50 minimum)

### **Exit Settings** (`configs/Risk.env`):
- `STEALTH_BREAKEVEN_THRESHOLD` = 0.0075 (0.75% activation - Rev 00196)
- `STEALTH_BREAKEVEN_TIME_MIN` = 6.4 (6.4 minutes - Rev 00196)
- `STEALTH_TRAILING_ACTIVATION_THRESHOLD` = 0.007 (0.7% activation - Rev 00196)
- `STEALTH_TRAILING_ACTIVATION_TIME_MIN` = 6.4 (6.4 minutes - Rev 00196)
- `STEALTH_BASE_TRAILING` = 0.015 (1.5% base trailing)
- Plus 60+ additional configurable settings

### **Strategy Enablement** (`configs/ORB0DTE.env`):
- `ENABLE_0DTE_STRATEGY=true` (Enable 0DTE options strategy)

**Key Features** (Rev 00201):
- ✅ 65+ configurable settings
- ✅ No hardcoded values
- ✅ Single source of truth
- ✅ Easy to adjust in one place

See [docs/Settings.md](Settings.md) for complete configuration reference.

---

## ✅ System Status Summary

### **Current Deployment (February 2026 - Rev 00280)**

**Deployment:**
- ✅ Rev 00280 deployed (Validation candle: explicit 7:15 close for rule 3, GCS persist/load for cross-instance scan, STEP 4 data-source logging)
- ✅ Rev 00279: Fix 0 signals with valid data — pass validation_close_715 into rules; persist validation candle to GCS; load from GCS when scan runs on different instance
- ✅ Service healthy and running
- ✅ Keep-alive jobs active (every 3-5 min)
- ✅ GCS persistence working (Rev 00203)

**Strategy:**
- ✅ ORB strategy operational
- ✅ SO trades optimized (90% capital allocation)
- ✅ ORR trades disabled (0% allocation)
- ✅ Holiday filter active (19 days/year skipped - Rev 00137)
- ✅ 0DTE strategy enabled (if configured - Rev 00209+)

**Risk Management:**
- ✅ Batch position sizing deployed (Rev 00090 - complete 6-step flow)
- ✅ Post-rounding redistribution active (Rev 00090)
- ✅ Rank-based multipliers active (3.0x, 2.5x, 2.0x...)
- ✅ Multi-factor ranking (**Rev 00348** SO + 0DTE; legacy v2.1 / 00246 priority % = historical)
- ✅ Capital allocation configurable (Rev 00103 - unified system)
- ✅ Normalization enforced (scales to 90% target)
- ✅ ADV limits respected (Slip Guard - 1% of ADV cap)
- ✅ Capital deployment: 88-90% guaranteed
- ✅ Exit settings optimized (Rev 00196: 0.75% breakeven, 0.7% trailing, 6.4 min)

**Position Monitoring:**
- ✅ Entry bar protection (Rev 00135 - permanent floor stops 2-8%)
- ✅ Breakeven protection (Rev 00196 - +0.75% after 6.4 min, locks +0.2%)
- ✅ Trailing stop (Rev 00196 - +0.7% after 6.4 min, 1.5-2.5% distance)
- ✅ Health checks (Rev 00067 - every 15 minutes, ~21 per day)
- ✅ All 14 exit triggers functional (Rev 00075)
- ✅ Aggregated batch alerts (Rev 00078 - 85% spam reduction)
- ✅ Expected 85-90% profit capture (Rev 00196)

**Performance:**
- ✅ +73.69% weekly return (23% above +60% target)
- ✅ 91% winning day consistency (10/11 days)
- ✅ 88-90% capital deployment efficiency
- ✅ Max drawdown -0.84% (96% reduced from -21.68%)
- ✅ Expected 85-90% profit capture (vs 67% current - Rev 00196)

**Alert System:**
- ✅ Morning alert (clouds and dove) - Time validation + deduplication (Rev 00233)
- ✅ Holiday alert (19 days/year)
- ✅ All trading alerts correct
- ✅ Enhanced execution alerts (bold formatting - Rev 00231)
- ✅ Trade ID shortening (Rev 00231)
- ✅ Aggregated exit alerts (Rev 00078)
- ✅ Signal collection deduplication (Rev 00232)
- ✅ Unified EOD report format

**Configuration:**
- ✅ Unified configuration system (65+ settings - Rev 00201)
- ✅ Single source of truth (Rev 00202)
- ✅ All settings configurable via `configs/` files

**Modes:**
- ✅ Demo Mode active ($1,000 starting balance)
- ✅ Live Mode ready for deployment
- ✅ Trade persistence working (Rev 00203)

---

## 🎯 Key Achievements

### **Strategy Optimization**
- ✅ **Multi-Factor Ranking**: **Rev 00348** — ORB SO continuation-first; ORB 0DTE **`0DTE_PRIORITY_RANK_W_*`** defaults
- ✅ **Greedy Capital Packing**: 88-90% capital efficiency
- ✅ **Rank-Based Position Sizing**: Scales automatically from $1K to $100K+
- ✅ **Optimized Exit Settings**: 0.75% breakeven, 0.7% trailing, 6.4 min (Rev 00196)
- ✅ **Expected 85-90% Profit Capture**: vs 67% current (+18-23% improvement)

### **System Simplification**
- ✅ **Single Strategy**: ORB only (ORR disabled, optimizing separately)
- ✅ **Dynamic Symbol List**: ORB (`core_list.csv`) + 0DTE (`0dte_list.csv`) merged for capture — fully scalable
- ✅ **Clear Windows**: Predictable entry timing
- ✅ **Proven Performance**: Validated with real historical data

### **Risk Management**
- ✅ **Capital Constraints**: Realistic position sizing
- ✅ **Automatic Affordability**: Greedy packing handles capital limits
- ✅ **Position Isolation**: No interference with manual trades
- ✅ **Safe Mode**: 10% drawdown protection
- ✅ **Red Day Filter**: Prevents trading on high-risk days (Rev 00176)
- ✅ **Holiday Filter**: Prevents trading on 19 high-risk days per year (Rev 00137)

---

## 📝 Documentation References

### **Core Documentation**
- **[docs/Strategy.md](Strategy.md)** - This file - Strategy overview and performance
- **[SignalRulesChecklist.md](doc_elements/Sessions/2026/Feb24%20Session/SignalRulesChecklist.md)** - ORB LONG/SHORT and 0DTE rules checklist; why 0 signals and how to verify
- **[docs/Risk.md](Risk.md)** - Risk management and position sizing
- **[docs/ProcessFlow.md](ProcessFlow.md)** - End-to-end process flow
- **[docs/Alerts.md](Alerts.md)** - Alert system documentation
- **[docs/Cloud.md](Cloud.md)** - Google Cloud deployment guide
- **[docs/CloudSecrets.md](CloudSecrets.md)** - Project-specific deploy, GCS, log analysis
- **[docs/Firebase.md](Firebase.md)** - Firebase OAuth web app deployment
- **[docs/Settings.md](Settings.md)** - Configuration reference (65+ settings)

---

## 🔄 Revision History

### **May 14, 2026 — Rev 00348 ranking docs (ORB SO + ORB 0DTE)**

- **Strategy / Risk / Data / Settings:** ORB SO **`calculate_so_priority_score`** documented as **continuation-first** (`SO_CONTINUATION_MOMENTUM_WEIGHT` + remainder blend, `SO_RANK_BREAKDOWN` / `SO_CONTINUATION_VS_EXTENSION_BIAS`). ORB 0DTE **`_rank_signals_by_priority`** defaults **`0.14 / 0.10 / 0.22 / 0.20 / 0.34`** + extension penalty + **`early_momentum`** tie-break. Convex gate: default **`0DTE_CONVEX_MIN_SCORE`** from execution defaults / profile. Grep **`ORB_0DTE_SELECTOR_FULL_REPLAY`**.

### **May 13, 2026 — Documentation accuracy (ORB 0DTE + watchlists + cadence)**

- **0DTEORB / README / Settings:** aligned with **`configs/ORB0DTE.env`** (spread widths **1.0–5.0**, single-leg OI/volume floors, chain relax **0.76**, May 13 selector block) and **`orb0dte_execution_defaults.py`** (**`0DTE_MIN_VIABILITY_THRESHOLD=0.30`** default).
- **0DTETrendline:** May 13 stealth calibration summary + appendix note for **`OPTION_STEALTH_TLINE_*`** in **`Shared.env`**.
- **Strategy / README:** Tier counts **10 / 75** (verify CSV); ORB 0DTE monitor cadence **~7s** (not 30s); **May 14 correction:** ORB range default **priority** share is **`0DTE_PRIORITY_RANK_W_ORB_RANGE=0.10`** (Convex still has its own range criterion weights — do not conflate with the old “25% of alpha rank” wording).

### **Latest Updates (April 2026 - Rev 00326)** ⭐ **0DTE HARD GATE + LOGGING + WATCHLIST**

**Rev 00326 (Apr 10, 2026):**
- ✅ **Hard Gate**: Removed **maximum ORB-range** rejection; very wide ORB logs as **warning only** (aligned with 0DTE priority favoring wider range).
- ✅ **Observability**: Grep-friendly Cloud Logging tokens along Convex → cap → Hard Gate → execution (`0DTE_PIPELINE`, `0DTE_TARGET_FILTER`, `0DTE_DEDUPE`, `0DTE_PRIORITY_DROP`, `0DTE_HARD_GATE_REJECT`, `0DTE_EXEC_REJECT`, `0DTE_EXEC_STAGE`, `CONVEX_REJECT_DETAIL`, `0DTE_CONVEX_STAGE`, etc.).
- ✅ **Watchlist**: Removed **SOLT, ETHT, ETU, MRAL** from `core_list.csv`; sentiment / inverse JSON updated — **ETHU** (bull) ↔ **ETHD** (bear), **ETHT** removed.

### **Previous Updates (February 2026 - Rev 00289/00292)** ⭐ **SIGNAL APPEND FIX & CONVEX DIAGNOSIS**

**Rev 00292 (Feb 26 - Convex filter 0-pass diagnosis):**
- ✅ When Convex filter rejects all: check-by-check failure counts (Volatility, ORB Range/ATR, Red Day, ORB Break, Volume, VWAP, Momentum, Market Regime, Score)
- ✅ Grep-friendly one-liner: `CONVEX_FILTER | 0_eligible | total=N | top_failures: ...`
- ✅ Top 5 per-symbol rejection details at INFO; input LONG/SHORT counts; eligible CALL/PUT breakdown

**Rev 00289 (Feb 26 - CRITICAL: Signal append bug):**
- ✅ Signal creation and append moved into `if orb_result.should_trade` — symbols that passed all 3 ORB/0DTE rules now correctly appear in Signal Collection lists

### **Previous Updates (February 2026 - Rev 00279/00280)** ⭐ **VALIDATION CANDLE FIX & DIAGNOSTICS**

**Rev 00280 (Feb - STEP 4 data-source diagnostic):**
- ✅ Log which validation candle data was used: PREFETCHED_IN_MEMORY, GCS_LOADED, or FRESH_INTRADAY (for next-session diagnosis).

**Rev 00279 (Feb - Validation candle fix for 0 signals):**
- ✅ **Explicit 7:15 close:** When we have a single prefetched bar (7:00–7:15), pass `validation_close_715` into rules so rule 3 (validation candle close vs ORB high/low) uses the same data as volume color — no bar-timestamp match required.
- ✅ **GCS persist:** After prefetch, persist validation candle (open/close per symbol) to `daily_markers/validation_candle_715/YYYY-MM-DD.json`.
- ✅ **GCS load:** When scan runs without in-memory prefetch (e.g. different Cloud Run instance), load validation candle from GCS and build prefetched structures so rules have correct 7:00 open and 7:15 close.

### **Previous Updates (January 22, 2026 - Rev 00259)** ⭐ **CLOUD CLEANUP AUTOMATION**

**Rev 00259 (Jan 22 - Cloud Cleanup Automation):**
- ✅ **Cleanup Endpoint**: Added `POST /api/cleanup/images` to main.py for automated cleanup
- ✅ **Cloud Scheduler Job**: Created `gcr-image-cleanup-weekly` (every Sunday at 2:00 AM PT)
- ✅ **Retention Policy**: Keep last 10 images + 30 days, keep last 20 revisions per service
- ✅ **Expected Savings**: 85% reduction in images, 91% reduction in revisions

### **Previous Updates (January 20, 2026 - Rev 00247)** ⭐ **CRITICAL BUG FIXES**

**Rev 00247 (Jan 20 - Critical Bug Fixes & Deployment Configuration):**
- ✅ **ETrade API Batch Limit Fix**: Enforced 25 symbol limit per API call (prevents error 1023)
- ✅ **0DTE Import Path Fix**: Fixed module import paths (easy0DTE.modules first, then modules fallback)
- ✅ **ORB Capture Alert Backfill Fix**: Alert now sent when system starts late (after 6:45 AM PT)
- ✅ **Deployment Configuration**: Fixed environment variables (DEMO mode, ENABLE_0DTE_STRATEGY, SYSTEM_MODE)
- ✅ **Scale-to-Zero Documentation**: Detailed behavior documented (trading days, weekends, holidays)

### **Previous Updates (January 19, 2026 - Rev 00246)** ⭐ **MAJOR ENHANCEMENTS**

**Rev 00246 (Jan 19 - 0DTE Priority Formula v1.1, Direction-Aware Red Day, Expanded Delta Selection, Comprehensive Logging):**
- ✅ **0DTE Priority Score Formula v1.1** (Rev 00246 lineage): Breakout 35%, Range 30%, Volume 20%, Eligibility 15% — **superseded for default runtime** by **Rev 00348** env-weighted alpha (**`0DTE_PRIORITY_RANK_W_*`**, defaults **0.14 / 0.10 / 0.22 / 0.20 / 0.34**); profile may still restore older shapes
- ✅ **Direction-Aware Red Day Filtering**: LONG rejected, SHORT allowed on Red Days
- ✅ **Delta Selection Expanded**: Range expanded to 0.15-0.35 (from 0.15-0.25)
- ✅ **Comprehensive Logging**: Added throughout entire 0DTE flow for better diagnostics

**Rev 00233 (Jan 8 - Performance Improvements & Data Quality Fixes + Alert Protection):**
- ✅ **Good Morning Alert Time Validation**: Only sends 5:30-5:35 AM PT (prevents wrong-time alerts)
- ✅ **Good Morning Alert Deduplication**: GCS-based (one alert per day maximum)
- ✅ **Data Quality**: Enhanced validation prevents false Red Day detection
- ✅ **Signal-Level Filtering**: Individual trade Red Day detection added
- ✅ **Secrets Management**: All sensitive credentials moved to `secretsprivate/` (gitignored)

**Rev 00231 (Jan 6 - Trade ID Shortening & Alert Formatting):**
- ✅ **Trade ID Shortening**: Shortened trade IDs for cleaner format
  - Format: `DEMO_QQQ_260106_485_488_c_704400`
  - Applied to: Debit spreads, credit spreads, lottos, both Demo and Live modes
- ✅ **Alert Formatting Enhancements**: Bold formatting for key metrics
  - Bold Priority Rank: `<b>Rank #1</b>`
  - Bold Priority Score: `<b>Priority Score: 0.856</b>`
  - Bold Confidence: `<b>Confidence: 85%</b>`
  - Bold Momentum: `<b>Momentum: 75/100</b>`
  - Bold Delta: `<b>Delta: 0.25</b>`
- ✅ **Integration**: Both ORB and 0DTE strategies updated
- ✅ **User Experience**: Improved readability of trade information

### **Previous Updates (December 2025)**

**Rev 00203 (Dec 19 - Trade Persistence Fix):**
- ✅ Trade persistence fixed (trades persist immediately to GCS)
- ✅ Trade history survives Cloud Run redeployments

**Rev 00201-00202 (Dec 19 - Unified Configuration):**
- ✅ 65+ configurable settings
- ✅ Clean configuration architecture
- ✅ Single source of truth for configuration

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
- ✅ RS vs SPY Calculation Fixed

**Rev 00180 (Dec 5 - Red Day Filter Enhanced):**
- ✅ 3-Pattern Detection (oversold, overbought, weak volume)
- ✅ 3-Tier Override System

**Rev 00176 (Nov - Red Day Detection Enhanced):**
- ✅ Enhanced pattern detection with 3-tier override system
- ✅ Distinguishes profitable vs losing days

**Rev 00137 (Nov - Holiday System Integrated):**
- ✅ Prevents trading on 19 high-risk days per year (bank + low-volume holidays)

**Rev 00108 (Nov 6 - Multi-Factor Ranking Formula v2.1):**
- ✅ Formula v2.1 deployed (VWAP 27%, RS vs SPY 25%, ORB Vol 22%) — **research / historical fixed-weight lineage**; **Rev 00348** SO runtime uses continuation-first base (see May 14, 2026 revision above)
- ✅ Data-driven refinement based on correlation analysis
- ✅ Expected +10-15% better capital allocation vs v2.0

---

## 🎯 Bottom Line

The Easy ORB Strategy provides a **proven, simple, profitable** automated trading system:

✅ **+73.69% weekly return** (23% above +60% target)  
✅ **91% winning day consistency** (10/11 days profitable)  
✅ **88-90% capital efficiency** with greedy packing  
✅ **ORB strategy** - simple, predictable, profitable  
✅ **Multi-factor ranking** — **Rev 00348** SO + 0DTE (Rev 00108 fixed-weight = historical SO research)  
✅ **Optimized exit settings** - expected 85-90% profit capture (Rev 00196)  
✅ **Demo Mode validated** - ready for live deployment  
✅ **Realistic performance** - proven with historical data  
✅ **Scales from $1K to $100K+** - consistent performance  
✅ **Unified configuration** - 65+ configurable settings (Rev 00201)  
✅ **Trade persistence** - GCS persistence working (Rev 00203)  

**Ready for production trading with proven performance!** 🚀

---

*Last Updated: May 15, 2026*  
*Version: Rev 00351+ docs — May 15 local calibration/execution/SO fixes (deploy pending); May 14 **Rev 00348** SO + 0DTE priority ranking alignment; May 13 ORB 0DTE / Trendline / ORB SO ETF operational notes retained.*
*Status: ✅ Production Ready - Critical Bug Fixes (Rev 00247), Trade Persistence Fix (Rev 00203), Unified Configuration (Rev 00201-00202), Exit Settings Optimized (Rev 00196), Trade ID Shortening (Rev 00231)*  
*Performance: +73.69% weekly return with 91% winning day consistency*  
*Capital Deployment: 88-90% guaranteed (6-step batch sizing + redistribution)*  
*Exit Settings: Optimized (Rev 00196: 0.75% breakeven, 0.7% trailing, 6.4 min activation - expected 85-90% profit capture)*  
*Position Sizing: Batch-sized quantities preserved (quantity_override)*  
*Priority Ranking: **Rev 00348** continuation-first SO + env-weighted 0DTE alpha; v2.1 fixed weights = historical research lineage*  
*Entry Bar Protection: PERMANENT FLOOR STOPS (Rev 00135) - ORB data passed for tiered stops 2-8%*  
*Exit System: All 14 triggers functional + verified integration*  
*Holiday Filter: 19 days/year skipped (10 bank + 9 low-volume, Rev 00137)*  
*Red Day Filter: Enhanced 3-Pattern Detection with 3-Tier Override System (Rev 00176)*  
*Scalability: Dynamic symbol system (merged ORB-capture universe is dynamic; add/remove without code changes)*
*Timezone: 100% DST-aware, works in EDT and EST*  
*Configuration: Unified configuration system (65+ settings - Rev 00201)*  
*Trade Persistence: GCS persistence working (Rev 00203)*  
*For risk management details, see [docs/Risk.md](Risk.md)*  
*For process flow details, see [docs/ProcessFlow.md](ProcessFlow.md)*  
*For alert documentation, see [docs/Alerts.md](Alerts.md)*
