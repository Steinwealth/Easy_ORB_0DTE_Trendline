#!/usr/bin/env python3
"""
Trendline builder for Easy Trendline 0DTE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .trendline_models import (
    OHLCVBar,
    TrendlineAnchor,
    TrendlineConfig,
    TrendlineDefinition,
    TrendlineDirection,
)

log = logging.getLogger(__name__)

# ORB-relative narrative guardrails: avoid failed_upside→PUT / failed_downside→CALL
# dominating when price is exhausted against the opposite ORB boundary.
_ORB_CLASSIFIER_NEAR_LOW_PCT = 0.25
_ORB_CLASSIFIER_EXHAUST_LOW_PCT = 0.18
_ORB_CLASSIFIER_NEAR_HIGH_PCT = 0.75
_ORB_CLASSIFIER_EXHAUST_HIGH_PCT = 0.82
_ORB_PUT_FAILURE_MIN_STRENGTH_NEAR_LOW = 0.55
_ORB_PUT_FAILURE_MIN_STRENGTH_EXHAUST_LOW = 0.70
_ORB_CALL_FAILURE_MIN_STRENGTH_NEAR_HIGH = 0.55
_ORB_CALL_FAILURE_MIN_STRENGTH_EXHAUST_HIGH = 0.70


@dataclass
class OrbTestFailureClassification:
    """ORB test / failure diagnostics for post-7:30 controlling-line selection (Trendline path)."""

    setup_side: str  # "call" | "put" | "none"
    line_direction: Optional[TrendlineDirection]
    test_side: str  # "downside_test" | "upside_test" | "none"
    failure_type: str  # failed_downside | failed_upside | compression | trend_continuation | unclear
    confidence: float
    score: float
    anchor_one_detail: Dict[str, Any] = field(default_factory=dict)
    anchor_two_detail: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    selected_line_reason: str = ""
    failure_point_index_in_post_orb: Optional[int] = None
    classifier_override_reason: str = ""


class TrendlineBuilder:
    """
    Build line geometry from ORB anchors plus pre-7:30 price structure.

    ``TrendlineDirection.BULL`` = ascending support (ORB low + higher lows).
    ``TrendlineDirection.BEAR`` = descending resistance (ORB high + lower highs).

    **Anchor law (current implementation, not 7:30-close-centric):** bull line anchor
    one is **ORB low** (timestamp from ``orb_low_ts`` when present), anchor two is the
    **deepest higher low** among pre-cutoff bars (see ``_find_higher_low_anchor``), not
    the 7:30 last close. Bear line uses **ORB high** + **highest lower high** among
    qualifying bars. ``value_at(7:30 cutoff)`` is therefore an *extrapolation* of that
    two-point line to the cutoff instant, not a line drawn from cutoff price to a
    post-cutoff extreme.

    Which geometry applies to a symbol is decided by ``trendline_setup_selector``,
    not by ORB signal collection side.
    """

    def __init__(self, config: Optional[TrendlineConfig] = None) -> None:
        self.config = config or TrendlineConfig()
        self._tz_pt = ZoneInfo("America/Los_Angeles")

    def _post_orb_window(self, bars: List[OHLCVBar], orb_context: Dict[str, Any]) -> List[OHLCVBar]:
        """Bars strictly after 6:45 PT ORB end through 7:30 PT (same window as classify_orb_test_failure)."""
        if not bars:
            return []
        nh = self._safe_float(orb_context.get("orb_high"))
        nl = self._safe_float(orb_context.get("orb_low"))
        if nh is None or nl is None or nh <= nl:
            return []
        ref_ts = bars[0].ts
        tz = ref_ts.tzinfo or timezone.utc
        local0 = ref_ts.astimezone(self._tz_pt)
        day = local0.date()
        orb_end_local = datetime.combine(day, time(6, 45), self._tz_pt)
        cutoff_local = datetime.combine(day, time(7, 30), self._tz_pt)
        orb_end_utc = orb_end_local.astimezone(tz)
        cutoff_utc = cutoff_local.astimezone(tz)
        return [
            b
            for b in bars
            if self._normalize_ts(b.ts) > self._normalize_ts(orb_end_utc)
            and self._normalize_ts(b.ts) <= self._normalize_ts(cutoff_utc)
        ]

    # LEGACY_VALIDATION (non-selector path only): historically used when the signal engine
    # built trendlines without a selector-supplied line. Production flow uses
    # trendline_setup_selector; callers should not rely on this for selector-built setups.
    def validate_post_failure_anchor_structure(
        self,
        symbol: str,
        bars: List[OHLCVBar],
        orb_context: Dict[str, Any],
        cls: OrbTestFailureClassification,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        CALL (failed_downside): require >=2 higher-low transitions after downside failure point.
        PUT (failed_upside): require >=2 lower-high transitions after upside failure point.
        """
        if cls.failure_type not in ("failed_downside", "failed_upside"):
            log.info(
                "TRENDLINE_ANCHOR_POINTS_VALIDATED | symbol=%s | passed=true | reason=not_applicable | failure_type=%s",
                symbol,
                cls.failure_type,
            )
            return True, "not_applicable", {"failure_type": cls.failure_type}
        window = self._post_orb_window(bars, orb_context)
        if len(window) < 4:
            log.info(
                "TRENDLINE_ANCHOR_POINTS_VALIDATED | symbol=%s | passed=false | reason=insufficient_window | n=%d",
                symbol,
                len(window),
            )
            return False, "insufficient_window", {"window_bars": len(window)}
        lows = [float(b.low) for b in window]
        highs = [float(b.high) for b in window]
        if cls.failure_type == "failed_downside":
            min_low = min(lows)
            fp = max(i for i, v in enumerate(lows) if v == min_low)
            post = lows[fp + 1 :]
            hl_trans = sum(1 for j in range(1, len(post)) if post[j] > post[j - 1])
            ok = hl_trans >= 2
            detail: Dict[str, Any] = {
                "failure_point_index": fp,
                "post_segment_bars": len(post),
                "higher_low_transitions": hl_trans,
            }
            log.info(
                "TRENDLINE_ANCHOR_POINTS_VALIDATED | symbol=%s | passed=%s | setup=call_failed_downside | "
                "failure_point_index=%d | higher_low_transitions=%d | post_segment_bars=%d",
                symbol,
                str(ok).lower(),
                fp,
                hl_trans,
                len(post),
            )
            if not ok:
                return False, "insufficient_structure_points", detail
            return True, "ok", detail
        max_high = max(highs)
        fp = max(i for i, v in enumerate(highs) if v == max_high)
        post_h = highs[fp + 1 :]
        lh_trans = sum(1 for j in range(1, len(post_h)) if post_h[j] < post_h[j - 1])
        ok = lh_trans >= 2
        detail = {
            "failure_point_index": fp,
            "post_segment_bars": len(post_h),
            "lower_high_transitions": lh_trans,
        }
        log.info(
            "TRENDLINE_ANCHOR_POINTS_VALIDATED | symbol=%s | passed=%s | setup=put_failed_upside | "
            "failure_point_index=%d | lower_high_transitions=%d | post_segment_bars=%d",
            symbol,
            str(ok).lower(),
            fp,
            lh_trans,
            len(post_h),
        )
        if not ok:
            return False, "insufficient_structure_points", detail
        return True, "ok", detail

    def build_from_intraday_data(
        self,
        symbol: str,
        direction: TrendlineDirection,
        bars: List[OHLCVBar],
        orb_context: Dict[str, Any],
    ) -> Optional[TrendlineDefinition]:
        """
        Build trendline definition from intraday bars and ORB context.

        Bull (ascending support):
          - anchor one: **ORB low price** at **orb_low_ts** when provided (earliest bar in
            6:30–6:45 PT that printed that low); else ORB capture_time.
          - anchor two: among bars with timestamp **at or before 7:30 PT** (cutoff), at least
            ``min_anchor_distance_minutes`` after anchor one, and with ``bar.low`` **strictly
            above** ORB low, choose the single bar whose **low is the minimum** (deepest
            “higher low” in the pre-7:30 window). That is **not** “the 7:30 candle low” by
            rule: if the deepest higher low occurs at e.g. 7:17, anchor two is **7:17**; the
            7:30 bar only becomes anchor two if its low is the **lowest** among all qualifying
            bars.
        Bear (descending resistance):
          - anchor one: **ORB high price** at **orb_high_ts** when provided (earliest bar in
            6:30–6:45 PT that printed that high); else ORB capture_time.
          - anchor two: among qualifying bars with bar.high **strictly below** ORB high,
            pick the bar whose **high is maximum** (lowest lower high before cutoff)
        """
        if not bars:
            log.debug("Trendline build skipped for %s: empty bars", symbol)
            return None

        cutoff = self._session_cutoff(bars[0].ts)
        prebuild_bars = [b for b in bars if b.ts <= cutoff]
        if len(prebuild_bars) < 2:
            log.debug("Trendline build skipped for %s: insufficient bars before cutoff", symbol)
            return None

        if direction == TrendlineDirection.BULL:
            first = self._build_bull_anchor_one(symbol, prebuild_bars, orb_context)
            second = self._find_higher_low_anchor(prebuild_bars, first)
        else:
            first = self._build_bear_anchor_one(symbol, prebuild_bars, orb_context)
            second = self._find_lower_high_anchor(prebuild_bars, first)

        if not first or not second:
            log.info("Trendline build failed for %s (%s): missing anchors", symbol, direction.value)
            return None

        seconds = second.ts.timestamp() - first.ts.timestamp()
        if seconds <= 0:
            log.info("Trendline build failed for %s: non-positive anchor spacing", symbol)
            return None

        slope = (second.price - first.price) / seconds
        if direction == TrendlineDirection.BULL and slope <= 0:
            log.info("Trendline build failed for %s: bull slope not ascending", symbol)
            return None
        if direction == TrendlineDirection.BEAR and slope >= 0:
            log.info("Trendline build failed for %s: bear slope not descending", symbol)
            return None

        intercept = first.price - (slope * first.ts.timestamp())
        spacing_min = round(seconds / 60.0, 2)
        if spacing_min >= 20:
            line_quality = "good"
        elif spacing_min >= 10:
            line_quality = "ok"
        else:
            line_quality = "poor"
        log.info(
            "TRENDLINE_PIPELINE | stage=line_built | symbol=%s | direction=%s | "
            "anchor1_ts=%s | anchor1_px=%.4f | anchor2_ts=%s | anchor2_px=%.4f | "
            "slope_per_s=%.10f | spacing_min=%.2f | prebuild_bars=%d",
            symbol,
            direction.value,
            first.ts.isoformat(),
            float(first.price),
            second.ts.isoformat(),
            float(second.price),
            slope,
            spacing_min,
            len(prebuild_bars),
        )
        meta = {
            "cutoff_pt": cutoff.isoformat(),
            "prebuild_bar_count": len(prebuild_bars),
            "anchor_spacing_min": spacing_min,
            "line_quality": line_quality,
            "anchor_one_source": str(first.source),
            "anchor_two_source": str(second.source),
        }
        log.info(
            "TRENDLINE_LINE_SELECTED | symbol=%s | direction=%s | reason=geometry_built | "
            "anchor_one_source=%s | anchor_two_source=%s | anchor_spacing_min=%.2f",
            symbol,
            direction.value,
            meta["anchor_one_source"],
            meta["anchor_two_source"],
            spacing_min,
        )
        return TrendlineDefinition(
            symbol=symbol,
            direction=direction,
            anchor_one=first,
            anchor_two=second,
            slope_per_second=slope,
            intercept=intercept,
            built_at=datetime.now(timezone.utc),
            metadata=meta,
        )

    def build_cutoff_to_farthest_extreme(
        self,
        symbol: str,
        pre_cutoff: List[OHLCVBar],
        orb_context: Dict[str, Any],
        *,
        regime: str,
    ) -> Optional[TrendlineDefinition]:
        """
        Exhaustion / reversal geometry: line from the farthest session extreme (high or low)
        in the pre-cutoff window to the **7:30 cutoff last close** (second anchor).

        - ``regime="below_orb"``: last close **strictly below** ORB low. Picks the extreme
          (among bars ending at least ``min_anchor_distance_minutes`` before the cutoff bar)
          whose price is farthest from the cutoff close; ties favor **high** → typically
          **descending** line → ``TrendlineDirection.BEAR`` (resistance) → CALL breakout.
        - ``regime="above_orb"``: last close **strictly above** ORB high. Ties favor **low**
          → typically **ascending** line → ``TrendlineDirection.BULL`` (support) → PUT breakdown.

        Slope sign sets geometry (bull = ascending support, bear = descending resistance).
        """
        if len(pre_cutoff) < 2:
            return None
        cutoff = self._session_cutoff(pre_cutoff[0].ts)
        last_bar = pre_cutoff[-1]
        if self._normalize_ts(last_bar.ts) > self._normalize_ts(cutoff):
            eligible = [b for b in pre_cutoff if self._normalize_ts(b.ts) <= self._normalize_ts(cutoff)]
            if not eligible:
                return None
            last_bar = max(eligible, key=lambda b: b.ts)
        cutoff_close = float(last_bar.close)
        cutoff_ts = last_bar.ts
        oh = self._safe_float(orb_context.get("orb_high"))
        ol = self._safe_float(orb_context.get("orb_low"))
        if oh is None or ol is None or oh <= ol:
            return None
        if regime == "below_orb":
            if cutoff_close >= float(ol):
                return None
        elif regime == "above_orb":
            if cutoff_close <= float(oh):
                return None
        else:
            return None

        min_gap_s = max(60.0, float(self.config.min_anchor_distance_minutes) * 60.0)
        cutoff_epoch = cutoff_ts.timestamp()
        pool = [b for b in pre_cutoff if b.ts.timestamp() <= cutoff_epoch - min_gap_s]
        if not pool:
            pool = pre_cutoff[:-1]
        if not pool:
            return None

        max_h_bar = max(pool, key=lambda b: float(b.high))
        min_l_bar = min(pool, key=lambda b: float(b.low))
        nh = float(max_h_bar.high)
        nl = float(min_l_bar.low)
        d_high = abs(nh - cutoff_close)
        d_low = abs(cutoff_close - nl)
        eps = 1e-9
        if regime == "below_orb":
            use_high = d_high > d_low + eps or (abs(d_high - d_low) <= eps)
        else:
            use_high = d_high > d_low + eps and abs(d_high - d_low) > eps

        if use_high:
            first = TrendlineAnchor(ts=max_h_bar.ts, price=nh, source="session_extreme_high")
            extreme_label = "high"
        else:
            first = TrendlineAnchor(ts=min_l_bar.ts, price=nl, source="session_extreme_low")
            extreme_label = "low"
        second = TrendlineAnchor(ts=cutoff_ts, price=cutoff_close, source="cutoff_last_close")

        seconds = second.ts.timestamp() - first.ts.timestamp()
        if seconds <= 0:
            log.info(
                "Trendline cutoff-extreme build failed for %s: non-positive anchor spacing | regime=%s",
                symbol,
                regime,
            )
            return None

        slope = (second.price - first.price) / seconds
        if abs(slope) < 1e-15:
            log.info("Trendline cutoff-extreme build failed for %s: flat slope | regime=%s", symbol, regime)
            return None
        if slope > 0:
            direction = TrendlineDirection.BULL
        else:
            direction = TrendlineDirection.BEAR

        intercept = first.price - (slope * first.ts.timestamp())
        spacing_min = round(seconds / 60.0, 2)
        line_quality = "good" if spacing_min >= 10 else "ok"
        meta = {
            "cutoff_pt": cutoff.isoformat(),
            "prebuild_bar_count": len(pre_cutoff),
            "anchor_spacing_min": spacing_min,
            "line_quality": line_quality,
            "anchor_one_source": str(first.source),
            "anchor_two_source": str(second.source),
            "construction_rule": "cutoff_to_farthest_extreme",
            "cutoff_extreme_regime": regime,
            "cutoff_extreme_choice": extreme_label,
        }
        log.info(
            "TRENDLINE_PIPELINE | stage=line_built | symbol=%s | direction=%s | reason=cutoff_to_farthest_extreme | "
            "regime=%s | extreme=%s | anchor1_ts=%s | anchor1_px=%.4f | anchor2_ts=%s | anchor2_px=%.4f | "
            "slope_per_s=%.10f | spacing_min=%.2f",
            symbol,
            direction.value,
            regime,
            extreme_label,
            first.ts.isoformat(),
            float(first.price),
            second.ts.isoformat(),
            float(second.price),
            slope,
            spacing_min,
        )
        return TrendlineDefinition(
            symbol=symbol,
            direction=direction,
            anchor_one=first,
            anchor_two=second,
            slope_per_second=slope,
            intercept=intercept,
            built_at=datetime.now(timezone.utc),
            metadata=meta,
        )

    def _session_cutoff(self, reference_ts: datetime) -> datetime:
        local = reference_ts.astimezone(self._tz_pt)
        cutoff_local = datetime.combine(local.date(), time(7, 30), self._tz_pt)
        return cutoff_local.astimezone(reference_ts.tzinfo or self._tz_pt)

    def _build_bull_anchor_one(
        self, symbol: str, bars: List[OHLCVBar], orb_context: Dict[str, Any]
    ) -> Optional[TrendlineAnchor]:
        orb_low = self._safe_float(orb_context.get("orb_low"))
        if orb_low is None:
            log.info(
                "TRENDLINE_PIPELINE | stage=fallback_orb_extreme_used | symbol=%s | anchor=orb_low | reason=missing_orb_low_price",
                symbol,
            )
            log.info(
                "TRENDLINE_FALLBACK_USED | symbol=%s | reason=missing_orb_low_price | fallback_type=bar_session_low",
                symbol,
            )
            return self._fallback_bar_anchor(min(bars, key=lambda b: b.low), "fallback_orb_low")

        explicit_ts = orb_context.get("orb_low_ts")
        if explicit_ts is None or not isinstance(explicit_ts, datetime):
            log.info(
                "TRENDLINE_PIPELINE | stage=fallback_orb_extreme_used | symbol=%s | anchor=orb_low | reason=missing_orb_low_ts_using_fallback_ts",
                symbol,
            )
            log.info(
                "TRENDLINE_FALLBACK_USED | symbol=%s | reason=missing_orb_low_ts | fallback_type=orb_ts_session_default",
                symbol,
            )
        orb_ts = explicit_ts or orb_context.get("orb_ts") or bars[0].ts
        orb_ts = orb_ts if isinstance(orb_ts, datetime) else bars[0].ts
        return TrendlineAnchor(ts=orb_ts, price=orb_low, source="orb_low")

    def _build_bear_anchor_one(
        self, symbol: str, bars: List[OHLCVBar], orb_context: Dict[str, Any]
    ) -> Optional[TrendlineAnchor]:
        orb_high = self._safe_float(orb_context.get("orb_high"))
        if orb_high is None:
            log.info(
                "TRENDLINE_PIPELINE | stage=fallback_orb_extreme_used | symbol=%s | anchor=orb_high | reason=missing_orb_high_price",
                symbol,
            )
            log.info(
                "TRENDLINE_FALLBACK_USED | symbol=%s | reason=missing_orb_high_price | fallback_type=bar_session_high",
                symbol,
            )
            return self._fallback_bar_anchor(max(bars, key=lambda b: b.high), "fallback_orb_high")

        explicit_ts = orb_context.get("orb_high_ts")
        if explicit_ts is None or not isinstance(explicit_ts, datetime):
            log.info(
                "TRENDLINE_PIPELINE | stage=fallback_orb_extreme_used | symbol=%s | anchor=orb_high | reason=missing_orb_high_ts_using_fallback_ts",
                symbol,
            )
            log.info(
                "TRENDLINE_FALLBACK_USED | symbol=%s | reason=missing_orb_high_ts | fallback_type=orb_ts_session_default",
                symbol,
            )
        orb_ts = explicit_ts or orb_context.get("orb_ts") or bars[0].ts
        orb_ts = orb_ts if isinstance(orb_ts, datetime) else bars[0].ts
        return TrendlineAnchor(ts=orb_ts, price=orb_high, source="orb_high")

    def _find_higher_low_anchor(
        self, bars: List[OHLCVBar], first: TrendlineAnchor
    ) -> Optional[TrendlineAnchor]:
        """Deepest higher low before 7:30: min(bar.low) over bars with low > ORB low, not the 7:30 candle per se."""
        min_gap = self.config.min_anchor_distance_minutes * 60
        candidates = [b for b in bars if b.ts.timestamp() - first.ts.timestamp() >= min_gap and b.low > first.price]
        if not candidates:
            return None
        anchor_bar = min(candidates, key=lambda b: b.low)
        return TrendlineAnchor(ts=anchor_bar.ts, price=anchor_bar.low, source="higher_low")

    def _find_lower_high_anchor(
        self, bars: List[OHLCVBar], first: TrendlineAnchor
    ) -> Optional[TrendlineAnchor]:
        min_gap = self.config.min_anchor_distance_minutes * 60
        candidates = [b for b in bars if b.ts.timestamp() - first.ts.timestamp() >= min_gap and b.high < first.price]
        if not candidates:
            return None
        anchor_bar = max(candidates, key=lambda b: b.high)
        return TrendlineAnchor(ts=anchor_bar.ts, price=anchor_bar.high, source="lower_high")

    def build_reanchored_from_recent_bars(
        self,
        symbol: str,
        direction: TrendlineDirection,
        bars: List[OHLCVBar],
        *,
        min_touches: int = 3,
    ) -> Optional[TrendlineDefinition]:
        """
        Build a secondary intraday structure from recent bars after 7:30.
        Keeps original direction geometry and avoids micro-lines by enforcing touch spacing.
        """
        if len(bars) < max(6, int(min_touches) + 2):
            return None
        lookback = max(8, int(getattr(self.config, "reanchor_lookback_bars", 30) or 30))
        recent = bars[-min(lookback, len(bars)) :]
        if direction == TrendlineDirection.BULL:
            first_idx = min(range(len(recent)), key=lambda i: float(recent[i].low))
            first_bar = recent[first_idx]
            first = TrendlineAnchor(ts=first_bar.ts, price=float(first_bar.low), source="reanchor_low")
            second = self._find_higher_low_anchor(recent[first_idx + 1 :], first)
        else:
            first_idx = max(range(len(recent)), key=lambda i: float(recent[i].high))
            first_bar = recent[first_idx]
            first = TrendlineAnchor(ts=first_bar.ts, price=float(first_bar.high), source="reanchor_high")
            second = self._find_lower_high_anchor(recent[first_idx + 1 :], first)
        if not second:
            return None
        seconds = second.ts.timestamp() - first.ts.timestamp()
        if seconds <= 0:
            return None
        slope = (second.price - first.price) / seconds
        if direction == TrendlineDirection.BULL and slope <= 0:
            return None
        if direction == TrendlineDirection.BEAR and slope >= 0:
            return None
        intercept = first.price - (slope * first.ts.timestamp())
        touch_count = 0
        tol = max(1e-6, float(getattr(self.config, "touch_tolerance_pct", 0.0012) or 0.0012))
        for b in recent:
            line = (slope * b.ts.timestamp()) + intercept
            if abs(line) <= 1e-9:
                continue
            dist = min(abs(float(b.low) - line), abs(float(b.high) - line), abs(float(b.close) - line)) / abs(line)
            if dist <= tol:
                touch_count += 1
        if touch_count < int(min_touches):
            return None
        return TrendlineDefinition(
            symbol=symbol,
            direction=direction,
            anchor_one=first,
            anchor_two=second,
            slope_per_second=slope,
            intercept=intercept,
            built_at=datetime.now(timezone.utc),
            metadata={
                "anchor_one_source": first.source,
                "anchor_two_source": second.source,
                "reanchor": True,
                "touch_count": touch_count,
                "anchor_spacing_min": round(seconds / 60.0, 2),
                "line_quality": "ok",
            },
        )

    @staticmethod
    def _fallback_bar_anchor(bar: OHLCVBar, source: str) -> TrendlineAnchor:
        price = bar.low if "low" in source else bar.high
        return TrendlineAnchor(ts=bar.ts, price=price, source=source)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _compute_orb_positional_telemetry(
        last_close: float,
        nh: float,
        nl: float,
        rng: float,
    ) -> Dict[str, Any]:
        """ORB-relative position for logs and geometry-aware narrative guards."""
        pct_low = max(0.0, min(1.0, (last_close - nl) / max(rng, 1e-12)))
        pct_from_high = max(0.0, min(1.0, (nh - last_close) / max(rng, 1e-12)))
        if pct_low <= _ORB_CLASSIFIER_EXHAUST_LOW_PCT:
            pos_bias = "extreme_near_orb_low"
        elif pct_low <= _ORB_CLASSIFIER_NEAR_LOW_PCT:
            pos_bias = "near_orb_low"
        elif pct_low >= _ORB_CLASSIFIER_EXHAUST_HIGH_PCT:
            pos_bias = "extreme_near_orb_high"
        elif pct_low >= _ORB_CLASSIFIER_NEAR_HIGH_PCT:
            pos_bias = "near_orb_high"
        else:
            pos_bias = "mid_orb"
        if pos_bias in ("extreme_near_orb_low", "near_orb_low"):
            geom_bias = "call_breakout_geometry_preferred"
        elif pos_bias in ("extreme_near_orb_high", "near_orb_high"):
            geom_bias = "put_breakdown_geometry_preferred"
        else:
            geom_bias = "neutral"
        return {
            "price_pct_from_orb_low": round(pct_low, 6),
            "price_pct_from_orb_high": round(pct_from_high, 6),
            "expansion_room_to_orb_low_pct": round(pct_low, 6),
            "expansion_room_to_orb_high_pct": round(pct_from_high, 6),
            "orb_position_bias": pos_bias,
            "geometry_bias": geom_bias,
        }

    @staticmethod
    def _put_failure_continuation_strength(
        *,
        lower_highs: int,
        last_close: float,
        mid: float,
        rng: float,
        max_high: float,
        upside_pressure: bool,
    ) -> float:
        """0..1 proxy for whether failed_upside→PUT is justified (pre-selector, window-only)."""
        s = 0.0
        if lower_highs >= 2:
            s += 0.35
        if last_close < mid - 0.04 * rng:
            s += 0.35
        if (max_high - last_close) / max(rng, 1e-12) >= 0.28:
            s += 0.30
        if upside_pressure:
            s += 0.10
        return max(0.0, min(1.0, s))

    @staticmethod
    def _call_failure_continuation_strength(
        *,
        higher_lows: int,
        last_close: float,
        mid: float,
        rng: float,
        min_low: float,
        downside_pressure: bool,
    ) -> float:
        """0..1 proxy for whether failed_downside→CALL is justified (pre-selector, window-only)."""
        s = 0.0
        if higher_lows >= 2:
            s += 0.35
        if last_close > mid + 0.04 * rng:
            s += 0.35
        if (last_close - min_low) / max(rng, 1e-12) >= 0.28:
            s += 0.30
        if downside_pressure:
            s += 0.10
        return max(0.0, min(1.0, s))

    @staticmethod
    def _suppress_failed_upside_near_orb_low(
        pos: Dict[str, Any],
        *,
        lower_highs: int,
        last_close: float,
        mid: float,
        rng: float,
        max_high: float,
        upside_pressure: bool,
    ) -> tuple[bool, str]:
        pct_low = float(pos.get("price_pct_from_orb_low") or 0.0)
        if pct_low > _ORB_CLASSIFIER_NEAR_LOW_PCT:
            return False, ""
        strength = TrendlineBuilder._put_failure_continuation_strength(
            lower_highs=lower_highs,
            last_close=last_close,
            mid=mid,
            rng=rng,
            max_high=max_high,
            upside_pressure=upside_pressure,
        )
        min_st = (
            _ORB_PUT_FAILURE_MIN_STRENGTH_EXHAUST_LOW
            if pct_low <= _ORB_CLASSIFIER_EXHAUST_LOW_PCT
            else _ORB_PUT_FAILURE_MIN_STRENGTH_NEAR_LOW
        )
        if strength < min_st:
            return True, (
                f"near_orb_low_failed_upside_suppressed|put_continuation_strength={strength:.3f}|"
                f"min_required={min_st:.2f}|orb_position_bias={pos.get('orb_position_bias')}"
            )
        return False, ""

    @staticmethod
    def _suppress_failed_downside_near_orb_high(
        pos: Dict[str, Any],
        *,
        higher_lows: int,
        last_close: float,
        mid: float,
        rng: float,
        min_low: float,
        downside_pressure: bool,
    ) -> tuple[bool, str]:
        pct_low = float(pos.get("price_pct_from_orb_low") or 0.0)
        if pct_low < _ORB_CLASSIFIER_NEAR_HIGH_PCT:
            return False, ""
        strength = TrendlineBuilder._call_failure_continuation_strength(
            higher_lows=higher_lows,
            last_close=last_close,
            mid=mid,
            rng=rng,
            min_low=min_low,
            downside_pressure=downside_pressure,
        )
        min_st = (
            _ORB_CALL_FAILURE_MIN_STRENGTH_EXHAUST_HIGH
            if pct_low >= _ORB_CLASSIFIER_EXHAUST_HIGH_PCT
            else _ORB_CALL_FAILURE_MIN_STRENGTH_NEAR_HIGH
        )
        if strength < min_st:
            return True, (
                f"near_orb_high_failed_downside_suppressed|call_continuation_strength={strength:.3f}|"
                f"min_required={min_st:.2f}|orb_position_bias={pos.get('orb_position_bias')}"
            )
        return False, ""

    def _log_orb_classifier_telemetry(
        self,
        symbol: str,
        *,
        failure_type: str,
        setup_side: str,
        pos: Dict[str, Any],
        override_reason: str,
        put_str: Optional[float] = None,
        call_str: Optional[float] = None,
    ) -> None:
        ps = "" if put_str is None else f"{put_str:.3f}"
        cs = "" if call_str is None else f"{call_str:.3f}"
        log.info(
            "TRENDLINE_ORB_CLASSIFIER_TELEMETRY | symbol=%s | price_pct_from_orb_low=%.4f | "
            "price_pct_from_orb_high=%.4f | orb_position_bias=%s | geometry_bias=%s | "
            "failure_type=%s | setup_side=%s | classifier_override_reason=%s | "
            "put_continuation_strength=%s | call_continuation_strength=%s",
            symbol,
            float(pos.get("price_pct_from_orb_low") or 0.0),
            float(pos.get("price_pct_from_orb_high") or 0.0),
            pos.get("orb_position_bias"),
            pos.get("geometry_bias"),
            failure_type,
            setup_side,
            override_reason or "none",
            ps or "na",
            cs or "na",
        )

    def classify_orb_test_failure(
        self,
        symbol: str,
        bars: List[OHLCVBar],
        orb_context: Dict[str, Any],
    ) -> OrbTestFailureClassification:
        """
        Classify ORB-window → 7:30 PT structure for CALL (failed downside / descending resistance)
        vs PUT (failed upside / ascending support). Deterministic heuristics; returns setup_side none
        when no clean test+failure pair is detected.
        """
        empty = OrbTestFailureClassification(
            setup_side="none",
            line_direction=None,
            test_side="none",
            failure_type="unclear",
            confidence=0.0,
            score=0.0,
            diagnostics={"reason": "no_bars"},
            selected_line_reason="unclear_no_bars",
        )
        if not bars:
            log.info(
                "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=none | test_side=none | failure_type=unclear | confidence=0.000 | score=0.000 | reason=no_bars",
                symbol,
            )
            return empty

        nh = self._safe_float(orb_context.get("orb_high"))
        nl = self._safe_float(orb_context.get("orb_low"))
        if nh is None or nl is None or nh <= nl:
            log.info(
                "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=none | test_side=none | failure_type=unclear | confidence=0.000 | score=0.000 | reason=missing_orb_bounds",
                symbol,
            )
            return OrbTestFailureClassification(
                setup_side="none",
                line_direction=None,
                test_side="none",
                failure_type="unclear",
                confidence=0.0,
                score=0.0,
                diagnostics={"orb_high": nh, "orb_low": nl},
                selected_line_reason="unclear_missing_orb_bounds",
            )

        window = self._post_orb_window(bars, orb_context)
        if len(window) < 3:
            log.info(
                "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=none | test_side=none | failure_type=unclear | confidence=0.000 | score=0.000 | reason=insufficient_post_orb_window_bars | n=%d",
                symbol,
                len(window),
            )
            return OrbTestFailureClassification(
                setup_side="none",
                line_direction=None,
                test_side="none",
                failure_type="unclear",
                confidence=0.0,
                score=0.0,
                diagnostics={"window_bars": len(window)},
                selected_line_reason="unclear_insufficient_window",
            )

        rng = float(nh - nl)
        mid = float((nh + nl) / 2.0)
        rel_range = rng / max(mid, 1e-9)
        highs = [float(b.high) for b in window]
        lows = [float(b.low) for b in window]
        closes = [float(b.close) for b in window]
        min_low = min(lows)
        max_high = max(highs)
        last_close = closes[-1]
        pos = self._compute_orb_positional_telemetry(last_close, float(nh), float(nl), rng)

        compression = rel_range < 0.0015 or rng < 1e-6
        if compression:
            log.info(
                "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=none | test_side=none | failure_type=compression | confidence=0.350 | score=0.350 | rel_range=%.6f",
                symbol,
                rel_range,
            )
            self._log_orb_classifier_telemetry(
                symbol,
                failure_type="compression",
                setup_side="none",
                pos=pos,
                override_reason="",
                put_str=None,
                call_str=None,
            )
            return OrbTestFailureClassification(
                setup_side="none",
                line_direction=None,
                test_side="none",
                failure_type="compression",
                confidence=0.35,
                score=0.35,
                diagnostics={**pos, "rel_range": rel_range, "range": rng},
                selected_line_reason="compression_skip",
            )

        # Downside probe: trade visited lower half / pushed toward ORB low
        downside_probe = min_low <= nl + 0.42 * rng or max_high < nh - 0.08 * rng
        lower_highs = self._count_lower_highs(highs)
        downside_pressure = lower_highs >= 2 or (max_high - min(lows)) / max(rng, 1e-9) > 0.55

        # Upside probe: visited upper half / pushed toward ORB high
        upside_probe = max_high >= nh - 0.42 * rng or min_low > nl + 0.08 * rng
        higher_lows = self._count_higher_lows(lows)
        upside_pressure = higher_lows >= 2 or (max_high - min_low) / max(rng, 1e-9) > 0.55

        put_continuation_strength = self._put_failure_continuation_strength(
            lower_highs=lower_highs,
            last_close=last_close,
            mid=mid,
            rng=rng,
            max_high=max_high,
            upside_pressure=upside_pressure,
        )
        call_continuation_strength = self._call_failure_continuation_strength(
            higher_lows=higher_lows,
            last_close=last_close,
            mid=mid,
            rng=rng,
            min_low=min_low,
            downside_pressure=downside_pressure,
        )

        # Failure: downside — lows stop pressing; reclaim toward mid/high
        tail_lows = lows[-4:] if len(lows) >= 4 else lows
        failed_downside = (
            downside_probe
            and min(tail_lows) >= min_low + 0.02 * rng
            and last_close > mid - 0.02 * rng
            and (closes[-1] >= closes[-2] if len(closes) >= 2 else True)
        )

        # Failure: upside — highs stall under NH; rollover
        tail_highs = highs[-4:] if len(highs) >= 4 else highs
        failed_upside = (
            upside_probe
            and max(tail_highs) <= max_high - 0.02 * rng
            and last_close < mid + 0.02 * rng
            and (closes[-1] <= closes[-2] if len(closes) >= 2 else True)
        )

        # Trend continuation (no failure): one-sided drift beyond ORB with no opposing test
        strong_up = last_close > nh and higher_lows >= 2 and not downside_probe
        strong_down = last_close < nl and lower_highs >= 2 and not upside_probe
        if (strong_up or strong_down) and not (failed_downside or failed_upside):
            ft = "trend_continuation"
            log.info(
                "TRENDLINE_TREND_CONTINUATION_SKIP | symbol=%s | direction_hint=%s | last_close=%.4f | nh=%.4f | nl=%.4f",
                symbol,
                "up" if strong_up else "down",
                last_close,
                nh,
                nl,
            )
            log.info(
                "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=none | test_side=%s | failure_type=%s | confidence=0.620 | score=0.620",
                symbol,
                "upside_test" if strong_up else "downside_test",
                ft,
            )
            self._log_orb_classifier_telemetry(
                symbol,
                failure_type=ft,
                setup_side="none",
                pos=pos,
                override_reason="",
                put_str=put_continuation_strength,
                call_str=call_continuation_strength,
            )
            return OrbTestFailureClassification(
                setup_side="none",
                line_direction=None,
                test_side="upside_test" if strong_up else "downside_test",
                failure_type=ft,
                confidence=0.62,
                score=0.62,
                diagnostics={
                    **pos,
                    "put_continuation_strength": put_continuation_strength,
                    "call_continuation_strength": call_continuation_strength,
                    "strong_up": strong_up,
                    "strong_down": strong_down,
                    "higher_lows": higher_lows,
                    "lower_highs": lower_highs,
                },
                selected_line_reason="trend_continuation_no_trade_line",
            )

        narrative_call = failed_downside and downside_probe
        suppress_call, sup_call_reason = (
            self._suppress_failed_downside_near_orb_high(
                pos,
                higher_lows=higher_lows,
                last_close=last_close,
                mid=mid,
                rng=rng,
                min_low=min_low,
                downside_pressure=downside_pressure,
            )
            if narrative_call
            else (False, "")
        )
        narrative_put = failed_upside and upside_probe
        suppress_put, sup_put_reason = (
            self._suppress_failed_upside_near_orb_low(
                pos,
                lower_highs=lower_highs,
                last_close=last_close,
                mid=mid,
                rng=rng,
                max_high=max_high,
                upside_pressure=upside_pressure,
            )
            if narrative_put
            else (False, "")
        )

        if narrative_call and suppress_call:
            log.info(
                "TRENDLINE_ORB_NARRATIVE_SUPPRESSED | symbol=%s | pattern=failed_downside | detail=%s",
                symbol,
                sup_call_reason,
            )
        if narrative_put and suppress_put:
            log.info(
                "TRENDLINE_ORB_NARRATIVE_SUPPRESSED | symbol=%s | pattern=failed_upside | detail=%s",
                symbol,
                sup_put_reason,
            )

        if narrative_call and not suppress_call:
            conf = 0.78 if downside_pressure else 0.58
            log.info(
                "TRENDLINE_TEST_FAILURE_DETECTED | symbol=%s | pattern=failed_downside | call_setup=true | confidence=%.3f",
                symbol,
                conf,
            )
            log.info(
                "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=call | test_side=downside_test | failure_type=failed_downside | confidence=%.3f | score=%.3f",
                symbol,
                conf,
                conf,
            )
            self._log_orb_classifier_telemetry(
                symbol,
                failure_type="failed_downside",
                setup_side="call",
                pos=pos,
                override_reason="",
                put_str=put_continuation_strength,
                call_str=call_continuation_strength,
            )
            a1: Dict[str, Any] = {
                "role": "orb_high",
                "price": nh,
                "ts": orb_context.get("orb_high_ts"),
                "source": "orb_high_ts" if orb_context.get("orb_high_ts") else "orb_context",
            }
            a2: Dict[str, Any] = {"role": "lower_high_anchor", "note": "built_by_trendline_builder_after_classification"}
            lows_w = [float(b.low) for b in window]
            min_lw = min(lows_w)
            fp_down = max(i for i, v in enumerate(lows_w) if v == min_lw)
            return OrbTestFailureClassification(
                setup_side="call",
                line_direction=TrendlineDirection.BEAR,
                test_side="downside_test",
                failure_type="failed_downside",
                confidence=conf,
                score=conf,
                anchor_one_detail=a1,
                anchor_two_detail=a2,
                diagnostics={
                    **pos,
                    "put_continuation_strength": put_continuation_strength,
                    "call_continuation_strength": call_continuation_strength,
                    "downside_probe": downside_probe,
                    "downside_pressure": downside_pressure,
                    "lower_highs": lower_highs,
                    "window_bars": len(window),
                    "failure_point_index_in_post_orb": fp_down,
                    "upside_probe": upside_probe,
                    "failed_upside_raw": failed_upside,
                },
                selected_line_reason="failed_downside_descending_resistance",
                failure_point_index_in_post_orb=fp_down,
            )

        if narrative_put and not suppress_put:
            conf = 0.78 if upside_pressure else 0.58
            log.info(
                "TRENDLINE_TEST_FAILURE_DETECTED | symbol=%s | pattern=failed_upside | put_setup=true | confidence=%.3f",
                symbol,
                conf,
            )
            log.info(
                "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=put | test_side=upside_test | failure_type=failed_upside | confidence=%.3f | score=%.3f",
                symbol,
                conf,
                conf,
            )
            self._log_orb_classifier_telemetry(
                symbol,
                failure_type="failed_upside",
                setup_side="put",
                pos=pos,
                override_reason="",
                put_str=put_continuation_strength,
                call_str=call_continuation_strength,
            )
            a1u: Dict[str, Any] = {
                "role": "orb_low",
                "price": nl,
                "ts": orb_context.get("orb_low_ts"),
                "source": "orb_low_ts" if orb_context.get("orb_low_ts") else "orb_context",
            }
            a2u: Dict[str, Any] = {"role": "higher_low_anchor", "note": "built_by_trendline_builder_after_classification"}
            highs_w = [float(b.high) for b in window]
            max_hw = max(highs_w)
            fp_up = max(i for i, v in enumerate(highs_w) if v == max_hw)
            return OrbTestFailureClassification(
                setup_side="put",
                line_direction=TrendlineDirection.BULL,
                test_side="upside_test",
                failure_type="failed_upside",
                confidence=conf,
                score=conf,
                anchor_one_detail=a1u,
                anchor_two_detail=a2u,
                diagnostics={
                    **pos,
                    "put_continuation_strength": put_continuation_strength,
                    "call_continuation_strength": call_continuation_strength,
                    "upside_probe": upside_probe,
                    "upside_pressure": upside_pressure,
                    "higher_lows": higher_lows,
                    "window_bars": len(window),
                    "failure_point_index_in_post_orb": fp_up,
                    "downside_probe": downside_probe,
                    "failed_downside_raw": failed_downside,
                },
                selected_line_reason="failed_upside_ascending_support",
                failure_point_index_in_post_orb=fp_up,
            )

        override_reasons: List[str] = []
        if narrative_call and suppress_call:
            override_reasons.append(f"suppressed_failed_downside:{sup_call_reason}")
        if narrative_put and suppress_put:
            override_reasons.append(f"suppressed_failed_upside:{sup_put_reason}")
        classifier_override_reason = "|".join(override_reasons)

        log.info(
            "TRENDLINE_STRUCTURE_CLASSIFIED | symbol=%s | setup_side=none | test_side=%s | failure_type=unclear | confidence=0.250 | score=0.250 | downside_probe=%s | upside_probe=%s",
            symbol,
            "downside_test" if downside_probe else ("upside_test" if upside_probe else "none"),
            str(downside_probe).lower(),
            str(upside_probe).lower(),
        )
        self._log_orb_classifier_telemetry(
            symbol,
            failure_type="unclear",
            setup_side="none",
            pos=pos,
            override_reason=classifier_override_reason,
            put_str=put_continuation_strength,
            call_str=call_continuation_strength,
        )
        return OrbTestFailureClassification(
            setup_side="none",
            line_direction=None,
            test_side="downside_test" if downside_probe else ("upside_test" if upside_probe else "none"),
            failure_type="unclear",
            confidence=0.25,
            score=0.25,
            diagnostics={
                **pos,
                "put_continuation_strength": put_continuation_strength,
                "call_continuation_strength": call_continuation_strength,
                "downside_probe": downside_probe,
                "upside_probe": upside_probe,
                "failed_downside": failed_downside,
                "failed_upside": failed_upside,
                "narrative_call_eligible": narrative_call,
                "narrative_put_eligible": narrative_put,
                "suppressed_failed_downside": narrative_call and suppress_call,
                "suppressed_failed_upside": narrative_put and suppress_put,
                "window_bars": len(window),
            },
            selected_line_reason="no_clean_test_failure_pair",
            classifier_override_reason=classifier_override_reason,
        )

    @staticmethod
    def _normalize_ts(ts: datetime) -> datetime:
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    @staticmethod
    def _count_lower_highs(highs: List[float]) -> int:
        if len(highs) < 3:
            return 0
        cnt = 0
        for i in range(1, len(highs)):
            if highs[i] < highs[i - 1]:
                cnt += 1
        return cnt

    @staticmethod
    def _count_higher_lows(lows: List[float]) -> int:
        if len(lows) < 3:
            return 0
        cnt = 0
        for i in range(1, len(lows)):
            if lows[i] > lows[i - 1]:
                cnt += 1
        return cnt

