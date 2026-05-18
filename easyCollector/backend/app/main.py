"""
Easy Collector - FastAPI Main Application
Market data collection service for US 0DTE and crypto futures
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.config import get_settings
from app.services.snapshot_service import SnapshotService
from app.models.snapshot_models import SnapshotType, CollectionSummary
from app.utils.time_utils import now_et, ensure_tz, get_market_tz

# Get settings early to configure logging
settings = get_settings()

# Setup logging with structured format for Cloud Logging
# Use log_level from settings (defaults to INFO)
log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S %Z'
)
log = logging.getLogger(__name__)

# Log startup
log.info("=" * 60)
log.info("🚀 Easy Collector starting up...")
log.info(f"📦 Version: 2.0.0")
log.info(f"🌍 Environment: {settings.environment}")
log.info(f"🔍 Dry-run mode: {settings.dry_run}")
log.info("=" * 60)


# Initialize services (before lifespan so it can access them)
snapshot_service = SnapshotService()

# Initialize FirestoreRepository once for health checks (reused, not per-request)
try:
    from app.storage.firestore_repo import FirestoreRepository
    firestore_repo = FirestoreRepository()
except Exception as e:
    log.warning(f"Failed to initialize FirestoreRepository: {e}")
    firestore_repo = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan event handler for startup/shutdown"""
    # Startup
    log.info("🚀 Starting up Easy Collector...")
    yield
    # Shutdown
    log.info("🛑 Shutting down Easy Collector...")
    # Close Coinbase client session
    if hasattr(snapshot_service, 'coinbase_client'):
        try:
            await snapshot_service.coinbase_client.close()
        except Exception as e:
            log.warning(f"Error closing Coinbase client: {e}")
    log.info("✅ Shutdown complete")


# Initialize FastAPI app
app = FastAPI(
    title="Easy Collector",
    description="Market data collection service for US 0DTE and crypto futures",
    version="2.0.0",
    lifespan=lifespan
)


# Request models
class USCollectionRequest(BaseModel):
    """Request model for US snapshot collection"""
    timestamp_et: Optional[str] = None  # YYYY-MM-DD HH:MM:SS format, ET timezone


class CryptoCollectionRequest(BaseModel):
    """Request model for crypto snapshot collection (session comes from path)"""
    timestamp_et: Optional[str] = None  # YYYY-MM-DD HH:MM:SS format, ET timezone


# Helper functions
def parse_request_timestamp_et(ts: Optional[str]) -> datetime:
    """
    Parse and normalize timestamp from request.
    Centralized helper for consistent timestamp handling across all endpoints.
    
    Args:
        ts: Optional timestamp string in "%Y-%m-%d %H:%M:%S" format (ET timezone)
    
    Returns:
        Timezone-aware datetime in ET
    """
    if ts:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return ensure_tz(dt, get_market_tz())
    return now_et()


# Health check endpoint
@app.get("/health")
async def health():
    """Health check endpoint. US uses run_healthcheck (Polygon→Alpaca→yfinance) to match collection."""
    try:
        from app.clients.us_provider_router import run_healthcheck, is_skipped

        # Check Firestore connection (reuse module-level instance)
        firestore_status = "connected"
        if firestore_repo is None:
            firestore_status = "unavailable"

        # US: use router (Polygon→Alpaca→yfinance) so health reflects what collection would use
        us_client, us_provider_name, us_router_health = run_healthcheck(
            yfinance_client=snapshot_service.yfinance_client
        )
        us_ok = us_client is not None and not is_skipped(us_router_health)
        coinbase_health = snapshot_service.coinbase_client.healthcheck()

        status = "healthy"
        if not us_ok or coinbase_health.get("status") != "healthy":
            status = "degraded"
        if firestore_status == "unavailable":
            status = "degraded"

        return {
            "status": status,
            "service": "easy-collector",
            "version": "2.0.0",
            "firestore": firestore_status,
            "us_provider": us_provider_name,
            "us_router_health": us_router_health,
            "coinbase": coinbase_health,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        log.error(f"Health check failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "easy-collector",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
        )


# Version endpoint
@app.get("/version")
async def version():
    """Get service version"""
    return {
        "service": "easy-collector",
        "version": "2.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


# US Collection Endpoints
@app.post("/collect/us/orb")
async def collect_us_orb(request: USCollectionRequest):
    """
    Collect US ORB snapshots (9:45 ET)
    
    Expected schedule: 9:45 ET weekdays
    """
    log.info("📥 Received US ORB collection request")
    
    # Dry-run guard
    if settings.dry_run:
        log.warning("⚠️ DRY-RUN mode: Will not write to Firestore")
    
    try:
        timestamp_et = parse_request_timestamp_et(request.timestamp_et)
        
        # Run collection in threadpool to avoid blocking event loop
        summary = await run_in_threadpool(
            snapshot_service.collect_us_snapshots,
            SnapshotType.ORB,
            timestamp_et
        )
        
        log.info(f"✅ US ORB collection completed: {summary.successful} successful, {summary.failed} failed")
        if summary.failed > 0:
            log.warning(f"⚠️ US ORB had {summary.failed} failures. Check logs above for details.")
            log.warning(f"   Failed symbols: {summary.errors[:5] if summary.errors else 'None'}")
        if summary.successful == 0:
            log.error(f"❌ US ORB: NO SNAPSHOTS SUCCESSFUL. All {summary.total_snapshots} symbols failed.")
        
        return {
            "success": True,
            "market": "US",
            "snapshot_type": "ORB",
            "summary": summary.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        log.error(f"❌ Failed to collect US ORB snapshots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/collect/us/signal")
async def collect_us_signal(request: USCollectionRequest):
    """
    Collect US SIGNAL snapshots (10:30 ET)
    
    Expected schedule: 10:30 ET weekdays
    """
    log.info("📥 Received US SIGNAL collection request")
    
    # Dry-run guard
    if settings.dry_run:
        log.warning("⚠️ DRY-RUN mode: Will not write to Firestore")
    
    try:
        timestamp_et = parse_request_timestamp_et(request.timestamp_et)
        
        # Run collection in threadpool to avoid blocking event loop
        summary = await run_in_threadpool(
            snapshot_service.collect_us_snapshots,
            SnapshotType.SIGNAL,
            timestamp_et
        )
        
        log.info(f"✅ US SIGNAL collection completed: {summary.successful} successful, {summary.failed} failed")
        if summary.failed > 0:
            log.warning(f"⚠️ US SIGNAL had {summary.failed} failures. Check logs above for details.")
            log.warning(f"   Failed symbols: {summary.errors[:5] if summary.errors else 'None'}")
        if summary.successful == 0:
            log.error(f"❌ US SIGNAL: NO SNAPSHOTS SUCCESSFUL. All {summary.total_snapshots} symbols failed.")
        
        return {
            "success": True,
            "market": "US",
            "snapshot_type": "SIGNAL",
            "summary": summary.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        log.error(f"Failed to collect US SIGNAL snapshots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/collect/us/outcome")
async def collect_us_outcome(request: USCollectionRequest):
    """
    Collect US OUTCOME snapshots (15:55 ET or early close - 5 min)
    
    Expected schedule: 15:55 ET weekdays (or early close - 5 min)
    """
    log.info("📥 Received US OUTCOME collection request")
    
    # Dry-run guard
    if settings.dry_run:
        log.warning("⚠️ DRY-RUN mode: Will not write to Firestore")
    
    try:
        timestamp_et = parse_request_timestamp_et(request.timestamp_et)
        
        # Run collection in threadpool to avoid blocking event loop
        summary = await run_in_threadpool(
            snapshot_service.collect_us_snapshots,
            SnapshotType.OUTCOME,
            timestamp_et
        )
        
        log.info(f"✅ US OUTCOME collection completed: {summary.successful} successful, {summary.failed} failed")
        if summary.failed > 0:
            log.warning(f"⚠️ US OUTCOME had {summary.failed} failures. Check logs above for details.")
            log.warning(f"   Failed symbols: {summary.errors[:5] if summary.errors else 'None'}")
        if summary.successful == 0:
            log.error(f"❌ US OUTCOME: NO SNAPSHOTS SUCCESSFUL. All {summary.total_snapshots} symbols failed.")
        
        return {
            "success": True,
            "market": "US",
            "snapshot_type": "OUTCOME",
            "summary": summary.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        log.error(f"Failed to collect US OUTCOME snapshots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Crypto Collection Endpoints
@app.post("/collect/crypto/{session}/orb")
async def collect_crypto_orb(session: str, request: CryptoCollectionRequest):
    """
    Collect crypto ORB snapshots (session open + 15 minutes)
    
    Expected schedule: session_open + 15m for LONDON, US, RESET, ASIA
    """
    log.info(f"📥 Received crypto {session} ORB collection request")
    
    # Dry-run guard
    if settings.dry_run:
        log.warning("⚠️ DRY-RUN mode: Will not write to Firestore")
    
    try:
        # Validate session
        valid_sessions = ["LONDON", "US", "RESET", "ASIA"]
        session_upper = session.upper()
        if session_upper not in valid_sessions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid session: {session}. Must be one of {valid_sessions}"
            )
        
        timestamp_et = parse_request_timestamp_et(request.timestamp_et)
        
        # Run collection in threadpool to avoid blocking event loop
        summary = await run_in_threadpool(
            snapshot_service.collect_crypto_snapshots,
            session_upper,
            SnapshotType.ORB,
            timestamp_et
        )
        
        log.info(f"✅ Crypto {session_upper} ORB collection completed: {summary.successful} successful, {summary.failed} failed")
        if summary.failed > 0:
            log.warning(f"⚠️ Crypto {session_upper} ORB had {summary.failed} failures. Check logs above for details.")
            log.warning(f"   Failed symbols: {summary.errors[:5] if summary.errors else 'None'}")
        if summary.successful == 0:
            log.error(f"❌ Crypto {session_upper} ORB: NO SNAPSHOTS SUCCESSFUL. All {summary.total_snapshots} symbols failed.")
        
        return {
            "success": True,
            "market": "CRYPTO",
            "session": session_upper,
            "snapshot_type": "ORB",
            "summary": summary.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to collect crypto {session} ORB snapshots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/collect/crypto/{session}/signal")
async def collect_crypto_signal(session: str, request: CryptoCollectionRequest):
    """
    Collect crypto SIGNAL snapshots (session open + 60 minutes)
    
    Expected schedule: session_open + 60m for LONDON, US, RESET, ASIA
    """
    log.info(f"📥 Received crypto {session} SIGNAL collection request")
    
    # Dry-run guard
    if settings.dry_run:
        log.warning("⚠️ DRY-RUN mode: Will not write to Firestore")
    
    try:
        valid_sessions = ["LONDON", "US", "RESET", "ASIA"]
        session_upper = session.upper()
        if session_upper not in valid_sessions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid session: {session}. Must be one of {valid_sessions}"
            )
        
        timestamp_et = parse_request_timestamp_et(request.timestamp_et)
        
        # Run collection in threadpool to avoid blocking event loop
        summary = await run_in_threadpool(
            snapshot_service.collect_crypto_snapshots,
            session_upper,
            SnapshotType.SIGNAL,
            timestamp_et
        )
        
        log.info(f"✅ Crypto {session_upper} SIGNAL collection completed: {summary.successful} successful, {summary.failed} failed")
        if summary.failed > 0:
            log.warning(f"⚠️ Crypto {session_upper} SIGNAL had {summary.failed} failures. Check logs above for details.")
            log.warning(f"   Failed symbols: {summary.errors[:5] if summary.errors else 'None'}")
        if summary.successful == 0:
            log.error(f"❌ Crypto {session_upper} SIGNAL: NO SNAPSHOTS SUCCESSFUL. All {summary.total_snapshots} symbols failed.")
        
        return {
            "success": True,
            "market": "CRYPTO",
            "session": session_upper,
            "snapshot_type": "SIGNAL",
            "summary": summary.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to collect crypto {session} SIGNAL snapshots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/collect/crypto/{session}/outcome")
async def collect_crypto_outcome(session: str, request: CryptoCollectionRequest):
    """
    Collect crypto OUTCOME snapshots (5 minutes before next session open)
    
    Expected schedule: next_session_open - 5m for LONDON, US, RESET, ASIA
    """
    log.info(f"📥 Received crypto {session} OUTCOME collection request")
    
    # Dry-run guard
    if settings.dry_run:
        log.warning("⚠️ DRY-RUN mode: Will not write to Firestore")
    
    try:
        valid_sessions = ["LONDON", "US", "RESET", "ASIA"]
        session_upper = session.upper()
        if session_upper not in valid_sessions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid session: {session}. Must be one of {valid_sessions}"
            )
        
        timestamp_et = parse_request_timestamp_et(request.timestamp_et)
        
        # Run collection in threadpool to avoid blocking event loop
        summary = await run_in_threadpool(
            snapshot_service.collect_crypto_snapshots,
            session_upper,
            SnapshotType.OUTCOME,
            timestamp_et
        )
        
        log.info(f"✅ Crypto {session_upper} OUTCOME collection completed: {summary.successful} successful, {summary.failed} failed")
        if summary.failed > 0:
            log.warning(f"⚠️ Crypto {session_upper} OUTCOME had {summary.failed} failures. Check logs above for details.")
            log.warning(f"   Failed symbols: {summary.errors[:5] if summary.errors else 'None'}")
        if summary.successful == 0:
            log.error(f"❌ Crypto {session_upper} OUTCOME: NO SNAPSHOTS SUCCESSFUL. All {summary.total_snapshots} symbols failed.")
        
        return {
            "success": True,
            "market": "CRYPTO",
            "session": session_upper,
            "snapshot_type": "OUTCOME",
            "summary": summary.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to collect crypto {session} OUTCOME snapshots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Debug: Polygon key and health (direct, no router)
@app.get("/debug/polygon")
async def debug_polygon():
    """
    Check if POLYGON_API_KEY is set and what PolygonClient.healthcheck() returns.
    Use this to distinguish: key missing vs key invalid vs API error.
    """
    from app.clients.polygon_client import PolygonClient

    settings = get_settings()
    key_set = bool(settings.polygon_api_key and str(settings.polygon_api_key).strip())
    h = PolygonClient().healthcheck()
    return {
        "polygon_api_key_set": key_set,
        "polygon_healthcheck": h,
    }


# Debug: US provider smoke test
@app.get("/debug/us/provider_smoke")
async def debug_us_provider_smoke(symbol: str = "SPY", bars: int = 50):
    """
    Smoke test US data provider: fetch recent 5m bars for the given symbol.
    Returns: provider name, row count, min/max timestamps, sample of last 3 bars.
    """
    from app.clients.us_provider_router import run_healthcheck, is_skipped
    from datetime import timezone

    # Use healthcheck which does a real fetch; we need a bit more bars
    settings = get_settings()
    sym = symbol or settings.us_provider_healthcheck_symbol
    n = max(int(bars) if bars else 50, 20)

    us_client, name, health = run_healthcheck(yfinance_client=snapshot_service.yfinance_client)
    if is_skipped(health) or us_client is None:
        return {
            "provider": None,
            "row_count": 0,
            "min_timestamp": None,
            "max_timestamp": None,
            "sample_last_3": [],
            "health": health,
            "message": "US provider unavailable",
        }

    from datetime import timedelta
    from app.utils.time_utils import in_us_market_hours, get_last_us_session_2h_window_utc

    if in_us_market_hours():
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=n * 5 + 30)
    else:
        # Outside 9:30–16:00 ET: use last 2h of the most recent US session so the request has data
        start, end = get_last_us_session_2h_window_utc()
    try:
        df = us_client.get_ohlcv(sym, "5m", start, end)
    except Exception as e:
        return {
            "provider": name,
            "row_count": 0,
            "min_timestamp": None,
            "max_timestamp": None,
            "sample_last_3": [],
            "error": str(e),
            "health": health,
        }

    rc = len(df)
    mn = str(df["timestamp"].min()) if rc else None
    mx = str(df["timestamp"].max()) if rc else None
    sample = []
    if rc >= 3:
        sample = df[["timestamp", "open", "high", "low", "close", "volume"]].tail(3).to_dict("records")
        for r in sample:
            if "timestamp" in r:
                r["timestamp"] = str(r["timestamp"])

    # Upgrade: last bar age, NaN checks, last_close
    last_bar_age_seconds = None
    nan_checks = {}
    last_close = None
    if rc:
        last_ts = df["timestamp"].max()
        dt = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        last_bar_age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
        nan_checks = {"o": bool(df["open"].isna().any()), "h": bool(df["high"].isna().any()), "l": bool(df["low"].isna().any()), "c": bool(df["close"].isna().any())}
        last_close = float(df["close"].iloc[-1])

    return {
        "provider": name,
        "row_count": rc,
        "min_timestamp": mn,
        "max_timestamp": mx,
        "bounds": {"start": str(start), "end": str(end)},
        "last_bar_timestamp_age_seconds": last_bar_age_seconds,
        "nan_checks": nan_checks,
        "last_close": last_close,
        "sample_last_3": sample,
        "health": health,
    }


# Debug: Crypto product smoke test
@app.get("/debug/crypto/product_smoke")
async def debug_crypto_product_smoke(symbol: str = "BTC-PERP"):
    """
    Smoke test crypto product resolution and candles: resolve symbol, check /products, fetch last 2h of 5m candles.
    Returns: resolved_product_id, exists_in_products, candle_row_count, last_timestamp_age_seconds.
    """
    from datetime import timezone, timedelta
    cb = snapshot_service.coinbase_client
    product_id, err = cb.resolve_product_id(symbol)
    exists_in_products = product_id in cb._valid_product_ids if product_id else False
    if err == "UNRESOLVABLE_SYMBOL" or not product_id:
        return {
            "symbol": symbol,
            "resolved_product_id": product_id,
            "exists_in_products": exists_in_products,
            "reason": err or "no_product_id",
            "candle_row_count": 0,
            "last_timestamp_age_seconds": None,
        }
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=2)
    try:
        df = await run_in_threadpool(cb.get_ohlcv, symbol, "5m", start, end)
    except Exception as e:
        return {
            "symbol": symbol,
            "resolved_product_id": product_id,
            "exists_in_products": exists_in_products,
            "candle_row_count": 0,
            "last_timestamp_age_seconds": None,
            "error": str(e),
        }
    rc = len(df)
    last_bar_age_seconds = None
    if rc:
        last_ts = df["timestamp"].max()
        dt = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        last_bar_age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    return {
        "symbol": symbol,
        "resolved_product_id": product_id,
        "exists_in_products": exists_in_products,
        "candle_row_count": rc,
        "last_timestamp_age_seconds": last_bar_age_seconds,
        "window_hours": 2,
    }


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "easy-collector",
        "version": "2.0.0",
        "description": "Market data collection service for US 0DTE and crypto futures",
        "endpoints": {
            "health": "/health",
            "version": "/version",
            "us_collection": "/collect/us/{orb|signal|outcome}",
            "crypto_collection": "/collect/crypto/{session}/{orb|signal|outcome}"
        },
        "timestamp": datetime.utcnow().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
