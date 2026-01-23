import os
import re

import docx
from pdfminer.high_level import extract_text


def extract_text_from_pdf(pdf_path):
    """Extracts raw text from a PDF file."""
    try:
        return extract_text(pdf_path)
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return ""


def extract_text_from_docx(docx_path):
    """Extracts raw text from a DOCX file."""
    try:
        doc = docx.Document(docx_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        print(f"Error reading DOCX: {e}")
        return ""


def extract_resume_content(file_path):
    """
    Determines file type and extracts text.
    Returns a formatted string suitable for the editor.
    """
    ext = os.path.splitext(file_path)[1].lower()
    raw_text = ""

    if ext == ".pdf":
        raw_text = extract_text_from_pdf(file_path)
    elif ext == ".docx":
        raw_text = extract_text_from_docx(file_path)
    else:
        return "# Error: Unsupported file format"

    if not raw_text:
        return "# Error: Could not extract text"

    return basic_formatting(raw_text)


def basic_formatting(text):
    """
    Attempts to format raw text into the editor's markdown-like syntax.
    """
    lines = text.split("\n")
    formatted_lines = []

    # Heuristics for headers
    common_headers = [
        "EDUCATION",
        "EXPERIENCE",
        "WORK EXPERIENCE",
        "PROJECTS",
        "SKILLS",
        "TECHNICAL SKILLS",
        "EXTRACURRICULAR",
        "ACTIVITIES",
        "CONTACT",
        "SUMMARY",
        "OBJECTIVE",
        "CERTIFICATIONS",
        "AWARDS",
    ]

    # Try to find name (usually first non-empty line)
    name_found = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if not name_found:
            formatted_lines.append(f"# Name: {line}")
            formatted_lines.append("")
            name_found = True
            continue

        # Check for headers
        if line.upper() in common_headers:
            formatted_lines.append(f"\n## {line.title()}")
            continue

        # Basic list item detection (if line starts with bullet-like chars)
        if line.startswith("•") or line.startswith("-") or line.startswith("*"):
            formatted_lines.append(f"- {line.lstrip('•-* ').strip()}")
        else:
            formatted_lines.append(line)

    return "\n".join(formatted_lines)
