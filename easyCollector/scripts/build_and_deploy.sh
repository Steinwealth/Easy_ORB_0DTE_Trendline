#!/bin/bash
# Build and Deploy Easy Collector: delegates to ORB root deploy-collector.sh.
# Run from easyCollector/ or easyCollector/scripts/. deploy-collector.sh copies
# 0dte_list.csv, builds from easyCollector context, and deploys to Cloud Run
# with POLYGON_API_KEY from Secret Manager.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ORB_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

exec "$ORB_ROOT/deploy-collector.sh" "$@"
