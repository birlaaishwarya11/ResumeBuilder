# Resume Builder

A multi-user Flask web app for uploading, parsing, editing, and tailoring resumes. Features an LLM-generated smart parser, Daytona sandbox execution, live YAML editing with preview, JD matching, and a REST tools API for external testing.

---

## Architecture Overview

```
Browser (onboarding.html / editor.html)
    │
    │  HTTP/JSON
    ▼
local_app.py  ──── Flask routes (auth, onboarding, editor, AI operations)
    │
    ├── pdf_parser.py      Heuristic PDF-to-YAML (no AI required)
    │       └── pdfplumber  Raw char/font extraction + hyperlink injection
    │
    ├── smart_parser.py    LLM-generated per-resume parser
    │       └── sandbox.py  Daytona sandbox execution of generated code
    │
    ├── confidence.py      Score parse quality (0–100)
    ├── models.py          SQLite/PostgreSQL: users, settings, parser store
    └── tools.py           REST tools API (parse_resume, lock_parser, ...)
```

### Parse Pipeline (called on every PDF upload)

```
PDF upload
    │
    ▼
1. EXTRACT  ──── sandbox.py:extract_text_in_sandbox()   [Daytona sandbox]
                 → pdf_parser.py:extract_text_local()    [local fallback]
    │
    ▼
2. PARSE (priority order)
    │
    ├─ 2a. Locked smart parser
    │       smart_parser.py:run_parser(stored_code)
    │
    ├─ 2b. Generate new smart parser  (requires API key)
    │       smart_parser.py:generate_parser_code()   ← LLM writes parse()
    │       smart_parser.py:run_parser(generated_code)
    │           └── sandbox.py:_run_in_daytona()     [Daytona execution]
    │               → smart_parser.py:_run_local()   [local fallback]
    │               On error: LLM fixes code, retry up to MAX_RETRIES=2
    │
    └─ 2c. Heuristic fallback (always works, no AI)
            pdf_parser.py:parse_resume_from_extracted()
    │
    ▼
3. POST-PROCESS
    smart_parser.py:normalize_dates()
    confidence.py:score_parsed_resume()
    │
    ▼
4. RETURN  JSON: { yaml, header, style, section_names, parser_used, confidence }
```

---

## File Reference

| File | Purpose | Key functions/routes |
|------|---------|---------------------|
| `local_app.py` | Main Flask app — all web routes | `POST /api/upload_resume`, `POST /api/preview`, `POST /api/ai_change`, `POST /api/complete_onboarding`, `call_ai_provider()` |
| `pdf_parser.py` | Heuristic PDF parser | `extract_text_local()`, `_build_line()`, `parse_resume_from_extracted()`, `classify_section()`, `extract_style_from_pdf()` |
| `smart_parser.py` | LLM-generated parser + sandbox runner | `generate_parser_code()`, `run_parser()`, `refine_parser_code()`, `normalize_dates()`, `resolve_parser_credentials()` |
| `sandbox.py` | Daytona sandbox integration | `extract_text_in_sandbox()`, `is_available()`, `EXTRACTION_SCRIPT`, `SEARCH_SCRIPT` |
| `tools.py` | REST tools API | `POST /tools/parse_resume`, `POST /tools/lock_parser`, `GET /tools/parser_status` |
| `confidence.py` | Parse quality scoring | `score_parsed_resume()` |
| `models.py` | DB models, auth, per-user parser storage | `create_user()`, `authenticate_user()`, `save_user_parser()`, `get_user_parser()` |
| `build_resume.py` | CLI: YAML → PDF | `build_resume()` |
| `deploy_tfy.py` | Truefoundry deployment script | — |

---

## How Tools Are Called

### `pdf_parser.py` — Heuristic extraction

Called unconditionally on every parse (either directly or as fallback):

```
local_app.py:upload_resume()
  → extract_text_local(pdf_path)           # builds {pages:[{lines:[{text,size,bold}]}]}
      → pdfplumber page.chars              # raw characters with font metadata
      → page.hyperlinks                    # inject hyperlink URIs into line text
      → _build_line(chars, uris)           # gap-aware text building per line
  → parse_resume_from_extracted(data)
      → _parse_from_lines(lines)
          → is_section_heading()           # size + bold + keyword detection
          → classify_section()             # maps heading → YAML key
          → parse_education_section()
          → parse_experience_section()
          → parse_skills_section()
          → parse_projects_section()
          → parse_extracurricular_section()
```

### `smart_parser.py` — LLM-generated parser

Called when an AI API key is provided or a locked parser exists:

```
local_app.py:upload_resume()
  → sp.resolve_parser_credentials(provider, api_key, model)
      # Priority: user-supplied key → PARSER_GEN_API_KEY env → None

  # If locked parser exists:
  → sp.run_parser(flat_lines, stored_code, provider, api_key, model)

  # If no locked parser but credentials available:
  → sp.generate_parser_code(flat_lines, provider, api_key, model)
      → LLM prompt: "Write a parse(lines) function for this resume"
      → call_ai_provider() in local_app.py
  → sp.run_parser(generated_code, ...)
      → _run_in_daytona(lines, code)        # sandbox execution
          → upload lines.json, parser.py, runner.py to Daytona
          → exec python3 /tmp/runner.py
          → parse error → _FIX_PROMPT → LLM fix → retry (up to 2×)
      → _run_local(lines, code)             # fallback if Daytona unavailable
  → sp.normalize_dates(result)             # normalise all date strings
```

### `sandbox.py` — Daytona sandbox

Used in two separate contexts:

1. **PDF extraction** (always attempted first):
   ```
   daytona_sandbox.extract_text_in_sandbox(pdf_path)
     → uploads PDF to Daytona
     → runs EXTRACTION_SCRIPT (pdfplumber inside sandbox)
     → returns {pages:[{lines:[{text,size,bold}]}]}
   ```

2. **Parser execution** (inside `smart_parser.run_parser`):
   ```
   _run_in_daytona(lines, code)
     → uploads lines.json, parser.py, runner.py
     → runs runner.py → calls parse(lines) → returns JSON
   ```

### `tools.py` — REST Tools API

External testing API — not used by the main UI. Call with curl or Postman:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/tools/parse_resume` | POST | Upload a PDF, run full pipeline, get YAML + parsed dict |
| `/tools/lock_parser` | POST | Lock (`action=lock`), unlock, or clear the smart parser |
| `/tools/parser_status` | GET | Check if user has a stored/locked parser |

**`POST /tools/parse_resume`** parameters:
- `resume_pdf` — PDF file (multipart)
- `parser` — `"heuristic"` / `"smart"` / `"auto"` (default: `"auto"`)
- `provider` — `"anthropic"` / `"openai"` / `"gemini"`
- `api_key` — LLM API key
- `model` — model name (optional)

Response includes: `yaml`, `parsed`, `parser_used`, `extraction_source`, `flat_lines`, `logs`

### `call_ai_provider()` — LLM abstraction (in `local_app.py`)

Single function used by all AI features (smart parser generation, AI edit, JD match, grammar check):

```python
call_ai_provider(provider, api_key, system_prompt, user_message, model=None)
```

| Provider | SDK | Default model |
|----------|-----|---------------|
| `anthropic` | `anthropic` Python SDK | `claude-3-haiku-20240307` |
| `openai` | `openai` Python SDK | `gpt-3.5-turbo` |
| `gemini` | `google-genai` SDK (v1 API) | `gemini-2.0-flash` |

> **Note (Gemini):** The Gemini v1 API does not accept `systemInstruction` in `GenerateContentConfig`. The system prompt is folded into the user message content string instead.

---

## Project Structure

```
SimpleLocalBuilder/
├── local_app.py          # Main Flask app (auth, onboarding, editor routes)
├── pdf_parser.py         # Heuristic PDF extraction and section parsing
├── smart_parser.py       # LLM parser generation, sandbox execution, date normalisation
├── sandbox.py            # Daytona sandbox client (extraction + parser execution)
├── tools.py              # REST tools API for external testing
├── confidence.py         # Parse quality scoring
├── models.py             # SQLite/PostgreSQL schema, user auth, parser storage
├── build_resume.py       # CLI: YAML → PDF via WeasyPrint
├── deploy_tfy.py         # Truefoundry deployment helper
├── requirements.txt
├── Dockerfile
└── templates/
    ├── onboarding.html   # Step 1: PDF upload + smart parser review
    ├── editor.html       # Main editor (YAML + live preview + AI tools)
    ├── resume.html       # Resume render template (Jinja2 → HTML/PDF)
    ├── login.html
    └── signup.html
```

---

## Setup (Local)

1. **Install dependencies:**
   ```bash
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```

   WeasyPrint also requires system libraries:
   - macOS: `brew install cairo pango gdk-pixbuf libffi`
   - Ubuntu: `sudo apt install libcairo2-dev libpango1.0-dev libgdk-pixbuf2.0-dev`

2. **Run:**
   ```bash
   python3 local_app.py
   # or
   flask run --port 5001
   ```

3. Open [http://127.0.0.1:5001](http://127.0.0.1:5001)

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `dev-secret-change-in-production` | Flask session key |
| `DATABASE_URL` | SQLite `resume_builder.db` | PostgreSQL URL for production |
| `PARSER_GEN_API_KEY` | — | Server-side API key for smart parser generation (optional — users can supply their own) |
| `PARSER_GEN_PROVIDER` | `anthropic` | Provider for server-side parser generation |
| `PARSER_GEN_MODEL` | `claude-sonnet-4-6` | Model for server-side parser generation |
| `DAYTONA_API_KEY` | — | Daytona sandbox API key |
| `DAYTONA_API_URL` | — | Daytona server URL |
| `DAYTONA_TARGET` | — | Daytona workspace target |

---

## Deployment (Truefoundry)

Use `deploy_tfy.py` to deploy to Truefoundry. Set the env vars above in the TFY service config. The `Dockerfile` is already configured for the production environment (Debian Bookworm, PostgreSQL support).

```bash
python3 deploy_tfy.py
```
