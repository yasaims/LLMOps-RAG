output "api_endpoint" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.api.name
}

output "api_domain_name" {
  description = "CloudFront のオリジンに使う execute-api ホスト名 (パスなし)。Phase 4 フロント用"
  value       = replace(aws_apigatewayv2_api.this.api_endpoint, "https://", "")
}

output "bedrock_model_arns" {
  description = <<-EOT
    Lambda 実行ロールが bedrock:InvokeModel を許可している ARN 一覧 (embed FM + chat 推論
    プロファイル + chat 推論プロファイルのルーティング先 FM)。Phase 3 の eval CI ロール
    (infra/modules/ci-eval) が同じ ARN を参照することで、eval CI が本番 Lambda と同一の
    最小権限セットになることを保証する。
  EOT
  value = [
    local.embed_model_arn,
    local.chat_inference_profile_arn,
    local.chat_foundation_model_arn,
  ]
}
