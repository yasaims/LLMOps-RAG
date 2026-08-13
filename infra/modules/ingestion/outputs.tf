output "bucket_name" {
  value = aws_s3_bucket.docs.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.docs.arn
}
