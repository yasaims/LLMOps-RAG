# LLMOps-RAG

[![CI](https://github.com/yasaims/LLMOps-RAG/actions/workflows/ci.yml/badge.svg)](https://github.com/yasaims/LLMOps-RAG/actions/workflows/ci.yml)

AWS 公式ドキュメント (Bedrock ユーザーガイド) に対する日本語 Q&A RAG システム。
評価 (eval) を CI に組み込む LLMOps 基盤構築の練習用ポートフォリオプロジェクト。

構成図: [docs/architecture.md](docs/architecture.md)

## 技術選定の要約

- **埋め込み**: `cohere.embed-v4:0` (日本語質問 × 英語原文のクロスリンガル検索) — [ADR 0002](docs/adr/0002-embedding-model-selection.md)
- **生成**: `jp.anthropic.claude-haiku-4-5-20251001-v1:0` (推論プロファイル経由) — [ADR 0004](docs/adr/0004-bedrock-inference-profile.md)
- **ベクトルストア**: pgvector (Phase 2 で Aurora Serverless v2 へ) — [ADR 0001](docs/adr/0001-vector-store-selection.md)
- **チャンク分割**: 見出し認識 + スライディングウィンドウ — [ADR 0003](docs/adr/0003-chunking-strategy.md)

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

## 出典・ライセンス

- ドキュメント原文は [AWS Bedrock ユーザーガイド](https://docs.aws.amazon.com/pdfs/bedrock/latest/userguide/bedrock-ug.pdf) (Amazon Web Services) を出典とする
- PDF 原文はリポジトリにコミットしない。`download_docs.py` で実行時に取得する
- 回答には出典 (サービス名 / ドキュメント名 / セクション / ページ番号) を必ず含める

## コスト設計

- Bedrock は従量課金、pgvector はローカル/Aurora 停止運用でコストを抑える
- デモ公開時のコストガード (usage plan・自動停止) の設計は Phase 4 で `docs/architecture.md` に追記予定
