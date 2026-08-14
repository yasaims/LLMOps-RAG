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

variable "github_repo" {
  type        = string
  description = "GitHub OIDC の trust policy で参照するリポジトリ (owner/repo)"
}

variable "oidc_provider_arn" {
  type        = string
  description = "infra/bootstrap で作成済みの GitHub OIDC provider ARN"
}

variable "vector_index_arn" {
  type        = string
  description = "S3 Vectors インデックスの ARN (module.vector_store.index_arn)"
}

variable "bedrock_model_arns" {
  type        = list(string)
  description = "eval CI に許可する bedrock:InvokeModel 対象 ARN (module.api.bedrock_model_arns)"
}
