"""AI inference service: multilingual sentence embeddings for headlines.

Handles ru, lt, lv, et, en, ...
POST /embed {"texts": [...]} -> {"vectors": [[...], ...]} (L2-normalised, so dot product = cosine).
"""

import os
import time

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL = os.environ.get("MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
model = SentenceTransformer(MODEL, device="cpu")  # weights are baked into the image at build time
app = FastAPI()


class Req(BaseModel):
    """Request body for /embed: the texts to embed."""

    texts: list[str]


@app.post("/embed")
def embed(r: Req):
    """Embed the given texts and return normalised vectors with model name, size and timing."""
    t = time.perf_counter()
    v = model.encode(r.texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True)
    return {
        "model": MODEL,
        "dim": int(v.shape[1]),
        "ms": int(1000 * (time.perf_counter() - t)),
        "vectors": v.round(5).tolist(),
    }


@app.get("/health")  # not /healthz: Cloud Run never routes that path
def health():
    """Report that the service is up and which model is loaded."""
    return {"ok": True, "model": MODEL}
