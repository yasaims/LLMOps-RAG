"""Ragas + Bedrock (ChatBedrockConverse) の配線。

ragas を import するのはこのモジュールだけにする (evals/metrics.py, evals/report.py は
依存ゼロの純粋関数のみで構成し、既存 CI の `uv sync --frozen` でもユニットテストできるように
しておくため)。

⚠️ embeddings 系メトリクス (SemanticSimilarity 等) は使わない。追加の Bedrock embed 課金を
発生させず、ragas 側の embeddings wrapper も不要になる (ADR 0007)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ⚠️ ragas は generate_text() のたびに https://t.explodinggradients.com へ
# requests.post でテレメトリを送信する (`ragas/_analytics.py` の track())。
# GitHub Actions のランナーではこのホストの DNS 解決が通らず、getaddrinfo のリトライ待ちで
# 1 呼び出しあたり約 10 秒が加算されていた (cProfile 実測: 10.7 秒中 10.0 秒が time.sleep、
# 本来の Bedrock 呼び出しは 0.7 秒)。judge は 25 問で 200 回超呼ばれるため、これだけで
# CI の judge フェーズが 2 分 → 58 分に膨らんでいた。
#
# ⚠️ 値は文字列 "true" でなければならない。ragas 側の判定が
#    `os.environ.get(...).lower() == "true"` の完全一致なので、"1" や "yes" では
#    黙って無効化されない。
#
# ragas の import より前に設定する必要があるため、モジュール先頭に置いている
# (ragas 本体の import は score_generation() 内の遅延 import)。
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

# ragas 0.4.3 は `ragas.metrics` から Faithfulness/FactualCorrectness/LLMContextRecall を
# import すると DeprecationWarning を出す (v1.0 で `ragas.metrics.collections` に統合予定)。
# 0.4 系に固定している間はこの経路で問題ないため、ここでは warning を抑制しない
# (v1.0 移行時に気づけるように)。


@dataclass
class GenerationSample:
    id: str
    question: str
    answer: str
    contexts: list[str]
    reference: str


@dataclass
class GenerationScore:
    id: str
    faithfulness: float
    factual_correctness: float
    context_recall: float


def score_generation(
    samples: list[GenerationSample],
    judge_model_id: str,
    region: str,
    max_workers: int = 4,
    timeout: int = 600,
) -> list[GenerationScore]:
    """Faithfulness / FactualCorrectness / LLMContextRecall を Bedrock judge で採点する。

    ⚠️ timeout の既定値を 180 → 600 秒に引き上げてある。3 メトリクスのうち
    FactualCorrectness だけが桁違いに重く (回答と参照の双方を claim に分解してから
    双方向 NLI を回すため 1 サンプルあたり LLM 呼び出しが 4 回前後、日本語の長文回答では
    さらに伸びる)、180 秒では CI 上で全 25 問が例外なくタイムアウトしていた。
    スロットリング由来ではない (実行ログに Throttling / retry の記録がない) ため、
    上限そのものが低すぎたと判断している。

    暴走の歯止めは呼び出し側の 2 段構えに任せる:
      - eval.yml の job 単位 `timeout-minutes`
      - run_eval.py の judge カバレッジ検査 (NaN 混入時は exit 2)
    """
    if not samples:
        return []

    from langchain_aws import ChatBedrockConverse
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import FactualCorrectness, Faithfulness, LLMContextRecall
    from ragas.run_config import RunConfig

    judge = ChatBedrockConverse(model=judge_model_id, region_name=region, temperature=0)
    evaluator_llm = LangchainLLMWrapper(judge)

    ragas_samples = [
        SingleTurnSample(
            user_input=s.question,
            response=s.answer,
            retrieved_contexts=s.contexts or [""],  # 空リストだと ragas 側で弾かれるため
            reference=s.reference,
        )
        for s in samples
    ]
    dataset = EvaluationDataset(samples=ragas_samples)
    result = evaluate(
        dataset,
        metrics=[Faithfulness(), FactualCorrectness(), LLMContextRecall()],
        llm=evaluator_llm,
        run_config=RunConfig(max_workers=max_workers, max_retries=5, timeout=timeout, seed=42),
        raise_exceptions=False,
        show_progress=False,
    )
    df = result.to_pandas()

    scores = []
    for sample, (_, row) in zip(samples, df.iterrows(), strict=True):
        scores.append(
            GenerationScore(
                id=sample.id,
                faithfulness=_safe_float(row.get("faithfulness")),
                factual_correctness=_safe_float(
                    row.get("factual_correctness(mode=f1)", row.get("factual_correctness"))
                ),
                context_recall=_safe_float(row.get("context_recall")),
            )
        )
    return scores


def _safe_float(value: object) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return f
