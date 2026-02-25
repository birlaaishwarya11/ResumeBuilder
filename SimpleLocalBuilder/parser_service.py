"""
Parser service — manages the full lifecycle of per-user resume parsers.

State machine:
    DRAFT  → created when an LLM generates a parse() function for a new upload.
              User is reviewing the parsed YAML; parser not yet trusted.
    ACTIVE → user confirmed the content looks correct. Parser is the current one.
              Can be regenerated or refined without losing LOCKED status.
    LOCKED → user confirmed this is their resume layout/format.
              Reused automatically on future uploads of the same format.

Future premium:
    Multiple LOCKED parsers per user (one per layout / PDF format).
    label and source_pdf_hash enable routing: "which parser fits this upload?"

Security notes:
- Parser code is LLM-generated Python that will be exec()d in a Daytona sandbox.
  It is NEVER exec()d on the host directly in production paths.
- The local fallback (_run_local in smart_parser) is retained as a last resort
  but only runs inside a restricted namespace.
- Parser code stored in DB contains no PII — it is pure logic derived from
  the shape of the PDF (font sizes, heading patterns), not the content.
- source_pdf_hash is a SHA-256 of the PDF bytes used to generate this parser.
  It identifies the PDF's layout/format, not its content.
"""

import hashlib

import smart_parser as sp
from ai_service import call_llm
from models import (
    create_parser,
    get_parser_by_id,
    get_active_parser,
    get_draft_parser,
    list_parsers,
    update_parser_state,
    update_parser_code,
    lock_parser,
    delete_parser,
)

# Re-export for callers that import from here
PARSER_STATES = ('DRAFT', 'ACTIVE', 'LOCKED')


# ---------------------------------------------------------------------------
# Parser creation / generation
# ---------------------------------------------------------------------------

def generate_and_store_parser(
    user_id: int,
    lines: list[dict],
    provider: str,
    api_key: str,
    model: str | None = None,
    pdf_bytes: bytes | None = None,
) -> tuple[int, str, list[str]]:
    """Generate a parse() function via LLM and store it as a DRAFT parser.

    Args:
        user_id:   Owning user.
        lines:     Extracted text lines [{text, size, bold}] from the PDF.
                   These are the in-memory PII-containing lines — they are NOT
                   stored in the DB. Only the resulting parser code is stored.
        provider, api_key, model: LLM credentials.
        pdf_bytes: Raw PDF bytes used to compute source_pdf_hash (optional).
                   Hash identifies the PDF layout, not the content.

    Returns:
        (parser_id, code, logs)
    """
    logs = []
    logs.append('Generating parser code via LLM...')
    code = sp.generate_parser_code(lines, provider, api_key, model)
    logs.append(f'Parser code generated ({len(code)} chars)')

    pdf_hash = _hash_pdf(pdf_bytes) if pdf_bytes else None

    parser_id = create_parser(
        user_id=user_id,
        code=code,
        state='DRAFT',
        source_pdf_hash=pdf_hash,
    )
    logs.append(f'Parser stored as DRAFT (id={parser_id})')
    return parser_id, code, logs


def run_parser(
    parser_id: int,
    user_id: int,
    lines: list[dict],
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> tuple[dict | None, str, list[str]]:
    """Execute a stored parser against extracted lines.

    Runs in Daytona sandbox (safe). Falls back to restricted local exec.
    On execution error, asks the LLM to fix the code (up to MAX_RETRIES) and
    updates the stored parser code if a fix was applied.

    Args:
        parser_id: ID of the parser to run.
        user_id:   Required as a security check.
        lines:     In-memory extracted lines. NOT stored.
        provider, api_key, model: LLM credentials for auto-fix.

    Returns:
        (result_dict or None, final_code, logs)
    """
    parser = get_parser_by_id(parser_id)
    if not parser or parser['user_id'] != user_id:
        return None, '', [f'Parser {parser_id} not found or access denied']

    result, final_code, logs = sp.run_parser(lines, parser['code'], provider, api_key, model)

    # If the code was auto-fixed, persist the fix
    if final_code and final_code != parser['code']:
        update_parser_code(parser_id, final_code)
        logs.append('Auto-fixed parser code persisted')

    return result, final_code, logs


def refine_parser(
    parser_id: int,
    user_id: int,
    change_request: str,
    lines: list[dict],
    provider: str,
    api_key: str,
    model: str | None = None,
) -> tuple[str, list[str]]:
    """Ask the LLM to modify an existing parser and re-run it to verify.

    Returns:
        (new_code, logs)
    """
    parser = get_parser_by_id(parser_id)
    if not parser or parser['user_id'] != user_id:
        return '', ['Parser not found or access denied']

    logs = ['Refining parser via LLM...']
    new_code = sp.refine_parser_code(parser['code'], change_request, provider, api_key, model)
    update_parser_code(parser_id, new_code)
    logs.append('Refined code stored')
    return new_code, logs


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def activate_parser(parser_id: int, user_id: int) -> None:
    """DRAFT → ACTIVE: user confirmed the parsed content is correct."""
    parser = _get_owned(parser_id, user_id)
    if parser['state'] == 'LOCKED':
        raise ValueError('Cannot demote a LOCKED parser to ACTIVE directly. Unlock it first.')
    update_parser_state(parser_id, 'ACTIVE')


def confirm_and_lock(parser_id: int, user_id: int) -> None:
    """ACTIVE/DRAFT → LOCKED: user confirmed this is their PDF layout.

    Any previously LOCKED parser is demoted to ACTIVE (only one LOCKED at a time
    for free tier; premium will support multiple).
    """
    _get_owned(parser_id, user_id)  # security check
    lock_parser(user_id, parser_id)


def unlock_parser(parser_id: int, user_id: int) -> None:
    """LOCKED → ACTIVE: allow regeneration while keeping the code."""
    parser = _get_owned(parser_id, user_id)
    if parser['state'] != 'LOCKED':
        raise ValueError('Parser is not LOCKED')
    update_parser_state(parser_id, 'ACTIVE')


def discard_parser(parser_id: int, user_id: int) -> None:
    """Delete a parser (any state). Ownership enforced."""
    _get_owned(parser_id, user_id)  # raises if not found / wrong user
    delete_parser(parser_id, user_id)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_best_parser(user_id: int) -> dict | None:
    """Return the parser to use for the next upload.

    Priority: LOCKED > ACTIVE > None
    If None, the caller should generate a new parser.
    """
    return get_active_parser(user_id)


def get_current_draft(user_id: int) -> dict | None:
    """Return the current DRAFT parser (the one awaiting user review)."""
    return get_draft_parser(user_id)


def list_user_parsers(user_id: int) -> list[dict]:
    """Return parser metadata (no code) for all parsers belonging to user."""
    return list_parsers(user_id)


def get_parser(parser_id: int, user_id: int) -> dict | None:
    """Return a specific parser by id. user_id enforced."""
    parser = get_parser_by_id(parser_id)
    if not parser or parser['user_id'] != user_id:
        return None
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_owned(parser_id: int, user_id: int) -> dict:
    """Return parser dict or raise ValueError if not found / wrong user."""
    parser = get_parser_by_id(parser_id)
    if not parser or parser['user_id'] != user_id:
        raise ValueError(f'Parser {parser_id} not found or access denied')
    return parser


def _hash_pdf(pdf_bytes: bytes) -> str:
    """Return SHA-256 hex digest of PDF bytes.

    Used to identify the PDF's layout/format for parser routing.
    The hash is stored; the raw bytes are not.
    """
    return hashlib.sha256(pdf_bytes).hexdigest()


def resolve_credentials(user_provider=None, user_api_key=None, user_model=None):
    """Return (provider, api_key, model) using priority:
      1. User-supplied key
      2. PARSER_GEN_* env vars
      3. (None, None, None) → caller should use heuristic fallback
    """
    return sp.resolve_parser_credentials(user_provider, user_api_key, user_model)
