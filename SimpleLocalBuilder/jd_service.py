"""
JD (Job Description) service.

Current capability:
    analyze(user_id, jd_text, ...)  → match score + structured suggestions
    apply(user_id, session_id, ...)  → AI applies selected suggestions → new YAML version

Future / agentic:
    apply_full(user_id, jd_text, ...)  → analyze + apply all suggestions in one call
    Exposed via MCP so Claude and other agents can call these tools autonomously.

Suggestion schema (each item in suggestions list):
    {
      "id":      str,          # stable key, e.g. "add_keyword_0"
      "type":    str,          # add_keyword | strengthen_bullet | add_section | reorder | rephrase
      "section": str,          # yaml key this suggestion targets, e.g. "technical_skills"
      "value":   str,          # what to add or how to change
      "reason":  str,          # why (short, for user display)
      "priority": int          # 1 (high) … 3 (low)
    }

Security notes:
- jd_text is stored in jd_sessions. It may contain company name / role info
  but is not personal PII.  It is scoped to user_id.
- yaml_content written via resume_service which validates YAML before writing.
- LLM responses are parsed as JSON; we never eval() or exec() them.
"""

import json
import yaml

from ai_service import call_llm, parse_json_response
from resume_service import get_current_resume, save_current_resume, parse_yaml, dump_yaml
from models import (
    create_jd_session,
    update_jd_session,
    mark_jd_applied,
    get_jd_session,
    list_jd_sessions,
)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_ANALYZE_SYSTEM = """\
You are an expert ATS analyst and resume coach.

Given a resume (YAML) and a job description, return a JSON object with exactly two keys:
  "match_score": integer 0–100 (how well the resume matches the JD)
  "suggestions": list of suggestion objects

Each suggestion object must have:
  "id":       string — unique stable key, e.g. "add_keyword_0"
  "type":     one of: add_keyword | strengthen_bullet | add_section | reorder | rephrase
  "section":  the resume YAML key this targets (e.g. "technical_skills", "experience", "projects")
  "value":    the specific change — what keyword to add, how to rewrite a bullet, etc.
  "reason":   one sentence explaining why this helps with the JD
  "priority": integer 1 (must-have), 2 (recommended), or 3 (nice-to-have)

Rules:
- Return 5–15 suggestions ordered by priority.
- Be specific: name the exact keyword, skill, or metric to add.
- Do not invent experience the candidate does not have — only surface what is already present
  but missing from the resume, or suggest how to rephrase existing content.
- Return ONLY valid JSON — no markdown fences, no prose.
"""

_APPLY_SYSTEM = """\
You are an expert resume editor.

You will receive a resume in YAML format and a list of approved suggestions.
Apply ALL of the suggestions to the YAML and return the complete updated YAML.

Rules:
- Preserve the exact YAML structure and keys.
- Do not remove any existing content unless a suggestion explicitly says to.
- Add keywords naturally into existing bullets or skill lists.
- When strengthening a bullet, keep it factual and concise (≤ 2 lines).
- Return ONLY valid YAML — no markdown fences, no explanation.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(
    user_id: int,
    jd_text: str,
    provider: str,
    api_key: str,
    model: str | None = None,
) -> tuple[int, dict, list[dict]]:
    """Analyze the user's current resume against a JD.

    Creates a jd_sessions row and populates it with the analysis.

    Returns:
        (session_id, {match_score, suggestions}, logs)
    """
    logs = []

    resume_yaml = get_current_resume(user_id)
    if not resume_yaml:
        raise ValueError('No resume found. Upload a resume before running JD analysis.')

    # Create session record first (jd_text stored; raw lines are never stored)
    session_id = create_jd_session(user_id, jd_text)
    logs.append(f'JD session created (id={session_id})')

    # Call LLM
    logs.append('Analyzing resume against JD...')
    user_msg = f"RESUME (YAML):\n{resume_yaml}\n\nJOB DESCRIPTION:\n{jd_text}"
    raw = call_llm(provider, api_key, _ANALYZE_SYSTEM, user_msg, model)

    result = parse_json_response(raw)
    match_score = int(result.get('match_score', 0))
    suggestions = result.get('suggestions', [])

    # Assign stable ids if LLM didn't
    for i, s in enumerate(suggestions):
        if not s.get('id'):
            s['id'] = f"{s.get('type', 'suggestion')}_{i}"

    logs.append(f'Analysis complete: score={match_score}, suggestions={len(suggestions)}')

    # Persist analysis results
    update_jd_session(session_id, match_score, suggestions)

    return session_id, {'match_score': match_score, 'suggestions': suggestions}, logs


def apply_suggestions(
    user_id: int,
    session_id: int,
    suggestion_ids: list[str],
    provider: str,
    api_key: str,
    model: str | None = None,
) -> tuple[str, int, list[str]]:
    """Apply a subset of approved suggestions to the user's resume.

    Args:
        user_id:        Owning user (security check).
        session_id:     JD session containing the suggestions.
        suggestion_ids: List of suggestion 'id' values the user approved.
        provider, api_key, model: LLM credentials.

    Returns:
        (new_yaml, version_id, logs)
    """
    logs = []

    session = get_jd_session(session_id, user_id)
    if not session:
        raise ValueError(f'JD session {session_id} not found or access denied')

    suggestions = session.get('suggestions', [])
    if isinstance(suggestions, str):
        suggestions = json.loads(suggestions)

    approved = [s for s in suggestions if s.get('id') in suggestion_ids]
    if not approved:
        raise ValueError('No valid suggestion ids provided')

    logs.append(f'Applying {len(approved)} approved suggestions...')

    resume_yaml = get_current_resume(user_id)
    if not resume_yaml:
        raise ValueError('No current resume found')

    user_msg = (
        f"CURRENT RESUME (YAML):\n{resume_yaml}\n\n"
        f"APPROVED SUGGESTIONS (JSON):\n{json.dumps(approved, indent=2)}"
    )
    raw_yaml = call_llm(provider, api_key, _APPLY_SYSTEM, user_msg, model)

    # Strip markdown fences the LLM might have added
    new_yaml = _strip_yaml_fences(raw_yaml)

    # Validate before writing
    try:
        yaml.safe_load(new_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f'LLM returned invalid YAML after applying suggestions: {e}') from e

    version_id = save_current_resume(
        user_id, new_yaml,
        source='jd_applied',
        label=f'JD session {session_id}: {len(approved)} suggestions applied'
    )
    mark_jd_applied(session_id, version_id)
    logs.append(f'Resume updated and saved as version {version_id}')

    return new_yaml, version_id, logs


def apply_full(
    user_id: int,
    jd_text: str,
    provider: str,
    api_key: str,
    model: str | None = None,
    min_priority: int = 2,
) -> tuple[str, int, dict, list[str]]:
    """Analyze and apply all suggestions in one call (for autonomous agents).

    Only applies suggestions with priority <= min_priority (1=must-have, 2=recommended).

    Returns:
        (new_yaml, version_id, analysis_result, logs)
    """
    all_logs = []

    session_id, analysis, analyze_logs = analyze(user_id, jd_text, provider, api_key, model)
    all_logs.extend(analyze_logs)

    suggestions = analysis['suggestions']
    to_apply = [s['id'] for s in suggestions if s.get('priority', 3) <= min_priority]

    if not to_apply:
        raise ValueError('No high-priority suggestions to apply automatically')

    new_yaml, version_id, apply_logs = apply_suggestions(
        user_id, session_id, to_apply, provider, api_key, model
    )
    all_logs.extend(apply_logs)

    return new_yaml, version_id, analysis, all_logs


def get_session(session_id: int, user_id: int) -> dict | None:
    """Return a JD session with parsed suggestions list."""
    return get_jd_session(session_id, user_id)


def list_sessions(user_id: int) -> list[dict]:
    """Return JD session history for a user (without full jd_text)."""
    return list_jd_sessions(user_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_yaml_fences(text: str) -> str:
    """Remove ```yaml / ``` markdown fences the LLM may have added."""
    text = text.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        # Remove first line (```yaml or ```) and last ``` line
        start = 1
        end = len(lines)
        if lines[-1].strip() == '```':
            end -= 1
        text = '\n'.join(lines[start:end])
    return text
