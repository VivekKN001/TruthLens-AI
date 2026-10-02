"""
Storage - persistence for run history (SQLite locally, Postgres when deployed).

A run is snapshotted here at meaningful checkpoints (draft ready for human
review, finished, or errored) so past work survives a server/CLI restart and
can be browsed later. This is not the source of truth for an in-progress run
- the in-memory AgentState is authoritative while a run is active; this is
purely a record of what happened.

Each run belongs to a user (`user_id`). With sign-in disabled (local use)
everything belongs to LOCAL_USER_ID. Alongside the latest snapshot in `runs`,
`run_events` keeps the whole session as a thread - topic, every draft, each
piece of feedback, approval, errors - so past sessions can be replayed.

Backends: a local SQLite file by default (settings.storage.db_path). When
settings.storage.database_url is set (DATABASE_URL env var, e.g. a free Neon
Postgres) everything goes there instead - free hosts wipe their disk on every
restart, so a SQLite file there would lose everyone's history. Every function
takes an optional `db_path`, which always means SQLite at that path; tests
use it to point at a tmp_path DB.

All SQL here uses named `:param` placeholders, which both sqlite3 and
pg8000.native understand, so the same statements run on either backend.
"""

import json
import sqlite3
import ssl
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Owner of every run when sign-in is disabled (CLI, or the web UI with no
# OAuth provider configured). Runs saved before users existed get it too.
LOCAL_USER_ID = "local"

# {autoid} differs per backend: SQLite's AUTOINCREMENT vs Postgres' BIGSERIAL.
_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        topic TEXT NOT NULL,
        status TEXT NOT NULL,
        research_content TEXT DEFAULT '',
        research_sources TEXT DEFAULT '[]',
        blog_draft TEXT DEFAULT '',
        blog_final TEXT DEFAULT '',
        grounding_notes TEXT DEFAULT '',
        iteration_count INTEGER DEFAULT 0,
        max_iterations INTEGER DEFAULT 3,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        avatar_url TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_events (
        id {autoid},
        run_id TEXT NOT NULL,
        role TEXT NOT NULL,
        kind TEXT NOT NULL,
        data TEXT DEFAULT '{{}}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events (run_id, id)",
]

# Columns added after the first release. Existing databases get them via
# ALTER TABLE on first use, so an old history DB keeps working.
_ADDED_COLUMNS = {
    "grounding_fixes": "TEXT DEFAULT '[]'",
    "user_id": "TEXT DEFAULT 'local'",
}

_AFTER_MIGRATION = ["CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_id, updated_at)"]


# ---------- Backends ----------

class _SQLiteBackend:
    """A local SQLite file. A fresh connection per call - cheap, and safe across threads."""

    _initialized: set[str] = set()
    _init_lock = threading.Lock()

    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        resolved = str(Path(self.path).resolve())
        with self._init_lock:
            if resolved in self._initialized and Path(self.path).exists():
                return
            for statement in _SCHEMA:
                conn.execute(statement.format(autoid="INTEGER PRIMARY KEY AUTOINCREMENT"))
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            for column, definition in _ADDED_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
            for statement in _AFTER_MIGRATION:
                conn.execute(statement)
            conn.commit()
            self._initialized.add(resolved)

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            rows = conn.execute(sql, params or {}).fetchall()
            conn.commit()
            return [dict(r) for r in rows]
        finally:
            conn.close()


class _PostgresBackend:
    """
    A Postgres database (e.g. Neon) via pg8000 - pure Python, so it installs
    anywhere without compiling a driver. One shared connection guarded by a
    lock, re-opened once if the server dropped it (Neon suspends idle databases).
    """

    def __init__(self, url: str):
        self.url = url
        self._conn = None
        self._lock = threading.Lock()

    def _open(self):
        import pg8000.native

        u = urlparse(self.url)
        query = parse_qs(u.query)
        sslmode = (query.get("sslmode") or ["prefer"])[0]
        ssl_context = None
        if sslmode in ("require", "verify-ca", "verify-full") or (u.hostname or "").endswith(".neon.tech"):
            ssl_context = ssl.create_default_context()
        conn = pg8000.native.Connection(
            user=unquote(u.username or ""),
            password=unquote(u.password or ""),
            host=u.hostname or "localhost",
            port=u.port or 5432,
            database=(u.path or "/").lstrip("/") or "postgres",
            ssl_context=ssl_context,
            timeout=15,
        )
        for statement in _SCHEMA:
            conn.run(statement.format(autoid="BIGSERIAL PRIMARY KEY"))
        for column, definition in _ADDED_COLUMNS.items():
            conn.run(f"ALTER TABLE runs ADD COLUMN IF NOT EXISTS {column} {definition}")
        for statement in _AFTER_MIGRATION:
            conn.run(statement)
        return conn

    def _run(self, sql: str, params: dict) -> list[dict]:
        rows = self._conn.run(sql, **params) or []
        names = [c["name"] for c in (self._conn.columns or [])]
        return [dict(zip(names, row)) for row in rows] if names else []

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        params = params or {}
        with self._lock:
            if self._conn is None:
                self._conn = self._open()
            try:
                return self._run(sql, params)
            except Exception as e:
                # A dropped connection (idle timeout, network blip) - reconnect
                # and retry once. SQL errors would just fail again and raise.
                logger.warning(f"Postgres query failed ({e}); reconnecting once")
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = self._open()
                return self._run(sql, params)


_postgres: Optional[_PostgresBackend] = None


def _db(db_path: Optional[str] = None):
    """The backend to use: SQLite at db_path if given, else Postgres if configured, else the default SQLite file."""
    global _postgres
    if db_path:
        return _SQLiteBackend(db_path)
    url = settings.storage.database_url
    if url:
        if _postgres is None or _postgres.url != url:
            _postgres = _PostgresBackend(url)
        return _postgres
    return _SQLiteBackend(settings.storage.db_path)


def backend_name() -> str:
    """"postgres" or "sqlite" - for the health endpoint / startup log."""
    return "postgres" if settings.storage.database_url else "sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Runs ----------

def save_run(
    run_id: str,
    state,
    status: str,
    db_path: Optional[str] = None,
    user_id: str = LOCAL_USER_ID,
) -> None:
    """
    Insert or update a snapshot of the given run.

    created_at and user_id are only written on insert - a run's owner never changes.
    """
    now = _now()
    _db(db_path).query(
        """
        INSERT INTO runs (id, category, topic, status, research_content, research_sources,
                          blog_draft, blog_final, grounding_notes, grounding_fixes,
                          iteration_count, max_iterations, created_at, updated_at, user_id)
        VALUES (:id, :category, :topic, :status, :research_content, :research_sources,
                :blog_draft, :blog_final, :grounding_notes, :grounding_fixes,
                :iteration_count, :max_iterations, :created_at, :updated_at, :user_id)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            research_content=excluded.research_content,
            research_sources=excluded.research_sources,
            blog_draft=excluded.blog_draft,
            blog_final=excluded.blog_final,
            grounding_notes=excluded.grounding_notes,
            grounding_fixes=excluded.grounding_fixes,
            iteration_count=excluded.iteration_count,
            max_iterations=excluded.max_iterations,
            updated_at=excluded.updated_at
        """,
        {
            "id": run_id,
            "category": state.category,
            "topic": state.topic,
            "status": status,
            "research_content": state.research_content,
            "research_sources": json.dumps(state.research_sources),
            "blog_draft": state.blog_draft,
            "blog_final": state.blog_final,
            "grounding_notes": getattr(state, "grounding_notes", ""),
            "grounding_fixes": json.dumps(getattr(state, "grounding_fixes", [])),
            "iteration_count": state.iteration_count,
            "max_iterations": state.max_iterations,
            "created_at": now,
            "updated_at": now,
            "user_id": user_id,
        },
    )


def list_runs(limit: int = 50, db_path: Optional[str] = None, user_id: Optional[str] = None) -> list[dict]:
    """
    Most-recently-updated runs first, without the large text fields.

    Args:
        limit: Max runs to return
        db_path: Optional SQLite path override (tests)
        user_id: If given, only that user's runs; None lists everyone's
    """
    where = "WHERE user_id = :user_id" if user_id is not None else ""
    params = {"limit": limit, **({"user_id": user_id} if user_id is not None else {})}
    return _db(db_path).query(
        f"""
        SELECT id, category, topic, status, iteration_count, max_iterations,
               created_at, updated_at, user_id
        FROM runs {where} ORDER BY updated_at DESC LIMIT :limit
        """,
        params,
    )


def get_run(run_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Full stored record for one run, or None if it was never saved."""
    rows = _db(db_path).query("SELECT * FROM runs WHERE id = :id", {"id": run_id})
    if not rows:
        return None
    record = rows[0]
    record["research_sources"] = json.loads(record["research_sources"] or "[]")
    record["grounding_fixes"] = json.loads(record.get("grounding_fixes") or "[]")
    return record


# ---------- Users ----------

def upsert_user(
    user_id: str,
    provider: str,
    name: str = "",
    email: str = "",
    avatar_url: str = "",
    db_path: Optional[str] = None,
) -> None:
    """Record a signed-in user, refreshing their profile and last-login time."""
    now = _now()
    _db(db_path).query(
        """
        INSERT INTO users (id, provider, name, email, avatar_url, created_at, last_login_at)
        VALUES (:id, :provider, :name, :email, :avatar_url, :now, :now)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            email=excluded.email,
            avatar_url=excluded.avatar_url,
            last_login_at=excluded.last_login_at
        """,
        {"id": user_id, "provider": provider, "name": name, "email": email, "avatar_url": avatar_url, "now": now},
    )


def get_user(user_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    rows = _db(db_path).query("SELECT * FROM users WHERE id = :id", {"id": user_id})
    return rows[0] if rows else None


# ---------- Session thread ----------

# role is who "said" it in the thread: "user" (topic, feedback, approval) or
# "assistant" (drafts, errors). kind is what it was.
EVENT_KINDS = {"topic", "draft", "feedback", "reresearch", "approved", "error"}


def add_event(run_id: str, role: str, kind: str, data: Optional[dict] = None, db_path: Optional[str] = None) -> None:
    """Append one entry to a run's session thread."""
    if kind not in EVENT_KINDS:
        raise ValueError(f"Unknown event kind: {kind}")
    _db(db_path).query(
        "INSERT INTO run_events (run_id, role, kind, data, created_at) VALUES (:run_id, :role, :kind, :data, :created_at)",
        {"run_id": run_id, "role": role, "kind": kind, "data": json.dumps(data or {}), "created_at": _now()},
    )


def get_events(run_id: str, db_path: Optional[str] = None) -> list[dict]:
    """A run's session thread, oldest first."""
    rows = _db(db_path).query(
        "SELECT role, kind, data, created_at FROM run_events WHERE run_id = :run_id ORDER BY id",
        {"run_id": run_id},
    )
    return [
        {"role": r["role"], "kind": r["kind"], "data": json.loads(r["data"] or "{}"), "created_at": r["created_at"]}
        for r in rows
    ]


def draft_event_data(state) -> dict:
    """What a "draft" thread entry records about the state the pipeline just produced."""
    return {
        "iteration": state.iteration_count,
        "blog": state.blog_final or state.blog_draft,
        "sources": list(state.research_sources),
        "grounding_notes": state.grounding_notes,
        "grounding_fixes": list(state.grounding_fixes),
    }
