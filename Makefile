install:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt

server:
	./venv/bin/python app.py
