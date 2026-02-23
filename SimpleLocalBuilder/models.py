import os
import json
import shutil
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# --- Database backend selection ---
DATABASE_URL = os.environ.get('DATABASE_URL', '')
DB_BACKEND = 'postgres' if DATABASE_URL.startswith('postgres') else 'sqlite'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
DB_PATH = os.path.join(BASE_DIR, 'resume_builder.db')  # SQLite only

if DB_BACKEND == 'postgres':
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    PH = '%s'
else:
    import sqlite3
    PH = '?'

DEFAULT_SECTION_NAMES = {
    "education": "EDUCATION",
    "technical_skills": "TECHNICAL SKILLS",
    "experience": "PROFESSIONAL EXPERIENCE",
    "projects": "PROJECTS AND HACKATHON HIGHLIGHTS",
    "extracurricular": "EXTRACURRICULAR ACTIVITIES / VOLUNTEER & RESEARCH PAPERS"
}


def get_db():
    if DB_BACKEND == 'postgres':
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def _fetchone(conn, query, params=()):
    """Execute a query and return one row as a dict, or None."""
    if DB_BACKEND == 'postgres':
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    else:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def _execute(conn, query, params=()):
    """Execute a query (INSERT/UPDATE/DELETE)."""
    if DB_BACKEND == 'postgres':
        cur = conn.cursor()
        cur.execute(query, params)
        cur.close()
    else:
        conn.execute(query, params)


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()

    if DB_BACKEND == 'postgres':
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                header_json TEXT NOT NULL DEFAULT '{}',
                section_names_json TEXT NOT NULL DEFAULT '{}',
                custom_sections_json TEXT NOT NULL DEFAULT '[]',
                style_json TEXT NOT NULL DEFAULT '{}'
            )
        ''')
        conn.commit()
        cur.close()
    else:
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
        # SQLite migrations for existing databases
        try:
            conn.execute("SELECT custom_sections_json FROM user_settings LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE user_settings ADD COLUMN custom_sections_json TEXT NOT NULL DEFAULT '[]'")
        try:
            conn.execute("SELECT style_json FROM user_settings LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE user_settings ADD COLUMN style_json TEXT NOT NULL DEFAULT '{}'")
        try:
            conn.execute("SELECT onboarding_complete FROM users LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE users ADD COLUMN onboarding_complete INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE users SET onboarding_complete = 1")
        conn.commit()

    conn.close()


def create_user(name, email, password):
    conn = get_db()
    pw_hash = generate_password_hash(password)
    try:
        if DB_BACKEND == 'postgres':
            cur = conn.cursor()
            cur.execute(
                f'INSERT INTO users (email, password_hash, name, created_at) VALUES ({PH}, {PH}, {PH}, {PH}) RETURNING id',
                (email, pw_hash, name, datetime.now().isoformat())
            )
            user_id = cur.fetchone()[0]
            cur.close()
        else:
            cursor = conn.execute(
                f'INSERT INTO users (email, password_hash, name, created_at) VALUES ({PH}, {PH}, {PH}, {PH})',
                (email, pw_hash, name, datetime.now().isoformat())
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
        _execute(conn,
            f'''INSERT INTO user_settings (user_id, header_json, section_names_json, custom_sections_json, style_json)
                VALUES ({PH}, {PH}, {PH}, {PH}, {PH})''',
            (user_id, json.dumps(default_header), json.dumps(DEFAULT_SECTION_NAMES), '[]', '{}')
        )
        conn.commit()

        user_dir = os.path.join(DATA_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(os.path.join(user_dir, 'versions'), exist_ok=True)

        conn.close()
        return user_id
    except Exception as e:
        is_duplicate = False
        if DB_BACKEND == 'postgres':
            is_duplicate = isinstance(e, psycopg2.errors.UniqueViolation)
            conn.rollback()
        else:
            is_duplicate = isinstance(e, sqlite3.IntegrityError)
        conn.close()
        if is_duplicate:
            return None
        raise


def authenticate_user(email, password):
    conn = get_db()
    user = _fetchone(conn, f'SELECT * FROM users WHERE email = {PH}', (email,))
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None


def verify_user_password(user_id, password):
    conn = get_db()
    user = _fetchone(conn, f'SELECT password_hash FROM users WHERE id = {PH}', (user_id,))
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        return True
    return False


def get_user_by_id(user_id):
    conn = get_db()
    user = _fetchone(conn, f'SELECT id, email, name, created_at FROM users WHERE id = {PH}', (user_id,))
    conn.close()
    return user


def get_user_dir(user_id):
    return os.path.join(DATA_DIR, str(user_id))


def get_user_versions_dir(user_id):
    return os.path.join(DATA_DIR, str(user_id), 'versions')


def get_user_settings(user_id):
    conn = get_db()
    row = _fetchone(conn, f'SELECT * FROM user_settings WHERE user_id = {PH}', (user_id,))
    conn.close()
    if row:
        custom_sections_raw = row.get('custom_sections_json', '[]')
        style_raw = row.get('style_json', '{}')
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

    _execute(conn,
        f'''INSERT INTO user_settings (user_id, header_json, section_names_json, custom_sections_json, style_json)
           VALUES ({PH}, {PH}, {PH}, {PH}, {PH})
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
    row = _fetchone(conn, f'SELECT onboarding_complete FROM users WHERE id = {PH}', (user_id,))
    conn.close()
    return bool(row and row['onboarding_complete'])


def mark_onboarding_complete(user_id):
    conn = get_db()
    _execute(conn, f'UPDATE users SET onboarding_complete = 1 WHERE id = {PH}', (user_id,))
    conn.commit()
    conn.close()


def delete_user(user_id):
    """Delete a user account, settings, and all workspace data."""
    conn = get_db()
    _execute(conn, f'DELETE FROM user_settings WHERE user_id = {PH}', (user_id,))
    _execute(conn, f'DELETE FROM users WHERE id = {PH}', (user_id,))
    conn.commit()
    conn.close()

    user_dir = get_user_dir(user_id)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)
