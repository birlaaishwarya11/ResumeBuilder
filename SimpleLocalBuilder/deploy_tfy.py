"""
Deploy SimpleLocalBuilder to TrueFoundry.

Prerequisites:
  1. pip install truefoundry
  2. tfy login --host https://demo.truefoundry.cloud
  3. Create a PostgreSQL database (TrueFoundry dashboard or external), note the DATABASE_URL
  4. Create a Persistent Volume in TrueFoundry dashboard, note the volume_fqn

Usage:
  python deploy_tfy.py \
    --workspace_fqn "cluster:workspace" \
    --volume_fqn "tfy-volume://org:cluster:resume-data" \
    --database_url "postgresql://user:pass@host:5432/dbname"
"""

import argparse
import logging
import secrets
from truefoundry.deploy import (
    Service, Image, Port, DockerFileBuild, Resources,
    VolumeMount,
)

logging.basicConfig(level=logging.INFO)


def deploy(workspace_fqn, volume_fqn, database_url, secret_key=None):
    if not secret_key:
        secret_key = secrets.token_hex(32)
        print(f"Generated SECRET_KEY (save this): {secret_key[:8]}...")

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
        env={
            "PORT": "8000",
            "DATABASE_URL": database_url,
            "DATA_DIR": "/app/data",
            "SECRET_KEY": secret_key,
            "FLASK_DEBUG": "0",
        },
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
    parser.add_argument("--workspace_fqn", required=True,
                        help="TrueFoundry workspace FQN (e.g., 'cluster:workspace')")
    parser.add_argument("--volume_fqn", required=True,
                        help="TrueFoundry volume FQN (e.g., 'tfy-volume://org:cluster:volume-name')")
    parser.add_argument("--database_url", required=True,
                        help="PostgreSQL connection string (e.g., 'postgresql://user:pass@host:5432/dbname')")
    parser.add_argument("--secret_key", default=None,
                        help="Flask secret key (auto-generated if not provided)")
    args = parser.parse_args()

    deploy(args.workspace_fqn, args.volume_fqn, args.database_url, args.secret_key)
