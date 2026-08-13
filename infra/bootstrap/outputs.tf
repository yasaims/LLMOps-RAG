output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecr_repository_name" {
  value = aws_ecr_repository.api.name
}
