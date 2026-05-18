#!/usr/bin/env python3
"""
Test Data Collection - Verify yfinance (US), Polygon/Alpaca, and Coinbase clients work
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.clients.yfinance_client import YFinanceClient
from app.clients.coinbase_client import CoinbaseClient
from app.config import get_settings

def test_yfinance_client():
    """Test YFinance US client"""
    print("\n" + "=" * 80)
    print("TESTING YFINANCE US CLIENT")
    print("=" * 80)
    
    client = YFinanceClient()
    
    # Test symbols (mix of indices, ETFs, stocks)
    test_symbols = ["SPY", "QQQ", "NVDA", "TSLA"]
    
    end_utc = datetime.utcnow()
    start_utc = end_utc - timedelta(days=1)
    
    results = {"success": 0, "failed": 0, "errors": []}
    
    for symbol in test_symbols:
        print(f"\n📊 Testing {symbol}...")
        try:
            df = client.get_ohlcv(
                symbol=symbol,
                timeframe="5m",
                start_utc=start_utc,
                end_utc=end_utc
            )
            
            if df is not None and not df.empty:
                print(f"  ✅ Success: Retrieved {len(df)} bars")
                print(f"     Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
                print(f"     Columns: {list(df.columns)}")
                results["success"] += 1
            else:
                print(f"  ❌ Failed: No data returned")
                results["failed"] += 1
                results["errors"].append(f"{symbol}: No data")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
            results["failed"] += 1
            results["errors"].append(f"{symbol}: {str(e)}")
    
    print(f"\n📊 YFinance Client Results:")
    print(f"  Success: {results['success']}/{len(test_symbols)}")
    print(f"  Failed: {results['failed']}/{len(test_symbols)}")
    
    return results["success"] > 0


def test_coinbase_client():
    """Test Coinbase client"""
    print("\n" + "=" * 80)
    print("TESTING COINBASE CLIENT")
    print("=" * 80)
    
    client = CoinbaseClient()
    
    # Test crypto symbols
    test_symbols = ["BTC-PERP", "ETH-PERP", "SOL-PERP"]
    
    end_utc = datetime.utcnow()
    start_utc = end_utc - timedelta(hours=24)
    
    results = {"success": 0, "failed": 0, "errors": []}
    
    for symbol in test_symbols:
        print(f"\n📊 Testing {symbol}...")
        try:
            df = client.get_ohlcv(
                symbol=symbol,
                timeframe="5m",
                start_utc=start_utc,
                end_utc=end_utc
            )
            
            if df is not None and not df.empty:
                print(f"  ✅ Success: Retrieved {len(df)} bars")
                print(f"     Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
                print(f"     Columns: {list(df.columns)}")
                results["success"] += 1
            else:
                print(f"  ❌ Failed: No data returned")
                results["failed"] += 1
                results["errors"].append(f"{symbol}: No data")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
            import traceback
            traceback.print_exc()
            results["failed"] += 1
            results["errors"].append(f"{symbol}: {str(e)}")
    
    print(f"\n📊 Coinbase Client Results:")
    print(f"  Success: {results['success']}/{len(test_symbols)}")
    print(f"  Failed: {results['failed']}/{len(test_symbols)}")
    
    return results["success"] > 0


def test_health_checks():
    """Test health checks"""
    print("\n" + "=" * 80)
    print("TESTING HEALTH CHECKS")
    print("=" * 80)
    
    yfinance_client = YFinanceClient()
    coinbase_client = CoinbaseClient()
    
    print("\n📊 YFinance (US) Health Check:")
    yf_health = yfinance_client.healthcheck()
    print(f"  Status: {yf_health.get('status')}")
    print(f"  Provider: {yf_health.get('provider')}")
    print(f"  Message: {yf_health.get('message')}")
    
    print("\n📊 Coinbase Health Check:")
    coinbase_health = coinbase_client.healthcheck()
    print(f"  Status: {coinbase_health.get('status')}")
    print(f"  Provider: {coinbase_health.get('provider')}")
    print(f"  Message: {coinbase_health.get('message')}")
    
    return yf_health.get('status') in ['healthy', 'degraded'] and coinbase_health.get('status') in ['healthy', 'degraded']


def main():
    """Run all tests"""
    print("=" * 80)
    print("EASY COLLECTOR - DATA COLLECTION TEST")
    print("=" * 80)
    print(f"Test Time: {datetime.now()}")
    
    # Test health checks first
    health_ok = test_health_checks()
    
    # Test data collection
    yf_ok = test_yfinance_client()
    coinbase_ok = test_coinbase_client()
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Health Checks: {'✅ PASS' if health_ok else '❌ FAIL'}")
    print(f"YFinance (US): {'✅ PASS' if yf_ok else '❌ FAIL'}")
    print(f"Coinbase: {'✅ PASS' if coinbase_ok else '❌ FAIL'}")
    
    all_pass = health_ok and yf_ok and coinbase_ok
    
    if all_pass:
        print("\n✅ All tests passed! Ready for deployment.")
        return 0
    else:
        print("\n❌ Some tests failed. Review errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
