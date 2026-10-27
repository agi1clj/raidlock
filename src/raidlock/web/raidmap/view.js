"use strict";
(function () {
  const views = window.RaidlockViews = window.RaidlockViews || [];
  function tile(c, e) {
    const held = !!e.holder;
    const cls = ["tile", held ? c.agentClass(e.holder) : "free", e.contested ? "contested" : ""].filter(Boolean).join(" ");
    const flags = [];
    if (e.waiting.length) flags.push(c.h("span", {class: "flag", text: `${e.waiting.length} waiting: ${e.waiting.join(", ")}`}));
    if (e.denials) flags.push(c.h("span", {class: "flag", text: `${e.denials} denied`}));
    if (e.violations) flags.push(c.h("span", {class: "flag", text: `${e.violations} write ${e.violations === 1 ? "violation" : "violations"}`}));
    const who = held ? `${e.holder} on ${e.card}` : (e.cards.length ? `free · ${e.cards.join(", ")}` : "free");
    const label = `${e.path}${e.metadata_source ? " (" + e.metadata_source + ")" : ""}: ${who}${e.contested ? ", contested" : ""}`;
    const el = c.h("div", {class: cls, "data-path": e.path, "data-holder": e.holder || "", title: label},
      c.h("b", {class: "p", text: e.rest || e.path}),
      c.h("span", {class: "who", text: who}),
      e.lane && held ? c.h("span", {class: "lane-tag", text: e.lane}) : null,
      flags);
    el.classList.add("tile-weight-" + Math.min(10, 1 + e.cards.length + (held ? 1 : 0)));
    return el;
  }
  function tiles(c, d) {
    if (!(d.repos || []).length) return [c.h("div", {class: "empty", text: "No locked paths yet. A card's --locks paths show up here once the card exists."})];
    return d.repos.map(r => c.h("div", {class: "repo-zone", "data-repo": r.name},
      c.h("h3", null, r.name, c.h("span", {class: "cls", text: ` ${r.held} held · ${r.contested} contested · ${r.paths.length} ${r.paths.length === 1 ? "path" : "paths"}`})),
      c.h("div", {class: "tiles"}, r.paths.map(e => tile(c, e)))));
  }
  function legend(c, d) {
    return c.h("div", {class: "legend-row"},
      (d.agents || []).map(a => c.h("span", {class: c.agentClass(a)}, c.h("i", {class: "sw"}), a)),
      c.h("span", null, c.h("i", {class: "sw free-sw"}), "free"),
      c.h("span", null, c.h("i", {class: "sw hatch"}), "contested: a waiting card overlaps a held path, or a lock denial or write violation was recorded on it"));
  }
  views.push({
    id: "map",
    endpoint: "raidmap",
    label: {raid: "War map", plain: "Map"},
    tiles, legend,
    render(c, d, pane) {
      const head = c.h("div", {class: "view-head"},
        c.h("h2", null, c.raid ? "War map" : "Map", c.raid && c.raidSub ? c.raidSub("Map") : null),
        c.h("p", {class: "cls", text: `Which agent holds which locked paths: ${d.held} held, ${d.contested} contested. Updates every few seconds.`}),
        c.h("button", {type: "button", class: "tog", "data-go": "replay", text: c.raid ? "See how it changed in the Chronicle" : "See how it changed in Timeline"}));
      head.querySelector("[data-go]").addEventListener("click", () => c.go("replay"));
      c.setKids(pane, [head, legend(c, d), c.h("div", {class: "zones"}, tiles(c, d))], JSON.stringify([d, c.theme]));
    }
  });
})();
