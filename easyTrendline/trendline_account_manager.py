#!/usr/bin/env python3
"""
Dedicated Trendline strategy account manager (demo-first).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .trendline_models import TrendlineDirection

log = logging.getLogger(__name__)


@dataclass
class TrendlinePosition:
    """Simple strategy-level position ledger row."""

    position_id: str
    symbol: str
    direction: TrendlineDirection
    option_side: str
    quantity: int
    entry_cost: float
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "open"
    exit_value: float = 0.0
    realized_pnl: float = 0.0
    closed_at: Optional[datetime] = None
    metadata: Dict[str, object] = field(default_factory=dict)


class TrendlineAccountManager:
    """
    Separate account manager for Easy Trendline path.

    Keeps strategy PnL/accounting isolated from ORB and ORB 0DTE demo ledgers.
    """

    def __init__(self, starting_balance: Optional[float] = None) -> None:
        # Prefer explicit constructor value; otherwise support both env spellings.
        # Canonical env is TRENDLINE_DEMO_STARTING_BALANCE (default 5000.0).
        env_balance = os.getenv(
            "TRENDLINE_DEMO_STARTING_BALANCE",
            os.getenv("TRENDLINE_DEMO_START_BALANCE", "5000.0"),
        )
        self.starting_balance = float(starting_balance) if starting_balance is not None else float(env_balance)
        self.account_balance = self.starting_balance
        self.active_positions: Dict[str, TrendlinePosition] = {}
        self.closed_positions: List[TrendlinePosition] = []

    def open_position(self, position: TrendlinePosition) -> bool:
        """Reserve capital and register active position."""
        if position.entry_cost <= 0:
            log.warning("Trendline open rejected for %s: non-positive entry cost", position.symbol)
            return False
        if position.entry_cost > self.account_balance:
            log.warning(
                "Trendline open rejected for %s: insufficient balance cost=%.2f balance=%.2f",
                position.symbol,
                position.entry_cost,
                self.account_balance,
            )
            return False

        self.account_balance -= position.entry_cost
        self.active_positions[position.position_id] = position
        log.info(
            "Trendline position opened %s %s qty=%d cost=%.2f balance=%.2f",
            position.position_id,
            position.symbol,
            position.quantity,
            position.entry_cost,
            self.account_balance,
        )
        return True

    def close_position(self, position_id: str, exit_value: float) -> Optional[TrendlinePosition]:
        """Close active position and realize PnL."""
        position = self.active_positions.get(position_id)
        if not position:
            log.warning("Trendline close skipped: unknown position_id=%s", position_id)
            return None

        exit_value = max(0.0, float(exit_value))
        realized = exit_value - position.entry_cost
        position.exit_value = exit_value
        position.realized_pnl = realized
        position.status = "closed"
        position.closed_at = datetime.now(timezone.utc)
        self.account_balance += exit_value

        self.closed_positions.append(position)
        del self.active_positions[position_id]

        log.info(
            "Trendline position closed %s pnl=%.2f new_balance=%.2f",
            position_id,
            realized,
            self.account_balance,
        )
        return position

    def finalize_close_from_unified_result(
        self,
        position_id: str,
        *,
        exit_value: float,
        exit_reason: str = "",
        exit_time: Optional[datetime] = None,
        close_metadata: Optional[Dict[str, object]] = None,
    ) -> Optional[TrendlinePosition]:
        """
        Downstream ledger sync from unified options executor close contract.
        """
        closed = self.close_position(position_id, exit_value)
        if not closed:
            return None
        if not isinstance(closed.metadata, dict):
            closed.metadata = {}
        closed.metadata["unified_close_result"] = {
            "exit_reason": str(exit_reason or ""),
            "exit_time": (
                (exit_time or datetime.now(timezone.utc)).isoformat()
                if hasattr((exit_time or datetime.now(timezone.utc)), "isoformat")
                else None
            ),
            "details": dict(close_metadata or {}),
        }
        return closed

    def summary(self) -> Dict[str, float]:
        """Return account-level statistics."""
        wins = sum(1 for p in self.closed_positions if p.realized_pnl > 0)
        losses = sum(1 for p in self.closed_positions if p.realized_pnl < 0)
        realized = sum(p.realized_pnl for p in self.closed_positions)
        return {
            "starting_balance": self.starting_balance,
            "account_balance": self.account_balance,
            "open_positions": float(len(self.active_positions)),
            "closed_positions": float(len(self.closed_positions)),
            "wins": float(wins),
            "losses": float(losses),
            "realized_pnl": realized,
            "win_rate": (wins / len(self.closed_positions)) if self.closed_positions else 0.0,
        }

