import argparse
import os
import sys
from datetime import datetime

import yaml
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML


def load_data(yaml_path):
    with open(yaml_path, "r") as file:
        return yaml.safe_load(file)


def generate_pdf(data, output_filename, template_dir="templates", template_name="resume.html", style=None):
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)

    html_content = template.render(resume=data, style=style or {})

    # Generate PDF
    HTML(string=html_content).write_pdf(output_filename)
    print(f"Resume generated: {output_filename}")


def main():
    parser = argparse.ArgumentParser(description="Generate Resume PDF from YAML data.")
    parser.add_argument(
        "--keywords", type=str, help='Comma separated keywords to include in filename (e.g., "LLM,Python")'
    )
    parser.add_argument("--data", type=str, default="resume.yaml", help="Path to YAML data file")
    parser.add_argument("--style", type=str, default=None, help="Path to JSON style file")

    args = parser.parse_args()

    try:
        data = load_data(args.data)
    except FileNotFoundError:
        print(f"Error: Data file '{args.data}' not found.")
        sys.exit(1)

    style = {}
    if args.style:
        try:
            import json

            with open(args.style, "r") as f:
                style = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load style file: {e}")

    # Construct filename
    base_name = "Aishwarya_Birla_Resume"
    date_str = datetime.now().strftime("%Y-%m-%d")

    keywords_part = ""
    if args.keywords:
        # Clean keywords: remove spaces around commas, replace spaces with underscores
        keywords = [k.strip().replace(" ", "_") for k in args.keywords.split(",")]
        keywords_part = "_" + "_".join(keywords)

    output_filename = f"{base_name}{keywords_part}_{date_str}.pdf"

    generate_pdf(data, output_filename, style=style)


if __name__ == "__main__":
    main()
