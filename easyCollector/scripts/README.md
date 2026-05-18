# Easy Collector Scripts

## ⭐ PRIMARY SCRIPT: `download_firestore_rest.py`

**This is the ONLY script you need to download Firestore data.**

### Purpose
Download Easy Collector snapshots from Firestore using REST API (fastest, most reliable method).

### Usage

```bash
# Download last 7 days (default)
python3 scripts/download_firestore_rest.py --days 7

# Download last 30 days
python3 scripts/download_firestore_rest.py --days 30

# Download all data (no date filter)
python3 scripts/download_firestore_rest.py --days 0

# Download with verification report
python3 scripts/download_firestore_rest.py --days 7 --verify

# Download to specific directory
python3 scripts/download_firestore_rest.py --days 7 --output-dir ./my_data
```

### Features
- ✅ Uses Firestore REST API (fastest method, same as Ichimoku)
- ✅ Automatically handles pagination (downloads all documents)
- ✅ Automatically falls back to Python Firestore client if REST API unavailable
- ✅ Downloads snapshots and run logs from Firestore
- ✅ Verifies data completeness (with `--verify` flag)
- ✅ Generates summary statistics
- ✅ Saves data as JSON files
- ✅ Shows statistics by market, type, symbol, and session

### Output Files
All files are saved to: `data/easy_collector/firestore_downloads/`

- `snapshots_YYYYMMDD_HHMMSS.json` - Full snapshot data (all data points)
- `run_logs_YYYYMMDD_HHMMSS.json` - Collection run logs (success/failure tracking)
- `summary_YYYYMMDD_HHMMSS.json` - Summary statistics and verification results

### Requirements
- `gcloud` CLI installed and authenticated (for REST API method)
  ```bash
  gcloud auth application-default login
  ```
- `requests` Python library (usually included)
- Falls back to `google-cloud-firestore` if REST API unavailable

### What Gets Downloaded

**Snapshots** include all expected data points:
- Core fields: market, symbol, snapshot_type, timestamps, session bounds
- Price candle data: open, high, low, close, volume, gaps, wicks
- ORB block data: orb_high, orb_low, orb_range_pct, post-ORB extremes
- Technical indicators: RSI, MACD, Bollinger Bands, Ichimoku, EMAs
- Trend & momentum: EMAs, ROC, momentum indicators
- Volume & VWAP: volume ratios, VWAP distance
- Signal data: For SIGNAL snapshots (signal_direction, confidence, etc.)
- Outcome data: For OUTCOME snapshots (MFE/MAE, opportunity_score, synthetic_r, etc.)
- Calendar tags: Holidays, early closes, macro events

**Run Logs** track collection attempts:
- Success/failure counts per run
- Error messages for failed collections
- Duration and timestamp for each run
- Market, snapshot type, and session information

### Verification

Use `--verify` flag to check data completeness:

```bash
python3 scripts/download_firestore_rest.py --days 7 --verify
```

This will show:
- Completeness score (% of expected data points present)
- Missing data blocks (if any)
- Missing fields (if any)
- Common errors from run logs (if no snapshots found)

### Troubleshooting

**If no snapshots are found:**
- Check run logs for error messages
- Verify collection service is running
- Check API credentials are configured
- Review Cloud Run logs for collection errors

**If REST API fails:**
- Script automatically falls back to Firestore Python client
- Ensure `gcloud auth application-default login` is run
- Check internet connectivity

**If import errors occur:**
- Install missing dependencies: `pip install requests`
- For Firestore client fallback: `pip install google-cloud-firestore`

---

## Other Scripts

### `test_data_collection.py` - Client Testing Script

Test E*TRADE and Coinbase clients to verify data collection works.

```bash
python3 scripts/test_data_collection.py
```

### `data_summary.py` - Local Data Analysis

Generate summaries from locally downloaded data (uses LocalRepository).

```bash
python3 scripts/data_summary.py --date 20250101
python3 scripts/data_summary.py --start-date 20250101 --end-date 20250107
```

### `analyze_profitable_patterns.py` - Pattern Analysis

Analyze ORB → SIGNAL → OUTCOME patterns to find profitable signals.

```bash
python3 scripts/analyze_profitable_patterns.py
```

---

## Quick Start

**To download Firestore data (recommended)**:
```bash
cd "0. Strategies and Automations/1. The Easy ORB Strategy/easyCollector"
python3 scripts/download_firestore_rest.py --days 7 --verify
```

**To download all data**:
```bash
python3 scripts/download_firestore_rest.py --days 0
```

---

## Notes

- **Primary script**: `download_firestore_rest.py` (REST API method, fastest, requires `gcloud` CLI)
- Project ID: `easy-etrade-strategy` (hardcoded in script)
- Download directory: `data/easy_collector/firestore_downloads/`
- Script automatically handles pagination (no limit on document count)
