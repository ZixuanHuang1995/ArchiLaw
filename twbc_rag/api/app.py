"""FastAPI query endpoint. Binds to 0.0.0.0 so Render and Compose can reach it."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from twbc_rag.rag.answer import DISCLAIMER, answer_question
from twbc_rag.rag.engine import EMBED_MODEL, get_engine
from twbc_rag.rag.generate import llm_status

logger = logging.getLogger(__name__)

EXAMPLES = [
    "設計施工編第110-1條對非防火構造建築物的防火間隔怎麼規定？",
    "私設通路可以計入法定空地嗎？和道路交叉要不要截角？",
    "台北市房屋稅的稅率是多少？",
]


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        get_engine().ensure_ready()
    except Exception:
        logger.exception("Index build failed; /query will retry")
    yield


app = FastAPI(title="twbc-rag", version="0.2.0", lifespan=_lifespan)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: str = "hybrid"
    top_k: int = 5


@app.get("/health")
def health() -> dict:
    engine = get_engine()
    return {
        "status": "ok",
        "ready": engine.ready,
        "articles": len(engine.chunks),
        "embed_model": EMBED_MODEL,
        "llm": llm_status(),
        "disclaimer": DISCLAIMER,
    }


@app.get("/examples")
def examples() -> dict:
    return {"questions": EXAMPLES}


@app.post("/query")
def query(body: QueryRequest) -> dict:
    mode = body.mode if body.mode in {"bm25", "dense", "hybrid", "hybrid_rerank"} else "hybrid"
    return answer_question(body.question.strip(), mode=mode, top_k=body.top_k)
