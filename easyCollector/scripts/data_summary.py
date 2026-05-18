#!/usr/bin/env python3
"""
Easy Collector - Data Summary Script
Analyzes collected snapshot data and generates summaries
"""

import sys
import json
import csv
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.app.storage.local_repo import LocalRepository


def generate_daily_summary(date_str: str, local_repo: LocalRepository) -> Dict[str, Any]:
    """Generate summary for a specific date"""
    snapshots = local_repo.get_snapshots_by_date(date_str)
    
    if not snapshots:
        return {
            "date": date_str,
            "status": "no_data",
            "total_snapshots": 0
        }
    
    # Organize by market and snapshot type
    by_market = defaultdict(lambda: defaultdict(list))
    by_symbol = defaultdict(lambda: defaultdict(list))
    
    for snapshot in snapshots:
        market = snapshot.get("market", "UNKNOWN")
        snapshot_type = snapshot.get("snapshot_type", "UNKNOWN")
        symbol = snapshot.get("symbol", "UNKNOWN")
        
        by_market[market][snapshot_type].append(snapshot)
        by_symbol[symbol][snapshot_type].append(snapshot)
    
    summary = {
        "date": date_str,
        "status": "complete",
        "total_snapshots": len(snapshots),
        "by_market": {},
        "by_symbol": {},
        "collection_times": {
            "us_orb": None,
            "us_signal": None,
            "us_outcome": None,
            "crypto_sessions": {}
        }
    }
    
    # Summarize by market
    for market, types in by_market.items():
        summary["by_market"][market] = {
            "total": sum(len(snaps) for snaps in types.values()),
            "by_type": {st: len(snaps) for st, snaps in types.items()}
        }
    
    # Summarize by symbol (top 20)
    symbol_counts = {sym: sum(len(snaps) for snaps in types.values()) 
                     for sym, types in by_symbol.items()}
    top_symbols = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:20]
    
    summary["by_symbol"] = {
        "top_20": {sym: count for sym, count in top_symbols},
        "total_unique_symbols": len(by_symbol)
    }
    
    # Extract collection times
    for snapshot in snapshots:
        market = snapshot.get("market")
        snapshot_type = snapshot.get("snapshot_type")
        timestamp_et = snapshot.get("timestamp_et")
        
        if market == "US":
            if snapshot_type == "ORB":
                summary["collection_times"]["us_orb"] = timestamp_et
            elif snapshot_type == "SIGNAL":
                summary["collection_times"]["us_signal"] = timestamp_et
            elif snapshot_type == "OUTCOME":
                summary["collection_times"]["us_outcome"] = timestamp_et
        elif market == "CRYPTO":
            session = snapshot.get("session", "UNKNOWN")
            if session not in summary["collection_times"]["crypto_sessions"]:
                summary["collection_times"]["crypto_sessions"][session] = {}
            summary["collection_times"]["crypto_sessions"][session][snapshot_type] = timestamp_et
    
    return summary


def generate_date_range_summary(start_date: str, end_date: str, local_repo: LocalRepository) -> Dict[str, Any]:
    """Generate summary for a date range"""
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    
    daily_summaries = []
    total_snapshots = 0
    markets_covered = set()
    symbols_covered = set()
    
    current = start
    while current <= end:
        date_str = current.strftime("%Y%m%d")
        daily = generate_daily_summary(date_str, local_repo)
        daily_summaries.append(daily)
        
        if daily["status"] == "complete":
            total_snapshots += daily["total_snapshots"]
            markets_covered.update(daily["by_market"].keys())
            for sym_data in daily.get("by_symbol", {}).get("top_20", {}).keys():
                symbols_covered.add(sym_data)
        
        current += timedelta(days=1)
    
    # Calculate coverage
    total_days = (end - start).days + 1
    days_with_data = sum(1 for d in daily_summaries if d["status"] == "complete")
    coverage_pct = (days_with_data / total_days * 100) if total_days > 0 else 0
    
    return {
        "date_range": f"{start_date} to {end_date}",
        "total_days": total_days,
        "days_with_data": days_with_data,
        "coverage_percentage": round(coverage_pct, 2),
        "total_snapshots": total_snapshots,
        "markets_covered": sorted(list(markets_covered)),
        "unique_symbols": len(symbols_covered),
        "daily_summaries": daily_summaries
    }


def print_summary(summary: Dict[str, Any]):
    """Print formatted summary"""
    print("=" * 80)
    print(f"📊 DATA SUMMARY: {summary.get('date', summary.get('date_range', 'Unknown'))}")
    print("=" * 80)
    
    if "date_range" in summary:
        # Date range summary
        print(f"\n📅 Date Range: {summary['date_range']}")
        print(f"   Total Days: {summary['total_days']}")
        print(f"   Days with Data: {summary['days_with_data']} ({summary['coverage_percentage']}%)")
        print(f"   Total Snapshots: {summary['total_snapshots']:,}")
        print(f"   Markets Covered: {', '.join(summary['markets_covered'])}")
        print(f"   Unique Symbols: {summary['unique_symbols']}")
    else:
        # Daily summary
        print(f"\n📅 Date: {summary['date']}")
        print(f"   Status: {summary['status']}")
        print(f"   Total Snapshots: {summary['total_snapshots']:,}")
        
        if summary['status'] == "complete":
            print(f"\n📈 By Market:")
            for market, data in summary['by_market'].items():
                print(f"   {market}: {data['total']} snapshots")
                for stype, count in data['by_type'].items():
                    print(f"      - {stype}: {count}")
            
            print(f"\n🔤 Top Symbols:")
            for sym, count in list(summary['by_symbol']['top_20'].items())[:10]:
                print(f"   {sym}: {count} snapshots")
            
            print(f"\n⏰ Collection Times:")
            times = summary['collection_times']
            if times.get('us_orb'):
                print(f"   US ORB: {times['us_orb']}")
            if times.get('us_signal'):
                print(f"   US SIGNAL: {times['us_signal']}")
            if times.get('us_outcome'):
                print(f"   US OUTCOME: {times['us_outcome']}")
            if times.get('crypto_sessions'):
                for session, stypes in times['crypto_sessions'].items():
                    print(f"   Crypto {session}:")
                    for stype, timestamp in stypes.items():
                        print(f"      - {stype}: {timestamp}")
    
    print("=" * 80)


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate data summaries for Easy Collector")
    parser.add_argument("--date", type=str, help="Date in YYYYMMDD format")
    parser.add_argument("--start-date", type=str, help="Start date in YYYYMMDD format")
    parser.add_argument("--end-date", type=str, help="End date in YYYYMMDD format")
    parser.add_argument("--output", type=str, help="Output JSON file path")
    parser.add_argument("--data-dir", type=str, help="Custom data directory path")
    
    args = parser.parse_args()
    
    # Initialize local repository
    base_dir = Path(args.data_dir) if args.data_dir else None
    local_repo = LocalRepository(base_dir=base_dir)
    
    # Generate summary
    if args.date:
        summary = generate_daily_summary(args.date, local_repo)
    elif args.start_date and args.end_date:
        summary = generate_date_range_summary(args.start_date, args.end_date, local_repo)
    else:
        # Default: today
        today = datetime.now().strftime("%Y%m%d")
        summary = generate_daily_summary(today, local_repo)
    
    # Print summary
    print_summary(summary)
    
    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"\n✅ Summary saved to: {output_path}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
