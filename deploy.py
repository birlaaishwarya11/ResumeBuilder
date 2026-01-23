import argparse
import logging

from truefoundry.deploy import Image, Port, PythonBuild, Resources, Service

# Set up logging
logging.basicConfig(level=logging.INFO)


def deploy_app(workspace_fqn):
    # Define the Flask App service
    service = Service(
        name="resume-builder",
        image=Image(
            build_source=PythonBuild(
                command="gunicorn app:app --bind 0.0.0.0:8000",
                requirements_path="requirements.txt",
                python_version="3.11",
            )
        ),
        ports=[Port(port=8000, expose=True, protocol="http")],
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
        },
    )

    # Deploy the service
    deployment = service.deploy(workspace_fqn=workspace_fqn)
    print(f"App Deployment triggered: {deployment.id}")
    print(f"Check status at: {deployment.dashboard_url}")


def deploy_mcp(workspace_fqn):
    # Define the MCP Server service
    service = Service(
        name="resume-mcp",
        image=Image(
            build_source=PythonBuild(
                command="python mcp_service.py",
                requirements_path="requirements.txt",
                python_version="3.11",
            )
        ),
        ports=[Port(port=8000, expose=True, protocol="http")],
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
            # Add env vars required for MCP servers if any (e.g. API keys for tools)
        },
    )

    # Deploy the service
    deployment = service.deploy(workspace_fqn=workspace_fqn)
    print(f"MCP Deployment triggered: {deployment.id}")
    print(f"Check status at: {deployment.dashboard_url}")


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
