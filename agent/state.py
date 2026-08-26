"""Firestore: drift fingerprints and idempotency."""

import hashlib
from datetime import datetime, timezone

from google.cloud import firestore

DELIVERY_COLLECTION = "driftwood-deliveries"
FINDING_COLLECTION = "driftwood-findings"


def _client() -> firestore.Client:
    return firestore.Client()


def content_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --- Webhook delivery dedup: guards against duplicate Pub/Sub delivery ---


def already_delivered(delivery_id: str) -> bool:
    return _client().collection(DELIVERY_COLLECTION).document(delivery_id).get().exists


def mark_delivered(delivery_id: str) -> None:
    _client().collection(DELIVERY_COLLECTION).document(delivery_id).set(
        {"received_at": datetime.now(timezone.utc).isoformat()}
    )


# --- Drift finding fingerprint: symbol + doc location, not the generated text ---


def finding_fingerprint(symbol: str, doc_path: str, doc_section: str) -> str:
    return content_hash(symbol, doc_path, doc_section)


def get_open_reference(fp: str) -> dict | None:
    """Returns route + reference for this drift, if something is already open for it."""
    doc = _client().collection(FINDING_COLLECTION).document(fp).get()
    return doc.to_dict() if doc.exists else None


def record_reference(fp: str, route: str, url: str, **extra) -> None:
    _client().collection(FINDING_COLLECTION).document(fp).set(
        {
            "route": route,
            "url": url,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
    )
