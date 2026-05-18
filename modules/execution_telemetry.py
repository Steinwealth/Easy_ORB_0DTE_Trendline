#!/usr/bin/env python3
"""
Structured execution telemetry (ORB SO, ORB 0DTE, Trendline 0DTE).

Single-line INFO logs with grep-friendly tokens; payload is JSON for downstream calibration.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_execution_payload(
    *,
    symbol: str,
    trade_id: str = "",
    strategy: str = "",
    signal_ts: Optional[str] = None,
    submit_ts: Optional[str] = None,
    fill_ts: Optional[str] = None,
    order_type: str = "",
    reprice_count: int = 0,
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    submitted_limit: Optional[float] = None,
    fill_price: Optional[float] = None,
    slippage_vs_mid: Optional[float] = None,
    spread_width_pct: Optional[float] = None,
    exit_reason: str = "",
    exit_urgency: str = "",
    exit_execution_style: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    lat_ms: Optional[float] = None
    if signal_ts and fill_ts:
        try:
            t0 = datetime.fromisoformat(signal_ts.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(fill_ts.replace("Z", "+00:00"))
            lat_ms = max(0.0, (t1 - t0).total_seconds() * 1000.0)
        except Exception:
            lat_ms = None
    if lat_ms is None and signal_ts:
        try:
            t0 = datetime.fromisoformat(signal_ts.replace("Z", "+00:00"))
            t1 = datetime.now(timezone.utc)
            lat_ms = max(0.0, (t1 - t0).total_seconds() * 1000.0)
        except Exception:
            lat_ms = None

    out: Dict[str, Any] = {
        "symbol": symbol,
        "trade_id": trade_id or "",
        "strategy": strategy or "",
        "signal_ts": signal_ts or "",
        "submit_ts": submit_ts or "",
        "fill_ts": fill_ts or "",
        "latency_ms": round(lat_ms, 1) if isinstance(lat_ms, (int, float)) else None,
        "order_type": order_type or "",
        "reprice_count": int(reprice_count or 0),
        "quote_bid": quote_bid,
        "quote_ask": quote_ask,
        "quote_mid": quote_mid,
        "submitted_limit": submitted_limit,
        "fill_price": fill_price,
        "slippage_vs_mid": slippage_vs_mid,
        "spread_width_pct": spread_width_pct,
        "exit_reason": exit_reason or "",
        "exit_urgency": exit_urgency or "",
        "exit_execution_style": exit_execution_style or "",
    }
    if extra:
        out["extra"] = extra
    return out


def log_execution_event(event: str, strategy: str, payload: Dict[str, Any]) -> None:
    """Emit one structured INFO line: EXECUTION_<EVENT> | strategy=... | {...}"""
    token = str(event or "").strip().upper()
    if not token.startswith("EXECUTION_"):
        token = f"EXECUTION_{token}"
    strat = str(strategy or "unknown")
    try:
        body = json.dumps(payload, default=str, separators=(",", ":"))
    except Exception:
        body = json.dumps({"error": "serialization_failed", "symbol": payload.get("symbol")})
    log.info("%s | strategy=%s | %s", token, strat, body)


def slippage_vs_mid(side: str, mid: Optional[float], fill: Optional[float]) -> Optional[float]:
    if mid is None or fill is None or mid <= 0:
        return None
    s = str(side or "").upper()
    if s == "BUY":
        return round((float(fill) - float(mid)) / float(mid) * 10000.0, 2)  # bps
    if s == "SELL":
        return round((float(mid) - float(fill)) / float(mid) * 10000.0, 2)
    return None


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0
