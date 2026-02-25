"""
MCP server for Resume Builder.

Security model
--------------
Each user has a unique, randomly-generated API key stored in the database
(column: users.mcp_api_key).  The bearer token in every request is looked up
against that column to identify *which* user is calling.

Consequences:
  - A valid token gives access only to that user's own data.
  - Passing someone else's user_id as a parameter has no effect — the user is
    always derived from the token, never from request body.
  - Regenerating a key (POST /api/mcp_key/regenerate on the Flask app)
    immediately invalidates the old token.

Getting your key
----------------
  curl -b <session_cookie> https://<flask-host>/api/mcp_key

Run locally
-----------
  python3 mcp_server.py           # port 8000, no extra env needed
  MCP_PORT=9000 python3 mcp_server.py

Truefoundry playground
----------------------
  URL:    https://<mcp-tfy-host>/mcp
  Header: Authorization: Bearer <your-mcp-api-key>
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="Resume Builder",
    instructions=(
        "Tools for parsing, reading, editing, and listing resume versions. "
        "Your identity is determined by your bearer token — you can only access "
        "your own resume data."
    ),
)


# ---------------------------------------------------------------------------
# Auth: token → user  (called at the top of every tool)
# ---------------------------------------------------------------------------

def _user_from_token(token: str) -> dict:
    """Resolve a bearer token to a user dict.  Raises ValueError on failure."""
    from models import get_user_by_mcp_key
    if not token or not token.strip():
        raise ValueError("No bearer token provided. Set Authorization: Bearer <your-mcp-api-key>.")
    user = get_user_by_mcp_key(token.strip())
    if not user:
        raise ValueError("Invalid or expired API key. Regenerate via POST /api/mcp_key/regenerate.")
    return user   # {"id": int, "email": str, "name": str}


def _get_context_token() -> str:
    """Extract the bearer token from the current MCP request context."""
    try:
        from mcp.server.fastmcp import Context
        ctx = mcp.get_context()
        auth_header = ctx.request_context.request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
    except Exception:
        pass
    # Fallback: honour MCP_API_KEY env var for local dev / curl testing
    return os.environ.get("MCP_DEV_TOKEN", "")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_parsers():
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


def _user_dir(user_id) -> str:
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

    Your identity is determined by your bearer token — no user_id needed.

    Args:
        pdf_path: Absolute path to the PDF file on the server.
        provider: LLM provider for smart parsing — "anthropic", "openai", or
                  "gemini".  Omit to use the heuristic parser (no API key needed).
        api_key:  API key for the chosen provider (required if provider is set).
        model:    Model name override (optional).

    Returns:
        JSON with keys: yaml, parser_used, sections, logs
    """
    token = _get_context_token()
    try:
        user = _user_from_token(token)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    m = _load_parsers()
    logs = [f"Authenticated as {user['email']}"]

    if not os.path.isfile(pdf_path):
        return json.dumps({"error": f"File not found: {pdf_path}"})

    # Extract
    try:
        import sandbox as daytona_sandbox
        extracted, sb_logs = daytona_sandbox.extract_text_in_sandbox(pdf_path)
        logs.extend(sb_logs or [])
        if not extracted:
            raise ValueError("sandbox returned empty")
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

    # Parse
    parser_used = 'heuristic'
    if provider and api_key:
        sp_provider, sp_key, sp_model = m['resolve_parser_credentials'](provider, api_key, model or None)
        if sp_provider and sp_key:
            try:
                code = m['generate_parser_code'](flat_lines, sp_provider, sp_key, sp_model)
                result, _final, sp_logs = m['run_parser'](
                    flat_lines, code, provider=sp_provider, api_key=sp_key, model=sp_model
                )
                logs.extend(sp_logs or [])
                if result:
                    parsed = m['normalize_dates'](result)
                    parser_used = 'smart_generated'
                else:
                    parsed = m['parse_resume_from_extracted'](extracted)
                    logs.append("Smart parser failed — fell back to heuristic")
            except Exception as exc:
                parsed = m['parse_resume_from_extracted'](extracted)
                logs.append(f"Smart parser error ({exc}) — fell back to heuristic")
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
def get_resume() -> str:
    """
    Get your current resume YAML.

    No arguments needed — your identity comes from your bearer token.

    Returns:
        JSON with keys: yaml, name (your name from the DB)
    """
    token = _get_context_token()
    try:
        user = _user_from_token(token)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    yaml_path = os.path.join(_user_dir(user['id']), 'resume.yaml')
    if not os.path.isfile(yaml_path):
        return json.dumps({"error": "No resume found. Complete onboarding first."})
    with open(yaml_path) as f:
        content = f.read()
    return json.dumps({"yaml": content, "name": user['name']})


@mcp.tool()
def update_resume(yaml_content: str) -> str:
    """
    Overwrite your current resume YAML.

    Args:
        yaml_content: Full YAML string to write. Must be valid YAML.

    Returns:
        JSON with "status": "success" or "error".
    """
    token = _get_context_token()
    try:
        user = _user_from_token(token)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    import yaml as _yaml
    try:
        _yaml.safe_load(yaml_content)
    except Exception as e:
        return json.dumps({"error": f"Invalid YAML: {e}"})

    user_dir = _user_dir(user['id'])
    os.makedirs(user_dir, exist_ok=True)
    yaml_path = os.path.join(user_dir, 'resume.yaml')
    with open(yaml_path, 'w') as f:
        f.write(yaml_content)
    return json.dumps({"status": "success"})


@mcp.tool()
def ai_edit_resume(
    change_request: str,
    provider: str,
    api_key: str,
    model: str = "",
) -> str:
    """
    Apply a natural-language change request to your resume using an LLM.

    Args:
        change_request: Plain-English description of the change to make.
        provider:       "anthropic" | "openai" | "gemini"
        api_key:        Your LLM API key.
        model:          Model name override (optional).

    Returns:
        JSON with key: yaml (modified resume YAML)
    """
    token = _get_context_token()
    try:
        user = _user_from_token(token)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    yaml_path = os.path.join(_user_dir(user['id']), 'resume.yaml')
    if not os.path.isfile(yaml_path):
        return json.dumps({"error": "No resume found. Complete onboarding first."})
    with open(yaml_path) as f:
        yaml_content = f.read()

    m = _load_parsers()
    system_prompt = (
        "You are a resume content editor. Apply the requested changes to the YAML and "
        "return ONLY the modified YAML — no explanation, no markdown fences."
    )
    user_msg = f"CURRENT YAML:\n{yaml_content}\n\nCHANGE REQUEST:\n{change_request}"

    try:
        response = m['call_ai_provider'](provider, api_key, system_prompt, user_msg, model or None)
        modified = response.strip()
        if modified.startswith('```'):
            modified = '\n'.join(
                l for l in modified.split('\n') if not l.strip().startswith('```')
            )
        m['yaml'].safe_load(modified)   # validate
        return json.dumps({"yaml": modified})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_resume_versions() -> str:
    """
    List your saved resume versions.

    Returns:
        JSON with key: versions — list of {filename, modified_at} dicts,
        newest first.
    """
    token = _get_context_token()
    try:
        user = _user_from_token(token)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    versions_dir = os.path.join(_user_dir(user['id']), 'versions')
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
# ASGI app (streamable-http transport, no separate auth middleware needed —
# auth is handled per-tool using the request context)
# ---------------------------------------------------------------------------

def make_app():
    return mcp.streamable_http_app()


if __name__ == '__main__':
    import uvicorn

    port = int(os.environ.get('MCP_PORT', 8000))
    host = os.environ.get('MCP_HOST', '0.0.0.0')

    print(f"[MCP] Resume Builder MCP server starting on {host}:{port}")
    print(f"[MCP] Endpoint: POST http://{host}:{port}/mcp")
    print("[MCP] Auth: per-user bearer token (GET /api/mcp_key from the Flask app)")

    uvicorn.run(make_app(), host=host, port=port)
