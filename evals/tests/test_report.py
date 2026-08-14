from evals.report import build_report, evaluate_gate, render_markdown


def _baseline(**overrides):
    base = {
        "dataset_sha256": "abc123",
        "metrics": {"recall@5": 0.80, "generation_score": 0.85},
        "gate": {
            "recall@5": {"tolerance": 0.02, "floor": 0.60},
            "generation_score": {"tolerance": 0.10, "floor": 0.65},
        },
    }
    base.update(overrides)
    return base


def test_gate_passes_within_tolerance():
    results = evaluate_gate(
        metrics={"recall@5": 0.79, "generation_score": 0.90},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
    )
    assert all(r.passed for r in results)


def test_gate_fails_when_below_baseline_minus_tolerance():
    results = evaluate_gate(
        metrics={"recall@5": 0.70, "generation_score": 0.90},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
    )
    recall_result = next(r for r in results if r.metric == "recall@5")
    assert recall_result.passed is False
    assert "baseline比" in recall_result.reason


def test_gate_fails_when_below_floor_even_if_within_tolerance_of_baseline():
    # baseline を意図的に低く設定してしまっていても floor で下限を守る
    results = evaluate_gate(
        metrics={"recall@5": 0.50, "generation_score": 0.90},
        baseline=_baseline(metrics={"recall@5": 0.51, "generation_score": 0.85}),
        current_dataset_sha256="abc123",
    )
    recall_result = next(r for r in results if r.metric == "recall@5")
    assert recall_result.passed is False
    assert "floor" in recall_result.reason


def test_gate_uses_floor_only_when_dataset_changed():
    results = evaluate_gate(
        metrics={"recall@5": 0.65, "generation_score": 0.90},
        baseline=_baseline(),  # baseline recall@5 = 0.80, tolerance 0.02 なら通常は不合格
        current_dataset_sha256="different-hash",
    )
    recall_result = next(r for r in results if r.metric == "recall@5")
    assert recall_result.passed is True
    assert "データセットが変更された" in recall_result.reason


def test_gate_uses_floor_only_when_no_prior_baseline_metric():
    results = evaluate_gate(
        metrics={"recall@5": 0.70},
        baseline={
            "dataset_sha256": None,
            "metrics": {},
            "gate": {"recall@5": {"tolerance": 0.02, "floor": 0.60}},
        },
        current_dataset_sha256="abc123",
    )
    assert results[0].passed is True
    assert "前回値がない" in results[0].reason


def test_gate_fails_when_metric_missing_from_current_run():
    results = evaluate_gate(
        metrics={},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
    )
    assert all(r.passed is False for r in results)


def test_build_report_overall_pass():
    report = build_report(
        metrics={"recall@5": 0.85, "generation_score": 0.90, "mrr": 0.5},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
        n_questions=25,
    )
    assert report.passed is True
    assert report.n_questions == 25
    assert report.dataset_changed is False


def test_build_report_overall_fail_if_any_gate_fails():
    report = build_report(
        metrics={"recall@5": 0.10, "generation_score": 0.90},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
        n_questions=25,
    )
    assert report.passed is False


def test_render_markdown_includes_reference_metrics_and_worst_questions():
    report = build_report(
        metrics={"recall@5": 0.85, "generation_score": 0.90, "mrr": 0.55},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
        n_questions=25,
    )
    md = render_markdown(report, worst_questions=[{"id": "q1", "reason": "hit しませんでした"}])
    assert "recall@5" in md
    assert "mrr: 0.550" in md
    assert "q1" in md
    assert "合格" in md
