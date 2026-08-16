# デモフロント (計画書 §7.5)。S3 (非公開, OAC 経由) + CloudFront。
#
# 設計方針: CloudFront 1 ディストリビューションに S3 (静的ファイル) と API Gateway
# (/query, /healthz) の 2 オリジンをぶら下げ、両方を同一オリジン (CloudFront ドメイン) に
# 見せる。ブラウザ側は相対パス `fetch("/query")` で完結し、`app/config.py` の
# cors_allow_origins は空のまま (CORS プリフライト自体が発生しない) にできる。
# 詳細は ADR 0011 を参照。

data "aws_caller_identity" "current" {}

locals {
  bucket_name = "${var.project}-${var.env}-web-${data.aws_caller_identity.current.account_id}"
  # 拡張子ごとの Content-Type。S3 は自動判定しないため明示しないとブラウザが正しく描画しない。
  web_files = {
    "index.html" = "text/html; charset=utf-8"
    "app.js"     = "application/javascript; charset=utf-8"
    "style.css"  = "text/css; charset=utf-8"
  }
}

resource "aws_s3_bucket" "web" {
  bucket        = local.bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_object" "web" {
  for_each     = local.web_files
  bucket       = aws_s3_bucket.web.id
  key          = each.key
  source       = "${var.web_dir}/${each.key}"
  etag         = filemd5("${var.web_dir}/${each.key}")
  content_type = each.value
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.project}-${var.env}-web-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# バケットは非公開のまま。CloudFront サービスプリンシパルにのみ、この特定の
# ディストリビューション (SourceArn) からの GetObject を許可する (OAC の標準パターン)。
data "aws_iam_policy_document" "web_bucket_policy" {
  statement {
    sid    = "AllowCloudFrontServicePrincipalReadOnly"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket_policy.json
}

# マネージドポリシーは ID をハードコードせず data source で解決する。
data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = var.price_class
  comment             = "${var.project}-${var.env} demo"
  # apply を CloudFront の配信伝播 (数分〜十数分) 待ちにしない。CI の terraform apply は
  # ここで止まらず、直後の /healthz スモークテストは API Gateway 直叩きのまま変更しない。
  wait_for_deployment = false

  origin {
    origin_id                = "s3-web"
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  origin {
    origin_id   = "api"
    domain_name = var.api_domain_name
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true
  }

  # ⚠️ Host ヘッダーを転送してはいけない (execute-api が CloudFront のホスト名を見て
  # SNI 不一致 403 を返す)。managed origin request policy
  # `AllViewerExceptHostHeader` を使うことで Host 以外の全ヘッダーを転送しつつこれを回避する。
  ordered_cache_behavior {
    path_pattern             = "/query"
    target_origin_id         = "api"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  ordered_cache_behavior {
    path_pattern             = "/healthz"
    target_origin_id         = "api"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  # ⚠️ SPA 的な 403/404 → index.html の書き換えはあえて入れていない。CloudFront の
  # custom_error_response はオリジン種別を区別せず HTTP ステータスにのみ反応するため、
  # 入れると API オリジンが返す 404 (未定義ルート等) まで index.html にすり替わり、
  # クライアント側でエラーを判別できなくなる。このサイトはページ遷移のない単一
  # index.html のみなので恩恵も薄く、正しさを優先して見送った (ADR 0011)。

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  depends_on = [aws_s3_object.web]
}
