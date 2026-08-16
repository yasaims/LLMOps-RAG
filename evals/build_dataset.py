"""評価用 QA データセットのドラフトを生成する CLI (ローカル実行専用)。

`data/raw/<doc>.pdf` (download_docs.py で事前取得が必要) を app/ingestion/parse.py +
app/ingestion/chunk.py で直接 parse/chunk し (VectorStore プロトコルには一切触れない)、
再現可能にサンプリングしたチャンクごとに Bedrock で日本語の質問+参照回答を生成する。

出力はあくまでドラフト。`--review-out` が出す Markdown を見ながら人手でレビューし、
良いものだけを `reviewed: true` にして `evals/datasets/<doc>-qa.jsonl` として確定させる
(ADR 0007)。チャンク本文はレビュー用 Markdown にのみ書き、確定 JSONL には含めない
(AWS ドキュメント原文を非コミット方針に保つため)。
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.bedrock import converse
from app.ingestion.chunk import Chunk, chunk_pages
from app.ingestion.download_docs import SOURCES
from app.ingestion.parse import extract_pages

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DEFAULT_GEN_MODEL = "jp.anthropic.claude-sonnet-4-5-20250929-v1:0"

# 目次断片・付録等、質問生成の対象として不適切な見出しのフィルタ (完全一致ではなく部分一致)。
_SECTION_DENYLIST_SUBSTRINGS = [
    "document history",
    "table of contents",
    "revision history",
    "glossary",
]

GEN_SYSTEM_PROMPT = """\
あなたは AWS 認定試験 (Machine Learning - Associate 等) の受験者向けに問題を作成する試験官です。
与えられた英語のドキュメント抜粋のみを根拠に、日本語の質問と参照回答を1組作成してください。

制約:
1. 抜粋を読まなければ答えられない具体的な質問にすること。
2. 「この節では」「上記の」など抜粋の文脈に依存する指示語を使わず、質問単体で意味が通ること。
3. 参照回答は、質問が直接聞いていることだけに答えること (質問のスコープを超えて抜粋の
   内容を要約しないこと)。日本語で1〜2文、120字程度を目安に簡潔にまとめ、
   抜粋に書かれていない事実を付け加えないこと。値を一意に特定する質問 (Model ID・数値・
   API 名など) の場合は、その値を省略せずそのまま書くこと。
4. 抜粋が目次の断片・表の残骸などで、意味のある文章になっていない場合は
   answerable を false にすること。

必ず以下の JSON 形式のみで出力してください (前後に説明や ```json フェンスを付けない):
{"answerable": true, "question": "...", "reference": "...",
 "topic": "短い英語のトピック名", "reason": ""}
answerable が false の場合は question/reference は空文字、reason に理由を日本語で書いてください。
"""


@dataclass
class Candidate:
    chunk: Chunk
    service: str
    doc: str


def _is_toc(section: str | None) -> bool:
    return bool(section) and section.strip().lower() == "table of contents"


def _looks_like_prose(text: str) -> bool:
    if not text:
        return False
    letters = sum(1 for c in text if c.isalpha())
    return letters / len(text) >= 0.5


def _section_allowed(section: str | None) -> bool:
    if section is None:
        return False
    lowered = section.lower()
    return not any(s in lowered for s in _SECTION_DENYLIST_SUBSTRINGS)


def load_chunks(doc_key: str) -> list[Chunk]:
    """`doc_key` の PDF を parse + chunk する (取り込み時と同じ経路)。

    ⚠️ TOC ページの除外 (`_is_toc`) をここに含めておくこと。質問生成 (`_load_candidates`)
    と参照解答の再生成 (`refresh_references.py`) の両方がこの関数を通ることで、
    双方が同じ `content_hash` を計算することを保証する (evals/refresh_references.py は
    既存 `gold_content_hash` をこの結果から引き直すため、経路がずれると解決できなくなる)。
    """
    source = SOURCES[doc_key]
    pdf_path = DATA_DIR / f"{source.doc}.pdf"
    if not pdf_path.exists():
        raise SystemExit(
            f"{pdf_path} が見つかりません。先に以下を実行してください:\n"
            f"  uv run python -m app.ingestion.download_docs --doc {doc_key}"
        )
    pages = [p for p in extract_pages(pdf_path) if not _is_toc(p.section)]
    return chunk_pages(pages)


def _load_candidates(doc_key: str, min_chars: int, max_chars: int) -> list[Candidate]:
    source = SOURCES[doc_key]
    chunks = load_chunks(doc_key)
    candidates = []
    for c in chunks:
        if not (min_chars <= len(c.content) <= max_chars):
            continue
        if not _section_allowed(c.section):
            continue
        if not _looks_like_prose(c.content):
            continue
        candidates.append(Candidate(chunk=c, service=source.service, doc=source.doc))
    return candidates


def _stratified_sample(candidates: list[Candidate], n: int, seed: int) -> list[Candidate]:
    """トップレベルセクションで層化し、シード固定のラウンドロビンで n 件選ぶ (再現可能)。"""
    # content_hash 昇順で安定ソートしてからグルーピングする — PDF の走査順 (抽出のたびに
    # 変わりうる) に依存させないため。
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for c in sorted(candidates, key=lambda c: c.chunk.content_hash):
        top = (c.chunk.section or "").split(" > ")[0]
        groups[top].append(c)

    rng = random.Random(seed)
    for g in groups.values():
        rng.shuffle(g)

    order = sorted(groups.keys())
    rng.shuffle(order)

    picked: list[Candidate] = []
    idx = dict.fromkeys(groups, 0)
    while len(picked) < n:
        progressed = False
        for g in order:
            if idx[g] < len(groups[g]):
                picked.append(groups[g][idx[g]])
                idx[g] += 1
                progressed = True
                if len(picked) >= n:
                    break
        if not progressed:
            break
    return picked


def _parse_generation_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return json.loads(cleaned)


def _generate_qa(candidate: Candidate, gen_model: str) -> dict | None:
    user_text = f"# ドキュメント抜粋\n{candidate.chunk.content}"
    result = converse(
        system=GEN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        max_tokens=1024,
        model_id=gen_model,
    )
    try:
        return _parse_generation_response(result["text"])
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  [パース失敗] {e}: {result['text'][:200]!r}")
        return None


def run(
    doc_key: str,
    n: int,
    seed: int,
    gen_model: str,
    out_path: Path,
    review_out_path: Path,
    min_chars: int,
    max_chars: int,
    dry_run: bool,
) -> None:
    candidates = _load_candidates(doc_key, min_chars, max_chars)
    print(f"[{doc_key}] 候補チャンク数 (フィルタ後): {len(candidates)}")

    sampled = _stratified_sample(candidates, n, seed)
    print(f"[{doc_key}] サンプリング数: {len(sampled)} (seed={seed})")

    if dry_run:
        print("--dry-run のため Bedrock 呼び出しは行いません。")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    review_out_path.parent.mkdir(parents=True, exist_ok=True)

    records = []
    review_parts = [f"# {doc_key} QA レビュー用ドラフト\n\n生成モデル: {gen_model}\n"]
    n_answerable = 0
    n_skipped_unanswerable = 0
    n_skipped_parse_error = 0

    for i, candidate in enumerate(sampled, start=1):
        c = candidate.chunk
        print(f"  [{i}/{len(sampled)}] {c.section} (p.{c.page_start}-{c.page_end}) を生成中...")
        qa = _generate_qa(candidate, gen_model)
        if qa is None:
            n_skipped_parse_error += 1
            continue
        if not qa.get("answerable", False):
            n_skipped_unanswerable += 1
            print(f"    -> answerable=false: {qa.get('reason', '')}")
            continue

        record_id = f"{candidate.doc}-{n_answerable + 1:03d}"
        record = {
            "id": record_id,
            "question": qa["question"],
            "reference": qa["reference"],
            "gold_content_hash": c.content_hash,
            "gold_section": c.section,
            "gold_page_start": c.page_start,
            "gold_page_end": c.page_end,
            "service": candidate.service,
            "doc": candidate.doc,
            "topic": qa.get("topic", ""),
            "generated_by": gen_model,
            "generated_at": datetime.now(UTC).isoformat(),
            "reviewed": False,
        }
        records.append(record)
        n_answerable += 1

        review_parts.append(
            f"## {record_id} ({c.section}, p.{c.page_start}-{c.page_end})\n\n"
            f"**質問**: {qa['question']}\n\n"
            f"**参照回答**: {qa['reference']}\n\n"
            f"<details><summary>元チャンク本文</summary>\n\n```\n{c.content}\n```\n</details>\n"
        )

    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    review_out_path.write_text("\n".join(review_parts), encoding="utf-8")

    print(
        f"[{doc_key}] 完了: answerable={n_answerable} / "
        f"unanswerable={n_skipped_unanswerable} / パース失敗={n_skipped_parse_error}"
    )
    print(f"ドラフト JSONL: {out_path}")
    print(f"レビュー用 Markdown: {review_out_path}")
    print(
        "レビュー後、良いものを reviewed=true にして "
        f"evals/datasets/{SOURCES[doc_key].doc}-qa.jsonl として確定してください。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="評価用 QA データセットのドラフトを生成する")
    parser.add_argument("--doc", default="bedrock-ug", choices=sorted(SOURCES.keys()))
    parser.add_argument("--n", type=int, default=35, help="サンプリングするチャンク数の目標")
    parser.add_argument("--seed", type=int, default=42, help="サンプリングの乱数シード")
    parser.add_argument("--gen-model", default=DEFAULT_GEN_MODEL)
    parser.add_argument("--min-chars", type=int, default=500)
    parser.add_argument("--max-chars", type=int, default=4000)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--review-out", type=Path, default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="候補件数だけ確認し Bedrock 呼び出しは行わない"
    )
    args = parser.parse_args()

    out_path = args.out or Path(f"evals/datasets/{args.doc}-qa.draft.jsonl")
    review_out_path = args.review_out or Path(f"evals/datasets/_review/{args.doc}-qa.md")

    run(
        doc_key=args.doc,
        n=args.n,
        seed=args.seed,
        gen_model=args.gen_model,
        out_path=out_path,
        review_out_path=review_out_path,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
