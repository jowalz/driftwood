"""Firestore: Fingerprints und Idempotenz für verarbeitete Events."""

import hashlib
from datetime import datetime, timezone

from google.cloud import firestore

COLLECTION = "driftwood-state"


def _client() -> firestore.Client:
    return firestore.Client()


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def already_processed(fp: str) -> bool:
    doc = _client().collection(COLLECTION).document(fp).get()
    return doc.exists


def mark_processed(fp: str, metadata: dict | None = None) -> None:
    _client().collection(COLLECTION).document(fp).set(
        {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
    )
