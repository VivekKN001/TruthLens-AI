# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TruthLens AI is a LangGraph multi-agent system (CLI + web UI) that researches a topic, writes a blog post, edits it, and fact-checks it against its own research, with a human-in-the-loop feedback/revision cycle. Everything runs locally and free: **Ollama** for the LLM (no API key, no rate limit) and **DuckDuckGo (via `ddgs`)** for web search (no API key, no quota). `README.md` covers the user-facing overview; this file covers the architecture.

## Running the system

There's no linter or build step configured in this repo. Tests use pytest.

```bash
# one-time setup
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt   # includes pytest

# Ollama must be running locally with the configured model pulled
ollama serve
ollama pull llama3.1:8b   # or whatever config.py's AgentConfigs.model is set to

# run the interactive CLI
python main.py

# or run the browser UI (FastAPI backend + static frontend)
python server.py                              # http://127.0.0.1:8000
# or: uvicorn server:app --reload --port 8000

# run tests (all mocked - no real Ollama/network calls, runs in well under a second)
pytest
pytest tests/test_agents.py -v   # single file
pytest -k determine_revision_target   # single test by name
```

`main.py` validates Ollama connectivity/model availability on startup (`Settings.validate_ollama`) and exits with setup instructions if it can't reach `http://localhost:11434` or the model isn't pulled. If switching to a smaller/different model, update the model in `config.py` (`LLMConfig.model`, default `llama3.1:8b`) and re-pull it in Ollama.

## Architecture

### State-driven LangGraph pipeline

All data flows through a single Pydantic model, `AgentState` ([state.py](state.py)), passed node-to-node and mutated in place (`Config.frozen = False`). Every agent method takes `state: AgentState` and returns the mutated `state`. There are two separate compiled graphs in [graph/workflow.py](graph/workflow.py):

- **`create_workflow()`** — the initial linear run: `research -> write -> edit -> grounding_check -> END`. Used once per topic (and again in full on "reresearch").
- **`create_revision_workflow()`** — used for every human-feedback round after that. It does **not** re-research; it routes feedback to either the writer or editor node via `utils/parsers.py:determine_revision_target()`, which does keyword matching (not an LLM call) against `writing_keywords` vs `editing_keywords`. Ties/no-match default to `editor_revise`. Writer revisions get one additional `final_edit` pass by the editor before ending; both paths converge on a `grounding_check` node before `END`.

Both `create_workflow()`/`create_revision_workflow()` accept an optional `on_token` callback threaded to every node — see Streaming below.

[main.py](main.py) owns the outer loop: it invokes the full workflow once, then loops invoking the revision workflow based on parsed human input (`approve`/`reresearch`/free-text feedback), up to `settings.workflow.max_iterations` (default 3).

### Agents (`agents/`)

All three agents (`ResearcherAgent`, `WriterAgent`, `EditorAgent`) extend `BaseAgent` ([agents/base.py](agents/base.py)), which handles:
- Building the `ChatOllama` client from `LLMConfig` (model/temperature/`num_predict`/`num_ctx`) — each agent gets its own temperature via `AgentConfigs` in [config.py](config.py) (researcher 0.2, writer 0.7, editor 0.3).
- `get_system_prompt(category)` — looks up a category-specific prompt from the dict each agent's `_load_prompts()` returns, falling back to `"default"`.
- `_invoke_llm` / `_invoke_llm_safe` — non-streaming LLM calls, wrapped in `utils/retry.py`'s exponential-backoff retry decorator; `_safe` swallows the final failure and returns a caller-supplied fallback string instead of raising.
- `_invoke_llm_stream` / `_invoke_llm_stream_safe` — the streaming equivalents, used by every agent method that produces user-facing text. Retries the *whole* generation on failure (a failed stream can't resume mid-token), discarding any partial output already sent to `on_token` before the retry. Every agent method that calls one of these takes an `on_token: Optional[Callable[[str], None]] = None` parameter — it's `None` almost everywhere except `server.py`, which is the reason streaming exists at all (see below).

Constructors accept optional pre-built `llm`/`search_client`/`app_settings` for dependency injection — this isn't just theoretical, [tests/test_agents.py](tests/test_agents.py) uses it to test all three agents against fake LLM/search clients with zero real Ollama/network calls (see Testing below).

Agent-specific notes:
- **Researcher** ([agents/researcher.py](agents/researcher.py)): runs 4 fixed DuckDuckGo queries per topic (`{topic} {category}`, `topic`, `recent research {topic}`, `latest news {topic}`), dedupes by URL, truncates raw results to `settings.search.max_content_chars`, then has the LLM synthesize them into a structured summary. Each query is retried up to `settings.search.max_retries` times with backoff before being treated as failed (ddgs scrapes DuckDuckGo rather than calling a real API, so it's prone to transient rate-limiting); there's also a small `settings.search.query_delay` pause between the 4 queries to reduce the odds of tripping rate-limits in the first place. If every query still fails, research continues anyway with whatever the model already knows, and a warning is appended to `state.messages` rather than the run erroring out.
- **Writer** ([agents/writer.py](agents/writer.py)): one LLM call per draft/revision. Deliberately does **not** re-truncate `research_content` — the researcher already sized it to fit the context window; re-truncating here was previously starving the writer and causing hallucination/repetition (see inline comment).
- **Editor** ([agents/editor.py](agents/editor.py)): also deliberately does not truncate the draft before editing, for the same reason. Expects the LLM to return a `---EDITED BLOG POST--- / ---EDITORIAL NOTES---`-delimited response, parsed by `utils/parsers.py:parse_editor_response()`, which tries several marker formats before falling back to treating the whole response as blog content. Also owns `check_grounding()` — a post-edit pass (see Grounding check below).

### Grounding check

`EditorAgent.check_grounding()` sends the finished blog post and the research data back to the LLM and asks it to flag specific claims (numbers, dates, quotes, names) that the research doesn't actually support, storing the result in `state.grounding_notes`. It's a lightweight LLM self-check, not a guarantee, but it's caught real cases in testing (e.g. the model inflating a research source's "thousands of lives" into "millions of lives"). It runs as the last node in both LangGraph workflows, so it re-runs after every revision too. Surfaced in the CLI (`main.py` prints it if non-empty) and the web UI (collapsible "Grounding check" section) and included in both the CLI's saved `.md` file and the web UI's `/download` endpoint.

### Context window sizing matters here

Because the LLM is a local Ollama model, `num_ctx` (`LLMConfig.context_window`, default 16384) and `search.max_content_chars` (default 12000) are load-bearing: Ollama silently truncates to a 2048-token window if not told otherwise, which was the root cause of past truncated/hallucinated output (see comments in [config.py](config.py) and [agents/base.py](agents/base.py)). When changing the model, consider whether it needs a different `context_window`.

### Adding a category

Science/Politics/Gaming are the three supported categories (`config.py:Category` enum). Adding a new one requires touching all of: the `Category` enum, prompt files (`prompts/researcher_prompts.py`, `prompts/writer_prompts.py`, `prompts/editor_prompts.py`), `WriterAgent.AUDIENCES`, and `main.py:CATEGORY_EXAMPLES`.

### Web UI (`server.py`, `static/`)

`server.py` is a FastAPI app that gives the same agents a browser front end. It does **not** go through `graph/workflow.py`'s compiled graphs — `_execute_steps()` runs a list of `(phase_name, agent_method)` steps directly, off the event loop via `run_in_executor`, so it can push events over SSE (`GET /api/runs/{id}/events`) as things happen. This intentionally re-sequences the same node order as the two graphs in `graph/workflow.py` (research→write→edit→verify for a fresh run; `utils/parsers.py:determine_revision_target()` routing for revisions) — if that routing logic changes, update both places.

Event types on the SSE stream: `status` (phase changed), `token` (one streamed chunk of the current phase's LLM output — see Streaming below), `awaiting_feedback` (pipeline finished, full snapshot attached), `error` (a step failed).

**Streaming**: each step gets an `on_token` callback built by `_make_token_emitter()`. Since the step itself runs in a worker thread (via `run_in_executor`) but `asyncio.Queue` isn't thread-safe, the callback uses `loop.call_soon_threadsafe(run.queue.put_nowait, ...)` to hand the chunk back to the event loop thread. The frontend just appends these into a `<pre>` box that resets whenever the phase changes (`static/app.js:setStep()`).

**Error handling / retry**: `_execute_steps()` remembers which step index it was on (`run.pending_steps`, `run.failed_step_index`) when a step raises. `POST /api/runs/{id}/retry` resumes from exactly that step rather than re-running the whole pipeline or losing the run. `_friendly_error()` gives connection-related failures (Ollama down mid-run) a clearer message than the raw exception text.

Run state lives in an in-memory `RUNS` dict keyed by `run_id` and is lost on server restart — but every meaningful checkpoint (`awaiting_feedback`, `done`, `error`) is also snapshotted to the history DB (see Persistence below), so finished/errored runs survive a restart even though in-flight ones don't. `main.py`'s CLI and `server.py`'s web UI are independent entry points into the same `agents`/`state`/`config` layer; neither depends on the other, but both call `utils/storage.py` to persist history.

### Persistence / run history (`utils/storage.py`)

A small SQLite wrapper (`data/truthlens.db` by default, path in `settings.storage.db_path`) that snapshots a run's `AgentState` + status whenever it reaches `awaiting_feedback`, `done`, or `error`. Every function (`save_run`/`list_runs`/`get_run`) takes an optional `db_path` override specifically so tests can point at a `tmp_path` DB instead of touching the real one. Both `main.py` (after each `full_workflow`/`revision_workflow` invocation) and `server.py` (inside `_execute_steps()` and the approve/error paths) call `save_run()` through a `_persist()` helper that swallows storage errors — a disk hiccup shouldn't crash a run. The web UI's `GET /api/history` and `GET /api/history/{id}` read this DB directly, and `GET /api/runs/{id}/download` falls back to it when the run isn't in the in-memory `RUNS` dict (i.e. after a restart).

### Testing (`tests/`)

Pytest suite, all mocked — no test depends on Ollama or a network connection, which is what makes `pytest` safe to run without `ollama serve` up. Key pieces:
- `tests/fakes.py` — `FakeLLM` (implements `.invoke()`/`.stream()` the way `ChatOllama` does; supports `fail_times` to simulate transient failures) and `FakeSearchClient` (implements `.text()` the way `ddgs.DDGS` does). Both get passed into agents via the constructor DI mentioned above.
- `tests/test_agents.py` — exercises `WriterAgent`/`EditorAgent`/`ResearcherAgent` behavior (streaming, fallback-on-failure, grounding check, search retry/dedup) through those fakes.
- `tests/test_parsers.py`, `tests/test_state.py`, `tests/test_retry.py`, `tests/test_storage.py` — pure-logic and storage round-trip tests.
- `tests/conftest.py` has an autouse fixture that no-ops `time.sleep` everywhere, so retry/backoff paths don't actually slow the suite down.

`pytest.ini` sets `pythonpath = .` so `from agents import ...`-style imports (matching how the app itself imports) work without installing the package.

### Feedback routing keywords

`utils/parsers.py`'s `writing_keywords`/`editing_keywords` lists are what `main.py`'s free-text human feedback gets matched against to decide writer-vs-editor revision. If revisions are routing to the wrong agent, this is the first place to check — it's plain substring matching on the lowercased feedback string, not an LLM classification.
