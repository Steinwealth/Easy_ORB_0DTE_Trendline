#!/usr/bin/env python3
"""
Time-of-day and momentum-aware execution profiles (internal defaults, minimal env).

Opening impulse prioritizes fill capture over midpoint perfection.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


@dataclass(frozen=True)
class ExecutionTimingProfile:
    name: str
    max_reprice: int
    poll_sec: float
    max_wait_per_attempt_sec: float
    max_total_ladder_sec: float
    spread_tolerance_mult: float
    buy_first_step_aggression: float  # 0=mid-leaning, 1=near-ask immediately
    opening_impulse_mode: bool


_PROFILES = {
    "OPENING_IMPULSE": ExecutionTimingProfile(
        name="OPENING_IMPULSE",
        max_reprice=2,
        poll_sec=0.35,
        max_wait_per_attempt_sec=1.1,
        max_total_ladder_sec=3.5,
        spread_tolerance_mult=1.65,
        buy_first_step_aggression=0.72,
        opening_impulse_mode=True,
    ),
    "MID_MORNING_NORMAL": ExecutionTimingProfile(
        name="MID_MORNING_NORMAL",
        max_reprice=3,
        poll_sec=0.5,
        max_wait_per_attempt_sec=1.8,
        max_total_ladder_sec=6.0,
        spread_tolerance_mult=1.0,
        buy_first_step_aggression=0.45,
        opening_impulse_mode=False,
    ),
    "LOW_LIQUIDITY_DEFENSIVE": ExecutionTimingProfile(
        name="LOW_LIQUIDITY_DEFENSIVE",
        max_reprice=3,
        poll_sec=0.55,
        max_wait_per_attempt_sec=2.0,
        max_total_ladder_sec=7.0,
        spread_tolerance_mult=0.92,
        buy_first_step_aggression=0.38,
        opening_impulse_mode=False,
    ),
}


def _now_pt() -> datetime:
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo("America/Los_Angeles"))
    return datetime.now()


def resolve_time_of_day_profile(now_pt: Optional[datetime] = None) -> ExecutionTimingProfile:
    """
    Pacific session heuristics (ORB 7:30 PT execution window).
    06:25–08:15 PT: opening impulse
    08:15–11:30 PT: mid-morning
    else: defensive
    """
    t = now_pt or _now_pt()
    hm = t.hour * 60 + t.minute
    if (6 * 60 + 25) <= hm < (8 * 60 + 15):
        return _PROFILES["OPENING_IMPULSE"]
    if (8 * 60 + 15) <= hm < (11 * 60 + 30):
        return _PROFILES["MID_MORNING_NORMAL"]
    return _PROFILES["LOW_LIQUIDITY_DEFENSIVE"]


def compute_aggression_level(
    context: Optional[Dict[str, Any]],
    profile: ExecutionTimingProfile,
) -> int:
    """
    0=base profile, 1–3 = escalating aggression (shorter waits, fewer passive steps).
    """
    if not context:
        return 1 if profile.opening_impulse_mode else 0
    level = 0
    try:
        secs = float(context.get("seconds_from_signal", 9999) or 9999)
    except (TypeError, ValueError):
        secs = 9999.0
    if secs <= 90:
        level += 1
    if secs <= 35:
        level += 1
    try:
        brk = float(context.get("breakout_score", 0) or 0)
    except (TypeError, ValueError):
        brk = 0.0
    if brk >= 0.68:
        level += 1
    try:
        vel = float(context.get("velocity", 0) or 0)
    except (TypeError, ValueError):
        vel = 0.0
    try:
        cont = float(context.get("continuation_distance", 0) or 0)
    except (TypeError, ValueError):
        cont = 0.0
    if vel >= 0.0012 or cont >= 0.0035:
        level += 1
    try:
        conf = float(context.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 0.82:
        level += 1
    if context.get("high_confidence_impulse") is True:
        level += 1
    if profile.opening_impulse_mode:
        level += 1
    return max(0, min(3, level))


def apply_aggression_to_profile(
    profile: ExecutionTimingProfile,
    aggression_level: int,
) -> ExecutionTimingProfile:
    """Tighten timing and skew limits more aggressive at higher levels."""
    lvl = max(0, min(3, int(aggression_level)))
    if lvl <= 0:
        return profile
    factor = 1.0 - 0.18 * lvl
    return replace(
        profile,
        max_reprice=max(1, profile.max_reprice - lvl),
        poll_sec=max(0.25, profile.poll_sec * factor),
        max_wait_per_attempt_sec=max(0.7, profile.max_wait_per_attempt_sec * factor),
        max_total_ladder_sec=max(2.5, profile.max_total_ladder_sec * factor),
        buy_first_step_aggression=min(1.0, profile.buy_first_step_aggression + 0.12 * lvl),
        spread_tolerance_mult=profile.spread_tolerance_mult * (1.0 + 0.08 * lvl),
        opening_impulse_mode=profile.opening_impulse_mode or lvl >= 2,
    )


def resolve_execution_profile(
    context: Optional[Dict[str, Any]] = None,
    *,
    now_pt: Optional[datetime] = None,
) -> Tuple[ExecutionTimingProfile, int, bool]:
    """Returns (effective_profile, aggression_level, opening_impulse_mode)."""
    base = resolve_time_of_day_profile(now_pt)
    lvl = compute_aggression_level(context, base)
    effective = apply_aggression_to_profile(base, lvl)
    opening = bool(effective.opening_impulse_mode or lvl >= 2)
    return effective, lvl, opening
