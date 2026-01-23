
import json
import os
from litellm import completion

class AIResumeParser:
    def __init__(self):
        self.default_model = os.getenv("ATS_MODEL", "ollama/llama3")
        self.default_api_key = os.getenv("ATS_API_KEY")

    def parse_resume(self, text: str) -> dict:
        """
        Uses an LLM to parse raw resume text into a structured JSON format.
        """
        
        schema_structure = {
            "name": "Full Name",
            "contact": {
                "location": "City, State/Country",
                "phone": "+1-123-456-7890",
                "email": "email@example.com",
                "linkedin": "https://linkedin.com/in/...",
                "portfolio_url": "https://...",
                "portfolio_label": "Portfolio/Website"
            },
            "education": [
                {
                    "institution": "University Name",
                    "degree": "Degree Name (e.g. BS Computer Science)",
                    "gpa": "3.9",
                    "location": "City, State",
                    "date": "Graduation Date (e.g. May 2024)",
                    "relevant_coursework": "Course 1, Course 2"
                }
            ],
            "experience": [
                {
                    "company": "Company Name",
                    "role": "Job Title",
                    "location": "City, State",
                    "date": "Start - End Date",
                    "bullets": ["Achievement 1", "Achievement 2"]
                }
            ],
            "projects": [
                {
                    "name": "Project Name",
                    "bullets": ["Detail 1", "Detail 2"]
                }
            ],
            "technical_skills": [
                {"category": "Languages", "skills": "Python, Java"},
                {"category": "Tools", "skills": "Docker, AWS"}
            ],
            "extracurricular": {
                "bullets": ["Activity 1", "Activity 2"],
                "research_papers": [{"title": "Paper Title", "publication": "Conference/Journal", "date": "Date"}]
            }
        }

        prompt = f"""
        <System_Instruction>
        You are an expert Resume Parser. Your goal is to extract structured data from the provided raw resume text.
        
        Rules:
        1. Extract the candidate's Name, Contact Info, Education, Experience, Projects, Skills, and Extracurriculars.
        2. Output MUST be a valid JSON object strictly following the structure below.
        3. Do not include any markdown formatting (like ```json). Just return the raw JSON string.
        4. If a field is not found, leave it empty or null (or empty list for arrays).
        5. For "experience" and "projects", extract bullet points as a list of strings.
        6. Infer "role", "company", "location", and "date" from the experience headers.
        
        Target JSON Structure:
        {json.dumps(schema_structure, indent=2)}
        </System_Instruction>

        <Raw_Resume_Text>
        {text}
        </Raw_Resume_Text>
        """

        try:
            # Use the configured model
            response = completion(
                model=self.default_model, 
                messages=[{"role": "user", "content": prompt}], 
                api_key=self.default_api_key
            )
            
            content = response.choices[0].message.content.strip()
            print(f"DEBUG: Raw LLM Response: {content[:500]}...") # Log first 500 chars

            # Clean up potential markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1]
            
            # Additional cleanup: find first '{' and last '}'
            start_idx = content.find('{')
            end_idx = content.rfind('}')
            
            if start_idx != -1 and end_idx != -1:
                content = content[start_idx:end_idx+1]
            else:
                print("DEBUG: No JSON object found in response")
                return {"error": "LLM did not return a valid JSON object"}

            parsed_data = json.loads(content)
            return parsed_data
            
        except Exception as e:
            print(f"AI Parsing Error: {e}")
            if 'content' in locals():
                print(f"Failed Content: {content}")
            # Fallback to empty structure or partial error
            return {"error": str(e)}

# Global instance
ai_parser = AIResumeParser()
