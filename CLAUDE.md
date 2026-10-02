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

## Deployment & backends

Three things switch on environment variables (all in `config.py`, documented in [.env.example](.env.example)):
- **LLM backend** — `ProviderConfig`: `LLM_PROVIDER=ollama` (default) builds `ChatOllama`; `gemini` / `groq` / `openai_compatible` build `langchain_openai.ChatOpenAI` against an OpenAI-compatible endpoint (`BaseAgent._create_llm()`). `PROVIDER_PRESETS` holds each hosted provider's base URL, default model, `reasoning_effort` and reply cap — Groq's is lower because its free tier is 8K tokens/min, and one request must fit under that. `Settings.validate_llm()` dispatches to `validate_ollama()` or `validate_hosted()` (which calls `GET {base}/models`; Gemini rejects a bad key with **400**, Groq with 401). `server.py`'s `/api/health` caches that result (it calls the provider) and reports `provider`/`storage`. When hosted, `AgentConfigs` per-agent temperatures still apply but their `model`/`context_window` don't.
- **Storage** — `utils/storage.py` routes to SQLite (`settings.storage.db_path`) or Postgres via pg8000 when `DATABASE_URL` is set; an explicit `db_path` argument always means SQLite (tests). All SQL uses `:named` params, which both drivers accept — keep it that way. The Postgres backend holds one connection behind a lock and reconnects once on failure (Neon suspends idle DBs). `tests/test_storage_postgres.py` covers it with a fake pg8000 connection, plus a real round-trip when `TEST_DATABASE_URL` is set.
- **Sign-in** — see Web UI below.

The [Dockerfile](Dockerfile) targets Hugging Face Spaces (uid 1000, port 7860, the `README.md` front matter sets `sdk: docker`). It must run **one** uvicorn worker: in-progress runs live in the process's `RUNS` dict. `server.py` reads `HOST`/`PORT` when run directly.

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

Constructors accept optional pre-built `llm`/`search_client`/`page_fetcher`/`app_settings` for dependency injection — this isn't just theoretical, [tests/test_agents.py](tests/test_agents.py) uses it to test all three agents against fake LLM/search clients with zero real Ollama/network calls (see Testing below).

Agent-specific notes:
- **Researcher** ([agents/researcher.py](agents/researcher.py)): runs 4 fixed DuckDuckGo queries per topic (`{topic} {category}`, `topic`, `recent research {topic}`, `latest news {topic}`), interleaves the results round-robin across queries (so the news/research angles aren't crowded out), dedupes by URL, and keeps the top `settings.search.max_sources`. It then downloads those pages in parallel via `utils/fetcher.py` (trafilatura article extraction, stdlib fallback) and swaps the search snippet for real article text on the first `settings.search.fetch_pages` that succeed — many sites 403 non-browser clients, which is why it tries all of them rather than just the top N. Sources are labelled `[1]..[N]` in the same order as `state.research_sources`, truncated to `settings.search.max_content_chars`, and the LLM synthesizes them into a structured summary that keeps the `[n]` markers. Each query is retried up to `settings.search.max_retries` times with backoff before being treated as failed (ddgs scrapes DuckDuckGo rather than calling a real API, so it's prone to transient rate-limiting); there's also a small `settings.search.query_delay` pause between the 4 queries to reduce the odds of tripping rate-limits in the first place. If every query still fails, research continues anyway with whatever the model already knows, and a warning is appended to `state.messages` rather than the run erroring out.
- **Writer** ([agents/writer.py](agents/writer.py)): one LLM call per draft/revision. Deliberately does **not** re-truncate `research_content` — the researcher already sized it to fit the context window; re-truncating here was previously starving the writer and causing hallucination/repetition (see inline comment).
- **Editor** ([agents/editor.py](agents/editor.py)): also deliberately does not truncate the draft before editing, for the same reason. Expects the LLM to return a `---EDITED BLOG POST--- / ---EDITORIAL NOTES---`-delimited response, parsed by `utils/parsers.py:parse_editor_response()`, which tries several marker formats before falling back to treating the whole response as blog content. Also owns `verify_and_fix()` / `check_grounding()` — the post-edit fact-check (see Grounding check below).

### Citations

Every `[n]` marker in research, blog and grounding notes means `state.research_sources[n - 1]` — the researcher's numbering is the single source of truth, so don't reorder or filter `research_sources` after research without renumbering. The writer/editor prompts share their citation instructions from [prompts/citation_rules.py](prompts/citation_rules.py). After every writer/editor LLM call, `BaseAgent._clean_citations()` runs [utils/citations.py](utils/citations.py)'s `clean_citations()`, which drops markers pointing past the source list and any model-written trailing "Sources"/"References" section (local models add these with invented URLs); the only source list is the real one appended by `main.py`'s save and `server.py`'s `/download`. The web UI renders markers as superscript links (`static/app.js:linkCitations()`, whose regex mirrors `CITATION_RE`).

**Math**: models write formulas as LaTeX, so `static/app.js:markdownToSafeHtml()` pulls `$$…$$`, `\[…\]`, `\(…\)` and `$…$` out into placeholder tokens *before* citation linking and `marked` (which would otherwise turn `x_1` into italics or `a[1]` into a citation), renders them with KaTeX (`trust: false`), and only then runs DOMPurify over the whole result. Inline `$…$` follows Pandoc's rule (non-space after the opening `$`, non-space before the closing `$`, no digit after it) so prices like "$5 billion and $10 billion" stay text; code spans/fences are skipped.

### Grounding check

`EditorAgent.check_grounding()` sends the finished blog post and the research data back to the LLM and asks it to flag specific claims (numbers, dates, quotes, names, and `[n]` citations whose source doesn't say that) that the research doesn't actually support. It's a lightweight LLM self-check, not a guarantee, but it's caught real cases in testing (e.g. the model inflating a research source's "thousands of lives" into "millions of lives"). The prompt asks for one `- CLAIM: "..." | PROBLEM: ... | FIX: ...` line per issue or the line `ALL CLAIMS SUPPORTED`; `utils/parsers.py:parse_grounding_issues()` parses that (falling back to treating plain bullets as issues, since small models drift from the format), and `format_grounding_notes()` turns it into the readable `state.grounding_notes`.

**Self-fixing:** both LangGraph workflows and `server.py` call `EditorAgent.verify_and_fix()`, not `check_grounding()` directly. It runs the check; if claims are flagged, `fix_grounding_issues()` asks the editor to correct only those claims (correct / soften / remove), then re-checks — up to `settings.workflow.max_fix_rounds` rounds (default 1; 0 = report only). A fix is rejected and `blog_final` left untouched if the LLM fails, returns the post unchanged, or returns under 60% of the original length (small models asked to "return the whole post" sometimes return a fragment). What was auto-corrected is recorded in `state.grounding_fixes` (reset on every verify pass, persisted in the history DB's `grounding_fixes` column — `utils/storage.py:_migrate()` adds it to older DBs), while `grounding_notes` always reflects the *last* check, i.e. what's still flagged. Between passes it streams `── Fixing … ──` / `── Re-checking … ──` lines through `on_token`, which `static/app.js:VERIFY_SUBPHASES` watches to relabel the progress screen. Both are surfaced in the CLI, the web UI (review callout + collapsible "Grounding check" section), and the exported `.md` via `utils/parsers.py:format_grounding_section()`, shared by `main.py`'s save and `server.py`'s `/download`.

### Context window sizing matters here

Because the LLM is a local Ollama model, `num_ctx` (`LLMConfig.context_window`, default 16384) and `search.max_content_chars` (default 12000) are load-bearing: Ollama silently truncates to a 2048-token window if not told otherwise, which was the root cause of past truncated/hallucinated output (see comments in [config.py](config.py) and [agents/base.py](agents/base.py)). When changing the model, consider whether it needs a different `context_window`.

### Adding a category

Science/Politics/Gaming are the three supported categories (`config.py:Category` enum). Adding a new one requires touching all of: the `Category` enum, prompt files (`prompts/researcher_prompts.py`, `prompts/writer_prompts.py`, `prompts/editor_prompts.py`), `WriterAgent.AUDIENCES`, and `main.py:CATEGORY_EXAMPLES`.

### Web UI (`server.py`, `static/`)

`server.py` is a FastAPI app that gives the same agents a browser front end. It does **not** go through `graph/workflow.py`'s compiled graphs — `_execute_steps()` runs a list of `(phase_name, agent_method)` steps directly, off the event loop via `run_in_executor`, so it can push events over SSE (`GET /api/runs/{id}/events`) as things happen. This intentionally re-sequences the same node order as the two graphs in `graph/workflow.py` (research→write→edit→verify for a fresh run; `utils/parsers.py:determine_revision_target()` routing for revisions) — if that routing logic changes, update both places.

Event types on the SSE stream: `status` (phase changed), `token` (one streamed chunk of the current phase's LLM output — see Streaming below), `awaiting_feedback` (pipeline finished, full snapshot attached), `error` (a step failed).

**Streaming**: each step gets an `on_token` callback built by `_make_token_emitter()`. Since the step itself runs in a worker thread (via `run_in_executor`) but `asyncio.Queue` isn't thread-safe, the callback uses `loop.call_soon_threadsafe(run.queue.put_nowait, ...)` to hand the chunk back to the event loop thread. The frontend just appends these into a `<pre>` box that resets whenever the phase changes (`static/app.js:setStep()`).

**Error handling / retry**: `_execute_steps()` remembers which step index it was on (`run.pending_steps`, `run.failed_step_index`) when a step raises. `POST /api/runs/{id}/retry` resumes from exactly that step rather than re-running the whole pipeline or losing the run. `_friendly_error()` gives connection-related failures (Ollama down mid-run) a clearer message than the raw exception text.

**Sign-in & ownership** ([auth.py](auth.py)): Google/GitHub OAuth via authlib, with the signed-in user kept in a signed session cookie (Starlette `SessionMiddleware`, secret from `SESSION_SECRET`). Sign-in is on only when at least one provider's client ID + secret is set (`config.py:AuthConfig`, all from env vars); otherwise every request is `auth.LOCAL_USER` (`id="local"`), which is how local use stays sign-in-free and how pre-existing/CLI runs remain visible. Every run/history endpoint takes `user = Depends(require_user)` and goes through `server.py:_get_run()` / `_get_record()`, which return **404 (not 403)** for another user's run so run IDs can't be probed — any new endpoint touching a run must do the same. `PUBLIC_URL` is needed in deployment to build OAuth callback URLs (behind a proxy the server can't see its public https address). Tests fake the signed-in user with `app.dependency_overrides[auth.current_user]` (see [tests/test_server.py](tests/test_server.py)).

**Session thread**: besides the latest snapshot, every step of a session is appended to `run_events` via `_log_event()` — `topic` (create), `draft` (each time a pipeline reaches `awaiting_feedback`, with the full blog/sources/grounding via `storage.draft_event_data()`), `feedback` (with the `routed_to` agent), `reresearch`, `approved`, `error`. `GET /api/history/{id}` returns it as `thread`; the frontend renders it as a chat (`static/app.js:renderThread()`), and `threadFromRecord()` synthesizes a minimal thread for runs saved before events existed. `main.py` logs the same events for CLI runs.

Run state lives in an in-memory `RUNS` dict keyed by `run_id` and is lost on server restart — but every meaningful checkpoint (`awaiting_feedback`, `done`, `error`) is also snapshotted to the history DB (see Persistence below), so finished/errored runs survive a restart even though in-flight ones don't. `main.py`'s CLI and `server.py`'s web UI are independent entry points into the same `agents`/`state`/`config` layer; neither depends on the other, but both call `utils/storage.py` to persist history.

### Persistence / run history (`utils/storage.py`)

A small SQLite wrapper (`data/truthlens.db` by default, path in `settings.storage.db_path`) that snapshots a run's `AgentState` + status whenever it reaches `awaiting_feedback`, `done`, or `error`. Tables: `runs` (latest snapshot, with `user_id` — written on insert only, never changed by later saves), `users` (profile upserted on each sign-in), and `run_events` (the session thread). `_migrate()` adds columns introduced after the first release (`grounding_fixes`, `user_id`) to older DBs on connect; old rows get `user_id = 'local'`. Every function (`save_run`/`list_runs`/`get_run`) takes an optional `db_path` override specifically so tests can point at a `tmp_path` DB instead of touching the real one. Both `main.py` (after each `full_workflow`/`revision_workflow` invocation) and `server.py` (inside `_execute_steps()` and the approve/error paths) call `save_run()` through a `_persist()` helper that swallows storage errors — a disk hiccup shouldn't crash a run. The web UI's `GET /api/history` and `GET /api/history/{id}` read this DB directly, and `GET /api/runs/{id}/download` falls back to it when the run isn't in the in-memory `RUNS` dict (i.e. after a restart).

### Testing (`tests/`)

Pytest suite, all mocked — no test depends on Ollama or a network connection, which is what makes `pytest` safe to run without `ollama serve` up. Key pieces:
- `tests/fakes.py` — `FakeLLM` (implements `.invoke()`/`.stream()` the way `ChatOllama` does; supports `fail_times` to simulate transient failures) and `FakeSearchClient` (implements `.text()` the way `ddgs.DDGS` does). Both get passed into agents via the constructor DI mentioned above.
- `tests/test_agents.py` — exercises `WriterAgent`/`EditorAgent`/`ResearcherAgent` behavior (streaming, fallback-on-failure, grounding check, search retry/dedup) through those fakes.
- `tests/test_server.py` — the web API via FastAPI's `TestClient`: per-user ownership on every run/history endpoint, 401s when signed out, input validation, the session thread, and the OAuth callback with a faked provider client. The real pipeline is replaced with a no-op so no LLM is touched.
- `tests/test_grounding.py` — grounding-output parsing and the `verify_and_fix()` check → fix → re-check loop, driven by `tests/fakes.py:ScriptedLLM` (a different scripted response per call).
- `tests/test_parsers.py`, `tests/test_state.py`, `tests/test_retry.py`, `tests/test_storage.py`, `tests/test_citations.py`, `tests/test_fetcher.py` — pure-logic and storage round-trip tests.
- `tests/conftest.py` has autouse fixtures that no-op `time.sleep` everywhere (so retry/backoff paths don't slow the suite down) and make `requests.get` raise (the researcher fetches pages by default; tests inject a `page_fetcher` instead — `FakeLLM.prompts` records what each call was sent, for asserting on prompt contents).

`pytest.ini` sets `pythonpath = .` so `from agents import ...`-style imports (matching how the app itself imports) work without installing the package.

### Feedback routing keywords

`utils/parsers.py`'s `writing_keywords`/`editing_keywords` lists are what `main.py`'s free-text human feedback gets matched against to decide writer-vs-editor revision. If revisions are routing to the wrong agent, this is the first place to check — it's plain substring matching on the lowercased feedback string, not an LLM classification.
