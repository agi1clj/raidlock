"use strict";
(function () {
const $ = id => document.getElementById(id);
const RMQ = matchMedia("(prefers-reduced-motion: reduce)");
let RM = RMQ.matches;
const POLL_MS = 3000, OVERVIEW_EVERY = 5, LANE_PAGE = 40, TODO_CAP = 5, NEEDS_CAP = 5, IDLE_CAP = 8, ROW_H = 64, LOG_CAP = 5000, CHAT_CAP = 40;
const DAY_MS = 86400000, FRESH_S = 600;
const LANES = ["todo", "doing", "review", "blocked", "done", "cancelled"];
const LANE = {todo: "To do", doing: "In progress", review: "Review", blocked: "Blocked", done: "Done", cancelled: "Cancelled"};
const RAID = {todo: "Quest board", doing: "On a quest", review: "Inspection", blocked: "Cursed", done: "Hall of legends", cancelled: "Abandoned quests",
  needs: "The raid leader's call", decisions: "Council", agents: "Raid party",
  cost: "Treasury", score: "Hall of renown", log: "Raid log", chat: "Tavern chatter", workspaces: "Guilds of the realm"};
const RAID_PRIMARY = {decision: "Hold council", blocked: "Lift the curse", review: "Inspect loot", question: "Answer", close: "Turn in quest",
  lore: "Inscribe", apply: "Apply enchant", duel: "Judge the duel"};
const RAID_ICON = {needs: "\u2694\uFE0F", todo: "\u{1F4DC}", doing: "\u{1F525}", review: "\u{1F989}", blocked: "\u{1F480}", done: "\u{1F3C6}"};
const STATUS = {
  blocked: {icon: "\u{1F480}", raid: "Cursed", label: "Blocked"},
  stalled: {icon: "⌛", raid: "Lost in the woods", label: "Stalled"},
  working: {icon: "\u{1F525}", raid: "On a quest", label: "Working"},
  quiet: {icon: "\u{1F4A4}", raid: "Quiet on a quest", label: "Working (quiet)"},
  review: {icon: "\u{1F989}", raid: "Awaiting inspection", label: "Waiting for review"},
  idle: {icon: "\u{1F37A}", raid: "At the tavern", label: "Idle"}};
const ORDER = ["blocked", "stalled", "working", "quiet", "review", "idle"];
const CLASSES = [["\u{1F52E}", "Oracle"], ["\u{1F409}", "Wyrmrider"], ["\u{1F319}", "Moonwarden"], ["⚡", "Stormcaller"],
  ["\u{1F43A}", "Wolfrunner"], ["\u{1F31F}", "Starseer"], ["\u{1F528}", "Tinkerer"], ["\u{1F3AF}", "Pathfinder"], ["\u{1F33F}", "Grovekeeper"],
  ["\u{1F340}", "Luckbringer"]];
const ASK = /\?|^lead\b|\blead[:,]|\b(waiting|wait) (on|for) (the )?lead\b|\bneeds? (a |the )?(lead|decision|approval|sign-?off|go-ahead)\b|\b(please|decide|approve|confirm|choose|pick)\b/i;
const CHAT_KINDS = new Set(["msg", "note", "relay", "handback"]);
const PRIMARY = {decision: "Decide", blocked: "Inspect", review: "Review", question: "Reply", close: "Close card", lore: "Approve", apply: "Apply patch", duel: "Decide"};
const KIND = {msg: "message", relay: "relay", handback: "handback", note: "note", status: "status", cost: "cost",
  artifact: "artifact", review: "review", verify: "verify"};
const BILL = {api: ["API", "Billed per token through an API key"], plan: ["Plan", "Included in a subscription plan; the cost shown is what the same tokens would cost through the API"],
  mixed: ["Mixed", "More than one billing mode"], unknown: ["Billing not reported", "The runtime did not report how it is billed"]};
const GLYPH_OK = new Map();
function canShow(g) {
  if (GLYPH_OK.has(g)) return GLYPH_OK.get(g);
  let ok = true;
  try {
    const c = document.createElement("canvas");
    c.width = c.height = 24;
    const x = c.getContext("2d", {willReadFrequently: true});
    x.textBaseline = "top";
    x.font = "18px sans-serif";
    const px = t => { x.clearRect(0, 0, 24, 24); x.fillText(t, 0, 0); return x.getImageData(0, 0, 24, 24).data.join(","); };
    const a = px(g);
    ok = a !== px("") && a !== px("");
  } catch (_) {}
  GLYPH_OK.set(g, ok);
  return ok;
}

const state = {view: null, mode: "all", projects: [], overview: null, board: null, cards: {}, first: true, open: new Set(),
  details: {}, laneLimit: {}, todoAll: false, needsAll: false, levels: {}, slain: {}, doneLimit: 5, etag: {}, log: [], cursor: 0, logWs: null, logSel: null,
  logFilter: "*", statusPrev: {}, warned: new Set(), seenChat: null, seenLog: null, ticks: 0, unknown: "",
  sound: true, vol: 80, notify: false, chatFilter: null, treasuryBy: "by_model", busy: false, muted: new Set(), msgPrev: null,
  repo: "", expired: new Set(), sub: "focus", subData: {}, achievements: false, loaded: false, error: "", anim: true, fxPrev: null,
  fxPulse: new Map(), fxView: {}};
const VIEW_DEFS = (window.RaidlockViews || []).filter(v => v && v.id && v.endpoint && typeof v.render === "function");
const SVG_NS = "http://www.w3.org/2000/svg";
const PANELS = ["needs", "todo", "doing", "donePanel", "agentsPanel", "logPanel", "chatter", "overviewCosts"];
let workTab = "todo", statusFilter = "", activityFilter = "all";
const chatRead = new Map();

function store(k, v) { try { localStorage.setItem(k, String(v)); } catch (_) {} }
function recall(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
function motion() { return !RM && state.anim; }

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") add(el, [v]);
      else if (k === "vars") for (const [n, x] of Object.entries(v)) el.style.setProperty(n, x);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false || k === "") continue;
    if (Array.isArray(k)) add(el, k);
    else if (k instanceof Node) el.appendChild(k);
    else if (el.matches("code, pre, .cmd")) el.appendChild(document.createTextNode(String(k)));
    else appendTimes(el, String(k));
  }
}
function svg(tag, props, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") el.textContent = v;
    else el.setAttribute(k, String(v));
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
function ico(s) { return s && canShow(s) ? h("span", {class: "ico", "aria-hidden": "true"}, s + " ") : null; }
function raidSub(key) { return RAID[key] ? h("small", {class: "raid-sub", text: RAID[key]}) : null; }
const LEVEL_CAP = 60;
const RANKS = [[60, "Mythic"], [50, "Legend"], [40, "Hero"], [30, "Champion"], [20, "Knight"], [10, "Squire"], [1, "Recruit"]];
function levelOf(counts) {
  const c = counts || {};
  const closed = c.done || 0, open = ["todo", "doing", "review", "blocked"].reduce((n, l) => n + (c[l] || 0), 0);
  const total = closed + open;
  const level = total ? 1 + Math.floor((LEVEL_CAP - 1) * closed / total) : 1;
  return {level, closed, open, total, rank: RANKS.find(([min]) => level >= min)[1]};
}
function levelUp(key, kind, counts, el) {
  const x = levelOf(counts);
  const prev = state.levels[key];
  state.levels[key] = x.level;
  if (prev === undefined || x.level <= prev) return false;
  toastAs("quest levelup", h("b", {text: `${kind} level ${x.level}!`}), ` ${x.rank}: ${x.closed} of ${x.total} quests completed.`);
  play("levelup");
  if (motion() && el) fxLayer(el, "fx-levelup", 2400, h("span", {class: "ding", text: `Level ${x.level}`}));
  return true;
}
function victory(key, slain) {
  const seen = state.slain[key];
  state.slain[key] = new Set(slain.map(b => b.id));
  if (!seen) return;
  const fresh = slain.filter(b => !seen.has(b.id));
  if (!fresh.length) return;
  const b = fresh[0];
  play("levelup");
  toastAs("quest levelup", h("b", {text: "Boss slain: "}), `${b.title} (${b.total} quests${b.final ? ", final blow by " + b.final : ""}).`);
  if (!motion()) return;
  document.getElementById("victory")?.remove();
  const close = () => { box.remove(); removeEventListener("keydown", onKey, true); };
  const onKey = e => { if (e.key === "Escape") close(); };
  const box = h("div", {id: "victory", class: "victory", role: "status", "aria-live": "assertive"},
    h("div", {class: "victory-card"}, h("div", {class: "v-kicker", text: "\u2694\ufe0f Boss slain"}), h("h2", {text: b.title}),
      h("p", {text: `${b.total} ${b.total === 1 ? "quest" : "quests"} conquered${b.final ? " \u00b7 final blow by " + b.final : ""}`}),
      h("button", {type: "button", class: "primary", text: "Onward", onclick: () => close()})));
  document.body.append(box);
  addEventListener("keydown", onKey, true);
  setTimeout(close, 6000);
}
function streakChip(n) {
  return n >= 2 ? h("span", {class: "streak", title: `${n} days in a row with a finished quest`}, `\ud83d\udd25 ${n}-day streak`) : null;
}
function sumCounts(projects) {
  const out = {};
  for (const p of projects) for (const [l, n] of Object.entries(p.counts || {})) out[l] = (out[l] || 0) + (n || 0);
  return out;
}
function levelBadge(kind, counts) {
  const x = levelOf(counts);
  const label = `${kind} level ${x.level} of ${LEVEL_CAP}, ${x.rank}: ${x.closed} of ${x.total} quests completed, ${x.open} open`;
  const fill = h("span", {class: "xp-fill"});
  fill.style.width = `${Math.round(100 * x.closed / (x.total || 1))}%`;
  return h("span", {class: "level", title: label},
    h("b", {class: "lvl", text: `${kind} level ${x.level}`}), h("span", {class: "rank", text: x.rank}),
    h("span", {class: "xp", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(x.total || 1), "aria-valuenow": String(x.closed), "aria-label": label},
      fill, h("span", {class: "xp-pct", text: `${Math.floor(100 * x.closed / (x.total || 1))}%`})),
    h("small", {class: "xp-text", text: `${x.closed}/${x.total} quests`}));
}
function setKids(el, kids, sig) {
  if (sig !== undefined) { if (el.dataset.sig === sig) return false; el.dataset.sig = sig; }
  const focus = focusKey(el);
  el.replaceChildren();
  add(el, Array.isArray(kids) ? kids : [kids]);
  if (focus) refocus(el, focus);
  return true;
}
// A re-render must not drop keyboard focus: remember what had it and focus its replacement.
function focusKey(el) {
  const a = document.activeElement;
  if (!a || a === document.body || !el.contains(a)) return null;
  return {id: a.id, row: a.closest("[data-id]")?.dataset.id, tag: a.tagName, cls: a.className};
}
function refocus(el, k) {
  let t = k.id ? document.getElementById(k.id) : null;
  if (!t && k.row) {
    const row = el.querySelector(`[data-id="${CSS.escape(k.row)}"]`);
    t = row && [...row.querySelectorAll(k.tag)].find(x => x.className === k.cls);
  }
  if (t && el.contains(t)) t.focus({preventScroll: true});
}

function hash(s) { let x = 0; for (const c of s) x = (x * 31 + c.charCodeAt(0)) >>> 0; return x; }
function heroOf(name) {
  if (!name) return null;
  const lead = state.board ? state.board.lead : "";
  if (name === lead) return ["\u{1F451}", "Raid leader"];
  return CLASSES[hash(name) % CLASSES.length];
}
function avatar(name) {
  const g = (heroOf(name) || ["?"])[0];
  if (!canShow(g)) return (name || "?").slice(0, 2).toUpperCase();
  return g;
}
function localTime(t) {
  const d = new Date(t);
  return isFinite(d.getTime()) ? d.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : t;
}
function appendTimes(el, text) {
  const re = /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b/g;
  let start = 0;
  for (const m of text.matchAll(re)) {
    el.appendChild(document.createTextNode(text.slice(start, m.index)));
    const time = document.createElement("time");
    time.dateTime = m[0];
    time.title = m[0];
    time.textContent = localTime(m[0]);
    el.appendChild(time);
    start = m.index + m[0].length;
  }
  el.appendChild(document.createTextNode(text.slice(start)));
}
function waitingText(reason) {
  const m = / overlaps .* held by (.+?) on card (.+?) in workspace (.+?) until (\S+)/.exec(reason);
  if (!m) return `Waiting: ${reason}`;
  const expiry = Date.parse(m[4]);
  const end = Number.isFinite(expiry) ? expiry > Date.now() ? `lock frees ${untilText(m[4])}` : `lock expired ${untilText(m[4])}` : "lock held";
  return `Waiting for ${m[2]} (${m[1]}) - ${end}`;
}
function secondsAgo(t) { const s = (Date.now() - Date.parse(t || "")) / 1000; return isFinite(s) ? s : null; }
function ago(t) {
  const s = secondsAgo(t);
  if (s === null) return "";
  if (s < 60) return Math.max(1, s | 0) + "s";
  if (s < 3600) return (s / 60 | 0) + " min";
  if (s < 86400) return (s / 3600 | 0) + " h";
  return (s / 86400 | 0) + " d";
}
function untilText(t) {
  const ms = Date.parse(t || "") - Date.now();
  if (!isFinite(ms)) return "";
  const m = Math.round(Math.abs(ms) / 60000);
  const span = m >= 120 ? `${Math.round(m / 60)} h` : `${Math.max(1, m)} min`;
  return ms > 0 ? `in ${span}` : `${span} ago`;
}
function resetText(t) {
  const d = new Date(t || "");
  if (!isFinite(d.getTime())) return "";
  const soon = d.getTime() - Date.now() < DAY_MS;
  return soon ? d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit"}) : d.toLocaleString(undefined, {weekday: "short", hour: "2-digit", minute: "2-digit"});
}
function usd(v) { v = +v || 0; return "$" + (v >= 100 ? v.toFixed(0) : v.toFixed(2)); }
function ktok(n) { n = +n || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n); }
function mins(ms) { const m = Math.round((+ms || 0) / 60000); return m >= 120 ? (m / 60).toFixed(1) + " h" : m + " min"; }
function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + "s")}`; }
function modelName(id) {
  const names = (state.board && state.board.model_names) || (state.overview && state.overview.model_names) || {};
  return id && id !== "unknown" ? (names[id] || id) : "";
}
function statusBadge(st) {
  const s = STATUS[st] || STATUS.idle;
  return h("span", {class: "badge", vars: {"--s": `var(--s-${st})`}, title: s.raid}, ico(s.icon), h("b", {text: s.label}));
}
function billBadge(mode) {
  if (!mode || mode === "unknown" || !BILL[mode]) return null;
  return h("span", {class: "bill " + mode, title: BILL[mode][1]}, BILL[mode][0]);
}
function identity(a) {
  a = a || {};
  return [modelName(a.model), a.runtime].filter(Boolean).join(" · ");
}
function evidenceCount(x) {
  x = x || {};
  return (x.verify_passed || 0) + (x.verify_failed || 0) + (x.review_approve || 0) + (x.review_changes || 0) + (x.rework || 0) +
    (x.lock_violations || 0) + (x.lock_denials || 0) + (x.stalls || 0);
}
function trustBadge(a) {
  const x = (a && a.reputation_inputs) || {};
  if (!Number.isFinite(a && a.reputation) || !evidenceCount(x)) return null;
  const title = `Verification: ${x.verify_passed || 0} passed, ${x.verify_failed || 0} failed; reviews: ${x.review_approve || 0} approved, ${x.review_changes || 0} changes requested`;
  return h("span", {class: "pill reputation", title, text: `Trust ${a.reputation}/100`});
}
function leadCmd(rest) {
  const src = (state.board && state.board.lead_src) || '"$LEAD_SECRET_FILE"';
  return `raid --lead-secret ${src} ${rest}`;
}
const CUE_LIST = [
  ["sent", "Message sent", "delivered right away"],
  ["queued", "Message queued", "held until the recipient is free"],
  ["delivered", "Message delivered", "a queued message is released"],
  ["read", "Message read", "the recipient opened it"],
  ["note", "Note", "an agent adds a note"],
  ["claim", "Claim", "a card moves to in progress"],
  ["review", "Review", "a card is handed in for review"],
  ["blocked", "Blocked", "a card is blocked"],
  ["done", "Done", "a card is finished"],
  ["stalled", "Stalled", "an agent goes quiet too long"],
  ["expiring", "Lease expiring", "under 5 minutes left"],
  ["expired", "Lease expired", "a held lease ran out"],
  ["posted", "New card", "a card is added or reopened"],
  ["coin", "Spend", "new tokens recorded on a card"],
  ["levelup", "Level up", "a workspace reaches a new progress level"]];
const CUE_NAMES = new Set(CUE_LIST.map(c => c[0]));
let actx = null, master = null, noiseBuf = null;
function soundReady() { return !!actx && actx.state === "running"; }
function updateSound() {
  const b = $("sound");
  b.setAttribute("aria-pressed", String(state.sound));
  b.textContent = state.sound ? "Sound on" : "Sound muted";
  $("vol").disabled = !state.sound;
  $("soundSummary").textContent = state.sound ? "Sound" : "Sound (muted)";
  $("soundBanner").hidden = !state.sound || soundReady();
}
function setVol(v) { state.vol = v; if (master) master.gain.value = Math.pow(v / 100, 1.5) * 1.6; }
function audio() {
  if (actx) return true;
  try {
    actx = new (window.AudioContext || window.webkitAudioContext)();
    const comp = actx.createDynamicsCompressor();
    comp.threshold.value = -10; comp.knee.value = 6; comp.ratio.value = 8; comp.attack.value = .002; comp.release.value = .12;
    master = actx.createGain();
    master.connect(comp); comp.connect(actx.destination);
    setVol(state.vol);
    actx.onstatechange = updateSound;
    return true;
  } catch (_) { actx = null; return false; }
}
function unlock() {
  if (!state.sound) return;
  if (audio() && actx.state !== "running") actx.resume().then(updateSound, () => {});
  updateSound();
}
function env(g, t, lvl, attack, dur) {
  g.gain.setValueAtTime(.0001, t);
  g.gain.exponentialRampToValueAtTime(lvl, t + attack);
  g.gain.exponentialRampToValueAtTime(.0001, t + dur);
}
function tone(f, dur, o = {}) {
  const a = actx, t = a.currentTime + (o.at || 0), osc = a.createOscillator(), g = a.createGain();
  osc.type = o.type || "triangle";
  osc.frequency.setValueAtTime(f, t);
  if (o.to) osc.frequency.exponentialRampToValueAtTime(o.to, t + dur);
  env(g, t, o.lvl || .7, o.attack || .008, dur);
  let out = osc;
  if (o.lp) { const fl = a.createBiquadFilter(); fl.type = "lowpass"; fl.frequency.value = o.lp; osc.connect(fl); out = fl; }
  out.connect(g); g.connect(master);
  osc.start(t); osc.stop(t + dur + .05);
}
function noise(dur, o = {}) {
  const a = actx, t = a.currentTime + (o.at || 0);
  if (!noiseBuf) {
    noiseBuf = a.createBuffer(1, a.sampleRate | 0, a.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  }
  const src = a.createBufferSource(), fl = a.createBiquadFilter(), g = a.createGain();
  src.buffer = noiseBuf;
  fl.type = "bandpass"; fl.frequency.setValueAtTime(o.f || 2000, t); fl.Q.value = o.q || 1.5;
  if (o.to) fl.frequency.exponentialRampToValueAtTime(o.to, t + dur);
  env(g, t, o.lvl || .8, o.attack || .004, dur);
  src.connect(fl); fl.connect(g); g.connect(master);
  src.start(t); src.stop(t + dur + .05);
}
const CUES = {
  sent: () => { noise(.22, {f: 800, to: 4000, lvl: .5}); tone(520, .22, {type: "sine", to: 1320, lvl: .8}); },
  queued: () => { tone(330, .16, {lvl: .8}); tone(330, .22, {at: .2, lvl: .7}); },
  delivered: () => { tone(784, .3, {type: "sine", lvl: .8}); tone(1175, .45, {type: "sine", at: .1, lvl: .8}); },
  read: () => { tone(1568, .09, {type: "sine", lvl: .7}); tone(2093, .14, {type: "sine", at: .07, lvl: .6}); },
  note: () => { noise(.06, {f: 3200, q: 4, lvl: .9}); noise(.06, {f: 2400, q: 4, lvl: .8, at: .09}); },
  claim: () => [392, 523, 784].forEach((f, i) => tone(f, .12, {type: "square", at: i * .07, lvl: .45, lp: 3000})),
  review: () => { tone(659, .2, {lvl: .8}); tone(880, .34, {at: .18, lvl: .8}); },
  blocked: () => { tone(220, .5, {type: "sawtooth", to: 110, lvl: .6, lp: 1200}); tone(233, .5, {type: "sawtooth", to: 116, lvl: .4, lp: 1200}); },
  done: () => { [523, 659, 784].forEach((f, i) => tone(f, .16, {at: i * .1, lvl: .7})); tone(1047, .6, {at: .3, lvl: .8}); tone(1319, .6, {type: "sine", at: .3, lvl: .4}); },
  stalled: () => { tone(392, .26, {lvl: .8}); tone(262, .55, {at: .22, lvl: .8, to: 247}); },
  expiring: () => [0, .16, .32].forEach(at => tone(1000, .07, {type: "square", at, lvl: .45, lp: 4000})),
  expired: () => { tone(880, .14, {type: "square", lvl: .45, lp: 3000}); tone(440, .4, {type: "square", at: .14, lvl: .45, lp: 2000}); },
  posted: () => { tone(523, .3, {lvl: .7}); tone(1046, .25, {type: "sine", lvl: .35}); },
  levelup: () => { [392, 523, 659, 784, 1047].forEach((f, i) => tone(f, .14, {at: i * .08, lvl: .6})); tone(1568, .9, {type: "sine", at: .42, lvl: .5}); tone(784, .9, {at: .42, lvl: .5}); },
  coin: () => { tone(988, .08, {type: "square", lvl: .4, lp: 5000}); tone(1319, .35, {type: "square", at: .07, lvl: .4, lp: 5000}); }};
const pending = new Set();
let flushTimer = 0;
function cueOn(n) { return state.sound && !state.muted.has(n); }
function voice(n) {
  if (!soundReady() || !master) return;
  try { CUES[n](); } catch (_) {}
}
function emit(n) {
  document.dispatchEvent(new CustomEvent("raidlock:cue", {detail: n}));
  voice(n);
}
function preview(n) {
  if (!state.sound) return;
  unlock();
  if (soundReady()) voice(n);
  else if (actx) actx.resume().then(() => voice(n), () => {});
}
function play(n) {
  if (!CUE_NAMES.has(n) || !cueOn(n)) return;
  pending.add(n);
  if (!flushTimer) flushTimer = setTimeout(flush, 0);
}
function flush() {
  flushTimer = 0;
  const names = [...pending];
  pending.clear();
  names.forEach((n, i) => setTimeout(() => { if (cueOn(n)) emit(n); }, i * 260));
}
function buildCues() {
  const box = $("cueList");
  const kids = [box.querySelector("legend")];
  for (const [id, label, hint] of CUE_LIST) {
    const cb = h("input", {type: "checkbox", id: "cue-" + id, "data-cue": id});
    cb.checked = !state.muted.has(id);
    kids.push(h("label", {class: "cue", for: "cue-" + id, title: hint}, cb, h("span", null, label, h("small", {text: hint}))),
      h("button", {type: "button", class: "cue-try", "data-try": id, "aria-label": `Play the ${label.toLowerCase()} sound`, text: "Play"}));
  }
  box.replaceChildren(...kids);
}

function updateAchieve() {
  const b = $("achieveBtn");
  b.setAttribute("aria-pressed", String(state.achievements));
  b.textContent = state.achievements ? "Achievements: on" : "Achievements: off";
}
function updateNotify() {
  const b = $("notify");
  const denied = "Notification" in window && Notification.permission === "denied";
  b.setAttribute("aria-pressed", state.notify);
  b.textContent = !("Notification" in window) ? "Desktop alerts: not available in this browser" : denied ? "Desktop alerts: blocked by the browser" : state.notify ? "Desktop alerts: on" : "Desktop alerts: off";
  b.disabled = !("Notification" in window) || denied;
}
function notify(title, body) {
  if (!state.notify || !("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
  try { new Notification(title, {body: body.slice(0, 200), tag: "raidlock"}); } catch (_) {}
}

function toast(...parts) { return toastAs("", ...parts); }
function toastAs(cls, ...parts) {
  const d = h("div", {class: "toast" + (cls ? " " + cls : "")}, parts);
  const t = $("toasts");
  t.prepend(d);
  if (t.showPopover) { try { if (t.matches(":popover-open")) t.hidePopover(); t.showPopover(); } catch (_) {} }
  while ($("toasts").children.length > 4) $("toasts").lastChild.remove();
  setTimeout(() => d.remove(), 6200);
  notify("raidlock", d.textContent);
}
function burst(el, emoji) {
  if (!motion() || !el) return;
  const r = el.getBoundingClientRect();
  for (let i = 0; i < 14; i++) {
    const s = h("span", {class: "burst", "aria-hidden": "true", text: emoji[i % emoji.length]});
    const a = Math.random() * Math.PI * 2, d = 60 + Math.random() * 90;
    s.style.left = (r.left + r.width / 2) + "px"; s.style.top = (r.top + r.height / 2) + "px";
    s.style.setProperty("--dx", Math.cos(a) * d + "px"); s.style.setProperty("--dy", Math.sin(a) * d + "px");
    document.body.appendChild(s); setTimeout(() => s.remove(), 1400);
  }
}
function centre(el) { const r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }
function onScreen(el) { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.bottom > 0 && r.top < innerHeight; }
function raven(from, to) {
  if (!motion()) return;
  const q = n => document.querySelector(`.agent[data-agent="${CSS.escape(n)}"] .av`) || document.querySelector(`.chip[data-agent="${CSS.escape(n)}"]`);
  const a = q(from), b = q(to);
  const [x1, y1] = onScreen(a) ? centre(a) : [24, innerHeight / 2];
  const [x2, y2] = onScreen(b) ? centre(b) : [innerWidth - 60, innerHeight - 60];
  const mx = (x1 + x2) / 2, my = Math.min(y1, y2) - 120;
  for (const [glyph, dx, dy, size] of [["\u{1F426}", 0, 0, ""], ["\u{1F4DC}", 10, 14, "13px"]]) {
    const s = h("span", {class: "raven", "aria-hidden": "true", text: glyph});
    if (size) s.style.fontSize = size;
    document.body.appendChild(s);
    s.animate([{transform: `translate(${x1 + dx}px,${y1 + dy}px) scale(.6)`, opacity: 0},
      {transform: `translate(${x1 + dx}px,${y1 + dy - 20}px) scale(1)`, opacity: 1, offset: .1},
      {transform: `translate(${mx + dx}px,${my + dy}px) scale(1.3) rotate(-10deg)`, offset: .5},
      {transform: `translate(${x2 + dx}px,${y2 + dy}px) scale(.8)`, opacity: 1, offset: .92},
      {transform: `translate(${x2 + dx}px,${y2 + dy}px) scale(.3)`, opacity: 0}], {duration: 1600, easing: "ease-in-out"}).onfinish = () => s.remove();
  }
}

const FX_VERIFY = /^verify (passed|failed)\b/;
const FX_MS = {done: 1100, verify: 1900, claim: 1300, refused: 1100, cost: 1700};
function fxLive(l) {
  if (!l || l.live === false) return false;
  return !l.expires || Date.parse(l.expires) > Date.now();
}
function fxMoney(k) { k = k || {}; return {usd: +k.usd_equiv || +k.usd || 0, tokens: +k.tokens || 0}; }
function fxOverlaps(cl) {
  return ((cl && cl.reasons) || []).map(String).filter(r => / overlaps /.test(r)).map(r => r.replace(/ until .*$/, ""));
}
function fxSnap(data) {
  const cards = {}, claim = {}, agents = {};
  for (const c of (data && data.cards) || []) {
    cards[c.id] = {lane: c.lane || "", owner: c.owner || "", live: fxLive(c.lease), notes_count: +c.notes_count || 0,
      notes: (c.notes || []).map(n => String((n && n.text) || "")), money: fxMoney(c.cost)};
  }
  for (const [id, x] of Object.entries((data && data.claimable) || {})) claim[id] = fxOverlaps(x);
  for (const [n, a] of Object.entries((data && data.agents) || {})) agents[n] = {status: (a && a.status) || "idle", card: (a && a.card) || ""};
  return {cards, claim, agents};
}
function fxNewNotes(p, c) {
  const n = c.notes_count - p.notes_count;
  return n > 0 ? c.notes.slice(-n) : [];
}
function fxDiff(prev, cur) {
  const out = [];
  if (!prev || !cur) return out;
  for (const [id, c] of Object.entries(cur.cards)) {
    const p = prev.cards[id];
    if (!p) continue;
    if (c.lane === "done" && p.lane !== "done") out.push({kind: "done", card: id});
    if (c.lane === "blocked" && p.lane !== "blocked") out.push({kind: "blocked", card: id});
    const v = fxNewNotes(p, c).map(t => FX_VERIFY.exec(t)).filter(Boolean).pop();
    if (v) out.push({kind: "verify", card: id, passed: v[1] === "passed"});
    if (c.owner && c.live && (!p.live || p.owner !== c.owner)) out.push({kind: "claim", card: id, agent: c.owner});
    const was = new Set(prev.claim[id] || []);
    if ((cur.claim[id] || []).some(r => !was.has(r))) out.push({kind: "refused", card: id});
    const usd = c.money.usd - p.money.usd, tokens = c.money.tokens - p.money.tokens;
    if (usd >= 0.005 || (tokens > 0 && usd > -0.005)) out.push({kind: "cost", card: id, usd: Math.max(0, usd), tokens: Math.max(0, tokens)});
  }
  for (const [n, a] of Object.entries(cur.agents)) {
    const was = prev.agents[n];
    if (a.status === "stalled" && was && was.status !== "stalled") out.push({kind: "stalled", agent: n, card: a.card});
  }
  return out;
}
function fxPulses(pulse, events, cur) {
  const next = new Map(pulse || []);
  for (const e of events || []) {
    if (e.kind === "blocked") next.set("card:" + e.card, "blocked");
    if (e.kind === "stalled") next.set("agent:" + e.agent, "stalled");
  }
  for (const key of [...next.keys()]) {
    const [type, id] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
    const ok = type === "card" ? (cur.cards[id] || {}).lane === "blocked" : (cur.agents[id] || {}).status === "stalled";
    if (!ok) next.delete(key);
  }
  return next;
}
function fxFrames(data) {
  const out = {};
  for (const f of (data && data.agents) || []) {
    const ctx = (f && f.context) || {};
    if (f && f.agent && Number.isFinite(ctx.pct)) out[f.agent] = {pct: ctx.pct, level: ctx.level || "ok"};
  }
  return out;
}
function fxContextDiff(prev, cur) {
  const out = [];
  if (!prev || !cur) return out;
  for (const [agent, c] of Object.entries(cur)) {
    const p = prev[agent];
    if (p && (Math.abs(c.pct - p.pct) >= 0.5 || c.level !== p.level)) out.push({kind: "context", agent, from: p.pct, to: c.pct, level: c.level, was: p.level});
  }
  return out;
}
function fxEarned(data) {
  return ((data && data.achievements) || []).filter(a => a && a.earned).map(a => a.id);
}
function fxNewEarned(prev, cur) {
  if (!prev || !cur) return [];
  const was = new Set(prev);
  return cur.filter(id => !was.has(id));
}
window.RaidlockFx = {snap: fxSnap, diff: fxDiff, pulses: fxPulses, frames: fxFrames, contextDiff: fxContextDiff, earned: fxEarned, newEarned: fxNewEarned};

function fxEmit(kind, detail) { document.dispatchEvent(new CustomEvent("raidlock:fx", {detail: {...detail, kind}})); }
function fxTargets(sel) { return [...document.querySelectorAll(sel)].filter(onScreen); }
function fxRows(id) { return id ? fxTargets(`.row[data-id="${CSS.escape(id)}"]`) : []; }
function fxAgents(name) { return name ? fxTargets(`.agent[data-agent="${CSS.escape(name)}"]`) : []; }
function fxLayer(el, cls, ms, ...kids) {
  const l = h("span", {class: "fx-layer " + cls, "aria-hidden": "true"}, kids);
  el.appendChild(l);
  setTimeout(() => l.remove(), ms + 100);
  return l;
}
function fxRays() {
  const rays = [];
  for (let i = 0; i < 12; i++) rays.push(svg("line", {x1: 0, y1: -20, x2: 0, y2: i % 2 ? -34 : -44, transform: `rotate(${i * 30})`}));
  return svg("svg", {class: "fx-rays", "aria-hidden": "true", viewBox: "-50 -50 100 100", width: 120, height: 120}, svg("circle", {r: 15}), rays);
}
function fxGlyph(cls) {
  return svg("svg", {class: cls, "aria-hidden": "true", viewBox: "-20 -20 40 40", width: 40, height: 40},
    svg("path", {class: "rim", d: "M0-17L15-8.5V8.5L0 17-15 8.5V-8.5Z"}), svg("path", {class: "mark", d: "M-7 0l5 5 9-10"}));
}
function fxCast(cls) { return h("span", {class: "fx-cast " + cls}, h("i")); }
function fxCostText(e) {
  if (e.usd >= 0.005) return "+" + usd(e.usd);
  return "+" + ktok(e.tokens) + " tok";
}
const FX = {
  done: e => fxRows(e.card).forEach(el => fxLayer(el, "fx-done", FX_MS.done, fxRays())),
  verify: e => fxRows(e.card).forEach(el => fxLayer(el, "fx-verify" + (e.passed ? "" : " fail"), FX_MS.verify, fxCast(e.passed ? "full" : "cut"), e.passed ? fxGlyph("fx-stamp") : null)),
  claim: e => [...fxRows(e.card), ...fxAgents(e.agent)].forEach(el => fxLayer(el, "fx-claim", FX_MS.claim, h("span", {class: "fx-shield"}))),
  refused: e => fxRows(e.card).forEach(el => fxLayer(el, "fx-refused", FX_MS.refused, fxCast("cut"), h("span", {class: "fx-ring"}), h("span", {class: "fx-ring late"}))),
  cost: e => fxRows(e.card).forEach(el => fxLayer(el, "fx-cost", FX_MS.cost, h("span", {class: "fx-float", text: fxCostText(e)}))),
  blocked: () => {},
  stalled: () => {}};
function fxPlay(events) {
  if (!motion()) return;
  for (const e of events) {
    fxEmit(e.kind, e);
    try { FX[e.kind](e); } catch (_) {}
  }
}
let fxIO = null;
function fxDecorate() {
  const on = motion();
  const want = new Map();
  if (on) {
    for (const [key, kind] of state.fxPulse) {
      const id = key.slice(key.indexOf(":") + 1);
      if (key.startsWith("card:")) document.querySelectorAll(`.row[data-id="${CSS.escape(id)}"]`).forEach(el => want.set(el, kind));
      else {
        document.querySelectorAll(`.agent[data-agent="${CSS.escape(id)}"]`).forEach(el => want.set(el, kind));
        const card = ((state.board && state.board.agents) || {})[id];
        if (card && card.card) document.querySelectorAll(`.row[data-id="${CSS.escape(card.card)}"]`).forEach(el => { if (!want.has(el)) want.set(el, kind); });
      }
    }
  }
  for (const el of document.querySelectorAll(".fx-pulse")) if (!want.has(el)) el.classList.remove("fx-pulse", "fx-stall", "fx-off");
  for (const [el, kind] of want) { el.classList.add("fx-pulse"); el.classList.toggle("fx-stall", kind === "stalled"); }
  if (!("IntersectionObserver" in window)) return;
  if (!fxIO) fxIO = new IntersectionObserver(list => { for (const x of list) x.target.classList.toggle("fx-off", !x.isIntersecting); });
  fxIO.disconnect();
  for (const el of want.keys()) fxIO.observe(el);
}
function fxBoard(data) {
  const cur = fxSnap(data);
  const events = fxDiff(state.fxPrev, cur).filter(e => !state.first || e.kind === "stalled");
  state.fxPrev = cur;
  state.fxPulse = fxPulses(state.fxPulse, motion() ? events : [], cur);
  fxPlay(events);
  fxDecorate();
}
function fxBanner(list) {
  if (!list.length) return;
  const old = document.querySelector(".ach-banner");
  if (old) old.remove();
  const a = list[0];
  const more = list.length > 1 ? ` and ${list.length - 1} more` : "";
  const b = h("div", {class: "ach-banner", role: "status"}, fxGlyph("ach-seal"),
    h("div", {class: "ach-text"}, h("small", {text: "Achievement unlocked"}), h("b", {text: a.name + more})));
  document.body.appendChild(b);
  setTimeout(() => b.remove(), 6000);
  if (motion()) fxEmit("achievement", {id: a.id});
}
function fxView(id, data) {
  if (id === "frames") {
    const cur = fxFrames(data), changes = fxContextDiff(state.fxView.frames, cur);
    state.fxView.frames = cur;
    if (!motion()) return;
    for (const x of changes) {
      fxEmit("context", x);
      for (const fr of document.querySelectorAll(`#viewPane .frame[data-agent="${CSS.escape(x.agent)}"]`)) {
        const fill = fr.querySelector(".fbar.hp .track i");
        if (fill && x.to > 0 && fill.animate) fill.animate([{transform: `scaleX(${Math.min(x.from / x.to, 8)})`}, {transform: "scaleX(1)"}], {duration: 700, easing: "cubic-bezier(.2,.8,.3,1)"});
        if (x.level !== x.was) fxLayer(fr, "fx-level lvl-" + x.level, 900);
      }
    }
  } else if (id === "achievements") {
    const cur = fxEarned(data), fresh = fxNewEarned(state.fxView.achievements, cur);
    state.fxView.achievements = cur;
    const names = Object.fromEntries(((data && data.achievements) || []).map(a => [a.id, a]));
    fxBanner(fresh.map(x => names[x]).filter(Boolean));
  }
}
function updateAnim() {
  const b = $("animBtn");
  b.setAttribute("aria-pressed", String(state.anim));
  b.textContent = state.anim ? "Animations: on" : "Animations: off";
  document.documentElement.setAttribute("data-anim", state.anim ? "on" : "off");
}

async function getJSON(url, key) {
  const headers = {};
  if (key && state.etag[key]) headers["If-None-Match"] = state.etag[key];
  const r = await fetch(url, {cache: "no-store", headers});
  if (r.status === 304) return null;
  if (!r.ok) { const e = new Error("http " + r.status); e.status = r.status; throw e; }
  if (key) state.etag[key] = r.headers.get("ETag") || "";
  return r.json();
}
function api(kind, ws, extra) { return `/api/${kind}/${encodeURIComponent(ws)}${extra || ""}`; }
function href(id) { return id === "*" ? "/" : "/" + encodeURIComponent(id); }

function copyCommand(button, text, label) {
  const restore = () => { button.textContent = label; };
  const done = ok => {
    button.textContent = ok ? "Copied" : "Copy failed";
    if (ok) toast("Copied. Paste it in the lead terminal: ", h("code", {text}));
    else {
      const row = button.closest("[data-id]");
      if (row) openRow(row, true);
      toast("Copy failed: select the command in Examine and copy it by hand.");
    }
    setTimeout(restore, 1500);
  };
  try { navigator.clipboard.writeText(text).then(() => done(true), () => done(false)); } catch (_) { done(false); }
}
function cmdLine(text) {
  return h("div", {class: "cmd"}, h("code", {text}),
    h("button", {type: "button", class: "copy", "aria-label": `Copy ${text}`, text: "Copy", "data-copy": text}));
}

function attention(o) { return o.missing || o.blocked || o.stalled ? 0 : o.review ? 1 : o.doing ? 2 : 3; }
function oldestStalled(agents, titles) {
  let best = null;
  for (const a of agents) {
    if (a.status !== "stalled") continue;
    const t = Date.parse(a.heartbeat || "");
    const at = isFinite(t) ? t : 0;
    if (!best || at < best.at) best = {at, name: a.name, title: a.title || (titles && titles[a.card]) || a.card || ""};
  }
  return best;
}
function statusLine(o) {
  const seg = (n, label, cls) => n ? h("span", {class: `seg ${cls}`}, h("b", {text: String(n)}), " " + label) : null;
  const parts = [seg(o.needs, "await the raid leader", "s-review"), seg(o.doing, "on a quest", "s-doing"),
    seg(o.blocked, "cursed", "s-blocked"), seg(o.todo, "on the quest board", "s-todo")];
  if (o.total) parts.push(h("span", {class: "seg s-done"}, h("b", {text: String(o.done)}), ` of ${o.total} quests completed`));
  if (o.stalled) {
    const mins = o.stalled.at ? Math.max(1, Math.round((Date.now() - o.stalled.at) / 60000)) : 0;
    const since = !mins ? "" : mins >= 120 ? `${Math.round(mins / 60)} h` : `${mins} min`;
    parts.push(h("span", {class: "seg s-stalled", title: `${o.stalled.title ? o.stalled.title + ": " : ""}${o.stalled.name} has been silent${since ? " for " + since : ""}`},
      "Stalled: ", h("b", {text: o.stalled.title || o.stalled.name}), since ? ` (${since})` : ""));
  }
  if (o.today > 0) parts.push(h("span", {class: "seg s-spend", title: "Spend recorded today (UTC), at API prices"}, h("b", {text: usd(o.today)}), " today"));
  const kids = parts.filter(Boolean);
  return kids.length || o.quiet ? kids : [h("span", {class: "seg zero", text: "No cards yet"})];
}
function repoChip(r, interactive) {
  const label = [r.name, h("span", {class: "rc", text: String(r.cards)})];
  if (!interactive) return h("span", {class: "repo", title: `${plural(r.cards, "card")} in ${r.name}`}, label);
  return h("button", {type: "button", class: "repo", "data-repo": r.name, "aria-pressed": String(state.repo === r.name), title: `Show only ${r.name}`}, label);
}

function needRule() { return +(((state.board.settings || {}).require_review || {}).count) || 0; }
function leaseText(c) {
  const l = c.lease;
  if (!l) return null;
  if (!l.expires) return h("span", {class: "pill lock", title: (l.paths || []).join(", ") || "no locked paths"}, "Lease held");
  const ms = Date.parse(l.expires) - Date.now();
  const title = `Locked paths: ${(l.paths || []).join(", ") || "none"}. Lease ends ${l.expires}.`;
  if (ms <= 0 || l.live === false) return h("span", {class: "pill lock low", title}, `Lease expired ${untilText(l.expires)}`);
  return h("span", {class: "pill lock" + (ms < 600000 ? " low" : ""), title}, `${ms < 600000 ? "Lease expiring soon: " : "Lease ends "}${untilText(l.expires)}`);
}
function canTakeOver(c) {
  const l = c.lease;
  if (c.lane !== "doing" || !c.owner || !l || !l.expires) return false;
  if (c.owner === state.board.lead) return false;
  return Date.parse(l.expires) <= Date.now();
}
function takeoverPill(c) {
  if (!canTakeOver(c)) return null;
  return h("span", {class: "pill takeover", title: `${c.owner}'s lease expired. Another worker can run: raid claim ${c.id} --take-over`}, `Can be taken over from ${c.owner}`);
}
function claimPill(c) {
  const cl = (state.board.claimable || {})[c.id];
  if (!cl || c.lane !== "todo") return null;
  if (cl.ok) return h("span", {class: "pill claim", title: "A member of the card's team can claim it now"}, "Ready");
  const r = cl.reasons || [];
  if (!r.length) return null;
  return h("span", {class: "pill noclaim", title: r.join("; ")}, waitingText(r[0]), r.length > 1 ? ` +${r.length - 1}` : "");
}
function approvalPill(c) {
  if (c.lane !== "review") return null;
  const need = needRule();
  const got = (c.approvals || []).length;
  if (got >= Math.max(need, 1)) return h("span", {class: "pill appr ok", title: `Approved by ${c.approvals.join(", ")}`}, "Approved");
  return h("span", {class: "pill appr", title: need ? `This board needs ${plural(need, "approving review")} before a card closes` : "No approving review yet"},
    need ? `Approvals ${got}/${need}` : "Not reviewed yet");
}
function costPill(c) {
  const k = c.cost || {};
  const eq = +k.usd_equiv || 0, paid = +k.usd || 0, tokens = +k.tokens || 0;
  if (!eq && !paid && !tokens) return null;
  const money = paid && Math.abs(eq - paid) >= 0.005 ? `${usd(paid)} paid` : (paid || eq) ? usd(paid || eq) : "";
  return h("span", {class: "pill coin", title: "Cost of this card's recorded runs; plan usage is shown at API prices"},
    [money, tokens ? `${ktok(tokens)} tokens` : "", k.duration_ms ? mins(k.duration_ms) : ""].filter(Boolean).join(" · "));
}
const PR_CHECK = {success: "checks passed", failure: "checks failed", pending: "checks running", none: ""};
function prPill(c) {
  const p = c.pr;
  if (!p) return null;
  const st = (p.state || "linked").toLowerCase();
  const ck = (p.checks || {}).state || "none";
  const text = `PR #${p.number} ${st}${PR_CHECK[ck] ? ", " + PR_CHECK[ck] : ""}`;
  const title = p.held ? `Merged but not closed: ${p.held}` : null;
  const safe = /^https:\/\/[^\s"<>]+\/pull\/\d+$/.test(p.url || "");
  return safe ? h("a", {class: "pill pr", href: p.url, target: "_blank", rel: "noopener noreferrer", title}, text) : h("span", {class: "pill pr", title}, text);
}
function ownerPill(c) {
  if (!c.owner) return null;
  const a = (state.board.agents || {})[c.owner] || {};
  return h("span", {class: "pill ow", title: identity(a) || null}, ico(avatar(c.owner)), c.owner === state.board.lead ? `${c.owner} (lead)` : c.owner);
}
const SYSTEM_NOTE = /^(claimed by \S+ \(fence \d+\)|lease (renewed|resumed|released|expired)\b|-> (doing|todo)\b)/;
function lastNoteObj(c) { return (c.notes || []).slice().reverse().find(x => !SYSTEM_NOTE.test(String(x.text || ""))) || null; }
function lastNote(c) { const n = lastNoteObj(c); return n ? String(n.text || "") : ""; }
function asksLead(c, item) {
  const n = lastNoteObj(c);
  if (n && n.by === state.board.lead) return false;
  return ASK.test(n ? String(n.text || "").replace(/^-> blocked\s*/, "") : blockedReason(c, item));
}
function cardDetail(c, extra) {
  const d = state.details[c.id];
  const cl = (state.board.claimable || {})[c.id];
  const rows = [];
  if (extra) rows.push(extra);
  if (cl && !cl.ok && (cl.reasons || []).length) rows.push(h("div", {class: "why"}, h("b", {text: "Not claimable yet: "}), cl.reasons.join("; ")));
  if (c.team) rows.push(h("div", null, h("b", {text: "Party: "}), c.team));
  if (c.ticket) rows.push(h("div", null, h("b", {text: "Ticket: "}), c.ticket));
  if ((c.allow_protected || []).length) rows.push(h("div", null, h("b", {text: "Lead-approved protected paths: "}), c.allow_protected.join(", ")));
  if ((c.lock_exclusions || []).length) rows.push(h("div", null, h("b", {text: "Excluded lock paths: "}), c.lock_exclusions.join(", ")));
  if (c.cancel_reason) rows.push(h("div", null, h("b", {text: "Cancelled: "}), c.cancel_reason));
  if (c.scope) rows.push(h("div", null, h("b", {text: "Scope: "}), c.scope));
  if ((c.locks || []).length) rows.push(h("div", null, h("b", {text: "Warded paths: "}), c.locks.join(", ")));
  if ((c.depends || []).length) rows.push(h("div", null, h("b", {text: "After quests: "}), c.depends.join(", ")));
  if (c.evidence) rows.push(h("div", null, h("b", {text: "Proof of deed: "}), /^https:\/\//.test(c.evidence) ? h("a", {href: c.evidence, target: "_blank", rel: "noopener noreferrer", text: c.evidence}) : c.evidence));
  if (c.verify) rows.push(h("div", null, h("b", {text: "Proof: "}), c.verify));
  if ((c.approvals || []).length) rows.push(h("div", null, h("b", {text: "Vouched for by: "}), c.approvals.join(", ")));
  if (c.pr && c.pr.held) rows.push(h("div", {class: "why"}, h("b", {text: "Merged, not closed: "}), c.pr.held));
  for (const r of (d && d.reviews) || []) rows.push(h("div", null, h("b", {text: `Review by ${r.by}, ${r.verdict}: `}), r.note || ""));
  const notes = ((d || c).notes || []).slice().reverse();
  rows.push(h("h3", {class: "drawer-h", text: "Quest chronicle"}));
  for (const n of notes) rows.push(h("div", null, h("b", {text: `${ago(n.ts)} ago, ${n.by || "?"}: `}), n.text));
  if (!notes.length) rows.push(h("div", {class: "cls", text: "No notes yet."}));
  if (!d && c.notes_count > (c.notes || []).length) rows.push(h("div", {class: "cls", text: `Loading ${c.notes_count} notes…`}));
  rows.push(cmdLine(`raid show ${c.id}`));
  return rows;
}
function moreMenu(id, commands, label) {
  if (!(commands || []).length) return null;
  return h("details", {class: "more-menu", "data-cmds": JSON.stringify(commands)}, h("summary", {class: "tog", "aria-label": `${label || "More actions"} for ${id}`, text: label || "More"}),
    h("div", {class: "more-body"}));
}
function fillMore(m) {
  const body = m.querySelector(".more-body");
  if (!body || body.childElementCount) return;
  let cmds = [];
  try { cmds = JSON.parse(m.dataset.cmds || "[]"); } catch (_) {}
  body.replaceChildren(h("p", {class: "cls", text: "Copy a command and run it in the lead terminal."}), ...cmds.map(cmdLine));
}
function row(c, opts) {
  opts = opts || {};
  const op = state.open.has(c.id);
  const actions = [];
  if (opts.primary) actions.push(h("button", {type: "button", class: "primary", "data-cmd": opts.primary.cmd, "data-label": opts.primary.label,
    title: opts.primary.cmd, "aria-label": `${opts.primary.label} ${c.title}: copy the command`, text: opts.primary.label}));
  if (opts.more) actions.push(opts.more);
  if (!opts.oneLine) actions.push(h("button", {type: "button", class: "tog details", "aria-expanded": String(op), "aria-controls": "cardDrawer", text: "Examine"}));
  return h("li", {class: "row" + (op ? " open" : "") + (opts.oneLine ? " one" : ""), "data-id": c.id, "data-lane": c.lane, "data-kind": opts.kind || c.lane},
    h("div", {class: "row-main"},
      h("div", {class: "row-title"}, h("strong", {class: "t", text: c.title}), h("span", {class: "cid", text: c.id})),
      opts.reason ? h("div", {class: "reason", text: opts.reason}) : null,
      opts.meta && opts.meta.filter(Boolean).length ? h("div", {class: "meta"}, opts.meta) : null),
    h("div", {class: "row-actions"}, actions));
}
function openRow(el, force) {
  const id = el.dataset.id;
  const open = force === undefined ? !state.open.has(id) : force;
  state.open.clear();
  if (open) state.open.add(id);
  for (const row of document.querySelectorAll(".row")) {
    const selected = open && row.dataset.id === id;
    row.classList.toggle("open", selected);
    row.querySelector("button.details")?.setAttribute("aria-expanded", String(selected));
  }
  if (!open) { closeDrawer(); return; }
  refreshRow(el);
  if (!$("cardDrawer").open) $("cardDrawer").showModal();
  loadDetail(id);
}
function closeDrawer() {
  const id = [...state.open][0];
  state.open.clear();
  for (const b of document.querySelectorAll("button.details")) { b.setAttribute("aria-expanded", "false"); b.closest(".row").classList.remove("open"); }
  if ($("cardDrawer").open) $("cardDrawer").close();
  if (id) document.querySelector(`.row[data-id="${CSS.escape(id)}"] button.details`)?.focus();
}
function refreshRow(el) {
  const c = state.cards[el.dataset.id];
  if (!c || !state.open.has(c.id)) return;
  $("drawerTitle").textContent = c.title;
  const item = itemFor(c.id);
  setKids($("drawerBody"), cardDetail(c, item?.commands?.length ? h("div", null, h("b", {text: "Suggested command: "}), cmdLine(item.commands[0])) : null), JSON.stringify([c, state.details[c.id], item]));
}
function itemFor(id) { return (((state.board || {}).approvals || {}).items || []).find(x => x.card === id) || null; }
async function loadDetail(id) {
  const ws = state.view;
  try {
    const d = await getJSON(api("card", ws, "/" + encodeURIComponent(id)));
    if (d && ws === state.view) { state.details[id] = d; document.querySelectorAll(`.row[data-id="${CSS.escape(id)}"]`).forEach(refreshRow); }
  } catch (_) {}
}

function blockedReason(c, item) {
  let r = lastNote(c) || (item && item.reason) || "";
  r = r.replace(/^blocked:\s*/, "").replace(/^-> blocked\s*/, "").replace(/^\((.*)\)$/, "$1");
  return r || "No reason given.";
}
function groups(cards) {
  const items = ((state.board.approvals || {}).items || []);
  const byCard = {};
  for (const x of items) if (x.card) byCard[x.card] = byCard[x.card] || x;
  const inView = new Set(cards.map(c => c.id));
  const needs = items.filter(x => ["close", "apply", "duel"].includes(x.kind) && (!x.card || inView.has(x.card)));
  const blocked = cards.filter(c => c.lane === "blocked");
  const ask = blocked.filter(c => asksLead(c, byCard[c.id]));
  const blockedOther = blocked.filter(c => !ask.includes(c));
  const closeIds = new Set(needs.filter(x => x.kind === "close").map(x => x.card));
  const review = cards.filter(c => c.lane === "review" && !closeIds.has(c.id));
  const represented = new Set(needs.map(x => x.card));
  for (const c of [...ask, ...review]) if (!represented.has(c.id)) {
    represented.add(c.id);
    const item = byCard[c.id];
    needs.push({id: c.id, card: c.id, kind: ask.includes(c) ? "decision" : c.lane, reason: c.lane === "review" ? "Review awaiting lead" : blockedReason(c, item), commands: item?.commands?.length ? item.commands : [`raid show ${c.id}`]});
  }
  const laneOf = Object.fromEntries(cards.map(c => [c.id, c.lane]));
  const chat = workspaceChat();
  const answered = r => chat.some(m => m.from?.agent === state.board.lead && m.ts > r.ts && (m.task ? m.task === r.task : m.to === r.from?.agent));
  for (const r of chat) if (r.from?.agent !== state.board.lead && ASK.test(r.text || "") && (r.to === state.board.lead || /\blead\b/i.test(r.text || "")) && (!r.task || inView.has(r.task))
    && !["done", "cancelled"].includes(laneOf[r.task]) && !answered(r)) {
    needs.push({id: "ask-" + r.id, card: r.task, title: `Question from ${r.from?.agent || "agent"}`, kind: "question", reason: r.text, commands: [leadCmd(`msg "<reply>" --to ${quote(r.from?.agent || "agent")}${r.task ? " --task " + quote(r.task) : ""}`)]});
  }
  return {needs, ask, blocked: blockedOther, review, byCard};
}
function byUpdated(a, b) { return (b.updated || "").localeCompare(a.updated || ""); }
function needRow(x) {
  const c = state.cards[x.card] || (state.board.cards || []).find(k => k.id === x.card);
  const label = RAID_PRIMARY[x.kind] || PRIMARY[x.kind] || "Copy command";
  const primary = x.commands && x.commands[0] ? {label, cmd: x.commands[0]} : null;
  const extra = x.commands && x.commands.length > 1 ? moreMenu(x.card || x.id, x.commands.slice(1)) : null;
  if (!c) {
    return h("li", {class: "row one", "data-id": x.id, "data-kind": x.kind},
      h("div", {class: "row-main"}, h("div", {class: "row-title"}, h("strong", {class: "t", text: x.title || x.id})), h("div", {class: "reason", text: x.reason})),
      h("div", {class: "row-actions"}, primary ? h("button", {type: "button", class: "primary", "data-cmd": primary.cmd, "data-label": label, title: primary.cmd, text: label}) : null, extra));
  }
  const why = x.kind === "close" ? (x.reason || "").replace(/^passes the done gate;?\s*/, "").replace(/^approved by/, "Approved by") || "Ready to close" : x.reason;
  return row(c, {kind: x.kind, primary, more: extra, reason: why, meta: [ownerPill(c), approvalPill(c), costPill(c), prPill(c)]});
}
function sectionHead(el, key, text, count) {
  const main = RAID[key] || text;
  const sub = RAID[key] ? h("small", {class: "raid-sub", text}) : null;
  setKids(el, [h("span", null, ico(RAID_ICON[key]), main, " ", count === undefined ? null : h("span", {class: "count", text: String(count)})), sub], JSON.stringify([text, count]));
}
function paged(list, key, make) {
  const limit = state.laneLimit[key] || LANE_PAGE;
  const kids = list.slice(0, limit).map(make);
  if (list.length > limit) kids.push(h("li", {class: "more-row"}, h("button", {type: "button", class: "tog lane-more", "data-lane": key,
    text: `Show ${Math.min(LANE_PAGE, list.length - limit)} more of ${list.length - limit}`})));
  return kids;
}
function needsSummary(needs) {
  const names = {close: ["to close", "to close"], decision: ["decision", "decisions"], blocked: ["blocked", "blocked"], review: ["review", "reviews"],
    question: ["question", "questions"], apply: ["patch to apply", "patches to apply"], duel: ["duel to decide", "duels to decide"]};
  const counts = {};
  for (const x of needs) counts[x.kind] = (counts[x.kind] || 0) + 1;
  return Object.entries(counts).map(([k, n]) => `${n} ${(names[k] || [k, k])[n === 1 ? 0 : 1]}`).join(" · ");
}
function todoOrder(cards) {
  return cards.filter(c => c.lane === "todo").sort((a, b) => (a.created || a.updated || "").localeCompare(b.created || b.updated || "") || a.id.localeCompare(b.id));
}
function renderFocus(cards) {
  const g = groups(cards);
  const lore = ((state.board.approvals || {}).items || []).filter(x => x.kind === "lore");
  $("loreInbox").hidden = !lore.length;
  $("loreTitle").textContent = `Lore inbox (${lore.length})`;
  setKids($("loreList"), paged(lore, "loreInbox", needRow));
  sectionHead($("needsTitle"), "needs", "Needs you", g.needs.length);
  $("needsSub").textContent = g.needs.length ? needsSummary(g.needs) : "";
  const allNeeds = state.needsAll || statusFilter === "needs";
  const needKids = g.needs.length ? (allNeeds ? g.needs : g.needs.slice(0, NEEDS_CAP)).map(needRow) : [h("li", {class: "empty"}, "All is quiet in the realm")];
  if (g.needs.length > NEEDS_CAP && statusFilter !== "needs") needKids.push(h("li", {class: "more-row"}, h("button", {type: "button", class: "tog", id: "needsMore", "aria-expanded": String(!!state.needsAll),
    text: state.needsAll ? `Roll up to ${NEEDS_CAP}` : `Unroll all ${g.needs.length}`})));
  setKids($("needsList"), needKids,
  JSON.stringify([g.needs, allNeeds, [...state.open], Object.keys(state.details).length]));
  if (!$("waitingNote")) $("needs").querySelector("summary").append(h("span", {id: "waitingNote", class: "waiting-note"}));
  const waiting = g.blocked.length;
  setKids($("waitingNote"), waiting ? [`${waiting} cursed ${waiting === 1 ? "quest waits" : "quests wait"} on others. `,
    h("a", {href: subHref("all"), "data-sub": "all", text: "See them in the Quest log"})] : [], JSON.stringify([waiting, state.view]));
  $("waitingNote").hidden = !waiting;
  $("needs").classList.toggle("calm", !g.needs.length);
  $("needsList").hidden = !g.needs.length;
  if (!g.needs.length) $("needsTitle").textContent = "All is quiet in the realm";
  const doing = cards.filter(c => c.lane === "doing").sort(byUpdated);
  sectionHead($("doingTitle"), "doing", "In progress", doing.length);
  setKids($("doingList"), doing.length ? paged(doing, "doingFocus", c => row(c, {meta: [ownerPill(c), leaseText(c), takeoverPill(c), costPill(c), prPill(c)],
    reason: [lastNote(c), (c.locks || []).length ? "Locks: " + c.locks.join(", ") : ""].filter(Boolean).join(" · ")})) : [h("li", {class: "empty"}, "No hero is on a quest. A worker takes one with ", h("code", {text: "raid claim <card>"}), ".")],
  JSON.stringify([doing, state.laneLimit.doingFocus, [...state.open], Object.keys(state.details).length, Math.floor(Date.now() / 30000)]));
  const claim = state.board.claimable || {};
  const todo = todoOrder(cards);
  sectionHead($("todoTitle"), "todo", "To do", todo.length);
  const shown = state.todoAll ? todo : todo.slice(0, TODO_CAP);
  const kids = shown.map(c => row(c, {meta: [claimPill(c), (c.depends || []).length ? h("span", {class: "pill", text: "after " + c.depends.join(", ")}) : null, c.ticket ? h("span", {class: "pill tk", text: c.ticket}) : null]}));
  if (!todo.length) kids.push(h("li", {class: "empty"}, "The quest board is empty. Post a quest with ", h("code", {text: leadCmd('new "Title" --team <team> --locks <repo>/<path>')}), "."));
  if (todo.length > TODO_CAP) kids.push(h("li", {class: "more-row"}, h("button", {type: "button", class: "tog", id: "todoMore", "aria-expanded": String(state.todoAll),
    text: state.todoAll ? `Roll up to ${TODO_CAP}` : `Unroll all ${todo.length} quests`})));
  setKids($("todoList"), kids, JSON.stringify([todo.map(c => [c.id, c.updated, (claim[c.id] || {}).ok]), state.todoAll, [...state.open], Object.keys(state.details).length]));
}
function renderDone(cards) {
  const done = cards.filter(c => c.lane === "done").sort(byUpdated);
  $("doneHint").textContent = "";
  $("doneHint").className = "cls";
  sectionHead($("doneTitle"), "done", "Done", done.length || undefined);
  if (!$("donePanel").open) { $("doneList").dataset.sig = ""; return; }
  const shown = done.slice(0, state.doneLimit);
  const kids = shown.map(c => row(c, {meta: [ownerPill(c), h("span", {class: "pill", text: `closed ${ago(c.updated)} ago`}), costPill(c)]}));
  if (!done.length) kids.push(h("li", {class: "empty", text: "No legends yet. The raid leader turns in quests from the call above."}));
  if (done.length > shown.length) kids.push(h("li", {class: "more-row"}, h("button", {type: "button", class: "tog", id: "doneMore", text: `Show all ${done.length}`})));
  setKids($("doneList"), kids, JSON.stringify([shown.map(c => [c.id, c.updated]), done.length, state.doneLimit, [...state.open], Object.keys(state.details).length]));
}
function renderAllLanes(cards) {
  const box = $("lanes");
  const sig = JSON.stringify([cards.map(c => [c.id, c.lane, c.updated, c.owner]), state.laneLimit, [...state.open], Object.keys(state.details).length]);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  const empty = {todo: "The quest board is empty.", doing: "No hero is on a quest.", review: "Nothing awaits inspection.", blocked: "No quest is cursed.", done: "No legends yet."};
  box.replaceChildren(...LANES.filter(l => l !== "cancelled" || cards.some(c => c.lane === l)).map(l => {
    const cs = l === "todo" ? todoOrder(cards) : cards.filter(c => c.lane === l).sort(byUpdated);
    const limit = state.laneLimit[l] || (l === "done" || l === "cancelled" ? 10 : LANE_PAGE);
    const kids = cs.slice(0, limit).map(c => row(c, {meta: [ownerPill(c), approvalPill(c), claimPill(c), leaseText(c), takeoverPill(c), costPill(c)]}));
    if (!cs.length) kids.push(h("li", {class: "empty", text: empty[l] || "None."}));
    if (cs.length > limit) kids.push(h("li", {class: "more-row"}, h("button", {type: "button", class: "tog lane-more", "data-lane": l, text: `Show ${Math.min(LANE_PAGE, cs.length - limit)} more of ${cs.length - limit}`})));
    return h("section", {class: "lane" + (cs.length ? "" : " empty"), "data-lane": l, "aria-labelledby": "lane-" + l},
      h("h3", {class: "lane-h", id: "lane-" + l}, h("span", null, ico(RAID_ICON[l]), RAID[l] || LANE[l], RAID[l] ? h("small", {class: "raid-sub", text: LANE[l]}) : null), h("span", {class: "count", text: String(cs.length)})),
      h("ul", {class: "rows"}, kids));
  }));
}

function lastSeen(a) {
  return Math.max(Date.parse(a.heartbeat || "") || 0, Date.parse(a.last_seen || "") || 0);
}
function isWorking(name, a) {
  return !!a.card || (state.board.cards || []).some(c =>
    (c.owner === name && !["todo", "done", "cancelled"].includes(c.lane)) ||
    (c.lease && c.lease.agent === name && fxLive(c.lease)));
}
function isActive(name, a) {
  return isWorking(name, a) || Date.now() - lastSeen(a) < 2 * 60 * 60 * 1000;
}
function agentCard(name, a, cards) {
  const st = a.status || "idle";
  const cur = cards.find(c => c.id === a.card);
  const lead = name === state.board.lead;
  const role = lead ? "Lead" : a.role === "reviewer" ? "Reviewer" : "Worker";
  const won = cards.filter(c => c.owner === name && c.lane === "done").length;
  const seen = lastSeen(a);
  const who = identity(a);
  const mail = (state.board.inbox || {})[name] || {};
  return h("div", {class: `agent st-${st}`, "data-agent": name, vars: {"--s": `var(--s-${st})`}, title: !lead ? (heroOf(name) || ["", ""])[1] : null},
    h("div", {class: "av", "aria-hidden": "true", text: avatar(name)}),
    h("div", {class: "body"},
      h("div", {class: "nm"}, h("b", {text: name}), " ", statusBadge(st)),
      h("div", {class: "line cls"}, [role, a.team ? `team ${a.team}` : "", won ? `${won} done` : ""].filter(Boolean).join(" · ")),
      who || billBadge(a.billing) ? h("div", {class: "line"}, who ? h("span", {class: "who", text: who}) : null, billBadge(a.billing), trustBadge(a)) : trustBadge(a) ? h("div", {class: "line"}, trustBadge(a)) : null,
      cur ? h("div", {class: "line"}, "On ", h("b", {text: cur.title})) : null,
      h("div", {class: "line cls"}, seen ? `seen ${ago(new Date(seen).toISOString())} ago` : "", +a.spend_usd ? ` · spent ${usd(a.spend_usd)}` : "",
        mail.unread ? ` · ${mail.unread} unread` : "", mail.queued ? ` · ${mail.queued} queued` : "")));
}
function renderAgents(cards) {
  const b = state.board;
  const all = Object.entries(b.agents || {}).map(([name, a]) => ({name, a}));
  const active = all.filter(x => isActive(x.name, x.a)).sort((x, y) => ORDER.indexOf(x.a.status || "idle") - ORDER.indexOf(y.a.status || "idle") || x.name.localeCompare(y.name));
  const idle = all.filter(x => !isActive(x.name, x.a)).sort((x, y) => lastSeen(y.a) - lastSeen(x.a) || x.name.localeCompare(y.name));
  $("agentsHint").textContent = all.length ? `${active.filter(x => isWorking(x.name, x.a)).length} working, ${active.filter(x => !isWorking(x.name, x.a)).length} recently active` : "nobody joined yet";
  if (!$("agentsPanel").open) { $("agents").dataset.sig = ""; return; }
  const sig = JSON.stringify([b.agents, b.inbox, cards.map(c => [c.id, c.owner, c.lane]), Math.floor(Date.now() / 30000), !!state.idleAll]);
  const kids = [];
  if (!all.some(x => x.name !== b.lead)) kids.push(h("p", {class: "empty"}, "No workers yet. A worker joins from its own terminal with ", h("code", {text: "raid up --join"}), "."));
  if (active.length) kids.push(h("div", {class: "agents"}, active.map(x => agentCard(x.name, x.a, cards))));
  else if (all.length) kids.push(h("p", {class: "empty", text: "No agent was active in the last 2 hours."}));
  if (idle.length) {
    kids.push(h("section", {id: "idleFold"},
      h("h3", {text: `${idle.length} not seen for 2 hours`}),
      h("p", {class: "cls", text: "No card, no lease and not seen in the last 2 hours."}),
      h("div", {class: "chips"}, (state.idleAll ? idle : idle.slice(0, IDLE_CAP)).map(x => h("span", {class: "chip", "data-agent": x.name, title: lastSeen(x.a) ? `seen ${ago(new Date(lastSeen(x.a)).toISOString())} ago` : "never seen"}, x.name)),
        idle.length > IDLE_CAP ? h("button", {type: "button", class: "tog", id: "idleMore", "aria-expanded": String(!!state.idleAll),
          text: state.idleAll ? `Show the first ${IDLE_CAP}` : `Show all ${idle.length}`}) : null)));
  }
  setKids($("agents"), kids, sig);
}

const BELL_ICON = {close: "\u2705", apply: "\u{1FA79}", duel: "\u2694\uFE0F", review: "\u{1F989}", blocked: "\u{1F480}", stalled: "\u231B", gates: "\u2696\uFE0F"};
function bellItems() {
  const ps = (state.projects || []).filter(p => state.view === "*" || p.name === state.view);
  return ps.flatMap(p => (p.needs || []).map(n => ({...n, guild: p.name, key: `${p.name}:${n.kind}:${n.card}`})));
}
function bellSeen() { try { return new Set(JSON.parse(recall("rl.bell.seen") || "[]")); } catch (_) { return new Set(); } }
function renderBell() {
  const items = bellItems(), seen = bellSeen();
  const unseen = items.filter(x => !seen.has(x.key)).length;
  let menu = $("bellMenu");
  if (!menu) {
    menu = h("details", {id: "bellMenu", class: "menu"}, h("summary", {class: "tog bell", "aria-label": "Notifications"}), h("div", {class: "menu-body bell-body"}));
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      store("rl.bell.seen", JSON.stringify([...new Set([...bellSeen(), ...bellItems().map(x => x.key)])]));
      renderBell();
    });
    $("soundMenu").before(menu);
  }
  const all = state.view === "*";
  const bell = menu.querySelector("summary");
  if (state.bellPrev !== undefined && unseen > state.bellPrev && motion()) { bell.classList.remove("ring"); void bell.offsetWidth; bell.classList.add("ring"); }
  state.bellPrev = unseen;
  if (state.bellKnown) for (const x of items) if (!state.bellKnown.has(x.key) && !seen.has(x.key)) notify(`${x.guild}: ${x.title}`, x.reason);
  state.bellKnown = new Set(items.map(x => x.key));
  setKids(bell, ["\u{1F514}", unseen ? h("span", {class: "badge bell-count", text: String(unseen)}) : null], JSON.stringify([unseen]));
  const body = menu.querySelector(".bell-body");
  const head = h("b", {text: all ? "The realm needs you" : `${state.view} needs you`});
  const rows = items.length ? items.map(x => h("a", {class: "bell-row" + (seen.has(x.key) ? "" : " new"), href: href(x.guild), "data-nav": x.guild},
    h("span", {class: "bell-ico", text: BELL_ICON[x.kind] || "\u2022"}),
    h("span", {class: "bell-text"}, h("span", {class: "bell-title", text: x.title}), h("span", {class: "cls", text: all ? `${x.guild} \u00b7 ${x.reason}` : x.reason})))) : [h("span", {class: "cls", text: "All is quiet."})];
  setKids(body, [head, ...rows], JSON.stringify([items.map(x => x.key), [...seen].length, state.view]));
}
function renderUpdate(u) {
  const old = $("updateMenu");
  if (!u) { if (old) old.remove(); return; }
  if (old) return;
  const row = (label, cmd) => {
    const btn = h("button", {type: "button", class: "tog", text: "Copy"});
    btn.addEventListener("click", () => copyCommand(btn, cmd, "Copy"));
    return h("div", {class: "upd-row"}, h("span", {class: "cls", text: label}), h("code", {text: cmd}), btn);
  };
  $("hudLevel").after(h("details", {id: "updateMenu", class: "menu"},
    h("summary", {class: "tog update-chip", title: `raidlock ${u.latest} is available`}, "Update", h("span", {class: "v", text: ` ${u.latest}`})),
    h("div", {class: "menu-body upd-body"},
      h("b", {text: `raidlock ${u.latest} is available (you have ${u.current})`}),
      row("CLI and plugin", u.upgrade),
      h("span", {class: "cls", text: "Then start a new Claude Code session so the new hooks load."}),
      h("a", {href: `https://github.com/agi1clj/raidlock/releases/tag/v${u.latest}`, target: "_blank", rel: "noopener", text: "What's new"}))));
}
function tourKey() { return "rl.tour." + state.view; }
function renderTour(cards, opts) {
  let el = $("tour");
  if (!el) el = h("section", {id: "tour", class: "tour", "aria-labelledby": "tourTitle", hidden: true});
  const spot = opts.realm ? $("realmbar") : $("focus");
  if (opts.realm ? spot.nextElementSibling !== el : spot.previousElementSibling !== el) opts.realm ? spot.after(el) : spot.before(el);
  const lanes = new Set(cards.map(c => c.lane));
  const any = (...ls) => ls.some(l => lanes.has(l));
  if (recall(tourKey()) === null && !opts.hide) store(tourKey(), cards.length ? "off" : "on");
  if (lanes.has("done")) store(tourKey(), "off");
  if (recall(tourKey()) !== "on" || (opts.realm && cards.length) || opts.hide) { el.hidden = true; el.dataset.sig = ""; return; }
  const verified = any("review", "done") || cards.some(c => (c.notes || []).some(n => FX_VERIFY.test(String((n && n.text) || ""))));
  const first = cards.slice().sort((a, b) => (a.created || "").localeCompare(b.created || "") || a.id.localeCompare(b.id))[0];
  const id = first ? first.id : "<card>";
  const repo = (opts.repos || []).length === 1 ? opts.repos[0].name : "<repo>";
  const steps = [
    [cards.length > 0, "Post a quest", "The raid leader posts a quest with the ground it may touch and the proof it needs.", leadCmd(`new "<title>" --locks ${repo}/<path> --verify-cmd "<check>"`)],
    [any("doing", "review", "blocked", "done"), "Accept the quest", "A hero accepts the quest and holds its ground.", `raid claim ${id}`],
    [verified, "Prove the deed", "The raid leader tests the deed and records the proof.", leadCmd(`verify ${id}`)],
    [any("done"), "Turn in the quest", "The raid leader turns in the quest and frees its ground.", leadCmd(`done ${id}`)]];
  const count = steps.filter(s => s[0]).length;
  const next = steps.findIndex(s => !s[0]);
  el.hidden = false;
  setKids(el, [
    h("div", {class: "tour-head"},
      h("h2", {id: "tourTitle", text: "First quest"}),
      h("span", {class: "cls tour-count", text: `${count} of ${steps.length}`}),
      h("button", {type: "button", id: "tourClose", class: "tour-x", "aria-label": "Dismiss the first quest", text: "×", onclick: closeTour})),
    h("ol", {class: "tour-steps"}, steps.map(([ok, title, text, cmd], i) => h("li", {class: "tour-step" + (ok ? " done" : i === next ? " next" : ""), "aria-current": i === next ? "step" : null},
      h("span", {class: "tour-mark", "aria-hidden": "true", text: ok ? "✓" : String(i + 1)}),
      h("div", {class: "tour-body"},
        h("b", null, title, h("span", {class: "sr-only", text: ok ? ": done" : ": pending"})),
        h("span", {class: "cls", text}),
        ok ? null : cmdLine(cmd)))))], JSON.stringify([state.view, steps.map(s => [s[0], s[3]]), next]));
}
function closeTour() {
  store(tourKey(), "off");
  $("tour").hidden = true;
  $("content").focus();
}
function renderLimits(data) {
  const providers = (data && data.providers) || [];
  const real = providers.filter(p => (p.status === "ok" || p.status === "stale") && ((p.windows || []).length || p.context || (p.session && p.session.cost_usd != null)));
  const missing = providers.filter(p => !real.includes(p));
  const NAME = {claude: "Claude Code", codex: "Codex", gemini: "Gemini CLI"};
  const info = missing.map(p => `${NAME[p.provider] || p.provider}: ${p.status}${p.detail ? ". " + p.detail : ""}`);
  setKids($("providerInfo"), info.length ? [h("p", {class: "cls", text: "Not shown in the usage strip:"}), h("ul", null, info.map(t => h("li", {text: t})))] : [], JSON.stringify(info));
  $("limits").hidden = !real.length;
  $("limits").title = info.join("\n");
  const sig = JSON.stringify([real, Math.floor(Date.now() / 30000)]);
  setKids($("limits"), real.map(p => {
    const windows = (p.windows || []).filter(w => Number.isFinite(w.used_percent));
    const ctx = p.context;
    const peak = Math.max(0, ...windows.map(w => w.used_percent), ctx ? ctx.used_percent : 0);
    const level = peak >= 90 ? "critical" : peak >= 70 ? "warning" : "normal";
    const parts = [];
    if (ctx) parts.push(h("span", {class: "lv", title: ctx.assumed_window ? "Window size not reported; assumed " + ktok(ctx.window_tokens) + " tokens" : `Context window used: ${ktok(ctx.used_tokens || 0)} of ${ktok(ctx.window_tokens)} tokens`},
      `Mana ${Math.round(ctx.used_percent)}%`, ctx.assumed_window ? "*" : ""));
    for (const w of windows) {
      const name = w.window === "weekly" ? "Weekly" : w.window || "Window";
      const reset = resetText(w.resets_at);
      parts.push(h("span", {class: "lv"}, `${name} ${Math.round(w.used_percent)}%`, reset ? h("small", {text: ` resets ${reset}`}) : null));
    }
    if (p.session && p.session.cost_usd != null) parts.push(h("span", {class: "lv", title: "Claude Code's own estimate for the current session, at list prices"}, `Session ${usd(p.session.cost_usd)}`));
    const seen = p.observed_at ? ago(p.observed_at) : "";
    const age = secondsAgo(p.observed_at);
    return h("div", {class: `limit ${level}` + (age !== null && age > 3600 ? " stale" : ""), "data-provider": p.provider || "", title: age !== null && age > 3600 ? `Last reported ${seen} ago; these numbers may be out of date` : null},
      h("strong", {text: (NAME[p.provider] || p.provider) + (p.plan ? ` (${p.plan})` : "")}), parts,
      seen ? h("span", {class: "age" + (age !== null && age > FRESH_S ? " old" : "")}, `updated ${seen} ago`) : null);
  }), sig);
}

function cardEvents(cards) {
  const prev = state.cards;
  for (const c of cards) {
    const p = prev[c.id];
    const el = () => document.querySelector(`.row[data-id="${CSS.escape(c.id)}"]`);
    const who = c.owner || "someone";
    if (!p) { toast("New card: ", h("b", {text: c.title})); play("posted"); continue; }
    if (p.lane !== c.lane) {
      if (c.lane === "done") toastAs("quest", fxGlyph("quest-seal"), h("b", {text: "Quest complete: "}), c.title, ` (${who})`);
      else toast(h("b", {text: who}), " moved ", h("b", {text: c.title}), ` to ${LANE[c.lane]}`);
      const fx = {done: [null, "done"], review: [["\u{1F989}", "✨"], "review"], blocked: [["\u{1F480}"], "blocked"], doing: [["\u{1F525}", "✨"], "claim"]}[c.lane];
      if (fx) { if (fx[0]) setTimeout(() => burst(el(), fx[0]), 400); play(fx[1]); } else play("posted");
    } else if ((+(c.cost || {}).tokens || 0) > (+(p.cost || {}).tokens || 0)) {
      toast(`${ktok(c.cost.tokens - ((p.cost || {}).tokens || 0))} more tokens on `, h("b", {text: c.title}));
      play("coin");
    } else if (p.owner !== c.owner && c.owner) {
      toast(h("b", {text: c.owner}), " took ", h("b", {text: c.title})); play("claim");
    } else if ((c.notes_count || 0) > (p.notes_count || 0)) {
      const n = (c.notes || [])[c.notes.length - 1] || {};
      toast(h("b", {text: n.by || who}), ": ", String(n.text || "").slice(0, 140)); play("note");
    }
  }
}
function statusEvents() {
  const lapsed = new Set();
  for (const c of state.board.cards) {
    const l = c.lease;
    if (!l || c.lane !== "doing" || !l.expires) continue;
    const ms = Date.parse(l.expires) - Date.now(), k = c.id + "@" + l.expires;
    if ((ms <= 0 || l.live === false) && !state.expired.has(k)) {
      state.expired.add(k);
      state.warned.add(k);
      if (!state.first) { lapsed.add(c.owner); toast("Lease on ", h("b", {text: c.title}), " expired"); play("expired"); }
    } else if (ms > 0 && ms < 5 * 60000 && !state.warned.has(k)) {
      state.warned.add(k);
      if (!state.first) { toast("Lease on ", h("b", {text: c.title}), ` ends in ${Math.max(1, Math.round(ms / 60000))} min`); play("expiring"); }
    }
  }
  for (const [n, s] of Object.entries(state.board.agents || {})) {
    const was = state.statusPrev[n];
    if (!state.first && was && was !== s.status && s.status === "stalled" && !lapsed.has(n)) { toast(h("b", {text: n}), " is stalled on ", s.card || "a card"); play("stalled"); }
    state.statusPrev[n] = s.status;
  }
}
function msgState(m, busy) {
  if (m.status === "read") return "read";
  if (m.status === "queued" && busy[m.to] && m.task !== busy[m.to]) return "queued";
  return "ready";
}
function messageEvents(data) {
  const busy = {};
  for (const c of data.cards || []) if (c.lane === "doing" && c.owner) busy[c.owner] = c.id;
  const now = {};
  for (const m of data.messages || []) if (m && m.id) now[m.id] = msgState(m, busy);
  const prev = state.msgPrev;
  state.msgPrev = now;
  if (!prev) return;
  for (const [id, st] of Object.entries(now)) {
    const was = prev[id];
    if (was === st) continue;
    if (!was) play(st === "queued" ? "queued" : st === "read" ? "read" : "sent");
    else if (st === "read") play("read");
    else if (was === "queued" && st === "ready") play("delivered");
  }
}

function ratio(a, b) { return `${a || 0}/${(a || 0) + (b || 0)}`; }
function evidenceSummary(x) {
  return `verify ${ratio(x.verify_passed, x.verify_failed)}, review ${ratio(x.review_approve, x.review_changes)}, rework ${x.rework || 0}`;
}
function signed(v) { const n = Math.round((+v || 0) * 100) / 100; return n > 0 ? `+${n}` : String(n); }
function evidenceList(x, c) {
  const items = [
    ["Verify runs passed", ratio(x.verify_passed, x.verify_failed), c.verify],
    ["Reviews approved", ratio(x.review_approve, x.review_changes), c.review],
    ["Rework", String(x.rework || 0), c.rework],
    ["Lock violations and denials", String((x.lock_violations || 0) + (x.lock_denials || 0)), c.lock],
    ["Stalls", String(x.stalls || 0), c.stall],
  ];
  return h("ul", {class: "evid"}, items.map(([label, value, delta]) =>
    h("li", null, h("span", {text: label}), h("b", {text: value}), h("span", {class: "delta", text: `${signed(delta)} pts`}))));
}
function renderScore(rows, reputations) {
  const sig = JSON.stringify([rows, reputations]);
  const children = [];
  const known = rows.filter(g => g.provider !== "unknown" || g.model !== "unknown" || g.usd || g.usd_equiv || g.tokens);
  const head = ["Provider", "Model", "Cards", "Done", "Review pass", "Paid", "At API prices", "Tokens", "Time"];
  const cell = (i, text) => h("td", {class: i > 1 ? "num" : null, "data-label": head[i], text});
  const name = v => v && v !== "unknown" ? v : "not reported";
  if (known.length) children.push(h("div", {class: "tbl", tabindex: "0", role: "region", "aria-label": "Results by provider and model"}, h("table", {class: "stack"},
    h("thead", null, h("tr", null, head.map((x, i) => h("th", {class: i > 1 ? "num" : null, scope: "col", text: x})))),
    h("tbody", null, known.map(g => h("tr", null, cell(0, name(g.provider)), cell(1, name(g.model) === "not reported" ? "not reported" : g.label), cell(2, String(g.quests)), cell(3, String(g.done)),
      cell(4, g.pass_rate == null ? "-" : `${Math.round(g.pass_rate * 100)}% (${g.approve}/${g.approve + g.changes})`),
      cell(5, g.usd ? usd(g.usd) : "-"), cell(6, g.usd_equiv ? usd(g.usd_equiv) : "-"), cell(7, g.tokens ? ktok(g.tokens) : "-"), cell(8, g.duration_ms ? mins(g.duration_ms) : "-")))))));
  else children.push(h("p", {class: "empty", text: "No runs recorded yet. Runners record usage on their own; an agent can add one with raid cost."}));
  const scored = reputations.filter(r => evidenceCount(r.inputs));
  if (scored.length) {
    const trustRows = scored.map(r => {
      const evidence = h("section", {"aria-label": evidenceSummary(r.inputs || {})}, evidenceList(r.inputs || {}, r.contributions || {}));
      return h("tr", null, h("td", {"data-label": "Workspace", text: r.workspace}), h("td", {"data-label": "Model", text: r.label || r.key}),
        h("td", {class: "num score", "data-label": "Score / 100", text: String(r.score)}), h("td", {"data-label": "Evidence"}, evidence));
    });
    const trustHead = ["Workspace", "Model", "Score / 100", "Evidence"].map((text, i) => h("th", {scope: "col", class: i === 2 ? "num" : null, text}));
    children.push(h("section", {class: "model-trust"}, h("h3", {text: "Model trust scores and evidence"}),
      h("p", {class: "note-sm", text: "Scores start at 50. Recorded verification and review results raise or lower them; rework, lock violations and stalls lower them. Scores grant no permissions. Each workspace is scored separately."}),
      h("div", {class: "tbl", tabindex: "0", role: "region", "aria-label": "Model trust table"}, h("table", {class: "stack"}, h("thead", null, h("tr", null, trustHead)), h("tbody", null, trustRows)))));
  }
  setKids($("score"), children, sig);
}
function forecastBlock(f) {
  if (!f) return null;
  if (f.error) return h("p", {class: "forecast", text: "The forecast could not be computed from the recorded spend."});
  const days = f.by_day || [];
  if (!(f.burn || {}).per_day_trailing && !f.forecast && !f.month && !(f.alerts || []).length) return null;
  const spent = d => +d.usd || 0;
  const max = Math.max(...days.map(spent), 0.000001);
  const endAt = new Date((f.period || {}).end || "");
  if (!isNaN(endAt)) endAt.setUTCDate(endAt.getUTCDate() - 1);
  const end = isNaN(endAt) ? "" : endAt.toISOString().slice(0, 10);
  const caps = f.caps ? [f.caps.daily != null ? `daily cap ${usd(f.caps.daily)}` : "", f.caps.monthly != null ? `monthly cap ${usd(f.caps.monthly)}` : ""].filter(Boolean) : [];
  const spark = h("div", {class: "spark", role: "img", "aria-label": `Spend per day, last ${days.length} days, peak ${usd(max)}`},
    days.map(d => { const i = h("i", {title: `${d.day}: ${spent(d) ? usd(spent(d)) : "no spend"}`}); i.classList.add("height-" + Math.round(Math.max(2, Math.min(100, 100 * spent(d) / max)))); return i; }));
  const alerts = f.alerts || [];
  const many = !!f.workspaces;
  return h("div", {class: "forecast"},
    h("h3", {text: "Forecast"}),
    h("div", {class: "tsum"}, h("span", {class: "big", text: usd((f.burn || {}).per_day_trailing)}), h("span", {text: `a day over the last ${(f.burn || {}).trail_days || 7} days`}),
      h("span", {class: "big", text: usd(f.forecast)}), h("span", {text: `by month end (${end})`})),
    h("div", {class: "note-sm", text: `${usd(f.month)} this month, ${usd(f.today)} today${caps.length ? "; " + caps.join(", ") : ""}.`}),
    spark,
    days.length ? h("div", {class: "spark-axis", "aria-hidden": "true"}, h("span", {text: days[0].day}), h("span", {text: `peak ${usd(max)} a day`}), h("span", {text: days[days.length - 1].day})) : null,
    alerts.length ? h("ul", {class: "alerts", "aria-label": "Budget alerts"}, alerts.map(a => h("li", {class: "alert " + a.level},
      h("b", {text: a.level === "critical" ? "Over budget: " : "Budget warning: "}), many ? `${a.workspace}: ` : "", a.text)))
      : caps.length || many ? null : h("div", {class: "note-sm"}, "No budget set. Set one with ", h("code", {text: leadCmd("budget set budget.workspace.monthly_usd 50")}), "."));
}
function renderTreasury(t, f) {
  if (!t) return;
  const by = state.treasuryBy;
  const total = t.total || {};
  const runs = +total.runs || 0;
  $("costHint").textContent = runs && ((f || {}).alerts || []).length ? plural(f.alerts.length, "budget alert") : "";
  const sig = JSON.stringify([t, f, by]);
  if (!runs) {
    setKids($("treasury"), [h("p", {class: "empty", text: "No usage recorded yet. Cost appears here once a runner or an agent records a run."})], sig);
    return;
  }
  const rows = (t[by] || []).slice(0, 20);
  const spent = r => +r.usd_equiv || +r.usd || 0;
  const max = Math.max(...rows.map(spent), 0.000001);
  const opts = [["by_provider", "provider"], ["by_model", "model"], ["by_agent", "agent"], ["by_workspace", "workspace"], ["by_day", "day"]];
  const sel = h("select", {id: "treasuryBy", "aria-label": "Group cost by"}, opts.map(([v, l]) => h("option", {value: v, text: l})));
  sel.value = by;
  sel.addEventListener("change", e => { state.treasuryBy = e.target.value; store("rl.treasuryBy", e.target.value); renderTreasury(t, f); });
  const b = t.billing || {};
  const modes = [["api", "usd", "paid"], ["plan", "usd_equiv", "at API prices, no extra charge"], ["unknown", "usd_equiv", "at API prices"]]
    .filter(([m, k]) => +((b[m] || {})[k]) || +((b[m] || {}).runs));
  setKids($("treasury"), [
    h("div", {class: "tsum"}, total.usd ? [h("span", {class: "big", text: usd(total.usd)}), h("span", {text: "paid"})] : null,
      total.usd_equiv ? [h("span", {class: "big", text: usd(total.usd_equiv)}), h("span", {text: "at API prices"})] : null,
      h("span", {text: `${ktok(total.tokens)} tokens in ${plural(runs, "run")}`})),
    modes.length > 1 ? h("div", {class: "tsum"}, modes.map(([m, k, label]) => [h("span", {class: "bill " + m, title: BILL[m][1], text: BILL[m][0]}), h("span", {text: `${usd((b[m] || {})[k])} ${label}`})])) : null,
    total.unpriced_runs ? h("div", {class: "note-sm", text: `${plural(total.unpriced_runs, "run")} used a model with no price in pricing.json; their tokens count, their cost does not.`}) : null,
    forecastBlock(f),
    h("div", {class: "tside"}, h("div", {class: "panel-tools"}, h("label", {class: "vol"}, "Group by ", sel)),
    h("div", {class: "bars"}, rows.map(r => {
      const w = h("i");
      w.style.width = (100 * spent(r) / max) + "%";
      const key = r.key === "unknown" ? "not reported" : (r.label || r.key);
      return h("div", {class: "brow"}, h("span", {class: "nm", title: key, text: key}), h("span", {class: "tr"}, w),
        h("span", null, `${+r.usd ? usd(r.usd) + " paid" : usd(r.usd_equiv) + " at API prices"} · ${ktok(r.tokens)} `, by === "by_agent" ? billBadge(r.billing) : null));
    })))], sig);
}
function logRows() {
  const f = state.logFilter;
  const records = state.log.filter(r => !CHAT_KINDS.has(r.kind));
  const completed = (state.board?.cards || []).filter(c => c.lane === "done").map(c => ({id: "done-" + c.id, kind: "done", task: c.id, ts: c.updated, text: c.title, from: {agent: c.owner}}));
  const merged = (activityFilter === "done" ? completed : activityFilter === "log" ? records : [...records, ...completed]).sort((a, b) => (a.ts || "").localeCompare(b.ts || "") || a.id.localeCompare(b.id));
  const rows = f === "*" ? merged : merged.filter(r => r.task === f);
  return rows;
}
function recordText(r) {
  const d = r.data || {};
  if (r.kind === "cost" && r.data) return `${ktok(d.tokens)} tokens${modelName(d.model) ? " on " + modelName(d.model) : ""}${+d.usd ? ", " + usd(d.usd) + " paid" : ""}${+d.usd_equiv ? ", " + usd(d.usd_equiv) + " at API prices" : ""}`;
  return r.text || (r.artifacts || []).map(a => a.name).join(", ") || Object.entries(d).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k}: ${v}`).join(", ");
}
function logRow(r, top, idx) {
  const fr = r.from || {};
  const who = fr.agent || "?";
  const meta = [modelName(fr.model), fr.runtime].filter(Boolean).join(" · ");
  const hd = h("div", {class: "hd", title: [who, ago(r.ts) + " ago", KIND[r.kind] || r.kind, r.task, r.to ? "→ " + r.to : "", meta].filter(Boolean).join(" · ")},
    h("b", {text: who}), h("span", {text: ago(r.ts) + " ago"}), h("span", {text: KIND[r.kind] || r.kind}),
    r.task ? h("span", null, r.task) : null, r.to ? h("span", {text: "→ " + r.to}) : null, meta ? h("span", {text: meta}) : null);
  const txt = recordText(r);
  const b = h("button", {type: "button", class: `lrow k-${r.kind}`, title: txt, "data-i": String(idx), "aria-pressed": String(state.logSel === r.id)},
    h("span", {class: "ava", "aria-hidden": "true", text: avatar(who)}), h("span", {class: "bub"}, hd, h("div", {class: "tx", text: txt})));
  b.style.top = top + "px";
  return b;
}
function renderLog(force) {
  const box = $("log");
  if (!$("logPanel").open || $("board").hidden) return;
  const rows = logRows();
  const n = rows.length;
  let sp = box.querySelector(".spacer");
  if (!sp) { sp = h("div", {class: "spacer"}); box.replaceChildren(sp); }
  sp.style.height = Math.max(n * ROW_H, 1) + "px";
  const first = Math.max(0, Math.floor(box.scrollTop / ROW_H) - 4);
  const last = Math.min(n, Math.ceil((box.scrollTop + box.clientHeight) / ROW_H) + 4);
  const sig = [n, first, last, state.logSel, rows.length ? rows[0].id : ""].join("|");
  if (!force && box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  const kids = [];
  for (let i = first; i < last; i++) { const r = rows[n - 1 - i]; kids.push(logRow(r, i * ROW_H, n - 1 - i)); }
  if (!n) kids.push(h("div", {class: "empty", text: "No activity yet. Completed work and agent records appear here."}));
  sp.replaceChildren(...kids);
  $("logHint").textContent = `${n} records${state.logFilter !== "*" ? " on " + state.logFilter : ""}`;
}
function showLogDetail(r) {
  const d = $("logDetail");
  if (!r) { d.hidden = true; return; }
  const fr = r.from || {};
  const who = [modelName(fr.model), fr.runtime, fr.provider].filter(Boolean).join(" · ");
  const kids = [h("div", null, h("b", {text: fr.agent || "?"}), who ? ` · ${who}` : ""),
    h("div", {class: "cls", text: `${KIND[r.kind] || r.kind}${r.to ? " to " + r.to : ""}${r.task ? " on " + r.task : ""} · ${r.ts}`}), h("div", {text: r.text || ""})];
  for (const a of r.artifacts || []) kids.push(h("div", null, h("b", {text: `${a.name}${a.kind ? " (" + a.kind + ")" : ""}: `}), a.text || ""));
  if (r.data) kids.push(h("div", {class: "cls", text: JSON.stringify(r.data)}));
  setKids(d, kids);
  d.hidden = false;
}
function renderLogFilter() {
  const sel = $("logFilter");
  const ids = Object.keys(state.cards).length ? Object.keys(state.cards) : (state.board.cards || []).map(c => c.id);
  const recent = [...new Set(state.log.slice(-400).reverse().map(r => r.task).filter(t => t && ids.includes(t)))].slice(0, 30);
  if (state.logFilter !== "*" && !recent.includes(state.logFilter) && ids.includes(state.logFilter)) recent.unshift(state.logFilter);
  const opts = [["*", "Every card"], ...recent.map(id => [id, id])];
  const sig = opts.map(o => o[0]).join("|");
  if (sel.dataset.sig !== sig) { setKids(sel, opts.map(([v, l]) => h("option", {value: v, text: l}))); sel.dataset.sig = sig; }
  if (!opts.some(o => o[0] === state.logFilter)) state.logFilter = "*";
  sel.value = state.logFilter;
}
function logEvents(fresh) {
  if (state.seenLog === null) { state.seenLog = true; return; }
  for (const r of fresh) {
    const fr = r.from || {};
    if (r.kind === "msg") { toast(ico(avatar(fr.agent)), h("b", {text: fr.agent || "?"}), " → ", h("b", {text: r.to || "?"}), r.task ? ` on ${r.task}` : "", ": ", (r.text || "").slice(0, 140)); raven(fr.agent || "", r.to || ""); }
    else if (r.kind === "handback") { toast(ico("\u{1F4E6}"), h("b", {text: fr.agent || "?"}), " handed back ", h("b", {text: r.task || ""})); }
  }
}
async function pollLog(ws) {
  if (state.logWs !== ws) { state.logWs = ws; state.log = []; state.cursor = 0; state.seenLog = null; state.logSel = null; showLogDetail(null); $("log").scrollTop = 0; }
  const d = await getJSON(api("transcript", ws, `?since=${state.cursor}`), "t:" + ws + ":" + state.cursor);
  if (!d || ws !== state.view) return;
  const fresh = d.records || [];
  state.cursor = d.cursor;
  if (fresh.length) {
    state.log = state.log.concat(fresh);
    if (state.log.length > LOG_CAP) state.log = state.log.slice(-LOG_CAP);
    logEvents(fresh);
    renderLog(true);
    renderLogFilter();
    renderChat();
    if (state.board && state.sub === "focus") { renderFocus(shownCards(state.board.cards || [])); renderLayout(shownCards(state.board.cards || [])); }
  } else if (state.seenLog === null) state.seenLog = true;
  if (d.more) pollLog(ws);
}

function viewDef(id) { return VIEW_DEFS.find(v => v.id === id) || null; }
function subOn(id) { if (id === "focus" || id === "all") return true; const d = viewDef(id); return !!d && (!d.optIn || state.achievements); }
function subRoute() {
  let v = "focus";
  try { v = new URLSearchParams(location.search).get("view") || "focus"; } catch (_) {}
  if (v === "board") v = "all";
  if (v === "chat") { statusFilter = "chatter"; workTab = "chatter"; return "focus"; }
  if (v === "treasury") { statusFilter = "treasury"; return "focus"; }
  return subOn(v) ? v : "focus";
}
function subHref(id) { return href(state.view) + (id === "focus" ? "" : "?view=" + encodeURIComponent(id)); }
const FILTER_TABS = {chat: "chatter", treasury: "treasury"};
function filterTab() { return state.sub === "focus" ? Object.keys(FILTER_TABS).find(k => FILTER_TABS[k] === statusFilter) || "" : ""; }
function agentClass(name) {
  const names = Object.keys((state.board && state.board.agents) || {}).sort();
  const i = names.indexOf(name || "");
  return "ag-" + ((i >= 0 ? i : hash(name || "")) % 8);
}
function viewLabel(d) { return (d.label || {}).sub || d.id; }
const TAB_ORDER = ["focus", "all", "chat", "graph", "map", "frames", "treasury", "replay", "bosses", "achievements"];
function tabRank(id) { const i = TAB_ORDER.indexOf(id); return i < 0 ? TAB_ORDER.length : i; }
function renderViewTabs() {
  const unread = unreadChat().length;
  const chat = unread ? ` \u00b7 ${unread}` : "";
  const active = filterTab() || state.sub;
  if (state.view === "*") {
    const realm = [["focus", "Workspaces", "Guilds"], ["chat", "Chatter" + chat, "Tavern chatter" + chat], ["treasury", "Cost", "Treasury"]];
    setKids($("realmTabs"), realm.map(([id, label, raidName]) => h("a", {href: subHref(id), "data-sub": id, "aria-current": id === active ? "page" : null, text: raidName})),
      JSON.stringify([realm, active]));
    return;
  }
  const items = [["focus", "Focus", "War room"], ["all", "All cards", "Quest log"], ["chat", "Chatter" + chat, "Tavern chatter" + chat], ["treasury", "Cost", "Treasury"], ...VIEW_DEFS.filter(d => subOn(d.id)).map(d => [d.id, viewLabel(d), (d.label || {}).raid])]
    .sort((a, b) => tabRank(a[0]) - tabRank(b[0]));
  setKids($("viewTabs"), items.map(([id, label, raidName]) => h("a", {href: subHref(id), "data-sub": id, "aria-current": id === active ? "page" : null,
    title: raidName && raidName !== label ? label : null, text: raidName || label})), JSON.stringify([items, active, state.view]));
}
function viewCtx(d) {
  return {h, svg, ago, localTime, usd, ktok, modelName, avatar, agentClass, setKids, RM: RM || !state.anim, ws: state.view, board: state.board,
    view: d.id, fresh: false, raidSub: key => key ? h("small", {class: "raid-sub", text: key}) : null,
    api: (endpoint, query) => getJSON(api(endpoint, state.view, query || "")), def: viewDef, go: id => goSub(id), reload: () => loadSub(true)};
}
function enter(el) { if (!el || !motion()) return; el.classList.remove("enter"); void el.offsetWidth; el.classList.add("enter"); }
function applySub() {
  document.body.dataset.sub = state.sub;
  if (!subOn(state.sub)) state.sub = "focus";
  const custom = state.sub !== "focus" && state.sub !== "all";
  $("focus").hidden = state.sub !== "focus";
  $("lanes").hidden = state.sub !== "all";
  $("viewPane").hidden = !custom;
  for (const d of VIEW_DEFS) if (d.id !== state.sub && d.leave) { try { d.leave(); } catch (_) {} }
  if (!custom) { $("viewPane").replaceChildren(); $("viewPane").dataset.sig = ""; $("viewPane").dataset.view = ""; }
  else if (!state.subData[state.sub]) setKids($("viewPane"), h("p", {class: "empty", role: "status", text: "Loading this view…"}));
  const d = viewDef(state.sub);
  $("viewPane").setAttribute("aria-label", d ? viewLabel(d) : "");
  $("sidebar").hidden = custom;
  if (state.sub === "all") $("board").append($("sidebar")); else $("focus").append($("sidebar"));
  for (const id of ["donePanel", "agentsPanel", "logPanel"]) $(id).hidden = custom || state.view === "*";
  $("donePanel").hidden = state.sub !== "focus" || state.view === "*";
  $("costPanel").hidden = false;
  renderViewTabs();
}
function drawSub() {
  const d = viewDef(state.sub), data = state.subData[state.sub];
  if (!d || !data || state.view === "*") return;
  const pane = $("viewPane"), c = viewCtx(d);
  c.fresh = pane.dataset.view !== d.id;
  if (c.fresh) { pane.dataset.sig = ""; pane.replaceChildren(); }
  pane.dataset.view = d.id;
  try { d.render(c, data, pane); fxView(d.id, data); } catch (e) { setKids($("viewPane"), h("p", {class: "empty", text: "This view could not be drawn. Reload the page; if it keeps failing, run the matching command, for example raid map."})); }
}
async function loadSub(force) {
  const d = viewDef(state.sub);
  if (!d || state.view === "*") return;
  if (!force && d.poll === false && state.subData[d.id]) return;
  const ws = state.view;
  const query = d.query ? d.query() : "";
  try {
    const r = await getJSON(api(d.endpoint, ws, query), `v:${d.id}:${ws}:${query}`);
    if (ws !== state.view || d.id !== state.sub) return;
    if (r) state.subData[d.id] = r;
    if (r || force) drawSub();
  } catch (e) {
    if (ws === state.view && d.id === state.sub) setKids($("viewPane"), h("p", {class: "empty", role: "alert", text: e.status === 413 ? "This board is too large to draw here. Use the command line instead, for example raid graph --json." : "This view could not be loaded. It retries every few seconds; check that raid ui is still running."}));
  }
}
function goSub(id) {
  if (!subOn(id)) id = "focus";
  history.pushState(null, "", subHref(id));
  state.sub = id;
  applySub();
  if (id === "focus" || id === "all") rerender();
  else loadSub(true);
  enter(id === "focus" ? $("focus") : id === "all" ? $("lanes") : $("viewPane"));
}
function renderTabs() {
  const items = state.mode === "all" ? [{id: "*", label: "The realm"}] : [];
  for (const p of state.projects) items.push({id: p.name, label: p.name});
  $("tabs").hidden = items.length < 2;
  setKids($("tabs"), items.map(i => h("a", {href: href(i.id), "data-nav": i.id, "aria-current": i.id === state.view ? "page" : null}, i.label)),
    JSON.stringify([items, state.view]));
}
function renderCrumbs() {
  const c = $("crumbs");
  if (state.error) { setKids(c, h("span", {class: "err", role: "alert", text: state.error})); return; }
  if (!state.loaded) { c.textContent = "Loading the board…"; return; }
  if (state.view === "*") { setKids(c, [h("span", {"aria-current": "page", text: "The realm"}), h("span", {class: "cls", text: ` · ${plural(state.projects.length, "guild")}`})]); return; }
  setKids(c, [state.mode === "all" ? h("a", {href: "/", "data-nav": "*", text: "The realm"}) : null, state.mode === "all" ? " / " : null,
    h("span", {"aria-current": "page", text: state.view})]);
}
function renderWsBar(data, cards) {
  setKids($("wsName"), [state.view], JSON.stringify([state.view]));
  setKids($("hudLevel"), [streakChip(data.streak || 0), levelBadge("Guild", data.counts)], JSON.stringify(["ws", state.view, data.counts, data.streak]));
  victory("ws:" + state.view, data.bosses_slain || []);
  levelUp("ws:" + state.view, "Guild", data.counts, $("hudLevel"));
  const repos = data.repos || [];
  if (state.repo && !repos.some(r => r.name === state.repo)) state.repo = "";
  const many = repos.length > 1;
  const chips = many ? [h("button", {type: "button", class: "repo all", "data-repo": "", "aria-pressed": String(!state.repo), text: "All zones"}), ...repos.map(r => repoChip(r, true))] : [];
  setKids($("repos"), chips, JSON.stringify([repos, state.repo]));
  const ids = new Set(cards.map(c => c.id));
  const titles = Object.fromEntries(cards.map(c => [c.id, c.title]));
  const agents = Object.entries(data.agents || {}).map(([name, a]) => ({...a, name})).filter(a => !state.repo || ids.has(a.card));
  const count = l => cards.filter(c => c.lane === l).length;
  const g = groups(cards);
  const lore = ((state.board.approvals || {}).items || []).filter(x => x.kind === "lore");
  $("loreInbox").hidden = !lore.length;
  $("loreTitle").textContent = `Lore inbox (${lore.length})`;
  setKids($("loreList"), paged(lore, "loreInbox", needRow));
  const info = {needs: g.needs.length, doing: count("doing"), blocked: count("blocked"), todo: count("todo"), done: count("done"), total: cards.length,
    stalled: oldestStalled(agents, titles), today: (data.today || {}).usd_equiv || 0};
  setKids($("wsStatus"), statusLine({...info, needs: 0, doing: 0, todo: 0, total: 0, quiet: true}), JSON.stringify([info, Math.floor(Date.now() / 30000)]));
}
function renderBoard(data) {
  state.board = data;
  state.loaded = true;
  blockStatus("");
  const cards = data.cards || [];
  applyStatuses(Object.values(data.agents || {}), Object.fromEntries(cards.map(c => [c.id, c])));
  $("board").hidden = false;
  const shown = shownCards(cards);
  if (!state.first) cardEvents(cards);
  state.cards = Object.fromEntries(cards.map(c => [c.id, c]));
  renderWsBar(data, shown);
  if (state.sub === "focus") renderFocus(shown);
  if (state.sub === "all") renderAllLanes(shown);
  renderDone(shown);
  statusEvents();
  messageEvents(data);
  renderAgents(cards);
  renderScore(data.scorecard || [], data.model_reputation || []);
  renderTreasury(data.treasury, data.forecast);
  renderLogFilter();
  renderChat();
  renderLayout(shown);
  renderTour(cards, {repos: data.repos});
  for (const id of state.open) if (state.cards[id]) loadDetail(id);
  fxBoard(data);
  state.first = false;
  if (location.hash.startsWith("#card-") && $("board").dataset.linked !== state.view + location.hash) {
    $("board").dataset.linked = state.view + location.hash;
    try { revealCard(decodeURIComponent(location.hash.slice(6))); } catch (_) {}
  }
}
const LIVE = new Set(["working", "quiet", "stalled"]);
function derive(a, card) {
  if (!LIVE.has(a.status)) return a.status;
  const exp = card && card.lease && card.lease.expires;
  if (exp && Date.parse(exp) <= Date.now()) return "stalled";
  const age = (Date.now() - Date.parse(a.heartbeat || "")) / 1000;
  if (!isFinite(age) || age > 2700) return "stalled";
  return age > 900 ? "quiet" : "working";
}
function applyStatuses(agents, cardsById) {
  let changed = false;
  for (const a of agents) {
    const st = derive(a, cardsById && cardsById[a.card]);
    if (st !== a.status) { a.status = st; a.label = (STATUS[st] || STATUS.idle).label; changed = true; }
  }
  return changed;
}
function statusClock() {
  if (state.view === "*") {
    if (state.overview && applyStatuses(state.projects.flatMap(p => p.agents || []), null)) renderOverview();
  } else if (state.board) {
    const byId = Object.fromEntries((state.board.cards || []).map(c => [c.id, c]));
    applyStatuses(Object.values(state.board.agents || {}), byId);
    rerender();
  }
}
function matches(c, terms) {
  const hay = [c.title, c.id, c.owner, c.ticket, ...(c.repos || [])].join(" ").toLowerCase();
  return terms.every(t => hay.includes(t));
}
function queryTerms() { return (state.q || "").toLowerCase().split(/\s+/).filter(Boolean); }
function shownCards(cards) {
  const inRepo = state.repo ? cards.filter(c => (c.repos || []).includes(state.repo)) : cards;
  const terms = queryTerms();
  const found = terms.length ? inRepo.filter(c => matches(c, terms)) : inRepo;
  if ($("qCount")) $("qCount").textContent = terms.length ? `${found.length} of ${inRepo.length} quests` : "";
  return found;
}

function projInfo(p) {
  const c = p.counts || {};
  return {missing: !!p.missing, doing: c.doing || 0, review: c.review || 0, blocked: c.blocked || 0, todo: c.todo || 0, done: c.done || 0,
    needs: p.review || 0, total: LANES.reduce((n, l) => n + (c[l] || 0), 0), stalled: oldestStalled(p.agents || []), today: (p.today || {}).usd_equiv || 0};
}
function projCard(p, info) {
  if (p.missing) return h("div", {class: "proj att-0"}, h("h3", {text: p.name}), h("p", {class: "cls", text: p.error ? `This board cannot be read (${p.error}). Run raid doctor in its folder.` : "The board folder is missing. Remove it with raid workspaces, or restore the folder."}));
  const head = h("div", {class: "top"}, h("h3", null, p.name, info.total ? " " : null, info.total ? h("span", {class: "lvl-chip", title: `Guild level ${levelOf(p.counts).level}, ${levelOf(p.counts).rank}`, text: `Lv ${levelOf(p.counts).level}`}) : null), h("span", {class: "act", text: p.last ? `active ${ago(p.last)} ago` : "no activity yet"}));
  const repos = (p.repos || []).length ? h("div", {class: "repos"}, p.repos.map(r => repoChip(r, false))) : null;
  if (!info.total) return h("a", {class: "proj att-3", href: href(p.name), "data-nav": p.name}, head, repos, h("div", {class: "start"}, "No cards yet. Add one from the lead terminal:", h("code", {text: 'raid --lead-secret "$LEAD_SECRET_FILE" new "First card" --team core --locks repo/path'})));
  const lbl = LANES.filter(l => p.counts[l]).map(l => `${p.counts[l]} ${LANE[l].toLowerCase()}`).join(", ");
  const bar = h("div", {class: "bar", role: "img", "aria-label": `Cards: ${lbl}`}, LANES.filter(l => p.counts[l]).map(l => { const i = h("i", {title: `${LANE[l]}: ${p.counts[l]}`, vars: {"--lc": `var(--${l})`}}); i.style.width = (100 * p.counts[l] / info.total) + "%"; return i; }));
  return h("a", {class: `proj att-${attention(info)}`, href: href(p.name), "data-nav": p.name}, head, repos, h("p", {class: "status-line"}, statusLine(info)), bar);
}
function renderOverview() {
  blockStatus("");
  const o = $("projects");
  const ps = state.projects;
  applyStatuses(ps.flatMap(p => p.agents || []), null);
  setKids($("overviewTitle"), ["The realm"], "realm");
  const live = ps.filter(p => attention(projInfo(p)) < 3).length;
  $("realmStatus").textContent = `${plural(ps.length, "guild")} · ${live} active`;
  setKids($("hudLevel"), [levelBadge("Realm", sumCounts(ps))], JSON.stringify(["realm", ps.map(p => p.counts)]));
  levelUp("realm", "Realm", sumCounts(ps), $("hudLevel"));
  renderTour([], {realm: true, hide: !ps.length || Object.values(sumCounts(ps)).some(n => n > 0)});
  if (!ps.length) { setKids(o, h("div", {class: "empty"}, "No boards yet. In a project folder, run ", h("code", {text: "raid quickstart"}), ", then reload this page."), "none"); $("idle").hidden = true; return; }
  const rows = ps.map(p => ({p, info: projInfo(p)})).map(r => ({...r, rank: attention(r.info)}));
  rows.sort((a, b) => a.rank - b.rank || (b.p.last || "").localeCompare(a.p.last || "") || a.p.name.localeCompare(b.p.name));
  const busy = rows.filter(r => r.rank < 3), idle = rows.filter(r => r.rank === 3);
  const sig = JSON.stringify([ps.map(p => [p.name, p.counts, p.repos, p.today, p.agents, p.last, p.missing, p.review]), Math.floor(Date.now() / 30000)]);
  setKids(o, busy.length ? busy.map(r => projCard(r.p, r.info)) : [h("p", {class: "empty", text: "Every guild rests: no quests underway, inspected or cursed."})], sig);
  $("idle").hidden = !idle.length;
  setKids($("idle"), [h("span", {class: "lbl", text: `Quiet (${idle.length}): `}), ...idle.flatMap((r, i) => [i ? ", " : null,
    h("a", {href: href(r.p.name), "data-nav": r.p.name, title: r.p.last ? `active ${ago(r.p.last)} ago` : "no activity yet", text: r.p.name})])], sig);
  renderScore(state.overview.scorecard || [], state.overview.model_reputation || []);
  renderTreasury(state.overview.treasury, state.overview.forecast);
}
function chatEvents() {
  const first = state.seenChat === null;
  const seen = state.seenChat || new Set();
  for (const p of state.projects) for (const r of p.chat || []) {
    const k = p.name + ":" + r.id;
    if (seen.has(k)) continue;
    seen.add(k);
    if (first || state.view !== "*" || r.kind !== "msg") continue;
    toast(h("b", {text: r.from.agent}), " → ", h("b", {text: r.to || "?"}), ` (${p.name}): `, (r.text || "").slice(0, 140));
    play("sent");
  }
  state.seenChat = seen;
}
function workspaceChat() {
  const projects = state.projects.filter(p => state.view === "*" ? state.chatFilter === "*" || !state.chatFilter || p.name === state.chatFilter : p.name === state.view);
  const items = projects.flatMap(p => (p.chat || []).filter(r => CHAT_KINDS.has(r.kind)).map(r => ({...r, workspace: p.name})));
  if (state.view !== "*") items.push(...state.log.filter(r => CHAT_KINDS.has(r.kind)).map(r => ({...r, workspace: state.view})));
  return [...new Map(items.map(r => [r.workspace + ":" + r.id, r])).values()].sort((a, b) => (a.ts || "").localeCompare(b.ts || "") || a.id.localeCompare(b.id));
}
function unreadChat() {
  const raw = chatRead.get(state.view) || recall("rl.chat.read." + state.view) || "";
  let seen = {ts: typeof raw === "string" ? raw : "", ids: null};
  try { seen = typeof raw === "object" ? raw : raw.startsWith("{") ? JSON.parse(raw) : seen; } catch (_) {}
  return workspaceChat().filter(r => (r.ts || "") > (seen.ts || "") ||
    ((r.ts || "") === seen.ts && Array.isArray(seen.ids) && !seen.ids.includes(r.workspace + ":" + r.id)));
}
function markChatRead() {
  if (!$("chatter").open || !$("chat").getClientRects().length || document.visibilityState === "hidden") return;
  const bounds = $("chat").getBoundingClientRect();
  if (bounds.bottom <= 0 || bounds.top >= innerHeight) return;
  const items = workspaceChat();
  const ts = items.at(-1)?.ts || "";
  const seen = {ts, ids: items.filter(r => r.ts === ts).map(r => r.workspace + ":" + r.id)};
  chatRead.set(state.view, seen);
  store("rl.chat.read." + state.view, JSON.stringify(seen));
  $("chatUnread").textContent = "0 unread";
  $("chatUnread").hidden = true;
  $("chat").querySelectorAll(".new-messages").forEach(e => e.remove());
  renderViewTabs();
  const tab = $("workTabs").querySelector('[data-tab="chatter"]');
  if (tab) tab.textContent = "Chatter (0)";
}
function renderChat() {
  const sel = $("chatFilter");
  const opts = [["*", "All workspaces"], ...state.projects.map(p => [p.name, p.name])];
  setKids(sel, opts.map(([v, l]) => h("option", {value: v, text: l})), JSON.stringify(opts));
  if (state.chatFilter === null) state.chatFilter = "*";
  sel.value = state.chatFilter;
  sel.closest(".panel-tools").hidden = state.view !== "*";
  const box = $("chat");
  const scroll = box.scrollTop;
  const terms = queryTerms();
  const top = workspaceChat().filter(r => !terms.length || terms.every(t => [r.text, r.task, r.from?.agent].join(" ").toLowerCase().includes(t))).slice(-CHAT_CAP).reverse();
  const unread = unreadChat();
  const unreadIds = new Set(unread.map(r => r.workspace + ":" + r.id));
  const firstRead = unread.length ? top.find(r => !unreadIds.has(r.workspace + ":" + r.id)) : null;
  const firstUnread = firstRead && top[0] !== firstRead ? firstRead.workspace + ":" + firstRead.id : null;
  $("chatUnread").textContent = `${unread.length} unread`;
  $("chatUnread").hidden = !unread.length;
  const content = top.length ? top.flatMap(r => [r.workspace + ":" + r.id === firstUnread ? h("div", {class: "new-messages", text: "Earlier messages"}) : null,
    h("div", {class: `msg k-${r.kind}`, "data-message": r.workspace + ":" + r.id}, h("div", {class: "ava", "aria-hidden": "true", text: avatar(r.from?.agent)}),
      h("div", {class: "bub"}, h("div", {class: "hd"}, h("b", {text: r.from?.agent || "agent"}),
        r.task ? h("a", {href: href(r.workspace) + "#card-" + encodeURIComponent(r.task), "data-card": r.task, text: r.task}) : null,
        h("span", {text: ago(r.ts) + " ago"})), h("div", {class: "tx", text: r.text || ""})))])
    : [h("div", {class: "empty", text: "Quiet for now. Agent messages and notes appear here."})];
  const sig = JSON.stringify([state.view, state.chatFilter, top]);
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    const previous = new Map([...box.querySelectorAll("[data-message]")].map(el => [el.dataset.message, el]));
    const nodes = content.filter(Boolean).map(el => previous.get(el.dataset.message) || el);
    for (const child of [...box.children]) if (!nodes.includes(child)) child.remove();
    nodes.forEach((el, i) => { if (box.children[i] !== el) box.insertBefore(el, box.children[i] || null); });
  }
  box.scrollTop = scroll;
  markChatRead();
}
function blockStatus(message) {
  for (const el of document.querySelectorAll(".block")) {
    let line = el.querySelector(":scope > .block-status");
    if (!line) { line = h("p", {class: "block-status sub", role: "status"}); el.firstElementChild.after(line); }
    line.hidden = !message;
    line.textContent = message;
  }
}
function foldDefault(id, actionable) {
  const el = $(id), preference = recall("rl.open." + id);
  const open = preference === null ? actionable : preference === "true";
  if (el.dataset.foldApplied === String(open)) return;
  el.dataset.foldApplied = String(open);
  if (el.open !== open) { el.dataset.autoFold = String(open); el.open = open; }
}
function renderLayout(cards) {
  const active = document.activeElement;
  const focusTab = active?.dataset.tab;
  const counts = {needs: groups(cards).needs.length, todo: cards.filter(c => c.lane === "todo").length,
    doing: cards.filter(c => c.lane === "doing").length, done: cards.filter(c => c.lane === "done").length, chatter: unreadChat().length};
  const medium = matchMedia("(min-width: 768px) and (max-width: 1279px)").matches;
  if (medium && workTab !== "chatter") workTab = "todo";
  const labels = {needs: "Raid leader\u2019s call", todo: "Quest board", doing: "On a quest", done: "Hall of legends", chatter: "Tavern chatter"};
  setKids($("workTabs"), (medium ? ["todo"] : ["todo", "doing", "done"]).map(id => h("button", {type: "button", role: "tab", id: "tab-" + id, class: "tog", "data-tab": id,
    "aria-selected": String(workTab === id), tabindex: workTab === id ? "0" : "-1", "aria-controls": medium && id === "todo" ? "columns" : id === "done" ? "donePanel" : id, text: medium && id === "todo" ? "Quest board" : `${labels[id]} (${counts[id]})`,
    onclick: () => { workTab = id; statusFilter = ""; renderLayout(cards); if (id === "chatter") { $("chatter").open = true; renderChat(); } }})));
  if (focusTab) $("workTabs").querySelector(`[data-tab="${focusTab}"]`)?.focus();
  $("focus").dataset.tab = workTab;
  $("focus").dataset.filter = statusFilter;
  document.body.dataset.tab = statusFilter;
  if (statusFilter === "treasury") $("overviewCosts").open = true;
  for (const id of ["todo", "doing", "donePanel", "chatter", "columns"]) {
    const el = $(id), tab = id === "donePanel" ? "done" : id === "columns" ? "todo" : id;
    const panel = matchMedia("(max-width: 1279px)").matches && (medium ? id === "columns" || id === "chatter" : id !== "columns");
    if (panel) { el.setAttribute("role", "tabpanel"); el.setAttribute("aria-labelledby", "tab-" + tab); }
    else { el.removeAttribute("role"); el.removeAttribute("aria-labelledby"); }
  }
  foldDefault("needs", true);
  for (const id of ["todo", "doing"]) foldDefault(id, true);
  foldDefault("donePanel", false);
  foldDefault("agentsPanel", false);
  foldDefault("logPanel", false);
  foldDefault("chatter", true);
  setKids($("activityChips"), ["all", "done", "log"].map(id => h("button", {type: "button", class: "tog", "aria-pressed": String(activityFilter === id),
    text: {all: "All", done: "Quests won", log: "Deeds"}[id], onclick: () => { activityFilter = id; renderLog(true); renderLayout(cards); }})));
}

function panelTitles() {
  const t = (id, text, key) => setKids($(id), RAID[key] ? [RAID[key], h("small", {class: "raid-sub", text})] : [text]);
  t("agentsTitle", "Team", "agents");
  t("costTitle", "Cost", "cost");
  if ($("ovCostTitle")) t("ovCostTitle", "Cost", "cost");
  t("logTitle", "Activity", "log");
  t("chatTitle", "Chatter", "chat");
}
function applyView() {
  const all = state.view === "*";
  document.body.dataset.page = all ? "realm" : "guild";
  $("overview").hidden = !all;
  $("content").prepend($("tabs"));
  $("board").hidden = all;
  $("chatter").hidden = false;
  if (all) $("panels").append($("chatter"), $("agentsPanel"), $("logPanel"));
  else { $("sidebar").append($("chatter"), $("agentsPanel")); $("panels").append($("overviewCosts"), $("logPanel")); }
  $("overviewCosts").hidden = false;
  if (!$("scoreHead")) $("score").before(h("h3", {id: "scoreHead", class: "merged-head", text: "Results by model"}));
  $("overviewCosts").append($("costPanel"), $("scoreHead"), $("score"));
  const costSub = $("overviewCosts").querySelector(".sub");
  if (costSub) costSub.textContent = all ? "Spend, forecast and results by model, across all workspaces." : "Spend, forecast and results by model.";
  $("export").hidden = all;
  if (!all) { $("export").href = api("snapshot", state.view); $("export").setAttribute("download", `raidlock-${state.view}.html`); }
  document.title = all ? "raidlock" : `${state.view} · raidlock`;
  document.body.dataset.tab = statusFilter;
  if (statusFilter === "chatter") $("chatter").open = true;
  if (statusFilter === "treasury") $("overviewCosts").open = true;
  applySub();
  $("notice").hidden = !state.unknown;
  $("notice").textContent = state.unknown ? `There is no workspace called "${state.unknown}". Showing all workspaces instead.` : "";
  renderTabs();
  renderCrumbs();
}
function route() {
  const seg = location.pathname.split("/").filter(Boolean)[0];
  try { return seg ? decodeURIComponent(seg) : "*"; } catch (_) { return seg; }
}
function select(id) {
  if (id === state.view) return;
  $("cardDrawer").close();
  state.log = [];
  state.view = id; state.board = null; state.cards = {}; state.first = true; state.statusPrev = {}; state.open.clear(); state.msgPrev = null; state.repo = ""; state.q = ""; if ($("q")) $("q").value = ""; statusFilter = ""; workTab = "todo";
  state.details = {}; state.laneLimit = {}; state.todoAll = false; state.needsAll = false; state.doneLimit = 5; state.unknown = ""; state.logFilter = "*"; state.subData = {}; state.sub = subRoute();
  state.fxPrev = null; state.fxPulse = new Map(); state.fxView = {};
  state.bellPrev = undefined; state.bellKnown = undefined;
  if ($("bellMenu")) renderBell();
  for (const k of Object.keys(state.etag)) if (!k.startsWith("o")) delete state.etag[k];
  for (const el of document.querySelectorAll("[data-sig]")) el.dataset.sig = "";
  applyView();
  blockStatus("Loading… Board data will appear shortly.");
  tick();
}
function onNav(e) {
  const a = e.target.closest && e.target.closest("a[data-nav]");
  if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  const id = a.dataset.nav;
  if (id === state.view) return;
  history.pushState(null, "", a.getAttribute("href"));
  select(id);
  scrollTo(0, 0);
}

async function pollOverview(force) {
  const d = await getJSON("/api/overview", "o");
  if (!d && !force) return false;
  if (d) { state.overview = d; state.mode = d.mode; state.projects = d.projects || []; }
  return true;
}
async function tick() {
  if (state.busy) return;
  state.busy = true;
  try {
    const want = state.view;
    const needOverview = want === "*" || state.ticks % OVERVIEW_EVERY === 0 || !state.overview;
    state.ticks++;
    let changed = false;
    if (needOverview) changed = await pollOverview(false);
    if (!state.overview) return;
    const wasError = !!state.error;
    state.error = "";
    if (state.view === "*") state.loaded = true;
    const names = state.projects.map(p => p.name);
    if (state.mode !== "all" && !names.includes(state.view) && names.length) { state.view = names[0]; applyView(); }
    else if (state.view !== "*" && !names.includes(state.view)) { state.unknown = state.view; state.view = "*"; applyView(); }
    if (changed || wasError) { renderTabs(); renderCrumbs(); chatEvents(); renderLimits(state.overview.limits); renderUpdate(state.overview.update); renderBell(); }
    if (state.view === "*") { if (changed || state.first || wasError) { renderOverview(); renderChat(); state.first = false; } return; }
    const ws = state.view;
    const b = await getJSON(api("board", ws), "b:" + ws);
    if (b && ws === state.view) { renderBoard(b); if (wasError || !$("crumbs").querySelector("[aria-current]")) renderCrumbs(); }
    await pollLog(ws);
    if (state.sub !== "focus" && state.sub !== "all") await loadSub(false);
  } catch (e) {
    state.error = e && e.status ? `This board could not be loaded (HTTP ${e.status}). It retries every few seconds; if it keeps failing, run raid doctor.`
      : "The board server is not answering. Start it again with: raid ui";
    renderCrumbs();
    blockStatus("Updates unavailable. Showing the last data; retrying automatically. Run raid ui if this persists.");
  } finally { state.busy = false; }
}
let starFrame = 0;
function stars() {
  const c = $("stars");
  cancelAnimationFrame(starFrame);
  const x = c.getContext("2d");
  let W2, H2, S = [], M = [], shoot = null;
  const rs = () => {
    W2 = c.width = innerWidth; H2 = c.height = innerHeight;
    S = Array.from({length: Math.min(380, W2 * H2 / 4500 | 0)}, () => ({x: Math.random() * W2, y: Math.random() * H2, r: Math.random() * 1.4 + .2, p: Math.random() * 6.28, s: .6 + Math.random() * 1.6}));
    M = Array.from({length: Math.min(36, W2 / 40 | 0)}, () => ({x: Math.random() * W2, y: Math.random() * H2, r: 1 + Math.random() * 1.8, v: .5 + Math.random(), p: Math.random() * 6.28}));
  };
  rs();
  window.onresize = () => { rs(); if (!motion()) still(); };
  const still = () => { x.clearRect(0, 0, W2, H2); for (const s of S) { x.globalAlpha = .6; x.fillStyle = "#fff6dc"; x.beginPath(); x.arc(s.x, s.y, s.r, 0, 6.28); x.fill(); } x.globalAlpha = 1; };
  if (!motion()) { still(); return; }
  let lastDraw = 0;
  const f = t => {
    if (!motion()) return;
    starFrame = requestAnimationFrame(f);
    if (t - lastDraw < 40) return;
    lastDraw = t;
    x.clearRect(0, 0, W2, H2);
    for (const s of S) { x.globalAlpha = .35 + .65 * Math.abs(Math.sin(s.p + t / 1000 * s.s)); x.fillStyle = "#fff6dc"; x.beginPath(); x.arc(s.x, s.y, s.r, 0, 6.28); x.fill(); }
    const wind = .6 + .9 * Math.max(0, Math.sin(t / 5200)) + .3 * Math.sin(t / 1700);
    for (const m of M) {
      m.x += wind * m.v * 1.6; m.y += Math.sin(m.p + t / 900) * .45 - .15;
      if (m.x > W2 + 10) { m.x = -10; m.y = Math.random() * H2; }
      if (m.y < -10) m.y = H2 + 10;
      x.globalAlpha = .35 + .4 * Math.abs(Math.sin(m.p + t / 700)); x.fillStyle = "#ffcf7a";
      x.shadowColor = "#ffb347"; x.shadowBlur = 6; x.beginPath(); x.arc(m.x, m.y, m.r, 0, 6.28); x.fill(); x.shadowBlur = 0;
    }
    if (!shoot && Math.random() < .008) shoot = {x: Math.random() * W2 * .7, y: Math.random() * H2 * .3, l: 0};
    if (shoot) { x.globalAlpha = 1 - shoot.l / 40; x.strokeStyle = "#ffe9a8"; x.lineWidth = 2; x.beginPath(); x.moveTo(shoot.x, shoot.y); x.lineTo(shoot.x - 40, shoot.y - 14); x.stroke(); shoot.x += 14; shoot.y += 4.7; if (++shoot.l > 40) shoot = null; }
    x.globalAlpha = 1;
  };
  starFrame = requestAnimationFrame(f);
}

function revealCard(id) {
  if (state.sub !== "focus") goSub("focus");
  const c = state.cards[id];
  if (!c) return;
  workTab = c.lane === "done" ? "done" : ["todo", "doing"].includes(c.lane) ? c.lane : "todo";
  statusFilter = "";
  if (["todo", "doing", "done"].includes(c.lane)) {
    const panel = c.lane === "done" ? "donePanel" : c.lane;
    store("rl.open." + panel, true);
    $(panel).open = true;
  }
  state.todoAll = true;
  state.doneLimit = Number.MAX_SAFE_INTEGER;
  state.laneLimit.doingFocus = Number.MAX_SAFE_INTEGER;
  rerender();
  const el = document.querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  if (el) { el.closest("details")?.setAttribute("open", ""); el.scrollIntoView({block: "center"}); openRow(el, true); }
}
function onAction(e) {
  const t = e.target.closest ? e.target : null;
  if (!t) return;
  const link = t.closest("a[data-card]");
  if (link && state.cards[link.dataset.card]) {
    e.preventDefault();
    revealCard(link.dataset.card);
    return;
  }
  const primary = t.closest("button[data-cmd]");
  if (primary) { copyCommand(primary, primary.dataset.cmd, primary.dataset.label); return; }
  const copy = t.closest("button[data-copy]");
  if (copy) { copyCommand(copy, copy.dataset.copy, "Copy"); return; }
  const det = t.closest("button.details");
  if (det) { openRow(det.closest(".row")); return; }
  if (t.closest("#todoMore")) { state.todoAll = !state.todoAll; rerender(); return; }
  if (t.closest("#needsMore")) { state.needsAll = !state.needsAll; rerender(); return; }
  if (t.closest("#idleMore")) { state.idleAll = !state.idleAll; $("agents").dataset.sig = ""; rerender(); return; }
  if (t.closest("#doneMore")) { state.doneLimit = Number.MAX_SAFE_INTEGER; rerender(); return; }
  const more = t.closest("button.lane-more");
  if (more) { const l = more.dataset.lane; state.laneLimit[l] = (state.laneLimit[l] || (l === "done" || l === "cancelled" ? 10 : LANE_PAGE)) + LANE_PAGE; rerender(); }
}
function quote(value) { return "'" + String(value).replaceAll("'", "'\"'\"'") + "'"; }
function init() {
  $("content").append(h("dialog", {id: "cardDrawer", "aria-labelledby": "drawerTitle"},
    h("button", {type: "button", class: "tog", id: "drawerClose", text: "Close details", onclick: closeDrawer}),
    h("h2", {id: "drawerTitle"}), h("div", {id: "drawerBody"})));
  $("cardDrawer").addEventListener("cancel", closeDrawer);
  // The close event is queued; a card reopened before it fires must stay open.
  $("cardDrawer").addEventListener("close", () => { if (!$("cardDrawer").open) closeDrawer(); });
  $("panels").append(h("details", {id: "overviewCosts", class: "block fold"}, h("summary", null, h("h2", {id: "ovCostTitle", text: "Cost"}), h("span", {class: "sub", text: "Usage across all workspaces."}))));
  $("columns").append($("donePanel"));
  $("focus").append(h("aside", {id: "sidebar", "aria-label": "Team communication"}));
  $("sidebar").append($("chatter"), $("agentsPanel"));
  $("content").prepend($("tabs"));
  for (const [old, current] of [["waiting", "review"], ["stuck", "blocked"], ["decisions", "needs"]]) {
    $("needs").querySelector("summary").prepend(h("span", {id: old}));
    if (current !== "needs") $("needs").querySelector("summary").prepend(h("span", {id: current}));
  }
  $("workTabs").setAttribute("role", "tablist");
  $("workTabs").addEventListener("keydown", e => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    const tabs = [...$("workTabs").querySelectorAll("button")].filter(b => b.offsetWidth);
    const i = tabs.indexOf(e.target);
    const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    e.preventDefault();
    const id = tabs[next].dataset.tab;
    tabs[next].click();
    $("workTabs").querySelector(`[data-tab="${id}"]`).focus();
  });
  addEventListener("resize", () => { if (state.board) renderLayout(shownCards(state.board.cards || [])); });
  addEventListener("scroll", () => {
    const box = $("chat");
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 32) markChatRead();
  }, {passive: true});
  $("chat").addEventListener("scroll", () => {
    const box = $("chat");
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 32) markChatRead();
  });

  state.sound = recall("rl.sound") !== "false";
  state.notify = recall("rl.notify") === "true" && "Notification" in window && Notification.permission === "granted";
  state.treasuryBy = recall("rl.treasuryBy") || "by_model";
  state.achievements = recall("rl.achievements") !== "false";
  state.anim = recall("rl.anim") !== "false";
  updateAchieve();
  updateAnim();
  const v = parseInt(recall("rl.vol"), 10);
  if (!isNaN(v)) state.vol = Math.max(0, Math.min(100, v));
  $("vol").value = state.vol;
  try { for (const n of JSON.parse(recall("rl.cuesOff") || "[]")) if (CUE_NAMES.has(n)) state.muted.add(n); } catch (_) {}
  buildCues();
  for (const id of PANELS) { const s = recall("rl.open." + id); if (s !== null) $(id).open = s === "true"; }
  foldDefault("chatter", true);
  $("sound").addEventListener("click", () => { state.sound = !state.sound; store("rl.sound", state.sound); updateSound(); preview("delivered"); });
  $("vol").addEventListener("input", e => { setVol(+e.target.value); store("rl.vol", e.target.value); });
  $("vol").addEventListener("change", () => preview("delivered"));
  $("cueList").addEventListener("change", e => {
    const n = e.target.dataset && e.target.dataset.cue;
    if (!n) return;
    e.target.checked ? state.muted.delete(n) : state.muted.add(n);
    store("rl.cuesOff", JSON.stringify([...state.muted]));
    if (e.target.checked) preview(n);
  });
  $("cueList").addEventListener("click", e => { const b = e.target.closest("[data-try]"); if (b) preview(b.dataset.try); });
  $("notify").addEventListener("click", async () => {
    if (!("Notification" in window)) return;
    if (!state.notify && Notification.permission !== "granted") { try { await Notification.requestPermission(); } catch (_) {} }
    state.notify = !state.notify && Notification.permission === "granted";
    store("rl.notify", state.notify);
    updateNotify();
  });
  $("chatFilter").addEventListener("change", e => { state.chatFilter = e.target.value; renderChat(); });
  $("logFilter").addEventListener("change", e => { state.logFilter = e.target.value; $("log").scrollTop = 0; renderLog(true); });
  for (const id of PANELS) $(id).addEventListener("toggle", e => {
    const automatic = e.target.dataset.autoFold === String(e.target.open);
    delete e.target.dataset.autoFold;
    if (!automatic) store("rl.open." + id, e.target.open);
    if (id === "chatter" && e.target.open) renderChat();
    if (id === "logPanel") renderLog(true);
    else if (e.target.open && state.board) rerender();
  });
  $("log").addEventListener("scroll", () => renderLog(false), {passive: true});
  $("log").addEventListener("click", e => {
    const b = e.target.closest(".lrow");
    if (!b) return;
    const r = logRows()[+b.dataset.i];
    state.logSel = r && state.logSel !== r.id ? r.id : null;
    showLogDetail(state.logSel ? r : null);
    renderLog(true);
  });
  let qTimer = 0;
  $("q").addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => {
      state.q = $("q").value;
      state.laneLimit = {};
      state.needsAll = !!state.q.trim();
      if (state.q.trim() && !["focus", "all"].includes(state.sub)) goSub("all"); else rerender();
      if (statusFilter === "chatter") renderChat();
    }, 120);
  });
  $("q").addEventListener("keydown", e => {
    if (e.key === "Escape" && $("q").value) { e.stopPropagation(); $("q").value = ""; $("q").dispatchEvent(new Event("input")); }
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "?" || e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select, [contenteditable=true], dialog[open]")) return;
    e.preventDefault();
    if (!$("kbdHelp").open) $("kbdHelp").showModal();
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey || $("board").hidden) return;
    const t = e.target;
    if (t.closest("input, textarea, select, [contenteditable=true], dialog[open]")) return;
    e.preventDefault();
    $("q").focus();
    $("q").select();
  });
  $("repos").addEventListener("click", e => {
    const b = e.target.closest("button[data-repo]");
    if (!b) return;
    state.repo = b.dataset.repo;
    state.laneLimit = {};
    rerender();
  });
  $("content").addEventListener("click", onAction);
  RMQ.addEventListener("change", e => { RM = e.matches; stars(); fxDecorate(); });
  $("animBtn").addEventListener("click", () => { state.anim = !state.anim; store("rl.anim", state.anim); updateAnim(); stars(); fxDecorate(); });
  addEventListener("pointerdown", unlock, {capture: true});
  addEventListener("keydown", unlock, {capture: true});
  $("soundBanner").addEventListener("click", unlock);
  addEventListener("click", onNav);
  addEventListener("popstate", () => {
    const sub = subRoute();
    select(route());
    if (sub !== state.sub) { state.sub = sub; applySub(); if (sub === "focus" || sub === "all") rerender(); else loadSub(true); }
  });
  const onTab = e => {
    const a = e.target.closest("a[data-sub]");
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const want = FILTER_TABS[a.dataset.sub] || "";
    const leaving = Object.values(FILTER_TABS).includes(statusFilter);
    if (want || leaving) { statusFilter = want; workTab = want === "chatter" ? "chatter" : "todo"; }
    const target = want ? "focus" : a.dataset.sub;
    if (target !== state.sub) goSub(target); else rerender();
    if (want || (leaving && target === "focus")) history.replaceState(null, "", subHref(a.dataset.sub));
    renderViewTabs();
    if (want === "chatter") { $("chatter").open = true; renderChat(); }
    if (want === "treasury") $("overviewCosts").open = true;
    document.body.dataset.tab = statusFilter;
  };
  $("viewTabs").addEventListener("click", onTab);
  $("realmTabs").addEventListener("click", onTab);
  $("achieveBtn").addEventListener("click", () => {
    state.achievements = !state.achievements;
    store("rl.achievements", state.achievements);
    updateAchieve();
    if (!state.achievements && state.sub === "achievements") goSub("focus");
    else renderViewTabs();
  });
  const menus = () => [...document.querySelectorAll("details.menu, details.more-menu")];
  document.addEventListener("toggle", e => {
    const m = e.target;
    if (!m.matches || !m.matches("details.menu, details.more-menu") || !m.open) return;
    if (m.matches(".more-menu")) fillMore(m);
    for (const o of menus()) if (o !== m && o.open) o.open = false;
  }, true);
  document.addEventListener("click", e => { for (const m of menus()) if (m.open && !m.contains(e.target)) m.open = false; });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    for (const m of menus()) if (m.open) { const inside = m.contains(document.activeElement); m.open = false; if (inside) m.querySelector("summary").focus(); }
  });
  audio();
  updateSound();
  updateNotify();
  panelTitles();
  stars();
  state.view = route();
  state.sub = subRoute();
  applyView();
  blockStatus("Loading… Board data will appear shortly.");
  tick();
  setInterval(tick, POLL_MS);
  setInterval(statusClock, 30000);
}
function rerender() {
  if (state.view === "*") { if (state.overview) { renderOverview(); renderChat(); } return; }
  if (state.board) {
    const wasFirst = state.first; state.first = true; renderBoard(state.board); state.first = wasFirst;
    renderLog(true);
    renderViewTabs();
    drawSub();
  }
}
init();
})();
