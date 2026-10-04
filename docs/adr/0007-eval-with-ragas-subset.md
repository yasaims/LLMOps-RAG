# ADR 0007: RAG 品質評価は決定的な検索指標と Ragas の LLM-only サブセットで構成する

## ステータス

Accepted

## コンテキスト

本プロジェクトの特徴は、RAG の品質評価を CI に組み込み、劣化があればマージを止めること。
PR ごとに実行するため、実行時間・Bedrock 課金・依存の重さがそのままコストになる。

選択肢:

1. **決定的指標のみ** (recall@k / MRR): 速く安いが、回答の忠実性を測れない
2. **Ragas フル機能**: `SemanticSimilarity` など embeddings 系を含めると追加の embed 課金が発生し、`ragas[all]` では torch 系依存も入る
3. **LLM-as-judge を自作**: claim 分解や NLI 判定の自前実装は車輪の再発明

`ragas==0.4.3` 単体の依存は torch を引かない (`sentence-transformers` / `transformers` は `ragas[all]` のみ)。

## 決定

### 検索: 決定的指標のみ

`recall@1/3/5` と `MRR` を LLM なしで計算する (`evals/metrics.py`)。

- gold 判定は **`SearchResult.id == content_hash`** が主。S3 Vectors の key が content_hash のため `VECTOR_STORE=s3vectors` が前提で、pgvector では成立しない
- 副判定として、doc/section の一致 + ページ範囲の重なりもヒットとする。
  `chunk.py` の window/overlap を変えると全 hash が変わり、副判定がないと全問が不合格になるため

### 生成: Ragas の LLM-only 3 指標

`Faithfulness` / `FactualCorrectness` / `LLMContextRecall` を、回答者と同じ Haiku 4.5 (`temperature=0`) で採点し、
平均を **`generation_score`** として 1 本のゲート対象にする。

- embeddings 系メトリクスは使わない (追加課金と torch 依存の回避)
- 指標ごとに閾値を置くと、LLM judge のばらつきと n=25 の標準誤差で誤検知が多発するため、平均で判定する (許容幅の根拠は [ADR 0009](0009-cicd-quality-gate.md))
- 引用の妥当性は LLM でなく決定的チェックで代替する (`citation_format_valid`: 回答中の `[n]` がすべて `1 <= n <= 出典数`)

### 構成と分離

- ragas を import するのは `evals/judge.py` のみ。`metrics.py` / `report.py` は依存ゼロの純粋関数で、`eval` グループなしの CI でもテストできる
- QA データセットの生成だけは `jp.anthropic.claude-sonnet-4-5-20250929-v1:0` を使う。出題・回答・採点を同一モデルで行うと自己選好バイアスが乗るため

## 影響

- **依存の固定**: `ragas==0.4.3` は `langchain_community.chat_models.vertexai` を無条件に import するが、
  `langchain-core>=1.4` 系の `langchain-community` ではこのモジュールが廃止されており `import ragas` が失敗する。
  そのため `langchain-aws` を `>=0.2,<1` に固定し、`langchain-core` / `langchain-community` を 0.3 系に留めている。ragas が対応したら見直す
- **コスト**: eval 1 回 (25 問) は約 **$0.65** (回答生成 $0.09 + judge $0.56)。
  `evals/measure_cost.py` で再計測できる。単価は公式料金ページから手動転記しており、改定時は手で更新する
- **eval の限界**: 評価は本番 S3 Vectors に対して read-only。`chunk.py` / `parse.py` の変更は再取り込みされないため、eval では検証できない
- **recall は 100% にならない**: ガイドには同じ事実が無関係な複数セクションに重複して書かれており、
  gold と別の (正しい) チャンクがヒットして「ミス」になる問題がある (25 問中 2 問)。これは原文書の構造に由来する安定したノイズ。
  目的は満点ではなく **baseline からの劣化検知** なので、ノイズを含んだ値を baseline にする
