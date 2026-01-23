import argparse
import logging

from truefoundry.deploy import (
    Build,
    DockerFileBuild,
    LocalSource,
    Port,
    Resources,
    Service,
    VolumeMount,
)

# Set up logging
logging.basicConfig(level=logging.INFO)

# Default base domain from user's environment
# You can change this if deploying to a different environment
BASE_DOMAIN = "ml.odsc-demo.truefoundry.cloud"

def get_host(service_name, workspace_fqn, port):
    """
    Constructs the host string based on the pattern:
    {service_name}-{workspace}-{port}.{base_domain}
    """
    try:
        # workspace_fqn format: cluster:workspace
        _, workspace = workspace_fqn.split(":")
    except ValueError:
        # Fallback if FQN format is unexpected
        workspace = workspace_fqn.replace(":", "-")
    
    return f"{service_name}-{workspace}-{port}.{BASE_DOMAIN}"


def get_volume_fqn(workspace_fqn):
    # Parse workspace_fqn to construct volume_fqn
    # Format: cluster:workspace
    # Example: odsc-cluster:dan-ai-nyc
    try:
        cluster, workspace = workspace_fqn.split(":")
        # Construct volume FQN based on the pattern in the user's YAML
        # user yaml: tfy-volume://odsc-cluster:dan-ai-nyc:resumebuildervolume
        return f"tfy-volume://{cluster}:{workspace}:resumebuildervolume"
    except ValueError:
        logging.warning("Could not parse workspace_fqn (expected 'cluster:workspace'). Using provided string as is.")
        # Fallback - might be incorrect but best effort
        return f"tfy-volume://{workspace_fqn}:resumebuildervolume"


def deploy_app(workspace_fqn):
    volume_fqn = get_volume_fqn(workspace_fqn)

    service_name = "resume-builder"
    port = 5001
    host = get_host(service_name, workspace_fqn, port)

    # Define the Flask App service
    service = Service(
        name=service_name,
        image=Build(
            build_source=LocalSource(local_build=True),
            build_spec=DockerFileBuild(
                dockerfile_path="./Dockerfile",
                build_context_path=".",
            ),
        ),
        ports=[
            Port(
                port=port,
                expose=True,
                protocol="TCP",
                app_protocol="http",
                host=host
            )
        ],
        env={
            "PORT": str(port),
            "FLASK_ENV": "production",
            # NOTE: These secret paths are hardcoded from the provided example. 
            # You may need to update 'odsc-demo' to your actual secret scope/workspace if different.
            "SECRET_KEY": "tfy-secret://odsc-demo:resumebuilder:SECRET_KEY",
            "DAYTONA_API_KEY": "tfy-secret://odsc-demo:resumebuilder:DAYTONA_API_KEY",
            "DAYTONA_TARGET_REPO": "https://github.com/birlaaishwarya11/ResumeBuilder.git",
        },
        mounts=[
            VolumeMount(
                mount_path="/app/data",
                volume_fqn=volume_fqn,
            )
        ],
        resources=Resources(
            cpu_request=1.5,
            cpu_limit=3.5,
            memory_request=5950,
            memory_limit=7000,
            ephemeral_storage_request=20000,
            ephemeral_storage_limit=100000,
        ),
    )

    # Deploy the service
    deployment = service.deploy(workspace_fqn=workspace_fqn)
    print(f"App Deployment triggered: {deployment.id}")
    print(f"Deployment FQN: {deployment.fqn}")


def deploy_mcp(workspace_fqn):
    service_name = "resume-mcp"
    port = 8000
    host = get_host(service_name, workspace_fqn, port)
    volume_fqn = get_volume_fqn(workspace_fqn)

    # Define the MCP Server service
    service = Service(
        name=service_name,
        image=Build(
            build_source=LocalSource(local_build=True),
            build_spec=DockerFileBuild(
                dockerfile_path="./Dockerfile.mcp",
                build_context_path=".",
            ),
        ),
        ports=[
            Port(
                port=port,
                expose=True,
                protocol="TCP",
                app_protocol="http",
                host=host
            )
        ],
        mounts=[
            VolumeMount(
                mount_path="/app/data",
                volume_fqn=volume_fqn,
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
            "PORT": str(port),
        },
    )

    # Deploy the service
    deployment = service.deploy(workspace_fqn=workspace_fqn)
    print(f"MCP Deployment triggered: {deployment.id}")
    print(f"Deployment FQN: {deployment.fqn}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy ResumeBuilder to TrueFoundry")
    parser.add_argument(
        "--workspace_fqn",
        type=str,
        required=True,
        help="The Fully Qualified Name of the TrueFoundry workspace (e.g., 'my-org:my-workspace')",
    )
    parser.add_argument(
        "--service",
        type=str,
        choices=["app", "mcp", "all"],
        default="all",
        help="Which service to deploy (app, mcp, or all)",
    )

    args = parser.parse_args()

    if args.service in ["app", "all"]:
        deploy_app(args.workspace_fqn)

    if args.service in ["mcp", "all"]:
        deploy_mcp(args.workspace_fqn)
