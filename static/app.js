// TruthLens AI - frontend. No framework, just DOM + fetch + SSE, plus a few
// hand-rolled animation layers (constellation canvas, scroll reveal,
// scroll-driven phase videos, confetti). Everything respects prefers-reduced-motion.

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

const CATEGORY_META = {
  Science: { icon: "🔬", desc: "Breakthroughs, studies & discoveries" },
  Politics: { icon: "🏛️", desc: "Policy, elections & world affairs" },
  Gaming: { icon: "🎮", desc: "Releases, esports & the industry" },
};

// Each phrase is worded to hit utils/parsers.py's routing keywords, so the
// chip goes to the agent the label implies (writer vs editor).
const QUICK_FEEDBACK = [
  "Make the intro more engaging",
  "Shorten it",
  "Add more detail on the key findings",
  "Fix grammar and typos",
  "Improve formatting with clearer headings",
];

const STEP_ORDER = ["researching", "writing", "editing", "verifying"];

const PHASE_COPY = {
  researching: { title: "Scouring the web", label: "01 · Research" },
  writing: { title: "Drafting your article", label: "02 · Write" },
  editing: { title: "Polishing & fact-checking", label: "03 · Edit" },
  verifying: { title: "Verifying every claim", label: "04 · Verify" },
};

const STATUS_LABELS = {
  done: "Published",
  awaiting_feedback: "Needs review",
  error: "Failed",
};

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const el = (id) => document.getElementById(id);
const mainScroll = el("main-scroll");

const screens = {
  setup: el("screen-setup"),
  progress: el("screen-progress"),
  review: el("screen-review"),
  done: el("screen-done"),
  historyDetail: el("screen-history-detail"),
};
const WIDE_SCREENS = new Set(["setup", "progress"]);

let selectedCategory = null;
let currentRunId = null;
let activeHistoryId = null;
let currentMarkdown = "";
let currentSources = [];
let historyMarkdown = "";
let currentScreen = "setup";

// From GET /api/me. With sign-in disabled (local use) user is the local user.
let authInfo = { auth_enabled: false, providers: [], user: null };
const needsSignIn = () => authInfo.auth_enabled && !authInfo.user;

function showScreen(name) {
  Object.values(screens).forEach((s) => s.classList.add("hidden"));
  screens[name].classList.remove("hidden");
  currentScreen = name;
  el("app").classList.toggle("is-wide", WIDE_SCREENS.has(name));
  mainScroll.scrollTop = 0;
  if (name !== "progress") stopElapsedTimer();
  setLandingVideosActive(name === "setup");
  updateReadProgress();
}

let toastTimer = null;
function showToast(message, kind = "error") {
  const toast = el("error-toast");
  toast.textContent = message;
  toast.classList.toggle("toast-ok", kind === "ok");
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 4500);
}
const showError = (message) => showToast(message, "error");

// Also escapes quotes, since some call sites interpolate into attributes.
function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ---------- Math ($$...$$, $...$, \[...\], \(...\)) ----------
// Models write formulas as LaTeX. Math is pulled out *before* markdown and
// citation processing (which would turn x_1 into italics or a[1] into a
// citation link), rendered with KaTeX, and put back.
//
// Inline $...$ follows Pandoc's rule so prices don't become math: the opening
// $ must be followed by a non-space, the closing $ preceded by a non-space and
// not followed by a digit - "$5 billion and $10 billion" stays plain text.
const MATH_PATTERNS = [
  { re: /\$\$([\s\S]+?)\$\$/g, display: true },
  { re: /\\\[([\s\S]+?)\\\]/g, display: true },
  { re: /\\\(([\s\S]+?)\\\)/g, display: false },
  { re: /\$(?=\S)([^$\n]*?\S)\$(?!\d)/g, display: false },
];
// Plain alphanumerics: nothing markdown or the citation regex would touch.
const MATH_TOKEN = (i) => `KATEXMATHTOKEN${String.fromCharCode(65 + (i % 26))}${i}X`;

function extractMath(md) {
  const math = [];
  // Leave code alone: split out fenced blocks and inline code spans first.
  const parts = md.split(/(```[\s\S]*?```|`[^`\n]+`)/g);
  const text = parts
    .map((part, i) => {
      if (i % 2 === 1) return part; // code
      let out = part;
      MATH_PATTERNS.forEach(({ re, display }) => {
        out = out.replace(re, (match, tex) => {
          math.push({ tex: tex.trim(), display, raw: match });
          return MATH_TOKEN(math.length - 1);
        });
      });
      return out;
    })
    .join("");
  return { text, math };
}

function renderMathToken(item) {
  if (!window.katex) return escapeHtml(item.raw);
  try {
    // trust: false (the default) keeps \href/\url etc. from producing live links.
    return katex.renderToString(item.tex, { displayMode: item.display, throwOnError: false, output: "html", trust: false });
  } catch {
    return escapeHtml(item.raw);
  }
}

// The blog text comes from an LLM that was fed scraped web pages, so treat it as
// untrusted: never inject marked's HTML without DOMPurify. If either CDN script
// failed to load, fall back to plain escaped paragraphs.
function markdownToSafeHtml(md, sources = null) {
  const { text, math } = extractMath(md || "");
  const withCitations = linkCitations(text, sources);
  if (window.marked && window.DOMPurify) {
    let html = marked.parse(withCitations);
    math.forEach((item, i) => {
      html = html.split(MATH_TOKEN(i)).join(renderMathToken(item));
    });
    // KaTeX output goes through the same sanitizer as everything else.
    return DOMPurify.sanitize(html);
  }
  return (md || "")
    .split(/\n{2,}/)
    .map((para) => `<p>${escapeHtml(para).replace(/\n/g, "<br>")}</p>`)
    .join("");
}

// Mirrors utils/citations.py's CITATION_RE: [3] or [1, 4], but not a markdown
// link [3](...) or a reference definition [3]: ...
const CITATION_RE = /\[(\d+(?:\s*,\s*\d+)*)\](?![(:])/g;

// Turn the pipeline's [n] markers into superscript links to research_sources[n - 1].
// Runs before markdown parsing; the result still goes through DOMPurify.
function linkCitations(md, sources) {
  if (!sources || !sources.length) return md;
  return md.replace(CITATION_RE, (match, group) => {
    const links = group.split(/\s*,\s*/).map((num) => {
      const url = sources[Number(num) - 1];
      if (!url) return escapeHtml(num);
      let label = url;
      try {
        label = new URL(url).hostname.replace(/^www\./, "");
      } catch {
        // keep the raw URL as the tooltip
      }
      return `<a class="cite-link" href="${escapeHtml(url)}" title="[${num}] ${escapeHtml(label)}">${num}</a>`;
    });
    return `<sup class="cite">${links.join("")}</sup>`;
  });
}

function renderMarkdown(target, md, sources = null) {
  target.innerHTML = markdownToSafeHtml(md || "", sources);
  [...target.children].forEach((child, i) => child.style.setProperty("--i", Math.min(i, 16)));
  target.querySelectorAll("a[href]").forEach((a) => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
}

function textStats(md) {
  const words = (md || "").replace(/[#*_>`\-]/g, " ").split(/\s+/).filter(Boolean).length;
  return { words, minutes: Math.max(1, Math.round(words / 220)) };
}

function animateNumber(node, to) {
  if (REDUCED_MOTION) {
    node.textContent = to.toLocaleString();
    return;
  }
  const start = performance.now();
  const duration = 900;
  const tick = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    node.textContent = Math.round(to * eased).toLocaleString();
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function renderSources(listEl, urls) {
  listEl.innerHTML = "";
  (urls || []).forEach((url, i) => {
    let domain = url;
    let path = "";
    try {
      const u = new URL(url);
      domain = u.hostname.replace(/^www\./, "");
      path = (u.pathname + u.search).replace(/\/$/, "") || "/";
    } catch {
      // not a parseable URL - just show it raw
    }
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.innerHTML = `
      <span class="src-num">${String(i + 1).padStart(2, "0")}</span>
      <span class="src-favicon">${escapeHtml(domain.charAt(0).toUpperCase())}</span>
      <span class="src-text"><span class="src-domain">${escapeHtml(domain)}</span><span class="src-path">${escapeHtml(path)}</span></span>
    `;
    li.appendChild(a);
    listEl.appendChild(li);
  });
}

// The grounding prompt asks for a bullet list of unsupported claims, or a single
// line saying everything is supported - so counting bullets is a decent proxy.
function countFlaggedClaims(notes) {
  return (notes || "").split("\n").filter((line) => /^\s*([-*•]|\d+[.)])\s+\S/.test(line)).length;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  showToast("Markdown copied to clipboard", "ok");
}

function relativeTime(iso) {
  const then = new Date(iso);
  if (isNaN(then)) return "";
  const secs = (Date.now() - then.getTime()) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 7 * 86400) return `${Math.floor(secs / 86400)}d ago`;
  return then.toLocaleDateString();
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
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSidebar();
});

function setActiveHistoryItem(id) {
  activeHistoryId = id;
  document.querySelectorAll(".history-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.runId === id);
  });
}

async function loadSidebarHistory() {
  const list = el("history-list");
  const empty = el("history-empty");

  if (needsSignIn()) {
    list.innerHTML = "";
    empty.classList.add("hidden");
    return;
  }

  try {
    const res = await fetch("/api/history");
    if (handleUnauthorized(res)) return;
    const { runs } = await res.json();

    list.innerHTML = "";
    if (!runs.length) {
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");

    runs.forEach((run, i) => {
      const item = document.createElement("li");
      item.className = "history-item" + (run.id === activeHistoryId ? " active" : "");
      item.dataset.runId = run.id;
      item.style.setProperty("--i", Math.min(i, 12));
      const icon = (CATEGORY_META[run.category] || {}).icon || "📝";
      const statusLabel = STATUS_LABELS[run.status] || run.status;
      item.innerHTML = `
        <span class="history-item-icon">${icon}</span>
        <span class="history-item-topic" title="${escapeHtml(run.topic)}">${escapeHtml(run.topic)}</span>
        <span class="history-item-meta">
          <span class="status-tag s-${escapeHtml(run.status)}">${escapeHtml(statusLabel)}</span>
          · ${escapeHtml(relativeTime(run.updated_at))}
        </span>
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
  stopListening();
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
    const meta = CATEGORY_META[category] || { icon: "📝", desc: "" };
    const card = document.createElement("button");
    card.type = "button";
    card.className = "category-card";
    card.dataset.category = category;
    card.innerHTML = `
      <span class="cat-icon">${meta.icon}</span>
      <span><span class="cat-name">${escapeHtml(category)}</span><br /><span class="cat-desc">${escapeHtml(meta.desc)}</span></span>
    `;
    card.addEventListener("click", () => selectCategory(category));
    attachTilt(card, 10);
    container.appendChild(card);
  });
}

function selectCategory(category) {
  selectedCategory = category;
  document.querySelectorAll(".category-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.category === category);
  });
  renderExampleChips(category);
  updateStartButton();
  if (!el("topic-input").value.trim()) el("topic-input").focus({ preventScroll: true });
}

function renderExampleChips(category) {
  const container = el("example-chips");
  container.innerHTML = "";
  (CATEGORY_EXAMPLES[category] || []).forEach((example, i) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.style.setProperty("--i", i);
    chip.textContent = example;
    chip.addEventListener("click", () => {
      el("topic-input").value = example;
      updateStartButton();
      el("topic-input").focus({ preventScroll: true });
    });
    container.appendChild(chip);
  });
}

function updateStartButton() {
  const topic = el("topic-input").value.trim();
  el("start-btn").disabled = !(selectedCategory && topic);
  const hint = el("composer-hint");
  if (!selectedCategory) hint.textContent = "Choose a category to get started.";
  else if (!topic) hint.textContent = "Now type a topic, or tap one of the suggestions.";
  else if (needsSignIn()) hint.textContent = "You'll be asked to sign in so this session is saved to your history.";
  else hint.textContent = "Press Enter or hit Start. Research takes a minute or two.";
}

el("topic-input").addEventListener("input", updateStartButton);
el("topic-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !el("start-btn").disabled) el("start-btn").click();
});

el("start-btn").addEventListener("click", async () => {
  const topic = el("topic-input").value.trim();
  if (!selectedCategory || !topic) return;
  if (needsSignIn()) {
    openSignIn();
    return;
  }

  const btn = el("start-btn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: selectedCategory, topic }),
    });
    if (handleUnauthorized(res)) return;
    if (!res.ok) throw new Error((await res.json()).detail || "Failed to start run");
    const { run_id } = await res.json();
    currentRunId = run_id;
    setActiveHistoryItem(null);
    el("progress-topic").textContent = `${selectedCategory} · ${topic}`;
    enterProgress();
    listenForEvents(run_id);
  } catch (err) {
    showError(err.message);
  } finally {
    updateStartButton();
  }
});

// Clicking the typewriter example fills the composer with it.
const EXAMPLE_TO_CATEGORY = Object.fromEntries(
  Object.entries(CATEGORY_EXAMPLES).flatMap(([cat, list]) => list.map((ex) => [ex, cat]))
);

function startTypewriter() {
  const typer = el("typer");
  const pool = Object.keys(EXAMPLE_TO_CATEGORY).sort(() => Math.random() - 0.5);
  typer.style.cursor = "pointer";
  typer.title = "Use this topic";
  typer.addEventListener("click", () => {
    const text = typer.dataset.full;
    if (!text) return;
    selectCategory(EXAMPLE_TO_CATEGORY[text]);
    el("topic-input").value = text;
    updateStartButton();
    pulseComposer();
  });

  if (REDUCED_MOTION) {
    typer.textContent = pool[0];
    typer.dataset.full = pool[0];
    return;
  }

  let idx = 0;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  (async function loop() {
    for (;;) {
      if (currentScreen !== "setup" || document.hidden) {
        await sleep(800);
        continue;
      }
      const text = pool[idx % pool.length];
      typer.dataset.full = text;
      for (let i = 1; i <= text.length; i++) {
        typer.textContent = text.slice(0, i);
        await sleep(38 + Math.random() * 40);
      }
      await sleep(2200);
      for (let i = text.length; i >= 0; i--) {
        typer.textContent = text.slice(0, i);
        await sleep(18);
      }
      await sleep(350);
      idx++;
    }
  })();
}

function pulseComposer() {
  const composer = el("composer");
  composer.classList.remove("pulse");
  void composer.offsetWidth; // restart the animation
  composer.classList.add("pulse");
}

el("cta-top-btn").addEventListener("click", () => {
  mainScroll.scrollTo({ top: 0, behavior: REDUCED_MOTION ? "auto" : "smooth" });
  setTimeout(() => {
    pulseComposer();
    if (!selectedCategory) document.querySelector(".category-card")?.focus({ preventScroll: true });
    else el("topic-input").focus({ preventScroll: true });
  }, 450);
});

el("scroll-cue").addEventListener("click", () => {
  el("story").scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth" });
});

// ---------- Progress screen ----------

let elapsedTimer = null;
let runStartedAt = 0;
let stepStartedAt = 0;
let activeStep = null;
let firstStepIdx = 0;
let tokenCount = 0;
let streamTextNode = null;

function formatDuration(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function startElapsedTimer({ resume = false } = {}) {
  stopElapsedTimer();
  if (!resume) {
    runStartedAt = Date.now();
    el("elapsed-time").textContent = "0:00";
  }
  elapsedTimer = setInterval(() => {
    el("elapsed-time").textContent = formatDuration(Date.now() - runStartedAt);
    if (activeStep) {
      el(`step-${activeStep}`).querySelector(".tl-time").textContent = formatDuration(Date.now() - stepStartedAt);
    }
  }, 1000);
}

function stopElapsedTimer() {
  clearInterval(elapsedTimer);
  elapsedTimer = null;
}

function enterProgress() {
  showScreen("progress");
  el("progress-title").innerHTML = `Working on it<span class="dots"><i>.</i><i>.</i><i>.</i></span>`;
  el("phase-label").textContent = "Starting…";
  el("progress-error").classList.add("hidden");
  el("overall-progress-bar").style.width = "2%";
  activeStep = null;
  firstStepIdx = 0;
  resetStreamPreview();
  STEP_ORDER.forEach((step) => {
    const li = el(`step-${step}`);
    li.classList.remove("active", "complete", "skipped");
    li.querySelector(".tl-time").textContent = "";
  });
  el("step-verifying").querySelector(".tl-body small").textContent = "Fact-check, fix & re-check";
  startElapsedTimer();
}

function setStep(status) {
  const idx = STEP_ORDER.indexOf(status);
  if (idx === -1) return;

  // Revisions start mid-pipeline (at write or edit); mark the earlier phases as
  // reused from the previous round rather than leaving them looking unfinished.
  if (activeStep === null) {
    firstStepIdx = idx;
    STEP_ORDER.slice(0, idx).forEach((step) => {
      const li = el(`step-${step}`);
      li.classList.add("skipped", "complete");
      li.querySelector(".tl-time").textContent = "kept";
    });
  } else if (activeStep !== status) {
    el(`step-${activeStep}`).querySelector(".tl-time").textContent = formatDuration(Date.now() - stepStartedAt);
  }

  activeStep = status;
  stepStartedAt = Date.now();
  resetStreamPreview();
  STEP_ORDER.forEach((step, i) => {
    const li = el(`step-${step}`);
    li.classList.remove("active");
    if (i < idx) li.classList.add("complete");
    else if (i === idx) li.classList.add("active");
    if (i >= idx) li.classList.remove("complete");
  });

  const span = STEP_ORDER.length - firstStepIdx;
  const pct = ((idx - firstStepIdx + 0.4) / span) * 100;
  el("overall-progress-bar").style.width = `${Math.max(4, pct)}%`;

  const copy = PHASE_COPY[status];
  el("progress-title").innerHTML = `${escapeHtml(copy.title)}<span class="dots"><i>.</i><i>.</i><i>.</i></span>`;
  el("phase-label").textContent = copy.label;
  swapVideo(el("phase-video"), `/static/videos/${status}.mp4`);
}

// One looping clip per phase, named to match STEP_ORDER exactly (see static/videos/).
// Fades out, swaps the source, fades back in once the new clip can play.
function swapVideo(video, src) {
  if (video.getAttribute("src") === src) {
    video.play().catch(() => {});
    return;
  }
  const load = () => {
    video.setAttribute("src", src);
    video.load();
    // Autoplay can be blocked on some browsers even when muted; that's fine,
    // the timeline still shows progress either way.
    video.play().catch(() => {});
    const reveal = () => video.classList.remove("swapping");
    video.addEventListener("loadeddata", reveal, { once: true });
    setTimeout(reveal, 900);
  };
  if (REDUCED_MOTION || !video.getAttribute("src")) {
    load();
    return;
  }
  video.classList.add("swapping");
  setTimeout(load, 300);
}

function resetStreamPreview() {
  const preview = el("stream-preview");
  preview.classList.remove("streaming");
  preview.innerHTML = `<span class="stream-placeholder">Waiting for the model's first words…</span>`;
  streamTextNode = null;
  tokenCount = 0;
  el("token-count").textContent = "waiting for tokens…";
}

// Status lines EditorAgent.verify_and_fix() emits between its check/fix passes.
const VERIFY_SUBPHASES = [
  { marker: "── Fixing", title: "Fixing flagged claims", label: "04 · Fix" },
  { marker: "── Re-checking", title: "Re-checking the corrected post", label: "04 · Re-check" },
];

function appendStreamToken(text) {
  const subphase = VERIFY_SUBPHASES.find((p) => text.includes(p.marker));
  if (subphase && activeStep === "verifying") {
    el("progress-title").innerHTML = `${escapeHtml(subphase.title)}<span class="dots"><i>.</i><i>.</i><i>.</i></span>`;
    el("phase-label").textContent = subphase.label;
    el("step-verifying").querySelector(".tl-body small").textContent = subphase.title;
  }

  const preview = el("stream-preview");
  // Only auto-scroll if the user hasn't scrolled up to read something.
  const nearBottom = preview.scrollHeight - preview.scrollTop - preview.clientHeight < 40;
  if (!streamTextNode) {
    preview.innerHTML = "";
    streamTextNode = document.createTextNode("");
    preview.appendChild(streamTextNode);
    preview.classList.add("streaming");
  }
  // appendData avoids re-serializing the whole buffer on every token.
  streamTextNode.appendData(text);
  tokenCount++;
  el("token-count").textContent = `${tokenCount.toLocaleString()} tokens`;
  if (nearBottom) preview.scrollTop = preview.scrollHeight;
}

function showProgressError(message, retryable = true) {
  stopElapsedTimer();
  el("stream-preview").classList.remove("streaming");
  el("progress-title").textContent = "Something went wrong";
  el("progress-error-message").textContent = message;
  el("retry-btn").classList.toggle("hidden", !retryable);
  el("progress-error").classList.remove("hidden");
}

// --- Event stream ---
// The SSE stream is the fast path. Two safety nets sit behind it so the page
// can't get stuck on "Working on it" forever: if the connection drops we
// reconnect (or report the run as lost if the server restarted), and a slow
// watchdog polls the run's status in case a terminal event was missed.

let eventSource = null;
let watchdog = null;
let reconnectTimer = null;
let listenToken = 0;

function stopListening() {
  listenToken++;
  if (eventSource) eventSource.close();
  eventSource = null;
  clearInterval(watchdog);
  clearTimeout(reconnectTimer);
  watchdog = null;
}

function listenForEvents(runId) {
  stopListening();
  const token = listenToken;
  const startedAt = Date.now();
  let sawStatus = false;

  const isCurrent = () => token === listenToken;
  const isActiveRun = () => runId === currentRunId;
  const userIsWatching = () => isActiveRun() && currentScreen === "progress";

  const finish = (snapshot) => {
    if (!isCurrent()) return;
    stopListening();
    loadSidebarHistory();
    if (userIsWatching()) {
      el("overall-progress-bar").style.width = "100%";
      STEP_ORDER.forEach((s) => el(`step-${s}`).classList.add("complete"));
      setActiveHistoryItem(runId);
      renderReview(snapshot);
    } else {
      showToast(`“${snapshot.topic}” is ready for review. Open it from History.`, "ok");
    }
  };

  const fail = (message, retryable = true) => {
    if (!isCurrent()) return;
    stopListening();
    loadSidebarHistory();
    if (userIsWatching()) showProgressError(message, retryable);
    else showError(message);
  };

  const checkStatus = async () => {
    const res = await fetch(`/api/runs/${runId}`);
    if (res.status === 401) {
      fail("You were signed out. Sign in again, then open this session from your history.", false);
      openSignIn();
      return "gone";
    }
    if (res.status === 404) {
      fail("This run is no longer on the server (was it restarted?). Start over to try again.", false);
      return "gone";
    }
    const snap = await res.json();
    // Right after a feedback/retry POST the server may not have flipped the
    // status yet, so only trust a stale-looking status once the stream has
    // shown signs of life or enough time has passed.
    const settled = sawStatus || Date.now() - startedAt > 8000;
    if (snap.status === "awaiting_feedback" && settled) finish(snap);
    else if (snap.status === "error" && settled) fail(snap.error || "The run failed.");
    return snap.status;
  };

  const source = new EventSource(`/api/runs/${runId}/events`);
  eventSource = source;

  source.onmessage = (msg) => {
    if (!isCurrent()) return;
    const payload = JSON.parse(msg.data);

    if (payload.event === "status") {
      sawStatus = true;
      // Keep the progress screen up to date even while the user is briefly
      // looking at another screen, so it's accurate when they come back.
      if (isActiveRun()) setStep(payload.status);
    } else if (payload.event === "token") {
      if (isActiveRun()) appendStreamToken(payload.text);
    } else if (payload.event === "awaiting_feedback") {
      finish(payload);
    } else if (payload.event === "error") {
      fail(payload.error);
    }
  };

  source.onerror = () => {
    source.close();
    if (!isCurrent()) return;
    eventSource = null;
    let attempt = 0;
    const tryReconnect = async () => {
      if (!isCurrent()) return;
      try {
        const status = await checkStatus();
        if (!isCurrent() || status === "gone") return;
        if (status !== "awaiting_feedback" && status !== "error") listenForEvents(runId);
      } catch {
        // Server unreachable - keep trying with backoff.
        attempt++;
        if (attempt === 1 && userIsWatching()) el("phase-label").textContent = "Reconnecting…";
        reconnectTimer = setTimeout(tryReconnect, Math.min(1500 * 2 ** attempt, 15000));
      }
    };
    reconnectTimer = setTimeout(tryReconnect, 1000);
  };

  watchdog = setInterval(() => {
    if (isCurrent()) checkStatus().catch(() => {});
  }, 6000);
}

el("retry-btn").addEventListener("click", async () => {
  el("progress-error").classList.add("hidden");
  try {
    const res = await fetch(`/api/runs/${currentRunId}/retry`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json()).detail || "Retry failed");
    resetStreamPreview();
    el("progress-title").innerHTML = `Retrying<span class="dots"><i>.</i><i>.</i><i>.</i></span>`;
    startElapsedTimer();
    activeStep = null;
    listenForEvents(currentRunId);
  } catch (err) {
    showProgressError(err.message);
  }
});

el("abandon-btn").addEventListener("click", startNewTopic);

// ---------- Review screen ----------

// Matches utils/parsers.py:format_grounding_notes()'s all-clear line.
const GROUNDING_ALL_CLEAR = "All specific claims are supported by the research.";

function renderGroundingCallout(calloutEl, notes, fixes = []) {
  if (!notes && !fixes.length) {
    calloutEl.classList.add("hidden");
    return;
  }
  const flagged = countFlaggedClaims(notes);
  const passed = flagged === 0 && (notes || "").trim().startsWith(GROUNDING_ALL_CLEAR);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const fixedText = fixes.length
    ? `The fact-check <b>auto-corrected ${plural(fixes.length, "claim")}</b> the research didn't support. `
    : "";

  let icon, body;
  if (flagged > 0) {
    icon = "⚠️";
    body = `${fixedText}<b>${plural(flagged, "claim")} still flagged</b>. The research may not support ${flagged === 1 ? "it" : "them"}. <a data-open-grounding>Review before publishing</a>`;
  } else if (passed) {
    icon = fixes.length ? "🛠️" : "✅";
    body = fixes.length
      ? `${fixedText}Every remaining specific claim is backed by the research. <a data-open-grounding>See what changed</a>`
      : `<b>Grounding check passed.</b> No unsupported specifics were found. <a data-open-grounding>See notes</a>`;
  } else {
    icon = "ℹ️";
    body = `${fixedText}The grounding check left notes to review. <a data-open-grounding>Read them</a>`;
  }
  calloutEl.classList.toggle("ok", passed);
  calloutEl.innerHTML = `<span>${icon}</span><div>${body}</div>`;
  calloutEl.classList.remove("hidden");
}

// The grounding section: what was auto-corrected (if anything), then the latest check's notes.
function renderGroundingSection(sectionEl, contentEl, notes, fixes, sources) {
  if (!notes && !fixes.length) {
    sectionEl.classList.add("hidden");
    return;
  }
  let md = "";
  if (fixes.length) {
    md += "**Auto-corrected before review**\n\n" + fixes.map((f) => `- ${f}`).join("\n") + "\n\n";
    if (notes) md += "**After correction**\n\n";
  }
  renderMarkdown(contentEl, md + (notes || ""), sources);
  sectionEl.classList.remove("hidden");
}

document.addEventListener("click", (e) => {
  if (!e.target.matches("[data-open-grounding]")) return;
  const section = currentScreen === "historyDetail" ? el("history-detail-grounding-section") : el("grounding-section");
  section.open = true;
  section.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" });
});

function renderReview(snapshot) {
  currentRunId = snapshot.run_id || currentRunId;
  currentMarkdown = snapshot.blog_final || snapshot.blog_draft || "";

  const meta = CATEGORY_META[snapshot.category] || { icon: "📝" };
  el("review-category").textContent = `${meta.icon} ${snapshot.category}`;
  el("review-title").textContent = snapshot.topic;
  el("review-iteration").textContent =
    snapshot.iteration_count > 0
      ? `Revision ${snapshot.iteration_count} of ${snapshot.max_iterations}`
      : "First draft";

  currentSources = snapshot.research_sources || [];
  renderMarkdown(el("blog-content"), currentMarkdown, currentSources);
  el("research-content").textContent = snapshot.research_content || "No research content.";

  const sources = snapshot.research_sources || [];
  renderSources(el("sources-list"), sources);
  el("sources-summary").textContent = `Sources (${sources.length})`;

  const fixes = snapshot.grounding_fixes || [];
  renderGroundingSection(el("grounding-section"), el("grounding-content"), snapshot.grounding_notes, fixes, currentSources);
  renderGroundingCallout(el("review-grounding-callout"), snapshot.grounding_notes, fixes);

  const atMax = snapshot.iteration_count >= snapshot.max_iterations;
  el("revise-btn").disabled = atMax;
  el("feedback-input").disabled = atMax;
  document.querySelectorAll("#quick-feedback .chip").forEach((c) => (c.disabled = atMax));
  el("max-iterations-note").classList.toggle("hidden", !atMax);

  el("feedback-input").value = "";
  setFeedbackBusy(false);
  showScreen("review");
  loadReviewThread(currentRunId);

  const { words, minutes } = textStats(currentMarkdown);
  animateNumber(el("review-words"), words);
  animateNumber(el("review-read"), minutes);
  animateNumber(el("review-sources-count"), sources.length);
}

function renderQuickFeedback() {
  const container = el("quick-feedback");
  QUICK_FEEDBACK.forEach((text, i) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.style.setProperty("--i", i);
    chip.textContent = text;
    chip.addEventListener("click", () => {
      const input = el("feedback-input");
      const existing = input.value.trim();
      input.value = existing ? `${existing}. ${text}` : text;
      input.focus();
    });
    container.appendChild(chip);
  });
}

function setFeedbackBusy(busy) {
  ["approve-btn", "reresearch-btn"].forEach((id) => (el(id).disabled = busy));
  if (busy) el("revise-btn").disabled = true;
}

el("copy-review-btn").addEventListener("click", () => copyText(currentMarkdown));

el("approve-btn").addEventListener("click", async () => {
  setFeedbackBusy(true);
  try {
    await sendFeedback({ action: "approve" });
  } catch {
    setFeedbackBusy(false);
    return;
  }
  loadSidebarHistory();
  enterDone();
});

el("reresearch-btn").addEventListener("click", async () => {
  if (!confirm("This discards the current draft and researches the topic again. Continue?")) return;
  setFeedbackBusy(true);
  try {
    await sendFeedback({ action: "reresearch" });
  } catch {
    setFeedbackBusy(false);
    return;
  }
  enterProgress();
  listenForEvents(currentRunId);
});

async function submitRevision() {
  const feedback = el("feedback-input").value.trim();
  if (!feedback) {
    showError("Please describe what you'd like changed.");
    el("feedback-input").focus();
    return;
  }
  setFeedbackBusy(true);
  try {
    await sendFeedback({ action: "revise", feedback });
  } catch {
    setFeedbackBusy(false);
    el("revise-btn").disabled = false;
    return;
  }
  enterProgress();
  listenForEvents(currentRunId);
}

el("revise-btn").addEventListener("click", submitRevision);
el("feedback-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !el("revise-btn").disabled) submitRevision();
});

async function sendFeedback(body) {
  try {
    const res = await fetch(`/api/runs/${currentRunId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (handleUnauthorized(res)) throw new Error("Please sign in again");
    if (!res.ok) throw new Error((await res.json()).detail || "Request failed");
  } catch (err) {
    showError(err.message);
    throw err;
  }
}

// ---------- Done screen ----------

function enterDone() {
  el("download-link").href = `/api/runs/${currentRunId}/download`;
  renderMarkdown(el("done-preview"), currentMarkdown, currentSources);
  setActiveHistoryItem(currentRunId);
  showScreen("done");
  launchConfetti();
}

el("copy-done-btn").addEventListener("click", () => copyText(currentMarkdown));

// ---------- History detail (opened from the sidebar) ----------

async function openHistoryDetail(runId) {
  // A run that's still live on the server and waiting for feedback can be
  // picked back up where it left off, instead of only viewed read-only.
  try {
    const live = await fetch(`/api/runs/${runId}`);
    if (live.ok) {
      const snap = await live.json();
      if (snap.status === "awaiting_feedback") {
        if (runId !== currentRunId) stopListening();
        currentRunId = runId;
        selectedCategory = snap.category;
        setActiveHistoryItem(runId);
        renderReview(snap);
        return;
      }
      if (runId === currentRunId && STEP_ORDER.includes(snap.status) && eventSource) {
        showScreen("progress");
        startElapsedTimer({ resume: true });
        return;
      }
    }
  } catch {
    // fall through to the stored copy
  }

  try {
    const res = await fetch(`/api/history/${runId}`);
    if (handleUnauthorized(res)) return;
    if (!res.ok) throw new Error((await res.json()).detail || "Not found");
    const record = await res.json();

    historyMarkdown = record.blog_final || record.blog_draft || "";
    const meta = CATEGORY_META[record.category] || { icon: "📝" };
    el("history-detail-category").textContent = `${meta.icon} ${record.category}`;
    el("history-detail-title").textContent = record.topic;
    el("history-detail-meta").textContent =
      `${STATUS_LABELS[record.status] || record.status} · revision ${record.iteration_count} of ${record.max_iterations}`;
    renderThread(el("history-thread"), threadFromRecord(record), { expandLastDraft: true });

    const sources = record.research_sources || [];
    renderSources(el("history-detail-sources"), sources);
    el("history-detail-sources-summary").textContent = `Sources (${sources.length})`;

    renderGroundingSection(
      el("history-detail-grounding-section"),
      el("history-detail-grounding"),
      record.grounding_notes,
      record.grounding_fixes || [],
      record.research_sources
    );

    const updated = new Date(record.updated_at);
    el("history-date").textContent = isNaN(updated) ? "" : updated.toLocaleString();
    el("history-detail-download").href = `/api/runs/${runId}/download`;
    el("history-detail-download").classList.toggle("hidden", !record.blog_final);
    setActiveHistoryItem(runId);
    showScreen("historyDetail");

    const { words, minutes } = textStats(historyMarkdown);
    animateNumber(el("history-words"), words);
    animateNumber(el("history-read"), minutes);
  } catch (err) {
    showError("Could not load run: " + err.message);
  }
}

// ---------- Session thread (the history "conversation") ----------

// Sessions saved before threads existed have no events - rebuild a minimal
// thread (topic -> final draft -> approval) from the stored snapshot.
function threadFromRecord(record) {
  if (record.thread && record.thread.length) return record.thread;
  const thread = [
    { role: "user", kind: "topic", data: { category: record.category, topic: record.topic }, created_at: record.created_at },
  ];
  if (record.blog_final || record.blog_draft) {
    thread.push({
      role: "assistant",
      kind: "draft",
      created_at: record.updated_at,
      data: {
        iteration: record.iteration_count,
        blog: record.blog_final || record.blog_draft,
        sources: record.research_sources || [],
        grounding_notes: record.grounding_notes,
        grounding_fixes: record.grounding_fixes || [],
      },
    });
  }
  if (record.status === "done") thread.push({ role: "user", kind: "approved", data: {}, created_at: record.updated_at });
  return thread;
}

function formatClock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function avatarHtml(role) {
  if (role === "assistant") {
    return `<span class="msg-avatar msg-avatar-ai"><svg viewBox="0 0 24 24" width="14" height="14"><circle cx="10.5" cy="10.5" r="5.5" fill="none" stroke="currentColor" stroke-width="2.6"/><path d="M15 15l5 5" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg></span>`;
  }
  const user = authInfo.user;
  if (user && user.avatar_url) {
    return `<img class="msg-avatar" src="${escapeHtml(user.avatar_url)}" alt="" referrerpolicy="no-referrer" />`;
  }
  const initial = ((user && user.name) || "You").charAt(0).toUpperCase();
  return `<span class="msg-avatar msg-avatar-user">${escapeHtml(initial)}</span>`;
}

function draftLabel(data, draftNumber) {
  if (data.iteration > 0) return `Revision ${data.iteration}`;
  return draftNumber === 1 ? "First draft" : "Fresh draft (re-researched)";
}

function renderThread(container, thread, { expandLastDraft = true } = {}) {
  container.innerHTML = "";
  const lastDraft = thread.map((e) => e.kind).lastIndexOf("draft");
  let draftNumber = 0;

  thread.forEach((event, i) => {
    const data = event.data || {};
    const msg = document.createElement("div");
    msg.className = `msg msg-${event.role === "assistant" ? "ai" : "user"} msg-kind-${event.kind}`;
    msg.style.setProperty("--i", Math.min(i, 12));
    const who = event.role === "assistant" ? "TruthLens" : (authInfo.user && authInfo.user.name) || "You";
    let title = "";
    let body = "";

    if (event.kind === "topic") {
      const icon = (CATEGORY_META[data.category] || {}).icon || "📝";
      body = `<p>Research <b>${escapeHtml(data.topic || "")}</b> <span class="msg-chip">${icon} ${escapeHtml(data.category || "")}</span></p>`;
    } else if (event.kind === "feedback") {
      const agent = data.routed_to === "writer" ? "writer" : "editor";
      body = `<p class="msg-quote">${escapeHtml(data.text || "")}</p><span class="msg-route">→ sent to the ${agent}</span>`;
    } else if (event.kind === "reresearch") {
      body = `<p>↻ Research this again from scratch</p>`;
    } else if (event.kind === "approved") {
      body = `<p class="msg-approved">✓ Approved. Article finalized</p>`;
    } else if (event.kind === "error") {
      body = `<p class="msg-error">⚠ ${escapeHtml(data.message || "Something went wrong")}</p>`;
    } else if (event.kind === "draft") {
      draftNumber++;
      title = draftLabel(data, draftNumber);
      const { words } = textStats(data.blog || "");
      const fixes = (data.grounding_fixes || []).length;
      const flagged = countFlaggedClaims(data.grounding_notes);
      const badges = [
        `${words.toLocaleString()} words`,
        `${(data.sources || []).length} sources`,
        fixes ? `🛠️ ${fixes} auto-corrected` : "",
        flagged ? `⚠️ ${flagged} flagged` : "",
      ].filter(Boolean);
      body = `
        <div class="msg-badges">${badges.map((b) => `<span>${b}</span>`).join("")}</div>
        <details class="msg-draft"${i === lastDraft && expandLastDraft ? " open" : ""}>
          <summary>${i === lastDraft ? "Read this version" : "Read this earlier version"}</summary>
          <div class="msg-draft-body prose"></div>
        </details>`;
    }

    msg.innerHTML = `
      ${avatarHtml(event.role)}
      <div class="msg-bubble">
        <div class="msg-meta"><b>${escapeHtml(who)}</b>${title ? ` · ${escapeHtml(title)}` : ""}<span>${formatClock(event.created_at)}</span></div>
        ${body}
      </div>`;

    // Drafts are full articles - only render one when it's opened.
    const details = msg.querySelector(".msg-draft");
    if (details) {
      const renderDraft = () => {
        const target = details.querySelector(".msg-draft-body");
        if (!target.childElementCount) renderMarkdown(target, data.blog || "", data.sources || []);
      };
      if (details.open) renderDraft();
      details.addEventListener("toggle", () => details.open && renderDraft());
    }
    container.appendChild(msg);
  });
}

// On the review screen, show the conversation so far once there's more to it
// than "topic -> first draft".
async function loadReviewThread(runId) {
  const section = el("review-thread-section");
  section.classList.add("hidden");
  section.open = false;
  try {
    const res = await fetch(`/api/history/${runId}`);
    if (!res.ok || runId !== currentRunId) return;
    const thread = threadFromRecord(await res.json());
    if (thread.length <= 2) return;
    renderThread(el("review-thread"), thread, { expandLastDraft: false });
    el("review-thread-summary").textContent = `Conversation so far (${thread.length} messages)`;
    section.classList.remove("hidden");
  } catch {
    // history is a nice-to-have on this screen
  }
}

// ---------- Sign-in ----------

function openSignIn() {
  el("signin-modal").classList.remove("hidden");
  closeSidebar();
  const first = el("signin-modal").querySelector(".btn-provider:not(.hidden)");
  if (first) first.focus();
}

function closeSignIn() {
  el("signin-modal").classList.add("hidden");
}

// True (and prompts sign-in) if the server says this request needs a signed-in user.
function handleUnauthorized(res) {
  if (res.status !== 401) return false;
  authInfo.user = null;
  renderAuth();
  openSignIn();
  return true;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-open-signin]")) openSignIn();
  if (e.target.closest("[data-close-signin]")) closeSignIn();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSignIn();
});

el("signout-btn").addEventListener("click", async () => {
  try {
    await fetch("/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/";
  }
});

// Embedded in another page (e.g. the huggingface.co Space page): Google/GitHub
// refuse to show their login inside a frame, and the browser won't keep our
// login cookie there - so sign-in has to happen with the app in its own tab.
const IS_FRAMED = (() => {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
})();

function renderAuth() {
  const { auth_enabled, providers, user } = authInfo;
  if (IS_FRAMED) {
    el("signin-modal").querySelector(".signin-providers").innerHTML =
      `<a class="btn btn-primary" href="${escapeHtml(window.location.origin)}/" target="_blank" rel="noopener">Open TruthLens in a new tab to sign in ↗</a>`;
  } else {
    providers.forEach((p) => el(`signin-${p}`)?.classList.remove("hidden"));
  }

  el("signin-card").classList.toggle("hidden", !needsSignIn());
  const showUser = auth_enabled && !!user;
  el("user-row").classList.toggle("hidden", !showUser);
  if (showUser) {
    el("user-name").textContent = user.name || user.email || "Signed in";
    el("user-name").title = user.email || "";
    const avatar = el("user-avatar");
    avatar.innerHTML = "";
    if (user.avatar_url) {
      const img = document.createElement("img");
      img.src = user.avatar_url;
      img.alt = "";
      img.referrerPolicy = "no-referrer";
      avatar.appendChild(img);
    } else {
      avatar.textContent = (user.name || "?").charAt(0).toUpperCase();
    }
  }
  updateStartButton();
}

async function loadAuth() {
  try {
    const res = await fetch("/api/me");
    if (res.ok) authInfo = await res.json();
  } catch {
    // server still starting - behave as signed-out-but-local until health check retries
  }
  renderAuth();

  // Coming back from a cancelled/failed OAuth redirect.
  const params = new URLSearchParams(window.location.search);
  if (params.has("auth_error")) {
    showError("Sign-in didn't complete. Please try again.");
    history.replaceState(null, "", "/");
  }
}

// =====================================================================
// Motion & ambience
// =====================================================================

// ---------- Pointer-reactive tilt + spotlight ----------

function attachTilt(node, maxDeg) {
  node.addEventListener("pointermove", (e) => {
    const r = node.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width;
    const py = (e.clientY - r.top) / r.height;
    node.style.setProperty("--mx", `${px * 100}%`);
    node.style.setProperty("--my", `${py * 100}%`);
    if (!REDUCED_MOTION && maxDeg) {
      node.style.transform = `rotateX(${(0.5 - py) * maxDeg}deg) rotateY(${(px - 0.5) * maxDeg}deg) translateY(-3px)`;
    }
  });
  node.addEventListener("pointerleave", () => {
    node.style.transform = "";
  });
}

document.querySelectorAll(".feature-card").forEach((card) => attachTilt(card, 0));

// Hero portal leans gently toward the cursor.
(function portalParallax() {
  if (REDUCED_MOTION) return;
  const hero = document.querySelector(".hero");
  const portal = el("portal");
  hero.addEventListener("pointermove", (e) => {
    const r = hero.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5;
    const y = (e.clientY - r.top) / r.height - 0.5;
    portal.style.transform = `rotateY(${x * 14}deg) rotateX(${-y * 14}deg)`;
  });
  hero.addEventListener("pointerleave", () => (portal.style.transform = ""));
})();

// ---------- Cursor glow ----------

(function cursorGlow() {
  if (REDUCED_MOTION || matchMedia("(pointer: coarse)").matches) return;
  const glow = el("cursor-glow");
  let x = -1000, y = -1000, queued = false;
  window.addEventListener("pointermove", (e) => {
    x = e.clientX;
    y = e.clientY;
    if (!queued) {
      queued = true;
      requestAnimationFrame(() => {
        glow.style.transform = `translate3d(${x}px, ${y}px, 0)`;
        queued = false;
      });
    }
  });
})();

// ---------- Scroll reveal ----------

const revealObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        revealObserver.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.12 }
);
document.querySelectorAll(".reveal").forEach((node) => revealObserver.observe(node));

// ---------- Scrollytelling: phase videos driven by scroll position ----------

const storyVideo = el("story-video");
const storySteps = [...document.querySelectorAll(".story-step")];
const stageSegments = [...document.querySelectorAll("#stage-segments span")];
let storyInView = false;
let heroInView = true;

function activateStoryStep(step) {
  storySteps.forEach((s) => s.classList.toggle("active", s === step));
  const phase = step.dataset.phase;
  el("stage-label").textContent = PHASE_COPY[phase].label;
  swapVideo(storyVideo, `/static/videos/${phase}.mp4`);
  if (!storyInView) storyVideo.pause();
}

const storyObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) activateStoryStep(entry.target);
    });
  },
  { root: mainScroll, rootMargin: "-45% 0px -45% 0px" }
);
storySteps.forEach((s) => storyObserver.observe(s));

// Only keep a landing video decoding while it's actually on screen.
const videoVisibility = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.target === el("hero-video")) heroInView = entry.isIntersecting;
      else storyInView = entry.isIntersecting;
    });
    setLandingVideosActive(currentScreen === "setup");
  },
  { root: mainScroll, threshold: 0.05 }
);
videoVisibility.observe(el("hero-video"));
videoVisibility.observe(storyVideo);

function setLandingVideosActive(active) {
  const hero = el("hero-video");
  if (active && heroInView) hero.play().catch(() => {});
  else hero.pause();
  if (active && storyInView && storyVideo.getAttribute("src")) storyVideo.play().catch(() => {});
  else storyVideo.pause();
}

function updateStorySegments() {
  const center = mainScroll.clientHeight / 2 + mainScroll.getBoundingClientRect().top;
  storySteps.forEach((step, i) => {
    const r = step.getBoundingClientRect();
    const fill = Math.min(1, Math.max(0, (center - r.top) / r.height));
    stageSegments[i]?.style.setProperty("--fill", fill.toFixed(3));
  });
}

function updateReadProgress() {
  const bar = el("read-progress-bar");
  if (currentScreen === "progress") {
    bar.style.transform = "scaleX(0)";
    return;
  }
  const max = mainScroll.scrollHeight - mainScroll.clientHeight;
  bar.style.transform = `scaleX(${max > 0 ? mainScroll.scrollTop / max : 0})`;
}

let scrollQueued = false;
mainScroll.addEventListener(
  "scroll",
  () => {
    if (scrollQueued) return;
    scrollQueued = true;
    requestAnimationFrame(() => {
      scrollQueued = false;
      updateReadProgress();
      if (currentScreen === "setup") updateStorySegments();
    });
  },
  { passive: true }
);

// ---------- Constellation background ----------

(function constellation() {
  const canvas = el("bg-canvas");
  const ctx = canvas.getContext("2d");
  let w = 0, h = 0, dpr = 1, particles = [];
  const mouse = { x: -9999, y: -9999 };
  const LINK = 130;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.min(90, Math.floor((w * h) / 16000));
    particles = Array.from({ length: count }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.25,
      vy: (Math.random() - 0.5) * 0.25,
      r: Math.random() * 1.6 + 0.4,
      gold: Math.random() < 0.18,
      tw: Math.random() * Math.PI * 2,
    }));
  }

  function draw() {
    ctx.clearRect(0, 0, w, h);
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      if (!REDUCED_MOTION) {
        p.x += p.vx;
        p.y += p.vy;
        p.tw += 0.02;
        if (p.x < -10) p.x = w + 10;
        if (p.x > w + 10) p.x = -10;
        if (p.y < -10) p.y = h + 10;
        if (p.y > h + 10) p.y = -10;
        // gentle pull toward the cursor
        const dxm = mouse.x - p.x, dym = mouse.y - p.y;
        const dm = Math.hypot(dxm, dym);
        if (dm < 180 && dm > 1) {
          p.x += (dxm / dm) * 0.18;
          p.y += (dym / dm) * 0.18;
        }
      }
      for (let j = i + 1; j < particles.length; j++) {
        const q = particles[j];
        const dx = p.x - q.x, dy = p.y - q.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < LINK * LINK) {
          const a = (1 - Math.sqrt(d2) / LINK) * 0.22;
          ctx.strokeStyle = `rgba(62, 230, 208, ${a})`;
          ctx.lineWidth = 0.6;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.stroke();
        }
      }
      const dmx = p.x - mouse.x, dmy = p.y - mouse.y;
      const dmouse = Math.hypot(dmx, dmy);
      if (dmouse < 180) {
        ctx.strokeStyle = `rgba(212, 175, 55, ${(1 - dmouse / 180) * 0.35})`;
        ctx.lineWidth = 0.7;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(mouse.x, mouse.y);
        ctx.stroke();
      }
      const twinkle = 0.55 + Math.sin(p.tw) * 0.35;
      ctx.fillStyle = p.gold ? `rgba(212, 175, 55, ${twinkle})` : `rgba(62, 230, 208, ${twinkle})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function loop() {
    if (!document.hidden) draw();
    requestAnimationFrame(loop);
  }

  window.addEventListener("resize", () => {
    resize();
    if (REDUCED_MOTION) draw();
  });
  window.addEventListener("pointermove", (e) => {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
  });
  document.addEventListener("pointerleave", () => {
    mouse.x = mouse.y = -9999;
  });

  resize();
  if (REDUCED_MOTION) draw();
  else loop();
})();

// ---------- Confetti (on approve) ----------

function launchConfetti() {
  if (REDUCED_MOTION) return;
  const canvas = el("confetti-canvas");
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = window.innerWidth, h = window.innerHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const colors = ["#3ee6d0", "#d4af37", "#ece7d6", "#5fe39a", "#9ff7ec"];
  const pieces = [];
  [[0.15, -0.5], [0.85, 0.5]].forEach(([fx, dir]) => {
    for (let i = 0; i < 90; i++) {
      const angle = -Math.PI / 2 + dir * (0.3 + Math.random() * 0.5) + (Math.random() - 0.5) * 0.4;
      const speed = 9 + Math.random() * 9;
      pieces.push({
        x: w * fx,
        y: h * 0.75,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        size: 5 + Math.random() * 6,
        rot: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.3,
        color: colors[(Math.random() * colors.length) | 0],
        life: 0,
      });
    }
  });

  const start = performance.now();
  (function frame(now) {
    ctx.clearRect(0, 0, w, h);
    pieces.forEach((p) => {
      p.vy += 0.32;
      p.vx *= 0.985;
      p.vy *= 0.985;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.globalAlpha = Math.max(0, 1 - (now - start) / 3200);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
      ctx.restore();
    });
    if (now - start < 3200) requestAnimationFrame(frame);
    else ctx.clearRect(0, 0, w, h);
  })(start);
}

// ---------- Startup ----------

let healthRetry = null;
async function checkHealth() {
  const pill = el("model-pill");
  const pillText = el("model-pill-text");
  const sideDot = el("sidebar-status-dot");
  const sideText = el("sidebar-status-text");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const banner = el("health-banner");
    pill.classList.toggle("is-online", data.ok);
    pill.classList.toggle("is-offline", !data.ok);
    sideDot.classList.toggle("is-online", data.ok);
    sideDot.classList.toggle("is-offline", !data.ok);
    if (!data.ok) {
      pillText.textContent = "model offline";
      const hosted = data.provider && data.provider !== "ollama";
      let title, fix;
      if (hosted) {
        // Visitors of a deployed app can't fix this - the message is for whoever runs it.
        sideText.textContent = "Model API unavailable";
        title = "The AI model isn't available right now";
        fix = "Research can't start until it's back. If you run this app, check its LLM_API_KEY and LLM_PROVIDER settings.";
      } else {
        sideText.textContent = "Ollama unreachable";
        const unreachable = data.errors.some((e) => /reach|connect/i.test(e));
        title = unreachable ? "Ollama isn't running" : "Ollama has a problem";
        fix = unreachable
          ? "Start it with <code>ollama serve</code>. This banner clears on its own once it's up."
          : `Make sure the model is pulled: <code>ollama pull ${escapeHtml(data.model || "")}</code>`;
      }
      banner.innerHTML = `
        <b>${title}</b>${fix}
        <details><summary>Details</summary><p>${escapeHtml(data.errors.join(" "))}</p></details>`;
      banner.classList.remove("hidden");
      // Keep checking so the banner clears on its own once Ollama comes up.
      clearTimeout(healthRetry);
      healthRetry = setTimeout(checkHealth, 15000);
    } else {
      pillText.textContent = data.model || "online";
      sideText.textContent = `${data.model || "model"} · ready`;
      banner.classList.add("hidden");
    }
  } catch {
    // health check itself failing just means the server is still starting; try again shortly.
    clearTimeout(healthRetry);
    healthRetry = setTimeout(checkHealth, 5000);
  }
}

renderCategoryCards();
renderQuickFeedback();
updateStartButton();
showScreen("setup");
startTypewriter();
checkHealth();
loadAuth().then(loadSidebarHistory);
