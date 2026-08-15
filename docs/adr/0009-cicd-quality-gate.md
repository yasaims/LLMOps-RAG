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
`context_recall`=0.887, `generation_score`=0.822。

⚠️ **当初この節には「25問中5問 (`bedrock-ug-001`/`003`/`006`/`009`/`013`) が gold
チャンクにヒットしなかった」と記載していたが、これは誤りだった。** 実際は **8 問**
(上記 5 問 + `bedrock-ug-014` / `025` / `028`) である。`run_eval.py` が
「要確認の問い」のリストを `[:5]` で切っており、その打ち切りが表示上どこにも
現れなかったため、切り捨て後のリストを実数と取り違えて記録してしまった
(`recall@5`=0.680 は 25 問中 17 問ヒット = 8 問ミスを意味しており、当時から指標側は
正しい値を出していた)。現在は総数を見出しに出し、打ち切る場合は「… 他 N 問」を
必ず添えるようにしてある。

**8 問すべてを個別に確認した結果** (2026-08-14。`context_recall` は「参照解答の主張が
検索結果から裏付けられるか」を測るため、検索が実質足りていたかの直接的な指標になる):

| `context_recall` | 問 | 評価 |
|---|---|---|
| 1.00 | `001` `003` `009` `013` `025` `028` (6問) | 検索は実質足りていた。gold チャンクが一意の情報源ではなかっただけで、回答は正しい |
| 0.50 | `014` | 部分的な取りこぼし。主要事実 (Model ID) は別チャンクから正答できている |
| 0.33 | `006` | **本物の検索失敗。誤答している** (下記) |

つまり `recall@5`=0.680 という数字ほど検索は劣化していない。8 問中 6 問は
「同じ事実を含む別の正しいチャンクが引けた」ケースで、ADR 0007 に記載した原文書の
構造的な重複によるノイズと一致する。実質的な検索失敗は `006` と、部分的に `014` の
2 問だけである。

⚠️ **`bedrock-ug-006` は「別の正しいチャンク」ではなく、明確な誤答だった。**
プロンプトキャッシング有効時の入力トークン合計を問う設問に対し、検索がクォータ消費
(burndown) という別トピックのチャンクを引き、モデルがそれを流用した結果
`cacheReadInputTokens` の扱いが gold と正反対になっている
(gold: 合計に含む / 回答: カウントされない)。

この問いの `faithfulness` は 0.83 と高い。**回答は検索結果に忠実で、その検索結果が
設問に対して間違っていた**ためで、faithfulness では原理的に検出できない失敗モードである
(`factual_correctness`=0.00 と `context_recall`=0.33 だけが正しく捉えている)。
3 指標を平均する `generation_score` はこの種の誤りを薄めるので、
ゲートを通ったことを「全問正しかった」と読んではならない。詳細は issue #6。

⚠️ **`factual_correctness` は構造的に低く出る**。全体で `faithfulness`≈0.94 に対し
`factual_correctness`≈0.58〜0.63 と乖離しているのは、参照解答が gold チャンクの
包括的な要約として生成されており、**質問が聞いている範囲より広い**ため。
`014` が典型で、Model ID しか問うていない設問に対し参照解答は 4 つの事実を含み、
正答していても未言及分が減点される。この指標は「回答の正しさ」より
「参照解答の網羅度との一致」を測っている。詳細は issue #7。

gold 判定の内訳 (2026-08-14 の CI 実測): `content_hash` 一致 16 問 /
`section_page` 一致 1 問 / ヒットなし 8 問。副判定 (`doc/section` + ページ範囲の
重なり) が実際に効いているのは 1 問だけで、現状は主判定がほぼ全てを担っている。
副判定は `chunk.py` の window/overlap を変えた際に全問が偽の不合格になるのを防ぐ
保険であり、平時の寄与が小さいこと自体は想定どおり。

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

### ⚠️ ragas のテレメトリ送信を必ず止める (2026-08 の最重要知見)

`evals/judge.py` の先頭で `RAGAS_DO_NOT_TRACK` を `"true"` に設定している。**外すと
CI の eval が 5 分から 59 分に膨れ上がる。**

ragas は `generate_text()` のたびに `https://t.explodinggradients.com` へ
`requests.post` で利用状況を送る (`ragas/_analytics.py` の `track()`)。GitHub Actions の
ランナーではこのホストの DNS 解決が通らず、`getaddrinfo` のリトライ待ちが
1 呼び出しあたり約 10 秒乗る。judge は 25 問で 200 回超呼ばれるため、これだけで
judge フェーズが 2 分 → 58 分になっていた。

cProfile の実測 (CI、1 呼び出し):

```
10.017s  ragas/_analytics.py:222(track) → requests.post
10.015s  socket.py:946(getaddrinfo)
 9.692s  time.sleep (10 回)
─────────
 0.699s  botocore _make_api_call        ← 本来の Bedrock 呼び出し
```

CPU 時間は 0.00 秒。無効化により 10.65s → **0.64s** (16.6 倍) を実測で確認した。

- ⚠️ **値は文字列 `"true"` でなければ効かない。** ragas 側の判定が
  `os.environ.get(...).lower() == "true"` の完全一致なので、`"1"` や `"yes"` では
  「無効化したつもりで有効なまま」になる
- ⚠️ 設定はワークフローの `env` ではなく `evals/judge.py` に置く。ローカル実行にも
  効かせるためと、ragas の import より前である必要があるため
- 性能だけの問題ではない。public リポジトリの CI から第三者エンドポイントへ
  利用状況が送信されていたという点でも止める理由がある

**切り分けの経緯** (同種の問題を再度追うとき用): Bedrock 側の `InvocationLatency` は
CI でも約 2.2 秒で正常、`InvocationThrottles` はゼロ、呼び出し回数もローカルと同じ
約 208 回、リトライ・例外もログにゼロ。自前の boto3 直呼び出しである検索+生成フェーズは
CI でも 25 問を約 2 分で完了していた (`cohere.embed-v4:0` の毎分カウントで確認)。
つまり AWS 側・ネットワーク・自前コードはすべてシロで、ragas / langchain のレイヤだけが
容疑者として残った。そこで同一の推論を boto3 / `ChatBedrockConverse` /
`LangchainLLMWrapper` の 3 層で計測したところ、3 層目だけが 10.65s (他は 0.65s) となり、
cProfile が `_analytics.track` を名指しした。

### judge のタイムアウト設定

`RunConfig(timeout=...)` は **600 秒** (当初 180 秒)。引き上げた当時は
「FactualCorrectness が最も重いから上限が低すぎた」と判断したが、⚠️ **真因は上記の
テレメトリ送信だった**。1 サンプルあたりの LLM 呼び出しが 4 回前後と最も多い
FactualCorrectness にだけ約 40 秒の DNS 待ちが乗り、この指標だけが 180 秒を超えていた。
テレメトリを止めた現在、600 秒は実質使われない安全余裕にすぎない。

暴走の歯止めは独立した 2 段構えで担保する:

1. `eval.yml` の job 単位 `timeout-minutes: 30` — 明示しないと GitHub 既定の
   **360 分 (6 時間)** まで走り続け、Bedrock 課金を垂れ流したまま誰も気づかない
2. 上記の judge カバレッジ検査 (exit 2)

実測の目安は 25 問で約 5 分 (ローカルは 3 分 51 秒)。

### ⚠️ `PYTHONUNBUFFERED` を設定する

`eval.yml` の評価ステップに `PYTHONUNBUFFERED: "1"` を入れている。これがないと
stdout がブロックバッファリングされ、**プロセス終了時まで 1 行も出力されない**。
実際、59 分の実行でログが完全に無音になり、CI ログからは「ハングしているのか
進行中なのか」を判断できなかった (切り分けには CloudWatch の Bedrock
`Invocations` 毎分カウントを使う必要があった)。

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
