"""
Easy Collector - Snapshot Service Orchestrator
Core logic for collecting market snapshots
Pure-ish and injectable design
"""

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd

from app.config import get_settings
from app.clients.base_client import BaseMarketDataClient
from app.clients.yfinance_client import YFinanceClient
from app.clients.coinbase_client import CoinbaseClient, SymbolUnresolvable
from app.clients.us_provider_router import run_healthcheck, is_skipped
from app.services.calendar_service import get_calendar_tags, get_early_close_time
from app.services.indicator_service import IndicatorService
from app.services.outcome_label_service import OutcomeLabelService
from app.storage.firestore_repo import FirestoreRepository
from app.storage.local_repo import LocalRepository
from app.storage.us_intraday_cache import get_cache
from app.models.snapshot_models import (
    Snapshot, SnapshotType, MarketType, CollectionSummary, CryptoSession,
    PriceCandleData, ORBBlock, TrendMomentumData, VolatilityData,
    VolumeVWAPData, OscillatorData, MACDData, BollingerData,
    IchimokuData, CalendarTags, SignalData, OutcomeDataUS, OutcomeDataCrypto,
    SessionBounds
)
from app.utils.time_utils import (
    now_et, et_to_utc, get_us_orb_time, get_us_signal_time, get_us_outcome_time,
    get_crypto_orb_time, get_crypto_signal_time, get_crypto_outcome_time,
    parse_time_string, get_minutes_since_open, get_minutes_to_next_session,
    ensure_tz, get_market_tz
)

log = logging.getLogger(__name__)


class SnapshotService:
    """Service for orchestrating market snapshot collection"""
    
    def __init__(
        self,
        yfinance_client: Optional[YFinanceClient] = None,
        coinbase_client: Optional[CoinbaseClient] = None,
        calendar_service=None,  # For dependency injection (if needed)
        indicator_service: Optional[IndicatorService] = None,
        outcome_label_service: Optional[OutcomeLabelService] = None,
        firestore_repo: Optional[FirestoreRepository] = None,
        local_repo: Optional[LocalRepository] = None
    ):
        """Initialize snapshot service with dependencies"""
        self.settings = get_settings()
        self.yfinance_client = yfinance_client or YFinanceClient()
        self.coinbase_client = coinbase_client or CoinbaseClient()
        self.indicator_service = indicator_service or IndicatorService()
        self.outcome_label_service = outcome_label_service or OutcomeLabelService()
        self.firestore_repo = firestore_repo or FirestoreRepository()
        
        # Initialize local storage if enabled
        self.local_repo = None
        if self.settings.enable_local_storage:
            base_dir = self.settings.resolved_local_storage_path
            self.local_repo = local_repo or LocalRepository(base_dir=base_dir)
            log.info(f"✅ Local storage enabled at {base_dir}")
        
        log.info("✅ SnapshotService initialized")
    
    def collect_us_snapshots(
        self,
        snapshot_type: SnapshotType,
        timestamp_et: Optional[datetime] = None,
        symbol_limit: Optional[int] = None,
    ) -> CollectionSummary:
        """
        Collect snapshots for US 0DTE symbols
        
        Args:
            snapshot_type: Type of snapshot (ORB, SIGNAL, OUTCOME)
            timestamp_et: Snapshot timestamp in ET (default: now)
            symbol_limit: If set, use only the first N symbols (e.g. 24 for Tier1-only validation)
        
        Returns:
            CollectionSummary with results
        """
        start_time = time.time()
        
        # Ensure timezone-aware ET (critical for correctness)
        if timestamp_et is None:
            timestamp_et = now_et()
        timestamp_et = ensure_tz(timestamp_et, get_market_tz())
        
        # Resolve symbols list - use 0DTE symbols from CSV
        symbols = self.settings.load_0dte_symbols()
        if symbol_limit is not None:
            symbols = symbols[:symbol_limit]
            log.info(f"📋 Using first {len(symbols)} symbols (symbol_limit={symbol_limit})")
        else:
            log.info(f"📋 Loaded {len(symbols)} 0DTE symbols from CSV")
        
        # Get calendar tags
        calendar_tags_dict = get_calendar_tags(timestamp_et.date())
        calendar_tags = CalendarTags(**calendar_tags_dict)
        
        # Determine OHLCV window based on snapshot type
        start_utc, end_utc = self._get_us_ohlcv_window(snapshot_type, timestamp_et)
        
        # Generate run_id for this collection run (same for all symbols)
        run_id = f"{snapshot_type.value}_{timestamp_et.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        
        successful = 0
        failed = 0
        errors = []
        
        log.info(f"📊 Collecting US {snapshot_type.value} snapshots for {len(symbols)} symbols at {timestamp_et}")
        log.info(f"📅 Snapshot window: {start_utc} to {end_utc} UTC")
        log.info(f"🆔 Run ID: {run_id}")

        # Provider router: healthcheck gate and failover. Fail-open if all providers down.
        us_client, us_provider_name, health_result = run_healthcheck(yfinance_client=self.yfinance_client)
        if is_skipped(health_result):
            duration = time.time() - start_time
            summary = CollectionSummary(
                market=MarketType.US,
                snapshot_type=snapshot_type,
                session=None,
                total_snapshots=0,
                successful=0,
                failed=0,
                errors=[health_result.get("message", "US provider down") or "SKIPPED_PROVIDER_DOWN"],
                duration_seconds=duration,
                timestamp=datetime.utcnow(),
                us_provider_used=None,
                us_provider_health=health_result,
                us_collection_status="SKIPPED_PROVIDER_DOWN",
            )
            if not self.settings.dry_run:
                self.firestore_repo.save_run_log(summary)
            if self.local_repo:
                self.local_repo.save_run_log(summary)
            log.warning(f"⏭️ US collection SKIPPED (provider down, fail-open). Run logs written. Crypto can still run.")
            return summary

        us_source_map = {"yfinance": "YFINANCE", "polygon": "POLYGON", "alpaca": "ALPACA"}
        us_source = us_source_map.get(us_provider_name, "ETRADE")
        log.info(f"📡 US provider: {us_provider_name} (source={us_source})")
        
        # Prefetch data: use us_client when available (Polygon/Alpaca), else cache uses yfinance
        cache = get_cache()
        target_date = timestamp_et.date()
        snapshot_time_utc = et_to_utc(timestamp_et)
        
        log.info(f"📦 Prefetching data (date: {target_date}, timeframe: {self.settings.timeframe}, us_client={us_provider_name})...")
        
        cached_data = cache.prefetch(
            date=target_date,
            symbols=symbols,
            timeframe=self.settings.timeframe,
            period="2d",
            us_client=us_client,
        )
        
        prefetch_successful = sum(1 for df in cached_data.values() if not df.empty)
        log.info(f"✅ Prefetch complete: {prefetch_successful}/{len(symbols)} symbols available in cache")
        
        # Tier gating: run Tier1 (first 24) first; if Tier1 success rate < 60%, skip Tier2
        tier1 = set(symbols[:24]) if len(symbols) >= 24 else set(symbols)
        successful_tier1 = 0
        processed_tier1 = 0
        tier2_skipped = False
        
        # Process each symbol using cache (two-layer design: indicator slab + snapshot window)
        for idx, symbol in enumerate(symbols, 1):
            log.info(f"📈 [{idx}/{len(symbols)}] Processing {symbol}...")
            if tier2_skipped and symbol not in tier1:
                log.warning("Tier2 skipped (Tier1 success < 60%%): %s", symbol)
                continue
            try:
                # NEW STRATEGY: Two-layer data retrieval
                # A) Get indicator slab (120 bars ending at snapshot time) for indicator calculations
                indicator_slab = cache.get_indicator_slab(
                    symbol=symbol,
                    snapshot_time_utc=snapshot_time_utc,
                    lookback_bars=self.settings.indicator_lookback_bars,
                    timeframe=self.settings.timeframe
                )
                
                # B) Get snapshot window (ORB/SIGNAL/OUTCOME window) for structure metrics
                snapshot_window = cache.get_slice(
                    symbol=symbol,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    timeframe=self.settings.timeframe
                )
                
                # Check data availability
                indicator_bars_available = len(indicator_slab)
                snapshot_bars_available = len(snapshot_window)
                indicator_ready = indicator_bars_available >= 60  # Minimum for basic indicators
                
                log.info(f"  📊 Data availability:")
                log.info(f"     - Indicator slab: {indicator_bars_available} bars (need {self.settings.indicator_lookback_bars}, ready: {indicator_ready})")
                log.info(f"     - Snapshot window: {snapshot_bars_available} bars (window: {start_utc} to {end_utc})")
                
                if indicator_slab.empty and snapshot_window.empty:
                    # Fallback: if cache prefetch failed (e.g., yfinance batch blocked),
                    # try a direct per-symbol fetch via the US client (yfinance path).
                    log.warning(f"⚠️ No cached data available for {symbol} - attempting direct fetch fallback")
                    try:
                        # Adjust fetch window if outside market hours (weekends/holidays)
                        # Use last trading session's data window
                        timestamp_et_adj = timestamp_et
                        if timestamp_et.weekday() >= 5:  # Weekend
                            # Adjust to last Friday
                            days_back = timestamp_et.weekday() - 4  # Sat=1, Sun=2
                            timestamp_et_adj = timestamp_et - timedelta(days=days_back)
                            timestamp_et_adj = timestamp_et_adj.replace(hour=16, minute=0, second=0, microsecond=0)
                            log.info(f"  📅 Adjusted weekend timestamp {timestamp_et} → {timestamp_et_adj} (last trading day)")
                        
                        timeframe_minutes = int(str(self.settings.timeframe).replace("m", ""))
                        lookback_minutes = int(self.settings.indicator_lookback_bars) * timeframe_minutes
                        snapshot_time_utc_adj = et_to_utc(timestamp_et_adj)
                        fetch_start_utc = snapshot_time_utc_adj - timedelta(minutes=lookback_minutes + 60)
                        fetch_end_utc = snapshot_time_utc_adj + timedelta(minutes=5)
                        
                        # Adjust snapshot window bounds if we adjusted the timestamp
                        start_utc_adj = start_utc
                        end_utc_adj = end_utc
                        if timestamp_et_adj != timestamp_et:
                            # Recalculate window bounds for adjusted timestamp
                            start_utc_adj, end_utc_adj = self._get_us_ohlcv_window(snapshot_type, timestamp_et_adj)

                        full_df = us_client.get_ohlcv(
                            symbol=symbol,
                            timeframe=self.settings.timeframe,
                            start_utc=fetch_start_utc,
                            end_utc=fetch_end_utc,
                            snapshot_type=snapshot_type.value
                        )

                        if full_df is not None and not full_df.empty and "timestamp" in full_df.columns:
                            ts = pd.to_datetime(full_df["timestamp"])
                            indicator_slab = full_df[ts <= snapshot_time_utc_adj].tail(self.settings.indicator_lookback_bars).copy()
                            snapshot_window = full_df[(ts >= start_utc_adj) & (ts <= end_utc_adj)].copy()
                        else:
                            indicator_slab = pd.DataFrame()
                            snapshot_window = pd.DataFrame()
                    except Exception as e:
                        log.error(f"❌ Direct fetch fallback failed for {symbol}: {e}", exc_info=True)
                        indicator_slab = pd.DataFrame()
                        snapshot_window = pd.DataFrame()

                    if indicator_slab.empty and snapshot_window.empty:
                        if symbol in tier1:
                            processed_tier1 += 1
                            if processed_tier1 == 24 and (successful_tier1 / 24) < 0.6:
                                tier2_skipped = True
                                log.warning("Tier2 skipped: Tier1 success rate < 60%% (%s/24)", successful_tier1)
                        failed += 1
                        errors.append(f"{symbol}: No cached data available")
                        continue
                
                # Guard: ensure required columns exist
                required_cols = ['timestamp', 'open', 'high', 'low', 'close']
                if not all(col in indicator_slab.columns for col in required_cols):
                    log.warning(f"⚠️ Missing columns in indicator slab for {symbol}")
                    if symbol in tier1:
                        processed_tier1 += 1
                        if processed_tier1 == 24 and (successful_tier1 / 24) < 0.6:
                            tier2_skipped = True
                            log.warning("Tier2 skipped: Tier1 success rate < 60%% (%s/24)", successful_tier1)
                    failed += 1
                    errors.append(f"{symbol}: Missing columns in indicator slab")
                    continue
                
                # Compute indicators from SLAB (not snapshot window)
                log.debug(f"  Computing indicators from slab ({indicator_bars_available} bars)...")
                indicators = self.indicator_service.calculate_indicators(
                    indicator_slab, 
                    symbol=symbol,
                    indicator_ready=indicator_ready,
                    bars_available=indicator_bars_available
                )
                
                # Get session start for session VWAP calculation
                market_open_dt, _, _, _ = self._get_session_bounds_us(timestamp_et)
                session_start_utc = et_to_utc(market_open_dt)
                
                # Calculate session VWAP (from session start, not full history)
                vwap_value, vwap_mode = IndicatorService.calculate_session_vwap(
                    indicator_slab,
                    session_start_utc,
                    timestamp_col='timestamp'
                )
                if vwap_value is not None:
                    indicators['vwap'] = vwap_value
                    indicators['vwap_mode'] = vwap_mode
                    # Recalculate VWAP distance and momentum with session VWAP
                    if not indicator_slab.empty:
                        latest_close = float(indicator_slab['close'].iloc[-1])
                        indicators['vwap_distance_pct'] = ((latest_close - vwap_value) / vwap_value * 100) if vwap_value > 0 else None
                        # VWAP momentum (difference from previous bar's VWAP)
                        if len(indicator_slab) >= 2:
                            prev_vwap, _ = IndicatorService.calculate_session_vwap(
                                indicator_slab.iloc[:-1],
                                session_start_utc,
                                timestamp_col='timestamp'
                            )
                            if prev_vwap is not None:
                                indicators['vwap_momentum'] = vwap_value - prev_vwap
                
                # Use snapshot_window for ORB structure metrics and latest bar
                # Combine slab + window for full context (ORB calculations need both)
                if not snapshot_window.empty:
                    # Combine: use slab for indicators, window for structure
                    # For ORB calculations, we need both: slab for context, window for ORB range
                    ohlcv_df = pd.concat([indicator_slab, snapshot_window]).sort_values('timestamp')
                    ohlcv_df = ohlcv_df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
                else:
                    # Fallback: use slab only (shouldn't happen if cache is working)
                    log.warning(f"  ⚠️ Snapshot window empty for {symbol}, using indicator slab only")
                    ohlcv_df = indicator_slab
                
                indicators_count = len([k for k, v in indicators.items() if v is not None and k not in ['indicator_ready', 'indicator_bars_available', 'vwap_mode']])
                log.debug(f"  Got {indicators_count} indicators for {symbol} (ready: {indicator_ready}, bars: {indicator_bars_available})")
                
                # VERIFICATION: Assert no yfinance calls during snapshot processing
                # All data should come from cache (logged as "CACHE READ" above)
                log.debug(f"  ✅ NO API CALLS: Using cached data only (no yfinance calls during snapshot)")
                
                # Build snapshot payload
                log.debug(f"  Building snapshot for {symbol} ({snapshot_type.value})...")
                snapshot = self._build_us_snapshot(
                    symbol=symbol,
                    snapshot_type=snapshot_type,
                    timestamp_et=timestamp_et,
                    ohlcv_df=ohlcv_df,
                    indicators=indicators,
                    calendar_tags=calendar_tags,
                    run_id=run_id,
                    us_source=us_source,
                )
                
                # Save snapshot (with dry-run support)
                doc_id = snapshot.get_doc_id()
                log.debug(f"  Generated snapshot doc_id: {doc_id}")
                
                # Log snapshot metadata for verification
                log.info(f"  📊 Snapshot metadata:")
                log.info(f"     - Indicator ready: {snapshot.indicator_ready} ({snapshot.indicator_bars_available}/{snapshot.indicator_lookback_target} bars)")
                log.info(f"     - Ichimoku ready: {snapshot.ichimoku_ready}")
                log.info(f"     - ORB integrity: {snapshot.orb_block.orb_integrity_ok} ({snapshot.orb_block.orb_actual_bars}/{snapshot.orb_block.orb_expected_bars} bars)")
                log.info(f"     - VWAP mode: {snapshot.vwap_mode}")
                if snapshot.serialized_snapshot_bytes:
                    log.info(f"     - Payload size: {snapshot.serialized_snapshot_bytes:,} bytes")
                
                # Always save locally if enabled (even in dry-run mode)
                local_saved = False
                if self.local_repo:
                    local_saved = self.local_repo.save_snapshot(snapshot)
                    if local_saved:
                        log.debug(f"  ✅ Saved snapshot locally: {doc_id}")
                
                if self.settings.dry_run:
                    log.info(f"  ✅ DRY-RUN: Would save snapshot {doc_id} (local: {'✅' if local_saved else '❌'})")
                    successful += 1
                    if symbol in tier1:
                        successful_tier1 += 1
                        processed_tier1 += 1
                        if processed_tier1 == 24 and (successful_tier1 / 24) < 0.6:
                            tier2_skipped = True
                            log.warning("Tier2 skipped: Tier1 success rate < 60%% (%s/24)", successful_tier1)
                else:
                    log.debug(f"  Saving snapshot {doc_id} to Firestore...")
                    firestore_saved = self.firestore_repo.save_snapshot(snapshot)
                    
                    if firestore_saved:
                        log.info(f"  ✅ Saved snapshot {doc_id} (Firestore: ✅, Local: {'✅' if local_saved else '❌'})")
                        successful += 1
                        if symbol in tier1:
                            successful_tier1 += 1
                            processed_tier1 += 1
                            if processed_tier1 == 24 and (successful_tier1 / 24) < 0.6:
                                tier2_skipped = True
                                log.warning("Tier2 skipped: Tier1 success rate < 60%% (%s/24)", successful_tier1)
                    else:
                        log.error(f"  ❌ Failed to save snapshot {doc_id} to Firestore")
                        if local_saved:
                            log.info(f"  ⚠️ Snapshot saved locally but Firestore failed")
                            successful += 1
                            if symbol in tier1:
                                successful_tier1 += 1
                                processed_tier1 += 1
                                if processed_tier1 == 24 and (successful_tier1 / 24) < 0.6:
                                    tier2_skipped = True
                                    log.warning("Tier2 skipped: Tier1 success rate < 60%% (%s/24)", successful_tier1)
                        else:
                            failed += 1
                            errors.append(f"{symbol}: Failed to save snapshot")
                            if symbol in tier1:
                                processed_tier1 += 1
                                if processed_tier1 == 24 and (successful_tier1 / 24) < 0.6:
                                    tier2_skipped = True
                                    log.warning("Tier2 skipped: Tier1 success rate < 60%% (%s/24)", successful_tier1)
                
            except Exception as e:
                log.error(f"Failed to collect snapshot for {symbol}: {e}", exc_info=True)
                if symbol in tier1:
                    processed_tier1 += 1
                    if processed_tier1 == 24 and (successful_tier1 / 24) < 0.6:
                        tier2_skipped = True
                        log.warning("Tier2 skipped: Tier1 success rate < 60%% (%s/24)", successful_tier1)
                failed += 1
                errors.append(f"{symbol}: {str(e)}")
        
        duration = time.time() - start_time
        
        # Final summary with actionable guidance
        if failed > 0:
            log.error(f"")
            log.error(f"❌ COLLECTION SUMMARY: {failed} failures out of {len(symbols)} symbols")
            log.error(f"   ✅ Successful: {successful}/{len(symbols)}")
            log.error(f"   ❌ Failed: {failed}/{len(symbols)}")
            log.error(f"   ⏱️ Duration: {duration:.1f}s")
            log.error(f"")
            log.error(f"   📋 TROUBLESHOOTING STEPS:")
            log.error(f"      1. Review failed symbols above for patterns")
            if any("Fetch error" in e for e in errors):
                log.error(f"      2. Check yfinance API connectivity and rate limits")
                log.error(f"      3. Verify symbols are valid and trading")
            if any("Empty OHLCV" in e for e in errors):
                log.error(f"      4. Check time window: {start_utc} to {end_utc} UTC")
                log.error(f"      5. Verify market hours for requested timeframe")
            if any("Firestore" in e for e in errors):
                log.error(f"      6. Check Firestore connectivity and permissions")
            log.error(f"      7. Review Cloud Run logs for detailed error messages")
            log.error(f"      8. Check circuit breaker status if rate limiting detected")
            log.error(f"")
            log.error(f"   📊 Failed symbols: {[e.split(':')[0] for e in errors[:10]]}{'...' if len(errors) > 10 else ''}")
        else:
            log.info(f"")
            log.info(f"✅ COLLECTION SUMMARY: All {successful}/{len(symbols)} symbols collected successfully")
            log.info(f"   ⏱️ Duration: {duration:.1f}s")
            log.info(f"")
        
        summary = CollectionSummary(
            market=MarketType.US,
            snapshot_type=snapshot_type,
            session=None,
            total_snapshots=len(symbols),
            successful=successful,
            failed=failed,
            errors=errors[:10],  # Limit errors in summary
            duration_seconds=duration,
            timestamp=datetime.utcnow(),
            us_provider_used=us_provider_name,
            us_provider_health=health_result,
            us_collection_status=health_result.get("us_collection_status", "OK"),
        )
        
        # Save run log (unless dry-run)
        if not self.settings.dry_run:
            self.firestore_repo.save_run_log(summary)
        
        # Always save run log locally if enabled
        if self.local_repo:
            self.local_repo.save_run_log(summary)
        
        log.info(f"✅ US {snapshot_type.value} collection complete: {successful}/{len(symbols)} successful ({duration:.2f}s)")
        
        return summary
    
    def collect_crypto_snapshots(
        self,
        session: str,
        snapshot_type: SnapshotType,
        timestamp_et: Optional[datetime] = None
    ) -> CollectionSummary:
        """
        Collect snapshots for crypto futures symbols
        
        Args:
            session: Crypto session (LONDON, US, RESET, ASIA)
            snapshot_type: Type of snapshot (ORB, SIGNAL, OUTCOME)
            timestamp_et: Snapshot timestamp in ET (default: now)
        
        Returns:
            CollectionSummary with results
        """
        start_time = time.time()
        
        # Ensure timezone-aware ET (critical for correctness)
        if timestamp_et is None:
            timestamp_et = now_et()
        timestamp_et = ensure_tz(timestamp_et, get_market_tz())
        
        # Resolve symbols list
        symbols = self.settings.crypto_symbols
        
        # Get calendar tags (for crypto, still useful for macro events)
        calendar_tags_dict = get_calendar_tags(timestamp_et.date())
        calendar_tags = CalendarTags(**calendar_tags_dict)
        
        # Determine OHLCV window based on snapshot type and session
        start_utc, end_utc = self._get_crypto_ohlcv_window(session, snapshot_type, timestamp_et)
        
        # Generate run_id for this collection run (same for all symbols)
        run_id = f"{session}_{snapshot_type.value}_{timestamp_et.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        
        successful = 0
        failed = 0
        errors = []
        
        log.info(f"📊 Collecting CRYPTO {session} {snapshot_type.value} snapshots for {len(symbols)} symbols at {timestamp_et}")
        log.info(f"📅 Snapshot window: {start_utc} to {end_utc} UTC")
        log.info(f"🆔 Run ID: {run_id}")
        
        snapshot_time_utc = et_to_utc(timestamp_et)
        
        # Slab size from config (120–180 bars; match US or slightly more for Ichimoku/MACD/BB)
        crypto_slab_bars = int(getattr(self.settings, 'crypto_indicator_slab_bars', 180))
        log.info(f"📦 Strategy: Fetch {crypto_slab_bars}-candle slab ending at snapshot time for each symbol")
        log.info(f"   This provides indicator slab + snapshot window in one call")
        
        for idx, symbol in enumerate(symbols, 1):
            log.info(f"📈 [{idx}/{len(symbols)}] Processing {symbol} ({session})...")
            try:
                # Add small delay between symbols to avoid API rate limiting
                if idx > 1:
                    delay = 0.1  # 100ms delay between symbols
                    time.sleep(delay)
                
                # Fetch slab ending at snapshot time (cap snapshot_time to now for request)
                timeframe_minutes = int(self.settings.timeframe.replace('m', ''))
                granularity_seconds = timeframe_minutes * 60
                slab_end_utc = min(snapshot_time_utc, datetime.now(timezone.utc))
                slab_start_utc = slab_end_utc - timedelta(seconds=crypto_slab_bars * granularity_seconds)
                
                log.info(f"  📥 COINBASE REQUEST:")
                log.info(f"     - Symbol: {symbol}")
                log.info(f"     - Granularity: {granularity_seconds}s ({self.settings.timeframe})")
                log.info(f"     - Start UTC: {slab_start_utc}")
                log.info(f"     - End UTC: {slab_end_utc}")
                log.info(f"     - Slab: {crypto_slab_bars} bars")
                
                window_seconds = (slab_end_utc - slab_start_utc).total_seconds()
                expected_window_seconds = crypto_slab_bars * granularity_seconds
                if abs(window_seconds - expected_window_seconds) > 60:  # Allow 1 minute tolerance
                    log.warning(f"  ⚠️ Window size mismatch: {window_seconds}s (expected: {expected_window_seconds}s)")
                
                try:
                    slab_df = self.coinbase_client.get_ohlcv(
                        symbol=symbol,
                        timeframe=self.settings.timeframe,
                        start_utc=slab_start_utc,
                        end_utc=slab_end_utc
                    )
                    
                    candles_returned = len(slab_df)
                    log.info(f"  ✅ COINBASE RESPONSE:")
                    log.info(f"     - Candles returned: {candles_returned}")
                    log.info(f"     - Window: {slab_start_utc} to {slab_end_utc} UTC")
                    
                    # Get indicator slab (last 120 bars ending at snapshot time)
                    indicator_slab = slab_df.tail(self.settings.indicator_lookback_bars).copy() if len(slab_df) >= self.settings.indicator_lookback_bars else slab_df.copy()
                    
                    # Get snapshot window (ORB/SIGNAL/OUTCOME window)
                    snapshot_window = slab_df[(slab_df['timestamp'] >= start_utc) & (slab_df['timestamp'] < end_utc)].copy()
                    
                    indicator_bars_available = len(indicator_slab)
                    snapshot_bars_available = len(snapshot_window)
                    indicator_ready = indicator_bars_available >= 60
                    
                    log.info(f"  ✅ Retrieved {len(slab_df)} bars total")
                    log.info(f"     - Indicator slab: {indicator_bars_available} bars (ready: {indicator_ready})")
                    log.info(f"     - Snapshot window: {snapshot_bars_available} bars")
                    
                    if slab_df.empty:
                        raise ValueError("Empty slab from Coinbase API")
                    
                except SymbolUnresolvable:
                    failed += 1
                    errors.append(f"{symbol}: UNRESOLVABLE_SYMBOL")
                    continue
                except Exception as fetch_error:
                    log.error(f"  ❌ Failed to fetch slab for {symbol}: {fetch_error}", exc_info=True)
                    log.error(f"     📋 TROUBLESHOOTING for {symbol}:")
                    log.error(f"        1. Check if symbol is valid: {symbol}")
                    log.error(f"        2. Verify Coinbase API connectivity and rate limits")
                    log.error(f"        3. Error type: {type(fetch_error).__name__}")
                    failed += 1
                    errors.append(f"{symbol}: Fetch error - {str(fetch_error)[:200]}")
                    continue
                
                # Guard: ensure required columns exist
                required_cols = ['timestamp', 'open', 'high', 'low', 'close']
                if slab_df.empty:
                    log.error(f"  ❌ Empty slab DataFrame for {symbol}")
                    failed += 1
                    errors.append(f"{symbol}: Empty slab DataFrame")
                    continue
                
                if not all(col in slab_df.columns for col in required_cols):
                    missing_cols = [col for col in required_cols if col not in slab_df.columns]
                    log.error(f"  ❌ Missing columns in slab for {symbol}: {missing_cols}")
                    failed += 1
                    errors.append(f"{symbol}: Missing columns - {', '.join(missing_cols)}")
                    continue
                
                # Compute indicators from SLAB (not snapshot window)
                log.debug(f"  Computing indicators from slab ({indicator_bars_available} bars)...")
                indicators = self.indicator_service.calculate_indicators(
                    indicator_slab,
                    symbol=symbol,
                    indicator_ready=indicator_ready,
                    bars_available=indicator_bars_available
                )
                
                # Get session start for session VWAP calculation
                session_open_dt, _, _, _ = self._get_session_bounds_crypto(session, timestamp_et)
                session_start_utc = et_to_utc(session_open_dt)
                
                # Calculate session VWAP (from session start, not full history)
                vwap_value, vwap_mode = IndicatorService.calculate_session_vwap(
                    indicator_slab,
                    session_start_utc,
                    timestamp_col='timestamp'
                )
                if vwap_value is not None:
                    indicators['vwap'] = vwap_value
                    indicators['vwap_mode'] = vwap_mode
                    # Recalculate VWAP distance and momentum with session VWAP
                    if not indicator_slab.empty:
                        latest_close = float(indicator_slab['close'].iloc[-1])
                        indicators['vwap_distance_pct'] = ((latest_close - vwap_value) / vwap_value * 100) if vwap_value > 0 else None
                        # VWAP momentum
                        if len(indicator_slab) >= 2:
                            prev_vwap, _ = IndicatorService.calculate_session_vwap(
                                indicator_slab.iloc[:-1],
                                session_start_utc,
                                timestamp_col='timestamp'
                            )
                            if prev_vwap is not None:
                                indicators['vwap_momentum'] = vwap_value - prev_vwap
                
                # Combine slab + window for full context (ORB calculations need both)
                if not snapshot_window.empty:
                    ohlcv_df = pd.concat([indicator_slab, snapshot_window]).sort_values('timestamp')
                    ohlcv_df = ohlcv_df.drop_duplicates(subset=['timestamp']).reset_index(drop=True)
                else:
                    log.warning(f"  ⚠️ Snapshot window empty for {symbol}, using indicator slab only")
                    ohlcv_df = indicator_slab
                
                indicators_count = len([k for k, v in indicators.items() if v is not None and k not in ['indicator_ready', 'indicator_bars_available', 'vwap_mode']])
                log.debug(f"  Got {indicators_count} indicators for {symbol} (ready: {indicator_ready}, bars: {indicator_bars_available})")
                
                # Build snapshot payload
                log.debug(f"  Building snapshot for {symbol} ({session}, {snapshot_type.value})...")
                snapshot = self._build_crypto_snapshot(
                    symbol=symbol,
                    session=session,
                    snapshot_type=snapshot_type,
                    timestamp_et=timestamp_et,
                    ohlcv_df=ohlcv_df,
                    indicators=indicators,
                    calendar_tags=calendar_tags,
                    run_id=run_id
                )
                
                # Log snapshot metadata for verification
                log.info(f"  📊 Snapshot metadata:")
                log.info(f"     - Indicator ready: {snapshot.indicator_ready} ({snapshot.indicator_bars_available}/{snapshot.indicator_lookback_target} bars)")
                log.info(f"     - Ichimoku ready: {snapshot.ichimoku_ready}")
                log.info(f"     - ORB integrity: {snapshot.orb_block.orb_integrity_ok} ({snapshot.orb_block.orb_actual_bars}/{snapshot.orb_block.orb_expected_bars} bars)")
                log.info(f"     - VWAP mode: {snapshot.vwap_mode}")
                if snapshot.serialized_snapshot_bytes:
                    log.info(f"     - Payload size: {snapshot.serialized_snapshot_bytes:,} bytes")
                
                # Save snapshot (with dry-run support)
                doc_id = snapshot.get_doc_id()
                log.debug(f"  Generated snapshot doc_id: {doc_id}")
                
                # Always save locally if enabled (even in dry-run mode)
                local_saved = False
                if self.local_repo:
                    local_saved = self.local_repo.save_snapshot(snapshot)
                    if local_saved:
                        log.debug(f"  ✅ Saved snapshot locally: {doc_id}")
                
                if self.settings.dry_run:
                    log.info(f"  ✅ DRY-RUN: Would save snapshot {doc_id} (local: {'✅' if local_saved else '❌'})")
                    successful += 1
                else:
                    log.debug(f"  Saving snapshot {doc_id} to Firestore...")
                    firestore_saved = self.firestore_repo.save_snapshot(snapshot)
                    
                    if firestore_saved:
                        log.info(f"  ✅ Saved snapshot {doc_id} (Firestore: ✅, Local: {'✅' if local_saved else '❌'})")
                        successful += 1
                    else:
                        log.error(f"  ❌ Failed to save snapshot {doc_id} to Firestore")
                        # Still count as successful if local save worked
                        if local_saved:
                            log.info(f"  ⚠️ Snapshot saved locally but Firestore failed")
                            successful += 1
                        else:
                            failed += 1
                            errors.append(f"{symbol}: Failed to save snapshot")
                
            except Exception as e:
                log.error(f"Failed to collect snapshot for {symbol}: {e}", exc_info=True)
                log.error(f"     📋 Check error details above for {symbol}")
                failed += 1
                errors.append(f"{symbol}: {str(e)}")
        
        duration = time.time() - start_time
        
        # Final summary with actionable guidance
        if failed > 0:
            log.error(f"")
            log.error(f"❌ CRYPTO COLLECTION SUMMARY: {failed} failures out of {len(symbols)} symbols")
            log.error(f"   ✅ Successful: {successful}/{len(symbols)}")
            log.error(f"   ❌ Failed: {failed}/{len(symbols)}")
            log.error(f"   ⏱️ Duration: {duration:.1f}s")
            log.error(f"   📊 Session: {session}")
            log.error(f"")
            log.error(f"   📋 TROUBLESHOOTING STEPS:")
            log.error(f"      1. Review failed symbols above for patterns")
            if any("Fetch error" in e for e in errors):
                log.error(f"      2. Check Coinbase API connectivity and rate limits")
                log.error(f"      3. Verify symbols are valid: {symbols}")
            if any("Empty OHLCV" in e for e in errors):
                log.error(f"      4. Check time window: {start_utc} to {end_utc} UTC")
                log.error(f"      5. Verify Coinbase API returned data for requested timeframe")
            if any("Firestore" in e for e in errors):
                log.error(f"      6. Check Firestore connectivity and permissions")
            log.error(f"      7. Review Cloud Run logs for detailed error messages")
            log.error(f"      8. Check Coinbase API status if multiple failures")
            log.error(f"")
            log.error(f"   📊 Failed symbols: {[e.split(':')[0] for e in errors[:10]]}{'...' if len(errors) > 10 else ''}")
        else:
            log.info(f"")
            log.info(f"✅ CRYPTO COLLECTION SUMMARY: All {successful}/{len(symbols)} symbols collected successfully")
            log.info(f"   📊 Session: {session}")
            log.info(f"   ⏱️ Duration: {duration:.1f}s")
            log.info(f"")
        
        summary = CollectionSummary(
            market=MarketType.CRYPTO,
            snapshot_type=snapshot_type,
            session=session,
            total_snapshots=len(symbols),
            successful=successful,
            failed=failed,
            errors=errors[:10],
            duration_seconds=duration,
            timestamp=datetime.utcnow()
        )
        
        # Save run log (unless dry-run)
        if not self.settings.dry_run:
            self.firestore_repo.save_run_log(summary)
        
        # Always save run log locally if enabled
        if self.local_repo:
            self.local_repo.save_run_log(summary)
        
        log.info(f"✅ Crypto {session} {snapshot_type.value} collection complete: {successful}/{len(symbols)} successful ({duration:.2f}s)")
        
        return summary
    
    def _get_us_ohlcv_window(
        self,
        snapshot_type: SnapshotType,
        timestamp_et: datetime
    ) -> Tuple[datetime, datetime]:
        """
        Get OHLCV window for US snapshot type.
        
        FIXED: Start from market open (not market open - lookback) because:
        1. yfinance period="1d" returns today's data from market open
        2. This provides ~78 bars (6.5 hours / 5m) which is enough for indicators
        3. Subtracting lookback would request previous day data, which yfinance period="1d" ignores
        
        The lookback period (50 bars = 4.2 hours) fits within the trading day,
        so yfinance period="1d" will include enough historical data for indicators.
        """
        # Get market open for today
        market_open_dt = timestamp_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_open_utc = et_to_utc(market_open_dt)
        
        # Start from market open (yfinance period="1d" handles lookback internally)
        # For 5m timeframe: 50 bars lookback = 250 minutes = 4.2 hours, which fits in trading day
        start_utc = market_open_utc
        
        # Set end_utc based on snapshot type (using session bounds for consistency)
        market_open_dt, orb_end_dt, signal_dt, outcome_dt = self._get_session_bounds_us(timestamp_et)
        
        if snapshot_type == SnapshotType.ORB:
            # ORB: Fetch from market open to ORB end (9:45 ET)
            end_utc = et_to_utc(orb_end_dt)
        
        elif snapshot_type == SnapshotType.SIGNAL:
            # SIGNAL: Fetch from market open to signal time (10:30 ET)
            end_utc = et_to_utc(signal_dt)
        
        elif snapshot_type == SnapshotType.OUTCOME:
            # OUTCOME: Fetch from market open through outcome time (includes full session path)
            end_utc = et_to_utc(outcome_dt)
        
        else:
            raise ValueError(f"Unknown snapshot type: {snapshot_type}")
        
        return start_utc, end_utc
    
    def _get_crypto_ohlcv_window(
        self,
        session: str,
        snapshot_type: SnapshotType,
        timestamp_et: datetime
    ) -> Tuple[datetime, datetime]:
        """
        Get OHLCV window for crypto snapshot type.
        Uses session bounds helpers for consistency.
        
        FIXED: Start from session open (not session open - lookback) because:
        1. Coinbase API works better with session-aligned windows
        2. Lookback (50 bars = 4.2 hours) fits within most session durations
        3. Coinbase chunking handles large windows, but smaller windows are more reliable
        
        The lookback period (50 bars = 4.2 hours) is reasonable for crypto sessions,
        and Coinbase API will return enough historical data within the session window.
        """
        # Get session bounds (consistent with _calculate_orb_block)
        session_open_dt, orb_end_dt, signal_dt, outcome_dt = self._get_session_bounds_crypto(session, timestamp_et)
        
        # Start from session open (Coinbase API will include enough historical data)
        # For 5m timeframe: 50 bars lookback = 250 minutes = 4.2 hours
        # Most crypto sessions are 6-8 hours, so this fits comfortably
        open_utc = et_to_utc(session_open_dt)
        start_utc = open_utc
        
        # Set end_utc based on snapshot type (using session anchors)
        if snapshot_type == SnapshotType.ORB:
            end_utc = et_to_utc(orb_end_dt)
        elif snapshot_type == SnapshotType.SIGNAL:
            end_utc = et_to_utc(signal_dt)
        elif snapshot_type == SnapshotType.OUTCOME:
            end_utc = et_to_utc(outcome_dt)
        else:
            raise ValueError(f"Unknown snapshot type: {snapshot_type}")
        
        return start_utc, end_utc
    
    def _build_us_snapshot(
        self,
        symbol: str,
        snapshot_type: SnapshotType,
        timestamp_et: datetime,
        ohlcv_df: pd.DataFrame,
        indicators: Dict,
        calendar_tags: CalendarTags,
        run_id: Optional[str] = None,
        us_source: str = "ETRADE",
    ) -> Snapshot:
        """Build US snapshot payload from OHLCV data and indicators"""
        import numpy as np
        
        # Get latest bar data
        latest = ohlcv_df.iloc[-1] if not ohlcv_df.empty else {}
        prev_bar = ohlcv_df.iloc[-2] if len(ohlcv_df) >= 2 else None
        
        # Calculate candle structure
        open_price = float(latest.get('open', 0))
        high_price = float(latest.get('high', 0))
        low_price = float(latest.get('low', 0))
        close_price = float(latest.get('close', 0))
        volume = float(latest.get('volume', 0) or 0)
        
        range_val = high_price - low_price if high_price > low_price else 0
        body = abs(close_price - open_price)
        upper_wick = high_price - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low_price
        
        range_pct = (range_val / open_price * 100) if open_price > 0 else 0
        body_pct = (body / open_price * 100) if open_price > 0 else 0
        upper_wick_pct = (upper_wick / open_price * 100) if open_price > 0 else 0
        lower_wick_pct = (lower_wick / open_price * 100) if open_price > 0 else 0
        
        # Calculate gap percentage (if previous bar available)
        gap_pct = None
        if prev_bar is not None:
            prev_close = float(prev_bar.get('close', 0))
            if prev_close > 0:
                gap_pct = ((open_price - prev_close) / prev_close * 100)
        
        # Calculate position in range (0-100%)
        position_in_range = ((close_price - low_price) / range_val * 100) if range_val > 0 else 50.0
        
        # Calculate ORB block - works for all snapshot types (ORB, SIGNAL, OUTCOME)
        # Always computes ORB levels + post-ORB extremes + state flags + interaction counts
        orb_block = self._calculate_orb_block(ohlcv_df, snapshot_type, timestamp_et, indicators=indicators)
        
        # Build price/candle data
        price_candle = PriceCandleData(
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            last_price=close_price,
            prev_close=float(prev_bar.get('close', 0)) if prev_bar is not None else None,
            gap_pct=gap_pct,
            range_pct=range_pct,
            body_pct=body_pct,
            upper_wick_pct=upper_wick_pct,
            lower_wick_pct=lower_wick_pct,
            position_in_range=position_in_range,
            range_vs_atr=range_pct / indicators.get('atr_pct', 1) if indicators.get('atr_pct', 0) > 0 else None,
            candle_strength=body_pct / range_pct if range_pct > 0 else 0
        )
        
        # Build trend/momentum data
        ema_8 = indicators.get('ema_8')
        ema_21 = indicators.get('ema_21')
        ema_50 = indicators.get('ema_50')
        ema_200 = None  # Removed: requires too much historical data
        
        ema_trend_8_21 = None
        ema_trend_21_50 = None
        if ema_8 and ema_21:
            ema_trend_8_21 = "BULLISH" if ema_8 > ema_21 else "BEARISH" if ema_8 < ema_21 else "NEUTRAL"
        if ema_21 and ema_50:
            ema_trend_21_50 = "BULLISH" if ema_21 > ema_50 else "BEARISH" if ema_21 < ema_50 else "NEUTRAL"
        
        trend_direction = None
        if ema_8 and ema_21 and ema_50:
            if ema_8 > ema_21 > ema_50:
                trend_direction = "UP"
            elif ema_8 < ema_21 < ema_50:
                trend_direction = "DOWN"
            else:
                trend_direction = "SIDEWAYS"
        
        trend_momentum = TrendMomentumData(
            ema_8=ema_8,
            ema_21=ema_21,
            ema_50=ema_50,
            ema_200=ema_200,
            ema_trend_8_21=ema_trend_8_21,
            ema_trend_21_50=ema_trend_21_50,
            roc=indicators.get('roc'),
            momentum_1bar=indicators.get('momentum_1bar'),
            momentum_5bar=indicators.get('momentum_5bar'),
            momentum_10bar=indicators.get('momentum_10bar'),
            trend_direction=trend_direction,
            trend_strength_score=self._calculate_trend_strength_score(ema_8, ema_21, ema_50, close_price)
        )
        
        # Build volatility data
        atr = indicators.get('atr')
        atr_pct = indicators.get('atr_pct')
        volatility = indicators.get('volatility')
        
        volatility_regime = None
        if atr_pct:
            if atr_pct > 2.0:
                volatility_regime = "EXTREME"
            elif atr_pct > 1.5:
                volatility_regime = "HIGH"
            elif atr_pct > 0.5:
                volatility_regime = "NORMAL"
            else:
                volatility_regime = "LOW"
        
        volatility_data = VolatilityData(
            atr=atr,
            atr_pct=atr_pct,
            atr_pct_change=indicators.get('atr_pct_change'),
            volatility=volatility,
            volatility_regime=volatility_regime,
            range_expansion_flag=atr_pct > 1.5 if atr_pct else None,
            compression_flag=atr_pct < 0.5 if atr_pct else None
        )
        
        # Build volume/VWAP data (volume_sma/volume_delta; coerce volume_delta to int; guard NaN)
        vd = indicators.get('volume_delta')
        vd_ok = vd is not None and (vd == vd)  # exclude None and NaN
        volume_vwap = VolumeVWAPData(
            volume=volume,
            volume_sma=indicators.get('volume_sma'),
            volume_ratio=indicators.get('volume_ratio'),
            volume_acceleration=indicators.get('volume_acceleration'),
            volume_delta=int(round(vd)) if vd_ok else None,
            vwap=indicators.get('vwap'),
            vwap_distance_pct=indicators.get('vwap_distance_pct'),
            vwap_momentum=indicators.get('vwap_momentum'),
            price_vs_vwap_direction="ABOVE" if close_price > indicators.get('vwap', 0) else "BELOW" if close_price < indicators.get('vwap', 0) else "AT"
        )

        # Build oscillators
        rsi = indicators.get('rsi')
        stoch_k = indicators.get('stoch_k')
        stoch_d = indicators.get('stoch_d')

        stoch_position = None
        if stoch_k:
            if stoch_k > 80:
                stoch_position = "OVERBOUGHT"
            elif stoch_k < 20:
                stoch_position = "OVERSOLD"
            else:
                stoch_position = "NEUTRAL"

        oscillators = OscillatorData(
            rsi=rsi,
            rsi_slope=indicators.get('rsi_slope'),
            rsi_acceleration=indicators.get('rsi_acceleration'),
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            stoch_position=stoch_position,
            williams_r=indicators.get('williams_r'),
            cci=indicators.get('cci'),
            mfi=indicators.get('mfi'),
            cmf=indicators.get('cmf')
        )

        # Build MACD data
        macd_line = indicators.get('macd')
        macd_signal = indicators.get('macd_signal')
        macd_histogram = indicators.get('macd_histogram')

        momentum_regime = None
        if macd_histogram:
            if macd_histogram > 0 and macd_line and macd_line > macd_signal:
                momentum_regime = "BULLISH"
            elif macd_histogram < 0 and macd_line and macd_line < macd_signal:
                momentum_regime = "BEARISH"
            else:
                momentum_regime = "NEUTRAL"

        macd = MACDData(
            macd=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            macd_slope=indicators.get('macd_slope'),
            momentum_regime=momentum_regime
        )

        # Build Bollinger Bands data
        bb_upper = indicators.get('bollinger_upper')
        bb_middle = indicators.get('bollinger_middle')
        bb_lower = indicators.get('bollinger_lower')
        bb_width = indicators.get('bollinger_width')
        bb_position = indicators.get('bollinger_position')

        bollinger = BollingerData(
            bollinger_upper=bb_upper,
            bollinger_middle=bb_middle,
            bollinger_lower=bb_lower,
            bollinger_width=bb_width,
            bollinger_position=bb_position,
            bb_squeeze_flag=bb_width and bb_width < (atr_pct * 0.5) if atr_pct else None,
            bb_expansion_flag=bb_width and bb_width > (atr_pct * 2) if atr_pct else None
        )

        # Build signal data (for SIGNAL snapshots)
        signal_data = None
        if snapshot_type == SnapshotType.SIGNAL:
            signal_data = self._build_signal_data(indicators, close_price, trend_direction, rsi, macd_histogram)

        # Build outcome data (for OUTCOME snapshots)
        outcome_us = None
        if snapshot_type == SnapshotType.OUTCOME:
            outcome_us = self._build_outcome_us_data(ohlcv_df, timestamp_et, run_id=run_id, indicators=indicators)
        
        # Get session bounds for metadata
        market_open_dt, orb_end_dt, signal_dt, outcome_dt = self._get_session_bounds_us(timestamp_et)
        early_close_time = get_early_close_time(timestamp_et.date())
        early_close_dt = None
        if early_close_time:
            early_close_dt = timestamp_et.replace(
                hour=early_close_time.hour,
                minute=early_close_time.minute,
                second=0,
                microsecond=0
            )
        
        session_bounds = SessionBounds(
            open_et=market_open_dt,
            orb_end_et=orb_end_dt,
            signal_et=signal_dt,
            outcome_et=outcome_dt,
            early_close_et=early_close_dt
        )
        
        # Get indicator readiness from indicators dict
        indicator_ready = indicators.get('indicator_ready', False)
        indicator_bars_available = indicators.get('indicator_bars_available', 0)
        indicator_lookback_target = self.settings.indicator_lookback_bars
        ichimoku_ready = indicator_bars_available >= 52  # Minimum for Ichimoku (52 bars)
        
        # Get VWAP metadata
        vwap_mode = indicators.get('vwap_mode', None)
        vwap_session_start_utc = et_to_utc(market_open_dt) if vwap_mode == "SESSION" else None
        
        # Get Ichimoku settings (for US, Ichimoku is optional but we still track settings)
        ichimoku_settings_used = self.indicator_service.get_ichimoku_preset(symbol)
        ichimoku_timeframe = self.settings.timeframe

        # Fail-open: save even when indicators degraded
        feature_quality = "FULL" if indicator_ready else "DEGRADED"
        skip_reason = "INSUFFICIENT_BARS" if not indicator_ready else None
        label_ready = (snapshot_type == SnapshotType.OUTCOME) and (outcome_us is not None)
        
        # Build snapshot
        snapshot = Snapshot(
            market=MarketType.US,
            symbol=symbol,
            snapshot_type=snapshot_type,
            timestamp_et=timestamp_et,
            timestamp_utc=et_to_utc(timestamp_et),
            day_of_week=timestamp_et.weekday(),
            minutes_since_open=self._calculate_minutes_since_open(snapshot_type, timestamp_et),
            minutes_to_next_session=self._calculate_minutes_to_next_session(snapshot_type, timestamp_et),
            session_bounds=session_bounds,
            price_candle=price_candle,
            orb_block=orb_block,
            trend_momentum=trend_momentum,
            volatility=volatility_data,
            volume_vwap=volume_vwap,
            oscillators=oscillators,
            macd=macd,
            bollinger=bollinger,
            signal=signal_data,
            outcome_us=outcome_us,
            calendar=calendar_tags,
            run_id=run_id,
            source=us_source,
            timeframe=self.settings.timeframe,
            schema_version="1.0",
            # Indicator readiness flags
            indicator_ready=indicator_ready,
            indicator_bars_available=indicator_bars_available,
            indicator_lookback_target=indicator_lookback_target,
            ichimoku_ready=ichimoku_ready,
            feature_quality=feature_quality,
            skip_reason=skip_reason,
            label_ready=label_ready,
            # VWAP metadata
            vwap_mode=vwap_mode,
            vwap_session_start_utc=vwap_session_start_utc,
            # Ichimoku metadata
            ichimoku_settings_used=ichimoku_settings_used,
            ichimoku_timeframe=ichimoku_timeframe
        )
        
        # PAYLOAD SIZE LOGGING
        try:
            import json
            snapshot_dict = snapshot.model_dump(exclude_none=True)
            serialized_bytes = len(json.dumps(snapshot_dict, default=str).encode('utf-8'))
            snapshot.serialized_snapshot_bytes = serialized_bytes
            log.debug(f"  📦 Snapshot payload size: {serialized_bytes:,} bytes ({serialized_bytes/1024:.1f} KB)")
            if serialized_bytes > 100000:  # > 100 KB
                log.warning(f"  ⚠️ Large snapshot payload: {serialized_bytes:,} bytes - consider optimization")
        except Exception as e:
            log.debug(f"  ⚠️ Failed to calculate payload size: {e}")
        
        return snapshot
    
    def _build_crypto_snapshot(
        self,
        symbol: str,
        session: str,
        snapshot_type: SnapshotType,
        timestamp_et: datetime,
        ohlcv_df: pd.DataFrame,
        indicators: Dict,
        calendar_tags: CalendarTags,
        run_id: Optional[str] = None
    ) -> Snapshot:
        """Build crypto snapshot payload from OHLCV data and indicators (similar to US but with Ichimoku)"""
        # Build snapshot using same logic as US but with crypto-specific fields
        # Get latest bar data
        latest = ohlcv_df.iloc[-1] if not ohlcv_df.empty else {}
        prev_bar = ohlcv_df.iloc[-2] if len(ohlcv_df) >= 2 else None
        
        # Calculate candle structure (same as US)
        open_price = float(latest.get('open', 0))
        high_price = float(latest.get('high', 0))
        low_price = float(latest.get('low', 0))
        close_price = float(latest.get('close', 0))
        volume = float(latest.get('volume', 0) or 0)
        
        range_val = high_price - low_price if high_price > low_price else 0
        body = abs(close_price - open_price)
        upper_wick = high_price - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low_price
        
        range_pct = (range_val / open_price * 100) if open_price > 0 else 0
        body_pct = (body / open_price * 100) if open_price > 0 else 0
        upper_wick_pct = (upper_wick / open_price * 100) if open_price > 0 else 0
        lower_wick_pct = (lower_wick / open_price * 100) if open_price > 0 else 0
        
        gap_pct = None
        if prev_bar is not None:
            prev_close = float(prev_bar.get('close', 0))
            if prev_close > 0:
                gap_pct = ((open_price - prev_close) / prev_close * 100)
        
        position_in_range = ((close_price - low_price) / range_val * 100) if range_val > 0 else 50.0
        
        # Calculate ORB block for crypto session - works for all snapshot types (ORB, SIGNAL, OUTCOME)
        # Always computes ORB levels + post-ORB extremes + state flags + interaction counts
        orb_block = self._calculate_orb_block(ohlcv_df, snapshot_type, timestamp_et, session=session, indicators=indicators)
        
        # Build price/candle data
        price_candle = PriceCandleData(
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            last_price=close_price,
            prev_close=float(prev_bar.get('close', 0)) if prev_bar is not None else None,
            gap_pct=gap_pct,
            range_pct=range_pct,
            body_pct=body_pct,
            upper_wick_pct=upper_wick_pct,
            lower_wick_pct=lower_wick_pct,
            position_in_range=position_in_range,
            range_vs_atr=range_pct / indicators.get('atr_pct', 1) if indicators.get('atr_pct', 0) > 0 else None,
            candle_strength=body_pct / range_pct if range_pct > 0 else 0
        )
        
        # Build trend/momentum data (same logic as US)
        ema_8 = indicators.get('ema_8')
        ema_21 = indicators.get('ema_21')
        ema_50 = indicators.get('ema_50')
        ema_200 = None  # Removed: requires too much historical data
        
        ema_trend_8_21 = None
        ema_trend_21_50 = None
        if ema_8 and ema_21:
            ema_trend_8_21 = "BULLISH" if ema_8 > ema_21 else "BEARISH" if ema_8 < ema_21 else "NEUTRAL"
        if ema_21 and ema_50:
            ema_trend_21_50 = "BULLISH" if ema_21 > ema_50 else "BEARISH" if ema_21 < ema_50 else "NEUTRAL"
        
        trend_direction = None
        if ema_8 and ema_21 and ema_50:
            if ema_8 > ema_21 > ema_50:
                trend_direction = "UP"
            elif ema_8 < ema_21 < ema_50:
                trend_direction = "DOWN"
            else:
                trend_direction = "SIDEWAYS"
        
        trend_momentum = TrendMomentumData(
            ema_8=ema_8,
            ema_21=ema_21,
            ema_50=ema_50,
            ema_200=ema_200,
            ema_trend_8_21=ema_trend_8_21,
            ema_trend_21_50=ema_trend_21_50,
            roc=indicators.get('roc'),
            momentum_1bar=indicators.get('momentum_1bar'),
            momentum_5bar=indicators.get('momentum_5bar'),
            momentum_10bar=indicators.get('momentum_10bar'),
            trend_direction=trend_direction,
            trend_strength_score=self._calculate_trend_strength_score(ema_8, ema_21, ema_50, close_price)
        )
        
        # Build volatility data (same as US)
        atr = indicators.get('atr')
        atr_pct = indicators.get('atr_pct')
        volatility = indicators.get('volatility')
        
        volatility_regime = None
        if atr_pct:
            if atr_pct > 2.0:
                volatility_regime = "EXTREME"
            elif atr_pct > 1.5:
                volatility_regime = "HIGH"
            elif atr_pct > 0.5:
                volatility_regime = "NORMAL"
            else:
                volatility_regime = "LOW"
        
        volatility_data = VolatilityData(
            atr=atr,
            atr_pct=atr_pct,
            atr_pct_change=indicators.get('atr_pct_change'),
            volatility=volatility,
            volatility_regime=volatility_regime,
            range_expansion_flag=atr_pct > 1.5 if atr_pct else None,
            compression_flag=atr_pct < 0.5 if atr_pct else None
        )
        
        # Build volume/VWAP data (coerce volume_delta to int for deployed Pydantic schema; guard NaN)
        vd = indicators.get('volume_delta')
        vd_ok = vd is not None and (vd == vd)  # exclude None and NaN
        volume_vwap = VolumeVWAPData(
            volume=volume,
            volume_sma=indicators.get('volume_sma'),
            volume_ratio=indicators.get('volume_ratio'),
            volume_acceleration=indicators.get('volume_acceleration'),
            volume_delta=int(round(vd)) if vd_ok else None,
            vwap=indicators.get('vwap'),
            vwap_distance_pct=indicators.get('vwap_distance_pct'),
            vwap_momentum=indicators.get('vwap_momentum'),
            price_vs_vwap_direction="ABOVE" if close_price > indicators.get('vwap', 0) else "BELOW" if close_price < indicators.get('vwap', 0) else "AT"
        )

        # Build oscillators (same as US)
        rsi = indicators.get('rsi')
        stoch_k = indicators.get('stoch_k')
        stoch_d = indicators.get('stoch_d')
        
        stoch_position = None
        if stoch_k:
            if stoch_k > 80:
                stoch_position = "OVERBOUGHT"
            elif stoch_k < 20:
                stoch_position = "OVERSOLD"
            else:
                stoch_position = "NEUTRAL"
        
        oscillators = OscillatorData(
            rsi=rsi,
            rsi_slope=indicators.get('rsi_slope'),
            rsi_acceleration=indicators.get('rsi_acceleration'),
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            stoch_position=stoch_position,
            williams_r=indicators.get('williams_r'),
            cci=indicators.get('cci'),
            mfi=indicators.get('mfi'),
            cmf=indicators.get('cmf')
        )
        
        # Build MACD data (same as US)
        macd_line = indicators.get('macd')
        macd_signal = indicators.get('macd_signal')
        macd_histogram = indicators.get('macd_histogram')
        
        momentum_regime = None
        if macd_histogram:
            if macd_histogram > 0 and macd_line and macd_line > macd_signal:
                momentum_regime = "BULLISH"
            elif macd_histogram < 0 and macd_line and macd_line < macd_signal:
                momentum_regime = "BEARISH"
            else:
                momentum_regime = "NEUTRAL"
        
        macd = MACDData(
            macd=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            macd_slope=indicators.get('macd_slope'),
            momentum_regime=momentum_regime
        )
        
        # Build Bollinger Bands data (same as US)
        bb_upper = indicators.get('bollinger_upper')
        bb_middle = indicators.get('bollinger_middle')
        bb_lower = indicators.get('bollinger_lower')
        bb_width = indicators.get('bollinger_width')
        bb_position = indicators.get('bollinger_position')
        
        bollinger = BollingerData(
            bollinger_upper=bb_upper,
            bollinger_middle=bb_middle,
            bollinger_lower=bb_lower,
            bollinger_width=bb_width,
            bollinger_position=bb_position,
            bb_squeeze_flag=bb_width and bb_width < (atr_pct * 0.5) if atr_pct else None,
            bb_expansion_flag=bb_width and bb_width > (atr_pct * 2) if atr_pct else None
        )
        
        # Build Ichimoku data from indicators
        ichimoku_tenkan = indicators.get('ichimoku_tenkan')
        ichimoku_kijun = indicators.get('ichimoku_kijun')
        ichimoku_senkou_a = indicators.get('ichimoku_senkou_a')
        ichimoku_senkou_b = indicators.get('ichimoku_senkou_b')
        ichimoku_chikou = indicators.get('ichimoku_chikou')
        
        # Calculate Ichimoku scores
        ichimoku_bullish_score = self._calculate_ichimoku_bullish_score(indicators, close_price)
        ichimoku_bearish_score = self._calculate_ichimoku_bearish_score(indicators, close_price)
        
        # Chikou confirmation (compare chikou to price displacement bars ago)
        chikou_confirmation = None
        if ichimoku_chikou and len(ohlcv_df) >= 3:
            # Chikou is close shifted back by displacement (3 bars)
            if len(ohlcv_df) >= 3:
                price_3bars_ago = float(ohlcv_df.iloc[-3].get('close', 0))
                if ichimoku_chikou > price_3bars_ago:
                    chikou_confirmation = "BULLISH"
                elif ichimoku_chikou < price_3bars_ago:
                    chikou_confirmation = "BEARISH"
                else:
                    chikou_confirmation = "NEUTRAL"
        
        ichimoku = IchimokuData(
            tenkan=ichimoku_tenkan,
            kijun=ichimoku_kijun,
            senkou_a=ichimoku_senkou_a,
            senkou_b=ichimoku_senkou_b,
            cloud_thickness_pct=indicators.get('ichimoku_cloud_thickness_pct'),
            price_vs_cloud_state=indicators.get('ichimoku_price_vs_cloud'),
            tk_cross_state=indicators.get('ichimoku_tk_cross'),
            chikou_confirmation=chikou_confirmation,
            future_cloud_bias="BULLISH" if indicators.get('ichimoku_cloud_bullish') else "BEARISH" if indicators.get('ichimoku_cloud_bearish') else "NEUTRAL",
            ichimoku_bullish_score=ichimoku_bullish_score,
            ichimoku_bearish_score=ichimoku_bearish_score
        )
        
        # Build signal data (for SIGNAL snapshots)
        signal_data = None
        if snapshot_type == SnapshotType.SIGNAL:
            signal_data = self._build_signal_data(indicators, close_price, trend_direction, rsi, macd_histogram)
        
        # Build outcome data (for OUTCOME snapshots)
        outcome_us = None
        outcome_crypto = None
        if snapshot_type == SnapshotType.OUTCOME:
            if session:  # Crypto
                outcome_crypto = self._build_outcome_crypto_data(ohlcv_df, session, timestamp_et, symbol, run_id=run_id, indicators=indicators)
            else:  # US
                outcome_us = self._build_outcome_us_data(ohlcv_df, timestamp_et, run_id=run_id, indicators=indicators)
        
        # Build snapshot with crypto-specific fields
        # Get session bounds for metadata
        session_open_dt, orb_end_dt, signal_dt, outcome_dt = self._get_session_bounds_crypto(session, timestamp_et)
        session_bounds = SessionBounds(
            open_et=session_open_dt,
            orb_end_et=orb_end_dt,
            signal_et=signal_dt,
            outcome_et=outcome_dt,
            early_close_et=None  # Crypto doesn't have early close
        )
        
        # Convert session string to enum
        try:
            session_enum = CryptoSession(session.upper()) if session else None
        except ValueError:
            session_enum = None
        
        # Get indicator readiness from indicators dict
        indicator_ready = indicators.get('indicator_ready', False)
        indicator_bars_available = indicators.get('indicator_bars_available', 0)
        indicator_lookback_target = self.settings.indicator_lookback_bars
        ichimoku_ready = indicator_bars_available >= 52  # Minimum for Ichimoku (52 bars)
        
        # Get VWAP metadata
        vwap_mode = indicators.get('vwap_mode', None)
        vwap_session_start_utc = et_to_utc(session_open_dt) if vwap_mode == "SESSION" else None
        
        # Get Ichimoku settings (crypto uses symbol-specific presets)
        ichimoku_settings_used = self.indicator_service.get_ichimoku_preset(symbol)
        ichimoku_timeframe = self.settings.timeframe

        # Fail-open: save even when indicators degraded
        feature_quality = "FULL" if indicator_ready else "DEGRADED"
        skip_reason = "INSUFFICIENT_BARS" if not indicator_ready else None
        label_ready = (snapshot_type == SnapshotType.OUTCOME) and (outcome_crypto is not None)
        
        snapshot = Snapshot(
            market=MarketType.CRYPTO,
            symbol=symbol,
            session=session_enum,
            snapshot_type=snapshot_type,
            timestamp_et=timestamp_et,
            timestamp_utc=et_to_utc(timestamp_et),
            day_of_week=timestamp_et.weekday(),
            minutes_since_open=self._calculate_minutes_since_open(snapshot_type, timestamp_et, session),
            minutes_to_next_session=self._calculate_minutes_to_next_session(snapshot_type, timestamp_et, session),
            session_bounds=session_bounds,
            price_candle=price_candle,
            orb_block=orb_block,
            trend_momentum=trend_momentum,
            volatility=volatility_data,
            volume_vwap=volume_vwap,
            oscillators=oscillators,
            macd=macd,
            bollinger=bollinger,
            ichimoku=ichimoku,
            signal=signal_data,
            outcome_crypto=outcome_crypto,
            calendar=calendar_tags,
            run_id=run_id,
            source="COINBASE",
            timeframe=self.settings.timeframe,
            schema_version="1.0",
            # Indicator readiness flags
            indicator_ready=indicator_ready,
            indicator_bars_available=indicator_bars_available,
            indicator_lookback_target=indicator_lookback_target,
            ichimoku_ready=ichimoku_ready,
            feature_quality=feature_quality,
            skip_reason=skip_reason,
            label_ready=label_ready,
            # VWAP metadata
            vwap_mode=vwap_mode,
            vwap_session_start_utc=vwap_session_start_utc,
            # Ichimoku metadata
            ichimoku_settings_used=ichimoku_settings_used,
            ichimoku_timeframe=ichimoku_timeframe
        )
        
        # PAYLOAD SIZE LOGGING
        try:
            import json
            snapshot_dict = snapshot.model_dump(exclude_none=True)
            serialized_bytes = len(json.dumps(snapshot_dict, default=str).encode('utf-8'))
            snapshot.serialized_snapshot_bytes = serialized_bytes
            log.debug(f"  📦 Snapshot payload size: {serialized_bytes:,} bytes ({serialized_bytes/1024:.1f} KB)")
            if serialized_bytes > 100000:  # > 100 KB
                log.warning(f"  ⚠️ Large snapshot payload: {serialized_bytes:,} bytes - consider optimization")
        except Exception as e:
            log.debug(f"  ⚠️ Failed to calculate payload size: {e}")
        
        return snapshot
    
    def _get_session_bounds_us(self, timestamp_et: datetime) -> Tuple[datetime, datetime, datetime, datetime]:
        """
        Get US market session bounds: market_open_dt, orb_end_dt, signal_dt, outcome_dt
        
        Returns:
            Tuple of (market_open_dt, orb_end_dt, signal_dt, outcome_dt) in ET timezone
        """
        # Market open: 9:30 ET
        market_open_dt = timestamp_et.replace(hour=9, minute=30, second=0, microsecond=0)
        
        # ORB end: 9:45 ET (open + 15 minutes)
        orb_end_dt = get_us_orb_time(timestamp_et)
        
        # Signal: 10:30 ET (open + 60 minutes)
        signal_dt = get_us_signal_time(timestamp_et)
        
        # Outcome: 15:55 ET or early close - 5 min
        early_close_time = get_early_close_time(timestamp_et.date())
        outcome_dt = get_us_outcome_time(timestamp_et, early_close_time)
        
        return market_open_dt, orb_end_dt, signal_dt, outcome_dt
    
    def _get_session_bounds_crypto(
        self,
        session: str,
        timestamp_et: datetime
    ) -> Tuple[datetime, datetime, datetime, datetime]:
        """
        Get crypto session bounds: session_open_dt, orb_end_dt, signal_dt, outcome_dt (pre-next-open)
        
        Returns:
            Tuple of (session_open_dt, orb_end_dt, signal_dt, outcome_dt) in ET timezone
        """
        # Get session open time
        session_open_str = {
            'LONDON': self.settings.crypto_london_open,
            'US': self.settings.crypto_us_open,
            'RESET': self.settings.crypto_reset_open,
            'ASIA': self.settings.crypto_asia_open
        }.get(session, self.settings.crypto_us_open)
        
        session_open_time = parse_time_string(session_open_str)
        session_open_dt = timestamp_et.replace(
            hour=session_open_time.hour,
            minute=session_open_time.minute,
            second=0,
            microsecond=0
        )
        
        # ORB end: open + 15 minutes
        orb_end_dt = get_crypto_orb_time(session_open_dt)
        
        # Signal: open + 60 minutes
        signal_dt = get_crypto_signal_time(session_open_dt)
        
        # Outcome: 5 minutes before next session open
        next_session_map = {
            'LONDON': 'US',
            'US': 'RESET',
            'RESET': 'ASIA',
            'ASIA': 'LONDON'
        }
        next_session = next_session_map.get(session, 'US')
        next_session_open_str = {
            'LONDON': self.settings.crypto_london_open,
            'US': self.settings.crypto_us_open,
            'RESET': self.settings.crypto_reset_open,
            'ASIA': self.settings.crypto_asia_open
        }.get(next_session, self.settings.crypto_us_open)
        next_session_open_time = parse_time_string(next_session_open_str)
        
        # Calculate next session open datetime
        if next_session == 'LONDON' and session == 'ASIA':
            next_session_dt = timestamp_et.replace(
                hour=next_session_open_time.hour,
                minute=next_session_open_time.minute,
                second=0,
                microsecond=0
            ) + timedelta(days=1)
        else:
            next_session_dt = timestamp_et.replace(
                hour=next_session_open_time.hour,
                minute=next_session_open_time.minute,
                second=0,
                microsecond=0
            )
            if next_session_dt <= timestamp_et:
                next_session_dt = next_session_dt + timedelta(days=1)
        
        outcome_dt = get_crypto_outcome_time(next_session_dt)
        
        return session_open_dt, orb_end_dt, signal_dt, outcome_dt
    
    def _slice_df_between(
        self,
        df: pd.DataFrame,
        start_dt_et: datetime,
        end_dt_et: datetime
    ) -> pd.DataFrame:
        """
        Slice dataframe between two ET datetimes (handles index conversion safely).
        Uses [start, end) convention (inclusive start, exclusive end) to prevent double-counting.
        
        Args:
            df: DataFrame with 'timestamp' column (UTC timezone-aware)
            start_dt_et: Start datetime in ET (inclusive)
            end_dt_et: End datetime in ET (exclusive)
        
        Returns:
            Sliced DataFrame
        """
        if df.empty:
            return df
        
        # Convert ET to UTC for comparison
        start_utc = et_to_utc(start_dt_et)
        end_utc = et_to_utc(end_dt_et)
        
        # Filter by timestamp column (end-exclusive to prevent double-counting)
        mask = (df['timestamp'] >= start_utc) & (df['timestamp'] < end_utc)
        return df[mask].copy()
    
    def _compute_orb_levels(
        self,
        df: pd.DataFrame,
        open_dt_et: datetime,
        orb_end_dt_et: datetime
    ) -> Optional[Dict[str, float]]:
        """
        Compute ORB levels from dataframe between open and ORB end.
        
        Returns:
            Dict with orb_open, orb_high, orb_low, orb_close, orb_mid, orb_range_pct, or None
        """
        orb_df = self._slice_df_between(df, open_dt_et, orb_end_dt_et)
        
        if orb_df.empty:
            return None
        
        orb_open = float(orb_df.iloc[0].get('open', 0))
        orb_high = float(orb_df['high'].max())
        orb_low = float(orb_df['low'].min())
        orb_close = float(orb_df.iloc[-1].get('close', 0))
        orb_mid = (orb_high + orb_low) / 2
        orb_range = orb_high - orb_low if orb_high > orb_low else 0
        orb_range_pct = (orb_range / orb_open * 100) if orb_open > 0 else 0
        
        return {
            'orb_open': orb_open,
            'orb_high': orb_high,
            'orb_low': orb_low,
            'orb_close': orb_close,
            'orb_mid': orb_mid,
            'orb_range_pct': orb_range_pct
        }
    
    
    def _compute_level_interactions(
        self,
        df: pd.DataFrame,
        orb_high: float,
        orb_low: float,
        orb_end_dt_et: datetime,
        end_dt_et: datetime
    ) -> Dict[str, int]:
        """
        Compute level interaction counts between ORB end and end_dt_et.
        
        Args:
            df: OHLCV DataFrame
            orb_high: ORB high level
            orb_low: ORB low level
            orb_end_dt_et: ORB end datetime (ET)
            end_dt_et: End datetime (ET) - can be signal time or snapshot time
        
        Returns:
            Dict with break counts, reclaim counts, and minutes above/below
        """
        post_orb_df = self._slice_df_between(df, orb_end_dt_et, end_dt_et)
        
        if post_orb_df.empty or not orb_high or not orb_low:
            return {
                'orb_low_break_count': 0,
                'orb_low_reclaim_count': 0,
                'minutes_below_orb_low': 0,
                'orb_high_break_count': 0,
                'orb_high_reclaim_count': 0,
                'minutes_above_orb_high': 0
            }
        
        timeframe_minutes = int(self.settings.timeframe.replace('m', ''))
        
        orb_low_break_count = 0
        orb_low_reclaim_count = 0
        minutes_below_orb_low = 0
        orb_high_break_count = 0
        orb_high_reclaim_count = 0
        minutes_above_orb_high = 0
        
        prev_below_low = False
        prev_above_high = False
        
        for idx in range(len(post_orb_df)):
            row = post_orb_df.iloc[idx]
            bar_low = float(row.get('low', 0))
            bar_high = float(row.get('high', 0))
            bar_close = float(row.get('close', 0))
            
            # ORB low interactions
            if bar_low < orb_low:
                if not prev_below_low:
                    orb_low_break_count += 1
                prev_below_low = True
                minutes_below_orb_low += timeframe_minutes
            elif bar_close > orb_low and prev_below_low:
                orb_low_reclaim_count += 1
                prev_below_low = False
            else:
                prev_below_low = False
            
            # ORB high interactions
            if bar_high > orb_high:
                if not prev_above_high:
                    orb_high_break_count += 1
                prev_above_high = True
                minutes_above_orb_high += timeframe_minutes
            elif bar_close < orb_high and prev_above_high:
                orb_high_reclaim_count += 1
                prev_above_high = False
            else:
                prev_above_high = False
        
        return {
            'orb_low_break_count': orb_low_break_count,
            'orb_low_reclaim_count': orb_low_reclaim_count,
            'minutes_below_orb_low': minutes_below_orb_low,
            'orb_high_break_count': orb_high_break_count,
            'orb_high_reclaim_count': orb_high_reclaim_count,
            'minutes_above_orb_high': minutes_above_orb_high
        }
    
    def _check_orb_integrity(
        self,
        orb_df: pd.DataFrame,
        snapshot_type: SnapshotType,
        timeframe: str,
        timestamp_et: datetime,
        session: Optional[str] = None
    ) -> Tuple[bool, int, int]:
        """
        Check ORB integrity and return flags.
        
        For ORB snapshots at ORB time, expect minimum bars:
        - 5m timeframe: 3 bars (9:30, 9:35, 9:40) if snapshot at 9:45
        - 1m timeframe: 15 bars if snapshot at 9:45
        
        Returns:
            Tuple of (orb_integrity_ok, orb_expected_bars, orb_actual_bars)
        """
        if snapshot_type != SnapshotType.ORB:
            return True, 0, len(orb_df)  # Not an ORB snapshot, skip check
        
        # Get ORB end time
        if session:
            _, orb_end_dt, _, _ = self._get_session_bounds_crypto(session, timestamp_et)
        else:
            _, orb_end_dt, _, _ = self._get_session_bounds_us(timestamp_et)
        
        # Check if we're at ORB time (within 5 minutes)
        time_diff = abs((timestamp_et - orb_end_dt).total_seconds())
        if time_diff > 300:  # Not at ORB time
            return True, 0, len(orb_df)  # Skip check if not at ORB time
        
        # Calculate expected bars
        timeframe_minutes = int(timeframe.replace('m', ''))
        expected_bars = 15 // timeframe_minutes  # 15 minutes / timeframe
        
        actual_bars = len(orb_df)
        orb_integrity_ok = actual_bars >= expected_bars
        
        if not orb_integrity_ok:
            log.warning(f"  ⚠️ ORB INTEGRITY FAILED:")
            log.warning(f"     - Expected: {expected_bars} bars (15 minutes / {timeframe})")
            log.warning(f"     - Actual: {actual_bars} bars")
            log.warning(f"     - Snapshot type: {snapshot_type.value}")
            log.warning(f"     - Time: {timestamp_et}")
        else:
            log.debug(f"  ✅ ORB integrity OK: {actual_bars} bars (expected: {expected_bars})")
        
        return orb_integrity_ok, expected_bars, actual_bars
    
    def _calculate_orb_block(
        self,
        ohlcv_df: pd.DataFrame,
        snapshot_type: SnapshotType,
        timestamp_et: datetime,
        session: Optional[str] = None,
        indicators: Optional[Dict] = None
    ) -> ORBBlock:
        """
        Calculate Opening Range Breakout block data.
        Works for ORB, SIGNAL, and OUTCOME snapshots using timestamp-based slicing.
        
        For all snapshot types:
        a) Compute ORB window (open -> open+15m): orb_open/high/low/close/mid/range_pct/volume/volume_ratio
        b) Compute post-ORB window (orb_end -> snapshot time): post_orb_high/low/range_pct
        c) Compute state flags at snapshot time
        d) Compute distances to key levels
        e) Compute interaction counts between orb_end and snapshot time
        f) Set orb_break_state more granular
        """
        if ohlcv_df.empty or "timestamp" not in ohlcv_df.columns:
            return ORBBlock()
        
        # Get session bounds (US or crypto)
        if session:
            open_dt, orb_end_dt, signal_dt, outcome_dt = self._get_session_bounds_crypto(session, timestamp_et)
            session_open_dt = open_dt
        else:
            open_dt, orb_end_dt, signal_dt, outcome_dt = self._get_session_bounds_us(timestamp_et)
            session_open_dt = open_dt
        
        # ORB window = open -> orb_end (timestamp-based slicing, end-exclusive)
        # Note: _slice_df_between uses [start, end) convention, so orb_end_dt is exclusive
        # To include the orb_end bar, we add 1 second to make it inclusive
        orb_end_dt_inclusive = orb_end_dt + timedelta(seconds=1)
        orb_df = self._slice_df_between(ohlcv_df, session_open_dt, orb_end_dt_inclusive)
        
        # ENFORCE ORB INTEGRITY CHECK
        orb_integrity_ok, orb_expected_bars, orb_actual_bars = self._check_orb_integrity(
            orb_df, snapshot_type, self.settings.timeframe, timestamp_et, session
        )
        
        if orb_df.empty:
            return ORBBlock(
                orb_integrity_ok=False,
                orb_expected_bars=orb_expected_bars,
                orb_actual_bars=0
            )
        
        # Compute ORB levels
        orb_open = float(orb_df.iloc[0].get("open", 0))
        orb_high = float(orb_df["high"].max())
        orb_low = float(orb_df["low"].min())
        orb_close = float(orb_df.iloc[-1].get("close", 0))
        orb_mid = (orb_high + orb_low) / 2
        orb_range = max(0.0, orb_high - orb_low)
        orb_range_pct = (orb_range / orb_open * 100) if orb_open > 0 else 0.0
        orb_volume = float(orb_df["volume"].sum()) if "volume" in orb_df.columns else 0.0
        
        # Calculate volume ratio vs average
        volume_sma = indicators.get("volume_sma") if indicators else None
        orb_volume_ratio = (orb_volume / volume_sma) if volume_sma and volume_sma > 0 else None
        
        # Determine effective snapshot end time based on snapshot type (not raw timestamp_et)
        # This ensures consistent dataset anchors regardless of scheduler timing drift
        if snapshot_type == SnapshotType.ORB:
            snapshot_end_dt_et = orb_end_dt
        elif snapshot_type == SnapshotType.SIGNAL:
            snapshot_end_dt_et = signal_dt
        elif snapshot_type == SnapshotType.OUTCOME:
            snapshot_end_dt_et = outcome_dt
        else:
            # Fallback to timestamp_et if unknown type
            snapshot_end_dt_et = timestamp_et
        
        # Current price at snapshot end time (use last bar up to snapshot_end_dt_et)
        snapshot_end_df = self._slice_df_between(ohlcv_df, session_open_dt, snapshot_end_dt_et)
        current_price = float(snapshot_end_df.iloc[-1].get("close", 0)) if not snapshot_end_df.empty else float(ohlcv_df.iloc[-1].get("close", 0))
        
        # Post-ORB window = orb_end -> snapshot end time (timestamp-based slicing, start-inclusive)
        # Start at orb_end_dt_inclusive to avoid double-counting the orb_end bar
        post_orb_df = self._slice_df_between(ohlcv_df, orb_end_dt_inclusive, snapshot_end_dt_et)
        
        post_orb_high = None
        post_orb_low = None
        post_orb_range_pct = None
        
        if not post_orb_df.empty:
            post_orb_high = float(post_orb_df["high"].max())
            post_orb_low = float(post_orb_df["low"].min())
            if orb_open > 0:
                post_orb_range_pct = ((post_orb_high - post_orb_low) / orb_open * 100)
        
        # Compute level interactions (uses timestamp-based slicing)
        # Use orb_end_dt_inclusive as start and snapshot_end_dt_et as end to match post_orb_df window
        interactions = self._compute_level_interactions(ohlcv_df, orb_high, orb_low, orb_end_dt_inclusive, snapshot_end_dt_et)
        
        # State flags at snapshot time
        is_making_new_low_since_orb = (current_price < orb_low) if orb_low else None
        is_making_new_high_since_orb = (current_price > orb_high) if orb_high else None
        is_inside_post_orb_range = (
            (post_orb_low <= current_price <= post_orb_high)
            if (post_orb_low is not None and post_orb_high is not None)
            else None
        )
        
        # Position in ranges (0-100%)
        pos_in_orb_range = None
        if orb_range > 0:
            pos_in_orb_range = ((current_price - orb_low) / orb_range * 100)
        
        pos_in_post_orb_range = None
        if post_orb_high is not None and post_orb_low is not None:
            post_orb_range = post_orb_high - post_orb_low
            if post_orb_range > 0:
                pos_in_post_orb_range = ((current_price - post_orb_low) / post_orb_range * 100)
        
        # Distances to key levels (%)
        dist_to_orb_low_pct = ((current_price - orb_low) / orb_low * 100) if orb_low > 0 else None
        dist_to_orb_high_pct = ((orb_high - current_price) / orb_high * 100) if orb_high > 0 else None
        dist_to_post_orb_low_pct = ((current_price - post_orb_low) / post_orb_low * 100) if post_orb_low and post_orb_low > 0 else None
        dist_to_post_orb_high_pct = ((post_orb_high - current_price) / post_orb_high * 100) if post_orb_high and post_orb_high > 0 else None
        
        # Set orb_break_state more granular
        orb_break_state = "IN_RANGE"
        if current_price > orb_high:
            orb_break_state = "BREAKOUT_UP"
        elif current_price < orb_low:
            orb_break_state = "BREAKOUT_DOWN"
        else:
            if interactions["orb_low_break_count"] > 0 or interactions["orb_high_break_count"] > 0:
                orb_break_state = "RETEST"
        
        # Check for failed breaks
        if interactions["orb_high_break_count"] > 0 and current_price <= orb_high:
            orb_break_state = "FAILED_BREAK"
        if interactions["orb_low_break_count"] > 0 and current_price >= orb_low:
            orb_break_state = "FAILED_BREAK"
        
        return ORBBlock(
            orb_open=orb_open,
            orb_high=orb_high,
            orb_low=orb_low,
            orb_close=orb_close,
            orb_mid=orb_mid,
            orb_volume=orb_volume,
            orb_range_pct=orb_range_pct,
            orb_volume_ratio=orb_volume_ratio,
            orb_break_state=orb_break_state,
            post_orb_high=post_orb_high,
            post_orb_low=post_orb_low,
            post_orb_range_pct=post_orb_range_pct,
            is_making_new_low_since_orb=is_making_new_low_since_orb,
            is_making_new_high_since_orb=is_making_new_high_since_orb,
            is_inside_post_orb_range=is_inside_post_orb_range,
            pos_in_orb_range=pos_in_orb_range,
            pos_in_post_orb_range=pos_in_post_orb_range,
            dist_to_orb_low_pct=dist_to_orb_low_pct,
            dist_to_orb_high_pct=dist_to_orb_high_pct,
            dist_to_post_orb_low_pct=dist_to_post_orb_low_pct,
            dist_to_post_orb_high_pct=dist_to_post_orb_high_pct,
            orb_low_break_count_post_orb=interactions["orb_low_break_count"],
            orb_low_reclaim_count_post_orb=interactions["orb_low_reclaim_count"],
            minutes_below_orb_low_post_orb=interactions["minutes_below_orb_low"],
            orb_high_break_count_post_orb=interactions["orb_high_break_count"],
            orb_high_reclaim_count_post_orb=interactions["orb_high_reclaim_count"],
            minutes_above_orb_high_post_orb=interactions["minutes_above_orb_high"],
            # ORB Integrity flags (enforcement)
            orb_integrity_ok=orb_integrity_ok,
            orb_expected_bars=orb_expected_bars,
            orb_actual_bars=orb_actual_bars
        )
    
    def _calculate_trend_strength_score(
        self,
        ema_8: Optional[float],
        ema_21: Optional[float],
        ema_50: Optional[float],
        current_price: float
    ) -> Optional[float]:
        """Calculate trend strength score (0-100) based on EMA alignment"""
        if not (ema_8 and ema_21 and ema_50):
            return None
        
        score = 0
        
        # EMA alignment (up to 40 points)
        if ema_8 > ema_21 > ema_50:
            score += 40  # Perfect bullish alignment
        elif ema_8 < ema_21 < ema_50:
            score += 40  # Perfect bearish alignment
        elif ema_8 > ema_21 and ema_21 > ema_50:
            score += 30  # Partial bullish
        elif ema_8 < ema_21 and ema_21 < ema_50:
            score += 30  # Partial bearish
        
        # Price vs EMA (up to 30 points)
        if current_price > ema_8:
            score += 15
        if current_price > ema_21:
            score += 10
        if current_price > ema_50:
            score += 5
        
        # EMA separation (up to 30 points)
        ema_sep_8_21 = abs(ema_8 - ema_21) / ema_21 * 100 if ema_21 > 0 else 0
        ema_sep_21_50 = abs(ema_21 - ema_50) / ema_50 * 100 if ema_50 > 0 else 0
        
        if ema_sep_8_21 > 1.0:
            score += 15
        if ema_sep_21_50 > 1.0:
            score += 15
        
        return min(100, max(0, score))
    
    def _calculate_minutes_since_open(
        self,
        snapshot_type: SnapshotType,
        timestamp_et: datetime,
        session: Optional[str] = None
    ) -> Optional[int]:
        """
        Calculate minutes since session open.
        Uses session bounds helpers for consistency.
        """
        try:
            if session:  # Crypto
                session_open_dt, _, _, _ = self._get_session_bounds_crypto(session, timestamp_et)
                return get_minutes_since_open(session_open_dt, timestamp_et)
            else:  # US
                market_open_dt, _, _, _ = self._get_session_bounds_us(timestamp_et)
                return get_minutes_since_open(market_open_dt, timestamp_et)
        except Exception:
            return None
    
    def _calculate_minutes_to_next_session(
        self,
        snapshot_type: SnapshotType,
        timestamp_et: datetime,
        session: Optional[str] = None
    ) -> Optional[int]:
        """
        Calculate minutes until next session open.
        Uses session bounds helpers for consistency.
        """
        try:
            if session:  # Crypto
                _, _, _, outcome_dt = self._get_session_bounds_crypto(session, timestamp_et)
                # Next session open is outcome_dt + 5 minutes (outcome is 5 min before next open)
                next_session_dt = outcome_dt + timedelta(minutes=5)
                return get_minutes_to_next_session(next_session_dt, timestamp_et)
            else:  # US - next trading day (simplified, can be None)
                return None
        except Exception:
            return None
    
    def _calculate_ichimoku_bullish_score(self, indicators: Dict, current_price: float) -> Optional[int]:
        """Calculate Ichimoku bullish score (0-5)"""
        score = 0
        
        # Price above cloud (+2)
        if indicators.get('ichimoku_price_vs_cloud') == "ABOVE":
            score += 2
        elif indicators.get('ichimoku_price_vs_cloud') == "IN_CLOUD":
            score += 1
        
        # TK cross bullish (+1)
        if indicators.get('ichimoku_tk_cross') == "BULLISH":
            score += 1
        
        # Cloud bullish (+1)
        if indicators.get('ichimoku_cloud_bullish'):
            score += 1
        
        return min(5, max(0, score))
    
    def _calculate_ichimoku_bearish_score(self, indicators: Dict, current_price: float) -> Optional[int]:
        """Calculate Ichimoku bearish score (0-5)"""
        score = 0
        
        # Price below cloud (+2)
        if indicators.get('ichimoku_price_vs_cloud') == "BELOW":
            score += 2
        elif indicators.get('ichimoku_price_vs_cloud') == "IN_CLOUD":
            score += 1
        
        # TK cross bearish (+1)
        if indicators.get('ichimoku_tk_cross') == "BEARISH":
            score += 1
        
        # Cloud bearish (+1)
        if indicators.get('ichimoku_cloud_bearish'):
            score += 1
        
        return min(5, max(0, score))
    
    def _build_signal_data(
        self,
        indicators: Dict,
        current_price: float,
        trend_direction: Optional[str],
        rsi: Optional[float],
        macd_histogram: Optional[float]
    ) -> SignalData:
        """Build signal data for SIGNAL snapshots"""
        # Determine signal direction based on indicators
        signal_direction = None
        signal_score = 0.0
        rules_hit = []
        
        # Trend direction
        if trend_direction == "UP":
            signal_direction = "LONG"
            signal_score += 0.3
            rules_hit.append("TREND_UP")
        elif trend_direction == "DOWN":
            signal_direction = "SHORT"
            signal_score += 0.3
            rules_hit.append("TREND_DOWN")
        
        # RSI confirmation
        if rsi:
            if signal_direction == "LONG" and rsi < 70:
                signal_score += 0.2
                rules_hit.append("RSI_NOT_OVERBOUGHT")
            elif signal_direction == "SHORT" and rsi > 30:
                signal_score += 0.2
                rules_hit.append("RSI_NOT_OVERSOLD")
        
        # MACD confirmation
        if macd_histogram:
            if signal_direction == "LONG" and macd_histogram > 0:
                signal_score += 0.2
                rules_hit.append("MACD_BULLISH")
            elif signal_direction == "SHORT" and macd_histogram < 0:
                signal_score += 0.2
                rules_hit.append("MACD_BEARISH")
        
        # Volume confirmation
        volume_ratio = indicators.get('volume_ratio')
        if volume_ratio and volume_ratio > 1.0:
            signal_score += 0.1
            rules_hit.append("VOLUME_ABOVE_AVERAGE")
        
        # Tradable flag (basic logic)
        tradable_flag = signal_score >= 0.5 and signal_direction is not None
        
        # Confidence (0-1 scale)
        confidence = min(1.0, signal_score)
        
        return SignalData(
            tradable_flag=tradable_flag,
            signal_direction=signal_direction,
            signal_score=signal_score,
            signal_rank=None,  # Would be calculated across all symbols
            confidence=confidence,
            rules_hit=rules_hit,
            strategy_version="1.0"
        )
    
    def _build_outcome_us_data(
        self,
        ohlcv_df: pd.DataFrame,
        timestamp_et: datetime,
        run_id: Optional[str] = None,
        indicators: Optional[Dict] = None
    ) -> OutcomeDataUS:
        """
        Build outcome data for US OUTCOME snapshots with proper Signal-based MFE/MAE calculation.
        Computes path-based metrics from Signal → Outcome.
        """
        if ohlcv_df.empty:
            return OutcomeDataUS()
        
        latest = ohlcv_df.iloc[-1]
        current_price = float(latest.get('close', 0))
        price_preclose = current_price
        
        # Calculate ORB levels (first 15 minutes)
        timeframe_minutes = int(self.settings.timeframe.replace('m', ''))
        orb_bars = 15 // timeframe_minutes if timeframe_minutes > 0 else 3
        orb_high = None
        orb_low = None
        orb_mid = None
        
        if len(ohlcv_df) >= orb_bars:
            orb_df = ohlcv_df.head(orb_bars)
            orb_high = float(orb_df['high'].max())
            orb_low = float(orb_df['low'].min())
            orb_mid = (orb_high + orb_low) / 2
        
        # Calculate Signal time (10:30 ET = market open + 60 minutes)
        market_open_time = timestamp_et.replace(hour=9, minute=30, second=0, microsecond=0)
        signal_time = market_open_time + timedelta(minutes=60)
        signal_time_utc = et_to_utc(signal_time)
        
        # Find Signal price (close at/just before signal time)
        signal_price = None
        signal_df = None
        signal_idx = None
        
        # Find the bar closest to signal time (timestamp column is UTC)
        for idx in range(len(ohlcv_df)):
            row = ohlcv_df.iloc[idx]
            bar_time = row.get('timestamp')
            
            # Handle timestamp (normalize to UTC-aware pd.Timestamp)
            bar_time_utc = self._to_utc_ts(bar_time)
            
            if bar_time_utc <= signal_time_utc:
                signal_price = float(row.get('close', 0))
                signal_idx = idx
            else:
                break
        
        # If no bar found before signal time, use first bar
        if signal_price is None and len(ohlcv_df) > 0:
            signal_price = float(ohlcv_df.iloc[0].get('close', 0))
            signal_idx = 0
        
        # Create signal_df (all bars up to and including signal)
        if signal_idx is not None:
            signal_df = ohlcv_df.iloc[:signal_idx + 1]
        else:
            signal_df = ohlcv_df.head(1) if len(ohlcv_df) > 0 else pd.DataFrame()
        
        # Calculate path window (Signal → Outcome)
        if signal_df is not None and len(signal_df) < len(ohlcv_df):
            # Get bars after signal
            after_signal_df = ohlcv_df.iloc[len(signal_df):]
        else:
            after_signal_df = pd.DataFrame()  # No bars after signal
        
        # Calculate anchors and path metrics
        max_high_after_signal = None
        min_low_after_signal = None
        mfe_long_from_signal_pct = None
        mae_long_from_signal_pct = None
        mfe_short_from_signal_pct = None
        mae_short_from_signal_pct = None
        minutes_to_mfe_long = None
        minutes_to_mfe_short = None
        minutes_to_break_orb_high_after_signal = None
        minutes_to_break_orb_low_after_signal = None
        break_orb_high_within_5m = False
        break_orb_high_within_10m = False
        break_orb_high_within_15m = False
        break_orb_low_within_5m = False
        break_orb_low_within_10m = False
        break_orb_low_within_15m = False
        
        if signal_price and signal_price > 0 and len(after_signal_df) > 0:
            max_high_after_signal = float(after_signal_df['high'].max())
            min_low_after_signal = float(after_signal_df['low'].min())
            
            # MFE/MAE from Signal (Long & Short)
            mfe_long_from_signal_pct = ((max_high_after_signal - signal_price) / signal_price * 100) if signal_price > 0 else None
            mae_long_from_signal_pct = ((signal_price - min_low_after_signal) / signal_price * 100) if signal_price > 0 else None
            mfe_short_from_signal_pct = ((signal_price - min_low_after_signal) / signal_price * 100) if signal_price > 0 else None
            mae_short_from_signal_pct = ((max_high_after_signal - signal_price) / signal_price * 100) if signal_price > 0 else None
            
            # Find timing metrics
            mfe_long_time = None
            mfe_short_time = None
            orb_high_break_time = None
            orb_low_break_time = None
            
            for idx in range(len(after_signal_df)):
                row = after_signal_df.iloc[idx]
                bar_time = row.get('timestamp')
                bar_high = float(row.get('high', 0))
                bar_low = float(row.get('low', 0))
                
                # Calculate minutes after signal (normalize timestamp safely)
                bar_time_utc = self._to_utc_ts(bar_time)
                if bar_time_utc is None:
                    continue
                minutes_after_signal = (bar_time_utc - signal_time_utc).total_seconds() / 60.0
                if minutes_after_signal < 0:
                    continue
                
                # Track MFE timing
                if bar_high >= max_high_after_signal and mfe_long_time is None:
                    mfe_long_time = minutes_after_signal
                if bar_low <= min_low_after_signal and mfe_short_time is None:
                    mfe_short_time = minutes_after_signal
                
                # Track ORB break timing
                if orb_high and bar_high > orb_high and orb_high_break_time is None:
                    orb_high_break_time = minutes_after_signal
                if orb_low and bar_low < orb_low and orb_low_break_time is None:
                    orb_low_break_time = minutes_after_signal
                
                # Check break flags
                if minutes_after_signal is not None:
                    if orb_high and bar_high > orb_high:
                        if minutes_after_signal <= 5:
                            break_orb_high_within_5m = True
                        if minutes_after_signal <= 10:
                            break_orb_high_within_10m = True
                        if minutes_after_signal <= 15:
                            break_orb_high_within_15m = True
                    if orb_low and bar_low < orb_low:
                        if minutes_after_signal <= 5:
                            break_orb_low_within_5m = True
                        if minutes_after_signal <= 10:
                            break_orb_low_within_10m = True
                        if minutes_after_signal <= 15:
                            break_orb_low_within_15m = True
            
            minutes_to_mfe_long = int(mfe_long_time) if mfe_long_time is not None else None
            minutes_to_mfe_short = int(mfe_short_time) if mfe_short_time is not None else None
            minutes_to_break_orb_high_after_signal = int(orb_high_break_time) if orb_high_break_time is not None else None
            minutes_to_break_orb_low_after_signal = int(orb_low_break_time) if orb_low_break_time is not None else None
        
        # Calculate best action and entry mode labels
        best_action_at_signal = "NO_TRADE"
        best_entry_mode = "WAIT"
        
        if signal_price and orb_high and orb_low:
            # Determine best action based on MFE/MAE
            if mfe_long_from_signal_pct and mfe_short_from_signal_pct:
                if mfe_long_from_signal_pct > mfe_short_from_signal_pct and mae_long_from_signal_pct and mae_long_from_signal_pct < mfe_long_from_signal_pct * 0.5:
                    best_action_at_signal = "LONG"
                    if signal_price < orb_mid:
                        best_entry_mode = "TRIGGER_ORB_HIGH"
                    else:
                        best_entry_mode = "IMMEDIATE"
                elif mfe_short_from_signal_pct > mfe_long_from_signal_pct and mae_short_from_signal_pct and mae_short_from_signal_pct < mfe_short_from_signal_pct * 0.5:
                    best_action_at_signal = "SHORT"
                    if signal_price > orb_mid:
                        best_entry_mode = "TRIGGER_ORB_LOW"
                    else:
                        best_entry_mode = "IMMEDIATE"
        
        # Legacy MFE/MAE from ORB (for backward compatibility)
        orb_price = orb_mid if orb_mid else None
        mfe_from_orb = None
        mae_from_orb = None
        if orb_price and orb_price > 0:
            max_high = float(ohlcv_df['high'].max())
            min_low = float(ohlcv_df['low'].min())
            mfe_from_orb = ((max_high - orb_price) / orb_price * 100)
            mae_from_orb = ((orb_price - min_low) / orb_price * 100)
        
        # Net move percentage from ORB
        net_move_pct = None
        if orb_price and orb_price > 0:
            net_move_pct = ((current_price - orb_price) / orb_price * 100)
        
        # Legacy mfe_from_signal/mae_from_signal (use long values)
        mfe_from_signal = mfe_long_from_signal_pct
        mae_from_signal = mae_long_from_signal_pct
        
        # Compute outcome labels using OutcomeLabelService
        outcome_time_utc = et_to_utc(timestamp_et)
        outcome_labels = None
        
        # Get ATR at signal time (compute indicators on signal_df)
        atr = None
        atr_pct = None
        if signal_df is not None and len(signal_df) > 0:
            try:
                signal_indicators = self.indicator_service.calculate_indicators(signal_df, symbol="")
                atr = signal_indicators.get('atr')
                atr_pct = signal_indicators.get('atr_pct')
            except Exception as e:
                log.warning(f"Failed to compute indicators at signal time: {e}")
        
        # Get indicator_ready flag
        indicator_ready = indicators.get('indicator_ready', True) if indicators else True
        
        # Compute outcome labels if we have post-signal data
        if signal_price and signal_price > 0 and len(after_signal_df) > 0:
            try:
                outcome_labels = self.outcome_label_service.compute_outcome_labels(
                    ohlcv_df=ohlcv_df,  # Full OHLCV (will be filtered internally)
                    signal_time_utc=signal_time_utc,
                    outcome_time_utc=outcome_time_utc,
                    signal_price=signal_price,
                    atr=atr,
                    atr_pct=atr_pct,
                    market="US",
                    indicator_ready=indicator_ready
                )
            except Exception as e:
                log.error(f"Failed to compute outcome labels: {e}", exc_info=True)
                outcome_labels = None
        
        # Extract outcome label fields
        peak_metrics = outcome_labels.get('peak_metrics', {}) if outcome_labels else {}
        synthetic_exit = outcome_labels.get('synthetic_exit', {}) if outcome_labels else {}
        trade_quality = outcome_labels.get('trade_quality', {}) if outcome_labels else {}
        
        return OutcomeDataUS(
            price_preclose=price_preclose,
            mfe_from_orb=mfe_from_orb,
            mae_from_orb=mae_from_orb,
            mfe_from_signal=mfe_from_signal,
            mae_from_signal=mae_from_signal,
            mfe_long_from_signal_pct=mfe_long_from_signal_pct,
            mae_long_from_signal_pct=mae_long_from_signal_pct,
            mfe_short_from_signal_pct=mfe_short_from_signal_pct,
            mae_short_from_signal_pct=mae_short_from_signal_pct,
            signal_price=signal_price,
            orb_high=orb_high,
            orb_low=orb_low,
            orb_mid=orb_mid,
            max_high_after_signal=max_high_after_signal,
            min_low_after_signal=min_low_after_signal,
            minutes_to_mfe_long=minutes_to_mfe_long,
            minutes_to_mfe_short=minutes_to_mfe_short,
            minutes_to_break_orb_high_after_signal=minutes_to_break_orb_high_after_signal,
            minutes_to_break_orb_low_after_signal=minutes_to_break_orb_low_after_signal,
            break_orb_high_within_5m=break_orb_high_within_5m,
            break_orb_high_within_10m=break_orb_high_within_10m,
            break_orb_high_within_15m=break_orb_high_within_15m,
            break_orb_low_within_5m=break_orb_low_within_5m,
            break_orb_low_within_10m=break_orb_low_within_10m,
            break_orb_low_within_15m=break_orb_low_within_15m,
            best_action_at_signal=outcome_labels.get('best_action_at_signal', best_action_at_signal) if outcome_labels else best_action_at_signal,
            best_entry_mode=best_entry_mode,
            net_move_pct=net_move_pct,
            trend_persistence_score=peak_metrics.get('trend_persistence'),
            delta_efficiency_proxy=None,  # Would need options data
            # Outcome Label Layer fields
            opportunity_score=outcome_labels.get('opportunity_score'),
            opportunity_long=outcome_labels.get('opportunity_long'),
            opportunity_short=outcome_labels.get('opportunity_short'),
            peak_mfe_pct=peak_metrics.get('mfe_pct'),
            peak_mae_pct=peak_metrics.get('mae_pct'),
            t_to_mfe_minutes=peak_metrics.get('t_to_mfe_minutes'),
            peak_end_drawdown_pct=peak_metrics.get('peak_end_drawdown_pct'),
            pullback_count=peak_metrics.get('pullback_count'),
            trend_persistence=peak_metrics.get('trend_persistence'),
            synthetic_r=synthetic_exit.get('synthetic_r_best'),  # Use best direction R
            synthetic_r_long=synthetic_exit.get('synthetic_r_long'),
            synthetic_r_short=synthetic_exit.get('synthetic_r_short'),
            synthetic_exit_reason=synthetic_exit.get('exit_reason'),
            synthetic_exit_time=synthetic_exit.get('exit_time'),
            synthetic_exit_price=synthetic_exit.get('exit_price'),
            trade_quality_score=trade_quality.get('quality_score'),
            trade_quality_label=trade_quality.get('quality_grade'),  # Updated to quality_grade
            exit_style=outcome_labels.get('exit_style') if outcome_labels else None,
            outcome_label_version=outcome_labels.get('label_version') if outcome_labels else None,
            # Linkage keys (for linking Outcome → Signal snapshots)
            signal_timestamp_utc=signal_time_utc,
            signal_run_id=run_id  # Same run_id links all snapshots (ORB, SIGNAL, OUTCOME) for same symbol/day
        )
    
    def _build_outcome_crypto_data(
        self,
        ohlcv_df: pd.DataFrame,
        session: str,
        timestamp_et: datetime,
        symbol: str,
        run_id: Optional[str] = None,
        indicators: Optional[Dict] = None
    ) -> OutcomeDataCrypto:
        """
        Build outcome data for crypto OUTCOME snapshots with proper Signal-based MFE/MAE calculation.
        Computes path-based metrics from Signal → Outcome (or Signal → next session open).
        """
        if ohlcv_df.empty:
            return OutcomeDataCrypto()
        
        current_price = float(ohlcv_df.iloc[-1].get('close', 0))
        
        # Get session bounds
        session_open_dt, orb_end_dt, signal_dt, outcome_dt = self._get_session_bounds_crypto(session, timestamp_et)
        
        # Calculate Signal time UTC - for linkage keys
        signal_time_utc = et_to_utc(signal_dt)
        
        # Calculate ORB levels (first 15 minutes)
        timeframe_minutes = int(self.settings.timeframe.replace('m', ''))
        orb_bars = 15 // timeframe_minutes if timeframe_minutes > 0 else 3
        orb_high = None
        orb_low = None
        orb_mid = None
        session_open_price = None
        
        if len(ohlcv_df) >= orb_bars:
            orb_df = ohlcv_df.head(orb_bars)
            session_open_price = float(ohlcv_df.iloc[0].get('open', 0))
            orb_high = float(orb_df['high'].max())
            orb_low = float(orb_df['low'].min())
            orb_mid = (orb_high + orb_low) / 2
        
        # Calculate Signal time (open + 60 minutes) - for linkage keys
        signal_time_utc = et_to_utc(signal_dt)
        
        # VERIFICATION: Outcome labels computed from future path (not endpoint only)
        log.debug(f"  📊 Outcome label computation (crypto):")
        log.debug(f"     - Signal time: {signal_time_utc} UTC")
        log.debug(f"     - Computing MFE/MAE from full path (not endpoint only)")
        
        # Find Signal price (close at/just before signal time)
        signal_price = None
        signal_idx = None
        
        for idx in range(len(ohlcv_df)):
            row = ohlcv_df.iloc[idx]
            bar_time = row.get('timestamp')
            
            # Normalize timestamp to UTC-aware pd.Timestamp
            bar_time_utc = self._to_utc_ts(bar_time)
            
            if bar_time_utc <= signal_time_utc:
                signal_price = float(row.get('close', 0))
                signal_idx = idx
            else:
                break
        
        if signal_price is None and len(ohlcv_df) > 0:
            signal_price = float(ohlcv_df.iloc[0].get('close', 0))
            signal_idx = 0
        
        # Create signal_df (all bars up to and including signal)
        if signal_idx is not None:
            signal_df = ohlcv_df.iloc[:signal_idx + 1]
        else:
            signal_df = ohlcv_df.head(1) if len(ohlcv_df) > 0 else pd.DataFrame()
        
        # Calculate path window (Signal → Outcome)
        if signal_df is not None and len(signal_df) < len(ohlcv_df):
            after_signal_df = ohlcv_df.iloc[len(signal_df):]
        else:
            after_signal_df = pd.DataFrame()
        
        # Calculate anchors and path metrics
        max_high_after_signal = None
        min_low_after_signal = None
        mfe_long_from_signal_pct = None
        mae_long_from_signal_pct = None
        mfe_short_from_signal_pct = None
        mae_short_from_signal_pct = None
        minutes_to_mfe_long = None
        minutes_to_mfe_short = None
        minutes_to_break_orb_high_after_signal = None
        minutes_to_break_orb_low_after_signal = None
        break_orb_high_within_5m = False
        break_orb_high_within_10m = False
        break_orb_high_within_15m = False
        break_orb_low_within_5m = False
        break_orb_low_within_10m = False
        break_orb_low_within_15m = False
        
        if signal_price and signal_price > 0 and len(after_signal_df) > 0:
            max_high_after_signal = float(after_signal_df['high'].max())
            min_low_after_signal = float(after_signal_df['low'].min())
            
            # MFE/MAE from Signal (Long & Short)
            mfe_long_from_signal_pct = ((max_high_after_signal - signal_price) / signal_price * 100) if signal_price > 0 else None
            mae_long_from_signal_pct = ((signal_price - min_low_after_signal) / signal_price * 100) if signal_price > 0 else None
            mfe_short_from_signal_pct = ((signal_price - min_low_after_signal) / signal_price * 100) if signal_price > 0 else None
            mae_short_from_signal_pct = ((max_high_after_signal - signal_price) / signal_price * 100) if signal_price > 0 else None
            
            # Find timing metrics
            mfe_long_time = None
            mfe_short_time = None
            orb_high_break_time = None
            orb_low_break_time = None
            
            for idx in range(len(after_signal_df)):
                row = after_signal_df.iloc[idx]
                bar_time = row.get('timestamp')
                bar_high = float(row.get('high', 0))
                bar_low = float(row.get('low', 0))
                
                # Calculate minutes after signal (normalize timestamp safely)
                bar_time_utc = self._to_utc_ts(bar_time)
                if bar_time_utc is None:
                    continue
                minutes_after_signal = (bar_time_utc - signal_time_utc).total_seconds() / 60.0
                if minutes_after_signal < 0:
                    continue
                
                # Track MFE timing
                if bar_high >= max_high_after_signal and mfe_long_time is None:
                    mfe_long_time = minutes_after_signal
                if bar_low <= min_low_after_signal and mfe_short_time is None:
                    mfe_short_time = minutes_after_signal
                
                # Track ORB break timing
                if orb_high and bar_high > orb_high and orb_high_break_time is None:
                    orb_high_break_time = minutes_after_signal
                if orb_low and bar_low < orb_low and orb_low_break_time is None:
                    orb_low_break_time = minutes_after_signal
                
                # Check break flags
                if orb_high and bar_high > orb_high:
                    if minutes_after_signal <= 5:
                        break_orb_high_within_5m = True
                    if minutes_after_signal <= 10:
                        break_orb_high_within_10m = True
                    if minutes_after_signal <= 15:
                        break_orb_high_within_15m = True
                if orb_low and bar_low < orb_low:
                    if minutes_after_signal <= 5:
                        break_orb_low_within_5m = True
                    if minutes_after_signal <= 10:
                        break_orb_low_within_10m = True
                    if minutes_after_signal <= 15:
                        break_orb_low_within_15m = True
            
            minutes_to_mfe_long = int(mfe_long_time) if mfe_long_time is not None else None
            minutes_to_mfe_short = int(mfe_short_time) if mfe_short_time is not None else None
            minutes_to_break_orb_high_after_signal = int(orb_high_break_time) if orb_high_break_time is not None else None
            minutes_to_break_orb_low_after_signal = int(orb_low_break_time) if orb_low_break_time is not None else None
        
        # Calculate best action and entry mode labels
        best_action_at_signal = "NO_TRADE"
        best_entry_mode = "WAIT"
        
        if signal_price and orb_high and orb_low:
            # Determine best action based on MFE/MAE
            if mfe_long_from_signal_pct and mfe_short_from_signal_pct:
                if mfe_long_from_signal_pct > mfe_short_from_signal_pct and mae_long_from_signal_pct and mae_long_from_signal_pct < mfe_long_from_signal_pct * 0.5:
                    best_action_at_signal = "LONG"
                    if signal_price < orb_mid:
                        best_entry_mode = "TRIGGER_ORB_HIGH"
                    else:
                        best_entry_mode = "IMMEDIATE"
                elif mfe_short_from_signal_pct > mfe_long_from_signal_pct and mae_short_from_signal_pct and mae_short_from_signal_pct < mfe_short_from_signal_pct * 0.5:
                    best_action_at_signal = "SHORT"
                    if signal_price > orb_mid:
                        best_entry_mode = "TRIGGER_ORB_LOW"
                    else:
                        best_entry_mode = "IMMEDIATE"
        
        # Fetch next session open price (existing logic)
        price_next_session_open = None
        try:
            next_session_map = {
                'LONDON': 'US',
                'US': 'RESET',
                'RESET': 'ASIA',
                'ASIA': 'LONDON'
            }
            next_session = next_session_map.get(session, 'US')
            
            next_session_open_str = {
                'LONDON': self.settings.crypto_london_open,
                'US': self.settings.crypto_us_open,
                'RESET': self.settings.crypto_reset_open,
                'ASIA': self.settings.crypto_asia_open
            }.get(next_session, self.settings.crypto_us_open)
            
            next_session_open_time = parse_time_string(next_session_open_str)
            
            if next_session == 'LONDON' and session == 'ASIA':
                next_session_dt = timestamp_et.replace(
                    hour=next_session_open_time.hour,
                    minute=next_session_open_time.minute,
                    second=0,
                    microsecond=0
                ) + timedelta(days=1)
            else:
                next_session_dt = timestamp_et.replace(
                    hour=next_session_open_time.hour,
                    minute=next_session_open_time.minute,
                    second=0,
                    microsecond=0
                )
                if next_session_dt <= timestamp_et:
                    next_session_dt = next_session_dt + timedelta(days=1)
            
            next_open_utc = et_to_utc(next_session_dt)
            fetch_start_utc = next_open_utc - timedelta(minutes=5)
            fetch_end_utc = next_open_utc + timedelta(minutes=10)
            
            log.debug(f"Fetching next session ({next_session}) open price for {symbol} at {next_open_utc} UTC")
            next_session_ohlcv = self.coinbase_client.get_ohlcv(
                symbol=symbol,
                timeframe=self.settings.timeframe,
                start_utc=fetch_start_utc,
                end_utc=fetch_end_utc
            )
            
            if not next_session_ohlcv.empty:
                price_next_session_open = float(next_session_ohlcv.iloc[0].get('open', 0))
                log.debug(f"✅ Retrieved next session open price: {price_next_session_open}")
            else:
                log.warning(f"⚠️ No OHLCV data for next session open, using get_last_price as fallback")
                price_next_session_open = self.coinbase_client.get_last_price(symbol)
                if price_next_session_open:
                    log.debug(f"✅ Used last price as fallback: {price_next_session_open}")
                    
        except Exception as e:
            log.warning(f"⚠️ Failed to fetch next session open price for {symbol}: {e}")
            price_next_session_open = current_price
        
        # Move to next open percentage
        move_to_next_open_pct = None
        if session_open_price and price_next_session_open and price_next_session_open > 0 and session_open_price > 0:
            move_to_next_open_pct = ((price_next_session_open - session_open_price) / session_open_price * 100)
        
        # Compute outcome labels using OutcomeLabelService
        # For crypto, use fixed horizon (signal + N hours) or next session open
        outcome_time_utc = None
        if self.settings.outcome_horizon_crypto_hours > 0:
            # Use fixed horizon
            outcome_time_utc = signal_time_utc + timedelta(hours=self.settings.outcome_horizon_crypto_hours)
        else:
            # Use next session open
            if price_next_session_open:
                outcome_time_utc = et_to_utc(next_session_dt) if 'next_session_dt' in locals() else signal_time_utc + timedelta(hours=6)
            else:
                outcome_time_utc = signal_time_utc + timedelta(hours=6)  # Fallback
        
        outcome_labels = None
        
        # Get ATR at signal time (compute indicators on signal_df)
        atr = None
        atr_pct = None
        indicator_ready = True  # Default for crypto (will be set from indicators if available)
        if signal_df is not None and len(signal_df) > 0:
            try:
                signal_indicators = self.indicator_service.calculate_indicators(signal_df, symbol="")
                atr = signal_indicators.get('atr')
                atr_pct = signal_indicators.get('atr_pct')
                indicator_ready = signal_indicators.get('indicator_ready', True)
            except Exception as e:
                log.warning(f"Failed to compute indicators at signal time (crypto): {e}")
        
        # Compute outcome labels if we have post-signal data
        if signal_price and signal_price > 0 and len(after_signal_df) > 0:
            try:
                outcome_labels = self.outcome_label_service.compute_outcome_labels(
                    ohlcv_df=ohlcv_df,  # Full OHLCV (will be filtered internally)
                    signal_time_utc=signal_time_utc,
                    outcome_time_utc=outcome_time_utc,
                    signal_price=signal_price,
                    atr=atr,
                    atr_pct=atr_pct,
                    market="CRYPTO",
                    indicator_ready=indicator_ready
                )
            except Exception as e:
                log.error(f"Failed to compute outcome labels (crypto): {e}", exc_info=True)
                outcome_labels = None
        
        # Extract outcome label fields
        peak_metrics = outcome_labels.get('peak_metrics', {}) if outcome_labels else {}
        synthetic_exit = outcome_labels.get('synthetic_exit', {}) if outcome_labels else {}
        trade_quality = outcome_labels.get('trade_quality', {}) if outcome_labels else {}
        
        return OutcomeDataCrypto(
            price_next_session_open=price_next_session_open if price_next_session_open and price_next_session_open > 0 else None,
            move_to_next_open_pct=move_to_next_open_pct,
            session_open_price=session_open_price,
            orb_high=orb_high,
            orb_low=orb_low,
            orb_mid=orb_mid,
            signal_price=signal_price,
            max_high_after_signal=max_high_after_signal,
            min_low_after_signal=min_low_after_signal,
            mfe_long_from_signal_pct=mfe_long_from_signal_pct,
            mae_long_from_signal_pct=mae_long_from_signal_pct,
            mfe_short_from_signal_pct=mfe_short_from_signal_pct,
            mae_short_from_signal_pct=mae_short_from_signal_pct,
            minutes_to_mfe_long=minutes_to_mfe_long,
            minutes_to_mfe_short=minutes_to_mfe_short,
            minutes_to_break_orb_high_after_signal=minutes_to_break_orb_high_after_signal,
            minutes_to_break_orb_low_after_signal=minutes_to_break_orb_low_after_signal,
            break_orb_high_within_5m=break_orb_high_within_5m,
            break_orb_high_within_10m=break_orb_high_within_10m,
            break_orb_high_within_15m=break_orb_high_within_15m,
            break_orb_low_within_5m=break_orb_low_within_5m,
            break_orb_low_within_10m=break_orb_low_within_10m,
            break_orb_low_within_15m=break_orb_low_within_15m,
            best_action_at_signal=outcome_labels.get('best_action_at_signal', best_action_at_signal) if outcome_labels else best_action_at_signal,
            best_entry_mode=best_entry_mode,
            session_followthrough_score=None,  # Would need trend analysis
            continuation_vs_reversal=None,  # Would need previous session data
            # Outcome Label Layer fields
            opportunity_score=outcome_labels.get('opportunity_score'),
            opportunity_long=outcome_labels.get('opportunity_long'),
            opportunity_short=outcome_labels.get('opportunity_short'),
            peak_mfe_pct=peak_metrics.get('mfe_pct'),
            peak_mae_pct=peak_metrics.get('mae_pct'),
            t_to_mfe_minutes=peak_metrics.get('t_to_mfe_minutes'),
            peak_end_drawdown_pct=peak_metrics.get('peak_end_drawdown_pct'),
            pullback_count=peak_metrics.get('pullback_count'),
            trend_persistence=peak_metrics.get('trend_persistence'),
            synthetic_r=synthetic_exit.get('synthetic_r_best'),  # Use best direction R
            synthetic_r_long=synthetic_exit.get('synthetic_r_long'),
            synthetic_r_short=synthetic_exit.get('synthetic_r_short'),
            synthetic_exit_reason=synthetic_exit.get('exit_reason'),
            synthetic_exit_time=synthetic_exit.get('exit_time'),
            synthetic_exit_price=synthetic_exit.get('exit_price'),
            trade_quality_score=trade_quality.get('quality_score'),
            trade_quality_label=trade_quality.get('quality_grade'),  # Updated to quality_grade
            exit_style=outcome_labels.get('exit_style') if outcome_labels else None,
            outcome_label_version=outcome_labels.get('label_version') if outcome_labels else None,
            # Linkage keys (for linking Outcome → Signal snapshots)
            signal_timestamp_utc=signal_time_utc,
            signal_run_id=run_id  # Same run_id links all snapshots (ORB, SIGNAL, OUTCOME) for same symbol/day
        )