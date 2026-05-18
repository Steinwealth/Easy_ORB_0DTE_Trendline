#!/usr/bin/env bash
# Smoke test: US (Polygon) and Crypto (Coinbase) via /debug smoke endpoints.
# Run the Collector first (e.g. uvicorn from backend with POLYGON_API_KEY in secretsprivate/.env).
# Usage: ./scripts/smoke_test.sh [BASE_URL]
#   BASE_URL defaults to http://127.0.0.1:8080

set -e
BASE_URL="${1:-http://127.0.0.1:8080}"

echo "Smoke test: $BASE_URL"
echo ""

# US (Polygon)
echo "1. US provider (Polygon): GET /debug/us/provider_smoke?symbol=SPY&bars=50"
US=$(curl -s "${BASE_URL}/debug/us/provider_smoke?symbol=SPY&bars=50" || echo '{"error":"request failed"}')
if echo "$US" | grep -q '"row_count"'; then
  RC=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('row_count',0))" 2>/dev/null || echo "?")
  PROV=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('provider','?'))" 2>/dev/null || echo "?")
  echo "   ✅ provider=$PROV, row_count=$RC"
else
  echo "   ❌ $US"
fi
echo ""

# Crypto (Coinbase)
echo "2. Crypto (Coinbase): GET /debug/crypto/product_smoke?symbol=BTC-PERP"
CR=$(curl -s "${BASE_URL}/debug/crypto/product_smoke?symbol=BTC-PERP" || echo '{"error":"request failed"}')
if echo "$CR" | grep -q '"candle_row_count"'; then
  RC=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('candle_row_count',0))" 2>/dev/null || echo "?")
  PID=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('resolved_product_id','?'))" 2>/dev/null || echo "?")
  echo "   ✅ resolved_product_id=$PID, candle_row_count=$RC"
else
  echo "   ❌ $CR"
fi
echo ""

echo "Done. For full validation: python scripts/validate_snapshot_collection.py --tier1-only"
