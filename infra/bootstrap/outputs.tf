output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecr_repository_name" {
  value = aws_ecr_repository.api.name
}

output "github_oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github_actions.arn
}

output "ci_tf_plan_role_arn" {
  value = aws_iam_role.ci_tf_plan.arn
}

output "ci_tf_apply_role_arn" {
  value = aws_iam_role.ci_tf_apply.arn
}
