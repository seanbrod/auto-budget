# Sandbox queue and worker. Credentials are provisioned separately in SSM;
# Terraform never receives the Plaid secret or Item access tokens.
resource "aws_sqs_queue" "plaid_dlq" {
  name                      = "${local.app_name}-plaid-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_apigatewayv2_route" "budget" {
  for_each = toset([
    "GET /v1/categories", "POST /v1/categories", "PUT /v1/categories/{category_id}",
    "GET /v1/budgets/{month}", "PUT /v1/budgets/{month}",
    "GET /v1/transactions", "PUT /v1/transactions/{transaction_id}/classification", "GET /v1/dashboard"
  ])
  api_id               = aws_apigatewayv2_api.app.id
  route_key            = each.value
  target               = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.cognito.id
  authorization_scopes = ["openid"]
}

resource "aws_sqs_queue" "plaid" {
  name                       = "${local.app_name}-plaid"
  visibility_timeout_seconds = 1800
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.plaid_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_cloudwatch_log_group" "plaid_worker" {
  name              = "/aws/lambda/${local.app_name}-api-plaid-worker"
  retention_in_days = 14
}

resource "aws_lambda_function" "plaid_worker" {
  function_name                  = "${local.app_name}-api-plaid-worker"
  role                           = aws_iam_role.api.arn
  handler                        = "app.plaid_worker.handler"
  runtime                        = "python3.12"
  architectures                  = ["x86_64"]
  filename                       = "${path.module}/../../../backend/dist/api.zip"
  source_code_hash               = fileexists("${path.module}/../../../backend/dist/api.zip") ? filebase64sha256("${path.module}/../../../backend/dist/api.zip") : null
  timeout                        = 300
  memory_size                    = 256
  reserved_concurrent_executions = 2
  environment {
    variables = {
      HOUSEHOLD_TABLE        = aws_dynamodb_table.app.name
      PLAID_PARAMETER_PREFIX = "/${local.app_name}/plaid/sandbox"
      PLAID_QUEUE_URL        = aws_sqs_queue.plaid.url
    }
  }
  depends_on = [aws_cloudwatch_log_group.plaid_worker, aws_iam_role_policy.api]
}

resource "aws_lambda_event_source_mapping" "plaid" {
  event_source_arn        = aws_sqs_queue.plaid.arn
  function_name           = aws_lambda_function.plaid_worker.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
}

resource "aws_cloudwatch_metric_alarm" "plaid_dlq" {
  alarm_name          = "${local.app_name}-api-plaid-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.plaid_dlq.name }
}

resource "aws_cloudwatch_event_rule" "plaid_weekly" {
  name                = "${local.app_name}-plaid-weekly"
  schedule_expression = "rate(7 days)"
}

resource "aws_cloudwatch_event_target" "plaid_weekly" {
  rule  = aws_cloudwatch_event_rule.plaid_weekly.name
  arn   = aws_lambda_function.plaid_worker.arn
  input = jsonencode({ source = "auto-budget-weekly-recovery" })
}

resource "aws_lambda_permission" "plaid_weekly" {
  statement_id  = "AllowWeeklyPlaidRecovery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.plaid_worker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.plaid_weekly.arn
}

locals {
  plaid_routes = toset([
    "POST /v1/plaid/link-token", "POST /v1/plaid/exchange", "GET /v1/plaid/items",
    "DELETE /v1/plaid/items/{item_id}", "POST /v1/plaid/items/{item_id}/sync", "GET /v1/accounts"
  ])
}

resource "aws_apigatewayv2_route" "plaid" {
  for_each             = local.plaid_routes
  api_id               = aws_apigatewayv2_api.app.id
  route_key            = each.value
  target               = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.cognito.id
  authorization_scopes = ["openid"]
}

resource "aws_apigatewayv2_route" "plaid_webhook" {
  api_id    = aws_apigatewayv2_api.app.id
  route_key = "POST /v1/webhooks/plaid"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}
