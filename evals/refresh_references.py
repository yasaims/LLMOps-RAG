"""確定済み QA データセットの `reference` (参照解答) だけを作り直す CLI (ローカル実行専用)。

issue #7: `evals/build_dataset.py` が生成する参照解答は gold チャンクの包括的な要約に
なっており、質問が聞いている範囲より広い。ragas の `FactualCorrectness` (既定 mode="f1")
は参照解答にしかない事実を未言及として減点するため、質問に完全に答えていても
`factual_correctness` が構造的に低く出る。

`question` / `gold_content_hash` / `gold_section` / `gold_page_start` / `gold_page_end` は
一切変更しない (検索指標 recall@k / MRR はこれらだけで決まるため、このスクリプトの前後で
変化しないはずというのが再生成の前提)。変えるのは `reference` のみ。

`evals/measure_cost.py` と同じ「ローカル専用ワンショットツール」の位置づけ。CI からは
呼ばれない。出力はあくまでドラフトで、`evals/build_dataset.py` 同様に人手レビューを経てから
確定 JSONL (`evals/datasets/<doc>-qa.jsonl`) に反映する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.bedrock import converse
from evals.build_dataset import _parse_generation_response, load_chunks

DEFAULT_GEN_MODEL = "jp.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_DATASET = Path("evals/datasets/bedrock-ug-qa.jsonl")
DEFAULT_OUT = Path("evals/datasets/bedrock-ug-qa.draft.jsonl")
DEFAULT_REVIEW_OUT = Path("evals/datasets/_review/bedrock-ug-qa-references.md")

REFERENCE_SYSTEM_PROMPT = """\
あなたは RAG システムの評価用データセットを整備する担当者です。
与えられた英語のドキュメント抜粋と日本語の質問に対して、「その質問の参照解答」を作成してください。

制約:
1. 質問が直接聞いていることだけに答える。抜粋に書かれていても質問が聞いていない事実は
   書かない (最も重要)。
2. 抜粋に書かれていない事実を付け加えない。
3. 質問が値を一意に特定するもの (Model ID・数値・API 名など) を聞いている場合は、
   その値を省略せずそのまま書く。
4. 日本語で1〜2文、120字程度を目安に簡潔にまとめる。背景説明・補足・言い換えは書かない。
5. 抜粋だけでは質問に答えられない場合は answerable を false にする。

必ず以下の JSON 形式のみで出力してください (前後に説明や ```json フェンスを付けない):
{"answerable": true, "reference": "...", "reason": ""}
answerable が false の場合は reference は空文字、reason に理由を日本語で書いてください。
"""


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _build_hash_index(doc_keys: set[str]) -> dict[str, Any]:
    """content_hash -> Chunk。複数 doc にまたがるデータセットにも対応する。"""
    index: dict[str, Any] = {}
    for doc_key in doc_keys:
        for chunk in load_chunks(doc_key):
            index[chunk.content_hash] = chunk
    return index


def _generate_reference(question: str, chunk_content: str, gen_model: str) -> dict | None:
    user_text = f"# ドキュメント抜粋\n{chunk_content}\n\n# 質問\n{question}"
    result = converse(
        system=REFERENCE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        max_tokens=512,
        model_id=gen_model,
    )
    try:
        return _parse_generation_response(result["text"])
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  [パース失敗] {e}: {result['text'][:200]!r}")
        return None


def run(
    dataset_path: Path,
    out_path: Path,
    review_out_path: Path,
    gen_model: str,
    only_ids: set[str] | None,
    dry_run: bool,
) -> int:
    records = _load_dataset(dataset_path)
    doc_keys = {r["doc"] for r in records}
    hash_index = _build_hash_index(doc_keys)

    # ⚠️ 解決できない gold_content_hash が1件でもあれば、生成に入る前に一覧を出して
    # exit 2 する。PDF の差し替えや chunk.py の window/overlap 変更を黙って通さないため
    # (このスクリプトの前提「gold_* は不変」自体が崩れている状態)。
    unresolved = [r["id"] for r in records if r["gold_content_hash"] not in hash_index]
    if unresolved:
        print(
            "エラー: 以下の id の gold_content_hash がチャンク再計算結果に見つかりません "
            "(PDF の差し替え or chunk.py の window/overlap 変更の可能性):",
            file=sys.stderr,
        )
        for rid in unresolved:
            print(f"  - {rid}", file=sys.stderr)
        return 2

    targets = [r for r in records if only_ids is None or r["id"] in only_ids]
    print(f"対象: {len(targets)} / {len(records)} 問 (dataset={dataset_path})")

    if dry_run:
        print("--dry-run のためハッシュ解決の確認のみ行いました。Bedrock 呼び出しは行いません。")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    review_out_path.parent.mkdir(parents=True, exist_ok=True)

    review_parts = [f"# 参照解答リフレッシュ レビュー用ドラフト\n\n生成モデル: {gen_model}\n"]
    n_updated = 0
    n_unanswerable = 0
    n_parse_error = 0

    new_records: list[dict[str, Any]] = []
    for i, record in enumerate(records, start=1):
        if only_ids is not None and record["id"] not in only_ids:
            new_records.append(record)
            continue

        chunk = hash_index[record["gold_content_hash"]]
        print(f"  [{i}/{len(records)}] {record['id']} を再生成中...")
        qa = _generate_reference(record["question"], chunk.content, gen_model)

        new_record = dict(record)
        new_record["reviewed"] = False  # 人手レビューを必ず通させるため false に戻す

        if qa is None:
            n_parse_error += 1
            new_records.append(new_record)
            review_parts.append(
                f"## {record['id']} ⚠️ パース失敗\n\n"
                f"**質問**: {record['question']}\n\n"
                f"**旧参照解答 (変更なし)**: {record['reference']}\n"
            )
            continue

        if not qa.get("answerable", False):
            n_unanswerable += 1
            new_records.append(new_record)
            review_parts.append(
                f"## {record['id']} ⚠️ answerable=false ({qa.get('reason', '')})\n\n"
                f"**質問**: {record['question']}\n\n"
                f"**旧参照解答 (変更なし)**: {record['reference']}\n"
            )
            continue

        old_reference = record["reference"]
        new_record["reference"] = qa["reference"]
        new_records.append(new_record)
        n_updated += 1

        review_parts.append(
            f"## {record['id']} ({chunk.section}, p.{chunk.page_start}-{chunk.page_end})\n\n"
            f"**質問**: {record['question']}\n\n"
            f"**旧参照解答**: {old_reference}\n\n"
            f"**新参照解答**: {qa['reference']}\n\n"
            f"<details><summary>元チャンク本文</summary>\n\n```\n{chunk.content}\n```\n</details>\n"
        )

    with out_path.open("w", encoding="utf-8") as f:
        for record in new_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    review_out_path.write_text("\n".join(review_parts), encoding="utf-8")

    print(
        f"完了: 更新={n_updated} / answerable=false={n_unanswerable} / "
        f"パース失敗={n_parse_error} (対象外はそのままコピー)"
    )
    print(f"ドラフト JSONL: {out_path}")
    print(f"レビュー用 Markdown: {review_out_path}")
    print(
        f"レビュー後、良いものを reviewed=true にして {dataset_path} に反映してください "
        "(新参照解答が旧より悪い問題は旧のまま残してよい)。"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="確定済み QA データセットの参照解答 (reference) だけを作り直す"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--review-out", type=Path, default=DEFAULT_REVIEW_OUT)
    parser.add_argument("--gen-model", default=DEFAULT_GEN_MODEL)
    parser.add_argument(
        "--only", default=None, help="カンマ区切りの id リスト (パイロット実行用。省略時は全件)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="gold_content_hash の解決確認のみ行い Bedrock 呼び出しは行わない",
    )
    args = parser.parse_args()

    only_ids = set(args.only.split(",")) if args.only else None
    exit_code = run(
        dataset_path=args.dataset,
        out_path=args.out,
        review_out_path=args.review_out,
        gen_model=args.gen_model,
        only_ids=only_ids,
        dry_run=args.dry_run,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
