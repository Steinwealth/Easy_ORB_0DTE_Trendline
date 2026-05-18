"""
Execution-time feature snapshots for ORB and Trendline execution paths (Priority Optimizer).

Rev 00333: Persist per-symbol technical fields + Red Day / enhanced gating context at execution
decision time so offline analysis can relate profitable vs losing days.

Scope: snapshot payloads can represent multiple execution paths (for example ORB SO 7:30 execution
or Trendline 0DTE options execution). `snapshot_strategy` tags the source strategy so downstream
analysis can split cohorts cleanly. `market_context.total_scanned` remains context-specific to the
snapshot producer.

Writes:
- GCS: priority_optimizer/execution_snapshots/{date}_{time}_{stage}.json (when GCS enabled)
- Local: priority_optimizer/execution_snapshots/ (same filename, repo-relative)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FEATURE_KEYS_89 = [
    # Price (5)
    "open", "high", "low", "close", "volume",
    # Moving averages (5)
    "sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
    # Momentum (7)
    "rsi", "rsi_14", "rsi_21", "macd", "macd_signal", "macd_histogram", "momentum_10",
    # Volatility (7)
    "atr", "bollinger_upper", "bollinger_middle", "bollinger_lower", "bollinger_width", "bollinger_position", "volatility",
    # Volume (4)
    "volume_ratio", "volume_sma", "obv", "ad_line",
    # Pattern recognition (4)
    "doji", "hammer", "engulfing", "morning_star",
    # VWAP (2)
    "vwap", "vwap_distance_pct",
    # Relative strength (1)
    "rs_vs_spy",
    # ORB data (6)
    "orb_high", "orb_low", "orb_open", "orb_close", "orb_volume", "orb_range_pct",
    # Market context (2)
    "spy_price", "spy_change_pct",
    # Trade data (15)
    "entry_price", "exit_price", "entry_time", "exit_time", "shares", "position_value", "peak_price", "peak_pct",
    "pnl_dollars", "pnl_pct", "exit_reason", "win", "holding_minutes", "entry_bar_volatility", "time_weighted_peak",
    # Ranking data (6)
    "rank", "priority_score", "confidence", "orb_volume_ratio", "exec_volume_ratio", "category",
    # Risk management (8)
    "current_stop_loss", "stop_loss_distance_pct", "opening_bar_protection_active", "trailing_activated",
    "trailing_distance_pct", "breakeven_activated", "gap_risk_pct", "max_adverse_excursion",
    # Market conditions (5)
    "market_regime", "volatility_regime", "trend_direction", "volume_regime", "momentum_regime",
    # Additional indicators (16)
    "stoch_k", "stoch_d", "williams_r", "cci", "adx", "plus_di", "minus_di", "aroon_up",
    "aroon_down", "mfi", "cmf", "roc", "ppo", "tsi", "ult_osc", "ichimoku_base",
    # Execution vs previous-day range context (new training features)
    "prev_day_high", "prev_day_low", "execution_price_for_prev_day_check",
    "price_vs_prev_day_high", "price_vs_prev_day_low", "price_vs_prev_day_range",
    "trade_direction", "prev_day_entry_gate_passed",
]


def _orb_from_signal(sig: Dict[str, Any]) -> Dict[str, Any]:
    od = sig.get("orb_data")
    if isinstance(od, dict):
        return {
            "orb_high": od.get("orb_high"),
            "orb_low": od.get("orb_low"),
            "orb_range": od.get("orb_range"),
        }
    if od is not None and hasattr(od, "orb_high"):
        return {
            "orb_high": getattr(od, "orb_high", None),
            "orb_low": getattr(od, "orb_low", None),
            "orb_range": getattr(od, "orb_range", None),
        }
    return {
        "orb_high": sig.get("orb_high"),
        "orb_low": sig.get("orb_low"),
        "orb_range": sig.get("orb_range"),
    }


def extract_signal_features(sig: Dict[str, Any]) -> Dict[str, Any]:
    """Flat technical + rank fields for one SO signal (JSON-serializable)."""
    orb = _orb_from_signal(sig)
    return {
        "symbol": (sig.get("symbol") or "").strip().upper() or None,
        "side": str(sig.get("side", "LONG")).upper(),
        "price": sig.get("price") or sig.get("current_price"),
        "current_price": sig.get("current_price"),
        "confidence": sig.get("confidence"),
        "priority_score": sig.get("priority_score"),
        "priority_rank": sig.get("priority_rank"),
        "rsi": sig.get("rsi"),
        "macd_histogram": sig.get("macd_histogram"),
        "volume_ratio": sig.get("volume_ratio"),
        "underlying_symbol": sig.get("underlying_symbol"),
        "underlying_volume_ratio": sig.get("underlying_volume_ratio"),
        "underlying_rsi": sig.get("underlying_rsi"),
        "underlying_macd_histogram": sig.get("underlying_macd_histogram"),
        "orb_volume_ratio": sig.get("orb_volume_ratio"),
        "rs_vs_spy": sig.get("rs_vs_spy"),
        "vwap_distance_pct": sig.get("vwap_distance_pct"),
        "orb_high": orb.get("orb_high"),
        "orb_low": orb.get("orb_low"),
        "orb_range": orb.get("orb_range"),
        "volume_color": sig.get("volume_color") or sig.get("validation_volume_color"),
        "quantity": sig.get("quantity"),
        "position_size_pct": sig.get("position_size_pct"),
        "risk_reduction_applied": sig.get("risk_reduction_applied"),
    }


def _safe_json_value(value: Any) -> Any:
    """Best-effort conversion of nested values to JSON-safe output."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return _safe_json_value(value.to_dict())
        except Exception:
            return str(value)
    return str(value)


def extract_signal_features_89(sig: Dict[str, Any]) -> Dict[str, Any]:
    """
    Canonical 89-feature map at execution time.
    Missing fields are preserved as null to keep schema stable across sessions.
    """
    od = sig.get("orb_data")
    md = sig.get("metadata") if isinstance(sig.get("metadata"), dict) else {}
    out: Dict[str, Any] = {k: None for k in FEATURE_KEYS_89}
    for k in FEATURE_KEYS_89:
        if k in sig:
            out[k] = _safe_json_value(sig.get(k))
        elif isinstance(md, dict) and k in md:
            out[k] = _safe_json_value(md.get(k))

    # ORB fallbacks from orb_data object/dict
    if isinstance(od, dict):
        out["orb_high"] = out["orb_high"] if out["orb_high"] is not None else _safe_json_value(od.get("orb_high", od.get("high")))
        out["orb_low"] = out["orb_low"] if out["orb_low"] is not None else _safe_json_value(od.get("orb_low", od.get("low")))
        out["orb_open"] = out["orb_open"] if out["orb_open"] is not None else _safe_json_value(od.get("orb_open", od.get("open")))
        out["orb_close"] = out["orb_close"] if out["orb_close"] is not None else _safe_json_value(od.get("orb_close", od.get("close")))
        out["orb_volume"] = out["orb_volume"] if out["orb_volume"] is not None else _safe_json_value(od.get("orb_volume", od.get("volume")))
        out["orb_range_pct"] = out["orb_range_pct"] if out["orb_range_pct"] is not None else _safe_json_value(od.get("orb_range_pct"))
    elif od is not None:
        out["orb_high"] = out["orb_high"] if out["orb_high"] is not None else _safe_json_value(getattr(od, "orb_high", getattr(od, "high", None)))
        out["orb_low"] = out["orb_low"] if out["orb_low"] is not None else _safe_json_value(getattr(od, "orb_low", getattr(od, "low", None)))
        out["orb_open"] = out["orb_open"] if out["orb_open"] is not None else _safe_json_value(getattr(od, "orb_open", getattr(od, "open", None)))
        out["orb_close"] = out["orb_close"] if out["orb_close"] is not None else _safe_json_value(getattr(od, "orb_close", getattr(od, "close", None)))
        out["orb_volume"] = out["orb_volume"] if out["orb_volume"] is not None else _safe_json_value(getattr(od, "orb_volume", getattr(od, "volume", None)))
        out["orb_range_pct"] = out["orb_range_pct"] if out["orb_range_pct"] is not None else _safe_json_value(getattr(od, "orb_range_pct", None))

    # Common aliases
    out["rank"] = out["rank"] if out["rank"] is not None else _safe_json_value(sig.get("priority_rank"))
    out["shares"] = out["shares"] if out["shares"] is not None else _safe_json_value(sig.get("quantity"))
    out["position_value"] = out["position_value"] if out["position_value"] is not None else _safe_json_value(sig.get("position_after_redistribution", sig.get("position_value")))
    return out


def _risk_assessment_to_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return {"repr": str(obj)}


def build_execution_snapshot_payload(
    *,
    date_key: str,
    stage: str,
    signals_full_pool: List[Dict[str, Any]],
    signals_selected_for_execution: List[Dict[str, Any]],
    red_day_filter_blocked: bool,
    portfolio_red_day_pattern: bool,
    red_day_reason: Optional[str],
    red_day_metrics: Optional[Dict[str, Any]],
    enhanced_assessment: Any,
    spy_momentum: float,
    vix_level: float,
    total_scanned: int,
    snapshot_strategy: str = "easy_orb_etf_so",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from zoneinfo import ZoneInfo

    pt = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(pt)
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": date_key,
        "captured_at_pt": now_pt.isoformat(),
        "stage": stage,
        "snapshot_strategy": str(snapshot_strategy or "unknown"),
        "market_context": {
            "spy_momentum_pct": spy_momentum,
            "vix_level": vix_level,
            "total_scanned": total_scanned,
        },
        "red_day": {
            "execution_blocked_by_portfolio_flag": red_day_filter_blocked,
            "portfolio_pattern_triggered": portfolio_red_day_pattern,
            "reason": red_day_reason,
            "metrics": red_day_metrics,
        },
        "enhanced_red_day": _risk_assessment_to_dict(enhanced_assessment),
        "signal_count_full_pool": len(signals_full_pool),
        "signal_count_selected": len(signals_selected_for_execution),
        "symbols_full_pool": [
            (s.get("symbol") or "").strip().upper()
            for s in signals_full_pool
            if isinstance(s, dict) and s.get("symbol")
        ],
        "symbols_selected": [
            (s.get("symbol") or "").strip().upper()
            for s in signals_selected_for_execution
            if isinstance(s, dict) and s.get("symbol")
        ],
        "features_full_pool": [extract_signal_features(s) for s in signals_full_pool if isinstance(s, dict)],
        "features_selected": [
            extract_signal_features(s) for s in signals_selected_for_execution if isinstance(s, dict)
        ],
        "feature_schema_89_keys": FEATURE_KEYS_89,
        "features_89_full_pool": [extract_signal_features_89(s) for s in signals_full_pool if isinstance(s, dict)],
        "features_89_selected": [
            extract_signal_features_89(s) for s in signals_selected_for_execution if isinstance(s, dict)
        ],
    }
    if extra:
        payload["extra"] = extra
    return payload


def persist_execution_snapshot(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Save snapshot to GCS and local priority_optimizer/execution_snapshots/.
    Returns (gcs_path_or_none, local_path_or_none).
    """
    try:
        from .config_loader import get_config_value
    except Exception:
        get_config_value = lambda k, d=None: d  # type: ignore

    enabled = str(get_config_value("ENABLE_EXECUTION_FEATURE_SNAPSHOT", "true")).lower() in (
        "1",
        "true",
        "yes",
    )
    if not enabled:
        return None, None
    try:
        keep_sessions = int(get_config_value("EXECUTION_SNAPSHOT_KEEP_SESSIONS", 50))
    except Exception:
        keep_sessions = 50
    if keep_sessions <= 0:
        keep_sessions = 50

    date_key = payload.get("date") or "unknown"
    stage = str(payload.get("stage") or "unknown").replace("/", "_")
    time_part = datetime.now().strftime("%H%M%S")
    fname = f"{date_key}_{time_part}_{stage}.json"

    root = Path(__file__).resolve().parent.parent
    local_dir = root / "priority_optimizer" / "execution_snapshots"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / fname
    body = json.dumps(payload, indent=2, default=str)

    gcs_path_written: Optional[str] = None
    try:
        from .gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        gcs_path = f"priority_optimizer/execution_snapshots/{fname}"
        if gcs.enabled and gcs.upload_string(gcs_path, body):
            gcs_path_written = gcs_path
    except Exception as exc:
        log.warning("Execution snapshot GCS upload skipped: %s", exc)

    try:
        local_path.write_text(body, encoding="utf-8")
    except Exception as exc:
        log.error("Execution snapshot local write failed: %s", exc)
        return gcs_path_written, None

    log.info(
        "EXECUTION_FEATURE_SNAPSHOT | stage=%s | gcs=%s | local=%s | pool=%s | selected=%s",
        stage,
        gcs_path_written or "-",
        str(local_path),
        payload.get("signal_count_full_pool"),
        payload.get("signal_count_selected"),
    )
    _prune_execution_snapshots(local_dir=local_dir, keep_sessions=keep_sessions)
    try:
        from .gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        if gcs.enabled:
            _prune_execution_snapshots_gcs(gcs=gcs, keep_sessions=keep_sessions)
    except Exception as exc:
        log.debug("Execution snapshot GCS retention prune skipped: %s", exc)
    return gcs_path_written, str(local_path)


def _extract_session_date_from_name(name: str) -> Optional[str]:
    date_part = name.split("_", 1)[0]
    if not _DATE_RE.match(date_part):
        return None
    return date_part


def _prune_execution_snapshots(local_dir: Path, keep_sessions: int) -> None:
    files = sorted(local_dir.glob("*.json"))
    if not files:
        return
    date_to_files: Dict[str, List[Path]] = {}
    for p in files:
        date_key = _extract_session_date_from_name(p.name)
        if not date_key:
            continue
        date_to_files.setdefault(date_key, []).append(p)
    if len(date_to_files) <= keep_sessions:
        return
    keep_dates = set(sorted(date_to_files.keys(), reverse=True)[:keep_sessions])
    deleted = 0
    for date_key, paths in date_to_files.items():
        if date_key in keep_dates:
            continue
        for p in paths:
            try:
                p.unlink(missing_ok=True)
                deleted += 1
            except Exception as exc:
                log.debug("Local snapshot prune failed for %s: %s", p, exc)
    if deleted:
        log.info("Execution snapshot retention(local): removed %s files", deleted)


def _prune_execution_snapshots_gcs(gcs: Any, keep_sessions: int) -> None:
    prefix = "priority_optimizer/execution_snapshots/"
    paths = gcs.list_files(prefix)
    if not paths:
        return
    date_to_paths: Dict[str, List[str]] = {}
    for gcs_path in paths:
        name = Path(gcs_path).name
        date_key = _extract_session_date_from_name(name)
        if not date_key or not name.endswith(".json"):
            continue
        date_to_paths.setdefault(date_key, []).append(gcs_path)
    if len(date_to_paths) <= keep_sessions:
        return
    keep_dates = set(sorted(date_to_paths.keys(), reverse=True)[:keep_sessions])
    deleted = 0
    for date_key, group in date_to_paths.items():
        if date_key in keep_dates:
            continue
        for gcs_path in group:
            try:
                if gcs.delete_file(gcs_path):
                    deleted += 1
            except Exception as exc:
                log.debug("GCS snapshot prune failed for %s: %s", gcs_path, exc)
    if deleted:
        log.info("Execution snapshot retention(gcs): removed %s files", deleted)
