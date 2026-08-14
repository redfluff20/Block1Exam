const $ = (id) => document.getElementById(id);

// ---------- Theme ----------
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("block1-theme", t); } catch (e) {}
}
function initTheme() {
  let t = "light";
  try { t = localStorage.getItem("block1-theme") || "light"; } catch (e) {}
  applyTheme(t);
  const sel = $("theme-select");
  if (sel) sel.value = t;
  sel.addEventListener("change", () => applyTheme(sel.value));
}

let state = {
  view: "dashboard",
  lectureId: null,
  sessionId: null,
  optionSelected: false,
  currentImages: [],
  currentSlide: null,
  tutorMode: true,
  quizTotal: 0,
  quizActive: false,
  reviewSlides: [],
  segAttached: false,
  position: 0,
  totalQuestions: 0,
  pendingIndex: null,   // quiz mode: the option selected but not yet submitted
  pendingQid: null,
  submitBtn: null,
};

// ---------- Navigation ----------
document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    switchView(btn.dataset.view);
  });
});

function switchView(view) {
  if (state.view === "drill" && view !== "drill") pauseActiveQuiz();
  state.view = view;
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $("view-" + view).classList.add("active");
  if (view === "dashboard") loadDashboard();
  if (view === "learn") loadLearnList();
  if (view === "drill") loadDrill();
  if (view === "progress") loadProgress();
}

// ---------- Toast ----------
let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3000);
}

// ---------- Dashboard ----------
function pct(x) {
  return x === null || x === undefined ? "—" : Math.round(x * 100) + "%";
}

function accClass(x) {
  if (x === null || x === undefined) return "";
  if (x < 0.6) return "acc-bad";
  if (x < 0.8) return "acc-mid";
  return "acc-good";
}

async function loadDashboard() {
  $("dash-loading").style.display = "";
  $("dash-content").hidden = true;
  refreshLLMKeyStatus();
  try {
    const d = await fetch("/api/dashboard").then((r) => r.json());
    $("stat-lectures").textContent = d.lecture_count;
    $("stat-coverage").textContent = d.coverage.question_coverage + "%";
    $("stat-accuracy").textContent = pct(d.overall_accuracy);
    $("stat-accuracy").className = "stat " + accClass(d.overall_accuracy);
    $("stat-questions").textContent = d.question_count;
    $("stat-due").textContent = d.missed_count;

    // Lecture breakdown table (slide coverage)
    const tbody = document.querySelector("#lecture-stats tbody");
    tbody.innerHTML = "";
    for (const l of d.lectures) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="td-title">${escapeHtml(l.lecture_title)}</td>
        <td>${l.slides_covered}/${l.slides_total} <span class="muted">covered</span></td>
        <td>${l.q_count}</td>
        <td><span class="acc-badge ${accClass(l.accuracy)}">${pct(l.accuracy)}</span></td>
        <td><span class="mini-bar"><span class="mini-fill" style="width:${l.coverage_pct}%"></span></span>
            <span class="muted">${l.coverage_pct}%</span></td>`;
      tbody.appendChild(tr);
    }

    // Weak slides
    const weak = $("weak-list");
    weak.innerHTML = "";
    if (d.weak_slides && d.weak_slides.length) {
      $("weak-empty").style.display = "none";
      d.weak_slides.forEach((s) => {
        const li = document.createElement("li");
        li.innerHTML = `
          <span class="w-left">
            <span class="acc-badge ${accClass(s.accuracy)}">${pct(s.accuracy)}</span>
            <span>
              <span class="w-title">Slide ${s.slide_num}: ${escapeHtml(shortLabel(s.text))}</span><br>
              <span class="muted">${escapeHtml(s.lecture_title)} · ${s.answers} answer${s.answers === 1 ? "" : "s"}</span>
            </span>
          </span>`;
        weak.appendChild(li);
      });
    } else {
      $("weak-empty").style.display = "";
    }

    // Coverage gaps (slides with no questions)
    const gapsEl = $("gap-list");
    gapsEl.innerHTML = "";
    const gaps = (d.gaps || []).slice(0, 12);
    if (gaps.length) {
      $("gap-empty").style.display = "none";
      for (const g of gaps) {
        const li = document.createElement("li");
        li.innerHTML = `
          <span class="w-title">Slide ${g.slide_num}: ${escapeHtml(shortLabel(g.text))}</span><br>
          <span class="muted">${escapeHtml(g.lecture_title)}</span>
          <span class="gap-tag">no questions</span>`;
        gapsEl.appendChild(li);
      }
      if (gaps.length >= 12) {
        const li = document.createElement("li");
        li.className = "muted";
        li.textContent = `…showing the first uncovered slides`;
        gapsEl.appendChild(li);
      }
    } else {
      $("gap-empty").style.display = "";
    }

    $("dash-loading").style.display = "none";
    $("dash-content").hidden = false;
  } catch (e) {
    $("dash-loading").textContent = "Failed to load dashboard: " + e.message;
  }
}

function shortLabel(s, n = 60) {
  s = s || "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

$("import-btn").addEventListener("click", async () => {
  const files = $("file-input").files;
  if (!files.length) return toast("Choose files first");
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  $("import-btn").disabled = true;
  $("import-result").textContent = "Importing...";
  try {
    const res = await fetch("/api/lectures/import", { method: "POST", body: fd }).then((r) => r.json());
    let msg = `Imported ${res.imported.length} lecture(s).`;
    if (res.duplicates && res.duplicates.length) {
      msg += ` Skipped ${res.duplicates.length} duplicate(s): ` + res.duplicates.map((d) => d.title).join("; ");
    }
    if (res.errors.length) msg += " Errors: " + res.errors.join("; ");
    $("import-result").textContent = msg;
    toast(msg);
    loadDashboard();
  } catch (e) {
    $("import-result").textContent = "Import failed: " + e.message;
  } finally {
    $("import-btn").disabled = false;
  }
});

// ---------- LLM settings (dashboard) ----------
async function refreshLLMKeyStatus() {
  const statusEl = $("llm-key-status");
  if (!statusEl) return;
  try {
    const s = await fetch("/api/settings/llm").then((r) => r.json());
    $("llm-base-url-input").value = s.base_url || "";
    $("llm-model-input").value = s.model || "";
    statusEl.innerHTML = s.api_key_set
      ? `API key saved · model: <strong>${escapeHtml(s.model)}</strong>`
      : `No API key yet. Paste your key and click Save.`;
  } catch (e) {
    statusEl.textContent = "";
  }
}

const saveLLMKeyBtn = $("save-llm-key");
if (saveLLMKeyBtn) saveLLMKeyBtn.addEventListener("click", async () => {
  const input = $("llm-api-key-input");
  const statusEl = $("llm-key-status");
  const key = (input.value || "").trim();
  if (!key) return toast("Paste an API key first");
  const btn = $("save-llm-key");
  btn.disabled = true;
  statusEl.textContent = "Saving...";
  try {
    const res = await fetch("/api/settings/llm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: key,
        base_url: (($("llm-base-url-input") || {}).value || "").trim(),
        model: (($("llm-model-input") || {}).value || "").trim(),
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "failed");
    input.value = "";
    statusEl.innerHTML = "Settings saved. Used immediately for captions, summaries, and question generation.";
    toast("LLM settings saved");
    refreshLLMKeyStatus();
  } catch (e) {
    statusEl.textContent = "Failed to save: " + e.message;
  } finally {
    btn.disabled = false;
  }
});

// ---------- Learn ----------
const LEARN_STATUS_LABEL = {
  not_started: "Not started",
  generating: "Summarizing…",
  done: "Summary ready",
  error: "Summary failed",
};

function learnStatusLabel(status) {
  return LEARN_STATUS_LABEL[status] || (status || "Not started");
}

async function loadLearnList() {
  const list = $("learn-lecture-list");
  $("learn-summary").hidden = true;
  list.hidden = false;
  const lectures = await fetch("/api/lectures").then((r) => r.json());
  list.innerHTML = "";
  if (!lectures.length) {
    list.innerHTML = '<div class="muted">No lectures yet. Import them on the Dashboard.</div>';
    return;
  }
  const gridView = localStorage.getItem("learn-view") === "grid";
  list.className = gridView ? "learn-grid" : "";
  const delBtn = (l) => `<button class="btn small ghost danger-text" id="del-${l.id}" title="Delete lecture">Delete</button>`;
  const regenBtn = (l) => `<button class="btn small ghost" id="regen-${l.id}" title="Re-run captions + summary (vision)">Regenerate</button>`;

  // Live-poll while any lecture is still generating
  const anyGenerating = lectures.some((l) => l.summary_status === "generating");

  for (const l of lectures) {
    const status = learnStatusLabel(l.summary_status);
    const captioned = l.captioned_slides || 0;
    const capCoverage = l.slide_count ? `${captioned}/${l.slide_count} captioned` : "";
    let el;
    if (gridView) {
      el = document.createElement("div");
      el.className = "lecture-card";
      el.innerHTML = `
        <div class="lc-title">${escapeHtml(l.title)}</div>
        <div class="lc-meta">${l.slide_count} slides · ${l.word_count} words</div>
        <div class="lc-status">${status}${capCoverage ? ` · ${capCoverage}` : ""}</div>
        <div class="lc-actions">${regenBtn(l)} ${delBtn(l)}</div>`;
      el.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        openSummary(l.id);
      });
    } else {
      el = document.createElement("div");
      el.className = "lecture-row";
      el.innerHTML = `
        <div class="row-main">
          <div class="title">${escapeHtml(l.title)}</div>
          <div class="meta">${l.slide_count} slides · ${l.word_count} words</div>
        </div>
        <div class="row-actions">
          <span class="meta">${status}${capCoverage ? ` · ${capCoverage}` : ""}</span>
          ${regenBtn(l)}
          ${delBtn(l)}
        </div>`;
      el.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        openSummary(l.id);
      });
    }
    const del = el.querySelector(`#del-${l.id}`);
    del.addEventListener("click", async () => {
      if (!confirm(`Delete "${l.title}" and all its questions? This cannot be undone.`)) return;
      await fetch(`/api/lectures/${l.id}`, { method: "DELETE" });
      toast("Lecture deleted");
      loadLearnList();
    });
    const regen = el.querySelector(`#regen-${l.id}`);
    regen.addEventListener("click", async () => {
      regen.disabled = true;
      regen.textContent = "Regenerating…";
      await fetch(`/api/lectures/${l.id}/regenerate`, { method: "POST" });
      toast("Regenerating captions + summary in background");
      setTimeout(loadLearnList, 1500);
    });
    list.appendChild(el);
  }

  if (anyGenerating) {
    setTimeout(loadLearnList, 3000);
  }
}

$("learn-view-toggle").addEventListener("change", () => {
  localStorage.setItem("learn-view", $("learn-view-toggle").checked ? "grid" : "list");
  loadLearnList();
});

async function openSummary(lectureId) {
  state.lectureId = lectureId;
  $("learn-lecture-list").hidden = true;
  $("learn-summary").hidden = false;
  const statusEl = $("summary-status");
  const bodyEl = $("summary-body");
  const kpEl = $("key-points");
  statusEl.textContent = "Loading summary...";
  bodyEl.innerHTML = "";
  kpEl.innerHTML = "";

  let res = await fetch(`/api/lectures/${lectureId}/summary`).then((r) => r.json());
  if (res.status === "done" && res.summary) {
    renderSummary(res.summary);
  } else if (res.status === "generating") {
    statusEl.textContent = "Summary is being generated — this refreshes automatically.";
    const poll = async () => {
      const again = await fetch(`/api/lectures/${lectureId}/summary`).then((r) => r.json());
      if (again.status === "done" && again.summary) {
        renderSummary(again.summary);
        statusEl.textContent = "";
      } else {
        setTimeout(poll, 3000);
      }
    };
    setTimeout(poll, 3000);
  } else {
    statusEl.textContent = "No summary yet — it's generated automatically right after import. Refresh in a moment.";
  }
  renderSourceSlides(lectureId);
}

function renderSourceSlides(lectureId) {
  const el = $("slide-deck");
  el.innerHTML = '<div class="muted">Loading slides…</div>';
  fetch(`/api/lectures/${lectureId}/deck`)
    .then((r) => r.json())
    .then((slides) => {
      const withContent = slides.filter((s) => s.text.trim() || (s.images && s.images.length));
      if (!withContent.length) {
        el.innerHTML = "";
        return;
      }
      const blocks = withContent.map((s) => {
        const text = s.text.trim()
          ? `<div class="deck-text">${escapeHtml(s.text)}</div>`
          : "";
        const caption = s.caption && s.caption.trim()
          ? `<div class="deck-caption">${escapeHtml(s.caption)}</div>`
          : "";
        const imgs = (s.images || [])
          .map((u) => `<img class="deck-img" src="${u}" loading="lazy" data-src="${u}">`)
          .join("");
        return `<div class="deck-slide" data-slide-num="${s.slide_num}">
          <div class="deck-num">Slide ${s.slide_num}</div>
          ${text}
          ${imgs ? `<div class="deck-imgs">${imgs}</div>` : ""}
          ${caption}
        </div>`;
      }).join("");
      el.innerHTML = `<h3 class="ss-title">Slides</h3>${blocks}`;
      el.querySelectorAll(".deck-img").forEach((img) => {
        img.addEventListener("click", () => openDeckLightbox(img.dataset.src, el));
      });
    })
    .catch(() => (el.innerHTML = ""));
}

function openDeckLightbox(src, container) {
  const imgs = [...container.querySelectorAll(".deck-img")];
  const idx = imgs.findIndex((i) => i.dataset.src === src);
  if (idx < 0) return;
  lbList = imgs.map((i) => i.dataset.src);
  lbIndex = idx;
  showLightbox();
}

function renderSummary(s) {
  $("summary-status").textContent = "";
  const bodyEl = $("summary-body");
  const kpEl = $("key-points");
  bodyEl.innerHTML = marked(s.body);
  const kps = JSON.parse(s.key_points || "[]");
  if (kps.length) {
    kpEl.innerHTML = "<h4>High-yield key points</h4><ul>" + kps.map((k) => `<li>${escapeHtml(k)}</li>`).join("") + "</ul>";
  }
}

$("learn-back").addEventListener("click", () => loadLearnList());

// ---------- Lightbox ----------
let lbList = [];
let lbIndex = 0;

function showLightbox() {
  if (!lbList.length || !lbList[lbIndex]) {
    closeLightbox();
    return;
  }
  const lb = $("lightbox");
  $("lb-img").src = lbList[lbIndex];
  $("lb-caption").textContent = `Slide ${lbIndex + 1} of ${lbList.length}`;
  lb.hidden = false;
  document.body.style.overflow = "hidden";
}

function closeLightbox() {
  $("lightbox").hidden = true;
  document.body.style.overflow = "";
}

$("lb-close").addEventListener("click", closeLightbox);
$("lb-prev").addEventListener("click", () => {
  lbIndex = (lbIndex - 1 + lbList.length) % lbList.length;
  showLightbox();
});
$("lb-next").addEventListener("click", () => {
  lbIndex = (lbIndex + 1) % lbList.length;
  showLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("lightbox").hidden) return closeLightbox();
    if (!$("slide-modal").hidden) return closeSlideModal();
  }
  if ($("lightbox").hidden) return;
  if (e.key === "ArrowLeft") $("lb-prev").click();
  if (e.key === "ArrowRight") $("lb-next").click();
});
$("lightbox").addEventListener("click", (e) => {
  if (e.target === $("lightbox")) closeLightbox();
});

// ---------- Slide citations (scroll deck to source slide) ----------
document.addEventListener("click", (e) => {
  const cite = e.target.closest(".slide-cite");
  if (!cite) return;
  e.preventDefault();
  const n = parseInt(cite.dataset.slide, 10);
  const deck = $("slide-deck");
  if (state.view === "learn" && deck) {
    // Learn view: scroll to the deck slide
    const el = deck.querySelector(`.deck-slide[data-slide-num="${n}"]`);
    if (!el) return;
    // clear previous highlights
    deck.querySelectorAll(".deck-slide.flash").forEach((d) => d.classList.remove("flash"));
    el.classList.add("flash");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => el.classList.remove("flash"), 2000);
    return;
  }
  // Drill / Review view: open the source slide in the modal
  let slide = state.currentSlide;
  let images = state.currentImages || [];
  const item = cite.closest(".review-item");
  if (item) {
    const idx = parseInt(item.dataset.qidx, 10);
    const rs = state.reviewSlides[idx];
    if (rs) { slide = rs; images = rs.images; }
  }
  openSlideModal(slide, images);
});

// ---------- Source slide modal (drill explanations / review) ----------
function openSlideModal(slide, images) {
  slide = slide || {};
  const num = slide.slide_num;
  $("sm-num").textContent = num ? `Slide ${num}` : "Source slide";
  $("sm-text").textContent = slide.text || "";
  $("sm-text").hidden = !slide.text;
  $("sm-caption").textContent = slide.caption || "";
  $("sm-caption").hidden = !slide.caption;
  const imgsEl = $("sm-imgs");
  if (images && images.length) {
    imgsEl.innerHTML = images.map((u) => `<img class="sm-img" src="${u}" loading="lazy">`).join("");
    wireImageZoom(imgsEl);
  } else {
    imgsEl.innerHTML = "";
  }
  $("slide-modal").hidden = false;
  document.body.style.overflow = "hidden";
}

function closeSlideModal() {
  $("slide-modal").hidden = true;
  document.body.style.overflow = "";
}

$("sm-close").addEventListener("click", closeSlideModal);
$("slide-modal").addEventListener("click", (e) => {
  if (e.target === $("slide-modal")) closeSlideModal();
});

// ---------- Image zoom (drill explanation / review / slide modal) ----------
function wireImageZoom(container, imgNodes) {
  const imgs = imgNodes !== undefined ? [...imgNodes] : [...container.querySelectorAll(".src-img, .sm-img")];
  imgs.forEach((img) => {
    img.addEventListener("click", () => {
      const all = [...container.querySelectorAll(".src-img, .sm-img")]
        .map((i) => i.getAttribute("src") || i.src);
      lbList = all;
      lbIndex = Math.max(0, all.indexOf(img.getAttribute("src") || img.src));
      showLightbox();
    });
  });
}

// ---------- Markdown (minimal) ----------
function parseSlideList(s) {
  // handle "6, 7, 8" and "6-9" and mixed
  const out = [];
  for (const part of String(s).split(",")) {
    const m = part.trim().match(/^(\d+)(?:\s*[-–]\s*(\d+))?$/);
    if (!m) continue;
    const a = parseInt(m[1], 10);
    if (m[2]) {
      const b = parseInt(m[2], 10);
      for (let i = a; i <= b; i++) out.push(i);
    } else {
      out.push(a);
    }
  }
  return out;
}

function marked(md) {
  let out = escapeHtml(md);
  // code spans
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  // slide citations: (slide 6) / (slides 6, 7, 8) / (slides 6-9)
  out = out.replace(/\(slides?\s+([^)]+)\)/gi, (m, nums) => {
    const slides = parseSlideList(nums);
    if (!slides.length) return m;
    const links = slides.map((n) =>
      `<a href="#" class="slide-cite" data-slide="${n}">slide ${n}</a>`
    ).join(", ");
    return `<span class="slide-cites">(${links})</span>`;
  });
  // bold/italic
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // headings
  out = out.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  out = out.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  out = out.replace(/^# (.*)$/gm, "<h1>$1</h1>");
  // tables (simple pipe tables)
  out = out.replace(/\n?(\|[^\n]+\|)\n\|[^\n]+\|/g, (m) => m.replace(/\n/g, "\n").replace(/\|[-: ]+\|/g, "|"));
  const tableBlock = /((?:\|[^\n]+\|\n){2,})/g;
  out = out.replace(tableBlock, (m) => {
    const lines = m.trim().split("\n").filter((l) => !/^\|[\s|:|-]+\|$/.test(l));
    const header = lines[0];
    const rows = lines.slice(1);
    return "<table><thead><tr>" + header.split("|").filter((c) => c.trim()).map((c) => `<th>${c.trim()}</th>`).join("") + "</tr></thead><tbody>" +
      rows.map((r) => "<tr>" + r.split("|").filter((c) => c.trim()).map((c) => `<td>${c.trim()}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
  });
  // lists
  out = out.replace(/^[-*] (.*)$/gm, "<li>$1</li>");
  out = out.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
  out = out.replace(/^(\d+)\. (.*)$/gm, "<li>$2</li>");
  // paragraphs
  out = out.replace(/\n\n+/g, "</p><p>");
  if (!out.startsWith("<")) out = "<p>" + out + "</p>";
  return out;
}

// ---------- Timer ----------
let timerInterval = null;
let timerSeconds = 0;
let timerCountingDown = false;

function startQuizCountdown(totalSeconds) {
  stopTimer();
  timerCountingDown = true;
  timerSeconds = Math.max(0, totalSeconds || 0);
  $("session-timer").hidden = false;
  $("session-timer").classList.remove("timer-danger");
  renderTimer();
  timerInterval = setInterval(() => {
    timerSeconds -= 1;
    renderTimer();
    if (timerSeconds <= 0) {
      stopTimer();
      onQuizTimeout();
    }
  }, 1000);
}

function stopTimer() {
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  const el = $("session-timer");
  if (el) el.hidden = true;
}

function renderTimer() {
  const el = $("session-timer");
  if (!el) return;
  const s = Math.max(0, timerSeconds);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  el.textContent = (timerCountingDown ? "− " : "") +
    `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  if (timerCountingDown) {
    el.classList.toggle("timer-danger", s < 60);
  }
}

// ---------- Drill ----------
let currentQid = null;

async function loadDrill() {
  stopTimer();
  $("drill-setup").hidden = false;
  $("drill-active").hidden = true;
  $("review-results").hidden = true;
  $("review-results").innerHTML = "";

  const modeSel = $("session-mode");
  const toggleCount = () => {
    $("count-wrap").style.display = modeSel.value === "practice" ? "" : "none";
  };
  modeSel.addEventListener("change", toggleCount);
  toggleCount();

  // time-mode segmented toggle
  const segBtns = [...$("time-mode-seg").querySelectorAll("button")];
  if (!state.segAttached) {
    state.segAttached = true;
    segBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        segBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const mode = btn.dataset.timeMode;
        $("time-mode-hint").querySelectorAll("span").forEach((s) => {
          s.hidden = s.dataset.hint !== mode;
        });
      });
    });
  }

  // populate lecture checkboxes
  const lectures = await fetch("/api/lectures").then((r) => r.json());
  const list = $("lecture-list");
  list.innerHTML = "";
  for (const l of lectures) {
    const lb = document.createElement("label");
    lb.className = "lp-item";
    lb.innerHTML = `<input type="checkbox" value="${l.id}" class="lp-check" checked> <span>${escapeHtml(l.title)}</span>`;
    list.appendChild(lb);
  }
  const allCheck = $("lecture-all");
  const toggleAll = () => {
    list.querySelectorAll(".lp-check").forEach((c) => {
      c.checked = allCheck.checked;
      c.disabled = allCheck.checked;
    });
  };
  allCheck.checked = true;
  allCheck.addEventListener("change", toggleAll);
  toggleAll();

  // missed summary panel
  await refreshMissedSummary();

  // past sessions panel
  await renderSessionHistory();
}

async function renderSessionHistory() {
  const el = $("session-history");
  let sessions = [];
  try {
    sessions = await fetch("/api/sessions").then((r) => r.json());
  } catch (e) {
    sessions = [];
  }
  if (!sessions.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = "<div class='due-total' style='margin-top:14px'>Past sessions</div>";
  const rows = sessions.map((s) => {
    const label = s.mode === "review" ? "Review" : "Practice";
    const timeMode = s.tutor_mode ? "tutor" : "quiz";
    const status = s.status === "completed" ? "done" : "in progress";
    const btn = s.status === "completed"
      ? `<button class="btn small ghost" data-act="review" data-id="${s.id}">Review</button>`
      : `<button class="btn small" data-act="resume" data-id="${s.id}">Resume</button>`;
    return `
      <div class="hist-row">
        <div class="hist-info">
          <span class="w-title">${escapeHtml(s.title)}</span>
          <span class="muted"> · ${label} · ${timeMode} · ${status}</span><br>
          <span class="muted">${s.completed_count}/${s.target_count} answered</span>
        </div>
        <div class="hist-actions">
          ${btn}
          <button class="btn small danger-text" data-act="del" data-id="${s.id}" title="Delete session">Delete</button>
        </div>
      </div>`;
  }).join("");
  el.innerHTML += `<div class="hist-list">${rows}</div>`;
  el.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.id, 10);
      if (btn.dataset.act === "resume") startExistingSession(id);
      else if (btn.dataset.act === "review") startCompletedSessionReview(id);
      else if (btn.dataset.act === "del") {
        if (!confirm("Delete this past session and its answers?")) return;
        await fetch(`/api/sessions/${id}`, { method: "DELETE" });
        toast("Session deleted");
        renderSessionHistory();
      }
    });
  });
}

async function refreshMissedSummary() {
  const panel = $("missed-summary");
  if (!panel) return;
  const m = await fetch("/api/missed").then((r) => r.json());
  if (m.count === 0) {
    panel.innerHTML = '<div class="muted">No missed questions to review.</div>';
    return;
  }
  const rows = (m.by_lecture || [])
    .map((d) => `<li><span class="w-title">${escapeHtml(d.title)}</span> <span class="due-badge has-due">${d.c} missed</span></li>`)
    .join("");
  panel.innerHTML = `
    <div class="due-total"><span class="stat">${m.count}</span> questions to review</div>
    ${rows ? `<ul class="detail-list due-list">${rows}</ul>` : ""}`;
}

$("start-session").addEventListener("click", async () => {
  const mode = $("session-mode").value;
  const target = parseInt($("session-target").value, 10) || 20;
  const timeMode = $("time-mode-seg").querySelector("button.active").dataset.timeMode;
  const fd = new FormData();
  fd.append("mode", mode);
  fd.append("target", target);
  fd.append("time_mode", timeMode);
  const allChecked = $("lecture-all").checked;
  if (!allChecked) {
    const checked = [...document.querySelectorAll(".lp-check:checked")].map((c) => c.value);
    checked.forEach((id) => fd.append("lecture_ids", id));
  }
  const statusEl = $("gen-status");
  statusEl.textContent = mode === "practice" ? "Generating fresh questions... (this may take ~30-60s)" : "Loading missed questions...";
  const res = await fetch("/api/sessions", { method: "POST", body: fd }).then((r) => r.json());
  statusEl.textContent = "";
  if (!res.question_count) {
    toast(res.message || "No questions available for this mode/source.");
    return;
  }
  state.sessionId = res.session_id;
  startExistingSession(res.session_id);
});

async function startExistingSession(sid) {
  state.sessionId = sid;
  $("drill-setup").hidden = true;
  $("drill-active").hidden = false;
  $("review-results").hidden = true;
  $("review-results").innerHTML = "";
  stopTimer();
  state.quizActive = false;
  state.position = 0;
  try {
    const sess = await fetch(`/api/sessions/${sid}`).then((r) => r.json());
    state.tutorMode = sess.session.tutor_mode !== 0;
    state.quizTotal = (sess.session.time_limit_min || 0) * 60;
    state.totalQuestions = sess.session.target_count || sess.questions.length;
    if (!state.tutorMode) {
      const elapsed = sess.session.elapsed_sec || 0;
      state.quizActive = true;
      startQuizCountdown(Math.max(0, state.quizTotal - elapsed));
    } else {
      // Tutor: continuous count-up timer across all questions until pause/submit.
      startTutorTimer(sess.session.elapsed_sec || 0);
    }
    // Resume at first unanswered position
    const firstUnanswered = sess.questions.findIndex((q) => !q.answered);
    state.position = firstUnanswered >= 0 ? firstUnanswered : 0;
  } catch (e) {
    state.tutorMode = true;
    state.quizTotal = 0;
    startTutorTimer(0);
  }
  loadQuestionAt(state.position);
}

function startTutorTimer(initialSec) {
  stopTimer();
  timerCountingDown = false;
  timerSeconds = initialSec || 0;
  $("session-timer").hidden = false;
  $("session-timer").classList.remove("timer-danger");
  renderTimer();
  timerInterval = setInterval(() => {
    timerSeconds += 1;
    renderTimer();
  }, 1000);
}

async function pauseActiveQuiz() {
  if (!state.sessionId) return;
  if (state.tutorMode) {
    // persist elapsed count-up time so resume restores it
    stopTimer();
    try {
      await fetch(`/api/sessions/${state.sessionId}/pause`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ elapsed_sec: Math.round(timerSeconds) }),
      });
    } catch (e) { console.error("pause failed", e); }
    return;
  }
  if (!state.quizActive) return;
  const elapsed = Math.max(0, state.quizTotal - timerSeconds);
  state.quizActive = false;
  stopTimer();
  try {
    await fetch(`/api/sessions/${state.sessionId}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ elapsed_sec: Math.round(elapsed) }),
    });
  } catch (e) {
    console.error("pause failed", e);
  }
}

async function startCompletedSessionReview(sid) {
  state.sessionId = sid;
  $("drill-setup").hidden = true;
  $("drill-active").hidden = false;
  stopTimer();
  $("question-card").innerHTML = "";
  $("review-results").hidden = true;
  await showReviewResults();
}

async function onQuizTimeout() {
  stopTimer();
  toast("Time's up — grading as incorrect");
  try {
    await fetch(`/api/sessions/${state.sessionId}/timeout`, { method: "POST" });
  } catch (e) {
    console.error("timeout grading failed", e);
  }
  await showReviewResults();
}

async function submitQuiz() {
  stopTimer();
  await fetch(`/api/sessions/${state.sessionId}/submit`, { method: "POST" });
  toast("Quiz submitted for grading");
  await showReviewResults();
}

async function loadQuestionAt(pos) {
  const res = await fetch(`/api/sessions/${state.sessionId}/question/${pos}`).then((r) => r.json());
  const card = $("question-card");
  if (res.done) {
    showReviewResults();
    return;
  }
  state.position = pos;
  state.optionSelected = false;
  state.pendingIndex = null;
  state.pendingQid = null;
  currentQid = res.question.id;
  const q = res.question;
  state.currentImages = q.source_images || [];
  state.currentSlide = q.source_slide || null;

  const opts = q.options.map((o, i) => {
    const isCorrect = q.correct_index === i;
    return `<button class="option" data-idx="${i}" data-correct="${isCorrect ? 1 : 0}">
      <strong>${letter(i)}.</strong> ${escapeHtml(o)}
    </button>`;
  }).join("");

  const prog = $("session-progress");
  const sess = await fetch(`/api/sessions/${state.sessionId}`).then((r) => r.json());
  prog.textContent = `Question ${pos + 1} of ${sess.questions.length}` +
    ` · ${state.tutorMode ? "Tutor" : "Quiz"}`;

  const isLast = pos + 1 >= sess.questions.length;
  let nextBtnHtml = isLast
    ? (state.tutorMode ? `<button class="btn" id="next-q">Finish</button>` : "")
    : `<button class="btn" id="next-q" ${state.tutorMode ? "" : "disabled"}>Next</button>`;
  const navBtns = `
    <div class="quiz-nav">
      ${pos > 0 ? `<button class="btn ghost" id="prev-q">Back</button>` : ""}
      ${nextBtnHtml}
      ${!state.tutorMode ? `<button class="btn" id="submit-q">Submit quiz</button>` : ""}
    </div>`;

  card.innerHTML = `
    <div class="question-box">
      <div class="q-stem">${escapeHtml(q.question)}</div>
      ${opts}
      <div class="explanation" hidden>
        <h4>Explanation</h4>
        <p id="explain-text"></p>
        <div id="source-images"></div>
      </div>
      ${navBtns}
    </div>`;

  const nextBtn = card.querySelector("#next-q");
  const prevBtn = card.querySelector("#prev-q");
  const submitBtn = card.querySelector("#submit-q");
  state.submitBtn = submitBtn;

  const savedSelection = q.selected_index;

  // Already graded (tutor answered, or quiz after submit): view-only, colors shown.
  const graded = q.answered === 1 || (q.answered && q.answered !== 0);
  const gradedLocked = !state.tutorMode && q.answered;

  if (savedSelection !== null && savedSelection !== undefined && savedSelection !== -1) {
    const savedOpt = card.querySelector(`.option[data-idx="${savedSelection}"]`);
    if (savedOpt) savedOpt.classList.add("selected");
  }

  if (graded && q.correct_index !== null && q.correct_index !== undefined) {
    // Show correct/wrong for the recorded answer
    card.querySelectorAll(".option").forEach((b) => {
      const bi = parseInt(b.dataset.idx, 10);
      if (bi === q.correct_index) b.classList.add("correct");
      else if (savedSelection === bi && savedSelection !== q.correct_index) b.classList.add("wrong");
    });
    const exp = $("explain-text");
    if (exp) exp.innerHTML = marked(q.explanation || "No explanation provided.");
    const srcImg = $("source-images");
    if (srcImg && (state.currentImages && state.currentImages.length || state.currentSlide)) {
      if (state.currentImages && state.currentImages.length) {
        const cite = citationHTML(state.currentSlide);
        srcImg.innerHTML = `<h4 class="src-title">Source material <span class="muted">(click to zoom)</span></h4>` +
          state.currentImages.map((u) => `<img class="src-img" src="${u}" loading="lazy">`).join("") +
          cite;
        wireImageZoom(srcImg);
      } else if (state.currentSlide) {
        const cite = citationHTML(state.currentSlide);
        srcImg.innerHTML = cite ? `<h4 class="src-title">Source</h4>${cite}` : "";
      }
    }
    const expBox = $("question-card").querySelector(".explanation");
    if (expBox) expBox.hidden = false;
    // lock: disable all options for graded tutor view
    card.querySelectorAll(".option").forEach((b) => { b.disabled = true; });
    state.optionSelected = true;
  }

  card.querySelectorAll(".option").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (state.optionSelected) return;
      const idx = parseInt(btn.dataset.idx, 10);
      if (state.tutorMode) {
        // Tutor: immediate grading. Mark only the picked option wrong and the
        // correct option green (fixes all-answers-red bug).
        state.optionSelected = true;
        if (nextBtn) nextBtn.disabled = false;
        const correct = btn.dataset.correct === "1";
        btn.classList.add(correct ? "correct" : "wrong");
        document.querySelectorAll(".option").forEach((b) => {
          if (b.dataset.correct === "1" && !correct) b.classList.add("correct");
        });
        submitAnswer(q.id, idx, correct, true);
      } else {
        // Quiz: select freely, changeable until submit.
        if (gradedLocked) return;
        card.querySelectorAll(".option").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        state.pendingIndex = idx;
        state.pendingQid = q.id;
        if (nextBtn) nextBtn.disabled = false;
      }
    });
  });

  if (prevBtn) prevBtn.addEventListener("click", () => loadQuestionAt(pos - 1));
  if (nextBtn) nextBtn.addEventListener("click", async () => {
    if (state.tutorMode) {
      if (isLast) { showReviewResults(); return; }
      loadQuestionAt(pos + 1);
      return;
    }
    // Quiz: submit the pending selection, then advance.
    if (state.pendingIndex !== null) {
      await saveQuizSelection(state.pendingQid, state.pendingIndex);
      state.pendingIndex = null;
      state.pendingQid = null;
    }
    if (isLast) { showReviewResults(); return; }
    loadQuestionAt(pos + 1);
  });
  if (submitBtn) submitBtn.addEventListener("click", submitQuiz);
  refreshSubmitEnabled();
}

function letter(i) { return String.fromCharCode(65 + i); }

async function refreshSubmitEnabled() {
  // Enable "Submit quiz" once every question has a selection saved.
  if (state.tutorMode || !state.submitBtn) return;
  let sess = null;
  try {
    sess = await fetch(`/api/sessions/${state.sessionId}`).then((r) => r.json());
  } catch (e) { return; }
  const answered = sess.questions.filter((q) =>
    q.selected_index !== null && q.selected_index !== undefined
  ).length;
  state.submitBtn.disabled = answered < sess.questions.length;
  state.submitBtn.textContent = answered >= sess.questions.length
    ? "Submit quiz"
    : `Submit quiz (${answered}/${sess.questions.length})`;
}

async function saveQuizSelection(qid, index) {
  await fetch(`/api/sessions/${state.sessionId}/answer/${qid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_index: index }),
  });
}

async function submitAnswer(qid, selectedIndex, correct, reveal) {
  const res = await fetch(`/api/sessions/${state.sessionId}/answer/${qid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_index: selectedIndex }),
  }).then((r) => r.json());
  if (reveal) {
    const exp = $("explain-text");
    exp.innerHTML = marked(res.explanation || "No explanation provided.");
    const srcImg = $("source-images");
    if (state.currentImages && state.currentImages.length) {
      const cite = citationHTML(state.currentSlide);
      srcImg.innerHTML = `<h4 class="src-title">Source material <span class="muted">(click to zoom)</span></h4>` +
        state.currentImages.map((u) => `<img class="src-img" src="${u}" loading="lazy">`).join("") +
        cite;
      wireImageZoom(srcImg);
    } else if (state.currentSlide) {
      const cite = citationHTML(state.currentSlide);
      srcImg.innerHTML = cite ? `<h4 class="src-title">Source</h4>${cite}` : "";
    } else {
      srcImg.innerHTML = "";
    }
    $("question-card").querySelector(".explanation").hidden = false;
    if (!correct) toast("Missed — added to tomorrow's review");
    else toast("Correct");
  }
}

function citationHTML(slide) {
  if (!slide) return "";
  const title = (slide.lecture_title || "").trim();
  const num = slide.slide_num;
  if (!title && !num) return "";
  const cite = `Source: ${title || "Lecture"}${num ? ` · Slide ${num}` : ""}`;
  const cap = (slide.caption || "").trim();
  return `<div class="src-cite">${escapeHtml(cite)}${cap ? `<br><span class="muted">${escapeHtml(cap)}</span>` : ""}</div>`;
}

async function showReviewResults() {
  stopTimer();
  state.quizActive = false;
  const card = $("question-card");
  const review = $("review-results");
  review.hidden = false;
  const data = await fetch(`/api/sessions/${state.sessionId}/review`).then((r) => r.json());
  const qs = data.questions;
  const isCorrectQ = (q) => {
    if (q.selected_index === -1) return false;
    if (q.selected_index !== null && q.selected_index !== undefined) return q.selected_index === q.correct_index;
    return q.selected_correct === 1; // legacy answer (before selected_index tracking)
  };
  const correctCount = qs.filter(isCorrectQ).length;
  const timedOut = qs.filter((q) => q.selected_index === -1).length;
  state.reviewSlides = qs.map((q) => ({
    slide_num: q.slide_num,
    text: q.slide_text || "",
    caption: q.slide_caption || "",
    images: q.source_images || [],
  }));
  card.innerHTML = `
    <div class="session-done">
      <h2>Session complete</h2>
      <p class="muted">${correctCount}/${qs.length} correct${timedOut ? ` · ${timedOut} timed out` : ""}. Review below.</p>
    </div>`;
  review.innerHTML = `
    <h3>Review</h3>
    <div class="review-list">
      ${qs.map((q, i) => {
        const userPick = q.selected_index;
        const timedOut = userPick === -1;
        const isCorrect = isCorrectQ(q);
        const tag = timedOut
          ? '<span class="rev-tag rev-tag-time">Time expired</span>'
          : (isCorrect ? '<span class="rev-tag rev-tag-good">Correct</span>' : '<span class="rev-tag rev-tag-bad">Incorrect</span>');
        const imgs = (q.source_images || []).length
          ? `<div class="review-imgs">${q.source_images.map((u) => `<img class="src-img" src="${u}" loading="lazy">`).join("")}` +
            citationHTML({ lecture_title: q.lecture_title, slide_num: q.slide_num, caption: q.slide_caption }) + `</div>`
          : (citationHTML({ lecture_title: q.lecture_title, slide_num: q.slide_num, caption: q.slide_caption }) || "");
        return `
        <div class="review-item" data-qidx="${i}">
          <div class="review-q">
            <span class="review-num">${i + 1}.</span>
            <span class="q-stem">${escapeHtml(q.question)}</span>
            ${tag}
          </div>
          <div class="review-opts">
            ${q.options.map((o, oi) => {
              let cls = "rev-opt";
              if (oi === q.correct_index) cls = "rev-opt-correct";
              else if (oi === userPick) cls = "rev-opt-wrong";
              const mark = oi === q.correct_index ? " ✓" : (oi === userPick ? " ✗" : "");
              return `<div class="${cls}">${String.fromCharCode(65 + oi)}. ${escapeHtml(o)}${mark}</div>`;
            }).join("")}
          </div>
          <div class="review-explain"><strong>Explanation:</strong> ${marked(q.explanation || "None")}</div>
          ${imgs}
        </div>`;
      }).join("")}
    </div>
    <div style="margin-top:16px"><button class="btn" id="review-back">Back to setup</button></div>`;
  review.querySelectorAll(".review-item").forEach((item) => {
    wireImageZoom(item, item.querySelectorAll(".src-img"));
  });
  $("review-back").addEventListener("click", loadDrill);
  await refreshMissedSummary();
}

$("quit-session").addEventListener("click", () => {
  pauseActiveQuiz();
  stopTimer();
  toast("Session paused. You can resume it from the Drill tab.");
  loadDrill();
});

// ---------- Progress ----------
async function loadProgress() {
  const body = $("progress-body");
  const d = await fetch("/api/dashboard").then((r) => r.json());
  const lect = await fetch("/api/lectures").then((r) => r.json());
  body.innerHTML = "<div class='panel'><h3>Progress overview</h3></div>";
  body.innerHTML += `
    <div class="cards">
      <div class="card"><div class="stat">${d.question_count}</div><div class="label">Questions generated</div></div>
      <div class="card"><div class="stat">${d.missed_count}</div><div class="label">Missed to review</div></div>
      <div class="card"><div class="stat">${d.sessions_today}</div><div class="label">Sessions today</div></div>
      <div class="card"><div class="stat">${d.coverage.total}</div><div class="label">Slides total</div></div>
    </div>`;

  // Missed questions per lecture, for sorting
  const missedSum = await fetch("/api/missed").then((r) => r.json());
  const missedByLecture = {};
  (missedSum.by_lecture || []).forEach((x) => (missedByLecture[x.lecture_id] = x.c));

  // Slide coverage per lecture for sorting (from dashboard lecture_stats)
  const covByLecture = {};
  (d.lectures || []).forEach((x) => (covByLecture[x.lecture_id] = x.coverage_pct || 0));

  // Sort lectures: most missed first, then least covered
  lect.sort((a, b) => {
    const mb = (missedByLecture[b.id] || 0) - (missedByLecture[a.id] || 0);
    if (mb !== 0) return mb;
    return (covByLecture[a.id] || 0) - (covByLecture[b.id] || 0);
  });

  for (const l of lect) {
    const missedN = missedByLecture[l.id] || 0;
    const qs = await fetch(`/api/lectures/${l.id}/questions`).then((r) => r.json());
    const slides = await fetch(`/api/lectures/${l.id}/slides_progress`).then((r) => r.json());
    const slideRows = slides.map((s) => {
      const acc = s.accuracy === null || s.accuracy === undefined
        ? '<span class="muted">not reviewed</span>'
        : `<span class="acc-badge ${accClass(s.accuracy)}">${pct(s.accuracy)}</span> <span class="muted">(${s.correct_count}/${s.correct_count + s.wrong_count})</span>`;
      const qInfo = `${s.q_count} Q`;
      return `
        <div class="concept-row">
          <span class="concept-label" title="${escapeHtml(s.text)}">Slide ${s.slide_num}: ${escapeHtml(shortLabel(s.text, 70))}</span>
          <span class="concept-meta">${qInfo} · ${acc}</span>
        </div>`;
    }).join("");
    body.innerHTML += `
      <div class="lecture-row" style="cursor:default">
        <div>
          <div class="title">${escapeHtml(l.title)}</div>
          <div class="meta">${l.slide_count} slides · ${l.word_count} words</div>
        </div>
        <div class="meta">
          <span class="due-badge ${missedN ? "has-due" : ""}">${missedN} missed</span>
          · <span class="mini-bar"><span class="mini-fill" style="width:${covByLecture[l.id] || 0}%"></span></span>
          ${covByLecture[l.id] || 0}% covered · ${qs.length} questions generated
        </div>
      </div>
      <div class="concept-list">${slideRows}</div>`;
  }
}

// ---------- Helpers ----------
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------- Init ----------
initTheme();
loadDashboard();
