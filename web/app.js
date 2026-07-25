"use strict";

const view = document.getElementById("view");
const tabs = document.getElementById("tabs");

const state = {
  user: null,
  tab: "today",
  digest: null,
  activities: [],
  lesson: null,      // { id, content, pickedBy }
  step: 0,           // index into cards + questions
  answers: [],       // chosen option index per question
  revealed: false,
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

function paintStats() {
  const streak = document.getElementById("streakStat");
  const xp = document.getElementById("xpStat");
  if (!state.user) {
    streak.classList.add("hidden");
    xp.classList.add("hidden");
    return;
  }
  streak.classList.remove("hidden");
  xp.classList.remove("hidden");
  document.getElementById("streakNum").textContent = state.user.current_streak;
  document.getElementById("xpNum").textContent = state.user.xp;
}

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

/* --- today --- */

async function loadToday() {
  const [user, activities, digest] = await Promise.all([
    api("/api/auth/me"),   // refreshes the remaining-quota counter too
    api("/api/activities"),
    api("/api/digest/today"),
  ]);
  state.user = user;
  state.activities = activities;
  state.digest = digest.exists ? digest : null;
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
    </div>

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

function startLesson(lesson) {
  state.lesson = lesson;
  state.step = 0;
  state.answers = [];
  state.revealed = false;
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
      <button class="btn ghost small" id="quit">Exit</button>
      <div class="progress" style="flex:1;margin:0"><i style="width:${pct}%"></i></div>
      <span class="tiny muted">${state.step + 1}/${total}</span>
    </div>
    <div class="card" id="stage"></div>`;

  document.getElementById("quit").onclick = exitLesson;

  if (isCard) renderCard(cards[state.step], state.step === 0 ? content : null);
  else renderQuestion(questions[state.step - cards.length], state.step - cards.length);
}

function renderCard(card, header) {
  const stage = document.getElementById("stage");
  stage.innerHTML = `
    ${header ? `<div class="tag cat">${state.lesson.picked_by === "user" ? "Your pick" : "Tangent's pick"}</div>
      <h1 style="margin-top:10px">${esc(header.title)}</h1>
      <p class="muted">${esc(header.subtitle)} · ${Number(header.estimated_minutes) || 10} min</p>
      <hr style="border:0;border-top:1px solid var(--border);margin:18px 0">` : ""}
    <h2>${esc(card.heading)}</h2>
    <p>${prose(card.body)}</p>
    ${card.diagram_svg ? `<div class="diagram">${card.diagram_svg}</div>` : ""}
    ${(card.key_terms || []).length ? `<div class="terms">
      ${card.key_terms.map((t) => `<div class="term"><b>${esc(t.term)}</b> — ${esc(t.definition)}</div>`).join("")}
    </div>` : ""}
    <button class="btn wide" id="next" style="margin-top:20px">Continue</button>`;
  document.getElementById("next").onclick = () => { state.step++; renderLesson(); };
}

function renderQuestion(question, qIndex) {
  const stage = document.getElementById("stage");
  const chosen = state.answers[qIndex];
  const answered = state.revealed && chosen !== undefined;

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
      state.revealed = true;
      renderQuestion(question, qIndex);
    };
  });

  const next = document.getElementById("next");
  if (next) next.onclick = () => { state.step++; state.revealed = false; renderLesson(); };
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
    </div>`;
  document.getElementById("done").onclick = exitLesson;
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
            <div class="tiny muted">${h.picked_by === "app" ? "Tangent's pick" : "Your pick"}
              · ${h.score}/${h.total} · +${h.xp} XP</div>
          </div>
          <button class="btn small subtle" data-open="${h.id}">Review</button>
        </div>`).join("") : `<p class="muted small">Nothing finished yet.</p>`}
    </div>`;

  view.querySelectorAll("[data-open]").forEach((b) => {
    b.onclick = () => openLesson(Number(b.dataset.open), b);
  });
}

/* --- profile --- */

function renderProfile() {
  const u = state.user;
  view.innerHTML = `
    <div class="card">
      <h2>Your profile</h2>
      <p class="muted small">${esc(u.email)}</p>
      <form id="profileForm" class="stack" style="margin-top:14px">
        <div class="field"><label for="name">Display name</label>
          <input id="name" type="text" value="${esc(u.display_name)}"></div>
        <div class="field"><label for="roleEdit">What do you do?</label>
          <textarea id="roleEdit" rows="4">${esc(u.role)}</textarea>
          <div class="tiny muted" style="margin-top:6px">The more specific this is, the
            sharper the suggestions. Name your sector, your tools, your typical week.</div></div>
        <button class="btn" type="submit" id="saveProfile">Save</button>
      </form>
    </div>
    <div class="card">
      <button class="btn ghost" id="signout">Sign out</button>
    </div>`;

  document.getElementById("profileForm").onsubmit = async (e) => {
    e.preventDefault();
    const button = document.getElementById("saveProfile");
    busy(button, true, "Saving…");
    try {
      state.user = await api("/api/auth/me", {
        method: "PATCH",
        body: {
          display_name: document.getElementById("name").value,
          role: document.getElementById("roleEdit").value,
        },
      });
      busy(button, false);
      toast("Saved");
    } catch (err) { busy(button, false); toast(err.message); }
  };

  document.getElementById("signout").onclick = async () => {
    await api("/api/auth/signout", { method: "POST" });
    state.user = null;
    state.digest = null;
    render();
  };
}

/* ------------------------------------------------------------------ boot */

(async function boot() {
  try {
    state.user = await api("/api/auth/me");
    await loadToday();
    setTab("today");
  } catch {
    render();
  }
})();
