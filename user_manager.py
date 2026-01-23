import sqlite3
import os
import shutil
from werkzeug.security import generate_password_hash, check_password_hash

DB_NAME = os.path.join("data", "users.db")
DATA_DIR = "data"

class UserManager:
    def __init__(self):
        self._ensure_data_dir()
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      username TEXT UNIQUE NOT NULL,
                      password_hash TEXT NOT NULL)''')
        conn.commit()
        conn.close()

    def _ensure_data_dir(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

    def create_user(self, username, password):
        try:
            password_hash = generate_password_hash(password)
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", 
                      (username, password_hash))
            conn.commit()
            conn.close()
            
            # Create private directory
            user_dir = os.path.join(DATA_DIR, username)
            os.makedirs(user_dir, exist_ok=True)
            
            # Initialize with default resume.yaml if not exists
            default_resume = "resume.yaml"
            if os.path.exists(default_resume):
                shutil.copy(default_resume, os.path.join(user_dir, "resume.yaml"))
                
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"Error creating user: {e}")
            return False

    def verify_user(self, username, password):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()
        
        if user and check_password_hash(user[0], password):
            return True
        return False

    def get_user_dir(self, username):
        return os.path.join(DATA_DIR, username)
