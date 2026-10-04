# ADR 0002: 埋め込みモデルに Cohere Embed v4 を採用

## ステータス

Accepted

## コンテキスト

要件は **日本語で質問し、英語原文の AWS ドキュメントを検索する** クロスリンガル検索。
候補は Titan Text Embeddings v2 (1024 次元) と Cohere Embed v4 (1536 次元)。
`ap-northeast-1` で両方の `invoke_model` が成功することを確認した。
Cohere Embed v4 は 100 以上の言語に対応し、日本語クエリ×英語文書の検索で優位と判断した。

## 決定

**`cohere.embed-v4:0`** (`output_dimension=1536`) を採用する。

- チャンク側は `input_type="search_document"`、クエリ側は `search_query`。
  **この使い分けは必須** で、揃えると検索精度が落ちる
- `app/bedrock.py` で `embed_documents()` / `embed_query()` を別関数に分け、取り違えを防ぐ

## 影響

- `chunks.embedding` は `vector(1536)`、S3 Vectors の `dimension` も 1536
- IAM は `cohere.embed-v4:0` への `bedrock:InvokeModel` を許可する
- 日本語クロスリンガル要件がない場合、HNSW のメモリ効率に優れる Titan v2 が切替候補になる
