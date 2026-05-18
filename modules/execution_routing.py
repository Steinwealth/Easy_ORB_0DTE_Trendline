#!/usr/bin/env python3
"""
Execution routing: backward-compat flags, exit urgency, last-look thresholds.

Minimal env surface: USE_MARKET_ORDERS, ENABLE_SMART_EXECUTION (see get_config_value defaults).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:
    from .config_loader import get_config_value
except ImportError:  # pragma: no cover
    from config_loader import get_config_value


def _truthy(raw: Any) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def smart_execution_enabled() -> bool:
    """
    Smart limit/reprice path is active only when:
    - ENABLE_SMART_EXECUTION is true (default true), and
    - USE_MARKET_ORDERS is false (default true -> legacy market-only).
    """
    if not _truthy(get_config_value("ENABLE_SMART_EXECUTION", "true")):
        return False
    if _truthy(get_config_value("USE_MARKET_ORDERS", "true")):
        return False
    return True


def last_look_max_spread_pct_default() -> float:
    try:
        return float(get_config_value("EXEC_LAST_LOOK_MAX_SPREAD_PCT", "2.5"))
    except (TypeError, ValueError):
        return 2.5


@dataclass(frozen=True)
class OptionsExitPlan:
    exit_urgency: str  # URGENT | MODERATE | PASSIVE
    exit_execution_style: str  # MARKET | AGGRESSIVE_LIMIT_LADDER | MIDPOINT_FIRST_LADDER
    order_type: str  # MARKET | LIMIT
    allow_market_fallback: bool


def _lower(s: Optional[str]) -> str:
    return str(s or "").strip().lower()


def resolve_options_exit_plan(
    exit_reason_override: Optional[str],
    *,
    exit_enum_value: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> OptionsExitPlan:
    """
    Map free-text / stealth exit strings + OptionsExitManager enum to execution style.
    """
    d = details if isinstance(details, dict) else {}
    stealth = _lower(d.get("stealth_exit_reason") or d.get("exit_reason") or "")
    base = _lower(exit_reason_override) or _lower(exit_enum_value) or stealth

    ev = _lower(exit_enum_value)
    if ev in ("fail_safe", "health_emergency", "hard_stop"):
        return OptionsExitPlan(
            exit_urgency="URGENT",
            exit_execution_style="MARKET",
            order_type="MARKET",
            allow_market_fallback=True,
        )

    # Urgent: risk / data / hard failures
    urgent_tokens = (
        "fast_fail",
        "fail_safe",
        "health_emergency",
        "hard_stop",
        "eod",
        "end_of_day",
        "degraded_data",
        "option_force_exit_no_data",
        "degraded_data_no_fallback",
        "degraded_data_outage",
    )
    if any(t in base for t in urgent_tokens):
        return OptionsExitPlan(
            exit_urgency="URGENT",
            exit_execution_style="MARKET",
            order_type="MARKET",
            allow_market_fallback=True,
        )

    # Moderate: structural / stall
    moderate_tokens = (
        "invalidation",
        "structure_invalidation",
        "trendline_structure_exit",
        "no_progress_timeout",
        "adverse_guard",
        "reversal_reclaim",
    )
    if any(t in base for t in moderate_tokens):
        return OptionsExitPlan(
            exit_urgency="MODERATE",
            exit_execution_style="AGGRESSIVE_LIMIT_LADDER",
            order_type="LIMIT",
            allow_market_fallback=True,
        )

    # Passive / profit: time decay, profit locks, runners, trailing-style
    passive_tokens = (
        "time_exit",
        "time_stop",
        "profit",
        "runner",
        "trailing",
        "micro_lock",
        "impulse",
        "reversal_exit",
        "trendline_reversal",
        "max_pnl_drawdown",
        "breakeven",
    )
    if any(t in base for t in passive_tokens):
        return OptionsExitPlan(
            exit_urgency="PASSIVE",
            exit_execution_style="MIDPOINT_FIRST_LADDER",
            order_type="LIMIT",
            allow_market_fallback=True,
        )

    # Default: do not block exits — behave like moderate aggressive limits with fallback.
    return OptionsExitPlan(
        exit_urgency="MODERATE",
        exit_execution_style="AGGRESSIVE_LIMIT_LADDER",
        order_type="LIMIT",
        allow_market_fallback=True,
    )


def map_equity_exit_urgency(exit_reason_value: str) -> OptionsExitPlan:
    """ORB SO stealth ExitReason.value -> exit plan (reuse OptionsExitPlan fields)."""
    r = _lower(exit_reason_value)
    if r in ("stop_loss", "gap_risk", "rapid_exit", "so_allowlist_violation", "manual"):
        return OptionsExitPlan("URGENT", "MARKET", "MARKET", True)
    if r in ("end_of_day_close",):
        return OptionsExitPlan("URGENT", "MARKET", "MARKET", True)
    if r in ("take_profit", "trailing_stop", "scale_out_t1", "scale_out_t2", "breakeven_protection"):
        return OptionsExitPlan("PASSIVE", "MIDPOINT_FIRST_LADDER", "LIMIT", True)
    if r in ("time_exit", "volume_exit", "momentum_exit"):
        return OptionsExitPlan("MODERATE", "AGGRESSIVE_LIMIT_LADDER", "LIMIT", True)
    return OptionsExitPlan("MODERATE", "AGGRESSIVE_LIMIT_LADDER", "LIMIT", True)


def last_look_option_spread_ok(bid: float, ask: float, mid: float) -> Tuple[bool, float]:
    if mid <= 0 or bid <= 0 or ask <= 0 or ask < bid:
        return False, 999.0
    sp = (ask - bid) / mid * 100.0
    return sp <= last_look_max_spread_pct_default(), sp
