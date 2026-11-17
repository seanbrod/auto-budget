"""Store Plaid Sandbox credentials in a standard encrypted SSM parameter."""

import argparse
import getpass
import json

import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--parameter", default="/auto-budget-dev/plaid/sandbox/credentials")
    parser.add_argument("--replace", action="store_true", help="replace existing credentials")
    args = parser.parse_args()
    client_id = getpass.getpass("Plaid Sandbox client ID (hidden): ").strip()
    secret = getpass.getpass("Plaid Sandbox secret (hidden): ").strip()
    if not client_id or not secret:
        raise SystemExit("Both values are required.")
    boto3.client("ssm", region_name=args.region).put_parameter(
        Name=args.parameter, Type="SecureString", Tier="Standard", Overwrite=args.replace,
        Value=json.dumps({"client_id": client_id, "secret": secret}, separators=(",", ":")),
    )
    print("Sandbox credentials stored as an encrypted standard parameter.")


if __name__ == "__main__":
    main()
