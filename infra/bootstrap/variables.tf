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
