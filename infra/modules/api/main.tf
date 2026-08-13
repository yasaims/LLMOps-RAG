# Lambda (コンテナイメージ) + API Gateway HTTP API。
# VPC 不要 (S3 Vectors 採用の主因。ADR 0005) なので NAT/VPC エンドポイントの
# 常時課金が発生しない。

data "aws_caller_identity" "current" {}

locals {
  function_name  = "${var.project}-${var.env}-api"
  log_group_name = "/aws/lambda/${local.function_name}"

  # Bedrock IAM リソース ARN。embed はリージョン内の foundation model、
  # chat は jp. 推論プロファイル + そのプロファイルが実際にルーティングする
  # 先の foundation model (クロスリージョン推論のため region を * にする) の両方が必要
  # (推論プロファイル経由の呼び出しは両方の ARN への権限が要る)
  embed_model_arn            = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_embed_model_id}"
  chat_inference_profile_arn = "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_chat_model_id}"
  chat_foundation_model_arn  = "arn:aws:bedrock:*::foundation-model/${trimprefix(var.bedrock_chat_model_id, "jp.")}"
}

resource "aws_cloudwatch_log_group" "api" {
  name              = local.log_group_name
  retention_in_days = 14
}

resource "aws_iam_role" "lambda" {
  name = "${local.function_name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  name = "${local.function_name}-policy"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.api.arn}:*"
      },
      {
        # Converse / InvokeModel は同じ bedrock:InvokeModel アクションで許可される
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = "bedrock:InvokeModel"
        Resource = [
          local.embed_model_arn,
          local.chat_inference_profile_arn,
          local.chat_foundation_model_arn,
        ]
      },
      {
        # PutVectors/ListVectors は含めない (取り込みはローカルから実行し、
        # Lambda の実行時パスは検索のみを行うため最小権限にする)
        Sid      = "S3VectorsQuery"
        Effect   = "Allow"
        Action   = ["s3vectors:QueryVectors", "s3vectors:GetVectors", "s3vectors:GetIndex"]
        Resource = var.vector_index_arn
      },
    ]
  })
}

resource "aws_lambda_function" "api" {
  function_name                  = local.function_name
  role                           = aws_iam_role.lambda.arn
  package_type                   = "Image"
  image_uri                      = var.image_uri
  architectures                  = ["x86_64"]
  memory_size                    = var.lambda_memory_mb
  timeout                        = var.lambda_timeout_s
  # 0 (または未設定) の場合はアカウントの同時実行数上限が小さく reserved を
  # 確保できない環境向けに null (予約なし) にする。詳細は ADR 0006 を参照
  reserved_concurrent_executions = var.reserved_concurrency > 0 ? var.reserved_concurrency : null

  environment {
    variables = {
      # AWS_REGION は Lambda 予約済み環境変数のため設定しない (実行リージョンが自動で入る)
      VECTOR_STORE           = var.vector_store
      S3_VECTORS_BUCKET      = var.s3_vectors_bucket
      S3_VECTORS_INDEX       = var.s3_vectors_index
      BEDROCK_EMBED_MODEL_ID = var.bedrock_embed_model_id
      BEDROCK_EMBED_DIM      = tostring(var.bedrock_embed_dim)
      BEDROCK_CHAT_MODEL_ID  = var.bedrock_chat_model_id
      BEDROCK_MAX_TOKENS     = tostring(var.bedrock_max_tokens)
      RAG_TOP_K              = tostring(var.rag_top_k)
      LOG_LEVEL              = var.log_level
      CORS_ALLOW_ORIGINS     = var.cors_allow_origins
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
}

resource "aws_apigatewayv2_api" "this" {
  name          = local.function_name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "query" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "healthz" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /healthz"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_cloudwatch_log_group" "api_gw" {
  name              = "/aws/apigateway/${local.function_name}"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.throttling_rate_limit
    throttling_burst_limit = var.throttling_burst_limit
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      integrationErr = "$context.integration.error"
    })
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
