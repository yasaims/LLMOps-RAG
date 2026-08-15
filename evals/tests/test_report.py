from evals.report import (
    build_report,
    dataset_sha256,
    evaluate_gate,
    insufficient_judge_coverage,
    render_markdown,
)


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


def test_judge_coverage_defaults_to_none_and_reports_no_incomplete_metrics():
    report = build_report(
        metrics={"recall@5": 0.85, "generation_score": 0.90},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
        n_questions=25,
    )
    assert report.judge_coverage is None
    assert report.incomplete_metrics == {}
    assert "judge 有効サンプル" not in render_markdown(report)


def test_full_judge_coverage_is_rendered_without_warning():
    report = build_report(
        metrics={"recall@5": 0.85, "generation_score": 0.90},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
        n_questions=25,
        judge_coverage={"faithfulness": 25, "factual_correctness": 25, "context_recall": 25},
    )
    md = render_markdown(report)
    assert report.incomplete_metrics == {}
    assert "factual_correctness 25/25" in md
    assert "採点できた問題だけの平均" not in md


def test_partial_judge_coverage_is_flagged_in_markdown():
    """タイムアウトで一部しか採点できていないとき、平均値だけを見て合格と読ませない。"""
    report = build_report(
        metrics={"recall@5": 0.85, "generation_score": 0.95},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
        n_questions=25,
        judge_coverage={"faithfulness": 25, "factual_correctness": 5, "context_recall": 25},
    )
    md = render_markdown(report)
    assert report.incomplete_metrics == {"factual_correctness": 5}
    assert "factual_correctness 5/25 ⚠️" in md
    assert "採点できた問題だけの平均" in md
    # ゲート自体は数値上通ってしまう。だからこそレポートに警告が出る必要がある。
    assert report.passed is True


def test_full_coverage_passes_the_check():
    assert insufficient_judge_coverage({"faithfulness": 25}, n_questions=25, min_coverage=0.8) == {}


def test_coverage_exactly_at_the_threshold_passes():
    """20/25 = 0.80 はちょうど下限。「下回った」ではないので通す。"""
    assert insufficient_judge_coverage({"faithfulness": 20}, n_questions=25, min_coverage=0.8) == {}


def test_coverage_below_threshold_is_reported():
    assert insufficient_judge_coverage(
        {"faithfulness": 25, "factual_correctness": 19, "context_recall": 25},
        n_questions=25,
        min_coverage=0.8,
    ) == {"factual_correctness": 19}


def test_all_metrics_timed_out_are_all_reported():
    assert insufficient_judge_coverage(
        {"faithfulness": 0, "factual_correctness": 0, "context_recall": 0},
        n_questions=25,
        min_coverage=0.8,
    ) == {"faithfulness": 0, "factual_correctness": 0, "context_recall": 0}


def test_no_judge_run_is_not_subject_to_the_check():
    assert insufficient_judge_coverage(None, n_questions=25, min_coverage=0.8) == {}
    assert insufficient_judge_coverage({}, n_questions=25, min_coverage=0.8) == {}


def test_empty_dataset_is_treated_as_insufficient_not_as_perfect_coverage():
    assert insufficient_judge_coverage({"faithfulness": 0}, n_questions=0, min_coverage=0.8) == {
        "faithfulness": 0
    }


# --- dataset_sha256: 改行コードで割れないこと (issue #4) -------------------

_JSONL_LINES = [
    b'{"id": "q1", "question": "\xe3\x81\x82"}',
    b'{"id": "q2", "question": "\xe3\x81\x84"}',
]


def _write(tmp_path, name: str, sep: bytes):
    path = tmp_path / name
    path.write_bytes(sep.join(_JSONL_LINES) + sep)
    return path


def test_dataset_sha256_is_identical_for_crlf_and_lf(tmp_path):
    """Windows で作った baseline が Linux の CI と食い違わないこと。

    これが崩れると dataset_changed が常に True になり、baseline 比較が無効化されて
    floor のみの判定に落ちる (tolerance によるリグレッション検知が死ぬ)。
    """
    lf = _write(tmp_path, "lf.jsonl", b"\n")
    crlf = _write(tmp_path, "crlf.jsonl", b"\r\n")

    assert lf.read_bytes() != crlf.read_bytes()  # 前提: バイト列としては別物
    assert dataset_sha256(lf) == dataset_sha256(crlf)


def test_dataset_sha256_still_detects_a_real_content_change(tmp_path):
    """改行を正規化しても、中身が変わればハッシュは変わること。"""
    original = _write(tmp_path, "a.jsonl", b"\n")

    changed = tmp_path / "b.jsonl"
    changed.write_bytes(b"\n".join([*_JSONL_LINES, b'{"id": "q3"}']) + b"\n")

    assert dataset_sha256(original) != dataset_sha256(changed)


# --- 要確認の問い: 件数を偽らないこと ---------------------------------------


def _report_with_worst():
    return build_report(
        metrics={"recall@5": 0.85, "generation_score": 0.90},
        baseline=_baseline(),
        current_dataset_sha256="abc123",
        n_questions=25,
    )


def test_worst_questions_heading_shows_the_total_count():
    """recall と件数が食い違って見えないよう、総数を必ず出す。"""
    worst = [{"id": f"q{i}", "reason": "検索でヒットしませんでした"} for i in range(8)]
    md = render_markdown(_report_with_worst(), worst_questions=worst)
    assert "**要確認の問い (8 問)**" in md
    for i in range(8):
        assert f"`q{i}`" in md
    assert "他" not in md  # 上限内なので打ち切りの注記は出ない


def test_worst_questions_beyond_the_cap_are_counted_not_dropped():
    worst = [{"id": f"q{i}", "reason": "検索でヒットしませんでした"} for i in range(14)]
    md = render_markdown(_report_with_worst(), worst_questions=worst)
    assert "**要確認の問い (14 問)**" in md
    assert "… 他 4 問" in md
    assert "`q9`" in md  # 10 件目までは並ぶ
    assert "`q10`" not in md  # 11 件目以降は件数のみ


def test_no_worst_questions_renders_no_section():
    md = render_markdown(_report_with_worst(), worst_questions=[])
    assert "要確認の問い" not in md
