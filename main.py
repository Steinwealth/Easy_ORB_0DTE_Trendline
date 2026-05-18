#!/usr/bin/env python3
"""
Improved Main Entry Point
High-performance entry point using the integrated trading system
Consolidates all functionality and eliminates redundancy
"""

from __future__ import annotations
import os
import shutil
import sys
import logging
import argparse
import asyncio
import signal
from typing import Optional
from datetime import datetime, timedelta

# --- Prime System Imports ---
from modules.prime_trading_system import (
    get_prime_trading_system, PrimeTradingSystem, TradingConfig, SystemMode
)
from modules.prime_market_manager import (
    get_prime_market_manager, PrimeMarketManager, MarketSession
)
# ARCHIVED (Rev 00173): Production signal generator no longer used - ORB manager generates signals directly
# DELETED (Oct 20, 2025): Removed production_signal_generator import - ORB strategy handles signals directly
from modules.etrade_oauth_integration import get_etrade_oauth_integration
from modules.prime_etrade_trading import PrimeETradeTrading
from modules.prime_models import StrategyMode
from modules.config_loader import load_configuration, get_config_value
from modules.prime_alert_manager import (
    ALERT_LABEL_EASY_ORB_ETF,
    ALERT_LABEL_EASY_ORB_0DTE_OPTIONS,
)

# OAuth keep-alive handled by Cloud Scheduler (no local keep-alive needed)

# --- Google Cloud specific imports ---
try:
    from google.cloud import logging as gcp_logging
    GCP_LOGGING_AVAILABLE = True
except ImportError:
    GCP_LOGGING_AVAILABLE = False

# --- Load configuration based on command line args or environment ---
def load_app_config():
    parser = argparse.ArgumentParser(description='ETrade Strategy Trading Bot - Improved')
    parser.add_argument('--strategy-mode', default=os.getenv('STRATEGY_MODE', 'standard'),
                       choices=['standard', 'advanced', 'quantum'],
                       help='Trading strategy mode')
    parser.add_argument('--system-mode', default=os.getenv('SYSTEM_MODE', 'full_trading'),
                       choices=['signal_only', 'scanner_only', 'full_trading', 'alert_only'],
                       help='System operation mode')
    parser.add_argument('--environment', default=os.getenv('ENVIRONMENT', 'development'),
                       choices=['development', 'production', 'sandbox'],
                       help='Deployment environment')
    parser.add_argument('--etrade-mode', default=os.getenv('ETRADE_MODE', 'demo'),
                       choices=['demo', 'live'],
                       help='ETrade trading mode (demo or live)')
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', 8080)),
                       help='Port for HTTP server (cloud mode)')
    parser.add_argument('--host', default=os.getenv('HOST', '0.0.0.0'),
                       help='Host for HTTP server (cloud mode)')
    parser.add_argument('--cloud-mode', action='store_true',
                       help='Enable cloud deployment mode with HTTP server')
    parser.add_argument('--enable-premarket', action='store_true',
                       help='Enable pre-market news analysis')
    parser.add_argument('--enable-confluence', action='store_true',
                       help='Enable confluence trading system')
    parser.add_argument('--enable-multi-strategy', action='store_true',
                       help='Enable multi-strategy engine')
    parser.add_argument('--enable-news-sentiment', action='store_true',
                       help='Enable news sentiment analysis')
    parser.add_argument('--enable-enhanced-signals', action='store_true',
                       help='Enable enhanced signal generation')
    parser.add_argument('--enable-production-signals', action='store_true',
                       help='Enable Production Signal Generator (THE ONE AND ONLY)')
    parser.add_argument('--enable-signal-optimization', action='store_true',
                       help='Enable signal optimization and quality monitoring')
    parser.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'),
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       help='Set logging level')
    parser.add_argument('--max-positions', type=int, default=int(os.getenv('MAX_POSITIONS', '10')),
                       help='Maximum number of positions')
    parser.add_argument('--scan-frequency', type=int, default=int(os.getenv('SCAN_FREQUENCY', '30')),
                       help='Scan frequency in seconds')
    
    args = parser.parse_args()
    
    # Load unified configuration
    automation_mode = 'live' if args.etrade_mode == 'live' else 'demo'
    config = load_configuration(args.strategy_mode, automation_mode, args.environment)
    
    # Set environment variables for backward compatibility
    for key, value in config.items():
        os.environ[key] = str(value)
    
    return config, args

# --- Initialize configuration ---
try:
    CONFIG, ARGS = load_app_config()
except Exception as e:
    print(f"Failed to load configuration: {e}")
    sys.exit(1)

# --- Logging Configuration ---
def setup_logging():
    """Setup optimized logging for all environments"""
    # Get log level from args or config
    log_level = ARGS.log_level.upper() if hasattr(ARGS, 'log_level') else get_config_value("SESSION_LOG_LEVEL", "INFO").upper()
    
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create formatter
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ"
    )
    
    # Console handler (required for all environments)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)
    
    # Optional file handler for local development
    if ARGS.environment == 'development' and get_config_value("FILE_LOGGING", True):
        log_path = get_config_value("LOG_PATH", "logs/signals.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    
    return logger

# --- Google Cloud Logging Setup ---
def setup_cloud_logging():
    """Setup Google Cloud Logging if available"""
    if GCP_LOGGING_AVAILABLE and ARGS.environment == 'production':
        try:
            client = gcp_logging.Client()
            client.setup_logging()
            # Drop stdout/stderr StreamHandlers so each log line is not duplicated in Cloud Run
            # (structured agent on stderr + plain stdout capture).
            root = logging.getLogger()
            for h in list(root.handlers):
                if isinstance(h, logging.StreamHandler):
                    stream = getattr(h, "stream", None)
                    if stream in (sys.stdout, sys.stderr):
                        root.removeHandler(h)
            print("Google Cloud Logging initialized")
            return True
        except Exception as e:
            print(f"Failed to initialize GCP logging: {e}")
            return False
    return False

# --- Global System Instance ---
_system_instance = None

def _to_strategy_mode(mode_str: str) -> StrategyMode:
    """Safely convert string to StrategyMode enum with a sensible default."""
    mapping = {
        "standard": StrategyMode.STANDARD,
        "advanced": StrategyMode.ADVANCED,
        "quantum": StrategyMode.QUANTUM,
    }
    return mapping.get(str(mode_str).lower(), StrategyMode.STANDARD)

def get_integrated_system():
    """Get or create the integrated system instance"""
    global _system_instance
    if _system_instance is None:
        # Determine trading mode strictly from ETRADE_MODE to avoid enum mismatch
        resolved_mode = SystemMode.DEMO_MODE if ARGS.etrade_mode == 'demo' else SystemMode.LIVE_MODE

        # Create system configuration
        system_config = TradingConfig(
            mode=resolved_mode,
            strategy_mode=_to_strategy_mode(ARGS.strategy_mode),
            enable_premarket_analysis=ARGS.enable_premarket,
            enable_confluence_trading=ARGS.enable_confluence,
            enable_multi_strategy=ARGS.enable_multi_strategy,
            enable_news_sentiment=ARGS.enable_news_sentiment,
            enable_enhanced_signals=ARGS.enable_enhanced_signals,
            max_positions=ARGS.max_positions,
            scan_frequency=ARGS.scan_frequency
        )
        _system_instance = get_prime_trading_system(system_config)
    return _system_instance

# --- Health Check Endpoint ---
async def health_check():
    """Comprehensive health check endpoint using integrated system"""
    try:
        # Cloud Run: return 200 immediately if system not yet built (allows fast container startup)
        if _system_instance is None:
            return {
                "status": "healthy",
                "message": "starting",
                "timestamp": datetime.utcnow().isoformat(),
                "environment": ARGS.environment,
            }
        # Get integrated system instance
        system = get_integrated_system()
        
        # Get system metrics
        metrics = system.get_metrics()
        
        # Determine health status
        health_status = "healthy"
        if metrics["system_metrics"]["errors"] > 10:
            health_status = "degraded"
        if metrics["system_metrics"]["errors"] > 50:
            health_status = "unhealthy"
        
        # Check if deployment test file exists
        import os
        test_file_exists = os.path.exists("DEPLOYMENT_TEST_00078.txt")
        test_file_content = ""
        if test_file_exists:
            try:
                with open("DEPLOYMENT_TEST_00078.txt", "r") as f:
                    test_file_content = f.read().strip()
            except:
                test_file_content = "Error reading file"
        
        return {
            "status": health_status,
            "timestamp": datetime.utcnow().isoformat(),
            "environment": ARGS.environment,
            "strategy_mode": ARGS.strategy_mode,
            "system_mode": ARGS.system_mode,
            "uptime_hours": metrics["system_metrics"]["uptime_hours"],
            "current_phase": metrics["current_phase"],
            "running": metrics["running"],
            "system_metrics": metrics["system_metrics"],
            "trading_metrics": metrics["trading_metrics"],
            "scanner_metrics": metrics["scanner_metrics"],
            "deployment_test": {
                "file_exists": test_file_exists,
                "content": test_file_content
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

# --- HTTP Server for Cloud Mode ---
async def check_and_build_stale_watchlist():
    """Deprecated: Dynamic watchlist build removed. Using static core_list.csv."""
    logger = logging.getLogger("improved_main")
    logger.info("🗒️ Watchlist builder disabled - using static core_list.csv")
    return

async def start_http_server():
    """Start HTTP server for cloud deployment"""
    try:
        try:
            from aiohttp import web
        except ImportError:
            logger = logging.getLogger("improved_main")
            logger.error("aiohttp not available. Install with: pip install aiohttp")
            return None
        
        async def handle_health(request):
            health_data = await health_check()
            status_code = 200 if health_data["status"] in ["healthy", "degraded"] else 503
            return web.json_response(health_data, status=status_code)
        
        async def handle_metrics(request):
            system = get_integrated_system()
            metrics = system.get_metrics()
            return web.json_response(metrics)
        
        async def handle_status(request):
            health_data = await health_check()
            return web.json_response(health_data)
        
        async def handle_control(request):
            """Control endpoint for system management"""
            data = await request.json()
            action = data.get('action')
            
            if action == 'shutdown':
                system = get_integrated_system()
                await system.shutdown()
                return web.json_response({"status": "shutdown_initiated"})
            elif action == 'restart':
                # Restart would be handled by the container orchestrator
                return web.json_response({"status": "restart_initiated"})
            elif action == 'force_close_symbol':
                system = get_integrated_system()
                symbol = str(data.get('symbol', '') or '').strip().upper()
                if not symbol:
                    return web.json_response({"error": "symbol_required"}, status=400)
                if not system or not hasattr(system, "stealth_trailing") or not system.stealth_trailing:
                    return web.json_response({"error": "stealth_trailing_unavailable"}, status=500)

                position_state = system.stealth_trailing.active_positions.get(symbol)
                if not position_state:
                    return web.json_response({
                        "error": "position_not_found",
                        "symbol": symbol,
                        "active_symbols": sorted(list(system.stealth_trailing.active_positions.keys()))
                    }, status=404)

                # Reuse existing close + remove flow so exit telemetry/alerts stay consistent.
                from modules.prime_stealth_trailing_tp import ExitReason
                close_reason = str(data.get('reason', 'manual_force_exit') or 'manual_force_exit')
                if getattr(system.stealth_trailing, "exec", None):
                    await system.stealth_trailing.exec.close_position(position_state, close_reason)
                await system.stealth_trailing._remove_position(symbol, ExitReason.TIME_EXIT, send_alert=True)

                return web.json_response({
                    "status": "force_closed",
                    "symbol": symbol,
                    "reason": close_reason,
                    "pnl": float(getattr(position_state, "unrealized_pnl", 0.0) or 0.0),
                    "pnl_pct": float(getattr(position_state, "unrealized_pnl_pct", 0.0) or 0.0),
                })
            else:
                return web.json_response({"error": "unknown_action"}, status=400)
        

        async def handle_build_watchlist(request):
            """Deprecated endpoint; static core_list.csv is used. Returns success no-op."""
            logger = logging.getLogger("improved_main")
            logger.info("📋 /api/build-watchlist called - dynamic builder disabled; using core_list.csv")
            return web.json_response({
                "status": "success",
                "message": "Dynamic watchlist disabled. Using static core_list.csv",
                "timestamp": datetime.utcnow().isoformat(),
                "force_rebuild": False,
                "symbol_count": None
            })
        
        async def handle_watchlist_status(request):
            """Return static watchlist status indicating core_list.csv usage."""
            logger = logging.getLogger("improved_main")
            file_path = "data/watchlist/core_list.csv"
            info = {
                "using_static_core_list": True,
                "file_exists": os.path.exists(file_path),
                "timestamp": datetime.utcnow().isoformat()
            }
            if info["file_exists"]:
                try:
                    mod_time = os.path.getmtime(file_path)
                    info["last_modified"] = datetime.fromtimestamp(mod_time).isoformat()
                    try:
                        import pandas as pd
                        df = pd.read_csv(file_path)
                        info["symbol_count"] = len(df)
                    except Exception:
                        info["symbol_count"] = None
                except Exception:
                    pass
            return web.json_response({"status": "success", "watchlist": info})
        
        async def handle_oauth_token_renewed(request):
            """Handle OAuth token renewal webhook"""
            try:
                logger = logging.getLogger("improved_main")
                data = await request.json()
                environment = data.get("environment", "prod")
                
                logger.info(f"🔄 Received OAuth token renewal webhook for {environment}")
                
                # Send OAuth token renewal confirmation alert
                system = get_integrated_system()
                if system.alert_manager:
                    success = await system.alert_manager.send_oauth_renewal_success(environment)
                    if success:
                        logger.info(f"✅ OAuth token renewal alert sent for {environment}")
                        return web.json_response({
                            "status": "success", 
                            "message": f"OAuth token renewal alert sent for {environment}"
                        })
                    else:
                        logger.error(f"❌ Failed to send OAuth token renewal alert for {environment}")
                        return web.json_response({
                            "status": "error", 
                            "message": f"Failed to send OAuth token renewal alert for {environment}"
                        }, status=500)
                else:
                    logger.error("❌ Alert manager not available")
                    return web.json_response({
                        "status": "error", 
                        "message": "Alert manager not available"
                    }, status=500)
                    
            except Exception as e:
                logger.error(f"❌ Error handling OAuth token renewal webhook: {e}")
                return web.json_response({
                    "status": "error", 
                    "message": f"Error handling OAuth token renewal webhook: {str(e)}"
                }, status=500)

        async def handle_oauth_test_alert(request):
            """Test OAuth alert functionality"""
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🔄 Testing OAuth alert functionality")
                
                # Get the integrated trading system
                system = get_integrated_system()
                
                if system.alert_manager:
                    # Production tokens only (sandbox deprecated)
                    success_prod = await system.alert_manager.send_oauth_renewal_success("prod")
                    if success_prod:
                        logger.info("✅ OAuth test alert (production) sent successfully")
                        return web.json_response({
                            "status": "success",
                            "message": "OAuth test alert sent (production only)"
                        })
                    else:
                        logger.error("❌ OAuth test alert failed")
                        return web.json_response({
                            "status": "error",
                            "message": "OAuth test alert failed"
                        })
                else:
                    logger.error("❌ Alert manager not available")
                    return web.json_response({
                        "status": "error", 
                        "message": "Alert manager not available"
                    }, status=500)
                    
            except Exception as e:
                logger.error(f"❌ Error testing OAuth alerts: {e}")
                return web.json_response({
                    "status": "error", 
                    "message": f"Error testing OAuth alerts: {str(e)}"
                }, status=500)
        
        async def handle_market_open_alert(request):
            """
            Market open alert endpoint (Cloud Scheduler at 8:30 AM ET / 5:30 AM PT)
            Sends Good Morning alert with token status 1 hour before market open
            
            RESTORED (Oct 24, 2025): Cloud Scheduler job IS calling this endpoint
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🌅 Market open alert triggered by Cloud Scheduler (8:30 AM ET)")
                
                # Get the system instance
                system = get_integrated_system()
                if not system or not system.alert_manager:
                    logger.warning("System or alert_manager not available for market open alert")
                    return web.json_response({
                        "status": "error",
                        "message": "System not available"
                    }, status=503)
                
                # Send Good Morning alert via alert manager
                success = await system.alert_manager.send_oauth_morning_alert()
                
                if success:
                    logger.info("✅ Good Morning alert sent successfully")
                    return web.json_response({
                        "status": "success",
                        "message": "Good Morning alert sent"
                    })
                else:
                    logger.warning("⚠️ Good Morning alert failed to send")
                    return web.json_response({
                        "status": "warning",
                        "message": "Alert send failed (non-critical)"
                    }, status=200)  # 200 to prevent Cloud Scheduler retries
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in market open alert endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"Market open alert error: {str(e)}"
                }, status=500)
        
        async def handle_validation_candle_700(request):
            """
            7:00 AM PT: Capture broker prices as open for 7:00-7:15 validation candle.
            Call via Cloud Scheduler at 7:00 AM PT so 7:15 prefetch can combine with 7:15 close for GREEN/RED.
            Broker-agnostic (E*TRADE today; same flow for Interactive Brokers etc.).
            """
            try:
                logger = logging.getLogger("improved_main")
                # Diagnostic: log immediately so cloud logs show 7:00 job ran even if capture fails (diagnose_zero_signals_cloud.py)
                logger.info("PIPELINE | STEP 2 VALIDATION OPEN (7:00) | endpoint hit | validation-candle-700")
                logger.info("📊 Validation candle 7:00 AM PT trigger (Cloud Scheduler)")
                system = get_integrated_system()
                if not system or not hasattr(system, 'capture_validation_open_700'):
                    logger.warning("System or capture_validation_open_700 not available")
                    return web.json_response({
                        "status": "error",
                        "message": "System not available"
                    }, status=503)
                count = await system.capture_validation_open_700()
                logger.info(f"VALIDATION_CANDLE | 7:00 OPEN | recorded={count} symbols | ready for 7:15 close")
                return web.json_response({
                    "status": "success",
                    "message": f"7:00 AM PT open prices stored for {count} symbols",
                    "symbols_captured": count
                })
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in validation-candle-700 endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": str(e)
                }, status=500)
        
        async def handle_prefetch_validation_715(request):
            """
            7:15 AM PT: Run validation candle prefetch (7:00 open + 7:15 close → GREEN/RED).
            Call via Cloud Scheduler at 7:15 AM PT so prefetch runs even if trading loop is not yet in window (scale-to-zero).
            Ensures validation candle is ready for signal collection at 7:30.
            """
            try:
                logger = logging.getLogger("improved_main")
                # Diagnostic: log immediately so cloud logs show 7:15 job ran even if prefetch fails (diagnose_zero_signals_cloud.py)
                logger.info("PIPELINE | STEP 3 VALIDATION CANDLE (7:00-7:15) | endpoint hit | prefetch-validation-715")
                logger.info("📊 Validation candle 7:15 AM PT prefetch trigger (Cloud Scheduler)")
                system = get_integrated_system()
                if not system or not hasattr(system, '_prefetch_previous_candle_data'):
                    logger.warning("System or _prefetch_previous_candle_data not available")
                    return web.json_response({"status": "error", "message": "System not available"}, status=503)
                await system._prefetch_previous_candle_data()
                system._prev_candle_prefetched_today = True
                logger.info("VALIDATION_CANDLE | 7:15 PREFETCH | endpoint completed | ready for signal collection")
                return web.json_response({
                    "status": "success",
                    "message": "7:15 AM PT validation candle prefetch completed (7:00 open + 7:15 close)"
                })
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in prefetch-validation-715 endpoint: {e}")
                return web.json_response({"status": "error", "message": str(e)}, status=500)
        
        async def handle_end_of_day_report(request):
            """
            End of day report endpoint (Cloud Scheduler at 4:05 PM ET / 1:05 PM PT)
            Sends EOD summary with daily P&L and performance metrics
            
            Rev 00260 (Jan 22, 2026): SINGLE SOURCE for all EOD reports
            - Easy ORB (ETF) EOD: demo + optional live summaries
            - Easy ORB 0DTE (options) EOD: options demo/live ledger report
            - Easy Trendline 0DTE (options) EOD: trendline demo ledger summary
            - Internal scheduler: Already DISABLED
            - GCS deduplication: Active as safety net
            
            RESTORED (Oct 24, 2025): Cloud Scheduler job IS calling this endpoint
            FIXED (Nov 2, 2025 - Rev 00093): Added weekday/holiday checking to prevent weekend alerts
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 End of day report triggered by Cloud Scheduler (4:05 PM ET)")
                
                # Rev 00093: Check if it's a trading day BEFORE sending EOD report
                from datetime import date
                from modules.dynamic_holiday_calculator import should_skip_trading
                
                today = date.today()
                is_weekend = today.weekday() >= 5  # Saturday=5, Sunday=6
                should_skip, skip_reason, holiday_name = should_skip_trading(today)
                
                if is_weekend:
                    logger.info(f"📅 Weekend ({today.strftime('%A')}) - Skipping EOD report")
                    return web.json_response({
                        "status": "skipped",
                        "message": f"Weekend ({today.strftime('%A')}) - No EOD report sent"
                    })
                elif should_skip:
                    logger.info(f"📅 Holiday ({holiday_name}) - Skipping EOD report")
                    return web.json_response({
                        "status": "skipped",
                        "message": f"Holiday ({holiday_name}) - No EOD report sent"
                    })
                
                # Get the system instance
                system = get_integrated_system()
                if not system or not system.alert_manager:
                    logger.warning("System or alert_manager not available for EOD report")
                    return web.json_response({
                        "status": "error",
                        "message": "System not available"
                    }, status=503)
                
                # Flatten all strategy paths before Telegram EOD stats. Trigger: Cloud Scheduler `end-of-day-report`
                # → POST /api/end-of-day-report. `flatten_all_paths_for_eod_scheduler` skips if the main loop already
                # flattened the same day in the same process (SO_ETF_EOD_CLOSE_* PT window in prime_trading_system).
                try:
                    if hasattr(system, "flatten_all_paths_for_eod_scheduler"):
                        await system.flatten_all_paths_for_eod_scheduler()
                    else:
                        logger.warning("EOD_REPORT | flatten_all_paths_for_eod_scheduler missing on system")
                except Exception as flatten_all_err:
                    logger.error(
                        "EOD_REPORT | flatten_all_paths_failed | err=%s",
                        flatten_all_err,
                        exc_info=True,
                    )
                
                # Send Demo Mode EOD summary (ORB ETF / core ORB account)
                logger.info(f"Sending {ALERT_LABEL_EASY_ORB_ETF} ETF demo EOD summary...")
                await system.alert_manager._send_demo_eod_summary()
                
                # Send Live Mode EOD summary (ORB ETF / live ORB account)
                if hasattr(system, 'unified_trade_manager') and system.unified_trade_manager:
                    logger.info(f"Sending {ALERT_LABEL_EASY_ORB_ETF} ETF live EOD summary...")
                    await system.alert_manager._send_live_eod_summary(system.unified_trade_manager)
                
                # Send 0DTE Options EOD report (Rev 00206)
                if hasattr(system, 'dte0_manager') and system.dte0_manager:
                    if hasattr(system.dte0_manager, 'options_executor') and system.dte0_manager.options_executor:
                        try:
                            logger.info(f"Sending {ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} EOD report...")
                            options_executor = system.dte0_manager.options_executor
                            # Pre-flatten is centralized in system.flatten_all_paths_for_eod_scheduler() (called above).
                            
                            # Get stats from mock executor if in demo mode
                            if options_executor.demo_mode and options_executor.mock_executor:
                                daily_stats = options_executor.mock_executor.get_daily_stats()
                                weekly_stats = options_executor.mock_executor.get_weekly_stats()
                                all_time_stats = options_executor.mock_executor.get_all_time_stats()
                                account_balance = options_executor.mock_executor.account_balance
                                starting_balance = options_executor.mock_executor.starting_balance
                                mode = "DEMO"
                            else:
                                # Live mode - would need to get stats from live executor
                                daily_stats = {'positions_closed': 0, 'winning_trades': 0, 'losing_trades': 0, 'total_pnl': 0.0, 'best_trade': 0.0, 'worst_trade': 0.0, 'total_wins_sum': 0.0, 'total_losses_sum': 0.0}
                                weekly_stats = {'positions_closed': 0, 'winning_trades': 0, 'losing_trades': 0, 'total_pnl': 0.0, 'total_wins_sum': 0.0, 'total_losses_sum': 0.0}
                                all_time_stats = None
                                account_balance = 0.0
                                starting_balance = 0.0
                                mode = "LIVE"
                            
                            # Send Options EOD report
                            await system.alert_manager.send_options_end_of_day_report(
                                daily_stats=daily_stats,
                                weekly_stats=weekly_stats,
                                all_time_stats=all_time_stats,
                                account_balance=account_balance,
                                starting_balance=starting_balance,
                                mode=mode
                            )
                            logger.info(f"✅ Options EOD report sent ({mode} Mode)")
                        except Exception as options_eod_error:
                            logger.error(f"Failed to send Options EOD report: {options_eod_error}", exc_info=True)
                
                # Send Easy Trendline EOD report (separate third strategy path)
                if (
                    hasattr(system, "trendline_reporter")
                    and system.trendline_reporter
                    and hasattr(system, "trendline_account_manager")
                    and system.trendline_account_manager
                ):
                    try:
                        if not getattr(system, "_trendline_eod_report_sent_today", False):
                            report = system.trendline_reporter.build_eod_report(
                                candidates=list(getattr(system, "_pending_trendline_candidates", None) or []),
                                closed_positions=system.trendline_account_manager.closed_positions,
                            )
                            from zoneinfo import ZoneInfo
                            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
                            now_et = datetime.now(ZoneInfo("America/New_York"))
                            pt_time = now_pt.strftime('%I:%M %p PT')
                            et_time = now_et.strftime('%I:%M %p ET')
                            report_date = now_pt.strftime('%Y-%m-%d')
                            closed_positions = list(system.trendline_account_manager.closed_positions or [])

                            def _sum_wins_losses(positions):
                                wins_sum = sum(float(p.realized_pnl) for p in positions if float(p.realized_pnl) > 0)
                                losses_sum = abs(sum(float(p.realized_pnl) for p in positions if float(p.realized_pnl) < 0))
                                return wins_sum, losses_sum

                            def _profit_factor(positions):
                                wins_sum, losses_sum = _sum_wins_losses(positions)
                                return (wins_sum / losses_sum) if losses_sum > 0 else (float("inf") if wins_sum > 0 else 0.0)

                            def _pf_str(value):
                                return f"{value:.2f}" if value != float("inf") else "∞"

                            def _pct(pnl_value, base):
                                return (pnl_value / base * 100.0) if base > 0 else 0.0

                            today_positions = [
                                p for p in closed_positions
                                if p.closed_at and p.closed_at.date() == now_pt.date()
                            ]
                            week_start = now_pt.date() - timedelta(days=now_pt.weekday())
                            week_positions = [
                                p for p in closed_positions
                                if p.closed_at and week_start <= p.closed_at.date() <= now_pt.date()
                            ]

                            starting_balance = float(getattr(system.trendline_account_manager, "starting_balance", 5000.0))
                            account_balance = float(getattr(system.trendline_account_manager, "account_balance", starting_balance))
                            all_time_pnl = account_balance - starting_balance

                            today_pnl = sum(float(p.realized_pnl) for p in today_positions)
                            week_pnl = sum(float(p.realized_pnl) for p in week_positions)
                            today_wins = sum(1 for p in today_positions if float(p.realized_pnl) > 0)
                            today_losses = sum(1 for p in today_positions if float(p.realized_pnl) < 0)
                            week_wins = sum(1 for p in week_positions if float(p.realized_pnl) > 0)
                            week_losses = sum(1 for p in week_positions if float(p.realized_pnl) < 0)
                            all_time_wins = sum(1 for p in closed_positions if float(p.realized_pnl) > 0)
                            all_time_losses = sum(1 for p in closed_positions if float(p.realized_pnl) < 0)

                            today_closed = len(today_positions)
                            week_closed = len(week_positions)
                            all_time_closed = len(closed_positions)

                            today_win_rate = (today_wins / today_closed * 100.0) if today_closed > 0 else 0.0
                            week_win_rate = (week_wins / week_closed * 100.0) if week_closed > 0 else 0.0
                            all_time_win_rate = (all_time_wins / all_time_closed * 100.0) if all_time_closed > 0 else 0.0

                            today_pf = _profit_factor(today_positions)
                            week_pf = _profit_factor(week_positions)
                            all_time_pf = _profit_factor(closed_positions)

                            today_best = max((float(p.realized_pnl) for p in today_positions), default=0.0)
                            today_worst = min((float(p.realized_pnl) for p in today_positions), default=0.0)
                            today_wins_sum, today_losses_sum = _sum_wins_losses(today_positions)
                            avg_win = (today_wins_sum / today_wins) if today_wins > 0 else 0.0
                            avg_loss = -(today_losses_sum / today_losses) if today_losses > 0 else 0.0

                            today_pnl_sign = "+" if today_pnl >= 0 else ""
                            week_pnl_sign = "+" if week_pnl >= 0 else ""
                            all_time_pnl_sign = "+" if all_time_pnl >= 0 else ""
                            today_best_sign = "+" if today_best >= 0 else "-"
                            today_worst_sign = "+" if today_worst >= 0 else "-"

                            tl_eod_mode = "DEMO"
                            from html import escape as _html_escape

                            mode_esc = _html_escape(tl_eod_mode, quote=False)
                            report_text = f"""====================================================================

💎 <b>END-OF-DAY TREND 0DTE</b> | 🎲 | {mode_esc} Mode
          Time: {pt_time} ({et_time})

📈 <b>P&L (TODAY):</b>
          <b>{today_pnl_sign}{_pct(today_pnl, starting_balance):.2f}% {today_pnl_sign}${today_pnl:.2f}</b>
          Win Rate: {today_win_rate:.1f}% • Total Trades: {today_closed}
          Wins: {today_wins} • Losses: {today_losses}
          Profit Factor: {_pf_str(today_pf)}
          Average Win: ${avg_win:.2f}
          Average Loss: ${avg_loss:.2f}
          Best Trade: {today_best_sign}${abs(today_best):.2f}
          Worst Trade: {today_worst_sign}${abs(today_worst):.2f}

🎖️⭐ <b>P&L (WEEK M-F):</b>
          <b>{week_pnl_sign}{_pct(week_pnl, starting_balance):.2f}% {week_pnl_sign}${week_pnl:.2f}</b>
          Win Rate: {week_win_rate:.1f}% • Total Trades: {week_closed}
          Profit Factor: {_pf_str(week_pf)}

💎 <b>Account Balances (All Time):</b>
          <b>{all_time_pnl_sign}{_pct(all_time_pnl, starting_balance):.2f}% {all_time_pnl_sign}${all_time_pnl:.2f}</b>
          <b>${account_balance:,.2f}</b>
          Win Rate: {all_time_win_rate:.1f}% • Total Trades: {all_time_closed}
          Profit Factor: {_pf_str(all_time_pf)}
          Wins: {all_time_wins} • Losses: {all_time_losses}

📅 <b>Report Date:</b> {report_date}
"""
                            await system.alert_manager.send_trendline_end_of_day_telegram(
                                report_text,
                                mode=tl_eod_mode,
                            )
                            logger.info(
                                "TRENDLINE_PIPELINE | stage=summary | "
                                f"candidates={report.total_candidates} | build_failures={report.metadata.get('build_failures', 0)} | "
                                f"first_breaks={report.metadata.get('first_breaks', 0)} | first_break_failed={report.metadata.get('first_breaks_failed', 0)} | "
                                f"hold_success={report.metadata.get('hold_success', 0)} | continuation_breaks={report.metadata.get('continuation_breaks', 0)} | "
                                f"structure_accepted={report.metadata.get('structure_accepted', 0)} | confirmed={report.momentum_confirmations} | "
                                f"executed={report.executed_trades} | invalidated={report.invalidated_setups} | expired={report.expired_setups} | "
                                f"dedupe_blocked={report.metadata.get('dedupe_blocked', 0)} | "
                                f"candidate_universe={getattr(system, '_trendline_candidate_universe_size', report.total_candidates)} | "
                                f"skipped_cap={getattr(system, '_trendline_daily_cap_skips', 0)} | "
                                f"skipped_sizing={getattr(system, '_trendline_sizing_skips', 0)} | "
                                f"capital_deployed={getattr(system, '_trendline_capital_deployed_today', 0.0):.2f} | "
                                f"unused_capital={max(0.0, (getattr(system, '_trendline_slot_capital', 0.0) * max(1, getattr(system, '_trendline_slot_count', 1))) - getattr(system, '_trendline_capital_deployed_today', 0.0)):.2f} | "
                                f"avg_730_to_first_break={report.metadata.get('avg_minutes_730_to_first_break', 0):.2f} | "
                                f"avg_first_break_to_hold={report.metadata.get('avg_minutes_first_break_to_hold_success', 0):.2f} | "
                                f"avg_hold_to_execution={report.metadata.get('avg_minutes_hold_success_to_execution', 0):.2f}"
                            )
                            system._trendline_eod_report_sent_today = True
                            logger.info("TRENDLINE_PIPELINE | stage=eod_summary | status=sent")
                    except Exception as trendline_eod_error:
                        logger.error(f"Failed to send Easy Trendline EOD report: {trendline_eod_error}", exc_info=True)
                
                logger.info("✅ EOD reports sent successfully")
                return web.json_response({
                    "status": "success",
                    "message": "EOD reports sent"
                })
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in EOD report endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"EOD report error: {str(e)}"
                }, status=500)
        
        async def handle_cleanup_historical_data(request):
            """
            Historical data cleanup endpoint (Cloud Scheduler at 4:05 PM ET)
            
            UPDATED (Oct 11, 2025 - Rev 00151):
            - No historical data to clean up (ORB strategy uses intraday bars only)
            - Endpoint kept for backward compatibility with Cloud Scheduler
            - Returns success immediately (no-op)
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🗑️ Cleanup endpoint called (no-op for ORB strategy)")
                
                # ORB strategy doesn't use 100-day historical data
                # Only uses intraday 15-minute bars (fetched on-demand)
                # No cleanup needed - endpoint kept for Cloud Scheduler compatibility
                
                return web.json_response({
                    "status": "success",
                    "message": "No cleanup needed (ORB strategy uses intraday bars only)",
                    "removed_count": 0,
                    "timestamp": datetime.now().isoformat(),
                    "note": "Historical data caching removed in Rev 00151 - ORB strategy optimization"
                })
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Cleanup endpoint error: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"Cleanup endpoint error: {str(e)}"
                }, status=500)
        
        async def handle_cleanup_images(request):
            """
            Container image and Cloud Run revision cleanup endpoint
            Rev 00259: Weekly cleanup of old images and revisions
            
            This endpoint runs the cleanup scripts to remove old container images
            and Cloud Run revisions to optimize storage costs.
            """
            try:
                import subprocess
                import json
                
                logger = logging.getLogger("improved_main")
                logger.info("🧹 Image cleanup endpoint called")
                
                # Get request data
                try:
                    data = await request.json()
                except:
                    data = {}
                
                cleanup_images = data.get('cleanup_images', True)
                cleanup_revisions = data.get('cleanup_revisions', True)
                
                results = {
                    "status": "success",
                    "timestamp": datetime.now().isoformat(),
                    "cleanup_images": cleanup_images,
                    "cleanup_revisions": cleanup_revisions,
                    "results": {}
                }
                
                # Get script directory (project root in container = /app)
                script_dir = os.path.dirname(os.path.abspath(__file__))
                # Fallback: Cloud Run uses /app; dirname can be / when __file__ is /main.py
                def _scripts_base():
                    candidates = [
                        script_dir,
                        "/app" if os.environ.get("PORT") else None,  # Cloud Run
                        os.getcwd(),
                    ]
                    for base in candidates:
                        if base and os.path.exists(os.path.join(base, "scripts", "cleanup_old_revisions.sh")):
                            return base
                    return script_dir  # use original even if missing
                script_dir = _scripts_base()
                
                # Run image cleanup if requested (requires gcloud - not in container)
                if cleanup_images:
                    gcloud_available = shutil.which("gcloud") is not None
                    if not gcloud_available:
                        logger.info("📦 Skipping image cleanup (gcloud not in container); revision cleanup will run")
                        results["results"]["images"] = {
                            "success": True,
                            "skipped": True,
                            "reason": "gcloud not in container; run ./scripts/cleanup_old_images.sh manually"
                        }
                    else:
                        logger.info("📦 Running image cleanup script...")
                        cleanup_images_script = os.path.join(script_dir, "scripts", "cleanup_old_images.sh")
                        if os.path.exists(cleanup_images_script):
                            try:
                                result = subprocess.run(
                                    ["bash", cleanup_images_script],
                                    capture_output=True,
                                    text=True,
                                    timeout=600,
                                    cwd=script_dir
                                )
                                results["results"]["images"] = {
                                    "success": result.returncode == 0,
                                    "stdout": result.stdout[-1000:] if result.stdout else "",
                                    "stderr": result.stderr[-1000:] if result.stderr else "",
                                    "returncode": result.returncode
                                }
                                logger.info(f"✅ Image cleanup completed (return code: {result.returncode})")
                            except subprocess.TimeoutExpired:
                                logger.error("❌ Image cleanup timed out after 10 minutes")
                                results["results"]["images"] = {"success": False, "error": "Timeout after 10 minutes"}
                            except Exception as e:
                                logger.error(f"❌ Image cleanup error: {e}")
                                results["results"]["images"] = {"success": False, "error": str(e)}
                        else:
                            results["results"]["images"] = {"success": False, "error": f"Script not found: {cleanup_images_script}"}
                
                # Run revision cleanup if requested
                # Rev 00294: Use Python Cloud Run API first (works in container without gcloud)
                if cleanup_revisions:
                    logger.info("📦 Running revision cleanup...")
                    try:
                        from modules.cloud_cleanup import cleanup_cloud_run_revisions
                        rev_results = cleanup_cloud_run_revisions()
                        deleted = rev_results.get("deleted", 0)
                        errors = rev_results.get("errors", [])
                        success = len(errors) == 0 or deleted > 0
                        results["results"]["revisions"] = {
                            "success": success,
                            "deleted": deleted,
                            "kept": rev_results.get("kept", 0),
                            "per_service": rev_results.get("per_service", {}),
                            "errors": errors[:5] if errors else []
                        }
                        if deleted > 0:
                            logger.info(f"✅ Revision cleanup completed: deleted {deleted}, kept {rev_results.get('kept', 0)}")
                        elif errors:
                            logger.warning(f"⚠️ Revision cleanup had errors: {errors[:3]}")
                    except ImportError as e:
                        # Fallback to script if cloud_cleanup not available
                        cleanup_revisions_script = os.path.join(script_dir, "scripts", "cleanup_old_revisions.sh")
                        if os.path.exists(cleanup_revisions_script):
                            try:
                                result = subprocess.run(
                                    ["bash", cleanup_revisions_script],
                                    capture_output=True,
                                    text=True,
                                    timeout=600,
                                    cwd=script_dir
                                )
                                results["results"]["revisions"] = {
                                    "success": result.returncode == 0,
                                    "stdout": result.stdout[-1000:] if result.stdout else "",
                                    "stderr": result.stderr[-1000:] if result.stderr else "",
                                    "returncode": result.returncode
                                }
                                logger.info(f"✅ Revision cleanup (script) completed (return code: {result.returncode})")
                            except Exception as script_err:
                                results["results"]["revisions"] = {"success": False, "error": str(script_err)}
                        else:
                            results["results"]["revisions"] = {
                                "success": False,
                                "error": f"cloud_cleanup import failed: {e}; script not found at {cleanup_revisions_script} (script_dir={script_dir})"
                            }
                    except Exception as e:
                        logger.error(f"❌ Revision cleanup error: {e}")
                        results["results"]["revisions"] = {"success": False, "error": str(e)}
                
                # Determine overall success: revision cleanup is primary (works in-container)
                # Return 200 if revision cleanup succeeded; image cleanup may be skipped (needs gcloud)
                rev_ok = results["results"].get("revisions", {}).get("success", False)
                all_ok = all(r.get("success", False) for r in results["results"].values()) if results["results"] else False
                # Scheduler-friendly: 200 when revision cleanup succeeded (main cost optimization)
                http_ok = rev_ok if cleanup_revisions else (all_ok or not results["results"])
                if not all_ok:
                    results["status"] = "partial_success" if rev_ok else "error"
                return web.json_response(results, status=200 if http_ok else 500)
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Image cleanup endpoint error: {e}", exc_info=True)
                return web.json_response({
                    "status": "error",
                    "message": f"Cleanup endpoint error: {str(e)}"
                }, status=500)
        
        async def handle_pending_signals(request):
            """
            Pending signals endpoint (Oct 27, 2025)
            Shows accumulated SO signals during collection window (7:15-7:44 AM PT)
            Useful for real-time monitoring and window optimization
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📡 Pending signals request received")
                
                # Get the system instance
                system = get_integrated_system()
                if not system:
                    return web.json_response({
                        "status": "error",
                        "message": "System not initialized"
                    }, status=503)
                
                # Check if ORB strategy manager exists and has signals
                if not hasattr(system, 'orb_strategy_manager') or not system.orb_strategy_manager:
                    return web.json_response({
                        "status": "ok",
                        "message": "ORB strategy manager not available",
                        "pending_signals": [],
                        "count": 0
                    })
                
                # Get accumulated SO signals
                orb_manager = system.orb_strategy_manager
                accumulated_signals = []
                
                if hasattr(orb_manager, 'accumulated_so_signals'):
                    accumulated_signals = orb_manager.accumulated_so_signals or []
                
                # Format signal preview
                signal_preview = []
                for idx, sig in enumerate(accumulated_signals[:20], 1):  # Show top 20
                    signal_preview.append({
                        "rank": idx,
                        "symbol": sig.get('symbol', 'UNKNOWN'),
                        "price": round(sig.get('price', 0), 2),
                        "confidence": round(sig.get('confidence', 0), 3),
                        "priority_score": round(sig.get('priority_score', 0), 3),
                        "orb_range": round(sig.get('orb_range_pct', 0), 2),
                        "volume_ratio": round(sig.get('volume_ratio', 0), 2)
                    })
                
                return web.json_response({
                    "status": "ok",
                    "timestamp": datetime.now().isoformat(),
                    "collection_window": "7:15-7:44 AM PT (10:15-10:44 AM ET)",
                    "total_signals": len(accumulated_signals),
                    "signals_preview": signal_preview,
                    "max_trades": 15,
                    "will_execute": min(len(accumulated_signals), 15)
                })
                    
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"❌ Error in pending signals endpoint: {e}")
                return web.json_response({
                    "status": "error",
                    "message": f"Pending signals error: {str(e)}"
                }, status=500)
        
        async def handle_market_holiday_check(request):
            """
            Market holiday check endpoint (Cloud Scheduler at 5:30 AM PT / 8:30 AM ET)
            Sends alert if today is a holiday - 1 hour before market would open
            Rev 00163
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("🏖️ Market holiday check triggered by Cloud Scheduler")
                
                # Get the system instance
                system = get_integrated_system()
                
                if not system or not system.market_manager:
                    logger.error("❌ System or market manager not available")
                    return web.json_response({
                        "status": "error",
                        "message": "System not initialized"
                    }, status=500)
                
                # Check if today is a trading day
                # Use module-level datetime import (line 16)
                today = datetime.now().date()
                is_trading_day = system.market_manager.is_trading_day()
                
                if is_trading_day:
                    logger.info("✅ Today is a trading day - no holiday alert needed")
                    return web.json_response({
                        "status": "success",
                        "message": "Today is a trading day",
                        "is_holiday": False,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # Today is NOT a trading day - check if it's a holiday (not just weekend)
                day_of_week = today.weekday()  # 0=Monday, 6=Sunday
                
                if day_of_week >= 5:  # Saturday or Sunday
                    logger.info("⏸️ Today is a weekend - no holiday alert needed")
                    return web.json_response({
                        "status": "success",
                        "message": "Today is a weekend",
                        "is_holiday": False,
                        "is_weekend": True,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # It's a weekday but not a trading day = HOLIDAY
                # Rev 00087: Use unified holiday checker
                logger.info("🏖️ Today is a holiday - checking type")
                
                # Use unified holiday checker
                from modules.dynamic_holiday_calculator import should_skip_trading
                
                should_skip, skip_reason, holiday_name = should_skip_trading(today)
                
                if not should_skip:
                    # Shouldn't happen (market_manager said not trading day), but handle gracefully
                    holiday_name = "Market Holiday"
                    skip_reason = "MARKET_CLOSED"
                    logger.warning("Holiday detected by market_manager but not by holiday calculator")
                
                # Send holiday alert (unified method)
                # NOTE: This endpoint is called by Cloud Scheduler, but the morning alert (5:30 AM PT)
                # now also checks for holidays. This is a redundant check for backwards compatibility.
                if system.alert_manager:
                    success = await system.alert_manager.send_holiday_alert(
                        holiday_name=holiday_name,
                        skip_reason=skip_reason
                    )
                    
                    if success:
                        logger.info(f"✅ Market holiday alert sent - {holiday_name}")
                        return web.json_response({
                            "status": "success",
                            "message": f"Holiday alert sent - {holiday_name}",
                            "holiday_name": holiday_name,
                            "holiday_date": holiday_date,
                            "is_holiday": True,
                            "timestamp": datetime.now().isoformat()
                        })
                    else:
                        logger.error("❌ Failed to send holiday alert")
                        return web.json_response({
                            "status": "error",
                            "message": "Failed to send holiday alert"
                        }, status=500)
                else:
                    logger.error("❌ Alert manager not available")
                    return web.json_response({
                        "status": "error",
                        "message": "Alert manager not available"
                    }, status=500)
                
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in market holiday check: {e}")
                return web.json_response({
                    "status": "error",
                    "message": str(e)
                }, status=500)
        
        async def handle_manual_orb_capture(request):
            """Manually trigger ORB capture (Rev 00173 - for testing/recovery)"""
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 Manual ORB capture triggered")
                
                # Get the integrated trading system
                system = get_integrated_system()
                
                if not system:
                    return web.json_response({
                        "status": "error",
                        "message": "Trading system not initialized"
                    }, status=500)
                
                # Check if ORB capture is available
                if not hasattr(system, '_capture_orb_for_all_symbols'):
                    return web.json_response({
                        "status": "error",
                        "message": "ORB capture method not available"
                    }, status=500)
                
                # Check if ORB strategy manager is initialized
                if not hasattr(system, 'orb_strategy_manager') or not system.orb_strategy_manager:
                    return web.json_response({
                        "status": "error",
                        "message": "ORB Strategy Manager not initialized"
                    }, status=500)
                
                # Trigger ORB capture
                logger.info(f"🎯 Manually capturing ORB for {len(system.symbol_list)} symbols...")
                await system._capture_orb_for_all_symbols()
                
                # Get capture results
                symbols_captured = len(system.orb_strategy_manager.orb_data)
                
                logger.info(f"✅ Manual ORB capture complete: {symbols_captured} symbols")
                
                return web.json_response({
                    "status": "success",
                    "message": f"ORB captured for {symbols_captured} symbols",
                    "symbols_captured": symbols_captured,
                    "symbol_list_size": len(system.symbol_list),
                    "timestamp": datetime.now().isoformat()
                })
                
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in manual ORB capture: {e}")
                return web.json_response({
                    "status": "error",
                    "message": str(e)
                }, status=500)
        
        async def handle_positions(request):
            """
            Get current open positions with full details (Rev 00068)
            Returns position data from stealth trailing system (source of truth for monitoring)
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 /api/positions called")
                
                # Get the integrated trading system
                system = get_integrated_system()
                
                if not system:
                    return web.json_response({
                        "error": "Trading system not initialized",
                        "total_positions": 0,
                        "positions": []
                    }, status=500)
                
                # Check if stealth trailing is available (source of truth for position monitoring)
                if not hasattr(system, 'stealth_trailing') or not system.stealth_trailing:
                    return web.json_response({
                        "error": "Stealth trailing system not available",
                        "total_positions": 0,
                        "positions": []
                    })
                
                # Get positions from stealth trailing
                positions_data = []
                for symbol, pos_state in system.stealth_trailing.active_positions.items():
                    time_held_sec = (datetime.now() - pos_state.entry_time).total_seconds()
                    time_held_min = time_held_sec / 60.0
                    
                    # Rev 00071: Fix field names to match PositionState dataclass
                    position_value = pos_state.quantity * pos_state.entry_price
                    max_favorable_pct = (pos_state.max_favorable / pos_state.entry_price) * 100 if pos_state.entry_price > 0 else 0.0
                    
                    positions_data.append({
                        'symbol': symbol,
                        'entry_price': pos_state.entry_price,
                        'current_price': pos_state.current_price,
                        'quantity': pos_state.quantity,
                        'position_value': position_value,  # Rev 00071: Calculate (was accessing non-existent field)
                        'unrealized_pnl': pos_state.unrealized_pnl,  # Rev 00071: Fixed field name (was unrealized_pl)
                        'unrealized_pnl_pct': pos_state.unrealized_pnl_pct * 100,  # Rev 00071: Convert to percentage (was unrealized_pl_pct)
                        'current_stop': pos_state.current_stop_loss,
                        'take_profit': pos_state.take_profit,
                        'time_held_min': round(time_held_min, 1),
                        'max_favorable': pos_state.max_favorable,
                        'max_favorable_pct': max_favorable_pct,  # Rev 00071: Calculate (was accessing non-existent field)
                        'entry_bar_protection': False,  # Rev 00071: Placeholder (field doesn't exist in PositionState)
                        'breakeven_activated': pos_state.breakeven_achieved,  # Rev 00071: Fixed field name (was breakeven_activated)
                        'trailing_activated': pos_state.trailing_activated
                    })
                
                logger.info(f"✅ Returning {len(positions_data)} positions from stealth trailing")
                
                return web.json_response({
                    'total_positions': len(positions_data),
                    'positions': positions_data,
                    'timestamp': datetime.now().isoformat(),
                    'mode': system.config.mode.value if hasattr(system.config, 'mode') else 'unknown'
                })
                
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in /api/positions endpoint: {e}")
                return web.json_response({
                    'error': str(e),
                    'total_positions': 0,
                    'positions': []
                }, status=500)

        async def handle_options_positions(request):
            """
            Get current open options positions for ORB 0DTE and Trendline 0DTE.
            Uses in-memory position + option_stealth metadata only (no extra market-data fan-out).
            """
            try:
                logger = logging.getLogger("improved_main")
                logger.info("📊 /api/options-positions called")

                system = get_integrated_system()
                if not system:
                    return web.json_response({
                        "error": "Trading system not initialized",
                        "orb_0dte": {"total_positions": 0, "positions": [], "summary": {}},
                        "trendline_0dte": {"total_positions": 0, "positions": [], "summary": {}},
                    }, status=500)

                orb_positions = []
                trendline_positions = []

                # ORB 0DTE open positions
                try:
                    executor = getattr(getattr(system, "dte0_manager", None), "options_executor", None)
                    for pos in (executor.get_open_positions() or []) if executor else []:
                        md = getattr(pos, "metadata", None)
                        md = md if isinstance(md, dict) else {}
                        osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
                        orb_positions.append({
                            "position_id": getattr(pos, "position_id", None),
                            "symbol": getattr(pos, "symbol", None),
                            "position_type": getattr(pos, "position_type", None),
                            "quantity": getattr(pos, "quantity", None),
                            "entry_price": getattr(pos, "entry_price", None),
                            "entry_time": getattr(getattr(pos, "entry_time", None), "isoformat", lambda: None)(),
                            "current_value": getattr(pos, "current_value", None),
                            "unrealized_pnl": getattr(pos, "unrealized_pnl", None),
                            "status": getattr(pos, "status", None),
                            "premium_source": osnap.get("premium_source"),
                            "current_effective_premium": osnap.get("current_effective_premium"),
                            "current_pnl_pct_per_contract": osnap.get("current_pnl_pct_per_contract"),
                            "stop_effective": osnap.get("stop_effective"),
                            "trail_active": osnap.get("trail_active"),
                            "be_active": osnap.get("be_active"),
                            "degraded_data_active": osnap.get("degraded_data_active"),
                        })
                except Exception as orb_err:
                    logger.warning(f"Failed reading ORB 0DTE open options: {orb_err}")

                # Trendline 0DTE open positions
                try:
                    acct = getattr(system, "trendline_account_manager", None)
                    active_map = (acct.active_positions if acct else {}) or {}
                    for pos in list(active_map.values()):
                        md = getattr(pos, "metadata", None)
                        md = md if isinstance(md, dict) else {}
                        osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
                        trendline_positions.append({
                            "position_id": getattr(pos, "position_id", None),
                            "symbol": getattr(pos, "symbol", None),
                            "option_side": getattr(pos, "option_side", None),
                            "direction": str(getattr(pos, "direction", "")),
                            "quantity": getattr(pos, "quantity", None),
                            "entry_cost": getattr(pos, "entry_cost", None),
                            "opened_at": getattr(getattr(pos, "opened_at", None), "isoformat", lambda: None)(),
                            "status": getattr(pos, "status", None),
                            "premium_source": osnap.get("premium_source"),
                            "current_effective_premium": osnap.get("current_effective_premium"),
                            "current_pnl_pct_per_contract": osnap.get("current_pnl_pct_per_contract"),
                            "stop_effective": osnap.get("stop_effective"),
                            "trail_active": osnap.get("trail_active"),
                            "be_active": osnap.get("be_active"),
                            "degraded_data_active": osnap.get("degraded_data_active"),
                        })
                except Exception as tl_err:
                    logger.warning(f"Failed reading Trendline 0DTE open options: {tl_err}")

                def _summary(rows):
                    pnl_rows = [r.get("current_pnl_pct_per_contract") for r in rows if isinstance(r.get("current_pnl_pct_per_contract"), (int, float))]
                    degraded = sum(1 for r in rows if bool(r.get("degraded_data_active")))
                    return {
                        "open_count": len(rows),
                        "pnl_count": len(pnl_rows),
                        "avg_open_pnl_pct_per_contract": (sum(float(x) for x in pnl_rows) / len(pnl_rows)) if pnl_rows else None,
                        "degraded_data_positions": degraded,
                    }

                return web.json_response({
                    "timestamp": datetime.now().isoformat(),
                    "mode": system.config.mode.value if hasattr(system.config, "mode") else "unknown",
                    "orb_0dte": {
                        "total_positions": len(orb_positions),
                        "positions": orb_positions,
                        "summary": _summary(orb_positions),
                    },
                    "trendline_0dte": {
                        "total_positions": len(trendline_positions),
                        "positions": trendline_positions,
                        "summary": _summary(trendline_positions),
                    },
                })
            except Exception as e:
                logger = logging.getLogger("improved_main")
                logger.error(f"Error in /api/options-positions endpoint: {e}")
                return web.json_response({
                    "error": str(e),
                    "orb_0dte": {"total_positions": 0, "positions": [], "summary": {}},
                    "trendline_0dte": {"total_positions": 0, "positions": [], "summary": {}},
                }, status=500)
        
        app = web.Application()
        app.router.add_get('/health', handle_health)
        app.router.add_get('/api/health', handle_health)  # Alias for Cloud Scheduler keep-alive
        app.router.add_get('/metrics', handle_metrics)
        app.router.add_get('/status', handle_status)
        app.router.add_post('/control', handle_control)
        app.router.add_post('/api/build-watchlist', handle_build_watchlist)  # Cloud Scheduler endpoint
        app.router.add_get('/api/watchlist-status', handle_watchlist_status)  # Watchlist status endpoint
        app.router.add_post('/api/cleanup-historical-data', handle_cleanup_historical_data)  # Historical data cleanup endpoint (4:05 PM ET)
        app.router.add_post('/api/cleanup/images', handle_cleanup_images)  # Container image and revision cleanup endpoint (Rev 00259 - Weekly)
        app.router.add_post('/api/oauth/token-renewed', handle_oauth_token_renewed)  # OAuth webhook endpoint
        app.router.add_get('/api/oauth/test-alert', handle_oauth_test_alert)  # OAuth test endpoint
        app.router.add_post('/api/end-of-day-report', handle_end_of_day_report)  # EOD report endpoint (4:05 PM ET) - RESTORED Oct 24
        app.router.add_post('/api/alerts/market-open', handle_market_open_alert)  # Good Morning alert (8:30 AM ET) - RESTORED Oct 24
        app.router.add_post('/api/alerts/validation-candle-700', handle_validation_candle_700)  # 7:00 AM PT: capture open for 7:00-7:15 bar (Cloud Scheduler)
        app.router.add_post('/api/alerts/prefetch-validation-715', handle_prefetch_validation_715)  # 7:15 AM PT: prefetch 7:00 open + 7:15 close (Cloud Scheduler)
        # NOTE: /api/alerts/midnight-token-expiry → OAuth backend handles midnight alert (separate service)
        app.router.add_get('/api/pending-signals', handle_pending_signals)  # Pending signals endpoint (Oct 27, 2025) - Real-time signal monitoring
        app.router.add_get('/api/positions', handle_positions)  # Position tracking endpoint (Rev 00068 - Oct 30, 2025) - Real-time position monitoring
        app.router.add_get('/api/options-positions', handle_options_positions)  # ORB 0DTE + Trendline 0DTE open options PnL snapshot
        app.router.add_post('/api/alerts/market-holiday-check', handle_market_holiday_check)  # Market holiday check endpoint (5:30 AM PT)
        app.router.add_post('/api/manual-orb-capture', handle_manual_orb_capture)  # Manual ORB capture endpoint (Rev 00173)
        app.router.add_get('/', handle_health)  # Root endpoint
        
        # Start server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, ARGS.host, ARGS.port)
        await site.start()
        
        logger = logging.getLogger("improved_main")
        logger.info(f"HTTP server started on {ARGS.host}:{ARGS.port}")
        
        return runner
        
    except Exception as e:
        logger = logging.getLogger("improved_main")
        logger.error(f"Failed to start HTTP server: {e}")
        return None

# --- Graceful Shutdown ---
async def graceful_shutdown(http_runner=None, trading_task=None):
    """Graceful shutdown for all services"""
    logger = logging.getLogger("improved_main")
    logger.info("Initiating graceful shutdown...")
    
    try:
        # Cancel trading task if running
        if trading_task and not trading_task.done():
            logger.info("🛑 Stopping trading system...")
            trading_task.cancel()
            try:
                await trading_task
            except asyncio.CancelledError:
                logger.info("✅ Trading system stopped")
            except Exception as e:
                logger.error(f"❌ Error stopping trading system: {e}")
        
        # OAuth keep-alive handled by Cloud Scheduler (no shutdown needed)
        logger.info("ℹ️  OAuth keep-alive runs automatically via Cloud Scheduler")
        
        # Shutdown HTTP server
        if http_runner:
            await http_runner.cleanup()
        
        # Shutdown integrated system
        system = get_integrated_system()
        await system.shutdown()
        
        logger.info("Graceful shutdown completed")
        
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

# --- Signal Handlers ---
# Global shutdown flag
shutdown_event = asyncio.Event()

def setup_signal_handlers(http_runner=None, trading_task=None):
    """Setup signal handlers for graceful shutdown"""
    def signal_handler(signum, frame):
        logger = logging.getLogger("improved_main")
        logger.info(f"Received signal {signum}, setting shutdown flag...")
        
        # Set the shutdown event to trigger graceful shutdown in the main loop
        shutdown_event.set()
    
    # Only setup signal handlers in the main thread
    try:
        import logging
        log = logging.getLogger("improved_main")
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        log.info("Signal handlers setup successfully")
    except ValueError as e:
        log.warning(f"Cannot setup signal handlers: {e} (not in main thread)")
        # In Cloud Run, signal handling is managed by the platform

# --- Main Function ---
async def main():
    """Main async function"""
    # Setup logging
    logger = setup_logging()
    
    # Cloud Run: start HTTP server before any GCP/network so container passes startup probe
    # (Skip when cloud_run_entry.py already started a minimal server.)
    http_runner = None
    trading_task = None
    if ARGS.cloud_mode and os.getenv("CLOUD_RUN_SERVER_ALREADY") != "1":
        logger.info("☁️  Cloud mode: Starting HTTP server first (fast startup)...")
        http_runner = await start_http_server()
        if http_runner:
            logger.info("✅ HTTP server listening on PORT (Cloud Run startup OK)")
    
    # Setup Google Cloud logging if available (server already listening in cloud mode)
    setup_cloud_logging()
    
    # Detect cloud deployment
    is_cloud_deployment = (
        ARGS.cloud_mode or 
        os.getenv('K_SERVICE') or  # Cloud Run
        os.getenv('GAE_APPLICATION') or  # App Engine
        os.getenv('FUNCTION_NAME') or  # Cloud Functions
        os.getenv('CLOUD_RUN_JOB')  # Cloud Run Jobs
    )
    
    logger.info("🚀 Starting ETrade Strategy - Improved")
    
    # Log version to verify fresh code deployment
    try:
        with open("VERSION.txt", "r") as f:
            version = f.read().strip()
            logger.info(f"📦 CODE VERSION: {version}")
    except:
        logger.warning("⚠️ VERSION.txt not found - cache might be stale")
    
    logger.info(f"Strategy Mode: {ARGS.strategy_mode}")
    logger.info(f"System Mode: {ARGS.system_mode}")
    logger.info(f"Environment: {ARGS.environment}")
    logger.info(f"ETrade Mode: {ARGS.etrade_mode}")
    logger.info(f"Cloud Mode: {ARGS.cloud_mode}")
    logger.info(f"Cloud Deployment Detected: {is_cloud_deployment}")
    logger.info(f"Pre-market Analysis: {ARGS.enable_premarket}")
    logger.info(f"Confluence Trading: {ARGS.enable_confluence}")
    logger.info(f"Multi-Strategy: {ARGS.enable_multi_strategy}")
    logger.info(f"News Sentiment: {ARGS.enable_news_sentiment}")
    logger.info(f"Enhanced Signals: {ARGS.enable_enhanced_signals}")
    logger.info(f"Production Signals: {ARGS.enable_production_signals}")
    logger.info(f"Signal Optimization: {ARGS.enable_signal_optimization}")
    logger.info(f"OAuth Keep-Alive: Managed by Cloud Scheduler")
    
    # Initialize ETrade OAuth and Trader
    logger.info("Initializing ETrade integration...")
    try:
        # Rev 00256 (Jan 22, 2026): CRITICAL FIX - Always use production tokens for both Demo and Live
        # Sandbox tokens are being phased out - both modes use production API
        # Demo mode uses sim account, Live mode uses live account
        secret_manager_env = 'prod'  # Always use production tokens
        logger.info(f"ETrade Mode: {ARGS.etrade_mode} → Using PRODUCTION tokens (sandbox phased out)")
        logger.info(f"   Demo mode: Uses sim account with production tokens")
        logger.info(f"   Live mode: Uses live account with production tokens")
        
        etrade_oauth = get_etrade_oauth_integration(secret_manager_env)
        
        # Check OAuth status
        oauth_status = etrade_oauth.get_auth_status()
        logger.info(f"OAuth Status: {oauth_status}")
        
        if not etrade_oauth.is_authenticated():
            logger.warning("⚠️  OAuth not authenticated. Please setup tokens first.")
            logger.info(f"Run: cd modules && python3 keepalive_oauth.py {ARGS.etrade_mode}")
            if ARGS.etrade_mode == 'live' and not ARGS.cloud_mode:
                logger.warning("⚠️  Live trading requires proper OAuth setup")
                return
            # In cloud mode, continue to start HTTP server even without OAuth
            if ARGS.cloud_mode:
                logger.warning("☁️  Cloud mode: Starting HTTP server despite OAuth issue")
                logger.warning("   System will wait for OAuth tokens from Secret Manager")
        
        logger.info("✅ OAuth authentication ready")
        
        # Rev 00245: Use broker config manager for account selection
        from modules.broker_config_manager import get_broker_config_manager
        broker_config = get_broker_config_manager()
        
        # Get ORB Strategy account configuration
        orb_account_config = broker_config.get_orb_account_config()
        if orb_account_config:
            logger.info(f"📊 ORB Strategy: {orb_account_config.broker_type.value.upper()} account {orb_account_config.account_id} ({orb_account_config.account_name or 'Unnamed'})")
        
        # Rev 00256: Always use production environment for tokens
        # Account selection (sim vs live) is handled separately based on ETRADE_MODE
        etrade_trader = PrimeETradeTrading(environment='prod')
        
        if etrade_trader.initialize():
            logger.info(f"✅ ETrade {ARGS.etrade_mode} trader initialized successfully")
            
            # Rev 00245: Select ORB Strategy account if configured
            # Demo: Use config account ID if valid; else keep auto-selected Sim account (ORB uses $1,000 mock balance).
            # Live: Must select configured live account for real trading.
            if orb_account_config and orb_account_config.broker_type.value == 'etrade':
                if etrade_trader.select_account(orb_account_config.account_id):
                    logger.info(f"✅ Selected ORB Strategy account: {orb_account_config.account_id}")
                else:
                    if ARGS.etrade_mode == 'demo':
                        logger.warning(f"⚠️ Demo: ORB account ID {orb_account_config.account_id} not found/inactive; using auto-selected Sim account (ORB sim balance: $1,000)")
                    else:
                        logger.warning(f"⚠️ Failed to select ORB account {orb_account_config.account_id}, using default")
        else:
            logger.error(f"❌ Failed to initialize ETrade {ARGS.etrade_mode} trader")
            if ARGS.etrade_mode == 'live':
                logger.warning("⚠️  Live trading requires proper OAuth setup")
                logger.info("Run: cd modules && python3 keepalive_oauth.py prod")
                return
        
        # OAuth keep-alive handled automatically by Cloud Scheduler
        # No local keep-alive needed - Cloud Scheduler hits backend every hour
        logger.info("ℹ️  OAuth keep-alive managed by Cloud Scheduler (hourly at :00 and :30)")
    except Exception as e:
        logger.error(f"ETrade initialization failed: {e}")
        if ARGS.etrade_mode == 'live' and not ARGS.cloud_mode:
            logger.warning("⚠️  Live trading requires proper OAuth setup")
            logger.info("Run: cd modules && python3 keepalive_oauth.py prod")
            return
        # In cloud mode, continue to start HTTP server even with OAuth errors
        if ARGS.cloud_mode:
            logger.warning("☁️  Cloud mode: Continuing despite ETrade initialization error")
            logger.warning("   HTTP server will start, system will retry OAuth later")
    
    if not ARGS.cloud_mode:
        http_runner = None
    # trading_task already set above; ensure defined for finally
    if trading_task is None:
        trading_task = None
    
    try:
        # Determine trading mode strictly from ETRADE_MODE to avoid enum mismatches
        resolved_mode = SystemMode.DEMO_MODE if ARGS.etrade_mode == 'demo' else SystemMode.LIVE_MODE

        # Create system configuration
        system_config = TradingConfig(
            mode=resolved_mode,
            strategy_mode=_to_strategy_mode(ARGS.strategy_mode),
            enable_premarket_analysis=ARGS.enable_premarket,
            enable_confluence_trading=ARGS.enable_confluence,
            enable_multi_strategy=ARGS.enable_multi_strategy,
            enable_news_sentiment=ARGS.enable_news_sentiment,
            enable_enhanced_signals=ARGS.enable_enhanced_signals,
            max_positions=ARGS.max_positions,
            scan_frequency=ARGS.scan_frequency
        )
        
        # Rev 00063: Use get_integrated_system() to ensure single instance (fixes health endpoint)
        system = get_integrated_system()
        
        # trading_task already defined above for safe shutdown
        
        # Initialize integrated trading system (without UnifiedServicesManager)
        # Note: UnifiedServicesManager disabled to avoid duplicate alert systems
        logger.info("🔧 Initializing integrated trading system...")
        
        # Initialize system with minimal components (system will create its own)
        # Rev 00236: Support configurable broker (backward compatible with etrade_oauth)
        broker_type = os.getenv('BROKER_TYPE', 'etrade').lower()
        minimal_components = {
            'data_manager': None,  # System will initialize
            'broker_oauth': etrade_oauth,  # Rev 00236: Generic broker OAuth (backward compatible)
            'etrade_oauth': etrade_oauth,  # Backward compatibility
            'broker_type': broker_type,  # Rev 00236: Configurable broker type
            # ARCHIVED (Rev 00173): Signal generator no longer used - ORB generates directly
            # 'signal_generator': None,
            'risk_manager': None,  # System will initialize
            'trade_manager': None,  # System will initialize
            'stealth_trailing': None,  # System will initialize
            'alert_manager': None  # System will initialize with proper config
        }
        
        try:
            await system.initialize(minimal_components)
            logger.info("✅ Trading system initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize trading system: {e}")
            raise
        
        # Initialize 0DTE Strategy (if enabled)
        dte0_manager = None
        etrade_options_api = None  # shared for 0DTE + optional Trendline live option quotes
        ENABLE_0DTE_STRATEGY = os.getenv('ENABLE_0DTE_STRATEGY', 'false').lower() == 'true'

        # Debug logging for 0DTE strategy enablement
        logger.info(f"🔧 ENABLE_0DTE_STRATEGY environment variable: {os.getenv('ENABLE_0DTE_STRATEGY', 'NOT_SET')}")
        logger.info(f"🔧 ENABLE_0DTE_STRATEGY parsed value: {ENABLE_0DTE_STRATEGY}")

        if ENABLE_0DTE_STRATEGY:
            logger.info("🎯 Initializing 0DTE Strategy...")
            try:
                # Add 0DTE Strategy modules to Python path
                import sys
                # Try multiple path locations for 0DTE Strategy
                # 1. Same directory as main.py (./easy0DTE) - NEW STRUCTURE
                # 2. Absolute path in container (/app/easy0DTE)
                # 3. Legacy path for backwards compatibility (../1. The Easy 0DTE Strategy)
                dte_strategy_paths = [
                    os.path.join(os.path.dirname(__file__), 'easy0DTE'),
                    '/app/easy0DTE',
                    os.path.join(os.path.dirname(__file__), '../1. The Easy 0DTE Strategy')  # Legacy fallback
                ]
                dte_strategy_path = None
                for path in dte_strategy_paths:
                    if os.path.exists(path):
                        dte_strategy_path = path
                        break
                
                if dte_strategy_path and os.path.exists(dte_strategy_path):
                    # Add both the base path and modules path to sys.path
                    sys.path.insert(0, dte_strategy_path)
                    modules_path = os.path.join(dte_strategy_path, 'modules')
                    if os.path.exists(modules_path):
                        sys.path.insert(0, modules_path)
                    logger.info(f"✅ Added 0DTE Strategy path: {dte_strategy_path}")
                
                # Import 0DTE modules (try both import styles)
                # Rev 00247 (Jan 20, 2026): Fixed import paths for 0DTE modules
                try:
                    # Try easy0DTE.modules first (correct path)
                    from easy0DTE.modules.convex_eligibility_filter import ConvexEligibilityFilter
                    from easy0DTE.modules.prime_0dte_strategy_manager import Prime0DTEStrategyManager
                    from easy0DTE.modules.options_chain_manager import OptionsChainManager
                    from easy0DTE.modules.options_trading_executor import OptionsTradingExecutor
                    from easy0DTE.modules.mock_options_executor import MockOptionsExecutor
                    logger.info("✅ Loaded 0DTE modules from easy0DTE.modules")
                except ImportError:
                    # Fallback: try modules path (for backward compatibility)
                    try:
                        from modules.convex_eligibility_filter import ConvexEligibilityFilter
                        from modules.prime_0dte_strategy_manager import Prime0DTEStrategyManager
                        from modules.options_chain_manager import OptionsChainManager
                        from modules.options_trading_executor import OptionsTradingExecutor
                        from modules.mock_options_executor import MockOptionsExecutor
                        logger.info("✅ Loaded 0DTE modules from modules (fallback)")
                    except ImportError as e:
                        logger.error(f"❌ Failed to import 0DTE modules from both paths: {e}")
                        raise
                
                # Determine Demo/Live mode for 0DTE (same as ORB Strategy)
                is_demo_mode = ARGS.etrade_mode == 'demo'
                require_live_option_data = os.getenv('REQUIRE_LIVE_OPTION_DATA', 'true').lower() == 'true'
                synthetic_chain_enabled = os.getenv('0DTE_DEMO_SYNTHETIC_CHAIN', 'true').lower() == 'true'
                if require_live_option_data and synthetic_chain_enabled:
                    logger.warning(
                        "0DTE_CHAIN_SOURCE_CONFIG | REQUIRE_LIVE_OPTION_DATA=true but 0DTE_DEMO_SYNTHETIC_CHAIN=true; forcing synthetic off"
                    )
                    os.environ['0DTE_DEMO_SYNTHETIC_CHAIN'] = 'false'
                    synthetic_chain_enabled = False
                
                # Initialize 0DTE components
                convex_filter = ConvexEligibilityFilter(
                    volatility_percentile_threshold=float(os.getenv('0DTE_CONVEX_VOLATILITY_PERCENTILE', '0.80')),
                    orb_range_min_pct=float(os.getenv('0DTE_CONVEX_ORB_RANGE_MIN', '0.35')),
                    momentum_confirmation_required=os.getenv('0DTE_CONVEX_MOMENTUM_REQUIRED', 'true').lower() == 'true',
                    trend_day_required=os.getenv('0DTE_CONVEX_TREND_DAY_REQUIRED', 'true').lower() == 'true'
                )
                
                # Initialize ETrade Options API for both Demo and Live modes (data fidelity parity)
                try:
                    # Rev 00247 (Jan 20, 2026): Fixed import path for ETradeOptionsAPI
                    try:
                        from easy0DTE.modules.etrade_options_api import ETradeOptionsAPI
                        logger.info("✅ Loaded ETradeOptionsAPI from easy0DTE.modules")
                    except ImportError:
                        # Fallback: try modules path
                        from modules.etrade_options_api import ETradeOptionsAPI
                        logger.info("✅ Loaded ETradeOptionsAPI from modules (fallback)")
                    
                    # Rev 00245: Use broker config manager for 0DTE account selection
                    from modules.broker_config_manager import get_broker_config_manager
                    broker_config = get_broker_config_manager()
                    
                    # Get 0DTE Strategy account configuration
                    dte_account_config = broker_config.get_dte_account_config()
                    
                    # Backward compatibility: Also check environment variables
                    dte_account_id = os.getenv('0DTE_ETRADE_ACCOUNT_ID', None)
                    if not dte_account_id and dte_account_config and dte_account_config.broker_type.value == 'etrade':
                        dte_account_id = dte_account_config.account_id
                    
                    dte_secret_name = os.getenv('0DTE_ETRADE_SECRET_NAME', None)
                    
                    if dte_account_id:
                        # Use separate account for 0DTE Strategy
                        logger.info(f"🔗 Initializing 0DTE Strategy with {dte_account_config.broker_type.value.upper()} account: {dte_account_id}")
                        
                        # Option 1: Use separate ETrade instance (if separate OAuth tokens)
                        if dte_secret_name:
                            # TODO: Support separate OAuth tokens via custom PrimeETradeTrading instance
                            # For now, use shared instance but select separate account
                            logger.info(f"   Using separate OAuth tokens from: {dte_secret_name}")
                            logger.warning("⚠️ Separate OAuth tokens not yet implemented - using shared tokens")
                        
                        # Initialize with account selection
                        etrade_options_api = ETradeOptionsAPI(
                            etrade_trading=etrade_trader,  # Can use shared instance or create separate
                            environment='prod',  # Sandbox deprecated; production tokens only
                            account_id=dte_account_id  # Select specific account
                        )
                        logger.info(
                            "✅ ETrade Options API initialized for %s mode with account: %s",
                            "DEMO" if is_demo_mode else "LIVE",
                            dte_account_id,
                        )
                    else:
                        # Use shared ETrade instance (default behavior)
                        etrade_options_api = ETradeOptionsAPI(
                            etrade_trading=etrade_trader,
                            environment='prod'  # Sandbox deprecated; production tokens only
                        )
                        logger.info(
                            "✅ ETrade Options API initialized for %s mode (shared account)",
                            "DEMO" if is_demo_mode else "LIVE",
                        )
                except Exception as e:
                    logger.error(f"❌ Failed to initialize ETrade Options API: {e}")
                    etrade_options_api = None
                    if require_live_option_data:
                        raise RuntimeError("Live option data is required but ETrade Options API is unavailable")
                
                use_live_chain_data = bool(etrade_options_api and etrade_options_api.is_available())
                if require_live_option_data and not use_live_chain_data:
                    raise RuntimeError("Live option chain data required but ETrade API is not authenticated/available")
                logger.info(
                    "0DTE_CHAIN_SOURCE_CONFIG | mode=%s | require_live=%s | etrade_api_available=%s | use_live_chain_data=%s | synthetic_enabled=%s",
                    "demo" if is_demo_mode else "live",
                    require_live_option_data,
                    bool(etrade_options_api and etrade_options_api.is_available()),
                    use_live_chain_data,
                    synthetic_chain_enabled,
                )
                runtime_min_oi = int(os.getenv("0DTE_MIN_OPEN_INTEREST", os.getenv("0DTE_OPTIONS_MIN_OPEN_INTEREST", "100")))
                runtime_min_volume = int(os.getenv("0DTE_MIN_VOLUME", os.getenv("0DTE_OPTIONS_MIN_VOLUME", "50")))
                runtime_max_spread = float(
                    os.getenv("0DTE_MAX_BID_ASK_SPREAD_PCT", os.getenv("0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT", "5.0"))
                )
                runtime_options_min_oi = int(os.getenv("0DTE_OPTIONS_MIN_OPEN_INTEREST", str(runtime_min_oi)))
                runtime_options_min_volume = int(os.getenv("0DTE_OPTIONS_MIN_VOLUME", str(runtime_min_volume)))
                runtime_options_max_spread = float(
                    os.getenv("0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT", str(runtime_max_spread))
                )
                if (
                    runtime_min_oi != runtime_options_min_oi
                    or runtime_min_volume != runtime_options_min_volume
                    or abs(runtime_max_spread - runtime_options_max_spread) > 1e-9
                ):
                    logger.warning(
                        "ORB0DTE_CONFIG_MISMATCH | min_oi=%s vs options_min_oi=%s | min_volume=%s vs options_min_volume=%s | "
                        "max_spread=%s vs options_max_spread=%s",
                        runtime_min_oi,
                        runtime_options_min_oi,
                        runtime_min_volume,
                        runtime_options_min_volume,
                        runtime_max_spread,
                        runtime_options_max_spread,
                    )
                options_chain_manager = OptionsChainManager(
                    min_open_interest=runtime_min_oi,
                    max_bid_ask_spread_pct=runtime_max_spread,
                    min_volume=runtime_min_volume,
                    options_min_open_interest=runtime_options_min_oi,
                    options_max_bid_ask_spread_pct=runtime_options_max_spread,
                    options_min_volume=runtime_options_min_volume,
                    single_leg_min_open_interest=int(os.getenv("0DTE_SINGLE_LEG_MIN_OPEN_INTEREST", "500")),
                    single_leg_min_volume=int(os.getenv("0DTE_SINGLE_LEG_MIN_VOLUME", "200")),
                    single_leg_open_window_minutes=float(os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_MINUTES", "5.0")),
                    single_leg_open_window_oi_mult=float(os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_OI_MULT", "0.50")),
                    single_leg_open_window_volume_mult=float(os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_VOLUME_MULT", "0.50")),
                    liquidity_relax_open_window_minutes=float(os.getenv("0DTE_LIQUIDITY_RELAX_OPEN_WINDOW_MINUTES", "8.0")),
                    liquidity_relax_spread_mult=float(os.getenv("0DTE_LIQUIDITY_RELAX_SPREAD_MULT", "1.35")),
                    liquidity_relax_open_interest_mult=float(os.getenv("0DTE_LIQUIDITY_RELAX_OI_MULT", "0.50")),
                    liquidity_relax_volume_mult=float(os.getenv("0DTE_LIQUIDITY_RELAX_VOLUME_MULT", "0.50")),
                    etrade_options_api=etrade_options_api,
                    use_live_api=use_live_chain_data
                )
                logger.info(
                    "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_MIN_OPEN_INTEREST | runtime_value=%s | source=env_or_default(100)",
                    runtime_min_oi,
                )
                logger.info(
                    "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_MIN_VOLUME | runtime_value=%s | source=env_or_default(50)",
                    runtime_min_volume,
                )
                logger.info(
                    "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_MAX_BID_ASK_SPREAD_PCT | runtime_value=%.3f | source=env_or_default(5.0)",
                    runtime_max_spread,
                )
                logger.info(
                    "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_OPTIONS_MIN_OPEN_INTEREST | runtime_value=%s | source=env_or_unified_with_0DTE_MIN_OPEN_INTEREST",
                    runtime_options_min_oi,
                )
                logger.info(
                    "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_OPTIONS_MIN_VOLUME | runtime_value=%s | source=env_or_unified_with_0DTE_MIN_VOLUME",
                    runtime_options_min_volume,
                )
                logger.info(
                    "ORB0DTE_RUNTIME_CONFIG_AUDIT | env_key=0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT | runtime_value=%.3f | source=env_or_unified_with_0DTE_MAX_BID_ASK_SPREAD_PCT",
                    runtime_options_max_spread,
                )
                logger.info(
                    "ORB0DTE_RUNTIME_CONFIG_AUDIT | selector=single_leg_directional | min_volume=%s | min_open_interest=%s | open_window_minutes=%s | "
                    "open_window_oi_mult=%s | open_window_volume_mult=%s | source=env_or_defaults",
                    os.getenv("0DTE_SINGLE_LEG_MIN_VOLUME", "200"),
                    os.getenv("0DTE_SINGLE_LEG_MIN_OPEN_INTEREST", "500"),
                    os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_MINUTES", "5.0"),
                    os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_OI_MULT", "0.50"),
                    os.getenv("0DTE_SINGLE_LEG_OPEN_WINDOW_VOLUME_MULT", "0.50"),
                )
                if is_demo_mode and not use_live_chain_data:
                    logger.info(
                        f"   0DTE DEMO chains: synthetic={os.getenv('0DTE_DEMO_SYNTHETIC_CHAIN', 'true').lower() == 'true'} "
                        f"(empty chains caused false 'No put/call contracts' before spot-based synthetic)"
                    )
                
                # Initialize Priority Data Collector (optional, for trade optimization)
                priority_collector = None
                if os.getenv('0DTE_PRIORITY_COLLECTOR_ENABLED', 'false').lower() == 'true':
                    try:
                        from modules.options_priority_data_collector import OptionsPriorityDataCollector
                        gcs_bucket = os.getenv('GCS_BUCKET_NAME', None)
                        priority_collector = OptionsPriorityDataCollector(
                            base_dir="priority_optimizer/0dte_data",
                            gcs_bucket=gcs_bucket,
                            gcs_prefix="priority_optimizer/0dte_signals"
                        )
                        logger.info("✅ Priority Data Collector initialized for 0DTE Strategy")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to initialize Priority Data Collector: {e}")
                
                # Initialize Mock Options Executor for Demo Mode
                mock_options_executor = None
                if is_demo_mode:
                    mock_options_executor = MockOptionsExecutor(alert_manager=system.alert_manager)
                    logger.info("✅ Mock Options Executor initialized for Demo Mode")
                    logger.info(f"   - Starting balance: ${mock_options_executor.starting_balance:.2f}")
                
                # Initialize Options Trading Executor
                # Rev 00231: Using only 35% max position size (matches ORB Strategy) - max_position_cost disabled
                _0dte_exec_max_pct = float(os.getenv("0DTE_EXECUTOR_MAX_POSITION_SIZE_PCT", os.getenv("MAX_POSITION_SIZE_PCT", "35"))) / 100.0
                options_executor = OptionsTradingExecutor(
                    auto_partial_enabled=os.getenv('0DTE_AUTO_PARTIAL_ENABLED', 'true').lower() == 'true',
                    partial_profit_pct=float(os.getenv('0DTE_PARTIAL_PROFIT_PCT', '0.50')),
                    runner_profit_pct=float(os.getenv('0DTE_RUNNER_PROFIT_PCT', '2.0')),
                    max_position_cost=999999.0,  # Disabled - percentage cap only (`0DTE_EXECUTOR_MAX_POSITION_SIZE_PCT`)
                    max_position_size_pct=_0dte_exec_max_pct,
                    demo_mode=is_demo_mode,
                    mock_executor=mock_options_executor,
                    alert_manager=system.alert_manager,
                    priority_collector=priority_collector
                )
                
                # Rev 00232: Set ETrade Options API instance if available (for Live Mode)
                # This ensures OptionsTradingExecutor uses the same ETrade instance as OptionsChainManager
                # with proper account selection (if separate 0DTE account is configured)
                if etrade_options_api:
                    options_executor.set_etrade_options_api(etrade_options_api)
                    logger.info("✅ ETrade Options API instance set for Options Trading Executor")
                
                # Initialize 0DTE Strategy Manager
                # Rev 00295: Use full 0dte_list.csv (target_symbols=None) — was hardcoded to [SPX,QQQ,SPY]
                # which caused 0 0DTE signals when Convex passed TSLA/META/KOLD etc (not in 3-symbol list)
                dte0_manager = Prime0DTEStrategyManager(
                    convex_filter=convex_filter,
                    target_symbols=None,  # Load from 0dte_list.csv (row count varies)
                    max_positions=int(os.getenv('0DTE_MAX_POSITIONS', '6')),  # Rev 0035x: Align with API capacity cap
                    enable_lotto_sleeve=os.getenv('0DTE_LOTTO_SLEEVE_ENABLED', 'false').lower() == 'true',
                    priority_collector=priority_collector,
                    alert_manager=system.alert_manager
                )
                
                # Store references for options execution
                dte0_manager.options_chain_manager = options_chain_manager
                dte0_manager.options_executor = options_executor
                
                # Store reference in system for signal hooking
                system.dte0_manager = dte0_manager

                logger.info("✅ 0DTE Strategy initialized successfully")
                logger.info(f"   🔗 0DTE Manager assigned to system: {system.dte0_manager is not None}")
                logger.info(f"   - Mode: {'🎮 DEMO' if is_demo_mode else '💰 LIVE'}")
                logger.info(f"   - Target symbols: {len(dte0_manager.target_symbols)} from 0dte_list.csv")
                logger.info(f"   - Max positions: {dte0_manager.max_positions}")
                logger.info(f"   - Lotto sleeve: {'Enabled' if dte0_manager.enable_lotto_sleeve else 'Disabled'}")
                logger.info(f"   - Convex Filter: Volatility ≥{convex_filter.volatility_percentile_threshold*100:.0f}%, Range ≥{convex_filter.orb_range_min_pct:.2f}%")
            except ImportError as e:
                logger.error(f"❌ Failed to import 0DTE modules: {e}", exc_info=True)
                logger.warning("⚠️ 0DTE Strategy disabled - continuing with ORB Strategy only")
                dte0_manager = None
                system.dte0_manager = None
            except Exception as e:
                logger.error(f"❌ Failed to initialize 0DTE Strategy: {e}", exc_info=True)
                logger.warning("⚠️ 0DTE Strategy disabled - continuing with ORB Strategy only")
                dte0_manager = None
                system.dte0_manager = None
        else:
            logger.info("ℹ️  0DTE Strategy disabled (ENABLE_0DTE_STRATEGY=false)")
        
        # Initialize Easy Trendline Strategy (third sibling path, additive)
        enable_trendline_strategy = os.getenv('ENABLE_TRENDLINE_STRATEGY', 'false').lower() == 'true'
        logger.info(f"🔧 ENABLE_TRENDLINE_STRATEGY parsed value: {enable_trendline_strategy}")
        if enable_trendline_strategy:
            try:
                import sys as _tl_sys

                for _tl_base in (
                    os.path.join(os.path.dirname(__file__), "easy0DTE"),
                    "/app/easy0DTE",
                ):
                    if os.path.isdir(_tl_base):
                        if _tl_base not in _tl_sys.path:
                            _tl_sys.path.insert(0, _tl_base)
                        _tl_mod = os.path.join(_tl_base, "modules")
                        if os.path.isdir(_tl_mod) and _tl_mod not in _tl_sys.path:
                            _tl_sys.path.insert(0, _tl_mod)
                        break

                from easyTrendline import (
                    TrendlineAccountManager,
                    TrendlineFeatureLogger,
                    TrendlineOptionsExecutor,
                    TrendlineReporter,
                    TrendlineSignalEngine,
                )
                from easyTrendline.trendline_config_loader import (
                    load_trendline_config_from_env,
                    load_trendline_option_selection_config,
                    warn_unused_trendline_related_env_keys,
                )
                from modules.config_loader import get_config_value
                from modules.prime_options_stealth_trailing_tp import (
                    TrendlineOptionsStealthEngine,
                    load_option_stealth_config,
                )

                trendline_config = load_trendline_config_from_env(get_config_value)
                option_sel_cfg = load_trendline_option_selection_config(get_config_value)
                warn_unused_trendline_related_env_keys()
                trendline_account_manager = TrendlineAccountManager(
                    starting_balance=float(os.getenv("TRENDLINE_DEMO_STARTING_BALANCE", "5000.0"))
                )
                trendline_options_executor = TrendlineOptionsExecutor(
                    demo_mode=(ARGS.etrade_mode == "demo"),
                    account_manager=trendline_account_manager,
                    option_config=option_sel_cfg,
                    option_quote_api=etrade_options_api,
                    require_live_chain_data=require_live_option_data,
                    trendline_signal_config=trendline_config,
                )
                trendline_signal_engine = TrendlineSignalEngine(config=trendline_config)
                trendline_reporter = TrendlineReporter()
                trendline_feature_logger = TrendlineFeatureLogger()
                trendline_options_stealth = TrendlineOptionsStealthEngine(
                    load_option_stealth_config(get_config_value)
                )

                tl_quote_api = etrade_options_api
                if tl_quote_api is None and ARGS.etrade_mode == "live":
                    try:
                        from easy0DTE.modules.etrade_options_api import ETradeOptionsAPI as _TL_ETradeOptionsAPI

                        tl_quote_api = _TL_ETradeOptionsAPI(etrade_trading=etrade_trader, environment="prod")
                        logger.info("TRENDLINE_PIPELINE | option_quote_api=initialized_for_trendline_live")
                    except Exception as tl_q_err:
                        logger.warning(
                            "TRENDLINE_PIPELINE | option_quote_api=unavailable | reason=%s",
                            tl_q_err,
                        )
                        tl_quote_api = None
                system._trendline_options_quote_api = tl_quote_api
                if getattr(system, "trendline_options_executor", None):
                    system.trendline_options_executor.option_quote_api = tl_quote_api

                system.trendline_account_manager = trendline_account_manager
                system.trendline_options_executor = trendline_options_executor
                system.trendline_signal_engine = trendline_signal_engine
                system.trendline_reporter = trendline_reporter
                system._trendline_feature_logger = trendline_feature_logger
                system.trendline_options_stealth = trendline_options_stealth

                logger.info("TRENDLINE_PIPELINE | stage=init | status=ready")
                logger.info(
                    f"   Trendline mode: {'DEMO' if ARGS.etrade_mode == 'demo' else 'LIVE_READY'} | "
                    f"starting_balance=${trendline_account_manager.starting_balance:.2f}"
                )
            except Exception as trendline_init_error:
                logger.error(
                    f"TRENDLINE_PIPELINE | stage=init | status=failed | reason={trendline_init_error}",
                    exc_info=True
                )
                # Avoid a half-initialized Trendline book (mirrors 0DTE dte0_manager=None on failure).
                system.trendline_account_manager = None
                system.trendline_options_executor = None
                system.trendline_signal_engine = None
                system.trendline_reporter = None
                system._trendline_feature_logger = None
                system.trendline_options_stealth = None
                system._trendline_options_quote_api = None
        else:
            logger.info("ℹ️ Easy Trendline Strategy disabled (ENABLE_TRENDLINE_STRATEGY=false)")
        
        # Dynamic watchlist build removed; static core_list.csv is used
        
        # Start trading system in background thread to avoid blocking HTTP server
        logger.info("🚀 Starting prime trading system with ORB strategy...")
        logger.info("📊 Using static core_list.csv (ORB SO universe; row count from CSV at load)")
        logger.info("🔍 ORB capture at 6:30 AM PT, SO batch at 7:30 AM PT")
        if dte0_manager:
            logger.info("🎯 0DTE Strategy enabled - will listen to ORB signals for QQQ/SPY options")
        if getattr(system, "trendline_signal_engine", None):
            logger.info("📈 Easy Trendline strategy enabled - post-7:30 break+momentum execution path active")
        
        # Start trading system in background task
        trading_task = asyncio.create_task(system.start())
        
        # Setup signal handlers with trading task
        setup_signal_handlers(http_runner, trading_task)
        
        logger.info("✅ Trading system started in background thread")
        
        # Keep main thread alive to handle HTTP requests
        if ARGS.cloud_mode:
            logger.info("🌐 HTTP server running, keeping main thread alive...")
            try:
                # Wait for shutdown signal instead of infinite loop
                await shutdown_event.wait()
                logger.info("Shutdown signal received, initiating graceful shutdown...")
            except KeyboardInterrupt:
                logger.info("Received KeyboardInterrupt, shutting down...")
        else:
            # In non-cloud mode, wait for trading task to complete
            await trading_task
        
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down...")
    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
        import sys  # Ensure sys is available in exception handler
        sys.exit(1)
    finally:
        # Only attempt graceful shutdown if HTTP runner was started
        if http_runner is not None:
            await graceful_shutdown(http_runner, trading_task)

# --- Entry Point ---
if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
