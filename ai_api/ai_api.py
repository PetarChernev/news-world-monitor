from datetime import datetime
import json
import os
from typing import List, Optional, Literal, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, constr, validator
from starlette.middleware.cors import CORSMiddleware

# Vertex AI SDK
import vertexai
from vertexai.language_models import TextEmbeddingModel
from openai import OpenAI

# --- NEW: Cloud NL imports ---------------------------------------------------
from google.cloud import language_v1

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------

PROJECT_ID = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GCP_LOCATION", "europe-west2")
# You can switch models via env if needed (e.g., "text-embedding-005" once available)
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-005")

PROMPT_SYSTEM_PATH = "prompts/topics/system.md"
PROMPT_USER_PATH = "prompts/topics/user.md"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

with open(PROMPT_SYSTEM_PATH, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read().strip()
with open(PROMPT_USER_PATH, "r", encoding="utf-8") as f:
    USER_PROMPT_TEMPLATE = f.read().strip()


# Vertex allows up to 2048 inputs per call; keep conservative for latency/memory
DEFAULT_MAX_BATCH = int(os.getenv("MAX_BATCH", "256"))

if not PROJECT_ID:
    raise RuntimeError(
        "Missing GCP project. Set the env var GCP_PROJECT (or GOOGLE_CLOUD_PROJECT)."
    )

# Initialize Vertex once at import time (Cloud Run keeps the instance warm when possible)
vertexai.init(project=PROJECT_ID, location=LOCATION)
_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

# --- NEW: Initialize Cloud NL once ------------------------------------------
_nl_client = language_v1.LanguageServiceClient()

# --------------------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------------------

app = FastAPI(title="Embeddings API (Vertex AI)",
              version="1.1.0",
              description="Word and text embeddings via Vertex AI text-embedding model, plus entity analysis via Cloud NL.")

# Optional: let browsers call your API during dev; tighten origins for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # change to your domain(s) in production
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# --------------------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------------------

# Keep types literal for discoverability; Vertex supports these task types.
TaskType = Optional[Literal["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY",
                            "CLASSIFICATION", "CLUSTERING"]]

class TextEmbedRequest(BaseModel):
    inputs: List[constr(strip_whitespace=True, min_length=1)] = Field(
        ..., description="Array of texts to embed."
    )
    batch_size: Optional[int] = Field(
        None, ge=1, le=2048, description="Override internal batching (default 256)."
    )

class WordEmbedRequest(BaseModel):
    words: List[constr(strip_whitespace=True, min_length=1)] = Field(
        ..., description="Array of words to embed (each treated as text)."
    )
    # kept for symmetry; not strictly needed for single words
    task_type: TaskType = None
    batch_size: Optional[int] = Field(
        None, ge=1, le=2048, description="Override internal batching (default 256)."
    )

class EmbedResponse(BaseModel):
    model: str
    dimension: int
    count: int
    embeddings: List[List[float]]

# --- NEW: Entity models ------------------------------------------------------
EntityType = Literal[
    "UNKNOWN", "PERSON", "LOCATION", "ORGANIZATION", "EVENT", "WORK_OF_ART",
    "CONSUMER_GOOD", "OTHER", "PHONE_NUMBER", "ADDRESS", "DATE", "NUMBER", "PRICE"
]
MentionType = Literal["TYPE_UNKNOWN", "PROPER", "COMMON"]

class EntityMention(BaseModel):
    text: str
    type: MentionType
    salience: float = Field(ge=0.0, le=1.0)
    time: datetime

class Entity(BaseModel):
    name: str
    type: EntityType
    metadata: Dict[str, str] = {}
    mentions: List[EntityMention] = []

class EntitiesRequest(BaseModel):
    text: constr(strip_whitespace=True, min_length=1)
    # Optional; if None the API will auto-detect
    language: Optional[str] = Field(
        default=None,
        description="BCP-47 language code (e.g., 'en', 'de'). If not provided, language is auto-detected."
    )
    # Optional encoding override; default UTF8 is appropriate for JSON
    encoding: Optional[Literal["UTF8", "UTF16", "UTF32"]] = "UTF8"

class EntitiesResponse(BaseModel):
    language: str
    entities: List[Entity]

class EmbeddingIn(BaseModel):
    input: list[str] | str
    model: str = "text-embedding-3-small"  # tiny, cheap; swap as needed


class TopicItem(BaseModel):
    text: str = Field(..., description="Original headline text.")
    entities: List[str] = Field(..., description="List of precomputed entities for the headline.")

    @validator("entities")
    def no_none_entities(cls, v):
        if any(e is None for e in v):
            raise ValueError("entities cannot contain nulls")
        return v

class TopicBatchRequest(BaseModel):
    items: List[TopicItem] = Field(..., min_items=1)

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _chunked(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _vertex_embed(
    inputs: List[str],
    batch_size: Optional[int] = None
) -> EmbedResponse:
    """
    Calls Vertex AI TextEmbeddingModel with batching.
    Returns a unified EmbedResponse.
    """
    if not inputs:
        raise HTTPException(status_code=400, detail="No inputs provided.")

    bsize = min(batch_size or DEFAULT_MAX_BATCH, 2048)

    vectors: List[List[float]] = []
    reported_dim: Optional[int] = None

    try:
        for batch in _chunked(inputs, bsize):
            result = _model.get_embeddings(batch)

            # result is a list of Embedding objects with .values (List[float])
            for emb in result:
                vec = list(emb.values)
                if reported_dim is None:
                    reported_dim = len(vec)
                vectors.append(vec)

        dim = reported_dim or 0

        return EmbedResponse(
            model=EMBEDDING_MODEL_NAME,
            dimension=dim,
            count=len(vectors),
            embeddings=vectors,
        )
    except Exception as e:
        # Surface a concise error; full trace in Cloud Logging
        raise HTTPException(status_code=502, detail=f"Vertex embedding call failed: {e}")
    

def _format_entities_as_python_list(entities: List[str]) -> str:
    """
    The model expects the entity line as something like:
    ['Blue Angels', 'National Mall', 'streets', 'DC', 'National Guard']
    Use repr() to preserve quotes/escapes robustly.
    """
    return "[" + ", ".join(repr(e) for e in entities) + "]"

def _build_user_prompt_block(items: List[TopicItem]) -> str:
    """
    Build the Input: block that the user prompt asks for:
    Headline on one line, entity Python-like list on the next line, repeated.
    Keep exact text spacing/punctuation as provided.
    """
    lines = []
    for it in items:
        lines.append(it.text)
        lines.append(_format_entities_as_python_list(it.entities))
    block = "\n".join(lines)
    return USER_PROMPT_TEMPLATE + "\n\nInput:\n\n```\n" + block + "\n```"

# --- NEW: Entities helper ----------------------------------------------------
_LV1 = language_v1  # alias for brevity

def _analyze_entities(
    text: str,
    language: Optional[str] = None,
    encoding: str = "UTF8",
) -> EntitiesResponse:
    if not text:
        raise HTTPException(status_code=400, detail="No text provided.")

    now = datetime.now()
    # Build Document
    doc_kwargs = {
        "content": text,
        "type_": _LV1.Document.Type.PLAIN_TEXT,
    }
    if language:
        doc_kwargs["language"] = language
    document = doc_kwargs

    # Map encoding str to enum
    enc_map = {
        "UTF8": _LV1.EncodingType.UTF8,
        "UTF16": _LV1.EncodingType.UTF16,
        "UTF32": _LV1.EncodingType.UTF32,
    }
    encoding_type = enc_map.get(encoding.upper(), _LV1.EncodingType.UTF8)

    try:
        response = _nl_client.analyze_entities(
            request={"document": document, "encoding_type": encoding_type}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cloud NL analyze_entities failed: {e}")

    ents: List[Entity] = []
    for ent in response.entities:
        # Type name from enum
        ent_type: EntityType = _LV1.Entity.Type(ent.type_).name  # type: ignore
        mentions = [
            EntityMention(
                text=m.text.content,
                type=_LV1.EntityMention.Type(m.type_).name,
                salience=float(ent.salience),
                time=now
            )
            for m in ent.mentions
        ]
        # metadata is a Mapping[str, str]
        md = dict(ent.metadata)
        ents.append(
            Entity(
                name=ent.name,
                type=ent_type,
                metadata=md,
                mentions=mentions
            )
        )

    return EntitiesResponse(language=response.language or (language or "und"), entities=ents)

# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "project": PROJECT_ID,
        "location": LOCATION,
        "model": EMBEDDING_MODEL_NAME,
    }

@app.post("/embed/text", response_model=EmbedResponse)
def embed_text(payload: TextEmbedRequest):
    """
    Embeds arbitrary texts (sentences/paragraphs/documents) using Vertex AI text-embedding model.
    """
    return _vertex_embed(
        inputs=payload.inputs,
        batch_size=payload.batch_size,
    )

@app.post("/embed/word", response_model=EmbedResponse)
def embed_word(payload: WordEmbedRequest):
    """
    Embeds words by calling the same Vertex model on each word.
    This keeps deployment simple and avoids maintaining separate word-vector files.
    """
    # Reuse the same path; you could set a different task_type here if desired.
    return _vertex_embed(
        inputs=payload.words,
        task_type=payload.task_type,
        batch_size=payload.batch_size,
    )

@app.post("/embed/gpt")
def embed(body: EmbeddingIn):
    # Accepts a string or list of strings
    resp = openai_client.embeddings.create(model=body.model, input=body.input)
    # Standardize to list-of-vectors output
    vectors = [d.embedding for d in resp.data]
    return {"model": resp.model, "vectors": vectors}

@app.post("/entities", response_model=EntitiesResponse)
def entities(payload: EntitiesRequest):
    """
    Extracts entities from text using Google Cloud Natural Language API.
    Returns canonical entity names, types, salience, metadata (e.g., wikipedia_url, mid),
    and the list of mentions (text and mention type).
    """
    return _analyze_entities(
        text=payload.text,
        language=payload.language,
        encoding=payload.encoding or "UTF8",
    )



@app.post("/topics")
def extract_topics(req: TopicBatchRequest):
    # Build the user prompt from the incoming batch
    user_prompt = _build_user_prompt_block(req.items)

    try:
        # Call the Responses API with system + user messages
        resp = openai_client.responses.create(
            model=DEFAULT_MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI request failed: {e}")

    # The Responses API provides a convenience .output_text for pure-text results.
    # If not present in your SDK version, fall back to traversing the content array.
    output_text = getattr(resp, "output_text", None)
    if not output_text:
        # Fallback extraction (works with many client versions)
        try:
            # Newer Responses API shape:
            # resp.output -> list of content parts; each part may have .content[0].text
            # We'll try common possibilities.
            if hasattr(resp, "output") and resp.output:
                # Try to join any text parts
                parts = []
                for item in resp.output:
                    for c in getattr(item, "content", []) or []:
                        if getattr(c, "type", None) == "output_text" and hasattr(c, "text"):
                            parts.append(c.text)
                output_text = "\n".join(parts).strip() if parts else None
            # Legacy fallback
            if not output_text and hasattr(resp, "choices"):
                output_text = resp.choices[0].message["content"]
        except Exception:
            pass

    if not output_text:
        raise HTTPException(status_code=500, detail="Could not read model output.")

    # Ensure the model returned valid JSON (a list of objects)
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Model did not return valid JSON. Error: {e}. Raw output: {output_text}",
        )

    return parsed