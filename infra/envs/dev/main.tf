terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# infra/bootstrap で一度きり作成済みの GitHub OIDC provider を参照する (鶏卵問題を避けるため
# provider 自体の作成は envs/dev の外に置いている。詳細は infra/bootstrap/github_oidc.tf と
# ADR 0008 を参照)。
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

module "vector_store" {
  source = "../../modules/vector-store"

  vector_bucket_name = "${var.project}-${var.env}-vectors"
  index_name         = "chunks"
  dimension          = var.bedrock_embed_dim
  distance_metric    = "cosine"
}

module "ingestion" {
  source = "../../modules/ingestion"

  bucket_name = "${var.project}-${var.env}-docs-${data.aws_caller_identity.current.account_id}"
}

module "api" {
  source = "../../modules/api"

  project    = var.project
  env        = var.env
  aws_region = var.aws_region
  image_uri  = var.image_uri

  vector_index_arn  = module.vector_store.index_arn
  vector_store      = "s3vectors"
  s3_vectors_bucket = module.vector_store.bucket_name
  s3_vectors_index  = module.vector_store.index_name

  bedrock_embed_model_id = var.bedrock_embed_model_id
  bedrock_embed_dim      = var.bedrock_embed_dim
  bedrock_chat_model_id  = var.bedrock_chat_model_id
  bedrock_max_tokens     = var.bedrock_max_tokens
  rag_top_k              = var.rag_top_k

  cors_allow_origins = var.cors_allow_origins
  log_level          = var.log_level

  reserved_concurrency   = var.lambda_reserved_concurrency
  throttling_rate_limit  = var.throttling_rate_limit
  throttling_burst_limit = var.throttling_burst_limit
}

module "observability" {
  source = "../../modules/observability"

  project              = var.project
  env                  = var.env
  notification_email   = var.notification_email
  monthly_budget_usd   = var.monthly_budget_usd
  lambda_function_name = module.api.function_name
}

# PR ごとの RAG 品質評価 (Phase 3) が本番 S3 Vectors に対して read-only で
# retrieve/generate を実行するための IAM ロール。Lambda 実行ロールと同一の ARN を渡すことで
# 権限が完全に一致するようにしている (ADR 0007/0008)。
module "ci_eval" {
  source = "../../modules/ci-eval"

  project               = var.project
  env                   = var.env
  aws_region            = var.aws_region
  github_repo           = var.github_repo
  github_repo_immutable = var.github_repo_immutable
  oidc_provider_arn     = data.aws_iam_openid_connect_provider.github.arn
  vector_index_arn      = module.vector_store.index_arn
  bedrock_model_arns    = module.api.bedrock_model_arns
}
