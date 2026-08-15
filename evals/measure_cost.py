"""eval 1 回あたりの Bedrock 課金を実測する (ワンショット・ローカル専用)。

`evals/run_eval.py` と全く同じコードパス (`_run_one` / `score_generation`) をそのまま
呼び出し、botocore の `BaseClient._make_api_call` を monkeypatch して全 Bedrock 呼び出しの
トークン数を横取りする。app/ にも evals/judge.py にも手を入れない
(app.bedrock._client() をラップする方式だと judge 側が自前で作る boto3 クライアントを
 取り逃すため、クラスメソッド単位でのパッチが唯一 app/judge 両対応できる方法)。

このスクリプトはコストの「参考指標」を出すだけで、CI にも品質ゲートにも組み込まない
(品質とコストは別軸であり、混ぜるとどちらかの誤検知でもう片方がブロックされる)。

⚠️ Cohere など一部の埋め込みモデルは `x-amzn-bedrock-input-token-count` ヘッダを
返さないケースが (CountTokens API 未対応と合わせて) 報告されている。そのためヘッダが
無い場合のフォールバックとしてリクエスト本文の文字数からの概算 (英語想定 chars/4) を
用意している。
⚠️ **ただし 2026-08-15 の実測では cohere.embed-v4:0 でもヘッダから実測値が取得できた**
(`evals/out/cost.json` の `estimated_input_tokens: 0` で確認可能)。フォールバックが
実際に使われた場合はレポートの `warnings` に明記されるので、cost.json を見れば
実測か概算かを都度判別できる。

⚠️ 単価は AWS Price List API では取得できない。get_pricing_attribute_values(
'AmazonBedrock', ['usagetype'], {'usagetype': 'APN1'}) で確認したところ、
ap-northeast-1 (APN1) の usagetype 一覧に Anthropic Claude / Cohere のエントリが
1 件も存在しない (2026-08-15 確認、Nova/Titan/DeepSeek/Qwen 等のみ)。
そのため下記 PRICES_USD_PER_MTOK は AWS 公式発表からの手動転記。単価改定時は
手で更新すること。

⚠️ Claude Haiku 4.5 以降は global / regional の 2 エンドポイントがあり、regional は
global より 10% 高い (Anthropic 公式アナウンス)。このプロジェクトは `jp.` プレフィックスの
regional 推論プロファイルを使う (on-demand 制約のため必須、CLAUDE.md 参照)。
下記単価は global 相当の基準値であり、実際の請求は regional premium 込みでこれより
高い可能性がある。算出後は CloudWatch (AWS/Bedrock 名前空間の InputTokenCount /
OutputTokenCount) の実績と突き合わせて裏取りすること。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import botocore.client

from evals import run_eval
from evals.judge import GenerationSample, score_generation

# 出典: https://aws.amazon.com/bedrock/pricing/ + AWS 公式アナウンス (Claude Haiku 4.5 /
# Cohere Embed v4)。確認日: 2026-08-15。global エンドポイント相当の基準単価 (上記 docstring
# の regional premium 注記を参照)。
PRICES_CHECKED_AT = "2026-08-15"
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 1.00, "output": 5.00},
    "cohere.embed-v4:0": {"input": 0.12, "output": 0.0},
}

# 推論プロファイル ID (jp.anthropic.claude-... 等) を単価表のキー (素のモデル ID) に
# 寄せるための接頭辞一覧
_PROFILE_PREFIXES = ("jp.", "us.", "eu.", "apac.")


def _normalize_model_id(model_id: str) -> str:
    for prefix in _PROFILE_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def _price_for(model_id: str) -> dict[str, float] | None:
    return PRICES_USD_PER_MTOK.get(_normalize_model_id(model_id))


def _estimate_tokens_from_text(texts: list[str]) -> int:
    """Cohere Embed はトークン数を返さないため、文字数からの粗い概算に使う。

    英語ドキュメントのチャンクを想定した chars/4 の経験則。正確な値ではない
    (CallRecord.estimated=True としてレポート側で区別する)。
    """
    return sum(len(t) for t in texts) // 4


@dataclass
class CallRecord:
    phase: str
    operation: str
    model_id: str
    input_tokens: int | None
    output_tokens: int
    estimated: bool = False


@dataclass
class Meter:
    phase: str = "unknown"
    records: list[CallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, rec: CallRecord) -> None:
        with self._lock:
            self.records.append(rec)


@contextlib.contextmanager
def metered_bedrock() -> Iterator[Meter]:
    """`with metered_bedrock() as meter:` の間、全 Bedrock 呼び出しを計測する。

    ragas judge は max_workers>1 で並列に呼ばれるため Meter.record は Lock で保護している。
    """
    meter = Meter()
    orig = botocore.client.BaseClient._make_api_call

    def patched(self: Any, operation_name: str, api_params: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        resp = orig(self, operation_name, api_params, *args, **kwargs)
        try:
            if self.meta.service_model.service_name != "bedrock-runtime":
                return resp
            model_id = api_params.get("modelId", "unknown")
            if operation_name == "Converse":
                usage = resp.get("usage", {})
                meter.record(
                    CallRecord(
                        phase=meter.phase,
                        operation=operation_name,
                        model_id=model_id,
                        input_tokens=usage.get("inputTokens"),
                        output_tokens=usage.get("outputTokens", 0),
                    )
                )
            elif operation_name == "InvokeModel":
                headers = resp.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                raw = headers.get("x-amzn-bedrock-input-token-count")
                estimated = False
                if raw is not None:
                    input_tokens: int | None = int(raw)
                else:
                    # レスポンスの StreamingBody (resp["body"]) は一度しか読めず、読むと
                    # 本来の呼び出し元 (app/bedrock.py) が壊れるため触らない。代わりに
                    # まだ消費前のリクエスト本文 (api_params["body"]) から概算する。
                    try:
                        req_body = json.loads(api_params.get("body", "{}"))
                        input_tokens = _estimate_tokens_from_text(req_body.get("texts", []))
                        estimated = True
                    except (TypeError, ValueError):
                        input_tokens = None
                meter.record(
                    CallRecord(
                        phase=meter.phase,
                        operation=operation_name,
                        model_id=model_id,
                        input_tokens=input_tokens,
                        output_tokens=0,
                        estimated=estimated,
                    )
                )
        except Exception as e:  # noqa: BLE001 - 計測失敗で本番呼び出し自体を壊さないため広く捕捉
            print(f"警告: コスト計測に失敗しました ({operation_name}): {e}", file=sys.stderr)
        return resp

    botocore.client.BaseClient._make_api_call = patched
    try:
        yield meter
    finally:
        botocore.client.BaseClient._make_api_call = orig


def _record_cost_usd(rec: CallRecord) -> float | None:
    price = _price_for(rec.model_id)
    if price is None or rec.input_tokens is None:
        return None
    return (rec.input_tokens * price["input"] + rec.output_tokens * price["output"]) / 1_000_000


def build_cost_report(meter: Meter, n_questions: int, top_k: int, judge_model: str) -> dict[str, Any]:
    by_phase: dict[str, dict[str, Any]] = {}
    unpriced_models: set[str] = set()
    total_usd = 0.0
    any_estimated = False
    any_unknown = False

    for rec in meter.records:
        phase = by_phase.setdefault(
            rec.phase,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_input_tokens": 0,
                "unknown_token_calls": 0,
                "usd": 0.0,
            },
        )
        phase["calls"] += 1
        if rec.input_tokens is None:
            phase["unknown_token_calls"] += 1
            any_unknown = True
        else:
            phase["input_tokens"] += rec.input_tokens
            if rec.estimated:
                phase["estimated_input_tokens"] += rec.input_tokens
                any_estimated = True
        phase["output_tokens"] += rec.output_tokens

        cost = _record_cost_usd(rec)
        if cost is None:
            if _price_for(rec.model_id) is None:
                unpriced_models.add(rec.model_id)
        else:
            phase["usd"] += cost
            total_usd += cost

    for phase in by_phase.values():
        phase["usd"] = round(phase["usd"], 4)

    warnings = []
    if any_estimated:
        warnings.append(
            "cohere.embed-v4:0 の入力トークン数は AWS がヘッダ/本文で返さないため、"
            "リクエスト文字数からの概算値 (chars/4) を使っている。正確な実測ではない。"
        )
    if any_unknown:
        warnings.append(
            "一部の呼び出しでトークン数を取得できなかった (unknown_token_calls)。"
            "その呼び出し分の金額は合計に含まれていない。"
        )
    if unpriced_models:
        warnings.append(
            f"単価表に無いモデルが呼ばれた: {sorted(unpriced_models)}。"
            "そのトークン数は集計されているが金額には含まれていない。"
        )

    return {
        "n_questions": n_questions,
        "top_k": top_k,
        "judge_model": judge_model,
        "prices_usd_per_mtok": PRICES_USD_PER_MTOK,
        "prices_checked_at": PRICES_CHECKED_AT,
        "by_phase": by_phase,
        "total_usd": round(total_usd, 4),
        "usd_per_question": round(total_usd / n_questions, 4) if n_questions else None,
        "unpriced_models": sorted(unpriced_models),
        "warnings": warnings,
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "### Bedrock コスト実測 (参考指標・ゲート対象外)",
        "",
        f"- 問題数: {report['n_questions']} (top_k={report['top_k']})",
        f"- judge モデル: {report['judge_model']}",
        f"- 単価確認日: {report['prices_checked_at']} "
        "(AWS Price List API に ap-northeast-1 の該当エントリが無いため手動転記)",
        "",
        "| フェーズ | 呼び出し回数 | 入力トークン | 出力トークン | USD |",
        "|---|---:|---:|---:|---:|",
    ]
    for phase, s in sorted(report["by_phase"].items()):
        note = " (一部概算)" if s["estimated_input_tokens"] else ""
        lines.append(
            f"| {phase}{note} | {s['calls']} | {s['input_tokens']} | {s['output_tokens']} | "
            f"${s['usd']:.4f} |"
        )
    per_q = report["usd_per_question"]
    per_q_str = "—" if per_q is None else f"${per_q:.4f}/問"
    lines += [
        "",
        f"**合計: ${report['total_usd']:.4f}** ({per_q_str})",
    ]
    for w in report["warnings"]:
        lines.append(f"- ⚠️ {w}")
    return "\n".join(lines)


def run(
    dataset_path: Path,
    out_path: Path,
    top_k: int,
    limit: int | None,
    judge_model: str,
    judge_region: str,
    no_judge: bool,
) -> int:
    dataset = run_eval._load_dataset(dataset_path)
    if limit is not None:
        dataset = dataset[:limit]
    if not dataset:
        print("エラー: データセットが空です", file=sys.stderr)
        return 2

    print(f"{len(dataset)} 問でコストを計測します (top_k={top_k})...")

    with metered_bedrock() as meter:
        meter.phase = "retrieve+generate"
        try:
            per_question = [run_eval._run_one(item, top_k) for item in dataset]
        except Exception as e:  # noqa: BLE001 - run_eval.py と同様、運用エラーとして扱う
            print(f"エラー: retrieve/generate 実行中に例外が発生しました: {e}", file=sys.stderr)
            return 2

        if not no_judge:
            meter.phase = "judge"
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
            try:
                score_generation(samples, judge_model_id=judge_model, region=judge_region)
            except Exception as e:  # noqa: BLE001
                print(f"エラー: ragas judge 実行中に例外が発生しました: {e}", file=sys.stderr)
                return 2

    report = build_cost_report(meter, n_questions=len(dataset), top_k=top_k, judge_model=judge_model)
    text = render_text(report)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(text)
    print(f"\n詳細: {out_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="eval 1 回あたりの Bedrock コストを実測する (品質ゲートには使わない参考指標)"
    )
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/bedrock-ug-qa.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("evals/out/cost.json"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="配線確認用に問題数を制限")
    parser.add_argument("--judge-model", default=run_eval.DEFAULT_JUDGE_MODEL)
    parser.add_argument("--judge-region", default=run_eval.DEFAULT_JUDGE_REGION)
    parser.add_argument(
        "--no-judge", action="store_true", help="judge (ragas) をスキップし retrieve+generate のみ計測"
    )
    args = parser.parse_args()

    sys.exit(
        run(
            dataset_path=args.dataset,
            out_path=args.out,
            top_k=args.top_k,
            limit=args.limit,
            judge_model=args.judge_model,
            judge_region=args.judge_region,
            no_judge=args.no_judge,
        )
    )


if __name__ == "__main__":
    main()
