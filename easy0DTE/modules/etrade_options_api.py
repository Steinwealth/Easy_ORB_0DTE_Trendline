#!/usr/bin/env python3
"""
ETrade Options API Integration
===============================

ETrade API integration for options trading:
- Options chain fetching
- Options order placement (debit spreads, single-leg)
- Options position tracking

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import logging
import os
import time
import re
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass

# Import ETrade trading module from ORB Strategy (parent of easy0DTE/)
try:
    import sys

    _modules_dir = os.path.dirname(os.path.abspath(__file__))
    _orb_strategy_root = os.path.dirname(os.path.dirname(_modules_dir))
    if os.path.isdir(_orb_strategy_root) and _orb_strategy_root not in sys.path:
        sys.path.insert(0, _orb_strategy_root)
    from modules.prime_etrade_trading import PrimeETradeTrading

    ETRADE_AVAILABLE = True
except ImportError:
    # Define a dummy class for type hints when ETrade is not available
    class PrimeETradeTrading:
        pass

    ETRADE_AVAILABLE = False
    logging.warning("ETrade trading module not available - will use mock/demo mode")

try:
    from modules.execution_telemetry import build_execution_payload, log_execution_event, slippage_vs_mid
    from modules.execution_routing import smart_execution_enabled, last_look_option_spread_ok
    from modules.execution_profiles import resolve_execution_profile
except ImportError:
    build_execution_payload = None  # type: ignore
    log_execution_event = None  # type: ignore
    slippage_vs_mid = None  # type: ignore
    smart_execution_enabled = lambda: False  # type: ignore
    last_look_option_spread_ok = lambda b, a, m: (True, 0.0)  # type: ignore
    resolve_execution_profile = None  # type: ignore


log = logging.getLogger(__name__)


@dataclass
class ETradeOptionContract:
    """ETrade Option Contract data"""
    symbol: str
    strike: float
    expiry: str
    option_type: str  # 'CALL' or 'PUT'
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price"""
        return (self.bid + self.ask) / 2.0 if self.bid > 0 and self.ask > 0 else 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'strike': self.strike,
            'expiry': self.expiry,
            'option_type': self.option_type.lower(),
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
            'mid_price': self.mid_price
        }


class ETradeOptionsAPI:
    """
    ETrade Options API Integration
    
    Handles options chain fetching and options order placement via ETrade API.
    """
    
    def __init__(
        self, 
        etrade_trading: Optional[PrimeETradeTrading] = None, 
        environment: str = 'prod',
        account_id: Optional[str] = None,
        secret_name: Optional[str] = None
    ):
        """
        Initialize ETrade Options API
        
        Args:
            etrade_trading: PrimeETradeTrading instance (optional, will create if None)
            environment: 'prod' only (sandbox deprecated)
            account_id: Specific account ID to use (optional, for separate 0DTE account)
            secret_name: Secret Manager name for OAuth tokens (optional, for separate account)
                         Default: 'etrade-oauth-prod' (sandbox deprecated; production tokens only)
        """
        self.environment = environment
        self.account_id = account_id  # Rev 00218: Support separate account selection
        
        if etrade_trading:
            self.etrade = etrade_trading
            # Rev 00218: Select specific account if provided
            if account_id and hasattr(self.etrade, 'select_account'):
                if self.etrade.select_account(account_id):
                    log.info(f"✅ Selected 0DTE Strategy account: {account_id}")
                else:
                    log.warning(f"⚠️ Failed to select account {account_id}, using default account")
        elif ETRADE_AVAILABLE:
            try:
                # Rev 00218: Support separate OAuth tokens for 0DTE Strategy
                # If secret_name is provided, we need to create a custom PrimeETradeTrading instance
                # For now, create standard instance - account selection happens after initialization
                self.etrade = PrimeETradeTrading(environment=environment)
                if not self.etrade.initialize():
                    log.error("Failed to initialize ETrade trading system")
                    self.etrade = None
                else:
                    # Rev 00218: Select specific account if provided
                    if account_id and hasattr(self.etrade, 'select_account'):
                        if self.etrade.select_account(account_id):
                            log.info(f"✅ Selected 0DTE Strategy account: {account_id}")
                        else:
                            log.warning(f"⚠️ Account {account_id} not found, using default account")
                            log.info(f"   Available accounts: {[acc.account_id for acc in self.etrade.accounts]}")
            except Exception as e:
                log.error(f"Failed to create ETrade trading instance: {e}")
                self.etrade = None
        else:
            self.etrade = None
            log.warning("ETrade Options API not available - ETrade module not found")
    
    def is_available(self) -> bool:
        """Check if ETrade API is available"""
        return self.etrade is not None and self.etrade.is_authenticated()
    
    async def fetch_options_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,
        strike_count: int = 20,
        include_greeks: bool = True,
        underlying_price: Optional[float] = None,
    ) -> Dict[str, List[ETradeOptionContract]]:
        """
        Fetch options chain from ETrade API
        
        Args:
            symbol: Underlying symbol (QQQ or SPY)
            expiry: Expiry date (YYYYMMDD format, None = 0DTE)
            strike_count: Number of strikes above/below ATM
            include_greeks: Include Greeks in response
            
        Returns:
            Dictionary with 'calls' and 'puts' lists
        """
        if not self.is_available():
            log.error("ETrade API not available for options chain fetch")
            return {'calls': [], 'puts': []}
        
        try:
            # Use US/Eastern calendar date for 0DTE (avoid UTC/server-local wrong day)
            if expiry is None:
                from zoneinfo import ZoneInfo as _Zi
                expiry = datetime.now(_Zi('America/New_York')).strftime('%Y%m%d')
            
            log.info(f"📊 Fetching options chain for {symbol} expiry {expiry}")
            
            # ETrade API endpoint: /v1/market/optionchains
            # Uses same OAuth authentication as ETF endpoints via _make_etrade_api_call()
            
            calls: List[ETradeOptionContract] = []
            puts: List[ETradeOptionContract] = []
            raw_option_pair_count = 0
            missing_or_zero_strike_count = 0
            strike_source_counts: Dict[str, int] = {}

            def _parse_chain_response(response_obj: Any) -> None:
                nonlocal raw_option_pair_count, missing_or_zero_strike_count
                if not isinstance(response_obj, dict) or 'OptionChainResponse' not in response_obj:
                    return
                chain_data = response_obj.get('OptionChainResponse') or {}
                option_pairs = chain_data.get('OptionPair', [])
                if not isinstance(option_pairs, list):
                    return
                raw_option_pair_count += len(option_pairs)

                def _coerce_strike(value: Any) -> Optional[float]:
                    try:
                        strike_val = float(value)
                    except (TypeError, ValueError):
                        return None
                    if strike_val <= 0:
                        return None
                    return strike_val

                def _extract_strike_from_symbol_text(value: Any) -> Optional[float]:
                    """
                    Best-effort strike extraction from contract text fields.
                    Common formats include:
                    - "... 250.0 Call"
                    - "... $250.00 Put"
                    """
                    if value is None:
                        return None
                    text = str(value).strip()
                    if not text:
                        return None
                    # Prefer dollar-denominated strike if present.
                    dollar_match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", text)
                    if dollar_match:
                        return _coerce_strike(dollar_match.group(1))
                    # Fallback: number immediately before option side keyword.
                    side_match = re.search(
                        r"([0-9]+(?:\.[0-9]+)?)\s*(?:CALL|PUT)\b",
                        text.upper(),
                    )
                    if side_match:
                        return _coerce_strike(side_match.group(1))
                    return None

                for pair in option_pairs:
                    if not isinstance(pair, dict):
                        continue
                    call_data = pair.get('Call', {}) if isinstance(pair.get('Call'), dict) else {}
                    put_data = pair.get('Put', {}) if isinstance(pair.get('Put'), dict) else {}
                    call_product = call_data.get('Product', {}) if isinstance(call_data.get('Product'), dict) else {}
                    put_product = put_data.get('Product', {}) if isinstance(put_data.get('Product'), dict) else {}
                    strike_candidates = [
                        ("pair.strikePrice", pair.get('strikePrice')),
                        ("pair.strike", pair.get('strike')),
                        ("call.strikePrice", call_data.get('strikePrice')),
                        ("call.strike", call_data.get('strike')),
                        ("call.product.strikePrice", call_product.get('strikePrice')),
                        ("call.product.strike", call_product.get('strike')),
                        ("call.product.callPut", call_product.get('callPut')),
                        ("call.product.securitySubType", call_product.get('securitySubType')),
                        ("call.product.symbol", call_product.get('symbol')),
                        ("call.product.displaySymbol", call_product.get('displaySymbol')),
                        ("call.displaySymbol", call_data.get('displaySymbol')),
                        ("call.symbol", call_data.get('symbol')),
                        ("put.strikePrice", put_data.get('strikePrice')),
                        ("put.strike", put_data.get('strike')),
                        ("put.product.strikePrice", put_product.get('strikePrice')),
                        ("put.product.strike", put_product.get('strike')),
                        ("put.product.callPut", put_product.get('callPut')),
                        ("put.product.securitySubType", put_product.get('securitySubType')),
                        ("put.product.symbol", put_product.get('symbol')),
                        ("put.product.displaySymbol", put_product.get('displaySymbol')),
                        ("put.displaySymbol", put_data.get('displaySymbol')),
                        ("put.symbol", put_data.get('symbol')),
                    ]
                    strike = 0.0
                    strike_source = "none"
                    for candidate_name, candidate_value in strike_candidates:
                        parsed = _coerce_strike(candidate_value)
                        if parsed is None:
                            parsed = _extract_strike_from_symbol_text(candidate_value)
                            if parsed is not None:
                                strike_source = f"{candidate_name}:text_extract"
                        if parsed is not None:
                            strike = parsed
                            if strike_source == "none":
                                strike_source = candidate_name
                            break
                    strike_source_counts[strike_source] = int(strike_source_counts.get(strike_source, 0)) + 1
                    if strike <= 0.0:
                        missing_or_zero_strike_count += 1
                        log.warning(
                            "0DTE_CHAIN_PARSE_WARN | symbol=%s | expiry=%s | reason=missing_or_zero_strike | "
                            "pair_keys=%s | call_keys=%s | put_keys=%s",
                            symbol,
                            expiry,
                            ",".join(sorted([str(k) for k in pair.keys()])),
                            ",".join(sorted([str(k) for k in call_data.keys()])),
                            ",".join(sorted([str(k) for k in put_data.keys()])),
                        )

                    if call_data:
                        calls.append(ETradeOptionContract(
                            symbol=symbol,
                            strike=strike,
                            expiry=expiry,
                            option_type='CALL',
                            bid=float(call_data.get('bid', 0) or 0),
                            ask=float(call_data.get('ask', 0) or 0),
                            last=float(call_data.get('lastPrice', 0) or 0),
                            volume=int(call_data.get('volume', 0) or 0),
                            open_interest=int(call_data.get('openInterest', 0) or 0),
                            delta=float(call_data.get('OptionGreeks', {}).get('delta', 0)) if call_data.get('OptionGreeks') else None,
                            gamma=float(call_data.get('OptionGreeks', {}).get('gamma', 0)) if call_data.get('OptionGreeks') else None,
                            theta=float(call_data.get('OptionGreeks', {}).get('theta', 0)) if call_data.get('OptionGreeks') else None,
                            vega=float(call_data.get('OptionGreeks', {}).get('vega', 0)) if call_data.get('OptionGreeks') else None,
                            implied_volatility=float(call_data.get('OptionGreeks', {}).get('iv', 0)) if call_data.get('OptionGreeks') else None
                        ))

                    if put_data:
                        puts.append(ETradeOptionContract(
                            symbol=symbol,
                            strike=strike,
                            expiry=expiry,
                            option_type='PUT',
                            bid=float(put_data.get('bid', 0) or 0),
                            ask=float(put_data.get('ask', 0) or 0),
                            last=float(put_data.get('lastPrice', 0) or 0),
                            volume=int(put_data.get('volume', 0) or 0),
                            open_interest=int(put_data.get('openInterest', 0) or 0),
                            delta=float(put_data.get('OptionGreeks', {}).get('delta', 0)) if put_data.get('OptionGreeks') else None,
                            gamma=float(put_data.get('OptionGreeks', {}).get('gamma', 0)) if put_data.get('OptionGreeks') else None,
                            theta=float(put_data.get('OptionGreeks', {}).get('theta', 0)) if put_data.get('OptionGreeks') else None,
                            vega=float(put_data.get('OptionGreeks', {}).get('vega', 0)) if put_data.get('OptionGreeks') else None,
                            implied_volatility=float(put_data.get('OptionGreeks', {}).get('iv', 0)) if put_data.get('OptionGreeks') else None
                        ))

            # Option 1: Use PrimeETradeTrading's helper (fetch both sides explicitly)
            if hasattr(self.etrade, 'get_option_chains'):
                expiry_formatted = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
                for side in ('CALL', 'PUT'):
                    response = self.etrade.get_option_chains(
                        symbol=symbol,
                        expiry_date=expiry_formatted,
                        option_type=side,
                        strike_count=strike_count,
                        include_greeks=include_greeks,
                    )
                    _parse_chain_response(response)
            else:
                # Option 2: Direct API call (fallback). First try side-agnostic, then explicit sides.
                url = f"{self.etrade.config['base_url']}/v1/market/optionchains"
                base_params = {
                    'symbol': symbol,
                    'expiryDate': expiry,
                    'strikeCount': strike_count,
                    'includeGreeks': 'true' if include_greeks else 'false'
                }
                response = self.etrade._make_etrade_api_call(
                    method='GET',
                    url=url,
                    params=base_params
                )
                _parse_chain_response(response)
                if not calls or not puts:
                    for side in ('CALL', 'PUT'):
                        params_side = dict(base_params)
                        params_side['optionType'] = side
                        response_side = self.etrade._make_etrade_api_call(
                            method='GET',
                            url=url,
                            params=params_side
                        )
                        _parse_chain_response(response_side)

            # Deduplicate by (type, strike) because explicit side fetch can overlap.
            parsed_contract_count = int(len(calls) + len(puts))
            parsed_unique_strikes = sorted({float(c.strike) for c in calls + puts if float(c.strike or 0.0) > 0.0})
            call_map = {float(c.strike): c for c in calls}
            put_map = {float(p.strike): p for p in puts}
            out_calls = list(call_map.values())
            out_puts = list(put_map.values())
            deduped_contract_count = int(len(out_calls) + len(out_puts))
            dedupe_removed = max(0, parsed_contract_count - deduped_contract_count)
            dedupe_removed_pct = (
                (float(dedupe_removed) / float(parsed_contract_count)) * 100.0
                if parsed_contract_count > 0 else 0.0
            )
            strike_anchor = float(underlying_price) if isinstance(underlying_price, (int, float)) and float(underlying_price) > 0 else None
            strike_anchor_source = "underlying_price" if strike_anchor is not None else "median_strike"
            if strike_anchor is None and parsed_unique_strikes:
                strike_anchor = parsed_unique_strikes[len(parsed_unique_strikes) // 2]
            nearby_strikes: List[str] = []
            if strike_anchor is not None and parsed_unique_strikes:
                nearby = sorted(parsed_unique_strikes, key=lambda s: abs(float(s) - float(strike_anchor)))[:5]
                nearby_strikes = [f"{float(s):.2f}" for s in nearby]

            log.info(
                "0DTE_CHAIN_PARSE_TELEMETRY | symbol=%s | requested_expiry=%s | raw_option_pairs=%d | "
                "parsed_contracts=%d | parsed_unique_strikes=%d | selected_expiry=%s | anchor_source=%s | "
                "anchor=%.2f | first5_strikes_near_anchor=%s | candidate_contracts_after_dedupe=%d | "
                "dedupe_removed=%d | dedupe_removed_pct=%.1f | strike_source_counts=%s",
                symbol,
                expiry,
                int(raw_option_pair_count),
                int(parsed_contract_count),
                int(len(parsed_unique_strikes)),
                expiry,
                strike_anchor_source,
                float(strike_anchor or 0.0),
                ",".join(nearby_strikes) if nearby_strikes else "none",
                int(deduped_contract_count),
                int(dedupe_removed),
                float(dedupe_removed_pct),
                ",".join([f"{k}:{int(v)}" for k, v in sorted(strike_source_counts.items())]) if strike_source_counts else "none",
            )
            if int(raw_option_pair_count) > 5 and int(len(parsed_unique_strikes)) <= 1:
                log.warning(
                    "0DTE_CHAIN_PARSE_WARN | symbol=%s | expiry=%s | reason=raw_pairs_high_but_unique_strikes_low | "
                    "raw_option_pairs=%d | parsed_unique_strikes=%d",
                    symbol,
                    expiry,
                    int(raw_option_pair_count),
                    int(len(parsed_unique_strikes)),
                )
            if dedupe_removed_pct > 80.0:
                log.warning(
                    "0DTE_CHAIN_PARSE_WARN | symbol=%s | expiry=%s | reason=dedupe_removed_gt_80pct | "
                    "parsed_contracts=%d | deduped_contracts=%d | removed_pct=%.1f",
                    symbol,
                    expiry,
                    int(parsed_contract_count),
                    int(deduped_contract_count),
                    float(dedupe_removed_pct),
                )
            if missing_or_zero_strike_count > 0:
                log.warning(
                    "0DTE_CHAIN_PARSE_WARN | symbol=%s | expiry=%s | reason=missing_or_zero_strike_aggregate | "
                    "count=%d | raw_option_pairs=%d",
                    symbol,
                    expiry,
                    int(missing_or_zero_strike_count),
                    int(raw_option_pair_count),
                )
            log.info(f"✅ Fetched options chain: {len(out_calls)} calls, {len(out_puts)} puts")
            return {'calls': out_calls, 'puts': out_puts}
                
        except Exception as e:
            log.error(f"Failed to fetch options chain: {e}")
            return {'calls': [], 'puts': []}
    
    async def get_option_quote(
        self,
        symbol: str,
        strike: float,
        expiry: str,
        option_type: str  # 'CALL' or 'PUT'
    ) -> Optional[Dict[str, float]]:
        """
        Fetch current quote for specific option contract (Rev 00238)
        
        Args:
            symbol: Underlying symbol (e.g., 'QQQ')
            strike: Strike price (e.g., 628.0)
            expiry: Expiry date (YYYYMMDD format)
            option_type: 'CALL' or 'PUT'
            
        Returns:
            Dictionary with current quote data:
            {
                'bid': float,
                'ask': float,
                'last': float,
                'mid_price': float,
                'volume': int,
                'open_interest': int,
                'delta': float (optional),
                'gamma': float (optional)
            }
            or None if not found
        """
        raise Exception("get_option_quote should not be used in 0DTE path")
    
    async def place_debit_spread_order(
        self,
        symbol: str,
        expiry: str,
        option_type: str,
        long_strike: float,
        short_strike: float,
        quantity: int = 1,
        *,
        max_net_debit: Optional[float] = None,
        quoted_mid_debit: Optional[float] = None,
        strategy: str = "ORB_0DTE",
        trade_id: str = "",
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Place debit spread order via ETrade API with explicit NET_DEBIT cap (bounded retries).

        Args:
            max_net_debit: Required max net debit per spread (per-share style consistent with selector debit_cost).
            quoted_mid_debit: Optional model mid for telemetry / first cap calibration.
        """
        if not self.is_available():
            log.error("ETrade API not available for order placement")
            return None
        if max_net_debit is None or float(max_net_debit) <= 0:
            log.error("place_debit_spread_order: max_net_debit is required and must be > 0")
            return None

        signal_ts = datetime.utcnow().isoformat()
        ladder_start = time.monotonic()
        profile, ag_level, opening_impulse = (
            resolve_execution_profile(execution_context)
            if resolve_execution_profile
            else (None, 0, False)
        )
        if ag_level > 0 and log_execution_event and build_execution_payload:
            log_execution_event(
                "EXECUTION_AGGRESSION_ESCALATED",
                strategy,
                build_execution_payload(
                    symbol=symbol,
                    trade_id=trade_id,
                    strategy=strategy,
                    signal_ts=signal_ts,
                    extra={
                        "execution_aggression_level": ag_level,
                        "opening_impulse_mode": opening_impulse,
                        "profile": getattr(profile, "name", ""),
                        "leg": "debit_spread_open",
                    },
                ),
            )
        base = float(max_net_debit)
        mid_hint = float(quoted_mid_debit) if quoted_mid_debit and float(quoted_mid_debit) > 0 else base
        # Progressive caps: stay within hard ceiling vs base
        caps = []
        multipliers = (1.025, 1.05, 1.08)
        if opening_impulse:
            multipliers = (1.04, 1.08)
        for m in multipliers:
            caps.append(min(round(base * m, 2), round(base * 1.12, 2)))
        last_resp: Optional[Dict[str, Any]] = None
        max_ladder_sec = float(getattr(profile, "max_total_ladder_sec", 6.0) or 6.0) if profile else 6.0

        try:
            log.info(
                "📝 Placing debit spread order: %s %s %s/%s max_net_debit=%.4f (n_attempts=%d)",
                symbol,
                option_type.upper(),
                long_strike,
                short_strike,
                base,
                len(caps),
            )

            expiry_short = expiry[2:]
            option_type_code = "C" if option_type.lower() == "call" else "P"
            long_option_symbol = f"{symbol} {expiry_short}{option_type_code}{int(long_strike * 1000):08d}"
            short_option_symbol = f"{symbol} {expiry_short}{option_type_code}{int(short_strike * 1000):08d}"

            preview_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/preview"
            place_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/place"

            for attempt, cap in enumerate(caps):
                if (time.monotonic() - ladder_start) >= max_ladder_sec:
                    if log_execution_event and build_execution_payload:
                        log_execution_event(
                            "EXECUTION_TIMEOUT_ABORT",
                            strategy,
                            build_execution_payload(
                                symbol=symbol,
                                trade_id=trade_id,
                                strategy=strategy,
                                signal_ts=signal_ts,
                                extra={"leg": "debit_spread_open", "attempt": attempt + 1},
                            ),
                        )
                    break
                submit_ts = datetime.utcnow().isoformat()
                if log_execution_event and build_execution_payload:
                    log_execution_event(
                        "EXECUTION_LIMIT_ATTEMPT",
                        strategy,
                        build_execution_payload(
                            symbol=symbol,
                            trade_id=trade_id,
                            strategy=strategy,
                            signal_ts=signal_ts,
                            submit_ts=submit_ts,
                            order_type="NET_DEBIT",
                            reprice_count=attempt,
                            quote_mid=mid_hint,
                            submitted_limit=float(cap),
                            spread_width_pct=None,
                            extra={
                                "leg": "debit_spread_open",
                                "cap_index": attempt + 1,
                                "quoted_mid_debit": float(mid_hint),
                                "debit_drift_vs_quoted": round(float(cap) - float(mid_hint), 4),
                            },
                        ),
                    )

                order_data = {
                    "orderType": "NET_DEBIT",
                    "clientOrderId": f"0DTE_{int(time.time())}_{attempt}",
                    "orderTerm": "GOOD_FOR_DAY",
                    "priceType": "NET_DEBIT",
                    "Order": [
                        {
                            "limitPrice": float(cap),
                            "Instrument": [
                                {
                                    "Product": {"securityType": "OPTION", "symbol": long_option_symbol},
                                    "orderAction": "BUY_OPEN",
                                    "quantity": quantity,
                                },
                                {
                                    "Product": {"securityType": "OPTION", "symbol": short_option_symbol},
                                    "orderAction": "SELL_OPEN",
                                    "quantity": quantity,
                                },
                            ],
                        }
                    ],
                }

                preview_response = self.etrade._make_etrade_api_call(
                    method="POST",
                    url=preview_url,
                    params=order_data,
                )
                if "PreviewOrderResponse" not in preview_response:
                    log.error(f"Preview failed: {preview_response}")
                    if log_execution_event and build_execution_payload:
                        log_execution_event(
                            "EXECUTION_REPRICE",
                            strategy,
                            build_execution_payload(
                                symbol=symbol,
                                trade_id=trade_id,
                                strategy=strategy,
                                signal_ts=signal_ts,
                                submit_ts=submit_ts,
                                order_type="NET_DEBIT",
                                reprice_count=attempt,
                                submitted_limit=float(cap),
                                extra={"error": "preview_failed"},
                            ),
                        )
                    continue

                preview_id = preview_response["PreviewOrderResponse"].get("previewId")
                if not preview_id:
                    log.error("No preview ID returned")
                    continue

                order_data["previewId"] = preview_id
                place_response = self.etrade._make_etrade_api_call(
                    method="POST",
                    url=place_url,
                    params=order_data,
                )
                last_resp = place_response
                if log_execution_event and build_execution_payload:
                    log_execution_event(
                        "EXECUTION_FILL_SUMMARY",
                        strategy,
                        build_execution_payload(
                            symbol=symbol,
                            trade_id=trade_id,
                            strategy=strategy,
                            signal_ts=signal_ts,
                            submit_ts=submit_ts,
                            fill_ts=datetime.utcnow().isoformat(),
                            order_type="NET_DEBIT",
                            reprice_count=attempt,
                            quote_mid=mid_hint,
                            submitted_limit=float(cap),
                            slippage_vs_mid=slippage_vs_mid("BUY", mid_hint, float(cap)) if slippage_vs_mid else None,
                            extra={"debit_cap": float(cap), "quoted_mid_debit": float(mid_hint), "path": "debit_spread_open"},
                        ),
                    )
                log.info(f"✅ Debit spread order placed (attempt {attempt + 1}): {place_response}")
                return place_response

            log.error("Debit spread placement exhausted retries")
            return last_resp

        except Exception as e:
            log.error(f"Failed to place debit spread order: {e}")
            return None

    async def place_single_option_order(
        self,
        symbol: str,
        expiry: str,
        option_type: str,
        strike: float,
        side: str,
        quantity: int = 1
    ) -> Optional[Dict[str, Any]]:
        """
        Place single-leg option order (lotto sleeve) via ETrade API
        
        Args:
            symbol: Underlying symbol
            expiry: Expiry date (YYYYMMDD)
            option_type: 'call' or 'put'
            strike: Strike price
            side: 'BUY' or 'SELL'
            quantity: Number of contracts
            
        Returns:
            Order response or None if failed
        """
        if not self.is_available():
            log.error("ETrade API not available for order placement")
            return None
        
        try:
            log.info(f"📝 Placing single option order: {symbol} {option_type.upper()} {strike} {side}")
            
            # Build option symbol string
            expiry_short = expiry[2:]  # YYMMDD
            option_type_code = 'C' if option_type.lower() == 'call' else 'P'
            option_symbol = f"{symbol} {expiry_short}{option_type_code}{int(strike * 1000):08d}"
            
            # Build order data
            order_action = 'BUY_OPEN' if side == 'BUY' else 'SELL_OPEN'
            
            order_data = {
                'orderType': 'MARKET',
                'clientOrderId': f"0DTE_LOTTO_{int(time.time())}",
                'orderTerm': 'GOOD_FOR_DAY',
                'priceType': 'MARKET',
                'Order': [{
                    'Instrument': [{
                        'Product': {
                            'securityType': 'OPTION',
                            'symbol': option_symbol
                        },
                        'orderAction': order_action,
                        'quantity': quantity
                    }]
                }]
            }
            
            # Preview order first
            preview_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/preview"
            preview_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=preview_url,
                params=order_data
            )
            
            if 'PreviewOrderResponse' not in preview_response:
                log.error(f"Preview failed: {preview_response}")
                return None
            
            preview_id = preview_response['PreviewOrderResponse'].get('previewId')
            if not preview_id:
                log.error("No preview ID returned")
                return None
            
            # Place order
            order_data['previewId'] = preview_id
            place_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/place"
            
            place_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=place_url,
                params=order_data
            )
            
            log.info(f"✅ Single option order placed: {place_response}")
            return place_response
            
        except Exception as e:
            log.error(f"Failed to place single option order: {e}")
            return None

    async def place_single_option_buy_open_smart(
        self,
        symbol: str,
        expiry: str,
        option_type: str,
        strike: float,
        quantity: int,
        bid: float,
        ask: float,
        mid: float,
        *,
        strategy: str = "ORB_0DTE",
        trade_id: str = "",
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Aggressive limit-first single-leg BUY_OPEN with profiles, anti-stall, MARKET fallback."""
        if not self.is_available():
            log.error("ETrade API not available for order placement")
            return None
        signal_ts = datetime.utcnow().isoformat()
        ladder_start = time.monotonic()
        profile, ag_level, opening_impulse = (
            resolve_execution_profile(execution_context)
            if resolve_execution_profile
            else (None, 0, False)
        )
        if profile is None:
            class _P:
                max_reprice = 3
                poll_sec = 0.35
                max_wait_per_attempt_sec = 1.0
                max_total_ladder_sec = 4.0
                spread_tolerance_mult = 1.0
                buy_first_step_aggression = 0.55
                name = "FALLBACK"

            profile = _P()

        if ag_level > 0 and log_execution_event and build_execution_payload:
            log_execution_event(
                "EXECUTION_AGGRESSION_ESCALATED",
                strategy,
                build_execution_payload(
                    symbol=symbol,
                    trade_id=trade_id,
                    strategy=strategy,
                    signal_ts=signal_ts,
                    extra={
                        "execution_aggression_level": ag_level,
                        "opening_impulse_mode": opening_impulse,
                        "profile": profile.name,
                    },
                ),
            )

        b, a, m = float(bid), float(ask), float(mid)
        mm = (b + a) / 2.0 if (b > 0 and a > 0) else max(m, 0.01)
        base_cap = 5.0 * float(profile.spread_tolerance_mult)
        ok, sp = last_look_option_spread_ok(b, a, mm)
        if not ok and sp > base_cap and not opening_impulse:
            if log_execution_event and build_execution_payload:
                log_execution_event(
                    "EXECUTION_SLIPPAGE_GUARD_REJECT",
                    strategy,
                    build_execution_payload(
                        symbol=symbol,
                        trade_id=trade_id,
                        strategy=strategy,
                        signal_ts=signal_ts,
                        spread_width_pct=sp,
                        quote_bid=b,
                        quote_ask=a,
                        quote_mid=mm,
                        extra={"path": "single_leg_last_look", "cap_pct": base_cap},
                    ),
                )
            return None

        expiry_short = expiry[2:]
        option_type_code = "C" if option_type.lower() == "call" else "P"
        option_symbol = f"{symbol} {expiry_short}{option_type_code}{int(strike * 1000):08d}"
        preview_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/preview"
        place_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/place"

        ag = float(getattr(profile, "buy_first_step_aggression", 0.5))
        limits = [
            round(min(a + 0.02, mm + ag * (a - mm) + (1.0 - ag) * 0.25 * (a - mm)), 2),
            round(min(a + 0.01, mm + 0.65 * (a - mm)), 2),
            round(a, 2),
            round(a + 0.02, 2),
        ]
        max_attempts = max(1, int(getattr(profile, "max_reprice", 2)))

        def _stall() -> bool:
            return (time.monotonic() - ladder_start) >= float(profile.max_total_ladder_sec)

        for attempt, lim in enumerate(limits[:max_attempts]):
            if _stall():
                if log_execution_event and build_execution_payload:
                    log_execution_event(
                        "EXECUTION_TIMEOUT_ABORT",
                        strategy,
                        build_execution_payload(
                            symbol=symbol,
                            trade_id=trade_id,
                            strategy=strategy,
                            signal_ts=signal_ts,
                            extra={"attempt": attempt + 1, "profile": profile.name},
                        ),
                    )
                break
            submit_ts = datetime.utcnow().isoformat()
            if log_execution_event and build_execution_payload:
                log_execution_event(
                    "EXECUTION_LIMIT_ATTEMPT",
                    strategy,
                    build_execution_payload(
                        symbol=symbol,
                        trade_id=trade_id,
                        strategy=strategy,
                        signal_ts=signal_ts,
                        submit_ts=submit_ts,
                        order_type="LIMIT",
                        reprice_count=attempt,
                        quote_bid=b,
                        quote_ask=a,
                        quote_mid=mm,
                        submitted_limit=float(lim),
                        spread_width_pct=sp,
                        extra={
                            "path": "single_leg_buy_open",
                            "profile": profile.name,
                            "opening_impulse_mode": opening_impulse,
                        },
                    ),
                )
            order_data = {
                "orderType": "LIMIT",
                "clientOrderId": f"0DTE_LOTTO_LIM_{int(time.time())}_{attempt}",
                "orderTerm": "GOOD_FOR_DAY",
                "priceType": "LIMIT",
                "Order": [
                    {
                        "limitPrice": float(lim),
                        "Instrument": [
                            {
                                "Product": {"securityType": "OPTION", "symbol": option_symbol},
                                "orderAction": "BUY_OPEN",
                                "quantity": quantity,
                            }
                        ],
                    }
                ],
            }
            try:
                preview_response = self.etrade._make_etrade_api_call(
                    method="POST", url=preview_url, params=order_data
                )
            except Exception as pe:
                log.warning("single-leg limit preview failed: %s", pe)
                await asyncio.sleep(min(0.2, profile.poll_sec))
                continue
            if "PreviewOrderResponse" not in preview_response:
                await asyncio.sleep(min(0.2, profile.poll_sec))
                continue
            preview_id = preview_response["PreviewOrderResponse"].get("previewId")
            if not preview_id:
                continue
            order_data["previewId"] = preview_id
            try:
                place_response = self.etrade._make_etrade_api_call(
                    method="POST", url=place_url, params=order_data
                )
            except Exception as pe:
                log.warning("single-leg limit place failed: %s", pe)
                continue
            if log_execution_event and build_execution_payload:
                log_execution_event(
                    "EXECUTION_FILL_SUMMARY",
                    strategy,
                    build_execution_payload(
                        symbol=symbol,
                        trade_id=trade_id,
                        strategy=strategy,
                        signal_ts=signal_ts,
                        submit_ts=submit_ts,
                        fill_ts=datetime.utcnow().isoformat(),
                        order_type="LIMIT",
                        reprice_count=attempt,
                        quote_bid=b,
                        quote_ask=a,
                        quote_mid=mm,
                        submitted_limit=float(lim),
                        slippage_vs_mid=slippage_vs_mid("BUY", mm, float(lim)) if slippage_vs_mid else None,
                        spread_width_pct=sp,
                        extra={
                            "path": "single_leg_buy_open",
                            "actual_fill_confirmed": False,
                            "profile": profile.name,
                        },
                    ),
                )
            return place_response

        if log_execution_event and build_execution_payload:
            log_execution_event(
                "EXECUTION_FORCE_FALLBACK",
                strategy,
                build_execution_payload(
                    symbol=symbol,
                    trade_id=trade_id,
                    strategy=strategy,
                    signal_ts=signal_ts,
                    order_type="MARKET",
                    quote_bid=b,
                    quote_ask=a,
                    quote_mid=mm,
                    extra={"path": "single_leg_buy_open", "profile": profile.name},
                ),
            )
            log_execution_event(
                "EXECUTION_MARKET_FALLBACK",
                strategy,
                build_execution_payload(
                    symbol=symbol,
                    trade_id=trade_id,
                    strategy=strategy,
                    signal_ts=signal_ts,
                    order_type="MARKET",
                    quote_bid=b,
                    quote_ask=a,
                    quote_mid=mm,
                    extra={"path": "single_leg_buy_open"},
                ),
            )
        return await self.place_single_option_order(
            symbol, expiry, option_type, strike, "BUY", quantity
        )
    
    async def place_credit_spread_order(
        self,
        symbol: str,
        expiry: str,
        option_type: str,
        short_strike: float,
        long_strike: float,
        quantity: int = 1
    ) -> Optional[Dict[str, Any]]:
        """
        Place credit spread order via ETrade API
        
        For credit spreads:
        - CALL credit spread: Sell call at lower strike, buy call at higher strike (bearish)
        - PUT credit spread: Sell put at higher strike, buy put at lower strike (bullish)
        
        Args:
            symbol: Underlying symbol
            expiry: Expiry date (YYYYMMDD)
            option_type: 'call' or 'put'
            short_strike: Short leg strike (sell this)
            long_strike: Long leg strike (buy this for protection)
            quantity: Number of spreads
            
        Returns:
            Order response or None if failed
        """
        if not self.is_available():
            log.error("ETrade API not available for order placement")
            return None
        
        try:
            log.info(f"📝 Placing credit spread order: {symbol} {option_type.upper()} {short_strike}/{long_strike}")
            
            # Build option symbol strings
            # Format: SYMBOL YYMMDD C/P STRIKE
            expiry_short = expiry[2:]  # YYMMDD
            option_type_code = 'C' if option_type.lower() == 'call' else 'P'
            
            short_option_symbol = f"{symbol} {expiry_short}{option_type_code}{int(short_strike * 1000):08d}"
            long_option_symbol = f"{symbol} {expiry_short}{option_type_code}{int(long_strike * 1000):08d}"
            
            # Build order data for ETrade API
            # Credit spread: NET_CREDIT (receive premium)
            # Order: Sell short leg, buy long leg
            order_data = {
                'orderType': 'NET_CREDIT',
                'clientOrderId': f"0DTE_CREDIT_{int(time.time())}",
                'orderTerm': 'GOOD_FOR_DAY',
                'priceType': 'NET_CREDIT',
                'Order': [{
                    'Instrument': [
                        {
                            'Product': {
                                'securityType': 'OPTION',
                                'symbol': short_option_symbol
                            },
                            'orderAction': 'SELL_OPEN',  # Sell short leg (receive premium)
                            'quantity': quantity
                        },
                        {
                            'Product': {
                                'securityType': 'OPTION',
                                'symbol': long_option_symbol
                            },
                            'orderAction': 'BUY_OPEN',  # Buy long leg (protection)
                            'quantity': quantity
                        }
                    ]
                }]
            }
            
            # Preview order first
            preview_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/preview"
            preview_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=preview_url,
                params=order_data
            )
            
            if 'PreviewOrderResponse' not in preview_response:
                log.error(f"Preview failed: {preview_response}")
                return None
            
            preview_id = preview_response['PreviewOrderResponse'].get('previewId')
            if not preview_id:
                log.error("No preview ID returned")
                return None
            
            # Place order
            order_data['previewId'] = preview_id
            place_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/place"
            
            place_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=place_url,
                params=order_data
            )
            
            log.info(f"✅ Credit spread order placed: {place_response}")
            return place_response
            
        except Exception as e:
            log.error(f"Failed to place credit spread order: {e}")
            return None
    
    async def close_position(
        self,
        position: 'OptionsPosition',
        exit_price: Optional[float] = None,
        order_type: str = 'MARKET'
    ) -> Optional[Dict[str, Any]]:
        """
        Close options position via ETrade API
        
        Args:
            position: OptionsPosition object to close
            exit_price: Exit price (for LIMIT orders, None = use MARKET)
            order_type: 'MARKET' or 'LIMIT'
            
        Returns:
            Order response or None if failed
        """
        if not self.is_available():
            log.error("ETrade API not available for position close")
            return None
        
        try:
            log.info(f"📝 Closing options position: {position.position_id}")
            log.info(f"   Symbol: {position.symbol}, Type: {position.position_type}")
            log.info(f"   Quantity: {position.quantity}, Exit Price: ${exit_price or 'MARKET'}")
            
            # Determine order structure based on position type
            if position.position_type == 'debit_spread':
                # Close debit spread: Buy back short leg, sell long leg
                spread = position.debit_spread
                expiry_short = spread.expiry.replace('-', '')[2:]  # YYMMDD
                option_type_code = 'C' if spread.option_type.lower() == 'call' else 'P'
                
                long_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.long_strike * 1000):08d}"
                short_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.short_strike * 1000):08d}"
                
                # Close spread: Buy back short leg (close short), sell long leg (close long)
                order_data = {
                    'orderType': 'NET_CREDIT' if exit_price else 'MARKET',
                    'clientOrderId': f"0DTE_CLOSE_{int(time.time())}",
                    'orderTerm': 'GOOD_FOR_DAY',
                    'priceType': 'NET_CREDIT' if exit_price else 'MARKET',
                    'Order': [{
                        'Instrument': [
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': short_option_symbol
                                },
                                'orderAction': 'BUY_CLOSE',  # Buy back short leg
                                'quantity': position.quantity
                            },
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': long_option_symbol
                                },
                                'orderAction': 'SELL_CLOSE',  # Sell long leg
                                'quantity': position.quantity
                            }
                        ]
                    }]
                }
                
                if exit_price and order_type == 'LIMIT':
                    order_data['Order'][0]['limitPrice'] = exit_price
                    
            elif position.position_type == 'credit_spread':
                # Close credit spread: Buy back short leg, sell long leg
                spread = position.credit_spread
                expiry_short = spread.expiry.replace('-', '')[2:]  # YYMMDD
                option_type_code = 'C' if spread.option_type.lower() == 'call' else 'P'
                
                short_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.short_strike * 1000):08d}"
                long_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.long_strike * 1000):08d}"
                
                # Close spread: Buy back short leg (close short), sell long leg (close long)
                order_data = {
                    'orderType': 'NET_DEBIT' if exit_price else 'MARKET',
                    'clientOrderId': f"0DTE_CLOSE_{int(time.time())}",
                    'orderTerm': 'GOOD_FOR_DAY',
                    'priceType': 'NET_DEBIT' if exit_price else 'MARKET',
                    'Order': [{
                        'Instrument': [
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': short_option_symbol
                                },
                                'orderAction': 'BUY_CLOSE',  # Buy back short leg
                                'quantity': position.quantity
                            },
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': long_option_symbol
                                },
                                'orderAction': 'SELL_CLOSE',  # Sell long leg
                                'quantity': position.quantity
                            }
                        ]
                    }]
                }
                
                if exit_price and order_type == 'LIMIT':
                    order_data['Order'][0]['limitPrice'] = exit_price
                    
            elif position.position_type == 'lotto':
                # Close single-leg option: Sell the contract
                contract = position.lotto_contract
                expiry_short = contract.expiry.replace('-', '')[2:]  # YYMMDD
                option_type_code = 'C' if contract.option_type.lower() == 'call' else 'P'
                option_symbol = f"{contract.symbol} {expiry_short}{option_type_code}{int(contract.strike * 1000):08d}"
                
                order_data = {
                    'orderType': order_type,
                    'clientOrderId': f"0DTE_CLOSE_{int(time.time())}",
                    'orderTerm': 'GOOD_FOR_DAY',
                    'priceType': order_type,
                    'Order': [{
                        'Instrument': [{
                            'Product': {
                                'securityType': 'OPTION',
                                'symbol': option_symbol
                            },
                            'orderAction': 'SELL_CLOSE',  # Close long position
                            'quantity': position.quantity
                        }]
                    }]
                }
                
                if exit_price and order_type == 'LIMIT':
                    order_data['Order'][0]['limitPrice'] = exit_price
            else:
                log.error(f"Unknown position type: {position.position_type}")
                return None
            
            # Preview order first
            preview_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/preview"
            preview_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=preview_url,
                params=order_data
            )
            
            if 'PreviewOrderResponse' not in preview_response:
                log.error(f"Preview failed: {preview_response}")
                return None
            
            preview_id = preview_response['PreviewOrderResponse'].get('previewId')
            if not preview_id:
                log.error("No preview ID returned")
                return None
            
            # Place order
            order_data['previewId'] = preview_id
            place_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/place"
            
            place_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=place_url,
                params=order_data
            )
            
            log.info(f"✅ Options position closed: {place_response}")
            return place_response
            
        except Exception as e:
            log.error(f"Failed to close options position: {e}")
            return None
    
    async def partial_close_position(
        self,
        position: 'OptionsPosition',
        partial_quantity: int,
        exit_price: Optional[float] = None,
        order_type: str = 'MARKET'
    ) -> Optional[Dict[str, Any]]:
        """
        Partially close options position via ETrade API
        
        Args:
            position: OptionsPosition object to partially close
            partial_quantity: Number of contracts/spreads to close (must be < position.quantity)
            exit_price: Exit price (for LIMIT orders, None = use MARKET)
            order_type: 'MARKET' or 'LIMIT'
            
        Returns:
            Order response or None if failed
        """
        if not self.is_available():
            log.error("ETrade API not available for partial position close")
            return None
        
        if partial_quantity >= position.quantity:
            log.error(f"Partial quantity {partial_quantity} must be less than position quantity {position.quantity}")
            return None
        
        try:
            log.info(f"📝 Partially closing options position: {position.position_id}")
            log.info(f"   Symbol: {position.symbol}, Type: {position.position_type}")
            log.info(f"   Closing: {partial_quantity}/{position.quantity} contracts, Exit Price: ${exit_price or 'MARKET'}")
            
            # Determine order structure based on position type
            if position.position_type == 'debit_spread':
                # Partially close debit spread: Buy back short leg, sell long leg (partial quantity)
                spread = position.debit_spread
                expiry_short = spread.expiry.replace('-', '')[2:]  # YYMMDD
                option_type_code = 'C' if spread.option_type.lower() == 'call' else 'P'
                
                long_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.long_strike * 1000):08d}"
                short_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.short_strike * 1000):08d}"
                
                # Partially close spread: Buy back short leg (close short), sell long leg (close long)
                order_data = {
                    'orderType': 'NET_CREDIT' if exit_price else 'MARKET',
                    'clientOrderId': f"0DTE_PARTIAL_{int(time.time())}",
                    'orderTerm': 'GOOD_FOR_DAY',
                    'priceType': 'NET_CREDIT' if exit_price else 'MARKET',
                    'Order': [{
                        'Instrument': [
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': short_option_symbol
                                },
                                'orderAction': 'BUY_CLOSE',  # Buy back short leg
                                'quantity': partial_quantity
                            },
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': long_option_symbol
                                },
                                'orderAction': 'SELL_CLOSE',  # Sell long leg
                                'quantity': partial_quantity
                            }
                        ]
                    }]
                }
                
                if exit_price and order_type == 'LIMIT':
                    order_data['Order'][0]['limitPrice'] = exit_price
                    
            elif position.position_type == 'credit_spread':
                # Partially close credit spread: Buy back short leg, sell long leg (partial quantity)
                spread = position.credit_spread
                expiry_short = spread.expiry.replace('-', '')[2:]  # YYMMDD
                option_type_code = 'C' if spread.option_type.lower() == 'call' else 'P'
                
                short_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.short_strike * 1000):08d}"
                long_option_symbol = f"{spread.symbol} {expiry_short}{option_type_code}{int(spread.long_strike * 1000):08d}"
                
                # Partially close spread: Buy back short leg (close short), sell long leg (close long)
                order_data = {
                    'orderType': 'NET_DEBIT' if exit_price else 'MARKET',
                    'clientOrderId': f"0DTE_PARTIAL_{int(time.time())}",
                    'orderTerm': 'GOOD_FOR_DAY',
                    'priceType': 'NET_DEBIT' if exit_price else 'MARKET',
                    'Order': [{
                        'Instrument': [
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': short_option_symbol
                                },
                                'orderAction': 'BUY_CLOSE',  # Buy back short leg
                                'quantity': partial_quantity
                            },
                            {
                                'Product': {
                                    'securityType': 'OPTION',
                                    'symbol': long_option_symbol
                                },
                                'orderAction': 'SELL_CLOSE',  # Sell long leg
                                'quantity': partial_quantity
                            }
                        ]
                    }]
                }
                
                if exit_price and order_type == 'LIMIT':
                    order_data['Order'][0]['limitPrice'] = exit_price
                    
            elif position.position_type == 'lotto':
                # Partially close single-leg option: Sell partial quantity
                contract = position.lotto_contract
                expiry_short = contract.expiry.replace('-', '')[2:]  # YYMMDD
                option_type_code = 'C' if contract.option_type.lower() == 'call' else 'P'
                option_symbol = f"{contract.symbol} {expiry_short}{option_type_code}{int(contract.strike * 1000):08d}"
                
                order_data = {
                    'orderType': order_type,
                    'clientOrderId': f"0DTE_PARTIAL_{int(time.time())}",
                    'orderTerm': 'GOOD_FOR_DAY',
                    'priceType': order_type,
                    'Order': [{
                        'Instrument': [{
                            'Product': {
                                'securityType': 'OPTION',
                                'symbol': option_symbol
                            },
                            'orderAction': 'SELL_CLOSE',  # Close long position (partial)
                            'quantity': partial_quantity
                        }]
                    }]
                }
                
                if exit_price and order_type == 'LIMIT':
                    order_data['Order'][0]['limitPrice'] = exit_price
            else:
                log.error(f"Unknown position type: {position.position_type}")
                return None
            
            # Preview order first
            preview_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/preview"
            preview_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=preview_url,
                params=order_data
            )
            
            if 'PreviewOrderResponse' not in preview_response:
                log.error(f"Preview failed: {preview_response}")
                return None
            
            preview_id = preview_response['PreviewOrderResponse'].get('previewId')
            if not preview_id:
                log.error("No preview ID returned")
                return None
            
            # Place order
            order_data['previewId'] = preview_id
            place_url = f"{self.etrade.config['base_url']}/v1/accounts/{self.etrade.selected_account.account_id_key}/orders/place"
            
            place_response = self.etrade._make_etrade_api_call(
                method='POST',
                url=place_url,
                params=order_data
            )
            
            log.info(f"✅ Options position partially closed: {partial_quantity} contracts")
            log.info(f"   Remaining: {position.quantity - partial_quantity} contracts")
            return place_response
            
        except Exception as e:
            log.error(f"Failed to partially close options position: {e}")
            return None

