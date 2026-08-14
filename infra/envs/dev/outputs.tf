output "api_endpoint" {
  value = module.api.api_endpoint
}

output "docs_bucket" {
  value = module.ingestion.bucket_name
}

output "vector_bucket" {
  value = module.vector_store.bucket_name
}

output "vector_index" {
  value = module.vector_store.index_name
}

output "eval_ci_role_arn" {
  value = module.ci_eval.role_arn
}
