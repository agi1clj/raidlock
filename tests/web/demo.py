import contextlib
import json
import os
from pathlib import Path

from raidlock.core import Board, RaidlockError, Usage

XSS = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>'


def make_repo(path, remote):
    path = Path(path)
    (path / ".git").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (path / ".git" / "config").write_text(
        f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {remote}\n', encoding="utf-8"
    )
    return path


class Demo:
    def __init__(self, root, name, repo):
        self.root = Path(root) / name
        make_repo(self.root / repo, f"git@github.com:demo/{name}-{repo}.git")
        self.repo = repo
        self.board, token = Board.init(self.root, lead="raid-lead", runtime="human")
        from raidlock.core import lead

        table = lead.process_table()
        self.board.proc_table = lambda: table
        self.lead = self.board.caller(agent="raid-lead", lead_secret=token)
        self.board.set_setting(self.lead, "require_verify", False)
        self.board.set_setting(self.lead, "require_review.other_provider", False)
        self.callers = {}
        self.tokens = {}

    def team(self, name, cap=3, mission=""):
        self.board.add_team(self.lead, name, cap, mission=mission)

    def agent(self, name, team, runtime, model, role="worker", billing="unknown"):
        r = self.board.join(
            self.lead, agent=name, team=team, role=role, runtime=runtime, model_id=model, billing=billing
        )
        self.tokens[name] = r["token"]
        self.callers[name] = self.board.caller(agent=name, token=r["token"], env={})
        return self.callers[name]

    def card(self, cid, title, team, path, repo=None, **kw):
        return self.board.new_card(self.lead, title, card_id=cid, team=team, locks=f"{repo or self.repo}/{path}", **kw)

    def claim(self, cid, who):
        r = self.board.claim(cid, self.board.caller(agent=who, token=self.tokens[who], env={}))
        self.callers[who] = self.board.caller(agent=who, cap=r["cap"], env={})
        return r

    def note(self, cid, who, text):
        self.board.note(cid, self.callers[who], text)

    def cost(self, cid, who, inp, cached, out, usd=None, ms=0):
        self.board.record_cost(
            cid, self.callers[who], usage=Usage(input=inp, cached_input=cached, output=out), usd=usd, duration_ms=ms
        )

    def move(self, cid, who, lane, note=None):
        self.board.move(cid, lane, self.callers[who], note=note)

    def review(self, cid, who, verdict, note=""):
        self.board.review(cid, self.callers[who], verdict, note)

    def done(self, cid):
        self.board.move(cid, "done", self.lead, note="verified")

    def msg(self, who, to, text, task=None):
        self.board.send(
            self.board.caller(agent=who, token=self.tokens[who], env={}) if who in self.tokens else self.lead,
            to,
            text,
            task=task,
        )


def acme_shop(root, showcase=False):
    d = Demo(root, "acme-shop", "storefront")
    make_repo(d.root / "web", "git@github.com:demo/acme-shop-web.git")
    make_repo(d.root / "admin", "git@github.com:demo/acme-shop-admin.git")
    d.team("checkout", 3, "Make checkout fast and safe")
    d.team("catalog", 2, "Product pages and search")
    d.agent("opus-checkout", "checkout", "claude-code", "claude-opus-5-5", billing="plan")
    d.agent("codex-payments", "checkout", "codex-cli", "gpt-6-astra", billing="api")
    d.agent("gemini-search", "catalog", "gemini-cli", "gemini-3-pro")
    d.agent("sonnet-pages", "catalog", "claude-code", "claude-sonnet-5-5", billing="api")
    d.agent("haiku-inspector", "", "claude-code", "claude-haiku-5-5", role="reviewer")
    d.card(
        "cart-api",
        "Cart API returns totals with tax",
        "checkout",
        "api/cart",
        ticket="SHOP-12",
        scope="Add tax lines to the cart total endpoint",
        verify="pytest tests/cart",
    )
    d.card("pay-retry", "Retry declined card payments once", "checkout", "api/payments", ticket="SHOP-14")
    d.card("search-typo", "Typo-tolerant product search", "catalog", "search", repo="web")
    d.card("pdp-images", "Lazy-load product images", "catalog", "pdp", repo="web")
    d.card("promo-codes", "Promo codes at checkout", "checkout", "api/promo", depends="cart-api")
    d.card("sitemap", "Generate sitemap.xml nightly", "catalog", "jobs/sitemap", repo="web")
    d.card("refunds", "Partial refunds from the admin", "checkout", "refunds", repo="admin")
    d.claim("cart-api", "opus-checkout")
    d.note("cart-api", "opus-checkout", "tax table wired; 14 tests green")
    if not showcase:
        d.note("cart-api", "opus-checkout", XSS)
    d.cost("cart-api", "opus-checkout", 42000, 310000, 18000, ms=1260000)
    d.claim("pay-retry", "codex-payments")
    d.note("pay-retry", "codex-payments", "idempotency key added to the retry path")
    d.cost("pay-retry", "codex-payments", 88000, 120000, 21000, usd=0.94, ms=900000)
    d.move("pay-retry", "codex-payments", "review", note="ready: retry once, logs the decline code")
    d.review("pay-retry", "haiku-inspector", "changes", "log line leaks the last four digits; mask them")
    d.note("pay-retry", "codex-payments", "masked card digits in the decline log")
    d.move("pay-retry", "codex-payments", "review", note="masked")
    d.review("pay-retry", "haiku-inspector", "approve", "looks right")
    d.done("pay-retry")
    d.claim("refunds", "codex-payments")
    d.note("refunds", "codex-payments", "partial refund form with an amount cap; audit line per refund")
    d.move("refunds", "codex-payments", "review", note="ready for a second pair of eyes")
    if showcase:
        d.review("refunds", "haiku-inspector", "approve", "cap and audit line verified")
    d.claim("search-typo", "gemini-search")
    d.note("search-typo", "gemini-search", "trigram index needs the ops team to bump memory")
    d.cost("search-typo", "gemini-search", 51000, 0, 9000, ms=600000)
    d.move("search-typo", "gemini-search", "blocked", note="waiting on memory bump")
    d.claim("pdp-images", "sonnet-pages")
    d.note("pdp-images", "sonnet-pages", "native loading=lazy plus width/height to stop layout shift")
    d.cost("pdp-images", "sonnet-pages", 23000, 140000, 7000, ms=480000)
    d.move("pdp-images", "sonnet-pages", "review", note="Lighthouse CLS 0.01")
    d.review("pdp-images", "haiku-inspector", "approve", "CLS fixed")
    d.done("pdp-images")
    d.claim("sitemap", "sonnet-pages")
    d.note("sitemap", "sonnet-pages", "nightly job scheduled; writes to the public bucket")
    d.cost("sitemap", "sonnet-pages", 9000, 60000, 3000, ms=240000)
    d.msg(
        "opus-checkout",
        "codex-payments",
        "cart totals now include tax lines; promo codes can build on them",
        task="cart-api",
    )
    d.msg("raid-lead", "gemini-search", "memory bump approved, should land within the hour")
    d.msg("codex-payments", "opus-checkout", "thanks, I will read the totals from the cart API")
    contention(d)
    return d


def contention(d):
    d.card("cart-rounding", "Round cart totals to the cent", "checkout", "api/cart/rounding")
    with contextlib.suppress(RaidlockError):
        d.board.claim("cart-rounding", d.board.caller(agent="codex-payments", token=d.tokens["codex-payments"], env={}))
    d.board.check_write(str(d.root / d.repo / "api" / "cart" / "totals.py"), d.callers["gemini-search"])


def pocket_notes(root):
    d = Demo(root, "pocket-notes", "app")
    d.team("mobile", 2, "Offline-first notes app")
    d.agent("opus-sync", "mobile", "claude-code", "claude-opus-5-5", billing="plan")
    d.agent("codex-ui", "mobile", "codex-cli", "gpt-6-astra")
    d.card("sync-merge", "Merge offline edits without losing text", "mobile", "sync")
    d.card("dark-mode", "Dark mode for the editor", "mobile", "ui/editor")
    d.card("export-md", "Export notes to Markdown", "mobile", "export")
    d.claim("sync-merge", "opus-sync")
    d.note("sync-merge", "opus-sync", "three-way merge on paragraphs; conflict marker when both sides edit")
    d.cost("sync-merge", "opus-sync", 30000, 200000, 12000, ms=900000)
    d.claim("dark-mode", "codex-ui")
    d.cost("dark-mode", "codex-ui", 20000, 50000, 6000, usd=0.31, ms=300000)
    d.move("dark-mode", "codex-ui", "review", note="tokens for both themes")
    d.msg("opus-sync", "codex-ui", "the editor reads colors from theme tokens now", task="sync-merge")
    return d


def weather_api(root):
    d = Demo(root, "weather-api", "service")
    d.team("api", 2, "Forecast API")
    d.agent("gemini-cache", "api", "gemini-cli", "gemini-3-pro")
    d.card("cache-ttl", "Cache forecasts for ten minutes", "api", "cache")
    d.card("rate-limit", "Per-key rate limits", "api", "middleware")
    d.claim("cache-ttl", "gemini-cache")
    d.note("cache-ttl", "gemini-cache", "TTL cache in front of the upstream call")
    return d


def docs_site(root):
    d = Demo(root, "docs-site", "docs")
    d.team("docs", 1, "Public documentation")
    d.agent("haiku-docs", "docs", "claude-code", "claude-haiku-5-5", billing="plan")
    d.card("api-ref", "Publish the API reference", "docs", "reference")
    d.card("changelog", "Changelog page", "docs", "changelog")
    d.claim("api-ref", "haiku-docs")
    d.done("api-ref")
    return d


def build(root, showcase=False):
    root = Path(root)
    return {
        "acme-shop": acme_shop(root, showcase),
        "pocket-notes": pocket_notes(root),
        "weather-api": weather_api(root),
        "docs-site": docs_site(root),
    }


def bulk_board(root, name, n_cards, agents=8):
    d = Demo(root, name, "repo")
    d.team("core", 50)
    for i in range(agents):
        d.agent(
            f"agent-{i:02d}",
            "core",
            "claude-code" if i % 2 else "codex-cli",
            "claude-sonnet-5-5" if i % 2 else "gpt-6-astra",
        )
    cards_dir = Path(d.board.dir) / "cards"
    lanes = ("todo", "todo", "doing", "review", "done", "done", "blocked")
    for i in range(n_cards):
        lane = lanes[i % len(lanes)]
        owner = "" if lane == "todo" else f"agent-{i % agents:02d}"
        card = {
            "id": f"c{i:05d}",
            "title": f"Generated quest {i}",
            "lane": lane,
            "owner": owner,
            "team": "core",
            "ticket": "",
            "scope": "",
            "output": "",
            "verify": "",
            "verify_cmd": "",
            "locks": [f"repo/m{i}"],
            "depends": [],
            "reviewers": [],
            "fence": 0,
            "rev": 0,
            "released": False,
            "created": "2026-10-22T10:00:00Z",
            "updated": "2026-10-22T10:00:00Z",
            "notes": [{"ts": "2026-10-22T10:00:00Z", "by": owner, "model": "", "text": f"note {i}"}],
            "reviews": [],
            "verifications": [],
            "costs": [
                {
                    "ts": "2026-10-22T10:00:00Z",
                    "by": owner or "agent-00",
                    "model": "claude-sonnet-5-5",
                    "provider": "anthropic",
                    "billing": "api",
                    "usage": {"input": 100, "cached_input": 0, "cache_write": 0, "output": 50, "reasoning_output": 0},
                    "tokens": 150,
                    "usd": 0.0007,
                    "usd_equiv": 0.0007,
                    "usd_source": "priced",
                    "duration_ms": 1000,
                }
            ]
            if owner
            else [],
        }
        with open(cards_dir / f"{card['id']}.json", "w", encoding="utf-8") as f:
            json.dump({"schema": 2, "card": card}, f)
    os.utime(cards_dir)
    return d


FLEET_AREAS = (
    "network",
    "cluster",
    "registry",
    "secrets",
    "ingress",
    "billing",
    "storage",
    "search",
    "mail",
    "backups",
    "audit",
    "dns",
)


def _age_agents(d, names, stripped):
    from raidlock.core import clock

    path = Path(d.board.dir) / "agents.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    old = clock.later(-3 * 86400)
    for name in names:
        data["agents"][name].update({"joined": old, "last_seen": old})
    for name in stripped:
        data["agents"][name].update({"model": "", "runtime": "", "provider": "", "model_source": "unknown"})
    path.write_text(json.dumps(data), encoding="utf-8")
    with d.board.index.write() as conn:
        conn.executemany(
            "UPDATE agents SET last_seen = ? WHERE ws = ? AND name = ?", [(old, d.board.uid, n) for n in names]
        )


def fleet_board(root, name="orbit-ops"):
    from raidlock.core import clock

    d = Demo(root, name, "infra")
    make_repo(d.root / "gitops", f"git@github.com:demo/{name}-gitops.git")
    d.board.set_setting(d.lead, "require_review.count", 1)
    for team, mission in (
        ("platform", "Clusters, networking and the registry"),
        ("gitops", "Deploy pipelines and drift checks"),
        ("apps", "Application services"),
        ("security", "Secrets, audit and access"),
    ):
        d.team(team, 6, mission)
    teams = ("platform", "gitops", "apps", "security")
    idle = []
    for i in range(60):
        who = f"worker-{FLEET_AREAS[i % len(FLEET_AREAS)]}-{i:02d}"
        d.agent(
            who, teams[i % 4], "claude-code" if i % 3 else "codex-cli", "claude-sonnet-5-5" if i % 3 else "gpt-6-astra"
        )
        idle.append(who)
    d.agent("review-bot", "", "claude-code", "claude-haiku-5-5", role="reviewer")
    for i in range(70):
        area = FLEET_AREAS[i % len(FLEET_AREAS)]
        cid = f"done-{area}-{i:02d}"
        who = idle[i % len(idle)]
        d.card(cid, f"Tune {area} settings, batch {i}", teams[(i % len(idle)) % 4], f"{area}/batch{i}")
        d.claim(cid, who)
        d.move(cid, who, "review", note="ready")
        d.review(cid, "review-bot", "approve", "ok")
        d.done(cid)
    d.card("wip-expired", "Retire the old load balancer", teams[0], "lb/legacy")
    d.board.claim("wip-expired", d.board.caller(agent=idle[0], token=d.tokens[idle[0]], env={}), ttl_minutes=1)
    active = {
        "ana-network": ("platform", "claude-code", "claude-opus-5-5", "plan"),
        "ben-gitops": ("gitops", "codex-cli", "gpt-6-astra", "api"),
        "cleo-apps": ("apps", "claude-code", "claude-sonnet-5-5", "plan"),
        "dev-security": ("security", "codex-cli", "gpt-6-astra", "api"),
        "eli-platform": ("platform", "claude-code", "claude-sonnet-5-5", "plan"),
    }
    for who, (team, runtime, model, billing) in active.items():
        d.agent(who, team, runtime, model, billing=billing)
    for i, (who, title) in enumerate(
        (
            ("ana-network", "Private endpoints for the registry"),
            ("ben-gitops", "Drift alert when a sync is skipped"),
            ("cleo-apps", "Health checks for the checkout service"),
            ("eli-platform", "Node pool for batch jobs"),
        )
    ):
        cid = f"wip-{i}"
        d.card(cid, title, active[who][0], f"wip/{i}", repo="gitops" if i % 2 else None)
        d.claim(cid, who)
        d.note(cid, who, "halfway: config written, tests next")
        d.cost(cid, who, 40000 + i * 1000, 120000, 9000, usd=0.42 if active[who][3] == "api" else None, ms=600000)
    d.card("lead-own", "Rotate the deploy key", "", "keys/deploy")
    d.board.claim("lead-own", d.lead)
    for i in range(10):
        area = FLEET_AREAS[i]
        cid = f"rev-{area}"
        who = list(active)[i % len(active)]
        d.card(cid, f"Harden {area} defaults", active[who][0], f"rev/{area}")
        d.claim(cid, who)
        d.note(cid, who, f"{area} defaults hardened; plan attached")
        d.move(cid, who, "review", note="ready for review")
        if i < 6:
            d.review(cid, "review-bot", "approve", "checked the plan")
    asks = (
        "lead: need a decision on which account owns the shared bucket",
        "waiting on the lead to approve the new IAM boundary",
        "lead please confirm we can drop the legacy DNS zone",
        "blocked until the vendor sends the API key",
        "upstream chart has a CVE; waiting for the patch release",
        "lead: pick one of the two runbook layouts in the notes",
        "needs a network change outside my locks",
        "waiting on the security review from another team",
    )
    for i, text in enumerate(asks):
        cid = f"blk-{FLEET_AREAS[i]}"
        who = list(active)[i % len(active)]
        d.card(cid, f"Move {FLEET_AREAS[i]} to the new account", active[who][0], f"blk/{FLEET_AREAS[i]}")
        d.claim(cid, who)
        d.move(cid, who, "blocked", note=text)
    for i in range(12):
        area = FLEET_AREAS[i]
        d.card(f"todo-{area}", f"Document the {area} runbook", teams[i % 4], f"docs/{area}")
    _age_agents(d, idle[1:], set(idle[::3]))
    with d.board.index.write() as conn:
        conn.execute(
            "UPDATE leases SET expires = ? WHERE ws = ? AND card = ?", (clock.later(-120), d.board.uid, "wip-expired")
        )
    return d


def empty_board(root, name="fresh-start"):
    return Demo(root, name, "app")
