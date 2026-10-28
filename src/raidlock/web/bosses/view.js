"use strict";
(function () {
  const views = window.RaidlockViews = window.RaidlockViews || [];
  const BAG_CAP = 12;

  function boss(c, b) {
    const fill = c.h("i");
    fill.classList.add("width-" + Math.max(0, Math.min(100, b.hp)));
    const left = b.slain ? "slain" : `${b.open} of ${b.total} quests still open`;
    return c.h("li", {class: "boss" + (b.slain ? " slain" : ""), "data-id": b.id},
      c.h("div", {class: "boss-head"}, c.h("b", {text: b.title}), c.h("span", {class: "cls", text: `${b.id} · ${b.dependents} quests wait on it`})),
      c.h("div", {class: "boss-hp", role: "meter", "aria-label": `${b.title} HP`, "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": String(b.hp), "aria-valuetext": `${b.hp}%, ${left}`}, fill),
      b.slain ? c.h("p", {class: "boss-note slain-at"}, "Slain ", b.slain_at) : c.h("p", {class: "boss-note", text: `HP ${b.hp}% · ${left}`}));
  }

  function bag(c, g) {
    return c.h("li", {class: "bag", "data-agent": g.agent}, c.h("b", {text: g.agent}),
      c.h("ul", {class: "drops"}, g.items.map(i => c.h("li", {class: `drop r${i.tier}`, "data-card": i.card},
        c.h("span", {class: "drop-name", text: i.item}), c.h("span", {class: "cls", text: `${i.rarity} · ${i.title}`})))));
  }

  views.push({
    id: "bosses",
    endpoint: "bosses",
    label: {raid: "Boss board", sub: "Big quests"},
    render(c, d, pane) {
      const bosses = d.bosses || [], bags = d.bags || [];
      const drops = bags.reduce((n, g) => n + g.items.length, 0);
      const best = g => Math.max(...g.items.map(i => i.tier));
      const ranked = bags.slice().sort((a, b) => best(b) - best(a) || b.items.length - a.items.length || a.agent.localeCompare(b.agent));
      c.setKids(pane, [
        c.h("div", {class: "view-head"}, c.h("h2", null, "Boss board", c.raidSub("Big quests and loot")),
          c.h("p", {class: "cls", text: `A quest that ${d.boss_dependents} or more others wait on, or that the raid leader names, is a boss. Its HP is the share of it and its dependents still open. Every finished quest drops one item for the agent who closed it.`})),
        bosses.length ? c.h("ul", {class: "bosses"}, bosses.map(b => boss(c, b)))
          : c.h("p", {class: "empty", text: `No bosses roam this realm yet. A quest becomes a boss once ${d.boss_dependents} others wait on it, or when the raid leader names it one.`}),
        c.h("h3", {class: "loot-head", text: `Loot bags · ${drops} ${drops === 1 ? "drop" : "drops"}`}),
        bags.length ? c.h("ul", {class: "bags"}, ranked.slice(0, BAG_CAP).map(g => bag(c, g)))
          : c.h("p", {class: "empty", text: "The bags are empty. Turn in a quest to earn the first drop."}),
        ranked.length > BAG_CAP ? c.h("details", {class: "more-bags"}, c.h("summary", {text: `Show all ${ranked.length} bags`}),
          c.h("ul", {class: "bags"}, ranked.slice(BAG_CAP).map(g => bag(c, g)))) : null],
        JSON.stringify(d));
    }
  });
})();
