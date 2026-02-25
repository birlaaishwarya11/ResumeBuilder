"""
MCP server for Resume Builder.

Exposes resume tools to AI agents via the Model Context Protocol.
Runs as a standalone process (separate from the Flask app).

Transport:
  Streamable-HTTP (primary):  POST /mcp
  SSE (legacy fallback):      GET  /sse  +  POST /messages

Authentication:
  Pass the MCP_API_KEY env var on the server.
  Clients must send:  Authorization: Bearer <MCP_API_KEY>
  If MCP_API_KEY is not set, auth is disabled (dev mode only).

Run locally:
  python3 mcp_server.py                     # default port 8000
  MCP_API_KEY=secret python3 mcp_server.py  # with auth

Truefoundry playground URL:
  https://<your-tfy-host>/mcp
  Header:  Authorization: Bearer <MCP_API_KEY>
"""

import os
import sys
import json
import tempfile

# Ensure the SimpleLocalBuilder package is importable
sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP
from mcp import types as mcp_types

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------
MCP_API_KEY = os.environ.get('MCP_API_KEY', '')   # empty = no auth (dev mode)

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="Resume Builder",
    instructions=(
        "Tools for parsing, editing, and exporting resumes. "
        "Use parse_resume to extract structured YAML from a PDF, "
        "get_resume to read the current resume, and ai_edit_resume to modify it."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_models():
    """Lazy-import project modules (avoids Flask app startup overhead)."""
    from pdf_parser import extract_text_local, parse_resume_from_extracted
    from smart_parser import (
        resolve_parser_credentials,
        generate_parser_code,
        run_parser,
        normalize_dates,
    )
    from local_app import call_ai_provider
    import yaml
    return {
        'extract_text_local': extract_text_local,
        'parse_resume_from_extracted': parse_resume_from_extracted,
        'resolve_parser_credentials': resolve_parser_credentials,
        'generate_parser_code': generate_parser_code,
        'run_parser': run_parser,
        'normalize_dates': normalize_dates,
        'call_ai_provider': call_ai_provider,
        'yaml': yaml,
    }


def _get_user_dir(user_id: str) -> str:
    from models import get_user_dir
    return get_user_dir(user_id)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def parse_resume(
    pdf_path: str,
    provider: str = "",
    api_key: str = "",
    model: str = "",
) -> str:
    """
    Parse a resume PDF into structured YAML.

    Args:
        pdf_path:  Absolute path to the PDF file on the server.
        provider:  LLM provider for smart parsing — "anthropic", "openai", or "gemini".
                   Omit to use the heuristic parser (no API key needed).
        api_key:   API key for the chosen provider (required if provider is set).
        model:     Model name override (optional).

    Returns:
        JSON string with keys:
          - yaml         (str)  Parsed resume as YAML
          - parser_used  (str)  "heuristic" | "smart_generated" | "smart_locked"
          - sections     (list) Section keys found
          - logs         (list) Processing log messages
    """
    m = _load_models()
    logs = []

    if not os.path.isfile(pdf_path):
        return json.dumps({"error": f"File not found: {pdf_path}"})

    # 1. Extract
    try:
        import sandbox as daytona_sandbox
        extracted, sb_logs = daytona_sandbox.extract_text_in_sandbox(pdf_path)
        logs.extend(sb_logs or [])
        if not extracted:
            raise ValueError("sandbox empty")
        logs.append("Extracted via sandbox")
    except Exception:
        extracted = m['extract_text_local'](pdf_path)
        logs.append("Extracted locally (no sandbox)")

    if not extracted or not extracted.get('pages'):
        return json.dumps({"error": "Could not extract text from PDF"})

    flat_lines = [
        line
        for page in extracted.get('pages', [])
        for line in page.get('lines', [])
    ]

    # 2. Parse
    parser_used = 'heuristic'
    if provider and api_key:
        sp_provider, sp_key, sp_model = m['resolve_parser_credentials'](provider, api_key, model or None)
        if sp_provider and sp_key:
            try:
                code = m['generate_parser_code'](flat_lines, sp_provider, sp_key, sp_model)
                result, _final_code, sp_logs = m['run_parser'](
                    flat_lines, code, provider=sp_provider, api_key=sp_key, model=sp_model
                )
                logs.extend(sp_logs or [])
                if result:
                    parsed = m['normalize_dates'](result)
                    parser_used = 'smart_generated'
                else:
                    parsed = m['parse_resume_from_extracted'](extracted)
                    logs.append("Smart parser failed — fell back to heuristic")
            except Exception as e:
                parsed = m['parse_resume_from_extracted'](extracted)
                logs.append(f"Smart parser error ({e}) — fell back to heuristic")
        else:
            parsed = m['parse_resume_from_extracted'](extracted)
    else:
        parsed = m['parse_resume_from_extracted'](extracted)

    if not parsed:
        return json.dumps({"error": "Parsing produced no output"})

    parsed.pop('_section_headings', None)
    yaml_text = m['yaml'].dump(parsed, sort_keys=False, allow_unicode=True)
    sections = [k for k in parsed if k not in ('name', 'contact')]

    return json.dumps({
        "yaml": yaml_text,
        "parser_used": parser_used,
        "sections": sections,
        "logs": logs,
    })


@mcp.tool()
def get_resume(user_id: str) -> str:
    """
    Get the current resume YAML for a user.

    Args:
        user_id: The user's ID (integer as string).

    Returns:
        JSON string with keys:
          - yaml  (str)  Current resume YAML content
          - path  (str)  Absolute path to the resume file
    """
    user_dir = _get_user_dir(user_id)
    yaml_path = os.path.join(user_dir, 'resume.yaml')
    if not os.path.isfile(yaml_path):
        return json.dumps({"error": f"No resume found for user {user_id}"})
    with open(yaml_path) as f:
        content = f.read()
    return json.dumps({"yaml": content, "path": yaml_path})


@mcp.tool()
def update_resume(user_id: str, yaml_content: str) -> str:
    """
    Overwrite the current resume YAML for a user.

    Args:
        user_id:      The user's ID (integer as string).
        yaml_content: Full YAML string to write.

    Returns:
        JSON string with "status": "success" or "error".
    """
    import yaml as _yaml
    try:
        _yaml.safe_load(yaml_content)   # validate before writing
    except Exception as e:
        return json.dumps({"error": f"Invalid YAML: {e}"})

    user_dir = _get_user_dir(user_id)
    yaml_path = os.path.join(user_dir, 'resume.yaml')
    os.makedirs(user_dir, exist_ok=True)
    with open(yaml_path, 'w') as f:
        f.write(yaml_content)
    return json.dumps({"status": "success", "path": yaml_path})


@mcp.tool()
def ai_edit_resume(
    yaml_content: str,
    change_request: str,
    provider: str,
    api_key: str,
    model: str = "",
) -> str:
    """
    Apply a natural-language change request to a resume YAML using an LLM.

    Args:
        yaml_content:   Current resume YAML.
        change_request: Plain-English description of the change.
        provider:       "anthropic" | "openai" | "gemini"
        api_key:        LLM API key.
        model:          Model override (optional).

    Returns:
        JSON string with:
          - yaml  (str)  Modified resume YAML
    """
    m = _load_models()

    system_prompt = (
        "You are a resume content editor. Apply the requested changes to the YAML and "
        "return ONLY the modified YAML — no explanation, no markdown fences."
    )
    user_msg = f"CURRENT YAML:\n{yaml_content}\n\nCHANGE REQUEST:\n{change_request}"

    try:
        response = m['call_ai_provider'](provider, api_key, system_prompt, user_msg, model or None)
        modified = response.strip()
        if modified.startswith('```'):
            lines = modified.split('\n')
            modified = '\n'.join(
                l for l in lines
                if not l.strip().startswith('```')
            )
        # validate
        m['yaml'].safe_load(modified)
        return json.dumps({"yaml": modified})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_resume_versions(user_id: str) -> str:
    """
    List saved resume versions for a user.

    Args:
        user_id: The user's ID (integer as string).

    Returns:
        JSON string with "versions": list of {filename, modified_at} dicts.
    """
    user_dir = _get_user_dir(user_id)
    versions_dir = os.path.join(user_dir, 'versions')
    if not os.path.isdir(versions_dir):
        return json.dumps({"versions": []})

    import datetime
    versions = []
    for fname in sorted(os.listdir(versions_dir), reverse=True):
        if fname.endswith('.yaml'):
            fpath = os.path.join(versions_dir, fname)
            mtime = os.path.getmtime(fpath)
            versions.append({
                "filename": fname,
                "modified_at": datetime.datetime.fromtimestamp(mtime).isoformat(),
            })
    return json.dumps({"versions": versions})


# ---------------------------------------------------------------------------
# Auth middleware wrapper
# ---------------------------------------------------------------------------

def _make_app_with_auth():
    """Wrap the FastMCP ASGI app with a simple Bearer-token auth layer."""
    base_app = mcp.streamable_http_app()

    if not MCP_API_KEY:
        print("[MCP] WARNING: MCP_API_KEY not set — running without authentication")
        return base_app

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount
    from starlette.middleware.base import BaseHTTPMiddleware

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            auth = request.headers.get('Authorization', '')
            if not auth.startswith('Bearer ') or auth[7:] != MCP_API_KEY:
                return JSONResponse(
                    {"error": "Unauthorized — provide Authorization: Bearer <MCP_API_KEY>"},
                    status_code=401,
                )
            return await call_next(request)

    app = Starlette()
    app.add_middleware(BearerAuthMiddleware)
    app.mount('/', base_app)
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import uvicorn

    port = int(os.environ.get('MCP_PORT', 8000))
    host = os.environ.get('MCP_HOST', '0.0.0.0')

    print(f"[MCP] Starting Resume Builder MCP server on {host}:{port}")
    print(f"[MCP] Streamable-HTTP endpoint: POST http://{host}:{port}/mcp")
    print(f"[MCP] Auth: {'enabled (Bearer token)' if MCP_API_KEY else 'DISABLED (set MCP_API_KEY)'}")

    app = _make_app_with_auth()
    uvicorn.run(app, host=host, port=port)
