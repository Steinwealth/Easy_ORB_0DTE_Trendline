#!/usr/bin/env python3
"""
Options Trading Executor
========================

Executes options trades (debit spreads and lotto sleeve) for 0DTE Strategy.
Handles order execution, position management, and profit-taking.

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import logging
import os
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, field

from .options_chain_manager import DebitSpread, CreditSpread, OptionContract
from .options_exit_manager import OptionsExitManager, ExitReason, ExitSignal
from .options_types import OptionsPosition
from .options_execution_normalize import (
    build_normalized_metadata_credit_spread,
    build_normalized_metadata_debit_spread,
    build_normalized_metadata_single_leg,
    log_metadata_normalized,
    log_position_type_normalized,
)

try:
    from modules.execution_routing import (
        smart_execution_enabled,
        resolve_options_exit_plan,
        last_look_option_spread_ok,
        last_look_max_spread_pct_default,
    )
    from modules.execution_telemetry import log_execution_event, build_execution_payload
except ImportError:
    def smart_execution_enabled() -> bool:  # type: ignore
        return False

    def resolve_options_exit_plan(*args, **kwargs):  # type: ignore
        class _P:
            order_type = "MARKET"
            allow_market_fallback = True
            exit_urgency = "URGENT"
            exit_execution_style = "MARKET"

        return _P()

    def last_look_option_spread_ok(*args):  # type: ignore
        return True, 0.0

    def last_look_max_spread_pct_default() -> float:  # type: ignore
        return 2.5

    log_execution_event = None  # type: ignore
    build_execution_payload = None  # type: ignore

# Import ETrade Options API
try:
    from .etrade_options_api import ETradeOptionsAPI

    ETRADE_OPTIONS_AVAILABLE = True
except ImportError:
    ETRADE_OPTIONS_AVAILABLE = False
    logging.warning("ETrade Options API not available")

log = logging.getLogger(__name__)


def _execution_context_for_entry(
    *,
    confidence: float = 0.0,
    breakout_score: float = 0.0,
    seconds_from_signal: float = 45.0,
    continuation_distance: float = 0.0,
    velocity: float = 0.0,
) -> Dict[str, Any]:
    """Lightweight momentum context for opening aggression (no signal-engine changes)."""
    return {
        "confidence": float(confidence or 0.0),
        "breakout_score": float(breakout_score or 0.0),
        "seconds_from_signal": float(seconds_from_signal),
        "continuation_distance": float(continuation_distance or 0.0),
        "velocity": float(velocity or 0.0),
        "high_confidence_impulse": float(confidence or 0.0) >= 0.85,
    }


def _infer_nonzero_option_per_share_mark(position: Any) -> float:
    """When current_value is zero, infer last per-share mark from nested open-leg data (demo EOD flatten)."""
    def _lotto_mid(lc: Any) -> float:
        if not isinstance(lc, dict):
            return 0.0
        for k in ("mid_price", "last", "premium", "mark"):
            try:
                v = float(lc.get(k) or 0.0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return 0.0

    def _debit_mid(ds: Any) -> float:
        if not isinstance(ds, dict):
            return 0.0
        try:
            dc = float(ds.get("debit_cost") or 0.0)
            if dc > 0:
                return dc
        except (TypeError, ValueError):
            pass
        long_c = ds.get("long_contract")
        if isinstance(long_c, dict):
            for k in ("mid_price", "last", "premium"):
                try:
                    v = float(long_c.get(k) or 0.0)
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    continue
        return 0.0

    if isinstance(position, dict):
        lc = position.get("lotto_contract")
        v = _lotto_mid(lc)
        if v > 0:
            return v
        v = _debit_mid(position.get("debit_spread"))
        if v > 0:
            return v
        cs = position.get("credit_spread")
        if isinstance(cs, dict):
            try:
                cr = float(cs.get("credit_received") or 0.0)
                if cr > 0:
                    return cr
            except (TypeError, ValueError):
                pass
        return 0.0

    lc = getattr(position, "lotto_contract", None)
    v = _lotto_mid(lc)
    if v > 0:
        return v
    v = _debit_mid(getattr(position, "debit_spread", None))
    if v > 0:
        return v
    cs = getattr(position, "credit_spread", None)
    if isinstance(cs, dict):
        try:
            cr = float(cs.get("credit_received") or 0.0)
            if cr > 0:
                return cr
        except (TypeError, ValueError):
            pass
    return 0.0


class OptionsTradingExecutor:
    """
    Options Trading Executor for 0DTE Strategy
    
    Handles:
    - Debit spread execution (Demo/Live)
    - Credit spread execution (Demo/Live)
    - Lotto sleeve execution (Demo/Live)
    - Position management
    - Auto-partial profits
    - Systematic runners
    
    Supports both Demo Mode (mock execution) and Live Mode (broker API).
    """
    
    def __init__(
        self,
        auto_partial_enabled: bool = True,
        partial_profit_pct: float = 0.50,  # Legacy: Take 50% profit at target
        runner_profit_pct: float = 2.0,  # Legacy: Let 50% run to 2x
        max_position_cost: float = 999999.0,  # Disabled - using only percentage-based limit (35%)
        max_position_size_pct: float = 0.35,  # Max position size as % of account equity (35% - matches ORB Strategy)
        demo_mode: Optional[bool] = None,  # None = auto-detect from env, True/False = explicit
        mock_executor=None,  # Mock executor for Demo mode
        alert_manager=None,  # Alert manager for notifications
        priority_collector=None  # Priority Data Collector for trade history
    ):
        """
        Initialize Options Trading Executor
        
        Args:
            auto_partial_enabled: Enable auto-partial profits
            partial_profit_pct: Percentage to take profit at (e.g., 0.50 = 50%)
            runner_profit_pct: Profit percentage for runners (e.g., 2.0 = 2x)
            max_position_cost: Maximum cost per position
        """
        self.auto_partial_enabled = auto_partial_enabled
        self.partial_profit_pct = partial_profit_pct  # Legacy, kept for compatibility
        self.runner_profit_pct = runner_profit_pct  # Legacy, kept for compatibility
        self.max_position_cost = max_position_cost
        self.max_position_size_pct = max_position_size_pct  # 35% max of account equity (matches ORB Strategy)
        
        # Detect mode from environment if not explicitly set
        if demo_mode is None:
            demo_mode = (
                os.getenv('ETRADE_MODE', 'demo').lower() == 'demo' or
                os.getenv('DEPLOYMENT_MODE', 'demo').lower() == 'demo' or
                os.getenv('DEMO_MODE_ENABLED', 'true').lower() == 'true'
            )
        
        self.demo_mode = demo_mode
        self.mock_executor = mock_executor
        self.alert_manager = alert_manager
        self.priority_collector = priority_collector  # Priority Data Collector
        # Optional callback(latency_ms: float, source: str) for external latency controllers.
        self.chain_latency_callback = None
        
        # ETrade Options API for Live Mode
        # Note: Can be set externally via set_etrade_options_api() if initialized in main.py
        self.etrade_options_api = None
        if not demo_mode and ETRADE_OPTIONS_AVAILABLE:
            try:
                environment = os.getenv('ETRADE_ENVIRONMENT', 'prod')
                self.etrade_options_api = ETradeOptionsAPI(environment=environment)
                if not self.etrade_options_api.is_available():
                    log.warning("ETrade Options API not available - Live mode disabled")
                    self.etrade_options_api = None
            except Exception as e:
                log.warning(f"Failed to initialize ETrade Options API: {e}")
                self.etrade_options_api = None
    
        # Position tracking
        self.positions: Dict[str, OptionsPosition] = {}
        
        # Initialize Exit Manager with automated exit targets
        self.exit_manager = OptionsExitManager(
            debit_spread_hard_stop_pct=float(os.getenv('0DTE_DEBIT_HARD_STOP_PCT', '-0.45')),
            debit_spread_time_stop_minutes=int(os.getenv('0DTE_DEBIT_TIME_STOP_MINUTES', '25')),
            debit_spread_fail_safe_pct=float(os.getenv('0DTE_DEBIT_FAIL_SAFE_PCT', '-0.60')),
            lotto_hard_stop_pct=float(os.getenv('0DTE_LOTTO_HARD_STOP_PCT', '-0.55')),
            lotto_time_stop_minutes=int(os.getenv('0DTE_LOTTO_TIME_STOP_MINUTES', '12')),
            lotto_fail_safe_pct=float(os.getenv('0DTE_LOTTO_FAIL_SAFE_PCT', '-0.60')),
            first_profit_target_pct=float(os.getenv('0DTE_FIRST_PROFIT_TARGET_PCT', '0.60')),  # +60%
            first_profit_sell_pct=float(os.getenv('0DTE_FIRST_PROFIT_SELL_PCT', '0.50')),  # Sell 50%
            second_profit_target_pct=float(os.getenv('0DTE_SECOND_PROFIT_TARGET_PCT', '1.20')),  # +120%
            second_profit_sell_pct=float(os.getenv('0DTE_SECOND_PROFIT_SELL_PCT', '0.25')),  # Sell 25%
            partial_profit_pct=partial_profit_pct,  # Legacy
            runner_profit_pct=runner_profit_pct  # Legacy
        )
        
        mode_label = "🎮 DEMO MODE" if demo_mode else "💰 LIVE MODE"
        log.info(f"Options Trading Executor initialized ({mode_label}):")
        log.info(f"  - Auto-partial enabled: {auto_partial_enabled}")
        log.info(f"  - First profit target: +{self.exit_manager.first_profit_target_pct*100:.0f}% → sell {self.exit_manager.first_profit_sell_pct*100:.0f}%")
        log.info(f"  - Second profit target: +{self.exit_manager.second_profit_target_pct*100:.0f}% → sell {self.exit_manager.second_profit_sell_pct*100:.0f}%")
        log.info(f"  - Max position cost: ${max_position_cost:.2f}")
        log.info(f"  - Max position size: {max_position_size_pct*100:.0f}% of account equity")
        log.info(f"  - Exit Manager: ✅ Initialized with optimized exit framework")
        if not demo_mode:
            log.info(f"  - ETrade API available: {self.etrade_options_api is not None}")

    @staticmethod
    def _demo_available_balance(mock_executor) -> float:
        if mock_executor is None:
            return 0.0
        fn = getattr(mock_executor, "available_balance", None)
        if callable(fn):
            return float(fn())
        return float(getattr(mock_executor, "account_balance", 0.0) or 0.0)

    def _snapshot_alert_account_balance(self) -> Optional[float]:
        """Best-effort cash/equity snapshot for exit Telegram footers (demo or live)."""
        if self.demo_mode and self.mock_executor is not None:
            return float(getattr(self.mock_executor, "account_balance", 0.0) or 0.0)
        api = self.etrade_options_api
        if api is None or not getattr(api, "is_available", lambda: False)():
            return None
        et = getattr(api, "etrade", None)
        if et is None:
            return None
        try:
            account_balance_obj = et.get_account_balance()
            if not account_balance_obj:
                return None
            bal = (
                account_balance_obj.cash_available_for_investment
                or account_balance_obj.account_value
                or 0.0
            )
            return float(bal) if bal else None
        except Exception:
            return None

    def set_etrade_options_api(self, etrade_options_api):
        """
        Set ETrade Options API instance (called from main.py initialization)
        
        Args:
            etrade_options_api: ETradeOptionsAPI instance
        """
        self.etrade_options_api = etrade_options_api
        if etrade_options_api and etrade_options_api.is_available():
            log.info("✅ ETrade Options API set for Options Trading Executor")
        else:
            log.warning("⚠️ ETrade Options API not available")

    async def execute_by_strategy(
        self,
        *,
        strategy_type: str,
        direction: str,
        quantity: int = 1,
        single_leg_contract: Optional[OptionContract] = None,
        debit_spread: Optional[DebitSpread] = None,
        credit_spread: Optional[CreditSpread] = None,
        send_open_alert: bool = True,
    ) -> Optional[OptionsPosition]:
        """
        Strategy-driven execution router.
        Routing is based on strategy_type, not spread_type.
        """
        st = str(strategy_type or "").strip().lower()
        d = str(direction or "LONG").strip().upper()

        if st in {"lotto", "long_call", "long_put"}:
            if not single_leg_contract:
                log.warning("0DTE_SPREAD_FALLBACK | symbol=unknown | reason=missing_single_leg_contract")
                return None
            return await self.execute_lotto_sleeve(
                contract=single_leg_contract,
                quantity=quantity,
                send_open_alert=send_open_alert,
                strategy_type=st,
                direction=d,
                spread_type="single_leg",
            )
        if st in {"momentum_scalper", "itm_probability_spread", "debit_spread"}:
            if not debit_spread:
                log.warning("0DTE_SPREAD_FALLBACK | symbol=unknown | reason=missing_debit_spread")
                return None
            return await self.execute_debit_spread(
                spread=debit_spread,
                quantity=quantity,
                send_open_alert=send_open_alert,
                strategy_type=st,
                direction=d,
                spread_type="debit",
            )
        if st == "credit_spread":
            if not credit_spread:
                log.warning("0DTE_SPREAD_FALLBACK | symbol=unknown | reason=missing_credit_spread")
                return None
            return await self.execute_credit_spread(
                spread=credit_spread,
                quantity=quantity,
                send_open_alert=send_open_alert,
                strategy_type=st,
                direction=d,
                spread_type="credit",
            )

        log.warning("Unsupported strategy_type for execution: %s", strategy_type)
        return None
    
    async def execute_debit_spread(
        self,
        spread: DebitSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "debit",
    ) -> Optional[OptionsPosition]:
        """
        Execute debit spread order (Demo or Live)
        
        Args:
            spread: DebitSpread object
            quantity: Number of spreads
            
        Returns:
            OptionsPosition or None if execution failed
        """
        # Validate combined package cost for the full spread position.
        # Debit spreads are one combined position under the 35% cap.
        total_cost = spread.debit_cost * quantity * 100  # Options are per 100 shares
        
        # Check max position size as % of account equity (35% max - matches ORB Strategy)
        # Note: max_position_cost check disabled - using only percentage-based limit
        if self.mock_executor:
            account_balance = self._demo_available_balance(self.mock_executor)
            max_allowed = account_balance * self.max_position_size_pct
            if total_cost > max_allowed:
                log.warning(f"Position cost ${total_cost:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f})")
                return None
        
        # Demo Mode: Use mock executor
        if self.demo_mode and self.mock_executor:
            return await self.mock_executor.execute_debit_spread(
                spread,
                quantity,
                send_open_alert=send_open_alert,
                strategy_type=strategy_type,
                direction=str(direction or "LONG"),
                spread_type=str(spread_type or "debit"),
            )
        
        # Live Mode: Use ETrade API
        if not self.demo_mode and self.etrade_options_api:
            # Check max position size as % of account equity (33% max) for Live Mode
            try:
                # Get account balance from ETrade API
                if hasattr(self.etrade_options_api, 'etrade') and self.etrade_options_api.etrade:
                    account_balance_obj = self.etrade_options_api.etrade.get_account_balance()
                    if account_balance_obj:
                        # Use cash available for investment or account value
                        account_balance = (
                            account_balance_obj.cash_available_for_investment or
                            account_balance_obj.account_value or
                            0.0
                        )
                        
                        if account_balance > 0:
                            max_allowed = account_balance * self.max_position_size_pct
                            if total_cost > max_allowed:
                                log.warning(f"Position cost ${total_cost:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f} of ${account_balance:.2f})")
                                return None
                            log.debug(f"✅ Live Mode: Position cost ${total_cost:.2f} within {self.max_position_size_pct*100:.0f}% limit (${max_allowed:.2f} of ${account_balance:.2f})")
                        else:
                            log.warning(f"⚠️ Live Mode: Could not determine account balance, skipping 33% check")
            except Exception as e:
                log.warning(f"⚠️ Live Mode: Error checking account balance for 33% limit: {e}")
                # Continue execution but log warning
            
            try:
                # Convert expiry format: YYYY-MM-DD -> YYYYMMDD
                expiry_etrade = spread.expiry.replace('-', '')
                max_debit = float(spread.debit_cost or 0.0)
                if max_debit <= 0:
                    try:
                        max_debit = float(spread.long_contract.mid_price) - float(spread.short_contract.mid_price)
                    except Exception:
                        max_debit = 0.0
                if max_debit <= 0:
                    log.error("Debit spread open blocked: invalid max_net_debit")
                    return None
                pending_tid = f"PENDING_{spread.symbol}_{int(datetime.now().timestamp())}"
                
                # Place order via ETrade API
                order_response = await self.etrade_options_api.place_debit_spread_order(
                    symbol=spread.symbol,
                    expiry=expiry_etrade,
                    option_type=spread.option_type,
                    long_strike=spread.long_strike,
                    short_strike=spread.short_strike,
                    quantity=quantity,
                    max_net_debit=max_debit,
                    quoted_mid_debit=float(spread.debit_cost or max_debit),
                    strategy=str(strategy_type or "ORB_0DTE"),
                    trade_id=pending_tid,
                    execution_context=_execution_context_for_entry(
                        confidence=0.8,
                        seconds_from_signal=35.0,
                        breakout_score=0.7,
                    ),
                )
                
                if order_response:
                    # Create position from order response (shortened ID format)
                    timestamp = int(datetime.now().timestamp())
                    expiry_short = spread.expiry.replace('-', '')[-6:] if spread.expiry else datetime.now().strftime('%y%m%d')
                    position_id = f"LIVE_{spread.symbol}_{expiry_short}_{int(spread.long_strike)}_{int(spread.short_strike)}_{spread.option_type[0]}_{str(timestamp)[-6:]}"
                    
                    norm = build_normalized_metadata_debit_spread(
                        trade_id=position_id,
                        spread=spread,
                        quantity=quantity,
                        strategy_type=strategy_type,
                        direction=str(direction or "LONG"),
                        spread_type=str(spread_type or "debit"),
                    )
                    _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
                    position = OptionsPosition(
                        position_id=position_id,
                        symbol=spread.symbol,
                        position_type='debit_spread',
                        debit_spread=spread,
                        entry_price=spread.debit_cost,
                        quantity=quantity,
                        current_value=spread.debit_cost,
                        unrealized_pnl=0.0,
                        strategy_type=_orb_st,
                        metadata={"normalized_options": norm},
                    )
                    log_position_type_normalized(position_id, norm["position_type"], "debit_spread")
                    log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
                    
                    self.positions[position_id] = position
                    
                    log.info(f"✅ Live debit spread executed: Position ID {position_id}")
                    return position
                else:
                    log.error("Failed to place debit spread order via ETrade API")
                    return None
                    
            except Exception as e:
                log.error(f"Error placing debit spread order via ETrade API: {e}")
                return None
        
        # Fallback: Log warning
        log.warning(f"💰 LIVE: ETrade Options API not available")
        log.info(f"Would execute debit spread: {spread.symbol} {spread.option_type} {spread.long_strike}/{spread.short_strike}")
        log.info(f"  Quantity: {quantity}, Debit: ${spread.debit_cost:.2f}, Total Cost: ${total_cost:.2f}")
        
        # Create position (Rev 00232: Shortened format for alerts - symbol_date_strikes_type_microseconds)
        now = datetime.now()
        expiry_short = spread.expiry.replace('-', '')[-6:] if spread.expiry else now.strftime('%y%m%d')
        microseconds_short = now.microsecond % 1000  # Last 3 digits for uniqueness
        # Format: LIVE_SYMBOL_YYMMDD_LONG_SHORT_TYPE_microseconds (e.g., LIVE_SPY_260107_585_590_c_546)
        position_id = f"LIVE_{spread.symbol}_{expiry_short}_{int(spread.long_strike)}_{int(spread.short_strike)}_{spread.option_type[0]}_{microseconds_short:03d}"
        
        norm = build_normalized_metadata_debit_spread(
            trade_id=position_id,
            spread=spread,
            quantity=quantity,
            strategy_type=strategy_type,
            direction=str(direction or "LONG"),
            spread_type=str(spread_type or "debit"),
        )
        _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
        position = OptionsPosition(
            position_id=position_id,
            symbol=spread.symbol,
            position_type='debit_spread',
            debit_spread=spread,
            entry_price=spread.debit_cost,
            quantity=quantity,
            current_value=spread.debit_cost,
            unrealized_pnl=0.0,
            strategy_type=_orb_st,
            metadata={"normalized_options": norm},
        )
        log_position_type_normalized(position_id, norm["position_type"], "debit_spread")
        log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
        
        self.positions[position_id] = position
        
        log.info(f"Debit spread executed: Position ID {position_id}")
        
        return position
    
    async def execute_lotto_sleeve(
        self,
        contract: OptionContract,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "single_leg",
    ) -> Optional[OptionsPosition]:
        """
        Execute lotto sleeve (single-leg option) (Demo or Live)
        
        Args:
            contract: OptionContract object
            quantity: Number of contracts
            
        Returns:
            OptionsPosition or None if execution failed
        """
        # Calculate position cost for percentage check
        total_cost = contract.mid_price * quantity * 100  # Options are per 100 shares
        
        # Check max position size as % of account equity (35% max - matches ORB Strategy)
        # Note: max_position_cost check disabled - using only percentage-based limit
        
        # Demo Mode: Use mock executor
        if self.demo_mode and self.mock_executor:
            return await self.mock_executor.execute_lotto_sleeve(
                contract,
                quantity,
                send_open_alert=send_open_alert,
                strategy_type=strategy_type,
                direction=str(direction or "LONG"),
                spread_type=str(spread_type or "single_leg"),
            )
        
        # Live Mode: Use ETrade API
        if not self.demo_mode and self.etrade_options_api:
            # Check max position size as % of account equity (33% max) for Live Mode
            try:
                # Get account balance from ETrade API
                if hasattr(self.etrade_options_api, 'etrade') and self.etrade_options_api.etrade:
                    account_balance_obj = self.etrade_options_api.etrade.get_account_balance()
                    if account_balance_obj:
                        # Use cash available for investment or account value
                        account_balance = (
                            account_balance_obj.cash_available_for_investment or
                            account_balance_obj.account_value or
                            0.0
                        )
                        
                        if account_balance > 0:
                            max_allowed = account_balance * self.max_position_size_pct
                            if total_cost > max_allowed:
                                log.warning(f"Position cost ${total_cost:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f} of ${account_balance:.2f})")
                                return None
                            log.debug(f"✅ Live Mode: Position cost ${total_cost:.2f} within {self.max_position_size_pct*100:.0f}% limit (${max_allowed:.2f} of ${account_balance:.2f})")
                        else:
                            log.warning(f"⚠️ Live Mode: Could not determine account balance, skipping 33% check")
            except Exception as e:
                log.warning(f"⚠️ Live Mode: Error checking account balance for 33% limit: {e}")
                # Continue execution but log warning
            
            try:
                # Convert expiry format: YYYY-MM-DD -> YYYYMMDD
                expiry_etrade = contract.expiry.replace('-', '')
                pending_tid = f"PENDING_{contract.symbol}_{int(datetime.now().timestamp())}"
                
                # Place order via ETrade API
                if smart_execution_enabled():
                    order_response = await self.etrade_options_api.place_single_option_buy_open_smart(
                        symbol=contract.symbol,
                        expiry=expiry_etrade,
                        option_type=contract.option_type,
                        strike=contract.strike,
                        quantity=quantity,
                        bid=float(contract.bid or 0.0),
                        ask=float(contract.ask or 0.0),
                        mid=float(contract.mid_price or 0.0),
                        strategy=str(strategy_type or "ORB_0DTE"),
                        trade_id=pending_tid,
                        execution_context=_execution_context_for_entry(
                            confidence=0.82,
                            seconds_from_signal=35.0,
                            breakout_score=0.72,
                        ),
                    )
                else:
                    order_response = await self.etrade_options_api.place_single_option_order(
                        symbol=contract.symbol,
                        expiry=expiry_etrade,
                        option_type=contract.option_type,
                        strike=contract.strike,
                        side='BUY',  # Lotto sleeve is always buying
                        quantity=quantity
                    )
                
                if order_response:
                    # Create position from order response
                    # Create position (shortened ID format)
                    timestamp = int(datetime.now().timestamp())
                    expiry_short = contract.expiry.replace('-', '')[-6:] if contract.expiry else datetime.now().strftime('%y%m%d')
                    position_id = f"LIVE_{contract.symbol}_{expiry_short}_{int(contract.strike)}_{contract.option_type[0]}_{str(timestamp)[-6:]}"
                    
                    norm = build_normalized_metadata_single_leg(
                        trade_id=position_id,
                        contract=contract,
                        quantity=quantity,
                        strategy_type=strategy_type or "lotto",
                        direction=str(direction or "LONG"),
                        spread_type=str(spread_type or "single_leg"),
                    )
                    _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "lotto").strip()
                    position = OptionsPosition(
                        position_id=position_id,
                        symbol=contract.symbol,
                        position_type='lotto',
                        lotto_contract=contract,
                        entry_price=contract.mid_price,
                        quantity=quantity,
                        current_value=contract.mid_price,
                        unrealized_pnl=0.0,
                        strategy_type=_orb_st,
                        metadata={"normalized_options": norm},
                    )
                    log_position_type_normalized(position_id, norm["position_type"], "lotto")
                    log_metadata_normalized(position_id, norm["position_type"], contract.symbol)
                    
                    self.positions[position_id] = position
                    
                    log.info(f"✅ Live lotto sleeve executed: Position ID {position_id}")
                    return position
                else:
                    log.error("Failed to place lotto sleeve order via ETrade API")
                    return None
                    
            except Exception as e:
                log.error(f"Error placing lotto sleeve order via ETrade API: {e}")
                return None
        
        # Fallback: Log warning
        log.warning(f"💰 LIVE: ETrade Options API not available")
        log.info(f"Would execute lotto sleeve: {contract.symbol} {contract.option_type} {contract.strike}")
        log.info(f"  Quantity: {quantity}, Cost: ${contract.mid_price:.2f}, Total Cost: ${total_cost:.2f}")
        
        # Create position
        # Create position (shortened ID format)
        timestamp = int(datetime.now().timestamp())
        expiry_short = contract.expiry.replace('-', '')[-6:] if contract.expiry else datetime.now().strftime('%y%m%d')
        position_id = f"LIVE_{contract.symbol}_{expiry_short}_{int(contract.strike)}_{contract.option_type[0]}_{str(timestamp)[-6:]}"
        
        norm = build_normalized_metadata_single_leg(
            trade_id=position_id,
            contract=contract,
            quantity=quantity,
            strategy_type=strategy_type or "lotto",
            direction=str(direction or "LONG"),
            spread_type=str(spread_type or "single_leg"),
        )
        _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "lotto").strip()
        position = OptionsPosition(
            position_id=position_id,
            symbol=contract.symbol,
            position_type='lotto',
            lotto_contract=contract,
            entry_price=contract.mid_price,
            quantity=quantity,
            current_value=contract.mid_price,
            unrealized_pnl=0.0,
            strategy_type=_orb_st,
            metadata={"normalized_options": norm},
        )
        log_position_type_normalized(position_id, norm["position_type"], "lotto")
        log_metadata_normalized(position_id, norm["position_type"], contract.symbol)
        
        self.positions[position_id] = position
        
        log.info(f"Lotto sleeve executed: Position ID {position_id}")
        
        return position
    
    async def execute_credit_spread(
        self,
        spread: CreditSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "credit",
    ) -> Optional[OptionsPosition]:
        """
        Execute credit spread order (Demo or Live)
        
        Args:
            spread: CreditSpread object
            quantity: Number of spreads
            
        Returns:
            OptionsPosition or None if execution failed
        """
        # Validate combined package margin for the full spread position.
        # Credit spreads are one combined position under the 35% cap.
        # Margin requirement = max_loss (spread_width - credit_received) * quantity * 100
        margin_requirement = spread.max_loss * quantity * 100  # Options are per 100 shares
        
        # Check max position size as % of account equity (35% max - matches ORB Strategy)
        # Note: max_position_cost check disabled - using only percentage-based limit
        if self.mock_executor:
            account_balance = self._demo_available_balance(self.mock_executor)
            max_allowed = account_balance * self.max_position_size_pct
            if margin_requirement > max_allowed:
                log.warning(f"Margin requirement ${margin_requirement:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f})")
                return None
        
        # Demo Mode: Use mock executor
        if self.demo_mode and self.mock_executor:
            return await self.mock_executor.execute_credit_spread(
                spread,
                quantity,
                send_open_alert=send_open_alert,
                strategy_type=strategy_type,
                direction=str(direction or "SHORT"),
                spread_type=str(spread_type or "credit"),
            )
        
        # Live Mode: Use ETrade API
        if not self.demo_mode and self.etrade_options_api:
            # Check max position size as % of account equity (33% max) for Live Mode
            try:
                # Get account balance from ETrade API
                if hasattr(self.etrade_options_api, 'etrade') and self.etrade_options_api.etrade:
                    account_balance_obj = self.etrade_options_api.etrade.get_account_balance()
                    if account_balance_obj:
                        # Use cash available for investment or account value
                        account_balance = (
                            account_balance_obj.cash_available_for_investment or
                            account_balance_obj.account_value or
                            0.0
                        )
                        
                        if account_balance > 0:
                            max_allowed = account_balance * self.max_position_size_pct
                            if margin_requirement > max_allowed:
                                log.warning(f"Margin requirement ${margin_requirement:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f} of ${account_balance:.2f})")
                                return None
                            log.debug(f"✅ Live Mode: Margin requirement ${margin_requirement:.2f} within {self.max_position_size_pct*100:.0f}% limit (${max_allowed:.2f} of ${account_balance:.2f})")
                        else:
                            log.warning(f"⚠️ Live Mode: Could not determine account balance, skipping 33% check")
            except Exception as e:
                log.warning(f"⚠️ Live Mode: Error checking account balance for 33% limit: {e}")
                # Continue execution but log warning
            
            try:
                # Convert expiry format: YYYY-MM-DD -> YYYYMMDD
                expiry_etrade = spread.expiry.replace('-', '')
                
                # Place order via ETrade API
                order_response = await self.etrade_options_api.place_credit_spread_order(
                    symbol=spread.symbol,
                    expiry=expiry_etrade,
                    option_type=spread.option_type,
                    short_strike=spread.short_strike,
                    long_strike=spread.long_strike,
                    quantity=quantity
                )
                
                if order_response:
                    # Create position from order response (shortened ID format)
                    timestamp = int(datetime.now().timestamp())
                    expiry_short = spread.expiry.replace('-', '')[-6:] if spread.expiry else datetime.now().strftime('%y%m%d')
                    position_id = f"LIVE_{spread.symbol}_{expiry_short}_{int(spread.short_strike)}_{int(spread.long_strike)}_{spread.option_type[0]}_{str(timestamp)[-6:]}"
                    
                    norm = build_normalized_metadata_credit_spread(
                        trade_id=position_id,
                        spread=spread,
                        quantity=quantity,
                        strategy_type=strategy_type or "debit_spread",
                        direction=str(direction or "SHORT"),
                        spread_type=str(spread_type or "credit"),
                    )
                    _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
                    position = OptionsPosition(
                        position_id=position_id,
                        symbol=spread.symbol,
                        position_type='credit_spread',
                        credit_spread=spread,
                        entry_price=spread.credit_received,  # Entry price = credit received
                        quantity=quantity,
                        current_value=spread.credit_received,
                        unrealized_pnl=0.0,
                        strategy_type=_orb_st,
                        metadata={"normalized_options": norm},
                    )
                    log_position_type_normalized(position_id, norm["position_type"], "credit_spread")
                    log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
                    
                    self.positions[position_id] = position
                    
                    log.info(f"✅ Live credit spread executed: Position ID {position_id}")
                    return position
                else:
                    log.error("Failed to place credit spread order via ETrade API")
                    return None
                    
            except Exception as e:
                log.error(f"Error placing credit spread order via ETrade API: {e}")
                return None
        
        # Fallback: Log warning
        log.warning(f"💰 LIVE: ETrade Options API not available")
        log.info(f"Would execute credit spread: {spread.symbol} {spread.option_type} {spread.short_strike}/{spread.long_strike}")
        log.info(f"  Quantity: {quantity}, Credit: ${spread.credit_received:.2f}, Margin: ${margin_requirement:.2f}")
        
        # Create position (shortened ID format)
        timestamp = int(datetime.now().timestamp())
        expiry_short = spread.expiry.replace('-', '')[-6:] if spread.expiry else datetime.now().strftime('%y%m%d')
        position_id = f"LIVE_{spread.symbol}_{expiry_short}_{int(spread.short_strike)}_{int(spread.long_strike)}_{spread.option_type[0]}_{str(timestamp)[-6:]}"
        
        norm = build_normalized_metadata_credit_spread(
            trade_id=position_id,
            spread=spread,
            quantity=quantity,
            strategy_type=strategy_type or "debit_spread",
            direction=str(direction or "SHORT"),
            spread_type=str(spread_type or "credit"),
        )
        _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
        position = OptionsPosition(
            position_id=position_id,
            symbol=spread.symbol,
            position_type='credit_spread',
            credit_spread=spread,
            entry_price=spread.credit_received,  # Entry price = credit received
            quantity=quantity,
            current_value=spread.credit_received,
            unrealized_pnl=0.0,
            strategy_type=_orb_st,
            metadata={"normalized_options": norm},
        )
        log_position_type_normalized(position_id, norm["position_type"], "credit_spread")
        log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
        
        self.positions[position_id] = position
        
        log.info(f"Credit spread executed: Position ID {position_id}")
        
        return position
    
    async def update_position_value(
        self,
        position_id: str,
        current_value: float
    ) -> Optional[OptionsPosition]:
        """
        Update position current value and P&L
        
        Args:
            position_id: Position ID
            current_value: Current position value
            
        Returns:
            Updated OptionsPosition or None if not found
        """
        if position_id not in self.positions:
            log.warning(f"Position {position_id} not found")
            return None
        
        position = self.positions[position_id]
        position.current_value = current_value
        meta = position.metadata if isinstance(position.metadata, dict) else {}
        norm = meta.get("normalized_options")
        if isinstance(norm, dict):
            norm["current_value"] = float(current_value)
        
        # Calculate unrealized P&L
        if position.position_type == 'debit_spread':
            # For debit spread: profit = (current_value - entry_price) * quantity * 100
            position.unrealized_pnl = (current_value - position.entry_price) * position.quantity * 100
        elif position.position_type == 'credit_spread':
            # For credit spread: profit = (entry_price - current_value) * quantity * 100
            # Entry price = credit received, current_value = current cost to close
            # Profit when current_value decreases (spread expires worthless or decreases)
            position.unrealized_pnl = (position.entry_price - current_value) * position.quantity * 100
        else:  # lotto
            # For lotto: profit = (current_value - entry_price) * quantity * 100
            position.unrealized_pnl = (current_value - position.entry_price) * position.quantity * 100
        
        return position
    
    async def update_positions_with_real_prices(
        self,
        *,
        shared_chain_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
        tick_id: Optional[str] = None,
    ) -> None:
        """
        Update all open positions with real-time options prices from E*TRADE API (Rev 00238)
        
        This ensures exit decisions are based on actual options P&L, not underlying price movement.
        For example: QQQ moves +0.86% but QQQ 628c moves from $0.19 to $0.97 (+410%)
        """
        open_positions = self.get_open_positions()

        if not open_positions:
            return

        log.debug(f"Updating {len(open_positions)} positions with real-time options prices...")

        # For demo mode, skip real API calls (use mock pricing path).
        if self.demo_mode:
            return
        if not self.etrade_options_api:
            return

        def _normalize_expiry(raw_expiry: Any) -> str:
            exp_s = str(raw_expiry or "").strip()
            if "-" in exp_s:
                return exp_s.replace("-", "")
            return exp_s

        def _to_float(value: Any) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _extract_contract_price(
            contracts: List[Any],
            target_strike: float,
            *,
            leg_role: str = "long",
        ) -> Optional[float]:
            exact_contract = None
            nearest_contract = None
            nearest_diff = None
            for contract in contracts:
                try:
                    c_strike = float(getattr(contract, "strike", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                diff = abs(c_strike - float(target_strike))
                if diff < 0.01:
                    exact_contract = contract
                    break
                if nearest_contract is None or diff < float(nearest_diff):
                    nearest_contract = contract
                    nearest_diff = diff

            chosen = exact_contract
            if chosen is None and nearest_contract is not None and float(nearest_diff or 999.0) <= 0.5:
                chosen = nearest_contract
            if chosen is None:
                return None

            mid = float(getattr(chosen, "mid_price", 0.0) or 0.0)
            if mid > 0:
                px = mid
            else:
                last = float(getattr(chosen, "last", 0.0) or 0.0)
                if last > 0:
                    px = last
                else:
                    bid = float(getattr(chosen, "bid", 0.0) or 0.0)
                    ask = float(getattr(chosen, "ask", 0.0) or 0.0)
                    if bid > 0 and ask > 0:
                        px = (bid + ask) / 2.0
                    else:
                        return None
            # Conservative marking for spread monitoring.
            if str(leg_role).lower() == "short":
                return float(px) * 1.02
            return float(px) * 0.98

        # Group once per symbol per monitor cycle and fetch exactly one chain snapshot per symbol.
        positions_by_symbol: Dict[str, List[OptionsPosition]] = {}
        for position in open_positions:
            sym = str(getattr(position, "symbol", "") or "").strip().upper()
            if not sym:
                continue
            positions_by_symbol.setdefault(sym, []).append(position)

        tick_key = str(tick_id or "default")
        cache_ref = shared_chain_cache if isinstance(shared_chain_cache, dict) else {}

        for symbol, symbol_positions in positions_by_symbol.items():
            chain_expiry = ""
            distinct_expiries: set[str] = set()
            for position in symbol_positions:
                expiry = None
                if position.position_type == 'debit_spread' and position.debit_spread:
                    expiry = OptionsExitManager._nested_field(position.debit_spread, 'expiry')
                elif position.position_type == 'credit_spread' and position.credit_spread:
                    expiry = OptionsExitManager._nested_field(position.credit_spread, 'expiry')
                elif position.position_type == 'lotto' and position.lotto_contract:
                    expiry = OptionsExitManager._nested_field(position.lotto_contract, 'expiry')
                exp_norm = _normalize_expiry(expiry)
                if exp_norm:
                    distinct_expiries.add(exp_norm)
                    if not chain_expiry:
                        chain_expiry = exp_norm

            if len(distinct_expiries) > 1:
                log.warning(
                    "OPTION_CHAIN_USAGE | symbol=%s | multiple_expiries=%s | action=use_first_expiry_only",
                    symbol,
                    ",".join(sorted(distinct_expiries)),
                )

            legs_expected = 0
            legs_resolved = 0
            quote_calls_saved = 0
            dedup_key = (str(symbol), str(chain_expiry or "0dte"), tick_key)
            reused_chain = dedup_key in cache_ref
            if reused_chain:
                chain = cache_ref.get(dedup_key) or {}
            else:
                chain_t0 = time.perf_counter()
                chain = await self.etrade_options_api.fetch_options_chain(
                    symbol=symbol,
                    expiry=chain_expiry or None,
                    strike_count=25,
                    include_greeks=True,
                )
                if callable(getattr(self, "chain_latency_callback", None)):
                    try:
                        self.chain_latency_callback(
                            (time.perf_counter() - chain_t0) * 1000.0,
                            source="orb",
                        )
                    except Exception:
                        pass
                cache_ref[dedup_key] = chain or {}
            log.info(
                "OPTION_CHAIN_DEDUP | symbol=%s | reused=%s",
                symbol,
                str(bool(reused_chain)).lower(),
            )
            calls = chain.get("calls") if isinstance(chain, dict) else []
            puts = chain.get("puts") if isinstance(chain, dict) else []
            calls = calls if isinstance(calls, list) else []
            puts = puts if isinstance(puts, list) else []

            for position in symbol_positions:
                try:
                    current_value = None
                    option_type = None
                    strike = None
                    long_strike = None
                    short_strike = None

                    if position.position_type == 'debit_spread' and position.debit_spread:
                        ds = position.debit_spread
                        _ot = OptionsExitManager._nested_field(ds, 'option_type', None)
                        option_type = str(_ot).upper() if _ot is not None else None
                        long_strike = _to_float(OptionsExitManager._nested_field(ds, 'long_strike'))
                        short_strike = _to_float(OptionsExitManager._nested_field(ds, 'short_strike'))
                        legs_expected += 2
                        quote_calls_saved += 2
                    elif position.position_type == 'credit_spread' and position.credit_spread:
                        cs = position.credit_spread
                        _ot = OptionsExitManager._nested_field(cs, 'option_type', None)
                        option_type = str(_ot).upper() if _ot is not None else None
                        long_strike = _to_float(OptionsExitManager._nested_field(cs, 'long_strike'))
                        short_strike = _to_float(OptionsExitManager._nested_field(cs, 'short_strike'))
                        legs_expected += 2
                        quote_calls_saved += 2
                    elif position.position_type == 'lotto' and position.lotto_contract:
                        lc = position.lotto_contract
                        _ot = OptionsExitManager._nested_field(lc, 'option_type', None)
                        option_type = str(_ot).upper() if _ot is not None else None
                        strike = _to_float(OptionsExitManager._nested_field(lc, 'strike'))
                        legs_expected += 1
                        quote_calls_saved += 1

                    if option_type not in {"CALL", "PUT"}:
                        log.warning(f"   Position {position.position_id} missing option_type, skipping")
                        continue
                    contracts = calls if option_type == "CALL" else puts

                    if position.position_type == 'lotto' and isinstance(strike, (int, float)):
                        leg_price = _extract_contract_price(contracts, float(strike), leg_role="long")
                        if leg_price is not None:
                            current_value = float(leg_price)
                            legs_resolved += 1

                    elif (
                        position.position_type == 'debit_spread'
                        and isinstance(long_strike, (int, float))
                        and isinstance(short_strike, (int, float))
                    ):
                        long_value = _extract_contract_price(contracts, float(long_strike), leg_role="long")
                        short_value = _extract_contract_price(contracts, float(short_strike), leg_role="short")
                        if long_value is not None:
                            legs_resolved += 1
                        if short_value is not None:
                            legs_resolved += 1
                        if long_value is not None and short_value is not None:
                            current_value = float(long_value - short_value)

                    elif (
                        position.position_type == 'credit_spread'
                        and isinstance(long_strike, (int, float))
                        and isinstance(short_strike, (int, float))
                    ):
                        short_value = _extract_contract_price(contracts, float(short_strike), leg_role="short")
                        long_value = _extract_contract_price(contracts, float(long_strike), leg_role="long")
                        if short_value is not None:
                            legs_resolved += 1
                        if long_value is not None:
                            legs_resolved += 1
                        if long_value is not None and short_value is not None:
                            current_value = float(short_value - long_value)

                    if current_value is not None and current_value > 0:
                        await self.update_position_value(position.position_id, current_value)
                    else:
                        log.warning(f"   Could not resolve chain quote for position {position.position_id}, keeping existing value")
                except Exception as e:
                    log.error(f"Error updating position {position.position_id} with real prices: {e}")
                    continue

            legs_resolved_from_chain = bool(legs_expected > 0 and legs_resolved == legs_expected)
            log.info(
                "OPTION_CHAIN_USAGE | symbol=%s | legs_resolved_from_chain=%s | quote_calls_saved=%d",
                symbol,
                str(legs_resolved_from_chain).lower(),
                int(quote_calls_saved),
            )
            log.info(
                "OPTION_CHAIN_ONLY_MODE | symbol=%s | chain_used=true | quote_calls_blocked=true",
                symbol,
            )

        log.debug(f"✅ Updated positions with real-time options prices")
    
    async def auto_partial_profit(
        self,
        position_id: str,
        target_name: str = 'first_target_60pct'
    ) -> Optional[Dict[str, Any]]:
        """
        Auto-partial profit: Automated exit strategy
        
        Automated Exit Strategy:
        - +60% → sell 50% (first target)
        - +120% → sell 25% (second target)
        - Runner trails until VWAP/ORB reclaim or time cutoff
        
        Args:
            position_id: Position ID
            target_name: Target name ('first_target_60pct' or 'second_target_120pct')
            
        Returns:
            Dictionary with partial execution details or None
        """
        if not self.auto_partial_enabled:
            return None
        
        if position_id not in self.positions:
            return None
        
        position = self.positions[position_id]
        
        # Determine sell percentage based on target
        if target_name == 'first_target_60pct':
            sell_pct = self.exit_manager.first_profit_sell_pct  # 50%
            target_pct = self.exit_manager.first_profit_target_pct  # +60%
        elif target_name == 'second_target_120pct':
            sell_pct = self.exit_manager.second_profit_sell_pct  # 25%
            target_pct = self.exit_manager.second_profit_target_pct  # +120%
        else:
            # Legacy fallback
            sell_pct = self.partial_profit_pct
            target_pct = 0.50
        
        # Check if profit target reached
        entry_price = position.entry_price
        current_value = position.current_value
        
        # Calculate P&L based on position type
        if position.position_type == 'credit_spread':
            # For credit spreads: profit = (entry_price - current_value) / entry_price
            # Entry price = credit received, current_value = cost to close
            # Profit when current_value decreases (spread expires worthless)
            pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
        else:
            # For debit spreads and lottos: profit = (current_value - entry_price) / entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
        
        if pnl_pct < target_pct:
            return None  # Profit target not reached
        
        # Calculate partial quantity
        partial_quantity = int(position.quantity * sell_pct)
        
        # Validate partial quantity
        if partial_quantity <= 0:
            log.warning(f"⚠️ Partial quantity calculation resulted in 0 or negative: {partial_quantity} (quantity: {position.quantity}, sell_pct: {sell_pct})")
            return None
        
        # Ensure we don't try to close more than available
        if partial_quantity >= position.quantity:
            log.warning(f"⚠️ Partial quantity ({partial_quantity}) >= position quantity ({position.quantity}), adjusting to close all")
            partial_quantity = position.quantity
        
        # Calculate profit for partial close
        current_profit = position.unrealized_pnl
        partial_profit = current_profit * sell_pct
        
        log.info(f"💰 AUTOMATED EXIT: Position {position_id}")
        log.info(f"   Target: {target_name} (+{target_pct*100:.0f}%)")
        log.info(f"   Selling: {partial_quantity}/{position.quantity} contracts ({sell_pct*100:.0f}%)")
        log.info(f"   Profit: ${partial_profit:.2f} (${current_profit:.2f} total)")
        
        # Demo Mode: Update position directly
        if self.demo_mode and self.mock_executor:
            # Demo mode: Just update position tracking
            position.quantity -= partial_quantity
            position.realized_pnl += partial_profit
            position.status = 'partial'
            log.info(f"🎮 DEMO: Partial close executed (simulated)")
        
        # Live Mode: Execute partial close via ETrade API
        elif not self.demo_mode and self.etrade_options_api:
            try:
                # Execute partial close via ETrade API
                p_plan = resolve_options_exit_plan("profit_target", exit_enum_value="profit_target", details={})
                p_ot = "LIMIT" if smart_execution_enabled() and str(p_plan.order_type).upper() == "LIMIT" else "MARKET"
                close_response = await self.etrade_options_api.partial_close_position(
                    position=position,
                    partial_quantity=partial_quantity,
                    exit_price=current_value,  # Use current value as exit price
                    order_type=p_ot,
                )
                
                if close_response:
                    # Update position after successful partial close
                    position.quantity -= partial_quantity
                    position.realized_pnl += partial_profit
                    position.status = 'partial'
                    log.info(f"✅ LIVE: Partial close executed via ETrade API")
                    log.info(f"   Closed: {partial_quantity} contracts, Remaining: {position.quantity}")
                else:
                    log.error(f"❌ LIVE: Failed to execute partial close via ETrade API")
                    # Don't update position if API call failed
                    return None
                    
            except Exception as e:
                log.error(f"❌ LIVE: Error executing partial close via ETrade API: {e}", exc_info=True)
                # Don't update position if API call failed
                return None
        
        # Fallback: Update position tracking (for cases where API is not available)
        else:
            position.quantity -= partial_quantity
            position.realized_pnl += partial_profit
            position.status = 'partial'
            log.warning(f"⚠️ Partial close executed (fallback mode - no broker API call)")
        
        partial_result = {
            'position_id': position_id,
            'target_name': target_name,
            'target_pct': target_pct,
            'partial_quantity': partial_quantity,
            'remaining_quantity': position.quantity,
            'realized_pnl': position.realized_pnl,
            'partial_profit': partial_profit,
            'status': position.status
        }
        
        # Send Partial Profit alert (Rev 00206)
        if self.alert_manager:
            try:
                position_dict = position.to_dict()
                mode = "DEMO" if self.demo_mode else "LIVE"
                await self.alert_manager.send_options_partial_profit_alert(
                    position=position_dict,
                    partial_details=partial_result,
                    mode=mode
                )
                log.info(f"✅ Partial Profit alert sent for position {position_id}")
            except Exception as alert_error:
                log.error(f"Failed to send Partial Profit alert: {alert_error}")
        
        return partial_result
    
    def get_open_positions(self) -> List[OptionsPosition]:
        """Get all open positions"""
        # Demo mode source of truth is mock executor state.
        if self.demo_mode and self.mock_executor:
            return self.mock_executor.get_open_positions()
        # Include any non-closed book row so fresh LIVE fills stay visible to monitors
        # even if a broker adapter sets a non-canonical status string.
        out: List[OptionsPosition] = []
        for p in self.positions.values():
            st = str(getattr(p, "status", "") or "").strip().lower()
            if st and st != "closed":
                out.append(p)
        return out
    
    def get_position(self, position_id: str) -> Optional[OptionsPosition]:
        """Get position by ID"""
        if self.demo_mode and self.mock_executor:
            pos = self.mock_executor.get_position(position_id)
            if pos:
                return pos
        return self.positions.get(position_id)
    
    async def monitor_positions(
        self,
        market_data_provider: Optional[Any] = None,
        orb_data_provider: Optional[Any] = None
    ) -> List[ExitSignal]:
        """
        Monitor all open positions and check exit conditions
        
        Args:
            market_data_provider: Function/provider to get current market data
            orb_data_provider: Function/provider to get ORB data
            
        Returns:
            List of ExitSignal objects for positions that should be closed
        """
        exit_signals = []
        open_positions = self.get_open_positions()
        
        if not open_positions:
            return exit_signals
        
        log.warning(
            "OPTIONS_STEALTH_FAILSAFE | stage=legacy_monitor_positions_invoked | "
            "mode=fallback_only | open_positions=%d",
            len(open_positions),
        )
        
        for position in open_positions:
            try:
                # Get current market data
                if market_data_provider:
                    market_data = await market_data_provider(position.symbol)
                else:
                    # Fallback: Use position current_value
                    market_data = {
                        'current_price': position.current_value,
                        'current_value': position.current_value,
                        'vwap': None,
                        'bid_ask_spread_pct': 0.0,
                        'momentum': 0.0
                    }

                # Keep tracked position value current before evaluating exits.
                latest_value = market_data.get('current_value') if isinstance(market_data, dict) else None
                try:
                    latest_value = float(latest_value) if latest_value is not None else None
                except (TypeError, ValueError):
                    latest_value = None
                if latest_value is not None and latest_value > 0:
                    if self.demo_mode and self.mock_executor:
                        await self.mock_executor.update_position_value(position.position_id, latest_value)
                    else:
                        await self.update_position_value(position.position_id, latest_value)
                
                # Get ORB data
                orb_data = None
                if orb_data_provider:
                    orb_data = await orb_data_provider(position.symbol)
                
                exit_signal = self.exit_manager.evaluate_emergency_only(
                    position=position,
                    current_value=position.current_value,
                    market_data=market_data,
                    stale_quote=False,
                    force_eod=False,
                )
                
                if exit_signal:
                    exit_signals.append(exit_signal)
                    log.info(f"✅ Exit signal generated for position {position.position_id}: {exit_signal.reason.value}")
                    
                    # Record trade performance for Priority Optimizer
                    if self.priority_collector:
                        try:
                            # Calculate peak values (use current value as peak if higher)
                            peak_value = max(position.current_value, position.entry_price)
                            if position.position_type == 'credit_spread':
                                peak_pnl_pct = (position.entry_price - peak_value) / position.entry_price if position.entry_price > 0 else 0.0
                            else:
                                peak_pnl_pct = (peak_value - position.entry_price) / position.entry_price if position.entry_price > 0 else 0.0
                            
                            self.priority_collector.record_trade_performance(
                                position=position,
                                exit_signal=exit_signal,
                                peak_value=peak_value,
                                peak_pnl_pct=peak_pnl_pct
                            )
                        except Exception as e:
                            log.error(f"Failed to record trade performance: {e}")
                    
            except Exception as e:
                log.error(f"Error monitoring position {position.position_id}: {e}", exc_info=True)
        
        return exit_signals

    async def close_position_with_data(
        self,
        *,
        position_id: str,
        exit_price: float,
        exit_reason: str,
        exit_time: Optional[datetime] = None,
        details: Optional[Dict[str, Any]] = None,
        external_position_data: Optional[Dict[str, Any]] = None,
        suppress_exit_alert: bool = False,
    ) -> Optional[Any]:
        """Unified close path for both ORB 0DTE and Trendline 0DTE."""
        position = self.get_position(position_id)
        if not position and not isinstance(external_position_data, dict):
            return None
        entry_price = float(
            (getattr(position, "entry_price", 0.0) if position else external_position_data.get("entry_price", 0.0)) or 0.0
        )
        qty = int(max(1, int((getattr(position, "quantity", 1) if position else external_position_data.get("quantity", 1)) or 1)))
        structural_type = None
        if position:
            structural_type = getattr(position, "normalized_position_type", None)
        if not structural_type and isinstance(external_position_data, dict):
            structural_type = external_position_data.get("position_type")
        structural_key = str(structural_type or "").strip().lower()
        ex = float(exit_price)
        if structural_key == "credit_spread":
            pnl_pct = ((entry_price - ex) / entry_price) if entry_price > 0 else 0.0
            pnl_dollar = (entry_price - ex) * qty * 100.0
        else:
            pnl_pct = ((ex - entry_price) / entry_price) if entry_price > 0 else 0.0
            pnl_dollar = (ex - entry_price) * qty * 100.0
        log.info(
            "OPTIONS_CLOSE_PNL_AUDIT | position_id=%s | position_type=%s | entry_price=%.6f | exit_price=%.6f | "
            "pnl_pct=%.6f | pnl_dollar=%.2f",
            position_id,
            structural_key or str(structural_type or "unknown"),
            entry_price,
            ex,
            float(pnl_pct),
            float(pnl_dollar),
        )
        signal_time = exit_time or datetime.now(timezone.utc)
        if not position and isinstance(external_position_data, dict):
            return {
                "position_id": position_id,
                "exit_price": float(exit_price),
                "exit_reason": str(exit_reason),
                "exit_time": signal_time,
                "entry_price": entry_price,
                "quantity": qty,
                "pnl_pct": float(pnl_pct),
                "pnl_dollar": float(pnl_dollar),
                "details": details or {},
                "external": True,
            }
        signal = ExitSignal(
            position_id=position_id,
            reason=ExitReason.FAIL_SAFE,
            exit_price=float(exit_price),
            exit_time=signal_time,
            pnl_pct=float(pnl_pct),
            pnl_dollar=float(pnl_dollar),
            details=details or {},
        )
        return await self.execute_exit(
            signal,
            exit_reason_override=exit_reason,
            suppress_exit_alert=bool(suppress_exit_alert),
        )
    
    async def execute_exit(
        self,
        exit_signal: ExitSignal,
        exit_reason_override: Optional[str] = None,
        *,
        suppress_exit_alert: bool = False,
    ) -> Optional[OptionsPosition]:
        """
        Execute exit for a position based on exit signal
        
        Args:
            exit_signal: ExitSignal object
            exit_reason_override: Override exit reason string
            
        Returns:
            Closed OptionsPosition or None if execution failed
        """
        position_id = exit_signal.position_id

        # Demo mode source of truth is mock executor state.
        if self.demo_mode and self.mock_executor:
            position = self.mock_executor.get_position(position_id)
            if not position:
                log.warning(f"Position {position_id} not found for exit")
                return None
        else:
            if position_id not in self.positions:
                log.warning(f"Position {position_id} not found for exit")
                return None
            position = self.positions[position_id]
        
        # Close position
        if self.demo_mode and self.mock_executor:
            # Demo Mode: Use mock executor
            closed_position = await self.mock_executor.close_position(
                position_id=position_id,
                exit_price=exit_signal.exit_price,
                reason=exit_reason_override or exit_signal.reason.value
            )
            
            if closed_position:
                # Remove from positions dict
                if position_id in self.positions:
                    del self.positions[position_id]
                
                log.info(f"✅ Position {position_id} closed via exit signal: {exit_signal.reason.value}")
                log.info(f"   P&L: {exit_signal.pnl_pct*100:.1f}% (${exit_signal.pnl_dollar:.2f})")
                
                # Send Position Exit alert (Rev 00206); batch closes use suppress_exit_alert + aggregated alert.
                if self.alert_manager and not suppress_exit_alert:
                    try:
                        # Calculate holding time
                        holding_time_minutes = int((exit_signal.exit_time - position.entry_time).total_seconds() / 60)
                        
                        position_dict = closed_position.to_dict()
                        position_dict["current_value"] = float(exit_signal.exit_price)
                        mode = "DEMO" if self.demo_mode else "LIVE"
                        entry_px = float(closed_position.entry_price)
                        exit_px = float(exit_signal.exit_price)
                        structural = str(closed_position.normalized_position_type or closed_position.position_type or "").lower()
                        if structural == "credit_spread" or closed_position.position_type == "credit_spread":
                            pnl_frac = (entry_px - exit_px) / entry_px if entry_px > 0 else 0.0
                        else:
                            pnl_frac = (exit_px - entry_px) / entry_px if entry_px > 0 else 0.0
                        acct_bal = self._snapshot_alert_account_balance()
                        
                        # Check if this is a runner exit
                        if exit_signal.reason.value == 'runner_target':
                            await self.alert_manager.send_options_runner_exit_alert(
                                position=position_dict,
                                exit_signal=exit_signal.to_dict() if hasattr(exit_signal, 'to_dict') else {
                                    'exit_price': exit_signal.exit_price,
                                    'pnl_pct': exit_signal.pnl_pct,
                                    'pnl_dollar': exit_signal.pnl_dollar,
                                    'details': exit_signal.details
                                },
                                mode=mode
                            )
                        else:
                            await self.alert_manager.send_options_position_exit_alert(
                                position=position_dict,
                                exit_price=exit_signal.exit_price,
                                exit_reason=exit_reason_override or exit_signal.reason.value,
                                holding_time_minutes=holding_time_minutes,
                                mode=mode,
                                realized_pnl_override=float(closed_position.realized_pnl),
                                pnl_pct_fraction_override=float(pnl_frac),
                                account_balance_override=acct_bal,
                            )
                        log.info(f"✅ Options Position Exit alert sent for {position_id}")
                    except Exception as alert_error:
                        log.error(f"Failed to send Options Position Exit alert: {alert_error}")
                
                return closed_position
        
        elif not self.demo_mode and self.etrade_options_api:
            # Live Mode: Use ETrade API
            try:
                log.info(f"💰 LIVE: Closing position {position_id} via ETrade API")
                log.info(f"   Exit reason: {exit_signal.reason.value}")
                log.info(f"   Exit price: ${exit_signal.exit_price:.2f}")
                
                rs = exit_reason_override or exit_signal.reason.value
                plan = resolve_options_exit_plan(
                    exit_reason_override,
                    exit_enum_value=exit_signal.reason.value,
                    details=exit_signal.details,
                )
                ot = plan.order_type
                ex_px = float(exit_signal.exit_price)

                if smart_execution_enabled():
                    if position.position_type == "lotto" and position.lotto_contract:
                        lc = position.lotto_contract
                        ok_ll, sp_ll = last_look_option_spread_ok(
                            float(lc.bid or 0.0),
                            float(lc.ask or 0.0),
                            float(lc.mid_price or 0.0),
                        )
                        if not ok_ll:
                            if log_execution_event and build_execution_payload:
                                log_execution_event(
                                    "LAST_LOOK_REJECT",
                                    str(position.strategy_type or "ORB_0DTE"),
                                    build_execution_payload(
                                        symbol=position.symbol,
                                        trade_id=position_id,
                                        strategy=str(position.strategy_type or "ORB_0DTE"),
                                        order_type=str(ot),
                                        spread_width_pct=sp_ll,
                                        exit_reason=str(rs),
                                        exit_urgency=plan.exit_urgency,
                                        exit_execution_style=plan.exit_execution_style,
                                        extra={"path": "options_close_single_leg"},
                                    ),
                                )
                            if plan.allow_market_fallback:
                                ot = "MARKET"
                                ex_px = None
                    elif position.position_type == "debit_spread" and position.debit_spread:
                        ds = position.debit_spread
                        lb = float(getattr(ds.long_contract, "bid", 0.0) or 0.0)
                        la = float(getattr(ds.long_contract, "ask", 0.0) or 0.0)
                        sb = float(getattr(ds.short_contract, "bid", 0.0) or 0.0)
                        sa = float(getattr(ds.short_contract, "ask", 0.0) or 0.0)
                        if lb > 0 and la > 0 and sb > 0 and sa > 0:
                            net_mid = max(0.01, float(ds.long_contract.mid_price) - float(ds.short_contract.mid_price))
                            spread_w = max(0.0, (la - lb) + (sa - sb))
                            sp_pct = (spread_w / net_mid * 100.0) if net_mid > 0 else 999.0
                            cap_sp = float(last_look_max_spread_pct_default())
                            if sp_pct > cap_sp * 3.0:
                                if log_execution_event and build_execution_payload:
                                    log_execution_event(
                                        "LAST_LOOK_REJECT",
                                        str(position.strategy_type or "ORB_0DTE"),
                                        build_execution_payload(
                                            symbol=position.symbol,
                                            trade_id=position_id,
                                            strategy=str(position.strategy_type or "ORB_0DTE"),
                                            spread_width_pct=sp_pct,
                                            exit_reason=str(rs),
                                            exit_urgency=plan.exit_urgency,
                                            extra={"path": "options_close_debit_spread", "threshold_pct": cap_sp * 3.0},
                                        ),
                                    )
                                if plan.allow_market_fallback:
                                    ot = "MARKET"
                                    ex_px = None

                if str(ot).upper() == "MARKET":
                    broker_ot = "MARKET"
                    broker_px = None
                else:
                    broker_ot = "LIMIT"
                    broker_px = ex_px

                close_response = await self.etrade_options_api.close_position(
                    position=position,
                    exit_price=broker_px,
                    order_type=broker_ot,
                )
                
                if close_response:
                    if log_execution_event and build_execution_payload:
                        log_execution_event(
                            "EXECUTION_FILL_SUMMARY",
                            str(position.strategy_type or "ORB_0DTE"),
                            build_execution_payload(
                                symbol=position.symbol,
                                trade_id=position_id,
                                strategy=str(position.strategy_type or "ORB_0DTE"),
                                order_type=str(broker_ot),
                                fill_ts=datetime.now(timezone.utc).isoformat(),
                                fill_price=float(exit_signal.exit_price),
                                exit_reason=str(rs),
                                exit_urgency=plan.exit_urgency,
                                exit_execution_style=plan.exit_execution_style,
                                extra={"path": "options_close"},
                            ),
                        )
                    # Update position status
                    position.status = 'closed'
                    position.realized_pnl = exit_signal.pnl_dollar
                    position.unrealized_pnl = 0.0
                    position.current_value = float(exit_signal.exit_price)
                    
                    # Remove from positions dict
                    if position_id in self.positions:
                        del self.positions[position_id]
                    
                    log.info(f"✅ Live position {position_id} closed via ETrade API")
                    log.info(f"   P&L: {exit_signal.pnl_pct*100:.1f}% (${exit_signal.pnl_dollar:.2f})")
                    
                    # Send Position Exit alert (Rev 00206)
                    if self.alert_manager and not suppress_exit_alert:
                        try:
                            # Calculate holding time
                            holding_time_minutes = int((exit_signal.exit_time - position.entry_time).total_seconds() / 60)
                            
                            position_dict = position.to_dict()
                            mode = "LIVE"
                            acct_bal = self._snapshot_alert_account_balance()
                            
                            # Check if this is a runner exit
                            if exit_signal.reason.value == 'runner_target':
                                await self.alert_manager.send_options_runner_exit_alert(
                                    position=position_dict,
                                    exit_signal=exit_signal.to_dict() if hasattr(exit_signal, 'to_dict') else {
                                        'exit_price': exit_signal.exit_price,
                                        'pnl_pct': exit_signal.pnl_pct,
                                        'pnl_dollar': exit_signal.pnl_dollar,
                                        'details': exit_signal.details
                                    },
                                    mode=mode
                                )
                            else:
                                await self.alert_manager.send_options_position_exit_alert(
                                    position=position_dict,
                                    exit_price=exit_signal.exit_price,
                                    exit_reason=exit_reason_override or exit_signal.reason.value,
                                    holding_time_minutes=holding_time_minutes,
                                    mode=mode,
                                    realized_pnl_override=float(exit_signal.pnl_dollar),
                                    pnl_pct_fraction_override=float(exit_signal.pnl_pct),
                                    account_balance_override=acct_bal,
                                )
                            log.info(f"✅ Options Position Exit alert sent for {position_id}")
                        except Exception as alert_error:
                            log.error(f"Failed to send Options Position Exit alert: {alert_error}")
                    
                    return position
                else:
                    log.error(f"Failed to close position {position_id} via ETrade API")
                    return None
                
            except Exception as e:
                log.error(f"Error closing position via ETrade API: {e}", exc_info=True)
                return None
        
        # Fallback: Update position status
        position.status = 'closed'
        position.realized_pnl = exit_signal.pnl_dollar
        position.unrealized_pnl = 0.0
        
        log.info(f"Position {position_id} marked as closed: {exit_signal.reason.value}")
        
        return position
    
    async def close_all_positions(
        self,
        reason: str = "EOD_CLOSE",
        *,
        flatten_status: Optional[Dict[str, Any]] = None,
    ) -> List[OptionsPosition]:
        """
        Close all open options positions (typically at EOD)
        
        Args:
            reason: Reason for closing all positions (e.g., "EOD_CLOSE")
            
        Returns:
            List of closed OptionsPosition objects
        """
        open_positions = self.get_open_positions()
        n_open = len(open_positions or [])
        if flatten_status is not None:
            flatten_status["no_positions_found"] = bool(n_open == 0)
            flatten_status.setdefault("positions_closed", 0)
            flatten_status.setdefault("positions_failed", 0)
            flatten_status.setdefault("positions_missing", 0)
        if not open_positions:
            if flatten_status is not None:
                flatten_status["positions_closed"] = 0
                flatten_status["positions_failed"] = 0
                flatten_status["positions_missing"] = 0
                flatten_status["aggregated_alert_sent"] = False
            log.info("No open options positions to close")
            return []
        
        log.info(f"Closing {len(open_positions)} open options positions: {reason}")
        closed_positions = []
        failed_closes = 0
        
        for position in open_positions:
            try:
                # Exit mark: prefer hydrated mark; else infer from open snapshot (EOD often runs with current_value=0).
                current_value = float(getattr(position, "current_value", 0.0) or 0.0)
                if current_value <= 0.0:
                    current_value = _infer_nonzero_option_per_share_mark(position)
                if current_value <= 0.0:
                    current_value = float(getattr(position, "entry_price", 0.0) or 0.0)
                
                # Create exit signal
                from .options_exit_manager import ExitSignal, ExitReason
                # Use module-level datetime import (line 17)
                
                exit_reason_enum = ExitReason.EOD_CLOSE if reason == "EOD_CLOSE" else ExitReason.HEALTH_EMERGENCY
                
                # Calculate P&L based on position type
                if position.position_type == 'credit_spread':
                    # For credit spreads: profit = (entry_price - current_value) / entry_price
                    # Entry price = credit received, current_value = cost to close
                    pnl_pct = (position.entry_price - current_value) / position.entry_price if position.entry_price > 0 else 0.0
                    pnl_dollar = (position.entry_price - current_value) * position.quantity * 100
                else:
                    # For debit spreads and lottos: profit = (current_value - entry_price) / entry_price
                    pnl_pct = (current_value - position.entry_price) / position.entry_price if position.entry_price > 0 else 0.0
                    pnl_dollar = (current_value - position.entry_price) * position.quantity * 100
                
                exit_signal = ExitSignal(
                    position_id=position.position_id,
                    reason=exit_reason_enum,
                    exit_price=current_value,
                    exit_time=datetime.now(),
                    pnl_pct=pnl_pct,
                    pnl_dollar=pnl_dollar,
                    details={'reason': reason}
                )
                
                # Execute exit
                closed_position = await self.execute_exit(
                    exit_signal,
                    exit_reason_override=reason,
                    suppress_exit_alert=True,
                )
                if closed_position:
                    closed_positions.append(closed_position)
                else:
                    failed_closes += 1
                    
            except Exception as e:
                failed_closes += 1
                log.error(f"Error closing position {position.position_id}: {e}", exc_info=True)
        
        log.info(f"✅ Closed {len(closed_positions)}/{len(open_positions)} options positions")

        remaining = len(self.get_open_positions() or [])
        if flatten_status is not None:
            flatten_status["positions_closed"] = int(len(closed_positions))
            flatten_status["positions_failed"] = int(failed_closes)
            flatten_status["positions_missing"] = int(remaining)

        # Send aggregated exit alert if positions were closed
        if closed_positions and self.alert_manager:
            try:
                mode = "DEMO" if self.demo_mode else "LIVE"
                closed_positions_dict = [p.to_dict() for p in closed_positions]
                batch_balance = self._snapshot_alert_account_balance()
                agg_ok = await self.alert_manager.send_options_aggregated_exit_alert(
                    closed_positions=closed_positions_dict,
                    exit_reason=reason,
                    mode=mode,
                    account_balance=batch_balance,
                    eod_flatten_status=flatten_status,
                )
                if flatten_status is not None:
                    flatten_status["aggregated_alert_sent"] = bool(agg_ok)
                log.info(
                    "OPTIONS_AGGREGATED_EXIT_ALERT_STATUS | exit_reason=%s | sent=%s | positions_closed=%d | positions_missing=%d | positions_failed=%d | no_positions_found=%s",
                    str(reason),
                    str(bool(agg_ok)).lower(),
                    int(flatten_status.get("positions_closed", len(closed_positions)) if flatten_status else len(closed_positions)),
                    int(flatten_status.get("positions_missing", remaining) if flatten_status else remaining),
                    int(flatten_status.get("positions_failed", failed_closes) if flatten_status else failed_closes),
                    str(bool(flatten_status.get("no_positions_found", False)) if flatten_status else False).lower(),
                )
                log.info(f"✅ Options aggregated exit alert sent for {len(closed_positions)} positions")
            except Exception as e:
                if flatten_status is not None:
                    flatten_status["aggregated_alert_sent"] = False
                log.error(f"Failed to send options aggregated exit alert: {e}")
        elif flatten_status is not None:
            flatten_status["aggregated_alert_sent"] = False
        
        return closed_positions
    
    def reset_daily(self):
        """Reset daily state"""
        self.positions = {}
        log.info("Options Trading Executor daily reset complete")

