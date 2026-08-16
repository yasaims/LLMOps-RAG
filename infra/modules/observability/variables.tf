variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "notification_email" {
  type        = string
  description = "予算超過・Lambda 異常の通知先メールアドレス"
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}

variable "lambda_function_name" {
  type = string
}

variable "lambda_log_group_name" {
  type        = string
  description = "Logs Insights ウィジェットが query_completed イベントを集計するロググループ"
}

variable "api_id" {
  type        = string
  description = "AWS/ApiGateway メトリクスの ApiId ディメンション用"
}

variable "api_stage_name" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "ap-northeast-1"
}

variable "bedrock_chat_model_id" {
  type = string
}

variable "bedrock_embed_model_id" {
  type = string
}

variable "abuse_detection_request_threshold" {
  type        = number
  description = "5分間の API Gateway リクエスト数がこれを超えたら乱用検知アラームを発報する"
  default     = 300
}
