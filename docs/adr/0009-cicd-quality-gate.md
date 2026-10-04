# ADR 0009: CI/CD パイプラインは 4 ワークフロー構成、eval は常時起動ジョブで必須チェック化する

## ステータス

Accepted

## コンテキスト

`ci.yml` (lint + unit test) に加え、infra の plan/apply と RAG 品質評価を CI に載せる。GitHub Actions の仕様上、次の 2 つが設計を制約する。

1. **path フィルタと必須ステータスチェックの相性が悪い**。workflow レベルの `paths:` でスキップされたワークフローはチェック自体が生成されない。必須チェックに指定すると、無関係な変更 (README のみの PR など) でチェックが永遠に `pending` となり PR がマージ不能になる
2. **LLM judge のスコアは確率的にばらつく**。n=25 程度では、PR ごとに数 % 動いても実質的な品質劣化ではないことが多い

## 決定

### ワークフロー構成

- **`terraform-plan.yml`**: `pull_request` + `paths: infra/**`。plan 結果を PR にコメントする (`actions/github-script` + HTML マーカーで upsert。第三者アクションは使わない)。**必須チェックにしない** (情報提供のみ。path フィルタでスキップされても実害がない)
- **`terraform-apply.yml`**: `main` への push。イメージ build & ECR push → `terraform apply` → `/healthz` スモークテストまで一貫して行う。`concurrency: {group: tf-apply-dev, cancel-in-progress: false}` で apply の割り込みキャンセルを防ぐ。`scripts/push_image.ps1` はローカルからの緊急デプロイ用
- **`eval.yml`**: `main` 向け `pull_request`。**workflow レベルの `paths:` は使わない**。ジョブは常に起動し、最初のステップで `git diff` により `app/` `evals/` `pyproject.toml` `uv.lock` の変更有無を判定する。無関係なら以降を `if:` でスキップして success で終える。これで Bedrock 課金なしに必須チェックとして機能する

ブランチ保護は ruleset `pr` (id `20790782`) で `~DEFAULT_BRANCH` に `pull_request` ルールと `required_status_checks: [lint-and-test, eval]` を課す。チェック名は各ジョブ ID。admin bypass は**意図的に残す** (通常フローは PR + CI 合格を必須にしつつ、緊急時の裁量を確保する)。

### リグレッション許容幅

`evals/baseline.json` の `gate` にメトリクスごとの `tolerance` (baseline からの許容低下幅) と `floor` (絶対下限) を持たせる。

| メトリクス | tolerance | floor | 根拠 |
|---|---:|---:|---|
| `recall@5` | 0.02 | 0.50 | 決定的指標。tolerance は「劣化させない」宣言。floor は baseline (0.68) − 0.15 が目安 |
| `mrr` | 0.02 | 0.30 | 同上 (baseline 0.467) |
| `generation_score` | 0.10 | 0.65 | judge のばらつきを吸収するため広め (baseline 0.822) |
| `citation_format_valid` | 0.02 | 0.90 | 決定的指標で baseline が 1.000 のため、目安より絶対値で厳しめ |

`generation_score` (`faithfulness` / `factual_correctness` / `context_recall` の平均、ADR 0007) の tolerance を広くする理由: n=25, p≈0.8 の二項標準誤差は `sqrt(0.8*0.2/25) ≈ 0.08`。個別メトリクスに 0.05 のような狭い幅を課すと 1σ 未満でも誤検知が頻発する。3 指標の平均で分散を下げ、tolerance 0.10 (≈1.25σ) とすることで、誤検知を抑えつつ実質的な劣化を捕まえる。

baseline の dataset sha256 が現在のデータセットと一致しない場合、差分比較は無効化し `floor` のみで判定する (`evals/report.evaluate_gate`)。

**baseline 実測値** (25 問、`jp.anthropic.claude-haiku-4-5-20251001-v1:0` の回答 + 同モデル judge): `recall@1`=0.320, `recall@3`=0.600, `recall@5`=0.680, `mrr`=0.467, `citation_format_valid`=1.000, `faithfulness`=0.955, `factual_correctness`=0.625, `context_recall`=0.887, `generation_score`=0.822。

指標を読むときの注意:

- **`recall@5`=0.680 ほど検索は悪くない**。ヒットなし 8 問のうち 6 問は `context_recall`=1.00 で、同じ事実を含む別チャンクが引けていた (原文書の構造的重複、ADR 0007)。実質的な検索失敗は `bedrock-ug-006` (誤答) と部分的な `014` のみ
- ⚠️ **`generation_score` が通っても「全問正しい」とは読めない**。`006` はプロンプトキャッシングの入力トークン合計を問う設問で、検索が別トピック (クォータ burndown) のチャンクを引き、`cacheReadInputTokens` の扱いが gold と逆の誤答になった。回答は検索結果に忠実なので `faithfulness`=0.83 と高く、`faithfulness` では原理的に検出できない。`factual_correctness`=0.00 と `context_recall`=0.33 だけが捉え、平均はこの種の誤りを薄める (issue #6)
- ⚠️ **`factual_correctness` は構造的に低く出る** (`faithfulness`≈0.94 に対し 0.58〜0.63)。参照解答が gold チャンクの包括的な要約で、質問の範囲より広いため、正答でも未言及分が減点される。「回答の正しさ」より「参照解答の網羅度との一致」を測る (issue #7)
- gold 判定は `content_hash` 一致が 16 問、副判定 (`doc/section` + ページ範囲) が 1 問、ヒットなしが 8 問。副判定は `chunk.py` の window/overlap 変更で全問が偽の不合格になるのを防ぐ保険で、平時の寄与が小さいのは想定どおり
- `run_eval.py` の「要確認の問い」は打ち切る場合に総数と「… 他 N 問」を必ず出す

### baseline の更新

`evals/run_eval.py --update-baseline` は**ローカル専用で CI からは呼ばない**。「新しいスコアが基準として妥当か」は人間が判断すべきで、CI が自動で書き換えると劣化がなし崩しに許容される。更新は独立した PR にして diff を残す。

### 終了コード

`0`=合格 / `1`=品質リグレッション / `2`=運用エラー (データセット欠損・AWS 例外・ragas 実行時例外・judge カバレッジ不足など)。スロットリング等の一過性障害を品質劣化と誤報しないための分離。

### judge カバレッジ検査

⚠️ `ragas.evaluate` は `raise_exceptions=False` でタイムアウトしたサンプルを NaN にし、`run_eval.py` の平均は NaN を除外する。このままでは **25 問中 5 問しか採点できていなくても、その 5 問の平均が `generation_score` となりゲートを通る**。

- 生成指標ごとに judge が採点できた問題数 (`judge_coverage`) を `report.json` / `summary.md` / baseline に記録する
- 有効サンプルが `MIN_JUDGE_COVERAGE` (0.8) 未満なら **exit 2**。残りだけの平均は品質指標として成立しないため、`1` とは区別する
- 検査はレポート出力の**後**に行う。先に return すると Artifact と PR コメントが生成されず、何問落ちたか追えない
- 不足時は `--update-baseline` を拒否する (部分的な結果を基準値に固定しない)
- 判定は `evals/report.py` の純粋関数 `insufficient_judge_coverage()` で、ユニットテスト対象

### ⚠️ ragas のテレメトリを止める

`evals/judge.py` の先頭で `RAGAS_DO_NOT_TRACK = "true"` を設定する。**外すと CI の eval が約 5 分から 59 分に膨れる。**

ragas は `generate_text()` のたびに `https://t.explodinggradients.com` へ `requests.post` で利用状況を送る。GitHub ランナーではこのホストの DNS 解決が通らず、`getaddrinfo` のリトライ待ちが 1 呼び出し約 10 秒乗る。judge は 25 問で 200 回超呼ばれるため、judge ステージだけで 2 分が 58 分になる。無効化で 1 呼び出し 10.65s → 0.64s (16.6 倍)。

- ⚠️ **値は文字列 `"true"` でなければ効かない** (ragas が `.lower() == "true"` の完全一致で判定する)。`"1"` や `"yes"` では無効化したつもりで有効のまま
- ⚠️ ワークフローの `env` ではなく `evals/judge.py` に置く。ローカル実行にも効かせるためと、ragas の import より前に必要なため
- 性能に加え、public リポジトリの CI から第三者エンドポイントへ利用状況が送信される点でも止める理由がある

同種の遅延を追うときの切り分け: Bedrock の `InvocationLatency` (約 2.2 秒)・`InvocationThrottles`・呼び出し回数が正常なら AWS 側と自前コードは無関係で、ragas / langchain 層が疑わしい。boto3 / `ChatBedrockConverse` / `LangchainLLMWrapper` の 3 層で同一推論を計測し、cProfile で絞る。

### タイムアウトと暴走防止

- `RunConfig(timeout=600)` 秒。実質使われない安全余裕で、遅延の真因は上記テレメトリだった (呼び出しが最も多い `FactualCorrectness` だけが DNS 待ちで上限を超えていた)
- 暴走の歯止めは独立した 2 段: (1) `eval.yml` の job 単位 `timeout-minutes: 30` (未指定だと GitHub 既定の 360 分まで Bedrock 課金が続く)、(2) judge カバレッジ検査 (exit 2)
- 実行時間の目安は 25 問で約 5 分

### ⚠️ `PYTHONUNBUFFERED` を設定する

`eval.yml` の評価ステップに `PYTHONUNBUFFERED: "1"` を入れる。ないと stdout がブロックバッファリングされ、**プロセス終了まで 1 行も出力されず**、ハングか進行中かを CI ログから判断できない (その場合は CloudWatch の Bedrock `Invocations` 毎分カウントで確認する)。

## 影響

- eval 1 回の Bedrock 課金は約 $0.65 (ADR 0007、`evals/measure_cost.py`)。月次 Budgets (10 USD) を圧迫しないよう、`app/` `evals/` 依存ファイル以外の変更では実行しない
- `terraform-plan.yml` は必須チェックでないため、infra 変更の安全性はレビュアーが plan コメントを目視確認する運用に依存する。必須化するなら `eval.yml` と同じ「変更ありなら plan、なしなら早期成功」パターンへの書き換えが必要
- ワークフロー定義は `.github/workflows/{terraform-plan,terraform-apply,eval}.yml` を参照
