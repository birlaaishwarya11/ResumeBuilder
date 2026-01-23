import os
import yaml
import json
import shutil
from typing import Dict, Any, List
import datetime

# --- Document Server Logic ---
class DocumentServer:
    """
    Handles resume.yaml operations, versioning, and file system access safely.
    Acts as the 'File System MCP'.
    """
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = data_dir
        self.resume_path = os.path.join(data_dir, "resume.yaml")
        self.history_dir = os.path.join(data_dir, "history")
        
        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

    def read_resume(self) -> Dict[str, Any]:
        """Reads the current resume.yaml."""
        if not os.path.exists(self.resume_path):
            return {}
        with open(self.resume_path, 'r') as f:
            return yaml.safe_load(f)

    def update_resume(self, data: Dict[str, Any], create_version: bool = True) -> str:
        """Updates resume.yaml and optionally creates a version backup."""
        # Create version
        if create_version and os.path.exists(self.resume_path):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(self.history_dir, f"resume_{timestamp}.yaml")
            shutil.copy2(self.resume_path, backup_path)

        with open(self.resume_path, 'w') as f:
            yaml.dump(data, f, sort_keys=False)
        
        return "Resume updated successfully."

    def get_versions(self) -> List[str]:
        """Lists available resume versions."""
        return sorted(os.listdir(self.history_dir), reverse=True)

# --- ATS Validator Server Logic ---
class ATSValidatorServer:
    """
    Handles PDF generation and 'parseability' checks.
    Acts as the 'Logic MCP'.
    """
    def __init__(self, doc_server: DocumentServer):
        self.doc_server = doc_server

    def validate_resume(self) -> Dict[str, Any]:
        """
        Generates the PDF (simulated or real) and checks if it can be parsed back.
        Returns a 'health report'.
        """
        # 1. Trigger PDF Generation (using existing logic implicitly or explicit call)
        # For this 'Server' logic, we'll assume we check the YAML structure first
        data = self.doc_server.read_resume()
        
        issues = []
        score = 100

        # Basic Structure Check (Simulating ATS parsing requirements)
        required_sections = ['contact_info', 'experience', 'education', 'skills']
        for section in required_sections:
            if section not in data or not data[section]:
                issues.append(f"Missing section: {section}")
                score -= 20

        # Keyword Check (Simulating if content is rich enough)
        # This could call the AIATSAnalyzer, but let's keep it simple logic for now
        if 'skills' in data and len(data['skills']) < 5:
            issues.append("Low skill count (risk of low ATS ranking).")
            score -= 10

        return {
            "status": "healthy" if score > 80 else "needs_attention",
            "ats_parse_score": score,
            "issues": issues
        }

# Singleton Instances
doc_server = DocumentServer()
ats_server = ATSValidatorServer(doc_server)
