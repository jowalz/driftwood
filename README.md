# Driftwood

Driftwood watches for repository changes and keeps documentation and code in sync: a webhook receiver on Cloud Run takes in GitHub events and publishes them to Pub/Sub; a Cloud Run job with an ADK agent analyzes diffs, checks them against the docs, and opens PRs or issues automatically when needed.

## Architecture

```
GitHub webhook → receiver (Cloud Run service) → Pub/Sub → Workflow → agent (Cloud Run job)
                                                                         ├── analysis.py  (diff → symbols → doc sections)
                                                                         ├── state.py     (Firestore: fingerprints/idempotency)
                                                                         └── actions.py   (GitHub: PR/issue/escalation)
```

Eventarc can only push directly to a Cloud Run *service* (an HTTP
endpoint) — a Cloud Run *job* has none, so the small Workflow in
`deploy/agent-trigger-workflow.yaml` is what actually starts a
`driftwood-agent` job execution in response to a Pub/Sub message. It's
orchestration glue, not a third compute component — the stack is still
one service (receiver) and one job (agent).

## Structure

- `receiver/` – Cloud Run service: receives GitHub webhooks, validates them, and publishes them to Pub/Sub.
- `agent/` – Cloud Run job: ADK agent that analyzes diffs and triggers actions on GitHub.
- `deploy/` – deployment scripts (gcloud) and the Pub/Sub-to-job Workflow definition.
- `docs/` – architecture and other documentation.

## Setup

1. Copy `.env.example` to `.env` and fill in the values.
2. `pip install -r requirements.txt`
3. Deploy via `deploy/deploy.sh` — it sources `.env` itself if present,
   enables the required GCP APIs, and is safe to run more than once.
4. Set up a GitHub webhook on the target repo pointing at the receiver
   URL (`/webhook`) — subscribe to **only the `push` event**, not "Send
   me everything". The receiver itself doesn't filter by event type (see
   `docs/CONCEPT.md`, "Why the receiver does almost nothing"); that
   filtering deliberately belongs in the webhook configuration, not in
   the code.

## Testing

### Prerequisites

- A GCP project with billing enabled.
- `gcloud` CLI installed and authenticated: `gcloud auth login` and
  `gcloud auth application-default login` (the agent talks to Vertex AI
  using application default credentials).
- A target GitHub repo to protect (e.g. `driftwood-testbed`) and a
  GitHub personal access token with `repo` scope, for `GITHUB_TOKEN`.
- Any string for `GITHUB_WEBHOOK_SECRET` — you choose it, GitHub's
  webhook config and `.env` both need the same value.
- Optional: a Slack incoming webhook URL for `SLACK_WEBHOOK_URL`, to see
  the ESCALATE route in action.

### Offline checks (no deployment needed)

```bash
# diff -> symbols -> referencing doc sections, against any local repo
python -m agent.analysis --repo /path/to/repo --diff my-change.diff

# calibration check for the FIX/ASK/ESCALATE classifier (needs Vertex AI
# credentials, but no Cloud Run deployment)
python -m agent.routing_examples
```

### Deploying

```bash
cp .env.example .env   # fill in the values described above
pip install -r requirements.txt
bash deploy/deploy.sh
```

Note the receiver URL printed by `gcloud run deploy` — you'll need it
below. Then add the GitHub webhook as described in Setup step 4.

### Simulating a webhook without pushing

The receiver only checks the HMAC signature and the payload shape, so a
synthetic push event tests the whole pipeline (receiver → Pub/Sub →
Workflow → agent job) without touching a real GitHub repo:

```bash
RECEIVER_URL="https://driftwood-receiver-xxxxx.a.run.app"  # from deploy output
SECRET="the same value as GITHUB_WEBHOOK_SECRET in .env"
PAYLOAD='{"ref":"refs/heads/main","after":"deadbeef"}'
SIGNATURE="sha256=$(printf '%s' "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')"

curl -X POST "$RECEIVER_URL/webhook" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-GitHub-Delivery: $(uuidgen 2>/dev/null || echo test-delivery-1)" \
  -H "X-Hub-Signature-256: $SIGNATURE" \
  -d "$PAYLOAD"
```

A `{"status":"accepted"}` response means the receiver verified the
signature and published to Pub/Sub. For the agent to actually find a
drift and act on it, `PAYLOAD` needs to resemble a real GitHub push
payload closely enough for your downstream code to extract a diff from
it — for a full end-to-end run, a real push to the testbed repo is more
reliable than hand-crafting the payload.

### Checking the result (Definition of done)

```bash
# 1. a push triggering the agent, visible in Cloud Run logs
gcloud run jobs executions list --job=driftwood-agent --region="$GCP_REGION"
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="driftwood-agent"' \
  --limit=50 --format="value(textPayload)"

# 3. the resulting PR/issue: check the target repo's Pull requests / Issues
# tab on GitHub directly.
```

Item 2 from the Definition of done — two different commits producing two
different routes — needs two pushes to the target repo that each trigger
a different classification (e.g. one mechanical default change for FIX,
one ambiguous signature change for ASK); run each through the same
checks above and compare the reasoning in the logs and the resulting PR
vs. issue.

## Conventions

- All code comments, docstrings, and git history are in English,
  regardless of the language used in conversation while building this.
