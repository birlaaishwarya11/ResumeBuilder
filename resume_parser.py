import re

import yaml

SECTION_MAPPING = {
    "edcation": "education",
    "edu": "education",
    "education": "education",
    "exp": "experience",
    "experience": "experience",
    "work_experience": "experience",
    "professional_experience": "experience",
    "work_history": "experience",
    "employment": "experience",
    "work": "experience",
    "tech_skills": "technical_skills",
    "technical_skills": "technical_skills",
    "core_competencies": "technical_skills",
    "skills": "technical_skills",
    "projects": "projects",
    "project": "projects",
    "extracurriculars": "extracurricular",
    "extracurricular": "extracurricular",
    "activities": "extracurricular",
    "leadership": "extracurricular",
    "contact": "contact",
    "contact_info": "contact",
    "contact_information": "contact",
    "summary": "summary",
    "professional_summary": "summary",
    "profile": "summary",
}


def to_text(data):
    lines = []

    # Name (Always first)
    if "name" in data:
        lines.append(f"# Name: {data['name']}")
        lines.append("")

    # Process all other keys in order
    for key, value in data.items():
        if key == "name":
            continue

        # Capitalize and format section header
        header = key.replace("_", " ").title()
        lines.append(f"## {header}")

        if key == "contact":
            for k, v in value.items():
                lines.append(f"{k.capitalize()}: {v}")
            lines.append("")
            continue

        # Handle list of items (Education, Experience, Projects, etc.)
        if isinstance(value, list):
            for item in value:
                # Handle simple list of strings (e.g. skills list or summary lines)
                if isinstance(item, str):
                    lines.append(f"- {item}")
                    continue

                # Determine title key based on content
                title = "Unknown"
                if "institution" in item:
                    title = item["institution"]
                elif "company" in item:
                    title = item["company"]
                elif "name" in item:
                    title = item["name"]
                elif "category" in item:  # Skills
                    lines.append(f"- {item['category']}: {item['skills']}")
                    continue

                if title != "Unknown":
                    lines.append(f"### {title}")

                # Fields
                for k, v in item.items():
                    if k not in ["institution", "company", "name", "bullets", "category", "skills"]:
                        lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")

                # Bullets
                if "bullets" in item:
                    for b in item["bullets"]:
                        lines.append(f"- {b}")
                lines.append("")

        # Handle Dictionary (Extracurricular or others)
        elif isinstance(value, dict):
            if "bullets" in value:
                for b in value["bullets"]:
                    lines.append(f"- {b}")
            if "research_papers" in value:
                for paper in value["research_papers"]:
                    lines.append("")
                    lines.append(f"### Research Paper: {paper.get('title', '')}")
                    for k, v in paper.items():
                        if k != "title":
                            lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")
            # Generic dict handling if not matching above
            if "bullets" not in value and "research_papers" not in value:
                for k, v in value.items():
                    lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")
            lines.append("")

        # Handle Simple String (e.g. Summary)
        elif isinstance(value, str):
            lines.append(value)
            lines.append("")

    return "\n".join(lines)


def ensure_resume_schema(data):
    """Ensures the resume data has all required fields."""
    if not isinstance(data, dict):
        data = {}

    defaults = {
        "name": "Your Name",
        "summary": "",
        "contact": {
            "location": "",
            "phone": "",
            "email": "",
            "linkedin": "",
            "portfolio_url": "",
            "portfolio_label": ""
        },
        "education": [],
        "technical_skills": [],
        "experience": [],
        "projects": [],
        "extracurricular": {"bullets": [], "research_papers": []}
    }

    # Deep merge defaults
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
        elif isinstance(value, dict) and isinstance(data[key], dict):
            for subkey, subvalue in value.items():
                if subkey not in data[key]:
                    data[key][subkey] = subvalue
    
    return data

def parse_text(text):
    data = {}
    current_section = None
    current_item = None  # For list items (edu, exp, proj)

    lines = text.split("\n")


    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Top level name
        if line.startswith("# Name:"):
            data["name"] = line.replace("# Name:", "").strip()
            # Default to contact section immediately after name
            current_section = "contact"
            if "contact" not in data:
                data["contact"] = {}
            continue

        # Section Headers
        if line.startswith("## "):
            # Store raw section name to preserve user intent, but normalize key
            raw_section = line.replace("## ", "").strip()
            normalized_key = raw_section.lower().replace(" ", "_")
            section_key = SECTION_MAPPING.get(normalized_key, normalized_key)
            current_section = section_key
            current_item = None

            # Initialize container based on likely type
            # We can't know for sure until we see items, but we can default to list
            # unless it's contact or specific known dicts
            if section_key == "contact":
                data[section_key] = {}
            elif section_key == "extracurricular":
                data[section_key] = {"bullets": [], "research_papers": []}
            else:
                data[section_key] = []
            continue

        # Item Headers (###)
        if line.startswith("### "):
            header_val = line.replace("### ", "").strip()

            # Special case for Research Papers
            if current_section == "extracurricular" and header_val.startswith("Research Paper:"):
                title = header_val.replace("Research Paper:", "").strip()
                current_item = {"title": title}
                if "research_papers" not in data[current_section]:
                    data[current_section]["research_papers"] = []
                data[current_section]["research_papers"].append(current_item)
                continue

            # General List Item
            if isinstance(data.get(current_section), list):
                # We need to guess the "title key" (institution, company, name)
                # But since we are parsing TO yaml, we can normalize.
                # OR we can just use a temporary dict and fix it later?
                # Better: Use context.
                if current_section == "education":
                    current_item = {"institution": header_val}
                elif current_section == "experience":
                    current_item = {"company": header_val, "bullets": []}
                elif current_section == "projects":
                    current_item = {"name": header_val, "bullets": []}
                else:
                    # Generic fallback: treat ### as "name" or "title"
                    current_item = {"name": header_val, "bullets": []}

                data[current_section].append(current_item)
            continue

        # List Items (-)
        if line.startswith("- "):
            bullet_val = line[2:].strip()

            # Technical Skills (Category: Skills)
            if current_section == "technical_skills":
                if ":" in bullet_val:
                    cat, skills = bullet_val.split(":", 1)
                    data[current_section].append({"category": cat.strip(), "skills": skills.strip()})
                continue

            # Generic Bullets
            if current_section == "extracurricular":
                if current_item is None:
                    if "bullets" not in data[current_section]:
                        data[current_section]["bullets"] = []
                    data[current_section]["bullets"].append(bullet_val)
                continue

            if current_item is not None and "bullets" in current_item:
                current_item["bullets"].append(bullet_val)
            continue

        # Key: Value pairs
        if ":" in line:
            key_part, val_part = line.split(":", 1)
            if len(key_part) < 25: # Heuristic: Keys shouldn't be too long
                key = key_part.strip().lower().replace(" ", "_")
                val = val_part.strip()

                if key == "summary":
                    data["summary"] = val
                    continue

                if current_section == "contact":
                    data[current_section][key] = val
                elif current_item is not None:
                    current_item[key] = val
                elif isinstance(data.get(current_section), dict):
                    # Generic dict fields
                    data[current_section][key] = val
                continue
            # If key is long, treat as generic content (fall through)

        # Generic content line handling (fallback for lines that are not headers/bullets/keys)
        if current_section and line and not line.startswith(("#", "-", "•", "*")):
            # Special handling for Contact section (if line doesn't have key:value)
            if current_section == "contact":
                # Heuristics for unlabeled contact info
                if "@" in line:
                    data["contact"]["email"] = line
                elif re.search(r"\d{10}|\d{3}[-\s]\d{3}[-\s]\d{4}", line):
                    data["contact"]["phone"] = line
                elif "linkedin.com" in line:
                    data["contact"]["linkedin"] = line
                elif "github.com" in line or "portfolio" in line.lower():
                    data["contact"]["portfolio_url"] = line
                else:
                    # Assume location or other info if not already set
                    if not data["contact"].get("location"):
                        data["contact"]["location"] = line
                    else:
                        # Append to location if it looks like address parts
                        data["contact"]["location"] += ", " + line
                continue

            # If we are in a list-based section
            if isinstance(data.get(current_section), list):
                # If no current item, create one using this line as the main identifier
                if current_item is None:
                    if current_section == "education":
                        # Try to parse Institution, Degree, Location, Year
                        # Heuristic: If comma separated, assume first part is institution
                        parts = [p.strip() for p in line.split(",") if p.strip()]
                        if len(parts) > 1:
                            current_item = {"institution": parts[0]}
                            # Try to identify other parts?
                            # For now just set institution. We can refine later.
                        else:
                            current_item = {"institution": line}
                    elif current_section == "experience":
                        # Try to parse Company, Role, Location
                        # Heuristic: Split by comma.
                        parts = [p.strip() for p in line.split(",") if p.strip()]
                        if len(parts) >= 2:
                            # Heuristic: "Company, Role, Location" or "Company, Role"
                            current_item = {
                                "company": parts[0],
                                "role": parts[1],
                                "bullets": []
                            }
                            if len(parts) > 2:
                                # Check if 3rd part looks like location
                                if re.search(r"[A-Z][a-z]+", parts[2]):
                                     current_item["location"] = parts[2]
                                     if len(parts) > 3:
                                         # Maybe Country?
                                         current_item["location"] += ", " + parts[3]
                        else:
                            current_item = {"company": line, "bullets": []}
                    elif current_section == "projects":
                        current_item = {"name": line, "bullets": []}
                    else:
                        current_item = {"name": line, "bullets": []}
                    data[current_section].append(current_item)
                else:
                    # Item exists. Treat this as a detail line (add to bullets) OR Metadata
                    
                    # Special handling for Education details (Degree, GPA) - Check BEFORE generic Location
                    if current_section == "education":
                         # Check for GPA
                         gpa_match = re.search(r"GPA[:\s]+([\d\.]+)", line, re.IGNORECASE)
                         if gpa_match:
                             current_item["gpa"] = gpa_match.group(1)
                             # If the line is JUST GPA, continue. Else, might be "Degree, GPA"
                             if len(line) < 15:
                                 continue
                         
                         # Check for Degree
                         degree_match = re.search(r"(?i)\b(B\.?S\.?|B\.?A\.?|M\.?S\.?|M\.?A\.?|Ph\.?D|Bachelor|Master|Doctor|BTech|MTech|MEng|MBA)\b", line)
                         if degree_match:
                             # If we haven't set a degree yet, or this line looks more like a degree
                             current_item["degree"] = line
                             continue

                    # Check for Date
                    date_match = re.search(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}|Present|Now|\d{4}\s*[-–]\s*(?:Present|Now|\d{4})", line, re.IGNORECASE)
                    if date_match and len(line) < 30: # Date lines are usually short
                        current_item["date"] = line
                        continue
                    
                    # Check for Location (City, ST or City, Country)
                    # Heuristic: Comma separated, capitalized words, short length
                    if "," in line and len(line) < 40:
                         parts = [p.strip() for p in line.split(",")]
                         if all(p and p[0].isupper() for p in parts if p):
                             if "location" in current_item:
                                 current_item["location"] = line
                             else:
                                 current_item["location"] = line
                             continue

                    if "bullets" not in current_item:
                        current_item["bullets"] = []
                    current_item["bullets"].append(line)

    return ensure_resume_schema(data)


if __name__ == "__main__":
    # Test with existing yaml
    with open("resume.yaml", "r") as f:
        original_data = yaml.safe_load(f)

    text = to_text(original_data)
    print("--- GENERATED TEXT ---")
    print(text)

    parsed_data = parse_text(text)
    print("\n--- PARSED YAML ---")
    print(yaml.dump(parsed_data, sort_keys=False))
