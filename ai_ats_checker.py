import yaml
import argparse
import sys
import json
import os
from litellm import completion

import subprocess

# Default model: can be changed via env var or arg
# Examples: "ollama/llama3", "gpt-3.5-turbo", "gemini/gemini-pro"
DEFAULT_MODEL = "ollama/llama3"

def get_ollama_models():
    """Returns a list of available local Ollama models."""
    try:
        result = subprocess.run(['ollama', 'list'], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')[1:] # Skip header
            models = [line.split()[0].split(':')[0] for line in lines] # Get names without tags mostly, or keep tags?
            # ollama list output: NAME ID SIZE MODIFIED
            # We want the full name usually, e.g. llama3:latest. 
            # But litellm might want ollama/llama3. 
            # Let's just get the first column.
            full_names = [line.split()[0] for line in lines]
            return full_names
    except FileNotFoundError:
        return []
    return []

class AIATSAnalyzer:
    def __init__(self, resume_path, job_description_path, model=None, api_key=None):
        self.resume_data = self._load_yaml(resume_path)
        self.job_description = self._load_text(job_description_path)
        self.model = model or os.getenv("ATS_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.getenv("ATS_API_KEY")

        # Auto-fallback for Ollama if default model missing
        if self.model.startswith("ollama/") and not self.api_key:
             model_name = self.model.replace("ollama/", "")
             available_models = get_ollama_models()
             
             # If specified model is not in list (exact match or match before colon)
             # Note: user might pass "llama3", list has "llama3:latest"
             has_model = any(m == model_name or m.split(':')[0] == model_name for m in available_models)
             
             if not has_model and available_models:
                 print(f"Warning: '{model_name}' not found in local Ollama.")
                 print(f"Switching to available model: {available_models[0]}")
                 self.model = f"ollama/{available_models[0]}"

    def _load_yaml(self, path):
        try:
            with open(path, 'r') as file:
                return yaml.safe_load(file)
        except FileNotFoundError:
            print(f"Error: File '{path}' not found.")
            sys.exit(1)

    def _load_text(self, path):
        try:
            with open(path, 'r') as file:
                return file.read()
        except FileNotFoundError:
            print(f"Error: File '{path}' not found.")
            sys.exit(1)

    def analyze(self):
        resume_json = json.dumps(self.resume_data)
        
        prompt = f"""
        You are an expert ATS (Applicant Tracking System) and Resume Coach.
        
        Task: Analyze the following Resume against the Job Description (JD).
        
        Resume (JSON):
        {resume_json}
        
        Job Description:
        {self.job_description}
        
        Output format: Return ONLY a valid JSON object with the following structure (do not include markdown formatting):
        {{
            "match_score": <number 0-100>,
            "missing_keywords": [<list of strings - high priority missing skills/keywords>],
            "suggestions": [<list of actionable advice strings>],
            "summary": "<short text summary of fit>"
        }}
        """
        
        print(f"Analyzing with model: {self.model}...")
        
        try:
            response = completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                api_key=self.api_key
            )
            
            content = response.choices[0].message.content
            # Clean up potential markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1]
                
            result = json.loads(content.strip())
            return result
            
        except Exception as e:
            print(f"Error calling LLM: {e}")
            print("\nTip: If using Ollama, ensure 'ollama serve' is running and you have the model pulled (e.g., 'ollama pull llama3').")
            print("Tip: If using OpenAI/Gemini, ensure ATS_API_KEY is set.")
            return None

    def print_report(self, result):
        if not result:
            return

        print("\n" + "="*40)
        print(f" AI ATS REPORT ")
        print("="*40)
        print(f"Match Score: {result.get('match_score', 0)}/100")
        print(f"Summary: {result.get('summary', '')}")
        
        print("\n--- Missing Keywords ---")
        for kw in result.get('missing_keywords', []):
            print(f"- {kw}")
            
        print("\n--- Suggestions ---")
        for suggestion in result.get('suggestions', []):
            print(f"- {suggestion}")
        print("="*40 + "\n")

def main():
    parser = argparse.ArgumentParser(description='AI-Powered ATS Checker')
    parser.add_argument('--resume', default='resume.yaml', help='Path to resume YAML file')
    parser.add_argument('--jd', default='job_description.txt', help='Path to Job Description text file')
    parser.add_argument('--model', help='Model to use (e.g., ollama/llama3, gpt-4o, gemini/gemini-1.5-flash)')
    parser.add_argument('--key', help='API Key (optional if using local model)')
    
    args = parser.parse_args()
    
    analyzer = AIATSAnalyzer(args.resume, args.jd, args.model, args.key)
    result = analyzer.analyze()
    analyzer.print_report(result)

if __name__ == "__main__":
    main()
