from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Iterable, Optional, Tuple

import pandas as pd

from news_data_loader import NewsDataLoader

@dataclass
class SmartArticleCache:
    """
    Caches articles by (hour, country, entity) and tries to satisfy more
    restrictive queries by filtering a less restrictive cached result if
    it already contains at least the expected number of items.

    When topic and countries are changed, we check if the state 
    (hour, country | None, entity | None) is present in the cache and if so,
    load it directly from there. If not, we check if there are articles
    in the cache which would include our new filter, e.g. when we select a 
    topic after having selected a country, the new state is (hour, country, entity), 
    but (hour, country, None) should be in the cache. In this case we get the 
    value of the number of articles for the current filter, filter the articles
    in the cache for the old filter and check if we have all articles for the
    new filter already present. If not, we load them from the DB.
    """
    store: Dict[str, list] = field(default_factory=dict)

    @staticmethod
    def _key(hour: str, country: Optional[str], entity: Optional[str]) -> str:
        return f"{hour}|{country or ''}|{entity or ''}"

    @staticmethod
    def _matches(article: Dict[str, Any], country: Optional[str], entity: Optional[str]) -> bool:
        if country and article.get("country") != country:
            return False
        if entity:
            slugs = article.get("entityNameSlug")
            if not isinstance(slugs, (list, tuple, set)):
                return False
            ent_slug = entity.lower().replace(" ", "-")
            lowset = {str(s).lower() for s in slugs}
            if (ent_slug not in lowset) and (entity not in slugs):
                return False
        return True

    @staticmethod
    def _sort_trim(rows: Iterable[Dict[str, Any]], limit: int = 3) -> list:
        def _to_dt(v):
            if isinstance(v, datetime):
                return v
            try:
                return pd.to_datetime(v)
            except Exception:
                return datetime.min
        rows_sorted = sorted(rows, key=lambda r: _to_dt(r.get("time")), reverse=True)
        return rows_sorted[:limit]

    @staticmethod
    def _expected_count(
        country: Optional[str],
        entity: Optional[str],
        totals_by_iso: Optional[Dict[str, int]],
        entity_breakdown_by_iso: Optional[Dict[str, int]],
        global_top_entities: Optional[Iterable[Tuple[str, int]]],
    ) -> Optional[int]:
        # country + entity
        if entity and country:
            if entity_breakdown_by_iso:
                return int(entity_breakdown_by_iso.get(country, 0))
            return None
        # entity only
        if entity and not country:
            if global_top_entities:
                g = {str(name): int(count) for name, count in global_top_entities}
                return int(g.get(entity, 0))
            return None
        # country only
        if country and not entity:
            if totals_by_iso:
                return int(totals_by_iso.get(country, 0))
            return None
        return None

    def get(
        self,
        *,
        hour: str,
        country: Optional[str],
        entity: Optional[str],
        totals_by_iso: Optional[Dict[str, int]],
        entity_breakdown_by_iso: Optional[Dict[str, int]],
        global_top_entities: Optional[Iterable[Tuple[str, int]]],
        loader: NewsDataLoader,
        per_page: int = 3,
    ) -> list:
        """
        Returns up to `per_page` articles for (hour, country, entity). Uses cache when possible.
        """
        if not hour:
            return []

        key = self._key(hour, country, entity)

        # 1) Exact cache hit
        if key in self.store:
            return self._sort_trim(self.store[key], per_page)

        # 2) Try to fulfill from a less restrictive cached parent if we have "enough"
        expected = self._expected_count(
            country, entity, totals_by_iso, entity_breakdown_by_iso, global_top_entities
        )

        parent_keys = []
        if country and entity:
            parent_keys.append(self._key(hour, country, None))  # country only
            parent_keys.append(self._key(hour, None, entity))   # entity only

        for pkey in parent_keys:
            if expected is not None and pkey in self.store:
                parent_rows = self.store[pkey] or []
                filtered = [r for r in parent_rows if self._matches(r, country, entity)]
                if len(filtered) >= expected:
                    # We have all we need for the stricter filter
                    self.store[key] = filtered
                    return self._sort_trim(filtered, per_page)

        # 3) Fallback to DB; cache exact key
        fetched = loader.load_articles(
            hour=hour,
            country=country or None,
            entity=entity or None,
            limit=per_page,
        )
        self.store[key] = fetched or []
        return self._sort_trim(self.store[key], per_page)

# Create a singleton instance for this process
article_cache = SmartArticleCache()
