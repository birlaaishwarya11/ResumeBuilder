"""
Smart parser: LLM-generated resume parser, Daytona sandbox execution,
date normalization, and retry logic.

Pipeline:
  1. generate_parser_code()  -> LLM writes parse(lines) Python function
  2. run_parser()            -> executes code in Daytona (with 2-retry fix loop)
                               or falls back to local exec
  3. normalize_dates()       -> normalises date strings across the parsed dict

API key priority:
  User-supplied key > PARSER_GEN_API_KEY env var > heuristic fallback
"""

import os
import re
import json

# --- Server-side parser-gen credentials (optional) ---
PARSER_GEN_API_KEY = os.environ.get('PARSER_GEN_API_KEY', '')
PARSER_GEN_PROVIDER = os.environ.get('PARSER_GEN_PROVIDER', 'anthropic')
PARSER_GEN_MODEL = os.environ.get('PARSER_GEN_MODEL', 'claude-sonnet-4-6')

# How many lines of the resume to show the LLM when generating the parser
SAMPLE_LINES = 60

# Max Daytona execution retries on error
MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_GENERATE_PROMPT = """\
You are an expert Python developer specialising in resume parsing.

I will give you a sample of lines extracted from a PDF resume. Each line is a dict:
  {{"text": str, "size": float, "bold": bool}}

Your task: write a Python function called `parse` with this signature:

    def parse(lines: list[dict]) -> dict:

The function must return a dict with the following keys (omit keys that have no data):

    {{
      "name": str,
      "contact": {{
        "email": str, "phone": str, "location": str,
        "linkedin": str, "github": str,
        "portfolio_url": str, "portfolio_label": str
      }},
      "education":        [dict, ...],   # list of entry dicts
      "experience":       [dict, ...],
      "technical_skills": [dict, ...],   # {{"category": str, "skills": str}}
      "projects":         [dict, ...],
      "extracurricular":  {{"bullets": [str, ...]}},
      # any other sections: key -> list of strings or list of dicts
      "_section_headings": {{key: "EXACT HEADING TEXT FROM PDF", ...}}
    }}

Rules:
- NO import statements — only Python builtins are available (re, json are pre-imported).
- Use size and bold metadata to identify section headings for THIS specific resume.
- Extract ALL content; do not summarise or omit any bullet point or detail.
- Normalise "Present"/"Current"/"Now"/"ongoing" in date strings to "Present".
- Return an empty dict on total failure (wrap risky code in try/except).
- The function must be completely self-contained.

Here are the first {n} lines of the resume (JSON):

{lines_json}

Respond with ONLY the Python function — no markdown fences, no explanation.
"""

_FIX_PROMPT = """\
The following Python parser function raised an error when executed.

ERROR:
{error}

ORIGINAL CODE:
{code}

Fix the bug and return ONLY the corrected Python function (no markdown fences, no explanation).
"""

_REFINE_PROMPT = """\
Below is a Python resume parser function. The user wants to change something about it.

USER REQUEST:
{change_request}

CURRENT CODE:
{code}

Return ONLY the updated Python function (no markdown fences, no explanation).
"""


# ---------------------------------------------------------------------------
# LLM call (reuses call_ai_provider from local_app; imported lazily)
# ---------------------------------------------------------------------------

def _call_llm(provider, api_key, prompt, model=None):
    """Thin wrapper around call_ai_provider from local_app."""
    from local_app import call_ai_provider
    return call_ai_provider(provider, api_key, '', prompt, model)


def _clean_code(raw: str) -> str:
    """Strip markdown fences if the LLM wrapped the code anyway."""
    raw = raw.strip()
    raw = re.sub(r'^```(?:python)?\n?', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\n?```$', '', raw)
    return raw.strip()


# ---------------------------------------------------------------------------
# Parser generation
# ---------------------------------------------------------------------------

def resolve_parser_credentials(user_provider=None, user_api_key=None, user_model=None):
    """
    Return (provider, api_key, model) using priority:
      1. User-supplied key
      2. PARSER_GEN_API_KEY env var
      3. None (caller should fall back to heuristic)
    """
    if user_api_key and user_api_key.strip():
        return (
            user_provider or 'anthropic',
            user_api_key.strip(),
            user_model or None,
        )
    if PARSER_GEN_API_KEY:
        return (
            PARSER_GEN_PROVIDER,
            PARSER_GEN_API_KEY,
            PARSER_GEN_MODEL,
        )
    return None, None, None


def generate_parser_code(lines, provider, api_key, model=None):
    """
    Ask the LLM to write a parse(lines) function tailored to this resume.

    Args:
        lines: full list of {text, size, bold} dicts
        provider, api_key, model: LLM credentials

    Returns:
        str: Python source code for the parse() function

    Raises:
        Exception on LLM call failure
    """
    sample = lines[:SAMPLE_LINES]
    prompt = _GENERATE_PROMPT.format(
        n=len(sample),
        lines_json=json.dumps(sample, indent=2, ensure_ascii=False)
    )
    raw = _call_llm(provider, api_key, prompt, model)
    return _clean_code(raw)


def refine_parser_code(code, change_request, provider, api_key, model=None):
    """
    Ask the LLM to modify an existing parse() function.

    Args:
        code: current Python source
        change_request: plain-text description of what to change
        provider, api_key, model: LLM credentials

    Returns:
        str: updated Python source
    """
    prompt = _REFINE_PROMPT.format(
        change_request=change_request,
        code=code,
    )
    raw = _call_llm(provider, api_key, prompt, model)
    return _clean_code(raw)


# ---------------------------------------------------------------------------
# Sandbox execution helpers
# ---------------------------------------------------------------------------

_RUNNER_SCRIPT = r'''
import sys, json, traceback, re

with open('/tmp/lines.json') as f:
    lines = json.load(f)

with open('/tmp/parser.py') as f:
    parser_code = f.read()

namespace = {'re': re, 'json': json}
try:
    exec(parser_code, namespace)
    result = namespace['parse'](lines)
    print(json.dumps(result, ensure_ascii=False, default=str))
except Exception as e:
    tb = traceback.format_exc()
    print(json.dumps({"_error": str(e), "_traceback": tb[-800:]}))
'''


def _run_in_daytona(lines, code):
    """
    Execute the generated parse() function in a Daytona sandbox.

    Returns:
        (result_dict_or_None, error_str_or_None, logs_list)
    """
    import sandbox as daytona_sandbox
    if not daytona_sandbox.is_available():
        return None, "Daytona not available", []

    from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

    api_key = daytona_sandbox.DAYTONA_API_KEY
    api_url = daytona_sandbox.DAYTONA_API_URL
    target = daytona_sandbox.DAYTONA_TARGET

    client = Daytona(DaytonaConfig(api_key=api_key, api_url=api_url, target=target))
    sb = None
    logs = []

    try:
        logs.append("Creating sandbox for parser execution...")
        sb = client.create(CreateSandboxFromSnapshotParams(language='python'))
        logs.append(f"Sandbox created (id: {getattr(sb, 'id', '?')})")

        # Upload lines JSON
        sb.fs.upload_file(
            json.dumps(lines, ensure_ascii=False).encode(),
            '/tmp/lines.json'
        )
        # Upload generated parser code
        sb.fs.upload_file(code.encode(), '/tmp/parser.py')
        # Upload runner
        sb.fs.upload_file(_RUNNER_SCRIPT.encode(), '/tmp/runner.py')

        logs.append("Running parser in sandbox...")
        resp = sb.process.exec('python3 /tmp/runner.py')
        output = resp.result if hasattr(resp, 'result') else str(resp)
        exit_code = resp.exit_code if hasattr(resp, 'exit_code') else None

        if exit_code and exit_code != 0:
            return None, f"Runner exited {exit_code}: {output[:300]}", logs

        # Find last JSON line in output
        for line in reversed(output.strip().splitlines()):
            line = line.strip()
            if line.startswith('{'):
                result = json.loads(line)
                if '_error' in result:
                    return None, result.get('_error', 'unknown error') + '\n' + result.get('_traceback', ''), logs
                logs.append("Parser ran successfully in sandbox")
                return result, None, logs

        return None, f"No JSON in output: {output[:300]}", logs

    except Exception as e:
        return None, str(e), logs
    finally:
        if sb:
            try:
                client.delete(sb)
                logs.append("Sandbox deleted")
            except Exception:
                pass


def _run_local(lines, code):
    """
    Execute parse(lines) locally with restricted builtins.
    Only used when Daytona is unavailable.

    Returns:
        (result_dict_or_None, error_str_or_None)
    """
    SAFE_BUILTINS = {
        '__builtins__': {
            'len': len, 'range': range, 'enumerate': enumerate,
            'zip': zip, 'dict': dict, 'list': list, 'set': set,
            'tuple': tuple, 'str': str, 'int': int, 'float': float,
            'bool': bool, 'isinstance': isinstance, 'hasattr': hasattr,
            'getattr': getattr, 'sorted': sorted, 'reversed': reversed,
            'min': min, 'max': max, 'sum': sum, 'abs': abs,
            'any': any, 'all': all, 'filter': filter, 'map': map,
            'print': print, 'repr': repr, 'type': type,
            'None': None, 'True': True, 'False': False,
            'Exception': Exception, 'ValueError': ValueError,
            'KeyError': KeyError, 'IndexError': IndexError,
        }
    }
    namespace = {'re': __import__('re'), 'json': __import__('json')}
    namespace.update(SAFE_BUILTINS)
    try:
        exec(code, namespace)
        result = namespace['parse'](lines)
        return result, None
    except Exception as e:
        return None, str(e)


def run_parser(lines, code, provider=None, api_key=None, model=None):
    """
    Run the generated parser with up to MAX_RETRIES fix attempts on error.

    Strategy:
      1. Try Daytona (if available)
      2. On error: ask LLM to fix code, retry (up to MAX_RETRIES times)
      3. After retries exhausted: try local exec
      4. On local exec failure: return None (caller should use heuristic)

    Returns:
        (result_dict_or_None, final_code_str, logs_list)
        final_code_str may differ from input code if fixes were applied.
    """
    current_code = code
    all_logs = []

    for attempt in range(MAX_RETRIES + 1):
        result, error, logs = _run_in_daytona(lines, current_code)
        all_logs.extend(logs)

        if result is not None:
            return result, current_code, all_logs

        all_logs.append(f"Attempt {attempt + 1} failed: {error[:200] if error else 'unknown'}")

        if attempt < MAX_RETRIES and error and provider and api_key:
            # Ask LLM to fix the code
            all_logs.append("Asking LLM to fix the error...")
            try:
                fix_prompt = _FIX_PROMPT.format(error=error[:600], code=current_code)
                fixed_raw = _call_llm(provider, api_key, fix_prompt, model)
                current_code = _clean_code(fixed_raw)
                all_logs.append("Received fix from LLM, retrying...")
            except Exception as fix_err:
                all_logs.append(f"Fix LLM call failed: {fix_err}")
                break
        else:
            break

    # Daytona retries exhausted — try local exec as last resort
    all_logs.append("Falling back to local exec...")
    result, error = _run_local(lines, current_code)
    if result is not None:
        all_logs.append("Local exec succeeded")
        return result, current_code, all_logs

    all_logs.append(f"Local exec failed: {error}")
    return None, current_code, all_logs


# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
    'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
    'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
}
_CURRENT_TOKENS = {'present', 'current', 'now', 'ongoing', 'today', 'till date', 'to date'}


def _parse_date_token(token):
    """Try to parse a date token to YYYY-MM string. Returns None if unparseable."""
    t = token.strip().lower().rstrip('.')
    # Check for current
    if t in _CURRENT_TOKENS:
        return 'Present'
    # Abbreviated month + year: "jan 2020" / "jan'20" / "jan20"
    m = re.match(r"([a-z]{3})['\s]?(\d{2,4})", t)
    if m:
        mon = _MONTH_MAP.get(m.group(1))
        yr = m.group(2)
        if len(yr) == 2:
            yr = ('20' if int(yr) < 70 else '19') + yr
        if mon:
            return f"{yr}-{mon}"
    # Year only: "2020"
    m = re.match(r'^(\d{4})$', t)
    if m:
        return m.group(1)
    # Q1/Q2/Q3/Q4 YYYY
    m = re.match(r'q([1-4])\s*(\d{4})', t)
    if m:
        qmon = {'1': '01', '2': '04', '3': '07', '4': '10'}[m.group(1)]
        return f"{m.group(2)}-{qmon}"
    return None


def normalize_date_string(date_str):
    """
    Normalise a date range string like "Jan 2020 - Present" or "2018–2021"
    to {"start": "2020-01", "end": None, "is_current": True, "raw": "Jan 2020 - Present"}.
    Returns None if the string doesn't look like a date.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    raw = date_str.strip()
    # Split on em/en dash or " to "
    parts = re.split(r'\s*[–—\-]\s*|\s+to\s+', raw, maxsplit=1)
    start_token = parts[0].strip() if parts else ''
    end_token = parts[1].strip() if len(parts) > 1 else ''

    start = _parse_date_token(start_token) if start_token else None
    end = _parse_date_token(end_token) if end_token else None

    if start is None and end is None:
        return None

    is_current = (end == 'Present') or (not end_token and not end)
    return {
        "start": start,
        "end": None if is_current else end,
        "is_current": is_current,
        "raw": raw,
    }


def _normalize_entry_dates(entry):
    """Recursively normalise date fields in a dict or list."""
    if isinstance(entry, list):
        return [_normalize_entry_dates(e) for e in entry]
    if not isinstance(entry, dict):
        return entry
    result = {}
    for k, v in entry.items():
        if k == 'date' and isinstance(v, str):
            normalized = normalize_date_string(v)
            result[k] = v  # keep raw string for template rendering
            if normalized:
                result['_date_parsed'] = normalized
        else:
            result[k] = _normalize_entry_dates(v)
    return result


def normalize_dates(parsed):
    """
    Walk the parsed resume dict and add _date_parsed fields next to every
    'date' string field. Non-destructive: original 'date' strings preserved.

    Returns the modified dict.
    """
    if not isinstance(parsed, dict):
        return parsed
    result = {}
    for k, v in parsed.items():
        if isinstance(v, list):
            result[k] = _normalize_entry_dates(v)
        elif isinstance(v, dict) and k not in ('contact', '_section_headings'):
            result[k] = _normalize_entry_dates(v)
        else:
            result[k] = v
    return result
