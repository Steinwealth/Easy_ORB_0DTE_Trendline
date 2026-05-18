#!/usr/bin/env python3
"""
Easy Collector - Firestore Data Download Script
Primary script for downloading and reviewing Easy Collector datasets from Firestore.

Uses Firestore REST API for fast, reliable data collection (same method as Ichimoku).
Automatically falls back to Python Firestore client if REST API unavailable.

Usage:
    python3 scripts/download_firestore_rest.py --days 7
    python3 scripts/download_firestore_rest.py --days 30 --output-dir ./my_data
    python3 scripts/download_firestore_rest.py --days 0  # Download all data
"""

import sys
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any, Optional

PROJECT_ID = "easy-etrade-strategy"
SNAPSHOTS_COLLECTION = "snapshots"
RUNS_COLLECTION = "runs"


def _convert_firestore_value(value: Dict) -> Any:
    """Convert Firestore value to Python type"""
    if 'stringValue' in value:
        return value['stringValue']
    elif 'integerValue' in value:
        return int(value['integerValue'])
    elif 'doubleValue' in value:
        return float(value['doubleValue'])
    elif 'booleanValue' in value:
        return value['booleanValue']
    elif 'timestampValue' in value:
        # Parse Firestore timestamp
        ts_str = value['timestampValue']
        try:
            return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except:
            return ts_str
    elif 'nullValue' in value:
        return None
    elif 'arrayValue' in value:
        return [_convert_firestore_value(v) for v in value['arrayValue'].get('values', [])]
    elif 'mapValue' in value:
        fields = value['mapValue'].get('fields', {})
        return {k: _convert_firestore_value(v) for k, v in fields.items()}
    else:
        return value


def download_via_rest_api(collection: str, project_id: str = PROJECT_ID) -> List[Dict]:
    """Download collection via Firestore REST API (fastest method) with pagination"""
    try:
        import requests
        
        print(f"   Using REST API method (fastest)...")
        
        # Get access token
        result = subprocess.run(
            ['gcloud', 'auth', 'print-access-token'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            raise Exception(f"Failed to get access token: {result.stderr}")
        
        token = result.stdout.strip()
        if not token:
            raise Exception("Empty access token")
        
        # Firestore REST API endpoint
        base_url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents"
        collection_url = f"{base_url}/{collection}"
        
        headers = {'Authorization': f'Bearer {token}'}
        print(f"   Fetching from: {collection_url}")
        
        all_documents = []
        page_token = None
        page_count = 0
        
        while True:
            params = {}
            if page_token:
                params['pageToken'] = page_token
            
            response = requests.get(collection_url, headers=headers, params=params, timeout=60)
            
            if response.status_code == 200:
                firestore_data = response.json()
                documents = firestore_data.get('documents', [])
                all_documents.extend(documents)
                page_count += 1
                
                print(f"   Page {page_count}: Found {len(documents)} documents (total: {len(all_documents)})")
                
                # Check for next page
                page_token = firestore_data.get('nextPageToken')
                if not page_token:
                    break
            else:
                raise Exception(f"REST API returned status {response.status_code}: {response.text[:200]}")
        
        print(f"   ✅ Found {len(all_documents)} total documents via REST API ({page_count} pages)")
        
        data = []
        for doc in all_documents:
            fields = doc.get('fields', {})
            
            # Convert Firestore document to dict
            doc_data = {}
            for key, value in fields.items():
                doc_data[key] = _convert_firestore_value(value)
            
            # Get document ID
            doc_name = doc.get('name', '')
            doc_id = doc_name.split('/')[-1] if '/' in doc_name else doc_name
            doc_data['_document_id'] = doc_id
            doc_data['_collected_at'] = datetime.now(timezone.utc).isoformat()
            
            data.append(doc_data)
        
        print(f"   ✅ Successfully converted {len(data)} documents")
        return data
    
    except FileNotFoundError:
        print("   ⚠️  gcloud CLI not found, falling back to Firestore client...")
        return None
    except subprocess.TimeoutExpired:
        print("   ⚠️  gcloud auth timed out, falling back to Firestore client...")
        return None
    except Exception as e:
        print(f"   ⚠️  REST API method failed: {e}")
        return None


def download_via_client(collection: str, project_id: str = PROJECT_ID, limit: Optional[int] = None) -> List[Dict]:
    """Download collection via Firestore Python client (fallback)"""
    try:
        from google.cloud import firestore
        
        print("   Using Firestore Python client (fallback)...")
        db = firestore.Client(project=project_id)
        collection_ref = db.collection(collection)
        
        if limit:
            docs = list(collection_ref.limit(limit).stream())
        else:
            docs = list(collection_ref.stream())
        
        print(f"   ✅ Found {len(docs)} documents via Firestore client")
        
        data = []
        for doc in docs:
            doc_data = doc.to_dict()
            doc_data['_document_id'] = doc.id
            doc_data['_collected_at'] = datetime.now(timezone.utc).isoformat()
            data.append(doc_data)
        
        return data
    except Exception as e:
        print(f"   ❌ Firestore client also failed: {e}")
        print(f"   💡 Try: gcloud auth application-default login")
        return []


def download_collection(collection: str, project_id: str = PROJECT_ID, limit: Optional[int] = None) -> List[Dict]:
    """Download collection using REST API first, fallback to client"""
    print(f"\n📥 Downloading '{collection}' collection...")
    
    # Try REST API first
    data = download_via_rest_api(collection, project_id)
    
    if data is not None:
        return data
    
    # Fallback to client
    return download_via_client(collection, project_id, limit)


def filter_recent_snapshots(snapshots: List[Dict], days: int = 7) -> List[Dict]:
    """Filter snapshots to recent ones"""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    filtered = []
    for snap in snapshots:
        coll_ts = snap.get('collection_timestamp')
        if coll_ts:
            if isinstance(coll_ts, str):
                try:
                    coll_ts = datetime.fromisoformat(coll_ts.replace('Z', '+00:00'))
                except:
                    continue
            
            if coll_ts >= cutoff_date:
                filtered.append(snap)
        else:
            # If no collection_timestamp, include it (might be old data)
            filtered.append(snap)
    
    return filtered


def verify_data_completeness(snapshots: List[Dict]) -> Dict[str, Any]:
    """Verify data completeness and check for missing fields"""
    if not snapshots:
        return {
            "status": "no_data",
            "completeness_score": 0,
            "missing_blocks": {},
            "missing_fields": {}
        }
    
    # Expected data blocks
    expected_blocks = {
        "price_candle": ["open", "high", "low", "close", "last_price"],
        "orb_block": ["orb_open", "orb_high", "orb_low", "orb_close", "orb_range_pct"],
        "trend_momentum": ["ema_8", "ema_21", "roc"],
        "volatility": ["atr", "atr_pct"],
        "volume_vwap": ["volume", "vwap"],
        "oscillators": ["rsi", "stoch_k", "stoch_d"],
        "macd": ["macd", "macd_signal", "macd_histogram"],
        "bollinger": ["bollinger_upper", "bollinger_middle", "bollinger_lower"],
        "ichimoku": ["tenkan", "kijun", "senkou_a", "senkou_b"],
        "calendar": ["is_market_closed", "is_us_holiday"]
    }
    
    missing_blocks = defaultdict(int)
    missing_fields = defaultdict(lambda: defaultdict(int))
    snapshots_checked = 0
    
    for snap in snapshots:
        snapshots_checked += 1
        
        # Check each expected block
        for block_name, required_fields in expected_blocks.items():
            if block_name not in snap or not isinstance(snap[block_name], dict):
                missing_blocks[block_name] += 1
            else:
                block_data = snap[block_name]
                for field in required_fields:
                    if field not in block_data or block_data[field] is None:
                        missing_fields[block_name][field] += 1
        
        # Check snapshot-type-specific data
        snapshot_type = snap.get("snapshot_type")
        market = snap.get("market")
        
        if snapshot_type == "SIGNAL":
            if "signal" not in snap or not isinstance(snap["signal"], dict):
                missing_blocks["signal"] += 1
        
        if snapshot_type == "OUTCOME":
            outcome_key = "outcome_us" if market == "US" else "outcome_crypto"
            if outcome_key not in snap or not isinstance(snap[outcome_key], dict):
                missing_blocks[outcome_key] += 1
    
    # Calculate completeness score
    total_checks = snapshots_checked * len(expected_blocks)
    missing_count = sum(missing_blocks.values())
    completeness_score = ((total_checks - missing_count) / total_checks * 100) if total_checks > 0 else 0
    
    return {
        "status": "complete" if completeness_score > 95 else "incomplete",
        "completeness_score": round(completeness_score, 2),
        "missing_blocks": dict(missing_blocks),
        "missing_fields": {k: dict(v) for k, v in missing_fields.items()},
        "snapshots_checked": snapshots_checked
    }


def generate_summary(snapshots: List[Dict], run_logs: List[Dict]) -> Dict[str, Any]:
    """Generate summary statistics"""
    summary = {
        "collection_date": datetime.now(timezone.utc).isoformat(),
        "total_snapshots": len(snapshots),
        "total_run_logs": len(run_logs),
        "by_market": defaultdict(int),
        "by_type": defaultdict(int),
        "by_symbol": defaultdict(int),
        "by_session": defaultdict(int),
        "date_range": {"earliest": None, "latest": None}
    }
    
    for snap in snapshots:
        market = snap.get("market", "UNKNOWN")
        stype = snap.get("snapshot_type", "UNKNOWN")
        symbol = snap.get("symbol", "UNKNOWN")
        session = snap.get("session", "NONE")
        
        summary["by_market"][market] += 1
        summary["by_type"][stype] += 1
        summary["by_symbol"][symbol] += 1
        summary["by_session"][session] += 1
        
        coll_ts = snap.get("collection_timestamp")
        if coll_ts:
            if isinstance(coll_ts, str):
                try:
                    coll_ts = datetime.fromisoformat(coll_ts.replace('Z', '+00:00'))
                except:
                    coll_ts = None
            
            if coll_ts:
                if summary["date_range"]["earliest"] is None or coll_ts < summary["date_range"]["earliest"]:
                    summary["date_range"]["earliest"] = coll_ts
                if summary["date_range"]["latest"] is None or coll_ts > summary["date_range"]["latest"]:
                    summary["date_range"]["latest"] = coll_ts
    
    # Convert defaultdicts to dicts
    summary["by_market"] = dict(summary["by_market"])
    summary["by_type"] = dict(summary["by_type"])
    summary["by_symbol"] = dict(summary["by_symbol"])
    summary["by_session"] = dict(summary["by_session"])
    
    # Add verification results
    verification = verify_data_completeness(snapshots)
    summary["verification"] = verification
    
    return summary


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Download Easy Collector Firestore data (REST API method)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download last 7 days (default)
  python3 scripts/download_firestore_rest.py --days 7

  # Download all data
  python3 scripts/download_firestore_rest.py --days 0

  # Download last 30 days to specific directory
  python3 scripts/download_firestore_rest.py --days 30 --output-dir ./my_data
        """
    )
    parser.add_argument("--days", type=int, default=7, help="Days to filter (default: 7, 0 = all)")
    parser.add_argument("--output-dir", type=str, help="Output directory")
    parser.add_argument("--limit", type=int, help="Limit number of documents (client fallback only)")
    parser.add_argument("--verify", action="store_true", help="Verify data completeness and show detailed report")
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("EASY COLLECTOR - FIRESTORE DATA DOWNLOAD (REST API)")
    print("=" * 80)
    print(f"Project: {PROJECT_ID}")
    print(f"Collections: {SNAPSHOTS_COLLECTION}, {RUNS_COLLECTION}")
    if args.days > 0:
        print(f"Filtering: Last {args.days} days")
    print("=" * 80)
    
    # Setup output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path(__file__).parent.parent.parent / "data" / "easy_collector" / "firestore_downloads"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📁 Output directory: {output_dir}\n")
    
    # Download snapshots
    snapshots = download_collection(SNAPSHOTS_COLLECTION, PROJECT_ID, args.limit)
    
    # Filter by date if requested
    if args.days > 0 and snapshots:
        original_count = len(snapshots)
        snapshots = filter_recent_snapshots(snapshots, args.days)
        print(f"   Filtered to {len(snapshots)} snapshots from last {args.days} days (from {original_count} total)")
    
    # Download run logs
    run_logs = download_collection(RUNS_COLLECTION, PROJECT_ID, args.limit or 100)
    
    # Generate summary
    summary = generate_summary(snapshots, run_logs)
    
    # Save files
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if snapshots:
        snapshots_file = output_dir / f"snapshots_{timestamp}.json"
        with open(snapshots_file, 'w') as f:
            json.dump(snapshots, f, indent=2, default=str)
        print(f"\n✅ Saved {len(snapshots)} snapshots to: {snapshots_file.name}")
    
    if run_logs:
        runs_file = output_dir / f"run_logs_{timestamp}.json"
        with open(runs_file, 'w') as f:
            json.dump(run_logs, f, indent=2, default=str)
        print(f"✅ Saved {len(run_logs)} run logs to: {runs_file.name}")
    
    summary_file = output_dir / f"summary_{timestamp}.json"
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"✅ Saved summary to: {summary_file.name}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("COLLECTION SUMMARY")
    print("=" * 80)
    print(f"Total Snapshots: {summary['total_snapshots']:,}")
    print(f"Total Run Logs: {summary['total_run_logs']:,}")
    
    if summary['by_market']:
        print(f"\n📈 By Market:")
        for market, count in sorted(summary['by_market'].items()):
            print(f"   {market}: {count:,}")
    
    if summary['by_type']:
        print(f"\n📊 By Type:")
        for stype, count in sorted(summary['by_type'].items()):
            print(f"   {stype}: {count:,}")
    
    if summary['by_symbol']:
        print(f"\n🔤 Top 10 Symbols:")
        top_symbols = sorted(summary['by_symbol'].items(), key=lambda x: x[1], reverse=True)[:10]
        for symbol, count in top_symbols:
            print(f"   {symbol}: {count:,}")
    
    if summary["date_range"]["earliest"]:
        print(f"\n⏰ Date Range:")
        print(f"   Earliest: {summary['date_range']['earliest']}")
        print(f"   Latest: {summary['date_range']['latest']}")
    
    # Show verification results if requested or if no snapshots found
    if args.verify or summary['total_snapshots'] == 0:
        verification = summary.get("verification", {})
        
        if summary['total_snapshots'] == 0:
            print(f"\n⚠️  NO SNAPSHOTS FOUND IN FIRESTORE")
            print(f"\n📊 Run Logs Analysis:")
            successful_runs = [log for log in run_logs if log.get("successful", 0) > 0]
            failed_runs = [log for log in run_logs if log.get("failed", 0) > 0]
            print(f"   Successful runs: {len(successful_runs)}")
            print(f"   Failed runs: {len(failed_runs)}")
            
            if failed_runs:
                print(f"\n❌ Common errors in failed runs:")
                error_counts = defaultdict(int)
                for log in failed_runs:
                    for error in log.get("errors", []):
                        error_type = error.split(":")[0] if ":" in error else error
                        error_counts[error_type] += 1
                
                for error_type, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
                    print(f"   {error_type}: {count} occurrences")
        else:
            print(f"\n🔍 Data Verification:")
            print(f"   Completeness Score: {verification.get('completeness_score', 0)}%")
            print(f"   Status: {verification.get('status', 'unknown').upper()}")
            
            if verification.get("missing_blocks"):
                print(f"\n⚠️  Missing Data Blocks:")
                for block, count in sorted(verification["missing_blocks"].items()):
                    pct = (count / verification["snapshots_checked"] * 100) if verification["snapshots_checked"] > 0 else 0
                    print(f"   {block}: {count} snapshots ({pct:.1f}%)")
            
            if verification.get("missing_fields"):
                print(f"\n⚠️  Missing Fields (Top 10):")
                all_missing = []
                for block, fields in verification["missing_fields"].items():
                    for field, count in fields.items():
                        all_missing.append((f"{block}.{field}", count))
                
                for field_path, count in sorted(all_missing, key=lambda x: x[1], reverse=True)[:10]:
                    pct = (count / verification["snapshots_checked"] * 100) if verification["snapshots_checked"] > 0 else 0
                    print(f"   {field_path}: {count} snapshots ({pct:.1f}%)")
    
    print("=" * 80)
    print(f"\n✅ Download complete! Files saved to: {output_dir}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
