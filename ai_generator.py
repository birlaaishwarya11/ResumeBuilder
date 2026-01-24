
import os
import json
from litellm import completion
from typing import Dict, Any

class AIGenerator:
    def __init__(self):
        # Using local Ollama with Llama3
        self.model = "ollama/llama3" 
        self.api_base = "http://localhost:11434"
        pass

    def parse_resume(self, text: str) -> Dict[str, Any]:
        """
        Parses resume text into a dynamic JSON structure using AI.
        """
        prompt = f"""
        You are an expert resume parser. 
        Analyze the following resume text and extract ALL information into a structured JSON object.
        
        Rules:
        1. Do NOT force a predefined schema. Create keys that best represent the content (e.g., "Work Experience", "Academic Projects", "Volunteering", "Patents").
        2. Use standard keys for common fields if they exist (e.g., "name", "email", "phone", "summary", "education", "experience", "skills").
        3. For lists of items (jobs, projects), use a list of objects.
        4. Capture dates, locations, and descriptions accurately.
        5. Return ONLY the valid JSON string. No markdown formatting.
        
        Resume Text:
        {text}
        """

        try:
            response = completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                api_base=self.api_base,
                temperature=0.1
            )
            content = response.choices[0].message.content.strip()
            print(f"DEBUG: AI Raw Output: {content[:100]}...") # Debug print
            
            # Robust JSON extraction
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                content = json_match.group(0)
            else:
                # If no braces found, maybe it's just raw?
                pass
                
            return json.loads(content)
        except Exception as e:
            print(f"AI Parsing failed: {e}")
            # Fallback or re-raise?
            # If AI fails, we might want to return a basic dict or error.
            raise e

    def generate_html_template(self, data_sample: Dict[str, Any]) -> str:
        """
        Generates a Jinja2 HTML template tailored to the specific JSON structure.
        """
        data_str = json.dumps(data_sample, indent=2)
        
        prompt = f"""
        You are a frontend web developer and designer.
        Create a single-file HTML resume template using Jinja2 syntax that perfectly renders the following JSON data structure.
        
        Data Structure Sample:
        {data_str}
        
        Requirements:
        1. **Dynamic Rendering**: The template must explicitly handle the top-level keys present in the sample data.
           - For "name", "contact", "summary", place them in a Header section.
           - For other sections (lists like "experience", "education"), iterate over them using `{{% for item in resume.section_key %}}`.
           - Detect the fields inside the items (e.g., 'title', 'company', 'date', 'description') and render them appropriate.
        2. **Design**: Use a clean, modern, professional layout. Use embedded CSS (in <style> tags).
           - Use a sans-serif font (Inter, Roboto, or system-ui).
           - Use a subtle color scheme (dark text, light background, maybe a defined accent color).
           - Ensure good spacing and readability.
        3. **Jinja2**: Use `resume` as the root object (e.g., `{{{{ resume.name }}}}`).
           - Check if fields exist before printing (e.g., `{{% if item.date %}}...{{% endif %}}`).
        4. **Output**: Return ONLY the raw HTML code. No markdown.
        
        The goal is: When this HTML is rendered with the provided JSON, it should look like a professional resume.
        """

        try:
            response = completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                api_base=self.api_base,
                temperature=0.2
            )
            content = response.choices[0].message.content.strip()
            print(f"DEBUG: AI Raw HTML: {content[:100]}...")
            
            # Robust HTML extraction
            import re
            # Check for markdown code block first
            code_block = re.search(r'```html(.*?)```', content, re.DOTALL)
            if code_block:
                content = code_block.group(1).strip()
            else:
                # Fallback: look for <html ... </html> or just return content if it looks like HTML
                if "<html" in content or "<div" in content:
                    # Try to strip text before/after
                    start = content.find("<")
                    end = content.rfind(">") + 1
                    if start != -1 and end != -1:
                        content = content[start:end]
            
            return content
        except Exception as e:
            print(f"AI Template Generation failed: {e}")
            raise e

if __name__ == "__main__":
    # Test stub
    pass
