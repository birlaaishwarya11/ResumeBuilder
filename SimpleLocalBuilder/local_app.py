from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from functools import wraps
import yaml
import os
import json
import glob
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from models import (
    init_db, create_user, authenticate_user, get_user_by_id,
    get_user_dir, get_user_versions_dir, get_user_settings,
    update_user_settings, verify_user_password, delete_user,
    is_onboarding_complete, mark_onboarding_complete,
    DEFAULT_SECTION_NAMES
)
from pdf_parser import parse_resume_pdf, extract_style_from_pdf

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

# Initialize database on startup
init_db()


# --- Auth helpers ---

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def get_current_user_id():
    return session.get('user_id')


def get_current_user_header():
    """Get the header (name + contact) for the current user from settings."""
    settings = get_user_settings(get_current_user_id())
    return settings.get('header', {})


def get_current_section_names():
    """Get section name mappings for the current user."""
    settings = get_user_settings(get_current_user_id())
    return settings.get('section_names', DEFAULT_SECTION_NAMES.copy())


def get_current_custom_sections():
    """Get custom sections for the current user."""
    settings = get_user_settings(get_current_user_id())
    return settings.get('custom_sections', [])


def merge_header(partial_data, header):
    """Merges user header into resume data."""
    if isinstance(partial_data, str):
        try:
            partial_data = yaml.safe_load(partial_data) or {}
        except Exception:
            partial_data = {}
    if not isinstance(partial_data, dict):
        partial_data = {}
    full_data = partial_data.copy()
    full_data.update(header)
    return full_data


def strip_header(full_data, header):
    """Removes header keys from resume data."""
    if isinstance(full_data, str):
        try:
            full_data = yaml.safe_load(full_data) or {}
        except Exception:
            full_data = {}
    if not isinstance(full_data, dict):
        return {}
    partial_data = full_data.copy()
    for key in header:
        if key in partial_data:
            del partial_data[key]
    return partial_data


BUILTIN_KEYS = {'name', 'contact', 'summary', 'education', 'technical_skills',
                 'experience', 'projects', 'extracurricular'}


def _infer_render_type(data):
    """Infer whether a parsed section is bullets, entries, or skills."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            if 'category' in data[0] and 'skills' in data[0]:
                return 'skills'
            if any(k in data[0] for k in ('company', 'role', 'institution', 'degree', 'date', 'name', 'event', 'award')):
                return 'entries'
        return 'bullets'
    if isinstance(data, dict) and 'bullets' in data:
        return 'bullets'
    return 'bullets'


def _ai_parse_resume(pdf_path, provider, api_key, model=None):
    """Use an AI provider to parse a resume PDF into structured YAML data."""
    import pdfplumber

    # Extract raw text from PDF
    text_lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_lines.append(text)
    raw_text = '\n'.join(text_lines)

    if not raw_text.strip():
        return {}

    system_prompt = """You are a resume parser. Extract the resume content into a structured JSON object.
Return ONLY valid JSON with these exact keys (omit empty sections):
{
  "name": "Full Name",
  "contact": {
    "location": "City, State",
    "phone": "+1-555-123-4567",
    "email": "email@example.com",
    "github": "https://github.com/...",
    "linkedin": "https://linkedin.com/in/...",
    "portfolio_url": "",
    "portfolio_label": "Portfolio"
  },
  "summary": "Professional summary text...",
  "education": [
    {
      "institution": "University Name",
      "location": "City, State",
      "degree": "Degree Name",
      "gpa": "3.9",
      "date": "May 2026",
      "honors": "Optional honors/awards",
      "coursework": "Optional relevant coursework"
    }
  ],
  "technical_skills": [
    {"category": "Category Name", "skills": "Skill1, Skill2, Skill3"}
  ],
  "experience": [
    {
      "company": "Company Name",
      "role": "Job Title",
      "location": "City, State",
      "date": "Jan 2023 - Present",
      "bullets": ["Achievement 1", "Achievement 2"]
    }
  ],
  "projects": [
    {
      "name": "Project Name",
      "subtitle": "Optional subtitle",
      "event": "Optional event/hackathon",
      "award": "Optional award",
      "date": "Date",
      "url": "",
      "bullets": ["Description 1", "Description 2"]
    }
  ],
  "extracurricular": {
    "bullets": ["Activity 1", "Activity 2"]
  }
}

For sections that don't fit the above categories (e.g., Certifications, Awards, Publications, Languages),
create additional top-level keys using snake_case (e.g., "certifications", "awards_honors").
Use the same structure patterns: list of bullets, list of entries with company/role/date/bullets, or list of category/skills objects.
Preserve ALL content from the resume. Do not summarize or omit anything."""

    user_msg = f"Parse this resume:\n\n{raw_text}"
    response_text = call_ai_provider(provider, api_key, system_prompt, user_msg, model)
    parsed = parse_json_response(response_text)

    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        return {}

    return parsed


def load_yaml(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r') as f:
        return yaml.safe_load(f) or {}


# --- Auth routes ---

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        return render_template('signup.html')

    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')

    if not name or not email or not password:
        return render_template('signup.html', error='All fields are required.')
    if len(password) < 6:
        return render_template('signup.html', error='Password must be at least 6 characters.')

    user_id = create_user(name, email, password)
    if user_id is None:
        return render_template('signup.html', error='An account with this email already exists.')

    session['user_id'] = user_id
    return redirect(url_for('onboarding'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')

    user = authenticate_user(email, password)
    if not user:
        return render_template('login.html', error='Invalid email or password.')

    session['user_id'] = user['id']
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- Onboarding ---

@app.route('/onboarding')
@login_required
def onboarding():
    user_id = get_current_user_id()
    if is_onboarding_complete(user_id):
        return redirect(url_for('index'))
    user = get_user_by_id(user_id)
    return render_template('onboarding.html', user=user)


@app.route('/api/upload_resume', methods=['POST'])
@login_required
def upload_resume():
    """Upload and parse a PDF during onboarding. Returns parsed data as JSON."""
    user_id = get_current_user_id()
    if is_onboarding_complete(user_id):
        return jsonify({"status": "error", "message": "Onboarding already completed."}), 400

    pdf_file = request.files.get('resume_pdf')
    if not pdf_file or not pdf_file.filename or not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify({"status": "error", "message": "Please upload a valid PDF file."}), 400

    parse_mode = request.form.get('parse_mode', 'local')
    ai_provider = request.form.get('ai_provider', '')
    ai_api_key = request.form.get('ai_api_key', '')
    ai_model = request.form.get('ai_model', '')

    user_dir = get_user_dir(user_id)
    pdf_path = os.path.join(user_dir, 'onboarding_upload.pdf')
    pdf_file.save(pdf_path)

    try:
        extracted_style = extract_style_from_pdf(pdf_path)

        if parse_mode == 'ai' and ai_api_key:
            parsed = _ai_parse_resume(pdf_path, ai_provider, ai_api_key, ai_model)
        else:
            parsed = parse_resume_pdf(pdf_path)

        if not parsed:
            return jsonify({"status": "error", "message": "Could not parse the PDF. Try a different file or AI mode."}), 500

        header = {
            "name": parsed.get('name', ''),
            "contact": parsed.get('contact', {})
        }

        custom_sections = []
        for key in parsed:
            if key not in BUILTIN_KEYS:
                display_name = key.replace('_', ' ').upper()
                render_type = _infer_render_type(parsed[key])
                custom_sections.append({
                    "key": key,
                    "display_name": display_name,
                    "render_type": render_type
                })

        editable_data = strip_header(parsed, header)
        yaml_content = yaml.dump(editable_data, sort_keys=False, allow_unicode=True)

        return jsonify({
            "status": "success",
            "yaml": yaml_content,
            "header": header,
            "style": extracted_style,
            "custom_sections": custom_sections
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/complete_onboarding', methods=['POST'])
@login_required
def complete_onboarding():
    """Save finalized resume data and mark onboarding complete."""
    user_id = get_current_user_id()
    if is_onboarding_complete(user_id):
        return jsonify({"status": "error", "message": "Onboarding already completed."}), 400

    data = request.json
    resume_yaml = data.get('resume', '')
    header = data.get('header', {})
    style = data.get('style', {})
    custom_sections = data.get('custom_sections', [])

    try:
        user_dir = get_user_dir(user_id)
        current_settings = get_user_settings(user_id)

        merged_header = current_settings['header'].copy()
        if header.get('name'):
            merged_header['name'] = header['name']
        if header.get('contact'):
            for k, v in header['contact'].items():
                if v:
                    merged_header.setdefault('contact', {})[k] = v

        update_user_settings(user_id, header=merged_header, style=style,
                             custom_sections=custom_sections)

        if resume_yaml.strip():
            full_data = merge_header(resume_yaml, merged_header)
            full_yaml = yaml.dump(full_data, sort_keys=False, allow_unicode=True)
            with open(os.path.join(user_dir, 'resume.yaml'), 'w') as f:
                f.write(full_yaml)

        # Clean up onboarding PDF
        onboarding_pdf = os.path.join(user_dir, 'onboarding_upload.pdf')
        if os.path.exists(onboarding_pdf):
            os.remove(onboarding_pdf)

        mark_onboarding_complete(user_id)
        return jsonify({"status": "success", "message": "Setup complete!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/skip_onboarding', methods=['POST'])
@login_required
def skip_onboarding():
    """Skip onboarding and go straight to the editor."""
    user_id = get_current_user_id()
    if is_onboarding_complete(user_id):
        return jsonify({"status": "error", "message": "Onboarding already completed."}), 400

    mark_onboarding_complete(user_id)
    return jsonify({"status": "success", "message": "Onboarding skipped."})


# --- Main editor ---

@app.route('/')
@login_required
def index():
    user_id = get_current_user_id()
    if not is_onboarding_complete(user_id):
        return redirect(url_for('onboarding'))
    user_dir = get_user_dir(user_id)
    header = get_current_user_header()
    section_names = get_current_section_names()
    custom_sections = get_current_custom_sections()
    user = get_user_by_id(user_id)

    resume_path = os.path.join(user_dir, 'resume.yaml')
    has_resume = os.path.exists(resume_path)

    if has_resume:
        resume_data = load_yaml(resume_path)
        editable_data = strip_header(resume_data, header)
    else:
        editable_data = {}

    # Load saved style from user settings, with defaults
    settings = get_user_settings(user_id)
    saved_style = settings.get('style', {})
    style = {
        'font_family': saved_style.get('font_family', '"Times New Roman", Times, serif'),
        'font_size': saved_style.get('font_size', '9.5pt'),
        'line_height': saved_style.get('line_height', '1.2'),
        'margin': saved_style.get('margin', '0.3in'),
        'accent_color': saved_style.get('accent_color', '#000000')
    }

    return render_template('editor.html',
                           resume=yaml.dump(editable_data, sort_keys=False, allow_unicode=True) if editable_data else '',
                           style=style,
                           fixed_header=header,
                           section_names=section_names,
                           custom_sections=custom_sections,
                           user=user,
                           has_resume=has_resume)


# --- API routes ---

@app.route('/api/preview', methods=['POST'])
@login_required
def preview():
    data = request.json
    resume_yaml = data.get('resume', '')
    style = data.get('style', {})

    try:
        header = get_current_user_header()
        section_names = get_current_section_names()
        custom_sections = get_current_custom_sections()
        resume_data = merge_header(resume_yaml, header)

        env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
        template = env.get_template('resume.html')
        html_content = template.render(resume=resume_data, style=style,
                                       section_names=section_names,
                                       custom_sections=custom_sections)

        return html_content
    except Exception as e:
        return str(e), 400


@app.route('/api/save', methods=['POST'])
@login_required
def save():
    data = request.json
    resume_yaml = data.get('resume', '')
    keyword = data.get('keyword', 'default')

    try:
        user_id = get_current_user_id()
        user_dir = get_user_dir(user_id)
        versions_dir = get_user_versions_dir(user_id)
        header = get_current_user_header()
        user = get_user_by_id(user_id)

        full_data = merge_header(resume_yaml, header)
        full_yaml = yaml.dump(full_data, sort_keys=False, allow_unicode=True)

        # Save main file
        with open(os.path.join(user_dir, 'resume.yaml'), 'w') as f:
            f.write(full_yaml)

        # Save version
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = "".join([c for c in keyword if c.isalnum() or c in ('-', '_')]).strip()
        if not safe_keyword:
            safe_keyword = "default"

        # Use user's name in filename
        safe_name = "".join([c for c in user['name'] if c.isalnum() or c in (' ', '-', '_')]).strip().replace(' ', '_')
        version_filename = f"{safe_name}_{safe_keyword}_{timestamp}.yaml"

        os.makedirs(versions_dir, exist_ok=True)
        with open(os.path.join(versions_dir, version_filename), 'w') as f:
            f.write(full_yaml)

        return jsonify({"status": "success", "message": f"Saved as {version_filename}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/history')
@login_required
def history():
    user_id = get_current_user_id()
    versions_dir = get_user_versions_dir(user_id)

    if not os.path.exists(versions_dir):
        return jsonify([])

    files = glob.glob(os.path.join(versions_dir, '*.yaml'))
    files.sort(key=os.path.getmtime, reverse=True)
    history_list = []
    for f in files:
        filename = os.path.basename(f)
        timestamp = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
        history_list.append({"filename": filename, "timestamp": timestamp})
    return jsonify(history_list)


@app.route('/api/load/<filename>')
@login_required
def load_version(filename):
    try:
        user_id = get_current_user_id()
        versions_dir = get_user_versions_dir(user_id)
        header = get_current_user_header()

        # Sanitize filename
        filename = os.path.basename(filename)
        filepath = os.path.join(versions_dir, filename)
        if not os.path.exists(filepath):
            return jsonify({"status": "error", "message": "File not found"}), 404

        with open(filepath, 'r') as f:
            content = f.read()

        editable_data = strip_header(content, header)
        editable_yaml = yaml.dump(editable_data, sort_keys=False, allow_unicode=True)

        return jsonify({"status": "success", "content": editable_yaml})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/delete_version/<filename>', methods=['DELETE'])
@login_required
def delete_version(filename):
    try:
        user_id = get_current_user_id()
        versions_dir = get_user_versions_dir(user_id)

        filename = os.path.basename(filename)
        filepath = os.path.join(versions_dir, filename)

        if os.path.exists(filepath):
            os.remove(filepath)
            return jsonify({"status": "success", "message": f"Deleted {filename}"})
        else:
            return jsonify({"status": "error", "message": "File not found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- Settings ---

@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def settings():
    user_id = get_current_user_id()

    if request.method == 'GET':
        s = get_user_settings(user_id)
        return jsonify({"status": "success", "settings": s})

    data = request.json
    header = data.get('header')
    section_names = data.get('section_names')
    custom_sections = data.get('custom_sections')

    update_user_settings(user_id, header=header, section_names=section_names,
                         custom_sections=custom_sections)
    return jsonify({"status": "success", "message": "Settings saved."})


# --- Delete Profile ---

@app.route('/api/delete_profile', methods=['POST'])
@login_required
def delete_profile():
    data = request.json
    password = data.get('password', '')
    user_id = get_current_user_id()

    if not password:
        return jsonify({"status": "error", "message": "Password is required."}), 400

    if not verify_user_password(user_id, password):
        return jsonify({"status": "error", "message": "Incorrect password."}), 403

    try:
        delete_user(user_id)
        session.clear()
        return jsonify({"status": "success", "message": "Account deleted."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- AI features ---

@app.route('/api/match_jd', methods=['POST'])
@login_required
def match_jd():
    data = request.json
    jd_text = data.get('jd_text', '')
    api_key = data.get('api_key', '')
    provider = data.get('provider', 'anthropic')
    model = data.get('model', '')

    if not jd_text or not api_key:
        return jsonify({"status": "error", "message": "Missing JD text or API Key"}), 400

    try:
        user_id = get_current_user_id()
        versions_dir = get_user_versions_dir(user_id)

        files = glob.glob(os.path.join(versions_dir, '*.yaml'))
        if not files:
            return jsonify({"status": "error", "message": "No saved resumes to compare"}), 404

        resumes_content = []
        file_map = {}
        for f in files:
            fname = os.path.basename(f)
            try:
                with open(f, 'r') as file:
                    content = file.read()
                    resumes_content.append(f"--- RESUME: {fname} ---\n{content}\n")
                    file_map[fname] = content
            except Exception:
                continue

        resumes_content = resumes_content[-10:]
        combined_resumes = "\n".join(resumes_content)

        ranking_system_prompt = """
        You are an expert ATS (Applicant Tracking System) ranker.
        Rank the provided resumes based on their relevance to the Job Description.
        Return ONLY a JSON list of objects. Each object must have:
        - "filename": string
        - "score": number (0-100)
        """

        ranking_user_message = f"JOB DESCRIPTION:\n{jd_text}\n\nRESUMES:\n{combined_resumes}"
        ranking_response = call_ai_provider(provider, api_key, ranking_system_prompt, ranking_user_message, model)
        results = parse_json_response(ranking_response)

        if isinstance(results, list):
            results.sort(key=lambda x: x.get('score', 0), reverse=True)
        else:
            results = []

        top_3 = results[:3]
        if top_3:
            top_3_filenames = [r['filename'] for r in top_3]
            top_3_content = []
            for fname in top_3_filenames:
                if fname in file_map:
                    top_3_content.append(f"--- RESUME: {fname} ---\n{file_map[fname]}\n")

            if top_3_content:
                detail_system_prompt = """
                You are an expert resume coach.
                For each provided resume, analyze it against the Job Description.
                Return a JSON list of objects. Each object must have:
                - "filename": string
                - "reasoning": string (brief explanation of the match score)
                - "improvements": list of strings (3-4 specific actionable bullet points)
                """
                detail_user_msg = f"JOB DESCRIPTION:\n{jd_text}\n\nRESUMES:\n{''.join(top_3_content)}"
                detail_response = call_ai_provider(provider, api_key, detail_system_prompt, detail_user_msg, model)
                details = parse_json_response(detail_response)

                detail_map = {d.get('filename'): d for d in details if isinstance(d, dict)}
                for r in results:
                    if r['filename'] in detail_map:
                        r.update(detail_map[r['filename']])
                    else:
                        r['reasoning'] = None
                        r['improvements'] = []

        return jsonify({"status": "success", "results": results})

    except Exception as e:
        print(f"Error in match_jd: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/check_grammar', methods=['POST'])
@login_required
def check_grammar():
    data = request.json
    resume_yaml = data.get('resume', '')
    api_key = data.get('api_key', '')
    provider = data.get('provider', 'anthropic')
    model = data.get('model', '')

    if not resume_yaml:
        return jsonify({"status": "error", "message": "Missing resume content"}), 400

    # Offline Mode
    if provider == 'local':
        try:
            def get_text_values(data):
                values = []
                if isinstance(data, dict):
                    for k, v in data.items():
                        values.extend(get_text_values(v))
                elif isinstance(data, list):
                    for item in data:
                        values.extend(get_text_values(item))
                elif isinstance(data, str):
                    values.append(data)
                return values

            try:
                parsed_yaml = yaml.safe_load(resume_yaml)
                text_segments = get_text_values(parsed_yaml)
                clean_text = "\n\n".join(text_segments)
            except Exception:
                clean_text = resume_yaml

            import language_tool_python

            try:
                tool = language_tool_python.LanguageTool('en-US')
                matches = tool.check(clean_text)

                results = []
                for match in matches:
                    context = match.context
                    if "{{" in context or "{%" in context:
                        continue
                    results.append({
                        "original": clean_text[match.offset: match.offset + match.errorLength],
                        "correction": match.replacements[0] if match.replacements else "",
                        "explanation": match.message,
                        "location": "Content Match"
                    })
                return jsonify({"status": "success", "results": results})

            except Exception as local_err:
                print(f"Local Java server failed: {local_err}. Falling back to public API.")
                import requests
                response = requests.post(
                    'https://api.languagetool.org/v2/check',
                    data={'text': clean_text, 'language': 'en-US'}
                )
                if response.status_code != 200:
                    raise Exception(f"Public API error: {response.text}")

                api_data = response.json()
                matches = api_data.get('matches', [])
                results = []
                for match in matches:
                    offset = match['offset']
                    length = match['length']
                    context_obj = match.get('context', {})
                    context_text = context_obj.get('text', '')
                    if "{{" in context_text or "{%" in context_text:
                        continue
                    replacements = match.get('replacements', [])
                    correction = replacements[0]['value'] if replacements else ""
                    results.append({
                        "original": clean_text[offset: offset + length],
                        "correction": correction,
                        "explanation": match['message'],
                        "location": "Content Match"
                    })
                return jsonify({"status": "success", "results": results})

        except ImportError:
            return jsonify({"status": "error", "message": "language-tool-python or requests not installed"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": f"Grammar check failed: {str(e)}"}), 500

    # AI Mode
    if not api_key:
        return jsonify({"status": "error", "message": "Missing API Key"}), 400

    try:
        system_prompt = """
        You are a professional resume editor. Proofread the following resume YAML content for spelling and grammar errors.
        Focus on the content values, ignoring keys and structure.
        Return a JSON list of objects. Each object must have:
        - "original": string
        - "correction": string
        - "explanation": string
        - "location": string
        If no errors are found, return an empty list.
        """
        user_msg = f"RESUME CONTENT:\n{resume_yaml}"
        response_text = call_ai_provider(provider, api_key, system_prompt, user_msg, model)
        results = parse_json_response(response_text)
        return jsonify({"status": "success", "results": results})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/analyze_resume', methods=['POST'])
@login_required
def analyze_resume():
    data = request.json
    resume_yaml = data.get('resume', '')
    jd_text = data.get('jd_text', '')
    api_key = data.get('api_key', '')
    provider = data.get('provider', 'anthropic')
    model = data.get('model', '')

    if not resume_yaml or not jd_text or not api_key:
        return jsonify({"status": "error", "message": "Missing Resume, JD, or API Key"}), 400

    try:
        system_prompt = """
        You are an expert ATS scanner and Resume Coach.
        Analyze the provided resume against the Job Description.
        Return a JSON object with:
        - "score": number (0-100)
        - "missing_keywords": list of strings
        - "suggestions": list of strings (3-4 specific improvements)
        """
        user_msg = f"JOB DESCRIPTION:\n{jd_text}\n\nRESUME CONTENT:\n{resume_yaml}"
        response_text = call_ai_provider(provider, api_key, system_prompt, user_msg, model)
        result = parse_json_response(response_text)

        if isinstance(result, list) and result:
            result = result[0]
        elif not isinstance(result, dict):
            result = {"score": 0, "missing_keywords": [], "suggestions": ["Failed to parse AI response"]}

        return jsonify({"status": "success", "result": result})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/analyze_selection', methods=['POST'])
@login_required
def analyze_selection():
    data = request.json
    jd_text = data.get('jd_text', '')
    api_key = data.get('api_key', '')
    provider = data.get('provider', 'anthropic')
    filenames = data.get('filenames', [])
    model = data.get('model', '')

    if not filenames or not jd_text:
        return jsonify({"status": "error", "message": "Missing filenames or JD"}), 400

    try:
        user_id = get_current_user_id()
        versions_dir = get_user_versions_dir(user_id)

        resumes_content = []
        for fname in filenames:
            fname = os.path.basename(fname)
            fpath = os.path.join(versions_dir, fname)
            if os.path.exists(fpath):
                with open(fpath, 'r') as file:
                    resumes_content.append(f"--- RESUME: {fname} ---\n{file.read()}\n")

        if not resumes_content:
            return jsonify({"status": "error", "message": "Files not found"}), 404

        system_prompt = """
        You are an expert resume coach.
        For each provided resume, analyze it against the Job Description.
        Return a JSON list of objects. Each object must have:
        - "filename": string
        - "reasoning": string
        - "improvements": list of strings (3-4 bullet points)
        """
        user_msg = f"JOB DESCRIPTION:\n{jd_text}\n\nRESUMES:\n{''.join(resumes_content)}"
        response_text = call_ai_provider(provider, api_key, system_prompt, user_msg, model)
        results = parse_json_response(response_text)

        return jsonify({"status": "success", "results": results})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/download_pdf', methods=['POST'])
@login_required
def download_pdf():
    data = request.json
    resume_yaml = data.get('resume', '')
    style = data.get('style', {})
    keyword = data.get('keyword', '')
    inline = data.get('inline', False)

    try:
        user_id = get_current_user_id()
        user_dir = get_user_dir(user_id)
        header = get_current_user_header()
        section_names = get_current_section_names()
        custom_sections = get_current_custom_sections()
        user = get_user_by_id(user_id)

        resume_data = merge_header(resume_yaml, header)

        env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
        template = env.get_template('resume.html')
        html_content = template.render(resume=resume_data, style=style,
                                       section_names=section_names,
                                       custom_sections=custom_sections)

        pdf_path = os.path.join(user_dir, 'preview.pdf')
        HTML(string=html_content, base_url=BASE_DIR).write_pdf(pdf_path)

        safe_name = "".join([c for c in user['name'] if c.isalnum() or c in (' ', '-', '_')]).strip().replace(' ', '_')
        if keyword:
            safe_keyword = "".join([c for c in keyword if c.isalnum() or c in ('-', '_')]).strip()
            download_name = f"{safe_name}_{safe_keyword}.pdf"
        else:
            download_name = f"{safe_name}_Resume.pdf"

        return send_file(pdf_path, as_attachment=not inline, download_name=download_name)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- AI provider helpers ---

def call_ai_provider(provider, api_key, system_prompt, user_message, model=None):
    if provider == 'anthropic':
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        model_name = model if model else "claude-3-haiku-20240307"
        message = client.messages.create(
            model=model_name,
            max_tokens=4000,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )
        return message.content[0].text

    elif provider == 'openai':
        import openai
        client = openai.OpenAI(api_key=api_key)
        model_name = model if model else "gpt-3.5-turbo"
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        return completion.choices[0].message.content

    elif provider == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model_name = model if model else 'gemini-1.5-flash'
        model_instance = genai.GenerativeModel(model_name)
        full_prompt = system_prompt + "\n\n" + user_message
        response = model_instance.generate_content(full_prompt)
        return response.text

    raise ValueError(f"Unknown provider: {provider}")


def parse_json_response(response_text):
    import json
    import re
    response_text = response_text.replace("```json", "").replace("```", "")

    try:
        return json.loads(response_text)
    except Exception:
        pass

    json_match_list = re.search(r'\[.*\]', response_text, re.DOTALL)
    if json_match_list:
        try:
            return json.loads(json_match_list.group(0))
        except Exception:
            pass

    json_match_obj = re.search(r'\{.*\}', response_text, re.DOTALL)
    if json_match_obj:
        try:
            return json.loads(json_match_obj.group(0))
        except Exception:
            pass

    return {}


if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0')
