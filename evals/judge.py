"""Ragas + Bedrock (ChatBedrockConverse) の配線。

ragas を import するのはこのモジュールだけにする (evals/metrics.py, evals/report.py は
依存ゼロの純粋関数のみで構成し、既存 CI の `uv sync --frozen` でもユニットテストできるように
しておくため)。

⚠️ embeddings 系メトリクス (SemanticSimilarity 等) は使わない。追加の Bedrock embed 課金を
発生させず、ragas 側の embeddings wrapper も不要になる (ADR 0007)。
"""

from __future__ import annotations

from dataclasses import dataclass

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
) -> list[GenerationScore]:
    """Faithfulness / FactualCorrectness / LLMContextRecall を Bedrock judge で採点する。"""
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
        run_config=RunConfig(max_workers=max_workers, max_retries=5, timeout=180, seed=42),
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
