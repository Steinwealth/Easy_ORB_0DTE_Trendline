#!/usr/bin/env python3
"""
Pre-7:30 PT structure selection: when the 7:30 close is outside the ORB band, the
primary line is **cutoff last close ↔ farthest session extreme** (exhaustion /
reversal geometry). Otherwise, ascending support vs descending resistance from
ORB-anchored builder lines plus classifier / MSE. Does not use SO list side or fixed
long bias.

**Lane separation:** ORB SO / ORB 0DTE ranking in this repo targets **continuation quality**
into the execution window; Trendline selection here targets **exhaustion / compression
into reversal breaks** — do not copy SO rank weights into Trendline tuning.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .trendline_builder import OrbTestFailureClassification, TrendlineBuilder
from .trendline_models import (
    OHLCVBar,
    TrendlineConfig,
    TrendlineDefinition,
    TrendlineDirection,
    TrendlineReasonCode,
    TrendlineSetupType,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructureSetupResult:
    """Outcome of structure-first trendline selection for one symbol."""

    direction: TrendlineDirection
    setup_type: TrendlineSetupType
    trendline: TrendlineDefinition
    trigger_direction: str
    expected_option_side: str
    fit_mse: float
    alternate_fit_mse: Optional[float]
    structure_bar_count: int
    selection_reason: str
    anchor_one_source: str
    anchor_two_source: str
    # Observability only (alerts / logs). Does not participate in selection math.
    structure_display_label: str = ""
    explainability: Dict[str, Any] = field(default_factory=dict)


def _anchor_sources_from_tl(tl: TrendlineDefinition) -> tuple[str, str]:
    meta = tl.metadata or {}
    a1 = meta.get("anchor_one_source")
    a2 = meta.get("anchor_two_source")
    return (
        str(a1) if a1 is not None else "unknown",
        str(a2) if a2 is not None else "unknown",
    )


def _session_cutoff_pt(reference_ts: datetime, tz_pt: ZoneInfo) -> datetime:
    local = reference_ts.astimezone(tz_pt)
    cutoff_local = datetime.combine(local.date(), time(7, 30), tz_pt)
    return cutoff_local.astimezone(reference_ts.tzinfo or tz_pt)


def _structure_window_bars(bars: List[OHLCVBar], tz_pt: ZoneInfo) -> List[OHLCVBar]:
    """Bars whose timestamp falls in 06:30–07:30 America/Los_Angeles same calendar day as first bar."""
    if not bars:
        return []
    day = bars[0].ts.astimezone(tz_pt).date()
    start_t = time(6, 30)
    end_t = time(7, 30)
    out: List[OHLCVBar] = []
    for b in bars:
        local = b.ts.astimezone(tz_pt)
        if local.date() != day:
            continue
        tt = local.time()
        if start_t <= tt <= end_t:
            out.append(b)
    return sorted(out, key=lambda x: x.ts)


def _pre_cutoff_bars(bars: List[OHLCVBar], cutoff: datetime) -> List[OHLCVBar]:
    return sorted([b for b in bars if b.ts <= cutoff], key=lambda x: x.ts)


def _eval_bars_for_line(
    trendline: TrendlineDefinition,
    structure_bars: List[OHLCVBar],
    fallback_bars: List[OHLCVBar],
    cutoff: datetime,
) -> List[OHLCVBar]:
    """Bars used to score fit: prefer 6:30–7:30 window, intersect [anchor_one, cutoff]."""
    pool = structure_bars if len(structure_bars) >= 2 else fallback_bars
    t0 = trendline.anchor_one.ts
    return [b for b in pool if b.ts >= t0 and b.ts <= cutoff]


def _fit_ascending_support_mse(trendline: TrendlineDefinition, eval_bars: List[OHLCVBar]) -> float:
    """Mean squared violation: line above bar.low (support should sit under lows)."""
    if not eval_bars:
        return float("inf")
    total = 0.0
    for b in eval_bars:
        lx = trendline.value_at(b.ts)
        d = max(0.0, lx - b.low)
        total += d * d
    return total / len(eval_bars)


def _fit_descending_resistance_mse(trendline: TrendlineDefinition, eval_bars: List[OHLCVBar]) -> float:
    """Mean squared violation: bar.high above line (resistance should cap highs)."""
    if not eval_bars:
        return float("inf")
    total = 0.0
    for b in eval_bars:
        lx = trendline.value_at(b.ts)
        d = max(0.0, b.high - lx)
        total += d * d
    return total / len(eval_bars)


def _orb_price_geometry_preference(
    price: float, orb_high: float, orb_low: float
) -> Optional[tuple[TrendlineDirection, str]]:
    """
    ORB-relative geometry hint (not a hard trade signal).

    Near ORB low → descending resistance / CALL breakout geometry is the natural read;
    near ORB high → ascending support / PUT breakdown geometry.
    """
    if orb_high <= orb_low:
        return None
    rng = float(orb_high) - float(orb_low)
    if rng <= 0:
        return None
    pct_low = (float(price) - float(orb_low)) / rng
    if pct_low <= 0.35:
        return (TrendlineDirection.BEAR, "near_orb_low_descending_resistance_bias")
    if pct_low >= 0.65:
        return (TrendlineDirection.BULL, "near_orb_high_ascending_support_bias")
    return None


def _relative_mse_tie(m_a: float, m_b: float, rel_tol: float = 0.15) -> bool:
    """True if the two MSE values are relatively ambiguous (within rel_tol of the larger)."""
    if m_a == float("inf") and m_b == float("inf"):
        return True
    lo = min(m_a, m_b)
    hi = max(m_a, m_b)
    if hi <= 0 or hi == float("inf"):
        return False
    return (hi - lo) / hi < rel_tol


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _anchor_snapshot_pt(tl: TrendlineDefinition, tz_pt: ZoneInfo) -> Dict[str, Any]:
    a1, a2 = tl.anchor_one, tl.anchor_two
    return {
        "anchor_one_ts_pt": a1.ts.astimezone(tz_pt).strftime("%H:%M"),
        "anchor_one_price": float(a1.price),
        "anchor_one_source": str(a1.source or ""),
        "anchor_two_ts_pt": a2.ts.astimezone(tz_pt).strftime("%H:%M"),
        "anchor_two_price": float(a2.price),
        "anchor_two_source": str(a2.source or ""),
        "slope_per_second": float(tl.slope_per_second),
        "slope_sign": "up" if float(tl.slope_per_second) > 0 else ("down" if float(tl.slope_per_second) < 0 else "flat"),
        "geometry_role": "support" if tl.direction == TrendlineDirection.BULL else "resistance",
    }


def _line_value_orb_offsets(
    tl: Optional[TrendlineDefinition], cutoff: datetime, orb_high: Optional[float], orb_low: Optional[float]
) -> Dict[str, Any]:
    if tl is None:
        return {}
    try:
        lv = float(tl.value_at(cutoff))
    except Exception:
        return {"line_price_at_cutoff": None}
    out: Dict[str, Any] = {"line_price_at_cutoff": lv}
    if orb_high is not None:
        out["line_minus_orb_high"] = lv - float(orb_high)
        out["abs_line_to_orb_high"] = abs(lv - float(orb_high))
    if orb_low is not None:
        out["line_minus_orb_low"] = lv - float(orb_low)
        out["abs_line_to_orb_low"] = abs(lv - float(orb_low))
    return out


def _derive_structure_display_label(
    chosen: StructureSetupResult,
    cls: OrbTestFailureClassification,
    *,
    bear_ok: bool,
    bull_ok: bool,
    mse_bull: float,
    mse_bear: float,
    price_pct_from_orb_low: Optional[float],
    compression_score: Optional[float],
) -> str:
    """
    Human-facing regime label for alerts/logs only.

    Priority encodes common visual misreads (e.g. resistance compressing price while the
    traded edge remains support breakdown / ORB-failure semantics).
    """
    sr = str(chosen.selection_reason or "")
    bull_chosen = chosen.direction == TrendlineDirection.BULL
    bear_chosen = chosen.direction == TrendlineDirection.BEAR
    mb = mse_bull if math.isfinite(mse_bull) else None
    mr = mse_bear if math.isfinite(mse_bear) else None
    comp = compression_score if compression_score is not None and math.isfinite(compression_score) else None
    p_low = price_pct_from_orb_low

    if bull_chosen and bear_ok and mb is not None and mr is not None and mr < mb * 0.98:
        if sr.startswith("structure_failed") or cls.setup_side == "put":
            return "descending_resistance_pressure_classifier_put_path"

    if bull_chosen and bear_ok and mb is not None and mr is not None and mr <= mb * 1.30:
        return "ascending_support_under_resistance"

    if bull_chosen and p_low is not None and p_low <= 0.15:
        return "support_failure_near_orb_low"

    if bull_ok and bear_ok and comp is not None and comp >= 0.65 and p_low is not None and p_low <= 0.30:
        return "orb_low_compression_breakdown"

    if bull_ok and bear_ok and comp is not None and comp >= 0.55:
        return "mixed_geometry_compression_regime"

    if bear_chosen and bull_ok and mb is not None and mr is not None and mb < mr * 0.98:
        if sr.startswith("structure_failed") or cls.setup_side == "call":
            return "ascending_support_pressure_classifier_call_path"

    if bear_chosen and bull_ok and mb is not None and mr is not None and mb <= mr * 1.30:
        return "descending_resistance_under_support"

    return f"{chosen.setup_type.value}__{sr}"


def _build_structure_explainability(
    symbol: str,
    chosen: StructureSetupResult,
    cls: OrbTestFailureClassification,
    orb_context: Dict[str, Any],
    cutoff: datetime,
    pre_cutoff: List[OHLCVBar],
    tl_bull: Optional[TrendlineDefinition],
    tl_bear: Optional[TrendlineDefinition],
    bull_ok: bool,
    bear_ok: bool,
    mse_bull: float,
    mse_bear: float,
    tz_pt: ZoneInfo,
) -> Tuple[str, Dict[str, Any]]:
    """Telemetry + display label. Purely observational."""
    orb_high = _safe_float(orb_context.get("orb_high"))
    orb_low = _safe_float(orb_context.get("orb_low"))
    orb_mid = None
    rng = None
    if orb_high is not None and orb_low is not None and orb_high > orb_low:
        rng = float(orb_high) - float(orb_low)
        orb_mid = float(orb_low) + 0.5 * rng

    last_close: Optional[float] = None
    if pre_cutoff:
        try:
            last_close = float(pre_cutoff[-1].close)
        except (TypeError, ValueError):
            last_close = None

    price_pct_from_orb_low: Optional[float] = None
    if last_close is not None and orb_high is not None and orb_low is not None and rng and rng > 0:
        price_pct_from_orb_low = max(0.0, min(1.0, (last_close - float(orb_low)) / rng))

    mb = mse_bull if math.isfinite(mse_bull) else None
    mr = mse_bear if math.isfinite(mse_bear) else None
    compression_score: Optional[float] = None
    if mb is not None and mr is not None and mb > 0 and mr > 0:
        compression_score = min(mb, mr) / max(mb, mr)

    selected_tl = chosen.trendline
    alt_tl = tl_bear if chosen.direction == TrendlineDirection.BULL else tl_bull

    rejected: List[Dict[str, Any]] = []
    if chosen.direction == TrendlineDirection.BULL and bear_ok and tl_bear is not None:
        rel = None
        if mb is not None and mr is not None and mb > 0:
            rel = float(mr) / float(mb)
        rejected.append(
            {
                "geometry": "bear",
                "role": "resistance",
                "mse": mr,
                "mse_ratio_resistance_to_support": rel,
                "reason_not_selected": "selector_chose_bull_geometry",
                "why_selected_summary": f"classifier_or_mse_path={chosen.selection_reason}",
            }
        )
    elif chosen.direction == TrendlineDirection.BEAR and bull_ok and tl_bull is not None:
        rel = None
        if mb is not None and mr is not None and mr > 0:
            rel = float(mb) / float(mr)
        rejected.append(
            {
                "geometry": "bull",
                "role": "support",
                "mse": mb,
                "mse_ratio_support_to_resistance": rel,
                "reason_not_selected": "selector_chose_bear_geometry",
                "why_selected_summary": f"classifier_or_mse_path={chosen.selection_reason}",
            }
        )

    narrative_parts = [
        f"symbol={symbol}",
        f"selected={chosen.direction.value}",
        f"selection_reason={chosen.selection_reason}",
        f"classifier_failure_type={cls.failure_type or 'none'}",
        f"classifier_setup_side={cls.setup_side or 'none'}",
    ]
    if mb is not None and mr is not None:
        narrative_parts.append(f"mse_support_vs_resistance={mb:.6g}/{mr:.6g}")
    if compression_score is not None:
        narrative_parts.append(f"compression_score={compression_score:.3f}")
    if price_pct_from_orb_low is not None:
        narrative_parts.append(f"price_pct_from_orb_low={price_pct_from_orb_low:.3f}")
    narrative = " | ".join(narrative_parts)

    explain: Dict[str, Any] = {
        "symbol": symbol,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_mid": orb_mid,
        "orb_range": rng,
        "price_last_close_pre_cutoff": last_close,
        "price_pct_from_orb_low": price_pct_from_orb_low,
        "orb_classifier": {
            "failure_type": cls.failure_type,
            "setup_side": cls.setup_side,
            "test_side": cls.test_side,
            "selected_line_reason": cls.selected_line_reason,
            "confidence": float(cls.confidence),
            "score": float(cls.score),
        },
        "selected": {
            "direction": chosen.direction.value,
            "setup_type": chosen.setup_type.value,
            "trigger_direction": chosen.trigger_direction,
            "expected_option_side": chosen.expected_option_side,
            "selection_reason": chosen.selection_reason,
            "fit_mse": float(chosen.fit_mse) if math.isfinite(chosen.fit_mse) else None,
            "alternate_fit_mse": float(chosen.alternate_fit_mse)
            if chosen.alternate_fit_mse is not None and math.isfinite(chosen.alternate_fit_mse)
            else None,
            "structure_bar_count": int(chosen.structure_bar_count),
            "anchor_one_source": chosen.anchor_one_source,
            "anchor_two_source": chosen.anchor_two_source,
        },
        "selected_line": {
            **_anchor_snapshot_pt(selected_tl, tz_pt),
            **_line_value_orb_offsets(selected_tl, cutoff, orb_high, orb_low),
        },
        "alternate_line": None,
        "mse_support": mb,
        "mse_resistance": mr,
        "compression_score": compression_score,
        "rejected_alternates": rejected,
        "narrative": narrative,
    }

    d_orb = cls.diagnostics if isinstance(cls.diagnostics, dict) else {}
    cob = str(getattr(cls, "classifier_override_reason", "") or "").strip()
    explain["price_pct_from_orb_high"] = d_orb.get("price_pct_from_orb_high")
    if explain.get("price_pct_from_orb_low") is None and d_orb.get("price_pct_from_orb_low") is not None:
        explain["price_pct_from_orb_low"] = d_orb.get("price_pct_from_orb_low")
    explain["orb_position_bias"] = d_orb.get("orb_position_bias")
    explain["geometry_bias"] = d_orb.get("geometry_bias")
    explain["classifier_override_reason"] = cob or d_orb.get("classifier_override_reason", "")
    explain["put_continuation_strength"] = d_orb.get("put_continuation_strength")
    explain["call_continuation_strength"] = d_orb.get("call_continuation_strength")

    if alt_tl is not None:
        explain["alternate_line"] = {
            **_anchor_snapshot_pt(alt_tl, tz_pt),
            **_line_value_orb_offsets(alt_tl, cutoff, orb_high, orb_low),
        }

    base_label = _derive_structure_display_label(
        chosen,
        cls,
        bear_ok=bear_ok,
        bull_ok=bull_ok,
        mse_bull=mse_bull,
        mse_bear=mse_bear,
        price_pct_from_orb_low=price_pct_from_orb_low,
        compression_score=compression_score,
    )
    hints: List[str] = []
    if price_pct_from_orb_low is not None and price_pct_from_orb_low <= 0.22:
        hints.append("price_near_orb_low_band")
    if mb is not None and mr is not None and mb > 0 and mr > 0 and max(mb, mr) / min(mb, mr) < 1.45:
        hints.append("dual_geometry_tight_mse")
    if chosen.direction == TrendlineDirection.BULL and bear_ok and mr is not None and mb is not None:
        hints.append(f"alternate_resistance_mse={mr:.6g}_vs_support_mse={mb:.6g}")
    if chosen.direction == TrendlineDirection.BEAR and bull_ok and mr is not None and mb is not None:
        hints.append(f"alternate_support_mse={mb:.6g}_vs_resistance_mse={mr:.6g}")
    label = base_label if not hints else base_label + " | " + " | ".join(hints[:3])
    explain["structure_display_label"] = label
    explain["structure_display_label_base"] = base_label
    explain["structure_label_hints"] = hints
    return label, explain


def _audit_anchor_compact(tl: Optional[TrendlineDefinition], tz_pt: ZoneInfo) -> str:
    if tl is None:
        return "na"
    a1, a2 = tl.anchor_one, tl.anchor_two
    t1 = a1.ts.astimezone(tz_pt).strftime("%H:%MPT")
    t2 = a2.ts.astimezone(tz_pt).strftime("%H:%MPT")
    return (
        f"A1({a1.source})={float(a1.price):.4f}@{t1}|"
        f"A2({a2.source})={float(a2.price):.4f}@{t2}"
    )


def _mse_winner_geometry(mse_bull: float, mse_bear: float) -> str:
    if not math.isfinite(mse_bull) and not math.isfinite(mse_bear):
        return "none"
    if not math.isfinite(mse_bull):
        return "bear"
    if not math.isfinite(mse_bear):
        return "bull"
    if mse_bull < mse_bear:
        return "bull"
    if mse_bear < mse_bull:
        return "bear"
    return "tie"


def _rule_implied_geometry_label(cls: OrbTestFailureClassification) -> str:
    """Classifier-first geometry hint (not necessarily what MSE or final chose)."""
    if cls.setup_side == "put":
        return "bull|classifier_setup_side_put"
    if cls.setup_side == "call":
        return "bear|classifier_setup_side_call"
    if cls.failure_type == "trend_continuation":
        return "skip|trend_continuation"
    d = cls.diagnostics if isinstance(cls.diagnostics, dict) else {}
    gb = str(d.get("geometry_bias") or "")
    if "call_breakout_geometry_preferred" in gb:
        return "bear|diagnostics_geometry_bias"
    if "put_breakdown_geometry_preferred" in gb:
        return "bull|diagnostics_geometry_bias"
    return f"unclear|failure_type={cls.failure_type or 'none'}"


def _rejected_alternate_reason(explain: Dict[str, Any]) -> str:
    rej = explain.get("rejected_alternates")
    if not isinstance(rej, list) or not rej:
        return "none"
    first = rej[0] if isinstance(rej[0], dict) else {}
    parts = [
        str(first.get("reason_not_selected") or ""),
        str(first.get("why_selected_summary") or ""),
    ]
    return "|".join(p for p in parts if p) or "none"


def _candidate_anchor_points_json(
    bars: List[OHLCVBar], tz_pt: ZoneInfo, *, max_len: int = 900
) -> str:
    """Compact post-ORB window bars for audit (time + OHLC in PT)."""
    out: List[Dict[str, Any]] = []
    for b in bars:
        loc = b.ts.astimezone(tz_pt)
        out.append(
            {
                "t": loc.strftime("%H:%M"),
                "h": float(b.high),
                "l": float(b.low),
                "c": float(b.close),
            }
        )
    try:
        txt = json.dumps(out, separators=(",", ":"), default=str)
    except Exception:
        txt = str(out)
    if len(txt) > max_len:
        return txt[: max_len - 3] + "..."
    return txt


def _log_trendline_draw_audit(
    symbol: str,
    *,
    tz_pt: ZoneInfo,
    cutoff: datetime,
    pre_cutoff: List[OHLCVBar],
    orb_context: Dict[str, Any],
    cls: OrbTestFailureClassification,
    chosen: StructureSetupResult,
    tl_bull: Optional[TrendlineDefinition],
    tl_bear: Optional[TrendlineDefinition],
    mse_bull: float,
    mse_bear: float,
    builder: TrendlineBuilder,
    explain: Dict[str, Any],
) -> None:
    """
    Single-line draw pipeline audit (grep: TRENDLINE_DRAW_AUDIT).

    Documents current anchor law (ORB extreme + higher_low / lower_high), MSE winner,
    classifier rule hint, and post-ORB extremes vs intended 7:30-cutoff-centric narratives.
    """
    oh = _safe_float(orb_context.get("orb_high"))
    ol = _safe_float(orb_context.get("orb_low"))
    cutoff_price: Optional[float] = None
    if pre_cutoff:
        try:
            cutoff_price = float(pre_cutoff[-1].close)
        except (TypeError, ValueError):
            cutoff_price = None

    post_orb = builder._post_orb_window(pre_cutoff, orb_context)
    farthest_hi: Optional[float] = None
    farthest_lo: Optional[float] = None
    farthest_hi_ts = ""
    farthest_lo_ts = ""
    if post_orb:
        bh = max(post_orb, key=lambda b: float(b.high))
        bl = min(post_orb, key=lambda b: float(b.low))
        farthest_hi = float(bh.high)
        farthest_lo = float(bl.low)
        farthest_hi_ts = bh.ts.astimezone(tz_pt).strftime("%H:%MPT")
        farthest_lo_ts = bl.ts.astimezone(tz_pt).strftime("%H:%MPT")

    cand_json = _candidate_anchor_points_json(post_orb, tz_pt) if post_orb else "[]"

    stl = chosen.trendline
    try:
        line_at_cut = float(stl.value_at(cutoff))
    except Exception:
        line_at_cut = float("nan")
    slope = float(stl.slope_per_second)
    slope_lbl = "up" if slope > 0 else ("down" if slope < 0 else "flat")

    a1, a2 = stl.anchor_one, stl.anchor_two
    sel_start = f"{a1.source}={float(a1.price):.4f}@{a1.ts.astimezone(tz_pt).strftime('%H:%MPT')}"
    sel_end = f"{a2.source}={float(a2.price):.4f}@{a2.ts.astimezone(tz_pt).strftime('%H:%MPT')}"

    mse_geo = _mse_winner_geometry(mse_bull, mse_bear)
    rule_geo = _rule_implied_geometry_label(cls)

    last_px = cutoff_price
    oh_g = oh
    ol_g = ol
    orb_bias_pref = "none"
    orb_bias_gate: Optional[bool] = None
    if (
        last_px is not None
        and oh_g is not None
        and ol_g is not None
        and oh_g > ol_g
        and str(cls.failure_type or "") == "unclear"
    ):
        pref = _orb_price_geometry_preference(last_px, oh_g, ol_g)
        if pref is not None:
            pref_dir, pref_reason = pref
            orb_bias_pref = f"{pref_dir.value}|{pref_reason}"
            soft_ratio = 1.32
            if pref_dir == TrendlineDirection.BEAR:
                orb_bias_gate = bool(math.isfinite(mse_bear) and math.isfinite(mse_bull) and mse_bear <= mse_bull * soft_ratio)
            elif pref_dir == TrendlineDirection.BULL:
                orb_bias_gate = bool(math.isfinite(mse_bear) and math.isfinite(mse_bull) and mse_bull <= mse_bear * soft_ratio)

    log.warning(
        "TRENDLINE_DRAW_AUDIT | symbol=%s | ts_730_cutoff=%s | cutoff_price=%s | orb_high=%s | orb_low=%s | "
        "candidate_anchor_points=%s | bull_support_anchor_1_2=%s | bear_resistance_anchor_1_2=%s | "
        "farthest_high_after_orb=%s@%s | farthest_low_after_orb=%s@%s | "
        "selected_anchor_start=%s | selected_anchor_end=%s | selected_geometry=%s | selected_slope=%s | "
        "selected_slope_per_s=%.10f | selected_line_at_cutoff=%.6f | support_mse=%s | resistance_mse=%s | "
        "mse_selected_geometry=%s | rule_selected_geometry=%s | final_selected_geometry=%s | "
        "selection_reason=%s | rejected_alternate_reason=%s | orb_bias_pref=%s | orb_bias_mse_gate_pass=%s | "
        "classifier_failure_type=%s | classifier_setup_side=%s",
        symbol,
        cutoff.isoformat(),
        f"{cutoff_price:.6f}" if cutoff_price is not None else "none",
        f"{oh:.6f}" if oh is not None else "none",
        f"{ol:.6f}" if ol is not None else "none",
        cand_json,
        _audit_anchor_compact(tl_bull, tz_pt),
        _audit_anchor_compact(tl_bear, tz_pt),
        f"{farthest_hi:.6f}" if farthest_hi is not None else "none",
        farthest_hi_ts or "none",
        f"{farthest_lo:.6f}" if farthest_lo is not None else "none",
        farthest_lo_ts or "none",
        sel_start,
        sel_end,
        chosen.direction.value,
        slope_lbl,
        slope,
        line_at_cut,
        f"{mse_bull:.10g}" if math.isfinite(mse_bull) else "inf",
        f"{mse_bear:.10g}" if math.isfinite(mse_bear) else "inf",
        mse_geo,
        rule_geo,
        chosen.direction.value,
        chosen.selection_reason,
        _rejected_alternate_reason(explain),
        orb_bias_pref,
        "none" if orb_bias_gate is None else str(orb_bias_gate).lower(),
        cls.failure_type,
        cls.setup_side,
    )


def _log_trendline_structure_comparison(
    symbol: str,
    chosen: StructureSetupResult,
    cls: OrbTestFailureClassification,
    explain: Dict[str, Any],
) -> None:
    sel = explain.get("selected_line") if isinstance(explain.get("selected_line"), dict) else {}
    rejected = explain.get("rejected_alternates") if isinstance(explain.get("rejected_alternates"), list) else []
    try:
        rejected_txt = json.dumps(rejected, separators=(",", ":"), default=str)
    except Exception:
        rejected_txt = str(rejected)
    if len(rejected_txt) > 520:
        rejected_txt = rejected_txt[:517] + "..."
    log.warning(
        "TRENDLINE_STRUCTURE_COMPARISON | symbol=%s | display_label=%s | selected_geometry=%s | "
        "selected_setup_type=%s | selection_reason=%s | classifier_failure_type=%s | classifier_setup_side=%s | "
        "mse_support=%s | mse_resistance=%s | compression_score=%s | price_pct_from_orb_low=%s | "
        "price_pct_from_orb_high=%s | orb_position_bias=%s | geometry_bias=%s | classifier_override_reason=%s | "
        "orb_high=%s | orb_low=%s | price_last_close_pre_cutoff=%s | selected_slope_sign=%s | "
        "line_price_at_cutoff=%s | abs_line_to_orb_high=%s | abs_line_to_orb_low=%s | rejected_alternates=%s | "
        "narrative=%s",
        symbol,
        str(explain.get("structure_display_label") or chosen.structure_display_label or ""),
        chosen.direction.value,
        chosen.setup_type.value,
        chosen.selection_reason,
        cls.failure_type,
        cls.setup_side,
        explain.get("mse_support"),
        explain.get("mse_resistance"),
        explain.get("compression_score"),
        explain.get("price_pct_from_orb_low"),
        explain.get("price_pct_from_orb_high"),
        explain.get("orb_position_bias"),
        explain.get("geometry_bias"),
        explain.get("classifier_override_reason"),
        explain.get("orb_high"),
        explain.get("orb_low"),
        explain.get("price_last_close_pre_cutoff"),
        sel.get("slope_sign"),
        sel.get("line_price_at_cutoff"),
        sel.get("abs_line_to_orb_high"),
        sel.get("abs_line_to_orb_low"),
        rejected_txt,
        str(explain.get("narrative") or "")[:420],
    )


def select_pre730_structure_setup(
    symbol: str,
    bars: List[OHLCVBar],
    orb_context: Dict[str, Any],
    config: Optional[TrendlineConfig] = None,
) -> Optional[StructureSetupResult]:
    """
    Inspect pre-7:30 structure and return at most one valid setup.

    Returns None when neither structure is valid.

    If the last bar close at or before 7:30 is **below ``orb_low``** or **above
    ``orb_high``**, uses ``TrendlineBuilder.build_cutoff_to_farthest_extreme`` and
    returns that line (no dual-line MSE contest).

    Otherwise, direction is chosen primarily from ``TrendlineBuilder.classify_orb_test_failure``
    (structure-first): failed downside → descending resistance (call); failed upside →
    ascending support (put); trend continuation → no setup. When classification is
    unclear or unavailable (e.g. compression), falls back to MSE / single-geometry logic.
    """
    cfg = config or TrendlineConfig()
    tz_pt = ZoneInfo("America/Los_Angeles")
    builder = TrendlineBuilder(cfg)

    if not bars:
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_skipped | symbol=%s | reason=%s",
            symbol,
            TrendlineReasonCode.NO_VALID_TRENDLINE_STRUCTURE.value,
        )
        return None

    cutoff = _session_cutoff_pt(bars[0].ts, tz_pt)
    pre_cutoff = _pre_cutoff_bars(bars, cutoff)
    if len(pre_cutoff) < 2:
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_skipped | symbol=%s | reason=%s | detail=insufficient_pre730_bars",
            symbol,
            TrendlineReasonCode.NO_VALID_TRENDLINE_STRUCTURE.value,
        )
        return None

    structure_window = _structure_window_bars(pre_cutoff, tz_pt)

    oh0 = _safe_float(orb_context.get("orb_high"))
    ol0 = _safe_float(orb_context.get("orb_low"))
    last_close_regime: Optional[float] = None
    if pre_cutoff:
        try:
            last_close_regime = float(pre_cutoff[-1].close)
        except (TypeError, ValueError):
            last_close_regime = None

    tl_cutoff: Optional[TrendlineDefinition] = None
    cutoff_regime = ""
    if last_close_regime is not None and oh0 is not None and ol0 is not None and oh0 > ol0:
        if last_close_regime < float(ol0):
            tl_cutoff = builder.build_cutoff_to_farthest_extreme(
                symbol, pre_cutoff, orb_context, regime="below_orb"
            )
            cutoff_regime = "below_orb"
        elif last_close_regime > float(oh0):
            tl_cutoff = builder.build_cutoff_to_farthest_extreme(
                symbol, pre_cutoff, orb_context, regime="above_orb"
            )
            cutoff_regime = "above_orb"

    if tl_cutoff is not None:
        struct_n_ce = len(structure_window if len(structure_window) >= 2 else pre_cutoff)
        cls_ce = builder.classify_orb_test_failure(symbol, pre_cutoff, orb_context)
        eb_ce = _eval_bars_for_line(tl_cutoff, structure_window, pre_cutoff, cutoff)
        if tl_cutoff.direction == TrendlineDirection.BULL:
            mse_b_ce = _fit_ascending_support_mse(tl_cutoff, eb_ce)
            mse_r_ce = float("inf")
            tl_bull_ce: Optional[TrendlineDefinition] = tl_cutoff
            tl_bear_ce: Optional[TrendlineDefinition] = None
            bull_ok_ce, bear_ok_ce = True, False
            chosen_ce = StructureSetupResult(
                direction=TrendlineDirection.BULL,
                setup_type=TrendlineSetupType.ASCENDING_SUPPORT,
                trendline=tl_cutoff,
                trigger_direction="breakdown_down",
                expected_option_side="put",
                fit_mse=mse_b_ce,
                alternate_fit_mse=None,
                structure_bar_count=struct_n_ce,
                selection_reason=f"cutoff_to_farthest_extreme|{cutoff_regime}",
                anchor_one_source=str((tl_cutoff.metadata or {}).get("anchor_one_source") or "unknown"),
                anchor_two_source=str((tl_cutoff.metadata or {}).get("anchor_two_source") or "unknown"),
            )
        else:
            mse_r_ce = _fit_descending_resistance_mse(tl_cutoff, eb_ce)
            mse_b_ce = float("inf")
            tl_bull_ce, tl_bear_ce = None, tl_cutoff
            bull_ok_ce, bear_ok_ce = False, True
            chosen_ce = StructureSetupResult(
                direction=TrendlineDirection.BEAR,
                setup_type=TrendlineSetupType.DESCENDING_RESISTANCE,
                trendline=tl_cutoff,
                trigger_direction="breakout_up",
                expected_option_side="call",
                fit_mse=mse_r_ce,
                alternate_fit_mse=None,
                structure_bar_count=struct_n_ce,
                selection_reason=f"cutoff_to_farthest_extreme|{cutoff_regime}",
                anchor_one_source=str((tl_cutoff.metadata or {}).get("anchor_one_source") or "unknown"),
                anchor_two_source=str((tl_cutoff.metadata or {}).get("anchor_two_source") or "unknown"),
            )
        disp_ce, explain_ce = _build_structure_explainability(
            symbol,
            chosen_ce,
            cls_ce,
            orb_context,
            cutoff,
            pre_cutoff,
            tl_bull_ce,
            tl_bear_ce,
            bull_ok_ce,
            bear_ok_ce,
            mse_b_ce,
            mse_r_ce,
            tz_pt,
        )
        chosen_ce = replace(
            chosen_ce,
            structure_display_label=disp_ce,
            explainability=explain_ce,
        )
        _log_trendline_structure_comparison(symbol, chosen_ce, cls_ce, explain_ce)
        _log_trendline_draw_audit(
            symbol,
            tz_pt=tz_pt,
            cutoff=cutoff,
            pre_cutoff=pre_cutoff,
            orb_context=orb_context,
            cls=cls_ce,
            chosen=chosen_ce,
            tl_bull=tl_bull_ce,
            tl_bear=tl_bear_ce,
            mse_bull=mse_b_ce,
            mse_bear=mse_r_ce,
            builder=builder,
            explain=explain_ce,
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_detected | symbol=%s | setup_type=%s | trigger=%s | option_side=%s | "
            "line_geometry=%s | fit_mse=%.8f | selection_reason=%s | structure_bars=%d | trendline_structure_source=cutoff_to_farthest_extreme | "
            "structure_display_label=%s",
            symbol,
            chosen_ce.setup_type.value,
            chosen_ce.trigger_direction,
            chosen_ce.expected_option_side,
            chosen_ce.direction.value,
            float(chosen_ce.fit_mse) if math.isfinite(chosen_ce.fit_mse) else 0.0,
            chosen_ce.selection_reason,
            chosen_ce.structure_bar_count,
            disp_ce,
        )
        return chosen_ce

    tl_bull = builder.build_from_intraday_data(
        symbol, TrendlineDirection.BULL, pre_cutoff, orb_context
    )
    tl_bear = builder.build_from_intraday_data(
        symbol, TrendlineDirection.BEAR, pre_cutoff, orb_context
    )

    bull_ok = tl_bull is not None
    bear_ok = tl_bear is not None

    if not bull_ok and not bear_ok:
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_skipped | symbol=%s | reason=%s",
            symbol,
            TrendlineReasonCode.NO_VALID_TRENDLINE_STRUCTURE.value,
        )
        return None

    mse_bull: float = float("inf")
    mse_bear: float = float("inf")

    if bull_ok and tl_bull is not None:
        eb = _eval_bars_for_line(tl_bull, structure_window, pre_cutoff, cutoff)
        mse_bull = _fit_ascending_support_mse(tl_bull, eb)

    if bear_ok and tl_bear is not None:
        eb = _eval_bars_for_line(tl_bear, structure_window, pre_cutoff, cutoff)
        mse_bear = _fit_descending_resistance_mse(tl_bear, eb)

    if bull_ok and mse_bull == float("inf"):
        bull_ok = False
    if bear_ok and mse_bear == float("inf"):
        bear_ok = False

    if not bull_ok and not bear_ok:
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_skipped | symbol=%s | reason=%s | detail=no_eval_bars_for_fit",
            symbol,
            TrendlineReasonCode.NO_VALID_TRENDLINE_STRUCTURE.value,
        )
        return None

    chosen: Optional[StructureSetupResult] = None
    struct_n = len(structure_window if len(structure_window) >= 2 else pre_cutoff)

    cls = builder.classify_orb_test_failure(symbol, pre_cutoff, orb_context)

    if cls.failure_type == "trend_continuation":
        log.info(
            "TRENDLINE_TREND_CONTINUATION_SKIP | symbol=%s | phase=setup_selector | action=no_setup",
            symbol,
        )
        return None

    if cls.setup_side == "call":
        if not bear_ok or tl_bear is None:
            log.info(
                "TRENDLINE_SETUP_SKIP | symbol=%s | reason=missing_descending_line",
                symbol,
            )
            return None
        _aos_b, _ats_b = _anchor_sources_from_tl(tl_bear)
        chosen = StructureSetupResult(
            direction=TrendlineDirection.BEAR,
            setup_type=TrendlineSetupType.DESCENDING_RESISTANCE,
            trendline=tl_bear,
            trigger_direction="breakout_up",
            expected_option_side="call",
            fit_mse=mse_bear,
            alternate_fit_mse=mse_bull if bull_ok else None,
            structure_bar_count=struct_n,
            selection_reason="structure_failed_downside",
            anchor_one_source=_aos_b,
            anchor_two_source=_ats_b,
        )
        log.info(
            "TRENDLINE_SELECTOR_STRUCTURE_OVERRIDE | symbol=%s | setup_side=%s | failure_type=%s | chosen_direction=%s | selection_reason=%s",
            symbol,
            cls.setup_side,
            cls.failure_type,
            "bear",
            chosen.selection_reason,
        )
    elif cls.setup_side == "put":
        if not bull_ok or tl_bull is None:
            log.info(
                "TRENDLINE_SETUP_SKIP | symbol=%s | reason=missing_ascending_line",
                symbol,
            )
            return None
        _aos_u, _ats_u = _anchor_sources_from_tl(tl_bull)
        chosen = StructureSetupResult(
            direction=TrendlineDirection.BULL,
            setup_type=TrendlineSetupType.ASCENDING_SUPPORT,
            trendline=tl_bull,
            trigger_direction="breakdown_down",
            expected_option_side="put",
            fit_mse=mse_bull,
            alternate_fit_mse=mse_bear if bear_ok else None,
            structure_bar_count=struct_n,
            selection_reason="structure_failed_upside",
            anchor_one_source=_aos_u,
            anchor_two_source=_ats_u,
        )
        log.info(
            "TRENDLINE_SELECTOR_STRUCTURE_OVERRIDE | symbol=%s | setup_side=%s | failure_type=%s | chosen_direction=%s | selection_reason=%s",
            symbol,
            cls.setup_side,
            cls.failure_type,
            "bull",
            chosen.selection_reason,
        )

    if chosen is None and bull_ok and not bear_ok:
        assert tl_bull is not None
        _aos_o, _ats_o = _anchor_sources_from_tl(tl_bull)
        chosen = StructureSetupResult(
            direction=TrendlineDirection.BULL,
            setup_type=TrendlineSetupType.ASCENDING_SUPPORT,
            trendline=tl_bull,
            trigger_direction="breakdown_down",
            expected_option_side="put",
            fit_mse=mse_bull,
            alternate_fit_mse=None,
            structure_bar_count=struct_n,
            selection_reason="only_ascending_support_valid",
            anchor_one_source=_aos_o,
            anchor_two_source=_ats_o,
        )
    elif chosen is None and bear_ok and not bull_ok:
        assert tl_bear is not None
        _aos_od, _ats_od = _anchor_sources_from_tl(tl_bear)
        chosen = StructureSetupResult(
            direction=TrendlineDirection.BEAR,
            setup_type=TrendlineSetupType.DESCENDING_RESISTANCE,
            trendline=tl_bear,
            trigger_direction="breakout_up",
            expected_option_side="call",
            fit_mse=mse_bear,
            alternate_fit_mse=None,
            structure_bar_count=struct_n,
            selection_reason="only_descending_resistance_valid",
            anchor_one_source=_aos_od,
            anchor_two_source=_ats_od,
        )
    elif chosen is None and bull_ok and bear_ok:
        assert tl_bull is not None and tl_bear is not None
        geo_chosen: Optional[StructureSetupResult] = None
        last_px: Optional[float] = None
        if pre_cutoff:
            try:
                last_px = float(pre_cutoff[-1].close)
            except (TypeError, ValueError):
                last_px = None
        oh_g = _safe_float(orb_context.get("orb_high"))
        ol_g = _safe_float(orb_context.get("orb_low"))
        if (
            cls.failure_type == "unclear"
            and last_px is not None
            and oh_g is not None
            and ol_g is not None
            and oh_g > ol_g
        ):
            pref = _orb_price_geometry_preference(last_px, oh_g, ol_g)
            if pref is not None:
                pref_dir, pref_reason = pref
                soft_ratio = 1.32
                if pref_dir == TrendlineDirection.BEAR and mse_bear <= mse_bull * soft_ratio:
                    _aos_gb, _ats_gb = _anchor_sources_from_tl(tl_bear)
                    geo_chosen = StructureSetupResult(
                        direction=TrendlineDirection.BEAR,
                        setup_type=TrendlineSetupType.DESCENDING_RESISTANCE,
                        trendline=tl_bear,
                        trigger_direction="breakout_up",
                        expected_option_side="call",
                        fit_mse=mse_bear,
                        alternate_fit_mse=mse_bull,
                        structure_bar_count=struct_n,
                        selection_reason=f"orb_geometry_price_bias|{pref_reason}",
                        anchor_one_source=_aos_gb,
                        anchor_two_source=_ats_gb,
                    )
                    log.info(
                        "TRENDLINE_SELECTOR_ORB_GEOMETRY_BIAS | symbol=%s | action=prefer_bear | reason=%s | "
                        "mse_bear=%.8f | mse_bull=%.8f | classifier_failure_type=%s",
                        symbol,
                        pref_reason,
                        mse_bear,
                        mse_bull,
                        cls.failure_type,
                    )
                elif pref_dir == TrendlineDirection.BULL and mse_bull <= mse_bear * soft_ratio:
                    _aos_gb2, _ats_gb2 = _anchor_sources_from_tl(tl_bull)
                    geo_chosen = StructureSetupResult(
                        direction=TrendlineDirection.BULL,
                        setup_type=TrendlineSetupType.ASCENDING_SUPPORT,
                        trendline=tl_bull,
                        trigger_direction="breakdown_down",
                        expected_option_side="put",
                        fit_mse=mse_bull,
                        alternate_fit_mse=mse_bear,
                        structure_bar_count=struct_n,
                        selection_reason=f"orb_geometry_price_bias|{pref_reason}",
                        anchor_one_source=_aos_gb2,
                        anchor_two_source=_ats_gb2,
                    )
                    log.info(
                        "TRENDLINE_SELECTOR_ORB_GEOMETRY_BIAS | symbol=%s | action=prefer_bull | reason=%s | "
                        "mse_bull=%.8f | mse_bear=%.8f | classifier_failure_type=%s",
                        symbol,
                        pref_reason,
                        mse_bull,
                        mse_bear,
                        cls.failure_type,
                    )

        if geo_chosen is not None:
            chosen = geo_chosen
        elif _relative_mse_tie(mse_bull, mse_bear):
            bull_strength = abs(tl_bull.anchor_two.price - tl_bull.anchor_one.price)
            bear_strength = abs(tl_bear.anchor_one.price - tl_bear.anchor_two.price)
            choose_bull = bull_strength >= bear_strength
            log.info(
                "TRENDLINE_PIPELINE | stage=setup_tie_break | symbol=%s | mse_support=%.8f | mse_resistance=%.8f | "
                "bull_strength=%.6f | bear_strength=%.6f | selected=%s",
                symbol,
                mse_bull,
                mse_bear,
                bull_strength,
                bear_strength,
                "ascending_support" if choose_bull else "descending_resistance",
            )
            if choose_bull:
                _aos_tb, _ats_tb = _anchor_sources_from_tl(tl_bull)
                chosen = StructureSetupResult(
                    direction=TrendlineDirection.BULL,
                    setup_type=TrendlineSetupType.ASCENDING_SUPPORT,
                    trendline=tl_bull,
                    trigger_direction="breakdown_down",
                    expected_option_side="put",
                    fit_mse=mse_bull,
                    alternate_fit_mse=mse_bear,
                    structure_bar_count=struct_n,
                    selection_reason="ambiguous_tie_break_support_strength",
                    anchor_one_source=_aos_tb,
                    anchor_two_source=_ats_tb,
                )
            else:
                _aos_tr, _ats_tr = _anchor_sources_from_tl(tl_bear)
                chosen = StructureSetupResult(
                    direction=TrendlineDirection.BEAR,
                    setup_type=TrendlineSetupType.DESCENDING_RESISTANCE,
                    trendline=tl_bear,
                    trigger_direction="breakout_up",
                    expected_option_side="call",
                    fit_mse=mse_bear,
                    alternate_fit_mse=mse_bull,
                    structure_bar_count=struct_n,
                    selection_reason="ambiguous_tie_break_resistance_strength",
                    anchor_one_source=_aos_tr,
                    anchor_two_source=_ats_tr,
                )
        elif mse_bull < mse_bear:
            _aos_bm, _ats_bm = _anchor_sources_from_tl(tl_bull)
            chosen = StructureSetupResult(
                direction=TrendlineDirection.BULL,
                setup_type=TrendlineSetupType.ASCENDING_SUPPORT,
                trendline=tl_bull,
                trigger_direction="breakdown_down",
                expected_option_side="put",
                fit_mse=mse_bull,
                alternate_fit_mse=mse_bear,
                structure_bar_count=struct_n,
                selection_reason="better_support_fit_mse",
                anchor_one_source=_aos_bm,
                anchor_two_source=_ats_bm,
            )
        else:
            _aos_rm, _ats_rm = _anchor_sources_from_tl(tl_bear)
            chosen = StructureSetupResult(
                direction=TrendlineDirection.BEAR,
                setup_type=TrendlineSetupType.DESCENDING_RESISTANCE,
                trendline=tl_bear,
                trigger_direction="breakout_up",
                expected_option_side="call",
                fit_mse=mse_bear,
                alternate_fit_mse=mse_bull,
                structure_bar_count=struct_n,
                selection_reason="better_resistance_fit_mse",
                anchor_one_source=_aos_rm,
                anchor_two_source=_ats_rm,
            )

    if chosen:
        assert chosen.trendline is not None
        assert chosen.direction.value in ("bull", "bear")
        assert chosen.anchor_one_source is not None
        assert chosen.anchor_two_source is not None
        disp_label, explain_pkg = _build_structure_explainability(
            symbol,
            chosen,
            cls,
            orb_context,
            cutoff,
            pre_cutoff,
            tl_bull,
            tl_bear,
            bull_ok,
            bear_ok,
            mse_bull,
            mse_bear,
            tz_pt,
        )
        chosen = replace(
            chosen,
            structure_display_label=disp_label,
            explainability=explain_pkg,
        )
        _log_trendline_structure_comparison(symbol, chosen, cls, explain_pkg)
        _log_trendline_draw_audit(
            symbol,
            tz_pt=tz_pt,
            cutoff=cutoff,
            pre_cutoff=pre_cutoff,
            orb_context=orb_context,
            cls=cls,
            chosen=chosen,
            tl_bull=tl_bull,
            tl_bear=tl_bear,
            mse_bull=mse_bull,
            mse_bear=mse_bear,
            builder=builder,
            explain=explain_pkg,
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_detected | symbol=%s | setup_type=%s | trigger=%s | option_side=%s | "
            "line_geometry=%s | fit_mse=%.8f | selection_reason=%s | structure_bars=%d | trendline_structure_source=pre_730_price_action | "
            "structure_display_label=%s",
            symbol,
            chosen.setup_type.value,
            chosen.trigger_direction,
            chosen.expected_option_side,
            chosen.direction.value,
            chosen.fit_mse,
            chosen.selection_reason,
            chosen.structure_bar_count,
            disp_label,
        )
    return chosen
