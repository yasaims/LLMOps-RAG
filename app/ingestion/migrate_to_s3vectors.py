"""ローカル pgvector に投入済みの埋め込みを、そのまま S3 Vectors へ移送する。

Phase 1 で Bedrock 埋め込み API に課金して作成済みのベクトルを再利用し、
Phase 2 移行時に同じ費用をもう一度払わないためのワンショットスクリプト。
ローカル DB が空 (未投入) の場合は、embed API を呼び出す通常の取り込み
([ingest.py](ingest.py) の `run()`) に自動でフォールバックする。
"""

from __future__ import annotations

import argparse

from app.ingestion.download_docs import SOURCES
from app.ingestion.ingest import run
from app.vectorstore.pgvector_store import PgVectorStore
from app.vectorstore.s3vectors_store import S3VectorsStore


def migrate(doc_key: str, max_pages: int | None) -> None:
    source = SOURCES[doc_key]
    pg_store = PgVectorStore()
    chunks = pg_store.fetch_chunks(source.service, source.doc)
    pg_store.close()

    if not chunks:
        print(
            f"[{doc_key}] ローカル pgvector にデータがありません。"
            " embed API を呼び出す通常の取り込みにフォールバックします。"
        )
        s3_store = S3VectorsStore()
        try:
            run(doc_key, dry_run=False, max_pages=max_pages, store=s3_store)
        finally:
            s3_store.close()
        return

    print(f"[{doc_key}] pgvector から {len(chunks)} 件のチャンクを読み出しました")
    s3_store = S3VectorsStore()
    already = s3_store.existing_hashes(source.service, source.doc)
    new_chunks = [c for c in chunks if c.content_hash not in already]
    print(
        f"[{doc_key}] 新規投入: {len(new_chunks)} 件 "
        f"(投入済み {len(chunks) - len(new_chunks)} 件をスキップ)"
    )
    inserted = s3_store.upsert_chunks(new_chunks)
    print(f"[{doc_key}] 完了: {inserted} 件を S3 Vectors に投入しました (embed API 再課金なし)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="pgvector に投入済みの埋め込みを S3 Vectors へ移送する (embed API 再課金を回避)"
    )
    parser.add_argument("--doc", required=True, choices=sorted(SOURCES.keys()))
    parser.add_argument(
        "--max-pages", type=int, default=None, help="フォールバック時の開発用ページ数制限"
    )
    args = parser.parse_args()
    migrate(args.doc, args.max_pages)


if __name__ == "__main__":
    main()
