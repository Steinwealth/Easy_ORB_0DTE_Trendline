#!/usr/bin/env python3
"""
Load TrendlineConfig / option selection from merged app config (get_config_value).

Every ``TRENDLINE_*`` key consumed here overrides dataclass defaults; defaults used when
the key is absent are logged as ``TRENDLINE_CONFIG_DEFAULT_USED``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import fields
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Tuple

from .trendline_models import TrendlineConfig, TrendlineOptionSelectionConfig

log = logging.getLogger(__name__)

_DEPRECATED_TRENDLINE_ENV_KEYS: FrozenSet[str] = frozenset(
    {
        "TRENDLINE_MIN_HOLD_SECONDS",
        "TRENDLINE_MIN_ENTRY_DISTANCE",
        "TRENDLINE_IMMEDIATE_BREAK_MIN_DISTANCE",
        "TRENDLINE_IMPULSE_MIN_BREAK_DISTANCE",
    }
)

_LOGGED_DEPRECATED: set[str] = set()

# Keys read by load_trendline_config_from_env (for unused-env scan).
_TRENDLINE_MAIN_ENV_KEYS: Tuple[str, ...] = (
    "TRENDLINE_BUILD_TIME_PT",
    "TRENDLINE_EXPIRATION_TIME_PT",
    "TRENDLINE_MIN_BREAK_PCT",
    "TRENDLINE_USE_ATR_BREAK",
    "TRENDLINE_ATR_BREAK_MULTIPLIER",
    "TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES",
    "TRENDLINE_BREAK_DISTANCE_MIN",
    "TRENDLINE_BODY_RATIO_MIN_STRONG",
    "TRENDLINE_BODY_RATIO_MIN_WEAK",
    "TRENDLINE_FAST_PATH_ENABLED",
    "TRENDLINE_CONFIRM_SECONDS",
    "TRENDLINE_REQUIRE_HOLD_AFTER_BREAK",
    "TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK",
    "TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE",
    "TRENDLINE_EXPANSION_NET_MOVE_RATIO_MIN",
    "TRENDLINE_EXPANSION_OVERLAP_MAX",
    "TRENDLINE_EXPANSION_BREAK_LEVEL_RETURN_MAX",
    "TRENDLINE_EXPANSION_IMPULSE_MFE_ENABLED",
    "TRENDLINE_EXPANSION_IMPULSE_BODY_STRONG",
    "TRENDLINE_EXPANSION_IMPULSE_SURVIVAL_MIN_BARS",
    "TRENDLINE_EXPANSION_IMPULSE_SURVIVAL_MIN_SECONDS",
    "TRENDLINE_MAX_BREAK_TO_CONFIRM_MINUTES",
    "TRENDLINE_MAX_DISTANCE_FROM_BREAK_PCT",
    "TRENDLINE_NO_NEW_ENTRIES_AFTER_PT",
    "TRENDLINE_STRONG_BREAKOUT_DISTANCE_MULT",
    "TRENDLINE_BODY_EXPANSION_MULT",
    "TRENDLINE_CLEAN_BREAKOUT_BYPASS_MOMENTUM",
    "TRENDLINE_CONFIRMATION_WINDOW_BARS",
    "TRENDLINE_MIN_FOLLOWTHROUGH_BARS",
    "TRENDLINE_MIN_VELOCITY_PCT",
    "TRENDLINE_RANGE_EXPANSION_MULTIPLIER",
    "TRENDLINE_MIN_BREAK_QUALITY_SCORE",
    "TRENDLINE_CHOP_RECENT_BARS",
    "TRENDLINE_CHOP_MAX_CROSSES",
    "TRENDLINE_CHOP_SMALL_RANGE_VS_ORB_RATIO",
    "TRENDLINE_HOLD_BARS_REQUIRED",
    "TRENDLINE_HOLD_MODE",
    "TRENDLINE_HOLD_BAR_INTERVAL",
    "TRENDLINE_POST_BREAK_STRUCTURE_LOOKBACK_BARS",
    "TRENDLINE_MAX_BREAK_TO_HOLD_RETRACE_PCT",
    "TRENDLINE_MIN_CONTINUATION_DISTANCE_PCT",
    "TRENDLINE_POST_CONTINUE_SETTLE_BARS",
    "TRENDLINE_POST_CONTINUE_CHOP_BOX_GATE_ENABLED",
    "TRENDLINE_POST_CONTINUE_FAST_FOLLOWTHROUGH_BARS",
    "TRENDLINE_POST_CONTINUE_FAST_EXTENSION_PCT",
    "TRENDLINE_POST_CONTINUE_BOX_BREAK_BUFFER_PCT",
    "TRENDLINE_POST_CONTINUE_CHOP_MIN_PRIOR_BARS",
    "TRENDLINE_ALLOW_SECOND_BREAK_ATTEMPT",
    "TRENDLINE_SECOND_BREAK_ATTEMPT_MAX_BARS",
    "TRENDLINE_CONFIRM_PENDING_LOG_SEC",
    "TRENDLINE_FALSE_BREAK_GATE_LOG_THROTTLE_SEC",
    "TRENDLINE_FUNNEL_SUMMARY_INTERVAL_SEC",
    "TRENDLINE_CONTINUATION_MAX_BARS",
    "TRENDLINE_CONTINUATION_MIN_BARS",
    "TRENDLINE_PULLBACK_STRENGTH_THRESHOLD",
    "TRENDLINE_REARM_ENABLED",
    "TRENDLINE_REARM_MAX_CHECKS",
    "TRENDLINE_REARM_MAX_MINUTES",
    "TRENDLINE_REARM_ALLOWED_REASONS_CSV",
    "TRENDLINE_REARM_BODY_RATIO_THRESHOLD",
    "TRENDLINE_EXTREME_BREAK_THRESHOLD",
    "TRENDLINE_NEAR_EXTREME_BREAK_THRESHOLD",
    "TRENDLINE_STRONG_BREAK_THRESHOLD",
    "TRENDLINE_STRONG_BODY_RATIO",
    "TRENDLINE_WEAK_BREAK_THRESHOLD",
    "TRENDLINE_REVERSAL_RECLAIM_MIN_DISTANCE",
    "TRENDLINE_REVERSAL_MAX_BARS_SINCE_REJECTION",
    "TRENDLINE_REVERSAL_EARLY_RECLAIM_BARS",
    "TRENDLINE_REVERSAL_CONFIDENCE_BOOST",
    "TRENDLINE_REGIME_CHOPPY_OVERLAP_MIN",
    "TRENDLINE_REGIME_TREND_DIRECTIONAL_MIN",
    "TRENDLINE_REGIME_MIN_AVG_RANGE_PCT",
    "TRENDLINE_REGIME_CHOPPY_BODY_RATIO_MIN",
    "TRENDLINE_REGIME_CHOPPY_MIN_FOLLOWTHROUGH_BARS",
    "TRENDLINE_IMPULSE_ENABLED",
    "TRENDLINE_IMPULSE_MIN_BODY_RATIO",
    "TRENDLINE_IMPULSE_CONFIRM_NEXT_CANDLE",
    "TRENDLINE_SLOW_TREND_ENABLED",
    "TRENDLINE_SLOW_TREND_MIN_CANDLES",
    "TRENDLINE_SLOW_TREND_MAX_CANDLES",
    "TRENDLINE_SLOW_TREND_CONSISTENCY_MIN",
    "TRENDLINE_SLOW_TREND_CUM_BREAK_MOVE_MIN",
    "TRENDLINE_EARLY_ENTRY_ENABLED",
    "TRENDLINE_EARLY_ENTRY_BODY_RATIO_MIN",
    "TRENDLINE_EARLY_ENTRY_SIZE_MULTIPLIER",
    "TRENDLINE_IMPULSE_BREAK_BODY_RATIO_MIN",
    "TRENDLINE_RETEST_ENABLED",
    "TRENDLINE_RETEST_MAX_CHECKS",
    "TRENDLINE_RETEST_MAX_MINUTES",
    "TRENDLINE_RETEST_BODY_RATIO_MIN",
    "TRENDLINE_MIN_EXPECTED_MOVE_PCT",
    "TRENDLINE_MIN_EXPECTED_MOVE_PCT_SLOW",
    "TRENDLINE_MIN_EXPECTED_MOVE_PCT_IMPULSE",
    "TRENDLINE_MIN_EXPECTED_MOVE_PCT_DEFAULT",
    "TRENDLINE_TOUCH_TOLERANCE_PCT",
    "TRENDLINE_HIGH_PRESSURE_TOUCH_COUNT",
    "TRENDLINE_PRESSURE_SCORE_MIN",
    "TRENDLINE_MIN_STRUCTURE_BARS",
    "TRENDLINE_MIN_STRUCTURE_SECONDS",
    "TRENDLINE_POST_BREAK_SURVIVAL_BARS",
    "TRENDLINE_MAX_ACTIVE_MINUTES",
    "TRENDLINE_MISSED_WIN_MOVE_PCT",
    "TRENDLINE_BAD_ENTRY_MAX_FAVORABLE_PCT",
    "TRENDLINE_BAD_ENTRY_DRAWDOWN_PCT",
    "TRENDLINE_MIN_TOUCH_BAR_GAP",
    "TRENDLINE_FAST_PATH_OVERRIDE_ABS_FLOOR",
    "TRENDLINE_FAST_PATH_FINAL_ABS_FLOOR",
    "TRENDLINE_STRONG_DIST_OVERRIDE_ABS_FLOOR",
    "TRENDLINE_TRIPLE_WEAK_CEILING_ABS_FLOOR",
    "TRENDLINE_COMPOSITE_CLOSE_POS_COMMIT_MIN",
    "TRENDLINE_TRIPLE_WEAK_CLOSE_POS_MAX",
    "TRENDLINE_MIN_DRIFT_DISPLACEMENT",
    "TRENDLINE_REQUIRE_DRIFT_CONFIRM",
    "TRENDLINE_REANCHOR_ENABLED",
    "TRENDLINE_REANCHOR_MINUTES",
    "TRENDLINE_REANCHOR_MIN_TOUCHES",
    "TRENDLINE_REANCHOR_LOOKBACK_BARS",
    "TRENDLINE_RETEST_LINE_BUFFER_PCT",
    "TRENDLINE_ACCEPTANCE_MIN_BEYOND_CLOSES",
    "TRENDLINE_ACCEPTANCE_MAX_RECLAIMS",
    "TRENDLINE_REARM_MAX_STRUCTURE_VIOLATIONS",
    "TRENDLINE_STRICT_MIN_BREAK_DISTANCE",
    "TRENDLINE_BODY_RATIO_MIN_STRICT",
    "TRENDLINE_MIN_SECONDS_AFTER_BREAK",
    "TRENDLINE_MAX_SECONDS_AFTER_BREAK",
    "TRENDLINE_NORMAL_ENTRY_REQUIRE_CONFIRMATION",
    "TRENDLINE_FAST_PATH_REQUIRE_STRONG_BREAK",
    "TRENDLINE_FAST_PATH_REQUIRE_DISTANCE_INCREASING",
    "TRENDLINE_FAST_PATH_REQUIRE_MOMENTUM_AGREEMENT",
    "TRENDLINE_FAST_PATH_REQUIRE_NO_RECLAIM",
    "TRENDLINE_FAST_PATH_MIN_BODY_RATIO",
    "TRENDLINE_FAST_PATH_MIN_BREAK_DISTANCE",
    "TRENDLINE_LOCAL_CONTINUATION_SURVIVAL_EXTRA_BARS",
    "TRENDLINE_LOCAL_CONTINUATION_SURVIVAL_SEC",
    "TRENDLINE_FALSE_BREAK_BYPASS_MIN_EM_MULT",
    "TRENDLINE_FAST_PATH_WEAK_BODY_CATASTROPHIC_ONLY",
    "TRENDLINE_ENTRY_SCORE_MIN_IMPULSE",
    "TRENDLINE_ENTRY_SCORE_MIN_CONTINUATION",
    "TRENDLINE_ENTRY_SCORE_MIN_EXHAUSTION",
    "TRENDLINE_SCORE_WEIGHT_STRUCTURE",
    "TRENDLINE_SCORE_WEIGHT_DISTANCE_INCREASING",
    "TRENDLINE_SCORE_WEIGHT_RECLAIM_HOLD",
    "TRENDLINE_SCORE_WEIGHT_CONTINUATION_SLOPE",
    "TRENDLINE_SCORE_WEIGHT_CONTINUATION_VELOCITY",
    "TRENDLINE_SCORE_WEIGHT_EXPECTED_MOVE",
    "TRENDLINE_SCORE_WEIGHT_BODY",
    "TRENDLINE_SCORE_WEIGHT_EXPANSION",
    "TRENDLINE_SCORE_WEIGHT_FOLLOWTHROUGH",
    "TRENDLINE_SCORE_PENALTY_CHOP",
    "TRENDLINE_SCORE_PENALTY_OPPOSITE_CANDLE",
    "TRENDLINE_SCORE_PENALTY_RECLAIM",
    "TRENDLINE_BOUNCEBACK_RECLAIM_MAX_BARS",
    "TRENDLINE_ENTRY_SCORE_STALE_SECONDS",
    "TRENDLINE_ENTRY_EM_COLLAPSE_RATIO",
    "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_IMPULSE",
    "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_DRIFT",
    "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_EXHAUSTION",
    "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_IMPULSE",
    "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_DRIFT",
    "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_EXHAUSTION",
)

# Keys read by Prime / option-stealth / main.py but not by ``load_trendline_config_from_env``;
# included so ``warn_unused_trendline_related_env_keys`` does not false-positive.
_TRENDLINE_CONSUMED_OUTSIDE_LOADER: Tuple[str, ...] = (
    "TRENDLINE_ENTRY_PROFILE",
    "TRENDLINE_ACCOUNT_ALLOCATION_PCT",
    "TRENDLINE_BUILD_ANCHOR_LOG_MAX",
    "TRENDLINE_DEMO_STARTING_BALANCE",
    "TRENDLINE_DATA_CHUNK_SIZE",
    "TRENDLINE_ENABLE_BUILD_DEGRADATION",
    "TRENDLINE_EXIT_MIN_HOLD_SECONDS",
    "TRENDLINE_GLOBAL_DAILY_CAP_ENABLED",
    "TRENDLINE_GLOBAL_DAILY_CAP_FAIL_OPEN",
    "TRENDLINE_GLOBAL_DAILY_CAP_NAMESPACE",
    "TRENDLINE_IMPULSE_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_IMPULSE_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_IMPULSE_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_MAX_BUILD_DURATION_MS",
    "TRENDLINE_MAX_INTRADAY_BATCH_CALLS_PER_BUILD",
    "TRENDLINE_MAX_OPEN_POSITIONS",
    "TRENDLINE_MAX_QUOTE_BATCH_CALLS_PER_BUILD",
    "TRENDLINE_MAX_SYMBOLS_PER_BUILD",
    "TRENDLINE_MAX_TRADES_PER_DAY",
    "TRENDLINE_MIN_DIAGNOSTIC_HOLD_SECONDS",
    "TRENDLINE_MONITOR_ALERT_ENABLED",
    "TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_ENABLED",
    "TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_FAIL_OPEN",
    "TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_NAMESPACE",
    "TRENDLINE_MONITOR_ALERT_INTERVAL_SEC",
    "TRENDLINE_MONITOR_ALERT_ONLY_ON_CHANGE",
    "TRENDLINE_OPTION_FORCE_EXIT_NO_DATA_SECONDS",
    "TRENDLINE_OPTION_NO_DATA_GRACE_SECONDS",
    "TRENDLINE_OPTION_NO_DATA_FAVORABLE_UNDERLYING_MOVE_PCT",
    "TRENDLINE_OPTION_REQUIRE_EXIT_GRADE_BEFORE_FORCE_EXIT",
    "TRENDLINE_OPTION_SENSITIVITY",
    "TRENDLINE_POSITION_MONITOR_INTERVAL_SEC",
    "TRENDLINE_POSITION_MONITOR_INTERVAL_SEC_DYNAMIC",
    "TRENDLINE_RETEST_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_RETEST_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_RETEST_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_SLOT_COUNT",
    "TRENDLINE_SLOW_TREND_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_SLOW_TREND_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_SLOW_TREND_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_USE_FULL_0DTE_LIST",
    "TRENDLINE_WATCH_EMPTY_QUOTES_LOG_SEC",
    "TRENDLINE_WATCH_IDLE_LOG_SEC",
    "TRENDLINE_WATCH_NEARLINE_LOG_SEC",
    "TRENDLINE_WATCH_STATUS_LOG_SEC",
    "TRENDLINE_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_BREAKEVEN_OFFSET_PCT",
)

_TRENDLINE_OPTION_ENV_KEYS: Tuple[str, ...] = (
    "TRENDLINE_OPTION_DELTA_MIN",
    "TRENDLINE_OPTION_DELTA_MAX",
    "TRENDLINE_OPTION_MAX_SPREAD_PCT",
    "TRENDLINE_OPTION_STRIKE_MODE",
    "TRENDLINE_OPTION_LOTTO_MODE",
    "TRENDLINE_OPTION_MIN_OPEN_INTEREST",
    "TRENDLINE_OPTION_MIN_VOLUME",
)

_OPTION_STEALTH_TLINE_ENV_KEYS: Tuple[str, ...] = (
    "OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_DEGRADED_EXIT_MIN_DURATION",
    "OPTION_STEALTH_TLINE_MIN_SECONDS_AFTER_BE_ACTIVATION",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_MULT",
    "OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT",
    "OPTION_STEALTH_TLINE_PROFIT_LOCK_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_PROFIT_LOCK_PCT",
    "OPTION_STEALTH_TLINE_BE_DELTA_HIGH_THRESHOLD",
    "OPTION_STEALTH_TLINE_BE_DELTA_MID_THRESHOLD",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_HIGH_DELTA_PCT",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_MID_DELTA_PCT",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_LOW_DELTA_PCT",
    "OPTION_STEALTH_TLINE_MIN_SECONDS_BEFORE_BE",
    "OPTION_STEALTH_TLINE_BASE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_AGGRESSIVE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_AGGRESSIVE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_EXPLOSIVE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_RUNNER_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_EXPLOSIVE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_RUNNER_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MINUTES",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MAX_PNL_PCT",
    "OPTION_STEALTH_TLINE_DEGRADED_NON_EXACT_TIGHTEN_AFTER_SECONDS",
    "OPTION_STEALTH_TLINE_DEGRADED_NON_EXACT_TRAIL_TIGHTEN_SCALE",
    "OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TP_TARGET_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TRAILING_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TIME_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_IMPULSE_NO_PROGRESS_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_EARLY_TRAILING_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_EARLY_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_EARLY_TIME_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_MAX_PNL_PCT",
    "OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_DRAWDOWN_PCT",
    "OPTION_STEALTH_TLINE_RETEST_MFE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_RETEST_MFE_DRAWDOWN_EXIT_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TRAIL_ACTIVATION_CAP_DEGRADED",
    "OPTION_STEALTH_TLINE_UNDERLYING_OPPORTUNITY_PCT",
    "OPTION_STEALTH_TLINE_UNDERLYING_RECLAIM_PROTECT",
    "OPTION_STEALTH_TLINE_MIN_HOLD_BYPASS_MFE_PCT",
    "OPTION_STEALTH_TLINE_MICRO_LOCK_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_BE_RELAX_HWM_WITH_UNDERLYING",
    "OPTION_STEALTH_TLINE_BE_RELAX_HWM_MIN_PNL_PCT",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_SINGLE_RELAX_EXIT_GRADE",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_SINGLE_MAX_MFE_PCT",
    "OPTION_STEALTH_STATE_LOG_SECONDS",
)

ALL_TRENDLINE_CONSUMED_ENV_KEYS: FrozenSet[str] = frozenset(
    _TRENDLINE_MAIN_ENV_KEYS
    + _TRENDLINE_OPTION_ENV_KEYS
    + _OPTION_STEALTH_TLINE_ENV_KEYS
    + _TRENDLINE_CONSUMED_OUTSIDE_LOADER
    + tuple(_DEPRECATED_TRENDLINE_ENV_KEYS)
)


def _warn_deprecated_trendline_env_keys(extra_scan_keys: Iterable[str] = ()) -> None:
    """Emit TRENDLINE_CONFIG_DEPRECATED once per deprecated key still present in os.environ."""
    keys = sorted(_DEPRECATED_TRENDLINE_ENV_KEYS | frozenset(extra_scan_keys))
    for key in keys:
        if key in _LOGGED_DEPRECATED:
            continue
        if key not in os.environ:
            continue
        _LOGGED_DEPRECATED.add(key)
        log.warning("TRENDLINE_CONFIG_DEPRECATED | key=%s | detail=ignored_use_canonical_loader", key)


def warn_unused_trendline_related_env_keys() -> None:
    """Log env keys that look like Trendline tuning but are not read by this package."""
    for key in sorted(os.environ.keys()):
        if not (key.startswith("TRENDLINE_") or key.startswith("OPTION_STEALTH_TLINE_")):
            continue
        if key in ALL_TRENDLINE_CONSUMED_ENV_KEYS:
            continue
        log.warning(
            "TRENDLINE_CONFIG_UNUSED_WARNING | key=%s | detail=present_in_environment_but_not_listed_as_consumed_by_trendline_config_loader",
            key,
        )


def _gv(get_config_value: Callable[[str, Any], Any], key: str, default: Any) -> Any:
    try:
        return get_config_value(key, default)
    except Exception:
        v = os.getenv(key)
        return default if v is None else v


def _raw_present(get_config_value: Callable[[str, Any], Any], key: str) -> bool:
    try:
        v = get_config_value(key, None)
    except Exception:
        v = os.getenv(key)
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def _to_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _to_int(x: Any, default: int) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def _to_bool(x: Any, default: bool) -> bool:
    if isinstance(x, bool):
        return x
    if x is None:
        return default
    s = str(x).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def _read_f(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: float,
    defaults_used: List[str],
) -> float:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    return _to_float(_gv(get_config_value, key, default), default)


def _read_i(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: int,
    defaults_used: List[str],
) -> int:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    return _to_int(_gv(get_config_value, key, default), default)


def _read_b(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: bool,
    defaults_used: List[str],
) -> bool:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    return _to_bool(_gv(get_config_value, key, default), default)


def _read_s(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: str,
    defaults_used: List[str],
) -> str:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    raw = _gv(get_config_value, key, default)
    s = str(raw).strip() if raw is not None else ""
    return s if s else default


def _log_loaded_kv(prefix: str, pairs: List[Tuple[str, Any]]) -> None:
    for name, val in pairs:
        log.info("%s | key=%s | value=%s", prefix, name, val)


def _log_config_surface_audit() -> None:
    structure_keys = [
        "TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES",
        "TRENDLINE_MIN_STRUCTURE_BARS",
        "TRENDLINE_MIN_STRUCTURE_SECONDS",
        "TRENDLINE_REANCHOR_ENABLED",
    ]
    break_quality_keys = [
        "TRENDLINE_MIN_BREAK_PCT",
        "TRENDLINE_BREAK_DISTANCE_MIN",
        "TRENDLINE_STRICT_MIN_BREAK_DISTANCE",
        "TRENDLINE_BODY_RATIO_MIN_STRICT",
        "TRENDLINE_IMPULSE_BREAK_BODY_RATIO_MIN",
        "TRENDLINE_EARLY_ENTRY_BODY_RATIO_MIN",
        "TRENDLINE_MIN_EXPECTED_MOVE_PCT_DEFAULT",
    ]
    continuation_keys = [
        "TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK",
        "TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE",
        "TRENDLINE_REQUIRE_HOLD_AFTER_BREAK",
        "TRENDLINE_POST_CONTINUE_FAST_EXTENSION_PCT",
        "TRENDLINE_POST_CONTINUE_BOX_BREAK_BUFFER_PCT",
        "TRENDLINE_PULLBACK_STRENGTH_THRESHOLD",
    ]
    chop_keys = [
        "TRENDLINE_CHOP_RECENT_BARS",
        "TRENDLINE_CHOP_MAX_CROSSES",
        "TRENDLINE_CHOP_SMALL_RANGE_VS_ORB_RATIO",
        "TRENDLINE_REGIME_CHOPPY_BODY_RATIO_MIN",
    ]
    execution_keys = [
        "TRENDLINE_MIN_SECONDS_AFTER_BREAK",
        "TRENDLINE_MAX_SECONDS_AFTER_BREAK",
        "TRENDLINE_OPTION_MAX_SPREAD_PCT",
        "TRENDLINE_OPTION_MIN_OPEN_INTEREST",
        "TRENDLINE_OPTION_MIN_VOLUME",
    ]
    deprecated_advisory_only = [
        "TRENDLINE_BODY_RATIO_MIN_STRICT",
        "TRENDLINE_IMPULSE_BREAK_BODY_RATIO_MIN",
        "TRENDLINE_EARLY_ENTRY_BODY_RATIO_MIN",
        "TRENDLINE_STRICT_MIN_BREAK_DISTANCE",
        "TRENDLINE_BREAK_DISTANCE_MIN",
        "TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK",
        "TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE",
        "TRENDLINE_REQUIRE_HOLD_AFTER_BREAK",
        "TRENDLINE_POST_CONTINUE_BOX_BREAK_BUFFER_PCT",
        "TRENDLINE_POST_CONTINUE_FAST_EXTENSION_PCT",
        "TRENDLINE_REGIME_CHOPPY_BODY_RATIO_MIN",
        "TRENDLINE_PULLBACK_STRENGTH_THRESHOLD",
    ]
    log.info(
        "TRENDLINE_CONFIG_SURFACE_AUDIT | structure=%s | break_quality=%s | continuation=%s | chop=%s | execution=%s | deprecated_advisory_only=%s",
        ",".join(structure_keys),
        ",".join(break_quality_keys),
        ",".join(continuation_keys),
        ",".join(chop_keys),
        ",".join(execution_keys),
        ",".join(deprecated_advisory_only),
    )


def log_trendline_entry_profile_audit(get_config_value: Callable[[str, Any], Any], cfg: TrendlineConfig) -> None:
    """Startup: resolved entry profile + composite score floors."""
    prof = str(_gv(get_config_value, "TRENDLINE_ENTRY_PROFILE", "balanced")).strip()
    log.warning(
        "TRENDLINE_ENTRY_PROFILE_AUDIT | TRENDLINE_ENTRY_PROFILE=%s | "
        "TRENDLINE_ENTRY_SCORE_MIN_IMPULSE=%.4f | TRENDLINE_ENTRY_SCORE_MIN_CONTINUATION=%.4f | "
        "TRENDLINE_ENTRY_SCORE_MIN_EXHAUSTION=%.4f | TRENDLINE_BOUNCEBACK_RECLAIM_MAX_BARS=%d",
        prof,
        float(cfg.entry_score_min_impulse),
        float(cfg.entry_score_min_continuation),
        float(cfg.entry_score_min_exhaustion),
        int(cfg.bounceback_reclaim_max_bars),
    )


def log_trendline_score_defaults_audit(cfg: TrendlineConfig) -> None:
    """Startup: score weights / penalties snapshot for auditability."""
    log.warning(
        "TRENDLINE_SCORE_DEFAULTS_AUDIT | w_structure=%.4f | w_dist_inc=%.4f | w_reclaim=%.4f | "
        "w_slope=%.4f | w_vel=%.4f | w_em=%.4f | w_body=%.4f | w_exp=%.4f | w_ft=%.4f | "
        "pen_chop=%.4f | pen_opp=%.4f | pen_reclaim=%.4f | stale_sec=%.1f | em_collapse=%.4f",
        float(cfg.score_weight_structure),
        float(cfg.score_weight_distance_increasing),
        float(cfg.score_weight_reclaim_hold),
        float(cfg.score_weight_continuation_slope),
        float(cfg.score_weight_continuation_velocity),
        float(cfg.score_weight_expected_move),
        float(cfg.score_weight_body),
        float(cfg.score_weight_expansion),
        float(cfg.score_weight_followthrough),
        float(cfg.score_penalty_chop),
        float(cfg.score_penalty_opposite_candle),
        float(cfg.score_penalty_reclaim),
        float(cfg.entry_score_stale_seconds),
        float(cfg.entry_em_collapse_ratio),
    )


def log_trendline_config_ownership_audit(get_config_value: Callable[[str, Any], Any]) -> None:
    """Count Trendline-related keys: active env, profile-default-only, deprecated, unused warnings."""
    try:
        from modules import config_profiles as cp

        repo_tl_flat = cp._repo_trendline_flat()
        shared_flat = cp._parse_env_file(cp._CONFIG_DIR / "Shared.env")
        merged_files: Dict[str, str] = {}
        for name in ("Data.env", "Shared.env", "ORBSO.env", "ORB0DTE.env", "Trendline0DTE.env", "Risk.env", "Alerts.env"):
            merged_files.update(cp._parse_env_file(cp._CONFIG_DIR / name))
        active_tl_env = sorted(k for k in repo_tl_flat if k.startswith("TRENDLINE_") or k == "ENABLE_TRENDLINE_STRATEGY")
        shared_tl = sorted(k for k in shared_flat if k.startswith("TRENDLINE_"))
        raw_tl_prof = str(_gv(get_config_value, "TRENDLINE_ENTRY_PROFILE", "balanced")).strip().lower()
        tl_p = raw_tl_prof if raw_tl_prof in cp.TRENDLINE_ENTRY_PROFILES else "balanced"
        bundle = cp.TRENDLINE_ENTRY_PROFILES.get(tl_p, cp.TRENDLINE_ENTRY_PROFILES["balanced"])
        profile_only = sorted(k for k in bundle if k.startswith("TRENDLINE_") and k not in merged_files)
        dep_hits = sorted(k for k in merged_files if k in _DEPRECATED_TRENDLINE_ENV_KEYS)
        unused_like = sorted(
            k
            for k in os.environ
            if k.startswith("TRENDLINE_") and k not in ALL_TRENDLINE_CONSUMED_ENV_KEYS
        )
        log.warning(
            "TRENDLINE_CONFIG_OWNERSHIP_AUDIT | trendline_file_key_count=%d | shared_trendline_key_count=%d | "
            "profile_default_only_key_count=%d | deprecated_key_hit_count=%d | unused_env_key_count=%d | "
            "sample_profile_only=%s",
            len(active_tl_env),
            len(shared_tl),
            len(profile_only),
            len(dep_hits),
            len(unused_like),
            ",".join(profile_only[:24]) + ("..." if len(profile_only) > 24 else ""),
        )
        if dep_hits:
            log.info("TRENDLINE_CONFIG_OWNERSHIP_DEPRECATED | keys=%s", ",".join(dep_hits))
    except Exception as exc:  # pragma: no cover
        log.debug("TRENDLINE_CONFIG_OWNERSHIP_AUDIT_SKIP | reason=%s", exc)


def log_trendline_effective_config_audit(
    get_config_value: Callable[[str, Any], Any],
    cfg: TrendlineConfig,
) -> None:
    """Single-line startup audit: resolved Trendline entry + false-break knobs (post-env merge)."""
    entry_profile = str(_gv(get_config_value, "TRENDLINE_ENTRY_PROFILE", "balanced")).strip()
    log.warning(
        "TRENDLINE_EFFECTIVE_CONFIG_AUDIT | TRENDLINE_ENTRY_PROFILE=%s | "
        "TRENDLINE_FAST_PATH_MIN_BREAK_DISTANCE=%.6f | TRENDLINE_FAST_PATH_MIN_BODY_RATIO=%.4f | "
        "TRENDLINE_FAST_PATH_WEAK_BODY_CATASTROPHIC_ONLY=%s | "
        "TRENDLINE_LOCAL_CONTINUATION_SURVIVAL_EXTRA_BARS=%d | TRENDLINE_LOCAL_CONTINUATION_SURVIVAL_SEC=%.2f | "
        "TRENDLINE_FALSE_BREAK_BYPASS_MIN_EM_MULT=%.4f | "
        "TRENDLINE_MIN_EXPECTED_MOVE_PCT=%.6f | TRENDLINE_MIN_EXPECTED_MOVE_PCT_DEFAULT=%.6f | "
        "TRENDLINE_MIN_EXPECTED_MOVE_PCT_SLOW=%.6f | TRENDLINE_MIN_EXPECTED_MOVE_PCT_IMPULSE=%.6f | "
        "TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK=%s | TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE=%s",
        entry_profile,
        float(cfg.fast_path_min_break_distance),
        float(cfg.fast_path_min_body_ratio),
        str(bool(cfg.fast_path_weak_body_catastrophic_only)).lower(),
        int(cfg.local_continuation_survival_extra_bars),
        float(cfg.local_continuation_survival_sec),
        float(cfg.false_break_bypass_min_em_mult),
        float(cfg.min_expected_move_pct),
        float(cfg.min_expected_move_pct_default),
        float(cfg.min_expected_move_pct_slow),
        float(cfg.min_expected_move_pct_impulse),
        str(bool(cfg.require_local_continuation_break)).lower(),
        str(bool(cfg.require_post_break_structure)).lower(),
    )


def load_trendline_config_from_env(get_config_value: Callable[[str, Any], Any]) -> TrendlineConfig:
    """Build ``TrendlineConfig`` from env; logs each resolved field and any defaults used."""
    _warn_deprecated_trendline_env_keys()
    defaults_used: List[str] = []
    g = get_config_value

    base = TrendlineConfig()
    confirm_seconds = min(30.0, max(1.0, _read_f(g, "TRENDLINE_CONFIRM_SECONDS", 10.0, defaults_used)))
    max_confirm = _read_i(g, "TRENDLINE_MAX_BREAK_TO_CONFIRM_MINUTES", 60, defaults_used)
    strong_break_th = _read_f(g, "TRENDLINE_STRONG_BREAK_THRESHOLD", base.strong_break_threshold, defaults_used)
    strong_body_ratio = _read_f(g, "TRENDLINE_STRONG_BODY_RATIO", base.direction_override_strong_body_ratio, defaults_used)

    cfg = TrendlineConfig(
        build_time_pt=_read_s(g, "TRENDLINE_BUILD_TIME_PT", base.build_time_pt, defaults_used),
        expiration_time_pt=_read_s(g, "TRENDLINE_EXPIRATION_TIME_PT", base.expiration_time_pt, defaults_used),
        min_break_pct=_read_f(g, "TRENDLINE_MIN_BREAK_PCT", base.min_break_pct, defaults_used),
        use_atr_break=_read_b(g, "TRENDLINE_USE_ATR_BREAK", base.use_atr_break, defaults_used),
        atr_break_multiplier=_read_f(g, "TRENDLINE_ATR_BREAK_MULTIPLIER", base.atr_break_multiplier, defaults_used),
        min_anchor_distance_minutes=_read_i(
            g, "TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES", base.min_anchor_distance_minutes, defaults_used
        ),
        break_distance_min=_read_f(g, "TRENDLINE_BREAK_DISTANCE_MIN", base.break_distance_min, defaults_used),
        body_ratio_min_strong=_read_f(g, "TRENDLINE_BODY_RATIO_MIN_STRONG", base.body_ratio_min_strong, defaults_used),
        body_ratio_min_weak=_read_f(g, "TRENDLINE_BODY_RATIO_MIN_WEAK", base.body_ratio_min_weak, defaults_used),
        fast_path_enabled=_read_b(g, "TRENDLINE_FAST_PATH_ENABLED", base.fast_path_enabled, defaults_used),
        confirm_seconds=float(confirm_seconds),
        require_hold_after_break=_read_b(
            g, "TRENDLINE_REQUIRE_HOLD_AFTER_BREAK", base.require_hold_after_break, defaults_used
        ),
        require_local_continuation_break=_read_b(
            g, "TRENDLINE_REQUIRE_LOCAL_CONTINUATION_BREAK", base.require_local_continuation_break, defaults_used
        ),
        require_post_break_structure=_read_b(
            g, "TRENDLINE_REQUIRE_POST_BREAK_STRUCTURE", base.require_post_break_structure, defaults_used
        ),
        expansion_net_move_ratio_min=_read_f(
            g, "TRENDLINE_EXPANSION_NET_MOVE_RATIO_MIN", base.expansion_net_move_ratio_min, defaults_used
        ),
        expansion_overlap_max=_read_f(g, "TRENDLINE_EXPANSION_OVERLAP_MAX", base.expansion_overlap_max, defaults_used),
        expansion_break_level_return_max=_read_i(
            g, "TRENDLINE_EXPANSION_BREAK_LEVEL_RETURN_MAX", base.expansion_break_level_return_max, defaults_used
        ),
        expansion_impulse_mfe_enabled=_read_b(
            g, "TRENDLINE_EXPANSION_IMPULSE_MFE_ENABLED", base.expansion_impulse_mfe_enabled, defaults_used
        ),
        expansion_impulse_body_strong=_read_f(
            g, "TRENDLINE_EXPANSION_IMPULSE_BODY_STRONG", base.expansion_impulse_body_strong, defaults_used
        ),
        expansion_impulse_survival_min_bars=_read_i(
            g, "TRENDLINE_EXPANSION_IMPULSE_SURVIVAL_MIN_BARS", base.expansion_impulse_survival_min_bars, defaults_used
        ),
        expansion_impulse_survival_min_seconds=_read_f(
            g,
            "TRENDLINE_EXPANSION_IMPULSE_SURVIVAL_MIN_SECONDS",
            base.expansion_impulse_survival_min_seconds,
            defaults_used,
        ),
        max_break_to_confirm_minutes=max_confirm,
        max_entry_distance_pct=_read_f(
            g, "TRENDLINE_MAX_DISTANCE_FROM_BREAK_PCT", base.max_entry_distance_pct, defaults_used
        ),
        no_new_entries_after_pt=_read_s(
            g, "TRENDLINE_NO_NEW_ENTRIES_AFTER_PT", base.no_new_entries_after_pt, defaults_used
        ),
        strong_breakout_distance_mult=_read_f(
            g, "TRENDLINE_STRONG_BREAKOUT_DISTANCE_MULT", base.strong_breakout_distance_mult, defaults_used
        ),
        body_expansion_mult=_read_f(g, "TRENDLINE_BODY_EXPANSION_MULT", base.body_expansion_mult, defaults_used),
        clean_breakout_bypass_momentum=_read_b(
            g, "TRENDLINE_CLEAN_BREAKOUT_BYPASS_MOMENTUM", base.clean_breakout_bypass_momentum, defaults_used
        ),
        confirmation_window_bars=_read_i(
            g, "TRENDLINE_CONFIRMATION_WINDOW_BARS", base.confirmation_window_bars, defaults_used
        ),
        min_followthrough_bars=_read_i(
            g, "TRENDLINE_MIN_FOLLOWTHROUGH_BARS", base.min_followthrough_bars, defaults_used
        ),
        min_velocity_pct=_read_f(g, "TRENDLINE_MIN_VELOCITY_PCT", base.min_velocity_pct, defaults_used),
        range_expansion_multiplier=_read_f(
            g, "TRENDLINE_RANGE_EXPANSION_MULTIPLIER", base.range_expansion_multiplier, defaults_used
        ),
        min_break_quality_score=_read_f(
            g, "TRENDLINE_MIN_BREAK_QUALITY_SCORE", base.min_break_quality_score, defaults_used
        ),
        chop_recent_bars=_read_i(g, "TRENDLINE_CHOP_RECENT_BARS", base.chop_recent_bars, defaults_used),
        chop_max_crosses=_read_i(g, "TRENDLINE_CHOP_MAX_CROSSES", base.chop_max_crosses, defaults_used),
        chop_small_range_vs_orb_ratio=_read_f(
            g, "TRENDLINE_CHOP_SMALL_RANGE_VS_ORB_RATIO", base.chop_small_range_vs_orb_ratio, defaults_used
        ),
        hold_bars_after_break=_read_i(g, "TRENDLINE_HOLD_BARS_REQUIRED", base.hold_bars_after_break, defaults_used),
        hold_mode=_read_s(g, "TRENDLINE_HOLD_MODE", base.hold_mode, defaults_used),
        hold_bar_interval=_read_s(g, "TRENDLINE_HOLD_BAR_INTERVAL", base.hold_bar_interval, defaults_used),
        post_break_structure_lookback_bars=_read_i(
            g, "TRENDLINE_POST_BREAK_STRUCTURE_LOOKBACK_BARS", base.post_break_structure_lookback_bars, defaults_used
        ),
        max_break_to_hold_retrace_pct=_read_f(
            g, "TRENDLINE_MAX_BREAK_TO_HOLD_RETRACE_PCT", base.max_break_to_hold_retrace_pct, defaults_used
        ),
        min_continuation_distance_pct=_read_f(
            g, "TRENDLINE_MIN_CONTINUATION_DISTANCE_PCT", base.min_continuation_distance_pct, defaults_used
        ),
        post_continue_settle_bars=_read_i(
            g, "TRENDLINE_POST_CONTINUE_SETTLE_BARS", base.post_continue_settle_bars, defaults_used
        ),
        post_continue_chop_box_gate_enabled=_read_b(
            g, "TRENDLINE_POST_CONTINUE_CHOP_BOX_GATE_ENABLED", base.post_continue_chop_box_gate_enabled, defaults_used
        ),
        post_continue_fast_followthrough_bars=_read_i(
            g,
            "TRENDLINE_POST_CONTINUE_FAST_FOLLOWTHROUGH_BARS",
            base.post_continue_fast_followthrough_bars,
            defaults_used,
        ),
        post_continue_fast_extension_pct=_read_f(
            g, "TRENDLINE_POST_CONTINUE_FAST_EXTENSION_PCT", base.post_continue_fast_extension_pct, defaults_used
        ),
        post_continue_box_break_buffer_pct=_read_f(
            g, "TRENDLINE_POST_CONTINUE_BOX_BREAK_BUFFER_PCT", base.post_continue_box_break_buffer_pct, defaults_used
        ),
        post_continue_chop_min_prior_bars=_read_i(
            g, "TRENDLINE_POST_CONTINUE_CHOP_MIN_PRIOR_BARS", base.post_continue_chop_min_prior_bars, defaults_used
        ),
        allow_second_break_attempt=_read_b(
            g, "TRENDLINE_ALLOW_SECOND_BREAK_ATTEMPT", base.allow_second_break_attempt, defaults_used
        ),
        second_break_attempt_max_bars=_read_i(
            g, "TRENDLINE_SECOND_BREAK_ATTEMPT_MAX_BARS", base.second_break_attempt_max_bars, defaults_used
        ),
        confirm_pending_log_sec=_read_f(g, "TRENDLINE_CONFIRM_PENDING_LOG_SEC", base.confirm_pending_log_sec, defaults_used),
        false_break_gate_log_throttle_sec=_read_f(
            g, "TRENDLINE_FALSE_BREAK_GATE_LOG_THROTTLE_SEC", base.false_break_gate_log_throttle_sec, defaults_used
        ),
        trendline_funnel_summary_interval_sec=_read_f(
            g, "TRENDLINE_FUNNEL_SUMMARY_INTERVAL_SEC", base.trendline_funnel_summary_interval_sec, defaults_used
        ),
        continuation_max_bars=_read_i(g, "TRENDLINE_CONTINUATION_MAX_BARS", base.continuation_max_bars, defaults_used),
        continuation_min_bars=_read_i(g, "TRENDLINE_CONTINUATION_MIN_BARS", base.continuation_min_bars, defaults_used),
        pullback_strength_threshold=_read_f(
            g, "TRENDLINE_PULLBACK_STRENGTH_THRESHOLD", base.pullback_strength_threshold, defaults_used
        ),
        rearm_enabled=_read_b(g, "TRENDLINE_REARM_ENABLED", base.rearm_enabled, defaults_used),
        rearm_max_checks=_read_i(g, "TRENDLINE_REARM_MAX_CHECKS", base.rearm_max_checks, defaults_used),
        rearm_max_minutes=_read_f(g, "TRENDLINE_REARM_MAX_MINUTES", base.rearm_max_minutes, defaults_used),
        rearm_allowed_reasons_csv=_read_s(
            g, "TRENDLINE_REARM_ALLOWED_REASONS_CSV", base.rearm_allowed_reasons_csv, defaults_used
        ),
        rearm_body_ratio_threshold=_read_f(
            g, "TRENDLINE_REARM_BODY_RATIO_THRESHOLD", base.rearm_body_ratio_threshold, defaults_used
        ),
        extreme_break_threshold=_read_f(g, "TRENDLINE_EXTREME_BREAK_THRESHOLD", base.extreme_break_threshold, defaults_used),
        near_extreme_break_threshold=_read_f(
            g, "TRENDLINE_NEAR_EXTREME_BREAK_THRESHOLD", base.near_extreme_break_threshold, defaults_used
        ),
        strong_break_threshold=strong_break_th,
        weak_break_threshold=_read_f(g, "TRENDLINE_WEAK_BREAK_THRESHOLD", base.weak_break_threshold, defaults_used),
        reversal_reclaim_min_distance=_read_f(
            g, "TRENDLINE_REVERSAL_RECLAIM_MIN_DISTANCE", base.reversal_reclaim_min_distance, defaults_used
        ),
        reversal_max_bars_since_rejection=_read_i(
            g, "TRENDLINE_REVERSAL_MAX_BARS_SINCE_REJECTION", base.reversal_max_bars_since_rejection, defaults_used
        ),
        reversal_early_reclaim_bars=_read_i(
            g, "TRENDLINE_REVERSAL_EARLY_RECLAIM_BARS", base.reversal_early_reclaim_bars, defaults_used
        ),
        reversal_confidence_boost=_read_f(
            g, "TRENDLINE_REVERSAL_CONFIDENCE_BOOST", base.reversal_confidence_boost, defaults_used
        ),
        regime_choppy_overlap_min=_read_f(
            g, "TRENDLINE_REGIME_CHOPPY_OVERLAP_MIN", base.regime_choppy_overlap_min, defaults_used
        ),
        regime_trend_directional_min=_read_f(
            g, "TRENDLINE_REGIME_TREND_DIRECTIONAL_MIN", base.regime_trend_directional_min, defaults_used
        ),
        regime_min_avg_range_pct=_read_f(
            g, "TRENDLINE_REGIME_MIN_AVG_RANGE_PCT", base.regime_min_avg_range_pct, defaults_used
        ),
        regime_choppy_body_ratio_min=_read_f(
            g, "TRENDLINE_REGIME_CHOPPY_BODY_RATIO_MIN", base.regime_choppy_body_ratio_min, defaults_used
        ),
        regime_choppy_min_followthrough_bars=_read_i(
            g, "TRENDLINE_REGIME_CHOPPY_MIN_FOLLOWTHROUGH_BARS", base.regime_choppy_min_followthrough_bars, defaults_used
        ),
        impulse_enabled=_read_b(g, "TRENDLINE_IMPULSE_ENABLED", base.impulse_enabled, defaults_used),
        impulse_min_body_ratio=_read_f(g, "TRENDLINE_IMPULSE_MIN_BODY_RATIO", base.impulse_min_body_ratio, defaults_used),
        impulse_confirm_next_candle=_read_b(
            g, "TRENDLINE_IMPULSE_CONFIRM_NEXT_CANDLE", base.impulse_confirm_next_candle, defaults_used
        ),
        slow_trend_enabled=_read_b(g, "TRENDLINE_SLOW_TREND_ENABLED", base.slow_trend_enabled, defaults_used),
        slow_trend_min_candles=_read_i(g, "TRENDLINE_SLOW_TREND_MIN_CANDLES", base.slow_trend_min_candles, defaults_used),
        slow_trend_max_candles=_read_i(g, "TRENDLINE_SLOW_TREND_MAX_CANDLES", base.slow_trend_max_candles, defaults_used),
        slow_trend_consistency_min=_read_f(
            g, "TRENDLINE_SLOW_TREND_CONSISTENCY_MIN", base.slow_trend_consistency_min, defaults_used
        ),
        slow_trend_cum_break_move_min=_read_f(
            g, "TRENDLINE_SLOW_TREND_CUM_BREAK_MOVE_MIN", base.slow_trend_cum_break_move_min, defaults_used
        ),
        early_entry_enabled=_read_b(g, "TRENDLINE_EARLY_ENTRY_ENABLED", base.early_entry_enabled, defaults_used),
        early_entry_body_ratio_min=_read_f(
            g, "TRENDLINE_EARLY_ENTRY_BODY_RATIO_MIN", base.early_entry_body_ratio_min, defaults_used
        ),
        early_entry_size_multiplier=_read_f(
            g, "TRENDLINE_EARLY_ENTRY_SIZE_MULTIPLIER", base.early_entry_size_multiplier, defaults_used
        ),
        impulse_break_body_ratio_min=_read_f(
            g, "TRENDLINE_IMPULSE_BREAK_BODY_RATIO_MIN", base.impulse_break_body_ratio_min, defaults_used
        ),
        retest_enabled=_read_b(g, "TRENDLINE_RETEST_ENABLED", base.retest_enabled, defaults_used),
        retest_max_checks=_read_i(g, "TRENDLINE_RETEST_MAX_CHECKS", base.retest_max_checks, defaults_used),
        retest_max_minutes=_read_f(g, "TRENDLINE_RETEST_MAX_MINUTES", base.retest_max_minutes, defaults_used),
        retest_body_ratio_min=_read_f(g, "TRENDLINE_RETEST_BODY_RATIO_MIN", base.retest_body_ratio_min, defaults_used),
        min_expected_move_pct=_read_f(g, "TRENDLINE_MIN_EXPECTED_MOVE_PCT", base.min_expected_move_pct, defaults_used),
        min_expected_move_pct_slow=_read_f(
            g, "TRENDLINE_MIN_EXPECTED_MOVE_PCT_SLOW", base.min_expected_move_pct_slow, defaults_used
        ),
        min_expected_move_pct_impulse=_read_f(
            g, "TRENDLINE_MIN_EXPECTED_MOVE_PCT_IMPULSE", base.min_expected_move_pct_impulse, defaults_used
        ),
        min_expected_move_pct_default=_read_f(
            g, "TRENDLINE_MIN_EXPECTED_MOVE_PCT_DEFAULT", base.min_expected_move_pct_default, defaults_used
        ),
        touch_tolerance_pct=_read_f(g, "TRENDLINE_TOUCH_TOLERANCE_PCT", base.touch_tolerance_pct, defaults_used),
        high_pressure_touch_count=_read_i(
            g, "TRENDLINE_HIGH_PRESSURE_TOUCH_COUNT", base.high_pressure_touch_count, defaults_used
        ),
        pressure_score_min=_read_f(g, "TRENDLINE_PRESSURE_SCORE_MIN", base.pressure_score_min, defaults_used),
        min_structure_bars=_read_i(g, "TRENDLINE_MIN_STRUCTURE_BARS", base.min_structure_bars, defaults_used),
        min_structure_seconds=_read_f(g, "TRENDLINE_MIN_STRUCTURE_SECONDS", base.min_structure_seconds, defaults_used),
        post_break_survival_bars=_read_i(
            g, "TRENDLINE_POST_BREAK_SURVIVAL_BARS", base.post_break_survival_bars, defaults_used
        ),
        max_active_minutes=_read_f(g, "TRENDLINE_MAX_ACTIVE_MINUTES", base.max_active_minutes, defaults_used),
        missed_win_move_pct=_read_f(g, "TRENDLINE_MISSED_WIN_MOVE_PCT", base.missed_win_move_pct, defaults_used),
        bad_entry_max_favorable_pct=_read_f(
            g, "TRENDLINE_BAD_ENTRY_MAX_FAVORABLE_PCT", base.bad_entry_max_favorable_pct, defaults_used
        ),
        bad_entry_drawdown_pct=_read_f(g, "TRENDLINE_BAD_ENTRY_DRAWDOWN_PCT", base.bad_entry_drawdown_pct, defaults_used),
        min_touch_bar_gap=_read_i(g, "TRENDLINE_MIN_TOUCH_BAR_GAP", base.min_touch_bar_gap, defaults_used),
        fast_path_override_abs_floor=_read_f(
            g, "TRENDLINE_FAST_PATH_OVERRIDE_ABS_FLOOR", base.fast_path_override_abs_floor, defaults_used
        ),
        fast_path_final_abs_floor=_read_f(
            g, "TRENDLINE_FAST_PATH_FINAL_ABS_FLOOR", base.fast_path_final_abs_floor, defaults_used
        ),
        strong_dist_override_abs_floor=_read_f(
            g, "TRENDLINE_STRONG_DIST_OVERRIDE_ABS_FLOOR", base.strong_dist_override_abs_floor, defaults_used
        ),
        triple_weak_ceiling_abs_floor=_read_f(
            g, "TRENDLINE_TRIPLE_WEAK_CEILING_ABS_FLOOR", base.triple_weak_ceiling_abs_floor, defaults_used
        ),
        composite_close_pos_commit_min=_read_f(
            g, "TRENDLINE_COMPOSITE_CLOSE_POS_COMMIT_MIN", base.composite_close_pos_commit_min, defaults_used
        ),
        triple_weak_close_pos_max=_read_f(
            g, "TRENDLINE_TRIPLE_WEAK_CLOSE_POS_MAX", base.triple_weak_close_pos_max, defaults_used
        ),
        min_drift_displacement=_read_f(g, "TRENDLINE_MIN_DRIFT_DISPLACEMENT", base.min_drift_displacement, defaults_used),
        require_drift_confirm=_read_b(g, "TRENDLINE_REQUIRE_DRIFT_CONFIRM", base.require_drift_confirm, defaults_used),
        reanchor_enabled=_read_b(g, "TRENDLINE_REANCHOR_ENABLED", base.reanchor_enabled, defaults_used),
        reanchor_minutes=_read_i(g, "TRENDLINE_REANCHOR_MINUTES", base.reanchor_minutes, defaults_used),
        reanchor_min_touches=_read_i(
            g, "TRENDLINE_REANCHOR_MIN_TOUCHES", base.reanchor_min_touches, defaults_used
        ),
        reanchor_lookback_bars=_read_i(
            g, "TRENDLINE_REANCHOR_LOOKBACK_BARS", base.reanchor_lookback_bars, defaults_used
        ),
        retest_line_buffer_pct=_read_f(
            g, "TRENDLINE_RETEST_LINE_BUFFER_PCT", base.retest_line_buffer_pct, defaults_used
        ),
        acceptance_min_beyond_closes=_read_i(
            g, "TRENDLINE_ACCEPTANCE_MIN_BEYOND_CLOSES", base.acceptance_min_beyond_closes, defaults_used
        ),
        acceptance_max_reclaims=_read_i(
            g, "TRENDLINE_ACCEPTANCE_MAX_RECLAIMS", base.acceptance_max_reclaims, defaults_used
        ),
        rearm_max_structure_violations=_read_i(
            g, "TRENDLINE_REARM_MAX_STRUCTURE_VIOLATIONS", base.rearm_max_structure_violations, defaults_used
        ),
        strict_min_break_distance=_read_f(
            g, "TRENDLINE_STRICT_MIN_BREAK_DISTANCE", base.strict_min_break_distance, defaults_used
        ),
        body_ratio_min_strict=_read_f(g, "TRENDLINE_BODY_RATIO_MIN_STRICT", base.body_ratio_min_strict, defaults_used),
        direction_override_strong_break_threshold=strong_break_th,
        direction_override_strong_body_ratio=strong_body_ratio,
        entry_min_seconds_after_break=_read_f(
            g, "TRENDLINE_MIN_SECONDS_AFTER_BREAK", base.entry_min_seconds_after_break, defaults_used
        ),
        entry_max_seconds_after_break=_read_f(
            g, "TRENDLINE_MAX_SECONDS_AFTER_BREAK", base.entry_max_seconds_after_break, defaults_used
        ),
        normal_entry_require_confirmation=_read_b(
            g, "TRENDLINE_NORMAL_ENTRY_REQUIRE_CONFIRMATION", base.normal_entry_require_confirmation, defaults_used
        ),
        fast_path_require_strong_break=_read_b(
            g, "TRENDLINE_FAST_PATH_REQUIRE_STRONG_BREAK", base.fast_path_require_strong_break, defaults_used
        ),
        fast_path_require_distance_increasing=_read_b(
            g,
            "TRENDLINE_FAST_PATH_REQUIRE_DISTANCE_INCREASING",
            base.fast_path_require_distance_increasing,
            defaults_used,
        ),
        fast_path_require_momentum_agreement=_read_b(
            g,
            "TRENDLINE_FAST_PATH_REQUIRE_MOMENTUM_AGREEMENT",
            base.fast_path_require_momentum_agreement,
            defaults_used,
        ),
        fast_path_require_no_reclaim=_read_b(
            g, "TRENDLINE_FAST_PATH_REQUIRE_NO_RECLAIM", base.fast_path_require_no_reclaim, defaults_used
        ),
        fast_path_min_body_ratio=_read_f(
            g, "TRENDLINE_FAST_PATH_MIN_BODY_RATIO", base.fast_path_min_body_ratio, defaults_used
        ),
        fast_path_min_break_distance=_read_f(
            g, "TRENDLINE_FAST_PATH_MIN_BREAK_DISTANCE", base.fast_path_min_break_distance, defaults_used
        ),
        local_continuation_survival_extra_bars=_read_i(
            g,
            "TRENDLINE_LOCAL_CONTINUATION_SURVIVAL_EXTRA_BARS",
            base.local_continuation_survival_extra_bars,
            defaults_used,
        ),
        local_continuation_survival_sec=_read_f(
            g, "TRENDLINE_LOCAL_CONTINUATION_SURVIVAL_SEC", base.local_continuation_survival_sec, defaults_used
        ),
        false_break_bypass_min_em_mult=_read_f(
            g, "TRENDLINE_FALSE_BREAK_BYPASS_MIN_EM_MULT", base.false_break_bypass_min_em_mult, defaults_used
        ),
        fast_path_weak_body_catastrophic_only=_read_b(
            g,
            "TRENDLINE_FAST_PATH_WEAK_BODY_CATASTROPHIC_ONLY",
            base.fast_path_weak_body_catastrophic_only,
            defaults_used,
        ),
        entry_score_min_impulse=_read_f(g, "TRENDLINE_ENTRY_SCORE_MIN_IMPULSE", base.entry_score_min_impulse, defaults_used),
        entry_score_min_continuation=_read_f(
            g, "TRENDLINE_ENTRY_SCORE_MIN_CONTINUATION", base.entry_score_min_continuation, defaults_used
        ),
        entry_score_min_exhaustion=_read_f(
            g, "TRENDLINE_ENTRY_SCORE_MIN_EXHAUSTION", base.entry_score_min_exhaustion, defaults_used
        ),
        score_weight_structure=_read_f(g, "TRENDLINE_SCORE_WEIGHT_STRUCTURE", base.score_weight_structure, defaults_used),
        score_weight_distance_increasing=_read_f(
            g, "TRENDLINE_SCORE_WEIGHT_DISTANCE_INCREASING", base.score_weight_distance_increasing, defaults_used
        ),
        score_weight_reclaim_hold=_read_f(
            g, "TRENDLINE_SCORE_WEIGHT_RECLAIM_HOLD", base.score_weight_reclaim_hold, defaults_used
        ),
        score_weight_continuation_slope=_read_f(
            g, "TRENDLINE_SCORE_WEIGHT_CONTINUATION_SLOPE", base.score_weight_continuation_slope, defaults_used
        ),
        score_weight_continuation_velocity=_read_f(
            g, "TRENDLINE_SCORE_WEIGHT_CONTINUATION_VELOCITY", base.score_weight_continuation_velocity, defaults_used
        ),
        score_weight_expected_move=_read_f(
            g, "TRENDLINE_SCORE_WEIGHT_EXPECTED_MOVE", base.score_weight_expected_move, defaults_used
        ),
        score_weight_body=_read_f(g, "TRENDLINE_SCORE_WEIGHT_BODY", base.score_weight_body, defaults_used),
        score_weight_expansion=_read_f(g, "TRENDLINE_SCORE_WEIGHT_EXPANSION", base.score_weight_expansion, defaults_used),
        score_weight_followthrough=_read_f(
            g, "TRENDLINE_SCORE_WEIGHT_FOLLOWTHROUGH", base.score_weight_followthrough, defaults_used
        ),
        score_penalty_chop=_read_f(g, "TRENDLINE_SCORE_PENALTY_CHOP", base.score_penalty_chop, defaults_used),
        score_penalty_opposite_candle=_read_f(
            g, "TRENDLINE_SCORE_PENALTY_OPPOSITE_CANDLE", base.score_penalty_opposite_candle, defaults_used
        ),
        score_penalty_reclaim=_read_f(g, "TRENDLINE_SCORE_PENALTY_RECLAIM", base.score_penalty_reclaim, defaults_used),
        bounceback_reclaim_max_bars=_read_i(
            g, "TRENDLINE_BOUNCEBACK_RECLAIM_MAX_BARS", base.bounceback_reclaim_max_bars, defaults_used
        ),
        entry_score_stale_seconds=_read_f(
            g, "TRENDLINE_ENTRY_SCORE_STALE_SECONDS", base.entry_score_stale_seconds, defaults_used
        ),
        entry_em_collapse_ratio=_read_f(g, "TRENDLINE_ENTRY_EM_COLLAPSE_RATIO", base.entry_em_collapse_ratio, defaults_used),
        entry_survival_sec_mult_impulse=_read_f(
            g, "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_IMPULSE", base.entry_survival_sec_mult_impulse, defaults_used
        ),
        entry_survival_sec_mult_drift=_read_f(
            g, "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_DRIFT", base.entry_survival_sec_mult_drift, defaults_used
        ),
        entry_survival_sec_mult_exhaustion=_read_f(
            g, "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_EXHAUSTION", base.entry_survival_sec_mult_exhaustion, defaults_used
        ),
        entry_survival_extra_bars_impulse=_read_i(
            g, "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_IMPULSE", base.entry_survival_extra_bars_impulse, defaults_used
        ),
        entry_survival_extra_bars_drift=_read_i(
            g, "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_DRIFT", base.entry_survival_extra_bars_drift, defaults_used
        ),
        entry_survival_extra_bars_exhaustion=_read_i(
            g, "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_EXHAUSTION", base.entry_survival_extra_bars_exhaustion, defaults_used
        ),
    )

    pairs: List[Tuple[str, Any]] = [(f.name, getattr(cfg, f.name)) for f in fields(cfg)]
    _log_loaded_kv("TRENDLINE_CONFIG_LOADED", pairs)
    _log_config_surface_audit()
    log_trendline_effective_config_audit(get_config_value, cfg)
    log_trendline_entry_profile_audit(get_config_value, cfg)
    log_trendline_score_defaults_audit(cfg)
    log_trendline_config_ownership_audit(get_config_value)

    for dk in sorted(set(defaults_used)):
        log.info("TRENDLINE_CONFIG_DEFAULT_USED | key=%s", dk)

    return cfg


def load_trendline_option_selection_config(
    get_config_value: Callable[[str, Any], Any],
) -> TrendlineOptionSelectionConfig:
    """Option-selection env keys including strike mode, liquidity floors, and spread cap."""
    defaults_used: List[str] = []
    g = get_config_value
    base = TrendlineOptionSelectionConfig()
    cfg = TrendlineOptionSelectionConfig(
        delta_min=_read_f(g, "TRENDLINE_OPTION_DELTA_MIN", base.delta_min, defaults_used),
        delta_max=_read_f(g, "TRENDLINE_OPTION_DELTA_MAX", base.delta_max, defaults_used),
        max_bid_ask_spread_pct=_read_f(g, "TRENDLINE_OPTION_MAX_SPREAD_PCT", base.max_bid_ask_spread_pct, defaults_used),
        strike_mode=_read_s(g, "TRENDLINE_OPTION_STRIKE_MODE", base.strike_mode, defaults_used),
        lotto_mode=_read_b(g, "TRENDLINE_OPTION_LOTTO_MODE", base.lotto_mode, defaults_used),
        min_open_interest=_read_i(g, "TRENDLINE_OPTION_MIN_OPEN_INTEREST", base.min_open_interest, defaults_used),
        min_volume=_read_i(g, "TRENDLINE_OPTION_MIN_VOLUME", base.min_volume, defaults_used),
    )
    log.info(
        "TRENDLINE_OPTION_CONFIG | strike_mode=%s | lotto=%s | oi=%d | volume=%d | delta_min=%.3f | delta_max=%.3f | max_spread_pct=%.4f",
        cfg.strike_mode,
        str(cfg.lotto_mode).lower(),
        cfg.min_open_interest,
        cfg.min_volume,
        cfg.delta_min,
        cfg.delta_max,
        cfg.max_bid_ask_spread_pct,
    )
    for dk in sorted(set(defaults_used)):
        if dk.startswith("TRENDLINE_OPTION_"):
            log.info("TRENDLINE_CONFIG_DEFAULT_USED | key=%s", dk)
    return cfg
