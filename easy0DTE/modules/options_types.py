#!/usr/bin/env python3
"""
Options Types
=============

Shared dataclasses for options trading to avoid circular imports.

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field

from .options_chain_manager import DebitSpread, CreditSpread, OptionContract


@dataclass
class OptionsPosition:
    """Options position tracking"""
    position_id: str
    symbol: str
    position_type: str  # 'debit_spread', 'credit_spread', or 'lotto'
    entry_price: float
    entry_time: datetime = field(default_factory=datetime.now)
    quantity: int = 1
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    status: str = 'open'  # 'open', 'partial', 'closed'
    debit_spread: Optional[DebitSpread] = None
    credit_spread: Optional[CreditSpread] = None
    lotto_contract: Optional[OptionContract] = None
    # Selector route label (momentum_scalper, debit_spread, fallback_tier_b, …) for alerts / durability.
    strategy_type: str = ""
    # Normalized options metadata (shared ORB / monitoring schema); additive.
    metadata: Optional[Dict[str, Any]] = None

    @property
    def normalized_options(self) -> Dict[str, Any]:
        """Normalized options metadata blob, if present."""
        meta = self.metadata if isinstance(self.metadata, dict) else {}
        norm = meta.get("normalized_options")
        return norm if isinstance(norm, dict) else {}

    @property
    def normalized_position_type(self) -> str:
        """
        Structural position type from normalized metadata when available.
        Falls back to legacy position_type for backward compatibility.
        """
        structural = str(self.normalized_options.get("position_type", "") or "").strip()
        return structural if structural else str(self.position_type or "")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'position_id': self.position_id,
            'symbol': self.symbol,
            'position_type': self.position_type,
            'debit_spread': self.debit_spread.to_dict() if self.debit_spread else None,
            'credit_spread': self.credit_spread.to_dict() if self.credit_spread else None,
            'lotto_contract': self.lotto_contract.to_dict() if self.lotto_contract else None,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time.isoformat(),
            'quantity': self.quantity,
            'current_value': self.current_value,
            'unrealized_pnl': self.unrealized_pnl,
            'realized_pnl': self.realized_pnl,
            'status': self.status,
            'strategy_type': self.strategy_type,
            'metadata': self.metadata,
        }
