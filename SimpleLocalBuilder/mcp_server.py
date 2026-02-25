"""
MCP server for Resume Builder — agentic interface.

This server exposes resume tools to Claude and other AI agents via the
Model Context Protocol. It imports the service layer directly — no HTTP
calls to the Flask app, no tools.py intermediary.

Security model
--------------
Each user has a unique, randomly-generated API key stored in the database
(column: users.mcp_api_key).  The bearer token in every request is looked up
against that column to identify *which* user is calling.

Consequences:
  - A valid token gives access only to that user's own data.
  - user_id is NEVER accepted as a parameter — identity always comes from the token.
  - Regenerating a key (POST /api/mcp_key/regenerate on the Flask app)
    immediately invalidates the old token.

Agent-facing tools (designed for autonomous workflows)
-------------------------------------------------------
  get_resume()                      → current resume YAML
  update_resume(yaml)               → overwrite current resume
  ai_edit_resume(change_request, …) → natural-language edit
  analyze_jd(jd_text, …)           → match score + structured suggestions
  apply_jd_suggestions(…)          → apply selected suggestions → new version
  apply_full_jd(jd_text, …)        → analyze + apply all priority suggestions
  list_versions()                   → list resume version history
  restore_version(version_id)       → roll back to a previous version
  create_version(label)             → snapshot current resume

Getting your API key
--------------------
  curl -b <session_cookie> https://<flask-host>/api/mcp_key

Run locally
-----------
  python3 mcp_server.py
  MCP_PORT=9000 python3 mcp_server.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name='Resume Builder',
    instructions=(
        'Tools for reading, editing, and tailoring resumes to job descriptions. '
        'Your identity is determined by your bearer token — you can only access '
        'your own resume data. Never pass a user_id as a parameter; the server '
        'derives it from your token.'
    ),
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_token() -> str:
    """Extract bearer token from MCP request context, with dev fallback."""
    try:
        ctx = mcp.get_context()
        auth = ctx.request_context.request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:].strip()
    except Exception:
        pass
    return os.environ.get('MCP_DEV_TOKEN', '')


def _authenticate(token: str) -> dict:
    """Resolve a token to a user dict. Raises ValueError on failure."""
    from models import get_user_by_mcp_key
    if not token:
        raise ValueError(
            'No bearer token. Set Authorization: Bearer <key>. '
            'Get your key from GET /api/mcp_key on the Flask app.'
        )
    user = get_user_by_mcp_key(token)
    if not user:
        raise ValueError('Invalid or expired API key. Regenerate via POST /api/mcp_key/regenerate.')
    return user  # {"id": int, "email": str, "name": str}


def _user_or_error(fn):
    """Decorator: authenticate and inject user_id, return JSON error on failure."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _get_token()
        try:
            user = _authenticate(token)
        except ValueError as e:
            return json.dumps({'error': str(e)})
        return fn(user['id'], *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Tools — resume read/write
# ---------------------------------------------------------------------------

@mcp.tool()
def get_resume() -> str:
    """Get your current resume YAML.

    No arguments needed — identity comes from your bearer token.

    Returns:
        JSON with keys:
          yaml      (str)  — full resume YAML
          name      (str)  — your display name
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from resume_service import get_current_resume
    yaml_content = get_current_resume(user['id'])
    if yaml_content is None:
        return json.dumps({'error': 'No resume found. Complete onboarding first.'})
    return json.dumps({'yaml': yaml_content, 'name': user['name']})


@mcp.tool()
def update_resume(yaml_content: str) -> str:
    """Overwrite your current resume with new YAML.

    The previous state is automatically saved as a version before overwriting,
    so you can always restore it.

    Args:
        yaml_content: Full YAML string. Must be valid YAML.

    Returns:
        JSON with keys:
          status      (str)  — "success"
          version_id  (int)  — id of the saved snapshot
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from resume_service import save_current_resume
    try:
        version_id = save_current_resume(user['id'], yaml_content, source='ai_edit',
                                         label='Updated via MCP agent')
        return json.dumps({'status': 'success', 'version_id': version_id})
    except ValueError as e:
        return json.dumps({'error': str(e)})


@mcp.tool()
def ai_edit_resume(
    change_request: str,
    provider: str,
    api_key: str,
    model: str = '',
) -> str:
    """Apply a natural-language change to your resume using an LLM.

    The edit is applied to the current resume YAML and the result is saved
    as a new version automatically.

    Args:
        change_request: Plain-English description of what to change.
                        Examples: "Make the summary more technical",
                                  "Add Python to the skills section".
        provider:       "anthropic" | "openai" | "gemini"
        api_key:        Your LLM API key.
        model:          Model name override (optional).

    Returns:
        JSON with keys:
          yaml        (str) — modified resume YAML
          version_id  (int) — id of the saved snapshot
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from resume_service import get_current_resume, save_current_resume
    from ai_service import call_llm

    yaml_content = get_current_resume(user['id'])
    if yaml_content is None:
        return json.dumps({'error': 'No resume found. Complete onboarding first.'})

    system_prompt = (
        'You are a resume content editor. Apply the requested changes to the YAML and '
        'return ONLY the modified YAML — no explanation, no markdown fences.'
    )
    user_msg = f'CURRENT YAML:\n{yaml_content}\n\nCHANGE REQUEST:\n{change_request}'

    try:
        modified = call_llm(provider, api_key, system_prompt, user_msg, model or None)
        # Strip markdown fences
        modified = modified.strip()
        if modified.startswith('```'):
            lines = modified.splitlines()
            modified = '\n'.join(l for l in lines if not l.strip().startswith('```'))

        version_id = save_current_resume(
            user['id'], modified, source='ai_edit',
            label=f'AI edit: {change_request[:60]}'
        )
        return json.dumps({'yaml': modified, 'version_id': version_id})
    except Exception as exc:
        return json.dumps({'error': str(exc)})


# ---------------------------------------------------------------------------
# Tools — JD analysis and tailoring (core agentic workflow)
# ---------------------------------------------------------------------------

@mcp.tool()
def analyze_jd(
    jd_text: str,
    provider: str,
    api_key: str,
    model: str = '',
) -> str:
    """Analyze your resume against a job description.

    Returns a match score and a list of structured suggestions. Use the
    suggestion ids with apply_jd_suggestions() to apply specific changes,
    or call apply_full_jd() to do everything in one step.

    Args:
        jd_text:  Full text of the job description.
        provider: "anthropic" | "openai" | "gemini"
        api_key:  Your LLM API key.
        model:    Model name override (optional).

    Returns:
        JSON with keys:
          session_id    (int)   — use this id to apply suggestions
          match_score   (int)   — 0–100, how well resume matches the JD
          suggestions   (list)  — list of suggestion objects, each with:
                                   id, type, section, value, reason, priority
          logs          (list)
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from jd_service import analyze
    try:
        session_id, result, logs = analyze(user['id'], jd_text, provider, api_key, model or None)
        return json.dumps({
            'session_id': session_id,
            'match_score': result['match_score'],
            'suggestions': result['suggestions'],
            'logs': logs,
        })
    except Exception as exc:
        return json.dumps({'error': str(exc)})


@mcp.tool()
def apply_jd_suggestions(
    session_id: int,
    suggestion_ids: str,
    provider: str,
    api_key: str,
    model: str = '',
) -> str:
    """Apply a selected subset of JD suggestions to your resume.

    After calling analyze_jd(), review the suggestions and pick the ones you
    want. Pass their ids here to apply only those changes.

    Args:
        session_id:     The session_id returned by analyze_jd().
        suggestion_ids: Comma-separated list of suggestion id strings,
                        e.g. "add_keyword_0,strengthen_bullet_2".
        provider:       "anthropic" | "openai" | "gemini"
        api_key:        Your LLM API key.
        model:          Model name override (optional).

    Returns:
        JSON with keys:
          yaml        (str)  — updated resume YAML
          version_id  (int)  — id of the new version snapshot
          logs        (list)
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from jd_service import apply_suggestions

    ids = [s.strip() for s in suggestion_ids.split(',') if s.strip()]
    if not ids:
        return json.dumps({'error': 'suggestion_ids must be a non-empty comma-separated list'})

    try:
        new_yaml, version_id, logs = apply_suggestions(
            user['id'], session_id, ids, provider, api_key, model or None
        )
        return json.dumps({'yaml': new_yaml, 'version_id': version_id, 'logs': logs})
    except Exception as exc:
        return json.dumps({'error': str(exc)})


@mcp.tool()
def apply_full_jd(
    jd_text: str,
    provider: str,
    api_key: str,
    model: str = '',
    min_priority: int = 2,
) -> str:
    """Analyze a JD and automatically apply all high-priority suggestions.

    This is the one-shot agentic tool: analyze + apply in a single call.
    Only suggestions with priority <= min_priority are applied (1=must-have,
    2=recommended, 3=nice-to-have).

    A version snapshot is saved before changes are made, so you can
    restore_version() if needed.

    Args:
        jd_text:      Full text of the job description.
        provider:     "anthropic" | "openai" | "gemini"
        api_key:      Your LLM API key.
        model:        Model name override (optional).
        min_priority: Apply suggestions with priority ≤ this value (default 2).

    Returns:
        JSON with keys:
          yaml          (str)  — updated resume YAML
          version_id    (int)  — id of the new version snapshot
          match_score   (int)  — original match score before changes
          suggestions   (list) — all suggestions (including unapplied)
          applied_count (int)  — number of suggestions applied
          logs          (list)
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from jd_service import apply_full

    try:
        new_yaml, version_id, analysis, logs = apply_full(
            user['id'], jd_text, provider, api_key, model or None, min_priority
        )
        applied = [s for s in analysis['suggestions'] if s.get('priority', 3) <= min_priority]
        return json.dumps({
            'yaml': new_yaml,
            'version_id': version_id,
            'match_score': analysis['match_score'],
            'suggestions': analysis['suggestions'],
            'applied_count': len(applied),
            'logs': logs,
        })
    except Exception as exc:
        return json.dumps({'error': str(exc)})


# ---------------------------------------------------------------------------
# Tools — version management
# ---------------------------------------------------------------------------

@mcp.tool()
def list_versions() -> str:
    """List your saved resume versions, newest first.

    Returns:
        JSON with key:
          versions (list) — each item has: id, source, label, created_at
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from resume_service import list_versions as _list
    versions = _list(user['id'])
    return json.dumps({'versions': versions})


@mcp.tool()
def restore_version(version_id: int) -> str:
    """Roll back to a previous resume version.

    The restored content becomes the new current resume, and a new version
    snapshot is created to record the restoration.

    Args:
        version_id: The id from list_versions().

    Returns:
        JSON with keys:
          yaml        (str) — restored resume YAML
          version_id  (int) — id of the new snapshot recording the restore
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from resume_service import restore_version as _restore
    try:
        yaml_content = _restore(version_id, user['id'])
        return json.dumps({'yaml': yaml_content, 'status': 'restored'})
    except ValueError as exc:
        return json.dumps({'error': str(exc)})


@mcp.tool()
def create_version(label: str = '') -> str:
    """Snapshot the current resume as a named version.

    Useful before making experimental changes — creates a restore point.

    Args:
        label: Optional human-readable label (e.g. "Before JD tailoring").

    Returns:
        JSON with key:
          version_id (int)
    """
    token = _get_token()
    try:
        user = _authenticate(token)
    except ValueError as e:
        return json.dumps({'error': str(e)})

    from resume_service import get_current_resume, save_current_resume
    yaml_content = get_current_resume(user['id'])
    if yaml_content is None:
        return json.dumps({'error': 'No resume to snapshot'})

    from models import save_resume_version
    version_id = save_resume_version(user['id'], yaml_content, source='manual_edit',
                                     label=label or 'Manual snapshot')
    return json.dumps({'version_id': version_id, 'status': 'saved'})


# ---------------------------------------------------------------------------
# ASGI app (streamable-http transport)
# ---------------------------------------------------------------------------

def make_app():
    return mcp.streamable_http_app()


if __name__ == '__main__':
    import uvicorn

    port = int(os.environ.get('MCP_PORT', 8000))
    host = os.environ.get('MCP_HOST', '0.0.0.0')

    print(f'[MCP] Resume Builder MCP server starting on {host}:{port}')
    print(f'[MCP] Endpoint: POST http://{host}:{port}/mcp')
    print('[MCP] Auth: per-user bearer token (GET /api/mcp_key from the Flask app)')
    print('[MCP] Tools: get_resume, update_resume, ai_edit_resume,')
    print('[MCP]        analyze_jd, apply_jd_suggestions, apply_full_jd,')
    print('[MCP]        list_versions, restore_version, create_version')

    uvicorn.run(make_app(), host=host, port=port)
