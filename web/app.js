const $ = (id) => document.getElementById(id);

const state = {
  phase: "idle", // idle | loading | typing | result
  sentence: null,
  typed: [""],
  corrected: [],
  replays: 0,
  startedAt: null,
  settings: {},
  view: "train",
};
let audio = null;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// --- header progress ------------------------------------------------------------

function renderProgress(s) {
  state.settings = s.settings;
  const p = $("progress");
  p.innerHTML = "";
  const part = (label, value, cls = "") => {
    const span = el("span", cls);
    span.append(`${label} `, el("b", "", value));
    return span;
  };
  p.append(
    part("level", s.level, "lvl"), " · ",
    part("known", s.counts.known), " · ",
    part("learning", s.counts.learning), " · ",
    part("new", s.counts.new),
  );
}

async function refreshProgress() {
  renderProgress(await api("/api/state"));
}

// --- training -----------------------------------------------------------------

async function loadNext() {
  state.phase = "loading";
  $("result").classList.add("hidden");
  $("words").innerHTML = "";
  $("status").textContent = "…";
  try {
    state.sentence = await api("/api/next");
  } catch (e) {
    $("status").textContent = "no sentence available — try another level in settings";
    state.phase = "idle";
    return;
  }
  state.typed = [""];
  state.corrected = [];
  state.replays = 0;
  state.startedAt = null;
  state.phase = "typing";
  renderWords();
  if (audio) audio.pause();
  audio = new Audio(state.sentence.audio);
  $("status").textContent = "synthesizing speech…";
  audio.addEventListener("canplaythrough", () => {
    if ($("status").textContent.startsWith("synth")) $("status").textContent = "";
  }, { once: true });
  audio.play().catch(() => { $("status").textContent = "press tab to play"; });
}

function play(slow = false) {
  if (!audio) return;
  if (state.phase === "typing") state.replays += 1;
  audio.pause();
  audio.currentTime = 0;
  audio.playbackRate = slow ? 0.7 : 1;
  audio.play();
}

function renderWords() {
  const box = $("words");
  box.innerHTML = "";
  const ref = state.sentence.words;
  const live = state.settings.live_feedback;
  const mask = state.settings.show_mask;
  const current = state.typed.length - 1;
  const count = mask ? Math.max(ref.length, state.typed.length) : state.typed.length;
  for (let i = 0; i < count; i++) {
    const r = ref[i] ?? "";
    const t = state.typed[i] ?? "";
    const w = el("span", "word");
    const len = mask ? Math.max(r.length, t.length) : t.length;
    for (let k = 0; k <= len; k++) {
      if (i === current && k === t.length) w.append(el("span", "caret"));
      if (k === len) break;
      if (k < t.length) {
        let cls = "typed";
        if (live) cls = k >= r.length ? "extra" : t[k] === r[k] ? "correct" : "incorrect";
        w.append(el("span", cls, t[k]));
      } else {
        w.append(el("span", "ph", r[k] === "'" ? "'" : "·"));
      }
    }
    if (live && i < current && t !== r) w.classList.add("done-wrong");
    box.append(w);
  }
}

function typeChar(ch) {
  const i = state.typed.length - 1;
  const ref = state.sentence.words[i] ?? "";
  const pos = state.typed[i].length;
  if (state.settings.live_feedback && ref[pos] !== ch) state.corrected[i] = true;
  state.typed[i] += ch;
  if (state.startedAt === null) state.startedAt = performance.now();
}

function nextWord() {
  if (state.typed[state.typed.length - 1] !== "") state.typed.push("");
}

function backspace(wholeWord) {
  const i = state.typed.length - 1;
  if (state.typed[i] === "") {
    if (i > 0) state.typed.pop();
  } else {
    state.typed[i] = wholeWord ? "" : state.typed[i].slice(0, -1);
  }
}

function isComplete() {
  const ref = state.sentence.words;
  return state.typed.length === ref.length && state.typed.every((t, i) => t === ref[i]);
}

async function submit() {
  if (state.phase !== "typing") return;
  state.phase = "result";
  const typed = state.typed.join(" ").trim();
  const elapsed = state.startedAt === null ? 0 : performance.now() - state.startedAt;
  const corrected = state.sentence.words.map((_, i) => !!state.corrected[i]);
  const res = await api("/api/submit", {
    sentence_id: state.sentence.id, typed, corrected, replays: state.replays, elapsed_ms: Math.round(elapsed),
  });
  renderResult(res);
  refreshProgress();
}

function renderResult(res) {
  $("words").innerHTML = "";
  $("status").textContent = "";
  const text = $("result-text");
  text.innerHTML = "";
  let refIndex = 0;
  for (const w of res.words) {
    const box = el("span", `rw ${w.status}`);
    if (w.status === "extra") {
      box.append(el("span", "w", w.typed));
    } else {
      box.append(el("span", "w", res.display[refIndex++]));
      if (w.status === "typo" || w.status === "wrong") box.append(el("span", "got", w.typed));
      else if (w.status === "missing") box.append(el("span", "got", "—"));
      else box.append(el("span", "got", " "));
    }
    text.append(box);
  }

  const meta = $("result-meta");
  meta.innerHTML = "";
  const stat = (value, label) => {
    const s = el("span");
    s.append(el("b", "", value), label);
    return s;
  };
  meta.append(stat(`${Math.round(res.accuracy * 100)}%`, "acc"));
  if (res.wpm) meta.append(stat(Math.round(res.wpm), "wpm"));
  if (state.replays) meta.append(stat(state.replays, "replays"));
  if (res.source === "tatoeba") {
    const a = el("a", "", `tatoeba #${res.source_id}`);
    a.href = `https://tatoeba.org/en/sentences/show/${res.source_id}`;
    a.target = "_blank";
    meta.append(a);
  } else {
    meta.append(el("span", "", "generated"));
  }

  const chips = $("result-items");
  chips.innerHTML = "";
  for (const item of res.items) {
    const phrase = item.kind !== "word";
    let cls = null;
    let note = "";
    if (item.performance < 0.5) [cls, note] = ["missed", "missed"];
    else if (item.before === "new") [cls, note] = ["new", "new"];
    else if (item.status === "known" && item.before !== "known") [cls, note] = ["learned", "learned ✓"];
    else if (phrase) cls = "";
    if (cls === null) continue;
    const chip = el("span", `chip ${cls}${phrase ? " phrase" : ""}`, item.label);
    if (note) chip.append(el("small", "", note));
    chips.append(chip);
  }

  const up = $("levelup");
  up.classList.toggle("hidden", !res.leveled_up);
  if (res.leveled_up) up.textContent = `level up! now ${res.level} most frequent words are in play`;
  $("result").classList.remove("hidden");
}

// --- keyboard -----------------------------------------------------------------

const WORD_CHAR = /^[a-z0-9']$/i;
const CYRILLIC = /^[а-яё]$/i;

document.addEventListener("keydown", (e) => {
  if (state.view !== "train") return;
  if (e.target.tagName === "INPUT") return;
  if (e.metaKey || (e.ctrlKey && e.key !== "Backspace")) return;

  if (state.phase === "idle") {
    $("start").classList.add("hidden");
    e.preventDefault();
    loadNext();
    return;
  }
  if (e.key === "Tab") {
    e.preventDefault();
    play(e.shiftKey);
    return;
  }
  if (state.phase === "result") {
    if (e.key === "Enter" || e.key === " " || e.key === "Escape") {
      e.preventDefault();
      loadNext();
    }
    return;
  }
  if (state.phase !== "typing") return;

  $("layout-warning").classList.toggle("hidden", !CYRILLIC.test(e.key));
  if (e.key === "Enter" || e.key === "Escape") {
    e.preventDefault();
    submit();
    return;
  }
  if (e.key === "Backspace") {
    e.preventDefault();
    backspace(e.ctrlKey || e.altKey);
  } else if (e.key === " " || e.key === "-") {
    e.preventDefault();
    nextWord();
  } else if (WORD_CHAR.test(e.key)) {
    e.preventDefault();
    typeChar(e.key.toLowerCase());
  } else {
    return;
  }
  renderWords();
  if (state.settings.live_feedback && isComplete()) submit();
});

// --- views ----------------------------------------------------------------------

function show(view) {
  state.view = view;
  for (const v of ["train", "stats", "settings"]) {
    $(`view-${v}`).classList.toggle("hidden", v !== view);
    $(`nav-${v}`).classList.toggle("active", v === view);
  }
  if (view === "stats") loadStats();
  if (view === "settings") loadSettings();
  if (view === "train" && state.phase === "typing") renderWords();
  document.activeElement?.blur();
}

$("nav-train").onclick = () => show("train");
$("nav-stats").onclick = () => show("stats");
$("nav-settings").onclick = () => show("settings");

async function loadStats() {
  const s = await api("/api/stats");
  const chip = (r, note) => {
    const c = el("span", "chip", r.label);
    c.append(el("small", "", note));
    return c;
  };
  $("struggling").replaceChildren(...s.struggling.map((r) => chip(r, `${Math.round(r.strength * 100)}%`)));
  $("learned").replaceChildren(...s.recent_known.map((r) => chip(r, `#${r.rank}`)));
  $("chart").innerHTML = chartSvg(s.attempts);
}

function smooth(values, n = 10) {
  return values.map((_, i) => {
    const win = values.slice(Math.max(0, i - n + 1), i + 1).filter((v) => v != null);
    return win.length ? win.reduce((a, b) => a + b, 0) / win.length : null;
  });
}

function chartSvg(attempts) {
  if (attempts.length < 2) return '<p class="note">not enough attempts yet</p>';
  const W = 900, H = 180, P = 24;
  const acc = smooth(attempts.map((a) => a.accuracy));
  const wpm = smooth(attempts.map((a) => a.wpm));
  const maxWpm = Math.max(10, ...wpm.filter((v) => v != null));
  const x = (i) => P + (i * (W - 2 * P)) / (attempts.length - 1);
  const line = (vals, scale) => vals
    .map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${(H - P - v * scale * (H - 2 * P)).toFixed(1)}`))
    .filter(Boolean).join(" ");
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <line class="axis" x1="${P}" y1="${H - P}" x2="${W - P}" y2="${H - P}"/>
    <polyline class="acc" points="${line(acc, 1)}"/>
    <polyline class="wpm" points="${line(wpm, 1 / maxWpm)}"/>
    <text x="${P}" y="14">accuracy (orange, 0–100%) · wpm (gray, max ${Math.round(maxWpm)})</text>
  </svg>`;
}

async function loadSettings() {
  const [s, voices] = await Promise.all([api("/api/state"), api("/api/voices")]);
  const set = s.settings;
  $("set-level").value = set.level;
  $("set-speed").value = set.speed;
  $("speed-value").textContent = `${set.speed}×`;
  $("set-live").checked = set.live_feedback;
  $("set-mask").checked = set.show_mask;
  const box = $("set-voices");
  box.innerHTML = "";
  for (const v of voices) {
    const b = el("button", set.voices.includes(v) ? "on" : "", v);
    b.onclick = () => {
      b.classList.toggle("on");
      const chosen = [...box.querySelectorAll("button.on")].map((x) => x.textContent);
      saveSettings({ voices: chosen });
    };
    box.append(b);
  }
  $("llm-note").textContent = s.llm
    ? `LLM top-up is on. ${s.starved} unlocked items still lack sentences.`
    : `LLM top-up is off (set DONKEY_LLM_API_KEY). ${s.starved} unlocked items lack sentences at this level.`;
}

async function saveSettings(values) {
  renderProgress(await api("/api/settings", values));
}

$("set-level").onchange = (e) => saveSettings({ level: Number(e.target.value) });
$("set-speed").oninput = (e) => { $("speed-value").textContent = `${e.target.value}×`; };
$("set-speed").onchange = (e) => saveSettings({ speed: Number(e.target.value) });
$("set-live").onchange = (e) => saveSettings({ live_feedback: e.target.checked });
$("set-mask").onchange = (e) => saveSettings({ show_mask: e.target.checked });

refreshProgress();
