"""PR ごとの RAG 品質評価オーケストレーション CLI (eval.yml から呼ばれる)。

`evals/datasets/<doc>-qa.jsonl` の各問について app.rag.retrieve / app.rag.generate を
直接呼び出し (本番 S3 Vectors に対して read-only)、決定的な検索・引用指標と Bedrock judge
による生成指標を算出して baseline と比較する。

終了コード: 0=合格 / 1=品質リグレッション / 2=運用エラー (AWS 例外・データセット欠損等、
品質劣化と誤区別しないため分けている)。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.rag.generate import generate_answer
from app.rag.retrieve import retrieve
from evals.judge import GenerationSample, score_generation
from evals.metrics import (
    RetrievalHit,
    citation_format_valid,
    evaluate_retrieval,
    mean_citation_format_valid,
    mean_reciprocal_rank,
    recall_at_k,
)
from evals.report import (
    build_report,
    dataset_sha256,
    insufficient_judge_coverage,
    load_baseline,
    render_markdown,
)

DEFAULT_JUDGE_MODEL = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_JUDGE_REGION = "ap-northeast-1"

GENERATION_METRIC_KEYS = ("faithfulness", "factual_correctness", "context_recall")

# judge が採点できた問題の割合がこれを下回ったら、品質判定そのものを信用しない。
# ragas は raise_exceptions=False の下でタイムアウトを NaN として返し、平均は NaN を
# 除外して計算されるため、この検査がないと「25 問中 5 問だけの平均」が高スコアを出して
# ゲートを通過してしまう (2026-08 に FactualCorrectness が全問タイムアウトして発覚)。
MIN_JUDGE_COVERAGE = 0.8


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"エラー: データセットが見つかりません: {path}", file=sys.stderr)
        raise SystemExit(2)
    items = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def _run_one(item: dict[str, Any], top_k: int) -> dict[str, Any]:
    chunks = retrieve(item["question"], top_k)
    result = generate_answer(item["question"], chunks)

    hit = evaluate_retrieval(
        retrieved_ids=[str(c.id) for c in chunks],
        retrieved_docs=[c.doc for c in chunks],
        retrieved_sections=[c.section for c in chunks],
        retrieved_page_starts=[c.page_start for c in chunks],
        gold_content_hash=item["gold_content_hash"],
        gold_doc=item["doc"],
        gold_section=item["gold_section"],
        gold_page_start=item["gold_page_start"],
        gold_page_end=item["gold_page_end"],
    )
    citation_valid = citation_format_valid(result.answer, num_sources=len(result.sources))

    return {
        "id": item["id"],
        "question": item["question"],
        "answer": result.answer,
        "contexts": [c.content for c in chunks],
        "reference": item["reference"],
        "hit_rank": hit.hit_rank,
        "matched_by": hit.matched_by,
        "citation_valid": citation_valid,
    }


def run(
    dataset_path: Path,
    baseline_path: Path,
    out_path: Path,
    top_k: int,
    limit: int | None,
    judge_model: str,
    judge_region: str,
    no_judge: bool,
    update_baseline: bool,
) -> int:
    dataset = _load_dataset(dataset_path)
    if limit is not None:
        dataset = dataset[:limit]
    if not dataset:
        print("エラー: データセットが空です", file=sys.stderr)
        return 2

    print(f"{len(dataset)} 問を評価します (top_k={top_k})...")
    try:
        per_question = [_run_one(item, top_k) for item in dataset]
    except Exception as e:  # noqa: BLE001 - 運用エラーとして exit 2 にするため意図的に広く捕捉
        print(f"エラー: retrieve/generate 実行中に例外が発生しました: {e}", file=sys.stderr)
        return 2

    hits = [RetrievalHit(hit_rank=q["hit_rank"], matched_by=q["matched_by"]) for q in per_question]
    metrics: dict[str, float] = {
        "recall@1": recall_at_k(hits, 1),
        "recall@3": recall_at_k(hits, 3),
        "recall@5": recall_at_k(hits, 5),
        "mrr": mean_reciprocal_rank(hits),
        "citation_format_valid": mean_citation_format_valid(
            [q["citation_valid"] for q in per_question]
        ),
    }

    judge_coverage: dict[str, int] | None = None
    if not no_judge:
        try:
            samples = [
                GenerationSample(
                    id=q["id"],
                    question=q["question"],
                    answer=q["answer"],
                    contexts=q["contexts"],
                    reference=q["reference"],
                )
                for q in per_question
            ]
            scores = score_generation(samples, judge_model_id=judge_model, region=judge_region)
        except Exception as e:  # noqa: BLE001
            print(f"エラー: ragas judge 実行中に例外が発生しました: {e}", file=sys.stderr)
            return 2

        scores_by_id = {s.id: s for s in scores}
        for q in per_question:
            s = scores_by_id.get(q["id"])
            q["faithfulness"] = s.faithfulness if s else float("nan")
            q["factual_correctness"] = s.factual_correctness if s else float("nan")
            q["context_recall"] = s.context_recall if s else float("nan")

        def _mean(key: str) -> float:
            values = [q[key] for q in per_question if q.get(key) == q.get(key)]  # NaN 除外
            return sum(values) / len(values) if values else float("nan")

        # NaN を落とした平均は「何問から算出したか」を失う。母数を別途記録しておかないと
        # 一部しか採点できていない実行と全問採点できた実行を区別できない。
        judge_coverage = {
            key: sum(1 for q in per_question if q.get(key) == q.get(key))
            for key in GENERATION_METRIC_KEYS
        }

        metrics["faithfulness"] = _mean("faithfulness")
        metrics["factual_correctness"] = _mean("factual_correctness")
        metrics["context_recall"] = _mean("context_recall")
        metrics["generation_score"] = (
            sum(metrics[k] for k in ("faithfulness", "factual_correctness", "context_recall")) / 3
        )

    current_sha = dataset_sha256(dataset_path)
    baseline = load_baseline(baseline_path)
    report = build_report(
        metrics=metrics,
        baseline=baseline,
        current_dataset_sha256=current_sha,
        n_questions=len(dataset),
        judge_coverage=judge_coverage,
    )

    # ⚠️ ここで件数を絞らないこと。表示上の打ち切りは render_markdown に任せる
    # (そちらは総数と「他 N 問」を必ず出す)。以前はここで [:5] していたため、
    # recall@5 と件数が食い違っていることに気づけなかった。
    worst = [
        {"id": q["id"], "reason": "検索でヒットしませんでした"}
        for q in per_question
        if q["hit_rank"] is None
    ]
    summary_md = render_markdown(report, worst_questions=worst)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "dataset_sha256": current_sha,
                "n_questions": len(dataset),
                "top_k": top_k,
                "metrics": metrics,
                "judge_coverage": judge_coverage,
                "passed": report.passed,
                "per_question": per_question,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_path.parent / "summary.md").write_text(summary_md, encoding="utf-8")

    print()
    print(summary_md)

    step_summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_env:
        step_summary = Path(step_summary_env)
        if step_summary.parent.exists():
            with step_summary.open("a", encoding="utf-8") as f:
                f.write(summary_md + "\n")

    # ⚠️ カバレッジ検査はレポートを書き出した「後」に行う。先に return すると Artifact と
    # PR コメントが生成されず、何問落ちたのかを追う手段がなくなる。
    if judge_coverage:
        insufficient = insufficient_judge_coverage(judge_coverage, len(dataset), MIN_JUDGE_COVERAGE)
        if insufficient:
            detail = ", ".join(f"{k} {n}/{len(dataset)}" for k, n in sorted(insufficient.items()))
            print(
                f"エラー: judge の有効サンプルが下限 {MIN_JUDGE_COVERAGE:.0%} を"
                f"下回りました ({detail})。"
                "残った問題だけの平均で品質を判定すると誤った合格を出すため、"
                "品質リグレッション (exit 1) ではなく運用エラーとして終了します。",
                file=sys.stderr,
            )
            if update_baseline:
                print(
                    "baseline は更新していません (部分的な結果を基準値にしないため)。",
                    file=sys.stderr,
                )
            return 2

    if update_baseline:
        new_baseline = {
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "dataset_sha256": current_sha,
            "metrics": metrics,
            "judge_coverage": judge_coverage,
            "gate": baseline.get("gate") or _default_gate(),
        }
        baseline_path.write_text(
            json.dumps(new_baseline, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nbaseline を更新しました: {baseline_path}")

    return 0 if report.passed else 1


def _default_gate() -> dict[str, dict[str, float]]:
    return {
        "recall@5": {"tolerance": 0.02, "floor": 0.60},
        "mrr": {"tolerance": 0.02, "floor": 0.50},
        "generation_score": {"tolerance": 0.10, "floor": 0.65},
        "citation_format_valid": {"tolerance": 0.02, "floor": 0.90},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 品質評価を実行し baseline と比較する")
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/bedrock-ug-qa.jsonl"))
    parser.add_argument("--baseline", type=Path, default=Path("evals/baseline.json"))
    parser.add_argument("--out", type=Path, default=Path("evals/out/report.json"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="スモークテスト用に問題数を制限")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--judge-region", default=DEFAULT_JUDGE_REGION)
    parser.add_argument(
        "--no-judge", action="store_true", help="ragas judge をスキップ (検索指標のみ)"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="今回のスコアを baseline.json に書き込む (ローカル専用。CI からは使わない)",
    )
    args = parser.parse_args()

    exit_code = run(
        dataset_path=args.dataset,
        baseline_path=args.baseline,
        out_path=args.out,
        top_k=args.top_k,
        limit=args.limit,
        judge_model=args.judge_model,
        judge_region=args.judge_region,
        no_judge=args.no_judge,
        update_baseline=args.update_baseline,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
