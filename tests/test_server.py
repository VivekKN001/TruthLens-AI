"""Web API: per-user ownership of runs/history, sign-in requirement, and the session thread."""

import pytest
from fastapi.testclient import TestClient

import server
from auth import LOCAL_USER, current_user
from config import settings
from state import AgentState
from utils import storage

ALICE = {"id": "github:1", "name": "Alice", "email": "", "avatar_url": "", "provider": "github"}
BOB = {"id": "google:2", "name": "Bob", "email": "", "avatar_url": "", "provider": "google"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.storage, "db_path", str(tmp_path / "test.db"))

    # Never start the real (Ollama-backed) pipeline from these tests.
    async def no_pipeline(run):
        return None

    monkeypatch.setattr(server, "_run_full_workflow", no_pipeline)
    monkeypatch.setattr(server, "_run_revision", no_pipeline)
    server.RUNS.clear()
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()
    server.RUNS.clear()


def act_as(user):
    server.app.dependency_overrides[current_user] = lambda: user


def _live_run(run_id, user, status="awaiting_feedback"):
    run = server.Run(run_id, "Science", "Fusion", user_id=user["id"])
    run.state.blog_final = "Fusion is hard [1]."
    run.state.research_sources = ["https://a.example"]
    run.status = status
    server.RUNS[run_id] = run
    return run


def _stored_run(run_id, user, topic="Fusion"):
    state = AgentState(category="Science", topic=topic, blog_final="Final text", research_sources=["https://a.example"])
    storage.save_run(run_id, state, "done", user_id=user["id"])
    storage.add_event(run_id, "user", "topic", {"category": "Science", "topic": topic})
    storage.add_event(run_id, "assistant", "draft", storage.draft_event_data(state))


# ---------- Sign-in modes ----------

def test_me_in_local_mode_is_the_local_user(client):
    body = client.get("/api/me").json()
    assert body["auth_enabled"] is False
    assert body["user"]["id"] == LOCAL_USER["id"]


def test_endpoints_require_sign_in_when_nobody_is_signed_in(client):
    act_as(None)
    assert client.get("/api/history").status_code == 401
    assert client.post("/api/runs", json={"category": "Science", "topic": "x"}).status_code == 401


# ---------- Ownership ----------

def test_history_lists_only_own_runs(client):
    _stored_run("a1", ALICE, "Alice topic")
    _stored_run("b1", BOB, "Bob topic")

    act_as(ALICE)
    topics = [r["topic"] for r in client.get("/api/history").json()["runs"]]

    assert topics == ["Alice topic"]


def test_history_detail_of_someone_elses_run_is_404(client):
    _stored_run("b1", BOB)
    act_as(ALICE)
    assert client.get("/api/history/b1").status_code == 404
    assert client.get("/api/runs/b1/download").status_code == 404


def test_history_detail_includes_thread(client):
    _stored_run("a1", ALICE)
    act_as(ALICE)

    body = client.get("/api/history/a1").json()

    assert [e["kind"] for e in body["thread"]] == ["topic", "draft"]
    assert body["thread"][1]["data"]["blog"] == "Final text"


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/api/runs/r1", None),
    ("get", "/api/runs/r1/events", None),
    ("get", "/api/runs/r1/download", None),
    ("post", "/api/runs/r1/feedback", {"action": "approve"}),
    ("post", "/api/runs/r1/retry", None),
])
def test_live_run_endpoints_hide_other_users_runs(client, method, path, payload):
    _live_run("r1", BOB)
    act_as(ALICE)

    response = getattr(client, method)(path, json=payload) if payload else getattr(client, method)(path)

    assert response.status_code == 404


def test_owner_can_read_and_download_live_run(client):
    _live_run("r1", ALICE)
    act_as(ALICE)

    assert client.get("/api/runs/r1").json()["topic"] == "Fusion"
    download = client.get("/api/runs/r1/download")
    assert download.status_code == 200
    assert "1. https://a.example" in download.text


# ---------- Creating runs & the thread ----------

def test_create_run_records_owner_and_topic_event(client):
    act_as(ALICE)

    run_id = client.post("/api/runs", json={"category": "Science", "topic": "  Fusion  "}).json()["run_id"]

    assert server.RUNS[run_id].user_id == ALICE["id"]
    [event] = storage.get_events(run_id)
    assert event["kind"] == "topic" and event["data"] == {"category": "Science", "topic": "Fusion"}


@pytest.mark.parametrize("payload", [
    {"category": "Cooking", "topic": "Pasta"},
    {"category": "Science", "topic": "x" * 301},
    {"category": "Science", "topic": "   "},
])
def test_create_run_validates_input(client, payload):
    act_as(ALICE)
    assert client.post("/api/runs", json=payload).status_code == 400


def test_revise_logs_feedback_with_routing_and_blocks_double_submit(client):
    _live_run("r1", ALICE)
    act_as(ALICE)

    first = client.post("/api/runs/r1/feedback", json={"action": "revise", "feedback": "Fix the grammar"})
    second = client.post("/api/runs/r1/feedback", json={"action": "revise", "feedback": "Fix the grammar"})

    assert first.status_code == 200
    assert second.status_code == 409  # status flipped to "queued" immediately
    [event] = storage.get_events("r1")
    assert event["kind"] == "feedback"
    assert event["data"] == {"text": "Fix the grammar", "routed_to": "editor"}


def test_approve_logs_event_and_saves_owner(client):
    _live_run("r1", ALICE)
    act_as(ALICE)

    client.post("/api/runs/r1/feedback", json={"action": "approve"})

    assert storage.get_events("r1")[-1]["kind"] == "approved"
    assert storage.get_run("r1")["user_id"] == ALICE["id"]


# ---------- OAuth callback (provider faked) ----------

class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeGitHub:
    def __init__(self, fail=False):
        self.fail = fail

    async def authorize_access_token(self, request):
        if self.fail:
            raise RuntimeError("state mismatch")
        return {"access_token": "t"}

    async def get(self, path, token=None):
        if path == "user":
            return _FakeResponse({"id": 42, "login": "octo", "name": None, "avatar_url": "https://img/42"})
        return _FakeResponse([{"email": "octo@example.com", "primary": True, "verified": True}])


@pytest.fixture
def github_enabled(monkeypatch):
    monkeypatch.setattr(settings.auth, "github_client_id", "id")
    monkeypatch.setattr(settings.auth, "github_client_secret", "secret")


def test_github_callback_signs_user_in(client, github_enabled, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "_client", lambda provider: _FakeGitHub())

    response = client.get("/auth/callback/github", follow_redirects=False)
    me = client.get("/api/me").json()

    assert response.status_code in (302, 307) and response.headers["location"] == "/"
    assert me["auth_enabled"] is True
    assert me["user"] == {
        "id": "github:42", "name": "octo", "email": "octo@example.com",
        "avatar_url": "https://img/42", "provider": "github",
    }
    assert storage.get_user("github:42")["email"] == "octo@example.com"

    client.post("/auth/logout")
    assert client.get("/api/me").json()["user"] is None


def test_failed_callback_redirects_with_error_and_stays_signed_out(client, github_enabled, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "_client", lambda provider: _FakeGitHub(fail=True))

    response = client.get("/auth/callback/github", follow_redirects=False)

    assert response.headers["location"] == "/?auth_error=1"
    assert client.get("/api/me").json()["user"] is None


def test_unknown_provider_is_404(client, github_enabled):
    assert client.get("/auth/login/facebook").status_code == 404
