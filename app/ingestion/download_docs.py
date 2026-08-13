"""AWS 公式ドキュメント (PDF) をダウンロードするスクリプト。

旧 `awsdocs` GitHub リポジトリは 2023 年にアーカイブ済みのため、
AWS 公式ページの PDF リンクから直接取得する。ドキュメント原文は
リポジトリにコミットせず、`data/raw/` (gitignore 済み) に保存する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"


@dataclass
class DocSource:
    service: str
    doc: str
    url: str


SOURCES: dict[str, DocSource] = {
    "bedrock-ug": DocSource(
        service="bedrock",
        doc="bedrock-ug",
        url="https://docs.aws.amazon.com/pdfs/bedrock/latest/userguide/bedrock-ug.pdf",
    ),
    # SageMaker Developer Guide は Phase 2 以降で追加する
    # "sagemaker-dg": DocSource(
    #     service="sagemaker",
    #     doc="sagemaker-dg",
    #     url="https://docs.aws.amazon.com/pdfs/sagemaker/latest/dg/sagemaker-dg.pdf",
    # ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(source: DocSource, dest_dir: Path = DATA_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = dest_dir / f"{source.doc}.pdf"
    meta_path = dest_dir / f"{source.doc}.meta.json"

    with httpx.stream("GET", source.url, follow_redirects=True, timeout=60.0) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        tmp_path = pdf_path.with_suffix(".pdf.tmp")
        with tmp_path.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    mb, total_mb = downloaded / 1e6, total / 1e6
                    print(f"\r{source.doc}: {mb:.1f} / {total_mb:.1f} MB ({pct:.0f}%)", end="")
        print()

    new_hash = _sha256(tmp_path)
    if pdf_path.exists() and meta_path.exists():
        existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if existing_meta.get("sha256") == new_hash:
            tmp_path.unlink()
            print(f"{source.doc}: 既存ファイルと同一のため取得をスキップ")
            return pdf_path

    tmp_path.replace(pdf_path)
    meta = {
        "service": source.service,
        "doc": source.doc,
        "source_url": source.url,
        "sha256": new_hash,
        "downloaded_at": datetime.now(UTC).isoformat(),
        "size_bytes": pdf_path.stat().st_size,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{source.doc}: ダウンロード完了 ({pdf_path})")
    return pdf_path


def main() -> None:
    parser = argparse.ArgumentParser(description="AWS 公式ドキュメント PDF をダウンロードする")
    parser.add_argument(
        "--doc",
        choices=sorted(SOURCES.keys()),
        default=None,
        help="取得するドキュメント (省略時は全件)",
    )
    args = parser.parse_args()

    targets = [SOURCES[args.doc]] if args.doc else list(SOURCES.values())
    for source in targets:
        download(source)


if __name__ == "__main__":
    main()
