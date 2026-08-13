output "api_endpoint" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.api.name
}
