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
