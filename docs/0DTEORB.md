# Easy ORB 0DTE

**Last Updated:** May 15, 2026 — **May 15 (local `BUILD_ID` `00349-20260515-may15-calibration-so-json-symbols`; Cloud Run deploy not performed this session):** ORB 0DTE runs in the same Cloud Run service as ORB SO and Trendline 0DTE (`ENABLE_0DTE_STRATEGY=true`). **Production revision until operator `deploy now`:** **`easy-etrade-strategy-00330-zdt`** (session summary). Local repo bundles sibling-path fixes: E*TRADE batch symbol aliases (**`CISCO`→`CSCO`**, **`NEBIUS`→`NBIS`** in `prime_data_manager.py` / `name_to_ticker`); ORB **SO** ranking **`json` closure** fix (`__import__("json").dumps` in `calculate_so_priority_score`) and **`_process_orb_signals` → bool** batch dedupe (**`SO_EXECUTION_FAILED | batch_dedupe_not_armed`** when ranking/execution fails); **execution policy layer** (telemetry, smart equity limits, capped 0DTE debit, urgency-aware options exits — defaults unchanged: **`USE_MARKET_ORDERS=true`**). Trendline **impulse calibration** is in **`docs/0DTETrendline.md`** and `docs/doc_elements/Sessions/2026/May15 Session/SESSION_SUMMARY_MAY15_2026.md`.

**May 14 (Rev 00348 continuation pass + selector forensics):** ORB **0DTE priority ranking** favors **continuation / early momentum** over raw breakout size, ORB width, and over-extension (`easy0DTE/modules/prime_0dte_strategy_manager.py`, `modules/orb0dte_execution_defaults.py`); **Convex** “momentum” credit no longer treats **ORB break alone** as proof unless **MACD > 0** or **volume_ratio ≥ 1.0** (`easy0DTE/modules/convex_eligibility_filter.py`). **Selector observability:** terminal **`momentum_scalper`** ladder rejects and **`debit_spread`** `spread_selection_failed` emit one structured **`INFO`** line **`ORB_0DTE_SELECTOR_FULL_REPLAY | {<json>}`** (full `selector_audit`, `tiers_attempted`, `skip_atm_tiers`, diagnostics, bounded near-spot contract snapshots; **240k**-char guard) — pair with **`0DTE_EXEC_REJECT`** / **`ORB_0DTE_SELECTOR_REJECTION_AUDIT`**. **Tier D semantics:** `select_lotto_strike` delegates to **`select_single_leg_contract`** with **`profile="directional"`** when **`0DTE_SELECTOR_FAILOVER_TARGET_DELTA ≥ 0.24`** (default **0.28**), so the final failover is **not** the low-delta **lotto** band unless config moves delta below that threshold. Local replay of rank math vs stored snapshots: **`scripts/predeploy_continuation_validation.py`**. Session narrative: `docs/doc_elements/Sessions/2026/May14 Session/SESSION_SUMMARY_MAY14_2026.md`.

**May 13, 2026 — May 13 execution + ops pass:** pre-queue observability (`0DTE_PREQUEUE_FUNNEL_SUMMARY`, `ORB_0DTE_EXECUTION_SKIP_STAGE`), ORB-mid **spot fallback** when signal price is missing at the open, **`0DTE_MIN_VIABILITY_THRESHOLD`** repo default **0.30** in `modules/orb0dte_execution_defaults.py` (profile/env can override), **`ORB_0DTE_CHAIN_HEALTH_RELAX_MIN_ELIGIBILITY=0.76`**, selector **momentum** threading + **`0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS`**, wider **`0DTE_DEBIT_SPREAD_WIDTH_OPTIONS`**, relaxed single-leg floors + open-window multipliers, **`ORB_0DTE_LIFECYCLE_AUDIT`** / lifecycle snapshot throttles, watchdog gap recovery on healthy ticks, **`flatten_status`** on **`close_all_positions`** + aggregated exit alerts, **`OPTION_STEALTH_ORB_*`** in `Shared.env` for degraded-spread / no-progress relief. **ORB batch scan:** Pacific date in `_scan_orb_batch_signals` no longer calls undefined `get_current_time()`. **May 11 execution-quality pass (retained):** ORB-distance **overextension** is no longer a single hard line at `0DTE_EXTENSION_THRESHOLD_PCT` only — the path uses **`ORB_0DTE_OVEREXTENSION_*`** tunables (soft priority penalty, strong-continuation bypass, extreme-only hard reject; legacy **`0DTE_EXTENSION_THRESHOLD_PCT`** still maps at load to **`ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD`** with **`CONFIG_DEPRECATED_ALIAS_USED`** / **`CONFIG_CANONICAL_KEY_OVERRIDES_ALIAS`** when applicable). **Chain health** may run a logged **fallback ladder** before final reject (**`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`**). **Two-stage durability** (execution vs exit-grade marks), **`strategy_type`** propagation onto positions/alerts, and **INFO** lifecycle tokens (**`ORB_0DTE_EXECUTION_*`**, **`ORB_0DTE_STRATEGY_TYPE_*`**) improve ops forensics. **ORB 0DTE debit spreads** under **`partial_leg` / non–exit-grade** marks can use **conservative then forced** timeout-exit relief so **`time_exit`**, **`no_progress_timeout`**, and **`fast_fail`** are not blocked indefinitely (**`ORB_OPTIONS_SPREAD_DEGRADED_EXIT_*`**, **`ORB_SPREAD_*`**; telemetry **`ORB_OPTIONS_EXIT_DEFERRED_AUDIT`**, **`ORB_OPTIONS_FORCED_DEGRADED_EXIT`**). **EOD:** three-path **`flatten_all_paths_for_eod_scheduler()`** + **`SO_ETF_EOD_CLOSE_*`** / **`POST /api/end-of-day-report`** flow documented in §5 End of Day. **Config surface:** Repo defaults for this path live in **`configs/ORB0DTE.env`** (same seven section groups as `ORBSO.env` / `Trendline0DTE.env`; see `configs/README.md`). The merged app loads **`Data.env` → `Shared.env` → `ORBSO.env` → `ORB0DTE.env` → `Trendline0DTE.env` → `Risk.env` → `Alerts.env`**; later files override earlier keys. Runtime reads use **`os.getenv`** plus env-helper reads (for example Convex min score via helper-backed env parse) throughout **`easy0DTE/modules/*`** and **`main.py`**, and **`get_config_value`** / **`os.getenv`** in **`modules/prime_trading_system.py`** / **`modules/prime_orb_strategy_manager.py`** (after `load_app_config()` exports merged config to **`os.environ`**). There is **no** single ORB-0DTE config loader class—the authoritative inventory is **`configs/ORB0DTE.env`** (the appendix snapshot below is a **partial** mirror; refresh it when you change the file). Cross-path caps and generic **`OPTION_STEALTH_*`** live in **`Shared.env`** / **`Risk.env`**; LONG 7:30 re-check vs ORB high uses **`SO_RECHECK_BREAKOUT_MULTIPLIER`** in **`ORBSO.env`**; SHORT side uses **`0DTE_RECHECK_BREAKDOWN_MULTIPLIER`** here. Full review tables: **[ORB0DTE_Path_Settings_Review.md](ORB0DTE_Path_Settings_Review.md)**.

**Status:** Primary ORB 0DTE documentation (production active)  
**Path code:** `easy0DTE/modules/*` + `modules/prime_trading_system.py` (0DTE execution), `main.py` (0DTE init), `modules/prime_options_stealth_trailing_tp.py` (single normal premium-based exit brain for ORB 0DTE + Trendline 0DTE).

---

## Overview

Easy ORB 0DTE is the options path that runs alongside:

1. ORB SO (ETF/stock) path
2. ORB 0DTE options path (this document)
3. Easy Trendline 0DTE path

The ORB 0DTE path uses shared ORB capture and signal windows, then executes a filtered options pipeline focused on high-conviction setups.

Core idea: **Selective convex amplification**. Not every ORB-qualified setup gets options.

---

## Current Production Baseline

- **Watchlists**: driven directly from `core_list.csv` and `0dte_list.csv` (dynamic counts; universe updates are applied via list maintenance). **`0dte_list.csv`** row order is the execution/diagnostic priority order (**SPX** first when present, then names aligned with **`core_list.csv`** top-to-bottom where both lists overlap); the **`tier`** column is metadata (e.g. Red Day CALL gating), not a reorder key when symbols are loaded from the CSV.
- **Shared ORB capture universe**: merged union of ORB SO + 0DTE symbol lists
- **Data policy**:
  - `REQUIRE_LIVE_OPTION_DATA=true`
  - `0DTE_DEMO_SYNTHETIC_CHAIN=false`
  - `OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA=true`
- **Live quote strictness policy**:
  - Production/live 0DTE: `OPTION_REQUIRE_LIVE_QUOTES=true` (recommended)
  - Local/dev simulation can keep `OPTION_REQUIRE_LIVE_QUOTES=false` when modeled proxy behavior is intentionally needed
  - `OPTION_PRICE_RESOLUTION_AUDIT_MIN_SECONDS=30` throttles per `trade_id|symbol` audit logs
- **Meaning**: Demo and Live both use real E*TRADE option chain/quote data for selection, monitoring, and exits.
- **Execution quality controls** include strictness profile, payoff guardrails, selector diagnostics, and reject telemetry.
- **Sibling Trendline production telemetry** (for shared run diagnostics): `SYSTEM_VERSION`, `TRENDLINE_CONFIRM_CONFIG`, `TRENDLINE_DRIFT_METRICS`, `TRENDLINE_ENTRY_DECISION` (details in `docs/0DTETrendline.md`). Trendline **7:30 geometry** is **structure-first** via `TrendlineBuilder.classify_orb_test_failure` when the **7:30 last close is inside the ORB band** (MSE fallback when classification is unclear). When the cutoff close is **strictly outside** ORB high/low, the selector uses **`build_cutoff_to_farthest_extreme`** (session extreme ↔ cutoff close) and **skips** the dual-line MSE contest for that symbol — see **`docs/0DTETrendline.md`**. Trendline intent is **exhaustion / reversal** into the break, not ORB SO / 0DTE **continuation-quality** ranking (do not copy SO priority weights into Trendline tuning). **May 15:** Trendline impulse entries additionally require **post-break continuation acceptance** and **2-of-3 momentum persistence** on fast paths — see **`docs/0DTETrendline.md`** header.
- **Watchlist tickers (Cisco / Nebius):** Production lists use **`CSCO`** and **`NBIS`** in `data/watchlist/core_list.csv` and `0dte_list.csv`. Company-name strings **`CISCO`** / **`NEBIUS`** are invalid E*TRADE symbols; batch quote paths normalize them via **`_ETRADE_SYMBOL_ALIASES`** when they appear upstream.
- **Expiry fallback policy**: `SPY/QQQ/IWM` are classified as `0DTE_NATIVE` and use strict same-day first, then nearest-expiry fallback if needed; non-native symbols can use nearest available expiry immediately when same-day is unavailable. Trace with `SYMBOL_CLASSIFICATION` and `CHAIN_EXPIRY_SELECTION`.

---

## Daily Flow (ORB 0DTE)

### 1) ORB Capture (6:30-6:45 AM PT, shared)

- Captures ORB high/low context for merged ORB + 0DTE universe.
- Uses broker data path only (E*TRADE).

### 2) Signal Collection (7:15-7:30 AM PT, shared window)

- ORB validation rules produce candidate direction.
- ORB 0DTE path receives both **LONG (CALL)** and **SHORT (PUT)** candidates.
- Convex filter and Hard Gate reduce candidates to execution-ready queue.

### 3) Execution (7:30 AM PT)

- Uses live options chain data to select contracts/legs and execute simulated or live orders.
- Strategy chooser supports lotto, long options, debit spreads, momentum/ITM variants depending on momentum and structure.
- Rejections are explicitly classified (for example `spread_selection_failed`, `poor_payoff_profile`) with diagnostics.
- **Concurrency:** at most **`0DTE_MAX_POSITIONS`** (default **6**) ORB 0DTE option positions at the 7:30 batch; new opens are also blocked when **ORB 0DTE + Trendline** open count ≥ **`MAX_TOTAL_OPTION_POSITIONS`** (default **11**) — see [Position and concurrency limits](#position-and-concurrency-limits-integrated-app) below.
- **May 15 execution policy (local, alpha unchanged):** Shared modules under **`modules/`** — `execution_telemetry.py`, `execution_routing.py`, `execution_profiles.py`, `execution_fill_reconcile.py`, `smart_equity_execution.py`. **ORB 0DTE opens:** `easy0DTE/modules/etrade_options_api.py` — **`max_net_debit`** required on debit spreads (progressive `limitPrice` caps); single-leg **`place_single_option_buy_open_smart`** with opening profiles and anti-stall timeouts. **ORB 0DTE closes:** `options_trading_executor.py` — `resolve_options_exit_plan` (URGENT / MODERATE / PASSIVE), last-look spread check, LIMIT vs MARKET. **ORB SO equity** (sibling): `prime_unified_trade_manager.py` + `prime_stealth_trailing_tp.LiveETradeAdapter`. Smart path is **off** until **`USE_MARKET_ORDERS=false`** (default `true`; `ENABLE_SMART_EXECUTION=true`). **Grep:** `EXECUTION_FILL_SUMMARY`, `EXECUTION_LIMIT_ATTEMPT`, `EXECUTION_REPRICE`, `EXECUTION_MARKET_FALLBACK`, `EXECUTION_FORCE_FALLBACK`, `EXECUTION_TIMEOUT_ABORT`, `EXECUTION_AGGRESSION_ESCALATED`, `EXECUTION_FILL_RECONCILED`, `EXECUTION_PARTIAL_FILL`, `EXECUTION_SLIPPAGE_GUARD_REJECT`, `LAST_LOOK_REJECT`.

### 4) Monitoring and Exits (intraday)

- ORB 0DTE uses a dedicated fast monitor (~**7s** baseline via `ORB_0DTE_POSITION_MONITOR_INTERVAL_SEC`, subject to dynamic throttling and latency backoff) with shared-loop backup.
- Exit logic uses options premium behavior (not underlying-only proxies).
- With live-data-only settings, degraded/non-live premium sources are generally blocked from making **normal** exit decisions; **ORB 0DTE debit spreads** may still take **timeout-class** exits (**`time_exit`**, **`no_progress_timeout`**, **`fast_fail`**) under **conservative or forced degraded-mark relief** when enabled (**`ORB_OPTIONS_SPREAD_DEGRADED_EXIT_ENABLE`**, **`ORB_SPREAD_*`**) so positions are not held indefinitely on **`partial_leg`** / frozen timeout clocks alone.
- Live quote observability now includes:
  - `OPTION_PRICE_RESOLUTION_AUDIT` (throttled)
  - `OPTION_DEGRADED_LIVE_QUOTE` (`reason=no_quote|stale_quote`, `action_taken=hold|exit|skip_entry`)
  - Exit-summary counters and derived availability metrics (`degraded_quote_count`, `degraded_exit_count`, `skipped_entry_due_to_no_quote`, `live_quote_availability_pct`)

### 5) End of Day

- **Flatten:** ORB 0DTE options are closed inside **`PrimeTradingSystem._eod_flatten_orb_0dte()`** → `options_executor.close_all_positions(reason="EOD_CLOSE")`, invoked from **`flatten_all_paths_for_eod_scheduler()`** alongside ORB ETF and Trendline closes.
- **When:** (**a**) Main trading loop during **`SO_ETF_EOD_CLOSE_START_PT`–`SO_ETF_EOD_CLOSE_END_PT`** (`configs/ORBSO.env`, default **12:55**–**12:56** PT), if the loop is active with **`orb_strategy_manager`** + **`stealth_trailing`**. (**b**) **`POST /api/end-of-day-report`** (Cloud Scheduler **`end-of-day-report`**, typically ~**1:05 PM PT** / **4:05 PM ET**) calls the same **`flatten_all_paths_for_eod_scheduler()`** before Telegram EOD; **same-process dedupe** avoids double-close if (**a**) already ran.
- **Reports:** **`send_options_end_of_day_report`** for **Easy ORB 0DTE** runs from **`main.py`** **`handle_end_of_day_report`** only (with ETF + Trendline EOD in one HTTP request). Logs include **`ORB_OPTIONS_EOD`** / **`EOD_ORB_0DTE`** style markers for open/closed counts.

---

## Entry Qualification Pipeline

### Convex Eligibility Filter

- Minimum pass score: **`0DTE_CONVEX_MIN_SCORE`** — repo baseline **0.75** is **profile-bundled** in **`modules/orb0dte_execution_defaults.py`** (merged via **`ORB_0DTE_EXECUTION_PROFILE`** / `ConfigLoader` into **`os.environ`**). Add an explicit **`0DTE_CONVEX_MIN_SCORE=`** line in **`configs/ORB0DTE.env`** only when you intend to override the bundled default (the committed **`ORB0DTE.env`** often has **no** Convex `KEY=` lines; section **3** is commented to that effect).
- Uses weighted components including volatility, ORB range/ATR, momentum/regime checks, and required structural checks (all **`0DTE_CONVEX_*`** weights and credits in **`ORB0DTE.env`**).
- Direction-aware behavior: long-side entries can be blocked on Red Day while bearish PUT opportunities can remain eligible.

### Hard Gate (pre-execution queue)

- Symbol/target allowlist validation
- Session timing checks
- Volume/volume-ratio checks
- Wide ORB range is warning-oriented (not an automatic reject on max ORB range)

### Chain/Strike Selection, Viability, and Liquidity

- Live chain is used to select strikes/legs, with fetch retry at open (configurable attempts/delay).
- Pre-selection viability precheck validates chain quality before execution queue admission.
- Viability score is computed from spread tightness, volume, open interest, ITM depth, and valid spread-pair density.
- Signals below `0DTE_MIN_VIABILITY_THRESHOLD` are rejected early (viability is a feasibility filter, not an alpha blend).
- Selector diagnostics are recorded for failed spread selection with explicit stage tagging.

### Payoff Guardrails

- Configurable quality thresholds prevent poor reward profiles.
- Strictness profile supports controlled tuning (`strict`, `balanced`, `aggressive` style behavior via env).

---

## Strategy Types

**Signal collection (`Prime0DTEStrategyManager._select_strategy_type`)** assigns **`strategy_type`** from momentum / distance / confidence / volume-ratio tiers:

1. **Strong directional** → **`long_call`** / **`long_put`**
2. **Moderate directional** → **`lotto`** (when **`enable_lotto_sleeve`** is on)
3. **Weak signal** → **`momentum_scalper`** (spread-centric label; execution still tries single-leg first — see below)
4. **Else** → **`itm_probability_spread`**

**Execution (`PrimeTradingSystem._execute_0dte_options_trades`)** always attempts **`route=single_leg_primary`** first for every queued signal: **`long_call` / `long_put`** use the **directional** single-leg profile; **`lotto`** uses the **lotto** profile; if that fails, **`momentum_scalper`** may run the **selector fallback ladder** (Tiers A–D) before final reject. Spread-first strategies (**`debit_spread`**, **`itm_probability_spread`**, width loops) run on their own branches when single-leg does not fill.

Current default thresholds are **pinned in `configs/ORB0DTE.env`** (tunable without code changes):

| Tier | Env keys (prefix) | Repo baseline |
|------|---------------------|---------------|
| **Strong** | `0DTE_STRONG_MOMENTUM_MIN`, `0DTE_STRONG_BREAKOUT_DISTANCE_RATIO_MIN`, `0DTE_STRONG_MIN_CONFIDENCE`, `0DTE_STRONG_MIN_VOLUME_RATIO` | 70.0, 0.08, 0.72, 1.05 |
| **Moderate** | `0DTE_MODERATE_*` | 55.0, 0.02, 0.58, 0.90 |
| **Weak / skip** | `0DTE_WEAK_MOMENTUM_MAX`, `0DTE_WEAK_BREAKOUT_DISTANCE_RATIO_MAX`, `0DTE_WEAK_MIN_CONFIDENCE`, `0DTE_WEAK_MIN_ELIGIBILITY` | 50.0, 0.015, 0.55, 0.72 |

Additional directional boosts toward debit-spread selection use **`0DTE_STRATEGY_BOOST_*`** in the same file.

Core selector telemetry:

- `0DTE_SIGNAL_METRICS`
- `0DTE_STRATEGY_SELECTED`
- `0DTE_EXECUTION_START`
- `0DTE_EXECUTION_ROUTE`
- `0DTE_ROUTING_AUDIT_OK` / `0DTE_ROUTING_AUDIT_FAIL`

Single-leg **`select_single_leg_contract`** (`easy0DTE/modules/options_chain_manager.py`) applies **profile bands** then **`single_leg_min_volume` / `single_leg_min_open_interest`** (from env **`0DTE_SINGLE_LEG_MIN_VOLUME`**, **`0DTE_SINGLE_LEG_MIN_OPEN_INTEREST`** — appendix snapshot uses **75** / **200**; class defaults without env differ). High momentum can apply **`0DTE_SINGLE_LEG_MOMENTUM_RELAX_*`** floors and widen directional spread cap.

- **Lotto profile**: delta **0.12–0.18**, premium **$0.15–$0.60**, max spread **≤10%** (before relax), **OI/volume = env single-leg floors**
- **Directional profile**: delta **0.28–0.40**, premium **$0.35–$1.20**, max spread **`0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT`** (default **8%**, relax path up to **`0DTE_SINGLE_LEG_MOMENTUM_MAX_SPREAD_PCT`**), **same env-driven OI/volume floors**

**Note:** `select_lotto_strike(...)` passes **`profile="directional"`** when **`target_delta ≥ 0.24`**. Default **`0DTE_SELECTOR_FAILOVER_TARGET_DELTA=0.28`** ⇒ Tier D failover selection follows **directional** thresholds even though **`execute_by_strategy(..., strategy_type="lotto")`** may label the order path — see **`ORB_0DTE_SELECTOR_FULL_REPLAY`** `extra.tier_d_select_lotto_resolves_to_profile` in logs.

ITM probability spread selection is direction-aware and uses explicit fallback stages when primary selection fails:

1. `ITM_SPREAD_PRIMARY`
2. `ITM_SPREAD_RELAXED`
3. `ATM_SPREAD`
4. `SINGLE_LEG` (if enabled)
5. `FINAL_REJECT`

PUT/CALL ITM classification is symmetric and explicit:
- CALL ITM: `strike < underlying_price`
- PUT ITM: `strike > underlying_price`

---

## Monitoring and Profit Protection

The ORB 0DTE path includes (through the unified options stealth engine):

- Fast premium-aware monitoring
- Breakeven and trailing logic
- Adaptive handling for spread payoff ceilings
- Pre-entry feasibility checks
- Post-entry adverse guard
- profile-resolved profit floor and max-PnL drawdown retention controls
- Monitor quality telemetry (degraded ratio, warning thresholds)

Goal: improve real profit retention while reducing avoidable churn and degraded-data exits.  
For ORB opening-impulse single-leg profiles, MFE-retention thresholds are tuned faster than Trendline slow-trend profiles to preserve temporary premium expansion.

Unified stealth telemetry includes:
- `OPTIONS_STEALTH_PROFILE_RESOLVED`
- `OPTIONS_STEALTH_PREMIUM_STATE`
- `OPTIONS_STEALTH_MAX_PNL_DRAWDOWN_EXIT`
- `OPTIONS_STEALTH_PROFIT_FLOOR_EXIT`
- `OPTIONS_STEALTH_TRAIL_EXIT`
- `OPTIONS_STEALTH_TIME_EXIT`
- `OPTIONS_STEALTH_NO_PROGRESS_EXIT`
- `OPTIONS_STEALTH_MFE_RETENTION`

---

## Alerting and Diagnostics

ORB 0DTE alerts include:

- Unified signal collection context
- Separate options execution summary
- Strategy/contract details, delta, and underlying reference price (`Delta @ $price`)
- Top reject reasons with richer diagnostics

Useful Cloud log families/tokens:

- `0DTE_PIPELINE`
- `0DTE_HARD_GATE_REJECT`
- `0DTE_EXEC_REJECT`
- `CONVEX_REJECT_DETAIL`
- `0DTE_CONVEX_STAGE`
- `SYMBOL_CLASSIFICATION`
- `CHAIN_EXPIRY_SELECTION`
- `OPTIONS_STEALTH` (monitor/exit quality)
- `OPTION_PRICE_RESOLUTION_AUDIT`
- `OPTION_DEGRADED_LIVE_QUOTE`
- `0DTE_EXECUTION_START`
- `0DTE_EXECUTION_ROUTE`
- `0DTE_ROUTING_AUDIT_OK`
- `0DTE_ROUTING_AUDIT_FAIL`
- `0DTE_CHAIN_PRECHECK`
- `0DTE_CHAIN_HEALTH`
- `0DTE_VIABILITY`
- **`0DTE_PREQUEUE_FUNNEL_SUMMARY`**, **`ORB_0DTE_EXECUTION_SKIP_STAGE`** (why a symbol never reached post–hard-gate execution)
- **`ORB_0DTE_LIFECYCLE_AUDIT`**, **`ORB_0DTE_POSITION_LIFECYCLE_SNAPSHOT`** (throttled), **`ORB_0DTE_MONITOR_WATCHDOG_MISSING_TICK`**
- **`OPTIONS_AGGREGATED_EXIT_ALERT_STATUS`**, **`OPTION_EXIT_ALERT_RETRY`** / **`OPTION_EXIT_ALERT_FINAL_FAILURE`**
- **`ORB_0DTE_OVEREXTENSION_SOFT_PENALTY`**, **`ORB_0DTE_OVEREXTENSION_ALLOWED_STRONG_CONTINUATION`**, **`ORB_0DTE_OVEREXTENSION_EXTREME_REJECT`** (legacy logs may still show `0DTE_OVEREXTENSION_REJECT` on older revisions)
- **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_ATTEMPT`**, **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_SELECTED`**, **`ORB_0DTE_CHAIN_HEALTH_FINAL_REJECT`**
- **`ORB_0DTE_EXECUTION_START`** … **`ORB_0DTE_MONITOR_ATTACHED`**, **`ORB_0DTE_FIRST_QUOTE_HYDRATED`**, **`ORB_0DTE_EXECUTION_DURABLE_CONFIRMED`**, **`ORB_0DTE_EXIT_GRADE_DURABLE_CONFIRMED`**, **`ORB_0DTE_DURABILITY_*`**, **`ORB_0DTE_STRATEGY_TYPE_PROPAGATED`**, **`ORB_0DTE_STRATEGY_TYPE_MISSING_FIXED`**
- **`ORB_0DTE_SELECTOR_FULL_REPLAY`** — single JSON **`INFO`** on **`selector_fallback_ladder_exhausted`** and **`spread_selection_failed`** (debit-spread width ladder); grep token also appears on related **`WARN`** / **`0DTE_EXEC_REJECT`** payloads
- **`ORB_OPTIONS_EXIT_DEFERRED_AUDIT`**, **`ORB_OPTIONS_FORCED_DEGRADED_EXIT`**
- **`SO_RANK_BREAKDOWN`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`** (ORB SO ranking — post–May 15 `json` fix)
- **`SO_EXECUTION_FAILED`** / **`SO_EXECUTION_COMPLETED`** (SO batch dedupe only arms on successful `_process_orb_signals`)
- **`EXECUTION_*`** family when smart execution enabled (`EXECUTION_FILL_SUMMARY`, `EXECUTION_LIMIT_ATTEMPT`, `EXECUTION_REPRICE`, `EXECUTION_MARKET_FALLBACK`, `EXECUTION_FORCE_FALLBACK`, `EXECUTION_TIMEOUT_ABORT`, `EXECUTION_AGGRESSION_ESCALATED`, `EXECUTION_FILL_RECONCILED`, `EXECUTION_PARTIAL_FILL`, `EXECUTION_SLIPPAGE_GUARD_REJECT`, `LAST_LOOK_REJECT`; debit opens log **`debit_drift_vs_quoted`** in selector payloads)
- `0DTE_FALLBACK_STAGE`
- `0DTE_EXECUTION_METRICS`
- `0DTE_DIRECTION_SPLIT`
- `0DTE_EXECUTION_SPLIT`

---

## Configuration & merge order

| Layer | File | ORB 0DTE concerns |
|-------|------|-------------------|
| Data / broker | `configs/Data.env` | Watchlists, **`ETRADE_0DTE_ACCOUNT_ID`**, API defaults |
| Cross-path | `configs/Shared.env` | **`MAX_TOTAL_OPTION_POSITIONS`**, **`REQUIRE_LIVE_OPTION_DATA`**, **`ORB_RTH_INTRADAY_SYMBOLS`**, **`ORB_BREAK_TOLERANCE`**, generic **`OPTION_STEALTH_*`**, live-quote policy; E*TRADE symbol aliases (**`CISCO`→`CSCO`**, **`NEBIUS`→`NBIS`**) applied in **`modules/prime_data_manager.py`** at batch-quote time |
| ORB ETF SO | `configs/ORBSO.env` | **`SO_RECHECK_BREAKOUT_MULTIPLIER`** (LONG 7:30 vs ORB high) |
| **ORB 0DTE path** | **`configs/ORB0DTE.env`** | All **`0DTE_*`**, **`ORB_0DTE_*`**, **`ORB_OPTIONS_MONITOR_*`**, path **`OPTION_STEALTH_ORB_*`** / **`OPTION_STEALTH_SPREAD_*`**, **`OPTION_0DTE_*`** |
| Trendline (later merge) | `configs/Trendline0DTE.env` | Does not define ORB 0DTE keys; wins only on duplicate names |
| Risk / alerts | `configs/Risk.env`, `configs/Alerts.env` | **`MAX_OPEN_POSITIONS`**, equity stealth, notifications |

**Standalone reference:** `easy0DTE/configs/0dte.env` mirrors a subset for tooling; **production parity** uses **`configs/ORB0DTE.env`** via `ConfigLoader`.

**Priority flags for current fidelity**

- `REQUIRE_LIVE_OPTION_DATA=true` (`Shared.env` / policy)
- `0DTE_DEMO_SYNTHETIC_CHAIN=false` (`ORB0DTE.env`)
- `OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA=true` (typically `Shared.env` / `Risk.env`)

---

## Key config knobs (by `ORB0DTE.env` section)

Use **`configs/ORB0DTE.env`** as the operator checklist. **`0DTE_CHOP_*`** keys are present for documentation/future use; they are **not** wired as active gates in current Python (see `configs/CONFIG_AUDIT_ORB_0DTE_TRENDLINE.md`). Legacy NEUTRAL fallbacks: if **`0DTE_ORB_TREAT_NEUTRAL_*`** are unset, **`prime_orb_strategy_manager`** may still read **`0DTE_VIX_BULLISH_TREAT_NEUTRAL_AS_GREEN`** / **`0DTE_VIX_BEARISH_TREAT_NEUTRAL_AS_RED`**—prefer the primary keys.

### 1 — Path toggle & scope

| Key | Role |
|-----|------|
| `ENABLE_0DTE_STRATEGY` | Master toggle (`main.py`) |
| **`ORB_0DTE_EXECUTION_PROFILE`** | Named preset (e.g. `balanced_open`) merging Convex / priority / chain-health / overextension defaults from **`modules/orb0dte_execution_defaults.py`** and **`modules/config_profiles.py`**; explicit `KEY=` lines in **`ORB0DTE.env`** override profile fills |
| `0DTE_DEMO_SYNTHETIC_CHAIN` | Synthetic vs broker chain in demo (`OptionsChainManager`) |

### 2 — ORB & session timing

| Key | Role |
|-----|------|
| `0DTE_ORB_TREAT_NEUTRAL_AS_GREEN` / `0DTE_ORB_TREAT_NEUTRAL_AS_RED` | NEUTRAL validation candle mapping (`prime_orb_strategy_manager`) |

### 3 — Eligibility, scoring & filters (Convex + chop doc keys)

| Key family | Role |
|------------|------|
| `0DTE_CONVEX_*` | Convex constructor weights, range/ATR credits, regime, near-miss, **`0DTE_CONVEX_LEVERAGED_SUBSTRINGS`** — often **profile-bundled** unless overridden with explicit lines in **`ORB0DTE.env`** |
| `0DTE_CHOP_*` | Documented chop thresholds (not enforced by reads today) |

### 4 — Position sizing & capital

| Key family | Role |
|------------|------|
| `0DTE_MAX_POSITIONS`, `0DTE_TRADING_CAPITAL_PCT`, `0DTE_MAX_POSITION_SIZE_PCT`, `0DTE_EXECUTOR_MAX_POSITION_SIZE_PCT` | Caps and sizing denominators |
| `0DTE_RANK_MULT_*` | Greedy rank multipliers (`prime_0dte_strategy_manager`) |

### 5 — Instruments, spreads & liquidity

Includes debit spread targets, lotto sleeve, OI/volume/spread, **`0DTE_NEAREST_EXPIRY_MAX_DAYS`**, long-leg **`0DTE_DEBIT_LONG_*`** premium bands and OTM bounds, momentum scoring CSVs, **`0DTE_DELTA_{SPX|QQQ|SPY|OTHER}_{HIGH|MED|LOW}_{DELTA|WIDTH}`**, **`0DTE_STRATEGY_BOOST_*`**, **`0DTE_STRONG_*` / `0DTE_MODERATE_*` / `0DTE_WEAK_*`**, and **May 13+** selector helpers (**`0DTE_DEBIT_SHORT_LEG_STEP_MULT`**, **`0DTE_HARD_GATE_EXEC_SPREAD_RELAX_MULT`**, **`0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS`**, **`0DTE_SINGLE_LEG_MOMENTUM_*`**, **`0DTE_MOMENTUM_SKIP_ATM_SPREAD_TIERS_MIN`** — see **`ORB0DTE.env`**). Primary modules: **`prime_0dte_strategy_manager`**, **`options_chain_manager`**.

### 6 — Execution gating & runtime checks

| Key family | Role |
|------------|------|
| **`USE_MARKET_ORDERS`**, **`ENABLE_SMART_EXECUTION`**, **`EXEC_LAST_LOOK_MAX_SPREAD_PCT`** | Smart limit policy (code defaults in `modules/execution_routing.py`; not required in `ORB0DTE.env` unless overriding) |
| `0DTE_RECHECK_BREAKDOWN_MULTIPLIER` | SHORT vs ORB low at 7:30 (read by execution path; **not** always present as `KEY=` in slim `ORB0DTE.env`—add explicitly if you need a non-default value) |
| `0DTE_EXECUTION_STRICTNESS_PROFILE`, `0DTE_MIN_RISK_REWARD`, `0DTE_MIN_MAX_PROFIT_PER_SPREAD`, `0DTE_MAX_DEBIT_TO_WIDTH_PCT` | Payoff guardrails (strictness / profile may bundle some of these) |
| `0DTE_PREENTRY_*` | Pre-entry feasibility |
| `0DTE_HARD_GATE_*`, `0DTE_SYMBOLS_FALLBACK` | Hard gate window and indices |
| `0DTE_CHAIN_FETCH_*`, `0DTE_CHAIN_HEALTH_*`, `0DTE_MIN_CHAIN_STRIKES`, `0DTE_MAX_AVG_SPREAD_PCT_*` | Chain fetch and health (many tunables are **profile-bundled**; grep merged config / add overrides in **`ORB0DTE.env`** when debugging `spread_too_wide`) |
| `0DTE_MAX_EXECUTION_CANDIDATES`, `0DTE_MIN_VIABILITY_THRESHOLD` (repo default **0.30** in **`orb0dte_execution_defaults.py`**; explicit `KEY=` in **`ORB0DTE.env`** wins), `0DTE_EXTENSION_THRESHOLD_PCT` (legacy alias → **`ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD`**), **`ORB_0DTE_OVEREXTENSION_*`**, **`ORB_0DTE_CHAIN_HEALTH_RELAX_MIN_ELIGIBILITY`**, `0DTE_DIRECTIONAL_SKEW_WARN_THRESHOLD`, `0DTE_OMISSION_LOG_MAX` | Queue, collection diagnostics, overextension scoring, thin-chain relax |
| **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`**, **`0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS`**, **`0DTE_DEBIT_SHORT_LEG_STEP_MULT`**, **`0DTE_HARD_GATE_EXEC_SPREAD_RELAX_MULT`**, **`0DTE_MOMENTUM_SKIP_ATM_SPREAD_TIERS_MIN`**, **`0DTE_DEBIT_SELECTOR_HIGH_MOMENTUM_MIN`**, **`0DTE_SINGLE_LEG_MOMENTUM_*`** | Pre-final-reject chain slice retries; momentum / liquidity selector calibration |
| `ORB_0DTE_CHAIN_LATENCY_*_MS` | Latency backoff tiers |
| **`ORB_0DTE_EXIT_GRADE_MIN_GOOD_TICKS`**, **`ORB_0DTE_DURABILITY_RECONCILE_WAIT_SECONDS`** | Exit-grade durability vs delayed reconcile |
| `ORB_OPTIONS_MONITOR_*`, `ORB_0DTE_POSITION_MONITOR_*`, `ORB_0DTE_DEBUG_VERBOSE` | Position monitor and degraded telemetry |
| **`ORB_OPTIONS_SPREAD_DEGRADED_EXIT_*`**, **`ORB_OPTIONS_SPREAD_CONSERVATIVE_EXIT_*`**, **`ORB_SPREAD_*`** | ORB spread timeout-exit relief under degraded marks |

### 7 — Exits, stealth, routing & monitoring

| Key family | Role |
|------------|------|
| `0DTE_AUTO_PARTIAL_*`, `0DTE_PARTIAL_*`, `0DTE_RUNNER_*` | Partial / runner policy (`OptionsTradingExecutor` init in `main.py`) |
| `0DTE_DEBIT_*`, `0DTE_LOTTO_*`, `0DTE_FIRST_*`, `0DTE_SECOND_*` | Executor stops and scale-out tiers |
| `0DTE_SYMBOL_MAPPING` | Leveraged-name routing |
| `0DTE_PRIORITY_*` | Priority ranking weights and penalties (often profile-bundled; see §3) |
| `OPTION_STEALTH_ORB_*` (**`Shared.env`** — ORB 0DTE degraded running / no-progress / watchdog trusted skip / drawdown tighten), `OPTION_STEALTH_TLINE_*`, `OPTION_STEALTH_SPREAD_*`, `OPTION_STEALTH_*`, `OPTION_0DTE_FAST_*` | Unified 0DTE premium-based stealth profiles (`prime_options_stealth_trailing_tp`); ORB spread relief keys above layer on the same module for **timeout-style** exits when quotes are partial/degraded |
| `0DTE_WATCHLIST_CSV_EXTRA_PATHS`, `0DTE_PRIORITY_COLLECTOR_ENABLED` | Optional universe merge and optimizer snapshots |

**Optional broker overrides** (usually **`Data.env`** / Cloud; commented placeholders at bottom of **`ORB0DTE.env`**): `0DTE_ETRADE_ACCOUNT_ID`, `0DTE_ETRADE_SECRET_NAME`, `0DTE_BROKER_TYPE`.

**Optional MFE-retention profile overrides** (unified stealth):
- `OPTION_STEALTH_ORB_MFE_TRIGGER_PCT`
- `OPTION_STEALTH_ORB_MFE_DRAWDOWN_EXIT_PCT`
- `OPTION_STEALTH_TLINE_IMPULSE_MFE_TRIGGER_PCT`
- `OPTION_STEALTH_TLINE_IMPULSE_MFE_DRAWDOWN_EXIT_PCT`
- `OPTION_STEALTH_TLINE_SLOW_MFE_TRIGGER_PCT`
- `OPTION_STEALTH_TLINE_SLOW_MFE_DRAWDOWN_EXIT_PCT`

---

## Key Config Surfaces (quick reference)

| Surface | Role |
|---------|------|
| **`configs/ORB0DTE.env`** | Canonical path tunables (appendix below) |
| `configs/Shared.env` / `configs/Risk.env` | Combined book cap, live data, generic **`OPTION_STEALTH_*`** |
| `configs/ORBSO.env` | **`SO_RECHECK_BREAKOUT_MULTIPLIER`** (LONG re-check) |
| `easy0DTE/configs/0dte.env` | Standalone / tooling mirror—not merged unless sourced manually |

---

## Position and concurrency limits (integrated app)

Canonical numbers are **split by path**: **`MAX_CONCURRENT_TRADES`** (15) lives in **`configs/ORBSO.env`** (ORB SO ETF 7:30 batch only — **not** the 0DTE cap). **`0DTE_MAX_POSITIONS`** and related execution knobs live in **`configs/ORB0DTE.env`**. **`MAX_TOTAL_OPTION_POSITIONS`** (combined ORB 0DTE + Trendline book) lives in **`configs/Shared.env`**. Trendline rolling cap: **`configs/Trendline0DTE.env`**.

| What | Env key (primary) | Baseline |
|------|-------------------|----------|
| Max simultaneous **ORB SO ETF** executions at **7:30 PT** + greedy SO sizing divisor | `MAX_CONCURRENT_TRADES` | **15** (`prime_risk_manager` / `prime_demo_risk_manager` SO branch) |
| Max concurrent **ORB 0DTE** option positions at the **7:30** batch | `0DTE_MAX_POSITIONS` | **6** |
| Max open **ORB 0DTE** + **Trendline** options combined | `MAX_TOTAL_OPTION_POSITIONS` | **11** (6 + 5) |
| Max simultaneous **Trendline** 0DTE options (rolling; refill when one exits) | `TRENDLINE_MAX_OPEN_POSITIONS` | **5** |
| Risk-manager **portfolio** position count ceiling (tracked strategy positions across paths) | `MAX_OPEN_POSITIONS` | **26** in default `configs/Risk.env` (≥ 15 + 6 + 5 so path-specific gates bite first) |

Standalone **`easy0DTE/configs/0dte.env`** mirrors `0DTE_MAX_POSITIONS` and related 0DTE knobs for the easy0DTE package; the merged trading app loads **`configs/Shared.env`**, path **`ORBSO.env` / `ORB0DTE.env` / `Trendline0DTE.env`**, and the rest of the chain via `ConfigLoader`.

---

## Appendix: `configs/ORB0DTE.env` snapshot

**Purpose:** Single diffable inventory of ORB 0DTE path defaults in git (aligned with **`docs/0DTETrendline.md`** appendix style). **Refresh rule:** When you change **`configs/ORB0DTE.env`**, update this block. **Shared-layer keys** are listed as references in the env file header—do not duplicate them here.

*Snapshot date: May 14, 2026 — partial mirror of `configs/ORB0DTE.env` for diff visibility. **Not exhaustive:** the committed env file also includes **`ORB_0DTE_EXIT_GRADE_MIN_GOOD_TICKS`**, **`ORB_0DTE_DURABILITY_RECONCILE_WAIT_SECONDS`**, overextension **`ORB_0DTE_OVEREXTENSION_*`** (when set), chain fallback **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`** (when set), selector tuning (**`0DTE_MOMENTUM_SKIP_ATM_SPREAD_TIERS_MIN`**, **`0DTE_SELECTOR_FAILOVER_TARGET_DELTA`**, **`0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS`**, …), monitor **`ORB_0DTE_LIFECYCLE_SNAPSHOT_LOG_SEC`**, and the **`ORB_OPTIONS_SPREAD_*` / `ORB_SPREAD_*`** spread relief block; refresh this appendix after substantive env edits.*

```env
# --- 1) Path toggle & scope ---
ENABLE_0DTE_STRATEGY=true
ORB_0DTE_EXECUTION_PROFILE=balanced_open
0DTE_DEMO_SYNTHETIC_CHAIN=false

# --- 2) ORB & session timing ---
0DTE_ORB_TREAT_NEUTRAL_AS_GREEN=true
0DTE_ORB_TREAT_NEUTRAL_AS_RED=true

# --- 3) Eligibility, scoring & filters ---
# Convex / chop / chain-health / priority / partial tiers / overextension / latency:
# defaults live in modules/orb0dte_execution_defaults.py and ORB_0DTE_EXECUTION_PROFILE.
# Add KEY= lines here only for deliberate path overrides (they win over profile defaults).

# --- 4) Position sizing & capital ---
0DTE_MAX_POSITIONS=6
0DTE_TRADING_CAPITAL_PCT=90
0DTE_MAX_POSITION_SIZE_PCT=35
0DTE_EXECUTOR_MAX_POSITION_SIZE_PCT=35
0DTE_RANK_MULT_1=3.0
0DTE_RANK_MULT_2=2.5
0DTE_RANK_MULT_3=2.0
0DTE_RANK_MULT_4_5=1.71
0DTE_RANK_MULT_6_10=1.5
0DTE_RANK_MULT_11_15=1.2
0DTE_RANK_MULT_16_PLUS=1.0

# --- 5) Instruments, spreads & liquidity ---
# (Full delta ladder, single-leg floors, long-leg premium bands — see git ORB0DTE.env)
0DTE_DEBIT_SPREAD_TARGET_DELTA_MIN=0.30
0DTE_DEBIT_SPREAD_TARGET_DELTA_MAX=0.45
0DTE_DEBIT_SPREAD_WIDTH_OPTIONS=1.0,2.0,3.0,5.0
0DTE_OPTIONS_MIN_OPEN_INTEREST=75
0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT=8.0
0DTE_OPTIONS_MIN_VOLUME=30
0DTE_SINGLE_LEG_MIN_OPEN_INTEREST=200
0DTE_SINGLE_LEG_MIN_VOLUME=75
# Momentum / liquidity selector tuning (see git ORB0DTE.env §5 — May 13)
# 0DTE_MOMENTUM_SKIP_ATM_SPREAD_TIERS_MIN=… 0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS=…
ORB_0DTE_CHAIN_HEALTH_RELAX_MIN_ELIGIBILITY=0.76

# -----------------------------------------------------------------------------
# 6) Execution gating & runtime checks
# -----------------------------------------------------------------------------
0DTE_MIN_RISK_REWARD=0.20
0DTE_MIN_MAX_PROFIT_PER_SPREAD=0.15
0DTE_MAX_DEBIT_TO_WIDTH_PCT=0.92
0DTE_PREENTRY_MIN_EXPECTED_MOVE_PCT=0.004
0DTE_PREENTRY_ORB_RANGE_WEIGHT=0.80
0DTE_PREENTRY_FEASIBILITY_MULT=1.50
0DTE_HARD_GATE_ET_START=10:30
0DTE_HARD_GATE_ET_END=10:40
0DTE_HARD_GATE_WIDE_ORB_WARN_PCT=2.0
0DTE_HARD_GATE_MAX_SPREAD_PCT=5.0
0DTE_HARD_GATE_VOLUME_MULTIPLIER=1.0
0DTE_HARD_GATE_BASE_INDICES=SPY,QQQ,IWM,SPX
0DTE_SYMBOLS_FALLBACK=SPX,QQQ,SPY
ORB_0DTE_EXIT_GRADE_MIN_GOOD_TICKS=3
0DTE_DIRECTIONAL_SKEW_WARN_THRESHOLD=0.80
ORB_0DTE_DURABILITY_RECONCILE_WAIT_SECONDS=75
0DTE_OMISSION_LOG_MAX=60
ORB_OPTIONS_MONITOR_HEARTBEAT_SEC=90
ORB_OPTIONS_MONITOR_NO_EXIT_DIAG_LOG_SEC=180
ORB_OPTIONS_MONITOR_STALE_WARN_SEC=300
ORB_OPTIONS_MONITOR_DEGRADED_WARN_RATIO=0.60
ORB_OPTIONS_MONITOR_DEGRADED_WARN_CONSECUTIVE=3
ORB_0DTE_MONITOR_WATCHDOG_SECONDS=20
ORB_0DTE_EXIT_CONFIRMATION_TIMEOUT_SECONDS=15
ORB_0DTE_LIVE_BLOCK_DEGRADED_ENTRY=true
ORB_0DTE_LIVE_MAX_QUOTE_AGE_SECONDS=10
ORB_0DTE_DEGRADED_ENTRY_KILL_SWITCH_ENABLED=true
ORB_0DTE_DEGRADED_ENTRY_KILL_SWITCH_RATIO=0.60
ORB_0DTE_DEGRADED_ENTRY_KILL_SWITCH_CONSECUTIVE=3
ORB_0DTE_POSITION_MONITOR_INTERVAL_SEC=7
ORB_0DTE_POSITION_MONITOR_INTERVAL_SEC_DYNAMIC=true
ORB_0DTE_DEBUG_VERBOSE=false
OPTION_MONITOR_ISOLATE_POSITION_EXCEPTIONS=true
ORB_OPTIONS_SPREAD_DEGRADED_EXIT_ENABLE=true
ORB_OPTIONS_SPREAD_CONSERVATIVE_EXIT_ENABLE=true
ORB_SPREAD_CONSERVATIVE_EXIT_MIN_TRADE_MINUTES=5
ORB_SPREAD_CONSERVATIVE_EXIT_MIN_PARTIAL_MINUTES=4
ORB_SPREAD_CONSERVATIVE_EXIT_MIN_DEFERRALS=15
ORB_SPREAD_CONSERVATIVE_EXIT_MAX_LEG_AGE_SECONDS=240
ORB_SPREAD_FORCED_DEGRADED_EXIT_MIN_TRADE_MINUTES=28
ORB_SPREAD_FORCED_DEGRADED_MIN_PARTIAL_LEG_MINUTES=20
ORB_SPREAD_FORCED_DEGRADED_MIN_DEFERRALS=55
ORB_SPREAD_FORCED_DEGRADED_WATCHDOG_ACCUM_SECONDS=480
ORB_SPREAD_FORCED_DEGRADED_ABS_MAX_MINUTES=90
ORB_SPREAD_FORCED_ALLOW_INCOMPLETE_MTM=true

# -----------------------------------------------------------------------------
# 7) Exits, stealth, routing & monitoring
# -----------------------------------------------------------------------------
0DTE_SYMBOL_MAPPING=TQQQ:QQQ,SQQQ:QQQ,SPYU:SPY,SPXS:SPY
0DTE_WATCHLIST_CSV_EXTRA_PATHS=
```

---

## Related Documentation

- **ORB 0DTE settings review (tables):** [ORB0DTE_Path_Settings_Review.md](ORB0DTE_Path_Settings_Review.md)
- **ORB 0DTE env / stage map:** [ORB0DTE_Config_Reference.md](ORB0DTE_Config_Reference.md)
- ORB system overview: `README.md`
- Main strategy flow: `docs/Strategy.md`
- Risk controls: `docs/Risk.md`
- Data architecture: `docs/Data.md`
- Trendline sibling path: `docs/0DTETrendline.md` (includes May 15 impulse calibration)
- May 15 session (all paths): `docs/doc_elements/Sessions/2026/May15 Session/SESSION_SUMMARY_MAY15_2026.md`
- ORB 0DTE deep-dive set:
  - `easy0DTE/docs/README.md`
  - `easy0DTE/docs/Strategy.md`
  - `easy0DTE/docs/Data.md`
  - `easy0DTE/docs/Alerts.md`

---

This file is the primary ORB 0DTE reference in `docs/`. The `easy0DTE/docs` files remain the detailed subsystem references.
