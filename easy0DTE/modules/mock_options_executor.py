#!/usr/bin/env python3
"""
Mock Options Trading Executor for Demo Mode
===========================================

Simulates options trade execution, P&L tracking, and position management
for 0DTE Strategy in Demo Mode. Mirrors MockTradingExecutor pattern from ORB Strategy.

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import json
import os
from dataclasses import dataclass, asdict, fields

from .options_trading_executor import OptionsPosition, DebitSpread, OptionContract
from .options_chain_manager import CreditSpread
from .options_chain_manager import OptionsChainManager
from .options_execution_normalize import (
    build_normalized_metadata_credit_spread,
    build_normalized_metadata_debit_spread,
    build_normalized_metadata_single_leg,
    log_metadata_normalized,
    log_position_type_normalized,
)

log = logging.getLogger(__name__)

# Aligns with modules.prime_alert_manager.ALERT_LABEL_EASY_ORB_0DTE_OPTIONS (avoid import cycle from easy0DTE)
_EASY_ORB_0DTE_ALERT = "Easy ORB 0DTE"


@dataclass
class MockOptionsPosition:
    """Mock options position for Demo Mode"""
    position_id: str
    symbol: str
    position_type: str  # 'debit_spread', 'credit_spread', or 'lotto'
    entry_price: float
    entry_time: datetime
    quantity: int = 1
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    status: str = 'open'  # 'open', 'partial', 'closed'
    debit_spread: Optional[Dict[str, Any]] = None
    credit_spread: Optional[Dict[str, Any]] = None
    lotto_contract: Optional[Dict[str, Any]] = None
    # Shared normalized schema (additive); full blob under normalized_options.
    metadata: Optional[Dict[str, Any]] = None
    # Selector route (momentum_scalper, debit_spread, …); mirrors OptionsPosition.strategy_type.
    strategy_type: str = ""
    # Per-share tradable value at open (matches entry_price for ORB demo).
    entry_value: float = 0.0
    entry_debit: Optional[float] = None
    entry_credit: Optional[float] = None
    # Demo credit spread: margin encumbered while position is open.
    reserved_margin: float = 0.0
    
    # Demo-specific tracking
    max_favorable: float = 0.0
    max_unfavorable: float = 0.0

    @property
    def normalized_options(self) -> Dict[str, Any]:
        meta = self.metadata if isinstance(self.metadata, dict) else {}
        norm = meta.get("normalized_options")
        return norm if isinstance(norm, dict) else {}

    @property
    def normalized_position_type(self) -> str:
        structural = str(self.normalized_options.get("position_type", "") or "").strip()
        return structural if structural else str(self.position_type or "")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'position_id': self.position_id,
            'symbol': self.symbol,
            'position_type': self.position_type,
            'debit_spread': self.debit_spread,
            'credit_spread': self.credit_spread,
            'lotto_contract': self.lotto_contract,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time.isoformat(),
            'quantity': self.quantity,
            'current_value': self.current_value,
            'unrealized_pnl': self.unrealized_pnl,
            'realized_pnl': self.realized_pnl,
            'status': self.status,
            'max_favorable': self.max_favorable,
            'max_unfavorable': self.max_unfavorable,
            'metadata': self.metadata,
            'entry_value': self.entry_value,
            'entry_debit': self.entry_debit,
            'entry_credit': self.entry_credit,
            'reserved_margin': self.reserved_margin,
            'strategy_type': self.strategy_type,
        }


def _deserialize_mock_position_row(pos_data: Dict[str, Any]) -> MockOptionsPosition:
    """Build MockOptionsPosition from persisted dict; drops unknown keys."""
    row = dict(pos_data)
    if "entry_time" in row and isinstance(row["entry_time"], str):
        row["entry_time"] = datetime.fromisoformat(row["entry_time"])
    allowed = {f.name for f in fields(MockOptionsPosition)}
    clean = {k: v for k, v in row.items() if k in allowed}
    return MockOptionsPosition(**clean)


class MockOptionsExecutor:
    """
    Mock Options Trading Executor for Demo Mode
    
    Simulates options trade execution, P&L tracking, and position management
    for 0DTE Strategy. Similar to MockTradingExecutor for ETF trading.
    """
    
    def __init__(self, alert_manager=None):
        """
        Initialize Mock Options Executor
        
        Args:
            alert_manager: Optional alert manager for notifications
        """
        self.alert_manager = alert_manager
        self.active_positions: Dict[str, MockOptionsPosition] = {}
        self.closed_positions: List[MockOptionsPosition] = []
        
        # Rev 00217: Demo account balance (starts at $5,000 for 0DTE Strategy, grows with profits)
        # Separate from ORB Strategy's $1,000 account
        self.account_balance = 5000.0
        self.starting_balance = 5000.0
        # Demo: cash includes collected option premium; encumbered margin for credit spreads.
        self.reserved_margin_total = 0.0
        
        # Daily stats tracking
        self.daily_stats = {
            'positions_opened': 0,
            'positions_closed': 0,
            'total_pnl': 0.0,
            'winning_trades': 0,
            'losing_trades': 0,
            'best_trade': 0.0,
            'worst_trade': 0.0
        }
        self.current_trading_date = datetime.utcnow().date()
        
        # Weekly stats tracking
        self.weekly_stats = {
            'positions_opened': 0,
            'positions_closed': 0,
            'total_pnl': 0.0,
            'winning_trades': 0,
            'losing_trades': 0,
            'best_trade': 0.0,
            'worst_trade': 0.0,
            'week_start_date': self._get_week_start_date()
        }
        self._check_and_reset_weekly_stats()
        
        # Mock data file (local backup)
        self.mock_data_file = "data/mock_options_history.json"
        # Rev 00217: GCS path for persistence (separate from ORB Strategy's account)
        self.gcs_path = "demo_account/mock_options_history.json"
        self._load_mock_data()
        log.info("🎮 Mock Options Executor initialized (%s):", _EASY_ORB_0DTE_ALERT)
        log.info("  - Starting balance: $%.2f", self.starting_balance)
        log.info("  - Current balance: $%.2f", self.account_balance)
        log.info("  - Active positions: %d", len(self.active_positions))

    @staticmethod
    def _get_orb_modules_path() -> Optional[str]:
        """
        Resolve ORB Strategy modules path for shared helpers (e.g., gcs_persistence).
        Avoids duplicating a brittle path join that can break in Cloud Run/container layouts.
        """
        try:
            strategy_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            modules_path = os.path.join(strategy_root, "modules")
            if os.path.isdir(modules_path):
                return modules_path
        except Exception:
            pass
        return None

    async def _send_demo_system_alert(self, title: str, message: str) -> None:
        """
        Best-effort demo alert adapter.

        PrimeAlertManager does not expose send_alert(); use send_system_alert when available,
        and gracefully fall back to send_telegram_alert or logging only.
        """
        if not self.alert_manager:
            return
        try:
            if hasattr(self.alert_manager, 'send_system_alert'):
                await self.alert_manager.send_system_alert(title=title, message=message)
            elif hasattr(self.alert_manager, 'send_telegram_alert'):
                await self.alert_manager.send_telegram_alert(f"{title}\n{message}")
            else:
                log.debug(f"Demo alert skipped (unsupported manager): {title}")
        except Exception as e:
            log.warning(f"Demo alert dispatch failed ({title}): {e}")

    def available_balance(self) -> float:
        """Demo cash available for new debits after reserved credit-spread margin."""
        return max(0.0, float(self.account_balance) - float(self.reserved_margin_total))

    def net_liquidation_value(self) -> float:
        """Cash ledger (includes premium collected; margin still reserved until close)."""
        return float(self.account_balance)
    
    def _get_week_start_date(self) -> datetime.date:
        """Get Monday of current week"""
        today = datetime.utcnow().date()
        days_since_monday = today.weekday()
        return today - timedelta(days=days_since_monday)
    
    def _check_and_reset_weekly_stats(self):
        """Reset weekly stats if new week"""
        today = datetime.utcnow().date()
        if today > self.weekly_stats['week_start_date']:
            log.info("📅 New week detected - resetting weekly stats")
            self.weekly_stats = {
                'positions_opened': 0,
                'positions_closed': 0,
                'total_pnl': 0.0,
                'winning_trades': 0,
                'losing_trades': 0,
                'best_trade': 0.0,
                'worst_trade': 0.0,
                'week_start_date': self._get_week_start_date()
            }
    
    def _load_mock_data(self):
        """
        Load existing mock trading data from GCS (primary) or local file (fallback)
        Rev 00217: Added GCS persistence similar to ORB Strategy
        """
        data = None
        loaded_from = None
        
        # Try GCS first (persists across Cloud Run redeployments)
        try:
            # Import here to avoid circular dependencies
            import sys
            modules_path = self._get_orb_modules_path()
            if modules_path:
                sys.path.insert(0, modules_path)
                from gcs_persistence import get_gcs_persistence
                gcs = get_gcs_persistence()
                
                if gcs.enabled and gcs.file_exists(self.gcs_path):
                    json_content = gcs.read_string(self.gcs_path)
                    if json_content:
                        data = json.loads(json_content)
                        loaded_from = "GCS"
                        log.info(f"✅ Loaded 0DTE mock trading data from GCS: {self.gcs_path}")
        except Exception as gcs_error:
            log.debug(f"GCS load failed (will try local): {gcs_error}")
        
        # Fallback to local file if GCS not available or failed
        if data is None:
            try:
                if os.path.exists(self.mock_data_file):
                    with open(self.mock_data_file, 'r') as f:
                        data = json.load(f)
                    loaded_from = "local file"
                    log.info(f"✅ Loaded 0DTE mock trading data from local file: {self.mock_data_file}")
            except Exception as local_error:
                log.debug(f"Local file load failed: {local_error}")
        
        # Process loaded data
        if data:
            try:
                # Rev 00217: Use $5,000 as default if not found in data
                self.account_balance = data.get('account_balance', 5000.0)
                self.starting_balance = data.get('starting_balance', 5000.0)
                self.reserved_margin_total = float(data.get("reserved_margin_total", 0.0) or 0.0)
                
                # Load closed positions
                loaded_position_ids = set()
                for pos_data in data.get('closed_positions', []):
                    position_id = pos_data.get('position_id', '')
                    # Skip duplicates
                    if position_id and position_id in loaded_position_ids:
                        log.debug(f"Skipping duplicate position: {position_id}")
                        continue
                    
                    try:
                        position = _deserialize_mock_position_row(pos_data)
                        self.closed_positions.append(position)
                        if position_id:
                            loaded_position_ids.add(position_id)
                    except Exception as load_error:
                        log.warning(f"⚠️ Could not load position {position_id}: {load_error}")
                
                log.info(f"✅ Loaded 0DTE mock trading data from {loaded_from}: ${self.account_balance:.2f} balance, {len(self.closed_positions)} closed positions")
                
                # If loaded from GCS, also save to local for faster future loads
                if loaded_from == "GCS":
                    try:
                        os.makedirs(os.path.dirname(self.mock_data_file), exist_ok=True)
                        with open(self.mock_data_file, 'w') as f:
                            json.dump(data, f, indent=2, default=str)
                        log.debug(f"✅ Synced GCS data to local file for faster future loads")
                    except Exception as sync_error:
                        log.debug(f"Could not sync GCS data to local: {sync_error}")
                        
            except json.JSONDecodeError as e:
                log.warning(f"Invalid JSON in 0DTE mock trading data ({loaded_from}): {e}")
                # Reset to defaults
                self.account_balance = 5000.0
                self.starting_balance = 5000.0
                self.reserved_margin_total = 0.0
                self.closed_positions = []
            except Exception as e:
                log.warning(f"Failed to process 0DTE mock trading data from {loaded_from}: {e}")
                # Reset to defaults
                self.account_balance = 5000.0
                self.starting_balance = 5000.0
                self.reserved_margin_total = 0.0
                self.closed_positions = []
        else:
            log.info(f"📭 No existing 0DTE mock trading data found (GCS or local) - starting fresh with $5,000")
            self.account_balance = 5000.0
            self.starting_balance = 5000.0
            self.reserved_margin_total = 0.0
            self.closed_positions = []
    
    def _save_mock_data(self):
        """
        Save mock trading data to GCS (primary) and local file (backup)
        Rev 00217: Added GCS persistence similar to ORB Strategy with bidirectional merging
        """
        try:
            # Rev 00217: SAFEGUARD - Before saving, check if GCS has more positions than we have in memory
            # This prevents data loss if closed_positions was accidentally cleared
            gcs_position_count = 0
            gcs_closed_positions = []
            try:
                import sys
                modules_path = self._get_orb_modules_path()
                if modules_path:
                    sys.path.insert(0, modules_path)
                    from gcs_persistence import get_gcs_persistence
                    gcs = get_gcs_persistence()
                    
                    if gcs.enabled and gcs.file_exists(self.gcs_path):
                        json_content = gcs.read_string(self.gcs_path)
                        if json_content:
                            gcs_data = json.loads(json_content)
                            gcs_closed_positions = gcs_data.get('closed_positions', [])
                            gcs_position_count = len(gcs_closed_positions)
                            log.info(f"📊 GCS has {gcs_position_count} historical positions, memory has {len(self.closed_positions)} positions")
            except Exception as gcs_check_error:
                log.debug(f"Could not check GCS before save (non-critical): {gcs_check_error}")
            
            # Rev 00217: ALWAYS merge GCS positions with memory positions (both directions)
            # This ensures we NEVER lose historical data, even if one source is reset
            all_closed_positions = list(self.closed_positions)  # Start with current positions
            existing_position_ids = {pos.position_id for pos in self.closed_positions}
            
            # Always merge GCS positions (if any exist) to prevent data loss
            if gcs_closed_positions:
                positions_recovered = 0
                for pos_data in gcs_closed_positions:
                    position_id = pos_data.get('position_id', '')
                    if position_id and position_id not in existing_position_ids:
                        try:
                            position = _deserialize_mock_position_row(pos_data)
                            all_closed_positions.append(position)
                            existing_position_ids.add(position_id)
                            positions_recovered += 1
                            log.info(f"✅ Recovered historical position: {position.symbol} ({position_id})")
                        except Exception as merge_error:
                            log.warning(f"⚠️ Could not merge position {position_id}: {merge_error}")
                
                if positions_recovered > 0:
                    # Update self.closed_positions with merged list
                    original_count = len(self.closed_positions)
                    self.closed_positions = all_closed_positions
                    log.info(f"✅ Merged positions: Now have {len(self.closed_positions)} total positions (was {original_count}, recovered {positions_recovered} from GCS)")
            
            # Rev 00217: CRITICAL SAFEGUARD - Never save if we would lose historical positions
            if gcs_position_count > 0 and len(self.closed_positions) < gcs_position_count:
                log.error(f"🚨 CRITICAL: About to save {len(self.closed_positions)} positions, but GCS had {gcs_position_count} positions - DATA LOSS RISK!")
                log.error(f"   Attempting to recover all GCS positions before save...")
                
                # Force merge all GCS positions
                for pos_data in gcs_closed_positions:
                    position_id = pos_data.get('position_id', '')
                    # Check if we already have this position
                    already_exists = False
                    for existing_pos in self.closed_positions:
                        if (existing_pos.position_id == position_id or
                            (existing_pos.symbol == pos_data.get('symbol') and
                             abs(existing_pos.realized_pnl - pos_data.get('realized_pnl', 0)) < 0.01 and
                             existing_pos.entry_time.isoformat()[:10] == pos_data.get('entry_time', '')[:10])):
                            already_exists = True
                            break
                    
                    if not already_exists:
                        try:
                            position = _deserialize_mock_position_row(pos_data)
                            self.closed_positions.append(position)
                            log.info(f"✅ Force-recovered position: {position.symbol} ({position_id})")
                        except Exception as merge_error:
                            log.warning(f"⚠️ Could not force-merge position {position_id}: {merge_error}")
                
                log.info(f"✅ After force-merge: {len(self.closed_positions)} positions")
            
            # Prepare data structure
            data = {
                'account_balance': self.account_balance,
                'starting_balance': self.starting_balance,
                'reserved_margin_total': self.reserved_margin_total,
                'closed_positions': [pos.to_dict() for pos in self.closed_positions]  # Keep all positions (not just last 100)
            }
            
            log.info(f"💾 Saving {len(data['closed_positions'])} total positions to GCS (ensures persistence across deployments)")
            
            # Save to local file first (for backup)
            try:
                os.makedirs(os.path.dirname(self.mock_data_file), exist_ok=True)
                with open(self.mock_data_file, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
                log.debug(f"✅ Saved 0DTE mock trading data to local file: {self.mock_data_file}")
            except Exception as local_error:
                log.warning(f"⚠️ Failed to save to local file: {local_error}")
            
            # Save to GCS (primary persistence for Cloud Run)
            try:
                import sys
                modules_path = self._get_orb_modules_path()
                if modules_path:
                    sys.path.insert(0, modules_path)
                    from gcs_persistence import get_gcs_persistence
                    gcs = get_gcs_persistence()
                    
                    if gcs.enabled:
                        # Upload JSON string directly to GCS
                        json_content = json.dumps(data, indent=2, default=str)
                        if gcs.upload_string(self.gcs_path, json_content):
                            log.info(f"✅ Saved 0DTE mock trading data to GCS: {self.gcs_path} ({len(data['closed_positions'])} positions, ${data['account_balance']:.2f} balance, persists across redeployments)")
                        else:
                            log.warning(f"⚠️ Failed to upload to GCS (will retry on next save)")
                    else:
                        log.debug(f"GCS persistence disabled - only saved locally")
            except Exception as gcs_error:
                log.warning(f"⚠️ Failed to save to GCS: {gcs_error} (local file saved as backup)")
                
        except Exception as e:
            log.error(f"❌ Failed to save 0DTE mock trading data: {e}")
    
    async def execute_debit_spread(
        self,
        spread: DebitSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "debit",
    ) -> Optional[MockOptionsPosition]:
        """
        Execute debit spread in Demo Mode
        
        Args:
            spread: DebitSpread object
            quantity: Number of spreads
            
        Returns:
            MockOptionsPosition or None if execution failed
        """
        # Calculate total cost
        total_cost = spread.debit_cost * quantity * 100  # Options are per 100 shares
        
        # Check account balance
        if total_cost > self.available_balance():
            log.warning(f"🎮 DEMO: Insufficient balance for {spread.symbol} debit spread")
            log.warning(
                f"  Required: ${total_cost:.2f}, Available: ${self.available_balance():.2f} "
                f"(cash=${self.account_balance:.2f}, reserved_margin=${self.reserved_margin_total:.2f})"
            )
            return None
        
        # Simulate execution
        log.info(f"🎮 DEMO: Executing debit spread: {spread.symbol} {spread.option_type} {spread.long_strike}/{spread.short_strike}")
        log.info(f"  Quantity: {quantity}, Debit: ${spread.debit_cost:.2f}, Total Cost: ${total_cost:.2f}")
        
        # Create mock position (Rev 00232: Shortened format for alerts - symbol_date_strikes_type_microseconds)
        now = datetime.now()
        expiry_short = spread.expiry.replace('-', '')[-6:] if spread.expiry else now.strftime('%y%m%d')
        microseconds_short = now.microsecond % 1000  # Last 3 digits for uniqueness
        # Format: DEMO_SYMBOL_YYMMDD_LONG_SHORT_TYPE_microseconds (e.g., DEMO_SPY_260107_585_590_c_546)
        position_id = f"DEMO_{spread.symbol}_{expiry_short}_{int(spread.long_strike)}_{int(spread.short_strike)}_{spread.option_type[0]}_{microseconds_short:03d}"
        
        position = MockOptionsPosition(
            position_id=position_id,
            symbol=spread.symbol,
            position_type='debit_spread',
            debit_spread=spread.to_dict(),
            entry_price=spread.debit_cost,
            entry_time=datetime.now(),
            quantity=quantity,
            current_value=spread.debit_cost,
            unrealized_pnl=0.0,
            entry_value=float(spread.debit_cost),
            entry_debit=float(spread.debit_cost),
            entry_credit=None,
        )
        norm = build_normalized_metadata_debit_spread(
            trade_id=position_id,
            spread=spread,
            quantity=quantity,
            strategy_type=strategy_type,
            direction=str(direction or "LONG"),
            spread_type=str(spread_type or "debit"),
        )
        position.metadata = {"normalized_options": norm}
        position.strategy_type = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
        log_position_type_normalized(position_id, norm["position_type"], "debit_spread")
        log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
        
        # Deduct cost from account
        self.account_balance -= total_cost
        
        # Track position
        self.active_positions[position_id] = position
        self.daily_stats['positions_opened'] += 1
        self.weekly_stats['positions_opened'] += 1
        
        if send_open_alert:
            await self._send_demo_system_alert(
                title=f"🎮 DEMO | {_EASY_ORB_0DTE_ALERT} | Debit Spread Opened",
                message=f"Strategy: {_EASY_ORB_0DTE_ALERT} (options)\n"
                        f"{spread.symbol} {spread.option_type.upper()} {spread.long_strike}/{spread.short_strike}\n"
                        f"Quantity: {quantity}\n"
                        f"Debit: ${spread.debit_cost:.2f}\n"
                        f"Total Cost: ${total_cost:.2f}\n"
                        f"Account Balance: ${self.account_balance:.2f}"
            )
        
        log.info(f"🎮 DEMO: Debit spread executed: Position ID {position_id}")
        log.info(f"  Account balance: ${self.account_balance:.2f}")
        
        return position
    
    async def execute_lotto_sleeve(
        self,
        contract: OptionContract,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "single_leg",
    ) -> Optional[MockOptionsPosition]:
        """
        Execute lotto sleeve (single-leg option) in Demo Mode
        
        Args:
            contract: OptionContract object
            quantity: Number of contracts
            
        Returns:
            MockOptionsPosition or None if execution failed
        """
        # Calculate total cost
        total_cost = contract.mid_price * quantity * 100  # Options are per 100 shares
        
        # Check account balance
        if total_cost > self.available_balance():
            log.warning(f"🎮 DEMO: Insufficient balance for {contract.symbol} lotto")
            log.warning(
                f"  Required: ${total_cost:.2f}, Available: ${self.available_balance():.2f} "
                f"(cash=${self.account_balance:.2f}, reserved_margin=${self.reserved_margin_total:.2f})"
            )
            return None
        
        # Simulate execution
        log.info(f"🎮 DEMO: Executing lotto sleeve: {contract.symbol} {contract.option_type} {contract.strike}")
        log.info(f"  Quantity: {quantity}, Cost: ${contract.mid_price:.2f}, Total Cost: ${total_cost:.2f}")
        
        # Create mock position (Rev 00232: Shortened format for alerts - symbol_date_strike_type_microseconds)
        now = datetime.now()
        expiry_short = contract.expiry.replace('-', '')[-6:] if contract.expiry else now.strftime('%y%m%d')
        microseconds_short = now.microsecond % 1000  # Last 3 digits for uniqueness
        # Format: DEMO_SYMBOL_YYMMDD_STRIKE_TYPE_microseconds (e.g., DEMO_SPY_260107_585_c_546)
        position_id = f"DEMO_{contract.symbol}_{expiry_short}_{int(contract.strike)}_{contract.option_type[0]}_{microseconds_short:03d}"
        
        position = MockOptionsPosition(
            position_id=position_id,
            symbol=contract.symbol,
            position_type='lotto',
            lotto_contract=contract.to_dict(),
            entry_price=contract.mid_price,
            entry_time=datetime.now(),
            quantity=quantity,
            current_value=contract.mid_price,
            unrealized_pnl=0.0,
            entry_value=float(contract.mid_price),
            entry_debit=float(contract.mid_price),
            entry_credit=None,
        )
        norm = build_normalized_metadata_single_leg(
            trade_id=position_id,
            contract=contract,
            quantity=quantity,
            strategy_type=strategy_type or "lotto",
            direction=str(direction or "LONG"),
            spread_type=str(spread_type or "single_leg"),
        )
        position.metadata = {"normalized_options": norm}
        position.strategy_type = str(norm.get("orb_strategy_type") or strategy_type or "lotto").strip()
        log_position_type_normalized(position_id, norm["position_type"], "lotto")
        log_metadata_normalized(position_id, norm["position_type"], contract.symbol)
        
        # Deduct cost from account
        self.account_balance -= total_cost
        
        # Track position
        self.active_positions[position_id] = position
        self.daily_stats['positions_opened'] += 1
        self.weekly_stats['positions_opened'] += 1
        
        if send_open_alert:
            await self._send_demo_system_alert(
                title=f"🎮 DEMO | {_EASY_ORB_0DTE_ALERT} | Lotto Sleeve Opened",
                message=f"Strategy: {_EASY_ORB_0DTE_ALERT} (options)\n"
                        f"{contract.symbol} {contract.option_type.upper()} {contract.strike}\n"
                        f"Quantity: {quantity}\n"
                        f"Cost: ${contract.mid_price:.2f}\n"
                        f"Total Cost: ${total_cost:.2f}\n"
                        f"Account Balance: ${self.account_balance:.2f}"
            )
        
        log.info(f"🎮 DEMO: Lotto sleeve executed: Position ID {position_id}")
        log.info(f"  Account balance: ${self.account_balance:.2f}")
        
        return position
    
    async def execute_credit_spread(
        self,
        spread: CreditSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "credit",
    ) -> Optional[MockOptionsPosition]:
        """
        Execute credit spread in Demo Mode
        
        Args:
            spread: CreditSpread object
            quantity: Number of spreads
            
        Returns:
            MockOptionsPosition or None if execution failed
        """
        # Calculate margin requirement (max_loss = spread_width - credit_received)
        margin_requirement = spread.max_loss * quantity * 100  # Options are per 100 shares
        
        # Require unencumbered cash to cover margin reservation
        if margin_requirement > self.available_balance():
            log.warning(f"🎮 DEMO: Insufficient balance for {spread.symbol} credit spread")
            log.warning(
                f"  Required Margin: ${margin_requirement:.2f}, Available: ${self.available_balance():.2f} "
                f"(cash=${self.account_balance:.2f}, reserved_margin=${self.reserved_margin_total:.2f})"
            )
            return None
        
        # Simulate execution
        credit_received = spread.credit_received * quantity * 100
        log.info(f"🎮 DEMO: Executing credit spread: {spread.symbol} {spread.option_type} {spread.short_strike}/{spread.long_strike}")
        log.info(f"  Quantity: {quantity}, Credit: ${spread.credit_received:.2f}, Total Credit: ${credit_received:.2f}")
        log.info(f"  Margin Required: ${margin_requirement:.2f}")
        
        # Create mock position (Rev 00232: Shortened format for alerts - symbol_date_strikes_type_microseconds)
        now = datetime.now()
        expiry_short = spread.expiry.replace('-', '')[-6:] if spread.expiry else now.strftime('%y%m%d')
        microseconds_short = now.microsecond % 1000  # Last 3 digits for uniqueness
        # Format: DEMO_SYMBOL_YYMMDD_SHORT_LONG_TYPE_microseconds (e.g., DEMO_SPY_260107_585_590_c_546)
        position_id = f"DEMO_{spread.symbol}_{expiry_short}_{int(spread.short_strike)}_{int(spread.long_strike)}_{spread.option_type[0]}_{microseconds_short:03d}"
        
        position = MockOptionsPosition(
            position_id=position_id,
            symbol=spread.symbol,
            position_type='credit_spread',
            credit_spread=spread.to_dict(),
            entry_price=spread.credit_received,  # Entry price = credit received
            entry_time=datetime.now(),
            quantity=quantity,
            current_value=spread.credit_received,
            unrealized_pnl=0.0,
            entry_value=float(spread.credit_received),
            entry_debit=None,
            entry_credit=float(spread.credit_received),
            reserved_margin=float(margin_requirement),
        )
        norm = build_normalized_metadata_credit_spread(
            trade_id=position_id,
            spread=spread,
            quantity=quantity,
            strategy_type=strategy_type or "debit_spread",
            direction=str(direction or "SHORT"),
            spread_type=str(spread_type or "credit"),
        )
        position.metadata = {"normalized_options": norm}
        position.strategy_type = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
        log_position_type_normalized(position_id, norm["position_type"], "credit_spread")
        log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
        
        # Receive premium; encumber margin so available_balance reflects risk capital
        self.account_balance += credit_received
        self.reserved_margin_total += float(margin_requirement)
        log.info(
            "OPTIONS_EXECUTOR | stage=credit_margin_reserved | trade_id=%s | symbol=%s | reserved=%.2f | total_reserved=%.2f | available=%.2f",
            position_id,
            spread.symbol,
            float(margin_requirement),
            self.reserved_margin_total,
            self.available_balance(),
        )
        
        # Track position
        self.active_positions[position_id] = position
        self.daily_stats['positions_opened'] += 1
        self.weekly_stats['positions_opened'] += 1
        
        if send_open_alert:
            await self._send_demo_system_alert(
                title=f"🎮 DEMO | {_EASY_ORB_0DTE_ALERT} | Credit Spread Opened",
                message=f"Strategy: {_EASY_ORB_0DTE_ALERT} (options)\n"
                        f"{spread.symbol} {spread.option_type.upper()} {spread.short_strike}/{spread.long_strike}\n"
                        f"Quantity: {quantity}\n"
                        f"Credit: ${spread.credit_received:.2f}\n"
                        f"Total Credit: ${credit_received:.2f}\n"
                        f"Margin Required: ${margin_requirement:.2f}\n"
                        f"Account Balance: ${self.account_balance:.2f}"
            )
        
        log.info(f"🎮 DEMO: Credit spread executed: Position ID {position_id}")
        log.info(f"  Account balance: ${self.account_balance:.2f}")
        
        return position
    
    async def update_position_value(
        self,
        position_id: str,
        current_value: float
    ) -> Optional[MockOptionsPosition]:
        """
        Update position current value and P&L
        
        Args:
            position_id: Position ID
            current_value: Current position value
            
        Returns:
            Updated MockOptionsPosition or None if not found
        """
        if position_id not in self.active_positions:
            return None
        
        position = self.active_positions[position_id]
        position.current_value = current_value
        meta = position.metadata if isinstance(position.metadata, dict) else {}
        norm = meta.get("normalized_options")
        if isinstance(norm, dict):
            norm["current_value"] = float(current_value)
        
        # Calculate unrealized P&L based on position type
        if position.normalized_position_type == 'credit_spread':
            # For credit spreads: profit = (entry_price - current_value) * quantity * 100
            # Entry price = credit received, current_value = current cost to close
            # Profit when current_value decreases (spread expires worthless or decreases)
            position.unrealized_pnl = (position.entry_price - current_value) * position.quantity * 100
        else:
            # For debit spreads and lottos: profit = (current_value - entry_price) * quantity * 100
            position.unrealized_pnl = (current_value - position.entry_price) * position.quantity * 100
        
        # Update max favorable/unfavorable
        if position.unrealized_pnl > position.max_favorable:
            position.max_favorable = position.unrealized_pnl
        if position.unrealized_pnl < position.max_unfavorable:
            position.max_unfavorable = position.unrealized_pnl
        
        return position
    
    async def close_position(
        self,
        position_id: str,
        exit_price: float,
        reason: str = "EOD"
    ) -> Optional[MockOptionsPosition]:
        """
        Close position in Demo Mode
        
        Args:
            position_id: Position ID
            exit_price: Exit price
            reason: Exit reason
            
        Returns:
            Closed MockOptionsPosition or None if not found
        """
        if position_id not in self.active_positions:
            log.warning(f"🎮 DEMO: Position {position_id} not found")
            return None
        
        position = self.active_positions[position_id]
        
        # Calculate final P&L and cash effect (per-share prices × 100 × qty)
        qty = int(position.quantity)
        mult = 100 * qty
        if position.normalized_position_type == 'credit_spread':
            final_pnl = (position.entry_price - exit_price) * mult
            self.account_balance -= exit_price * mult
            rm = float(getattr(position, "reserved_margin", 0.0) or 0.0)
            self.reserved_margin_total = max(0.0, float(self.reserved_margin_total) - rm)
            log.info(
                "OPTIONS_EXECUTOR | stage=credit_margin_released | trade_id=%s | symbol=%s | released=%.2f | total_reserved=%.2f | available=%.2f",
                position_id,
                position.symbol,
                rm,
                self.reserved_margin_total,
                self.available_balance(),
            )
        else:
            final_pnl = (exit_price - position.entry_price) * mult
            self.account_balance += exit_price * mult

        position.realized_pnl = final_pnl
        position.unrealized_pnl = 0.0
        position.status = 'closed'
        position.current_value = float(exit_price)
        
        # Update stats
        self.daily_stats['positions_closed'] += 1
        self.daily_stats['total_pnl'] += final_pnl
        self.weekly_stats['positions_closed'] += 1
        self.weekly_stats['total_pnl'] += final_pnl
        
        if final_pnl > 0:
            self.daily_stats['winning_trades'] += 1
            self.weekly_stats['winning_trades'] += 1
            if final_pnl > self.daily_stats['best_trade']:
                self.daily_stats['best_trade'] = final_pnl
            if final_pnl > self.weekly_stats['best_trade']:
                self.weekly_stats['best_trade'] = final_pnl
        else:
            self.daily_stats['losing_trades'] += 1
            self.weekly_stats['losing_trades'] += 1
            if final_pnl < self.daily_stats['worst_trade']:
                self.daily_stats['worst_trade'] = final_pnl
            if final_pnl < self.weekly_stats['worst_trade']:
                self.weekly_stats['worst_trade'] = final_pnl
        
        # Move to closed positions
        self.closed_positions.append(position)
        del self.active_positions[position_id]
        
        # Save data
        self._save_mock_data()

        log.info(f"🎮 DEMO: Position closed: {position_id}")
        log.info(f"  P&L: ${final_pnl:+.2f}, Account balance: ${self.account_balance:.2f}")
        
        return position
    
    def get_open_positions(self) -> List[MockOptionsPosition]:
        """Get all open positions"""
        return list(self.active_positions.values())
    
    def get_position(self, position_id: str) -> Optional[MockOptionsPosition]:
        """Get position by ID"""
        return self.active_positions.get(position_id)
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """Get daily statistics"""
        return self.daily_stats.copy()
    
    def get_weekly_stats(self) -> Dict[str, Any]:
        """Get weekly statistics"""
        return self.weekly_stats.copy()
    
    def get_all_time_stats(self) -> Dict[str, Any]:
        """Get all-time statistics from closed positions"""
        if not self.closed_positions:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0.0,
                'total_wins_sum': 0.0,
                'total_losses_sum': 0.0
            }
        
        total_trades = len(self.closed_positions)
        winning_trades = sum(1 for p in self.closed_positions if p.realized_pnl > 0)
        losing_trades = total_trades - winning_trades
        total_pnl = sum(p.realized_pnl for p in self.closed_positions)
        total_wins_sum = sum(p.realized_pnl for p in self.closed_positions if p.realized_pnl > 0)
        total_losses_sum = abs(sum(p.realized_pnl for p in self.closed_positions if p.realized_pnl < 0))
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'total_pnl': total_pnl,
            'total_wins_sum': total_wins_sum,
            'total_losses_sum': total_losses_sum
        }
    
    def reset_daily(self):
        """Reset daily state"""
        self.daily_stats = {
            'positions_opened': 0,
            'positions_closed': 0,
            'total_pnl': 0.0,
            'winning_trades': 0,
            'losing_trades': 0,
            'best_trade': 0.0,
            'worst_trade': 0.0
        }
        self.current_trading_date = datetime.utcnow().date()
        log.info("🎮 DEMO: Mock Options Executor daily reset complete")

