# ADR 0009: CI/CD パイプラインは 4 ワークフロー構成、eval は常時起動ジョブで必須チェック化する

## ステータス

Accepted

## コンテキスト

計画書 §5 は `ci.yml` (lint+unit test, 実装済み) に加えて `terraform-plan.yml` /
`terraform-apply.yml` / `eval.yml` の 3 ワークフローを想定している。実装にあたり、
GitHub Actions の仕様上の制約が 2 つ絡んだ:

1. **path フィルタと必須ステータスチェックの相性が悪い**。workflow レベルの `paths:`
   でスキップされたワークフローは、そのブランチではチェック自体が生成されない。
   これをブランチ保護の必須チェックに指定すると、無関係な変更 (例: README のみの PR)
   の場合にチェックが永遠に `pending` のままになり PR がマージ不能になる
2. **LLM judge のスコアは確率的にばらつく**。n=25 程度のサンプルサイズでは、
   個々の PR で数 % 変動しても実質的な品質劣化ではないことが多い

## 決定

### ワークフロー構成

- **`terraform-plan.yml`**: `pull_request` + `paths: infra/**`。plan 結果を PR に
  コメント (`actions/github-script` で HTML マーカーによる upsert。第三者アクションの
  追加依存を避けるため標準アクションのみで組んでいる)。**必須チェックにはしない**
  (情報提供目的。path フィルタでスキップされても実害がないのはこちらだけ)
- **`terraform-apply.yml`**: `push` to `main`。イメージの build & ECR push
  (`scripts/push_image.ps1` のロジックを移植) → `terraform apply` →
  `/healthz` へのスモークテストまで一気通貫で行う。`concurrency:
  {group: tf-apply-dev, cancel-in-progress: false}` で apply の割り込みキャンセルを
  防ぐ。`scripts/push_image.ps1` はローカルからの緊急デプロイ用として残すが、
  通常の経路はこのワークフローが正
- **`eval.yml`**: `pull_request` (base: main)。**workflow レベルの `paths:` は
  使わない**。ジョブは常に起動し、最初のステップで `git diff` により
  `app/` `evals/` `pyproject.toml` `uv.lock` の変更有無を判定して、無関係なら
  以降のステップを `if:` でスキップして success で終える。これにより Bedrock 課金を
  発生させずに必須チェックとして機能させられる

ブランチ保護は既存 ruleset `pr` (id `20790782`。適用時点では
`conditions.ref_name.include` が空で実質無効化されていた) を更新し、
`~DEFAULT_BRANCH` に対する `pull_request` ルール + `required_status_checks:
[lint-and-test, eval]` を設定する。チェック名は各ワークフローのジョブ ID
(`ci.yml` の `lint-and-test`, `eval.yml` の `eval`) がそのまま context 名になる。
admin (自分自身) の bypass は温存する — 「本当に一切マージできなくする」のではなく
「通常フローは PR + CI 合格を必須にしつつ、自分の緊急時の裁量は残す」運用とした。

### リグレッション許容幅

`evals/baseline.json` の `gate` に、メトリクスごとに `tolerance` (baseline からの
許容低下幅) と `floor` (絶対下限) を持たせる:

| メトリクス | tolerance | floor | 根拠 |
|---|---:|---:|---|
| `recall@5` | 0.02 | 0.50 | 決定的指標 (コードとインデックスが同じなら再現する) なので tolerance は「劣化させない」宣言に近い。floor は初回計測 (0.68。下記) から `baseline - 0.15` の目安で設定 |
| `mrr` | 0.02 | 0.30 | 同上。初回計測は 0.467 |
| `generation_score` | 0.10 | 0.65 | LLM judge の確率的ばらつきを吸収するため tolerance は広め (下記)。初回計測は 0.822 |
| `citation_format_valid` | 0.02 | 0.90 | 決定的指標かつ初回計測が 1.000 だったため、`baseline - 0.15` の目安より絶対値で厳しめに設定 |

`generation_score` (= `faithfulness`/`factual_correctness`/`context_recall` の平均、
ADR 0007) に広い tolerance を割り当てている理由: n=25, p≈0.8 程度のスコア分布を
仮定すると二項標準誤差は `sqrt(0.8*0.2/25) ≈ 0.08`。個々のメトリクスに 0.05 のような
狭い許容幅を課すと 1σ 未満でも誤検知が頻発する。3 メトリクスの平均に集約して分散を
下げたうえで tolerance 0.10 (≈1.25σ) にすることで、誤検知率を抑えつつ実質的な劣化は
捕まえる設計にしている。

**初回 baseline 実測値** (`evals/datasets/bedrock-ug-qa.jsonl`, 25問,
`jp.anthropic.claude-haiku-4-5-20251001-v1:0` 回答 + 同モデル judge):
`recall@1`=0.320, `recall@3`=0.600, `recall@5`=0.680, `mrr`=0.467,
`citation_format_valid`=1.000, `faithfulness`=0.955, `factual_correctness`=0.625,
`context_recall`=0.887, `generation_score`=0.822。25問中5問 (`bedrock-ug-001` /
`003` / `006` / `009` / `013`) が gold チャンクにヒットしなかったが、実際の回答内容を
確認したところ大半は「同じ事実 (boto3 のサービス名等) を含む別の正しいチャンク」が
検索されたことによるもので、ADR 0007 に記載した原文書の構造的な重複によるノイズと
一致する。`factual_correctness` が 1.0 に張り付いていない (0.625) ことは、judge が
実際に非自明な判定を行っている傍証でもある。

データセット (`evals/datasets/bedrock-ug-qa.jsonl`) が baseline 記録時から変更されて
いる場合 (sha256 が不一致)、baseline との差分比較は無効化し `floor` のみで判定する
(`evals/report.evaluate_gate`)。

### baseline の更新方法

`evals/run_eval.py --update-baseline` はローカル専用で、CI からは呼ばない。baseline
の更新は「今回のスコアが新しい基準として妥当か」を人間が判断すべき変更であり、
CI が自動で基準を書き換えると劣化がなし崩しに許容されてしまう。baseline の更新は
独立した PR にして diff を残す運用とする。

### 終了コード

`evals/run_eval.py` は `0`=合格 / `1`=品質リグレッション / `2`=運用エラー
(データセット欠損・AWS 例外・ragas 実行時例外など) を区別する。AWS 側の一過性障害
(スロットリング等) を「品質劣化」と誤って報告しないための分離。

### judge カバレッジ検査 (2026-08 追加)

⚠️ **黙って合格してしまう経路があったため追加した。** `ragas.evaluate` は
`raise_exceptions=False` の下でタイムアウトしたサンプルを NaN として返し、
`run_eval.py` の平均は NaN を除外して計算する。この 2 つが組み合わさると
**25 問中 5 問しか採点できていなくても、その 5 問の平均がそのまま
`generation_score` になってゲートを通過する**。母数はレポートにも PR コメントにも
出ていなかったため、外から気づく手段がなかった。

- 生成指標ごとに「judge が実際に採点できた問題数」(`judge_coverage`) を記録し、
  `report.json` / `summary.md` / baseline に残す
- 有効サンプルが `MIN_JUDGE_COVERAGE` (0.8) を下回ったら **exit 2 (運用エラー)**。
  残った問題だけの平均は品質の指標として成立しないので、`1` (品質リグレッション)
  とは区別する
- カバレッジ検査はレポートを書き出した「後」に行う。先に return すると Artifact と
  PR コメントが生成されず、何問落ちたのか追えなくなる
- カバレッジ不足時は `--update-baseline` を明示的に拒否する (部分的な結果を
  基準値に焼き付けないため)
- 判定は `evals/report.py` の `insufficient_judge_coverage()` に純粋関数として切り出し、
  依存ゼロのまま既存 CI のユニットテスト対象にしている

### judge のタイムアウト設定

`RunConfig(timeout=...)` は **600 秒**。当初の 180 秒では CI 上で
`FactualCorrectness` だけが全 25 問タイムアウトしていた (`Faithfulness` と
`LLMContextRecall` は完走)。この 1 指標は回答と参照の双方を claim に分解してから
双方向 NLI を回すため 1 サンプルあたり LLM 呼び出しが 4 回前後になり、
日本語の長文回答ではさらに伸びる。実行ログに `Throttling` / retry の記録がないため、
スロットリング由来ではなく上限そのものが低すぎたと判断した。

暴走の歯止めは上限の引き上げではなく、独立した 2 段構えで担保する:

1. `eval.yml` の job 単位 `timeout-minutes: 60` — 明示しないと GitHub 既定の
   **360 分 (6 時間)** まで走り続け、Bedrock 課金を垂れ流したまま誰も気づかない
2. 上記の judge カバレッジ検査 (exit 2)

実測の目安は 検索+生成 約 5 分 (25 問・逐次) + judge 約 30 分
(75 ジョブ / `max_workers=4`)。

## 影響

- eval 1 回あたりの Bedrock 課金は概算 $1 未満 (ADR 0007)。月次 Budgets (10 USD) を
  圧迫しないよう、`app/`/`evals/`/依存ファイル以外の変更では実行されない設計にしている
- `evals/baseline.json` は 2026-08-14 11:58Z に `judge_coverage` 付きで取り直した
  (3 指標とも 25/25)。取り直し前の版は母数を記録していなかったため一時的に
  「一部タイムアウトの残りの平均かもしれない」と疑ったが、**結果として旧版は正しかった**:
  最も疑わしかった `factual_correctness` が 0.6248 → 0.6280 とほぼ完全に再現し、
  検索指標 5 つ (`recall@1/3/5`, `mrr`, `citation_format_valid`) は完全一致した
  (LLM を使わない決定的計算なので当然の挙動)。生成指標の差は judge のばらつきの範囲
  (`generation_score` +0.012、tolerance 0.10 に対して十分小さい)
- `terraform-plan.yml` を必須チェックにしていないため、infra 変更が実際に安全かどうかは
  レビュアーが plan コメントを目視で確認する運用に依存する。将来的に必須化したい場合は
  「変更ありなら plan を出力、変更なしなら早期成功」という eval.yml と同じパターンへの
  書き換えが必要になる
- 詳細なワークフロー定義は `.github/workflows/{terraform-plan,terraform-apply,eval}.yml`
  を参照
