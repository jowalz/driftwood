"""Firestore: Drift-Fingerprints und Idempotenz."""

import hashlib
from datetime import datetime, timezone

from google.cloud import firestore

DELIVERY_COLLECTION = "driftwood-deliveries"
FINDING_COLLECTION = "driftwood-findings"


def _client() -> firestore.Client:
    return firestore.Client()


def content_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --- Webhook-Delivery-Dedup: schuetzt vor doppelter Pub/Sub-Zustellung ---


def already_delivered(delivery_id: str) -> bool:
    return _client().collection(DELIVERY_COLLECTION).document(delivery_id).get().exists


def mark_delivered(delivery_id: str) -> None:
    _client().collection(DELIVERY_COLLECTION).document(delivery_id).set(
        {"received_at": datetime.now(timezone.utc).isoformat()}
    )


# --- Drift-Finding-Fingerprint: Symbol + Doku-Stelle, nicht der generierte Text ---


def finding_fingerprint(symbol: str, doc_path: str, doc_section: str) -> str:
    return content_hash(symbol, doc_path, doc_section)


def get_open_reference(fp: str) -> dict | None:
    """Liefert Route + Referenz fuer diesen Drift, falls dazu schon etwas offen ist."""
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
