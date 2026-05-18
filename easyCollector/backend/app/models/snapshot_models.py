"""
Easy Collector - Snapshot Models
Pydantic models for market snapshot data collection
Master schema that unifies ORB Priority Optimizer and Ichimoku Historical Enhancer datapoints
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


class MarketType(str, Enum):
    """Market type enumeration"""
    US = "US"
    CRYPTO = "CRYPTO"


class SnapshotType(str, Enum):
    """Snapshot type enumeration"""
    ORB = "ORB"
    SIGNAL = "SIGNAL"
    OUTCOME = "OUTCOME"


class CryptoSession(str, Enum):
    """Crypto trading session enumeration"""
    LONDON = "LONDON"
    US = "US"
    RESET = "RESET"
    ASIA = "ASIA"


class SessionBounds(BaseModel):
    """Session bounds/anchors for ML + debugging (prevents time-window confusion)"""
    open_et: Optional[datetime] = None
    orb_end_et: Optional[datetime] = None
    signal_et: Optional[datetime] = None
    outcome_et: Optional[datetime] = None
    early_close_et: Optional[datetime] = None  # US only


class CalendarTags(BaseModel):
    """Calendar and event tags"""
    is_market_closed: bool = False
    is_us_holiday: bool = False
    holiday_name: Optional[str] = None
    is_low_volume_holiday: bool = False
    is_early_close: bool = False
    early_close_time_et: Optional[str] = None  # HH:MM format
    is_macro_event_day: bool = False
    macro_events: List[str] = Field(default_factory=list)
    is_fed_day: bool = False
    liquidity_risk_flag: bool = False


class PriceCandleData(BaseModel):
    """Price and candle structure data"""
    open: float
    high: float
    low: float
    close: float
    last_price: float
    prev_close: Optional[float] = None
    gap_pct: Optional[float] = None
    range_pct: Optional[float] = None
    body_pct: Optional[float] = None
    upper_wick_pct: Optional[float] = None
    lower_wick_pct: Optional[float] = None
    position_in_range: Optional[float] = None  # 0-100%
    range_vs_atr: Optional[float] = None
    candle_strength: Optional[float] = None


class ORBBlock(BaseModel):
    """Opening Range Breakout block data"""
    orb_open: Optional[float] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_close: Optional[float] = None
    orb_mid: Optional[float] = None  # (orb_high + orb_low) / 2
    orb_range_pct: Optional[float] = None
    orb_volume: Optional[float] = None  # float for crypto; int-like for US
    orb_volume_ratio: Optional[float] = None
    orb_break_state: Optional[str] = None  # "NONE", "BREAKOUT", "RETEST", etc.
    orb_retest_count: Optional[int] = None
    orb_failed_break_flag: Optional[bool] = None
    # Post-ORB extremes (ORB end → Signal/Outcome)
    post_orb_high: Optional[float] = None
    post_orb_low: Optional[float] = None
    post_orb_range_pct: Optional[float] = None
    # State flags at Signal/Outcome
    is_making_new_low_since_orb: Optional[bool] = None
    is_making_new_high_since_orb: Optional[bool] = None
    is_inside_post_orb_range: Optional[bool] = None
    pos_in_orb_range: Optional[float] = None  # Position in ORB range (0-100%)
    pos_in_post_orb_range: Optional[float] = None  # Position in post-ORB range (0-100%)
    # Distances to levels
    dist_to_orb_low_pct: Optional[float] = None
    dist_to_orb_high_pct: Optional[float] = None
    dist_to_post_orb_low_pct: Optional[float] = None
    dist_to_post_orb_high_pct: Optional[float] = None
    # Level interaction counts
    orb_low_break_count_post_orb: Optional[int] = None
    orb_low_reclaim_count_post_orb: Optional[int] = None
    minutes_below_orb_low_post_orb: Optional[int] = None
    orb_high_break_count_post_orb: Optional[int] = None
    orb_high_reclaim_count_post_orb: Optional[int] = None
    minutes_above_orb_high_post_orb: Optional[int] = None
    # Categorical states for ML bucketing and rules
    orb_position_state: Optional[str] = None  # "BELOW_ORB_LOW", "AT_ORB_LOW", "IN_RANGE_LOW", "MID_RANGE", "IN_RANGE_HIGH", "AT_ORB_HIGH", "ABOVE_ORB_HIGH"
    orb_range_quality: Optional[str] = None  # "TIGHT", "NORMAL", "WIDE" (useful for trap day detection)
    post_orb_behavior: Optional[str] = None  # "TREND", "CHOP", "FAKEOUT" (useful for trap day detection)
    # ORB Integrity flags (enforcement)
    orb_integrity_ok: Optional[bool] = None  # True if ORB window has expected bars
    orb_expected_bars: Optional[int] = None  # Expected number of bars in ORB window
    orb_actual_bars: Optional[int] = None  # Actual number of bars in ORB window


class TrendMomentumData(BaseModel):
    """Trend and momentum indicators"""
    ema_8: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    ema_trend_8_21: Optional[str] = None  # "BULLISH", "BEARISH", "NEUTRAL"
    ema_trend_21_50: Optional[str] = None
    roc: Optional[float] = None  # Rate of Change
    momentum_1bar: Optional[float] = None
    momentum_5bar: Optional[float] = None
    momentum_10bar: Optional[float] = None
    trend_direction: Optional[str] = None  # "UP", "DOWN", "SIDEWAYS"
    trend_strength_score: Optional[float] = None  # 0-100


class VolatilityData(BaseModel):
    """Volatility indicators"""
    atr: Optional[float] = None
    atr_pct: Optional[float] = None
    atr_pct_change: Optional[float] = None
    volatility: Optional[float] = None  # Annualized
    volatility_regime: Optional[str] = None  # "LOW", "NORMAL", "HIGH", "EXTREME"
    range_expansion_flag: Optional[bool] = None
    compression_flag: Optional[bool] = None


class VolumeVWAPData(BaseModel):
    """Volume and VWAP indicators. volume/volume_sma/volume_delta are float for crypto-safe handling."""
    volume: Optional[float] = None  # Optional; float for crypto (fractional volume)
    volume_sma: Optional[float] = None
    volume_ratio: Optional[float] = None
    volume_acceleration: Optional[float] = None
    volume_delta: Optional[float] = None
    vwap: Optional[float] = None
    vwap_distance_pct: Optional[float] = None
    vwap_momentum: Optional[float] = None
    price_vs_vwap_direction: Optional[str] = None  # "ABOVE", "BELOW", "AT"


class OscillatorData(BaseModel):
    """Oscillator indicators"""
    rsi: Optional[float] = None
    rsi_slope: Optional[float] = None
    rsi_acceleration: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    stoch_position: Optional[str] = None  # "OVERBOUGHT", "OVERSOLD", "NEUTRAL"
    williams_r: Optional[float] = None
    cci: Optional[float] = None
    mfi: Optional[float] = None  # Money Flow Index
    cmf: Optional[float] = None  # Chaikin Money Flow


class MACDData(BaseModel):
    """MACD indicators"""
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    macd_slope: Optional[float] = None
    momentum_regime: Optional[str] = None  # "BULLISH", "BEARISH", "NEUTRAL"


class BollingerData(BaseModel):
    """Bollinger Bands indicators"""
    bollinger_upper: Optional[float] = None
    bollinger_middle: Optional[float] = None
    bollinger_lower: Optional[float] = None
    bollinger_width: Optional[float] = None
    bollinger_position: Optional[float] = None  # 0-100%
    bb_squeeze_flag: Optional[bool] = None
    bb_expansion_flag: Optional[bool] = None


class IchimokuData(BaseModel):
    """Ichimoku Cloud indicators (required for crypto, optional for US)"""
    tenkan: Optional[float] = None
    kijun: Optional[float] = None
    senkou_a: Optional[float] = None
    senkou_b: Optional[float] = None
    cloud_thickness_pct: Optional[float] = None
    price_vs_cloud_state: Optional[str] = None  # "ABOVE", "BELOW", "IN"
    tk_cross_state: Optional[str] = None  # "BULLISH", "BEARISH", "NONE"
    chikou_confirmation: Optional[str] = None  # "BULLISH", "BEARISH", "NONE"
    future_cloud_bias: Optional[str] = None  # "BULLISH", "BEARISH", "NEUTRAL"
    ichimoku_bullish_score: Optional[int] = None  # 0-5
    ichimoku_bearish_score: Optional[int] = None  # 0-5


class SignalData(BaseModel):
    """Signal-specific data (only for SIGNAL snapshots)"""
    tradable_flag: Optional[bool] = None
    signal_direction: Optional[str] = None  # "LONG", "SHORT"
    signal_score: Optional[float] = None
    signal_rank: Optional[int] = None
    confidence: Optional[float] = None  # 0-1 scale (legacy)
    confidence_pct: Optional[float] = Field(default=None, ge=0, le=100)  # 0-100 scale (explicit)
    signal_family: Optional[str] = None  # "ORB_BREAKOUT", "ORB_RETEST", "MEAN_REVERT", "TREND_CONTINUATION"
    rules_hit: List[str] = Field(default_factory=list)
    strategy_version: Optional[str] = None


class OutcomeDataUS(BaseModel):
    """Outcome-specific data for US markets (only for OUTCOME snapshots)"""
    price_preclose: Optional[float] = None
    mfe_from_orb: Optional[float] = None  # Maximum Favorable Excursion
    mae_from_orb: Optional[float] = None  # Maximum Adverse Excursion
    mfe_from_signal: Optional[float] = None
    mae_from_signal: Optional[float] = None
    # Enhanced MFE/MAE from Signal (Long & Short)
    mfe_long_from_signal_pct: Optional[float] = None
    mae_long_from_signal_pct: Optional[float] = None
    mfe_short_from_signal_pct: Optional[float] = None
    mae_short_from_signal_pct: Optional[float] = None
    # Signal anchor prices
    signal_price: Optional[float] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_mid: Optional[float] = None
    # Path window (Signal → Outcome)
    max_high_after_signal: Optional[float] = None
    min_low_after_signal: Optional[float] = None
    # Timing metrics
    minutes_to_mfe_long: Optional[int] = None
    minutes_to_mfe_short: Optional[int] = None
    minutes_to_break_orb_high_after_signal: Optional[int] = None
    minutes_to_break_orb_low_after_signal: Optional[int] = None
    # Break flags
    break_orb_high_within_5m: Optional[bool] = None
    break_orb_high_within_10m: Optional[bool] = None
    break_orb_high_within_15m: Optional[bool] = None
    break_orb_low_within_5m: Optional[bool] = None
    break_orb_low_within_10m: Optional[bool] = None
    break_orb_low_within_15m: Optional[bool] = None
    # Labels (existing)
    best_action_at_signal: Optional[str] = None  # "LONG", "SHORT", "NO_TRADE"
    best_entry_mode: Optional[str] = None  # "IMMEDIATE", "TRIGGER_ORB_HIGH", "TRIGGER_ORB_LOW", "WAIT"
    tradeability_label: Optional[str] = None  # "A_PLUS", "A", "B", "CHOP", "TRAP", "NO_EDGE" (was it worth trading?)
    # First major event after signal (critical for 0DTE decision rules)
    first_major_event: Optional[str] = None  # "BREAK_ORB_HIGH", "BREAK_ORB_LOW", "HIT_MFE_LONG", "HIT_MFE_SHORT", "NONE"
    minutes_to_first_major_event: Optional[int] = None
    net_move_pct: Optional[float] = None
    trend_persistence_score: Optional[float] = None
    delta_efficiency_proxy: Optional[float] = None
    
    # Outcome Label Layer fields (v1.0)
    # Opportunity scoring
    opportunity_score: Optional[float] = None  # Best direction opportunity score after costs
    opportunity_long: Optional[float] = None  # Long opportunity score
    opportunity_short: Optional[float] = None  # Short opportunity score
    
    # Peak structure metrics
    peak_mfe_pct: Optional[float] = None  # MFE for best direction (redundant with mfe_long/short but kept for convenience)
    peak_mae_pct: Optional[float] = None  # MAE for best direction
    t_to_mfe_minutes: Optional[int] = None  # Time to MFE (minutes from signal)
    peak_end_drawdown_pct: Optional[float] = None  # Drawdown from peak to horizon end
    pullback_count: Optional[int] = None  # Count of pullbacks exceeding threshold
    trend_persistence: Optional[float] = None  # Fraction of bars in direction (0-1)
    
    # Synthetic exit (baseline R-multiple policy)
    synthetic_r: Optional[float] = None  # Synthetic R-multiple (best direction)
    synthetic_r_long: Optional[float] = None  # Synthetic R-multiple (long direction)
    synthetic_r_short: Optional[float] = None  # Synthetic R-multiple (short direction)
    synthetic_exit_reason: Optional[str] = None  # "TP", "SL", "HORIZON_END", "INSUFFICIENT_DATA"
    synthetic_exit_time: Optional[datetime] = None  # Exit timestamp
    synthetic_exit_price: Optional[float] = None  # Exit price
    
    # Trade quality
    trade_quality_score: Optional[float] = None  # Quality score (0-1)
    trade_quality_label: Optional[str] = None  # "A", "B", "C", "D", "F"
    
    # Exit style
    exit_style: Optional[str] = None  # "SCALP_TP", "TRAIL_RUNNER", "CHOP_RISK", "STOP_FAST", "STANDARD", "INSUFFICIENT_DATA"
    
    # Label version for reproducibility
    outcome_label_version: Optional[str] = None  # "v1.0"
    
    # Linkage keys (for linking Outcome → Signal snapshots)
    signal_timestamp_utc: Optional[datetime] = None  # Signal snapshot timestamp (UTC)
    signal_run_id: Optional[str] = None  # Signal snapshot run_id (for linking)


class OutcomeDataCrypto(BaseModel):
    """Outcome-specific data for crypto markets (only for OUTCOME snapshots)"""
    price_next_session_open: Optional[float] = None
    move_to_next_open_pct: Optional[float] = None
    
    # Anchor prices
    session_open_price: Optional[float] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_mid: Optional[float] = None
    signal_price: Optional[float] = None
    
    # Path window (Signal -> next session open OR Signal -> outcome time)
    max_high_after_signal: Optional[float] = None
    min_low_after_signal: Optional[float] = None
    
    # MFE/MAE from Signal (Long & Short)
    mfe_long_from_signal_pct: Optional[float] = None
    mae_long_from_signal_pct: Optional[float] = None
    mfe_short_from_signal_pct: Optional[float] = None
    mae_short_from_signal_pct: Optional[float] = None
    
    # Timing (minutes from Signal)
    minutes_to_mfe_long: Optional[int] = None
    minutes_to_mfe_short: Optional[int] = None
    minutes_to_break_orb_high_after_signal: Optional[int] = None
    minutes_to_break_orb_low_after_signal: Optional[int] = None
    
    # Break flags
    break_orb_high_within_5m: Optional[bool] = None
    break_orb_high_within_10m: Optional[bool] = None
    break_orb_high_within_15m: Optional[bool] = None
    break_orb_low_within_5m: Optional[bool] = None
    break_orb_low_within_10m: Optional[bool] = None
    break_orb_low_within_15m: Optional[bool] = None
    
    # Labels (existing)
    best_action_at_signal: Optional[str] = None  # "LONG", "SHORT", "NO_TRADE"
    best_entry_mode: Optional[str] = None  # "IMMEDIATE", "TRIGGER_ORB_HIGH", "TRIGGER_ORB_LOW", "WAIT"
    tradeability_label: Optional[str] = None  # "A_PLUS", "A", "B", "CHOP", "TRAP", "NO_EDGE" (was it worth trading?)
    # First major event after signal (critical for 0DTE decision rules)
    first_major_event: Optional[str] = None  # "BREAK_ORB_HIGH", "BREAK_ORB_LOW", "HIT_MFE_LONG", "HIT_MFE_SHORT", "NONE"
    minutes_to_first_major_event: Optional[int] = None
    
    # Outcome Label Layer fields (v1.0)
    # Opportunity scoring
    opportunity_score: Optional[float] = None  # Best direction opportunity score after costs
    opportunity_long: Optional[float] = None  # Long opportunity score
    opportunity_short: Optional[float] = None  # Short opportunity score
    
    # Peak structure metrics
    peak_mfe_pct: Optional[float] = None  # MFE for best direction
    peak_mae_pct: Optional[float] = None  # MAE for best direction
    t_to_mfe_minutes: Optional[int] = None  # Time to MFE (minutes from signal)
    peak_end_drawdown_pct: Optional[float] = None  # Drawdown from peak to horizon end
    pullback_count: Optional[int] = None  # Count of pullbacks exceeding threshold
    trend_persistence: Optional[float] = None  # Fraction of bars in direction (0-1)
    
    # Synthetic exit (baseline R-multiple policy)
    synthetic_r: Optional[float] = None  # Synthetic R-multiple (best direction)
    synthetic_r_long: Optional[float] = None  # Synthetic R-multiple (long direction)
    synthetic_r_short: Optional[float] = None  # Synthetic R-multiple (short direction)
    synthetic_exit_reason: Optional[str] = None  # "TP", "SL", "HORIZON_END", "INSUFFICIENT_DATA"
    synthetic_exit_time: Optional[datetime] = None  # Exit timestamp
    synthetic_exit_price: Optional[float] = None  # Exit price
    
    # Trade quality
    trade_quality_score: Optional[float] = None  # Quality score (0-1)
    trade_quality_label: Optional[str] = None  # "A", "B", "C", "D", "F"
    
    # Exit style
    exit_style: Optional[str] = None  # "SCALP_TP", "TRAIL_RUNNER", "CHOP_RISK", "STOP_FAST", "STANDARD", "INSUFFICIENT_DATA"
    
    # Label version for reproducibility
    outcome_label_version: Optional[str] = None  # "v1.0"
    
    # Linkage keys (for linking Outcome → Signal snapshots)
    signal_timestamp_utc: Optional[datetime] = None  # Signal snapshot timestamp (UTC)
    signal_run_id: Optional[str] = None  # Signal snapshot run_id (for linking)
    
    # Existing (optional keep)
    session_followthrough_score: Optional[float] = None
    continuation_vs_reversal: Optional[str] = None  # "CONTINUATION", "REVERSAL", "NEUTRAL"


class Snapshot(BaseModel):
    """Master snapshot model - unified schema for all market snapshots"""
    
    # Identity & Context
    market: MarketType
    symbol: str
    session: Optional[CryptoSession] = None  # Crypto session (typed enum) or None for US
    snapshot_type: SnapshotType
    timestamp_et: datetime
    timestamp_utc: datetime
    day_of_week: int = Field(ge=0, le=6)  # 0=Monday, 6=Sunday
    minutes_since_open: Optional[int] = None
    minutes_to_next_session: Optional[int] = None
    
    # Session bounds (explicit anchors for ML + debugging)
    session_bounds: Optional[SessionBounds] = None
    
    # Core Data Blocks
    price_candle: PriceCandleData
    orb_block: ORBBlock = Field(default_factory=ORBBlock)
    trend_momentum: TrendMomentumData = Field(default_factory=TrendMomentumData)
    volatility: VolatilityData = Field(default_factory=VolatilityData)
    volume_vwap: VolumeVWAPData
    oscillators: OscillatorData = Field(default_factory=OscillatorData)
    macd: MACDData = Field(default_factory=MACDData)
    bollinger: BollingerData = Field(default_factory=BollingerData)
    ichimoku: IchimokuData = Field(default_factory=IchimokuData)
    
    # Snapshot-Type-Specific Data
    signal: Optional[SignalData] = None  # Only for SIGNAL snapshots
    outcome_us: Optional[OutcomeDataUS] = None  # Only for US OUTCOME snapshots
    outcome_crypto: Optional[OutcomeDataCrypto] = None  # Only for crypto OUTCOME snapshots
    
    # Calendar Tags
    calendar: CalendarTags = Field(default_factory=CalendarTags)
    
    # Metadata
    doc_id: Optional[str] = None  # Firestore document ID (idempotent)
    collection_timestamp: Optional[datetime] = None
    # Run identifiers for idempotency + debugging
    run_id: Optional[str] = None  # Same for all symbols in a collection run
    source: Optional[str] = None  # "ETRADE", "COINBASE"
    timeframe: Optional[str] = None  # "1m", "5m"
    schema_version: str = "1.0"  # Schema version for safe migration
    # Indicator readiness flags
    indicator_ready: Optional[bool] = None  # True if enough bars available for indicators
    indicator_bars_available: Optional[int] = None  # Number of bars available for indicators
    indicator_lookback_target: Optional[int] = None  # Target lookback (120 bars)
    ichimoku_ready: Optional[bool] = None  # True if enough bars for Ichimoku (52+ bars)
    # Degraded / fail-open: save even when indicators or labels incomplete
    feature_quality: Optional[str] = None  # "FULL" | "DEGRADED"
    skip_reason: Optional[str] = None  # "INSUFFICIENT_BARS" | "PROVIDER_PARTIAL" | ...
    label_ready: Optional[bool] = None  # True when outcome labels computed (OUTCOME only)
    # VWAP metadata
    vwap_mode: Optional[str] = None  # "SESSION" or "SLAB_FALLBACK"
    vwap_session_start_utc: Optional[datetime] = None  # Session start time for VWAP
    # Ichimoku metadata
    ichimoku_settings_used: Optional[Dict] = None  # Ichimoku period settings used
    ichimoku_timeframe: Optional[str] = None  # Timeframe used for Ichimoku ("5m")
    # Payload size (for monitoring)
    serialized_snapshot_bytes: Optional[int] = None  # Size of serialized snapshot in bytes
    
    class Config:
        use_enum_values = True
        
    def get_doc_id(self) -> str:
        """Generate idempotent document ID: {market}_{symbol}_{session}_{snapshot_type}_{YYYYMMDD}_{HHMM_ET}"""
        from app.utils.time_utils import format_datetime_for_doc_id
        
        # Handle enum or string (use_enum_values may convert to string)
        market_val = self.market.value if hasattr(self.market, 'value') else str(self.market)
        snapshot_type_val = self.snapshot_type.value if hasattr(self.snapshot_type, 'value') else str(self.snapshot_type)
        
        session_part = f"{self.session}_" if self.session else ""
        return f"{market_val}_{self.symbol}_{session_part}{snapshot_type_val}_{format_datetime_for_doc_id(self.timestamp_et)}"


class SnapshotCreateRequest(BaseModel):
    """Request model for creating a snapshot"""
    market: MarketType
    symbol: str
    snapshot_type: SnapshotType
    session: Optional[str] = None  # For crypto only
    timestamp_et: Optional[datetime] = None  # If None, uses current time


class SnapshotResponse(BaseModel):
    """Response model for snapshot creation"""
    success: bool
    doc_id: str
    symbol: str
    snapshot_type: SnapshotType
    timestamp_et: datetime
    message: Optional[str] = None


class CollectionSummary(BaseModel):
    """Summary of a collection run"""
    market: MarketType
    snapshot_type: SnapshotType
    session: Optional[str] = None
    total_snapshots: int
    successful: int
    failed: int
    errors: List[str] = Field(default_factory=list)
    duration_seconds: float
    timestamp: datetime
    # US provider router (when market=US)
    us_provider_used: Optional[str] = None
    us_provider_health: Optional[Dict] = None
    us_collection_status: Optional[str] = None  # "OK" | "SKIPPED_PROVIDER_DOWN"
