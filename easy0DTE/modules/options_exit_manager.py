#!/usr/bin/env python3
"""
Options Exit Manager
====================

Comprehensive exit management for 0DTE Strategy options positions.
Implements multi-layered exit framework for maximum profit capture and capital protection.

Exit Framework:
1. Hard Stop (Primary Protection)
2. Underlying Invalidation Stop (Structural Stop)
3. Time Stop (Silent Killer Prevention)
4. Absolute Fail-Safe (Rare but Mandatory)

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import Enum

from .options_types import OptionsPosition

log = logging.getLogger(__name__)


class ExitReason(Enum):
    """Exit reason types"""
    HARD_STOP = "hard_stop"
    INVALIDATION_STOP = "invalidation_stop"
    TIME_STOP = "time_stop"
    FAIL_SAFE = "fail_safe"
    PROFIT_TARGET = "profit_target"
    RUNNER_TARGET = "runner_target"
    EOD_CLOSE = "eod_close"
    HEALTH_EMERGENCY = "health_emergency"


@dataclass
class ExitSignal:
    """Exit signal for options position"""
    position_id: str
    reason: ExitReason
    exit_price: float
    exit_time: datetime
    pnl_pct: float
    pnl_dollar: float
    details: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'position_id': self.position_id,
            'reason': self.reason.value,
            'exit_price': self.exit_price,
            'exit_time': self.exit_time.isoformat(),
            'pnl_pct': self.pnl_pct,
            'pnl_dollar': self.pnl_dollar,
            'details': self.details
        }


class OptionsExitManager:
    """
    Options Exit Manager for 0DTE Strategy
    
    Implements comprehensive exit framework:
    - Hard stops (premium-based)
    - Underlying invalidation stops (structural)
    - Time stops (theta decay prevention)
    - Absolute fail-safe (emergency exits)
    """
    
    def __init__(
        self,
        # Debit Spread Stops
        debit_spread_hard_stop_pct: float = -0.45,  # -45% of premium
        debit_spread_time_stop_minutes: int = 25,  # 20-30 minutes
        debit_spread_fail_safe_pct: float = -0.60,  # -60% absolute fail-safe
        
        # Credit Spread Stops
        credit_spread_hard_stop_pct: float = -0.50,  # -50% of credit received
        credit_spread_time_stop_minutes: int = 25,  # 20-30 minutes
        credit_spread_fail_safe_pct: float = -0.60,  # -60% absolute fail-safe
        
        # Lotto Sleeve Stops
        lotto_hard_stop_pct: float = -0.55,  # -55% of premium
        lotto_time_stop_minutes: int = 12,  # 10-15 minutes
        lotto_fail_safe_pct: float = -0.60,  # -60% absolute fail-safe
        
        # Profit Targets (Automated Exits)
        first_profit_target_pct: float = 0.60,  # +60% → sell 50%
        first_profit_sell_pct: float = 0.50,  # Sell 50% at first target
        second_profit_target_pct: float = 1.20,  # +120% → sell 25%
        second_profit_sell_pct: float = 0.25,  # Sell 25% at second target
        runner_profit_pct: float = 2.0,  # 2x for runners (legacy, kept for compatibility)
        partial_profit_pct: float = 0.50,  # Legacy: Partial profit percentage (0.50 = 50%)
        
        # Invalidation Checks
        require_vwap_data: bool = True,
        require_orb_data: bool = True
    ):
        """
        Initialize Options Exit Manager
        
        Args:
            debit_spread_hard_stop_pct: Hard stop percentage for debit spreads (-0.45 = -45%)
            debit_spread_time_stop_minutes: Time stop minutes for debit spreads (25 minutes)
            debit_spread_fail_safe_pct: Fail-safe percentage for debit spreads (-0.60 = -60%)
            lotto_hard_stop_pct: Hard stop percentage for lottos (-0.55 = -55%)
            lotto_time_stop_minutes: Time stop minutes for lottos (12 minutes)
            lotto_fail_safe_pct: Fail-safe percentage for lottos (-0.60 = -60%)
            partial_profit_pct: Partial profit percentage (0.50 = 50%)
            runner_profit_pct: Runner profit multiplier (2.0 = 2x)
            require_vwap_data: Require VWAP data for invalidation checks
            require_orb_data: Require ORB data for invalidation checks
        """
        # Debit Spread Stops
        self.debit_spread_hard_stop_pct = debit_spread_hard_stop_pct
        self.debit_spread_time_stop_minutes = debit_spread_time_stop_minutes
        self.debit_spread_fail_safe_pct = debit_spread_fail_safe_pct
        
        # Credit Spread Stops
        self.credit_spread_hard_stop_pct = credit_spread_hard_stop_pct
        self.credit_spread_time_stop_minutes = credit_spread_time_stop_minutes
        self.credit_spread_fail_safe_pct = credit_spread_fail_safe_pct
        
        # Lotto Sleeve Stops
        self.lotto_hard_stop_pct = lotto_hard_stop_pct
        self.lotto_time_stop_minutes = lotto_time_stop_minutes
        self.lotto_fail_safe_pct = lotto_fail_safe_pct
        
        # Profit Targets (Automated Exits)
        self.first_profit_target_pct = first_profit_target_pct
        self.first_profit_sell_pct = first_profit_sell_pct
        self.second_profit_target_pct = second_profit_target_pct
        self.second_profit_sell_pct = second_profit_sell_pct
        self.runner_profit_pct = runner_profit_pct  # Legacy, kept for compatibility
        self.partial_profit_pct = partial_profit_pct  # Legacy, kept for compatibility
        
        # Invalidation Checks
        self.require_vwap_data = require_vwap_data
        self.require_orb_data = require_orb_data
        
        log.info(f"Options Exit Manager initialized:")
        log.info(f"  Debit Spread Stops:")
        log.info(f"    - Hard stop: {debit_spread_hard_stop_pct*100:.0f}%")
        log.info(f"    - Time stop: {debit_spread_time_stop_minutes} minutes")
        log.info(f"    - Fail-safe: {debit_spread_fail_safe_pct*100:.0f}%")
        log.info(f"  Credit Spread Stops:")
        log.info(f"    - Hard stop: {credit_spread_hard_stop_pct*100:.0f}%")
        log.info(f"    - Time stop: {credit_spread_time_stop_minutes} minutes")
        log.info(f"    - Fail-safe: {credit_spread_fail_safe_pct*100:.0f}%")
        log.info(f"  Lotto Sleeve Stops:")
        log.info(f"    - Hard stop: {lotto_hard_stop_pct*100:.0f}%")
        log.info(f"    - Time stop: {lotto_time_stop_minutes} minutes")
        log.info(f"    - Fail-safe: {lotto_fail_safe_pct*100:.0f}%")
        log.info(f"  Profit Targets (Automated Exits):")
        log.info(f"    - First target: +{first_profit_target_pct*100:.0f}% → sell {first_profit_sell_pct*100:.0f}%")
        log.info(f"    - Second target: +{second_profit_target_pct*100:.0f}% → sell {second_profit_sell_pct*100:.0f}%")
        log.info(f"    - Runner: Trails until VWAP/ORB reclaim or time cutoff")

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _nested_field(obj: Any, field: str, default: Any = None) -> Any:
        """Safely read dataclass-like or dict-like nested fields."""
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(field, default)
        return getattr(obj, field, default)

    def _get_structural_position_type(self, position: OptionsPosition) -> str:
        """
        Prefer normalized structural position type when present.
        Falls back to legacy position_type for backward compatibility.
        """
        try:
            meta = position.metadata if isinstance(position.metadata, dict) else {}
            norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
            structural = str(norm.get("position_type", "") or "").strip().lower()
        except Exception:
            structural = ""

        if structural:
            return structural

        legacy = str(getattr(position, "position_type", "") or "").strip().lower()
        if legacy == "lotto":
            # Legacy lotto maps to single-leg bucket.
            return "single_leg"
        return legacy

    def _compute_pnl_pct(self, position: OptionsPosition, current_value: float) -> float:
        """Compute current pnl pct for structural position type."""
        entry_price = float(getattr(position, "entry_price", 0.0) or 0.0)
        structural_type = self._get_structural_position_type(position)
        is_credit_spread = structural_type == "credit_spread"
        if entry_price <= 0:
            return 0.0
        if is_credit_spread:
            return (entry_price - float(current_value)) / entry_price
        return (float(current_value) - entry_price) / entry_price

    def _update_position_performance(self, position: OptionsPosition, current_value: float) -> None:
        """
        Track max pnl and opportunity flags on every monitoring cycle.
        This is additive metadata used by ORB/stealth monitoring quality logic.
        """
        try:
            if not isinstance(getattr(position, "metadata", None), dict):
                position.metadata = {}
            md = position.metadata
            perf = md.get("exit_performance")
            if not isinstance(perf, dict):
                perf = {}
                md["exit_performance"] = perf

            current_pnl_pct = float(self._compute_pnl_pct(position, current_value))
            prev_max = float(perf.get("max_pnl_pct", current_pnl_pct))
            max_pnl_pct = max(prev_max, current_pnl_pct)
            perf["current_pnl_pct"] = current_pnl_pct
            perf["max_pnl_pct"] = max_pnl_pct

            if max_pnl_pct > prev_max + 1e-9:
                log.info(
                    "OPTION_MAX_PNL_UPDATE | position_id=%s | symbol=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position.position_id,
                    position.symbol,
                    current_pnl_pct,
                    max_pnl_pct,
                )

            had_opportunity = bool(perf.get("had_opportunity", False))
            if (not had_opportunity) and max_pnl_pct >= 0.05:
                perf["had_opportunity"] = True
                log.info(
                    "OPTION_OPPORTUNITY_DETECTED | position_id=%s | symbol=%s | max_pnl_pct=%.4f",
                    position.position_id,
                    position.symbol,
                    max_pnl_pct,
                )
        except Exception as perf_err:
            log.debug("OPTION_PERF_TRACK_SKIP | position_id=%s | reason=%s", getattr(position, "position_id", "unknown"), perf_err)

    def check_hard_stop(
        self,
        position: OptionsPosition,
        current_value: float
    ) -> Optional[ExitSignal]:
        """
        Check hard stop (premium-based)
        
        Args:
            position: Options position
            current_value: Current position value
            
        Returns:
            ExitSignal if hard stop triggered, None otherwise
        """
        entry_price = position.entry_price
        
        structural_type = self._get_structural_position_type(position)
        is_credit_spread = structural_type == "credit_spread"
        is_debit_spread = structural_type == "debit_spread"
        # Calculate P&L based on structural type
        if is_credit_spread:
            # For credit spreads: profit = (entry_price - current_value) / entry_price
            # Entry price = credit received, current_value = cost to close
            # Loss when current_value increases (spread moves against us)
            pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (entry_price - current_value) * position.quantity * 100
        else:
            # For debit spreads and lottos: profit = (current_value - entry_price) / entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (current_value - entry_price) * position.quantity * 100
        
        # Determine stop threshold based on position type
        if is_debit_spread:
            stop_threshold = self.debit_spread_hard_stop_pct
        elif is_credit_spread:
            stop_threshold = self.credit_spread_hard_stop_pct
        else:  # single-leg (legacy lotto/long_call/long_put)
            stop_threshold = self.lotto_hard_stop_pct
        
        # For credit spreads, loss is negative pnl_pct (current_value > entry_price)
        # For debit spreads/lottos, loss is negative pnl_pct (current_value < entry_price)
        if pnl_pct <= stop_threshold:
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.HARD_STOP,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={
                    'entry_price': entry_price,
                    'current_value': current_value,
                    'stop_threshold_pct': stop_threshold * 100,
                    'position_type': position.position_type,
                    'structural_position_type': structural_type,
                }
            )
        
        return None
    
    def check_invalidation_stop(
        self,
        position: OptionsPosition,
        market_data: Dict[str, Any],
        orb_data: Optional[Any] = None
    ) -> Optional[ExitSignal]:
        """
        Check underlying invalidation stop (structural stop)
        
        Exit immediately if ANY occur:
        - VWAP reclaim against position
        - ORB midpoint reclaim
        - ORB breakdown candle fully retraced
        - Higher low + momentum shift detected
        
        Args:
            position: Options position
            market_data: Current market data (price, VWAP, etc.)
            orb_data: ORB data for symbol (optional)
            
        Returns:
            ExitSignal if invalidation stop triggered, None otherwise
        """
        symbol = position.symbol
        current_price = market_data.get('current_price', 0.0)
        vwap = market_data.get('vwap', None)
        structural_type = self._get_structural_position_type(position)
        is_credit_spread = structural_type == "credit_spread"
        direction = self._nested_field(position.debit_spread, 'option_type') if position.debit_spread else (
            self._nested_field(position.credit_spread, 'option_type') if position.credit_spread else (
            self._nested_field(position.lotto_contract, 'option_type', 'call') if position.lotto_contract else 'call'
            )
        )
        
        if current_price <= 0:
            return None
        
        invalidation_reasons = []
        
        # 1. VWAP Reclaim Check
        if vwap and self.require_vwap_data:
            if direction == 'call':  # Long position
                # Exit if price reclaims VWAP from above (bearish)
                if current_price < vwap:
                    invalidation_reasons.append(f"VWAP reclaim: Price ${current_price:.2f} < VWAP ${vwap:.2f}")
            else:  # put (short position)
                # Exit if price reclaims VWAP from below (bullish)
                if current_price > vwap:
                    invalidation_reasons.append(f"VWAP reclaim: Price ${current_price:.2f} > VWAP ${vwap:.2f}")
        
        # 2. ORB Midpoint Reclaim Check
        if orb_data and self.require_orb_data:
            orb_midpoint = (orb_data.orb_high + orb_data.orb_low) / 2.0
            
            if direction == 'call':  # Long position (breakout above ORB high)
                # Exit if price drops below ORB midpoint (breakdown)
                if current_price < orb_midpoint:
                    invalidation_reasons.append(f"ORB midpoint reclaim: Price ${current_price:.2f} < ORB Mid ${orb_midpoint:.2f}")
            else:  # put (breakdown below ORB low)
                # Exit if price rises above ORB midpoint (breakout)
                if current_price > orb_midpoint:
                    invalidation_reasons.append(f"ORB midpoint reclaim: Price ${current_price:.2f} > ORB Mid ${orb_midpoint:.2f}")
        
        # 3. ORB Breakdown Retracement Check
        if orb_data and self.require_orb_data:
            if direction == 'call':  # Long position
                # Exit if price was below ORB low and now above ORB high (full retracement)
                # This indicates the breakdown was false
                if current_price > orb_data.orb_high:
                    invalidation_reasons.append(f"ORB breakdown retraced: Price ${current_price:.2f} > ORB High ${orb_data.orb_high:.2f}")
            else:  # put (short position)
                # Exit if price was above ORB high and now below ORB low (full retracement)
                if current_price < orb_data.orb_low:
                    invalidation_reasons.append(f"ORB breakdown retraced: Price ${current_price:.2f} < ORB Low ${orb_data.orb_low:.2f}")
        
        # 4. Higher Low + Momentum Shift Check
        # This requires historical price data - simplified check
        momentum = market_data.get('momentum', 0.0)
        if direction == 'call' and momentum < -0.5:  # Long position, negative momentum
            invalidation_reasons.append(f"Momentum shift: Momentum {momentum:.2f} < -0.5")
        elif direction == 'put' and momentum > 0.5:  # Short position, positive momentum
            invalidation_reasons.append(f"Momentum shift: Momentum {momentum:.2f} > 0.5")
        
        if invalidation_reasons:
            # Calculate current P&L
            current_value = market_data.get('current_value', position.current_value)
            entry_price = position.entry_price
            
            # Calculate P&L based on structural type
            if is_credit_spread:
                # For credit spreads: profit = (entry_price - current_value) / entry_price
                pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
                pnl_dollar = (entry_price - current_value) * position.quantity * 100
            else:
                # For debit spreads and lottos: profit = (current_value - entry_price) / entry_price
                pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
                pnl_dollar = (current_value - entry_price) * position.quantity * 100
            
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.INVALIDATION_STOP,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={
                    'invalidation_reasons': invalidation_reasons,
                    'current_price': current_price,
                    'vwap': vwap,
                    'orb_data': {
                        'high': orb_data.orb_high if orb_data else None,
                        'low': orb_data.orb_low if orb_data else None,
                        'midpoint': (orb_data.orb_high + orb_data.orb_low) / 2.0 if orb_data else None
                    } if orb_data else None,
                    'position_type': position.position_type,
                    'structural_position_type': structural_type,
                }
            )
        
        return None
    
    def check_time_stop(
        self,
        position: OptionsPosition,
        current_value: float
    ) -> Optional[ExitSignal]:
        """
        Check time stop (theta decay prevention)
        
        Debit Spread: Exit if no favorable move within 20-30 minutes
        Lotto: Exit if no move within 10-15 minutes
        
        Args:
            position: Options position
            current_value: Current position value
            
        Returns:
            ExitSignal if time stop triggered, None otherwise
        """
        entry_time = self._normalize_dt(position.entry_time)
        time_held = datetime.now(timezone.utc) - entry_time
        time_held_minutes = time_held.total_seconds() / 60.0
        
        structural_type = self._get_structural_position_type(position)
        is_credit_spread = structural_type == "credit_spread"
        is_spread = structural_type in {"debit_spread", "credit_spread"}

        # Determine time threshold based on position type
        if structural_type == 'debit_spread':
            time_threshold = self.debit_spread_time_stop_minutes
        elif structural_type == 'credit_spread':
            time_threshold = self.credit_spread_time_stop_minutes
        else:  # single-leg
            time_threshold = self.lotto_time_stop_minutes
        
        # Check if time threshold exceeded
        if time_held_minutes >= time_threshold:
            # Check if position has moved favorably
            entry_price = position.entry_price
            
            # Calculate P&L based on position type
            if is_credit_spread:
                # For credit spreads: profit = (entry_price - current_value) / entry_price
                pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
            else:
                # For debit spreads and lottos: profit = (current_value - entry_price) / entry_price
                pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
            
            # For debit spreads: Check if no favorable move (still near entry or negative)
            # For credit spreads: Check if no favorable move (still near entry or negative)
            # For lottos: Check if no move at all (still at entry or negative)
            if is_spread:
                # Debit/Credit spread: Exit if still negative or minimal gain (< 5%)
                # For credit spreads, negative pnl_pct means loss (current_value > entry_price)
                # For debit spreads, negative pnl_pct means loss (current_value < entry_price)
                if pnl_pct < 0.05:
                    # Calculate P&L dollar amount based on position type
                    if is_credit_spread:
                        pnl_dollar = (entry_price - current_value) * position.quantity * 100
                    else:
                        pnl_dollar = (current_value - entry_price) * position.quantity * 100
                    
                    return ExitSignal(
                        position_id=position.position_id,
                        reason=ExitReason.TIME_STOP,
                        exit_price=current_value,
                        exit_time=datetime.now(timezone.utc),
                        pnl_pct=pnl_pct,
                        pnl_dollar=pnl_dollar,
                        details={
                            'time_held_minutes': time_held_minutes,
                            'time_threshold_minutes': time_threshold,
                            'entry_price': entry_price,
                            'current_value': current_value,
                            'position_type': position.position_type,
                            'structural_position_type': structural_type,
                            'reason': 'No favorable move within time window'
                        }
                    )
            else:  # single-leg
                # Lotto: Exit if no move (still at entry or negative)
                if pnl_pct <= 0.0:
                    pnl_dollar = (current_value - entry_price) * position.quantity * 100
                    
                    return ExitSignal(
                        position_id=position.position_id,
                        reason=ExitReason.TIME_STOP,
                        exit_price=current_value,
                        exit_time=datetime.now(timezone.utc),
                        pnl_pct=pnl_pct,
                        pnl_dollar=pnl_dollar,
                        details={
                            'time_held_minutes': time_held_minutes,
                            'time_threshold_minutes': time_threshold,
                            'entry_price': entry_price,
                            'current_value': current_value,
                            'position_type': position.position_type,
                            'structural_position_type': structural_type,
                            'reason': 'No impulse move within time window'
                        }
                    )
        
        return None
    
    def check_fail_safe(
        self,
        position: OptionsPosition,
        current_value: float,
        market_data: Dict[str, Any]
    ) -> Optional[ExitSignal]:
        """
        Check absolute fail-safe (emergency exit)
        
        Exit immediately if:
        - Option price drops to -60%
        - Liquidity degrades (bid/ask spread widens abnormally)
        - Spread widens abnormally (for debit spreads)
        
        Args:
            position: Options position
            current_value: Current position value
            market_data: Current market data
            
        Returns:
            ExitSignal if fail-safe triggered, None otherwise
        """
        entry_price = position.entry_price
        
        structural_type = self._get_structural_position_type(position)
        is_credit_spread = structural_type == "credit_spread"

        # Calculate P&L based on structural type
        if is_credit_spread:
            # For credit spreads: profit = (entry_price - current_value) / entry_price
            pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (entry_price - current_value) * position.quantity * 100
        else:
            # For debit spreads and lottos: profit = (current_value - entry_price) / entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (current_value - entry_price) * position.quantity * 100
        
        # Determine fail-safe threshold based on position type
        if structural_type == 'debit_spread':
            fail_safe_threshold = self.debit_spread_fail_safe_pct
        elif structural_type == 'credit_spread':
            fail_safe_threshold = self.credit_spread_fail_safe_pct
        else:  # single-leg
            fail_safe_threshold = self.lotto_fail_safe_pct
        
        fail_safe_reasons = []
        
        # 1. Price Drop Check
        if pnl_pct <= fail_safe_threshold:
            fail_safe_reasons.append(f"Price drop: {pnl_pct*100:.1f}% <= {fail_safe_threshold*100:.0f}% threshold")
        
        # 2. Liquidity Degradation Check
        bid_ask_spread_pct = market_data.get('bid_ask_spread_pct', 0.0)
        if bid_ask_spread_pct > 0.10:  # Spread > 10% indicates poor liquidity
            fail_safe_reasons.append(f"Liquidity degraded: Bid/ask spread {bid_ask_spread_pct*100:.1f}% > 10%")
        
        # 3. Spread Widening Check (for debit spreads)
        if structural_type == 'debit_spread' and position.debit_spread:
            long_strike = float(self._nested_field(position.debit_spread, 'long_strike', 0.0) or 0.0)
            short_strike = float(self._nested_field(position.debit_spread, 'short_strike', 0.0) or 0.0)
            original_spread = abs(long_strike - short_strike)
            current_spread = market_data.get('current_spread', original_spread)
            if current_spread > original_spread * 1.5:  # Spread widened >50%
                fail_safe_reasons.append(f"Spread widened: {current_spread:.2f} > {original_spread:.2f} * 1.5")
        elif structural_type == 'credit_spread' and position.credit_spread:
            short_strike = float(self._nested_field(position.credit_spread, 'short_strike', 0.0) or 0.0)
            long_strike = float(self._nested_field(position.credit_spread, 'long_strike', 0.0) or 0.0)
            original_spread = abs(short_strike - long_strike)
            current_spread = market_data.get('current_spread', original_spread)
            if current_spread > original_spread * 1.5:  # Spread widened >50%
                fail_safe_reasons.append(f"Spread widened: {current_spread:.2f} > {original_spread:.2f} * 1.5")
        
        if fail_safe_reasons:
            
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.FAIL_SAFE,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={
                    'fail_safe_reasons': fail_safe_reasons,
                    'entry_price': entry_price,
                    'current_value': current_value,
                    'bid_ask_spread_pct': bid_ask_spread_pct,
                    'position_type': position.position_type,
                    'structural_position_type': structural_type,
                }
            )
        
        return None
    
    def check_profit_targets(
        self,
        position: OptionsPosition,
        current_value: float
    ) -> Optional[Tuple[ExitReason, Dict[str, Any]]]:
        """
        Check profit targets (automated exits)
        
        Automated Exit Strategy:
        - +60% → sell 50% (first target)
        - +120% → sell 25% (second target)
        - Runner trails until VWAP/ORB reclaim or time cutoff
        
        Args:
            position: Options position
            current_value: Current position value
            
        Returns:
            Tuple of (ExitReason, details) if profit target reached, None otherwise
        """
        entry_price = position.entry_price
        
        structural_type = self._get_structural_position_type(position)
        # Calculate P&L based on structural type
        if structural_type == 'credit_spread':
            # For credit spreads: profit = (entry_price - current_value) / entry_price
            # Entry price = credit received, current_value = cost to close
            # Profit when current_value decreases (spread expires worthless)
            pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
        else:
            # For debit spreads and single-legs: profit = (current_value - entry_price) / entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
        
        # First target: +60% → sell 50%
        if pnl_pct >= self.first_profit_target_pct and position.status == 'open':
            return (ExitReason.PROFIT_TARGET, {
                'target_pct': self.first_profit_target_pct,
                'current_pnl_pct': pnl_pct,
                'sell_pct': self.first_profit_sell_pct,
                'action': 'partial_close_50pct',
                'target_name': 'first_target_60pct'
            })
        
        # Second target: +120% → sell 25% (remaining position)
        if pnl_pct >= self.second_profit_target_pct and position.status == 'partial':
            # Check if we've already taken first profit (status is 'partial')
            return (ExitReason.PROFIT_TARGET, {
                'target_pct': self.second_profit_target_pct,
                'current_pnl_pct': pnl_pct,
                'sell_pct': self.second_profit_sell_pct,
                'action': 'partial_close_25pct',
                'target_name': 'second_target_120pct'
            })
        
        return None
    
    def check_runner_exit(
        self,
        position: OptionsPosition,
        current_value: float,
        market_data: Dict[str, Any],
        orb_data: Optional[Any] = None
    ) -> Optional[ExitSignal]:
        """
        Check runner exit conditions
        
        Runner exits on:
        - VWAP reclaim ❌
        - ORB midpoint reclaim ❌
        - Time stop (near EOD)
        
        Args:
            position: Options position (should be in 'partial' status after first/second targets)
            current_value: Current position value
            market_data: Current market data
            orb_data: ORB data for symbol
            
        Returns:
            ExitSignal if runner should exit, None otherwise
        """
        # Only check runners (positions that have taken partial profits)
        if position.status != 'partial':
            return None
        
        entry_price = position.entry_price
        structural_type = self._get_structural_position_type(position)
        # Calculate P&L based on structural type
        if structural_type == 'credit_spread':
            # For credit spreads: profit = (entry_price - current_value) / entry_price
            pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
        else:
            # For debit spreads and single-legs: profit = (current_value - entry_price) / entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
        symbol = position.symbol
        current_price = market_data.get('current_price', 0.0)
        vwap = market_data.get('vwap', None)
        
        if current_price <= 0:
            return None
        
        # Determine position direction
        direction = self._nested_field(position.debit_spread, 'option_type') if position.debit_spread else (
            self._nested_field(position.credit_spread, 'option_type') if position.credit_spread else (
                self._nested_field(position.lotto_contract, 'option_type', 'call') if position.lotto_contract else 'call'
            )
        )
        
        exit_reasons = []
        
        # 1. VWAP Reclaim Check (Runner Exit)
        if vwap:
            if direction == 'call':  # Long position
                if current_price < vwap:
                    exit_reasons.append(f"Runner exit: VWAP reclaim - Price ${current_price:.2f} < VWAP ${vwap:.2f}")
            else:  # put (short position)
                if current_price > vwap:
                    exit_reasons.append(f"Runner exit: VWAP reclaim - Price ${current_price:.2f} > VWAP ${vwap:.2f}")
        
        # 2. ORB Midpoint Reclaim Check (Runner Exit)
        if orb_data:
            orb_midpoint = (orb_data.orb_high + orb_data.orb_low) / 2.0
            
            if direction == 'call':  # Long position
                if current_price < orb_midpoint:
                    exit_reasons.append(f"Runner exit: ORB midpoint reclaim - Price ${current_price:.2f} < ORB Mid ${orb_midpoint:.2f}")
            else:  # put (short position)
                if current_price > orb_midpoint:
                    exit_reasons.append(f"Runner exit: ORB midpoint reclaim - Price ${current_price:.2f} > ORB Mid ${orb_midpoint:.2f}")
        
        if exit_reasons:
            pnl_dollar = (current_value - entry_price) * position.quantity * 100
            
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.RUNNER_TARGET,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={
                    'exit_reasons': exit_reasons,
                    'current_price': current_price,
                    'vwap': vwap,
                    'orb_midpoint': (orb_data.orb_high + orb_data.orb_low) / 2.0 if orb_data else None,
                    'position_type': position.position_type,
                    'structural_position_type': structural_type,
                    'runner_exit': True
                }
            )
        
        return None
    
    def evaluate_exit(
        self,
        position: OptionsPosition,
        current_value: float,
        market_data: Dict[str, Any],
        orb_data: Optional[Any] = None
    ) -> Optional[ExitSignal]:
        """
        Evaluate all exit conditions and return exit signal if any triggered
        
        Priority Order:
        1. Fail-Safe (highest priority - emergency exit)
        2. Hard Stop (premium-based protection)
        3. Invalidation Stop (structural stop)
        4. Time Stop (theta decay prevention)
        5. Profit Targets (partial and runner)
        
        Args:
            position: Options position
            current_value: Current position value
            market_data: Current market data (price, VWAP, bid/ask spread, etc.)
            orb_data: ORB data for symbol (optional)
            
        Returns:
            ExitSignal if exit condition triggered, None otherwise
        """
        structural_type = self._get_structural_position_type(position)
        log.debug(
            "OPTIONS_EXIT_EVAL | position_id=%s | legacy_type=%s | structural_type=%s",
            position.position_id,
            position.position_type,
            structural_type,
        )

        # Normal exits are handled exclusively by shared stealth engine.
        # Legacy manager now delegates to emergency-only fallback behavior.
        return self.evaluate_emergency_only(
            position=position,
            current_value=current_value,
            market_data=market_data,
            stale_quote=False,
            force_eod=False,
        )

    def evaluate_emergency_only(
        self,
        position: OptionsPosition,
        current_value: float,
        market_data: Dict[str, Any],
        stale_quote: bool = False,
        force_eod: bool = False,
    ) -> Optional[ExitSignal]:
        """Fallback-only emergency exits (no trailing/breakeven/profit logic)."""
        # Always update performance telemetry even in emergency-only mode.
        self._update_position_performance(position, current_value)
        if force_eod:
            entry_price = position.entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (current_value - entry_price) * position.quantity * 100
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.EOD_CLOSE,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={"fallback": "eod_forced_close"},
            )

        if stale_quote:
            entry_price = position.entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (current_value - entry_price) * position.quantity * 100
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.HEALTH_EMERGENCY,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={"fallback": "stale_quote_threshold_exceeded"},
            )

        fail_safe_signal = self.check_fail_safe(position, current_value, market_data)
        if fail_safe_signal:
            return fail_safe_signal
        return None
    
    def get_exit_summary(self, exit_signals: List[ExitSignal]) -> Dict[str, Any]:
        """
        Get summary of exit signals
        
        Args:
            exit_signals: List of exit signals
            
        Returns:
            Dictionary with exit summary statistics
        """
        if not exit_signals:
            return {
                'total_exits': 0,
                'by_reason': {},
                'total_pnl': 0.0,
                'avg_pnl_pct': 0.0
            }
        
        by_reason = {}
        total_pnl = 0.0
        
        for signal in exit_signals:
            reason = signal.reason.value
            by_reason[reason] = by_reason.get(reason, 0) + 1
            total_pnl += signal.pnl_dollar
        
        avg_pnl_pct = sum(s.pnl_pct for s in exit_signals) / len(exit_signals) if exit_signals else 0.0
        
        return {
            'total_exits': len(exit_signals),
            'by_reason': by_reason,
            'total_pnl': total_pnl,
            'avg_pnl_pct': avg_pnl_pct
        }

