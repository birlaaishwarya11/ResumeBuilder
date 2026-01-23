
import requests
import json

url = "http://localhost:8000/api/convert"

# We need a session/cookie for login_required?
# app.py has @login_required.
# I might need to mock login or disable it for testing, or use a test client.
# Since I'm running the actual server, I need to log in first.

# Let's try to hit the login endpoint first.
session = requests.Session()
login_url = "http://localhost:8000/login"
# Assuming default user/pass or create one.
# app.py uses UserManager. verify_user checks if user exists.
# I can try to sign up a test user.

signup_url = "http://localhost:8000/signup"
test_user = "test_ai_user"
test_pass = "password"

# Signup
print("Signing up...")
r = session.post(signup_url, data={"username": test_user, "password": test_pass})
# If redirects to dashboard, we are logged in.

# Now try convert with AI
print("Testing AI Convert...")
text = """
John Doe
New York, NY
Software Engineer
Experience:
Google, Software Engineer, CA
2020-2022
- Built search engine
"""

payload = {
    "text": text,
    "from": "text",
    "to": "text", # we want structured text back
    "use_ai": True
}

try:
    r = session.post(url, json=payload)
    if r.status_code == 200:
        print("Success!")
        print(json.dumps(r.json(), indent=2))
    else:
        print(f"Failed: {r.status_code}")
        print(r.text)
except Exception as e:
    print(f"Error: {e}")
