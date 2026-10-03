from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Caps embed/chat input tokens per request on the public endpoint.
MAX_QUESTION_CHARS = 1000


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    top_k: int | None = Field(default=None, ge=1, le=20)


class SourceOut(BaseModel):
    index: int
    service: str
    doc: str
    section: str | None
    page_start: int | None
    source_url: str
    score: float


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    usage: dict[str, Any]
    latency_ms: float
