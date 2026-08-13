# S3 Vectors (ADR 0005): VPC 不要で Lambda から直接呼び出せ、アイドル時ほぼ 0 円。
# Aurora Serverless v2 (ADR 0001) からの変更理由は docs/adr/0005-*.md を参照。

resource "aws_s3vectors_vector_bucket" "this" {
  vector_bucket_name = var.vector_bucket_name
  # ポートフォリオ用デモのため、terraform destroy でベクトルごと確実に消せるようにしておく
  force_destroy = true
}

resource "aws_s3vectors_index" "chunks" {
  vector_bucket_name = aws_s3vectors_vector_bucket.this.vector_bucket_name
  index_name         = var.index_name
  data_type          = "float32"
  dimension          = var.dimension
  distance_metric    = var.distance_metric

  # チャンク本文はここに置く。filterable metadata の 2KB 上限を超えないよう、
  # 検索フィルタに使わないフィールドは必ずここに含めること
  # (app/vectorstore/s3vectors_store.py の実装と対応させる)
  metadata_configuration {
    non_filterable_metadata_keys = ["content"]
  }
}
