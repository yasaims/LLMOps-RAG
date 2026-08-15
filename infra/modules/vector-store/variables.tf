variable "vector_bucket_name" {
  type        = string
  description = "S3 Vectors バケット名"
}

variable "index_name" {
  type    = string
  default = "chunks"
}

variable "dimension" {
  type        = number
  description = "app/config.py の bedrock_embed_dim と一致させること"
  default     = 1536
}

variable "distance_metric" {
  type    = string
  default = "cosine"
}
