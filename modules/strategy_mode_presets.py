# modules/strategy_mode_presets.py
"""
In-repo presets for strategy_mode advanced / quantum.

Former configs/modes/advanced.env and configs/modes/quantum.env were removed (May 2026);
`ConfigLoader` applies these dicts after the seven canonical .env files merge.
Values are strings to match `.env` parsing behavior.
"""

from typing import Dict

ADVANCED_MODE_PRESET: Dict[str, str] = {
    "PERFORMANCE_MODE": "ultra_high",
    "TARGET_WEEKLY_RETURN": "0.10",
    "BASE_RISK_PER_TRADE": "0.05",
    "MAX_RISK_PER_TRADE": "0.15",
    "MIN_QUALITY_SCORE": "70",
    "MIN_CONFIDENCE_SCORE": "0.9",
    "POSITION_SIZE_PCT": "20.0",
    "CONFIDENCE_MULTIPLIER": "2.0",
    "POLL_SECONDS": "0.5",
    "BATCH_SIZE": "20",
    "CACHE_TTL_SECONDS": "30",
    "MAX_WORKERS": "8",
    "MAX_OPEN_POSITIONS": "26",
    "STOP_LOSS_ATR_MULTIPLIER": "1.8",
    "TAKE_PROFIT_ATR_MULTIPLIER": "3.0",
    "MAX_DAILY_TRADES": "20",
    "LOG_PATH": "logs/advanced_strategy.log",
    "SESSION_LOG_LEVEL": "INFO",
    "ALERT_ON_ENTRY_SIGNALS": "true",
    "ALERT_ON_EXIT_SIGNALS": "true",
    "ALERT_ON_ERRORS": "true",
    "ALERT_ON_PERFORMANCE_ISSUES": "true",
    "ALERT_ON_DATA_FAILURES": "true",
}

QUANTUM_MODE_PRESET: Dict[str, str] = {
    "PERFORMANCE_MODE": "quantum",
    "TARGET_WEEKLY_RETURN": "0.50",
    "BASE_RISK_PER_TRADE": "0.10",
    "MAX_RISK_PER_TRADE": "0.25",
    "MIN_QUALITY_SCORE": "80",
    "MIN_CONFIDENCE_SCORE": "0.95",
    "POSITION_SIZE_PCT": "30.0",
    "QUANTUM_MULTIPLIER": "5.0",
    "POLL_SECONDS": "0.1",
    "BATCH_SIZE": "50",
    "CACHE_TTL_SECONDS": "10",
    "MAX_WORKERS": "16",
    "MAX_OPEN_POSITIONS": "26",
    "STOP_LOSS_ATR_MULTIPLIER": "2.0",
    "TAKE_PROFIT_ATR_MULTIPLIER": "4.0",
    "MAX_DAILY_TRADES": "50",
    "LOG_PATH": "logs/quantum_strategy.log",
    "SESSION_LOG_LEVEL": "INFO",
    "ALERT_ON_ENTRY_SIGNALS": "true",
    "ALERT_ON_EXIT_SIGNALS": "true",
    "ALERT_ON_ERRORS": "true",
    "ALERT_ON_PERFORMANCE_ISSUES": "true",
    "ALERT_ON_DATA_FAILURES": "true",
    "ALERT_ON_API_LIMITS": "true",
    "QUANTUM_ML_ENABLED": "true",
    "QUANTUM_REAL_TIME_ANALYSIS": "true",
    "QUANTUM_MULTI_TIMEFRAME": "true",
    "QUANTUM_ADAPTIVE_SIZING": "true",
}
