"""Cloud Run Service: nimmt GitHub-Webhooks entgegen und veröffentlicht sie auf Pub/Sub."""

import hashlib
import hmac
import json
import os

from fastapi import FastAPI, Header, HTTPException, Request
from google.cloud import pubsub_v1

app = FastAPI()

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
TOPIC = os.environ.get("PUBSUB_TOPIC", "driftwood-events")
WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]

publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC)


def verify_signature(payload: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook")
async def webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    payload = await request.body()
    if not verify_signature(payload, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    message = {
        "event": x_github_event,
        "payload": json.loads(payload),
    }
    publisher.publish(topic_path, json.dumps(message).encode("utf-8"))
    return {"status": "accepted"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
