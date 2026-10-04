# ADR 0006: Lambda はコンテナイメージ、API は HTTP API を採用

## ステータス

Accepted

## コンテキスト

FastAPI アプリを AWS 上でサーバーレス実行する。論点は Lambda のパッケージング方式と API Gateway の種別。

- zip 方式は Linux 向け依存の解決が必要で、Windows 開発機からのビルドは環境差異のリスクがある
- コンテナイメージは `docker build --platform linux/amd64` でターゲット環境を再現できる。ECR 保管費は月数円〜数十円
- HTTP API は REST API よりリクエスト単価が約 70% 安く、Lambda プロキシ統合 (`payload_format_version = "2.0"`) も単純。
  WAF 連携などは REST API が上だが、`/query` と `/healthz` の 2 ルートでは不要

## 決定

- Lambda: **コンテナイメージ** (`public.ecr.aws/lambda/python:3.12` ベース、`x86_64`)
- API Gateway: **HTTP API**、ステージ `$default` (auto_deploy)
- 乱用対策: `default_route_settings` でスロットリング (`rate_limit = 2` / `burst_limit = 5`)
- ログ: Lambda と API Gateway アクセスログでロググループを分け、保持 14 日
- Bedrock の IAM: 推論プロファイル ([ADR 0004](0004-bedrock-inference-profile.md)) 経由のため、
  プロファイル ARN とルーティング先の基盤モデル ARN (リージョン `*`) の両方を Resource に含める
- **`reserved_concurrent_executions` は 0 を指定して予約なしにする**。
  対象アカウントの Lambda 同時実行数上限は全体で 10 で、予約後も未予約枠が 10 以上必要という AWS の制約により、
  1 以上の予約はすべて `PutFunctionConcurrency` で失敗する。
  `infra/modules/api` は 0 のとき `null` にフォールバックする。上限が大きいアカウントでは 1 以上を指定すれば暴走ガードになる

## 影響

- 同時実行数ガードが効かない環境では、**API Gateway のスロットリングが唯一の乱用対策** になる
- WAF による高度なレート制限や、コスト超過時の自動停止は備えていない ([ADR 0011](0011-demo-frontend-cloudfront.md))
