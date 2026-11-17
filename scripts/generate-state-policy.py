"""Print permissions for the default-workspace Terraform state and S3 lock."""

import argparse
import json
import re


def build_policy(bucket: str, key: str) -> dict:
    state = f"arn:aws:s3:::{bucket}/{key}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ListStateBucket", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}"},
            {"Sid": "ReadWriteState", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": state},
            {"Sid": "ManageStateLock", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": f"{state}.tflock"},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", default="dev/terraform.tfstate")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", args.bucket):
        parser.error("invalid S3 bucket name")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", args.key) or ".." in args.key or args.key.startswith("/"):
        parser.error("invalid state key")
    print(json.dumps(build_policy(args.bucket, args.key), separators=(",", ":")))


if __name__ == "__main__":
    main()
