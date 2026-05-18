#!/bin/bash
# ======================================================
# Cloud Scheduler Setup Script for Easy Collector
# Project: easy-etrade-strategy
# Creates all 15 jobs: US (3) + Crypto (12) with OIDC auth
# ======================================================

set -e

PROJECT_ID="easy-etrade-strategy"
REGION="us-central1"
SERVICE_ACCOUNT="easy-collector-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

# Get the Easy Collector service URL
COLLECTOR_URL=$(gcloud run services describe easy-collector --region $REGION --format="value(status.url)" --project=$PROJECT_ID)

echo "🚀 Setting up Cloud Scheduler jobs for Easy Collector"
echo "📍 Service URL: $COLLECTOR_URL"
echo "🔐 Service Account: $SERVICE_ACCOUNT"
echo ""

# Set GCP project
gcloud config set project $PROJECT_ID

echo "=========================================="
echo "US Market Snapshots (Weekdays Only)"
echo "=========================================="
echo ""

# US ORB (9:45 ET weekdays)
echo "📅 Creating US ORB job (9:45 AM ET weekdays)..."
gcloud scheduler jobs create http easy-collector-us-orb \
  --location $REGION \
  --schedule "45 9 * * 1-5" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/us/orb" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect US ORB snapshots at 9:45 AM ET on weekdays" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# US SIGNAL (10:30 ET weekdays)
echo "📅 Creating US SIGNAL job (10:30 AM ET weekdays)..."
gcloud scheduler jobs create http easy-collector-us-signal \
  --location $REGION \
  --schedule "30 10 * * 1-5" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/us/signal" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect US SIGNAL snapshots at 10:30 AM ET on weekdays" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# US OUTCOME (15:55 ET weekdays)
echo "📅 Creating US OUTCOME job (3:55 PM ET weekdays)..."
gcloud scheduler jobs create http easy-collector-us-outcome \
  --location $REGION \
  --schedule "55 15 * * 1-5" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/us/outcome" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect US OUTCOME snapshots at 3:55 PM ET on weekdays (5 min before close)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

echo ""
echo "=========================================="
echo "Crypto Market Snapshots - LONDON Session (Daily)"
echo "=========================================="
echo ""

# Crypto London ORB (03:15 ET daily)
echo "📅 Creating Crypto London ORB job (3:15 AM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-london-orb \
  --location $REGION \
  --schedule "15 3 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/LONDON/orb" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "LONDON"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto London session ORB snapshots at 3:15 AM ET (open+15m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto London SIGNAL (04:00 ET daily)
echo "📅 Creating Crypto London SIGNAL job (4:00 AM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-london-signal \
  --location $REGION \
  --schedule "0 4 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/LONDON/signal" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "LONDON"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto London session SIGNAL snapshots at 4:00 AM ET (open+60m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto London OUTCOME (07:55 ET daily - before US open)
echo "📅 Creating Crypto London OUTCOME job (7:55 AM ET daily - before US open)..."
gcloud scheduler jobs create http easy-collector-crypto-london-outcome \
  --location $REGION \
  --schedule "55 7 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/LONDON/outcome" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "LONDON"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto London session OUTCOME snapshots at 7:55 AM ET (5 min before US open)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

echo ""
echo "=========================================="
echo "Crypto Market Snapshots - US Session (Daily)"
echo "=========================================="
echo ""

# Crypto US ORB (08:15 ET daily)
echo "📅 Creating Crypto US ORB job (8:15 AM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-us-orb \
  --location $REGION \
  --schedule "15 8 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/US/orb" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "US"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto US session ORB snapshots at 8:15 AM ET (open+15m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto US SIGNAL (09:00 ET daily)
echo "📅 Creating Crypto US SIGNAL job (9:00 AM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-us-signal \
  --location $REGION \
  --schedule "0 9 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/US/signal" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "US"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto US session SIGNAL snapshots at 9:00 AM ET (open+60m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto US OUTCOME (16:55 ET daily - before Reset)
echo "📅 Creating Crypto US OUTCOME job (4:55 PM ET daily - before Reset)..."
gcloud scheduler jobs create http easy-collector-crypto-us-outcome \
  --location $REGION \
  --schedule "55 16 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/US/outcome" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "US"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto US session OUTCOME snapshots at 4:55 PM ET (5 min before Reset open)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

echo ""
echo "=========================================="
echo "Crypto Market Snapshots - RESET Session (Daily)"
echo "=========================================="
echo ""

# Crypto Reset ORB (17:15 ET daily)
echo "📅 Creating Crypto Reset ORB job (5:15 PM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-reset-orb \
  --location $REGION \
  --schedule "15 17 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/RESET/orb" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "RESET"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto Reset session ORB snapshots at 5:15 PM ET (open+15m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto Reset SIGNAL (18:00 ET daily)
echo "📅 Creating Crypto Reset SIGNAL job (6:00 PM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-reset-signal \
  --location $REGION \
  --schedule "0 18 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/RESET/signal" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "RESET"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto Reset session SIGNAL snapshots at 6:00 PM ET (open+60m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto Reset OUTCOME (18:55 ET daily - before Asia)
echo "📅 Creating Crypto Reset OUTCOME job (6:55 PM ET daily - before Asia)..."
gcloud scheduler jobs create http easy-collector-crypto-reset-outcome \
  --location $REGION \
  --schedule "55 18 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/RESET/outcome" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "RESET"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto Reset session OUTCOME snapshots at 6:55 PM ET (5 min before Asia open)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

echo ""
echo "=========================================="
echo "Crypto Market Snapshots - ASIA Session (Daily)"
echo "=========================================="
echo ""

# Crypto Asia ORB (19:15 ET daily)
echo "📅 Creating Crypto Asia ORB job (7:15 PM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-asia-orb \
  --location $REGION \
  --schedule "15 19 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/ASIA/orb" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "ASIA"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto Asia session ORB snapshots at 7:15 PM ET (open+15m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto Asia SIGNAL (20:00 ET daily)
echo "📅 Creating Crypto Asia SIGNAL job (8:00 PM ET daily)..."
gcloud scheduler jobs create http easy-collector-crypto-asia-signal \
  --location $REGION \
  --schedule "0 20 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/ASIA/signal" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "ASIA"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto Asia session SIGNAL snapshots at 8:00 PM ET (open+60m)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

# Crypto Asia OUTCOME (02:55 ET daily - before London next day)
echo "📅 Creating Crypto Asia OUTCOME job (2:55 AM ET daily - before London next day)..."
gcloud scheduler jobs create http easy-collector-crypto-asia-outcome \
  --location $REGION \
  --schedule "55 2 * * *" \
  --time-zone "America/New_York" \
  --uri "$COLLECTOR_URL/collect/crypto/ASIA/outcome" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body '{"session": "ASIA"}' \
  --oidc-service-account-email $SERVICE_ACCOUNT \
  --description "Collect Crypto Asia session OUTCOME snapshots at 2:55 AM ET (5 min before London open)" \
  --project=$PROJECT_ID \
  2>&1 | grep -v "already exists" || echo "  ✅ Job already exists (skipped)"

echo ""
echo "✅ All Cloud Scheduler jobs created!"
echo ""
echo "📋 Summary of scheduled jobs:"
gcloud scheduler jobs list --location $REGION --filter="name:easy-collector*" --format="table(name,schedule,timeZone,state)" --project=$PROJECT_ID
echo ""
echo "💡 Management commands:"
echo "   Pause:  gcloud scheduler jobs pause easy-collector-us-orb --location $REGION"
echo "   Resume: gcloud scheduler jobs resume easy-collector-us-orb --location $REGION"
echo "   Run:    gcloud scheduler jobs run easy-collector-us-orb --location $REGION"
echo "   Logs:   gcloud run services logs read easy-collector --region $REGION"
echo ""
echo "📊 Total jobs created: 15"
echo "   - US: 3 jobs (ORB, SIGNAL, OUTCOME)"
echo "   - Crypto: 12 jobs (4 sessions × 3 types)"
