"""Print the Phase 2 GitHub deployer policy; this script does not modify AWS."""

import argparse
import json
import re


def build_policy(account: str, region: str) -> dict:
    project = "auto-budget-dev"
    prefix = f"arn:aws:{{service}}:{region}:{account}:"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "UserPoolManagement",
                "Effect": "Allow",
                "Action": [
                    "cognito-idp:CreateUserPool", "cognito-idp:DescribeUserPool",
                    "cognito-idp:UpdateUserPool", "cognito-idp:DeleteUserPool",
                    "cognito-idp:ListTagsForResource", "cognito-idp:TagResource",
                    "cognito-idp:UntagResource", "cognito-idp:GetUserPoolMfaConfig",
                    "cognito-idp:SetUserPoolMfaConfig", "cognito-idp:CreateUserPoolClient",
                    "cognito-idp:DescribeUserPoolClient", "cognito-idp:UpdateUserPoolClient",
                    "cognito-idp:DeleteUserPoolClient", "cognito-idp:CreateUserPoolDomain",
                    "cognito-idp:DeleteUserPoolDomain", "cognito-idp:UpdateUserPoolDomain",
                ],
                "Resource": f"arn:aws:cognito-idp:{region}:{account}:userpool/*",
            },
            {
                "Sid": "ReadCognitoDomain",
                "Effect": "Allow",
                "Action": ["cognito-idp:DescribeUserPoolDomain"],
                "Resource": "*",
            },
            {
                "Sid": "ApplicationTable",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:CreateTable", "dynamodb:DescribeTable", "dynamodb:UpdateTable",
                    "dynamodb:DeleteTable", "dynamodb:DescribeContinuousBackups",
                    "dynamodb:UpdateContinuousBackups", "dynamodb:ListTagsOfResource",
                    "dynamodb:TagResource", "dynamodb:UntagResource",
                    "dynamodb:DescribeTimeToLive", "dynamodb:UpdateTimeToLive",
                    "dynamodb:DescribeContributorInsights",
                ],
                "Resource": prefix.format(service="dynamodb") + f"table/{project}-data",
            },
            {
                "Sid": "ApplicationLambda",
                "Effect": "Allow",
                "Action": [
                    "lambda:CreateFunction", "lambda:GetFunction", "lambda:GetFunctionConfiguration",
                    "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration", "lambda:DeleteFunction",
                    "lambda:GetPolicy", "lambda:AddPermission", "lambda:RemovePermission",
                    "lambda:ListVersionsByFunction", "lambda:ListTags", "lambda:TagResource",
                    "lambda:UntagResource", "lambda:GetFunctionCodeSigningConfig",
                    "lambda:GetFunctionConcurrency", "lambda:GetFunctionEventInvokeConfig",
                    "lambda:PutFunctionConcurrency", "lambda:DeleteFunctionConcurrency",
                ],
                "Resource": prefix.format(service="lambda") + f"function:{project}-api*",
            },
            {
                "Sid": "PlaidQueues",
                "Effect": "Allow",
                "Action": ["sqs:CreateQueue", "sqs:DeleteQueue", "sqs:GetQueueAttributes", "sqs:SetQueueAttributes",
                           "sqs:GetQueueUrl", "sqs:ListQueueTags", "sqs:TagQueue", "sqs:UntagQueue"],
                "Resource": prefix.format(service="sqs") + f"{project}-plaid*",
            },
            {
                "Sid": "PlaidWeeklySchedule",
                "Effect": "Allow",
                "Action": ["events:PutRule", "events:DeleteRule", "events:DescribeRule", "events:PutTargets",
                           "events:RemoveTargets", "events:ListTargetsByRule", "events:ListTagsForResource",
                           "events:TagResource", "events:UntagResource"],
                "Resource": prefix.format(service="events") + f"rule/{project}-plaid-weekly",
            },
            {
                "Sid": "LambdaEventSourceManagement",
                "Effect": "Allow",
                "Action": ["lambda:CreateEventSourceMapping", "lambda:GetEventSourceMapping",
                           "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping",
                           "lambda:ListEventSourceMappings"],
                "Resource": "*",
            },
            {
                "Sid": "ApplicationExecutionRole",
                "Effect": "Allow",
                "Action": [
                    "iam:CreateRole", "iam:GetRole", "iam:UpdateRole", "iam:DeleteRole",
                    "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:GetRolePolicy",
                    "iam:DeleteRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
                    "iam:ListInstanceProfilesForRole", "iam:TagRole", "iam:UntagRole",
                    "iam:ListRoleTags",
                ],
                "Resource": f"arn:aws:iam::{account}:role/{project}-api",
            },
            {
                "Sid": "PassApplicationRoleToLambdaOnly",
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": f"arn:aws:iam::{account}:role/{project}-api",
                "Condition": {"StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"}},
            },
            {
                "Sid": "ApplicationLogGroup",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy",
                    "logs:DeleteRetentionPolicy", "logs:ListTagsForResource", "logs:ListTagsLogGroup",
                    "logs:TagResource", "logs:UntagResource", "logs:TagLogGroup", "logs:UntagLogGroup",
                ],
                "Resource": prefix.format(service="logs") + f"log-group:/aws/lambda/{project}-api*",
            },
            {
                "Sid": "DescribeLogGroups",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": "*",
            },
            {
                "Sid": "ApiGatewayRegionalManagement",
                "Effect": "Allow",
                "Action": ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE"],
                "Resource": f"arn:aws:apigateway:{region}::*",
            },
            {
                "Sid": "ApiGatewayStageTagOnCreate",
                "Effect": "Allow",
                "Action": "apigateway:TagResource",
                "Resource": f"arn:aws:apigateway:{region}::/apis/*/stages",
                "Condition": {"StringEquals": {"aws:RequestedRegion": region}},
            },
            {
                "Sid": "ApplicationAlarm",
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource", "cloudwatch:UntagResource", "cloudwatch:ListTagsForResource"],
                "Resource": prefix.format(service="cloudwatch") + f"alarm:{project}-api-*",
            },
            {
                "Sid": "DescribeAlarms",
                "Effect": "Allow",
                "Action": "cloudwatch:DescribeAlarms",
                "Resource": "*",
            },
            {
                "Sid": "ApplicationBudget",
                "Effect": "Allow",
                "Action": ["budgets:ModifyBudget", "budgets:ViewBudget", "budgets:TagResource", "budgets:UntagResource", "budgets:ListTagsForResource"],
                "Resource": f"arn:aws:budgets::{account}:budget/{project}-monthly-account-cost",
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{12}", args.account_id):
        parser.error("account ID must contain exactly 12 digits")
    if not re.fullmatch(r"[a-z]{2}(-[a-z0-9]+)+-[0-9]+", args.region):
        parser.error("invalid AWS region")
    print(json.dumps(build_policy(args.account_id, args.region), separators=(",", ":")))


if __name__ == "__main__":
    main()
