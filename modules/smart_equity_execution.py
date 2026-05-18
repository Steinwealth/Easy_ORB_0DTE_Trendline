#!/usr/bin/env python3
"""
Aggressive-smart equity execution (ORB SO): limit-first with profiles, anti-stall, fill reconcile.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .execution_telemetry import build_execution_payload, log_execution_event, slippage_vs_mid
    from .execution_routing import last_look_max_spread_pct_default, smart_execution_enabled
    from .execution_profiles import resolve_execution_profile
    from .execution_fill_reconcile import reconcile_order_fill
except ImportError:  # pragma: no cover
    from execution_telemetry import build_execution_payload, log_execution_event, slippage_vs_mid
    from execution_routing import last_look_max_spread_pct_default, smart_execution_enabled
    from execution_profiles import resolve_execution_profile
    from execution_fill_reconcile import reconcile_order_fill

log = logging.getLogger(__name__)

_STOCK_BASE_SPREAD_PCT = 2.5


def _round_px(x: float) -> float:
    return round(float(x), 2)


def _mid(bid: float, ask: float) -> float:
    return (float(bid) + float(ask)) / 2.0


def _spread_pct(bid: float, ask: float) -> float:
    mid = _mid(bid, ask)
    if mid <= 0:
        return 999.0
    return (float(ask) - float(bid)) / mid * 100.0


def _order_blob_upper(resp: Any) -> str:
    try:
        import json

        return json.dumps(resp or {}, default=str).upper()
    except Exception:
        return str(resp or "").upper()


def _order_id_from_response(etrade: Any, resp: Any) -> Optional[str]:
    fn = getattr(etrade, "_extract_order_id_from_response", None)
    if callable(fn):
        oid = fn(resp)
        return str(oid) if oid else None
    if isinstance(resp, dict) and resp.get("orderId"):
        return str(resp["orderId"])
    return None


def _order_terminal_filled(blob: str) -> bool:
    for tok in ("EXECUTED", "FILLED", "COMPLETE", "PARTIALLY_FILLED"):
        if tok in blob:
            return True
    return False


def _buy_limit_steps(
    bid: float,
    ask: float,
    mid: float,
    aggression: float,
    max_steps: int,
) -> List[float]:
    """Skew first step toward ask when aggression is high (opening impulse)."""
    ag = max(0.0, min(1.0, float(aggression)))
    steps = [
        _round_px(mid + ag * (ask - mid) + (1.0 - ag) * 0.22 * (ask - mid)),
        _round_px(mid + 0.55 * (ask - mid)),
        _round_px(ask),
        _round_px(ask + 0.01),
    ]
    return steps[: max(1, max_steps)]


def _sell_limit_steps(bid: float, ask: float, mid: float, max_steps: int) -> List[float]:
    steps = [
        _round_px(mid + 0.45 * (ask - mid)),
        _round_px(mid),
        _round_px(mid - 0.45 * (mid - bid)),
        _round_px(bid),
    ]
    return steps[: max(1, max_steps)]


async def _call_place(etrade: Any, **kwargs):
    return await asyncio.to_thread(lambda: etrade.place_order(**kwargs))


async def _call_cancel(etrade: Any, oid: str):
    return await asyncio.to_thread(lambda: etrade.cancel_order(oid))


async def _call_status(etrade: Any, oid: str):
    return await asyncio.to_thread(lambda: etrade.get_order_status(oid))


async def _call_quotes(etrade: Any, symbols: List[str]):
    return await asyncio.to_thread(lambda: etrade.get_quotes(symbols))


async def _emit_fill_summary(
    *,
    strategy: str,
    symbol: str,
    trade_id: str,
    signal_ts_iso: str,
    submit_ts: str,
    side: str,
    order_type: str,
    reprice_count: int,
    bid: float,
    ask: float,
    mid: float,
    sp_pct: float,
    submitted_limit: Optional[float],
    fill_price: Optional[float],
    exit_reason: str,
    exit_plan: Optional[Any],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    urgency = getattr(exit_plan, "exit_urgency", "") if exit_plan else ""
    style = getattr(exit_plan, "exit_execution_style", "") if exit_plan else ""
    pl = build_execution_payload(
        symbol=symbol,
        trade_id=trade_id,
        strategy=strategy,
        signal_ts=signal_ts_iso,
        submit_ts=submit_ts,
        fill_ts=datetime.now(timezone.utc).isoformat(),
        order_type=order_type,
        reprice_count=reprice_count,
        quote_bid=bid,
        quote_ask=ask,
        quote_mid=mid,
        submitted_limit=submitted_limit,
        fill_price=fill_price,
        slippage_vs_mid=slippage_vs_mid(side, mid, fill_price),
        spread_width_pct=sp_pct,
        exit_reason=exit_reason,
        exit_urgency=urgency,
        exit_execution_style=style,
        extra=extra or {},
    )
    log_execution_event("EXECUTION_FILL_SUMMARY", strategy, pl)


async def execute_equity_order_smart(
    etrade: Any,
    *,
    symbol: str,
    quantity: int,
    side: str,
    strategy: str,
    trade_id: str,
    signal_ts_iso: str,
    reference_price: float,
    exit_reason: str = "",
    exit_plan: Optional[Any] = None,
    execution_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Entry/exit for equities. Profiles + anti-stall + optional fill reconciliation.
    """
    submit0 = datetime.now(timezone.utc).isoformat()
    ladder_start = time.monotonic()

    if not smart_execution_enabled() or quantity <= 0:
        resp = await _call_place(
            etrade,
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type="MARKET",
        )
        await _emit_fill_summary(
            strategy=strategy,
            symbol=symbol,
            trade_id=trade_id,
            signal_ts_iso=signal_ts_iso,
            submit_ts=submit0,
            side=side,
            order_type="MARKET",
            reprice_count=0,
            bid=0.0,
            ask=0.0,
            mid=float(reference_price or 0) or 0.0,
            sp_pct=0.0,
            submitted_limit=None,
            fill_price=float(reference_price or 0) or None,
            exit_reason=exit_reason,
            exit_plan=exit_plan,
            extra={"path": "legacy_market", "actual_fill_confirmed": False},
        )
        return resp

    profile, ag_level, opening_impulse = resolve_execution_profile(execution_context)
    if ag_level > 0:
        log_execution_event(
            "EXECUTION_AGGRESSION_ESCALATED",
            strategy,
            build_execution_payload(
                symbol=symbol,
                trade_id=trade_id,
                strategy=strategy,
                signal_ts=signal_ts_iso,
                submit_ts=submit0,
                extra={
                    "execution_aggression_level": ag_level,
                    "opening_impulse_mode": opening_impulse,
                    "profile": profile.name,
                },
            ),
        )

    quotes = await _call_quotes(etrade, [symbol])
    bid = ask = last = 0.0
    if quotes:
        q0 = quotes[0]
        bid = float(getattr(q0, "bid", 0.0) or 0.0)
        ask = float(getattr(q0, "ask", 0.0) or 0.0)
        last = float(getattr(q0, "last_price", 0.0) or getattr(q0, "last", 0.0) or 0.0)
    if bid <= 0 or ask <= 0 or ask < bid:
        bid = max(0.0, float(reference_price) * 0.999)
        ask = max(bid + 0.01, float(reference_price) * 1.001)
    mid = _mid(bid, ask)
    sp_pct = _spread_pct(bid, ask)
    max_sp = max(
        float(last_look_max_spread_pct_default()) * profile.spread_tolerance_mult,
        _STOCK_BASE_SPREAD_PCT * profile.spread_tolerance_mult,
    )

    def _stall_exceeded() -> bool:
        return (time.monotonic() - ladder_start) >= profile.max_total_ladder_sec

    if sp_pct > max_sp and not opening_impulse:
        log_execution_event(
            "EXECUTION_SLIPPAGE_GUARD_REJECT",
            strategy,
            build_execution_payload(
                symbol=symbol,
                trade_id=trade_id,
                strategy=strategy,
                signal_ts=signal_ts_iso,
                submit_ts=submit0,
                spread_width_pct=sp_pct,
                quote_bid=bid,
                quote_ask=ask,
                quote_mid=mid,
                extra={"max_spread_pct": max_sp, "profile": profile.name},
            ),
        )
        return None

    urgency = getattr(exit_plan, "exit_urgency", "") if exit_plan else ""
    style = getattr(exit_plan, "exit_execution_style", "") if exit_plan else ""
    if urgency == "URGENT" or style == "MARKET" or _stall_exceeded():
        if _stall_exceeded() and urgency != "URGENT":
            log_execution_event(
                "EXECUTION_TIMEOUT_ABORT",
                strategy,
                build_execution_payload(
                    symbol=symbol,
                    trade_id=trade_id,
                    strategy=strategy,
                    signal_ts=signal_ts_iso,
                    extra={"profile": profile.name, "path": "pre_ladder_timeout"},
                ),
            )
        resp = await _call_place(etrade, symbol=symbol, quantity=quantity, side=side, order_type="MARKET")
        oid = _order_id_from_response(etrade, resp)
        fill_px = last or mid
        if oid:
            try:
                rec = await reconcile_order_fill(
                    etrade,
                    oid,
                    requested_quantity=quantity,
                    side=side,
                    strategy=strategy,
                    symbol=symbol,
                    trade_id=trade_id,
                    signal_ts_iso=signal_ts_iso,
                    quote_mid=mid,
                    timeout_sec=min(3.0, profile.max_total_ladder_sec),
                    poll_sec=profile.poll_sec,
                )
                if rec.average_fill_price and rec.average_fill_price > 0:
                    fill_px = rec.average_fill_price
            except Exception as re:
                log.warning("urgent market reconcile failed: %s", re)
        await _emit_fill_summary(
            strategy=strategy,
            symbol=symbol,
            trade_id=trade_id,
            signal_ts_iso=signal_ts_iso,
            submit_ts=submit0,
            side=side,
            order_type="MARKET",
            reprice_count=0,
            bid=bid,
            ask=ask,
            mid=mid,
            sp_pct=sp_pct,
            submitted_limit=None,
            fill_price=fill_px,
            exit_reason=exit_reason,
            exit_plan=exit_plan,
            extra={"path": "urgent_market", "actual_fill_confirmed": bool(oid)},
        )
        return resp

    s = str(side).upper()
    if s == "BUY":
        steps = _buy_limit_steps(bid, ask, mid, profile.buy_first_step_aggression, profile.max_reprice + 1)
    else:
        steps = _sell_limit_steps(bid, ask, mid, profile.max_reprice + 1)
    steps = steps[: profile.max_reprice]

    reprice = 0
    last_oid: Optional[str] = None
    for i, lim in enumerate(steps):
        if _stall_exceeded():
            log_execution_event(
                "EXECUTION_TIMEOUT_ABORT",
                strategy,
                build_execution_payload(
                    symbol=symbol,
                    trade_id=trade_id,
                    strategy=strategy,
                    signal_ts=signal_ts_iso,
                    extra={"attempt": i + 1, "profile": profile.name},
                ),
            )
            break

        attempt_ts = datetime.now(timezone.utc).isoformat()
        log_execution_event(
            "EXECUTION_LIMIT_ATTEMPT",
            strategy,
            build_execution_payload(
                symbol=symbol,
                trade_id=trade_id,
                strategy=strategy,
                signal_ts=signal_ts_iso,
                submit_ts=attempt_ts,
                order_type="LIMIT",
                reprice_count=reprice,
                quote_bid=bid,
                quote_ask=ask,
                quote_mid=mid,
                submitted_limit=float(lim),
                spread_width_pct=sp_pct,
                extra={
                    "attempt": i + 1,
                    "profile": profile.name,
                    "aggression_level": ag_level,
                    "opening_impulse_mode": opening_impulse,
                },
            ),
        )
        try:
            resp = await _call_place(
                etrade,
                symbol=symbol,
                quantity=quantity,
                side=side,
                order_type="LIMIT",
                price=float(lim),
            )
        except Exception as e:
            log.warning("equity smart limit place failed: %s", e)
            resp = None
        oid = _order_id_from_response(etrade, resp) if resp else None
        last_oid = oid or last_oid
        if oid:
            attempt_deadline = time.monotonic() + profile.max_wait_per_attempt_sec
            filled = False
            fill_px = float(lim)
            while time.monotonic() < attempt_deadline and not _stall_exceeded():
                await asyncio.sleep(profile.poll_sec)
                try:
                    st = await _call_status(etrade, oid)
                except Exception:
                    st = {}
                if _order_terminal_filled(_order_blob_upper(st)):
                    filled = True
                    try:
                        rec = await reconcile_order_fill(
                            etrade,
                            oid,
                            requested_quantity=quantity,
                            side=side,
                            strategy=strategy,
                            symbol=symbol,
                            trade_id=trade_id,
                            signal_ts_iso=signal_ts_iso,
                            quote_mid=mid,
                            timeout_sec=profile.max_wait_per_attempt_sec,
                            poll_sec=profile.poll_sec,
                        )
                        if rec.average_fill_price and rec.average_fill_price > 0:
                            fill_px = rec.average_fill_price
                    except Exception:
                        pass
                    break
            if filled:
                await _emit_fill_summary(
                    strategy=strategy,
                    symbol=symbol,
                    trade_id=trade_id,
                    signal_ts_iso=signal_ts_iso,
                    submit_ts=attempt_ts,
                    side=side,
                    order_type="LIMIT",
                    reprice_count=reprice,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    sp_pct=sp_pct,
                    submitted_limit=float(lim),
                    fill_price=fill_px,
                    exit_reason=exit_reason,
                    exit_plan=exit_plan,
                    extra={
                        "order_id": oid,
                        "actual_fill_confirmed": True,
                        "profile": profile.name,
                    },
                )
                return resp
            try:
                await _call_cancel(etrade, oid)
            except Exception as ce:
                log.warning("equity cancel after non-fill failed: %s", ce)
        reprice += 1
        log_execution_event(
            "EXECUTION_REPRICE",
            strategy,
            build_execution_payload(
                symbol=symbol,
                trade_id=trade_id,
                strategy=strategy,
                signal_ts=signal_ts_iso,
                submit_ts=attempt_ts,
                order_type="LIMIT",
                reprice_count=reprice,
                submitted_limit=float(lim),
                quote_mid=mid,
                extra={"order_id": oid or ""},
            ),
        )

    log_execution_event(
        "EXECUTION_FORCE_FALLBACK",
        strategy,
        build_execution_payload(
            symbol=symbol,
            trade_id=trade_id,
            strategy=strategy,
            signal_ts=signal_ts_iso,
            order_type="MARKET",
            reprice_count=reprice,
            extra={"last_limit_order_id": last_oid or "", "profile": profile.name},
        ),
    )
    log_execution_event(
        "EXECUTION_MARKET_FALLBACK",
        strategy,
        build_execution_payload(
            symbol=symbol,
            trade_id=trade_id,
            strategy=strategy,
            signal_ts=signal_ts_iso,
            order_type="MARKET",
            reprice_count=reprice,
            quote_bid=bid,
            quote_ask=ask,
            quote_mid=mid,
            exit_reason=exit_reason,
            exit_urgency=urgency,
            exit_execution_style="MARKET_FALLBACK",
            extra={"path": "market_fallback"},
        ),
    )
    resp = await _call_place(etrade, symbol=symbol, quantity=quantity, side=side, order_type="MARKET")
    oid = _order_id_from_response(etrade, resp)
    fill_px = last or mid
    if oid:
        try:
            rec = await reconcile_order_fill(
                etrade,
                oid,
                requested_quantity=quantity,
                side=side,
                strategy=strategy,
                symbol=symbol,
                trade_id=trade_id,
                signal_ts_iso=signal_ts_iso,
                quote_mid=mid,
                timeout_sec=3.0,
                poll_sec=profile.poll_sec,
            )
            if rec.average_fill_price and rec.average_fill_price > 0:
                fill_px = rec.average_fill_price
        except Exception:
            pass
    await _emit_fill_summary(
        strategy=strategy,
        symbol=symbol,
        trade_id=trade_id,
        signal_ts_iso=signal_ts_iso,
        submit_ts=datetime.now(timezone.utc).isoformat(),
        side=side,
        order_type="MARKET",
        reprice_count=reprice,
        bid=bid,
        ask=ask,
        mid=mid,
        sp_pct=sp_pct,
        submitted_limit=None,
        fill_price=fill_px,
        exit_reason=exit_reason,
        exit_plan=exit_plan,
        extra={"path": "market_fallback", "actual_fill_confirmed": bool(oid)},
    )
    return resp
