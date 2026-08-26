# Driftwood

Driftwood watches for repository changes and keeps documentation and code in sync: a webhook receiver on Cloud Run takes in GitHub events and publishes them to Pub/Sub; a Cloud Run job with an ADK agent analyzes diffs, checks them against the docs, and opens PRs or issues automatically when needed.

## Architecture

```
GitHub webhook → receiver (Cloud Run service) → Pub/Sub → agent (Cloud Run job)
                                                              ├── analysis.py  (diff → symbols → doc sections)
                                                              ├── state.py     (Firestore: fingerprints/idempotency)
                                                              └── actions.py   (GitHub: PR/issue/escalation)
```

## Structure

- `receiver/` – Cloud Run service: receives GitHub webhooks, validates them, and publishes them to Pub/Sub.
- `agent/` – Cloud Run job: ADK agent that analyzes diffs and triggers actions on GitHub.
- `deploy/` – deployment scripts (gcloud).
- `docs/` – architecture and other documentation.

## Setup

1. Copy `.env.example` to `.env` and fill in the values.
2. `pip install -r requirements.txt`
3. Deploy via `deploy/deploy.sh`.
4. Set up a GitHub webhook on the target repo pointing at the receiver
   URL (`/webhook`) — subscribe to **only the `push` event**, not "Send
   me everything". The receiver itself doesn't filter by event type (see
   `docs/CONCEPT.md`, "Why the receiver does almost nothing"); that
   filtering deliberately belongs in the webhook configuration, not in
   the code.

## Conventions

- All code comments, docstrings, and git history are in English,
  regardless of the language used in conversation while building this.
