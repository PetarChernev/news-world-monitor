from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import os, json, requests, hashlib
from typing import List, Dict, Optional
import logging

from google.cloud import pubsub_v1
import google.cloud.logging

client = google.cloud.logging.Client()

client.setup_logging()

PROJECT_ID = os.environ["PROJECT_ID"] 
TOPIC_ID = os.environ["TOPIC_ID"] 
SOURCE_NAME = os.getenv("SOURCE_NAME", "gdelt") 

publisher = pubsub_v1.PublisherClient() 
logging.info(f"Using topic {TOPIC_ID} in project {PROJECT_ID}")
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)
app = FastAPI()

class RunBody(BaseModel):
    start: Optional[str] = None   # ISO8601 (UTC)
    end: Optional[str] = None     # ISO8601 (UTC)
    window_minutes: int = 60

def sha256(s: str) -> str:
    import hashlib; return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ---- Upstream error types that carry context --------------------------------
class UpstreamTemporaryError(Exception):
    def __init__(self, msg: str, context: Optional[Dict] = None):
        super().__init__(msg)
        self.context = context or {}

class UpstreamPermanentError(Exception):
    def __init__(self, msg: str, context: Optional[Dict] = None):
        super().__init__(msg)
        self.context = context or {}

# ---- Helper to build serializable upstream context ---------------------------
def _upstream_context(resp: requests.Response, note: str) -> Dict:
    # Limit body size to avoid huge payloads in error responses/logs
    body = ""
    try:
        body = resp.text  # safe even for bytes; requests decodes if possible
    except Exception:
        body = "<unreadable body>"
    return {
        "note": note,
        "status_code": resp.status_code,
        "url": getattr(resp, "url", None),
        "content_type": resp.headers.get("Content-Type"),
        "cache_control": resp.headers.get("Cache-Control"),
        "body_snippet": body[:4000],   # truncate to 4KB
        "body_truncated": len(body) > 4000,
    }

# ---- GDELT fetch -------------------------------------------------------------
def fetch_gdelt(start_iso: str, end_iso: str) -> List[Dict]:
    BASE = "https://api.gdeltproject.org/api/v2/doc/doc"

    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end_dt   = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))

    start_str = start_dt.strftime("%Y%m%d%H%M%S")
    end_str   = end_dt.strftime("%Y%m%d%H%M%S")

    params = {
        "query": "sourcelang:english",
        "mode": "artlist",
        "format": "json",
        "maxrecords": 250,
        "startdatetime": start_str,
        "enddatetime": end_str,
    }

    try:
        r = requests.get(BASE, params=params, timeout=60)
    except requests.RequestException as e:
        raise UpstreamTemporaryError(f"Network error calling GDELT: {e}")

    # Treat 5xx/429 as temporary; include body
    if 500 <= r.status_code < 600 or r.status_code == 429:
        raise UpstreamTemporaryError(
            f"GDELT HTTP {r.status_code}: temporary upstream error",
            context=_upstream_context(r, "5xx/429 from GDELT"),
        )

    # 4xx likely permanent (bad request), unless it's their generic HTML error page
    if 400 <= r.status_code < 500:
        txt_lower = r.text.lower() if r.text else ""
        if "unknown error occurred" in txt_lower:
            raise UpstreamTemporaryError(
                f"GDELT HTTP {r.status_code}: temporary 'unknown error occurred' page",
                context=_upstream_context(r, "HTML error page (4xx)"),
            )
        raise UpstreamPermanentError(
            f"GDELT HTTP {r.status_code}: bad request",
            context=_upstream_context(r, "4xx from GDELT"),
        )

    # Parse JSON; on failure include raw body/content-type in context
    try:
        data = r.json()
    except ValueError:
        txt_lower = r.text.lower() if r.text else ""
        note = "Non-JSON response from GDELT"
        if "unknown error occurred" in txt_lower:
            note = "Temporary HTML 'unknown error occurred' page (non-JSON)"
            raise UpstreamTemporaryError(
                "GDELT responded with temporary HTML error page (non-JSON).",
                context=_upstream_context(r, note),
            )
        raise UpstreamTemporaryError(
            "GDELT returned non-JSON response; could not parse JSON.",
            context=_upstream_context(r, note),
        )

    articles = data.get("articles", [])
    for a in articles:
        a.setdefault("source", "gdelt")
    return articles

# ---- Routes ------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.post("/run")
async def run(body: RunBody):
    if body.start and body.end:
        start = datetime.fromisoformat(body.start.replace("Z","")).replace(tzinfo=timezone.utc)
        end   = datetime.fromisoformat(body.end.replace("Z","")).replace(tzinfo=timezone.utc)
    else:
        end   = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
        start = end - timedelta(minutes=body.window_minutes)

    start_iso = start.isoformat().replace("+00:00","Z")
    end_iso   = end.isoformat().replace("+00:00","Z")

    try:
        batch = fetch_gdelt(start_iso, end_iso)
    except UpstreamTemporaryError as e:
        detail = {"ok": False, "error": str(e), "start": start_iso, "end": end_iso}
        if getattr(e, "context", None):
            detail["upstream"] = e.context
        # 503 so Cloud Scheduler retries
        raise HTTPException(status_code=503, detail=detail)
    except UpstreamPermanentError as e:
        detail = {"ok": False, "error": str(e), "start": start_iso, "end": end_iso}
        if getattr(e, "context", None):
            detail["upstream"] = e.context
        # 502 to signal upstream/bad request (tune per your retry policy)
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": f"Unexpected error: {e}", "start": start_iso, "end": end_iso},
        )

    published = 0
    for a in batch:
        key = (a.get("url") or "") + "|" + (a.get("published_at") or start_iso)
        a["article_id"] = sha256(key)
        data = json.dumps(a).encode("utf-8")
        logging.info(data)
        future = publisher.publish(topic_path, data); 
        future.result()
        published += 1

    return {"ok": True, "source": SOURCE_NAME, "start": start_iso, "end": end_iso, "count": published}
