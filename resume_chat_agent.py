import os
import json
import yaml
from litellm import completion
from typing import Optional

class ResumeChatAgent:
    def __init__(self):
        self.default_model = os.getenv("ATS_MODEL", "ollama/llama3")
        self.default_api_key = os.getenv("ATS_API_KEY")

    def process_command(self, current_resume: dict, command: str, original_text: Optional[str] = None, template_content: Optional[str] = None) -> dict:
        """
        Process a user command to update the resume or template.
        
        Args:
            current_resume: The current resume data (dict).
            command: The user's natural language command.
            original_text: The raw text of the original resume (optional context).
            template_content: The current HTML template (optional, for design updates).
            
        Returns:
            A dict with 'action' ("update_data" or "update_template") and the payload ('data' or 'html').
        """
        
        system_prompt = f"""
        <System_Instruction>
        You are an expert Resume Editor and Web Designer. 
        Your task is to update EITHER the User's Resume Data (JSON) OR the Resume Template (HTML/Jinja2) based on their Command.
        
        Rules:
        1. Analyze the 'User Command' to determine if it is a CONTENT update or a DESIGN/TEMPLATE update.
        
        IF CONTENT UPDATE (e.g., "add skill", "change summary", "fix typo", "update experience"):
           - Modify the 'Current Resume JSON' accordingly.
           - Return JSON: {{ "action": "update_data", "data": <updated_resume_json> }}
           - If the command asks to add information that is not in the resume, you are authorized to "make things up" (hallucinate) plausible, professional details to fill the gaps.
           - Maintain existing structure.
           
        IF DESIGN/TEMPLATE UPDATE (e.g., "change header color to blue", "make name bigger", "add 2 column layout", "change font"):
           - You must have the 'Current_Template_HTML' provided below. If not, treat as content update or return error.
           - Modify the HTML/CSS to achieve the design goal.
           - CRITICAL: PRESERVE ALL JINJA2 VARIABLES (e.g., {{{{ resume.name }}}}, {{% if ... %}}). Do not remove data placeholders.
           - Return JSON: {{ "action": "update_template", "html": <updated_html_string> }}
           
        General Rules:
        - Return ONLY valid JSON.
        - No markdown formatting outside the JSON string values.
        </System_Instruction>
        
        <Current_Resume_JSON>
        {json.dumps(current_resume, indent=2)}
        </Current_Resume_JSON>
        """
        
        if template_content:
            system_prompt += f"""
            <Current_Template_HTML>
            {template_content}
            </Current_Template_HTML>
            """
            
        if original_text:
            system_prompt += f"""
            <Original_Resume_Text>
            {original_text[:5000]} 
            </Original_Resume_Text>
            """
            
        user_message = f"Command: {command}"
        
        try:
            print(f"DEBUG: Chat Agent Command: {command}")
            
            response = completion(
                model=self.default_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                api_key=self.default_api_key
            )
            
            content = response.choices[0].message.content.strip()
            print(f"DEBUG: Chat Agent Response: {content[:500]}...") # Log first 500 chars
            
            # Extract JSON if wrapped in markdown
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].strip()
                
            # Parse JSON
            try:
                parsed_response = json.loads(content)
                
                # Backwards compatibility / Robustness check
                if "action" not in parsed_response:
                    # Assume it's a direct resume update (legacy behavior compatibility)
                    if "contact" in parsed_response or "experience" in parsed_response:
                         return {"action": "update_data", "data": parsed_response}
                    # Or maybe it's just the HTML? Unlikely given instructions.
                    return {"action": "update_data", "data": parsed_response}
                    
                return parsed_response
                
            except json.JSONDecodeError:
                # Try to find the first { and last }
                start = content.find('{')
                end = content.rfind('}')
                if start != -1 and end != -1:
                    json_str = content[start:end+1]
                    parsed_response = json.loads(json_str)
                    if "action" not in parsed_response:
                         return {"action": "update_data", "data": parsed_response}
                    return parsed_response
                else:
                    raise Exception("Could not extract valid JSON from response")
                    
        except Exception as e:
            print(f"Resume Chat Agent Error: {e}")
            return {"error": str(e)}

resume_chat_agent = ResumeChatAgent()
