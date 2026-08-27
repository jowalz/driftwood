#!/usr/bin/env bash
# Reproducible deployment for receiver (Cloud Run service), agent (Cloud Run
# job), and the Workflow that bridges Pub/Sub to the job (Eventarc can only
# push directly to a Cloud Run *service* -- a Job has no HTTP endpoint to
# push to, so Eventarc -> Workflows -> Cloud Run Admin API jobs.run is the
# standard way to trigger a job from an event without adding a second
# service).
set -euo pipefail

# Pick up local overrides from .env if present, without requiring the
# caller to export every variable by hand first.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set}"
: "${GCP_REGION:=europe-west3}"
: "${PUBSUB_TOPIC:=driftwood-events}"
: "${GITHUB_REPO:?GITHUB_REPO must be set (owner/repo)}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN must be set}"
: "${GITHUB_WEBHOOK_SECRET:?GITHUB_WEBHOOK_SECRET must be set}"

gcloud config set project "$GCP_PROJECT_ID"

# --- APIs ---
gcloud services enable \
  run.googleapis.com \
  pubsub.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  eventarc.googleapis.com \
  workflows.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  --quiet

# --- Firestore ---
gcloud firestore databases create --location="$GCP_REGION" --type=firestore-native --quiet || true

# --- Pub/Sub topic ---
gcloud pubsub topics create "$PUBSUB_TOPIC" --quiet || true

# --- Secrets (GitHub token, webhook secret, optional Slack) go into Secret
#     Manager instead of as a plaintext env var on the service/job. ---
put_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "$name" --quiet >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --quiet
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --quiet
  fi
}

put_secret driftwood-github-token "$GITHUB_TOKEN"
put_secret driftwood-github-webhook-secret "$GITHUB_WEBHOOK_SECRET"
if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
  put_secret driftwood-slack-webhook-url "$SLACK_WEBHOOK_URL"
fi

RUNTIME_SA="$(gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

for secret in driftwood-github-token driftwood-github-webhook-secret driftwood-slack-webhook-url; do
  gcloud secrets describe "$secret" --quiet >/dev/null 2>&1 || continue
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet >/dev/null
done

# Firestore (state.py) and Pub/Sub publish (receiver) for the runtime service
# account; run.developer so the workflow can call jobs.run on its behalf.
for role in roles/datastore.user roles/pubsub.publisher roles/run.developer; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
done

# --- Receiver: Cloud Run service ---
gcloud builds submit --tag "gcr.io/${GCP_PROJECT_ID}/driftwood-receiver" -f receiver/Dockerfile .

gcloud run deploy driftwood-receiver \
  --image "gcr.io/${GCP_PROJECT_ID}/driftwood-receiver" \
  --region "$GCP_REGION" \
  --platform managed \
  --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID},PUBSUB_TOPIC=${PUBSUB_TOPIC}" \
  --set-secrets "GITHUB_WEBHOOK_SECRET=driftwood-github-webhook-secret:latest" \
  --allow-unauthenticated

# --- Agent: Cloud Run job ---
gcloud builds submit --tag "gcr.io/${GCP_PROJECT_ID}/driftwood-agent" -f agent/Dockerfile .

AGENT_SECRETS="GITHUB_TOKEN=driftwood-github-token:latest"
if gcloud secrets describe driftwood-slack-webhook-url --quiet >/dev/null 2>&1; then
  AGENT_SECRETS="${AGENT_SECRETS},SLACK_WEBHOOK_URL=driftwood-slack-webhook-url:latest"
fi

gcloud run jobs deploy driftwood-agent \
  --image "gcr.io/${GCP_PROJECT_ID}/driftwood-agent" \
  --region "$GCP_REGION" \
  --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID},GITHUB_REPO=${GITHUB_REPO},GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${GCP_PROJECT_ID},GOOGLE_CLOUD_LOCATION=${GCP_REGION}" \
  --set-secrets "$AGENT_SECRETS"

# --- Workflow: bridges Pub/Sub events to a driftwood-agent job execution
#     (Eventarc has no direct Cloud Run job destination). ---
gcloud workflows deploy driftwood-agent-trigger \
  --source=deploy/agent-trigger-workflow.yaml \
  --location="$GCP_REGION" \
  --service-account="$RUNTIME_SA" \
  --set-env-vars="GCP_REGION=${GCP_REGION}" \
  --quiet

# --- Eventarc trigger: Pub/Sub -> workflow (not the job directly) ---
gcloud eventarc triggers create driftwood-agent-trigger \
  --location "$GCP_REGION" \
  --destination-workflow driftwood-agent-trigger \
  --destination-workflow-location "$GCP_REGION" \
  --event-filters "type=google.cloud.pubsub.topic.v1.messagePublished" \
  --transport-topic "$PUBSUB_TOPIC" \
  --service-account "$RUNTIME_SA" \
  --quiet || true

echo "Deployment complete."
