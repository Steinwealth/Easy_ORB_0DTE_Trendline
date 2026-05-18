#!/usr/bin/env python3
"""
Easy Trendline 0DTE strategy models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TrendlineDirection(str, Enum):
    """
    Trendline *geometry* (line type), not ORB signal side.

    BULL  = ascending support (ORB low + higher lows): valid trigger is breakdown
            below the line → long PUT.
    BEAR  = descending resistance (ORB high + lower highs): valid trigger is
            breakout above the line → long CALL.

    Do not infer this from SO/0DTE collection side or a fixed long bias; it must
    come from pre-7:30 structure selection.
    """

    BULL = "bull"
    BEAR = "bear"


class TrendlineSetupType(str, Enum):
    """User-facing setup semantics (maps 1:1 to TrendlineDirection geometry)."""

    ASCENDING_SUPPORT = "ascending_support"
    DESCENDING_RESISTANCE = "descending_resistance"

    @classmethod
    def from_direction(cls, direction: "TrendlineDirection") -> "TrendlineSetupType":
        if direction == TrendlineDirection.BULL:
            return cls.ASCENDING_SUPPORT
        return cls.DESCENDING_RESISTANCE


class BreakStatus(str, Enum):
    """Break detection status."""

    NONE = "none"
    DETECTED = "detected"
    REJECTED = "rejected"


class MomentumStatus(str, Enum):
    """Post-break momentum confirmation status."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class TrendlineCandidateState(str, Enum):
    """State machine status for each trendline candidate."""

    WAITING_FOR_BUILD = "waiting_for_build"
    WAITING_FOR_BREAK = "waiting_for_break"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    REVERSAL_WATCH = "reversal_watch"
    READY_TO_EXECUTE = "ready_to_execute"
    EXECUTED = "executed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class TrendlineReasonCode(str, Enum):
    """Canonical reason codes for trendline lifecycle diagnostics."""

    BUILD_FAILED = "build_failed"
    MISSING_ORB_CONTEXT = "missing_orb_context"
    INSUFFICIENT_PRE730_BARS = "insufficient_pre730_bars"
    MICRO_BREAK_REJECTED = "micro_break_rejected"
    FAILED_HOLD = "failed_hold"
    NO_STRUCTURE_HOLD = "no_structure_hold"
    NO_LOCAL_CONTINUATION = "no_local_continuation"
    NO_POST_BREAK_STRUCTURE = "no_post_break_structure"
    POST_CONTINUE_CHOP_GATE = "post_continue_chop_gate"
    MOMENTUM_TOO_WEAK = "momentum_too_weak"
    LATE_CONFIRMATION = "late_confirmation"
    RETRACE_VIOLATION = "retrace_violation"
    SECOND_BREAK_NOT_ALLOWED = "second_break_not_allowed"
    EXPIRED_CUTOFF = "expired_cutoff"
    DEDUPE_BLOCK = "dedupe_block"
    EXECUTED_VALID_CONFIRMATION = "executed_valid_confirmation"
    NO_VALID_TRENDLINE_STRUCTURE = "no_valid_trendline_structure"
    AMBIGUOUS_STRUCTURE = "ambiguous_structure"


@dataclass(frozen=True)
class TrendlineConfig:
    """
    Trendline 0DTE configuration.

    All runtime tuning is loaded from env via ``trendline_config_loader.load_trendline_config_from_env``;
    dataclass defaults match the prior in-code baseline and are logged when used.
    """

    # --- Session / line geometry (defaults; not part of canonical env surface) ---
    build_time_pt: str = "07:30"
    expiration_time_pt: str = "12:30"
    min_break_pct: float = 0.001
    use_atr_break: bool = True
    atr_break_multiplier: float = 0.25
    min_anchor_distance_minutes: int = 3

    # --- Canonical BREAK / QUALITY (env) ---
    break_distance_min: float = 0.0015
    body_ratio_min_strong: float = 0.5
    body_ratio_min_weak: float = 0.35

    # --- Canonical ENTRY PATH (env) ---
    fast_path_enabled: bool = True

    # --- Canonical CONFIRMATION (env) ---
    confirm_seconds: float = 10.0
    require_hold_after_break: bool = True
    require_local_continuation_break: bool = True
    require_post_break_structure: bool = True

    # --- Canonical EXPANSION (env) ---
    expansion_net_move_ratio_min: float = 0.35
    expansion_overlap_max: float = 0.65
    expansion_break_level_return_max: int = 2
    # Impulse / early-entry only: MFE-based ratio + survival window before expansion can reject (see trendline_signal_engine).
    expansion_impulse_mfe_enabled: bool = True
    expansion_impulse_body_strong: float = 1.25
    expansion_impulse_survival_min_bars: int = 2
    expansion_impulse_survival_min_seconds: float = 45.0

    # --- Canonical ENTRY TIMING / LIMITS (env) ---
    max_break_to_confirm_minutes: int = 0
    max_entry_distance_pct: float = 0.005
    no_new_entries_after_pt: str = "11:30"

    # --- Break detector quality flags (defaults; not canonical env) ---
    strong_breakout_distance_mult: float = 1.2
    body_expansion_mult: float = 1.3
    clean_breakout_bypass_momentum: bool = True

    # --- Momentum / chop / hold mechanics (defaults) ---
    confirmation_window_bars: int = 3
    min_followthrough_bars: int = 2
    min_velocity_pct: float = 0.0010
    range_expansion_multiplier: float = 1.15
    min_break_quality_score: float = 0.38
    chop_recent_bars: int = 8
    chop_max_crosses: int = 2
    chop_small_range_vs_orb_ratio: float = 0.20
    hold_bars_after_break: int = 2
    hold_mode: str = "time_based"
    hold_bar_interval: str = "1m"
    post_break_structure_lookback_bars: int = 3
    max_break_to_hold_retrace_pct: float = 0.40
    min_continuation_distance_pct: float = 0.0005
    post_continue_settle_bars: int = 1
    post_continue_chop_box_gate_enabled: bool = True
    post_continue_fast_followthrough_bars: int = 8
    post_continue_fast_extension_pct: float = 0.0009
    post_continue_box_break_buffer_pct: float = 0.0002
    post_continue_chop_min_prior_bars: int = 3
    allow_second_break_attempt: bool = False
    second_break_attempt_max_bars: int = 3

    # --- Logging throttle (default; not canonical env) ---
    confirm_pending_log_sec: float = 120.0
    false_break_gate_log_throttle_sec: float = 45.0
    trendline_funnel_summary_interval_sec: float = 180.0

    # --- Former TrendlineEngineInternals (now env-driven) ---
    continuation_max_bars: int = 8
    continuation_min_bars: int = 2
    pullback_strength_threshold: float = 0.62
    rearm_enabled: bool = True
    rearm_max_checks: int = 2
    rearm_max_minutes: float = 10.0
    rearm_allowed_reasons_csv: str = (
        "no_follow_through,strict_body_ratio,reversal_candle,break_quality,insufficient_move_potential"
    )
    rearm_body_ratio_threshold: float = 0.80
    extreme_break_threshold: float = 0.04
    near_extreme_break_threshold: float = 0.035
    strong_break_threshold: float = 0.025
    weak_break_threshold: float = 0.015
    reversal_reclaim_min_distance: float = 0.01
    reversal_max_bars_since_rejection: int = 5
    reversal_early_reclaim_bars: int = 2
    reversal_confidence_boost: float = 0.05
    regime_choppy_overlap_min: float = 0.55
    regime_trend_directional_min: float = 0.58
    regime_min_avg_range_pct: float = 0.0012
    regime_choppy_body_ratio_min: float = 1.10
    regime_choppy_min_followthrough_bars: int = 2
    impulse_enabled: bool = True
    impulse_min_body_ratio: float = 1.35
    impulse_confirm_next_candle: bool = True
    slow_trend_enabled: bool = True
    slow_trend_min_candles: int = 3
    slow_trend_max_candles: int = 6
    slow_trend_consistency_min: float = 0.67
    slow_trend_cum_break_move_min: float = 0.0018
    early_entry_enabled: bool = True
    early_entry_body_ratio_min: float = 0.8
    early_entry_size_multiplier: float = 0.50
    impulse_break_body_ratio_min: float = 0.70
    retest_enabled: bool = True
    retest_max_checks: int = 2
    retest_max_minutes: float = 5.0
    retest_body_ratio_min: float = 0.60
    min_expected_move_pct: float = 0.0015
    min_expected_move_pct_slow: float = 0.0004
    min_expected_move_pct_impulse: float = 0.0010
    min_expected_move_pct_default: float = 0.0006
    touch_tolerance_pct: float = 0.0012
    high_pressure_touch_count: int = 3
    pressure_score_min: float = 2.0
    min_structure_bars: int = 3
    min_structure_seconds: float = 90.0
    post_break_survival_bars: int = 3
    max_active_minutes: float = 180.0
    missed_win_move_pct: float = 0.005
    bad_entry_max_favorable_pct: float = 0.002
    bad_entry_drawdown_pct: float = 0.003
    min_touch_bar_gap: int = 1
    fast_path_override_abs_floor: float = 0.010
    fast_path_final_abs_floor: float = 0.008
    strong_dist_override_abs_floor: float = 0.008
    triple_weak_ceiling_abs_floor: float = 0.010
    composite_close_pos_commit_min: float = 0.80
    triple_weak_close_pos_max: float = 0.70
    min_drift_displacement: float = 0.003
    require_drift_confirm: bool = False
    reanchor_enabled: bool = True
    reanchor_minutes: int = 20
    reanchor_min_touches: int = 3
    reanchor_lookback_bars: int = 30
    retest_line_buffer_pct: float = 0.0007
    acceptance_min_beyond_closes: int = 2
    acceptance_max_reclaims: int = 2
    rearm_max_structure_violations: int = 2

    # --- Execution quality / direction / entry timing gates ---
    strict_min_break_distance: float = 0.0015
    body_ratio_min_strict: float = 0.50
    direction_override_strong_break_threshold: float = 0.025
    direction_override_strong_body_ratio: float = 0.72
    entry_min_seconds_after_break: float = 0.0
    entry_max_seconds_after_break: float = 0.0
    normal_entry_require_confirmation: bool = True
    fast_path_require_strong_break: bool = True
    fast_path_require_distance_increasing: bool = True
    fast_path_require_momentum_agreement: bool = True
    fast_path_require_no_reclaim: bool = True
    fast_path_min_body_ratio: float = 0.72
    fast_path_min_break_distance: float = 0.0035
    # --- False-break / continuation survival (env-driven via loader) ---
    local_continuation_survival_extra_bars: int = 2
    local_continuation_survival_sec: float = 55.0
    false_break_bypass_min_em_mult: float = 1.0
    fast_path_weak_body_catastrophic_only: bool = True

    # --- Composite entry score + bounce-back (defaults in modules/trendline_entry_defaults.py) ---
    entry_score_min_impulse: float = 0.72
    entry_score_min_continuation: float = 0.58
    entry_score_min_exhaustion: float = 0.52
    score_weight_structure: float = 0.12
    score_weight_distance_increasing: float = 0.10
    score_weight_reclaim_hold: float = 0.12
    score_weight_continuation_slope: float = 0.10
    score_weight_continuation_velocity: float = 0.08
    score_weight_expected_move: float = 0.10
    score_weight_body: float = 0.08
    score_weight_expansion: float = 0.10
    score_weight_followthrough: float = 0.08
    score_penalty_chop: float = 0.14
    score_penalty_opposite_candle: float = 0.18
    score_penalty_reclaim: float = 0.16
    bounceback_reclaim_max_bars: int = 3
    entry_score_stale_seconds: float = 280.0
    entry_em_collapse_ratio: float = 0.62
    entry_survival_sec_mult_impulse: float = 1.0
    entry_survival_sec_mult_drift: float = 2.15
    entry_survival_sec_mult_exhaustion: float = 2.75
    entry_survival_extra_bars_impulse: int = 0
    entry_survival_extra_bars_drift: int = 2
    entry_survival_extra_bars_exhaustion: int = 3


@dataclass(frozen=True)
class TrendlineOptionSelectionConfig:
    """0DTE contract selection knobs (Trendline path only)."""

    delta_min: float = 0.20
    delta_max: float = 0.35
    strike_mode: str = "otm_1_to_2"
    lotto_mode: bool = True
    max_bid_ask_spread_pct: float = 0.40
    min_open_interest: int = 0
    min_volume: int = 0


@dataclass(frozen=True)
class TrendlineAnchor:
    """An anchor point used to define a trendline."""

    ts: datetime
    price: float
    source: str
    candle_index: Optional[int] = None


@dataclass(frozen=True)
class TrendlineDefinition:
    """Projected trendline built from two anchors."""

    symbol: str
    direction: TrendlineDirection
    anchor_one: TrendlineAnchor
    anchor_two: TrendlineAnchor
    slope_per_second: float
    intercept: float
    built_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)

    def value_at(self, ts: datetime) -> float:
        """Return projected line value at a timestamp."""
        return (self.slope_per_second * ts.timestamp()) + self.intercept


@dataclass
class TrendlineCandidate:
    """Pending candidate carried through post-7:30 lifecycle."""

    symbol: str
    direction: TrendlineDirection
    qualified_at: datetime
    priority_score: float = 0.0
    confidence: float = 0.0
    setup_payload: Dict[str, Any] = field(default_factory=dict)
    trendline: Optional[TrendlineDefinition] = None
    state: TrendlineCandidateState = TrendlineCandidateState.WAITING_FOR_BUILD
    state_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    break_event: Optional["TrendlineBreakEvent"] = None
    momentum_confirmation: Optional["TrendlineMomentumConfirmation"] = None
    candidate_id: str = ""
    executed_trade_id: Optional[str] = None
    has_broken_once: bool = False
    first_break_at: Optional[datetime] = None
    break_attempt_count: int = 0
    hold_success_at: Optional[datetime] = None
    continuation_break_at: Optional[datetime] = None
    local_continuation_level: Optional[float] = None
    hold_bars_achieved: int = 0
    hold_end_bar_index: int = 0
    last_hold_duration_seconds: float = 0.0
    continuation_pending: bool = False
    continuation_start_index: int = 0
    continuation_max_bars: int = 8
    continuation_reason: str = ""
    rearm_pending: bool = False
    rearm_origin_reason: str = ""
    rearm_started_at: Optional[datetime] = None
    rearm_checks_done: int = 0
    rearm_break_level: Optional[float] = None
    rearm_break_candle_low: Optional[float] = None
    rearm_break_candle_high: Optional[float] = None
    retest_pending: bool = False
    retest_started_at: Optional[datetime] = None
    retest_break_level: Optional[float] = None
    retest_direction: str = ""
    retest_checks_done: int = 0
    break_bar_index: Optional[int] = None
    mfe_window_logged: Dict[str, bool] = field(default_factory=dict)
    impulse_pending_confirmation: bool = False
    impulse_break_high: Optional[float] = None
    impulse_break_low: Optional[float] = None
    reversal_watch_active: bool = False
    reversal_watch_started_at: Optional[datetime] = None
    reversal_watch_start_index: int = 0
    reversal_original_break_direction: str = ""
    reversal_rejection_high: Optional[float] = None
    reversal_rejection_low: Optional[float] = None
    break_timestamp: Optional[datetime] = None
    # Dedupes TRENDLINE_DECISION_SNAPSHOT within the current process_new_bar pass; reset each bar.
    decision_logged: bool = False
    skip_timestamp: Optional[datetime] = None
    skip_price: Optional[float] = None
    missed_win_early_logged: bool = False
    reanchor_attempted: bool = False
    reanchor_anchor_count: int = 0
    # Deferred entry after strict pre-emit soft-fail (e.g. reversal candle on micro-break path).
    delayed_continuation_armed: bool = False
    delayed_continuation_origin: str = ""
    delayed_continuation_started_at: Optional[datetime] = None
    delayed_continuation_deadline_ts: Optional[datetime] = None
    delayed_continuation_bars_seen: int = 0
    delayed_continuation_break_low: Optional[float] = None
    delayed_continuation_break_high: Optional[float] = None
    delayed_continuation_ref_em_pct: Optional[float] = None


@dataclass(frozen=True)
class TrendlineBreakEvent:
    """A detected trendline break event."""

    symbol: str
    direction: TrendlineDirection
    status: BreakStatus
    candle_ts: datetime
    close_price: float
    trendline_price: float
    break_distance: float
    break_distance_pct: float
    threshold_used: float
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendlineMomentumConfirmation:
    """Momentum verdict for a break event."""

    symbol: str
    direction: TrendlineDirection
    status: MomentumStatus
    confirmed_at: datetime
    bars_used: int
    velocity_pct: float
    range_expansion_ratio: float
    pullback_ratio: float
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendlineTradeSignal:
    """Trade-ready signal emitted by trendline signal engine."""

    symbol: str
    direction: TrendlineDirection
    emitted_at: datetime
    trendline: TrendlineDefinition
    break_event: TrendlineBreakEvent
    momentum_confirmation: TrendlineMomentumConfirmation
    confidence: float
    priority_score: float
    option_side: str  # call or put
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendlineTradeResult:
    """Execution result for a trendline signal."""

    symbol: str
    direction: TrendlineDirection
    success: bool
    executed_at: datetime
    signal_time: datetime
    account_mode: str
    position_id: Optional[str] = None
    order_payload: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class OHLCVBar:
    """Minimal candle representation for trendline engines."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    atr: Optional[float] = None

