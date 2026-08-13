# ADR 0006: Lambda はコンテナイメージ、API は HTTP API (API Gateway v2) を採用

## ステータス

Accepted

## コンテキスト

Phase 2 で FastAPI アプリを AWS 上でサーバーレス実行する構成を検討した。
論点は (1) Lambda のパッケージング方式、(2) API Gateway の種別、の2つ。

### (1) パッケージング: コンテナイメージ vs zip

- zip 方式は `uv pip install --python-platform x86_64-manylinux2014` で Linux 向け
  依存関係を固める必要があり、Windows 開発機からのビルドで環境差異のリスクがある
- コンテナイメージ方式 (`public.ecr.aws/lambda/python:3.12` ベース) は
  `docker build --platform linux/amd64` で確実にターゲット環境を再現できる。
  ECR の保管コストは月数円〜数十円程度で、計画書の許容コストの範囲内
- 計画書 (§5 リポジトリ構成) も「コンテナイメージ」を明示していた

→ **コンテナイメージ**を採用。`Dockerfile` (リポジトリ直下) と
`scripts/push_image.ps1` (ECR ログイン→build→push の薄いラッパー) で構成する。

### (2) API Gateway: REST API (v1) vs HTTP API (v2)

- HTTP API は REST API よりリクエスト単価が安く (目安 約70%減)、
  Lambda プロキシ統合もシンプル (`payload_format_version = "2.0"`)
- WAF 連携や高度なリクエスト検証は REST API の方が充実しているが、
  Phase 2 時点では `/query` `/healthz` の 2 ルートのみで要件を満たす

→ **HTTP API** (`aws_apigatewayv2_*`) を採用。

## 決定

- Lambda: `package_type = "Image"`, `architectures = ["x86_64"]`
- API Gateway: HTTP API、ステージは `$default` (auto_deploy)
- 乱用対策として `default_route_settings` にスロットリング
  (`throttling_rate_limit = 2` req/s, `throttling_burst_limit = 5`) を設定
- **同時実行数の予約 (`reserved_concurrent_executions`) は今回のデプロイ先アカウントでは未設定 (null)**。
  このアカウントは `aws lambda get-account-settings` で確認したところ
  `ConcurrentExecutions = 10` (アカウント全体の上限が学習用途向けに絞られている) であり、
  AWS の制約上「予約後も unreserved が最低 10 以上残ること」が必須のため、
  1以上のいかなる予約値を設定しても `PutFunctionConcurrency` が失敗する。
  `infra/modules/api` の `reserved_concurrency` 変数は 0 の場合に
  `reserved_concurrent_executions = null` (予約なし、共有プール使用) にフォールバックする
  実装にし、`infra/envs/dev/terraform.tfvars` で `lambda_reserved_concurrency = 0` を指定した。
  同時実行数の上限が大きいアカウントにデプロイする場合は 1 以上を指定すれば
  同時実行数によるコスト暴走ガードとして機能する
- ログ: Lambda 用ロググループ (`/aws/lambda/<function>`) と API Gateway アクセスログ用
  ロググループ (`/aws/apigateway/<function>`) を分け、いずれも保持期間 14 日
- Bedrock 呼び出しの IAM: Converse/InvokeModel は同一の `bedrock:InvokeModel` アクションで
  許可される。推論プロファイル (ADR 0004) 経由の呼び出しのため、
  推論プロファイル ARN とルーティング先の基盤モデル ARN (region を `*` にしたもの) の
  両方を Resource に含める必要がある

## 影響

- WAF によるより高度なレート制限や Bot 対策、コスト暴走時の自動停止
  (Budgets → Lambda concurrency=0、計画書 §7.5) は Phase 4 で扱う
- 同時実行数によるガードが効かないアカウント制約下では、API Gateway のスロットリングが
  実質的な唯一の乱用対策になる点に留意する
