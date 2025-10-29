from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from google.cloud import firestore


def _to_utc(dt: Any) -> datetime:
    """Accepts Firestore Timestamp, datetime, or ISO string -> aware UTC datetime."""
    if hasattr(dt, "to_datetime"):
        dt = dt.to_datetime()
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if not isinstance(dt, datetime):
        raise TypeError(f"Unsupported time value: {type(dt)}")
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hour_bucket(dt_utc: datetime) -> str:
    return dt_utc.strftime("%Y%m%d%H")


_slug_re = re.compile(r"[^\w\s-]", re.UNICODE)
def slugify(s: str, max_len: int = 128) -> str:
    s = (s or "").lower()
    s = _slug_re.sub("", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:max_len] or "entity"


@dataclass(frozen=True)
class RollupPaths:
    mentions_root: str = "mentions"   # /mentions/{hour}/(entities|countries)/...


class FirestoreRollup:
    """
    Transactionally increments per-hour rollups under:
      - /mentions/{hour}/entities/{entityId}                -> { entity, type, total }
          /countries/{ISO3}                                  -> { country, count }
      - /mentions/{hour}/countries/{ISO3}                   -> { country, total }
          /entities/{entityId}                               -> { entityId, entity, type, count }

    Idempotent via article.rollups_done = True (set inside the same txn).
    """

    def __init__(self, client: Optional[firestore.Client] = None, paths: RollupPaths = RollupPaths()):
        self.db = client or firestore.Client()
        self.paths = paths

    def apply_for_article(self, article_ref: firestore.DocumentReference) -> None:
        @firestore.transactional
        def _txn(tx: firestore.Transaction) -> None:
            snap = article_ref.get(transaction=tx)
            if not snap.exists:
                return

            a = snap.to_dict() or {}
            if a.get("rollups_done"):
                return

            # Derive hour and normalize country
            time_val = a.get("time")
            dt_utc = _to_utc(time_val) if time_val else datetime.now(timezone.utc)
            hour = a.get("hour") or hour_bucket(dt_utc)
            country = (a.get("country") or "XX").upper()
            entities: Sequence[Dict[str, Any]] = a.get("entities") or []

            # Mark article as processed (+ store hour)
            tx.set(
                article_ref,
                {"hour": hour, "rollups_done": True, "updated_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )

            # Base refs
            mentions_hour = self.db.collection(self.paths.mentions_root).document(hour)
            tx.set(
                mentions_hour,
                {
                    "hour": hour,
                },
                merge=True,
            )
            countries_coll = mentions_hour.collection("countries")
            entities_coll = mentions_hour.collection("entities")

            # Country totals (per article)
            country_ref = countries_coll.document(country)
            tx.set(
                country_ref,
                {
                    "country": country,
                    "total": firestore.Increment(1),
                    "updated_at": firestore.SERVER_TIMESTAMP,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )

            # For each entity in the article, bump both directions
            for ent in entities:
                name = (ent or {}).get("name")
                if not name:
                    continue
                etype = (ent or {}).get("type") or "OTHER"
                eid = slugify(name)

                # /mentions/{hour}/entities/{eid} -> keep global per-hour total
                ent_ref = entities_coll.document(eid)
                tx.set(
                    ent_ref,
                    {
                        "entity": name,
                        "type": etype,
                        "total": firestore.Increment(1),
                        "updated_at": firestore.SERVER_TIMESTAMP,
                        "created_at": firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )

                # /mentions/{hour}/entities/{eid}/countries/{ISO2} -> count by country
                ent_country_ref = ent_ref.collection("countries").document(country)
                tx.set(
                    ent_country_ref,
                    {
                        "country": country,
                        "count": firestore.Increment(1),
                        "updated_at": firestore.SERVER_TIMESTAMP,
                        "created_at": firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )

                # /mentions/{hour}/countries/{ISO2}/entities/{eid} -> count by entity for this country
                country_entity_ref = country_ref.collection("entities").document(eid)
                tx.set(
                    country_entity_ref,
                    {
                        "entityId": eid,
                        "entity": name,
                        "type": etype,
                        "count": firestore.Increment(1),
                        "updated_at": firestore.SERVER_TIMESTAMP,
                        "created_at": firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )

        tx = self.db.transaction()
        _txn(tx)
