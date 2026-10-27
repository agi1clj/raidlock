"use strict";
(function () {
  const views = window.RaidlockViews = window.RaidlockViews || [];
  const LANES = [["todo", "To do"], ["doing", "In progress"], ["review", "Review"], ["blocked", "Blocked"], ["done", "Done"]];
  const STEP_MS = 700;
  let st = null;
  let inFlight = false;

  function stop() {
    if (st && st.timer) { clearInterval(st.timer); st.timer = 0; }
    if (st && st.play) { st.play.setAttribute("aria-pressed", "false"); st.play.textContent = "Play"; }
  }
  function markText(m) {
    if (!m) return "No records yet. The timeline fills as agents claim, note and move cards.";
    return `#${m.seq} · ${st.c.localTime(m.ts)} · ${m.agent || "?"}: ${m.text || m.kind}`;
  }
  function label() {
    const m = st.marks[st.idx];
    st.c.setKids(st.out, [markText(m)]);
    st.out.title = m ? m.ts : "";
    st.range.setAttribute("aria-valuetext", m ? `record ${m.seq}, ${st.c.localTime(m.ts)}` : "empty");
    st.play.disabled = !st.marks.length;
    st.range.disabled = !st.marks.length;
    st.prev.disabled = st.idx <= 0;
    st.next.disabled = st.idx >= st.marks.length - 1;
  }
  function lanes(c, d) {
    return c.h("div", {class: "rp-lanes"}, LANES.map(([id, name]) => {
      const cards = (d.cards || []).filter(x => x.lane === id);
      return c.h("section", {class: "rp-lane", "data-lane": id, "aria-label": name},
        c.h("h3", null, name, c.h("span", {class: "count", text: String((d.counts || {})[id] || 0)})),
        c.h("div", {class: "rp-chips"}, cards.length ? cards.map(x => c.h("span", {class: "rp-chip" + (x.owner ? " " + c.agentClass(x.owner) : ""), "data-id": x.id, title: x.title},
          x.id, x.owner ? c.h("small", {text: " " + x.owner}) : null)) : c.h("span", {class: "cls", text: "none"})));
    }));
  }
  function show(c, d, map) {
    st.cur = d;
    const recent = (d.recent || []).map(m => c.h("li", {class: "k-" + m.kind}, c.h("b", {text: `#${m.seq} ${m.agent || "?"}`}), ` ${m.kind}${m.task ? " on " + m.task : ""}${m.source && m.source !== "board" ? " (" + m.source + ")" : ""}: `, m.text || ""));
    const ev = d.events || {};
    const notes = [];
    if (d.untracked) notes.push(`${d.untracked} cards have no creation record in the transcript and appear once they are first mentioned.`);
    if (d.truncated) notes.push(`Only the newest ${st.marks.length} records are on the slider; older slider entries are omitted.`);
    if (d.incomplete_history) notes.push(`Incomplete history: reconstruction stops at ${d.at}; true latest sequence is ${d.last}.`);
    if (d.non_core_records) notes.push("Non-core or unknown records were skipped.");
    if (map && map.error) notes.push("Incomplete territory: " + map.error);
    const mapDef = c.def("map");
    c.setKids(st.body, [
      c.h("p", {class: "cls rp-sum", text: `${d.total} records · lock denials ${ev.lock_denied || 0} · write violations ${ev.lock_violation || 0} so far`}),
      lanes(c, d),
      c.h("h3", {text: "Locks at this point"}),
      map && mapDef ? mapDef.legend(c, map) : null,
      map && mapDef ? c.h("div", {class: "zones"}, mapDef.tiles(c, map)) : null,
      c.h("h3", {text: "Records up to this point"}),
      recent.length ? c.h("ul", {class: "rp-recent"}, recent) : c.h("p", {class: "cls", text: "Nothing yet."}),
      notes.length ? c.h("p", {class: "note-sm", text: notes.join(" ")}) : null]);
  }
  async function load(c) {
    const m = st.marks[st.idx];
    if (!m || inFlight) return;
    inFlight = true;
    const owner = st;
    const token = ++st.token;
    try {
      const d = await c.api("replay", `?at=${m.seq}&marks=0`);
      const map = d && d.territory;
      if (st === owner && token === st.token && d) show(c, d, map);
    } catch (_) {} finally {
      inFlight = false;
      if (st === owner && st.marks[st.idx] !== m) load(c);
    }
  }
  function scrub(c, i, now) {
    st.idx = Math.max(0, Math.min(st.marks.length - 1, i));
    st.range.value = String(st.idx);
    label();
    clearTimeout(st.debounce);
    if (now) load(c); else st.debounce = setTimeout(() => load(c), 120);
  }
  function build(c, d, pane) {
    stop();
    st = {ws: c.ws, marks: d.marks || [], idx: Math.max(0, (d.marks || []).length - 1), timer: 0, token: 0, debounce: 0};
    const max = Math.max(0, st.marks.length - 1);
    st.range = c.h("input", {type: "range", min: "0", max: String(max), step: "1", value: String(st.idx), id: "replayRange", "aria-label": "Position in the transcript"});
    st.prev = c.h("button", {type: "button", class: "tog", text: "Previous", "aria-label": "Previous record"});
    st.next = c.h("button", {type: "button", class: "tog", text: "Next", "aria-label": "Next record"});
    st.play = c.h("button", {type: "button", class: "tog", "aria-pressed": "false", text: "Play"});
    st.live = c.h("button", {type: "button", class: "tog", text: "Refresh"});
    st.out = c.h("output", {class: "rp-out", for: "replayRange"});
    st.body = c.h("div", {class: "rp-body"});
    st.range.addEventListener("input", () => { stop(); scrub(st.c, +st.range.value, false); });
    st.prev.addEventListener("click", () => { stop(); scrub(st.c, st.idx - 1, true); });
    st.next.addEventListener("click", () => { stop(); scrub(st.c, st.idx + 1, true); });
    st.live.addEventListener("click", () => { stop(); st.ws = null; st.c.reload(); });
    st.play.addEventListener("click", () => {
      if (st.timer) { stop(); return; }
      if (st.idx >= st.marks.length - 1) scrub(st.c, 0, true);
      st.play.setAttribute("aria-pressed", "true");
      st.play.textContent = "Pause";
      st.timer = setInterval(() => {
        if (inFlight) return;
        if (!st || st.idx >= st.marks.length - 1) { stop(); return; }
        scrub(st.c, st.idx + 1, true);
      }, STEP_MS);
    });
    pane.replaceChildren(
      c.h("div", {class: "view-head"}, c.h("h2", null, c.raid ? "Chronicle" : "Timeline", c.raid && c.raidSub ? c.raidSub("Timeline") : null),
        c.h("p", {class: "cls", text: "The board rebuilt from the transcript at any point. Transcript records are not signed, so treat this as unverified history; lock paths use today's repo layout."})),
      c.h("div", {class: "rp-controls"}, st.prev, st.range, st.next, st.play, st.live),
      st.out, st.body);
    st.c = c;
    label();
    show(c, d, d.territory);
    if (st.marks.length) load(c);
  }
  views.push({
    id: "replay",
    endpoint: "replay",
    label: {raid: "Chronicle", plain: "Timeline"},
    poll: false,
    leave() { stop(); },
    render(c, d, pane) {
      if (c.fresh || !st || st.ws !== c.ws || !pane.contains(st.range)) { build(c, d, pane); return; }
      st.c = c;
      if (st.cur) load(c);
    }
  });
})();
