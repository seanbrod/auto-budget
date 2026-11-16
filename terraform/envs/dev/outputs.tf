output "environment" {
  description = "Environment name"
  value       = var.environment
}

output "aws_region" {
  description = "AWS region"
  value       = var.aws_region
}

output "project_name" {
  description = "Project name"
  value       = var.project_name
}

output "api_url" {
  value = aws_apigatewayv2_api.app.api_endpoint
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.app.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.app.id
}

output "cognito_domain" {
  value = "https://${aws_cognito_user_pool_domain.app.domain}.auth.${var.aws_region}.amazoncognito.com"
}

output "household_table_name" {
  value = aws_dynamodb_table.app.name
}
