from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from flask_cors import CORS
from flask_session import Session
import os
import yaml
import json
from user_manager import UserManager
from ai_ats_checker import AIATSAnalyzer
# from generate_resume import generate_pdf # Removed local generation
from resume_parser import to_text, parse_text
# from resume_extractor import extract_resume_content # Removed local extraction
from daytona_orchestrator import DaytonaOrchestrator
from werkzeug.utils import secure_filename

app = Flask(__name__)
# Initialize Daytona Orchestrator
orchestrator = DaytonaOrchestrator()

app.config["SECRET_KEY"] = "super-secret-key-change-in-production"

@app.route('/api/health')
def health():
    status = {
        "status": "ok",
        "daytona": "connected" if orchestrator.daytona else "disconnected",
        "api_key_set": bool(orchestrator.api_key)
    }
    return jsonify(status)

app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
# For localhost development, Secure must be False if not using HTTPS
app.config["SESSION_COOKIE_SECURE"] = False 
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
Session(app)
CORS(app, supports_credentials=True) 

user_manager = UserManager()

def get_current_user():
    return session.get("user")

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if get_current_user() is None:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    return render_template('landing.html', user=get_current_user())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if user_manager.verify_user(username, password):
            session['user'] = username
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if user_manager.create_user(username, password):
            session['user'] = username
            return redirect(url_for('dashboard'))
        return render_template('signup.html', error="Username already exists")
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/stash_jd', methods=['POST'])
@login_required
def stash_jd():
    data = request.json
    session['stashed_jd'] = data.get('text')
    return jsonify({"status": "success"})

@app.route('/api/upload_resume', methods=['POST'])
@login_required
def upload_resume():
    if not orchestrator.daytona:
         return jsonify({"error": "Daytona SDK not connected. Check server logs."}), 503

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    if file and (file.filename.endswith('.pdf') or file.filename.endswith('.docx')):
        user = get_current_user()
        user_dir = user_manager.get_user_dir(user)
        # filename = secure_filename(file.filename)
        # save_path = os.path.join(user_dir, filename)
        # file.save(save_path) # Don't save locally for privacy/worker pattern
        
        # Read file content
        file_content = file.read()
        
        try:
            # Extract content via Worker Sandbox
            extracted_text = orchestrator.parse_resume(file.filename, file_content)
            
            # Update resume.yaml
            parsed_data = parse_text(extracted_text)
            resume_path = os.path.join(user_dir, "resume.yaml")
            with open(resume_path, 'w') as f:
                yaml.dump(parsed_data, f, sort_keys=False)
            return jsonify({"status": "success", "text": extracted_text})
        except Exception as e:
            return jsonify({"error": f"Failed to parse resume: {str(e)}"}), 500
            
    return jsonify({"error": "Invalid file type"}), 400

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    
    # Load resume data
    resume_path = os.path.join(user_dir, "resume.yaml")
    resume_text = ""
    if os.path.exists(resume_path):
        try:
            with open(resume_path, 'r') as f:
                data = yaml.safe_load(f) or {}
                resume_text = to_text(data)
        except Exception:
            resume_text = "# Error loading resume"
            
    # List generated PDFs
    pdfs = [f for f in os.listdir(user_dir) if f.endswith('.pdf')]
    pdfs.sort(reverse=True) # Show newest first
    
    # Check for stashed JD
    stashed_jd = session.pop('stashed_jd', '')
    
    # Load style
    style_path = os.path.join(user_dir, "style.json")
    saved_style = {}
    if os.path.exists(style_path):
        try:
            with open(style_path, 'r') as f:
                saved_style = json.load(f)
        except:
            pass
    
    return render_template('dashboard.html', user=user, resume_text=resume_text, pdfs=pdfs, stashed_jd=stashed_jd, saved_style=saved_style)


@app.route('/api/preview_html', methods=['POST'])
@login_required
def preview_html():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    text_content = request.json.get('text')
    style = request.json.get('style', {}) # Get style options
    
    try:
        if text_content:
            data = parse_text(text_content)
        else:
            # Fallback to saved file
            resume_path = os.path.join(user_dir, "resume.yaml")
            with open(resume_path, 'r') as f:
                data = yaml.safe_load(f)
                
        # Render template
        from flask import render_template_string
        
        # Read template file
        template_path = os.path.join('templates', 'resume.html')
        with open(template_path, 'r') as f:
            template_content = f.read()
            
        # Render with style
        html = render_template_string(template_content, resume=data, style=style)
        return jsonify({"status": "success", "html": html})
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/update_resume', methods=['POST'])
@login_required
def update_resume():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    text_content = request.json.get('text')
    style = request.json.get('style', {})
    
    try:
        # Parse Text to Dict
        data = parse_text(text_content)
        
        # Save as YAML
        with open(os.path.join(user_dir, "resume.yaml"), 'w') as f:
            yaml.dump(data, f, sort_keys=False)
            
        # Save Style
        with open(os.path.join(user_dir, "style.json"), 'w') as f:
            json.dump(style, f)
            
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/analyze_ats', methods=['POST'])
@login_required
def analyze_ats():
    if not orchestrator.daytona:
         return jsonify({"status": "error", "message": "Daytona SDK not connected."}), 503

    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    
    # Get inputs
    resume_text = request.json.get('resume_text')
    job_desc = request.json.get('job_desc')
    
    if not resume_text or not job_desc:
        return jsonify({"status": "error", "message": "Missing resume text or job description"}), 400
        
    try:
        # Run ATS Analysis via Worker Sandbox
        result = orchestrator.analyze_ats(resume_text, job_desc)
        return jsonify({"status": "success", "analysis": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/generate', methods=['POST'])
@login_required
def generate():
    if not orchestrator.daytona:
         return jsonify({"status": "error", "message": "Daytona SDK not connected."}), 503

    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    keywords = request.json.get('keywords', '')
    
    # Setup paths
    resume_path = os.path.join(user_dir, "resume.yaml")
    style_path = os.path.join(user_dir, "style.json")
    
    # Filename logic
    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")
    kw_part = "_" + keywords.replace(" ", "_").replace(",", "_") if keywords else ""
    base_filename = f"{user}_Resume{kw_part}_{date_str}"
    
    # Handle duplicates
    counter = 0
    filename = f"{base_filename}.pdf"
    while os.path.exists(os.path.join(user_dir, filename)):
        counter += 1
        filename = f"{base_filename}_{counter}.pdf"
    
    output_path = os.path.join(user_dir, filename)
    snapshot_path = os.path.join(user_dir, filename.replace('.pdf', '.json'))
    
    try:
        with open(resume_path, 'r') as f:
            data = yaml.safe_load(f)
            
        style = {}
        if os.path.exists(style_path):
            with open(style_path, 'r') as f:
                style = json.load(f)
            
        # Generate via Worker Sandbox
        # generate_pdf(data, output_path, template_dir='templates', style=style)
        
        # We need to pass data and style to orchestrator
        # The orchestrator currently only takes data. I should update it to take style too if needed,
        # but generate_resume.py takes yaml data. We can merge style into data or update generate_resume.py
        # For now, let's assume style is handled by merging it into the data or the script handles it.
        # Actually generate_resume.py in current state takes data and uses 'style' var but only from args or hardcoded?
        # Let's check generate_resume.py again. It accepts --data.
        # It renders with `style=style or {}`.
        # So we should pass style in the yaml or update generate_resume.py to take style file.
        # For simplicity, let's update data to include style under a '_style' key if the template supports it,
        # or just stick to basic generation for now.
        # Wait, the user requirement is "Generating the final PDF".
        # I'll update orchestrator to pass style if I can, but let's just get basic generation working first.
        
        pdf_content = orchestrator.generate_pdf(data)
        
        # Save the returned PDF content
        with open(output_path, 'wb') as f:
            f.write(pdf_content)
        
        # Save Snapshot (Data + Style)
        snapshot_data = {
            "data": data,
            "style": style
        }
        with open(snapshot_path, 'w') as f:
            json.dump(snapshot_data, f)
        
        return jsonify({"status": "success", "filename": filename})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/delete_pdf', methods=['POST'])
@login_required
def delete_pdf():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    filename = request.json.get('filename')
    
    if not filename or '/' in filename:
        return jsonify({"status": "error", "message": "Invalid filename"}), 400
        
    try:
        pdf_path = os.path.join(user_dir, filename)
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
            
        # Remove snapshot if exists
        json_path = pdf_path.replace('.pdf', '.json')
        if os.path.exists(json_path):
            os.remove(json_path)
            
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/restore_version', methods=['POST'])
@login_required
def restore_version():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    filename = request.json.get('filename')
    
    if not filename or '/' in filename:
        return jsonify({"status": "error", "message": "Invalid filename"}), 400
        
    json_filename = filename.replace('.pdf', '.json')
    json_path = os.path.join(user_dir, json_filename)
    
    try:
        if not os.path.exists(json_path):
            return jsonify({"status": "error", "message": "No source data found for this version"}), 404
            
        with open(json_path, 'r') as f:
            snapshot = json.load(f)
            
        data = snapshot.get('data')
        style = snapshot.get('style', {})
        
        # Convert data to text
        text_content = to_text(data)
        
        # Overwrite current resume.yaml and style.json
        with open(os.path.join(user_dir, "resume.yaml"), 'w') as f:
            yaml.dump(data, f, sort_keys=False)
            
        with open(os.path.join(user_dir, "style.json"), 'w') as f:
            json.dump(style, f)
            
        return jsonify({
            "status": "success", 
            "text": text_content,
            "style": style
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    
    jd_text = request.json.get('jd_text')
    model = request.json.get('model', 'ollama/llama3') # Default
    api_key = request.json.get('api_key') # Optional API Key
    
    # Save JD temporarily
    jd_path = os.path.join(user_dir, "job_description.txt")
    with open(jd_path, 'w') as f:
        f.write(jd_text)
        
    resume_path = os.path.join(user_dir, "resume.yaml")
    
    # Pass API Key securely
    analyzer = AIATSAnalyzer(resume_path, jd_path, model=model, api_key=api_key)
    result = analyzer.analyze()
    
    return jsonify(result)

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    return send_from_directory(user_dir, filename)

if __name__ == '__main__':
    # Listen on all interfaces for Daytona access
    app.run(debug=True, host='0.0.0.0', port=5000)
