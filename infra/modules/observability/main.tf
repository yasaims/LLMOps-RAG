# コスト・異常検知の最小構成。Budgets → 停止用 Lambda の自動停止 (計画書 §7.5) は Phase 4。

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-${var.env}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-${var.env}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project}-${var.env}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  dimensions = {
    FunctionName = var.lambda_function_name
  }
  alarm_description  = "Lambda エラーが5分間で5件を超えた"
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "${var.project}-${var.env}-lambda-throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  dimensions = {
    FunctionName = var.lambda_function_name
  }
  alarm_description  = "Lambda スロットリングが5分間で5件を超えた (reserved_concurrent_executions のガードが効いている可能性)"
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.project}-${var.env}-api-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "5xx"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  dimensions = {
    ApiId = var.api_id
    Stage = var.api_stage_name
  }
  alarm_description  = "API Gateway の 5xx が5分間で5件を超えた"
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "bedrock_throttles" {
  alarm_name          = "${var.project}-${var.env}-bedrock-throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "InvocationThrottles"
  namespace           = "AWS/Bedrock"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  dimensions = {
    ModelId = var.bedrock_chat_model_id
  }
  alarm_description  = "Bedrock (chat) のスロットリングが5分間で10件を超えた (同一アカウントの他実行とのクォータ競合の可能性)"
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_request_spike" {
  alarm_name          = "${var.project}-${var.env}-api-request-spike"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Count"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = var.abuse_detection_request_threshold
  dimensions = {
    ApiId = var.api_id
    Stage = var.api_stage_name
  }
  alarm_description  = "デモ公開の乱用検知 (tripwire)。5分間のリクエスト数が閾値を超えた。スロットリング設定自体は infra/modules/api の throttling_rate_limit を参照 (このアラームは検知のみで自動遮断はしない)"
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

# CloudWatch ダッシュボードは 3 枚まで無料。API Gateway の詳細メトリクス
# (detailed_metrics_enabled) はカスタムメトリクス課金 ($0.30/metric/月) になるため有効化しない
# (infra/modules/api には未設定 = 既定で無効)。ルート単位ではなく API 全体の集計になるが、
# 月次予算 10 USD の制約下ではこちらを優先する。同じ理由で EMF によるカスタムメトリクス化も
# せず、既存の JSON 構造化ログ (app/logging_config.py) + Logs Insights クエリで賄う。
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project}-${var.env}"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Lambda"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", var.lambda_function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Errors", "FunctionName", var.lambda_function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Throttles", "FunctionName", var.lambda_function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Duration", "FunctionName", var.lambda_function_name, { stat = "Average" }],
            ["AWS/Lambda", "Duration", "FunctionName", var.lambda_function_name, { stat = "p90" }],
            ["AWS/Lambda", "Duration", "FunctionName", var.lambda_function_name, { stat = "p99" }],
          ]
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "API Gateway"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", var.api_id, "Stage", var.api_stage_name, { stat = "Sum" }],
            ["AWS/ApiGateway", "4xx", "ApiId", var.api_id, "Stage", var.api_stage_name, { stat = "Sum" }],
            ["AWS/ApiGateway", "5xx", "ApiId", var.api_id, "Stage", var.api_stage_name, { stat = "Sum" }],
            ["AWS/ApiGateway", "Latency", "ApiId", var.api_id, "Stage", var.api_stage_name, { stat = "Average" }],
            ["AWS/ApiGateway", "IntegrationLatency", "ApiId", var.api_id, "Stage", var.api_stage_name, { stat = "Average" }],
          ]
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Bedrock — Chat (${var.bedrock_chat_model_id})"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/Bedrock", "Invocations", "ModelId", var.bedrock_chat_model_id, { stat = "Sum" }],
            ["AWS/Bedrock", "InvocationLatency", "ModelId", var.bedrock_chat_model_id, { stat = "Average" }],
            ["AWS/Bedrock", "InvocationThrottles", "ModelId", var.bedrock_chat_model_id, { stat = "Sum" }],
            ["AWS/Bedrock", "InputTokenCount", "ModelId", var.bedrock_chat_model_id, { stat = "Sum" }],
            ["AWS/Bedrock", "OutputTokenCount", "ModelId", var.bedrock_chat_model_id, { stat = "Sum" }],
          ]
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "Bedrock — Embed (${var.bedrock_embed_model_id})"
          region  = var.aws_region
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/Bedrock", "Invocations", "ModelId", var.bedrock_embed_model_id, { stat = "Sum" }],
            ["AWS/Bedrock", "InvocationLatency", "ModelId", var.bedrock_embed_model_id, { stat = "Average" }],
            ["AWS/Bedrock", "InvocationThrottles", "ModelId", var.bedrock_embed_model_id, { stat = "Sum" }],
          ]
          period = 300
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 12
        width  = 24
        height = 6
        properties = {
          title  = "query_completed 集計 (app/logging_config.py の JSON 構造化ログ)"
          region = var.aws_region
          view   = "table"
          query  = <<-EOT
            SOURCE '${var.lambda_log_group_name}'
            | filter message = "query_completed"
            | stats count() as queries,
                    avg(latency_ms) as avg_ms, pct(latency_ms, 90) as p90_ms,
                    sum(input_tokens) as in_tok, sum(output_tokens) as out_tok,
                    avg(top_score) as avg_top_score
              by bin(1h)
          EOT
        }
      },
    ]
  })
}
