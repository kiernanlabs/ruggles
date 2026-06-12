"""Provision the data-plane AWS resources (DynamoDB table + S3 bucket) directly
via boto3 — the no-SAM-CLI path used to stand the cloud up for the initial
Watercolor migration.

These two resources are the "state" half of the system; the compute half
(Lambdas + HTTP API in infra/template.yaml) is layered on top later and
references these by name. Keeping them out of the SAM stack means a `sam delete`
can never wipe the migrated leaderboard.

Idempotent: re-running is a no-op if the resources already exist. All names are
prefixed "ruggles-".

Usage (from inside reddit_art_ranker_cloud/, with AWS creds in ../.env):
    python -m infra.provision_data_resources
"""

import json
import sys

import boto3
from botocore.exceptions import ClientError

# Load AWS creds + region from the project-root .env before any boto3 client.
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")
except Exception:
    pass

from shared.config import DDB_TABLE, S3_BUCKET  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def ensure_table(name: str) -> None:
    ddb = boto3.client("dynamodb")
    try:
        ddb.describe_table(TableName=name)
        print(f"  [table] {name} already exists — skipping create")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        print(f"  [table] creating {name} (on-demand, pk/sk) ...")
        ddb.create_table(
            TableName=name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            Tags=[{"Key": "project", "Value": "ruggles"}],
        )
        ddb.get_waiter("table_exists").wait(TableName=name)
        print(f"  [table] {name} is ACTIVE")

    # TTL is a separate call; safe to repeat.
    try:
        ddb.update_time_to_live(
            TableName=name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
        print("  [table] TTL enabled on attribute 'ttl'")
    except ClientError as e:
        # Already enabled → "TimeToLive is already enabled" ValidationException
        if "already" not in str(e).lower():
            print(f"  [table] TTL note: {e}")


def ensure_bucket(name: str) -> None:
    s3 = boto3.client("s3")
    region = s3.meta.region_name
    try:
        s3.head_bucket(Bucket=name)
        print(f"  [bucket] {name} already exists — skipping create")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket", "403"):
            raise
        if code == "403":
            print(f"  [bucket] {name} exists but is owned elsewhere (name taken). "
                  "Pass a different S3_BUCKET.")
            return
        print(f"  [bucket] creating {name} in {region} ...")
        kwargs = {"Bucket": name}
        if region and region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
        print(f"  [bucket] {name} created")

    # CORS so the browser can PUT uploads + GET images directly.
    s3.put_bucket_cors(Bucket=name, CORSConfiguration={"CORSRules": [{
        "AllowedMethods": ["PUT", "GET"],
        "AllowedOrigins": ["*"],
        "AllowedHeaders": ["*"],
        "MaxAgeSeconds": 3000,
    }]})

    # Public read for the object prefixes the result/salon pages render as plain
    # static URLs: mirrored pool images (pool/), transient consumer uploads
    # (uploads/), and permanent signed-in salon images (users/). The bucket is
    # created with Block Public Access fully on, so first relax the two flags
    # that would otherwise reject a public *policy* (the ACL-blocking flags stay
    # on — we grant access via policy, not ACLs).
    s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": False,
        "RestrictPublicBuckets": False,
    })
    s3.put_bucket_policy(Bucket=name, Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "PublicReadAssets",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": [
                f"arn:aws:s3:::{name}/pool/*",
                f"arn:aws:s3:::{name}/uploads/*",
                f"arn:aws:s3:::{name}/users/*",
            ],
        }],
    }))
    # Expire transient consumer uploads after 7 days.
    s3.put_bucket_lifecycle_configuration(Bucket=name, LifecycleConfiguration={"Rules": [{
        "ID": "ExpireRawUploads",
        "Status": "Enabled",
        "Filter": {"Prefix": "uploads/"},
        "Expiration": {"Days": 7},
    }]})
    print("  [bucket] CORS + uploads/ lifecycle + public-read policy applied")


def main() -> None:
    sts = boto3.client("sts")
    ident = sts.get_caller_identity()
    print(f"Provisioning into account {ident['Account']} as {ident['Arn']}")
    print(f"Resources: table={DDB_TABLE}  bucket={S3_BUCKET}\n")
    ensure_table(DDB_TABLE)
    ensure_bucket(S3_BUCKET)
    print("\nDone. Data-plane resources are ready for local.publish.")


if __name__ == "__main__":
    main()
