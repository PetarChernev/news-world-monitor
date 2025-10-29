import traceback
from fastapi import FastAPI, Request, HTTPException
import base64, json
import logging
import google.cloud.logging

from article_processor import ArticleRaw, ArticleProcessor

client = google.cloud.logging.Client()

client.setup_logging()

app = FastAPI()
sb = ArticleProcessor(dry_run=False)  # set False to actually write to Firestore

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/pubsub")
async def pubsub_push(request: Request):
    envelope = await request.json()
    if not envelope or "message" not in envelope:
        raise HTTPException(status_code=400, detail="Invalid Pub/Sub push format.")

    msg = envelope["message"]
    data_b64 = msg.get("data", "")
    try:
        decoded = base64.b64decode(data_b64).decode("utf-8")
        payload = json.loads(decoded)
        article = ArticleRaw.from_dict(payload)
        logging.info(f"Received article for processing: {payload}")
    except Exception as e:
        logging.error("Error decoding Pub/Sub message:\n%s", traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"Bad message: {e}")
    
    if not article.title:
        return {"ok": True, "note": "No title; skipping processing."}

    try:
        summary = sb.process_article(article)
        # Return 200 so Pub/Sub acks message
        return {"ok": True, "summary": summary}
    except Exception as e:
        logging.error("Error processing article:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
