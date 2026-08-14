variable "aws_region" {
  type    = string
  default = "ap-northeast-1"
}

variable "project" {
  type    = string
  default = "llmops-rag"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "image_uri" {
  type        = string
  description = "scripts/push_image.ps1 が出力する ECR イメージ URI (タグ込み)"
}

variable "bedrock_embed_model_id" {
  type    = string
  default = "cohere.embed-v4:0"
}

variable "bedrock_embed_dim" {
  type    = number
  default = 1536
}

variable "bedrock_chat_model_id" {
  type    = string
  default = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "bedrock_max_tokens" {
  type    = number
  default = 1024
}

variable "rag_top_k" {
  type    = number
  default = 5
}

variable "cors_allow_origins" {
  type    = string
  default = ""
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "lambda_reserved_concurrency" {
  type        = number
  description = <<-EOT
    コスト暴走ガード。同時実行数の上限 (0 なら予約なし)。
    ⚠️ このデプロイ先アカウントは Lambda の同時実行数上限がアカウント全体で 10 しかなく、
    1 以上を指定すると PutFunctionConcurrency が失敗する (ADR 0006)。CI は gitignore された
    terraform.tfvars を読まないため、default は必ず 0 のままにしておくこと。
  EOT
  default     = 0
}

variable "throttling_rate_limit" {
  type    = number
  default = 2
}

variable "throttling_burst_limit" {
  type    = number
  default = 5
}

variable "notification_email" {
  type        = string
  description = "Budgets/CloudWatch アラームの通知先"
  # ⚠️ public リポジトリの CI が terraform plan を実行し、結果を PR に自動コメントする
  # (Phase 3)。sensitive を外すと SNS subscription / Budgets の subscriber_email_addresses
  # に生メールが表示され、そのまま PR コメントに個人情報が晒される。
  sensitive = true
}

variable "github_repo" {
  type        = string
  description = "GitHub OIDC の trust policy で参照するリポジトリ (owner/repo)"
  default     = "yasaims/LLMOps-RAG"
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}
