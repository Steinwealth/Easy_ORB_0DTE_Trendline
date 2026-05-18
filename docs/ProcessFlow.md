# Prime Trading System - End-to-End Process Flow

**Last Updated**: April 30, 2026 — aligns **three concurrent paths** (ORB SO, ORB 0DTE, **Easy Trendline 0DTE**) through EOD; Trendline **7:30 setup direction** is **structure-first** (`classify_orb_test_failure`, MSE fallback — see [0DTETrendline.md](0DTETrendline.md)). Baseline revisions: **Rev 00349** ORB 0DTE fast monitor + execution reliability; **Rev 00347** SO winner-profile + ORB 0DTE monitor hardening; **Rev 00328** Hard Gate spread doc; **Rev 00327** watchlists (`core_list` / `0dte_list` counts drift over time — verify CSVs); **Rev 00319** payoff guardrails; **Rev 00312** ORB range %.  
**Status**: ✅ Production Ready — **ORB SO + ORB 0DTE + Trendline 0DTE** (when enabled); merge-on-persist; Convex on **ORB 0DTE only**; pipeline STEP 1–5; validation candle 7:00/7:15  
**Performance**: +73.69% weekly return with 91% winning day consistency  
**Expected**: 85-90% profit capture with optimized exit settings (Rev 00196)  
**Deployment**: Google Cloud Run (scales to zero, keep-alive jobs ensure availability)  
**Capital Deployment**: 88-90% guaranteed (6-step batch sizing + post-rounding redistribution)

---

## Overview

This document defines the **complete production flow** of the Easy ORB Strategy trading system from **OAuth token renewal** to **end-of-day reporting**. Every step is documented with code references, data flows, and verification checklists. **Session analysis:** Cloud Run logs emit `PIPELINE | STEP N ...` lines (ORB Capture → Validation Open 7:00 → Validation Candle 7:00–7:15 → Signal Collection → Trade Execution); filter with `grep "PIPELINE"` to verify each step’s counts and outcomes for the next session (Rev 00260). **Validation candle:** One log search `textPayload:"VALIDATION_CANDLE"` confirms 7:00 open, 7:00–7:15 open-close, and STEP 4 (see [CLOUD_LOGS_0_SIGNALS_FEB19.md](doc_elements/Sessions/2026/Feb19%20Session/CLOUD_LOGS_0_SIGNALS_FEB19.md)). **Signal collection (Rev 00285–00293+):** List is persisted to GCS after scan with **merge-on-persist** (union by symbol; prevents 0-signal overwrite). At 7:30 execution, **always loads from GCS** when enabled (latest merged state). Only current-day data used; orb_data serialized for risk/0DTE. **Rev 00296:** Dual-path routing — ORB SO and 0DTE lists built independently. **Rev 00298:** Execution paths verified; 0DTE_MAX_POSITIONS aligned (15); _pending_dte0_signals always init. See [Mar02 Session — Post-Signal Collection Verification](doc_elements/Sessions/2026/Mar02%20Session/POST_SIGNAL_COLLECTION_EXECUTION_VERIFICATION_MAR02_2026.md).

**System**: Dynamic symbol lists (`core_list` + `0dte_list`, merged union for ORB capture — count varies with CSVs; fully scalable without code changes)  
**Strategies**: 
- **ORB Strategy (SO / ETF)**: Opening Range Breakout standard orders (`core_list`, LONG-only rule stack; **no Convex**)
- **ORB 0DTE Strategy**: Zero-days options batch at **~7:30** from signal scan → Convex → Hard Gate (`0dte_list`)
- **Easy Trendline 0DTE Strategy**: Same shared ORB context; **~7:30 build** of trendline candidates on **`0dte_list`** (often full universe); **event-driven** entries after break/hold/structure/momentum; **isolated ledger** and **`TrendlineOptionsStealthEngine`** — full detail [0DTETrendline.md](0DTETrendline.md)
**Optimization**: Smart loss prevention + multi-factor ranking + batch position sizing + optimized exit settings  
**Performance**: +73.69% weekly return with 91% winning day consistency  
**Deployment**: Google Cloud Run (scales to zero, keep-alive jobs ensure availability)  
**Configuration**: Unified configuration system (65+ configurable settings - Rev 00201)

**⚠️ Note**: For sensitive deployment-specific information (OAuth portal URLs, service URLs, account IDs), see [PrivateSecrets.md](PrivateSecrets.md).

**Required Cloud Scheduler jobs (7):** All must be **ENABLED** for token → ORB → validation candle → signal collection → execution → EOD. List: `gcloud scheduler jobs list --location=us-central1 --project=easy-etrade-strategy`. Resume PAUSED: `gcloud scheduler jobs resume JOB_NAME --location=us-central1 --project=easy-etrade-strategy`. Full checklist: [CLOUD_JOBS_CHECKLIST.md](doc_elements/Sessions/2026/Feb24%20Session/CLOUD_JOBS_CHECKLIST.md).

| Job | Schedule (PT) | Purpose |
|-----|----------------|---------|
| trading-hours-keepalive-1 | `*/3 5-6 * * 1-5` | Pre-market warm |
| trading-hours-keepalive-2 | `*/5 7-9 * * 1-5` | Warm 7:00–7:30 and session start |
| trading-hours-keepalive-3 | `*/5 10-13 * * 1-5` | Session 10 AM–2 PM |
| oauth-market-open-alert | 5:30 AM PT | Good Morning + token status |
| **validation-candle-700** | **7:00 AM** | 7:00 open for validation candle |
| **prefetch-validation-715** | **7:15 AM** | 7:00 open + 7:15 close → GREEN/RED |
| end-of-day-report | 4:05 PM ET | EOD report |

### **Complete Trading Session Flow**

The complete trading session follows this flow:

1. **OAuth Token Renewal** (12:00 AM ET) - Daily token expiry and renewal
2. **Pre-Market Preparation** (5:00-6:30 AM PT) - Keep-alive, morning alert, holiday check
3. **ORB Capture** (6:30-6:45 AM PT) - Opening range for **merged** universe (ORB SO + ORB 0DTE + Trendline **`orb_context`**)
4. **Signal Collection** (7:15-7:30 AM PT) - ORB SO + ORB 0DTE rule scans (Trendline does **not** use this list for universe; it uses **`0dte_list`** at build)
5. **7:30 AM PT block** - Signal Collection alert; **ORB SO** + **ORB 0DTE** batch execution; **Trendline** candidate **build** (no batch options entry here)
6. **Position Monitoring** (7:30 AM - 12:55 PM PT) - ETF ~30s; options **~5s** fast paths (ORB 0DTE + Trendline) with shared-loop nuances; Trendline **new entries** may stop after **`TRENDLINE_NO_NEW_ENTRIES_AFTER_PT`**
7. **Portfolio Health Checks** (Every 15 minutes) - Risk management and emergency exits
8. **End-of-Day Close** (12:55 PM PT) - Force close all positions
9. **End-of-Day Report** (1:05 PM PT) - Daily and weekly performance summaries (single source: Cloud Scheduler endpoint)

---

## 📅 **Complete Daily Timeline** (Monday-Friday)

**Quick Reference - Full Trading Day Flow**:

| Time PT | Time ET | Phase | Activity | Alert |
|---------|---------|-------|----------|-------|
| **9:00 PM** (prev day) | **12:00 AM** | OAuth expiry | Tokens expire at midnight ET | OAuth Expired |
| **5:00 AM** | 8:00 AM | Pre-Market | Keep-alive starts (every 3 min) | None |
| **5:30 AM** | 8:30 AM | Pre-Market | Morning alert + holiday check | Good Morning / Holiday ⭐ Rev 00233 |
| **6:30-6:45 AM** | 9:30-9:45 AM | ORB Capture | Opening ranges captured (ETF + 0DTE) | ORB Capture Complete (includes 0DTE counts if enabled) |
| **7:00 AM** | 10:00 AM | Validation open | Broker prices captured for 7:00–7:15 bar open (Cloud Scheduler) | None |
| **7:15 AM** | 10:15 AM | SO Prefetch | 7:00–7:15 validation candle built (open=7:00, close=7:15) | None |
| **7:15-7:30 AM** | 10:15-10:30 AM | SO Collection | Scan every 30 sec (30 scans) | None |
| **7:30 AM (Start)** | 10:30 AM | Execution block | Signal collection alert; batch SO + 0DTE; Trendline **build** | Trade Signal Collection (SO + 0DTE); SO Execution + 0DTE Execution; **`TRENDLINE_PIPELINE` build** / `setup_detected` (per symbol, if enabled) |
| **7:30 AM (End)** | 10:30 AM | Batch complete | ETF + 0DTE batch done; Trendline candidates **registered for watch** | SO Execution + 0DTE Execution alerts; Trendline logs (no 7:30 options batch) |
| **7:30-12:55 PM** | 10:30-3:55 PM | Monitoring | ORB ETF ~30s; ORB 0DTE + Trendline options **~5s** fast monitors (+ shared-loop backup for ORB 0DTE) | Individual exits per path |
| **7:45 AM-12:45 PM** | 10:45-3:45 PM | Health Checks | Every 15 min (~21 checks) | Emergency/Warning |
| **12:55 PM** | 3:55 PM | EOD Close | All positions closed (ETF + 0DTE + Trendline when enabled) | Aggregated exits per ledger |
| **1:05 PM** | 4:05 PM | EOD Report | Cloud Scheduler: **three** summaries (ETF, ORB 0DTE, Trendline 0DTE) | See [Alerts.md](Alerts.md) |

**Total Alerts Per Day** (Typical — all enabled paths):
- 1 Morning Alert (Good Morning or Holiday)
- 1 ORB Capture Complete (shared ORB data for ORB SO + 0DTE + Trendline context)
- 1 Signal Collection (ORB + 0DTE combined)
- 1 ORB Execution (with **bold formatting** - Rev 00231)
- 1 0DTE Execution (if 0DTE enabled)
- 0-2 Health Check Alerts (if EMERGENCY/WARNING)
- 0-10+ Individual Exit Alerts (trailing, breakeven, rapid, etc.) — ORB ETF, ORB 0DTE, Trendline 0DTE (as applicable)
- 1 ORB Aggregated Exit (EOD close)
- 1 0DTE Aggregated Exit (EOD close, if 0DTE enabled)
- 1 ORB EOD Report
- 1 0DTE EOD Report (if 0DTE enabled)
- 1 Trendline 0DTE EOD Report (if Trendline enabled)
**Total**: 8–24+ alerts per day (depending on 0DTE / Trendline enablement and trading activity)

**Holiday Days** (19 per year - Rev 00137):
- 1 Holiday Alert
- 0 Trading alerts (system disabled)
**Total**: 1 alert on holidays

---

## 📋 Daily Performance Flow — Steps the Software Takes

This section summarizes the **actual performance flow** of a trading session: what the software does, in order, from token renewal through EOD. Use it for quick reference; detailed steps and code references follow in the numbered sections below.

### 1. OAuth Token Renewal
- **When**: Tokens expire at 12:00 AM ET; renewal is manual via web portal (see PrivateSecrets.md).
- **Software**: Midnight alert sent; tokens stored in Google Secret Manager after renewal. Hourly keep-alive job keeps production tokens active. Both Demo and Live use the same production E*TRADE OAuth tokens.

### 2. Good Morning Alert
- **When**: 5:30 AM PT (8:30 AM ET), 1 hour before market open. Only between 5:30–5:35 AM PT; GCS deduplication ensures one per day.
- **Software**: (1) Holiday check — if holiday (19 days/year), sends Holiday alert and disables trading. (2) If not holiday, validates production tokens; if valid, sends Good Morning alert (token status, Demo/Live mode, today’s ORB/execution times).

### 3. ORB Capture (6:30–6:45 AM PT)
- **When**: First 15 minutes after market open. Alert at 6:45 AM PT (or after backfill if system starts late).
- **Software**: E*TRADE batch quotes for **merged** `core_list` ∪ `0dte_list` (dynamic count). **ORB high/low** = extremes in the opening window (aggregated if multiple bars). **orb_range_pct** = \((H-L)/L×100\) stored on each symbol’s `ORBData`. If broker returns flat OHLC (0% width), **recovery pass** re-fetches multi-bar 15m and re-captures. Same **orb_range_pct** feeds Convex, 0DTE/OR SO priority, Opening Bar Protection, and Trendline **`orb_context`**. Single ORB Capture Complete alert. Missing ORB for a symbol → that symbol cannot participate in SO/0DTE rules or a valid Trendline build for that symbol.

### 4. Signal Collection (7:15–7:30 AM PT) — Rules That Validate Opening ORB and Options Positions

**Rules checklist:** For a concise list of every rule we verify for LONG/SHORT and how to confirm success or see why 0 signals occurred, see **[SignalRulesChecklist.md](doc_elements/Sessions/2026/Feb24%20Session/SignalRulesChecklist.md)**.

**Validation open (7:00 AM PT):** A Cloud Scheduler job calls the service to capture broker prices at 7:00 AM PT; these are stored in memory and persisted to GCS so the 7:15 prefetch (same or different instance) can use them as the **open** of the validation bar. Without this, the bar would be all NEUTRAL (open=close=7:15).

**Prefetch (7:15 AM PT):** Load **real 7:00–7:15 AM PT bar** (open = 7:00 price, close = 7:15 price) for volume color and previous-candle rule. No proxy (e.g. market open) is used—only 7:00 PT open + 7:15 PT close. Full ORB + 0DTE symbol list; batches of 25 for broker quotes.

**ORB LONG / 0DTE CALL (all 3 required):** (1) Price (scan-time 7:15–7:30 PT) ≥ ORB high × 1.001. (2) Validation candle volume **GREEN** — 7:00–7:15 close > open. (3) Validation candle close > ORB high.

**ORB SHORT / 0DTE PUT (all 3 required):** (1) Price (scan-time 7:15–7:30 PT) ≤ ORB low × 0.999. (2) Validation candle volume **RED** — 7:00–7:15 close < open. (3) Validation candle close < ORB low.

**Steps:** Scan every 30 sec (7:15–7:30). Apply the 3 rules → collect qualified symbols → **ORB SO** multi-factor ranking (VWAP 27%, RS vs SPY 25%, ORB Vol 22%, etc.; 0DTE uses a different formula without RS vs SPY) → Red Day filter (portfolio-level; **blocks ORB Long execution and 0DTE Long/CALL execution; keeps 0DTE Short/PUT enabled**) → signal-level filtering → position sizing and capital (90%, max 15 positions) → **final confirmed SO list** for execution. The scan instance **persists** this list (and the 0DTE list) to GCS (`daily_markers/signal_collection_730/YYYY-MM-DD.json`) so the 7:30 execution block—which may run on a different Cloud Run instance—can **load** it when in-memory is empty. **At 7:30, a final cutoff revalidation using fresh quotes is applied**: LONG candidates must still satisfy `current_price_now >= orb_high * 1.001`, and SHORT candidates must satisfy `current_price_now <= orb_low * 0.999` (Rev 00330/00331), before the list is passed into ranking/risk/execution. Only **current-day** lists are used (stale in-memory discarded; GCS payload used only when `data.date == today`). Each signal's `orb_data` is stored as a dict so risk and 0DTE have `orb_high`/`orb_low` after load (Rev 00285–00287). See [Feb24 Session — Signal Collection Gaps and Fixes](doc_elements/Sessions/2026/Feb24%20Session/SIGNAL_COLLECTION_GAPS_AND_FIXES.md).

**0DTE — Long or Short options positions:**
- **Eligibility**: Convex filter (score ≥ 0.75): volatility, ORB range/ATR, NOT Red Day (15%), ORB break required, volume confirmation, VWAP, momentum, market regime. Strategy selection (long call, debit spread, etc.) and strike selection (delta 0.15–0.35, liquidity gates).
- **Red Day (0DTE):** Red Day filtering blocks long risk and keeps bearish 0DTE active. On Red Days: **ORB Long blocked**, **0DTE LONG (CALL) rejected**, **0DTE SHORT (PUT) allowed** (and often favorable — e.g. oversold/SPY-VIX down).
- **Steps:** Receives ORB context → convex eligibility → strategy/strike selection → hard gates (OI, spread, volume) → position sizing → **final confirmed 0DTE list** for execution.

**Alert:** Single Signal Collection alert at 7:30 AM PT with both final SO and 0DTE lists (execution-ready).

**Execution path fork (Rev 00284; Rev 00309):** Signal collection builds **ORB SO list** (core_list, Long rules only) and **0DTE list** (same Long rules for CALL, inverse rules for PUT — no bypass). **Convex + Hard Gate** run **after** those lists, **before** 0DTE options execution only. **ORB ETF execution** does not use Convex; it executes the SO list from signal collection.

---

#### ORB Strategy — From Signal Collection List to Execution and Monitoring

| Step | Description |
|------|-------------|
| 1. **Signal Collection list** | `_pending_so_signals` — LONG only; symbols that passed the 3 rules (price ≥ ORB high×1.001, 7:00–7:15 GREEN, bar close > ORB high). **Does not use Convex filter.** |
| 2. **At 7:30** | Confirmed list taken; apply final cutoff revalidation with fresh quotes (LONG: `current_price_now >= orb_high*1.001`; SHORT: `current_price_now <= orb_low*0.999`) before sending the Signal Collection alert and before ranking/risk/execution. |
| 3. **Enrich** | Technical data (RSI, VWAP, volume ratio, MACD, RS vs SPY, etc.) from data manager. |
| 4. **Priority ranking** | Multi-factor base score (VWAP 27%, RS vs SPY 25%, ORB volume 22%, confidence 13%, RSI 10%, **ORB range 3%** using **capture orb_range_pct**) plus Rev 00347 **quality multiplier**; sort by score descending. |
| 5. **Winner-profile quality gate** | Rev 00347 pre-adaptive filter rejects loss-prone combinations (weak participation/confidence, overextended non-bull, wide ORB + weak participation) with minimum keep-count restore logic. |
| 6. **Red Day filter** | Portfolio-level: if pattern detected (e.g. oversold + weak volume), **block ORB Long and 0DTE Long/CALL execution**; keep **0DTE Short/PUT** execution enabled. |
| 7. **Risk management** | Expensive filter (e.g. 3× fair share); batch position sizing (`risk_manager.calculate_batch_position_sizes`); slip guard / normalization to SO capital (e.g. 90%). |
| 8. **Execution** | DEMO: `mock_executor` (Demo ORB sim account). LIVE: `trade_manager` (E*TRADE). Batch or per-signal execution. |
| 9. **Monitoring** | `stealth_trailing` (trailing stops, breakeven, rapid exit); 15-minute portfolio health check. |

---

#### 0DTE Strategy — From Signal Collection List to Execution and Monitoring

**0DTE produces both LONG (CALL) and SHORT (PUT) signals** — unlike ORB SO (Long-only). The combined list is ranked by priority; top N (max 15) are executed as options. Full 0DTE details: [easy0DTE/docs/Strategy.md](../easy0DTE/docs/Strategy.md), [easy0DTE/docs/README.md](../easy0DTE/docs/README.md).

| Step | Description |
|------|-------------|
| 1. **Signal Collection list** | `_pending_dte_signals` — raw 0DTE list from scan: **LONG (CALL)** when price > ORB high + rules pass; **SHORT (PUT)** when price < ORB low + rules pass. Same 3 rules (price, volume color, validation close vs ORB). Input to 0DTE is this list (not ORB SO list; ORB SO is Long-only). |
| 2. **Convex Eligibility Filter** | **Before 7:30 alert.** Input is first restricted to symbols in `0dte_list.csv` (target universe), then Convex runs with min score 0.75; **ORB range** = morning **orb_range_pct** (≥ min % or ATR alt; SHORT breakdown fallback only if capture width still 0). Plus ORB break, volume, VWAP, momentum, regime. Output: eligible LONG + SHORT. |
| 3. **Strategy selection & ranking** | Per direction: strategy type (long call/put, debit spread, momentum scalper, ITM probability). **Priority formula (0DTE, no RS vs SPY):** Breakout 35%, Range 30%, Volume 20%, Eligibility 15%. Single combined ranking for all 0DTE signals. |
| 4. **Hard Gate** | Pre-validate OI ≥100, bid/ask spread ≤**15%** of mid (default in `OptionsChainManager.validate_liquidity`; align with live env overrides), volume ≥50, and symbol ∈ 0DTE target list from `0dte_list.csv`. Time window check enforces that execution happens only in the **10:30–10:40 ET (7:30–7:40 PT)** block, *after* the 7:30 AM PT Signal Collection step completes. Build `_pending_dte0_signals` (qualified list for execution). When all signals fail Hard Gate, `_pending_dte0_signals` is empty but the Convex-qualified Long/Short list is still surfaced in the Signal Collection alert for diagnosis. |
| 5. **At 7:30** | Take `_pending_dte0_signals` (only signals that passed Hard Gate and ranking cap). Signal Collection alert 0DTE section shows ranked CALL/PUT context plus pipeline diagnostics: `candidates`, `convex`, `hard_gate`, `pending_exec`. In full trading mode, **no 0DTE options execution occurs before this 7:30 block**, and when `_pending_dte0_signals` is empty, 0DTE Options Execution performs 0 attempts (no trades). |
| 6. **Position sizing** | `prime_0dte_strategy_manager.calculate_position_sizing`: 90% capital, max 15 positions (combined CALL+PUT), rank-based multipliers (3.0x, 2.5x, 2.0x…). **Tiers:** Tier 1 (top 3) 35%, Tier 2 (next 5) 20%, Tier 3 rest 10%. |
| 7. **Per signal execution** | Hard Gate validate (including time window) → options chain (strike selection, delta 0.15–0.35, premium $0.15–$0.60, 1–3 strikes OTM for typical debit-spread long leg, plus per-leg liquidity) → execute CALL or PUT (long call/put, debit spread by strategy type). If no valid structure, **`spread_selection_failed`** / “No debit spread found…” (Rev 00328). |
| 8. **Execution** | DEMO: Demo 0DTE options account (MockOptionsExecutor). LIVE: E*TRADE options API. Separate 0DTE Options Execution alert (sent only when at least one Hard-Gate-passed signal is executed). |
| 9. **Monitoring** | Options exit manager: real-time options prices every 30s; hard stops (-45% debit, -55% lotto), time stops (25 min / 12 min), profit targets (+60%, +120%), fail-safe -60%. |

**Key details from 0DTE docs (for primary reference):** Convex 8 criteria and direction-aware Red Day; 0DTE priority formula (Breakout 35%, Range 30%, Volume 20%, Eligibility 15%); position tiers (35%/20%/10%); Hard Gate (OI, bid–ask spread **≤15%** of mid default, volume); strategy types (long call/put, debit spread, momentum scalper, ITM probability); delta 0.15–0.35, premium $0.15–$0.60; spread width SPX $5–$10, QQQ/SPY $1–$2, most other names $1–$2. Exit framework: hard stop, time stop, partial profits, runner. See [docs/Strategy.md](Strategy.md) and [easy0DTE/docs/Strategy.md](../easy0DTE/docs/Strategy.md).

---

#### Easy Trendline 0DTE — Build (~7:30), Watch, Execute (intraday)

**Not** gated by ORB SO list or Convex. Uses **shared ORB capture** + **pre-7:30 merged bars** (ORB-timed intraday + quote merge; chunked broker budgets — see [0DTETrendline.md](0DTETrendline.md)).

| Step | Description |
|------|-------------|
| 1. **Universe** | Typically **`0dte_list`** in full when `TRENDLINE_USE_FULL_0DTE_LIST=true`; watchlist order preserved |
| 2. **~7:30 build** | `select_pre730_structure_setup`: builds **both** bull + bear trendlines, scores MSE; **primary direction** from **`TrendlineBuilder.classify_orb_test_failure`** (failed downside → bear/call; failed upside → bull/put); **`trend_continuation`** → no candidate; unclear/compression → **MSE** fallback |
| 3. **Candidate lifecycle** | Valid setups → **`WAITING_FOR_BREAK`** (no option fill at build) |
| 4. **Intraday** | Break → hold → structure → momentum → anti-chase → **`TrendlineOptionsExecutor`**; rolling cap **`TRENDLINE_MAX_OPEN_POSITIONS`**; **no new entries** after **`TRENDLINE_NO_NEW_ENTRIES_AFTER_PT`** (monitor/exits continue) |
| 5. **Monitoring / exits** | ~**5s** Trendline fast loop; **`TrendlineOptionsStealthEngine`** (premium BE/trailing/invalidation vs line — not ETF stealth) |
| 6. **Telemetry** | `TRENDLINE_PIPELINE`, **`TRENDLINE_SELECTOR_STRUCTURE_OVERRIDE`**, decision snapshots — see [0DTETrendline.md](0DTETrendline.md) |

---

**Why 0 signals — quick reference**

| Situation | Likely cause | What to do |
|-----------|--------------|------------|
| All NEUTRAL (validation candle) | 7:00 open and/or 7:15 close not captured | Fix 7:00 job (`validation-candle-700`), 7:15 prefetch (`prefetch-validation-715`), GCS persist/load. |
| Validation SUCCESS but 0 ORB signals | No symbol had 7:00–7:15 close > ORB high (LONG) or close < ORB low (SHORT) | Market-driven; check logs for LONG rule breakdown. |
| 0DTE raw signals but 0 qualified | Convex filter rejected all (score < 0.75) | Grep `CONVEX_REJECT` / `CONVEX_FILTER | 0_eligible`. Common: **ORB range 0%** before recovery — ensure ORB capture + recovery deployed (Rev 00311). |
| Many "Price below ORB high" | Current price not above ORB high by 0.1% | Scan-time price vs ORB; market or timing. |
| Volume RED (need GREEN) / GREEN (need RED) | Direction filter working as designed | Validation candle correct; no code change. |
| Symbols skipped (no ORB data) | ORB capture failed or symbol not in list | Fix ORB capture (6:30–6:45) and symbol list. |
| 0DTE on collection list but **“No debit spread found”** / **`spread_selection_failed`** | Chain/strike selector could not pair contracts (premium band, delta/OTM, liquidity on legs) | Grep `spread_selection_failed`, `Rejecting .* premium`, `Hard Gate FAILED`; see [Strategy.md](Strategy.md) debit-spread section (Rev 00328). Distinct from **`poor_payoff_profile`**. |
| Strong chart, **missing ORB SO** | Symbol only on `0dte_list`, not `core_list` | Add symbol to `core_list.csv` if it should execute on ORB SO (dual-list pattern: **HIMS**, **CRWD**). |

**Verify in logs:** `Validation candle: SUCCESSFUL (GREEN=X RED=Y NEUTRAL=Z)`; `STEP 4 validation candle data source: PREFETCHED_IN_MEMORY | GCS_LOADED | ...`; `ORB data available (captured): N`; `LONG rule breakdown` with counts; `Rejection reasons (ORB) - counts:`. **0DTE 0 pass:** grep `CONVEX_FILTER | 0_eligible | total=N | top_failures:`; check-by-check failure counts. Full rules and diagnosis: [SignalRulesChecklist.md](doc_elements/Sessions/2026/Feb24%20Session/SignalRulesChecklist.md), [SESSION_SUMMARY_FEB26_2026.md](doc_elements/Sessions/2026/Feb26%20Session/SESSION_SUMMARY_FEB26_2026.md).

**Pipeline from validation candle to execution (verified steps):**
1. **Opening Range (ORB):** 6:30-6:45 AM PT only. ORB High/Low come from here. Not 7:00-7:15.
2. **Validation candle (not opening range):** 7:00 open + 7:15 close → volume color (GREEN/RED/NEUTRAL) per symbol. All NEUTRAL = capture failed. Used only for rule checks vs ORB high/low.
3. **Scan (7:15–7:30):** For each symbol, run rules (price vs ORB high/low from 6:30-6:45 only, volume color from validation candle, validation candle close vs ORB high/low). Append to **all_signals** (ORB LONG, **`core_list` only**) and **dte_signals** (0DTE CALL+PUT, **`0dte_list`**) only when rules pass.
4. **Collection lists:** `_pending_so_signals` = ORB LONG list; `_pending_dte_signals` = raw 0DTE list from scan.
5. **Signal Collection alert:** Sent at 7:30 with ORB symbol list and 0DTE CALL/PUT lists (or 0 and diagnostic reason).
6. **0DTE qualification:** `listen_to_orb_signals` runs Convex eligibility, hard gates, ranking → **qualified 0DTE list** (`_pending_dte0_signals`).
7. **ORB execution:** Priority ranking (multi-factor score) → risk manager batch sizing (rank multipliers, caps, ADV) → orders sorted by **priority_rank** → batch execution (ORB account).
8. **0DTE execution:** Ranked 0DTE list → position sizing → execution (0DTE account).
9. **Execution alerts:** SO Execution alert (ORB); 0DTE Execution alert when 0DTE list is non-empty.
10. **Trendline build (if enabled):** After shared ORB + merged bars, **`select_pre730_structure_setup`** per symbol → candidates registered in **`TrendlineSignalEngine`** (logs `setup_detected` / skips — **not** the Signal Collection SO/0DTE lists).
11. **Trendline execution (intraday):** Event-driven option fills **after** confirmations; separate Trendline execution alerts when fills occur (not part of the 7:30 SO/0DTE batch alerts).

### 5. Red Day Filtering — Blocks Long Risk; Keeps Bearish 0DTE
- **Purpose:** Red Day Filtering is a directional risk gate used to avoid long exposure on weak/bearish days. On Red Days, **ORB (SO) Long execution is blocked** and **0DTE Long (CALL) execution is blocked**, while **0DTE SHORT (PUT)** strategies remain allowed and can be favorable.
- **When:** During Signal Collection / pre-execution (after signals are collected, before execution).
- **Complete metric set used for Red Day evaluation (logged/alerted each check):**
  - `Signals analyzed`: e.g. `25`
  - `Weak Volume (<1.0x)`: e.g. `100% (25/25)`
  - `Oversold (RSI <40)`: e.g. `36% (9/25)`
  - `Overbought (RSI >80)`: e.g. `0%`
  - `Avg RSI`: e.g. `43.7`
  - `Avg Volume`: e.g. `0.49x`
  - `Avg MACD`: e.g. `0.219`
  - `Avg RS vs SPY`: e.g. `0.00`
  - `Avg VWAP distance`: e.g. `0.00%`
- **Rules that activate Red Day (portfolio-level) — effect: block long exposure, keep bearish 0DTE):**
  - **Pattern 1 — OVERSOLD:** RSI &lt;40 in enough symbols + weak volume (&lt;1.0x). This signals **market weakness and a strong 0DTE Short (PUT) opportunity**; ORB Long + 0DTE CALL are blocked, 0DTE Short remains enabled.
  - **Pattern 2 — OVERBOUGHT:** RSI &gt;80 + weak volume (&lt;1.0x). **3-tier override** (MACD, RS vs SPY, VWAP) can allow ORB trading to **avoid disabling trading on profitable days**.
  - **Pattern 3 — WEAK VOLUME ALONE:** ≥80% of symbols with weak volume. A good **primary indicator** of Red Day; when **overall trade or options volume is low**, that is a day we generally do not want to execute (especially ORB). Use with care so as not to disable 0DTE on days when Short strategies would be successful.
- **Design intent:** Avoid disabling trading on profitable days. **SPY/VIX trending down** = favorable for **0DTE Short** — do not disable 0DTE options on those days. **Weak volume or negative VWAP** can be used to determine when not to trade, but **avoid disabling 0DTE options on days when Short strategies will be successful.**
- **Enhanced Red Day:** Uses real SPY momentum and VIX from E*TRADE; if recommendation = SKIP_EXECUTION → SO long signals cleared, **ORB Long blocked**. 0DTE: **LONG (CALL) blocked**, **SHORT (PUT) still allowed**.
- **Alert dedupe:** Red Day Triggered alert is limited to one send per trading day.
- **Signal-level:** Per-signal filter (weak volume + low RSI / zero MACD / negative VWAP) can remove individual symbols; does not necessarily zero out all signals.

### 6. Trade Execution (7:30 AM PT) + Trendline **build**
- **ORB SO:** Execute from **final confirmed SO list** only (all rules and Red Day already applied). Up to 15 positions; 6-step position sizing (rank multipliers, ADV cap, normalize to 90%, rounding, redistribution). SO Execution alert sent.
- **ORB 0DTE:** Execute from **final confirmed 0DTE list** only. Options chains fetched; strikes already validated. Separate 0DTE Options Execution alert sent.
- **Trendline 0DTE:** **No** batch options execution at 7:30 — **build** only (`select_pre730_structure_setup`, chunked data path). Intraday option fills after confirmations; see [0DTETrendline.md](0DTETrendline.md).

### 7. Position Monitoring (7:30 AM – 12:55 PM PT)
- **Frequency:** ORB ETF ~**30 seconds** on the main loop. **ORB 0DTE** and **Trendline 0DTE** options use **~5-second fast-monitor** loops (ORB 0DTE also has shared-loop backup). Health checks every **15 minutes** (~21/day).
- **ORB ETF:** Stealth trailing (breakeven ~0.75% after ~6.4 min, trailing ~0.7%, tiered floor stops from ORB volatility).
- **ORB 0DTE:** Options stealth exits — premium-based decisions; evaluated on **fast-monitor** cadence (~5s), not limited to the ETF 30s loop.
- **Trendline 0DTE:** **`TrendlineOptionsStealthEngine`** (~5s); **no new entries** after **`TRENDLINE_NO_NEW_ENTRIES_AFTER_PT`** while monitoring continues.

### 8. Exit Strategies — List and Brief Review
- **ORB ETF — 14 exit triggers:** (1) Stop loss (current stop level). (2) Trailing stop (1.5–2.5% from peak after breakeven/TP). (3) Breakeven (+0.75% after 6.4 min, locks +0.2%). (4) Take profit +3% (activates trailing). (5) Profit timeout 2.5 hr if profitable and unprotected. (6) Max hold 4 hr. (7) Rapid — no momentum (15 min, peak &lt;+0.3%). (8) Rapid — immediate reversal (5–10 min, down &gt;-0.5%). (9) Rapid — weak position (20 min, down &gt;-0.3%, peak &lt;+0.2%). (10) RSI momentum (RSI &lt;45 for 90 sec and losing). (11) Gap risk (&gt;2% from high). (12) EOD close 12:55 PM. (13) Emergency — 3+ red flags → close all. (14) Weak day — 2 red flags → close losing.
- **ORB 0DTE:** Hard stops, time stops, partial profit targets, runner trailing; exit decisions based on actual options P&L / shared stealth rules.
- **Trendline 0DTE:** `OPTION_STEALTH_TLINE_*`, invalidation vs trendline, chop-hold — see [0DTETrendline.md](0DTETrendline.md).
- **Health checks (every 15 min):** Win rate &lt;35%, avg P&L &lt;-0.5%, low momentum &lt;40%, weak peaks &lt;0.8%, all positions losing → EMERGENCY (close all) or WARNING (close weak).

### 9. End-of-Day (EOD)
- **12:55 PM PT:** Force close **per ledger**: ORB ETF, ORB 0DTE options, Trendline 0DTE options (when enabled). Aggregated exit alerts **per path**.
- **1:05 PM PT:** Cloud Scheduler calls `POST /api/end-of-day-report`. Separate **ORB ETF**, **ORB 0DTE**, and **Trendline 0DTE** EOD summaries when enabled (daily + weekly if Friday where applicable). GCS markers prevent duplicates. Scheduler is the single **scheduled trigger** for these reports.

---

## 1) Pre-Market Preparation (Daily)

### **OAuth Token Management**

**Timing**: 12:00 AM ET (midnight alert) + hourly keepalive

**Process:**
- **Midnight Alert**: OAuth Tokens Expired alert sent at 12:00 AM ET
- **Token Renewal**: Via web app (see PrivateSecrets.md for portal URL)
- **Tokens Stored**: Google Secret Manager after renewal
- **Keepalive**: Hourly Cloud Scheduler job at :00 (keeps production tokens active)
- **Token Format**: JSON stored in Secret Manager as `etrade-oauth-prod`
- **Both Modes**: Demo and Live use same production tokens (see OAuth.md for details)

**Alerts:**
- ✅ OAuth Tokens Expired (12:00 AM ET) - Daily expiry reminder
- ✅ OAuth Token Renewed (when renewed) - Confirmation alert
- ✅ OAuth Morning Alert (8:30 AM ET) - Token status check (if tokens invalid)

**Important**: Both Demo and Live modes use the same production E*TRADE OAuth tokens since they use the same production API. The difference is which account is used (demo account vs live account).

**Alert Delivery**: Direct Telegram API (works 24/7, independent of trading system)

**Validate OAuth:**
- [ ] OAuth Tokens Expired alert received at 9:00 PM PT (12:00 AM ET) if scheduler runs.
- [ ] After renewal: OAuth Token Renewed (Production) confirmation alert received.
- [ ] Tokens visible in Google Secret Manager (`etrade-oauth-prod`); no stale or duplicate versions if cleanup is enabled.
- [ ] Hourly keep-alive job (`oauth-keepalive-prod`) is enabled so production tokens stay active (sandbox deprecated; production only).

---

### **Watchlist Loading (System Startup)** ⭐ PRIMARY

**Sources**: 
- **ORB Strategy**: `data/watchlist/core_list.csv` (currently **129** symbols - fully scalable)
- **0DTE Strategy**: `data/watchlist/0dte_list.csv` (currently **80** symbols — Tier 1: **9**, Tier 2: **71**; same project root as core_list)

**Process:**
1. **ORB Watchlist**: Load core_list.csv at startup (dynamically reads ALL symbols)
   - Pre-filtered with volatility, ATR, volume, performance metrics
   - Organized by leverage (4x, 3x, 2x, 1x) + Category (Quantum, Crypto, Tech)
   - All symbols used for ORB capture (no hardcoded limits)
   - Multi-factor ranking with VWAP (27%), RS vs SPY (25%), ORB Vol (22%) - Rev 00108
   - **Add/remove symbols without code changes** (Rev 00058)

2. **0DTE / Trendline watchlist**: Load `0dte_list.csv` at startup (if ORB 0DTE and/or **Trendline** enabled)
   - Symbols merged with ORB watchlist (no duplicates) for **ORB capture**
   - Used for ORB 0DTE signal scan and for **Trendline** 7:30 build universe (typically full list when `TRENDLINE_USE_FULL_0DTE_LIST=true`)
   - **Add/remove symbols without code changes**

3. **Shared ORB Data**: ORB capture data used by **ORB SO**, **ORB 0DTE**, and **Trendline** (`orb_context`)

**Alert:**
- ✅ Symbol List Loaded (shows dynamic count loaded)
- Sent ONLY during market hours (6:30 AM - 4:00 PM PT)

**Benefits:**
- Instant startup (zero API calls)
- Proven profitability
- No dynamic building needed

**Validate Watchlist:**
- [ ] Symbol List Loaded alert (during market hours) shows expected counts (**verify** `core_list.csv` + `0dte_list.csv`; merged capture size changes with CSVs).
- [ ] If 0DTE enabled: 0dte_list.csv loaded and merged; no path or permission errors in logs.
- [ ] Files exist: `data/watchlist/core_list.csv`, `data/watchlist/0dte_list.csv` (if 0DTE enabled).

---

## 2) Service Startup (Cloud Run)

**Entry Point**: `main.py --cloud-mode`

**Initialization:**
- OAuth integration (validates tokens)
- Prime system configuration
- Unified configuration loading (65+ settings - Rev 00201)
- HTTP server with endpoints
- Market hours check
- 0DTE strategy initialization (if `ENABLE_0DTE_STRATEGY=true`)

**Endpoints:**
- `GET /health` - Health check
- `GET /status` - System status
- `GET /metrics` - Performance metrics
- `POST /api/end-of-day-report` - EOD trigger
- `GET /api/positions` - Position tracking (Rev 00068)

**Market Hours Behavior:**
- During market: Normal initialization with alerts
- After hours: Silent initialization without alerts
- Container restarts: Load watchlist silently

**Configuration Loading** (Rev 00201-00202):
- `configs/ORBSO.env`: Capital allocation (90% SO / 10% Reserve)
- `configs/position-sizing.env`: Position sizing rules
- `configs/risk-management.env`: Exit settings (65+ configurable settings)
- `configs/deployment.env`: Strategy enablement (ORB/0DTE)

---

## 3) Morning Alert & Holiday Check (5:30 AM PT / 8:30 AM ET) ⭐ FIRST ALERT ⭐ Rev 00233 Enhanced

**Timing**: 5:30 AM PT (8:30 AM ET) - 1 hour before market open
**Time Validation**: Only sends between 5:30-5:35 AM PT (8:30-8:35 AM ET) - Rev 00233
**Deduplication**: GCS-based (one alert per day maximum) - Rev 00233

**Component**: Prime Alert Manager + Dynamic Holiday Calculator

**Process:**

### **Step 1: Holiday Detection (Rev 00137)**
1. Check if today is a holiday using `should_skip_trading()`
2. Detects both bank holidays (market closed) AND low-volume holidays (skip trading)
3. **19 days per year** skipped (10 bank + 9 low-volume holidays)
4. Calculated mathematically for any year (future-proof)

### **Step 2: Holiday Alert (If Holiday Detected)**
If holiday detected:
- Send holiday alert with vacation emojis 🎭 ☁️🏖️🏝️⛱️🌤️☁️☁️
- Disable trading for the day
- System enters sleep mode
- Different emoji for bank (🏖️) vs low-volume (🎃) holidays
- Skip all trading windows (ORB, SO, monitoring)

**Holiday Alert Example**:
```
====================================================================

🎃 Holiday! - Halloween
          Friday, October 31, 2025

🎭 No Trading Today! ☁️🏖️🏝️⛱️🌤️☁️☁️

🚫 Status:
          Trading DISABLED today.

💡 Why:
          Market is open, but volume is typically low on this holiday.
          Trading disabled to preserve capital quality.

✅ System Status: Normal
🔍 Next Trading: System will resume at next normal trading day
```

### **Step 3: Token Validation (If Normal Trading Day)**
If NOT a holiday:
1. Check production access token from Secret Manager
2. Check production access secret from Secret Manager
3. Both must be valid for trading to proceed

### **Step 4: Good Morning Alert (Normal Trading Day)** ⭐ Rev 00233 Enhanced
If tokens valid:
- **Time Validation**: Only sends if called between 5:30-5:35 AM PT (prevents wrong-time alerts)
- **Deduplication Check**: GCS marker prevents duplicate alerts (one per day)
- Send Good Morning alert with clouds and dove ☁️☁️🌤️☁️☁️☁️🕊️☁️
- Display token status (valid/expired)
- Show configuration mode (Demo/Live)
- System ready status
- **Protection**: Rejects calls outside valid window with warning log

**Validate Morning / Holiday:**
- [ ] On a trading day: Good Morning alert received between 5:30–5:35 AM PT (token status, Demo/Live, ORB/execution times).
- [ ] Only one Good Morning per day (GCS deduplication); no duplicate if scheduler retries.
- [ ] On a holiday (19 days/year): Holiday alert received instead of Good Morning; trading disabled for the day.
- [ ] If tokens invalid: OAuth Morning Alert or token warning in Good Morning content.

**Good Morning Alert Example**:
```
====================================================================

☁️☁️🌤️☁️☁️☁️🕊️☁️ Good Morning! ☁️☁️🌤️☁️☁️☁️🕊️☁️
          Time: 05:30 AM PT (08:30 AM ET)

✅ Token Status:
          E*TRADE tokens are VALID ✅

📊 System Mode: Demo Trading
💎 Status: Trading system ready and operational

🎯 Today's Trading:
          • ORB Capture: 06:45 AM PT
          • SO Execution: 07:30 AM PT
          • Monitoring: 07:30 AM - 12:55 PM PT
```

---

## 4) ORB Capture (6:30-6:45 AM PT / 9:30-9:45 AM ET) ⭐ CRITICAL

**Definitions:** The **Opening Range** is the first 15 minutes after market open only (6:30-6:45 AM PT). ORB High and ORB Low come from this window only. The **7:00-7:15 AM PT** bar is **not** an opening range (market open is earlier); it is the **validation candle** used only for volume color (GREEN/RED) and for the rule “validation candle close above ORB high / below ORB low.” Do not use 7:00-7:15 high/low as ORB high/low.

**Timing**: 6:30-6:45 AM PT (9:30-9:45 AM ET) - First 15 minutes after market open

**Component**: Prime ORB Strategy Manager

**Process:**

### **Step 1: Capture Window**
1. **Start**: 6:30 AM PT (market open)
2. **End**: 6:45 AM PT (15 minutes after open)
3. **Trigger**: Alert sent at 6:45 AM PT (ensures complete range)

### **Step 2: Data Collection**
1. **Primary Method**: E*TRADE batch quotes
   - Batch request for all symbols in core_list.csv (ORB Strategy)
   - Batch request for all symbols in 0dte_list.csv (0DTE Strategy)
   - **Batch Limit**: 25 symbols per API call (Rev 00247: Enforced to prevent error 1023)
   - Today's OHLC = ORB high/low
   - Processing: 2-5 seconds for all symbols (automatically batched)
   - Success rate: ~100% (all symbols captured)
   - **Shared Data**: ORB data used by both ORB Strategy and 0DTE Strategy

2. **Late Start Backfill** (Rev 00247): If system starts after 6:45 AM PT
   - System automatically backfills ORB data from today's market
   - ORB capture alert sent after backfill completes
   - Ensures alert is sent even when system starts late

3. **No Fallback**: System stops if broker fails (no third-party backup - Rev 00236)
   - Ensures data quality and consistency
   - Prevents trading on unreliable data sources

### **Step 3: Data Storage**
- ORB high/low stored for entire trading day
- Used for:
  - Breakout detection (price > ORB high)
  - Entry bar protection (volatility calculation)
  - Stop loss calculation (tiered stops 2-8%)

### **Step 4: ORB Capture Alert**
Sent at 6:45 AM PT (or after backfill if system starts late - Rev 00247) with:
- Number of symbols captured (**dynamic** merged `core_list` ∪ `0dte_list`)
- Capture method (E*TRADE batch quotes)
- Processing time
- Any errors (no fallback; broker-only — see Data.md)
- **Rev 00247**: Alert now reliably sent even when system starts after ORB window
- **Shared Data**: ORB data available for both ORB Strategy and 0DTE Strategy

**Validate ORB Capture:**
- [ ] ORB Capture Complete alert received at or after 6:45 AM PT with non-zero merged symbol count (matches union of CSVs).
- [ ] Alert shows method: E*TRADE Batch Quotes; processing time 2–5 seconds typical.
- [ ] If 0DTE enabled: alert includes 0DTE ORB Capture subsection with 0DTE symbol count.
- [ ] No "ORB Capture Failed" alert (if failed, scan will show 0 signals later).
- [ ] Logs: "ORB capture complete alert sent (N symbols)" and no batch/API errors.

**ORB Capture Alert Example**:
```
====================================================================

✅ ORB Capture Complete
          Time: 06:45 AM PT (09:45 AM ET)

📊 Capture Summary:
          • Symbols Captured: 205
          • Method: E*TRADE Batch Quotes
          • Processing Time: 3.2 seconds
          • Success Rate: 100.0%

✅ Status: All symbols captured successfully
```

---

## 5) Signal Collection & Rules Confirmation (7:15-7:30 AM PT / 10:15-10:30 AM ET) ⭐ PRIMARY

**Timing**: 7:15-7:30 AM PT (10:15-10:30 AM ET) - 15-minute validation window

**Components**: 
- **ORB Strategy**: Prime ORB Strategy Manager
- **0DTE Strategy**: Prime 0DTE Strategy Manager (if enabled)

**Process:**

### **Step 1: ORB Strategy - Prefetch (7:15 AM PT)**
1. Load previous candle data (7:00-7:15 AM PT): 7:00 opens from in-memory store or GCS `daily_markers/validation_open_700/YYYY-MM-DD.json` (Rev 00270); 7:15 close via E*TRADE batch quotes. **Full ORB + 0DTE symbol list** (no cap).
2. Prepare for signal validation (volume color GREEN/RED/NEUTRAL; prev-candle close vs ORB high).
3. No alert sent (internal process). If 7:00 snapshot was never captured, all symbols are NEUTRAL → 0 SO signals.

**Validate Prefetch (7:15 AM PT):**
- [ ] Logs: "SO WINDOW OPENED: Pre-fetching 7:00-7:15 AM PT validation candle…" on first entry to SO window.
- [ ] Either "Using E*TRADE 7:00 + 7:15 prices" or "Loaded 7:00 open prices from GCS" (no "No 7:00 AM PT snapshot").
- [ ] Volume color counts logged (GREEN / RED / NEUTRAL) with at least some non-NEUTRAL if 7:00 was captured.
- [ ] Prefetch coverage: full symbol list (no 100-symbol cap); "Prefetch coverage: N/M" with N = M or high.

### **Step 2: ORB Strategy - Continuous Scanning (7:15-7:30 AM PT)**
1. **Frequency**: Every 30 seconds (30 scans total)
2. **Validation Rules** (3 strict rules):
   - **Price**: Must break above ORB high
   - **Volume Color**: Must be green/positive
   - **Previous Candle**: Must validate previous candle pattern

3. **Signal Collection**:
   - Collects 6-15 qualified signals from all symbols
   - Tracks timing logs (when each signal appears - Rev 00055)
   - Stores signal metadata for ranking

### **Step 3: ORB Strategy - Ranking (Multi-Factor Priority Scoring)**
**Formula v2.1** (Rev 00108 - Deployed Nov 6, 2025):
- **VWAP Distance**: 27% (strongest predictor - +0.772 correlation)
- **RS vs SPY**: 25% (2nd strongest - +0.609 correlation)
- **ORB Volume**: 22% (moderate - +0.342 correlation)
- **Confidence**: 13% (weak - +0.333 correlation)
- **RSI**: 10% (context-aware)
- **ORB Range**: 3% (minimal contribution)

**Result**: System prioritizes market leaders (high RS vs SPY) with institutional support (above VWAP)

### **Step 4: ORB Strategy - Selection & Risk Management**
- Top 15 affordable signals pre-selected
- Filtered by affordability (share price vs fair share)
- Position sizing calculated (rank-based multipliers)
- Capital allocation validated (90% target)
- **Final confirmed SO trades** ready for execution

### **Step 5: 0DTE Strategy - Signal Reception & Rules Confirmation** (if enabled)
1. **Signal Reception**: Receives ORB signals from ORB Strategy during SO Signal Collection window
2. **Convex Eligibility Filter**: 
   - Score ≥ 0.75 (8 criteria validation)
   - Uses ORB data for eligibility filtering
3. **Strategy Selection**: Long call, debit spread, credit spread, or lotto
4. **Strike Selection**: Delta validation (0.15-0.35 for debit spreads), premium checks, liquidity validation
5. **Position Sizing**: Capital allocation, max position limits
6. **Risk Management**: Hard gate validation, liquidity checks, position size validation
7. **Red Day Filtering**: LONG (CALL) trades rejected on Red Days; SHORT (PUT) trades allowed
8. **Final confirmed 0DTE options trades** ready for execution

### **Step 6: Signal Collection Alert**
Sent at 7:30 AM PT (start of execution window) with:
- **ORB Strategy**: Number of qualified signals found (typically 6-15), signal validation summary, top-ranked signals preview, capital allocation preview
- **0DTE Strategy**: Number of qualified options trades found, strategy types, strike selection summary, capital allocation preview
- Both lists represent **final execution-ready trades** confirmed to open positions

**Validate Signal Collection:**
- [ ] Trade Signal Collection alert received at 7:30 AM PT (single alert for SO + 0DTE).
- [ ] SO count and 0DTE count (if enabled) match expectations; when 0 signals, alert may include diagnostic reason (`zero_signals_reason`).
- [ ] Logs: SIGNAL COLLECTION DIAGNOSIS block present (rejection reason counts, volume color, sample symbols) for troubleshooting if 0 signals. When 0DTE 0 qualified: `CONVEX_FILTER | 0_eligible` line and check-by-check failure counts (Rev 00292).
- [ ] No "No ORB data available" or "CRITICAL: No ORB data" during scan (would indicate ORB capture failure).
- [ ] GCS deduplication: only one Signal Collection alert per day per mode.

**Signal Collection Alert Example**:
```
====================================================================

📊 Trade Signal Collection Complete
          Time: 07:30 AM PT (10:30 AM ET)

📈 Signals Found: 12
          • Qualified: 12
          • Filtered: 0
          • Top Ranked: QQQ, SPY, TQQQ, SOXL, UPRO

💰 Capital Allocation:
          • SO Capital: $900 (90%)
          • Reserve: $100 (10%)

✅ Status: Ready for batch execution
```

---

## 6) Trade Execution & Trendline Build (7:30 AM PT / 10:30 AM ET) ⭐ PRIMARY

**Timing**: 7:30 AM PT (10:30 AM ET) — **ORB SO + ORB 0DTE** batch execution; **Trendline** candidate **build** (no batch options fills here)

**Components**: 
- **ORB SO**: Prime Trading System + Prime Risk Manager
- **ORB 0DTE**: Prime 0DTE Strategy Manager + Options Executor
- **Trendline 0DTE**: `PrimeTradingSystem` trendline pipeline + `TrendlineSignalEngine` / `select_pre730_structure_setup` (when `ENABLE_TRENDLINE_STRATEGY`)

**Process:**

### **ORB Strategy Execution**

### **Step 1: ORB Position Sizing (6-Step Flow - Rev 00090)**

**6-Step Process**:
1. **Apply Rank Multipliers** (3.0x, 2.5x, 2.0x, 1.71x, 1.5x, 1.2x, 1.0x)
2. **Apply Max Position Cap** (35% - from MAX_POSITION_SIZE_PCT)
3. **Apply ADV Limits** (Slip Guard - 1% ADV cap if enabled)
4. **Normalize to Target Allocation** (90% - from SO_CAPITAL_PCT)
5. **Constrained Sequential Rounding** (whole shares, maximize deployment)
6. **Post-Rounding Redistribution** ⭐ NEW - Redistributes unused capital to top signals

**Result**: 88-90% capital deployment guaranteed

### **Step 2: ORB Trade Execution**
- Up to 15 best trades executed simultaneously from **final confirmed SO trades list**
- Demo Mode: Mock executor (simulated trades)
- Live Mode: E*TRADE API (real trades)
- Trade IDs: Shortened format (Rev 00231)
  - Format: `DEMO_QQQ_260106_485_488_c_704400`
  - Applied to: All ORB trade types

### **0DTE Strategy Execution**

### **Step 3: 0DTE Options Execution**
- Trades executed from **final confirmed 0DTE options trades list**
- Options Chain: Fetched from E*TRADE API for each confirmed symbol
- Strike Selection: Already validated during Signal Collection (delta, premium, liquidity)
- Debit spread payoff quality checks applied before send:
  - `0DTE_MIN_RISK_REWARD`
  - `0DTE_MIN_MAX_PROFIT_PER_SPREAD`
  - `0DTE_MAX_DEBIT_TO_WIDTH_PCT`
- Rejections are logged with explicit reason codes (`poor_payoff_profile`, `spread_selection_failed`, etc.)
- Position Sizing: Already calculated during Signal Collection (capital allocation, max limits)
- Trade Limit: Maximum 15 concurrent positions (already validated)
- Demo Mode: MockOptionsExecutor (simulated options trades)
- Live Mode: E*TRADE Options API (real options trades)
- Trade IDs: Shortened format (same as ORB trades)

### **Step 4 (Trendline): Trendline 7:30 Build** (when enabled)

- Chunked intraday + quote merge → **`select_pre730_structure_setup`** per symbol (`TRENDLINE_PIPELINE` / `build_summary`).
- Valid setups attach **`TrendlineDefinition`** and enter **`WAITING_FOR_BREAK`** — **no** Trendline Options Execution alert until intraday confirmation + fill.
- Direction selection: **`classify_orb_test_failure`** (structure-first); see [0DTETrendline.md](0DTETrendline.md).

### **Step 5: Execution Alerts** ⭐ Rev 00231 Enhanced

Sent immediately after **ORB SO / ORB 0DTE** execution with **enhanced formatting** (Trendline uses separate execution alerts on intraday fills):

**ORB Execution Alert** (Rev 00231):
- **Bold Priority Rank**: `<b>Rank #1</b>`
- **Bold Priority Score**: `<b>Priority Score: 0.856</b>`
- **Bold Confidence**: `<b>Confidence: 85%</b>`
- **Bold Momentum**: `<b>Momentum: 75/100</b>`

**0DTE Execution Alert**:
- **Bold Priority Rank**: `<b>Rank #1</b>`
- **Bold Priority Score**: `<b>Priority Score: 0.856</b>`
- **Bold Delta**: `<b>Delta: 0.25</b>`
- **Bold Strategy Type**: `<b>Debit Spread</b>` or `<b>Long Call</b>`
- **Bold Strike Selection**: `<b>Strike: 450/455</b>`

**Validate Execution:**
- [ ] ORB SO: Standard Order Execution alert received with trades executed, capital deployed, and per-position details (rank, score, confidence, momentum, trade ID).
- [ ] ORB 0DTE (if enabled): 0DTE Options Execution alert received with options positions, strategy types, strikes, delta.
- [ ] Trendline (if enabled): build telemetry (`build_summary`, `setup_detected` / `setup_skipped`) — **not** expecting a Trendline batch execution alert at 7:30.
- [ ] When SO signals existed and no Red Day: at least one ORB execution (or explicit Red Day / execution-skipped messaging).
- [ ] When 0DTE signals existed: 0DTE execution alert; positions match Signal Collection qualified list.
- [ ] Logs: "PRIORITIZED ORDER QUEUE" and batch execution completion; no executor/API errors.

**Note**: ORB SO + ORB 0DTE execution alerts fire **after** those batch trades. The Signal Collection alert lists **SO + 0DTE** execution-ready names only — Trendline candidates are built **outside** that combined list (see [0DTETrendline.md](0DTETrendline.md)).

**Diagnosis when Signal Collection has 0 signals:** (1) **ORB capture empty** — scan exits immediately; check ORB Capture alert. (2) **No 7:00 snapshot** — prefetch yields all NEUTRAL; ensure 7:00 AM PT job runs and/or GCS has `daily_markers/validation_open_700/YYYY-MM-DD.json`. (3) **All symbols failed SO rules** — check logs for SIGNAL COLLECTION DIAGNOSIS (rejection reason counts, volume color GREEN/RED/NEUTRAL, sample symbols). (4) **Red Day** — blocks ORB SO long / ORB 0DTE CALL execution paths per policy; diagnosis differs from “no symbols scanned.” (5) **Trendline** — still runs its **~7:30 build** from **`0dte_list`** when enabled; **no SO/0DTE signals does not imply Trendline skipped**. Inspect **`TRENDLINE_PIPELINE`** / `build_summary` / `setup_skipped`.

**ORB Execution Alert Example** (Rev 00231):
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

          • SPY - 8 shares @ $485.00
            <b>Rank #2</b> | <b>Priority Score: 0.823</b>
            <b>Confidence: 82%</b> | <b>Momentum: 68/100</b>
            Trade ID: DEMO_SPY_260106_485_488_c_704401
```

---

## 7) Position Monitoring (7:30 AM - 12:55 PM PT / 10:30 AM - 3:55 PM ET)

**Timing**: Throughout trading day

**Components**: 
- **ORB ETF**: Prime Stealth Trailing TP (`prime_stealth_trailing_tp`)
- **ORB 0DTE**: Shared options stealth engine (`orb_options_stealth` / `prime_options_stealth_trailing_tp`)
- **Trendline 0DTE**: **`TrendlineOptionsStealthEngine`** (`trendline_options_stealth`)

**Process:**

### **Monitoring Frequency**
- **ORB ETF**: Main loop ~**30 seconds**
- **ORB 0DTE options**: **~5-second fast-monitor** loop (shared-loop backup); premium resolved from chain/quotes per stealth rules
- **Trendline 0DTE options**: **~5-second** dedicated fast loop (`TRENDLINE_POSITION_MONITOR_INTERVAL_SEC`)
- **Rev 00319 reliability**: Demo options monitoring/exits use the mock options position store as source-of-truth where applicable.
- **Rev 00347 ORB 0DTE monitor hardening**: lock/flag re-entrancy protection, heartbeat summaries, fallback usage counters, and premium-source diagnostics (`OPTIONS_STEALTH | stage=orb_monitor_*`, `stage=orb_premium_source_summary`).
- **Health Checks**: Every 15 minutes (~21 checks per day) — portfolio-level across paths

### **Exit Settings** ⭐ Rev 00196 - OPTIMIZED

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
12. **End of Day Close**: 12:55 PM PT auto-close all positions

**Portfolio-Level Health Checks** (2):
13. **Emergency Exit**: 3+ red flags → Close ALL positions (every 15 min)
14. **Weak Day Exit**: 2 red flags → Close losing positions (every 15 min)

**All Settings Configurable** (Rev 00201):
- ✅ 65+ configurable settings via `configs/risk-management.env`
- ✅ No hardcoded values
- ✅ Single source of truth

### **Entry Bar Protection** 🛡️ Rev 00135

**Permanent Floor Stops**:
- Based on actual ORB volatility (2-8% stops)
- Maintained for entire trade (breakeven and trailing can move up but NEVER below floor)
- Prevents early exits at 30 minutes

**Tiered Stops**:
- **9%+ volatility**: 8% EXTREME stop
- **6-9% volatility**: 8% EXTREME stop
- **3-6% volatility**: 5% HIGH stop
- **2-3% volatility**: 3% MODERATE stop
- **<2% volatility**: 2% LOW stop

### **Exit Alerts**

**ORB Individual Exits** (Rev 00184 - Fixed Formatting):
- Clear exit reason
- Entry/exit prices
- P&L highlighted
- Hold time displayed
- Peak price reached
- Trade ID (shortened format - Rev 00231)

**0DTE Individual Exits**:
- Clear exit reason (Partial Profit, Runner Exit, Stop Loss, etc.)
- Entry/exit prices
- P&L highlighted
- Hold time displayed
- Peak price reached
- Strategy type (Debit Spread, Long Call, etc.)
- Trade ID (shortened format)

**Aggregated Exits** (Rev 00078 - Batch Closes):
- **ORB ETF Aggregated Exit**: ONE alert for all ORB SO positions closed
- **ORB 0DTE Aggregated Exit**: ONE alert for all ORB 0DTE option positions closed
- **Trendline 0DTE Aggregated Exit** (when enabled): Trendline ledger batch close at EOD window
- Summary of exit reasons
- Total P&L per strategy
- Number of positions closed per strategy
- Prevents duplicate notifications

**Validate Position Monitoring:**
- [ ] ORB ETF positions update every 30 seconds; ORB 0DTE and Trendline 0DTE options update on ~5-second fast monitor cadence (logs or position endpoint).
- [ ] Individual exit alerts received for each closed position (trailing, breakeven, rapid, stop, etc.) with exit reason, P&L, trade ID.
- [ ] No unexpected "position not found" or price-fetch errors in logs during session.

---

## 8) Portfolio Health Checks (Every 15 Minutes)

**Timing**: 7:45 AM - 12:45 PM PT (every 15 minutes, ~21 checks per day)

**Component**: Prime Trading System

**Process:**

### **Health Check Frequency** (Rev 00067)
- **Frequency**: Every 15 minutes (~21 checks per day)
- **Not**: Once per day (was fixed in Rev 00067)

### **Red Flags Monitored** (Rev 00168 Enhanced):
- Win rate <35%
- Avg P&L <-0.5% (kept at -0.5% to avoid premature exits on recoverable days)
- Low momentum <40%
- Weak peaks <0.8%
- **All positions losing (100% losers)** ⭐ NEW

### **Actions**:
- **EMERGENCY (3+ red flags)**: Close ALL positions immediately
- **WARNING (2 red flags)**: Close weak positions (P&L < -0.5%)
- **OK (0-1 red flags)**: Continue normal trading (no alert, log only)

### **Health Check Alerts**

**Emergency Alert Example**:
```
====================================================================

🚨 EMERGENCY EXIT TRIGGERED
          Time: 08:15 AM PT (11:15 AM ET)

⚠️ Red Flags Detected: 4
          • Win Rate: 20% (<35%)
          • Avg P&L: -0.8% (<-0.5%)
          • Momentum: 25% (<40%)
          • All Positions Losing: 100%

🔄 Action: Closing ALL positions immediately
```

**Validate Health Checks:**
- [ ] Health checks run every 15 minutes (~21/day); logs show check time and red-flag counts.
- [ ] If EMERGENCY (3+ red flags): alert received and all positions closed.
- [ ] If WARNING (2 red flags): alert received and weak positions closed.
- [ ] OK (0–1 red flags): no alert, log only.

---

## 9) End-of-Day Close (12:55 PM PT / 3:55 PM ET)

**Timing**: 12:55 PM PT (3:55 PM ET) - 5 minutes before market close

**Components**: 
- **ORB SO / ETF**: Prime Trading System
- **ORB 0DTE**: Prime 0DTE Strategy Manager
- **Trendline 0DTE**: Trendline account / stealth close path (when enabled)

**Process:**

### **Step 1: Force Close All Positions**
- **ORB ETF**: All open SO positions closed automatically
- **ORB 0DTE**: All open ORB 0DTE option positions closed automatically
- **Trendline 0DTE**: All open Trendline option positions closed automatically (separate ledger)
- Never holds overnight (each strategy path)
- Aggregated exit alerts (Rev 00078) — **per path** where applicable

### **Step 2: Aggregated Exit Alerts** (Rev 00078)
- **ORB ETF Aggregated Exit**: ONE alert for all ORB SO positions closed
- **ORB 0DTE Aggregated Exit**: ONE alert for all ORB 0DTE positions closed
- **Trendline 0DTE**: Batch close messaging per Trendline alerting
- Summary of exit reasons per strategy
- Total P&L for the day per strategy
- Number of positions closed per strategy
- Individual position details (if space permits)
- Prevents duplicate notifications

**Validate EOD Close:**
- [ ] At 12:55 PM PT: all **ORB ETF**, **ORB 0DTE**, and **Trendline** positions closed per ledger (no overnight holds).
- [ ] ORB ETF Aggregated Exit alert received with summary (positions closed, total P&L, exit reasons).
- [ ] If ORB 0DTE had open positions: ORB 0DTE Aggregated Exit alert received.
- [ ] If Trendline had open positions: Trendline batch close / aggregated messaging per current alerting.
- [ ] Logs confirm force-close and order placement for any remaining positions.

**EOD Close Alert Example**:
```
====================================================================

🔄 <b>End of Day Close</b>
          Time: 12:55 PM PT (03:55 PM ET)

📊 Summary:
          Positions Closed: 6
          Total P&L: +$45.23 (+5.7%)

📈 Positions:
          • QQQ: +$12.50 (+2.1%) - Trailing Stop
          • SPY: +$8.75 (+1.8%) - Breakeven
          • TQQQ: +$15.20 (+3.2%) - EOD Close
          • SOXL: +$4.50 (+0.9%) - EOD Close
          • UPRO: +$2.28 (+0.5%) - EOD Close
          • NEBX: +$2.00 (+0.3%) - EOD Close
```

---

## 10) End-of-Day Report (1:05 PM PT / 4:05 PM ET)

**Timing**: 1:05 PM PT (4:05 PM ET) - After market close (10 minutes after EOD close)  
**Source**: Single source - Cloud Scheduler endpoint only (Rev 00260)  
**Deduplication**: GCS-based (prevents duplicate reports)

**Components**: 
- **ORB ETF**: Prime Alert Manager (`_send_demo_eod_summary`, `_send_live_eod_summary`)
- **ORB 0DTE**: Prime Alert Manager (`send_options_end_of_day_report`)
- **Trendline 0DTE**: Trendline EOD summary path when enabled (see [Alerts.md](Alerts.md))

**Process:**

### **Step 1: Cloud Scheduler Trigger**
- **Endpoint**: `POST /api/end-of-day-report` (main.py:443)
- **Schedule**: Cloud Scheduler job `end-of-day-report` at 4:05 PM ET (1:05 PM PT) weekdays
- **Holiday Check**: Validates trading day before sending (skips weekends/holidays)
- **Single Source**: Only Cloud Scheduler triggers EOD reports (internal trading loop triggers removed - Rev 00260)

### **Step 2: Report Generation**
- **ORB ETF**: Daily performance summary, weekly summary (if Friday), account balance (Demo/Live), trade statistics
- **ORB 0DTE**: Daily performance summary, weekly summary (if Friday), account balance (Demo/Live), options trade statistics
- **Trendline 0DTE** (when enabled): Trendline-specific daily summary / Telegram title family (**Easy Trendline 0DTE**)
- **Combined Metrics**: Analysts may aggregate across ledgers externally; reports remain **per path**

### **Step 3: GCS Persistence** ⭐ Rev 00203
- Trade history persists immediately to GCS (ORB ETF, ORB 0DTE, Trendline accounts as configured)
- Account balances persist between deployments per ledger
- Mock histories persist across redeployments per path
- Trade persistence bug fixed (Rev 00203)

### **Step 4: GCS Deduplication** ⭐ Rev 00260
- **ORB ETF EOD**: GCS marker prevents duplicate reports (`eod_markers/demo_eod_sent_{date}.txt` or `live_eod_sent_{date}.txt`)
- **ORB 0DTE EOD**: GCS marker prevents duplicate reports (`eod_markers/options_eod_sent_{date}_{mode}.txt`)
- **Trendline**: Marker behavior per Trendline reporter implementation (when enabled)
- **Safety Net**: Even if Cloud Scheduler retries or multiple instances exist, only one scheduled report per day **per report family**

### **Step 5: EOD Report Alerts**
- **ORB ETF EOD Report**: Daily + weekly summary for ORB SO
- **ORB 0DTE EOD Report**: Daily + weekly summary for ORB 0DTE options
- **Trendline 0DTE EOD Report** (when enabled): Separate Trendline summary (see [Alerts.md](Alerts.md))
- **Timing**: Scheduled batch at **1:05 PM PT** (4:05 PM ET) via Cloud Scheduler endpoint

**Validate EOD Report:**
- [ ] Cloud Scheduler job `end-of-day-report` runs at 1:05 PM PT (4:05 PM ET) on weekdays.
- [ ] ORB ETF EOD Report alert received (daily P&L, weekly if Friday, account balance).
- [ ] If ORB 0DTE enabled: ORB 0DTE EOD Report alert received with options performance.
- [ ] If Trendline enabled: Trendline EOD summary received per alerting configuration.
- [ ] Only one report per **report family** per day (GCS markers prevent duplicates on retry).
- [ ] No EOD report on weekends/holidays (scheduler or endpoint skips).

**ORB EOD Report Example**:
```
====================================================================

🛃 END-OF-DAY REPORT | DEMO Mode

📈 P&L (TODAY):
          +2.75% +$27.49
          Win Rate: 100.0% • Total Trades: 25
          Wins: 25 • Losses: 0
          Profit Factor: 0.00
          Average Win: $1.10
          Average Loss: $0.00
          Best Trade: +$4.29
          Worst Trade: $+0.07

🎖️ P&L (WEEK M-F):
          +2.75% +$27.49
          Win Rate: 100.0% • Total Trades: 25
          Profit Factor: 0.00

💎 Account Balance: 
          $1,027.49

📅 Report Date: 2026-01-06
```

---

## 🔄 Mode Switching

### **Demo Mode → Live Mode**

**Prerequisites:**
1. ✅ 3-5 days successful Demo performance
2. ✅ Win rate >75%
3. ✅ Avg P&L >+$1.00 per trade
4. ✅ No system errors
5. ✅ OAuth tokens renewed

**Switch Command:**
```bash
# Replace YOUR_SERVICE_NAME with your actual service name (see PrivateSecrets.md)
gcloud run services update YOUR_SERVICE_NAME \
  --set-env-vars="ETRADE_MODE=live,DEPLOYMENT_MODE=live" \
  --region=us-central1
```

**Note**: Both Demo and Live modes use the same production E*TRADE API and OAuth tokens. The difference is which account is used (demo account vs live account).

**Verification:**
- Check logs for "💰 Live Mode: Initialized Prime Risk Manager"
- Verify E*TRADE connection successful
- Monitor first few trades closely

---

## 📊 Performance Tracking

### **Daily Metrics**
- Trades executed vs signals found
- Win rate (wins / total trades)
- Avg P&L per trade
- Total daily P&L
- Capital efficiency (deployed / available)
- Profit capture rate (Rev 00196)

### **Weekly Metrics**
- Total trades (5 days)
- Weekly P&L
- Weekly return %
- Compounding effect
- Consistency check

### **Monthly Metrics**
- Total trades (20 days)
- Monthly P&L
- Monthly return %
- Account growth
- Drawdown analysis

---

## ✅ System Status Summary

### **Current Deployment (Rev 00231+; Process Flow Rev 00260)**

**Deployment:**
- ✅ Rev 00231 deployed (Trade ID Shortening & Alert Formatting)
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
- ✅ Multi-factor ranking (VWAP 27%, RS vs SPY 25%, ORB Vol 22% - Rev 00108)
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

**Logging & analysis:**
- ✅ Pipeline logging (Rev 00260): `PIPELINE | STEP 1..5` from ORB Capture through Trade Execution for session analysis
- ✅ Convex filter diagnosis (Rev 00292): `CONVEX_FILTER | 0_eligible | total=N | top_failures:` when 0 pass; check-by-check failure counts

**Configuration:**
- ✅ Unified configuration system (65+ settings - Rev 00201)
- ✅ Single source of truth (Rev 00202)
- ✅ All settings configurable via `configs/` files

**Modes:**
- ✅ Demo Mode active: ORB sim $1,000, 0DTE sim $5,000 (mock balances); Live uses configured broker account
- ✅ Live Mode ready for deployment
- ✅ Trade persistence working (Rev 00203)

**Next Steps:**
- ✅ Monitor trading performance with optimized exit settings
- ✅ Verify all flows working correctly
- ✅ Track profit capture rate (expected 85-90%)
- ✅ Assess holiday filter effectiveness over time
- ✅ Prepare for Live Mode after 3-5 successful Demo days

---

## 🚀 Key Features

### **What Makes This Strategy Work**

**1. Simple & Proven Concept**
- Based on opening range breakout (time-tested)
- Clear entry rules (price above ORB high)
- Defined risk (ORB low is natural stop)

**2. Multi-Factor Priority Ranking** (Rev 00108)
- VWAP Distance (27%) - strongest predictor
- RS vs SPY (25%) - 2nd strongest
- ORB Volume (22%) - moderate
- Better predictor than confidence alone
- Best signals prioritized by composite score

**3. Rank-Based Position Sizing**
- Top rank gets 3.0x fair share (scales automatically)
- Fair share = SO capital / num signals
- Same PERCENTAGE across all account sizes

**4. Greedy Capital Packing**
- Maximizes trade count (up to 15 trades)
- 88-90% capital efficiency
- Automatic affordability handling

**5. Optimized Exit Settings** (Rev 00196)
- 0.75% breakeven activation after 6.4 min ⭐ OPTIMIZED
- 0.7% trailing activation after 6.4 min ⭐ OPTIMIZED
- 1.5-2.5% trailing distance
- Expected 85-90% profit capture (vs 67% current)

**6. Account Scalability**
- Works from $1K to $100K+
- Position sizing scales automatically
- Same strategy, different dollar amounts

---

## 📝 Documentation References

- **[docs/Risk.md](Risk.md)** - Risk management and position sizing details
- **[docs/ProcessFlow.md](ProcessFlow.md)** - This file - End-to-end process flow
- **[docs/Alerts.md](Alerts.md)** - Alert system documentation (ORB SO + ORB 0DTE + Trendline)
- **[docs/0DTETrendline.md](0DTETrendline.md)** - Easy Trendline 0DTE path (build, selector, stealth, telemetry)
- **[docs/0DTEORB.md](0DTEORB.md)** - ORB 0DTE primary reference (Convex, Hard Gate, execution)
- **[docs/Cloud.md](Cloud.md)** - Google Cloud deployment guide
- **[docs/CloudSecrets.md](CloudSecrets.md)** - Project scripts, cleanup (scripts deployed with app)
- **[docs/Settings.md](Settings.md)** - Configuration reference (65+ settings)
- **[docs/OAuth.md](OAuth.md)** - OAuth token management system
- **[docs/PrivateSecrets.md](PrivateSecrets.md)** - Sensitive deployment-specific information

---

**Ready for production trading with proven +73.69% weekly returns!** 🚀

---

## 📋 **Complete Trading Session Summary**

### **From Token Renewal to EOD Report - Complete Flow**

This document covers the **complete trading session** from OAuth token renewal through end-of-day reporting:

1. **OAuth Token Renewal** (12:00 AM ET daily)
   - Tokens expire at midnight ET
   - Renewal via web app (see PrivateSecrets.md for portal URL)
   - Tokens stored in Google Secret Manager
   - Hourly keep-alive prevents idle timeout
   - **Both Demo and Live modes use production E*TRADE API and tokens**

2. **Pre-Market Preparation** (5:00-6:30 AM PT)
   - Keep-alive pings every 3 minutes
   - Morning alert at 5:30 AM PT (token status, holiday check)
   - Holiday detection (19 days/year skipped)
   - Watchlist loading (`core_list` + `0dte_list`; Trendline uses merged ORB + same lists when enabled)

3. **ORB Capture** (6:30-6:45 AM PT)
   - Opening range capture for **merged** `core_list` ∪ `0dte_list` (dynamic count)
   - E*TRADE batch quotes (2-5 seconds)
   - Shared **`ORBData`** / **`orb_context`** for ORB SO, ORB 0DTE, and Trendline
   - Alert sent at 6:45 AM PT

4. **Signal Collection & Rules Confirmation** (7:15-7:30 AM PT)
   - **ORB SO**: LONG-only scan → ranking → sizing (`core_list`)
   - **ORB 0DTE**: Same rule stacks on **`0dte_list`** → Convex → Hard Gate → execution queue
   - **Trendline**: Does **not** depend on this combined alert for universe — uses **`0dte_list`** + merged bars at **~7:30 build**
   - Combined Signal Collection alert covers **SO + ORB 0DTE** readiness only

5. **7:30 Block — Execution + Trendline Build**
   - **ORB SO**: Batch execution of confirmed SO trades
   - **ORB 0DTE**: Options execution of confirmed ORB 0DTE trades
   - **Trendline**: Candidate **build** and watch registration (intraday fills later)

6. **Position Monitoring** (7:30 AM - 12:55 PM PT)
   - ORB ETF ~30s main loop
   - ORB 0DTE + Trendline options ~**5s** fast monitors (+ backups per path)
   - Separate stealth stacks: ETF vs shared options stealth vs **`TrendlineOptionsStealthEngine`**
   - Individual exit alerts as positions close

7. **Portfolio Health Checks** (Every 15 minutes)
   - Risk management checks
   - Emergency exit triggers
   - Warning alerts if needed

8. **End-of-Day Close** (12:55 PM PT)
   - Force close all positions **per ledger** (ORB ETF, ORB 0DTE, Trendline when enabled)
   - Separate aggregated exit alerts **per path**
   - Never holds overnight

9. **End-of-Day Report** (1:05 PM PT / 4:05 PM ET)
   - Single **scheduler trigger**: Cloud Scheduler endpoint (Rev 00260)
   - Separate daily summaries: **ORB ETF**, **ORB 0DTE**, **Trendline** (when enabled)
   - Weekly summaries (if Friday) where implemented
   - GCS persistence + deduplication per report family

### **Key Integration Points**

- **Shared ORB capture**: All three paths consume the same opening-range snapshot where applicable
- **Shared alerts**: Telegram delivery; distinct titles/blocks per path
- **Shared portfolio health**: 15-minute checks apply across open risk
- **Independent execution**: ORB SO vs ORB 0DTE batch vs Trendline event-driven
- **Independent ledgers**: ORB demo account vs ORB 0DTE demo account vs **`TrendlineAccountManager`**
- **Independent monitoring cadence**: ETF ~30s vs options fast loops ~5s

---

## ✅ **Trading Session Validation Checklist** (Critical Steps)

Use this list to validate each event in a trading session from OAuth through EOD. Each phase has detailed **Validate** subsections above; this is a single-page summary.

| # | Phase | Critical validation |
|---|--------|----------------------|
| 1 | **OAuth** | Midnight ET expiry alert (9 PM PT); Token Renewed confirmation after renewal; tokens in Secret Manager; hourly keepalive enabled. |
| 2 | **Watchlist** | Symbol List Loaded (market hours); core_list.csv + 0dte_list.csv present; no path errors. |
| 3 | **Morning / Holiday** | Good Morning 5:30–5:35 AM PT (or Holiday); one per day; token status and mode correct. |
| 4 | **ORB Capture** | ORB Capture Complete at/after 6:45 AM PT; non-zero symbol count; E*TRADE Batch Quotes; no ORB Capture Failed. |
| 5 | **Prefetch (7:15)** | Logs: SO window prefetch, 7:00+7:15 or GCS 7:00; volume color counts; full symbol list. |
| 6 | **Signal Collection** | Trade Signal Collection alert at 7:30 AM PT (SO + 0DTE); counts and diagnostic reason if 0 signals. |
| 7 | **Execution** | SO Execution + ORB 0DTE Execution (if enabled); Trendline **build** logs — no Trendline batch execution alert expected at 7:30. |
| 8 | **Monitoring** | ETF ~30 s; ORB 0DTE + Trendline options ~5 s fast paths; individual exit alerts with reason and P&L. |
| 9 | **Health** | Health checks every 15 min; EMERGENCY/WARNING alerts if triggered. |
| 10 | **EOD Close** | 12:55 PM PT: all positions closed **per ledger**; aggregated exits per ORB ETF / ORB 0DTE / Trendline. |
| 11 | **EOD Report** | 1:05 PM PT: Cloud Scheduler; separate EOD summaries for ORB ETF, ORB 0DTE, Trendline (when enabled); no report on weekends/holidays. |

**Pre-session (before 6:30 AM PT):** OAuth valid, Good Morning or Holiday received, watchlists loaded, 7:00 AM PT validation-candle job (`validation-candle-700`) scheduled; 7:15 AM PT prefetch job (`prefetch-validation-715`) recommended for scale-to-zero (see CloudSecrets.md and [TOMORROW_SESSION_CHECKLIST.md](doc_elements/Sessions/2026/Feb19%20Session/TOMORROW_SESSION_CHECKLIST.md)).

**Post-session:** EOD close and EOD report alerts received; trade history and GCS persistence as expected.

---

*Last Updated: April 30, 2026*  
*Version: Doc pass — **three-path** flow (ORB SO, ORB 0DTE, Trendline 0DTE), Trendline structure-first selector (`classify_orb_test_failure`), monitoring/EOD per ledger. Code baselines include Rev 00349 (ORB 0DTE fast monitor + execution reliability); Rev 00347 (SO winner-profile + ORB 0DTE monitor hardening); Rev 00328 (Hard Gate spread doc); Rev 00319 (payoff guardrails); Rev 00312 (ORB range %). Watchlist counts are **dynamic** — verify `core_list.csv` / `0dte_list.csv`.*
*Status: ✅ Production Ready — token renewal through EOD for all enabled paths*  
*Performance: +73.69% weekly return with 91% winning day consistency*  
*Capital Deployment: 88-90% guaranteed (6-step batch sizing + redistribution)*  
*Exit Settings: Optimized (Rev 00196: 0.75% breakeven, 0.7% trailing, 6.4 min activation - expected 85-90% profit capture)*  
*Position Sizing: Batch-sized quantities preserved (quantity_override)*  
*Priority Ranking: Multi-factor (VWAP 27%, RS vs SPY 25%, ORB Vol 22% - Rev 00108)*  
*Entry Bar Protection: PERMANENT FLOOR STOPS (Rev 00135) - ORB data passed for tiered stops 2-8%*  
*Exit System: All 14 triggers functional + verified integration*  
*Holiday Filter: 19 days/year skipped (10 bank + 9 low-volume, Rev 00137)*  
*Scalability: Dynamic symbol lists (`core_list` + `0dte_list` merged for capture; counts vary — no hard-coded universe size)*  
*Timezone: 100% DST-aware, works in EDT and EST*  
*Configuration: Unified configuration system (65+ settings - Rev 00201)*  
*Trade Persistence: GCS persistence working (Rev 00203)*  
*Complete Flow: End-to-end verified, no gaps in data passing*  
*Three paths: ORB SO + ORB 0DTE + Trendline 0DTE — shared ORB capture where applicable; independent execution ledgers*
