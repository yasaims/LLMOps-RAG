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

    @property
    def dataset_changed(self) -> bool:
        return (
            self.baseline_dataset_sha256 is not None
            and self.baseline_dataset_sha256 != self.dataset_sha256
        )


def dataset_sha256(dataset_path: Path) -> str:
    return hashlib.sha256(dataset_path.read_bytes()).hexdigest()


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


def build_report(
    metrics: dict[str, float],
    baseline: dict[str, Any],
    current_dataset_sha256: str,
    n_questions: int,
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
    )


def render_markdown(report: EvalReport, worst_questions: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "### RAG 品質評価",
        "",
        f"- 問題数: {report.n_questions}",
        f"- 判定: {'✅ 合格' if report.passed else '❌ 不合格'}",
    ]
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
