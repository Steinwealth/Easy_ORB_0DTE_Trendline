"""
Easy Collector - Outcome Label Service
Computes supervised learning labels for trade opportunities from post-signal price paths.
Uses edge-based formulas: edge = MFE - k*MAE - cost_penalty
Produces: best_action, opportunity_score, peak_metrics, synthetic_exit, trade_quality, exit_style
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from app.config import get_settings

log = logging.getLogger(__name__)


class OutcomeLabelService:
    """Service for computing outcome labels from post-signal price paths using edge-based formulas"""
    
    def __init__(self):
        """Initialize outcome label service with settings"""
        self.settings = get_settings()
        
        # Edge formula parameters
        self.k = getattr(self.settings, 'edge_risk_weight', 0.5)  # Risk penalty coefficient (MAE weight)
        
        # Cost penalties (bid-ask spread + commissions + slippage)
        # US 0DTE: Even though labels are on underlyings, options execution is expensive
        self.cost_penalty_us = getattr(self.settings, 'cost_penalty_us', 0.15)  # 0.10-0.25% recommended
        # Crypto perp: Include taker + slippage
        self.cost_penalty_crypto = getattr(self.settings, 'cost_penalty_crypto', 0.08)  # 0.04-0.12% recommended
        
        # Minimum edge to trade (no-trade threshold)
        self.min_edge_to_trade_us = getattr(self.settings, 'min_edge_to_trade_us', 0.45)  # 0.35-0.60% recommended
        self.min_edge_to_trade_crypto = getattr(self.settings, 'min_edge_to_trade_crypto', 0.30)  # 0.20-0.40% recommended
        
        # Opportunity normalization scales (for optional 0-1 normalization)
        self.opportunity_norm_scale_us = getattr(self.settings, 'opportunity_norm_scale_us', 2.5)  # 2.0-3.0 recommended
        self.opportunity_norm_scale_crypto = getattr(self.settings, 'opportunity_norm_scale_crypto', 4.5)  # 3.0-6.0 recommended
        
        # Trade quality parameters
        self.edge_norm_us = getattr(self.settings, 'edge_norm_us', 2.0)  # Normalization for US edge component
        self.edge_norm_crypto = getattr(self.settings, 'edge_norm_crypto', 4.0)  # Normalization for crypto edge component
        self.r_norm = getattr(self.settings, 'r_norm', 3.0)  # Synthetic R normalization (3.0 = excellent)
        self.mae_floor_pct = getattr(self.settings, 'mae_floor_pct', 0.05)  # Floor for MAE to prevent division blowups
        self.w_edge = getattr(self.settings, 'quality_weight_edge', 0.7)  # Weight for edge component
        self.w_r = getattr(self.settings, 'quality_weight_r', 0.3)  # Weight for R component
        
        # Outcome horizon (crypto fixed horizon in hours)
        self.outcome_horizon_crypto_hours = getattr(self.settings, 'outcome_horizon_crypto_hours', 6)
        
        # Pullback thresholds (ATR-based or fixed %)
        self.pullback_threshold_us_pct = getattr(self.settings, 'pullback_threshold_us_pct', 0.25)
        self.pullback_threshold_crypto_pct = getattr(self.settings, 'pullback_threshold_crypto_pct', 0.50)
        
        # Minimum bars for valid labels
        self.min_bars_for_labels = getattr(self.settings, 'min_bars_for_labels', 3)
        
        # Label version for reproducibility
        self.label_version = "v2.0"  # Updated to v2.0 for edge-based formulas
    
    def compute_outcome_labels(
        self,
        ohlcv_df: pd.DataFrame,
        signal_time_utc: datetime,
        outcome_time_utc: datetime,
        signal_price: float,
        atr: Optional[float] = None,
        atr_pct: Optional[float] = None,
        market: str = "US",
        indicator_ready: bool = True
    ) -> Dict:
        """
        Compute outcome labels from post-signal price path using edge-based formulas.
        
        Edge formula: edge = MFE - k*MAE - cost_penalty
        
        Args:
            ohlcv_df: DataFrame with post-signal bars (must include signal_time_utc bar)
            signal_time_utc: Signal timestamp (UTC)
            outcome_time_utc: Outcome horizon end timestamp (UTC)
            signal_price: Entry price at signal time
            atr: ATR value at signal time (for synthetic exit)
            atr_pct: ATR percentage at signal time (for pullback threshold)
            market: "US" or "CRYPTO"
            indicator_ready: Whether indicators were ready at signal time
        
        Returns:
            Dict with outcome labels:
            - mfe_long_pct, mae_long_pct, mfe_short_pct, mae_short_pct
            - edge_long, edge_short, best_edge
            - best_action_at_signal: "LONG", "SHORT", "NO_TRADE"
            - opportunity_score: float (raw %)
            - opportunity_long, opportunity_short: float (raw %)
            - peak_metrics: dict
            - synthetic_exit: dict (synthetic_r_long, synthetic_r_short, synthetic_r_best)
            - trade_quality: dict (quality_score 0-1, quality_grade A/B/C/D)
            - exit_style: str
            - label_params: dict (k, cost_penalty_pct, min_edge_to_trade_pct)
            - label_ready: bool (False if insufficient data)
            - feature_quality: str ("GOOD" or "DEGRADED")
            - label_version: str
        """
        # Guardrails: Check for insufficient data
        if ohlcv_df.empty or signal_price <= 0:
            return self._empty_labels(market, indicator_ready)
        
        # Filter to post-signal window (signal_time_utc to outcome_time_utc)
        post_signal_df = ohlcv_df[
            (ohlcv_df['timestamp'] >= signal_time_utc) & 
            (ohlcv_df['timestamp'] < outcome_time_utc)
        ].copy()
        
        if post_signal_df.empty or len(post_signal_df) < self.min_bars_for_labels:
            log.warning(f"Insufficient post-signal bars: {len(post_signal_df)} < {self.min_bars_for_labels}")
            return self._empty_labels(market, indicator_ready, label_ready=False)
        
        # Get market-specific parameters
        cost_penalty = self.cost_penalty_us if market == "US" else self.cost_penalty_crypto
        min_edge_to_trade = self.min_edge_to_trade_us if market == "US" else self.min_edge_to_trade_crypto
        
        # Compute opportunity (path extrema)
        opportunity = self._compute_opportunity(post_signal_df, signal_price)
        
        # Compute edges using edge formula: edge = MFE - k*MAE - cost_penalty
        edge_long = opportunity['long_mfe_pct'] - self.k * opportunity['long_mae_pct'] - cost_penalty
        edge_short = opportunity['short_mfe_pct'] - self.k * opportunity['short_mae_pct'] - cost_penalty
        best_edge = max(edge_long, edge_short)
        
        # Guardrail: If both edges negative, force NO_TRADE
        if edge_long < 0 and edge_short < 0:
            best_action = "NO_TRADE"
        else:
            # Determine best action with no-trade threshold
            if best_edge < min_edge_to_trade:
                best_action = "NO_TRADE"
            else:
                best_action = "LONG" if edge_long >= edge_short else "SHORT"
        
        # Compute opportunity scores (positive edge only)
        opportunity_long = max(0.0, edge_long)
        opportunity_short = max(0.0, edge_short)
        opportunity_score = max(0.0, best_edge)
        
        # Compute peak structure metrics (for best direction)
        peak_metrics = self._compute_peak_metrics(
            post_signal_df, 
            signal_price, 
            opportunity,
            atr_pct,
            market,
            best_action
        )
        
        # Compute synthetic exits for both directions
        synthetic_exit_long = self._compute_synthetic_exit(
            post_signal_df,
            signal_price,
            atr,
            outcome_time_utc,
            "LONG"
        )
        synthetic_exit_short = self._compute_synthetic_exit(
            post_signal_df,
            signal_price,
            atr,
            outcome_time_utc,
            "SHORT"
        )
        
        # Get synthetic R for best direction
        if best_action == "LONG":
            synthetic_exit_best = synthetic_exit_long
        elif best_action == "SHORT":
            synthetic_exit_best = synthetic_exit_short
        else:
            synthetic_exit_best = {'synthetic_r': 0.0, 'exit_reason': "NO_TRADE", 'exit_time': None, 'exit_price': None}
        
        # Compute trade quality (using stable normalization)
        trade_quality = self._compute_trade_quality(
            best_edge,
            opportunity['best_mfe_pct'],
            opportunity['best_mae_pct'],
            synthetic_exit_best.get('synthetic_r', 0.0),
            market
        )
        
        # Determine exit style (enhanced with MEAN_REVERT and TREND_CONTINUATION)
        exit_style = self._determine_exit_style(
            peak_metrics,
            synthetic_exit_best,
            best_action,
            post_signal_df,
            signal_price
        )
        
        # Feature quality flag
        feature_quality = "GOOD" if indicator_ready else "DEGRADED"
        
        # Store parameters used (for auditing)
        label_params = {
            'k': self.k,
            'cost_penalty_pct': cost_penalty,
            'min_edge_to_trade_pct': min_edge_to_trade,
            'edge_norm': self.edge_norm_us if market == "US" else self.edge_norm_crypto,
            'r_norm': self.r_norm,
            'w_edge': self.w_edge,
            'w_r': self.w_r
        }
        
        return {
            # Core MFE/MAE metrics
            'mfe_long_pct': opportunity['long_mfe_pct'],
            'mae_long_pct': opportunity['long_mae_pct'],
            'mfe_short_pct': opportunity['short_mfe_pct'],
            'mae_short_pct': opportunity['short_mae_pct'],
            
            # Edge metrics
            'edge_long': edge_long,
            'edge_short': edge_short,
            'best_edge': best_edge,
            
            # Best action
            'best_action_at_signal': best_action,
            
            # Opportunity scores (raw %)
            'opportunity_score': opportunity_score,
            'opportunity_long': opportunity_long,
            'opportunity_short': opportunity_short,
            
            # Peak metrics
            'peak_metrics': peak_metrics,
            
            # Synthetic exits (both directions + best)
            'synthetic_exit': {
                'synthetic_r_long': synthetic_exit_long.get('synthetic_r', 0.0),
                'synthetic_r_short': synthetic_exit_short.get('synthetic_r', 0.0),
                'synthetic_r_best': synthetic_exit_best.get('synthetic_r', 0.0),
                'exit_reason': synthetic_exit_best.get('exit_reason', "HORIZON_END"),
                'exit_time': synthetic_exit_best.get('exit_time'),
                'exit_price': synthetic_exit_best.get('exit_price')
            },
            
            # Trade quality
            'trade_quality': trade_quality,
            
            # Exit style
            'exit_style': exit_style,
            
            # Metadata
            'label_params': label_params,
            'label_ready': True,
            'feature_quality': feature_quality,
            'label_version': self.label_version
        }
    
    def _compute_opportunity(
        self,
        post_signal_df: pd.DataFrame,
        signal_price: float
    ) -> Dict:
        """Compute opportunity metrics from price path extrema"""
        max_high = float(post_signal_df['high'].max())
        min_low = float(post_signal_df['low'].min())
        
        # Long opportunity
        long_mfe_pct = ((max_high - signal_price) / signal_price * 100) if signal_price > 0 else 0.0
        long_mae_pct = ((signal_price - min_low) / signal_price * 100) if signal_price > 0 else 0.0
        
        # Short opportunity
        short_mfe_pct = ((signal_price - min_low) / signal_price * 100) if signal_price > 0 else 0.0
        short_mae_pct = ((max_high - signal_price) / signal_price * 100) if signal_price > 0 else 0.0
        
        # Determine best direction based on MFE
        if long_mfe_pct > short_mfe_pct:
            best_direction = "LONG"
            best_mfe_pct = long_mfe_pct
            best_mae_pct = long_mae_pct
        else:
            best_direction = "SHORT"
            best_mfe_pct = short_mfe_pct
            best_mae_pct = short_mae_pct
        
        return {
            'long_mfe_pct': long_mfe_pct,
            'long_mae_pct': long_mae_pct,
            'short_mfe_pct': short_mfe_pct,
            'short_mae_pct': short_mae_pct,
            'best_direction': best_direction,
            'best_mfe_pct': best_mfe_pct,
            'best_mae_pct': best_mae_pct
        }
    
    def _compute_peak_metrics(
        self,
        post_signal_df: pd.DataFrame,
        signal_price: float,
        opportunity: Dict,
        atr_pct: Optional[float],
        market: str,
        best_action: str
    ) -> Dict:
        """Compute peak structure metrics for best direction"""
        if best_action == "NO_TRADE":
            # Use best_direction from opportunity
            best_direction = opportunity['best_direction']
        else:
            best_direction = best_action
        
        best_mfe_pct = opportunity['best_mfe_pct']
        best_mae_pct = opportunity['best_mae_pct']
        
        # Find time to MFE
        if best_direction == "LONG":
            max_high = float(post_signal_df['high'].max())
            mfe_rows = post_signal_df[post_signal_df['high'] == max_high]
            if not mfe_rows.empty:
                mfe_idx = mfe_rows.index[0]
                mfe_time = post_signal_df.loc[mfe_idx, 'timestamp']
                signal_time = post_signal_df.iloc[0]['timestamp']
                t_to_mfe_minutes = int((mfe_time - signal_time).total_seconds() / 60) if isinstance(mfe_time, datetime) else None
            else:
                t_to_mfe_minutes = None
        else:  # SHORT
            min_low = float(post_signal_df['low'].min())
            mfe_rows = post_signal_df[post_signal_df['low'] == min_low]
            if not mfe_rows.empty:
                mfe_idx = mfe_rows.index[0]
                mfe_time = post_signal_df.loc[mfe_idx, 'timestamp']
                signal_time = post_signal_df.iloc[0]['timestamp']
                t_to_mfe_minutes = int((mfe_time - signal_time).total_seconds() / 60) if isinstance(mfe_time, datetime) else None
            else:
                t_to_mfe_minutes = None
        
        # Peak end drawdown (peak price → last close)
        last_close = float(post_signal_df.iloc[-1]['close'])
        if best_direction == "LONG":
            peak_price = float(post_signal_df['high'].max())
            peak_end_drawdown_pct = ((peak_price - last_close) / peak_price * 100) if peak_price > 0 else 0.0
        else:  # SHORT
            peak_price = float(post_signal_df['low'].min())
            peak_end_drawdown_pct = ((last_close - peak_price) / peak_price * 100) if peak_price > 0 else 0.0
        
        # Pullback count (using ATR-based or fixed threshold)
        pullback_threshold_pct = atr_pct if atr_pct and atr_pct > 0 else (
            self.pullback_threshold_us_pct if market == "US" else self.pullback_threshold_crypto_pct
        )
        
        pullback_count = self._count_pullbacks(
            post_signal_df,
            best_direction,
            signal_price,
            pullback_threshold_pct
        )
        
        # Trend persistence (fraction of bars in direction)
        trend_persistence = self._compute_trend_persistence(
            post_signal_df,
            best_direction
        )
        
        return {
            'mfe_pct': best_mfe_pct,
            'mae_pct': best_mae_pct,
            't_to_mfe_minutes': t_to_mfe_minutes,
            'peak_end_drawdown_pct': peak_end_drawdown_pct,
            'pullback_count': pullback_count,
            'trend_persistence': trend_persistence
        }
    
    def _count_pullbacks(
        self,
        df: pd.DataFrame,
        direction: str,
        entry_price: float,
        threshold_pct: float
    ) -> int:
        """Count pullbacks exceeding threshold"""
        if df.empty:
            return 0
        
        pullback_count = 0
        if direction == "LONG":
            # Count bars where low drops below previous high by threshold
            highs = df['high'].values
            lows = df['low'].values
            for i in range(1, len(df)):
                if highs[i-1] > 0:
                    pullback_pct = ((highs[i-1] - lows[i]) / highs[i-1] * 100)
                    if pullback_pct >= threshold_pct:
                        pullback_count += 1
        else:  # SHORT
            # Count bars where high rises above previous low by threshold
            highs = df['high'].values
            lows = df['low'].values
            for i in range(1, len(df)):
                if lows[i-1] > 0:
                    pullback_pct = ((highs[i] - lows[i-1]) / lows[i-1] * 100)
                    if pullback_pct >= threshold_pct:
                        pullback_count += 1
        
        return pullback_count
    
    def _compute_trend_persistence(
        self,
        df: pd.DataFrame,
        direction: str
    ) -> float:
        """Compute trend persistence (fraction of bars in direction)"""
        if len(df) < 2:
            return 0.5
        
        closes = df['close'].values
        in_direction = 0
        
        if direction == "LONG":
            for i in range(1, len(closes)):
                if closes[i] >= closes[i-1]:
                    in_direction += 1
        else:  # SHORT
            for i in range(1, len(closes)):
                if closes[i] <= closes[i-1]:
                    in_direction += 1
        
        return in_direction / (len(closes) - 1) if len(closes) > 1 else 0.5
    
    def _compute_synthetic_exit(
        self,
        post_signal_df: pd.DataFrame,
        signal_price: float,
        atr: Optional[float],
        outcome_time_utc: datetime,
        direction: str
    ) -> Dict:
        """
        Compute synthetic exit using baseline R-multiple policy.
        SL = 1.0 * ATR, TP = 1.5 * ATR
        """
        # Fallback to fixed % if ATR not available
        if atr and atr > 0:
            sl_distance = atr * 1.0
            tp_distance = atr * 1.5
        else:
            # Fixed % fallback (0.5% SL, 0.75% TP for US; 1% SL, 1.5% TP for crypto)
            sl_distance = signal_price * 0.005  # 0.5%
            tp_distance = signal_price * 0.0075  # 0.75%
        
        if direction == "LONG":
            stop_price = signal_price - sl_distance
            tp_price = signal_price + tp_distance
        else:  # SHORT
            stop_price = signal_price + sl_distance
            tp_price = signal_price - tp_distance
        
        # Walk forward bar-by-bar
        exit_reason = "HORIZON_END"
        exit_time = outcome_time_utc
        exit_price = float(post_signal_df.iloc[-1]['close'])
        synthetic_r = None
        
        for idx, row in post_signal_df.iterrows():
            bar_high = float(row['high'])
            bar_low = float(row['low'])
            bar_time = row['timestamp']
            
            if direction == "LONG":
                # Check TP first (more favorable)
                if bar_high >= tp_price:
                    exit_reason = "TP"
                    exit_time = bar_time
                    exit_price = tp_price
                    synthetic_r = 1.5
                    break
                # Then check SL
                elif bar_low <= stop_price:
                    exit_reason = "SL"
                    exit_time = bar_time
                    exit_price = stop_price
                    synthetic_r = -1.0
                    break
            else:  # SHORT
                # Check TP first
                if bar_low <= tp_price:
                    exit_reason = "TP"
                    exit_time = bar_time
                    exit_price = tp_price
                    synthetic_r = 1.5
                    break
                # Then check SL
                elif bar_high >= stop_price:
                    exit_reason = "SL"
                    exit_time = bar_time
                    exit_price = stop_price
                    synthetic_r = -1.0
                    break
        
        # If horizon end reached, compute R-multiple
        if exit_reason == "HORIZON_END":
            if direction == "LONG":
                synthetic_r = (exit_price - signal_price) / sl_distance
            else:  # SHORT
                synthetic_r = (signal_price - exit_price) / sl_distance
        
        return {
            'synthetic_r': float(synthetic_r) if synthetic_r is not None else 0.0,
            'exit_reason': exit_reason,
            'exit_time': exit_time,
            'exit_price': exit_price
        }
    
    def _compute_trade_quality(
        self,
        best_edge: float,
        mfe_pct: float,
        mae_pct: float,
        synthetic_r: float,
        market: str
    ) -> Dict:
        """
        Compute trade quality using stable normalization.
        Quality = w_edge * (edge_component / edge_norm) + w_r * (r_component / r_norm)
        """
        # Edge component (already cost-adjusted)
        edge_component = max(0.0, best_edge)
        edge_norm = self.edge_norm_us if market == "US" else self.edge_norm_crypto
        edge_normalized = np.clip(edge_component / edge_norm, 0.0, 1.0)
        
        # R component (synthetic R ratio, stable)
        # synthetic_r = mfe_pct / max(mae_pct, mae_floor_pct)
        mae_safe = max(mae_pct, self.mae_floor_pct)
        synthetic_r_ratio = mfe_pct / mae_safe if mae_safe > 0 else 0.0
        r_component = np.clip(synthetic_r_ratio / self.r_norm, 0.0, 1.0)
        
        # Combined quality
        quality_raw = self.w_edge * edge_normalized + self.w_r * r_component
        trade_quality = np.clip(quality_raw, 0.0, 1.0)
        
        # Map to letter grade
        if trade_quality >= 0.80:
            quality_grade = "A"
        elif trade_quality >= 0.65:
            quality_grade = "B"
        elif trade_quality >= 0.50:
            quality_grade = "C"
        else:
            quality_grade = "D"
        
        return {
            'quality_score': float(trade_quality),
            'quality_grade': quality_grade
        }
    
    def _determine_exit_style(
        self,
        peak_metrics: Dict,
        synthetic_exit: Dict,
        best_action: str,
        post_signal_df: pd.DataFrame,
        signal_price: float
    ) -> str:
        """Determine exit style category (enhanced with MEAN_REVERT and TREND_CONTINUATION)"""
        if best_action == "NO_TRADE":
            return "NO_TRADE"
        
        t_to_mfe = peak_metrics.get('t_to_mfe_minutes', None)
        peak_end_drawdown_pct = peak_metrics.get('peak_end_drawdown_pct', 0.0)
        mfe_pct = peak_metrics.get('mfe_pct', 0.0)
        trend_persistence = peak_metrics.get('trend_persistence', 0.5)
        pullback_count = peak_metrics.get('pullback_count', 0)
        synthetic_r = synthetic_exit.get('synthetic_r', 0.0)
        mae_pct = peak_metrics.get('mae_pct', 0.0)
        
        # SCALP_TP: fast peak, small drawdown (peak holds)
        if t_to_mfe is not None and t_to_mfe <= 15 and peak_end_drawdown_pct < 0.5:
            return "SCALP_TP"
        
        # TRAIL_RUNNER: large MFE, late peak, high persistence
        if mfe_pct > 1.0 and t_to_mfe is not None and t_to_mfe > 60 and trend_persistence > 0.6:
            return "TRAIL_RUNNER"
        
        # TREND_CONTINUATION: breakout + holds direction + MACD/EMA support + low MAE
        # (Simplified: high persistence + low MAE relative to MFE + late peak)
        if trend_persistence > 0.65 and mae_pct < mfe_pct * 0.4 and t_to_mfe is not None and t_to_mfe > 30:
            return "TREND_CONTINUATION"
        
        # MEAN_REVERT: price reverses after MFE (BB extremes + RSI snapback)
        # Check if price closes back toward VWAP/mid after extreme
        if len(post_signal_df) >= 3:
            first_close = float(post_signal_df.iloc[0]['close'])
            last_close = float(post_signal_df.iloc[-1]['close'])
            mid_price = (first_close + last_close) / 2
            
            # Check for reversion pattern
            if best_action == "LONG":
                # Price went up then came back down
                max_price = float(post_signal_df['high'].max())
                if max_price > signal_price * 1.01 and last_close < max_price * 0.98:
                    return "MEAN_REVERT"
            else:  # SHORT
                # Price went down then came back up
                min_price = float(post_signal_df['low'].min())
                if min_price < signal_price * 0.99 and last_close > min_price * 1.02:
                    return "MEAN_REVERT"
        
        # CHOP_RISK: high pullbacks or low persistence
        if pullback_count >= 3 or trend_persistence < 0.4:
            return "CHOP_RISK"
        
        # STOP_FAST: large MAE early or negative R with MAE dominating
        if (mae_pct > abs(mfe_pct) * 0.7) or (synthetic_r < 0 and abs(mae_pct) > mfe_pct):
            return "STOP_FAST"
        
        # Default
        return "STANDARD"
    
    def _empty_labels(self, market: str = "US", indicator_ready: bool = True, label_ready: bool = False) -> Dict:
        """Return empty labels structure"""
        cost_penalty = self.cost_penalty_us if market == "US" else self.cost_penalty_crypto
        min_edge_to_trade = self.min_edge_to_trade_us if market == "US" else self.min_edge_to_trade_crypto
        edge_norm = self.edge_norm_us if market == "US" else self.edge_norm_crypto
        
        label_params = {
            'k': self.k,
            'cost_penalty_pct': cost_penalty,
            'min_edge_to_trade_pct': min_edge_to_trade,
            'edge_norm': edge_norm,
            'r_norm': self.r_norm,
            'w_edge': self.w_edge,
            'w_r': self.w_r
        }
        
        return {
            'mfe_long_pct': None,
            'mae_long_pct': None,
            'mfe_short_pct': None,
            'mae_short_pct': None,
            'edge_long': None,
            'edge_short': None,
            'best_edge': None,
            'best_action_at_signal': "NO_TRADE",
            'opportunity_score': 0.0,
            'opportunity_long': 0.0,
            'opportunity_short': 0.0,
            'peak_metrics': {
                'mfe_pct': None,
                'mae_pct': None,
                't_to_mfe_minutes': None,
                'peak_end_drawdown_pct': None,
                'pullback_count': 0,
                'trend_persistence': 0.5
            },
            'synthetic_exit': {
                'synthetic_r_long': 0.0,
                'synthetic_r_short': 0.0,
                'synthetic_r_best': 0.0,
                'exit_reason': "INSUFFICIENT_DATA",
                'exit_time': None,
                'exit_price': None
            },
            'trade_quality': {
                'quality_score': 0.0,
                'quality_grade': "D"
            },
            'exit_style': "INSUFFICIENT_DATA",
            'label_params': label_params,
            'label_ready': label_ready,
            'feature_quality': "GOOD" if indicator_ready else "DEGRADED",
            'label_version': self.label_version
        }
