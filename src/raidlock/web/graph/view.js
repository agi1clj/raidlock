"use strict";
(function () {
  const views = window.RaidlockViews = window.RaidlockViews || [];
  const NW = 224, NH = 66, CG = 56, RG = 12, PAD = 12, LW = 230, CELL = 36, HEAD = 34;

  function clip(t, n) { t = String(t || ""); return t.length > n ? t.slice(0, n - 1) + "…" : t; }

  function depGraph(c, d) {
    const nodes = [...(d.nodes || [])];
    if (!nodes.length) return c.h("p", {class: "empty", text: "No cards yet. Add a card to see its dependencies."});
    const known = new Set(nodes.map(n => n.id));
    for (const e of d.edges || []) for (const id of [e.from, e.to]) {
      if (!known.has(id)) {
        known.add(id);
        nodes.push({id, title: "Missing card", lane: "missing", depth: 0, missing: true});
      }
    }
    const linked = new Set([...(d.edges || []).flatMap(e => [e.from, e.to]), ...(d.cycles || []).flat()]);
    const drawn = nodes.filter(n => linked.has(n.id));
    const loose = nodes.length - drawn.length;
    const looseNote = loose ? c.h("p", {class: "cls", text: `${loose} ${loose === 1 ? "card has" : "cards have"} no dependencies; they are listed under Dependencies as text.`}) : null;
    if (!drawn.length) return c.h("div", null, c.h("p", {class: "empty", text: c.raid ? "No quest chains yet. Link quests with raid edit <card> --depends <other>." : "No dependencies between cards yet. Link cards with raid edit <card> --depends <other>."}), looseNote);
    const at = new Map(), rowAt = {};
    let rows = 0;
    for (const n of drawn) {
      const col = Math.max(0, n.depth || 0), row = rowAt[col] = (rowAt[col] ?? -1) + 1;
      rows = Math.max(rows, row + 1);
      at.set(n.id, [PAD + col * (NW + CG), PAD + row * (NH + RG)]);
    }
    const width = PAD * 2 + Math.max(...drawn.map(n => Math.max(0, n.depth || 0))) * (NW + CG) + NW + CG;
    const height = PAD * 2 + rows * (NH + RG);
    const s = c.svg;
    const defs = s("defs", null, s("marker", {id: "rlArrow", viewBox: "0 0 8 8", refX: "8", refY: "4", markerWidth: "8", markerHeight: "8", orient: "auto"},
      s("path", {d: "M0,0 L8,4 L0,8 z", class: "arrow"})));
    const edges = (d.edges || []).map(e => {
      const [x1, y1] = at.get(e.from), [x2, y2] = at.get(e.to);
      const start = x1 + NW, end = x2 > x1 ? x2 : x2 + NW;
      const track = start + CG / 2;
      const path = e.from === e.to
        ? `M${start},${y1 + 18} h20 v30 h-20`
        : `M${start},${y1 + NH / 2} H${track} V${y2 + NH / 2} H${end}`;
      return s("path", {class: `edge${e.missing ? " missing" : ""}`, "data-from": e.from, "data-to": e.to, d: path, "marker-end": "url(#rlArrow)"},
        s("title", {text: `${e.from} → ${e.to}${e.missing ? " (missing card)" : ""}`}));
    });
    const boxes = drawn.map(n => {
      const [x, y] = at.get(n.id);
      const label = `${n.id}: ${n.title} (${n.lane}${n.owner ? ", " + n.owner : ""})`;
      const box = s(n.missing ? "g" : "a", {class: `gnode lane-${n.lane}`, "data-id": n.id,
        "data-card": n.missing ? null : n.id, href: n.missing ? null : `#card-${encodeURIComponent(n.id)}`,
        tabindex: n.missing ? null : "0", "aria-label": label, transform: `translate(${x},${y})`},
        s("title", {text: label}),
        s("rect", {width: NW, height: NH, rx: "8"}),
        s("text", {x: "10", y: "18", class: "gid", text: clip(n.id, 27)}),
        s("text", {x: "10", y: "37", class: "gtitle", text: clip(n.title, 30)}),
        s("text", {x: "10", y: "55", class: "gsub", text: clip(`${n.lane}${n.owner ? " · " + n.owner : ""}`, 30)}));
      if (!n.missing) box.addEventListener("keydown", e => {
        if (e.key === "Enter") { e.preventDefault(); box.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true})); }
      });
      return box;
    });
    return c.h("div", null, looseNote, c.h("div", {class: "scrollbox dep-scroll", tabindex: "0", role: "region", "aria-label": "Dependency graph, scrollable"},
      s("svg", {width, height, viewBox: `0 0 ${width} ${height}`, role: "group", "aria-label": "Dependencies: arrows point to waiting cards", class: "dep-svg"}, defs, edges, boxes)));
  }

  function heat(c, d) {
    const hm = d.heat || {};
    const rows = hm.rows || [], days = hm.days || [];
    if (!rows.length) return c.h("p", {class: "empty", text: "No contention recorded: no lock denials, write violations, takeovers or waiting cards."});
    const s = c.svg;
    const cols = days.length + 1;
    const width = LW + cols * CELL + PAD, height = HEAD + rows.length * CELL + PAD;
    const peak = Math.max(1, hm.peak || 0);
    const kids = [];
    days.forEach((day, i) => kids.push(s("text", {class: "hday", x: LW + i * CELL + CELL / 2, y: HEAD - 10, "text-anchor": "middle", text: day.slice(5)})));
    kids.push(s("text", {class: "hday", x: LW + days.length * CELL + CELL / 2, y: HEAD - 10, "text-anchor": "middle", text: "wait"}));
    rows.forEach((r, j) => {
      const y = HEAD + j * CELL;
      kids.push(s("text", {class: "hpath", x: LW - 8, y: y + CELL / 2 + 4, "text-anchor": "end", text: clip(r.path, 30)}, s("title", {text: r.path})));
      r.cells.forEach((cell, i) => {
        const n = cell.lock_denied + cell.lock_violation + cell.takeover;
        const lvl = n ? Math.min(4, 1 + Math.floor(3 * n / peak)) : 0;
        kids.push(s("rect", {class: `hcell heat-${lvl}`, x: LW + i * CELL + 2, y: y + 2, width: CELL - 4, height: CELL - 4, rx: "4", "data-path": r.path, "data-day": days[i]},
          s("title", {text: `${r.path} on ${days[i]}: ${cell.lock_denied} denied, ${cell.lock_violation} write violations, ${cell.takeover} takeovers`})));
        if (n) kids.push(s("text", {class: "hnum", x: LW + i * CELL + CELL / 2, y: y + CELL / 2 + 4, "text-anchor": "middle", text: String(n)}));
      });
      const w = r.waiting;
      kids.push(s("rect", {class: `hcell wait-${w ? Math.min(3, w) : 0}`, x: LW + days.length * CELL + 2, y: y + 2, width: CELL - 4, height: CELL - 4, rx: "4"},
        s("title", {text: `${r.path}: ${w} cards waiting now`})));
      if (w) kids.push(s("text", {class: "hnum", x: LW + days.length * CELL + CELL / 2, y: y + CELL / 2 + 4, "text-anchor": "middle", text: String(w)}));
    });
    return c.h("div", {class: "scrollbox", tabindex: "0", role: "region", "aria-label": "Lock contention heatmap, scrollable"},
      s("svg", {width, height, viewBox: `0 0 ${width} ${height}`, role: "img", class: "heat-svg",
        "aria-label": `Lock contention by path and day: ${rows.length} paths, busiest ${rows[0].path}`}, kids));
  }

  function asText(c, d) {
    const titles = Object.fromEntries((d.nodes || []).map(n => [n.id, n.title]));
    const name = id => titles[id] ? `${id}: ${titles[id]}` : `${id}: Missing card`;
    const items = (d.edges || []).map(e => c.h("li", {text: `${name(e.from)} → ${name(e.to)}${e.missing ? " (missing card)" : ""}`}));
    for (const n of d.nodes || []) if (!(d.edges || []).some(e => e.from === n.id || e.to === n.id)) items.push(c.h("li", {text: `${name(n.id)} (no dependencies)`}));
    for (const cy of d.cycles || []) items.push(c.h("li", {class: "warn", text: `cycle: ${cy.join(" → ")}`}));
    return c.h("details", {class: "as-text"}, c.h("summary", {text: "Dependencies as text"}), items.length ? c.h("ul", null, items) : c.h("p", {class: "cls", text: "No dependencies."}));
  }

  views.push({
    id: "graph",
    endpoint: "graph",
    label: {raid: "Quest web", plain: "Dependencies"},
    render(c, d, pane) {
      const repo = document.querySelector('#repos [data-repo][aria-pressed="true"]')?.dataset.repo || "";
      if (repo) {
        const ids = new Set((d.nodes || []).filter(n => (n.repos || []).includes(repo)).map(n => n.id));
        const edges = (d.edges || []).filter(e => ids.has(e.to));
        const needed = new Set([...ids, ...edges.map(e => e.from)]);
        d = {...d, nodes: (d.nodes || []).filter(n => needed.has(n.id)), edges,
          cycles: (d.cycles || []).filter(cy => cy.every(id => needed.has(id)))};
      }
      const active = pane.contains(document.activeElement) ? document.activeElement.closest(".gnode")?.dataset.id : null;
      const oldScroll = pane.querySelector(".dep-scroll");
      const scroll = oldScroll ? [oldScroll.scrollLeft, oldScroll.scrollTop] : [0, 0];
      const cycles = (d.cycles || []).length;
      c.setKids(pane, [
        c.h("div", {class: "view-head"}, c.h("h2", null, c.raid ? "Quest web" : "Dependencies", c.raid && c.raidSub ? c.raidSub("Dependencies") : null),
          c.h("p", {class: "cls", text: `${(d.nodes || []).length} cards, ${(d.edges || []).length} ${(d.edges || []).length === 1 ? "dependency" : "dependencies"}${cycles ? `, ${cycles} cycle${cycles === 1 ? "" : "s"}` : ""}. Arrows point from a card to the cards that wait on it.`})),
        c.h("div", {class: "legend-row"}, ["todo", "doing", "review", "blocked", "done"].map(l => c.h("span", null, c.h("i", {class: "sw lane-sw lane-" + l}), l))),
        depGraph(c, d),
        asText(c, d),
        c.h("h3", {text: "Lock contention"}),
        c.h("p", {class: "cls", text: "Lock denials, write violations and takeovers per path and day, plus cards waiting on a held path now. Darker means more."}),
        heat(c, d)], JSON.stringify([d, c.theme, c.ws, repo]));
      const nextScroll = pane.querySelector(".dep-scroll");
      if (nextScroll) { nextScroll.scrollLeft = scroll[0]; nextScroll.scrollTop = scroll[1]; }
      if (active) [...pane.querySelectorAll(".gnode")].find(n => n.dataset.id === active)?.focus({preventScroll: true});
    }
  });
})();
