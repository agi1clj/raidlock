import copy
import json
from datetime import UTC, datetime, timedelta

FX_LOG = "window.__fx = []; document.addEventListener('raidlock:fx', e => window.__fx.push(e.detail));"
POLL_MS = 3000


def iso(**delta):
    return (datetime.now(UTC) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Feed:
    def __init__(self, page, server, ws="acme-shop"):
        self.data = {}
        for kind in ("board", "frames", "achievements"):
            status, _, body = server.get(f"/api/{kind}/{ws}")
            assert status == 200, kind
            self.data[kind] = json.loads(body)
            page.route(f"**/api/{kind}/{ws}", self._handler(kind))

    def _handler(self, kind):
        def handle(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.data[kind]))

        return handle

    def card(self, cid):
        return next(c for c in self.data["board"]["cards"] if c["id"] == cid)

    def agent(self, name):
        return self.data["board"]["agents"][name]

    def frame(self, name):
        return next(f for f in self.data["frames"]["agents"] if f["agent"] == name)

    def achievement(self, aid):
        return next(a for a in self.data["achievements"]["achievements"] if a["id"] == aid)

    def snapshot(self):
        return copy.deepcopy(self.data)


def close_card(feed, cid="sitemap"):
    c = feed.card(cid)
    c.update(lane="done", lease=None, updated=iso())


def sign_receipt(feed):
    c = feed.card("sitemap")
    c["notes"] = [*c["notes"], {"ts": iso(), "by": "raid-lead", "text": "verify passed (exit 0)"}]
    c["notes_count"] += 1


def take_lease(feed):
    c = feed.card("cart-rounding")
    c.update(
        lane="doing", owner="haiku-inspector", lease={"expires": iso(minutes=40), "paths": c["locks"], "live": True}
    )
    feed.data["board"]["claimable"].pop("cart-rounding", None)
    feed.agent("haiku-inspector").update(status="working", card="cart-rounding", heartbeat=iso())


def refuse_lock(feed):
    cl = feed.data["board"]["claimable"]["promo-codes"]
    cl["reasons"] = [
        *cl["reasons"],
        "storefront/checkout overlaps storefront/api/cart held by opus-checkout on card cart-api "
        f"in workspace acme-shop until {iso(minutes=30)}",
    ]


def block_card(feed, cid="sitemap"):
    feed.card(cid)["lane"] = "blocked"


def stall_agent(feed):
    feed.agent("sonnet-pages")["heartbeat"] = iso(hours=-2)


def record_cost(feed):
    k = feed.card("sitemap")["cost"]
    k.update(usd=k["usd"] + 0.12, usd_equiv=k["usd_equiv"] + 0.12, tokens=k["tokens"] + 9000)


def fill_context(feed):
    f = feed.frame("sonnet-pages")
    f["context"].update(pct=78.0, tokens=156000, level="warning")


def unlock_achievement(feed):
    feed.achievement("first-seal").update(earned=True, by="raid-lead", at=iso(), detail="sitemap")


EVENTS = {
    "done": ("/acme-shop?view=all", close_card, ".toast.quest", ()),
    "verify": ("/acme-shop", sign_receipt, '.row[data-id="sitemap"] .fx-verify .fx-stamp', ()),
    "claim": ("/acme-shop", take_lease, '.agent[data-agent="haiku-inspector"] .fx-claim', ("agentsPanel",)),
    "refused": ("/acme-shop", refuse_lock, '.row[data-id="promo-codes"] .fx-refused', ()),
    "blocked": ("/acme-shop?view=all", block_card, '.row[data-id="sitemap"].fx-pulse', ()),
    "stalled": ("/acme-shop", stall_agent, '.agent[data-agent="sonnet-pages"].fx-pulse.fx-stall', ("agentsPanel",)),
    "cost": ("/acme-shop", record_cost, '.row[data-id="sitemap"] .fx-float', ()),
    "context": ("/acme-shop?view=frames", fill_context, '.frame[data-agent="sonnet-pages"] .fx-level', ()),
    "achievement": ("/acme-shop?view=achievements", unlock_achievement, ".ach-banner", ()),
}
