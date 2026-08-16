# ADR 0010: CloudWatch ダッシュボードは既存メトリクス + Logs Insights のみで構成し、詳細メトリクス/EMF は使わない

## ステータス

Accepted

## コンテキスト

計画書 §6 の Phase 4 は「監視ダッシュボード」を掲げている。Phase 2 時点で CloudWatch アラーム
(Lambda `Errors`/`Throttles`) は既にあるが、可視化する画面が無い。また `app/logging_config.py`
は「Phase 4 の監視ダッシュボードの土台」というコメント付きで、リクエストごとに
`latency_ms` / `input_tokens` / `output_tokens` / `top_score` を JSON 1 行のログとして
既に出力しており、これを使わない手はない。

検討した論点は 2 つ:

### (1) API Gateway のメトリクス粒度: API 全体 vs ルート単位 (詳細メトリクス)

HTTP API の `detailed_metrics_enabled` を有効にするとルート単位 (`POST /query` /
`GET /healthz` を分離) でメトリクスが取れるが、これは CloudWatch のカスタムメトリクス扱いになり
$0.30/メトリクス/月の課金が発生する。本プロジェクトはルートが 2 つしかなく実用上の価値が薄い
一方、月次予算が 10 USD (`monthly_budget_usd`) と小さいため見送った。API レベルの
`Count`/`4xx`/`5xx`/`Latency`/`IntegrationLatency` は既定で無料。

### (2) トークン数・検索スコアの可視化: EMF (カスタムメトリクス) vs Logs Insights

`latency_ms` 等をカスタムメトリクス化する方法として Embedded Metric Format (EMF) も検討したが:

- EMF もカスタムメトリクス課金が発生する (ディメンションの組み合わせ数に応じて増える)
- 既に JSON 構造化ログとして出力済みのフィールドを Logs Insights で集計すれば、
  追加のコード変更・追加課金なしで同じ情報が得られる
- ダッシュボードは自分がポートフォリオを見るときと定期的な健全性確認用途であり、
  秒単位のリアルタイム性は不要。Logs Insights の集計遅延 (数十秒〜数分) は許容範囲

→ **EMF は導入せず、既存ログを `log` ウィジェット (Logs Insights クエリ) で集計する。**

## 決定

`infra/modules/observability/main.tf` に以下を追加:

- `aws_cloudwatch_dashboard.main` (`${project}-${env}`)。ウィジェット構成:
  - Lambda (Invocations/Errors/Throttles/Duration の avg・p90・p99)
  - API Gateway (Count/4xx/5xx/Latency/IntegrationLatency)
  - Bedrock chat モデル (Invocations/InvocationLatency/InvocationThrottles/トークン数)
  - Bedrock embed モデル (Invocations/InvocationLatency/InvocationThrottles)
  - `query_completed` ログの Logs Insights 集計 (1時間ビンで件数・レイテンシ・トークン・
    平均 top_score)
- アラーム追加 (既存の Lambda Errors/Throttles と同じパターン):
  - `api-5xx`: API Gateway 5xx が5分で5件超
  - `bedrock-throttles`: Bedrock (chat) の `InvocationThrottles` が5分で10件超。
    CLAUDE.md に記録済みの「ローカル eval と CI eval のクォータ競合で 324 件発生」の
    ような事象を検知する
  - `api-request-spike`: API Gateway `Count` が5分で閾値 (既定 300、
    `abuse_detection_request_threshold`) 超。デモ公開 (Phase 4) の乱用検知 tripwire。
    **検知のみで自動遮断はしない** (自動停止は今回見送り。理由は本 ADR の「影響」参照)
- CloudWatch ダッシュボードは 3 枚まで無料、アラームは $0.10/個/月なので追加コストは
  実質ゼロ (アラーム3本で月 $0.30)

ダッシュボードが参照する `ApiId`/`Stage` は `infra/modules/api` の新規 output
(`api_id`/`stage_name`) から、ロググループ名は既存の `log_group_name` output から取得する。

## 影響

- API Gateway のルート単位の内訳 (`/query` と `/healthz` の切り分け) はダッシュボード上では
  見えない。両ルートともレイテンシ特性が大きく異なる (`/healthz` はほぼ即時) ため、必要になれば
  詳細メトリクスを有効化する判断材料としてこの ADR を参照する
- `api-request-spike` アラームは検知のみで自動遮断を行わない。デモ公開時の恒久対策
  (Budgets → Lambda concurrency=0 の自動停止、または CloudFront + WAF のレートベースルール)
  は Phase 4 の残タスクとして未着手 ([README](../../README.md) のコスト設計セクション参照)
