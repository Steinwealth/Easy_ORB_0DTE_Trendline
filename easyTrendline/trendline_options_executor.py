#!/usr/bin/env python3
"""
Dedicated options executor for Trendline strategy path.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from easy0DTE.modules.options_execution_normalize import (
    build_trendline_normalized_metadata,
    log_metadata_normalized,
    log_position_type_normalized,
    normalize_execution_result,
    validate_normalized_options_for_stealth,
)

from .trendline_account_manager import TrendlineAccountManager, TrendlinePosition
from .trendline_models import (
    TrendlineConfig,
    TrendlineDirection,
    TrendlineOptionSelectionConfig,
    TrendlineTradeResult,
    TrendlineTradeSignal,
)

log = logging.getLogger(__name__)


def _expiry_ymd_trendline() -> str:
    """US session calendar day for 0DTE chain lookup (E*TRADE YYYYMMDD)."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y%m%d")


@dataclass
class _ContractPick:
    strike: float
    delta_est: float
    premium: float
    otm_steps: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None


class TrendlineOptionsExecutor:
    """
    Consume TrendlineTradeSignal and create execution payload/results.

    Demo-first implementation with delta / OTM-aware contract selection (0DTE intent).
    """

    def __init__(
        self,
        account_manager: TrendlineAccountManager,
        demo_mode: Optional[bool] = None,
        option_config: Optional[TrendlineOptionSelectionConfig] = None,
        option_quote_api: Optional[Any] = None,
        require_live_chain_data: bool = True,
        trendline_signal_config: Optional[TrendlineConfig] = None,
    ) -> None:
        self.account_manager = account_manager
        self.demo_mode = (
            demo_mode
            if demo_mode is not None
            else os.getenv("ETRADE_MODE", "demo").lower() == "demo"
        )
        self.max_position_pct = float(os.getenv("TRENDLINE_MAX_POSITION_PCT", "0.12"))
        self.option_config = option_config or TrendlineOptionSelectionConfig()
        self.trendline_signal_config = trendline_signal_config
        self.option_quote_api = option_quote_api
        self.require_live_chain_data = bool(require_live_chain_data)
        self.options_chain_cache: Dict[str, Dict[str, Any]] = {}
        self._executed_trade_ids: set[str] = set()
        self._executed_candidate_ids: set[str] = set()
        try:
            self.delta_tolerance = max(0.0, float(os.getenv("TRENDLINE_DELTA_TOLERANCE", "0.02")))
        except ValueError:
            self.delta_tolerance = 0.02

    def _otm_steps_deterministic(self, symbol: str) -> int:
        cfg = self.option_config
        if cfg.strike_mode != "otm_1_to_2":
            return 1
        h = sum(ord(c) for c in symbol.upper()) % 2
        if cfg.lotto_mode:
            return 2 if h == 0 else 1
        return 1 + h

    def _strike_for_side(self, spot: float, option_side: str, otm_steps: int) -> float:
        inc = 1.0
        side = str(option_side).lower()
        if side == "call":
            base = math.ceil(spot / inc) * inc
            return base + (otm_steps - 1) * inc
        base = math.floor(spot / inc) * inc
        return base - (otm_steps - 1) * inc

    def _estimate_delta(self, spot: float, strike: float, option_side: str, otm_steps: int) -> float:
        """Heuristic 0DTE delta proxy (not a pricing model)."""
        side = str(option_side).lower()
        if spot <= 0:
            return 0.25
        if side == "call":
            m = (spot - strike) / spot
        else:
            m = (strike - spot) / spot
        d = 0.45 + 2.2 * m - 0.04 * otm_steps
        return max(0.08, min(0.55, d))

    @staticmethod
    def _break_geometry_aligns_signal(signal: TrendlineTradeSignal) -> bool:
        be = signal.break_event
        close_p = float(be.close_price or 0.0)
        line_p = float(be.trendline_price or 0.0)
        if signal.direction == TrendlineDirection.BULL:
            return close_p < line_p
        return close_p > line_p

    def _estimate_premium(self, spot: float, delta: float, otm_steps: int) -> float:
        """Conservative per-share premium for demo sizing."""
        base = 0.08 + 0.25 * max(0.0, 0.42 - delta) + 0.02 * otm_steps
        return max(0.03, round(base + spot * 0.00015, 2))

    def select_contract(
        self,
        signal: TrendlineTradeSignal,
        underlying_spot: float,
        slot_capital: float,
        chain_hint: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[_ContractPick], Optional[str]]:
        """
        Pick 0DTE long call/put consistent with delta band and OTM mode.
        chain_hint may include bid, ask, openInterest, volume for rejection checks.
        """
        cfg = self.option_config
        side = str(signal.option_side or "").lower()
        otm = self._otm_steps_deterministic(signal.symbol)
        strike = self._strike_for_side(underlying_spot, side, otm)
        delta_est = self._estimate_delta(underlying_spot, strike, side, otm)
        live_chain_selected = False
        if chain_hint:
            try:
                strike_hint = float(chain_hint.get("strike")) if chain_hint.get("strike") is not None else None
            except (TypeError, ValueError):
                strike_hint = None
            try:
                delta_hint = float(chain_hint.get("delta")) if chain_hint.get("delta") is not None else None
            except (TypeError, ValueError):
                delta_hint = None
            if strike_hint is not None and delta_hint is not None:
                strike = strike_hint
                delta_est = abs(delta_hint)
                live_chain_selected = True
        max_delta_with_tolerance = cfg.delta_max + self.delta_tolerance
        if delta_est < cfg.delta_min - 1e-6 or delta_est > max_delta_with_tolerance + 1e-6:
            alt_otm = 2 if otm == 1 else 1
            strike2 = self._strike_for_side(underlying_spot, signal.option_side, alt_otm)
            d2 = self._estimate_delta(underlying_spot, strike2, signal.option_side, alt_otm)
            if cfg.delta_min <= d2 <= max_delta_with_tolerance:
                otm, strike, delta_est = alt_otm, strike2, d2
            else:
                log.warning(
                    "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=delta_out_of_band | delta=%.4f",
                    signal.symbol,
                    delta_est,
                )
                return None, "delta_out_of_band"
        if cfg.delta_max + 1e-6 < delta_est <= max_delta_with_tolerance + 1e-6:
            log.info(
                "TRENDLINE_DELTA_TOLERANCE_USED | symbol=%s | delta=%.4f | max=%.4f | tolerance=%.4f",
                signal.symbol,
                delta_est,
                cfg.delta_max,
                self.delta_tolerance,
            )

        prem = self._estimate_premium(underlying_spot, delta_est, otm)
        bid = ask = oi = vol = None
        if chain_hint:
            bid = chain_hint.get("bid")
            ask = chain_hint.get("ask")
            oi = chain_hint.get("openInterest") or chain_hint.get("open_interest")
            vol = chain_hint.get("volume")
            if chain_hint.get("premium") is not None:
                try:
                    prem = max(0.01, float(chain_hint.get("premium")))
                    live_chain_selected = True
                except (TypeError, ValueError):
                    pass
        if bid is not None and ask is not None:
            mid = (float(bid) + float(ask)) / 2.0
            if mid > 0 and (float(ask) - float(bid)) / mid > cfg.max_bid_ask_spread_pct:
                spr_pct = (float(ask) - float(bid)) / mid
                log.warning(
                    "TRENDLINE_OPTION_REJECT_ILLIQUID | symbol=%s | reason=spread_too_wide | spread_pct=%.5f | max_spread_pct=%.5f",
                    signal.symbol,
                    spr_pct,
                    cfg.max_bid_ask_spread_pct,
                )
                log.warning(
                    "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=spread_too_wide",
                    signal.symbol,
                )
                return None, "spread_too_wide"
            prem = max(prem, float(ask))
        if cfg.min_open_interest > 0 and oi is not None and int(oi) < cfg.min_open_interest:
            log.warning(
                "TRENDLINE_OPTION_REJECT_ILLIQUID | symbol=%s | reason=low_open_interest | open_interest=%s | min_open_interest=%d",
                signal.symbol,
                str(oi),
                cfg.min_open_interest,
            )
            log.warning(
                "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=low_open_interest",
                signal.symbol,
            )
            return None, "low_open_interest"
        if cfg.min_volume > 0 and vol is not None and int(vol) < cfg.min_volume:
            log.warning(
                "TRENDLINE_OPTION_REJECT_ILLIQUID | symbol=%s | reason=low_volume | volume=%s | min_volume=%d",
                signal.symbol,
                str(vol),
                cfg.min_volume,
            )
            log.warning(
                "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=low_volume",
                signal.symbol,
            )
            return None, "low_volume"

        est_cost = prem * 100.0
        if slot_capital > 0 and est_cost > slot_capital * 1.02:
            log.warning(
                "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=contract_too_expensive_for_slot | est=%.2f cap=%.2f",
                signal.symbol,
                est_cost,
                slot_capital,
            )
            return None, "contract_too_expensive_for_slot"

        pick = _ContractPick(
            strike=strike,
            delta_est=delta_est,
            premium=prem,
            otm_steps=otm,
            bid=bid if bid is not None else None,
            ask=ask if ask is not None else None,
            open_interest=int(oi) if oi is not None else None,
            volume=int(vol) if vol is not None else None,
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=contract_selected | symbol=%s | option_side=%s | strike=%.2f | delta=%.3f | premium=%.4f | otm_steps=%d | source=%s",
            signal.symbol,
            signal.option_side,
            strike,
            delta_est,
            prem,
            otm,
            "live_chain" if live_chain_selected else "estimated",
        )
        log.warning(
            "TRENDLINE_OPTION_SELECTION | symbol=%s | option_side=%s | strike=%.2f | delta=%.3f | premium=%.4f | otm_steps=%d | source=%s",
            signal.symbol,
            signal.option_side,
            strike,
            delta_est,
            prem,
            otm,
            "live_chain" if live_chain_selected else "estimated",
        )
        return pick, None

    async def _fetch_live_chain_hint(
        self,
        signal: TrendlineTradeSignal,
        underlying_spot: float,
    ) -> Optional[Dict[str, Any]]:
        """Select a concrete contract from live chain for Trendline execution."""
        api = self.option_quote_api
        if api is None or not hasattr(api, "fetch_options_chain"):
            return None
        side = str(signal.option_side or "").lower()
        if side not in ("call", "put"):
            return None
        expiry = _expiry_ymd_trendline()
        try:
            chain = await api.fetch_options_chain(
                symbol=signal.symbol,
                expiry=expiry,
                strike_count=20,
                include_greeks=True,
            )
        except Exception as exc:
            log.debug("TRENDLINE_PIPELINE | stage=live_chain_fetch | symbol=%s | err=%s", signal.symbol, exc)
            return None
        if not isinstance(chain, dict):
            return None
        contracts = chain.get("calls" if side == "call" else "puts", [])
        if not isinstance(contracts, list) or not contracts:
            return None

        cfg = self.option_config
        valid: list[tuple[float, Any]] = []
        for c in contracts:
            try:
                strike = float(getattr(c, "strike", 0.0) or 0.0)
                delta = abs(float(getattr(c, "delta", 0.0) or 0.0))
                bid = float(getattr(c, "bid", 0.0) or 0.0)
                ask = float(getattr(c, "ask", 0.0) or 0.0)
                oi = int(getattr(c, "open_interest", 0) or 0)
                vol = int(getattr(c, "volume", 0) or 0)
            except (TypeError, ValueError):
                continue
            if strike <= 0 or delta <= 0:
                continue
            if cfg.min_open_interest > 0 and oi < cfg.min_open_interest:
                continue
            if cfg.min_volume > 0 and vol < cfg.min_volume:
                continue
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                if mid > 0 and ((ask - bid) / mid) > cfg.max_bid_ask_spread_pct:
                    continue
            if side == "call" and strike < underlying_spot:
                continue
            if side == "put" and strike > underlying_spot:
                continue
            delta_penalty = 0.0
            if delta < cfg.delta_min:
                delta_penalty = cfg.delta_min - delta
            elif delta > cfg.delta_max:
                delta_penalty = delta - cfg.delta_max
            dist_penalty = abs(strike - underlying_spot) / max(underlying_spot, 1e-9)
            score = delta_penalty * 10.0 + dist_penalty
            valid.append((score, c))

        if not valid:
            return None
        valid.sort(key=lambda x: x[0])
        best = valid[0][1]
        bid = float(getattr(best, "bid", 0.0) or 0.0)
        ask = float(getattr(best, "ask", 0.0) or 0.0)
        mid = float(getattr(best, "mid_price", 0.0) or 0.0)
        premium = ask if ask > 0 else (mid if mid > 0 else max(0.01, bid))
        strike = float(getattr(best, "strike", 0.0) or 0.0)
        delta = abs(float(getattr(best, "delta", 0.0) or 0.0))
        otm_steps = max(1, int(round(abs(strike - underlying_spot) / 1.0)))
        return {
            "strike": strike,
            "delta": delta,
            "premium": premium,
            "bid": bid,
            "ask": ask,
            "openInterest": int(getattr(best, "open_interest", 0) or 0),
            "volume": int(getattr(best, "volume", 0) or 0),
            "otm_steps": otm_steps,
            "source": "live_chain",
        }

    def _trendline_dict(self, signal: TrendlineTradeSignal) -> Dict[str, Any]:
        tl = signal.trendline
        return {
            "slope_per_second": tl.slope_per_second,
            "intercept": tl.intercept,
            "direction": tl.direction.value,
            "symbol": tl.symbol,
        }

    def build_execution_payload(
        self,
        signal: TrendlineTradeSignal,
        slot_index: int = 0,
        slot_capital: Optional[float] = None,
        trendline_daily_trade_number: int = 0,
        contract: Optional[_ContractPick] = None,
    ) -> Dict[str, object]:
        # Trendline slot-based sizing: when a slot cap is provided by the orchestrator,
        # use that slot budget directly so sizing does not shrink with cash-on-hand after fills.
        if slot_capital is not None:
            budget = max(0.0, float(slot_capital))
        else:
            budget = self.account_manager.account_balance * self.max_position_pct
        size_mult = 1.0
        try:
            md = signal.metadata if isinstance(signal.metadata, dict) else {}
            size_mult = float(md.get("entry_size_multiplier") or 1.0)
        except (TypeError, ValueError):
            size_mult = 1.0
        size_mult = max(0.10, min(1.00, size_mult))
        budget *= size_mult
        unit_price = float(contract.premium) if contract else self._fallback_unit_price(signal)
        qty = max(1, int(budget // max(unit_price * 100.0, 1.0)))
        total_cost = qty * unit_price * 100.0

        meta = dict(signal.metadata or {})
        exp_ymd = _expiry_ymd_trendline()
        side = str(signal.option_side or "").lower()
        payload: Dict[str, object] = {
            "symbol": signal.symbol,
            "underlying_symbol": signal.symbol,
            "direction": signal.direction.value,
            "option_side": signal.option_side,
            "strategy": "trendline_0dte",
            "strategy_name": "easyTrendline_0DTE",
            "strategy_type": "trendline_0dte",
            "entry_type": "trendline_break",
            "source_path": "trendline_0dte",
            "trigger_type": "trendline_break_momentum",
            "signal_time": signal.emitted_at.isoformat(),
            "estimated_contract_price": unit_price,
            "quantity": qty,
            "estimated_total_cost": total_cost,
            "slot_index": slot_index,
            "slot_capital": budget,
            "trendline_daily_trade_number": trendline_daily_trade_number,
            "setup_type": meta.get("setup_type"),
            "trigger_direction": meta.get("trigger_direction"),
            "expected_option_side": signal.option_side,
            "trendline_structure_source": meta.get("trendline_structure_source", "pre_730_price_action"),
            "trendline_for_exit": self._trendline_dict(signal),
            "dte": 0,
            "expiry_ymd": exp_ymd,
            "expiration_ymd": exp_ymd,
            "meta": meta,
            # Per-contract tradable value (ORB-aligned semantics); multiplies by qty × 100 for dollars.
            "entry_value": float(unit_price),
            "entry_debit": float(unit_price),
            "entry_credit": None,
            "current_value": float(unit_price),
            "position_type": "single_leg_long_put" if side == "put" else "single_leg_long_call",
            "trade_id": "",
        }
        payload["position_type"] = payload.get(
            "position_type",
            "single_leg_long_put" if side == "put" else "single_leg_long_call",
        )
        if contract:
            payload["strike"] = contract.strike
            payload["delta_at_entry"] = contract.delta_est
            payload["otm_steps"] = contract.otm_steps
            payload["position_type"] = "single_leg_long_put" if side == "put" else "single_leg_long_call"
            payload["entry_value"] = float(contract.premium)
            payload["entry_debit"] = float(contract.premium)
            payload["current_value"] = float(contract.premium)
            payload["legs"] = [
                {
                    "leg_id": f"{signal.symbol}_{exp_ymd}_{contract.strike}_{side}",
                    "symbol": signal.symbol,
                    "long_or_short": "long",
                    "option_side": side,
                    "strike": float(contract.strike),
                    "quantity": int(qty),
                    "entry_price": float(contract.premium),
                    "delta_at_entry": float(contract.delta_est),
                }
            ]
        return payload

    @staticmethod
    def _fallback_unit_price(signal: TrendlineTradeSignal) -> float:
        base = 0.35
        confidence_boost = min(0.35, max(0.0, signal.confidence - 0.5))
        return round(base + confidence_boost, 2)

    async def execute(
        self,
        signal: TrendlineTradeSignal,
        *,
        slot_index: int = 0,
        slot_capital: Optional[float] = None,
        trendline_daily_trade_number: int = 0,
        chain_hint: Optional[Dict[str, Any]] = None,
    ) -> TrendlineTradeResult:
        """Execute signal through demo ledger now; live hook reserved for next pass."""
        now = datetime.now(timezone.utc)
        cfg_tl = self.trendline_signal_config
        if cfg_tl is not None:
            aligned = self._break_geometry_aligns_signal(signal)
            if not aligned:
                meta = signal.metadata or {}
                bd = float(meta.get("break_distance") or 0.0)
                br = float(meta.get("body_ratio") or 0.0)
                exec_reason = str(meta.get("execution_reason") or "").strip().lower()
                catastrophic_reversal = bool(
                    str(meta.get("catastrophic_reversal") or "").strip().lower() == "true"
                )
                impossible_geometry = abs(float(getattr(signal.break_event, "trendline_price", 0.0) or 0.0)) <= 0.0
                if exec_reason == "executed_valid_confirmation" and not catastrophic_reversal and not impossible_geometry:
                    log.warning(
                        "TRENDLINE_EXECUTOR_DIRECTION_ADVISORY | symbol=%s | trade_id=%s | break_distance=%.6f | "
                        "body_ratio=%.4f | execution_reason=%s | action=continue",
                        signal.symbol,
                        str(meta.get("candidate_id") or ""),
                        bd,
                        br,
                        exec_reason,
                    )
                elif bd >= float(cfg_tl.direction_override_strong_break_threshold) and br >= float(
                    cfg_tl.direction_override_strong_body_ratio
                ):
                    log.warning(
                        "TRENDLINE_DIRECTION_OVERRIDE | symbol=%s | trade_id=%s | break_distance=%.6f | body_ratio=%.4f | "
                        "thresholds_break=%.6f | thresholds_body=%.4f",
                        signal.symbol,
                        str(meta.get("candidate_id") or ""),
                        bd,
                        br,
                        float(cfg_tl.direction_override_strong_break_threshold),
                        float(cfg_tl.direction_override_strong_body_ratio),
                    )
                else:
                    log.warning(
                        "TRENDLINE_DIRECTION_REJECT | symbol=%s | trade_id=%s | break_distance=%.6f | body_ratio=%.4f",
                        signal.symbol,
                        str(meta.get("candidate_id") or ""),
                        bd,
                        br,
                    )
                    log.warning(
                        "TRENDLINE_HARD_VETO_AUDIT | symbol=%s | stage=executor_direction | veto_reason=direction_mismatch | "
                        "catastrophic=%s | stale=false | severe_chop=false | invalid_structure=false | liquidity_block=false | executor_called=true",
                        signal.symbol,
                        str(bool(catastrophic_reversal or impossible_geometry)).lower(),
                    )
                    return TrendlineTradeResult(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        success=False,
                        executed_at=now,
                        signal_time=signal.emitted_at,
                        account_mode="demo" if self.demo_mode else "live",
                        order_payload={"reject_reason": "direction_mismatch"},
                        error="direction_mismatch",
                    )
        spot = float(signal.break_event.close_price)
        # Keep selection budget consistent with payload sizing.
        if slot_capital is not None:
            budget = max(0.0, float(slot_capital))
        else:
            budget = self.account_manager.account_balance * self.max_position_pct

        chain_source = "preloaded"
        if chain_hint is None:
            chain_source = "live_retry"
            for attempt in range(3):
                chain_hint = await self._fetch_live_chain_hint(signal, spot)
                if chain_hint:
                    self.options_chain_cache[signal.symbol] = dict(chain_hint)
                    break
                log.warning(
                    "TRENDLINE_CHAIN_RETRY | symbol=%s | attempt=%d | wait_sec=%.2f",
                    signal.symbol,
                    attempt + 1,
                    0.5 * (attempt + 1),
                )
                await asyncio.sleep(0.5 * (attempt + 1))
            if chain_hint is None:
                cached = self.options_chain_cache.get(signal.symbol)
                if cached:
                    chain_hint = dict(cached)
                    chain_source = "cache"
                    log.warning("TRENDLINE_CHAIN_CACHE_USED | symbol=%s", signal.symbol)
        if chain_hint is None and self.require_live_chain_data:
            if self.demo_mode:
                chain_source = "demo_synthetic"
                log.warning(
                    "TRENDLINE_CHAIN_FALLBACK | symbol=%s | using synthetic contract",
                    signal.symbol,
                )
            else:
                log.error("TRENDLINE_EXECUTION_BLOCKED | symbol=%s", signal.symbol)
                log.warning(
                    "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=live_chain_required_unavailable",
                    signal.symbol,
                )
                return TrendlineTradeResult(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    success=False,
                    executed_at=now,
                    signal_time=signal.emitted_at,
                    account_mode="demo" if self.demo_mode else "live",
                    order_payload={"reject_reason": "live_chain_required_unavailable"},
                    error="live_chain_required_unavailable",
                )
        log.warning(
            "TRENDLINE_EXECUTION_CONTINUED | symbol=%s | chain_source=%s",
            signal.symbol,
            chain_source,
        )
        pick, reject = self.select_contract(signal, spot, budget, chain_hint=chain_hint)
        if pick is None:
            log.warning(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=%s | spot=%.4f",
                signal.symbol,
                reject or "contract_selection_failed",
                spot,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload={"reject_reason": reject},
                error=reject or "contract_selection_failed",
            )

        payload = self.build_execution_payload(
            signal,
            slot_index=slot_index,
            slot_capital=slot_capital,
            trendline_daily_trade_number=trendline_daily_trade_number,
            contract=pick,
        )
        payload["entry_underlying_price"] = spot

        if not self.demo_mode:
            log.warning(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=live_execution_not_wired",
                signal.symbol,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="live",
                order_payload=payload,
                error="live_execution_not_wired",
            )

        slot_cap = float(payload.get("slot_capital", 0.0) or 0.0)
        est_contract = float(payload.get("estimated_contract_price", 0.0) or 0.0) * 100.0
        if slot_cap > 0 and est_contract > slot_cap:
            log.warning(
                "TRENDLINE_PIPELINE | stage=sizing_rejected | symbol=%s | reason=slot_capital_below_min_contract | slot_capital=%.2f | contract_cost=%.2f",
                signal.symbol,
                slot_cap,
                est_contract,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo",
                order_payload=payload,
                error="execution_skipped_due_to_slot_sizing",
            )
        if float(payload.get("estimated_total_cost", 0.0) or 0.0) <= 0:
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo",
                order_payload=payload,
                error="execution_skipped_due_to_slot_sizing",
            )

        position_id = self._new_position_id(signal.symbol)
        candidate_id = str(signal.metadata.get("candidate_id", "") or "")
        if candidate_id and candidate_id in self._executed_candidate_ids:
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo",
                order_payload=payload,
                error="candidate_already_executed",
            )
        if position_id in self._executed_trade_ids:
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo",
                order_payload=payload,
                error="trade_id_collision",
            )
        payload["trade_id"] = position_id
        norm = normalize_execution_result(build_trendline_normalized_metadata(
            trade_id=position_id,
            underlying_symbol=signal.symbol,
            expiration_ymd=str(payload.get("expiry_ymd") or ""),
            option_side=str(signal.option_side),
            strike=float(pick.strike),
            quantity=int(payload["quantity"]),
            entry_premium_per_contract=float(pick.premium),
            delta_at_entry=float(pick.delta_est),
            setup_type=(signal.metadata or {}).get("setup_type"),
            trigger_direction=(signal.metadata or {}).get("trigger_direction"),
            source_path="trendline_0dte",
        ))
        norm_ok, norm_missing = validate_normalized_options_for_stealth(norm)
        if not norm_ok:
            log.warning(
                "OPTIONS_STEALTH_FAILSAFE | stage=trendline_normalized_invalid | trade_id=%s | missing=%s",
                position_id,
                ",".join(norm_missing) if norm_missing else "unknown",
            )
        log_position_type_normalized(
            position_id, norm["position_type"], norm.get("legacy_position_type", "trendline")
        )
        log_metadata_normalized(position_id, norm["position_type"], signal.symbol)
        pos = TrendlinePosition(
            position_id=position_id,
            symbol=signal.symbol,
            direction=signal.direction,
            option_side=signal.option_side,
            quantity=int(payload["quantity"]),
            entry_cost=float(payload["estimated_total_cost"]),
            metadata={
                "execution_payload": payload,
                "normalized_options": norm,
                "signal_snapshot": {
                    "breakout_confirmed": True,
                    "entry_underlying_price": spot,
                    "break_timestamp": signal.break_event.candle_ts.isoformat(),
                    "option_side": signal.option_side,
                    "direction": signal.direction.value,
                    "setup_type": (signal.metadata or {}).get("setup_type"),
                    "trigger_direction": (signal.metadata or {}).get("trigger_direction"),
                    "trendline_structure_source": (signal.metadata or {}).get(
                        "trendline_structure_source", "pre_730_price_action"
                    ),
                    "strike": pick.strike,
                    "delta_at_entry": pick.delta_est,
                    "entry_premium_per_contract": pick.premium,
                    "expiry_ymd": payload.get("expiry_ymd"),
                    "entry_value": float(pick.premium),
                    "current_value": float(pick.premium),
                },
            },
        )
        opened = self.account_manager.open_position(pos)

        if not opened:
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo",
                order_payload=payload,
                error="insufficient_balance_or_invalid_cost",
            )

        self._executed_trade_ids.add(position_id)
        if candidate_id:
            self._executed_candidate_ids.add(candidate_id)
        log.warning(
            "TRENDLINE_PIPELINE | stage=execution | action=filled | symbol=%s | trade_id=%s",
            signal.symbol,
            position_id,
        )
        return TrendlineTradeResult(
            symbol=signal.symbol,
            direction=signal.direction,
            success=True,
            executed_at=now,
            signal_time=signal.emitted_at,
            account_mode="demo",
            position_id=position_id,
            order_payload=payload,
            details={
                "status": "simulated_filled",
                "entry_cost": float(payload["estimated_total_cost"]),
                "entry_contract_price": float(payload["estimated_contract_price"]),
                "quantity": int(payload["quantity"]),
            },
        )

    @staticmethod
    def _new_position_id(symbol: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%y%m%d_%H%M%S_%f")[-12:]
        return f"TL_{symbol}_{ts}"
