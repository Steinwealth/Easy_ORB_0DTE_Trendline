#!/usr/bin/env python3
"""
Broker order-status polling and fill reconciliation for execution telemetry.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .execution_telemetry import build_execution_payload, log_execution_event, slippage_vs_mid
except ImportError:  # pragma: no cover
    from execution_telemetry import build_execution_payload, log_execution_event, slippage_vs_mid

log = logging.getLogger(__name__)


@dataclass
class FillReconcileResult:
    order_id: str
    status: str
    filled_quantity: float
    requested_quantity: float
    average_fill_price: Optional[float]
    partial: bool
    actual_fill_confirmed: bool
    raw: Dict[str, Any]


def _walk(obj: Any, keys: Tuple[str, ...]) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                found.append(v)
            found.extend(_walk(v, keys))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk(item, keys))
    return found


def _first_float(vals: List[Any]) -> Optional[float]:
    for v in vals:
        try:
            f = float(v)
            if f == f:  # not nan
                return f
        except (TypeError, ValueError):
            continue
    return None


def _first_int(vals: List[Any]) -> Optional[int]:
    for v in vals:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            continue
    return None


def parse_order_status_response(resp: Any) -> Dict[str, Any]:
    """Best-effort E*TRADE order detail extraction."""
    blob = json.dumps(resp or {}, default=str).upper()
    status = "UNKNOWN"
    for tok in ("EXECUTED", "FILLED", "COMPLETE"):
        if tok in blob:
            status = "FILLED"
            break
    if "PARTIALLY" in blob or "PARTIAL" in blob:
        status = "PARTIAL"
    if "CANCEL" in blob and "CANCELLED" in blob:
        status = "CANCELLED"
    if "OPEN" in blob and status == "UNKNOWN":
        status = "OPEN"
    if "REJECT" in blob:
        status = "REJECTED"

    avg = _first_float(
        _walk(
            resp,
            (
                "averageExecutionPrice",
                "averagePrice",
                "avgPrice",
                "executionPrice",
                "price",
                "limitPrice",
            ),
        )
    )
    filled = _first_float(
        _walk(
            resp,
            (
                "filledQuantity",
                "executedQuantity",
                "quantityExecuted",
                "filled",
            ),
        )
    )
    ordered = _first_float(
        _walk(resp, ("quantity", "orderQuantity", "orderedQuantity"))
    )
    return {
        "status": status,
        "average_fill_price": avg,
        "filled_quantity": filled,
        "ordered_quantity": ordered,
    }


async def reconcile_order_fill(
    etrade: Any,
    order_id: str,
    *,
    requested_quantity: int,
    side: str,
    strategy: str,
    symbol: str,
    trade_id: str,
    signal_ts_iso: str,
    quote_mid: Optional[float],
    timeout_sec: float = 4.0,
    poll_sec: float = 0.45,
) -> FillReconcileResult:
    """Poll get_order_status until terminal state or timeout."""
    deadline = asyncio.get_event_loop().time() + max(0.5, float(timeout_sec))
    last_raw: Dict[str, Any] = {}
    parsed: Dict[str, Any] = {"status": "TIMEOUT"}
    oid = str(order_id)

    while asyncio.get_event_loop().time() < deadline:
        try:
            st = await asyncio.to_thread(etrade.get_order_status, oid)
        except Exception as e:
            log.warning("reconcile_order_fill status poll failed: %s", e)
            await asyncio.sleep(poll_sec)
            continue
        if isinstance(st, dict):
            last_raw = st
        else:
            last_raw = {"response": st}
        parsed = parse_order_status_response(last_raw)
        st_name = str(parsed.get("status") or "")
        if st_name in ("FILLED", "PARTIAL", "CANCELLED", "REJECTED"):
            break
        await asyncio.sleep(poll_sec)

    filled_q = float(parsed.get("filled_quantity") or 0.0)
    if filled_q <= 0 and parsed.get("status") == "FILLED":
        filled_q = float(requested_quantity)
    req_q = float(requested_quantity)
    ord_q = float(parsed.get("ordered_quantity") or req_q)
    if ord_q <= 0:
        ord_q = req_q
    partial = filled_q > 0 and filled_q < ord_q - 1e-6
    avg_px = parsed.get("average_fill_price")
    confirmed = parsed.get("status") in ("FILLED", "PARTIAL") and (
        avg_px is not None and float(avg_px) > 0 or filled_q > 0
    )

    result = FillReconcileResult(
        order_id=oid,
        status=str(parsed.get("status") or "UNKNOWN"),
        filled_quantity=filled_q,
        requested_quantity=req_q,
        average_fill_price=float(avg_px) if avg_px is not None else None,
        partial=partial,
        actual_fill_confirmed=bool(confirmed),
        raw=last_raw,
    )

    fill_ts = datetime.now(timezone.utc).isoformat()
    base = build_execution_payload(
        symbol=symbol,
        trade_id=trade_id,
        strategy=strategy,
        signal_ts=signal_ts_iso,
        submit_ts=signal_ts_iso,
        fill_ts=fill_ts,
        order_type="RECONCILED",
        quote_mid=quote_mid,
        fill_price=result.average_fill_price,
        slippage_vs_mid=slippage_vs_mid(side, quote_mid, result.average_fill_price),
        extra={
            "order_id": oid,
            "actual_fill_confirmed": result.actual_fill_confirmed,
            "filled_quantity": result.filled_quantity,
            "requested_quantity": result.requested_quantity,
            "status": result.status,
        },
    )
    if partial:
        log_execution_event("EXECUTION_PARTIAL_FILL", strategy, base)
    if confirmed:
        log_execution_event("EXECUTION_FILL_RECONCILED", strategy, base)
    return result
