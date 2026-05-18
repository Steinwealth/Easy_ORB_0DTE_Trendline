#!/bin/bash
# ======================================================
# Easy Collector Deployment Check Script
# Verifies scheduler jobs, service status, and data collection
# ======================================================

set -e

PROJECT_ID="easy-etrade-strategy"
REGION="us-central1"
SERVICE_NAME="easy-collector"

echo "🔍 Checking Easy Collector Deployment"
echo "======================================"
echo ""

# Check service status
echo "📡 Checking Cloud Run service..."
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
  --region $REGION \
  --project=$PROJECT_ID \
  --format="value(status.url)" 2>&1)

if [ -z "$SERVICE_URL" ]; then
    echo "❌ Service not found or not accessible"
    exit 1
else
    echo "✅ Service URL: $SERVICE_URL"
fi

# Check scheduler jobs
echo ""
echo "📅 Checking Cloud Scheduler jobs..."
echo ""

# US Market Jobs
echo "US Market Jobs:"
gcloud scheduler jobs list \
  --location=$REGION \
  --project=$PROJECT_ID \
  --filter="name:easy-collector-us" \
  --format="table(name.basename(),schedule,timeZone,state)" 2>&1 | grep -v "^NAME" || echo "  No US jobs found"

echo ""
echo "Crypto Market Jobs:"
gcloud scheduler jobs list \
  --location=$REGION \
  --project=$PROJECT_ID \
  --filter="name:easy-collector-crypto" \
  --format="table(name.basename(),schedule,timeZone,state)" 2>&1 | grep -v "^NAME" || echo "  No Crypto jobs found"

# Check recent job executions
echo ""
echo "📊 Recent Job Executions (last 5):"
gcloud scheduler jobs list \
  --location=$REGION \
  --project=$PROJECT_ID \
  --filter="name:easy-collector" \
  --format="value(name)" 2>&1 | head -1 | while read job_name; do
    if [ ! -z "$job_name" ]; then
        echo "  Checking: $job_name"
        gcloud logging read "resource.type=cloud_scheduler_job AND resource.labels.job_id=$job_name" \
          --limit=5 \
          --project=$PROJECT_ID \
          --format="table(timestamp,severity,textPayload)" 2>&1 | head -6 || echo "    No recent executions"
    fi
done

echo ""
echo "✅ Deployment check complete"
echo ""
echo "📝 Next Steps:"
echo "   1. Verify scheduler jobs are ENABLED"
echo "   2. Check that local storage is enabled (ENABLE_LOCAL_STORAGE=true)"
echo "   3. Monitor data collection at scheduled times"
echo "   4. Use data_summary.py to analyze collected data"
