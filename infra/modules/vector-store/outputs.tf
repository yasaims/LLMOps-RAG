output "bucket_name" {
  value = aws_s3vectors_vector_bucket.this.vector_bucket_name
}

output "bucket_arn" {
  value = aws_s3vectors_vector_bucket.this.vector_bucket_arn
}

output "index_name" {
  value = aws_s3vectors_index.chunks.index_name
}

output "index_arn" {
  value = aws_s3vectors_index.chunks.index_arn
}
