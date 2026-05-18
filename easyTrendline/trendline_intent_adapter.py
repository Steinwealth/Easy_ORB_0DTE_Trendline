from modules.execution_intent import ExecutionIntent

from .trendline_models import TrendlineTradeSignal


def build_execution_intent_from_trendline_signal(signal: TrendlineTradeSignal) -> ExecutionIntent:
    """
    Convert TrendlineTradeSignal into unified ExecutionIntent format.
    This allows Trendline trades to flow through Risk + Execution pipeline.
    """

    option_side = str(signal.option_side).lower()

    position_type = (
        "single_leg_long_put" if option_side == "put" else "single_leg_long_call"
    )

    metadata = signal.metadata if isinstance(signal.metadata, dict) else {}

    return ExecutionIntent(
        symbol=signal.symbol,
        side="LONG",
        strategy_type="trendline_0dte",
        asset_type="option",
        structure_type=position_type,
        confidence=float(signal.confidence),
        metadata={
            "priority_score": float(signal.priority_score),
            "signal_time": signal.emitted_at.isoformat(),
            "source": "easyTrendline",
            "entry_type": "trendline_break",
            "trendline_direction": signal.direction.value,
            "break_quality_score": metadata.get("break_quality_score"),
            "setup_type": metadata.get("setup_type"),
            "structure_display_label": metadata.get("structure_display_label"),
            "trigger_direction": metadata.get("trigger_direction"),
            "trendline_structure_source": metadata.get("trendline_structure_source"),
            "trendline_mode": metadata.get("trendline_mode"),
            "impulse_mode": bool(metadata.get("impulse_mode")),
            "slow_trend_mode": bool(metadata.get("slow_trend_mode")),
            "early_entry_mode": bool(metadata.get("early_entry_mode")),
            "entry_size_multiplier": metadata.get("entry_size_multiplier"),
        },
    )
