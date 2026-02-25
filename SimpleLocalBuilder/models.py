import os
import json
import shutil
import secrets
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
    "education": "Education",
    "technical_skills": "Skills",
    "experience": "Experience",
    "projects": "Projects",
    "extracurricular": "Extracurricular"
}

_OLD_SECTION_NAMES_JSON = json.dumps({
    "education": "EDUCATION",
    "technical_skills": "TECHNICAL SKILLS",
    "experience": "PROFESSIONAL EXPERIENCE",
    "projects": "PROJECTS AND HACKATHON HIGHLIGHTS",
    "extracurricular": "EXTRACURRICULAR ACTIVITIES / VOLUNTEER & RESEARCH PAPERS"
})


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
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                header_json TEXT NOT NULL DEFAULT '{}',
                section_names_json TEXT NOT NULL DEFAULT '{}',
                custom_sections_json TEXT NOT NULL DEFAULT '[]',
                style_json TEXT NOT NULL DEFAULT '{}'
            )
        ''')
        # --- New tables ---
        cur.execute('''
            CREATE TABLE IF NOT EXISTS parsers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'DRAFT',
                label TEXT DEFAULT NULL,
                source_pdf_hash TEXT DEFAULT NULL,
                coverage_score REAL DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS resume_versions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                yaml_content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual_edit',
                label TEXT DEFAULT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS jd_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                jd_text TEXT NOT NULL,
                match_score INTEGER DEFAULT NULL,
                suggestions_json TEXT NOT NULL DEFAULT '[]',
                applied_version_id INTEGER DEFAULT NULL REFERENCES resume_versions(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            )
        ''')
        # Migrations for existing databases (legacy columns on users, kept for backward compat)
        for col, defval in [('parser_code', 'NULL'), ('parser_locked', '0'), ('mcp_api_key', 'NULL')]:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {defval}")
            except Exception:
                pass  # column already exists
        # Reset verbose legacy section name defaults to simple ones
        cur.execute(
            "UPDATE user_settings SET section_names_json = %s WHERE section_names_json = %s",
            (json.dumps(DEFAULT_SECTION_NAMES), _OLD_SECTION_NAMES_JSON)
        )
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
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS parsers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'DRAFT',
                label TEXT DEFAULT NULL,
                source_pdf_hash TEXT DEFAULT NULL,
                coverage_score REAL DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS resume_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                yaml_content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual_edit',
                label TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS jd_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                jd_text TEXT NOT NULL,
                match_score INTEGER DEFAULT NULL,
                suggestions_json TEXT NOT NULL DEFAULT '[]',
                applied_version_id INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (applied_version_id) REFERENCES resume_versions(id) ON DELETE SET NULL
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
        try:
            conn.execute("SELECT parser_code FROM users LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE users ADD COLUMN parser_code TEXT DEFAULT NULL")
        try:
            conn.execute("SELECT parser_locked FROM users LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE users ADD COLUMN parser_locked INTEGER NOT NULL DEFAULT 0")
        try:
            conn.execute("SELECT mcp_api_key FROM users LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE users ADD COLUMN mcp_api_key TEXT DEFAULT NULL")
        # Reset verbose legacy section name defaults to simple ones
        conn.execute(
            "UPDATE user_settings SET section_names_json = ? WHERE section_names_json = ?",
            (json.dumps(DEFAULT_SECTION_NAMES), _OLD_SECTION_NAMES_JSON)
        )
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
    _execute(conn, f'UPDATE users SET onboarding_complete = TRUE WHERE id = {PH}', (user_id,))
    conn.commit()
    conn.close()


def get_user_parser(user_id):
    """Return (parser_code, parser_locked) for a user, or (None, False)."""
    conn = get_db()
    row = _fetchone(conn, f'SELECT parser_code, parser_locked FROM users WHERE id = {PH}', (user_id,))
    conn.close()
    if row:
        return row.get('parser_code'), bool(row.get('parser_locked'))
    return None, False


def save_user_parser(user_id, code, locked=True):
    """Store generated parser code and optionally mark it locked."""
    conn = get_db()
    _execute(conn,
        f'UPDATE users SET parser_code = {PH}, parser_locked = {PH} WHERE id = {PH}',
        (code, 1 if locked else 0, user_id)
    )
    conn.commit()
    conn.close()


def clear_user_parser(user_id):
    """Remove the stored parser (e.g. when user wants to regenerate)."""
    conn = get_db()
    _execute(conn,
        f'UPDATE users SET parser_code = NULL, parser_locked = 0 WHERE id = {PH}',
        (user_id,)
    )
    conn.commit()
    conn.close()


def generate_mcp_api_key(user_id):
    """Generate (or regenerate) a cryptographically random MCP API key for a user.

    Returns the new key string.
    """
    key = secrets.token_urlsafe(32)   # 256 bits of entropy
    conn = get_db()
    _execute(conn, f'UPDATE users SET mcp_api_key = {PH} WHERE id = {PH}', (key, user_id))
    conn.commit()
    conn.close()
    return key


def get_user_by_mcp_key(api_key):
    """Look up and return the user row for a given MCP API key, or None."""
    if not api_key:
        return None
    conn = get_db()
    user = _fetchone(conn, f'SELECT id, email, name FROM users WHERE mcp_api_key = {PH}', (api_key,))
    conn.close()
    return user


def get_mcp_api_key(user_id):
    """Return the stored MCP API key for a user, or None if not yet generated."""
    conn = get_db()
    row = _fetchone(conn, f'SELECT mcp_api_key FROM users WHERE id = {PH}', (user_id,))
    conn.close()
    return row['mcp_api_key'] if row else None


def delete_user(user_id):
    """Delete a user account, settings, and all workspace data."""
    conn = get_db()
    # ON DELETE CASCADE handles user_settings, parsers, resume_versions, jd_sessions
    _execute(conn, f'DELETE FROM users WHERE id = {PH}', (user_id,))
    conn.commit()
    conn.close()

    user_dir = get_user_dir(user_id)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)


# ---------------------------------------------------------------------------
# Parsers table — parser lifecycle (DRAFT → ACTIVE → LOCKED)
# ---------------------------------------------------------------------------

PARSER_STATES = ('DRAFT', 'ACTIVE', 'LOCKED')


def create_parser(user_id, code, state='DRAFT', label=None, source_pdf_hash=None, coverage_score=None):
    """Insert a new parser row and return its id."""
    now = datetime.now().isoformat()
    conn = get_db()
    if DB_BACKEND == 'postgres':
        cur = conn.cursor()
        cur.execute(
            f'INSERT INTO parsers (user_id, code, state, label, source_pdf_hash, coverage_score, created_at, updated_at) '
            f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH}) RETURNING id',
            (user_id, code, state, label, source_pdf_hash, coverage_score, now, now)
        )
        parser_id = cur.fetchone()[0]
        cur.close()
    else:
        cursor = conn.execute(
            f'INSERT INTO parsers (user_id, code, state, label, source_pdf_hash, coverage_score, created_at, updated_at) '
            f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})',
            (user_id, code, state, label, source_pdf_hash, coverage_score, now, now)
        )
        parser_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return parser_id


def get_parser_by_id(parser_id):
    """Return parser row as dict or None."""
    conn = get_db()
    row = _fetchone(conn, f'SELECT * FROM parsers WHERE id = {PH}', (parser_id,))
    conn.close()
    return row


def get_active_parser(user_id):
    """Return the best parser for a user: LOCKED first, then ACTIVE, or None.

    Only one parser should be LOCKED at a time. ACTIVE is the in-progress draft
    the user is reviewing.
    """
    conn = get_db()
    # Prefer LOCKED, fall back to ACTIVE (most recent)
    row = _fetchone(
        conn,
        f"SELECT * FROM parsers WHERE user_id = {PH} AND state = 'LOCKED' ORDER BY updated_at DESC LIMIT 1",
        (user_id,)
    )
    if not row:
        row = _fetchone(
            conn,
            f"SELECT * FROM parsers WHERE user_id = {PH} AND state = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1",
            (user_id,)
        )
    conn.close()
    return row


def get_draft_parser(user_id):
    """Return the most recent DRAFT parser for a user, or None."""
    conn = get_db()
    row = _fetchone(
        conn,
        f"SELECT * FROM parsers WHERE user_id = {PH} AND state = 'DRAFT' ORDER BY created_at DESC LIMIT 1",
        (user_id,)
    )
    conn.close()
    return row


def list_parsers(user_id):
    """Return all parsers for a user (without code field) ordered newest first."""
    conn = get_db()
    if DB_BACKEND == 'postgres':
        cur = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)
        cur.execute(
            'SELECT id, user_id, state, label, source_pdf_hash, coverage_score, created_at, updated_at '
            f'FROM parsers WHERE user_id = {PH} ORDER BY created_at DESC',
            (user_id,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
    else:
        rows = conn.execute(
            'SELECT id, user_id, state, label, source_pdf_hash, coverage_score, created_at, updated_at '
            f'FROM parsers WHERE user_id = {PH} ORDER BY created_at DESC',
            (user_id,)
        ).fetchall()
        rows = [dict(r) for r in rows]
    conn.close()
    return rows


def update_parser_state(parser_id, state):
    """Transition a parser to a new state. Validates against PARSER_STATES."""
    if state not in PARSER_STATES:
        raise ValueError(f"Invalid parser state: {state}. Must be one of {PARSER_STATES}")
    now = datetime.now().isoformat()
    conn = get_db()
    _execute(conn,
        f'UPDATE parsers SET state = {PH}, updated_at = {PH} WHERE id = {PH}',
        (state, now, parser_id)
    )
    conn.commit()
    conn.close()


def update_parser_code(parser_id, code, coverage_score=None):
    """Update a parser's code (and optionally its coverage score)."""
    now = datetime.now().isoformat()
    conn = get_db()
    _execute(conn,
        f'UPDATE parsers SET code = {PH}, coverage_score = {PH}, updated_at = {PH} WHERE id = {PH}',
        (code, coverage_score, now, parser_id)
    )
    conn.commit()
    conn.close()


def lock_parser(user_id, parser_id):
    """Lock a specific parser and demote any previously LOCKED parser to ACTIVE."""
    now = datetime.now().isoformat()
    conn = get_db()
    # Demote all other LOCKED parsers for this user to ACTIVE
    _execute(conn,
        f"UPDATE parsers SET state = 'ACTIVE', updated_at = {PH} "
        f"WHERE user_id = {PH} AND state = 'LOCKED' AND id != {PH}",
        (now, user_id, parser_id)
    )
    # Lock the target parser
    _execute(conn,
        f"UPDATE parsers SET state = 'LOCKED', updated_at = {PH} WHERE id = {PH} AND user_id = {PH}",
        (now, parser_id, user_id)
    )
    conn.commit()
    conn.close()


def delete_parser(parser_id, user_id):
    """Delete a parser. user_id is required as a security check."""
    conn = get_db()
    _execute(conn, f'DELETE FROM parsers WHERE id = {PH} AND user_id = {PH}', (parser_id, user_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Resume versions table
# ---------------------------------------------------------------------------

RESUME_SOURCES = ('upload', 'manual_edit', 'jd_applied', 'ai_edit')


def save_resume_version(user_id, yaml_content, source='manual_edit', label=None):
    """Persist a resume snapshot. Returns the new version id."""
    if source not in RESUME_SOURCES:
        source = 'manual_edit'
    now = datetime.now().isoformat()
    conn = get_db()
    if DB_BACKEND == 'postgres':
        cur = conn.cursor()
        cur.execute(
            f'INSERT INTO resume_versions (user_id, yaml_content, source, label, created_at) '
            f'VALUES ({PH},{PH},{PH},{PH},{PH}) RETURNING id',
            (user_id, yaml_content, source, label, now)
        )
        version_id = cur.fetchone()[0]
        cur.close()
    else:
        cursor = conn.execute(
            f'INSERT INTO resume_versions (user_id, yaml_content, source, label, created_at) '
            f'VALUES ({PH},{PH},{PH},{PH},{PH})',
            (user_id, yaml_content, source, label, now)
        )
        version_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return version_id


def list_resume_versions(user_id):
    """Return all versions for a user (without yaml_content) newest first."""
    conn = get_db()
    if DB_BACKEND == 'postgres':
        cur = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)
        cur.execute(
            f'SELECT id, user_id, source, label, created_at FROM resume_versions '
            f'WHERE user_id = {PH} ORDER BY created_at DESC',
            (user_id,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
    else:
        rows = conn.execute(
            f'SELECT id, user_id, source, label, created_at FROM resume_versions '
            f'WHERE user_id = {PH} ORDER BY created_at DESC',
            (user_id,)
        ).fetchall()
        rows = [dict(r) for r in rows]
    conn.close()
    return rows


def get_resume_version(version_id, user_id):
    """Return a specific version dict (including yaml_content). user_id is a security check."""
    conn = get_db()
    row = _fetchone(
        conn,
        f'SELECT * FROM resume_versions WHERE id = {PH} AND user_id = {PH}',
        (version_id, user_id)
    )
    conn.close()
    return row


def get_latest_resume_version(user_id):
    """Return the most recent resume version for a user, or None."""
    conn = get_db()
    row = _fetchone(
        conn,
        f'SELECT * FROM resume_versions WHERE user_id = {PH} ORDER BY created_at DESC LIMIT 1',
        (user_id,)
    )
    conn.close()
    return row


# ---------------------------------------------------------------------------
# JD sessions table
# ---------------------------------------------------------------------------

def create_jd_session(user_id, jd_text):
    """Create a new JD session and return its id."""
    now = datetime.now().isoformat()
    conn = get_db()
    if DB_BACKEND == 'postgres':
        cur = conn.cursor()
        cur.execute(
            f'INSERT INTO jd_sessions (user_id, jd_text, created_at) VALUES ({PH},{PH},{PH}) RETURNING id',
            (user_id, jd_text, now)
        )
        session_id = cur.fetchone()[0]
        cur.close()
    else:
        cursor = conn.execute(
            f'INSERT INTO jd_sessions (user_id, jd_text, created_at) VALUES ({PH},{PH},{PH})',
            (user_id, jd_text, now)
        )
        session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return session_id


def update_jd_session(session_id, match_score, suggestions):
    """Attach analysis results to a JD session."""
    conn = get_db()
    _execute(conn,
        f'UPDATE jd_sessions SET match_score = {PH}, suggestions_json = {PH} WHERE id = {PH}',
        (match_score, json.dumps(suggestions), session_id)
    )
    conn.commit()
    conn.close()


def mark_jd_applied(session_id, version_id):
    """Record which resume version was produced by applying this JD session."""
    conn = get_db()
    _execute(conn,
        f'UPDATE jd_sessions SET applied_version_id = {PH} WHERE id = {PH}',
        (version_id, session_id)
    )
    conn.commit()
    conn.close()


def get_jd_session(session_id, user_id):
    """Return a JD session dict. user_id is a security check."""
    conn = get_db()
    row = _fetchone(
        conn,
        f'SELECT * FROM jd_sessions WHERE id = {PH} AND user_id = {PH}',
        (session_id, user_id)
    )
    conn.close()
    if row and isinstance(row.get('suggestions_json'), str):
        row['suggestions'] = json.loads(row['suggestions_json'])
    return row


def list_jd_sessions(user_id):
    """Return JD sessions for a user (without full jd_text) newest first."""
    conn = get_db()
    if DB_BACKEND == 'postgres':
        cur = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)
        cur.execute(
            f'SELECT id, user_id, match_score, applied_version_id, created_at, '
            f'LEFT(jd_text, 200) as jd_preview FROM jd_sessions '
            f'WHERE user_id = {PH} ORDER BY created_at DESC',
            (user_id,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
    else:
        rows = conn.execute(
            f'SELECT id, user_id, match_score, applied_version_id, created_at, '
            f'SUBSTR(jd_text, 1, 200) as jd_preview FROM jd_sessions '
            f'WHERE user_id = {PH} ORDER BY created_at DESC',
            (user_id,)
        ).fetchall()
        rows = [dict(r) for r in rows]
    conn.close()
    return rows
