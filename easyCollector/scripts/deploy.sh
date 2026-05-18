#!/bin/bash
# Deploy Easy Collector to Cloud Run
# Waits for build to complete, then deploys

set -e

PROJECT_ID="easy-etrade-strategy"
SERVICE_NAME="easy-collector"
REGION="us-central1"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "🚀 Easy Collector Deployment"
echo "============================"
echo "Project: ${PROJECT_ID}"
echo "Service: ${SERVICE_NAME}"
echo "Region: ${REGION}"
echo ""

# Wait for latest build to complete
echo "⏳ Waiting for build to complete..."
BUILD_ID=$(gcloud builds list --project ${PROJECT_ID} --limit 1 --format="value(id)" 2>/dev/null)

if [ -z "$BUILD_ID" ]; then
    echo "❌ No build found. Please run build first."
    exit 1
fi

echo "Build ID: ${BUILD_ID}"

# Wait for build to finish
while true; do
    STATUS=$(gcloud builds describe ${BUILD_ID} --project ${PROJECT_ID} --format="value(status)" 2>/dev/null)
    
    if [ "$STATUS" = "SUCCESS" ]; then
        echo "✅ Build completed successfully!"
        break
    elif [ "$STATUS" = "FAILURE" ] || [ "$STATUS" = "CANCELLED" ]; then
        echo "❌ Build failed with status: ${STATUS}"
        exit 1
    elif [ "$STATUS" = "WORKING" ] || [ "$STATUS" = "QUEUED" ]; then
        echo "⏳ Build status: ${STATUS} - waiting..."
        sleep 10
    else
        echo "⚠️  Unknown build status: ${STATUS}"
        sleep 10
    fi
done

# Deploy to Cloud Run (POLYGON_API_KEY from Secret Manager; see easyCollector/SECRETS.md)
echo ""
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
  --image ${IMAGE} \
  --region ${REGION} \
  --project ${PROJECT_ID} \
  --platform managed \
  --allow-unauthenticated \
  --set-secrets "POLYGON_API_KEY=polygon-api-key:latest"

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Service URL:"
gcloud run services describe ${SERVICE_NAME} \
  --region ${REGION} \
  --project ${PROJECT_ID} \
  --format="value(status.url)"

echo ""
echo "📊 Monitor logs:"
echo "gcloud logging tail \"resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE_NAME}\" --project ${PROJECT_ID}"
