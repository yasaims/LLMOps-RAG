# ADR 0007: RAG 品質評価は Ragas の LLM-only サブセット + 決定的な検索指標で構成する

## ステータス

Accepted

## コンテキスト

計画書 §2 の差別化ポイントは「評価 (eval) を CI に組み込んだ LLMOps 設計」。Phase 3 で
PR ごとに RAG の品質を自動評価し、劣化があればマージをブロックする仕組みを作る。
PR ごとに実行される前提のため、実行時間・Bedrock 課金・依存関係の重さが直接コストに跳ねる。

検討した選択肢:

1. **決定的指標のみ** (recall@k / MRR / 文字列一致)。実行が速く無料に近いが、
   回答の忠実性 (ハルシネーションの有無) や表現の妥当性を測れない
2. **Ragas をデフォルト設定でフル機能利用**。実績のあるフレームワークだが、
   `SemanticSimilarity` 等の embeddings 系メトリクスまで含めると `ragas[all]` 相当の
   重い依存 (`sentence-transformers`/`transformers` = torch) を引き込みかねず、
   追加の embed API 課金も発生する
3. **LLM-as-judge を完全自作** (Bedrock を直接呼んでプロンプトで採点)。依存は最小だが、
   claim 分解や NLI ベースの厳密な忠実性判定を自前実装するのは車輪の再発明になる

`uv lock` で実測したところ、`ragas==0.4.3` 単体の依存関係
(`datasets`, `openai`, `instructor`, `langchain`, `langchain-community`, `networkx`,
`scikit-network`, `tiktoken`, `pillow` 等) は torch を引かず、`sentence-transformers` /
`transformers` は `ragas[all]` extra を明示的に指定した場合のみ入ることを確認した。

## 決定

- **検索側は決定的指標のみ**: `recall@1/3/5`, `MRR` を Python で自前計算する
  (`evals/metrics.py`)。LLM を介さないため再現性が高く、コストもかからない。
  gold チャンクの一致判定は `SearchResult.id == content_hash` を主判定とする
  (S3 Vectors の vector key は content_hash そのもの。⚠️ pgvector の id は連番の
  DB 主キーであり成立しないため、この判定は `VECTOR_STORE=s3vectors` 前提)。
  `app/ingestion/chunk.py` の window/overlap パラメータを変更すると全チャンクの
  content_hash が変わってしまうため、doc/section が一致しページ範囲が重なる場合も
  ヒット扱いにするフォールバックを用意している (`evals/metrics.evaluate_retrieval`)
- **生成側は Ragas の LLM-only メトリクス 3 種のみ**: `Faithfulness` (忠実性),
  `FactualCorrectness` (事実正確性), `LLMContextRecall` (文脈の再現度) を、
  `LangchainLLMWrapper(ChatBedrockConverse(model="jp.anthropic.claude-haiku-4-5-20251001-v1:0",
  region_name="ap-northeast-1", temperature=0))` で採点する。**embeddings 系メトリクス
  (`SemanticSimilarity` 等) は採用しない** — 追加の Bedrock embed 課金を発生させず、
  ragas 側の embeddings wrapper も不要になる
- 当初は `AspectCritic` による引用妥当性メトリクスも検討したが、月次 Budgets
  (10 USD 据え置き) を優先しコストを抑えるため不採用とした。引用の形式検証は
  決定的チェック (`citation_format_valid`: 回答文中の `[n]` がすべて
  `1 <= n <= 出典数` を満たすか) で代替する
- `generation_score = mean(faithfulness, factual_correctness, context_recall)` に
  集約して 1 本のゲート対象にする。LLM judge のスコアは確率的にばらつくため、個別
  メトリクスをそれぞれ閾値判定すると誤検知が頻発する (n=25 程度のサンプルサイズでは
  二項標準誤差が無視できない)。詳細な許容幅の根拠は ADR 0009 を参照
- ragas を import するのは `evals/judge.py` のみに閉じ込める。`evals/metrics.py` /
  `evals/report.py` は依存ゼロの純粋関数で構成し、既存 CI (`ci.yml`, eval グループ
  なしの `uv sync --frozen`) でもユニットテストできるようにしている
- QA データセットの生成には回答者・judge と同じ `jp.anthropic.claude-haiku-4-5-*`
  ではなく `jp.anthropic.claude-sonnet-4-5-20250929-v1:0` を使う。同一モデルで
  問題作成・回答・採点を行うと自己選好バイアスが乗るリスクがあるため、生成だけは
  意図的に別モデルにしている (`evals/build_dataset.py`)

## 影響

- **依存関係の実測上の落とし穴**: `ragas==0.4.3` は `ragas/llms/base.py` で
  `langchain_community.chat_models.vertexai` を無条件 import している。
  `langchain-aws>=1.7` (最新) が要求する `langchain-core>=1.4.7` の下では
  `langchain-community` も 0.4 系に引き上がり、この vertexai サブモジュールが
  廃止されているため `ModuleNotFoundError` で `import ragas` 自体が失敗することを
  実機で確認した。回避策として `langchain-aws` を `>=0.2,<1` に固定し、連動して
  `langchain-core`/`langchain-community` を 0.3 系 (vertexai サブモジュールが
  現存するバージョン) に引き下げることで解決した (`pyproject.toml` の `eval` グループ)。
  ragas か langchain-community 側でこの互換性が修正され次第、`langchain-aws` の
  バージョン上限を見直すこと
- eval 1 回あたりの Bedrock 課金は **実測 $0.65 前後** (25 問、2026-08-15 に
  `evals/measure_cost.py` で計測、内訳は回答生成 $0.09 + judge 3 指標 $0.56、
  1 問あたり $0.026)。⚠️ 当初この行は机上計算の「概算 $1 未満」だったが、
  コストボードの実額と乖離があったため実測に差し替えた
  (`evals/run_eval.py` と同じ `_run_one`/`score_generation` を呼び出し、
  botocore の `_make_api_call` を横取りしてトークン数を集計している。
  単価は AWS Price List API に ap-northeast-1 の Anthropic/Cohere エントリが
  無いため `aws.amazon.com/bedrock/pricing/` からの手動転記。CloudWatch
  `AWS/Bedrock` の実績とも桁が一致することを確認済み)。月次 Budgets
  (10 USD) を圧迫しないよう、データセット規模と judge メトリクス数を絞っている
- 評価は本番 S3 Vectors インデックスに対して read-only で実行するため
  (ADR 0008)、PR が `app/ingestion/chunk.py` / `parse.py` を変更しても
  再取り込みは行われない。取り込みロジックの変更そのものは eval CI では検証できない
  という既知の限界がある (gold 判定の section+page フォールバックは、chunk 境界が
  変わってもチャンクの中身自体は大きく変わらない場合に限り緩和する)
- **既知の制約: recall は 100% にならない設計になっている**。Bedrock User Guide
  (5,000ページ超) には「boto3 クライアントのサービス名は `bedrock-runtime`」
  「コスト配分タグは有効化から最大24時間で反映され遡及適用されない」のような
  定型的な事実が複数の無関係なセクションにまたがって繰り返し記載されている。
  そのため、質問生成時にどれか1チャンクを「gold」として選んでも、検索時には
  同じ事実を含む**別の (それ自体は正しい) チャンク**がヒットし、gold とは
  一致しないことがある (実測で 25 問中 2 問がこのパターンで recall 上「ミス」に
  なることを確認済み)。これはコードのバグではなく原文書の構造に起因する再現性の
  ある挙動であり、baseline はこのノイズを含んだ値を基準にする — 目的は「完璧な
  recall の達成」ではなく「基準からの劣化を検知すること」なので、安定して
  再現するノイズは許容している
