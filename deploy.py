import argparse
import logging
from truefoundry.deploy import Service, Image, Port, PythonBuild, Resources

# Set up logging
logging.basicConfig(level=logging.INFO)

def deploy(workspace_fqn):
    # Define the service
    service = Service(
        name="resume-builder",
        image=Image(
            build_source=PythonBuild(
                command="gunicorn app:app --bind 0.0.0.0:8000",
                requirements_path="requirements.txt",
                python_version="3.11"
            )
        ),
        ports=[
            Port(
                port=8000,
                expose=True,
                protocol="http"
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
            # Add other non-secret env vars here
            # "DAYTONA_TARGET_REPO": "https://github.com/yourusername/ResumeBuilder.git"
        },
        # Secrets like DAYTONA_API_KEY should be set in the TrueFoundry UI or passed securely
        # secrets={
        #     "DAYTONA_API_KEY": "tfy-secret://your-secret-group/daytona-api-key"
        # }
    )

    # Deploy the service
    deployment = service.deploy(workspace_fqn=workspace_fqn)
    print(f"Deployment triggered: {deployment.id}")
    print(f"Check status at: {deployment.dashboard_url}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy ResumeBuilder to TrueFoundry")
    parser.add_argument("--workspace_fqn", type=str, required=True, help="The Fully Qualified Name of the TrueFoundry workspace (e.g., 'my-org:my-workspace')")
    
    args = parser.parse_args()
    
    deploy(args.workspace_fqn)
