"""
Daytona sandbox integration for safe PDF processing.

Runs pdfplumber inside an isolated Daytona Cloud sandbox so that:
1. Malformed PDFs can't exploit the host process
2. Text extraction happens in a throwaway container
3. Parser code is executed in the same isolated environment

PII / data-flow contract
------------------------
PDF resumes contain PII (name, email, phone, address, etc.).

* extract_text_in_sandbox()            — returns raw lines to the host.
  The host must NOT persist these lines to any database. They are held
  in memory only for the duration of the request, then discarded.

* extract_and_test_parser()            — preferred when a parser already exists.
  Extraction AND parser execution happen in ONE sandbox session.
  Raw extracted lines are NEVER returned to the host; only the structured
  parsed result (dict) and a content-coverage score are returned.
  This is the most privacy-preserving path: PII lines live and die inside
  the Daytona container.

* The PDF file itself is uploaded to the sandbox temporarily and deleted
  when the sandbox is torn down. Callers should delete the local copy
  (onboarding_upload.pdf) once all sandbox operations are complete.

Requires:
  pip install daytona-sdk
  Environment variables: DAYTONA_API_KEY (required)
  Optional: DAYTONA_API_URL (default: https://app.daytona.io/api)
            DAYTONA_TARGET (default: us)
"""

import os
import json

DAYTONA_API_KEY = os.environ.get('DAYTONA_API_KEY', '')
DAYTONA_API_URL = os.environ.get('DAYTONA_API_URL', 'https://app.daytona.io/api')
DAYTONA_TARGET = os.environ.get('DAYTONA_TARGET', 'us')

# Embedded script: extracts text with font metadata from PDF using pdfplumber
EXTRACTION_SCRIPT = r'''
import sys, json, pdfplumber

def extract(pdf_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            chars = page.chars

            # Collect hyperlink annotations: (top_y, uri)
            page_height = page.height
            link_positions = []
            for h in (page.hyperlinks or []):
                uri = ''.join(c for c in h.get('uri', '') if c.isprintable()).strip()
                if not uri:
                    continue
                if 'top' in h:
                    top = h['top']
                elif 'y1' in h:
                    top = page_height - h['y1']
                else:
                    continue
                link_positions.append((round(top, 1), uri))

            if not chars:
                # Fallback: plain text extraction
                text = page.extract_text()
                if text:
                    lines = [{"text": line, "size": 10.0, "bold": False}
                             for line in text.splitlines() if line.strip()]
                    pages.append({"page": page_num, "lines": lines})
                continue

            # Group chars into lines by y-position
            sorted_chars = sorted(chars, key=lambda c: (round(c['top'], 1), c['x0']))
            current_line_chars = []
            current_y = None
            raw_lines = []

            for ch in sorted_chars:
                y = round(ch['top'], 1)
                if current_y is None or abs(y - current_y) > 3:
                    if current_line_chars:
                        raw_lines.append(current_line_chars)
                    current_line_chars = [ch]
                    current_y = y
                else:
                    current_line_chars.append(ch)
            if current_line_chars:
                raw_lines.append(current_line_chars)

            # Assign each URI to exactly the closest line only
            line_uris = [[] for _ in raw_lines]
            line_ys = [round(lc[0]['top'], 1) for lc in raw_lines]
            for link_top, uri in link_positions:
                if not line_ys:
                    continue
                best_idx = min(range(len(line_ys)), key=lambda i: abs(line_ys[i] - link_top))
                if abs(line_ys[best_idx] - link_top) < 20:
                    line_uris[best_idx].append(uri)

            lines = []
            for line_chars, uris in zip(raw_lines, line_uris):
                line_chars = sorted(line_chars, key=lambda c: c['x0'])
                parts = []
                for i, ch in enumerate(line_chars):
                    if i > 0:
                        prev_end = line_chars[i-1].get('x1', line_chars[i-1]['x0'] + 5)
                        gap = ch['x0'] - prev_end
                        if gap > 2:
                            parts.append(' ')
                    parts.append(ch['text'])
                text = ''.join(parts).strip()
                if not text:
                    continue
                # Append only pre-assigned URIs (one URI → one line, no duplication)
                for uri in uris:
                    if uri not in text:
                        text = text + ' ' + uri
                sizes = [ch.get('size', 0) for ch in line_chars if ch['text'].strip()]
                avg_size = sum(sizes) / len(sizes) if sizes else 10.0
                fonts = [ch.get('fontname', '') for ch in line_chars if ch['text'].strip()]
                is_bold = any('bold' in f.lower() for f in fonts)
                lines.append({"text": text, "size": round(avg_size, 2), "bold": is_bold})

            if lines:
                pages.append({"page": page_num, "lines": lines})

    return {"pages": pages}

if __name__ == '__main__':
    result = extract(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))
'''

# Embedded script: searches for a specific section heading and returns surrounding lines
SEARCH_SCRIPT = r'''
import sys, json, pdfplumber, re

def search(pdf_path, section_hint):
    hint_lower = section_hint.lower().strip()
    all_lines = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chars = page.chars
            if not chars:
                text = page.extract_text()
                if text:
                    for line in text.splitlines():
                        if line.strip():
                            all_lines.append({"text": line.strip(), "size": 10.0, "bold": False})
                continue

            sorted_chars = sorted(chars, key=lambda c: (round(c['top'], 1), c['x0']))
            current_line_chars = []
            current_y = None
            raw_lines = []
            for ch in sorted_chars:
                y = round(ch['top'], 1)
                if current_y is None or abs(y - current_y) > 3:
                    if current_line_chars:
                        raw_lines.append(current_line_chars)
                    current_line_chars = [ch]
                    current_y = y
                else:
                    current_line_chars.append(ch)
            if current_line_chars:
                raw_lines.append(current_line_chars)

            for line_chars in raw_lines:
                line_chars = sorted(line_chars, key=lambda c: c['x0'])
                parts = []
                for i, ch in enumerate(line_chars):
                    if i > 0:
                        prev_end = line_chars[i-1].get('x1', line_chars[i-1]['x0'] + 5)
                        if ch['x0'] - prev_end > 2:
                            parts.append(' ')
                    parts.append(ch['text'])
                text = ''.join(parts).strip()
                if not text:
                    continue
                sizes = [ch.get('size', 0) for ch in line_chars if ch['text'].strip()]
                avg_size = sum(sizes) / len(sizes) if sizes else 10.0
                fonts = [ch.get('fontname', '') for ch in line_chars if ch['text'].strip()]
                is_bold = any('bold' in f.lower() for f in fonts)
                all_lines.append({"text": text, "size": round(avg_size, 2), "bold": is_bold})

    # Search for the heading
    for i, line in enumerate(all_lines):
        line_lower = line["text"].lower().strip()
        # Match if hint is contained in the line or line matches hint
        if hint_lower in line_lower or line_lower in hint_lower:
            # Check if this looks like a heading (bold, large, or ALL CAPS)
            alpha = [c for c in line["text"] if c.isalpha()]
            is_caps = alpha and all(c.isupper() for c in alpha) and len(alpha) > 2
            if line["bold"] or is_caps or line["size"] > 11:
                # Return up to 30 lines after the heading
                context_lines = all_lines[i+1:i+31]
                return {
                    "found": True,
                    "heading": line["text"],
                    "lines": context_lines
                }

    return {"found": False}

if __name__ == '__main__':
    result = search(sys.argv[1], sys.argv[2])
    print(json.dumps(result, ensure_ascii=False))
'''


def is_available():
    """Check if Daytona SDK is installed and an API key is configured."""
    if not DAYTONA_API_KEY:
        print("[Daytona] DAYTONA_API_KEY not set — sandbox disabled")
        return False
    try:
        from daytona_sdk import Daytona  # noqa: F401
        print(f"[Daytona] Available — API URL: {DAYTONA_API_URL}, Target: {DAYTONA_TARGET}")
        return True
    except ImportError:
        print("[Daytona] daytona-sdk not installed — sandbox disabled")
        return False


def _get_daytona():
    """Create a configured Daytona Cloud client."""
    from daytona_sdk import Daytona, DaytonaConfig
    return Daytona(DaytonaConfig(
        api_key=DAYTONA_API_KEY,
        api_url=DAYTONA_API_URL,
        target=DAYTONA_TARGET,
    ))


def _run_in_sandbox(pdf_path, script_code, extra_args=''):
    """Run a Python script in a Daytona sandbox with a PDF file.

    Args:
        pdf_path: Local path to the PDF file.
        script_code: Python script source to execute.
        extra_args: Additional CLI arguments appended after the PDF path.

    Returns:
        tuple: (result_dict, logs_list) where result_dict is parsed JSON output
               or None on failure, and logs_list is a list of log strings.
    """
    from daytona_sdk import CreateSandboxFromSnapshotParams

    daytona = _get_daytona()
    sandbox = None
    logs = []

    try:
        logs.append("Creating sandbox...")
        sandbox = daytona.create(CreateSandboxFromSnapshotParams(language='python'))
        logs.append(f"Sandbox created (id: {getattr(sandbox, 'id', 'unknown')})")

        # Install pdfplumber
        logs.append("Installing pdfplumber...")
        sandbox.process.exec('pip install -q pdfplumber')
        logs.append("pdfplumber installed")

        # Upload the PDF
        logs.append("Uploading PDF to sandbox...")
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        sandbox.fs.upload_file(pdf_bytes, '/tmp/resume.pdf')
        logs.append(f"PDF uploaded ({len(pdf_bytes)} bytes)")

        # Upload and run the script
        logs.append("Running extraction script...")
        sandbox.fs.upload_file(script_code.encode(), '/tmp/script.py')
        cmd = f'python3 /tmp/script.py /tmp/resume.pdf {extra_args}'.strip()
        response = sandbox.process.exec(cmd)

        exit_code = response.exit_code if hasattr(response, 'exit_code') else None
        output = response.result if hasattr(response, 'result') else str(response)

        if exit_code and exit_code != 0:
            logs.append(f"Script exited with code {exit_code}: {output[:200]}")
            return None, logs

        if not output or not output.strip():
            logs.append("Script produced no output")
            return None, logs

        logs.append("Script completed, parsing output...")

        # Extract JSON from output (skip pip noise lines)
        for line in reversed(output.strip().splitlines()):
            line = line.strip()
            if line.startswith('{'):
                result = json.loads(line)
                page_count = len(result.get('pages', []))
                line_count = sum(len(p.get('lines', [])) for p in result.get('pages', []))
                logs.append(f"Extracted {line_count} lines from {page_count} page(s)")
                return result, logs

        result = json.loads(output.strip())
        logs.append("Output parsed successfully")
        return result, logs

    except Exception as e:
        logs.append(f"Error: {e}")
        return None, logs
    finally:
        if sandbox:
            try:
                logs.append("Cleaning up sandbox...")
                daytona.delete(sandbox)
                logs.append("Sandbox deleted")
            except Exception:
                logs.append("Warning: sandbox cleanup failed")


def extract_text_in_sandbox(pdf_path):
    """Extract text with font metadata from a PDF in a Daytona sandbox.

    Args:
        pdf_path: Local path to the PDF file.

    Returns:
        tuple: (result_dict, logs_list) where result_dict has
               {"pages": [{"page": 1, "lines": [{"text": "...", "size": 14.0, "bold": true}]}]}
               or (None, []) if sandbox is unavailable or extraction fails.
    """
    print(f"[Daytona] extract_text_in_sandbox called for: {pdf_path}")
    if not is_available():
        return None, ["Sandbox not available (no API key or SDK not installed)"]
    print("[Daytona] Starting sandbox extraction...")
    result, logs = _run_in_sandbox(pdf_path, EXTRACTION_SCRIPT)
    print(f"[Daytona] Extraction complete. Success: {result is not None}")
    return result, logs


def search_section_in_sandbox(pdf_path, section_hint):
    """Search for a specific section in a PDF using a Daytona sandbox.

    Args:
        pdf_path: Local path to the PDF file.
        section_hint: Section name to search for (e.g., "Awards", "Publications").

    Returns:
        tuple: (result_dict, logs_list) where result_dict is
               {"found": true, "heading": "...", "lines": [...]} or {"found": false}
    """
    if not is_available():
        return {"found": False}, ["Sandbox not available"]

    # Shell-escape the hint
    safe_hint = section_hint.replace("'", "'\\''")
    result, logs = _run_in_sandbox(pdf_path, SEARCH_SCRIPT, f"'{safe_hint}'")
    return (result if result else {"found": False}), logs


# ---------------------------------------------------------------------------
# Combined extract + parser verification (privacy-preserving path)
# ---------------------------------------------------------------------------

# Script that extracts text, runs the user-supplied parser, and computes a
# content-coverage score — all inside the sandbox. Raw lines are NEVER
# returned to the host; only the parsed dict and coverage score are.
_EXTRACT_AND_VERIFY_SCRIPT = r'''
import sys, json, re, traceback, pdfplumber


def extract_flat_lines(pdf_path):
    """Extract all lines as flat list [{text, size, bold}] using pdfplumber."""
    all_lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chars = page.chars
            if not chars:
                text = page.extract_text()
                if text:
                    for line in text.splitlines():
                        if line.strip():
                            all_lines.append({"text": line.strip(), "size": 10.0, "bold": False})
                continue
            sorted_chars = sorted(chars, key=lambda c: (round(c["top"], 1), c["x0"]))
            current_line_chars, current_y, raw_lines = [], None, []
            for ch in sorted_chars:
                y = round(ch["top"], 1)
                if current_y is None or abs(y - current_y) > 3:
                    if current_line_chars:
                        raw_lines.append(current_line_chars)
                    current_line_chars, current_y = [ch], y
                else:
                    current_line_chars.append(ch)
            if current_line_chars:
                raw_lines.append(current_line_chars)
            for lc in raw_lines:
                lc = sorted(lc, key=lambda c: c["x0"])
                parts = []
                for i, ch in enumerate(lc):
                    if i > 0:
                        prev_end = lc[i-1].get("x1", lc[i-1]["x0"] + 5)
                        if ch["x0"] - prev_end > 2:
                            parts.append(" ")
                    parts.append(ch["text"])
                text = "".join(parts).strip()
                if not text:
                    continue
                sizes = [c.get("size", 0) for c in lc if c["text"].strip()]
                avg_size = sum(sizes) / len(sizes) if sizes else 10.0
                fonts = [c.get("fontname", "") for c in lc if c["text"].strip()]
                is_bold = any("bold" in f.lower() for f in fonts)
                all_lines.append({"text": text, "size": round(avg_size, 2), "bold": is_bold})
    return all_lines


def compute_coverage(lines, parsed):
    """Return % of significant words from raw lines that appear in parsed output."""
    _STOPWORDS = {
        "the","and","for","are","was","were","has","have","had","with","this",
        "that","from","been","but","not","they","you","all","can","will","more",
        "also","into","than","its","our","their","your","his","her","out","use",
        "used","using","new","each","which","about","over","get","got","any",
        "one","two","three","four","five","six","seven","eight","nine","ten",
    }
    raw_text = " ".join(l["text"] for l in lines).lower()
    raw_words = set(re.findall(r"\b[a-z]{3,}\b", raw_text)) - _STOPWORDS
    if not raw_words:
        return 0.0

    def get_text(obj):
        if isinstance(obj, str):
            return obj
        if isinstance(obj, list):
            return " ".join(get_text(i) for i in obj)
        if isinstance(obj, dict):
            return " ".join(get_text(v) for v in obj.values())
        return ""

    parsed_words = set(re.findall(r"\b[a-z]{3,}\b", get_text(parsed).lower()))
    found = raw_words & parsed_words
    return round(len(found) / len(raw_words) * 100, 1)


if __name__ == "__main__":
    pdf_path = sys.argv[1]
    lines = extract_flat_lines(pdf_path)
    output = {"line_count": len(lines)}

    parser_code = open("/tmp/parser.py").read()
    namespace = {"re": re, "json": json}
    try:
        exec(parser_code, namespace)
        parsed = namespace["parse"](lines)
        if isinstance(parsed, dict) and "_error" not in parsed:
            output["parsed"] = parsed
            output["coverage_score"] = compute_coverage(lines, parsed)
        else:
            err = parsed.get("_error", "unknown") if isinstance(parsed, dict) else str(parsed)
            output["parser_error"] = err
    except Exception as e:
        output["parser_error"] = str(e)
        output["parser_traceback"] = traceback.format_exc()[-800:]

    # Raw lines are NOT included in output — they stay in the sandbox
    print(json.dumps(output, ensure_ascii=False, default=str))
'''


def extract_and_test_parser(pdf_path, parser_code):
    """Extract text from a PDF and verify the parser against it — in ONE sandbox session.

    This is the privacy-preserving path. Raw extracted lines (which contain
    all PII from the resume) are never returned to the host process. Only the
    structured parsed dict and a content-coverage score come back.

    Use this function when a parser already exists and you want to:
      1. Confirm it still works on a new upload of the same format.
      2. Get the parsed result without holding raw PII on the host.

    Args:
        pdf_path:    Local path to the PDF file (will be uploaded to sandbox).
        parser_code: Python source of the parse(lines) function to test.

    Returns:
        tuple: (result, logs) where result is:
               {
                 "parsed":         dict,   # parsed resume structure
                 "coverage_score": float,  # 0–100, % of words from PDF captured
                 "line_count":     int,    # total lines extracted
               }
               or None on failure.

    Security:
        - PDF bytes uploaded to sandbox; sandbox deleted on completion.
        - parser_code is LLM-generated; it is exec()d only inside the sandbox.
        - Raw lines remain inside the sandbox container throughout.
        - Callers should delete the local PDF file after this call returns.
    """
    if not is_available():
        return None, ['Sandbox not available (no API key or SDK not installed)']

    from daytona_sdk import CreateSandboxFromSnapshotParams

    daytona = _get_daytona()
    sandbox = None
    logs = []

    try:
        logs.append('Creating sandbox for extract+verify...')
        sandbox = daytona.create(CreateSandboxFromSnapshotParams(language='python'))
        logs.append(f'Sandbox created (id: {getattr(sandbox, "id", "unknown")})')

        logs.append('Installing pdfplumber...')
        sandbox.process.exec('pip install -q pdfplumber')

        logs.append('Uploading PDF to sandbox...')
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        sandbox.fs.upload_file(pdf_bytes, '/tmp/resume.pdf')
        logs.append(f'PDF uploaded ({len(pdf_bytes)} bytes)')

        # Upload parser code (exec()d inside the sandbox, never on the host)
        sandbox.fs.upload_file(parser_code.encode(), '/tmp/parser.py')
        logs.append('Parser code uploaded to sandbox')

        # Upload and run the combined extract+verify script
        sandbox.fs.upload_file(_EXTRACT_AND_VERIFY_SCRIPT.encode(), '/tmp/verify.py')
        response = sandbox.process.exec('python3 /tmp/verify.py /tmp/resume.pdf')

        exit_code = response.exit_code if hasattr(response, 'exit_code') else None
        output = response.result if hasattr(response, 'result') else str(response)

        if exit_code and exit_code != 0:
            logs.append(f'Verify script exited {exit_code}: {output[:300]}')
            return None, logs

        # Parse result JSON from last JSON line in output
        for line in reversed(output.strip().splitlines()):
            line = line.strip()
            if line.startswith('{'):
                result = json.loads(line)
                if 'parser_error' in result:
                    logs.append(f"Parser error in sandbox: {result['parser_error']}")
                    return None, logs
                cov = result.get('coverage_score', 0)
                logs.append(
                    f"Extract+verify complete: {result.get('line_count', 0)} lines, "
                    f"coverage={cov}%"
                )
                return result, logs

        logs.append(f'No JSON found in output: {output[:200]}')
        return None, logs

    except Exception as e:
        logs.append(f'Error: {e}')
        return None, logs
    finally:
        if sandbox:
            try:
                daytona.delete(sandbox)
                logs.append('Sandbox deleted — PII lines discarded with container')
            except Exception:
                logs.append('Warning: sandbox cleanup failed')
