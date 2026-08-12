from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI

from app import db
from app.api.schemas import QueryRequest, QueryResponse, SourceOut
from app.config import get_settings
from app.rag.generate import generate_answer
from app.rag.retrieve import retrieve


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.open_pool()
    try:
        yield
    finally:
        db.close_pool()


app = FastAPI(title="LLMOps RAG API", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    ok = db.ping()
    return {
        "status": "ok" if ok else "error",
        "db": ok,
        "embed_model": settings.bedrock_embed_model_id,
        "chat_model": settings.bedrock_chat_model_id,
    }


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    settings = get_settings()
    top_k = req.top_k or settings.rag_top_k

    start = time.perf_counter()
    chunks = retrieve(req.question, top_k)
    result = generate_answer(req.question, chunks)
    latency_ms = (time.perf_counter() - start) * 1000

    return QueryResponse(
        answer=result.answer,
        sources=[SourceOut(**asdict(s)) for s in result.sources],
        usage=result.usage,
        latency_ms=latency_ms,
    )
