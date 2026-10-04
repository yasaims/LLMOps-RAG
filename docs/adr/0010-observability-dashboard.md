# ADR 0010: CloudWatch ダッシュボードは既存メトリクス + Logs Insights のみで構成し、詳細メトリクス/EMF は使わない

## ステータス

Accepted

## コンテキスト

Lambda `Errors`/`Throttles` のアラームはあるが可視化する画面が無い。`app/logging_config.py` は
リクエストごとに `latency_ms` / `input_tokens` / `output_tokens` / `top_score` を JSON 1 行で出力済みであり、これを活用できる。
月次予算が 10 USD (`monthly_budget_usd`) と小さいため、追加課金の有無が判断の軸になる。

- **API Gateway の粒度**: `detailed_metrics_enabled` を有効にするとルート単位で取れるが、カスタムメトリクス扱いで $0.30/メトリクス/月かかる。ルートは 2 つしかなく価値が薄い。API レベルの `Count`/`4xx`/`5xx`/`Latency`/`IntegrationLatency` は既定で無料
- **トークン数・スコアの可視化**: EMF もカスタムメトリクス課金 (ディメンション組み合わせ数に比例) が発生する。構造化ログを Logs Insights で集計すれば、コード変更も追加課金も不要。用途は定期的な健全性確認なので、集計遅延 (数十秒〜数分) は許容できる

## 決定

**詳細メトリクスも EMF も使わず、標準メトリクスと既存ログの Logs Insights (`log` ウィジェット) で構成する。** `infra/modules/observability/main.tf` に以下を置く。

- `aws_cloudwatch_dashboard.main` (`${project}-${env}`)
  - Lambda (Invocations/Errors/Throttles/Duration の avg・p90・p99)
  - API Gateway (Count/4xx/5xx/Latency/IntegrationLatency)
  - Bedrock chat (Invocations/InvocationLatency/InvocationThrottles/トークン数) と embed (トークン以外同様)
  - `query_completed` ログの 1 時間ビン集計 (件数・レイテンシ・トークン・平均 top_score)
- アラーム (既存の Lambda Errors/Throttles と同パターン)
  - `api-5xx`: 5xx が 5 分で 5 件超
  - `bedrock-throttles`: chat の `InvocationThrottles` が 5 分で 10 件超。ローカル eval と CI eval のクォータ競合 (324 件発生) のような事象の検知用
  - `api-request-spike`: `Count` が 5 分で閾値超 (既定 300、`abuse_detection_request_threshold`)。デモ公開の乱用検知用。**検知のみで自動遮断はしない**
- ダッシュボードの `ApiId`/`Stage` は `infra/modules/api` の output (`api_id`/`stage_name`)、ロググループ名は `log_group_name` output から取得する
- 追加コストは実質ゼロ (ダッシュボードは 3 枚まで無料、アラーム 3 本で月 $0.30)

## 影響

- ダッシュボード上で `/query` と `/healthz` を切り分けられない。両者のレイテンシ特性は大きく異なるため、必要になれば詳細メトリクス有効化を再検討する
- **現状は異常を検知しても自動で遮断しない。** Budgets 連動の Lambda concurrency=0 や CloudFront + WAF のレートベースルールは未導入 ([ADR 0011](0011-demo-frontend-cloudfront.md) 参照)
