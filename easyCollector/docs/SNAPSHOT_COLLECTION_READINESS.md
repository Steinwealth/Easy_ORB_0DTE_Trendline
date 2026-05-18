# Snapshot Collection Readiness Checklist

**Goal**: Ensure Easy Collector is successfully recording 0DTE (US) and Crypto snapshots for strategy optimization.

---

## Current Status

### ✅ What's Working
- **Polygon API Key**: Valid and has US stocks access (579 bars for SPY)
- **Coinbase API**: Working (24 candles for BTC-USD)
- **Service Deployment**: Deployed and healthy
- **Secret Manager**: Configured with IAM permissions
- **Deploy Scripts**: Configured to use `POLYGON_API_KEY=polygon-api-key:latest`

### ❌ What Needs Fixing

#### 1. **Crypto Snapshots** - Code Fix Ready (needs deployment)
- **Error**: `'IndicatorService' object has no attribute 'get_ichimoku_preset'`
- **Fix Applied**: Added `get_ichimoku_preset()` method in `indicator_service.py`
- **Status**: Code fixed, needs deployment after EOD

#### 2. **US Snapshots** - Cache/Prefetch Issue
- **Error**: "No cached data available" for symbols
- **Status**: Polygon provider is healthy, but cache is empty
- **Action**: Investigate prefetch logic or direct fetch fallback

#### 3. **Cloud Scheduler Jobs** - Need Verification
- **Status**: Unknown if jobs are created and enabled
- **Action**: Run `./scripts/check_snapshot_status.sh` to verify

---

## Pre-Deployment Checklist

### Code Fixes (✅ Ready)
- [x] Crypto `get_ichimoku_preset` method added
- [x] Crypto `volume_delta` NaN guard added
- [x] Polygon key validation enhanced

### Deployment Steps (After EOD)

1. **Deploy fixes**:
   ```bash
   cd "/Users/eisenstein/Easy Co/1. Easy Trading Software/0. Strategies and Automations/1. The Easy ORB Strategy"
   ./deploy-collector.sh
   ```

2. **Verify deployment**:
   ```bash
   cd easyCollector
   ./scripts/check_polygon_coinbase.sh
   ```

3. **Test snapshot collection**:
   ```bash
   # Test crypto
   ./scripts/test_crypto_snapshot.sh
   
   # Test US (manual trigger)
   BASE_URL=$(gcloud run services describe easy-collector --region us-central1 --project easy-etrade-strategy --format="value(status.url)")
   curl -X POST "${BASE_URL}/collect/us/orb" -H "Content-Type: application/json" -d '{}'
   ```

---

## Post-Deployment Verification

### 1. Check Snapshot Status
```bash
cd easyCollector
./scripts/check_snapshot_status.sh
```

This checks:
- Cloud Scheduler jobs (enabled/paused)
- Firestore snapshot counts (last 7 days)
- Recent run logs (last 24 hours)
- Service health

### 2. Verify Scheduler Jobs
```bash
gcloud scheduler jobs list --location us-central1 --filter="name:easy-collector*" --project easy-etrade-strategy
```

Expected: 15 jobs total
- **US**: 3 jobs (ORB 9:45 ET, SIGNAL 10:30 ET, OUTCOME 3:55 ET) - weekdays only
- **Crypto**: 12 jobs (4 sessions × 3 snapshot types)
  - LONDON: ORB 3:15 ET, SIGNAL 4:00 ET, OUTCOME 7:55 ET
  - US: ORB 8:15 ET, SIGNAL 9:00 ET, OUTCOME 4:55 ET
  - RESET: ORB 5:15 ET, SIGNAL 6:00 ET, OUTCOME 7:55 ET
  - ASIA: ORB 7:15 ET, SIGNAL 8:00 ET, OUTCOME 2:55 ET

### 3. Monitor First Collection Runs

**US Market** (next weekday):
- 9:45 AM ET: ORB snapshots (111 symbols)
- 10:30 AM ET: SIGNAL snapshots (111 symbols)
- 3:55 PM ET: OUTCOME snapshots (111 symbols)

**Crypto Market** (daily):
- Check logs after each scheduled time

```bash
gcloud logging tail "resource.type=cloud_run_revision AND resource.labels.service_name=easy-collector" \
  --project easy-etrade-strategy \
  --format="table(timestamp,jsonPayload.message)" \
  --filter="jsonPayload.message:snapshot"
```

### 4. Verify Firestore Snapshots

**Check snapshot counts**:
```bash
# Using Python/Firestore client
python3 << 'EOF'
from google.cloud import firestore
from datetime import datetime, timedelta, timezone

db = firestore.Client(project="easy-etrade-strategy")
yesterday = datetime.now(timezone.utc) - timedelta(days=1)

# US snapshots
us_snapshots = db.collection("snapshots").where("market", "==", "US").where("collection_timestamp", ">=", yesterday).stream()
us_count = sum(1 for _ in us_snapshots)
print(f"US snapshots (last 24h): {us_count}")

# Crypto snapshots
crypto_snapshots = db.collection("snapshots").where("market", "==", "CRYPTO").where("collection_timestamp", ">=", yesterday).stream()
crypto_count = sum(1 for _ in crypto_snapshots)
print(f"Crypto snapshots (last 24h): {crypto_count}")
EOF
```

**Expected after first full day**:
- **US**: ~333 snapshots (111 symbols × 3 types) on weekdays
- **Crypto**: ~48 snapshots (4 symbols × 3 types × 4 sessions) daily

---

## Troubleshooting

### No Snapshots in Firestore

1. **Check scheduler jobs are enabled**:
   ```bash
   gcloud scheduler jobs list --location us-central1 --filter="name:easy-collector* AND state:PAUSED"
   ```

2. **Check service logs for errors**:
   ```bash
   gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=easy-collector AND severity>=ERROR" \
     --project easy-etrade-strategy \
     --limit 50 \
     --format="table(timestamp,jsonPayload.message)"
   ```

3. **Check run logs in Firestore**:
   ```bash
   python3 << 'EOF'
   from google.cloud import firestore
   from datetime import datetime, timedelta, timezone
   
   db = firestore.Client(project="easy-etrade-strategy")
   recent_runs = db.collection("runs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(10).stream()
   
   for run in recent_runs:
       data = run.to_dict()
       print(f"{data.get('market')} {data.get('snapshot_type')}: {data.get('successful')} ok, {data.get('failed')} failed")
       if data.get('errors'):
           print(f"  Errors: {data.get('errors')[:3]}")
   EOF
   ```

### US Snapshots Failing

- **"No cached data available"**: Check prefetch logic or direct fetch fallback
- **Polygon 403**: Verify key is valid: `./scripts/check_polygon_secret_ready.sh --validate-key`
- **Empty responses**: Check Polygon health: `curl "BASE_URL/debug/us/provider_smoke?symbol=SPY&bars=50"`

### Crypto Snapshots Failing

- **`get_ichimoku_preset` error**: Deploy latest code fix
- **`volume_delta` validation**: Should be fixed in latest code
- **Coinbase connectivity**: Check `curl "BASE_URL/debug/crypto/product_smoke?symbol=BTC-PERP"`

---

## Success Criteria

### After Deployment + First Collection Cycle

✅ **US Market**:
- ORB snapshots: 111 successful (one per symbol)
- SIGNAL snapshots: 111 successful
- OUTCOME snapshots: 111 successful
- Total: ~333 snapshots per weekday

✅ **Crypto Market**:
- All 4 sessions (LONDON, US, RESET, ASIA) collecting
- All 3 snapshot types (ORB, SIGNAL, OUTCOME) per session
- All 4 symbols (BTC-PERP, ETH-PERP, SOL-PERP, XRP-PERP)
- Total: ~48 snapshots per day

✅ **Data Quality**:
- Snapshots have all indicators populated
- ORB integrity checks pass
- Outcome labels computed correctly
- Payload sizes reasonable (< 100 KB)

---

## Next Steps After Successful Collection

1. **Download snapshots for analysis**:
   ```bash
   cd easyCollector
   python3 scripts/download_firestore_rest.py --days 7 --verify
   ```

2. **Review data structure**:
   - Check indicator completeness
   - Verify ORB block calculations
   - Validate outcome labels

3. **Optimize strategies**:
   - Analyze snapshot patterns
   - Identify high-probability setups
   - Refine entry/exit rules

---

## Files Reference

- **Deployment**: `deploy-collector.sh` (from ORB root)
- **Scheduler Setup**: `SETUP_SCHEDULER.sh`
- **Status Check**: `scripts/check_snapshot_status.sh`
- **Validation**: `scripts/validate_snapshot_collection.py`
- **Crypto Test**: `scripts/test_crypto_snapshot.sh`
- **Data Sources**: `scripts/check_polygon_coinbase.sh`

---

**Last Updated**: February 7, 2026  
**Status**: Code fixes ready, awaiting deployment after EOD
