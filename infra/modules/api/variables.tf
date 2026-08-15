variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "ap-northeast-1"
}

variable "image_uri" {
  type        = string
  description = "scripts/push_image.ps1 が push した ECR イメージ URI (タグ込み)"
}

variable "vector_index_arn" {
  type        = string
  description = "S3 Vectors インデックスの ARN (IAM ポリシーのリソース指定用)"
}

variable "vector_store" {
  type    = string
  default = "s3vectors"
}

variable "s3_vectors_bucket" {
  type = string
}

variable "s3_vectors_index" {
  type = string
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

variable "lambda_memory_mb" {
  type    = number
  default = 1024
}

variable "lambda_timeout_s" {
  type    = number
  default = 60
}

variable "reserved_concurrency" {
  type        = number
  description = "コスト暴走ガード。同時実行数の上限 (0 ならアカウント全体の共有プールを使用し予約しない)"
  default     = 2
}

variable "throttling_rate_limit" {
  type        = number
  description = "API Gateway の秒間リクエスト数上限 (乱用対策)"
  default     = 2
}

variable "throttling_burst_limit" {
  type    = number
  default = 5
}
