"""
Smart-parser tool endpoints — Flask blueprint.

Each endpoint is independent and stateless (except lock_parser which writes to DB).
All endpoints require authentication (session cookie) except they can also be called
with an Authorization header for TrueFoundry deployment.

Endpoints
---------
POST /tools/extract_raw       – extract text+metadata from an uploaded PDF
POST /tools/generate_parser   – LLM writes a parse() function for this resume
POST /tools/test_parser       – run a parse() function on lines in Daytona (or local)
POST /tools/refine_parser     – ask LLM to modify an existing parse() function
POST /tools/normalize_dates   – walk a parsed dict and add _date_parsed fields
POST /tools/lock_parser       – store a parser in the DB and mark it locked
POST /tools/parse_resume      – full pipeline: extract → parse → YAML (for testing)
"""

import os
import json
import tempfile

import yaml
from flask import Blueprint, request, jsonify, session

from models import (
    get_user_parser, save_user_parser, clear_user_parser, get_user_dir,
)
import sandbox as daytona_sandbox
from pdf_parser import extract_text_local, parse_resume_from_extracted
import smart_parser as sp

tools_bp = Blueprint('tools', __name__, url_prefix='/tools')


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _get_user_id():
    """
    Return the authenticated user_id or None.
    Supports both session-based auth (browser) and a simple
    'Authorization: Bearer <user_id>' header for API callers.
    """
    if 'user_id' in session:
        return session['user_id']
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        try:
            return int(auth[7:].strip())
        except ValueError:
            pass
    return None


def _require_auth():
    uid = _get_user_id()
    if uid is None:
        return None, jsonify({"status": "error", "message": "Authentication required."}), 401
    return uid, None, None


# ---------------------------------------------------------------------------
# 1. /tools/extract_raw
# ---------------------------------------------------------------------------

@tools_bp.route('/extract_raw', methods=['POST'])
def extract_raw():
    """
    Extract text with font metadata from a PDF file.

    Accepts multipart/form-data with field ``resume_pdf`` (PDF file).

    Returns:
        {
          "status": "success",
          "pages": [{"page": 1, "lines": [{"text": "...", "size": 12.0, "bold": true}]}],
          "flat_lines": [{"text": "...", "size": 12.0, "bold": true}],
          "line_count": 123,
          "extraction_source": "sandbox" | "local",
          "logs": [...]
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    pdf_file = request.files.get('resume_pdf')
    if not pdf_file or not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify({"status": "error", "message": "A PDF file is required (field: resume_pdf)."}), 400

    # Save to user dir so other tools can reuse it
    user_dir = get_user_dir(uid)
    os.makedirs(user_dir, exist_ok=True)
    pdf_path = os.path.join(user_dir, 'onboarding_upload.pdf')
    pdf_file.save(pdf_path)

    logs = []
    extracted, sandbox_logs = daytona_sandbox.extract_text_in_sandbox(pdf_path)
    logs.extend(sandbox_logs or [])
    source = 'sandbox'

    if not extracted:
        extracted = extract_text_local(pdf_path)
        source = 'local'
        logs.append("Sandbox unavailable, used local extraction")

    if not extracted or not extracted.get('pages'):
        return jsonify({"status": "error", "message": "Could not extract text from the PDF."}), 500

    # Build flat list across all pages
    flat_lines = []
    for page in extracted.get('pages', []):
        flat_lines.extend(page.get('lines', []))

    return jsonify({
        "status": "success",
        "pages": extracted['pages'],
        "flat_lines": flat_lines,
        "line_count": len(flat_lines),
        "extraction_source": source,
        "logs": logs,
    })


# ---------------------------------------------------------------------------
# 2. /tools/generate_parser
# ---------------------------------------------------------------------------

@tools_bp.route('/generate_parser', methods=['POST'])
def generate_parser():
    """
    Generate a custom parse() function for the supplied lines.

    Accepts JSON body:
        {
          "lines": [{"text": "...", "size": 12.0, "bold": true}, ...],
          "provider": "anthropic" | "openai" | "gemini",
          "api_key": "<key>",
          "model": "<optional model name>"
        }

    Returns:
        {
          "status": "success",
          "code": "<Python source>",
          "credentials_source": "user" | "server" | null
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    body = request.get_json(force=True, silent=True) or {}
    lines = body.get('lines')
    if not lines or not isinstance(lines, list):
        return jsonify({"status": "error", "message": "'lines' array is required."}), 400

    user_provider = body.get('provider', '')
    user_api_key = body.get('api_key', '')
    user_model = body.get('model', '')

    provider, api_key, model = sp.resolve_parser_credentials(
        user_provider, user_api_key, user_model
    )

    if not provider or not api_key:
        return jsonify({
            "status": "error",
            "message": "No API key available. Provide api_key in the request or set PARSER_GEN_API_KEY on the server."
        }), 400

    cred_source = 'user' if (user_api_key and user_api_key.strip()) else 'server'

    try:
        code = sp.generate_parser_code(lines, provider, api_key, model)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "success",
        "code": code,
        "credentials_source": cred_source,
    })


# ---------------------------------------------------------------------------
# 3. /tools/test_parser
# ---------------------------------------------------------------------------

@tools_bp.route('/test_parser', methods=['POST'])
def test_parser():
    """
    Execute a parse() function on the provided lines.

    Accepts JSON body:
        {
          "lines": [...],
          "code": "<Python source of parse() function>",
          "provider": "...",   // optional — used only for auto-fix retries
          "api_key": "...",
          "model": "..."
        }

    Returns:
        {
          "status": "success" | "error",
          "result": {...},          // parsed dict on success
          "final_code": "...",      // possibly fixed code
          "logs": [...],
          "error": "..."            // only on error
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    body = request.get_json(force=True, silent=True) or {}
    lines = body.get('lines')
    code = body.get('code', '').strip()

    if not lines or not isinstance(lines, list):
        return jsonify({"status": "error", "message": "'lines' array is required."}), 400
    if not code:
        return jsonify({"status": "error", "message": "'code' is required."}), 400

    user_provider = body.get('provider', '')
    user_api_key = body.get('api_key', '')
    user_model = body.get('model', '')
    provider, api_key, model = sp.resolve_parser_credentials(
        user_provider, user_api_key, user_model
    )

    result, final_code, logs = sp.run_parser(
        lines, code,
        provider=provider, api_key=api_key, model=model
    )

    if result is not None:
        # Also run date normalisation for convenience
        result = sp.normalize_dates(result)
        return jsonify({
            "status": "success",
            "result": result,
            "final_code": final_code,
            "logs": logs,
        })

    return jsonify({
        "status": "error",
        "message": "Parser execution failed after all retries.",
        "final_code": final_code,
        "logs": logs,
    }), 500


# ---------------------------------------------------------------------------
# 4. /tools/refine_parser
# ---------------------------------------------------------------------------

@tools_bp.route('/refine_parser', methods=['POST'])
def refine_parser():
    """
    Ask the LLM to modify an existing parse() function.

    Accepts JSON body:
        {
          "code": "<current Python source>",
          "change_request": "Extract the summary section under the key 'summary'.",
          "provider": "...",
          "api_key": "...",
          "model": "..."
        }

    Returns:
        {
          "status": "success",
          "code": "<updated Python source>"
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    body = request.get_json(force=True, silent=True) or {}
    code = body.get('code', '').strip()
    change_request = body.get('change_request', '').strip()

    if not code:
        return jsonify({"status": "error", "message": "'code' is required."}), 400
    if not change_request:
        return jsonify({"status": "error", "message": "'change_request' is required."}), 400

    user_provider = body.get('provider', '')
    user_api_key = body.get('api_key', '')
    user_model = body.get('model', '')
    provider, api_key, model = sp.resolve_parser_credentials(
        user_provider, user_api_key, user_model
    )

    if not provider or not api_key:
        return jsonify({
            "status": "error",
            "message": "No API key available. Provide api_key or set PARSER_GEN_API_KEY."
        }), 400

    try:
        updated_code = sp.refine_parser_code(code, change_request, provider, api_key, model)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "success",
        "code": updated_code,
    })


# ---------------------------------------------------------------------------
# 5. /tools/normalize_dates
# ---------------------------------------------------------------------------

@tools_bp.route('/normalize_dates', methods=['POST'])
def normalize_dates():
    """
    Walk a parsed resume dict and add _date_parsed fields next to every
    'date' string. Original strings are preserved.

    Accepts JSON body:
        {
          "parsed": { ... }   // the dict returned by parse()
        }

    Returns:
        {
          "status": "success",
          "parsed": { ... }   // same dict with _date_parsed fields injected
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    body = request.get_json(force=True, silent=True) or {}
    parsed = body.get('parsed')
    if parsed is None or not isinstance(parsed, dict):
        return jsonify({"status": "error", "message": "'parsed' dict is required."}), 400

    result = sp.normalize_dates(parsed)
    return jsonify({
        "status": "success",
        "parsed": result,
    })


# ---------------------------------------------------------------------------
# 6. /tools/lock_parser
# ---------------------------------------------------------------------------

@tools_bp.route('/lock_parser', methods=['POST'])
def lock_parser():
    """
    Store a parse() function in the database for the current user and mark it
    as the active (locked) parser. Future uploads will use this code instead
    of generating a new one.

    Accepts JSON body:
        {
          "code": "<Python source>",   // required to lock
          "action": "lock" | "unlock" | "clear"
        }

    Actions:
        lock    – store code and set locked=1 (default)
        unlock  – keep code but set locked=0 (allows re-generation on next upload)
        clear   – delete stored code entirely

    Returns:
        {
          "status": "success",
          "action": "lock" | "unlock" | "clear",
          "has_code": true | false
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    body = request.get_json(force=True, silent=True) or {}
    action = body.get('action', 'lock')

    if action == 'clear':
        clear_user_parser(uid)
        return jsonify({"status": "success", "action": "clear", "has_code": False})

    code = body.get('code', '').strip()

    if action == 'lock':
        if code:
            # Caller provided new code — save and lock it
            save_user_parser(uid, code, locked=True)
        else:
            # No code in body: lock whatever is already stored (e.g. code saved during upload)
            existing_code, _ = get_user_parser(uid)
            if not existing_code:
                return jsonify({
                    "status": "error",
                    "message": "No parser stored yet. Upload a PDF first so a parser can be generated."
                }), 400
            save_user_parser(uid, existing_code, locked=True)
        return jsonify({"status": "success", "action": "lock", "has_code": True})

    if action == 'unlock':
        if code:
            save_user_parser(uid, code, locked=False)
        else:
            # Unlock whatever is already stored
            existing_code, _ = get_user_parser(uid)
            if existing_code:
                save_user_parser(uid, existing_code, locked=False)
        existing_code, _ = get_user_parser(uid)
        return jsonify({"status": "success", "action": "unlock", "has_code": bool(existing_code)})

    return jsonify({"status": "error", "message": f"Unknown action '{action}'. Use lock, unlock, or clear."}), 400


# ---------------------------------------------------------------------------
# Utility: GET /tools/parser_status  — read current parser state
# ---------------------------------------------------------------------------

@tools_bp.route('/parser_status', methods=['GET'])
def parser_status():
    """
    Return the current parser state for the logged-in user.

    Returns:
        {
          "status": "success",
          "has_parser": true | false,
          "locked": true | false,
          "code_preview": "<first 300 chars of code>" | null
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    code, locked = get_user_parser(uid)
    return jsonify({
        "status": "success",
        "has_parser": bool(code),
        "locked": bool(locked),
        "code_preview": code[:300] if code else None,
    })


# ---------------------------------------------------------------------------
# 8. POST /tools/parse_resume  — full pipeline test (extract → parse → YAML)
# ---------------------------------------------------------------------------

@tools_bp.route('/parse_resume', methods=['POST'])
def parse_resume():
    """
    Full pipeline test: upload a PDF, extract text, run the parser (heuristic or
    smart), and return the resulting YAML plus the raw extracted lines.

    Useful for verifying parser quality without going through the full onboarding UI.

    Accepts multipart/form-data:
        resume_pdf     – PDF file (required)
        parser         – "heuristic" | "smart" | "auto" (default: "auto")
                         auto = use locked parser if available, else smart if keys
                         available, else heuristic
        provider       – AI provider (for smart parser; optional)
        api_key        – AI API key  (for smart parser; optional)
        model          – AI model    (for smart parser; optional)

    Returns:
        {
          "status": "success",
          "yaml": "<YAML string>",
          "parsed": { ... },           // structured dict
          "parser_used": "heuristic" | "smart_locked" | "smart_generated" | "heuristic_fallback",
          "extraction_source": "sandbox" | "local",
          "flat_lines": [...],         // raw extracted lines for debugging
          "logs": [...]
        }
    """
    uid, err_resp, err_code = _require_auth()
    if err_resp:
        return err_resp, err_code

    pdf_file = request.files.get('resume_pdf')
    if not pdf_file or not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify({"status": "error", "message": "A PDF file is required (field: resume_pdf)."}), 400

    parser_mode = request.form.get('parser', 'auto')
    user_provider = request.form.get('provider', '')
    user_api_key = request.form.get('api_key', '')
    user_model = request.form.get('model', '')

    # Save PDF
    user_dir = get_user_dir(uid)
    os.makedirs(user_dir, exist_ok=True)
    pdf_path = os.path.join(user_dir, 'onboarding_upload.pdf')
    pdf_file.save(pdf_path)

    # Step 1: Extract text
    logs = []
    extracted, sandbox_logs = daytona_sandbox.extract_text_in_sandbox(pdf_path)
    logs.extend(sandbox_logs or [])
    source = 'sandbox'

    if not extracted:
        extracted = extract_text_local(pdf_path)
        source = 'local'
        logs.append("Sandbox unavailable, used local extraction")

    if not extracted or not extracted.get('pages'):
        return jsonify({"status": "error", "message": "Could not extract text from the PDF."}), 500

    flat_lines = []
    for page in extracted.get('pages', []):
        flat_lines.extend(page.get('lines', []))

    # Step 2: Parse
    sp_provider, sp_api_key, sp_model = sp.resolve_parser_credentials(
        user_provider, user_api_key, user_model
    )
    parsed = None
    parser_used = 'heuristic'

    if parser_mode in ('auto', 'smart'):
        # Try locked parser first (if auto)
        if parser_mode == 'auto':
            stored_code, is_locked = get_user_parser(uid)
            if stored_code and is_locked:
                result, _code, sp_logs = sp.run_parser(
                    flat_lines, stored_code,
                    provider=sp_provider, api_key=sp_api_key, model=sp_model
                )
                logs.extend(sp_logs)
                if result is not None:
                    parsed = sp.normalize_dates(result)
                    parser_used = 'smart_locked'
                else:
                    logs.append("Locked parser failed, falling back")

        # Generate a new smart parser if credentials available
        if parsed is None and sp_provider and sp_api_key:
            try:
                code = sp.generate_parser_code(flat_lines, sp_provider, sp_api_key, sp_model)
                result, _code, sp_logs = sp.run_parser(
                    flat_lines, code,
                    provider=sp_provider, api_key=sp_api_key, model=sp_model
                )
                logs.extend(sp_logs)
                if result is not None:
                    parsed = sp.normalize_dates(result)
                    parser_used = 'smart_generated'
                else:
                    logs.append("Generated parser failed, falling back to heuristic")
            except Exception as e:
                logs.append(f"Smart parser error: {e}")

    if parsed is None:
        parsed = parse_resume_from_extracted(extracted)
        parser_used = 'heuristic' if parser_mode == 'heuristic' else 'heuristic_fallback'

    if not parsed:
        return jsonify({"status": "error", "message": "Parser returned no data."}), 500

    # Remove internal keys before serialising
    parsed_clean = {k: v for k, v in parsed.items() if not k.startswith('_')}
    yaml_str = yaml.dump(parsed_clean, sort_keys=False, allow_unicode=True)

    return jsonify({
        "status": "success",
        "yaml": yaml_str,
        "parsed": parsed_clean,
        "parser_used": parser_used,
        "extraction_source": source,
        "flat_lines": flat_lines,
        "logs": logs,
    })
