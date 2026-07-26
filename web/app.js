"use strict";

const view = document.getElementById("view");
const tabs = document.getElementById("tabs");

const state = {
  user: null,
  tab: "today",
  digest: null,
  activities: [],
  saved: [],         // shared lessons added from someone else's link
  observations: [],  // capture proposals awaiting a yes/no
  shared: null,      // { token, ... } when viewing /s/<token>
  lesson: null,      // { id, content, pickedBy }
  step: 0,           // index into cards + questions
  answers: [],       // chosen option index per question; also marks it answered
};

/* ------------------------------------------------------------------ utils */

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Card bodies are prose with **bold** and *italic* only — everything else is escaped.
const prose = (s) =>
  esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+?)\*/g, "$1<em>$2</em>")
    .replace(/\n{2,}/g, "</p><p>")
    .replace(/\n/g, "<br>");

function toast(message, ms = 2600) {
  document.querySelector(".toast")?.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function busy(button, on, label) {
  if (!button) return;
  button.disabled = on;
  if (on) {
    button.dataset.label = button.innerHTML;
    button.innerHTML = `<span class="spinner"></span> ${label || "Working…"}`;
  } else if (button.dataset.label) {
    button.innerHTML = button.dataset.label;
  }
}

const initials = (name, email) =>
  (String(name || email || "?").trim()[0] || "?").toUpperCase();

// Avatars are data: URLs we validated server-side (raster only, never SVG).
const avatarStyle = (url) => (url ? `background-image:url("${url}")` : "");

function applyAccent(accent) {
  const root = document.documentElement;
  if (accent) root.setAttribute("data-accent", accent);
  else root.removeAttribute("data-accent");
}

function paintStats() {
  const streak = document.getElementById("streakStat");
  const xp = document.getElementById("xpStat");
  const avatarBtn = document.getElementById("avatarBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const signedIn = !!state.user;

  for (const el of [streak, xp, avatarBtn, logoutBtn]) {
    el.classList.toggle("hidden", !signedIn);
  }
  applyAccent(signedIn ? state.user.accent : "");
  if (!signedIn) return;

  document.getElementById("streakNum").textContent = state.user.current_streak;
  document.getElementById("xpNum").textContent = state.user.xp;
  avatarBtn.style.cssText = avatarStyle(state.user.avatar);
  avatarBtn.textContent = state.user.avatar
    ? ""
    : initials(state.user.display_name, state.user.email);
}

async function signOut() {
  try { await api("/api/auth/signout", { method: "POST" }); } catch { /* leaving anyway */ }
  state.user = null;
  state.digest = null;
  state.lesson = null;
  state.shared = null;
  if (location.pathname !== "/") history.replaceState({}, "", "/");
  render();
}

/* The wordmark is the way home — from mid-lesson too, which is why it exits
   the lesson rather than just switching tab. */
document.getElementById("homeBtn").onclick = () => {
  if (!state.user) return render();
  state.lesson = null;
  state.shared = null;
  if (location.pathname !== "/") history.replaceState({}, "", "/");
  loadToday().then(() => setTab("today")).catch(() => setTab("today"));
};

document.getElementById("logoutBtn").onclick = signOut;
document.getElementById("avatarBtn").onclick = () => {
  state.lesson = null;
  state.shared = null;
  setTab("profile");
};

function setTab(tab) {
  state.tab = tab;
  [...tabs.querySelectorAll("button")].forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  render();
}

tabs.addEventListener("click", (e) => {
  const tab = e.target.closest("button")?.dataset.tab;
  if (tab) setTab(tab);
});

/* ------------------------------------------------------------------ views */

function render() {
  paintStats();
  if (state.shared) return renderShared();
  if (!state.user) return renderAuth();
  tabs.classList.remove("hidden");
  if (state.lesson) return renderLesson();
  if (state.tab === "progress") return renderProgress();
  if (state.tab === "profile") return renderProfile();
  return renderToday();
}

/* --- auth --- */

function renderAuth() {
  tabs.classList.add("hidden");
  view.innerHTML = `
    <div class="card center" style="margin-top:24px">
      <img src="/static/owl.svg" alt="" width="86" height="86">
      <h1 style="margin-top:8px">Learn the ring around your job</h1>
      <p class="muted">Tangent watches what you work on, then teaches you the
      subjects next door — the ones nobody assigns you.</p>
    </div>

    <div class="card">
      <div class="row" style="margin-bottom:16px">
        <button class="btn small" id="tabSignin">Sign in</button>
        <button class="btn small ghost" id="tabSignup">Create account</button>
      </div>
      <form id="authForm" class="stack">
        <div class="field"><label for="email">Email</label>
          <input id="email" type="email" autocomplete="username" required></div>
        <div class="field"><label for="password">Password</label>
          <input id="password" type="password" autocomplete="current-password" required></div>
        <div class="field hidden" id="roleField">
          <label for="role">What do you do?</label>
          <textarea id="role" placeholder="e.g. Pricing actuary — financial lines, UK market. Mostly PI and D&amp;O case pricing."></textarea>
          <div class="tiny muted" style="margin-top:6px">This is how Tangent knows what
            you already know, so it can suggest what's adjacent instead.</div>
        </div>
        <button class="btn wide" type="submit" id="authSubmit">Sign in</button>
      </form>
    </div>`;

  let mode = "signin";
  const setMode = (next) => {
    mode = next;
    document.getElementById("tabSignin").className =
      `btn small ${next === "signin" ? "" : "ghost"}`;
    document.getElementById("tabSignup").className =
      `btn small ${next === "signup" ? "" : "ghost"}`;
    document.getElementById("roleField").classList.toggle("hidden", next !== "signup");
    document.getElementById("authSubmit").textContent =
      next === "signin" ? "Sign in" : "Create account";
    document.getElementById("password").autocomplete =
      next === "signin" ? "current-password" : "new-password";
  };
  document.getElementById("tabSignin").onclick = () => setMode("signin");
  document.getElementById("tabSignup").onclick = () => setMode("signup");

  document.getElementById("authForm").onsubmit = async (e) => {
    e.preventDefault();
    const button = document.getElementById("authSubmit");
    busy(button, true);
    try {
      const payload = {
        email: document.getElementById("email").value,
        password: document.getElementById("password").value,
      };
      if (mode === "signup") payload.role = document.getElementById("role").value;
      state.user = await api(`/api/auth/${mode === "signin" ? "signin" : "signup"}`, {
        method: "POST", body: payload,
      });
      await loadToday();
      render();
    } catch (err) {
      busy(button, false);
      toast(err.message);
    }
  };
}

/* --- screen capture --- */

const CONFIDENCE_LABEL = { low: "not sure", medium: "fairly sure", high: "confident" };

function captureCardHtml() {
  const running = window.TangentCapture && TangentCapture.isRunning();
  const supported = window.TangentCapture && TangentCapture.isSupported();

  if (!supported) {
    return `<div class="wip" style="cursor:default">
      <span class="dot"></span>
      <span class="label"><b>Record while you work</b>
        <span>Needs Chrome, Edge or Firefox on a desktop</span></span>
    </div>`;
  }

  if (running) {
    return `
      <div class="capture live">
        <div class="row">
          <span class="rec"></span>
          <div style="flex:1">
            <b>Watching your screen</b>
            <div class="tiny muted" id="captureStats">Taking a look every 20 seconds…</div>
          </div>
          <button class="btn small" id="stopCapture">Stop</button>
        </div>
        <p class="tiny muted" style="margin-top:10px">
          Nothing is recorded. Frames are checked, turned into notes, and dropped —
          you confirm each note before it reaches your log.
        </p>
      </div>`;
  }

  return `
    <button class="capture" id="startCapture">
      <span class="dot"></span>
      <span class="label">
        <b>Record while you work</b>
        <span>Tangent watches, writes your log, and asks you to confirm it</span>
      </span>
      <span class="badge-go">Start</span>
    </button>`;
}

function observationsHtml() {
  const items = state.observations || [];
  if (!items.length) return "";
  return `
    <div class="card">
      <h2>Did I get this right?</h2>
      <p class="muted small">From what was on your screen. Nothing here is in your
        log yet — keep what's right, bin the rest.</p>
      <div class="stack" style="margin-top:14px">
        ${items.map((o) => `
          <div class="observation" data-obs="${o.id}">
            <div class="row" style="align-items:flex-start">
              <div style="flex:1">
                <input type="text" class="obs-text" value="${esc(o.activity)}"
                  aria-label="Proposed activity">
                <div class="tiny muted" style="margin-top:6px">
                  <span class="tag">${esc(CONFIDENCE_LABEL[o.confidence] || o.confidence)}</span>
                  ${esc(o.evidence)}
                </div>
              </div>
            </div>
            <div class="row" style="margin-top:10px">
              <button class="btn small" data-confirm="${o.id}">Yes, log it</button>
              <button class="btn small ghost" data-reject="${o.id}">No</button>
            </div>
          </div>`).join("")}
      </div>
      <button class="btn ghost small wide" id="clearObs" style="margin-top:12px">
        Discard all of these</button>
    </div>`;
}

function repaintCapture() {
  const slot = document.getElementById("captureSlot");
  const obs = document.getElementById("observeSlot");
  if (!slot || !obs) return;
  slot.innerHTML = captureCardHtml();
  obs.innerHTML = observationsHtml();
  wireCapture();
}

function wireCapture() {
  const start = document.getElementById("startCapture");
  if (start) start.onclick = startCapture;

  const stopBtn = document.getElementById("stopCapture");
  if (stopBtn) stopBtn.onclick = async () => {
    busy(stopBtn, true, "Finishing…");
    await TangentCapture.stop();
    await refreshObservations();
    repaintCapture();
  };

  document.querySelectorAll("[data-confirm]").forEach((b) => {
    b.onclick = async () => {
      const row = b.closest(".observation");
      const text = row.querySelector(".obs-text").value.trim();
      busy(b, true, "Saving…");
      try {
        await api(`/api/capture/observations/${b.dataset.confirm}/confirm`, {
          method: "POST", body: { activity: text },
        });
        state.observations = state.observations.filter((o) => String(o.id) !== b.dataset.confirm);
        state.activities = await api("/api/activities");
        renderToday();
      } catch (err) { busy(b, false); toast(err.message); }
    };
  });

  document.querySelectorAll("[data-reject]").forEach((b) => {
    b.onclick = async () => {
      try {
        await api(`/api/capture/observations/${b.dataset.reject}/reject`, { method: "POST" });
        state.observations = state.observations.filter((o) => String(o.id) !== b.dataset.reject);
        repaintCapture();
      } catch (err) { toast(err.message); }
    };
  });

  const clear = document.getElementById("clearObs");
  if (clear) clear.onclick = async () => {
    try {
      await api("/api/capture/observations", { method: "DELETE" });
      state.observations = [];
      repaintCapture();
    } catch (err) { toast(err.message); }
  };
}

async function refreshObservations() {
  try {
    const data = await api("/api/capture/pending");
    state.observations = data.observations || [];
  } catch { /* not signed in, or offline */ }
}

async function startCapture() {
  try {
    await TangentCapture.start();
  } catch (err) {
    // Denying the browser prompt lands here — not an error worth shouting about.
    if (err && (err.name === "NotAllowedError" || /denied|permission/i.test(err.message))) {
      toast("No problem — you can always log the day by hand.");
    } else {
      toast(err.message || "Couldn't start screen sharing.");
    }
    return;
  }
  repaintCapture();
  toast("Watching. Stop any time — from here or your browser's sharing bar.");
}

if (window.TangentCapture) {
  TangentCapture.on("observations", (items) => {
    state.observations = (state.observations || []).concat(items);
    if (state.tab === "today" && !state.lesson && !state.shared) repaintCapture();
    toast(`${items.length} new ${items.length === 1 ? "note" : "notes"} to check`);
  });

  TangentCapture.on("stats", (s) => {
    const el = document.getElementById("captureStats");
    if (!el) return;
    const mins = Math.max(1, Math.round((Date.now() - s.startedAt) / 60000));
    el.textContent =
      `${mins} min · ${s.kept} frame${s.kept === 1 ? "" : "s"} kept · ` +
      `${s.skipped} skipped as unchanged`;
  });

  TangentCapture.on("stopped", (s) => {
    if (s.fromBrowser) {
      refreshObservations().then(repaintCapture);
      toast("Stopped sharing — that's the recording ended.");
    }
  });

  TangentCapture.on("error", (message) => toast(message));
}

/* --- today --- */

async function loadToday() {
  const [user, activities, digest, saved, pending] = await Promise.all([
    api("/api/auth/me"),   // refreshes the remaining-quota counter too
    api("/api/activities"),
    api("/api/digest/today"),
    api("/api/saved-lessons"),
    api("/api/capture/pending"),
  ]);
  state.user = user;
  state.activities = activities;
  state.digest = digest.exists ? digest : null;
  state.saved = saved;
  state.observations = pending.observations || [];
}

function categoryLabel(c) {
  return { domain: "Domain", technical: "Technical", regulatory: "Regulatory",
           commercial: "Commercial", frontier: "Frontier" }[c] || c;
}

function renderToday() {
  const logged = state.activities.length;
  const digest = state.digest;

  view.innerHTML = `
    <div class="card">
      <h2>What did you work on?</h2>
      <p class="muted small">One line per thing. Cases, models, searches, papers,
        meetings — whatever ate your day.</p>
      <form id="logForm" class="row" style="margin-top:14px">
        <input id="logText" type="text" placeholder="e.g. Priced a PI claim for a solicitors' firm — missed limitation date" required>
        <button class="btn" type="submit">Add</button>
      </form>
      <div id="logList" style="margin-top:12px">
        ${logged
          ? state.activities.map((a) => `
              <div class="logrow">
                <div style="flex:1">${esc(a.text)}</div>
                <button class="x" data-del="${a.id}" title="Remove">✕</button>
              </div>`).join("")
          : `<p class="muted small">Nothing logged today yet.</p>`}
      </div>

      <div id="captureSlot">${captureCardHtml()}</div>
    </div>

    <div id="observeSlot">${observationsHtml()}</div>

    ${state.saved.length ? `
      <div class="card">
        <h2>Shared with you</h2>
        <div class="stack" style="margin-top:12px">
          ${state.saved.map((l) => `
            <div class="topic" style="cursor:default">
              <span class="tag cat">${l.author ? `From ${esc(l.author)}` : "Shared"}</span>
              ${l.completed ? `<span class="tag">Done ${l.score}/${l.total_questions}</span>` : ""}
              <h3 style="margin-top:10px">${esc(l.title)}</h3>
              <button class="btn small" data-lesson="${l.id}" style="margin-top:10px">
                ${l.completed ? "Review" : "Start lesson"}</button>
            </div>`).join("")}
        </div>
      </div>` : ""}

    ${digest ? renderDigest(digest) : `
      <div class="card center">
        <h2>Ready for your evening?</h2>
        <p class="muted">Tangent reads today's log and finds six subjects sitting
          just outside your lane.</p>
        <button class="btn wide" id="buildDigest" ${logged ? "" : "disabled"}>
          Get today's topics</button>
        ${logged ? "" : `<p class="tiny muted" style="margin-top:10px">Log at least one thing first.</p>`}
      </div>`}

    ${state.user.lessons_left_today !== undefined ? `
      <p class="tiny muted center">${state.user.lessons_left_today} of
      ${state.user.daily_lesson_cap} lessons left today</p>` : ""}`;

  document.getElementById("logForm").onsubmit = async (e) => {
    e.preventDefault();
    const input = document.getElementById("logText");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    try {
      await api("/api/activities", { method: "POST", body: { text } });
      state.activities = await api("/api/activities");
      renderToday();
    } catch (err) { toast(err.message); }
  };

  view.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      try {
        await api(`/api/activities/${b.dataset.del}`, { method: "DELETE" });
        state.activities = await api("/api/activities");
        renderToday();
      } catch (err) { toast(err.message); }
    };
  });

  wireCapture();

  const build = document.getElementById("buildDigest");
  if (build) build.onclick = () => buildDigest(build, false);

  const refresh = document.getElementById("refreshDigest");
  if (refresh) refresh.onclick = () => buildDigest(refresh, true);

  view.querySelectorAll("[data-pick]").forEach((b) => {
    b.onclick = async () => {
      busy(b, true, "Picking…");
      try {
        state.digest = await api("/api/digest/today/choose", {
          method: "POST", body: { index: Number(b.dataset.pick) },
        });
        renderToday();
      } catch (err) { busy(b, false); toast(err.message); }
    };
  });

  view.querySelectorAll("[data-lesson]").forEach((b) => {
    b.onclick = () => openLesson(Number(b.dataset.lesson), b);
  });
}

async function buildDigest(button, refresh) {
  busy(button, true, refresh ? "Rethinking…" : "Reading your day…");
  try {
    state.digest = await api(`/api/digest/today${refresh ? "?refresh=true" : ""}`,
      { method: "POST" });
    renderToday();
  } catch (err) {
    busy(button, false);
    toast(err.message);
  }
}

function renderDigest(digest) {
  const topics = digest.topics || [];

  if (digest.chosen_index === null || digest.chosen_index === undefined) {
    return `
      <div class="card">
        <h2>Today's tangents</h2>
        <p class="muted small">${esc(digest.summary)}</p>
        <p class="small" style="margin:14px 0 12px"><b>Pick one.</b>
          Tangent picks the second — deliberately from somewhere else.</p>
        <div class="topics">
          ${topics.map((t, i) => `
            <button class="topic" data-pick="${i}">
              <span class="tag cat">${esc(categoryLabel(t.category))}</span>
              <span class="tag">${esc(t.difficulty)}</span>
              <h3 style="margin-top:10px">${esc(t.title)}</h3>
              <div class="small muted">${esc(t.blurb)}</div>
              <div class="why">${esc(t.why_now)}</div>
            </button>`).join("")}
        </div>
        <button class="btn ghost small" id="refreshDigest" style="margin-top:14px">
          Not feeling these — try again</button>
      </div>`;
  }

  const lessons = digest.lessons || [];
  return `
    <div class="card">
      <h2>Tonight's two</h2>
      <p class="muted small">${esc(digest.summary)}</p>
      <div class="stack" style="margin-top:16px">
        ${lessons.map((l) => `
          <div class="topic" style="cursor:default">
            <span class="tag cat">${l.picked_by === "user" ? "Your pick" : "Tangent's pick"}</span>
            ${l.completed ? `<span class="tag">Done ${l.score}/${l.total_questions}</span>` : ""}
            <h3 style="margin-top:10px">${esc(l.title)}</h3>
            <button class="btn small" data-lesson="${l.id}" style="margin-top:10px">
              ${l.completed ? "Review" : (l.ready ? "Continue" : "Start lesson")}</button>
          </div>`).join("")}
      </div>
    </div>`;
}

/* --- lesson player --- */

const WRITING_LINES = [
  "Reading around the topic…",
  "Working out what you already know…",
  "Sketching the diagrams…",
  "Writing the questions…",
  "Checking the explanations hold up…",
];

function lessonSourceLabel(lesson) {
  if (lesson.picked_by === "shared") return `From ${lesson.author || "someone"}`;
  return lesson.picked_by === "user" ? "Your pick" : "Tangent's pick";
}

function startLesson(lesson) {
  state.lesson = lesson;
  state.step = 0;
  state.answers = [];
  render();
}

async function openLesson(id, button) {
  busy(button, true, "Opening…");
  try {
    const res = await api(`/api/lessons/${id}`);
    if (res.status === "ready") return startLesson(res);
    if (res.status === "failed") {
      busy(button, false);
      toast(res.error || "That lesson failed to generate. Try again.");
      return;
    }
    waitForLesson(id);
  } catch (err) {
    busy(button, false);
    toast(err.message);
  }
}

/* Generation runs server-side in the background — poll rather than hold a
   request open for a minute, which platform proxies tend to cut. */
function waitForLesson(id) {
  tabs.classList.add("hidden");
  let line = 0;
  const paint = () => {
    view.innerHTML = `
      <div class="card center" style="margin-top:40px">
        <img src="/static/owl.svg" alt="" width="72" height="72">
        <h2 style="margin-top:10px">Writing your lesson</h2>
        <p class="muted" id="writingLine">${esc(WRITING_LINES[line])}</p>
        <div class="progress" style="margin-top:18px"><i style="width:${Math.min(90, 12 + line * 18)}%"></i></div>
        <p class="tiny muted" style="margin-top:14px">Usually 30–60 seconds. It's writing
          this one from scratch, for you.</p>
        <button class="btn ghost small" id="cancelWait" style="margin-top:14px">Back</button>
      </div>`;
    document.getElementById("cancelWait").onclick = () => { stop = true; exitLesson(); };
  };

  let stop = false;
  paint();
  const ticker = setInterval(() => {
    line = (line + 1) % WRITING_LINES.length;
    const el = document.getElementById("writingLine");
    if (el) el.textContent = WRITING_LINES[line];
  }, 6000);

  (async () => {
    for (let attempt = 0; attempt < 60 && !stop; attempt++) {
      await new Promise((r) => setTimeout(r, 2500));
      if (stop) break;
      let res;
      try { res = await api(`/api/lessons/${id}`); }
      catch { continue; }  // transient — keep polling
      if (res.status === "ready") { clearInterval(ticker); return startLesson(res); }
      if (res.status === "failed") {
        clearInterval(ticker);
        view.innerHTML = `
          <div class="card center" style="margin-top:40px">
            <h2>That didn't work</h2>
            <p class="muted">${esc(res.error || "The lesson failed to generate.")}</p>
            <button class="btn" id="retry">Try again</button>
            <button class="btn ghost" id="giveUp" style="margin-top:10px">Back to today</button>
          </div>`;
        document.getElementById("retry").onclick = (e) => openLesson(id, e.target);
        document.getElementById("giveUp").onclick = exitLesson;
        return;
      }
    }
    clearInterval(ticker);
    if (!stop) {
      toast("Still working — check back in a moment.");
      exitLesson();
    }
  })();
}

function exitLesson() {
  state.lesson = null;
  loadToday().then(render).catch(() => render());
}

function renderLesson() {
  tabs.classList.add("hidden");  // a lesson is a focus mode — no tab-hopping mid-question
  const { content } = state.lesson;
  const cards = content.cards || [];
  const questions = content.questions || [];
  const total = cards.length + questions.length;

  if (state.step >= total) return renderFinish();

  const pct = Math.round((state.step / total) * 100);
  const isCard = state.step < cards.length;

  view.innerHTML = `
    <div class="row" style="margin:16px 0 10px">
      <button class="btn ghost small" id="back" ${state.step === 0 ? "disabled" : ""}
        title="Previous step" aria-label="Previous step">←</button>
      <button class="btn ghost small" id="quit">Exit</button>
      <div class="progress" style="flex:1;margin:0"><i style="width:${pct}%"></i></div>
      <span class="tiny muted">${state.step + 1}/${total}</span>
    </div>
    <div class="card" id="stage"></div>`;

  document.getElementById("quit").onclick = exitLesson;
  document.getElementById("back").onclick = () => {
    if (state.step > 0) { state.step--; renderLesson(); }
  };

  if (isCard) renderCard(cards[state.step], state.step === 0 ? content : null);
  else renderQuestion(questions[state.step - cards.length], state.step - cards.length);
}

function renderCard(card, header) {
  const stage = document.getElementById("stage");
  stage.innerHTML = `
    ${header ? `<div class="tag cat">${esc(lessonSourceLabel(state.lesson))}</div>
      <h1 style="margin-top:10px">${esc(header.title)}</h1>
      <p class="muted">${esc(header.subtitle)} · ${Number(header.estimated_minutes) || 10} min</p>
      <hr style="border:0;border-top:1px solid var(--border);margin:18px 0">` : ""}
    <h2>${esc(card.heading)}</h2>
    <p>${prose(card.body)}</p>
    ${card.diagram_svg ? `<figure class="diagram">
      ${card.diagram_svg}
      ${card.diagram_caption ? `<figcaption>${esc(card.diagram_caption)}</figcaption>` : ""}
    </figure>` : ""}
    ${card.intuition ? `<div class="intuition">
      <b>The intuition</b>${prose(card.intuition)}</div>` : ""}
    ${(card.key_terms || []).length ? `<div class="terms">
      ${card.key_terms.map((t) => `<div class="term"><b>${esc(t.term)}</b> — ${esc(t.definition)}</div>`).join("")}
    </div>` : ""}
    <button class="btn wide" id="next" style="margin-top:20px">Continue</button>`;
  document.getElementById("next").onclick = () => { state.step++; renderLesson(); };
}

function renderQuestion(question, qIndex) {
  const stage = document.getElementById("stage");
  const chosen = state.answers[qIndex];
  // Derived per question, not a single flag — otherwise stepping back into an
  // answered question would show it blank and let you answer twice.
  const answered = chosen !== undefined;

  stage.innerHTML = `
    <div class="tag">Question ${qIndex + 1}</div>
    <h2 style="margin-top:12px">${esc(question.prompt)}</h2>
    <div class="options">
      ${question.options.map((opt, i) => {
        let cls = "option";
        if (answered && i === question.answer_index) cls += " correct";
        else if (answered && i === chosen) cls += " wrong";
        return `<button class="${cls}" data-opt="${i}" ${answered ? "disabled" : ""}>${esc(opt)}</button>`;
      }).join("")}
    </div>
    ${answered ? `
      <div class="verdict ${chosen === question.answer_index ? "correct" : "wrong"}">
        <b>${chosen === question.answer_index ? "Correct" : "Not quite"}</b>
        ${esc(question.explanation)}
      </div>
      <button class="btn wide" id="next">Continue</button>` : ""}`;

  stage.querySelectorAll("[data-opt]").forEach((b) => {
    b.onclick = () => {
      state.answers[qIndex] = Number(b.dataset.opt);
      renderQuestion(question, qIndex);
    };
  });

  const next = document.getElementById("next");
  if (next) next.onclick = () => { state.step++; renderLesson(); };
}

async function renderFinish() {
  const questions = state.lesson.content.questions || [];
  view.innerHTML = `<div class="card center"><span class="spinner"></span>
    <p class="muted">Marking…</p></div>`;

  let result;
  try {
    result = await api(`/api/lessons/${state.lesson.id}/complete`, {
      method: "POST",
      body: { answers: questions.map((_, i) => state.answers[i] ?? -1) },
    });
  } catch (err) {
    view.innerHTML = `<div class="card center"><p>${esc(err.message)}</p>
      <button class="btn" id="back">Back</button></div>`;
    document.getElementById("back").onclick = exitLesson;
    return;
  }

  state.user = { ...state.user, ...result };
  paintStats();

  const perfect = result.score === result.total && result.total > 0;
  view.innerHTML = `
    <div class="card center">
      <img src="/static/owl.svg" alt="" width="72" height="72">
      <h1>${perfect ? "Clean sweep." : result.score >= result.total / 2 ? "Nice work." : "Worth another pass."}</h1>
      <div class="bigscore">${result.score}<span class="muted" style="font-size:24px">/${result.total}</span></div>
      <div class="rewards">
        <div class="reward"><div class="n">+${result.xp_awarded}</div><div class="tiny muted">XP earned</div></div>
        <div class="reward"><div class="n">🔥 ${result.current_streak}</div><div class="tiny muted">day streak</div></div>
        <div class="reward"><div class="n">${result.level}</div><div class="tiny muted">level</div></div>
      </div>
      ${result.already_completed ? `<p class="tiny muted">Review run — no extra XP.</p>` : ""}
      <div class="levelbar"><i style="width:${result.xp_into_level}%"></i></div>
      <p class="tiny muted" style="margin-top:6px">${100 - result.xp_into_level} XP to level ${result.level + 1}</p>
      <button class="btn wide" id="done" style="margin-top:18px">Back to today</button>
      <button class="btn ghost wide" id="shareBtn" style="margin-top:10px">Share this lesson</button>
      <div id="shareSlot"></div>
    </div>`;
  document.getElementById("done").onclick = exitLesson;
  document.getElementById("shareBtn").onclick = (e) =>
    shareLesson(state.lesson.id, e.currentTarget, document.getElementById("shareSlot"));
}

/* --- sharing --- */

async function shareLesson(lessonId, button, slot) {
  busy(button, true, "Creating link…");
  try {
    const { path } = await api(`/api/lessons/${lessonId}/share`, { method: "POST" });
    const url = location.origin + path;
    busy(button, false);
    if (button) button.classList.add("hidden");
    slot.innerHTML = `
      <div class="sharebox">
        <input type="text" id="shareUrl" readonly value="${esc(url)}">
        <button class="btn small" id="copyShare">Copy</button>
      </div>
      <p class="tiny muted" style="margin-top:8px">Anyone with this link can read the
        lesson — no account needed. They can add it to their own Tangent to answer
        the questions and earn XP.</p>`;
    document.getElementById("copyShare").onclick = async () => {
      const input = document.getElementById("shareUrl");
      try {
        await navigator.clipboard.writeText(url);
        toast("Link copied");
      } catch {
        input.select();  // clipboard blocked (insecure context / permissions)
        toast("Press Ctrl+C to copy");
      }
    };
  } catch (err) {
    busy(button, false);
    toast(err.message);
  }
}

/* A shared link must work for someone with no account, so this view renders
   before any auth check. */
async function renderShared() {
  tabs.classList.add("hidden");
  const token = state.shared.token;

  if (!state.shared.content) {
    view.innerHTML = `<div class="card center"><span class="spinner"></span></div>`;
    try {
      state.shared = { ...state.shared, ...(await api(`/api/shared/${token}`)) };
    } catch (err) {
      view.innerHTML = `
        <div class="card center" style="margin-top:40px">
          <img src="/static/owl.svg" alt="" width="72" height="72">
          <h2>Link not found</h2>
          <p class="muted">${esc(err.message)}</p>
          <button class="btn" id="goHome">Go to Tangent</button>
        </div>`;
      document.getElementById("goHome").onclick = leaveShared;
      return;
    }
  }

  const s = state.shared;
  const cards = s.content.cards || [];
  const questions = s.content.questions || [];

  view.innerHTML = `
    <div class="card">
      <div class="byline">
        <span class="who" style="${avatarStyle(s.author_avatar)}">${
          s.author_avatar ? "" : esc(initials(s.author))}</span>
        <span><b>${esc(s.author)}</b> shared this lesson with you</span>
      </div>
      <h1>${esc(s.content.title || s.title)}</h1>
      <p class="muted">${esc(s.content.subtitle || s.blurb)}</p>
      <p class="tiny muted" style="margin-top:10px">${cards.length} cards ·
        ${questions.length} questions · ${Number(s.content.estimated_minutes) || 10} min</p>
      ${state.user ? `
        <button class="btn wide" id="addShared" style="margin-top:16px">
          Add to my lessons</button>
        <p class="tiny muted center" style="margin-top:8px">Free — no generation needed,
          and it counts towards your streak.</p>`
      : `
        <button class="btn wide" id="readShared" style="margin-top:16px">Read it</button>
        <button class="btn ghost wide" id="joinShared" style="margin-top:10px">
          Create an account to answer the questions</button>`}
    </div>

    ${!state.user ? cards.map((card) => `
      <div class="card">
        <h2>${esc(card.heading)}</h2>
        <p>${prose(card.body)}</p>
        ${card.diagram_svg ? `<figure class="diagram">${card.diagram_svg}
          ${card.diagram_caption ? `<figcaption>${esc(card.diagram_caption)}</figcaption>` : ""}
        </figure>` : ""}
        ${card.intuition ? `<div class="intuition"><b>The intuition</b>${prose(card.intuition)}</div>` : ""}
      </div>`).join("") : ""}`;

  const add = document.getElementById("addShared");
  if (add) add.onclick = async () => {
    busy(add, true, "Adding…");
    try {
      const { id } = await api(`/api/shared/${token}/add`, { method: "POST" });
      state.shared = null;
      history.replaceState({}, "", "/");
      await loadToday();
      openLesson(id, null);
    } catch (err) { busy(add, false); toast(err.message); }
  };

  const read = document.getElementById("readShared");
  if (read) read.onclick = () =>
    view.querySelectorAll(".card")[1]?.scrollIntoView({ behavior: "smooth", block: "start" });

  const join = document.getElementById("joinShared");
  if (join) join.onclick = leaveShared;
}

function leaveShared() {
  state.shared = null;
  history.replaceState({}, "", "/");
  render();
}

/* --- progress --- */

async function renderProgress() {
  view.innerHTML = `<div class="card center"><span class="spinner"></span></div>`;
  let data;
  try { data = await api("/api/progress"); }
  catch (err) { view.innerHTML = `<div class="card">${esc(err.message)}</div>`; return; }

  const active = new Set(data.active_days);
  // Local dates, not toISOString() — the server stamps days in its own local
  // time, and a UTC round-trip shifts the whole grid for anyone west of GMT.
  const localISO = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const cells = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const iso = localISO(d);
    cells.push(`<span class="${active.has(iso) ? "on" : ""}" title="${iso}"></span>`);
  }

  view.innerHTML = `
    <div class="card">
      <h2>Level ${data.level}</h2>
      <div class="levelbar"><i style="width:${data.xp_into_level}%"></i></div>
      <p class="tiny muted" style="margin-top:6px">${data.xp} XP total ·
        ${100 - data.xp_into_level} to next level</p>
      <div class="rewards">
        <div class="reward"><div class="n">🔥 ${data.current_streak}</div><div class="tiny muted">current streak</div></div>
        <div class="reward"><div class="n">${data.longest_streak}</div><div class="tiny muted">longest</div></div>
        <div class="reward"><div class="n">${data.lessons_completed}</div><div class="tiny muted">lessons</div></div>
      </div>
      <h3 style="margin-top:18px">Last 30 days</h3>
      <div class="heat">${cells.join("")}</div>
    </div>

    <div class="card">
      <h2>What you've learned</h2>
      ${data.history.length ? data.history.map((h) => `
        <div class="logrow">
          <div style="flex:1">
            <div>${esc(h.title)}</div>
            <div class="tiny muted">${
              h.picked_by === "shared"
                ? `From ${esc(h.author || "someone")}`
                : h.picked_by === "app" ? "Tangent's pick" : "Your pick"}
              · ${h.score}/${h.total} · +${h.xp} XP</div>
          </div>
          <button class="btn small subtle" data-share="${h.id}" title="Share this lesson">
            ${h.share_token ? "Link" : "Share"}</button>
          <button class="btn small subtle" data-open="${h.id}">Review</button>
        </div>`).join("") : `<p class="muted small">Nothing finished yet.</p>`}
      <div id="shareSlot"></div>
    </div>`;

  view.querySelectorAll("[data-open]").forEach((b) => {
    b.onclick = () => openLesson(Number(b.dataset.open), b);
  });

  view.querySelectorAll("[data-share]").forEach((b) => {
    b.onclick = () => shareLesson(Number(b.dataset.share), b, document.getElementById("shareSlot"));
  });
}

/* --- profile --- */

const ACCENTS = ["violet", "ember", "teal", "rose", "lime"];

/* Resize client-side before upload: a phone photo is several megabytes, and
   the avatar renders at 68px. Keeps the row small enough to live in Postgres. */
function resizeToDataUrl(file, size = 256) {
  return new Promise((resolve, reject) => {
    if (!/^image\/(png|jpe?g|webp)$/i.test(file.type)) {
      reject(new Error("Pick a PNG, JPEG or WebP image."));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Couldn't read that file."));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("That file isn't a readable image."));
      img.onload = () => {
        const side = Math.min(img.width, img.height);      // centre-crop to square
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = size;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, (img.width - side) / 2, (img.height - side) / 2,
                      side, side, 0, 0, size, size);
        resolve(canvas.toDataURL("image/jpeg", 0.85));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function renderProfile() {
  const u = state.user;
  let pendingAvatar;  // undefined = unchanged, null = remove, string = new image

  view.innerHTML = `
    <div class="card">
      <h2>Your profile</h2>
      <p class="muted small">${esc(u.email)}</p>

      <div class="avatar-edit" style="margin-top:16px">
        <div class="avatar-preview" id="avatarPreview" style="${avatarStyle(u.avatar)}">${
          u.avatar ? "" : esc(initials(u.display_name, u.email))}</div>
        <div class="stack" style="flex:1">
          <div class="row wrap">
            <button class="btn small subtle" id="pickAvatar" type="button">Upload photo</button>
            <button class="btn small ghost ${u.avatar ? "" : "hidden"}" id="removeAvatar" type="button">Remove</button>
          </div>
          <p class="tiny muted">Square works best. Resized to 256px in your browser
            before it's sent.</p>
        </div>
        <input type="file" id="avatarFile" accept="image/png,image/jpeg,image/webp" class="hidden">
      </div>

      <form id="profileForm" class="stack">
        <div class="field"><label for="name">Display name</label>
          <input id="name" type="text" value="${esc(u.display_name)}"></div>

        <div class="field"><label for="bioEdit">Short bio</label>
          <input id="bioEdit" type="text" maxlength="280" value="${esc(u.bio || "")}"
            placeholder="One line about you — shown when you share a lesson">
        </div>

        <div class="field"><label for="roleEdit">What do you do?</label>
          <textarea id="roleEdit" rows="4">${esc(u.role)}</textarea>
          <div class="tiny muted" style="margin-top:6px">The more specific this is, the
            sharper the suggestions. Name your sector, your tools, your typical week.</div></div>

        <div class="field"><label>Accent colour</label>
          <div class="swatches" id="swatches">
            ${ACCENTS.map((a) => `
              <button type="button" class="swatch ${a}" data-accent="${a}"
                aria-pressed="${(u.accent || "violet") === a}" title="${a}"></button>`).join("")}
          </div>
        </div>

        <button class="btn" type="submit" id="saveProfile">Save</button>
      </form>
    </div>

    <div class="card">
      <button class="btn ghost wide" id="signout">Log out</button>
    </div>`;

  const preview = document.getElementById("avatarPreview");
  const fileInput = document.getElementById("avatarFile");
  const removeBtn = document.getElementById("removeAvatar");

  document.getElementById("pickAvatar").onclick = () => fileInput.click();
  fileInput.onchange = async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    try {
      pendingAvatar = await resizeToDataUrl(file);
      preview.style.cssText = avatarStyle(pendingAvatar);
      preview.textContent = "";
      removeBtn.classList.remove("hidden");
    } catch (err) { toast(err.message); }
    fileInput.value = "";  // let the same file be picked again after a failure
  };

  removeBtn.onclick = () => {
    pendingAvatar = null;
    preview.style.cssText = "";
    preview.textContent = initials(
      document.getElementById("name").value || u.display_name, u.email);
    removeBtn.classList.add("hidden");
  };

  let accent = u.accent || "violet";
  document.getElementById("swatches").onclick = (e) => {
    const button = e.target.closest("[data-accent]");
    if (!button) return;
    accent = button.dataset.accent;
    view.querySelectorAll("[data-accent]").forEach((s) =>
      s.setAttribute("aria-pressed", String(s.dataset.accent === accent)));
    applyAccent(accent === "violet" ? "" : accent);  // live preview
  };

  document.getElementById("profileForm").onsubmit = async (e) => {
    e.preventDefault();
    const button = document.getElementById("saveProfile");
    busy(button, true, "Saving…");
    try {
      const body = {
        display_name: document.getElementById("name").value,
        role: document.getElementById("roleEdit").value,
        bio: document.getElementById("bioEdit").value,
        accent: accent === "violet" ? "" : accent,
      };
      if (pendingAvatar !== undefined) body.avatar = pendingAvatar;
      state.user = await api("/api/auth/me", { method: "PATCH", body });
      pendingAvatar = undefined;
      paintStats();
      busy(button, false);
      toast("Saved");
    } catch (err) { busy(button, false); toast(err.message); }
  };

  document.getElementById("signout").onclick = signOut;
}

/* ------------------------------------------------------------------ boot */

(async function boot() {
  // /s/<token> is a real URL people paste around — resolve it before auth, so
  // a signed-out recipient still sees the lesson rather than a login wall.
  const sharedMatch = location.pathname.match(/^\/s\/([\w-]+)\/?$/);
  if (sharedMatch) state.shared = { token: sharedMatch[1] };

  try {
    state.user = await api("/api/auth/me");
    if (!state.shared) {
      await loadToday();
      setTab("today");
      return;
    }
    await loadToday();
  } catch { /* signed out — the shared view still renders */ }

  render();
})();
