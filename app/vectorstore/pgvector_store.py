"""pgvector (ローカル docker compose / CI) 向けの VectorStore 実装。

SQL 本体は [app/db.py](../db.py) に集約したまま変更せず、ここでは
`VectorStore` プロトコルへの適合 (document_id の解決など) のみ行う。
"""

from __future__ import annotations

from typing import Any

from app import db
from app.vectorstore.base import ChunkRecord, SearchResult


class PgVectorStore:
    def open(self) -> None:
        db.open_pool()

    def close(self) -> None:
        db.close_pool()

    def ping(self) -> bool:
        return db.ping()

    def register_document(self, service: str, doc: str, source_url: str, content_hash: str) -> None:
        db.upsert_document(service, doc, source_url, content_hash)

    def fetch_chunks(self, service: str, doc: str) -> list[ChunkRecord]:
        """指定文書の全チャンクを埋め込み込みで取得する。

        S3 Vectors への移送用の補助メソッドで、`VectorStore` プロトコルには含めない。
        """
        return db.fetch_chunks(service, doc)

    def existing_hashes(self, service: str, doc: str) -> set[str]:
        document_id = db.get_document_id(service, doc)
        if document_id is None:
            return set()
        return db.existing_content_hashes(document_id)

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> int:
        if not chunks:
            return 0
        service, doc = chunks[0].service, chunks[0].doc
        document_id = db.get_document_id(service, doc)
        if document_id is None:
            raise RuntimeError(
                f"documents レコードが見つかりません ({service}/{doc})。"
                " 先に register_document を呼んでください。"
            )
        return db.upsert_chunks(document_id, chunks)

    def search(
        self,
        embedding: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if metadata_filter:
            raise NotImplementedError("pgvector バックエンドでの metadata_filter は未実装です")
        return db.search(embedding, top_k)
