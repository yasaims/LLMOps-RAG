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

## Phase 2 以降 (想定)

```mermaid
flowchart LR
    user["利用者"] -->|HTTPS| cf["CloudFront + S3\n(静的フロント)"]
    cf --> apigw["API Gateway"]
    apigw --> lambda["Lambda\n(コンテナイメージ)"]
    lambda --> bedrock[("Amazon Bedrock")]
    lambda --> aurora[("Aurora Serverless v2\n(pgvector)")]
    s3["S3\n(取り込み元PDF)"] --> ingestlambda["取り込みLambda/バッチ"]
    ingestlambda --> aurora

    budgets["AWS Budgets"] -->|閾値超過| stopfn["停止用Lambda\nPutFunctionConcurrency(0)"]
    stopfn -.->|reserved concurrency=0| lambda
```

- Lambda (コンテナイメージ) + API Gateway でサーバーレス化 (アイドル時ゼロ円)
- Aurora Serverless v2 は未使用時に停止する運用
- AWS Budgets → 停止用 Lambda によるコスト暴走対策 (計画書 §7.5)
- Terraform でモジュール分割 (`network` / `api` / `vector-store` / `ingestion` / `observability`)

## モデル選定の要約

| 用途 | モデル | 詳細 |
| --- | --- | --- |
| 埋め込み | `cohere.embed-v4:0` (1536次元) | [ADR 0002](adr/0002-embedding-model-selection.md) |
| 生成 | `jp.anthropic.claude-haiku-4-5-20251001-v1:0` | [ADR 0004](adr/0004-bedrock-inference-profile.md) |
| ベクトルストア | pgvector → Aurora Serverless v2 | [ADR 0001](adr/0001-vector-store-selection.md) |
| チャンク分割 | 見出し認識 + スライディングウィンドウ | [ADR 0003](adr/0003-chunking-strategy.md) |
