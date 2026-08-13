from __future__ import annotations

from app.bedrock import embed_query
from app.vectorstore import SearchResult, get_store


def retrieve(question: str, top_k: int) -> list[SearchResult]:
    embedding = embed_query(question)
    return get_store().search(embedding, top_k)
