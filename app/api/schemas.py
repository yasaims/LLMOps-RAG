from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
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
