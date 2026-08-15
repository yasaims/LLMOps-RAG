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

# GitHub の immutable subject claim 用のリポジトリ識別子 (owner@ownerID/repo@repoID)。
# GitHub は sub クレームにアカウント ID / リポジトリ ID を埋め込む形式へ既定を切り替えており、
# 実際に発行されるトークンは sub = "repo:yasaims@148611624/LLMOps-RAG@1332093841:pull_request"
# となる。var.github_repo だけを StringEquals にすると完全一致せず
# AssumeRoleWithWebIdentity が Not authorized で落ちるため、両形式を許可する。
#
# ⚠️ 値は `gh api repos/<owner>/<repo>/actions/oidc/customization/sub` の sub_claim_prefix から
#    "repo:" を除いた文字列。ID は不変なのでリポジトリ名やアカウント名を変えても追随不要。
variable "github_repo_immutable" {
  type        = string
  description = "immutable subject claim 用のリポジトリ識別子 (owner@ownerID/repo@repoID)"
  default     = "yasaims@148611624/LLMOps-RAG@1332093841"
}
