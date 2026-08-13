from __future__ import annotations

from app import db
from app.bedrock import embed_query


def retrieve(question: str, top_k: int) -> list[db.SearchResult]:
    embedding = embed_query(question)
    return db.search(embedding, top_k)
