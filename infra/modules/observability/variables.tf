variable "project" {
  type = string
}

variable "env" {
  type = string
}

variable "notification_email" {
  type        = string
  description = "予算超過・Lambda 異常の通知先メールアドレス"
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}

variable "lambda_function_name" {
  type = string
}
