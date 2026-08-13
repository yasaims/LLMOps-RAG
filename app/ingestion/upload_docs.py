"""取り込み元 PDF を docs S3 バケットへアップロードする。

`data/raw/*.pdf` は方針上リポジトリにコミットしない
([download_docs.py](download_docs.py) 参照) ため、Terraform で作成した
docs バケットに実行時アップロードしておくことで、「どのバージョンの PDF から
何を取り込んだか」を後から追跡できるようにする (出典・再現性の証跡)。
"""

from __future__ import annotations

import argparse

import boto3

from app.config import get_settings
from app.ingestion.download_docs import DATA_DIR, SOURCES


def upload(doc_key: str) -> None:
    source = SOURCES[doc_key]
    settings = get_settings()
    if not settings.docs_bucket:
        raise SystemExit("DOCS_BUCKET が未設定です (.env を確認してください)")

    pdf_path = DATA_DIR / f"{source.doc}.pdf"
    meta_path = DATA_DIR / f"{source.doc}.meta.json"
    if not pdf_path.exists() or not meta_path.exists():
        raise SystemExit(
            f"{pdf_path} が見つかりません。先に以下を実行してください:\n"
            f"  uv run python -m app.ingestion.download_docs --doc {doc_key}"
        )

    s3 = boto3.client("s3", region_name=settings.aws_region)
    prefix = f"{source.service}/{source.doc}"
    for path, content_type in (
        (pdf_path, "application/pdf"),
        (meta_path, "application/json"),
    ):
        key = f"{prefix}/{path.name}"
        print(f"[{doc_key}] s3://{settings.docs_bucket}/{key} へアップロード中...")
        s3.upload_file(
            str(path), settings.docs_bucket, key, ExtraArgs={"ContentType": content_type}
        )
    print(f"[{doc_key}] アップロード完了")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="取り込み元 PDF を docs バケットへアップロードする"
    )
    parser.add_argument("--doc", choices=sorted(SOURCES.keys()), default=None, help="省略時は全件")
    args = parser.parse_args()
    targets = [args.doc] if args.doc else list(SOURCES.keys())
    for doc_key in targets:
        upload(doc_key)


if __name__ == "__main__":
    main()
