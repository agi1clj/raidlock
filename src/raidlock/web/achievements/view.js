"use strict";
(function () {
  const views = window.RaidlockViews = window.RaidlockViews || [];

  const ART = {
    sun: s => [s("circle", {cx: 24, cy: 24, r: 9, class: "fillc"}),
      ...[0, 45, 90, 135, 180, 225, 270, 315].map(a => s("line", {x1: 24, y1: 6, x2: 24, y2: 11, transform: `rotate(${a} 24 24)`, class: "strokec"}))],
    seal: s => [s("path", {class: "fillc", d: "M24 6l4 3 5-1 2 5 5 2-1 5 3 4-3 4 1 5-5 2-2 5-5-1-4 3-4-3-5 1-2-5-5-2 1-5-3-4 3-4-1-5 5-2 2-5 5 1z"}),
      s("path", {class: "cut", d: "M17 24l5 5 9-10"})],
    eyes: s => [s("ellipse", {cx: 15, cy: 24, rx: 9, ry: 6, class: "strokec"}), s("ellipse", {cx: 33, cy: 24, rx: 9, ry: 6, class: "strokec"}),
      s("circle", {cx: 16, cy: 24, r: 3, class: "fillc"}), s("circle", {cx: 34, cy: 24, r: 3, class: "fillc"})],
    quill: s => [s("path", {class: "fillc", d: "M38 8C24 10 16 20 13 34l3 1c3-8 8-14 15-18-5 5-9 10-11 17 11-4 17-14 18-26z"}),
      s("line", {x1: 13, y1: 34, x2: 10, y2: 41, class: "strokec"})],
    chord: s => [10, 24, 38].flatMap((x, i) => [s("line", {x1: x, y1: 10, x2: x, y2: 34, class: "strokec"}), s("circle", {cx: x - 3, cy: 36 - i * 6, r: 4, class: "fillc"})]),
    hands: s => [s("path", {class: "fillc", d: "M10 38V20a2 2 0 014 0v8V14a2 2 0 014 0v14V12a2 2 0 014 0v16-12a2 2 0 014 0v18c0 6-4 10-10 10h-1c-3 0-5-2-7-4z"}),
      s("path", {class: "strokec", d: "M30 30l4-4a2 2 0 013 3l-6 7"})],
    rune: s => [s("circle", {cx: 24, cy: 24, r: 15, class: "strokec"}), s("path", {class: "strokec", d: "M16 24l6 6 11-13"})],
    shield: s => [s("path", {class: "fillc", d: "M24 6l15 5v11c0 10-7 17-15 20C16 39 9 32 9 22V11z"}), s("path", {class: "strokec", d: "M24 14v20M17 22h14"})],
    blade: s => [s("path", {class: "fillc", d: "M34 6l8 0 0 8-18 18-8-8z"}), s("path", {class: "strokec", d: "M14 26l8 8M10 38l6-6"})],
    moon: s => [s("path", {class: "fillc", d: "M30 8a16 16 0 1 0 10 26A13 13 0 0 1 30 8z"}), s("circle", {cx: 36, cy: 14, r: 2, class: "fillc"})]
  };

  function art(c, a) {
    const draw = ART[a.art] || ART.sun;
    return c.svg("svg", {viewBox: "0 0 48 48", width: "48", height: "48", class: "ach-art", "aria-hidden": "true"},
      c.svg("circle", {cx: 24, cy: 24, r: 22, class: "ring"}), draw(c.svg));
  }

  function badge(c, a) {
    const p = a.progress;
    const status = a.earned ? `Earned${a.by ? " by " + a.by : ""}${a.at ? ", " + c.ago(a.at) + " ago" : ""}${a.detail ? " · " + a.detail : ""}`
      : p ? `Not earned yet · ${p.value}/${p.goal}` : "Not earned yet";
    let meter = null;
    if (!a.earned && p) {
      const fill = c.h("i");
      fill.classList.add("width-" + Math.round(Math.max(0, Math.min(100, 100 * p.value / Math.max(1, p.goal)))));
      meter = c.h("div", {class: "ach-meter", role: "meter", "aria-label": `${a.name} progress`, "aria-valuemin": "0", "aria-valuemax": String(p.goal), "aria-valuenow": String(p.value)}, fill);
    }
    return c.h("div", {class: "ach " + (a.earned ? "earned" : "locked"), "data-id": a.id},
      art(c, a), c.h("div", {class: "ach-body"}, c.h("b", {text: a.name}), c.h("span", {text: a.description}), meter, c.h("small", {text: status})));
  }

  views.push({
    id: "achievements",
    endpoint: "achievements",
    label: {raid: "Feats", sub: "Achievements"},
    optIn: "achievements",
    render(c, d, pane) {
      const titles = Object.entries(d.titles || {});
      c.setKids(pane, [
        c.h("div", {class: "view-head"}, c.h("h2", null, "Feats", c.raidSub("Achievements")),
          c.h("p", {class: "cls", text: `${d.incomplete_history ? "History is incomplete (newest record read: #" + d.last + "). " : ""}${d.earned} of ${d.total} earned, from recorded events only. Turn them off in Settings.`})),
        titles.length ? c.h("section", {class: "titles", "aria-label": "Titles earned"}, c.h("h3", {text: "Titles earned"}),
          c.h("ul", null, titles.map(([who, title]) => c.h("li", null, c.h("b", {text: who}), " " + title)))) : null,
        c.h("div", {class: "achs"}, (d.achievements || []).map(a => badge(c, a)))], JSON.stringify([d, Math.floor(Date.now() / 60000)]));
    }
  });
})();
