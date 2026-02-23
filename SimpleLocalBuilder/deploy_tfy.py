"""
Deploy SimpleLocalBuilder to TrueFoundry.

Prerequisites:
  1. pip install truefoundry
  2. tfy login --host https://demo.truefoundry.cloud
  3. Create a PostgreSQL database (TrueFoundry dashboard or external), note the DATABASE_URL
  4. Create a Persistent Volume in TrueFoundry dashboard, note the volume_fqn

Usage (minimal):
  python deploy_tfy.py \
    --workspace_fqn "cluster:workspace" \
    --volume_fqn "tfy-volume://org:cluster:resume-data" \
    --database_url "postgresql://user:pass@host:5432/dbname"

Usage (with smart parser server-side key):
  python deploy_tfy.py \
    --workspace_fqn "cluster:workspace" \
    --volume_fqn "tfy-volume://org:cluster:resume-data" \
    --database_url "postgresql://user:pass@host:5432/dbname" \
    --parser_gen_api_key "sk-ant-..." \
    --parser_gen_provider anthropic \
    --parser_gen_model claude-haiku-4-5-20251001

Usage (with Daytona sandbox for isolated parser execution):
  python deploy_tfy.py \
    ... \
    --daytona_api_key "dtn-..." \
    --daytona_api_url "https://app.daytona.io/api" \
    --daytona_target us
"""

import argparse
import logging
import secrets
from truefoundry.deploy import (
    Service, Image, Port, DockerFileBuild, Resources,
    VolumeMount,
)

logging.basicConfig(level=logging.INFO)


def deploy(
    workspace_fqn,
    volume_fqn,
    database_url,
    secret_key=None,
    parser_gen_api_key=None,
    parser_gen_provider=None,
    parser_gen_model=None,
    daytona_api_key=None,
    daytona_api_url=None,
    daytona_target=None,
):
    if not secret_key:
        secret_key = secrets.token_hex(32)
        print(f"Generated SECRET_KEY (save this): {secret_key}")

    env = {
        "PORT": "8000",
        "DATABASE_URL": database_url,
        "DATA_DIR": "/app/data",
        "SECRET_KEY": secret_key,
        "FLASK_DEBUG": "0",
    }

    # Smart parser — server-side LLM credentials (optional)
    # If set, users can generate custom parsers without supplying their own API key
    if parser_gen_api_key:
        env["PARSER_GEN_API_KEY"] = parser_gen_api_key
        env["PARSER_GEN_PROVIDER"] = parser_gen_provider or "anthropic"
        if parser_gen_model:
            env["PARSER_GEN_MODEL"] = parser_gen_model

    # Daytona sandbox — isolated parser execution (optional)
    # If not set, generated parsers run in a local restricted exec() instead
    if daytona_api_key:
        env["DAYTONA_API_KEY"] = daytona_api_key
        if daytona_api_url:
            env["DAYTONA_API_URL"] = daytona_api_url
        if daytona_target:
            env["DAYTONA_TARGET"] = daytona_target

    service = Service(
        name="resume-builder",
        image=Image(
            build_source=DockerFileBuild(
                dockerfile_path="./Dockerfile",
                build_context_path="./",
            )
        ),
        ports=[
            Port(
                port=8000,
                expose=True,
                protocol="http",
            )
        ],
        resources=Resources(
            cpu_request=0.5,
            cpu_limit=1.0,
            memory_request=512,
            memory_limit=1024,
            ephemeral_storage_request=1024,
            ephemeral_storage_limit=2048,
        ),
        env=env,
        mounts=[
            VolumeMount(
                mount_path="/app/data",
                volume_fqn=volume_fqn,
            ),
        ],
    )

    deployment = service.deploy(workspace_fqn=workspace_fqn)
    print(f"Deployment triggered: {deployment.id}")
    print(f"Dashboard URL: {deployment.dashboard_url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy Resume Builder to TrueFoundry")

    # Required
    parser.add_argument("--workspace_fqn", required=True,
                        help="TrueFoundry workspace FQN (e.g., 'cluster:workspace')")
    parser.add_argument("--volume_fqn", required=True,
                        help="TrueFoundry volume FQN (e.g., 'tfy-volume://org:cluster:volume-name')")
    parser.add_argument("--database_url", required=True,
                        help="PostgreSQL connection string")

    # Optional — base app
    parser.add_argument("--secret_key", default=None,
                        help="Flask secret key (auto-generated if not provided)")

    # Optional — smart parser server-side LLM credentials
    parser.add_argument("--parser_gen_api_key", default=None,
                        help="API key for server-side parser generation (users won't need to supply their own)")
    parser.add_argument("--parser_gen_provider", default="anthropic",
                        choices=["anthropic", "openai", "gemini"],
                        help="LLM provider for parser generation (default: anthropic)")
    parser.add_argument("--parser_gen_model", default=None,
                        help="Model to use for parser generation (e.g. claude-haiku-4-5-20251001)")

    # Optional — Daytona sandbox
    parser.add_argument("--daytona_api_key", default=None,
                        help="Daytona API key for isolated sandbox execution")
    parser.add_argument("--daytona_api_url", default=None,
                        help="Daytona API URL (default: https://app.daytona.io/api)")
    parser.add_argument("--daytona_target", default=None,
                        help="Daytona target region (default: us)")

    args = parser.parse_args()

    deploy(
        workspace_fqn=args.workspace_fqn,
        volume_fqn=args.volume_fqn,
        database_url=args.database_url,
        secret_key=args.secret_key,
        parser_gen_api_key=args.parser_gen_api_key,
        parser_gen_provider=args.parser_gen_provider,
        parser_gen_model=args.parser_gen_model,
        daytona_api_key=args.daytona_api_key,
        daytona_api_url=args.daytona_api_url,
        daytona_target=args.daytona_target,
    )
