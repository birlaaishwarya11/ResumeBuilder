"""
Daytona sandbox integration for safe PDF text extraction.

Runs pdfplumber inside an isolated Daytona Cloud sandbox so that:
1. Malformed PDFs can't exploit the host process
2. Text extraction happens in a throwaway container
3. No parser code or AI runs in the sandbox — only raw text extraction

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

            lines = []
            for line_chars in raw_lines:
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
        return False
    try:
        from daytona_sdk import Daytona  # noqa: F401
        return True
    except ImportError:
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
    if not is_available():
        return None, ["Sandbox not available (no API key or SDK not installed)"]
    return _run_in_sandbox(pdf_path, EXTRACTION_SCRIPT)


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
