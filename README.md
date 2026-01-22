# Resume Builder with Daytona Integration

This project is a Resume Builder application designed to run on **Daytona**.

## Architecture

- **Main Application**: A Flask web server (`app.py`) that serves the frontend and orchestrates tasks. It runs in a **persistent Daytona Sandbox**.
- **Worker Sandboxes**: Ephemeral sandboxes created on-demand using the Daytona SDK to handle resource-intensive and secure tasks:
  - **Resume Parsing**: Extracts text from PDF/DOCX uploads (`worker_extractor.py`).
  - **ATS Analysis**: Analyzes resumes against job descriptions using NLTK and optional Ollama (`ats_analyzer.py`).
  - **PDF Generation**: Generates PDFs from resume data (`generate_resume.py`).

## Privacy & Security

- No raw uploaded files are stored on the Main Server. They are streamed to Worker Sandboxes for processing.
- Worker Sandboxes are **deleted immediately** after the task is completed.
- User profile data (parsed resume YAML and generated PDFs) is stored in the persistent Main Sandbox for user access.

## Deployment

1.  **Main Application**:
    The main application is designed to be the entry point.
    ```bash
    make install
    make server
    ```
    This starts the Flask app on port 5001.

2.  **Daytona Configuration**:
    - Ensure `DAYTONA_API_KEY` is set in the environment.
    - The application uses `daytona_sdk` to manage worker sandboxes.

## Features

- **Resume Parsing**: Upload PDF/DOCX to populate the editor.
- **ATS Analysis**: Compare your resume with a Job Description using NLTK keyword matching.
- **Ollama Integration**: (Optional) Use local LLM in the worker sandbox for advanced insights.
- **PDF Generation**: Create formatted PDFs from your data.
