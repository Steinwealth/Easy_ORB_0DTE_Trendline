"""
Easy Collector - Indicator Service
Computes technical indicators from OHLCV data
Uses pandas/numpy for efficient calculations
"""

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from typing import Dict, Optional, List, Tuple
from app.models.snapshot_models import (
    TrendMomentumData, VolatilityData, VolumeVWAPData,
    OscillatorData, MACDData, BollingerData, IchimokuData
)

log = logging.getLogger(__name__)


class IndicatorService:
    """Service for computing technical indicators from OHLCV data"""
    
    @staticmethod
    def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
        """Calculate Exponential Moving Average"""
        return prices.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
        """Calculate Simple Moving Average"""
        return prices.rolling(window=period).mean()
    
    @staticmethod
    def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Relative Strength Index.
        Handles divide-by-zero and flat periods safely.
        """
        delta = prices.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        
        # Handle divide-by-zero: replace 0 with NaN, then fill with 50 (neutral)
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)  # Neutral for flat/no-data regions
    
    @staticmethod
    def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Calculate Average True Range"""
        high_low = high - low
        high_close = np.abs(high - close.shift())
        low_close = np.abs(low - close.shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=period).mean()
        return atr
    
    @staticmethod
    def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Calculate MACD (line, signal, histogram)"""
        ema_fast = IndicatorService.calculate_ema(prices, fast)
        ema_slow = IndicatorService.calculate_ema(prices, slow)
        macd_line = ema_fast - ema_slow
        macd_signal = IndicatorService.calculate_ema(macd_line, signal)
        macd_histogram = macd_line - macd_signal
        return macd_line, macd_signal, macd_histogram
    
    @staticmethod
    def calculate_bollinger_bands(prices: pd.Series, period: int = 20, std_dev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Calculate Bollinger Bands (upper, middle, lower)"""
        sma = prices.rolling(window=period, min_periods=period).mean()
        std = prices.rolling(window=period, min_periods=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        return upper, sma, lower
    
    @staticmethod
    def calculate_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """
        Calculate Volume Weighted Average Price (full history).
        Handles divide-by-zero when cumulative volume is 0.
        """
        typical_price = (high + low + close) / 3
        pv = (typical_price * volume).cumsum()
        vv = volume.cumsum().replace(0, np.nan)
        vwap = pv / vv
        return vwap.fillna(method="ffill")  # Forward fill for early periods with zero volume
    
    @staticmethod
    def calculate_session_vwap(
        df: pd.DataFrame,
        session_start_utc: datetime,
        timestamp_col: str = 'timestamp'
    ) -> Tuple[Optional[float], str]:
        """
        Calculate session VWAP (from session start to snapshot time).
        
        Args:
            df: DataFrame with OHLCV data
            session_start_utc: Session start time (UTC-aware)
            timestamp_col: Name of timestamp column
        
        Returns:
            Tuple of (vwap_value, vwap_mode)
            - vwap_value: VWAP price or None if insufficient data
            - vwap_mode: "SESSION" if computed from session start, "SLAB_FALLBACK" if using slab data
        """
        if df.empty or timestamp_col not in df.columns:
            return None, "INSUFFICIENT_DATA"
        
        # Filter to session start onwards
        session_df = df[df[timestamp_col] >= session_start_utc].copy()
        
        if session_df.empty or len(session_df) < 1:
            # Fallback: use slab data (full history)
            log.debug(f"    Session VWAP: No session data, using slab fallback")
            if len(df) >= 1:
                high = df['high']
                low = df['low']
                close = df['close']
                volume = df['volume']
                vwap = IndicatorService.calculate_vwap(high, low, close, volume)
                return float(vwap.iloc[-1]), "SLAB_FALLBACK"
            return None, "INSUFFICIENT_DATA"
        
        # Compute VWAP from session start
        high = session_df['high']
        low = session_df['low']
        close = session_df['close']
        volume = session_df['volume']
        
        typical_price = (high + low + close) / 3
        pv = (typical_price * volume).sum()
        vv = volume.sum()
        
        if vv > 0:
            vwap_value = pv / vv
            return float(vwap_value), "SESSION"
        else:
            return None, "INSUFFICIENT_DATA"
    
    @staticmethod
    def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
        """
        Calculate Stochastic Oscillator (%K, %D).
        Handles divide-by-zero when highest_high == lowest_low.
        """
        lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
        highest_high = high.rolling(window=k_period, min_periods=k_period).max()
        # Handle divide-by-zero: replace 0 with NaN, then fill with 50 (neutral)
        den = (highest_high - lowest_low).replace(0, np.nan)
        stoch_k = 100 * ((close - lowest_low) / den)
        stoch_k = stoch_k.fillna(50)  # Neutral when range is 0
        stoch_d = stoch_k.rolling(window=d_period, min_periods=d_period).mean()
        return stoch_k, stoch_d
    
    @staticmethod
    def calculate_williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Williams %R (Williams Percent Range).
        Formula: %R = (Highest High - Close) / (Highest High - Lowest Low) * -100
        Range: -100 (oversold) to 0 (overbought)
        Handles divide-by-zero when highest_high == lowest_low.
        """
        highest_high = high.rolling(window=period, min_periods=period).max()
        lowest_low = low.rolling(window=period, min_periods=period).min()
        # Handle divide-by-zero: replace 0 with NaN, then fill with -50 (neutral)
        den = (highest_high - lowest_low).replace(0, np.nan)
        williams_r = -100 * ((highest_high - close) / den)
        return williams_r.fillna(-50)  # Neutral when range is 0
    
    @staticmethod
    def calculate_cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate Commodity Channel Index (CCI).
        Formula: CCI = (Typical Price - SMA of Typical Price) / (0.015 * Mean Deviation)
        Typical Price = (High + Low + Close) / 3
        Handles divide-by-zero when mean deviation is 0.
        """
        typical_price = (high + low + close) / 3
        sma_tp = typical_price.rolling(window=period, min_periods=period).mean()
        mean_deviation = typical_price.rolling(window=period, min_periods=period).apply(
            lambda x: np.mean(np.abs(x - x.mean())), raw=False
        )
        # Handle divide-by-zero: replace 0 with NaN, then fill with 0 (neutral)
        den = (0.015 * mean_deviation).replace(0, np.nan)
        cci = (typical_price - sma_tp) / den
        return cci.fillna(0)  # Neutral when mean deviation is 0
    
    @staticmethod
    def calculate_mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Money Flow Index (MFI).
        MFI is a volume-weighted RSI.
        Handles edge cases: all positive flow → 100, all negative flow → 0, both zero → 50.
        """
        typical_price = (high + low + close) / 3
        raw_flow = typical_price * volume
        
        direction = typical_price.diff()
        pos = raw_flow.where(direction > 0, 0.0)
        neg = raw_flow.where(direction < 0, 0.0)
        
        pos_sum = pos.rolling(window=period, min_periods=period).sum()
        neg_sum = neg.rolling(window=period, min_periods=period).sum()
        
        # Ratio handling: replace 0 with NaN to avoid inf
        ratio = pos_sum / neg_sum.replace(0, np.nan)
        mfi = 100 - (100 / (1 + ratio))
        
        # Edge cases
        mfi = mfi.where(~((neg_sum == 0) & (pos_sum > 0)), 100)  # All positive → 100
        mfi = mfi.where(~((pos_sum == 0) & (neg_sum > 0)), 0)    # All negative → 0
        return mfi.fillna(50)  # Both zero → 50 (neutral)
    
    @staticmethod
    def calculate_cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate Chaikin Money Flow (CMF)
        Formula: CMF = Sum(Volume * Money Flow Multiplier) / Sum(Volume) over period
        Money Flow Multiplier = ((Close - Low) - (High - Close)) / (High - Low)
        Range: -1 to +1
        
        Safe handling: Replace zero ranges with NaN to avoid divide by zero
        """
        high_low_range = high - low
        # Replace zero ranges with NaN to avoid divide by zero
        high_low_range = high_low_range.replace(0, np.nan)
        money_flow_multiplier = ((close - low) - (high - close)) / high_low_range
        # Fill NaN with 0 (when range is 0, multiplier is 0)
        money_flow_multiplier = money_flow_multiplier.fillna(0)
        money_flow_volume = money_flow_multiplier * volume
        cmf = money_flow_volume.rolling(window=period).sum() / volume.rolling(window=period).sum()
        return cmf.fillna(0)  # Fill any remaining NaN with 0
    
    @staticmethod
    def _get_ichimoku_settings_for_symbol(symbol: Optional[str]) -> Dict[str, int]:
        """
        Get Ichimoku settings for a symbol based on 1H preset from Easy Ichimoku v14
        
        NOTE: Ichimoku cloud calculations are configurable and supplementary.
        Focus is on other datapoints in crypto datasets for analysis.
        
        Returns:
            Dict with conversion, base, lagging_span2, displacement
        """
        if not symbol:
            # Default BTC settings
            return {'conversion': 6, 'base': 8, 'lagging_span2': 8, 'displacement': 1}
        
        symbol_upper = symbol.upper()
        
        # 1H preset settings from Easy Ichimoku v14
        if 'BTC' in symbol_upper or 'BIP' in symbol_upper:
            # 1H BIP / BTC PERP: conversion=6, base=8, lagging_span2=8, displacement=1
            return {'conversion': 6, 'base': 8, 'lagging_span2': 8, 'displacement': 1}
        elif 'ETH' in symbol_upper or 'ETP' in symbol_upper:
            # 1H ETP / ETH PERP: conversion=2, base=5, lagging_span2=9, displacement=5
            return {'conversion': 2, 'base': 5, 'lagging_span2': 9, 'displacement': 5}
        elif 'SOL' in symbol_upper or 'SLP' in symbol_upper:
            # 1H SLP / SOL PERP: conversion=4, base=7, lagging_span2=6, displacement=2
            return {'conversion': 4, 'base': 7, 'lagging_span2': 6, 'displacement': 2}
        elif 'XRP' in symbol_upper or 'XPP' in symbol_upper:
            # 1H XPP / XRP PERP: conversion=5, base=10, lagging_span2=10, displacement=3
            return {'conversion': 5, 'base': 10, 'lagging_span2': 10, 'displacement': 3}
        else:
            # Default BTC settings
            return {'conversion': 6, 'base': 8, 'lagging_span2': 8, 'displacement': 1}
    
    def get_ichimoku_preset(self, symbol: Optional[str]) -> Dict[str, int]:
        """
        Get Ichimoku preset settings for a symbol (public wrapper for _get_ichimoku_settings_for_symbol).
        
        Args:
            symbol: Symbol name (e.g., 'BTC-PERP', 'ETH-PERP')
        
        Returns:
            Dict with conversion, base, lagging_span2, displacement
        """
        return self._get_ichimoku_settings_for_symbol(symbol)
    
    @staticmethod
    def calculate_ichimoku(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        conversion_periods: int = 9,
        base_periods: int = 26,
        lagging_span2_periods: int = 52,
        displacement: int = 26
    ) -> Dict[str, pd.Series]:
        """
        Calculate Ichimoku Cloud components
        
        Args:
            high: High prices
            low: Low prices
            close: Close prices
            conversion_periods: Tenkan-sen period (default: 9)
            base_periods: Kijun-sen period (default: 26)
            lagging_span2_periods: Senkou Span B period (default: 52)
            displacement: Displacement for Senkou spans and Chikou (default: 26)
        
        Returns:
            Dict with tenkan, kijun, senkou_a, senkou_b, chikou, cloud_top, cloud_bottom
        """
        # Tenkan-sen (Conversion Line): (highest high + lowest low) / 2 over conversion_periods
        tenkan_high = high.rolling(window=conversion_periods, min_periods=conversion_periods).max()
        tenkan_low = low.rolling(window=conversion_periods, min_periods=conversion_periods).min()
        tenkan = (tenkan_high + tenkan_low) / 2
        
        # Kijun-sen (Base Line): (highest high + lowest low) / 2 over base_periods
        kijun_high = high.rolling(window=base_periods, min_periods=base_periods).max()
        kijun_low = low.rolling(window=base_periods, min_periods=base_periods).min()
        kijun = (kijun_high + kijun_low) / 2
        
        # Senkou Span A (Leading Span A): (Tenkan + Kijun) / 2, shifted forward by displacement
        senkou_a = (tenkan + kijun) / 2
        senkou_a = senkou_a.shift(displacement)
        
        # Senkou Span B (Leading Span B): (highest high + lowest low) / 2 over lagging_span2_periods, shifted forward by displacement
        senkou_b_high = high.rolling(window=lagging_span2_periods, min_periods=lagging_span2_periods).max()
        senkou_b_low = low.rolling(window=lagging_span2_periods, min_periods=lagging_span2_periods).min()
        senkou_b = (senkou_b_high + senkou_b_low) / 2
        senkou_b = senkou_b.shift(displacement)
        
        # Chikou Span (Lagging Span): Close price, shifted back by displacement
        # NOTE: Using shift(displacement) instead of shift(-displacement) to avoid data leakage
        # Classic Ichimoku: Chikou shows past close aligned to current index (no future peeking)
        chikou = close.shift(displacement)
        
        # Cloud boundaries
        cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
        cloud_bottom = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
        
        return {
            'tenkan': tenkan,
            'kijun': kijun,
            'senkou_a': senkou_a,
            'senkou_b': senkou_b,
            'chikou': chikou,
            'cloud_top': cloud_top,
            'cloud_bottom': cloud_bottom
        }
    
    @staticmethod
    def calculate_indicators(
        ohlcv_df: pd.DataFrame, 
        symbol: Optional[str] = None,
        indicator_ready: Optional[bool] = None,
        bars_available: Optional[int] = None
    ) -> Dict:
        """
        Calculate all technical indicators from OHLCV DataFrame (indicator slab).
        
        Args:
            ohlcv_df: DataFrame with columns: open, high, low, close, volume (indicator slab)
            symbol: Optional symbol name for logging
            indicator_ready: Optional flag indicating if enough bars are available (auto-calculated if None)
            bars_available: Optional number of bars available (auto-calculated if None)
        
        Returns:
            Dict with all calculated indicators + quality flags (indicator_ready, indicator_bars_available)
        """
        symbol_str = f" for {symbol}" if symbol else ""
        log.info(f"  🔢 Calculating indicators{symbol_str}...")
        
        try:
            if ohlcv_df.empty or len(ohlcv_df) < 20:
                log.warning(f"  ⚠️ Insufficient data for indicator calculation{symbol_str}: {len(ohlcv_df)} bars (need >= 20)")
                return {}
            
            log.debug(f"    Input: {len(ohlcv_df)} bars, columns: {list(ohlcv_df.columns)}")
            
            # Copy DataFrame to avoid mutating input (prevents side effects with caching/reuse)
            df = ohlcv_df.copy(deep=False)
            
            # Validate required columns exist
            required_cols = ['open', 'high', 'low', 'close', 'volume']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                log.error(f"  ❌ Missing required columns{symbol_str}: {missing_cols}")
                return {}
            
            # Ensure columns are numeric and sanitize
            for col in required_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            # Crypto-safe: coerce volume NaN to 0.0 (fractional volume allowed)
            df['volume'] = df['volume'].fillna(0.0)

            # Drop rows with invalid OHLC data (keep volume optional)
            rows_before = len(df)
            df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
            rows_after = len(df)
            if rows_before != rows_after:
                log.debug(f"    Dropped {rows_before - rows_after} rows with invalid OHLC data")
            
            if df.empty:
                log.error(f"  ❌ No valid OHLC data after sanitization{symbol_str}")
                return {}
            
            close = df['close']
            high = df['high']
            low = df['low']
            volume = df['volume']
            
            # Get latest values (most recent bar)
            latest_idx = len(df) - 1
            log.debug(f"    Using latest bar index: {latest_idx} (total bars: {len(df)})")
            
            # Calculate quality flags
            if bars_available is None:
                bars_available = len(df)
            if indicator_ready is None:
                indicator_ready = bars_available >= 60  # Minimum for basic indicators
            
            indicators = {
                'indicator_ready': indicator_ready,
                'indicator_bars_available': bars_available
            }
            
            # Moving Averages - compute EMAs once for efficiency
            # REMOVED EMA 200: Requires too much data (200 bars = 16.7 hours for 5m timeframe)
            emas = {p: close.ewm(span=p, adjust=False).mean() for p in (8, 21, 50)}
            ema_8 = emas[8]
            ema_21 = emas[21]
            ema_50 = emas[50]
            
            indicators['ema_8'] = float(ema_8.iloc[latest_idx]) if latest_idx >= 7 else None
            indicators['ema_21'] = float(ema_21.iloc[latest_idx]) if latest_idx >= 20 else None
            indicators['ema_50'] = float(ema_50.iloc[latest_idx]) if latest_idx >= 49 else None
            indicators['ema_200'] = None  # Removed: requires too much historical data
            
            # EMA slopes (diff)
            indicators['ema_8_slope'] = float(ema_8.diff().iloc[latest_idx]) if latest_idx >= 8 else None
            indicators['ema_21_slope'] = float(ema_21.diff().iloc[latest_idx]) if latest_idx >= 21 else None
            
            # RSI
            rsi = IndicatorService.calculate_rsi(close, 14)
            indicators['rsi'] = float(rsi.iloc[latest_idx]) if latest_idx >= 13 else None
            rsi_diff = rsi.diff()
            indicators['rsi_slope'] = float(rsi_diff.iloc[latest_idx]) if latest_idx >= 14 else None
            # RSI acceleration (second diff)
            indicators['rsi_acceleration'] = float(rsi_diff.diff().iloc[latest_idx]) if latest_idx >= 15 else None
            
            # ATR
            atr = IndicatorService.calculate_atr(high, low, close, 14)
            indicators['atr'] = float(atr.iloc[latest_idx]) if latest_idx >= 13 else None
            atr_pct = atr / close * 100
            indicators['atr_pct'] = float(atr_pct.iloc[latest_idx]) if latest_idx >= 13 else None
            # ATR % change (diff of atr_pct)
            indicators['atr_pct_change'] = float(atr_pct.diff().iloc[latest_idx]) if latest_idx >= 14 else None
            
            # MACD
            macd_line, macd_signal, macd_histogram = IndicatorService.calculate_macd(close, 12, 26, 9)
            indicators['macd'] = float(macd_line.iloc[latest_idx]) if latest_idx >= 25 else None
            indicators['macd_signal'] = float(macd_signal.iloc[latest_idx]) if latest_idx >= 25 else None
            indicators['macd_histogram'] = float(macd_histogram.iloc[latest_idx]) if latest_idx >= 25 else None
            indicators['macd_slope'] = float(macd_line.diff().iloc[latest_idx]) if latest_idx >= 26 else None
            
            # Bollinger Bands
            bb_upper, bb_middle, bb_lower = IndicatorService.calculate_bollinger_bands(close, 20, 2)
            indicators['bollinger_upper'] = float(bb_upper.iloc[latest_idx]) if latest_idx >= 19 else None
            indicators['bollinger_middle'] = float(bb_middle.iloc[latest_idx]) if latest_idx >= 19 else None
            indicators['bollinger_lower'] = float(bb_lower.iloc[latest_idx]) if latest_idx >= 19 else None
            
            if latest_idx >= 19:
                bb_width = bb_upper.iloc[latest_idx] - bb_lower.iloc[latest_idx]
                indicators['bollinger_width'] = float(bb_width)
                indicators['bollinger_position'] = float(((close.iloc[latest_idx] - bb_lower.iloc[latest_idx]) / bb_width * 100)) if bb_width > 0 else 50.0
            
            # VWAP
            vwap = IndicatorService.calculate_vwap(high, low, close, volume)
            indicators['vwap'] = float(vwap.iloc[latest_idx])
            indicators['vwap_distance_pct'] = float(((close.iloc[latest_idx] - vwap.iloc[latest_idx]) / vwap.iloc[latest_idx] * 100)) if vwap.iloc[latest_idx] > 0 else None
            # VWAP momentum (diff)
            indicators['vwap_momentum'] = float(vwap.diff().iloc[latest_idx]) if latest_idx >= 1 else None
            # VWAP slope (diff)
            indicators['vwap_slope'] = float(vwap.diff().iloc[latest_idx]) if latest_idx >= 1 else None
            
            # Stochastic
            stoch_k, stoch_d = IndicatorService.calculate_stochastic(high, low, close, 14, 3)
            indicators['stoch_k'] = float(stoch_k.iloc[latest_idx]) if latest_idx >= 13 else None
            indicators['stoch_d'] = float(stoch_d.iloc[latest_idx]) if latest_idx >= 15 else None
            
            # Williams %R
            williams_r = IndicatorService.calculate_williams_r(high, low, close, 14)
            indicators['williams_r'] = float(williams_r.iloc[latest_idx]) if latest_idx >= 13 else None
            
            # CCI (Commodity Channel Index)
            cci = IndicatorService.calculate_cci(high, low, close, 20)
            indicators['cci'] = float(cci.iloc[latest_idx]) if latest_idx >= 19 else None
            
            # MFI (Money Flow Index)
            mfi = IndicatorService.calculate_mfi(high, low, close, volume, 14)
            indicators['mfi'] = float(mfi.iloc[latest_idx]) if latest_idx >= 13 else None
            
            # CMF (Chaikin Money Flow)
            cmf = IndicatorService.calculate_cmf(high, low, close, volume, 20)
            indicators['cmf'] = float(cmf.iloc[latest_idx]) if latest_idx >= 19 else None
            
            # Volume indicators (float for crypto-safe; volume_delta can be fractional)
            volume_sma = volume.rolling(window=20, min_periods=20).mean()
            indicators['volume_sma'] = float(volume_sma.iloc[latest_idx]) if latest_idx >= 19 else None
            indicators['volume_ratio'] = float(volume.iloc[latest_idx] / volume_sma.iloc[latest_idx]) if latest_idx >= 19 and volume_sma.iloc[latest_idx] > 0 else None
            # Volume delta and acceleration (float for crypto)
            volume_diff = volume.diff()
            indicators['volume_delta'] = float(volume_diff.iloc[latest_idx]) if latest_idx >= 1 else None
            indicators['volume_acceleration'] = float(volume_diff.diff().iloc[latest_idx]) if latest_idx >= 2 else None
            
            # Momentum
            if latest_idx >= 10:
                indicators['momentum_10bar'] = float(((close.iloc[latest_idx] - close.iloc[latest_idx - 10]) / close.iloc[latest_idx - 10] * 100))
            if latest_idx >= 5:
                indicators['momentum_5bar'] = float(((close.iloc[latest_idx] - close.iloc[latest_idx - 5]) / close.iloc[latest_idx - 5] * 100))
            if latest_idx >= 1:
                indicators['momentum_1bar'] = float(((close.iloc[latest_idx] - close.iloc[latest_idx - 1]) / close.iloc[latest_idx - 1] * 100))
            
            # ROC (Rate of Change) - 10-bar, 5-bar, 1-bar
            if latest_idx >= 10:
                roc_10 = ((close.iloc[latest_idx] - close.iloc[latest_idx - 10]) / close.iloc[latest_idx - 10] * 100)
                indicators['roc_10'] = float(roc_10)
                indicators['roc'] = float(roc_10)  # Backward compatibility
            if latest_idx >= 5:
                indicators['roc_5'] = float(((close.iloc[latest_idx] - close.iloc[latest_idx - 5]) / close.iloc[latest_idx - 5] * 100))
            if latest_idx >= 1:
                indicators['roc_1'] = float(((close.iloc[latest_idx] - close.iloc[latest_idx - 1]) / close.iloc[latest_idx - 1] * 100))
            
            # Volatility (annualized)
            returns = close.pct_change()
            if len(returns) > 20:
                volatility = returns.rolling(window=20).std() * np.sqrt(252) * 100  # Annualized %
                indicators['volatility'] = float(volatility.iloc[latest_idx])
            
            # Rolling range context features (15, 30, 60 bars)
            # These help understand position in recent range context
            for n in [15, 30, 60]:
                if latest_idx >= n - 1:
                    rolling_window = df.iloc[latest_idx - n + 1:latest_idx + 1]
                    rolling_high = float(rolling_window['high'].max())
                    rolling_low = float(rolling_window['low'].min())
                    rolling_range = rolling_high - rolling_low if rolling_high > rolling_low else 0
                    current_close = close.iloc[latest_idx]
                    
                    indicators[f'rolling_high_{n}'] = rolling_high
                    indicators[f'rolling_low_{n}'] = rolling_low
                    indicators[f'rolling_range_pct_{n}'] = float((rolling_range / current_close * 100)) if current_close > 0 else 0
                    
                    # Position in rolling range (0-100%)
                    if rolling_range > 0:
                        pos_in_rolling_range = ((current_close - rolling_low) / rolling_range * 100)
                        indicators[f'pos_in_rolling_range_{n}'] = float(pos_in_rolling_range)
                    else:
                        indicators[f'pos_in_rolling_range_{n}'] = 50.0  # Neutral if no range
            
            # Ichimoku Cloud (for crypto, optional for US)
            # NOTE: Ichimoku settings are configurable per symbol
            # Using 1H preset settings from Easy Ichimoku v14 strategy
            # Focus on other datapoints in crypto datasets; Ichimoku is supplementary
            ichimoku_settings = IndicatorService._get_ichimoku_settings_for_symbol(symbol)
            log.debug(f"Ichimoku settings for {symbol}: {ichimoku_settings}")
            ichimoku = IndicatorService.calculate_ichimoku(
                high, low, close,
                conversion_periods=ichimoku_settings['conversion'],
                base_periods=ichimoku_settings['base'],
                lagging_span2_periods=ichimoku_settings['lagging_span2'],
                displacement=ichimoku_settings['displacement']
            )
            
            # Get current values (accounting for displacement)
            if latest_idx >= 2:  # Need at least 3 bars for displacement
                indicators['ichimoku_tenkan'] = float(ichimoku['tenkan'].iloc[latest_idx]) if not pd.isna(ichimoku['tenkan'].iloc[latest_idx]) else None
                indicators['ichimoku_kijun'] = float(ichimoku['kijun'].iloc[latest_idx]) if not pd.isna(ichimoku['kijun'].iloc[latest_idx]) else None
                indicators['ichimoku_senkou_a'] = float(ichimoku['senkou_a'].iloc[latest_idx]) if not pd.isna(ichimoku['senkou_a'].iloc[latest_idx]) else None
                indicators['ichimoku_senkou_b'] = float(ichimoku['senkou_b'].iloc[latest_idx]) if not pd.isna(ichimoku['senkou_b'].iloc[latest_idx]) else None
                indicators['ichimoku_chikou'] = float(ichimoku['chikou'].iloc[latest_idx]) if not pd.isna(ichimoku['chikou'].iloc[latest_idx]) else None
                indicators['ichimoku_cloud_top'] = float(ichimoku['cloud_top'].iloc[latest_idx]) if not pd.isna(ichimoku['cloud_top'].iloc[latest_idx]) else None
                indicators['ichimoku_cloud_bottom'] = float(ichimoku['cloud_bottom'].iloc[latest_idx]) if not pd.isna(ichimoku['cloud_bottom'].iloc[latest_idx]) else None
                
                # Calculate cloud thickness and position
                if indicators['ichimoku_cloud_top'] and indicators['ichimoku_cloud_bottom']:
                    cloud_thickness = indicators['ichimoku_cloud_top'] - indicators['ichimoku_cloud_bottom']
                    indicators['ichimoku_cloud_thickness_pct'] = float((cloud_thickness / close.iloc[latest_idx] * 100)) if close.iloc[latest_idx] > 0 else None
                    
                    # Price vs cloud state
                    current_price = close.iloc[latest_idx]
                    if current_price > indicators['ichimoku_cloud_top']:
                        indicators['ichimoku_price_vs_cloud'] = "ABOVE"
                    elif current_price < indicators['ichimoku_cloud_bottom']:
                        indicators['ichimoku_price_vs_cloud'] = "BELOW"
                    else:
                        indicators['ichimoku_price_vs_cloud'] = "IN_CLOUD"
                    
                    # Cloud bias (future cloud)
                    if indicators['ichimoku_senkou_a'] and indicators['ichimoku_senkou_b']:
                        indicators['ichimoku_cloud_bullish'] = indicators['ichimoku_senkou_a'] > indicators['ichimoku_senkou_b']
                        indicators['ichimoku_cloud_bearish'] = indicators['ichimoku_senkou_a'] < indicators['ichimoku_senkou_b']
                
                # TK cross state
                if indicators['ichimoku_tenkan'] and indicators['ichimoku_kijun']:
                    if indicators['ichimoku_tenkan'] > indicators['ichimoku_kijun']:
                        indicators['ichimoku_tk_cross'] = "BULLISH"
                    elif indicators['ichimoku_tenkan'] < indicators['ichimoku_kijun']:
                        indicators['ichimoku_tk_cross'] = "BEARISH"
                    else:
                        indicators['ichimoku_tk_cross'] = "NEUTRAL"
            
            indicators_count = len([k for k, v in indicators.items() if v is not None])
            indicators_total = len(indicators)
            log.info(f"  ✅ Calculated {indicators_count}/{indicators_total} indicators{symbol_str}")
            if indicators_count < indicators_total:
                missing = [k for k, v in indicators.items() if v is None]
                log.debug(f"    Missing indicators ({len(missing)}): {missing[:10]}{'...' if len(missing) > 10 else ''}")
            return indicators
            
        except Exception as e:
            symbol_str = f" for {symbol}" if symbol else ""
            log.error(f"  ❌ Failed to calculate indicators{symbol_str}: {e}", exc_info=True)
            return {}
