"""
Storage - Lightweight SQLite persistence for run history.

A run is snapshotted here at meaningful checkpoints (draft ready for human
review, finished, or errored) so past work survives a server/CLI restart and
can be browsed later. This is not the source of truth for an in-progress run
- the in-memory AgentState is authoritative while a run is active; this is
purely a record of what happened.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import settings

_SCHEMA = """
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
);
"""


@contextmanager
def _connect(db_path: Optional[str] = None):
    path = Path(db_path or settings.storage.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_run(run_id: str, state, status: str, db_path: Optional[str] = None) -> None:
    """Insert or update a snapshot of the given run."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        existing = conn.execute("SELECT created_at FROM runs WHERE id = ?", (run_id,)).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO runs (id, category, topic, status, research_content, research_sources,
                               blog_draft, blog_final, grounding_notes, iteration_count,
                               max_iterations, created_at, updated_at)
            VALUES (:id, :category, :topic, :status, :research_content, :research_sources,
                    :blog_draft, :blog_final, :grounding_notes, :iteration_count,
                    :max_iterations, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                research_content=excluded.research_content,
                research_sources=excluded.research_sources,
                blog_draft=excluded.blog_draft,
                blog_final=excluded.blog_final,
                grounding_notes=excluded.grounding_notes,
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
                "iteration_count": state.iteration_count,
                "max_iterations": state.max_iterations,
                "created_at": created_at,
                "updated_at": now,
            },
        )


def list_runs(limit: int = 50, db_path: Optional[str] = None) -> list[dict]:
    """Most-recently-updated runs first, without the large text fields."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, category, topic, status, iteration_count, max_iterations,
                   created_at, updated_at
            FROM runs ORDER BY updated_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Full stored record for one run, or None if it was never saved."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        record = dict(row)
        record["research_sources"] = json.loads(record["research_sources"] or "[]")
        return record
