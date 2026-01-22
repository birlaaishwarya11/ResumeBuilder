import sys
import os
from resume_extractor import extract_resume_content

def main():
    if len(sys.argv) < 2:
        print("Usage: python worker_extractor.py <file_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found")
        sys.exit(1)

    result = extract_resume_content(file_path)
    print(result)

if __name__ == "__main__":
    main()
