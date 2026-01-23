import re

import yaml

SECTION_MAPPING = {
    "edcation": "education",
    "edu": "education",
    "education": "education",
    "exp": "experience",
    "experience": "experience",
    "work_experience": "experience",
    "work": "experience",
    "tech_skills": "technical_skills",
    "technical_skills": "technical_skills",
    "skills": "technical_skills",
    "projects": "projects",
    "project": "projects",
    "extracurriculars": "extracurricular",
    "extracurricular": "extracurricular",
    "activities": "extracurricular",
    "leadership": "extracurricular",
    "contact": "contact",
    "contact_info": "contact",
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

    return "\n".join(lines)


def ensure_resume_schema(data):
    """Ensures the resume data has all required fields."""
    if not isinstance(data, dict):
        data = {}

    defaults = {
        "name": "Your Name",
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
            key, val = line.split(":", 1)
            key = key.strip().lower().replace(" ", "_")
            val = val.strip()

            if current_section == "contact":
                data[current_section][key] = val
            elif current_item is not None:
                current_item[key] = val
            elif isinstance(data.get(current_section), dict):
                # Generic dict fields
                data[current_section][key] = val
            continue

        # Generic content line handling (fallback for lines that are not headers/bullets/keys)
        if current_section and line and not line.startswith(("#", "-", "•", "*")):
            # If we are in a list-based section
            if isinstance(data.get(current_section), list):
                # If no current item, create one using this line as the main identifier
                if current_item is None:
                    if current_section == "education":
                        current_item = {"institution": line}
                    elif current_section == "experience":
                        current_item = {"company": line, "bullets": []}
                    elif current_section == "projects":
                        current_item = {"name": line, "bullets": []}
                    else:
                        current_item = {"name": line, "bullets": []}
                    data[current_section].append(current_item)
                else:
                    # Item exists. Treat this as a detail line (add to bullets)
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
