#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-east4}"
SERVICE="travel-optimizer"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}"

echo "Building frontend..."
cd frontend && npm install && npm run build && cd ..

echo "Submitting build to Cloud Build..."
gcloud builds submit --tag "${IMAGE}"

echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --min-instances 1 \
  --memory 1Gi \
  --set-env-vars "ENV=prod" \
  --set-secrets "OPENAI_API_KEY=openai-key:latest,GOOGLE_MAPS_API_KEY=gmaps-key:latest,GOOGLE_PLACES_API_KEY=places-key:latest"

echo "Done. Service URL:"
gcloud run services describe "${SERVICE}" --region "${REGION}" --format "value(status.url)"
