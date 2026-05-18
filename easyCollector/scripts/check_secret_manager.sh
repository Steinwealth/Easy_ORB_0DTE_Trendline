#!/usr/bin/env bash
# Check Google Secret Manager for polygon-api-key and other Easy Collector secrets.
# Run in your terminal (needs gcloud auth). Project: easy-etrade-strategy.

set -e
PROJECT="${GCP_PROJECT_ID:-easy-etrade-strategy}"

echo "Secret Manager (project=$PROJECT)"
echo "================================"

echo ""
echo "All secrets:"
gcloud secrets list --project="$PROJECT" --format="table(name,createTime)" || { echo "  Failed. Run in your terminal: gcloud auth login; gcloud config set project $PROJECT"; exit 1; }

echo ""
echo "polygon-api-key:"
if gcloud secrets describe polygon-api-key --project="$PROJECT" &>/dev/null; then
  echo "  EXISTS"
  VER=$(gcloud secrets versions list polygon-api-key --project="$PROJECT" --limit=1 --format="value(name)" 2>/dev/null || true)
  if [[ -n "$VER" ]]; then
    echo "  latest version: $VER"
  fi
else
  echo "  NOT FOUND — create with: ./secretsprivate/ensure_secret_manager_polygon.sh"
fi

echo ""
echo "Optional (for reference): alpaca-key, alpaca-secret, etrade-prod-consumer-key, etrade-prod-consumer-secret"
