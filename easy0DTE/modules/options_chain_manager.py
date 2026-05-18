#!/usr/bin/env python3
"""
Options Chain Manager
=====================

Manages SPX, QQQ & SPY options chains for 0DTE trading.
Handles chain fetching, liquidity analysis, and strike selection.
Priority Order: SPX (professional/institutional) → QQQ (momentum 0DTE) → SPY (most liquid)

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import logging
import math
import os
import time
from typing import Dict, List, Optional, Any, Tuple, Set
from datetime import datetime, date, timedelta
from dataclasses import dataclass
from zoneinfo import ZoneInfo
import asyncio

# Import ETrade Options API
try:
    from .etrade_options_api import ETradeOptionsAPI, ETradeOptionContract
    ETRADE_OPTIONS_AVAILABLE = True
except ImportError:
    ETRADE_OPTIONS_AVAILABLE = False
    logging.warning("ETrade Options API not available")

log = logging.getLogger(__name__)

ZERO_DTE_SYMBOLS = ["SPY", "QQQ", "IWM"]


def _options_expiry_yyyy_mm_dd_us() -> str:
    """0DTE expiry calendar date in US equity session timezone (not server local/UTC)."""
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


@dataclass
class OptionContract:
    """Option contract data"""
    symbol: str
    strike: float
    expiry: str
    option_type: str  # 'call' or 'put'
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    delta: float
    gamma: float
    theta: float
    vega: float
    implied_volatility: float
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price"""
        return (self.bid + self.ask) / 2.0
    
    @property
    def bid_ask_spread(self) -> float:
        """Calculate bid-ask spread"""
        return self.ask - self.bid
    
    @property
    def bid_ask_spread_pct(self) -> float:
        """Calculate bid-ask spread as percentage"""
        if self.mid_price > 0:
            return (self.bid_ask_spread / self.mid_price) * 100.0
        return 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'strike': self.strike,
            'expiry': self.expiry,
            'option_type': self.option_type,
            'bid': self.bid,
            'ask': self.ask,
            'last': self.last,
            'volume': self.volume,
            'open_interest': self.open_interest,
            'delta': self.delta,
            'gamma': self.gamma,
            'theta': self.theta,
            'vega': self.vega,
            'implied_volatility': self.implied_volatility,
            'mid_price': self.mid_price,
            'bid_ask_spread': self.bid_ask_spread,
            'bid_ask_spread_pct': self.bid_ask_spread_pct
        }


@dataclass
class DebitSpread:
    """Debit spread structure"""
    symbol: str
    expiry: str
    option_type: str  # 'call' or 'put'
    long_strike: float
    short_strike: float
    long_contract: OptionContract
    short_contract: OptionContract
    debit_cost: float
    max_profit: float
    max_loss: float
    break_even: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'expiry': self.expiry,
            'option_type': self.option_type,
            'long_strike': self.long_strike,
            'short_strike': self.short_strike,
            'long_contract': self.long_contract.to_dict(),
            'short_contract': self.short_contract.to_dict(),
            'debit_cost': self.debit_cost,
            'max_profit': self.max_profit,
            'max_loss': self.max_loss,
            'break_even': self.break_even
        }


@dataclass
class CreditSpread:
    """Credit spread structure"""
    symbol: str
    expiry: str
    option_type: str  # 'call' or 'put'
    short_strike: float  # Short leg (sell this)
    long_strike: float  # Long leg (buy this for protection)
    short_contract: OptionContract
    long_contract: OptionContract
    credit_received: float  # Net credit received
    max_profit: float  # Max profit = credit received
    max_loss: float  # Max loss = spread_width - credit_received
    break_even: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'expiry': self.expiry,
            'option_type': self.option_type,
            'short_strike': self.short_strike,
            'long_strike': self.long_strike,
            'short_contract': self.short_contract.to_dict(),
            'long_contract': self.long_contract.to_dict(),
            'credit_received': self.credit_received,
            'max_profit': self.max_profit,
            'max_loss': self.max_loss,
            'break_even': self.break_even
        }


class OptionsChainManager:
    """
    Options Chain Manager for 0DTE Strategy
    
    Handles:
    - Fetching options chains from broker API
    - Analyzing liquidity (bid/ask spreads, open interest)
    - Selecting optimal strikes for debit spreads
    - Validating trade eligibility
    """
    
    def __init__(
        self,
        min_open_interest: int = 100,
        max_bid_ask_spread_pct: float = 15.0,  # 15% max spread (deterministic rule: reject if >15% of mid)
        min_volume: int = 50,
        options_min_open_interest: Optional[int] = None,
        options_max_bid_ask_spread_pct: Optional[float] = None,
        options_min_volume: Optional[int] = None,
        min_risk_reward_ratio: float = 1.5,  # Minimum R:R ratio (1.5-2.5x)
        max_risk_reward_ratio: float = 2.5,  # Maximum R:R ratio (1.5-2.5x)
        chain_stale_threshold_seconds: int = 300,  # 5 minutes (reject if chain older than this)
        single_leg_min_open_interest: int = 500,
        single_leg_min_volume: int = 200,
        single_leg_open_window_minutes: float = 5.0,
        single_leg_open_window_oi_mult: float = 0.50,
        single_leg_open_window_volume_mult: float = 0.50,
        single_leg_mega_symbols: Optional[Set[str]] = None,
        liquidity_relax_open_window_minutes: float = 8.0,
        liquidity_relax_spread_mult: float = 1.35,
        liquidity_relax_open_interest_mult: float = 0.50,
        liquidity_relax_volume_mult: float = 0.50,
        liquidity_relax_symbols: Optional[Set[str]] = None,
        etrade_options_api: Optional[Any] = None,
        use_live_api: bool = False
    ):
        """
        Initialize Options Chain Manager
        
        Deterministic Contract/Spread Picker Rules:
        - Reject trade if bid/ask spread > 15% of mid
        - Reject trade if option volume < minimum
        - Reject trade if OI < minimum
        - Reject trade if chain update stale (>5 minutes)
        - Pick: ATM → slightly OTM long leg, Short leg at R:R ≥ 1.5–2.5x
        
        Args:
            min_open_interest: Minimum open interest for liquidity (reject if <minimum)
            max_bid_ask_spread_pct: Maximum bid-ask spread percentage (reject if >15% of mid)
            min_volume: Minimum volume for liquidity (reject if <minimum)
            min_risk_reward_ratio: Minimum risk:reward ratio for spread selection (1.5x)
            max_risk_reward_ratio: Maximum risk:reward ratio for spread selection (2.5x)
            chain_stale_threshold_seconds: Reject trade if chain update stale (seconds)
            etrade_options_api: ETradeOptionsAPI instance (optional)
            use_live_api: Use live ETrade API (False = demo/mock)
        """
        # Unified threshold resolution: 0DTE_MIN_* and 0DTE_OPTIONS_* must resolve to one runtime set.
        eff_min_oi = int(options_min_open_interest) if options_min_open_interest is not None else int(min_open_interest)
        eff_min_vol = int(options_min_volume) if options_min_volume is not None else int(min_volume)
        eff_max_spread = (
            float(options_max_bid_ask_spread_pct)
            if options_max_bid_ask_spread_pct is not None
            else float(max_bid_ask_spread_pct)
        )
        self.min_open_interest = eff_min_oi
        self.max_bid_ask_spread_pct = eff_max_spread
        self.min_volume = eff_min_vol
        self.options_min_open_interest = eff_min_oi
        self.options_max_bid_ask_spread_pct = eff_max_spread
        self.options_min_volume = eff_min_vol
        self.min_risk_reward_ratio = min_risk_reward_ratio
        self.max_risk_reward_ratio = max_risk_reward_ratio
        self.chain_stale_threshold_seconds = chain_stale_threshold_seconds
        self.use_live_api = use_live_api
        self.single_leg_min_open_interest = int(max(1, single_leg_min_open_interest))
        self.single_leg_min_volume = int(max(1, single_leg_min_volume))
        self.single_leg_open_window_minutes = max(0.0, float(single_leg_open_window_minutes))
        self.single_leg_open_window_oi_mult = max(0.10, min(1.0, float(single_leg_open_window_oi_mult)))
        self.single_leg_open_window_volume_mult = max(0.10, min(1.0, float(single_leg_open_window_volume_mult)))
        self.single_leg_mega_symbols = set(single_leg_mega_symbols or {
            "TSLA", "NVDA", "SPY", "QQQ", "META", "AAPL", "MSFT", "AMD", "AMZN"
        })
        self.liquidity_relax_open_window_minutes = max(0.0, float(liquidity_relax_open_window_minutes))
        self.liquidity_relax_spread_mult = max(1.0, float(liquidity_relax_spread_mult))
        self.liquidity_relax_open_interest_mult = max(0.10, min(1.0, float(liquidity_relax_open_interest_mult)))
        self.liquidity_relax_volume_mult = max(0.10, min(1.0, float(liquidity_relax_volume_mult)))
        self.liquidity_relax_symbols = set(liquidity_relax_symbols or self.single_leg_mega_symbols)
        for tok in str(os.getenv("0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS", "") or "").split(","):
            symx = str(tok or "").strip().upper()
            if symx:
                self.liquidity_relax_symbols.add(symx)

        # ETrade Options API
        if etrade_options_api:
            self.etrade_api = etrade_options_api
        elif use_live_api and ETRADE_OPTIONS_AVAILABLE:
            try:
                environment = os.getenv('ETRADE_ENVIRONMENT', 'prod')
                self.etrade_api = ETradeOptionsAPI(environment=environment)
            except Exception as e:
                log.warning(f"Failed to initialize ETrade Options API: {e}")
                self.etrade_api = None
        else:
            self.etrade_api = None
        
        # Cache for options chains (5-minute TTL)
        self.chain_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl = 300  # 5 minutes
        # Selector telemetry for downstream execution-reject diagnostics.
        self.last_selector_diagnostics: Dict[str, Any] = {}
        # Chain fetch telemetry keyed by symbol for execution diagnostics.
        self.last_chain_fetch_meta: Dict[str, Dict[str, Any]] = {}
        
        mode_label = "💰 LIVE API" if use_live_api and self.etrade_api else "🎮 DEMO/MOCK"
        log.info(f"Options Chain Manager initialized ({mode_label}):")
        log.info(f"  - Min open interest: {self.min_open_interest} (reject if <minimum)")
        log.info(f"  - Max bid-ask spread: {self.max_bid_ask_spread_pct:.1f}% of mid (reject if >15%)")
        log.info(f"  - Min volume: {self.min_volume} (reject if <minimum)")
        log.info(
            "  - Liquidity relax window: open_window_min=%.1f spread_mult=%.2f oi_mult=%.2f volume_mult=%.2f symbols=%d",
            self.liquidity_relax_open_window_minutes,
            self.liquidity_relax_spread_mult,
            self.liquidity_relax_open_interest_mult,
            self.liquidity_relax_volume_mult,
            len(self.liquidity_relax_symbols),
        )
        log.info(
            "  - Single-leg floors: min_volume=%d min_open_interest=%d open_window_min=%.1f "
            "open_window_oi_mult=%.2f open_window_volume_mult=%.2f",
            self.single_leg_min_volume,
            self.single_leg_min_open_interest,
            self.single_leg_open_window_minutes,
            self.single_leg_open_window_oi_mult,
            self.single_leg_open_window_volume_mult,
        )
        log.info(f"  - Risk:Reward ratio: {min_risk_reward_ratio:.1f}x - {max_risk_reward_ratio:.1f}x")
        log.info(f"  - Chain stale threshold: {chain_stale_threshold_seconds}s (reject if stale)")
        log.info(f"  - Live API enabled: {use_live_api and self.etrade_api is not None}")

    @staticmethod
    def _parse_hh_mm_et(raw: str, default_h: int, default_m: int) -> Tuple[int, int]:
        s = (raw or "").strip()
        if not s:
            return default_h, default_m
        parts = s.replace(":", " ").split()
        try:
            return int(parts[0]) % 24, int(parts[1]) % 60 if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return default_h, default_m

    def _in_hard_gate_execution_window_et(self) -> bool:
        """ORB 0DTE hard-gate clock window in America/New_York (same keys as Hard Gate)."""
        sh, sm = self._parse_hh_mm_et(os.getenv("0DTE_HARD_GATE_ET_START", "10:30"), 10, 30)
        eh, em = self._parse_hh_mm_et(os.getenv("0DTE_HARD_GATE_ET_END", "10:40"), 10, 40)
        now_et = datetime.now(ZoneInfo("America/New_York")).time()
        start_t = datetime.now(ZoneInfo("America/New_York")).replace(
            hour=sh, minute=sm, second=0, microsecond=0
        ).time()
        end_t = datetime.now(ZoneInfo("America/New_York")).replace(
            hour=eh, minute=em, second=0, microsecond=0
        ).time()
        if start_t <= end_t:
            return start_t <= now_et <= end_t
        return now_et >= start_t or now_et <= end_t

    def _liquidity_thresholds_for_symbol(self, symbol: str) -> Tuple[float, int, int]:
        max_spread_pct = float(self.max_bid_ask_spread_pct)
        min_open_interest = int(self.min_open_interest)
        min_volume = int(self.min_volume)
        symbol_u = str(symbol or "").upper()
        if not symbol_u or symbol_u not in self.liquidity_relax_symbols:
            base = (max_spread_pct, min_open_interest, min_volume)
        elif self.liquidity_relax_open_window_minutes <= 0:
            base = (max_spread_pct, min_open_interest, min_volume)
        else:
            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
            session_open = now_pt.replace(hour=7, minute=30, second=0, microsecond=0)
            minutes_since_open = max(0.0, (now_pt - session_open).total_seconds() / 60.0)
            if minutes_since_open > self.liquidity_relax_open_window_minutes:
                base = (max_spread_pct, min_open_interest, min_volume)
            else:
                base = (
                    max_spread_pct * self.liquidity_relax_spread_mult,
                    max(1, int(round(min_open_interest * self.liquidity_relax_open_interest_mult))),
                    max(1, int(round(min_volume * self.liquidity_relax_volume_mult))),
                )
        ms, m_oi, m_vol = base
        # Wider bid/ask cap only during hard-gate ET window, and only off mega-cap list (preserve mega-cap quality).
        try:
            relax_exec = float(os.getenv("0DTE_HARD_GATE_EXEC_SPREAD_RELAX_MULT", "1.125"))
        except (TypeError, ValueError):
            relax_exec = 1.125
        relax_exec = max(1.0, min(1.35, relax_exec))
        if symbol_u and symbol_u not in self.single_leg_mega_symbols and self._in_hard_gate_execution_window_et():
            ms = min(35.0, float(ms) * relax_exec)
        return ms, m_oi, m_vol

    def _record_chain_fetch_meta(
        self,
        *,
        symbol: str,
        source: str,
        expiry: Optional[str],
        elapsed_ms: float,
        from_cache: bool,
        calls: int,
        puts: int,
        error_type: Optional[str] = None,
    ) -> None:
        key = str(symbol or "").upper()
        self.last_chain_fetch_meta[key] = {
            "symbol": key,
            "source": str(source or "unknown"),
            "expiry": expiry,
            "elapsed_ms": float(elapsed_ms),
            "from_cache": bool(from_cache),
            "calls": int(calls),
            "puts": int(puts),
            "error_type": str(error_type) if error_type else None,
            "timestamp": datetime.now().isoformat(),
        }

    def get_last_chain_fetch_meta(self, symbol: str) -> Dict[str, Any]:
        return dict(self.last_chain_fetch_meta.get(str(symbol or "").upper(), {}))

    @staticmethod
    def _is_itm_contract(contract: OptionContract, option_type: str, underlying_price: float) -> bool:
        """
        Determine ITM by option direction.

        CALL ITM: strike < underlying_price
        PUT ITM: strike > underlying_price
        """
        if option_type == "call":
            return float(contract.strike) < float(underlying_price)
        return float(contract.strike) > float(underlying_price)

    def _itm_contracts(
        self,
        contracts: List[OptionContract],
        option_type: str,
        underlying_price: float,
    ) -> List[OptionContract]:
        """Return contracts that are ITM by direction-aware rules."""
        return [
            c for c in contracts
            if self._is_itm_contract(c, option_type, underlying_price)
        ]

    def validate_chain_viability(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        underlying_price: float,
        min_itm_for_spread: int = 2,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate pre-execution chain viability for spread building.
        """
        contracts = list((chain or {}).get(option_type + "s", []) or [])
        itm = self._itm_contracts(contracts, option_type, underlying_price)
        bid_ask_valid = [
            c for c in contracts
            if float(c.bid or 0.0) > 0.0 and float(c.ask or 0.0) > 0.0
        ]
        viable = (
            len(contracts) > 0
            and len(bid_ask_valid) > 0
            and len(itm) >= int(max(1, min_itm_for_spread))
        )
        diag = {
            "option_type": option_type,
            "spot": float(underlying_price),
            "total_contracts": int(len(contracts)),
            "valid_bid_ask_contracts": int(len(bid_ask_valid)),
            "itm_contracts": int(len(itm)),
            "min_itm_for_spread": int(max(1, min_itm_for_spread)),
            "viable": bool(viable),
        }
        return viable, diag

    def count_valid_spread_candidates(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        underlying_price: float,
    ) -> int:
        """
        Count rough valid spread pairs from ITM long legs to OTM short legs.
        """
        contracts = list((chain or {}).get(option_type + "s", []) or [])
        if not contracts:
            return 0
        itm_legs = self._itm_contracts(contracts, option_type, underlying_price)
        count = 0
        for long_leg in itm_legs:
            long_ok, _ = self.validate_liquidity(long_leg)
            if not long_ok:
                continue
            if option_type == "call":
                otm = [c for c in contracts if float(c.strike) > float(long_leg.strike)]
            else:
                otm = [c for c in contracts if float(c.strike) < float(long_leg.strike)]
            for short_leg in otm:
                short_ok, _ = self.validate_liquidity(short_leg)
                if short_ok:
                    count += 1
        return int(count)

    def compute_viability_score(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        underlying_price: float,
        target_delta: Optional[float] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Compute normalized viability score for early execution filtering.
        """
        contracts = list((chain or {}).get(option_type + "s", []) or [])
        viable, base_diag = self.validate_chain_viability(
            chain=chain,
            option_type=option_type,
            underlying_price=underlying_price,
            min_itm_for_spread=2,
        )
        valid_quotes = [
            c for c in contracts
            if float(c.bid or 0.0) > 0.0 and float(c.ask or 0.0) > 0.0
        ]
        fallback_priced = [
            c for c in contracts
            if float(c.last or 0.0) > 0.0 or float(c.mid_price or 0.0) > 0.0
        ]
        avg_spread_pct = (
            sum(float(c.bid_ask_spread_pct) for c in valid_quotes) / len(valid_quotes)
            if valid_quotes else 999.0
        )
        representative_pool = valid_quotes if valid_quotes else fallback_priced
        representative_contract: Optional[OptionContract] = None
        if representative_pool:
            if isinstance(target_delta, (int, float)) and float(target_delta) > 0.0:
                representative_contract = min(
                    representative_pool,
                    key=lambda c: (
                        abs(abs(float(c.delta or 0.0)) - float(target_delta)),
                        abs(float(c.strike or 0.0) - float(underlying_price)),
                    ),
                )
            else:
                representative_contract = min(
                    representative_pool,
                    key=lambda c: abs(float(c.strike or 0.0) - float(underlying_price)),
                )
        representative_spread_pct: Optional[float] = None
        representative_price_source = "none"
        representative_price: Optional[float] = None
        if representative_contract is not None:
            if float(representative_contract.bid or 0.0) > 0.0 and float(representative_contract.ask or 0.0) > 0.0:
                representative_spread_pct = float(representative_contract.bid_ask_spread_pct)
                representative_price_source = "bid_ask_mid"
                representative_price = float(representative_contract.mid_price)
            elif float(representative_contract.last or 0.0) > 0.0:
                representative_price_source = "last"
                representative_price = float(representative_contract.last)
            elif float(representative_contract.mid_price or 0.0) > 0.0:
                representative_price_source = "mid_approx"
                representative_price = float(representative_contract.mid_price)
        total_volume = int(sum(int(c.volume or 0) for c in valid_quotes))
        total_oi = int(sum(int(c.open_interest or 0) for c in valid_quotes))
        itm_count = int(base_diag.get("itm_contracts", 0))
        spread_candidates = self.count_valid_spread_candidates(
            chain=chain,
            option_type=option_type,
            underlying_price=underlying_price,
        )

        spread_norm = min(1.0, max(0.0, 1.0 - (avg_spread_pct / 30.0)))
        volume_norm = min(1.0, math.log(total_volume + 1.0) / 10.0)
        oi_norm = min(1.0, math.log(total_oi + 1.0) / 10.0)
        itm_norm = min(1.0, itm_count / 12.0)
        pair_norm = min(1.0, spread_candidates / 25.0)

        score_raw = (
            spread_norm * 0.35
            + volume_norm * 0.20
            + oi_norm * 0.20
            + itm_norm * 0.10
            + pair_norm * 0.15
        )
        viability_score = max(0.0, min(1.0, float(score_raw)))
        diag = {
            **base_diag,
            "avg_bid_ask_spread_pct": float(avg_spread_pct),
            "fallback_price_contracts": int(len(fallback_priced)),
            "representative_spread_pct": representative_spread_pct,
            "representative_price_source": representative_price_source,
            "representative_price": representative_price,
            "representative_strike": float(representative_contract.strike) if representative_contract else None,
            "representative_delta": float(representative_contract.delta) if representative_contract else None,
            "representative_bid": float(representative_contract.bid) if representative_contract else None,
            "representative_ask": float(representative_contract.ask) if representative_contract else None,
            "representative_volume": int(representative_contract.volume) if representative_contract else None,
            "representative_open_interest": int(representative_contract.open_interest) if representative_contract else None,
            "total_volume": int(total_volume),
            "total_open_interest": int(total_oi),
            "spread_candidate_count": int(spread_candidates),
            "spread_norm": float(spread_norm),
            "volume_norm": float(volume_norm),
            "open_interest_norm": float(oi_norm),
            "itm_norm": float(itm_norm),
            "pair_norm": float(pair_norm),
            "viability_score": float(viability_score),
            "viability": bool(viable),
        }
        return viability_score, diag
    
    def _build_synthetic_demo_chain(self, symbol: str, expiry: str, spot: float) -> Dict[str, List[OptionContract]]:
        """
        DEMO-only chain so strike selection and mock execution can run without E*TRADE.
        Does not represent real market quotes — enables end-to-end 0DTE flow in DEMO_MODE.
        """
        spot = max(float(spot), 0.01)
        if spot >= 200:
            step = 5.0
        elif spot >= 50:
            step = 2.5
        elif spot >= 15:
            step = 1.0
        else:
            step = 0.5
        center = round(spot / step) * step
        strikes = [center + i * step for i in range(-10, 11)]
        scale = max(0.2, spot * 0.015)
        vol, oi = 500, 500
        calls: List[OptionContract] = []
        puts: List[OptionContract] = []
        for strike in strikes:
            dist = abs(strike - spot) / spot
            call_intr = max(0.0, spot - strike)
            put_intr = max(0.0, strike - spot)
            call_mid = max(0.12, call_intr + scale * math.exp(-dist * 2.5))
            put_mid = max(0.12, put_intr + scale * math.exp(-dist * 2.5))
            bid_c, ask_c = call_mid * 0.985, call_mid * 1.015
            bid_p, ask_p = put_mid * 0.985, put_mid * 1.015
            delta_c = max(0.03, min(0.97, 0.5 + (spot - strike) / max(spot * 0.4, 1e-6)))
            delta_p = delta_c - 1.0
            calls.append(OptionContract(
                symbol=symbol,
                strike=float(strike),
                expiry=expiry,
                option_type='call',
                bid=bid_c,
                ask=ask_c,
                last=call_mid,
                volume=vol,
                open_interest=oi,
                delta=delta_c,
                gamma=0.01,
                theta=-0.02,
                vega=0.05,
                implied_volatility=0.35,
            ))
            puts.append(OptionContract(
                symbol=symbol,
                strike=float(strike),
                expiry=expiry,
                option_type='put',
                bid=bid_p,
                ask=ask_p,
                last=put_mid,
                volume=vol,
                open_interest=oi,
                delta=delta_p,
                gamma=0.01,
                theta=-0.02,
                vega=0.05,
                implied_volatility=0.35,
            ))
        return {'calls': calls, 'puts': puts}
    
    async def fetch_options_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        use_cache: bool = True,
        underlying_price: Optional[float] = None,
    ) -> Dict[str, List[OptionContract]]:
        """
        Fetch options chain for symbol
        
        Args:
            symbol: Underlying symbol
            expiry: Expiry date (YYYY-MM-DD format, None = 0DTE US/Eastern)
            use_cache: Use cached chain if available
            underlying_price: Spot for DEMO synthetic chain (ORB last); required for non-empty demo chains
            
        Returns:
            Dictionary with 'calls' and 'puts' lists
        """
        started = time.perf_counter()
        requested_expiry = expiry
        symbol_u = str(symbol or "").upper()
        is_0dte_native = symbol_u in ZERO_DTE_SYMBOLS
        log.info(
            "SYMBOL_CLASSIFICATION | symbol=%s | type=%s",
            symbol,
            "0DTE_NATIVE" if is_0dte_native else "NON_0DTE",
        )
        prefer_0dte = requested_expiry is None
        if expiry is None:
            expiry = _options_expiry_yyyy_mm_dd_us()
        
        cache_key = f"{symbol}_{expiry}"
        if not self.use_live_api and underlying_price and float(underlying_price) > 0:
            cache_key = f"{symbol}_{expiry}_syn_u{round(float(underlying_price), 2)}"
        
        # Check cache
        if use_cache and cache_key in self.chain_cache:
            cached_data = self.chain_cache[cache_key]
            cache_time = cached_data.get('timestamp', 0)
            cache_age_seconds = datetime.now().timestamp() - cache_time
            
            # Deterministic rule: Reject trade if chain update stale
            if cache_age_seconds > self.chain_stale_threshold_seconds:
                log.warning(f"⚠️ Chain update stale for {symbol} {expiry}: {cache_age_seconds:.0f}s > {self.chain_stale_threshold_seconds}s threshold")
                log.warning(f"   Rejecting trade - chain data too old (deterministic rule)")
                # Don't use stale cache, fetch fresh data
            elif cache_age_seconds < self.cache_ttl:
                log.debug(f"Using cached options chain for {symbol} {expiry} (age: {cache_age_seconds:.0f}s)")
                cached_chain = cached_data['chain']
                log.info(
                    "0DTE_CHAIN_SOURCE | symbol=%s | source=cache_%s | expiry=%s | calls=%d | puts=%d | cache_age_s=%.0f",
                    symbol,
                    "live" if self.use_live_api else "demo",
                    expiry,
                    len((cached_chain or {}).get('calls', []) or []),
                    len((cached_chain or {}).get('puts', []) or []),
                    cache_age_seconds,
                )
                self._record_chain_fetch_meta(
                    symbol=symbol,
                    source=f"cache_{'live' if self.use_live_api else 'demo'}",
                    expiry=expiry,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    from_cache=True,
                    calls=len((cached_chain or {}).get('calls', []) or []),
                    puts=len((cached_chain or {}).get('puts', []) or []),
                )
                return cached_data['chain']
        
        # Live API: Fetch from ETrade
        if self.use_live_api and self.etrade_api and self.etrade_api.is_available():
            try:
                async def _fetch_live_for_expiry(candidate_expiry: str) -> Tuple[List[OptionContract], List[OptionContract]]:
                    candidate_etrade = candidate_expiry.replace('-', '')
                    etrade_chain = await self.etrade_api.fetch_options_chain(
                        symbol=symbol,
                        expiry=candidate_etrade,
                        strike_count=20,
                        include_greeks=True,
                        underlying_price=underlying_price,
                    )
                    calls_local: List[OptionContract] = []
                    puts_local: List[OptionContract] = []

                    for etrade_call in etrade_chain.get('calls', []):
                        calls_local.append(
                            OptionContract(
                                symbol=etrade_call.symbol,
                                strike=etrade_call.strike,
                                expiry=candidate_expiry,
                                option_type='call',
                                bid=etrade_call.bid,
                                ask=etrade_call.ask,
                                last=etrade_call.last,
                                volume=etrade_call.volume,
                                open_interest=etrade_call.open_interest,
                                delta=etrade_call.delta or 0.0,
                                gamma=etrade_call.gamma or 0.0,
                                theta=etrade_call.theta or 0.0,
                                vega=etrade_call.vega or 0.0,
                                implied_volatility=etrade_call.implied_volatility or 0.0,
                            )
                        )

                    for etrade_put in etrade_chain.get('puts', []):
                        puts_local.append(
                            OptionContract(
                                symbol=etrade_put.symbol,
                                strike=etrade_put.strike,
                                expiry=candidate_expiry,
                                option_type='put',
                                bid=etrade_put.bid,
                                ask=etrade_put.ask,
                                last=etrade_put.last,
                                volume=etrade_put.volume,
                                open_interest=etrade_put.open_interest,
                                delta=etrade_put.delta or 0.0,
                                gamma=etrade_put.gamma or 0.0,
                                theta=etrade_put.theta or 0.0,
                                vega=etrade_put.vega or 0.0,
                                implied_volatility=etrade_put.implied_volatility or 0.0,
                            )
                        )
                    return calls_local, puts_local

                selected_expiry = expiry
                selected_type = "EXPLICIT"
                calls: List[OptionContract] = []
                puts: List[OptionContract] = []

                if prefer_0dte:
                    # 0DTE-native symbols: strict same-day first, then nearest fallback.
                    # Non-0DTE symbols: nearest expiry immediately.
                    today_us = date.fromisoformat(expiry)
                    max_fallback_days = int(os.getenv("0DTE_NEAREST_EXPIRY_MAX_DAYS", "10"))

                    if is_0dte_native:
                        calls, puts = await _fetch_live_for_expiry(expiry)
                        if calls or puts:
                            selected_type = "0DTE"
                        else:
                            nearest_expiry: Optional[str] = None
                            for d in range(1, max(1, max_fallback_days) + 1):
                                candidate = (today_us + timedelta(days=d)).isoformat()
                                c_calls, c_puts = await _fetch_live_for_expiry(candidate)
                                if c_calls or c_puts:
                                    nearest_expiry = candidate
                                    calls, puts = c_calls, c_puts
                                    break
                            if nearest_expiry:
                                selected_expiry = nearest_expiry
                                selected_type = "NEAREST"
                            else:
                                selected_type = "NONE"
                    else:
                        nearest_expiry = None
                        for d in range(0, max(1, max_fallback_days) + 1):
                            candidate = (today_us + timedelta(days=d)).isoformat()
                            c_calls, c_puts = await _fetch_live_for_expiry(candidate)
                            if c_calls or c_puts:
                                nearest_expiry = candidate
                                calls, puts = c_calls, c_puts
                                break
                        if nearest_expiry:
                            selected_expiry = nearest_expiry
                            selected_type = "0DTE" if nearest_expiry == expiry else "NEAREST"
                        else:
                            selected_type = "NONE"
                else:
                    calls, puts = await _fetch_live_for_expiry(expiry)

                chain = {'calls': calls, 'puts': puts}
                selected_cache_key = f"{symbol}_{selected_expiry}"
                self.chain_cache[selected_cache_key] = {
                    'chain': chain,
                    'timestamp': datetime.now().timestamp(),
                }

                log.info(
                    "CHAIN_EXPIRY_SELECTION | symbol=%s | type=%s | requested=%s | selected=%s | calls=%d | puts=%d",
                    symbol,
                    selected_type,
                    requested_expiry or _options_expiry_yyyy_mm_dd_us(),
                    selected_expiry,
                    len(calls),
                    len(puts),
                )
                log.info(f"✅ Fetched options chain from ETrade: {len(calls)} calls, {len(puts)} puts")
                log.info(
                    "0DTE_CHAIN_SOURCE | symbol=%s | source=etrade_live | expiry=%s | calls=%d | puts=%d",
                    symbol,
                    selected_expiry,
                    len(calls),
                    len(puts),
                )
                self._record_chain_fetch_meta(
                    symbol=symbol,
                    source="etrade_live",
                    expiry=selected_expiry,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    from_cache=False,
                    calls=len(calls),
                    puts=len(puts),
                )
                return chain
                
            except Exception as e:
                log.error(f"Failed to fetch options chain from ETrade: {e}")
                error_type = "timeout" if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else "API_error"
                self._record_chain_fetch_meta(
                    symbol=symbol,
                    source="etrade_live_error",
                    expiry=expiry,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    from_cache=False,
                    calls=0,
                    puts=0,
                    error_type=error_type,
                )
                # Fall through to demo synthetic / empty chain
        
        demo_synthetic = os.getenv('0DTE_DEMO_SYNTHETIC_CHAIN', 'true').lower() == 'true'
        if (
            not self.use_live_api
            and demo_synthetic
            and underlying_price is not None
            and float(underlying_price) > 0
        ):
            chain = self._build_synthetic_demo_chain(symbol, expiry, float(underlying_price))
            self.chain_cache[cache_key] = {
                'chain': chain,
                'timestamp': datetime.now().timestamp(),
            }
            log.info(
                f"0DTE_DEMO | synthetic_chain | symbol={symbol} expiry={expiry} spot={float(underlying_price):.2f} "
                f"calls={len(chain['calls'])} puts={len(chain['puts'])}"
            )
            log.info(
                "0DTE_CHAIN_SOURCE | symbol=%s | source=synthetic_demo | expiry=%s | calls=%d | puts=%d | spot=%.2f",
                symbol,
                expiry,
                len(chain['calls']),
                len(chain['puts']),
                float(underlying_price),
            )
            self._record_chain_fetch_meta(
                symbol=symbol,
                source="synthetic_demo",
                expiry=expiry,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                from_cache=False,
                calls=len(chain['calls']),
                puts=len(chain['puts']),
            )
            return chain
        
        # Demo/Mock: no spot or synthetic disabled — empty chain (execution will report no contracts)
        log.warning(
            f"Options chain unavailable (Demo) for {symbol} {expiry}: "
            f"synthetic={'off' if not demo_synthetic else 'on'} underlying_price={underlying_price!r}"
        )
        chain = {'calls': [], 'puts': []}
        self.chain_cache[cache_key] = {
            'chain': chain,
            'timestamp': datetime.now().timestamp(),
        }
        log.info(
            "0DTE_CHAIN_SOURCE | symbol=%s | source=none | expiry=%s | calls=0 | puts=0",
            symbol,
            expiry,
        )
        meta = self.get_last_chain_fetch_meta(symbol)
        self._record_chain_fetch_meta(
            symbol=symbol,
            source=meta.get("source", "none"),
            expiry=expiry,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            from_cache=bool(meta.get("from_cache", False)),
            calls=0,
            puts=0,
            error_type=meta.get("error_type") or "no_chain_data",
        )
        return chain
    
    def validate_liquidity(self, contract: OptionContract) -> Tuple[bool, List[str]]:
        """
        Validate contract liquidity (Deterministic Contract Picker Rules)
        
        Rev 00246: Enhanced logging for performance adjustments
        
        Reject trade if:
        - Bid/ask spread > 15% of mid
        - Option volume < minimum
        - OI < minimum
        - Missing bid/ask prices
        
        Args:
            contract: Option contract to validate
            
        Returns:
            Tuple of (is_valid, reasons)
        """
        reasons = []
        is_valid = True
        
        # Deterministic Rule 1: Check open interest (reject if <minimum)
        max_spread_pct, min_open_interest, min_volume = self._liquidity_thresholds_for_symbol(contract.symbol)
        if contract.open_interest < min_open_interest:
            is_valid = False
            reasons.append(f"Open interest {contract.open_interest} < {min_open_interest} (reject: OI < minimum)")
            log.debug(f"   ❌ Hard Gate: OI {contract.open_interest} < {min_open_interest} for {contract.symbol} {contract.option_type} ${contract.strike}")
        else:
            log.debug(f"   ✅ Hard Gate: OI {contract.open_interest} ≥ {min_open_interest} for {contract.symbol} {contract.option_type} ${contract.strike}")
        
        # Deterministic Rule 2: Check bid-ask spread (reject if above configured max)
        if contract.bid_ask_spread_pct > max_spread_pct:
            is_valid = False
            reasons.append(
                f"Bid/ask spread {contract.bid_ask_spread_pct:.2f}% > {max_spread_pct:.1f}% of mid "
                f"(reject: spread >{max_spread_pct:.1f}%)"
            )
            log.debug(f"   ❌ Hard Gate: Spread {contract.bid_ask_spread_pct:.2f}% > {max_spread_pct:.1f}% for {contract.symbol} {contract.option_type} ${contract.strike} (Bid: ${contract.bid:.2f}, Ask: ${contract.ask:.2f}, Mid: ${contract.mid_price:.2f})")
        else:
            log.debug(f"   ✅ Hard Gate: Spread {contract.bid_ask_spread_pct:.2f}% ≤ {max_spread_pct:.1f}% for {contract.symbol} {contract.option_type} ${contract.strike}")
        
        # Deterministic Rule 3: Check volume (reject if <minimum)
        if contract.volume < min_volume:
            is_valid = False
            reasons.append(f"Volume {contract.volume} < {min_volume} (reject: volume < minimum)")
            log.debug(f"   ❌ Hard Gate: Volume {contract.volume} < {min_volume} for {contract.symbol} {contract.option_type} ${contract.strike}")
        else:
            log.debug(f"   ✅ Hard Gate: Volume {contract.volume} ≥ {min_volume} for {contract.symbol} {contract.option_type} ${contract.strike}")
        
        # Deterministic Rule 4: Check if bid/ask exist (reject if missing)
        if contract.bid <= 0 or contract.ask <= 0:
            is_valid = False
            reasons.append("Missing bid/ask prices (reject: no pricing data)")
            log.debug(f"   ❌ Hard Gate: Missing bid/ask prices for {contract.symbol} {contract.option_type} ${contract.strike} (Bid: ${contract.bid:.2f}, Ask: ${contract.ask:.2f})")
        else:
            log.debug(f"   ✅ Hard Gate: Bid/ask prices exist for {contract.symbol} {contract.option_type} ${contract.strike} (Bid: ${contract.bid:.2f}, Ask: ${contract.ask:.2f})")
        
        # Rev 00246: Summary log for Hard Gate validation
        if is_valid:
            log.debug(f"   ✅ Hard Gate PASSED: {contract.symbol} {contract.option_type} ${contract.strike} (OI: {contract.open_interest}, Spread: {contract.bid_ask_spread_pct:.2f}%, Volume: {contract.volume})")
        else:
            # Avoid warning-level noise for obviously unusable stale placeholders (e.g. strike=0, no bid/ask).
            has_actionable_quote = contract.strike > 0 and contract.bid > 0 and contract.ask > 0
            _msg = f"   ❌ Hard Gate FAILED: {contract.symbol} {contract.option_type} ${contract.strike} - {', '.join(reasons)}"
            if has_actionable_quote:
                log.warning(_msg)
            else:
                log.debug(_msg)
        
        return is_valid, reasons
    
    def select_debit_spread_strikes(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        target_delta: float,
        spread_width: float,
        current_price: float,
        relax_delta_extra: float = 0.0,
        allow_nearest_long_outside_band: bool = False,
        momentum_score: float = 0.0,
    ) -> Optional[DebitSpread]:
        """
        Select optimal strikes for debit spread - Rev 00212
        
        Contract Selection Logic (Best Practices):
        - Premium: $0.20-$0.60 (cheap contracts for gamma explosion)
        - Delta: 10-30 (0.10-0.30) - cheap gamma that can explode
        - Strike: 1-3 strikes OTM (out of the money)
        - Why: Cheap contracts give gamma explosion, easy 5-20x with small moves
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            target_delta: Target delta for long leg (0.10-0.30) - Rev 00212
            spread_width: Spread width ($1 or $2 for QQQ/SPY, $5 or $10 for SPX)
            current_price: Current underlying price
            
        Returns:
            DebitSpread object or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        self.last_selector_diagnostics = {
            "selector": "debit_spread",
            "option_type": option_type,
            "target_delta": float(target_delta),
            "spread_width": float(spread_width),
            "spot": float(current_price),
            "contracts_this_side": int(len(contracts)),
            "selected": False,
            "momentum_score": float(momentum_score or 0.0),
        }
        
        if not contracts:
            log.warning(f"No {option_type} contracts available")
            self.last_selector_diagnostics.update({
                "failure_stage": "no_contracts",
                "failure_reason": f"no_{option_type}_contracts",
            })
            return None
        
        # Rev 00349: Relax delta window for spread construction reliability on high-priced names.
        # This still keeps us near intended structure but avoids brittle "no spread found" outcomes.
        relax_delta_extra = max(0.0, float(relax_delta_extra or 0.0))
        try:
            mom_thr_sel = float(os.getenv("0DTE_DEBIT_SELECTOR_HIGH_MOMENTUM_MIN", "68"))
        except (TypeError, ValueError):
            mom_thr_sel = 68.0
        _high_mom = float(momentum_score or 0.0) >= mom_thr_sel
        _mom_pad = 0.055 if _high_mom else 0.0
        delta_min = max(0.04, target_delta - (0.10 + relax_delta_extra + _mom_pad * 0.55))
        delta_max = min(0.78 if _high_mom else 0.70, target_delta + (0.14 + relax_delta_extra + _mom_pad))
        
        # Rev 00348: Premium ladder for spread long-leg selection.
        # Keep the original "cheap gamma" profile first, then relax premium cap (still spread-only)
        # so high-price names (e.g., META/NVDA/TSLA) don't get dropped solely by a strict $0.60 cap.
        strict_premium_min = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MIN", "0.15"))
        strict_premium_max = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MAX_STRICT", "0.60"))
        soft_premium_max = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MAX_SOFT", "1.20"))
        rescue_premium_max = float(os.getenv("0DTE_DEBIT_LONG_PREMIUM_MAX_RESCUE", "2.50"))
        premium_ladder = [
            ("strict_gamma", strict_premium_min, strict_premium_max),
            ("balanced_soft_cap", max(0.05, strict_premium_min), max(strict_premium_max, soft_premium_max)),
            ("liquidity_rescue_cap", max(0.03, strict_premium_min), max(soft_premium_max, rescue_premium_max)),
        ]

        # Rev 00349: Configurable OTM leg distance for spread construction.
        # Default allows wider lattice search than strict 1-3, reducing false no-spread outcomes.
        otm_min = int(float(os.getenv("0DTE_DEBIT_LONG_OTM_MIN", "1")))
        otm_max = int(float(os.getenv("0DTE_DEBIT_LONG_OTM_MAX", "5")))
        if otm_min < 1:
            otm_min = 1
        if otm_max < otm_min:
            otm_max = otm_min

        # Rev 00212: Filter by strike position (configurable OTM range)
        # For calls: strike > current_price (OTM)
        # For puts: strike < current_price (OTM)
        candidate_long_legs: List[OptionContract] = []
        selected_ladder_label = "none"
        selected_premium_min = strict_premium_min
        selected_premium_max = strict_premium_max

        # Get all strikes sorted for OTM calculation
        all_strikes = sorted(set(c.strike for c in contracts))

        for ladder_label, premium_min, premium_max in premium_ladder:
            tmp_candidates: List[OptionContract] = []
            for c in contracts:
                # Check delta range
                if not (delta_min <= abs(c.delta) <= delta_max):
                    continue

                # Check premium range (use mid price)
                premium = c.mid_price
                if not (premium_min <= premium <= premium_max):
                    continue

                # Check strike position (1-3 strikes OTM)
                if option_type == 'call':
                    # Call: strike should be above current price (OTM)
                    if c.strike <= current_price:
                        continue  # Not OTM

                    # Count how many strikes are OTM between current_price and this strike
                    otm_strikes = [s for s in all_strikes if current_price < s <= c.strike]
                    strikes_otm = len(otm_strikes)

                    if not (otm_min <= strikes_otm <= otm_max):
                        continue
                else:  # put
                    # Put: strike should be below current price (OTM)
                    if c.strike >= current_price:
                        continue  # Not OTM

                    # Count how many strikes are OTM between this strike and current_price
                    otm_strikes = [s for s in all_strikes if current_price > s >= c.strike]
                    strikes_otm = len(otm_strikes)

                    if not (otm_min <= strikes_otm <= otm_max):
                        continue

                tmp_candidates.append(c)
            if tmp_candidates:
                candidate_long_legs = tmp_candidates
                selected_ladder_label = ladder_label
                selected_premium_min = premium_min
                selected_premium_max = premium_max
                break

        if not candidate_long_legs and allow_nearest_long_outside_band:
            # Final selector relaxation: keep premium/OTM constraints, but allow nearest delta
            # contract when strict delta band is too restrictive.
            nearest_candidates: List[OptionContract] = []
            for c in contracts:
                premium = c.mid_price
                if not (selected_premium_min <= premium <= max(selected_premium_max, rescue_premium_max)):
                    continue
                if option_type == 'call':
                    if c.strike <= current_price:
                        continue
                    otm_strikes = [s for s in all_strikes if current_price < s <= c.strike]
                    strikes_otm = len(otm_strikes)
                    if not (otm_min <= strikes_otm <= otm_max):
                        continue
                else:
                    if c.strike >= current_price:
                        continue
                    otm_strikes = [s for s in all_strikes if current_price > s >= c.strike]
                    strikes_otm = len(otm_strikes)
                    if not (otm_min <= strikes_otm <= otm_max):
                        continue
                nearest_candidates.append(c)
            if nearest_candidates:
                nearest_candidates.sort(key=lambda x: abs(abs(float(x.delta or 0.0)) - float(target_delta)))
                candidate_long_legs = [nearest_candidates[0]]
                self.last_selector_diagnostics.update({
                    "delta_relaxed_nearest": True,
                    "allow_nearest_long_outside_band": True,
                })
                log.warning(
                    "DEBIT_SPREAD_SELECTOR | delta_relaxed_nearest=true | option_type=%s | symbol=%s | "
                    "target_delta=%.2f | selected_delta=%.4f",
                    option_type,
                    contracts[0].symbol if contracts else "unknown",
                    float(target_delta),
                    float(candidate_long_legs[0].delta or 0.0),
                )

        if not candidate_long_legs:
            log.warning(
                f"No contracts found with delta in range [{delta_min:.2f}, {delta_max:.2f}] "
                f"and premium ladder strict={strict_premium_max:.2f}, soft={soft_premium_max:.2f}, rescue={rescue_premium_max:.2f} "
                f"(otm_range={otm_min}-{otm_max})"
            )
            self.last_selector_diagnostics.update({
                "failure_stage": "long_leg_candidates",
                "failure_reason": "no_long_leg_candidates",
                "delta_min": float(delta_min),
                "delta_max": float(delta_max),
                "otm_min": int(otm_min),
                "otm_max": int(otm_max),
                "strict_premium_max": float(strict_premium_max),
                "soft_premium_max": float(soft_premium_max),
                "rescue_premium_max": float(rescue_premium_max),
                "relax_delta_extra": float(relax_delta_extra),
                "allow_nearest_long_outside_band": bool(allow_nearest_long_outside_band),
            })
            return None
        self.last_selector_diagnostics.update({
            "selected_ladder": selected_ladder_label,
            "selected_premium_min": float(selected_premium_min),
            "selected_premium_max": float(selected_premium_max),
            "candidate_long_legs": int(len(candidate_long_legs)),
            "delta_min": float(delta_min),
            "delta_max": float(delta_max),
            "relax_delta_extra": float(relax_delta_extra),
            "allow_nearest_long_outside_band": bool(allow_nearest_long_outside_band),
            "otm_min": int(otm_min),
            "otm_max": int(otm_max),
        })
        if selected_ladder_label != "strict_gamma":
            log.warning(
                "DEBIT_SPREAD_SELECTOR | premium_ladder=%s | option_type=%s | symbol=%s | "
                "target_delta=%.2f | selected_candidates=%d | premium_range=[%.2f, %.2f]",
                selected_ladder_label,
                option_type,
                contracts[0].symbol if contracts else "unknown",
                target_delta,
                len(candidate_long_legs),
                selected_premium_min,
                selected_premium_max,
            )
        
        # Multi-factor optimization: Cheap gamma + Low decay + Peak gamma potential + Vega risk
        # Score = (gamma_score * gamma_weight) - (theta_score * theta_weight) - (vega_risk * vega_weight)
        # Higher score = better option
        
        def calculate_option_score(contract, target_delta):
            """Calculate multi-factor optimization score for option selection"""
            score = 0.0
            vega_w = 0.035 if _high_mom else 0.10

            # 1. Gamma Score (40% weight) - Buy cheap gamma, maximize peak gamma potential
            # Higher gamma = better, but we want cheap gamma (OTM options)
            gamma_score = abs(contract.gamma) if contract.gamma else 0.0
            # Normalize gamma (assume max gamma around 0.05-0.10 for OTM options)
            gamma_normalized = min(gamma_score / 0.10, 1.0) if gamma_score > 0 else 0.0
            score += gamma_normalized * 0.40
            
            # 2. Theta Score (30% weight) - Minimize decay (lower absolute theta = better)
            # Lower theta (less negative) = less decay = better
            theta_score = abs(contract.theta) if contract.theta else 0.0
            # Normalize theta (assume max theta around 0.20-0.30 for 0DTE OTM options)
            # Lower theta is better, so invert: (max_theta - theta) / max_theta
            theta_normalized = max(0.0, (0.30 - theta_score) / 0.30) if theta_score > 0 else 1.0
            score += theta_normalized * 0.30
            
            # 3. Delta Proximity (20% weight) - Closest to target delta
            delta_diff = abs(abs(contract.delta) - target_delta)
            delta_score = max(0.0, 1.0 - (delta_diff / 0.10))  # Within ±0.10 range
            score += delta_score * 0.20
            
            # 4. Vega Risk — down-weight on high-momentum path so explosive IV names are not over-penalized.
            vega_score = abs(contract.vega) if contract.vega else 0.0
            vega_normalized = max(0.0, (0.15 - vega_score) / 0.15) if vega_score > 0 else 1.0
            score += vega_normalized * vega_w
            
            return score
        
        # Sort by multi-factor optimization score (descending)
        # This optimizes for: cheap gamma + low decay + peak gamma potential + low vega risk
        candidate_long_legs.sort(
            key=lambda x: (
                -calculate_option_score(x, target_delta),  # Higher score first (negative for descending)
                abs(abs(x.delta) - target_delta)  # Then closest delta to target (tiebreaker)
            )
        )
        
        # Try to find valid debit spread (bounded tries + structured pairing audit)
        pairing_audit: List[Dict[str, Any]] = []
        long_try_n = 0
        for long_contract in candidate_long_legs:
            long_try_n += 1
            if long_try_n > 32:
                break
            long_strike = long_contract.strike
            step = self._get_strike_increment(long_strike)
            if option_type == 'call':
                base_tgt = long_strike + spread_width
            else:
                base_tgt = long_strike - spread_width
            strike_targets = [base_tgt]
            if _high_mom:
                if option_type == 'call':
                    strike_targets.extend(
                        [base_tgt + 0.5 * step, base_tgt - 0.5 * step, base_tgt + 1.0 * step]
                    )
                else:
                    strike_targets.extend(
                        [base_tgt - 0.5 * step, base_tgt + 0.5 * step, base_tgt - 1.0 * step]
                    )
            short_contract = None
            used_tgt = base_tgt
            for tgt in strike_targets:
                sc = self._pick_otm_short_leg(contracts, option_type, long_strike, float(tgt))
                if sc is not None:
                    short_contract = sc
                    used_tgt = float(tgt)
                    break
            if not short_contract:
                pairing_audit.append(
                    {
                        "long_strike": float(long_strike),
                        "long_delta": float(long_contract.delta or 0.0),
                        "targets_tried": [float(x) for x in strike_targets[:6]],
                        "failure": "short_leg_not_found",
                    }
                )
                continue
            
            # Validate both legs
            long_valid, long_reasons = self.validate_liquidity(long_contract)
            short_valid, short_reasons = self.validate_liquidity(short_contract)
            
            if not long_valid or not short_valid:
                log.debug(f"Invalid spread: long={long_valid}, short={short_valid}")
                pairing_audit.append(
                    {
                        "long_strike": float(long_strike),
                        "short_strike": float(getattr(short_contract, "strike", 0.0) or 0.0),
                        "target_short": float(used_tgt),
                        "long_ok": bool(long_valid),
                        "short_ok": bool(short_valid),
                        "long_reasons": list(long_reasons or [])[:2],
                        "short_reasons": list(short_reasons or [])[:2],
                        "failure": "liquidity_reject",
                    }
                )
                continue
            
            # Calculate spread metrics
            debit_cost = long_contract.ask - short_contract.bid
            max_profit = spread_width - debit_cost
            max_loss = debit_cost
            
            # Keep informational R:R here; final payoff guardrails are enforced in execution stage.
            if max_loss > 0:
                risk_reward_ratio = max_profit / max_loss
            else:
                risk_reward_ratio = 0.0
            
            if option_type == 'call':
                break_even = long_strike + debit_cost
            else:  # put
                break_even = long_strike - debit_cost
            
            short_strike = short_contract.strike
            spread = DebitSpread(
                symbol=long_contract.symbol,
                expiry=long_contract.expiry,
                option_type=option_type,
                long_strike=long_strike,
                short_strike=short_strike,
                long_contract=long_contract,
                short_contract=short_contract,
                debit_cost=debit_cost,
                max_profit=max_profit,
                max_loss=max_loss,
                break_even=break_even
            )
            
            # Calculate optimization score for logging
            opt_score = calculate_option_score(long_contract, target_delta)
            long_prem = long_contract.mid_price
            
            log.info(f"✅ Selected debit spread: {spread.symbol} {option_type} {spread.long_strike}/{spread.short_strike}")
            log.info(f"  Debit: ${debit_cost:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
            log.info(f"  Risk:Reward Ratio: {risk_reward_ratio:.2f}x (within {self.min_risk_reward_ratio:.1f}x-{self.max_risk_reward_ratio:.1f}x range ✅)")
            log.info(f"  Long Leg: 1-3 strikes OTM (delta: {long_contract.delta:.2f}, premium: ${long_contract.mid_price:.2f})")
            log.info(f"  Short Leg: R:R {risk_reward_ratio:.2f}x ✅")
            log.info(f"  Long Leg Greeks: Gamma={long_contract.gamma:.4f}, Theta={long_contract.theta:.4f}, Vega={long_contract.vega:.4f}, IV={long_contract.implied_volatility:.2%}")
            log.info(f"  Optimization Score: {opt_score:.3f} (Gamma:40%, Theta:30%, Delta:20%, Vega:10%)")
            log.info(
                f"  Rev 00348: Premium ${long_prem:.2f} in range "
                f"[${selected_premium_min:.2f}, ${selected_premium_max:.2f}] "
                f"(ladder={selected_ladder_label}) ✅"
            )
            self.last_selector_diagnostics.update({
                "selected": True,
                "failure_stage": None,
                "failure_reason": None,
                "selected_long_strike": float(long_strike),
                "selected_short_strike": float(short_strike),
                "selected_long_delta": float(long_contract.delta),
                "selected_short_delta": float(short_contract.delta),
                "selected_long_mid": float(long_contract.mid_price),
                "selected_short_mid": float(short_contract.mid_price),
                "debit_cost": float(debit_cost),
                "max_profit": float(max_profit),
                "max_loss": float(max_loss),
                "risk_reward_ratio": float(risk_reward_ratio),
                "pairing_attempts_logged": int(len(pairing_audit)),
            })
            
            return spread
        
        self.last_selector_diagnostics.update({
            "failure_stage": "pairing_or_liquidity",
            "failure_reason": "no_valid_spread_pair_found",
            "pairing_audit_sample": pairing_audit[:14],
            "pairing_attempts": int(len(pairing_audit)),
            "long_leg_candidates": int(len(candidate_long_legs)),
            "high_momentum_selector": bool(_high_mom),
        })
        log.warning(f"No valid debit spread found for {option_type} with delta {target_delta:.2f}")
        return None
    
    def select_single_leg_contract(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        profile: str = "lotto",
        symbol: str = "",
        adaptive_open_window: bool = False,
        momentum_score: float = 0.0,
    ) -> Optional[OptionContract]:
        """
        General single-leg selector used by lotto and directional long_call/long_put.

        Profile rules:
        - lotto: delta 0.12-0.18, premium 0.15-0.60, spread<=10%, volume>=200, OI>=500
        - directional: delta 0.28-0.40, premium 0.35-1.20, spread<=8%, volume>=200, OI>=500
        """
        contracts = chain.get(option_type + 's', [])
        sym0 = str(symbol or (contracts[0].symbol if contracts else "") or "").upper()
        self.last_selector_diagnostics = {
            "selector": "single_leg",
            "profile": str(profile or "lotto").strip().lower(),
            "symbol": sym0,
            "option_type": option_type,
            "spot": float(current_price),
            "contracts_this_side": int(len(contracts)),
            "momentum_score": float(momentum_score or 0.0),
            "selected": False,
        }
        if not contracts:
            self.last_selector_diagnostics.update(
                {"failure_stage": "no_contracts", "failure_reason": f"no_{option_type}_contracts"}
            )
            return None

        profile_key = str(profile or "lotto").strip().lower()
        if profile_key == "directional":
            delta_min, delta_max = 0.28, 0.40
            premium_min, premium_max = 0.35, 1.20
            try:
                max_spread_pct = float(os.getenv("0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT", "8.0"))
            except (TypeError, ValueError):
                max_spread_pct = 8.0
        else:
            profile_key = "lotto"
            delta_min, delta_max = 0.12, 0.18
            premium_min, premium_max = 0.15, 0.60
            max_spread_pct = 10.0
        target_delta = (delta_min + delta_max) / 2.0
        min_volume = int(self.single_leg_min_volume)
        min_open_interest = int(self.single_leg_min_open_interest)
        symbol_u = str(symbol or (contracts[0].symbol if contracts else "")).upper()
        if adaptive_open_window and symbol_u in self.single_leg_mega_symbols:
            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
            session_open = now_pt.replace(hour=7, minute=30, second=0, microsecond=0)
            minutes_since_open = max(0.0, (now_pt - session_open).total_seconds() / 60.0)
            if minutes_since_open <= self.single_leg_open_window_minutes:
                min_open_interest = max(1, int(round(min_open_interest * self.single_leg_open_window_oi_mult)))
                min_volume = max(1, int(round(min_volume * self.single_leg_open_window_volume_mult)))
                log.info(
                    "0DTE_SINGLE_LEG_ADAPTIVE_FLOORS | symbol=%s | minutes_since_open=%.2f | "
                    "min_volume=%d | min_open_interest=%d",
                    symbol_u,
                    minutes_since_open,
                    min_volume,
                    min_open_interest,
                )

        try:
            mom_relax_min = float(os.getenv("0DTE_SINGLE_LEG_MOMENTUM_RELAX_MIN", "72"))
        except (TypeError, ValueError):
            mom_relax_min = 72.0
        if float(momentum_score or 0.0) >= mom_relax_min:
            try:
                floor_oi = int(os.getenv("0DTE_SINGLE_LEG_MOMENTUM_FLOOR_OI", "100"))
            except (TypeError, ValueError):
                floor_oi = 100
            try:
                floor_vol = int(os.getenv("0DTE_SINGLE_LEG_MOMENTUM_FLOOR_VOL", "50"))
            except (TypeError, ValueError):
                floor_vol = 50
            min_open_interest = max(floor_oi, int(min_open_interest * 0.72))
            min_volume = max(floor_vol, int(min_volume * 0.72))
            if profile_key == "directional":
                try:
                    max_spread_pct = max(
                        float(max_spread_pct),
                        float(os.getenv("0DTE_SINGLE_LEG_MOMENTUM_MAX_SPREAD_PCT", "10.0")),
                    )
                except (TypeError, ValueError):
                    max_spread_pct = max(float(max_spread_pct), 10.0)
            log.info(
                "0DTE_SINGLE_LEG_MOMENTUM_RELAX | symbol=%s | profile=%s | momentum=%.1f | "
                "min_volume=%d | min_open_interest=%d | max_spread_pct=%.2f",
                symbol_u,
                profile_key,
                float(momentum_score or 0.0),
                min_volume,
                min_open_interest,
                float(max_spread_pct),
            )

        # Filter first: premium + liquidity + side.
        side_candidates: List[OptionContract] = []
        rej_bucket = {"premium": 0, "spread": 0, "volume": 0, "oi": 0, "side": 0}
        for c in contracts:
            if c.mid_price < premium_min or c.mid_price > premium_max:
                rej_bucket["premium"] += 1
                continue
            if c.bid_ask_spread_pct > max_spread_pct:
                rej_bucket["spread"] += 1
                continue
            if int(c.volume or 0) < min_volume:
                rej_bucket["volume"] += 1
                continue
            if int(c.open_interest or 0) < min_open_interest:
                rej_bucket["oi"] += 1
                continue
            if option_type == "call" and c.strike <= current_price:
                rej_bucket["side"] += 1
                continue
            if option_type == "put" and c.strike >= current_price:
                rej_bucket["side"] += 1
                continue
            side_candidates.append(c)

        # Then enforce target delta window.
        candidates = [c for c in side_candidates if delta_min <= abs(c.delta) <= delta_max]
        if not candidates:
            dmin = delta_min
            dmax = delta_max
            abs_deltas = [abs(float(x.delta or 0.0)) for x in side_candidates[:40]]
            self.last_selector_diagnostics.update(
                {
                    "failure_stage": "single_leg_no_match",
                    "failure_reason": "delta_band_empty" if side_candidates else "liquidity_or_premium_filters",
                    "side_candidates": int(len(side_candidates)),
                    "reject_bucket_counts": dict(rej_bucket),
                    "delta_window": [float(dmin), float(dmax)],
                    "sample_abs_deltas": [round(x, 4) for x in sorted(abs_deltas)[:12]],
                }
            )
            log.warning(
                "0DTE_SINGLE_LEG_FAIL_DETAIL | symbol=%s | profile=%s | side=%s | "
                "side_candidates=%d | reject_buckets=%s | delta_window=%.2f-%.2f",
                symbol_u,
                profile_key,
                option_type.upper(),
                int(len(side_candidates)),
                rej_bucket,
                float(delta_min),
                float(delta_max),
            )
            return None

        # Score contracts with gamma emphasized over theta.
        def _single_leg_score(contract: OptionContract) -> float:
            gamma_norm = min(abs(contract.gamma or 0.0) / 0.12, 1.0)
            theta_norm = max(0.0, (0.35 - abs(contract.theta or 0.0)) / 0.35)
            delta_norm = max(0.0, 1.0 - (abs(abs(contract.delta) - target_delta) / max(0.001, (delta_max - delta_min))))
            if option_type == "call":
                strike_gap = max(0.0, contract.strike - current_price)
            else:
                strike_gap = max(0.0, current_price - contract.strike)
            strike_norm = max(0.0, 1.0 - min(strike_gap / max(current_price * 0.02, 0.5), 1.0))
            return (gamma_norm * 0.50) + (theta_norm * 0.20) + (delta_norm * 0.20) + (strike_norm * 0.10)

        candidates.sort(
            key=lambda c: (
                -_single_leg_score(c),
                abs(c.strike - current_price),  # nearest valid strike preference
                abs(abs(c.delta) - target_delta),
            )
        )

        selected = candidates[0]
        log.info(
            "0DTE_SINGLE_LEG_SELECTED | symbol=%s | profile=%s | side=%s | strike=%.2f | delta=%.2f | premium=%.2f | spread_pct=%.2f",
            selected.symbol,
            profile_key,
            option_type.upper(),
            selected.strike,
            selected.delta,
            selected.mid_price,
            selected.bid_ask_spread_pct,
        )
        self.last_selector_diagnostics.update(
            {
                "selected": True,
                "failure_stage": None,
                "failure_reason": None,
                "strike": float(selected.strike),
                "delta": float(selected.delta or 0.0),
                "mid": float(selected.mid_price),
                "spread_pct": float(selected.bid_ask_spread_pct),
            }
        )
        return selected

    def select_lotto_strike(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        target_delta: float = 0.15,  # Lower delta for lotto
        symbol: str = "",
        adaptive_open_window: bool = False,
        momentum_score: float = 0.0,
    ) -> Optional[OptionContract]:
        """
        Select strike for lotto sleeve (single-leg option)
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            current_price: Current underlying price
            target_delta: Target delta (lower for lotto)
            
        Returns:
            OptionContract or None if no valid contract found
        """
        return self.select_single_leg_contract(
            chain=chain,
            option_type=option_type,
            current_price=current_price,
            profile="directional" if float(target_delta or 0.0) >= 0.24 else "lotto",
            symbol=symbol,
            adaptive_open_window=adaptive_open_window,
            momentum_score=float(momentum_score or 0.0),
        )
    
    def _pick_otm_short_leg(
        self,
        contracts: List[OptionContract],
        option_type: str,
        long_strike: float,
        target_short_strike: float,
    ) -> Optional[OptionContract]:
        """
        Nearest listed strike to target on the OTM side of the long leg.

        Rev 00309: Previous logic required |strike - target| < 0.5 * increment, which
        almost never matches when the chain is spaced by full increments (e.g. DEMO
        synthetic step 5.0 with target long+7.5). That produced None for ITM probability
        and often for momentum scalper despite a valid chain.
        """
        step = max(self._get_strike_increment(long_strike), 0.01)
        if option_type == 'call':
            otm = [c for c in contracts if c.strike > long_strike + 1e-9]
        else:
            otm = [c for c in contracts if c.strike < long_strike - 1e-9]
        if not otm:
            return None
        best = min(otm, key=lambda c: abs(c.strike - target_short_strike))
        try:
            step_mult = float(os.getenv("0DTE_DEBIT_SHORT_LEG_STEP_MULT", "6.0"))
        except (TypeError, ValueError):
            step_mult = 6.0
        step_mult = max(3.5, min(10.0, step_mult))
        if abs(best.strike - target_short_strike) > step_mult * step:
            return None
        return best
    
    def select_atm_momentum_scalper(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        strikes_otm: int = 1  # 1-2 strikes OTM for quick payoff
    ) -> Optional[DebitSpread]:
        """
        Select ATM Debit Spread for Momentum Scalper (Rev 00227: Level 2 Strategy)
        
        Use when: Expect quick 5-15 min expansion
        Structure: Buy ATM option, Sell 1-2 strikes out
        Why: Cheap entry, fast payoff, high % ROI potential
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            current_price: Current underlying price
            strikes_otm: Number of strikes OTM for short leg (1-2)
        
        Returns:
            DebitSpread or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        
        if not contracts:
            return None
        
        # Find ATM or slightly ITM option for long leg.
        # Rev 00316: use tiered delta fallback instead of a single strict 0.30-0.50
        # band so fast-moving 0DTE chains still produce an executable spread.
        if option_type == 'call':
            directional_candidates = [c for c in contracts if c.strike <= current_price * 1.01]
        else:  # put
            directional_candidates = [c for c in contracts if c.strike >= current_price * 0.99]

        if not directional_candidates:
            return None

        valid_delta_candidates = [
            c for c in directional_candidates
            if c.delta is not None and math.isfinite(float(c.delta))
        ]
        if not valid_delta_candidates:
            return None

        primary_band = [c for c in valid_delta_candidates if 0.30 <= abs(float(c.delta)) <= 0.50]
        relaxed_band = [c for c in valid_delta_candidates if 0.20 <= abs(float(c.delta)) <= 0.60]
        if primary_band:
            long_candidates = primary_band
        elif relaxed_band:
            long_candidates = relaxed_band
        else:
            # Last resort: closest directional contract to ATM target delta.
            long_candidates = list(valid_delta_candidates)

        # Sort by delta proximity to 0.40 (ideal ATM-ish delta)
        long_candidates.sort(key=lambda x: abs(abs(float(x.delta)) - 0.40))
        
        for long_contract in long_candidates[:5]:  # Try top 5 candidates
            # Find short leg: strikes_otm strikes away
            step = self._get_strike_increment(long_contract.strike)
            if option_type == 'call':
                target_short_strike = long_contract.strike + (strikes_otm * step)
            else:  # put
                target_short_strike = long_contract.strike - (strikes_otm * step)
            
            short_contract = self._pick_otm_short_leg(
                contracts, option_type, long_contract.strike, target_short_strike
            )
            if short_contract is None:
                continue
            
            # Validate liquidity
            long_valid, _ = self.validate_liquidity(long_contract)
            short_valid, _ = self.validate_liquidity(short_contract)
            
            if long_valid and short_valid:
                # Calculate spread details
                spread_width = abs(long_contract.strike - short_contract.strike)
                debit_cost = long_contract.mid_price - short_contract.mid_price
                max_profit = spread_width - debit_cost
                max_loss = debit_cost
                break_even = long_contract.strike + debit_cost if option_type == 'call' else long_contract.strike - debit_cost
                
                spread = DebitSpread(
                    symbol=long_contract.symbol,
                    expiry=long_contract.expiry,
                    option_type=option_type,
                    long_strike=long_contract.strike,
                    short_strike=short_contract.strike,
                    long_contract=long_contract,
                    short_contract=short_contract,
                    debit_cost=debit_cost,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    break_even=break_even
                )
                
                log.info(f"✅ Selected ATM Momentum Scalper: {spread.symbol} {option_type} {spread.long_strike}/{spread.short_strike}")
                log.info(f"   - Debit: ${debit_cost:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
                return spread
        
        return None
    
    def select_itm_probability_spread(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        current_price: float,
        target_delta: float = 0.65  # Deeper ITM (0.60-0.70 delta)
    ) -> Optional[DebitSpread]:
        """
        Select ITM Probability Spread (Rev 00229: Easy Mode Strategy)
        
        Use when: Market is valid but not explosive
        Structure: Buy deeper ITM option (Δ 0.60-0.70), Sell OTM option
        Why: Lower breakeven, higher probability, less dependent on speed
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            current_price: Current underlying price
            target_delta: Target delta for long leg (0.60-0.70 for ITM)
        
        Returns:
            DebitSpread or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        symbol = contracts[0].symbol if contracts else "unknown"
        self.last_selector_diagnostics = {
            "selector": "itm_probability_spread",
            "option_type": option_type,
            "target_delta": float(target_delta),
            "spot": float(current_price),
            "contracts_this_side": int(len(contracts)),
            "selected": False,
        }
        
        if not contracts:
            self.last_selector_diagnostics.update({
                "failure_stage": "no_contracts",
                "failure_reason": f"no_{option_type}_contracts",
            })
            return None

        # Direction-aware ITM classification (explicit):
        # CALL ITM => strike < spot
        # PUT  ITM => strike > spot
        itm_contracts = self._itm_contracts(contracts, option_type, current_price)
        if not itm_contracts:
            self.last_selector_diagnostics.update({
                "failure_stage": "no_itm_contracts",
                "failure_reason": "zero_itm_contracts_for_side",
                "itm_contracts": 0,
                "total_contracts": int(len(contracts)),
            })
            log.warning(
                "ITM_SPREAD_SELECTOR | symbol=%s | option_type=%s | spot=%.2f | total_strikes=%d | itm_strikes=0 | candidate_spreads=0 | failure=no_itm_contracts",
                symbol,
                option_type,
                float(current_price),
                len(contracts),
            )
            return None
        
        # Find deeper ITM option for long leg with tiered fallback.
        # Rev 00316+: strict -> relaxed -> wide delta bands to reduce false negatives.
        directional_candidates = itm_contracts

        valid_delta_candidates = [
            c for c in directional_candidates
            if c.delta is not None and math.isfinite(float(c.delta))
        ]
        band_candidates = [
            ("primary", 0.60, 0.70),
            ("relaxed", 0.50, 0.80),
            ("wide_relaxed", 0.35, 0.90),
        ]
        long_candidates = []
        band_label = "nearest"
        band_min, band_max = None, None
        for lbl, bmin, bmax in band_candidates:
            in_band = [c for c in valid_delta_candidates if bmin <= abs(float(c.delta)) <= bmax]
            if in_band:
                long_candidates = in_band
                band_label = lbl
                band_min, band_max = bmin, bmax
                break
        if not long_candidates:
            long_candidates = list(valid_delta_candidates)

        if not long_candidates:
            self.last_selector_diagnostics.update({
                "failure_stage": "long_leg_unavailable",
                "failure_reason": "no_itm_long_leg_in_delta_band",
                "long_candidates": 0,
                "delta_min": 0.60,
                "delta_max": 0.70,
            })
            log.warning(
                "ITM_SPREAD_SELECTOR | symbol=%s | option_type=%s | failure=long_leg_unavailable | "
                "contracts=%d | spot=%.2f | delta_band=[0.60,0.70]",
                contracts[0].symbol if contracts else "unknown",
                option_type,
                len(contracts),
                float(current_price),
            )
            return None
        
        # Sort by delta proximity to target (~0.65 ITM probability profile).
        long_candidates.sort(key=lambda x: abs(abs(float(x.delta)) - target_delta))
        self.last_selector_diagnostics.update({
            "long_candidates": int(len(long_candidates)),
            "delta_band_mode": band_label,
            "delta_min": band_min,
            "delta_max": band_max,
        })

        short_leg_missing_count = 0
        long_liquidity_fail_count = 0
        short_liquidity_fail_count = 0
        candidate_spreads_built = 0
        long_liquidity_fail_reasons: List[str] = []
        short_liquidity_fail_reasons: List[str] = []
        
        for long_contract in long_candidates[:8]:
            step = self._get_strike_increment(long_contract.strike)
            short_contract = None
            for otm_steps in (3, 2, 4, 5):
                if option_type == 'call':
                    target_short_strike = long_contract.strike + (otm_steps * step)
                else:  # put
                    target_short_strike = long_contract.strike - (otm_steps * step)
                short_contract = self._pick_otm_short_leg(
                    contracts, option_type, long_contract.strike, target_short_strike
                )
                if short_contract is not None:
                    break
            if short_contract is None:
                short_leg_missing_count += 1
                continue
            candidate_spreads_built += 1
            
            # Validate liquidity
            long_valid, long_reasons = self.validate_liquidity(long_contract)
            short_valid, short_reasons = self.validate_liquidity(short_contract)
            if not long_valid:
                long_liquidity_fail_count += 1
                if long_reasons and len(long_liquidity_fail_reasons) < 3:
                    long_liquidity_fail_reasons.append("; ".join(long_reasons))
            if not short_valid:
                short_liquidity_fail_count += 1
                if short_reasons and len(short_liquidity_fail_reasons) < 3:
                    short_liquidity_fail_reasons.append("; ".join(short_reasons))
            
            if long_valid and short_valid:
                # Calculate spread details
                spread_width = abs(long_contract.strike - short_contract.strike)
                debit_cost = long_contract.mid_price - short_contract.mid_price
                max_profit = spread_width - debit_cost
                max_loss = debit_cost
                break_even = long_contract.strike + debit_cost if option_type == 'call' else long_contract.strike - debit_cost
                
                spread = DebitSpread(
                    symbol=long_contract.symbol,
                    expiry=long_contract.expiry,
                    option_type=option_type,
                    long_strike=long_contract.strike,
                    short_strike=short_contract.strike,
                    long_contract=long_contract,
                    short_contract=short_contract,
                    debit_cost=debit_cost,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    break_even=break_even
                )
                
                log.info(f"✅ Selected ITM Probability Spread: {spread.symbol} {option_type} {spread.long_strike}/{spread.short_strike}")
                log.info(f"   - Debit: ${debit_cost:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
                log.info(f"   - Long Leg Delta: {long_contract.delta:.2f} (ITM), Higher Probability")
                self.last_selector_diagnostics.update({
                    "selected": True,
                    "failure_stage": None,
                    "failure_reason": None,
                    "selected_long_strike": float(long_contract.strike),
                    "selected_short_strike": float(short_contract.strike),
                    "selected_long_delta": float(long_contract.delta),
                    "selected_short_delta": float(short_contract.delta),
                    "selected_long_mid": float(long_contract.mid_price),
                    "selected_short_mid": float(short_contract.mid_price),
                    "debit_cost": float(debit_cost),
                    "max_profit": float(max_profit),
                    "max_loss": float(max_loss),
                    "candidate_spreads_built": int(candidate_spreads_built),
                    "itm_contracts": int(len(itm_contracts)),
                    "total_contracts": int(len(contracts)),
                })
                log.info(
                    "ITM_SPREAD_SELECTOR | symbol=%s | direction=%s | spot=%.2f | total_strikes=%d | itm_strikes=%d | candidate_spreads=%d | selected=true | band=%s",
                    symbol,
                    option_type.upper(),
                    float(current_price),
                    len(contracts),
                    len(itm_contracts),
                    candidate_spreads_built,
                    band_label,
                )
                return spread

        failure_stage = "pairing_or_liquidity"
        failure_reason = "no_valid_itm_spread_pair_found"
        if short_leg_missing_count > 0 and long_liquidity_fail_count == 0 and short_liquidity_fail_count == 0:
            failure_stage = "short_leg_unavailable"
            failure_reason = "no_otm_short_leg_for_itm_long_candidate"
        elif long_liquidity_fail_count > 0 and short_liquidity_fail_count == 0:
            failure_stage = "long_leg_liquidity"
            failure_reason = "itm_long_leg_failed_liquidity"
        elif short_liquidity_fail_count > 0 and long_liquidity_fail_count == 0:
            failure_stage = "short_leg_liquidity"
            failure_reason = "otm_short_leg_failed_liquidity"

        self.last_selector_diagnostics.update({
            "failure_stage": failure_stage,
            "failure_reason": failure_reason,
            "short_leg_missing_count": int(short_leg_missing_count),
            "long_liquidity_fail_count": int(long_liquidity_fail_count),
            "short_liquidity_fail_count": int(short_liquidity_fail_count),
            "long_liquidity_fail_examples": long_liquidity_fail_reasons,
            "short_liquidity_fail_examples": short_liquidity_fail_reasons,
            "candidate_spreads_built": int(candidate_spreads_built),
            "itm_contracts": int(len(itm_contracts)),
            "total_contracts": int(len(contracts)),
        })
        log.warning(
            "ITM_SPREAD_SELECTOR | symbol=%s | direction=%s | spot=%.2f | total_strikes=%d | itm_strikes=%d | candidate_spreads=%d | failure_stage=%s | "
            "long_candidates=%d | short_leg_missing=%d | long_liq_fail=%d | short_liq_fail=%d",
            symbol,
            option_type,
            float(current_price),
            len(contracts),
            len(itm_contracts),
            candidate_spreads_built,
            failure_stage,
            len(long_candidates),
            short_leg_missing_count,
            long_liquidity_fail_count,
            short_liquidity_fail_count,
        )
        return None
    
    def _get_strike_increment(self, strike: float) -> float:
        """Get strike increment based on strike price"""
        if strike < 50:
            return 0.5
        elif strike < 200:
            return 1.0
        elif strike < 500:
            return 2.5
        else:
            return 5.0
    
    def select_credit_spread_strikes(
        self,
        chain: Dict[str, List[OptionContract]],
        option_type: str,
        target_delta: float,
        spread_width: float,
        current_price: float
    ) -> Optional[CreditSpread]:
        """
        Select optimal strikes for credit spread - Rev 00212
        
        Contract Selection Logic (Best Practices):
        - Premium: $0.20-$0.60 (cheap contracts for gamma explosion)
        - Delta: 10-30 (0.10-0.30) - cheap gamma that can explode
        - Strike: 1-3 strikes OTM (out of the money)
        - Why: Cheap contracts give gamma explosion, easy 5-20x with small moves
        
        For credit spreads:
        - CALL credit spread: Sell call at lower strike, buy call at higher strike (bearish)
        - PUT credit spread: Sell put at higher strike, buy put at lower strike (bullish)
        
        Args:
            chain: Options chain dictionary
            option_type: 'call' or 'put'
            target_delta: Target delta for short leg (0.10-0.30) - Rev 00212
            spread_width: Spread width ($1 or $2 for QQQ/SPY, $5 or $10 for SPX)
            current_price: Current underlying price
            
        Returns:
            CreditSpread object or None if no valid spread found
        """
        contracts = chain.get(option_type + 's', [])
        
        if not contracts:
            log.warning(f"No {option_type} contracts available")
            return None
        
        # Rev 00212: Filter contracts by delta range (target ± 0.05 for tighter range)
        delta_min = max(0.10, target_delta - 0.05)  # Minimum 10 delta
        delta_max = min(0.30, target_delta + 0.05)  # Maximum 30 delta
        
        # Rev 00238: Filter by premium range $0.15-$0.60 (cheap contracts for gamma explosion)
        # Lowered minimum from $0.20 to $0.15 to allow $0.19 entries (like successful QQQ trades)
        premium_min = 0.15
        premium_max = 0.60
        
        # Get all strikes sorted for OTM calculation
        all_strikes = sorted(set(c.strike for c in contracts))
        
        # Rev 00212: Filter by strike position (1-3 strikes OTM) and premium
        candidate_short_legs = []
        for c in contracts:
            # Check delta range
            if not (delta_min <= abs(c.delta) <= delta_max):
                continue
            
            # Check premium range (use mid price)
            premium = c.mid_price
            if not (premium_min <= premium <= premium_max):
                continue
            
            # Check strike position (1-3 strikes OTM) - for credit spreads, short leg should be OTM
            if option_type == 'call':
                # CALL credit: Short call should be OTM (strike > current_price)
                if c.strike <= current_price:
                    continue  # Not OTM
                strikes_otm = sum(1 for s in all_strikes if current_price < s <= c.strike)
                if not (1 <= strikes_otm <= 3):
                    continue
            else:  # put
                # PUT credit: Short put should be OTM (strike < current_price)
                if c.strike >= current_price:
                    continue  # Not OTM
                strikes_otm = sum(1 for s in all_strikes if current_price > s >= c.strike)
                if not (1 <= strikes_otm <= 3):
                    continue
            
            candidate_short_legs.append(c)
        
        if not candidate_short_legs:
            log.warning(f"No contracts found with delta in range [{delta_min:.2f}, {delta_max:.2f}]")
            return None
        
        # For credit spreads: Consider gamma (for liquidity), but prioritize low vega risk
        # Credit spreads have negative gamma, so we accept that but minimize vega risk
        def calculate_credit_score(contract, target_delta):
            """Calculate optimization score for credit spread short leg"""
            score = 0.0
            
            # 1. Delta Proximity (40% weight) - Closest to target delta
            delta_diff = abs(abs(contract.delta) - target_delta)
            delta_score = max(0.0, 1.0 - (delta_diff / 0.10))
            score += delta_score * 0.40
            
            # 2. Vega Risk (30% weight) - Minimize vega risk (lower vega = less IV sensitivity)
            # For credit spreads, lower vega is better (less sensitive to IV changes)
            vega_score = abs(contract.vega) if contract.vega else 0.0
            vega_normalized = max(0.0, (0.15 - vega_score) / 0.15) if vega_score > 0 else 1.0
            score += vega_normalized * 0.30
            
            # 3. Gamma (20% weight) - Higher gamma for liquidity/spread quality
            # Accept negative gamma exposure, but use for liquidity selection
            gamma_score = abs(contract.gamma) if contract.gamma else 0.0
            gamma_normalized = min(gamma_score / 0.10, 1.0) if gamma_score > 0 else 0.0
            score += gamma_normalized * 0.20
            
            # 4. Theta Benefit (10% weight) - Higher theta benefits credit spreads (time decay helps)
            # For credit spreads, higher theta (more negative) is actually beneficial
            theta_score = abs(contract.theta) if contract.theta else 0.0
            theta_normalized = min(theta_score / 0.30, 1.0) if theta_score > 0 else 0.0
            score += theta_normalized * 0.10
            
            return score
        
        # Sort by credit spread optimization score (descending)
        candidate_short_legs.sort(
            key=lambda x: (
                -calculate_credit_score(x, target_delta),  # Higher score first
                abs(abs(x.delta) - target_delta)  # Then closest delta to target
            )
        )
        
        # Try to find valid credit spread
        for short_contract in candidate_short_legs:
            short_strike = short_contract.strike
            
            # Find long leg (spread_width away)
            if option_type == 'call':
                # CALL credit spread: Short at lower strike, long at higher strike
                long_strike = short_strike + spread_width
            else:  # put
                # PUT credit spread: Short at higher strike, long at lower strike
                long_strike = short_strike - spread_width
            
            # Find long leg contract
            long_contract = next(
                (c for c in contracts if c.strike == long_strike),
                None
            )
            
            if not long_contract:
                continue
            
            # Validate both legs
            short_valid, short_reasons = self.validate_liquidity(short_contract)
            long_valid, long_reasons = self.validate_liquidity(long_contract)
            
            if not short_valid or not long_valid:
                log.debug(f"Invalid credit spread: short={short_valid}, long={long_valid}")
                continue
            
            # Calculate spread metrics
            # Credit spread: Receive premium from short leg, pay premium for long leg
            credit_received = short_contract.bid - long_contract.ask
            
            # Ensure we receive a credit (positive net credit)
            if credit_received <= 0:
                log.debug(f"Invalid credit spread: credit_received ${credit_received:.2f} <= 0")
                continue
            
            max_profit = credit_received  # Max profit = credit received
            max_loss = spread_width - credit_received  # Max loss = spread width - credit
            
            # Deterministic Rule: Short leg at R:R ≥ 1.5–2.5x
            if max_loss > 0:
                risk_reward_ratio = max_profit / max_loss
            else:
                risk_reward_ratio = 0.0
            
            # Reject if R:R not in range 1.5-2.5x
            if risk_reward_ratio < self.min_risk_reward_ratio or risk_reward_ratio > self.max_risk_reward_ratio:
                log.debug(f"Rejecting credit spread: R:R {risk_reward_ratio:.2f}x not in range [{self.min_risk_reward_ratio:.1f}x, {self.max_risk_reward_ratio:.1f}x]")
                continue
            
            if option_type == 'call':
                # CALL credit spread: Break-even = short_strike + credit_received
                break_even = short_strike + credit_received
            else:  # put
                # PUT credit spread: Break-even = short_strike - credit_received
                break_even = short_strike - credit_received
            
            spread = CreditSpread(
                symbol=short_contract.symbol,
                expiry=short_contract.expiry,
                option_type=option_type,
                short_strike=short_strike,
                long_strike=long_strike,
                short_contract=short_contract,
                long_contract=long_contract,
                credit_received=credit_received,
                max_profit=max_profit,
                max_loss=max_loss,
                break_even=break_even
            )
            
            credit_score = calculate_credit_score(short_contract, target_delta)
            log.info(f"Selected credit spread: {spread.symbol} {option_type} {spread.short_strike}/{spread.long_strike}")
            log.info(f"  Credit: ${credit_received:.2f}, Max Profit: ${max_profit:.2f}, Max Loss: ${max_loss:.2f}")
            log.info(f"  Short Leg Greeks: Gamma={short_contract.gamma:.4f}, Theta={short_contract.theta:.4f}, Vega={short_contract.vega:.4f}, IV={short_contract.implied_volatility:.2%}")
            log.info(f"  Optimization Score: {credit_score:.3f} (Delta:40%, Vega:30%, Gamma:20%, Theta:10%)")
            
            return spread
        
        log.warning(f"No valid credit spread found for {option_type} with delta {target_delta:.2f}")
        return None

