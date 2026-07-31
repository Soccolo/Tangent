"use strict";

const view = document.getElementById("view");
const tabs = document.getElementById("tabs");

const state = {
  user: null,
  tab: "today",
  digest: null,
  activities: [],
  saved: [],         // shared lessons added from someone else's link
  introing: false,   // replaying the intro from Profile
  introStep: 0,
  introDraft: null,
  observations: [],  // capture proposals awaiting a yes/no
  shared: null,      // { token, ... } when viewing /s/<token>
  lesson: null,      // { id, content, pickedBy }
  step: 0,           // index into cards + questions
  answers: [],       // chosen option index per question; also marks it answered
  hints: {},         // question index -> wrong option removed by a paid hint
  growth: null,
  explore: {
    request: 0,
    nodeKey: "",
    jumpTo: "",
    visitSent: false,
    reviewRun: null,
    bossFeedback: null,
  },
  cosmetics: { owl: "", card: "", celebration: "" },
  growthPriming: null,
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

function applyAccent(accent) {
  const root = document.documentElement;
  if (accent) root.setAttribute("data-accent", accent);
  else root.removeAttribute("data-accent");
  try {
    if (accent) localStorage.setItem("tangent.accent", accent);
    else localStorage.removeItem("tangent.accent");
  } catch { /* private mode */ }
}

const systemPrefersLight = () =>
  window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;

const browserTimezone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }
  catch { return ""; }
};

/* "system" is resolved to a concrete value here, matching the inline script in
   index.html, so the CSS only ever sees data-theme="light" or "dark". */
function applyTheme(theme) {
  const pref = theme || "system";
  const light = pref === "light" || (pref === "system" && systemPrefersLight());
  document.documentElement.setAttribute("data-theme", light ? "light" : "dark");
  try { localStorage.setItem("tangent.theme", pref); } catch { /* private mode */ }
}

function cosmeticSlot(value) {
  const slot = String(value || "").toLowerCase();
  if (slot.includes("owl") || slot.includes("accessor")) return "owl";
  if (slot.includes("desk")) return "desk";
  if (slot.includes("card") || slot.includes("skin")) return "card";
  if (slot.includes("celebr") || slot.includes("confetti") || slot.includes("burst")) {
    return "celebration";
  }
  return "";
}

function cosmeticKey(value) {
  const raw = typeof value === "object" && value
    ? value.item_key ?? value.key ?? value.id ?? value.name
    : value;
  return String(raw || "").toLowerCase().replace(/[^a-z0-9_-]/g, "");
}

const OWL_ACCESSORY_NAMES = {
  owl_constellation_pin: "Constellation pin",
  owl_star_pin: "North Star pin",
  owl_scholar_cap: "Scholar's cap",
  owl_curious_scarf: "Curious scarf",
  owl_scarf: "Explorer scarf",
  owl_teal_bow: "Teal bow tie",
  owl_bow: "Teal bow",
  owl_star_glasses: "Star glasses",
};

function profilePictureAccessory(profilePicture, fallbackAccessory = "") {
  const descriptorAccessory = profilePicture?.kind === "owl"
    ? profilePicture.owl?.accessory
    : "";
  return cosmeticKey(descriptorAccessory || fallbackAccessory);
}

function owlAccessoryName(accessory) {
  const key = cosmeticKey(accessory);
  if (!key) return "Classic Tangent";
  const item = state.growth?.workshop?.items?.find((entry) =>
    cosmeticKey(entry.key ?? entry.item_key) === key);
  return item?.name || OWL_ACCESSORY_NAMES[key] || "custom look";
}

function owlAvatarMarkup(profilePicture, {
  fallbackAccessory = "", size = 32, mood = "idle",
  label = "Tangent owl", decorative = false,
} = {}) {
  const accessory = profilePictureAccessory(profilePicture, fallbackAccessory);
  return Owl.svg(size, mood, accessory, { label, decorative });
}

/* Both /auth/me and /growth may carry equipped cosmetics. Accept the compact
   slot map as well as item records so the global look applies immediately
   after boot, purchase or equip without coupling this client to one serializer. */
function cosmeticsFrom(source) {
  if (!source) return {};
  const workshop = source.workshop || {};
  const items = workshop.items || source.items || [];
  const equipped = workshop.equipped
    ?? source.equipped_cosmetics
    ?? source.workshop_equipped
    ?? source.cosmetics
    ?? source.equipped;
  const next = {};
  const itemMap = new Map(items.map((item) => [cosmeticKey(item.key ?? item.item_key), item]));

  if (Array.isArray(equipped)) {
    equipped.forEach((entry) => {
      const item = typeof entry === "string" ? itemMap.get(cosmeticKey(entry)) : entry;
      const slot = cosmeticSlot(item?.slot || item?.type);
      if (slot) next[slot] = cosmeticKey(item?.key ?? item?.item_key ?? entry);
    });
  } else if (equipped && typeof equipped === "object") {
    Object.entries(equipped).forEach(([rawSlot, value]) => {
      const slot = cosmeticSlot(rawSlot) || cosmeticSlot(value?.slot || value?.type);
      if (slot) next[slot] = cosmeticKey(value);
    });
  }

  items.filter((item) => item.equipped).forEach((item) => {
    const slot = cosmeticSlot(item.slot || item.type);
    if (slot) next[slot] = cosmeticKey(item.key ?? item.item_key);
  });

  const direct = {
    owl: source.owl_cosmetic ?? source.equipped_owl ?? source.owl_accessory
      ?? source.equipped_owl_accessory ?? source.profile_picture?.owl?.accessory,
    desk: source.desk_cosmetic ?? source.equipped_desk ?? source.desk_item
      ?? source.equipped_desk_item,
    card: source.card_cosmetic ?? source.equipped_card ?? source.card_skin
      ?? source.equipped_card_theme,
    celebration: source.celebration_cosmetic
      ?? source.equipped_celebration
      ?? source.celebration_style,
  };
  Object.entries(direct).forEach(([slot, value]) => {
    if (value !== undefined && value !== null) next[slot] = cosmeticKey(value);
  });
  return next;
}

function applyCosmetics(source, { clear = false } = {}) {
  const next = clear
    ? { owl: "", desk: "", card: "", celebration: "" }
    : cosmeticsFrom(source);
  state.cosmetics = clear ? next : { ...state.cosmetics, ...next };
  const root = document.documentElement;
  Object.entries(state.cosmetics).forEach(([slot, key]) => {
    const attr = `${slot}Cosmetic`;
    if (key) root.dataset[attr] = key;
    else delete root.dataset[attr];
  });
  const homeOwl = document.getElementById("homeOwl");
  if (homeOwl && window.Owl) homeOwl.innerHTML = Owl.svg(34, "idle", state.cosmetics.owl);
}

// Follow the OS live, but only while the user is actually on "system".
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if ((state.user?.theme || "system") === "system") applyTheme("system");
  });
}

function displayStreak(user) {
  return user?.streak_status?.expired ? 0 : (user?.current_streak || 0);
}

function paintStats() {
  const streak = document.getElementById("streakStat");
  const coin = document.getElementById("coinStat");
  const xp = document.getElementById("xpStat");
  const avatarBtn = document.getElementById("avatarBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const signedIn = !!state.user;
  const showChrome = signedIn && state.user.onboarded && !state.introing;

  for (const el of [streak, coin, xp, avatarBtn, logoutBtn]) {
    el.classList.toggle("hidden", !showChrome);
  }
  applyAccent(signedIn ? state.user.accent : "");
  if (signedIn) {
    applyTheme(state.user.theme);
    applyCosmetics(state.user);
  } else applyCosmetics(null, { clear: true });
  if (!signedIn) return;
  if (!state.growth) primeGrowthCosmetics();

  document.getElementById("streakNum").textContent = displayStreak(state.user);
  document.getElementById("coinNum").textContent = state.user.coins || 0;
  document.getElementById("xpNum").textContent = state.user.xp;
  avatarBtn.removeAttribute("style");
  avatarBtn.innerHTML = owlAvatarMarkup(state.user.profile_picture, {
    fallbackAccessory: state.cosmetics.owl,
    size: 30,
    decorative: true,
  });
}

function applyRewardPayload(data) {
  if (!state.user || !data) return;
  const equippedCosmetics = cosmeticsFrom(data);
  state.user = {
    ...state.user,
    coins: data.coins ?? state.user.coins,
    hint_tokens: data.hint_tokens ?? state.user.hint_tokens,
    streak_freezes: data.streak_freezes ?? state.user.streak_freezes,
    streak_status: data.streak_status ?? state.user.streak_status,
    reward_catalog: data.catalog || data.reward_catalog || state.user.reward_catalog,
    profile_picture: data.profile_picture ?? state.user.profile_picture,
    equipped_cosmetics: Object.keys(equippedCosmetics).length
      ? { ...(state.user.equipped_cosmetics || {}), ...equippedCosmetics }
      : state.user.equipped_cosmetics,
  };
  applyCosmetics(data);
  paintStats();
}

/* Appearance choices apply and persist on click rather than on Save.
   `state.user` is the source of truth for paintStats(), which runs on every
   render — so a change that only touched the DOM was reverted by the next
   navigation. Update state first (instant, survives navigation), then persist. */
async function savePreference(patch) {
  const previous = { ...state.user };
  state.user = { ...state.user, ...patch };
  if ("theme" in patch) applyTheme(state.user.theme);
  if ("accent" in patch) applyAccent(state.user.accent);

  try {
    state.user = await api("/api/auth/me", { method: "PATCH", body: patch });
  } catch (err) {
    state.user = previous;               // put it back if the server refused
    if ("theme" in patch) applyTheme(state.user.theme);
    if ("accent" in patch) applyAccent(state.user.accent);
    toast(err.message);
  }
}

async function signOut() {
  try { await api("/api/auth/signout", { method: "POST" }); } catch { /* leaving anyway */ }
  state.user = null;
  state.digest = null;
  state.lesson = null;
  state.shared = null;
  state.growth = null;
  state.growthPriming = null;
  state.explore.reviewRun = null;
  state.explore.bossFeedback = null;
  state.explore.visitSent = false;
  applyCosmetics(null, { clear: true });
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
document.getElementById("coinStat").onclick = () => {
  if (state.lesson) return toast("Finish or exit this lesson to visit Rewards.");
  setTab("rewards");
};
document.getElementById("streakStat").onclick = () => {
  if (!state.lesson) setTab("rewards");
};
document.getElementById("xpStat").onclick = () => {
  if (!state.lesson) setTab("progress");
};
document.getElementById("avatarBtn").onclick = () => {
  state.lesson = null;
  state.shared = null;
  setTab("profile");
};

function setTab(tab) {
  state.tab = tab;
  [...tabs.querySelectorAll("button")].forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  window.scrollTo({ top: 0, behavior: "auto" });
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
  // A new account meets Tangent before it meets the dashboard — the app makes
  // no sense until someone explains what it's for.
  if (!state.user.onboarded || state.introing) return renderIntro();
  tabs.classList.remove("hidden");
  if (state.lesson) return renderLesson();
  if (state.tab === "explore") return renderExplore();
  if (state.tab === "library") return renderLibrary();
  if (state.tab === "rewards") return renderRewards();
  if (state.tab === "progress") return renderProgress();
  if (state.tab === "profile") return renderProfile();
  return renderToday();
}

/* --- auth --- */

function renderAuth() {
  tabs.classList.add("hidden");
  view.innerHTML = `
    <div class="card center" style="margin-top:24px">
      ${Owl.render({ size: 104, mood: "wave" })}
      <div class="owl-bubble" id="authSay"></div>
      <h1 style="margin-top:16px">Learn the ring around your job</h1>
      <p class="muted">You already know what you do. Tangent teaches you the
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
      <button class="btn ghost small wide" id="forgotLink" style="margin-top:10px">
        Forgot your password?</button>
    </div>`;

  const authOwl = view.querySelector(".owl");
  Owl.say(document.getElementById("authSay"), "Hello — I'm Tangent.");
  setTimeout(() => Owl.setMood(authOwl, "happy"), 2400);

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
  document.getElementById("forgotLink").onclick = renderForgot;

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
      primeGrowthCosmetics();
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

/* --- meeting Tangent --- */

const AIM_CHIPS = [
  "Be useful in more conversations",
  "Move into a new area",
  "Keep up with my field",
  "Stop nodding along in meetings",
  "Plain curiosity",
];

function introSteps(user) {
  return [
    {
      mood: "wave",
      line: "Hello — I'm Tangent.",
      sub: "I turn the edges of your working day into a path towards broader range.",
    },
    {
      mood: "curious",
      line: "Start with the work you're already doing.",
      sub: "Type a few lines, or let screen capture propose a private activity log for "
         + "you to approve. I turn it into six useful subjects just outside your lane.",
      preview: "discover",
    },
    {
      mood: "thinking",
      line: "Choose a tangent. I'll widen it.",
      sub: "You pick one topic and I add a surprise from another direction. Each becomes "
         + "a visual lesson with questions, explanations and help when you need it.",
      preview: "learn",
    },
    {
      mood: "curious",
      line: "So — what do you do?",
      key: "role",
      placeholder: "e.g. Pricing actuary — financial lines, UK. Mostly PI and D&O case pricing.",
      hint: "Be specific. This is how I tell what you already know from what's next door.",
      rows: 3,
    },
    {
      mood: "thinking",
      line: "And what do you wish you understood better?",
      key: "learning_goals",
      placeholder: "e.g. The legal side of the claims I price. Modern ML, properly this time.",
      hint: "I'll lean towards these when today's work gives me the chance. Leave it blank if you'd rather be surprised.",
      rows: 3,
      optional: true,
    },
    {
      mood: "curious",
      line: "Last one. What would make this worth your time?",
      key: "aim",
      chips: AIM_CHIPS,
      placeholder: "Or say it in your own words…",
      optional: true,
    },
    {
      mood: "proud",
      line: "Your first tangent is waiting.",
      sub: `You have ${Number(user.coins) || 0} coins and ${Number(user.hint_tokens) || 0} hint token`
         + `${Number(user.hint_tokens) === 1 ? "" : "s"}. Finish lessons `
         + "to earn more, keep your streak alive, and unlock help when it matters.",
      last: true,
    },
  ];
}

function tourPreviewHtml(kind) {
  if (kind === "discover") return `
    <div class="tour-flow" aria-label="How Tangent finds subjects for you">
      <div class="tour-node"><span>✍️</span><b>Log or capture</b><small>You approve every line</small></div>
      <i aria-hidden="true">→</i>
      <div class="tour-node"><span>✦</span><b>Six directions</b><small>Built from today</small></div>
      <i aria-hidden="true">→</i>
      <div class="tour-node"><span>🦉</span><b>Two picks</b><small>Yours + a surprise</small></div>
    </div>`;
  if (kind === "learn") return `
    <div class="tour-grid" aria-label="What Tangent offers">
      <div class="tour-feature"><span>◫</span><div><b>Visual lessons</b><small>Cards, diagrams and key terms</small></div></div>
      <div class="tour-feature"><span>💡</span><div><b>Useful hints</b><small>Remove a wrong answer</small></div></div>
      <div class="tour-feature"><span>🔥</span><div><b>Daily momentum</b><small>Streaks, XP and levels</small></div></div>
      <div class="tour-feature"><span><i class="coin-mini" aria-hidden="true">T</i></span><div><b>Earn coins</b><small>Buy hints and streak freezes</small></div></div>
      <div class="tour-feature"><span>↻</span><div><b>3-minute reviews</b><small>Recall ideas when they are due</small></div></div>
      <div class="tour-feature"><span>✦</span><div><b>Your constellation</b><small>See your range light up</small></div></div>
      <div class="tour-feature"><span>⌁</span><div><b>Shared library</b><small>Learn and share without regenerating</small></div></div>
      <div class="tour-feature"><span>◎</span><div><b>Private circles</b><small>Shared goals, never rankings</small></div></div>
    </div>`;
  return "";
}

function renderIntro() {
  tabs.classList.add("hidden");
  const steps = introSteps(state.user);
  state.introDraft = state.introDraft || {
    role: state.user.role || "",
    learning_goals: state.user.learning_goals || "",
    aim: state.user.aim || "",
  };
  let i = Math.min(state.introStep || 0, steps.length - 1);

  let stopSpeech = () => {};
  let settleTimer;
  const paint = () => {
    stopSpeech();
    clearTimeout(settleTimer);
    const step = steps[i];
    const value = step.key ? state.introDraft[step.key] : "";
    view.innerHTML = `
      <div class="card intro" style="margin-top:18px">
        <div class="intro-progress">
          <span>Step ${i + 1} of ${steps.length}</span>
          <div class="step-dots" aria-hidden="true">
          ${steps.map((_, n) => `<i class="${n <= i ? "on" : ""}"></i>`).join("")}
          </div>
        </div>
        ${Owl.render({ size: 108, mood: step.mood })}
        <div class="owl-bubble" id="introSay"></div>
        ${step.sub ? `<p class="muted small" id="introSub" style="margin-top:14px;opacity:0">${esc(step.sub)}</p>` : ""}
        ${step.preview ? tourPreviewHtml(step.preview) : ""}
        ${step.key ? `
          <div class="field">
            ${step.chips ? `<div class="chips" id="introChips">
              ${step.chips.map((c) => `<button type="button" class="chip"
                data-chip="${esc(c)}" aria-pressed="${value === c}">${esc(c)}</button>`).join("")}
            </div>` : ""}
            <textarea id="introInput" rows="${step.rows || 2}"
              placeholder="${esc(step.placeholder || "")}"
              style="margin-top:${step.chips ? "12px" : "0"}">${esc(value)}</textarea>
            ${step.hint ? `<div class="tiny muted" style="margin-top:6px">${esc(step.hint)}</div>` : ""}
          </div>` : ""}
        <button class="btn wide" id="introNext" style="margin-top:16px">
          ${step.last ? "Let's go" : "Continue"}</button>
        <div class="row" style="justify-content:center;margin-top:10px">
          ${i > 0 ? `<button class="btn ghost small" id="introBack">Back</button>` : ""}
          ${!step.last && (i < 3 || step.optional) ? `<button class="btn ghost small" id="introSkip">${
            i < 3 ? "Skip tour" : "Skip this question"}</button>` : ""}
        </div>
      </div>`;
    window.scrollTo({ top: 0, behavior: "auto" });

    const owl = view.querySelector(".owl");
    stopSpeech = Owl.say(document.getElementById("introSay"), step.line, {
      done: () => {
        const sub = document.getElementById("introSub");
        if (sub) sub.style.transition = "opacity .4s ease", sub.style.opacity = "1";
        const input = document.getElementById("introInput");
        if (input && !step.chips) input.focus();
      },
    });
    // Settle to a resting expression once the greeting animation has played.
    if (step.mood === "wave") {
      settleTimer = setTimeout(() => Owl.setMood(owl, "happy"), 2400);
    }

    const chips = document.getElementById("introChips");
    if (chips) chips.onclick = (e) => {
      const chip = e.target.closest("[data-chip]");
      if (!chip) return;
      const picked = chip.dataset.chip;
      const already = chip.getAttribute("aria-pressed") === "true";
      view.querySelectorAll("[data-chip]").forEach((c) =>
        c.setAttribute("aria-pressed", String(!already && c.dataset.chip === picked)));
      document.getElementById("introInput").value = already ? "" : picked;
    };

    const capture = () => {
      const input = document.getElementById("introInput");
      if (input && step.key) state.introDraft[step.key] = input.value.trim();
    };

    document.getElementById("introNext").onclick = async (e) => {
      capture();
      if (step.key && !step.optional && !state.introDraft[step.key]) {
        Owl.setMood(owl, "curious");
        Owl.say(document.getElementById("introSay"),
          "I do need this one — even a rough answer helps.", { typed: false });
        return;
      }
      if (!step.last) { i++; state.introStep = i; return paint(); }
      await finishIntro(e.currentTarget);
    };

    const back = document.getElementById("introBack");
    if (back) back.onclick = () => { capture(); i--; state.introStep = i; paint(); };

    const skip = document.getElementById("introSkip");
    if (skip) skip.onclick = (e) => {
      capture();
      if (i < 3) {
        i = 3;
        state.introStep = i;
        paint();
      } else {
        i++;
        state.introStep = i;
        paint();
      }
    };
  };

  paint();
}

async function finishIntro(button) {
  busy(button, true, "Setting up…");
  try {
    state.user = await api("/api/auth/me", {
      method: "PATCH",
      body: { ...state.introDraft, onboarded: true, timezone: browserTimezone() || undefined },
    });
    state.introing = false;
    state.introStep = 0;
    state.introDraft = null;
    await loadToday();
    setTab("today");
    toast("Welcome aboard.");
  } catch (err) { busy(button, false); toast(err.message); }
}

/* --- picking a topic: level, and optionally two diagnostic questions --- */

const LEVEL_HINT = [
  "",
  "Never come across it. Start from what the thing even is.",
  "Heard the term, couldn't define it.",
  "Could define it, couldn't use it.",
  "Know the basics, fuzzy on the mechanism.",
  "Comfortable with the fundamentals, new to the detail.",
  "Work near this. Skip the introductions.",
  "Use it occasionally. Go for the edge cases.",
  "Solid working knowledge. Show me what's contested.",
  "Near-practitioner. Assume notation and argument.",
  "Deep. Only the surprising parts are worth my time.",
];

function renderPick(index) {
  const topic = state.digest.topics[index];
  const level = state.pickLevel || state.user.default_level || 5;
  state.pickLevel = level;

  view.innerHTML = `
    <div class="card">
      <button class="btn ghost small" id="pickBack">← All topics</button>
      <div class="row wrap" style="gap:6px;margin-top:14px">
        <span class="tag cat">${esc(categoryLabel(topic.category))}</span>
        <span class="tag">${esc(topic.difficulty)}</span>
      </div>
      <h2 style="margin-top:10px">${esc(topic.title)}</h2>
      <p class="muted small">${esc(topic.blurb)}</p>

      <div class="levelpick">
        <label for="lvl">How much do you already know about this?</label>
        <div class="row">
          <span class="levelnum" id="lvlNum">${level}</span>
          <input type="range" id="lvl" min="1" max="10" step="1" value="${level}">
        </div>
        <div class="levelhint" id="lvlHint">${esc(LEVEL_HINT[level])}</div>
      </div>

      <button class="btn wide" id="startPick" style="margin-top:8px">
        Write my lesson</button>
      <button class="btn ghost wide" id="takePlacement" style="margin-top:10px">
        Not sure? Answer two quick questions</button>
      <p class="tiny muted center" style="margin-top:8px">Two questions about this topic
        so the lesson starts in the right place. Nothing is scored.</p>
      <div id="placementSlot"></div>
    </div>`;

  const slider = document.getElementById("lvl");
  const paint = () => {
    slider.style.setProperty("--fill", `${((slider.value - 1) / 9) * 100}%`);
    document.getElementById("lvlNum").textContent = slider.value;
    document.getElementById("lvlHint").textContent = LEVEL_HINT[slider.value];
    state.pickLevel = Number(slider.value);
  };
  paint();
  slider.oninput = paint;

  document.getElementById("pickBack").onclick = () => { state.pickLevel = null; renderToday(); };
  document.getElementById("startPick").onclick = (e) => confirmPick(index, [], e.currentTarget);
  document.getElementById("takePlacement").onclick = (e) => runPlacement(index, e.currentTarget);
}

async function runPlacement(index, button) {
  busy(button, true, "Writing two questions…");
  let questions;
  try {
    ({ questions } = await api("/api/digest/today/placement", {
      method: "POST", body: { index },
    }));
  } catch (err) { busy(button, false); toast(err.message); return; }
  busy(button, false);
  button.classList.add("hidden");
  document.getElementById("startPick").classList.add("hidden");

  const answers = [];
  const slot = document.getElementById("placementSlot");

  const paintQuestion = (qi) => {
    const q = questions[qi];
    slot.innerHTML = `
      <div class="placement">
        <div class="qnum">Question ${qi + 1} of ${questions.length}</div>
        <h3 style="margin-top:8px">${esc(q.prompt)}</h3>
        <div class="options">
          ${q.options.map((o, i) => `<button class="option" data-opt="${i}">${esc(o)}</button>`).join("")}
        </div>
      </div>`;
    slot.querySelectorAll("[data-opt]").forEach((b) => {
      b.onclick = () => {
        // Graded here, not on the server: it's a self-assessment, so there's
        // nothing to gain by cheating and no state worth storing.
        answers.push({
          probes: q.probes || "",
          prompt: q.prompt,
          correct: Number(b.dataset.opt) === q.answer_index,
        });
        if (qi + 1 < questions.length) paintQuestion(qi + 1);
        else finish();
      };
    });
  };

  const finish = () => {
    const correct = answers.filter((a) => a.correct).length;
    const self = state.pickLevel;
    const adjusted = correct === answers.length ? Math.min(10, self + 2)
      : correct === 0 ? Math.max(1, self - 3) : self;
    slot.innerHTML = `
      <div class="placement">
        <div class="verdict ${correct === answers.length ? "correct" : correct === 0 ? "wrong" : ""}"
          style="${correct && correct < answers.length ? "background:var(--surface-2)" : ""}">
          <b>${correct} of ${answers.length} right</b>
          ${adjusted > self
            ? `You know more than you gave yourself credit for — pitching this at ${adjusted} instead of ${self}.`
            : adjusted < self
            ? `We'll start further back than ${self} and build up, at ${adjusted}.`
            : `That matches roughly where you put yourself, so we'll keep ${self}.`}
        </div>
        <button class="btn wide" id="startAfter">Write my lesson</button>
      </div>`;
    document.getElementById("startAfter").onclick = (e) =>
      confirmPick(index, answers, e.currentTarget);
  };

  paintQuestion(0);
}

async function confirmPick(index, placement, button) {
  busy(button, true, "Setting up…");
  try {
    state.digest = await api("/api/digest/today/choose", {
      method: "POST",
      body: { index, level: state.pickLevel || 5, placement },
    });
    state.pickLevel = null;
    state.user = await api("/api/auth/me");
    renderToday();
  } catch (err) { busy(button, false); toast(err.message); }
}

/* --- password reset --- */

function renderForgot() {
  tabs.classList.add("hidden");
  view.innerHTML = `
    <div class="card" style="margin-top:24px">
      <h2>Reset your password</h2>
      <p class="muted small">Give us the email you signed up with and we'll send a
        link. It works once and expires within the hour.</p>
      <form id="forgotForm" class="stack" style="margin-top:14px">
        <div class="field"><label for="forgotEmail">Email</label>
          <input id="forgotEmail" type="email" autocomplete="username" required></div>
        <button class="btn wide" type="submit" id="forgotSubmit">Send the link</button>
      </form>
      <button class="btn ghost small wide" id="backToSignin" style="margin-top:10px">
        Back to sign in</button>
    </div>`;

  document.getElementById("backToSignin").onclick = renderAuth;
  document.getElementById("forgotForm").onsubmit = async (e) => {
    e.preventDefault();
    const button = document.getElementById("forgotSubmit");
    busy(button, true, "Sending…");
    try {
      const res = await api("/api/auth/forgot", {
        method: "POST",
        body: { email: document.getElementById("forgotEmail").value },
      });
      view.innerHTML = `
        <div class="card center" style="margin-top:24px">
          <img src="/static/owl.svg" alt="" width="72" height="72">
          <h2>Check your email</h2>
          <p class="muted">If that address has an account, a reset link is on its way.</p>
          ${res.delivery === "log" ? `<p class="tiny muted">Email isn't configured on
            this server — the link was written to the server log instead.</p>` : ""}
          <button class="btn" id="backToSignin2">Back to sign in</button>
        </div>`;
      document.getElementById("backToSignin2").onclick = renderAuth;
    } catch (err) { busy(button, false); toast(err.message); }
  };
}

function renderReset() {
  tabs.classList.add("hidden");
  view.innerHTML = `
    <div class="card" style="margin-top:24px">
      <h2>Choose a new password</h2>
      <form id="resetForm" class="stack" style="margin-top:14px">
        <div class="field"><label for="newPass">New password</label>
          <input id="newPass" type="password" autocomplete="new-password"
            minlength="8" required>
          <div class="tiny muted" style="margin-top:6px">At least 8 characters.
            Setting it signs out every other device.</div></div>
        <button class="btn wide" type="submit" id="resetSubmit">Set password</button>
      </form>
    </div>`;

  document.getElementById("resetForm").onsubmit = async (e) => {
    e.preventDefault();
    const button = document.getElementById("resetSubmit");
    busy(button, true, "Saving…");
    try {
      state.user = await api("/api/auth/reset", {
        method: "POST",
        body: { token: state.resetToken, password: document.getElementById("newPass").value },
      });
      state.resetToken = null;
      history.replaceState({}, "", "/");
      await loadToday();
      setTab("today");
      toast("Password changed — you're signed in.");
    } catch (err) {
      busy(button, false);
      toast(err.message);
    }
  };
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
  const timezone = browserTimezone();
  if (timezone && state.user.timezone !== timezone) {
    try {
      state.user = await api("/api/auth/me", {
        method: "PATCH", body: { timezone },
      });
    } catch { /* Streaks fall back to the server day until the next visit. */ }
  }
}

function categoryLabel(c) {
  return { domain: "Domain", technical: "Technical", regulatory: "Regulatory",
           commercial: "Commercial", frontier: "Frontier" }[c] || c;
}

function momentumHtml() {
  const u = state.user;
  const status = u.streak_status || {};
  const currentStreak = displayStreak(u);
  let message = "Finish a lesson today to start your streak.";
  if (status.active_today) message = "Today's streak is safe. Anything else is a bonus.";
  else if (status.expired) {
    message = "That streak has ended. Finish a lesson today to start a fresh one.";
  } else if (status.protected) {
    message = `${status.missed_days} missed day${status.missed_days === 1 ? "" : "s"} will be covered when you finish a lesson.`;
  } else if (status.at_risk) {
    message = `Your streak needs ${status.missed_days} freeze${status.missed_days === 1 ? "" : "s"} before your next finish.`;
  } else if (currentStreak > 0) {
    message = "Finish one lesson today to keep it moving.";
  }
  return `
    <button class="momentum" id="openRewards" type="button">
      <span class="momentum-flame">🔥</span>
      <span class="momentum-copy"><b>${currentStreak} day streak</b><small>${esc(message)}</small></span>
      <span class="momentum-wallet"><i class="coin-mini">T</i> ${u.coins || 0}</span>
    </button>`;
}

function renderToday() {
  const logged = state.activities.length;
  const digest = state.digest;

  view.innerHTML = `
    ${momentumHtml()}
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
  document.getElementById("openRewards").onclick = () => setTab("rewards");

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
    b.onclick = () => renderPick(Number(b.dataset.pick));
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

/* Tangent reacting to an answer. Varied openers so a run of questions doesn't
   read like the same stamp four times — and the explanation stays exactly where
   it was, just delivered by someone rather than by a box. */
const PRAISE = [
  "That's it.", "Exactly right.", "Nicely done.", "Spot on.",
  "Yes — good.", "That's the one.", "Correct, and quickly.",
];
const CONSOLE_ = [
  "Not quite — here's the thing.", "Close, but no.", "Ah, not this time.",
  "That's the common answer, but no.", "Not that one — look at this.",
];

function reactionHtml(correct, explanation, seed = 0) {
  const bank = correct ? PRAISE : CONSOLE_;
  const line = bank[(seed + (correct ? 0 : 2)) % bank.length];
  return `
    <div class="reaction ${correct ? "correct" : "wrong"}">
      ${Owl.svg(52, correct ? "happy" : "oops")}
      <div class="said">
        <b>${esc(line)}</b>
        <span>${esc(explanation)}</span>
      </div>
    </div>`;
}

function lessonSourceLabel(lesson) {
  if (lesson.picked_by === "library") return lesson.author ? `Library · ${lesson.author}` : "From the library";
  if (lesson.picked_by === "shared") return `From ${lesson.author || "someone"}`;
  return lesson.picked_by === "user" ? "Your pick" : "Tangent's pick";
}

function startLesson(lesson) {
  state.lesson = lesson;
  state.step = 0;
  state.answers = [];
  state.hints = lesson.hints || {};
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
        <p class="tiny muted" style="margin-top:14px">Written from scratch, for you.
          A minute or two is normal for a deep one.</p>
        <p class="tiny muted" id="writingClock">0s so far</p>
        <button class="btn ghost small" id="cancelWait" style="margin-top:14px">
          Leave it running</button>
        <p class="tiny muted" style="margin-top:8px">It keeps writing if you leave —
          it'll be waiting on your Today screen.</p>
      </div>`;
    document.getElementById("cancelWait").onclick = () => { stop = true; exitLesson(); };
  };

  let stop = false;
  paint();
  const ticker = setInterval(() => {
    line = (line + 1) % WRITING_LINES.length;
    const el = document.getElementById("writingLine");
    if (el) el.textContent = WRITING_LINES[line];
    const clock = document.getElementById("writingClock");
    if (clock) {
      const secs = Math.round((Date.now() - startedAt) / 1000);
      clock.textContent = secs < 90 ? `${secs}s so far` : `${Math.floor(secs / 60)}m ${secs % 60}s so far`;
    }
  }, 1000);

  const startedAt = Date.now();

  (async () => {
    // Poll until the server itself gives up (it times a stalled generation out
    // and returns "failed"). Backing off keeps a long wait cheap. Crucially we
    // never bail out on our own and dump the user on the home screen — the
    // lesson is still coming, and losing their place is worse than waiting.
    for (let attempt = 0; !stop; attempt++) {
      await new Promise((r) => setTimeout(r, attempt < 24 ? 2500 : 5000));
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
  const eliminated = state.hints[String(qIndex)];
  const hintPrice = state.user.reward_catalog?.hint?.price ?? 15;
  const hintTokens = Number(state.user.hint_tokens) || 0;

  stage.innerHTML = `
    <div class="tag">Question ${qIndex + 1}</div>
    <h2 style="margin-top:12px">${esc(question.prompt)}</h2>
    ${!answered ? `<div class="question-help">
      ${Number.isInteger(eliminated)
        ? `<div class="hint-reveal" role="status"><span>💡</span><span>One wrong answer is out. The rest is yours.</span></div>`
        : `<button class="btn hint-btn small" id="useHint" type="button">
            💡 ${hintTokens ? `Use hint · ${hintTokens} ready` : `Buy & use hint · ${hintPrice} coins`}
          </button>`}
    </div>` : ""}
    <div class="options">
      ${question.options.map((opt, i) => {
        let cls = "option";
        if (answered && i === question.answer_index) cls += " correct";
        else if (answered && i === chosen) cls += " wrong";
        else if (!answered && i === eliminated) cls += " eliminated";
        const disabled = answered || (!answered && i === eliminated);
        return `<button class="${cls}" data-opt="${i}" ${disabled ? "disabled" : ""}>${
          esc(opt)}${!answered && i === eliminated ? `<span class="eliminated-label">Removed</span>` : ""}</button>`;
      }).join("")}
    </div>
    ${answered ? reactionHtml(chosen === question.answer_index, question.explanation, qIndex)
      + `<button class="btn wide" id="next">Continue</button>` : ""}`;

  stage.querySelectorAll("[data-opt]").forEach((b) => {
    b.onclick = () => {
      state.answers[qIndex] = Number(b.dataset.opt);
      renderQuestion(question, qIndex);
    };
  });

  const hint = document.getElementById("useHint");
  if (hint) hint.onclick = () => useQuestionHint(qIndex, hint);

  const next = document.getElementById("next");
  if (next) next.onclick = () => { state.step++; renderLesson(); };
}

async function useQuestionHint(qIndex, button) {
  if (!state.lesson || state.answers[qIndex] !== undefined) return;
  const lessonId = state.lesson.id;
  busy(button, true, state.user.hint_tokens ? "Finding a clue…" : "Buying a hint…");
  try {
    if (!(Number(state.user.hint_tokens) > 0)) {
      const purchase = await api("/api/rewards/purchase", {
        method: "POST", body: { item: "hint" },
      });
      applyRewardPayload(purchase);
    }
    const result = await api("/api/rewards/hints/use", {
      method: "POST",
      body: { lesson_id: lessonId, question_index: qIndex },
    });
    applyRewardPayload(result);
    state.hints[String(qIndex)] = Number(result.eliminated_index);
    if (state.lesson?.id !== lessonId) return;
    const cards = state.lesson.content.cards || [];
    if (state.step === cards.length + qIndex) renderQuestion(
      state.lesson.content.questions[qIndex], qIndex);
  } catch (err) {
    busy(button, false);
    toast(err.message);
  }
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
  const half = result.score >= result.total / 2;
  view.innerHTML = `
    <div class="card center">
      ${Owl.render({ size: 96, mood: perfect ? "proud" : half ? "happy" : "curious" })}
      <div class="owl-bubble" id="finishSay"></div>
      <h1 style="margin-top:16px">${perfect ? "Clean sweep." : half ? "Nice work." : "Worth another pass."}</h1>
      <div class="bigscore">${result.score}<span class="muted" style="font-size:24px">/${result.total}</span></div>
      ${result.streak_saved ? `<div class="streak-saved" role="status">🧊 A streak freeze covered ${
        result.freezes_used} missed day${result.freezes_used === 1 ? "" : "s"}. Your streak lives.</div>` : ""}
      <div class="rewards">
        <div class="reward" data-reward="xp"><div class="n">+${result.xp_awarded}</div><div class="tiny muted">XP earned</div></div>
        <div class="reward coin-reward" data-reward="coins"><div class="n">+${result.coins_awarded}</div><div class="tiny muted">coins earned</div></div>
        <div class="reward" data-reward="streak"><div class="n">🔥 ${result.current_streak}</div><div class="tiny muted">day streak</div></div>
        <div class="reward" data-reward="level"><div class="n">${result.level}</div><div class="tiny muted">level</div></div>
      </div>
      ${result.already_completed ? `<p class="tiny muted">Review run — no extra XP or coins.</p>` : ""}
      <div class="levelbar"><i style="width:${result.xp_into_level}%"></i></div>
      <p class="tiny muted" style="margin-top:6px">${100 - result.xp_into_level} XP to level ${result.level + 1}</p>
      <button class="btn wide" id="done" style="margin-top:18px">Back to today</button>
      <button class="btn ghost wide" id="shareBtn" style="margin-top:10px">Share this lesson</button>
      <div id="shareSlot"></div>
    </div>`;
  Owl.say(document.getElementById("finishSay"),
    perfect ? "Every one. You knew more than you let on."
    : half ? `${result.score} out of ${result.total}. That'll stick.`
    : "The ones you missed are the ones worth rereading. Come back to it.");

  document.getElementById("done").onclick = exitLesson;
  document.getElementById("shareBtn").onclick = (e) =>
    shareLesson(state.lesson.id, e.currentTarget, document.getElementById("shareSlot"));
}

/* --- library --- */

async function renderLibrary(query) {
  const q = query === undefined ? (state.libraryQuery || "") : query;
  state.libraryQuery = q;
  view.innerHTML = `<div class="card center"><span class="spinner"></span></div>`;

  let data;
  try { data = await api(`/api/library?q=${encodeURIComponent(q)}`); }
  catch (err) { view.innerHTML = `<div class="card">${esc(err.message)}</div>`; return; }

  view.innerHTML = `
    <div class="card">
      <h2>Library</h2>
      <p class="muted small">Lessons already written — by you, or by anyone else using
        Tangent. Adding one is instant and costs nothing to generate.</p>
      <form id="libSearch" class="row" style="margin-top:14px">
        <input type="text" id="libQ" placeholder="Search topics…" value="${esc(q)}">
        <button class="btn" type="submit">Search</button>
      </form>
      <p class="tiny muted" style="margin-top:10px">${data.total} lesson${
        data.total === 1 ? "" : "s"} in the library</p>
    </div>

    ${data.lessons.length ? `<div class="topics">
      ${data.lessons.map((l) => `
        <div class="topic" style="cursor:default">
          <div class="row wrap" style="gap:6px">
            ${l.category ? `<span class="tag cat">${esc(categoryLabel(l.category))}</span>` : ""}
            ${l.difficulty ? `<span class="tag">${esc(l.difficulty)}</span>` : ""}
            ${l.times_used > 1 ? `<span class="tag">used ${l.times_used}×</span>` : ""}
          </div>
          <h3 style="margin-top:10px">${esc(l.title)}</h3>
          <div class="small muted">${esc(l.blurb)}</div>
          <div class="tiny muted" style="margin-top:8px">
            ${l.cards} cards · ${l.questions} questions${
              l.author ? ` · by ${esc(l.author)}` : ""}</div>
          <button class="btn small" data-add-lib="${l.id}" style="margin-top:10px"
            ${l.already_added ? "disabled" : ""}>
            ${l.already_added ? "In your lessons" : "Add to my lessons"}</button>
        </div>`).join("")}
    </div>` : `<div class="card center"><p class="muted">${
      q ? "Nothing matches that yet." : "The library fills up as lessons get written."}</p></div>`}`;

  document.getElementById("libSearch").onsubmit = (e) => {
    e.preventDefault();
    renderLibrary(document.getElementById("libQ").value);
  };

  view.querySelectorAll("[data-add-lib]").forEach((b) => {
    b.onclick = async () => {
      busy(b, true, "Adding…");
      try {
        const { id } = await api(`/api/library/${b.dataset.addLib}/add`, { method: "POST" });
        await loadToday();
        openLesson(id, null);
      } catch (err) { busy(b, false); toast(err.message); }
    };
  });
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
        <span class="who owl-avatar">${owlAvatarMarkup(s.author_profile_picture, {
          fallbackAccessory: s.author_owl_accessory || "",
          size: 24,
          decorative: true,
        })}</span>
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

/* --- explore -------------------------------------------------------------
   A single home for the more playful systems. All progress, rewards and
   correctness remain server-owned; this layer only presents them. */

const growthNumber = (value, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const growthPercent = (progress, target = 100) => {
  const total = Math.max(1, growthNumber(target, 100));
  return Math.max(0, Math.min(100, (growthNumber(progress) / total) * 100));
};

function rewardLabel(reward) {
  if (reward == null || reward === "") return "";
  if (typeof reward === "number") return `${reward} coins`;
  if (typeof reward === "string") return reward;
  const amount = reward.amount ?? reward.coins ?? reward.value;
  const name = reward.name ?? reward.label ?? reward.type ?? "reward";
  return amount == null ? String(name) : `${amount} ${name}`;
}

function growthWallet(data) {
  return { ...(data?.wallet || {}), ...(data || {}) };
}

function constellationNodeKey(node, index) {
  return String(node?.key ?? node?.id ?? node?.title ?? node?.name ?? `node-${index}`);
}

function constellationNodeTitle(node) {
  return node?.title ?? node?.name ?? node?.topic ?? node?.label ?? "New direction";
}

function constellationNodeProgress(node) {
  if (node?.progress != null || node?.mastery != null) {
    return growthNumber(node.progress ?? node.mastery);
  }
  if (node?.lesson_count) {
    return growthPercent(node.completed_count, node.lesson_count);
  }
  return node?.completed_count ? 100 : 0;
}

function constellationMarkup(constellation = {}) {
  const categories = Array.isArray(constellation.categories)
    ? constellation.categories.slice(0, 8)
    : [];
  const nodes = Array.isArray(constellation.nodes) ? constellation.nodes.slice(0, 16) : [];
  const dense = nodes.length > 8;
  const selected = nodes.find((node, index) =>
    constellationNodeKey(node, index) === state.explore.nodeKey);
  const role = constellation.role || state.user?.role || "Your work";

  const categoryButtons = categories.map((category, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index / Math.max(1, categories.length));
    const x = 50 + Math.cos(angle) * 32;
    const y = 50 + Math.sin(angle) * 32;
    const matchingNode = typeof category === "string"
      ? nodes.find((node) => String(node.key) === category)
      : null;
    const title = typeof category === "string"
      ? matchingNode?.label || category
      : category.title ?? category.name ?? category.label ?? "Adjacent";
    return `<span class="constellation-category" style="left:${x.toFixed(2)}%;top:${y.toFixed(2)}%">
      ${esc(title)}
    </span>`;
  }).join("");

  const nodeButtons = nodes.map((node, index) => {
    const innerCount = dense ? Math.ceil(nodes.length / 2) : nodes.length;
    const outerCount = Math.max(1, nodes.length - innerCount);
    const outer = dense && index >= innerCount;
    const ringIndex = outer ? index - innerCount : index;
    const ringCount = outer ? outerCount : innerCount;
    const offset = outer ? Math.PI / ringCount : 0;
    const angle = -Math.PI / 2 + offset + (Math.PI * 2 * ringIndex / Math.max(1, ringCount));
    const radius = dense ? (outer ? 44 : 30) : (index % 2 ? 44 : 40);
    const x = 50 + Math.cos(angle) * radius;
    const y = 50 + Math.sin(angle) * radius;
    const key = constellationNodeKey(node, index);
    const progress = constellationNodeProgress(node);
    const active = key === state.explore.nodeKey;
    const discovered = node.visited || node.complete || node.unlocked || progress > 0;
    return `<button class="constellation-node ${discovered ? "discovered" : ""} ${active ? "active" : ""}"
      style="left:${x.toFixed(2)}%;top:${y.toFixed(2)}%"
      data-constellation-node="${index}" aria-pressed="${active}"
      aria-label="${esc(constellationNodeTitle(node))}${progress ? `, ${Math.round(progress)} percent explored` : ""}">
      <span>${esc(constellationNodeTitle(node))}</span>
    </button>`;
  }).join("");

  const detail = selected ? `
    <div class="constellation-detail" tabindex="-1">
      <div>
        <span class="tag cat">${esc(selected.category ?? selected.category_name ?? "Topic")}</span>
        <h3>${esc(constellationNodeTitle(selected))}</h3>
        <p class="muted small">${esc(selected.detail ?? selected.description
          ?? selected.summary ?? "A promising direction just outside your usual work.")}</p>
      </div>
      ${selected.progress != null || selected.mastery != null || selected.lesson_count != null ? `
        <div class="node-progress" aria-label="${Math.round(growthNumber(
          constellationNodeProgress(selected)))} percent explored">
          <b>${Math.round(constellationNodeProgress(selected))}%</b>
          <small>explored</small>
        </div>` : ""}
      ${selected.lesson_id ? `<button class="btn small" data-constellation-open="${
        Number(selected.lesson_id)}">Open</button>` : ""}
    </div>` : `
    <p class="tiny muted center constellation-prompt">
      Tap a glowing topic to see why it sits next to your role.
    </p>`;

  return `
    <section class="explore-section card" id="explore-map" aria-labelledby="explore-map-title">
      <div class="section-heading">
        <div><span class="eyebrow">Your learning map</span><h2 id="explore-map-title">Constellation</h2></div>
        <span class="visit-status">${constellation.visited_today ? "Seen today" : "Mapping..."}</span>
      </div>
      <p class="muted small">Your role is the centre. The brightest nodes are the next useful tangents.</p>
      <div class="constellation ${dense ? "dense" : ""}" role="group"
        aria-label="Topics adjacent to ${esc(role)}">
        <span class="constellation-orbit orbit-one" aria-hidden="true"></span>
        <span class="constellation-orbit orbit-two" aria-hidden="true"></span>
        ${categoryButtons}
        ${nodeButtons}
        <div class="constellation-role">
          <small>Your role</small><strong>${esc(role)}</strong>
        </div>
      </div>
      ${detail}
    </section>`;
}

function reviewMarkup(review = {}) {
  const run = state.explore.reviewRun;
  const due = growthNumber(review.due_count);
  const reviewed = growthNumber(review.reviewed_today);
  const available = Array.isArray(review.questions) ? review.questions : [];

  if (run?.complete) {
    const correct = run.results.filter(Boolean).length;
    return `
      <section class="explore-section card review-card complete" id="explore-review"
        aria-labelledby="explore-review-title">
        <div class="review-finish">
          ${Owl.render({ size: 72, mood: "proud" })}
          <div><span class="eyebrow">Three minutes well spent</span>
            <h2 id="explore-review-title">${correct}/${run.questions.length} recalled</h2>
            <p class="muted small">The next review will arrive when the memory needs it.</p>
          </div>
        </div>
        ${due > 0 && available.length ? `
          <button class="btn ghost small wide" data-review-start>Review another set</button>` : ""}
      </section>`;
  }

  if (!run) {
    return `
      <section class="explore-section card review-card" id="explore-review"
        aria-labelledby="explore-review-title">
        <div class="section-heading">
          <div><span class="eyebrow">Keep it retrievable</span><h2 id="explore-review-title">3-minute review</h2></div>
          <span class="review-count">${due} due</span>
        </div>
        <p class="muted small">${reviewed
          ? `${reviewed} reviewed today. A short return makes yesterday's tangent stick.`
          : "Three quick questions, chosen from lessons that are ready to be recalled."}</p>
        <button class="btn wide" data-review-start ${available.length ? "" : "disabled"}>
          ${available.length ? "Start the review" : "Nothing due right now"}
        </button>
      </section>`;
  }

  const question = run.questions[run.index];
  const feedback = run.feedback;
  const options = Array.isArray(question.options) ? question.options : [];
  const correctIndex = feedback?.correct_index ?? feedback?.correct_answer_index;
  return `
    <section class="explore-section card review-card" id="explore-review"
      aria-labelledby="explore-review-title">
      <div class="section-heading">
        <div><span class="eyebrow">${esc(question.lesson_title || "Quick recall")}</span>
          <h2 id="explore-review-title">Question ${run.index + 1} of ${run.questions.length}</h2></div>
        <span class="review-timer" aria-label="About three minutes">~3 min</span>
      </div>
      <div class="review-dots" aria-hidden="true">${run.questions.map((_, index) =>
        `<i class="${index < run.index ? "done" : index === run.index ? "now" : ""}"></i>`).join("")}</div>
      <p class="review-prompt">${esc(question.prompt)}</p>
      <div class="options explore-options">${options.map((option, index) => {
        let resultClass = "";
        if (feedback) {
          if (index === correctIndex || (feedback.correct && index === feedback.selected)) {
            resultClass = "correct";
          } else if (!feedback.correct && index === feedback.selected) {
            resultClass = "wrong";
          }
        }
        return `<button class="option ${resultClass}" data-review-option="${index}"
          ${feedback ? "disabled" : ""}>${esc(option)}</button>`;
      }).join("")}</div>
      ${feedback ? `
        <div class="review-feedback ${feedback.correct ? "correct" : "wrong"}" role="status" aria-live="polite">
          <b>${feedback.correct ? "That came back." : "Worth another look."}</b>
          <span>${esc(feedback.explanation ?? feedback.feedback
            ?? "The answer has been added back to your review rhythm.")}</span>
        </div>
        <button class="btn wide" data-review-next>
          ${run.index + 1 >= run.questions.length ? "Complete review" : "Next question"}
        </button>` : ""}
    </section>`;
}

function missionsMarkup(missions = []) {
  const list = Array.isArray(missions) ? missions : [];
  return `
    <section class="explore-section card" id="explore-missions" aria-labelledby="explore-missions-title">
      <div class="section-heading">
        <div><span class="eyebrow">Small wins, every day</span><h2 id="explore-missions-title">Daily missions</h2></div>
        <span class="mission-total">${list.filter((mission) => mission.complete).length}/${list.length}</span>
      </div>
      <div class="mission-list">${list.length ? list.map((mission) => {
        const progress = growthNumber(mission.progress);
        const target = Math.max(1, growthNumber(mission.target, 1));
        const complete = !!mission.complete;
        const claimed = !!mission.claimed;
        return `<article class="mission ${complete ? "complete" : ""}">
          <div class="mission-copy"><h3>${esc(mission.title || "Daily tangent")}</h3>
            <p class="tiny muted">${esc(mission.detail || "")}</p>
            <div class="progress mission-progress"><i style="width:${growthPercent(progress, target)}%"></i></div>
          </div>
          <div class="mission-action">
            <span>${Math.min(progress, target)}/${target}</span>
            ${claimed ? `<b class="claimed">Claimed</b>`
              : complete ? `<button class="btn small" data-mission-claim="${esc(mission.key)}">
                  +${esc(rewardLabel(mission.reward))}</button>`
              : `<small>${esc(rewardLabel(mission.reward))}</small>`}
          </div>
        </article>`;
      }).join("") : `<p class="muted small">New missions are being prepared.</p>`}</div>
    </section>`;
}

function bossMarkup(boss = {}) {
  const cachedFeedback = state.explore.bossFeedback;
  const feedback = cachedFeedback
    && cachedFeedback.week_key === boss.week_key
    && (!cachedFeedback.scenario_key || !boss.scenario_key
      || cachedFeedback.scenario_key === boss.scenario_key)
    ? cachedFeedback
    : null;
  const attempted = boss.attempted || feedback?.attempted;
  const correct = feedback?.correct ?? boss.correct;
  return `
    <section class="explore-section card boss-card ${boss.locked ? "locked" : ""}"
      id="explore-boss" aria-labelledby="explore-boss-title">
      <div class="boss-mark" aria-hidden="true">${boss.locked ? "LOCK" : "WEEK"}</div>
      <span class="eyebrow">One bigger connection</span>
      <h2 id="explore-boss-title">Weekly boss</h2>
      ${boss.locked ? `
        <p class="muted">${esc(boss.reason || "Complete more of this week's learning to unlock the boss.")}</p>
      ` : attempted ? `
        <div class="boss-result ${correct ? "correct" : "wrong"}" role="status">
          <h3>${correct ? "Connection made." : "The boss got this round."}</h3>
          <p class="muted small">${esc(feedback?.explanation ?? boss.explanation
            ?? "A fresh challenge arrives next week.")}</p>
          <span class="tag cat">Reward: ${esc(rewardLabel(feedback?.reward ?? boss.reward))}</span>
        </div>
      ` : `
        <p class="boss-prompt">${esc(boss.prompt || "Your weekly challenge will appear here.")}</p>
        <div class="options boss-options">${(boss.options || []).map((option, index) =>
          `<button class="option" data-boss-option="${index}">${esc(option)}</button>`).join("")}</div>
        <p class="tiny muted">One attempt. Think across the boundaries of your role.</p>
      `}
    </section>`;
}

function itemEquipped(item, equipped) {
  if (item.equipped) return true;
  const key = cosmeticKey(item.key ?? item.item_key);
  if (Array.isArray(equipped)) return equipped.some((entry) => cosmeticKey(entry) === key);
  if (equipped && typeof equipped === "object") {
    return Object.values(equipped).some((entry) => cosmeticKey(entry) === key);
  }
  return false;
}

function workshopPreview(item) {
  const rawSlot = String(item.slot || item.type || "").toLowerCase();
  const slot = cosmeticSlot(rawSlot);
  const key = cosmeticKey(item.key ?? item.item_key);
  if (slot === "owl") {
    return `<div class="workshop-preview owl-preview">${Owl.render({
      size: 60, mood: "proud", accessory: key, decorative: true,
    })}</div>`;
  }
  if (slot === "card") {
    return `<div class="workshop-preview card-preview" data-preview-cosmetic="${key}">
      <span></span><i></i><i></i><small>Tangent card</small>
    </div>`;
  }
  if (slot === "celebration") {
    return `<div class="workshop-preview burst-preview" data-preview-cosmetic="${key}" aria-hidden="true">
      ${Array.from({ length: 7 }, (_, index) => `<i style="--piece:${index}"></i>`).join("")}
    </div>`;
  }
  if (rawSlot.includes("desk")) {
    return `<div class="workshop-preview desk-preview" aria-label="${esc(item.name || "Desk item")}">
      <span class="fern-leaf leaf-one"></span><span class="fern-leaf leaf-two"></span>
      <span class="fern-leaf leaf-three"></span><i class="fern-pot"></i>
    </div>`;
  }
  return `<div class="workshop-preview generic-preview">${esc(item.preview || "New")}</div>`;
}

function workshopMarkup(workshop = {}, coins = 0) {
  const items = Array.isArray(workshop.items) ? workshop.items : [];
  const equippedOwl = profilePictureAccessory(
    state.user?.profile_picture, state.cosmetics.owl);
  const equippedOwlName = owlAccessoryName(equippedOwl);
  return `
    <section class="explore-section card workshop-card" id="explore-workshop"
      aria-labelledby="explore-workshop-title">
      <div class="section-heading">
        <div><span class="eyebrow">Make Tangent yours</span><h2 id="explore-workshop-title">Owl Workshop</h2></div>
        <span class="workshop-balance"><span class="coin-mini" aria-hidden="true">T</span> ${growthNumber(coins)}</span>
      </div>
      <p class="muted small">Your owl is your profile picture. Earn coins, choose a look, and Tangent will wear it everywhere.</p>
      <div class="workshop-owl-identity">
        <div class="workshop-owl-stage">${Owl.render({
          size: 88,
          mood: "proud",
          accessory: equippedOwl,
          label: equippedOwl
            ? `Your Tangent owl wearing ${equippedOwlName}`
            : "Your classic Tangent owl",
        })}</div>
        <div class="workshop-owl-copy">
          <span class="eyebrow">Your owl</span>
          <h3>${esc(equippedOwlName)}</h3>
          <p class="tiny muted">This look appears on your profile, in learning circles, and on shared lessons.</p>
          ${equippedOwl
            ? `<button class="btn ghost small" data-workshop-classic>Use classic look</button>`
            : `<span class="workshop-current">Wearing the classic look</span>`}
        </div>
      </div>
      <div class="workshop-grid">${items.length ? items.map((item) => {
        const key = cosmeticKey(item.key ?? item.item_key);
        const owlItem = cosmeticSlot(item.slot || item.type) === "owl";
        const equipped = itemEquipped(item, workshop.equipped);
        const affordable = growthNumber(coins) >= growthNumber(item.price);
        return `<article class="workshop-item ${equipped ? "equipped" : ""}">
          ${workshopPreview(item)}
          <div class="workshop-copy"><h3>${esc(item.name || key)}</h3>
            <p class="tiny muted">${esc(item.description || item.preview || "")}</p></div>
          ${equipped ? `<button class="btn ghost small" disabled>${owlItem ? "Wearing" : "Equipped"}</button>`
            : item.owned ? `<button class="btn small" data-workshop-equip="${key}">${owlItem ? "Wear this" : "Equip"}</button>`
            : `<button class="btn small" data-workshop-buy="${key}" ${affordable ? "" : "disabled"}>
                ${affordable ? `${owlItem ? "Buy & wear" : "Buy"} · ${growthNumber(item.price)} T`
                  : `Need ${Math.max(0, growthNumber(item.price) - growthNumber(coins))} more`}
              </button>`}
        </article>`;
      }).join("") : `<p class="muted small">The workshop shelves are being stocked.</p>`}</div>
    </section>`;
}

function circlesMarkup(circles = []) {
  const list = Array.isArray(circles) ? circles : [];
  return `
    <section class="explore-section card circles-card" id="explore-circles"
      aria-labelledby="explore-circles-title">
      <div class="section-heading">
        <div><span class="eyebrow">Private, collaborative, calm</span><h2 id="explore-circles-title">Learning circles</h2></div>
        <span class="no-ranks">No leaderboard</span>
      </div>
      <p class="muted small">Invite people you trust and fill a shared weekly goal. Contributions are visible; nobody is ranked.</p>
      <div class="circle-forms">
        <form id="circleCreateForm">
          <label for="circleName">Start a circle</label>
          <div class="row"><input id="circleName" type="text" maxlength="60"
            placeholder="e.g. Curious generalists" required>
            <button class="btn small" type="submit">Create</button></div>
        </form>
        <form id="circleJoinForm">
          <label for="circleCode">Join with an invite</label>
          <div class="row"><input id="circleCode" type="text" maxlength="32"
            placeholder="Invite code" autocapitalize="characters" required>
            <button class="btn ghost small" type="submit">Join</button></div>
        </form>
      </div>
      <div class="circle-list">${list.map((circle) => {
        const progress = growthNumber(circle.weekly_progress);
        const goal = Math.max(1, growthNumber(circle.weekly_goal, 1));
        return `<article class="circle">
          <div class="circle-top"><div><h3>${esc(circle.name)}</h3>
            <span class="tiny muted">${growthNumber(circle.member_count, (circle.members || []).length)}
              member${growthNumber(circle.member_count, (circle.members || []).length) === 1 ? "" : "s"}</span></div>
            <button class="btn ghost small" data-circle-copy="${esc(circle.invite_code)}">Copy invite</button>
          </div>
          <div class="circle-goal"><div class="row"><b>Shared weekly goal</b>
            <span class="spacer"></span><span>${progress}/${goal}</span></div>
            <div class="progress"><i style="width:${growthPercent(progress, goal)}%"></i></div></div>
          <div class="circle-members">${(circle.members || []).map((member) => `
            <div class="circle-member">
              <span class="circle-avatar owl-avatar">${owlAvatarMarkup(member.profile_picture, {
                fallbackAccessory: member.owl_accessory || "",
                size: 26,
                decorative: true,
              })}</span>
              <span>${esc(member.display_name || member.name || "Member")}</span>
              <b>+${growthNumber(member.contribution)}</b>
            </div>`).join("")}</div>
          <button class="circle-leave" data-circle-leave="${esc(circle.id)}">Leave circle</button>
        </article>`;
      }).join("")}</div>
    </section>`;
}

function paintExplore() {
  if (state.tab !== "explore" || state.lesson || !state.growth) return;
  const data = state.growth;
  const coins = growthNumber(data.coins ?? data.wallet?.coins ?? state.user?.coins);
  view.innerHTML = `
    <div class="explore-hero card">
      <div class="explore-hero-copy">
        <span class="eyebrow">Go tangent on purpose</span>
        <h1>Explore</h1>
        <p class="muted">See what is adjacent, keep it in memory, and grow with a little momentum.</p>
      </div>
      ${Owl.render({ size: 82, mood: "curious" })}
    </div>
    <nav class="explore-jumps" aria-label="Explore sections">
      ${[
        ["map", "Map"], ["review", "Review"], ["missions", "Missions"],
        ["boss", "Boss"], ["workshop", "Workshop"], ["circles", "Circles"],
      ].map(([id, label]) =>
        `<button data-explore-jump="${id}">${label}</button>`).join("")}
    </nav>
    ${constellationMarkup(data.constellation)}
    ${reviewMarkup(data.review)}
    ${missionsMarkup(data.missions)}
    ${bossMarkup(data.boss)}
    ${workshopMarkup(data.workshop, coins)}
    ${circlesMarkup(data.circles)}`;
  bindExplore();
  if (state.explore.jumpTo) {
    const jumpTo = state.explore.jumpTo;
    state.explore.jumpTo = "";
    requestAnimationFrame(() => {
      const section = document.getElementById(`explore-${jumpTo}`);
      section?.scrollIntoView({ block: "start" });
      focusExplore(`#explore-${jumpTo}`);
    });
  }
}

function focusExplore(selector) {
  const target = view.querySelector(selector);
  if (!target) return;
  if (!target.matches("button, input, select, textarea, a[href], [tabindex]")) {
    target.setAttribute("tabindex", "-1");
  }
  target.focus({ preventScroll: true });
}

function primeGrowthCosmetics() {
  if (!state.user || state.growth || state.growthPriming) return state.growthPriming;
  const userId = state.user.id;
  state.growthPriming = (async () => {
    try {
      const data = await api("/api/growth");
      if (!state.user || state.user.id !== userId) return;
      state.growth = data;
      applyRewardPayload(growthWallet(data));
      applyCosmetics(data);
      if (state.tab === "explore" && !state.lesson) paintExplore();
    } catch {
      /* Explore will show a retry if the user opens it; cosmetics are optional chrome. */
    } finally {
      state.growthPriming = null;
    }
  })();
  return state.growthPriming;
}

async function renderExplore() {
  const request = ++state.explore.request;
  if (state.growth) paintExplore();
  else view.innerHTML = `<div class="card center"><span class="spinner"></span>
    <p class="tiny muted">Charting the edges of your role...</p></div>`;
  let data;
  try {
    data = await api("/api/growth");
  } catch (err) {
    if (request !== state.explore.request || state.tab !== "explore") return;
    if (state.growth) return toast(err.message);
    view.innerHTML = `<div class="card center"><h2>Explore could not load</h2>
      <p class="muted small">${esc(err.message)}</p>
      <button class="btn small" id="retryExplore">Try again</button></div>`;
    document.getElementById("retryExplore").onclick = renderExplore;
    return;
  }
  if (request !== state.explore.request || state.tab !== "explore" || state.lesson) return;
  const previousVisit = state.growth?.constellation?.visited_today;
  state.growth = data;
  if (state.explore.bossFeedback
    && (state.explore.bossFeedback.week_key !== data.boss?.week_key
      || (state.explore.bossFeedback.scenario_key && data.boss?.scenario_key
        && state.explore.bossFeedback.scenario_key !== data.boss.scenario_key))) {
    state.explore.bossFeedback = null;
  }
  if (previousVisit && !data.constellation?.visited_today) state.explore.visitSent = false;
  applyRewardPayload(growthWallet(data));
  applyCosmetics(data);
  paintExplore();

  if (data.constellation?.visited_today) state.explore.visitSent = true;
  if (!data.constellation?.visited_today && !state.explore.visitSent) {
    state.explore.visitSent = true;
    api("/api/growth/constellation/visit", { method: "POST" })
      .then((response) => {
        applyRewardPayload(growthWallet(response));
        if (state.tab === "explore" && !state.lesson) renderExplore();
      })
      .catch(() => { state.explore.visitSent = false; });
  }
}

function startExploreReview() {
  const questions = (state.growth?.review?.questions || []).slice(0, 3);
  if (!questions.length) return toast("Nothing is due right now.");
  state.explore.reviewRun = {
    questions,
    index: 0,
    feedback: null,
    results: [],
    complete: false,
  };
  paintExplore();
  document.getElementById("explore-review")?.scrollIntoView({ block: "start" });
  focusExplore("[data-review-option]");
}

async function answerExploreReview(index, button) {
  const run = state.explore.reviewRun;
  const question = run?.questions?.[run.index];
  if (!question || run.feedback) return;
  view.querySelectorAll("[data-review-option]").forEach((option) => { option.disabled = true; });
  busy(button, true, "Checking...");
  try {
    const response = await api("/api/growth/review/answer", {
      method: "POST",
      body: {
        lesson_id: question.lesson_id,
        question_index: question.question_index,
        answer_index: index,
      },
    });
    applyRewardPayload(growthWallet(response));
    run.feedback = { ...response, selected: index };
    run.results[run.index] = !!response.correct;
    paintExplore();
    focusExplore("[data-review-next]");
  } catch (err) {
    paintExplore();
    focusExplore("[data-review-option]");
    toast(err.message);
  }
}

async function nextExploreReview() {
  const run = state.explore.reviewRun;
  if (!run?.feedback) return;
  if (run.index + 1 < run.questions.length) {
    run.index += 1;
    run.feedback = null;
    paintExplore();
    document.getElementById("explore-review")?.scrollIntoView({ block: "start" });
    focusExplore("[data-review-option]");
    return;
  }
  run.complete = true;
  run.feedback = null;
  window.TangentCelebrate?.burst(42);
  await renderExplore();
  document.getElementById("explore-review")?.scrollIntoView({ block: "start" });
  focusExplore("[data-review-start], #explore-review-title");
}

async function mutateGrowth(path, body, button, working, success, celebrate = false) {
  const returnSectionId = button?.closest(".explore-section")?.id;
  busy(button, true, working);
  try {
    const response = await api(`/api/growth${path}`, {
      method: "POST",
      body,
    });
    applyRewardPayload(growthWallet(response));
    applyCosmetics(response);
    if (success) toast(success);
    if (celebrate) window.TangentCelebrate?.burst(38);
    await renderExplore();
    if (returnSectionId) {
      const section = document.getElementById(returnSectionId);
      const target = section?.querySelector("h2, h3, button:not([disabled])");
      if (target) {
        if (!target.matches("button, [tabindex]")) target.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: true });
      }
    }
    return response;
  } catch (err) {
    busy(button, false);
    toast(err.message);
    return null;
  }
}

function bindExplore() {
  view.querySelectorAll("[data-explore-jump]").forEach((button) => {
    button.onclick = () => document.getElementById(`explore-${button.dataset.exploreJump}`)
      ?.scrollIntoView({
        behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
          ? "auto" : "smooth",
        block: "start",
      });
  });
  view.querySelectorAll("[data-constellation-node]").forEach((button) => {
    button.onclick = () => {
      const index = Number(button.dataset.constellationNode);
      const node = state.growth?.constellation?.nodes?.[index];
      state.explore.nodeKey = constellationNodeKey(node, index);
      paintExplore();
      document.querySelector(".constellation-detail")?.scrollIntoView({ block: "nearest" });
      focusExplore("[data-constellation-open], .constellation-detail");
    };
  });
  view.querySelector("[data-constellation-open]")?.addEventListener("click", (event) => {
    openLesson(Number(event.currentTarget.dataset.constellationOpen), event.currentTarget);
  });
  view.querySelectorAll("[data-review-start]").forEach((button) => {
    button.onclick = startExploreReview;
  });
  view.querySelectorAll("[data-review-option]").forEach((button) => {
    button.onclick = () => answerExploreReview(Number(button.dataset.reviewOption), button);
  });
  view.querySelector("[data-review-next]")?.addEventListener("click", nextExploreReview);
  view.querySelectorAll("[data-mission-claim]").forEach((button) => {
    button.onclick = () => mutateGrowth(
      "/missions/claim", { key: button.dataset.missionClaim }, button,
      "Claiming...", "Mission reward claimed.", true);
  });
  view.querySelectorAll("[data-boss-option]").forEach((button) => {
    button.onclick = async () => {
      view.querySelectorAll("[data-boss-option]").forEach((option) => { option.disabled = true; });
      const response = await mutateGrowth(
        "/boss/answer", {
          answer_index: Number(button.dataset.bossOption),
          scenario_key: state.growth?.boss?.scenario_key,
        }, button,
        "Checking...", "", false);
      if (response) {
        state.explore.bossFeedback = response;
        if (response.correct) window.TangentCelebrate?.burst(72);
        paintExplore();
        document.getElementById("explore-boss")?.scrollIntoView({ block: "start" });
        focusExplore(".boss-result h3, #explore-boss-title");
      }
    };
  });
  view.querySelectorAll("[data-workshop-buy]").forEach((button) => {
    button.onclick = () => {
      const key = button.dataset.workshopBuy;
      const item = state.growth?.workshop?.items?.find((entry) =>
        cosmeticKey(entry.key ?? entry.item_key) === key);
      const owlItem = cosmeticSlot(item?.slot || item?.type) === "owl";
      return mutateGrowth(
        "/workshop/purchase", { item_key: key }, button,
        owlItem ? "Buying & wearing..." : "Buying...",
        owlItem
          ? `${item?.name || owlAccessoryName(key)} is now your profile look.`
          : `${item?.name || "New look"} was added to your workshop.`,
        owlItem);
    };
  });
  view.querySelectorAll("[data-workshop-equip]").forEach((button) => {
    button.onclick = () => {
      const key = button.dataset.workshopEquip;
      const item = state.growth?.workshop?.items?.find((entry) =>
        cosmeticKey(entry.key ?? entry.item_key) === key);
      const owlItem = cosmeticSlot(item?.slot || item?.type) === "owl";
      return mutateGrowth(
        "/workshop/equip", { item_key: key }, button,
        owlItem ? "Changing your owl..." : "Equipping...",
        owlItem
          ? `${item?.name || owlAccessoryName(key)} is now your profile look.`
          : `${item?.name || "New look"} is now equipped.`,
        true);
    };
  });
  view.querySelector("[data-workshop-classic]")?.addEventListener("click", (event) => {
    mutateGrowth(
      "/workshop/classic", undefined, event.currentTarget,
      "Restoring...", "Classic Tangent is now your profile look.", true);
  });

  document.getElementById("circleCreateForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const name = document.getElementById("circleName").value.trim();
    if (name) mutateGrowth("/circles", { name }, button, "Creating...", "Circle created.");
  });
  document.getElementById("circleJoinForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const inviteCode = document.getElementById("circleCode").value.trim();
    if (inviteCode) mutateGrowth(
      "/circles/join", { invite_code: inviteCode }, button, "Joining...", "Joined the circle.");
  });
  view.querySelectorAll("[data-circle-copy]").forEach((button) => {
    button.onclick = async () => {
      const code = button.dataset.circleCopy;
      try {
        await navigator.clipboard.writeText(code);
        toast("Invite code copied.");
      } catch {
        toast(`Invite code: ${code}`, 5000);
      }
    };
  });
  view.querySelectorAll("[data-circle-leave]").forEach((button) => {
    button.onclick = () => {
      if (!window.confirm("Leave this private learning circle?")) return;
      const id = encodeURIComponent(button.dataset.circleLeave);
      mutateGrowth(`/circles/${id}/leave`, undefined, button, "Leaving...", "You left the circle.");
    };
  });
}

/* --- rewards --- */

function streakMessage(status, current) {
  if (status.active_today) return "Safe for today — you already finished a lesson.";
  if (status.expired) {
    return "That streak has ended. Finish a lesson today to begin a new one.";
  }
  if (status.protected) {
    return `${status.missed_days} missed day${status.missed_days === 1 ? "" : "s"} will be covered automatically on your next finish.`;
  }
  if (status.at_risk) {
    return `At risk: stock ${status.missed_days} freeze${status.missed_days === 1 ? "" : "s"} before your next finish to keep it.`;
  }
  return current > 0
    ? "Finish a lesson today to extend it."
    : "Finish your first lesson to light the flame.";
}

async function renderRewards() {
  view.innerHTML = `<div class="card center"><span class="spinner"></span></div>`;
  let data;
  try { data = await api("/api/rewards"); }
  catch (err) {
    if (state.tab === "rewards") view.innerHTML = `<div class="card">${esc(err.message)}</div>`;
    return;
  }
  if (state.tab !== "rewards" || state.lesson) return;
  applyRewardPayload(data);
  const currentStreak = displayStreak(state.user);

  const hint = data.catalog.hint;
  const freeze = data.catalog.streak_freeze;
  const freezeFull = data.streak_freezes >= freeze.max_owned;
  const hintAffordable = data.coins >= hint.price;
  const freezeAffordable = data.coins >= freeze.price;

  view.innerHTML = `
    <div class="card wallet-hero">
      <div class="coin-orb" aria-hidden="true">T</div>
      <div>
        <div class="tiny muted">Your balance</div>
        <h1>${data.coins} coins</h1>
        <p class="muted small">Earned by finishing new lessons. Never sold for money.</p>
      </div>
    </div>

    <div class="inventory">
      <div><span>💡</span><b>${data.hint_tokens}</b><small>hints ready</small></div>
      <div><span>🧊</span><b>${data.streak_freezes}</b><small>freezes ready</small></div>
      <div><span>🔥</span><b>${currentStreak}</b><small>day streak</small></div>
    </div>

    <div class="card streak-card ${data.streak_status.at_risk ? "at-risk" : ""}">
      <div class="row">
        <span class="streak-icon">🔥</span>
        <div style="flex:1">
          <h2>${currentStreak} day streak</h2>
          <p class="muted small">${esc(streakMessage(
            data.streak_status, currentStreak))}</p>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="row" style="align-items:flex-start">
        <div style="flex:1">
          <h2>Owl's counter</h2>
          <p class="muted small">A little help, paid for by showing up.</p>
        </div>
        <span class="tag cat">No real money</span>
      </div>
      <div class="shop-grid">
        <article class="shop-product">
          <div class="product-icon">💡</div>
          <div class="product-copy">
            <h3>${esc(hint.name)}</h3>
            <p class="muted small">${esc(hint.description)}</p>
            <div class="owned">${data.hint_tokens} ready</div>
          </div>
          <button class="btn small" data-buy="hint" ${hintAffordable ? "" : "disabled"}>
            ${hintAffordable ? `${hint.price} coins` : `Need ${hint.price - data.coins} more`}
          </button>
        </article>
        <article class="shop-product">
          <div class="product-icon">🧊</div>
          <div class="product-copy">
            <h3>${esc(freeze.name)}</h3>
            <p class="muted small">${esc(freeze.description)}</p>
            <div class="owned">${data.streak_freezes}/${freeze.max_owned} stocked</div>
          </div>
          <button class="btn small" data-buy="streak_freeze" ${
            !freezeFull && freezeAffordable ? "" : "disabled"}>
            ${freezeFull ? "Inventory full"
              : freezeAffordable ? `${freeze.price} coins` : `Need ${freeze.price - data.coins} more`}
          </button>
        </article>
      </div>
      <p class="tiny muted center" style="margin-top:14px">Freezes apply automatically.
        A hint removes one wrong answer and can only be used once per question.</p>
    </div>`;

  view.querySelectorAll("[data-buy]").forEach((button) => {
    button.onclick = () => buyReward(button.dataset.buy, button);
  });
}

async function buyReward(item, button) {
  busy(button, true, "Buying…");
  try {
    const data = await api("/api/rewards/purchase", {
      method: "POST", body: { item },
    });
    applyRewardPayload(data);
    toast(item === "hint" ? "Hint ready for your next question." : "Streak freeze stocked.");
    if (state.tab === "rewards" && !state.lesson) renderRewards();
  } catch (err) {
    busy(button, false);
    toast(err.message);
  }
}

/* --- progress --- */

async function renderProgress() {
  view.innerHTML = `<div class="card center"><span class="spinner"></span></div>`;
  let data;
  try { data = await api("/api/progress"); }
  catch (err) { view.innerHTML = `<div class="card">${esc(err.message)}</div>`; return; }
  if (state.tab !== "progress" || state.lesson) return;
  applyRewardPayload(data);
  const currentStreak = data.streak_status?.expired ? 0 : data.current_streak;

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
        <div class="reward"><div class="n">🔥 ${currentStreak}</div><div class="tiny muted">current streak</div></div>
        <div class="reward"><div class="n">${data.longest_streak}</div><div class="tiny muted">longest</div></div>
        <div class="reward"><div class="n">${data.lessons_completed}</div><div class="tiny muted">lessons</div></div>
        <div class="reward coin-reward"><div class="n">${data.coins}</div><div class="tiny muted">coin balance</div></div>
      </div>
      <button class="btn subtle wide" id="progressRewards">Open rewards · ${
        data.hint_tokens} 💡 · ${data.streak_freezes} 🧊</button>
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
              · ${h.score}/${h.total} · +${h.xp} XP · +${h.coins || 0} coins</div>
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
  document.getElementById("progressRewards").onclick = () => setTab("rewards");

  view.querySelectorAll("[data-share]").forEach((b) => {
    b.onclick = () => shareLesson(Number(b.dataset.share), b, document.getElementById("shareSlot"));
  });
}

/* --- profile --- */

const ACCENTS = ["violet", "ember", "teal", "rose", "lime"];

function renderProfile() {
  const u = state.user;
  const owlAccessory = profilePictureAccessory(u.profile_picture, state.cosmetics.owl);
  const owlName = owlAccessoryName(owlAccessory);

  view.innerHTML = `
    <div class="card">
      <h2>Your profile</h2>
      <p class="muted small">${esc(u.email)}</p>

      <div class="owl-profile-card" style="margin-top:16px">
        <div class="profile-owl-avatar">${owlAvatarMarkup(u.profile_picture, {
          fallbackAccessory: state.cosmetics.owl,
          size: 92,
          mood: "proud",
          label: owlAccessory
            ? `Your customised owl wearing ${owlName}`
            : "Your classic Tangent owl",
        })}</div>
        <div class="owl-profile-copy">
          <span class="eyebrow">${esc(owlName)}</span>
          <h3>Your Tangent owl</h3>
          <p class="small muted">This is how you'll appear in learning circles and shared lessons.</p>
          <button class="btn small" id="customizeOwl" type="button">Change my owl</button>
        </div>
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

        <div class="field"><label>Theme</label>
          <div class="row wrap" id="themePick">
            ${["system", "light", "dark"].map((t) => `
              <button type="button" class="btn small ${(u.theme || "system") === t ? "" : "ghost"}"
                data-theme-opt="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}
          </div>
        </div>

        <div class="field">
          <label for="defaultLevel">Default starting level</label>
          <div class="row">
            <span class="levelnum" id="defLevelNum">${u.default_level || 5}</span>
            <input type="range" id="defaultLevel" min="1" max="10" step="1"
              value="${u.default_level || 5}">
          </div>
          <div class="tiny muted" style="margin-top:6px">Where the slider starts when you
            pick a topic. You can change it per lesson.</div>
        </div>

        <div class="field">
          <label>Shared library</label>
          <label class="check">
            <input type="checkbox" id="contribute" ${u.contribute_to_library ? "checked" : ""}>
            <span>Add lessons written for me to the shared library</span>
          </label>
          <div class="tiny muted" style="margin-top:6px">Only the lesson and its topic
            — never your activity log. It means nobody has to pay to write the same
            lesson twice, and you get everyone else's for free.</div>
        </div>

        <button class="btn" type="submit" id="saveProfile">Save</button>
      </form>
    </div>

    <div class="card">
      <h2>Meet Tangent again</h2>
      <p class="muted small">Replay the introduction and revisit what you told me
        about your work and what you're after.</p>
      <button class="btn subtle wide" id="replayIntro" style="margin-top:12px">
        Run the intro again</button>
    </div>

    <div class="card">
      <h2>Your data</h2>
      <p class="muted small">Everything Tangent holds about you, as one JSON file.</p>
      <button class="btn subtle wide" id="exportData" style="margin-top:12px">
        Download my data</button>
      <button class="btn ghost wide" id="signout" style="margin-top:10px">Log out</button>
    </div>

    <div class="card danger">
      <h2>Delete your account</h2>
      <p class="muted small">Removes your profile, activity log, observations, lessons
        and progress. This can't be undone. Lessons you contributed to the library stay,
        without your name on them.</p>
      <button class="btn ghost wide" id="deleteAccount" style="margin-top:12px">
        Delete my account</button>
      <div id="deleteSlot"></div>
    </div>`;

  document.getElementById("customizeOwl").onclick = () => {
    state.explore.jumpTo = "workshop";
    setTab("explore");
  };

  document.getElementById("themePick").onclick = (e) => {
    const button = e.target.closest("[data-theme-opt]");
    if (!button) return;
    const theme = button.dataset.themeOpt;
    view.querySelectorAll("[data-theme-opt]").forEach((b) =>
      b.className = `btn small ${b.dataset.themeOpt === theme ? "" : "ghost"}`);
    savePreference({ theme });
  };

  const defLevel = document.getElementById("defaultLevel");
  const paintRange = (input, label) => {
    input.style.setProperty("--fill", `${((input.value - 1) / 9) * 100}%`);
    if (label) label.textContent = input.value;
  };
  paintRange(defLevel, document.getElementById("defLevelNum"));
  defLevel.oninput = () => paintRange(defLevel, document.getElementById("defLevelNum"));

  document.getElementById("swatches").onclick = (e) => {
    const button = e.target.closest("[data-accent]");
    if (!button) return;
    const accent = button.dataset.accent;
    view.querySelectorAll("[data-accent]").forEach((s) =>
      s.setAttribute("aria-pressed", String(s.dataset.accent === accent)));
    savePreference({ accent: accent === "violet" ? "" : accent });
  };

  document.getElementById("profileForm").onsubmit = async (e) => {
    e.preventDefault();
    const button = document.getElementById("saveProfile");
    busy(button, true, "Saving…");
    try {
      // Theme and accent are deliberately absent: they save on click, and
      // re-sending them here would just race with that.
      const body = {
        display_name: document.getElementById("name").value,
        role: document.getElementById("roleEdit").value,
        bio: document.getElementById("bioEdit").value,
        contribute_to_library: document.getElementById("contribute").checked,
        default_level: Number(defLevel.value),
      };
      state.user = await api("/api/auth/me", { method: "PATCH", body });
      paintStats();
      busy(button, false);
      toast("Saved");
    } catch (err) { busy(button, false); toast(err.message); }
  };

  document.getElementById("signout").onclick = signOut;

  document.getElementById("replayIntro").onclick = () => {
    state.introing = true;
    state.introStep = 0;
    state.introDraft = null;
    render();
  };

  document.getElementById("exportData").onclick = () => {
    // A normal navigation, so the browser handles Content-Disposition itself.
    window.location.href = "/api/auth/me/export";
  };

  document.getElementById("deleteAccount").onclick = () => {
    const slot = document.getElementById("deleteSlot");
    if (slot.innerHTML) { slot.innerHTML = ""; return; }
    slot.innerHTML = `
      <div class="stack" style="margin-top:14px">
        <div class="field"><label for="delPass">Confirm with your password</label>
          <input id="delPass" type="password" autocomplete="current-password"></div>
        <button class="btn wide" id="confirmDelete" style="background:var(--bad);border-color:var(--bad);color:#2a0f16">
          Permanently delete everything</button>
      </div>`;
    document.getElementById("confirmDelete").onclick = async (e) => {
      const button = e.currentTarget;
      busy(button, true, "Deleting…");
      try {
        await api("/api/auth/me/delete", {
          method: "POST",
          body: { password: document.getElementById("delPass").value },
        });
        state.user = null;
        state.digest = null;
        state.observations = [];
        state.growth = null;
        state.growthPriming = null;
        state.explore.reviewRun = null;
        state.explore.bossFeedback = null;
        state.explore.nodeKey = "";
        state.explore.visitSent = false;
        applyCosmetics(null, { clear: true });
        render();
        toast("Your account and everything in it is gone.");
      } catch (err) { busy(button, false); toast(err.message); }
    };
  };
}

/* ------------------------------------------------------------------ boot */

(async function boot() {
  // /s/<token> is a real URL people paste around — resolve it before auth, so
  // a signed-out recipient still sees the lesson rather than a login wall.
  const sharedMatch = location.pathname.match(/^\/s\/([\w-]+)\/?$/);
  if (sharedMatch) state.shared = { token: sharedMatch[1] };

  const resetMatch = location.pathname.match(/^\/reset\/([\w-]+)\/?$/);
  if (resetMatch) {
    state.resetToken = resetMatch[1];
    paintStats();
    return renderReset();   // takes precedence: they can't sign in anyway
  }

  try {
    state.user = await api("/api/auth/me");
    primeGrowthCosmetics();
    if (!state.shared) {
      await loadToday();
      setTab("today");
      return;
    }
    await loadToday();
  } catch { /* signed out — the shared view still renders */ }

  render();
})();

/* Installable web-app foundation. The worker caches only the application
   shell; authenticated API data remains network-only. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* Installation is an enhancement; the live app keeps working without it. */
    });
  });
}
