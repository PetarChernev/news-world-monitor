# firestore_writer.py
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from firestore_rollup import FirestoreRollup, hour_bucket  # import from above


def _parse_time_to_utc(time_val: Any) -> datetime:
    if isinstance(time_val, datetime):
        dt = time_val
    elif isinstance(time_val, str):
        dt = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported time type: {type(time_val)}")
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _deterministic_article_id(url: Optional[str], title: str, dt_utc: datetime) -> str:
    basis = url or f"{title}||{hour_bucket(dt_utc)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


class FirestoreWriter:
    """
    Upserts an article at /articles/{docId} and then calls the rollup manager
    to increment counters idempotently (transactional).
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        collection: str = "articles",
        rollup: Optional[FirestoreRollup] = None,
    ):
        self.client = firestore.Client(project=project_id, database="world-news-knowledge")
        self.articles = self.client.collection(collection)
        self.rollup = rollup or FirestoreRollup(self.client)

    def _normalize_entities(self, canonical: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        ents = []
        for _, meta in canonical.items():
            name = meta.get("name")
            if not name:
                continue
            ents.append({"name": name, "type": meta.get("type") or "OTHER"})
        return ents
    
    @firestore.transactional
    @staticmethod
    def _tx_write(tx: firestore.Transaction, doc_ref, payload):
        snap = doc_ref.get(transaction=tx)
        if snap.exists:
            tx.set(doc_ref, payload, merge=True)
        else:
            tx.set(
                doc_ref,
                {**payload, "created_at": firestore.SERVER_TIMESTAMP},
                merge=False,
            )

    def write_article(
        self,
        *,
        title: str,
        text: str,
        country: Optional[str],
        time_val: Any,
        entities_canonical: Dict[str, Dict[str, Any]],
        url: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        dt_utc = _parse_time_to_utc(time_val)
        entities = self._normalize_entities(entities_canonical)
        entity_names = [e["name"] for e in entities]

        used_doc_id = doc_id or _deterministic_article_id(url, title, dt_utc)
        doc_ref = self.articles.document(used_doc_id)

        payload = {
            "title": title,
            "text": text or "",
            "country": (country or "").upper(),
            "time": dt_utc,  # SDK converts to Timestamp
            "entities": entities,
            "entityNameSlug": entity_names,
            "url": url or "",
            "updated_at": firestore.SERVER_TIMESTAMP,
            # NOTE: we DO NOT set "hour" or "rollups_done" here; rollup txn will.
        }
        if extra_fields:
            payload.update(extra_fields)


        tx = self.client.transaction()
        self._tx_write(tx, doc_ref, payload)

        # Now apply rollups safely (transactional + idempotent)
        self.rollup.apply_for_article(doc_ref)

        return used_doc_id
