#!/usr/bin/env python3
"""
Execution-layer normalization for Easy 0DTE ORB options.

Maps strategy labels to structural position types and builds a shared metadata
schema for monitoring / exits. ETF path does not use this module.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .options_chain_manager import CreditSpread, DebitSpread, OptionContract

log = logging.getLogger(__name__)

# Canonical structural types consumed by shared options monitoring.
SINGLE_LEG_LONG_CALL = "single_leg_long_call"
SINGLE_LEG_LONG_PUT = "single_leg_long_put"
DEBIT_SPREAD = "debit_spread"
CREDIT_SPREAD = "credit_spread"

STRUCTURAL_POSITION_TYPES = frozenset(
    {SINGLE_LEG_LONG_CALL, SINGLE_LEG_LONG_PUT, DEBIT_SPREAD, CREDIT_SPREAD}
)


def validate_normalized_options_for_stealth(norm: Any) -> Tuple[bool, List[str]]:
    """
    Minimum field checks before using metadata['normalized_options'] as stealth/monitoring source of truth.

    Returns (is_valid, missing_or_invalid_tags). Never raises.
    """
    missing: List[str] = []
    if not isinstance(norm, dict):
        return False, ["not_a_dict"]

    pt = str(norm.get("position_type") or "").strip().lower()
    if pt not in STRUCTURAL_POSITION_TYPES:
        missing.append("position_type_invalid")

    und = str(norm.get("underlying_symbol") or "").strip()
    if not und:
        missing.append("underlying_symbol")

    exp = str(norm.get("expiration_ymd") or "").strip()
    if not exp:
        missing.append("expiration_ymd")

    legs = norm.get("legs")
    leg_dicts: List[Dict[str, Any]] = []
    if isinstance(legs, list):
        leg_dicts = [x for x in legs if isinstance(x, dict)]
    else:
        missing.append("legs_not_a_list")

    def _fnum(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    ev = _fnum(norm.get("entry_value"))
    ed_raw = norm.get("entry_debit")
    ec_raw = norm.get("entry_credit")
    ed = _fnum(ed_raw) if ed_raw is not None else 0.0
    ec = _fnum(ec_raw) if ec_raw is not None else 0.0

    if pt in STRUCTURAL_POSITION_TYPES:
        if pt in (SINGLE_LEG_LONG_CALL, SINGLE_LEG_LONG_PUT):
            if len(leg_dicts) < 1:
                missing.append("legs_min_1")
            if ev <= 0 and ed <= 0:
                missing.append("entry_value_or_entry_debit")
        elif pt == DEBIT_SPREAD:
            if len(leg_dicts) < 2:
                missing.append("legs_min_2")
            if ev <= 0 and ed <= 0:
                missing.append("entry_value_or_entry_debit")
        elif pt == CREDIT_SPREAD:
            if len(leg_dicts) < 2:
                missing.append("legs_min_2")
            if ev <= 0 and ec <= 0:
                missing.append("entry_value_or_entry_credit")

    return (len(missing) == 0), missing


def normalize_execution_result(norm: Dict[str, Any]) -> Dict[str, Any]:
    """Canonicalize execution metadata before stealth registration/monitoring."""
    if not isinstance(norm, dict):
        return {}
    out = dict(norm)
    out["position_type"] = str(out.get("position_type") or "").strip().lower()
    out["underlying_symbol"] = str(out.get("underlying_symbol") or "").strip().upper()
    out["expiration_ymd"] = expiry_to_ymd(str(out.get("expiration_ymd") or ""))
    out["strategy_type"] = str(out.get("strategy_type") or "").strip().lower()
    out.setdefault("legs", [])
    return out


def expiry_to_ymd(expiry: str) -> str:
    """Normalize expiry to YYYYMMDD when possible."""
    if not expiry:
        return ""
    s = str(expiry).strip().replace("-", "")
    if len(s) >= 8:
        return s[:8]
    return s


def orb_strategy_to_structural_execution_type(
    strategy_type: str,
    direction: str,
    *,
    spread_type: str = "debit",
) -> str:
    """
    Map Level-2 ORB strategy label + execution context to structural position_type.

    spread_type: 'single_leg' | 'debit' | 'credit' from signal.
    """
    st = (strategy_type or "").strip().lower()
    sp = (spread_type or "debit").strip().lower()
    d = (direction or "LONG").strip().upper()

    if sp == "credit":
        return CREDIT_SPREAD
    if sp == "single_leg":
        if st == "long_put":
            return SINGLE_LEG_LONG_PUT
        if st == "long_call":
            return SINGLE_LEG_LONG_CALL
        if st == "lotto":
            return SINGLE_LEG_LONG_PUT if d == "SHORT" else SINGLE_LEG_LONG_CALL

    if st == "long_call":
        return SINGLE_LEG_LONG_CALL
    if st == "long_put":
        return SINGLE_LEG_LONG_PUT
    if st == "lotto":
        # Lotto uses signal option side; direction LONG -> call, SHORT -> put.
        return SINGLE_LEG_LONG_PUT if d == "SHORT" else SINGLE_LEG_LONG_CALL
    if st in ("momentum_scalper", "itm_probability_spread", "debit_spread"):
        return DEBIT_SPREAD
    if st in ("no_trade", ""):
        return DEBIT_SPREAD
    return DEBIT_SPREAD


def _leg_from_contract(
    contract: Any,
    *,
    long_or_short: str,
    quantity: int,
    leg_id_suffix: str,
) -> Dict[str, Any]:
    sym = getattr(contract, "symbol", None) or ""
    return {
        "leg_id": f"{sym}_{leg_id_suffix}".strip("_") or leg_id_suffix,
        "symbol": sym or None,
        "long_or_short": long_or_short,
        "option_side": str(getattr(contract, "option_type", "") or "").lower(),
        "strike": float(getattr(contract, "strike", 0.0) or 0.0),
        "quantity": int(max(1, quantity)),
        "entry_price": float(getattr(contract, "mid_price", 0.0) or 0.0),
        "delta_at_entry": float(getattr(contract, "delta", 0.0) or 0.0),
    }


def build_normalized_metadata_debit_spread(
    *,
    trade_id: str,
    spread: Union["DebitSpread", Dict[str, Any]],
    quantity: int,
    strategy_type: str = "",
    direction: str = "",
    spread_type: str = "debit",
    strategy_name: str = "Easy ORB 0DTE",
    source_path: str = "easy0DTE",
    setup_type: str = "",
    trigger_direction: str = "",
) -> Dict[str, Any]:
    if hasattr(spread, "symbol"):
        sym = spread.symbol
        exp = expiry_to_ymd(spread.expiry)
        opt_t = str(spread.option_type or "").lower()
        legs: List[Dict[str, Any]] = [
            _leg_from_contract(
                spread.long_contract,
                long_or_short="long",
                quantity=quantity,
                leg_id_suffix=f"{exp}_L{int(spread.long_strike)}_{opt_t}",
            ),
            _leg_from_contract(
                spread.short_contract,
                long_or_short="short",
                quantity=quantity,
                leg_id_suffix=f"{exp}_S{int(spread.short_strike)}_{opt_t}",
            ),
        ]
        entry_debit = float(spread.debit_cost)
    else:
        d = spread if isinstance(spread, dict) else {}
        sym = str(d.get("symbol") or "")
        exp = expiry_to_ymd(str(d.get("expiry") or ""))
        opt_t = str(d.get("option_type") or "").lower()
        lc = d.get("long_contract") or {}
        sc = d.get("short_contract") or {}
        entry_debit = float(d.get("debit_cost") or 0.0)
        long_mid = float(lc.get("mid_price") or 0.0)
        short_mid = float(sc.get("mid_price") or 0.0)
        if long_mid <= 0 and entry_debit > 0:
            long_mid = max(entry_debit * 0.65, 0.01)
        if short_mid <= 0 and entry_debit > 0:
            short_mid = max(entry_debit - long_mid, 0.01)
        legs = [
            {
                "leg_id": f"{sym}_{exp}_L{d.get('long_strike')}_{opt_t}",
                "symbol": lc.get("symbol"),
                "long_or_short": "long",
                "option_side": opt_t,
                "strike": float(d.get("long_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": long_mid,
                "delta_at_entry": float(lc.get("delta") or 0.0),
            },
            {
                "leg_id": f"{sym}_{exp}_S{d.get('short_strike')}_{opt_t}",
                "symbol": sc.get("symbol"),
                "long_or_short": "short",
                "option_side": opt_t,
                "strike": float(d.get("short_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": short_mid,
                "delta_at_entry": float(sc.get("delta") or 0.0),
            },
        ]

    structural = orb_strategy_to_structural_execution_type(
        strategy_type, direction, spread_type=spread_type
    )
    entry_value = entry_debit
    return {
        "position_type": structural,
        "legacy_position_type": "debit_spread",
        "strategy_name": strategy_name,
        "strategy_type": "orb_0dte",
        "orb_strategy_type": strategy_type or "",
        "underlying_symbol": sym,
        "expiration_ymd": exp,
        "entry_value": entry_value,
        "entry_debit": entry_debit,
        "entry_credit": None,
        "current_value": entry_value,
        "legs": legs,
        "delta_at_entry": float(legs[0].get("delta_at_entry") or 0.0) if legs else 0.0,
        "trigger_direction": trigger_direction or direction,
        "setup_type": setup_type or strategy_type,
        "source_path": source_path,
        "trade_id": trade_id,
    }


def build_normalized_metadata_credit_spread(
    *,
    trade_id: str,
    spread: Union["CreditSpread", Dict[str, Any]],
    quantity: int,
    strategy_type: str = "",
    direction: str = "",
    spread_type: str = "credit",
    strategy_name: str = "Easy ORB 0DTE",
    source_path: str = "easy0DTE",
    setup_type: str = "",
    trigger_direction: str = "",
) -> Dict[str, Any]:
    if hasattr(spread, "symbol"):
        sym = spread.symbol
        exp = expiry_to_ymd(spread.expiry)
        opt_t = str(spread.option_type or "").lower()
        legs = [
            _leg_from_contract(
                spread.short_contract,
                long_or_short="short",
                quantity=quantity,
                leg_id_suffix=f"{exp}_Sh{int(spread.short_strike)}_{opt_t}",
            ),
            _leg_from_contract(
                spread.long_contract,
                long_or_short="long",
                quantity=quantity,
                leg_id_suffix=f"{exp}_Lg{int(spread.long_strike)}_{opt_t}",
            ),
        ]
        entry_credit = float(spread.credit_received)
    else:
        d = spread if isinstance(spread, dict) else {}
        sym = str(d.get("symbol") or "")
        exp = expiry_to_ymd(str(d.get("expiry") or ""))
        opt_t = str(d.get("option_type") or "").lower()
        sc = d.get("short_contract") or {}
        lc = d.get("long_contract") or {}
        legs = [
            {
                "leg_id": f"{sym}_{exp}_Sh{d.get('short_strike')}_{opt_t}",
                "symbol": sc.get("symbol"),
                "long_or_short": "short",
                "option_side": opt_t,
                "strike": float(d.get("short_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": float(sc.get("mid_price") or 0.0),
                "delta_at_entry": float(sc.get("delta") or 0.0),
            },
            {
                "leg_id": f"{sym}_{exp}_Lg{d.get('long_strike')}_{opt_t}",
                "symbol": lc.get("symbol"),
                "long_or_short": "long",
                "option_side": opt_t,
                "strike": float(d.get("long_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": float(lc.get("mid_price") or 0.0),
                "delta_at_entry": float(lc.get("delta") or 0.0),
            },
        ]
        entry_credit = float(d.get("credit_received") or 0.0)

    structural = orb_strategy_to_structural_execution_type(
        strategy_type, direction, spread_type=spread_type
    )
    entry_value = entry_credit
    return {
        "position_type": structural,
        "legacy_position_type": "credit_spread",
        "strategy_name": strategy_name,
        "strategy_type": "orb_0dte",
        "orb_strategy_type": strategy_type or "",
        "underlying_symbol": sym,
        "expiration_ymd": exp,
        "entry_value": entry_value,
        "entry_debit": None,
        "entry_credit": entry_credit,
        "current_value": entry_value,
        "legs": legs,
        "delta_at_entry": float(legs[0].get("delta_at_entry") or 0.0) if legs else 0.0,
        "trigger_direction": trigger_direction or direction,
        "setup_type": setup_type or strategy_type,
        "source_path": source_path,
        "trade_id": trade_id,
    }


def build_normalized_metadata_single_leg(
    *,
    trade_id: str,
    contract: Union["OptionContract", Dict[str, Any]],
    quantity: int,
    strategy_type: str = "",
    direction: str = "",
    spread_type: str = "single_leg",
    strategy_name: str = "Easy ORB 0DTE",
    source_path: str = "easy0DTE",
    setup_type: str = "",
    trigger_direction: str = "",
) -> Dict[str, Any]:
    if hasattr(contract, "symbol"):
        sym = contract.symbol
        exp = expiry_to_ymd(contract.expiry)
        opt_side = str(contract.option_type or "").lower()
        strike = float(contract.strike or 0.0)
        prem = float(contract.mid_price or 0.0)
        delta = float(contract.delta or 0.0)
    else:
        c = contract if isinstance(contract, dict) else {}
        sym = str(c.get("symbol") or "")
        exp = expiry_to_ymd(str(c.get("expiry") or ""))
        opt_side = str(c.get("option_type") or "").lower()
        strike = float(c.get("strike") or 0.0)
        prem = float(c.get("mid_price") or 0.0)
        delta = float(c.get("delta") or 0.0)

    structural = orb_strategy_to_structural_execution_type(
        strategy_type, direction, spread_type=spread_type
    )
    if structural not in (SINGLE_LEG_LONG_CALL, SINGLE_LEG_LONG_PUT):
        structural = SINGLE_LEG_LONG_PUT if opt_side == "put" else SINGLE_LEG_LONG_CALL

    leg = {
        "leg_id": f"{sym}_{exp}_{strike}_{opt_side}",
        "symbol": sym or None,
        "long_or_short": "long",
        "option_side": opt_side,
        "strike": strike,
        "quantity": int(max(1, quantity)),
        "entry_price": prem,
        "delta_at_entry": delta,
    }
    st_low = (strategy_type or "").lower()
    legacy = "single_leg" if st_low == "lotto" else ("long_call" if st_low == "long_call" else ("long_put" if st_low == "long_put" else "single_leg"))
    return {
        "position_type": structural,
        "legacy_position_type": legacy,
        "strategy_name": strategy_name,
        "strategy_type": "orb_0dte",
        "orb_strategy_type": strategy_type or "",
        "underlying_symbol": sym,
        "expiration_ymd": exp,
        "entry_value": prem,
        "entry_debit": prem,
        "entry_credit": None,
        "current_value": prem,
        "legs": [leg],
        "delta_at_entry": delta,
        "trigger_direction": trigger_direction or direction,
        "setup_type": setup_type or strategy_type,
        "source_path": source_path,
        "trade_id": trade_id,
    }


def log_metadata_normalized(trade_id: str, position_type: str, symbol: str) -> None:
    log.info(
        "OPTIONS_EXECUTOR | stage=metadata_normalized | trade_id=%s | symbol=%s | position_type=%s",
        trade_id,
        symbol,
        position_type,
    )


def log_position_type_normalized(trade_id: str, structural: str, legacy: str) -> None:
    log.info(
        "OPTIONS_EXECUTOR | stage=position_type_normalized | trade_id=%s | structural=%s | legacy=%s",
        trade_id,
        structural,
        legacy,
    )


def build_trendline_normalized_metadata(
    *,
    trade_id: str,
    underlying_symbol: str,
    expiration_ymd: str,
    option_side: str,
    strike: float,
    quantity: int,
    entry_premium_per_contract: float,
    delta_at_entry: float,
    setup_type: Optional[str] = None,
    trigger_direction: Optional[str] = None,
    strategy_name: str = "easyTrendline_0DTE",
    strategy_type: str = "trendline_0dte",
    source_path: str = "easyTrendline",
) -> Dict[str, Any]:
    """Shared schema for Trendline 0DTE single-leg executions (aligned with ORB field names)."""
    side = (option_side or "").lower()
    structural = SINGLE_LEG_LONG_PUT if side == "put" else SINGLE_LEG_LONG_CALL
    exp = expiry_to_ymd(expiration_ymd)
    qty = int(max(1, quantity))
    leg = {
        "leg_id": f"{underlying_symbol}_{exp}_{strike}_{side}",
        "symbol": underlying_symbol,
        "long_or_short": "long",
        "option_side": side,
        "strike": float(strike),
        "quantity": qty,
        "entry_price": float(entry_premium_per_contract),
        "delta_at_entry": float(delta_at_entry),
    }
    return {
        "position_type": structural,
        "legacy_position_type": "trendline_single_leg",
        "strategy_name": strategy_name,
        "strategy_type": strategy_type,
        "orb_strategy_type": "",
        "underlying_symbol": underlying_symbol,
        "expiration_ymd": exp,
        "entry_value": float(entry_premium_per_contract),
        "entry_debit": float(entry_premium_per_contract),
        "entry_credit": None,
        "current_value": float(entry_premium_per_contract),
        "legs": [leg],
        "delta_at_entry": float(delta_at_entry),
        "trigger_direction": trigger_direction or "",
        "setup_type": setup_type or "",
        "source_path": source_path,
        "trade_id": trade_id,
    }
