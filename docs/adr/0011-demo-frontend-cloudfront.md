# ADR 0011: デモフロントは CloudFront 1 ディストリビューション + 2 オリジンで CORS を発生させない構成にする

## ステータス

Accepted

## コンテキスト

静的フロントを S3 + CloudFront で公開する。フロントと API (execute-api) が別オリジンだと CORS プリフライト (`OPTIONS`) が必要になり、Lambda 側のハンドリングと `Access-Control-Allow-Origin` 付与が要る。`app/config.py` の `cors_allow_origins` は空のままである。

## 決定

- **単一オリジン化**: CloudFront 1 ディストリビューションに S3 と API Gateway の 2 オリジンを置く。`ordered_cache_behavior` で `/query` `/healthz` を API Gateway へ、その他を S3 へ振る。`cors_allow_origins` と `app/api/main.py` の CORS ミドルウェアは変更不要
  - default behavior: S3 + `Managed-CachingOptimized`
  - `/query` `/healthz`: API Gateway + `Managed-CachingDisabled` + `Managed-AllViewerExceptHostHeader`
- **S3 は OAC で非公開**: 静的ウェブサイトホスティングは HTTP のみで CloudFront の HTTPS 配信と相性が悪い。`aws_cloudfront_origin_access_control` (sigv4/always) を使い、バケットポリシーで CloudFront の `GetObject` のみを `AWS:SourceArn` (このディストリビューション限定) で許可する
- **Host ヘッダーは転送しない**: execute-api は CloudFront ドメインを Host として受けると SNI 不一致で 403 を返すため、Host のみ除外するマネージドポリシー `Managed-AllViewerExceptHostHeader` を使う
- **`custom_error_response` (403/404 → `index.html`) は入れない**: オリジンを区別せず HTTP ステータスにのみ反応するため、API の 404 まで `index.html` にすり替わり、クライアントがエラーを判別できなくなる。単一 `index.html` で SPA ルーティングの利点も薄い
- `infra/modules/frontend`: S3 (非公開, force_destroy) + OAC + CloudFront (`price_class = "PriceClass_200"`, `wait_for_deployment = false`)
- 静的ファイル (`web/index.html` / `web/app.js` / `web/style.css`) は `aws_s3_object` で管理し、`etag = filemd5(...)` で変更時のみ再アップロードする。`terraform-apply.yml` が apply 後に `aws cloudfront create-invalidation --paths "/*"` を実行する (月 1,000 パスまで無料)
- `web/app.js` は相対パス `fetch("/query")` を使い、429 を「デモのレート制限」として明示的に扱う
- API Gateway のスロットリングを `2 req/s` から **`1 req/s`** (バースト 5 から 3) に下げた (`infra/envs/dev/variables.tf`)
- `infra/bootstrap` の apply ロールに CloudFront 権限を追加した。`Create*`/`List*`/`Get*` はリソースタイプ未定義で `Resource="*"` が必須、`Update*`/`Delete*`/`CreateInvalidation` 等は `distribution/*`・`origin-access-control/*` にスコープできる。web バケットは既存 statement に相乗りし `S3BucketsManage` に改名した。詳細は `docs/iam-permissions.md`

## 影響

- **乱用対策はスロットリング (ステージ全体、IP 単位ではない)・Budgets の事後メール通知・CloudWatch アラーム ([ADR 0010](0010-observability-dashboard.md) の `api-request-spike`) のみで、自動遮断はしない。** 悪意ある連続リクエストは理論上 1 日 86,400 リクエストまで通り得る。Budgets 超過時の Lambda concurrency=0 自動停止や WAF は未導入
- ディストリビューションの作成・伝播に数分〜十数分かかる (`wait_for_deployment = false` のため apply はブロックしない)。マージ直後は `demo_url` に反映されないことがある
- カスタムドメイン (Route 53 + ACM) は無く、`*.cloudfront.net` のデフォルト証明書による HTTPS のみ
