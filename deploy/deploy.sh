#!/usr/bin/env bash
# Reproduzierbares Deployment für receiver (Cloud Run Service) und agent (Cloud Run Job).
set -euo pipefail

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID muss gesetzt sein}"
: "${GCP_REGION:=europe-west3}"
: "${PUBSUB_TOPIC:=driftwood-events}"

gcloud config set project "$GCP_PROJECT_ID"

# --- Pub/Sub Topic ---
gcloud pubsub topics create "$PUBSUB_TOPIC" --quiet || true

# --- Receiver: Cloud Run Service ---
gcloud builds submit --tag "gcr.io/${GCP_PROJECT_ID}/driftwood-receiver" -f receiver/Dockerfile .

gcloud run deploy driftwood-receiver \
  --image "gcr.io/${GCP_PROJECT_ID}/driftwood-receiver" \
  --region "$GCP_REGION" \
  --platform managed \
  --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID},PUBSUB_TOPIC=${PUBSUB_TOPIC}" \
  --allow-unauthenticated

# --- Agent: Cloud Run Job ---
gcloud builds submit --tag "gcr.io/${GCP_PROJECT_ID}/driftwood-agent" -f agent/Dockerfile .

gcloud run jobs deploy driftwood-agent \
  --image "gcr.io/${GCP_PROJECT_ID}/driftwood-agent" \
  --region "$GCP_REGION" \
  --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID}"

# --- Pub/Sub Subscription, die den Job triggert ---
gcloud eventarc triggers create driftwood-agent-trigger \
  --location "$GCP_REGION" \
  --destination-run-job driftwood-agent \
  --destination-run-region "$GCP_REGION" \
  --event-filters "type=google.cloud.pubsub.topic.v1.messagePublished" \
  --transport-topic "$PUBSUB_TOPIC" \
  --quiet || true

echo "Deployment abgeschlossen."
