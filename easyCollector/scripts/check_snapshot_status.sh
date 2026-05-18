#!/usr/bin/env bash
# Check snapshot collection status: Firestore counts, scheduler jobs, recent runs
# Usage: ./scripts/check_snapshot_status.sh

set -e
PROJECT_ID="${GCP_PROJECT_ID:-easy-etrade-strategy}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="easy-collector"

echo "=============================================="
echo "Easy Collector Snapshot Status Check"
echo "=============================================="
echo "Project: $PROJECT_ID"
echo "Service: $SERVICE_NAME"
echo ""

# 1. Check Cloud Scheduler jobs
echo "1. Cloud Scheduler Jobs"
echo "   Checking if scheduler jobs exist and are enabled..."
JOBS=$(gcloud scheduler jobs list --location="$REGION" --project="$PROJECT_ID" --filter="name:easy-collector*" --format="value(name,state)" 2>/dev/null || echo "")
if [[ -z "$JOBS" ]]; then
  echo "   ❌ No scheduler jobs found. Run: ./SETUP_SCHEDULER.sh"
else
  ENABLED=$(echo "$JOBS" | grep -c "ENABLED" || echo "0")
  PAUSED=$(echo "$JOBS" | grep -c "PAUSED" || echo "0")
  TOTAL=$(echo "$JOBS" | wc -l | tr -d ' ')
  echo "   📊 Total jobs: $TOTAL (Enabled: $ENABLED, Paused: $PAUSED)"
  if [[ "$ENABLED" -lt "$TOTAL" ]]; then
    echo "   ⚠️  Some jobs are paused. Enable with: gcloud scheduler jobs resume JOB_NAME --location=$REGION"
  fi
fi
echo ""

# 2. Check Firestore snapshot counts (last 7 days)
echo "2. Firestore Snapshot Counts (last 7 days)"
echo "   Querying Firestore..."
python3 << 'PYTHON_SCRIPT'
import sys
from datetime import datetime, timedelta, timezone
from google.cloud import firestore

PROJECT_ID = "easy-etrade-strategy"
db = firestore.Client(project=PROJECT_ID)

# Get snapshots from last 7 days
seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
snapshots_ref = db.collection("snapshots")
recent_snapshots = snapshots_ref.where("collection_timestamp", ">=", seven_days_ago).stream()

us_count = 0
crypto_count = 0
us_by_type = {"ORB": 0, "SIGNAL": 0, "OUTCOME": 0}
crypto_by_type = {"ORB": 0, "SIGNAL": 0, "OUTCOME": 0}
sessions = {}

for doc in recent_snapshots:
    data = doc.to_dict()
    market = data.get("market", "")
    snapshot_type = data.get("snapshot_type", "")
    session = data.get("session")
    
    if market == "US":
        us_count += 1
        if snapshot_type in us_by_type:
            us_by_type[snapshot_type] += 1
    elif market == "CRYPTO":
        crypto_count += 1
        if snapshot_type in crypto_by_type:
            crypto_by_type[snapshot_type] += 1
        if session:
            sessions[session] = sessions.get(session, 0) + 1

print(f"   US Market: {us_count} snapshots")
print(f"      ORB: {us_by_type['ORB']}, SIGNAL: {us_by_type['SIGNAL']}, OUTCOME: {us_by_type['OUTCOME']}")
print(f"   Crypto Market: {crypto_count} snapshots")
print(f"      ORB: {crypto_by_type['ORB']}, SIGNAL: {crypto_by_type['SIGNAL']}, OUTCOME: {crypto_by_type['OUTCOME']}")
if sessions:
    print(f"   Crypto Sessions: {', '.join([f'{k}={v}' for k, v in sessions.items()])}")

if us_count == 0 and crypto_count == 0:
    print("   ⚠️  No snapshots found in last 7 days. Check:")
    print("      - Scheduler jobs are enabled and running")
    print("      - Service is deployed with latest fixes")
    print("      - Data sources (Polygon, Coinbase) are working")
elif us_count == 0:
    print("   ⚠️  No US snapshots. Check Polygon key and prefetch/cache logic.")
elif crypto_count == 0:
    print("   ⚠️  No crypto snapshots. Check Coinbase connectivity and snapshot build fixes.")
PYTHON_SCRIPT

echo ""

# 3. Check recent run logs
echo "3. Recent Run Logs (last 24 hours)"
python3 << 'PYTHON_SCRIPT'
import sys
from datetime import datetime, timedelta, timezone
from google.cloud import firestore

PROJECT_ID = "easy-etrade-strategy"
db = firestore.Client(project=PROJECT_ID)

one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
runs_ref = db.collection("runs")
recent_runs = runs_ref.where("timestamp", ">=", one_day_ago).order_by("timestamp", direction=firestore.Query.DESCENDING).limit(10).stream()

runs = []
for doc in recent_runs:
    data = doc.to_dict()
    runs.append({
        "market": data.get("market", "?"),
        "snapshot_type": data.get("snapshot_type", "?"),
        "session": data.get("session"),
        "successful": data.get("successful", 0),
        "failed": data.get("failed", 0),
        "timestamp": data.get("timestamp"),
    })

if not runs:
    print("   ⚠️  No run logs in last 24 hours. Scheduler may not be running.")
else:
    print(f"   Found {len(runs)} recent runs:")
    for r in runs[:5]:
        session_str = f" ({r['session']})" if r.get("session") else ""
        status = "✅" if r["successful"] > 0 and r["failed"] == 0 else "⚠️" if r["successful"] > 0 else "❌"
        print(f"   {status} {r['market']} {r['snapshot_type']}{session_str}: {r['successful']} ok, {r['failed']} failed")
PYTHON_SCRIPT

echo ""

# 4. Check service health
echo "4. Service Health"
BASE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null || echo "")
if [[ -z "$BASE_URL" ]]; then
  echo "   ❌ Could not get service URL. Check deployment."
else
  echo "   Service URL: $BASE_URL"
  HEALTH=$(curl -s --max-time 10 "${BASE_URL}/health" 2>/dev/null || echo '{"status":"error"}')
  if echo "$HEALTH" | grep -q '"status":"healthy"'; then
    echo "   ✅ Service is healthy"
  else
    echo "   ⚠️  Service health check failed or returned unexpected response"
  fi
fi
echo ""

# 5. Summary and next steps
echo "=============================================="
echo "Summary & Next Steps"
echo "=============================================="
echo ""
echo "To ensure snapshots are recording:"
echo ""
echo "1. ✅ Deploy fixes (after EOD):"
echo "   cd '$(dirname "$(dirname "$(pwd)")")'"
echo "   ./deploy-collector.sh"
echo ""
echo "2. ✅ Verify scheduler jobs:"
echo "   gcloud scheduler jobs list --location=$REGION --filter='name:easy-collector*'"
echo ""
echo "3. ✅ Test snapshot collection:"
echo "   ./scripts/test_crypto_snapshot.sh"
echo "   curl -X POST $BASE_URL/collect/us/orb -H 'Content-Type: application/json' -d '{}'"
echo ""
echo "4. ✅ Monitor logs:"
echo "   gcloud logging tail \"resource.type=cloud_run_revision AND resource.labels.service_name=$SERVICE_NAME\" --project $PROJECT_ID"
echo ""
