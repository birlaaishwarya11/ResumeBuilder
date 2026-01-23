import os
import json
import time
from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxBaseParams

# Configuration
# REPO_URL = "https://github.com/daytonaio/sample-python-flask" # Placeholder, ideally use current repo if public or accessible
# Since we are in a Daytona sandbox, we might want to clone the *current* code or use a standard image and upload scripts.
# For simplicity, we will assume we can clone this repo. If it's a private repo/local, we might need to upload files manually.
# User said "The main application... must run inside a persistent Daytona Sandbox".
# We will assume the worker sandbox can clone the same repo or we upload the necessary scripts.
# Uploading scripts is safer if the repo is private/local changes.

class DaytonaOrchestrator:
    def __init__(self):
        self.api_key = os.environ.get("DAYTONA_API_KEY")
        self.daytona = None
        # Default to the user's repo for consistency
        self.target_repo = os.environ.get("DAYTONA_TARGET_REPO", "https://github.com/birlaaishwarya11/ResumeBuilder.git")
        
        if self.api_key:
            try:
                self.daytona = Daytona()
                print("Daytona SDK Initialized successfully.")
            except Exception as e:
                print(f"Error initializing Daytona SDK: {e}")
        else:
            print("Warning: DAYTONA_API_KEY not set. Orchestrator will fail to create sandboxes.")

    def create_worker_sandbox(self):
        """Creates a fresh, ephemeral sandbox."""
        if not self.daytona:
            raise Exception("Daytona SDK not initialized. Please set DAYTONA_API_KEY environment variable.")
        
        print(f"Creating worker sandbox...")
        try:
            # Create a standard python environment instead of cloning a repo
            # This is faster and we upload scripts anyway
            params = CreateSandboxBaseParams(language="python", ephemeral=True)
            sandbox = self.daytona.create(params)
            print(f"Sandbox {sandbox.id} created.")
            
            # Setup dependencies
            print("Setting up dependencies in worker...")
            # We install all potential requirements
            deps = "pdfminer.six python-docx weasyprint jinja2 pyyaml nltk"
            # If we want ollama in the worker, we might need to install it too, 
            # but usually ollama needs to be installed as a service (curl ... | sh).
            # The 'ollama' python package is just a client.
            # Let's install the client.
            deps += " ollama"
            
            res = sandbox.process.exec(f"pip install {deps}")
            if res.exit_code != 0:
                print(f"Warning: Dependency installation might have failed: {res.result}")
            
            return sandbox
        except Exception as e:
            print(f"Failed to create sandbox: {e}")
            raise

    def cleanup_worker(self, sandbox):
        """Deletes the sandbox immediately."""
        # Ensure we use delete() as remove() is deprecated/not available in this SDK version
        try:
            print(f"Deleting sandbox {sandbox.id}...")
            # Pass the sandbox object, not the ID, as per SDK docs
            # If delete fails with attribute error, it might be because we passed a string before.
            if hasattr(self.daytona, 'delete'):
                self.daytona.delete(sandbox)
            elif hasattr(self.daytona, 'remove'):
                self.daytona.remove(sandbox)
            else:
                print("Warning: Daytona SDK does not have delete or remove method.")
            print(f"Sandbox {sandbox.id} deleted.")
        except Exception as e:
            print(f"Error cleaning up sandbox: {e}")

    def parse_resume(self, file_path, file_content):
        """
        1. Create Worker
        2. Upload file & script
        3. Run extraction
        4. Cleanup
        """
        print(f"Starting Parse Resume for {file_path}...")
        sandbox = None
        try:
            sandbox = self.create_worker_sandbox()
            
            # Upload script
            print("Uploading scripts...")
            with open('worker_extractor.py', 'r') as f:
                script_content = f.read()
            self.upload_file(sandbox, 'worker_extractor.py', script_content)
            
            # Upload Resume Extractor Lib
            with open('resume_extractor.py', 'r') as f:
                lib_content = f.read()
            self.upload_file(sandbox, 'resume_extractor.py', lib_content)

            # Upload Target File
            filename = os.path.basename(file_path)
            print(f"Uploading resume file: {filename}")
            self.upload_file(sandbox, filename, file_content)

            # Run
            cmd = f"python worker_extractor.py '{filename}'"
            print(f"Running command in sandbox: {cmd}")
            response = sandbox.process.exec(cmd)
            
            print(f"Worker Output:\n{response.result}")
            
            if response.exit_code != 0:
                raise Exception(f"Extraction failed: {response.result}")
            
            return response.result

        except Exception as e:
            print(f"Error in parse_resume: {e}")
            raise
        finally:
            if sandbox:
                self.cleanup_worker(sandbox)

    def generate_pdf(self, resume_data, style_data=None):
        """
        1. Create Worker
        2. Upload data & scripts
        3. Generate PDF
        4. Download PDF
        5. Cleanup
        """
        print("Starting PDF Generation...")
        sandbox = None
        try:
            sandbox = self.create_worker_sandbox()
            
            # Upload scripts and templates
            print("Uploading scripts and templates...")
            self.upload_file(sandbox, 'generate_resume.py', open('generate_resume.py').read())
            
            sandbox.process.exec("mkdir -p templates")
            self.upload_file(sandbox, 'templates/resume.html', open('templates/resume.html').read())
            
            # Upload Data
            import yaml
            yaml_str = yaml.dump(resume_data)
            self.upload_file(sandbox, 'resume.yaml', yaml_str)

            # Upload Style
            cmd_style_arg = ""
            if style_data:
                import json
                style_str = json.dumps(style_data)
                self.upload_file(sandbox, 'style.json', style_str)
                cmd_style_arg = " --style style.json"

            # Debug: List files and check CWD
            print("Debugging sandbox state...")
            debug_res = sandbox.process.exec("pwd && ls -la && ls -la templates")
            print(f"Sandbox State:\n{debug_res.result}")

            # Run
            cmd = f"python generate_resume.py --data resume.yaml{cmd_style_arg}"
            print(f"Running command: {cmd}")
            response = sandbox.process.exec(cmd)
            print(f"Worker Output:\n{response.result}")
            
            if response.exit_code != 0:
                raise Exception(f"Generation failed: {response.result}")
            
            # Find the PDF file
            ls_res = sandbox.process.exec("ls *.pdf")
            pdf_filename = ls_res.result.strip().split('\n')[0]
            if not pdf_filename:
                 raise Exception("No PDF file found in worker output")
            
            # Download
            print(f"Downloading {pdf_filename}...")
            cat_res = sandbox.process.exec(f"base64 {pdf_filename}")
            if cat_res.exit_code != 0:
                raise Exception("Failed to read PDF file")
            
            import base64
            # Clean up output (remove newlines/spaces if any)
            b64_content = cat_res.result.strip().replace('\n', '').replace('\r', '')
            pdf_content = base64.b64decode(b64_content)
            return pdf_content

        except Exception as e:
            print(f"Error in generate_pdf: {e}")
            raise
        finally:
            if sandbox:
                self.cleanup_worker(sandbox)

    def analyze_ats(self, resume_text, job_desc_text):
        """
        1. Create Worker
        2. Upload scripts & data
        3. Run ATS analysis
        4. Cleanup
        """
        print("Starting ATS Analysis...")
        sandbox = None
        try:
            sandbox = self.create_worker_sandbox()
            
            # Upload scripts
            print("Uploading scripts...")
            self.upload_file(sandbox, 'ats_analyzer.py', open('ats_analyzer.py').read())
            
            # Upload Data
            self.upload_file(sandbox, 'resume.txt', resume_text)
            self.upload_file(sandbox, 'job_desc.txt', job_desc_text)
            
            # Run
            # Install Ollama if we want to use it? 
            # The base setup doesn't have ollama server running.
            # We can try to install it.
            print("Checking/Installing Ollama in worker...")
            # This might be slow.
            sandbox.process.exec("curl -fsSL https://ollama.com/install.sh | sh")
            # Start ollama in background?
            # sandbox.process.exec("ollama serve &")
            # This is complex in a transient sandbox. 
            # For now, let's assume we use the python logic first.
            
            cmd = "python ats_analyzer.py resume.txt job_desc.txt" # --ollama omitted for speed/reliability unless requested
            # If user wants ollama, we need a persistent worker or pre-built image.
            
            print(f"Running command: {cmd}")
            response = sandbox.process.exec(cmd)
            print(f"Worker Output:\n{response.result}")
            
            if response.exit_code != 0:
                raise Exception(f"ATS Analysis failed: {response.result}")
            
            return json.loads(response.result)

        except Exception as e:
            print(f"Error in analyze_ats: {e}")
            raise
        finally:
            if sandbox:
                self.cleanup_worker(sandbox)

    def upload_file(self, sandbox, path, content):
        """Helper to upload file content to sandbox using a robust chunked base64-exec pattern."""
        import base64
        
        # Prepare content
        if isinstance(content, str):
            content_bytes = content.encode('utf-8')
        else:
            content_bytes = content

        b64_content = base64.b64encode(content_bytes).decode('utf-8')
        
        # Ensure directory exists
        dir_name = os.path.dirname(path)
        if dir_name:
            # We can use a simple python one-liner for mkdir
            sandbox.process.exec(f"python -c \"import os; os.makedirs('{dir_name}', exist_ok=True)\"")
        
        # Upload in chunks to avoid command length limits
        CHUNK_SIZE = 10000 # 10KB chunks
        total_len = len(b64_content)
        
        # Create/Clear the temporary b64 file
        temp_b64_path = path + ".b64"
        sandbox.process.exec(f"rm -f {temp_b64_path}")
        
        for i in range(0, total_len, CHUNK_SIZE):
            chunk = b64_content[i:i+CHUNK_SIZE]
            # Use printf to append chunk to temp file
            # We must be careful with shell escaping, but base64 is safe-ish.
            # However, simpler to use python to append to avoid shell limits/issues entirely.
            
            # Python script to append chunk
            # We pass chunk inside the script. 10KB is safe for exec.
            append_script = f"""
with open('{temp_b64_path}', 'a') as f:
    f.write('{chunk}')
"""
            # Encode script to avoid special char issues in shell
            script_b64 = base64.b64encode(append_script.encode('utf-8')).decode('utf-8')
            cmd = f"python -c \"import base64; exec(base64.b64decode('{script_b64}').decode('utf-8'))\""
            
            res = sandbox.process.exec(cmd)
            if res.exit_code != 0:
                raise Exception(f"Failed to upload chunk {i} of {path}: {res.result}")
        
        # Decode base64 file to target file
        decode_script = f"""
import base64
with open('{temp_b64_path}', 'r') as f_in, open('{path}', 'wb') as f_out:
    b64_data = f_in.read()
    f_out.write(base64.b64decode(b64_data))
"""
        decode_script_b64 = base64.b64encode(decode_script.encode('utf-8')).decode('utf-8')
        cmd = f"python -c \"import base64; exec(base64.b64decode('{decode_script_b64}').decode('utf-8'))\""
        
        res = sandbox.process.exec(cmd)
        if res.exit_code != 0:
            raise Exception(f"Failed to decode {path}: {res.result}")
            
        # Cleanup temp file
        sandbox.process.exec(f"rm {temp_b64_path}")
        
        print(f"Successfully uploaded {path}")

