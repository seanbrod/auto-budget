"""Create an invited Cognito user and grant one household membership."""

import argparse
import uuid

import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--user-pool-id", required=True)
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--household-id", help="Existing household ID; generated when omitted")
    args = parser.parse_args()
    cognito = boto3.client("cognito-idp", region_name=args.region)
    dynamodb = boto3.client("dynamodb", region_name=args.region)
    try:
        cognito.admin_create_user(
            UserPoolId=args.user_pool_id,
            Username=args.email,
            UserAttributes=[{"Name": "email", "Value": args.email}, {"Name": "email_verified", "Value": "true"}],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except cognito.exceptions.UsernameExistsException:
        pass
    user = cognito.admin_get_user(UserPoolId=args.user_pool_id, Username=args.email)
    subject = next(attribute["Value"] for attribute in user["UserAttributes"] if attribute["Name"] == "sub")
    household = args.household_id or str(uuid.uuid4())
    try:
        dynamodb.put_item(
            TableName=args.table_name,
            Item={"pk": {"S": f"USER#{subject}"}, "sk": {"S": "MEMBERSHIP"}, "household_id": {"S": household}},
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise SystemExit("User already has a household membership; no changes made to it.") from error
        raise
    print(f"Household created or joined: {household}")
    print("The user can sign in using their Cognito invitation.")


if __name__ == "__main__":
    main()
