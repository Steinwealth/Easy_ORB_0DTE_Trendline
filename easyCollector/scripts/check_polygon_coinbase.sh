#!/usr/bin/env bash
# Verify Polygon (US) and Coinbase (Crypto) are working for snapshots.
# Usage: ./scripts/check_polygon_coinbase.sh [BASE_URL]
#   BASE_URL: e.g. https://easy-collector-XXXX.run.app or http://127.0.0.1:8080
#   If omitted, uses gcloud to get the easy-collector Cloud Run URL.

set -e

BASE_URL="${1:-}"
if [[ -z "$BASE_URL" ]]; then
  PROJECT_ID="${GCP_PROJECT_ID:-easy-etrade-strategy}"
  REGION="${GCP_REGION:-us-central1}"
  if command -v gcloud &>/dev/null; then
    BASE_URL=$(gcloud run services describe easy-collector --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null || true)
  fi
  if [[ -z "$BASE_URL" ]]; then
    echo "Pass BASE_URL: ./scripts/check_polygon_coinbase.sh https://YOUR-SERVICE.run.app"
    exit 1
  fi
  echo "Using: $BASE_URL"
fi

echo ""
echo "=== Polygon (US) and Coinbase (Crypto) Check ==="
echo ""

# --- 1. Polygon (US) ---
echo "1. Polygon (US snapshots): GET /debug/us/provider_smoke?symbol=SPY&bars=50"
US=$(curl -s --max-time 60 "${BASE_URL}/debug/us/provider_smoke?symbol=SPY&bars=50" 2>/dev/null || echo '{"error":"request_failed"}')

if echo "$US" | grep -q '"provider"'; then
  PROV=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('provider') or '')" 2>/dev/null || echo "")
  RC=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('row_count',0))" 2>/dev/null || echo "0")
  ERR=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','') or d.get('message',''))" 2>/dev/null || echo "")

  if [[ -n "$PROV" && "$PROV" != "None" && "${RC:-0}" -ge 10 ]]; then
    echo "   ✅ Polygon (US): OK — provider=$PROV, row_count=$RC"
    if [[ "$PROV" == "polygon" ]]; then
      echo "      → Polygon key is working for US market snapshots."
    else
      echo "      → US data via $PROV (Polygon not selected; set POLYGON_API_KEY for Polygon)."
    fi
  else
    echo "   ❌ Polygon (US): FAIL — provider=$PROV, row_count=$RC"
    if [[ -n "$ERR" && "$ERR" != "None" ]]; then echo "      error: $ERR"; fi
    # When Polygon fails, show /debug/polygon to see if key is set and healthcheck result
    P=$(curl -s --max-time 30 "${BASE_URL}/debug/polygon" 2>/dev/null || echo '{}')
    if echo "$P" | grep -q '"polygon_api_key_set"'; then
      KEY_SET=$(echo "$P" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('polygon_api_key_set',False))" 2>/dev/null || echo "?")
      HSTAT=$(echo "$P" | python3 -c "import sys,json; d=json.load(sys.stdin); h=d.get('polygon_healthcheck',{}); print(h.get('status','?'))" 2>/dev/null || echo "?")
      HMSG=$(echo "$P" | python3 -c "import sys,json; d=json.load(sys.stdin); h=d.get('polygon_healthcheck',{}); print(h.get('message') or h.get('error') or '')" 2>/dev/null || echo "")
      echo "      → POLYGON_API_KEY set: $KEY_SET | polygon healthcheck: $HSTAT${HMSG:+ ($HMSG)}"
    fi
  fi
else
  echo "   ❌ Polygon (US): FAIL — $US"
fi
echo ""

# --- 2. Coinbase (Crypto) ---
echo "2. Coinbase (Crypto snapshots): GET /debug/crypto/product_smoke?symbol=BTC-PERP"
CR=$(curl -s --max-time 60 "${BASE_URL}/debug/crypto/product_smoke?symbol=BTC-PERP" 2>/dev/null || echo '{"error":"request_failed"}')

if echo "$CR" | grep -q '"resolved_product_id"'; then
  PID=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('resolved_product_id') or '')" 2>/dev/null || echo "")
  RC=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('candle_row_count',0))" 2>/dev/null || echo "0")
  ERR=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','') or d.get('reason',''))" 2>/dev/null || echo "")

  if [[ -n "$PID" && "$PID" != "None" && "${RC:-0}" -gt 0 ]]; then
    echo "   ✅ Coinbase (Crypto): OK — product=$PID, candle_row_count=$RC"
    echo "      → Coinbase is working for crypto snapshots."
  else
    echo "   ❌ Coinbase (Crypto): FAIL — product=$PID, candle_row_count=$RC"
    if [[ -n "$ERR" && "$ERR" != "None" ]]; then echo "      error/reason: $ERR"; fi
  fi
else
  echo "   ❌ Coinbase (Crypto): FAIL — $CR"
fi
echo ""

echo "=== Done ==="
