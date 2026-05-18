#!/usr/bin/env python3
"""
Trendline break detector.
"""

from __future__ import annotations

import logging
from typing import Optional

from .trendline_models import (
    BreakStatus,
    OHLCVBar,
    TrendlineBreakEvent,
    TrendlineConfig,
    TrendlineDefinition,
    TrendlineDirection,
)

log = logging.getLogger(__name__)


class TrendlineBreakDetector:
    """Detect close-based trendline breaks with anti-micro-break filtering."""

    def __init__(self, config: Optional[TrendlineConfig] = None) -> None:
        self.config = config or TrendlineConfig()
        self._strong_breakout_distance_mult = float(
            getattr(self.config, "strong_breakout_distance_mult", 1.2) or 1.2
        )
        self._body_expansion_mult = float(getattr(self.config, "body_expansion_mult", 1.3) or 1.3)
        self._clean_breakout_bypass_momentum = bool(
            getattr(self.config, "clean_breakout_bypass_momentum", True)
        )

    def detect_break(
        self,
        trendline: TrendlineDefinition,
        bar: OHLCVBar,
        previous_bar: Optional[OHLCVBar] = None,
    ) -> TrendlineBreakEvent:
        """
        Validate whether a bar close has materially broken the trendline.

        Line geometry (``TrendlineDirection``) is structure-derived, not ORB side:
        - BULL = ascending support → valid break is close *below* line (breakdown).
        - BEAR = descending resistance → valid break is close *above* line (breakout).

        Distance must exceed configured threshold (percent and optionally ATR).
        """
        line_px = trendline.value_at(bar.ts)
        close = bar.close
        raw_distance = close - line_px
        breakout_distance = abs(raw_distance) / line_px if line_px > 0 else 0.0
        body = abs(bar.close - bar.open)
        prev_body = abs(previous_bar.close - previous_bar.open) if previous_bar else 0.0

        threshold = max(self.config.min_break_pct * line_px, 0.0)
        if self.config.use_atr_break and bar.atr and bar.atr > 0:
            threshold = max(threshold, bar.atr * self.config.atr_break_multiplier)

        # Early-break mode: close crossing line is the trigger. Threshold is still computed
        # for breakout quality classification and diagnostics, but not required for entry.
        if trendline.direction == TrendlineDirection.BULL:
            is_break = close <= line_px
        else:
            is_break = close >= line_px

        # "Clean" means the break is not only beyond the threshold, but also
        # extends farther vs prior bar and has an expanding candle body.
        prev_line = trendline.value_at(previous_bar.ts) if previous_bar else line_px
        prev_distance = abs(previous_bar.close - prev_line) if previous_bar else 0.0
        distance_increasing = abs(raw_distance) > prev_distance
        body_expanding = (
            body >= (prev_body * self._body_expansion_mult)
            if previous_bar and prev_body > 0
            else (body > 0)
        )
        threshold_pct = threshold / line_px if line_px > 0 else 0.0
        breakout_is_clean = bool(is_break and distance_increasing and body_expanding)
        strong_breakout = bool(
            is_break
            and breakout_distance >= (self._strong_breakout_distance_mult * threshold_pct)
            and distance_increasing
            and body_expanding
        )
        min_bd = float(getattr(self.config, "break_distance_min", 0.0015) or 0.0015)
        # Anti-overfit: do not downgrade valid close-cross breaks solely on break distance.
        # Keep break_distance_min as a quality/scoring input only.
        breakout_direction = "up" if raw_distance > 0 else "down"
        body_ratio = body / max(prev_body, 1e-6) if previous_bar else 0.0
        log.info(
            "TRENDLINE_BREAKOUT_METRICS | symbol=%s | distance=%.4f | body_ratio=%.2f | strong=%s",
            trendline.symbol,
            breakout_distance,
            body_ratio,
            strong_breakout,
        )

        bypass = self._clean_breakout_bypass_momentum
        if not is_break:
            return TrendlineBreakEvent(
                symbol=trendline.symbol,
                direction=trendline.direction,
                status=BreakStatus.NONE,
                candle_ts=bar.ts,
                close_price=close,
                trendline_price=line_px,
                break_distance=raw_distance,
                break_distance_pct=breakout_distance,
                threshold_used=threshold,
                reason="close_not_beyond_threshold",
                metadata={
                    "breakout_direction": breakout_direction,
                    "breakout_is_clean": False,
                    "strong_breakout": False,
                    "distance_increasing": distance_increasing,
                    "body_expanding": body_expanding,
                    "clean_breakout_bypass_momentum": bypass,
                    "body_ratio_vs_prev": body_ratio,
                    "body_ratio": body_ratio,
                },
            )

        log.info(
            "TRENDLINE_EARLY_BREAK | symbol=%s | close=%.4f | line=%.4f",
            trendline.symbol,
            close,
            line_px,
        )

        # Optional catastrophic micro-break rejection using prior close context.
        # This is the only hard veto here and requires all of:
        # - extremely tiny break distance
        # - weak body ratio
        # - distance not increasing
        # - no continuation signal yet (proxied by non-expanding body)
        if previous_bar is not None:
            prev_gap = abs(previous_bar.close - prev_line)
            catastrophic_micro_break = (
                breakout_distance < max(min_bd * 0.20, 0.00015)
                and body_ratio < float(getattr(self.config, "body_ratio_min_weak", 0.35) or 0.35)
                and (not distance_increasing)
                and (not body_expanding)
                and prev_gap <= (threshold * 0.25)
            )
            if catastrophic_micro_break:
                return TrendlineBreakEvent(
                    symbol=trendline.symbol,
                    direction=trendline.direction,
                    status=BreakStatus.REJECTED,
                    candle_ts=bar.ts,
                    close_price=close,
                    trendline_price=line_px,
                    break_distance=raw_distance,
                    break_distance_pct=breakout_distance,
                    threshold_used=threshold,
                    reason="catastrophic_micro_break",
                    metadata={
                        "breakout_direction": breakout_direction,
                        "breakout_is_clean": breakout_is_clean,
                        "strong_breakout": strong_breakout,
                        "distance_increasing": distance_increasing,
                        "body_expanding": body_expanding,
                        "catastrophic_micro_break": True,
                        "clean_breakout_bypass_momentum": bypass,
                        "body_ratio_vs_prev": body_ratio,
                        "body_ratio": body_ratio,
                    },
                )

        return TrendlineBreakEvent(
            symbol=trendline.symbol,
            direction=trendline.direction,
            status=BreakStatus.DETECTED,
            candle_ts=bar.ts,
            close_price=close,
            trendline_price=line_px,
            break_distance=raw_distance,
            break_distance_pct=breakout_distance,
            threshold_used=threshold,
            reason="valid_close_break",
            metadata={
                "breakout_direction": breakout_direction,
                "breakout_is_clean": breakout_is_clean,
                "strong_breakout": strong_breakout,
                "distance_increasing": distance_increasing,
                "body_expanding": body_expanding,
                "clean_breakout_bypass_momentum": bypass,
                "body_ratio_vs_prev": body_ratio,
                "body_ratio": body_ratio,
            },
        )
