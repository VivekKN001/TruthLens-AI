"""
The Postgres backend in utils/storage.py.

The unit tests swap pg8000 for a fake connection with the same interface
(run(sql, **params) -> list of row lists, .columns -> [{"name": ...}]) backed
by in-memory SQLite, to check row mapping and reconnect behaviour without a
database server. The integration test runs the real thing against
TEST_DATABASE_URL when it's set (e.g. a Neon branch), and is skipped otherwise.
"""

import os
import sqlite3
import uuid

import pytest

from config import settings
from state import AgentState
from utils import storage


class FakePg8000Connection:
    def __init__(self):
        self._db = sqlite3.connect(":memory:")
        self.columns = None
        self.closed = False
        self.fail_next = False

    def run(self, sql, **params):
        if self.fail_next:
            self.fail_next = False
            raise ConnectionResetError("server closed the connection")
        sql = sql.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN")
        try:
            cursor = self._db.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e):  # sqlite has no ADD COLUMN IF NOT EXISTS
                return None
            raise
        self._db.commit()
        self.columns = [{"name": d[0]} for d in cursor.description] if cursor.description else None
        rows = cursor.fetchall()
        return [list(r) for r in rows] if self.columns else None

    def close(self):
        self.closed = True


@pytest.fixture
def fake_postgres(monkeypatch):
    opened = []

    def fake_open(self):
        conn = FakePg8000Connection()
        for statement in storage._SCHEMA:
            conn.run(statement.format(autoid="BIGSERIAL PRIMARY KEY"))
        for column, definition in storage._ADDED_COLUMNS.items():
            conn.run(f"ALTER TABLE runs ADD COLUMN IF NOT EXISTS {column} {definition}")
        opened.append(conn)
        return conn

    monkeypatch.setattr(storage._PostgresBackend, "_open", fake_open)
    monkeypatch.setattr(settings.storage, "database_url", "postgresql://u:p@ep-test.neon.tech/db?sslmode=require")
    monkeypatch.setattr(storage, "_postgres", None)
    return opened


def _state(**kw):
    return AgentState(category="Science", topic="Fusion", blog_final="Text", research_sources=["https://a.example"], **kw)


def test_database_url_selects_postgres(fake_postgres):
    assert storage.backend_name() == "postgres"
    assert isinstance(storage._db(), storage._PostgresBackend)
    # an explicit db_path (tests) still means SQLite
    assert isinstance(storage._db("x.db"), storage._SQLiteBackend)


def test_postgres_round_trip_maps_rows_to_dicts(fake_postgres):
    storage.save_run("r1", _state(grounding_fixes=["fixed"]), "done", user_id="alice")
    storage.add_event("r1", "user", "topic", {"topic": "Fusion"})
    storage.upsert_user("alice", "github", "Alice")

    record = storage.get_run("r1")

    assert record["user_id"] == "alice"
    assert record["research_sources"] == ["https://a.example"]
    assert record["grounding_fixes"] == ["fixed"]
    assert [r["id"] for r in storage.list_runs(user_id="alice")] == ["r1"]
    assert storage.get_events("r1")[0]["data"] == {"topic": "Fusion"}
    assert storage.get_user("alice")["name"] == "Alice"
    assert len(fake_postgres) == 1  # one shared connection


def test_postgres_reconnects_once_after_dropped_connection(fake_postgres):
    storage.save_run("r1", _state(), "done")
    fake_postgres[0].fail_next = True

    storage.add_event("r1", "user", "approved")

    assert fake_postgres[0].closed
    assert len(fake_postgres) == 2  # reopened, and the write went to the new connection
    assert storage.get_events("r1")[0]["kind"] == "approved"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to run against a real Postgres")
def test_real_postgres_round_trip(monkeypatch):
    monkeypatch.setattr(settings.storage, "database_url", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(storage, "_postgres", None)
    run_id = f"test-{uuid.uuid4().hex[:8]}"
    user_id = f"pytest:{uuid.uuid4().hex[:8]}"

    try:
        storage.save_run(run_id, _state(), "awaiting_feedback", user_id=user_id)
        storage.save_run(run_id, _state(), "done", user_id="someone-else")
        storage.add_event(run_id, "user", "topic", {"topic": "Fusion"})
        storage.upsert_user(user_id, "github", "Py Test")
        storage.upsert_user(user_id, "github", "Py Test 2")

        record = storage.get_run(run_id)
        assert record["status"] == "done" and record["user_id"] == user_id
        assert run_id in [r["id"] for r in storage.list_runs(user_id=user_id)]
        assert storage.get_events(run_id)[0]["data"] == {"topic": "Fusion"}
        assert storage.get_user(user_id)["name"] == "Py Test 2"
    finally:
        # This may be the database the deployed app uses - leave nothing behind.
        db = storage._db()
        db.query("DELETE FROM run_events WHERE run_id = :id", {"id": run_id})
        db.query("DELETE FROM runs WHERE id = :id", {"id": run_id})
        db.query("DELETE FROM users WHERE id = :id", {"id": user_id})
