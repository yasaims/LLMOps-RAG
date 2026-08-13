"""PDF をパース・チャンク分割・埋め込み・DB 投入する CLI。

再実行しても `content_hash` の UNIQUE 制約 (DB) と事前フィルタにより、
既に投入済みのチャンクは埋め込み API を再度叩かずスキップする。
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

from app.bedrock import embed_documents
from app.ingestion.chunk import chunk_pages
from app.ingestion.download_docs import SOURCES
from app.ingestion.parse import PageText, extract_pages
from app.vectorstore import ChunkRecord, get_store
from app.vectorstore.base import VectorStore

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"
EMBED_BATCH_SIZE = 96
# 英語テキストの概算換算 (目安であり正確なトークナイズ結果ではない)
CHARS_PER_TOKEN_ESTIMATE = 4


def _is_toc(section: str | None) -> bool:
    return bool(section) and section.strip().lower() == "table of contents"


def _load_pages(pdf_path: Path, max_pages: int | None) -> list[PageText]:
    pages_iter = (p for p in extract_pages(pdf_path) if not _is_toc(p.section))
    if max_pages is not None:
        pages_iter = itertools.islice(pages_iter, max_pages)
    return list(pages_iter)


def _resolve_store(store_name: str | None) -> VectorStore:
    """--store が指定されていれば設定を無視してそのバックエンドを使う。

    省略時は VECTOR_STORE 設定に従う。
    """
    if store_name is None:
        return get_store()
    if store_name == "pgvector":
        from app.vectorstore.pgvector_store import PgVectorStore

        return PgVectorStore()
    if store_name == "s3vectors":
        from app.vectorstore.s3vectors_store import S3VectorsStore

        return S3VectorsStore()
    raise SystemExit(f"未知の --store 値です: {store_name}")


def run(doc_key: str, dry_run: bool, max_pages: int | None, store: VectorStore) -> None:
    source = SOURCES[doc_key]
    pdf_path = DATA_DIR / f"{source.doc}.pdf"
    meta_path = DATA_DIR / f"{source.doc}.meta.json"
    if not pdf_path.exists() or not meta_path.exists():
        raise SystemExit(
            f"{pdf_path} が見つかりません。先に以下を実行してください:\n"
            f"  uv run python -m app.ingestion.download_docs --doc {doc_key}"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    print(f"[{doc_key}] ページ抽出中...")
    pages = _load_pages(pdf_path, max_pages)
    print(f"[{doc_key}] 抽出ページ数: {len(pages)} (Table of Contents は除外)")

    print(f"[{doc_key}] チャンク分割中...")
    chunks = chunk_pages(pages)
    total_chars = sum(len(c.content) for c in chunks)
    est_tokens = total_chars // CHARS_PER_TOKEN_ESTIMATE
    print(
        f"[{doc_key}] チャンク数: {len(chunks)} / 総文字数: {total_chars:,} "
        f"/ 概算入力トークン数: {est_tokens:,} (目安。正確な料金は AWS の Bedrock 料金ページを参照)"
    )

    if dry_run:
        print("--dry-run のため embed API 呼び出しと DB 投入は行いません。")
        return

    store.register_document(
        service=source.service,
        doc=source.doc,
        source_url=source.url,
        content_hash=meta["sha256"],
    )
    already = store.existing_hashes(source.service, source.doc)
    new_chunks = [c for c in chunks if c.content_hash not in already]
    skipped = len(chunks) - len(new_chunks)
    print(f"[{doc_key}] 新規チャンク数: {len(new_chunks)} (投入済み {skipped} 件をスキップ)")

    inserted_total = 0
    for i in range(0, len(new_chunks), EMBED_BATCH_SIZE):
        batch = new_chunks[i : i + EMBED_BATCH_SIZE]
        embeddings = embed_documents([c.content for c in batch])
        records = [
            ChunkRecord(
                service=source.service,
                doc=source.doc,
                source_url=source.url,
                section=c.section,
                page_start=c.page_start,
                page_end=c.page_end,
                content=c.content,
                content_hash=c.content_hash,
                embedding=emb,
            )
            for c, emb in zip(batch, embeddings, strict=True)
        ]
        inserted = store.upsert_chunks(records)
        inserted_total += inserted
        done = min(i + EMBED_BATCH_SIZE, len(new_chunks))
        print(f"[{doc_key}] {done}/{len(new_chunks)} 件処理 (挿入 {inserted} 件)")

    print(f"[{doc_key}] 完了: {inserted_total} 件のチャンクを新規投入しました")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF をパース・チャンク分割・埋め込み投入する")
    parser.add_argument("--doc", required=True, choices=sorted(SOURCES.keys()))
    parser.add_argument(
        "--dry-run", action="store_true", help="規模だけ確認し API 呼び出しは行わない"
    )
    parser.add_argument(
        "--max-pages", type=int, default=None, help="開発中の高速反復用にページ数を制限"
    )
    parser.add_argument(
        "--store",
        choices=["pgvector", "s3vectors"],
        default=None,
        help="投入先を明示指定 (省略時は VECTOR_STORE 環境変数の設定に従う)",
    )
    args = parser.parse_args()
    store = _resolve_store(args.store)
    try:
        run(args.doc, args.dry_run, args.max_pages, store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
