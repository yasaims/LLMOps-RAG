# ADR 0002: 埋め込みモデルに Cohere Embed v4 を選定 (Titan v2 案からの変更)

## ステータス

Accepted

## コンテキスト

当初の計画書 (`.vscode/開発計画書.md`) では埋め込みモデルとして Titan Embeddings を想定していた。
実装開始時点でユーザーと言語方針を確認したところ、
「日本語で質問し、英語原文の AWS ドキュメントを検索して日本語で回答する」
クロスリンガル検索が要件であることが確定した。

`ap-northeast-1` で以下 2 モデルへの実際の `invoke_model` 呼び出しを検証した。

| モデル | 次元数 | 結果 |
| --- | --- | --- |
| `amazon.titan-embed-text-v2:0` | 1024 | 成功 |
| `cohere.embed-v4:0` | 1536 (`output_dimension` 指定可) | 成功 |

Titan Embeddings は多言語対応を謳っているが、日本語クエリと英語文書間の
クロスリンガル検索精度の実績は Cohere Embed v4 (multilingual 対応、100+ 言語) の方が高い。

## 決定

**`cohere.embed-v4:0`** を採用する (`output_dimension=1536`)。

- チャンク (文書) 側は `input_type="search_document"`
- 検索クエリ側は `input_type="search_query"`
- この使い分けは **必須**。両者を揃えると Cohere の埋め込み空間の設計上、
  検索精度が明確に落ちる。実装 (`app/bedrock.py`) では `embed_documents()` /
  `embed_query()` を別関数に分離し、呼び出し側が誤って混同できないようにした

## 影響

- DB スキーマの `chunks.embedding` は `vector(1536)` (Titan v2 の 1024 ではない)
- Phase 2 の Bedrock IAM ポリシーは `cohere.embed-v4:0` への `bedrock:InvokeModel` を許可する
- Titan v2 は次元数が小さく HNSW インデックスのメモリ効率で有利なため、
  日本語クロスリンガル要件がないユースケースでは切替候補として残す
