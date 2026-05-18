#!/usr/bin/env python3
"""
Unified 0DTE options stealth trailing — premium-based exits for ORB 0DTE + Trendline 0DTE.

Single source of truth for 0DTE option position exits when wired from PrimeTradingSystem.
ORB ETF path continues to use prime_stealth_trailing_tp.py unchanged.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from .config_loader import get_config_value
except ImportError:
    from config_loader import get_config_value

log = logging.getLogger("prime_options_stealth_trailing")

# trade_id|symbol -> monotonic time of last OPTION_PRICE_RESOLUTION_AUDIT INFO emit
_option_price_resolution_audit_last: Dict[str, float] = {}
_option_degraded_live_quote_last: Dict[str, float] = {}
_option_price_source_log_last: Dict[str, float] = {}
_option_degraded_mode_log_last: Dict[str, float] = {}
_option_hwm_skip_log_last: Dict[str, float] = {}
_orb_exit_deferred_audit_last: Dict[str, float] = {}
# Throttle consolidated premium lifecycle lines (OPTION_PREMIUM_MARK_UPDATE / OPTION_EXIT_TRIGGER_EVAL).
_option_premium_lifecycle_tick_last: Dict[str, float] = {}
_option_exit_trigger_eval_last: Dict[str, float] = {}
# ORB 0DTE: throttled OPTION_*_SUPPRESSED_EXIT_GRADE lines (per trade_id|kind).
_orb_exit_grade_suppress_log_last: Dict[str, float] = {}
_orb_degraded_hold_escalated_last: Dict[str, float] = {}


def _orb_exit_grade_suppression_log_min_seconds() -> float:
    try:
        return float(get_config_value("OPTION_ORB_EXIT_GRADE_SUPPRESSION_LOG_MIN_SECONDS", 55.0) or 0.0)
    except Exception:
        return 55.0


def _maybe_emit_orb_exit_grade_timeout_suppression(
    *,
    suppress_kind: str,
    position_id: str,
    position_symbol: str,
    blocked_reason: str,
    prem_src: str,
    osnap: Dict[str, Any],
    st: Any,
    required_good_ticks: int,
    now: datetime,
) -> None:
    """
    High-visibility ORB-only telemetry when time/no-progress exits are held for exit-grade policy.

    Emits OPTION_TIMEOUT_SUPPRESSED_EXIT_GRADE or OPTION_NO_PROGRESS_SUPPRESSED_EXIT_GRADE (throttled).
    Call sites should continue to emit OPTION_EXIT_TRIGGER_SUPPRESSED_QUOTE_QUALITY / ORB_OPTIONS_DEGRADED_DATA_HOLD as today.
    """
    iso_key = (
        "orb_time_exit_suppress_started_iso"
        if suppress_kind == "time_exit"
        else "orb_no_progress_suppress_started_iso"
    )
    if not osnap.get(iso_key):
        osnap[iso_key] = now.replace(tzinfo=timezone.utc).isoformat()
    started_iso = str(osnap.get(iso_key) or "")
    supp_sec = 0.0
    try:
        t0 = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        supp_sec = max(0.0, (now - t0.astimezone(timezone.utc)).total_seconds())
    except Exception:
        supp_sec = 0.0
    qg = str(osnap.get("orb_quote_grade") or osnap.get("quote_grade") or "unknown")
    quote_src = str(prem_src or osnap.get("last_premium_source") or "unknown")
    dedupe = f"{str(position_id or '')}|{suppress_kind}"
    min_sec = float(_orb_exit_grade_suppression_log_min_seconds())
    if min_sec > 0:
        tmono = time.monotonic()
        last = _orb_exit_grade_suppress_log_last.get(dedupe, -1e30)
        if (tmono - last) < min_sec:
            return
        _orb_exit_grade_suppress_log_last[dedupe] = tmono
    log_token = (
        "OPTION_TIMEOUT_SUPPRESSED_EXIT_GRADE"
        if suppress_kind == "time_exit"
        else "OPTION_NO_PROGRESS_SUPPRESSED_EXIT_GRADE"
    )
    log.info(
        "%s | trade_id=%s | symbol=%s | quote_grade=%s | quote_src=%s | mark_is_exit_grade=%s | "
        "good_mark_tick_count=%d | required_tick_count=%d | blocked_reason=%s | suppression_duration_sec=%.1f | "
        "timeout_clock_frozen=%s | mark_quality=%s",
        log_token,
        str(position_id or "-"),
        str(position_symbol or "-"),
        qg,
        quote_src,
        str(bool(osnap.get("mark_is_exit_grade"))).lower(),
        int(getattr(st, "good_mark_tick_count", 0) or 0),
        int(max(1, int(required_good_ticks or 1))),
        str(blocked_reason or ""),
        float(supp_sec),
        str(bool(osnap.get("timeout_clock_frozen") or getattr(st, "timeout_clock_frozen", False))).lower(),
        str(osnap.get("mark_quality") or "unknown"),
    )
    if supp_sec >= 180.0:
        ekey = f"esc|{dedupe}"
        now_e = time.monotonic()
        last_e = float(_orb_degraded_hold_escalated_last.get(ekey, -1e30))
        if now_e - last_e >= 120.0:
            _orb_degraded_hold_escalated_last[ekey] = now_e
            try:
                bypass = str(
                    bool(get_config_value("ORB_OPTIONS_SAFETY_EXIT_BYPASS_EXIT_GRADE", True))
                ).lower()
            except Exception:
                bypass = "true"
            log.warning(
                "ORB_OPTIONS_DEGRADED_HOLD_ESCALATED | trade_id=%s | symbol=%s | suppress_kind=%s | "
                "suppression_duration_sec=%.1f | blocked_reason=%s | safety_bypass_exit_grade_config=%s",
                str(position_id or "-"),
                str(position_symbol or "-"),
                str(suppress_kind or ""),
                float(supp_sec),
                str(blocked_reason or ""),
                bypass,
            )


def _option_premium_lifecycle_log_min_seconds() -> float:
    try:
        return float(get_config_value("OPTION_PREMIUM_LIFECYCLE_LOG_MIN_SECONDS", 7.0) or 0.0)
    except Exception:
        return 7.0


def _lifecycle_tick_should_emit(cache: Dict[str, float], dedupe_key: str) -> bool:
    min_sec = float(_option_premium_lifecycle_log_min_seconds())
    if min_sec <= 0:
        return True
    now = time.monotonic()
    last = cache.get(dedupe_key, -1e30)
    if (now - last) < min_sec:
        return False
    cache[dedupe_key] = now
    return True


def _log_orb_exit_suppressed_quote_quality(
    *,
    candidate_exit_reason: str,
    blocked_reason: str,
    position_id: str,
    position_symbol: str,
    prem_src: str,
    osnap: Dict[str, Any],
    st: Any,
) -> None:
    log.info(
        "OPTION_EXIT_TRIGGER_SUPPRESSED_QUOTE_QUALITY | trade_id=%s | symbol=%s | strategy_type=orb_0dte | "
        "candidate_exit_reason=%s | blocked_reason=%s | premium_source=%s | mark_quality=%s | mark_is_exit_grade=%s | "
        "good_mark_tick_count=%d | timeout_clock_frozen=%s | quote_age_seconds=%s",
        str(position_id or "-"),
        str(position_symbol or "-"),
        str(candidate_exit_reason or ""),
        str(blocked_reason or ""),
        str(prem_src or ""),
        str(osnap.get("mark_quality") or "unknown"),
        str(bool(osnap.get("mark_is_exit_grade"))).lower(),
        int(getattr(st, "good_mark_tick_count", 0) or 0),
        str(bool(osnap.get("timeout_clock_frozen"))).lower(),
        str(osnap.get("quote_age_seconds") if osnap.get("quote_age_seconds") is not None else "n/a"),
    )


def _premium_updates_favorable_hwm(
    prem_src: str,
    detail: Dict[str, Any],
    *,
    is_spread: bool,
) -> bool:
    """
    Gate favorable high-water-mark (premium_hwm / spread_hwm) and max_pnl_pct / had_opportunity updates.

    Trust matrix (intentionally conservative on synthetic / proxy paths):
    - Single-leg: True when ``price_source_telemetry == live``, OR when ``prem_src`` is
      ``exact`` / ``nearest`` / ``cached_quote`` (E*TRADE contract path; may still be
      ``mark_quality=cached_recent`` — exit-grade is separate via ``mark_is_exit_grade``).
    - Spreads: True only when ``price_source_telemetry == live`` (e.g. both legs resolved
      for ``spread_mtm_mid_last`` with ``spread_premium_source_quality=mtm_leg_mid_last``).
      Otherwise HWM / MFE do not ratchet up on incomplete or modeled marks.

    Exit triggers use additional checks (``live_non_synthetic_quote``, ``mark_is_exit_grade``,
    timeout clock freeze, ORB relief tiers) — see ``OPTION_EXIT_TRIGGER_EVAL`` and
    ``ORB_OPTIONS_EXIT_DEFERRED_AUDIT`` in this module.
    """
    pt = str(detail.get("price_source_telemetry") or "").strip().lower()
    if pt == "live":
        return True
    if not is_spread and prem_src in ("exact", "nearest", "cached_quote"):
        return True
    return False


_option_stealth_state_snapshot_last: Dict[str, float] = {}


def _emit_option_stealth_state_if_due(*, cfg: Any, trade_id: str, symbol: str, message: str) -> None:
    """Throttled Cloud-friendly OPTION_STEALTH_STATE (see option_stealth_state_log_seconds)."""
    interval = float(getattr(cfg, "option_stealth_state_log_seconds", 15.0) or 0.0)
    if interval <= 0.0:
        return
    key = f"oss|{str(trade_id or '-')}|{str(symbol or '-')}"
    now_m = time.monotonic()
    last = float(_option_stealth_state_snapshot_last.get(key, -1e30))
    if (now_m - last) < interval:
        return
    _option_stealth_state_snapshot_last[key] = now_m
    log.info(message)


def _tline_quote_age_ok(cfg: Any, quote_age_seconds: Any) -> bool:
    try:
        age = float(quote_age_seconds or 0.0)
    except (TypeError, ValueError):
        age = 9999.0
    return age <= float(getattr(cfg, "quote_stale_max_age_seconds", 90.0) or 90.0)


def _tline_bid_ask_sanity_ok(detail: Dict[str, Any], prem: float) -> bool:
    try:
        bid = float(detail.get("option_bid") or 0.0)
        ask = float(detail.get("option_ask") or 0.0)
    except (TypeError, ValueError):
        return True
    if bid <= 0 or ask <= 0:
        return True
    if ask < bid:
        return False
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return True
    sp = (ask - bid) / max(mid, 1e-9)
    return sp <= 0.65


def tline_trusted_degraded_mark(
    cfg: Any,
    *,
    prem_src: str,
    mark_quality: str,
    quote_age_seconds: Any,
    synthetic_active: bool,
) -> bool:
    """Trusted non-synthetic contract marks for Trendline 0DTE protection/HWM (not modeled synth)."""
    if synthetic_active:
        return False
    if not _tline_quote_age_ok(cfg, quote_age_seconds):
        return False
    src = str(prem_src or "").strip().lower()
    mq = str(mark_quality or "").strip().lower()
    if src in ("exact", "nearest", "cached_quote"):
        return True
    if mq == "cached_recent" and src not in ("synthetic_modeled", "synthetic_proxy", "underlying_proxy", "delta_estimate"):
        return True
    return False


def _premium_hwm_allow_tline_single(
    cfg: Any,
    prem_src: str,
    detail: Dict[str, Any],
    *,
    prem: float,
    underlying_move_confirms: bool,
    synthetic_active: bool,
) -> bool:
    if _premium_updates_favorable_hwm(prem_src, detail, is_spread=False):
        return True
    if not underlying_move_confirms:
        return False
    if not tline_trusted_degraded_mark(
        cfg,
        prem_src=prem_src,
        mark_quality=str(detail.get("mark_quality") or ""),
        quote_age_seconds=detail.get("quote_age_seconds"),
        synthetic_active=bool(synthetic_active),
    ):
        return False
    if prem <= 0:
        return False
    return _tline_bid_ask_sanity_ok(detail, prem)


def _option_degraded_live_quote_should_emit(cfg: Any, dedupe_key: str) -> bool:
    """Throttle OPTION_DEGRADED_LIVE_QUOTE per dedupe_key (e.g. trade_id|symbol or skip|symbol)."""
    min_sec = float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0)
    if min_sec <= 0:
        return True
    key = f"deg|{dedupe_key}"
    now = time.monotonic()
    last = _option_degraded_live_quote_last.get(key, -1e30)
    if (now - last) < min_sec:
        return False
    _option_degraded_live_quote_last[key] = now
    return True


def _emit_option_degraded_live_quote_log(
    cfg: Any,
    *,
    symbol: str,
    trade_id: str,
    reason: str,
    action_taken: str,
    dedupe_key: str,
    force: bool = False,
) -> None:
    if not bool(getattr(cfg, "require_live_quotes", False)):
        return
    if not force and not _option_degraded_live_quote_should_emit(cfg, dedupe_key):
        return
    log.warning(
        "OPTION_DEGRADED_LIVE_QUOTE | symbol=%s | trade_id=%s | reason=%s | action_taken=%s",
        str(symbol or "-"),
        str(trade_id or "-"),
        str(reason or "unknown"),
        str(action_taken or "unknown"),
    )


def _option_price_resolution_audit_should_emit(
    cfg: Any,
    trade_id: str,
    position_symbol: str,
) -> bool:
    """Throttle OPTION_PRICE_RESOLUTION_AUDIT per trade_id + symbol."""
    min_sec = float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0)
    if min_sec <= 0:
        return True
    tid = str(trade_id or "").strip() or "-"
    sym = str(position_symbol or "").strip() or "-"
    key = f"{tid}|{sym}"
    now = time.monotonic()
    last = _option_price_resolution_audit_last.get(key, -1e30)
    if (now - last) < min_sec:
        return False
    _option_price_resolution_audit_last[key] = now
    return True


def _option_telemetry_log_should_emit(cfg: Any, cache: Dict[str, float], dedupe_key: str) -> bool:
    min_sec = float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0)
    if min_sec <= 0:
        return True
    now = time.monotonic()
    last = cache.get(dedupe_key, -1e30)
    if (now - last) < min_sec:
        return False
    cache[dedupe_key] = now
    return True


def _apply_synthetic_conservative_premium(
    raw_synthetic: float,
    *,
    last_valid_cap: Optional[float],
    is_short_premium: bool,
    cfg: Any,
) -> float:
    """Cap modeled premium vs last live; bias conservative for long vs short premium."""
    p = max(0.01, float(raw_synthetic))
    if last_valid_cap is not None and float(last_valid_cap) > 0:
        p = min(p, float(last_valid_cap))
    long_pct = max(0.02, min(0.05, float(getattr(cfg, "option_synthetic_long_devalue_pct", 0.035) or 0.035)))
    short_pct = max(0.02, min(0.05, float(getattr(cfg, "option_synthetic_short_penalize_pct", 0.035) or 0.035)))
    if is_short_premium:
        p *= 1.0 + short_pct
    else:
        p *= 1.0 - long_pct
    return max(0.01, p)


class OptionStealthMode(str, Enum):
    INACTIVE = "inactive"
    BREAKEVEN = "breakeven"
    TRAILING = "trailing"
    EXPLOSIVE = "explosive"
    MOON = "moon"


class OptionPositionType(str, Enum):
    SINGLE_LEG_LONG_CALL = "single_leg_long_call"
    SINGLE_LEG_LONG_PUT = "single_leg_long_put"
    DEBIT_SPREAD = "debit_spread"
    CREDIT_SPREAD = "credit_spread"


@dataclass
class OptionStealthConfig:
    breakeven_trigger_pct: float = 0.25
    breakeven_lock_pct: float = 0.08
    early_be_lock_pct: float = 0.04
    delta_itm_threshold: float = 0.60
    delta_atm_threshold: float = 0.40
    be_trigger_itm_pct: float = 0.15
    be_trigger_atm_pct: float = 0.20
    be_trigger_otm_pct: float = 0.28
    min_seconds_before_be: float = 60.0
    require_new_hwm_for_be: bool = True
    trailing_trigger_pct: float = 0.35
    base_trailing_pct: float = 0.22
    explosive_trailing_pct: float = 0.14
    moon_trailing_pct: float = 0.10
    explosive_pnl_threshold_pct: float = 0.50
    moon_pnl_threshold_pct: float = 1.00
    time_exit_minutes: float = 12.0
    no_progress_exit_minutes: float = 10.0
    structure_invalidation_enabled: bool = True
    disable_tp_ladder: bool = True
    structure_buffer_pct: float = 0.0005
    premium_move_sensitivity: float = 2.5
    premium_max_jump_pct: float = 0.55
    premium_max_mult_from_entry: float = 12.0
    premium_min_mult_from_entry: float = 0.02
    max_stale_seconds: float = 2.0
    premium_jump_recheck_pct: float = 0.15
    force_reeval_on_premium_jump: bool = True
    fast_fail_enabled: bool = True
    fast_fail_minutes: float = 3.0
    fast_fail_min_pnl_pct: float = 0.05
    adverse_guard_enabled: bool = True
    adverse_guard_min_minutes: float = 6.0
    adverse_guard_underlying_move_pct: float = 0.004
    adverse_guard_max_pnl_pct: float = 0.03
    spread_be_trigger_pct: float = 0.18
    spread_be_lock_pct: float = 0.04
    spread_trailing_trigger_pct: float = 0.28
    spread_base_trailing_pct: float = 0.12
    spread_time_exit_minutes: float = 9.0
    spread_no_progress_exit_minutes: float = 6.0
    # ORB 0DTE spread: if underlying keeps moving favorably, do not classify as no-progress.
    orb_spread_no_progress_favorable_underlying_move_pct: float = 0.0035
    # Exit-grade/staleness policy for timeout-style exits.
    timeout_min_good_mark_ticks: int = 3
    quote_exit_grade_max_age_seconds: float = 30.0
    quote_stale_max_age_seconds: float = 90.0
    freeze_timeouts_on_degraded_marks: bool = True
    require_exit_grade_for_timeouts: bool = True
    require_exit_grade_for_weak_exits: bool = True
    # ORB 0DTE: allow time/no-progress (and matching weak exits) without exit-grade / tick gates to avoid alertless stalls.
    orb_safety_exits_bypass_exit_grade_gates: bool = True
    no_progress_favorable_underlying_move_pct: float = 0.0035
    # ORB 0DTE-specific tuning overlays for faster lock/retention.
    orb_be_trigger_mult: float = 0.75
    orb_trailing_trigger_mult: float = 0.85
    orb_profit_lock_trigger_pct: float = 0.12
    orb_profit_lock_pct: float = 0.03
    # Trendline 0DTE: thesis can take >12m; optional overlays (0 time = use global time_exit).
    tline_time_exit_minutes: float = 240.0
    tline_no_progress_exit_minutes: float = 6.0
    tline_min_hold_seconds: float = 240.0
    tline_breakeven_activate_pct: float = 0.25
    tline_breakeven_offset_pct: float = 0.02
    tline_trail_activate_pct: float = 0.40
    tline_trail_giveback_pct: float = 0.30
    tline_impulse_breakeven_activate_pct: float = 0.20
    tline_impulse_trail_activate_pct: float = 0.35
    tline_impulse_trail_giveback_pct: float = 0.25
    tline_slow_trend_breakeven_activate_pct: float = 0.30
    tline_slow_trend_trail_activate_pct: float = 0.50
    tline_slow_trend_trail_giveback_pct: float = 0.35
    tline_retest_breakeven_activate_pct: float = 0.25
    tline_retest_trail_activate_pct: float = 0.40
    tline_retest_trail_giveback_pct: float = 0.30
    tline_be_trigger_mult: float = 0.50
    tline_trailing_trigger_mult: float = 0.82
    tline_profit_lock_trigger_pct: float = 0.08
    tline_profit_lock_pct: float = 0.02
    tline_be_delta_high_threshold: float = 0.50
    tline_be_delta_mid_threshold: float = 0.35
    tline_be_trigger_high_delta_pct: float = 0.10
    tline_be_trigger_mid_delta_pct: float = 0.12
    tline_be_trigger_low_delta_pct: float = 0.15
    tline_min_seconds_before_be: float = 60.0
    tline_base_trailing_pct: float = 0.275
    tline_explosive_trailing_pct: float = 0.18
    tline_runner_trailing_pct: float = 0.11
    tline_explosive_trigger_pct: float = 0.70
    tline_runner_trigger_pct: float = 1.20
    # Trendline single-leg: "early" no-progress scratch (runs before BE/trailing gates). Keep conservative:
    # do not cut while structure is still valid or underlying has moved favorably vs entry — see evaluate path.
    tline_no_progress_early_minutes: float = 7.0
    tline_no_progress_early_max_pnl_pct: float = 0.05
    tline_degraded_non_exact_tighten_after_seconds: float = 150.0
    tline_degraded_non_exact_trail_tighten_scale: float = 0.90
    tline_min_seconds_after_be_activation: float = 20.0
    tline_degraded_exit_min_duration_seconds: float = 10.0
    # Trendline single-leg: while pnl >= this, skip time_exit and no_progress exits (slow thesis grind).
    tline_chop_hold_min_pnl_pct: float = 0.10
    # Trendline impulse mode: quick capture profile.
    tline_impulse_tp_target_pct: float = 0.50
    tline_impulse_trailing_trigger_pct: float = 0.18
    tline_impulse_trailing_pct: float = 0.12
    tline_impulse_time_exit_minutes: float = 12.0
    tline_impulse_no_progress_exit_minutes: float = 5.0
    tline_early_trailing_trigger_pct: float = 0.30
    tline_early_trailing_pct: float = 0.24
    tline_early_time_exit_minutes: float = 30.0
    tline_aggressive_trailing_pct: float = 0.22
    tline_aggressive_trigger_pct: float = 0.55
    # Trendline 0DTE: optional giveback exit (mirrors ORB reversal protection, config-driven).
    tline_drawdown_exit_min_max_pnl_pct: float = 0.10
    tline_drawdown_exit_min_drawdown_pct: float = 0.07
    tline_option_no_data_grace_seconds: float = 180.0
    tline_option_force_exit_no_data_seconds: float = 300.0
    tline_option_require_exit_grade_before_force_exit: bool = True
    tline_min_diagnostic_hold_seconds: float = 120.0
    tline_no_data_favorable_underlying_move_pct: float = 0.0010
    require_live_option_data: bool = True
    # Strict single-leg pricing: never use delta/underlying proxy; hold last/entry until live bid/ask/mark.
    require_live_quotes: bool = False
    option_quote_fresh_max_age_seconds: float = 2.0
    option_0dte_fast_underlying_move_abs: float = 0.50
    option_0dte_fast_entry_minutes: float = 10.0
    option_price_resolution_audit_min_seconds: float = 30.0
    allow_safety_exits_on_degraded_data: bool = True
    no_data_outage_seconds: float = 90.0
    # Max seconds without a fresh live option quote (bid/ask/mark/last) before fail-safe exit (0DTE single-leg).
    option_max_degraded_seconds: float = 90.0
    option_synthetic_long_devalue_pct: float = 0.035
    option_synthetic_short_penalize_pct: float = 0.035
    position_health_log_sec: float = 60.0
    degraded_tighten_after_minutes: float = 5.0
    degraded_be_trigger_scale: float = 0.80
    degraded_trailing_trigger_scale: float = 0.80
    degraded_trailing_pct_scale: float = 0.75
    orb_spread_open_grace_seconds: float = 60.0
    orb_spread_open_grace_max_attempts: int = 6
    orb_spread_open_grace_enable_synthetic: bool = True
    # ORB 0DTE debit/credit spreads: avoid indefinite timeout holds when quotes stay partial_leg / non–exit-grade.
    orb_options_spread_degraded_exit_enable: bool = True
    orb_options_spread_conservative_exit_enable: bool = True
    orb_spread_conservative_min_trade_minutes: float = 5.0
    orb_spread_conservative_min_partial_minutes: float = 4.0
    orb_spread_conservative_min_deferrals: int = 15
    orb_spread_conservative_max_leg_age_sec: float = 240.0
    orb_spread_forced_min_trade_minutes: float = 28.0
    orb_spread_forced_min_partial_minutes: float = 20.0
    orb_spread_forced_min_deferrals: int = 55
    orb_spread_forced_watchdog_accum_seconds: float = 480.0
    orb_spread_forced_abs_max_minutes: float = 90.0
    orb_spread_forced_allow_incomplete_mtm: bool = True
    # ORB 0DTE spread: trusted degraded marks (partial_leg / cached paths) for timeout-style exits without HWM ratchet.
    orb_trusted_degraded_spread_enable: bool = True
    orb_trusted_degraded_mark_max_age_seconds: float = 90.0
    orb_trusted_degraded_min_fresh_leg_age_seconds: float = 45.0
    orb_underlying_opportunity_pct: float = 0.0025
    orb_degraded_protect_enable: bool = True
    orb_spread_degraded_time_exit_slack_minutes: float = 1.5
    orb_spread_degraded_no_progress_slack_minutes: float = 1.5
    orb_no_progress_relax_exit_grade_min_minutes: float = 11.0
    orb_no_progress_relax_max_mfe_pct: float = 0.06
    orb_watchdog_trusted_skip_clock_freeze: bool = True
    orb_degraded_drawdown_tighten_scale: float = 0.88
    # Unified 0DTE premium protections (applies via profile resolution across ORB + Trendline).
    max_pnl_drawdown_enabled: bool = True
    max_pnl_drawdown_trigger_pct: float = 0.10
    max_pnl_drawdown_exit_pct: float = 0.07
    micro_lock_enabled: bool = True
    micro_lock_trigger_pct: float = 0.08
    micro_lock_level_pct: float = -0.02
    profit_lock_trigger_pct: float = 0.12
    profit_lock_level_pct: float = 0.02
    orb_mfe_trigger_pct: float = 0.08
    orb_mfe_drawdown_exit_pct: float = 0.055
    tline_impulse_mfe_trigger_pct: float = 0.10
    tline_impulse_mfe_drawdown_exit_pct: float = 0.07
    tline_retest_mfe_trigger_pct: float = 0.12
    tline_retest_mfe_drawdown_exit_pct: float = 0.08
    tline_slow_mfe_trigger_pct: float = 0.15
    tline_slow_mfe_drawdown_exit_pct: float = 0.10
    # Trendline 0DTE: underlying excursion + min-hold bypass + trusted degraded marks (May 2026).
    tline_underlying_opportunity_pct: float = 0.0025
    tline_underlying_reclaim_protect: bool = True
    tline_impulse_trail_activation_cap_degraded: float = 0.22
    tline_min_hold_bypass_mfe_pct: float = 0.05
    tline_micro_lock_trigger_pct: float = 0.05
    tline_be_relax_hwm_with_underlying: bool = True
    tline_be_relax_hwm_min_pnl_pct: float = 0.02
    tline_no_progress_single_relax_exit_grade: bool = True
    tline_no_progress_single_max_mfe_pct: float = 0.05
    option_stealth_state_log_seconds: float = 15.0


@dataclass
class OptionExitProfile:
    source_path: str
    position_type: str
    entry_archetype: str
    be_trigger_pct: float
    be_lock_pct: float
    trail_trigger_pct: float
    trail_distance_pct: float
    profit_lock_trigger_pct: float
    profit_lock_pct: float
    max_pnl_drawdown_trigger_pct: float
    max_pnl_drawdown_exit_pct: float
    no_progress_minutes: float
    time_exit_minutes: float
    min_seconds_before_be: float
    min_hold_seconds: float
    allow_underlying_structure_exit: bool
    max_pnl_drawdown_enabled: bool = True
    micro_lock_enabled: bool = True
    explosive_pnl_threshold_pct: float = 0.50
    moon_pnl_threshold_pct: float = 1.00


def log_trendline_stealth_config_loaded(cfg: "OptionStealthConfig") -> None:
    """Emit resolved Trendline stealth knobs (defaults are explicit in code / env loader)."""
    log.info(
        "TRENDLINE_STEALTH_CONFIG_LOADED | tline_time_exit_minutes=%.2f | tline_no_progress_exit_minutes=%.2f | "
        "tline_degraded_exit_min_duration_seconds=%.2f | tline_min_seconds_after_be_activation=%.2f | "
        "tline_be_delta_high=%.3f | tline_be_delta_mid=%.3f | tline_be_trigger_high_delta_pct=%.4f | "
        "tline_be_trigger_mid_delta_pct=%.4f | tline_be_trigger_low_delta_pct=%.4f | "
        "tline_base_trailing_pct=%.4f | tline_aggressive_trailing_pct=%.4f | tline_explosive_trailing_pct=%.4f | "
        "tline_runner_trailing_pct=%.4f | tline_aggressive_trigger_pct=%.4f | tline_explosive_trigger_pct=%.4f | "
        "tline_runner_trigger_pct=%.4f | tline_drawdown_exit_min_max_pnl_pct=%.4f | tline_drawdown_exit_min_drawdown_pct=%.4f",
        float(cfg.tline_time_exit_minutes),
        float(cfg.tline_no_progress_exit_minutes),
        float(cfg.tline_degraded_exit_min_duration_seconds),
        float(cfg.tline_min_seconds_after_be_activation),
        float(cfg.tline_be_delta_high_threshold),
        float(cfg.tline_be_delta_mid_threshold),
        float(cfg.tline_be_trigger_high_delta_pct),
        float(cfg.tline_be_trigger_mid_delta_pct),
        float(cfg.tline_be_trigger_low_delta_pct),
        float(cfg.tline_base_trailing_pct),
        float(cfg.tline_aggressive_trailing_pct),
        float(cfg.tline_explosive_trailing_pct),
        float(cfg.tline_runner_trailing_pct),
        float(cfg.tline_aggressive_trigger_pct),
        float(cfg.tline_explosive_trigger_pct),
        float(cfg.tline_runner_trigger_pct),
        float(cfg.tline_drawdown_exit_min_max_pnl_pct),
        float(cfg.tline_drawdown_exit_min_drawdown_pct),
    )


def log_trendline_exit_config_audit(cfg: "OptionStealthConfig") -> None:
    """Cloud-friendly consolidated Trendline exit policy (grep=TRENDLINE_EXIT_CONFIG_AUDIT)."""
    log.info(
        "TRENDLINE_EXIT_CONFIG_AUDIT | grep=TRENDLINE_EXIT_CONFIG_AUDIT | "
        "tline_min_hold_seconds=%.1f | tline_be_delta_high=%.3f | tline_be_delta_mid=%.3f | "
        "tline_be_trigger_high_delta_pct=%.4f | tline_be_trigger_mid_delta_pct=%.4f | tline_be_trigger_low_delta_pct=%.4f | "
        "tline_impulse_trailing_trigger_pct=%.4f | tline_impulse_trailing_pct=%.4f | tline_impulse_trail_cap_degraded=%.4f | "
        "tline_profit_lock_trigger_pct=%.4f | tline_profit_lock_pct=%.4f | tline_micro_lock_trigger_pct=%.4f | "
        "tline_impulse_no_progress_min=%.2f | tline_no_progress_min=%.2f | tline_time_exit_min=%.2f | "
        "require_exit_grade_timeouts=%s | require_exit_grade_weak=%s | freeze_timeouts_degraded=%s | "
        "tline_underlying_opportunity_pct=%.5f | tline_underlying_reclaim_protect=%s | quote_stale_max_age_sec=%.1f | "
        "tline_no_progress_single_relax_exit_grade=%s | tline_no_progress_single_max_mfe_pct=%.4f | "
        "tline_be_relax_hwm_with_underlying=%s | tline_be_relax_hwm_min_pnl_pct=%.4f | tline_min_hold_bypass_mfe_pct=%.4f | "
        "option_stealth_state_log_seconds=%.1f",
        float(cfg.tline_min_hold_seconds),
        float(cfg.tline_be_delta_high_threshold),
        float(cfg.tline_be_delta_mid_threshold),
        float(cfg.tline_be_trigger_high_delta_pct),
        float(cfg.tline_be_trigger_mid_delta_pct),
        float(cfg.tline_be_trigger_low_delta_pct),
        float(cfg.tline_impulse_trailing_trigger_pct),
        float(cfg.tline_impulse_trailing_pct),
        float(cfg.tline_impulse_trail_activation_cap_degraded),
        float(cfg.tline_profit_lock_trigger_pct),
        float(cfg.tline_profit_lock_pct),
        float(cfg.tline_micro_lock_trigger_pct),
        float(cfg.tline_impulse_no_progress_exit_minutes),
        float(cfg.tline_no_progress_exit_minutes),
        float(cfg.tline_time_exit_minutes),
        str(bool(cfg.require_exit_grade_for_timeouts)).lower(),
        str(bool(cfg.require_exit_grade_for_weak_exits)).lower(),
        str(bool(cfg.freeze_timeouts_on_degraded_marks)).lower(),
        float(cfg.tline_underlying_opportunity_pct),
        str(bool(cfg.tline_underlying_reclaim_protect)).lower(),
        float(cfg.quote_stale_max_age_seconds),
        str(bool(cfg.tline_no_progress_single_relax_exit_grade)).lower(),
        float(cfg.tline_no_progress_single_max_mfe_pct),
        str(bool(cfg.tline_be_relax_hwm_with_underlying)).lower(),
        float(cfg.tline_be_relax_hwm_min_pnl_pct),
        float(cfg.tline_min_hold_bypass_mfe_pct),
        float(cfg.option_stealth_state_log_seconds),
    )


def load_option_stealth_config(
    get_config_value_fn: Optional[Callable[[str, Any], Any]] = None,
) -> OptionStealthConfig:
    g = get_config_value_fn or get_config_value

    def f(key: str, default: float) -> float:
        try:
            return float(g(key, default))
        except (TypeError, ValueError):
            return float(default)

    def i(key: str, default: float) -> float:
        return float(f(key, default))

    def b(key: str, default: bool) -> bool:
        v = g(key, default)
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        return bool(default)

    no_data_outage_default = f("OPTION_STEALTH_NO_DATA_OUTAGE_SECONDS", 90.0)

    _osc = OptionStealthConfig(
        breakeven_trigger_pct=f("OPTION_STEALTH_BREAKEVEN_TRIGGER_PCT", 0.25),
        breakeven_lock_pct=f("OPTION_STEALTH_BREAKEVEN_LOCK_PCT", 0.08),
        early_be_lock_pct=f("OPTION_STEALTH_EARLY_BE_LOCK_PCT", 0.04),
        delta_itm_threshold=f("OPTION_STEALTH_DELTA_ITM_THRESHOLD", 0.60),
        delta_atm_threshold=f("OPTION_STEALTH_DELTA_ATM_THRESHOLD", 0.40),
        be_trigger_itm_pct=f("OPTION_STEALTH_BE_TRIGGER_ITM_PCT", 0.15),
        be_trigger_atm_pct=f("OPTION_STEALTH_BE_TRIGGER_ATM_PCT", 0.20),
        be_trigger_otm_pct=f("OPTION_STEALTH_BE_TRIGGER_OTM_PCT", 0.28),
        min_seconds_before_be=f("OPTION_STEALTH_MIN_SECONDS_BEFORE_BE", 60.0),
        require_new_hwm_for_be=b("OPTION_STEALTH_REQUIRE_NEW_HWM_FOR_BE", True),
        trailing_trigger_pct=f("OPTION_STEALTH_TRAILING_TRIGGER_PCT", 0.35),
        base_trailing_pct=f("OPTION_STEALTH_BASE_TRAILING_PCT", 0.22),
        explosive_trailing_pct=f("OPTION_STEALTH_EXPLOSIVE_TRAILING_PCT", 0.14),
        moon_trailing_pct=f("OPTION_STEALTH_MOON_TRAILING_PCT", 0.10),
        time_exit_minutes=i("OPTION_STEALTH_TIME_EXIT_MINUTES", 12.0),
        no_progress_exit_minutes=i("OPTION_STEALTH_NO_PROGRESS_EXIT_MINUTES", 10.0),
        structure_invalidation_enabled=b("OPTION_STEALTH_STRUCTURE_INVALIDATION_ENABLED", True),
        disable_tp_ladder=b("OPTION_STEALTH_DISABLE_TP_LADDER", True),
        structure_buffer_pct=f("OPTION_STEALTH_STRUCTURE_BUFFER_PCT", 0.0005),
        premium_move_sensitivity=f("TRENDLINE_OPTION_SENSITIVITY", 2.5),
        premium_max_jump_pct=f("OPTION_STEALTH_PREMIUM_MAX_JUMP_PCT", 0.55),
        premium_max_mult_from_entry=f("OPTION_STEALTH_PREMIUM_MAX_MULT", 12.0),
        premium_min_mult_from_entry=f("OPTION_STEALTH_PREMIUM_MIN_MULT", 0.02),
        max_stale_seconds=f("OPTION_STEALTH_MAX_STALE_SECONDS", 2.0),
        premium_jump_recheck_pct=f("OPTION_STEALTH_PREMIUM_JUMP_RECHECK_PCT", 0.15),
        force_reeval_on_premium_jump=b("OPTION_STEALTH_FORCE_REEVAL_ON_PREMIUM_JUMP", True),
        fast_fail_enabled=b("OPTION_STEALTH_FAST_FAIL_ENABLE", True),
        fast_fail_minutes=f("OPTION_STEALTH_FAST_FAIL_MINUTES", 3.0),
        fast_fail_min_pnl_pct=f("OPTION_STEALTH_FAST_FAIL_MIN_PNL_PCT", 0.05),
        adverse_guard_enabled=b("OPTION_STEALTH_ADVERSE_GUARD_ENABLE", True),
        adverse_guard_min_minutes=f("OPTION_STEALTH_ADVERSE_GUARD_MIN_MINUTES", 6.0),
        adverse_guard_underlying_move_pct=f("OPTION_STEALTH_ADVERSE_GUARD_UNDERLYING_MOVE_PCT", 0.004),
        adverse_guard_max_pnl_pct=f("OPTION_STEALTH_ADVERSE_GUARD_MAX_PNL_PCT", 0.03),
        spread_be_trigger_pct=f("OPTION_STEALTH_SPREAD_BE_TRIGGER_PCT", 0.18),
        spread_be_lock_pct=f("OPTION_STEALTH_SPREAD_BE_LOCK_PCT", 0.04),
        spread_trailing_trigger_pct=f("OPTION_STEALTH_SPREAD_TRAILING_TRIGGER_PCT", 0.28),
        spread_base_trailing_pct=f("OPTION_STEALTH_SPREAD_BASE_TRAILING_PCT", 0.12),
        spread_time_exit_minutes=i("OPTION_STEALTH_SPREAD_TIME_EXIT_MINUTES", 9.0),
        spread_no_progress_exit_minutes=i("OPTION_STEALTH_SPREAD_NO_PROGRESS_EXIT_MINUTES", 6.0),
        orb_spread_no_progress_favorable_underlying_move_pct=f(
            "OPTION_STEALTH_ORB_SPREAD_NO_PROGRESS_FAVORABLE_UNDERLYING_MOVE_PCT", 0.0035
        ),
        timeout_min_good_mark_ticks=int(max(1, f("OPTION_STEALTH_TIMEOUT_MIN_GOOD_MARK_TICKS", 3))),
        quote_exit_grade_max_age_seconds=f("OPTION_QUOTE_EXIT_GRADE_MAX_AGE_SECONDS", 30.0),
        quote_stale_max_age_seconds=f("OPTION_QUOTE_STALE_MAX_AGE_SECONDS", 90.0),
        freeze_timeouts_on_degraded_marks=b("OPTION_STEALTH_FREEZE_TIMEOUTS_ON_DEGRADED_MARKS", True),
        require_exit_grade_for_timeouts=b("OPTION_STEALTH_REQUIRE_EXIT_GRADE_FOR_TIMEOUTS", True),
        require_exit_grade_for_weak_exits=b("OPTION_STEALTH_REQUIRE_EXIT_GRADE_FOR_WEAK_EXITS", True),
        orb_safety_exits_bypass_exit_grade_gates=b("ORB_OPTIONS_SAFETY_EXIT_BYPASS_EXIT_GRADE", True),
        no_progress_favorable_underlying_move_pct=f(
            "OPTION_STEALTH_NO_PROGRESS_FAVORABLE_UNDERLYING_MOVE_PCT", 0.0035
        ),
        orb_be_trigger_mult=f("OPTION_STEALTH_ORB_BE_TRIGGER_MULT", 0.75),
        orb_trailing_trigger_mult=f("OPTION_STEALTH_ORB_TRAILING_TRIGGER_MULT", 0.85),
        orb_profit_lock_trigger_pct=f("OPTION_STEALTH_ORB_PROFIT_LOCK_TRIGGER_PCT", 0.12),
        orb_profit_lock_pct=f("OPTION_STEALTH_ORB_PROFIT_LOCK_PCT", 0.03),
        tline_time_exit_minutes=f("OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES", 240.0),
        tline_no_progress_exit_minutes=f("OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES", 6.0),
        tline_min_hold_seconds=f("TRENDLINE_EXIT_MIN_HOLD_SECONDS", 240.0),
        tline_breakeven_activate_pct=f("TRENDLINE_BREAKEVEN_ACTIVATE_PCT", 0.25),
        tline_breakeven_offset_pct=f("TRENDLINE_BREAKEVEN_OFFSET_PCT", 0.02),
        tline_trail_activate_pct=f("TRENDLINE_TRAIL_ACTIVATE_PCT", 0.40),
        tline_trail_giveback_pct=f("TRENDLINE_TRAIL_GIVEBACK_PCT", 0.30),
        tline_impulse_breakeven_activate_pct=f("TRENDLINE_IMPULSE_BREAKEVEN_ACTIVATE_PCT", 0.20),
        tline_impulse_trail_activate_pct=f("TRENDLINE_IMPULSE_TRAIL_ACTIVATE_PCT", 0.35),
        tline_impulse_trail_giveback_pct=f("TRENDLINE_IMPULSE_TRAIL_GIVEBACK_PCT", 0.25),
        tline_slow_trend_breakeven_activate_pct=f("TRENDLINE_SLOW_TREND_BREAKEVEN_ACTIVATE_PCT", 0.30),
        tline_slow_trend_trail_activate_pct=f("TRENDLINE_SLOW_TREND_TRAIL_ACTIVATE_PCT", 0.50),
        tline_slow_trend_trail_giveback_pct=f("TRENDLINE_SLOW_TREND_TRAIL_GIVEBACK_PCT", 0.35),
        tline_retest_breakeven_activate_pct=f("TRENDLINE_RETEST_BREAKEVEN_ACTIVATE_PCT", 0.25),
        tline_retest_trail_activate_pct=f("TRENDLINE_RETEST_TRAIL_ACTIVATE_PCT", 0.40),
        tline_retest_trail_giveback_pct=f("TRENDLINE_RETEST_TRAIL_GIVEBACK_PCT", 0.30),
        tline_be_trigger_mult=f("OPTION_STEALTH_TLINE_BE_TRIGGER_MULT", 0.50),
        tline_trailing_trigger_mult=f("OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT", 0.82),
        tline_profit_lock_trigger_pct=f("OPTION_STEALTH_TLINE_PROFIT_LOCK_TRIGGER_PCT", 0.08),
        tline_profit_lock_pct=f("OPTION_STEALTH_TLINE_PROFIT_LOCK_PCT", 0.02),
        tline_be_delta_high_threshold=f("OPTION_STEALTH_TLINE_BE_DELTA_HIGH_THRESHOLD", 0.50),
        tline_be_delta_mid_threshold=f("OPTION_STEALTH_TLINE_BE_DELTA_MID_THRESHOLD", 0.35),
        tline_be_trigger_high_delta_pct=f("OPTION_STEALTH_TLINE_BE_TRIGGER_HIGH_DELTA_PCT", 0.10),
        tline_be_trigger_mid_delta_pct=f("OPTION_STEALTH_TLINE_BE_TRIGGER_MID_DELTA_PCT", 0.12),
        tline_be_trigger_low_delta_pct=f("OPTION_STEALTH_TLINE_BE_TRIGGER_LOW_DELTA_PCT", 0.15),
        tline_min_seconds_before_be=f("OPTION_STEALTH_TLINE_MIN_SECONDS_BEFORE_BE", 60.0),
        tline_base_trailing_pct=f("OPTION_STEALTH_TLINE_BASE_TRAILING_PCT", 0.275),
        tline_explosive_trailing_pct=f("OPTION_STEALTH_TLINE_EXPLOSIVE_TRAILING_PCT", 0.18),
        tline_runner_trailing_pct=f("OPTION_STEALTH_TLINE_RUNNER_TRAILING_PCT", 0.11),
        tline_explosive_trigger_pct=f("OPTION_STEALTH_TLINE_EXPLOSIVE_TRIGGER_PCT", 0.70),
        tline_runner_trigger_pct=f("OPTION_STEALTH_TLINE_RUNNER_TRIGGER_PCT", 1.20),
        tline_no_progress_early_minutes=f("OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MINUTES", 7.0),
        tline_no_progress_early_max_pnl_pct=f("OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MAX_PNL_PCT", 0.05),
        tline_degraded_non_exact_tighten_after_seconds=f("OPTION_STEALTH_TLINE_DEGRADED_NON_EXACT_TIGHTEN_AFTER_SECONDS", 150.0),
        tline_degraded_non_exact_trail_tighten_scale=f("OPTION_STEALTH_TLINE_DEGRADED_NON_EXACT_TRAIL_TIGHTEN_SCALE", 0.90),
        tline_min_seconds_after_be_activation=f("OPTION_STEALTH_TLINE_MIN_SECONDS_AFTER_BE_ACTIVATION", 20.0),
        tline_degraded_exit_min_duration_seconds=f("OPTION_STEALTH_TLINE_DEGRADED_EXIT_MIN_DURATION", 10.0),
        tline_chop_hold_min_pnl_pct=f("OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT", 0.10),
        tline_impulse_tp_target_pct=f("OPTION_STEALTH_TLINE_IMPULSE_TP_TARGET_PCT", 0.50),
        tline_impulse_trailing_trigger_pct=f("OPTION_STEALTH_TLINE_IMPULSE_TRAILING_TRIGGER_PCT", 0.18),
        tline_impulse_trailing_pct=f("OPTION_STEALTH_TLINE_IMPULSE_TRAILING_PCT", 0.12),
        tline_impulse_time_exit_minutes=f("OPTION_STEALTH_TLINE_IMPULSE_TIME_EXIT_MINUTES", 12.0),
        tline_impulse_no_progress_exit_minutes=f("OPTION_STEALTH_TLINE_IMPULSE_NO_PROGRESS_EXIT_MINUTES", 5.0),
        tline_early_trailing_trigger_pct=f("OPTION_STEALTH_TLINE_EARLY_TRAILING_TRIGGER_PCT", 0.30),
        tline_early_trailing_pct=f("OPTION_STEALTH_TLINE_EARLY_TRAILING_PCT", 0.24),
        tline_early_time_exit_minutes=f("OPTION_STEALTH_TLINE_EARLY_TIME_EXIT_MINUTES", 30.0),
        tline_aggressive_trailing_pct=f("OPTION_STEALTH_TLINE_AGGRESSIVE_TRAILING_PCT", 0.22),
        tline_aggressive_trigger_pct=f("OPTION_STEALTH_TLINE_AGGRESSIVE_TRIGGER_PCT", 0.55),
        tline_drawdown_exit_min_max_pnl_pct=f("OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_MAX_PNL_PCT", 0.10),
        tline_drawdown_exit_min_drawdown_pct=f("OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_DRAWDOWN_PCT", 0.07),
        tline_option_no_data_grace_seconds=f("TRENDLINE_OPTION_NO_DATA_GRACE_SECONDS", 180.0),
        tline_option_force_exit_no_data_seconds=f("TRENDLINE_OPTION_FORCE_EXIT_NO_DATA_SECONDS", 300.0),
        tline_option_require_exit_grade_before_force_exit=b(
            "TRENDLINE_OPTION_REQUIRE_EXIT_GRADE_BEFORE_FORCE_EXIT", True
        ),
        tline_min_diagnostic_hold_seconds=f("TRENDLINE_MIN_DIAGNOSTIC_HOLD_SECONDS", 120.0),
        tline_no_data_favorable_underlying_move_pct=f(
            "TRENDLINE_OPTION_NO_DATA_FAVORABLE_UNDERLYING_MOVE_PCT", 0.0010
        ),
        require_live_option_data=b("OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA", True),
        require_live_quotes=b("OPTION_REQUIRE_LIVE_QUOTES", False),
        option_quote_fresh_max_age_seconds=f("OPTION_QUOTE_FRESH_MAX_AGE_SECONDS", 2.0),
        option_0dte_fast_underlying_move_abs=f("OPTION_0DTE_FAST_UNDERLYING_MOVE_ABS", 0.50),
        option_0dte_fast_entry_minutes=f("OPTION_0DTE_FAST_ENTRY_MINUTES", 10.0),
        option_price_resolution_audit_min_seconds=f("OPTION_PRICE_RESOLUTION_AUDIT_MIN_SECONDS", 30.0),
        allow_safety_exits_on_degraded_data=b("OPTION_STEALTH_ALLOW_SAFETY_EXITS_ON_DEGRADED_DATA", True),
        no_data_outage_seconds=no_data_outage_default,
        option_max_degraded_seconds=f("OPTION_MAX_DEGRADED_SECONDS", no_data_outage_default),
        option_synthetic_long_devalue_pct=max(
            0.02,
            min(0.05, f("OPTION_SYNTHETIC_LONG_DEVALUE_PCT", 0.035)),
        ),
        option_synthetic_short_penalize_pct=max(
            0.02,
            min(0.05, f("OPTION_SYNTHETIC_SHORT_PENALIZE_PCT", 0.035)),
        ),
        position_health_log_sec=f("OPTION_STEALTH_POSITION_HEALTH_LOG_SEC", 60.0),
        degraded_tighten_after_minutes=f("OPTION_STEALTH_DEGRADED_TIGHTEN_AFTER_MINUTES", 5.0),
        degraded_be_trigger_scale=f("OPTION_STEALTH_DEGRADED_BE_TRIGGER_SCALE", 0.80),
        degraded_trailing_trigger_scale=f("OPTION_STEALTH_DEGRADED_TRAILING_TRIGGER_SCALE", 0.80),
        degraded_trailing_pct_scale=f("OPTION_STEALTH_DEGRADED_TRAILING_PCT_SCALE", 0.75),
        orb_spread_open_grace_seconds=f("ORB_0DTE_SPREAD_OPEN_GRACE_SECONDS", 60.0),
        orb_spread_open_grace_max_attempts=int(max(1, f("ORB_0DTE_SPREAD_OPEN_GRACE_MAX_ATTEMPTS", 6))),
        orb_spread_open_grace_enable_synthetic=b("ORB_0DTE_SPREAD_OPEN_GRACE_ENABLE_SYNTHETIC", True),
        orb_options_spread_degraded_exit_enable=b("ORB_OPTIONS_SPREAD_DEGRADED_EXIT_ENABLE", True),
        orb_options_spread_conservative_exit_enable=b("ORB_OPTIONS_SPREAD_CONSERVATIVE_EXIT_ENABLE", True),
        orb_spread_conservative_min_trade_minutes=f("ORB_SPREAD_CONSERVATIVE_EXIT_MIN_TRADE_MINUTES", 5.0),
        orb_spread_conservative_min_partial_minutes=f("ORB_SPREAD_CONSERVATIVE_EXIT_MIN_PARTIAL_MINUTES", 4.0),
        orb_spread_conservative_min_deferrals=int(max(1, f("ORB_SPREAD_CONSERVATIVE_EXIT_MIN_DEFERRALS", 15))),
        orb_spread_conservative_max_leg_age_sec=f("ORB_SPREAD_CONSERVATIVE_EXIT_MAX_LEG_AGE_SECONDS", 240.0),
        orb_spread_forced_min_trade_minutes=f("ORB_SPREAD_FORCED_DEGRADED_EXIT_MIN_TRADE_MINUTES", 28.0),
        orb_spread_forced_min_partial_minutes=f("ORB_SPREAD_FORCED_DEGRADED_MIN_PARTIAL_LEG_MINUTES", 20.0),
        orb_spread_forced_min_deferrals=int(max(1, f("ORB_SPREAD_FORCED_DEGRADED_MIN_DEFERRALS", 55))),
        orb_spread_forced_watchdog_accum_seconds=f("ORB_SPREAD_FORCED_DEGRADED_WATCHDOG_ACCUM_SECONDS", 480.0),
        orb_spread_forced_abs_max_minutes=f("ORB_SPREAD_FORCED_DEGRADED_ABS_MAX_MINUTES", 90.0),
        orb_spread_forced_allow_incomplete_mtm=b("ORB_SPREAD_FORCED_ALLOW_INCOMPLETE_MTM", True),
        orb_trusted_degraded_spread_enable=b("OPTION_STEALTH_ORB_TRUSTED_DEGRADED_SPREAD_ENABLE", True),
        orb_trusted_degraded_mark_max_age_seconds=f(
            "OPTION_STEALTH_ORB_TRUSTED_DEGRADED_MAX_MARK_AGE_SECONDS", 90.0
        ),
        orb_trusted_degraded_min_fresh_leg_age_seconds=f(
            "OPTION_STEALTH_ORB_TRUSTED_DEGRADED_MIN_FRESH_LEG_AGE_SECONDS", 45.0
        ),
        orb_underlying_opportunity_pct=f("OPTION_STEALTH_ORB_UNDERLYING_OPPORTUNITY_PCT", 0.0025),
        orb_degraded_protect_enable=b("OPTION_STEALTH_ORB_DEGRADED_PROTECT", True),
        orb_spread_degraded_time_exit_slack_minutes=f("OPTION_STEALTH_ORB_DEGRADED_RUNNING_TIME_EXIT_SLACK_MIN", 1.5),
        orb_spread_degraded_no_progress_slack_minutes=f(
            "OPTION_STEALTH_ORB_DEGRADED_RUNNING_NO_PROGRESS_SLACK_MIN", 1.5
        ),
        orb_no_progress_relax_exit_grade_min_minutes=f("OPTION_STEALTH_ORB_NO_PROGRESS_RELAX_MIN_MINUTES", 11.0),
        orb_no_progress_relax_max_mfe_pct=f("OPTION_STEALTH_ORB_NO_PROGRESS_RELAX_MAX_MFE_PCT", 0.06),
        orb_watchdog_trusted_skip_clock_freeze=b("OPTION_STEALTH_ORB_WATCHDOG_TRUSTED_SKIP_CLOCK_FREEZE", True),
        orb_degraded_drawdown_tighten_scale=f("OPTION_STEALTH_ORB_DEGRADED_DRAWDOWN_TIGHTEN_SCALE", 0.88),
        max_pnl_drawdown_enabled=b("OPTION_STEALTH_MAX_PNL_DRAWDOWN_ENABLED", True),
        max_pnl_drawdown_trigger_pct=f(
            "OPTION_STEALTH_MAX_PNL_DRAWDOWN_TRIGGER_PCT",
            f("OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_MAX_PNL_PCT", 0.10),
        ),
        max_pnl_drawdown_exit_pct=f(
            "OPTION_STEALTH_MAX_PNL_DRAWDOWN_EXIT_PCT",
            f("OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_DRAWDOWN_PCT", 0.07),
        ),
        micro_lock_enabled=b("OPTION_STEALTH_MICRO_LOCK_ENABLED", True),
        micro_lock_trigger_pct=f("OPTION_STEALTH_MICRO_LOCK_TRIGGER_PCT", 0.08),
        micro_lock_level_pct=f("OPTION_STEALTH_MICRO_LOCK_LEVEL_PCT", -0.02),
        profit_lock_trigger_pct=f(
            "OPTION_STEALTH_PROFIT_LOCK_TRIGGER_PCT",
            f("OPTION_STEALTH_ORB_PROFIT_LOCK_TRIGGER_PCT", 0.12),
        ),
        profit_lock_level_pct=f(
            "OPTION_STEALTH_PROFIT_LOCK_LEVEL_PCT",
            f("OPTION_STEALTH_ORB_PROFIT_LOCK_PCT", 0.02),
        ),
        orb_mfe_trigger_pct=f("OPTION_STEALTH_ORB_MFE_TRIGGER_PCT", 0.08),
        orb_mfe_drawdown_exit_pct=f("OPTION_STEALTH_ORB_MFE_DRAWDOWN_EXIT_PCT", 0.055),
        tline_impulse_mfe_trigger_pct=f("OPTION_STEALTH_TLINE_IMPULSE_MFE_TRIGGER_PCT", 0.10),
        tline_impulse_mfe_drawdown_exit_pct=f("OPTION_STEALTH_TLINE_IMPULSE_MFE_DRAWDOWN_EXIT_PCT", 0.07),
        tline_retest_mfe_trigger_pct=f("OPTION_STEALTH_TLINE_RETEST_MFE_TRIGGER_PCT", 0.12),
        tline_retest_mfe_drawdown_exit_pct=f("OPTION_STEALTH_TLINE_RETEST_MFE_DRAWDOWN_EXIT_PCT", 0.08),
        tline_slow_mfe_trigger_pct=f("OPTION_STEALTH_TLINE_SLOW_MFE_TRIGGER_PCT", 0.15),
        tline_slow_mfe_drawdown_exit_pct=f("OPTION_STEALTH_TLINE_SLOW_MFE_DRAWDOWN_EXIT_PCT", 0.10),
        tline_underlying_opportunity_pct=f("OPTION_STEALTH_TLINE_UNDERLYING_OPPORTUNITY_PCT", 0.0025),
        tline_underlying_reclaim_protect=b("OPTION_STEALTH_TLINE_UNDERLYING_RECLAIM_PROTECT", True),
        tline_impulse_trail_activation_cap_degraded=f(
            "OPTION_STEALTH_TLINE_IMPULSE_TRAIL_ACTIVATION_CAP_DEGRADED", 0.22
        ),
        tline_min_hold_bypass_mfe_pct=f("OPTION_STEALTH_TLINE_MIN_HOLD_BYPASS_MFE_PCT", 0.05),
        tline_micro_lock_trigger_pct=f("OPTION_STEALTH_TLINE_MICRO_LOCK_TRIGGER_PCT", 0.05),
        tline_be_relax_hwm_with_underlying=b("OPTION_STEALTH_TLINE_BE_RELAX_HWM_WITH_UNDERLYING", True),
        tline_be_relax_hwm_min_pnl_pct=f("OPTION_STEALTH_TLINE_BE_RELAX_HWM_MIN_PNL_PCT", 0.02),
        tline_no_progress_single_relax_exit_grade=b(
            "OPTION_STEALTH_TLINE_NO_PROGRESS_SINGLE_RELAX_EXIT_GRADE", True
        ),
        tline_no_progress_single_max_mfe_pct=f("OPTION_STEALTH_TLINE_NO_PROGRESS_SINGLE_MAX_MFE_PCT", 0.05),
        option_stealth_state_log_seconds=f("OPTION_STEALTH_STATE_LOG_SECONDS", 15.0),
        explosive_pnl_threshold_pct=f("OPTION_STEALTH_EXPLOSIVE_PNL_THRESHOLD_PCT", 0.50),
        moon_pnl_threshold_pct=f("OPTION_STEALTH_MOON_PNL_THRESHOLD_PCT", 1.00),
    )
    log_trendline_stealth_config_loaded(_osc)
    log_trendline_exit_config_audit(_osc)
    return _osc


@dataclass
class OptionStealthState:
    mode: OptionStealthMode = OptionStealthMode.INACTIVE
    entry_premium: float = 0.0
    premium_hwm: float = 0.0
    premium_lwm: float = 0.0
    breakeven_floor: Optional[float] = None
    breakeven_activated_at: Optional[datetime] = None
    trailing_activated_at: Optional[datetime] = None
    trail_stop_premium: Optional[float] = None
    last_hwm_at: Optional[datetime] = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trendline: Dict[str, Any] = field(default_factory=dict)
    option_side: str = ""
    line_geometry: str = ""
    delta_at_entry: float = 0.0
    strike: float = 0.0
    last_effective_premium: float = 0.0
    last_premium_source: str = ""
    last_trail_pct: float = 0.0
    structure_invalidation_triggered: bool = False
    structure_invalidation_reason: str = ""
    no_progress_timeout_triggered: bool = False
    favorable_advance_seen: bool = False
    max_pnl_pct: float = 0.0
    had_opportunity: bool = False
    had_underlying_opportunity: bool = False
    max_underlying_favorable_move_pct: float = 0.0
    trade_behavior: str = "unknown"
    last_update_at: Optional[datetime] = None
    last_underlying_update_at: Optional[datetime] = None
    last_premium_update_at: Optional[datetime] = None
    stale_data_detected: bool = False
    premium_jump_detected: bool = False
    be_trigger_pct_used: Optional[float] = None
    be_lock_pct_used: Optional[float] = None
    delta_bucket: str = "unknown"
    position_type: str = OptionPositionType.SINGLE_LEG_LONG_CALL.value
    strategy_name: str = ""
    strategy_type: str = ""
    entry_value: float = 0.0
    entry_debit: Optional[float] = None
    entry_credit: Optional[float] = None
    leg_count: int = 0
    legs: List[Dict[str, Any]] = field(default_factory=list)
    spread_hwm: float = 0.0
    spread_lwm: float = 0.0
    structure_invalidation_enabled_override: Optional[bool] = None
    trendline_mode: str = "STANDARD"
    impulse_mode: bool = False
    break_archetype: str = ""
    good_mark_tick_count: int = 0
    timeout_clock_frozen: bool = False
    timeout_frozen_seconds: float = 0.0
    timeout_clock_last_resumed_at: Optional[datetime] = None
    timeout_clock_last_frozen_at: Optional[datetime] = None
    watchdog_gap_seen: bool = False


def _modeled_option_premium(
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    base_sensitivity: float,
    delta_at_entry: float,
    strike: float,
) -> float:
    """Lightweight delta- and moneyness-aware proxy (not a full Greek model)."""
    if entry_underlying <= 0 or current_underlying <= 0 or entry_premium <= 0:
        return max(0.01, float(entry_premium))
    side = str(option_side).lower()
    ref_delta = max(0.08, min(0.55, float(delta_at_entry or 0.30)))
    if side == "put":
        move = (entry_underlying - current_underlying) / entry_underlying
        m0 = (strike - entry_underlying) / max(entry_underlying, 1e-9)
        m1 = (strike - current_underlying) / max(current_underlying, 1e-9)
    else:
        move = (current_underlying - entry_underlying) / entry_underlying
        m0 = (entry_underlying - strike) / max(entry_underlying, 1e-9)
        m1 = (current_underlying - strike) / max(current_underlying, 1e-9)
    drift = 0.40 * max(0.0, m1 - m0) - 0.18 * max(0.0, m0 - m1)
    delta_eff = ref_delta + drift
    delta_eff = max(0.06, min(0.92, delta_eff))
    scale = base_sensitivity * (delta_eff / 0.35)
    raw_mult = 1.0 + move * scale
    prem = float(entry_premium) * raw_mult
    return max(0.01, prem)


def _clamp_premium_step(
    candidate: float,
    previous: Optional[float],
    entry_premium: float,
    cfg: OptionStealthConfig,
) -> float:
    """Limit unrealistic tick-to-tick jumps while allowing trend."""
    p = max(0.01, float(candidate))
    ep = max(0.01, float(entry_premium))
    p = min(p, ep * max(0.05, cfg.premium_max_mult_from_entry))
    p = max(p, ep * cfg.premium_min_mult_from_entry)
    if previous is not None and previous > 0:
        lo = previous * (1.0 - cfg.premium_max_jump_pct)
        hi = previous * (1.0 + cfg.premium_max_jump_pct)
        p = min(hi, max(lo, p))
    return max(0.01, p)


def _underlying_proxy_premium(
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    cfg: OptionStealthConfig,
) -> float:
    """Underlying-only proxy used when delta-aware estimate cannot be trusted."""
    if entry_underlying <= 0 or current_underlying <= 0:
        return max(0.01, float(entry_premium))
    side = str(option_side or "").lower()
    move = (
        (current_underlying - entry_underlying) / entry_underlying
        if side == "call"
        else (entry_underlying - current_underlying) / entry_underlying
    )
    raw_mult = 1.0 + move * max(0.1, float(cfg.premium_move_sensitivity) * 0.45)
    return max(0.01, float(entry_premium) * raw_mult)


def resolve_option_price(
    *,
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    cfg: OptionStealthConfig,
    option_quote: Optional[Dict[str, Any]],
    previous_effective: Optional[float],
    delta_at_entry: float,
    strike: float,
    position_symbol: str,
    is_0dte: bool = False,
    seconds_since_entry: Optional[float] = None,
    trade_id: str = "",
    stored_last_valid_option_price: Optional[float] = None,
    is_short_premium: bool = False,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Resolution order (non-0DTE unchanged):
      1) mid from bid/ask
      2) option last
      3) cached_recent_quote (<= option_quote_fresh_max_age_seconds)
      4) delta_estimate / underlying_proxy (or strict hold when require_live_quotes)

    0DTE single-leg tier when fresh live quote unavailable:
      A) last_valid_option_price from monitor snapshot (stale OK)
      B) synthetic modeled (delta) or underlying proxy, conservatively capped/biased
    """
    modeled = _modeled_option_premium(
        entry_underlying,
        current_underlying,
        option_side,
        entry_premium,
        cfg.premium_move_sensitivity,
        delta_at_entry,
        strike,
    )
    proxy = _underlying_proxy_premium(
        entry_underlying,
        current_underlying,
        option_side,
        entry_premium,
        cfg,
    )
    detail: Dict[str, Any] = {
        "option_bid": None,
        "option_ask": None,
        "option_mark": None,
        "option_last": None,
        "modeled_premium": round(modeled, 6),
        "underlying_proxy_premium": round(proxy, 6),
        "effective_premium_used": None,
        "premium_source": "delta_estimate",
        "quote_age_seconds": None,
        "spread_pct": None,
        "fallback_used": True,
        "clamp_skipped_fast_market": False,
        "pricing_degraded": False,
        "live_quotes_strict_block": False,
        "quote_slice": None,
        "live_degraded_reason": None,
        "price_source_telemetry": None,
        "synthetic_pricing_active": False,
        "stale_last_valid_option": False,
    }

    def _f(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    source = "delta_estimate"
    raw_effective: float = modeled

    if option_quote and isinstance(option_quote, dict):
        bid = _f(option_quote.get("bid"))
        ask = _f(option_quote.get("ask"))
        last = _f(option_quote.get("last"))
        mark = _f(option_quote.get("mark")) or _f(option_quote.get("mid_price"))
        quote_age = _f(
            option_quote.get("quote_age_seconds")
            or option_quote.get("age_seconds")
            or option_quote.get("quote_age")
        )
        max_age = float(max(0.05, cfg.option_quote_fresh_max_age_seconds))
        if quote_age is None:
            ts_raw = option_quote.get("quote_timestamp") or option_quote.get("timestamp")
            if isinstance(ts_raw, str):
                try:
                    qdt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    quote_age = max(
                        0.0,
                        (datetime.now(timezone.utc) - qdt.astimezone(timezone.utc)).total_seconds(),
                    )
                except Exception:
                    quote_age = None
        quote_is_fresh = (quote_age is None) or (float(quote_age) <= max_age)
        detail["option_bid"] = bid
        detail["option_ask"] = ask
        detail["option_last"] = last
        detail["quote_age_seconds"] = quote_age
        spread_pct = None
        if bid is not None and ask is not None and (bid + ask) > 0:
            spread_pct = abs(ask - bid) / ((ask + bid) / 2.0)
        detail["spread_pct"] = spread_pct
        quote_hint = str(option_quote.get("quote_source") or "").strip().lower()
        if mark is not None and quote_is_fresh:
            detail["option_mark"] = mark
            raw_effective = mark
            source = "nearest" if quote_hint == "nearest" else "exact"
            detail["quote_slice"] = "mark"
        elif bid is not None and ask is not None and quote_is_fresh:
            mid = (bid + ask) / 2.0
            if mid > 0:
                raw_effective = mid
                source = "nearest" if quote_hint == "nearest" else "exact"
                detail["quote_slice"] = "mid"
        elif last is not None and quote_is_fresh:
            raw_effective = last
            source = "nearest" if quote_hint == "nearest" else "exact"
            detail["quote_slice"] = "last"
        else:
            cached_price = _f(option_quote.get("cached_price"))
            cached_age = _f(option_quote.get("cached_age_seconds"))
            if cached_price is not None and cached_age is not None and cached_age <= max_age:
                raw_effective = cached_price
                source = "cached_quote"
                detail["quote_age_seconds"] = cached_age
                detail["quote_slice"] = "cached_recent"

    if is_0dte and source in ("delta_estimate", "underlying_proxy"):
        slv = _f(stored_last_valid_option_price) if stored_last_valid_option_price is not None else None
        if slv is not None:
            raw_effective = float(slv)
            source = "last_valid_stale"
            detail["quote_slice"] = "last_valid_stale"
            detail["price_source_telemetry"] = "last_valid"
            detail["stale_last_valid_option"] = True
            detail["synthetic_pricing_active"] = False
        else:
            use_modeled = isinstance(delta_at_entry, (int, float)) and float(delta_at_entry) > 0
            base_syn = float(modeled) if use_modeled else float(proxy)
            src_syn = "synthetic_modeled" if use_modeled else "synthetic_proxy"
            raw_effective = _apply_synthetic_conservative_premium(
                base_syn,
                last_valid_cap=None,
                is_short_premium=bool(is_short_premium),
                cfg=cfg,
            )
            source = src_syn
            detail["quote_slice"] = "synthetic"
            detail["price_source_telemetry"] = "synthetic"
            detail["synthetic_pricing_active"] = True
    elif source == "delta_estimate":
        if isinstance(delta_at_entry, (int, float)) and float(delta_at_entry) > 0:
            raw_effective = modeled
            source = "delta_estimate"
            detail["quote_slice"] = "modeled_delta"
        else:
            raw_effective = proxy
            source = "underlying_proxy"
            detail["quote_slice"] = "underlying_proxy"
    else:
        raw_effective = max(0.01, float(raw_effective))

    if source in ("exact", "nearest", "cached_quote"):
        detail["price_source_telemetry"] = "live"
        detail["synthetic_pricing_active"] = False

    if cfg.require_live_quotes and (not is_0dte) and source in ("delta_estimate", "underlying_proxy"):
        oq_r = option_quote if isinstance(option_quote, dict) else None
        if not oq_r:
            detail["live_degraded_reason"] = "no_quote"
        else:
            has_any_quote_field = any(
                _f(option_quote.get(k)) is not None
                for k in ("bid", "ask", "mark", "mid_price", "last", "cached_price")
            )
            detail["live_degraded_reason"] = "stale_quote" if has_any_quote_field else "no_quote"
        hold_px = previous_effective if previous_effective is not None and float(previous_effective) > 0 else max(0.01, float(entry_premium))
        raw_effective = float(hold_px)
        source = "live_quote_unavailable"
        detail["pricing_degraded"] = True
        detail["live_quotes_strict_block"] = True
        detail["quote_slice"] = "hold_last_or_entry"

    if detail.get("price_source_telemetry") is None:
        if source in ("exact", "nearest", "cached_quote"):
            detail["price_source_telemetry"] = "live"
        elif source == "last_valid_stale":
            detail["price_source_telemetry"] = "last_valid"
        elif source in ("synthetic_modeled", "synthetic_proxy"):
            detail["price_source_telemetry"] = "synthetic"
        elif source in ("delta_estimate", "underlying_proxy"):
            detail["price_source_telemetry"] = "synthetic"
        elif source == "live_quote_unavailable":
            detail["price_source_telemetry"] = "last_valid"

    if is_0dte and detail.get("price_source_telemetry") not in (None, "live"):
        detail["pricing_degraded"] = True

    fast_market = False
    if is_0dte and float(entry_underlying or 0.0) > 0:
        move_abs = abs(float(current_underlying) - float(entry_underlying))
        if move_abs > float(cfg.option_0dte_fast_underlying_move_abs):
            fast_market = True
        if seconds_since_entry is not None and float(seconds_since_entry) < float(cfg.option_0dte_fast_entry_minutes) * 60.0:
            fast_market = True

    if fast_market:
        effective = max(0.01, float(raw_effective))
        detail["clamp_skipped_fast_market"] = True
    else:
        effective = _clamp_premium_step(raw_effective, previous_effective, entry_premium, cfg)

    detail["effective_premium_used"] = round(effective, 6)
    detail["premium_source"] = source
    detail["fallback_used"] = source not in {"exact", "nearest", "cached_quote"}
    max_exit_age = float(max(1.0, getattr(cfg, "quote_exit_grade_max_age_seconds", 30.0)))
    stale_age = float(max(max_exit_age, getattr(cfg, "quote_stale_max_age_seconds", 90.0)))
    qa = detail.get("quote_age_seconds")
    qa_f = float(qa) if isinstance(qa, (int, float)) else 0.0
    if source in {"exact", "nearest", "cached_quote"}:
        mark_quality = "live_single_leg" if qa_f > 0 and qa_f <= max_exit_age else "cached_recent"
    elif source == "last_valid_stale":
        mark_quality = "stale_last_valid" if qa_f <= stale_age else "missing"
    elif source in {"synthetic_modeled", "synthetic_proxy", "delta_estimate", "underlying_proxy"}:
        mark_quality = "synthetic"
    else:
        mark_quality = "missing"
    detail["mark_quality"] = mark_quality
    detail["mark_is_exit_grade"] = bool(mark_quality == "live_single_leg")
    detail["leg_completeness"] = "single_leg"
    detail["using_synthetic_mark"] = bool(mark_quality == "synthetic")
    detail["using_stale_mark"] = bool(mark_quality in {"stale_last_valid", "cached_recent"})
    if is_0dte and _option_price_resolution_audit_should_emit(cfg, trade_id, position_symbol):
        log.info(
            "OPTION_PRICE_RESOLUTION_AUDIT | trade_id=%s | symbol=%s | source=%s | quote_slice=%s | raw_effective=%.6f | effective=%.6f | "
            "quote_age_seconds=%s | spread_pct=%s | clamp_skipped=%s | fast_market=%s | audit_throttle_sec=%.1f",
            str(trade_id or "-"),
            position_symbol,
            source,
            detail.get("quote_slice"),
            float(raw_effective),
            float(effective),
            detail.get("quote_age_seconds"),
            detail.get("spread_pct"),
            str(bool(detail.get("clamp_skipped_fast_market"))).lower(),
            str(fast_market).lower(),
            float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0),
        )
    return effective, source, detail


def resolve_option_premium(
    *,
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    cfg: OptionStealthConfig,
    option_quote: Optional[Dict[str, Any]],
    previous_effective: Optional[float],
    delta_at_entry: float,
    strike: float,
    position_symbol: str,
    is_0dte: bool = False,
    seconds_since_entry: Optional[float] = None,
    trade_id: str = "",
    stored_last_valid_option_price: Optional[float] = None,
    is_short_premium: bool = False,
) -> Tuple[float, str, Dict[str, Any]]:
    """Backward-compatible alias for resolve_option_price()."""
    return resolve_option_price(
        entry_underlying=entry_underlying,
        current_underlying=current_underlying,
        option_side=option_side,
        entry_premium=entry_premium,
        cfg=cfg,
        option_quote=option_quote,
        previous_effective=previous_effective,
        delta_at_entry=delta_at_entry,
        strike=strike,
        position_symbol=position_symbol,
        is_0dte=is_0dte,
        seconds_since_entry=seconds_since_entry,
        trade_id=trade_id,
        stored_last_valid_option_price=stored_last_valid_option_price,
        is_short_premium=is_short_premium,
    )


def _normalize_position_type(position_type: Optional[str], option_side: str) -> str:
    raw = str(position_type or "").strip().lower()
    if raw in (
        OptionPositionType.SINGLE_LEG_LONG_CALL.value,
        OptionPositionType.SINGLE_LEG_LONG_PUT.value,
        OptionPositionType.DEBIT_SPREAD.value,
        OptionPositionType.CREDIT_SPREAD.value,
    ):
        return raw
    if raw in ("lotto", "single_leg", "long_option"):
        return (
            OptionPositionType.SINGLE_LEG_LONG_PUT.value
            if str(option_side).lower() == "put"
            else OptionPositionType.SINGLE_LEG_LONG_CALL.value
        )
    return (
        OptionPositionType.SINGLE_LEG_LONG_PUT.value
        if str(option_side).lower() == "put"
        else OptionPositionType.SINGLE_LEG_LONG_CALL.value
    )


def validate_stealth_registration_inputs(
    *,
    norm_position_type: str,
    underlying_symbol: str,
    expiration_ymd: str,
    norm_legs: List[Dict[str, Any]],
    spread_entry_value: float,
    entry_debit: Optional[float],
    entry_credit: Optional[float],
) -> Tuple[List[str], List[str]]:
    """
    Pre-register completeness check by structural position type.
    Returns (missing_or_weak, warnings). Does not raise — callers log and may apply fallbacks.
    """
    missing: List[str] = []
    warnings: List[str] = []
    if not str(underlying_symbol or "").strip():
        warnings.append("underlying_symbol_empty")
    if not str(expiration_ymd or "").strip():
        warnings.append("expiration_ymd_empty")
    pt = norm_position_type
    if pt in (
        OptionPositionType.SINGLE_LEG_LONG_CALL.value,
        OptionPositionType.SINGLE_LEG_LONG_PUT.value,
    ):
        if len(norm_legs) != 1:
            warnings.append(f"single_leg_expected_1_leg_got_{len(norm_legs)}")
        if spread_entry_value <= 0:
            missing.append("entry_value_non_positive")
    elif pt == OptionPositionType.DEBIT_SPREAD.value:
        if len(norm_legs) < 2:
            warnings.append(f"debit_spread_expected_2plus_legs_got_{len(norm_legs)}")
        if (entry_debit is None or float(entry_debit) <= 0) and spread_entry_value <= 0:
            missing.append("entry_debit_or_entry_value")
    elif pt == OptionPositionType.CREDIT_SPREAD.value:
        if len(norm_legs) < 2:
            warnings.append(f"credit_spread_expected_2plus_legs_got_{len(norm_legs)}")
        if (entry_credit is None or float(entry_credit) <= 0) and spread_entry_value <= 0:
            missing.append("entry_credit_or_entry_value")
    return missing, warnings


def _normalize_legs(legs: Optional[List[Dict[str, Any]]], option_side: str, strike: float, delta: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(legs, list):
        for idx, leg in enumerate(legs):
            if not isinstance(leg, dict):
                continue
            out.append(
                {
                    "leg_id": leg.get("leg_id") or leg.get("symbol") or f"leg_{idx+1}",
                    "symbol": leg.get("symbol"),
                    "long_or_short": str(leg.get("long_or_short") or "long").lower(),
                    "option_side": str(leg.get("option_side") or option_side or "").lower(),
                    "strike": float(leg.get("strike") or 0.0),
                    "quantity": int(max(1, int(leg.get("quantity") or 1))),
                    "entry_price": float(leg.get("entry_price") or 0.0),
                    "delta_at_entry": float(leg.get("delta_at_entry") or 0.0),
                }
            )
    if out:
        return out
    return [
        {
            "leg_id": "single_leg",
            "symbol": None,
            "long_or_short": "long",
            "option_side": str(option_side or "").lower(),
            "strike": float(strike or 0.0),
            "quantity": 1,
            "entry_price": 0.0,
            "delta_at_entry": float(delta or 0.0),
        }
    ]


def _mtm_positive_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _leg_mark_mid_or_last(
    leg_quote: Optional[Dict[str, Any]],
    leg_role: str = "",
) -> Tuple[float, str, str]:
    """
    Per-leg mark aligned with options_trading_executor.update_positions_with_real_prices:
    mid_price (or mark), else last, else (bid+ask)/2.

    For spread MTM / close valuation, mid-derived marks are conservative vs raw mid:
    sell long at worse (lower) price, buy short back at worse (higher) price.
    long: effective = mid * 0.98; short: effective = mid * 1.02.
    Last-only path is unchanged (no scaling).
    """
    if not isinstance(leg_quote, dict):
        return 0.0, "missing", ""
    role = str(leg_role or "").lower()
    mid = _mtm_positive_float(leg_quote.get("mid_price")) or _mtm_positive_float(leg_quote.get("mark"))
    if mid is not None:
        px = float(mid)
        adj = ""
        if role == "long":
            px *= 0.98
            adj = "long_mid_0.98"
        elif role == "short":
            px *= 1.02
            adj = "short_mid_1.02"
        return px, "mid", adj
    last = _mtm_positive_float(leg_quote.get("last"))
    if last is not None:
        return float(last), "last", ""
    bid = _mtm_positive_float(leg_quote.get("bid"))
    ask = _mtm_positive_float(leg_quote.get("ask"))
    if bid is not None and ask is not None and (bid + ask) > 0:
        px = (float(bid) + float(ask)) / 2.0
        adj = ""
        if role == "long":
            px *= 0.98
            adj = "long_bidask_mid_0.98"
        elif role == "short":
            px *= 1.02
            adj = "short_bidask_mid_1.02"
        return px, "bidask_mid", adj
    return 0.0, "missing", ""


def _pick_long_short_legs(legs: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    long_leg: Optional[Dict[str, Any]] = None
    short_leg: Optional[Dict[str, Any]] = None
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        los = str(leg.get("long_or_short") or "").lower()
        if los == "long" and long_leg is None:
            long_leg = leg
        elif los == "short" and short_leg is None:
            short_leg = leg
    return long_leg, short_leg


def resolve_spread_net_value(
    *,
    current_underlying: float,
    entry_underlying: float,
    cfg: OptionStealthConfig,
    previous_effective: Optional[float],
    position_symbol: str,
    legs: List[Dict[str, Any]],
    option_quote: Optional[Dict[str, Any]],
    entry_value: float,
    position_type: str = OptionPositionType.DEBIT_SPREAD.value,
    trade_id: str = "",
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Vertical spread mark-to-market from leg quotes only (no resolve_option_price, no abs net,
    no entry/cached inflation, no _clamp_premium_step).

    Debit: long (conservative sell mark: mid*0.98 or last) minus short (conservative cover: mid*1.02 or last),
    clamped to [0, strike width].
    Credit: short - long (cost to close), clamped to [0, strike width].
    """
    _ = (current_underlying, entry_underlying, cfg, previous_effective)

    detail: Dict[str, Any] = {
        "leg_quote_sources": [],
        "spread_current_value": None,
        "spread_entry_value": float(entry_value or 0.0),
        "spread_premium_source_quality": "mtm_incomplete",
        "spread_monitoring_mode": "spread_mtm",
    }
    if not legs:
        detail["spread_monitoring_mode"] = "spread_mtm_no_legs"
        detail["spread_premium_source_quality"] = "no_legs"
        detail["price_source_telemetry"] = "synthetic"
        return 0.0, "spread_mtm_no_legs", detail

    leg_quotes_by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(option_quote, dict):
        sca_in = option_quote.get("spread_chain_source_audit")
        if isinstance(sca_in, dict):
            detail["spread_chain_source_audit"] = dict(sca_in)
        for lq in option_quote.get("leg_quotes", []) or []:
            if not isinstance(lq, dict):
                continue
            leg_id = str(lq.get("leg_id") or lq.get("symbol") or "")
            if leg_id:
                leg_quotes_by_id[leg_id] = lq

    long_leg, short_leg = _pick_long_short_legs(legs)
    if long_leg is None or short_leg is None:
        detail["spread_monitoring_mode"] = "spread_mtm_missing_leg_role"
        detail["spread_premium_source_quality"] = "incomplete_structure"
        detail["price_source_telemetry"] = "synthetic"
        return 0.0, "spread_mtm_missing_leg_role", detail

    long_strike = float(long_leg.get("strike") or 0.0)
    short_strike = float(short_leg.get("strike") or 0.0)
    spread_width = abs(float(short_strike) - float(long_strike))

    long_id = str(long_leg.get("leg_id") or long_leg.get("symbol") or "")
    short_id = str(short_leg.get("leg_id") or short_leg.get("symbol") or "")
    long_q = leg_quotes_by_id.get(long_id) if long_id else None
    short_q = leg_quotes_by_id.get(short_id) if short_id else None

    long_px, long_src, long_mid_adj = _leg_mark_mid_or_last(long_q, "long")
    short_px, short_src, short_mid_adj = _leg_mark_mid_or_last(short_q, "short")
    def _qf(q: Optional[Dict[str, Any]], key: str) -> float:
        try:
            if not isinstance(q, dict):
                return 0.0
            v = float(q.get(key) or 0.0)
            return v if v > 0 else 0.0
        except (TypeError, ValueError):
            return 0.0
    long_bid = _qf(long_q, "bid")
    long_ask = _qf(long_q, "ask")
    short_bid = _qf(short_q, "bid")
    short_ask = _qf(short_q, "ask")
    long_mid_live = ((long_bid + long_ask) / 2.0) if long_bid > 0 and long_ask > 0 else 0.0
    short_mid_live = ((short_bid + short_ask) / 2.0) if short_bid > 0 and short_ask > 0 else 0.0
    long_age = _qf(long_q, "quote_age_seconds")
    short_age = _qf(short_q, "quote_age_seconds")

    is_credit = str(position_type or "").strip().lower() == OptionPositionType.CREDIT_SPREAD.value
    if is_credit:
        raw_spread = float(short_px) - float(long_px)
        mid_value_raw = (short_mid_live - long_mid_live) if short_mid_live > 0 and long_mid_live > 0 else raw_spread
        liq_value_raw = (short_ask - long_bid) if short_ask > 0 and long_bid > 0 else raw_spread
    else:
        raw_spread = float(long_px) - float(short_px)
        mid_value_raw = (long_mid_live - short_mid_live) if long_mid_live > 0 and short_mid_live > 0 else raw_spread
        liq_value_raw = (long_bid - short_ask) if long_bid > 0 and short_ask > 0 else raw_spread

    if spread_width > 0.0:
        eff = max(0.0, min(float(spread_width), float(raw_spread)))
    else:
        eff = max(0.0, float(raw_spread))

    if float(eff) < 0.0:
        log.error(
            "SPREAD_VALUE_NEGATIVE | trade_id=%s | symbol=%s | spread_value=%.6f | raw_spread=%.6f | spread_width=%.6f",
            str(trade_id or ""),
            str(position_symbol or ""),
            float(eff),
            float(raw_spread),
            float(spread_width),
        )
    if spread_width > 0.0:
        if float(raw_spread) > float(spread_width) + 0.05:
            log.error(
                "SPREAD_VALUE_EXCEEDS_WIDTH | trade_id=%s | symbol=%s | phase=raw_pre_clamp | "
                "raw_spread=%.6f | spread_width=%.6f | clamped=%.6f",
                str(trade_id or ""),
                str(position_symbol or ""),
                float(raw_spread),
                float(spread_width),
                float(eff),
            )
        if float(eff) > float(spread_width) + 0.05:
            log.error(
                "SPREAD_VALUE_EXCEEDS_WIDTH | trade_id=%s | symbol=%s | phase=post_clamp | "
                "spread_value=%.6f | spread_width=%.6f",
                str(trade_id or ""),
                str(position_symbol or ""),
                float(eff),
                float(spread_width),
            )

    entry_debit = float(entry_value or 0.0)
    qty = int(max(1, int(long_leg.get("quantity") or short_leg.get("quantity") or 1)))
    if is_credit:
        pnl_dollars = (entry_debit - eff) * float(qty) * 100.0
    else:
        pnl_dollars = (eff - entry_debit) * float(qty) * 100.0

    if long_px <= 0.0 or short_px <= 0.0:
        src = "spread_mtm_incomplete_quotes"
        detail["spread_premium_source_quality"] = "incomplete_quotes"
        detail["spread_monitoring_mode"] = "spread_mtm_incomplete_quotes"
    else:
        src = "spread_mtm_mid_last"
        detail["spread_premium_source_quality"] = "mtm_leg_mid_last"

    detail["leg_quote_sources"] = [
        {
            "leg_id": long_id,
            "source": long_src,
            "value": round(float(long_px), 6),
            "long_or_short": "long",
            "quantity": qty,
            "mid_conservative": long_mid_adj or None,
        },
        {
            "leg_id": short_id,
            "source": short_src,
            "value": round(float(short_px), 6),
            "long_or_short": "short",
            "quantity": qty,
            "mid_conservative": short_mid_adj or None,
        },
    ]
    mid_value = max(0.0, min(float(spread_width), float(mid_value_raw))) if spread_width > 0 else max(0.0, float(mid_value_raw))
    liq_value = max(0.0, min(float(spread_width), float(liq_value_raw))) if spread_width > 0 else max(0.0, float(liq_value_raw))
    detail["spread_current_value"] = round(float(eff), 6)
    detail["spread_mid_value"] = round(float(mid_value), 6)
    detail["spread_liquidation_value"] = round(float(liq_value), 6)
    detail["long_leg_quote_age_seconds"] = float(long_age)
    detail["short_leg_quote_age_seconds"] = float(short_age)
    detail["long_leg_bid"] = float(long_bid)
    detail["long_leg_ask"] = float(long_ask)
    detail["long_leg_mid"] = float(long_mid_live)
    detail["short_leg_bid"] = float(short_bid)
    detail["short_leg_ask"] = float(short_ask)
    detail["short_leg_mid"] = float(short_mid_live)
    detail["leg_completeness"] = "complete" if (long_mid_live > 0 and short_mid_live > 0) else ("partial" if (long_px > 0 or short_px > 0) else "missing")
    if str(detail.get("spread_premium_source_quality") or "") == "mtm_leg_mid_last" and long_px > 0.0 and short_px > 0.0:
        detail["price_source_telemetry"] = "live"
    else:
        detail["price_source_telemetry"] = "synthetic"
    max_age = float(max(1.0, getattr(cfg, "quote_exit_grade_max_age_seconds", 30.0)))
    is_fresh_both = bool(long_age > 0 and short_age > 0 and long_age <= max_age and short_age <= max_age)
    mark_quality = (
        "live_two_leg_spread"
        if (detail["leg_completeness"] == "complete" and is_fresh_both)
        else "partial_leg"
        if detail["leg_completeness"] == "partial"
        else "missing"
    )
    detail["mark_quality"] = mark_quality
    detail["mark_is_exit_grade"] = bool(mark_quality == "live_two_leg_spread")
    detail["using_synthetic_mark"] = bool(detail.get("price_source_telemetry") != "live")
    detail["using_stale_mark"] = bool(not is_fresh_both and detail["leg_completeness"] != "missing")
    entry_debit_for_pnl = float(entry_debit if entry_debit > 0 else entry_value or 0.0)
    if entry_debit_for_pnl > 0:
        detail["current_pnl_pct_mid"] = float((mid_value - entry_debit_for_pnl) / entry_debit_for_pnl)
        detail["current_pnl_pct_liquidation"] = float((liq_value - entry_debit_for_pnl) / entry_debit_for_pnl)
    else:
        detail["current_pnl_pct_mid"] = 0.0
        detail["current_pnl_pct_liquidation"] = 0.0
    detail["long_price"] = float(long_px)
    detail["short_price"] = float(short_px)
    detail["raw_spread"] = float(raw_spread)
    detail["spread_width"] = float(spread_width)
    detail["entry_debit"] = float(entry_debit)
    detail["pnl_dollars"] = float(pnl_dollars)
    detail["quantity"] = int(qty)

    sc_audit = detail.get("spread_chain_source_audit")
    ch_src = str(sc_audit.get("source")) if isinstance(sc_audit, dict) and sc_audit.get("source") is not None else ""
    ch_ts = str(sc_audit.get("timestamp")) if isinstance(sc_audit, dict) and sc_audit.get("timestamp") is not None else ""
    log.info(
        "SPREAD_VALUATION_AUDIT | trade_id=%s | symbol=%s | position_type=%s | long_price=%.6f | short_price=%.6f | "
        "raw_spread=%.6f | spread_value=%.6f | spread_width=%.6f | entry_debit=%.6f | qty=%d | pnl_dollars=%.2f | "
        "long_src=%s | short_src=%s | mtm_source=%s | chain_source=%s | chain_ts=%s",
        str(trade_id or ""),
        str(position_symbol or ""),
        str(position_type or ""),
        float(long_px),
        float(short_px),
        float(raw_spread),
        float(eff),
        float(spread_width),
        float(entry_debit),
        int(qty),
        float(pnl_dollars),
        str(long_src),
        str(short_src),
        str(src),
        ch_src or "n/a",
        ch_ts or "n/a",
    )
    log.info(
        "OPTION_SPREAD_NET_MARK_AUDIT | trade_id=%s | symbol=%s | position_type=%s | option_side=%s | entry_net_debit=%.6f | "
        "mid_value=%.6f | liquidation_value=%.6f | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | mark_quality=%s | "
        "mark_is_exit_grade=%s | quote_source=%s | quote_age_seconds=%.1f | long_leg_bid=%.6f | long_leg_ask=%.6f | long_leg_mid=%.6f | "
        "short_leg_bid=%.6f | short_leg_ask=%.6f | short_leg_mid=%.6f | leg_completeness=%s",
        str(trade_id or ""),
        str(position_symbol or ""),
        str(position_type or ""),
        str((long_leg or {}).get("option_side") or ""),
        float(entry_debit_for_pnl),
        float(mid_value),
        float(liq_value),
        float(detail.get("current_pnl_pct_mid") or 0.0),
        float(detail.get("current_pnl_pct_liquidation") or 0.0),
        str(mark_quality),
        str(bool(detail.get("mark_is_exit_grade"))).lower(),
        str(src),
        float(max(long_age, short_age)),
        float(long_bid),
        float(long_ask),
        float(long_mid_live),
        float(short_bid),
        float(short_ask),
        float(short_mid_live),
        str(detail.get("leg_completeness") or "missing"),
    )

    return float(eff), str(src), detail


def _line_value_at(trendline: Dict[str, Any], ts: datetime) -> float:
    slope = float(trendline.get("slope_per_second", 0.0) or 0.0)
    intercept = float(trendline.get("intercept", 0.0) or 0.0)
    return (slope * ts.timestamp()) + intercept


def _structure_invalid(
    cfg: OptionStealthConfig,
    trendline: Dict[str, Any],
    underlying: float,
    now: datetime,
    line_geometry: str,
    option_side: str,
) -> Tuple[bool, str]:
    if not cfg.structure_invalidation_enabled or not trendline:
        return False, ""
    line_px = _line_value_at(trendline, now)
    if line_px <= 0:
        return False, ""
    buf = cfg.structure_buffer_pct
    geom = str(line_geometry).lower()
    side = str(option_side).lower()

    if geom == "bear" and side == "call":
        if underlying < line_px * (1.0 - buf):
            return True, "long_call_price_fell_back_below_resistance_structure"
        return False, ""

    if geom == "bull" and side == "put":
        if underlying > line_px * (1.0 + buf):
            return True, "long_put_price_reclaimed_above_support_structure"
        return False, ""

    if geom == "bear":
        if underlying < line_px * (1.0 - buf):
            return True, "underlying_below_resistance_invalidation"
        return False, ""
    if geom == "bull":
        if underlying > line_px * (1.0 + buf):
            return True, "underlying_above_support_invalidation"
        return False, ""
    return False, ""


def _structure_still_valid(
    cfg: OptionStealthConfig,
    trendline: Dict[str, Any],
    underlying: float,
    now: datetime,
    line_geometry: str,
    option_side: str,
) -> bool:
    inv, _ = _structure_invalid(cfg, trendline, underlying, now, line_geometry, option_side)
    return not inv


class TrendlineOptionsStealthEngine:
    """
    Single normal 0DTE options exit brain (premium-based) for ORB 0DTE + Trendline 0DTE.
    ORB SO ETF path remains separate in prime_stealth_trailing_tp.py.
    """

    def __init__(self, config: Optional[OptionStealthConfig] = None) -> None:
        self.config = config or load_option_stealth_config()
        self._session: Dict[str, Any] = {
            "opens": 0,
            "breakeven_exits": 0,
            "trailing_exits": 0,
            "invalidation_exits": 0,
            "no_progress_exits": 0,
            "fast_fail_exits": 0,
            "time_cap_exits": 0,
            "eod_exits": 0,
            "pnl_pcts": [],
            "max_excursions": [],
            "drawdowns_hwm": [],
            "min_to_breakeven": [],
            "min_to_trailing": [],
            "exit_premium_sources": {"exact": 0, "nearest": 0, "delta_estimate": 0, "underlying_proxy": 0},
            "degraded_quote_count": 0,
            "degraded_exit_count": 0,
            "skipped_entry_due_to_no_quote": 0,
            "live_quote_tick_hits": 0,
            "live_quote_tick_total": 0,
        }
        self._fix_session_types()
        log.info(
            "TRENDLINE_PIPELINE | stage=option_stealth_init | breakeven_trigger=%.3f | "
            "trailing_trigger=%.3f | base_trail=%.3f | tp_ladder_disabled=%s | "
            "tline_time_exit_min=%.0f | tline_no_progress_min=%.0f | tline_be_mult=%.2f | "
            "tline_trail_mult=%.2f | tline_profit_lock=%.3f@+%.3f | tline_chop_hold_pnl=%.3f",
            self.config.breakeven_trigger_pct,
            self.config.trailing_trigger_pct,
            self.config.base_trailing_pct,
            self.config.disable_tp_ladder,
            self.config.tline_time_exit_minutes,
            self.config.tline_no_progress_exit_minutes,
            self.config.tline_be_trigger_mult,
            self.config.tline_trailing_trigger_mult,
            self.config.tline_profit_lock_trigger_pct,
            self.config.tline_profit_lock_pct,
            self.config.tline_chop_hold_min_pnl_pct,
        )
        log.info(
            "0DTE_RUNTIME_CONFIG | revision_id=%s | system_version=%s | outage_seconds=%.1f | data_source_priority=%s | "
            "require_live_option_data=%s | require_live_quotes=%s | quote_fresh_max_age_sec=%.2f | "
            "price_resolution_audit_min_sec=%.1f",
            os.getenv("K_REVISION", "unknown"),
            os.getenv("APP_VERSION", "unknown"),
            float(self.config.option_max_degraded_seconds),
            "live>last_valid>synthetic",
            str(bool(self.config.require_live_option_data)).lower(),
            str(bool(self.config.require_live_quotes)).lower(),
            float(self.config.option_quote_fresh_max_age_seconds),
            float(self.config.option_price_resolution_audit_min_seconds),
        )

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _orb_spread_timeout_exit_relief(
        self,
        *,
        is_orb_0dte: bool,
        is_spread: bool,
        is_trendline_0dte: bool,
        st: OptionStealthState,
        osnap: Dict[str, Any],
        detail: Dict[str, Any],
        prem_src: str,
        prem: float,
        held_min: float,
        mark_is_exit_grade_now: bool,
        mark_quality: str,
        now: datetime,
    ) -> Tuple[bool, str, str]:
        """
        ORB 0DTE spread-only relief from strict exit-grade + good-tick + timeout-freeze gates
        for timeout-style exits (time / no-progress / fast_fail), using conservative MTM first
        then a hard forced tier so spreads cannot remain open indefinitely on partial_leg quotes.
        """
        if not is_orb_0dte or is_trendline_0dte or not is_spread:
            return False, "", ""
        cfg = self.config
        if not bool(getattr(cfg, "orb_options_spread_degraded_exit_enable", True)):
            return False, "", ""
        if mark_is_exit_grade_now:
            return False, "", ""
        defer_n = int(osnap.get("orb_exit_defer_total") or 0)
        partial_iso = osnap.get("orb_partial_leg_first_iso")
        partial_min = 0.0
        if isinstance(partial_iso, str) and partial_iso.strip():
            try:
                partial_min = max(
                    0.0,
                    (now - self._normalize_dt(datetime.fromisoformat(partial_iso))).total_seconds() / 60.0,
                )
            except Exception:
                partial_min = 0.0
        wd_accum = float(osnap.get("orb_watchdog_gap_accum_sec") or 0.0)
        long_age = float(detail.get("long_leg_quote_age_seconds") or 0.0)
        short_age = float(detail.get("short_leg_quote_age_seconds") or 0.0)
        max_leg_age = max(long_age, short_age)
        lpx = float(detail.get("long_price") or 0.0)
        spx = float(detail.get("short_price") or 0.0)
        spread_val = float(detail.get("spread_current_value") or prem or 0.0)
        src_q = str(detail.get("spread_premium_source_quality") or "")
        unsafe = prem_src in {
            "spread_modeled_fallback",
            "spread_position_value_fallback",
            "delta_estimate",
            "underlying_proxy",
            "synthetic_proxy",
            "synthetic_modeled",
            "synthetic_open_grace",
        }
        abs_max = float(max(15.0, getattr(cfg, "orb_spread_forced_abs_max_minutes", 90.0)))
        forced_min_trade = float(max(5.0, getattr(cfg, "orb_spread_forced_min_trade_minutes", 28.0)))
        forced_partial = float(max(3.0, getattr(cfg, "orb_spread_forced_min_partial_minutes", 20.0)))
        forced_defers = int(max(5, getattr(cfg, "orb_spread_forced_min_deferrals", 55)))
        forced_wd = float(max(60.0, getattr(cfg, "orb_spread_forced_watchdog_accum_seconds", 480.0)))
        allow_incomplete = bool(getattr(cfg, "orb_spread_forced_allow_incomplete_mtm", True))
        forced_ok_src = (not unsafe) or (
            allow_incomplete
            and held_min >= abs_max - 5.0
            and prem_src in ("spread_mtm_incomplete_quotes", "spread_mtm_mid_last")
        )
        forced_price_ok = spread_val > 0.0 and (lpx > 0.0 or spx > 0.0)
        tier_b = forced_ok_src and forced_price_ok and (
            held_min >= abs_max
            or (held_min >= forced_min_trade and partial_min >= forced_partial)
            or defer_n >= forced_defers
            or (wd_accum >= forced_wd and partial_min >= 5.0)
        )
        if tier_b:
            return (
                True,
                "forced",
                f"held_min={held_min:.2f}|partial_min={partial_min:.2f}|defers={defer_n}|wd_accum={wd_accum:.0f}|prem_src={prem_src}|mq={mark_quality}",
            )
        cons_trade = float(max(1.0, getattr(cfg, "orb_spread_conservative_min_trade_minutes", 5.0)))
        cons_partial = float(max(1.0, getattr(cfg, "orb_spread_conservative_min_partial_minutes", 4.0)))
        cons_defers = int(max(3, getattr(cfg, "orb_spread_conservative_min_deferrals", 15)))
        cons_max_age = float(max(30.0, getattr(cfg, "orb_spread_conservative_max_leg_age_sec", 240.0)))
        mtm_ok = (
            prem_src == "spread_mtm_mid_last"
            and src_q == "mtm_leg_mid_last"
            and lpx > 0.0
            and spx > 0.0
            and spread_val > 0.0
        )
        if bool(getattr(cfg, "orb_options_spread_conservative_exit_enable", True)) and mtm_ok:
            if (
                max_leg_age <= cons_max_age
                and held_min >= cons_trade
                and (partial_min >= cons_partial or defer_n >= cons_defers)
            ):
                return (
                    True,
                    "conservative",
                    f"held_min={held_min:.2f}|max_leg_age={max_leg_age:.1f}|defers={defer_n}|partial_min={partial_min:.2f}|prem_src={prem_src}",
                )
        return False, "", ""

    def _orb_trusted_degraded_spread_mark_eligible(
        self,
        *,
        is_orb_0dte: bool,
        is_spread: bool,
        synthetic_quote: bool,
        prem_src: str,
        mark_quality: str,
        quote_age_seconds: float,
        detail: Dict[str, Any],
        u_move_pct_eval: float,
        entry_debit: float,
        spread_mid: float,
        spread_liq: float,
        position_type: str,
    ) -> Tuple[bool, str]:
        """
        ORB 0DTE spread-only: allow timeout-style exits under partial_leg/cached marks when quotes are stale
        but not garbage — does NOT enable HWM ratchet (see _premium_updates_favorable_hwm).
        """
        cfg = self.config
        if not is_orb_0dte or not is_spread:
            return False, "not_orb_spread"
        if not bool(getattr(cfg, "orb_trusted_degraded_spread_enable", True)):
            return False, "disabled"
        if synthetic_quote:
            return False, "synthetic_quote"
        src = str(prem_src or "").strip().lower()
        mq = str(mark_quality or "").strip().lower()
        unsafe = {
            "spread_modeled_fallback",
            "spread_position_value_fallback",
            "delta_estimate",
            "underlying_proxy",
            "synthetic_proxy",
            "synthetic_modeled",
            "synthetic_open_grace",
        }
        if src in unsafe:
            return False, "unsafe_prem_src"
        allowed_mq = {"partial_leg", "cached_recent", "nearest", "cached_quote"}
        if mq not in allowed_mq:
            return False, f"mark_quality={mq}"
        stale_cap = float(getattr(cfg, "orb_trusted_degraded_mark_max_age_seconds", 90.0) or 90.0)
        if float(quote_age_seconds or 0.0) > stale_cap:
            return False, "quote_age_over_cap"
        long_age = float(detail.get("long_leg_quote_age_seconds") or 0.0)
        short_age = float(detail.get("short_leg_quote_age_seconds") or 0.0)
        fresh_cap = float(getattr(cfg, "orb_trusted_degraded_min_fresh_leg_age_seconds", 45.0) or 45.0)
        if min(long_age, short_age) > fresh_cap:
            return False, "no_recent_leg"
        spread_val = float(detail.get("spread_current_value") or spread_mid or 0.0)
        if spread_val <= 0.0 or float(spread_liq or 0.0) <= 0.0:
            return False, "nonpositive_spread_val"
        ed = float(entry_debit or 0.0)
        if ed > 0.0:
            mmx = float(getattr(cfg, "premium_max_mult_from_entry", 12.0) or 12.0)
            mmn = float(getattr(cfg, "premium_min_mult_from_entry", 0.02) or 0.02)
            if str(position_type or "").lower() == OptionPositionType.DEBIT_SPREAD.value:
                if spread_val > ed * mmx or spread_val < ed * mmn:
                    return False, "spread_bounds_fail"
        if float(u_move_pct_eval or 0.0) < -0.0005:
            return False, "underlying_adverse"
        return True, "ok"

    def _emit_orb_options_exit_deferred_audit(
        self,
        *,
        position_id: str,
        position_symbol: str,
        position_type: str,
        candidate_exit_reason: str,
        blocked_reason: str,
        st: OptionStealthState,
        osnap: Dict[str, Any],
        detail: Dict[str, Any],
        prem_src: str,
        prem: float,
        relief_active: bool,
        relief_tier: str,
        relief_detail: str,
    ) -> None:
        dedupe_key = f"orb_def_audit|{position_id}|{candidate_exit_reason}"
        if not _option_telemetry_log_should_emit(self.config, _orb_exit_deferred_audit_last, dedupe_key):
            return
        defer_n = int(osnap.get("orb_exit_defer_total") or 0)
        lc = str(detail.get("leg_completeness") or osnap.get("leg_completeness") or "unknown")
        mtm_method = str(detail.get("spread_premium_source_quality") or prem_src or "unknown")
        qage = osnap.get("quote_age_seconds")
        syn_live = str(detail.get("price_source_telemetry") or osnap.get("option_price_source_telemetry") or "unknown")
        est_exec = float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0)
        why = (
            f"exit_grade_required:blocked={blocked_reason}"
            if not relief_active
            else f"relief_active:tier={relief_tier}:{relief_detail}"
        )
        log.warning(
            "ORB_OPTIONS_EXIT_DEFERRED_AUDIT | trade_id=%s | symbol=%s | position_type=%s | candidate_exit_reason=%s | "
            "mark_quality=%s | leg_quote_availability=%s | spread_mtm_method=%s | timeout_clock_frozen=%s | quote_age_sec=%s | "
            "synthetic_vs_live=%s | good_mark_tick_count=%d | defer_total=%d | why_exit_deferred=%s | estimated_executable_exit_value=%.6f",
            str(position_id or ""),
            str(position_symbol or ""),
            str(position_type or ""),
            str(candidate_exit_reason or ""),
            str(osnap.get("mark_quality") or "unknown"),
            lc,
            mtm_method,
            str(bool(osnap.get("timeout_clock_frozen"))).lower(),
            str(qage if qage is not None else "n/a"),
            syn_live,
            int(st.good_mark_tick_count),
            defer_n,
            why,
            float(est_exec),
        )

    def _delta_bucket(self, delta_at_entry: float) -> str:
        d = float(delta_at_entry or 0.0)
        if d >= self.config.delta_itm_threshold:
            return "ITM"
        if d >= self.config.delta_atm_threshold:
            return "ATM"
        if d > 0:
            return "OTM"
        return "UNKNOWN"

    def _delta_aware_be_trigger(self, delta_at_entry: float) -> tuple[float, str]:
        bucket = self._delta_bucket(delta_at_entry)
        if bucket == "ITM":
            return float(self.config.be_trigger_itm_pct), bucket
        if bucket == "ATM":
            return float(self.config.be_trigger_atm_pct), bucket
        if bucket == "OTM":
            return float(self.config.be_trigger_otm_pct), bucket
        return float(self.config.breakeven_trigger_pct), bucket

    def _resolve_entry_archetype(
        self,
        *,
        source_path: str,
        is_orb_0dte: bool,
        trendline_mode: str,
        impulse_mode: bool,
        is_trendline_0dte: bool,
        trendline_break_archetype: str = "",
    ) -> str:
        if is_orb_0dte:
            return "opening_impulse"
        sp = str(source_path or "").lower()
        tm = str(trendline_mode or "").upper()
        barn = str(trendline_break_archetype or "").strip().lower()
        if "orb" in sp:
            return "opening_impulse"
        if is_trendline_0dte:
            if barn in {"impulse_exhaustion", "exhaustion_reversal"}:
                return "trendline_impulse"
            if barn == "impulse_break":
                return "trendline_impulse"
            if barn == "weak_break_failure":
                return "trendline_weak_break_failure"
            if impulse_mode or tm == "IMPULSE":
                return "trendline_impulse"
            if tm == "RETEST":
                return "trendline_retest"
            if tm == "SLOW_TREND":
                return "trendline_slow_trend"
        return "generic"

    def _resolve_exit_profile(
        self,
        *,
        source_path: str,
        position_type: str,
        entry_archetype: str,
        is_orb_0dte: bool,
        is_trendline_0dte: bool,
        trendline_break_archetype: str = "",
    ) -> OptionExitProfile:
        cfg = self.config
        is_spread = position_type in (OptionPositionType.DEBIT_SPREAD.value, OptionPositionType.CREDIT_SPREAD.value)

        be_trigger = float(cfg.breakeven_trigger_pct)
        be_lock = float(cfg.early_be_lock_pct or cfg.breakeven_lock_pct)
        trail_trigger = float(cfg.trailing_trigger_pct)
        trail_dist = float(cfg.spread_base_trailing_pct if is_spread else cfg.base_trailing_pct)
        profit_trigger = float(cfg.profit_lock_trigger_pct)
        profit_lock = float(cfg.profit_lock_level_pct)
        no_progress = float(cfg.spread_no_progress_exit_minutes if is_spread else cfg.no_progress_exit_minutes)
        time_exit = float(cfg.spread_time_exit_minutes if is_spread else cfg.time_exit_minutes)
        min_seconds_before_be = float(cfg.min_seconds_before_be)
        min_hold_seconds = 0.0
        allow_structure_exit = bool(is_trendline_0dte)

        if is_orb_0dte:
            be_trigger = max(0.03, be_trigger * float(cfg.orb_be_trigger_mult))
            trail_trigger = max(0.03, trail_trigger * float(cfg.orb_trailing_trigger_mult))
            profit_trigger = float(cfg.orb_profit_lock_trigger_pct or cfg.profit_lock_trigger_pct)
            profit_lock = float(cfg.orb_profit_lock_pct or cfg.profit_lock_level_pct)
            no_progress = min(no_progress, 8.0 if not is_spread else no_progress)
            time_exit = min(time_exit, 12.0 if not is_spread else time_exit)
            allow_structure_exit = False
            if not is_spread and entry_archetype == "opening_impulse":
                max_pnl_drawdown_trigger_pct = float(cfg.orb_mfe_trigger_pct)
                max_pnl_drawdown_exit_pct = float(cfg.orb_mfe_drawdown_exit_pct)
            else:
                max_pnl_drawdown_trigger_pct = float(cfg.max_pnl_drawdown_trigger_pct)
                max_pnl_drawdown_exit_pct = float(cfg.max_pnl_drawdown_exit_pct)
        elif is_trendline_0dte:
            min_hold_seconds = float(cfg.tline_min_hold_seconds)
            min_seconds_before_be = float(cfg.tline_min_seconds_before_be)
            profit_trigger = float(cfg.tline_profit_lock_trigger_pct or cfg.profit_lock_trigger_pct)
            profit_lock = float(cfg.tline_profit_lock_pct or cfg.profit_lock_level_pct)
            barn_prof = str(trendline_break_archetype or "").strip().lower()
            if entry_archetype == "trendline_impulse":
                be_trigger = float(cfg.tline_impulse_breakeven_activate_pct)
                trail_trigger = float(cfg.tline_impulse_trailing_trigger_pct)
                trail_dist = float(cfg.tline_impulse_trailing_pct)
                no_progress = float(cfg.tline_impulse_no_progress_exit_minutes)
                time_exit = float(cfg.tline_impulse_time_exit_minutes)
                max_pnl_drawdown_trigger_pct = float(cfg.tline_impulse_mfe_trigger_pct)
                max_pnl_drawdown_exit_pct = float(cfg.tline_impulse_mfe_drawdown_exit_pct)
                if barn_prof in {"impulse_exhaustion", "exhaustion_reversal"}:
                    be_trigger *= 0.88
                    trail_trigger *= 0.91
                    no_progress *= 0.72
                    time_exit *= 0.86
                    max_pnl_drawdown_trigger_pct *= 0.91
                    max_pnl_drawdown_exit_pct *= 0.87
                    trail_dist *= 0.93
            elif entry_archetype == "trendline_retest":
                be_trigger = float(cfg.tline_retest_breakeven_activate_pct)
                trail_trigger = float(cfg.tline_retest_trail_activate_pct)
                trail_dist = float(cfg.tline_base_trailing_pct)
                max_pnl_drawdown_trigger_pct = float(cfg.tline_retest_mfe_trigger_pct)
                max_pnl_drawdown_exit_pct = float(cfg.tline_retest_mfe_drawdown_exit_pct)
            elif entry_archetype == "trendline_slow_trend":
                be_trigger = float(cfg.tline_slow_trend_breakeven_activate_pct)
                trail_trigger = float(cfg.tline_slow_trend_trail_activate_pct)
                trail_dist = float(cfg.tline_base_trailing_pct)
                no_progress = float(cfg.tline_no_progress_exit_minutes)
                time_exit = float(cfg.tline_time_exit_minutes)
                max_pnl_drawdown_trigger_pct = float(cfg.tline_slow_mfe_trigger_pct)
                max_pnl_drawdown_exit_pct = float(cfg.tline_slow_mfe_drawdown_exit_pct)
            elif entry_archetype == "trendline_weak_break_failure":
                be_trigger = float(cfg.tline_breakeven_activate_pct) * 0.90
                trail_trigger = float(cfg.tline_trail_activate_pct) * 0.90
                trail_dist = float(cfg.tline_base_trailing_pct)
                no_progress = min(3.5, float(cfg.tline_impulse_no_progress_exit_minutes) * 0.55)
                time_exit = min(10.5, float(cfg.tline_impulse_time_exit_minutes) * 0.80)
                max_pnl_drawdown_trigger_pct = float(cfg.tline_impulse_mfe_trigger_pct) * 0.93
                max_pnl_drawdown_exit_pct = float(cfg.tline_impulse_mfe_drawdown_exit_pct) * 0.94
            else:
                be_trigger = float(cfg.tline_breakeven_activate_pct)
                trail_trigger = float(cfg.tline_trail_activate_pct)
                trail_dist = float(cfg.tline_base_trailing_pct)
                no_progress = float(cfg.tline_no_progress_exit_minutes)
                time_exit = float(cfg.tline_time_exit_minutes)
                max_pnl_drawdown_trigger_pct = float(cfg.max_pnl_drawdown_trigger_pct)
                max_pnl_drawdown_exit_pct = float(cfg.max_pnl_drawdown_exit_pct)
        else:
            max_pnl_drawdown_trigger_pct = float(cfg.max_pnl_drawdown_trigger_pct)
            max_pnl_drawdown_exit_pct = float(cfg.max_pnl_drawdown_exit_pct)

        if is_spread:
            # Keep spreads on wider/default drawdown controls.
            max_pnl_drawdown_trigger_pct = float(cfg.max_pnl_drawdown_trigger_pct)
            max_pnl_drawdown_exit_pct = float(cfg.max_pnl_drawdown_exit_pct)

        return OptionExitProfile(
            source_path=str(source_path or "unknown"),
            position_type=str(position_type or "unknown"),
            entry_archetype=str(entry_archetype or "generic"),
            be_trigger_pct=float(be_trigger),
            be_lock_pct=float(be_lock),
            trail_trigger_pct=float(trail_trigger),
            trail_distance_pct=float(trail_dist),
            profit_lock_trigger_pct=float(profit_trigger),
            profit_lock_pct=float(profit_lock),
            max_pnl_drawdown_trigger_pct=float(max_pnl_drawdown_trigger_pct),
            max_pnl_drawdown_exit_pct=float(max_pnl_drawdown_exit_pct),
            no_progress_minutes=float(no_progress),
            time_exit_minutes=float(time_exit),
            min_seconds_before_be=float(min_seconds_before_be),
            min_hold_seconds=float(min_hold_seconds),
            allow_underlying_structure_exit=bool(allow_structure_exit),
            max_pnl_drawdown_enabled=bool(cfg.max_pnl_drawdown_enabled),
            micro_lock_enabled=bool(cfg.micro_lock_enabled),
        )

    def _fix_session_types(self) -> None:
        s = self._session
        s.setdefault("opens", 0)
        s.setdefault("breakeven_exits", 0)
        s.setdefault("trailing_exits", 0)
        s.setdefault("invalidation_exits", 0)
        s.setdefault("no_progress_exits", 0)
        s.setdefault("fast_fail_exits", 0)
        s.setdefault("time_cap_exits", 0)
        s.setdefault("eod_exits", 0)
        s.setdefault("pnl_pcts", [])
        s.setdefault("max_excursions", [])
        s.setdefault("drawdowns_hwm", [])
        s.setdefault("min_to_breakeven", [])
        s.setdefault("min_to_trailing", [])
        s.setdefault(
            "exit_premium_sources",
            {"exact": 0, "nearest": 0, "delta_estimate": 0, "underlying_proxy": 0},
        )
        s.setdefault("degraded_quote_count", 0)
        s.setdefault("degraded_exit_count", 0)
        s.setdefault("skipped_entry_due_to_no_quote", 0)
        s.setdefault("live_quote_tick_hits", 0)
        s.setdefault("live_quote_tick_total", 0)

    def reset_session_exit_metrics(self) -> None:
        self._session = {
            "opens": 0,
            "breakeven_exits": 0,
            "trailing_exits": 0,
            "invalidation_exits": 0,
            "no_progress_exits": 0,
            "fast_fail_exits": 0,
            "time_cap_exits": 0,
            "eod_exits": 0,
            "pnl_pcts": [],
            "max_excursions": [],
            "drawdowns_hwm": [],
            "min_to_breakeven": [],
            "min_to_trailing": [],
            "exit_premium_sources": {"exact": 0, "nearest": 0, "delta_estimate": 0, "underlying_proxy": 0},
            "degraded_quote_count": 0,
            "degraded_exit_count": 0,
            "skipped_entry_due_to_no_quote": 0,
            "live_quote_tick_hits": 0,
            "live_quote_tick_total": 0,
        }

    def register_on_open(
        self,
        position_id: str,
        *,
        entry_premium_per_contract: float,
        underlying_entry: float,
        option_side: str,
        line_geometry: str,
        strike: float,
        delta: float,
        trendline_dict: Dict[str, Any],
        metadata_target: Dict[str, Any],
        underlying_symbol: str = "",
        expiration_ymd: str = "",
        setup_type: Optional[str] = None,
        trigger_direction: Optional[str] = None,
        trendline_mode: Optional[str] = None,
        impulse_mode: Optional[bool] = None,
        early_entry_mode: Optional[bool] = None,
        position_type: Optional[str] = None,
        strategy_name: str = "",
        strategy_type: str = "",
        entry_value: Optional[float] = None,
        entry_debit: Optional[float] = None,
        entry_credit: Optional[float] = None,
        legs: Optional[List[Dict[str, Any]]] = None,
        structure_invalidation_enabled: Optional[bool] = None,
        source_path: str = "",
        break_archetype: Optional[str] = None,
    ) -> None:
        norm_position_type = _normalize_position_type(position_type, option_side)
        norm_legs = _normalize_legs(legs, option_side, strike, delta)
        arch_norm = str(break_archetype or "").strip().lower()
        spread_entry_value = float(
            entry_value
            if entry_value is not None
            else (entry_credit if norm_position_type == OptionPositionType.CREDIT_SPREAD.value else entry_debit)
            if (entry_debit is not None or entry_credit is not None)
            else entry_premium_per_contract
        )
        miss, reg_warn = validate_stealth_registration_inputs(
            norm_position_type=norm_position_type,
            underlying_symbol=underlying_symbol,
            expiration_ymd=expiration_ymd,
            norm_legs=norm_legs,
            spread_entry_value=spread_entry_value,
            entry_debit=entry_debit,
            entry_credit=entry_credit,
        )
        if miss or reg_warn:
            log.info(
                "OPTIONS_STEALTH | stage=runtime_validation_warning | trade_id=%s | missing=%s | warnings=%s",
                position_id,
                ",".join(miss) if miss else "none",
                ",".join(reg_warn) if reg_warn else "none",
            )
        st = OptionStealthState(
            entry_premium=entry_premium_per_contract,
            premium_hwm=entry_premium_per_contract,
            premium_lwm=entry_premium_per_contract,
            last_effective_premium=entry_premium_per_contract,
            option_side=option_side,
            line_geometry=str(line_geometry).lower(),
            delta_at_entry=delta,
            strike=strike,
            trendline=dict(trendline_dict or {}),
            last_hwm_at=datetime.now(timezone.utc),
            position_type=norm_position_type,
            strategy_name=str(strategy_name or ""),
            strategy_type=str(strategy_type or ""),
            entry_value=spread_entry_value,
            entry_debit=float(entry_debit) if entry_debit is not None else None,
            entry_credit=float(entry_credit) if entry_credit is not None else None,
            legs=norm_legs,
            leg_count=len(norm_legs),
            spread_hwm=spread_entry_value,
            spread_lwm=spread_entry_value,
            structure_invalidation_enabled_override=structure_invalidation_enabled,
            trendline_mode=str(trendline_mode or "STANDARD").upper(),
            impulse_mode=bool(impulse_mode),
            break_archetype=arch_norm,
            # early-entry is represented via trendline_mode tag.
        )
        be_trigger_used, delta_bucket = self._delta_aware_be_trigger(delta)
        st.be_trigger_pct_used = be_trigger_used
        st.delta_bucket = delta_bucket
        block = {
            "state": st,
            "entry_underlying": underlying_entry,
            "underlying_symbol": underlying_symbol or "",
            "expiration_ymd": expiration_ymd or "",
            "is_0dte": bool(expiration_ymd),
            "setup_type": setup_type or "",
            "trigger_direction": trigger_direction or "",
            "source_path": str(source_path or ""),
            "trendline_mode": str(trendline_mode or "STANDARD").upper(),
            "impulse_mode": bool(impulse_mode),
            "early_entry_mode": bool(early_entry_mode),
            "break_archetype": arch_norm,
        }
        metadata_target["option_stealth"] = {
            "engine": block,
            "position_type": norm_position_type,
            "leg_count": len(norm_legs),
            "legs": norm_legs,
            "strategy_name": strategy_name or "",
            "strategy_type": strategy_type or "",
            "underlying_symbol": underlying_symbol or "",
            "expiration_ymd": expiration_ymd or "",
            "entry_value": spread_entry_value,
            "entry_debit": float(entry_debit) if entry_debit is not None else None,
            "entry_credit": float(entry_credit) if entry_credit is not None else None,
            "entry_premium": entry_premium_per_contract,
            "premium_hwm": entry_premium_per_contract,
            "premium_lwm": entry_premium_per_contract,
            "delta_at_entry": delta,
            "strike": strike,
            "breakeven_activated_at": None,
            "trailing_activated_at": None,
            "breakeven_active": False,
            "trailing_active": False,
            "exit_reason": None,
            "exit_premium": None,
            "current_effective_premium": entry_premium_per_contract,
            "last_premium_source": "entry",
            "delta_bucket": delta_bucket,
            "be_trigger_pct_used": be_trigger_used,
            "be_lock_pct_used": None,
            "be_activation_hwm": None,
            "effective_stop_level": None,
            "last_premium_update_ts": None,
            "last_underlying_update_ts": None,
            "stale_data_detected": False,
            "degraded_start_timestamp": None,
            "fast_fail_active": bool(self.config.fast_fail_enabled),
            "premium_jump_detected": False,
            "spread_current_value": spread_entry_value,
            "spread_entry_value": spread_entry_value,
            "spread_high_water_mark": spread_entry_value,
            "spread_low_water_mark": spread_entry_value,
            "spread_exit_reason": None,
            "spread_premium_source_quality": None,
            "leg_quote_sources": [],
            "source_path": str(source_path or ""),
            "trendline_mode": str(trendline_mode or "STANDARD").upper(),
            "impulse_mode": bool(impulse_mode),
            "early_entry_mode": bool(early_entry_mode),
            "break_archetype": arch_norm,
        }
        self._session["opens"] = int(self._session.get("opens", 0)) + 1
        struct_inv_eff = (
            bool(structure_invalidation_enabled)
            if structure_invalidation_enabled is not None
            else (
                str(strategy_type or "").lower() == "trendline_0dte"
                or str(strategy_name or "").lower() == "easytrendline_0dte"
            )
        )
        log.info(
            "OPTIONS_STEALTH | stage=register_on_open | trade_id=%s | position_type=%s | strategy_type=%s | "
            "strategy_name=%s | structure_invalidation_enabled=%s | leg_count=%d | expiry=%s | source_path=%s",
            position_id,
            norm_position_type,
            strategy_type or "",
            strategy_name or "",
            struct_inv_eff,
            len(norm_legs),
            expiration_ymd or "",
            str(source_path or "") or "unspecified",
        )
        log.info(
            "OPTION_LIFECYCLE_OPEN | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | "
            "entry_premium=%.6f | entry_underlying=%.6f | source_path=%s",
            position_id,
            underlying_symbol or "",
            str(strategy_type or ""),
            norm_position_type,
            float(entry_premium_per_contract or 0.0),
            float(underlying_entry or 0.0),
            str(source_path or "") or "unspecified",
        )
        log.info(
            "OPTIONS_STEALTH | stage=position_type_detected | trade_id=%s | position_type=%s | leg_count=%d | strategy_type=%s",
            position_id,
            norm_position_type,
            len(norm_legs),
            strategy_type or "",
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=option_stealth_init | trade_id=%s | strike=%.2f | delta=%.3f | premium=%.4f | side=%s",
            position_id,
            strike,
            delta,
            entry_premium_per_contract,
            option_side,
        )

    def _merge_live_snapshot(
        self,
        osnap: Dict[str, Any],
        st: OptionStealthState,
        prem: float,
        prem_src: str,
        detail: Dict[str, Any],
        current_underlying: float,
        pnl_pct: float,
        structure_ok: bool,
    ) -> None:
        osnap["current_effective_premium"] = prem
        osnap["premium_hwm"] = st.premium_hwm
        osnap["premium_lwm"] = st.premium_lwm
        osnap["current_underlying_price"] = current_underlying
        osnap["current_pnl_pct_per_contract"] = pnl_pct
        osnap["last_premium_source"] = prem_src
        osnap["option_bid"] = detail.get("option_bid")
        osnap["option_ask"] = detail.get("option_ask")
        osnap["option_mark"] = detail.get("option_mark")
        osnap["option_last"] = detail.get("option_last")
        osnap["modeled_premium"] = detail.get("modeled_premium")
        osnap["effective_premium_used"] = detail.get("effective_premium_used")
        osnap["breakeven_active"] = st.breakeven_floor is not None
        osnap["breakeven_lock_premium"] = st.breakeven_floor
        osnap["trailing_active"] = st.trailing_activated_at is not None
        osnap["trailing_mode"] = st.mode.value
        osnap["trailing_stop_level"] = st.trail_stop_premium
        osnap["structure_still_valid"] = structure_ok
        osnap["structure_invalidation_reason"] = st.structure_invalidation_reason or None
        osnap["delta_bucket"] = st.delta_bucket
        osnap["be_trigger_pct_used"] = st.be_trigger_pct_used
        osnap["be_lock_pct_used"] = st.be_lock_pct_used
        osnap["last_premium_update_ts"] = st.last_premium_update_at.isoformat() if st.last_premium_update_at else None
        osnap["last_underlying_update_ts"] = st.last_underlying_update_at.isoformat() if st.last_underlying_update_at else None
        osnap["stale_data_detected"] = st.stale_data_detected
        osnap["premium_jump_detected"] = st.premium_jump_detected
        osnap["fast_fail_active"] = bool(self.config.fast_fail_enabled)
        effective_stop = st.trail_stop_premium if st.trail_stop_premium is not None else st.breakeven_floor
        osnap["effective_stop_level"] = effective_stop
        osnap["position_type"] = st.position_type
        osnap["leg_count"] = st.leg_count
        osnap["strategy_name"] = st.strategy_name
        osnap["strategy_type"] = st.strategy_type
        osnap["spread_entry_value"] = st.entry_value
        osnap["spread_high_water_mark"] = st.spread_hwm
        osnap["spread_low_water_mark"] = st.spread_lwm
        if st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        ):
            osnap["spread_premium_source_quality"] = detail.get("spread_premium_source_quality")
            osnap["spread_monitoring_mode"] = detail.get("spread_monitoring_mode")
            osnap["leg_quote_sources"] = detail.get("leg_quote_sources", [])

    def _build_exit_diagnostics(
        self,
        *,
        position_id: str,
        position_symbol: str,
        block: Dict[str, Any],
        st: OptionStealthState,
        osnap: Dict[str, Any],
        exit_premium: float,
        exit_reason: str,
        now: datetime,
        prem_detail: Dict[str, Any],
        premium_source_at_exit: str,
    ) -> Dict[str, Any]:
        entry_ts = self._normalize_dt(st.opened_at)
        entry_prem = st.entry_premium
        pnl_pct = (exit_premium - entry_prem) / entry_prem if entry_prem > 0 else 0.0
        contracts = 1
        realized_pnl = (exit_premium - entry_prem) * 100.0 * contracts
        held_min = (now - entry_ts).total_seconds() / 60.0
        hwm = st.premium_hwm
        lwm = st.premium_lwm
        max_exc = (hwm - entry_prem) / entry_prem if entry_prem > 0 else 0.0
        dd_hwm = (hwm - exit_premium) / hwm if hwm > 0 else 0.0
        be_min: Optional[float] = None
        if st.breakeven_activated_at:
            be_min = (self._normalize_dt(st.breakeven_activated_at) - entry_ts).total_seconds() / 60.0
        tr_min: Optional[float] = None
        if st.trailing_activated_at:
            tr_min = (self._normalize_dt(st.trailing_activated_at) - entry_ts).total_seconds() / 60.0

        return {
            "symbol": position_symbol,
            "underlying_symbol": block.get("underlying_symbol") or position_symbol,
            "trade_id": position_id,
            "option_side": st.option_side,
            "strike": st.strike,
            "expiration_ymd": block.get("expiration_ymd") or "",
            "is_0dte": block.get("is_0dte"),
            "setup_type": block.get("setup_type") or "",
            "trigger_direction": block.get("trigger_direction") or "",
            "source_path": block.get("source_path") or "",
            "entry_archetype": block.get("entry_archetype") or "generic",
            "entry_timestamp": entry_ts.isoformat(),
            "entry_premium": entry_prem,
            "premium_high_water_mark": hwm,
            "premium_low_water_mark": lwm,
            "effective_exit_premium": exit_premium,
            "exit_price": exit_premium,
            "premium_source_at_exit": premium_source_at_exit,
            "exit_source": premium_source_at_exit,
            "option_bid_at_exit": prem_detail.get("option_bid"),
            "option_ask_at_exit": prem_detail.get("option_ask"),
            "option_mark_at_exit": prem_detail.get("option_mark"),
            "option_last_at_exit": prem_detail.get("option_last"),
            "modeled_premium_at_exit": prem_detail.get("modeled_premium"),
            "realized_pnl_per_contract_dollars": realized_pnl,
            "realized_pnl_pct": pnl_pct,
            "pnl_pct": pnl_pct,
            "max_pnl_pct": max_exc,
            "exit_reason": exit_reason,
            "exit_timestamp": now.isoformat(),
            "breakeven_activated": st.breakeven_floor is not None,
            "breakeven_activation_time": st.breakeven_activated_at.isoformat() if st.breakeven_activated_at else None,
            "breakeven_lock_premium": st.breakeven_floor,
            "trailing_activated": st.trailing_activated_at is not None,
            "trailing_activation_time": st.trailing_activated_at.isoformat() if st.trailing_activated_at else None,
            "trailing_mode": st.mode.value,
            "trailing_stop_level": st.trail_stop_premium,
            "structure_invalidation_triggered": st.structure_invalidation_triggered,
            "structure_invalidation_reason": st.structure_invalidation_reason or "",
            "no_progress_timeout_triggered": st.no_progress_timeout_triggered,
            "time_in_trade_minutes": held_min,
            "max_premium_excursion_pct": max_exc,
            "drawdown_from_premium_hwm_pct": dd_hwm,
            "minutes_to_breakeven": be_min,
            "minutes_to_trailing_activation": tr_min,
            "delta_bucket": st.delta_bucket,
            "be_trigger_pct_used": st.be_trigger_pct_used,
            "be_lock_pct_used": st.be_lock_pct_used,
            "stale_data_detected": st.stale_data_detected,
            "premium_jump_detected": st.premium_jump_detected,
            "position_type": st.position_type,
            "leg_count": st.leg_count,
            "strategy_name": st.strategy_name,
            "strategy_type": st.strategy_type,
            "trendline_mode": st.trendline_mode,
            "entry_mode": (
                "impulse"
                if bool(st.impulse_mode) or str(st.trendline_mode or "").upper() == "IMPULSE"
                else "slow_trend"
                if str(st.trendline_mode or "").upper() == "SLOW_TREND"
                else "early_entry"
                if str(st.trendline_mode or "").upper() == "EARLY_ENTRY"
                else "standard"
            ),
            "spread_entry_value": st.entry_value,
            "spread_current_value": exit_premium,
            "spread_high_water_mark": st.spread_hwm,
            "spread_low_water_mark": st.spread_lwm,
            "spread_exit_reason": exit_reason,
            "spread_premium_source_quality": prem_detail.get("spread_premium_source_quality"),
            "spread_monitoring_mode": prem_detail.get("spread_monitoring_mode"),
            "leg_quote_sources": prem_detail.get("leg_quote_sources", []),
            "data_quality": {
                "quote_age_seconds": prem_detail.get("quote_age_seconds"),
                "spread_pct": prem_detail.get("spread_pct"),
                "fallback_used": prem_detail.get("fallback_used"),
            },
            "degraded_data_source": osnap.get("degraded_data_source"),
            "live_degraded_reason": osnap.get("last_live_degraded_reason"),
            "mark_quality_at_exit": prem_detail.get("mark_quality") or osnap.get("mark_quality"),
            "mark_is_exit_grade_at_exit": osnap.get("mark_is_exit_grade"),
            "had_underlying_opportunity_at_exit": bool(getattr(st, "had_underlying_opportunity", False)),
            "max_underlying_favorable_move_pct_at_exit": float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
        }

    def _record_exit(self, reason: str, pnl_pct: float, src: str, diag: Dict[str, Any]) -> None:
        self._fix_session_types()
        s = self._session
        log.info(
            "OPTION_LIFECYCLE_EXIT | trade_id=%s | symbol=%s | strategy_type=%s | reason=%s | "
            "pnl_pct=%.4f | exit_premium=%.6f | entry_premium=%.6f | premium_source=%s",
            str(diag.get("trade_id") or ""),
            str(diag.get("symbol") or ""),
            str(diag.get("strategy_type") or ""),
            reason,
            float(pnl_pct),
            float(diag.get("exit_premium_per_contract") or 0.0),
            float(diag.get("entry_premium_per_contract") or 0.0),
            src,
        )
        max_pnl_pct = float(diag.get("max_pnl_pct") or diag.get("max_premium_excursion_pct") or 0.0)
        exit_pnl_pct = float(diag.get("realized_pnl_pct") or diag.get("pnl_pct") or pnl_pct)
        profit_leak_pct = max(0.0, max_pnl_pct - exit_pnl_pct)
        retained_mfe_ratio = 0.0
        if max_pnl_pct > 1e-9:
            retained_mfe_ratio = max(0.0, min(1.0, exit_pnl_pct / max_pnl_pct))
        log.info(
            "OPTIONS_STEALTH_MFE_RETENTION | trade_id=%s | symbol=%s | source_path=%s | entry_archetype=%s | position_type=%s | "
            "max_pnl_pct=%.4f | exit_pnl_pct=%.4f | profit_leak_pct=%.4f | retained_mfe_ratio=%.4f | exit_reason=%s | premium_source_at_exit=%s",
            str(diag.get("trade_id") or ""),
            str(diag.get("symbol") or ""),
            str(diag.get("source_path") or "unknown"),
            str(diag.get("entry_archetype") or "generic"),
            str(diag.get("position_type") or ""),
            max_pnl_pct,
            exit_pnl_pct,
            profit_leak_pct,
            retained_mfe_ratio,
            reason,
            str(diag.get("premium_source_at_exit") or src),
        )
        stype = str(diag.get("strategy_type") or "").lower()
        sname = str(diag.get("strategy_name") or "").lower()
        if stype == "trendline_0dte" or sname == "easytrendline_0dte":
            log.info(
                "TRENDLINE_TRADE_OUTCOME | trade_id=%s | symbol=%s | pnl_pct=%.6f | max_pnl_pct=%.6f | exit_reason=%s | time_held_minutes=%.4f",
                str(diag.get("trade_id") or ""),
                str(diag.get("symbol") or ""),
                float(diag.get("realized_pnl_pct") or diag.get("pnl_pct") or pnl_pct),
                float(diag.get("max_premium_excursion_pct") or diag.get("max_pnl_pct") or 0.0),
                reason,
                float(diag.get("time_in_trade_minutes") or 0.0),
            )
            ddm = float(diag.get("drawdown_from_premium_hwm_pct") or max(0.0, max_pnl_pct - exit_pnl_pct))
            held_sec = float(diag.get("time_in_trade_minutes") or 0.0) * 60.0
            log.info(
                "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | grep=TRENDLINE_EXIT_POSTMORTEM | final_reason=%s | "
                "pnl_pct=%.6f | max_pnl_pct=%.6f | drawdown_from_mfe=%.6f | held_sec=%.1f | mark_quality=%s | mark_is_exit_grade=%s | "
                "had_underlying_opportunity=%s | max_underlying_favorable_pct=%.5f | exit_allowed=true",
                str(diag.get("trade_id") or ""),
                str(diag.get("symbol") or ""),
                reason,
                float(diag.get("realized_pnl_pct") or diag.get("pnl_pct") or pnl_pct),
                max_pnl_pct,
                ddm,
                held_sec,
                str(diag.get("mark_quality_at_exit") or ""),
                str(diag.get("mark_is_exit_grade_at_exit")).lower()
                if diag.get("mark_is_exit_grade_at_exit") is not None
                else "unknown",
                str(bool(diag.get("had_underlying_opportunity_at_exit"))).lower(),
                float(diag.get("max_underlying_favorable_move_pct_at_exit") or 0.0),
            )
        if reason.startswith("structure_invalidation"):
            s["invalidation_exits"] = int(s["invalidation_exits"]) + 1
        elif reason == "breakeven_stop":
            s["breakeven_exits"] = int(s["breakeven_exits"]) + 1
        elif reason == "trailing_stop":
            s["trailing_exits"] = int(s["trailing_exits"]) + 1
        elif reason in ("no_progress_timeout", "no_progress_timeout_early"):
            s["no_progress_exits"] = int(s["no_progress_exits"]) + 1
        elif reason == "fast_fail":
            s["fast_fail_exits"] = int(s["fast_fail_exits"]) + 1
        elif reason == "time_exit":
            s["time_cap_exits"] = int(s["time_cap_exits"]) + 1
        elif reason in ("end_of_day_close", "eod_no_state"):
            s["eod_exits"] = int(s["eod_exits"]) + 1
        s["pnl_pcts"].append(float(pnl_pct))
        s["max_excursions"].append(float(diag.get("max_premium_excursion_pct") or 0.0))
        s["drawdowns_hwm"].append(float(diag.get("drawdown_from_premium_hwm_pct") or 0.0))
        mb = diag.get("minutes_to_breakeven")
        if mb is not None:
            s["min_to_breakeven"].append(float(mb))
        mt = diag.get("minutes_to_trailing_activation")
        if mt is not None:
            s["min_to_trailing"].append(float(mt))
        ps = s["exit_premium_sources"]
        if isinstance(ps, dict):
            ps[src] = int(ps.get(src, 0)) + 1
        if self.config.require_live_quotes and reason in (
            "degraded_data_no_fallback",
            "degraded_data_outage",
            "option_force_exit_no_data",
        ):
            dds = str(diag.get("degraded_data_source") or "")
            pse = str(diag.get("premium_source_at_exit") or src)
            if (
                dds == "live_quote_unavailable"
                or pse == "live_quote_unavailable"
                or reason == "option_force_exit_no_data"
            ):
                s["degraded_exit_count"] = int(s.get("degraded_exit_count", 0)) + 1

    def bump_skipped_entry_no_quote(self, symbol: str, trade_id: str = "-") -> None:
        """Entry blocked due to missing live option chain/quotes (Trendline 0DTE path)."""
        if not self.config.require_live_quotes:
            return
        self._fix_session_types()
        self._session["skipped_entry_due_to_no_quote"] = int(self._session.get("skipped_entry_due_to_no_quote", 0)) + 1
        _emit_option_degraded_live_quote_log(
            self.config,
            symbol=str(symbol or "-"),
            trade_id=str(trade_id or "-"),
            reason="no_quote",
            action_taken="skip_entry",
            dedupe_key=f"skip|{symbol}|{trade_id}",
        )

    def log_options_exit_summary(self) -> None:
        self._fix_session_types()
        s = self._session
        n = len(s["pnl_pcts"])
        avg_pnl = sum(s["pnl_pcts"]) / n if n else 0.0
        avg_exc = sum(s["max_excursions"]) / len(s["max_excursions"]) if s["max_excursions"] else 0.0
        avg_dd = sum(s["drawdowns_hwm"]) / len(s["drawdowns_hwm"]) if s["drawdowns_hwm"] else 0.0
        avg_be = sum(s["min_to_breakeven"]) / len(s["min_to_breakeven"]) if s["min_to_breakeven"] else 0.0
        avg_tr = sum(s["min_to_trailing"]) / len(s["min_to_trailing"]) if s["min_to_trailing"] else 0.0
        ps = s.get("exit_premium_sources", {})
        ps_s = ",".join(f"{k}:{v}" for k, v in sorted(ps.items())) if isinstance(ps, dict) else ""
        lq_total = int(s.get("live_quote_tick_total", 0) or 0)
        lq_hits = int(s.get("live_quote_tick_hits", 0) or 0)
        if bool(self.config.require_live_quotes) and lq_total > 0:
            live_quote_availability_pct = 100.0 * float(lq_hits) / float(lq_total)
        elif bool(self.config.require_live_quotes):
            live_quote_availability_pct = 0.0
        else:
            live_quote_availability_pct = -1.0
        log.info(
            "TRENDLINE_PIPELINE | stage=options_exit_summary | executed=%s | breakeven_exits=%s | trailing_exits=%s | "
            "invalidation_exits=%s | no_progress_exits=%s | fast_fail_exits=%s | time_cap_exits=%s | eod_exits=%s | avg_pnl_pct=%.6f | "
            "avg_max_premium_excursion_pct=%.6f | avg_drawdown_from_hwm_pct=%.6f | avg_min_to_breakeven=%.4f | "
            "avg_min_to_trailing=%.4f | premium_sources=%s | live_quote_availability_pct=%s | "
            "live_quote_ticks=%d/%d | degraded_quote_count=%d | degraded_exit_count=%d | skipped_entry_due_to_no_quote=%d | "
            "OPTION_REQUIRE_LIVE_QUOTES=%s",
            s.get("opens", 0),
            s.get("breakeven_exits", 0),
            s.get("trailing_exits", 0),
            s.get("invalidation_exits", 0),
            s.get("no_progress_exits", 0),
            s.get("fast_fail_exits", 0),
            s.get("time_cap_exits", 0),
            s.get("eod_exits", 0),
            avg_pnl,
            avg_exc,
            avg_dd,
            avg_be,
            avg_tr,
            ps_s,
            (f"{live_quote_availability_pct:.1f}" if live_quote_availability_pct >= 0.0 else "n/a"),
            lq_hits,
            lq_total,
            int(s.get("degraded_quote_count", 0) or 0),
            int(s.get("degraded_exit_count", 0) or 0),
            int(s.get("skipped_entry_due_to_no_quote", 0) or 0),
            str(bool(self.config.require_live_quotes)).lower(),
        )

    def process_position(
        self,
        position_id: str,
        *,
        position_symbol: str,
        position_metadata: Dict[str, Any],
        current_underlying: float,
        now: Optional[datetime] = None,
        option_quote: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[float, str, Dict[str, Any]]]:
        """
        Return (exit_premium, reason, telemetry) to close, or None to hold.
        """
        now = self._normalize_dt(now or datetime.now(timezone.utc))
        osnap = position_metadata.get("option_stealth")
        if not isinstance(osnap, dict):
            return None
        block = osnap.get("engine")
        if not isinstance(block, dict):
            return None
        st: OptionStealthState = block["state"]
        entry_u = float(block.get("entry_underlying", current_underlying) or current_underlying)

        if not osnap.get("_stealth_process_handoff_logged"):
            osnap["_stealth_process_handoff_logged"] = True
            log.info(
                "OPTIONS_STEALTH | stage=process_position | trade_id=%s | position_type=%s | strategy_type=%s | is_spread=%s",
                position_id,
                st.position_type,
                st.strategy_type or "",
                st.position_type
                in (
                    OptionPositionType.DEBIT_SPREAD.value,
                    OptionPositionType.CREDIT_SPREAD.value,
                ),
            )

        prev_eff = st.last_effective_premium if st.last_effective_premium > 0 else None
        is_spread = st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        )
        is_trendline_0dte = str(st.strategy_type or "").lower() == "trendline_0dte" or str(
            st.strategy_name or ""
        ).lower() == "easytrendline_0dte"
        is_orb_0dte = str(st.strategy_type or "").lower() == "orb_0dte"
        is_0dte_single = bool(is_orb_0dte or is_trendline_0dte) and (not is_spread)
        trendline_mode = str(getattr(st, "trendline_mode", "") or osnap.get("trendline_mode") or "").upper()
        is_impulse_mode = bool(
            getattr(st, "impulse_mode", False)
            or str(getattr(st, "trendline_mode", "") or "").upper() == "IMPULSE"
            or bool(osnap.get("impulse_mode"))
            or str(osnap.get("trendline_mode") or "").upper() == "IMPULSE"
        )
        is_slow_trend_mode = trendline_mode == "SLOW_TREND" or bool(osnap.get("slow_trend_mode"))
        is_early_entry_mode = trendline_mode == "EARLY_ENTRY" or bool(osnap.get("early_entry_mode"))
        is_retest_mode = trendline_mode == "RETEST" or bool(osnap.get("retest_mode"))
        osnap["trendline_mode"] = trendline_mode or "STANDARD"
        osnap["entry_mode"] = (
            "impulse"
            if is_impulse_mode
            else "slow_trend"
            if is_slow_trend_mode
            else "early_entry"
            if is_early_entry_mode
            else "standard"
        )
        u_move_pct_eval = 0.0
        if entry_u > 0 and current_underlying > 0:
            if str(st.option_side or "").lower() == "put":
                u_move_pct_eval = (float(entry_u) - float(current_underlying)) / float(entry_u)
            else:
                u_move_pct_eval = (float(current_underlying) - float(entry_u)) / float(entry_u)
        underlying_confirms_hwm = False
        if is_trendline_0dte and (not is_spread) and entry_u > 0 and current_underlying > 0:
            u_thr_opp = float(self.config.tline_underlying_opportunity_pct)
            underlying_confirms_hwm = bool(float(u_move_pct_eval) >= float(u_thr_opp) - 1e-15)
            try:
                st.max_underlying_favorable_move_pct = max(
                    float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
                    float(u_move_pct_eval),
                )
            except Exception:
                pass
            if underlying_confirms_hwm:
                st.had_underlying_opportunity = True
                osnap["had_underlying_opportunity"] = True
                st.had_opportunity = True
                osnap["had_opportunity"] = True
        elif is_orb_0dte and is_spread and entry_u > 0 and current_underlying > 0:
            if bool(getattr(self.config, "orb_degraded_protect_enable", True)):
                u_thr_opp = float(getattr(self.config, "orb_underlying_opportunity_pct", 0.0025))
                orb_under_confirms = bool(float(u_move_pct_eval) >= float(u_thr_opp) - 1e-15)
                try:
                    st.max_underlying_favorable_move_pct = max(
                        float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
                        float(u_move_pct_eval),
                    )
                except Exception:
                    pass
                if orb_under_confirms:
                    st.had_underlying_opportunity = True
                    osnap["had_underlying_opportunity"] = True
                    st.had_opportunity = True
                    osnap["had_opportunity"] = True
        source_path = str(block.get("source_path") or osnap.get("source_path") or "").lower()
        break_arch = str(
            getattr(st, "break_archetype", "")
            or block.get("break_archetype")
            or osnap.get("break_archetype")
            or ""
        ).strip().lower()
        if break_arch:
            osnap.setdefault("break_archetype", break_arch)
        parent_normalized = (
            position_metadata.get("normalized_options")
            if isinstance(position_metadata.get("normalized_options"), dict)
            else None
        )
        osnap_normalized = osnap.get("normalized_options") if isinstance(osnap.get("normalized_options"), dict) else None
        normalized = parent_normalized if isinstance(parent_normalized, dict) else osnap_normalized or {}
        if not isinstance(normalized, dict) or not normalized.get("position_type"):
            log.warning(
                "OPTIONS_STEALTH_METADATA_INVALID | trade_id=%s | symbol=%s | reason=missing_normalized_options",
                position_id,
                position_symbol,
            )
        resolved_strategy_type = str(
            normalized.get("strategy_type")
            or normalized.get("source_path")
            or st.strategy_type
            or ""
        )
        entry_archetype = self._resolve_entry_archetype(
            source_path=source_path,
            is_orb_0dte=is_orb_0dte,
            trendline_mode=trendline_mode,
            impulse_mode=is_impulse_mode,
            is_trendline_0dte=is_trendline_0dte,
            trendline_break_archetype=break_arch,
        )
        log.info(
            "OPTIONS_STEALTH_PROFILE_SOURCE_AUDIT | trade_id=%s | symbol=%s | parent_normalized_metadata_present=%s | "
            "osnap_normalized_metadata_present=%s | resolved_source_path=%s | resolved_strategy_type=%s | is_orb_0dte=%s | "
            "is_trendline_0dte=%s | entry_archetype=%s",
            position_id,
            position_symbol,
            str(bool(isinstance(parent_normalized, dict))).lower(),
            str(bool(isinstance(osnap_normalized, dict))).lower(),
            source_path or "unknown",
            resolved_strategy_type or "unknown",
            str(bool(is_orb_0dte)).lower(),
            str(bool(is_trendline_0dte)).lower(),
            entry_archetype,
        )
        profile = self._resolve_exit_profile(
            source_path=source_path,
            position_type=str(st.position_type or ""),
            entry_archetype=entry_archetype,
            is_orb_0dte=is_orb_0dte,
            is_trendline_0dte=is_trendline_0dte,
            trendline_break_archetype=break_arch,
        )
        block["entry_archetype"] = entry_archetype
        block["source_path"] = source_path or str(block.get("source_path") or "")
        profile_sig = (
            profile.source_path,
            profile.entry_archetype,
            profile.position_type,
            round(profile.be_trigger_pct, 6),
            round(profile.be_lock_pct, 6),
            round(profile.trail_trigger_pct, 6),
            round(profile.trail_distance_pct, 6),
            round(profile.profit_lock_trigger_pct, 6),
            round(profile.profit_lock_pct, 6),
            round(profile.max_pnl_drawdown_trigger_pct, 6),
            round(profile.max_pnl_drawdown_exit_pct, 6),
            round(profile.no_progress_minutes, 4),
            round(profile.time_exit_minutes, 4),
            round(profile.min_seconds_before_be, 4),
            round(profile.min_hold_seconds, 4),
            bool(profile.allow_underlying_structure_exit),
        )
        if osnap.get("_profile_sig") != profile_sig:
            osnap["_profile_sig"] = profile_sig
            log.warning(
                "OPTIONS_STEALTH_PROFILE_RESOLVED | trade_id=%s | symbol=%s | source_path=%s | entry_archetype=%s | position_type=%s | "
                "be_trigger_pct=%.4f | be_lock_pct=%.4f | trail_trigger_pct=%.4f | trail_distance_pct=%.4f | "
                "profit_lock_trigger_pct=%.4f | profit_lock_pct=%.4f | max_pnl_drawdown_trigger_pct=%.4f | "
                "max_pnl_drawdown_exit_pct=%.4f | no_progress_minutes=%.2f | time_exit_minutes=%.2f",
                position_id,
                position_symbol,
                profile.source_path,
                profile.entry_archetype,
                profile.position_type,
                profile.be_trigger_pct,
                profile.be_lock_pct,
                profile.trail_trigger_pct,
                profile.trail_distance_pct,
                profile.profit_lock_trigger_pct,
                profile.profit_lock_pct,
                profile.max_pnl_drawdown_trigger_pct,
                profile.max_pnl_drawdown_exit_pct,
                profile.no_progress_minutes,
                profile.time_exit_minutes,
            )

        def _trendline_structure_invalid() -> bool:
            trendline_line_px = _line_value_at(st.trendline, now) if st.trendline else 0.0
            break_high = (
                osnap.get("break_candle_high")
                or osnap.get("break_high")
                or (st.trendline.get("break_candle_high") if isinstance(st.trendline, dict) else None)
                or (st.trendline.get("break_high") if isinstance(st.trendline, dict) else None)
                or (st.trendline.get("rearm_break_candle_high") if isinstance(st.trendline, dict) else None)
            )
            break_low = (
                osnap.get("break_candle_low")
                or osnap.get("break_low")
                or (st.trendline.get("break_candle_low") if isinstance(st.trendline, dict) else None)
                or (st.trendline.get("break_low") if isinstance(st.trendline, dict) else None)
                or (st.trendline.get("rearm_break_candle_low") if isinstance(st.trendline, dict) else None)
            )
            current_high = float(osnap.get("current_high") or current_underlying)
            current_low = float(osnap.get("current_low") or current_underlying)
            break_high_f = float(break_high) if isinstance(break_high, (int, float)) else None
            break_low_f = float(break_low) if isinstance(break_low, (int, float)) else None
            is_put_side = str(st.option_side or "").lower() == "put"
            structure_invalid = False
            if trendline_line_px > 0:
                if is_put_side:
                    structure_invalid = (
                        current_underlying > trendline_line_px
                        or (break_high_f is not None and current_high > break_high_f)
                    )
                else:
                    structure_invalid = (
                        current_underlying < trendline_line_px
                        or (break_low_f is not None and current_low < break_low_f)
                    )
            return bool(structure_invalid)
        if is_spread:
            prem, prem_src, detail = resolve_spread_net_value(
                current_underlying=current_underlying,
                entry_underlying=entry_u,
                cfg=self.config,
                previous_effective=prev_eff,
                position_symbol=position_symbol,
                legs=st.legs,
                option_quote=option_quote,
                entry_value=max(0.01, st.entry_value or st.entry_premium),
                position_type=str(st.position_type or OptionPositionType.DEBIT_SPREAD.value),
                trade_id=str(position_id or ""),
            )
            mon_mode = str(detail.get("spread_monitoring_mode") or "")
            spread_log_key = (prem_src, mon_mode)
            if osnap.get("_last_spread_value_log_key") != spread_log_key:
                osnap["_last_spread_value_log_key"] = spread_log_key
                log.info(
                    "OPTIONS_STEALTH | stage=spread_value_update | trade_id=%s | symbol=%s | current_value=%.6f | source=%s | monitoring_mode=%s | quality=%s",
                    position_id,
                    position_symbol,
                    prem,
                    prem_src,
                    mon_mode,
                    detail.get("spread_premium_source_quality"),
                )
            prev_mode = osnap.get("_last_spread_monitoring_mode")
            if mon_mode and mon_mode != prev_mode:
                osnap["_last_spread_monitoring_mode"] = mon_mode
                log.info(
                    "OPTIONS_STEALTH | stage=spread_monitoring_mode | trade_id=%s | symbol=%s | monitoring_mode=%s | premium_source=%s",
                    position_id,
                    position_symbol,
                    mon_mode,
                    prem_src,
                )
            if prem_src in (
                "spread_modeled_fallback",
                "spread_position_value_fallback",
                "spread_mtm_incomplete_quotes",
                "spread_mtm_no_legs",
                "spread_mtm_missing_leg_role",
            ):
                log.info(
                    "OPTIONS_STEALTH | stage=spread_quote_fallback | trade_id=%s | symbol=%s | source=%s | monitoring_mode=%s | degraded=true",
                    position_id,
                    position_symbol,
                    prem_src,
                    mon_mode,
                )
            weak_modes = (
                "leg_derived_mixed_degraded",
                "leg_derived_all_modeled_degraded",
                "position_value_fallback",
                "entry_anchor_fallback",
                "position_value_fallback_no_legs",
                "entry_anchor_fallback_no_legs",
                "spread_mtm_incomplete_quotes",
                "spread_mtm_no_legs",
                "spread_mtm_missing_leg_role",
            )
            if mon_mode in weak_modes and not osnap.get("_spread_degraded_monitoring_logged"):
                osnap["_spread_degraded_monitoring_logged"] = True
                log.info(
                    "OPTIONS_STEALTH | stage=runtime_validation_warning | trade_id=%s | missing=none | degraded_spread_monitoring=%s",
                    position_id,
                    mon_mode,
                )
            if self.config.require_live_option_data and prem_src in (
                "spread_modeled_fallback",
                "spread_position_value_fallback",
                "spread_mtm_incomplete_quotes",
                "spread_mtm_no_legs",
                "spread_mtm_missing_leg_role",
            ):
                if not osnap.get("_live_data_required_skip_logged"):
                    osnap["_live_data_required_skip_logged"] = True
                    log.warning(
                        "OPTIONS_STEALTH | stage=live_data_required_skip | trade_id=%s | symbol=%s | "
                        "reason=degraded_spread_source | source=%s",
                        position_id,
                        position_symbol,
                        prem_src,
                    )
                osnap["degraded_data_active"] = True
                osnap["degraded_data_source"] = prem_src
                if not osnap.get("degraded_data_since"):
                    osnap["degraded_data_since"] = now.isoformat()
                if not osnap.get("degraded_start_timestamp"):
                    osnap["degraded_start_timestamp"] = now.isoformat()
            # ORB spread open grace: protect opening-minute winners from transient leg quote gaps.
            if is_orb_0dte and is_spread:
                seconds_since_entry_spread = max(
                    0.0,
                    (now - self._normalize_dt(st.opened_at)).total_seconds(),
                )
                open_grace_active = bool(osnap.get("open_grace_active"))
                open_grace_source = str(prem_src or "")
                open_grace_eligible = open_grace_source in {
                    "spread_mtm_incomplete_quotes",
                    "spread_mtm_no_legs",
                    "spread_mtm_missing_leg_role",
                    "spread_modeled_fallback",
                    "spread_position_value_fallback",
                }
                open_grace_window = max(30.0, float(self.config.orb_spread_open_grace_seconds))
                if open_grace_eligible and seconds_since_entry_spread <= open_grace_window:
                    open_grace_attempts = int(osnap.get("open_grace_attempts", 0) or 0) + 1
                    osnap["open_grace_attempts"] = open_grace_attempts
                    osnap["open_grace_active"] = True
                    osnap["open_grace_start_ts"] = osnap.get("open_grace_start_ts") or now.isoformat()
                    if self.config.orb_spread_open_grace_enable_synthetic and float(prem or 0.0) <= 0.0:
                        prem = max(
                            0.01,
                            float(st.entry_debit or 0.0) or float(st.entry_value or 0.0) or float(st.entry_premium or 0.0),
                            float(osnap.get("last_valid_option_price") or 0.0),
                        )
                        prem_src = "synthetic_open_grace"
                        detail["price_source_telemetry"] = "synthetic"
                        detail["fallback_used"] = True
                    log.warning(
                        "OPTIONS_STEALTH_OPEN_GRACE_ACTIVE | trade_id=%s | symbol=%s | source=%s | attempts=%d | "
                        "seconds_since_entry=%.1f | grace_seconds=%.1f",
                        position_id,
                        position_symbol,
                        open_grace_source,
                        open_grace_attempts,
                        seconds_since_entry_spread,
                        open_grace_window,
                    )
                elif open_grace_active and prem_src == "spread_mtm_mid_last":
                    osnap["open_grace_active"] = False
                    osnap["open_grace_recovered_ts"] = now.isoformat()
                    log.info(
                        "OPTIONS_STEALTH_OPEN_GRACE_RECOVERED | trade_id=%s | symbol=%s | source=%s | attempts=%s",
                        position_id,
                        position_symbol,
                        prem_src,
                        int(osnap.get("open_grace_attempts", 0) or 0),
                    )
                elif (
                    open_grace_active
                    and seconds_since_entry_spread > open_grace_window
                    and open_grace_eligible
                    and int(osnap.get("open_grace_attempts", 0) or 0)
                    >= int(max(1, self.config.orb_spread_open_grace_max_attempts))
                ):
                    osnap["open_grace_active"] = False
                    osnap["open_grace_failed"] = True
                    log.warning(
                        "OPTIONS_STEALTH_OPEN_GRACE_FAILED | trade_id=%s | symbol=%s | source=%s | attempts=%s | "
                        "seconds_since_entry=%.1f",
                        position_id,
                        position_symbol,
                        open_grace_source,
                        int(osnap.get("open_grace_attempts", 0) or 0),
                        seconds_since_entry_spread,
                    )
        else:
            oq_single = dict(option_quote or {}) if isinstance(option_quote, dict) else {}
            last_valid_px = None
            try:
                last_valid_px = float(osnap.get("last_valid_option_price")) if osnap.get("last_valid_option_price") is not None else None
            except (TypeError, ValueError):
                last_valid_px = None
            last_valid_ts = osnap.get("last_valid_option_quote_ts")
            if (not is_0dte_single) and last_valid_px is not None and isinstance(last_valid_ts, str):
                try:
                    lv_dt = self._normalize_dt(datetime.fromisoformat(last_valid_ts))
                    cached_age = max(0.0, (now - lv_dt).total_seconds())
                    oq_single["cached_price"] = last_valid_px
                    oq_single["cached_age_seconds"] = cached_age
                except Exception:
                    pass
            sec_since_entry = (now - self._normalize_dt(st.opened_at)).total_seconds()
            is_short_prem = bool(osnap.get("is_short_premium"))
            prem, prem_src, detail = resolve_option_price(
                entry_underlying=entry_u,
                current_underlying=current_underlying,
                option_side=st.option_side,
                entry_premium=st.entry_premium,
                cfg=self.config,
                option_quote=oq_single,
                previous_effective=prev_eff,
                delta_at_entry=st.delta_at_entry,
                strike=st.strike,
                position_symbol=position_symbol,
                is_0dte=is_0dte_single,
                seconds_since_entry=sec_since_entry,
                trade_id=str(position_id or ""),
                stored_last_valid_option_price=last_valid_px,
                is_short_premium=is_short_prem,
            )
            px_telem = str(detail.get("price_source_telemetry") or "").strip().lower()
            if px_telem == "live":
                osnap["last_live_degraded_reason"] = None
            elif prem_src == "live_quote_unavailable":
                osnap["last_live_degraded_reason"] = str(detail.get("live_degraded_reason") or "unknown")
            if self.config.require_live_quotes and (not is_spread) and is_0dte_single:
                self._fix_session_types()
                self._session["live_quote_tick_total"] = int(self._session.get("live_quote_tick_total", 0)) + 1
                if px_telem == "live":
                    self._session["live_quote_tick_hits"] = int(self._session.get("live_quote_tick_hits", 0)) + 1
                if prem_src == "live_quote_unavailable":
                    self._session["degraded_quote_count"] = int(self._session.get("degraded_quote_count", 0)) + 1
                    _emit_option_degraded_live_quote_log(
                        self.config,
                        symbol=position_symbol,
                        trade_id=str(position_id or ""),
                        reason=str(detail.get("live_degraded_reason") or "unknown"),
                        action_taken="hold",
                        dedupe_key=f"{str(position_id or '-')}|{position_symbol}",
                    )
            if osnap.get("_last_single_leg_src_logged") != prem_src:
                osnap["_last_single_leg_src_logged"] = prem_src
                log.info(
                    "OPTIONS_STEALTH | stage=single_leg_value_update | trade_id=%s | symbol=%s | current_value=%.6f | source=%s",
                    position_id,
                    position_symbol,
                    prem,
                    prem_src,
                )
            degraded_single = (
                self.config.require_live_option_data
                and prem_src
                in (
                    "delta_estimate",
                    "underlying_proxy",
                    "last_valid_stale",
                    "synthetic_modeled",
                    "synthetic_proxy",
                )
            ) or (self.config.require_live_quotes and prem_src == "live_quote_unavailable")
            if is_0dte_single and px_telem and px_telem != "live":
                degraded_single = True
            if degraded_single:
                log_flag = (
                    "_live_data_required_skip_logged"
                    if prem_src
                    in (
                        "delta_estimate",
                        "underlying_proxy",
                        "last_valid_stale",
                        "synthetic_modeled",
                        "synthetic_proxy",
                    )
                    else "_live_quotes_strict_skip_logged"
                )
                if not osnap.get(log_flag):
                    osnap[log_flag] = True
                    reason = (
                        "degraded_single_leg_source"
                        if prem_src
                        in (
                            "delta_estimate",
                            "underlying_proxy",
                            "last_valid_stale",
                            "synthetic_modeled",
                            "synthetic_proxy",
                        )
                        else "live_quotes_strict_no_fresh_quote"
                    )
                    log.warning(
                        "OPTIONS_STEALTH | stage=live_data_required_skip | trade_id=%s | symbol=%s | "
                        "reason=%s | source=%s",
                        position_id,
                        position_symbol,
                        reason,
                        prem_src,
                    )
                osnap["degraded_data_active"] = True
                osnap["degraded_data_source"] = prem_src
                if not osnap.get("degraded_data_since"):
                    osnap["degraded_data_since"] = now.isoformat()
                if not osnap.get("degraded_start_timestamp"):
                    osnap["degraded_start_timestamp"] = now.isoformat()
        live_sources = {
            "exact",
            "nearest",
            "cached_quote",
            "spread_leg_quotes",
            "spread_mtm_mid_last",
        }
        fallback_sources = {
            "delta_estimate",
            "underlying_proxy",
            "live_quote_unavailable",
            "last_valid_stale",
            "synthetic_modeled",
            "synthetic_proxy",
            "spread_position_value_fallback",
            "spread_modeled_fallback",
            "spread_mtm_no_legs",
            "spread_mtm_missing_leg_role",
            "spread_mtm_incomplete_quotes",
            "synthetic_open_grace",
        }
        px_telem_g = str(detail.get("price_source_telemetry") or "").strip().lower()
        has_live_data = prem_src in live_sources or (not is_spread and px_telem_g == "live")
        has_fallback_data = prem_src in fallback_sources and float(prem or 0.0) > 0.0
        if has_live_data:
            osnap["last_live_data_ts"] = now.isoformat()
        if has_live_data or has_fallback_data:
            osnap["last_any_data_ts"] = now.isoformat()
        if (not is_spread) and px_telem_g == "live":
            osnap["last_valid_option_price"] = float(prem)
            osnap["last_valid_option_quote_ts"] = now.isoformat()
            osnap["last_live_option_quote_ts"] = now.isoformat()
        elif prem_src in {"exact", "nearest", "cached_quote", "spread_mtm_mid_last"}:
            osnap["last_valid_option_price"] = float(prem)
            osnap["last_valid_option_quote_ts"] = now.isoformat()
        osnap["option_price_source_telemetry"] = px_telem_g
        osnap["synthetic_pricing_active"] = bool(detail.get("synthetic_pricing_active"))
        if is_0dte_single:
            ref_live_raw = osnap.get("last_live_option_quote_ts")
            try:
                if isinstance(ref_live_raw, str) and str(ref_live_raw).strip():
                    live_dt_ref = self._normalize_dt(datetime.fromisoformat(str(ref_live_raw)))
                else:
                    live_dt_ref = self._normalize_dt(st.opened_at)
                secs_no_live_opt = max(0.0, (now - live_dt_ref).total_seconds())
            except Exception:
                secs_no_live_opt = max(0.0, (now - self._normalize_dt(st.opened_at)).total_seconds())
            osnap["seconds_since_live_option_quote"] = secs_no_live_opt
            psrc_key = f"psrc|{position_id}|{position_symbol}"
            if _option_telemetry_log_should_emit(self.config, _option_price_source_log_last, psrc_key):
                log.info(
                    "OPTION_PRICE_SOURCE | trade_id=%s | symbol=%s | source=%s | premium_source=%s",
                    str(position_id or "-"),
                    position_symbol,
                    px_telem_g or "-",
                    prem_src,
                )
            if px_telem_g != "live":
                deg_key = f"degm|{position_id}|{position_symbol}"
                if _option_telemetry_log_should_emit(self.config, _option_degraded_mode_log_last, deg_key):
                    log.warning(
                        "OPTION_DEGRADED_MODE | trade_id=%s | symbol=%s | duration_sec=%.1f",
                        str(position_id or "-"),
                        position_symbol,
                        float(secs_no_live_opt),
                    )
        log.info(
            "OPTIONS_DATA_SOURCE_UPDATE | trade_id=%s | symbol=%s | data_source=%s | quote_age_seconds=%s | spread_pct=%s | fallback_used=%s",
            position_id,
            position_symbol,
            prem_src,
            detail.get("quote_age_seconds"),
            detail.get("spread_pct"),
            str(bool(detail.get("fallback_used"))).lower(),
        )
        if bool(detail.get("fallback_used")):
            log.warning(
                "OPTIONS_DATA_FALLBACK_USED | trade_id=%s | symbol=%s | source=%s | quote_age_seconds=%s",
                position_id,
                position_symbol,
                prem_src,
                detail.get("quote_age_seconds"),
            )
        if not osnap.get("degraded_data_active"):
            osnap["degraded_data_since"] = None
            osnap["degraded_data_source"] = None
            osnap["degraded_start_timestamp"] = None
        quote_age_seconds = 0.0
        try:
            if is_spread:
                quote_age_seconds = max(
                    float(detail.get("long_leg_quote_age_seconds") or 0.0),
                    float(detail.get("short_leg_quote_age_seconds") or 0.0),
                )
            else:
                quote_age_seconds = float(detail.get("quote_age_seconds") or 0.0)
        except (TypeError, ValueError):
            quote_age_seconds = 0.0
        mark_quality = str(detail.get("mark_quality") or "").strip().lower()
        if not mark_quality:
            if is_spread:
                mark_quality = "live_two_leg_spread" if prem_src == "spread_mtm_mid_last" else "partial_leg"
            else:
                mark_quality = "live_single_leg" if px_telem_g == "live" else "synthetic"
        mark_is_exit_grade = bool(detail.get("mark_is_exit_grade"))
        if not mark_is_exit_grade:
            mark_is_exit_grade = mark_quality in {"live_two_leg_spread", "live_single_leg"}
        osnap["mark_quality"] = mark_quality
        osnap["mark_is_exit_grade"] = bool(mark_is_exit_grade)
        osnap["quote_age_seconds"] = float(quote_age_seconds)
        osnap["leg_completeness"] = str(detail.get("leg_completeness") or ("single_leg" if not is_spread else "missing"))
        osnap["long_leg_quote_age_seconds"] = float(detail.get("long_leg_quote_age_seconds") or 0.0)
        osnap["short_leg_quote_age_seconds"] = float(detail.get("short_leg_quote_age_seconds") or 0.0)
        osnap["using_synthetic_mark"] = bool(detail.get("using_synthetic_mark") or mark_quality in {"synthetic", "partial_leg", "missing"})
        osnap["using_stale_mark"] = bool(detail.get("using_stale_mark") or mark_quality in {"stale_last_valid", "cached_recent"})
        trusted_td, trusted_td_why = self._orb_trusted_degraded_spread_mark_eligible(
            is_orb_0dte=is_orb_0dte,
            is_spread=is_spread,
            synthetic_quote=bool(detail.get("synthetic_pricing_active")),
            prem_src=str(prem_src or ""),
            mark_quality=mark_quality,
            quote_age_seconds=float(quote_age_seconds or 0.0),
            detail=detail,
            u_move_pct_eval=float(u_move_pct_eval or 0.0),
            entry_debit=float(st.entry_debit or st.entry_value or st.entry_premium or 0.0),
            spread_mid=float(detail.get("spread_mid_value") or prem or 0.0),
            spread_liq=float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0),
            position_type=str(st.position_type or ""),
        )
        osnap["trusted_degraded_spread_mark"] = bool(trusted_td)
        osnap["trusted_degraded_spread_mark_reason"] = str(trusted_td_why or "")
        stale_cat = float(getattr(self.config, "quote_stale_max_age_seconds", 90.0) or 90.0)
        osnap["orb_spread_degraded_running"] = bool(
            trusted_td
            and is_orb_0dte
            and is_spread
            and mark_quality in {"partial_leg", "cached_recent", "nearest", "cached_quote"}
            and float(quote_age_seconds or 0.0) <= stale_cat
        )
        if bool(osnap.get("orb_spread_degraded_running")):
            _emit_option_stealth_state_if_due(
                cfg=self.config,
                trade_id=str(position_id or "-"),
                symbol=str(position_symbol or "-"),
                message=(
                    f"ORB_0DTE_STEALTH_STATE | path=orb_0dte_spread | trade_id={position_id} | symbol={position_symbol} | "
                    f"degraded_running=true | trusted_degraded=true | mark_quality={mark_quality} | "
                    f"quote_age_seconds={float(quote_age_seconds or 0.0):.2f} | timeout_clock_frozen_pre="
                    f"{str(bool(st.timeout_clock_frozen)).lower()} | watchdog_gap_seconds={float(osnap.get('watchdog_gap_seconds') or 0.0):.2f} | "
                    f"trusted_reason={trusted_td_why}"
                ),
            )
        freeze_reasons: List[str] = []
        watchdog_gap_seconds = float(osnap.get("watchdog_gap_seconds") or 0.0)
        wd_trusted_skip = bool(
            is_orb_0dte
            and is_spread
            and bool(trusted_td)
            and bool(getattr(self.config, "orb_watchdog_trusted_skip_clock_freeze", True))
        )
        if watchdog_gap_seconds > 0:
            st.watchdog_gap_seen = True
            if not wd_trusted_skip:
                freeze_reasons.append("watchdog_gap")
                log.warning(
                    "OPTION_MONITOR_WATCHDOG_GAP_APPLIED | trade_id=%s | symbol=%s | gap_seconds=%.1f | reason=watchdog_gap",
                    str(position_id or ""),
                    position_symbol,
                    float(watchdog_gap_seconds),
                )
            else:
                log.info(
                    "OPTION_MONITOR_CLOCK_DEGRADED_RUNNING | trade_id=%s | symbol=%s | reason=watchdog_gap_trusted_degraded | "
                    "gap_seconds=%.1f | skip_timeout_freeze=true",
                    str(position_id or ""),
                    position_symbol,
                    float(watchdog_gap_seconds),
                )
            if is_orb_0dte and is_spread:
                osnap["orb_watchdog_gap_accum_sec"] = float(osnap.get("orb_watchdog_gap_accum_sec") or 0.0) + float(
                    watchdog_gap_seconds
                )
        if not mark_is_exit_grade:
            if not (is_orb_0dte and is_spread and bool(trusted_td)):
                freeze_reasons.append(f"mark_quality:{mark_quality}")
        if bool(getattr(self.config, "freeze_timeouts_on_degraded_marks", True)) and freeze_reasons:
            if not st.timeout_clock_frozen:
                st.timeout_clock_frozen = True
                st.timeout_clock_last_frozen_at = now
                log.info(
                    "OPTION_TIMEOUT_CLOCK_FROZEN | trade_id=%s | symbol=%s | previous_clock_state=running | new_clock_state=frozen | reason=%s",
                    str(position_id or ""),
                    position_symbol,
                    "|".join(freeze_reasons),
                )
            elif st.timeout_clock_last_frozen_at is not None:
                st.timeout_frozen_seconds = float(st.timeout_frozen_seconds or 0.0) + max(
                    0.0,
                    (now - self._normalize_dt(st.timeout_clock_last_frozen_at)).total_seconds(),
                )
                st.timeout_clock_last_frozen_at = now
            st.good_mark_tick_count = 0
        else:
            if st.timeout_clock_frozen:
                st.timeout_clock_frozen = False
                st.timeout_clock_last_resumed_at = now
                resume_reason = "exit_grade_mark" if mark_is_exit_grade else "trusted_degraded_or_watchdog_cleared"
                log.info(
                    "OPTION_TIMEOUT_CLOCK_RESUMED | trade_id=%s | symbol=%s | previous_clock_state=frozen | new_clock_state=running | reason=%s",
                    str(position_id or ""),
                    position_symbol,
                    resume_reason,
                )
                if resume_reason != "exit_grade_mark":
                    log.info(
                        "OPTION_MONITOR_CLOCK_RECOVERED | trade_id=%s | symbol=%s | reason=%s | mark_quality=%s | trusted_degraded=%s",
                        str(position_id or ""),
                        position_symbol,
                        resume_reason,
                        mark_quality,
                        str(bool(trusted_td)).lower(),
                    )
                if st.timeout_clock_last_frozen_at is not None:
                    st.timeout_frozen_seconds = float(st.timeout_frozen_seconds or 0.0) + max(
                        0.0,
                        (now - self._normalize_dt(st.timeout_clock_last_frozen_at)).total_seconds(),
                    )
                st.timeout_clock_last_frozen_at = None
            st.good_mark_tick_count = int(st.good_mark_tick_count) + 1
        osnap["good_mark_tick_count"] = int(st.good_mark_tick_count)
        osnap["timeout_clock_frozen"] = bool(st.timeout_clock_frozen)
        osnap["timeout_frozen_seconds"] = float(st.timeout_frozen_seconds or 0.0)
        osnap["watchdog_gap_seen"] = bool(st.watchdog_gap_seen)
        if is_orb_0dte and is_spread:
            if (not bool(osnap.get("mark_is_exit_grade"))) and str(osnap.get("mark_quality") or "") == "partial_leg":
                if not osnap.get("orb_partial_leg_first_iso"):
                    osnap["orb_partial_leg_first_iso"] = now.isoformat()
            elif bool(osnap.get("mark_is_exit_grade")):
                osnap["orb_partial_leg_first_iso"] = None
        log.info(
            "OPTION_POSITION_MARK_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
            "entry_net_debit=%.6f | mid_value=%.6f | liquidation_value=%.6f | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | "
            "mark_quality=%s | mark_is_exit_grade=%s | quote_source=%s | quote_age_seconds=%.1f | leg_completeness=%s",
            str(position_id or ""),
            position_symbol,
            str(st.strategy_type or ""),
            str(st.position_type or ""),
            str(st.option_side or ""),
            float(st.entry_debit or st.entry_value or st.entry_premium or 0.0),
            float(detail.get("spread_mid_value") if is_spread else prem or 0.0),
            float(detail.get("spread_liquidation_value") if is_spread else detail.get("option_bid") or prem or 0.0),
            float(detail.get("current_pnl_pct_mid") if is_spread else ((float(prem or 0.0) - float(st.entry_premium or 0.0)) / float(st.entry_premium or 1.0))),
            float(detail.get("current_pnl_pct_liquidation") if is_spread else ((float(detail.get("option_bid") or prem or 0.0) - float(st.entry_premium or 0.0)) / float(st.entry_premium or 1.0))),
            mark_quality,
            str(bool(mark_is_exit_grade)).lower(),
            str(prem_src),
            float(quote_age_seconds),
            str(osnap.get("leg_completeness") or "missing"),
        )
        st.last_effective_premium = prem
        st.last_premium_source = prem_src
        st.last_update_at = now
        st.last_underlying_update_at = now
        st.last_premium_update_at = now
        non_exact_quote_seconds = 0.0
        if is_trendline_0dte and (not is_spread):
            if prem_src != "exact":
                if not osnap.get("non_exact_quote_since"):
                    osnap["non_exact_quote_since"] = now.isoformat()
                try:
                    ne_dt = self._normalize_dt(datetime.fromisoformat(str(osnap.get("non_exact_quote_since"))))
                    non_exact_quote_seconds = max(0.0, (now - ne_dt).total_seconds())
                except Exception:
                    non_exact_quote_seconds = 0.0
            else:
                osnap["non_exact_quote_since"] = None
                non_exact_quote_seconds = 0.0

        prev_logged_src = osnap.get("last_logged_premium_source")
        if prem_src != prev_logged_src:
            osnap["last_logged_premium_source"] = prem_src
            log.info(
                "TRENDLINE_PIPELINE | stage=premium_update | symbol=%s | premium_source=%s | premium=%.6f",
                position_symbol,
                prem_src,
                prem,
            )
        if (
            prem_src
            in (
                "delta_estimate",
                "underlying_proxy",
                "last_valid_stale",
                "synthetic_modeled",
                "synthetic_proxy",
            )
            and not osnap.get("_premium_fallback_logged")
        ):
            fb_reason = (
                "no_option_quote_supplied"
                if option_quote is None
                else "option_quote_missing_usable_fields"
            )
            log.info(
                "TRENDLINE_PIPELINE | stage=premium_fallback | symbol=%s | reason=%s | fallback_source=%s",
                position_symbol,
                fb_reason,
                prem_src,
            )
            osnap["_premium_fallback_logged"] = True

        allow_hwm = _premium_updates_favorable_hwm(prem_src, detail, is_spread=is_spread)
        if is_trendline_0dte and (not is_spread):
            allow_hwm = _premium_hwm_allow_tline_single(
                self.config,
                prem_src,
                detail,
                prem=float(prem or 0.0),
                underlying_move_confirms=bool(underlying_confirms_hwm),
                synthetic_active=bool(detail.get("synthetic_pricing_active")),
            )
        prev_ps_life = str(osnap.get("_lifecycle_prev_premium_source") or "")
        prev_mq_life = str(osnap.get("_lifecycle_prev_mark_quality") or "")
        mq_tick = str(osnap.get("mark_quality") or "")
        pt_tick = str(detail.get("price_source_telemetry") or osnap.get("option_price_source_telemetry") or "")
        if prem_src != prev_ps_life or mq_tick != prev_mq_life:
            log.info(
                "OPTION_PREMIUM_SOURCE_TRANSITION | trade_id=%s | symbol=%s | strategy_type=%s | "
                "premium_source=%s->%s | mark_quality=%s->%s | price_source_telemetry=%s | allow_hwm=%s | "
                "mark_is_exit_grade=%s | spread_premium_source_quality=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                prev_ps_life or "init",
                prem_src,
                prev_mq_life or "init",
                mq_tick,
                pt_tick,
                str(bool(allow_hwm)).lower(),
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                str(detail.get("spread_premium_source_quality") or ""),
            )
            osnap["_lifecycle_prev_premium_source"] = prem_src
            osnap["_lifecycle_prev_mark_quality"] = mq_tick
        if allow_hwm:
            if prem > st.premium_hwm:
                st.premium_hwm = prem
                st.last_hwm_at = now
                if prem > st.entry_premium:
                    st.favorable_advance_seen = True
                if (
                    is_trendline_0dte
                    and (not is_spread)
                    and (not _premium_updates_favorable_hwm(prem_src, detail, is_spread=False))
                ):
                    hwm_acc_key = f"hwm_acc|{position_id}|{position_symbol}"
                    if _option_telemetry_log_should_emit(self.config, _option_hwm_skip_log_last, hwm_acc_key):
                        log.info(
                            "OPTION_HWM_UPDATE_ACCEPTED | trade_id=%s | symbol=%s | premium_source=%s | mark_quality=%s | "
                            "reason=tline_trusted_degraded_underlying | underlying_confirms_hwm=%s | premium_hwm=%.6f",
                            str(position_id or "-"),
                            position_symbol,
                            prem_src,
                            str(detail.get("mark_quality") or osnap.get("mark_quality") or ""),
                            str(bool(underlying_confirms_hwm)).lower(),
                            float(st.premium_hwm or 0.0),
                        )
            if is_spread and prem > st.spread_hwm:
                st.spread_hwm = prem
        elif prem > float(st.premium_hwm or 0.0) + 1e-12 or (is_spread and prem > float(st.spread_hwm or 0.0) + 1e-12):
            hwm_skip_key = f"hwm_skip|{position_id}|{position_symbol}"
            if _option_telemetry_log_should_emit(self.config, _option_hwm_skip_log_last, hwm_skip_key):
                skip_reason = "non_live_price_source"
                td_elig = False
                if is_trendline_0dte and (not is_spread):
                    td_elig = tline_trusted_degraded_mark(
                        self.config,
                        prem_src=prem_src,
                        mark_quality=str(detail.get("mark_quality") or osnap.get("mark_quality") or ""),
                        quote_age_seconds=detail.get("quote_age_seconds"),
                        synthetic_active=bool(detail.get("synthetic_pricing_active")),
                    )
                    if not bool(underlying_confirms_hwm):
                        skip_reason = "tline_underlying_not_confirming"
                    elif not td_elig:
                        skip_reason = "tline_not_trusted_degraded_mark"
                    else:
                        skip_reason = "tline_bid_ask_sanity_or_positive_mark"
                log.info(
                    "OPTION_HWM_UPDATE_SKIPPED | trade_id=%s | symbol=%s | premium_source=%s | mark_quality=%s | reason=%s | "
                    "underlying_confirms_hwm=%s | trusted_degraded_eligible=%s",
                    str(position_id or "-"),
                    position_symbol,
                    prem_src,
                    str(detail.get("mark_quality") or osnap.get("mark_quality") or ""),
                    skip_reason,
                    str(bool(underlying_confirms_hwm)).lower(),
                    str(bool(td_elig)).lower(),
                )
        if prem < st.premium_lwm:
            st.premium_lwm = prem
        if is_spread and prem < st.spread_lwm:
            st.spread_lwm = prem

        osnap["premium_hwm"] = st.premium_hwm
        osnap["premium_lwm"] = st.premium_lwm

        if is_spread and st.position_type == OptionPositionType.CREDIT_SPREAD.value:
            base = float(st.entry_credit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (base - prem) / base if base > 0 else 0.0
        elif is_spread:
            base = float(st.entry_debit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (prem - base) / base if base > 0 else 0.0
        else:
            pnl_pct = (prem - st.entry_premium) / st.entry_premium if st.entry_premium > 0 else 0.0
        current_pnl_pct = float(pnl_pct)
        prev_max_pnl_pct = float(getattr(st, "max_pnl_pct", current_pnl_pct))
        if allow_hwm:
            st.max_pnl_pct = max(prev_max_pnl_pct, current_pnl_pct)
            if st.max_pnl_pct > prev_max_pnl_pct + 1e-9:
                log.info(
                    "OPTION_MAX_PNL_UPDATE | symbol=%s | trade_id=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_symbol,
                    position_id,
                    current_pnl_pct,
                    st.max_pnl_pct,
                )
                log.info(
                    "OPTION_MFE_UPDATE | trade_id=%s | symbol=%s | premium_source=%s | allow_hwm=%s | "
                    "current_pnl_pct=%.4f | max_pnl_pct=%.4f | mark_quality=%s | mark_is_exit_grade=%s",
                    str(position_id or "-"),
                    position_symbol,
                    prem_src,
                    str(bool(allow_hwm)).lower(),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(osnap.get("mark_quality") or ""),
                    str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                )
            if (not st.had_opportunity) and st.max_pnl_pct >= 0.05:
                st.had_opportunity = True
                osnap["had_opportunity"] = True
                log.info(
                    "OPTION_OPPORTUNITY_DETECTED | symbol=%s | trade_id=%s | max_pnl_pct=%.4f",
                    position_symbol,
                    position_id,
                    st.max_pnl_pct,
                )
        osnap["current_pnl_pct"] = current_pnl_pct
        osnap["max_pnl_pct"] = st.max_pnl_pct
        osnap["had_opportunity"] = bool(getattr(st, "had_opportunity", False))
        drawdown_from_max_pct = max(0.0, float(st.max_pnl_pct) - float(current_pnl_pct))
        hwm_age_seconds = max(
            0.0,
            (now - self._normalize_dt(st.last_hwm_at)).total_seconds() if st.last_hwm_at else 0.0,
        )
        log.info(
            "OPTION_STEALTH_PREMIUM_PNL_AUDIT | symbol=%s | trade_id=%s | position_type=%s | premium_source=%s | entry_premium=%.6f | "
            "current_effective_premium=%.6f | current_pnl_pct=%.4f | max_pnl_pct=%.4f | premium_hwm=%.6f",
            position_symbol,
            position_id,
            str(st.position_type or ""),
            prem_src,
            float(st.entry_premium or 0.0),
            float(prem or 0.0),
            float(current_pnl_pct),
            float(st.max_pnl_pct),
            float(st.premium_hwm or 0.0),
        )
        log.info(
            "OPTIONS_STEALTH_PREMIUM_STATE | trade_id=%s | symbol=%s | source_path=%s | position_type=%s | entry_archetype=%s | "
            "current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | be_active=%s | profit_lock_active=%s | "
            "trailing_active=%s | protection_floor_pct=%.4f | trailing_distance_pct=%.4f | premium_source=%s | quote_age_seconds=%s | "
            "degraded_mode=%s | elapsed_seconds=%.1f | high_watermark_age_seconds=%.1f",
            position_id,
            position_symbol,
            profile.source_path,
            str(st.position_type or ""),
            profile.entry_archetype,
            float(current_pnl_pct),
            float(st.max_pnl_pct),
            float(drawdown_from_max_pct),
            str(bool(st.breakeven_floor is not None)).lower(),
            str(bool(osnap.get("profit_lock_active", False))).lower(),
            str(bool(st.trailing_activated_at is not None)).lower(),
            float(
                ((float(st.breakeven_floor) / float(st.entry_premium)) - 1.0)
                if st.breakeven_floor is not None and float(st.entry_premium or 0.0) > 0
                else 0.0
            ),
            float(st.last_trail_pct or profile.trail_distance_pct or 0.0),
            prem_src,
            detail.get("quote_age_seconds"),
            str(bool(osnap.get("degraded_data_active"))).lower(),
            float(max(0.0, (now - self._normalize_dt(st.opened_at)).total_seconds())),
            float(hwm_age_seconds),
        )
        last_eval_raw = osnap.get("_last_eval_ts")
        seconds_since_last_tick = 0.0
        if isinstance(last_eval_raw, str):
            try:
                seconds_since_last_tick = max(
                    0.0,
                    (now - self._normalize_dt(datetime.fromisoformat(str(last_eval_raw)))).total_seconds(),
                )
            except Exception:
                seconds_since_last_tick = 0.0
        log.info(
            "OPTIONS_STEALTH_MONITOR_CADENCE | trade_id=%s | symbol=%s | seconds_since_last_tick=%.3f | premium_source=%s | "
            "quote_age_seconds=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
            position_id,
            position_symbol,
            float(seconds_since_last_tick),
            prem_src,
            detail.get("quote_age_seconds"),
            float(current_pnl_pct),
            float(st.max_pnl_pct),
        )
        lc_key = f"{str(position_id or '-')}:{position_symbol}"
        if _lifecycle_tick_should_emit(_option_premium_lifecycle_tick_last, lc_key):
            spq = str(detail.get("spread_premium_source_quality") or osnap.get("spread_premium_source_quality") or "")
            log.info(
                "OPTION_PREMIUM_MARK_UPDATE | trade_id=%s | symbol=%s | strategy_type=%s | ts=%s | "
                "effective_premium=%.6f | premium_source=%s | mark_quality=%s | mark_is_exit_grade=%s | "
                "good_mark_tick_count=%d | timeout_clock_frozen=%s | allow_hwm=%s | "
                "current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | premium_hwm=%.6f | "
                "entry_ref=%.6f | quote_age_seconds=%.1f | long_leg_age=%.1f | short_leg_age=%.1f | "
                "degraded_data_active=%s | spread_premium_source_quality=%s | price_source_telemetry=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                now.isoformat(),
                float(prem or 0.0),
                prem_src,
                str(osnap.get("mark_quality") or ""),
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                int(st.good_mark_tick_count),
                str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                str(bool(allow_hwm)).lower(),
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                float(drawdown_from_max_pct),
                float(st.premium_hwm or 0.0),
                float(st.entry_debit or st.entry_value or st.entry_premium or 0.0),
                float(quote_age_seconds),
                float(osnap.get("long_leg_quote_age_seconds") or 0.0),
                float(osnap.get("short_leg_quote_age_seconds") or 0.0),
                str(bool(osnap.get("degraded_data_active"))).lower(),
                spq,
                str(detail.get("price_source_telemetry") or osnap.get("option_price_source_telemetry") or ""),
            )
        held_min = (now - self._normalize_dt(st.opened_at)).total_seconds() / 60.0
        elapsed_seconds = max(0.0, held_min * 60.0)
        if is_orb_0dte or is_trendline_0dte:
            prior_behavior = str(getattr(st, "trade_behavior", "unknown") or "unknown").lower()
            next_behavior = prior_behavior
            if float(st.max_pnl_pct) >= 0.08 and elapsed_seconds <= 180.0:
                next_behavior = "impulse"
            elif elapsed_seconds >= 240.0:
                next_behavior = "trend"
            if next_behavior not in {"unknown", "impulse", "trend"}:
                next_behavior = "unknown"
            if next_behavior != prior_behavior:
                st.trade_behavior = next_behavior
                log.info(
                    "OPTION_TRADE_BEHAVIOR_CLASSIFIED | symbol=%s | trade_behavior=%s | max_pnl_pct=%.4f | elapsed_seconds=%.1f",
                    position_symbol,
                    st.trade_behavior,
                    float(st.max_pnl_pct),
                    float(elapsed_seconds),
                )
        osnap["trade_behavior"] = str(getattr(st, "trade_behavior", "unknown") or "unknown")

        impulse_profit_lock_floor_pct: Optional[float] = None
        if (is_orb_0dte or is_trendline_0dte) and str(getattr(st, "trade_behavior", "unknown")).lower() == "impulse":
            if float(st.max_pnl_pct) >= 0.15:
                impulse_profit_lock_floor_pct = 0.06
            elif float(st.max_pnl_pct) >= 0.10:
                impulse_profit_lock_floor_pct = 0.03

        use_structure_invalidation = bool(
            st.structure_invalidation_enabled_override
            if st.structure_invalidation_enabled_override is not None
            else (
                str(st.strategy_type or "").lower() == "trendline_0dte"
                or str(st.strategy_name or "").lower() == "easytrendline_0dte"
            )
        )
        use_structure_invalidation = bool(use_structure_invalidation and profile.allow_underlying_structure_exit)
        structure_exit_allowed = bool(
            profile.allow_underlying_structure_exit
            and (bool(st.had_opportunity) or st.breakeven_floor is not None or st.trailing_activated_at is not None)
        )
        held_seconds = held_min * 60.0
        min_hold_active = held_seconds < float(profile.min_hold_seconds or 0.0)
        if min_hold_active and not osnap.get("_trendline_min_hold_logged"):
            osnap["_trendline_min_hold_logged"] = True
            log.info(
                "TRENDLINE_MIN_HOLD_ACTIVE | trade_id=%s | held_seconds=%.1f | min_hold_seconds=%.1f | blocking_exits=true",
                position_id,
                held_seconds,
                float(profile.min_hold_seconds),
            )
        def _log_min_hold_bypassed_for_protection(exit_type: str) -> None:
            if not min_hold_active:
                return
            ctx_parts: List[str] = []
            if st.breakeven_floor is not None:
                ctx_parts.append("be_active")
            if st.trailing_activated_at is not None:
                ctx_parts.append("trailing_active")
            if bool(osnap.get("degraded_data_active")):
                ctx_parts.append("degraded_mode")
            log.info(
                "TRENDLINE_MIN_HOLD_BYPASSED_FOR_PROTECTION | trade_id=%s | exit_type=%s | held_seconds=%.1f | min_hold_seconds=%.1f | context=%s",
                position_id,
                str(exit_type),
                float(held_seconds),
                float(profile.min_hold_seconds),
                "+".join(ctx_parts) if ctx_parts else "none",
            )

        if use_structure_invalidation:
            structure_ok = _structure_still_valid(
                self.config,
                st.trendline,
                current_underlying,
                now,
                st.line_geometry,
                st.option_side,
            )
        else:
            structure_ok = True
            if not osnap.get("_structure_invalidation_skip_logged"):
                osnap["_structure_invalidation_skip_logged"] = True
                log.info(
                    "OPTIONS_STEALTH | stage=structure_invalidation_skipped | trade_id=%s | position_type=%s | strategy_type=%s",
                    position_id,
                    st.position_type,
                    st.strategy_type,
                )
        tline_min_hold_bypass = True
        if is_trendline_0dte and (not is_spread):
            tline_min_hold_bypass = False
            if held_seconds >= float(profile.min_hold_seconds or 0.0):
                tline_min_hold_bypass = True
            elif float(st.max_pnl_pct) >= float(self.config.tline_min_hold_bypass_mfe_pct):
                tline_min_hold_bypass = True
            elif bool(getattr(st, "had_underlying_opportunity", False)) and bool(underlying_confirms_hwm):
                tline_min_hold_bypass = True
            elif bool(self.config.tline_underlying_reclaim_protect) and bool(use_structure_invalidation) and (not bool(structure_ok)):
                tline_min_hold_bypass = True
        min_hold_blocks_tline = bool(
            min_hold_active and is_trendline_0dte and (not is_spread) and (not tline_min_hold_bypass)
        )
        self._merge_live_snapshot(osnap, st, prem, prem_src, detail, current_underlying, pnl_pct, structure_ok)
        if is_spread:
            osnap["spread_current_value"] = prem

        if not osnap.get("_telemetry_snapshot_logged"):
            wanted = (
                "position_type",
                "leg_count",
                "legs",
                "entry_value",
                "entry_debit",
                "entry_credit",
                "current_effective_premium",
                "last_premium_source",
                "premium_hwm",
                "premium_lwm",
                "spread_current_value",
                "spread_entry_value",
                "spread_high_water_mark",
                "spread_low_water_mark",
                "spread_premium_source_quality",
                "spread_monitoring_mode",
                "structure_still_valid",
                "strategy_type",
                "source_path",
            )
            present = [k for k in wanted if osnap.get(k) is not None and osnap.get(k) != ""]
            log.info(
                "OPTIONS_STEALTH | stage=telemetry_snapshot | trade_id=%s | fields_present=%s",
                position_id,
                ",".join(present),
            )
            osnap["_telemetry_snapshot_logged"] = True

        telemetry: Dict[str, Any] = {
            "current_premium_est": prem,
            "premium_source": prem_src,
            "pnl_pct": pnl_pct,
            "mode": st.mode.value,
            "structure_still_valid": structure_ok,
        }
        synthetic_active = bool(detail.get("synthetic_pricing_active"))
        non_live_premium = str(px_telem_g or "").lower() != "live"
        telemetry["synthetic_pricing_active"] = synthetic_active
        telemetry["non_live_premium"] = non_live_premium
        telemetry["price_source_telemetry"] = px_telem_g
        if is_0dte_single:
            max_deg = max(1.0, float(self.config.option_max_degraded_seconds))
            snl = float(osnap.get("seconds_since_live_option_quote") or 0.0)
            no_data_force_threshold = float(max_deg)
            no_data_grace_threshold = 0.0
            if is_trendline_0dte:
                no_data_grace_threshold = max(0.0, float(self.config.tline_option_no_data_grace_seconds))
                no_data_force_threshold = max(
                    no_data_grace_threshold,
                    float(self.config.tline_option_force_exit_no_data_seconds),
                )
            if px_telem_g != "live":
                require_exit_grade = bool(
                    is_trendline_0dte and bool(self.config.tline_option_require_exit_grade_before_force_exit)
                )
                mark_exit_grade = bool(osnap.get("mark_is_exit_grade"))
                favorable_underlying = False
                if is_trendline_0dte:
                    entry_u = osnap.get("entry_underlying")
                    try:
                        entry_underlying = float(entry_u)
                    except (TypeError, ValueError):
                        entry_underlying = 0.0
                    if entry_underlying > 0:
                        move_pct = (float(current_underlying) - entry_underlying) / entry_underlying
                        thr = float(self.config.tline_no_data_favorable_underlying_move_pct)
                        side = str(st.option_side or "").lower()
                        favorable_underlying = bool(
                            (side == "put" and move_pct <= -abs(thr))
                            or (side == "call" and move_pct >= abs(thr))
                        )
                if is_trendline_0dte and held_seconds < float(self.config.tline_min_diagnostic_hold_seconds):
                    log.info(
                        "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                        "candidate_exit_reason=option_force_exit_no_data | final_exit_reason=hold | exit_allowed=false | blocked_reason=min_diagnostic_hold | "
                        "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | elapsed_seconds=%.1f",
                        str(position_id or ""),
                        position_symbol,
                        str(st.strategy_type or ""),
                        str(st.position_type or ""),
                        str(st.option_side or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        str(bool(mark_exit_grade)).lower(),
                        osnap.get("quote_age_seconds"),
                        int(st.good_mark_tick_count),
                        str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                        float(held_seconds),
                    )
                    log.warning(
                        "TRENDLINE_OPTION_NO_DATA_GRACE_HOLD | trade_id=%s | symbol=%s | reason=min_diagnostic_hold | held_seconds=%.1f | min_diagnostic_hold_seconds=%.1f | seconds_since_live=%.1f",
                        str(position_id or "-"),
                        position_symbol,
                        held_seconds,
                        float(self.config.tline_min_diagnostic_hold_seconds),
                        snl,
                    )
                    log.warning(
                        "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD | trade_id=%s | symbol=%s | reason=min_diagnostic_hold",
                        str(position_id or "-"),
                        position_symbol,
                    )
                    return None
                if is_trendline_0dte and snl < no_data_grace_threshold:
                    log.info(
                        "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                        "candidate_exit_reason=option_force_exit_no_data | final_exit_reason=hold | exit_allowed=false | blocked_reason=no_data_grace | "
                        "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | seconds_since_live=%.1f",
                        str(position_id or ""),
                        position_symbol,
                        str(st.strategy_type or ""),
                        str(st.position_type or ""),
                        str(st.option_side or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        str(bool(mark_exit_grade)).lower(),
                        osnap.get("quote_age_seconds"),
                        int(st.good_mark_tick_count),
                        str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                        float(snl),
                    )
                    log.warning(
                        "TRENDLINE_OPTION_NO_DATA_GRACE_HOLD | trade_id=%s | symbol=%s | reason=no_data_grace | seconds_since_live=%.1f | grace_seconds=%.1f | premium_source=%s",
                        str(position_id or "-"),
                        position_symbol,
                        snl,
                        no_data_grace_threshold,
                        prem_src,
                    )
                    log.warning(
                        "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD | trade_id=%s | symbol=%s | reason=no_data_grace",
                        str(position_id or "-"),
                        position_symbol,
                    )
                    return None
                if is_trendline_0dte and snl >= no_data_grace_threshold and snl < no_data_force_threshold:
                    log.info(
                        "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                        "candidate_exit_reason=option_force_exit_no_data | final_exit_reason=hold | exit_allowed=false | blocked_reason=force_exit_not_yet_eligible | "
                        "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | seconds_since_live=%.1f",
                        str(position_id or ""),
                        position_symbol,
                        str(st.strategy_type or ""),
                        str(st.position_type or ""),
                        str(st.option_side or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        str(bool(mark_exit_grade)).lower(),
                        osnap.get("quote_age_seconds"),
                        int(st.good_mark_tick_count),
                        str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                        float(snl),
                    )
                    log.warning(
                        "TRENDLINE_OPTION_NO_DATA_FORCE_EXIT_ARMED | trade_id=%s | symbol=%s | seconds_since_live=%.1f | grace_seconds=%.1f | force_seconds=%.1f | mark_is_exit_grade=%s",
                        str(position_id or "-"),
                        position_symbol,
                        snl,
                        no_data_grace_threshold,
                        no_data_force_threshold,
                        str(bool(mark_exit_grade)).lower(),
                    )
                    log.warning(
                        "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD | trade_id=%s | symbol=%s | reason=force_exit_not_yet_eligible",
                        str(position_id or "-"),
                        position_symbol,
                    )
                    return None
                if is_trendline_0dte and favorable_underlying:
                    log.info(
                        "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                        "candidate_exit_reason=option_force_exit_no_data | final_exit_reason=hold | exit_allowed=false | blocked_reason=favorable_underlying_during_no_data | "
                        "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s",
                        str(position_id or ""),
                        position_symbol,
                        str(st.strategy_type or ""),
                        str(st.position_type or ""),
                        str(st.option_side or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        str(bool(mark_exit_grade)).lower(),
                        osnap.get("quote_age_seconds"),
                        int(st.good_mark_tick_count),
                        str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                    )
                    log.warning(
                        "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD | trade_id=%s | symbol=%s | reason=favorable_underlying_during_no_data",
                        str(position_id or "-"),
                        position_symbol,
                    )
                    return None
                if is_trendline_0dte and require_exit_grade and not mark_exit_grade:
                    log.info(
                        "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                        "candidate_exit_reason=option_force_exit_no_data | final_exit_reason=hold | exit_allowed=false | blocked_reason=non_exit_grade_mark | "
                        "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s",
                        str(position_id or ""),
                        position_symbol,
                        str(st.strategy_type or ""),
                        str(st.position_type or ""),
                        str(st.option_side or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        str(bool(mark_exit_grade)).lower(),
                        osnap.get("quote_age_seconds"),
                        int(st.good_mark_tick_count),
                        str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                    )
                    log.warning(
                        "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD | trade_id=%s | symbol=%s | reason=non_exit_grade_mark",
                        str(position_id or "-"),
                        position_symbol,
                    )
                    return None
                if snl >= no_data_force_threshold:
                    _log_min_hold_bypassed_for_protection("option_force_exit_no_data")
                    log.info(
                        "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                        "candidate_exit_reason=option_force_exit_no_data | final_exit_reason=option_force_exit_no_data | exit_allowed=true | blocked_reason=none | "
                        "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | seconds_since_live=%.1f",
                        str(position_id or ""),
                        position_symbol,
                        str(st.strategy_type or ""),
                        str(st.position_type or ""),
                        str(st.option_side or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        str(bool(mark_exit_grade)).lower(),
                        osnap.get("quote_age_seconds"),
                        int(st.good_mark_tick_count),
                        str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                        float(snl),
                    )
                    log.warning(
                        "TRENDLINE_OPTION_NO_DATA_FORCE_EXIT_CONFIRMED | trade_id=%s | symbol=%s | seconds_since_live=%.1f | force_seconds=%.1f | premium_source=%s",
                        str(position_id or "-"),
                        position_symbol,
                        snl,
                        no_data_force_threshold,
                        prem_src,
                    )
                    log.warning(
                        "OPTION_FORCE_EXIT_NO_DATA | trade_id=%s | symbol=%s | seconds_since_live=%.1f | limit_sec=%.1f | premium_source=%s",
                        str(position_id or "-"),
                        position_symbol,
                        snl,
                        no_data_force_threshold,
                        prem_src,
                    )
                    diag = self._build_exit_diagnostics(
                        position_id=position_id,
                        position_symbol=position_symbol,
                        block=block,
                        st=st,
                        osnap=osnap,
                        exit_premium=prem,
                        exit_reason="option_force_exit_no_data",
                        now=now,
                        prem_detail=detail,
                        premium_source_at_exit=prem_src,
                    )
                    diag["seconds_since_live_option_quote"] = snl
                    diag["option_max_degraded_seconds"] = no_data_force_threshold
                    diag["price_source_telemetry"] = px_telem_g
                    diag["trendline_no_data_grace_seconds"] = no_data_grace_threshold
                    self._record_exit("option_force_exit_no_data", pnl_pct, prem_src, diag)
                    telemetry["exit_diagnostics"] = diag
                    return prem, "option_force_exit_no_data", telemetry
        if is_orb_0dte:
            log.info(
                "OPTION_LIFECYCLE_TICK | trade_id=%s | symbol=%s | strategy_type=orb_0dte | trade_behavior=%s | "
                "current_premium=%.6f | premium_source=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "had_opportunity=%s | mode=%s",
                position_id,
                position_symbol,
                str(getattr(st, "trade_behavior", "unknown") or "unknown"),
                float(prem or 0.0),
                prem_src,
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                str(bool(st.had_opportunity)).lower(),
                st.mode.value,
            )

        # Priority 1: impulse behavior profit-lock before underlying/reversal/trailing/BE/fast-fail.
        if (
            (not synthetic_active)
            and impulse_profit_lock_floor_pct is not None
            and float(current_pnl_pct) <= float(impulse_profit_lock_floor_pct)
        ):
            log.info(
                "OPTIONS_STEALTH | stage=impulse_profit_lock_exit | symbol=%s | trade_id=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | lock_floor_pct=%.4f | elapsed_seconds=%.1f",
                position_symbol,
                position_id,
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                float(impulse_profit_lock_floor_pct),
                float(elapsed_seconds),
            )
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason="impulse_profit_lock",
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["trade_behavior"] = str(st.trade_behavior)
            diag["lock_floor_pct"] = float(impulse_profit_lock_floor_pct)
            self._record_exit("impulse_profit_lock", pnl_pct, prem_src, diag)
            telemetry["exit_diagnostics"] = diag
            return prem, "impulse_profit_lock", telemetry

        prev_eval_ts = osnap.get("_last_eval_ts")
        if isinstance(prev_eval_ts, str):
            try:
                prev_dt = self._normalize_dt(datetime.fromisoformat(prev_eval_ts))
                seconds_stale = max(0.0, (now - prev_dt).total_seconds())
                if seconds_stale > float(self.config.max_stale_seconds):
                    st.stale_data_detected = True
                    osnap["stale_data_detected"] = True
                    log.info(
                        "TRENDLINE_PIPELINE | stage=option_monitor_stale | symbol=%s | seconds_stale=%.3f",
                        position_symbol,
                        seconds_stale,
                    )
                    if self.config.force_reeval_on_premium_jump:
                        log.info(
                            "TRENDLINE_PIPELINE | stage=option_forced_reeval | symbol=%s | reason=stale_monitor_data",
                            position_symbol,
                        )
            except Exception:
                pass
        osnap["_last_eval_ts"] = now.isoformat()

        if prev_eff and prev_eff > 0:
            jump_pct = abs(prem - prev_eff) / prev_eff
            if jump_pct >= float(self.config.premium_jump_recheck_pct):
                st.premium_jump_detected = True
                osnap["premium_jump_detected"] = True
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_premium_jump | symbol=%s | jump_pct=%.4f",
                    position_symbol,
                    jump_pct,
                )
                if self.config.force_reeval_on_premium_jump:
                    log.info(
                        "TRENDLINE_PIPELINE | stage=option_forced_reeval | symbol=%s | reason=premium_jump",
                        position_symbol,
                    )

        def _underlying_signal_confirmed_exit(signal_name: str) -> bool:
            confirmed = bool(st.had_opportunity) and (float(current_pnl_pct) < float(st.max_pnl_pct))
            if confirmed:
                log.info(
                    "OPTION_UNDERLYING_SIGNAL_CONFIRMED_EXIT | symbol=%s | trade_id=%s | signal=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | had_opportunity=%s",
                    position_symbol,
                    position_id,
                    signal_name,
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(bool(st.had_opportunity)).lower(),
                )
            else:
                log.info(
                    "OPTION_STEALTH_EXIT_PRIORITY_AUDIT | symbol=%s | trade_id=%s | underlying_signal=%s | candidate=true | confirmed=false | "
                    "had_opportunity=%s | in_drawdown=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_symbol,
                    position_id,
                    signal_name,
                    str(bool(st.had_opportunity)).lower(),
                    str(bool(float(current_pnl_pct) < float(st.max_pnl_pct))).lower(),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                )
            return confirmed

        if use_structure_invalidation:
            inv, inv_reason = _structure_invalid(
                self.config,
                st.trendline,
                current_underlying,
                now,
                st.line_geometry,
                st.option_side,
            )
        else:
            inv, inv_reason = False, ""
        if inv and structure_exit_allowed:
            st.structure_invalidation_triggered = True
            st.structure_invalidation_reason = inv_reason
            if _underlying_signal_confirmed_exit(f"structure_invalidation:{inv_reason}"):
                _log_min_hold_bypassed_for_protection("structure_invalidation")
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_structure_invalidation | symbol=%s | trade_id=%s | reason=%s | underlying=%.4f",
                    position_symbol,
                    position_id,
                    inv_reason,
                    current_underlying,
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason=f"structure_invalidation:{inv_reason}",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                self._record_exit("structure_invalidation", pnl_pct, prem_src, diag)
                telemetry["structure_invalidation"] = inv_reason
                telemetry["exit_diagnostics"] = diag
                return prem, f"structure_invalidation:{inv_reason}", telemetry

        if is_trendline_0dte and (not is_spread):
            trend_meta = st.trendline if isinstance(st.trendline, dict) else {}
            break_meta = trend_meta.get("break_event") if isinstance(trend_meta.get("break_event"), dict) else {}
            break_meta_md = break_meta.get("metadata") if isinstance(break_meta.get("metadata"), dict) else {}
            is_reversal_entry = bool(
                break_meta_md.get("reversal_entry")
                or trend_meta.get("reversal_entry")
                or str((osnap.get("entry_type") or "")).upper() == "TRENDLINE_REVERSAL_ENTRY"
            )
            reclaim_level = None
            for k in ("reclaim_level", "reversal_reclaim_level", "reclaim_price"):
                v = break_meta_md.get(k) if break_meta_md else trend_meta.get(k)
                if isinstance(v, (int, float)):
                    reclaim_level = float(v)
                    break
            if reclaim_level is None and isinstance(break_meta.get("trendline_price"), (int, float)):
                reclaim_level = float(break_meta.get("trendline_price"))
            if is_reversal_entry and isinstance(reclaim_level, (int, float)) and float(reclaim_level) > 0:
                side = str(st.option_side or "").lower()
                recross = (
                    (side == "put" and float(current_underlying) > float(reclaim_level))
                    or (side == "call" and float(current_underlying) < float(reclaim_level))
                )
                if recross:
                    if (not synthetic_active) and _underlying_signal_confirmed_exit("reversal_reclaim_recross"):
                        _log_min_hold_bypassed_for_protection("reversal_reclaim_recross")
                        log.warning(
                            "TRENDLINE_REVERSAL_GUARD_EXIT | trade_id=%s | symbol=%s | side=%s | current_underlying=%.4f | reclaim_level=%.4f",
                            position_id,
                            position_symbol,
                            side,
                            float(current_underlying),
                            float(reclaim_level),
                        )
                        diag = self._build_exit_diagnostics(
                            position_id=position_id,
                            position_symbol=position_symbol,
                            block=block,
                            st=st,
                            osnap=osnap,
                            exit_premium=prem,
                            exit_reason="reversal_reclaim_recross",
                            now=now,
                            prem_detail=detail,
                            premium_source_at_exit=prem_src,
                        )
                        self._record_exit("reversal_reclaim_recross", pnl_pct, prem_src, diag)
                        telemetry["exit_diagnostics"] = diag
                        return prem, "reversal_reclaim_recross", telemetry

        degraded_mode = bool(osnap.get("degraded_data_active"))
        degraded_min = 0.0
        degraded_reason = ""
        degraded_tighten_active = False
        if degraded_mode:
            degraded_since = osnap.get("degraded_data_since")
            if isinstance(degraded_since, str):
                try:
                    degraded_dt = self._normalize_dt(datetime.fromisoformat(degraded_since))
                    degraded_min = max(0.0, (now - degraded_dt).total_seconds() / 60.0)
                except Exception:
                    degraded_min = 0.0
            telemetry["degraded_data_active"] = True
            telemetry["degraded_data_minutes"] = degraded_min
            telemetry["degraded_data_source"] = osnap.get("degraded_data_source")
            degraded_reason = str(osnap.get("degraded_data_source") or prem_src or "unknown_degraded_source")
            # Availability-based safety: continue managing with fallback data, but force exit on true outage.
            if not has_live_data and not has_fallback_data:
                if bool(osnap.get("open_grace_active")):
                    telemetry["open_grace_active"] = True
                    telemetry["open_grace_attempts"] = int(osnap.get("open_grace_attempts", 0) or 0)
                    log.warning(
                        "OPTIONS_STEALTH | stage=degraded_no_fallback_but_open_grace_hold | symbol=%s | trade_id=%s | source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("degraded_data_source") or prem_src or "unknown"),
                    )
                    return None
                _log_min_hold_bypassed_for_protection("degraded_data_no_fallback")
                log.warning(
                    "OPTIONS_DATA_OUTAGE | trade_id=%s | symbol=%s | reason=no_live_and_no_fallback | degraded_min=%.2f",
                    position_id,
                    position_symbol,
                    degraded_min,
                )
                log.warning(
                    "OPTIONS_STEALTH | stage=degraded_data_no_fallback_exit | symbol=%s | trade_id=%s | degraded_min=%.2f | source=%s",
                    position_symbol,
                    position_id,
                    degraded_min,
                    osnap.get("degraded_data_source"),
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="degraded_data_no_fallback",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag["degraded_data_minutes"] = degraded_min
                diag["degraded_data_source"] = osnap.get("degraded_data_source")
                if self.config.require_live_quotes and str(osnap.get("degraded_data_source") or "") == "live_quote_unavailable":
                    _emit_option_degraded_live_quote_log(
                        self.config,
                        symbol=position_symbol,
                        trade_id=str(position_id or ""),
                        reason=str(osnap.get("last_live_degraded_reason") or "unknown"),
                        action_taken="exit",
                        dedupe_key=f"exitnf|{position_id}|{position_symbol}",
                        force=True,
                    )
                self._record_exit("degraded_data_no_fallback", pnl_pct, prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                return prem, "degraded_data_no_fallback", telemetry

            last_any_data_ts = osnap.get("last_any_data_ts")
            outage_seconds = 0.0
            if isinstance(last_any_data_ts, str):
                try:
                    last_any_dt = self._normalize_dt(datetime.fromisoformat(last_any_data_ts))
                    outage_seconds = max(0.0, (now - last_any_dt).total_seconds())
                except Exception:
                    outage_seconds = 0.0
            max_outage_seconds = max(10.0, float(self.config.no_data_outage_seconds))
            telemetry["degraded_outage_seconds"] = outage_seconds
            telemetry["degraded_outage_limit_seconds"] = max_outage_seconds
            degraded_duration_seconds = 0.0
            if isinstance(osnap.get("degraded_start_timestamp"), str):
                try:
                    degraded_dt = self._normalize_dt(datetime.fromisoformat(str(osnap.get("degraded_start_timestamp"))))
                    degraded_duration_seconds = max(0.0, (now - degraded_dt).total_seconds())
                except Exception:
                    degraded_duration_seconds = 0.0
            degraded_min_duration = max(0.0, float(self.config.tline_degraded_exit_min_duration_seconds))
            degraded_exit_allowed = (outage_seconds >= max_outage_seconds) and (degraded_duration_seconds >= degraded_min_duration)
            log.info(
                "TRENDLINE_DEGRADED_EXIT_CHECK | trade_id=%s | symbol=%s | degraded_duration=%.1f | threshold=%.1f | exit_allowed=%s",
                position_id,
                position_symbol,
                degraded_duration_seconds,
                degraded_min_duration,
                str(bool(degraded_exit_allowed)).lower(),
            )
            if degraded_exit_allowed and (not is_0dte_single):
                _log_min_hold_bypassed_for_protection("degraded_data_outage")
                log.warning(
                    "OPTIONS_DATA_OUTAGE | trade_id=%s | symbol=%s | reason=outage_seconds_exceeded | outage_sec=%.1f | limit_sec=%.1f",
                    position_id,
                    position_symbol,
                    outage_seconds,
                    max_outage_seconds,
                )
                log.warning(
                    "OPTIONS_STEALTH | stage=degraded_data_outage_exit | symbol=%s | trade_id=%s | outage_sec=%.1f | limit_sec=%.1f | source=%s",
                    position_symbol,
                    position_id,
                    outage_seconds,
                    max_outage_seconds,
                    osnap.get("degraded_data_source"),
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="degraded_data_outage",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag["degraded_data_minutes"] = degraded_min
                diag["degraded_data_source"] = osnap.get("degraded_data_source")
                diag["outage_seconds"] = outage_seconds
                diag["outage_limit_seconds"] = max_outage_seconds
                diag["degraded_duration_seconds"] = degraded_duration_seconds
                if self.config.require_live_quotes and str(osnap.get("degraded_data_source") or "") == "live_quote_unavailable":
                    _emit_option_degraded_live_quote_log(
                        self.config,
                        symbol=position_symbol,
                        trade_id=str(position_id or ""),
                        reason=str(osnap.get("last_live_degraded_reason") or "unknown"),
                        action_taken="exit",
                        dedupe_key=f"exitout|{position_id}|{position_symbol}",
                        force=True,
                    )
                self._record_exit("degraded_data_outage", pnl_pct, prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                return prem, "degraded_data_outage", telemetry
            tighten_after_min = max(0.0, float(self.config.degraded_tighten_after_minutes))
            degraded_tighten_active = degraded_min >= tighten_after_min and tighten_after_min > 0.0
        else:
            telemetry["degraded_data_active"] = False

        log.info(
            "OPTIONS_MONITOR_MODE | trade_id=%s | symbol=%s | quote_source=%s | exit_logic_active=true | reason=%s",
            position_id,
            position_symbol,
            prem_src,
            degraded_reason if degraded_mode else "none",
        )
        live_non_synthetic_quote = (not synthetic_active) and (
            str(detail.get("price_source_telemetry") or "").strip().lower() == "live"
            or prem_src in {"exact", "nearest", "cached_quote", "spread_leg_quotes", "spread_mtm_mid_last"}
        )
        tline_trusted_degraded = False
        if is_trendline_0dte and (not is_spread):
            tline_trusted_degraded = tline_trusted_degraded_mark(
                self.config,
                prem_src=prem_src,
                mark_quality=str(mark_quality or ""),
                quote_age_seconds=quote_age_seconds,
                synthetic_active=bool(synthetic_active),
            )
        live_or_tline_protection_quote = bool(live_non_synthetic_quote) or bool(tline_trusted_degraded)
        protection_quote_ok = live_non_synthetic_quote
        if is_trendline_0dte and (not is_spread):
            protection_quote_ok = live_or_tline_protection_quote
        if (
            is_orb_0dte
            and is_spread
            and bool(osnap.get("trusted_degraded_spread_mark"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        ):
            protection_quote_ok = bool(live_non_synthetic_quote) or bool(osnap.get("trusted_degraded_spread_mark"))
        if profile.micro_lock_enabled and protection_quote_ok:
            protection_floor_pct = None
            if float(st.max_pnl_pct) >= float(profile.profit_lock_trigger_pct):
                protection_floor_pct = float(profile.profit_lock_pct)
                osnap["profit_lock_active"] = True
                osnap["micro_lock_active"] = True
            else:
                micro_trig_use = float(self.config.micro_lock_trigger_pct)
                if is_trendline_0dte and (not is_spread):
                    micro_trig_use = min(micro_trig_use, float(self.config.tline_micro_lock_trigger_pct))
                if float(st.max_pnl_pct) >= micro_trig_use:
                    protection_floor_pct = float(self.config.micro_lock_level_pct)
                    osnap["micro_lock_active"] = True
            if protection_floor_pct is not None:
                lock_floor = float(st.entry_premium or 0.0) * (1.0 + float(protection_floor_pct))
                if st.breakeven_floor is None or lock_floor > float(st.breakeven_floor):
                    st.breakeven_floor = lock_floor
                    st.mode = OptionStealthMode.BREAKEVEN
                    if st.breakeven_activated_at is None:
                        st.breakeven_activated_at = now
                        osnap["breakeven_activated_at"] = now.isoformat()
                    osnap["protection_floor_pct"] = float(protection_floor_pct)

        if bool(osnap.get("profit_lock_active")) and not bool(osnap.get("_option_profit_lock_micro_logged")):
            log.info(
                "OPTION_PROFIT_LOCK_ARMED | trade_id=%s | symbol=%s | strategy_type=%s | via=micro_lock_engine | "
                "max_pnl_pct=%.4f | profit_lock_trigger_pct=%.4f | profit_lock_level_pct=%.4f | micro_lock_active=%s | "
                "live_non_synthetic_quote=%s | premium_source=%s | mark_is_exit_grade=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                float(st.max_pnl_pct),
                float(profile.profit_lock_trigger_pct),
                float(profile.profit_lock_pct),
                str(bool(osnap.get("micro_lock_active", False))).lower(),
                str(bool(protection_quote_ok)).lower(),
                prem_src,
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
            )
            osnap["_option_profit_lock_micro_logged"] = True

        dd_trig = float(profile.max_pnl_drawdown_trigger_pct)
        dd_exit = float(profile.max_pnl_drawdown_exit_pct)
        if (
            is_orb_0dte
            and is_spread
            and bool(getattr(st, "had_underlying_opportunity", False))
            and bool(osnap.get("trusted_degraded_spread_mark"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        ):
            sc = float(getattr(self.config, "orb_degraded_drawdown_tighten_scale", 0.88) or 0.88)
            dd_trig *= sc
            dd_exit *= sc
            osnap["orb_drawdown_thresholds_tightened"] = True
        else:
            osnap["orb_drawdown_thresholds_tightened"] = False

        if (
            profile.max_pnl_drawdown_enabled
            and protection_quote_ok
            and float(st.max_pnl_pct) >= float(dd_trig)
            and float(drawdown_from_max_pct) >= float(dd_exit)
        ):
            log.info(
                "OPTIONS_STEALTH_MAX_PNL_DRAWDOWN_EXIT | trade_id=%s | symbol=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "drawdown_from_max_pct=%.4f | trigger_pct=%.4f | exit_pct=%.4f",
                position_id,
                position_symbol,
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                float(drawdown_from_max_pct),
                float(dd_trig),
                float(dd_exit),
            )
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason="options_stealth:max_pnl_drawdown_exit",
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["drawdown_from_max_pct"] = float(drawdown_from_max_pct)
            self._record_exit("options_stealth:max_pnl_drawdown_exit", pnl_pct, prem_src, diag)
            telemetry["exit_diagnostics"] = diag
            return prem, "options_stealth:max_pnl_drawdown_exit", telemetry
        drawdown_exit_eligible = bool(
            profile.max_pnl_drawdown_enabled
            and protection_quote_ok
            and float(st.max_pnl_pct) >= float(dd_trig)
            and float(drawdown_from_max_pct) >= float(dd_exit)
        )
        log.info(
            "OPTIONS_STEALTH_PROTECTION_STATE | trade_id=%s | micro_lock_active=%s | profit_lock_active=%s | be_active=%s | "
            "trailing_active=%s | drawdown_exit_eligible=%s | live_non_synthetic_quote=%s | tline_trusted_degraded=%s | protection_quote_ok=%s",
            position_id,
            str(bool(osnap.get("micro_lock_active", False))).lower(),
            str(bool(osnap.get("profit_lock_active", False))).lower(),
            str(bool(st.breakeven_floor is not None)).lower(),
            str(bool(st.trailing_activated_at is not None)).lower(),
            str(bool(drawdown_exit_eligible)).lower(),
            str(bool(live_non_synthetic_quote)).lower(),
            str(bool(tline_trusted_degraded)).lower(),
            str(bool(protection_quote_ok)).lower(),
        )
        eval_key = f"ev|{str(position_id or '-')}:{position_symbol}"
        if _lifecycle_tick_should_emit(_option_exit_trigger_eval_last, eval_key):
            log.info(
                "OPTION_EXIT_TRIGGER_EVAL | trade_id=%s | symbol=%s | strategy_type=%s | synthetic_active=%s | "
                "live_non_synthetic_quote=%s | mark_is_exit_grade=%s | mark_quality=%s | premium_source=%s | "
                "drawdown_exit_eligible=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | "
                "good_mark_tick_count=%d | timeout_clock_frozen=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                str(bool(synthetic_active)).lower(),
                str(bool(live_non_synthetic_quote)).lower(),
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                str(osnap.get("mark_quality") or ""),
                prem_src,
                str(bool(drawdown_exit_eligible)).lower(),
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                float(drawdown_from_max_pct),
                int(st.good_mark_tick_count),
                str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
            )

        be_trigger_scale = 1.0
        trailing_trigger_scale = 1.0
        trailing_pct_scale = 1.0
        if degraded_tighten_active:
            be_trigger_scale = max(0.25, min(1.0, float(self.config.degraded_be_trigger_scale)))
            trailing_trigger_scale = max(0.25, min(1.0, float(self.config.degraded_trailing_trigger_scale)))
            trailing_pct_scale = max(0.25, min(1.0, float(self.config.degraded_trailing_pct_scale)))
            log.warning(
                "OPTIONS_STEALTH | stage=degraded_risk_tighten | trade_id=%s | symbol=%s | degraded_min=%.2f | "
                "be_trigger_scale=%.2f | trailing_trigger_scale=%.2f | trailing_pct_scale=%.2f",
                position_id,
                position_symbol,
                degraded_min,
                be_trigger_scale,
                trailing_trigger_scale,
                trailing_pct_scale,
            )
            log.info(
                "OPTIONS_STEALTH | stage=degraded_data_continue | symbol=%s | trade_id=%s | source=%s | has_live=%s | has_fallback=%s | outage_sec=%.1f",
                position_symbol,
                position_id,
                prem_src,
                str(bool(has_live_data)).lower(),
                str(bool(has_fallback_data)).lower(),
                outage_seconds,
            )
        is_orb_0dte = str(st.strategy_type or "").lower() == "orb_0dte"
        # Emit effective runtime thresholds for ORB/Trendline exit logic.
        if is_orb_0dte:
            effective_orb_be_trigger_pct = max(
                0.03,
                float(self.config.breakeven_trigger_pct) * float(be_trigger_scale) * float(self.config.orb_be_trigger_mult),
            )
            effective_orb_be_lock_pct = float(self.config.early_be_lock_pct or self.config.breakeven_lock_pct)
            effective_orb_trailing_trigger_pct = max(
                0.03,
                float(self.config.trailing_trigger_pct) * float(trailing_trigger_scale) * float(self.config.orb_trailing_trigger_mult),
            )
            effective_orb_profit_lock_trigger_pct = float(self.config.orb_profit_lock_trigger_pct)
            orb_sig = (
                round(effective_orb_be_trigger_pct, 6),
                round(effective_orb_be_lock_pct, 6),
                round(effective_orb_trailing_trigger_pct, 6),
                round(effective_orb_profit_lock_trigger_pct, 6),
                round(float(self.config.fast_fail_minutes), 4),
                round(float(self.config.fast_fail_min_pnl_pct), 6),
            )
            if osnap.get("_orb_threshold_sig") != orb_sig:
                osnap["_orb_threshold_sig"] = orb_sig
                log.info(
                    "OPTION_STEALTH_EFFECTIVE_THRESHOLDS | path=orb_0dte | symbol=%s | trade_id=%s | "
                    "effective_orb_be_trigger_pct=%.4f | effective_orb_be_lock_pct=%.4f | effective_orb_trailing_trigger_pct=%.4f | "
                    "effective_orb_profit_lock_trigger_pct=%.4f | fast_fail_minutes=%.2f | fast_fail_min_pnl_pct=%.4f",
                    position_symbol,
                    position_id,
                    effective_orb_be_trigger_pct,
                    effective_orb_be_lock_pct,
                    effective_orb_trailing_trigger_pct,
                    effective_orb_profit_lock_trigger_pct,
                    float(self.config.fast_fail_minutes),
                    float(self.config.fast_fail_min_pnl_pct),
                )
        elif is_trendline_0dte:
            effective_tline_be_trigger_pct = float(self.config.tline_breakeven_activate_pct)
            effective_tline_be_lock_pct = float(self.config.tline_breakeven_offset_pct)
            effective_tline_trailing_trigger_pct = max(
                0.01,
                float(self.config.trailing_trigger_pct) * float(trailing_trigger_scale),
            )
            tline_sig = (
                round(effective_tline_be_trigger_pct, 6),
                round(effective_tline_be_lock_pct, 6),
                round(effective_tline_trailing_trigger_pct, 6),
                round(float(self.config.tline_impulse_breakeven_activate_pct), 6),
                round(float(self.config.tline_impulse_trailing_trigger_pct), 6),
                round(float(self.config.tline_retest_breakeven_activate_pct), 6),
                round(float(self.config.tline_retest_trail_activate_pct), 6),
                round(float(self.config.tline_slow_trend_breakeven_activate_pct), 6),
                round(float(self.config.tline_slow_trend_trail_activate_pct), 6),
            )
            if osnap.get("_tline_threshold_sig") != tline_sig:
                osnap["_tline_threshold_sig"] = tline_sig
                log.info(
                    "OPTION_STEALTH_EFFECTIVE_THRESHOLDS | path=trendline_0dte | symbol=%s | trade_id=%s | "
                    "effective_tline_be_trigger_pct=%.4f | effective_tline_be_lock_pct=%.4f | effective_tline_trailing_trigger_pct=%.4f | "
                    "impulse_be_trigger_pct=%.4f | impulse_trailing_trigger_pct=%.4f | retest_be_trigger_pct=%.4f | retest_trailing_trigger_pct=%.4f | "
                    "slow_trend_be_trigger_pct=%.4f | slow_trend_trailing_trigger_pct=%.4f",
                    position_symbol,
                    position_id,
                    effective_tline_be_trigger_pct,
                    effective_tline_be_lock_pct,
                    effective_tline_trailing_trigger_pct,
                    float(self.config.tline_impulse_breakeven_activate_pct),
                    float(self.config.tline_impulse_trailing_trigger_pct),
                    float(self.config.tline_retest_breakeven_activate_pct),
                    float(self.config.tline_retest_trail_activate_pct),
                    float(self.config.tline_slow_trend_breakeven_activate_pct),
                    float(self.config.tline_slow_trend_trail_activate_pct),
                )

        # Exit priority audit: impulse lock > underlying > reversal > trailing > breakeven > fast-fail.
        reversal_eligible = bool(is_orb_0dte and st.had_opportunity and float(st.max_pnl_pct) >= 0.10 and (float(st.max_pnl_pct) - float(current_pnl_pct)) >= 0.07)
        fast_fail_eligible = bool(self.config.fast_fail_enabled and (not is_trendline_0dte) and held_min >= float(self.config.fast_fail_minutes) and float(st.max_pnl_pct) < float(self.config.fast_fail_min_pnl_pct))
        log.info(
            "OPTION_STEALTH_EXIT_PRIORITY_AUDIT | symbol=%s | trade_id=%s | order=impulse_lock_underlying_reversal_trailing_be_fast_fail | reversal_eligible=%s | fast_fail_eligible=%s",
            position_symbol,
            position_id,
            str(reversal_eligible).lower(),
            str(fast_fail_eligible).lower(),
        )

        # ORB 0DTE reversal protection: if trade had opportunity, protect against large giveback.
        if (not profile.max_pnl_drawdown_enabled) and (not synthetic_active) and is_orb_0dte and bool(st.had_opportunity):
            max_pnl_pct = float(st.max_pnl_pct)
            drawdown_from_max_pct = max_pnl_pct - float(current_pnl_pct)
            if max_pnl_pct >= 0.10 and drawdown_from_max_pct >= 0.07:
                log.info(
                    "OPTION_REVERSAL_EXIT | symbol=%s | trade_id=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f",
                    position_symbol,
                    position_id,
                    float(current_pnl_pct),
                    max_pnl_pct,
                    drawdown_from_max_pct,
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="reversal_exit",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag["max_pnl_pct"] = max_pnl_pct
                diag["current_pnl_pct"] = float(current_pnl_pct)
                diag["drawdown_from_max_pct"] = drawdown_from_max_pct
                self._record_exit("reversal_exit", pnl_pct, prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                return prem, "reversal_exit", telemetry

        # Trendline 0DTE giveback exit (thresholds from OPTION_STEALTH_TLINE_DRAWDOWN_*).
        if (
            (not profile.max_pnl_drawdown_enabled)
            and (not synthetic_active)
            and is_trendline_0dte
            and (not is_spread)
            and bool(st.had_opportunity)
        ):
            max_pnl_tl = float(st.max_pnl_pct)
            dd_tl = max_pnl_tl - float(current_pnl_pct)
            th_max = float(self.config.tline_drawdown_exit_min_max_pnl_pct)
            th_dd = float(self.config.tline_drawdown_exit_min_drawdown_pct)
            if max_pnl_tl >= th_max and dd_tl >= th_dd:
                log.info(
                    "TRENDLINE_REVERSAL_EXIT | symbol=%s | trade_id=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                    "drawdown_from_max_pct=%.4f | threshold_max_pnl_pct=%.4f | threshold_drawdown_pct=%.4f",
                    position_symbol,
                    position_id,
                    float(current_pnl_pct),
                    max_pnl_tl,
                    dd_tl,
                    th_max,
                    th_dd,
                )
                diag_tl = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="trendline_reversal_exit",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag_tl["max_pnl_pct"] = max_pnl_tl
                diag_tl["current_pnl_pct"] = float(current_pnl_pct)
                diag_tl["drawdown_from_max_pct"] = dd_tl
                self._record_exit("trendline_reversal_exit", pnl_pct, prem_src, diag_tl)
                telemetry["exit_diagnostics"] = diag_tl
                return prem, "trendline_reversal_exit", telemetry

        pending_fast_fail_diag: Optional[Dict[str, Any]] = None
        if self.config.fast_fail_enabled and (not is_trendline_0dte):
            ff_min = float(self.config.fast_fail_minutes)
            ff_min_pnl = float(self.config.fast_fail_min_pnl_pct)
            ff_active = held_min >= ff_min
            log.info(
                "TRENDLINE_PIPELINE | stage=option_fast_fail_check | symbol=%s | trade_id=%s | held_min=%.2f | "
                "pnl_pct=%.4f | max_pnl_pct=%.4f | favorable_advance=%s | active=%s",
                position_symbol,
                position_id,
                held_min,
                pnl_pct,
                float(st.max_pnl_pct),
                st.favorable_advance_seen,
                ff_active,
            )
            if ff_active and (float(st.max_pnl_pct) < ff_min_pnl):
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_fast_fail_exit | symbol=%s | trade_id=%s | held_min=%.2f | "
                    "pnl_pct=%.4f | max_pnl_pct=%.4f | min_required=%.4f",
                    position_symbol,
                    position_id,
                    held_min,
                    pnl_pct,
                    float(st.max_pnl_pct),
                    ff_min_pnl,
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="fast_fail",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                pending_fast_fail_diag = diag

        if self.config.adverse_guard_enabled and entry_u > 0 and (not min_hold_active):
            if is_trendline_0dte and (not is_spread) and pnl_pct >= 0.10:
                log.info(
                    "TRENDLINE_ADVERSE_GUARD_SKIPPED | trade_id=%s | symbol=%s | reason=profit_lock | pnl_pct=%.4f",
                    position_id,
                    position_symbol,
                    pnl_pct,
                )
                trendline_line_px = _line_value_at(st.trendline, now) if st.trendline else 0.0
                break_high = (
                    osnap.get("break_candle_high")
                    or osnap.get("break_high")
                    or (st.trendline.get("break_candle_high") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("break_high") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("rearm_break_candle_high") if isinstance(st.trendline, dict) else None)
                )
                break_low = (
                    osnap.get("break_candle_low")
                    or osnap.get("break_low")
                    or (st.trendline.get("break_candle_low") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("break_low") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("rearm_break_candle_low") if isinstance(st.trendline, dict) else None)
                )
                current_high = float(osnap.get("current_high") or current_underlying)
                current_low = float(osnap.get("current_low") or current_underlying)
                break_high_f = float(break_high) if isinstance(break_high, (int, float)) else None
                break_low_f = float(break_low) if isinstance(break_low, (int, float)) else None
                is_put_side = str(st.option_side or "").lower() == "put"
                structure_exit = False
                if trendline_line_px > 0:
                    if is_put_side:
                        structure_exit = (
                            current_underlying > trendline_line_px
                            or (break_high_f is not None and current_high > break_high_f)
                        )
                    else:
                        structure_exit = (
                            current_underlying < trendline_line_px
                            or (break_low_f is not None and current_low < break_low_f)
                        )
                if structure_exit:
                    if _underlying_signal_confirmed_exit("trendline_structure_exit"):
                        log.info(
                            "TRENDLINE_STRUCTURE_EXIT | trade_id=%s | symbol=%s | option_side=%s | line_px=%.6f | current_underlying=%.6f | current_high=%.6f | current_low=%.6f | break_high=%s | break_low=%s",
                            position_id,
                            position_symbol,
                            str(st.option_side or "").lower(),
                            float(trendline_line_px),
                            float(current_underlying),
                            float(current_high),
                            float(current_low),
                            f"{break_high_f:.6f}" if break_high_f is not None else "none",
                            f"{break_low_f:.6f}" if break_low_f is not None else "none",
                        )
                        diag = self._build_exit_diagnostics(
                            position_id=position_id,
                            position_symbol=position_symbol,
                            block=block,
                            st=st,
                            osnap=osnap,
                            exit_premium=prem,
                            exit_reason="trendline_structure_exit",
                            now=now,
                            prem_detail=detail,
                            premium_source_at_exit=prem_src,
                        )
                        self._record_exit("trendline_structure_exit", pnl_pct, prem_src, diag)
                        telemetry["exit_diagnostics"] = diag
                        return prem, "trendline_structure_exit", telemetry
            adverse_move_pct = 0.0
            if str(st.option_side).lower() == "put":
                adverse_move_pct = max(0.0, (current_underlying - entry_u) / entry_u)
            else:
                adverse_move_pct = max(0.0, (entry_u - current_underlying) / entry_u)
            ag_min = max(10.0, float(self.config.adverse_guard_min_minutes))
            ag_move = float(self.config.adverse_guard_underlying_move_pct)
            ag_max_pnl = float(self.config.adverse_guard_max_pnl_pct)
            ag_held_ok = held_min >= ag_min
            if is_trendline_0dte:
                ag_held_ok = ag_held_ok and (held_seconds >= 600.0)
            if ag_held_ok and adverse_move_pct >= ag_move and pnl_pct <= ag_max_pnl:
                if _underlying_signal_confirmed_exit("adverse_guard"):
                    log.info(
                        "OPTIONS_STEALTH | stage=adverse_guard_exit | symbol=%s | trade_id=%s | held_min=%.2f | "
                        "adverse_move_pct=%.4f | threshold=%.4f | pnl_pct=%.4f | max_allowed=%.4f",
                        position_symbol,
                        position_id,
                        held_min,
                        adverse_move_pct,
                        ag_move,
                        pnl_pct,
                        ag_max_pnl,
                    )
                    diag = self._build_exit_diagnostics(
                        position_id=position_id,
                        position_symbol=position_symbol,
                        block=block,
                        st=st,
                        osnap=osnap,
                        exit_premium=prem,
                        exit_reason="adverse_guard",
                        now=now,
                        prem_detail=detail,
                        premium_source_at_exit=prem_src,
                    )
                    diag["adverse_move_pct"] = adverse_move_pct
                    diag["adverse_guard_threshold"] = ag_move
                    diag["adverse_guard_min_minutes"] = ag_min
                    self._record_exit("adverse_guard", pnl_pct, prem_src, diag)
                    telemetry["exit_diagnostics"] = diag
                    return prem, "adverse_guard", telemetry

        adaptive_spread_be_trigger_pct: Optional[float] = None
        adaptive_spread_trailing_trigger_pct: Optional[float] = None
        if is_spread and st.position_type == OptionPositionType.DEBIT_SPREAD.value:
            try:
                entry_debit_val = float(st.entry_debit or st.entry_value or st.entry_premium or 0.0)
                long_strike = None
                short_strike = None
                for leg in st.legs or []:
                    if not isinstance(leg, dict):
                        continue
                    ls = str(leg.get("long_or_short") or "").lower()
                    strike_v = float(leg.get("strike") or 0.0)
                    if strike_v <= 0:
                        continue
                    if ls == "long" and long_strike is None:
                        long_strike = strike_v
                    elif ls == "short" and short_strike is None:
                        short_strike = strike_v
                spread_width = (
                    abs(float(long_strike) - float(short_strike))
                    if long_strike is not None and short_strike is not None
                    else 0.0
                )
                max_profit = max(0.0, spread_width - entry_debit_val)
                max_profit_pct = (max_profit / entry_debit_val) if entry_debit_val > 0 else 0.0
                if max_profit_pct > 0:
                    adaptive_spread_be_trigger_pct = min(
                        float(self.config.spread_be_trigger_pct),
                        max(0.05, max_profit_pct * 0.65),
                    )
                    adaptive_spread_trailing_trigger_pct = min(
                        float(self.config.spread_trailing_trigger_pct),
                        max(0.06, max_profit_pct * 0.85),
                    )
                    prev_cap = osnap.get("_spread_trigger_cap_logged")
                    cap_sig = (
                        round(float(max_profit_pct), 4),
                        round(float(adaptive_spread_be_trigger_pct), 4),
                        round(float(adaptive_spread_trailing_trigger_pct), 4),
                    )
                    if prev_cap != cap_sig:
                        osnap["_spread_trigger_cap_logged"] = cap_sig
                        log.info(
                            "OPTIONS_STEALTH | stage=spread_trigger_adaptation | trade_id=%s | max_profit_pct=%.4f | "
                            "be_trigger=%.4f | trailing_trigger=%.4f",
                            position_id,
                            max_profit_pct,
                            adaptive_spread_be_trigger_pct,
                            adaptive_spread_trailing_trigger_pct,
                        )
            except Exception as spread_cap_err:
                log.debug(
                    "OPTIONS_STEALTH | stage=spread_trigger_adaptation_skip | trade_id=%s | reason=%s",
                    position_id,
                    spread_cap_err,
                )

        time_exit_minutes = float(profile.time_exit_minutes)
        no_progress_exit_minutes = float(profile.no_progress_minutes)
        if (
            is_orb_0dte
            and (not is_trendline_0dte)
            and is_spread
            and bool(osnap.get("orb_spread_degraded_running"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        ):
            time_exit_minutes = float(time_exit_minutes) + float(
                getattr(self.config, "orb_spread_degraded_time_exit_slack_minutes", 1.5) or 0.0
            )
            no_progress_exit_minutes = float(no_progress_exit_minutes) + float(
                getattr(self.config, "orb_spread_degraded_no_progress_slack_minutes", 1.5) or 0.0
            )
        trailing_trigger_pct = (
            float(adaptive_spread_trailing_trigger_pct)
            if adaptive_spread_trailing_trigger_pct is not None
            else float(profile.trail_trigger_pct)
        )
        tline_hold_chop_exits = (
            is_trendline_0dte
            and (not is_spread)
            and (not is_impulse_mode)
            and pnl_pct >= float(self.config.tline_chop_hold_min_pnl_pct)
        )
        if (not synthetic_active) and is_trendline_0dte and is_impulse_mode:
            impulse_tp_target = min(
                0.80, max(0.30, float(self.config.tline_impulse_tp_target_pct))
            )
            if pnl_pct >= impulse_tp_target:
                log.warning(
                    "TRENDLINE_IMPULSE_EXIT | trade_id=%s | symbol=%s | reason=impulse_take_profit | pnl_pct=%.4f | target_pct=%.4f",
                    position_id,
                    position_symbol,
                    pnl_pct,
                    impulse_tp_target,
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="impulse_take_profit",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                self._record_exit("impulse_take_profit", pnl_pct, prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                return prem, "impulse_take_profit", telemetry
        required_good_ticks = int(max(1, getattr(self.config, "timeout_min_good_mark_ticks", 3)))
        mark_is_exit_grade_now = bool(osnap.get("mark_is_exit_grade"))
        timeout_clock_frozen_now = bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))
        favorable_underlying_move_pct = 0.0
        if entry_u > 0 and current_underlying > 0:
            if str(st.option_side).lower() == "put":
                favorable_underlying_move_pct = (float(entry_u) - float(current_underlying)) / float(entry_u)
            else:
                favorable_underlying_move_pct = (float(current_underlying) - float(entry_u)) / float(entry_u)
        favorable_underlying = bool(
            favorable_underlying_move_pct >= float(getattr(self.config, "no_progress_favorable_underlying_move_pct", 0.0035))
        )
        if favorable_underlying:
            log.info(
                "OPTION_MARK_UNDERLYING_DIVERGENCE_AUDIT | trade_id=%s | symbol=%s | option_side=%s | strategy_type=%s | "
                "entry_underlying=%.6f | current_underlying=%.6f | underlying_move_pct=%.4f | favorable_underlying_move_pct=%.4f | "
                "entry_net_debit=%.6f | current_mid_value=%.6f | current_liquidation_value=%.6f | current_pnl_pct_liquidation=%.4f | "
                "mark_quality=%s | mark_is_exit_grade=%s | exit_blocked=%s | bypass_reason=%s",
                str(position_id or ""),
                position_symbol,
                str(st.option_side or ""),
                str(st.strategy_type or ""),
                float(entry_u),
                float(current_underlying),
                float(favorable_underlying_move_pct),
                float(getattr(self.config, "no_progress_favorable_underlying_move_pct", 0.0035)),
                float(st.entry_debit or st.entry_value or st.entry_premium or 0.0),
                float(detail.get("spread_mid_value") if is_spread else prem or 0.0),
                float(detail.get("spread_liquidation_value") if is_spread else detail.get("option_bid") or prem or 0.0),
                float(detail.get("current_pnl_pct_liquidation") if is_spread else ((float(detail.get("option_bid") or prem or 0.0) - float(st.entry_premium or 0.0)) / float(st.entry_premium or 1.0))),
                str(osnap.get("mark_quality") or "unknown"),
                str(bool(mark_is_exit_grade_now)).lower(),
                "true",
                "favorable_underlying",
            )
        orb_relief_active, orb_relief_tier, orb_relief_detail = self._orb_spread_timeout_exit_relief(
            is_orb_0dte=is_orb_0dte,
            is_spread=is_spread,
            is_trendline_0dte=is_trendline_0dte,
            st=st,
            osnap=osnap,
            detail=detail,
            prem_src=str(prem_src or ""),
            prem=float(prem or 0.0),
            held_min=float(held_min),
            mark_is_exit_grade_now=mark_is_exit_grade_now,
            mark_quality=str(osnap.get("mark_quality") or ""),
            now=now,
        )
        if is_orb_0dte and (not is_trendline_0dte) and is_spread:
            osnap["orb_spread_relief_active"] = bool(orb_relief_active)
            osnap["orb_spread_relief_tier"] = str(orb_relief_tier or "") if orb_relief_active else ""
            osnap["orb_spread_relief_detail"] = str(orb_relief_detail or "") if orb_relief_active else ""
            if orb_relief_active:
                _rk = f"{str(orb_relief_tier or '')}"
                if str(osnap.get("_orb_spread_relief_armed_logged_tier") or "") != _rk:
                    osnap["_orb_spread_relief_armed_logged_tier"] = _rk
                    log.info(
                        "OPTION_SPREAD_EXIT_RELIEF_ARMED | trade_id=%s | symbol=%s | relief_tier=%s | relief_detail=%s | "
                        "mark_is_exit_grade=%s | prem_src=%s | is_spread=true",
                        str(position_id or "-"),
                        str(position_symbol or "-"),
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(bool(mark_is_exit_grade_now)).lower(),
                        str(prem_src or ""),
                    )
        orb_trusted_exit_grade_bypass = bool(
            is_orb_0dte
            and (not is_trendline_0dte)
            and is_spread
            and bool(osnap.get("trusted_degraded_spread_mark"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        )
        osnap["orb_trusted_exit_grade_bypass"] = bool(orb_trusted_exit_grade_bypass)
        orb_np_relax = bool(
            orb_trusted_exit_grade_bypass
            and held_min >= float(getattr(self.config, "orb_no_progress_relax_exit_grade_min_minutes", 11.0))
            and float(st.max_pnl_pct or 0.0) <= float(getattr(self.config, "orb_no_progress_relax_max_mfe_pct", 0.06))
            and (not favorable_underlying)
        )
        osnap["orb_no_progress_relax_active"] = bool(orb_np_relax)
        orb_grade_timeout_bypass = bool(
            is_orb_0dte
            and (not is_trendline_0dte)
            and bool(getattr(self.config, "orb_safety_exits_bypass_exit_grade_gates", True))
        )
        osnap["orb_grade_timeout_bypass"] = bool(orb_grade_timeout_bypass)
        if is_orb_0dte and (not is_trendline_0dte):
            if (
                mark_is_exit_grade_now
                and (not timeout_clock_frozen_now)
                and int(st.good_mark_tick_count) >= required_good_ticks
            ):
                osnap.pop("orb_time_exit_suppress_started_iso", None)
                osnap.pop("orb_no_progress_suppress_started_iso", None)
        if (
            (not synthetic_active)
            and held_min >= time_exit_minutes
            and pnl_pct < trailing_trigger_pct
            and (not tline_hold_chop_exits)
            and not min_hold_blocks_tline
        ):
            grade_and_tick_blocked = (
                (not mark_is_exit_grade_now)
                or timeout_clock_frozen_now
                or int(st.good_mark_tick_count) < required_good_ticks
            )
            if orb_relief_active or orb_trusted_exit_grade_bypass:
                grade_and_tick_blocked = False
            if orb_grade_timeout_bypass:
                grade_and_tick_blocked = False
            if bool(getattr(self.config, "require_exit_grade_for_timeouts", True)) and (
                favorable_underlying or grade_and_tick_blocked
            ):
                hold_reason = (
                    "favorable_underlying"
                    if favorable_underlying
                    else "timeout_clock_frozen"
                    if timeout_clock_frozen_now
                    else "insufficient_good_marks"
                    if int(st.good_mark_tick_count) < required_good_ticks
                    else "non_exit_grade_mark"
                )
                hold_token = "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD" if is_trendline_0dte else "ORB_OPTIONS_DEGRADED_DATA_HOLD"
                if is_orb_0dte and (not is_trendline_0dte):
                    osnap["orb_last_exit_hold_reason"] = f"time_exit:{hold_reason}"
                log.warning(
                    "%s | trade_id=%s | symbol=%s | candidate_exit_reason=time_exit | blocked_reason=%s | mark_quality=%s | mark_is_exit_grade=%s | "
                    "good_mark_tick_count=%d | timeout_clock_frozen=%s | quote_age_seconds=%s",
                    hold_token,
                    str(position_id or ""),
                    position_symbol,
                    hold_reason,
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    osnap.get("quote_age_seconds"),
                )
                if is_orb_0dte and (not is_trendline_0dte):
                    _log_orb_exit_suppressed_quote_quality(
                        candidate_exit_reason="time_exit",
                        blocked_reason=str(hold_reason),
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        prem_src=str(prem_src or ""),
                        osnap=osnap,
                        st=st,
                    )
                    if hold_reason != "favorable_underlying":
                        _maybe_emit_orb_exit_grade_timeout_suppression(
                            suppress_kind="time_exit",
                            position_id=str(position_id or ""),
                            position_symbol=position_symbol,
                            blocked_reason=str(hold_reason),
                            prem_src=str(prem_src or ""),
                            osnap=osnap,
                            st=st,
                            required_good_ticks=int(required_good_ticks),
                            now=now,
                        )
                    osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                    self._emit_orb_options_exit_deferred_audit(
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        position_type=str(st.position_type or ""),
                        candidate_exit_reason="time_exit",
                        blocked_reason=str(hold_reason),
                        st=st,
                        osnap=osnap,
                        detail=detail,
                        prem_src=str(prem_src or ""),
                        prem=float(prem or 0.0),
                        relief_active=bool(orb_relief_active),
                        relief_tier=str(orb_relief_tier or ""),
                        relief_detail=str(orb_relief_detail or ""),
                    )
            else:
                if orb_relief_active:
                    osnap["orb_last_degraded_exit_tier"] = str(orb_relief_tier or "")
                    osnap["orb_last_degraded_exit_detail"] = str(orb_relief_detail or "")
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT_ATTEMPT | trade_id=%s | symbol=%s | tier=%s | "
                        "candidate_exit_reason=time_exit | detail=%s | prem_src=%s",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                    )
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT | trade_id=%s | symbol=%s | tier=%s | candidate_exit_reason=time_exit | detail=%s | "
                        "prem_src=%s | mark_quality=%s | est_liquidation=%.6f",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0),
                    )
                log.info(
                    "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                    "candidate_exit_reason=time_exit | final_exit_reason=time_exit | exit_allowed=true | blocked_reason=none | "
                    "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | "
                    "underlying_move_pct=%.4f | favorable_underlying=%s | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | "
                    "max_pnl_pct=%.4f | had_opportunity=%s | elapsed_seconds=%.1f | adjusted_elapsed_seconds=%.1f",
                    str(position_id or ""),
                    position_symbol,
                    str(st.strategy_type or ""),
                    str(st.position_type or ""),
                    str(st.option_side or ""),
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    osnap.get("quote_age_seconds"),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    float(favorable_underlying_move_pct),
                    str(bool(favorable_underlying)).lower(),
                    float(detail.get("current_pnl_pct_mid") if is_spread else current_pnl_pct),
                    float(detail.get("current_pnl_pct_liquidation") if is_spread else current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(bool(st.had_opportunity)).lower(),
                    float(held_min * 60.0),
                    float(max(0.0, (held_min * 60.0) - float(st.timeout_frozen_seconds or 0.0))),
                )
                if is_trendline_0dte and is_impulse_mode:
                    log.warning(
                        "TRENDLINE_IMPULSE_EXIT | trade_id=%s | symbol=%s | reason=impulse_time_exit | held_min=%.2f | pnl_pct=%.4f",
                        position_id,
                        position_symbol,
                        held_min,
                        pnl_pct,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=time_exit | held_min=%.2f",
                    position_id,
                    held_min,
                )
                if is_orb_0dte and (not is_trendline_0dte):
                    osnap.pop("orb_time_exit_suppress_started_iso", None)
                    osnap.pop("orb_last_exit_hold_reason", None)
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="time_exit",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                self._record_exit("time_exit", pnl_pct, prem_src, diag)
                log.info(
                    "OPTIONS_STEALTH_TIME_EXIT | trade_id=%s | symbol=%s | held_min=%.2f | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_id,
                    position_symbol,
                    float(held_min),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                )
                telemetry["exit_diagnostics"] = diag
                return prem, "time_exit", telemetry
        # Keep strong runners alive: no-progress timeout is only for weak/non-trending trades.
        if (
            st.last_hwm_at
            and held_min >= 2.0
            and pnl_pct < trailing_trigger_pct
            and (not tline_hold_chop_exits)
            and not (is_trendline_0dte and (not is_spread))
        ):
            stall_minutes = max(
                0.0,
                ((now - self._normalize_dt(st.last_hwm_at)).total_seconds() - float(st.timeout_frozen_seconds or 0.0)) / 60.0,
            )
            stalled = stall_minutes >= no_progress_exit_minutes
            if bool(getattr(self.config, "require_exit_grade_for_timeouts", True)) and (not orb_grade_timeout_bypass) and (
                (not mark_is_exit_grade_now) or timeout_clock_frozen_now or int(st.good_mark_tick_count) < required_good_ticks
            ) and (not orb_relief_active) and (not orb_np_relax):
                stalled = False
            if stalled:
                held_seconds = held_min * 60.0
                if is_trendline_0dte and held_seconds < float(self.config.tline_min_hold_seconds):
                    log.info(
                        "TRENDLINE_TIMEOUT_SKIPPED | trade_id=%s | symbol=%s | reason=min_hold | held_seconds=%.1f | min_hold_seconds=%.1f",
                        position_id,
                        position_symbol,
                        held_seconds,
                        float(self.config.tline_min_hold_seconds),
                    )
                    stalled = False
                elif is_orb_0dte and is_spread and entry_u > 0 and current_underlying > 0:
                    side = str(st.option_side or "").lower()
                    favorable_move_pct = (
                        (float(current_underlying) - float(entry_u)) / float(entry_u)
                        if side != "put"
                        else (float(entry_u) - float(current_underlying)) / float(entry_u)
                    )
                    favorable_threshold = float(
                        self.config.orb_spread_no_progress_favorable_underlying_move_pct
                    )
                    if favorable_move_pct >= favorable_threshold:
                        log.info(
                            "ORB_OPTIONS_NO_PROGRESS_BYPASS | trade_id=%s | symbol=%s | reason=favorable_underlying_continuation | "
                            "favorable_move_pct=%.4f | threshold=%.4f | premium_source=%s",
                            position_id,
                            position_symbol,
                            float(favorable_move_pct),
                            float(favorable_threshold),
                            str(prem_src or "unknown"),
                        )
                        stalled = False
                # Shared favorable-underlying protection for ORB + Trendline
                if favorable_underlying:
                    stalled = False
                if is_trendline_0dte and is_slow_trend_mode:
                    log.info(
                        "TRENDLINE_TIMEOUT_SKIPPED | trade_id=%s | symbol=%s | reason=valid_structure | detail=slow_trend_mode",
                        position_id,
                        position_symbol,
                    )
                    stalled = False
            if stalled:
                trendline_line_px = _line_value_at(st.trendline, now) if st.trendline else 0.0
                break_meta = st.trendline.get("break_event") if isinstance(st.trendline, dict) else None
                if not isinstance(break_meta, dict):
                    break_meta = {}
                distance_increasing = bool(
                    osnap.get(
                        "distance_increasing",
                        break_meta.get("distance_increasing", False),
                    )
                )
                is_put_side = str(st.option_side or "").lower() == "put"
                price_no_longer_beyond_break_level = False
                if trendline_line_px > 0:
                    if is_put_side:
                        price_no_longer_beyond_break_level = current_underlying >= trendline_line_px
                    else:
                        price_no_longer_beyond_break_level = current_underlying <= trendline_line_px
                timeout_structure_gate = (
                    (not distance_increasing)
                    and price_no_longer_beyond_break_level
                    and (not is_slow_trend_mode)
                )
                if is_trendline_0dte and (not timeout_structure_gate):
                    log.info(
                        "TRENDLINE_TIMEOUT_SKIPPED | trade_id=%s | symbol=%s | reason=valid_structure | distance_increasing=%s | price_no_longer_beyond_break_level=%s | slow_trend_mode=%s",
                        position_id,
                        position_symbol,
                        str(bool(distance_increasing)).lower(),
                        str(bool(price_no_longer_beyond_break_level)).lower(),
                        str(bool(is_slow_trend_mode)).lower(),
                    )
                    stalled = False
            if stalled and is_trendline_0dte:
                # Structure-aware timeout replacement: exit only on explicit structure break.
                trendline_line_px = _line_value_at(st.trendline, now) if st.trendline else 0.0
                break_high = (
                    osnap.get("break_candle_high")
                    or osnap.get("break_high")
                    or (st.trendline.get("break_candle_high") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("break_high") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("rearm_break_candle_high") if isinstance(st.trendline, dict) else None)
                )
                break_low = (
                    osnap.get("break_candle_low")
                    or osnap.get("break_low")
                    or (st.trendline.get("break_candle_low") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("break_low") if isinstance(st.trendline, dict) else None)
                    or (st.trendline.get("rearm_break_candle_low") if isinstance(st.trendline, dict) else None)
                )
                current_high = float(osnap.get("current_high") or current_underlying)
                current_low = float(osnap.get("current_low") or current_underlying)
                break_high_f = float(break_high) if isinstance(break_high, (int, float)) else None
                break_low_f = float(break_low) if isinstance(break_low, (int, float)) else None
                is_put_side = str(st.option_side or "").lower() == "put"
                structure_exit = False
                if trendline_line_px > 0:
                    if is_put_side:
                        structure_exit = (
                            current_underlying > trendline_line_px
                            or (break_high_f is not None and current_high > break_high_f)
                        )
                    else:
                        structure_exit = (
                            current_underlying < trendline_line_px
                            or (break_low_f is not None and current_low < break_low_f)
                        )
                if not structure_exit:
                    log.info(
                        "TRENDLINE_TIMEOUT_SKIPPED | trade_id=%s | symbol=%s | reason=valid_structure | line_px=%.6f | current_underlying=%.6f | current_high=%.6f | current_low=%.6f | break_high=%s | break_low=%s",
                        position_id,
                        position_symbol,
                        float(trendline_line_px),
                        float(current_underlying),
                        float(current_high),
                        float(current_low),
                        f"{break_high_f:.6f}" if break_high_f is not None else "none",
                        f"{break_low_f:.6f}" if break_low_f is not None else "none",
                    )
                    stalled = False
                else:
                    if _underlying_signal_confirmed_exit("trendline_structure_exit_timeout_gate"):
                        log.info(
                            "TRENDLINE_STRUCTURE_EXIT | trade_id=%s | symbol=%s | option_side=%s | line_px=%.6f | current_underlying=%.6f | current_high=%.6f | current_low=%.6f | break_high=%s | break_low=%s",
                            position_id,
                            position_symbol,
                            str(st.option_side or "").lower(),
                            float(trendline_line_px),
                            float(current_underlying),
                            float(current_high),
                            float(current_low),
                            f"{break_high_f:.6f}" if break_high_f is not None else "none",
                            f"{break_low_f:.6f}" if break_low_f is not None else "none",
                        )
                        st.no_progress_timeout_triggered = True
                        diag = self._build_exit_diagnostics(
                            position_id=position_id,
                            position_symbol=position_symbol,
                            block=block,
                            st=st,
                            osnap=osnap,
                            exit_premium=prem,
                            exit_reason="trendline_structure_exit",
                            now=now,
                            prem_detail=detail,
                            premium_source_at_exit=prem_src,
                        )
                        self._record_exit("trendline_structure_exit", pnl_pct, prem_src, diag)
                        telemetry["exit_diagnostics"] = diag
                        return prem, "trendline_structure_exit", telemetry
                    stalled = False
            if stalled and non_live_premium:
                log.info(
                    "ORB_OPTIONS_NO_PROGRESS_BYPASS | trade_id=%s | symbol=%s | reason=non_live_premium_source | premium_source=%s | price_source_telemetry=%s",
                    position_id,
                    position_symbol,
                    str(prem_src or "unknown"),
                    str(px_telem_g or "unknown"),
                )
                stalled = False
            if stalled and (not synthetic_active):
                if orb_relief_active:
                    osnap["orb_last_degraded_exit_tier"] = str(orb_relief_tier or "")
                    osnap["orb_last_degraded_exit_detail"] = str(orb_relief_detail or "")
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT_ATTEMPT | trade_id=%s | symbol=%s | tier=%s | "
                        "candidate_exit_reason=no_progress_timeout | detail=%s | prem_src=%s",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                    )
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT | trade_id=%s | symbol=%s | tier=%s | candidate_exit_reason=no_progress_timeout | detail=%s | "
                        "prem_src=%s | mark_quality=%s | est_liquidation=%.6f",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0),
                    )
                log.info(
                    "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                    "candidate_exit_reason=no_progress_timeout | final_exit_reason=no_progress_timeout | exit_allowed=true | blocked_reason=none | "
                    "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | "
                    "underlying_move_pct=%.4f | favorable_underlying=%s | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | "
                    "max_pnl_pct=%.4f | had_opportunity=%s | elapsed_seconds=%.1f | adjusted_elapsed_seconds=%.1f",
                    str(position_id or ""),
                    position_symbol,
                    str(st.strategy_type or ""),
                    str(st.position_type or ""),
                    str(st.option_side or ""),
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    osnap.get("quote_age_seconds"),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    float(favorable_underlying_move_pct),
                    str(bool(favorable_underlying)).lower(),
                    float(detail.get("current_pnl_pct_mid") if is_spread else current_pnl_pct),
                    float(detail.get("current_pnl_pct_liquidation") if is_spread else current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(bool(st.had_opportunity)).lower(),
                    float(held_min * 60.0),
                    float(max(0.0, (held_min * 60.0) - float(st.timeout_frozen_seconds or 0.0))),
                )
                st.no_progress_timeout_triggered = True
                if is_orb_0dte and (not is_trendline_0dte):
                    osnap.pop("orb_no_progress_suppress_started_iso", None)
                    osnap.pop("orb_last_exit_hold_reason", None)
                if is_trendline_0dte and is_impulse_mode:
                    log.warning(
                        "TRENDLINE_IMPULSE_EXIT | trade_id=%s | symbol=%s | reason=impulse_no_progress | stalled_min>=%.1f | pnl_pct=%.4f",
                        position_id,
                        position_symbol,
                        no_progress_exit_minutes,
                        pnl_pct,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=no_progress_timeout | stalled_min>=%.1f",
                    position_id,
                    no_progress_exit_minutes,
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="no_progress_timeout",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                self._record_exit("no_progress_timeout", pnl_pct, prem_src, diag)
                log.info(
                    "OPTIONS_STEALTH_NO_PROGRESS_EXIT | trade_id=%s | symbol=%s | stalled_min=%.2f | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_id,
                    position_symbol,
                    float(no_progress_exit_minutes),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                )
                telemetry["exit_diagnostics"] = diag
                return prem, "no_progress_timeout", telemetry
            elif (
                bool(getattr(self.config, "require_exit_grade_for_timeouts", True))
                and (not orb_grade_timeout_bypass)
                and (not orb_relief_active)
                and (not orb_np_relax)
                and st.last_hwm_at
                and held_min >= 2.0
                and pnl_pct < trailing_trigger_pct
                and (not tline_hold_chop_exits)
            ):
                hold_reason = (
                    "favorable_underlying"
                    if favorable_underlying
                    else "timeout_clock_frozen"
                    if timeout_clock_frozen_now
                    else "insufficient_good_marks"
                    if int(st.good_mark_tick_count) < required_good_ticks
                    else "non_exit_grade_mark"
                )
                hold_token = "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD" if is_trendline_0dte else "ORB_OPTIONS_DEGRADED_DATA_HOLD"
                if is_orb_0dte and (not is_trendline_0dte):
                    osnap["orb_last_exit_hold_reason"] = f"no_progress_timeout:{hold_reason}"
                log.warning(
                    "%s | trade_id=%s | symbol=%s | candidate_exit_reason=no_progress_timeout | blocked_reason=%s | mark_quality=%s | "
                    "mark_is_exit_grade=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | quote_age_seconds=%s",
                    hold_token,
                    str(position_id or ""),
                    position_symbol,
                    hold_reason,
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    osnap.get("quote_age_seconds"),
                )
                if is_orb_0dte and (not is_trendline_0dte):
                    _log_orb_exit_suppressed_quote_quality(
                        candidate_exit_reason="no_progress_timeout",
                        blocked_reason=str(hold_reason),
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        prem_src=str(prem_src or ""),
                        osnap=osnap,
                        st=st,
                    )
                    if hold_reason != "favorable_underlying":
                        _maybe_emit_orb_exit_grade_timeout_suppression(
                            suppress_kind="no_progress_timeout",
                            position_id=str(position_id or ""),
                            position_symbol=position_symbol,
                            blocked_reason=str(hold_reason),
                            prem_src=str(prem_src or ""),
                            osnap=osnap,
                            st=st,
                            required_good_ticks=int(required_good_ticks),
                            now=now,
                        )
                    osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                    self._emit_orb_options_exit_deferred_audit(
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        position_type=str(st.position_type or ""),
                        candidate_exit_reason="no_progress_timeout",
                        blocked_reason=str(hold_reason),
                        st=st,
                        osnap=osnap,
                        detail=detail,
                        prem_src=str(prem_src or ""),
                        prem=float(prem or 0.0),
                        relief_active=bool(orb_relief_active),
                        relief_tier=str(orb_relief_tier or ""),
                        relief_detail=str(orb_relief_detail or ""),
                    )

        if (not synthetic_active) and is_trendline_0dte and (not is_spread) and (not tline_hold_chop_exits):
            tline_np_hold_min = float(
                self.config.tline_impulse_no_progress_exit_minutes
                if is_impulse_mode
                else self.config.tline_no_progress_exit_minutes
            )
            tline_np_underlying_fail = (not favorable_underlying) or (
                bool(self.config.tline_underlying_reclaim_protect)
                and bool(use_structure_invalidation)
                and (not bool(structure_ok))
            )
            if (
                st.trailing_activated_at is None
                and held_min >= max(4.0, tline_np_hold_min)
                and float(st.max_pnl_pct) < float(self.config.tline_no_progress_single_max_mfe_pct)
                and tline_np_underlying_fail
                and (not min_hold_blocks_tline)
                and bool(protection_quote_ok)
                and (not bool(structure_ok))
            ):
                grade_blocked_np = bool(
                    bool(getattr(self.config, "require_exit_grade_for_timeouts", True))
                    and (
                        (not mark_is_exit_grade_now)
                        or timeout_clock_frozen_now
                        or int(st.good_mark_tick_count) < required_good_ticks
                    )
                    and (not orb_relief_active)
                )
                relax_np = bool(
                    bool(self.config.tline_no_progress_single_relax_exit_grade) and bool(tline_trusted_degraded)
                )
                if grade_blocked_np and (not relax_np):
                    if not osnap.get("_tline_single_np_grade_defer_logged"):
                        osnap["_tline_single_np_grade_defer_logged"] = True
                        log.warning(
                            "TRENDLINE_SINGLE_NO_PROGRESS_DEFERRED | trade_id=%s | symbol=%s | held_min=%.2f | max_pnl_pct=%.4f | "
                            "mark_quality=%s | mark_is_exit_grade=%s | trusted_degraded=%s",
                            str(position_id or "-"),
                            position_symbol,
                            float(held_min),
                            float(st.max_pnl_pct),
                            str(osnap.get("mark_quality") or "unknown"),
                            str(bool(mark_is_exit_grade_now)).lower(),
                            str(bool(tline_trusted_degraded)).lower(),
                        )
                else:
                    log.info(
                        "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=trendline_single_no_progress | "
                        "held_min=%.2f | max_pnl_pct=%.4f | favorable_underlying=%s | structure_ok=%s | impulse=%s",
                        position_id,
                        float(held_min),
                        float(st.max_pnl_pct),
                        str(bool(favorable_underlying)).lower(),
                        str(bool(structure_ok)).lower(),
                        str(bool(is_impulse_mode)).lower(),
                    )
                    diag = self._build_exit_diagnostics(
                        position_id=position_id,
                        position_symbol=position_symbol,
                        block=block,
                        st=st,
                        osnap=osnap,
                        exit_premium=prem,
                        exit_reason="no_progress_timeout",
                        now=now,
                        prem_detail=detail,
                        premium_source_at_exit=prem_src,
                    )
                    diag["trendline_single_no_progress"] = True
                    self._record_exit("no_progress_timeout", pnl_pct, prem_src, diag)
                    telemetry["exit_diagnostics"] = diag
                    return prem, "no_progress_timeout", telemetry

        be_trigger_pct = float(
            adaptive_spread_be_trigger_pct
            if adaptive_spread_be_trigger_pct is not None
            else profile.be_trigger_pct
        )
        be_trigger_pct = max(0.01, float(be_trigger_pct) * float(be_trigger_scale))
        if is_trendline_0dte and not is_spread:
            abs_delta = abs(float(st.delta_at_entry or 0.0))
            if abs_delta >= float(self.config.tline_be_delta_high_threshold):
                be_trigger_pct = float(self.config.tline_be_trigger_high_delta_pct)
            elif abs_delta >= float(self.config.tline_be_delta_mid_threshold):
                be_trigger_pct = float(self.config.tline_be_trigger_mid_delta_pct)
            else:
                be_trigger_pct = float(self.config.tline_be_trigger_low_delta_pct)
        log.info(
            "TRENDLINE_PIPELINE | stage=option_breakeven_candidate | symbol=%s | delta=%.4f | trigger_pct=%.4f",
            position_symbol,
            float(st.delta_at_entry or 0.0),
            be_trigger_pct,
        )
        if (
            (not synthetic_active)
            and is_trendline_0dte
            and (not is_spread)
            and st.trailing_activated_at is None
            and held_min >= float(self.config.tline_no_progress_early_minutes)
            and pnl_pct < float(self.config.tline_no_progress_early_max_pnl_pct)
            and not min_hold_blocks_tline
            and (not favorable_underlying)
            and (not bool(structure_ok))
        ):
            log.info(
                "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=no_progress_timeout_early | held_min=%.2f | "
                "pnl_pct=%.4f | structure_ok=%s | favorable_underlying=%s",
                position_id,
                held_min,
                pnl_pct,
                str(bool(structure_ok)).lower(),
                str(bool(favorable_underlying)).lower(),
            )
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason="no_progress_timeout_early",
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["no_progress_timeout_early"] = True
            self._record_exit("no_progress_timeout_early", pnl_pct, prem_src, diag)
            telemetry["exit_diagnostics"] = diag
            return prem, "no_progress_timeout_early", telemetry
        be_ready_time = held_seconds >= float(profile.min_seconds_before_be)
        be_hwm_ok = (not self.config.require_new_hwm_for_be) or (st.premium_hwm > (st.entry_premium * 1.0001))
        if is_trendline_0dte and (not is_spread):
            be_hwm_ok = st.premium_hwm > (st.entry_premium * 1.0001)
        if (
            is_trendline_0dte
            and (not is_spread)
            and bool(self.config.tline_be_relax_hwm_with_underlying)
            and bool(getattr(st, "had_underlying_opportunity", False))
            and float(st.max_pnl_pct) >= float(self.config.tline_be_relax_hwm_min_pnl_pct)
        ):
            be_hwm_ok = True
        if (
            pnl_pct >= be_trigger_pct
            and st.breakeven_floor is None
            and be_ready_time
            and be_hwm_ok
        ):
            if is_spread:
                lock_pct = float(self.config.spread_be_lock_pct)
                base_for_floor = float(st.entry_value or st.entry_premium)
                st.breakeven_floor = base_for_floor * (1.0 + lock_pct)
            else:
                lock_pct = float(profile.be_lock_pct)
                if lock_pct <= 0:
                    lock_pct = float(self.config.breakeven_lock_pct)
                st.breakeven_floor = st.entry_premium * (1.0 + lock_pct)
            st.breakeven_activated_at = now
            st.be_lock_pct_used = lock_pct
            st.mode = OptionStealthMode.BREAKEVEN
            osnap["breakeven_activated_at"] = now.isoformat()
            osnap["be_activation_timestamp"] = now.isoformat()
            osnap["be_activation_hwm"] = float(st.premium_hwm)
            osnap["_trendline_be_activation_threshold"] = be_trigger_pct
            if is_trendline_0dte and (not is_spread):
                log.info(
                    "TRENDLINE_BREAKEVEN_ACTIVATED | symbol=%s | trade_id=%s | trendline_mode=%s | pnl_pct=%.4f | max_pnl_pct=%.4f | current_premium=%.4f | entry_premium=%.4f | breakeven_floor=%.4f | trailing_stop_pct=%.4f | data_source=%s",
                    position_symbol,
                    position_id,
                    str(osnap.get("trendline_mode") or "STANDARD"),
                    pnl_pct,
                    float((st.premium_hwm / st.entry_premium - 1.0) if st.entry_premium > 0 else 0.0),
                    prem,
                    st.entry_premium,
                    st.breakeven_floor,
                    float(st.last_trail_pct or 0.0),
                    prem_src,
                )
            else:
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_breakeven | symbol=%s | trade_id=%s | floor_premium=%.4f | "
                    "trigger_pct=%.4f | lock_pct=%.4f | delta_bucket=%s",
                    position_symbol,
                    position_id,
                    st.breakeven_floor,
                    be_trigger_pct,
                    lock_pct,
                    st.delta_bucket,
                )
            log.info(
                "OPTION_BE_ARMED | trade_id=%s | symbol=%s | strategy_type=%s | via=breakeven_trigger | "
                "breakeven_floor=%.6f | be_trigger_pct=%.4f | be_lock_pct=%.4f | pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "premium_hwm=%.6f | premium_source=%s | mark_is_exit_grade=%s | is_spread=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                float(st.breakeven_floor or 0.0),
                float(be_trigger_pct),
                float(lock_pct),
                float(pnl_pct),
                float(st.max_pnl_pct),
                float(st.premium_hwm or 0.0),
                prem_src,
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                str(bool(is_spread)).lower(),
            )

        be_exit_allowed = True
        seconds_since_be_activation = 0.0
        hwm_since_be = False
        if st.breakeven_floor is not None and is_trendline_0dte and (not is_spread):
            be_activated_iso = osnap.get("be_activation_timestamp") or osnap.get("breakeven_activated_at")
            if isinstance(be_activated_iso, str):
                try:
                    be_dt = self._normalize_dt(datetime.fromisoformat(str(be_activated_iso)))
                    seconds_since_be_activation = max(0.0, (now - be_dt).total_seconds())
                except Exception:
                    seconds_since_be_activation = 0.0
            be_activation_hwm = osnap.get("be_activation_hwm")
            try:
                be_activation_hwm_f = float(be_activation_hwm) if be_activation_hwm is not None else float(st.entry_premium)
            except Exception:
                be_activation_hwm_f = float(st.entry_premium)
            hwm_since_be = float(st.premium_hwm) > (be_activation_hwm_f + 1e-9)
            be_exit_allowed = (
                seconds_since_be_activation >= float(self.config.tline_min_seconds_after_be_activation)
                or hwm_since_be
            )
            log.info(
                "TRENDLINE_BE_BUFFER | trade_id=%s | symbol=%s | be_active=%s | seconds_since_be_activation=%.1f | hwm_since_be=%s | be_exit_allowed=%s",
                position_id,
                position_symbol,
                str(bool(st.breakeven_floor is not None)).lower(),
                float(seconds_since_be_activation),
                str(bool(hwm_since_be)).lower(),
                str(bool(be_exit_allowed)).lower(),
            )

        pending_be_diag: Optional[Dict[str, Any]] = None
        if st.breakeven_floor is not None and prem <= st.breakeven_floor and be_exit_allowed:
            be_exit_reason = (
                "options_stealth:profit_floor_exit"
                if bool(osnap.get("profit_lock_active") or osnap.get("micro_lock_active"))
                else "breakeven_stop"
            )
            if is_trendline_0dte and (not is_spread):
                structure_invalid = _trendline_structure_invalid()
                if not structure_invalid:
                    log.info(
                        "TRENDLINE_BREAKEVEN_HELD_VALID_STRUCTURE | symbol=%s | trade_id=%s | trendline_mode=%s | pnl_pct=%.4f | max_pnl_pct=%.4f | current_premium=%.4f | entry_premium=%.4f | breakeven_floor=%.4f | trailing_stop_pct=%.4f | data_source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("trendline_mode") or "STANDARD"),
                        pnl_pct,
                        float((st.premium_hwm / st.entry_premium - 1.0) if st.entry_premium > 0 else 0.0),
                        prem,
                        st.entry_premium,
                        float(st.breakeven_floor),
                        float(st.last_trail_pct or 0.0),
                        prem_src,
                    )
                else:
                    _log_min_hold_bypassed_for_protection("breakeven_stop")
                    log.info(
                        "TRENDLINE_BREAKEVEN_EXIT | symbol=%s | trade_id=%s | trendline_mode=%s | pnl_pct=%.4f | max_pnl_pct=%.4f | current_premium=%.4f | entry_premium=%.4f | breakeven_floor=%.4f | trailing_stop_pct=%.4f | data_source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("trendline_mode") or "STANDARD"),
                        pnl_pct,
                        float((st.premium_hwm / st.entry_premium - 1.0) if st.entry_premium > 0 else 0.0),
                        prem,
                        st.entry_premium,
                        float(st.breakeven_floor),
                        float(st.last_trail_pct or 0.0),
                        prem_src,
                    )
                    diag = self._build_exit_diagnostics(
                        position_id=position_id,
                        position_symbol=position_symbol,
                        block=block,
                        st=st,
                        osnap=osnap,
                        exit_premium=prem,
                        exit_reason=be_exit_reason,
                        now=now,
                        prem_detail=detail,
                        premium_source_at_exit=prem_src,
                    )
                    pending_be_diag = diag
            else:
                _log_min_hold_bypassed_for_protection("breakeven_stop")
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=breakeven_stop | prem=%.4f | floor=%.4f",
                    position_id,
                    prem,
                    st.breakeven_floor,
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason=be_exit_reason,
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                pending_be_diag = diag

        profit_lock_quote_ok = bool(live_non_synthetic_quote)
        if is_trendline_0dte and (not is_spread):
            profit_lock_quote_ok = bool(protection_quote_ok)
        if (not synthetic_active) and profit_lock_quote_ok and pnl_pct >= float(profile.profit_lock_trigger_pct):
            lock_floor = st.entry_premium * (1.0 + float(profile.profit_lock_pct))
            if st.breakeven_floor is None or lock_floor > float(st.breakeven_floor):
                prev_floor = st.breakeven_floor
                st.breakeven_floor = lock_floor
                if st.breakeven_activated_at is None:
                    st.breakeven_activated_at = now
                    osnap["be_activation_timestamp"] = self._normalize_dt(st.breakeven_activated_at).isoformat()
                    osnap["be_activation_hwm"] = float(st.premium_hwm)
                st.be_lock_pct_used = max(
                    float(st.be_lock_pct_used or 0.0), float(profile.profit_lock_pct)
                )
                st.mode = OptionStealthMode.BREAKEVEN
                osnap["breakeven_activated_at"] = self._normalize_dt(st.breakeven_activated_at).isoformat()
                log.info(
                    "OPTIONS_STEALTH_PROFIT_FLOOR_LOCK | trade_id=%s | symbol=%s | pnl_pct=%.4f | "
                    "floor=%.4f | prev_floor=%s | lock_pct=%.4f",
                    position_id,
                    position_symbol,
                    pnl_pct,
                    st.breakeven_floor,
                    f"{float(prev_floor):.4f}" if prev_floor is not None else "none",
                    float(profile.profit_lock_pct),
                )
                if not bool(osnap.get("_option_profit_lock_floor_logged")):
                    log.info(
                        "OPTION_PROFIT_LOCK_ARMED | trade_id=%s | symbol=%s | strategy_type=%s | via=profit_floor_tier | "
                        "pnl_pct=%.4f | floor=%.6f | lock_pct=%.4f | premium_source=%s | mark_is_exit_grade=%s",
                        str(position_id or "-"),
                        position_symbol,
                        str(st.strategy_type or ""),
                        float(pnl_pct),
                        float(st.breakeven_floor or 0.0),
                        float(profile.profit_lock_pct),
                        prem_src,
                        str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                    )
                    osnap["_option_profit_lock_floor_logged"] = True

        trail_pct = float(profile.trail_distance_pct)
        if not is_spread:
            if pnl_pct >= self.config.moon_pnl_threshold_pct:
                st.mode = OptionStealthMode.MOON
                trail_pct = self.config.moon_trailing_pct
            elif pnl_pct >= self.config.explosive_pnl_threshold_pct:
                st.mode = OptionStealthMode.EXPLOSIVE
                trail_pct = self.config.explosive_trailing_pct

        trailing_gate = float(profile.trail_trigger_pct)
        if is_trendline_0dte and (not is_spread):
            if pnl_pct >= float(self.config.tline_runner_trigger_pct):
                trail_pct = float(self.config.tline_runner_trailing_pct)
            elif pnl_pct >= float(self.config.tline_explosive_trigger_pct):
                trail_pct = float(self.config.tline_explosive_trailing_pct)
            elif pnl_pct >= float(self.config.tline_aggressive_trigger_pct):
                trail_pct = float(self.config.tline_aggressive_trailing_pct)
            else:
                trail_pct = float(self.config.tline_base_trailing_pct)
        trailing_gate = max(0.01, float(trailing_gate) * float(trailing_trigger_scale))
        trail_pct = max(0.01, float(trail_pct) * float(trailing_pct_scale))
        if (
            is_trendline_0dte
            and (not is_spread)
            and prem_src != "exact"
            and non_exact_quote_seconds >= float(self.config.tline_degraded_non_exact_tighten_after_seconds)
        ):
            tighten_scale = max(0.80, min(0.95, float(self.config.tline_degraded_non_exact_trail_tighten_scale)))
            trail_pct = max(0.01, float(trail_pct) * float(tighten_scale))
            log.warning(
                "OPTIONS_STEALTH | stage=degraded_non_exact_trail_tighten | trade_id=%s | symbol=%s | source=%s | non_exact_quote_seconds=%.1f | tighten_scale=%.2f",
                position_id,
                position_symbol,
                prem_src,
                non_exact_quote_seconds,
                tighten_scale,
            )
        hwm_pct = (
            (float(st.premium_hwm) / float(st.entry_premium) - 1.0)
            if float(st.entry_premium or 0.0) > 0
            else 0.0
        )
        if is_trendline_0dte and (not is_spread):
            if pnl_pct >= float(self.config.tline_runner_trigger_pct):
                exit_phase = "runner"
            elif pnl_pct >= float(self.config.tline_explosive_trigger_pct):
                exit_phase = "explosive"
            elif pnl_pct >= float(self.config.tline_aggressive_trigger_pct):
                exit_phase = "aggressive"
            elif st.trailing_activated_at is not None:
                exit_phase = "base"
            else:
                exit_phase = "early"
            log.info(
                "EXIT_ENGINE_STATE | trade_id=%s | symbol=%s | pnl_pct=%.4f | hwm_pct=%.4f | trailing_pct=%.4f | be_locked=%s | phase=%s | quote_source=%s",
                position_id,
                position_symbol,
                float(pnl_pct),
                float(hwm_pct),
                float(trail_pct),
                str(bool(st.breakeven_floor is not None)).lower(),
                exit_phase,
                prem_src,
            )
        degraded_mode_for_trailing = str(prem_src or "").lower() not in {"exact", "nearest", "cached_quote"}
        required_trailing_activation_threshold = float(trailing_gate) + (0.05 if degraded_mode_for_trailing else 0.0)
        if is_trendline_0dte and (not is_spread) and is_impulse_mode:
            cap_act = float(self.config.tline_impulse_trail_activation_cap_degraded)
            if cap_act > 0.0:
                required_trailing_activation_threshold = min(float(required_trailing_activation_threshold), cap_act)
        trailing_can_activate = pnl_pct >= required_trailing_activation_threshold
        log.info(
            "TRENDLINE_TRAILING_DEGRADED_ADJUSTMENT | trade_id=%s | symbol=%s | degraded_mode=%s | required_activation_threshold=%.4f | actual_gain_pct=%.4f | trailing_activated=%s",
            position_id,
            position_symbol,
            str(bool(degraded_mode_for_trailing)).lower(),
            float(required_trailing_activation_threshold),
            float(pnl_pct),
            str(bool(st.trailing_activated_at is not None or trailing_can_activate)).lower(),
        )
        if is_trendline_0dte and (not is_spread):
            try:
                eu_snap = float(osnap.get("entry_underlying") or 0.0)
            except (TypeError, ValueError):
                eu_snap = 0.0
            blocked_reason = "none"
            if min_hold_blocks_tline:
                blocked_reason = "min_hold"
            elif (
                bool(getattr(self.config, "require_exit_grade_for_timeouts", True))
                and (
                    (not mark_is_exit_grade_now)
                    or timeout_clock_frozen_now
                    or int(st.good_mark_tick_count) < required_good_ticks
                )
            ):
                blocked_reason = "timeout_quote_grade_or_clock"
            cand_exit = "evaluating"
            if min_hold_blocks_tline:
                cand_exit = "min_hold"
            elif float(st.max_pnl_pct) < float(self.config.tline_no_progress_single_max_mfe_pct) and held_min >= max(
                4.0,
                float(
                    self.config.tline_impulse_no_progress_exit_minutes
                    if is_impulse_mode
                    else self.config.tline_no_progress_exit_minutes
                ),
            ):
                cand_exit = "trendline_single_no_progress"
            msg = (
                "OPTION_STEALTH_STATE | trade_id=%s | symbol=%s | path=trendline_0dte | side=%s | held_sec=%.1f | "
                "entry_premium=%.6f | current_premium=%.6f | pnl_pct=%.4f | max_pnl_pct=%.4f | premium_hwm=%.6f | "
                "underlying_entry=%.6f | underlying_now=%.6f | underlying_favorable_pct=%.5f | had_opportunity=%s | "
                "had_underlying_opportunity=%s | be_armed=%s | be_trigger_effective=%.4f | trail_armed=%s | "
                "trail_trigger_effective=%.4f | trail_stop=%s | profit_lock_armed=%s | micro_lock_armed=%s | "
                "premium_source=%s | mark_quality=%s | mark_is_exit_grade=%s | trusted_degraded=%s | synthetic_active=%s | "
                "min_hold_active=%s | candidate_exit_reason=%s | blocked_reason=%s"
                % (
                    str(position_id or "-"),
                    position_symbol,
                    str(st.option_side or ""),
                    float(held_seconds),
                    float(st.entry_premium or 0.0),
                    float(prem or 0.0),
                    float(pnl_pct),
                    float(st.max_pnl_pct),
                    float(st.premium_hwm or 0.0),
                    float(eu_snap),
                    float(current_underlying or 0.0),
                    float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
                    str(bool(st.had_opportunity)).lower(),
                    str(bool(getattr(st, "had_underlying_opportunity", False))).lower(),
                    str(bool(st.breakeven_floor is not None)).lower(),
                    float(be_trigger_pct),
                    str(bool(st.trailing_activated_at is not None)).lower(),
                    float(required_trailing_activation_threshold),
                    f"{float(st.trail_stop_premium):.6f}" if st.trail_stop_premium is not None else "none",
                    str(bool(osnap.get("profit_lock_active"))).lower(),
                    str(bool(osnap.get("micro_lock_active"))).lower(),
                    str(prem_src or ""),
                    str(osnap.get("mark_quality") or ""),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    str(bool(tline_trusted_degraded)).lower(),
                    str(bool(synthetic_active)).lower(),
                    str(bool(min_hold_active)).lower(),
                    cand_exit,
                    blocked_reason,
                )
            )
            _emit_option_stealth_state_if_due(
                cfg=self.config,
                trade_id=str(position_id or "-"),
                symbol=str(position_symbol or "-"),
                message=msg,
            )
        if pnl_pct >= trailing_gate and (st.trailing_activated_at is not None or trailing_can_activate):
            if st.trailing_activated_at is None:
                _log_min_hold_bypassed_for_protection("trailing_activation")
                st.trailing_activated_at = now
                st.mode = OptionStealthMode.TRAILING
                osnap["trailing_activated_at"] = now.isoformat()
                if is_trendline_0dte and (not is_spread):
                    log.info(
                        "TRENDLINE_TRAIL_ACTIVATED | symbol=%s | trade_id=%s | trendline_mode=%s | pnl_pct=%.4f | max_pnl_pct=%.4f | current_premium=%.4f | entry_premium=%.4f | breakeven_floor=%.4f | trailing_stop_pct=%.4f | data_source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("trendline_mode") or "STANDARD"),
                        pnl_pct,
                        float((st.premium_hwm / st.entry_premium - 1.0) if st.entry_premium > 0 else 0.0),
                        prem,
                        st.entry_premium,
                        float(st.breakeven_floor or 0.0),
                        trail_pct,
                        prem_src,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_trailing | trade_id=%s | reason=activate | pnl_pct=%.4f | "
                    "trail_pct=%.4f | trigger_pct=%.4f",
                    position_id,
                    pnl_pct,
                    trail_pct,
                    trailing_gate,
                )
                _trail_stop_preview = float(st.premium_hwm or 0.0) * (1.0 - float(trail_pct))
                log.info(
                    "OPTION_TRAILING_STATE | trade_id=%s | symbol=%s | strategy_type=%s | event=activated | "
                    "trail_pct=%.4f | trailing_gate=%.4f | required_activation_threshold=%.4f | premium_hwm=%.6f | "
                    "initial_trail_stop=%.6f | premium_source=%s | mark_is_exit_grade=%s | degraded_mode_for_trailing=%s",
                    str(position_id or "-"),
                    position_symbol,
                    str(st.strategy_type or ""),
                    float(trail_pct),
                    float(trailing_gate),
                    float(required_trailing_activation_threshold),
                    float(st.premium_hwm or 0.0),
                    float(_trail_stop_preview),
                    prem_src,
                    str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                    str(bool(degraded_mode_for_trailing)).lower(),
                )
            stop = st.premium_hwm * (1.0 - trail_pct)
            prev_stop = st.trail_stop_premium
            st.trail_stop_premium = stop
            st.last_trail_pct = trail_pct
            if prev_stop is not None and stop > prev_stop + 1e-9:
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_trailing | trade_id=%s | action=ratchet_stop | stop=%.4f -> %.4f | hwm=%.4f",
                    position_id,
                    prev_stop,
                    stop,
                    st.premium_hwm,
                )
                log.info(
                    "OPTION_TRAILING_STATE | trade_id=%s | symbol=%s | strategy_type=%s | event=ratchet | "
                    "trail_stop_prev=%.6f | trail_stop_new=%.6f | premium_hwm=%.6f | trail_pct=%.4f | premium_source=%s",
                    str(position_id or "-"),
                    position_symbol,
                    str(st.strategy_type or ""),
                    float(prev_stop),
                    float(stop),
                    float(st.premium_hwm or 0.0),
                    float(trail_pct),
                    prem_src,
                )
            if prem <= stop:
                _log_min_hold_bypassed_for_protection("trailing_stop")
                if is_trendline_0dte and is_impulse_mode:
                    log.warning(
                        "TRENDLINE_IMPULSE_EXIT | trade_id=%s | symbol=%s | reason=impulse_trailing_stop | pnl_pct=%.4f | stop=%.4f",
                        position_id,
                        position_symbol,
                        pnl_pct,
                        stop,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=trailing_stop | prem=%.4f stop=%.4f",
                    position_id,
                    prem,
                    stop,
                )
                if is_trendline_0dte and (not is_spread):
                    log.info(
                        "TRENDLINE_TRAIL_EXIT | symbol=%s | trade_id=%s | trendline_mode=%s | pnl_pct=%.4f | max_pnl_pct=%.4f | current_premium=%.4f | entry_premium=%.4f | breakeven_floor=%.4f | trailing_stop_pct=%.4f | data_source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("trendline_mode") or "STANDARD"),
                        pnl_pct,
                        float((st.premium_hwm / st.entry_premium - 1.0) if st.entry_premium > 0 else 0.0),
                        prem,
                        st.entry_premium,
                        float(st.breakeven_floor or 0.0),
                        trail_pct,
                        prem_src,
                    )
                if is_spread:
                    log.info(
                        "OPTIONS_STEALTH | stage=spread_exit | trade_id=%s | reason=trailing_stop | spread_current=%.6f | spread_hwm=%.6f",
                        position_id,
                        prem,
                        st.spread_hwm,
                    )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="trailing_stop",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                self._record_exit("trailing_stop", pnl_pct, prem_src, diag)
                log.info(
                    "OPTIONS_STEALTH_TRAIL_EXIT | trade_id=%s | symbol=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | trail_pct=%.4f",
                    position_id,
                    position_symbol,
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                    float(trail_pct),
                )
                telemetry["exit_diagnostics"] = diag
                return prem, "trailing_stop", telemetry
            log.debug(
                "TRENDLINE_PIPELINE | stage=option_trailing | trade_id=%s | pnl_pct=%.4f hwm=%.4f stop=%.4f",
                position_id,
                pnl_pct,
                st.premium_hwm,
                stop,
            )

        if pending_be_diag is not None:
            exit_key = str(pending_be_diag.get("exit_reason") or "breakeven_stop")
            if exit_key == "options_stealth:profit_floor_exit":
                log.info(
                    "OPTIONS_STEALTH_PROFIT_FLOOR_EXIT | trade_id=%s | symbol=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_id,
                    position_symbol,
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                )
            self._record_exit(exit_key, pnl_pct, prem_src, pending_be_diag)
            telemetry["exit_diagnostics"] = pending_be_diag
            return prem, exit_key, telemetry

        if pending_fast_fail_diag is not None:
            ff_blocked = (
                (not bool(osnap.get("mark_is_exit_grade")))
                or bool(osnap.get("timeout_clock_frozen"))
                or int(st.good_mark_tick_count) < int(max(1, getattr(self.config, "timeout_min_good_mark_ticks", 3)))
            )
            if orb_relief_active or orb_trusted_exit_grade_bypass:
                ff_blocked = False
            if orb_grade_timeout_bypass and is_orb_0dte and (not is_trendline_0dte):
                ff_blocked = False
            if bool(getattr(self.config, "require_exit_grade_for_weak_exits", True)) and ff_blocked:
                hold_token = "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD" if is_trendline_0dte else "ORB_OPTIONS_DEGRADED_DATA_HOLD"
                log.warning(
                    "%s | trade_id=%s | symbol=%s | candidate_exit_reason=fast_fail | blocked_reason=non_exit_grade_or_clock_frozen | "
                    "mark_quality=%s | mark_is_exit_grade=%s | good_mark_tick_count=%d",
                    hold_token,
                    str(position_id or ""),
                    position_symbol,
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                    int(st.good_mark_tick_count),
                )
                if is_orb_0dte and (not is_trendline_0dte):
                    _log_orb_exit_suppressed_quote_quality(
                        candidate_exit_reason="fast_fail",
                        blocked_reason="non_exit_grade_or_clock_frozen",
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        prem_src=str(prem_src or ""),
                        osnap=osnap,
                        st=st,
                    )
                    osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                    self._emit_orb_options_exit_deferred_audit(
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        position_type=str(st.position_type or ""),
                        candidate_exit_reason="fast_fail",
                        blocked_reason="non_exit_grade_or_clock_frozen",
                        st=st,
                        osnap=osnap,
                        detail=detail,
                        prem_src=str(prem_src or ""),
                        prem=float(prem or 0.0),
                        relief_active=bool(orb_relief_active),
                        relief_tier=str(orb_relief_tier or ""),
                        relief_detail=str(orb_relief_detail or ""),
                    )
                pending_fast_fail_diag = None
            else:
                if orb_relief_active:
                    osnap["orb_last_degraded_exit_tier"] = str(orb_relief_tier or "")
                    osnap["orb_last_degraded_exit_detail"] = str(orb_relief_detail or "")
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT | trade_id=%s | symbol=%s | tier=%s | candidate_exit_reason=fast_fail | detail=%s | "
                        "prem_src=%s | mark_quality=%s | est_liquidation=%.6f",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0),
                    )
                log.info(
                    "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                    "candidate_exit_reason=fast_fail | final_exit_reason=fast_fail | exit_allowed=true | blocked_reason=none | "
                    "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | "
                    "underlying_move_pct=%.4f | favorable_underlying=%s | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | "
                    "max_pnl_pct=%.4f | had_opportunity=%s | elapsed_seconds=%.1f | adjusted_elapsed_seconds=%.1f",
                    str(position_id or ""),
                    position_symbol,
                    str(st.strategy_type or ""),
                    str(st.position_type or ""),
                    str(st.option_side or ""),
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                    osnap.get("quote_age_seconds"),
                    int(st.good_mark_tick_count),
                    str(bool(osnap.get("timeout_clock_frozen"))).lower(),
                    float((float(current_underlying) - float(entry_u)) / float(entry_u) if float(entry_u) > 0 else 0.0),
                    str(bool(favorable_underlying)).lower(),
                    float(detail.get("current_pnl_pct_mid") if is_spread else current_pnl_pct),
                    float(detail.get("current_pnl_pct_liquidation") if is_spread else current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(bool(st.had_opportunity)).lower(),
                    float(held_min * 60.0),
                    float(max(0.0, (held_min * 60.0) - float(st.timeout_frozen_seconds or 0.0))),
                )
        if pending_fast_fail_diag is not None:
            self._record_exit("fast_fail", pnl_pct, prem_src, pending_fast_fail_diag)
            telemetry["exit_diagnostics"] = pending_fast_fail_diag
            return prem, "fast_fail", telemetry

        log_every = max(0.0, float(self.config.position_health_log_sec))
        if log_every > 0.0:
            last_health = osnap.get("_last_position_health_log_ts")
            should_log = False
            if isinstance(last_health, str):
                try:
                    last_health_dt = self._normalize_dt(datetime.fromisoformat(last_health))
                    should_log = (now - last_health_dt).total_seconds() >= log_every
                except Exception:
                    should_log = True
            else:
                should_log = True
            if should_log:
                osnap["_last_position_health_log_ts"] = now.isoformat()
                effective_stop = (
                    st.trail_stop_premium if st.trail_stop_premium is not None else st.breakeven_floor
                )
                log.info(
                    "OPTIONS_STEALTH | stage=position_health | symbol=%s | trade_id=%s | delta_entry=%.3f | premium_current=%.4f | "
                    "premium_source=%s | pnl_pct=%.4f | mode=%s | be_active=%s | trail_active=%s | stop=%.4f | degraded=%s",
                    position_symbol,
                    position_id,
                    float(st.delta_at_entry or 0.0),
                    float(prem),
                    prem_src,
                    float(pnl_pct),
                    st.mode.value,
                    st.breakeven_floor is not None,
                    st.trailing_activated_at is not None,
                    float(effective_stop or 0.0),
                    bool(osnap.get("degraded_data_active")),
                )

        return None

    def force_eod_close_premium(
        self,
        position_metadata: Dict[str, Any],
        current_underlying: float,
        option_quote: Optional[Dict[str, Any]] = None,
        position_symbol: str = "",
        trade_id: str = "",
    ) -> Tuple[float, str, Dict[str, Any]]:
        osnap = position_metadata.get("option_stealth")
        if not isinstance(osnap, dict):
            return 0.0, "eod_no_state", {"premium_source": "none", "effective_premium_used": 0.0}
        block = osnap.get("engine")
        if not isinstance(block, dict):
            return 0.0, "eod_no_state", {"premium_source": "none", "effective_premium_used": 0.0}
        st: OptionStealthState = block["state"]
        entry_u = float(block.get("entry_underlying", current_underlying) or current_underlying)
        sym = position_symbol or str(block.get("underlying_symbol") or "")
        prev_eff = st.last_effective_premium if st.last_effective_premium > 0 else None
        is_spread = st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        )
        if is_spread:
            prem, prem_src, detail = resolve_spread_net_value(
                current_underlying=current_underlying,
                entry_underlying=entry_u,
                cfg=self.config,
                previous_effective=prev_eff,
                position_symbol=sym or "UNKNOWN",
                legs=st.legs,
                option_quote=option_quote,
                entry_value=max(0.01, st.entry_value or st.entry_premium),
                position_type=str(st.position_type or OptionPositionType.DEBIT_SPREAD.value),
                trade_id="",
            )
        else:
            is_0dte_eod = (
                str(st.strategy_type or "").lower() in ("orb_0dte", "trendline_0dte")
                or str(st.strategy_name or "").lower() == "easytrendline_0dte"
            )
            eod_now = datetime.now(timezone.utc)
            sec_eod = (self._normalize_dt(eod_now) - self._normalize_dt(st.opened_at)).total_seconds()
            eod_lv = None
            try:
                eod_lv = float(osnap.get("last_valid_option_price")) if osnap.get("last_valid_option_price") is not None else None
            except (TypeError, ValueError):
                eod_lv = None
            prem, prem_src, detail = resolve_option_price(
                entry_underlying=entry_u,
                current_underlying=current_underlying,
                option_side=st.option_side,
                entry_premium=st.entry_premium,
                cfg=self.config,
                option_quote=option_quote,
                previous_effective=prev_eff,
                delta_at_entry=st.delta_at_entry,
                strike=st.strike,
                position_symbol=sym or "UNKNOWN",
                is_0dte=is_0dte_eod,
                seconds_since_entry=sec_eod,
                trade_id=str(trade_id or ""),
                stored_last_valid_option_price=eod_lv,
                is_short_premium=bool(osnap.get("is_short_premium")),
            )
        st.last_effective_premium = prem
        if _premium_updates_favorable_hwm(prem_src, detail, is_spread=is_spread) and prem > st.premium_hwm:
            st.premium_hwm = prem
        if prem < st.premium_lwm:
            st.premium_lwm = prem
        extra = {
            "premium_source": prem_src,
            "effective_premium_used": prem,
            "premium_detail": detail,
        }
        return prem, "end_of_day_close", extra

    def complete_eod_close(
        self,
        position_id: str,
        position_symbol: str,
        position_metadata: Dict[str, Any],
        current_underlying: float,
        option_quote: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[float, str, Dict[str, Any]]:
        """
        Resolve EOD exit premium, update HWM/LWM, record session metrics, return diagnostics for logging.
        """
        now = self._normalize_dt(now or datetime.now(timezone.utc))
        prem, reason, extra = self.force_eod_close_premium(
            position_metadata,
            current_underlying,
            option_quote=option_quote,
            position_symbol=position_symbol,
            trade_id=str(position_id or ""),
        )
        osnap = position_metadata.get("option_stealth")
        if not isinstance(osnap, dict):
            return prem, reason, {**extra, "exit_diagnostics": {}}
        block = osnap.get("engine")
        if not isinstance(block, dict):
            return prem, reason, {**extra, "exit_diagnostics": {}}
        st: OptionStealthState = block["state"]
        detail = extra.get("premium_detail") or {}
        prem_src = str(extra.get("premium_source") or "delta_estimate")
        is_spread_eod = st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        )
        if is_spread_eod and st.position_type == OptionPositionType.CREDIT_SPREAD.value:
            base = float(st.entry_credit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (base - prem) / base if base > 0 else 0.0
        elif is_spread_eod:
            base = float(st.entry_debit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (prem - base) / base if base > 0 else 0.0
        else:
            pnl_pct = (prem - st.entry_premium) / st.entry_premium if st.entry_premium > 0 else 0.0
        diag = self._build_exit_diagnostics(
            position_id=position_id,
            position_symbol=position_symbol,
            block=block,
            st=st,
            osnap=osnap,
            exit_premium=prem,
            exit_reason="end_of_day_close",
            now=now,
            prem_detail=detail if isinstance(detail, dict) else {},
            premium_source_at_exit=prem_src,
        )
        self._record_exit("end_of_day_close", pnl_pct, prem_src, diag)
        return prem, reason, {**extra, "exit_diagnostics": diag}


def dev_stealth_wiring_smoke(engine: Optional[TrendlineOptionsStealthEngine] = None) -> None:
    """
    Optional REPL/dev check: exercises register_on_open + one process_position tick per structural type.
    Does not substitute integration tests; safe to no-op if misconfigured.
    """
    eng = engine or TrendlineOptionsStealthEngine()
    now = datetime.now(timezone.utc)
    for label, meta, und, quote in (
        (
            "single_leg_call",
            {},
            100.0,
            {"bid": 1.0, "ask": 1.1, "last": 1.05},
        ),
        (
            "debit_spread",
            {},
            100.0,
            {"bid": 0.40, "ask": 0.44, "last": 0.42},
        ),
        (
            "credit_spread",
            {},
            100.0,
            {"bid": 0.34, "ask": 0.36, "last": 0.35},
        ),
    ):
        pid = f"smoke_{label}"
        eng.register_on_open(
            pid,
            entry_premium_per_contract=1.0,
            underlying_entry=und,
            option_side="call",
            line_geometry="bear",
            strike=100.0,
            delta=0.3,
            trendline_dict={},
            metadata_target=meta,
            underlying_symbol="SMOKE",
            expiration_ymd="20990101",
            position_type=(
                "single_leg_long_call"
                if label == "single_leg_call"
                else ("debit_spread" if label == "debit_spread" else "credit_spread")
            ),
            strategy_name="smoke",
            strategy_type="orb_0dte",
            entry_value=1.0 if label == "single_leg_call" else (0.5 if label == "debit_spread" else 0.4),
            entry_debit=1.0 if label == "single_leg_call" else (0.5 if label == "debit_spread" else None),
            entry_credit=None if label != "credit_spread" else 0.4,
            legs=(
                [
                    {
                        "leg_id": "A",
                        "long_or_short": "long",
                        "option_side": "call",
                        "strike": 99.0,
                        "quantity": 1,
                        "entry_price": 0.6,
                        "delta_at_entry": 0.35,
                    }
                ]
                if label == "single_leg_call"
                else [
                    {
                        "leg_id": "L",
                        "long_or_short": "long",
                        "option_side": "call",
                        "strike": 99.0,
                        "quantity": 1,
                        "entry_price": 0.35,
                        "delta_at_entry": 0.35,
                    },
                    {
                        "leg_id": "S",
                        "long_or_short": "short",
                        "option_side": "call",
                        "strike": 101.0,
                        "quantity": 1,
                        "entry_price": 0.15,
                        "delta_at_entry": 0.25,
                    },
                ]
            ),
            structure_invalidation_enabled=False,
            source_path="dev_smoke",
        )
        out = eng.process_position(
            pid,
            position_symbol="SMOKE",
            position_metadata=meta,
            current_underlying=und + 0.1,
            now=now,
            option_quote=quote,
        )
        log.info("OPTIONS_STEALTH | stage=dev_smoke | label=%s | process_result=%s", label, out is not None)
