from __future__ import annotations

from dataclasses import dataclass

from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None


def open_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=5,
            configure=register_vector,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def ping() -> bool:
    with open_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() == (1,)


@dataclass
class ChunkRecord:
    section: str | None
    page_start: int | None
    page_end: int | None
    content: str
    content_hash: str
    embedding: list[float]


@dataclass
class SearchResult:
    id: int
    section: str | None
    content: str
    page_start: int | None
    service: str
    doc: str
    source_url: str
    score: float


def upsert_document(service: str, doc: str, source_url: str, content_hash: str) -> int:
    sql = """
        INSERT INTO documents (service, doc, source_url, content_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (service, doc) DO UPDATE
            SET source_url = EXCLUDED.source_url,
                content_hash = EXCLUDED.content_hash,
                ingested_at = now()
        RETURNING id
    """
    with open_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (service, doc, source_url, content_hash))
        row = cur.fetchone()
        assert row is not None
        return row[0]


def upsert_chunks(document_id: int, chunks: list[ChunkRecord]) -> int:
    sql = """
        INSERT INTO chunks (
            document_id, section, page_start, page_end, content, content_hash, embedding
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (document_id, content_hash) DO NOTHING
    """
    inserted = 0
    with open_pool().connection() as conn, conn.cursor() as cur:
        for chunk in chunks:
            cur.execute(
                sql,
                (
                    document_id,
                    chunk.section,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.content,
                    chunk.content_hash,
                    chunk.embedding,
                ),
            )
            inserted += cur.rowcount
    return inserted


def existing_content_hashes(document_id: int) -> set[str]:
    sql = "SELECT content_hash FROM chunks WHERE document_id = %s"
    with open_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (document_id,))
        return {row[0] for row in cur.fetchall()}


def search(embedding: list[float], top_k: int) -> list[SearchResult]:
    sql = """
        SELECT c.id, c.section, c.content, c.page_start, d.service, d.doc, d.source_url,
               1 - (c.embedding <=> %s::vector) AS score
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
    """
    with open_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (embedding, embedding, top_k))
        return [SearchResult(*row) for row in cur.fetchall()]
