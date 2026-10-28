"use strict";
(function () {
  const views = window.RaidlockViews = window.RaidlockViews || [];
  const ROLES = {tank: ["Tank", "holds the most leases and the hardest quests"], healer: ["Healer", "reviews, verifies and lifts curses for others"],
    dps: ["DPS", "closes the most quests"]};

  function role(c, f) {
    const r = ROLES[f.party];
    return r ? c.h("span", {class: `prole prole-${f.party}`, title: `${r[0]}: ${r[1]}`}, c.h("span", {class: "sr-only", text: "Party role: "}), r[0]) : null;
  }

  function bar(c, kind, pct, label, valuetext, level) {
    const fill = c.h("i");
    fill.classList.add("width-" + Math.round(Math.max(0, Math.min(100, pct || 0))));
    return c.h("div", {class: `fbar ${kind} lvl-${level || "ok"}`},
      c.h("span", {class: "fl", text: label}),
      c.h("div", {class: "track", role: "meter", "aria-label": label, "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": String(Math.round(Math.max(0, Math.min(100, pct || 0)))), "aria-valuetext": valuetext}, fill),
      c.h("span", {class: "fv", text: valuetext}));
  }

  function frame(c, f, d) {
    const ctx = f.context, burn = f.burn;
    const pct = ctx.pct;
    const pctText = pct == null ? "not measured" : pct > 100 ? "over 100%" : `${Math.round(pct)}%`;
    const tags = [];
    if (ctx.source === "estimate") tags.push("estimate");
    if (ctx.window_source !== "pricing") tags.push("assumed window");
    const ctxText = `${pctText} of ${c.ktok(ctx.window)}${tags.length ? " (" + tags.join(", ") + ")" : ""}`;
    const rate = burn.tokens_per_hour ? `${c.ktok(burn.tokens_per_hour)} tok/h · ${c.usd(burn.usd_per_hour)}/h` : "no timed runs yet";
    const warn = f.handoff ? c.h("p", {class: `handoff lvl-${ctx.level}`, role: "status"},
      c.h("b", {text: ctx.level === "critical" ? "Hand off now: " : "Hand off soon: "}),
      `context is ${pctText} full. Write a handback note and start a fresh session.`) : null;
    return c.h("div", {class: `frame ${c.agentClass(f.agent)} lvl-${ctx.level}`, "data-agent": f.agent},
      c.h("div", {class: "fhead"}, c.h("span", {class: "av", "aria-hidden": "true", text: c.avatar(f.agent)}),
        c.h("div", {class: "fname"}, c.h("span", {class: "fwho"}, c.h("b", {text: f.agent}), role(c, f)),
          c.h("span", {class: "cls", text: [c.modelName(f.model), f.status, f.card].filter(Boolean).join(" · ")}))),
      bar(c, "hp", pct, "Context", ctxText, ctx.level),
      bar(c, "mp", 100 * (burn.share || 0), "Burn rate", rate, "ok"),
      ctx.source === "estimate" ? c.h("p", {class: "note-sm", text: "No recorded run yet: estimated from the transcript and card brief at about 4 characters per token."}) : null,
      warn);
  }

  views.push({
    id: "frames",
    endpoint: "frames",
    label: {raid: "Raid frames", sub: "Context"},
    render(c, d, pane) {
      const all = (d.agents || []).slice().sort((a, b) => (b.handoff - a.handoff) || ((b.context.pct || 0) - (a.context.pct || 0)) || a.agent.localeCompare(b.agent));
      const list = all.filter(f => f.card || (f.status && f.status !== "idle") || f.handoff);
      const idle = all.filter(f => !list.includes(f));
      c.setKids(pane, [
        c.h("div", {class: "view-head"}, c.h("h2", null, "Raid frames", c.raidSub("Context and burn rate")),
          c.h("p", {class: "cls", text: `Context fill uses the prompt tokens of each agent's newest recorded run; burn rate is tokens per hour of run time. Warnings at ${d.warn_pct}% and ${d.critical_pct}%.`})),
        (d.handoffs || []).length ? c.h("p", {class: "handoff-sum", role: "status", text: `${d.handoffs.length} ${d.handoffs.length === 1 ? "agent is" : "agents are"} near the context limit: ${d.handoffs.join(", ")}`}) : null,
        list.length ? c.h("div", {class: "frames"}, list.map(f => frame(c, f, d))) : c.h("p", {class: "empty", text: all.length ? "No agent holds a card right now." : "No agents joined yet. A worker joins with raid up --join."}),
        idle.length ? c.h("details", {class: "sub-fold"}, c.h("summary", {text: `${idle.length} idle ${idle.length === 1 ? "agent" : "agents"}`}),
          c.h("div", {class: "frames"}, idle.map(f => frame(c, f, d)))) : null],
        JSON.stringify(d));
    }
  });
})();
