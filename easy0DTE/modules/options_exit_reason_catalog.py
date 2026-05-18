"""
Shared 0DTE options exit reason catalog (ORB + Trendline).

Canonical names are stable for analytics; legacy stealth strings remain in logs
and close metadata for backward compatibility.
"""

from __future__ import annotations

from typing import Dict, Optional

# legacy_reason -> canonical module name (ORB / Trendline profiles only change thresholds)
EXIT_REASON_CANONICAL: Dict[str, str] = {
    "time_exit": "options_time_exit",
    "no_progress_timeout": "options_no_progress_exit",
    "no_progress_timeout_early": "options_no_progress_exit",
    "trailing_stop": "options_trailing_exit",
    "impulse_profit_lock": "options_profit_lock_exit",
    "impulse_take_profit": "options_profit_lock_exit",
    "micro_lock": "options_micro_lock_exit",
    "impulse_micro_lock": "options_micro_lock_exit",
    "reversal_exit": "options_peak_giveback_exit",
    "trendline_reversal_exit": "options_peak_giveback_exit",
    "trendline_structure_exit": "options_structure_exit",
    "structure_invalidation": "options_structure_exit",
    "adverse_guard": "options_adverse_move_exit",
    "reversal_reclaim_recross": "options_adverse_move_exit",
    "option_force_exit_no_data": "options_no_data_exit",
    "degraded_data_no_fallback": "options_no_data_exit",
    "degraded_data_outage": "options_no_data_exit",
    "end_of_day_close": "options_eod_exit",
    "fast_fail": "options_fast_fail_exit",
    "options_stealth:max_pnl_drawdown_exit": "options_peak_giveback_exit",
    "breakeven_stop": "options_breakeven_exit",
    "options_stealth:profit_floor_exit": "options_profit_lock_exit",
}


def canonical_exit_reason(legacy_reason: Optional[str]) -> str:
    """Map a stealth / executor exit reason to a canonical catalog id."""
    if not legacy_reason:
        return "options_unknown_exit"
    key = str(legacy_reason).strip()
    if key.startswith("orb_options_stealth:"):
        key = key.split(":", 1)[-1].strip()
    if key.startswith("trendline_options_stealth:"):
        key = key.split(":", 1)[-1].strip()
    if ":" in key:
        inner = key.split(":")[-1]
        if inner in EXIT_REASON_CANONICAL:
            key = inner
    return EXIT_REASON_CANONICAL.get(key, f"options_legacy:{key}")
