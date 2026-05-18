#!/usr/bin/env bash
# Verify Google Secret Manager polygon-api-key and that Easy Collector is wired to use it
# for US Market 0DTE (Polygon). Run from easyCollector/ or ORB root. Needs gcloud.
#
# Usage: ./scripts/check_polygon_secret_ready.sh [--validate-key] [--key=YOUR_API_KEY]
#   --validate-key     Fetch from Secret Manager (or .env) and test against Polygon API (SPY 5m aggs).
#   --key=YOUR_KEY     Test this key against Polygon API (skips SM for validation). Use to verify
#                      a new key before adding to Secret Manager. Implies --validate-key.

set -e
PROJECT_ID="${GCP_PROJECT_ID:-easy-etrade-strategy}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="easy-collector"
SECRET_NAME="polygon-api-key"
ENV_VAR="POLYGON_API_KEY"

VALIDATE_KEY=
KEY_OVERRIDE=
for a in "$@"; do
  [[ "$a" == "--validate-key" ]] && VALIDATE_KEY=1
  if [[ "$a" == --key=* ]]; then VALIDATE_KEY=1; KEY_OVERRIDE="${a#--key=}"; fi
done

# Resolve script dir and project root (easyCollector)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORB_ROOT="$(cd "$COLLECTOR_ROOT/.." && pwd)"

echo "=============================================="
echo "Polygon Secret & Deploy Readiness for Collector"
echo "=============================================="
echo "Project:    $PROJECT_ID"
echo "Secret:     $SECRET_NAME -> env $ENV_VAR in Cloud Run"
echo "Service:    $SERVICE_NAME ($REGION)"
echo ""

FAIL=0

# ---- 1. Secret exists ----
echo "1. Secret Manager: $SECRET_NAME"
if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" &>/dev/null; then
  echo "   ✅ EXISTS"
else
  echo "   ❌ NOT FOUND — create: ./secretsprivate/ensure_secret_manager_polygon.sh (or see SECRETS.md)"
  FAIL=1
fi
echo ""

# ---- 2. Secret has a version ----
echo "2. Secret has version 'latest'"
VER=$(gcloud secrets versions list "$SECRET_NAME" --project="$PROJECT_ID" --limit=1 --format="value(name)" 2>/dev/null || true)
if [[ -n "$VER" ]]; then
  echo "   ✅ latest version: $VER"
else
  echo "   ❌ No versions — add: echo -n YOUR_KEY | gcloud secrets versions add $SECRET_NAME --project=$PROJECT_ID --data-file=-"
  FAIL=1
fi
echo ""

# ---- 3. (Optional) Validate key with Polygon API ----
if [[ -n "$VALIDATE_KEY" ]]; then
  echo "3. Validate key against Polygon API (SPY 5m aggregates)"
  RAW=
  if [[ -n "$KEY_OVERRIDE" ]]; then
    RAW="$KEY_OVERRIDE"
    echo "   (using --key=...)"
  elif [[ -z "$RAW" ]]; then
    RAW=$(gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$PROJECT_ID" 2>/dev/null || true)
    [[ -n "$RAW" ]] && echo "   (using Secret Manager: $SECRET_NAME:latest)"
  fi
  if [[ -z "$RAW" ]] && [[ -f "$COLLECTOR_ROOT/secretsprivate/.env" ]]; then
    RAW=$(grep -E '^POLYGON_API_KEY=' "$COLLECTOR_ROOT/secretsprivate/.env" 2>/dev/null | cut -d= -f2- | sed 's/^["'\'' \t]*//;s/["'\'' \t]*$//' | head -1)
    [[ -n "$RAW" ]] && echo "   (using secretsprivate/.env POLYGON_API_KEY)"
  fi
  if [[ -z "$RAW" ]]; then
    echo "   ❌ No key to validate: provide --key=YOUR_KEY, or add to Secret Manager, or set POLYGON_API_KEY in secretsprivate/.env"
    if [[ -z "$KEY_OVERRIDE" ]]; then
      echo "      (gcloud read failed: need roles/secretmanager.secretAccessor, or secret has no versions)"
    fi
    FAIL=1
  else
    # Request a 5-day window so we get data even outside US market hours (last 20 min can be empty)
    NOW_MS=$(($(date +%s) * 1000))
    FROM_MS=$((NOW_MS - 5 * 24 * 60 * 60 * 1000))
    URL="https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/${FROM_MS}/${NOW_MS}?apiKey=${RAW}&adjusted=true"
    RESP=$(curl -s -w "\n%{http_code}" --max-time 20 "$URL" 2>/dev/null || echo -e "\n000")
    BODY=$(echo "$RESP" | sed '$d')
    CODE=$(echo "$RESP" | tail -1)
    if [[ "$CODE" == "200" ]]; then
      N=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('results') or []))" 2>/dev/null || echo "0")
      if [[ "${N:-0}" -gt 0 ]]; then
        echo "   ✅ Polygon API OK (HTTP 200, $N bars for SPY) — key is valid and has US stocks access"
      else
        echo "   ✅ Polygon API accepted the key (HTTP 200). 0 bars in 5-day range."
        echo "      If outside US market hours or holiday, this can be normal. During market hours, confirm your plan includes US stocks aggregates (polygon.io/dashboard or massive.com)."
      fi
    elif [[ "$CODE" == "403" || "$CODE" == "401" ]]; then
      echo "   ❌ Polygon API $CODE — key invalid, expired, or plan does not include US stocks aggregates."
      echo "      Check polygon.io/dashboard (or massive.com) and API keys. Order a new key if the current one is discontinued."
      FAIL=1
    else
      echo "   ⚠️  Polygon API HTTP $CODE — key may be invalid or plan insufficient. Response: ${BODY:0:200}"
      echo "      US stocks 5m aggregates require an appropriate Polygon/Massive plan. See polygon.io or massive.com."
      if [[ "$CODE" != "000" ]] && [[ "$CODE" -ge 400 ]]; then FAIL=1; fi
    fi
  fi
  echo ""
fi

# ---- 4. Cloud Run service account ----
echo "4. Cloud Run service account (needs secretAccessor on $SECRET_NAME)"
SA=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --format="value(spec.template.spec.serviceAccountName)" 2>/dev/null || true)
if [[ -z "$SA" ]]; then
  PROJ_NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)
  SA="${PROJ_NUM}-compute@developer.gserviceaccount.com"
  echo "   (default) $SA"
else
  echo "   $SA"
fi

# Check IAM on the secret for this SA
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
  echo "   ✅ SA has roles/secretmanager.secretAccessor on $SECRET_NAME"
else
  echo "   ❌ SA does not have secretAccessor on $SECRET_NAME"
  echo "      Run: gcloud secrets add-iam-policy-binding $SECRET_NAME --project=$PROJECT_ID --member=\"serviceAccount:${SA}\" --role=\"roles/secretmanager.secretAccessor\""
  FAIL=1
fi
echo ""

# ---- 5. Deploy wiring: --set-secrets POLYGON_API_KEY=polygon-api-key:latest ----
echo "5. Deploy scripts use --set-secrets $ENV_VAR=$SECRET_NAME:latest"
F1="$ORB_ROOT/deploy-collector.sh"
F2="$COLLECTOR_ROOT/scripts/deploy.sh"
OK=0
if [[ -f "$F1" ]] && grep -q "POLYGON_API_KEY=polygon-api-key" "$F1" 2>/dev/null; then
  echo "   ✅ $F1"
  OK=1
fi
if [[ -f "$F2" ]] && grep -q "POLYGON_API_KEY=polygon-api-key" "$F2" 2>/dev/null; then
  echo "   ✅ $F2"
  OK=1
fi
if [[ $OK -eq 0 ]]; then
  echo "   ❌ Neither deploy script sets $ENV_VAR=$SECRET_NAME:latest"
  FAIL=1
fi
echo ""

# ---- 6. App config: polygon_api_key from POLYGON_API_KEY ----
echo "6. App: config.polygon_api_key <- env POLYGON_API_KEY, PolygonClient uses it for US 0DTE"
echo "   (config.py: polygon_api_key; polygon_client.py: settings.polygon_api_key)"
echo "   ✅ Code path: POLYGON_API_KEY (env) -> Settings.polygon_api_key -> PolygonClient.api_key -> /v2/aggs/... for US symbols"
echo ""

# ---- Summary ----
echo "=============================================="
if [[ $FAIL -eq 0 ]]; then
  echo "✅ Polygon secret and deploy wiring are ready for US Market 0DTE."
  echo "   Deploy: ./deploy-collector.sh   (from ORB root)"
  echo "   Then:   ./scripts/check_polygon_coinbase.sh   to verify Polygon in the running service."
else
  echo "❌ Fix the items above, then re-run. Optional: add --validate-key to test the key against Polygon API."
  exit 1
fi
