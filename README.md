---
title: TruthLens AI
emoji: 🔍
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# TruthLens AI

A local, free, multi-agent pipeline that researches a topic, writes a blog post about it, fact-checks itself, and lets you refine the result through feedback — all running on your own machine with **zero API keys and zero cost**.

Three agents built on [LangGraph](https://langchain-ai.github.io/langgraph/) hand off a topic between them:

**Researcher** → **Writer** → **Editor** → **Grounding Check** → **you**

and then loop on your feedback until you approve.

![Setup screen](docs/screenshots/setup.png)

![Progress screen with live phase animation](docs/screenshots/progress.png)

## Why it's different

- **Actually free, not "free tier."** The LLM is [Ollama](https://ollama.com) running locally (no API key, no rate limit, no bill) and web search is DuckDuckGo via `ddgs` (no API key, no quota).
- **Fact-checks — and fixes — its own output.** After editing, a dedicated grounding-check pass flags specific claims — numbers, dates, quotes, names — that the research doesn't actually support. The editor then corrects just those claims and the post is checked again, and you're shown exactly what was auto-corrected and anything still flagged, so you're not just trusting the model.
- **Two ways in.** A polished interactive CLI, and a browser UI with live token streaming, a themed loading animation per phase, run history, and one-click retry if a phase fails.
- **Human-in-the-loop by design.** Approve, ask for a re-research from scratch, or give free-text feedback — it's automatically routed to whichever agent (writer or editor) is actually relevant.

## Quick start

**Prerequisites:** Python 3.10+, and [Ollama](https://ollama.com/download) installed.

```bash
# 1. Set up a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt   # includes pytest

# 3. Start Ollama and pull the model (one-time, ~4.7GB)
ollama serve
ollama pull llama3.1:8b
```

Then run either interface:

```bash
# Interactive terminal CLI
python main.py

# Browser UI at http://127.0.0.1:8000
python server.py
```

That's it — no `.env` file is required. Only add one if your Ollama isn't on the default `localhost:11434`, or you want to change the log level (see Configuration below).

If Ollama isn't running or the model isn't pulled, both entry points fail fast with the exact command you need to run.

## How it works

1. **Research** — runs four DuckDuckGo queries per topic, deduplicates results, reads the actual article text of the top pages (not just search snippets), and has the LLM synthesize them into a structured summary with numbered sources.
2. **Write** — turns the research into a category-specific blog draft (Science / Politics / Gaming, each with its own tone and audience), citing specific facts inline as `[1]`, `[2]`… against the real source list. Citations to sources that don't exist are stripped automatically.
3. **Edit** — fact-checks the draft against the research, tightens structure, removes repetition, and polishes tone.
4. **Grounding check** — a second pass that specifically hunts for claims the research doesn't back up. Anything it flags is corrected (or removed) by the editor and re-checked before the post reaches you; whatever is still flagged after that is shown to you rather than silently published.
5. **Your turn** — approve it, ask for full re-research, or describe what to change. Feedback is routed to the writer or editor automatically based on what you're asking for, then it loops back to step 4.

Every run — the research, both drafts, the grounding notes, sources, and how many revisions it took — is saved to a local SQLite history, browsable from the sidebar in the web UI or via `GET /api/history`.

## Project layout

```
main.py              interactive CLI entry point
server.py            FastAPI web UI (streaming, retry, history)
config.py            all tunable settings (model, context window, search, etc.)
state.py             the shared state object every agent reads/writes
agents/               ResearcherAgent, WriterAgent, EditorAgent
graph/workflow.py     the two LangGraph pipelines (initial run + revisions)
prompts/              category-specific system prompts per agent
utils/                retry/backoff, response parsing, feedback routing, SQLite storage
static/               the web UI (HTML/CSS/JS, no framework, no build step)
tests/                pytest suite - fully mocked, no Ollama/network needed
```

For the full technical breakdown (why things are structured this way, what's load-bearing, what to touch when adding a category), see [CLAUDE.md](CLAUDE.md).

## Testing

```bash
pytest                              # full suite, mocked, runs in well under a second
pytest tests/test_agents.py -v      # one file
pytest -k determine_revision_target # one test
```

No test depends on Ollama or a network connection.

## Deploy for free (Hugging Face Spaces)

No Ollama needed: the deployed app uses a hosted model's free tier, a free Postgres database for history, and Google/GitHub sign-in so each person gets their own history.

**1. Get the free pieces**

| What | Where | You'll copy |
|---|---|---|
| Model API key | Gemini: [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (or Groq: [console.groq.com/keys](https://console.groq.com/keys)) | `LLM_API_KEY` |
| Postgres database | [neon.tech](https://neon.tech) → new project → *Connection string* | `DATABASE_URL` |
| Hugging Face account | [huggingface.co/join](https://huggingface.co/join) | — |

**2. Create the Space:** New Space → SDK **Docker** → *Blank* → choose Public or Private. Your app's address will be `https://<username>-<space-name>.hf.space` — that's your `PUBLIC_URL`.

**3. Register sign-in apps** (one or both), using the `PUBLIC_URL` from step 2:
- **GitHub:** Settings → Developer settings → OAuth Apps → New. Homepage URL = `PUBLIC_URL`, callback URL = `PUBLIC_URL/auth/callback/github`.
- **Google:** [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials → Create OAuth client ID (*Web application*). Authorized redirect URI = `PUBLIC_URL/auth/callback/google`. (Configure the consent screen first if asked; add yourself as a test user while it's in testing.)

**4. Add Space secrets** (Space → Settings → *Variables and secrets*), see [.env.example](.env.example):

```
LLM_PROVIDER=gemini            # or groq
LLM_API_KEY=...
DATABASE_URL=postgresql://...  # from Neon
GITHUB_CLIENT_ID=...           # and/or GOOGLE_CLIENT_ID
GITHUB_CLIENT_SECRET=...       # and/or GOOGLE_CLIENT_SECRET
SESSION_SECRET=...             # python -c "import secrets; print(secrets.token_urlsafe(48))"
PUBLIC_URL=https://<username>-<space-name>.hf.space
```

**5. Push the code** to the Space's git repo (`git remote add space https://huggingface.co/spaces/<username>/<space-name>` then `git push space main`). It builds from the [Dockerfile](Dockerfile) and starts automatically. Hugging Face stores binary files (the phase videos and screenshots) through Git LFS, so if the push is rejected for binary files, convert them first: `git lfs install` then `git lfs migrate import --include="*.mp4,*.png" --everything`, and push again. That rewrites history, so do it on a copy or branch you're happy to force-push.

Open the app at its **own address** (`PUBLIC_URL`), not the huggingface.co Space page: that page shows the app inside a frame, where Google/GitHub sign-in can't work (the app offers to open itself in a new tab if you land there).

**Free-tier limits to know about:** Groq's free tier is 8K tokens/minute and 200K tokens/day on `gpt-oss`, so expect only a handful of articles a day; Gemini's free limits are shown in your AI Studio dashboard, and free-tier prompts may be used by Google to improve its products. A free Space sleeps after ~48 hours without visitors and wakes on the next visit. DuckDuckGo throttles cloud servers more than home connections, so research may occasionally be thinner when deployed.

## Configuration

Everything tunable lives in `config.py` — model name, temperature per agent, context window, search retry/backoff behavior, max revision iterations, and the SQLite history path. Environment variables (set them in a `.env` file):

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` (local), `gemini`, `groq`, or `openai_compatible` |
| `LLM_API_KEY` | — | API key for a hosted provider |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_MAX_TOKENS` / `LLM_REASONING_EFFORT` | provider preset | Override the hosted preset (required for `openai_compatible`) |
| `DATABASE_URL` | — | Postgres URL for history (e.g. Neon); without it history is the local SQLite file |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama is running |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | Enable "Continue with Google" in the web UI |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | — | Enable "Continue with GitHub" in the web UI |
| `SESSION_SECRET` | random per start | Signs the login cookie — set a long random value when sign-in is enabled, or everyone is logged out on restart |
| `PUBLIC_URL` | — | The app's public address (e.g. `https://you-truthlens.hf.space`), used for OAuth callback URLs when deployed |

### Sign-in and personal history

With no OAuth provider configured (the default, e.g. on your own PC) the web UI needs no login and all history is yours. Configure Google and/or GitHub and visitors sign in; each person then sees only their own sessions, and opening one shows the whole conversation — the topic, every draft, your feedback and where it was routed, and the approval — with every earlier draft still readable. OAuth callback URLs to register with the provider: `<PUBLIC_URL>/auth/callback/google` and `<PUBLIC_URL>/auth/callback/github` (for local testing, `http://127.0.0.1:8000/auth/callback/...`).

Science, Politics, and Gaming are the three built-in categories. Adding a new one means touching the `Category` enum in `config.py`, the three `prompts/*.py` files, `WriterAgent.AUDIENCES`, and `main.py`'s example topics — see CLAUDE.md for details.
