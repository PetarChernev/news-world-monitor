

import logging
from typing import Any, Dict, List, Optional
import time as pytime

from google.oauth2 import id_token
from google.auth.transport.requests import Request
import requests

import google.cloud.logging

client = google.cloud.logging.Client()

client.setup_logging()

class AIAPILoader:
    """
    Secure API client for AIAPI running on GCP (e.g., Cloud Run).
    Uses OIDC ID tokens for service-to-service authentication.
    """

    def __init__(self, base: str, timeout: int = 30, audience: Optional[str] = None):
        """
        Args:
            base: Base URL of the AIAPI service, e.g. "https://aiapi-xxxxx.a.run.app"
            timeout: Request timeout in seconds
            audience: Optional explicit audience for ID token (defaults to `base`)
        """
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.audience = audience or self.base
        self._cached_token: Optional[str] = None
        self._cached_token_expiry: float = 0.0

    # -------------------------------------------------------------------------
    # Authentication helper
    # -------------------------------------------------------------------------

    def _get_id_token(self) -> str:
        """Fetch and cache an ID token, supporting local impersonation."""
        now = pytime.time()
        if self._cached_token and now < self._cached_token_expiry - 30:
            return self._cached_token
        
        token = id_token.fetch_id_token(Request(), self.audience)

        self._cached_token = token
        self._cached_token_expiry = now + 50 * 60
        return token

    def _auth_headers(self) -> Dict[str, str]:
        token = self._get_id_token()
        return {"Authorization": f"Bearer {token}"}

    # -------------------------------------------------------------------------
    # API calls
    # -------------------------------------------------------------------------

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base}{path}"
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_headers())
        r = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        # Try JSON, fallback to text
        try:
            return r.json()
        except Exception:
            return r.text

    def embed_texts(self, texts: List[str], batch_size: Optional[int] = None) -> List[List[float]]:
        payload: Dict[str, Any] = {"inputs": texts}
        if batch_size is not None:
            payload["batch_size"] = batch_size
        data = self._post_json("/embed/text", payload)
        return data["embeddings"]

    def embed_words(self, words: List[str], task_type: Optional[str] = None,
                    batch_size: Optional[int] = None) -> List[List[float]]:
        payload: Dict[str, Any] = {"words": words, "task_type": task_type}
        if batch_size is not None:
            payload["batch_size"] = batch_size
        data = self._post_json("/embed/word", payload)
        return data["embeddings"]

    def extract_entities(self, text: str) -> List[Dict[str, Any]]:
        """
        Calls /entities endpoint and parses plaintext output into list of entity dicts.
        """
        payload = {"text": text}
        logging.info(payload)
        raw = self._post_json("/entities", payload)
        if isinstance(raw, dict):
            # If API returns JSON instead of text, just pass through
            return raw.get("entities", [])
        if not isinstance(raw, str):
            raw = str(raw)

        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        ents: List[Dict[str, Any]] = []
        cur: Dict[str, Any] = {}

        def flush():
            if cur:
                ents.append(cur.copy())
                cur.clear()

        for ln in lines:
            if ln.startswith("Representative name for the entity:"):
                flush()
                cur["rep_name"] = ln.split(":", 1)[1].strip()
            elif ln.startswith("Entity type:"):
                cur["type"] = ln.split(":", 1)[1].strip()
            elif ln.startswith("Salience score:"):
                try:
                    cur["salience"] = float(ln.split(":", 1)[1].strip())
                except Exception:
                    cur["salience"] = None
            elif ln.startswith("Mention text:"):
                cur.setdefault("mentions", [])
                cur["mentions"].append({"text": ln.split(":", 1)[1].strip()})
            elif ln.startswith("Mention type:"):
                if "mentions" in cur and cur["mentions"]:
                    cur["mentions"][-1]["type"] = ln.split(":", 1)[1].strip()
        flush()
        return ents
