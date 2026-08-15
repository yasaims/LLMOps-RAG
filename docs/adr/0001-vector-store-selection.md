# ADR 0001: ベクトルストアに Aurora Serverless v2 (pgvector) を選定

## ステータス

Accepted, Phase 2 で一部変更 ([ADR 0005](0005-s3-vectors-for-phase2.md) を参照)。
Phase 1 はローカル pgvector で検証。Phase 2 の AWS 上ベクトルストアは
当初この ADR で決定した Aurora Serverless v2 ではなく S3 Vectors を採用した
(VPC 常時課金がデモ公開方針と相容れないため)。以下の内容は決定時点の記録として残す。

## コンテキスト

RAG のベクトル検索基盤として、以下を比較した。

- **Aurora Serverless v2 (pgvector)**
- **OpenSearch Serverless**
- **S3 Vectors** (2025年に GA した新サービス)

本プロジェクトはポートフォリオ用のデモであり、常時アクセスされるプロダクションworkloadではない。
そのため「未使用時はコストがほぼゼロになる」ことを最優先の評価軸とした。

## 決定

**Aurora Serverless v2 (pgvector 拡張)** を採用する。

- SQL でメタデータ (`service` / `doc` / `section`) とベクトル検索を単一クエリで結合できる
- Serverless v2 は最小 ACU まで縮小でき、未使用時は `aws rds stop-db-cluster` で完全停止できる
- pgvector は HNSW インデックスをサポートしており、1536 次元 (Cohere Embed v4) でも利用可能

**OpenSearch Serverless は不採用**とする。理由は最小課金でも常時 OCU 課金が発生し、
「アイドル時ゼロ円」というデモ公開の方針 (計画書 §7.5) と相容れないため。

**S3 Vectors は代替候補として保留**する。2025年 GA の新サービスで真にスケールtoゼロだが、
本プロジェクトでは `service` / `doc` / `section` によるメタデータフィルタと
ベクトル検索を組み合わせたクエリの実績を優先し、まずは pgvector で構築する。
Phase 4 (コスト最適化) で S3 Vectors への切り替えを検証する余地を残す。

## 影響

- Phase 1: docker compose の `pgvector/pgvector:pg17` イメージでローカル検証
- Phase 2: Terraform で Aurora Serverless v2 クラスタを構築し、停止運用の仕組みを合わせて用意する
