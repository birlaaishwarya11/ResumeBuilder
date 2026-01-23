import json
import os

import yaml
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename

from ai_ats_checker import AIATSAnalyzer
from ats_analyzer import analyze_keywords
from daytona_orchestrator import DaytonaOrchestrator
from flask_session import Session
from knowledge_base import KnowledgeBase
from mcp_servers import ats_server, doc_server
from rag_agent import RagAgent
from resume_extractor import extract_resume_content

# from generate_resume import generate_pdf # Removed local generation
from resume_parser import ensure_resume_schema, parse_text, to_text
from user_manager import UserManager

app = Flask(__name__)
# Initialize Daytona Orchestrator
orchestrator = DaytonaOrchestrator()
# Initialize RAG Agent (lazy init or global)
rag_agent = RagAgent()
kb = KnowledgeBase()

app.config["SECRET_KEY"] = "super-secret-key-change-in-production"


@app.route("/api/health")
def health():
    status = {
        "status": "unknown",
        "services": {"daytona": "disconnected", "knowledge_base": "unknown", "mcp_core": "unknown"},
        "details": [],
    }

    # 1. Check Knowledge Base
    try:
        if kb.health_check():
            status["services"]["knowledge_base"] = "healthy"
        else:
            status["services"]["knowledge_base"] = "unhealthy"
            status["details"].append("Knowledge Base (ChromaDB) unreachable")
    except Exception as e:
        status["services"]["knowledge_base"] = "error"
        status["details"].append(f"Knowledge Base error: {str(e)}")

    # 2. Check Daytona
    if orchestrator.daytona:
        status["services"]["daytona"] = "connected"
    else:
        status["details"].append("Daytona SDK not connected (using local fallback)")

    # 3. Check MCP Core (Local Modules)
    try:
        # Simple sanity check if modules are loaded
        if doc_server and ats_server:
            status["services"]["mcp_core"] = "operational"
        else:
            status["services"]["mcp_core"] = "failed"
    except Exception as e:
        status["services"]["mcp_core"] = "error"
        status["details"].append(f"MCP Core error: {str(e)}")

    # Determine Overall Status
    services = status["services"]
    if services["knowledge_base"] == "healthy" and services["daytona"] == "connected":
        status["status"] = "healthy"
    elif services["knowledge_base"] == "healthy":  # Daytona is optional
        status["status"] = "degraded (local-only)"
    else:
        status["status"] = "unhealthy"

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
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


@app.route("/")
def index():
    return render_template("landing.html", user=get_current_user())


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if user_manager.verify_user(username, password):
            session["user"] = username
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if user_manager.create_user(username, password):
            session["user"] = username
            return redirect(url_for("dashboard"))
        return render_template("signup.html", error="Username already exists")
    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/stash_jd", methods=["POST"])
@login_required
def stash_jd():
    data = request.json
    session["stashed_jd"] = data.get("text")
    return jsonify({"status": "success"})


@app.route("/api/convert", methods=["POST"])
@login_required
def convert_format():
    text = request.json.get("text")
    from_fmt = request.json.get("from")
    to_fmt = request.json.get("to")

    try:
        data = {}
        # Parse input
        if from_fmt == "yaml":
            data = yaml.safe_load(text)
        else:
            data = parse_text(text)

        # Convert to output
        if to_fmt == "yaml":
            result = yaml.dump(data, sort_keys=False)
        else:
            result = to_text(data)

        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/save_jd", methods=["POST"])
@login_required
def save_jd():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    text = request.json.get("text")

    try:
        jd_path = os.path.join(user_dir, "job_description.txt")
        with open(jd_path, "w") as f:
            f.write(text)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/upload_resume", methods=["POST"])
@login_required
def upload_resume():
    # Daytona check removed to allow local fallback
    # if not orchestrator.daytona:
    #      return jsonify({"error": "Daytona SDK not connected. Check server logs."}), 503

    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if file and (file.filename.endswith(".pdf") or file.filename.endswith(".docx")):
        user = get_current_user()
        user_dir = user_manager.get_user_dir(user)
        filename = secure_filename(file.filename)
        save_path = os.path.join(user_dir, filename)

        # Save locally first (needed for local fallback)
        file.save(save_path)

        try:
            extracted_text = ""

            # Try Daytona if connected
            if orchestrator.daytona:
                try:
                    with open(save_path, "rb") as f:
                        file_content = f.read()
                    extracted_text = orchestrator.parse_resume(filename, file_content)
                except Exception as e:
                    print(f"Daytona parsing failed, falling back to local: {e}")
                    # Fallback to local
                    extracted_text = extract_resume_content(save_path)
            else:
                # Local Extraction
                extracted_text = extract_resume_content(save_path)

            if not extracted_text or extracted_text.startswith("# Error"):
                raise Exception(extracted_text or "Extraction returned empty")

            # Update resume.yaml
            parsed_data = parse_text(extracted_text)

            # Add to Knowledge Base (Graph Extraction)
            try:
                kb.add_text(extracted_text, source=f"Resume: {filename}")
            except Exception as e:
                # Log warning but don't fail the upload
                print(f"Warning: Failed to add to Knowledge Base (Ollama might be down): {e}")

            resume_path = os.path.join(user_dir, "resume.yaml")
            with open(resume_path, "w") as f:
                yaml.dump(parsed_data, f, sort_keys=False)
            return jsonify({"status": "success", "text": extracted_text})
        except Exception as e:
            return jsonify({"error": f"Failed to parse resume: {str(e)}"}), 500

    return jsonify({"error": "Invalid file type"}), 400


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)

    # Load resume data
    resume_path = os.path.join(user_dir, "resume.yaml")
    resume_text = ""
    if os.path.exists(resume_path):
        try:
            with open(resume_path, "r") as f:
                data = yaml.safe_load(f) or {}
                resume_text = to_text(data)
        except Exception:
            resume_text = "# Error loading resume"

    # List generated PDFs
    pdfs = [f for f in os.listdir(user_dir) if f.endswith(".pdf")]
    pdfs.sort(reverse=True)  # Show newest first

    # Check for stashed JD or saved JD file
    stashed_jd = session.pop("stashed_jd", "")
    if not stashed_jd:
        jd_path = os.path.join(user_dir, "job_description.txt")
        if os.path.exists(jd_path):
            with open(jd_path, "r") as f:
                stashed_jd = f.read()

    # Load style
    style_path = os.path.join(user_dir, "style.json")
    saved_style = {}
    if os.path.exists(style_path):
        try:
            with open(style_path, "r") as f:
                saved_style = json.load(f)
        except:
            pass

    return render_template(
        "dashboard.html", user=user, resume_text=resume_text, pdfs=pdfs, stashed_jd=stashed_jd, saved_style=saved_style
    )


@app.route("/api/preview_html", methods=["POST"])
@login_required
def preview_html():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    text_content = request.json.get("text")
    style = request.json.get("style", {})  # Get style options

    try:
        if text_content:
            data = parse_text(text_content)
        else:
            # Fallback to saved file
            resume_path = os.path.join(user_dir, "resume.yaml")
            with open(resume_path, "r") as f:
                data = yaml.safe_load(f)
            data = ensure_resume_schema(data)

        # Render template
        from flask import render_template_string

        # Read template file
        template_path = os.path.join("templates", "resume.html")
        with open(template_path, "r") as f:
            template_content = f.read()

        # Render with style
        html = render_template_string(template_content, resume=data, style=style)
        return jsonify({"status": "success", "html": html})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/update_resume", methods=["POST"])
@login_required
def update_resume():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    text_content = request.json.get("text")
    style = request.json.get("style", {})

    try:
        # Check if text_content is already valid YAML
        # If the user edited the "raw text" which is usually markdown-like,
        # but pasted YAML, we should detect it.
        try:
            # Try parsing as YAML directly first
            data = yaml.safe_load(text_content)
            # Basic validation: must be a dict
            if not isinstance(data, dict):
                raise ValueError("Not a dictionary")
        except Exception:
            # Fallback to custom parser
            data = parse_text(text_content)

        # Save as YAML
        with open(os.path.join(user_dir, "resume.yaml"), "w") as f:
            yaml.dump(data, f, sort_keys=False)

        # Save Style
        with open(os.path.join(user_dir, "style.json"), "w") as f:
            json.dump(style, f)

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/analyze_ats", methods=["POST"])
@login_required
def analyze_ats():
    user = get_current_user()

    # Get inputs
    resume_text = request.json.get("resume_text")
    job_desc = request.json.get("job_desc")
    enable_rag = request.json.get("enable_rag", False)

    if not resume_text or not job_desc:
        return jsonify({"status": "error", "message": "Missing resume text or job description"}), 400

    result = {}
    source = "local"

    # 1. Try Daytona (Preferred for isolation/scaling)
    if orchestrator.daytona:
        try:
            result = orchestrator.analyze_ats(resume_text, job_desc)
            source = "daytona"
        except Exception as e:
            print(f"Daytona ATS failed, falling back to local: {e}")
            # Fallback to local
            try:
                result = analyze_keywords(resume_text, job_desc)
                source = "local (fallback)"
            except Exception as inner_e:
                return jsonify({"status": "error", "message": f"Analysis failed: {str(inner_e)}"}), 500
    else:
        # 2. Local Fallback
        try:
            # Run locally using imported function
            result = analyze_keywords(resume_text, job_desc)
            source = "local"
        except Exception as e:
            return jsonify({"status": "error", "message": f"Analysis failed: {str(e)}"}), 500

    # 3. RAG Enhancement (if enabled and missing keywords exist)
    rag_suggestions = []
    rag_status = "skipped"
    if enable_rag and result.get("missing_keywords"):
        try:
            # Check for top 3 missing keywords in the knowledge base
            missing = result.get("missing_keywords", [])[:3]
            if missing:
                query = f"Experience with {', '.join(missing)}"

                # Check health before call
                if not kb.health_check():
                    raise Exception("Knowledge Base unavailable")

                suggestions_json = rag_agent.find_lost_bullets(query)

                # Parse if it's a string JSON
                if isinstance(suggestions_json, str):
                    try:
                        rag_suggestions = json.loads(suggestions_json)
                    except:
                        rag_suggestions = [suggestions_json]
                elif isinstance(suggestions_json, list):
                    rag_suggestions = suggestions_json

                # Annotate suggestions
                rag_suggestions = [{"content": s, "source": "RAG (Knowledge Base)"} for s in rag_suggestions]
                rag_status = "success"
        except Exception as e:
            print(f"RAG Analysis failed: {e}")
            rag_suggestions = [
                {"content": "Could not retrieve RAG suggestions at this time.", "source": "System Error"}
            ]
            rag_status = f"error: {str(e)}"

    # Merge RAG suggestions into result
    if rag_suggestions:
        result["rag_suggestions"] = rag_suggestions
        result["rag_status"] = rag_status

    return jsonify({"status": "success", "analysis": result, "source": source})


@app.route("/api/generate", methods=["POST"])
@login_required
def generate():
    if not orchestrator.daytona:
        return jsonify({"status": "error", "message": "Daytona SDK not connected."}), 503

    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    keywords = request.json.get("keywords", "")

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
    snapshot_path = os.path.join(user_dir, filename.replace(".pdf", ".json"))

    try:
        with open(resume_path, "r") as f:
            data = yaml.safe_load(f)

        style = {}
        if os.path.exists(style_path):
            with open(style_path, "r") as f:
                style = json.load(f)

        # Generate via Worker Sandbox
        if not orchestrator.daytona:
            # Fallback message if somehow we got here
            raise Exception("Daytona Worker not available for PDF generation")

        try:
            pdf_content = orchestrator.generate_pdf(data, style_data=style)
        except Exception as worker_err:
            print(f"Worker generation failed: {worker_err}")
            raise Exception(f"PDF Generation Worker failed: {str(worker_err)}. Please try again or check styles.")

        # Save the returned PDF content
        with open(output_path, "wb") as f:
            f.write(pdf_content)

        # Save Snapshot (Data + Style)
        snapshot_data = {"data": data, "style": style}
        with open(snapshot_path, "w") as f:
            json.dump(snapshot_data, f)

        return jsonify({"status": "success", "filename": filename})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/delete_pdf", methods=["POST"])
@login_required
def delete_pdf():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    filename = request.json.get("filename")

    if not filename or "/" in filename:
        return jsonify({"status": "error", "message": "Invalid filename"}), 400

    try:
        pdf_path = os.path.join(user_dir, filename)
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

        # Remove snapshot if exists
        json_path = pdf_path.replace(".pdf", ".json")
        if os.path.exists(json_path):
            os.remove(json_path)

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/restore_version", methods=["POST"])
@login_required
def restore_version():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    filename = request.json.get("filename")

    if not filename or "/" in filename:
        return jsonify({"status": "error", "message": "Invalid filename"}), 400

    json_filename = filename.replace(".pdf", ".json")
    json_path = os.path.join(user_dir, json_filename)

    try:
        if not os.path.exists(json_path):
            return jsonify({"status": "error", "message": "No source data found for this version"}), 404

        with open(json_path, "r") as f:
            snapshot = json.load(f)

        data = snapshot.get("data")
        style = snapshot.get("style", {})

        # Convert data to text
        text_content = to_text(data)

        # Overwrite current resume.yaml and style.json
        with open(os.path.join(user_dir, "resume.yaml"), "w") as f:
            yaml.dump(data, f, sort_keys=False)

        with open(os.path.join(user_dir, "style.json"), "w") as f:
            json.dump(style, f)

        return jsonify({"status": "success", "text": text_content, "style": style})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
@login_required
def analyze():
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)

    jd_text = request.json.get("jd_text")
    model = request.json.get("model", "ollama/llama3")  # Default
    api_key = request.json.get("api_key")  # Optional API Key
    resume_source = request.json.get("resume_source", "current")

    # Save JD temporarily
    jd_path = os.path.join(user_dir, "job_description.txt")
    with open(jd_path, "w") as f:
        f.write(jd_text)

    resume_path = os.path.join(user_dir, "resume.yaml")
    resume_data = None

    if resume_source != "current" and resume_source.endswith(".pdf"):
        # Load from snapshot
        json_filename = resume_source.replace(".pdf", ".json")
        json_path = os.path.join(user_dir, json_filename)
        if os.path.exists(json_path):
            try:
                with open(json_path, "r") as f:
                    snapshot = json.load(f)
                    resume_data = snapshot.get("data")
            except Exception as e:
                return jsonify({"status": "error", "message": f"Failed to load snapshot: {str(e)}"}), 500

    # Pass API Key securely
    try:
        analyzer = AIATSAnalyzer(resume_path, jd_path, model=model, api_key=api_key, resume_data=resume_data)
        result = analyzer.analyze()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    if result:
        result["source"] = f"AI ({model})"

        # RAG Enhancement
        enable_rag = request.json.get("enable_rag", False)
        if enable_rag and result.get("missing_keywords"):
            try:
                # Check for top 3 missing keywords
                missing = result.get("missing_keywords", [])[:3]
                if missing:
                    query = f"My experience with {', '.join(missing)}"
                    suggestions_json = rag_agent.find_lost_bullets(query)

                    rag_suggestions = []
                    if isinstance(suggestions_json, str):
                        try:
                            rag_suggestions = json.loads(suggestions_json)
                        except:
                            rag_suggestions = [suggestions_json]
                    elif isinstance(suggestions_json, list):
                        rag_suggestions = suggestions_json

                    if rag_suggestions:
                        result["rag_suggestions"] = rag_suggestions
            except Exception as e:
                print(f"RAG Analysis failed: {e}")
                # Don't fail the whole request

    return jsonify(result)


# --- New RAG & MCP Endpoints ---


@app.route("/api/kb/upload", methods=["POST"])
@login_required
def kb_upload():
    """Upload documents to Knowledge Base."""
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    files = request.files.getlist("file")

    try:
        saved_paths = []
        user = get_current_user()
        user_dir = user_manager.get_user_dir(user)
        kb_upload_dir = os.path.join(user_dir, "kb_uploads")
        if not os.path.exists(kb_upload_dir):
            os.makedirs(kb_upload_dir)

        for file in files:
            if file.filename:
                path = os.path.join(kb_upload_dir, secure_filename(file.filename))
                file.save(path)
                saved_paths.append(path)

        result = kb.add_documents(saved_paths)
        return jsonify({"status": "success", "message": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/kb/add_text", methods=["POST"])
@login_required
def kb_add_text():
    """Add raw text or web link to Knowledge Base."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        text = data.get("text")
        source = data.get("source", "manual_entry")

        if not text:
            return jsonify({"error": "No text provided"}), 400

        # Check if text is a URL
        import validators

        if validators.url(text.strip()):
            result = kb.add_web_page(text.strip())
        else:
            result = kb.add_text(text, source)

        return jsonify({"status": "success", "message": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/kb/add_link", methods=["POST"])
@login_required
def kb_add_link():
    """Explicitly add a link to scrape."""
    try:
        data = request.json
        url = data.get("url")
        if not url:
            return jsonify({"error": "No URL provided"}), 400

        result = kb.add_web_page(url)
        return jsonify({"status": "success", "message": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/rag/find_bullets", methods=["POST"])
@login_required
def rag_find_bullets():
    """Find missing bullet points using RAG."""
    data = request.json
    query = data.get("query")
    model = data.get("model")
    api_key = data.get("api_key")

    if not query:
        return jsonify({"error": "Query required"}), 400

    try:
        suggestions_json = rag_agent.find_lost_bullets(query, model=model, api_key=api_key)
        # Parse JSON string if necessary, assuming agent returns string
        try:
            suggestions = json.loads(suggestions_json)
        except:
            # Fallback if LLM didn't return valid JSON
            suggestions = [suggestions_json]

        return jsonify({"status": "success", "suggestions": suggestions})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/rag/interview_prep", methods=["POST"])
@login_required
def rag_interview_prep():
    """Get interview answers/coaching."""
    data = request.json
    question = data.get("question")
    model = data.get("model")
    api_key = data.get("api_key")

    if not question:
        return jsonify({"error": "Question required"}), 400

    try:
        answer = rag_agent.prep_for_interview(question, model=model, api_key=api_key)
        return jsonify({"status": "success", "answer": answer})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/mcp/validate_resume", methods=["POST"])
@login_required
def mcp_validate_resume():
    """Run the ATS Validator Server check."""
    # Temporarily point doc_server to current user's resume
    # In a real server this would be request-scoped or passed in
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    doc_server.resume_path = os.path.join(user_dir, "resume.yaml")

    result = ats_server.validate_resume()
    return jsonify(result)


@app.route("/download/<filename>")
@login_required
def download_file(filename):
    user = get_current_user()
    user_dir = user_manager.get_user_dir(user)
    return send_from_directory(user_dir, filename)


if __name__ == "__main__":
    # Listen on all interfaces for Daytona access
    # Port 5000 is often taken by AirPlay on macOS, so we default to 8000
    port = int(os.environ.get("PORT", 8000))
    app.run(debug=True, host="0.0.0.0", port=port)
