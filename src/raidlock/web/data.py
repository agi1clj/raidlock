import hashlib
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from raidlock import party, updates
from raidlock.core import Board, RaidlockError, clock, db, redact, registry
from raidlock.core import models as core_models
from raidlock.core.db import Index

LANES = ("todo", "doing", "review", "blocked", "done", "cancelled")
CHAT_KINDS = ("msg", "relay", "handback", "note", "review", "verify")
SPEND_BY = ("provider", "model", "agent", "workspace", "day")
NOTE_TAIL = 3
HIDDEN = (
    "fence",
    "token",
    "cap",
    "lead_secret",
    "lead_secret_file",
    "lead_fence",
    "checkout",
    "raidlock_token",
    "raidlock_cap",
    "raidlock_lead_secret",
    "raidlock_lead_secret_file",
)
CHAT_TAIL = 20
VIEWS = {
    "raidmap": "raidlock.web.raidmap",
    "replay": "raidlock.web.replay",
    "graph": "raidlock.web.graph",
    "frames": "raidlock.frames",
    "achievements": "raidlock.achievements",
    "bosses": "raidlock.party",
}
VIEW_QUERY = ("at", "marks")
CLOCKED = ("frames", "raidmap")


def etag_of(*parts):
    raw = json.dumps(parts, sort_keys=True, default=str).encode()
    return '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'


def _scrub(value, roots):
    if isinstance(value, str):
        for root, alias in roots:
            if root and root in value:
                value = value.replace(root, alias)
        return value
    if isinstance(value, list):
        return [_scrub(v, roots) for v in value]
    if isinstance(value, dict):
        return {_scrub(k, roots): _scrub(v, roots) for k, v in value.items()}
    return value


def private_roots(home):
    pairs = []
    for root, alias in ((home, "~raid"), (os.path.expanduser("~"), "~"), (tempfile.gettempdir(), "~tmp")):
        pairs.extend(((root, alias), (os.path.realpath(root), alias)))
    return sorted(pairs, key=lambda p: -len(p[0]))


def scrub_private(value, home):
    return _scrub(value, private_roots(home))


def _roots(board):
    pairs = [
        (board.dir, ".board"),
        (os.path.realpath(board.dir), ".board"),
        (board.workspace, board.name),
        (os.path.realpath(board.workspace), board.name),
    ] + private_roots(board.home)
    return sorted(pairs, key=lambda p: -len(p[0]))


def _text_of(record):
    return "\n".join(p.get("text", "") for p in record.get("parts", []) if p.get("kind") == "text")


def slim_record(record):
    sender = record.get("from") or {}
    data = next((p.get("data") for p in record.get("parts", []) if p.get("kind") == "data"), None)
    return {
        "seq": record.get("seq", 0),
        "id": record.get("messageId", ""),
        "ts": record.get("ts", ""),
        "kind": record.get("kind", ""),
        "task": record.get("taskId", ""),
        "to": record.get("to", ""),
        "from": {
            "agent": sender.get("agent", ""),
            "provider": sender.get("provider", ""),
            "runtime": sender.get("runtime", ""),
            "model": sender.get("model", ""),
        },
        "text": _text_of(record),
        "data": {k: v for k, v in data.items() if k not in HIDDEN} if isinstance(data, dict) else None,
        "artifacts": [
            {"name": a.get("name", ""), "kind": (a.get("metadata") or {}).get("kind", ""), "text": _text_of(a)}
            for a in record.get("artifacts", [])
        ],
    }


def _slim_lease(lease):
    if not lease:
        return None
    return {
        "agent": lease.get("agent", ""),
        "paths": lease.get("paths", []),
        "expires": lease.get("expires"),
        "renewed": lease.get("renewed", ""),
        "live": lease.get("live", True),
        "branch": lease.get("branch", ""),
    }


def repos_of(locks):
    out = []
    for lock in locks or []:
        head = str(lock).strip().strip("/").split("/")[0]
        if head and head not in out:
            out.append(head)
    return out


def repo_counts(cards):
    counts = {}
    for c in cards:
        for r in c.get("repos", []):
            counts[r] = counts.get(r, 0) + 1
    return [{"name": k, "cards": counts[k]} for k in sorted(counts)]


def spend_today(spend):
    today = clock.iso()[:10]
    row = next((r for r in (spend or {}).get("by_day", []) if str(r.get("key", ""))[:10] == today), None) or {}
    return {
        "day": today,
        "usd": row.get("usd", 0),
        "usd_equiv": row.get("usd_equiv", 0),
        "tokens": row.get("tokens", 0),
    }


def slim_pr(pr):
    if not isinstance(pr, dict) or not pr.get("number"):
        return None
    checks = pr.get("checks") or {}
    return {
        "number": pr.get("number"),
        "url": pr.get("url", ""),
        "state": pr.get("state", ""),
        "checks": {k: checks.get(k, 0) for k in ("passed", "failed", "pending", "total")}
        | {"state": checks.get("state", "none")},
        "held": pr.get("held", ""),
    }


def slim_card(card, known=None):
    notes = card.get("notes", [])
    heads = repos_of(card.get("locks", [])) + ([card["repo"]] if card.get("repo") else [])
    return {
        "id": card["id"],
        "repos": sorted({r for r in heads if not known or r in known}),
        "title": card.get("title", ""),
        "lane": card.get("lane", ""),
        "owner": card.get("owner", ""),
        "team": card.get("team", ""),
        "ticket": card.get("ticket", ""),
        "scope": card.get("scope", ""),
        "allow_protected": card.get("allow_protected", []),
        "lock_exclusions": card.get("lock_exclusions", []),
        "cancel_reason": card.get("cancel_reason", ""),
        "evidence": card.get("evidence", ""),
        "verify": card.get("verify", ""),
        "locks": card.get("locks", []),
        "depends": card.get("depends", []),
        "reviewers": card.get("reviewers", []),
        "updated": card.get("updated", ""),
        "created": card.get("created", ""),
        "a2a_state": card.get("a2a_state", ""),
        "approvals": card.get("approvals", []),
        "cost": card.get("cost", {}),
        "pr": slim_pr(card.get("pr")),
        "lease": _slim_lease(card.get("lease")),
        "notes_count": len(notes),
        "notes": notes[-NOTE_TAIL:],
    }


def agent_reputation(board):
    """Use the optional public scorer without making it a UI startup dependency."""
    try:
        from raidlock.reputation import reputation
    except ImportError:
        return {}
    try:
        result = reputation(board, by="agent")
        return {row["key"]: row for row in result.get("rows", [])}
    except (RaidlockError, KeyError, TypeError, ValueError):
        return {}


def model_reputation(board, workspace, names):
    try:
        from raidlock.reputation import reputation
    except ImportError:
        return []
    try:
        result = reputation(board, by="model")
        return [
            {**row, "workspace": workspace, "label": names.get(row["key"], row["key"])}
            for row in result.get("rows", [])
        ]
    except (RaidlockError, KeyError, TypeError, ValueError):
        return []


def score_rows(cards, agents):
    rows = []
    for card in cards:
        if card.get("lane") == "cancelled":
            continue
        owner = card.get("owner", "")
        if not owner:
            continue
        agent = agents.get(owner, {})
        verdicts = [r.get("verdict") for r in card.get("reviews", []) if r.get("verdict") in ("approve", "changes")]
        costs = card.get("costs", [])
        provider = agent.get("provider", "")
        if provider in ("", "local"):
            provider = next((r.get("provider") for r in reversed(costs) if r.get("provider")), provider)
        rows.append(
            {
                "provider": provider or "unknown",
                "model": agent.get("model", "") or "unknown",
                "done": card.get("lane") == "done" and not card.get("evidence"),
                "approve": verdicts.count("approve"),
                "changes": verdicts.count("changes"),
                "usd": round(sum(r.get("usd") or 0 for r in costs), 6),
                "usd_equiv": round(sum(r.get("usd_equiv") or 0 for r in costs), 6),
                "tokens": sum(r.get("tokens", 0) for r in costs),
                "duration_ms": sum(r.get("duration_ms", 0) for r in costs),
            }
        )
    return rows


def scorecard(rows, names):
    out = {}
    for r in rows:
        k = (r["provider"], r["model"])
        g = out.setdefault(
            k,
            {
                "provider": r["provider"],
                "model": r["model"],
                "label": names.get(r["model"], r["model"]),
                "quests": 0,
                "done": 0,
                "approve": 0,
                "changes": 0,
                "usd": 0.0,
                "usd_equiv": 0.0,
                "tokens": 0,
                "duration_ms": 0,
            },
        )
        g["quests"] += 1
        g["done"] += 1 if r["done"] else 0
        for f in ("approve", "changes", "tokens", "duration_ms"):
            g[f] += r[f]
        for f in ("usd", "usd_equiv"):
            g[f] = round(g[f] + r[f], 6)
    for g in out.values():
        reviewed = g["approve"] + g["changes"]
        g["pass_rate"] = round(g["approve"] / reviewed, 4) if reviewed else None
    return sorted(out.values(), key=lambda g: (-g["done"], -g["quests"], g["provider"], g["model"]))


def treasury(board, names, agents, everywhere=False):
    out = {}
    billing = board.spend(by="billing", everywhere=everywhere)
    out["total"] = billing["total"]
    modes = {r["key"]: r for r in billing["rows"]}
    zero = {"usd": 0.0, "usd_equiv": 0.0, "tokens": 0, "runs": 0}
    out["billing"] = {m: {k: (modes.get(m) or zero).get(k, 0) for k in zero} for m in ("api", "plan", "unknown")}
    out["api_equivalent_usd"] = round(out["billing"]["plan"]["usd_equiv"] + out["billing"]["unknown"]["usd_equiv"], 6)
    for by in SPEND_BY:
        rows = board.spend(by=by, everywhere=everywhere)["rows"]
        for r in rows:
            r["key"] = r["key"] if r["key"] not in (None, "") else "unknown"
            r["label"] = names.get(r["key"], r["key"]) if by == "model" else r["key"]
            if by == "agent":
                r["billing"] = (agents.get(r["key"]) or {}).get("billing", "unknown")
        out["by_" + by] = sorted(rows, key=lambda r: r["key"]) if by == "day" else rows
    return out


def needs_count(payload):
    items = [
        x for x in (payload.get("approvals") or {}).get("items", []) if x.get("kind") in ("close", "apply", "duel")
    ]
    seen = {x.get("card") for x in items if x.get("card")}
    extra = {c["id"] for c in payload["cards"] if c["lane"] in ("blocked", "review")} - seen
    return len(items) + len(extra)


def summary_of(name, payload, chat):
    titles = {c["id"]: c["title"] for c in payload["cards"]}
    rows = []
    for agent, a in payload["agents"].items():
        rows.append(
            {
                "name": agent,
                "status": a.get("status", "idle"),
                "label": a.get("label", ""),
                "card": a.get("card", ""),
                "title": titles.get(a.get("card", ""), ""),
                "display": a.get("display", agent),
                "provider": a.get("provider", ""),
                "runtime": a.get("runtime", ""),
                "model": a.get("model", ""),
                "billing": a.get("billing", "unknown"),
                "heartbeat": a.get("heartbeat", ""),
            }
        )
    claim = payload["claimable"]
    return {
        "name": name,
        "missing": False,
        "counts": payload["counts"],
        "claimable": sum(1 for v in claim.values() if v["ok"]),
        "open": len(claim),
        "cost": payload["costs"]["workspace"],
        "agents": rows,
        "teams": len(payload["teams"]),
        "last": payload["last"],
        "repos": payload["repos"],
        "today": payload["today"],
        "review": needs_count(payload),
        "chat": [dict(r, workspace=name) for r in chat],
        "warnings": payload["warnings"],
    }


class Workspace:
    def __init__(self, name, board_dir, home):
        self.name = name
        self.dir = board_dir
        self.home = home
        self.lock = threading.Lock()
        self.key = None
        self.payload = None
        self._board = None
        self._stamp = None

    def board(self, fresh=False):
        meta = os.path.join(self.dir, "board.json")
        try:
            stamp = os.stat(meta).st_mtime_ns
        except OSError:
            stamp = None
        if fresh or self._board is None or stamp != self._stamp:
            self._board = Board.open(self.dir, home=self.home)
            self._stamp = stamp
        return self._board

    def key_now(self):
        with self.lock:
            return self._key()

    def _key(self):
        return etag_of(self.name, self.board().version())

    def refresh(self):
        with self.lock:
            key = self._key()
            if key != self.key:
                self.payload = self._build(self.board())
                self.key = key
            return self.key, self.payload

    def roots(self):
        return _roots(self.board())

    def _build(self, board):
        snap = board.snapshot(transcript_limit=1, messages_limit=50)
        names = core_models.display_map(self.home)
        raw_cards = board.cards()
        agents = {}
        trust = agent_reputation(board)
        for name, a in snap["agents"].items():
            agents[name] = {k: v for k, v in a.items() if k not in ("session", "token")}
            agents[name]["billing"] = a.get("billing") or "unknown"
            agents[name]["reputation"] = trust.get(name, {}).get("score")
            agents[name]["reputation_inputs"] = trust.get(name, {}).get("inputs", {})
        try:
            known = set(board._resolver().repos)
        except RaidlockError:
            known = set()
        cards = [slim_card(c, known) for c in snap["cards"]]
        spend = treasury(board, names, agents)
        tail = [slim_record(r) for r in board.transcript.read(limit=60)]
        chat = [r for r in tail if r["kind"] in CHAT_KINDS][-CHAT_TAIL:]
        cursor = tail[-1]["seq"] if tail else 0
        last = max([c.get("updated", "") for c in cards] + [r["ts"] for r in tail[-1:]] + [""])
        payload = {
            "workspace": self.name,
            "lead": snap["lead"],
            "now": snap["now"],
            "warnings": snap["warnings"],
            "version": snap.get("version"),
            "settings": {
                "require_review": snap["settings"].get("require_review"),
                "ttl_minutes": snap["settings"].get("ttl_minutes"),
            },
            "cards": cards,
            "teams": snap["teams"],
            "agents": agents,
            "claimable": snap["claimable"],
            "review_queue": snap["review_queue"],
            "inbox": snap["inbox"],
            "messages": snap["messages"],
            "costs": snap["costs"],
            "model_names": names,
            "scorecard": scorecard(score_rows(raw_cards, agents), names),
            "model_reputation": model_reputation(board, self.name, names),
            "treasury": spend,
            "forecast": forecast_of(board),
            "today": spend_today(spend),
            "repos": repo_counts(cards),
            "bosses_slain": party.slain_bosses(raw_cards),
            "streak": party.streak(raw_cards),
            "cursor": cursor,
            "last": last,
            "counts": {lane: sum(1 for c in cards if c["lane"] == lane) for lane in LANES},
            "approvals": _approvals(board),
            "lead_src": _lead_src(board),
            "_score_rows": score_rows(raw_cards, agents),
            "_chat": chat,
        }
        return _scrub(payload, _roots(board))


def _approvals(board):
    try:
        from raidlock.approvals import collect, web_lead_src
    except ImportError:
        return {"items": [], "counts": {}, "total": 0}
    try:
        return collect(board, secret=web_lead_src(board))
    except (RaidlockError, OSError, ValueError):
        return {"items": [], "counts": {}, "total": 0, "error": "approvals unavailable"}


def _lead_src(board):
    try:
        from raidlock.approvals import web_lead_src

        return web_lead_src(board)
    except (ImportError, RaidlockError, OSError, ValueError):
        return '"$LEAD_SECRET_FILE"'


def public(payload):
    return redacted({k: v for k, v in payload.items() if not k.startswith("_")})


class Catalog:
    def __init__(self, single=None, home=None, extra=None):
        self.single = os.path.realpath(single) if single else None
        self.extra = os.path.realpath(extra) if extra else None
        self.home = home or registry.home()
        self.lock = threading.Lock()
        self.cache = {}
        self._limits_lock = threading.Lock()
        self._limits_at = float("-inf")
        self._limits = {"providers": []}

    def provider_limits(self):
        """Sample quotas at most twice a minute, independently of board writes."""
        with self._limits_lock:
            now = time.monotonic()
            if now - self._limits_at >= 30:
                try:
                    from raidlock.limits import collect
                except ImportError:
                    value = {"providers": []}
                else:
                    try:
                        value = collect()
                        json.dumps(value, allow_nan=False)
                    except (OSError, ValueError, TypeError, OverflowError, AttributeError):
                        value = {"providers": []}
                self._limits = redacted(value)
                self._limits_at = now
            return self._limits

    def mode(self):
        return "single" if self.single else "all"

    def _registered(self):
        try:
            return [(w["name"], w["board"]) for w in registry.workspaces(Index(self.home))]
        except Exception:
            return []

    def entries(self):
        if self.single:
            return [(self._name(self.single), self.single)]
        found = [(n, d) for n, d in self._registered() if os.path.isdir(d)]
        if self.extra and os.path.isdir(self.extra) and all(os.path.realpath(d) != self.extra for _, d in found):
            taken = {n for n, _ in found}
            stem = self._name(self.extra)
            name, i = stem, 2
            while name in taken:
                name, i = f"{stem}-{i}", i + 1
            found = sorted(found + [(name, self.extra)])
        return found

    def _name(self, board_dir):
        try:
            return Board.open(board_dir, home=self.home).name
        except RaidlockError:
            return os.path.basename(os.path.dirname(board_dir)) or "board"

    def workspaces(self):
        out = []
        with self.lock:
            for name, board_dir in self.entries():
                ws = self.cache.get(name)
                if ws is None or ws.dir != board_dir:
                    ws = self.cache[name] = Workspace(name, board_dir, self.home)
                out.append(ws)
        return out

    def get(self, name):
        return next((w for w in self.workspaces() if w.name == name), None)

    def overview_key(self):
        parts = []
        for ws in self.workspaces():
            try:
                parts.append((ws.name, ws.key_now()))
            except (RaidlockError, OSError, ValueError):
                parts.append((ws.name, None))
        return etag_of("overview", self.mode(), parts, self.provider_limits())

    def overview(self):
        projects, srows, names, agents, first = [], [], {}, {}, None
        reputations, forecasts = [], []
        spaces = self.workspaces()

        def load(ws):
            try:
                with db.released():
                    return ws.refresh()[1]
            except (RaidlockError, OSError, ValueError) as e:
                return e

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(spaces)))) as pool:
            results = list(pool.map(load, spaces))
        for ws, payload in zip(spaces, results, strict=True):
            if isinstance(payload, Exception):
                e = payload
                projects.append(
                    {
                        "name": ws.name,
                        "missing": True,
                        "error": str(e).split(":")[0][:80],
                        "counts": {},
                        "agents": [],
                        "chat": [],
                    }
                )
                continue
            first = first or ws
            projects.append(summary_of(ws.name, payload, payload["_chat"]))
            srows += payload["_score_rows"]
            names.update(payload["model_names"])
            agents.update(payload["agents"])
            reputations.extend(payload.get("model_reputation", []))
            if payload.get("forecast"):
                forecasts.append(payload["forecast"])
        spend = None
        if first is not None:
            with first.lock:
                board = first.board()
                if self.single:
                    spend = treasury(board, names, agents)
                else:
                    spend = _scrub(treasury(board, names, agents, everywhere=True), [(os.path.expanduser("~"), "~")])
        return {
            "mode": self.mode(),
            "projects": projects,
            "treasury": spend,
            "forecast": _combined(forecasts),
            "scorecard": scorecard(srows, names),
            "model_names": names,
            "limits": self.provider_limits(),
            "update": updates.available(),
            "model_reputation": reputations,
        }


def view_clock(kind):
    return int(time.time() // 30) if kind in CLOCKED else 0


def view(ws, kind, query):
    import importlib

    module = importlib.import_module(VIEWS[kind])
    with ws.lock:
        board = ws.board()
        return _scrub(module.payload(board, query), _roots(board))


def forecast_of(board):
    try:
        from raidlock.treasury import forecast
    except ImportError:
        return None
    try:
        return forecast(board)
    except (RaidlockError, KeyError, TypeError, ValueError):
        return None


def _combined(forecasts):
    if not forecasts:
        return None
    from raidlock.treasury import combine

    return combine(forecasts)


def transcript_page(ws, since, limit, task):
    with ws.lock:
        return _transcript_page(ws.board(), since, limit, task)


def _transcript_page(board, since, limit, task):
    if since:
        rows = board.transcript.read(task=task or None, since=since, limit=limit)
    else:
        rows = board.transcript.read(task=task or None, limit=limit)
    recs = [slim_record(r) for r in rows]
    cursor = recs[-1]["seq"] if recs else since
    if not since and not recs:
        cursor = 0
    return _scrub({"records": recs, "cursor": cursor, "more": bool(since) and len(recs) >= limit}, _roots(board))


def card_detail(ws, card_id):
    with ws.lock:
        return _card_detail(ws.board(), card_id)


def _card_detail(board, card_id):
    card = board.card_view(board.card(card_id))
    for k in ("fence", "verify_cmd"):
        card.pop(k, None)
    card["lease"] = _slim_lease(card.get("lease"))
    card["claim"] = board.claimable().get(card_id)
    card["log"] = [slim_record(r) for r in board.transcript.read(task=card_id, limit=200)]
    return _scrub(card, _roots(board))


def redacted(value):
    if isinstance(value, str):
        value = re.sub(r"(?i)\bRAIDLOCK_CAP\s*[:=]\s*[^\s'\",;]+", "RAIDLOCK_CAP=[REDACTED]", value)
        return redact(value)
    if isinstance(value, list):
        return [redacted(v) for v in value]
    if isinstance(value, dict):
        return {k: redacted(v) for k, v in value.items() if k.lower() not in HIDDEN}
    return value
