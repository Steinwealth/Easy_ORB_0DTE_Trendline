from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class ExecutionIntent:
    symbol: str
    side: str  # "LONG"
    strategy_type: str  # "ORB_SO", "lotto", "long_call", etc.
    asset_type: str  # "equity" or "option"
    structure_type: str  # "equity_long", "single_leg_long_call", "debit_spread", etc.
    confidence: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
