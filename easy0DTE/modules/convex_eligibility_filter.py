#!/usr/bin/env python3
"""
Convex Eligibility Filter
=========================

Filters ORB signals to determine which trades deserve options exposure.
Not every ORB-qualified trade gets options—only the highest-conviction setups.

Easy 0DTE = selective convex amplification. Gamma > leverage.

Key Criteria (All Must Pass):
1. ORB Volatility Score ≥ Top X percentile
2. ORB range ≥ 0.25% of SYMBOL price OR 5-min ATR ≥ intraday minimum threshold
3. Red Day Check (Rev 00246: Direction-aware):
   - LONG (CALL): Must NOT be Red Day (declining market not good for calls)
   - SHORT (PUT): Red Day is GOOD (declining market is perfect for puts)
4. ORB Break: Long requires price > ORB High, Short requires price < ORB Low
5. Volume > ORB volume average
6. VWAP Condition: Long requires Price ≥ VWAP, Short requires Price ≤ VWAP
7. Early momentum confirmation
8. Market regime = impulse/trend (NOT rotation)

Long Setup Requirements:
- Price breaks above ORB High
- Volume > ORB volume average
- Price ≥ VWAP

Short Setup Requirements:
- Price breaks below ORB Low
- Volume > ORB volume average
- Price ≤ VWAP

Trade Allowed ONLY if:
- ORB range ≥ 0.25% of SYMBOL price
- OR 5-min ATR ≥ intraday minimum threshold

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import logging
import os
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
import numpy as np

log = logging.getLogger(__name__)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def is_leveraged_0dte_symbol(symbol: str) -> bool:
    """Substring match against 0DTE_CONVEX_LEVERAGED_SUBSTRINGS (comma-separated)."""
    raw = os.getenv(
        "0DTE_CONVEX_LEVERAGED_SUBSTRINGS",
        "TQQQ,SPXL,UPRO,SSO,QLD,SQQQ,SPXU,SPXS,SDS,QID",
    )
    toks = [t.strip().upper() for t in str(raw).split(",") if t.strip()]
    su = str(symbol or "").upper()
    return any(t in su for t in toks)


def compute_orb_range_pct(orb_high: float, orb_low: float) -> float:
    """
    Canonical ORB range % for LONG and SHORT (identical).
    Width of opening range as % of low: (orb_high - orb_low) / orb_low * 100.
    """
    try:
        oh, ol = float(orb_high), float(orb_low)
        if ol <= 0 or oh < ol:
            return 0.0
        return (oh - ol) / ol * 100.0
    except (TypeError, ValueError):
        return 0.0


def _merge_orb_into_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    orb_range_pct preferred from morning ORB capture (orb_data dict from ORBData.to_dict()).
    If missing, derive from capture H/L only — same formula as capture.
    """
    signal = dict(signal)
    orb_raw = signal.get('orb_data')
    if orb_raw is not None and not isinstance(orb_raw, dict):
        if hasattr(orb_raw, 'to_dict'):
            signal['orb_data'] = orb_raw.to_dict()
        elif hasattr(orb_raw, 'orb_high') and hasattr(orb_raw, 'orb_low'):
            oh = float(getattr(orb_raw, 'orb_high'))
            ol = float(getattr(orb_raw, 'orb_low'))
            signal['orb_high'] = signal.get('orb_high') or oh
            signal['orb_low'] = signal.get('orb_low') or ol
            pct = float(getattr(orb_raw, 'orb_range_pct', 0) or 0) or compute_orb_range_pct(oh, ol)
            signal['orb_data'] = {'orb_high': oh, 'orb_low': ol, 'orb_range_pct': pct}
    orb_data = signal.get('orb_data') or {}
    if isinstance(orb_data, dict):
        if signal.get('orb_high') is None and orb_data.get('orb_high') is not None:
            signal['orb_high'] = orb_data['orb_high']
        if signal.get('orb_low') is None and orb_data.get('orb_low') is not None:
            signal['orb_low'] = orb_data['orb_low']
        if orb_data.get('orb_range_pct') is not None:
            signal['orb_range_pct'] = float(orb_data['orb_range_pct'])
    oh, ol = signal.get('orb_high'), signal.get('orb_low')
    if oh is not None and ol is not None and (signal.get('orb_range_pct') is None or signal.get('orb_range_pct') == 0):
        signal['orb_range_pct'] = compute_orb_range_pct(oh, ol)
    if (signal.get('current_price') is None or signal.get('current_price') == 0) and signal.get('price') is not None:
        signal['current_price'] = signal['price']
    return signal


@dataclass
class ConvexEligibilityResult:
    """Result of convex eligibility filtering"""
    signal: Dict[str, Any]
    eligibility_score: float
    is_eligible: bool
    eligibility_reasons: List[str]
    rejection_reasons: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'signal': self.signal,
            'eligibility_score': self.eligibility_score,
            'is_eligible': self.is_eligible,
            'eligibility_reasons': self.eligibility_reasons,
            'rejection_reasons': self.rejection_reasons
        }


class ConvexEligibilityFilter:
    """
    Convex Eligibility Filter for 0DTE Strategy
    
    Determines which ORB signals deserve options exposure based on:
    - Volatility score percentile
    - ORB range threshold
    - Red day status
    - Momentum confirmation
    - Market regime
    """
    
    def __init__(
        self,
        volatility_percentile_threshold: float = 0.80,  # Top 20%
        orb_range_min_pct: float = 0.25,  # 0.25% minimum (prevents theta chop)
        momentum_confirmation_required: bool = True,
        trend_day_required: bool = True
    ):
        """
        Initialize Convex Eligibility Filter
        
        Args:
            volatility_percentile_threshold: Minimum percentile for volatility score (0.0-1.0)
            orb_range_min_pct: Minimum ORB range percentage (e.g., 0.35 = 0.35%)
            momentum_confirmation_required: Require early momentum confirmation
            trend_day_required: Require trend/impulse day (not rotation)
        """
        self.volatility_percentile_threshold = volatility_percentile_threshold
        self.orb_range_min_pct = orb_range_min_pct
        self.momentum_confirmation_required = momentum_confirmation_required
        self.trend_day_required = trend_day_required
        
        log.info(f"Convex Eligibility Filter initialized:")
        log.info(f"  - Volatility percentile threshold: {volatility_percentile_threshold*100:.1f}%")
        log.info(f"  - ORB range minimum: {orb_range_min_pct:.2f}%")
        log.info(f"  - Momentum confirmation required: {momentum_confirmation_required}")
        log.info(f"  - Trend day required: {trend_day_required}")

        # Internal score weights / partial credits (ORB 0DTE path — all overridable via env)
        self._w_volatility = _env_float("0DTE_CONVEX_WEIGHT_VOLATILITY", 0.40)
        self._w_range = _env_float("0DTE_CONVEX_WEIGHT_RANGE", 0.18)
        self._w_neutral_pass = _env_float("0DTE_CONVEX_WEIGHT_NEUTRAL_PASS", 0.15)
        self._w_momentum = _env_float("0DTE_CONVEX_WEIGHT_MOMENTUM", 0.18)
        self._w_regime = _env_float("0DTE_CONVEX_WEIGHT_REGIME", 0.09)
        self._range_score_scale = _env_float("0DTE_CONVEX_RANGE_SCORE_SCALE", 0.50)
        self._range_atr_credit = _env_float("0DTE_CONVEX_RANGE_ATR_CREDIT", 0.20)
        self._range_breakdown_credit = _env_float("0DTE_CONVEX_RANGE_BREAKDOWN_CREDIT", 0.22)
        self._lev_vol_floor = _env_float("0DTE_CONVEX_LEVERAGED_VOL_FLOOR", 0.60)
        self._lev_vol_relief = _env_float("0DTE_CONVEX_LEVERAGED_VOL_RELIEF", 0.10)
        self._momentum_proxy_vwap_pct = _env_float("0DTE_CONVEX_MOMENTUM_PROXY_VWAP_PCT", 0.5)
        self._regime_vwap_abs = _env_float("0DTE_CONVEX_REGIME_VWAP_ABS", 1.0)
        self._regime_orb_mult = _env_float("0DTE_CONVEX_REGIME_ORB_MULT", 1.5)
        self._near_miss_low = _env_float("0DTE_CONVEX_NEAR_MISS_SCORE_LOW", 0.60)
        # Last full per-signal Convex results (eligible + rejected), for pipeline audit / Cloud grep joins.
        self._last_full_results: List[ConvexEligibilityResult] = []

    def calculate_eligibility_score(
        self,
        signal: Dict[str, Any],
        all_signals: Optional[List[Dict[str, Any]]] = None
    ) -> float:
        """
        Calculate eligibility score for a signal (0.0-1.0)
        
        Args:
            signal: ORB signal dictionary
            all_signals: All signals for percentile calculation (optional)
            
        Returns:
            Eligibility score (0.0 = not eligible, 1.0 = highly eligible)
        """
        signal = _merge_orb_into_signal(signal)

        score = 0.0
        max_score = 0.0
        
        # 1. Volatility Score (40% weight)
        # Use orb_volume_ratio if orb_volatility_score not available
        volatility_score = signal.get('orb_volatility_score', None)
        if volatility_score is None:
            # Fallback to orb_volume_ratio as proxy for volatility
            volatility_score = signal.get('orb_volume_ratio', 0.0)
        
        if all_signals:
            # Calculate percentile from available volatility scores
            volatility_scores = []
            for s in all_signals:
                vs = s.get('orb_volatility_score', None)
                if vs is None:
                    vs = s.get('orb_volume_ratio', 0.0)
                volatility_scores.append(vs)
            
            if volatility_scores:
                percentile = np.percentile(volatility_scores, self.volatility_percentile_threshold * 100)
                if volatility_score >= percentile:
                    score += self._w_volatility
        else:
            # Fallback: use raw score if above threshold
            if volatility_score >= self.volatility_percentile_threshold:
                score += self._w_volatility
        max_score += self._w_volatility
        
        # 2. ORB Range OR 5-min ATR (25% weight)
        # Trade allowed ONLY if: ORB range ≥ 0.25% OR 5-min ATR ≥ intraday minimum threshold
        orb_range_pct = signal.get('orb_range_pct', 0.0)
        current_price = signal.get('current_price', 0.0)
        atr_5min = signal.get('atr_5min', None)
        atr_threshold_pct = signal.get(
            'atr_threshold_pct',
            _env_float("0DTE_CONVEX_ATR_THRESHOLD_PCT_DEFAULT", 0.25),
        )
        
        orb_range_pass = orb_range_pct >= self.orb_range_min_pct
        atr_pass = False
        if not orb_range_pass and atr_5min is not None and current_price > 0:
            atr_threshold = current_price * (atr_threshold_pct / 100.0)
            atr_pass = atr_5min >= atr_threshold
        # SHORT only: if opening range still 0% after capture bug, breakdown below ORB low proves movement
        _dir = (signal.get('side') or 'LONG').upper()
        _ol = signal.get('orb_low')
        breakdown_pass = False
        if (
            not orb_range_pass and not atr_pass and _dir == 'SHORT'
            and _ol and float(_ol) > 0 and current_price > 0 and current_price < float(_ol)
        ):
            br = (float(_ol) - current_price) / float(_ol) * 100.0
            if br >= self.orb_range_min_pct:
                breakdown_pass = True
        if orb_range_pass:
            range_score = min(1.0, (orb_range_pct / max(self.orb_range_min_pct, 1e-9)) * self._range_score_scale)
            score += self._w_range * range_score
        elif atr_pass:
            score += self._range_atr_credit
        elif breakdown_pass:
            score += self._range_breakdown_credit
        max_score += self._w_range
        
        # 3. Market Regime / Red Day (15% weight)
        # Rev 00313: Red Day filter MUST NOT affect 0DTE direction or eligibility.
        # 0DTE CALL/PUT trades are scored purely on instrument-level signals; any
        # portfolio "Red Day" state only blocks ORB ETF execution.
        #
        # Preserve the weighting budget by giving all signals a neutral pass here.
        direction = signal.get('side', 'LONG').upper()
        score += self._w_neutral_pass
        max_score += self._w_neutral_pass
        
        # 4. Momentum Confirmation (10% weight)
        # NOTE: RS vs SPY NOT used - primary 0DTE underlyings (SPX, SPY, QQQ) make it null/nonsensical
        has_momentum = signal.get('momentum_confirmed', None)
        if has_momentum is None:
            macd_histogram = float(signal.get('macd_histogram') or 0)
            vwap_distance = float(signal.get('vwap_distance_pct') or 0)
            volr = float(
                signal.get('volume_ratio')
                or signal.get('orb_volume_ratio')
                or signal.get('exec_volume_ratio')
                or 0.0
            )
            has_momentum = macd_histogram > 0 or vwap_distance > self._momentum_proxy_vwap_pct
            # ORB break alone is **not** continuation proof (prevents pure extension / spike scoring).
            if not has_momentum:
                _oh = signal.get('orb_high')
                _ol = signal.get('orb_low')
                _cp = signal.get('current_price') or signal.get('price') or 0
                _side = (signal.get('side') or 'LONG').upper()
                if _side == 'LONG' and _oh and _cp > _oh:
                    if macd_histogram > 0 or volr >= 1.0:
                        has_momentum = True
                elif _side == 'SHORT' and _ol and _cp > 0 and _cp < _ol:
                    if macd_histogram > 0 or volr >= 1.0:
                        has_momentum = True
        
        if has_momentum or not self.momentum_confirmation_required:
            score += self._w_momentum
        max_score += self._w_momentum
        
        # 5. Market Regime (10% weight)
        # NOTE: RS vs SPY NOT used - SPX/SPY/QQQ are primary 0DTE underlyings, RS would be null
        market_regime = signal.get('market_regime', None)
        if market_regime is None:
            vwap_distance = signal.get('vwap_distance_pct') or 0
            orb_range_pct = signal.get('orb_range_pct') or 0
            # VWAP distance or ORB range expansion indicate trend (no RS vs SPY)
            if abs(vwap_distance) > self._regime_vwap_abs:
                market_regime = 'trend'
            elif orb_range_pct >= (self.orb_range_min_pct or 0.25) * self._regime_orb_mult:
                market_regime = 'trend'
            else:
                market_regime = 'rotation'
        
        is_trend_day = market_regime in ['trend', 'impulse', 'BULL', 'BEAR']
        if is_trend_day or not self.trend_day_required:
            score += self._w_regime
        max_score += self._w_regime
        
        # Normalize score
        if max_score > 0:
            normalized_score = score / max_score
        else:
            normalized_score = 0.0
        
        return normalized_score
    
    def is_eligible(
        self,
        signal: Dict[str, Any],
        all_signals: Optional[List[Dict[str, Any]]] = None,
        min_score: float = 0.75
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Determine if signal is eligible for options trading
        
        Args:
            signal: ORB signal dictionary
            all_signals: All signals for percentile calculation
            min_score: Minimum eligibility score threshold
            
        Returns:
            Tuple of (is_eligible, eligibility_reasons, rejection_reasons)
        """
        eligibility_reasons = []
        rejection_reasons = []
        
        signal = _merge_orb_into_signal(signal)
        
        # Calculate eligibility score
        score = self.calculate_eligibility_score(signal, all_signals)
        
        # Check individual criteria
        checks = {
            'volatility': False,
            'orb_range_or_atr': False,  # ORB range ≥ 0.25% OR 5-min ATR ≥ threshold
            'not_red_day': False,
            'orb_break': False,  # Long: price > ORB High, Short: price < ORB Low
            'volume': False,  # Volume > ORB volume average
            'vwap': False,  # Long: Price ≥ VWAP, Short: Price ≤ VWAP
            'momentum': False,
            'trend_day': False
        }
        
        # 1. Volatility Score Check
        # Use orb_volume_ratio if orb_volatility_score not available
        volatility_score = signal.get('orb_volatility_score', None)
        if volatility_score is None:
            volatility_score = signal.get('orb_volume_ratio', 0.0)
        
        # Adjust threshold for leveraged ETFs (TQQQ, SPXL, etc.)
        symbol = signal.get('symbol', '')
        is_leveraged = is_leveraged_0dte_symbol(symbol)
        volatility_threshold = self.volatility_percentile_threshold
        if is_leveraged:
            volatility_threshold = max(self._lev_vol_floor, volatility_threshold - self._lev_vol_relief)
        
        if all_signals:
            volatility_scores = []
            for s in all_signals:
                vs = s.get('orb_volatility_score', None)
                if vs is None:
                    vs = s.get('orb_volume_ratio', 0.0)
                volatility_scores.append(vs)
            
            if volatility_scores:
                percentile = np.percentile(volatility_scores, volatility_threshold * 100)
                if volatility_score >= percentile:
                    checks['volatility'] = True
                    eligibility_reasons.append(f"Volatility score {volatility_score:.2f} ≥ {percentile:.2f} percentile ({'leveraged ETF' if is_leveraged else 'standard'})")
                else:
                    rejection_reasons.append(f"Volatility score {volatility_score:.2f} < {percentile:.2f} percentile ({'leveraged ETF' if is_leveraged else 'standard'})")
        else:
            if volatility_score >= volatility_threshold:
                checks['volatility'] = True
                eligibility_reasons.append(f"Volatility score {volatility_score:.2f} ≥ threshold ({'leveraged ETF' if is_leveraged else 'standard'})")
            else:
                rejection_reasons.append(f"Volatility score {volatility_score:.2f} < threshold ({'leveraged ETF' if is_leveraged else 'standard'})")
        
        # 2. ORB Range OR 5-min ATR Check
        # Trade allowed ONLY if: ORB range ≥ 0.25% OR 5-min ATR ≥ intraday minimum threshold
        orb_range_pct = signal.get('orb_range_pct', 0.0)
        current_price = signal.get('current_price', 0.0)
        atr_5min = signal.get('atr_5min', None)  # 5-minute ATR
        atr_threshold_pct = signal.get(
            'atr_threshold_pct',
            _env_float("0DTE_CONVEX_ATR_THRESHOLD_PCT_DEFAULT", 0.25),
        )
        
        symbol = signal.get('symbol', '')
        is_leveraged = is_leveraged_0dte_symbol(symbol)
        range_threshold = self.orb_range_min_pct  # Defaults to 0.25%
        
        orb_range_pass = orb_range_pct >= range_threshold
        atr_pass = False
        if not orb_range_pass and atr_5min is not None and current_price > 0:
            atr_threshold = current_price * (atr_threshold_pct / 100.0)
            atr_pass = atr_5min >= atr_threshold
        direction = signal.get('side', 'LONG').upper()
        orb_low = signal.get('orb_low')
        breakdown_pass = False
        breakdown_pct = 0.0
        if (
            not orb_range_pass and not atr_pass and direction == 'SHORT'
            and orb_low and float(orb_low) > 0 and current_price > 0 and current_price < float(orb_low)
        ):
            breakdown_pct = (float(orb_low) - current_price) / float(orb_low) * 100.0
            if breakdown_pct >= range_threshold:
                breakdown_pass = True
        if orb_range_pass:
            checks['orb_range_or_atr'] = True
            eligibility_reasons.append(f"ORB range {orb_range_pct:.2f}% ≥ {range_threshold:.2f}% ({'leveraged ETF' if is_leveraged else 'standard'})")
        elif atr_pass:
            checks['orb_range_or_atr'] = True
            eligibility_reasons.append(f"5-min ATR ${atr_5min:.2f} ≥ ${atr_threshold:.2f} threshold (alternative to ORB range)")
        elif breakdown_pass:
            checks['orb_range_or_atr'] = True
            eligibility_reasons.append(
                f"SHORT breakdown {breakdown_pct:.2f}% below ORB low (ORB width {orb_range_pct:.2f}% unreliable — same min {range_threshold:.2f}%)"
            )
        else:
            rejection_reasons.append(f"ORB range {orb_range_pct:.2f}% < {range_threshold:.2f}% AND 5-min ATR insufficient")
        
        # 3. Red Day Check (DISABLED for 0DTE) - Rev 00313
        # Requirement: Red Day must NOT impact 0DTE signal eligibility.
        # Direction (CALL vs PUT) is determined solely by 0DTE rules, independent of
        # any portfolio-level Red Day state.
        #
        # We keep the `not_red_day` check key marked as pass for all signals so the
        # diagnostics and weighting remain consistent, but we do not inspect or use
        # any `is_red_day` flag here.
        direction = signal.get('side', 'LONG').upper()
        checks['not_red_day'] = True
        eligibility_reasons.append("0DTE Red Day filter disabled (direction determined by rules only)")
        
        # 4. ORB High/Low Break Check
        # Long Setup: Price breaks above ORB High
        # Short Setup: Price breaks below ORB Low
        direction = signal.get('side', 'LONG').upper()
        orb_high = signal.get('orb_high', None)
        orb_low = signal.get('orb_low', None)
        current_price = signal.get('current_price', 0.0)
        
        orb_break_pass = False
        if direction == 'LONG' and orb_high is not None and current_price > 0:
            if current_price > orb_high:
                checks['orb_break'] = True
                orb_break_pass = True
                eligibility_reasons.append(f"LONG: Price ${current_price:.2f} > ORB High ${orb_high:.2f}")
            else:
                rejection_reasons.append(f"LONG: Price ${current_price:.2f} ≤ ORB High ${orb_high:.2f} (no breakout)")
        elif direction == 'SHORT' and orb_low is not None and current_price > 0:
            if current_price < orb_low:
                checks['orb_break'] = True
                orb_break_pass = True
                eligibility_reasons.append(f"SHORT: Price ${current_price:.2f} < ORB Low ${orb_low:.2f}")
            else:
                rejection_reasons.append(f"SHORT: Price ${current_price:.2f} ≥ ORB Low ${orb_low:.2f} (no breakdown)")
        else:
            # If ORB data missing, skip this check (don't reject)
            checks['orb_break'] = True
            orb_break_pass = True
            eligibility_reasons.append(f"ORB break check skipped (missing ORB data)")
        
        # 5. Volume Check
        # Volume > ORB volume average
        current_volume = signal.get('volume', 0)
        orb_volume_avg = signal.get('orb_volume_avg', None)
        
        if orb_volume_avg is not None and orb_volume_avg > 0:
            if current_volume > orb_volume_avg:
                checks['volume'] = True
                eligibility_reasons.append(f"Volume {current_volume:,} > ORB avg {orb_volume_avg:,.0f}")
            else:
                rejection_reasons.append(f"Volume {current_volume:,} ≤ ORB avg {orb_volume_avg:,.0f}")
        else:
            # If ORB volume data missing, skip this check (don't reject)
            checks['volume'] = True
            eligibility_reasons.append("Volume check skipped (missing ORB volume data)")
        
        # 6. VWAP Check
        # Long Setup: Price ≥ VWAP
        # Short Setup: Price ≤ VWAP
        vwap = signal.get('vwap', None)
        current_price = signal.get('current_price', 0.0)
        
        if vwap is not None and current_price > 0:
            if direction == 'LONG':
                if current_price >= vwap:
                    checks['vwap'] = True
                    eligibility_reasons.append(f"LONG: Price ${current_price:.2f} ≥ VWAP ${vwap:.2f}")
                else:
                    rejection_reasons.append(f"LONG: Price ${current_price:.2f} < VWAP ${vwap:.2f}")
            else:  # SHORT
                if current_price <= vwap:
                    checks['vwap'] = True
                    eligibility_reasons.append(f"SHORT: Price ${current_price:.2f} ≤ VWAP ${vwap:.2f}")
                else:
                    rejection_reasons.append(f"SHORT: Price ${current_price:.2f} > VWAP ${vwap:.2f}")
        else:
            # If VWAP data missing, skip this check (don't reject)
            checks['vwap'] = True
            eligibility_reasons.append("VWAP check skipped (missing VWAP data)")
        
        # 7. Momentum Confirmation Check
        # NOTE: RS vs SPY NOT used - primary 0DTE underlyings (SPX, SPY, QQQ) make it null
        if self.momentum_confirmation_required:
            has_momentum = signal.get('momentum_confirmed', None)
            if has_momentum is None:
                macd_histogram = float(signal.get('macd_histogram') or 0)
                vwap_distance = float(signal.get('vwap_distance_pct') or 0)
                volr = float(
                    signal.get('volume_ratio')
                    or signal.get('orb_volume_ratio')
                    or signal.get('exec_volume_ratio')
                    or 0.0
                )
                has_momentum = macd_histogram > 0 or vwap_distance > self._momentum_proxy_vwap_pct
                if not has_momentum and direction == 'LONG' and orb_high and current_price > orb_high:
                    if macd_histogram > 0 or volr >= 1.0:
                        has_momentum = True
                elif not has_momentum and direction == 'SHORT' and orb_low and current_price > 0 and current_price < orb_low:
                    if macd_histogram > 0 or volr >= 1.0:
                        has_momentum = True
            
            if has_momentum:
                checks['momentum'] = True
                eligibility_reasons.append("Momentum confirmed (from MACD/VWAP or ORB breakout)")
            else:
                rejection_reasons.append("Momentum confirmation missing")
        else:
            checks['momentum'] = True  # Not required
        
        # 8. Market Regime Check
        # NOTE: RS vs SPY NOT used - SPX/SPY/QQQ primary 0DTE underlyings, RS would be null
        if self.trend_day_required:
            market_regime = signal.get('market_regime', None)
            if market_regime is None:
                vwap_distance = signal.get('vwap_distance_pct') or 0
                orb_range_pct = signal.get('orb_range_pct') or 0
                # VWAP distance or ORB range expansion indicates trend (no RS vs SPY)
                if abs(vwap_distance) > self._regime_vwap_abs:
                    market_regime = 'trend'
                elif orb_range_pct >= (self.orb_range_min_pct or 0.25) * self._regime_orb_mult:
                    market_regime = 'trend'
                elif direction == 'LONG' and orb_high and current_price > orb_high:
                    market_regime = 'trend'  # ORB breakout = directional trend
                elif direction == 'SHORT' and orb_low and current_price > 0 and current_price < orb_low:
                    market_regime = 'trend'
                else:
                    market_regime = 'rotation'
            
            is_trend_day = market_regime in ['trend', 'impulse', 'BULL', 'BEAR']
            if is_trend_day:
                checks['trend_day'] = True
                eligibility_reasons.append(f"Market regime: {market_regime} (inferred from VWAP/ORB)")
            else:
                rejection_reasons.append(f"Market regime: {market_regime} (not trend/impulse)")
        else:
            checks['trend_day'] = True  # Not required
        
        # Determine eligibility
        all_checks_pass = all(checks.values())
        score_pass = score >= min_score
        
        is_eligible = all_checks_pass and score_pass
        
        if is_eligible:
            eligibility_reasons.append(f"Eligibility score: {score:.2f} ≥ {min_score:.2f}")
        else:
            if not score_pass:
                rejection_reasons.append(f"Eligibility score: {score:.2f} < {min_score:.2f}")
        
        return is_eligible, eligibility_reasons, rejection_reasons
    
    def filter_signals(
        self,
        signals: List[Dict[str, Any]],
        min_score: float = 0.75,
        max_signals: Optional[int] = None
    ) -> List[ConvexEligibilityResult]:
        """
        Filter signals through convex eligibility criteria
        
        Args:
            signals: List of ORB signals
            min_score: Minimum eligibility score threshold
            max_signals: Maximum number of eligible signals to return (None = all)
            
        Returns:
            List of ConvexEligibilityResult objects
        """
        if not signals:
            log.warning("No signals provided to filter")
            self._last_full_results = []
            return []
        
        log.info(f"🔍 Convex Eligibility Filter: Filtering {len(signals)} signals (min score: {min_score:.2f})")
        log.info(f"   Filter criteria: 8 checks — Volatility 40%, ORB Range/ATR 25%, Red Day 15%, ORB Break (required), Volume (required), VWAP (required), Momentum 10%, Market Regime 10%")
        log.info(
            "  0DTE_CONVEX_STAGE | stage=filter_start | "
            "grep=CONVEX_PASS|CONVEX_REJECT|CONVEX_REJECT_DETAIL|CONVEX_near_miss|CONVEX_0_eligible"
        )
        
        # Calculate eligibility for all signals
        results = []
        for signal in signals:
            symbol = signal.get('symbol', 'UNKNOWN')
            direction = signal.get('side', 'LONG')
            
            is_eligible, eligibility_reasons, rejection_reasons = self.is_eligible(
                signal, signals, min_score
            )
            
            score = self.calculate_eligibility_score(signal, signals)
            
            result = ConvexEligibilityResult(
                signal=signal,
                eligibility_score=score,
                is_eligible=is_eligible,
                eligibility_reasons=eligibility_reasons,
                rejection_reasons=rejection_reasons
            )
            
            results.append(result)
            
            # Rev 00246 / 00292: Per-signal pass/fail at DEBUG; summary block logs top 5 at INFO when 0 pass
            opt_type = 'CALL' if direction == 'LONG' else 'PUT'
            if is_eligible:
                log.debug(f"   ✅ {symbol} {opt_type}: ELIGIBLE (Score: {score:.3f}, Passed: {len(eligibility_reasons)} checks)")
            else:
                log.debug(f"   ❌ {symbol} {opt_type}: REJECTED (Score: {score:.3f}, Failed: {len(rejection_reasons)} checks)")
                if rejection_reasons:
                    log.debug(f"      Top rejection: {rejection_reasons[0]}")
        
        # Sort by eligibility score (descending)
        results.sort(key=lambda x: x.eligibility_score, reverse=True)
        
        # Filter to eligible signals only
        eligible_results = [r for r in results if r.is_eligible]
        
        # Limit to max_signals if specified
        if max_signals and len(eligible_results) > max_signals:
            eligible_results = eligible_results[:max_signals]
        
        input_long = sum(1 for s in signals if s.get('side', 'LONG') == 'LONG')
        input_short = sum(1 for s in signals if s.get('side', 'LONG') == 'SHORT')
        log.info(f"Convex Eligibility Filter Results:")
        log.info(f"  - Total signals: {len(signals)} (LONG: {input_long}, SHORT: {input_short})")
        log.info(f"  - Eligible signals: {len(eligible_results)}")
        log.info(f"  - Rejected signals: {len(signals) - len(eligible_results)}")
        
        # Grep-friendly per-symbol pass/reject (so we can see exactly which symbols passed and which rejected when 0 or few out)
        for r in results:
            symbol = r.signal.get('symbol', 'UNKNOWN')
            direction = r.signal.get('side', 'LONG')
            opt_type = 'CALL' if direction == 'LONG' else 'PUT'
            score = r.eligibility_score
            if r.is_eligible:
                log.info(f"  CONVEX_PASS | {symbol} {opt_type} score={score:.2f}")
            else:
                first_reason = r.rejection_reasons[0] if (r.rejection_reasons and len(r.rejection_reasons) > 0) else 'Unknown'
                top_reason = (first_reason[:80] + '...') if (isinstance(first_reason, str) and len(first_reason) > 80) else first_reason
                log.info(f"  CONVEX_REJECT | {symbol} {opt_type} score={score:.2f} reason={top_reason}")
                if r.rejection_reasons:
                    joined = " || ".join(str(x) for x in r.rejection_reasons[:20])
                    if len(joined) > 720:
                        joined = joined[:717] + "..."
                    log.info(f"  CONVEX_REJECT_DETAIL | {symbol} {opt_type} score={score:.2f} | all_reasons={joined}")
        
        # Summary line for grep: 0 vs N eligible
        if eligible_results:
            passed_symbols = [f"{r.signal.get('symbol', '?')}_{'CALL' if r.signal.get('side', 'LONG') == 'LONG' else 'PUT'}" for r in eligible_results]
            log.info(f"  CONVEX_N_eligible | total={len(eligible_results)} LONG={sum(1 for r in eligible_results if r.signal.get('side','LONG')=='LONG')} SHORT={sum(1 for r in eligible_results if r.signal.get('side','LONG')=='SHORT')} | symbols={','.join(passed_symbols[:25])}{'...' if len(passed_symbols) > 25 else ''}")
        else:
            all_symbols = [f"{r.signal.get('symbol', '?')}_{'CALL' if r.signal.get('side', 'LONG') == 'LONG' else 'PUT'}" for r in results[:50]]
            log.info(f"  CONVEX_0_eligible | total={len(signals)} LONG={input_long} SHORT={input_short} | rejected_symbols_sample={','.join(all_symbols)}{'...' if len(results) > 50 else ''} | grep CONVEX_REJECT for per-symbol reasons")
            # Near-miss: scores in [low, min_score) — helps see if filter is too strict
            nm_low = self._near_miss_low
            near_miss = [r for r in results if nm_low <= r.eligibility_score < min_score]
            if near_miss:
                near_miss.sort(key=lambda x: x.eligibility_score, reverse=True)
                top5_parts = [f"{r.signal.get('symbol','?')}_{'CALL' if r.signal.get('side','LONG')=='LONG' else 'PUT'}={r.eligibility_score:.2f}" for r in near_miss[:5]]
                log.info(f"  CONVEX_near_miss | count={len(near_miss)} (score {nm_low:.2f}-{min_score:.2f}) | top5: {', '.join(top5_parts)}")
        
        log.info(
            f"  0DTE_CONVEX_STAGE | stage=filter_done | in={len(signals)} eligible={len(eligible_results)} "
            f"rejected={len(signals) - len(eligible_results)}"
        )
        
        # Log top eligible signals
        if eligible_results:
            eligible_long = sum(1 for r in eligible_results if r.signal.get('side', 'LONG') == 'LONG')
            eligible_short = sum(1 for r in eligible_results if r.signal.get('side', 'LONG') == 'SHORT')
            log.info(f"  CONVEX_FILTER | {len(eligible_results)}_eligible | CALL={eligible_long} PUT={eligible_short}")
            log.info(f"  ✅ Eligible breakdown: {eligible_long} CALL (LONG), {eligible_short} PUT (SHORT)")
            log.info(f"  ✅ Top {min(5, len(eligible_results))} Eligible Signals:")
            for i, result in enumerate(eligible_results[:5], 1):
                symbol = result.signal.get('symbol', 'UNKNOWN')
                direction = result.signal.get('side', 'LONG')
                opt_type = 'CALL' if direction == 'LONG' else 'PUT'
                score = result.eligibility_score
                reasons = result.eligibility_reasons[:4] if result.eligibility_reasons else ['No reasons provided']
                log.info(f"    {i}. {symbol} {opt_type}: Score {score:.2f}")
                log.info(f"       Passed: {', '.join(reasons)}")
        else:
            # Log top rejection reasons if no signals passed (Rev 00232: Enhanced diagnostics, Rev 00292: Check-by-check counts)
            rejection_reason_counts = {}
            for result in results:
                for reason in result.rejection_reasons:
                    rejection_reason_counts[reason] = rejection_reason_counts.get(reason, 0) + 1
            
            top_rejection_reasons = sorted(
                rejection_reason_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:8]
            
            # Rev 00292: Bucket rejection reasons by check type for diagnosis
            check_labels = {
                "volatility": "Volatility (40%)",
                "orb_range_atr": "ORB Range/ATR (25%)",
                "red_day": "Red Day (15%)",
                "orb_break": "ORB Break (price vs high/low)",
                "volume": "Volume",
                "vwap": "VWAP",
                "momentum": "Momentum (10%)",
                "market_regime": "Market Regime (10%)",
                "score": "Eligibility score < 0.75",
                "other": "Other"
            }
            def _bucket_rejection(r: str) -> str:
                if not r:
                    return "other"
                rl = r.lower()
                if "volatility" in rl or "percentile" in rl:
                    return "volatility"
                if "orb range" in rl or "atr" in rl:
                    return "orb_range_atr"
                if "red day" in rl:
                    return "red_day"
                if "orb high" in rl or "orb low" in rl or "breakout" in rl or "breakdown" in rl:
                    return "orb_break"
                if "volume" in rl and "orb" in rl:
                    return "volume"
                if "vwap" in rl:
                    return "vwap"
                if "momentum" in rl:
                    return "momentum"
                if "market regime" in rl or "rotation" in rl or "trend" in rl:
                    return "market_regime"
                if "eligibility score" in rl:
                    return "score"
                return "other"
            
            check_fail_counts = {}
            for result in results:
                for reason in result.rejection_reasons:
                    bucket = _bucket_rejection(reason)
                    check_fail_counts[bucket] = check_fail_counts.get(bucket, 0) + 1
            
            # Rev 00292: One-line grep-friendly summary
            top_3_buckets = sorted(check_fail_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            top_fail_str = "; ".join([f"{check_labels.get(b, b)}={c}" for b, c in top_3_buckets])
            log.info(f"  CONVEX_FILTER | 0_eligible | total={len(signals)} | top_failures: {top_fail_str}")
            log.info(f"  📊 Convex Filter Diagnosis (0 passed) — Check failure counts:")
            for bucket in ["volatility", "orb_range_atr", "red_day", "orb_break", "volume", "vwap", "momentum", "market_regime", "score", "other"]:
                cnt = check_fail_counts.get(bucket, 0)
                if cnt > 0:
                    pct = (cnt / len(signals)) * 100
                    log.info(f"    • {check_labels.get(bucket, bucket)}: {cnt} signals ({pct:.1f}%)")
            
            if top_rejection_reasons:
                log.info(f"  📊 Top Rejection Reasons (verbatim):")
                for i, (reason, count) in enumerate(top_rejection_reasons, 1):
                    pct = (count / len(signals)) * 100
                    log.info(f"    {i}. [{count}/{len(signals)}] {reason[:95]}{'...' if len(reason) > 95 else ''}")
            
            # Log top 5 signals with their scores and ALL rejection reasons (Rev 00233: Enhanced, Rev 00292: INFO level when 0 pass)
            log.info(f"  📋 Top 5 Signals (by score) - Per-symbol rejection details:")
            for i, result in enumerate(results[:5], 1):
                symbol = result.signal.get('symbol', 'UNKNOWN')
                direction = result.signal.get('side', 'LONG')
                opt_type = 'CALL' if direction == 'LONG' else 'PUT'
                score = result.eligibility_score
                all_reasons = result.rejection_reasons if result.rejection_reasons else ['Unknown']
                log.info(f"    {i}. {symbol} {opt_type}: Score {score:.2f} — FAILED {len(all_reasons)} check(s):")
                for j, reason in enumerate(all_reasons, 1):
                    log.info(f"       {j}. {reason}")
        
        self._last_full_results = results
        return eligible_results
    
    def get_filter_stats(self, results: List[ConvexEligibilityResult]) -> Dict[str, Any]:
        """
        Get statistics about filtering results
        
        Args:
            results: List of ConvexEligibilityResult objects
            
        Returns:
            Dictionary with filter statistics
        """
        if not results:
            return {
                'total_signals': 0,
                'eligible_count': 0,
                'rejected_count': 0,
                'eligibility_rate': 0.0,
                'avg_eligibility_score': 0.0,
                'top_rejection_reasons': []
            }
        
        eligible = [r for r in results if r.is_eligible]
        rejected = [r for r in results if not r.is_eligible]
        
        # Count rejection reasons
        rejection_reason_counts = {}
        for result in rejected:
            for reason in result.rejection_reasons:
                rejection_reason_counts[reason] = rejection_reason_counts.get(reason, 0) + 1
        
        top_rejection_reasons = sorted(
            rejection_reason_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        return {
            'total_signals': len(results),
            'eligible_count': len(eligible),
            'rejected_count': len(rejected),
            'eligibility_rate': len(eligible) / len(results) if results else 0.0,
            'avg_eligibility_score': sum(r.eligibility_score for r in results) / len(results) if results else 0.0,
            'top_rejection_reasons': [{'reason': r[0], 'count': r[1]} for r in top_rejection_reasons]
        }

