import sqlite3
import os
import json
import shutil
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'resume_builder.db')
DATA_DIR = os.path.join(BASE_DIR, 'data')

DEFAULT_SECTION_NAMES = {
    "education": "EDUCATION",
    "technical_skills": "TECHNICAL SKILLS",
    "experience": "PROFESSIONAL EXPERIENCE",
    "projects": "PROJECTS AND HACKATHON HIGHLIGHTS",
    "extracurricular": "EXTRACURRICULAR ACTIVITIES / VOLUNTEER & RESEARCH PAPERS"
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            header_json TEXT NOT NULL DEFAULT '{}',
            section_names_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    ''')
    # Migration: add custom_sections_json column if missing
    try:
        conn.execute("SELECT custom_sections_json FROM user_settings LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE user_settings ADD COLUMN custom_sections_json TEXT NOT NULL DEFAULT '[]'")
    # Migration: add style_json column if missing
    try:
        conn.execute("SELECT style_json FROM user_settings LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE user_settings ADD COLUMN style_json TEXT NOT NULL DEFAULT '{}'")
    # Migration: add onboarding_complete column if missing
    try:
        conn.execute("SELECT onboarding_complete FROM users LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE users ADD COLUMN onboarding_complete INTEGER NOT NULL DEFAULT 0")
        # Mark all existing users as onboarded so they aren't forced through the flow
        conn.execute("UPDATE users SET onboarding_complete = 1")
    conn.commit()
    conn.close()


def create_user(name, email, password):
    conn = get_db()
    password_hash = generate_password_hash(password)
    try:
        cursor = conn.execute(
            'INSERT INTO users (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)',
            (email, password_hash, name, datetime.now().isoformat())
        )
        user_id = cursor.lastrowid

        default_header = {
            "name": name,
            "contact": {
                "location": "",
                "phone": "",
                "email": email,
                "github": "",
                "linkedin": "",
                "portfolio_label": "Portfolio",
                "portfolio_url": ""
            }
        }
        conn.execute(
            'INSERT INTO user_settings (user_id, header_json, section_names_json, custom_sections_json, style_json) VALUES (?, ?, ?, ?, ?)',
            (user_id, json.dumps(default_header), json.dumps(DEFAULT_SECTION_NAMES), '[]', '{}')
        )
        conn.commit()

        user_dir = os.path.join(DATA_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(os.path.join(user_dir, 'versions'), exist_ok=True)

        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        return None


def authenticate_user(email, password):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        return dict(user)
    return None


def verify_user_password(user_id, password):
    conn = get_db()
    user = conn.execute('SELECT password_hash FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        return True
    return False


def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute('SELECT id, email, name, created_at FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_dir(user_id):
    return os.path.join(DATA_DIR, str(user_id))


def get_user_versions_dir(user_id):
    return os.path.join(DATA_DIR, str(user_id), 'versions')


def get_user_settings(user_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if row:
        custom_sections_raw = row['custom_sections_json'] if 'custom_sections_json' in row.keys() else '[]'
        style_raw = row['style_json'] if 'style_json' in row.keys() else '{}'
        return {
            "header": json.loads(row['header_json']),
            "section_names": json.loads(row['section_names_json']),
            "custom_sections": json.loads(custom_sections_raw or '[]'),
            "style": json.loads(style_raw or '{}')
        }
    return {
        "header": {"name": "", "contact": {}},
        "section_names": DEFAULT_SECTION_NAMES.copy(),
        "custom_sections": [],
        "style": {}
    }


def update_user_settings(user_id, header=None, section_names=None, custom_sections=None, style=None):
    conn = get_db()
    current = get_user_settings(user_id)

    if header is not None:
        current["header"] = header
    if section_names is not None:
        current["section_names"] = section_names
    if custom_sections is not None:
        current["custom_sections"] = custom_sections
    if style is not None:
        current["style"] = style

    conn.execute(
        '''INSERT INTO user_settings (user_id, header_json, section_names_json, custom_sections_json, style_json)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             header_json = excluded.header_json,
             section_names_json = excluded.section_names_json,
             custom_sections_json = excluded.custom_sections_json,
             style_json = excluded.style_json''',
        (user_id, json.dumps(current["header"]), json.dumps(current["section_names"]),
         json.dumps(current["custom_sections"]), json.dumps(current["style"]))
    )
    conn.commit()
    conn.close()


def is_onboarding_complete(user_id):
    conn = get_db()
    row = conn.execute('SELECT onboarding_complete FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(row and row['onboarding_complete'])


def mark_onboarding_complete(user_id):
    conn = get_db()
    conn.execute('UPDATE users SET onboarding_complete = 1 WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()


def delete_user(user_id):
    """Delete a user account, settings, and all workspace data."""
    conn = get_db()
    conn.execute('DELETE FROM user_settings WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

    # Remove user data directory
    user_dir = get_user_dir(user_id)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)
