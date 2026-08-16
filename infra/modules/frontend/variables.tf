variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "api_domain_name" {
  type        = string
  description = "CloudFront が /query, /healthz をプロキシする先の execute-api ホスト名 (パスなし)。infra/modules/api の api_domain_name output"
}

variable "web_dir" {
  type        = string
  description = "静的サイトのソースディレクトリ (リポジトリ直下の web/) への絶対パス"
}

variable "price_class" {
  type        = string
  description = "CloudFront の配信エッジ範囲。PriceClass_200 は東京含む最安帯の次 (北米/欧州/アジアの主要リージョン)"
  default     = "PriceClass_200"
}
