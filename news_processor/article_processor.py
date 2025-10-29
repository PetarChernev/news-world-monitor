"""
statement_builder.py

Pipeline:
(headline, text, time) -> EntityBuilder -> SyntaxBuilder -> RelationsBuilder -> EmbeddingsBuilder -> FirestoreWriter

- AIAPILoader handles calls to:
    /embed/text, /embed/word, /entities
- Entities endpoint returns a plaintext format (as in the prompt); we parse it.
- Writes reified statements ("edges") and entities into Firestore with deterministic IDs.

Author: you :)
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from typing import Any, Dict, Optional

import pycountry
import google.cloud.logging

from ai_api_loader import AIAPILoader
from datatypes import ArticleRaw
from entity_extractor import EntityExtractor
from firestore_writer import FirestoreWriter
from utils import normalize_space


client = google.cloud.logging.Client()

client.setup_logging()


AI_API_BASE = os.getenv("AI_API", "https://ai-api-1010480476071.europe-central2.run.app")
PROJECT_ID = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")



class ArticleProcessor:
    """
    Orchestrates: (headline, text, time_iso) -> entities -> BigQuery.
    """
    def __init__(
            self,
            api: Optional[AIAPILoader] = None,
            firestore_writer: Optional[FirestoreWriter] = None,
            dry_run: bool = False):
        self.api = api or AIAPILoader(AI_API_BASE)
        self.entities_extractor = EntityExtractor(self.api)
        self.writer = firestore_writer or FirestoreWriter(project_id=PROJECT_ID)
        self.dry_run = dry_run

    def process_article(self, article: ArticleRaw) -> Dict[str, Any]:
        """
        Runs the full pipeline and writes results.
        Returns a small summary dict.
        """
        # For now: just use the title as the full doc.
        full_text = normalize_space(article.title.strip())
        logging.info(f"Processing {article.title} with full text {full_text}")

        # 1) Entities (canonical + sentence-level spans)
        # We don't have the body text yet; pass empty string for text.
        canonical = self.entities_extractor.build(full_text)
        logging.info(f"Entities are {canonical}")

        summary = {
            "doc_id": article.url,
            "entities": canonical,
        }

        country = getattr(article, "sourcecountry", None)
        try:
            country_code = pycountry.countries.lookup(country).alpha_3
        except Exception:
            print(f"Unknown country: {country}")
            country_code = None

        if not self.dry_run:
            # Prepare Firestore write with your schema
            used_id = self.writer.write_article(
                title=article.title,
                text=getattr(article, "text", "") or "",  
                country=country_code,
                time_val=datetime.fromisoformat(getattr(article, "seendate")),
                entities_canonical=canonical,
                url=getattr(article, "url", None),
            )
            summary["firestore_id"] = used_id
        return summary
    
# =========================
# Example usage
# =========================

if __name__ == "__main__":
    with open("/home/petar/Downloads/downloaded-logs-20251028-230836.json", 'rb') as file:
        articles_logs = json.load(file)
    articles_jsons = [a['textPayload'][2:-1] for a in articles_logs if 'textPayload' in a]
    articles = []
    for article_json in articles_jsons:
        try:
            article = ArticleRaw.from_dict(json.loads(article_json))
        except Exception:
            pass
        articles.append(article)


    sb = ArticleProcessor(dry_run=False)  # set dry_run=False when Firestore is configured
    for article in articles:
        # print(article.sourcecountry)
        summary = sb.process_article(article)
    print("Summary:", summary)
