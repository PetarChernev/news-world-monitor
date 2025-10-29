# news_data_loader.py
from __future__ import annotations

from typing import List, Optional, Dict, Any
import re

import pandas as pd
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


_slug_re = re.compile(r"[^\w\s-]", re.UNICODE)
def slugify(s: str, max_len: int = 128) -> str:
    s = (s or "").lower()
    s = _slug_re.sub("", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:max_len] or "entity"


class NewsDataLoader:
    """
    Reads per-hour rollups from:
      /mentions/{hour}/entities/{entityId}
        { entity, type, total, countries: { ISO3: count }, ... }
      /mentions/{hour}/countries/{ISO3}
        { country, total, entities: { entityId: count }, ... }

    All methods return pandas.Series:
      - index: entity names (for entity-oriented queries) or ISO3 codes (for country-oriented)
      - values: integer counts
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        database: Optional[str] = None,  # e.g., "(default)" or custom db name
    ):
        client_kwargs: Dict[str, Any] = {}
        if project_id:
            client_kwargs["project"] = project_id
        if database:
            client_kwargs["database"] = database
        self.db = firestore.Client(**client_kwargs)

    def list_hours(self) -> List[str]:
        """
        Returns all available mention hours (document IDs) from /mentions,
        sorted ascending (e.g., '2025102908'). Filters to 10-digit YYYYMMDDHH.
        """
        coll = self.db.collection("mentions")
        hours = [snap.id for snap in coll.stream()]
        hours = [h for h in hours if re.fullmatch(r"\d{10}", h)]
        print(hours)
        return sorted(hours)

    # ---------- Helpers ----------
    def _mentions_hour_ref(self, hour: str) -> firestore.DocumentReference:
        return self.db.collection("mentions").document(hour)
    
    def load_articles(
        self,
        hour: str,
        country: Optional[str] = None,
        entity: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Dict[str, Any]]:
        """
        Load articles for a given hour, optionally filtered by country (ISO3) and entity.
        Uses server-side filtering on `entityNameSlug` (array of slug strings) with array_contains.

        Firestore: /articles/{articleId}
        - hour: 'YYYYMMDDHH' (string)
        - country: ISO3 (string)
        - entityNameSlug: [ 'joe-biden', 'openai', ... ]  # array of slugs

        Returns:
        pandas
        """
        coll = self.db.collection("articles")
        q = coll.where("hour", "==", hour)

        if country:
            q = q.where("country", "==", country)

        if entity:
            q = q.where(filter=FieldFilter("entityNameSlug", "array_contains", entity))

        if limit is not None:
            q = q.limit(int(limit))

        rows = []
        for snap in q.stream():
            dd = snap.to_dict() or {}
            rows.append({"id": snap.id, **dd})

        return rows


    # ---------- 1) number of news per country ----------
    def country_totals(self, hour: str) -> pd.Series:
        """
        Returns a Series indexed by ISO3 with 'total' per country in this hour.
        """
        docs = self._mentions_hour_ref(hour).collection("countries").stream()
        data = {}
        for d in docs:
            if not d.exists:
                continue
            dd = d.to_dict() or {}
            iso3 = d.id  # countries collection document ids are ISO3
            total = int(dd.get("total", 0) or 0)
            data[iso3] = total
        return pd.Series(data, dtype="int64").sort_index()

    # ---------- 2) number of news per entity (global) sorted & limited ----------
    def top_entities(self, hour: str, limit: int = 20) -> pd.Series:
        """
        Returns top-N entities globally for this hour.
        Index = entity names, values = total mentions.
        """
        q = (
            self._mentions_hour_ref(hour)
            .collection("entities")
            .order_by("total", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        data = {}
        for d in q.stream():
            dd = d.to_dict() or {}
            name = dd.get("entity") or d.id  # prefer human-readable name
            total = int(dd.get("total", 0) or 0)
            data[name] = total
        return pd.Series(data, dtype="int64").sort_values(ascending=False)

    # ---------- 3) number of news per entity for a given country sorted & limited ----------
    def top_entities_by_country(self, hour: str, iso3: str, limit: int = 20) -> pd.Series:
        """
        Reads from: /mentions/{hour}/countries/{ISO3}/entities/*
        Returns top-N entities for a specific ISO3 country in this hour.
        Index = entity names, values = mentions in that country for the hour.
        """
        q = (
            self._mentions_hour_ref(hour)
            .collection("countries")
            .document(iso3)
            .collection("entities")
            .order_by("count", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )

        data = {}
        for d in q.stream():
            dd = d.to_dict() or {}
            name = dd.get("entity") or dd.get("entityId") or d.id
            count = int(dd.get("count", 0) or 0)
            if count > 0:
                data[name] = count

        return pd.Series(data, dtype="int64").sort_values(ascending=False)

    # ---------- 4) number of news per country for a given entity ----------
    def country_breakdown_for_entity(self, hour: str, entity_name: str) -> pd.Series:
        """
        Reads from: /mentions/{hour}/entities/{entityId}/countries/*
        Returns counts per ISO3 for the given entity in this hour.
        Index = ISO3, values = counts.
        """
        eid = slugify(entity_name)
        countries_coll = (
            self._mentions_hour_ref(hour)
            .collection("entities")
            .document(eid)
            .collection("countries")
        )

        data = {}
        for c in countries_coll.stream():
            dd = c.to_dict() or {}
            iso3 = dd.get("country") or c.id
            count = int(dd.get("count", 0) or 0)
            data[iso3] = count

        return pd.Series(data, dtype="int64").sort_index()
