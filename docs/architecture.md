# アーキテクチャ

## Phase 1: ローカル構成

```mermaid
flowchart LR
    subgraph local["ローカル環境"]
        dl["download_docs.py"] -->|PDF 66MB| raw["data/raw/*.pdf\n(gitignore)"]
        raw --> ingest["ingest.py\nparse -> chunk -> embed"]
        ingest -->|"Cohere Embed v4\ninput_type=search_document"| bedrock1[("Amazon Bedrock\nap-northeast-1")]
        ingest --> db[("pgvector\n(docker compose)")]

        client["curl / ブラウザ"] -->|"POST /query\n(日本語質問)"| api["FastAPI\n/query, /healthz"]
        api -->|"Cohere Embed v4\ninput_type=search_query"| bedrock1
        api -->|"top-k コサイン類似検索"| db
        api -->|"Claude Haiku 4.5\n(jp. 推論プロファイル)\nConverse API"| bedrock2[("Amazon Bedrock\nap-northeast-1")]
        api -->|"出典付き日本語回答"| client
    end
```

- **取り込み**: `download_docs.py` → `ingest.py` (parse → chunk → embed → upsert)
- **推論**: FastAPI `/query` が検索 (Retrieve) と生成 (Generate) を実行
- ベクトルストアは pgvector (docker compose)。Bedrock 呼び出しは `app/bedrock.py` に集約

## Phase 2: AWS 最小構成 (Terraform, デプロイ済み)

```mermaid
flowchart LR
    client["curl / ブラウザ"] -->|"HTTPS\nPOST /query, GET /healthz"| apigw["API Gateway (HTTP API)\nスロットリング 2 req/s"]
    apigw --> lambda["Lambda\n(コンテナイメージ, VPC外)"]
    lambda -->|"Cohere Embed v4 / Claude Haiku 4.5"| bedrock[("Amazon Bedrock\nap-northeast-1")]
    lambda -->|"QueryVectors / GetVectors"| s3v[("S3 Vectors\nindex: chunks")]
    lambda -->|"JSON構造化ログ"| logs[("CloudWatch Logs")]

    dev["開発者ローカル"] -->|"upload_docs.py"| docs["S3 (docs バケット)\n取り込み元PDF保管"]
    dev -->|"ingest.py / migrate_to_s3vectors.py\n(PutVectors, ローカル資格情報)"| s3v

    budgets["AWS Budgets\n月次予算アラート"] -->|"80% / 100%超過"| sns["SNS (メール通知)"]
    cwalarm["CloudWatch アラーム\nLambda Errors/Throttles"] --> sns
```

- Lambda (コンテナイメージ, x86_64) + API Gateway HTTP API でサーバーレス化 (アイドル時ゼロ円)
- **ベクトルストアは S3 Vectors** (Aurora Serverless v2 からの変更。[ADR 0005](adr/0005-s3-vectors-for-phase2.md))。
  VPC 不要のため NAT/VPC エンドポイントの常時課金が発生しない
- 取り込み (embed + PutVectors) はローカル/バッチから実行し、Lambda の実行時パスは
  検索専用 (最小権限の IAM)。[ADR 0006](adr/0006-lambda-container-http-api.md)
- AWS Budgets (月次) + CloudWatch アラーム (Errors/Throttles) → SNS メール通知。
  Budgets 超過時の自動停止 (計画書 §7.5) は Phase 4
- Terraform 構成: `infra/bootstrap` (tfstate用S3 + ECR, 一度きり) /
  `infra/modules/{vector-store,ingestion,api,observability}` / `infra/envs/dev`

## モデル選定の要約

| 用途 | モデル | 詳細 |
| --- | --- | --- |
| 埋め込み | `cohere.embed-v4:0` (1536次元) | [ADR 0002](adr/0002-embedding-model-selection.md) |
| 生成 | `jp.anthropic.claude-haiku-4-5-20251001-v1:0` | [ADR 0004](adr/0004-bedrock-inference-profile.md) |
| ベクトルストア | pgvector (Phase 1) → S3 Vectors (Phase 2) | [ADR 0001](adr/0001-vector-store-selection.md) / [ADR 0005](adr/0005-s3-vectors-for-phase2.md) |
| チャンク分割 | 見出し認識 + スライディングウィンドウ | [ADR 0003](adr/0003-chunking-strategy.md) |
| 実行基盤 | Lambda (コンテナイメージ) + API Gateway HTTP API | [ADR 0006](adr/0006-lambda-container-http-api.md) |
