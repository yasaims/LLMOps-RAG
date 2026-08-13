from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas import QueryRequest, QueryResponse, SourceOut
from app.config import get_settings
from app.logging_config import configure_logging, log_event
from app.rag.generate import generate_answer
from app.rag.retrieve import retrieve
from app.vectorstore import get_store

configure_logging()
logger = logging.getLogger("app.query")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_store().open()
    try:
        yield
    finally:
        get_store().close()


app = FastAPI(title="LLMOps RAG API", lifespan=lifespan)

_settings = get_settings()
if _settings.cors_allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _settings.cors_allow_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    ok = get_store().ping()
    return {
        "status": "ok" if ok else "error",
        "vector_store": settings.vector_store,
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

    log_event(
        logger,
        "query_completed",
        latency_ms=round(latency_ms, 1),
        top_k=top_k,
        num_sources=len(result.sources),
        top_score=chunks[0].score if chunks else None,
        input_tokens=result.usage.get("inputTokens"),
        output_tokens=result.usage.get("outputTokens"),
    )

    return QueryResponse(
        answer=result.answer,
        sources=[SourceOut(**asdict(s)) for s in result.sources],
        usage=result.usage,
        latency_ms=latency_ms,
    )
