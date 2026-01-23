import uvicorn
from mcp.server.fastmcp import FastMCP

from mcp_servers import ats_server, doc_server

# Create an MCP server
mcp = FastMCP("ResumeBuilder", host="0.0.0.0", port=8000)


@mcp.tool()
def read_resume():
    """Reads the current resume data from the file system."""
    return doc_server.read_resume()


@mcp.tool()
def update_resume(data: dict, create_version: bool = True):
    """Updates the resume data and optionally creates a backup version."""
    return doc_server.update_resume(data, create_version)


@mcp.tool()
def get_versions():
    """Lists all available backup versions of the resume."""
    return doc_server.get_versions()


@mcp.tool()
def validate_resume():
    """Validates the resume for ATS compatibility and structure."""
    return ats_server.validate_resume()


if __name__ == "__main__":
    # For TrueFoundry deployment, we need to run as an SSE server.
    # We can default to SSE on port 8000.
    print("Starting ResumeBuilder MCP Server on port 8000 (SSE)...")
    mcp.run(transport="sse")
