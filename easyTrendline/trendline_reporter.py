#!/usr/bin/env python3
"""
End-of-day reporting helpers for Easy Trendline strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from .trendline_account_manager import TrendlinePosition
from .trendline_models import TrendlineCandidate, TrendlineCandidateState


@dataclass(frozen=True)
class TrendlineEODReport:
    """Normalized daily summary for trendline strategy."""

    date_utc: str
    total_candidates: int
    trendlines_built: int
    breaks_detected: int
    momentum_confirmations: int
    executed_trades: int
    invalidated_setups: int
    expired_setups: int
    win_rate: float
    realized_pnl: float
    best_trade: float
    worst_trade: float
    avg_minutes_build_to_break: float
    avg_minutes_break_to_confirmation: float
    pct_candidates_never_broke: float
    metadata: Dict[str, object]


class TrendlineReporter:
    """Builds reporting objects from candidate state + closed positions."""

    def build_eod_report(
        self,
        candidates: Iterable[TrendlineCandidate],
        closed_positions: Iterable[TrendlinePosition],
    ) -> TrendlineEODReport:
        cands = list(candidates)
        closed = list(closed_positions)

        trendlines_built = sum(1 for c in cands if c.trendline is not None)
        breaks_detected = sum(1 for c in cands if c.break_event is not None)
        momentum_confirmations = sum(
            1 for c in cands if c.momentum_confirmation and c.momentum_confirmation.status.value == "confirmed"
        )
        never_broke = sum(1 for c in cands if c.break_event is None)
        executed = sum(1 for c in cands if c.state == TrendlineCandidateState.EXECUTED)
        invalidated = sum(1 for c in cands if c.state == TrendlineCandidateState.INVALIDATED)
        expired = sum(1 for c in cands if c.state == TrendlineCandidateState.EXPIRED)

        realized = sum(p.realized_pnl for p in closed)
        wins = [p for p in closed if p.realized_pnl > 0]
        win_rate = (len(wins) / len(closed)) if closed else 0.0
        best_trade = max((p.realized_pnl for p in closed), default=0.0)
        worst_trade = min((p.realized_pnl for p in closed), default=0.0)

        avg_build_to_break = self._avg_minutes_build_to_break(cands)
        avg_break_to_confirm = self._avg_minutes_break_to_confirm(cands)
        avg_730_to_first_break = self._avg_minutes_730_to_first_break(cands)
        avg_first_break_to_hold = self._avg_minutes_first_break_to_hold(cands)
        avg_hold_to_execution = self._avg_minutes_hold_to_execution(cands)
        build_failures = sum(1 for c in cands if c.state_reason in {"build_failed", "missing_orb_context", "insufficient_pre730_bars"})
        first_break_detected = sum(1 for c in cands if c.first_break_at is not None)
        first_break_failed = sum(1 for c in cands if c.first_break_at is not None and c.state == TrendlineCandidateState.INVALIDATED)
        hold_success = sum(1 for c in cands if c.hold_success_at is not None)
        continuation_breaks = sum(1 for c in cands if c.continuation_break_at is not None)
        structure_accepted = sum(1 for c in cands if c.state in {TrendlineCandidateState.READY_TO_EXECUTE, TrendlineCandidateState.EXECUTED})
        dedupe_blocked = sum(1 for c in cands if c.state_reason == "dedupe_block")

        return TrendlineEODReport(
            date_utc=datetime.utcnow().strftime("%Y-%m-%d"),
            total_candidates=len(cands),
            trendlines_built=trendlines_built,
            breaks_detected=breaks_detected,
            momentum_confirmations=momentum_confirmations,
            executed_trades=executed,
            invalidated_setups=invalidated,
            expired_setups=expired,
            win_rate=win_rate,
            realized_pnl=realized,
            best_trade=best_trade,
            worst_trade=worst_trade,
            avg_minutes_build_to_break=avg_build_to_break,
            avg_minutes_break_to_confirmation=avg_break_to_confirm,
            pct_candidates_never_broke=((never_broke / len(cands)) * 100.0 if cands else 0.0),
            metadata={
                "closed_trades": len(closed),
                "wins": len(wins),
                "losses": sum(1 for p in closed if p.realized_pnl < 0),
                "never_broke": never_broke,
                "build_failures": build_failures,
                "first_breaks": first_break_detected,
                "first_breaks_failed": first_break_failed,
                "hold_success": hold_success,
                "continuation_breaks": continuation_breaks,
                "structure_accepted": structure_accepted,
                "dedupe_blocked": dedupe_blocked,
                "avg_minutes_730_to_first_break": avg_730_to_first_break,
                "avg_minutes_first_break_to_hold_success": avg_first_break_to_hold,
                "avg_minutes_hold_success_to_execution": avg_hold_to_execution,
            },
        )

    @staticmethod
    def _avg_minutes_build_to_break(cands: List[TrendlineCandidate]) -> float:
        values: List[float] = []
        for c in cands:
            if c.trendline and c.break_event:
                delta = c.break_event.candle_ts - c.trendline.built_at
                values.append(max(0.0, delta.total_seconds() / 60.0))
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _avg_minutes_break_to_confirm(cands: List[TrendlineCandidate]) -> float:
        values: List[float] = []
        for c in cands:
            if c.break_event and c.momentum_confirmation:
                delta = c.momentum_confirmation.confirmed_at - c.break_event.candle_ts
                values.append(max(0.0, delta.total_seconds() / 60.0))
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _avg_minutes_730_to_first_break(cands: List[TrendlineCandidate]) -> float:
        values: List[float] = []
        for c in cands:
            if c.first_break_at and c.qualified_at:
                delta = c.first_break_at - c.qualified_at
                values.append(max(0.0, delta.total_seconds() / 60.0))
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _avg_minutes_first_break_to_hold(cands: List[TrendlineCandidate]) -> float:
        values: List[float] = []
        for c in cands:
            if c.first_break_at and c.hold_success_at:
                delta = c.hold_success_at - c.first_break_at
                values.append(max(0.0, delta.total_seconds() / 60.0))
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _avg_minutes_hold_to_execution(cands: List[TrendlineCandidate]) -> float:
        values: List[float] = []
        for c in cands:
            if c.hold_success_at and c.updated_at and c.state == TrendlineCandidateState.EXECUTED:
                delta = c.updated_at - c.hold_success_at
                values.append(max(0.0, delta.total_seconds() / 60.0))
        return sum(values) / len(values) if values else 0.0

