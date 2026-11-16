variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used to namespace all resources"
  type        = string
}

variable "budget_alert_email" {
  description = "Email for a $5 monthly account-wide budget alert; empty disables it"
  type        = string
  default     = ""
}
