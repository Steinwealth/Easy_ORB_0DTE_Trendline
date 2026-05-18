#!/usr/bin/env python3
"""
Prime ORB Strategy Manager - COMPLETE & STANDALONE
==================================================

Single comprehensive module for ORB (Opening Range Breakout) Strategy.
Replaces multi-strategy manager with simpler, more predictable trading.

This manager handles the COMPLETE ORB trading workflow:
1. Opening Range Detection (6:30-6:45 AM PT / 9:30-9:45 AM ET)
2. Standard Order (SO) Signals (7:15 AM PT / 10:15 AM ET)
3. Opening Range Reversal (ORR) Signals (7:15 AM-12:15 PM PT / 10:15 AM-3:15 PM ET)
4. Inverse ETF Selection for bearish signals
5. Volume Color Validation (green/red candle confirmation)
6. Daily Trade Limits (1 SO + 1 ORR per symbol)

Lists and symmetry (Rev 00344):
===============================
- **core_list (SO ORB)**: leveraged / tactical ETFs and names used for **LONG-only** Standard Order
  signal collection at 7:30 PT. Same bullish SO rule set as below.
- **0dte_list (0DTE ORB)**: **primary** underlyings (stocks + broad ETFs/indices) for **CALL** (LONG SO
  rules) and **PUT** (bearish SO / inverse rules). No cross-list pairing; each symbol is judged only
  on its own ORB and validation candle.
- **0DTE Trendline** is a separate path and does not add symbols to ORB SO or 0DTE ORB lists.

ORB capture note (Rev 00345): For symbols in **ORB_RTH_INTRADAY_SYMBOLS** (default VIX, SPX), ORB high/low are
taken from **Yahoo Finance 15m bars** with **prepost=False**, using only bar starts in **6:30–6:45 PT**, because
broker “today” OHLC for those indices can span extended hours and is not the cash-session opening range.

Definitions (do not conflate):
==============================
- Opening Range (ORB): The FIRST 15 minutes after market open ONLY: 6:30-6:45 AM PT (9:30-9:45 AM ET).
  ORB High and ORB Low come from this window only. This is the true "Opening Range" for breakout logic.
- Validation candle (7:00-7:15 AM PT): NOT an opening range. Market open is earlier (6:30 AM PT).
  The 7:00-7:15 bar is used only to check: (1) volume color (GREEN/RED = close vs open), and
  (2) whether that bar's close is above ORB high or below ORB low for rule confirmation. Never use
  7:00-7:15 high/low as ORB high/low.

Entry Rules:
============

Opening Range (6:30-6:45 AM PT only):
  - First 15-minute candle of market open
  - ORB High: High of that candle
  - ORB Low: Low of that candle

Standard Order (SO) - 7:15 AM PT (10:15 AM ET):
  Bullish SO:
    - Current price +0.2% above ORB high (ORB high = from 6:30-6:45 only)
    - Validation candle (7:00-7:15 AM PT) closed above ORB high
    - Validation candle closed above its open (green volume)
  
  Inverse SO:
    - Current price -0.2% below ORB low (ORB low = from 6:30-6:45 only)
    - Validation candle (7:00-7:15 AM PT) closed below ORB low
    - Validation candle closed below its open (red volume)

Opening Range Reversal (ORR) - 7:15 AM-12:15 PM PT (10:15 AM-3:15 PM ET):
  Bullish ORR (ONLY VALID ORR SIGNAL):
    - Price was previously below ORB low in the day
    - Price breaks above ORB high for the FIRST TIME in the day
    - V-shaped reversal pattern
    - ALWAYS generates LONG positions
    - NO SHORT positions or bearish signals

Target Gains: 3% average move (1%-10% range)
Hard Cutoff: No new SO after 7:30 AM PT, No new ORR after 12:15 PM PT

Author: Easy Trading Software Team
Date: October 11, 2025
Revision: 00151
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import pytz
import json
import os

from .execution_intent import ExecutionIntent
from .config_loader import get_config_value

log = logging.getLogger(__name__)

# Import SignalSide from prime_models
try:
    from .prime_models import SignalSide
except ImportError:
    class SignalSide(Enum):
        LONG = "LONG"
        SHORT = "SHORT"

log = logging.getLogger("prime_orb_strategy_manager")

# Timezone constants
ET_TZ = pytz.timezone('America/New_York')
PT_TZ = pytz.timezone('America/Los_Angeles')


def _parse_pt_hhmm(raw: Optional[str], fallback: time) -> time:
    """Parse HH:MM (PT) from config string; invalid or empty → fallback."""
    if not raw:
        return fallback
    s = str(raw).strip()
    if not s:
        return fallback
    try:
        parts = s.split(":", 1)
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return time(h % 24, m % 60, 0)
    except (ValueError, TypeError, IndexError):
        log.warning("CONFIG_INVALID_TIME | value=%r | using_fallback=%s", raw, fallback)
        return fallback


def _env_time(key: str, fallback: time) -> time:
    return _parse_pt_hhmm(str(get_config_value(key, "") or "").strip() or None, fallback)


def _env_float(key: str, default: float) -> float:
    try:
        return float(get_config_value(key, default))
    except (TypeError, ValueError):
        log.warning("CONFIG_INVALID_FLOAT | key=%s | using_default=%s", key, default)
        return float(default)

# ============================================================================
# ENUMS & DATA STRUCTURES
# ============================================================================

class SignalType(Enum):
    """ORB Signal Types"""
    STANDARD_ORDER = "SO"
    OPENING_RANGE_REVERSAL = "ORR"

def compute_orb_range_pct(orb_high: float, orb_low: float) -> float:
    """
    Canonical ORB range % for LONG and SHORT (same geometry).
    Opening-range width as % of low: (high - low) / low * 100.
    LONG and SHORT both use this width; direction does not change the formula.
    """
    try:
        oh, ol = float(orb_high), float(orb_low)
        if ol <= 0 or oh < ol:
            return 0.0
        return (oh - ol) / ol * 100.0
    except (TypeError, ValueError):
        return 0.0


@dataclass
class ORBData:
    """
    Morning ORB capture (6:30–6:45 AM PT): high/low from session open.
    orb_range_pct is always from capture: (orb_high - orb_low) / orb_low * 100.

    orb_high_extreme_ts / orb_low_extreme_ts: timestamps of the **earliest** bar in the ORB
    window whose high (low) equals the captured orb_high (orb_low). Used by Easy Trendline
    so anchor-one time matches when price printed the range extreme (e.g. 6:35 wick), not
    ``capture_time`` when aggregation finished.
    """
    symbol: str
    orb_high: float
    orb_low: float
    orb_open: float
    orb_close: float
    orb_volume: float
    orb_range: float
    orb_range_pct: float  # from capture H/L — single source for Convex, alerts, sizing
    orb_is_green: bool
    capture_time: datetime
    orb_high_extreme_ts: Optional[datetime] = None
    orb_low_extreme_ts: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Persist/load; orb_range_pct always matches morning capture H/L."""
        return {
            'symbol': self.symbol,
            'orb_high': self.orb_high,
            'orb_low': self.orb_low,
            'orb_open': self.orb_open,
            'orb_close': self.orb_close,
            'orb_volume': self.orb_volume,
            'orb_range': self.orb_range,
            'orb_range_pct': self.orb_range_pct,
            'orb_is_green': self.orb_is_green,
            'capture_time': self.capture_time.isoformat() if self.capture_time else None,
            'orb_high_extreme_ts': self.orb_high_extreme_ts.isoformat()
            if self.orb_high_extreme_ts
            else None,
            'orb_low_extreme_ts': self.orb_low_extreme_ts.isoformat()
            if self.orb_low_extreme_ts
            else None,
        }

@dataclass
class ORRReversalState:
    """Track ORR reversal state for a symbol"""
    symbol: str
    was_above_orb_high: bool = False
    was_below_orb_low: bool = False
    first_above_timestamp: Optional[datetime] = None
    first_below_timestamp: Optional[datetime] = None
    bullish_orr_triggered: bool = False
    inverse_orr_triggered: bool = False

@dataclass
class PostORBValidation:
    """Track post-ORB validation to disable trading if major move already occurred"""
    symbol: str
    trading_disabled: bool = False
    validation_time: Optional[datetime] = None
    validation_reason: Optional[str] = None
    post_orb_high: Optional[float] = None
    post_orb_low: Optional[float] = None
    orb_high_breached: bool = False
    orb_low_breached: bool = False

@dataclass
class ORBStrategyResult:
    """Result from ORB strategy analysis"""
    symbol: str
    should_trade: bool
    signal_type: Optional[SignalType]
    side: SignalSide
    confidence: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    position_size_pct: float
    reasoning: str
    inverse_symbol: Optional[str] = None
    orb_data: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# ============================================================================
# DAILY TRADE COUNTER
# ============================================================================

class DailyTradeCounter:
    """Track daily trade executions for SO/ORR limits"""
    
    def __init__(self):
        self.so_trades = set()  # Symbols traded with SO today
        self.orr_trades = set()  # Symbols traded with ORR today
        self.current_date = datetime.now().date()
        log.info("Daily Trade Counter initialized")
    
    def has_traded_so_today(self, symbol: str) -> bool:
        """Check if symbol has SO trade today"""
        self._check_new_day()
        return symbol in self.so_trades
    
    def has_traded_orr_today(self, symbol: str) -> bool:
        """Check if symbol has ORR trade today"""
        self._check_new_day()
        return symbol in self.orr_trades
    
    def record_trade(self, symbol: str, signal_type: SignalType, **kwargs):
        """Record a trade execution"""
        self._check_new_day()
        
        if signal_type == SignalType.STANDARD_ORDER:
            self.so_trades.add(symbol)
            log.info(f"📝 Recorded SO trade for {symbol}")
        elif signal_type == SignalType.OPENING_RANGE_REVERSAL:
            self.orr_trades.add(symbol)
            log.info(f"📝 Recorded ORR trade for {symbol}")
    
    def _check_new_day(self):
        """Reset counters if new day"""
        today = datetime.now().date()
        if today != self.current_date:
            self.reset_daily()
            self.current_date = today
    
    def reset_daily(self):
        """Reset daily counters"""
        self.so_trades.clear()
        self.orr_trades.clear()
        log.info("🔄 Daily trade counters reset")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status"""
        return {
            'date': self.current_date.isoformat(),
            'so_trades': len(self.so_trades),
            'orr_trades': len(self.orr_trades),
            'total_trades': len(self.so_trades) + len(self.orr_trades)
        }

# ============================================================================
# PRIME ORB STRATEGY MANAGER - COMPLETE IMPLEMENTATION
# ============================================================================

class PrimeORBStrategyManager:
    """
    Prime ORB Strategy Manager - COMPLETE & STANDALONE
    
    Single comprehensive manager for ORB trading with SO/ORR signals,
    inverse ETF selection, daily trade limits, and complete integration.
    """
    
    def __init__(self, data_manager=None):
        """Initialize Prime ORB Strategy Manager"""
        self.data_manager = data_manager
        
        # Core data structures
        self.orb_data = {}  # {symbol: ORBData}
        self.reversal_states = {}  # {symbol: ORRReversalState}
        self.post_orb_validation = {}  # {symbol: PostORBValidation}
        
        # Daily trade counter
        self.trade_counter = DailyTradeCounter()
        
        # Duplicate signal prevention (Rev 00163)
        # Rev 00046: FORCE clear on initialization to prevent stale data from persisting across container restarts
        self.executed_symbols_today: set = set()  # Track executed trading symbols
        log.info("🔄 Initialized: executed_symbols_today = {} (empty - ready for today's trading)")
        
        # Load inverse ETF mappings
        self.inverse_mapping = self._load_inverse_mapping()
        
        # Trading windows (Pacific Time) — configs/ORBSO.env + Shared.env (merged into os.environ at startup)
        self.orb_window_start = _env_time("ORB_WINDOW_START", time(6, 30))
        self.orb_window_end = _env_time("ORB_WINDOW_END", time(6, 45))
        self.so_entry_time = _env_time("SO_ENTRY_TIME", time(7, 15))
        self.so_cutoff_time = _env_time("SO_CUTOFF_TIME", time(7, 30))
        _so_exec_raw = str(get_config_value("SO_EXECUTION_TIME", "") or "").strip()
        self.so_execution_time = (
            _parse_pt_hhmm(_so_exec_raw, self.so_cutoff_time)
            if _so_exec_raw
            else self.so_cutoff_time
        )
        self.orr_start_time = _env_time("ORR_START_TIME", time(8, 15))
        self.orr_cutoff_time = _env_time("ORR_CUTOFF_TIME", time(12, 15))

        # Validation candle (7:00–7:15 default): volume color + close vs ORB; not the opening range
        self.validation_candle_start = _env_time("SO_VALIDATION_CANDLE_START", time(7, 0))
        self.validation_candle_end = _env_time("SO_VALIDATION_CANDLE_END", time(7, 15))
        # Post-ORB chop filter (6:45–7:00 default)
        self.post_orb_candle_start = _env_time("SO_POST_ORB_CANDLE_START", time(6, 45))
        self.post_orb_candle_end = _env_time("SO_POST_ORB_CANDLE_END", time(7, 0))

        # Strategy parameters (fractional distance from ORB high/low, e.g. 0.001 = 0.1%)
        self.target_gain_pct = _env_float("SO_TARGET_GAIN_PCT", 3.0)
        _tg = self.target_gain_pct / 100.0
        self._tp_long_mult = 1.0 + _tg
        self._tp_short_mult = 1.0 - _tg
        self.so_bullish_threshold = _env_float("SO_BULLISH_THRESHOLD", 0.001)
        self.so_inverse_threshold = _env_float("SO_INVERSE_THRESHOLD", 0.001)

        # Confidence-tier position sizing (% of book) for ETF SO/ORR signals
        self._pos_size_high = _env_float("SO_ETF_POSITION_SIZE_PCT_HIGH", 32.0)
        self._pos_size_med = _env_float("SO_ETF_POSITION_SIZE_PCT_MED", 27.0)
        self._pos_size_low = _env_float("SO_ETF_POSITION_SIZE_PCT_LOW", 22.0)
        self._so_signal_bonus_pct = _env_float("SO_ETF_SO_SIGNAL_BONUS_PCT", 3.0)
        self.default_position_size = _env_float("SO_ETF_DEFAULT_POSITION_SIZE_PCT", 25.0)
        self.max_position_size = _env_float("SO_ETF_MAX_POSITION_SIZE_PCT", 35.0)

        log.info("🚀 Prime ORB Strategy Manager initialized")
        log.info(f"   - ORB Window: {self.orb_window_start}-{self.orb_window_end} PT")
        log.info(f"   - SO Window: {self.so_entry_time}-{self.so_cutoff_time} PT (execution {self.so_execution_time} PT)")
        log.info(f"   - ORR Window: {self.orr_start_time}-{self.orr_cutoff_time} PT")
        log.info(f"   - Validation candle: {self.validation_candle_start}-{self.validation_candle_end} PT")
        log.info(f"   - Target Gain: {self.target_gain_pct}% (LONG TP ×{self._tp_long_mult:.4f} SHORT TP ×{self._tp_short_mult:.4f})")
        log.info(f"   - SO buffers: bullish≥{self.so_bullish_threshold:.4f} inverse≤{self.so_inverse_threshold:.4f} (fraction of ORB level)")
        log.info(f"   - Inverse ETFs: {len(self.inverse_mapping)}")
    
    @staticmethod
    def _orb_neutral_as_green_enabled() -> bool:
        """NEUTRAL validation candle counts as GREEN for bullish SO (all symbols). Primary env 0DTE_ORB_TREAT_NEUTRAL_AS_GREEN; legacy 0DTE_VIX_BULLISH_TREAT_NEUTRAL_AS_GREEN if unset."""
        p = os.getenv("0DTE_ORB_TREAT_NEUTRAL_AS_GREEN", "").strip().lower()
        if p in ("1", "true", "yes"):
            return True
        if p in ("0", "false", "no"):
            return False
        return os.getenv("0DTE_VIX_BULLISH_TREAT_NEUTRAL_AS_GREEN", "true").strip().lower() in ("1", "true", "yes")
    
    @staticmethod
    def _orb_neutral_as_red_enabled() -> bool:
        """NEUTRAL validation candle counts as RED for bearish SO (all symbols). Primary env 0DTE_ORB_TREAT_NEUTRAL_AS_RED; legacy 0DTE_VIX_BEARISH_TREAT_NEUTRAL_AS_RED if unset."""
        p = os.getenv("0DTE_ORB_TREAT_NEUTRAL_AS_RED", "").strip().lower()
        if p in ("1", "true", "yes"):
            return True
        if p in ("0", "false", "no"):
            return False
        return os.getenv("0DTE_VIX_BEARISH_TREAT_NEUTRAL_AS_RED", "true").strip().lower() in ("1", "true", "yes")
    
    def _load_inverse_mapping(self) -> Dict[str, str]:
        """Load inverse ETF mappings"""
        try:
            mapping_file = "data/watchlist/orb_inverse_mapping.json"
            if os.path.exists(mapping_file):
                with open(mapping_file, 'r') as f:
                    data = json.load(f)
                    
                    # Extract inverse mappings from nested structure
                    inverse_mappings = {}
                    orb_strategy = data.get('orb_strategy_mapping', {})
                    
                    # Process 3x leverage pairs
                    for category, pairs in orb_strategy.get('3x_leverage_pairs', {}).items():
                        for symbol, info in pairs.items():
                            if 'bear_etf' in info:
                                inverse_mappings[symbol] = info['bear_etf']
                    
                    # Process 2x leverage pairs
                    for category, pairs in orb_strategy.get('2x_leverage_pairs', {}).items():
                        for symbol, info in pairs.items():
                            if 'bear_etf' in info:
                                inverse_mappings[symbol] = info['bear_etf']
                    
                    log.info(f"✅ Loaded {len(inverse_mappings)} inverse ETF mappings")
                    return inverse_mappings
            else:
                log.warning(f"⚠️ Inverse mapping file not found: {mapping_file}")
                return {}
        except Exception as e:
            log.error(f"❌ Error loading inverse ETF mappings: {e}")
            return {}
    
    # ========================================================================
    # TIME WINDOW MANAGEMENT
    # ========================================================================
    
    def _get_current_time_pt(self) -> time:
        """Get current time in Pacific Time"""
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        now_pt = now_utc.astimezone(PT_TZ)
        return now_pt.time()
    
    def _is_within_so_window(self) -> bool:
        """Check if current time is within SO collection window (7:15-7:30 AM PT, 15-minute window)"""
        current_time = self._get_current_time_pt()
        return current_time >= self.so_entry_time and current_time < self.so_cutoff_time
    
    def _is_within_orr_window(self) -> bool:
        """Check if current time is within ORR entry window"""
        current_time = self._get_current_time_pt()
        return current_time >= self.orr_start_time and current_time < self.orr_cutoff_time
    
    # ========================================================================
    # ORB DATA CAPTURE & ANALYSIS
    # ========================================================================
    
    def _capture_opening_range(self, symbol: str, intraday_data: List[Dict[str, Any]]) -> Optional[ORBData]:
        """
        Capture ORB high/low from 6:30-6:45 AM PT window.
        Rev 00311: Aggregate HIGH = max(high), LOW = min(low) over every bar in that window
        (fixes broker flat OHLC on bars=1 → 0% range). Same range % for LONG and SHORT.
        """
        try:
            if not intraday_data:
                return None
            
            window_bars: List[Dict[str, Any]] = []
            for bar in intraday_data:
                timestamp = bar.get('timestamp', bar.get('datetime'))
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                pt_time = timestamp.astimezone(PT_TZ)
                bar_time = pt_time.time()
                if self.orb_window_start <= bar_time <= self.orb_window_end:
                    window_bars.append(bar)
            
            if not window_bars:
                return None
            
            # Aggregate true opening-range extremes (one 15m bar or many 1m bars in window)
            orb_high = max(float(b['high']) for b in window_bars)
            orb_low = min(float(b['low']) for b in window_bars)
            first = window_bars[0]
            last = window_bars[-1]
            orb_open = float(first['open'])
            orb_close = float(last['close'])
            orb_volume = sum(float(b.get('volume') or 0) for b in window_bars)

            def _bar_dt(bar: Dict[str, Any]) -> datetime:
                t = bar.get('timestamp', bar.get('datetime'))
                if isinstance(t, str):
                    return datetime.fromisoformat(t.replace('Z', '+00:00'))
                if isinstance(t, datetime):
                    return t
                return datetime.now(PT_TZ)

            hi_eps = max(1e-4, abs(orb_high) * 1e-6)
            lo_eps = max(1e-4, abs(orb_low) * 1e-6)
            high_hitters = [b for b in window_bars if abs(float(b['high']) - orb_high) <= hi_eps]
            low_hitters = [b for b in window_bars if abs(float(b['low']) - orb_low) <= lo_eps]
            orb_high_extreme_ts: Optional[datetime] = (
                min(_bar_dt(b) for b in high_hitters) if high_hitters else None
            )
            orb_low_extreme_ts: Optional[datetime] = (
                min(_bar_dt(b) for b in low_hitters) if low_hitters else None
            )
            
            # Track if price violated the ORB range during the capture window
            was_above_orb_high = False
            was_below_orb_low = False
            
            # Second pass: Check all bars in the ORB window for range violations
            for bar in intraday_data:
                timestamp = bar.get('timestamp', bar.get('datetime'))
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                
                pt_time = timestamp.astimezone(PT_TZ)
                bar_time = pt_time.time()
                
                # Check all bars within the ORB window (6:30-6:45 AM PT)
                if self.orb_window_start <= bar_time <= self.orb_window_end:
                    bar_high = float(bar['high'])
                    bar_low = float(bar['low'])
                    
                    # Track if price ever went above ORB high during capture
                    if bar_high > orb_high:
                        was_above_orb_high = True
                        log.debug(f"🔍 {symbol} went ABOVE ORB high during capture: ${bar_high:.2f} > ${orb_high:.2f}")
                    
                    # Track if price ever went below ORB low during capture
                    if bar_low < orb_low:
                        was_below_orb_low = True
                        log.debug(f"🔍 {symbol} went BELOW ORB low during capture: ${bar_low:.2f} < ${orb_low:.2f}")
            
            orb_range = orb_high - orb_low
            pct = compute_orb_range_pct(orb_high, orb_low)
            if pct < 1e-6 and orb_low > 0:
                log.warning(
                    f"⚠️ ORB_DEGENERATE {symbol}: H=L≈${orb_high:.2f} (0% range) — broker may have sent flat OHLC; "
                    f"recommend multi-bar capture for this symbol"
                )
            orb_is_green = orb_close > orb_open
            
            orb_data = ORBData(
                symbol=symbol,
                orb_high=orb_high,
                orb_low=orb_low,
                orb_open=orb_open,
                orb_close=orb_close,
                orb_volume=orb_volume,
                orb_range=orb_range,
                orb_range_pct=pct,
                orb_is_green=orb_is_green,
                capture_time=datetime.now(PT_TZ),
                orb_high_extreme_ts=orb_high_extreme_ts,
                orb_low_extreme_ts=orb_low_extreme_ts,
            )
            
            # Initialize ORR reversal state with capture window violations
            self.reversal_states[symbol] = ORRReversalState(
                symbol=symbol,
                was_above_orb_high=was_above_orb_high,
                was_below_orb_low=was_below_orb_low,
                bullish_orr_triggered=False,
                inverse_orr_triggered=False
            )
            
            self.orb_data[symbol] = orb_data
            
            violation_info = ""
            if was_above_orb_high and was_below_orb_low:
                violation_info = " (violated BOTH high and low during capture)"
            elif was_above_orb_high:
                violation_info = " (went above high during capture)"
            elif was_below_orb_low:
                violation_info = " (went below low during capture)"
            
            log.info(
                f"✅ ORB captured for {symbol}: H=${orb_high:.2f}, L=${orb_low:.2f}, "
                f"Range=${orb_range:.2f} ({pct:.3f}%){violation_info}"
            )
            return orb_data
            
        except Exception as e:
            log.error(f"Error capturing ORB for {symbol}: {e}")
            return None
    
    def _get_volume_color(self, symbol: str, intraday_data: List[Dict[str, Any]]) -> str:
        """Get volume color from validation candle (SO_VALIDATION_CANDLE_* PT). Not the opening range (ORB_WINDOW_*)."""
        try:
            prev_candle_start = self.validation_candle_start
            prev_candle_end = self.validation_candle_end
            
            for bar in intraday_data:
                timestamp = bar.get('timestamp', bar.get('datetime'))
                
                # Handle different timestamp formats
                if isinstance(timestamp, str):
                    try:
                        # Try ISO format first
                        if 'T' in timestamp:
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        else:
                            # Try other formats
                            timestamp = datetime.fromisoformat(timestamp)
                    except:
                        # If parsing fails, use current time as fallback
                        timestamp = datetime.now(PT_TZ)
                elif isinstance(timestamp, datetime):
                    # Already a datetime object
                    pass
                else:
                    # Unknown format, use current time
                    timestamp = datetime.now(PT_TZ)
                
                # Ensure timezone awareness
                if timestamp.tzinfo is None:
                    timestamp = PT_TZ.localize(timestamp)
                
                pt_time = timestamp.astimezone(PT_TZ)
                bar_time = pt_time.time()
                
                if prev_candle_start <= bar_time <= prev_candle_end:
                    candle_open = float(bar['open'])
                    candle_close = float(bar['close'])
                    
                    if candle_close > candle_open:
                        return "GREEN"
                    elif candle_close < candle_open:
                        return "RED"
                    else:
                        return "NEUTRAL"
            
            return "NEUTRAL"
            
        except Exception as e:
            log.error(f"Error getting volume color for {symbol}: {e}")
            return "NEUTRAL"
    
    def _get_validation_close_715_value(self, intraday_data: List[Dict[str, Any]], validation_close_715: Optional[float]) -> Optional[float]:
        """Return 7:15 close for logging. Uses explicit value when provided, else extracts from 7:00-7:15 bar. Rev 00289."""
        if validation_close_715 is not None:
            return validation_close_715
        if not intraday_data:
            return None
        prev_candle_start, prev_candle_end = self.validation_candle_start, self.validation_candle_end
        for bar in intraday_data:
            ts = bar.get('timestamp', bar.get('datetime'))
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '+00:00')) if 'T' in ts else datetime.fromisoformat(ts)
                except Exception:
                    continue
            elif not isinstance(ts, datetime):
                continue
            if ts.tzinfo is None:
                ts = PT_TZ.localize(ts)
            bar_time = ts.astimezone(PT_TZ).time()
            if prev_candle_start <= bar_time <= prev_candle_end:
                try:
                    return float(bar['close'])
                except (KeyError, TypeError, ValueError):
                    pass
        return None
    
    def _check_prev_candle_vs_orb(self, symbol: str, intraday_data: List[Dict[str, Any]], orb_level: float, above: bool) -> bool:
        """Check if validation candle (SO_VALIDATION_CANDLE_* PT) closed above/below ORB level."""
        try:
            prev_candle_start = self.validation_candle_start
            prev_candle_end = self.validation_candle_end
            
            for bar in intraday_data:
                timestamp = bar.get('timestamp', bar.get('datetime'))
                
                # Handle different timestamp formats
                if isinstance(timestamp, str):
                    try:
                        # Try ISO format first
                        if 'T' in timestamp:
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        else:
                            # Try other formats
                            timestamp = datetime.fromisoformat(timestamp)
                    except:
                        # If parsing fails, use current time as fallback
                        timestamp = datetime.now(PT_TZ)
                elif isinstance(timestamp, datetime):
                    # Already a datetime object
                    pass
                else:
                    # Unknown format, use current time
                    timestamp = datetime.now(PT_TZ)
                
                # Ensure timezone awareness
                if timestamp.tzinfo is None:
                    timestamp = PT_TZ.localize(timestamp)
                
                pt_time = timestamp.astimezone(PT_TZ)
                bar_time = pt_time.time()
                
                if prev_candle_start <= bar_time <= prev_candle_end:
                    candle_close = float(bar['close'])
                    if above:
                        return candle_close > orb_level
                    else:
                        return candle_close < orb_level
            
            return False
            
        except Exception as e:
            log.error(f"Error checking previous candle for {symbol}: {e}")
            return False
    
    def _validate_post_orb_candle(self, symbol: str, intraday_data: List[Dict[str, Any]]) -> bool:
        """
        Validate post-ORB candle (6:45-7:00 AM PT) to detect sideways/choppy markets.
        
        DISABLES TRADING if the 6:45-7:00 AM candle engulfs BOTH the high AND low 
        of the opening range (6:30-6:45 AM PT). This indicates a sideways market 
        with no clear directional bias.
        
        Returns:
            True if symbol is safe to trade (normal market)
            False if trading should be disabled (choppy market)
        """
        try:
            if symbol not in self.orb_data:
                return True  # No ORB data yet, allow trading
            
            # Check if already validated
            if symbol in self.post_orb_validation:
                validation = self.post_orb_validation[symbol]
                if validation.trading_disabled:
                    log.debug(f"⛔ {symbol} already disabled: {validation.validation_reason}")
                return not validation.trading_disabled
            
            orb = self.orb_data[symbol]
            post_orb_start = self.post_orb_candle_start
            post_orb_end = self.post_orb_candle_end
            
            # Find the 6:45-7:00 AM PT candle
            for bar in intraday_data:
                timestamp = bar.get('timestamp', bar.get('datetime'))
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                
                pt_time = timestamp.astimezone(PT_TZ)
                bar_time = pt_time.time()
                
                # Check if this is the post-ORB candle (6:45-7:00 AM PT)
                if post_orb_start <= bar_time <= post_orb_end:
                    post_orb_high = float(bar['high'])
                    post_orb_low = float(bar['low'])
                    
                    # Check if post-ORB candle breached BOTH ORB high and low
                    breached_high = post_orb_high > orb.orb_high
                    breached_low = post_orb_low < orb.orb_low
                    
                    validation = PostORBValidation(
                        symbol=symbol,
                        validation_time=datetime.now(PT_TZ),
                        post_orb_high=post_orb_high,
                        post_orb_low=post_orb_low,
                        orb_high_breached=breached_high,
                        orb_low_breached=breached_low
                    )
                    
                    # DISABLE trading if BOTH high and low breached (sideways/choppy)
                    if breached_high and breached_low:
                        validation.trading_disabled = True
                        validation.validation_reason = (
                            f"Post-ORB candle ({self.post_orb_candle_start}-{self.post_orb_candle_end} PT) engulfed entire ORB range "
                            f"(H:{post_orb_high:.2f} > {orb.orb_high:.2f}, "
                            f"L:{post_orb_low:.2f} < {orb.orb_low:.2f}) - Sideways market detected"
                        )
                        log.warning(f"⛔ {symbol}: Trading DISABLED - {validation.validation_reason}")
                    else:
                        validation.trading_disabled = False
                        validation.validation_reason = (
                            f"Post-ORB candle normal "
                            f"(High breached: {breached_high}, Low breached: {breached_low})"
                        )
                        log.info(f"✅ {symbol}: Trading ENABLED - {validation.validation_reason}")
                    
                    self.post_orb_validation[symbol] = validation
                    return not validation.trading_disabled
            
            # If no post-ORB candle found yet, allow trading (will check later)
            return True
            
        except Exception as e:
            log.error(f"Error validating post-ORB for {symbol}: {e}")
            return True  # Default to allowing trading on error
    
    # ========================================================================
    # SIGNAL GENERATION
    # ========================================================================
    
    def _evaluate_so_signal(self, symbol: str, current_price: float, intraday_data: List[Dict[str, Any]], volume_color_override: Optional[str] = None, validation_close_715: Optional[float] = None) -> Optional[ORBStrategyResult]:
        """Evaluate Standard Order signal at 7:15 AM PT. Uses volume_color_override (prefetch) when provided (Rev 00269). When validation_close_715 is provided (Rev 00279), use it for rule 3 (validation candle close > ORB high) instead of bar match."""
        try:
            if symbol not in self.orb_data:
                return None
            
            orb = self.orb_data[symbol]
            volume_color = volume_color_override if volume_color_override is not None else self._get_volume_color(symbol, intraday_data)
            
            # Rev 00261: Diagnostic logging for timestamp fix verification
            if symbol in ['SPY', 'QQQ', 'SPX'] and intraday_data:
                first_bar = intraday_data[0] if intraday_data else None
                if first_bar and 'timestamp' in first_bar:
                    bar_ts = first_bar['timestamp']
                    if isinstance(bar_ts, datetime):
                        bar_time = bar_ts.astimezone(PT_TZ).time()
                        log.debug(
                            f"🔍 {symbol} SO Check: intraday_data has {len(intraday_data)} bars, "
                            f"first bar timestamp={bar_ts.strftime('%H:%M:%S %Z')}, bar_time={bar_time}, "
                            f"window_match={self.validation_candle_start <= bar_time <= self.validation_candle_end}"
                        )
            
            # Bullish SO
            # Rev 20251022: Add zero check to prevent division by zero
            if orb.orb_high == 0:
                log.warning(f"⚠️ {symbol}: ORB high is zero, skipping SO evaluation")
                return self._create_rejection_result(symbol, current_price, "SO: ORB high is zero")
            
            distance_from_high = (current_price - orb.orb_high) / orb.orb_high
            
            # DEBUG: Log validation checks for first few symbols
            if distance_from_high >= self.so_bullish_threshold:
                # Rev 00279: Use explicit 7:15 close when provided (same source as volume color); else fall back to bar match
                if validation_close_715 is not None:
                    prev_candle_ok = validation_close_715 > orb.orb_high
                else:
                    prev_candle_ok = self._check_prev_candle_vs_orb(symbol, intraday_data, orb.orb_high, above=True)
                rule2_ok = volume_color == "GREEN"
                # Thin validation candle (open≈close): optional NEUTRAL→GREEN for all symbols (SO + 0DTE ORB share this evaluator).
                if not rule2_ok and volume_color == "NEUTRAL" and self._orb_neutral_as_green_enabled():
                    rule2_ok = True
                    log.info(
                        f"LONG_CHECK {symbol}: NEUTRAL validation color treated as GREEN "
                        f"(0DTE_ORB_TREAT_NEUTRAL_AS_GREEN or legacy VIX env)"
                    )
                close_715_val = self._get_validation_close_715_value(intraday_data, validation_close_715)
                close_str = f"{close_715_val:.2f}" if close_715_val is not None else "N/A"
                # Rev 00289: Consolidated per-symbol log for diagnosis (e.g. CONL) - grep for symbol to see exact rule outcome
                if rule2_ok and prev_candle_ok:
                    outcome = "PASSED"
                else:
                    fails = []
                    if not rule2_ok:
                        fails.append(f"Rule2(vol=GREEN fail, got {volume_color})")
                    if not prev_candle_ok:
                        fails.append(f"Rule3(close_715={close_str} ≤ orb_high={orb.orb_high:.2f})")
                    outcome = f"REJECTED: {'; '.join(fails)}"
                log.info(
                    f"LONG_CHECK {symbol}: price={current_price:.2f} orb_high={orb.orb_high:.2f} vol={volume_color} close_715={close_str} | "
                    f"Rule2(vol=GREEN)={'OK' if rule2_ok else 'FAIL'} Rule3(close>orb_high)={'OK' if prev_candle_ok else 'FAIL'} | {outcome}"
                )
                # Rev 00261: Additional diagnostic if validation fails (SPY/QQQ/SPX)
                if not prev_candle_ok and symbol in ['SPY', 'QQQ', 'SPX']:
                    log.warning(f"   ⚠️ {symbol}: Prev candle validation failed - checking intraday_data: {len(intraday_data)} bars")
                    if intraday_data:
                        for i, bar in enumerate(intraday_data):
                            bar_ts = bar.get('timestamp', bar.get('datetime'))
                            log.warning(f"      Bar {i}: timestamp={bar_ts}, open={bar.get('open')}, close={bar.get('close')}")
                
                if rule2_ok and prev_candle_ok:
                    log.info(f"✅ {symbol}: ALL VALIDATION PASSED - Creating signal!")
                    return self._create_signal(
                        symbol=symbol,
                        signal_type=SignalType.STANDARD_ORDER,
                        side=SignalSide.LONG,
                        current_price=current_price,
                        stop_loss=orb.orb_low,
                        take_profit=current_price * self._tp_long_mult,
                        confidence=min(0.95, 0.7 + abs(distance_from_high) * 10),
                        reasoning=f"Bullish SO: Price {distance_from_high:.2%} above ORB high with GREEN volume",
                        orb_data=orb
                    )
                else:
                    # Rev 00266: Return specific reason for diagnosis (Signal Collection logging)
                    reason = f"SO: Price above ORB high but "
                    if volume_color != "GREEN" and not prev_candle_ok:
                        reason += f"Volume={volume_color} (need GREEN) and prev candle did not close above ORB high"
                    elif volume_color != "GREEN":
                        reason += f"Volume={volume_color} (need GREEN)"
                    else:
                        reason += "prev candle did not close above ORB high"
                    log.info(f"   ❌ REJECTED: {reason}")
                    return self._create_rejection_result(symbol, current_price, reason)
            
            # CRITICAL RULE: NO LONG POSITIONS if price is below ORB high
            # Only allow LONG positions when price > ORB high (momentum signals)
            if current_price < orb.orb_high:
                reason = f"SO: Price ${current_price:.2f} below ORB high ${orb.orb_high:.2f}"
                log.debug(
                    f"LONG_CHECK {symbol}: price={current_price:.4f} < orb_high={orb.orb_high:.4f} — no LONG SO (bearish path is separate)"
                )
                return self._create_rejection_result(symbol, current_price, reason)
            
            log.debug(
                f"LONG_CHECK {symbol}: below bullish distance threshold vs ORB high "
                f"(spot={current_price:.4f} orb_high={orb.orb_high:.4f})"
            )
            return self._create_rejection_result(symbol, current_price, "SO: No signal (price not above ORB high by threshold)")
            
        except Exception as e:
            log.error(f"Error evaluating SO for {symbol}: {e}")
            return self._create_rejection_result(symbol, current_price, f"SO: Error - {str(e)}")
    
    def _evaluate_bearish_so_signal(self, symbol: str, current_price: float, intraday_data: List[Dict[str, Any]], volume_color_override: Optional[str] = None, validation_close_715: Optional[float] = None) -> Optional[ORBStrategyResult]:
        """
        Evaluate Bearish SO signal (for 0DTE PUT options) - Rev 00211.
        Uses volume_color_override (prefetch) when provided (Rev 00269). When validation_close_715 is provided (Rev 00279), use it for rule 3.
        
        Validation Rules (All 3 Required):
        1. Current price at or below ORB low (Rev 00346: includes touch at printed low; no silent gap between buffer and cash low)
        2. Previous close < ORB Low (7:00-7:15 AM candle closed below ORB low)
        3. Red candle (7:00-7:15 AM candle close < open = selling pressure)
        """
        try:
            if symbol not in self.orb_data:
                return None
            
            orb = self.orb_data[symbol]
            volume_color = volume_color_override if volume_color_override is not None else self._get_volume_color(symbol, intraday_data)
            
            # Bearish SO validation
            if orb.orb_low == 0:
                log.warning(f"⚠️ {symbol}: ORB low is zero, skipping bearish SO evaluation")
                return None
            
            distance_from_low = (current_price - orb.orb_low) / orb.orb_low
            
            if current_price <= orb.orb_low:
                # Rev 00279: Use explicit 7:15 close when provided; else fall back to bar match
                if validation_close_715 is not None:
                    prev_candle_ok = validation_close_715 < orb.orb_low
                else:
                    prev_candle_ok = self._check_prev_candle_vs_orb(symbol, intraday_data, orb.orb_low, above=False)
                rule2_ok = volume_color == "RED"
                if not rule2_ok and volume_color == "NEUTRAL" and self._orb_neutral_as_red_enabled():
                    rule2_ok = True
                    log.info(
                        f"SHORT_CHECK {symbol}: NEUTRAL validation color treated as RED "
                        f"(0DTE_ORB_TREAT_NEUTRAL_AS_RED or legacy VIX env)"
                    )
                close_715_val = self._get_validation_close_715_value(intraday_data, validation_close_715)
                close_str = f"{close_715_val:.2f}" if close_715_val is not None else "N/A"
                # Rev 00289: Consolidated per-symbol log for diagnosis - grep for symbol to see exact rule outcome
                if rule2_ok and prev_candle_ok:
                    outcome = "PASSED"
                else:
                    fails = []
                    if not rule2_ok:
                        fails.append(f"Rule2(vol=RED fail, got {volume_color})")
                    if not prev_candle_ok:
                        fails.append(f"Rule3(close_715={close_str} ≥ orb_low={orb.orb_low:.2f})")
                    outcome = f"REJECTED: {'; '.join(fails)}"
                log.info(
                    f"SHORT_CHECK {symbol}: price={current_price:.2f} orb_low={orb.orb_low:.2f} vol={volume_color} close_715={close_str} | "
                    f"Rule2(vol=RED)={'OK' if rule2_ok else 'FAIL'} Rule3(close<orb_low)={'OK' if prev_candle_ok else 'FAIL'} | {outcome}"
                )
                
                if rule2_ok and prev_candle_ok:
                    log.info(f"✅ {symbol}: ALL BEARISH VALIDATION PASSED - Creating SHORT signal!")
                    return self._create_signal(
                        symbol=symbol,
                        signal_type=SignalType.STANDARD_ORDER,
                        side=SignalSide.SHORT,  # SHORT for PUT options
                        current_price=current_price,
                        stop_loss=orb.orb_high,
                        take_profit=current_price * self._tp_short_mult,
                        confidence=min(0.95, 0.7 + abs(distance_from_low) * 10),
                        reasoning=f"Bearish SO: Price {distance_from_low:.2%} below ORB low with RED volume",
                        orb_data=orb
                    )
                else:
                    # Rev 00288: Return rejection result so 0DTE Short bypass can run (price near/below ORB low)
                    reason = f"SO: Price below ORB low but "
                    if volume_color != "RED" and not prev_candle_ok:
                        reason += f"Volume={volume_color} (need RED) and prev candle did not close below ORB low"
                    elif volume_color != "RED":
                        reason += f"Volume={volume_color} (need RED)"
                    else:
                        reason += "prev candle did not close below ORB low"
                    log.info(f"   ❌ REJECTED: {reason}")
                    return self._create_rejection_result(symbol, current_price, reason)
            
            log.debug(
                f"SHORT_CHECK {symbol}: price={current_price:.4f} > orb_low={orb.orb_low:.4f} "
                f"— no bearish SO / PUT (price above ORB low)"
            )
            return None
            
        except Exception as e:
            log.error(f"Error evaluating bearish SO for {symbol}: {e}")
            return None
    
    def _evaluate_orr_signal(self, symbol: str, current_price: float, intraday_data: List[Dict[str, Any]]) -> Optional[ORBStrategyResult]:
        """Evaluate Opening Range Reversal signal - triggers on CROSSING events only"""
        try:
            if symbol not in self.orb_data:
                return None
            
            orb = self.orb_data[symbol]
            
            # Initialize reversal state
            if symbol not in self.reversal_states:
                self.reversal_states[symbol] = ORRReversalState(symbol=symbol)
            
            state = self.reversal_states[symbol]
            
            # CRITICAL RULE (Rev 00180): Check price position FIRST - MUST be above ORB high to proceed
            # NO positions allowed if price is below ORB high (fundamental rule)
            if current_price <= orb.orb_high:
                # Price at or below ORB high - NO LONG positions allowed
                # Still track if we go below ORB low for future ORR detection
                if current_price < orb.orb_low and not state.was_below_orb_low:
                    state.was_below_orb_low = True
                    state.first_below_timestamp = datetime.now(PT_TZ)
                    log.debug(f"📉 {symbol}: Price ${current_price:.2f} below ORB low ${orb.orb_low:.2f} (tracking for future ORR)")
                return None  # Exit early - no signals below/at ORB high
            
            # At this point: current_price > orb.orb_high (confirmed above ORB high)
            
            # STEP 1: Track if price was previously BELOW ORB low (required for V-shaped reversal)
            # This tracking happens continuously, but we already know price is currently ABOVE ORB high
            # So this checks historical state only
            if not state.was_below_orb_low:
                # Price is above ORB high but was never below ORB low - no V-shape possible
                log.debug(f"⏭️ {symbol}: Price ${current_price:.2f} above ORB high but never went below ORB low - no V-shaped reversal")
                return None
            
            # STEP 2: Bullish ORR - Detect FIRST TIME crossing above ORB high (after V-shape confirmed)
            if not state.was_above_orb_high:
                # Price just crossed above ORB high for the FIRST TIME
                state.was_above_orb_high = True
                state.first_above_timestamp = datetime.now(PT_TZ)
                
                # Trigger Bullish ORR (V-shaped reversal confirmed)
                if not state.bullish_orr_triggered:
                    state.bullish_orr_triggered = True
                    log.info(f"🔔 Bullish ORR triggered for {symbol}: Price ${current_price:.2f} crossed above ORB high ${orb.orb_high:.2f} after being below ORB low ${orb.orb_low:.2f}")
                    return self._create_signal(
                        symbol=symbol,
                        signal_type=SignalType.OPENING_RANGE_REVERSAL,
                        side=SignalSide.LONG,
                        current_price=current_price,
                        stop_loss=orb.orb_low,
                        take_profit=current_price * self._tp_long_mult,
                        confidence=0.85,
                        reasoning=f"Bullish ORR: V-shaped reversal from below ORB low ${orb.orb_low:.2f} to above ORB high ${orb.orb_high:.2f}",
                        orb_data=orb
                    )
            else:
                # Already triggered ORR for this symbol today
                log.debug(f"⏭️ {symbol}: Bullish ORR already triggered today")
                return None
            
            return None
            
        except Exception as e:
            log.error(f"Error evaluating ORR for {symbol}: {e}")
            return None
    
    def _create_rejection_result(self, symbol: str, current_price: float, reasoning: str) -> ORBStrategyResult:
        """Create ORBStrategyResult with should_trade=False for diagnosis (Rev 00266)."""
        return ORBStrategyResult(
            symbol=symbol,
            should_trade=False,
            signal_type=None,
            side=SignalSide.LONG,
            confidence=0.0,
            entry_price=current_price,
            stop_loss=None,
            take_profit=None,
            position_size_pct=0.0,
            reasoning=reasoning,
            inverse_symbol=None,
            orb_data=None,
            metadata={}
        )
    
    def _create_signal(self, symbol: str, signal_type: SignalType, side: SignalSide,
                      current_price: float, stop_loss: float, take_profit: float,
                      confidence: float, reasoning: str, orb_data: ORBData,
                      inverse_symbol: Optional[str] = None) -> ORBStrategyResult:
        """Create ORB strategy result"""
        
        # Calculate position size (SO_ETF_POSITION_SIZE_PCT_* / SO_ETF_SO_SIGNAL_BONUS_PCT)
        if confidence >= 0.8:
            position_size = self._pos_size_high
        elif confidence >= 0.6:
            position_size = self._pos_size_med
        else:
            position_size = self._pos_size_low

        if signal_type == SignalType.STANDARD_ORDER:
            position_size += self._so_signal_bonus_pct

        position_size = min(self.max_position_size, position_size)
        
        # Trading symbol (original or inverse)
        trading_symbol = inverse_symbol if inverse_symbol else symbol
        
        md: Dict[str, Any] = {
            'original_symbol': symbol,
            'is_inverse': inverse_symbol is not None,
            'signal_type_name': signal_type.value,
            'original_side': side.value  # Keep track of original side for debugging
        }

        result = ORBStrategyResult(
            symbol=trading_symbol,
            should_trade=True,
            signal_type=signal_type,
            side=side,  # Use the correct side passed in (LONG for inverse ETFs)
            confidence=confidence,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size_pct=position_size,
            reasoning=reasoning,
            inverse_symbol=inverse_symbol,
            orb_data=orb_data.to_dict() if orb_data else None,
            metadata=md
        )

        # Validation-only normalized execution intent (non-breaking; no flow changes).
        intent = ExecutionIntent(
            symbol=result.symbol,
            side="LONG",
            strategy_type="ORB_SO",
            asset_type="equity",
            structure_type="equity_long",
            confidence=result.confidence,
            entry_price=result.entry_price,
            stop_loss=result.stop_loss,
            take_profit=result.take_profit,
            metadata={
                "orb_range": (result.orb_data or {}).get("orb_range_pct") if isinstance(result.orb_data, dict) else None,
                "volume_ratio": (result.orb_data or {}).get("volume_ratio") if isinstance(result.orb_data, dict) else None,
                "reasoning": result.reasoning,
            },
        )
        log.info(
            f"EXECUTION_INTENT_ETF | {intent.symbol} | {intent.strategy_type} | "
            f"{intent.structure_type} | conf={intent.confidence:.2f}"
        )

        return result
    
    # ========================================================================
    # MAIN ANALYSIS METHOD
    # ========================================================================
    
    async def analyze_symbol(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        *,
        within_so: Optional[bool] = None,
        within_orr: Optional[bool] = None,
    ) -> ORBStrategyResult:
        """
        Analyze a symbol using ORB Primary Strategy
        
        Args:
            symbol: Symbol to analyze
            market_data: Market data including current price and intraday data
        
        Returns:
            ORBStrategyResult with trading recommendation
        """
        try:
            # CRITICAL: Check for inverse pair conflicts BEFORE analysis
            # This prevents both GUSH and DRIP from generating signals on the same day
            potential_trading_symbol = self.inverse_mapping.get(symbol, symbol)
            
            # Check if either the symbol or its inverse pair has already generated a signal today
            inverse_symbol = self.inverse_mapping.get(symbol)
            if inverse_symbol and inverse_symbol in self.executed_symbols_today:
                log.info(f"⏭️  {symbol}: Inverse pair {inverse_symbol} already executed today, preventing conflict")
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.LONG,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning=f"Inverse pair {inverse_symbol} already executed today - preventing conflict"
                )
            
            # Also check if the symbol itself has already been executed
            if symbol in self.executed_symbols_today:
                log.info(f"⏭️  {symbol}: Already executed today, skipping duplicate")
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.LONG,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning=f"{symbol} already executed today"
                )
            
            # Check time windows.
            # Rev 00315: PrimeTradingSystem computes `within_so/within_orr` once per scan. Passing them here
            # prevents per-symbol evaluations crossing the 7:30 boundary mid-scan (e.g., scan started at 7:29:58
            # but later symbols evaluated at 7:30:02) from being rejected as "Outside trading windows".
            if within_so is None:
                within_so = self._is_within_so_window()
            if within_orr is None:
                within_orr = self._is_within_orr_window()
            
            if not within_so and not within_orr:
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.LONG,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning=f"Outside trading windows (Current: {self._get_current_time_pt()})"
                )
            
            # Get intraday data
            intraday_data = market_data.get('intraday_data', [])
            if not intraday_data:
                # Create minimal bar from quote
                intraday_data = [{
                    'timestamp': datetime.now(),
                    'open': market_data.get('open_price', market_data.get('current_price', 0.0)),
                    'high': market_data.get('high_price', market_data.get('current_price', 0.0)),
                    'low': market_data.get('low_price', market_data.get('current_price', 0.0)),
                    'close': market_data.get('current_price', 0.0),
                    'volume': market_data.get('volume', 0)
                }]
            
            # Capture ORB if not already done
            if symbol not in self.orb_data:
                self._capture_opening_range(symbol, intraday_data)
            
            if symbol not in self.orb_data:
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.LONG,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning="No ORB data available (outside 6:30-6:45 AM PT window)"
                )
            
            # CRITICAL: Validate post-ORB candle (6:45-7:00 AM PT)
            # Disables trading if 6:45-7:00 candle engulfed BOTH ORB high AND low
            # This filters out sideways/choppy markets (~1% of symbols)
            if not self._validate_post_orb_candle(symbol, intraday_data):
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.LONG,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning=f"Trading disabled: Post-ORB candle engulfed entire range (sideways market)"
                )
            
            current_price = market_data.get('current_price', 0.0)
            
            # Try SO signal (within SO window). Use prefetched volume_color and validation_close_715 when provided (Rev 00269, 00279).
            if within_so:
                result = self._evaluate_so_signal(
                    symbol, current_price, intraday_data,
                    volume_color_override=market_data.get('volume_color'),
                    validation_close_715=market_data.get('validation_close_715')
                )
                if result and result.should_trade:
                    # Rev 00046: CRITICAL FIX - DO NOT mark SO signals as executed during generation!
                    # SO signals are collected at 7:15-7:30 AM but executed at 7:30 AM (batch)
                    # Marking them as "executed" during generation prevents all signals!
                    # 
                    # The executed_symbols_today check is moved to _process_orb_signals (after execution)
                    # This allows signals to be GENERATED during 7:15-7:30 AM and EXECUTED at 7:30 AM
                    
                    # Rev 00163: Check if trading_symbol already executed today (prevent duplicates)
                    # ONLY check, don't mark yet - marking happens AFTER execution at 7:30 AM
                    trading_symbol = result.symbol  # Could be inverse
                    if trading_symbol in self.executed_symbols_today:
                        log.info(f"⏭️  {symbol} → {trading_symbol} SO: Already executed today, skipping duplicate")
                        result.should_trade = False
                        result.reasoning = f"{trading_symbol} SO already executed today"
                        return result
                    
                    # Check daily limit
                    if not self.trade_counter.has_traded_so_today(symbol):
                        # Rev 00046: REMOVED premature marking - now happens after execution in prime_trading_system.py
                        # Symbols are marked as executed in prime_trading_system._process_orb_signals()
                        # AFTER actual execution at 7:30 AM (not during generation at 7:15-7:30 AM)
                        
                        # Rev 00055: Log signal timing for collection window analysis
                        # Rev 00064: Removed redundant datetime import (already imported at top line 54)
                        from zoneinfo import ZoneInfo
                        pt_tz = ZoneInfo('America/Los_Angeles')
                        now_pt = datetime.now(pt_tz)
                        signal_time = now_pt.strftime('%H:%M:%S')
                        log.info(f"✅ {trading_symbol} SO: Signal generated at {signal_time} PT, will be executed at 7:30 AM PT")
                        
                        return result
                    else:
                        result.should_trade = False
                        result.reasoning = f"Already traded SO for {symbol} today"
                        return result
                    # Rev 00266: Return result with specific rejection reason for Signal Collection diagnosis
                if result is not None:
                    return result
            
            # Try ORR signal (within ORR window)
            if within_orr:
                result = self._evaluate_orr_signal(symbol, current_price, intraday_data)
                if result and result.should_trade:
                    # Rev 00163: Check if trading_symbol already executed today (prevent duplicates)
                    trading_symbol = result.symbol  # Could be inverse symbol
                    if trading_symbol in self.executed_symbols_today:
                        log.info(f"⏭️  {symbol} → {trading_symbol} ORR: Already executed today, skipping duplicate")
                        result.should_trade = False
                        result.reasoning = f"{trading_symbol} ORR already executed today"
                        return result
                    
                    # Check daily limit on TRADING SYMBOL (not original symbol)
                    # This prevents both SPYU Inverse ORR → SPXS and SPXS ORR → SPXS on same day
                    if not self.trade_counter.has_traded_orr_today(trading_symbol):
                        # Rev 00046: REMOVED premature marking for ORR (same bug as SO)
                        # Marking happens AFTER actual execution in prime_trading_system._process_orb_signals()
                        # self.executed_symbols_today.add(trading_symbol)  # ❌ REMOVED
                        # self.executed_symbols_today.add(symbol)  # ❌ REMOVED
                        # inverse_symbol = self.inverse_mapping.get(symbol)
                        # if inverse_symbol:
                        #     self.executed_symbols_today.add(inverse_symbol)  # ❌ REMOVED
                        log.info(f"✅ {trading_symbol} ORR: Signal generated, will be executed immediately")
                        return result
                    else:
                        result.should_trade = False
                        result.reasoning = f"Already traded ORR on {trading_symbol} today (via {symbol})"
                        return result
            
            # No signal
            return ORBStrategyResult(
                symbol=symbol,
                should_trade=False,
                signal_type=None,
                side=SignalSide.LONG,
                confidence=0.0,
                entry_price=current_price,
                stop_loss=None,
                take_profit=None,
                position_size_pct=0.0,
                reasoning="No ORB signal (rules not met)"
            )
            
        except Exception as e:
            log.error(f"Error analyzing {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return ORBStrategyResult(
                symbol=symbol,
                should_trade=False,
                signal_type=None,
                side=SignalSide.LONG,
                confidence=0.0,
                entry_price=market_data.get('current_price', 0.0),
                stop_loss=None,
                take_profit=None,
                position_size_pct=0.0,
                reasoning=f"Error: {str(e)}"
            )
    
    async def analyze_bearish_symbol(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        *,
        within_so: Optional[bool] = None,
    ) -> ORBStrategyResult:
        """
        Analyze symbol for bearish SO signal (for 0DTE PUT options) - Rev 00211
        
        Same validation flow as analyze_symbol but evaluates bearish signals.
        Used by 0DTE Strategy to generate PUT option signals.
        
        Validation Rules (All 3 Required):
        1. Current price ≤ ORB Low × 0.999 (-0.1% buffer)
        2. Previous close < ORB Low (7:00-7:15 AM candle closed below ORB low)
        3. Red candle (7:00-7:15 AM candle close < open = selling pressure)
        """
        try:
            # Check if symbol has ORB data
            if symbol not in self.orb_data:
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.SHORT,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning="No ORB data available"
                )
            
            # Check time windows (only SO window for bearish signals).
            # Rev 00315: Use scan-level `within_so` when provided to avoid crossing cutoff mid-scan.
            if within_so is None:
                within_so = self._is_within_so_window()
            
            if not within_so:
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.SHORT,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning=f"Outside SO window (Current: {self._get_current_time_pt()})"
                )
            
            # Get intraday data
            intraday_data = market_data.get('intraday_data', [])
            if not intraday_data:
                # Create minimal bar from quote
                intraday_data = [{
                    'timestamp': datetime.now(),
                    'open': market_data.get('open_price', market_data.get('current_price', 0.0)),
                    'high': market_data.get('high_price', market_data.get('current_price', 0.0)),
                    'low': market_data.get('low_price', market_data.get('current_price', 0.0)),
                    'close': market_data.get('current_price', 0.0),
                    'volume': market_data.get('volume', 0)
                }]
            
            # Validate post-ORB candle (same as bullish)
            if not self._validate_post_orb_candle(symbol, intraday_data):
                return ORBStrategyResult(
                    symbol=symbol,
                    should_trade=False,
                    signal_type=None,
                    side=SignalSide.SHORT,
                    confidence=0.0,
                    entry_price=market_data.get('current_price', 0.0),
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning=f"Trading disabled: Post-ORB candle engulfed entire range (sideways market)"
                )
            
            current_price = market_data.get('current_price', 0.0)
            
            # Try bearish SO signal (within SO window). Use prefetched volume_color when provided (Rev 00269).
            if within_so:
                result = self._evaluate_bearish_so_signal(
                    symbol, current_price, intraday_data,
                    volume_color_override=market_data.get('volume_color'),
                    validation_close_715=market_data.get('validation_close_715')
                )
                if result and result.should_trade:
                    # Check if already executed today
                    if symbol in self.executed_symbols_today:
                        result.should_trade = False
                        result.reasoning = f"Already traded bearish SO for {symbol} today"
                        return result
                    
                    log.info(f"✅ {symbol} Bearish SO: Signal generated for PUT options")
                    return result
            
            return ORBStrategyResult(
                symbol=symbol,
                should_trade=False,
                signal_type=None,
                side=SignalSide.SHORT,
                confidence=0.0,
                entry_price=current_price,
                stop_loss=None,
                take_profit=None,
                position_size_pct=0.0,
                reasoning="No valid bearish SO signal"
            )
            
        except Exception as e:
            log.error(f"Error analyzing bearish {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return ORBStrategyResult(
                symbol=symbol,
                should_trade=False,
                signal_type=None,
                side=SignalSide.SHORT,
                confidence=0.0,
                entry_price=market_data.get('current_price', 0.0),
                stop_loss=None,
                take_profit=None,
                position_size_pct=0.0,
                reasoning=f"Error: {str(e)}"
            )
    
    # ========================================================================
    # INVERSE PAIR CONFLICT RESOLUTION (Rev 00167)
    # ========================================================================
    
    def resolve_inverse_pair_conflict(self, symbol1: str, signal1: ORBStrategyResult, 
                                    symbol2: str, signal2: ORBStrategyResult) -> ORBStrategyResult:
        """
        Resolve conflicts between inverse pairs by choosing the stronger signal
        
        Criteria (Rev 00348 — continuation hygiene, not blow-off size):
        1. Higher ``calculate_signal_quality_score`` (confidence tempered by range / extension drag)
        2. Prefer direct signals over inverse signals when scores tie (momentum vs reversal)
        
        Returns:
            ORBStrategyResult for the stronger signal, with conflict resolution note
        """
        try:
            # Calculate quality scores for both signals
            score1 = self.calculate_signal_quality_score(signal1)
            score2 = self.calculate_signal_quality_score(signal2)
            
            # Choose the higher scoring signal
            if score1 > score2:
                stronger_signal = signal1
                weaker_symbol = symbol2
                stronger_symbol = symbol1
                stronger_score = score1
                weaker_score = score2
            else:
                stronger_signal = signal2
                weaker_symbol = symbol1
                stronger_symbol = symbol2
                stronger_score = score2
                weaker_score = score1
            
            # Add conflict resolution note to reasoning
            stronger_signal.reasoning += f" [CONFLICT RESOLVED: {stronger_symbol} (score: {stronger_score:.2f}) vs {weaker_symbol} (score: {weaker_score:.2f})]"
            
            log.info(f"🔀 Inverse pair conflict resolved: {stronger_symbol} (score: {stronger_score:.2f}) chosen over {weaker_symbol} (score: {weaker_score:.2f})")
            
            return stronger_signal
            
        except Exception as e:
            log.error(f"Error resolving inverse pair conflict between {symbol1} and {symbol2}: {e}")
            # Default to first signal if error
            return signal1
    
    # ========================================================================
    # SIGNAL RANKING (Rev 00163)
    # ========================================================================
    
    def calculate_signal_quality_score(self, signal: ORBStrategyResult) -> float:
        """
        Inverse-pair tie-break score (Rev 00348): prefer **continuation hygiene**, not
        ``confidence × wide ORB × farthest extension`` (that stack rewards exhaustion spikes).
        """
        try:
            orb = self.orb_data.get(signal.symbol)
            if not orb:
                return 0.0
            
            confidence_score = float(signal.confidence or 0.0)
            
            orb_range_pct = float(getattr(orb, 'orb_range_pct', 0) or 0)
            if orb_range_pct <= 0 and orb.orb_low > 0:
                orb_range_pct = compute_orb_range_pct(orb.orb_high, orb.orb_low)
            # Mild preference for orderly opens — very wide range is a drag, not a boost.
            range_factor = 1.0 / (1.0 + max(0.0, orb_range_pct - 1.2) * 0.08)
            
            if signal.signal_type == SignalType.STANDARD_ORDER:
                if signal.entry_price > orb.orb_high and orb.orb_high > 0:
                    distance_pct = (signal.entry_price - orb.orb_high) / orb.orb_high
                elif signal.entry_price < orb.orb_low and orb.orb_low > 0:
                    distance_pct = (orb.orb_low - signal.entry_price) / orb.orb_low
                else:
                    distance_pct = 0.0
                chase_pct = max(0.0, distance_pct * 100.0)
                extension_drag = 1.0 / (1.0 + chase_pct * 0.06)
            else:
                extension_drag = 1.0
            
            quality_score = confidence_score * range_factor * extension_drag
            
            return max(0.0, min(2.5, quality_score))
            
        except Exception as e:
            log.error(f"Error calculating quality score for {signal.symbol}: {e}")
            return 0.0
    
    # ========================================================================
    # TRADE MANAGEMENT
    # ========================================================================
    
    def record_trade(self, symbol: str, signal_type: SignalType, **kwargs):
        """Record a trade execution (use trading_symbol if provided for inverse trades)"""
        # Use trading_symbol if provided (for inverse trades), otherwise use original symbol
        trading_symbol = kwargs.get('trading_symbol', symbol)
        self.trade_counter.record_trade(trading_symbol, signal_type, **kwargs)
    
    def reset_daily(self):
        """Reset for new trading day"""
        self.trade_counter.reset_daily()
        self.orb_data.clear()
        self.reversal_states.clear()
        self.post_orb_validation.clear()
        self.executed_symbols_today.clear()  # Rev 00163: Reset executed symbols
        log.info("🔄 Prime ORB Strategy Manager reset for new trading day")
        log.info("🔄 Executed symbols tracking reset")

    def load_orb_snapshot(self, snapshot: Dict[str, Dict[str, Any]]) -> int:
        """Load ORB data from a persisted snapshot dictionary."""
        if not snapshot:
            return 0
        
        loaded = 0
        self.orb_data.clear()
        self.reversal_states.clear()
        
        for symbol, data in snapshot.items():
            try:
                capture_time_str = data.get("capture_time")
                capture_dt: Optional[datetime] = None
                if capture_time_str:
                    try:
                        capture_dt = datetime.fromisoformat(capture_time_str.replace("Z", "+00:00"))
                    except ValueError:
                        capture_dt = datetime.now(PT_TZ)

                def _parse_opt_ts(key: str) -> Optional[datetime]:
                    raw = data.get(key)
                    if not raw:
                        return None
                    try:
                        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    except ValueError:
                        return None

                _oh = float(data.get("orb_high", 0.0))
                _ol = float(data.get("orb_low", 0.0))
                _pct = data.get("orb_range_pct")
                if _pct is not None:
                    _pct_f = float(_pct)
                else:
                    _pct_f = compute_orb_range_pct(_oh, _ol)
                orb_data = ORBData(
                    symbol=symbol,
                    orb_high=_oh,
                    orb_low=_ol,
                    orb_open=float(data.get("orb_open", 0.0)),
                    orb_close=float(data.get("orb_close", 0.0)),
                    orb_volume=float(data.get("orb_volume", 0.0)),
                    orb_range=float(data.get("orb_range", _oh - _ol)),
                    orb_range_pct=_pct_f,
                    orb_is_green=bool(data.get("orb_is_green", False)),
                    capture_time=capture_dt or datetime.now(PT_TZ),
                    orb_high_extreme_ts=_parse_opt_ts("orb_high_extreme_ts"),
                    orb_low_extreme_ts=_parse_opt_ts("orb_low_extreme_ts"),
                )
                self.orb_data[symbol] = orb_data
                self.reversal_states[symbol] = ORRReversalState(symbol=symbol)
                loaded += 1
            except Exception as load_error:
                log.warning(f"⚠️ Failed to load ORB snapshot for {symbol}: {load_error}")
        
        if loaded:
            log.info(f"☁️ ORB snapshot applied ({loaded} symbols)")
        else:
            log.warning("⚠️ ORB snapshot applied but contained no valid symbols")
        return loaded
    
    def get_strategy_status(self) -> Dict[str, Any]:
        """Get current strategy status"""
        # Count disabled symbols
        disabled_count = sum(1 for v in self.post_orb_validation.values() if v.trading_disabled)
        enabled_count = len(self.post_orb_validation) - disabled_count
        
        return {
            'manager': 'Prime ORB Strategy Manager',
            'within_so_window': self._is_within_so_window(),
            'within_orr_window': self._is_within_orr_window(),
            'current_time_pt': str(self._get_current_time_pt()),
            'trade_counter': self.trade_counter.get_status(),
            'inverse_etfs_loaded': len(self.inverse_mapping),
            'target_gain_pct': self.target_gain_pct,
            'orb_data_captured': len(self.orb_data),
            'reversal_states_tracked': len(self.reversal_states),
            'post_orb_validation': {
                'total_validated': len(self.post_orb_validation),
                'enabled_for_trading': enabled_count,
                'disabled_sideways': disabled_count
            }
        }

# Factory function
# SINGLETON INSTANCE - FIX for ORB data persistence (Rev 20251020)
_prime_orb_strategy_manager_singleton = None

def get_prime_orb_strategy_manager(data_manager=None) -> PrimeORBStrategyManager:
    """
    Get Prime ORB Strategy Manager instance (SINGLETON)
    
    CRITICAL FIX (Oct 20, 2025): Changed to singleton pattern to ensure
    ORB data persists between capture (6:45 AM) and SO scanning (7:15+ AM).
    
    Previous bug: Created new instance each time, so ORB data captured at
    6:45 AM was not accessible during SO scanning.
    """
    global _prime_orb_strategy_manager_singleton
    
    if _prime_orb_strategy_manager_singleton is None:
        _prime_orb_strategy_manager_singleton = PrimeORBStrategyManager(data_manager=data_manager)
        log.info("🔧 Created SINGLETON Prime ORB Strategy Manager instance")
    else:
        # Update data manager if provided (but keep same instance)
        if data_manager is not None:
            _prime_orb_strategy_manager_singleton.data_manager = data_manager
    
    return _prime_orb_strategy_manager_singleton
