"""
Easy Collector - Configuration Management
Handles environment variables and service configuration
"""

import os
import json
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache

# Load secretsprivate/.env for local dev when present (never in Docker; deploy uses Secret Manager)
_secrets_env = Path(__file__).resolve().parents[2] / "secretsprivate" / ".env"
if _secrets_env.exists():
    from dotenv import load_dotenv
    load_dotenv(_secrets_env)


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # GCP Configuration
    gcp_project_id: str = "easy-etrade-strategy"
    gcp_region: str = "us-central1"
    firestore_database_id: str = "(default)"
    use_firestore_emulator: bool = False
    firestore_emulator_host: str = "localhost:8080"
    
    # Service Configuration
    service_name: str = "easy-collector"
    environment: str = "production"
    log_level: str = "INFO"
    market_timezone: str = "America/New_York"
    
    # Market Data API Credentials
    etrade_enabled: bool = False  # Set ETRADE_ENABLED=true to use PrimeETradeTrading; disabled uses yfinance only
    etrade_consumer_key: Optional[str] = None
    etrade_consumer_secret: Optional[str] = None
    etrade_sandbox: bool = False
    
    coinbase_api_key: Optional[str] = None
    coinbase_api_secret: Optional[str] = None
    coinbase_sandbox: bool = False
    
    # Crypto Session Times (ET)
    crypto_london_open: str = "03:00"
    crypto_us_open: str = "08:00"
    crypto_reset_open: str = "17:00"
    crypto_asia_open: str = "19:00"
    
    # Holiday Configuration
    holidays_enabled: bool = True
    enable_muslim_holidays: bool = False
    
    # Macro Events (stub - comma-separated YYYY-MM-DD dates)
    macro_event_dates: str = ""
    
    # Health Check
    health_check_enabled: bool = True
    
    # Data Collection Configuration
    timeframe: str = "5m"  # Bar timeframe for indicator calculations (1m or 5m)
    indicator_lookback_bars: int = 120  # Number of bars needed for indicators (safe for MACD/BB/RSI/ATR/Stoch/MFI/CCI/CMF/EMA50)
    crypto_indicator_slab_bars: int = 180  # Crypto: bars for indicator slab (120–180; match US or slightly more)
    dry_run: bool = False  # Dry-run mode (doesn't write to Firestore)
    dry_run_collection: str = "snapshots_debug"  # Collection name for dry-run mode

    # US Data Provider Router (Polygon primary; yfinance unreliable from Cloud Run)
    us_data_provider: str = "polygon"  # "polygon" | "alpaca" | "yfinance" | "disabled"
    us_data_provider_fallbacks: str = "alpaca,yfinance"  # Ordered fallbacks; primary excluded
    us_provider_healthcheck_symbol: str = "SPY"
    us_provider_healthcheck_bars: int = 20  # Min 5m bars for healthcheck (e.g. ~100 min)
    us_provider_fail_open: bool = True  # If all US providers down: skip US, still run crypto + write run logs
    polygon_api_key: Optional[str] = None  # POLYGON_API_KEY; required when polygon in use
    alpaca_key: Optional[str] = None  # ALPACA_KEY
    alpaca_secret: Optional[str] = None  # ALPACA_SECRET
    
    # Local Storage Configuration
    enable_local_storage: bool = True  # Enable local CSV/JSON storage
    local_storage_path: Optional[str] = None  # Custom path for local storage (default: /tmp/easy_collector on Cloud Run)
    
    # Symbol Lists
    us_symbols: List[str] = [
        # Indices
        "SPX", "SPY", "QQQ", "IWM", "VIX", "GLD",
        # Tech
        "NVDA", "AMD", "TSLA", "META", "AMZN", "AAPL", "MSFT", "AVGO", "SMCI",
        # Trading favorites
        "COIN", "HOOD", "PLTR", "QCOM", "MU", "VST", "OKLO", "CRWV",
        "SOFI", "HIMS", "DAL", "RGTI"
    ]
    
    # 0DTE Symbols CSV Path (relative to project root)
    dte_symbols_csv_path: str = "data/watchlist/0dte_list.csv"
    
    # Project root path (for Cloud Run: /app, for local: can be overridden)
    project_root: str = "/app"
    
    crypto_symbols: List[str] = [
        "BTC-PERP", "ETH-PERP", "SOL-PERP", "XRP-PERP"
    ]
    
    # Outcome Labeling Configuration (Edge-Based Formulas v2.0)
    # Edge formula: edge = MFE - k*MAE - cost_penalty
    
    # Risk penalty coefficient (k in edge formula)
    edge_risk_weight: float = 0.5  # Penalize MAE at half weight (0.5 recommended)
    
    # Cost penalties (bid-ask spread + commissions + slippage, in %)
    cost_penalty_us: float = 0.15  # 0.10-0.25% recommended for US 0DTE (options execution is expensive)
    cost_penalty_crypto: float = 0.08  # 0.04-0.12% recommended for crypto PERPs (taker + slippage)
    
    # Minimum edge to trade (no-trade threshold, in %)
    min_edge_to_trade_us: float = 0.45  # 0.35-0.60% recommended for US
    min_edge_to_trade_crypto: float = 0.30  # 0.20-0.40% recommended for crypto
    
    # Opportunity normalization scales (for optional 0-1 normalization)
    opportunity_norm_scale_us: float = 2.5  # 2.0-3.0 recommended for US
    opportunity_norm_scale_crypto: float = 4.5  # 3.0-6.0 recommended for crypto
    
    # Trade quality parameters
    edge_norm_us: float = 2.0  # Normalization for US edge component in trade quality
    edge_norm_crypto: float = 4.0  # Normalization for crypto edge component in trade quality
    r_norm: float = 3.0  # Synthetic R normalization (3.0 = excellent)
    mae_floor_pct: float = 0.05  # Floor for MAE to prevent division blowups in synthetic R
    quality_weight_edge: float = 0.7  # Weight for edge component in trade quality
    quality_weight_r: float = 0.3  # Weight for R component in trade quality
    
    # Outcome horizon (crypto fixed horizon in hours)
    outcome_horizon_crypto_hours: int = 6  # 6-hour fixed horizon for crypto
    
    # Pullback thresholds (ATR-based or fixed %, in %)
    pullback_threshold_us_pct: float = 0.25  # 0.25% for US
    pullback_threshold_crypto_pct: float = 0.50  # 0.50% for crypto
    
    # Minimum bars for valid labels
    min_bars_for_labels: int = 3  # Minimum bars needed for outcome label calculation
    
    # Legacy parameters (kept for backward compatibility, not used in v2.0)
    opportunity_thresh_us: float = 0.20  # Legacy: not used in edge-based formulas
    opportunity_thresh_crypto: float = 0.35  # Legacy: not used in edge-based formulas
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    @field_validator("us_symbols", "crypto_symbols", mode="before")
    @classmethod
    def _parse_symbol_list(cls, v):
        """
        Parse symbol list from environment variable.
        Supports both JSON format (["SPY","QQQ"]) and comma-separated (SPY,QQQ,IWM).
        """
        if v is None:
            return v
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            # JSON list support
            if s.startswith("["):
                try:
                    return json.loads(s)
                except Exception:
                    pass
            # CSV support
            return [x.strip() for x in s.split(",") if x.strip()]
        return v
    
    @property
    def resolved_local_storage_path(self) -> Path:
        """
        Get resolved local storage path with Cloud Run safe default.
        
        Returns:
            Path object for local storage directory
        """
        if self.local_storage_path:
            return Path(self.local_storage_path)
        # Cloud Run safe default (only writable directory)
        return Path("/tmp/easy_collector")
    
    @property
    def macro_event_dates_list(self) -> List[str]:
        """Parse macro event dates from comma-separated string"""
        if not self.macro_event_dates:
            return []
        return [date.strip() for date in self.macro_event_dates.split(",") if date.strip()]

    @property
    def us_provider_fallbacks_list(self) -> List[str]:
        """Parse US provider fallbacks from comma-separated string (e.g. 'polygon,alpaca,yfinance')."""
        s = (self.us_data_provider_fallbacks or "").strip()
        if not s:
            return []
        return [p.strip().lower() for p in s.split(",") if p.strip()]
    
    def load_0dte_symbols(self) -> List[str]:
        """
        Load 0DTE symbols from CSV file

        Returns:
            Symbols in CSV row order (SPX first, then core_list-aligned). ``tier`` is metadata only.
        """
        import logging
        log = logging.getLogger(__name__)
        
        try:
            import pandas as pd
            
            # Try multiple paths for the 0DTE list (less brittle)
            possible_paths = [
                Path(self.dte_symbols_csv_path),  # Relative path (if run from project root)
                Path(self.project_root) / "data" / "watchlist" / "0dte_list.csv",  # Cloud Run canonical path
                Path(__file__).resolve().parents[3] / "data" / "watchlist" / "0dte_list.csv",  # Local dev path (config.py -> backend -> app -> easyCollector -> root)
            ]
            
            log.info(f"🔍 Loading 0DTE symbols from CSV - trying {len(possible_paths)} possible paths")
            for idx, path in enumerate(possible_paths, 1):
                log.debug(f"  [{idx}/{len(possible_paths)}] Checking path: {path} (exists: {path.exists()})")
            
            for path in possible_paths:
                if path.exists():
                    log.info(f"✅ Found 0DTE CSV at: {path}")
                    try:
                        df = pd.read_csv(path, comment='#')
                        log.debug(f"  CSV loaded: {len(df)} rows, columns: {list(df.columns)}")
                        
                        symbols = df['symbol'].tolist() if 'symbol' in df.columns else df.iloc[:, 0].tolist()
                        log.info(f"  Extracted {len(symbols)} symbols from CSV")
                        
                        if 'tier' in df.columns and 'symbol' in df.columns:
                            tier_map = dict(zip(df['symbol'], df['tier']))
                            tier_1_count = sum(1 for s in symbols if tier_map.get(s) == 1)
                            tier_2_count = sum(1 for s in symbols if tier_map.get(s) == 2)
                            log.info(f"  Tier distribution: Tier 1={tier_1_count}, Tier 2={tier_2_count} (CSV row order preserved)")
                        
                        log.info(f"✅ Successfully loaded {len(symbols)} 0DTE symbols from {path}")
                        log.debug(f"  First 10 symbols: {symbols[:10]}")
                        return symbols
                    except Exception as csv_error:
                        log.warning(f"⚠️ Failed to parse CSV at {path}: {csv_error}, trying next path...")
                        continue
            
            # Fallback to default symbols if CSV not found
            log.warning(f"⚠️ 0DTE CSV not found in any of {len(possible_paths)} paths, using default US symbols ({len(self.us_symbols)} symbols)")
            log.debug(f"  Default symbols: {self.us_symbols[:10]}...")
            return self.us_symbols.copy()
            
        except Exception as e:
            log.error(f"❌ Failed to load 0DTE symbol list: {e}", exc_info=True)
            log.warning(f"⚠️ Falling back to default US symbols ({len(self.us_symbols)} symbols)")
            return self.us_symbols.copy()


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
