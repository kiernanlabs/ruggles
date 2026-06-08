"""Deploy the consumer app (compute half) entirely via boto3 — no SAM CLI.

Builds a Linux-compatible Lambda zip (openai + deps as manylinux wheels, plus
backend/ and shared/), uploads it to the assets bucket, then creates/updates:

  * IAM role     ruggles-art-ranker-lambda-role
  * Lambda       ruggles-art-ranker-api     (HTTP API handler, 29s)
  * Lambda       ruggles-art-ranker-worker  (async insertion, 600s)
  * HTTP API     ruggles-art-ranker-http    ($default route → api Lambda)

Idempotent: re-running updates code/config in place and reuses the same API id
(so the URL is stable). Assumes the data-plane resources already exist
(infra/provision_data_resources.py) and creds + OPENROUTER_API_KEY are in
../.env.

Usage (from inside reddit_art_ranker_cloud/):
    python -m infra.deploy_app
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")
except Exception:
    pass

from shared.config import DDB_TABLE, S3_BUCKET, LLM_MODEL  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Names (all ruggles-prefixed) ────────────────────────────────────────────
ROLE_NAME = "ruggles-art-ranker-lambda-role"
API_FN = "ruggles-art-ranker-api"
WORKER_FN = "ruggles-art-ranker-worker"
API_NAME = "ruggles-art-ranker-http"
CODE_KEY = "lambda/ruggles-app.zip"
RUNTIME = "python3.12"
ARCH = "x86_64"

MODULE_DIR = Path(__file__).resolve().parent.parent  # reddit_art_ranker_cloud/
# Build in the system temp dir (NOT under OneDrive, whose sync locks __pycache__
# and breaks rmtree on Windows).
BUILD_DIR = Path(tempfile.gettempdir()) / "ruggles-lambda-build"

REGION = boto3.session.Session().region_name or os.getenv("AWS_REGION", "us-east-1")
ACCOUNT = boto3.client("sts").get_caller_identity()["Account"]


# ── 1. Build the deployment package ─────────────────────────────────────────
def build_zip() -> bytes:
    print("Building Lambda package (Linux wheels + code)...")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # Cross-compile-safe: download manylinux wheels for the Lambda platform
    # rather than building from source on this (Windows) host.
    subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--platform", "manylinux2014_x86_64",
         "--implementation", "cp", "--python-version", "3.12",
         "--only-binary=:all:", "--target", str(BUILD_DIR),
         "openai>=1.40"],
        check=True,
    )

    # Our code (boto3 is provided by the Lambda runtime, so it's not bundled).
    for pkg in ("backend", "shared"):
        shutil.copytree(MODULE_DIR / pkg, BUILD_DIR / pkg,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in BUILD_DIR.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                z.write(path, path.relative_to(BUILD_DIR).as_posix())
    data = buf.getvalue()
    print(f"  package: {len(data) / 1e6:.1f} MB zipped")
    return data


def upload_code(data: bytes) -> None:
    print(f"Uploading code to s3://{S3_BUCKET}/{CODE_KEY}")
    boto3.client("s3").put_object(Bucket=S3_BUCKET, Key=CODE_KEY, Body=data)


# ── 2. IAM role ─────────────────────────────────────────────────────────────
def ensure_role() -> str:
    """Resolve the Lambda execution role. The deploy creds (claude_code) can't
    create/modify IAM, so prefer an existing role:

      1. ROLE_ARN env var, if set (explicit override), else
      2. look up ROLE_NAME and use it, else
      3. try to create it (works only if the caller has IAM write perms).

    Inline-policy application is best-effort — if it's denied, we assume the
    role was already created with infra/iam/role-policy.json.
    """
    override = os.getenv("ROLE_ARN")
    if override:
        print(f"  [role] using ROLE_ARN override: {override}")
        return override

    iam = boto3.client("iam")
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"  [role] using existing {ROLE_NAME}")
        return arn
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    # Role doesn't exist — try to create it (needs IAM write perms).
    import json
    trust = Path(MODULE_DIR / "infra" / "iam" / "role-trust.json").read_text()
    policy = Path(MODULE_DIR / "infra" / "iam" / "role-policy.json").read_text()
    try:
        print(f"  [role] creating {ROLE_NAME}")
        arn = iam.create_role(
            RoleName=ROLE_NAME, AssumeRolePolicyDocument=trust,
            Tags=[{"Key": "project", "Value": "ruggles"}],
        )["Role"]["Arn"]
        iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="ruggles-app-policy",
                            PolicyDocument=policy)
        print("  [role] created + policy applied")
        return arn
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("AccessDenied", "AccessDeniedException"):
            raise
        raise SystemExit(
            "\nIAM role is missing and these credentials can't create it.\n"
            "Have an account admin run (from reddit_art_ranker_cloud/):\n\n"
            f"  aws iam create-role --role-name {ROLE_NAME} \\\n"
            "      --assume-role-policy-document file://infra/iam/role-trust.json\n"
            f"  aws iam put-role-policy --role-name {ROLE_NAME} \\\n"
            "      --policy-name ruggles-app-policy \\\n"
            "      --policy-document file://infra/iam/role-policy.json\n\n"
            "Then re-run: python -m infra.deploy_app\n"
        )


# ── 3. Lambda functions ─────────────────────────────────────────────────────
def _common_env() -> dict:
    return {
        "DDB_TABLE": DDB_TABLE,
        "S3_BUCKET": S3_BUCKET,
        "LLM_MODEL": LLM_MODEL,
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY", ""),
    }


def ensure_function(lam, name: str, handler: str, timeout: int, role_arn: str,
                    extra_env: dict) -> str:
    env = {**_common_env(), **extra_env}
    code = {"S3Bucket": S3_BUCKET, "S3Key": CODE_KEY}
    try:
        lam.get_function(FunctionName=name)
        print(f"  [lambda] updating {name}")
        lam.update_function_code(FunctionName=name, S3Bucket=S3_BUCKET, S3Key=CODE_KEY)
        lam.get_waiter("function_updated").wait(FunctionName=name)
        lam.update_function_configuration(
            FunctionName=name, Handler=handler, Timeout=timeout, MemorySize=512,
            Runtime=RUNTIME, Role=role_arn, Environment={"Variables": env},
        )
        lam.get_waiter("function_updated").wait(FunctionName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        print(f"  [lambda] creating {name}")
        # New roles take a few seconds to be assumable by Lambda — retry.
        for attempt in range(8):
            try:
                lam.create_function(
                    FunctionName=name, Runtime=RUNTIME, Role=role_arn,
                    Handler=handler, Code=code, Timeout=timeout, MemorySize=512,
                    Architectures=[ARCH], Environment={"Variables": env},
                    Tags={"project": "ruggles"},
                )
                break
            except ClientError as ce:
                if ce.response["Error"]["Code"] == "InvalidParameterValueException" \
                        and attempt < 7:
                    print("    role not assumable yet, retrying...")
                    time.sleep(5)
                else:
                    raise
        lam.get_waiter("function_active_v2").wait(FunctionName=name)
    return f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{name}"


# ── 4. HTTP API ─────────────────────────────────────────────────────────────
def ensure_http_api(api_lambda_arn: str) -> str:
    gw = boto3.client("apigatewayv2")
    # Reuse an existing API by name so the URL stays stable across re-deploys.
    apis = gw.get_apis().get("Items", [])
    api = next((a for a in apis if a["Name"] == API_NAME), None)
    if api:
        api_id = api["ApiId"]
        print(f"  [api] reusing {API_NAME} ({api_id})")
    else:
        print(f"  [api] creating {API_NAME}")
        api_id = gw.create_api(Name=API_NAME, ProtocolType="HTTP")["ApiId"]

    # Integration (AWS_PROXY, payload v2)
    ints = gw.get_integrations(ApiId=api_id).get("Items", [])
    integ = next((i for i in ints if i.get("IntegrationUri") == api_lambda_arn), None)
    if integ:
        integ_id = integ["IntegrationId"]
    else:
        integ_id = gw.create_integration(
            ApiId=api_id, IntegrationType="AWS_PROXY",
            IntegrationUri=api_lambda_arn, IntegrationMethod="POST",
            PayloadFormatVersion="2.0",
        )["IntegrationId"]
    target = f"integrations/{integ_id}"

    # $default route catches every method/path → our in-Lambda router
    routes = gw.get_routes(ApiId=api_id).get("Items", [])
    default = next((r for r in routes if r["RouteKey"] == "$default"), None)
    if default:
        gw.update_route(ApiId=api_id, RouteId=default["RouteId"], Target=target)
    else:
        gw.create_route(ApiId=api_id, RouteKey="$default", Target=target)

    # $default stage with auto-deploy
    stages = gw.get_stages(ApiId=api_id).get("Items", [])
    if not any(s["StageName"] == "$default" for s in stages):
        gw.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)

    # Allow API Gateway to invoke the api Lambda
    lam = boto3.client("lambda")
    try:
        lam.add_permission(
            FunctionName=API_FN, StatementId="apigw-invoke",
            Action="lambda:InvokeFunction", Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{REGION}:{ACCOUNT}:{api_id}/*/*",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise
    return f"https://{api_id}.execute-api.{REGION}.amazonaws.com"


def main() -> None:
    print(f"Deploying app into account {ACCOUNT} ({REGION})")
    print(f"  table={DDB_TABLE}  bucket={S3_BUCKET}  model={LLM_MODEL}\n")

    data = build_zip()
    upload_code(data)

    print("IAM:")
    role_arn = ensure_role()

    print("Lambda:")
    lam = boto3.client("lambda")
    worker_arn = ensure_function(lam, WORKER_FN, "backend.handlers.worker_handler",
                                 600, role_arn, extra_env={})
    api_arn = ensure_function(lam, API_FN, "backend.handlers.api_handler",
                              29, role_arn,
                              extra_env={"WORKER_FUNCTION": WORKER_FN, "CORS_ORIGIN": "*"})

    print("API Gateway:")
    url = ensure_http_api(api_arn)

    print("\n" + "=" * 60)
    print(f"  Deployed. API base URL:\n    {url}")
    print(f"  Try:  {url}/pools")
    print(f"  Put this in frontend/index.html as API_URL.")
    print("=" * 60)


if __name__ == "__main__":
    main()
