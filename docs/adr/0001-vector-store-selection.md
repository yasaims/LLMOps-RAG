# ADR 0001: ローカル開発のベクトルストアに pgvector を採用

## ステータス

Accepted。AWS 上のベクトルストアは [ADR 0005](0005-s3-vectors-vector-store.md) で S3 Vectors に決定した。
本 ADR は **ローカル開発・CI 用ストア** の選定と、AWS 側候補の比較を記録する。

## コンテキスト

候補は Aurora Serverless v2 (pgvector)、OpenSearch Serverless、S3 Vectors。
本プロジェクトはデモ用途であり、**未使用時のコストがほぼゼロであること**を最優先の評価軸とした。

## 決定

ローカル開発・CI は **pgvector** (`pgvector/pgvector:pg17`, docker compose) を使う。

- SQL でメタデータ (`service` / `doc` / `section`) 絞り込みとベクトル検索を 1 クエリで書ける
- HNSW インデックスが 1536 次元 (Cohere Embed v4) でも使える

AWS 上の候補の評価:

- **Aurora Serverless v2**: VPC 内配置が前提で、NAT / VPC Endpoint の常時課金が避けられない。不採用
- **OpenSearch Serverless**: 最小構成でも OCU の常時課金が発生する。不採用
- **S3 Vectors**: スケール to ゼロで VPC 不要。採用 ([ADR 0005](0005-s3-vectors-vector-store.md))

## 影響

- アプリは `VectorStore` プロトコルで pgvector と S3 Vectors を差し替える
