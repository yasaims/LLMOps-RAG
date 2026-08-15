# ADR 0005: Phase 2 のベクトルストアを S3 Vectors に変更 (ADR 0001 からの変更)

## ステータス

Accepted (ADR 0001 の「Phase 2 は Aurora Serverless v2」を上書きする)

## コンテキスト

ADR 0001 は Aurora Serverless v2 (pgvector) を Phase 2 の採用候補として決定していたが、
実際に Terraform で AWS 最小構成を設計する段階で、アイドル時コストの試算が
計画書 §7 の目標 (月数百円〜千円) を超えることが判明した。

- RDS/Aurora はデフォルトで VPC 内にしか置けない。Lambda から Bedrock (`bedrock-runtime`)
  を呼び出すには、Lambda も同じ VPC に置いた上で以下いずれかが必要になる
  - NAT Gateway: 常時起動で概算 月5,000円程度
  - Bedrock 用 VPC Interface Endpoint: 常時起動で概算 月1,500円程度
  いずれも「未使用時はほぼ0円」というデモ公開方針 (計画書 §7.5) と相容れない
- **S3 Vectors** (2025年 GA) は VPC 不要で Lambda から直接呼び出せる。
  `aws s3vectors list-vector-buckets --region ap-northeast-1` で
  当該リージョンでの利用可能性を確認済み
- Terraform AWS provider も `aws_s3vectors_vector_bucket` / `aws_s3vectors_index` を
  v6.53.0 (2026-07) 以降でサポート済み (今回使用したのは v6.59.0)

## 決定

**S3 Vectors** を Phase 2 の AWS 上ベクトルストアとして採用する。

- インデックス設定: `data_type = "float32"` / `dimension = 1536` (Cohere Embed v4 と一致) /
  `distance_metric = "cosine"`
- **filterable metadata は 1 ベクトルあたり 2KB 上限**のため、チャンク本文 (`content`) は
  必ず `non_filterable_metadata_keys` に含める
  (`infra/modules/vector-store/main.tf` の `metadata_configuration` ブロック、
  および `app/vectorstore/s3vectors_store.py` の実装で対応)
- アプリ側は `app/vectorstore/` に `VectorStore` プロトコルを導入し、pgvector 実装
  (`pgvector_store.py`) と S3 Vectors 実装 (`s3vectors_store.py`) を差し替え可能にした。
  ローカル開発・CI は引き続き pgvector (docker compose) を使う
- Phase 1 で pgvector にすでに投入済みの埋め込み (Bedrock Embed API 課金済み) は
  `app/ingestion/migrate_to_s3vectors.py` で S3 Vectors へそのまま移送し、
  embed API の再課金を避けた (実績: 6,455 チャンクを再課金なしで移送)

**Aurora Serverless v2 は不採用**とする。理由は上記の VPC 常時課金がデモ公開方針と
相容れないため。ADR 0001 の「pgvector で構築し、Phase 4 で S3 Vectors を検証する」
という記述は、Phase 2 の時点で S3 Vectors 採用に前倒しされた形になる。

## 影響

- IAM: Lambda 実行ロールには VPC 関連の権限が不要になった (`vpc_config` ブロックなし)
- S3 Vectors には pgvector の `documents` テーブルに相当する正規化がないため、
  `service` / `doc` / `source_url` を各ベクトルの metadata に非正規化して持たせている
  (`app/vectorstore/base.py` の `ChunkRecord`)
- `list_vectors` はメタデータでの絞り込みができないため、再取り込み時の重複チェック
  (`existing_hashes`) は全件列挙してクライアント側でフィルタする実装になっている。
  現在の規模 (数千チャンク) では許容範囲だが、対象ドキュメントが大幅に増える場合は
  再検討が必要
- Lambda の IAM ポリシーは `s3vectors:QueryVectors` / `GetVectors` / `GetIndex` のみを許可し、
  `PutVectors` / `ListVectors` は含めない (取り込みは常にローカル/バッチから実行する設計)
