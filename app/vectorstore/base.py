from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ChunkRecord:
    """埋め込み投入用の 1 チャンク。

    service/doc/source_url を各レコードに持たせて非正規化しておくことで、
    pgvector (documents テーブルとの結合) と S3 Vectors (メタデータのみ) の
    どちらでも同じデータ構造で完結する。
    """

    service: str
    doc: str
    source_url: str
    section: str | None
    page_start: int | None
    page_end: int | None
    content: str
    content_hash: str
    embedding: list[float]


@dataclass
class SearchResult:
    id: int | str
    section: str | None
    content: str
    page_start: int | None
    service: str
    doc: str
    source_url: str
    score: float


class VectorStore(Protocol):
    """ベクトルストアの差し替え可能な抽象化。

    pgvector (ローカル/CI, [pgvector_store.py](pgvector_store.py)) と
    S3 Vectors (AWS Phase 2, [s3vectors_store.py](s3vectors_store.py)) を
    同一インターフェースで扱う。
    """

    def open(self) -> None:
        """接続の初期化 (pgvector はコネクションプール確立、S3 Vectors は no-op)。"""
        ...

    def close(self) -> None: ...

    def ping(self) -> bool: ...

    def register_document(self, service: str, doc: str, source_url: str, content_hash: str) -> None:
        """文書レベルのメタデータを記録する (再取り込み判定・出典管理用)。"""
        ...

    def existing_hashes(self, service: str, doc: str) -> set[str]:
        """投入済みチャンクの content_hash 集合。

        埋め込み API の再課金を避けるための事前フィルタとして使う。
        """
        ...

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> int:
        """チャンクを冪等に投入する (content_hash が既存ならスキップ)。挿入件数を返す。"""
        ...

    def search(
        self,
        embedding: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]: ...
