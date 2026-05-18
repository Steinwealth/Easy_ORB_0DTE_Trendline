#!/usr/bin/env python3
"""
Post-break momentum confirmation engine.
"""

from __future__ import annotations

from typing import List, Optional

from .trendline_models import (
    MomentumStatus,
    OHLCVBar,
    TrendlineBreakEvent,
    TrendlineConfig,
    TrendlineDirection,
    TrendlineMomentumConfirmation,
    TrendlineReasonCode,
)


class MomentumConfirmationEngine:
    """Confirm continuation and filter fakeouts/stalls after a break."""

    def __init__(self, config: Optional[TrendlineConfig] = None) -> None:
        self.config = config or TrendlineConfig()

    def confirm(
        self,
        direction: TrendlineDirection,
        break_event: TrendlineBreakEvent,
        post_break_bars: List[OHLCVBar],
    ) -> TrendlineMomentumConfirmation:
        """
        Confirm momentum using follow-through, velocity, range expansion, retrace checks.
        """
        if not post_break_bars:
            return self._result(
                break_event,
                MomentumStatus.PENDING,
                bars_used=0,
                velocity_pct=0.0,
                range_ratio=1.0,
                pullback_ratio=0.0,
                reason="awaiting_confirmation_bars",
            )

        breakout_meta = break_event.metadata or {}
        breakout_is_clean = bool(breakout_meta.get("breakout_is_clean"))
        strong_breakout = bool(breakout_meta.get("strong_breakout"))
        bypass_default = bool(getattr(self.config, "clean_breakout_bypass_momentum", True))
        bypass_momentum = str(
            breakout_meta.get("clean_breakout_bypass_momentum", str(bypass_default).lower())
        ).strip().lower() in {"1", "true", "yes", "on"}

        window = post_break_bars[: max(1, self.config.confirmation_window_bars)]
        entry_ref = break_event.close_price
        first_range = max(window[0].high - window[0].low, 1e-9)
        last_close = window[-1].close

        if direction == TrendlineDirection.BULL:
            velocity = (entry_ref - last_close) / entry_ref if entry_ref > 0 else 0.0
            favorable_bars = sum(1 for b in window if b.close < b.open)
            peak_favor = min(b.low for b in window)
            retrace = max(b.high for b in window) - break_event.trendline_price
            pullback_ratio = retrace / abs(break_event.break_distance) if break_event.break_distance != 0 else 999.0
        else:
            velocity = (last_close - entry_ref) / entry_ref if entry_ref > 0 else 0.0
            favorable_bars = sum(1 for b in window if b.close > b.open)
            peak_favor = max(b.high for b in window)
            retrace = break_event.trendline_price - min(b.low for b in window)
            pullback_ratio = retrace / abs(break_event.break_distance) if break_event.break_distance != 0 else 999.0

        avg_range = sum((b.high - b.low) for b in window) / len(window)
        range_ratio = avg_range / first_range if first_range > 0 else 1.0

        followthrough_score = min(1.0, favorable_bars / max(1, self.config.min_followthrough_bars))
        velocity_score = min(1.0, max(0.0, velocity / max(self.config.min_velocity_pct, 1e-9)))
        range_score = min(1.0, max(0.0, range_ratio / max(self.config.range_expansion_multiplier, 1.0)))

        # Fast path for responsive 0DTE handling: if break quality is already clean/strong,
        # do not block on strict momentum gates.
        if (breakout_is_clean or strong_breakout) and bypass_momentum:
            break_quality_score = (velocity_score * 0.4) + (range_score * 0.3) + (followthrough_score * 0.3)
            return self._result(
                break_event,
                MomentumStatus.CONFIRMED,
                bars_used=len(window),
                velocity_pct=velocity,
                range_ratio=range_ratio,
                pullback_ratio=pullback_ratio,
                reason=TrendlineReasonCode.EXECUTED_VALID_CONFIRMATION.value,
                extra={
                    "followthrough_score": followthrough_score,
                    "velocity_score": velocity_score,
                    "range_expansion_score": range_score,
                    "break_quality_score": break_quality_score,
                    "clean_breakout_bypass": True,
                    "strong_breakout": strong_breakout,
                    "clean_breakout_bypass_momentum": True,
                },
            )

        if favorable_bars < self.config.min_followthrough_bars:
            return self._result(
                break_event,
                MomentumStatus.FAILED,
                bars_used=len(window),
                velocity_pct=velocity,
                range_ratio=range_ratio,
                pullback_ratio=pullback_ratio,
                reason=TrendlineReasonCode.MOMENTUM_TOO_WEAK.value,
                extra={
                    "failure_component": "followthrough",
                    "followthrough_score": followthrough_score,
                    "velocity_score": velocity_score,
                    "range_expansion_score": range_score,
                },
            )

        if velocity < self.config.min_velocity_pct:
            return self._result(
                break_event,
                MomentumStatus.FAILED,
                bars_used=len(window),
                velocity_pct=velocity,
                range_ratio=range_ratio,
                pullback_ratio=pullback_ratio,
                reason=TrendlineReasonCode.MOMENTUM_TOO_WEAK.value,
                extra={
                    "failure_component": "velocity",
                    "followthrough_score": followthrough_score,
                    "velocity_score": velocity_score,
                    "range_expansion_score": range_score,
                },
            )

        if range_ratio < self.config.range_expansion_multiplier:
            return self._result(
                break_event,
                MomentumStatus.FAILED,
                bars_used=len(window),
                velocity_pct=velocity,
                range_ratio=range_ratio,
                pullback_ratio=pullback_ratio,
                reason=TrendlineReasonCode.MOMENTUM_TOO_WEAK.value,
                extra={
                    "failure_component": "range_expansion",
                    "followthrough_score": followthrough_score,
                    "velocity_score": velocity_score,
                    "range_expansion_score": range_score,
                },
            )

        if pullback_ratio > 1.2:
            return self._result(
                break_event,
                MomentumStatus.FAILED,
                bars_used=len(window),
                velocity_pct=velocity,
                range_ratio=range_ratio,
                pullback_ratio=pullback_ratio,
                reason=TrendlineReasonCode.RETRACE_VIOLATION.value,
                extra={
                    "failure_component": "retrace",
                    "followthrough_score": followthrough_score,
                    "velocity_score": velocity_score,
                    "range_expansion_score": range_score,
                },
            )

        break_quality_score = (velocity_score * 0.4) + (range_score * 0.3) + (followthrough_score * 0.3)
        return self._result(
            break_event,
            MomentumStatus.CONFIRMED,
            bars_used=len(window),
            velocity_pct=velocity,
            range_ratio=range_ratio,
            pullback_ratio=pullback_ratio,
            reason=TrendlineReasonCode.EXECUTED_VALID_CONFIRMATION.value,
            extra={
                "followthrough_score": followthrough_score,
                "velocity_score": velocity_score,
                "range_expansion_score": range_score,
                "break_quality_score": break_quality_score,
            },
        )

    @staticmethod
    def _result(
        break_event: TrendlineBreakEvent,
        status: MomentumStatus,
        bars_used: int,
        velocity_pct: float,
        range_ratio: float,
        pullback_ratio: float,
        reason: str,
        extra: Optional[dict] = None,
    ) -> TrendlineMomentumConfirmation:
        from datetime import datetime, timezone

        return TrendlineMomentumConfirmation(
            symbol=break_event.symbol,
            direction=break_event.direction,
            status=status,
            confirmed_at=datetime.now(timezone.utc),
            bars_used=bars_used,
            velocity_pct=velocity_pct,
            range_expansion_ratio=range_ratio,
            pullback_ratio=pullback_ratio,
            reason=reason,
            metadata=extra or {},
        )

