from state import AgentState
from utils import storage


def _make_state(**overrides):
    defaults = dict(
        category="Science",
        topic="Test Topic",
        research_content="some research",
        research_sources=["https://example.com/a", "https://example.com/b"],
        blog_draft="draft text",
        blog_final="final text",
        grounding_notes="all good",
        iteration_count=1,
        max_iterations=3,
    )
    defaults.update(overrides)
    return AgentState(**defaults)


def test_save_and_get_run_round_trip(tmp_path):
    db_path = str(tmp_path / "test.db")
    state = _make_state()

    storage.save_run("run-1", state, "awaiting_feedback", db_path=db_path)
    record = storage.get_run("run-1", db_path=db_path)

    assert record is not None
    assert record["topic"] == "Test Topic"
    assert record["status"] == "awaiting_feedback"
    assert record["research_sources"] == ["https://example.com/a", "https://example.com/b"]
    assert record["blog_final"] == "final text"


def test_get_run_returns_none_for_unknown_id(tmp_path):
    db_path = str(tmp_path / "test.db")
    assert storage.get_run("does-not-exist", db_path=db_path) is None


def test_save_run_upserts_existing_record(tmp_path):
    db_path = str(tmp_path / "test.db")
    state = _make_state(blog_final="v1")
    storage.save_run("run-1", state, "awaiting_feedback", db_path=db_path)

    state.blog_final = "v2"
    storage.save_run("run-1", state, "done", db_path=db_path)

    record = storage.get_run("run-1", db_path=db_path)
    assert record["blog_final"] == "v2"
    assert record["status"] == "done"

    # Upsert should update the one row, not add a second.
    assert len(storage.list_runs(db_path=db_path)) == 1


def test_list_runs_returns_all_saved_runs(tmp_path):
    db_path = str(tmp_path / "test.db")
    storage.save_run("run-a", _make_state(topic="A"), "done", db_path=db_path)
    storage.save_run("run-b", _make_state(topic="B"), "done", db_path=db_path)

    ids = {r["id"] for r in storage.list_runs(db_path=db_path)}

    assert ids == {"run-a", "run-b"}


def test_grounding_fixes_round_trip(tmp_path):
    db = str(tmp_path / "runs.db")
    storage.save_run("r1", _make_state(grounding_fixes=['"50%" — research says 1.5%']), "done", db_path=db)

    record = storage.get_run("r1", db_path=db)

    assert record["grounding_fixes"] == ['"50%" — research says 1.5%']


def test_old_database_without_grounding_fixes_column_is_migrated(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE runs (id TEXT PRIMARY KEY, category TEXT NOT NULL, topic TEXT NOT NULL,
           status TEXT NOT NULL, research_content TEXT DEFAULT '', research_sources TEXT DEFAULT '[]',
           blog_draft TEXT DEFAULT '', blog_final TEXT DEFAULT '', grounding_notes TEXT DEFAULT '',
           iteration_count INTEGER DEFAULT 0, max_iterations INTEGER DEFAULT 3,
           created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO runs (id, category, topic, status, created_at, updated_at) VALUES ('old', 'Science', 'T', 'done', 'x', 'x')"
    )
    conn.commit()
    conn.close()

    old = storage.get_run("old", db_path=str(db))
    storage.save_run("new", _make_state(grounding_fixes=["fixed"]), "done", db_path=str(db))

    assert old["grounding_fixes"] == []
    assert storage.get_run("new", db_path=str(db))["grounding_fixes"] == ["fixed"]


def test_list_runs_filters_by_user(tmp_path):
    db = str(tmp_path / "runs.db")
    storage.save_run("a", _make_state(topic="A"), "done", db_path=db, user_id="alice")
    storage.save_run("b", _make_state(topic="B"), "done", db_path=db, user_id="bob")

    assert [r["topic"] for r in storage.list_runs(db_path=db, user_id="alice")] == ["A"]
    assert len(storage.list_runs(db_path=db)) == 2


def test_save_run_never_changes_owner(tmp_path):
    db = str(tmp_path / "runs.db")
    storage.save_run("a", _make_state(), "awaiting_feedback", db_path=db, user_id="alice")
    storage.save_run("a", _make_state(), "done", db_path=db, user_id="mallory")

    assert storage.get_run("a", db_path=db)["user_id"] == "alice"


def test_runs_saved_without_user_belong_to_local(tmp_path):
    db = str(tmp_path / "runs.db")
    storage.save_run("cli", _make_state(), "done", db_path=db)
    assert storage.get_run("cli", db_path=db)["user_id"] == storage.LOCAL_USER_ID


def test_events_round_trip_in_order(tmp_path):
    db = str(tmp_path / "runs.db")
    storage.add_event("r", "user", "topic", {"topic": "T"}, db_path=db)
    storage.add_event("r", "assistant", "draft", {"blog": "B"}, db_path=db)
    storage.add_event("other", "user", "approved", db_path=db)

    events = storage.get_events("r", db_path=db)

    assert [(e["role"], e["kind"]) for e in events] == [("user", "topic"), ("assistant", "draft")]
    assert events[1]["data"] == {"blog": "B"}


def test_add_event_rejects_unknown_kind(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        storage.add_event("r", "user", "hacked", db_path=str(tmp_path / "runs.db"))


def test_upsert_user_updates_profile(tmp_path):
    db = str(tmp_path / "runs.db")
    storage.upsert_user("github:1", "github", "Old Name", db_path=db)
    storage.upsert_user("github:1", "github", "New Name", "a@b.c", db_path=db)

    user = storage.get_user("github:1", db_path=db)

    assert user["name"] == "New Name" and user["email"] == "a@b.c"
