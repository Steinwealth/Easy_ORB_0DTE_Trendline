# Easy Trendline 0DTE

Last Updated: **May 15, 2026** — **May 15 (impulse calibration pass, local `BUILD_ID` `00349-20260515-may15-calibration-so-json-symbols`; production still **`easy-etrade-strategy-00330-zdt` until deploy):** Shift impulse validation from **breakout appearance** toward **post-break continuation acceptance** (desk calibration: **MELI** false-positive / **NBIS** false-negative). **R1** — dual body metrics: **`body_ratio_vs_prev`** on break metadata (`break_detector.py`) for qualification; **`body_ratio_vs_avg`** (`_break_bar_body_ratio`) advisory/catastrophic only. **R2** — **`_post_break_acceptance_met()`** required before impulse-fast emit (`immediate_break` / `first_move` / impulse paths). **R3** — expansion defer no longer silent-pass: **`survival_defer_low_expansion`** / **`survival_defer_requires_acceptance`**. **R4** — **`_momentum_persistence_agrees()`** (2-of-3 post-break closes in option direction; slow-trend bypass) replaces single-bar momentum on fast path. **R5** — line separation wired into acceptance (`_line_separation_persistent`, `_count_beyond_line_closes`). **R6** — telemetry: **`TRENDLINE_POST_BREAK_ACCEPTANCE`**, false-break logs use **`selector_structure_ready`** (not misleading **`structure_accepted`** = selector init only). No new config knobs. Session: `docs/doc_elements/Sessions/2026/May15 Session/SESSION_SUMMARY_MAY15_2026.md`.

**May 14 (geometry + observability):** When the **last pre-7:30 close** is **strictly below ORB low** or **strictly above ORB high**, **`select_pre730_structure_setup`** (`easyTrendline/trendline_setup_selector.py`) builds **`TrendlineBuilder.build_cutoff_to_farthest_extreme`** (farthest session extreme ↔ cutoff close; metadata **`construction_rule=cutoff_to_farthest_extreme`**) and **does not** run the dual-line MSE contest for that symbol. **`TRENDLINE_DRAW_AUDIT`** logs a single-line audit at draw time (grep token). Module docstring documents **lane separation:** ORB SO / ORB 0DTE target **continuation quality** into the execution window; Trendline targets **exhaustion / compression into reversal** — avoid copying SO rank weights into Trendline tuning.

**May 13, 2026 — May 13 Trendline stealth calibration (DAL / fast 0DTE):** `OPTION_STEALTH_TLINE_*` refresh in **`configs/Shared.env`**; Trendline-only paths in **`prime_options_stealth_trailing_tp.py`** for **`tline_trusted_degraded_mark`** (HWM + no-progress exit-grade relax under caps), **`min_hold_blocks_tline`**, underlying excursion forensics (**`had_underlying_opportunity`**, **`max_underlying_favorable_move_pct`** and exit snapshots), **`OPTION_HWM_UPDATE_ACCEPTED`**, and **`TRENDLINE_TRAILING_DEGRADED_ADJUSTMENT`** INFO telemetry. **May 11, 2026 — Config surface (Rev):** `load_trendline_config_from_env` (`easyTrendline/trendline_config_loader.py`) maps a **large explicit** set of `TRENDLINE_*` keys into `TrendlineConfig` (see `_TRENDLINE_MAIN_ENV_KEYS`). `TRENDLINE_OPTION_*` and `OPTION_STEALTH_TLINE_*` surfaces are listed in the same module (`ALL_TRENDLINE_CONSUMED_ENV_KEYS`). Any `TRENDLINE_*` / `OPTION_STEALTH_TLINE_*` present in merged `os.environ` but **not** in that set logs **`TRENDLINE_CONFIG_UNUSED_WARNING`** at startup. Repo defaults for the Trendline path live primarily in **`configs/Trendline0DTE.env`** (merge order: `configs/README.md`). Deprecated keys still set in env log **`TRENDLINE_CONFIG_DEPRECATED | key=...`** once at startup. New startup telemetry **`TRENDLINE_CONFIG_SURFACE_AUDIT`** groups loaded surfaces into `structure`, `break_quality`, `continuation`, `chop`, `execution`, and `deprecated_advisory_only` for operator verification. May 7 anti-overfit pass converted several prior micro-confirmation hard vetoes into score/advisory signals while preserving hard vetoes for catastrophic/structural/chop/stale/liquidity risks. Added telemetry: **`TRENDLINE_SCORE_SUMMARY`**, **`TRENDLINE_HARD_VETO_AUDIT`**, and **`TRENDLINE_EXECUTOR_DIRECTION_ADVISORY`**. **May 7 (pass 2):** explicit **break archetype** routing — `impulse_exhaustion`, `delayed_continuation`, `weak_break_failure`, `catastrophic_micro_break`, `chop_fakeout` — with a **single canonical break-distance floor** (`TRENDLINE_BREAK_DISTANCE_MIN` × archetype multiplier) replacing several layered checks. Added **`TRENDLINE_BREAK_ARCHETYPE`**, **`TRENDLINE_DELAYED_CONTINUATION_ARMED|CHECK|ENTRY|EXPIRED`**, **`TRENDLINE_REANCHOR_FREEZE`**, **`TRENDLINE_STRUCTURE_FRAGILITY_ACTIVE`**, **`TRENDLINE_REANCHOR_REJECT_BREAK_ERASURE`** (plus existing `TRENDLINE_REANCHOR_ACCEPTED`). Stealth profiles are now keyed off archetype: `impulse_exhaustion` tightens the impulse profile; `weak_break_failure` routes to the `trendline_weak_break_failure` exit branch and caps the continuation window at **4 bars**. Added **MFE-retention** layer in unified stealth engine with profile-specific drawdown thresholds; new env overrides `OPTION_STEALTH_ORB_MFE_TRIGGER_PCT` / `OPTION_STEALTH_ORB_MFE_DRAWDOWN_EXIT_PCT`, `OPTION_STEALTH_TLINE_IMPULSE_MFE_TRIGGER_PCT` / `OPTION_STEALTH_TLINE_IMPULSE_MFE_DRAWDOWN_EXIT_PCT`, `OPTION_STEALTH_TLINE_RETEST_MFE_TRIGGER_PCT` / `OPTION_STEALTH_TLINE_RETEST_MFE_DRAWDOWN_EXIT_PCT`, `OPTION_STEALTH_TLINE_SLOW_MFE_TRIGGER_PCT` / `OPTION_STEALTH_TLINE_SLOW_MFE_DRAWDOWN_EXIT_PCT`. Added telemetry: **`OPTIONS_STEALTH_MFE_RETENTION`**, **`OPTIONS_STEALTH_PROFILE_SOURCE_AUDIT`**, **`OPTIONS_STEALTH_PROFILE_RESOLVED`**, **`OPTIONS_STEALTH_PREMIUM_STATE`**, **`OPTIONS_STEALTH_MAX_PNL_DRAWDOWN_EXIT`**, **`OPTIONS_STEALTH_PROFIT_FLOOR_EXIT`**. **May 11, 2026:** Canonical break archetypes on stored breaks / metadata are **`IMPULSE_BREAK`**, **`CONTINUATION_DRIFT`**, **`EXHAUSTION_REVERSAL`**; legacy strings (`impulse_exhaustion`, `weak_break_failure`, etc.) remain accepted in loaders and stealth exit mapping. **Composite entry-quality score** path: `TRENDLINE_ENTRY_SCORE_*`, `TRENDLINE_SCORE_WEIGHT_*`, `TRENDLINE_SCORE_PENALTY_*`, bounce-back `TRENDLINE_BOUNCEBACK_*`, archetype-scaled survival `TRENDLINE_ENTRY_SURVIVAL_*` — defaults centralized in **`modules/trendline_entry_defaults.py`** (merged with env / profiles). **`READY_TO_EXECUTE`** remains in the watch candidate set so deferred setups still receive **`process_new_bar`** rescoring. Startup audits: **`TRENDLINE_ENTRY_PROFILE_AUDIT`**, **`TRENDLINE_SCORE_DEFAULTS_AUDIT`**, **`TRENDLINE_CONFIG_OWNERSHIP_AUDIT`**. Runtime score/bounce grep: **`TRENDLINE_ENTRY_SCORE_SNAPSHOT`**, **`TRENDLINE_ENTRY_SCORE_PASS`**, **`TRENDLINE_ENTRY_SCORE_DEFER`**, **`TRENDLINE_ENTRY_SCORE_REJECT`**, **`TRENDLINE_BOUNCEBACK_BLOCK`**, **`TRENDLINE_BOUNCEBACK_DEFER`**, **`TRENDLINE_BOUNCEBACK_CLEARED`**.
**May 4 (EOD docs):** Trendline closes are part of **`flatten_all_paths_for_eod_scheduler()`** with ORB ETF and ORB 0DTE; timing uses **`SO_ETF_EOD_CLOSE_*`** (`configs/ORBSO.env`) and **`POST /api/end-of-day-report`** same-process dedupe — see EOD subsection below and [Alerts.md](Alerts.md).
Earlier April 17: ORB **extreme-bar timestamps** for anchor-one time (`orb_high_extreme_ts` / `orb_low_extreme_ts` on `ORBData`, wired into Trendline `orb_context`); **`TRENDLINE_PIPELINE | stage=line_built`**; post-break structure accepts **tie** on last bar vs prior-window extreme. **May 4 (doc refresh):** **Key Config Knobs** summarizes behavior; the **authoritative** env key list is `ALL_TRENDLINE_CONSUMED_ENV_KEYS` / `_TRENDLINE_MAIN_ENV_KEYS` in `trendline_config_loader.py` (many momentum, hold, chop, regime, and strict-break keys are env-backed—do not assume “code defaults only” without checking the loader).  
Earlier: April 16, 2026 — full **0DTE universe** size follows `data/watchlist/0dte_list.csv` (dynamic as list updates when `TRENDLINE_USE_FULL_0DTE_LIST=true`). April 10, 2026: 7:30 build **`bars=1` ORB-timed intraday + quote merge**; chunked paired fetches + request budgets + `request_summary` / `build_degraded` / `build_summary`; `build_bar_diagnostics`. **normalized_options** primary stealth registration.  
Path: `easyTrendline` (+ `modules/prime_options_stealth_trailing_tp.py` for exits)  
Status: Integrated as third sibling strategy path

## Overview

Easy Trendline 0DTE is the third strategy path in the Easy ORB application:

1. ORB ETF path (existing)
2. ORB 0DTE options path (existing)
3. Easy Trendline 0DTE path (structure-first, event-driven 0DTE long premium)

This path is additive and does not replace the existing 7:30 SO / ORB 0DTE batch execution logic.

**At 7:30 AM PT** the system evaluates **pre-7:30 price structure** (6:30–7:30 PT when bars exist) for each symbol in the **full 0DTE universe** and selects **at most one** valid setup per symbol: **ascending support** (ORB low + higher lows → long put on breakdown) or **descending resistance** (ORB high + lower highs → long call on breakout). Symbols with no valid structure are skipped.

**Outside ORB at cutoff:** if the **last bar close at or before the 7:30 cutoff** is **strictly below `orb_low`** or **strictly above `orb_high`**, the primary line is **`cutoff_to_farthest_extreme`** (farthest eligible session extreme ↔ cutoff close; **slope** sets **bull** vs **bear** / support vs resistance — see **`TrendlineBuilder.build_cutoff_to_farthest_extreme`**). That path **skips** the dual-line MSE comparison for the symbol.

**Inside ORB at cutoff:** **direction (which geometry)** is chosen primarily by **`classify_orb_test_failure`** on the **post-ORB through 7:30** window (failed downside → bear/call; failed upside → bull/put; trend continuation → **no** setup that day for that symbol). When classification does **not** pin a side (**unclear**, **compression**, missing ORB bounds, etc.), the selector falls back to **MSE** among built lines; when **both** geometries fit with **nearly equal** MSE in that fallback path, selection uses a deterministic **anchor price-span** tie-break and logs `setup_tie_break` (the symbol is not dropped for that reason alone).

**After 7:30 AM PT** the watch loop waits for:

- valid close-based break beyond the line (threshold + optional ATR from **`TrendlineConfig`**, populated from **`TRENDLINE_MIN_BREAK_PCT`**, **`TRENDLINE_USE_ATR_BREAK`**, **`TRENDLINE_ATR_BREAK_MULTIPLIER`** when present in merged env)
- **hold** on the correct side of the line (time-based or true-bar-based), then **normal-path OR confirmation** among expansion / local continuation / post-break structure when time-hold fails (logs **`TRENDLINE_NORMAL_GATE_OR`**)
- post-break structure (lower low for puts / higher high for calls), unless expansion **waives** structure when applicable (**`expansion_waives_structure_gate`** in engine logs)
- momentum + break-quality gate
- anti-chase distance check
- **delta / OTM-aware** 0DTE contract selection (demo-mode simulated fills using live broker option-chain/quote data)
- execution into the **Trendline demo ledger**, then **premium-based stealth trailing** exits (not ETF-style fixed TP/SL ladders)

## Design Intent

- **Structure-first:** Direction and option side come from **ORB-window structure** — **outside ORB at cutoff** uses **`cutoff_to_farthest_extreme`**; **inside ORB** uses **`classify_orb_test_failure`** plus valid trendline builds on pre-7:30 bars—not from ORB signal collection side, fixed long bias, or retired **price-at-7:30 vs ORB** proximity heuristics.
- **Precise entries:** Hold semantics are explicit (**time vs bars**); structure and momentum checks are direction-correct for each setup type.
- **Isolated accounting:** Dedicated `TrendlineAccountManager` (demo-first).
- **Options-native exits:** `prime_options_stealth_trailing_tp.py` is the **single normal premium-based exit brain** for both ORB 0DTE and Trendline 0DTE option positions (profile-resolved by source path, position type, and entry archetype). It handles delta-aware breakeven/profit floor, max-PnL drawdown retention exits, trailing, time/no-progress, and structure exits when enabled. ORB ETF `prime_stealth_trailing_tp.py` remains separate/unchanged.
- **Rich telemetry** for comparison vs ORB 0DTE and for tuning.
- **Defensive** when data or chain hints are missing.

## Module Set (`easyTrendline`)

- `trendline_models.py`  
  Dataclasses and enums: `TrendlineConfig`, `TrendlineOptionSelectionConfig`, `TrendlineSetupType`, lifecycle, break/momentum, signals/results. **`TrendlineDirection` = line geometry** (`bull` = ascending support, `bear` = descending resistance)—not ORB “side.”

- `trendline_setup_selector.py`  
  **Structure-first** 7:30 selection: builds **both** geometries and scores fit (MSE vs line) when **not** using the cutoff-extreme shortcut. **Primary (inside ORB at cutoff):** `classify_orb_test_failure(symbol, pre_cutoff_bars, orb_context)` chooses bear vs bull when **`failed_downside` / `failed_upside`** apply; **`trend_continuation`** returns no setup; missing required line logs **`TRENDLINE_SETUP_SKIP`** (`missing_descending_line` / `missing_ascending_line`). **Outside ORB at cutoff:** **`build_cutoff_to_farthest_extreme`** returns a single line; **`selection_reason`** includes `cutoff_to_farthest_extreme|below_orb` / `above_orb`. **Secondary:** when classification does not pin direction (inside-ORB path), falls back to better MSE or—on a **relative MSE tie**—**anchor price span** (deterministic). Emits `setup_detected`, **`TRENDLINE_SELECTOR_STRUCTURE_OVERRIDE`** (structure-chosen path), `setup_tie_break`, **`TRENDLINE_TREND_CONTINUATION_SKIP`** (`phase=setup_selector`), **`TRENDLINE_DRAW_AUDIT`**, and `setup_skipped` logs.

- `trendline_config_loader.py`  
  Builds **`TrendlineConfig`** from the keys enumerated in **`_TRENDLINE_MAIN_ENV_KEYS`**; **`TrendlineOptionSelectionConfig`** from **`_TRENDLINE_OPTION_ENV_KEYS`** (delta band, spread cap, strike mode, lotto, OI/volume floors). Warns once per deprecated key still present in **`os.environ`** (**`TRENDLINE_CONFIG_DEPRECATED`**). Calls **`warn_unused_trendline_related_env_keys()`** so stray `TRENDLINE_*` / `OPTION_STEALTH_TLINE_*` names log **`TRENDLINE_CONFIG_UNUSED_WARNING`** (see **Key Config Knobs** for how this relates to **`configs/Trendline0DTE.env`**).

- `trendline_engine_internals.py`  
  **`DEFAULT_TRENDLINE_ENGINE_INTERNALS`** — fixed signal-engine numerics (rearm, regime, impulse, drift, fast-path floors, continuation windows, pressure/scoring, survival bars, etc.). **Not** env-backed; edit in code to tune.

- `trendline_builder.py`  
  Builds one line type from ORB anchors + pre-7:30 bars (used by the selector and for any legacy prebuilt path). Exposes **`classify_orb_test_failure`** used by the selector to classify post-ORB→7:30 structure (**setup_side**, **failure_type**) before MSE tie-breaking on the **inside-ORB** path. **`build_cutoff_to_farthest_extreme`** builds the **outside-ORB-at-cutoff** exhaustion line (session extreme ↔ cutoff close; **`construction_rule=cutoff_to_farthest_extreme`** in metadata). **Anchor-one price** is always **`orb_high`** (bear) or **`orb_low`** (bull) from morning ORB capture. **Anchor-one time** uses **`orb_high_ts` / `orb_low_ts`** from `orb_context`: these prefer the **earliest bar in the 6:30–6:45 PT ORB window** whose high/low equals that ORB extreme (so the line’s first point aligns with the bar that printed the range high/low, e.g. a 6:35 wick), and fall back to ORB `capture_time` only if extremes are unavailable (e.g. legacy snapshot). When orb price or orb extreme timestamp is missing, the builder uses bar/session fallbacks and logs **`TRENDLINE_FALLBACK_USED`** (explicit; not silent). Trendline **`metadata`** includes **`line_quality`** (`good` / `ok` / `poor`) derived from **`anchor_spacing_min`** for snapshot analytics. Anchor two remains the selector-chosen second pivot before 7:30 with minimum spacing. On success, logs **`TRENDLINE_PIPELINE | stage=line_built`** (anchors, slope, spacing, prebuild bar count) for Cloud verification.

- `break_detector.py`  
  Close-based breaks: **bull line** → break = close **below**; **bear line** → break = close **above** (threshold + optional ATR from **`TrendlineConfig`**). `TRENDLINE_BREAK_DISTANCE_MIN` remains a quality/scoring input and no longer downgrades valid close-cross breaks by itself. Hard reject in this module is narrowed to **`catastrophic_micro_break`** only (tiny break + weak body + non-increasing distance + prior gap vs threshold — see code). **May 15:** every break metadata payload persists **`body_ratio_vs_prev`** (`body / previous bar body`) alongside legacy **`body_ratio`** for downstream unification. **`break_archetype`** is **not** assigned here; archetypes are inferred and persisted on the break event in **`trendline_signal_engine.py`** (`_apply_break_archetype_on_break`), which also emits **`TRENDLINE_BREAK_ARCHETYPE`**.

- `momentum_confirm.py`  
  Post-break momentum: velocity / range expansion / follow-through; direction-aware relative to line geometry. Clean/strong breakouts can bypass strict momentum when **`TrendlineConfig.clean_breakout_bypass_momentum`** is true. **No** volume-confirmation branch.

- `trendline_signal_engine.py`  
  State machine: build → break → **hold** (time or bar mode, with **pending** vs invalidate) → normal-path **OR** gates (expansion / local continuation / structure/acceptance) → momentum → anti-chase → signal. Uses **`TrendlineConfig`** + **`DEFAULT_TRENDLINE_ENGINE_INTERNALS`** only (no signal-engine `os.getenv`). **`TRENDLINE_FAST_PATH_ENABLED`:** fast path can skip deeper gates per engine rules. **May 15 impulse calibration:** impulse-fast emit paths (`_is_impulse_fast_emit_entry`) require **`_post_break_acceptance_met()`** (structural OR slow-trend OR continuation distance without reclaim OR beyond-line closes + line separation); expansion MFE defer returns **`survival_defer_low_expansion`** and only defers when acceptance passes (**`survival_defer_requires_acceptance`** otherwise). Fast-path body qualification uses **`_break_body_ratio_vs_prev()`**; avg-based **`_break_bar_body_ratio()`** is advisory/catastrophic only (fixes split-brain where vs-prev ~1.55 and vs-avg ~0.028 disagreed on the same bar). Fast-path momentum uses **`_momentum_persistence_agrees()`** (2-of-3 closes in expected option direction). Logs include **`TRENDLINE_CONFIG_SUMMARY`**, **`TRENDLINE_NORMAL_GATE_OR`**, **`TRENDLINE_BREAK_QUALITY`**, **`TRENDLINE_BREAK_QUALITY_METRICS`**, **`TRENDLINE_DECISION_SNAPSHOT`**, **`TRENDLINE_SCORE_SUMMARY`**, **`TRENDLINE_POST_BREAK_ACCEPTANCE`**, and hard-stop telemetry **`TRENDLINE_HARD_VETO_AUDIT`**. Single-metric break/body/distance/expected-move checks are now primarily advisory unless catastrophic criteria are met. **Break archetype routing (May 7 pass 2; May 11 canonical labels):** archetype is inferred at break time and stored on `candidate.break_event.metadata["break_archetype"]`; effective minimum break distance = `TRENDLINE_BREAK_DISTANCE_MIN` × archetype multiplier (eased for **`EXHAUSTION_REVERSAL`** and legacy **`impulse_exhaustion`**). Module-level **`classify_break_archetype(...)`** at the bottom of this file is a thin wrapper for tests/tools (`TrendlineSignalEngine._infer_break_archetype`). On marginal reversal/quality failure the engine arms a **delayed continuation** (TTL + bars seen + break-candle LOD/HOD + reference expected-move snapshot stored on `TrendlineCandidate`) instead of terminal reject; entry runs through `_maybe_arm_delayed_continuation()` / `TRENDLINE_DELAYED_CONTINUATION_ARMED|CHECK|ENTRY|EXPIRED`. **Re-anchor hardening:** when prior break context is still meaningful, re-anchor geometry that would erase the near-break is rejected via `TRENDLINE_REANCHOR_REJECT_BREAK_ERASURE`; a freeze is logged via `TRENDLINE_REANCHOR_FREEZE` + `TRENDLINE_STRUCTURE_FRAGILITY_ACTIVE`. **Continuation discipline:** `_activate_continuation_candidate()` caps the continuation window at **4 bars** when archetype is **`CONTINUATION_DRIFT`** or legacy **`weak_break_failure`** (short fakeout window).

- `trendline_options_executor.py`  
  Contract **selection** (delta band, OTM 1–2, lotto bias, spread/OI/volume rejects, slot budget), demo fills, dedupe, payload fields `strike`, `delta_at_entry`, `expiry_ymd` (US/Eastern calendar day for 0DTE chain lookup), `trendline_for_exit`.

- `trendline_account_manager.py`  
  Trendline-only ledger positions and PnL.

- `trendline_reporter.py`  
  EOD statistics and summary object generation.

- `trendline_feature_logger.py`  
  Append-only JSONL (`data/trendline_optimizer/trendline_features.jsonl`).

- `__init__.py`  
  Package exports (includes config loaders and setup selector).

**App module (unified ORB + Trendline options exits):**

- `modules/prime_options_stealth_trailing_tp.py`  
  Unified 0DTE options stealth engine (`TrendlineOptionsStealthEngine` class name retained for compatibility): resolves effective premium, tracks HWM/LWM and max-PnL%, resolves an `OptionExitProfile`, and applies a single decision model for ORB + Trendline: data-outage safety, max-PnL drawdown retention exits, profit-floor/breakeven, trailing, no-progress, time, and optional structure invalidation. Profile-aware overlays still allow Trendline-specific behavior (`OPTION_STEALTH_TLINE_*`) without a second exit system. **Archetype-keyed profile (May 7 pass 2; May 11 naming):** `OptionStealthState.break_archetype` is set via `register_on_open(..., break_archetype=...)`; **`EXHAUSTION_REVERSAL`** / legacy **`impulse_exhaustion`** tightens the impulse profile (faster trail/no-progress); **`CONTINUATION_DRIFT`** / legacy **`weak_break_failure`** routes to the **`trendline_weak_break_failure`** exit branch (faster scratch bias). **ORB 0DTE spreads (May 11):** timeout-style exits may use **degraded-mark relief** (`ORB_OPTIONS_SPREAD_*`, `ORB_SPREAD_*`) without duplicating a second exit engine. **MFE-retention layer:** profile-specific drawdown thresholds (e.g. ORB `opening_impulse` trigger 0.08 / drawdown exit 0.055; Trendline `impulse` 0.10 / 0.07; Trendline `slow_trend` 0.15 / 0.10) protect early premium gains; live/non-synthetic quote context required. Env overrides: `OPTION_STEALTH_ORB_MFE_TRIGGER_PCT`, `OPTION_STEALTH_ORB_MFE_DRAWDOWN_EXIT_PCT`, `OPTION_STEALTH_TLINE_IMPULSE_MFE_TRIGGER_PCT`, `OPTION_STEALTH_TLINE_IMPULSE_MFE_DRAWDOWN_EXIT_PCT`, `OPTION_STEALTH_TLINE_RETEST_MFE_TRIGGER_PCT`, `OPTION_STEALTH_TLINE_RETEST_MFE_DRAWDOWN_EXIT_PCT`, `OPTION_STEALTH_TLINE_SLOW_MFE_TRIGGER_PCT`, `OPTION_STEALTH_TLINE_SLOW_MFE_DRAWDOWN_EXIT_PCT`. Close diagnostics emitted as `OPTIONS_STEALTH_MFE_RETENTION` (`max_pnl_pct`, `exit_pnl_pct`, `profit_leak_pct`, `retained_mfe_ratio`, `exit_reason`, `premium_source_at_exit`).

## Integration Points in Main App

### Startup Initialization

When `ENABLE_TRENDLINE_STRATEGY=true`, app startup initializes:

- `TrendlineSignalEngine` with `TrendlineConfig` from **`load_trendline_config_from_env(get_config_value)`**
- `TrendlineOptionsExecutor` with **`load_trendline_option_selection_config(get_config_value)`**
- `TrendlineAccountManager`
- `TrendlineReporter`
- `TrendlineFeatureLogger`
- **`TrendlineOptionsStealthEngine`** (`load_option_stealth_config(get_config_value)`) on `PrimeTradingSystem` as `trendline_options_stealth`
- **`_trendline_options_quote_api`** (optional): in **demo and live** modes, `main.py` reuses the 0DTE `ETradeOptionsAPI` when present, or constructs one for Trendline-only deploys (requires `easy0DTE` on `sys.path`). Used to fetch **per-contract** quotes during monitor/EOD when strike + `expiry_ymd` are known. If API data is missing, fallback modeling may be computed for diagnostics, but with `OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA=true` degraded/non-live premium sources are not used for exit decisions.

Logged under `TRENDLINE_PIPELINE | stage=init` and `TRENDLINE_PIPELINE | stage=config_loaded`. **`TRENDLINE_CONFIG_SUMMARY`** at engine startup mirrors resolved thresholds. Hold mode, **`min_break_pct`**, ATR break, chop windows, rearm, regime, impulse/retest/slow-trend, and many quality gates are **env-loaded** when keys are present in merged config (`trendline_config_loader.py`). Anything still falling back to **`TrendlineConfig`** dataclass defaults is logged as **`TRENDLINE_CONFIG_DEFAULT_USED`** for that key.

**Config source:** Path toggles and schedules live in **`configs/ORBSO.env`**, **`configs/ORB0DTE.env`**, and **`configs/Trendline0DTE.env`**. **Canonical Trendline signal keys**, **Trendline stealth overlays** (`OPTION_STEALTH_TLINE_*`), caps, build/monitor budgets, and demo ledger defaults live in **`configs/Trendline0DTE.env`**. **`configs/Shared.env`** holds cross-path items such as **`MAX_TOTAL_OPTION_POSITIONS`** and generic ORB hygiene (`ORB_BREAK_TOLERANCE`, `ORB_RTH_INTRADAY_SYMBOLS`). Generic options discipline (`OPTION_STEALTH_*` shared across paths) lives in **`configs/Shared.env`** / **`configs/Risk.env`**. After `load_app_config()`, `main.py` exports the merged dict into **`os.environ`**. See **`configs/README.md`** for full load order. Treat **Key Config Knobs** in this doc as authoritative for what `TrendlineConfig` reads.

### 7:30 Build Step (No Immediate Entry)

During the existing 7:30 pipeline block (after signal collection finalization), symbols with valid ORB context are processed in **stable watchlist order**. **Broker-only constraint:** `PrimeDataManager.get_batch_intraday_data` with **`bars > 1`** returns **one** synthetic bar per symbol (today’s OHLC), not a true multi-bar series—so the Trendline build must **not** rely on large `bars=` requests for history.

**Current data path (Rev 00325):**

1. **Chunked broker calls:** Symbols are split into chunks (default **25**, `TRENDLINE_DATA_CHUNK_SIZE`). For each chunk, the app calls **`get_batch_intraday_data(..., interval="15m", bars=1)`** once and **`get_batch_quotes(..., skip_cache=True)`** once—paired so there are **no duplicate** intraday/quote requests per symbol in the same build. **`TRENDLINE_MAX_INTRADAY_BATCH_CALLS_PER_BUILD`** and **`TRENDLINE_MAX_QUOTE_BATCH_CALLS_PER_BUILD`** cap how many chunk pairs run; **`TRENDLINE_MAX_SYMBOLS_PER_BUILD`** caps the post-ORB universe before chunking. If the full universe would exceed the budget and **`TRENDLINE_ENABLE_BUILD_DEGRADATION=false`**, the **entire** Trendline build is skipped (`budget_strict_abort`). If degradation is **true**, later symbols are **deferred** and counted as **`request_budget_exhausted`** in summary counts. Optional **`TRENDLINE_MAX_BUILD_DURATION_MS`** stops fetching mid-pass (`time_budget`).
2. **Merge:** Parsed ORB-timed bar(s) are merged with prefetch / **7:00 open** map / **quote** in **`_trendline_merge_structure_bars`** (dedupe by PT minute; quote-derived **7:00 / 7:30 PT** anchors when needed) so the selector sees **≥ 2** distinct timestamps when data allows.
3. **`select_pre730_structure_setup`** runs on **attempted** symbols only (full 0DTE list when `TRENDLINE_USE_FULL_0DTE_LIST=true`; otherwise rule-confirmed symbols only).
4. Per symbol: either **one** candidate is created with **pre-attached** `TrendlineDefinition` + rich `setup_payload`, or the symbol is skipped. Selector may log `setup_skipped` with `detail=insufficient_pre730_bars` when structure is invalid; the build path also records distinct merge/data reasons (`raw_fetch_no_bars`, `quote_unavailable`, `fallback_insufficient_bars`, `fallback_failed`, `merged_ok`, etc.) in **`build_bar_diagnostics`** and **`reason_*=`** on **`build_summary`**.

**Setup semantics (metadata):**

| `setup_type` | Line geometry (`TrendlineDirection`) | Trigger | Option |
|--------------|----------------------------------------|---------|--------|
| `ascending_support` | `bull` | `breakdown_down` | long **put** |
| `descending_resistance` | `bear` | `breakout_up` | long **call** |

No options are executed at this step—only candidates enter `WAITING_FOR_BREAK` after **`trendline_selector_built`** initialization (selector-built line attached in `setup_payload`).

### Rolling Max-Open Execution Model

Trendline uses first-come execution (not pre-ranking reservation) with a rolling open-position cap:

- monitor all built candidates
- execute confirmations while `open_positions < TRENDLINE_MAX_OPEN_POSITIONS` (default 5)
- if one position exits, capacity is freed and the next ready confirmation can execute
- no new entries after `TRENDLINE_NO_NEW_ENTRIES_AFTER_PT` (default `11:30` PT), while monitoring/exits continue

### Slot-Based Capital Sizing

Trendline uses preallocated slot sizing (separate from ORB ETF and ORB 0DTE greedy execution):

- `usable_capital = trendline_account_balance * (TRENDLINE_ACCOUNT_ALLOCATION_PCT / 100)` (allocation is a percent; see `PrimeTradingSystem` slot refresh)
- `slot_capital = usable_capital / TRENDLINE_SLOT_COUNT` → **one fifth of 90%** of the Trendline ledger when `TRENDLINE_SLOT_COUNT=5` and `TRENDLINE_ACCOUNT_ALLOCATION_PCT=90`
- each execution uses one slot budget (subject to safety max position constraints)
- if slot capital cannot support minimum valid quantity, trade is skipped with sizing reason

### Combined options book cap (ORB 0DTE + Trendline)

New Trendline **and** ORB 0DTE option opens both consult `MAX_TOTAL_OPTION_POSITIONS` in **`configs/Shared.env`** (default **11** = max ORB 0DTE **6** + max Trendline **5**). `PrimeTradingSystem._total_option_positions_count()` sums open positions from both books so neither path can exceed the combined ceiling independently of the other path’s per-path cap.

### Post-7:30 Watch Loop

Trendline watch runs in the existing monitoring loop and reuses quote infrastructure. Bars are **quote-derived** (synthetic OHLC from last/close); **time-based hold** uses real elapsed time from the break timestamp on those samples, not “one bar = one minute” unless your feed is true 1m candles.

**Open Trendline option positions** have a dedicated fast monitor loop in `PrimeTradingSystem`, default **7s** via `TRENDLINE_POSITION_MONITOR_INTERVAL_SEC`. This fast path runs **only** `_monitor_trendline_positions` for already-open Trendline option positions and does **not** run candidate watch scanning. The shared `position_monitor_interval` loop remains unchanged for ORB ETF / ORB 0DTE cadence and Trendline watch cadence.

For active candidates:

- append snapshot bar → break stage or confirmation stage
- on confirmation: hold → **normal-path OR** (expansion / local continuation / post-break structure, per config) → momentum (+ optional post-continue stages still in **`TrendlineConfig`** defaults) → emit signal or invalidate
- execute via `TrendlineOptionsExecutor` (contract selection + demo fill)

### Execution + Monitoring + Exit

**Execution routing (current):** `TrendlineSignalEngine` emits `TrendlineTradeSignal`, then creates and attaches a unified **`ExecutionIntent`** object (`signal.metadata["execution_intent"]`). `PrimeTradingSystem` performs a Trendline risk gate (`evaluate_intent`) before calling `TrendlineOptionsExecutor`. If intent is missing/invalid or risk rejects, execution is skipped with explicit `TRENDLINE_PIPELINE` logs.

**Execution:** Demo-first; opens `TrendlinePosition` rows in `TrendlineAccountManager`. On successful fill, **`TrendlineOptionsStealthEngine.register_on_open`** stores entry premium, strike, delta, and **`trendline_for_exit`** (slope/intercept + line geometry) inside `position.metadata`. **Live broker opens** for Trendline are **not** wired yet (`live_execution_not_wired` in `trendline_options_executor.py`); demo fills still log **`EXECUTION_FILL_SUMMARY`** when smart execution telemetry is active on the demo path.

**Sibling execution policy (May 15, shared with ORB paths):** When **`USE_MARKET_ORDERS=false`** and **`ENABLE_SMART_EXECUTION=true`**, ORB 0DTE and ORB SO use the shared **`modules/execution_*`** stack (see [Strategy.md](Strategy.md#execution-policy-may-15-local--deploy-pending) and [0DTEORB.md](0DTEORB.md)). Trendline **live** opens will use the same options stack when wired; **Trendline option closes** today route through **`prime_options_stealth_trailing_tp`** → **`options_trading_executor`** (urgency-aware LIMIT vs MARKET via **`resolve_options_exit_plan`**), same as ORB 0DTE. ORB ETF **`prime_stealth_trailing_tp`** equity closes are unchanged by Trendline.

**Monitoring:** `_monitor_trendline_positions` delegates to **`trendline_options_stealth.process_position`** when the engine is present. A reentrancy guard + lock prevent overlapping monitor runs when shared and fast loops are close in time (`fast_monitor_reentrant_skip`). **Effective premium per contract** is resolved in order: (1) **mid** from bid+ask (preferred live source), (2) **last**, (3) **recent cache** (<5s), (4) **delta_estimate** proxy (underlying move × sensitivity scaled by entry delta and moneyness drift, with per-tick jump clamps and min/max vs entry). Legacy `current_position_value` is not used as a runtime price source for Trendline single-leg monitoring. Within a single monitor tick, option chain fetches are deduped **once per (`symbol`, `expiry_ymd`)** and reused across positions; `chain_fetch` logs include cache hit/miss. Position metadata stores `premium_source`, bid/ask/last, `modeled_premium`, `effective_premium_used`, plus live **HWM/LWM**, underlying, PnL%, breakeven/trailing flags, stop level, and **structure still valid**. Data-quality observability includes `OPTIONS_DATA_SOURCE_UPDATE`, `OPTIONS_DATA_FALLBACK_USED`, and `OPTIONS_DATA_OUTAGE` markers, plus startup `0DTE_RUNTIME_CONFIG`. **Long gaps between stealth evaluations** or **large tick-to-tick premium moves** can trigger `option_monitor_stale`, `option_premium_jump`, and optional `option_forced_reeval` (see `OPTION_STEALTH_MAX_STALE_SECONDS`, `OPTION_STEALTH_PREMIUM_JUMP_RECHECK_PCT`, `OPTION_STEALTH_FORCE_REEVAL_ON_PREMIUM_JUMP`). Decisions use **stealth rules**, not fixed `TRENDLINE_EXIT_TP_PCT` / `TRENDLINE_EXIT_SL_PCT`.

**Registration source of truth:** After fill, `metadata["normalized_options"]` is built by the Trendline executor (`build_trendline_normalized_metadata`). `PrimeTradingSystem` calls **`validate_normalized_options_for_stealth`** (`easy0DTE/modules/options_execution_normalize.py`); when valid, **`register_on_open`** uses that blob as the primary source (legs, expiry, underlying, entry, `strategy_type`, `source_path`, etc.), preserving **`trendline_for_exit`** from the order payload and keeping **`structure_invalidation_enabled=True`**. If validation fails or the blob is absent, registration falls back to payload/signal fields and logs `OPTIONS_STEALTH | stage=legacy_metadata_fallback` (and `normalized_metadata_invalid` when a dict was present but incomplete).

Underlying quotes: batch quotes for equity symbols; if a batch fails, monitor falls back to last watch price or entry snapshot so open positions still evaluate.

**If stealth is absent** (fallback): legacy underlying-proxy TP/SL percentages still apply for backward compatibility.

**Exit reasons (stealth):** structure invalidation (underlying vs line), time exit (flat/slow while PnL% **below** trailing trigger—for **Trendline single-leg**, longer `OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES` and **no** time exit while `pnl_pct` ≥ **`OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT`**), **no-progress** (same chop-hold skip for Trendline when modestly green; Trendline uses **`OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES`** when set), **fast-fail**, **tline_profit_lock** (raises floor after small premium gain), breakeven stop after trigger (delta-bucket + optional Trendline BE mult, min seconds, optional new-HWM gate, then floor lock), trailing stop from HWM (trailing gate can use **`OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT`** on Trendline singles), EOD.

### End of Day

Trendline open positions are closed as part of **`PrimeTradingSystem.flatten_all_paths_for_eod_scheduler()`**, which runs (**a**) in the main loop during **`SO_ETF_EOD_CLOSE_START_PT`–`SO_ETF_EOD_CLOSE_END_PT`** in **`configs/ORBSO.env`** (default **12:55**–**12:56** PT), and (**b**) again from **`POST /api/end-of-day-report`** before Telegram stats — **same-process dedupe** skips a second close if (**a**) already ran. The **Telegram** Trendline end-of-day **summary** is sent only via **`/api/end-of-day-report`** (**~1:05 PM PT / 4:05 PM ET** when Cloud Scheduler `end-of-day-report` is configured that way); see [Alerts.md](Alerts.md).

- batch **underlying** quotes are fetched when possible; fallbacks: entry underlying from position snapshot → last watch price → small positive floor for modeled math
- optional **option** quote via the same API as intraday when available
- **`complete_eod_close`** resolves exit premium (same resolution order as intraday), attaches **`exit_diagnostics`**, records the close in session metrics, and logs `TRENDLINE_PIPELINE | stage=eod_close | symbol=... | premium_source=... | premium=... | pnl_pct=...` (and `note=underlying_fallback` when underlying was synthetic)
- after closing any Trendline positions at EOD: **`stage=options_exit_summary`** aggregates counts (breakeven / trailing / invalidation / no-progress / time cap / **fast-fail** / EOD), average PnL%, excursion/drawdown/timing stats, and exit **premium_source** usage
- intraday: when the **last** open Trendline position is closed in the monitor loop, **`options_exit_summary`** is also emitted so sessions that flatten before EOD still get a summary
- trendline EOD alerts / summary flows unchanged in intent; positions are closed in the **Trendline ledger only**

## Candidate Lifecycle (State Machine)

Defined in `TrendlineCandidateState`:

- `WAITING_FOR_BUILD`
- `WAITING_FOR_BREAK`
- `WAITING_FOR_CONFIRMATION`
- `READY_TO_EXECUTE`
- `EXECUTED`
- `INVALIDATED`
- `EXPIRED`

Common invalidation/expiry reasons include:

- missing ORB context
- insufficient pre-7:30 bars
- trendline build failure
- micro-break rejection
- failed momentum confirmation
- momentum stall timeout
- late confirmation timeout (`break -> confirmation` exceeded max delay)
- choppy structure invalidation (cross-count / compressed range)
- entry too far from trendline (anti-chase guard)
- expiration cutoff reached

## Entry Timing (Current Logic)

Entries are gated to avoid first-break fakeouts and to align **long call** vs **long put** with the **correct side of the line** after the break.

**Sequence:**

1. **Break** detected (close beyond line by **`TrendlineConfig.min_break_pct`** / ATR rules — values come from env when set: **`TRENDLINE_MIN_BREAK_PCT`**, **`TRENDLINE_USE_ATR_BREAK`**, **`TRENDLINE_ATR_BREAK_MULTIPLIER`**, **`TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES`**, etc., via `load_trendline_config_from_env`).
2. **Hold** — must stay on the **trade side** of the line:
   - **Ascending support (bull geometry):** breakdown → hold requires closes **below** the line.
   - **Descending resistance (bear geometry):** breakout → hold requires closes **above** the line.
   - **`hold_mode=time_based` (default):** elapsed **`TRENDLINE_CONFIRM_SECONDS`** (clamped **1–30** s) after the break; while waiting → **pending** (no invalidate). **`TRENDLINE_MIN_HOLD_SECONDS`** is **deprecated** and ignored for config loading (warns if still in env).
   - **`hold_mode=true_bar_based`:** need **`TrendlineConfig.hold_bars_after_break`** consecutive valid-side samples — populated from **`TRENDLINE_HOLD_BARS_REQUIRED`** in env (mapped in `load_trendline_config_from_env`). **`TRENDLINE_HOLD_MODE`** and **`TRENDLINE_HOLD_BAR_INTERVAL`** are also loader-backed.
3. **Normal-path OR (when time-hold fails)** — if **`TRENDLINE_REQUIRE_HOLD_AFTER_BREAK`** and time-hold fails, the engine can still proceed when **expansion** and/or **local continuation** and/or **post-break structure** satisfy the OR gate (see **`TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK`**, **`TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE`**, expansion env keys; logs **`TRENDLINE_NORMAL_GATE_OR`**).
4. **Post-break structure** — bull: last bar’s low is **≤** the minimum of prior bars in the lookback window (ties allowed); bear: last bar’s high is **≥** the maximum of prior bars (ties allowed). Lookback length is **`TrendlineConfig.post_break_structure_lookback_bars`** from **`TRENDLINE_POST_BREAK_STRUCTURE_LOOKBACK_BARS`** when set.
5. **Post-break acceptance (impulse-fast paths, May 15)** — before impulse **`signal_emit`**, **`_post_break_acceptance_met()`** must pass via structural acceptance, slow-trend acceptance, continuation distance without reclaim, or beyond-line closes with persistent line separation. Logged as **`TRENDLINE_POST_BREAK_ACCEPTANCE`**.
6. **Momentum + quality scoring** — break/body/displacement/expected-move/continuation persistence contribute to scoring and classification; impulse-fast path uses **`_momentum_persistence_agrees()`** (2-of-3 directional closes vs single-bar tick). Hard vetoes reserved for catastrophic/invalid/chop/stale/liquidity cases. **Body ratio:** qualify with **`body_ratio_vs_prev`**; avg-based ratio is advisory/catastrophic only.
7. **Anti-chase** — **`max_entry_distance_pct`** from **`TRENDLINE_MAX_DISTANCE_FROM_BREAK_PCT`**.
8. **Pullback continuation** — still tracked for strong setups; continuation window/thresholds live in **`TrendlineEngineInternals`** (not env).
9. **Contract selection** — **`load_trendline_option_selection_config`**: delta min/max, max spread, **`TRENDLINE_OPTION_STRIKE_MODE`**, **`TRENDLINE_OPTION_LOTTO_MODE`**, **`TRENDLINE_OPTION_MIN_OPEN_INTEREST`**, **`TRENDLINE_OPTION_MIN_VOLUME`**. Executor **`os.getenv`**: **`TRENDLINE_DELTA_TOLERANCE`**, sizing **`TRENDLINE_MAX_POSITION_PCT`**.
10. Emit signal → **`signal_emit`** / **`entry_ready`**; execute → **`TRENDLINE_PIPELINE | stage=execution | action=filled`**.

Pipeline shorthand:

`break -> hold (time|bars) -> [OR: expansion | continuation | structure] -> post_break_acceptance (impulse-fast) -> momentum (2-of-3 persistence) -> anti_chase -> contract -> execute`

**Prime direction override (post-signal):** `PrimeTradingSystem` still evaluates direction conflict. When `execution_reason=executed_valid_confirmation` and entry is fresh (`seconds_since_break <= 20`), direction conflict becomes advisory unless catastrophic reversal is detected — orchestrator logs **`TRENDLINE_DIRECTION_ADVISORY`** (`decision=allow_fresh_valid_confirmation`). `TrendlineOptionsExecutor` applies aligned behavior and logs **`TRENDLINE_EXECUTOR_DIRECTION_ADVISORY`** when continuing (override/reject paths log **`TRENDLINE_DIRECTION_OVERRIDE`** / **`TRENDLINE_DIRECTION_REJECT`**).

### Break Behavior

- First break tracked; failure after break invalidates with explicit **`entry_blocked`** / `hold_failed` / etc.
- Confirmation is first-break driven, with pullback-continuation tracking available for strong setups that are temporarily blocked by anti-chop/follow-through noise.

### Acceptance Logic

Hold/structure are **direction-correct** for each setup type (support breakdown vs resistance breakout), not a single “always above line” rule.

## Duplicate-Execution Protection

Trendline path includes layered dedupe guards:

- candidate ID keying (`symbol:direction`) in signal engine
- emitted-signal dedupe in signal engine
- per-symbol/day executed guard in orchestrator
- candidate ID dedupe guard in executor
- trade ID collision guard in executor
- strict one-trade-per-symbol-per-day execution guard

Dedupe logs use:

- `TRENDLINE_PIPELINE | stage=dedupe_guard`

## Alerts and Expected Behavior

Trendline alerts are independent and event-driven (not strictly tied to 7:30 timing). Telegram titles use **`Easy Trendline 0DTE`** (see [Alerts.md](Alerts.md)):

- **Fill** — `🔮 Trendline Options Execution | DEMO` (after successful `TrendlineOptionsExecutor` open)
- **Exit** — `🔮 TRENDLINE OPTION CLOSED | DEMO`
- **Monitor** (optional, throttled) — `🔮 Easy Trendline 0DTE — Monitor | DEMO` (disabled by default via `TRENDLINE_MONITOR_ALERT_ENABLED=false`)
- **Scheduled EOD** — `💎 END-OF-DAY TREND 0DTE | 🎲` (raw body via same EOD pipe as other paths)

This is in addition to existing ORB Capture / Signal Collection / SO Execution / ORB 0DTE Execution / existing exit/EOD flows.

## Dedicated Account Isolation

Trendline account is intentionally isolated:

- separate `TrendlineAccountManager` ledger
- separate starting balance (`TRENDLINE_DEMO_STARTING_BALANCE`, default set at init path)
- no reuse of ORB ETF mock account
- no reuse of ORB 0DTE mock options account
- trendline EOD close/report operates on trendline account positions/stats only

## Execution Payload Mapping

Trendline execution payload includes:

- `symbol`, `underlying_symbol`
- `direction` (line geometry: `bull` / `bear`)
- `option_side` (`call` / `put`)
- `strike`, `delta_at_entry`, `otm_steps` (when selection succeeds)
- `dte` = `0` (intent: 0DTE)
- `expiry_ymd` — `YYYYMMDD` for US/Eastern session day (chain / option quote lookup)
- `trendline_for_exit` — `slope_per_second`, `intercept`, `direction` for stealth **structure invalidation**
- `strategy_name=easyTrendline_0DTE`, `entry_type=trendline_break`, `strategy_type=trendline_0dte`
- **`source_path`:** **`easyTrendline`** on 7:30 **`setup_payload`** / orchestrator candidate seeds; **`trendline_0dte`** on the **`TrendlineOptionsExecutor`** execution payload and on the **`normalized_options`** blob passed into stealth (`build_trendline_normalized_metadata(..., source_path="trendline_0dte")`). Either string may appear in logs; stealth registration prefers validated **`normalized_options`**.
- `trigger_type=trendline_break_momentum`
- `setup_type`, `trigger_direction`, `expected_option_side`, `trendline_structure_source`
- `trade_id`, slot fields, `estimated_contract_price`, `quantity`, `estimated_total_cost`
- nested `meta` on the trade signal (hold mode, scores, etc.)

On fill, **`register_on_open`** also stores `underlying_symbol`, `expiration_ymd`, `setup_type`, and `trigger_direction` in stealth engine metadata for exit telemetry.

Downstream lifecycle and stealth registration use these fields; ORB ETF / ORB 0DTE payloads are unchanged.

## Telemetry and Feature Capture

Trendline feature snapshots are written to:

- `data/trendline_optimizer/trendline_features.jsonl`

One event row is appended for key lifecycle stages (build / executed / execution_failed / execution_skipped_cap / **option_exit** / eod_close). On exit, rows include **`exit_diagnostics`** (full stealth exit record) when available.  
Captured fields include:

- `setup_type`, `trigger_direction`, `expected_option_side`, structure selection metadata
- `hold_mode`, `hold_duration_seconds`, `continuation_break_time`
- `delta_at_entry`, `strike_selected`, `premium_at_entry` (when execution payload exists)
- symbol, date, line geometry (`direction`), ORB high/low, anchors, slope
- break timestamp / distance / threshold
- momentum scores, break quality, missed-move diagnostics
- execution / cap / sizing block reasons

The logger is defensive: write failures are non-fatal and do not break trading flow.

### Priority Optimizer Execution Snapshot Integration

In addition to `data/trendline_optimizer/trendline_features.jsonl`, successful Trendline option executions now write into the shared Priority Optimizer snapshot stream:

- GCS/local path family: `priority_optimizer/execution_snapshots/`
- Stage: `trendline_options_executed`
- Strategy tag: `snapshot_strategy=easy_trendline_0dte`
- Purpose: unify ORB SO and Trendline execution-time datasets under one retrievable snapshot prefix while keeping paths distinguishable by `stage` + `snapshot_strategy`.

## Developer Validation Helper

`TrendlineSignalEngine.dry_run_candidate(...)` provides a replay-style internal check:

- trendline built?
- break detected?
- momentum confirmed?
- would execution trigger?
- final state + state path

This is intended for developer validation/testing only.

## Key Config Knobs

Use this section as the **operator checklist** when calibrating Trendline entries/exits. **Authoritative key inventory:** `ALL_TRENDLINE_CONSUMED_ENV_KEYS` in `easyTrendline/trendline_config_loader.py` (includes every `TRENDLINE_*`, `TRENDLINE_OPTION_*`, and `OPTION_STEALTH_TLINE_*` name the package reads). **Do not add new env names** without adding them to that set and to `load_trendline_config_from_env` / `load_trendline_option_selection_config` / stealth loaders as appropriate—otherwise startup will emit **`TRENDLINE_CONFIG_UNUSED_WARNING`**.

**Pinned repo defaults** for the Trendline path are listed at the end of this doc (**Appendix: `configs/Trendline0DTE.env` snapshot**); refresh that appendix when you edit the env file.

### Configuration levels (what actually reads env)

| Level | Source | Purpose |
|-------|--------|---------|
| **A** | `load_trendline_config_from_env` → `TrendlineConfig` | **Canonical signal path** — break quality floors, confirmation gates, expansion thresholds, anti-chase, session entry cutoff, confirm seconds |
| **B** | `load_trendline_option_selection_config` → `TrendlineOptionSelectionConfig` | **Canonical contract filter** — delta band + max bid/ask spread |
| **C** | `trendline_options_executor.py` `os.getenv` | **Executor-only** — max position %, delta tolerance, demo mode |
| **D** | `PrimeTradingSystem` / build path `get_config_value` | Caps, watch cadence, build budgets, monitor throttles, daily cap, target active count — **not** passed into `TrendlineConfig` |
| **E** | `TrendlineConfig` / `TrendlineOptionSelectionConfig` **dataclass fields** in `trendline_models.py` | Defaults used everywhere unless loader is extended (break %, ATR, momentum window, chop, hold bars, post-continue flags, `expansion_break_level_return_max`, etc.) |
| **F** | `DEFAULT_TRENDLINE_ENGINE_INTERNALS` in `trendline_engine_internals.py` | Rearm, regime, impulse, drift, continuation windows, pressure/scoring, survival bars, fast-path absolute floors — **code constants** |
| **G** | `load_option_stealth_config` and related | Exits / premium behavior (`OPTION_STEALTH_*`, Trendline-specific `OPTION_STEALTH_TLINE_*`, legacy `TRENDLINE_*` BE/trail keys consumed by stealth) |
| **H** | *(none)* | **`TRENDLINE_*` / `OPTION_STEALTH_TLINE_*` strings in merged env that are not in `ALL_TRENDLINE_CONSUMED_ENV_KEYS`** — not read; **`TRENDLINE_CONFIG_UNUSED_WARNING`** at startup (see **Unused / mistyped env names** below). |

### Level A — Canonical signal (`TrendlineConfig`)

Loaded in `easyTrendline/trendline_config_loader.py` (`load_trendline_config_from_env`). The table below lists **commonly tuned** keys; the **full** set is **`_TRENDLINE_MAIN_ENV_KEYS`** in that file (includes hold bars/mode, min break %, ATR, chop, rearm, regime, impulse/retest/slow-trend, continuation/pullback, reversal, drift, strict body/distance floors, expected-move tiers, survival/active windows, etc.).

| Env variable | `TrendlineConfig` field | Role |
|--------------|-------------------------|------|
| `TRENDLINE_BREAK_DISTANCE_MIN` | `break_distance_min` | Normalized minimum break distance for quality / tiering (with engine-internal absolute floors on fast paths) |
| `TRENDLINE_BODY_RATIO_MIN_STRONG` | `body_ratio_min_strong` | Strict break-quality body ratio (strong tier) |
| `TRENDLINE_BODY_RATIO_MIN_WEAK` | `body_ratio_min_weak` | Weaker body-ratio tiering threshold |
| `TRENDLINE_FAST_PATH_ENABLED` | `fast_path_enabled` | When true, engine may emit on fast-track / reduced gates per internal rules |
| `TRENDLINE_CONFIRM_SECONDS` | `confirm_seconds` | Time-based hold target after break (**clamped 1–30** seconds) |
| `TRENDLINE_REQUIRE_HOLD_AFTER_BREAK` | `require_hold_after_break` | Require time-hold when true (OR-path still applies after failed hold when other gates enabled) |
| `TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK` | `require_local_continuation_break` | Require local continuation leg in normal path |
| `TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE` | `require_post_break_structure` | Require post-break structure unless expansion waives it |
| `TRENDLINE_EXPANSION_NET_MOVE_RATIO_MIN` | `expansion_net_move_ratio_min` | Minimum net-move ratio for post-break **expansion** acceptance |
| `TRENDLINE_EXPANSION_OVERLAP_MAX` | `expansion_overlap_max` | Maximum overlap ratio for expansion candle vs prior range |
| `TRENDLINE_MAX_BREAK_TO_CONFIRM_MINUTES` | `max_break_to_confirm_minutes` | Wall-clock budget from first break through confirmation; **`0` = disabled** |
| `TRENDLINE_MAX_DISTANCE_FROM_BREAK_PCT` | `max_entry_distance_pct` | Anti-chase: max distance from line at signal time |
| `TRENDLINE_NO_NEW_ENTRIES_AFTER_PT` | `no_new_entries_after_pt` | Pacific **`HH:MM`** — no new entries after this clock (Prime + engine) |

**Prime alignment:** `modules/prime_trading_system.py` reads **`TRENDLINE_BREAK_DISTANCE_MIN`** and **`TRENDLINE_BODY_RATIO_MIN_STRONG`** again for **direction-conflict override** logic (strong-break floors **`max(0.008, bdm)`** and **`max(0.6, br_strong)`**). Keep Level A values consistent with that override when tuning.

### Deprecated env (warn once, ignored for `TrendlineConfig`)

If any of these keys are still in **`os.environ`**, startup logs **`TRENDLINE_CONFIG_DEPRECATED | key=... | detail=ignored_use_canonical_loader`** (see `_DEPRECATED_TRENDLINE_ENV_KEYS` in `trendline_config_loader.py`):

`TRENDLINE_MIN_HOLD_SECONDS`, `TRENDLINE_MIN_ENTRY_DISTANCE`, `TRENDLINE_IMMEDIATE_BREAK_MIN_DISTANCE`, `TRENDLINE_IMPULSE_MIN_BREAK_DISTANCE`.

**Note:** `TRENDLINE_STRICT_MIN_BREAK_DISTANCE` is **loaded** into `TrendlineConfig.strict_min_break_distance` (not deprecated). Prefer **`TRENDLINE_STRICT_MIN_BREAK_DISTANCE`** / **`TRENDLINE_BODY_RATIO_MIN_STRICT`** over the deprecated impulse/min-distance keys above.

### Level B — Canonical option selection (`TrendlineOptionSelectionConfig`)

| Env variable | Field | Role |
|--------------|-------|------|
| `TRENDLINE_OPTION_DELTA_MIN` | `delta_min` | Lower bound of target \|delta\| band |
| `TRENDLINE_OPTION_DELTA_MAX` | `delta_max` | Upper bound of target \|delta\| band |
| `TRENDLINE_OPTION_MAX_SPREAD_PCT` | `max_bid_ask_spread_pct` | Reject contracts when bid/ask spread exceeds this fraction |
| `TRENDLINE_OPTION_STRIKE_MODE` | `strike_mode` | Strike selection mode string (e.g. `otm_1_to_2`) |
| `TRENDLINE_OPTION_LOTTO_MODE` | `lotto_mode` | Lotto bias toggle |
| `TRENDLINE_OPTION_MIN_OPEN_INTEREST` | `min_open_interest` | Minimum OI filter |
| `TRENDLINE_OPTION_MIN_VOLUME` | `min_volume` | Minimum volume filter |

All are read in **`load_trendline_option_selection_config`** (`trendline_config_loader.py`).

### Level C — Executor `os.getenv` (`trendline_options_executor.py`)

| Env variable | Role |
|--------------|------|
| `TRENDLINE_MAX_POSITION_PCT` | Cap fraction of slot capital per open (default **0.12**) |
| `TRENDLINE_DELTA_TOLERANCE` | Extra \|delta\| slack beyond band for near-boundary picks (default **0.02**) |
| `ETRADE_MODE` | Demo vs live for simulated-fill behavior (`demo` → demo mode when not overridden at construct time) |

### Level D — Prime / app (`get_config_value` unless noted)

**Path / universe / caps:** `ENABLE_TRENDLINE_STRATEGY` (**`os.getenv`** in Prime), `TRENDLINE_USE_FULL_0DTE_LIST`, `TRENDLINE_MAX_TRADES_PER_DAY`, `TRENDLINE_SLOT_COUNT`, `TRENDLINE_MAX_OPEN_POSITIONS`, `TARGET_TRENDLINE_ACTIVE`, `MAX_TRENDLINE_OPTION_SYMBOLS`, `TRENDLINE_ACCOUNT_ALLOCATION_PCT` (falls back to `SO_CAPITAL_PCT`), `TRENDLINE_DEMO_STARTING_BALANCE`, `MAX_TOTAL_OPTION_POSITIONS` (ORB 0DTE + Trendline combined option opens).

**7:30 build budgets** (same table as earlier in this doc): `TRENDLINE_DATA_CHUNK_SIZE`, `TRENDLINE_MAX_INTRADAY_BATCH_CALLS_PER_BUILD`, `TRENDLINE_MAX_QUOTE_BATCH_CALLS_PER_BUILD`, `TRENDLINE_MAX_SYMBOLS_PER_BUILD`, `TRENDLINE_ENABLE_BUILD_DEGRADATION`, `TRENDLINE_MAX_BUILD_DURATION_MS`, `TRENDLINE_BUILD_ANCHOR_LOG_MAX`.

**Watch / monitor throttles:** `TRENDLINE_POSITION_MONITOR_INTERVAL_SEC`, `TRENDLINE_POSITION_MONITOR_INTERVAL_SEC_DYNAMIC`, `TRENDLINE_WATCH_STATUS_LOG_SEC`, `TRENDLINE_WATCH_NEARLINE_LOG_SEC`, `TRENDLINE_WATCH_IDLE_LOG_SEC`, `TRENDLINE_WATCH_EMPTY_QUOTES_LOG_SEC`, `TRENDLINE_MONITOR_ALERT_*`, `TRENDLINE_GLOBAL_DAILY_CAP_*`.

**Other:** `TRENDLINE_CONFIRM_SECONDS` is echoed in `SYSTEM_VERSION` for deploy verification (reads env string in Prime init).

### Level E — `TrendlineConfig` defaults (fallback when env omits a key)

Defined on **`TrendlineConfig`** in `trendline_models.py`. Most fields have **env counterparts** in `_TRENDLINE_MAIN_ENV_KEYS`; if a key is absent from merged config, startup logs **`TRENDLINE_CONFIG_DEFAULT_USED`** for that key and uses the dataclass default. Tune defaults in **code** only when you want a new baseline without setting env everywhere.

- **Current repo baseline (May 6):** `configs/Trendline0DTE.env` now pins the full `TrendlineConfig` loader surface (including advanced calibration keys), so operators can review/tune in one file. Keep the loader as authority for new key additions.

### Level F — `TrendlineEngineInternals` (`trendline_engine_internals.py`)

Single frozen dataclass **`DEFAULT_TRENDLINE_ENGINE_INTERNALS`** supplies signal-engine constants **not** duplicated on `TrendlineConfig`. Many **pressure / survival / regime / drift** numerics are **also** overridden from env via **`TrendlineConfig`** (see loader). For anything still only on **`DEFAULT_TRENDLINE_ENGINE_INTERNALS`**, edit **`trendline_engine_internals.py`**.

### Unused / mistyped `TRENDLINE_*` names

If a name is **not** in `ALL_TRENDLINE_CONSUMED_ENV_KEYS` (`trendline_config_loader.py`), it has **no effect** on `TrendlineConfig` / option config / documented `OPTION_STEALTH_TLINE_*` reads—startup emits **`TRENDLINE_CONFIG_UNUSED_WARNING`**. Examples of mistakes: deprecated spellings, renamed keys (e.g. chop gate must be **`TRENDLINE_POST_CONTINUE_CHOP_BOX_GATE_ENABLED`**, not `..._CHOP_BOX_GATE`), or leftover keys from older templates. **Verify names against the loader** before assuming a line in **`configs/Trendline0DTE.env`** takes effect.

### Level G — Options stealth trailing (→ `OptionStealthConfig` via `load_option_stealth_config`)

| Env variable | Role |
|--------------|------|
| `OPTION_STEALTH_BREAKEVEN_TRIGGER_PCT` | Fallback BE trigger if bucket not applied; at open, **ITM/ATM/OTM** thresholds below are stored on the position |
| `OPTION_STEALTH_BREAKEVEN_LOCK_PCT` | BE floor = entry × (1 + lock) when `EARLY_BE_LOCK_PCT` is zero or negative |
| `OPTION_STEALTH_EARLY_BE_LOCK_PCT` | Preferred BE lock when **positive**; otherwise code uses `BREAKEVEN_LOCK_PCT` |
| `OPTION_STEALTH_DELTA_ITM_THRESHOLD` | \|delta\| ≥ this → treat as **ITM** for BE trigger bucket |
| `OPTION_STEALTH_DELTA_ATM_THRESHOLD` | \|delta\| between ATM and ITM thresholds → **ATM** bucket |
| `OPTION_STEALTH_BE_TRIGGER_ITM_PCT` / `OPTION_STEALTH_BE_TRIGGER_ATM_PCT` / `OPTION_STEALTH_BE_TRIGGER_OTM_PCT` | Per-bucket BE trigger thresholds |
| `OPTION_STEALTH_MIN_SECONDS_BEFORE_BE` | Minimum seconds after open before BE can arm |
| `OPTION_STEALTH_REQUIRE_NEW_HWM_FOR_BE` | If true, require a new premium HWM after open before BE can arm |
| `OPTION_STEALTH_TRAILING_TRIGGER_PCT` | Start HWM trailing |
| `OPTION_STEALTH_BASE_TRAILING_PCT` | Trail width from HWM |
| `OPTION_STEALTH_EXPLOSIVE_TRAILING_PCT` / `OPTION_STEALTH_MOON_TRAILING_PCT` | Tighter trails at higher PnL |
| `OPTION_REQUIRE_LIVE_QUOTES` | Strict live quote mode for singles: when true, no delta/proxy execution basis; missing/stale live quotes hold prior/entry premium and mark degraded path |
| `OPTION_PRICE_RESOLUTION_AUDIT_MIN_SECONDS` | Throttle interval for `OPTION_PRICE_RESOLUTION_AUDIT` and degraded-live-quote logging dedupe per `trade_id|symbol` (0 = per tick) |
| `OPTION_STEALTH_TIME_EXIT_MINUTES` | Flat/slow time stop (evaluated when PnL% **below** trailing trigger); **Trendline single-leg** can override with `OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES` |
| `OPTION_STEALTH_NO_PROGRESS_EXIT_MINUTES` | Stall after last HWM update (evaluated when PnL% **below** trailing trigger); Trendline override: `OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES` |
| `OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES` | **Trendline single-leg only:** minutes before time-exit is considered (default **240** in code when set in env) |
| `OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES` | **Trendline single-leg:** minutes without new premium HWM before no-progress exit (default **45** typical) |
| `OPTION_STEALTH_TLINE_BE_TRIGGER_MULT` | Multiplier on delta-tier BE **trigger** for Trendline singles (e.g. tighten vs OTM default) |
| `OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT` | Multiplier on trailing **activation** threshold for Trendline singles |
| `OPTION_STEALTH_TLINE_PROFIT_LOCK_TRIGGER_PCT` | When premium `pnl_pct` ≥ this, raise `breakeven_floor` to entry × (1 + next row) |
| `OPTION_STEALTH_TLINE_PROFIT_LOCK_PCT` | Early **profit floor** increment (e.g. **0.012** = +1.2% above entry debit after small winner) |
| `OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT` | While `pnl_pct` ≥ this, **skip** time_exit and no_progress for Trendline singles (slow grind still “working”) |
| `OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MINUTES` | **May 2026:** Minutes after open before the **early** single-leg scratch is *eligible* (default **7** in code/`Shared.env`). Fires only when **structure is already invalid** (`_structure_invalid`) **and** underlying has **not** moved favorably vs entry — avoids cutting valid breakdown PUTs during a shallow premium stall. |
| `OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MAX_PNL_PCT` | Premium `pnl_pct` must stay **below** this (e.g. **0.05**) for the early scratch; close reason is **`no_progress_timeout_early`** (distinct from `no_progress_timeout`). |
| `OPTION_STEALTH_FAST_FAIL_ENABLE` | Enable fast-fail exit for shallow green stalls |
| `OPTION_STEALTH_FAST_FAIL_MINUTES` | Window for fast-fail |
| `OPTION_STEALTH_FAST_FAIL_MIN_PNL_PCT` | Fast-fail only runs while PnL% is **strictly below** this threshold |
| `TRENDLINE_EXIT_MIN_HOLD_SECONDS` | Trendline min-hold protection window; during this window, breakeven/trailing/adverse exits are blocked |
| `TRENDLINE_BREAKEVEN_ACTIVATE_PCT` / `TRENDLINE_BREAKEVEN_OFFSET_PCT` | Baseline Trendline BE activation and floor offset |
| `TRENDLINE_TRAIL_ACTIVATE_PCT` / `TRENDLINE_TRAIL_GIVEBACK_PCT` | Baseline Trendline trailing activation and giveback |
| `TRENDLINE_IMPULSE_BREAKEVEN_ACTIVATE_PCT` / `TRENDLINE_IMPULSE_TRAIL_ACTIVATE_PCT` / `TRENDLINE_IMPULSE_TRAIL_GIVEBACK_PCT` | Impulse-mode BE/trailing overrides |
| `TRENDLINE_SLOW_TREND_BREAKEVEN_ACTIVATE_PCT` / `TRENDLINE_SLOW_TREND_TRAIL_ACTIVATE_PCT` / `TRENDLINE_SLOW_TREND_TRAIL_GIVEBACK_PCT` | Slow-trend BE/trailing overrides |
| `TRENDLINE_RETEST_BREAKEVEN_ACTIVATE_PCT` / `TRENDLINE_RETEST_TRAIL_ACTIVATE_PCT` / `TRENDLINE_RETEST_TRAIL_GIVEBACK_PCT` | Retest/standard BE/trailing overrides |
| `OPTION_STEALTH_STRUCTURE_INVALIDATION_ENABLED` | Underlying vs line exit |
| `OPTION_STEALTH_DISABLE_TP_LADDER` | Document intent: no scale-out ladder on this path |
| `OPTION_STEALTH_STRUCTURE_BUFFER_PCT` | Buffer vs trendline for invalidation (fraction of price) |
| `OPTION_STEALTH_PREMIUM_MAX_JUMP_PCT` | Max fractional move of effective premium per evaluation vs previous |
| `OPTION_STEALTH_MAX_STALE_SECONDS` | If wall-clock seconds since **last stealth evaluation** exceed this, log `option_monitor_stale` (and optional `option_forced_reeval` with `reason=stale_monitor_data`) |
| `OPTION_STEALTH_PREMIUM_JUMP_RECHECK_PCT` | If effective premium moves vs the prior tick by at least this fraction, set `premium_jump_detected` and log `option_premium_jump` |
| `OPTION_STEALTH_FORCE_REEVAL_ON_PREMIUM_JUMP` | When true, also log `option_forced_reeval` on stale gap or premium jump (telemetry / next-pass hint) |
| `OPTION_STEALTH_PREMIUM_MAX_MULT` | Cap on premium as multiple of entry |
| `OPTION_STEALTH_PREMIUM_MIN_MULT` | Floor on premium as multiple of entry |
| `OPTION_STEALTH_EXPLOSIVE_PNL_THRESHOLD_PCT` | Min premium PnL fraction to tighten to explosive trail |
| `OPTION_STEALTH_MOON_PNL_THRESHOLD_PCT` | Min premium PnL fraction to tighten to moon trail |
| `OPTION_STEALTH_ORB_MFE_TRIGGER_PCT` | **MFE-retention** trigger for ORB `opening_impulse` single-leg (default **0.08**) — once max-PnL% crosses, drawdown protection arms |
| `OPTION_STEALTH_ORB_MFE_DRAWDOWN_EXIT_PCT` | Drawdown from MFE that fires retention exit on ORB single-leg (default **0.055**) |
| `OPTION_STEALTH_TLINE_IMPULSE_MFE_TRIGGER_PCT` | MFE trigger for Trendline `impulse` archetype (default **0.10**) |
| `OPTION_STEALTH_TLINE_IMPULSE_MFE_DRAWDOWN_EXIT_PCT` | Drawdown exit for Trendline `impulse` (default **0.07**) |
| `OPTION_STEALTH_TLINE_SLOW_MFE_TRIGGER_PCT` | MFE trigger for Trendline `slow_trend` (default **0.15**) |
| `OPTION_STEALTH_TLINE_SLOW_MFE_DRAWDOWN_EXIT_PCT` | Drawdown exit for Trendline `slow_trend` (default **0.10**) |
| `OPTION_STEALTH_TLINE_RETEST_MFE_TRIGGER_PCT` | MFE trigger for Trendline `retest` archetype (default **0.12**) |
| `OPTION_STEALTH_TLINE_RETEST_MFE_DRAWDOWN_EXIT_PCT` | Drawdown exit for Trendline `retest` (default **0.08**) |

**Spread-specific thresholds (ORB 0DTE / shared stealth module):** `OPTION_STEALTH_SPREAD_BE_TRIGGER_PCT`, `OPTION_STEALTH_SPREAD_BE_LOCK_PCT`, `OPTION_STEALTH_SPREAD_TRAILING_TRIGGER_PCT`, `OPTION_STEALTH_SPREAD_BASE_TRAILING_PCT`, `OPTION_STEALTH_SPREAD_TIME_EXIT_MINUTES`, `OPTION_STEALTH_SPREAD_NO_PROGRESS_EXIT_MINUTES` — apply when `position_type` is `debit_spread` or `credit_spread`; Trendline **single-leg** path uses the non-`SPREAD` keys in the table above.

**Premium modeling (shared):** `TRENDLINE_OPTION_SENSITIVITY` — scales the **modeled** leg when live quotes are unavailable (with entry delta / moneyness heuristics).

**Fallback monitoring (only if stealth engine missing):** `TRENDLINE_EXIT_TP_PCT`, `TRENDLINE_EXIT_SL_PCT`

## Logging Markers

Trendline observability uses `TRENDLINE_PIPELINE` markers, including:

**Lifecycle:** `init`, `config_loaded`, **`TRENDLINE_CONFIG_SUMMARY`**, **`TRENDLINE_CONFIG_SURFACE_AUDIT`**, **`TRENDLINE_CONFIG_DEPRECATED`** (deprecated env still set), **`line_built`**, `build` (includes **`action=use_selector_built`**), `build_failed`, `break_detected`, `first_break_detected`, `intent_created`, `risk_check`, `risk_rejected`, **`execution`** (`action=filled` on demo open), `dedupe_guard`, `invalidated`, `expired`, `missed_move`, `eod_close`, `eod_summary`, `summary`, `daily_cap_reached`, `execution_skipped_cap`, **`execution_blocked`**, `slot_sizing`, `sizing_rejected`, `monitor` (quote errors), `option_quote` (debug), `fast_monitor_init`, `fast_monitor_tick`, `fast_monitor_skip`, `fast_monitor_reentrant_skip`, `fast_monitor_metrics`, `chain_fetch`, **`watch_status`**, **`watch_nearline`**, **`watch_idle`**, **`confirm_pending`**, **`rearmed`**, **`TRENDLINE_SCORE_SUMMARY`**, **`TRENDLINE_HARD_VETO_AUDIT`**, **`TRENDLINE_DIRECTION_ADVISORY`** (orchestrator fresh-confirmation allowance), **`TRENDLINE_EXECUTOR_DIRECTION_ADVISORY`**

**Break archetype + delayed continuation + re-anchor (May 7 pass 2):**
- **`TRENDLINE_BREAK_ARCHETYPE`** — emitted at break time; `archetype` ∈ `impulse_exhaustion` | `delayed_continuation` | `weak_break_failure` | `catastrophic_micro_break` | `chop_fakeout`; includes `break_distance_pct` and quality fields.
- **`TRENDLINE_DELAYED_CONTINUATION_ARMED`** — marginal reversal/quality break deferred (`origin`, `ttl_minutes`, `break_low`, `break_high`, `ref_em_pct`).
- **`TRENDLINE_DELAYED_CONTINUATION_CHECK`** — per-bar check while armed (`bars_seen`, `origin`, optional `blocked_antichop`).
- **`TRENDLINE_DELAYED_CONTINUATION_ENTRY`** — deferred entry triggered (`em_pct`, `bars_since_arm`).
- **`TRENDLINE_DELAYED_CONTINUATION_EXPIRED`** — TTL/bars exhausted (`reason`, `bars_seen`, `ttl_bars`).
- **`TRENDLINE_REANCHOR_FREEZE`** — re-anchor frozen due to recent break context (`line_proximity_frac`, `touch_count`).
- **`TRENDLINE_STRUCTURE_FRAGILITY_ACTIVE`** — structure marked fragile (`touch_count`, `threshold`).
- **`TRENDLINE_REANCHOR_REJECT_BREAK_ERASURE`** — re-anchor would erase a valid near-break; rejected.
- **`TRENDLINE_REANCHOR_ACCEPTED`** — re-anchor accepted (`touch_count`, `anchor_spacing_min`).

**7:30 build telemetry (Rev 00325):** `build_context`, `build_summary`, `request_summary`, `build_degraded`, `build_bar_diagnostics`, `build_merge_failed`. Field semantics are in the **7:30 Build Step** and **broker request budgets** subsections above. Session rollups: `_trendline_session_request_totals`, `_trendline_last_build_telemetry` on `PrimeTradingSystem`. Missing ORB at filter: `build_reject` at DEBUG (`missing_orb_context`).

**Structure (7:30):** `setup_detected`, `setup_tie_break`, **`TRENDLINE_SELECTOR_STRUCTURE_OVERRIDE`**, **`TRENDLINE_SETUP_SKIP`** (e.g. `missing_descending_line` / `missing_ascending_line`), **`TRENDLINE_TREND_CONTINUATION_SKIP`** (`phase=setup_selector`; classifier may also emit continuation skips earlier), `setup_skipped` (typically `no_valid_trendline_structure` with `detail=insufficient_pre730_bars` or `no_eval_bars_for_fit` when selector rejects). **`TRENDLINE_DRAW_AUDIT`** — single-line draw audit (grep). ~~`setup_orb_price_rule`~~ — removed; ORB distance geometry rule is disabled.

**Entry gates:** `hold_check`, `hold_failed`, `entry_blocked` (sub-stages: `confirmation_timeout` / `late_confirmation`, hold, structure, momentum, momentum_quality, anti_chase, entry_filter, contract_or_execution, **`post_break_acceptance_required`**, **`survival_defer_requires_acceptance`**), `entry_ready`, `signal_emit`, `contract_selected`, `contract_rejected`, `pre_execute_check`, **`selector_structure_ready`** (selector line exists at init — **not** post-break acceptance; do not confuse with true structural acceptance), `first_break_failed`, `strong_breakout_fasttrack`, `TRENDLINE_PULLBACK_CANDIDATE`, `TRENDLINE_CONTINUATION_TRACK`, `TRENDLINE_CONTINUATION_ENTRY`, `TRENDLINE_CONTINUATION_EXPIRED`, `TRENDLINE_CHOP_REJECT`, `TRENDLINE_DELTA_TOLERANCE_USED`

**May 15 acceptance / expansion defer:** `TRENDLINE_POST_BREAK_ACCEPTANCE`, `survival_defer_low_expansion`, `survival_defer_requires_acceptance`, `body_ratio_vs_prev` / `body_ratio_vs_avg` in false-break gate lines.

**Entry gate telemetry:** `confirm_pending` surfaces while hold/structure/momentum gates are not yet satisfied and the candidate remains eligible. Additional markers include **`TRENDLINE_NORMAL_GATE_OR`** (which leg satisfied the normal-path OR), `TRENDLINE_CONFIRM_CONFIG`, `TRENDLINE_DRIFT_METRICS`, `TRENDLINE_BREAKOUT_QUALITY`, `TRENDLINE_BREAK_QUALITY`, `TRENDLINE_ENTRY_FILTER`, `TRENDLINE_ENTRY_FILTERED`, `TRENDLINE_ENTRY_DECISION`, **`TRENDLINE_DECISION_SNAPSHOT`** (canonical per-outcome row, **≤ one per candidate per bar**; structure, anchors, **`line_quality`**, **`confidence_score`**, **`source`** `selector_built`\|`classified`, timing-from-730 / structure-shift, pressure, break, expansion, **`entry_mode`**, **`entry_path`**, **`skip_reason`** when skipped), **`TRENDLINE_DECISION_GEOMETRY_DETAIL`** (intrabar geometry + expected-move context — separate schema), **`TRENDLINE_SKIP_REASON`** (canonical reason + optional **`raw_reason`**), **`TRENDLINE_FALLBACK_USED`**, **`TRENDLINE_MOVE_FALLBACK_USED`**, **`TRENDLINE_SELECTOR_STRUCTURE_ACTIVE`**, **`TRENDLINE_SELECTOR_LOW_CONFIDENCE`**, **`TRENDLINE_LINE_SELECTED`**, **`TRENDLINE_CANDIDATE_EXPIRED`**, **`TRENDLINE_PRESSURE_TOUCH`**, **`TRENDLINE_PRESSURE_SCORE`**, **`TRENDLINE_FLOW_STAGE`**, **`TRENDLINE_PRE_ENTRY_CHECKPOINT`**, **`TRENDLINE_ENTRY_EVAL_ORDER`**, **`TRENDLINE_MISSED_WIN`**, **`TRENDLINE_MISSED_WIN_EARLY`**, **`TRENDLINE_BAD_ENTRY`**, **`TRENDLINE_ALERT`**, `TRENDLINE_ENTRY_TIMING`, `TRENDLINE_DRIFT_ENTRY`, and `TRENDLINE_BREAKOUT_ENTRY`. **Guard / init:** **`TRENDLINE_LEGACY_PATH_BLOCKED`** if the signal engine would build a line without selector output (production path requires selector-built lines). (Older deploy docs referred to **`TRENDLINE_PREBUILT_*`** log names; current code emits **`TRENDLINE_SELECTOR_*`** / **`use_selector_built`**.)

**Canonical `TRENDLINE_SKIP_REASON` values** (non-matching strings are mapped for analytics; originals appear as **`raw_reason`** when remapped): `trend_continuation`, `unclear_structure`, `insufficient_structure_points`, `structure_not_mature`, `weak_break`, `retest_failed`, `chop_zone`, `strong_opposing_trend`, `final_gate_rejection`, `low_pressure`.

**Deployment/version telemetry:** `PrimeTradingSystem` emits `SYSTEM_VERSION | version=... | trendline_confirm_sec=...` at startup to hard-verify the running revision and active confirm-time env.

**Options stealth / premium:** `option_stealth_init`, `premium_update`, `premium_fallback`, `option_breakeven_candidate`, `option_breakeven`, `option_trailing` (including `action=ratchet_stop`), `option_fast_fail_*`, `option_monitor_stale`, `option_premium_jump`, `option_forced_reeval`, `option_structure_invalidation`, `option_exit`, `options_exit_summary`, `OPTION_PRICE_RESOLUTION_AUDIT`, `OPTION_DEGRADED_LIVE_QUOTE`, plus unified profile/retention telemetry: `OPTIONS_STEALTH_PROFILE_RESOLVED`, `OPTIONS_STEALTH_PROFILE_SOURCE_AUDIT`, `OPTIONS_STEALTH_PREMIUM_STATE`, `OPTIONS_STEALTH_MAX_PNL_DRAWDOWN_EXIT`, `OPTIONS_STEALTH_PROFIT_FLOOR_EXIT`, `OPTIONS_STEALTH_MFE_RETENTION`, `OPTIONS_STEALTH_TIME_EXIT`, `OPTIONS_STEALTH_NO_PROGRESS_EXIT`, `OPTIONS_STEALTH_TRAIL_EXIT`.

## Hardening Features Added

High-impact protections now included:

- **Dual-path confirmation**  
  Entries can execute from immediate breakout confirmation or from a bounded pullback-continuation window when structure remains valid.

- **Break/continuation scoring (anti-overfit)**  
  Pre-execution quality is summarized by score telemetry rather than layered micro-vetoes:
  - `TRENDLINE_SCORE_SUMMARY` (`structure_quality_score`, `break_quality_score`, `continuation_quality_score`, `combined_score`, `classification`, `executor_called`, `final_decision`).
  Single body/distance/expected-move metrics are advisory-first; hard blocks are reserved for catastrophic/invalid/chop/stale/liquidity paths.

- **Break-to-entry max delay**  
  Explicit timeout for late confirmations (`late_confirmation`).

- **No-trade zone cutoff**  
  New trendline entries stop after **`TRENDLINE_NO_NEW_ENTRIES_AFTER_PT`** (Pacific `HH:MM`, default `11:30`); monitoring/exits continue.

- **Chop detection filter**  
  Invalidates structures with excessive recent trendline crosses or too-small recent range vs ORB range.

- **Entry distance anti-chase guard**  
  Rejects entries when price is too far from trendline at emission time.

- **Missed move logging**  
  Captures opportunity-cost telemetry for tuning strictness vs looseness.

## EOD Metrics

Trendline EOD now includes:

- total candidates
- trendlines built
- breaks detected
- momentum confirmations
- executed trades
- invalidated setups
- expired setups
- realized PnL and win rate
- average build->break and break->confirmation timing
- **% candidates that never broke** (new)

## Cross-Path Analytics Tags

For path comparison, canonical labels are:

- ORB ETF: `strategy_name=easyORB_ETF`, `entry_type=time_based_730`
- ORB 0DTE: `strategy_name=easyORB_0DTE`, `entry_type=time_based_730`
- Trendline 0DTE: `strategy_name=easyTrendline_0DTE`, `entry_type=trendline_break`

## Current Limitations (Intentional for First Integration)

- **Synthetic / degraded option marks (exit path):** When chain quotes are **`synthetic_modeled`** / **`non_exit_grade_mark`**, stealth may hold exits (`TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD`) even when underlying has moved favorably — separate from May 15 **entry** calibration (see May 15 session **MELI** monitor forensics). Exit-path relief is a follow-up track, not part of the May 15 impulse emit pass.
- **ORB snapshot reload**: persisted `ORBData` without `orb_high_extreme_ts` / `orb_low_extreme_ts` falls back to **`capture_time`** for Trendline anchor-one time until a fresh intraday capture repopulates extremes.
- **7:30 structure data** is synthesized from ORB-timed intraday + equity quotes + optional prefetch/7:00 map—not a full broker minute history (by design under broker-only constraints).
- **Option marks** depend on E*TRADE chain data (`mid_price` / bid / ask / last). When the options API is unavailable or the contract is missing from the chain response, the engine uses the **modeled proxy** (not a full vol surface or tick-level Greeks).
- **Watch loop** uses quote-derived bars; `true_bar_based` hold is only as reliable as the bar cadence of your feed.
- Trendline execution remains demo-simulated in the current deployment mode; option-chain/quote inputs are broker-live for selection/monitoring fidelity in both Demo and Live data policy.
- **Feature log** is local JSONL; rows for exits prefer **`exit_diagnostics`** from stealth when present.
- **ORB ETF** `prime_stealth_trailing_tp.py` is a separate equity stealth brain; Trendline does not change it. **ORB 0DTE** shares the same **`prime_options_stealth_trailing_tp`** engine as Trendline; May 15 **order-time** execution policy (limits, urgency exits) is shared via **`options_trading_executor`** / **`execution_routing`** when smart execution is enabled.

## Calibration Plan (Profitability Tuning)

The strategy is intentionally instrumentation-heavy so it can be tuned after multiple live sessions.

**May 15 desk calibration (local repo; deploy pending):** Live **MELI** (false-positive impulse CALL) vs **NBIS** (false-negative PUT block) drove the acceptance-first impulse pass documented in the header. Composite entry score includes a small **`persistence_bonus`** (continuation × slope; no new body-ratio vetoes). After deploy, grep **`TRENDLINE_POST_BREAK_ACCEPTANCE`**, **`survival_defer_requires_acceptance`**, and compare **`body_ratio_vs_prev`** vs **`body_ratio_vs_avg`** on break bars. Expected directional outcome: fewer MELI-class fake breakouts; more NBIS-class continuation breakdowns when post-break acceptance and 2-of-3 momentum agree.

Recommended calibration process:

1. **Collect 10-20 sessions first**  
   Do not aggressively retune on 1-2 sessions.

2. **Segment by path tags**  
   Compare by:
   - `strategy_name=easyTrendline_0DTE`
   - `entry_type=trendline_break`
   against ORB ETF / ORB 0DTE paths.

3. **Review key rejection buckets**  
   Count reason codes:
   - `failed_hold`
   - `no_post_break_structure`
   - `momentum_too_weak`
   - `late_confirmation`
   - `retrace_violation`

4. **Tune in this order**
   - **Level A** hold and gates: `TRENDLINE_CONFIRM_SECONDS`, `TRENDLINE_REQUIRE_HOLD_AFTER_BREAK`, `TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK`, `TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE`, expansion keys, `TRENDLINE_FAST_PATH_ENABLED`
   - **Level A** break / body floors: `TRENDLINE_BREAK_DISTANCE_MIN`, `TRENDLINE_BODY_RATIO_MIN_STRONG` / `_WEAK` (keep aligned with Prime override floors)
   - **Loader-backed** momentum / chop / hold bars / min break % / ATR: keys in **`_TRENDLINE_MAIN_ENV_KEYS`** (`trendline_config_loader.py`) — prefer editing **`configs/Trendline0DTE.env`** before changing dataclass defaults
   - **Level F** (`DEFAULT_TRENDLINE_ENGINE_INTERNALS`) only for internals **not** mirrored on `TrendlineConfig`
   - Anti-chase and timing: `TRENDLINE_MAX_DISTANCE_FROM_BREAK_PCT`, `TRENDLINE_MAX_BREAK_TO_CONFIRM_MINUTES`, **`TRENDLINE_NO_NEW_ENTRIES_AFTER_PT`**
   - **Level B** contract band + **Level C** `TRENDLINE_DELTA_TOLERANCE`; OTM/lotto via **Level E** defaults unless loader extended
   - Stealth profit capture (`OPTION_STEALTH_*`, **`OPTION_STEALTH_TLINE_*`**, `TRENDLINE_OPTION_SENSITIVITY`)
   - **Level D** caps and build budgets if execution or 7:30 coverage is the bottleneck

5. **Track opportunity cost**  
   Use `TRENDLINE_MISSED_OPPORTUNITY` logs (`max_move_pct_after_break`, `time_to_peak_bars`) to identify whether rules are too strict.

6. **Promote only stable improvements**  
   Keep changes small, test for several sessions, and verify win rate + expectancy improvements before further tuning.

## Summary

Easy Trendline is integrated as a **structure-first**, post-7:30, event-driven **0DTE long premium** path with:

- **pre-7:30** setup selection: **outside ORB at cutoff** → **`cutoff_to_farthest_extreme`**; **inside ORB** → **`classify_orb_test_failure`** pins geometry when actionable; otherwise **MSE** / tie-break among ascending support vs descending resistance and explicit call/put mapping; production path attaches **selector-built** lines only (`trendline_line_source` and snapshot **`source`** = **`selector_built`**; legacy **`prebuilt`** values normalize to **`selector_built`**)
- **time- or bar-based hold**, then **normal-path OR** among expansion / continuation / structure (see **`TRENDLINE_NORMAL_GATE_OR`**), then momentum; post-break **survival window** and **`TRENDLINE_DECISION_SNAPSHOT`** (**≤ one row per candidate per bar**) for deploy analytics
- **delta / OTM-aware** contract selection and rejection logging
- **TrendlineOptionsStealthEngine** for premium-centric exits (delta-bucket BE, **Trendline `OPTION_STEALTH_TLINE_*` overlays**, trailing, invalidation, EOD)—without altering ORB ETF or ORB 0DTE engines
- dedicated demo ledger, alerts, dedupe/invalidation hardening, and JSONL telemetry for research vs ORB 0DTE

---

## Appendix: `configs/Trendline0DTE.env` snapshot

**Purpose:** Single place to diff Trendline path defaults committed in git. **Merge context:** This file is loaded **after** `Data.env`, `Shared.env`, `ORBSO.env`, and `ORB0DTE.env`; later files win on duplicate keys (see `configs/README.md`). Shared-layer keys are documented as references at the top of `Trendline0DTE.env`—do not duplicate them here.

**Refresh rule:** When you change `configs/Trendline0DTE.env`, update this block so the doc stays a faithful inventory.

*Snapshot date: May 6, 2026 (matches `configs/Trendline0DTE.env` in repo unless noted). **May 13:** substantive **`OPTION_STEALTH_TLINE_*`** updates may land in **`configs/Shared.env`** (canonical per in-file comments); refresh this appendix from both env files when auditing exits.*

```env
# --- 1) Path toggle & scope ---
ENABLE_TRENDLINE_STRATEGY=true
TARGET_TRENDLINE_ACTIVE=4
TRENDLINE_USE_FULL_0DTE_LIST=true

# --- 2) ORB & session timing ---
TRENDLINE_NO_NEW_ENTRIES_AFTER_PT=11:30
TRENDLINE_CONFIRM_SECONDS=10
TRENDLINE_HOLD_MODE=time_based
TRENDLINE_HOLD_BARS_REQUIRED=2
TRENDLINE_HOLD_BAR_INTERVAL=1m
TRENDLINE_REQUIRE_HOLD_AFTER_BREAK=true
TRENDLINE_MAX_BREAK_TO_CONFIRM_MINUTES=10
TRENDLINE_CONFIRMATION_WINDOW_BARS=1

# --- 3) Eligibility, scoring & filters ---
TRENDLINE_BREAK_DISTANCE_MIN=0.0015
TRENDLINE_BODY_RATIO_MIN_STRONG=0.5
TRENDLINE_BODY_RATIO_MIN_WEAK=0.35
TRENDLINE_FAST_PATH_ENABLED=true
TRENDLINE_MIN_BREAK_PCT=0.001
TRENDLINE_USE_ATR_BREAK=true
TRENDLINE_ATR_BREAK_MULTIPLIER=0.25
TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES=3
TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK=false
TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE=false
TRENDLINE_POST_BREAK_STRUCTURE_LOOKBACK_BARS=3
TRENDLINE_MAX_BREAK_TO_HOLD_RETRACE_PCT=0.40
TRENDLINE_MIN_CONTINUATION_DISTANCE_PCT=0.0005
TRENDLINE_POST_CONTINUE_SETTLE_BARS=1
TRENDLINE_POST_CONTINUE_CHOP_BOX_GATE_ENABLED=true
TRENDLINE_POST_CONTINUE_FAST_FOLLOWTHROUGH_BARS=8
TRENDLINE_POST_CONTINUE_FAST_EXTENSION_PCT=0.0009
TRENDLINE_POST_CONTINUE_BOX_BREAK_BUFFER_PCT=0.0002
TRENDLINE_POST_CONTINUE_CHOP_MIN_PRIOR_BARS=3
TRENDLINE_MAX_DISTANCE_FROM_BREAK_PCT=0.02
TRENDLINE_ALLOW_SECOND_BREAK_ATTEMPT=false
TRENDLINE_MIN_FOLLOWTHROUGH_BARS=2
TRENDLINE_MIN_VELOCITY_PCT=0.0010
TRENDLINE_RANGE_EXPANSION_MULTIPLIER=1.25
TRENDLINE_MIN_BREAK_QUALITY_SCORE=0.38
TRENDLINE_REARM_ENABLED=true
TRENDLINE_REARM_MAX_CHECKS=2
TRENDLINE_REARM_MAX_MINUTES=10
TRENDLINE_REARM_ALLOWED_REASONS_CSV=no_follow_through,strict_body_ratio,reversal_candle,break_quality,insufficient_move_potential
TRENDLINE_REANCHOR_ENABLED=true
TRENDLINE_REANCHOR_MINUTES=20
TRENDLINE_REANCHOR_MIN_TOUCHES=3
TRENDLINE_REGIME_CHOPPY_OVERLAP_MIN=0.55
TRENDLINE_REGIME_TREND_DIRECTIONAL_MIN=0.58
TRENDLINE_REGIME_MIN_AVG_RANGE_PCT=0.0012
TRENDLINE_REGIME_CHOPPY_BODY_RATIO_MIN=1.10
TRENDLINE_REGIME_CHOPPY_MIN_FOLLOWTHROUGH_BARS=2
TRENDLINE_IMPULSE_ENABLED=true
TRENDLINE_IMPULSE_MIN_BODY_RATIO=1.35
TRENDLINE_IMPULSE_BREAK_BODY_RATIO_MIN=0.70
TRENDLINE_IMPULSE_CONFIRM_NEXT_CANDLE=true
TRENDLINE_RETEST_ENABLED=true
TRENDLINE_RETEST_MAX_CHECKS=2
TRENDLINE_RETEST_MAX_MINUTES=8
TRENDLINE_RETEST_BODY_RATIO_MIN=0.60
TRENDLINE_SLOW_TREND_ENABLED=true
TRENDLINE_SLOW_TREND_MIN_CANDLES=3
TRENDLINE_SLOW_TREND_MAX_CANDLES=6
TRENDLINE_SLOW_TREND_CONSISTENCY_MIN=0.67
TRENDLINE_SLOW_TREND_CUM_BREAK_MOVE_MIN=0.0018
TRENDLINE_EARLY_ENTRY_ENABLED=true
TRENDLINE_EARLY_ENTRY_BODY_RATIO_MIN=0.8
TRENDLINE_EARLY_ENTRY_SIZE_MULTIPLIER=0.50
TRENDLINE_EXPANSION_NET_MOVE_RATIO_MIN=0.35
TRENDLINE_EXPANSION_OVERLAP_MAX=0.65
TRENDLINE_EXPANSION_BREAK_LEVEL_RETURN_MAX=2
TRENDLINE_POST_BREAK_SURVIVAL_BARS=3
TRENDLINE_STRICT_MIN_BREAK_DISTANCE=0.0015
TRENDLINE_BODY_RATIO_MIN_STRICT=0.50
TRENDLINE_MIN_EXPECTED_MOVE_PCT_DEFAULT=0.0006
TRENDLINE_MIN_EXPECTED_MOVE_PCT_SLOW=0.0004
TRENDLINE_MIN_EXPECTED_MOVE_PCT_IMPULSE=0.0010
TRENDLINE_HIGH_PRESSURE_TOUCH_COUNT=3
TRENDLINE_PRESSURE_SCORE_MIN=2.0
TRENDLINE_TOUCH_TOLERANCE_PCT=0.0012
TRENDLINE_MIN_TOUCH_BAR_GAP=1
TRENDLINE_MIN_STRUCTURE_BARS=3
TRENDLINE_MIN_STRUCTURE_SECONDS=90
TRENDLINE_MAX_ACTIVE_MINUTES=180
TRENDLINE_MISSED_WIN_MOVE_PCT=0.005
TRENDLINE_BAD_ENTRY_MAX_FAVORABLE_PCT=0.002
TRENDLINE_BAD_ENTRY_DRAWDOWN_PCT=0.003
TRENDLINE_BUILD_TIME_PT=07:30
TRENDLINE_EXPIRATION_TIME_PT=12:30
TRENDLINE_STRONG_BREAKOUT_DISTANCE_MULT=1.2
TRENDLINE_BODY_EXPANSION_MULT=1.3
TRENDLINE_CLEAN_BREAKOUT_BYPASS_MOMENTUM=true
TRENDLINE_CHOP_RECENT_BARS=8
TRENDLINE_CHOP_MAX_CROSSES=2
TRENDLINE_CHOP_SMALL_RANGE_VS_ORB_RATIO=0.20
TRENDLINE_SECOND_BREAK_ATTEMPT_MAX_BARS=3
TRENDLINE_CONTINUATION_MAX_BARS=8
TRENDLINE_CONTINUATION_MIN_BARS=2
TRENDLINE_PULLBACK_STRENGTH_THRESHOLD=0.62
TRENDLINE_REARM_BODY_RATIO_THRESHOLD=0.80
TRENDLINE_REARM_MAX_STRUCTURE_VIOLATIONS=2
TRENDLINE_EXTREME_BREAK_THRESHOLD=0.04
TRENDLINE_NEAR_EXTREME_BREAK_THRESHOLD=0.035
TRENDLINE_STRONG_BREAK_THRESHOLD=0.025
TRENDLINE_STRONG_BODY_RATIO=0.72
TRENDLINE_WEAK_BREAK_THRESHOLD=0.015
TRENDLINE_REVERSAL_RECLAIM_MIN_DISTANCE=0.01
TRENDLINE_REVERSAL_MAX_BARS_SINCE_REJECTION=5
TRENDLINE_REVERSAL_EARLY_RECLAIM_BARS=2
TRENDLINE_REVERSAL_CONFIDENCE_BOOST=0.05
TRENDLINE_FAST_PATH_OVERRIDE_ABS_FLOOR=0.010
TRENDLINE_FAST_PATH_FINAL_ABS_FLOOR=0.008
TRENDLINE_STRONG_DIST_OVERRIDE_ABS_FLOOR=0.008
TRENDLINE_TRIPLE_WEAK_CEILING_ABS_FLOOR=0.010
TRENDLINE_COMPOSITE_CLOSE_POS_COMMIT_MIN=0.80
TRENDLINE_TRIPLE_WEAK_CLOSE_POS_MAX=0.70
TRENDLINE_MIN_DRIFT_DISPLACEMENT=0.003
TRENDLINE_REQUIRE_DRIFT_CONFIRM=false
TRENDLINE_REANCHOR_LOOKBACK_BARS=30
TRENDLINE_RETEST_LINE_BUFFER_PCT=0.0007
TRENDLINE_ACCEPTANCE_MIN_BEYOND_CLOSES=2
TRENDLINE_ACCEPTANCE_MAX_RECLAIMS=2
TRENDLINE_MIN_SECONDS_AFTER_BREAK=0
TRENDLINE_MAX_SECONDS_AFTER_BREAK=0

# --- 4) Position sizing & capital ---
TRENDLINE_MAX_TRADES_PER_DAY=5
TRENDLINE_SLOT_COUNT=5
TRENDLINE_MAX_OPEN_POSITIONS=5
TRENDLINE_ACCOUNT_ALLOCATION_PCT=90
TRENDLINE_DEMO_STARTING_BALANCE=5000.0
TRENDLINE_MAX_POSITION_PCT=0.12

# --- 5) Instruments, spreads & liquidity ---
TRENDLINE_OPTION_DELTA_MIN=0.20
TRENDLINE_OPTION_DELTA_MAX=0.35
TRENDLINE_OPTION_STRIKE_MODE=otm_1_to_2
TRENDLINE_OPTION_LOTTO_MODE=true
TRENDLINE_OPTION_MAX_SPREAD_PCT=0.30
TRENDLINE_OPTION_MIN_OPEN_INTEREST=100
TRENDLINE_OPTION_MIN_VOLUME=50
TRENDLINE_OPTION_SENSITIVITY=2.5

# --- 6) Execution gating & runtime checks ---
TRENDLINE_MIN_EXPECTED_MOVE_PCT=0.0015
TRENDLINE_DATA_CHUNK_SIZE=25
TRENDLINE_MAX_INTRADAY_BATCH_CALLS_PER_BUILD=8
TRENDLINE_MAX_QUOTE_BATCH_CALLS_PER_BUILD=8
TRENDLINE_MAX_SYMBOLS_PER_BUILD=200
TRENDLINE_ENABLE_BUILD_DEGRADATION=true
TRENDLINE_MAX_BUILD_DURATION_MS=0

# --- 7) Exits, stealth, routing & monitoring ---
TRENDLINE_EXIT_MIN_HOLD_SECONDS=900
TRENDLINE_BREAKEVEN_ACTIVATE_PCT=0.25
TRENDLINE_BREAKEVEN_OFFSET_PCT=0.02
TRENDLINE_TRAIL_ACTIVATE_PCT=0.40
TRENDLINE_TRAIL_GIVEBACK_PCT=0.20
TRENDLINE_IMPULSE_BREAKEVEN_ACTIVATE_PCT=0.20
TRENDLINE_IMPULSE_TRAIL_ACTIVATE_PCT=0.35
TRENDLINE_IMPULSE_TRAIL_GIVEBACK_PCT=0.18
TRENDLINE_SLOW_TREND_BREAKEVEN_ACTIVATE_PCT=0.30
TRENDLINE_SLOW_TREND_TRAIL_ACTIVATE_PCT=0.50
TRENDLINE_SLOW_TREND_TRAIL_GIVEBACK_PCT=0.35
TRENDLINE_RETEST_BREAKEVEN_ACTIVATE_PCT=0.25
TRENDLINE_RETEST_TRAIL_ACTIVATE_PCT=0.40
TRENDLINE_RETEST_TRAIL_GIVEBACK_PCT=0.30
OPTION_STEALTH_TLINE_BE_TRIGGER_MULT=0.50
OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT=0.82
OPTION_STEALTH_TLINE_PROFIT_LOCK_TRIGGER_PCT=0.10
OPTION_STEALTH_TLINE_PROFIT_LOCK_PCT=0.012
OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT=0.10
OPTION_STEALTH_TLINE_IMPULSE_TP_TARGET_PCT=0.50
OPTION_STEALTH_TLINE_IMPULSE_TRAILING_TRIGGER_PCT=0.25
OPTION_STEALTH_TLINE_IMPULSE_TRAILING_PCT=0.20
OPTION_STEALTH_TLINE_IMPULSE_TIME_EXIT_MINUTES=12
OPTION_STEALTH_TLINE_IMPULSE_NO_PROGRESS_EXIT_MINUTES=8
OPTION_STEALTH_TLINE_EARLY_TRAILING_TRIGGER_PCT=0.30
OPTION_STEALTH_TLINE_EARLY_TRAILING_PCT=0.24
OPTION_STEALTH_TLINE_EARLY_TIME_EXIT_MINUTES=30
TRENDLINE_POSITION_MONITOR_INTERVAL_SEC=7
TRENDLINE_MONITOR_ALERT_INTERVAL_SEC=300
TRENDLINE_MONITOR_ALERT_ENABLED=false
TRENDLINE_MONITOR_ALERT_ONLY_ON_CHANGE=true
TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_ENABLED=true
TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_FAIL_OPEN=true
TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_NAMESPACE=trendline_monitor_alerts
TRENDLINE_GLOBAL_DAILY_CAP_ENABLED=true
TRENDLINE_GLOBAL_DAILY_CAP_FAIL_OPEN=true
TRENDLINE_GLOBAL_DAILY_CAP_NAMESPACE=trendline_daily_cap
TRENDLINE_WATCH_STATUS_LOG_SEC=120
TRENDLINE_WATCH_NEARLINE_LOG_SEC=180
TRENDLINE_WATCH_IDLE_LOG_SEC=300
TRENDLINE_WATCH_EMPTY_QUOTES_LOG_SEC=60
TRENDLINE_BUILD_ANCHOR_LOG_MAX=15
TRENDLINE_CONFIRM_PENDING_LOG_SEC=120
```

