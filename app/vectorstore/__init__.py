from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.vectorstore.base import ChunkRecord, SearchResult, VectorStore

__all__ = ["ChunkRecord", "SearchResult", "VectorStore", "get_store"]


@lru_cache
def get_store() -> VectorStore:
    settings = get_settings()
    if settings.vector_store == "s3vectors":
        from app.vectorstore.s3vectors_store import S3VectorsStore

        return S3VectorsStore()
    if settings.vector_store == "pgvector":
        from app.vectorstore.pgvector_store import PgVectorStore

        return PgVectorStore()
    raise ValueError(f"未知の VECTOR_STORE 設定です: {settings.vector_store!r}")
