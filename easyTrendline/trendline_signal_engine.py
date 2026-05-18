#!/usr/bin/env python3
"""
Main orchestration engine for Easy Trendline 0DTE signal lifecycle.

FINAL TRENDLINE FLOW:

1. Selector builds trendline (structure-first)
2. Engine monitors price action
3. Break detected
4. Survival window allows structure to form
5. Retest OR continuation triggers entry
6. Immediate break only for strong impulse
7. Execution → stealth management
"""

from __future__ import annotations

import logging
import time as time_module
from statistics import mean
from datetime import datetime, timedelta, time, timezone
from typing import Any, Dict, Iterable, List, NamedTuple, Optional
from zoneinfo import ZoneInfo


class _HoldEval(NamedTuple):
    ok: bool
    reason: str
    pending: bool
    hold_end_index: int
    hold_duration_seconds: float

from .break_detector import TrendlineBreakDetector
from .momentum_confirm import MomentumConfirmationEngine
from .trendline_builder import OrbTestFailureClassification, TrendlineBuilder
from .trendline_intent_adapter import build_execution_intent_from_trendline_signal
from .trendline_models import (
    BreakStatus,
    MomentumStatus,
    OHLCVBar,
    TrendlineCandidate,
    TrendlineCandidateState,
    TrendlineConfig,
    TrendlineBreakEvent,
    TrendlineDefinition,
    TrendlineDirection,
    TrendlineMomentumConfirmation,
    TrendlineReasonCode,
    TrendlineSetupType,
    TrendlineTradeSignal,
)


# Minimal internal knobs (prefer over new env scatter for archetype tuning).
_BREAK_ARCHETYPE_EXHAUSTION_BD_MULT = 0.26
_BREAK_ARCHETYPE_MICRO_FLOOR_SCALE = 2.6
_REANCHOR_NEAR_LINE_FREEZE_FRAC = 0.00048
_REANCHOR_ERASURE_SHIFT_FRAC = 0.0001
_DELAYED_CONTINUATION_TTL_MIN = 12.0
_DELAYED_CONTINUATION_MAX_BARS = 8
_DELAYED_EXTENSION_REL = 6.5e-5
_DELAYED_EXPECTED_MOVE_BOOST_MULT = 1.08
_STRUCTURE_FRAGILITY_TOUCH_COUNT = 8

# Canonical entry archetypes (stored on break_event.metadata["break_archetype"]).
ENTRY_ARCHETYPE_IMPULSE = "IMPULSE_BREAK"
ENTRY_ARCHETYPE_DRIFT = "CONTINUATION_DRIFT"
ENTRY_ARCHETYPE_EXHAUSTION = "EXHAUSTION_REVERSAL"

log = logging.getLogger(__name__)


class TrendlineSignalEngine:
    """
    State-machine driven trendline signal engine.

    Lifecycle:
      WAITING_FOR_BUILD -> WAITING_FOR_BREAK -> WAITING_FOR_CONFIRMATION
      -> READY_TO_EXECUTE -> EXECUTED / INVALIDATED / EXPIRED
    """

    def __init__(self, config: Optional[TrendlineConfig] = None) -> None:
        self.config = config or TrendlineConfig()
        self.builder = TrendlineBuilder(self.config)
        self.break_detector = TrendlineBreakDetector(self.config)
        self.momentum_engine = MomentumConfirmationEngine(self.config)
        self._tz_pt = ZoneInfo("America/Los_Angeles")

        self._candidates: Dict[str, TrendlineCandidate] = {}
        self._bar_cache: Dict[str, List[OHLCVBar]] = {}
        self._emitted_candidate_ids: set[str] = set()
        self._confirm_pending_last_log_ts: Dict[str, float] = {}
        self._confirm_pending_log_sec = max(30.0, float(self.config.confirm_pending_log_sec))
        self._confirm_seconds = min(30.0, max(1.0, float(self.config.confirm_seconds)))

        self._break_distance_min = max(1e-9, float(self.config.break_distance_min or 0.0015))
        c = self.config
        self._fast_path_override_min_bd = max(float(c.fast_path_override_abs_floor), self._break_distance_min)
        self._fast_path_final_min_bd = max(float(c.fast_path_final_abs_floor), self._break_distance_min)
        self._strong_dist_override_min_bd = max(float(c.strong_dist_override_abs_floor), self._break_distance_min)
        self._triple_weak_bd_ceiling = max(float(c.triple_weak_ceiling_abs_floor), self._break_distance_min)

        self._continuation_max_bars = max(1, int(c.continuation_max_bars))
        self._continuation_min_bars = max(1, int(c.continuation_min_bars))
        self._pullback_strength_threshold = max(0.0, min(1.0, float(c.pullback_strength_threshold)))

        self._rearm_enabled = bool(c.rearm_enabled)
        self._rearm_max_checks = max(1, int(c.rearm_max_checks))
        self._rearm_max_minutes = max(1.0, float(c.rearm_max_minutes))
        allowed_reasons_raw = str(c.rearm_allowed_reasons_csv or "no_follow_through").strip()
        self._rearm_allowed_reasons = {
            s.strip().lower() for s in allowed_reasons_raw.split(",") if s and s.strip()
        } or {"no_follow_through"}
        self._rearm_disallowed_reasons = {
            "large_opposite_candle",
            "anti_chop",
            "lost_breakout_level",
            "weak_break",
            "micro_break",
        }
        self._rearm_body_ratio_threshold = float(c.rearm_body_ratio_threshold)

        self._extreme_break_threshold = float(c.extreme_break_threshold)
        self._near_extreme_break_threshold = float(c.near_extreme_break_threshold)
        self._strong_break_threshold = float(c.strong_break_threshold)
        self._weak_break_threshold = float(c.weak_break_threshold)
        self._reversal_reclaim_min_distance = float(c.reversal_reclaim_min_distance)
        self._reversal_max_bars_since_rejection = max(1, int(c.reversal_max_bars_since_rejection))
        self._reversal_early_reclaim_bars = max(1, int(c.reversal_early_reclaim_bars))
        self._reversal_confidence_boost = max(0.0, float(c.reversal_confidence_boost))

        self._session_summary_logged = False
        self._decision_stats: Dict[str, int] = {
            "break_detected": 0,
            "entry_emitted": 0,
            "filtered_small_break": 0,
            "filtered_weak_break": 0,
            "filtered_anti_chop": 0,
            "missed_opportunity": 0,
            "executed": 0,
        }
        self._entry_tracking: Dict[str, Dict[str, float]] = {}
        self._market_regime = "TRENDING"
        self._market_regime_metrics: Dict[str, float] = {}
        self._regime_choppy_overlap_min = float(c.regime_choppy_overlap_min)
        self._regime_trend_directional_min = float(c.regime_trend_directional_min)
        self._regime_min_avg_range_pct = float(c.regime_min_avg_range_pct)
        self._choppy_body_ratio_min = float(c.regime_choppy_body_ratio_min)
        self._choppy_min_followthrough_bars = max(1, int(c.regime_choppy_min_followthrough_bars))

        self._impulse_enabled = bool(c.impulse_enabled)
        self._impulse_min_body_ratio = float(c.impulse_min_body_ratio)
        self._impulse_confirm_next_candle = bool(c.impulse_confirm_next_candle)
        self._slow_trend_enabled = bool(c.slow_trend_enabled)
        self._slow_trend_min_candles = max(3, int(c.slow_trend_min_candles))
        self._slow_trend_max_candles = max(self._slow_trend_min_candles, int(c.slow_trend_max_candles))
        self._slow_trend_consistency_min = float(c.slow_trend_consistency_min)
        self._slow_trend_cum_break_move_min = float(c.slow_trend_cum_break_move_min)

        self._early_entry_enabled = bool(c.early_entry_enabled)
        self._early_entry_body_ratio_min = float(c.early_entry_body_ratio_min)
        self._early_entry_size_multiplier = max(
            0.1, min(1.0, float(c.early_entry_size_multiplier))
        )

        self._impulse_break_body_ratio_min = float(c.impulse_break_body_ratio_min)
        self._retest_enabled = bool(c.retest_enabled)
        self._retest_max_checks = max(1, int(c.retest_max_checks))
        self._retest_max_minutes = max(1.0, float(c.retest_max_minutes))
        self._retest_body_ratio_min = float(c.retest_body_ratio_min)
        self._min_expected_move_pct = float(c.min_expected_move_pct)
        self._min_expected_move_pct_slow = float(c.min_expected_move_pct_slow)
        self._min_expected_move_pct_impulse = float(c.min_expected_move_pct_impulse)
        self._min_expected_move_pct_default = float(c.min_expected_move_pct_default)
        self._touch_tolerance_pct = max(1e-6, float(c.touch_tolerance_pct))
        self._high_pressure_touch_count = max(1, int(c.high_pressure_touch_count))
        self._pressure_score_min = float(c.pressure_score_min)
        self._min_structure_bars = max(0, int(c.min_structure_bars))
        self._min_structure_seconds = max(0.0, float(c.min_structure_seconds))
        self._reanchor_enabled = bool(c.reanchor_enabled)
        self._reanchor_minutes = max(5, int(c.reanchor_minutes))
        self._reanchor_min_touches = max(2, int(c.reanchor_min_touches))
        self._reanchor_lookback_bars = max(8, int(c.reanchor_lookback_bars))
        self._retest_line_buffer_pct = max(1e-6, float(c.retest_line_buffer_pct))
        self._acceptance_min_beyond_closes = max(1, int(c.acceptance_min_beyond_closes))
        self._acceptance_max_reclaims = max(1, int(c.acceptance_max_reclaims))
        self._rearm_max_structure_violations = max(0, int(c.rearm_max_structure_violations))

        self._body_ratio_min_strong = max(1e-6, float(self.config.body_ratio_min_strong or 0.5))
        self._body_ratio_min_weak = max(1e-6, float(self.config.body_ratio_min_weak or 0.35))
        self._fast_path_override_body_min = max(self._body_ratio_min_weak, self._body_ratio_min_strong * 0.8)

        self._post_break_survival_bars = max(0, int(c.post_break_survival_bars))
        self._local_continuation_survival_extra_bars = max(0, int(c.local_continuation_survival_extra_bars))
        self._local_continuation_survival_sec = max(0.0, float(c.local_continuation_survival_sec))
        self._false_break_bypass_min_em_mult = max(0.5, float(c.false_break_bypass_min_em_mult))
        self._fast_path_weak_body_catastrophic_only = bool(c.fast_path_weak_body_catastrophic_only)
        self._false_break_gate_log_throttle_sec = max(
            15.0, float(getattr(c, "false_break_gate_log_throttle_sec", 45.0))
        )
        self._false_break_log_throttle_bucket: Dict[str, int] = {}
        self._false_break_survival_armed_trade_ids: set[str] = set()
        self._false_break_terminal_logged: set[str] = set()
        self._funnel_counts: Dict[str, int] = {
            "candidates_seen": 0,
            "break_detected": 0,
            "structure_accepted": 0,
            "pre_execute_passed": 0,
            "selector_reached": 0,
            "submitted": 0,
            "durable_confirmed": 0,
            "blocked_by_false_break": 0,
            "blocked_by_continuation": 0,
            "blocked_by_quality": 0,
        }
        self._funnel_last_log_ts = 0.0
        self._funnel_interval_sec = max(60.0, float(getattr(c, "trendline_funnel_summary_interval_sec", 180.0)))
        self._max_active_minutes = max(1.0, float(c.max_active_minutes))
        self._missed_win_move_pct = float(c.missed_win_move_pct)
        self._bad_entry_max_favorable_pct = float(c.bad_entry_max_favorable_pct)
        self._bad_entry_drawdown_pct = float(c.bad_entry_drawdown_pct)
        self._emit_skip_watch: Dict[str, Dict[str, Any]] = {}
        self._post_break_no_emit_diag_ts: Dict[str, float] = {}
        self._min_touch_bar_gap = max(1, int(c.min_touch_bar_gap))
        self._consecutive_skips_session = 0
        self._skip_streak_alert_armed = True
        self._log_trendline_config_summary()

    def _log_trendline_config_summary(self) -> None:
        fp_on = bool(self.config.fast_path_enabled)
        log.warning(
            "TRENDLINE_CONFIG_SUMMARY | break_distance_min=%.6f | body_ratio_strong=%.4f | body_ratio_weak=%.4f | "
            "confirm_seconds=%.1f | fast_path_enabled=%s | expansion_net_move_ratio_min=%.4f | expansion_overlap_max=%.4f | "
            "fast_path_override_bd=%.6f | fast_path_final_bd=%.6f | require_hold=%s | require_local_cont=%s | "
            "require_post_structure=%s | max_break_to_confirm_min=%d | max_entry_distance_pct=%.5f | reanchor_enabled=%s | reanchor_minutes=%d",
            float(self._break_distance_min),
            float(self._body_ratio_min_strong),
            float(self._body_ratio_min_weak),
            float(self._confirm_seconds),
            str(fp_on).lower(),
            float(self.config.expansion_net_move_ratio_min),
            float(self.config.expansion_overlap_max),
            float(self._fast_path_override_min_bd),
            float(self._fast_path_final_min_bd),
            str(self.config.require_hold_after_break).lower(),
            str(self.config.require_local_continuation_break).lower(),
            str(self.config.require_post_break_structure).lower(),
            int(self.config.max_break_to_confirm_minutes),
            float(self.config.max_entry_distance_pct),
            str(bool(self._reanchor_enabled)).lower(),
            int(self._reanchor_minutes),
        )

    def _trade_id(self, candidate: Optional[TrendlineCandidate]) -> str:
        if not candidate:
            return "unknown_na"
        ts = None
        if candidate.break_event and candidate.break_event.candle_ts:
            ts = self._normalize_dt(candidate.break_event.candle_ts).strftime("%Y%m%dT%H%M%S")
        if not ts:
            ts = "na"
        return f"{candidate.symbol}_{ts}"

    @property
    def candidates(self) -> Dict[str, TrendlineCandidate]:
        return self._candidates

    def _log_trendline_flow_stage(self, candidate: TrendlineCandidate, stage: str) -> None:
        log.info(
            "TRENDLINE_FLOW_STAGE | symbol=%s | trade_id=%s | stage=%s",
            candidate.symbol,
            self._trade_id(candidate),
            stage,
        )

    def _in_survival_window(self, candidate: TrendlineCandidate, current_bar_index: int) -> bool:
        bbi = getattr(candidate, "break_bar_index", None)
        if bbi is None:
            return False
        w = int(self._post_break_survival_bars)
        if w <= 0:
            return False
        return (int(current_bar_index) - int(bbi)) < w

    def _in_post_break_survival_window(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> bool:
        if not bars:
            return False
        return self._in_survival_window(candidate, max(0, len(bars) - 1))

    def _survival_blocks_negative_verdict(self, candidate: TrendlineCandidate) -> bool:
        fb = self._bar_cache.get(candidate.symbol, [])
        if not fb:
            return False
        cur_idx = max(0, len(fb) - 1)
        if self._in_survival_window(candidate, cur_idx):
            self._log_trendline_flow_stage(candidate, "survival_window_blocked_invalidation")
            return True
        return False

    def _infer_entry_path(self, candidate: TrendlineCandidate) -> str:
        sr = str(candidate.state_reason or "").lower()
        meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        if meta.get("retest_mode") or "retest" in sr:
            return "retest"
        if "continuation" in sr:
            return "continuation"
        return "immediate_break"

    def _compute_break_quality_metrics(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        *,
        post_break: Optional[List[OHLCVBar]] = None,
        distance_hint: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if not candidate.break_event or not bars:
            return {
                "break_distance_pct": 0.0, "body_ratio_break": 0.0, "close_position_ratio": None,
                "range_expansion_ratio": 0.0, "direction_commitment_score": 0.0, "expected_move_pct": 0.0,
                "distance_increasing": False, "strong_close": False, "weak_reversal_risk": False,
            }
        br, bd = self._break_body_ratio_vs_prev(candidate, bars)
        br_avg, _ = self._break_bar_body_ratio(candidate, bars)
        close_pos = self._break_bar_close_position_in_candle(candidate, bars)
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        bbar = next((b for b in reversed(bars) if self._normalize_dt(b.ts) == ts_n), bars[-1])
        c_range = abs(float(bbar.high)-float(bbar.low))
        sample = bars[-4:-1] if len(bars) >= 4 else bars[:-1]
        avg_range = (sum(abs(float(x.high)-float(x.low)) for x in sample)/max(1,len(sample))) if sample else 0.0
        range_exp = (c_range/avg_range) if avg_range>0 else 1.0
        pb = post_break if post_break is not None else [b for b in bars if self._normalize_dt(b.ts) >= ts_n]
        expected = self._compute_expected_move_pct(candidate, pb) if pb else 0.0
        dist_inc = bool(distance_hint) if distance_hint is not None else bool((candidate.break_event.metadata or {}).get('distance_increasing'))
        if len(bars)>=2 and candidate.trendline is not None:
            prev=bars[-2]; cur=bars[-1]
            lp=float(candidate.trendline.value_at(prev.ts)); lc=float(candidate.trendline.value_at(cur.ts))
            if abs(lp)>1e-9 and abs(lc)>1e-9:
                dp=abs(float(prev.close)-lp)/abs(lp); dc=abs(float(cur.close)-lc)/abs(lc)
                dist_inc = dc>dp
        commit = (close_pos if close_pos is not None else 0.0) if candidate.direction == TrendlineDirection.BEAR else ((1.0-close_pos) if close_pos is not None else 0.0)
        strong_close = bool(commit >= 0.70)
        weak_reversal = self._has_large_opposite_candle(candidate, self._bars_for_break_event_window(candidate, bars))
        metrics = {
            "break_distance_pct": float(bd),
            "body_ratio_break": float(br),
            "body_ratio_vs_prev": float(br),
            "body_ratio_vs_avg": float(br_avg),
            "close_position_ratio": float(close_pos) if close_pos is not None else None,
            "range_expansion_ratio": float(range_exp),
            "direction_commitment_score": float(max(0.0, min(1.0, commit))),
            "expected_move_pct": float(expected),
            "distance_increasing": bool(dist_inc),
            "strong_close": bool(strong_close),
            "weak_reversal_risk": bool(weak_reversal),
        }
        log.info(
            "TRENDLINE_BREAK_QUALITY_METRICS | symbol=%s | trade_id=%s | break_distance_pct=%.6f | body_ratio_break=%.3f | close_position_ratio=%s | range_expansion_ratio=%.3f | direction_commitment_score=%.3f | expected_move_pct=%.6f | distance_increasing=%s | strong_close=%s | weak_reversal_risk=%s",
            candidate.symbol,
            self._trade_id(candidate),
            float(metrics["break_distance_pct"]),
            float(metrics["body_ratio_break"]),
            f"{float(metrics['close_position_ratio']):.3f}" if metrics["close_position_ratio"] is not None else "none",
            float(metrics["range_expansion_ratio"]),
            float(metrics["direction_commitment_score"]),
            float(metrics["expected_move_pct"]),
            str(bool(metrics["distance_increasing"])).lower(),
            str(bool(metrics["strong_close"])).lower(),
            str(bool(metrics["weak_reversal_risk"])).lower(),
        )
        return metrics

    def _is_slow_trend_acceptance(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, Dict[str, Any]]:
        if len(post_break) < 3 or candidate.trendline is None:
            return False, {}
        look = post_break[-min(6, len(post_break)):]
        directional = 0
        overlap_hits = 0
        for i in range(1, len(look)):
            d = float(look[i].close) - float(look[i-1].close)
            if (candidate.direction == TrendlineDirection.BEAR and d > 0) or (candidate.direction == TrendlineDirection.BULL and d < 0):
                directional += 1
            if max(float(look[i].high), float(look[i-1].high)) > min(float(look[i].low), float(look[i-1].low)):
                overlap_hits += 1
        consistency = directional / max(1, len(look)-1)
        start = float(look[0].close); end = float(look[-1].close)
        disp = abs(end-start) / max(abs(start), 1e-9)
        ok = consistency >= max(0.60, float(self._slow_trend_consistency_min)-0.07) and disp >= float(self._min_expected_move_pct_default) and overlap_hits <= len(look)-1
        return ok, {"consistency": consistency, "cumulative_displacement": disp, "overlap_hits": overlap_hits, "bars": len(look)}

    def _has_structural_acceptance(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, str]:
        if len(post_break) < 3 or candidate.break_event is None:
            return False, "insufficient_post_break_bars"
        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 0:
            return False, "invalid_break_level"
        closes = [float(b.close) for b in post_break[-6:]]
        beyond = 0
        reclaim = 0
        for c in closes:
            if candidate.direction == TrendlineDirection.BEAR:
                beyond += 1 if c > break_level else 0
                reclaim += 1 if c <= break_level else 0
            else:
                beyond += 1 if c < break_level else 0
                reclaim += 1 if c >= break_level else 0
        if beyond < int(self._acceptance_min_beyond_closes):
            return False, "acceptance_beyond_line_insufficient"
        if reclaim > int(self._acceptance_max_reclaims):
            return False, "repeated_reclaim_failures"
        slow_ok, slow_meta = self._is_slow_trend_acceptance(candidate, post_break)
        if slow_ok:
            log.info("TRENDLINE_SLOW_TREND_ACCEPT | symbol=%s | trade_id=%s | consistency=%.3f | displacement=%.6f | overlap_hits=%d", candidate.symbol, self._trade_id(candidate), float(slow_meta.get('consistency',0.0)), float(slow_meta.get('cumulative_displacement',0.0)), int(slow_meta.get('overlap_hits',0)))
            return True, "slow_trend_acceptance"
        log.info("TRENDLINE_SLOW_TREND_REJECT | symbol=%s | trade_id=%s | reason=slow_structure_not_directional", candidate.symbol, self._trade_id(candidate))
        return True, "multi_close_acceptance"

    def _break_body_ratio_vs_prev(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> tuple[float, float]:
        """Body ratio vs prior bar (detector-aligned). Used for break qualification and impulse gates."""
        if not candidate.break_event:
            return 0.0, 0.0
        meta = candidate.break_event.metadata or {}
        if meta.get("body_ratio_vs_prev") is not None:
            return float(meta["body_ratio_vs_prev"]), float(candidate.break_event.break_distance_pct or 0.0)
        if meta.get("body_ratio") is not None:
            return float(meta["body_ratio"]), float(candidate.break_event.break_distance_pct or 0.0)
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        idx = -1
        for i in range(len(bars) - 1, -1, -1):
            if self._normalize_dt(bars[i].ts) == ts_n:
                idx = i
                break
        if idx <= 0:
            return 0.0, float(candidate.break_event.break_distance_pct or 0.0)
        bbar = bars[idx]
        prev = bars[idx - 1]
        body = abs(float(bbar.close) - float(bbar.open))
        prev_body = abs(float(prev.close) - float(prev.open))
        ratio = (body / max(prev_body, 1e-6)) if prev_body > 0 else 0.0
        return float(ratio), float(candidate.break_event.break_distance_pct or 0.0)

    def _persist_break_body_ratios(
        self,
        event: TrendlineBreakEvent,
        *,
        body_ratio_vs_avg: float,
        prev_bar: Optional[OHLCVBar],
        break_bar: OHLCVBar,
    ) -> None:
        if event.metadata is None:
            event.metadata = {}
        br_prev = float((event.metadata or {}).get("body_ratio_vs_prev") or (event.metadata or {}).get("body_ratio") or 0.0)
        if br_prev <= 0.0 and prev_bar is not None:
            body = abs(float(break_bar.close) - float(break_bar.open))
            prev_body = abs(float(prev_bar.close) - float(prev_bar.open))
            br_prev = (body / max(prev_body, 1e-6)) if prev_body > 0 else 0.0
        event.metadata["body_ratio_vs_prev"] = float(br_prev)
        event.metadata["body_ratio_vs_avg"] = float(body_ratio_vs_avg)
        event.metadata["body_ratio"] = float(br_prev)

    def _is_impulse_fast_emit_entry(self, candidate: TrendlineCandidate, break_meta: Dict[str, Any]) -> bool:
        sr = str(candidate.state_reason or "").lower()
        return bool(
            break_meta.get("impulse_mode")
            or break_meta.get("early_entry_mode")
            or "immediate_break" in sr
            or "first_move" in sr
            or str(break_meta.get("trendline_mode") or "").upper() in {"IMPULSE", "EARLY_ENTRY"}
        )

    def _count_beyond_line_closes(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> int:
        if not candidate.break_event or not post_break:
            return 0
        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 0:
            return 0
        beyond = 0
        for b in post_break[-6:]:
            c = float(b.close)
            if candidate.direction == TrendlineDirection.BEAR:
                beyond += 1 if c > break_level else 0
            else:
                beyond += 1 if c < break_level else 0
        return beyond

    def _line_separation_persistent(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, float]:
        if not post_break or candidate.trendline is None:
            return False, 0.0
        last = post_break[-1]
        line_px = float(candidate.trendline.value_at(last.ts))
        if line_px <= 1e-9:
            return False, 0.0
        dist_pct = abs(float(last.close) - line_px) / abs(line_px)
        min_sep = max(float(self.config.break_distance_min) * 0.35, 0.0004)
        return dist_pct >= min_sep, float(dist_pct)

    def _post_break_acceptance_met(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        *,
        continuation_dist: float = 0.0,
        reclaimed: bool = False,
    ) -> tuple[bool, str, Dict[str, Any]]:
        struct_ok, struct_reason = self._has_structural_acceptance(candidate, post_break)
        slow_ok, slow_meta = self._is_slow_trend_acceptance(candidate, post_break)
        beyond = self._count_beyond_line_closes(candidate, post_break)
        sep_ok, sep_pct = self._line_separation_persistent(candidate, post_break)
        min_cont = float(self.config.min_continuation_distance_pct)
        cont_ok = float(continuation_dist) >= min_cont and not reclaimed
        meta: Dict[str, Any] = {
            "structural_acceptance": bool(struct_ok),
            "slow_trend_acceptance": bool(slow_ok),
            "continuation_dist": float(continuation_dist),
            "reclaimed": bool(reclaimed),
            "beyond_line_closes": int(beyond),
            "line_separation_pct": float(sep_pct),
            "line_separation_ok": bool(sep_ok),
        }
        meta.update(slow_meta)
        if struct_ok:
            return True, str(struct_reason), meta
        if slow_ok:
            return True, "slow_trend_acceptance", meta
        if cont_ok:
            return True, "continuation_dist_no_reclaim", meta
        if beyond >= int(self._acceptance_min_beyond_closes) and not reclaimed and sep_ok:
            return True, "beyond_line_closes_separation", meta
        return False, "post_break_acceptance_missing", meta

    def _log_post_break_acceptance(
        self,
        candidate: TrendlineCandidate,
        *,
        accepted: bool,
        via: str,
        meta: Dict[str, Any],
        expansion_state: str = "",
    ) -> None:
        log.warning(
            "TRENDLINE_POST_BREAK_ACCEPTANCE | symbol=%s | trade_id=%s | accepted=%s | via=%s | "
            "structural_acceptance=%s | slow_trend_acceptance=%s | continuation_dist=%.6f | reclaimed=%s | "
            "beyond_line_closes=%d | line_separation_pct=%.6f | expansion_quality_state=%s | persistence_state=%s",
            candidate.symbol,
            self._trade_id(candidate),
            str(bool(accepted)).lower(),
            via or "none",
            str(bool(meta.get("structural_acceptance"))).lower(),
            str(bool(meta.get("slow_trend_acceptance"))).lower(),
            float(meta.get("continuation_dist", 0.0)),
            str(bool(meta.get("reclaimed"))).lower(),
            int(meta.get("beyond_line_closes", 0)),
            float(meta.get("line_separation_pct", 0.0)),
            expansion_state or "n/a",
            via or "pending",
        )

    def _momentum_persistence_agrees(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> bool:
        slow_ok, _ = self._is_slow_trend_acceptance(candidate, post_break)
        if slow_ok:
            return True
        opt = self._expected_option_side_for_candidate(candidate)
        seg = post_break[-4:] if len(post_break) >= 4 else post_break
        if len(seg) < 2:
            return True
        directional = 0
        pairs = 0
        for i in range(1, len(seg)):
            pairs += 1
            c0 = float(seg[i - 1].close)
            c1 = float(seg[i].close)
            if opt == "PUT":
                if c1 < c0:
                    directional += 1
            else:
                if c1 > c0:
                    directional += 1
        required = 2 if pairs >= 2 else 1
        return directional >= required

    def _maybe_reanchor_candidate(self, candidate: TrendlineCandidate, bars: List[OHLCVBar], now: datetime) -> None:
        if not self._reanchor_enabled or candidate.state != TrendlineCandidateState.WAITING_FOR_BREAK:
            return
        if getattr(candidate, "reanchor_attempted", False):
            return
        started = self._normalize_dt(getattr(candidate, "start_time", now))
        elapsed = max(0.0, (self._normalize_dt(now) - started).total_seconds() / 60.0)
        if elapsed < float(self._reanchor_minutes):
            return
        if not bars:
            return
        last = bars[-1]
        near = self._near_price_to_line_frac(candidate, last)
        touch_ct = int(getattr(candidate, "touch_count", 0) or 0)
        fragile = touch_ct >= int(_STRUCTURE_FRAGILITY_TOUCH_COUNT)
        if fragile:
            log.warning(
                "TRENDLINE_STRUCTURE_FRAGILITY_ACTIVE | symbol=%s | trade_id=%s | touch_count=%d | threshold=%d",
                candidate.symbol,
                self._trade_id(candidate),
                touch_ct,
                int(_STRUCTURE_FRAGILITY_TOUCH_COUNT),
            )
        if near is not None and (
            float(near) < float(_REANCHOR_NEAR_LINE_FREEZE_FRAC) or fragile
        ):
            log.warning(
                "TRENDLINE_REANCHOR_FREEZE | symbol=%s | trade_id=%s | line_proximity_frac=%s | touch_count=%d "
                "| fragile=%s",
                candidate.symbol,
                self._trade_id(candidate),
                "none" if near is None else f"{float(near):.8f}",
                touch_ct,
                str(bool(fragile)).lower(),
            )
            candidate.reanchor_attempted = True
            return
        log.info(
            "TRENDLINE_REANCHOR_BUILD | symbol=%s | trade_id=%s | elapsed_minutes=%.2f | min_touches=%d",
            candidate.symbol,
            self._trade_id(candidate),
            elapsed,
            int(self._reanchor_min_touches),
        )
        rebuilt = self.builder.build_reanchored_from_recent_bars(
            candidate.symbol,
            candidate.direction,
            bars[-min(len(bars), int(self._reanchor_lookback_bars)) :],
            min_touches=int(self._reanchor_min_touches),
        )
        if rebuilt is None:
            candidate.reanchor_attempted = True
            log.info(
                "TRENDLINE_REANCHOR_REJECTED | symbol=%s | trade_id=%s | reason=insufficient_structure_quality",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return
        if self._reanchor_break_geometry_would_erase(candidate, bars, rebuilt):
            candidate.reanchor_attempted = True
            log.warning(
                "TRENDLINE_REANCHOR_REJECT_BREAK_ERASURE | symbol=%s | trade_id=%s | reason=recent_break_context_preserved",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return
        candidate.reanchor_attempted = True
        candidate.trendline = rebuilt
        candidate.reanchor_anchor_count = int((rebuilt.metadata or {}).get("touch_count", 0))
        setattr(candidate, "trendline_line_source", "reanchor")
        setattr(candidate, "structure_maturity_bar_index", max(0, len(bars) - 1))
        log.warning(
            "TRENDLINE_REANCHOR_ACCEPTED | symbol=%s | trade_id=%s | touch_count=%d | anchor_spacing_min=%s",
            candidate.symbol,
            self._trade_id(candidate),
            int(candidate.reanchor_anchor_count),
            str((rebuilt.metadata or {}).get("anchor_spacing_min", "none")),
        )

    def _maybe_log_missed_win_early(self, candidate: TrendlineCandidate, bar: OHLCVBar) -> None:
        st = getattr(candidate, "skip_timestamp", None)
        sp = getattr(candidate, "skip_price", None)
        if st is None or sp is None or float(sp) <= 0:
            return
        if getattr(candidate, "missed_win_early_logged", False):
            return
        now_ts = self._normalize_dt(bar.ts)
        dt_min = (now_ts - self._normalize_dt(st)).total_seconds() / 60.0
        if dt_min > 15.0:
            return
        close = float(bar.close)
        want_up = candidate.direction == TrendlineDirection.BEAR
        move = (close - float(sp)) / float(sp) if want_up else (float(sp) - close) / float(sp)
        if move >= 0.004:
            log.info(
                "TRENDLINE_MISSED_WIN_EARLY | symbol=%s | trade_id=%s | skip_ts=%s | minutes_since_skip=%.2f | "
                "move_pct=%.4f | expected_direction=%s",
                candidate.symbol,
                self._trade_id(candidate),
                self._normalize_dt(st).isoformat(),
                dt_min,
                float(move) * 100.0,
                "up" if want_up else "down",
            )
            candidate.missed_win_early_logged = True

    def _warn_confirm_pending(self, candidate: TrendlineCandidate, phase: str, detail: str) -> None:
        """Sampled WARNING so Cloud Logging shows break registered but entry gates not yet satisfied."""
        key = candidate.candidate_id or candidate.symbol
        now = time_module.time()
        if now - float(self._confirm_pending_last_log_ts.get(key, 0.0)) < float(
            self._confirm_pending_log_sec
        ):
            return
        self._confirm_pending_last_log_ts[key] = now
        log.warning(
            "TRENDLINE_PIPELINE | stage=confirm_pending | symbol=%s | trade_id=%s | geometry=%s | phase=%s | %s",
            candidate.symbol,
            self._trade_id(candidate),
            candidate.direction.value,
            phase,
            detail,
        )

    def _rearm_for_next_break(self, candidate: TrendlineCandidate, reason: str) -> None:
        """
        Reset break-specific state so symbol can keep watching for a fresh break.

        Used when a specific break attempt is invalidated, but the symbol should remain
        eligible for a later profitable setup.
        """
        candidate.break_event = None
        candidate.momentum_confirmation = None
        candidate.has_broken_once = False
        candidate.break_attempt_count = 0
        candidate.hold_success_at = None
        candidate.continuation_break_at = None
        candidate.local_continuation_level = None
        candidate.hold_bars_achieved = 0
        candidate.hold_end_bar_index = 0
        candidate.last_hold_duration_seconds = 0.0
        candidate.continuation_pending = False
        candidate.continuation_start_index = 0
        candidate.continuation_reason = ""
        candidate.continuation_max_bars = int(self._continuation_max_bars)
        candidate.rearm_pending = False
        candidate.rearm_origin_reason = ""
        candidate.rearm_started_at = None
        candidate.rearm_checks_done = 0
        candidate.rearm_break_level = None
        candidate.rearm_break_candle_low = None
        candidate.rearm_break_candle_high = None
        candidate.retest_pending = False
        candidate.retest_started_at = None
        candidate.retest_break_level = None
        candidate.retest_direction = ""
        candidate.retest_checks_done = 0
        candidate.mfe_window_logged = {}
        candidate.impulse_pending_confirmation = False
        candidate.impulse_break_high = None
        candidate.impulse_break_low = None
        candidate.break_bar_index = None
        candidate.break_timestamp = None
        candidate.decision_logged = False
        candidate.skip_timestamp = None
        candidate.skip_price = None
        candidate.missed_win_early_logged = False
        self._clear_delayed_continuation_flags(candidate)
        self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BREAK, reason)

    def _structure_still_intact(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> bool:
        if not bars or not candidate.trendline:
            return False
        look = bars[-min(6, len(bars)) :]
        line_violations = 0
        for b in look:
            line = float(candidate.trendline.value_at(b.ts))
            if abs(line) <= 1e-9:
                continue
            close = float(b.close)
            if candidate.direction == TrendlineDirection.BEAR and close < line:
                line_violations += 1
            if candidate.direction == TrendlineDirection.BULL and close > line:
                line_violations += 1
        return line_violations <= int(self._rearm_max_structure_violations)

    def _activate_rearm_candidate(
        self,
        candidate: TrendlineCandidate,
        reason: str,
        post_break: List[OHLCVBar],
    ) -> bool:
        reason_norm = str(reason or "").strip().lower()
        if not self._rearm_enabled:
            return False
        if reason_norm in self._rearm_disallowed_reasons:
            log.warning(
                "TRENDLINE_REARM_BLOCKED | symbol=%s | trade_id=%s | reason=%s | detail=disallowed_reason",
                candidate.symbol,
                self._trade_id(candidate),
                reason,
            )
            return False
        if reason_norm not in self._rearm_allowed_reasons:
            return False
        if not candidate.break_event or not post_break:
            return False
        if not self._structure_still_intact(candidate, post_break):
            log.warning(
                "TRENDLINE_REARM_REJECT_STRUCTURE | symbol=%s | trade_id=%s | reason=%s | detail=structure_not_intact",
                candidate.symbol,
                self._trade_id(candidate),
                reason_norm,
            )
            return False
        break_bar = post_break[0]
        candidate.rearm_pending = True
        candidate.rearm_origin_reason = reason_norm
        candidate.rearm_started_at = self._normalize_dt(candidate.break_event.candle_ts)
        candidate.rearm_checks_done = 0
        candidate.rearm_break_level = float(candidate.break_event.close_price or 0.0)
        candidate.rearm_break_candle_low = float(break_bar.low)
        candidate.rearm_break_candle_high = float(break_bar.high)
        log.warning(
            "TRENDLINE_REARM_ELIGIBLE_EXPANDED | symbol=%s | trade_id=%s | reason=%s | max_checks=%d | max_minutes=%.1f",
            candidate.symbol,
            self._trade_id(candidate),
            reason_norm,
            int(self._rearm_max_checks),
            float(self._rearm_max_minutes),
        )
        return True

    def _handle_rearm_stage(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> Optional[TrendlineTradeSignal]:
        if not candidate.rearm_pending:
            return None
        now_bar = post_break[-1] if post_break else None
        if not now_bar or not candidate.trendline:
            return None

        started_at = self._normalize_dt(
            candidate.rearm_started_at or candidate.break_event.candle_ts  # type: ignore[union-attr]
        )
        elapsed_minutes = max(0.0, (self._normalize_dt(now_bar.ts) - started_at).total_seconds() / 60.0)
        if elapsed_minutes > float(self._rearm_max_minutes):
            candidate.rearm_pending = False
            log.warning(
                "TRENDLINE_REARM_EXPIRED | symbol=%s | trade_id=%s | reason=max_minutes_exceeded | elapsed_minutes=%.2f | max_minutes=%.2f",
                candidate.symbol,
                self._trade_id(candidate),
                elapsed_minutes,
                float(self._rearm_max_minutes),
            )
            return None
        if int(candidate.rearm_checks_done) >= int(self._rearm_max_checks):
            candidate.rearm_pending = False
            log.warning(
                "TRENDLINE_REARM_EXPIRED | symbol=%s | trade_id=%s | reason=max_checks_exceeded | checks=%d | max_checks=%d",
                candidate.symbol,
                self._trade_id(candidate),
                int(candidate.rearm_checks_done),
                int(self._rearm_max_checks),
            )
            return None

        candidate.rearm_checks_done = int(candidate.rearm_checks_done) + 1
        check_idx = int(candidate.rearm_checks_done)
        last_bar = post_break[-1]
        prev_bar = post_break[-2] if len(post_break) >= 2 else None
        line_now = float(candidate.trendline.value_at(last_bar.ts))
        if line_now <= 0:
            log.warning(
                "TRENDLINE_REARM_BLOCKED | symbol=%s | trade_id=%s | reason=invalid_trendline_value",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return None
        close_px = float(last_bar.close)
        break_level = float(candidate.rearm_break_level or 0.0)
        break_low = float(candidate.rearm_break_candle_low or 0.0)
        break_high = float(candidate.rearm_break_candle_high or 0.0)
        body = abs(float(last_bar.close) - float(last_bar.open))
        sample = post_break[-3:] if len(post_break) >= 3 else post_break
        avg_body = (
            sum(abs(float(b.close) - float(b.open)) for b in sample) / max(1, len(sample))
            if sample
            else 0.0
        )
        body_ratio = (body / avg_body) if avg_body > 0 else 0.0
        distance_increasing = False
        if prev_bar is not None:
            line_prev = float(candidate.trendline.value_at(prev_bar.ts))
            if line_prev > 0:
                dist_prev = abs(float(prev_bar.close) - line_prev) / line_prev
                dist_now = abs(float(last_bar.close) - line_now) / line_now
                distance_increasing = dist_now > dist_prev
        anti_chop_fail, anti_chop_reason = self._fails_anti_chop_entry(candidate, post_break)

        if candidate.direction == TrendlineDirection.BULL:
            holds_direction = close_px < line_now and (break_level <= 0.0 or close_px < break_level)
            extension_ok = float(last_bar.low) < break_low
        else:
            holds_direction = close_px > line_now and (break_level <= 0.0 or close_px > break_level)
            extension_ok = float(last_bar.high) > break_high
        quality_ok = distance_increasing and body_ratio >= float(self._rearm_body_ratio_threshold) and not anti_chop_fail
        ready = holds_direction and extension_ok and quality_ok

        log.warning(
            "TRENDLINE_REARM_CHECK | symbol=%s | trade_id=%s | check=%d/%d | elapsed_min=%.2f | holds_direction=%s | extension_ok=%s | distance_increasing=%s | body_ratio=%.3f | anti_chop=%s | ready=%s",
            candidate.symbol,
            self._trade_id(candidate),
            check_idx,
            int(self._rearm_max_checks),
            elapsed_minutes,
            str(bool(holds_direction)).lower(),
            str(bool(extension_ok)).lower(),
            str(bool(distance_increasing)).lower(),
            body_ratio,
            anti_chop_reason if anti_chop_fail else "clear",
            str(bool(ready)).lower(),
        )

        if not ready:
            if anti_chop_fail:
                log.warning(
                    "TRENDLINE_REARM_BLOCKED | symbol=%s | trade_id=%s | reason=%s | check=%d/%d",
                    candidate.symbol,
                    self._trade_id(candidate),
                    anti_chop_reason or "anti_chop",
                    check_idx,
                    int(self._rearm_max_checks),
                )
            if check_idx >= int(self._rearm_max_checks):
                candidate.rearm_pending = False
                log.warning(
                    "TRENDLINE_REARM_EXPIRED | symbol=%s | trade_id=%s | reason=max_checks_exhausted",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
            return None

        if candidate.break_event is not None and candidate.break_event.metadata is not None:
            candidate.break_event.metadata["distance_increasing"] = True
            candidate.break_event.metadata["body_expanding"] = True
            candidate.break_event.metadata["rearm_entry"] = True
            candidate.break_event.metadata["rearm_check_count"] = check_idx
        candidate.rearm_pending = False
        candidate.momentum_confirmation = self.momentum_engine.confirm(
            direction=candidate.direction,
            break_event=candidate.break_event,
            post_break_bars=post_break,
        )
        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "rearm_follow_through_entry")
        log.warning(
            "TRENDLINE_REARM_SIGNAL_EMIT | symbol=%s | trade_id=%s | check=%d/%d | reason=no_follow_through_recovered",
            candidate.symbol,
            self._trade_id(candidate),
            check_idx,
            int(self._rearm_max_checks),
        )
        return self._emit_trade_signal(
            candidate,
            float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
        )

    def _activate_retest_candidate(self, candidate: TrendlineCandidate, reason: str) -> bool:
        if not self._retest_enabled or not candidate.break_event:
            return False
        candidate.retest_pending = True
        candidate.retest_started_at = self._normalize_dt(candidate.break_event.candle_ts)
        candidate.retest_break_level = float(candidate.break_event.close_price or 0.0)
        candidate.retest_direction = "put" if candidate.direction == TrendlineDirection.BULL else "call"
        candidate.retest_checks_done = 0
        self._log_trendline_flow_stage(candidate, "retest_pending")
        log.warning(
            "TRENDLINE_RETEST_ELIGIBLE | symbol=%s | trade_id=%s | reason=%s | direction=%s | max_checks=%d | max_minutes=%.1f",
            candidate.symbol,
            self._trade_id(candidate),
            reason,
            candidate.retest_direction,
            int(self._retest_max_checks),
            float(self._retest_max_minutes),
        )
        return True

    def _handle_retest_stage(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> Optional[TrendlineTradeSignal]:
        if not candidate.retest_pending or not candidate.trendline or not candidate.break_event or not post_break:
            return None
        now_bar = post_break[-1]
        started = self._normalize_dt(candidate.retest_started_at or candidate.break_event.candle_ts)
        elapsed_min = max(0.0, (self._normalize_dt(now_bar.ts) - started).total_seconds() / 60.0)
        if elapsed_min > float(self._retest_max_minutes):
            candidate.retest_pending = False
            log.warning(
                "TRENDLINE_RETEST_EXPIRED | symbol=%s | trade_id=%s | reason=max_minutes_exceeded | elapsed_min=%.2f",
                candidate.symbol,
                self._trade_id(candidate),
                elapsed_min,
            )
            return None
        if int(candidate.retest_checks_done) >= int(self._retest_max_checks):
            candidate.retest_pending = False
            log.warning(
                "TRENDLINE_RETEST_EXPIRED | symbol=%s | trade_id=%s | reason=max_checks_exceeded | checks=%d",
                candidate.symbol,
                self._trade_id(candidate),
                int(candidate.retest_checks_done),
            )
            return None
        candidate.retest_checks_done = int(candidate.retest_checks_done) + 1
        check = int(candidate.retest_checks_done)
        prev = post_break[-2] if len(post_break) >= 2 else None
        line_now = float(candidate.trendline.value_at(now_bar.ts))
        line_prev = float(candidate.trendline.value_at(prev.ts)) if prev is not None else line_now
        if line_now <= 0 or line_prev <= 0:
            return None
        break_level = float(candidate.retest_break_level or candidate.break_event.close_price or 0.0)
        body = abs(float(now_bar.close) - float(now_bar.open))
        sample = post_break[-3:] if len(post_break) >= 3 else post_break
        avg_body = (
            sum(abs(float(b.close) - float(b.open)) for b in sample) / max(1, len(sample))
            if sample
            else 0.0
        )
        body_ratio = body / avg_body if avg_body > 0 else 0.0
        dist_prev = abs(float(prev.close) - line_prev) / line_prev if prev is not None else 0.0
        dist_now = abs(float(now_bar.close) - line_now) / line_now
        distance_resumed = dist_now > dist_prev
        break_bar = post_break[0]
        anti_chop_fail, anti_chop_reason = self._fails_anti_chop_entry(candidate, post_break)
        ready = False
        if candidate.direction == TrendlineDirection.BULL:
            holds_line = float(now_bar.close) < line_now
            below_break = float(now_bar.close) < break_level
            failed_retest = float(now_bar.high) >= (
                line_now * (1.0 - float(self._retest_line_buffer_pct))
            ) and float(now_bar.close) < line_now
            structure = float(now_bar.high) < float(break_bar.high)
            ready = holds_line and below_break and failed_retest and structure and distance_resumed
        else:
            holds_line = float(now_bar.close) > line_now
            above_break = float(now_bar.close) > break_level
            failed_retest = float(now_bar.low) <= (
                line_now * (1.0 + float(self._retest_line_buffer_pct))
            ) and float(now_bar.close) > line_now
            structure = float(now_bar.low) > float(break_bar.low)
            ready = holds_line and above_break and failed_retest and structure and distance_resumed
        ready = ready and (body_ratio >= float(self._retest_body_ratio_min)) and (not anti_chop_fail)
        log.warning(
            "TRENDLINE_RETEST_CHECK | symbol=%s | trade_id=%s | check=%d/%d | elapsed_min=%.2f | body_ratio=%.3f | distance_resumed=%s | anti_chop=%s | ready=%s",
            candidate.symbol,
            self._trade_id(candidate),
            check,
            int(self._retest_max_checks),
            elapsed_min,
            body_ratio,
            str(bool(distance_resumed)).lower(),
            anti_chop_reason if anti_chop_fail else "clear",
            str(bool(ready)).lower(),
        )
        if not ready:
            if check >= int(self._retest_max_checks):
                candidate.retest_pending = False
                log.warning(
                    "TRENDLINE_RETEST_EXPIRED | symbol=%s | trade_id=%s | reason=max_checks_exhausted",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
            return None
        candidate.retest_pending = False
        if candidate.break_event.metadata is not None:
            candidate.break_event.metadata["trendline_mode"] = "RETEST"
            candidate.break_event.metadata["retest_mode"] = True
            candidate.break_event.metadata["distance_increasing"] = True
            candidate.break_event.metadata["body_expanding"] = True
        candidate.momentum_confirmation = self.momentum_engine.confirm(
            direction=candidate.direction,
            break_event=candidate.break_event,
            post_break_bars=post_break,
        )
        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "retest_entry")
        log.warning(
            "TRENDLINE_RETEST_ENTRY | symbol=%s | trade_id=%s | check=%d/%d",
            candidate.symbol,
            self._trade_id(candidate),
            check,
            int(self._retest_max_checks),
        )
        return self._emit_trade_signal(
            candidate,
            float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
        )

    def _maybe_log_post_break_mfe(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> None:
        if not candidate.break_event or not post_break:
            return
        break_ts = self._normalize_dt(candidate.break_event.candle_ts)
        last_ts = self._normalize_dt(post_break[-1].ts)
        elapsed_min = max(0.0, (last_ts - break_ts).total_seconds() / 60.0)
        entry_ref = float(candidate.break_event.close_price or 0.0)
        if entry_ref <= 0:
            return
        for label, mins in (("5m", 5.0), ("10m", 10.0), ("20m", 20.0)):
            if elapsed_min < mins or candidate.mfe_window_logged.get(label):
                continue
            window = [b for b in post_break if (self._normalize_dt(b.ts) - break_ts).total_seconds() <= mins * 60.0]
            if not window:
                continue
            if candidate.direction == TrendlineDirection.BULL:
                mfe = (entry_ref - min(float(b.low) for b in window)) / entry_ref
            else:
                mfe = (max(float(b.high) for b in window) - entry_ref) / entry_ref
            candidate.mfe_window_logged[label] = True
            log.info(
                "TRENDLINE_POST_BREAK_MFE | symbol=%s | trade_id=%s | window=%s | mfe_pct=%.6f",
                candidate.symbol,
                self._trade_id(candidate),
                label,
                mfe,
            )

    def _check_impulse_pending_confirmation(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> Optional[bool]:
        """
        Returns:
          None  -> not pending
          False -> still waiting / blocked
          True  -> confirmed and can proceed
        """
        if not candidate.impulse_pending_confirmation:
            return None
        if len(post_break) < 2:
            return False
        cur = post_break[-1]
        break_hi = float(candidate.impulse_break_high or 0.0)
        break_lo = float(candidate.impulse_break_low or 0.0)
        blocked = False
        if candidate.direction == TrendlineDirection.BULL:
            blocked = break_hi > 0 and float(cur.high) >= break_hi
        else:
            blocked = break_lo > 0 and float(cur.low) <= break_lo
        log.warning(
            "TRENDLINE_IMPULSE_CONFIRM_CHECK | symbol=%s | trade_id=%s | blocked=%s | break_high=%.4f | break_low=%.4f | cur_high=%.4f | cur_low=%.4f",
            candidate.symbol,
            self._trade_id(candidate),
            str(bool(blocked)).lower(),
            break_hi,
            break_lo,
            float(cur.high),
            float(cur.low),
        )
        if blocked:
            candidate.impulse_pending_confirmation = False
            if candidate.break_event and candidate.break_event.metadata is not None:
                candidate.break_event.metadata["impulse_mode"] = False
            log.warning(
                "TRENDLINE_IMPULSE_BLOCKED | symbol=%s | trade_id=%s | reason=next_candle_reversal",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return False
        candidate.impulse_pending_confirmation = False
        log.warning(
            "TRENDLINE_IMPULSE_CONFIRMED | symbol=%s | trade_id=%s",
            candidate.symbol,
            self._trade_id(candidate),
        )
        return True

    def _log_entry_filtered(
        self,
        candidate: TrendlineCandidate,
        filter_type: str,
        distance: float,
        body_ratio: float,
        distance_increasing: bool,
    ) -> None:
        log.warning(
            "TRENDLINE_ENTRY_FILTERED | symbol=%s | trade_id=%s | type=%s | distance=%.6f | body_ratio=%.3f | distance_increasing=%s",
            candidate.symbol,
            self._trade_id(candidate),
            filter_type,
            float(distance),
            float(body_ratio),
            str(bool(distance_increasing)).lower(),
        )

    def _log_breakout_quality(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        break_meta: Dict[str, object],
    ) -> tuple[float, float, float]:
        if not candidate.trendline or not bars:
            return 0.0, 0.0, 0.0
        last_bar = bars[-1]
        line_px = float(candidate.trendline.value_at(last_bar.ts))
        if line_px <= 0:
            return 0.0, 0.0, 0.0
        distance = abs(float(last_bar.close) - line_px) / line_px
        body = abs(float(last_bar.close) - float(last_bar.open))
        sample = bars[-3:] if len(bars) >= 3 else bars
        avg_body = (
            sum(abs(float(b.close) - float(b.open)) for b in sample) / max(1, len(sample))
            if sample
            else 0.0
        )
        body_ratio = (body / avg_body) if avg_body > 0 else 0.0
        log.info(
            "TRENDLINE_BREAKOUT_QUALITY | symbol=%s | trade_id=%s | distance=%.6f | body_ratio=%.3f | avg_body=%.6f | distance_increasing=%s | strong_breakout=%s | breakout_is_clean=%s",
            candidate.symbol,
            self._trade_id(candidate),
            distance,
            body_ratio,
            avg_body,
            str(bool(break_meta.get("distance_increasing"))).lower(),
            str(bool(break_meta.get("strong_breakout"))).lower(),
            str(bool(break_meta.get("breakout_is_clean"))).lower(),
        )
        return distance, body_ratio, avg_body

    def _log_decision_snapshot(
        self,
        candidate: TrendlineCandidate,
        entry_path: str,
        decision: str,
        reason: str,
    ) -> None:
        bars = self._bar_cache.get(candidate.symbol, [])
        last_bar = bars[-1] if bars else None
        if not candidate.trendline or not last_bar:
            return
        line_px = float(candidate.trendline.value_at(last_bar.ts))
        if line_px <= 0:
            return
        close_px = float(last_bar.close)
        break_distance = abs(close_px - line_px) / line_px
        body_size = abs(float(last_bar.close) - float(last_bar.open))
        body_sample = bars[-3:] if len(bars) >= 3 else bars
        avg_body = (
            sum(abs(float(b.close) - float(b.open)) for b in body_sample) / max(1, len(body_sample))
            if body_sample
            else 0.0
        )
        body_ratio = (body_size / avg_body) if avg_body > 0 else 0.0
        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        candle_range = abs(float(last_bar.high) - float(last_bar.low))
        close_position_in_candle = None
        if candle_range > 1e-12:
            close_position_in_candle = (float(last_bar.close) - float(last_bar.low)) / candle_range
        prev_bar = bars[-2] if len(bars) >= 2 else None
        velocity = (
            abs(float(last_bar.close) - float(prev_bar.close)) / max(abs(float(prev_bar.close)), 1e-9)
            if prev_bar is not None
            else 0.0
        )
        break_ts = self._normalize_dt(candidate.break_event.candle_ts) if candidate.break_event else None
        post_break = (
            [b for b in bars if break_ts is not None and self._normalize_dt(b.ts) >= break_ts]
            if break_ts is not None
            else []
        )
        expected_move_pct = self._compute_expected_move_pct(candidate, post_break)
        if candidate.direction == TrendlineDirection.BULL:
            direction = "put"
        else:
            direction = "call"
        log.info(
            "TRENDLINE_DECISION_GEOMETRY_DETAIL | symbol=%s | trade_id=%s | geometry=%s | direction=%s | line=%.4f | close=%.4f | "
            "break_distance=%.6f | body_ratio=%.3f | distance_increasing=%s | velocity=%.6f | close_position_in_candle=%s | expected_move_pct=%.6f | "
            "drift_breakout=%s | entry_path=%s | decision=%s | reason=%s",
            candidate.symbol,
            self._trade_id(candidate),
            candidate.direction.value,
            direction,
            line_px,
            close_px,
            break_distance,
            body_ratio,
            str(bool(break_meta.get("distance_increasing"))).lower(),
            velocity,
            f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
            expected_move_pct,
            str(bool(break_meta.get("drift_breakout"))).lower(),
            entry_path,
            decision,
            reason,
        )

    def _log_entry_timing(self, candidate: TrendlineCandidate, entry_path: str) -> None:
        if not candidate.break_event:
            return
        bars = self._bar_cache.get(candidate.symbol, [])
        break_ts = self._normalize_dt(candidate.break_event.candle_ts)
        post_break = [b for b in bars if self._normalize_dt(b.ts) >= break_ts]
        self._maybe_log_post_break_mfe(candidate, post_break)
        bars_since_break = max(0, len(post_break) - 1)
        seconds_since_break = 0.0
        entry_vs_break_pct = 0.0
        if post_break:
            seconds_since_break = max(
                0.0,
                (self._normalize_dt(post_break[-1].ts) - break_ts).total_seconds(),
            )
            break_px = float(candidate.break_event.close_price or 0.0)
            if break_px > 0:
                entry_vs_break_pct = (float(post_break[-1].close) - break_px) / break_px
        log.info(
            "TRENDLINE_ENTRY_TIMING | symbol=%s | trade_id=%s | bars_since_break=%d | seconds_since_break=%.1f | entry_path=%s | entry_vs_break_pct=%.6f",
            candidate.symbol,
            self._trade_id(candidate),
            bars_since_break,
            seconds_since_break,
            entry_path,
            entry_vs_break_pct,
        )
        log.info(
            "TRENDLINE_ENTRY_SIGNAL | symbol=%s | trade_id=%s | timestamp=%s | seconds_since_break=%.3f | entry_type=%s",
            candidate.symbol,
            self._trade_id(candidate),
            datetime.now(timezone.utc).isoformat(),
            seconds_since_break,
            entry_path,
        )

    def _seconds_since_break_on_bar(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> float:
        if not candidate.break_event or not bars:
            return 0.0
        break_ts = self._normalize_dt(candidate.break_event.candle_ts)
        last_ts = self._normalize_dt(bars[-1].ts)
        return max(0.0, (last_ts - break_ts).total_seconds())

    def _entry_timing_fast_path_ok(self, break_distance_pct: float) -> bool:
        return (
            float(break_distance_pct) >= float(self._extreme_break_threshold)
            or float(break_distance_pct) >= float(self._near_extreme_break_threshold)
            or float(break_distance_pct) >= float(self._strong_break_threshold)
        )

    def _execution_quality_gate(
        self,
        candidate: TrendlineCandidate,
        break_distance_pct: float,
        body_ratio_break: float,
        distance_increasing: bool,
    ) -> tuple[bool, str]:
        cfg = self.config
        # Deprecated as hard vetoes: strict break/body gates now mostly influence scoring.
        # Keep only catastrophic micro-break protection as a binary block.
        if (
            float(break_distance_pct) < max(1e-6, float(cfg.strict_min_break_distance) * 0.20)
            and float(body_ratio_break) < 0.05
            and not bool(distance_increasing)
        ):
            return False, "catastrophic_micro_break"
        strong_override = (
            float(break_distance_pct) >= float(cfg.strong_break_threshold)
            and float(body_ratio_break) >= float(cfg.direction_override_strong_body_ratio)
        )
        if not bool(distance_increasing):
            if strong_override:
                log.info(
                    "TRENDLINE_STRONG_BREAK_OVERRIDE_GATE | symbol=%s | trade_id=%s | break_distance_pct=%.6f | "
                    "body_ratio_break=%.4f | strong_break_threshold=%.6f | direction_override_body_ratio=%.4f",
                    candidate.symbol,
                    self._trade_id(candidate),
                    float(break_distance_pct),
                    float(body_ratio_break),
                    float(cfg.strong_break_threshold),
                    float(cfg.direction_override_strong_body_ratio),
                )
            # Advisory-only in simplified mode.
            return True, "distance_not_increasing_advisory"
        return True, ""

    def _expected_option_side_for_candidate(self, candidate: TrendlineCandidate) -> str:
        setup = candidate.setup_payload or {}
        side = str(setup.get("expected_option_side") or "").strip().lower()
        if side in ("call", "put"):
            return side
        return "put" if candidate.direction == TrendlineDirection.BULL else "call"

    def _false_break_gate_log_suppressed(self, symbol: str, trade_id: str, kind: str, reason: str) -> bool:
        if kind not in (
            "TRENDLINE_FALSE_BREAK_SURVIVAL_WINDOW",
            "TRENDLINE_FALSE_BREAK_ADVISORY",
            "TRENDLINE_FALSE_BREAK_HARD_BLOCK",
        ):
            return False
        if kind == "TRENDLINE_FALSE_BREAK_HARD_BLOCK" and reason != "no_local_continuation":
            return False
        bucket_sec = max(15.0, float(self._false_break_gate_log_throttle_sec))
        bucket = int(time_module.time() // bucket_sec)
        key = f"{symbol}|{trade_id}|{kind}|{reason}"
        if self._false_break_log_throttle_bucket.get(key) == bucket:
            return True
        self._false_break_log_throttle_bucket[key] = bucket
        return False

    def _emit_false_break_terminal(self, candidate: TrendlineCandidate, outcome: str, detail: str) -> None:
        tid = self._trade_id(candidate)
        if tid not in self._false_break_survival_armed_trade_ids or tid in self._false_break_terminal_logged:
            return
        self._false_break_terminal_logged.add(tid)
        if outcome == "hard_reject":
            self._funnel_blocked("continuation")
        log.warning(
            "TRENDLINE_FALSE_BREAK_TERMINAL | symbol=%s | trade_id=%s | outcome=%s | detail=%s",
            candidate.symbol,
            tid,
            outcome,
            detail,
        )
        self._false_break_survival_armed_trade_ids.discard(tid)

    def _funnel_inc(self, name: str, delta: int = 1) -> None:
        if name in self._funnel_counts:
            self._funnel_counts[name] += int(delta)

    def _funnel_blocked(self, bucket: str) -> None:
        key = f"blocked_by_{bucket}"
        if key in self._funnel_counts:
            self._funnel_counts[key] += 1

    def _maybe_log_trendline_funnel_summary(self) -> None:
        now = time_module.time()
        if now - self._funnel_last_log_ts < self._funnel_interval_sec:
            return
        self._funnel_last_log_ts = now
        fc = self._funnel_counts
        log.warning(
            "TRENDLINE_FUNNEL_SUMMARY | interval_sec=%.0f | candidates_seen=%d | break_detected=%d | selector_structure_ready=%d | "
            "pre_execute_passed=%d | selector_reached=%d | submitted=%d | durable_confirmed=%d | blocked_by_false_break=%d | "
            "blocked_by_continuation=%d | blocked_by_quality=%d",
            self._funnel_interval_sec,
            int(fc.get("candidates_seen", 0)),
            int(fc.get("break_detected", 0)),
            int(fc.get("structure_accepted", 0)),
            int(fc.get("pre_execute_passed", 0)),
            int(fc.get("selector_reached", 0)),
            int(fc.get("submitted", 0)),
            int(fc.get("durable_confirmed", 0)),
            int(fc.get("blocked_by_false_break", 0)),
            int(fc.get("blocked_by_continuation", 0)),
            int(fc.get("blocked_by_quality", 0)),
        )
        for k in list(fc.keys()):
            fc[k] = 0

    def _on_trendline_signal_submit(self, candidate: TrendlineCandidate) -> None:
        self._funnel_inc("submitted")
        self._emit_false_break_terminal(candidate, "execution", "signal_emit")

    def _log_trendline_false_break_gate(
        self,
        *,
        kind: str,
        symbol: str,
        trade_id: str,
        reason: str,
        final_action: str,
        break_distance_pct: float,
        body_ratio: float,
        distance_increasing: bool,
        expected_move_pct: float,
        selector_structure_ready: bool,
        pre_execute_passed: bool,
        seconds_since_break: float,
        option_side: str = "",
        extra: str = "",
    ) -> None:
        if self._false_break_gate_log_suppressed(symbol, trade_id, kind, reason):
            return
        suffix = f" | {extra}" if extra else ""
        log.warning(
            "%s | symbol=%s | trade_id=%s | reason=%s | side=%s | break_distance_pct=%.6f | body_ratio=%.4f | "
            "distance_increasing=%s | expected_move_pct=%.6f | selector_structure_ready=%s | pre_execute_passed=%s | "
            "seconds_since_break=%.2f | final_action=%s%s",
            kind,
            symbol,
            trade_id,
            reason,
            option_side or "n/a",
            float(break_distance_pct),
            float(body_ratio),
            str(bool(distance_increasing)).lower(),
            float(expected_move_pct),
            str(bool(selector_structure_ready)).lower(),
            str(bool(pre_execute_passed)).lower(),
            float(seconds_since_break),
            final_action,
            suffix,
        )

    def _catastrophic_impulse_weak_body(
        self,
        *,
        break_distance_pct: float,
        body_ratio_fast: float,
        distance_increasing: bool,
        expected_move_pct: float,
        reclaimed: bool,
        opposite_conflict: bool,
        min_fast_break: float,
    ) -> bool:
        """Hard veto only when weak body is paired with other failure modes (0DTE micro-chop)."""
        if body_ratio_fast >= float(getattr(self.config, "fast_path_min_body_ratio", 0.72)):
            return False
        tiny_vs_floor = float(break_distance_pct) < max(1e-6, float(min_fast_break) * 0.55)
        tiny_vs_strict = float(break_distance_pct) < max(
            1e-6, float(self.config.strict_min_break_distance) * 0.35
        )
        em_dead = float(expected_move_pct) <= max(1e-8, float(self._min_expected_move_pct_default) * 0.35)
        if reclaimed or opposite_conflict:
            return True
        if em_dead and not bool(distance_increasing):
            return True
        if tiny_vs_floor and tiny_vs_strict and not bool(distance_increasing):
            return True
        if float(body_ratio_fast) < 0.18 and tiny_vs_strict:
            return True
        return False

    def _strict_pre_emit_fail_is_catastrophic(
        self,
        *,
        fail_reason: str,
        triple_weak: bool,
        break_distance_current: float,
        br_body_ratio: float,
        dist_inc_meta: Optional[bool],
        reversal_candle: bool,
        bars: List[OHLCVBar],
        candidate: TrendlineCandidate,
    ) -> bool:
        if fail_reason == "reversal_candle":
            return bool(reversal_candle) and float(break_distance_current) < float(
                self._strong_break_threshold
            ) * 0.35 and float(self._compute_expected_move_pct(candidate, bars)) < float(
                self._min_expected_move_pct_default
            )
        if fail_reason == "weak_break":
            return False
        if fail_reason == "break_quality":
            if triple_weak and bool(dist_inc_meta):
                return False
            if triple_weak:
                return True
            return self._execution_quality_gate(
                candidate,
                float(break_distance_current),
                float(br_body_ratio),
                bool(dist_inc_meta),
            )[1] == "catastrophic_micro_break"
        return True

    def _entry_timing_decision(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        break_distance_pct: float,
        body_ratio_break: float,
    ) -> tuple[bool, str]:
        cfg = self.config
        sec = self._seconds_since_break_on_bar(candidate, bars)
        min_s = float(cfg.entry_min_seconds_after_break)
        max_s = float(cfg.entry_max_seconds_after_break)
        timing_fast = self._entry_timing_fast_path_ok(break_distance_pct)
        strong_entry = (
            float(break_distance_pct) >= float(cfg.strong_break_threshold)
            and float(body_ratio_break) >= float(cfg.direction_override_strong_body_ratio)
        )
        if min_s > 0.0 and sec < min_s:
            if strong_entry:
                log.info(
                    "TRENDLINE_FAST_ENTRY_STRONG_BREAK | symbol=%s | trade_id=%s | seconds_since_break=%.3f | "
                    "min_seconds_after_break=%.3f | break_distance_pct=%.6f | body_ratio_break=%.4f",
                    candidate.symbol,
                    self._trade_id(candidate),
                    sec,
                    min_s,
                    float(break_distance_pct),
                    float(body_ratio_break),
                )
            elif not timing_fast:
                return False, f"too_soon_sec={sec:.1f}_min={min_s:.1f}_fast_path=false"
        if max_s > 0.0 and sec > max_s:
            return False, f"too_late_sec={sec:.1f}_max={max_s:.1f}"
        eff_fast = timing_fast or strong_entry
        return True, f"ok_sec={sec:.1f}_fast_path={str(eff_fast).lower()}"

    def _compute_market_regime(
        self,
        intraday_by_symbol: Dict[str, List[OHLCVBar]],
    ) -> tuple[str, Dict[str, float]]:
        """
        Session-level regime from 6:30–7:30 PT bars:
          - avg candle range
          - directional candle consistency
          - overlap ratio (choppiness)
        """
        sym_metrics: List[Dict[str, float]] = []
        start_t = time(6, 30)
        end_t = time(7, 30)
        for _, bars in intraday_by_symbol.items():
            if not bars or len(bars) < 4:
                continue
            window = []
            for b in bars:
                ts_pt = self._normalize_dt(b.ts).astimezone(self._tz_pt)
                if start_t <= ts_pt.time() < end_t:
                    window.append(b)
            if len(window) < 4:
                continue
            ranges = []
            overlaps = []
            directions = []
            for i, b in enumerate(window):
                c = float(b.close)
                if abs(c) > 1e-9:
                    ranges.append(abs(float(b.high) - float(b.low)) / abs(c))
                if i >= 1:
                    prev = window[i - 1]
                    d = float(b.close) - float(prev.close)
                    if abs(d) > 1e-12:
                        directions.append(1.0 if d > 0 else -1.0)
                    inter = max(
                        0.0,
                        min(float(prev.high), float(b.high)) - max(float(prev.low), float(b.low)),
                    )
                    union = max(float(prev.high), float(b.high)) - min(float(prev.low), float(b.low))
                    if union > 1e-12:
                        overlaps.append(inter / union)
            if not ranges or len(directions) < 2 or not overlaps:
                continue
            up = sum(1 for d in directions if d > 0)
            down = sum(1 for d in directions if d < 0)
            directional_ratio = max(up, down) / max(1, len(directions))
            sym_metrics.append(
                {
                    "avg_range_pct": float(mean(ranges)),
                    "directional_ratio": float(directional_ratio),
                    "overlap_ratio": float(mean(overlaps)),
                }
            )
        if not sym_metrics:
            return "TRENDING", {"avg_range_pct": 0.0, "directional_ratio": 0.0, "overlap_ratio": 0.0, "symbols_used": 0.0}
        avg_range_pct = float(mean(m["avg_range_pct"] for m in sym_metrics))
        directional_ratio = float(mean(m["directional_ratio"] for m in sym_metrics))
        overlap_ratio = float(mean(m["overlap_ratio"] for m in sym_metrics))
        is_choppy = (
            overlap_ratio >= float(self._regime_choppy_overlap_min)
            and (
                directional_ratio < float(self._regime_trend_directional_min)
                or avg_range_pct < float(self._regime_min_avg_range_pct)
            )
        )
        regime = "CHOPPY" if is_choppy else "TRENDING"
        return regime, {
            "avg_range_pct": avg_range_pct,
            "directional_ratio": directional_ratio,
            "overlap_ratio": overlap_ratio,
            "symbols_used": float(len(sym_metrics)),
        }

    def _detect_impulse_mode(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> tuple[bool, Dict[str, float]]:
        """
        Detect fast impulse within 1-2 candles after break.
        """
        if not self._impulse_enabled or not candidate.trendline or not post_break:
            return False, {}
        window = post_break[:2]
        best: Dict[str, float] = {}
        for b in window:
            line_px = float(candidate.trendline.value_at(b.ts))
            if line_px <= 0:
                continue
            candle_range = abs(float(b.high) - float(b.low))
            close_position_in_candle = None
            if candle_range > 1e-9:
                close_position_in_candle = (float(b.close) - float(b.low)) / candle_range
            body = abs(float(b.close) - float(b.open))
            sample = post_break[:3] if len(post_break) >= 3 else post_break
            avg_body = (
                sum(abs(float(x.close) - float(x.open)) for x in sample) / max(1, len(sample))
                if sample
                else 0.0
            )
            body_ratio = (body / avg_body) if avg_body > 0 else 0.0
            break_distance = abs(float(b.close) - line_px) / line_px
            is_large = body_ratio >= float(self._impulse_min_body_ratio)
            is_far = break_distance >= float(self._break_distance_min)
            if is_large and is_far:
                # Fake-break guard: require decisive close location inside candle.
                if candidate.direction == TrendlineDirection.BULL:
                    if close_position_in_candle is None or close_position_in_candle > 0.5:
                        log.warning(
                            "TRENDLINE_IMPULSE_BLOCKED | symbol=%s | trade_id=%s | reason=weak_close_position | close_position_in_candle=%s",
                            candidate.symbol,
                            self._trade_id(candidate),
                            f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
                        )
                        continue
                else:
                    if close_position_in_candle is None or close_position_in_candle < 0.5:
                        log.warning(
                            "TRENDLINE_IMPULSE_BLOCKED | symbol=%s | trade_id=%s | reason=weak_close_position | close_position_in_candle=%s",
                            candidate.symbol,
                            self._trade_id(candidate),
                            f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
                        )
                        continue
                best = {
                    "body_ratio": body_ratio,
                    "break_distance": break_distance,
                    "line_px": line_px,
                    "close_px": float(b.close),
                    "close_position_in_candle": float(close_position_in_candle)
                    if close_position_in_candle is not None
                    else -1.0,
                }
                break
        return bool(best), best

    def _detect_slow_trend_mode(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> tuple[bool, Dict[str, float]]:
        """
        Detect steady directional drift (3-6 bars) without a single impulse candle.
        """
        if (
            not self._slow_trend_enabled
            or not candidate.trendline
            or not candidate.break_event
            or len(post_break) < int(self._slow_trend_min_candles)
        ):
            return False, {}
        seg = post_break[: int(self._slow_trend_max_candles)]
        if len(seg) < int(self._slow_trend_min_candles):
            return False, {}
        anti_chop_fail, anti_chop_reason = self._fails_anti_chop_entry(candidate, seg)
        if anti_chop_fail:
            return False, {"blocked_reason": anti_chop_reason}
        if self._has_large_opposite_candle(candidate, seg[-2:] if len(seg) >= 2 else seg):
            return False, {"blocked_reason": "large_opposite_candle"}

        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 0.0:
            return False, {}

        directional_hits = 0
        checks = 0
        no_reversal = True
        for i in range(1, len(seg)):
            prev = seg[i - 1]
            cur = seg[i]
            checks += 1
            line_px = float(candidate.trendline.value_at(cur.ts))
            if line_px <= 0:
                continue
            if candidate.direction == TrendlineDirection.BULL:
                if float(cur.high) <= float(prev.high) and float(cur.low) <= float(prev.low):
                    directional_hits += 1
                if float(cur.close) >= line_px:
                    no_reversal = False
                if float(cur.close) >= break_level:
                    no_reversal = False
            else:
                if float(cur.high) >= float(prev.high) and float(cur.low) >= float(prev.low):
                    directional_hits += 1
                if float(cur.close) <= line_px:
                    no_reversal = False
                if float(cur.close) <= break_level:
                    no_reversal = False
        consistency = (float(directional_hits) / float(checks)) if checks > 0 else 0.0
        last_close = float(seg[-1].close)
        if candidate.direction == TrendlineDirection.BULL:
            cumulative_break_move = (break_level - last_close) / break_level
        else:
            cumulative_break_move = (last_close - break_level) / break_level
        ready = (
            no_reversal
            and consistency >= float(self._slow_trend_consistency_min)
            and cumulative_break_move >= float(self._slow_trend_cum_break_move_min)
        )
        return ready, {
            "consistency": consistency,
            "cumulative_break_move": cumulative_break_move,
            "candles_used": float(len(seg)),
            "break_level": break_level,
            "no_reversal": 1.0 if no_reversal else 0.0,
        }

    def _fails_expansion_quality_filter(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        *,
        bars_for_timing: Optional[List[OHLCVBar]] = None,
        body_ratio_break: Optional[float] = None,
    ) -> tuple[bool, str]:
        """Return (True, reason) when expansion quality fails.

        Standard path: endpoint close vs break close / total_range (legacy).

        Narrow impulse path (when enabled): IMPULSE_BREAK or EARLY_ENTRY, distance_increasing,
        and body_ratio_break >= expansion_impulse_body_strong — uses MFE excursion vs total_range
        after a survival window (min bars or min seconds after break) before low_expansion can fire.
        """
        if not candidate.break_event or len(post_break) < 2:
            return False, ""
        cfg = self.config
        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 0:
            return False, ""
        meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        highs = [float(b.high) for b in post_break]
        lows = [float(b.low) for b in post_break]
        total_range = max(highs) - min(lows)
        tid = self._trade_id(candidate)
        sym = candidate.symbol

        if candidate.direction == TrendlineDirection.BULL:
            net_move_endpoint = break_level - float(post_break[-1].close)
            max_favorable = max(0.0, float(max(highs)) - break_level)
        else:
            net_move_endpoint = float(post_break[-1].close) - break_level
            max_favorable = max(0.0, break_level - float(min(lows)))
        net_move_ratio_endpoint = max(0.0, net_move_endpoint) / total_range if total_range > 1e-9 else 0.0
        mfe_move_ratio = max(0.0, max_favorable) / total_range if total_range > 1e-9 else 0.0

        timing_bars = bars_for_timing if bars_for_timing is not None else post_break
        sec_since = self._seconds_since_break_on_bar(candidate, timing_bars)
        break_ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        bars_after_break = sum(1 for b in post_break if self._normalize_dt(b.ts) > break_ts_n)

        br_body = float(body_ratio_break) if body_ratio_break is not None else 0.0
        if body_ratio_break is None:
            br_body, _ = self._break_body_ratio_vs_prev(candidate, timing_bars)

        arch_norm = self._normalize_entry_archetype(str(meta.get("break_archetype") or ""))
        impulse_arch = arch_norm == ENTRY_ARCHETYPE_IMPULSE
        early_em = bool(meta.get("early_entry_mode"))
        dist_inc = bool(meta.get("distance_increasing"))
        strong_th = float(getattr(cfg, "expansion_impulse_body_strong", 1.25) or 1.25)
        use_mfe_path = (
            bool(getattr(cfg, "expansion_impulse_mfe_enabled", True))
            and dist_inc
            and br_body >= strong_th
            and (impulse_arch or early_em)
        )
        min_surv_bars = max(0, int(getattr(cfg, "expansion_impulse_survival_min_bars", 2) or 0))
        min_surv_sec = max(0.0, float(getattr(cfg, "expansion_impulse_survival_min_seconds", 45.0) or 0.0))
        survival_met = (bars_after_break >= min_surv_bars) or (sec_since >= min_surv_sec)
        mode = "standard"
        if use_mfe_path:
            mode = "impulse_mfe" if survival_met else "impulse_mfe_survival"

        if use_mfe_path:
            log.info(
                "TRENDLINE_EXPANSION_MODE | symbol=%s | trade_id=%s | mode=%s | use_mfe_path=%s | survival_met=%s | "
                "impulse_arch=%s | early_entry=%s | dist_inc=%s | body_ratio_break=%.4f | strong_th=%.4f",
                sym,
                tid,
                mode,
                str(use_mfe_path).lower(),
                str(survival_met).lower(),
                str(impulse_arch).lower(),
                str(early_em).lower(),
                str(dist_inc).lower(),
                br_body,
                strong_th,
            )
            log.info(
                "TRENDLINE_EXPANSION_MFE | symbol=%s | trade_id=%s | mfe_move_ratio=%.6f | max_favorable=%.6f | total_range=%.6f | direction=%s",
                sym,
                tid,
                mfe_move_ratio,
                max_favorable,
                total_range,
                candidate.direction.value,
            )
            log.info(
                "TRENDLINE_EXPANSION_LAST_CLOSE | symbol=%s | trade_id=%s | endpoint_ratio=%.6f | break_close=%.6f | last_close=%.6f",
                sym,
                tid,
                net_move_ratio_endpoint,
                break_level,
                float(post_break[-1].close),
            )
            log.info(
                "TRENDLINE_EXPANSION_SURVIVAL_WINDOW | symbol=%s | trade_id=%s | bars_after_break=%d | sec_since_break=%.2f | "
                "min_bars=%d | min_sec=%.2f | survival_met=%s | deferred_low_expansion=%s",
                sym,
                tid,
                bars_after_break,
                sec_since,
                min_surv_bars,
                min_surv_sec,
                str(survival_met).lower(),
                str(bool(use_mfe_path and not survival_met)).lower(),
            )
        else:
            log.debug(
                "TRENDLINE_EXPANSION_MODE | symbol=%s | trade_id=%s | mode=standard | endpoint_ratio=%.6f | mfe_ratio=%.6f | bars_after=%d",
                sym,
                tid,
                net_move_ratio_endpoint,
                mfe_move_ratio,
                bars_after_break,
            )

        if total_range <= 1e-9:
            log.warning(
                "TRENDLINE_EXPANSION_REJECT_DETAIL | symbol=%s | trade_id=%s | reason=TRENDLINE_REJECT_LOW_EXPANSION | "
                "detail=zero_total_range | mode=%s",
                sym,
                tid,
                mode,
            )
            return True, "TRENDLINE_REJECT_LOW_EXPANSION"

        overlaps = []
        returns_to_break = 0
        break_eps = max(1e-9, abs(break_level) * float(self._retest_line_buffer_pct))
        for i in range(1, len(post_break)):
            a = post_break[i - 1]
            b = post_break[i]
            inter = max(0.0, min(float(a.high), float(b.high)) - max(float(a.low), float(b.low)))
            union = max(float(a.high), float(b.high)) - min(float(a.low), float(b.low))
            if union > 1e-12:
                overlaps.append(inter / union)
            if abs(float(b.close) - break_level) <= break_eps:
                returns_to_break += 1
        overlap_ratio = (sum(overlaps) / len(overlaps)) if overlaps else 0.0

        if overlap_ratio > float(self.config.expansion_overlap_max) or returns_to_break > int(
            self.config.expansion_break_level_return_max
        ):
            log.warning(
                "TRENDLINE_EXPANSION_REJECT_DETAIL | symbol=%s | trade_id=%s | reason=chop_range_expansion_fail | "
                "overlap_ratio=%.4f | overlap_max=%.4f | break_returns=%d | break_returns_max=%d | mode=%s | "
                "endpoint_ratio=%.6f | mfe_ratio=%.6f",
                sym,
                tid,
                overlap_ratio,
                float(self.config.expansion_overlap_max),
                returns_to_break,
                int(self.config.expansion_break_level_return_max),
                mode,
                net_move_ratio_endpoint,
                mfe_move_ratio,
            )
            log.warning(
                "TRENDLINE_REJECT_CHOP_RANGE | symbol=%s | trade_id=%s | overlap_ratio=%.3f | overlap_max=%.3f | break_returns=%d | break_returns_max=%d",
                sym,
                tid,
                overlap_ratio,
                float(self.config.expansion_overlap_max),
                returns_to_break,
                int(self.config.expansion_break_level_return_max),
            )
            return True, "chop_range_expansion_fail"

        th = float(self.config.expansion_net_move_ratio_min)
        if use_mfe_path and not survival_met:
            log.info(
                "TRENDLINE_EXPANSION_REJECT_DETAIL | symbol=%s | trade_id=%s | reason=none | detail=survival_defer_low_expansion | "
                "mode=%s | endpoint_ratio=%.6f | mfe_ratio=%.6f | threshold=%.4f",
                sym,
                tid,
                mode,
                net_move_ratio_endpoint,
                mfe_move_ratio,
                th,
            )
            return False, "survival_defer_low_expansion"

        ratio_for_gate = mfe_move_ratio if use_mfe_path and survival_met else net_move_ratio_endpoint
        if ratio_for_gate < th:
            log.warning(
                "TRENDLINE_EXPANSION_REJECT_DETAIL | symbol=%s | trade_id=%s | reason=low_expansion_quality | "
                "mode=%s | ratio_used=%.6f | endpoint_ratio=%.6f | mfe_ratio=%.6f | threshold=%.4f | survival_met=%s",
                sym,
                tid,
                mode,
                ratio_for_gate,
                net_move_ratio_endpoint,
                mfe_move_ratio,
                th,
                str(survival_met).lower(),
            )
            log.warning(
                "TRENDLINE_REJECT_LOW_EXPANSION | symbol=%s | trade_id=%s | net_move_ratio=%.3f | threshold=%.3f",
                sym,
                tid,
                ratio_for_gate,
                th,
            )
            return True, "low_expansion_quality"
        return False, ""

    def _impulse_emit_persistence_met(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        *,
        bars_for_timing: List[OHLCVBar],
    ) -> tuple[bool, str]:
        """Impulse-only emit gate: wait for survival window OR strong net displacement vs range.

        Reuses ``expansion_impulse_survival_min_bars`` / ``expansion_impulse_survival_min_seconds``.
        Net-ratio bypass (no new config) avoids blocking clean one-bar explosions that already
        cleared expansion with materially stronger endpoint commitment.
        """
        if not candidate.break_event or len(post_break) < 2:
            return False, "impulse_emit_defer_post_break"
        cfg = self.config
        break_ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        bars_after_break = sum(1 for b in post_break if self._normalize_dt(b.ts) > break_ts_n)
        sec_since = self._seconds_since_break_on_bar(candidate, bars_for_timing)
        min_surv_bars = max(0, int(getattr(cfg, "expansion_impulse_survival_min_bars", 2) or 0))
        min_surv_sec = max(0.0, float(getattr(cfg, "expansion_impulse_survival_min_seconds", 45.0) or 0.0))
        survival_met = (bars_after_break >= min_surv_bars) or (sec_since >= min_surv_sec)
        if survival_met:
            return True, "impulse_emit_survival_met"
        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 1e-9:
            return False, "impulse_emit_defer_break_level"
        highs = [float(b.high) for b in post_break]
        lows = [float(b.low) for b in post_break]
        total_range = max(highs) - min(lows)
        if total_range <= 1e-9:
            return False, "impulse_emit_defer_range"
        if candidate.direction == TrendlineDirection.BULL:
            net_move_endpoint = break_level - float(post_break[-1].close)
        else:
            net_move_endpoint = float(post_break[-1].close) - break_level
        net_move_ratio_endpoint = max(0.0, net_move_endpoint) / total_range
        th = float(cfg.expansion_net_move_ratio_min)
        bypass_floor = max(th * 1.35, th + 0.08)
        if net_move_ratio_endpoint >= bypass_floor:
            return True, "impulse_emit_net_expansion_strong"
        log.info(
            "TRENDLINE_IMPULSE_EMIT_DEFER | symbol=%s | trade_id=%s | bars_after_break=%d | sec_since=%.2f | "
            "min_bars=%d | min_sec=%.2f | net_move_ratio=%.6f | expansion_ratio_min=%.4f | bypass_floor=%.4f",
            candidate.symbol,
            self._trade_id(candidate),
            bars_after_break,
            sec_since,
            min_surv_bars,
            min_surv_sec,
            net_move_ratio_endpoint,
            th,
            bypass_floor,
        )
        return False, "impulse_emit_defer_pending_persistence"

    def _post_break_expansion_strong(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        *,
        bars_for_expansion_timing: Optional[List[OHLCVBar]] = None,
    ) -> bool:
        """True when post-break price action passes expansion quality (used to relax hold / continuation)."""
        if not post_break:
            return False
        timing = bars_for_expansion_timing if bars_for_expansion_timing is not None else post_break
        br_body, _ = self._break_body_ratio_vs_prev(candidate, timing)
        fail, _ = self._fails_expansion_quality_filter(
            candidate,
            post_break,
            bars_for_timing=timing,
            body_ratio_break=float(br_body),
        )
        return not fail

    def _compute_expected_move_pct(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> float:
        if not candidate.break_event or not post_break:
            return 0.0
        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 1e-9:
            return 0.0
        ref_price = None
        look = post_break[: min(len(post_break), 6)]
        if candidate.direction == TrendlineDirection.BULL:
            ref_price = min(float(b.low) for b in look)
        else:
            ref_price = max(float(b.high) for b in look)
        expected_move = abs(float(break_level) - float(ref_price))
        expected_move_pct = expected_move / max(abs(float(break_level)), 1e-9)
        if expected_move_pct <= 0.0:
            break_distance = float(candidate.break_event.break_distance_pct or 0.0)
            if break_distance <= 0.0 and candidate.trendline and post_break:
                last_bar = post_break[-1]
                line_px = float(candidate.trendline.value_at(last_bar.ts))
                if line_px > 1e-9:
                    break_distance = abs(float(last_bar.close) - line_px) / line_px
            expected_move_pct = abs(break_distance) * 0.5
            log.warning(
                "TRENDLINE_MOVE_FALLBACK_USED | symbol=%s | trade_id=%s | break_distance=%.6f | expected_move_pct=%.6f",
                candidate.symbol,
                self._trade_id(candidate),
                float(break_distance),
                float(expected_move_pct),
            )
            log.warning(
                "TRENDLINE_FALLBACK_USED | symbol=%s | reason=expected_move_zero_derive_from_break_distance | "
                "fallback_type=expected_move_half_break_distance",
                candidate.symbol,
            )
        return expected_move_pct

    def _maybe_log_session_summary(self, now: Optional[datetime] = None) -> None:
        if self._session_summary_logged:
            return
        ts = self._normalize_dt(now or datetime.now(timezone.utc))
        if any(
            c.state
            not in {
                TrendlineCandidateState.EXECUTED,
                TrendlineCandidateState.INVALIDATED,
                TrendlineCandidateState.EXPIRED,
            }
            for c in self._candidates.values()
        ):
            return
        log.info(
            "TRENDLINE_SESSION_SUMMARY | trade_id=session_summary | symbols=%d | break_detected=%d | entry_emitted=%d | executed=%d | filtered_small_break=%d | filtered_weak_break=%d | filtered_anti_chop=%d | missed_opportunity=%d",
            len(self._candidates),
            int(self._decision_stats.get("break_detected", 0)),
            int(self._decision_stats.get("entry_emitted", 0)),
            int(self._decision_stats.get("executed", 0)),
            int(self._decision_stats.get("filtered_small_break", 0)),
            int(self._decision_stats.get("filtered_weak_break", 0)),
            int(self._decision_stats.get("filtered_anti_chop", 0)),
            int(self._decision_stats.get("missed_opportunity", 0)),
        )
        self._session_summary_logged = True

    def _fails_anti_chop_entry(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> tuple[bool, str]:
        if not candidate.trendline or not bars:
            return False, ""
        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        impulse_mode = bool(break_meta.get("impulse_mode"))
        slow_trend_mode = bool(break_meta.get("slow_trend_mode")) or (
            str(break_meta.get("trendline_mode") or "").upper() == "SLOW_TREND"
        )
        break_distance = float(candidate.break_event.break_distance_pct or 0.0) if candidate.break_event else 0.0
        recent = bars[-8:] if len(bars) >= 8 else bars
        if not recent:
            return False, ""
        avg_body = sum(abs(float(b.close) - float(b.open)) for b in recent) / max(1, len(recent))
        threshold = avg_body * 1.5
        last2 = bars[-2:] if len(bars) >= 2 else bars
        for b in last2:
            body = abs(float(b.close) - float(b.open))
            if body < threshold:
                continue
            is_large_opposite = (
                (candidate.direction == TrendlineDirection.BEAR and float(b.close) < float(b.open))
                or (candidate.direction == TrendlineDirection.BULL and float(b.close) > float(b.open))
            )
            if not is_large_opposite:
                continue
            if impulse_mode and break_distance >= 0.007:
                if candidate.break_event and candidate.break_event.metadata is not None:
                    candidate.break_event.metadata["anti_chop_override_used"] = True
                log.warning(
                    "TRENDLINE_ANTI_CHOP_OVERRIDE | symbol=%s | trade_id=%s | mode=impulse | reason=large_opposite_candle | break_distance=%.6f",
                    candidate.symbol,
                    self._trade_id(candidate),
                    float(break_distance),
                )
                return False, ""
            if slow_trend_mode and self._breakout_still_trending(candidate):
                if candidate.break_event and candidate.break_event.metadata is not None:
                    candidate.break_event.metadata["anti_chop_override_used"] = True
                log.warning(
                    "TRENDLINE_ANTI_CHOP_OVERRIDE | symbol=%s | trade_id=%s | mode=slow_trend | reason=large_opposite_candle | structure_intact=true",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                return False, ""
            if self._survival_blocks_negative_verdict(candidate):
                return False, ""
            return True, "large_opposite_candle"
        last_bar = bars[-1]
        line_px = candidate.trendline.value_at(last_bar.ts)
        if candidate.direction == TrendlineDirection.BEAR and float(last_bar.close) < float(line_px):
            if self._survival_blocks_negative_verdict(candidate):
                return False, ""
            return True, "snapback_across_line"
        if candidate.direction == TrendlineDirection.BULL and float(last_bar.close) > float(line_px):
            if self._survival_blocks_negative_verdict(candidate):
                return False, ""
            return True, "snapback_across_line"
        return False, ""

    def _compute_breakout_strength(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
    ) -> float:
        """Normalized breakout strength for anti-chop pullback classification."""
        if not candidate.trendline or not bars:
            return 0.0
        last_bar = bars[-1]
        line_px = float(candidate.trendline.value_at(last_bar.ts))
        if line_px <= 0:
            return 0.0
        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        distance = abs(float(last_bar.close) - line_px) / line_px
        body = abs(float(last_bar.close) - float(last_bar.open))
        sample = bars[-3:] if len(bars) >= 3 else bars
        avg_body = (
            sum(abs(float(b.close) - float(b.open)) for b in sample) / max(1, len(sample))
            if sample
            else 0.0
        )
        body_ratio = (body / avg_body) if avg_body > 0 else 0.0
        # Keep score bounded and simple: distance + candle expansion + momentum quality.
        distance_score = max(0.0, min(1.0, distance / max(self._break_distance_min * 2.5, 1e-9)))
        body_score = max(0.0, min(1.0, body_ratio / 1.8))
        momentum_score = 0.35 if bool(break_meta.get("distance_increasing")) else 0.0
        momentum_score += 0.35 if bool(break_meta.get("body_expanding")) else 0.0
        momentum_score += 0.30 if (
            bool(break_meta.get("strong_breakout"))
            or bool(break_meta.get("breakout_is_clean"))
            or bool(break_meta.get("drift_breakout"))
        ) else 0.0
        momentum_score = max(0.0, min(1.0, momentum_score))
        return max(
            0.0,
            min(
                1.0,
                distance_score * 0.40 + body_score * 0.25 + momentum_score * 0.35,
            ),
        )

    def _activate_continuation_candidate(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        reason: str,
    ) -> None:
        candidate.continuation_pending = True
        candidate.continuation_start_index = max(0, len(post_break) - 1)
        candidate.continuation_reason = reason
        mx = int(self._continuation_max_bars)
        arc_mx = str((candidate.break_event.metadata or {}).get("break_archetype") or "") if candidate.break_event else ""
        arc_u = arc_mx.strip().upper()
        if arc_u in {ENTRY_ARCHETYPE_DRIFT, "DELAYED_CONTINUATION"} or arc_mx.strip().lower() in {
            "weak_break_failure",
            "delayed_continuation",
            "continuation_drift",
        }:
            mx = min(mx, 4)
        candidate.continuation_max_bars = mx
        self._log_trendline_flow_stage(candidate, "continuation_pending")
        log.warning(
            "TRENDLINE_PULLBACK_CANDIDATE | symbol=%s | trade_id=%s | reason=%s | start_idx=%d | max_bars=%d",
            candidate.symbol,
            self._trade_id(candidate),
            reason,
            candidate.continuation_start_index,
            candidate.continuation_max_bars,
        )

    def _continuation_window_expired(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> bool:
        if not candidate.continuation_pending:
            return False
        bars_since = max(0, len(post_break) - 1 - int(candidate.continuation_start_index))
        return bars_since > int(candidate.continuation_max_bars or self._continuation_max_bars)

    def _continuation_structure_broken(
        self,
        candidate: TrendlineCandidate,
        continuation_slice: List[OHLCVBar],
    ) -> tuple[bool, str]:
        """Hard invalidation for continuation mode."""
        if not candidate.trendline or not continuation_slice:
            return False, ""
        for b in continuation_slice:
            line_px = float(candidate.trendline.value_at(b.ts))
            if line_px <= 0:
                continue
            if candidate.direction == TrendlineDirection.BEAR and float(b.close) < line_px:
                if self._survival_blocks_negative_verdict(candidate):
                    return False, ""
                return True, "breakdown_below_trendline"
            if candidate.direction == TrendlineDirection.BULL and float(b.close) > line_px:
                if self._survival_blocks_negative_verdict(candidate):
                    return False, ""
                return True, "breakout_above_trendline_against_put"
        # strong reversal in signal-opposite direction
        recent = continuation_slice[-2:] if len(continuation_slice) >= 2 else continuation_slice
        if self._has_large_opposite_candle(candidate, recent):
            if self._survival_blocks_negative_verdict(candidate):
                return False, ""
            return True, "strong_reversal"
        return False, ""

    def _continuation_entry_ready(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
    ) -> tuple[bool, str]:
        """
        Continuation entry path conditions:
        - structure still valid (checked upstream),
        - compression + tightening in pullback bars,
        - expansion candle appears in breakout direction,
        - momentum resumes (distance_increasing).
        """
        if not candidate.continuation_pending or len(post_break) < max(3, self._continuation_min_bars + 1):
            return False, "insufficient_continuation_bars"
        start = int(candidate.continuation_start_index)
        cont = post_break[start:] if start < len(post_break) else post_break[-1:]
        if len(cont) < max(3, self._continuation_min_bars + 1):
            return False, "continuation_window_too_short"
        breakout_level = float(candidate.break_event.close_price) if candidate.break_event else 0.0
        if breakout_level > 0:
            for b in cont:
                if candidate.direction == TrendlineDirection.BEAR and float(b.close) < breakout_level:
                    return False, "lost_breakout_level_call_path"
                if candidate.direction == TrendlineDirection.BULL and float(b.close) > breakout_level:
                    return False, "lost_breakout_level_put_path"
        # All but last bar should compress/tighten.
        pullback = cont[:-1]
        trigger = cont[-1]
        if len(pullback) < self._continuation_min_bars:
            return False, "awaiting_pullback_compression"
        pullback_bodies = [abs(float(b.close) - float(b.open)) for b in pullback]
        if len(pullback_bodies) >= 2 and pullback_bodies[-1] > pullback_bodies[0] * 1.10:
            return False, "bodies_not_compressing"
        pullback_ranges = [abs(float(b.high) - float(b.low)) for b in pullback]
        if len(pullback_ranges) >= 2 and pullback_ranges[-1] > pullback_ranges[0] * 1.05:
            return False, "range_not_tightening"
        trig_body = abs(float(trigger.close) - float(trigger.open))
        avg_pullback_body = sum(pullback_bodies) / max(1, len(pullback_bodies))
        if avg_pullback_body <= 0 or trig_body < avg_pullback_body * 1.20:
            return False, "no_expansion_candle"
        # Expansion in breakout direction.
        if candidate.direction == TrendlineDirection.BEAR and not (float(trigger.close) > float(trigger.open)):
            return False, "expansion_not_in_breakout_direction"
        if candidate.direction == TrendlineDirection.BULL and not (float(trigger.close) < float(trigger.open)):
            return False, "expansion_not_in_breakout_direction"
        # Momentum resumes via distance increase vs prior bar.
        if not candidate.trendline or len(cont) < 2:
            return False, "continuation_distance_not_increasing"
        prev = cont[-2]
        line_prev = float(candidate.trendline.value_at(prev.ts))
        line_now = float(candidate.trendline.value_at(trigger.ts))
        if line_prev <= 0 or line_now <= 0:
            return False, "continuation_distance_not_increasing"
        dist_prev = abs(float(prev.close) - line_prev) / line_prev
        dist_now = abs(float(trigger.close) - line_now) / line_now
        if dist_now <= dist_prev:
            return False, "continuation_distance_not_increasing"
        return True, "continuation_ready"

    def _fails_min_entry_distance(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> tuple[bool, float]:
        if not candidate.trendline or not bars:
            return False, 0.0
        last_bar = bars[-1]
        line_px = float(candidate.trendline.value_at(last_bar.ts))
        if line_px <= 0:
            return False, 0.0
        break_distance = abs(float(last_bar.close) - line_px) / line_px
        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        min_bd = float(self._effective_break_distance_min_pct(break_meta))
        if break_distance < min_bd:
            self._log_entry_filtered(
                candidate,
                "small_break",
                break_distance,
                0.0,
                bool(break_meta.get("distance_increasing")),
            )
            self._decision_stats["filtered_small_break"] += 1
            return True, break_distance
        return False, break_distance

    def _fails_weak_break_filter(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        distance_increasing: bool,
    ) -> tuple[bool, float, float]:
        if not candidate.trendline or not bars:
            return False, 0.0, 1.0
        last_bar = bars[-1]
        line_px = float(candidate.trendline.value_at(last_bar.ts))
        if line_px <= 0:
            return False, 0.0, 1.0

        metrics = self._compute_break_quality_metrics(candidate, bars, distance_hint=distance_increasing)
        break_distance = float(metrics["break_distance_pct"])
        body_ratio = float(metrics["body_ratio_break"])
        close_pos: Optional[float] = metrics.get("close_position_ratio")

        fb = self._bar_cache.get(candidate.symbol, [])
        cur_idx = max(0, len(fb) - 1) if fb else 0
        if self._in_survival_window(candidate, cur_idx):
            self._log_trendline_flow_stage(candidate, "survival_window_blocked_invalidation")
            return False, break_distance, body_ratio

        # Strong distance + momentum: skip legacy weak-break rejection (aligns with emit-path override).
        if self._strong_break_distance_override(break_distance, bool(distance_increasing)):
            sc = self._break_quality_score_telemetry(
                body_ratio, break_distance, close_pos, bool(distance_increasing)
            )
            self._log_trendline_break_quality_score(
                candidate,
                body_ratio=body_ratio,
                break_distance=break_distance,
                close_position_in_candle=close_pos,
                distance_increasing=bool(distance_increasing),
                score=sc,
                decision="accepted",
            )
            return False, break_distance, body_ratio

        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        min_bd_floor = self._effective_break_distance_min_pct(break_meta)
        composite = self._composite_break_quality_accepts(
            body_ratio, break_distance, close_pos, bool(distance_increasing), min_bd_floor=min_bd_floor
        )
        relax_ok, relax_tag = self._strict_body_ratio_advisory_relax(
            candidate,
            fb,
            break_meta,
            float(break_distance),
            bool(distance_increasing),
            float(body_ratio),
            close_pos,
            composite_ok=bool(composite),
        )
        if not composite and relax_ok:
            log.warning(
                "TRENDLINE_STRICT_BODY_RATIO_ADVISORY | symbol=%s | trade_id=%s | path=weak_break_filter | "
                "relaxed_via=%s | break_distance_pct=%.6f | body_ratio_break=%.4f | distance_increasing=%s",
                candidate.symbol,
                self._trade_id(candidate),
                relax_tag,
                float(break_distance),
                float(body_ratio),
                str(bool(distance_increasing)).lower(),
            )
            composite = True
        triple_weak = self._triple_weak_break_quality(body_ratio, break_distance, close_pos)
        sc = self._break_quality_score_telemetry(
            body_ratio, break_distance, close_pos, bool(distance_increasing)
        )

        if composite:
            self._log_trendline_break_quality_score(
                candidate,
                body_ratio=body_ratio,
                break_distance=break_distance,
                close_position_in_candle=close_pos,
                distance_increasing=bool(distance_increasing),
                score=sc,
                decision="accepted",
            )
            return False, break_distance, body_ratio

        if triple_weak:
            self._log_trendline_break_quality_score(
                candidate,
                body_ratio=body_ratio,
                break_distance=break_distance,
                close_position_in_candle=close_pos,
                distance_increasing=bool(distance_increasing),
                score=sc,
                decision="rejected",
            )
            self._log_entry_filtered(
                candidate,
                "weak_break",
                break_distance,
                body_ratio,
                bool(distance_increasing),
            )
            self._decision_stats["filtered_weak_break"] += 1
            return True, break_distance, body_ratio

        self._log_trendline_break_quality_score(
            candidate,
            body_ratio=body_ratio,
            break_distance=break_distance,
            close_position_in_candle=close_pos,
            distance_increasing=bool(distance_increasing),
            score=sc,
            decision="rejected",
        )
        self._log_entry_filtered(
            candidate,
            "break_quality",
            break_distance,
            body_ratio,
            bool(distance_increasing),
        )
        return True, break_distance, body_ratio

    def _touch_line_distance_pct(self, candidate: TrendlineCandidate, b: OHLCVBar) -> tuple[float, float]:
        if not candidate.trendline:
            return 0.0, 1.0
        line = float(candidate.trendline.value_at(b.ts))
        if line <= 0:
            return line, 1.0
        dist = min(
            abs(float(b.high) - line),
            abs(float(b.low) - line),
            abs(float(b.close) - line),
        ) / max(line, 1e-12)
        return line, dist

    def _close_broke_trendline(self, candidate: TrendlineCandidate, close: float, line: float) -> bool:
        rel = self._touch_tolerance_pct * 0.5
        if candidate.direction == TrendlineDirection.BEAR:
            return close > line * (1.0 + rel)
        return close < line * (1.0 - rel)

    def _update_pressure_touch(self, candidate: TrendlineCandidate, bar: OHLCVBar) -> None:
        if candidate.trendline is None:
            return
        lt = getattr(candidate, "last_touch_ts", None)
        if lt is not None and self._normalize_dt(lt) == self._normalize_dt(bar.ts):
            return
        bars_touch = self._bar_cache.get(candidate.symbol, [])
        cur_idx = len(bars_touch) - 1 if bars_touch else -1
        last_bi = getattr(candidate, "last_touch_bar_index", None)
        if cur_idx >= 0 and last_bi is not None:
            if cur_idx - int(last_bi) < int(self._min_touch_bar_gap):
                return
        line, dist_pct = self._touch_line_distance_pct(candidate, bar)
        if line <= 0:
            return
        close = float(bar.close)
        if self._close_broke_trendline(candidate, close, line):
            return
        bars = self._bar_cache.get(candidate.symbol, [])
        prev_near = False
        if len(bars) >= 2:
            pb = bars[-2]
            pline, pd = self._touch_line_distance_pct(candidate, pb)
            if pline > 0:
                pc = float(pb.close)
                prev_near = pd <= self._touch_tolerance_pct and not self._close_broke_trendline(
                    candidate, pc, pline
                )
        if dist_pct > self._touch_tolerance_pct or prev_near:
            return
        tc = int(getattr(candidate, "touch_count", 0) or 0) + 1
        setattr(candidate, "touch_count", tc)
        setattr(candidate, "pressure_score", float(tc))
        setattr(candidate, "last_touch_ts", bar.ts)
        if cur_idx >= 0:
            setattr(candidate, "last_touch_bar_index", cur_idx)
        log.info(
            "TRENDLINE_PRESSURE_TOUCH | symbol=%s | trade_id=%s | touch_count=%d | pressure_score=%.2f | "
            "distance_from_line_pct=%.6f | line=%.4f | close=%.4f",
            candidate.symbol,
            self._trade_id(candidate),
            tc,
            float(tc),
            dist_pct,
            line,
            close,
        )
        log.info(
            "TRENDLINE_PRESSURE_SCORE | symbol=%s | trade_id=%s | pressure_score=%.2f | touch_count=%d | "
            "distance_from_line_pct=%.6f",
            candidate.symbol,
            self._trade_id(candidate),
            float(tc),
            tc,
            dist_pct,
        )

    def _compute_entry_mode(
        self,
        candidate: TrendlineCandidate,
        break_meta: Dict[str, Any],
        break_quality_score: float,
        break_distance_pct: float = 0.0,
    ) -> str:
        impulse_mode = bool(break_meta.get("impulse_mode"))
        strong_breakout = bool(break_meta.get("strong_breakout"))
        breakout_is_clean = bool(break_meta.get("breakout_is_clean"))
        drift_breakout = bool(break_meta.get("drift_breakout"))
        slow_trend_mode = bool(break_meta.get("slow_trend_mode")) or (
            str(break_meta.get("trendline_mode") or "").upper() == "SLOW_TREND"
        )
        tc = int(getattr(candidate, "touch_count", 0) or 0)
        ps = float(getattr(candidate, "pressure_score", 0.0) or 0.0)
        min_q = float(self.config.min_break_quality_score)
        large_break = float(break_distance_pct) >= float(self._break_distance_min)
        strong_impulse = bool(impulse_mode) and (
            bool(break_meta.get("distance_increasing"))
            or (bool(strong_breakout) and bool(breakout_is_clean))
        )
        if large_break and strong_impulse:
            return "immediate_break"
        if (
            tc >= self._high_pressure_touch_count
            and ps >= self._pressure_score_min
            and break_quality_score >= min_q
        ):
            return "pressure_break"
        if (
            slow_trend_mode
            or drift_breakout
            or int(getattr(candidate, "rearm_checks_done", 0) or 0) > 0
            or int(getattr(candidate, "retest_checks_done", 0) or 0) > 0
            or int(getattr(candidate, "break_attempt_count", 0) or 0) > 1
        ):
            return "delayed_confirmation"
        if tc > 0:
            return "delayed_confirmation"
        return "delayed_confirmation"

    def _structure_emit_metadata(self, candidate: TrendlineCandidate) -> Dict[str, Any]:
        cls = getattr(candidate, "orb_test_failure", None)
        tl = candidate.trendline
        meta = (tl.metadata or {}) if tl else {}
        sp = candidate.setup_payload if isinstance(candidate.setup_payload, dict) else {}
        ex = sp.get("structure_explainability")
        explain_out = dict(ex) if isinstance(ex, dict) else {}
        return {
            "test_side": getattr(cls, "test_side", "none") if cls else "none",
            "failure_type": getattr(cls, "failure_type", "unclear") if cls else "unclear",
            "selected_line_reason": getattr(cls, "selected_line_reason", "") if cls else "",
            "anchor_one_source": meta.get("anchor_one_source"),
            "anchor_two_source": meta.get("anchor_two_source"),
            "anchor_spacing_min": meta.get("anchor_spacing_min"),
            "structure_display_label": str(sp.get("structure_display_label") or ""),
            "structure_explainability": explain_out,
        }

    _CANONICAL_SKIP_REASONS = frozenset(
        {
            "trend_continuation",
            "unclear_structure",
            "insufficient_structure_points",
            "structure_not_mature",
            "weak_break",
            "break_quality",
            "reversal_candle",
            "retest_failed",
            "chop_zone",
            "strong_opposing_trend",
            "final_gate_rejection",
            "low_pressure",
            "missing_selector_trendline",
        }
    )

    def _normalize_skip_reason(self, raw: str) -> str:
        r = (raw or "").strip().lower()
        if not r:
            return "final_gate_rejection"
        if r in self._CANONICAL_SKIP_REASONS:
            return r
        alias_map = {
            "invalid_structure": "unclear_structure",
            "missing_break_event": "final_gate_rejection",
            "strong_bull_trend": "strong_opposing_trend",
            "strong_bear_trend": "strong_opposing_trend",
            "low_expansion_quality": "weak_break",
            "trendline_reject_low_expansion": "weak_break",
            "chop_range_expansion_fail": "chop_zone",
            "insufficient_move_potential": "weak_break",
            "entry_too_far_from_line": "final_gate_rejection",
            "no_follow_through": "final_gate_rejection",
            "weak_pressure": "low_pressure",
            "pressure_too_low": "low_pressure",
        }
        if r.startswith("regime_choppy"):
            return "chop_zone"
        mapped = alias_map.get(r)
        if mapped:
            return mapped
        return "final_gate_rejection"

    def _log_trendline_skip_reason(self, symbol: str, reason: str, **kwargs: Any) -> None:
        canon = self._normalize_skip_reason(reason)
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        raw_suffix = f" raw_reason={reason}" if canon != reason else ""
        log.warning(
            "TRENDLINE_SKIP_REASON | symbol=%s | reason=%s%s%s",
            symbol,
            canon,
            raw_suffix,
            f" | {extra}" if extra else "",
        )

    def _break_timing_minutes(self, candidate: TrendlineCandidate) -> tuple[float, float]:
        """Minutes from 7:30 PT session open reference and from structure-shift bar to break candle."""
        if not candidate.break_event:
            return -1.0, -1.0
        break_dt = self._normalize_dt(candidate.break_event.candle_ts)
        local_break = break_dt.astimezone(self._tz_pt)
        cutoff_local = datetime.combine(local_break.date(), time(7, 30), self._tz_pt)
        cutoff_utc = cutoff_local.astimezone(timezone.utc)
        min730 = (break_dt - cutoff_utc).total_seconds() / 60.0

        shift_ts = getattr(candidate, "structure_shift_ts", None)
        if shift_ts is None:
            origin = getattr(candidate, "structure_maturity_bar_index", None)
            bars = self._bar_cache.get(candidate.symbol, [])
            if origin is not None and bars and int(origin) < len(bars):
                shift_ts = bars[int(origin)].ts
            else:
                return min730, -1.0
        shift_dt = self._normalize_dt(shift_ts)
        min_shift = (break_dt - shift_dt).total_seconds() / 60.0
        return min730, min_shift

    def _line_quality_for_snapshot(self, meta: Dict[str, Any]) -> str:
        raw = meta.get("line_quality")
        if raw in ("good", "ok", "poor"):
            return str(raw)
        try:
            sm = float(meta.get("anchor_spacing_min"))
        except (TypeError, ValueError):
            return "unknown"
        if sm >= 20:
            return "good"
        if sm >= 10:
            return "ok"
        return "poor"

    def _snapshot_entry_mode_for_confidence(
        self,
        candidate: TrendlineCandidate,
        entry_mode: str,
        break_meta: Optional[Dict[str, Any]],
        break_distance_pct: float,
        break_quality_score: Optional[float],
    ) -> str:
        if entry_mode:
            return entry_mode
        if break_quality_score is None or break_meta is None:
            return str(getattr(candidate, "entry_mode", "") or "")
        return self._compute_entry_mode(
            candidate,
            break_meta,
            float(break_quality_score),
            float(break_distance_pct),
        )

    def _confidence_score_snapshot(
        self,
        candidate: TrendlineCandidate,
        break_distance_pct: float,
        body_ratio_break: float,
        entry_mode_resolved: str,
    ) -> float:
        tc = min(3, int(getattr(candidate, "touch_count", 0) or 0))
        score = float(tc) * 1.5
        if float(break_distance_pct) >= float(self._break_distance_min):
            score += 1.0
        if float(body_ratio_break) >= float(self._body_ratio_min_strong):
            score += 1.0
        if entry_mode_resolved == "immediate_break":
            score += 0.5
        if entry_mode_resolved == "pressure_break":
            score += 0.5
        return float(score)

    def _log_trendline_decision_snapshot(
        self,
        candidate: TrendlineCandidate,
        *,
        decision: str,
        skip_reason: str = "",
        bars: Optional[List[OHLCVBar]] = None,
        break_meta: Optional[Dict[str, Any]] = None,
        break_distance_pct: float = 0.0,
        body_ratio_break: float = 0.0,
        expansion_ok: Optional[bool] = None,
        entry_mode: str = "",
        break_quality_score: Optional[float] = None,
        entry_path: str = "",
        velocity_pct: Optional[float] = None,
        expected_move_pct: Optional[float] = None,
        min_expected_move_threshold: Optional[float] = None,
        distance_increasing: Optional[bool] = None,
    ) -> None:
        """
        Emit ``TRENDLINE_DECISION_SNAPSHOT`` at most once per candidate per *bar* (see
        ``decision_logged``). A candidate may produce many snapshots over its lifetime
        (one per bar in which a skip/enter decision is logged); do not treat the log as
        globally unique per candidate.
        """
        if getattr(candidate, "decision_logged", False):
            return
        setattr(candidate, "decision_logged", True)
        cls = getattr(candidate, "orb_test_failure", None)
        ft = getattr(cls, "failure_type", "") if cls else ""
        ts_side = getattr(cls, "test_side", "") if cls else ""
        tl = candidate.trendline
        meta = (tl.metadata or {}) if tl else {}
        tc = int(getattr(candidate, "touch_count", 0) or 0)
        ps = float(getattr(candidate, "pressure_score", 0.0) or 0.0)
        exp_s = "na" if expansion_ok is None else str(expansion_ok).lower()
        skip_disp = (
            self._normalize_skip_reason(skip_reason)
            if decision == "skip"
            else (skip_reason or "none")
        )
        bm = break_meta if break_meta is not None else (
            (candidate.break_event.metadata or {}) if candidate.break_event else {}
        )
        em_resolved = self._snapshot_entry_mode_for_confidence(
            candidate,
            entry_mode,
            bm,
            float(break_distance_pct),
            break_quality_score,
        )
        conf_score = self._confidence_score_snapshot(
            candidate,
            float(break_distance_pct),
            float(body_ratio_break),
            em_resolved,
        )
        line_quality = self._line_quality_for_snapshot(meta)
        src = getattr(candidate, "trendline_line_source", "") or ""
        if src not in ("selector_built", "classified", "prebuilt"):
            sr = str(getattr(candidate, "state_reason", "") or "")
            if sr in ("trendline_selector_built", "trendline_prebuilt"):
                src = "selector_built"
            else:
                src = "classified"
        if src == "prebuilt":
            src = "selector_built"
        min730, min_shift = self._break_timing_minutes(candidate)
        ep_disp = (entry_path or "").strip() or (
            "none" if decision != "enter" else self._infer_entry_path(candidate)
        )
        if decision == "skip" and bars:
            lb = bars[-1]
            candidate.skip_timestamp = self._normalize_dt(lb.ts)
            candidate.skip_price = float(lb.close)
            candidate.missed_win_early_logged = False
        dist_inc_s = "na" if distance_increasing is None else str(bool(distance_increasing)).lower()
        vel_s = "na" if velocity_pct is None else f"{float(velocity_pct):.6f}"
        exp_s2 = "na" if expected_move_pct is None else f"{float(expected_move_pct):.6f}"
        min_exp_s = "na" if min_expected_move_threshold is None else f"{float(min_expected_move_threshold):.6f}"
        cfg = self.config
        log.warning(
            "TRENDLINE_DECISION_SNAPSHOT | symbol=%s | trade_id=%s | direction=%s | decision=%s | skip_reason=%s | "
            "structure_failure_type=%s | structure_test_side=%s | anchor_one_source=%s | anchor_two_source=%s | "
            "anchor_spacing_min=%s | line_quality=%s | touch_count=%d | pressure_score=%.2f | break_distance_pct=%.6f | "
            "body_ratio_break=%.3f | expansion_ok=%s | entry_mode=%s | confidence_score=%.2f | source=%s | "
            "minutes_from_730_to_break=%.2f | minutes_from_structure_shift_to_break=%.2f | entry_path=%s | "
            "velocity_pct=%s | expected_move_pct=%s | min_expected_move_threshold=%s | distance_increasing=%s | "
            "strict_min_break_distance=%.6f | strict_body_ratio_min=%.4f",
            candidate.symbol,
            self._trade_id(candidate),
            candidate.direction.value,
            decision,
            skip_disp if decision == "skip" else "none",
            ft or "none",
            ts_side or "none",
            meta.get("anchor_one_source", "none"),
            meta.get("anchor_two_source", "none"),
            str(meta.get("anchor_spacing_min", "none")),
            line_quality,
            tc,
            ps,
            break_distance_pct,
            body_ratio_break,
            exp_s,
            entry_mode or em_resolved or "none",
            conf_score,
            src,
            min730,
            min_shift,
            ep_disp,
            vel_s,
            exp_s2,
            min_exp_s,
            dist_inc_s,
            float(cfg.strict_min_break_distance),
            float(cfg.body_ratio_min_strict),
        )
        if decision == "enter":
            self._consecutive_skips_session = 0
            self._skip_streak_alert_armed = True
        elif decision == "skip":
            self._consecutive_skips_session += 1
            if self._consecutive_skips_session >= 10 and self._skip_streak_alert_armed:
                log.warning(
                    "TRENDLINE_ALERT | type=high_skip_rate | consecutive_skips=%d | symbol=%s | skip_reason=%s",
                    self._consecutive_skips_session,
                    candidate.symbol,
                    skip_disp,
                )
                self._skip_streak_alert_armed = False

    def _register_emit_skip_missed_win_watch(
        self,
        candidate: TrendlineCandidate,
        ref_bar: OHLCVBar,
        skip_reason: str,
    ) -> None:
        key = candidate.candidate_id or f"{candidate.symbol}:{candidate.direction.value}"
        ref_px = float(ref_bar.close)
        want_up = candidate.direction == TrendlineDirection.BEAR
        sr_norm = self._normalize_skip_reason(skip_reason)
        min730_b, min_shift_b = self._break_timing_minutes(candidate)
        self._emit_skip_watch[key] = {
            "symbol": candidate.symbol,
            "ref_price": ref_px,
            "want_up": want_up,
            "skip_ts": self._normalize_dt(ref_bar.ts),
            "skip_reason": sr_norm,
            "minutes_from_730_to_break": float(min730_b),
            "minutes_from_structure_shift_to_break": float(min_shift_b),
        }

    def _update_missed_win_watchers(self, symbol: str, bar: OHLCVBar) -> None:
        now_ts = self._normalize_dt(bar.ts)
        close = float(bar.close)
        done_keys: List[str] = []
        for key, w in list(self._emit_skip_watch.items()):
            if w.get("symbol") != symbol:
                continue
            ref = float(w.get("ref_price", 0.0) or 0.0)
            if ref <= 0:
                done_keys.append(key)
                continue
            move = (close - ref) / ref if w.get("want_up") else (ref - close) / ref
            if move >= float(self._missed_win_move_pct):
                mins = (now_ts - self._normalize_dt(w["skip_ts"])).total_seconds() / 60.0
                exp_dir = "up" if w.get("want_up") else "down"
                log.info(
                    "TRENDLINE_MISSED_WIN | symbol=%s | expected_direction=%s | skip_reason=%s | "
                    "max_move_pct_after_skip=%.4f | minutes_to_move=%.2f | "
                    "minutes_from_730_to_break=%.2f | minutes_from_structure_shift_to_break=%.2f",
                    symbol,
                    exp_dir,
                    w.get("skip_reason", ""),
                    float(move) * 100.0,
                    mins,
                    float(w.get("minutes_from_730_to_break", -1.0)),
                    float(w.get("minutes_from_structure_shift_to_break", -1.0)),
                )
                done_keys.append(key)
        for k in done_keys:
            self._emit_skip_watch.pop(k, None)

    def _structure_maturity_allows_break(self, candidate: TrendlineCandidate, bar: OHLCVBar) -> bool:
        bars = self._bar_cache.get(candidate.symbol, [])
        origin = getattr(candidate, "structure_maturity_bar_index", None)
        if origin is None or self._min_structure_bars <= 0:
            return True
        if not bars or int(origin) >= len(bars):
            return True
        cur_idx = len(bars) - 1
        bars_since = cur_idx - int(origin)
        origin_bar = bars[int(origin)]
        sec_since = (self._normalize_dt(bar.ts) - self._normalize_dt(origin_bar.ts)).total_seconds()
        ok = bars_since >= self._min_structure_bars and sec_since >= float(self._min_structure_seconds)
        if not ok:
            if not getattr(candidate, "_maturity_fail_logged", False):
                log.info(
                    "TRENDLINE_STRUCTURE_MATURITY_CHECK | symbol=%s | trade_id=%s | passed=false | "
                    "bars_since=%d | seconds_since=%.1f | min_bars=%d | min_seconds=%.1f",
                    candidate.symbol,
                    self._trade_id(candidate),
                    bars_since,
                    sec_since,
                    self._min_structure_bars,
                    float(self._min_structure_seconds),
                )
                setattr(candidate, "_maturity_fail_logged", True)
            return False
        if not getattr(candidate, "_maturity_pass_logged", False):
            log.info(
                "TRENDLINE_STRUCTURE_MATURITY_CHECK | symbol=%s | trade_id=%s | passed=true | "
                "bars_since=%d | seconds_since=%.1f | min_bars=%d | min_seconds=%.1f",
                candidate.symbol,
                self._trade_id(candidate),
                bars_since,
                sec_since,
                self._min_structure_bars,
                float(self._min_structure_seconds),
            )
            setattr(candidate, "_maturity_pass_logged", True)
        return True

    def _emit_time_structure_mature(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> bool:
        origin = getattr(candidate, "structure_maturity_bar_index", None)
        if origin is None or self._min_structure_bars <= 0:
            return True
        if not bars or int(origin) >= len(bars):
            return True
        cur_idx = len(bars) - 1
        bars_since = cur_idx - int(origin)
        origin_bar = bars[int(origin)]
        last_bar = bars[-1]
        sec_since = (
            self._normalize_dt(last_bar.ts) - self._normalize_dt(origin_bar.ts)
        ).total_seconds()
        return bars_since >= self._min_structure_bars and sec_since >= float(self._min_structure_seconds)

    def _effective_break_distance_min_pct(self, break_meta: Optional[Dict[str, Any]]) -> float:
        """Canonical break-distance floor merged with exhaustion easing (single distance concept)."""
        base = float(self._break_distance_min)
        micro_floor = max(1e-6, float(self.config.strict_min_break_distance) * 0.20)
        bm = dict(break_meta or {})
        arc = str(bm.get("break_archetype") or "").strip().upper()
        if arc == "EXHAUSTION_REVERSAL" or str(bm.get("break_archetype") or "").strip().lower() == "impulse_exhaustion":
            eased = base * float(_BREAK_ARCHETYPE_EXHAUSTION_BD_MULT)
            return max(float(eased), micro_floor * float(_BREAK_ARCHETYPE_MICRO_FLOOR_SCALE))
        return base

    def _infer_break_archetype(
        self,
        *,
        bars: List[OHLCVBar],
        candidate: TrendlineCandidate,
        break_meta: Dict[str, Any],
        body_ratio: float,
        break_distance_pct: float,
        expected_move_pct: float,
        dist_inc_meta: bool,
        reversal_slice: Optional[List[OHLCVBar]] = None,
    ) -> str:
        if (
            float(break_distance_pct) < max(1e-6, float(self.config.strict_min_break_distance) * 0.20) * 1.05
            and float(body_ratio) < 0.055
            and not bool(dist_inc_meta)
        ):
            return "catastrophic_micro_break"
        if bars and self._terminal_chop_zone(bars):
            return "chop_fakeout"

        close_meta = break_meta.get("close_position_in_candle")
        close_pos: Optional[float] = None
        if close_meta is not None:
            try:
                close_pos = float(close_meta)
            except (TypeError, ValueError):
                close_pos = None
        if close_pos is None and reversal_slice:
            cp_b = reversal_slice[-1]
            rng = abs(float(cp_b.high) - float(cp_b.low))
            if rng > 1e-12:
                close_pos = (float(cp_b.close) - float(cp_b.low)) / rng

        reversal_candle = bool(
            reversal_slice and self._has_large_opposite_candle(candidate, reversal_slice)
        )
        triple_weak = self._triple_weak_break_quality(
            float(body_ratio), float(break_distance_pct), close_pos
        )

        canon = float(self._break_distance_min)
        exhaustion_body = float(body_ratio) >= float(self._body_ratio_min_strong)
        micro_floor_relaxed = max(1e-6, float(self.config.strict_min_break_distance) * 0.21)

        # Exhaustion-style reversal: reversal pressure on the break candle but constructive hold / drift potential.
        if (
            bool(dist_inc_meta)
            and exhaustion_body
            and bool(reversal_candle)
            and float(break_distance_pct) + 1e-12 < canon * 1.15
            and expected_move_pct >= float(self._min_expected_move_pct_default) * 0.88
            and float(break_distance_pct) >= micro_floor_relaxed
        ):
            return ENTRY_ARCHETYPE_EXHAUSTION

        # Impulse: cleaner distance + body + expansion DNA.
        if (
            float(break_distance_pct) >= canon * 0.92
            and float(body_ratio) >= float(self._body_ratio_min_strong)
            and bool(dist_inc_meta)
        ):
            return ENTRY_ARCHETYPE_IMPULSE

        if triple_weak:
            return ENTRY_ARCHETYPE_DRIFT

        if reversal_candle and float(break_distance_pct) + 1e-12 < canon * 1.12:
            return ENTRY_ARCHETYPE_EXHAUSTION

        if (
            float(break_distance_pct) + 1e-12 < canon * 0.85
            and float(body_ratio) < float(self._body_ratio_min_weak)
        ):
            return ENTRY_ARCHETYPE_DRIFT

        return ENTRY_ARCHETYPE_DRIFT

    def _apply_break_archetype_on_break(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        break_meta: Dict[str, Any],
        body_ratio_now: float,
        expected_move_pct: float,
        reversal_slice: List[OHLCVBar],
    ) -> None:
        if not candidate.break_event or candidate.break_event.metadata is None:
            return
        prior = dict(candidate.break_event.metadata or {})
        arch = self._infer_break_archetype(
            bars=bars,
            candidate=candidate,
            break_meta={**prior, **break_meta},
            body_ratio=float(body_ratio_now),
            break_distance_pct=float(candidate.break_event.break_distance_pct or 0.0),
            expected_move_pct=float(expected_move_pct),
            dist_inc_meta=bool(break_meta.get("distance_increasing")),
            reversal_slice=reversal_slice if reversal_slice else None,
        )
        candidate.break_event.metadata["break_archetype"] = arch
        candidate.break_event.metadata["entry_archetype"] = arch
        log.warning(
            "TRENDLINE_BREAK_ARCHETYPE | symbol=%s | trade_id=%s | archetype=%s | break_distance_pct=%.6f | "
            "body_ratio=%.4f | dist_inc=%s | expected_move_pct=%.6f",
            candidate.symbol,
            self._trade_id(candidate),
            arch,
            float(candidate.break_event.break_distance_pct or 0.0),
            float(body_ratio_now),
            str(bool(break_meta.get("distance_increasing"))).lower(),
            float(expected_move_pct),
        )

    def _near_price_to_line_frac(self, candidate: TrendlineCandidate, bar: OHLCVBar) -> Optional[float]:
        if not candidate.trendline:
            return None
        line_px = float(candidate.trendline.value_at(bar.ts))
        if line_px <= 0:
            return None
        return abs(float(bar.close) - line_px) / max(line_px, 1e-9)

    def _reanchor_break_geometry_would_erase(
        self, candidate: TrendlineCandidate, bars: List[OHLCVBar], rebuilt: TrendlineDefinition
    ) -> bool:
        if len(bars) < 3 or not candidate.trendline:
            return False
        tol = float(_REANCHOR_ERASURE_SHIFT_FRAC)
        look = bars[-3:]
        for b in look:
            old_l = float(candidate.trendline.value_at(b.ts))
            new_l = float(rebuilt.value_at(b.ts))
            if old_l <= 0 or new_l <= 0:
                continue
            c = float(b.close)
            if candidate.direction == TrendlineDirection.BULL:
                line_eps = abs(old_l) * tol * 3.0
                was_broken = c + line_eps < old_l
                now_broken = c + abs(new_l) * tol * 3.0 < new_l if was_broken else True
                if was_broken and not now_broken and abs(old_l - new_l) > abs(old_l) * tol:
                    return True
            else:
                line_eps = abs(old_l) * tol * 3.0
                was_broken = c > old_l + line_eps
                now_broken = c > new_l + abs(new_l) * tol * 3.0 if was_broken else True
                if was_broken and not now_broken and abs(old_l - new_l) > abs(old_l) * tol:
                    return True
        return False

    def _maybe_arm_delayed_continuation(self, candidate: TrendlineCandidate, bars: List[OHLCVBar], fail_reason: str) -> None:
        bm = dict(candidate.break_event.metadata or {}) if candidate.break_event else {}
        arc = str(bm.get("break_archetype") or "")
        if arc in {"catastrophic_micro_break", "chop_fakeout"}:
            return
        if arc == ENTRY_ARCHETYPE_IMPULSE:
            return
        if getattr(candidate, "delayed_continuation_armed", False):
            return
        if not bars or not candidate.break_event:
            return
        now = self._normalize_dt(bars[-1].ts)
        ttl_min = float(_DELAYED_CONTINUATION_TTL_MIN)
        deadline = now + timedelta(minutes=ttl_min)

        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        bbar = None
        for b in bars:
            if self._normalize_dt(b.ts) == ts_n:
                bbar = b
                break
        if bbar is None:
            bbar = bars[-1]
        candidate.delayed_continuation_armed = True
        candidate.delayed_continuation_origin = str(fail_reason or "")
        candidate.delayed_continuation_started_at = now
        candidate.delayed_continuation_deadline_ts = deadline
        candidate.delayed_continuation_bars_seen = 0
        candidate.delayed_continuation_break_low = float(bbar.low)
        candidate.delayed_continuation_break_high = float(bbar.high)
        pb = [b for b in bars if self._normalize_dt(b.ts) >= ts_n]
        candidate.delayed_continuation_ref_em_pct = float(self._compute_expected_move_pct(candidate, pb))

        log.warning(
            "TRENDLINE_DELAYED_CONTINUATION_ARMED | symbol=%s | trade_id=%s | origin=%s | ttl_minutes=%.2f "
            "| break_low=%.6f | break_high=%.6f",
            candidate.symbol,
            self._trade_id(candidate),
            str(fail_reason),
            ttl_min,
            float(candidate.delayed_continuation_break_low or 0.0),
            float(candidate.delayed_continuation_break_high or 0.0),
        )

    def _clear_delayed_continuation_flags(self, candidate: TrendlineCandidate) -> None:
        candidate.delayed_continuation_armed = False
        candidate.delayed_continuation_origin = ""
        candidate.delayed_continuation_started_at = None
        candidate.delayed_continuation_deadline_ts = None
        candidate.delayed_continuation_bars_seen = 0
        candidate.delayed_continuation_break_low = None
        candidate.delayed_continuation_break_high = None
        candidate.delayed_continuation_ref_em_pct = None

    def _try_delayed_continuation_entry(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        bars: List[OHLCVBar],
    ) -> Optional[TrendlineTradeSignal]:
        if (
            not getattr(candidate, "delayed_continuation_armed", False)
            or not candidate.break_event
            or not post_break
        ):
            return None
        arc_raw = str((candidate.break_event.metadata or {}).get("break_archetype") or "")
        arc_norm = self._normalize_entry_archetype(arc_raw)
        if arc_norm == ENTRY_ARCHETYPE_IMPULSE:
            return None
        if arc_norm not in {ENTRY_ARCHETYPE_DRIFT, ENTRY_ARCHETYPE_EXHAUSTION} and arc_raw not in {
            "",
            "delayed_continuation",
        }:
            return None

        now_bar = bars[-1] if bars else None
        if now_bar is None:
            return None
        now = self._normalize_dt(now_bar.ts)
        deadline_ts = getattr(candidate, "delayed_continuation_deadline_ts", None)

        cand_bars_seen = int(getattr(candidate, "delayed_continuation_bars_seen", 0) or 0) + 1
        candidate.delayed_continuation_bars_seen = cand_bars_seen

        log.info(
            "TRENDLINE_DELAYED_CONTINUATION_CHECK | symbol=%s | trade_id=%s | bars_seen=%d | origin=%s",
            candidate.symbol,
            self._trade_id(candidate),
            cand_bars_seen,
            getattr(candidate, "delayed_continuation_origin", "") or "",
        )

        ttl_bars = int(_DELAYED_CONTINUATION_MAX_BARS)

        expire = False
        if deadline_ts is not None and now > self._normalize_dt(deadline_ts):
            expire = True
        if ttl_bars > 0 and cand_bars_seen > ttl_bars:
            expire = True
        if expire:
            origin = getattr(candidate, "delayed_continuation_origin", "") or "ttl"
            self._clear_delayed_continuation_flags(candidate)
            log.warning(
                "TRENDLINE_DELAYED_CONTINUATION_EXPIRED | symbol=%s | trade_id=%s | reason=%s | bars_seen=%d | ttl_bars=%d",
                candidate.symbol,
                self._trade_id(candidate),
                origin,
                cand_bars_seen,
                ttl_bars,
            )
            return None

        bl = getattr(candidate, "delayed_continuation_break_low", None)
        bh = getattr(candidate, "delayed_continuation_break_high", None)
        ext_rel = float(_DELAYED_EXTENSION_REL)
        directional_extreme_ok = False
        if candidate.direction == TrendlineDirection.BULL:
            if bl is not None:
                directional_extreme_ok = float(now_bar.low) <= float(bl) - max(1e-6, abs(float(bl)) * ext_rel)
        else:
            if bh is not None:
                directional_extreme_ok = float(now_bar.high) >= float(bh) + max(1e-6, abs(float(bh)) * ext_rel)

        if not directional_extreme_ok:
            return None

        fresh_em = float(self._compute_expected_move_pct(candidate, post_break))
        ref_em = getattr(candidate, "delayed_continuation_ref_em_pct", None)
        improved_em = True
        if ref_em is not None and float(ref_em) > 1e-12:
            improved_em = fresh_em >= float(ref_em) * float(_DELAYED_EXPECTED_MOVE_BOOST_MULT)

        if not improved_em:
            return None

        line_px = float(candidate.trendline.value_at(now_bar.ts)) if candidate.trendline else 0.0
        prev = bars[-2] if len(bars) >= 2 else None
        dist_inc = False
        if candidate.trendline and prev is not None and line_px > 0:
            line_prev = float(candidate.trendline.value_at(prev.ts))
            if line_prev > 0:
                dist_prev = abs(float(prev.close) - line_prev) / line_prev
                dist_now = abs(float(now_bar.close) - line_px) / line_px
                dist_inc = dist_now > dist_prev * 1.02

        if not bool(dist_inc):
            return None

        if candidate.break_event.metadata is not None:
            candidate.break_event.metadata["break_archetype"] = ENTRY_ARCHETYPE_DRIFT
            candidate.break_event.metadata["delayed_continuation_entry"] = True

        candidate.momentum_confirmation = self.momentum_engine.confirm(
            direction=candidate.direction,
            break_event=candidate.break_event,
            post_break_bars=post_break[-4:] if len(post_break) >= 4 else post_break,
        )
        candidate.break_event.metadata["distance_increasing"] = True

        anti_fail, anti_r = self._fails_anti_chop_entry(candidate, post_break)
        if anti_fail:
            log.info(
                "TRENDLINE_DELAYED_CONTINUATION_CHECK | symbol=%s | trade_id=%s | blocked_antichop=%s",
                candidate.symbol,
                self._trade_id(candidate),
                anti_r,
            )
            return None

        self._clear_delayed_continuation_flags(candidate)
        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "delayed_continuation_entry")

        log.warning(
            "TRENDLINE_DELAYED_CONTINUATION_ENTRY | symbol=%s | trade_id=%s | em_pct=%.6f | bars_since_arm=%d",
            candidate.symbol,
            self._trade_id(candidate),
            fresh_em,
            cand_bars_seen,
        )

        return self._emit_trade_signal(
            candidate,
            float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 0.85)),
        )

    def _strict_pre_emit_pipeline(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        break_meta: Dict[str, Any],
        break_distance_current: float,
    ) -> bool:
        """
        STRUCTURE → TRENDLINE → MATURITY → BREAK QUALITY → anti-chop / trend guards → final gate.
        Returns True if all checks pass; logs and returns False otherwise.
        """
        fail_reason = ""
        if not candidate.break_event:
            fail_reason = "missing_break_event"
        br_body_ratio = 0.0
        dist_inc_meta = bool(break_meta.get("distance_increasing"))
        bq_pass = False
        st_block, st_reason = False, ""
        if not fail_reason:
            br_body_ratio, _bd_ev = self._break_bar_body_ratio(candidate, bars)
            tc_c = int(getattr(candidate, "touch_count", 0) or 0)
            ps_c = float(getattr(candidate, "pressure_score", 0.0) or 0.0)
            high_pressure_ok = tc_c >= self._high_pressure_touch_count and ps_c >= self._pressure_score_min
            hp_relaxed_body = max(
                float(self._body_ratio_min_weak), float(self._body_ratio_min_strong) * 0.8
            )
            effective_min_body_ratio = (
                hp_relaxed_body if high_pressure_ok else float(self._body_ratio_min_strong)
            )
            close_meta = break_meta.get("close_position_in_candle")
            close_pos_pre: Optional[float] = None
            if close_meta is not None:
                try:
                    close_pos_pre = float(close_meta)
                except (TypeError, ValueError):
                    close_pos_pre = self._break_bar_close_position_in_candle(candidate, bars)
            else:
                close_pos_pre = self._break_bar_close_position_in_candle(candidate, bars)

            min_bd = float(self._effective_break_distance_min_pct(break_meta))
            triple_weak = self._triple_weak_break_quality(
                br_body_ratio, float(break_distance_current), close_pos_pre
            )
            composite_ok = self._composite_break_quality_accepts(
                br_body_ratio,
                float(break_distance_current),
                close_pos_pre,
                dist_inc_meta,
                min_bd_floor=min_bd,
            )
            relax_ok, relax_tag = self._strict_body_ratio_advisory_relax(
                candidate,
                bars,
                break_meta,
                float(break_distance_current),
                bool(dist_inc_meta),
                float(br_body_ratio),
                close_pos_pre,
                composite_ok=bool(composite_ok),
            )
            if not composite_ok and relax_ok:
                log.warning(
                    "TRENDLINE_STRICT_BODY_RATIO_ADVISORY | symbol=%s | trade_id=%s | path=strict_pre_emit | "
                    "relaxed_via=%s | break_distance_pct=%.6f | body_ratio_break=%.4f | distance_increasing=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    relax_tag,
                    float(break_distance_current),
                    float(br_body_ratio),
                    str(bool(dist_inc_meta)).lower(),
                )
                composite_ok = True
            high_pressure_path = bool(
                high_pressure_ok
                and dist_inc_meta
                and float(break_distance_current) >= min_bd
                and float(br_body_ratio) >= hp_relaxed_body
            )
            strong_dist_override = self._strong_break_distance_override(
                float(break_distance_current), dist_inc_meta
            )

            brk_slice: List[OHLCVBar] = []
            if candidate.break_event and bars:
                ts_n = self._normalize_dt(candidate.break_event.candle_ts)
                idx_hit = -1
                for i in range(len(bars) - 1, -1, -1):
                    if self._normalize_dt(bars[i].ts) == ts_n:
                        idx_hit = i
                        break
                if idx_hit >= 0:
                    lo = max(0, idx_hit - 1)
                    brk_slice = bars[lo : idx_hit + 1]
                else:
                    brk_slice = bars[-2:] if len(bars) >= 2 else bars[-1:]
            reversal_candle = bool(
                brk_slice and self._has_large_opposite_candle(candidate, brk_slice)
            )

            structural_acceptance, structural_reason = self._has_structural_acceptance(candidate, bars)
            structural_override = bool(
                float(break_distance_current) >= float(self._strong_break_threshold)
                and dist_inc_meta is not None
                and float(self._compute_expected_move_pct(candidate, bars)) >= float(self._min_expected_move_pct_default)
                and bool(structural_acceptance)
            )
            if reversal_candle:
                bq_pass = False
                fail_reason = "reversal_candle"
            elif triple_weak:
                bq_pass = False
                fail_reason = "weak_break"
            else:
                bq_pass = bool(
                    float(break_distance_current) >= min_bd
                    and (composite_ok or high_pressure_path or strong_dist_override)
                )
                if not bq_pass:
                    fail_reason = "break_quality"
            if not bq_pass and structural_override:
                log.warning(
                    "TRENDLINE_STRUCTURAL_OVERRIDE | symbol=%s | trade_id=%s | reason=%s | break_distance=%.6f | expected_move_pct=%.6f | structural_reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    fail_reason or "none",
                    float(break_distance_current),
                    float(self._compute_expected_move_pct(candidate, bars)),
                    structural_reason,
                )
                bq_pass = True
                fail_reason = ""
                log.warning(
                    "TRENDLINE_STRUCTURAL_OVERRIDE_ACCEPTED | symbol=%s | trade_id=%s | path=confirmation_or_retest",
                    candidate.symbol,
                    self._trade_id(candidate),
                )

            bq_score = self._break_quality_score_telemetry(
                br_body_ratio,
                float(break_distance_current),
                close_pos_pre,
                dist_inc_meta,
            )
            self._log_trendline_break_quality_score(
                candidate,
                body_ratio=br_body_ratio,
                break_distance=float(break_distance_current),
                close_position_in_candle=close_pos_pre,
                distance_increasing=dist_inc_meta,
                score=bq_score,
                decision="accepted" if bq_pass else "rejected",
            )
            log.info(
                "TRENDLINE_BREAK_QUALITY | symbol=%s | trade_id=%s | passed=%s | break_distance=%.6f | "
                "body_ratio_break=%.3f | distance_increasing=%s | min_break=%.5f | min_body=%.2f | "
                "high_pressure_body_relax=%s | composite_ok=%s | triple_weak=%s | strong_dist_override=%s | "
                "reversal_candle=%s",
                candidate.symbol,
                self._trade_id(candidate),
                str(bq_pass).lower(),
                break_distance_current,
                br_body_ratio,
                str(dist_inc_meta).lower(),
                float(self._break_distance_min),
                float(effective_min_body_ratio),
                str(high_pressure_ok).lower(),
                str(composite_ok).lower(),
                str(triple_weak).lower(),
                str(strong_dist_override).lower(),
                str(reversal_candle).lower(),
            )
            if not bq_pass:
                skip_sym = candidate.symbol
                if fail_reason == "reversal_candle":
                    if float(break_distance_current) >= float(self._strong_break_threshold) or float(
                        self._compute_expected_move_pct(candidate, bars)
                    ) >= float(self._min_expected_move_pct_default) * 2.0:
                        self._activate_retest_candidate(candidate, "reversal_candle")
                        log.warning(
                            "TRENDLINE_REVERSAL_SOFT_ACCEPT | symbol=%s | trade_id=%s | break_distance=%.6f | expected_move_pct=%.6f | path=retest_watch",
                            skip_sym,
                            self._trade_id(candidate),
                            float(break_distance_current),
                            float(self._compute_expected_move_pct(candidate, bars)),
                        )
                        return False
                cat_sq = self._strict_pre_emit_fail_is_catastrophic(
                    fail_reason=str(fail_reason or ""),
                    triple_weak=triple_weak,
                    break_distance_current=float(break_distance_current),
                    br_body_ratio=float(br_body_ratio),
                    dist_inc_meta=dist_inc_meta,
                    reversal_candle=reversal_candle,
                    bars=bars,
                    candidate=candidate,
                )
                em_pre = float(self._compute_expected_move_pct(candidate, bars))
                sec_pre = self._seconds_since_break_on_bar(candidate, bars)
                opt_pre = self._expected_option_side_for_candidate(candidate)
                if not cat_sq and str(fail_reason or "") != "weak_break":
                    self._log_trendline_false_break_gate(
                        kind="TRENDLINE_FALSE_BREAK_ADVISORY",
                        symbol=skip_sym,
                        trade_id=self._trade_id(candidate),
                        reason=f"strict_break_quality:{fail_reason}",
                        final_action="advisory_pass_to_confirmation",
                        break_distance_pct=float(break_distance_current),
                        body_ratio=float(br_body_ratio),
                        distance_increasing=bool(dist_inc_meta),
                        expected_move_pct=em_pre,
                        selector_structure_ready=bool(getattr(candidate, "structure_hard_gate_passed", False)),
                        pre_execute_passed=False,
                        seconds_since_break=sec_pre,
                        option_side=opt_pre,
                        extra=f"detail={fail_reason}",
                    )
                    bq_pass = True
                    fail_reason = ""
                elif fail_reason == "weak_break":
                    self._log_trendline_skip_reason(
                        skip_sym,
                        "weak_break",
                        break_distance=f"{break_distance_current:.6f}",
                        body_ratio_break=f"{br_body_ratio:.3f}",
                        distance_increasing=str(dist_inc_meta).lower(),
                    )
                    log.warning(
                        "TRENDLINE_FALSE_BREAK_HARD_BLOCK | symbol=%s | trade_id=%s | reason=strict_break_quality_weak_break | "
                        "break_distance_pct=%.6f | body_ratio=%.4f | distance_increasing=%s | expected_move_pct=%.6f | "
                        "selector_structure_ready=%s | pre_execute_passed=%s | seconds_since_break=%.2f | final_action=entry_blocked",
                        skip_sym,
                        self._trade_id(candidate),
                        float(break_distance_current),
                        float(br_body_ratio),
                        str(bool(dist_inc_meta)).lower(),
                        em_pre,
                        str(bool(getattr(candidate, "structure_hard_gate_passed", False))).lower(),
                        "false",
                        sec_pre,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=strict_break_quality | reason=weak_break",
                        skip_sym,
                        self._trade_id(candidate),
                    )
                elif fail_reason == "reversal_candle":
                    self._log_trendline_skip_reason(
                        skip_sym,
                        "reversal_candle",
                        break_distance=f"{break_distance_current:.6f}",
                        body_ratio_break=f"{br_body_ratio:.3f}",
                    )
                    log.warning(
                        "TRENDLINE_FALSE_BREAK_HARD_BLOCK | symbol=%s | trade_id=%s | reason=strict_break_quality_reversal_candle | "
                        "break_distance_pct=%.6f | body_ratio=%.4f | distance_increasing=%s | expected_move_pct=%.6f | "
                        "selector_structure_ready=%s | pre_execute_passed=%s | seconds_since_break=%.2f | final_action=entry_blocked",
                        skip_sym,
                        self._trade_id(candidate),
                        float(break_distance_current),
                        float(br_body_ratio),
                        str(bool(dist_inc_meta)).lower(),
                        em_pre,
                        str(bool(getattr(candidate, "structure_hard_gate_passed", False))).lower(),
                        "false",
                        sec_pre,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=strict_break_quality | reason=reversal_candle",
                        skip_sym,
                        self._trade_id(candidate),
                    )
                else:
                    self._log_trendline_skip_reason(
                        skip_sym,
                        "break_quality",
                        break_distance=f"{break_distance_current:.6f}",
                        body_ratio_break=f"{br_body_ratio:.3f}",
                        distance_increasing=str(dist_inc_meta).lower(),
                    )
                    log.warning(
                        "TRENDLINE_FALSE_BREAK_HARD_BLOCK | symbol=%s | trade_id=%s | reason=strict_break_quality_break_quality | "
                        "break_distance_pct=%.6f | body_ratio=%.4f | distance_increasing=%s | expected_move_pct=%.6f | "
                        "selector_structure_ready=%s | pre_execute_passed=%s | seconds_since_break=%.2f | final_action=entry_blocked",
                        skip_sym,
                        self._trade_id(candidate),
                        float(break_distance_current),
                        float(br_body_ratio),
                        str(bool(dist_inc_meta)).lower(),
                        em_pre,
                        str(bool(getattr(candidate, "structure_hard_gate_passed", False))).lower(),
                        "false",
                        sec_pre,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=strict_break_quality | reason=break_quality",
                        skip_sym,
                        self._trade_id(candidate),
                    )
            elif getattr(candidate, "awaiting_line_retest", False) and not getattr(
                candidate, "line_retest_ok", False
            ):
                fail_reason = "retest_failed"
                self._log_trendline_skip_reason(candidate.symbol, "retest_failed")
                log.warning(
                    "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=line_retest | reason=retest_failed",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
            elif self._terminal_chop_zone(bars):
                fail_reason = "chop_zone"
                log.info(
                    "TRENDLINE_CHOP_FILTER | symbol=%s | trade_id=%s | passed=false | zone=terminal_last_5",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._log_trendline_skip_reason(candidate.symbol, "chop_zone")
                log.warning(
                    "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=chop_filter | reason=chop_zone",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
            else:
                st_block, st_reason = self._strong_opposing_trend_blocks(
                    candidate,
                    bars,
                    option_side=self._expected_option_side_for_candidate(candidate),
                )
                if st_block:
                    fail_reason = st_reason
                    log.info(
                        "TRENDLINE_STRONG_TREND_SKIP | symbol=%s | trade_id=%s | reason=%s | direction=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        st_reason,
                        candidate.direction.value,
                    )
                    self._log_trendline_skip_reason(candidate.symbol, st_reason)
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=strong_trend | reason=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        st_reason,
                    )
                else:
                    valid_structure = bool(getattr(candidate, "structure_hard_gate_passed", False))
                    valid_trendline = candidate.trendline is not None
                    structure_mature = self._emit_time_structure_mature(candidate, bars)
                    break_quality_good = bq_pass
                    not_choppy = not self._terminal_chop_zone(bars)
                    not_opp = not st_block
                    if not all(
                        [
                            valid_structure,
                            valid_trendline,
                            structure_mature,
                            break_quality_good,
                            not_choppy,
                            not_opp,
                        ]
                    ):
                        fail_reason = "final_gate_rejection"
                        if not structure_mature:
                            log.info(
                                "TRENDLINE_STRUCTURE_MATURITY_CHECK | symbol=%s | trade_id=%s | passed=false | phase=pre_emit",
                                candidate.symbol,
                                self._trade_id(candidate),
                            )
                            self._log_trendline_skip_reason(candidate.symbol, "structure_not_mature")
                        self._log_trendline_skip_reason(
                            candidate.symbol,
                            "final_gate_rejection",
                            valid_structure=str(valid_structure).lower(),
                            valid_trendline=str(valid_trendline).lower(),
                            structure_mature=str(structure_mature).lower(),
                            break_quality=str(break_quality_good).lower(),
                            not_choppy=str(not_choppy).lower(),
                            not_opposite_trend=str(not_opp).lower(),
                        )
                        log.warning(
                            "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=final_gate | reason=final_gate_rejection",
                            candidate.symbol,
                            self._trade_id(candidate),
                        )
        if fail_reason and fail_reason != "missing_break_event":
            fb_s = self._bar_cache.get(candidate.symbol, [])
            cur_ix_s = max(0, len(fb_s) - 1) if fb_s else 0
            if self._in_survival_window(candidate, cur_ix_s) and str(fail_reason or "") != "weak_break":
                self._log_trendline_flow_stage(candidate, "survival_window_blocked_invalidation")
                fail_reason = ""
        if fail_reason:
            if fail_reason in {"reversal_candle", "break_quality"}:
                self._maybe_arm_delayed_continuation(candidate, bars, fail_reason)
            ref_bar = bars[-1] if bars else None
            if ref_bar is not None:
                self._register_emit_skip_missed_win_watch(candidate, ref_bar, fail_reason)
            self._log_trendline_decision_snapshot(
                candidate,
                decision="skip",
                skip_reason=fail_reason,
                bars=bars,
                break_meta=break_meta,
                break_distance_pct=break_distance_current,
                body_ratio_break=br_body_ratio,
                expansion_ok=None,
                entry_mode="",
            )
            return False
        return True

    def _break_bar_body_ratio(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> tuple[float, float]:
        """Return (body_ratio_vs_avg, break_distance_pct) for the break candle (advisory / catastrophic only)."""
        if not candidate.break_event or not bars:
            return 0.0, 0.0
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        bbar = None
        idx = len(bars) - 1
        for i in range(len(bars) - 1, -1, -1):
            if self._normalize_dt(bars[i].ts) == ts_n:
                bbar = bars[i]
                idx = i
                break
        if bbar is None:
            bbar = bars[-1]
            idx = len(bars) - 1
        body = abs(float(bbar.close) - float(bbar.open))
        sample = bars[max(0, idx - 3) : idx] or bars[-3:]
        if not sample:
            sample = [bbar]
        avg_body = sum(abs(float(x.close) - float(x.open)) for x in sample) / max(1, len(sample))
        body_ratio = (body / avg_body) if avg_body > 0 else 0.0
        bd = float(candidate.break_event.break_distance_pct or 0.0)
        return float(body_ratio), float(bd)

    def _break_bar_close_position_in_candle(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> Optional[float]:
        """0..1 close position within break candle range (same bar as _break_bar_body_ratio)."""
        if not candidate.break_event or not bars:
            return None
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        bbar = None
        for i in range(len(bars) - 1, -1, -1):
            if self._normalize_dt(bars[i].ts) == ts_n:
                bbar = bars[i]
                break
        if bbar is None:
            bbar = bars[-1]
        candle_range = abs(float(bbar.high) - float(bbar.low))
        if candle_range <= 1e-12:
            return None
        return (float(bbar.close) - float(bbar.low)) / candle_range

    def _strong_break_distance_override(self, break_distance: float, distance_increasing: bool) -> bool:
        """Existing strong-break bar: distance + momentum (used to skip legacy weak-break filter)."""
        return bool(distance_increasing) and float(break_distance) >= float(self._strong_dist_override_min_bd)

    def _post_break_bars_for_relax(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> List[OHLCVBar]:
        if not candidate.break_event or not bars:
            return []
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        return [b for b in bars if self._normalize_dt(b.ts) >= ts_n]

    def _strict_body_ratio_advisory_relax(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        break_meta: Dict[str, Any],
        break_distance_pct: float,
        distance_increasing: bool,
        body_ratio_break: float,
        close_position_in_candle: Optional[float],
        *,
        composite_ok: bool,
    ) -> tuple[bool, str]:
        """
        When distance / delayed continuation / expansion already confirm the break,
        treat strict body-ratio shortfall as advisory (do not hard-veto). Never relaxes
        triple-weak (NVDA/TSLA-style) or non-increasing distance without other confirm paths.
        ``distance_strong`` additionally requires ``_post_break_expansion_strong`` so large
        break distance alone cannot relax weak body quality.
        """
        if composite_ok:
            return False, ""
        if not bool(distance_increasing):
            return False, ""
        if self._triple_weak_break_quality(
            float(body_ratio_break), float(break_distance_pct), close_position_in_candle
        ):
            return False, ""
        post = self._post_break_bars_for_relax(candidate, bars)
        expansion_strong = bool(post) and self._post_break_expansion_strong(
            candidate, post, bars_for_expansion_timing=bars
        )
        delayed_done = bool(break_meta.get("delayed_continuation_entry"))
        dist_strong = bool(
            self._strong_break_distance_override(float(break_distance_pct), True)
            or float(break_distance_pct) >= float(self._strong_break_threshold)
            or bool(break_meta.get("strong_breakout"))
            or (
                bool(break_meta.get("breakout_is_clean"))
                and float(break_distance_pct)
                >= float(self._effective_break_distance_min_pct(break_meta))
            )
        )
        if delayed_done:
            return True, "delayed_continuation_entry"
        if dist_strong and expansion_strong:
            return True, "distance_strong"
        if expansion_strong:
            return True, "expansion_strong"
        return False, ""

    def _composite_break_quality_accepts(
        self,
        body_ratio: float,
        break_distance: float,
        close_position_in_candle: Optional[float],
        distance_increasing: bool,
        *,
        min_bd_floor: Optional[float] = None,
    ) -> bool:
        """
        Composite breakout acceptance (strict pre-emit / weak-break alignment).
        A) strong body  B) moderate body + break  C) momentum close + distance
        D) strong break + distance + minimum body (weak floor)
        """
        br = float(body_ratio)
        bd = float(break_distance)
        cp = float(close_position_in_candle) if close_position_in_candle is not None else None
        dist = bool(distance_increasing)
        strong = float(self._body_ratio_min_strong)
        weak = float(self._body_ratio_min_weak)
        canonical_bd = float(min_bd_floor) if min_bd_floor is not None else float(self._break_distance_min)
        bd_mod = max(float(self._triple_weak_bd_ceiling), canonical_bd)
        commit_min = float(self.config.composite_close_pos_commit_min)
        if br >= strong:
            return True
        if br >= weak and bd >= bd_mod:
            return True
        if cp is not None and cp >= commit_min and dist:
            return True
        d_body_min = weak
        if bd >= float(self._strong_dist_override_min_bd) and dist and br >= d_body_min:
            return True
        return False

    def _triple_weak_break_quality(
        self,
        body_ratio: float,
        break_distance: float,
        close_position_in_candle: Optional[float],
    ) -> bool:
        """Canonical weak_break: all three limbs weak (unknown close treated as 0 for the upper bound)."""
        cp = float(close_position_in_candle) if close_position_in_candle is not None else 0.0
        weak = float(self._body_ratio_min_weak)
        tw_max = float(self.config.triple_weak_close_pos_max)
        return (
            float(body_ratio) < weak
            and float(break_distance) < float(self._triple_weak_bd_ceiling)
            and cp < tw_max
        )

    def _break_quality_score_telemetry(
        self,
        body_ratio: float,
        break_distance: float,
        close_position_in_candle: Optional[float],
        distance_increasing: bool,
    ) -> float:
        """0..1 scalar for logs only (not a trading signal)."""
        cp = float(close_position_in_candle) if close_position_in_candle is not None else 0.0
        dist_w = 1.0 if distance_increasing else 0.0
        return min(
            1.0,
            0.35 * min(float(body_ratio) / 1.25, 1.0)
            + 0.35 * min(float(break_distance) / 0.025, 1.0)
            + 0.20 * min(max(cp, 0.0), 1.0)
            + 0.10 * dist_w,
        )

    def _log_trendline_break_quality_score(
        self,
        candidate: TrendlineCandidate,
        *,
        body_ratio: float,
        break_distance: float,
        close_position_in_candle: Optional[float],
        distance_increasing: bool,
        score: float,
        decision: str,
    ) -> None:
        cp_s = f"{float(close_position_in_candle):.4f}" if close_position_in_candle is not None else "none"
        log.info(
            "TRENDLINE_BREAK_QUALITY_SCORE | symbol=%s | trade_id=%s | body_ratio=%.4f | break_distance=%.6f | "
            "close_position=%s | distance_increasing=%s | score=%.4f | decision=%s",
            candidate.symbol,
            self._trade_id(candidate),
            float(body_ratio),
            float(break_distance),
            cp_s,
            str(bool(distance_increasing)).lower(),
            float(score),
            decision,
        )

    @staticmethod
    def _classify_score(score: float) -> str:
        s = float(score)
        if s >= 0.85:
            return "extreme"
        if s >= 0.65:
            return "strong"
        if s >= 0.45:
            return "moderate"
        return "weak"

    def _continuation_quality_score(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        break_level: float,
    ) -> float:
        if not post_break:
            return 0.0
        look = post_break[-min(6, len(post_break)) :]
        direction_sign = -1.0 if candidate.direction == TrendlineDirection.BULL else 1.0
        net = direction_sign * (float(look[-1].close) - float(look[0].close))
        disp = abs(net) / max(abs(float(break_level) or 1.0), 1e-9)
        overlap = 0.0
        if len(look) >= 2:
            hits = 0
            for b in look:
                lo, hi = float(b.low), float(b.high)
                if lo <= float(break_level) <= hi:
                    hits += 1
            overlap = hits / max(1, len(look))
        retrace = 0.0
        if candidate.direction == TrendlineDirection.BULL:
            worst = max(float(b.close) for b in look)
            retrace = max(0.0, worst - float(look[-1].close)) / max(abs(float(break_level) or 1.0), 1e-9)
        else:
            worst = min(float(b.close) for b in look)
            retrace = max(0.0, float(look[-1].close) - worst) / max(abs(float(break_level) or 1.0), 1e-9)
        persistence = min(1.0, disp / max(float(self._min_expected_move_pct_default) * 1.5, 1e-6))
        overlap_penalty = min(1.0, overlap)
        retrace_penalty = min(1.0, retrace / max(float(self.config.max_break_to_hold_retrace_pct), 1e-6))
        score = 0.60 * persistence + 0.25 * (1.0 - overlap_penalty) + 0.15 * (1.0 - retrace_penalty)
        return max(0.0, min(1.0, score))

    def _log_score_summary(
        self,
        *,
        candidate: TrendlineCandidate,
        break_quality_score: float,
        continuation_quality_score: float,
        expected_move_pct: float,
        executor_called: bool,
        final_decision: str,
    ) -> None:
        structure_quality_score = max(
            0.0,
            min(
                1.0,
                0.55 * min(max(float(getattr(candidate, "pressure_score", 0.0) or 0.0), 0.0) / 4.0, 1.0)
                + 0.45 * min(max(float(getattr(candidate, "touch_count", 0) or 0), 0.0) / 4.0, 1.0),
            ),
        )
        move_score = min(1.0, max(0.0, float(expected_move_pct)) / max(float(self._min_expected_move_pct_default) * 2.0, 1e-6))
        combined = (
            0.35 * structure_quality_score
            + 0.35 * float(break_quality_score)
            + 0.25 * float(continuation_quality_score)
            + 0.05 * move_score
        )
        log.info(
            "TRENDLINE_SCORE_SUMMARY | symbol=%s | structure_quality_score=%.3f | break_quality_score=%.3f | "
            "continuation_quality_score=%.3f | combined_score=%.3f | classification=%s | executor_called=%s | final_decision=%s",
            candidate.symbol,
            structure_quality_score,
            float(break_quality_score),
            float(continuation_quality_score),
            float(combined),
            self._classify_score(combined),
            str(bool(executor_called)).lower(),
            final_decision,
        )

    def _log_hard_veto_audit(
        self,
        *,
        symbol: str,
        stage: str,
        veto_reason: str,
        catastrophic: bool = False,
        stale: bool = False,
        severe_chop: bool = False,
        invalid_structure: bool = False,
        liquidity_block: bool = False,
        executor_called: bool = False,
    ) -> None:
        log.warning(
            "TRENDLINE_HARD_VETO_AUDIT | symbol=%s | stage=%s | veto_reason=%s | catastrophic=%s | stale=%s | "
            "severe_chop=%s | invalid_structure=%s | liquidity_block=%s | executor_called=%s",
            symbol,
            stage,
            veto_reason,
            str(bool(catastrophic)).lower(),
            str(bool(stale)).lower(),
            str(bool(severe_chop)).lower(),
            str(bool(invalid_structure)).lower(),
            str(bool(liquidity_block)).lower(),
            str(bool(executor_called)).lower(),
        )

    def _terminal_chop_zone(self, bars: List[OHLCVBar]) -> bool:
        if len(bars) < 5:
            return False
        last5 = bars[-5:]
        dirs: List[int] = []
        for b in last5:
            o, c = float(b.open), float(b.close)
            if c > o:
                dirs.append(1)
            elif c < o:
                dirs.append(-1)
            else:
                dirs.append(0)
        flips = sum(1 for i in range(1, len(dirs)) if dirs[i] * dirs[i - 1] < 0)
        bodies = [abs(float(b.close) - float(b.open)) for b in last5]
        ref = max(abs(float(last5[-1].close)), 1e-9)
        avg_body_pct = (sum(bodies) / max(1, len(bodies))) / ref
        bias = abs(sum(dirs))
        if flips >= 3 and avg_body_pct < 0.001:
            return True
        if flips >= 2 and bias <= 1 and avg_body_pct < 0.0012:
            return True
        return False

    def _strong_opposing_trend_blocks(
        self, candidate: TrendlineCandidate, bars: List[OHLCVBar], *, option_side: str = ""
    ) -> tuple[bool, str]:
        """
        Tape filter aligned with **option side** (not raw line geometry label).

        - Block **PUT** entries when the recent window is a strong **bull** tape (fighting downside).
        - Block **CALL** entries when the recent window is a strong **bear** tape (fighting upside).
        """
        if len(bars) < 6:
            return False, ""
        look = bars[-25:] if len(bars) >= 25 else bars
        c0, c1 = float(look[0].close), float(look[-1].close)
        base = max(abs(c0), 1e-9)
        cum = (c1 - c0) / base
        if abs(cum) < 0.007:
            return False, ""
        up_closes = sum(1 for i in range(1, len(look)) if float(look[i].close) > float(look[i - 1].close))
        down_closes = sum(1 for i in range(1, len(look)) if float(look[i].close) < float(look[i - 1].close))
        total = max(1, len(look) - 1)
        dominant = max(up_closes, down_closes) / float(total)
        if dominant < 0.55:
            return False, ""
        side = str(option_side or "").strip().lower()
        if side not in ("call", "put"):
            side = self._expected_option_side_for_candidate(candidate)
        if cum > 0 and side == "put":
            return True, "strong_bull_trend"
        if cum < 0 and side == "call":
            return True, "strong_bear_trend"
        return False, ""

    def _maybe_ack_line_retest(self, candidate: TrendlineCandidate, bar: OHLCVBar) -> None:
        if not getattr(candidate, "awaiting_line_retest", False):
            return
        if candidate.trendline is None or candidate.break_event is None:
            return
        bt = self._normalize_dt(candidate.break_event.candle_ts)
        if self._normalize_dt(bar.ts) < bt:
            return
        line = float(candidate.trendline.value_at(bar.ts))
        if line <= 0:
            return
        close = float(bar.close)
        hi, lo = float(bar.high), float(bar.low)
        tol = self._touch_tolerance_pct * 2.5
        dist = min(abs(close - line), abs(lo - line), abs(hi - line)) / max(line, 1e-9)
        near = dist <= tol
        if candidate.direction == TrendlineDirection.BEAR:
            holds = close >= line * (1.0 - tol * 0.35)
        else:
            holds = close <= line * (1.0 + tol * 0.35)
        if near and holds:
            setattr(candidate, "line_retest_ok", True)
            log.info(
                "TRENDLINE_PIPELINE | stage=line_retest | symbol=%s | trade_id=%s | status=satisfied | dist_pct=%.5f",
                candidate.symbol,
                self._trade_id(candidate),
                dist,
            )

    def initialize_candidates(
        self,
        candidates: Iterable[TrendlineCandidate],
        intraday_by_symbol: Dict[str, List[OHLCVBar]],
        orb_context_by_symbol: Dict[str, Dict[str, object]],
        now_utc: Optional[datetime] = None,
    ) -> None:
        """
        Accept 7:30 candidate list for post-7:30 monitoring.

        Trendlines must be supplied by ``trendline_setup_selector`` (selector-built lines only).
        """
        self._confirm_pending_last_log_ts.clear()
        self._session_summary_logged = False
        self._decision_stats = {
            "break_detected": 0,
            "entry_emitted": 0,
            "filtered_small_break": 0,
            "filtered_weak_break": 0,
            "filtered_anti_chop": 0,
            "missed_opportunity": 0,
            "executed": 0,
        }
        self._entry_tracking.clear()
        self._emit_skip_watch.clear()
        self._consecutive_skips_session = 0
        self._skip_streak_alert_armed = True
        self._market_regime, self._market_regime_metrics = self._compute_market_regime(intraday_by_symbol)
        log.warning(
            "TRENDLINE_MARKET_REGIME | regime=%s | avg_range_pct=%.6f | directional_ratio=%.3f | overlap_ratio=%.3f | symbols_used=%d",
            self._market_regime,
            float(self._market_regime_metrics.get("avg_range_pct", 0.0)),
            float(self._market_regime_metrics.get("directional_ratio", 0.0)),
            float(self._market_regime_metrics.get("overlap_ratio", 0.0)),
            int(self._market_regime_metrics.get("symbols_used", 0.0)),
        )
        now = self._normalize_dt(now_utc or datetime.now(timezone.utc))
        struct_cache: Dict[str, OrbTestFailureClassification] = {}
        for candidate in candidates:
            symbol = candidate.symbol
            self._bar_cache[symbol] = list(intraday_by_symbol.get(symbol, []))
            if symbol not in struct_cache:
                struct_cache[symbol] = self.builder.classify_orb_test_failure(
                    symbol,
                    self._bar_cache.get(symbol, []),
                    orb_context_by_symbol.get(symbol, {}),
                )
            orb_cls = struct_cache[symbol]
            setattr(candidate, "orb_test_failure", orb_cls)
            setattr(candidate, "touch_count", 0)
            setattr(candidate, "pressure_score", 0.0)
            setattr(candidate, "last_touch_ts", None)
            setattr(candidate, "last_touch_bar_index", None)
            setattr(candidate, "entry_mode", "")
            setattr(candidate, "awaiting_line_retest", False)
            setattr(candidate, "line_retest_ok", False)
            setattr(candidate, "structure_hard_gate_passed", False)
            setattr(candidate, "_maturity_fail_logged", False)
            setattr(candidate, "_maturity_pass_logged", False)
            setattr(candidate, "low_confidence", False)

            # NOTE:
            # Trendlines must be built by trendline_setup_selector.
            # Engine-side construction is deprecated and disabled in production flow.
            if candidate.trendline is None:
                self._log_trendline_skip_reason(
                    symbol,
                    "missing_selector_trendline",
                    failure_type=str(getattr(orb_cls, "failure_type", "") or ""),
                )
                log.info(
                    "TRENDLINE_LEGACY_PATH_BLOCKED | symbol=%s | detail=engine_internal_build_disabled",
                    symbol,
                )
                continue

            setattr(candidate, "structure_hard_gate_passed", True)
            setup_pl = candidate.setup_payload or {}
            tl_pb = candidate.trendline
            meta_pb = (tl_pb.metadata or {}) if tl_pb else {}
            log.info(
                "TRENDLINE_SELECTOR_STRUCTURE_ACTIVE | symbol=%s | direction=%s | setup_type=%s | "
                "failure_type=%s | selected_line_reason=%s | anchor_one_source=%s | anchor_two_source=%s",
                symbol,
                candidate.direction.value,
                setup_pl.get("setup_type", ""),
                orb_cls.failure_type or "none",
                orb_cls.selected_line_reason or "none",
                meta_pb.get("anchor_one_source", "none"),
                meta_pb.get("anchor_two_source", "none"),
            )
            ft_pb = orb_cls.failure_type or ""
            if (not ft_pb) or ft_pb not in ("failed_downside", "failed_upside"):
                setattr(candidate, "low_confidence", True)
                log.info(
                    "TRENDLINE_SELECTOR_LOW_CONFIDENCE | symbol=%s | direction=%s | failure_type=%s | "
                    "note=structure_classification_not_failed_side_pair",
                    symbol,
                    candidate.direction.value,
                    ft_pb or "none",
                )
            log.info(
                "TRENDLINE_LINE_SELECTED | symbol=%s | direction=%s | reason=selector_built_trendline | setup_type=%s | "
                "structure_failure_type=%s | note=structure_gate_selector_built",
                symbol,
                candidate.direction.value,
                setup_pl.get("setup_type", ""),
                orb_cls.failure_type,
            )

            candidate_key = f"{symbol}:{candidate.direction.value}"
            candidate.candidate_id = candidate_key
            candidate.continuation_pending = False
            candidate.continuation_start_index = 0
            candidate.continuation_reason = ""
            candidate.continuation_max_bars = int(self._continuation_max_bars)
            candidate.rearm_pending = False
            candidate.rearm_origin_reason = ""
            candidate.rearm_started_at = None
            candidate.rearm_checks_done = 0
            candidate.rearm_break_level = None
            candidate.rearm_break_candle_low = None
            candidate.rearm_break_candle_high = None
            candidate.retest_pending = False
            candidate.retest_started_at = None
            candidate.retest_break_level = None
            candidate.retest_direction = ""
            candidate.retest_checks_done = 0
            candidate.mfe_window_logged = {}
            candidate.impulse_pending_confirmation = False
            candidate.impulse_break_high = None
            candidate.impulse_break_low = None
            candidate.break_bar_index = None
            candidate.break_timestamp = None
            candidate.decision_logged = False
            candidate.skip_timestamp = None
            candidate.skip_price = None
            candidate.missed_win_early_logged = False
            self._candidates[candidate_key] = candidate
            self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BUILD, "initialized")

            if self._is_expired(now, candidate):
                self._set_state(candidate, TrendlineCandidateState.EXPIRED, TrendlineReasonCode.EXPIRED_CUTOFF.value)
                log.info(
                    "TRENDLINE_CANDIDATE_EXPIRED | symbol=%s | trade_id=%s | reason=%s | phase=initialize",
                    symbol,
                    f"{symbol}_na",
                    TrendlineReasonCode.EXPIRED_CUTOFF.value,
                )
                log.info(
                    "TRENDLINE_PIPELINE | stage=expired | symbol=%s | trade_id=%s | reason=%s",
                    symbol,
                    f"{symbol}_na",
                    TrendlineReasonCode.EXPIRED_CUTOFF.value,
                )
                self._maybe_log_session_summary(now)
                continue

            self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BREAK, "trendline_selector_built")
            setattr(candidate, "start_time", now)
            bc = self._bar_cache.get(symbol, [])
            sm_idx = max(0, len(bc) - 1)
            setattr(candidate, "structure_maturity_bar_index", sm_idx)
            setattr(
                candidate,
                "structure_shift_ts",
                self._normalize_dt(bc[sm_idx].ts) if bc else now,
            )
            setattr(candidate, "trendline_line_source", "selector_built")
            tls_chk = str(getattr(candidate, "trendline_line_source", "") or "")
            if tls_chk != "selector_built":
                log.error(
                    "TRENDLINE_ERROR | type=unexpected_trendline_source | symbol=%s | source=%s",
                    symbol,
                    tls_chk,
                )
            assert tls_chk == "selector_built"
            self._log_trendline_flow_stage(candidate, "selector_built")
            self._log_trendline_flow_stage(candidate, "monitoring")
            log.info(
                "TRENDLINE_PIPELINE | stage=build | symbol=%s | trade_id=%s | action=use_selector_built | line_geometry=%s | setup_type=%s",
                symbol,
                self._trade_id(candidate),
                candidate.direction.value,
                (candidate.setup_payload or {}).get("setup_type", ""),
            )

    # region LEGACY_NON_PRIME_ENTRY (disabled in production)
    def _legacy_initialize_candidates_engine_build_DISABLED(
        self,
        *,
        symbol: str,
        candidate: TrendlineCandidate,
        orb_cls: OrbTestFailureClassification,
        intraday_by_symbol: Dict[str, List[OHLCVBar]],
        orb_context_by_symbol: Dict[str, Dict[str, object]],
        now: datetime,
    ) -> None:
        """
        Preserved snapshot of pre-unification initialization when the engine classified
        structure, validated anchors, and called ``build_from_intraday_data`` without a
        selector-built line. Never called; kept for audit and extension reference.
        """
        _ = (symbol, candidate, orb_cls, intraday_by_symbol, orb_context_by_symbol, now)
        log.info(
            "TRENDLINE_LEGACY_INIT_SNAPSHOT | symbol=%s | note=non_selector_engine_build_path_not_executed",
            symbol,
        )

    # endregion LEGACY_NON_PRIME_ENTRY (disabled in production)

    def process_new_bar(self, symbol: str, bar: OHLCVBar) -> Optional[TrendlineTradeSignal]:
        """
        Process a new post-7:30 candle and emit trade signal once fully confirmed.
        """
        self._maybe_log_trendline_funnel_summary()
        candidates = [
            c for c in self._candidates.values()
            if c.symbol == symbol and c.state not in {
                TrendlineCandidateState.EXECUTED,
                TrendlineCandidateState.INVALIDATED,
                TrendlineCandidateState.EXPIRED,
            }
        ]
        if not candidates:
            return None
        self._funnel_inc("candidates_seen", len(candidates))

        self._bar_cache.setdefault(symbol, []).append(bar)
        now = self._normalize_dt(bar.ts)
        self._update_missed_win_watchers(symbol, bar)

        for candidate in candidates:
            # Allows one TRENDLINE_DECISION_SNAPSHOT per candidate per bar (not once per candidate lifetime).
            candidate.decision_logged = False
            if self._is_expired(now, candidate):
                self._set_state(candidate, TrendlineCandidateState.EXPIRED, TrendlineReasonCode.EXPIRED_CUTOFF.value)
                log.info(
                    "TRENDLINE_CANDIDATE_EXPIRED | symbol=%s | trade_id=%s | reason=%s | phase=intraday_bar",
                    symbol,
                    self._trade_id(candidate),
                    TrendlineReasonCode.EXPIRED_CUTOFF.value,
                )
                log.info(
                    "TRENDLINE_PIPELINE | stage=expired | symbol=%s | trade_id=%s | reason=%s",
                    symbol,
                    self._trade_id(candidate),
                    TrendlineReasonCode.EXPIRED_CUTOFF.value,
                )
                continue

            if candidate.state == TrendlineCandidateState.WAITING_FOR_BREAK and candidate.trendline:
                self._maybe_reanchor_candidate(candidate, self._bar_cache.get(candidate.symbol, []), now)
                signal = self._handle_break_stage(candidate, bar)
                if signal:
                    return signal

            if candidate.state == TrendlineCandidateState.WAITING_FOR_CONFIRMATION:
                self._maybe_ack_line_retest(candidate, bar)
                max_confirm_minutes = int(self.config.max_break_to_confirm_minutes)
                if (
                    max_confirm_minutes > 0
                    and candidate.break_event
                    and (now - self._normalize_dt(candidate.break_event.candle_ts))
                    > timedelta(minutes=max_confirm_minutes)
                ):
                    if self._breakout_still_trending(candidate):
                        self._warn_confirm_pending(
                            candidate,
                            "confirmation_timeout",
                            "breakout_still_trending | retaining_confirmation_state",
                        )
                        continue
                    self._rearm_for_next_break(candidate, TrendlineReasonCode.LATE_CONFIRMATION.value)
                    log.warning(
                        "TRENDLINE_PENDING_CONFIRMATION_EXPIRED | symbol=%s | trade_id=%s | reason=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        TrendlineReasonCode.LATE_CONFIRMATION.value,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=confirmation_timeout | reason=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        TrendlineReasonCode.LATE_CONFIRMATION.value,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=rearmed | symbol=%s | trade_id=%s | reason=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        TrendlineReasonCode.LATE_CONFIRMATION.value,
                    )
                    self._log_missed_move(candidate, self._bar_cache.get(candidate.symbol, []), reason=TrendlineReasonCode.LATE_CONFIRMATION.value)
                    continue
                signal = self._handle_confirmation_stage(candidate)
                if signal:
                    return signal

            if candidate.state == TrendlineCandidateState.REVERSAL_WATCH:
                signal = self._handle_reversal_watch_stage(candidate)
                if signal:
                    return signal

            if candidate.state == TrendlineCandidateState.READY_TO_EXECUTE:
                mc = candidate.momentum_confirmation
                bqs = 0.72
                if mc and getattr(mc, "metadata", None):
                    try:
                        bqs = float((mc.metadata or {}).get("break_quality_score", bqs))
                    except (TypeError, ValueError):
                        bqs = 0.72
                elif mc:
                    try:
                        bqs = float(self._compute_break_quality_score(mc))
                    except Exception:
                        bqs = 0.72
                try:
                    signal = self._emit_trade_signal(candidate, float(bqs))
                except RuntimeError:
                    continue
                if signal:
                    return signal

            self._maybe_log_missed_win_early(candidate, bar)

        self._maybe_log_session_summary(now)
        post_break_active = [
            c
            for c in candidates
            if c.state
            in (
                TrendlineCandidateState.WAITING_FOR_CONFIRMATION,
                TrendlineCandidateState.REVERSAL_WATCH,
                TrendlineCandidateState.READY_TO_EXECUTE,
            )
        ]
        if post_break_active:
            tnow = time_module.time()
            last_pe = float(self._post_break_no_emit_diag_ts.get(symbol, 0.0) or 0.0)
            if tnow - last_pe >= 180.0:
                self._post_break_no_emit_diag_ts[symbol] = tnow
                parts = [
                    f"{self._trade_id(c)}:{c.state.value}:state_reason={getattr(c, 'state_reason', '') or ''}"
                    for c in post_break_active[:5]
                ]
                log.warning(
                    "TRENDLINE_NO_EMIT_THIS_BAR | symbol=%s | n_post_break=%d | summary=%s | "
                    "hint=pair_logs_TRENDLINE_DECISION_SNAPSHOT|TRENDLINE_SKIP_REASON|TRENDLINE_PIPELINE|stage=entry_blocked",
                    symbol,
                    len(post_break_active),
                    " | ".join(parts),
                )
        return None

    def mark_executed(self, symbol: str) -> None:
        """Mark candidate as executed after downstream executor success."""
        for c in self._candidates.values():
            if c.symbol == symbol and c.state == TrendlineCandidateState.READY_TO_EXECUTE:
                self._set_state(c, TrendlineCandidateState.EXECUTED, "executor_ack")
                self._decision_stats["executed"] += 1
                self._funnel_inc("durable_confirmed")
                track = self._entry_tracking.get(c.symbol) or {}
                track["entry_ts"] = float(track.get("entry_ts", time_module.time()))
                track["max_pnl_pct"] = float(track.get("max_pnl_pct", 0.0))
                track["entry_mode"] = str(getattr(c, "entry_mode", "") or "")
                track["pressure_score"] = float(getattr(c, "pressure_score", 0.0) or 0.0)
                if c.break_event:
                    track["break_distance_pct"] = float(getattr(c.break_event, "break_distance_pct", 0.0) or 0.0)
                orb_c = getattr(c, "orb_test_failure", None)
                track["structure_type"] = getattr(orb_c, "failure_type", "") if orb_c else ""
                self._entry_tracking[c.symbol] = track
        self._maybe_log_session_summary()

    def track_trade_pnl(self, symbol: str, pnl_pct: float) -> None:
        track = self._entry_tracking.get(symbol) or {}
        prev = float(track.get("max_pnl_pct", float("-inf")))
        track["max_pnl_pct"] = max(prev, float(pnl_pct))
        self._entry_tracking[symbol] = track

    def log_trade_close(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        max_pnl_pct: float,
        bars_held: int,
        exit_type: str,
    ) -> None:
        track = self._entry_tracking.get(symbol) or {}
        tracked_max = float(track.get("max_pnl_pct", float(max_pnl_pct)))
        final_max_pnl_pct = max(float(max_pnl_pct), tracked_max)
        efficiency = (float(pnl_pct) / final_max_pnl_pct) if abs(final_max_pnl_pct) > 1e-9 else 0.0
        trade_id = f"{symbol}_{self._normalize_dt(datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%S')}"
        for c in self._candidates.values():
            if c.symbol == symbol:
                trade_id = self._trade_id(c)
                break
        log.info(
            "TRENDLINE_EXIT_SUMMARY | symbol=%s | trade_id=%s | entry_price=%.4f | exit_price=%.4f | pnl_pct=%.4f | max_pnl_pct=%.4f | bars_held=%d | exit_type=%s | efficiency=%.6f",
            symbol,
            trade_id,
            float(entry_price),
            float(exit_price),
            float(pnl_pct),
            final_max_pnl_pct,
            int(bars_held),
            str(exit_type),
            efficiency,
        )
        em = str(track.get("entry_mode", "") or "")
        ps = float(track.get("pressure_score", 0.0) or 0.0)
        bdp = float(track.get("break_distance_pct", 0.0) or 0.0)
        st = str(track.get("structure_type", "") or "")
        if final_max_pnl_pct < float(self._bad_entry_max_favorable_pct) or float(pnl_pct) <= -float(
            self._bad_entry_drawdown_pct
        ):
            log.info(
                "TRENDLINE_BAD_ENTRY | symbol=%s | trade_id=%s | entry_mode=%s | pressure_score=%.2f | "
                "break_distance_pct=%.6f | structure_type=%s | max_favorable_pct=%.4f | pnl_at_close_pct=%.4f",
                symbol,
                trade_id,
                em,
                ps,
                bdp,
                st,
                final_max_pnl_pct,
                float(pnl_pct),
            )

    def _handle_break_stage(
        self,
        candidate: TrendlineCandidate,
        bar: OHLCVBar,
    ) -> Optional[TrendlineTradeSignal]:
        bars = self._bar_cache.get(candidate.symbol, [])
        prev = bars[-2] if len(bars) >= 2 else None
        _cur_ix_ck = max(0, len(bars) - 1) if bars else 0
        if self._is_choppy_structure(candidate, bars):
            if self._in_survival_window(candidate, _cur_ix_ck):
                self._log_trendline_flow_stage(candidate, "survival_window_blocked_invalidation")
                return None
            self._set_state(candidate, TrendlineCandidateState.INVALIDATED, "choppy_structure")
            self._emit_false_break_terminal(candidate, "invalidation", "choppy_structure")
            log.info(
                "TRENDLINE_PIPELINE | stage=invalidated | symbol=%s | trade_id=%s | reason=choppy_structure",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return None
        if not self._structure_maturity_allows_break(candidate, bar):
            return None
        self._update_pressure_touch(candidate, bar)
        event = self.break_detector.detect_break(candidate.trendline, bar, prev)

        if event.status == BreakStatus.REJECTED:
            reason = event.reason or TrendlineReasonCode.MICRO_BREAK_REJECTED.value
            break_meta = event.metadata or {}
            distance_increasing = bool(break_meta.get("distance_increasing"))
            body_ratio = float(
                break_meta.get("body_ratio_vs_prev") or break_meta.get("body_ratio") or 0.0
            )
            if (
                reason == TrendlineReasonCode.MICRO_BREAK_REJECTED.value
                and distance_increasing
                and body_ratio >= float(self._body_ratio_min_strong)
            ):
                log.warning(
                    "TRENDLINE_MICRO_BREAK_ALLOWED | symbol=%s | trade_id=%s | body_ratio=%.3f | distance_increasing=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    body_ratio,
                    str(distance_increasing).lower(),
                )
            else:
                log.warning(
                    "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=break | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    reason,
                )
                log.warning(
                    "TRENDLINE_PIPELINE | stage=confirm_pending | symbol=%s | trade_id=%s | geometry=%s | phase=break_retry | waiting_for_fresh_break | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    candidate.direction.value,
                    reason,
                )
                return None
        if event.status == BreakStatus.NONE:
            return None
        candidate.break_event = event
        candidate.decision_logged = False
        _bc_br = self._bar_cache.get(candidate.symbol, [])
        _brk_ix = max(0, len(_bc_br) - 1) if _bc_br else None
        if candidate.break_bar_index is None and _brk_ix is not None:
            candidate.break_bar_index = _brk_ix
        if getattr(candidate, "break_timestamp", None) is None:
            candidate.break_timestamp = self._normalize_dt(bar.ts)
        self._log_trendline_flow_stage(candidate, "break_detected")
        bd_init = float(event.break_distance_pct or 0.0)
        if bd_init >= float(self._break_distance_min):
            setattr(candidate, "awaiting_line_retest", False)
            setattr(candidate, "line_retest_ok", True)
        else:
            setattr(candidate, "awaiting_line_retest", True)
            setattr(candidate, "line_retest_ok", False)
        self._decision_stats["break_detected"] += 1
        self._funnel_inc("break_detected")
        candidate.break_attempt_count += 1
        candidate.has_broken_once = True
        if candidate.first_break_at is None:
            candidate.first_break_at = event.candle_ts
            log.warning(
                "TRENDLINE_PIPELINE | stage=first_break_detected | symbol=%s | trade_id=%s",
                candidate.symbol,
                self._trade_id(candidate),
            )
        self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_CONFIRMATION, "break_detected")
        body_ratio_now = 0.0
        if bars:
            b0 = bars[-1]
            body = abs(float(b0.close) - float(b0.open))
            sample = bars[-3:] if len(bars) >= 3 else bars
            avg_body = (
                sum(abs(float(x.close) - float(x.open)) for x in sample) / max(1, len(sample))
                if sample
                else 0.0
            )
            body_ratio_now = (body / avg_body) if avg_body > 0 else 0.0
        self._persist_break_body_ratios(
            event,
            body_ratio_vs_avg=body_ratio_now,
            prev_bar=prev,
            break_bar=bar,
        )
        break_meta = event.metadata or {}
        candle_range = abs(float(bar.high) - float(bar.low))
        close_position_in_candle = (
            ((float(bar.close) - float(bar.low)) / candle_range) if candle_range > 1e-12 else None
        )
        post_break_now = [b for b in bars if self._normalize_dt(b.ts) >= self._normalize_dt(event.candle_ts)]
        expected_move_pct = self._compute_expected_move_pct(candidate, post_break_now)
        velocity = (
            abs(float(bar.close) - float(prev.close)) / max(abs(float(prev.close)), 1e-9)
            if prev is not None
            else 0.0
        )
        bm_enrich = dict(break_meta)
        if close_position_in_candle is not None:
            bm_enrich["close_position_in_candle"] = float(close_position_in_candle)
        brk_slice_early: List[OHLCVBar] = []
        if candidate.break_event and bars:
            ts_n_arc = self._normalize_dt(candidate.break_event.candle_ts)
            idx_arc = -1
            for i in range(len(bars) - 1, -1, -1):
                if self._normalize_dt(bars[i].ts) == ts_n_arc:
                    idx_arc = i
                    break
            if idx_arc >= 0:
                lo_a = max(0, idx_arc - 1)
                brk_slice_early = bars[lo_a : idx_arc + 1]
            else:
                brk_slice_early = bars[-2:] if len(bars) >= 2 else bars[-1:]
        self._apply_break_archetype_on_break(
            candidate,
            bars,
            bm_enrich,
            body_ratio_now,
            expected_move_pct,
            brk_slice_early or ([bar] if bar is not None else []),
        )
        log.warning(
            "TRENDLINE_PIPELINE | stage=break_detected | symbol=%s | trade_id=%s | close=%.4f | line=%.4f | reason=%s | "
            "break_distance=%.6f | body_ratio=%.3f | distance_increasing=%s | candle_range=%.6f | velocity=%.6f | close_position_in_candle=%s | expected_move_pct=%.6f",
            candidate.symbol,
            self._trade_id(candidate),
            float(event.close_price),
            float(event.trendline_price),
            event.reason or "",
            float(event.break_distance_pct or 0.0),
            body_ratio_now,
            str(bool(break_meta.get("distance_increasing"))).lower(),
            candle_range,
            velocity,
            f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
            expected_move_pct,
        )
        br_vs_prev_gate = float((event.metadata or {}).get("body_ratio_vs_prev") or 0.0)
        if (
            self._early_entry_enabled
            and br_vs_prev_gate >= float(self._early_entry_body_ratio_min)
            and bool(break_meta.get("distance_increasing"))
        ):
            candidate.momentum_confirmation = self.momentum_engine.confirm(
                direction=candidate.direction,
                break_event=event,
                post_break_bars=bars[-2:] if len(bars) >= 2 else [bar],
            )
            if candidate.break_event is not None and candidate.break_event.metadata is not None:
                candidate.break_event.metadata["trendline_mode"] = "EARLY_ENTRY"
                candidate.break_event.metadata["early_entry_mode"] = True
                candidate.break_event.metadata["entry_size_multiplier"] = float(
                    self._early_entry_size_multiplier
                )
            self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "early_break_entry")
            log.warning(
                "TRENDLINE_EARLY_ENTRY | symbol=%s | trade_id=%s | body_ratio=%.3f | distance_increasing=true | size_multiplier=%.2f",
                candidate.symbol,
                self._trade_id(candidate),
                body_ratio_now,
                float(self._early_entry_size_multiplier),
            )
            return self._emit_trade_signal(
                candidate,
                float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
            )
        is_impulse_break = (
            float(event.break_distance_pct or 0.0) >= float(self._break_distance_min)
            and br_vs_prev_gate >= float(self._impulse_break_body_ratio_min)
            and bool(break_meta.get("distance_increasing"))
            and not self._has_large_opposite_candle(candidate, [bar])
        )
        if is_impulse_break and candidate.break_event and candidate.break_event.metadata is not None:
            if candidate.direction == TrendlineDirection.BULL:
                if close_position_in_candle is None or float(close_position_in_candle) > 0.4:
                    log.warning(
                        "TRENDLINE_IMPULSE_BLOCKED | symbol=%s | trade_id=%s | reason=weak_close_position | close_position_in_candle=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
                    )
                    is_impulse_break = False
            else:
                if close_position_in_candle is None or float(close_position_in_candle) < 0.6:
                    log.warning(
                        "TRENDLINE_IMPULSE_BLOCKED | symbol=%s | trade_id=%s | reason=weak_close_position | close_position_in_candle=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
                    )
                    is_impulse_break = False
        if is_impulse_break and candidate.break_event and candidate.break_event.metadata is not None:
            candidate.break_event.metadata["trendline_mode"] = "IMPULSE"
            candidate.break_event.metadata["impulse_mode"] = True
            candidate.break_event.metadata["impulse_body_ratio"] = body_ratio_now
            candidate.break_event.metadata["impulse_break_distance"] = float(event.break_distance_pct or 0.0)
            candidate.break_event.metadata["close_position_in_candle"] = (
                float(close_position_in_candle) if close_position_in_candle is not None else None
            )
            log.warning(
                "TRENDLINE_IMPULSE_DETECTED | symbol=%s | trade_id=%s | body_ratio=%.3f | break_distance=%.6f | close_position_in_candle=%s",
                candidate.symbol,
                self._trade_id(candidate),
                body_ratio_now,
                float(event.break_distance_pct or 0.0),
                f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
            )
            if self._impulse_confirm_next_candle:
                candidate.impulse_pending_confirmation = True
                candidate.impulse_break_high = float(bar.high)
                candidate.impulse_break_low = float(bar.low)
        self._log_breakout_quality(candidate, bars, break_meta)
        if candidate.impulse_pending_confirmation:
            return None
        if bool(
            break_meta.get("breakout_is_clean")
            or break_meta.get("strong_breakout")
            or break_meta.get("drift_breakout")
            or break_meta.get("distance_increasing")
        ):
            weak_break_fail, _, _ = self._fails_weak_break_filter(
                candidate,
                bars,
                bool(break_meta.get("distance_increasing")),
            )
            if weak_break_fail:
                return None
            distance_fail, _ = self._fails_min_entry_distance(candidate, bars)
            if distance_fail:
                return None
            anti_chop_fail, anti_chop_reason = self._fails_anti_chop_entry(candidate, bars)
            break_distance_pct = float(event.break_distance_pct or 0.0)
            strength_tier = self._break_strength_tier(break_distance_pct)
            if self._classify_extreme_break(candidate, break_distance=break_distance_pct, bars=bars):
                strength_tier = "extreme"
            if anti_chop_fail:
                if strength_tier == "strong":
                    self._log_break_strength_decision(
                        candidate,
                        break_distance=break_distance_pct,
                        strength_tier=strength_tier,
                        anti_chop_bypassed=True,
                        final_decision="allow_entry_immediate_strong_break",
                        extreme_break=False,
                    )
                    if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                        candidate.break_event.metadata["anti_chop_bypass"] = True
                        candidate.break_event.metadata["break_strength_tier"] = strength_tier
                elif strength_tier == "extreme":
                    extreme_ok, _, _ = self._extreme_break_validation(candidate, bars)
                    if not extreme_ok:
                        strength_tier = "strong"
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier="extreme",
                            anti_chop_bypassed=False,
                            final_decision="downgraded_to_strong_validation_failed",
                            extreme_break=True,
                        )
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier="strong",
                            anti_chop_bypassed=True,
                            final_decision="allow_entry_immediate_strong_break",
                            extreme_break=False,
                        )
                        if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                            candidate.break_event.metadata["break_strength_tier"] = "strong"
                            candidate.break_event.metadata["extreme_break"] = False
                            candidate.break_event.metadata["anti_chop_bypass"] = True
                    else:
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier=strength_tier,
                            anti_chop_bypassed=True,
                            final_decision="allow_entry_immediate_extreme_break",
                            extreme_break=True,
                        )
                        if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                            candidate.break_event.metadata["anti_chop_bypass"] = True
                            candidate.break_event.metadata["break_strength_tier"] = strength_tier
                            candidate.break_event.metadata["extreme_break"] = True
                        candidate.momentum_confirmation = self.momentum_engine.confirm(
                            direction=candidate.direction,
                            break_event=event,
                            post_break_bars=bars[-2:] if len(bars) >= 2 else [bar],
                        )
                        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "extreme_break_entry")
                        return self._emit_trade_signal(
                            candidate,
                            float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
                            entry_type_override="TRENDLINE_EXTREME_BREAK_ENTRY",
                            priority_score_override=999.0,
                            bypass_secondary_checks=True,
                        )
                else:
                    self._log_break_strength_decision(
                        candidate,
                        break_distance=break_distance_pct,
                        strength_tier=strength_tier,
                        anti_chop_bypassed=False,
                        final_decision="blocked_or_continuation",
                        extreme_break=False,
                    )
            if anti_chop_fail and strength_tier not in {"strong", "extreme"}:
                impulse_override_ok = bool(break_meta.get("impulse_mode")) and bool(
                    break_meta.get("distance_increasing")
                ) and float(event.break_distance_pct or 0.0) >= float(self._break_distance_min)
                impulse_override_ok = impulse_override_ok and float(
                    event.break_distance_pct or 0.0
                ) >= float(self._break_distance_min)
                if impulse_override_ok and self._breakout_still_trending(candidate):
                    if self._impulse_confirm_next_candle and candidate.impulse_pending_confirmation:
                        return None
                    log.warning(
                        "TRENDLINE_IMPULSE_ENTRY | symbol=%s | trade_id=%s | mode=IMPULSE | anti_chop_override=true",
                        candidate.symbol,
                        self._trade_id(candidate),
                    )
                    candidate.momentum_confirmation = self.momentum_engine.confirm(
                        direction=candidate.direction,
                        break_event=event,
                        post_break_bars=bars[-2:] if len(bars) >= 2 else [bar],
                    )
                    self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "impulse_break_entry")
                    return self._emit_trade_signal(
                        candidate,
                        float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
                    )
                last_distance = abs(float(bar.close) - float(event.trendline_price)) / max(1e-9, float(event.trendline_price))
                self._log_entry_filtered(
                    candidate,
                    "anti_chop",
                    last_distance,
                    0.0,
                    bool(break_meta.get("distance_increasing")),
                )
                self._decision_stats["filtered_anti_chop"] += 1
                breakout_strength = self._compute_breakout_strength(candidate, bars)
                if strength_tier == "mid" and breakout_strength >= self._pullback_strength_threshold:
                    self._log_break_strength_decision(
                        candidate,
                        break_distance=break_distance_pct,
                        strength_tier=strength_tier,
                        anti_chop_bypassed=False,
                        final_decision="continuation_confirmation_only",
                        extreme_break=False,
                    )
                    self._activate_continuation_candidate(candidate, bars, anti_chop_reason)
                    log.warning(
                        "TRENDLINE_CONTINUATION_TRACK | symbol=%s | trade_id=%s | phase=anti_chop | action=continue | reason=%s | strength=%.3f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        anti_chop_reason,
                        breakout_strength,
                    )
                    self._warn_confirm_pending(
                        candidate,
                        "anti_chop",
                        f"waiting_for_pullback_continuation | reason={anti_chop_reason} | strength={breakout_strength:.3f}",
                    )
                else:
                    self._log_break_strength_decision(
                        candidate,
                        break_distance=break_distance_pct,
                        strength_tier=strength_tier,
                        anti_chop_bypassed=False,
                        final_decision="strict_anti_chop_block",
                        extreme_break=False,
                    )
                    self._log_decision_snapshot(candidate, "immediate", "filtered", anti_chop_reason)
                    log.warning(
                        "TRENDLINE_CHOP_REJECT | symbol=%s | trade_id=%s | stage=anti_chop | reason=%s | strength=%.3f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        anti_chop_reason,
                        breakout_strength,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=anti_chop | reason=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        anti_chop_reason,
                    )
                    self._warn_confirm_pending(
                        candidate,
                        "anti_chop",
                        f"waiting_for_cleaner_break | reason={anti_chop_reason}",
                    )
                if anti_chop_reason == "large_opposite_candle":
                    self._activate_reversal_watch(candidate, bars, reason=anti_chop_reason)
                return None
            bd_immediate = float(event.break_distance_pct or 0.0)
            if bd_immediate < float(self._break_distance_min) or not is_impulse_break:
                self._log_trendline_flow_stage(candidate, "monitoring")
                return self._handle_confirmation_stage(candidate)
            candidate.momentum_confirmation = self.momentum_engine.confirm(
                direction=candidate.direction,
                break_event=event,
                post_break_bars=bars[-2:] if len(bars) >= 2 else [bar],
            )
            entry_trigger_type = (
                "clean_break" if bool(break_meta.get("breakout_is_clean"))
                else "drift" if bool(break_meta.get("drift_breakout"))
                else "strong_break" if bool(break_meta.get("strong_breakout"))
                else "first_move"
            )
            log.warning(
                "TRENDLINE_IMMEDIATE_ENTRY | symbol=%s | trade_id=%s",
                candidate.symbol,
                self._trade_id(candidate),
            )
            log.info(
                "TRENDLINE_ENTRY_TRIGGER | symbol=%s | trade_id=%s | type=%s",
                candidate.symbol,
                self._trade_id(candidate),
                entry_trigger_type,
            )
            self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "immediate_breakout_entry")
            self._log_decision_snapshot(candidate, "immediate", "enter", "immediate_breakout_entry")
            return self._emit_trade_signal(
                candidate,
                float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
            )
        return self._handle_confirmation_stage(candidate)

    def _activate_reversal_watch(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        *,
        reason: str,
    ) -> None:
        if not bars or not candidate.trendline:
            return
        rej = bars[-1]
        line_px = float(candidate.trendline.value_at(rej.ts))
        try:
            close_px = float(rej.close)
        except (TypeError, ValueError):
            close_px = line_px
        original_break_direction = "bullish_break" if close_px > line_px else "bearish_break"
        candidate.reversal_watch_active = True
        candidate.reversal_watch_started_at = self._normalize_dt(rej.ts)
        candidate.reversal_watch_start_index = max(0, len(bars) - 1)
        candidate.reversal_original_break_direction = original_break_direction
        candidate.reversal_rejection_high = float(rej.high)
        candidate.reversal_rejection_low = float(rej.low)
        self._set_state(candidate, TrendlineCandidateState.REVERSAL_WATCH, "reversal_watch")
        log.info(
            "TRENDLINE_PIPELINE | stage=reversal_watch_started | symbol=%s | trade_id=%s | reason=%s | "
            "original_break_direction=%s | rejection_high=%.4f | rejection_low=%.4f",
            candidate.symbol,
            self._trade_id(candidate),
            reason,
            original_break_direction,
            float(candidate.reversal_rejection_high or 0.0),
            float(candidate.reversal_rejection_low or 0.0),
        )

    def _handle_reversal_watch_stage(
        self,
        candidate: TrendlineCandidate,
    ) -> Optional[TrendlineTradeSignal]:
        if not candidate.trendline or not candidate.reversal_watch_active:
            self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BREAK, "reversal_watch_invalid")
            return None
        bars = self._bar_cache.get(candidate.symbol, [])
        if len(bars) < 2:
            return None
        curr = bars[-1]
        prev = bars[-2]
        line_curr = float(candidate.trendline.value_at(curr.ts))
        line_prev = float(candidate.trendline.value_at(prev.ts))
        if abs(line_curr) <= 1e-9 or abs(line_prev) <= 1e-9:
            return None

        original_break_direction = str(candidate.reversal_original_break_direction or "unknown")
        prev_rel = float(prev.close) - line_prev
        curr_rel = float(curr.close) - line_curr
        rejection_high = float(candidate.reversal_rejection_high or 0.0)
        rejection_low = float(candidate.reversal_rejection_low or 0.0)
        bars_since_rejection = max(0, len(bars) - 1 - int(candidate.reversal_watch_start_index or 0))
        if bars_since_rejection > int(self._reversal_max_bars_since_rejection):
            candidate.reversal_watch_active = False
            self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BREAK, "reversal_watch_expired")
            log.info(
                "TRENDLINE_PIPELINE | stage=reversal_watch_expired | symbol=%s | trade_id=%s | bars_since_rejection=%d | max_bars=%d",
                candidate.symbol,
                self._trade_id(candidate),
                int(bars_since_rejection),
                int(self._reversal_max_bars_since_rejection),
            )
            return None
        if original_break_direction == "bearish_break":
            reclaim_cross = prev_rel <= 0.0 and curr_rel > 0.0
            beyond_rejection = float(curr.close) > rejection_high if rejection_high > 0.0 else False
        else:
            reclaim_cross = prev_rel >= 0.0 and curr_rel < 0.0
            beyond_rejection = float(curr.close) < rejection_low if rejection_low > 0.0 else False

        sample = bars[-3:] if len(bars) >= 3 else bars
        avg_body = (
            sum(abs(float(b.close) - float(b.open)) for b in sample) / max(1, len(sample))
            if sample
            else 0.0
        )
        body_now = abs(float(curr.close) - float(curr.open))
        body_ratio = (body_now / avg_body) if avg_body > 0 else 0.0
        signed_dist_prev = prev_rel / line_prev
        signed_dist_curr = curr_rel / line_curr
        if original_break_direction == "bearish_break":
            distance_increasing = bool(signed_dist_curr > 0.0 and signed_dist_curr > signed_dist_prev)
        else:
            distance_increasing = bool(signed_dist_curr < 0.0 and signed_dist_curr < signed_dist_prev)
        reclaim_distance = abs(float(curr.close) - float(line_curr)) / max(abs(float(line_curr)), 1e-9)
        reclaim_strength_ok = reclaim_distance >= float(self._reversal_reclaim_min_distance)
        early_reclaim = bars_since_rejection <= int(self._reversal_early_reclaim_bars)
        confirmation_passed = bool(
            reclaim_cross
            and beyond_rejection
            and body_ratio >= float(self._body_ratio_min_strong)
            and distance_increasing
            and reclaim_strength_ok
        )
        log.info(
            "TRENDLINE_REVERSAL_SIGNAL | symbol=%s | trade_id=%s | original_break_direction=%s | reclaim_price=%.4f | "
            "distance_from_line=%.6f | momentum_strength=%.3f | reclaim_distance=%.6f | bars_since_rejection=%d | "
            "early_reclaim=%s | confirmation_passed=%s",
            candidate.symbol,
            self._trade_id(candidate),
            original_break_direction,
            float(curr.close),
            reclaim_distance,
            body_ratio,
            reclaim_distance,
            int(bars_since_rejection),
            str(bool(early_reclaim)).lower(),
            str(bool(confirmation_passed)).lower(),
        )
        if not confirmation_passed:
            return None

        reversal_direction = (
            TrendlineDirection.BEAR
            if original_break_direction == "bearish_break"
            else TrendlineDirection.BULL
        )
        break_distance_abs = abs(float(curr.close) - float(line_curr))
        break_distance_pct = break_distance_abs / max(abs(float(line_curr)), 1e-9)
        candidate.direction = reversal_direction
        candidate.break_event = TrendlineBreakEvent(
            symbol=candidate.symbol,
            direction=reversal_direction,
            status=BreakStatus.CONFIRMED,
            candle_ts=self._normalize_dt(curr.ts),
            close_price=float(curr.close),
            trendline_price=float(line_curr),
            break_distance=float(break_distance_abs),
            break_distance_pct=float(break_distance_pct),
            threshold_used=float(self._break_distance_min),
            reason="reversal_reclaim_confirmed",
            metadata={
                "distance_increasing": True,
                "body_ratio": float(body_ratio),
                "breakout_is_clean": True,
                "strong_breakout": True,
                "trendline_mode": "REVERSAL",
                "reversal_entry": True,
            },
        )
        candidate.momentum_confirmation = self.momentum_engine.confirm(
            direction=reversal_direction,
            break_event=candidate.break_event,
            post_break_bars=bars[-3:] if len(bars) >= 3 else bars,
        )
        candidate.reversal_watch_active = False
        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "reversal_reclaim_confirmed")
        reversal_setup_type = (
            TrendlineSetupType.DESCENDING_RESISTANCE.value
            if reversal_direction == TrendlineDirection.BEAR
            else TrendlineSetupType.ASCENDING_SUPPORT.value
        )
        reversal_trigger = "breakout_up" if reversal_direction == TrendlineDirection.BEAR else "breakdown_down"
        return self._emit_trade_signal(
            candidate,
            float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
            entry_type_override="TRENDLINE_REVERSAL_ENTRY",
            setup_type_override=reversal_setup_type,
            trigger_direction_override=reversal_trigger,
        )

    def _break_strength_tier(self, break_distance: float) -> str:
        d = float(max(0.0, break_distance))
        if d >= float(self._extreme_break_threshold):
            return "extreme"
        if d >= float(self._strong_break_threshold):
            return "strong"
        if d >= float(self._weak_break_threshold):
            return "mid"
        return "weak"

    def _classify_extreme_break(
        self,
        candidate: TrendlineCandidate,
        *,
        break_distance: float,
        bars: List[OHLCVBar],
    ) -> bool:
        d = float(max(0.0, break_distance))
        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        distance_increasing = bool(break_meta.get("distance_increasing"))
        if not distance_increasing and len(bars) >= 2 and candidate.trendline:
            prev = bars[-2]
            curr = bars[-1]
            prev_line = float(candidate.trendline.value_at(prev.ts))
            curr_line = float(candidate.trendline.value_at(curr.ts))
            if abs(prev_line) > 1e-9 and abs(curr_line) > 1e-9:
                prev_rel = (float(prev.close) - prev_line) / prev_line
                curr_rel = (float(curr.close) - curr_line) / curr_line
                if candidate.direction == TrendlineDirection.BEAR:
                    distance_increasing = curr_rel > prev_rel
                else:
                    distance_increasing = curr_rel < prev_rel
        near_extreme = d >= float(self._near_extreme_break_threshold) and d < float(self._extreme_break_threshold)
        classified_as_extreme = bool(
            d >= float(self._extreme_break_threshold)
            or (near_extreme and distance_increasing)
        )
        log.info(
            "TRENDLINE_EXTREME_BREAK_CLASSIFICATION | symbol=%s | trade_id=%s | break_distance=%.6f | "
            "distance_increasing=%s | near_extreme=%s | classified_as_extreme=%s",
            candidate.symbol,
            self._trade_id(candidate),
            d,
            str(bool(distance_increasing)).lower(),
            str(bool(near_extreme)).lower(),
            str(bool(classified_as_extreme)).lower(),
        )
        return classified_as_extreme

    def _log_break_strength_decision(
        self,
        candidate: TrendlineCandidate,
        *,
        break_distance: float,
        strength_tier: str,
        anti_chop_bypassed: bool,
        final_decision: str,
        extreme_break: bool,
    ) -> None:
        log.info(
            "TRENDLINE_BREAK_STRENGTH_DECISION | symbol=%s | trade_id=%s | break_distance=%.6f | strength_tier=%s | "
            "anti_chop_bypassed=%s | final_decision=%s | extreme_break=%s",
            candidate.symbol,
            self._trade_id(candidate),
            float(break_distance),
            str(strength_tier),
            str(bool(anti_chop_bypassed)).lower(),
            str(final_decision),
            str(bool(extreme_break)).lower(),
        )

    def _extreme_break_validation(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
    ) -> tuple[bool, float, bool]:
        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        distance_increasing = bool(break_meta.get("distance_increasing"))
        body_ratio = float(break_meta.get("body_ratio") or 0.0)
        if body_ratio <= 0.0 and bars:
            last = bars[-1]
            sample = bars[-3:] if len(bars) >= 3 else bars
            body = abs(float(last.close) - float(last.open))
            avg_body = (
                sum(abs(float(b.close) - float(b.open)) for b in sample) / max(1, len(sample))
                if sample
                else 0.0
            )
            body_ratio = (body / avg_body) if avg_body > 0 else 0.0
        passed = bool(distance_increasing and body_ratio >= float(self._body_ratio_min_strong))
        log.info(
            "TRENDLINE_EXTREME_BREAK_VALIDATION | symbol=%s | trade_id=%s | body_ratio=%.3f | distance_increasing=%s | passed_validation=%s",
            candidate.symbol,
            self._trade_id(candidate),
            float(body_ratio),
            str(bool(distance_increasing)).lower(),
            str(bool(passed)).lower(),
        )
        return passed, float(body_ratio), bool(distance_increasing)

    def _bars_for_break_event_window(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> List[OHLCVBar]:
        """Break candle plus prior bar (when present) for reversal-style checks. Mirrors strict emit windowing."""
        if not candidate.break_event or not bars:
            return []
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        idx_hit = -1
        for i in range(len(bars) - 1, -1, -1):
            if self._normalize_dt(bars[i].ts) == ts_n:
                idx_hit = i
                break
        if idx_hit >= 0:
            lo = max(0, idx_hit - 1)
            return bars[lo : idx_hit + 1]
        return bars[-2:] if len(bars) >= 2 else bars[-1:]

    def _try_trendline_fast_path_confirmation(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        bars: List[OHLCVBar],
    ) -> Optional[TrendlineTradeSignal]:
        """
        Strong-breakout fast path: skip hold / local continuation / post-break structure gates.
        Strong-breakout override (distance/body/distance_increasing) may bypass strict pre-emit
        for fast-path evaluation only; otherwise strict pipeline must pass. Does not alter
        _strict_pre_emit_pipeline implementation.
        """
        if not bool(getattr(self.config, "fast_path_enabled", True)):
            return None
        if candidate.retest_pending or candidate.rearm_pending:
            return None
        if not candidate.break_event or not candidate.trendline or not bars:
            return None
        break_meta = (candidate.break_event.metadata or {})
        break_distance_current = (
            float(
                abs(
                    float(candidate.break_event.close_price)
                    - float(candidate.break_event.trendline_price)
                )
                / max(abs(float(candidate.break_event.trendline_price)), 1e-9)
            )
            if candidate.break_event
            else 0.0
        )
        br_body_ratio, _ = self._break_bar_body_ratio(candidate, bars)
        dist_inc = bool(break_meta.get("distance_increasing"))
        strong_break_override = (
            float(break_distance_current) >= float(self._fast_path_override_min_bd)
            and float(br_body_ratio) >= float(self._fast_path_override_body_min)
            and dist_inc
        )
        if strong_break_override:
            log.warning(
                "TRENDLINE_FAST_PATH_OVERRIDE | symbol=%s | trade_id=%s | break_distance=%.6f | body_ratio=%.3f | "
                "reason=strong_break_override",
                candidate.symbol,
                self._trade_id(candidate),
                float(break_distance_current),
                float(br_body_ratio),
            )
        elif not self._strict_pre_emit_pipeline(candidate, bars, break_meta, break_distance_current):
            log.info(
                "TRENDLINE_PATH_DECISION | symbol=%s | trade_id=%s | path=normal | break_distance=%.6f | body_ratio=%.3f | "
                "distance_increasing=%s | fast_path_blocker=break_quality_pipeline",
                candidate.symbol,
                self._trade_id(candidate),
                float(break_distance_current),
                float(br_body_ratio),
                str(dist_inc).lower(),
            )
            return None
        brk_slice = self._bars_for_break_event_window(candidate, bars)
        reversal_candle = bool(brk_slice and self._has_large_opposite_candle(candidate, brk_slice))
        close_pos = self._break_bar_close_position_in_candle(candidate, bars)
        triple_weak = self._triple_weak_break_quality(
            float(br_body_ratio), float(break_distance_current), close_pos
        )
        eligible = (
            not triple_weak
            and not reversal_candle
            and float(break_distance_current) >= float(self._fast_path_final_min_bd)
            and float(br_body_ratio) >= float(self._body_ratio_min_strong)
            and dist_inc
        )
        log.info(
            "TRENDLINE_PATH_DECISION | symbol=%s | trade_id=%s | path=%s | break_distance=%.6f | body_ratio=%.3f | "
            "distance_increasing=%s | triple_weak=%s | reversal_candle=%s | strong_break_override=%s",
            candidate.symbol,
            self._trade_id(candidate),
            "fast" if eligible else "normal",
            float(break_distance_current),
            float(br_body_ratio),
            str(dist_inc).lower(),
            str(triple_weak).lower(),
            str(reversal_candle).lower(),
            str(strong_break_override).lower(),
        )
        if not eligible:
            return None
        log.warning(
            "TRENDLINE_FAST_PATH_ENTRY | symbol=%s | trade_id=%s | break_distance=%.6f | body_ratio=%.3f | "
            "distance_increasing=%s | path=fast",
            candidate.symbol,
            self._trade_id(candidate),
            float(break_distance_current),
            float(br_body_ratio),
            str(dist_inc).lower(),
        )
        candidate.continuation_pending = False
        if candidate.break_event.metadata is not None:
            candidate.break_event.metadata["trendline_fast_path"] = True
        candidate.momentum_confirmation = self.momentum_engine.confirm(
            direction=candidate.direction,
            break_event=candidate.break_event,
            post_break_bars=post_break if post_break else bars[-3:] if len(bars) >= 3 else bars,
        )
        confirmation = candidate.momentum_confirmation
        break_quality_score = float(
            (confirmation.metadata or {}).get(
                "break_quality_score", self._compute_break_quality_score(confirmation)
            )
        )
        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "fast_path_breakout_entry")
        self._log_decision_snapshot(candidate, "fast_path", "enter", "fast_path_breakout_entry")
        try:
            if strong_break_override and candidate.break_event.metadata is not None:
                candidate.break_event.metadata["trendline_fast_path_bypass_strict_emit"] = True
            return self._emit_trade_signal(
                candidate,
                break_quality_score,
                entry_type_override="TRENDLINE_FAST_PATH_ENTRY",
            )
        finally:
            if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                candidate.break_event.metadata.pop("trendline_fast_path_bypass_strict_emit", None)

    def _handle_confirmation_stage(self, candidate: TrendlineCandidate) -> Optional[TrendlineTradeSignal]:
        if not candidate.break_event or not candidate.trendline:
            self._set_state(candidate, TrendlineCandidateState.INVALIDATED, TrendlineReasonCode.BUILD_FAILED.value)
            self._emit_false_break_terminal(candidate, "invalidation", TrendlineReasonCode.BUILD_FAILED.value)
            log.info(
                "TRENDLINE_PIPELINE | stage=invalidated | symbol=%s | trade_id=%s | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                TrendlineReasonCode.BUILD_FAILED.value,
            )
            return None

        bars = self._bar_cache.get(candidate.symbol, [])
        break_ts = self._normalize_dt(candidate.break_event.candle_ts)
        post_break = [b for b in bars if self._normalize_dt(b.ts) >= break_ts]
        self._maybe_log_post_break_mfe(candidate, post_break)
        dc_early = self._try_delayed_continuation_entry(candidate, post_break, bars)
        if dc_early is not None:
            return dc_early
        impulse_confirm = self._check_impulse_pending_confirmation(candidate, post_break)
        if impulse_confirm is False:
            return None
        if impulse_confirm is True:
            latest = post_break[-1]
            line_px = float(candidate.trendline.value_at(latest.ts))
            break_distance = (
                abs(float(latest.close) - line_px) / line_px if line_px > 0 else 0.0
            )
            body = abs(float(latest.close) - float(latest.open))
            sample = post_break[-3:] if len(post_break) >= 3 else post_break
            avg_body = (
                sum(abs(float(x.close) - float(x.open)) for x in sample) / max(1, len(sample))
                if sample
                else 0.0
            )
            body_ratio = (body / avg_body) if avg_body > 0 else 0.0
            if candidate.break_event is not None and candidate.break_event.metadata is not None:
                candidate.break_event.metadata["trendline_mode"] = "IMPULSE"
                candidate.break_event.metadata["impulse_mode"] = True
                candidate.break_event.metadata["distance_increasing"] = True
                candidate.break_event.metadata["body_expanding"] = True
                candidate.break_event.metadata["impulse_body_ratio"] = body_ratio
                candidate.break_event.metadata["impulse_break_distance"] = break_distance
            candidate.momentum_confirmation = self.momentum_engine.confirm(
                direction=candidate.direction,
                break_event=candidate.break_event,
                post_break_bars=post_break,
            )
            self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "impulse_mode_confirmed")
            log.warning(
                "TRENDLINE_IMPULSE_ENTRY | symbol=%s | trade_id=%s | mode=IMPULSE | confirm_next_candle=true",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return self._emit_trade_signal(
                candidate,
                float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
            )
        if candidate.rearm_pending:
            rearm_signal = self._handle_rearm_stage(candidate, post_break)
            if rearm_signal:
                return rearm_signal
        if candidate.retest_pending:
            retest_signal = self._handle_retest_stage(candidate, post_break)
            if retest_signal:
                return retest_signal
        if candidate.continuation_pending:
            start = int(candidate.continuation_start_index)
            continuation_slice = post_break[start:] if start < len(post_break) else post_break[-1:]
            bars_since = max(0, len(post_break) - 1 - start)
            log.info(
                "TRENDLINE_CONTINUATION_TRACK | symbol=%s | trade_id=%s | bars_since=%d | max_bars=%d | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                bars_since,
                int(candidate.continuation_max_bars or self._continuation_max_bars),
                candidate.continuation_reason or "",
            )
            broken, broken_reason = self._continuation_structure_broken(candidate, continuation_slice)
            if broken:
                candidate.continuation_pending = False
                log.warning(
                    "TRENDLINE_CONTINUATION_EXPIRED | symbol=%s | trade_id=%s | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    broken_reason,
                )
                self._log_missed_move(candidate, post_break, reason=broken_reason)
                self._rearm_for_next_break(candidate, broken_reason)
                return None
            if self._continuation_window_expired(candidate, post_break):
                cur_ix_ce = max(0, len(bars) - 1)
                if self._in_survival_window(candidate, cur_ix_ce):
                    self._log_trendline_flow_stage(candidate, "survival_window_blocked_invalidation")
                    return None
                candidate.continuation_pending = False
                log.warning(
                    "TRENDLINE_CONTINUATION_EXPIRED | symbol=%s | trade_id=%s | reason=continuation_window_expired",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._log_missed_move(candidate, post_break, reason="continuation_window_expired")
                self._rearm_for_next_break(candidate, "continuation_window_expired")
                return None
        impulse_ok, impulse_metrics = self._detect_impulse_mode(candidate, post_break)
        if impulse_ok:
            if candidate.impulse_pending_confirmation:
                return None
            if candidate.break_event is not None and candidate.break_event.metadata is not None:
                candidate.break_event.metadata["impulse_mode"] = True
                candidate.break_event.metadata["distance_increasing"] = True
                candidate.break_event.metadata["body_expanding"] = True
                candidate.break_event.metadata["impulse_body_ratio"] = float(
                    impulse_metrics.get("body_ratio", 0.0)
                )
                candidate.break_event.metadata["impulse_break_distance"] = float(
                    impulse_metrics.get("break_distance", 0.0)
                )
                candidate.break_event.metadata["close_position_in_candle"] = (
                    float(impulse_metrics.get("close_position_in_candle", -1.0))
                    if float(impulse_metrics.get("close_position_in_candle", -1.0)) >= 0.0
                    else None
                )
            candidate.momentum_confirmation = self.momentum_engine.confirm(
                direction=candidate.direction,
                break_event=candidate.break_event,
                post_break_bars=post_break,
            )
            self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "impulse_mode_entry")
            log.warning(
                "TRENDLINE_IMPULSE_DETECTED | symbol=%s | trade_id=%s | body_ratio=%.3f | break_distance=%.6f | close_position_in_candle=%s | candles_after_break=%d",
                candidate.symbol,
                self._trade_id(candidate),
                float(impulse_metrics.get("body_ratio", 0.0)),
                float(impulse_metrics.get("break_distance", 0.0)),
                (
                    f"{float(impulse_metrics.get('close_position_in_candle', -1.0)):.3f}"
                    if float(impulse_metrics.get("close_position_in_candle", -1.0)) >= 0.0
                    else "none"
                ),
                len(post_break),
            )
            return self._emit_trade_signal(
                candidate,
                float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
            )
        slow_ok, slow_metrics = self._detect_slow_trend_mode(candidate, post_break)
        if slow_ok:
            if candidate.break_event is not None and candidate.break_event.metadata is not None:
                candidate.break_event.metadata["trendline_mode"] = "SLOW_TREND"
                candidate.break_event.metadata["slow_trend_mode"] = True
                candidate.break_event.metadata["slow_trend_consistency"] = float(
                    slow_metrics.get("consistency", 0.0)
                )
                candidate.break_event.metadata["slow_trend_cumulative_break_move"] = float(
                    slow_metrics.get("cumulative_break_move", 0.0)
                )
                candidate.break_event.metadata["distance_increasing"] = True
            candidate.momentum_confirmation = self.momentum_engine.confirm(
                direction=candidate.direction,
                break_event=candidate.break_event,
                post_break_bars=post_break,
            )
            self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "slow_trend_mode_entry")
            log.warning(
                "TRENDLINE_SLOW_TREND_DETECTED | symbol=%s | trade_id=%s | candles=%d | consistency=%.3f | cumulative_break_move=%.6f",
                candidate.symbol,
                self._trade_id(candidate),
                int(slow_metrics.get("candles_used", 0.0)),
                float(slow_metrics.get("consistency", 0.0)),
                float(slow_metrics.get("cumulative_break_move", 0.0)),
            )
            return self._emit_trade_signal(
                candidate,
                float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
            )
        breakout_is_clean = bool((candidate.break_event.metadata or {}).get("breakout_is_clean"))
        strong_breakout = bool((candidate.break_event.metadata or {}).get("strong_breakout"))
        drift_breakout, directional_candles = self._detect_drift_breakout(candidate, post_break)
        if candidate.break_event.metadata is not None:
            candidate.break_event.metadata["drift_breakout"] = drift_breakout
            candidate.break_event.metadata["directional_candles"] = directional_candles
        self._log_breakout_quality(candidate, post_break, candidate.break_event.metadata or {})

        fp_signal = self._try_trendline_fast_path_confirmation(candidate, post_break, bars)
        if fp_signal is not None:
            return fp_signal

        hold_ev = self._check_hold_requirement(candidate, post_break)
        exp_strong_hold = self._post_break_expansion_strong(candidate, post_break, bars_for_expansion_timing=bars)
        if exp_strong_hold and hold_ev.pending and candidate.break_event:
            last_dur = (
                (post_break[-1].ts - candidate.break_event.candle_ts).total_seconds()
                if post_break
                else 0.0
            )
            hold_ev = _HoldEval(True, "", False, max(1, len(post_break)), float(last_dur))
            log.info(
                "TRENDLINE_HOLD_WAIVED_EXPANSION | symbol=%s | trade_id=%s | reason=post_break_expansion_strong",
                candidate.symbol,
                self._trade_id(candidate),
            )
        log.info(
            "TRENDLINE_CONFIRM_CONFIG | symbol=%s | trade_id=%s | confirm_sec=%s",
            candidate.symbol,
            self._trade_id(candidate),
            f"{float(self._confirm_seconds):.1f}",
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=hold_check | symbol=%s | trade_id=%s | hold_mode=%s | hold_seconds=%.1f | hold_bars=%d | passed=%s | pending=%s | duration_sec=%.1f",
            candidate.symbol,
            self._trade_id(candidate),
            self.config.hold_mode,
            self._confirm_seconds,
            self.config.hold_bars_after_break,
            hold_ev.ok,
            hold_ev.pending,
            hold_ev.hold_duration_seconds,
        )
        if hold_ev.pending:
            if (
                strong_breakout
                or breakout_is_clean
                or drift_breakout
                or bool((candidate.break_event.metadata or {}).get("distance_increasing"))
            ) and post_break:
                weak_break_fail, _, _ = self._fails_weak_break_filter(
                    candidate,
                    post_break,
                    bool((candidate.break_event.metadata or {}).get("distance_increasing")),
                )
                if weak_break_fail:
                    return None
                distance_fail, _ = self._fails_min_entry_distance(candidate, post_break)
                if distance_fail:
                    return None
                anti_chop_fail, anti_chop_reason = self._fails_anti_chop_entry(candidate, post_break)
                break_distance_pct = float(getattr(candidate.break_event, "break_distance_pct", 0.0) or 0.0)
                strength_tier = self._break_strength_tier(break_distance_pct)
                if self._classify_extreme_break(candidate, break_distance=break_distance_pct, bars=post_break):
                    strength_tier = "extreme"
                if anti_chop_fail:
                    if strength_tier == "strong":
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier=strength_tier,
                            anti_chop_bypassed=True,
                            final_decision="allow_entry_hold_stage_strong_break",
                            extreme_break=False,
                        )
                        if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                            candidate.break_event.metadata["anti_chop_bypass"] = True
                            candidate.break_event.metadata["break_strength_tier"] = strength_tier
                    elif strength_tier == "extreme":
                        extreme_ok, _, _ = self._extreme_break_validation(candidate, post_break)
                        if not extreme_ok:
                            strength_tier = "strong"
                            self._log_break_strength_decision(
                                candidate,
                                break_distance=break_distance_pct,
                                strength_tier="extreme",
                                anti_chop_bypassed=False,
                                final_decision="downgraded_to_strong_validation_failed",
                                extreme_break=True,
                            )
                            self._log_break_strength_decision(
                                candidate,
                                break_distance=break_distance_pct,
                                strength_tier="strong",
                                anti_chop_bypassed=True,
                                final_decision="allow_entry_hold_stage_strong_break",
                                extreme_break=False,
                            )
                            if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                                candidate.break_event.metadata["break_strength_tier"] = "strong"
                                candidate.break_event.metadata["extreme_break"] = False
                                candidate.break_event.metadata["anti_chop_bypass"] = True
                        else:
                            self._log_break_strength_decision(
                                candidate,
                                break_distance=break_distance_pct,
                                strength_tier=strength_tier,
                                anti_chop_bypassed=True,
                                final_decision="allow_entry_hold_stage_extreme_break",
                                extreme_break=True,
                            )
                            if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                                candidate.break_event.metadata["anti_chop_bypass"] = True
                                candidate.break_event.metadata["break_strength_tier"] = strength_tier
                                candidate.break_event.metadata["extreme_break"] = True
                            candidate.momentum_confirmation = self.momentum_engine.confirm(
                                direction=candidate.direction,
                                break_event=candidate.break_event,
                                post_break_bars=post_break[-3:] if len(post_break) >= 3 else post_break,
                            )
                            self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "extreme_break_entry")
                            return self._emit_trade_signal(
                                candidate,
                                float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
                                entry_type_override="TRENDLINE_EXTREME_BREAK_ENTRY",
                                priority_score_override=999.0,
                                bypass_secondary_checks=True,
                            )
                    else:
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier=strength_tier,
                            anti_chop_bypassed=False,
                            final_decision="blocked_or_continuation",
                            extreme_break=False,
                        )
                if anti_chop_fail and strength_tier not in {"strong", "extreme"}:
                    line_px = candidate.trendline.value_at(post_break[-1].ts) if post_break else 0.0
                    distance = (
                        abs(float(post_break[-1].close) - float(line_px)) / max(1e-9, float(line_px))
                        if post_break and line_px
                        else 0.0
                    )
                    self._log_entry_filtered(
                        candidate,
                        "anti_chop",
                        distance,
                        0.0,
                        bool((candidate.break_event.metadata or {}).get("distance_increasing")),
                    )
                    self._decision_stats["filtered_anti_chop"] += 1
                    breakout_strength = self._compute_breakout_strength(candidate, post_break)
                    if strength_tier == "mid" and breakout_strength >= self._pullback_strength_threshold:
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier=strength_tier,
                            anti_chop_bypassed=False,
                            final_decision="continuation_confirmation_only",
                            extreme_break=False,
                        )
                        self._activate_continuation_candidate(candidate, post_break, anti_chop_reason)
                        log.warning(
                            "TRENDLINE_CONTINUATION_TRACK | symbol=%s | trade_id=%s | phase=anti_chop | action=continue | reason=%s | strength=%.3f",
                            candidate.symbol,
                            self._trade_id(candidate),
                            anti_chop_reason,
                            breakout_strength,
                        )
                        self._warn_confirm_pending(
                            candidate,
                            "anti_chop",
                            f"waiting_for_pullback_continuation | reason={anti_chop_reason} | strength={breakout_strength:.3f}",
                        )
                    else:
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier=strength_tier,
                            anti_chop_bypassed=False,
                            final_decision="strict_anti_chop_block",
                            extreme_break=False,
                        )
                        self._log_decision_snapshot(candidate, "immediate", "filtered", anti_chop_reason)
                        log.warning(
                            "TRENDLINE_CHOP_REJECT | symbol=%s | trade_id=%s | stage=anti_chop | reason=%s | strength=%.3f",
                            candidate.symbol,
                            self._trade_id(candidate),
                            anti_chop_reason,
                            breakout_strength,
                        )
                        log.warning(
                            "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=anti_chop | reason=%s",
                            candidate.symbol,
                            self._trade_id(candidate),
                            anti_chop_reason,
                        )
                        self._warn_confirm_pending(
                            candidate,
                            "anti_chop",
                            f"waiting_for_cleaner_break | reason={anti_chop_reason}",
                        )
                    if anti_chop_reason == "large_opposite_candle":
                        self._activate_reversal_watch(candidate, post_break, reason=anti_chop_reason)
                    return None
                if drift_breakout:
                    log.info(
                        "TRENDLINE_DRIFT_ENTRY | symbol=%s | trade_id=%s | candles=%d",
                        candidate.symbol,
                        self._trade_id(candidate),
                        directional_candles,
                    )
                candidate.momentum_confirmation = self.momentum_engine.confirm(
                    direction=candidate.direction,
                    break_event=candidate.break_event,
                    post_break_bars=post_break,
                )
                log.warning(
                    "TRENDLINE_IMMEDIATE_ENTRY | symbol=%s | trade_id=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                log.info(
                    "TRENDLINE_ENTRY_TRIGGER | symbol=%s | trade_id=%s | type=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    "clean_break" if breakout_is_clean else "drift" if drift_breakout else "first_move",
                )
                self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "breakout_fast_entry")
                self._log_decision_snapshot(candidate, "immediate", "enter", "breakout_fast_entry")
                return self._emit_trade_signal(
                    candidate,
                    float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
                )
            self._warn_confirm_pending(
                candidate,
                "hold",
                f"break_confirmed_awaiting_hold | hold_mode={self.config.hold_mode} | "
                f"elapsed_sec={hold_ev.hold_duration_seconds:.1f} | need_sec={float(self._confirm_seconds):.1f} | "
                f"post_break_bars={len(post_break)}",
            )
            return None
        if not hold_ev.ok:
            hi_or = max(1, int(hold_ev.hold_end_index or 1))
            alt_ok = False
            alt_reason = ""
            if self._post_break_expansion_strong(candidate, post_break, bars_for_expansion_timing=bars):
                alt_ok, alt_reason = True, "expansion"
            elif self.config.require_local_continuation_break and post_break:
                cont_ok, _, _ = self._check_local_continuation(
                    candidate, post_break, hi_or, waive_on_expansion=False
                )
                if cont_ok:
                    alt_ok, alt_reason = True, "local_continuation"
            if not alt_ok and self.config.require_post_break_structure:
                struct_ok, _ = self._check_post_break_structure(candidate, post_break)
                if struct_ok:
                    alt_ok, alt_reason = True, "post_break_structure"
            if alt_ok:
                log.info(
                    "TRENDLINE_NORMAL_GATE_OR | symbol=%s | trade_id=%s | satisfied_by=%s | hold_ok=false",
                    candidate.symbol,
                    self._trade_id(candidate),
                    alt_reason,
                )
                hold_ev = _HoldEval(
                    True,
                    "",
                    False,
                    max(0, len(post_break) - 1),
                    float(hold_ev.hold_duration_seconds),
                )
            elif self._breakout_still_trending(candidate):
                self._warn_confirm_pending(
                    candidate,
                    "hold",
                    f"hold_not_met_but_trending | reason={hold_ev.reason}",
                )
                return None
        if not hold_ev.ok:
            log.warning(
                "TRENDLINE_PIPELINE | stage=hold_failed | symbol=%s | trade_id=%s | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                hold_ev.reason,
            )
            log.warning(
                "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=hold | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                hold_ev.reason,
            )
            self._log_missed_move(candidate, post_break, reason=hold_ev.reason)
            self._rearm_for_next_break(candidate, hold_ev.reason)
            log.warning(
                "TRENDLINE_PIPELINE | stage=rearmed | symbol=%s | trade_id=%s | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                hold_ev.reason,
            )
            self._log_hard_veto_audit(
                symbol=candidate.symbol,
                stage="hold",
                veto_reason=hold_ev.reason,
                invalid_structure=True,
                executor_called=False,
            )
            return None

        if candidate.continuation_pending:
            ready, cont_reason = self._continuation_entry_ready(candidate, post_break)
            if not ready:
                catastrophic_continuation = cont_reason in {
                    "lost_breakout_level_put_path",
                    "lost_breakout_level_call_path",
                    "strong_reversal",
                }
                if cont_reason in {"lost_breakout_level_put_path", "lost_breakout_level_call_path"}:
                    self._activate_retest_candidate(candidate, cont_reason)
                if catastrophic_continuation:
                    self._warn_confirm_pending(
                        candidate,
                        "continuation",
                        f"awaiting_continuation_entry | reason={cont_reason}",
                    )
                    self._log_hard_veto_audit(
                        symbol=candidate.symbol,
                        stage="continuation",
                        veto_reason=cont_reason,
                        catastrophic=("reversal" in str(cont_reason)),
                        invalid_structure=True,
                        executor_called=False,
                    )
                    return None
                log.info(
                    "TRENDLINE_CONTINUATION_ADVISORY | symbol=%s | trade_id=%s | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    cont_reason,
                )
            log.warning(
                "TRENDLINE_CONTINUATION_ENTRY | symbol=%s | trade_id=%s | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                cont_reason,
            )
            candidate.continuation_pending = False

        candidate.hold_end_bar_index = hold_ev.hold_end_index
        candidate.last_hold_duration_seconds = hold_ev.hold_duration_seconds

        # First-move window: allow same/next candle entry after break.
        if len(post_break) <= 2 and post_break:
            weak_break_fail, _, _ = self._fails_weak_break_filter(
                candidate,
                post_break,
                bool((candidate.break_event.metadata or {}).get("distance_increasing")),
            )
            if weak_break_fail:
                return None
            distance_fail, _ = self._fails_min_entry_distance(candidate, post_break)
            if distance_fail:
                return None
            anti_chop_fail, anti_chop_reason = self._fails_anti_chop_entry(candidate, post_break)
            if anti_chop_fail:
                break_distance_pct = float(getattr(candidate.break_event, "break_distance_pct", 0.0) or 0.0)
                strength_tier = self._break_strength_tier(break_distance_pct)
                if self._classify_extreme_break(candidate, break_distance=break_distance_pct, bars=post_break):
                    strength_tier = "extreme"
                if strength_tier == "strong":
                    self._log_break_strength_decision(
                        candidate,
                        break_distance=break_distance_pct,
                        strength_tier=strength_tier,
                        anti_chop_bypassed=True,
                        final_decision="allow_entry_first_move_strong_break",
                        extreme_break=False,
                    )
                    if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                        candidate.break_event.metadata["anti_chop_bypass"] = True
                        candidate.break_event.metadata["break_strength_tier"] = strength_tier
                    candidate.momentum_confirmation = self.momentum_engine.confirm(
                        direction=candidate.direction,
                        break_event=candidate.break_event,
                        post_break_bars=post_break,
                    )
                    self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "first_move_entry")
                    return self._emit_trade_signal(
                        candidate,
                        float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
                    )
                if strength_tier == "extreme":
                    extreme_ok, _, _ = self._extreme_break_validation(candidate, post_break)
                    if not extreme_ok:
                        strength_tier = "strong"
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier="extreme",
                            anti_chop_bypassed=False,
                            final_decision="downgraded_to_strong_validation_failed",
                            extreme_break=True,
                        )
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier="strong",
                            anti_chop_bypassed=True,
                            final_decision="allow_entry_first_move_strong_break",
                            extreme_break=False,
                        )
                        if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                            candidate.break_event.metadata["break_strength_tier"] = "strong"
                            candidate.break_event.metadata["extreme_break"] = False
                            candidate.break_event.metadata["anti_chop_bypass"] = True
                    else:
                        self._log_break_strength_decision(
                            candidate,
                            break_distance=break_distance_pct,
                            strength_tier=strength_tier,
                            anti_chop_bypassed=True,
                            final_decision="allow_entry_first_move_extreme_break",
                            extreme_break=True,
                        )
                        if candidate.break_event and isinstance(candidate.break_event.metadata, dict):
                            candidate.break_event.metadata["anti_chop_bypass"] = True
                            candidate.break_event.metadata["break_strength_tier"] = strength_tier
                            candidate.break_event.metadata["extreme_break"] = True
                        candidate.momentum_confirmation = self.momentum_engine.confirm(
                            direction=candidate.direction,
                            break_event=candidate.break_event,
                            post_break_bars=post_break,
                        )
                        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "extreme_break_entry")
                        return self._emit_trade_signal(
                            candidate,
                            float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
                            entry_type_override="TRENDLINE_EXTREME_BREAK_ENTRY",
                            priority_score_override=999.0,
                            bypass_secondary_checks=True,
                        )
                line_px = candidate.trendline.value_at(post_break[-1].ts) if post_break else 0.0
                distance = (
                    abs(float(post_break[-1].close) - float(line_px)) / max(1e-9, float(line_px))
                    if post_break and line_px
                    else 0.0
                )
                self._log_entry_filtered(
                    candidate,
                    "anti_chop",
                    distance,
                    0.0,
                    bool((candidate.break_event.metadata or {}).get("distance_increasing")),
                )
                self._decision_stats["filtered_anti_chop"] += 1
                breakout_strength = self._compute_breakout_strength(candidate, post_break)
                if strength_tier == "mid" and breakout_strength >= self._pullback_strength_threshold:
                    self._log_break_strength_decision(
                        candidate,
                        break_distance=break_distance_pct,
                        strength_tier=strength_tier,
                        anti_chop_bypassed=False,
                        final_decision="continuation_confirmation_only",
                        extreme_break=False,
                    )
                    self._activate_continuation_candidate(candidate, post_break, anti_chop_reason)
                    log.warning(
                        "TRENDLINE_CONTINUATION_TRACK | symbol=%s | trade_id=%s | phase=anti_chop | action=continue | reason=%s | strength=%.3f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        anti_chop_reason,
                        breakout_strength,
                    )
                else:
                    self._log_break_strength_decision(
                        candidate,
                        break_distance=break_distance_pct,
                        strength_tier=strength_tier,
                        anti_chop_bypassed=False,
                        final_decision="strict_anti_chop_block",
                        extreme_break=False,
                    )
                    self._log_decision_snapshot(candidate, "first_move", "filtered", anti_chop_reason)
                    log.warning(
                        "TRENDLINE_CHOP_REJECT | symbol=%s | trade_id=%s | stage=anti_chop | reason=%s | strength=%.3f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        anti_chop_reason,
                        breakout_strength,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=anti_chop | reason=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        anti_chop_reason,
                    )
                return None
            candidate.momentum_confirmation = self.momentum_engine.confirm(
                direction=candidate.direction,
                break_event=candidate.break_event,
                post_break_bars=post_break,
            )
            log.warning(
                "TRENDLINE_FIRST_MOVE_ENTRY | symbol=%s | trade_id=%s",
                candidate.symbol,
                self._trade_id(candidate),
            )
            log.info("TRENDLINE_ENTRY_TRIGGER | symbol=%s | trade_id=%s | type=first_move", candidate.symbol, self._trade_id(candidate))
            self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "first_move_entry")
            self._log_decision_snapshot(candidate, "first_move", "enter", "first_move_entry")
            return self._emit_trade_signal(
                candidate,
                float((candidate.momentum_confirmation.metadata or {}).get("break_quality_score", 1.0)),
            )

        # Need at least one post-hold bar to continue confirmation checks.
        if len(post_break) <= hold_ev.hold_end_index:
            self._warn_confirm_pending(
                candidate,
                "post_hold",
                f"hold_passed_awaiting_post_hold_bar | hold_end_index={hold_ev.hold_end_index} | "
                f"post_break_bars={len(post_break)}",
            )
            return None

        structure_ok, struct_reason = self._check_post_break_structure(candidate, post_break)
        if not structure_ok and self._post_break_expansion_strong(candidate, post_break, bars_for_expansion_timing=bars):
            structure_ok, struct_reason = True, "expansion_waives_structure_gate"
            log.info(
                "TRENDLINE_NORMAL_GATE_OR | symbol=%s | trade_id=%s | satisfied_by=expansion | stage=structure_waived",
                candidate.symbol,
                self._trade_id(candidate),
            )
        if not structure_ok:
            acc_ok, acc_reason = self._has_structural_acceptance(candidate, post_break)
            if acc_ok:
                structure_ok = True
                struct_reason = acc_reason
                log.warning(
                    "TRENDLINE_ACCEPTANCE_CONFIRMATION | symbol=%s | trade_id=%s | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    acc_reason,
                )
            else:
                log.warning(
                    "TRENDLINE_ACCEPTANCE_REJECT | symbol=%s | trade_id=%s | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    acc_reason,
                )
        if not structure_ok:
            log.warning(
                "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=false | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                struct_reason,
            )
            log.warning(
                "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=structure | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                struct_reason,
            )
            self._warn_confirm_pending(
                candidate,
                "structure",
                f"waiting_for_post_break_structure | reason={struct_reason}",
            )
            return None
        confirmation = self.momentum_engine.confirm(
            direction=candidate.direction,
            break_event=candidate.break_event,
            post_break_bars=post_break,
        )
        candidate.momentum_confirmation = confirmation

        # Emit momentum stage before any pre_execute_check decision logs.
        if candidate.break_event is not None:
            option_side_for_log = (
                "put" if candidate.direction == TrendlineDirection.BULL else "call"
            )
            log.warning(
                "TRENDLINE_PIPELINE | stage=momentum_confirmed | symbol=%s | direction=%s | option_side=%s | status=%s | reason=%s",
                candidate.symbol,
                candidate.direction.value,
                option_side_for_log,
                str(getattr(confirmation, "status", "") or "").lower() or "unknown",
                str(getattr(confirmation, "reason", "") or "").strip() or "none",
            )

        if confirmation.status == MomentumStatus.PENDING:
            self._warn_confirm_pending(
                candidate,
                "momentum",
                f"hold_structure_passed_awaiting_momentum | reason={confirmation.reason or 'pending'} | "
                f"bars_in_window={confirmation.bars_used} | velocity_pct={confirmation.velocity_pct:.6f} | "
                f"range_ratio={confirmation.range_expansion_ratio:.4f}",
            )
            return None
        if confirmation.status == MomentumStatus.FAILED:
            log.warning(
                "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=true | reason=momentum_informational_only:%s",
                candidate.symbol,
                self._trade_id(candidate),
                confirmation.reason,
            )

        break_quality_score = float((confirmation.metadata or {}).get("break_quality_score", self._compute_break_quality_score(confirmation)))
        if break_quality_score < self.config.min_break_quality_score:
            log.warning(
                "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=true | reason=break_quality_informational_only:%.3f_below_%.3f",
                candidate.symbol,
                self._trade_id(candidate),
                break_quality_score,
                self.config.min_break_quality_score,
            )

        log.warning(
            "TRENDLINE_PIPELINE | stage=structure_accepted | symbol=%s | trade_id=%s | direction=%s",
            candidate.symbol,
            self._trade_id(candidate),
            candidate.direction.value,
        )
        self._funnel_inc("structure_accepted")
        self._set_state(candidate, TrendlineCandidateState.READY_TO_EXECUTE, "momentum_confirmed")
        return self._emit_trade_signal(candidate, break_quality_score)

    def _emit_trade_signal(
        self,
        candidate: TrendlineCandidate,
        break_quality_score: float,
        *,
        entry_type_override: Optional[str] = None,
        setup_type_override: Optional[str] = None,
        trigger_direction_override: Optional[str] = None,
        priority_score_override: Optional[float] = None,
        bypass_secondary_checks: bool = False,
    ) -> Optional[TrendlineTradeSignal]:
        if bypass_secondary_checks:
            self._log_trendline_flow_stage(candidate, "entry_triggered")
            self._funnel_inc("selector_reached")
            return self._emit_trade_signal_direct(
                candidate,
                break_quality_score,
                entry_type_override=entry_type_override,
                setup_type_override=setup_type_override,
                trigger_direction_override=trigger_direction_override,
                priority_score_override=priority_score_override,
            )
        if candidate.candidate_id and candidate.candidate_id in self._emitted_candidate_ids:
            # hard dedupe guard for repeated emission across watch loop iterations
            log.info(
                "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=false | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                TrendlineReasonCode.DEDUPE_BLOCK.value,
            )
            raise RuntimeError(f"{TrendlineReasonCode.DEDUPE_BLOCK.value}:{candidate.candidate_id}")
        self._log_trendline_flow_stage(candidate, "entry_triggered")
        self._funnel_inc("selector_reached")
        break_meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        break_ts = self._normalize_dt(candidate.break_event.candle_ts) if candidate.break_event else None
        bars_early = self._bar_cache.get(candidate.symbol, [])
        post_break = (
            [b for b in bars_early if self._normalize_dt(b.ts) >= break_ts]
            if break_ts is not None
            else []
        )
        break_distance_current = (
            float(
                abs(
                    float(candidate.break_event.close_price)
                    - float(candidate.break_event.trendline_price)
                )
                / max(abs(float(candidate.break_event.trendline_price)), 1e-9)
            )
            if candidate.break_event
            else 0.0
        )
        hold_end_index = int(getattr(candidate, "hold_end_bar_index", 1) or 1)
        structure_ok_gate, structure_reason_gate = self._check_post_break_structure(candidate, post_break)
        local_ok_gate, _, local_level_gate = self._check_local_continuation(
            candidate,
            post_break,
            hold_end_index,
            waive_on_expansion=False,
        )
        last_pb = post_break[-1] if post_break else None
        prev_pb = post_break[-2] if len(post_break) >= 2 else None
        velocity_now = (
            abs(float(last_pb.close) - float(prev_pb.close)) / max(abs(float(prev_pb.close)), 1e-9)
            if last_pb is not None and prev_pb is not None
            else 0.0
        )
        reclaimed = False
        if candidate.trendline is not None and post_break:
            for pb in post_break:
                line_px_pb = float(candidate.trendline.value_at(pb.ts))
                if candidate.direction == TrendlineDirection.BULL and float(pb.close) >= line_px_pb:
                    reclaimed = True
                    break
                if candidate.direction == TrendlineDirection.BEAR and float(pb.close) <= line_px_pb:
                    reclaimed = True
                    break
        early_reclaim, late_reclaim = self._reclaim_early_late(candidate, post_break)
        opposite_conflict = bool(self._has_large_opposite_candle(candidate, post_break[-2:] if len(post_break) >= 2 else post_break))
        momentum_agrees = self._momentum_persistence_agrees(candidate, post_break)
        continuation_dist = 0.0
        if local_level_gate is not None and last_pb is not None:
            if candidate.direction == TrendlineDirection.BULL:
                continuation_dist = (float(local_level_gate) - float(last_pb.close)) / max(float(local_level_gate), 1e-9)
            else:
                continuation_dist = (float(last_pb.close) - float(local_level_gate)) / max(float(local_level_gate), 1e-9)
            continuation_dist = max(0.0, float(continuation_dist))
        _tid_loc = self._trade_id(candidate)
        if local_ok_gate and _tid_loc in self._false_break_survival_armed_trade_ids:
            self._false_break_survival_armed_trade_ids.discard(_tid_loc)
        fast_mode = bool(
            break_meta.get("impulse_mode")
            or break_meta.get("early_entry_mode")
            or str(break_meta.get("trendline_mode") or "").upper() in {"IMPULSE", "EARLY_ENTRY"}
        )
        if bool(getattr(self.config, "normal_entry_require_confirmation", True)) and not fast_mode:
            confirm_needed = max(1, int(self.config.confirmation_window_bars))
            if len(post_break) < confirm_needed:
                log.info(
                    "TRENDLINE_PENDING_CONFIRMATION | symbol=%s | trade_id=%s | mode=normal_confirmed_break | post_break_bars=%d | required_bars=%d",
                    candidate.symbol,
                    self._trade_id(candidate),
                    len(post_break),
                    confirm_needed,
                )
                return None
            arch_raw_gate = str(break_meta.get("break_archetype") or "")
            arch_norm_gate = self._normalize_entry_archetype(arch_raw_gate)
            last_pb2 = post_break[-1] if post_break else None
            line_ix = (
                float(candidate.trendline.value_at(last_pb2.ts))
                if (last_pb2 is not None and candidate.trendline is not None)
                else 1.0
            )
            cur_dist_pct = (
                abs(float(last_pb2.close) - line_ix) / max(abs(line_ix), 1e-9) if last_pb2 is not None else 0.0
            )
            opp_body_ratio_pre = self._opposite_body_ratio_vs_break(candidate, bars_early, post_break)
            opt_side_gate = self._expected_option_side_for_candidate(candidate)
            if early_reclaim:
                log.warning(
                    "TRENDLINE_BOUNCEBACK_BLOCK | symbol=%s | trade_id=%s | side=%s | archetype=%s | bars_since_break=%d | "
                    "seconds_since_break=%.2f | break_distance_pct=%.6f | current_distance_pct=%.6f | distance_increasing=%s | "
                    "reclaim_detected=true | opposite_body_ratio=%.4f | continuation_slope_score=n/a | score=n/a | threshold=n/a | "
                    "final_action=%s | detail=early_reclaim_within_%d_bars",
                    candidate.symbol,
                    self._trade_id(candidate),
                    opt_side_gate,
                    arch_norm_gate,
                    len(post_break),
                    float(self._seconds_since_break_on_bar(candidate, bars_early)),
                    float(break_distance_current),
                    float(cur_dist_pct),
                    str(bool(break_meta.get("distance_increasing"))).lower(),
                    float(opp_body_ratio_pre),
                    "invalidate",
                    int(self.config.bounceback_reclaim_max_bars),
                )
                self._set_state(candidate, TrendlineCandidateState.INVALIDATED, "trendline_bounceback_early_reclaim")
                self._funnel_blocked("false_break")
                return None
            if opposite_conflict:
                log.warning(
                    "TRENDLINE_MOMENTUM_CONFLICT_BLOCKED | symbol=%s | trade_id=%s | mode=normal_confirmed_break | reason=large_opposite_candle",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._funnel_blocked("false_break")
                return None
            allow_relaxed_path = bool(local_ok_gate)
            thresh_gate = self._entry_score_threshold_for(arch_raw_gate)
            if not local_ok_gate:
                confirm_needed2 = max(1, int(self.config.confirmation_window_bars))
                min_bars_local = confirm_needed2 + int(self._entry_survival_extra_bars(arch_raw_gate))
                surv_sec = float(self._entry_survival_seconds(arch_raw_gate))
                sec_since = self._seconds_since_break_on_bar(candidate, bars_early)
                br_loc, _bd_loc = self._break_bar_body_ratio(candidate, bars_early)
                em_loc = self._compute_expected_move_pct(candidate, post_break)
                opt_loc = self._expected_option_side_for_candidate(candidate)
                if len(post_break) < min_bars_local or sec_since < surv_sec:
                    self._false_break_survival_armed_trade_ids.add(self._trade_id(candidate))
                    comp_defer, bd_def = self._compute_composite_entry_score(
                        candidate,
                        post_break,
                        bars_early,
                        break_meta=break_meta,
                        break_distance_current=float(break_distance_current),
                        local_ok_gate=False,
                        structure_ok_gate=bool(structure_ok_gate),
                        continuation_dist=float(continuation_dist),
                        velocity_now=float(velocity_now),
                        reclaimed=reclaimed,
                        early_reclaim=False,
                        late_reclaim=late_reclaim,
                        opposite_conflict=False,
                        arch_raw=arch_raw_gate,
                    )
                    slope_d = float(bd_def.get("slope", 0.0))
                    log.warning(
                        "TRENDLINE_ENTRY_SCORE_SNAPSHOT | symbol=%s | trade_id=%s | side=%s | archetype=%s | bars_since_break=%d | "
                        "seconds_since_break=%.2f | break_distance_pct=%.6f | current_distance_pct=%.6f | distance_increasing=%s | "
                        "reclaim_detected=%s | opposite_body_ratio=%.4f | continuation_slope_score=%.4f | score=%.4f | threshold=%.4f | phase=survival_defer",
                        candidate.symbol,
                        self._trade_id(candidate),
                        opt_loc,
                        arch_norm_gate,
                        len(post_break),
                        float(sec_since),
                        float(break_distance_current),
                        float(cur_dist_pct),
                        str(bool(break_meta.get("distance_increasing"))).lower(),
                        str(bool(reclaimed)).lower(),
                        float(opp_body_ratio_pre),
                        float(slope_d),
                        float(comp_defer),
                        float(thresh_gate),
                    )
                    log.warning(
                        "TRENDLINE_ENTRY_SCORE_DEFER | symbol=%s | trade_id=%s | reason=survival_window | score=%.4f | threshold=%.4f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(comp_defer),
                        float(thresh_gate),
                    )
                    self._log_trendline_false_break_gate(
                        kind="TRENDLINE_FALSE_BREAK_SURVIVAL_WINDOW",
                        symbol=candidate.symbol,
                        trade_id=self._trade_id(candidate),
                        reason="no_local_continuation",
                        final_action="defer_confirm_next_bar",
                        break_distance_pct=float(break_distance_current),
                        body_ratio=float(br_loc),
                        distance_increasing=bool(break_meta.get("distance_increasing")),
                        expected_move_pct=float(em_loc),
                        selector_structure_ready=bool(getattr(candidate, "structure_hard_gate_passed", False)),
                        pre_execute_passed=True,
                        seconds_since_break=float(sec_since),
                        option_side=opt_loc,
                        extra=f"post_break_bars={len(post_break)}|min_bars_local={min_bars_local}|local_ok=false",
                    )
                    return None
                if getattr(candidate, "_entry_score_ref_em_pct", None) is None:
                    setattr(candidate, "_entry_score_ref_em_pct", float(em_loc))
                ref_em = float(getattr(candidate, "_entry_score_ref_em_pct"))
                composite_score, bd_map = self._compute_composite_entry_score(
                    candidate,
                    post_break,
                    bars_early,
                    break_meta=break_meta,
                    break_distance_current=float(break_distance_current),
                    local_ok_gate=False,
                    structure_ok_gate=bool(structure_ok_gate),
                    continuation_dist=float(continuation_dist),
                    velocity_now=float(velocity_now),
                    reclaimed=reclaimed,
                    early_reclaim=False,
                    late_reclaim=late_reclaim,
                    opposite_conflict=bool(opposite_conflict),
                    arch_raw=arch_raw_gate,
                )
                slope_log = float(bd_map.get("slope", 0.0))
                log.warning(
                    "TRENDLINE_ENTRY_SCORE_SNAPSHOT | symbol=%s | trade_id=%s | side=%s | archetype=%s | bars_since_break=%d | "
                    "seconds_since_break=%.2f | break_distance_pct=%.6f | current_distance_pct=%.6f | distance_increasing=%s | "
                    "reclaim_detected=%s | opposite_body_ratio=%.4f | continuation_slope_score=%.4f | score=%.4f | threshold=%.4f | phase=post_survival",
                    candidate.symbol,
                    self._trade_id(candidate),
                    opt_loc,
                    arch_norm_gate,
                    len(post_break),
                    float(sec_since),
                    float(break_distance_current),
                    float(cur_dist_pct),
                    str(bool(break_meta.get("distance_increasing"))).lower(),
                    str(bool(reclaimed)).lower(),
                    float(opp_body_ratio_pre),
                    float(slope_log),
                    float(composite_score),
                    float(thresh_gate),
                )
                if opp_body_ratio_pre > 1.0:
                    log.warning(
                        "TRENDLINE_BOUNCEBACK_BLOCK | symbol=%s | trade_id=%s | side=%s | archetype=%s | bars_since_break=%d | "
                        "seconds_since_break=%.2f | break_distance_pct=%.6f | current_distance_pct=%.6f | distance_increasing=%s | "
                        "reclaim_detected=%s | opposite_body_ratio=%.4f | continuation_slope_score=%.4f | score=%.4f | threshold=%.4f | "
                        "final_action=%s | detail=opposite_body_gt_break",
                        candidate.symbol,
                        self._trade_id(candidate),
                        opt_loc,
                        arch_norm_gate,
                        len(post_break),
                        float(sec_since),
                        float(break_distance_current),
                        float(cur_dist_pct),
                        str(bool(break_meta.get("distance_increasing"))).lower(),
                        str(bool(reclaimed)).lower(),
                        float(opp_body_ratio_pre),
                        float(slope_log),
                        float(composite_score),
                        float(thresh_gate),
                        "invalidate",
                    )
                    self._set_state(candidate, TrendlineCandidateState.INVALIDATED, "trendline_bounceback_opposite_body")
                    self._funnel_blocked("false_break")
                    return None
                em_collapse = ref_em > 1e-12 and float(em_loc) < ref_em * float(self.config.entry_em_collapse_ratio)
                if em_collapse:
                    log.warning(
                        "TRENDLINE_ENTRY_SCORE_REJECT | symbol=%s | trade_id=%s | reason=expected_move_collapse | score=%.4f | threshold=%.4f | em=%.6f | ref_em=%.6f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(composite_score),
                        float(thresh_gate),
                        float(em_loc),
                        float(ref_em),
                    )
                    self._set_state(candidate, TrendlineCandidateState.INVALIDATED, "trendline_entry_em_collapse")
                    self._funnel_blocked("continuation")
                    return None
                if composite_score >= thresh_gate:
                    allow_relaxed_path = True
                    log.warning(
                        "TRENDLINE_ENTRY_SCORE_PASS | symbol=%s | trade_id=%s | score=%.4f | threshold=%.4f | archetype=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(composite_score),
                        float(thresh_gate),
                        arch_norm_gate,
                    )
                    if self._trade_id(candidate) in self._false_break_survival_armed_trade_ids:
                        log.warning(
                            "TRENDLINE_BOUNCEBACK_CLEARED | symbol=%s | trade_id=%s | side=%s | archetype=%s | score=%.4f | final_action=proceed_entry",
                            candidate.symbol,
                            self._trade_id(candidate),
                            opt_loc,
                            arch_norm_gate,
                            float(composite_score),
                        )
                        self._false_break_survival_armed_trade_ids.discard(self._trade_id(candidate))
                elif sec_since > float(self.config.entry_score_stale_seconds):
                    log.warning(
                        "TRENDLINE_ENTRY_SCORE_REJECT | symbol=%s | trade_id=%s | reason=stale | score=%.4f | threshold=%.4f | seconds_since_break=%.2f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(composite_score),
                        float(thresh_gate),
                        float(sec_since),
                    )
                    self._set_state(candidate, TrendlineCandidateState.INVALIDATED, "trendline_entry_score_stale")
                    self._funnel_blocked("continuation")
                    return None
                else:
                    log.warning(
                        "TRENDLINE_BOUNCEBACK_DEFER | symbol=%s | trade_id=%s | score=%.4f | threshold=%.4f | detail=below_threshold_rescore_next_bar",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(composite_score),
                        float(thresh_gate),
                    )
                    log.warning(
                        "TRENDLINE_ENTRY_SCORE_DEFER | symbol=%s | trade_id=%s | reason=below_threshold | score=%.4f | threshold=%.4f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(composite_score),
                        float(thresh_gate),
                    )
                    return None
            if not structure_ok_gate:
                log.warning(
                    "TRENDLINE_FALSE_BREAK_BLOCKED | symbol=%s | trade_id=%s | mode=normal_confirmed_break | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    structure_reason_gate or "no_post_break_structure",
                )
                return None
            if local_ok_gate:
                if continuation_dist < float(self.config.min_continuation_distance_pct):
                    log.warning(
                        "TRENDLINE_DISTANCE_SHRINK_BLOCKED | symbol=%s | trade_id=%s | mode=normal_confirmed_break | continuation_dist=%.6f | min_cont_dist=%.6f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(continuation_dist),
                        float(self.config.min_continuation_distance_pct),
                    )
                    self._funnel_blocked("continuation")
                    return None
                if velocity_now < float(self.config.min_velocity_pct):
                    log.warning(
                        "TRENDLINE_MOMENTUM_CONFLICT_BLOCKED | symbol=%s | trade_id=%s | mode=normal_confirmed_break | velocity=%.6f | min_velocity=%.6f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(velocity_now),
                        float(self.config.min_velocity_pct),
                    )
                    self._funnel_blocked("continuation")
                    return None
            elif not allow_relaxed_path:
                return None
            log.info(
                "TRENDLINE_PENDING_CONFIRMATION_CONFIRMED | symbol=%s | trade_id=%s | mode=normal_confirmed_break | post_break_bars=%d | break_quality=%.3f | break_distance=%.6f | continuation_dist=%.6f | velocity=%.6f",
                candidate.symbol,
                self._trade_id(candidate),
                len(post_break),
                float(break_quality_score),
                float(break_distance_current),
                float(continuation_dist),
                float(velocity_now),
            )
        if fast_mode:
            min_fast = float(getattr(self.config, "fast_path_min_break_distance", 0.0035))
            impulse_mode_fm = bool(break_meta.get("impulse_mode"))
            slow_fm = bool(break_meta.get("slow_trend_mode")) or (
                str(break_meta.get("trendline_mode") or "").upper() == "SLOW_TREND"
            )
            if slow_fm:
                min_em_gate = float(self._min_expected_move_pct_slow)
            elif impulse_mode_fm:
                min_em_gate = float(self._min_expected_move_pct_impulse)
            else:
                min_em_gate = float(self._min_expected_move_pct_default)
            em_post_fast = self._compute_expected_move_pct(candidate, post_break if post_break else bars_early)
            dist_inc_fm = bool(break_meta.get("distance_increasing"))
            exec_reason = str(break_meta.get("execution_reason") or "").strip().lower()
            sr_low = str(candidate.state_reason or "").lower()
            cont_like = bool(
                break_meta.get("delayed_continuation_entry")
                or break_meta.get("retest_mode")
                or "retest" in sr_low
                or "continuation" in sr_low
            )
            if bool(getattr(self.config, "fast_path_require_strong_break", True)):
                if float(break_distance_current) < min_fast:
                    bypass_dist = (
                        bool(getattr(candidate, "structure_hard_gate_passed", False))
                        and dist_inc_fm
                        and float(em_post_fast) >= float(min_em_gate) * float(self._false_break_bypass_min_em_mult)
                        and (
                            float(break_distance_current) >= float(self.config.break_distance_min) * 0.85
                            or float(break_distance_current) >= float(self._fast_path_final_min_bd) * 0.72
                            or exec_reason == "executed_valid_confirmation"
                            or cont_like
                        )
                    )
                    br_d, _bd_d = self._break_bar_body_ratio(candidate, bars_early)
                    sec_d = self._seconds_since_break_on_bar(candidate, bars_early)
                    opt_d = self._expected_option_side_for_candidate(candidate)
                    if bypass_dist:
                        self._log_trendline_false_break_gate(
                            kind="TRENDLINE_FALSE_BREAK_BYPASS_STRONG_SETUP",
                            symbol=candidate.symbol,
                            trade_id=self._trade_id(candidate),
                            reason="break_distance_too_small",
                            final_action="bypass_impulse_distance_floor",
                            break_distance_pct=float(break_distance_current),
                            body_ratio=float(br_d),
                            distance_increasing=dist_inc_fm,
                            expected_move_pct=float(em_post_fast),
                            selector_structure_ready=bool(getattr(candidate, "structure_hard_gate_passed", False)),
                            pre_execute_passed=True,
                            seconds_since_break=float(sec_d),
                            option_side=opt_d,
                            extra=f"min_fast=%.6f|min_em_gate=%.6f|cont_like=%s"
                            % (min_fast, min_em_gate, str(cont_like).lower()),
                        )
                    else:
                        self._log_trendline_false_break_gate(
                            kind="TRENDLINE_FALSE_BREAK_HARD_BLOCK",
                            symbol=candidate.symbol,
                            trade_id=self._trade_id(candidate),
                            reason="break_distance_too_small",
                            final_action="blocked_impulse_distance_floor",
                            break_distance_pct=float(break_distance_current),
                            body_ratio=float(br_d),
                            distance_increasing=dist_inc_fm,
                            expected_move_pct=float(em_post_fast),
                            selector_structure_ready=bool(getattr(candidate, "structure_hard_gate_passed", False)),
                            pre_execute_passed=True,
                            seconds_since_break=float(sec_d),
                            option_side=opt_d,
                            extra=f"min_fast=%.6f" % (min_fast,),
                        )
                        log.warning(
                            "TRENDLINE_FALSE_BREAK_BLOCKED | symbol=%s | trade_id=%s | mode=impulse_fast_path | reason=break_distance_too_small | break_distance=%.6f | min=%.6f",
                            candidate.symbol,
                            self._trade_id(candidate),
                            float(break_distance_current),
                            min_fast,
                        )
                        self._funnel_blocked("false_break")
                        return None
            body_ratio_qual, _ = self._break_body_ratio_vs_prev(candidate, bars_early)
            body_ratio_avg, _ = self._break_bar_body_ratio(candidate, bars_early)
            min_body_fast = float(getattr(self.config, "fast_path_min_body_ratio", 0.72))
            if body_ratio_qual < min_body_fast:
                sec_b = self._seconds_since_break_on_bar(candidate, bars_early)
                opt_b = self._expected_option_side_for_candidate(candidate)
                if bool(self._fast_path_weak_body_catastrophic_only):
                    cat_body = self._catastrophic_impulse_weak_body(
                        break_distance_pct=float(break_distance_current),
                        body_ratio_fast=float(body_ratio_avg),
                        distance_increasing=bool(break_meta.get("distance_increasing")),
                        expected_move_pct=float(em_post_fast),
                        reclaimed=reclaimed,
                        opposite_conflict=opposite_conflict,
                        min_fast_break=min_fast,
                    )
                    if not cat_body:
                        self._log_trendline_false_break_gate(
                            kind="TRENDLINE_FALSE_BREAK_ADVISORY",
                            symbol=candidate.symbol,
                            trade_id=self._trade_id(candidate),
                            reason="weak_body_ratio",
                            final_action="advisory_pass_impulse_body_gate",
                            break_distance_pct=float(break_distance_current),
                            body_ratio=float(body_ratio_qual),
                            distance_increasing=bool(break_meta.get("distance_increasing")),
                            expected_move_pct=float(em_post_fast),
                            selector_structure_ready=bool(getattr(candidate, "structure_hard_gate_passed", False)),
                            pre_execute_passed=True,
                            seconds_since_break=float(sec_b),
                            option_side=opt_b,
                            extra=(
                                f"min_body_fast=%.4f|body_ratio_vs_avg=%.4f|catastrophic=%s"
                                % (min_body_fast, body_ratio_avg, str(cat_body).lower())
                            ),
                        )
                    else:
                        self._log_trendline_false_break_gate(
                            kind="TRENDLINE_FALSE_BREAK_HARD_BLOCK",
                            symbol=candidate.symbol,
                            trade_id=self._trade_id(candidate),
                            reason="weak_body_ratio",
                            final_action="blocked_impulse_body_catastrophic",
                            break_distance_pct=float(break_distance_current),
                            body_ratio=float(body_ratio_qual),
                            distance_increasing=bool(break_meta.get("distance_increasing")),
                            expected_move_pct=float(em_post_fast),
                            selector_structure_ready=bool(getattr(candidate, "structure_hard_gate_passed", False)),
                            pre_execute_passed=True,
                            seconds_since_break=float(sec_b),
                            option_side=opt_b,
                            extra=f"min_body_fast=%.4f|body_ratio_vs_avg=%.4f" % (min_body_fast, body_ratio_avg),
                        )
                        log.warning(
                            "TRENDLINE_FALSE_BREAK_BLOCKED | symbol=%s | trade_id=%s | mode=impulse_fast_path | reason=weak_body_ratio | body_ratio=%.4f | body_ratio_vs_avg=%.4f | min=%.4f",
                            candidate.symbol,
                            self._trade_id(candidate),
                            float(body_ratio_qual),
                            float(body_ratio_avg),
                            min_body_fast,
                        )
                        self._funnel_blocked("false_break")
                        return None
                else:
                    self._log_trendline_false_break_gate(
                        kind="TRENDLINE_FALSE_BREAK_HARD_BLOCK",
                        symbol=candidate.symbol,
                        trade_id=self._trade_id(candidate),
                        reason="weak_body_ratio",
                        final_action="blocked_impulse_body_floor_legacy",
                        break_distance_pct=float(break_distance_current),
                        body_ratio=float(body_ratio_qual),
                        distance_increasing=bool(break_meta.get("distance_increasing")),
                        expected_move_pct=float(em_post_fast),
                        selector_structure_ready=bool(getattr(candidate, "structure_hard_gate_passed", False)),
                        pre_execute_passed=True,
                        seconds_since_break=float(sec_b),
                        option_side=opt_b,
                        extra=f"min_body_fast=%.4f|body_ratio_vs_avg=%.4f" % (min_body_fast, body_ratio_avg),
                    )
                    log.warning(
                        "TRENDLINE_FALSE_BREAK_BLOCKED | symbol=%s | trade_id=%s | mode=impulse_fast_path | reason=weak_body_ratio | body_ratio=%.4f | body_ratio_vs_avg=%.4f | min=%.4f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        float(body_ratio_qual),
                        float(body_ratio_avg),
                        min_body_fast,
                    )
                    self._funnel_blocked("false_break")
                    return None
            if bool(getattr(self.config, "fast_path_require_distance_increasing", True)) and not bool(break_meta.get("distance_increasing")):
                log.warning(
                    "TRENDLINE_DISTANCE_SHRINK_BLOCKED | symbol=%s | trade_id=%s | mode=impulse_fast_path | reason=distance_not_increasing",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._funnel_blocked("quality")
                return None
            if bool(getattr(self.config, "fast_path_require_no_reclaim", True)) and reclaimed:
                log.warning(
                    "TRENDLINE_RECLAIM_BLOCKED | symbol=%s | trade_id=%s | mode=impulse_fast_path | reason=reclaimed_trendline",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._funnel_blocked("false_break")
                return None
            if opposite_conflict:
                log.warning(
                    "TRENDLINE_MOMENTUM_CONFLICT_BLOCKED | symbol=%s | trade_id=%s | mode=impulse_fast_path | reason=large_opposite_candle",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._funnel_blocked("false_break")
                return None
            if bool(getattr(self.config, "fast_path_require_momentum_agreement", True)) and not momentum_agrees:
                log.warning(
                    "TRENDLINE_MOMENTUM_CONFLICT_BLOCKED | symbol=%s | trade_id=%s | mode=impulse_fast_path | reason=directional_momentum_mismatch",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._funnel_blocked("continuation")
                return None
        log.info(
            "TRENDLINE_ENTRY_CONFIRMATION_AUDIT | symbol=%s | trade_id=%s | mode=%s | break_distance=%.6f | break_quality=%.3f | "
            "confirm_bars=%d | required_bars=%d | hold_beyond_line=%s | local_continuation=%s | post_break_structure=%s | no_reclaim=%s | "
            "opposite_momentum_conflict=%s | continuation_distance=%.6f | velocity=%.6f",
            candidate.symbol,
            self._trade_id(candidate),
            "impulse_fast_path" if fast_mode else "normal_confirmed_break",
            float(break_distance_current),
            float(break_quality_score),
            len(post_break),
            max(1, int(self.config.confirmation_window_bars)),
            str(bool(not reclaimed)).lower(),
            str(bool(local_ok_gate)).lower(),
            str(bool(structure_ok_gate)).lower(),
            str(bool(not reclaimed)).lower(),
            str(bool(opposite_conflict)).lower(),
            float(continuation_dist),
            float(velocity_now),
        )
        br_for_expansion, _ = self._break_body_ratio_vs_prev(candidate, bars_early)
        expansion_fail, expansion_reason = self._fails_expansion_quality_filter(
            candidate,
            post_break,
            bars_for_timing=bars_early,
            body_ratio_break=float(br_for_expansion),
        )
        expansion_state = expansion_reason or ("pass" if not expansion_fail else "blocked")
        if expansion_reason == "survival_defer_low_expansion" and self._is_impulse_fast_emit_entry(
            candidate, break_meta
        ):
            accept_ok, accept_via, accept_meta = self._post_break_acceptance_met(
                candidate,
                post_break,
                continuation_dist=float(continuation_dist),
                reclaimed=bool(reclaimed),
            )
            self._log_post_break_acceptance(
                candidate,
                accepted=accept_ok,
                via=accept_via,
                meta=accept_meta,
                expansion_state=expansion_state,
            )
            if not accept_ok:
                expansion_fail = True
                expansion_reason = f"survival_defer_requires_acceptance|{accept_via}"
        if expansion_fail:
            brb_e, _ = self._break_bar_body_ratio(candidate, bars_early)
            self._log_trendline_decision_snapshot(
                candidate,
                decision="skip",
                skip_reason=str(expansion_reason),
                bars=bars_early,
                break_meta=break_meta,
                break_distance_pct=break_distance_current,
                body_ratio_break=brb_e,
                expansion_ok=False,
                entry_mode="",
                break_quality_score=float(break_quality_score),
            )
            if bars_early:
                self._register_emit_skip_missed_win_watch(candidate, bars_early[-1], str(expansion_reason))
            log.warning(
                "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=expansion_quality | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                expansion_reason,
            )
            self._funnel_blocked("quality")
            return None
        if self._is_impulse_fast_emit_entry(candidate, break_meta):
            accept_ok, accept_via, accept_meta = self._post_break_acceptance_met(
                candidate,
                post_break,
                continuation_dist=float(continuation_dist),
                reclaimed=bool(reclaimed),
            )
            self._log_post_break_acceptance(
                candidate,
                accepted=accept_ok,
                via=accept_via,
                meta=accept_meta,
                expansion_state=expansion_state,
            )
            if not accept_ok:
                brb_acc, _ = self._break_body_ratio_vs_prev(candidate, bars_early)
                self._log_trendline_decision_snapshot(
                    candidate,
                    decision="skip",
                    skip_reason=f"post_break_acceptance_required|{accept_via}",
                    bars=bars_early,
                    break_meta=break_meta,
                    break_distance_pct=break_distance_current,
                    body_ratio_break=brb_acc,
                    expansion_ok=True,
                    entry_mode="post_break_acceptance",
                    break_quality_score=float(break_quality_score),
                )
                if bars_early:
                    self._register_emit_skip_missed_win_watch(
                        candidate, bars_early[-1], f"post_break_acceptance|{accept_via}"
                    )
                log.warning(
                    "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | "
                    "stage_after_break=post_break_acceptance | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    accept_via,
                )
                self._funnel_blocked("continuation")
                return None
        if bool(break_meta.get("impulse_mode")):
            persist_ok, persist_reason = self._impulse_emit_persistence_met(
                candidate, post_break, bars_for_timing=bars_early
            )
            if not persist_ok:
                brb_p, _ = self._break_bar_body_ratio(candidate, bars_early)
                self._log_trendline_decision_snapshot(
                    candidate,
                    decision="skip",
                    skip_reason=str(persist_reason),
                    bars=bars_early,
                    break_meta=break_meta,
                    break_distance_pct=break_distance_current,
                    body_ratio_break=brb_p,
                    expansion_ok=True,
                    entry_mode="impulse_emit_persistence",
                    break_quality_score=float(break_quality_score),
                )
                if bars_early:
                    self._register_emit_skip_missed_win_watch(candidate, bars_early[-1], str(persist_reason))
                log.warning(
                    "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | "
                    "stage_after_break=impulse_emit_persistence | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    persist_reason,
                )
                self._funnel_blocked("quality")
                return None
            log.info(
                "TRENDLINE_IMPULSE_EMIT_READY | symbol=%s | trade_id=%s | via=%s",
                candidate.symbol,
                self._trade_id(candidate),
                persist_reason,
            )
        expected_move_pct = self._compute_expected_move_pct(candidate, post_break)
        impulse_mode = bool(break_meta.get("impulse_mode"))
        slow_trend_mode = bool(break_meta.get("slow_trend_mode")) or (
            str(break_meta.get("trendline_mode") or "").upper() == "SLOW_TREND"
        )
        if slow_trend_mode:
            min_expected_move_pct = float(self._min_expected_move_pct_slow)
            mode_label = "slow_trend"
        elif impulse_mode:
            min_expected_move_pct = float(self._min_expected_move_pct_impulse)
            mode_label = "impulse"
        else:
            min_expected_move_pct = float(self._min_expected_move_pct_default)
            mode_label = "standard"
        log.warning(
            "TRENDLINE_DYNAMIC_THRESHOLD | symbol=%s | mode=%s | min_expected_move_pct=%.6f | expected_move_pct=%.6f",
            candidate.symbol,
            mode_label,
            float(min_expected_move_pct),
            float(expected_move_pct),
        )
        strong_break_override = break_distance_current >= float(self._strong_dist_override_min_bd)
        if strong_break_override:
            log.warning(
                "TRENDLINE_OVERRIDE_STRONG_BREAK | symbol=%s | trade_id=%s | break_distance=%.6f | expected_move_pct=%.6f | min_expected_move_pct=%.6f",
                candidate.symbol,
                self._trade_id(candidate),
                break_distance_current,
                expected_move_pct,
                float(min_expected_move_pct),
            )
        if expected_move_pct < float(min_expected_move_pct) and not strong_break_override:
            brb_m, _ = self._break_bar_body_ratio(candidate, bars_early)
            self._log_trendline_decision_snapshot(
                candidate,
                decision="skip",
                skip_reason="insufficient_move_potential",
                bars=bars_early,
                break_meta=break_meta,
                break_distance_pct=break_distance_current,
                body_ratio_break=brb_m,
                expansion_ok=True,
                entry_mode="",
                break_quality_score=float(break_quality_score),
            )
            # Deprecated as hard veto in simplified path; keep as advisory unless move is effectively zero.
            if float(expected_move_pct) <= max(1e-8, float(min_expected_move_pct) * 0.05):
                if bars_early:
                    self._register_emit_skip_missed_win_watch(
                        candidate, bars_early[-1], "insufficient_move_potential"
                    )
                log.warning(
                    "TRENDLINE_BLOCKED | symbol=%s | trade_id=%s | reason=insufficient_move_potential | expected_move_pct=%.6f | threshold=%.6f",
                    candidate.symbol,
                    self._trade_id(candidate),
                    expected_move_pct,
                    float(min_expected_move_pct),
                )
                self._log_score_summary(
                    candidate=candidate,
                    break_quality_score=float(break_quality_score),
                    continuation_quality_score=self._continuation_quality_score(
                        candidate,
                        post_break if post_break else bars_early,
                        float(candidate.break_event.close_price) if candidate.break_event else 0.0,
                    ),
                    expected_move_pct=float(expected_move_pct),
                    executor_called=False,
                    final_decision="blocked:insufficient_move_potential_catastrophic",
                )
                self._log_hard_veto_audit(
                    symbol=candidate.symbol,
                    stage="break_quality",
                    veto_reason="insufficient_move_potential_catastrophic",
                    catastrophic=True,
                    executor_called=False,
                )
                return None
            log.info(
                "TRENDLINE_ADVISORY_MOVE_POTENTIAL | symbol=%s | trade_id=%s | expected_move_pct=%.6f | threshold=%.6f",
                candidate.symbol,
                self._trade_id(candidate),
                expected_move_pct,
                float(min_expected_move_pct),
            )
        distance_increasing = bool(break_meta.get("distance_increasing"))
        body_ratio_gate, _ = self._break_bar_body_ratio(candidate, bars_early)
        last_bg = bars_early[-1] if bars_early else None
        prev_bg = bars_early[-2] if len(bars_early) >= 2 else None
        velocity_gate = (
            abs(float(last_bg.close) - float(prev_bg.close)) / max(abs(float(prev_bg.close)), 1e-9)
            if last_bg is not None and prev_bg is not None
            else 0.0
        )
        ok_q, q_reason = self._execution_quality_gate(
            candidate, break_distance_current, body_ratio_gate, distance_increasing
        )
        if not ok_q:
            self._log_trendline_decision_snapshot(
                candidate,
                decision="skip",
                skip_reason=f"weak_execution_gate:{q_reason}",
                bars=bars_early,
                break_meta=break_meta,
                break_distance_pct=break_distance_current,
                body_ratio_break=body_ratio_gate,
                expansion_ok=True,
                entry_mode="",
                break_quality_score=float(break_quality_score),
                velocity_pct=float(velocity_gate),
                expected_move_pct=float(expected_move_pct),
                min_expected_move_threshold=float(min_expected_move_pct),
                distance_increasing=distance_increasing,
            )
            if q_reason == "catastrophic_micro_break":
                if bars_early:
                    self._register_emit_skip_missed_win_watch(
                        candidate, bars_early[-1], f"weak_execution_gate:{q_reason}"
                    )
                log.warning(
                    "TRENDLINE_REJECT_WEAK_BREAK | symbol=%s | trade_id=%s | reason=%s | break_distance_pct=%.6f | "
                    "body_ratio_break=%.4f | distance_increasing=%s | strict_min_break_distance=%.6f | strict_body_ratio_min=%.4f",
                    candidate.symbol,
                    self._trade_id(candidate),
                    q_reason,
                    break_distance_current,
                    body_ratio_gate,
                    str(bool(distance_increasing)).lower(),
                    float(self.config.strict_min_break_distance),
                    float(self.config.body_ratio_min_strict),
                )
                self._log_score_summary(
                    candidate=candidate,
                    break_quality_score=float(break_quality_score),
                    continuation_quality_score=self._continuation_quality_score(
                        candidate,
                        post_break if post_break else bars_early,
                        float(candidate.break_event.close_price) if candidate.break_event else 0.0,
                    ),
                    expected_move_pct=float(expected_move_pct),
                    executor_called=False,
                    final_decision=f"blocked:{q_reason}",
                )
                self._log_hard_veto_audit(
                    symbol=candidate.symbol,
                    stage="break_quality",
                    veto_reason=q_reason,
                    catastrophic=True,
                    executor_called=False,
                )
                return None
            log.info(
                "TRENDLINE_BREAK_QUALITY_ADVISORY | symbol=%s | trade_id=%s | reason=%s | break_distance_pct=%.6f | body_ratio_break=%.4f",
                candidate.symbol,
                self._trade_id(candidate),
                q_reason,
                break_distance_current,
                body_ratio_gate,
            )
        timing_ok, timing_detail = self._entry_timing_decision(
            candidate, bars_early, break_distance_current, body_ratio_gate
        )
        if not timing_ok:
            self._log_trendline_decision_snapshot(
                candidate,
                decision="skip",
                skip_reason=f"entry_timing:{timing_detail}",
                bars=bars_early,
                break_meta=break_meta,
                break_distance_pct=break_distance_current,
                body_ratio_break=body_ratio_gate,
                expansion_ok=True,
                entry_mode="",
                break_quality_score=float(break_quality_score),
                velocity_pct=float(velocity_gate),
                expected_move_pct=float(expected_move_pct),
                min_expected_move_threshold=float(min_expected_move_pct),
                distance_increasing=distance_increasing,
            )
            if bars_early:
                self._register_emit_skip_missed_win_watch(candidate, bars_early[-1], f"entry_timing:{timing_detail}")
            log.warning(
                "TRENDLINE_ENTRY_TIMING_DECISION | symbol=%s | trade_id=%s | passed=false | detail=%s",
                candidate.symbol,
                self._trade_id(candidate),
                timing_detail,
            )
            self._funnel_blocked("quality")
            return None
        log.info(
            "TRENDLINE_ENTRY_TIMING_DECISION | symbol=%s | trade_id=%s | passed=true | detail=%s",
            candidate.symbol,
            self._trade_id(candidate),
            timing_detail,
        )
        retest_ready_ev = bool(candidate.retest_pending)
        continuation_ready_ev = bool(candidate.continuation_pending)
        impulse_ready_ev = bool(break_meta.get("impulse_mode")) or bool(candidate.impulse_pending_confirmation)
        log.info(
            "TRENDLINE_ENTRY_EVAL_ORDER | symbol=%s | trade_id=%s | retest_ready=%s | continuation_ready=%s | impulse_ready=%s",
            candidate.symbol,
            self._trade_id(candidate),
            str(retest_ready_ev).lower(),
            str(continuation_ready_ev).lower(),
            str(impulse_ready_ev).lower(),
        )
        body_expanding = bool(break_meta.get("body_expanding"))
        strong_breakout = bool(break_meta.get("strong_breakout"))
        breakout_is_clean = bool(break_meta.get("breakout_is_clean"))
        drift_breakout = bool(break_meta.get("drift_breakout"))
        directional_candles = int(break_meta.get("directional_candles") or 0)
        follow_through = bool(distance_increasing and body_expanding)
        early_entry_mode = bool(break_meta.get("early_entry_mode")) or (
            str(break_meta.get("trendline_mode") or "").upper() == "EARLY_ENTRY"
        )
        bars = bars_early
        last_bar = bars[-1] if bars else None
        sample = bars[-3:] if len(bars) >= 3 else bars
        body_size = (
            abs(float(last_bar.close) - float(last_bar.open))
            if last_bar is not None
            else 0.0
        )
        avg_body = (
            sum(abs(float(b.close) - float(b.open)) for b in sample) / max(1, len(sample))
            if sample
            else 0.0
        )
        body_ratio = (body_size / avg_body) if avg_body > 0 else 0.0
        bypass_strict_emit = bool(break_meta.get("trendline_fast_path_bypass_strict_emit"))
        if not bypass_strict_emit and not self._strict_pre_emit_pipeline(candidate, bars, break_meta, break_distance_current):
            self._funnel_blocked("quality")
            return None
        candle_range = abs(float(last_bar.high) - float(last_bar.low)) if last_bar is not None else 0.0
        close_position_in_candle = (
            ((float(last_bar.close) - float(last_bar.low)) / candle_range)
            if last_bar is not None and candle_range > 1e-12
            else None
        )
        prev_bar = bars[-2] if len(bars) >= 2 else None
        velocity = (
            abs(float(last_bar.close) - float(prev_bar.close)) / max(abs(float(prev_bar.close)), 1e-9)
            if last_bar is not None and prev_bar is not None
            else 0.0
        )
        momentum_bars_used = int(getattr(candidate.momentum_confirmation, "bars_used", 0) or 0)
        log.info(
            "TRENDLINE_ENTRY_FILTER | symbol=%s | trade_id=%s | follow_through=%s",
            candidate.symbol,
            self._trade_id(candidate),
            follow_through,
        )
        if self._market_regime == "CHOPPY":
            regime_reason = None
            if not distance_increasing:
                regime_reason = "regime_choppy_requires_distance_increasing"
            elif body_ratio < float(self._choppy_body_ratio_min):
                regime_reason = (
                    f"regime_choppy_body_ratio_too_low:{body_ratio:.3f}_below_{self._choppy_body_ratio_min:.3f}"
                )
            elif not follow_through:
                regime_reason = "regime_choppy_requires_strict_follow_through"
            elif momentum_bars_used < int(self._choppy_min_followthrough_bars):
                regime_reason = (
                    f"regime_choppy_followthrough_bars_too_low:{momentum_bars_used}_below_{self._choppy_min_followthrough_bars}"
                )
            if regime_reason:
                log.warning(
                    "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=false | reason=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    regime_reason,
                )
                log.warning(
                    "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=market_regime | reason=%s | regime=%s",
                    candidate.symbol,
                    self._trade_id(candidate),
                    regime_reason,
                    self._market_regime,
                )
                self._warn_confirm_pending(
                    candidate,
                    "market_regime",
                    f"waiting_for_stronger_confirmation | regime={self._market_regime} | reason={regime_reason}",
                )
                self._log_decision_snapshot(candidate, "standard", "filtered", regime_reason)
                return None
        if impulse_mode:
            log.warning(
                "TRENDLINE_IMPULSE_ENTRY | symbol=%s | trade_id=%s | mode=IMPULSE | follow_through_relaxed=true",
                candidate.symbol,
                self._trade_id(candidate),
            )
        if slow_trend_mode:
            log.warning(
                "TRENDLINE_SLOW_TREND_ENTRY | symbol=%s | trade_id=%s | mode=SLOW_TREND | single_candle_follow_through_not_required=true",
                candidate.symbol,
                self._trade_id(candidate),
            )
        if not impulse_mode and not slow_trend_mode and not early_entry_mode and not (follow_through or drift_breakout):
            post_break = []
            if candidate.break_event:
                break_ts = self._normalize_dt(candidate.break_event.candle_ts)
                post_break = [
                    b
                    for b in self._bar_cache.get(candidate.symbol, [])
                    if self._normalize_dt(b.ts) >= break_ts
                ]
            structure_ok = bool(post_break and self._breakout_still_trending(candidate))
            if structure_ok:
                if not candidate.rearm_pending:
                    armed = self._activate_rearm_candidate(candidate, "no_follow_through", post_break)
                    if not armed and not candidate.continuation_pending:
                        self._activate_continuation_candidate(candidate, post_break, "no_follow_through")
                log.warning(
                    "TRENDLINE_CONTINUATION_TRACK | symbol=%s | trade_id=%s | phase=entry_filter | action=continue | reason=no_follow_through",
                    candidate.symbol,
                    self._trade_id(candidate),
                )
                self._warn_confirm_pending(
                    candidate,
                    "entry_filter",
                    "waiting_for_follow_through | reason=no_follow_through",
                )
                return None
            self._log_decision_snapshot(candidate, "standard", "filtered", "no_follow_through")
            log.warning(
                "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=false | reason=no_follow_through",
                candidate.symbol,
                self._trade_id(candidate),
            )
            log.warning(
                "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=entry_filter | reason=no_follow_through",
                candidate.symbol,
                self._trade_id(candidate),
            )
            self._warn_confirm_pending(
                candidate,
                "entry_filter",
                "waiting_for_follow_through | reason=no_follow_through",
            )
            self._log_missed_move(candidate, self._bar_cache.get(candidate.symbol, []), reason="no_follow_through")
            return None
        if drift_breakout and not follow_through:
            log.info(
                "TRENDLINE_DRIFT_ENTRY | symbol=%s | trade_id=%s | candles=%d",
                candidate.symbol,
                self._trade_id(candidate),
                directional_candles,
            )
        entry_type = (
            "strong_break" if strong_breakout
            else "clean_break" if breakout_is_clean
            else "drift" if drift_breakout
            else "first_move"
        )
        anti_chop_override_used = bool(break_meta.get("anti_chop_override_used"))
        log.info(
            "TRENDLINE_ENTRY_DECISION | symbol=%s | trade_id=%s | type=%s | break_distance=%.6f | body_ratio=%.3f | "
            "distance_increasing=%s | velocity=%.6f | close_position_in_candle=%s | expected_move_pct=%.6f",
            candidate.symbol,
            self._trade_id(candidate),
            entry_type,
            break_distance_current,
            body_ratio,
            str(bool(distance_increasing)).lower(),
            velocity,
            f"{float(close_position_in_candle):.3f}" if close_position_in_candle is not None else "none",
            expected_move_pct,
        )
        self._log_decision_snapshot(candidate, entry_type, "enter", "entry_filters_passed")
        if candidate.trendline and last_bar:
            line_px = candidate.trendline.value_at(last_bar.ts)
            if line_px > 0:
                dist = abs(last_bar.close - line_px) / line_px
                if dist > self.config.max_entry_distance_pct:
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=false | reason=%s",
                        candidate.symbol,
                        self._trade_id(candidate),
                        TrendlineReasonCode.RETRACE_VIOLATION.value,
                    )
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=entry_blocked | symbol=%s | trade_id=%s | stage_after_break=anti_chase | reason=entry_too_far_from_line | distance_pct=%.4f",
                        candidate.symbol,
                        self._trade_id(candidate),
                        dist,
                    )
                    self._set_state(
                        candidate,
                        TrendlineCandidateState.WAITING_FOR_CONFIRMATION,
                        TrendlineReasonCode.RETRACE_VIOLATION.value,
                    )
                    self._warn_confirm_pending(
                        candidate,
                        "anti_chase",
                        f"waiting_for_retrace_closer_to_line | distance_pct={dist:.4f} | max_allowed={self.config.max_entry_distance_pct:.4f}",
                    )
                    self._log_decision_snapshot(candidate, entry_type, "filtered", "entry_too_far_from_line")
                    brb_r, _ = self._break_bar_body_ratio(candidate, bars)
                    self._log_trendline_decision_snapshot(
                        candidate,
                        decision="skip",
                        skip_reason="entry_too_far_from_line",
                        bars=bars,
                        break_meta=break_meta,
                        break_distance_pct=break_distance_current,
                        body_ratio_break=brb_r,
                        expansion_ok=True,
                        entry_mode=str(getattr(candidate, "entry_mode", "") or ""),
                        break_quality_score=float(break_quality_score),
                    )
                    if bars:
                        self._register_emit_skip_missed_win_watch(candidate, bars[-1], "entry_too_far_from_line")
                    return None
        setup = candidate.setup_payload or {}
        confidence = float(candidate.confidence)
        if str(entry_type_override or "").upper() == "TRENDLINE_REVERSAL_ENTRY":
            confidence = min(1.0, confidence + float(self._reversal_confidence_boost))
        option_side = str(setup.get("expected_option_side") or "").strip().lower()
        if candidate.trendline and last_bar:
            line_px = candidate.trendline.value_at(last_bar.ts)
            if candidate.direction == TrendlineDirection.BEAR and last_bar.close > line_px:
                option_side = "call"
            elif candidate.direction == TrendlineDirection.BULL and last_bar.close < line_px:
                option_side = "put"
        if option_side not in ("call", "put"):
            option_side = "put" if candidate.direction == TrendlineDirection.BULL else "call"
        setup_type = str(setup_type_override or setup.get("setup_type") or "").strip()
        if not setup_type:
            setup_type = TrendlineSetupType.from_direction(candidate.direction).value
        trigger_direction = str(trigger_direction_override or setup.get("trigger_direction") or "").strip()
        if not trigger_direction:
            trigger_direction = (
                "breakdown_down" if candidate.direction == TrendlineDirection.BULL else "breakout_up"
            )
        entry_type = str(entry_type_override or "trendline_break")
        structure_block = self._structure_emit_metadata(candidate)
        entry_mode = self._compute_entry_mode(
            candidate, break_meta, break_quality_score, break_distance_current
        )
        setattr(candidate, "entry_mode", entry_mode)
        touch_ct = int(getattr(candidate, "touch_count", 0) or 0)
        press_sc = float(getattr(candidate, "pressure_score", 0.0) or 0.0)
        log.info(
            "TRENDLINE_ENTRY_MODE_SELECTED | symbol=%s | trade_id=%s | entry_mode=%s | touch_count=%d | "
            "pressure_score=%.2f | break_quality_score=%.3f",
            candidate.symbol,
            self._trade_id(candidate),
            entry_mode,
            touch_ct,
            press_sc,
            break_quality_score,
        )
        br_body_enter, _ = self._break_bar_body_ratio(candidate, bars)
        entry_path_infer = self._infer_entry_path(candidate)
        fb_ck = bars_early
        cur_ix_ck = max(0, len(fb_ck) - 1) if fb_ck else 0
        survival_active_ck = self._in_survival_window(candidate, cur_ix_ck)
        log.info(
            "TRENDLINE_PRE_ENTRY_CHECKPOINT | symbol=%s | trade_id=%s | break_distance_pct=%.6f | body_ratio=%.3f | "
            "pressure_score=%.2f | survival_window_active=%s | entry_path=%s",
            candidate.symbol,
            self._trade_id(candidate),
            break_distance_current,
            body_ratio,
            press_sc,
            str(survival_active_ck).lower(),
            entry_path_infer,
        )
        self._log_trendline_decision_snapshot(
            candidate,
            decision="enter",
            skip_reason="",
            bars=bars,
            break_meta=break_meta,
            break_distance_pct=break_distance_current,
            body_ratio_break=br_body_enter,
            expansion_ok=not expansion_fail,
            entry_mode=entry_mode,
            break_quality_score=float(break_quality_score),
            entry_path=entry_path_infer,
            velocity_pct=float(velocity),
            expected_move_pct=float(expected_move_pct),
            min_expected_move_threshold=float(min_expected_move_pct),
            distance_increasing=bool(distance_increasing),
        )
        self._funnel_inc("pre_execute_passed")
        signal = TrendlineTradeSignal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            emitted_at=datetime.now(timezone.utc),
            trendline=candidate.trendline,  # type: ignore[arg-type]
            break_event=candidate.break_event,  # type: ignore[arg-type]
            momentum_confirmation=candidate.momentum_confirmation,  # type: ignore[arg-type]
            confidence=confidence,
            priority_score=float(priority_score_override) if priority_score_override is not None else candidate.priority_score,
            option_side=option_side,
            metadata={
                "state_reason": candidate.state_reason,
                "setup_payload": candidate.setup_payload,
                "candidate_id": candidate.candidate_id,
                "strategy_name": "easyTrendline_0DTE",
                "entry_type": entry_type,
                "entry_mode": entry_mode,
                "touch_count": touch_ct,
                "pressure_score": press_sc,
                **structure_block,
                "trendline_mode": (
                    "IMPULSE"
                    if impulse_mode
                    else "SLOW_TREND"
                    if slow_trend_mode
                    else "EARLY_ENTRY"
                    if early_entry_mode
                    else "STANDARD"
                ),
                "impulse_mode": impulse_mode,
                "slow_trend_mode": slow_trend_mode,
                "early_entry_mode": early_entry_mode,
                "entry_size_multiplier": float(break_meta.get("entry_size_multiplier") or 1.0),
                "break_distance": break_distance_current,
                "body_ratio": body_ratio,
                "distance_increasing": bool(distance_increasing),
                "velocity": float(velocity),
                "close_position_in_candle": float(close_position_in_candle) if close_position_in_candle is not None else None,
                "expected_move_pct": float(expected_move_pct),
                "anti_chop_override_used": anti_chop_override_used,
                "break_quality_score": break_quality_score,
                "execution_reason": TrendlineReasonCode.EXECUTED_VALID_CONFIRMATION.value,
                "setup_type": setup_type,
                "trigger_direction": trigger_direction,
                "expected_option_side": option_side,
                "trendline_structure_source": setup.get("trendline_structure_source", "pre_730_price_action"),
                "hold_mode": self.config.hold_mode,
                "min_hold_seconds": self._confirm_seconds,
                "reversal_watch_origin": bool(candidate.reversal_watch_started_at is not None),
                "entry_path": entry_path_infer,
                "break_archetype": str((candidate.break_event.metadata or {}).get("break_archetype") or ""),
            },
        )
        log.info(
            "TRENDLINE_ENTRY_QUALITY | symbol=%s | trade_id=%s | break_distance=%.6f | body_ratio=%.3f | expected_move_pct=%.6f | mode=%s | anti_chop_override_used=%s",
            candidate.symbol,
            self._trade_id(candidate),
            break_distance_current,
            body_ratio,
            expected_move_pct,
            mode_label,
            str(anti_chop_override_used).lower(),
        )
        log.warning(
            "TRENDLINE_PIPELINE | stage=signal_emit | symbol=%s | trade_id=%s | setup_type=%s | trigger=%s | option_side=%s | line_geometry=%s | structure_display_label=%s",
            candidate.symbol,
            self._trade_id(candidate),
            setup_type,
            trigger_direction,
            option_side,
            candidate.direction.value,
            str((candidate.setup_payload or {}).get("structure_display_label") or ""),
        )
        self._on_trendline_signal_submit(candidate)
        log.warning(
            "TRENDLINE_PIPELINE | stage=entry_ready | symbol=%s | trade_id=%s | setup_type=%s | trigger=%s | option_side=%s | hold_mode=%s | hold_duration_sec=%.1f",
            candidate.symbol,
            self._trade_id(candidate),
            setup_type,
            trigger_direction,
            option_side,
            self.config.hold_mode,
            candidate.last_hold_duration_seconds,
        )
        dist = float(abs((candidate.break_event.close_price or 0.0) - (candidate.break_event.trendline_price or 0.0)))
        log.warning(
            "TRENDLINE_BREAKOUT_ENTRY | symbol=%s | trade_id=%s | direction=%s | distance=%.4f",
            candidate.symbol,
            self._trade_id(candidate),
            option_side,
            dist,
        )
        log.warning(
            "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=true | reason=%s",
            candidate.symbol,
            self._trade_id(candidate),
            TrendlineReasonCode.EXECUTED_VALID_CONFIRMATION.value,
        )
        if candidate.candidate_id:
            self._emitted_candidate_ids.add(candidate.candidate_id)
        self._decision_stats["entry_emitted"] += 1
        orb_e = getattr(candidate, "orb_test_failure", None)
        prev_tr = dict(self._entry_tracking.get(candidate.symbol) or {})
        self._entry_tracking[candidate.symbol] = {
            **prev_tr,
            "entry_ts": time_module.time(),
            "entry_price": float(getattr(candidate.break_event, "close_price", 0.0) or 0.0),
            "entry_mode": entry_mode,
            "pressure_score": press_sc,
            "break_distance_pct": break_distance_current,
            "structure_type": getattr(orb_e, "failure_type", "") if orb_e else "",
        }
        self._log_entry_timing(candidate, entry_type)
        log.info(
            "Trendline signal READY %s %s confidence=%.3f score=%.3f",
            signal.symbol,
            signal.direction.value,
            signal.confidence,
            signal.priority_score,
        )
        intent = build_execution_intent_from_trendline_signal(signal)
        signal.metadata["execution_intent"] = intent
        self._log_score_summary(
            candidate=candidate,
            break_quality_score=float(break_quality_score),
            continuation_quality_score=self._continuation_quality_score(
                candidate,
                post_break if post_break else bars_early,
                float(candidate.break_event.close_price) if candidate.break_event else 0.0,
            ),
            expected_move_pct=float(expected_move_pct),
            executor_called=True,
            final_decision="entry_ready",
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=intent_created | symbol=%s | trade_id=%s | type=%s | confidence=%.3f",
            intent.symbol,
            self._trade_id(candidate),
            intent.structure_type,
            intent.confidence,
        )
        return signal

    def _emit_trade_signal_direct(
        self,
        candidate: TrendlineCandidate,
        break_quality_score: float,
        *,
        entry_type_override: Optional[str] = None,
        setup_type_override: Optional[str] = None,
        trigger_direction_override: Optional[str] = None,
        priority_score_override: Optional[float] = None,
    ) -> Optional[TrendlineTradeSignal]:
        if candidate.candidate_id and candidate.candidate_id in self._emitted_candidate_ids:
            log.info(
                "TRENDLINE_PIPELINE | stage=pre_execute_check | symbol=%s | trade_id=%s | passed=false | reason=%s",
                candidate.symbol,
                self._trade_id(candidate),
                TrendlineReasonCode.DEDUPE_BLOCK.value,
            )
            raise RuntimeError(f"{TrendlineReasonCode.DEDUPE_BLOCK.value}:{candidate.candidate_id}")
        if candidate.break_event is None or candidate.trendline is None:
            return None
        if candidate.momentum_confirmation is None:
            candidate.momentum_confirmation = self.momentum_engine.confirm(
                direction=candidate.direction,
                break_event=candidate.break_event,
                post_break_bars=self._bar_cache.get(candidate.symbol, [])[-3:],
            )
        setup = candidate.setup_payload or {}
        entry_type = str(entry_type_override or "trendline_break")
        setup_type = str(setup_type_override or setup.get("setup_type") or "").strip()
        if not setup_type:
            setup_type = TrendlineSetupType.from_direction(candidate.direction).value
        trigger_direction = str(trigger_direction_override or setup.get("trigger_direction") or "").strip()
        if not trigger_direction:
            trigger_direction = "breakdown_down" if candidate.direction == TrendlineDirection.BULL else "breakout_up"
        option_side = "put" if candidate.direction == TrendlineDirection.BULL else "call"
        break_meta_d = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        structure_block_d = self._structure_emit_metadata(candidate)
        bd_direct = (
            float(
                abs(
                    float(candidate.break_event.close_price)
                    - float(candidate.break_event.trendline_price)
                )
                / max(abs(float(candidate.break_event.trendline_price)), 1e-9)
            )
            if candidate.break_event
            else 0.0
        )
        bars_d = self._bar_cache.get(candidate.symbol, [])
        br_dir, _ = self._break_bar_body_ratio(candidate, bars_d)
        break_ts_d = self._normalize_dt(candidate.break_event.candle_ts) if candidate.break_event else None
        post_bd = (
            [b for b in bars_d if self._normalize_dt(b.ts) >= break_ts_d]
            if break_ts_d is not None
            else []
        )
        expected_mv_d = self._compute_expected_move_pct(candidate, post_bd)
        impulse_md = bool(break_meta_d.get("impulse_mode"))
        slow_md = bool(break_meta_d.get("slow_trend_mode")) or (
            str(break_meta_d.get("trendline_mode") or "").upper() == "SLOW_TREND"
        )
        min_exp_d = float(
            self._min_expected_move_pct_slow
            if slow_md
            else (self._min_expected_move_pct_impulse if impulse_md else self._min_expected_move_pct_default)
        )
        distance_inc_d = bool(break_meta_d.get("distance_increasing"))
        ok_qd, q_reason_d = self._execution_quality_gate(
            candidate, bd_direct, br_dir, distance_inc_d
        )
        if not ok_qd:
            log.warning(
                "TRENDLINE_REJECT_WEAK_BREAK | symbol=%s | trade_id=%s | reason=%s | path=direct_emit | "
                "break_distance_pct=%.6f | body_ratio_break=%.4f | distance_increasing=%s",
                candidate.symbol,
                self._trade_id(candidate),
                q_reason_d,
                bd_direct,
                br_dir,
                str(distance_inc_d).lower(),
            )
            self._funnel_blocked("quality")
            return None
        timing_ok_d, timing_detail_d = self._entry_timing_decision(
            candidate, bars_d, bd_direct, br_dir
        )
        if not timing_ok_d:
            log.warning(
                "TRENDLINE_ENTRY_TIMING_DECISION | symbol=%s | trade_id=%s | passed=false | path=direct_emit | detail=%s",
                candidate.symbol,
                self._trade_id(candidate),
                timing_detail_d,
            )
            self._funnel_blocked("quality")
            return None
        log.info(
            "TRENDLINE_ENTRY_TIMING_DECISION | symbol=%s | trade_id=%s | passed=true | path=direct_emit | detail=%s",
            candidate.symbol,
            self._trade_id(candidate),
            timing_detail_d,
        )
        if not self._strict_pre_emit_pipeline(candidate, bars_d, break_meta_d, bd_direct):
            self._funnel_blocked("quality")
            return None
        self._funnel_inc("pre_execute_passed")
        entry_mode_d = self._compute_entry_mode(
            candidate, break_meta_d, break_quality_score, bd_direct
        )
        setattr(candidate, "entry_mode", entry_mode_d)
        touch_ct_d = int(getattr(candidate, "touch_count", 0) or 0)
        press_sc_d = float(getattr(candidate, "pressure_score", 0.0) or 0.0)
        log.info(
            "TRENDLINE_ENTRY_MODE_SELECTED | symbol=%s | trade_id=%s | entry_mode=%s | touch_count=%d | "
            "pressure_score=%.2f | break_quality_score=%.3f | path=direct_emit",
            candidate.symbol,
            self._trade_id(candidate),
            entry_mode_d,
            touch_ct_d,
            press_sc_d,
            break_quality_score,
        )
        entry_path_dir = self._infer_entry_path(candidate)
        cur_ix_d = max(0, len(bars_d) - 1) if bars_d else 0
        survival_d = self._in_survival_window(candidate, cur_ix_d)
        log.info(
            "TRENDLINE_PRE_ENTRY_CHECKPOINT | symbol=%s | trade_id=%s | break_distance_pct=%.6f | body_ratio=%.3f | "
            "pressure_score=%.2f | survival_window_active=%s | entry_path=%s",
            candidate.symbol,
            self._trade_id(candidate),
            bd_direct,
            br_dir,
            press_sc_d,
            str(survival_d).lower(),
            entry_path_dir,
        )
        log.info(
            "TRENDLINE_ENTRY_EVAL_ORDER | symbol=%s | trade_id=%s | retest_ready=%s | continuation_ready=%s | impulse_ready=%s",
            candidate.symbol,
            self._trade_id(candidate),
            str(bool(candidate.retest_pending)).lower(),
            str(bool(candidate.continuation_pending)).lower(),
            str(bool(break_meta_d.get("impulse_mode")) or bool(candidate.impulse_pending_confirmation)).lower(),
        )
        prev_vel = bars_d[-2] if len(bars_d) >= 2 else None
        last_vel = bars_d[-1] if bars_d else None
        vel_d = (
            abs(float(last_vel.close) - float(prev_vel.close)) / max(abs(float(prev_vel.close)), 1e-9)
            if last_vel is not None and prev_vel is not None
            else 0.0
        )
        self._log_trendline_decision_snapshot(
            candidate,
            decision="enter",
            skip_reason="",
            bars=bars_d,
            break_meta=break_meta_d,
            break_distance_pct=bd_direct,
            body_ratio_break=br_dir,
            expansion_ok=None,
            entry_mode=entry_mode_d,
            break_quality_score=float(break_quality_score),
            entry_path=entry_path_dir,
            velocity_pct=float(vel_d),
            expected_move_pct=float(expected_mv_d),
            min_expected_move_threshold=float(min_exp_d),
            distance_increasing=distance_inc_d,
        )
        signal = TrendlineTradeSignal(
            symbol=candidate.symbol,
            direction=candidate.direction,
            emitted_at=datetime.now(timezone.utc),
            trendline=candidate.trendline,
            break_event=candidate.break_event,
            momentum_confirmation=candidate.momentum_confirmation,
            confidence=float(candidate.confidence),
            priority_score=float(priority_score_override) if priority_score_override is not None else candidate.priority_score,
            option_side=option_side,
            metadata={
                "state_reason": candidate.state_reason,
                "setup_payload": candidate.setup_payload,
                "candidate_id": candidate.candidate_id,
                "strategy_name": "easyTrendline_0DTE",
                "entry_type": entry_type,
                "entry_mode": entry_mode_d,
                "touch_count": touch_ct_d,
                "pressure_score": press_sc_d,
                **structure_block_d,
                "break_quality_score": break_quality_score,
                "execution_reason": TrendlineReasonCode.EXECUTED_VALID_CONFIRMATION.value,
                "setup_type": setup_type,
                "trigger_direction": trigger_direction,
                "expected_option_side": option_side,
                "extreme_break": True,
                "entry_path": entry_path_dir,
                "break_archetype": str((candidate.break_event.metadata or {}).get("break_archetype") or ""),
            },
        )
        log.warning(
            "TRENDLINE_PIPELINE | stage=signal_emit | symbol=%s | trade_id=%s | setup_type=%s | trigger=%s | option_side=%s | line_geometry=%s | structure_display_label=%s",
            candidate.symbol,
            self._trade_id(candidate),
            setup_type,
            trigger_direction,
            option_side,
            candidate.direction.value,
            str((candidate.setup_payload or {}).get("structure_display_label") or ""),
        )
        self._on_trendline_signal_submit(candidate)
        log.warning(
            "TRENDLINE_PIPELINE | stage=entry_ready | symbol=%s | trade_id=%s | setup_type=%s | trigger=%s | option_side=%s | hold_mode=%s | hold_duration_sec=%.1f",
            candidate.symbol,
            self._trade_id(candidate),
            setup_type,
            trigger_direction,
            option_side,
            self.config.hold_mode,
            candidate.last_hold_duration_seconds,
        )
        if candidate.candidate_id:
            self._emitted_candidate_ids.add(candidate.candidate_id)
        self._decision_stats["entry_emitted"] += 1
        orb_d = getattr(candidate, "orb_test_failure", None)
        prev_td = dict(self._entry_tracking.get(candidate.symbol) or {})
        self._entry_tracking[candidate.symbol] = {
            **prev_td,
            "entry_ts": time_module.time(),
            "entry_price": float(getattr(candidate.break_event, "close_price", 0.0) or 0.0),
            "entry_mode": entry_mode_d,
            "pressure_score": press_sc_d,
            "break_distance_pct": bd_direct,
            "structure_type": getattr(orb_d, "failure_type", "") if orb_d else "",
        }
        self._log_entry_timing(candidate, entry_type)
        intent = build_execution_intent_from_trendline_signal(signal)
        signal.metadata["execution_intent"] = intent
        return signal

    def _detect_drift_breakout(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, int]:
        """
        Detect "drift breakout" for slow, consistent breaks.
        Requires:
          - at least 3 directional close-to-close candles in recent bars
          - break-event distance_increasing flag from detector
          - no large opposite candle in the last 2 bars (anti-chop safety)
        """
        if not candidate.break_event:
            return False, 0
        min_drift_displacement = float(self.config.min_drift_displacement)
        require_drift_confirm = bool(self.config.require_drift_confirm)
        distance_increasing = bool((candidate.break_event.metadata or {}).get("distance_increasing"))
        if not distance_increasing or len(post_break) < 2:
            return False, 0

        lookback_bars = max(4, int(getattr(self.config, "confirmation_window_bars", 3)) + 1)
        recent = post_break[-lookback_bars:]
        directional_candles = 0
        for i in range(1, len(recent)):
            prev_close = float(recent[i - 1].close)
            cur_close = float(recent[i].close)
            if candidate.direction == TrendlineDirection.BULL:
                if cur_close < prev_close:
                    directional_candles += 1
            else:
                if cur_close > prev_close:
                    directional_candles += 1

        breakout_price = float(
            (candidate.break_event.close_price or 0.0)
            or getattr(candidate, "price", 0.0)
            or (recent[0].close if recent else 0.0)
        )
        current_price = float(
            (recent[-1].close if recent else 0.0)
            or getattr(candidate, "price", 0.0)
            or breakout_price
        )
        net_move = abs(current_price - breakout_price) / max(1e-6, abs(breakout_price))
        log.info(
            "TRENDLINE_DRIFT_METRICS | symbol=%s | trade_id=%s | candles=%d | net_move=%.4f",
            candidate.symbol,
            self._trade_id(candidate),
            directional_candles,
            net_move,
        )
        min_directional = 4 if require_drift_confirm else 3
        if directional_candles < min_directional:
            return False, directional_candles
        if net_move < min_drift_displacement:
            return False, directional_candles
        if self._has_large_opposite_candle(candidate, recent[-2:]):
            return False, directional_candles
        return True, directional_candles

    def _has_large_opposite_candle(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> bool:
        if not bars:
            return False
        bodies = [abs(float(b.close) - float(b.open)) for b in bars]
        avg_body = sum(bodies) / max(1, len(bodies))
        threshold = avg_body * 1.5
        for b in bars:
            body = abs(float(b.close) - float(b.open))
            if body < threshold:
                continue
            if candidate.direction == TrendlineDirection.BULL and float(b.close) > float(b.open):
                return True
            if candidate.direction == TrendlineDirection.BEAR and float(b.close) < float(b.open):
                return True
        return False

    def _continuation_bar_index(self, post_break: List[OHLCVBar], candidate: TrendlineCandidate) -> Optional[int]:
        if not candidate.continuation_break_at:
            return None
        cb = self._normalize_dt(candidate.continuation_break_at)
        for i, b in enumerate(post_break):
            if self._normalize_dt(b.ts) >= cb:
                return i
        return None

    def _check_post_continue_chop_box_gate(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        idx_cont: Optional[int],
        continuation_level: Optional[float],
    ) -> tuple[bool, str]:
        """
        After continuation + settle: either fast extension in the trade direction (RVMD-style)
        within the first N bars, or — once N is exceeded without that extension — a clean
        break of the post-continuation range (OKLO-style box low / high). Skips SPY-style
        trendline breaks that oscillate in a band without committing.
        """
        if not self.config.post_continue_chop_box_gate_enabled:
            return True, ""
        if idx_cont is None or continuation_level is None:
            return True, ""
        ref = float(continuation_level)
        if abs(ref) < 1e-9:
            return True, ""
        seg = post_break[idx_cont + 1 :]
        if not seg:
            return True, ""

        cfg = self.config
        fb = max(1, int(cfg.post_continue_fast_followthrough_bars))
        ext_need = float(cfg.post_continue_fast_extension_pct)
        buf = float(cfg.post_continue_box_break_buffer_pct)
        min_prior = max(1, int(cfg.post_continue_chop_min_prior_bars))

        fast_len = min(len(seg), fb)
        fast_seg = seg[:fast_len]

        if candidate.direction == TrendlineDirection.BULL:
            # Close-based so a lone lower wick in chop does not fake fast follow-through.
            min_close_fast = min(b.close for b in fast_seg)
            ext_fast = (ref - min_close_fast) / abs(ref)
            if ext_fast >= ext_need:
                return True, ""
            if len(seg) <= fb:
                return False, (
                    f"awaiting_fast_downside_extension | bars_after_continuation={len(seg)}/{fb} | "
                    f"ext_fast={ext_fast:.6f} | min_ext={ext_need:.6f}"
                )
            prior = seg[:-1]
            if len(prior) < min_prior:
                return False, f"awaiting_box_break_prior_too_short | prior_bars={len(prior)} | min_prior={min_prior}"
            box_lo = min(b.low for b in prior)
            last_close = seg[-1].close
            threshold = box_lo * (1.0 - buf)
            if last_close < threshold:
                return True, ""
            return False, (
                f"post_chop_awaiting_box_low_break | box_lo={box_lo:.4f} | last_close={last_close:.4f} | "
                f"ext_fast_window={ext_fast:.6f}"
            )

        max_close_fast = max(b.close for b in fast_seg)
        ext_fast = (max_close_fast - ref) / abs(ref)
        if ext_fast >= ext_need:
            return True, ""
        if len(seg) <= fb:
            return False, (
                f"awaiting_fast_upside_extension | bars_after_continuation={len(seg)}/{fb} | "
                f"ext_fast={ext_fast:.6f} | min_ext={ext_need:.6f}"
            )
        prior = seg[:-1]
        if len(prior) < min_prior:
            return False, f"awaiting_box_break_prior_too_short | prior_bars={len(prior)} | min_prior={min_prior}"
        box_hi = max(b.high for b in prior)
        last_close = seg[-1].close
        threshold = box_hi * (1.0 + buf)
        if last_close > threshold:
            return True, ""
        return False, (
            f"post_chop_awaiting_box_high_break | box_hi={box_hi:.4f} | last_close={last_close:.4f} | "
            f"ext_fast_window={ext_fast:.6f}"
        )

    def _normalize_entry_archetype(self, raw: str) -> str:
        s = (raw or "").strip().upper().replace(" ", "_")
        if s == "IMPULSE_EXHAUSTION":
            return ENTRY_ARCHETYPE_EXHAUSTION
        if s in {ENTRY_ARCHETYPE_EXHAUSTION, ENTRY_ARCHETYPE_IMPULSE, ENTRY_ARCHETYPE_DRIFT}:
            return s
        low = (raw or "").strip().lower()
        if low == "impulse_exhaustion":
            return ENTRY_ARCHETYPE_EXHAUSTION
        if low in {"delayed_continuation", "weak_break_failure"}:
            return ENTRY_ARCHETYPE_DRIFT
        if low == "impulse_break":
            return ENTRY_ARCHETYPE_IMPULSE
        return ENTRY_ARCHETYPE_DRIFT

    def _reclaim_early_late(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, bool]:
        early = False
        late = False
        if not post_break or not candidate.trendline:
            return early, late
        n = max(1, int(self.config.bounceback_reclaim_max_bars))
        for i, pb in enumerate(post_break):
            line_px_pb = float(candidate.trendline.value_at(pb.ts))
            crossed = False
            if candidate.direction == TrendlineDirection.BULL and float(pb.close) >= line_px_pb:
                crossed = True
            elif candidate.direction == TrendlineDirection.BEAR and float(pb.close) <= line_px_pb:
                crossed = True
            if not crossed:
                continue
            if i < n:
                early = True
            else:
                late = True
        return early, late

    def _entry_survival_extra_bars(self, arch_raw: str) -> int:
        arch = self._normalize_entry_archetype(arch_raw)
        base = int(self._local_continuation_survival_extra_bars)
        cfg = self.config
        if arch == ENTRY_ARCHETYPE_IMPULSE:
            return base + int(cfg.entry_survival_extra_bars_impulse)
        if arch == ENTRY_ARCHETYPE_EXHAUSTION:
            return base + int(cfg.entry_survival_extra_bars_exhaustion)
        return base + int(cfg.entry_survival_extra_bars_drift)

    def _entry_survival_seconds(self, arch_raw: str) -> float:
        arch = self._normalize_entry_archetype(arch_raw)
        base = float(self._local_continuation_survival_sec)
        cfg = self.config
        if arch == ENTRY_ARCHETYPE_IMPULSE:
            return base * float(cfg.entry_survival_sec_mult_impulse)
        if arch == ENTRY_ARCHETYPE_EXHAUSTION:
            return base * float(cfg.entry_survival_sec_mult_exhaustion)
        return base * float(cfg.entry_survival_sec_mult_drift)

    def _entry_score_threshold_for(self, arch_raw: str) -> float:
        arch = self._normalize_entry_archetype(arch_raw)
        cfg = self.config
        if arch == ENTRY_ARCHETYPE_IMPULSE:
            return float(cfg.entry_score_min_impulse)
        if arch == ENTRY_ARCHETYPE_EXHAUSTION:
            return float(cfg.entry_score_min_exhaustion)
        return float(cfg.entry_score_min_continuation)

    def _break_bar_body_abs(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> float:
        br, _ = self._break_bar_body_ratio(candidate, bars)
        last = bars[-1] if bars else None
        if last is None:
            return 0.0
        rng = abs(float(last.high) - float(last.low))
        return float(br) * rng

    def _opposite_body_ratio_vs_break(self, candidate: TrendlineCandidate, bars: List[OHLCVBar], post_break: List[OHLCVBar]) -> float:
        bb_abs = self._break_bar_body_abs(candidate, bars)
        if bb_abs <= 1e-12:
            return 0.0
        recent = post_break[-4:] if len(post_break) >= 4 else post_break
        mx = 0.0
        for b in recent:
            body = abs(float(b.close) - float(b.open))
            if candidate.direction == TrendlineDirection.BULL and float(b.close) > float(b.open):
                mx = max(mx, body)
            elif candidate.direction == TrendlineDirection.BEAR and float(b.close) < float(b.open):
                mx = max(mx, body)
        return mx / bb_abs

    def _continuation_slope_component(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> float:
        if len(post_break) < 2:
            return 0.35
        tail = post_break[-min(5, len(post_break)) :]
        xs = list(range(len(tail)))
        ys = [float(b.close) for b in tail]
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
        den = sum((xi - mx) ** 2 for xi in xs) or 1e-12
        slope = num / den
        scale = abs(float(tail[-1].close)) or 1.0
        rel = slope / scale * float(len(tail))
        want_neg = candidate.direction == TrendlineDirection.BULL
        if want_neg:
            aligned = max(0.0, min(1.0, (-rel) * 800.0))
        else:
            aligned = max(0.0, min(1.0, rel * 800.0))
        return aligned

    def _compute_composite_entry_score(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        bars_early: List[OHLCVBar],
        *,
        break_meta: Dict[str, Any],
        break_distance_current: float,
        local_ok_gate: bool,
        structure_ok_gate: bool,
        continuation_dist: float,
        velocity_now: float,
        reclaimed: bool,
        early_reclaim: bool,
        late_reclaim: bool,
        opposite_conflict: bool,
        arch_raw: str,
    ) -> tuple[float, Dict[str, float]]:
        cfg = self.config
        arch = self._normalize_entry_archetype(arch_raw)
        br_body, _ = self._break_bar_body_ratio(candidate, bars_early)
        min_body = float(self._body_ratio_min_weak)
        if arch == ENTRY_ARCHETYPE_IMPULSE:
            min_body = float(self._body_ratio_min_strong)
        elif arch == ENTRY_ARCHETYPE_EXHAUSTION:
            min_body = float(self._body_ratio_min_weak) * 0.92
        body_c = min(1.0, float(br_body) / max(1e-9, min_body))

        dist_inc = bool(break_meta.get("distance_increasing"))
        last_pb = post_break[-1] if post_break else None
        prev_pb = post_break[-2] if len(post_break) >= 2 else None
        if last_pb is not None and prev_pb is not None and candidate.trendline is not None:
            lp = float(candidate.trendline.value_at(prev_pb.ts))
            lc = float(candidate.trendline.value_at(last_pb.ts))
            if abs(lp) > 1e-9 and abs(lc) > 1e-9:
                dp = abs(float(prev_pb.close) - lp) / abs(lp)
                dc = abs(float(last_pb.close) - lc) / abs(lc)
                dist_inc = dist_inc or (dc > dp * 1.01)
        dist_c = 1.0 if dist_inc else 0.28

        reclaim_c = 1.0
        if early_reclaim:
            reclaim_c = 0.0
        elif late_reclaim or reclaimed:
            reclaim_c = 0.42

        structure_c = 1.0 if structure_ok_gate else 0.38
        if local_ok_gate:
            loc_bonus = 1.0
        else:
            loc_bonus = min(
                1.0,
                max(0.25, continuation_dist / max(1e-9, float(cfg.min_continuation_distance_pct) * 4.0)),
            )

        slope_c = self._continuation_slope_component(candidate, post_break)
        vel_c = min(1.0, float(velocity_now) / max(1e-9, float(cfg.min_velocity_pct) * 2.2))

        persistence_tail = min(
            1.0,
            (float(continuation_dist) / max(1e-9, float(cfg.min_continuation_distance_pct) * 2.8)) * float(slope_c),
        )
        persistence_bonus = 0.045 * persistence_tail

        em = self._compute_expected_move_pct(candidate, post_break)
        if arch == ENTRY_ARCHETYPE_IMPULSE:
            min_em = float(self._min_expected_move_pct_impulse)
        elif arch == ENTRY_ARCHETYPE_EXHAUSTION:
            min_em = float(self._min_expected_move_pct_slow)
        else:
            min_em = float(self._min_expected_move_pct_default)
        em_c = min(1.0, float(em) / max(1e-9, min_em))

        expansion_fail, _ = self._fails_expansion_quality_filter(
            candidate,
            post_break,
            bars_for_timing=bars_early,
            body_ratio_break=float(br_body),
        )
        exp_c = 0.22 if expansion_fail else 1.0

        ft_need = max(1, int(cfg.confirmation_window_bars))
        ft_c = min(1.0, len(post_break) / float(ft_need + 3))

        chop = self._is_choppy_structure(candidate, bars_early)
        opp_recent = bool(
            self._has_large_opposite_candle(candidate, post_break[-3:] if len(post_break) >= 3 else post_break)
        )

        raw = (
            float(cfg.score_weight_structure) * structure_c * loc_bonus
            + float(cfg.score_weight_distance_increasing) * dist_c
            + float(cfg.score_weight_reclaim_hold) * reclaim_c
            + float(cfg.score_weight_continuation_slope) * slope_c
            + float(cfg.score_weight_continuation_velocity) * vel_c
            + float(cfg.score_weight_expected_move) * em_c
            + float(cfg.score_weight_body) * body_c
            + float(cfg.score_weight_expansion) * exp_c
            + float(cfg.score_weight_followthrough) * ft_c
            + float(persistence_bonus)
        )
        penalties = 0.0
        if chop:
            penalties += float(cfg.score_penalty_chop)
        if opposite_conflict or opp_recent:
            penalties += float(cfg.score_penalty_opposite_candle)
        if reclaimed or late_reclaim:
            penalties += float(cfg.score_penalty_reclaim) * (0.55 if late_reclaim and not early_reclaim else 1.0)

        score = max(0.0, min(1.0, raw - penalties))
        breakdown = {
            "structure": structure_c,
            "distance_increasing": dist_c,
            "reclaim_hold": reclaim_c,
            "slope": slope_c,
            "velocity": vel_c,
            "expected_move": em_c,
            "body": body_c,
            "expansion": exp_c,
            "followthrough": ft_c,
            "persistence_tail": persistence_tail,
            "persistence_bonus": persistence_bonus,
            "penalties": penalties,
            "raw_partial": raw,
        }
        return score, breakdown

    def _price_holds_beyond_line(self, candidate: TrendlineCandidate, close: float, ts: datetime) -> bool:
        """After valid break: BULL=support breakdown → stay below line; BEAR=resistance breakout → stay above line."""
        if not candidate.trendline:
            return False
        line_px = candidate.trendline.value_at(ts)
        if candidate.direction == TrendlineDirection.BULL:
            return close < line_px
        return close > line_px

    def _check_hold_requirement(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> _HoldEval:
        if not self.config.require_hold_after_break:
            return _HoldEval(True, "", False, len(post_break), 0.0)
        if not candidate.trendline or not candidate.break_event:
            return _HoldEval(False, TrendlineReasonCode.NO_STRUCTURE_HOLD.value, False, 0, 0.0)
        break_ts = candidate.break_event.candle_ts
        mode = (self.config.hold_mode or "time_based").strip().lower()

        if mode == "true_bar_based":
            need = max(1, self.config.hold_bars_after_break)
            if len(post_break) < need:
                last_dur = (post_break[-1].ts - break_ts).total_seconds() if post_break else 0.0
                return _HoldEval(False, "hold_pending", True, 0, last_dur)
            hold_slice = post_break[:need]
            for b in hold_slice:
                if not self._price_holds_beyond_line(candidate, b.close, b.ts):
                    return _HoldEval(False, TrendlineReasonCode.FAILED_HOLD.value, False, 0, 0.0)
                break_ref = candidate.break_event.close_price if candidate.break_event else b.close
                retrace = abs(b.close - break_ref) / max(abs(break_ref), 1e-9)
                if retrace > self.config.max_break_to_hold_retrace_pct:
                    return _HoldEval(False, TrendlineReasonCode.RETRACE_VIOLATION.value, False, 0, 0.0)
            candidate.hold_bars_achieved = len(hold_slice)
            candidate.hold_success_at = hold_slice[-1].ts
            dur = (hold_slice[-1].ts - break_ts).total_seconds()
            return _HoldEval(True, "", False, need, dur)

        # time_based (default): require min real time beyond line, all samples valid
        if not post_break:
            return _HoldEval(False, "hold_pending", True, 0, 0.0)
        for i, b in enumerate(post_break):
            if not self._price_holds_beyond_line(candidate, b.close, b.ts):
                return _HoldEval(False, TrendlineReasonCode.FAILED_HOLD.value, False, 0, 0.0)
            break_ref = candidate.break_event.close_price if candidate.break_event else b.close
            retrace = abs(b.close - break_ref) / max(abs(break_ref), 1e-9)
            if retrace > self.config.max_break_to_hold_retrace_pct:
                return _HoldEval(False, TrendlineReasonCode.RETRACE_VIOLATION.value, False, 0, 0.0)
            elapsed = (b.ts - break_ts).total_seconds()
            if elapsed >= self._confirm_seconds:
                candidate.hold_bars_achieved = i + 1
                candidate.hold_success_at = b.ts
                return _HoldEval(True, "", False, i + 1, elapsed)
        last_dur = (post_break[-1].ts - break_ts).total_seconds()
        return _HoldEval(False, "hold_pending", True, 0, last_dur)

    def _breakout_still_trending(self, candidate: TrendlineCandidate) -> bool:
        if not candidate.break_event or not candidate.trendline:
            return False
        bars = self._bar_cache.get(candidate.symbol, [])
        if not bars:
            return False
        last_bar = bars[-1]
        line_px = candidate.trendline.value_at(last_bar.ts)
        if candidate.direction == TrendlineDirection.BEAR:
            return bool(last_bar.close > line_px)
        return bool(last_bar.close < line_px)

    def _check_local_continuation(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        hold_end_index: int,
        *,
        waive_on_expansion: bool = True,
    ) -> tuple[bool, str, Optional[float]]:
        if not self.config.require_local_continuation_break:
            return True, "", None
        if (
            waive_on_expansion
            and post_break
            and self._post_break_expansion_strong(candidate, post_break)
        ):
            log.info(
                "TRENDLINE_CONTINUATION_WAIVED | symbol=%s | trade_id=%s | reason=post_break_expansion_strong",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return True, "", None
        if hold_end_index <= 0:
            hold_end_index = 1
        if len(post_break) <= hold_end_index:
            return False, TrendlineReasonCode.NO_LOCAL_CONTINUATION.value, None
        hold_slice = post_break[:hold_end_index]
        cont_slice = post_break[hold_end_index:]
        level: Optional[float] = None
        if candidate.direction == TrendlineDirection.BULL:
            level = min(b.low for b in hold_slice)
            for b in cont_slice:
                if b.close < level and ((level - b.close) / max(level, 1e-9)) >= self.config.min_continuation_distance_pct:
                    candidate.continuation_break_at = b.ts
                    return True, "", level
        else:
            level = max(b.high for b in hold_slice)
            for b in cont_slice:
                if b.close > level and ((b.close - level) / max(level, 1e-9)) >= self.config.min_continuation_distance_pct:
                    candidate.continuation_break_at = b.ts
                    return True, "", level
        return False, TrendlineReasonCode.NO_LOCAL_CONTINUATION.value, level

    def _check_post_break_structure(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, str]:
        if not self.config.require_post_break_structure:
            return True, ""
        lb = max(2, self.config.post_break_structure_lookback_bars)
        if len(post_break) < lb:
            return False, TrendlineReasonCode.NO_POST_BREAK_STRUCTURE.value
        sample = post_break[-lb:]
        if candidate.direction == TrendlineDirection.BULL:
            lows = [b.low for b in sample]
            # Allow last bar to tie the prior-window low (strict < rejected marginal follow-through).
            if len(lows) >= 2 and not (lows[-1] <= min(lows[:-1])):
                return False, TrendlineReasonCode.NO_POST_BREAK_STRUCTURE.value
        else:
            highs = [b.high for b in sample]
            if len(highs) >= 2 and not (highs[-1] >= max(highs[:-1])):
                return False, TrendlineReasonCode.NO_POST_BREAK_STRUCTURE.value
        return True, ""

    def _is_choppy_structure(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> bool:
        if not candidate.trendline or len(bars) < 3:
            return False
        recent = bars[-max(3, self.config.chop_recent_bars):]
        crosses = 0
        prev_side = None
        for b in recent:
            line_px = candidate.trendline.value_at(b.ts)
            side = 1 if b.close >= line_px else -1
            if prev_side is not None and side != prev_side:
                crosses += 1
            prev_side = side
        if crosses > self.config.chop_max_crosses:
            return True

        highs = [b.high for b in recent]
        lows = [b.low for b in recent]
        if not highs or not lows:
            return False
        recent_range = max(highs) - min(lows)
        orb_high = (candidate.setup_payload or {}).get("orb_high")
        orb_low = (candidate.setup_payload or {}).get("orb_low")
        try:
            orb_range = float(orb_high) - float(orb_low)
        except (TypeError, ValueError):
            return False
        if orb_range > 0 and recent_range < (orb_range * self.config.chop_small_range_vs_orb_ratio):
            return True
        return False

    def _compute_break_quality_score(self, confirmation: TrendlineMomentumConfirmation) -> float:
        velocity_score = max(0.0, min(1.0, confirmation.velocity_pct / max(1e-9, self.config.min_velocity_pct * 2.0)))
        range_score = max(0.0, min(1.0, confirmation.range_expansion_ratio / max(1.0, self.config.range_expansion_multiplier)))
        followthrough_score = max(0.0, min(1.0, confirmation.bars_used / max(1, self.config.confirmation_window_bars)))
        return (velocity_score * 0.4) + (range_score * 0.3) + (followthrough_score * 0.3)

    def _log_missed_move(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar], reason: str = "no_confirmation") -> None:
        if not candidate.break_event:
            return
        move = 0.0
        if post_break:
            entry_ref = candidate.break_event.close_price
            if entry_ref > 0:
                if candidate.direction == TrendlineDirection.BULL:
                    best = min(b.low for b in post_break)
                    move = (entry_ref - best) / entry_ref
                else:
                    best = max(b.high for b in post_break)
                    move = (best - entry_ref) / entry_ref
        peak_idx = 0
        if post_break and candidate.break_event and candidate.break_event.close_price > 0:
            entry_ref = float(candidate.break_event.close_price)
            best_metric = float("-inf")
            for i, b in enumerate(post_break):
                metric = (
                    (entry_ref - float(b.low)) / entry_ref
                    if candidate.direction == TrendlineDirection.BULL
                    else (float(b.high) - entry_ref) / entry_ref
                )
                if metric > best_metric:
                    best_metric = metric
                    peak_idx = i
        self._decision_stats["missed_opportunity"] += 1
        log.info(
            "TRENDLINE_MISSED_OPPORTUNITY | symbol=%s | trade_id=%s | max_move_pct_after_break=%.4f | time_to_peak_bars=%d | reason_not_taken=%s",
            candidate.symbol,
            self._trade_id(candidate),
            move,
            max(0, peak_idx),
            reason,
        )

    def dry_run_candidate(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> Dict[str, object]:
        """Developer helper for deterministic candidate replay validation."""
        key = f"{candidate.symbol}:{candidate.direction.value}"
        self._candidates[key] = candidate
        self._bar_cache[candidate.symbol] = []
        states: List[str] = []
        emitted = False
        for bar in bars:
            sig = self.process_new_bar(candidate.symbol, bar)
            states.append(candidate.state.value)
            if sig:
                emitted = True
                break
        return {
            "candidate_id": key,
            "symbol": candidate.symbol,
            "built": candidate.trendline is not None,
            "break_detected": candidate.break_event is not None,
            "momentum_confirmed": bool(candidate.momentum_confirmation and candidate.momentum_confirmation.status == MomentumStatus.CONFIRMED),
            "would_execute": emitted,
            "final_state": candidate.state.value,
            "state_path": states,
        }

    def _is_expired(self, now: datetime, candidate: TrendlineCandidate) -> bool:
        local = self._normalize_dt(now).astimezone(self._tz_pt)
        hh, mm = self._parse_hhmm(self.config.expiration_time_pt)
        expiry_local = datetime.combine(local.date(), time(hh, mm), self._tz_pt)
        start = getattr(candidate, "start_time", None)
        if start is not None:
            start_local = self._normalize_dt(start).astimezone(self._tz_pt)
            elapsed_min = (local - start_local).total_seconds() / 60.0
            if elapsed_min < float(self._max_active_minutes):
                return False
        return local >= expiry_local

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int]:
        parts = value.split(":")
        return int(parts[0]), int(parts[1])

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        """Return timezone-aware UTC datetime for safe comparisons."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _set_state(candidate: TrendlineCandidate, state: TrendlineCandidateState, reason: str) -> None:
        candidate.state = state
        candidate.state_reason = reason
        candidate.updated_at = datetime.now(timezone.utc)


def classify_break_archetype(
    signal_engine: TrendlineSignalEngine,
    *,
    bars: List[OHLCVBar],
    candidate: TrendlineCandidate,
    break_meta: Dict[str, Any],
    body_ratio: float,
    break_distance_pct: float,
    expected_move_pct: float,
    distance_increasing: bool,
    reversal_slice: Optional[List[OHLCVBar]] = None,
) -> str:
    """Wrapper for tests/tools; persisted value is written in _apply_break_archetype_on_break."""
    return signal_engine._infer_break_archetype(
        bars=bars,
        candidate=candidate,
        break_meta=break_meta,
        body_ratio=body_ratio,
        break_distance_pct=break_distance_pct,
        expected_move_pct=expected_move_pct,
        dist_inc_meta=distance_increasing,
        reversal_slice=reversal_slice,
    )

