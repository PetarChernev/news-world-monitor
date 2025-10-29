
from typing import Any, Dict

from ai_api_loader import AIAPILoader



class EntityExtractor:
    """
    Uses your /entities API to get canonical entity candidates from (headline + text).
    """
    ENTITY_TYPE_FILTER = {
        "PERSON", "LOCATION", "ORGANIZATION",
    }

    def __init__(self, api: AIAPILoader):
        self.api = api

    def build(self, text) -> Dict[str, Any]:
        api_ents = self.api.extract_entities(text)
        # we filter the entities on type and Wikipedia link or Knowledge Graph ID (mid)
        # since the entity extraction is too fine an tags abstract entities (e.g. man, street)
        api_ents = [e for e in api_ents 
                    if e.get("type") in self.ENTITY_TYPE_FILTER 
                    and ('wikipedia_url' in e['metadata'] or 'mid' in e['metadata'])]

        # Simple canonical table by representative name
        canonical: Dict[str, Dict[str, Any]] = {}
        for e in api_ents:
            name = e["name"]
            if name not in canonical:
                canonical[name] = {
                    "type": e.get("type") or "OTHER",
                    "name": name,
                }


        return canonical