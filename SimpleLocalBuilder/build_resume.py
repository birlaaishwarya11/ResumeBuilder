import yaml
import os
import sys
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

def load_data(yaml_path):
    print(f"Loading data from {yaml_path}...")
    try:
        with open(yaml_path, 'r') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Error: File '{yaml_path}' not found.")
        sys.exit(1)
    except yaml.YAMLError as exc:
        print(f"Error parsing YAML: {exc}")
        sys.exit(1)

def generate_pdf(data, output_filename, template_dir='templates', template_name='resume.html'):
    print(f"Loading template '{template_name}' from '{template_dir}'...")
    try:
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template(template_name)
    except Exception as e:
        print(f"Error loading template: {e}")
        sys.exit(1)
    
    print("Rendering HTML...")
    try:
        # Pass empty style dict if your template expects it
        html_content = template.render(resume=data, style={})
    except Exception as e:
        print(f"Error rendering template: {e}")
        sys.exit(1)
    
    print(f"Generating PDF: {output_filename}...")
    try:
        HTML(string=html_content, base_url=os.getcwd()).write_pdf(output_filename)
        print(f"Success! Resume generated at: {os.path.abspath(output_filename)}")
    except Exception as e:
        print(f"Error generating PDF: {e}")
        sys.exit(1)

def main():
    # Configuration
    yaml_file = 'resume.yaml'
    output_file = 'resume.pdf'
    template_dir = 'templates'
    
    # Check if we are in the right directory or if templates exist
    if not os.path.exists(template_dir):
        # Maybe the user ran it from the parent directory?
        if os.path.exists(os.path.join('SimpleLocalBuilder', template_dir)):
            print("Warning: It seems you are running from the parent directory.")
            print("Please change directory to SimpleLocalBuilder first:")
            print("  cd SimpleLocalBuilder")
            print("  python3 build_resume.py")
            sys.exit(1)
        else:
            print(f"Error: '{template_dir}' directory not found.")
            sys.exit(1)

    data = load_data(yaml_file)
    generate_pdf(data, output_file, template_dir)

if __name__ == "__main__":
    main()
