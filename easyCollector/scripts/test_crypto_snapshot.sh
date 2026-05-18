#!/usr/bin/env bash
# Run a single crypto ORB collection to confirm snapshots succeed (Coinbase data → snapshot build → save).
# Usage: ./scripts/test_crypto_snapshot.sh [BASE_URL]
#   BASE_URL: e.g. https://easy-collector-XXXX.run.app or http://127.0.0.1:8080
#   If omitted, uses gcloud to get the easy-collector Cloud Run URL.
#
# Success: summary.successful >= 1 and summary.failed == 0 for the 4 crypto symbols.
# If product_smoke works but collection fails, check logs for VolumeVWAPData/volume_delta or other validation errors.

set -e

BASE_URL="${1:-}"
if [[ -z "$BASE_URL" ]]; then
  PROJECT_ID="${GCP_PROJECT_ID:-easy-etrade-strategy}"
  REGION="${GCP_REGION:-us-central1}"
  if command -v gcloud &>/dev/null; then
    BASE_URL=$(gcloud run services describe easy-collector --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null || true)
  fi
  if [[ -z "$BASE_URL" ]]; then
    echo "Pass BASE_URL: ./scripts/test_crypto_snapshot.sh https://YOUR-SERVICE.run.app"
    exit 1
  fi
  echo "Using: $BASE_URL"
fi

echo ""
echo "=== 1. Coinbase product_smoke (data source) ==="
CR=$(curl -s --max-time 60 "${BASE_URL}/debug/crypto/product_smoke?symbol=BTC-PERP" 2>/dev/null || echo '{"error":"request_failed"}')
if echo "$CR" | grep -q '"candle_row_count"'; then
  RC=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('candle_row_count',0))" 2>/dev/null || echo "0")
  if [[ "${RC:-0}" -gt 0 ]]; then
    echo "   ✅ Coinbase: candle_row_count=$RC (data OK)"
  else
    echo "   ❌ Coinbase: no candles (product_smoke); fix data before testing snapshots."
    exit 1
  fi
else
  echo "   ❌ Coinbase: $CR"
  exit 1
fi

echo ""
echo "=== 2. POST /collect/crypto/US/orb (snapshot collection) ==="
R=$(curl -s --max-time 120 -X POST "${BASE_URL}/collect/crypto/US/orb" \
  -H "Content-Type: application/json" \
  -d '{}' 2>/dev/null || echo '{"success":false,"detail":"request_failed"}')

if echo "$R" | grep -q '"summary"'; then
  OK=$(echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); s=d.get('summary',{}); print(s.get('successful',0))" 2>/dev/null || echo "0")
  FAIL=$(echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); s=d.get('summary',{}); print(s.get('failed',0))" 2>/dev/null || echo "0")
  ERR=$(echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); s=d.get('summary',{}); e=s.get('errors',[]); print(e[0] if e else '')" 2>/dev/null || echo "")

  if [[ "${OK:-0}" -ge 1 && "${FAIL:-0}" -eq 0 ]]; then
    echo "   ✅ Crypto snapshots: successful=$OK, failed=$FAIL"
    echo "   → Snapshot build and save are working."
  else
    echo "   ❌ Crypto snapshots: successful=$OK, failed=$FAIL"
    if [[ -n "$ERR" ]]; then echo "      first error: $ERR"; fi
    echo "   → If product_smoke is OK, check volume_delta/VolumeVWAPData and Cloud Run logs."
    exit 1
  fi
else
  echo "   ❌ Collection request failed or non-JSON: $R"
  exit 1
fi

echo ""
echo "=== Done ==="
