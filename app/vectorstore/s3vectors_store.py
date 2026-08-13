"""S3 Vectors (AWS, Phase 2) 向けの VectorStore 実装。

VPC 不要で Lambda から直接呼び出せるため、アイドル時のコストがほぼ 0 円になる
(ADR 0005)。pgvector 版の `documents` テーブルに相当する正規化はなく、
service/doc/source_url は各ベクトルのメタデータに非正規化して持たせる。

⚠️ filterable metadata は 1 ベクトルあたり 2KB 上限。チャンク本文 (`content`) は
必ず non-filterable metadata に置くこと (Terraform 側 index の
`non_filterable_metadata_keys` と対応させる)。
"""

from __future__ import annotations

from typing import Any

import boto3

from app.config import get_settings
from app.vectorstore.base import ChunkRecord, SearchResult

PUT_BATCH_SIZE = 100
_NO_PAGE = -1  # S3 Vectors のメタデータは null 非対応のため、ページ番号なしはこの値で表現する


class S3VectorsStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_vectors_bucket
        self._index = settings.s3_vectors_index
        self._client = boto3.client("s3vectors", region_name=settings.aws_region)

    def open(self) -> None:
        pass  # boto3 クライアントは遅延接続のため何もしない

    def close(self) -> None:
        pass

    def ping(self) -> bool:
        try:
            self._client.get_index(vectorBucketName=self._bucket, indexName=self._index)
            return True
        except Exception:
            return False

    def register_document(self, service: str, doc: str, source_url: str, content_hash: str) -> None:
        # S3 Vectors には文書テーブルがなく、service/doc/source_url は
        # upsert_chunks 側で各チャンクのメタデータに直接持たせるため何もしない。
        pass

    def existing_hashes(self, service: str, doc: str) -> set[str]:
        """投入済みチャンクの content_hash (= key) 集合。

        S3 Vectors の list_vectors はメタデータでの絞り込みができないため、
        全件を列挙してクライアント側で service/doc を絞り込む。
        """
        hashes: set[str] = set()
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "vectorBucketName": self._bucket,
                "indexName": self._index,
                "returnMetadata": True,
                "returnData": False,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = self._client.list_vectors(**kwargs)
            for v in resp.get("vectors", []):
                meta = v.get("metadata", {})
                if meta.get("service") == service and meta.get("doc") == doc:
                    hashes.add(v["key"])
            next_token = resp.get("nextToken")
            if not next_token:
                break
        return hashes

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> int:
        inserted = 0
        for i in range(0, len(chunks), PUT_BATCH_SIZE):
            batch = chunks[i : i + PUT_BATCH_SIZE]
            vectors = [
                {
                    "key": c.content_hash,
                    "data": {"float32": c.embedding},
                    "metadata": {
                        "service": c.service,
                        "doc": c.doc,
                        "source_url": c.source_url,
                        "section": c.section or "",
                        "page_start": c.page_start if c.page_start is not None else _NO_PAGE,
                        "page_end": c.page_end if c.page_end is not None else _NO_PAGE,
                        "content": c.content,
                    },
                }
                for c in batch
            ]
            self._client.put_vectors(
                vectorBucketName=self._bucket, indexName=self._index, vectors=vectors
            )
            inserted += len(vectors)
        return inserted

    def search(
        self,
        embedding: list[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        kwargs: dict[str, Any] = {
            "vectorBucketName": self._bucket,
            "indexName": self._index,
            "topK": top_k,
            "queryVector": {"float32": embedding},
            "returnMetadata": True,
            "returnDistance": True,
        }
        if metadata_filter:
            kwargs["filter"] = metadata_filter
        resp = self._client.query_vectors(**kwargs)

        results = []
        for v in resp.get("vectors", []):
            meta = v.get("metadata", {})
            page_start = meta.get("page_start")
            # cosine distance (S3 Vectors) は pgvector の `<=>` と同じ
            # 「1 - コサイン類似度」の定義なので、score の計算式を揃えられる
            score = 1 - v.get("distance", 0.0)
            results.append(
                SearchResult(
                    id=v["key"],
                    section=meta.get("section") or None,
                    content=meta.get("content", ""),
                    page_start=None if page_start in (None, _NO_PAGE) else page_start,
                    service=meta.get("service", ""),
                    doc=meta.get("doc", ""),
                    source_url=meta.get("source_url", ""),
                    score=score,
                )
            )
        return results
