#!/usr/bin/env bash
# Ensure data sources (Polygon for US, Coinbase for Crypto) are working so the next
# snapshots will succeed. Runs Secret Manager + IAM checks, then hits the deployed
# service /health and /debug endpoints.
#
# Usage: ./scripts/ensure_data_sources_ready.sh [--fix-iam] [BASE_URL]
#   --fix-iam   If the Cloud Run SA lacks secretAccessor on polygon-api-key, run
#               gcloud secrets add-iam-policy-binding to add it.
#   BASE_URL    Optional. If omitted, uses gcloud to get easy-collector Cloud Run URL.
#
# Run from anywhere. Examples:
#   From ORB root (parent of easyCollector):  ./easyCollector/scripts/ensure_data_sources_ready.sh
#   From easyCollector:                       ./scripts/ensure_data_sources_ready.sh
# Needs: gcloud, curl, network.

set -e
PROJECT_ID="${GCP_PROJECT_ID:-easy-etrade-strategy}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="easy-collector"
SECRET_NAME="polygon-api-key"

FIX_IAM=
BASE_URL=
for a in "$@"; do
  [[ "$a" == "--fix-iam" ]] && { FIX_IAM=1; continue; }
  [[ -z "$BASE_URL" && "$a" != --* ]] && BASE_URL="$a"
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORB_ROOT="$(cd "$COLLECTOR_ROOT/.." && pwd)"

echo "=============================================="
echo "Ensure Data Sources Ready (Polygon + Coinbase)"
echo "=============================================="
echo "Project:  $PROJECT_ID"
echo "Service:  $SERVICE_NAME ($REGION)"
echo ""

# ---- 1. Polygon secret exists ----
echo "1. Secret Manager: $SECRET_NAME"
if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" &>/dev/null; then
  echo "   ✅ Exists"
else
  echo "   ❌ NOT FOUND"
  echo "      Create: ./secretsprivate/ensure_secret_manager_polygon.sh  (needs secretsprivate/.env with POLYGON_API_KEY)"
  echo "      Or:     echo -n YOUR_KEY | gcloud secrets create $SECRET_NAME --project=$PROJECT_ID --data-file=-"
  echo ""
  exit 1
fi

# ---- 2. Secret has a version ----
VER=$(gcloud secrets versions list "$SECRET_NAME" --project="$PROJECT_ID" --limit=1 --format="value(name)" 2>/dev/null || true)
if [[ -n "$VER" ]]; then
  echo "   ✅ Has version: $VER"
else
  echo "   ❌ No versions (secret is empty)"
  echo "      Add key: ./secretsprivate/ensure_secret_manager_polygon.sh  (reads POLYGON_API_KEY from secretsprivate/.env)"
  echo "      Or:     echo -n YOUR_KEY | gcloud secrets versions add $SECRET_NAME --project=$PROJECT_ID --data-file=-"
  echo ""
  exit 1
fi
echo ""

# ---- 3. Cloud Run SA has secretAccessor ----
echo "2. IAM: Cloud Run SA can read $SECRET_NAME"
SA=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --format="value(spec.template.spec.serviceAccountName)" 2>/dev/null || true)
if [[ -z "$SA" ]]; then
  PROJ_NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)
  SA="${PROJ_NUM}-compute@developer.gserviceaccount.com"
  echo "   (using default) $SA"
else
  echo "   $SA"
fi

HAS_ACCESS=
if gcloud secrets get-iam-policy "$SECRET_NAME" --project="$PROJECT_ID" --format="json" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
sa = \"$SA\"
for b in d.get('bindings') or []:
  if b.get('role') == 'roles/secretmanager.secretAccessor':
    for m in b.get('members') or []:
      if sa in m:
        sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
  HAS_ACCESS=1
fi

if [[ -n "$HAS_ACCESS" ]]; then
  echo "   ✅ SA has roles/secretmanager.secretAccessor"
else
  echo "   ❌ SA does NOT have secretAccessor"
  if [[ -n "$FIX_IAM" ]]; then
    echo "   Running: gcloud secrets add-iam-policy-binding $SECRET_NAME ..."
    gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
      --project="$PROJECT_ID" \
      --member="serviceAccount:${SA}" \
      --role="roles/secretmanager.secretAccessor" \
      --quiet
    echo "   ✅ IAM binding added. Redeploy so Cloud Run picks up the secret: ./deploy-collector.sh (from ORB root)"
  else
    echo "      Run: gcloud secrets add-iam-policy-binding $SECRET_NAME --project=$PROJECT_ID --member=\"serviceAccount:${SA}\" --role=\"roles/secretmanager.secretAccessor\""
    echo "      Or:  $0 --fix-iam [BASE_URL]"
  fi
  echo ""
  [[ -z "$FIX_IAM" ]] && exit 1
fi
echo ""

# ---- 4. Get Cloud Run URL ----
if [[ -z "$BASE_URL" ]]; then
  BASE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null || true)
fi
if [[ -z "$BASE_URL" ]]; then
  echo "3. Live service checks: SKIP (no BASE_URL)"
  echo "   Pass URL: $0 [--fix-iam] https://easy-collector-XXXX.run.app"
  echo ""
  echo "=============================================="
  echo "Next: 1) Fix any IAM (--fix-iam or gcloud above)"
  echo "      2) Redeploy if secret or IAM changed: cd to parent of easyCollector && ./deploy-collector.sh"
  echo "      3) Run this script with BASE_URL to verify /health and /debug endpoints"
  exit 0
fi

echo "3. Live service: $BASE_URL"
echo ""

# ---- 5. /health ----
echo "   GET /health (max 45s, cold start may be slow)"
HEALTH=$(curl -s --max-time 45 "$BASE_URL/health" 2>/dev/null || echo '{"status":"request_failed"}')
HSTAT=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
US_PROV=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('us_provider','?'))" 2>/dev/null || echo "?")
CBSTAT=$(echo "$HEALTH" | python3 -c "import sys,json; c=json.load(sys.stdin).get('coinbase',{}); print(c.get('status','?'))" 2>/dev/null || echo "?")
if [[ -z "$HSTAT" ]]; then HSTAT="?"; fi
if [[ "$HSTAT" == "healthy" ]]; then
  echo "      ✅ status=$HSTAT  us_provider=$US_PROV  coinbase=$CBSTAT"
elif [[ "$HSTAT" == "request_failed" ]]; then
  echo "      ❌ status=$HSTAT  (curl time-out or connection error; try: curl -s -m 60 \"$BASE_URL/health\")"
else
  echo "      ⚠️  status=$HSTAT  us_provider=$US_PROV  coinbase=$CBSTAT"
fi
echo ""

# ---- 6. /debug/polygon ----
echo "   GET /debug/polygon"
PG=$(curl -s --max-time 15 "$BASE_URL/debug/polygon" 2>/dev/null || echo '{}')
KEY_SET=$(echo "$PG" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('polygon_api_key_set',False))" 2>/dev/null || echo "?")
PGSTAT=$(echo "$PG" | python3 -c "import sys,json; h=json.load(sys.stdin).get('polygon_healthcheck',{}); print(h.get('status','?'))" 2>/dev/null || echo "?")
PGMSG=$(echo "$PG" | python3 -c "import sys,json; h=json.load(sys.stdin).get('polygon_healthcheck',{}); print(h.get('message') or h.get('error') or '')" 2>/dev/null || echo "")
if [[ "$KEY_SET" == "True" || "$KEY_SET" == "true" ]]; then
  if [[ "$PGSTAT" == "healthy" ]]; then
    echo "      ✅ polygon_api_key_set=True  polygon_healthcheck=$PGSTAT"
  else
    echo "      ⚠️  polygon_api_key_set=True  polygon_healthcheck=$PGSTAT  ${PGMSG:+($PGMSG)}"
  fi
else
  echo "      ❌ polygon_api_key_set=$KEY_SET  (Cloud Run cannot read the secret: check IAM and redeploy)"
fi
echo ""

# ---- 7. /debug/us/provider_smoke ----
echo "   GET /debug/us/provider_smoke?symbol=SPY&bars=50"
US=$(curl -s --max-time 60 "$BASE_URL/debug/us/provider_smoke?symbol=SPY&bars=50" 2>/dev/null || echo '{"error":"request_failed"}')
PROV=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('provider') or '')" 2>/dev/null || echo "")
RC=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('row_count',0))" 2>/dev/null || echo "0")
USERR=$(echo "$US" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','') or d.get('message',''))" 2>/dev/null || echo "")
if [[ -n "$PROV" && "$PROV" != "None" && "${RC:-0}" -ge 10 ]]; then
  echo "      ✅ US (Polygon): provider=$PROV  row_count=$RC"
else
  echo "      ❌ US: provider=$PROV  row_count=$RC  ${USERR:+error: $USERR}"
fi
echo ""

# ---- 8. /debug/crypto/product_smoke ----
echo "   GET /debug/crypto/product_smoke?symbol=BTC-PERP"
CR=$(curl -s --max-time 60 "$BASE_URL/debug/crypto/product_smoke?symbol=BTC-PERP" 2>/dev/null || echo '{"error":"request_failed"}')
PID=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('resolved_product_id') or '')" 2>/dev/null || echo "")
CRC=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('candle_row_count',0))" 2>/dev/null || echo "0")
CRERR=$(echo "$CR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','') or d.get('reason',''))" 2>/dev/null || echo "")
if [[ -n "$PID" && "$PID" != "None" && "${CRC:-0}" -gt 0 ]]; then
  echo "      ✅ Crypto (Coinbase): product=$PID  candle_row_count=$CRC"
else
  echo "      ❌ Crypto: product=$PID  candle_row_count=$CRC  ${CRERR:+error/reason: $CRERR}"
fi
echo ""

# ---- Summary ----
echo "=============================================="
echo "Summary"
echo "=============================================="
if [[ "$KEY_SET" == "True" || "$KEY_SET" == "true" ]] && [[ -n "$PROV" && "$PROV" != "None" && "${RC:-0}" -ge 10 ]] && [[ -n "$PID" && "${CRC:-0}" -gt 0 ]]; then
  echo "✅ Polygon (US) and Coinbase (Crypto) are working. Next snapshots should succeed."
  echo "   If they still fail, check: 0dte_list.csv present in the image, scheduler jobs firing, and run logs in Firestore."
else
  echo "⚠️  One or both data sources need attention:"
  if [[ "$KEY_SET" != "True" && "$KEY_SET" != "true" ]]; then
    echo "   • Polygon: POLYGON_API_KEY not set in the running service. Ensure IAM (step 2) and redeploy: ./deploy-collector.sh (from ORB root)"
  fi
  if [[ -z "$PROV" || "$PROV" == "None" || "${RC:-0}" -lt 10 ]]; then
    echo "   • US: Prefetch will fail until Polygon returns data. Fix key/IAM, redeploy, then re-run this script."
  fi
  if [[ -z "$PID" || "${CRC:-0}" -le 0 ]]; then
    echo "   • Crypto: Coinbase product_smoke returned no candles. Check network from Cloud Run to api.exchange.coinbase.com; see Data.md §6."
  fi
  echo ""
  echo "   After fixing: redeploy from ORB root: ./deploy-collector.sh"
  echo "   Then re-run: ./scripts/ensure_data_sources_ready.sh $BASE_URL"
fi
echo ""
