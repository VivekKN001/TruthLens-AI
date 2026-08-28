"""
Web Server - FastAPI backend for the TruthLens AI browser UI.

This calls the same agents used by main.py's CLI, but invokes them one phase
at a time (mirroring graph/workflow.py's node sequence) instead of through
the compiled LangGraph, so it can:
  - stream each phase's LLM output token-by-token to the browser over SSE
  - report a friendly error and let the browser retry just the failed phase,
    instead of losing the whole run
  - snapshot progress to the history DB (utils/storage.py) as it goes
"""

import asyncio
import functools
import json
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents import EditorAgent, ResearcherAgent, WriterAgent
from config import Category, settings
from state import AgentState
from utils import storage
from utils.logger import get_logger
from utils.parsers import determine_revision_target

logger = get_logger(__name__)

app = FastAPI(title="TruthLens AI")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One shared instance per agent type - each just wraps a stateless LLM/search
# client, so it's safe to reuse across concurrent runs instead of recreating
# the Ollama client on every request.
researcher = ResearcherAgent()
writer = WriterAgent()
editor = EditorAgent()

# A pipeline step: (phase name shown to the user, agent method taking (state, on_token=...))
Step = Tuple[str, Callable[..., AgentState]]


class Run:
    """In-memory state + event queue for a single browser session's workflow run."""

    def __init__(self, run_id: str, category: str, topic: str):
        self.id = run_id
        self.state = AgentState(
            category=category,
            topic=topic,
            iteration_count=0,
            max_iterations=settings.workflow.max_iterations,
        )
        self.status = "queued"
        self.error: Optional[str] = None
        self.queue: "asyncio.Queue[dict]" = asyncio.Queue()
        # If a step fails, these let /retry resume from that exact step
        # instead of re-running the whole pipeline (or losing the run).
        self.pending_steps: Optional[List[Step]] = None
        self.failed_step_index: int = 0

    async def emit(self, event: str, data: Optional[dict] = None) -> None:
        await self.queue.put({"event": event, **(data or {})})


RUNS: dict[str, Run] = {}


class CreateRunRequest(BaseModel):
    category: str
    topic: str


class FeedbackRequest(BaseModel):
    action: str  # "approve" | "reresearch" | "revise"
    feedback: str = ""


def _get_run(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


def _snapshot(run: Run) -> dict:
    s = run.state
    return {
        "run_id": run.id,
        "status": run.status,
        "error": run.error,
        "category": s.category,
        "topic": s.topic,
        "research_content": s.research_content,
        "research_sources": s.research_sources,
        "blog_draft": s.blog_draft,
        "blog_final": s.blog_final,
        "grounding_notes": s.grounding_notes,
        "iteration_count": s.iteration_count,
        "max_iterations": s.max_iterations,
    }


def _persist(run: Run, status: str) -> None:
    """Best-effort snapshot to the history DB - a storage hiccup shouldn't break the run."""
    try:
        storage.save_run(run.id, run.state, status)
    except Exception as e:
        logger.warning(f"Could not save run history for {run.id}: {e}")


def _friendly_error(e: Exception, phase: str) -> str:
    text = str(e)
    lowered = text.lower()
    if any(kw in lowered for kw in ("connection", "connect", "timed out", "timeout")):
        return (
            f"Lost connection to Ollama while {phase}. Make sure 'ollama serve' is still running, "
            f"then retry. ({text})"
        )
    return f"Something went wrong while {phase}: {text}"


def _make_token_emitter(run: Run, loop: asyncio.AbstractEventLoop, phase: str) -> Callable[[str], None]:
    """
    Build a callback agents can call (from a worker thread, via run_in_executor)
    to push streamed text back to the browser. asyncio.Queue isn't thread-safe,
    so the put has to be scheduled back onto the event loop thread.
    """

    def on_token(text: str) -> None:
        loop.call_soon_threadsafe(run.queue.put_nowait, {"event": "token", "phase": phase, "text": text})

    return on_token


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    ok, errors = settings.validate_ollama()
    return {"ok": ok, "errors": errors, "model": settings.llm.writer.model}


@app.get("/api/categories")
async def categories():
    return {"categories": [c.value for c in Category]}


@app.post("/api/runs")
async def create_run(req: CreateRunRequest):
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(400, "Topic cannot be empty")

    run_id = uuid.uuid4().hex[:12]
    run = Run(run_id, req.category, topic)
    RUNS[run_id] = run
    asyncio.create_task(_run_full_workflow(run))
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    return _snapshot(_get_run(run_id))


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    run = _get_run(run_id)

    async def gen():
        while True:
            payload = await run.queue.get()
            yield f"data: {json.dumps(payload)}\n\n"
            if payload.get("event") in ("awaiting_feedback", "done", "error"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/feedback")
async def submit_feedback(run_id: str, req: FeedbackRequest):
    run = _get_run(run_id)
    if run.status != "awaiting_feedback":
        raise HTTPException(409, f"Run is not awaiting feedback (status: {run.status})")

    if req.action == "approve":
        run.state.human_approved = True
        run.status = "done"
        _persist(run, "done")
        await run.emit("done")
        return {"ok": True}

    if req.action == "reresearch":
        run.state = AgentState(
            category=run.state.category,
            topic=run.state.topic,
            iteration_count=0,
            max_iterations=run.state.max_iterations,
        )
        asyncio.create_task(_run_full_workflow(run))
        return {"ok": True}

    if req.action == "revise":
        feedback = req.feedback.strip()
        if not feedback:
            raise HTTPException(400, "Feedback text is required")
        if run.state.iteration_count >= run.state.max_iterations:
            raise HTTPException(409, "Maximum revision iterations reached")
        run.state.human_feedback = feedback
        run.state.human_approved = False
        asyncio.create_task(_run_revision(run))
        return {"ok": True}

    raise HTTPException(400, f"Unknown action: {req.action}")


@app.post("/api/runs/{run_id}/retry")
async def retry_run(run_id: str):
    """Resume a run from the exact phase that failed, instead of starting over."""
    run = _get_run(run_id)
    if run.status != "error" or run.pending_steps is None:
        raise HTTPException(409, "This run isn't in a retryable error state")

    steps, start_index = run.pending_steps, run.failed_step_index
    asyncio.create_task(_execute_steps(run, steps, start_index))
    return {"ok": True}


@app.get("/api/history")
async def get_history(limit: int = 50):
    return {"runs": storage.list_runs(limit=limit)}


@app.get("/api/history/{run_id}")
async def get_history_run(run_id: str):
    record = storage.get_run(run_id)
    if not record:
        raise HTTPException(404, "No history record for this run")
    return record


@app.get("/api/runs/{run_id}/download")
async def download_run(run_id: str):
    """Serves a live run from memory if present, else falls back to the history DB
    (so a run can still be downloaded after a server restart)."""
    run = RUNS.get(run_id)
    if run:
        s = run.state
        topic, category = s.topic, s.category
        blog_final, sources, grounding_notes = s.blog_final, s.research_sources, s.grounding_notes
    else:
        record = storage.get_run(run_id)
        if not record:
            raise HTTPException(404, "Run not found")
        topic, category = record["topic"], record["category"]
        blog_final, sources, grounding_notes = record["blog_final"], record["research_sources"], record["grounding_notes"]

    if not blog_final:
        raise HTTPException(404, "No finished blog for this run yet")

    content = f"# {topic}\n\n**Category:** {category}\n\n---\n\n{blog_final}\n\n---\n\n## Sources\n\n"
    content += "\n".join(f"{i}. {src}" for i, src in enumerate(sources, 1))
    if grounding_notes:
        content += f"\n\n## Grounding Check\n\n{grounding_notes}"
    filename = topic[:40].strip().replace(" ", "_") or "blog"

    return PlainTextResponse(
        content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
    )


async def _execute_steps(run: Run, steps: List[Step], start_index: int = 0) -> None:
    """
    Run a sequence of (phase_name, agent_method) steps against run.state,
    reporting status/token events as it goes. On failure, remembers exactly
    where it stopped so /retry can resume there rather than from scratch.
    """
    loop = asyncio.get_event_loop()
    run.pending_steps = steps

    for i in range(start_index, len(steps)):
        phase, func = steps[i]
        run.failed_step_index = i
        run.status = phase
        run.error = None
        await run.emit("status", {"status": phase})

        try:
            on_token = _make_token_emitter(run, loop, phase)
            run.state = await loop.run_in_executor(None, functools.partial(func, run.state, on_token=on_token))
        except Exception as e:
            logger.error(f"Run {run.id} failed during '{phase}': {e}")
            run.status = "error"
            run.error = _friendly_error(e, phase)
            _persist(run, "error")
            await run.emit("error", {"error": run.error, "phase": phase, "retryable": True})
            return

    run.status = "awaiting_feedback"
    run.pending_steps = None
    _persist(run, "awaiting_feedback")
    await run.emit("awaiting_feedback", _snapshot(run))


async def _run_full_workflow(run: Run) -> None:
    steps: List[Step] = [
        ("researching", researcher.research),
        ("writing", writer.write_blog),
        ("editing", editor.edit_blog),
        ("verifying", editor.check_grounding),
    ]
    await _execute_steps(run, steps)


async def _run_revision(run: Run) -> None:
    target = determine_revision_target(run.state.human_feedback)

    if target == "writer_revise":
        steps: List[Step] = [
            ("writing", writer.rewrite_with_feedback),
            ("editing", editor.edit_blog),
            ("verifying", editor.check_grounding),
        ]
    else:
        steps = [
            ("editing", editor.rewrite_with_feedback),
            ("verifying", editor.check_grounding),
        ]

    await _execute_steps(run, steps)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
