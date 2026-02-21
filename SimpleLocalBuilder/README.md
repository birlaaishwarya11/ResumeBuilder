# Resume Builder — Multi-User Edition

A local Flask web app for building, editing, and tailoring resumes. Supports multiple users with individual workspaces, PDF upload with automatic parsing, customizable section names, and AI-powered JD matching.

## Features

- **Multi-User Accounts**: Sign up with email/password. Each user gets an isolated workspace with their own resume data, versions, and settings.
- **PDF Upload & Auto-Parse**: Upload an existing resume PDF and have it automatically parsed into editable YAML using heuristic text extraction (no AI required). The parser detects sections like Education, Experience, Skills, and Projects based on font size, bold text, and common heading patterns.
- **Live YAML Editor**: Edit your resume content in YAML format with a live HTML preview side-by-side.
- **Customizable Header**: Edit your name, phone, email, GitHub, LinkedIn, and portfolio links from the Settings panel. The header is stored separately and merged at render time.
- **Customizable Section Names**: Rename any section title (e.g., change "EDUCATION" to "ACADEMIC BACKGROUND") from the Settings panel.
- **Version History**: Save multiple resume versions with keywords (e.g., "tech", "finance"). Restore or delete any version.
- **PDF Download**: Export your resume as a professionally formatted PDF.
- **Styling Options**: Adjust font, font size, line height, margins, and accent color.
- **JD Match (AI)**: Paste a Job Description and use Claude, GPT, or Gemini to score and rank your resume versions against the JD.
- **Grammar Check**: Run an offline grammar check (LanguageTool) or AI-powered proofreading.

## Project Structure

```
SimpleLocalBuilder/
├── local_app.py          # Main Flask application
├── models.py             # SQLite database, user auth, settings CRUD
├── pdf_parser.py         # Heuristic PDF-to-YAML parser (no AI)
├── build_resume.py       # CLI tool to generate PDF from YAML
├── requirements.txt      # Python dependencies
├── templates/
│   ├── editor.html       # Main editor UI
│   ├── resume.html       # Resume HTML/PDF template (Jinja2)
│   ├── login.html        # Login page
│   └── signup.html       # Sign-up page (with optional PDF upload)
└── data/                 # Created at runtime
    └── <user_id>/        # Per-user workspace
        ├── resume.yaml   # Current working resume
        ├── resume_upload.pdf  # Uploaded PDF
        ├── preview.pdf   # Temp preview
        └── versions/     # Saved resume versions
```

## Setup

1. **Clone or navigate to the directory:**
   ```bash
   cd SimpleLocalBuilder
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

   > **Note on WeasyPrint:** WeasyPrint requires system libraries (`cairo`, `pango`, `gdk-pixbuf`). On macOS: `brew install cairo pango gdk-pixbuf libffi`. On Ubuntu: `sudo apt install libcairo2-dev libpango1.0-dev libgdk-pixbuf2.0-dev`.

4. **Run the app:**
   ```bash
   python3 local_app.py
   ```

5. **Open your browser:**
   Go to [http://127.0.0.1:5001](http://127.0.0.1:5001)

## Usage

### First Time
1. Go to `/signup` and create an account.
2. Optionally upload your existing resume PDF during signup — it will be parsed and pre-fill the editor.
3. If you skip the upload, you'll see an upload prompt on the editor page, or you can start writing YAML from scratch.

### Editor Workflow
- **Left pane**: YAML editor for resume content (everything except header/contact info).
- **Right pane**: Live HTML preview of your resume.
- **Toolbar**: Save versions, view history, upload new PDFs, download PDF, and access JD Match.
- **Header**: Displayed above the editor as read-only. Edit it via the **Settings** panel (top-right).

### Settings Panel
Click **Settings** in the top bar to:
- Edit your **name, phone, email, location, GitHub, LinkedIn, portfolio** — these appear in the resume header.
- Rename **section titles** (Education, Technical Skills, Experience, Projects, Extracurricular) to anything you want.

### Saving & Versions
- Enter a keyword (e.g., "devops") in the toolbar and click **Save Version**.
- Click **History** to browse, restore, or delete saved versions.

### JD Match (requires AI API key)
- Click **JD Match**, select a provider (Claude/GPT/Gemini), enter your API key, paste the Job Description.
- **Check Current Resume Score**: Scores your current editor content against the JD.
- **Analyze Saved Versions**: Ranks all saved versions by relevance to the JD.

### Grammar Check
- Click the pencil icon above the editor.
- Choose **Offline** (LanguageTool — requires Java) or **AI** mode.
- Review suggestions and accept/reject each one.

### PDF Download
- Click **Download PDF** to get a formatted PDF.
- Click the eye icon for a **Live View** modal with the rendered PDF.

## PDF Parser Details

The heuristic PDF parser (`pdf_parser.py`) extracts text from PDFs using `pdfplumber` and structures it without any AI:

1. **Name detection**: Identifies the largest-font text as the person's name.
2. **Contact extraction**: Finds email, phone, URLs (GitHub, LinkedIn, portfolio), and location using regex patterns.
3. **Section detection**: Identifies section headings via ALL CAPS text, bold+large font, or matching against common heading keywords.
4. **Section-specific parsing**:
   - Education: Detects institutions, degrees, GPA, dates.
   - Experience: Detects company/role lines with date ranges, then extracts bullet points.
   - Skills: Parses "Category: skill1, skill2" patterns.
   - Projects: Similar to experience with name/event/award fields.
   - Extracurricular: Collected as flat bullet points.

The parser is designed as a best-effort starting point. After upload, users should review and adjust the YAML in the editor.

### Command Line (Optional)
If you prefer not to use the web interface, you can still build from the terminal:
1. Edit a `resume.yaml` file.
2. Run:
   ```bash
   python3 build_resume.py
   ```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `dev-secret-change-in-production` | Flask session secret key. Set to a random string in production. |

## Backup

A backup of the original single-user code is preserved at `SimpleLocalBuilder_BACKUP_<timestamp>/` in the parent directory.



