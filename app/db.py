import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "block1.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    filename TEXT,
    source TEXT,
    slide_count INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    summary_status TEXT DEFAULT 'not_started'
);

CREATE TABLE IF NOT EXISTS slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    slide_num INTEGER NOT NULL,
    text TEXT NOT NULL,
    caption TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS slide_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slide_id INTEGER NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
    path TEXT NOT NULL,                 -- relative path under data/images
    kind TEXT DEFAULT 'page',           -- 'page' (pdf render)
    seq INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS summaries (
    lecture_id INTEGER PRIMARY KEY REFERENCES lectures(id) ON DELETE CASCADE,
    body TEXT,
    key_points TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    slide_id INTEGER REFERENCES slides(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    options TEXT NOT NULL,
    correct_index INTEGER NOT NULL,
    explanation TEXT,
    level TEXT DEFAULT 'recall',
    source TEXT DEFAULT 'generated',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    status TEXT DEFAULT 'active',
    mode TEXT,
    target_count INTEGER,
    completed_count INTEGER DEFAULT 0,
    lecture_id INTEGER,
    current_question_id INTEGER,
    tutor_mode INTEGER DEFAULT 1,
    time_limit_min INTEGER DEFAULT 0,
    elapsed_sec INTEGER DEFAULT 0,
    gen_status TEXT DEFAULT 'ready',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS missed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    lecture_id INTEGER,
    missed_at TEXT DEFAULT (datetime('now')),
    resolved INTEGER DEFAULT 0,
    last_wrong_at TEXT
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    session_id INTEGER,
    correct INTEGER NOT NULL,
    answered_at TEXT DEFAULT (datetime('now')),
    selected_index INTEGER
);

CREATE TABLE IF NOT EXISTS session_questions (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    answered INTEGER DEFAULT 0,
    selected_index INTEGER,
    PRIMARY KEY (session_id, question_id)
);
"""


def get_conn(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    """Idempotent migrations. Additive columns are added if missing; orphaned
    tables/columns from the pre-vision rework are dropped if present."""
    SCHEMA_VERSION = 14

    def cols(table):
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]

    # ---- Drop orphaned tables (legacy concept/scheduler layers) ----
    for t in ("concepts", "concept_details", "reviews", "scheduler_state"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")

    # ---- Drop orphaned columns ----
    if "ocr_text" in cols("slides"):
        conn.execute("ALTER TABLE slides DROP COLUMN ocr_text")
    if "ocr_status" in cols("lectures"):
        conn.execute("ALTER TABLE lectures DROP COLUMN ocr_status")
    if "concept_ids" in cols("questions"):
        conn.execute("ALTER TABLE questions DROP COLUMN concept_ids")

    # ---- Additive columns ----
    if "elapsed_sec" not in cols("sessions"):
        conn.execute("ALTER TABLE sessions ADD COLUMN elapsed_sec INTEGER DEFAULT 0")
    if "selected_index" not in cols("answers"):
        conn.execute("ALTER TABLE answers ADD COLUMN selected_index INTEGER")
    if "selected_index" not in cols("session_questions"):
        conn.execute("ALTER TABLE session_questions ADD COLUMN selected_index INTEGER")

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def to_json(obj):
    return json.dumps(obj)


def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()
