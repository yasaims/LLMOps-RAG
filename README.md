# LLMOps-RAG

[![CI](https://github.com/yasaims/LLMOps-RAG/actions/workflows/ci.yml/badge.svg)](https://github.com/yasaims/LLMOps-RAG/actions/workflows/ci.yml)
[![Eval](https://github.com/yasaims/LLMOps-RAG/actions/workflows/eval.yml/badge.svg)](https://github.com/yasaims/LLMOps-RAG/actions/workflows/eval.yml)
[![Terraform Apply](https://github.com/yasaims/LLMOps-RAG/actions/workflows/terraform-apply.yml/badge.svg)](https://github.com/yasaims/LLMOps-RAG/actions/workflows/terraform-apply.yml)

AWS 公式ドキュメント (Bedrock ユーザーガイド) に対する日本語 Q&A RAG システム。
評価 (eval) を CI に組み込む LLMOps 基盤構築の練習用ポートフォリオプロジェクト。

構成図: [docs/architecture.md](docs/architecture.md)

## 技術選定の要約

- **埋め込み**: `cohere.embed-v4:0` (日本語質問 × 英語原文のクロスリンガル検索) — [ADR 0002](docs/adr/0002-embedding-model-selection.md)
- **生成**: `jp.anthropic.claude-haiku-4-5-20251001-v1:0` (推論プロファイル経由) — [ADR 0004](docs/adr/0004-bedrock-inference-profile.md)
- **ベクトルストア**: pgvector (Phase 1 ローカル) / S3 Vectors (Phase 2 AWS) — [ADR 0001](docs/adr/0001-vector-store-selection.md) / [ADR 0005](docs/adr/0005-s3-vectors-for-phase2.md)
- **チャンク分割**: 見出し認識 + スライディングウィンドウ — [ADR 0003](docs/adr/0003-chunking-strategy.md)
- **実行基盤**: Lambda (コンテナイメージ) + API Gateway HTTP API — [ADR 0006](docs/adr/0006-lambda-container-http-api.md)
- **評価**: 検索は決定的指標 (recall@k/MRR)、生成は Ragas + Bedrock judge — [ADR 0007](docs/adr/0007-eval-with-ragas-subset.md)
- **CI/CD**: GitHub OIDC (長期キーなし) + plan/apply/eval の 3 ロール — [ADR 0008](docs/adr/0008-github-oidc-iam-roles.md) / [ADR 0009](docs/adr/0009-cicd-quality-gate.md)

IAM 権限の全体像 (誰が何をできるか) は [docs/iam-permissions.md](docs/iam-permissions.md) にまとめている。

## ローカル起動手順

```bash
uv sync
cp .env.example .env

docker compose up -d                                   # pgvector 起動
uv run python -m app.ingestion.download_docs            # PDF 取得 (data/raw/, 非コミット)
uv run python -m app.ingestion.ingest --doc bedrock-ug --dry-run  # 規模確認
uv run python -m app.ingestion.ingest --doc bedrock-ug   # 埋め込み投入

uv run uvicorn app.api.main:app --reload
curl -s localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"question":"Bedrock でモデルアクセスを有効にする手順は？"}'
```

## テスト

```bash
uv run ruff check .
uv run pytest -m "not integration"
```

## 評価 (Phase 3)

```bash
uv sync --group eval
uv run python -m evals.run_eval --dataset evals/datasets/bedrock-ug-qa.jsonl --baseline evals/baseline.json
```

PR ごとに `eval.yml` が本番 S3 Vectors に対して RAG 品質を評価し、`evals/baseline.json` からの
リグレッションがあればマージをブロックする。詳細は [ADR 0007](docs/adr/0007-eval-with-ragas-subset.md)。

## AWS へのデプロイ

Phase 3 以降、`main` へのマージで `terraform-apply.yml` がイメージ build & push →
`terraform apply` → `/healthz` スモークテストまで自動実行する ([ADR 0009](docs/adr/0009-cicd-quality-gate.md))。
以下は初回セットアップ・手動デプロイ用の手順:

```bash
# 1. tfstate 用 S3 + ECR + GitHub OIDC ロール (一度だけ)
terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply

# 2. Lambda 用イメージを build & push
./scripts/push_image.ps1

# 3. cp infra/envs/dev/{backend.hcl,terraform.tfvars}.example → 値を埋めて apply
terraform -chdir=infra/envs/dev init -backend-config=backend.hcl
terraform -chdir=infra/envs/dev apply

# 4. データ投入 (pgvector に投入済みなら embed API 再課金なしで移送)
uv run python -m app.ingestion.migrate_to_s3vectors --doc bedrock-ug
uv run python -m app.ingestion.upload_docs --doc bedrock-ug  # 出典PDFの保管

curl "$(terraform -chdir=infra/envs/dev output -raw api_endpoint)healthz"
```

## 出典・ライセンス

- ドキュメント原文は [AWS Bedrock ユーザーガイド](https://docs.aws.amazon.com/pdfs/bedrock/latest/userguide/bedrock-ug.pdf) (Amazon Web Services) を出典とする
- PDF 原文はリポジトリにコミットしない。`download_docs.py` で実行時に取得する
- 回答には出典 (サービス名 / ドキュメント名 / セクション / ページ番号) を必ず含める

## コスト設計

- Bedrock は従量課金。ベクトルストアは S3 Vectors (VPC 不要、アイドル時ほぼ0円) — [ADR 0005](docs/adr/0005-s3-vectors-for-phase2.md)
- Lambda + API Gateway もアイドル時ゼロ円。API Gateway のスロットリング (2 req/s) で乱用を抑制
- AWS Budgets (月次) + CloudWatch アラームで異常時にメール通知
- デモ公開時のさらなるコストガード (Budgets 超過での自動停止など) は Phase 4 で `docs/architecture.md` に追記予定
