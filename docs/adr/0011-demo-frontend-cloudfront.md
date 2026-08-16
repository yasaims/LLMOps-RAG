# ADR 0011: デモフロントは CloudFront 1 ディストリビューション + 2 オリジンで CORS を発生させない構成にする

## ステータス

Accepted

## コンテキスト

計画書 §7.5 は「S3 + CloudFront で静的フロントを公開する」ことを想定している。
バックエンド (`app/config.py` の `cors_allow_origins`) は「Phase 4 のフロント用」として
CORS 設定を空のまま予約してあった。素直に実装すると、フロント (CloudFront ドメイン) と
API (execute-api ドメイン) が別オリジンになり、ブラウザからの `fetch` は CORS プリフライト
(`OPTIONS`) を経由する必要が生じ、Lambda 側にプリフライトのハンドリングと
`Access-Control-Allow-Origin` の付与が要る。

検討した論点は 3 つ:

### (1) CORS を発生させるか、単一オリジンに見せるか

CloudFront はオリジンごとにパスパターンでルーティングできる (`ordered_cache_behavior`)。
`/query` と `/healthz` を API Gateway オリジンへ、それ以外を S3 オリジンへ向ければ、
ブラウザからは常に同一オリジン (CloudFront ドメイン) への呼び出しに見える。

→ **CloudFront 1 ディストリビューションに S3 と API Gateway の 2 オリジンをぶら下げる。**
`cors_allow_origins` は空のままでよく、`app/api/main.py` の CORS ミドルウェアにも変更は不要。

### (2) S3 バケットの公開方法: 静的ウェブサイトホスティング vs Origin Access Control (OAC)

`aws_s3_bucket_website_configuration` によるパブリック公開は HTTPS 非対応 (S3 ウェブサイト
エンドポイントは HTTP のみ) で、CloudFront 経由の HTTPS 配信と相性が悪い。

→ **OAC** (`aws_cloudfront_origin_access_control`, signing: sigv4/always) を採用し、
S3 バケットは `infra/modules/ingestion` の docs バケットと同様に完全非公開のまま、
バケットポリシーで CloudFront サービスプリンシパルの `GetObject` のみを
`AWS:SourceArn` (このディストリビューション限定) で許可する。

### (3) API オリジンへのリクエスト転送で Host ヘッダーをどう扱うか

CloudFront のデフォルトのオリジンリクエストポリシーは Host ヘッダーをそのまま転送するが、
API Gateway の execute-api エンドポイントは CloudFront のドメイン名を Host として受け取ると
SNI 不一致で 403 を返す。

→ マネージド origin request policy **`Managed-AllViewerExceptHostHeader`** を使い、
Host 以外の全ヘッダーを転送しつつ Host だけ除外する。

## 決定

- `infra/modules/frontend` を新設。S3 (非公開, force_destroy) + OAC + CloudFront
  (`price_class = "PriceClass_200"`, `wait_for_deployment = false`) を作成
- default behavior は S3 オリジンへ `Managed-CachingOptimized`。`/query` `/healthz` の
  ordered behavior は API Gateway オリジンへ `Managed-CachingDisabled` +
  `Managed-AllViewerExceptHostHeader`
- 静的ファイル (`web/index.html` / `web/app.js` / `web/style.css`) は `aws_s3_object` で
  Terraform 管理。`etag = filemd5(...)` により内容変更時のみ再アップロードされる。
  `terraform-apply.yml` の apply 直後に `aws cloudfront create-invalidation --paths "/*"`
  を実行し、キャッシュ済みコンテンツを即時反映する (月 1,000 パスまで無料)
- **SPA 的な 403/404 → `index.html` への書き換え (`custom_error_response`) は入れない**。
  CloudFront の `custom_error_response` はオリジンを区別せず HTTP ステータスにのみ反応するため、
  入れると API オリジンが返す 404 (未定義ルート等) まで `index.html` にすり替わり、
  クライアント側でエラーを判別できなくなる。本サイトはページ遷移のない単一 `index.html`
  のみなので SPA ルーティングの恩恵も薄く、正しさを優先して見送った
- フロント (`web/app.js`) は相対パス `fetch("/query")` で呼び出す。429 (スロットリング)
  を「デモのレート制限」として明示的にハンドリングする
- 公開に伴い API Gateway のスロットリングを `2 req/s` → **`1 req/s`** (バーストも 5→3) に
  引き下げた (`infra/envs/dev/variables.tf`)
- `infra/bootstrap` の apply ロールに CloudFront 権限を追加。`Create*`/`List*`/`Get*` は
  サービス認可リファレンス上リソースタイプが未定義のため `Resource="*"` が必須で、
  `Update*`/`Delete*`/`CreateInvalidation` 等は `distribution/*`・`origin-access-control/*`
  にスコープできる (region セグメントなし)。web バケットは既存の `DocsBucketManage`
  statement に相乗り (`S3BucketsManage` に改名)。詳細は `docs/iam-permissions.md`

## 影響

- **デモ公開に対する乱用対策は「スロットリング (ステージ全体、IP 単位ではない) +
  Budgets の事後メール通知 + CloudWatch アラーム (ADR 0010 の `api-request-spike`)」の
  みで、自動遮断の仕組みはまだない**。計画書 §7.5 が想定する
  「Budgets 超過 → Lambda concurrency=0 の自動停止」は Phase 4 の残タスクとして未着手
  ([README](../../README.md) のコスト設計セクション参照)。1 req/s のステージ全体上限でも
  悪意ある連続リクエストは理論上 1 日 86,400 リクエストまで通り得る点に留意する
- CloudFront ディストリビューションの作成・伝播には数分〜十数分かかる
  (`wait_for_deployment = false` のため `terraform apply` 自体はブロックしない)。
  マージ直後は `demo_url` にアクセスしてもしばらく反映されないことがある
- カスタムドメイン (Route 53 + ACM) は導入していない。`*.cloudfront.net` のデフォルト
  証明書のみで、HTTPS 自体は有効
