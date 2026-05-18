#!/usr/bin/env bash
# Health and smoke checks for Easy Collector (local or deployed).
# Usage:
#   ./scripts/check_health.sh                    # use Cloud Run URL from gcloud
#   ./scripts/check_health.sh http://127.0.0.1:8080
#   ./scripts/check_health.sh https://easy-collector-XXXX.run.app
set -e

BASE_URL="${1:-}"
if [[ -z "$BASE_URL" ]]; then
  PROJECT_ID="${GCP_PROJECT_ID:-easy-etrade-strategy}"
  REGION="${GCP_REGION:-us-central1}"
  if ! command -v gcloud &>/dev/null; then
    echo "❌ gcloud not found. Pass BASE_URL: ./scripts/check_health.sh https://YOUR-SERVICE.run.app"
    exit 1
  fi
  BASE_URL=$(gcloud run services describe easy-collector --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null || true)
  if [[ -z "$BASE_URL" ]]; then
    echo "❌ Could not get easy-collector URL. Run: gcloud auth login and pass BASE_URL."
    exit 1
  fi
  echo "📡 Using Cloud Run URL: $BASE_URL"
fi

echo ""
echo "=== Easy Collector Health Check ==="
echo ""

# 1. /health
echo "1. GET /health"
H=$(curl -s "${BASE_URL}/health" || echo '{"status":"request_failed"}')
if echo "$H" | grep -q '"status"'; then
  STAT=$(echo "$H" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
  USP=$(echo "$H" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('us_provider','?'))" 2>/dev/null || echo "?")
  FS=$(echo "$H" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('firestore','?'))" 2>/dev/null || echo "?")
  echo "   status=$STAT, us_provider=$USP, firestore=$FS"
  if [[ "$STAT" != "healthy" && "$STAT" != "degraded" ]]; then
    echo "   ❌ Response: $H"
  fi
else
  echo "   ❌ $H"
fi
echo ""

# 2. /version
echo "2. GET /version"
V=$(curl -s "${BASE_URL}/version" || echo '{}')
echo "   $V"
echo ""

# 3. US provider smoke
echo "3. GET /debug/us/provider_smoke?symbol=SPY&bars=50"
US=$(curl -s "${BASE_URL}/debug/us/provider_smoke?symbol=SPY&bars=50" || echo '{"error":"request failed"}')
if echo "$US" | grep -q '"row_count"'; then
  RC=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('row_count',0))" 2>/dev/null || echo "?")
  PROV=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('provider','?'))" 2>/dev/null || echo "?")
  echo "   ✅ provider=$PROV, row_count=$RC"
else
  echo "   ❌ $US"
fi
echo ""

# 4. Crypto smoke
echo "4. GET /debug/crypto/product_smoke?symbol=BTC-PERP"
CR=$(curl -s "${BASE_URL}/debug/crypto/product_smoke?symbol=BTC-PERP" || echo '{"error":"request failed"}')
if echo "$CR" | grep -q '"candle_row_count"'; then
  RC=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('candle_row_count',0))" 2>/dev/null || echo "?")
  PID=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('resolved_product_id','?'))" 2>/dev/null || echo "?")
  echo "   ✅ resolved_product_id=$PID, candle_row_count=$RC"
else
  echo "   ❌ $CR"
fi
echo ""

echo "=== Done ==="
echo "For full validation: python scripts/validate_snapshot_collection.py --tier1-only"
