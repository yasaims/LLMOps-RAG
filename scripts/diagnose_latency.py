"""[一時的な診断スクリプト] judge フェーズが CI でのみ 30 倍遅い原因を切り分ける。

背景: eval の judge フェーズが CI で約 58 分かかる一方、同一データセット・同一呼び出し回数
(約 208 回) でローカルでは約 2 分で終わる。CloudWatch 上の Bedrock 側 InvocationLatency は
どちらも約 2.2 秒で正常、スロットリングもゼロ。検索+生成フェーズ (自前の boto3 直呼び出し)
は CI でも約 2 分で正常に完了している。つまり遅延は ragas / langchain のレイヤに入っている。

同じ 1 回の推論を 3 つの層で順に測り、どこで時間が増えるかを見る:
  1. boto3 の bedrock-runtime.converse を直接叩く (= app.bedrock と同じ経路)
  2. langchain_aws.ChatBedrockConverse.invoke
  3. ragas の LangchainLLMWrapper.generate_text (judge が実際に通る経路)

原因が判明したら削除すること。
"""

from __future__ import annotations

import os
import statistics
import sys
import time

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
MODEL = os.environ.get("JUDGE_MODEL", "jp.anthropic.claude-haiku-4-5-20251001-v1:0")
N = int(os.environ.get("PROBE_N", "3"))
PROMPT = "Reply with exactly one word: ok"


def _report(label: str, samples: list[tuple[float, float]]) -> None:
    """samples は (実時間, CPU時間) の組。

    実時間 ≒ CPU時間 なら計算で焼いている (CPU バウンド)。
    実時間 >> CPU時間 なら待っている (I/O・sleep・ロック)。この 1 点で対処法が変わる。
    """
    if not samples:
        print(f"{label:<38} 計測できませんでした")
        return
    each = ", ".join(f"{w:.2f}" for w, _ in samples)
    med_wall = statistics.median([w for w, _ in samples])
    med_cpu = statistics.median([c for _, c in samples])
    verdict = "CPU バウンド" if med_cpu > med_wall * 0.5 else "待ち (I/O・sleep)"
    print(f"{label:<38} 実時間 {med_wall:6.2f}s / CPU {med_cpu:6.2f}s  → {verdict}  (各回: {each})")


def _timed(fn) -> tuple[float, float]:
    w0, c0 = time.perf_counter(), time.process_time()
    fn()
    return time.perf_counter() - w0, time.process_time() - c0


def probe_boto3() -> list[tuple[float, float]]:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=REGION)

    def call() -> None:
        client.converse(
            modelId=MODEL,
            messages=[{"role": "user", "content": [{"text": PROMPT}]}],
            inferenceConfig={"maxTokens": 16, "temperature": 0},
        )

    return [_timed(call) for _ in range(N)]


def probe_langchain() -> list[tuple[float, float]]:
    from langchain_aws import ChatBedrockConverse

    llm = ChatBedrockConverse(model=MODEL, region_name=REGION, temperature=0, max_tokens=16)
    return [_timed(lambda: llm.invoke(PROMPT)) for _ in range(N)]


def _ragas_call():
    from langchain_aws import ChatBedrockConverse
    from langchain_core.prompt_values import StringPromptValue
    from ragas.llms import LangchainLLMWrapper

    llm = ChatBedrockConverse(model=MODEL, region_name=REGION, temperature=0, max_tokens=16)
    wrapper = LangchainLLMWrapper(llm)
    return lambda: wrapper.generate_text(StringPromptValue(text=PROMPT), n=1)


def probe_ragas_wrapper() -> list[tuple[float, float]]:
    call = _ragas_call()
    return [_timed(call) for _ in range(N)]


def profile_ragas_wrapper() -> None:
    """10 秒がどの関数に入っているかを名指しする。"""
    import cProfile
    import io
    import pstats

    call = _ragas_call()
    call()  # 初回の遅延初期化をプロファイルから除く

    pr = cProfile.Profile()
    pr.enable()
    call()
    pr.disable()

    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(25)
    print(buf.getvalue())


def main() -> None:
    print("=== 環境 ===")
    print(f"python          : {sys.version.split()[0]}")
    print(f"os.cpu_count()  : {os.cpu_count()}")
    print(f"region / model  : {REGION} / {MODEL}")
    print(f"1 層あたりの試行: {N}")
    print()

    # import 自体に時間がかかっている可能性 (初回のトークナイザ取得など) も測る。
    t0 = time.perf_counter()
    import ragas  # noqa: F401

    print(f"{'import ragas':<38} {time.perf_counter() - t0:6.2f}s")

    t0 = time.perf_counter()
    import langchain_aws  # noqa: F401

    print(f"{'import langchain_aws':<38} {time.perf_counter() - t0:6.2f}s")
    print()

    print("=== 1 回の推論にかかる時間 (層ごと) ===")
    for label, fn in (
        ("1. boto3 converse (自前経路)", probe_boto3),
        ("2. ChatBedrockConverse.invoke", probe_langchain),
        ("3. ragas LangchainLLMWrapper", probe_ragas_wrapper),
    ):
        try:
            _report(label, fn())
        except Exception as e:  # noqa: BLE001 - 診断用途なので握りつぶして次の層へ進む
            print(f"{label:<38} 例外: {type(e).__name__}: {e}")

    print()
    print("=== ragas ラッパー 1 回分の cProfile (cumulative 上位 25) ===")
    try:
        profile_ragas_wrapper()
    except Exception as e:  # noqa: BLE001
        print(f"プロファイル取得に失敗: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
