variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "project" {
  type    = string
  default = "llmops-rag"
}

variable "ecr_repo_name" {
  type    = string
  default = "llmops-rag-api"
}

variable "env" {
  type        = string
  description = "infra/envs/dev のリソース命名規約 (project-env-*) と合わせる"
  default     = "dev"
}

variable "github_repo" {
  type        = string
  description = "GitHub OIDC の trust policy で参照するリポジトリ (owner/repo)"
  default     = "yasaims/LLMOps-RAG"
}
