# ADR 0005: AWS 上のベクトルストアに S3 Vectors を採用

## ステータス

Accepted ([ADR 0001](0001-vector-store-selection.md) の AWS 側候補比較を確定させる)

## コンテキスト

Aurora Serverless v2 (pgvector) は VPC 内にしか置けない。Lambda から Bedrock を呼ぶには Lambda も VPC に入れ、次のいずれかが常時必要になる。

- NAT Gateway: 月 5,000 円程度
- Bedrock 用 VPC Interface Endpoint: 月 1,500 円程度

どちらも「未使用時はほぼ 0 円」という方針に合わない。
S3 Vectors は VPC 不要で Lambda から直接呼べ、`ap-northeast-1` で利用可能。
Terraform は `aws_s3vectors_vector_bucket` / `aws_s3vectors_index` (AWS provider 6.53.0 以降) で管理できる。

## 決定

**S3 Vectors** を採用する。

- インデックス: `float32` / `dimension = 1536` / `cosine`
- **filterable metadata は 1 ベクトル 2KB 上限** のため、チャンク本文 (`content`) は必ず `non_filterable_metadata_keys` に置く
  (`infra/modules/vector-store/main.tf` と `app/vectorstore/s3vectors_store.py` を対応させる)
- `VectorStore` プロトコルで pgvector 実装と差し替える。ローカル開発・CI は pgvector
- 既存の pgvector 埋め込みは `app/ingestion/migrate_to_s3vectors.py` で移送し、embed の再課金を避ける (6,455 チャンク)

## 影響

- Lambda に VPC 関連の権限・設定が不要
- 正規化テーブルがないため、`service` / `doc` / `source_url` を各ベクトルの metadata に非正規化して持つ (`ChunkRecord`)
- `list_vectors` はメタデータ絞り込みができず、再取り込み時の重複チェック (`existing_hashes`) は全件列挙後にクライアントで絞る。
  数千チャンク規模では許容できるが、対象ドキュメントが大幅に増える場合は見直す
- **Lambda の IAM は `QueryVectors` / `GetVectors` / `GetIndex` のみ**。`PutVectors` / `ListVectors` は許可せず、取り込みは常にローカル/バッチから行う
