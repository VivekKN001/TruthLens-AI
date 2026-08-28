// TruthLens AI - minimal frontend. No framework, just DOM + fetch + SSE.

// Mirrors main.py's CATEGORY_EXAMPLES - keep the two in sync if changed.
const CATEGORY_EXAMPLES = {
  Science: [
    "The impact of quantum computing on cybersecurity",
    "CRISPR gene editing and its medical applications",
    "Latest breakthroughs in nuclear fusion energy",
    "How AI is transforming drug discovery",
  ],
  Politics: [
    "2024 US Presidential Election analysis",
    "India's new criminal law reforms",
    "Russia-Ukraine conflict latest developments",
    "Climate policy changes in the EU",
  ],
  Gaming: [
    "The rise of AI in competitive gaming",
    "GTA 6 release and industry expectations",
    "Cloud gaming vs traditional consoles",
    "Esports growth and mainstream acceptance",
  ],
};

const STEP_ORDER = ["researching", "writing", "editing", "verifying"];

const el = (id) => document.getElementById(id);

const screens = {
  setup: el("screen-setup"),
  progress: el("screen-progress"),
  review: el("screen-review"),
  done: el("screen-done"),
  historyDetail: el("screen-history-detail"),
};

let selectedCategory = null;
let currentRunId = null;
let activeHistoryId = null;

function showScreen(name) {
  Object.values(screens).forEach((s) => s.classList.add("hidden"));
  screens[name].classList.remove("hidden");
}

function showError(message) {
  const toast = el("error-toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 5000);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------- Sidebar ----------

function openSidebar() {
  el("sidebar").classList.add("open");
  el("sidebar-backdrop").classList.add("open");
}

function closeSidebar() {
  el("sidebar").classList.remove("open");
  el("sidebar-backdrop").classList.remove("open");
}

el("sidebar-toggle-btn").addEventListener("click", () => {
  if (el("sidebar").classList.contains("open")) closeSidebar();
  else openSidebar();
});
el("sidebar-backdrop").addEventListener("click", closeSidebar);

function setActiveHistoryItem(id) {
  activeHistoryId = id;
  document.querySelectorAll(".history-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.runId === id);
  });
}

async function loadSidebarHistory() {
  const list = el("history-list");
  const empty = el("history-empty");

  try {
    const res = await fetch("/api/history");
    const { runs } = await res.json();

    list.innerHTML = "";
    if (!runs.length) {
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");

    runs.forEach((run) => {
      const item = document.createElement("li");
      item.className = "history-item" + (run.id === activeHistoryId ? " active" : "");
      item.dataset.runId = run.id;
      item.innerHTML = `
        <span class="history-item-topic">${escapeHtml(run.topic)}</span>
        <span class="history-item-meta">${escapeHtml(run.category)} · ${escapeHtml(run.status)}</span>
      `;
      item.addEventListener("click", () => {
        openHistoryDetail(run.id);
        closeSidebar();
      });
      list.appendChild(item);
    });
  } catch (err) {
    // Sidebar history is a nice-to-have - fail quietly rather than spam a toast on every load.
    console.error("Could not load history", err);
  }
}

function startNewTopic() {
  currentRunId = null;
  selectedCategory = null;
  el("topic-input").value = "";
  document.querySelectorAll(".category-card").forEach((c) => c.classList.remove("selected"));
  el("example-chips").innerHTML = "";
  updateStartButton();
  setActiveHistoryItem(null);
  showScreen("setup");
  closeSidebar();
}

el("new-topic-sidebar-btn").addEventListener("click", startNewTopic);
el("new-topic-btn").addEventListener("click", startNewTopic);

// ---------- Setup screen ----------

function renderCategoryCards() {
  const container = el("category-cards");
  container.innerHTML = "";
  Object.keys(CATEGORY_EXAMPLES).forEach((category) => {
    const card = document.createElement("div");
    card.className = "category-card";
    card.textContent = category;
    card.addEventListener("click", () => selectCategory(category));
    container.appendChild(card);
  });
}

function selectCategory(category) {
  selectedCategory = category;
  document.querySelectorAll(".category-card").forEach((c) => {
    c.classList.toggle("selected", c.textContent === category);
  });
  renderExampleChips(category);
  updateStartButton();
}

function renderExampleChips(category) {
  const container = el("example-chips");
  container.innerHTML = "";
  (CATEGORY_EXAMPLES[category] || []).forEach((example) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = example;
    chip.addEventListener("click", () => {
      el("topic-input").value = example;
      updateStartButton();
    });
    container.appendChild(chip);
  });
}

function updateStartButton() {
  const topic = el("topic-input").value.trim();
  el("start-btn").disabled = !(selectedCategory && topic);
}

el("topic-input").addEventListener("input", updateStartButton);

el("start-btn").addEventListener("click", async () => {
  const topic = el("topic-input").value.trim();
  if (!selectedCategory || !topic) return;

  try {
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: selectedCategory, topic }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Failed to start run");
    const { run_id } = await res.json();
    currentRunId = run_id;
    setActiveHistoryItem(null);
    el("progress-topic").textContent = `${selectedCategory} · ${topic}`;
    enterProgress();
    listenForEvents(run_id);
  } catch (err) {
    showError(err.message);
  }
});

// ---------- Progress screen ----------

function enterProgress() {
  showScreen("progress");
  el("progress-title").textContent = "Working on it…";
  el("progress-error").classList.add("hidden");
  resetStreamPreview();
  STEP_ORDER.forEach((step) => {
    el(`step-${step}`).classList.remove("active", "complete");
  });
}

function setStep(status) {
  const idx = STEP_ORDER.indexOf(status);
  if (idx === -1) return;
  resetStreamPreview();
  STEP_ORDER.forEach((step, i) => {
    const li = el(`step-${step}`);
    li.classList.remove("active", "complete");
    if (i < idx) li.classList.add("complete");
    else if (i === idx) li.classList.add("active");
  });
  setPhaseVideo(status);
}

// One looping clip per phase, named to match STEP_ORDER exactly (see static/videos/).
function setPhaseVideo(phase) {
  const video = el("phase-video");
  const src = `/static/videos/${phase}.mp4`;
  if (video.getAttribute("src") !== src) {
    video.setAttribute("src", src);
    video.load();
  }
  // Autoplay can be blocked on some browsers even when muted; that's fine, the
  // dot-stepper above still shows progress either way.
  video.play().catch(() => {});
}

function resetStreamPreview() {
  const preview = el("stream-preview");
  preview.textContent = "";
  preview.classList.add("hidden");
}

function appendStreamToken(text) {
  const preview = el("stream-preview");
  preview.classList.remove("hidden");
  preview.textContent += text;
  preview.scrollTop = preview.scrollHeight;
}

function showProgressError(message) {
  el("progress-error-message").textContent = message;
  el("progress-error").classList.remove("hidden");
}

function listenForEvents(runId) {
  const source = new EventSource(`/api/runs/${runId}/events`);

  source.onmessage = (msg) => {
    const payload = JSON.parse(msg.data);

    if (payload.event === "status") {
      setStep(payload.status);
    } else if (payload.event === "token") {
      appendStreamToken(payload.text);
    } else if (payload.event === "awaiting_feedback") {
      STEP_ORDER.forEach((s) => el(`step-${s}`).classList.add("complete"));
      source.close();
      renderReview(payload);
      setActiveHistoryItem(runId);
      loadSidebarHistory();
    } else if (payload.event === "error") {
      source.close();
      showProgressError(payload.error);
    }
  };

  source.onerror = () => {
    source.close();
  };
}

el("retry-btn").addEventListener("click", async () => {
  el("progress-error").classList.add("hidden");
  try {
    const res = await fetch(`/api/runs/${currentRunId}/retry`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json()).detail || "Retry failed");
    resetStreamPreview();
    listenForEvents(currentRunId);
  } catch (err) {
    showProgressError(err.message);
  }
});

el("abandon-btn").addEventListener("click", () => {
  currentRunId = null;
  showScreen("setup");
});

// ---------- Review screen ----------

function renderReview(snapshot) {
  el("review-title").textContent = snapshot.topic;
  el("review-iteration").textContent =
    snapshot.iteration_count > 0
      ? `Revision ${snapshot.iteration_count} of ${snapshot.max_iterations}`
      : "First draft";

  el("blog-content").innerHTML = marked.parse(snapshot.blog_final || snapshot.blog_draft || "");
  el("research-content").textContent = snapshot.research_content || "No research content.";

  const sourcesList = el("sources-list");
  sourcesList.innerHTML = "";
  (snapshot.research_sources || []).forEach((url) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = url;
    a.textContent = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    li.appendChild(a);
    sourcesList.appendChild(li);
  });
  el("sources-summary").textContent = `Sources (${(snapshot.research_sources || []).length})`;

  const groundingSection = el("grounding-section");
  if (snapshot.grounding_notes) {
    el("grounding-content").textContent = snapshot.grounding_notes;
    groundingSection.classList.remove("hidden");
  } else {
    groundingSection.classList.add("hidden");
  }

  const atMax = snapshot.iteration_count >= snapshot.max_iterations;
  el("revise-btn").disabled = atMax;
  el("feedback-input").disabled = atMax;
  el("max-iterations-note").classList.toggle("hidden", !atMax);

  el("feedback-input").value = "";
  showScreen("review");
}

el("approve-btn").addEventListener("click", async () => {
  await sendFeedback({ action: "approve" });
  loadSidebarHistory();
  enterDone();
});

el("reresearch-btn").addEventListener("click", async () => {
  if (!confirm("This discards the current draft and researches the topic again. Continue?")) return;
  await sendFeedback({ action: "reresearch" });
  enterProgress();
  listenForEvents(currentRunId);
});

el("revise-btn").addEventListener("click", async () => {
  const feedback = el("feedback-input").value.trim();
  if (!feedback) {
    showError("Please describe what you'd like changed.");
    return;
  }
  await sendFeedback({ action: "revise", feedback });
  enterProgress();
  listenForEvents(currentRunId);
});

async function sendFeedback(body) {
  try {
    const res = await fetch(`/api/runs/${currentRunId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Request failed");
  } catch (err) {
    showError(err.message);
    throw err;
  }
}

// ---------- Done screen ----------

function enterDone() {
  el("download-link").href = `/api/runs/${currentRunId}/download`;
  showScreen("done");
}

// ---------- History detail (read-only, opened from the sidebar) ----------

async function openHistoryDetail(runId) {
  try {
    const res = await fetch(`/api/history/${runId}`);
    if (!res.ok) throw new Error((await res.json()).detail || "Not found");
    const record = await res.json();

    el("history-detail-title").textContent = record.topic;
    el("history-detail-meta").textContent =
      `${record.category} · ${record.status} · revision ${record.iteration_count} of ${record.max_iterations}`;
    el("history-detail-content").innerHTML = marked.parse(record.blog_final || record.blog_draft || "");

    const sourcesList = el("history-detail-sources");
    sourcesList.innerHTML = "";
    (record.research_sources || []).forEach((url) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = url;
      a.textContent = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      li.appendChild(a);
      sourcesList.appendChild(li);
    });
    el("history-detail-sources-summary").textContent = `Sources (${(record.research_sources || []).length})`;

    const groundingSection = el("history-detail-grounding-section");
    if (record.grounding_notes) {
      el("history-detail-grounding").textContent = record.grounding_notes;
      groundingSection.classList.remove("hidden");
    } else {
      groundingSection.classList.add("hidden");
    }

    el("history-detail-download").href = `/api/runs/${runId}/download`;
    setActiveHistoryItem(runId);
    showScreen("historyDetail");
  } catch (err) {
    showError("Could not load run: " + err.message);
  }
}

// ---------- Startup ----------

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const banner = el("health-banner");
    if (!data.ok) {
      banner.textContent = `Can't reach Ollama: ${data.errors.join(" ")}`;
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  } catch {
    // health check itself failing just means the server is still starting; ignore.
  }
}

renderCategoryCards();
checkHealth();
loadSidebarHistory();
