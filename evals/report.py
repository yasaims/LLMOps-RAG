"""baseline との比較・合否判定・Markdown レポート生成。

このモジュールも evals/metrics.py 同様 ragas/AWS に依存しない (evals/judge.py だけが
ragas を import する)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GateResult:
    metric: str
    current: float
    baseline: float | None
    tolerance: float
    floor: float | None
    passed: bool
    reason: str


@dataclass
class EvalReport:
    metrics: dict[str, float]
    gate_results: list[GateResult]
    dataset_sha256: str
    baseline_dataset_sha256: str | None
    n_questions: int
    passed: bool
    # 生成指標ごとの「judge が実際に採点できた問題数」。ragas は raise_exceptions=False の
    # 下でタイムアウトしたサンプルを NaN にし、run_eval 側の平均は NaN を除外するため、
    # これがないと「25 問中 5 問だけの平均」が満点近くでゲートを通過してしまう。
    judge_coverage: dict[str, int] | None = None

    @property
    def incomplete_metrics(self) -> dict[str, int]:
        """全問を採点しきれなかった生成指標 -> 有効サンプル数。"""
        if not self.judge_coverage:
            return {}
        return {k: v for k, v in self.judge_coverage.items() if v < self.n_questions}

    @property
    def dataset_changed(self) -> bool:
        return (
            self.baseline_dataset_sha256 is not None
            and self.baseline_dataset_sha256 != self.dataset_sha256
        )


def dataset_sha256(dataset_path: Path) -> str:
    """データセット「内容」の SHA-256。

    ⚠️ 生バイトではなく、改行を LF に正規化してからハッシュする。このリポジトリは
    `core.autocrlf=true` のため、同じファイルでもチェックアウト環境でバイト列が変わる:

        Windows ワーキングツリー (CRLF) : 3ec628d5...
        Linux / GitHub Actions   (LF)  : b2f83681...

    baseline の `dataset_sha256` は Windows でローカル実行した `--update-baseline` が
    書き込む一方、CI は Linux で計算するため、生バイトだと**必ず**食い違う。その結果
    `evaluate_gate()` の `dataset_changed` が常に True になり、baseline 比較が無効化されて
    floor のみの判定に落ちていた (tolerance 判定が CI で一度も作動していなかった)。

    `.gitattributes` の `*.jsonl text eol=lf` でチェックアウト側も固定しているが、
    そちらは各自の git 設定に依存しうるので、ハッシュ側でも正規化して二重に守る。
    """
    normalized = dataset_path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"metrics": {}, "gate": {}, "dataset_sha256": None}
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_gate(
    metrics: dict[str, float],
    baseline: dict[str, Any],
    current_dataset_sha256: str,
) -> list[GateResult]:
    """baseline["gate"] に定義された各メトリクスについて合否判定する。

    ゲート対象外のメトリクス (baseline["gate"] に載っていないもの) は判定しない
    (report.render_markdown 側で「参考指標」として表示するのみ)。
    """
    gate_cfg: dict[str, dict[str, float]] = baseline.get("gate", {})
    baseline_metrics: dict[str, float] = baseline.get("metrics", {})
    dataset_changed = (
        baseline.get("dataset_sha256") is not None
        and baseline.get("dataset_sha256") != current_dataset_sha256
    )

    results: list[GateResult] = []
    for name, cfg in gate_cfg.items():
        current = metrics.get(name)
        tolerance = cfg.get("tolerance", 0.0)
        floor = cfg.get("floor")

        if current is None:
            results.append(
                GateResult(
                    metric=name,
                    current=float("nan"),
                    baseline=baseline_metrics.get(name),
                    tolerance=tolerance,
                    floor=floor,
                    passed=False,
                    reason="今回の実行結果にこの指標がありません (運用エラーの可能性)",
                )
            )
            continue

        base_value = baseline_metrics.get(name)
        floor_ok = floor is None or current >= floor

        if dataset_changed or base_value is None:
            passed = floor_ok
            reason = (
                "データセットが変更されたため baseline 比較を無効化し floor のみで判定"
                if dataset_changed
                else "baseline に前回値がないため floor のみで判定"
            )
            if not floor_ok:
                reason = f"floor {floor} を下回りました ({reason})"
            elif floor is None:
                reason = "baseline 未設定・floor 未設定のため無条件合格 (要 baseline 初期化)"
        else:
            tolerance_ok = current >= base_value - tolerance
            passed = tolerance_ok and floor_ok
            if not tolerance_ok:
                reason = f"baseline比 {current - base_value:+.3f} (許容 -{tolerance:.3f})"
            elif not floor_ok:
                reason = f"floor {floor} を下回りました"
            else:
                reason = "OK"

        results.append(
            GateResult(
                metric=name,
                current=current,
                baseline=base_value,
                tolerance=tolerance,
                floor=floor,
                passed=passed,
                reason=reason,
            )
        )
    return results


def insufficient_judge_coverage(
    judge_coverage: dict[str, int] | None,
    n_questions: int,
    min_coverage: float,
) -> dict[str, int]:
    """下限を満たさない生成指標 -> 有効サンプル数。空 dict なら検査通過。

    judge を回していない (--no-judge) 場合は judge_coverage が None になり、検査対象外。
    n_questions が 0 以下なら割合を計算できないので、全指標を不足扱いにする
    (「0 問中 0 問採点できたので 100%」と読ませないため)。
    """
    if not judge_coverage:
        return {}
    if n_questions <= 0:
        return dict(judge_coverage)
    return {k: v for k, v in judge_coverage.items() if v / n_questions < min_coverage}


def build_report(
    metrics: dict[str, float],
    baseline: dict[str, Any],
    current_dataset_sha256: str,
    n_questions: int,
    judge_coverage: dict[str, int] | None = None,
) -> EvalReport:
    gate_results = evaluate_gate(metrics, baseline, current_dataset_sha256)
    passed = all(g.passed for g in gate_results)
    return EvalReport(
        metrics=metrics,
        gate_results=gate_results,
        dataset_sha256=current_dataset_sha256,
        baseline_dataset_sha256=baseline.get("dataset_sha256"),
        n_questions=n_questions,
        passed=passed,
        judge_coverage=judge_coverage,
    )


def render_markdown(report: EvalReport, worst_questions: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "### RAG 品質評価",
        "",
        f"- 問題数: {report.n_questions}",
        f"- 判定: {'✅ 合格' if report.passed else '❌ 不合格'}",
    ]
    if report.judge_coverage:
        parts = []
        for name, n_valid in sorted(report.judge_coverage.items()):
            mark = "" if n_valid >= report.n_questions else " ⚠️"
            parts.append(f"{name} {n_valid}/{report.n_questions}{mark}")
        lines.append(f"- judge 有効サンプル: {', '.join(parts)}")
    if report.incomplete_metrics:
        lines.append(
            "- ⚠️ judge が一部の問題を採点できていません (タイムアウト等)。"
            "下記スコアは**採点できた問題だけの平均**であり、母数が異なるため "
            "baseline との比較は意味を持ちません"
        )
    if report.dataset_changed:
        lines.append(
            "- ⚠️ データセットが baseline から変更されているため、"
            "baseline 比較は無効化されています (floor のみで判定)"
        )
    lines += [
        "",
        "| メトリクス | 今回 | baseline | 差分 | 許容 | floor | 判定 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for g in report.gate_results:
        base_str = f"{g.baseline:.3f}" if g.baseline is not None else "—"
        diff_str = f"{g.current - g.baseline:+.3f}" if g.baseline is not None else "—"
        floor_str = f"{g.floor:.3f}" if g.floor is not None else "—"
        mark = "✅" if g.passed else "❌"
        lines.append(
            f"| {g.metric} | {g.current:.3f} | {base_str} | {diff_str} | "
            f"-{g.tolerance:.3f} | {floor_str} | {mark} {g.reason} |"
        )

    gated = {g.metric for g in report.gate_results}
    reference_metrics = {k: v for k, v in sorted(report.metrics.items()) if k not in gated}
    if reference_metrics:
        lines += ["", "**参考指標 (非ゲート)**", ""]
        for name, value in reference_metrics.items():
            lines.append(f"- {name}: {value:.3f}")

    if worst_questions:
        lines += ["", "**要確認の問い**", ""]
        for q in worst_questions:
            lines.append(f"- `{q.get('id')}`: {q.get('reason', '')}")

    return "\n".join(lines)
