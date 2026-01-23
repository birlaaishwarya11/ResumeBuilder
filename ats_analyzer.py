import os
import subprocess
import sys

import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize

# Ensure NLTK data is downloaded (in case it's not in the image)
try:
    nltk.data.find("tokenizers/punkt")
except LookupError:
    nltk.download("punkt")
try:
    nltk.data.find("corpora/stopwords")
except LookupError:
    nltk.download("stopwords")


def analyze_keywords(resume_text, job_desc_text):
    """
    Analyzes the resume against the job description using NLTK.
    Returns a dictionary with match score and missing keywords.
    """
    resume_tokens = set(word_tokenize(resume_text.lower()))
    job_tokens = set(word_tokenize(job_desc_text.lower()))

    stop_words = set(stopwords.words("english"))

    # Filter stopwords and non-alphanumeric
    resume_keywords = {w for w in resume_tokens if w.isalnum() and w not in stop_words}
    job_keywords = {w for w in job_tokens if w.isalnum() and w not in stop_words}

    common_keywords = resume_keywords.intersection(job_keywords)
    missing_keywords = job_keywords - resume_keywords

    score = len(common_keywords) / len(job_keywords) if job_keywords else 0

    return {
        "match_score": round(score * 100, 2),
        "score": round(score * 100, 2),  # Backward compatibility
        "matched_count": len(common_keywords),
        "total_keywords": len(job_keywords),
        "missing_keywords": list(missing_keywords)[:10],  # Top 10 missing
        "summary": f"Matched {len(common_keywords)} out of {len(job_keywords)} keywords.",
        "suggestions": [f"Consider adding: {kw}" for kw in list(missing_keywords)[:5]],
    }


def run_ollama_analysis(resume_text, job_desc_text):
    """
    Uses Ollama to analyze the resume if available.
    """
    # Check if Ollama is running
    try:
        # Simple check using curl or ollama cli
        # We assume 'ollama' is in path
        prompt = f"Compare this resume to the job description and give 3 improvements:\n\nResume:\n{resume_text[:1000]}...\n\nJob Description:\n{job_desc_text[:1000]}..."

        cmd = ["ollama", "run", "llama3", prompt]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            return result.stdout
        else:
            return f"Ollama analysis failed: {result.stderr}"

    except FileNotFoundError:
        return "Ollama not installed or not found in path."
    except Exception as e:
        return f"Error running Ollama: {str(e)}"


def main():
    if len(sys.argv) < 3:
        print("Usage: python ats_analyzer.py <resume_file> <job_desc_file>")
        sys.exit(1)

    resume_file = sys.argv[1]
    job_file = sys.argv[2]

    try:
        with open(resume_file, "r") as f:
            resume_text = f.read()
        with open(job_file, "r") as f:
            job_text = f.read()

        analysis = analyze_keywords(resume_text, job_text)

        # Check for --ollama flag
        if "--ollama" in sys.argv:
            ollama_feedback = run_ollama_analysis(resume_text, job_text)
            analysis["ollama_feedback"] = ollama_feedback

        import json

        print(json.dumps(analysis, indent=2))

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
