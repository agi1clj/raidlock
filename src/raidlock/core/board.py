import copy
import inspect
import json
import math
import os
import re
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlsplit

from . import advisories, clock, db, migrate, model, models, registry, repos, store
from . import billing as billing_mod
from . import brief as B
from . import cost as C
from . import identity as I
from . import lead as LD
from . import leases as L
from . import messages as M
from . import status as S
from . import transcript as T
from .db import Index
from .errors import Denied, IdentityError, Precondition, StaleLead, Timeout
from .hints import lead_cmd
from .lock import FileLock
from .paths import parse_list

MODEL_PROVIDERS = ("anthropic", "openai", "google")
DEFAULT_SETTINGS = {
    "auth": "token",
    "require_review": {"count": 0, "other_provider": False},
    "ttl_minutes": 45,
    "hold_minutes": 60,
    "brief_max_tokens": 1200,
    "lead_store": "file",
    "require_verify": False,
    "require_card_for_lead_edits": True,
    "board_first_subagents": True,
}
SETTING_KEYS = (
    "auth",
    "require_review",
    "ttl_minutes",
    "brief_max_tokens",
    "lead_store",
    "require_verify",
    "hold_minutes",
    "require_card_for_lead_edits",
    "board_first_subagents",
)
LEAD_CARD_RULE = (
    "the lead holds no card covering this path (require_card_for_lead_edits is on). "
    "Add a card that locks it (raid --lead-secret FILE new TITLE --locks REPO/PATH), claim it "
    "(raid --lead-secret FILE claim CARD), or: "
    "raid --lead-secret FILE config set require_card_for_lead_edits off"
)
ENFORCE_KEYS = ("require_card_for_lead_edits", "board_first_subagents")
PR_SETTING_KEYS = ("pr.redact", "pr.terms", "pr.base", "pr.remote")
TAIL_LINES = 40


def find_board_dir(path=None, env=None):
    """Resolve a `.board` directory from an explicit path, RAIDLOCK_BOARD, BOARD_DIR, or by walking up from cwd."""
    env = os.environ if env is None else env
    explicit = path or env.get("RAIDLOCK_BOARD") or env.get("BOARD_DIR")
    if explicit:
        explicit = os.path.abspath(os.path.expanduser(explicit))
        if os.path.basename(explicit.rstrip(os.sep)) == ".board" and os.path.isdir(explicit):
            return explicit
        if os.path.isdir(os.path.join(explicit, ".board")):
            return os.path.join(explicit, ".board")
        raise Precondition(f"no board at {explicit}", board=explicit, hint=f"raidlock init {explicit}")
    cur = os.getcwd()
    while True:
        if os.path.isdir(os.path.join(cur, ".board")):
            return os.path.join(cur, ".board")
        parent = os.path.dirname(cur)
        if parent == cur:
            raise Precondition(
                "no board found here or in any parent folder", hint="raidlock up   (or pass --board PATH)"
            )
        cur = parent


def normalize_review_setting(value):
    if isinstance(value, int):
        return {"count": value, "other_provider": False}
    value = dict(value or {})
    return {"count": int(value.get("count", 0)), "other_provider": bool(value.get("other_provider", False))}


def serialized_write(fn):
    """Keep credential validation and its mutation in the same board-lock interval."""
    signature = inspect.signature(fn)

    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        caller = signature.bind(self, *args, **kwargs).arguments.get("caller")
        with self.lock():
            previous = getattr(self, "_validated_lead", None)
            try:
                if caller and caller.is_lead:
                    self._lead_write(caller)
                    self._validated_lead = caller
                result = fn(self, *args, **kwargs)
                if caller and caller.is_lead:
                    self._record(
                        "status",
                        caller.name,
                        f"lead action: {fn.__name__}",
                        data={
                            "event": "lead_action",
                            "action": fn.__name__,
                            "session": caller.session,
                            "lead_fence": caller.lead_fence,
                        },
                    )
                return result
            finally:
                self._validated_lead = previous

    return wrapped


class Board:
    """One workspace board (`<workspace>/.board`). Every mutation takes the board lock; lease writes also take the
    global lock."""

    def __init__(self, board_dir, env=None, home=None):
        migrate.reject_legacy(board_dir)
        self.dir = os.path.abspath(board_dir)
        self.workspace = os.path.dirname(self.dir)
        self.env = dict(os.environ if env is None else env)
        self.home = home or registry.home()
        self.index = Index(self.home)
        self._resolver_cache = None
        self._cache = None
        self._lead_session = None
        self.proc_table = None
        self.keychain = None
        self.meta = store.read_json(self._p("board.json"))
        if self.meta is None:
            raise Precondition(f"{self.dir} is not a raidlock board; run `raidlock init`", board=self.dir)
        store.check_schema(self.meta, self._p("board.json"))
        self.transcript = T.Transcript(self.dir, self.index, self.uid)
        self._sync_mirror()
        with self.lock():
            for lease in self.index.leases(self.uid).values():
                if lease["expires"] is None:
                    lease["keys"] = [{**key, "advisory": True} for key in lease["keys"]]
                    lease["expires"] = clock.later(
                        float(self.settings["hold_minutes"]) * 60,
                        start=clock.parse(lease["renewed"]),
                    )
                    self.index.put_lease(self.uid, lease)

    def _sync_mirror(self):
        folder = self._p("cards")
        n = sum(1 for f in os.listdir(folder) if f.endswith(".json")) if os.path.isdir(folder) else 0
        if n != self.index.card_count(self.uid):
            with self.lock():
                cards = self.cards()
                self.index.put_cards(self.uid, cards)
                if any(c.get("costs") for c in cards) and not self.index.cost_count(self.uid):
                    self.index.rebuild_costs(self.uid, cards)

    @classmethod
    def open(cls, path=None, env=None, home=None):
        """Open the board at `path` (workspace or .board dir), or find it from RAIDLOCK_BOARD / cwd."""
        return cls(find_board_dir(path, env), env=env, home=home)

    @classmethod
    def init(
        cls, workspace=".", lead=None, runtime="human", provider=None, model_id=None, auth=True, env=None, home=None
    ):
        """Create a board in `workspace` (idempotent). Returns (board, lead_secret or None when the board existed)."""
        workspace = os.path.abspath(workspace)
        board_dir = os.path.join(workspace, ".board")
        migrate.reject_legacy(board_dir)
        os.makedirs(os.path.join(board_dir, "cards"), exist_ok=True)
        if not os.path.exists(os.path.join(board_dir, ".gitignore")):
            store.atomic_write_text(os.path.join(board_dir, ".gitignore"), "*\n")
        created = False
        initial_session = None
        with FileLock(os.path.join(board_dir, ".lock")):
            if not os.path.exists(os.path.join(board_dir, "board.json")) and not migrate.needs_migration(board_dir):
                name = model.agent_name(lead or I.default_name(runtime, "lead", "", set()))
                initial_session = LD.session_of(os.environ if env is None else env)
                if not all(initial_session):
                    raise IdentityError("cannot establish lead lease without a session process anchor")
                settings = dict(DEFAULT_SETTINGS, auth="token" if auth else "off")
                store.write_json(
                    os.path.join(board_dir, "board.json"),
                    {
                        "schema": store.SCHEMA,
                        "uid": uuid.uuid4().hex,
                        "workspace": os.path.basename(workspace),
                        "created": clock.iso(),
                        "lead": name,
                        "settings": settings,
                    },
                )
                created = True
        board = cls(board_dir, env=env, home=home)
        token = None
        if created:
            sid, pid, start = initial_session
            board._enroll(board.lead, "lead", "", runtime, provider, model_id, None, None, None, sid, issue=False)
            with board.lock():
                token = board.leadlease().establish(sid, pid, start)
                board._lead_session = (sid, pid, start)
                board._record(
                    "status",
                    board.lead,
                    f"lead lease established for session {sid} (fence 1)",
                    data={"event": "lead_established", "session": sid, "lead_fence": 1},
                )
        board.register()
        return board, token

    def _p(self, *parts):
        return os.path.join(self.dir, *parts)

    @contextmanager
    def lock(self):
        """The board lock. Reads of agents.json and teams.json are cached while it is held."""
        with FileLock(self._p(".lock")):
            outer = self._cache is None
            if outer:
                self._cache = {}
            try:
                yield self
            finally:
                if outer:
                    self._cache = None

    @property
    def lead(self):
        return self.meta.get("lead", "lead")

    @property
    def uid(self):
        return self.meta["uid"]

    @property
    def name(self):
        return self.meta.get("workspace") or os.path.basename(self.workspace)

    @property
    def settings(self):
        self.meta = store.read_json(self._p("board.json"))
        s = dict(DEFAULT_SETTINGS)
        s.update(self.meta.get("settings", {}))
        s["require_review"] = normalize_review_setting(s.get("require_review"))
        return s

    def ttl_seconds(self, ttl_minutes=None):
        return float(ttl_minutes if ttl_minutes is not None else self.settings["ttl_minutes"]) * 60

    def register(self, refresh=True):
        """Register this board in the global registry; returns the workspace name. With refresh=False it only
        writes when the board is missing from the registry."""
        if not refresh:
            name = self.index.registered_name(self.uid, self.dir)
            if name:
                return name
        return registry.register(self.index, self.uid, self.dir)

    def warnings(self):
        provider = registry.synced_folder(self.workspace)
        return [f"workspace is inside a {provider} synced folder; sync can replay old board files"] if provider else []

    def caller(self, agent=None, token=None, fence=None, env=None, lead_secret=None, cap=None, session=None):
        """Resolve the calling agent from flags and the environment (RAIDLOCK_AGENT_ID, RAIDLOCK_AGENT, RAIDLOCK_TOKEN,
        RAIDLOCK_CAP, RAIDLOCK_FENCE, RAIDLOCK_LEAD_SECRET, RAIDLOCK_LEAD_SECRET_FILE). Lead rights need the presented
        lead secret, from the session that holds the lead lease."""
        env = self.env if env is None else env

        def check(secret):
            sid, pid, start = LD.session_of(env, session, self._table())
            with self.lock():
                fence_value = self._lead_check(secret, sid, pid=pid, start=start)
                if self.leadlease().lease():
                    self.leadlease().touch(secret, sid, pid, start)
            self._lead_session = (sid, pid, start)
            return fence_value, sid

        return I.resolve(
            self.home,
            self.uid,
            self.settings,
            self.agents(),
            self.lead,
            agent=agent,
            token=token,
            fence=fence,
            env=env,
            lead_secret=lead_secret,
            cap=cap,
            lead_check=check,
        )

    def _table(self):
        return (self.proc_table or LD.process_table)()

    def leadlease(self):
        """The board's lead lease (`.board/lead.json`) with its secret store."""
        return LD.Lead(
            self.dir,
            I.token_path(self.home, self.uid, self.lead),
            f"{self.uid}/{self.lead}",
            self.ttl_seconds(),
            self.settings.get("lead_store", "file"),
            keychain=self.keychain,
            table=self._table() if self.proc_table else None,
        )

    def _lead_check(self, secret, sid, any_session=False, pid=None, start=None):
        try:
            return self.leadlease().check(secret, sid, any_session=any_session, pid=pid, start=start)
        except (LD.ForeignLead, StaleLead) as e:
            event = "lead_stale_fence" if isinstance(e, StaleLead) else "lead_foreign_session"
            holder = e.details.get("lead_session", "")
            if isinstance(e, LD.ForeignLead) and sid and holder and holder != sid:
                expires = self.leadlease().lease().get("expires", "")
                e.hint = (
                    f"from the lead session run: {lead_cmd(self, f'lead handover --to {sid}')}; "
                    f"or, once its lease expires ({expires}), run here: {lead_cmd(self, 'lead takeover')}"
                )
            with self.lock():
                self._record(
                    "status",
                    self.lead,
                    f"ALERT {event}: {e.message}",
                    data={
                        "event": event,
                        "alert": True,
                        "session": sid,
                        "lead_session": e.details.get("lead_session", ""),
                        "lead_fence": e.details.get("fence", 0),
                    },
                )
            raise

    def _lead_write(self, caller):
        """Every lead write: re-check the presented secret and its fence against the lease under the board lock,
        renew the lease, and remember the session for the transcript."""
        with self.lock():
            if getattr(self, "_validated_lead", None) is caller:
                return
            sid, pid, start = LD.session_of(self.env, caller.session, self._table())
            checked = self._lead_check(caller.lead_secret, caller.session, pid=pid, start=start)
            if checked != caller.lead_fence:
                raise StaleLead("caller lead fence does not match its secret", fence=caller.lead_fence)
            fence_value, replaced = self.leadlease().touch(caller.lead_secret, sid, pid, start)
            if replaced:
                caller.lead_secret = replaced
                self._record(
                    "status",
                    self.lead,
                    f"lead lease adopted by session {sid} (fence {fence_value})",
                    data={"event": "lead_adopted", "session": sid, "lead_fence": fence_value},
                )
            caller.lead_fence = fence_value
            self._lead_session = (sid, pid, start)

    def _require_lead(self, caller, action):
        I.require_lead(caller, action)
        self._lead_write(caller)

    @contextmanager
    def lead_write(self, caller, action):
        """Authorize extension writes against the current lease and hold the board lock through the mutation."""
        with self.lock():
            self._require_lead(caller, action)
            yield self
            self._record(
                "status",
                caller.name,
                f"lead action: {action}",
                data={
                    "event": "lead_action",
                    "action": action,
                    "session": caller.session,
                    "lead_fence": caller.lead_fence,
                },
            )

    def lead_status(self):
        """The lead lease without its secret: session, fence, expiry, liveness and store."""
        lease = self.leadlease()
        return {"lead": self.lead, **lease.summary(), "secret_file": I.token_path(self.home, self.uid, self.lead)}

    def lead_session_resumed(self, session):
        """The session the lease names started again (resume, or the next headless turn) after its lease lapsed:
        re-arm it as a pending lease, as a handover would, so its process binds on the next authenticated use. Nobody
        took over in between, or the lease would name another session. A new session id under the lease's own host
        process (the host renamed its session, as /clear does) takes the lease over in place and keeps the process
        binding."""
        with self.lock():
            lead = self.leadlease()
            lease = lead.lease()
            if not session or not lead.expired(lease) and lease.get("session") == session:
                return False
            if lease.get("session") == session:
                lease.update(pid=None, pid_start=None)
            elif LD.under_lead_host(lease, self._table()):
                lease.update(session=session)
            else:
                return False
            lease.update(renewed=clock.iso(), expires=clock.later(lead.ttl_s))
            lead._save(lease)
            self._record(
                "status",
                self.lead,
                f"lead session {session} is back; its lead lease is live again",
                data={"event": "lead_rearmed", "session": session, "lead_fence": lease.get("fence", 0)},
            )
            return True

    def lead_session_ended(self, session):
        """The lead's host session ended: expire its lease now, so the next session can take over without waiting out
        the TTL. Only the session the lease names releases it, and only from inside the lease's own process tree (the
        host's SessionEnd hook), so another session that learned the id cannot. A pending handover keeps its TTL. The
        secret and fence stay until a takeover rotates them."""
        with self.lock():
            lead = self.leadlease()
            lease = lead.lease()
            if not session or lease.get("session") != session or not lease.get("pid") or lead.expired(lease):
                return False
            anchor = (lease["pid"], lease.get("pid_start"))
            if not any((pid, start) == anchor for pid, start, _ in LD.ancestors(self._table())):
                return False
            lease["expires"] = clock.iso()
            lead._save(lease)
            self._record(
                "status",
                self.lead,
                f"lead session {session} ended; the lead lease is free for takeover",
                data={"event": "lead_released", "session": session, "lead_fence": lease.get("fence", 0)},
            )
            return True

    @serialized_write
    def lead_heartbeat(self, caller):
        self._require_lead(caller, "renew the lead lease")
        return self.lead_status()

    @serialized_write
    def lead_handover(self, caller, to_session):
        """Lead-only: rotate the lead secret, bump the lead fence and bind the lease to `to_session`."""
        self._require_lead(caller, "hand the lead over")
        with self.lock():
            r = self.leadlease().handover(to_session)
            self._lead_session = None
            self._record(
                "status",
                self.lead,
                f"lead handed over from session {r['from']} to session {r['to']} (fence {r['fence']})",
                data={"event": "lead_handover", "from": r["from"], "to": r["to"], "lead_fence": r["fence"]},
            )
        return {**r, "secret_file": I.token_path(self.home, self.uid, self.lead)}

    def lead_takeover(self, lead_secret=None, env=None, session=None):
        """Take the lead lease after it expired. Needs the current lead secret; refused for worker credentials.
        Logged loudly as an alert."""
        env = self.env if env is None else env
        secret = I.presented_secret(lead_secret, env)
        if any(env.get(k) for k in ("RAIDLOCK_AGENT_ID", "RAIDLOCK_TOKEN", "RAIDLOCK_CAP")):
            raise IdentityError("workers cannot take over the lead", hint="unset worker credentials first")
        if not secret:
            raise IdentityError("takeover needs the current lead secret", hint=I.LEAD_HINT)
        sid, pid, start = LD.session_of(env, session, self._table())
        with self.lock():
            self._lead_check(secret, sid, any_session=True)
            lease = self.leadlease()
            r = lease.takeover(sid, pid, start)
            prev = r["previous"]
            self._lead_session = (sid, pid, start)
            self._record(
                "status",
                self.lead,
                f"ALERT LEAD TAKEOVER: session {sid} took the lead from session "
                f"{prev['session'] or '(none)'} whose lease expired at "
                f"{prev['expires'] or '-'}; lead fence now {r['fence']}",
                data={
                    "event": "lead_takeover",
                    "alert": True,
                    "session": sid,
                    "previous": prev,
                    "lead_fence": r["fence"],
                },
            )
        return {**r, "secret_file": I.token_path(self.home, self.uid, self.lead)}

    def agents(self):
        return self._cached("agents.json", "agents")

    def teams(self):
        return self._cached("teams.json", "teams")

    def _cached(self, name, key):
        # One read of self._cache: a runner thread may reach here while the writer clears it.
        cache = self._cache
        if cache is None:
            return store.read_doc(self._p(name), key)
        if name not in cache:
            cache[name] = store.read_doc(self._p(name), key)
        return copy.deepcopy(cache[name])

    def leases(self):
        return self.index.leases(self.uid)

    def version(self):
        """Cheap change counters: {workspace, global}. Use them as ETags for polling."""
        return self.index.versions(self.uid)

    def cards(self):
        """All cards, sorted by id. Unchanged files (same mtime and size) are served from the DB mirror."""
        folder = self._p("cards")
        names = sorted(f for f in os.listdir(folder) if f.endswith(".json")) if os.path.isdir(folder) else []
        cached = self.index.card_docs(self.uid)
        out, fresh, stats = [], [], {}
        for name in names:
            cid = name[:-5]
            try:
                st = os.stat(os.path.join(folder, name))
            except FileNotFoundError:
                continue
            key = (st.st_mtime_ns, st.st_size)
            doc, mtime, size = cached.get(cid, (None, None, None))
            if doc and (mtime, size) == key:
                out.append(json.loads(doc))
                continue
            card = store.read_doc(os.path.join(folder, name), "card")
            out.append(card)
            fresh.append(card)
            stats[cid] = key
        if fresh:
            self.index.put_cards(self.uid, fresh, stats, replace=False, bump=False)
        return out

    def card(self, card_id):
        path = self._p("cards", model.card_id(card_id) + ".json")
        try:
            st = os.stat(path)
        except FileNotFoundError:
            raise Precondition(f"no card {card_id}", card=card_id, hint="raidlock show") from None
        doc, mtime, size = self.index.card_doc(self.uid, card_id)
        if doc and (mtime, size) == (st.st_mtime_ns, st.st_size):
            return json.loads(doc)
        data = store.read_json(path)
        if data is None:
            raise Precondition(f"no card {card_id}", card=card_id, hint="raidlock show")
        store.check_schema(data, card_id)
        return data["card"]

    def brief(self, card_id, max_tokens=None):
        """Deterministic agent brief for a card with its token estimate (ceil(chars/4)), capped at brief_max_tokens."""
        card = self.card(card_id)
        cap = int(max_tokens or self.settings["brief_max_tokens"])
        lanes = self.index.lanes(self.uid, card.get("depends", []))
        minimal = B.render(dict(card, notes=[], output="", ticket="", reviewers=[]), lanes, cap)
        try:
            from ..lore import lessons_for
        except ImportError:
            return B.render(card, lanes, cap)
        lore = lessons_for(self, card, min(200, max(0, cap - minimal["tokens"])))
        result = B.render(card, lanes, cap, context=lore["text"])
        result.update(lore_tokens=lore["tokens"], lore_ids=[item["id"] for item in lore["lessons"]])
        return result

    def _save_card(self, card):
        card["updated"] = clock.iso()
        path = self._p("cards", card["id"] + ".json")
        store.write_doc(path, "card", card)
        st = os.stat(path)
        self.index.put_card(self.uid, card, (st.st_mtime_ns, st.st_size))

    def _save_agents(self, agents):
        store.write_doc(self._p("agents.json"), "agents", agents)
        if self._cache is not None:
            self._cache["agents.json"] = copy.deepcopy(agents)
        self.index.bump(self.uid)

    def _from(self, name):
        a = self.agents().get(name, {})
        out = {
            "agent": name,
            "provider": a.get("provider", ""),
            "runtime": a.get("runtime", ""),
            "model": a.get("model", ""),
        }
        if name == self.lead:
            out["session"] = (
                self._lead_session[0] if self._lead_session else self.leadlease().lease().get("session", "")
            )
        return out

    def _record(self, kind, by, text="", task="", **kw):
        rec = T.make(
            kind, self._from(by), text=text, task=task, context=f"{self.name}/{task}" if task else self.name, **kw
        )
        return self.transcript.append(rec)

    def _note(self, card, by, text):
        card["notes"].append(
            {"ts": clock.iso(), "by": by, "model": self.agents().get(by, {}).get("model", ""), "text": text}
        )

    def _auth(self, caller, card=None, scoped_ok=False):
        I.require_auth(caller, self.settings, self.env, self.lead, self.agents())
        if caller.scope_card and not scoped_ok and card != caller.scope_card:
            raise Denied(
                f"{caller.name}'s capability is scoped to card {caller.scope_card}",
                agent=caller.name,
                card=card or "",
                hint="use the agent token for other work",
            )
        if caller.scope_card:
            current = self.card(caller.scope_card)
            if current.get("owner") != caller.name or current.get("fence") != caller.fence or current.get("released"):
                raise Denied("card capability is stale or released", card=caller.scope_card)
        if caller.is_lead:
            self._lead_write(caller)

    def _live_providers(self, agents):
        """Providers with an agent present now: the lead, an agent seen within the lease TTL, or a live lease holder."""
        now = clock.iso()
        holders = {lease["agent"] for lease in self.index.leases(self.uid).values() if lease["expires"] > now}
        seen = self.index.last_seen(self.uid)
        ttl = self.ttl_seconds()
        live = set()
        for name, agent in agents.items():
            provider = agent.get("provider")
            if provider not in MODEL_PROVIDERS:
                continue
            age = clock.age_seconds(seen.get(name) or agent.get("last_seen", ""))
            if name == self.lead or name in holders or (age is not None and age <= ttl):
                live.add(provider)
        return live

    def _gate_settings(self, meta, agents=None):
        """Stored settings with the gates the lead never set following the providers present: on while two or more
        are live, the defaults when only one is left. Values set with `config set` are kept either way."""
        agents = self.agents() if agents is None else agents
        multi = len(self._live_providers(agents)) >= 2
        settings = copy.deepcopy(meta.get("settings", {}))
        explicit = meta.get("explicit_settings", [])
        review = normalize_review_setting(settings.get("require_review"))
        base = DEFAULT_SETTINGS["require_review"]
        if "require_review.count" not in explicit:
            review["count"] = max(1, base["count"]) if multi else base["count"]
        if "require_review.other_provider" not in explicit:
            review["other_provider"] = multi or base["other_provider"]
        settings["require_review"] = review
        if "require_verify" not in explicit:
            settings["require_verify"] = multi or DEFAULT_SETTINGS["require_verify"]
        return settings, multi

    def live_settings(self):
        """Settings as the next close will apply them, for display; nothing is written."""
        settings, _ = self._gate_settings(store.read_json(self._p("board.json")))
        s = {**DEFAULT_SETTINGS, **settings}
        s["require_review"] = normalize_review_setting(s.get("require_review"))
        return s

    def _sync_gate_defaults(self, actor, agents=None):
        """Store the provider-following gates; returns "enabled" or "relaxed" when they changed, else ""."""
        meta = store.read_json(self._p("board.json"))
        settings, multi = self._gate_settings(meta, agents)
        changed = ""
        if settings != meta.get("settings"):
            changed = "enabled" if multi else "relaxed"
            meta["settings"] = settings
            meta["gate_event"] = {"event": changed, "ts": clock.iso()}
            store.write_json(self._p("board.json"), meta)
            self._record(
                "status",
                actor,
                "multi-provider review and verify defaults " + ("enabled" if multi else "relaxed: one provider left"),
                data={"event": "gate_defaults" if multi else "gate_defaults_relaxed", "settings": settings},
            )
        self.meta = meta
        return changed

    def _touch(self, name):
        self.index.touch_agent(self.uid, name)

    def _resolver(self):
        if self._resolver_cache is None:
            self._resolver_cache = repos.Resolver(self.workspace, self.dir)
        return self._resolver_cache

    def _enroll(
        self, name, role, team, runtime, provider, declared, flag, reported, effort, session, issue=True, billing=None
    ):
        agents = self.agents()
        runtime = runtime or "shell"
        if runtime not in I.RUNTIMES:
            raise Precondition(
                f"runtime must be one of {', '.join(I.RUNTIMES)}",
                runtime=runtime,
                hint="the short names claude, codex and gemini work too",
            )
        provider = provider or I.RUNTIME_PROVIDER[runtime]
        if provider not in I.PROVIDERS:
            raise Precondition(f"provider must be one of {', '.join(I.PROVIDERS)}", provider=provider)
        found = models.detect(runtime, declared=declared, flag=flag, reported=reported, effort=effort, env=self.env)
        found.update(billing_mod.detect(runtime, declared=billing, env=self.env))
        old = agents.get(name, {})
        record = {
            **old,
            "team": team if team is not None else old.get("team", ""),
            "role": role,
            "provider": provider,
            "runtime": runtime,
            "session": session or old.get("session", ""),
            "joined": old.get("joined") or clock.iso(),
            "last_seen": clock.iso(),
            "card": old.get("card", ""),
            **found,
        }
        if not found["model"] and old.get("model"):
            record.update({k: old[k] for k in ("model", "model_version", "effort", "model_source") if k in old})
        record["display"] = f"{name} · {models.display(record['model'], self.home)} · {runtime}"
        agents[name] = record
        self._save_agents(agents)
        self.index.put_agent(self.uid, name, record)
        self._sync_gate_defaults(name, agents)
        token = I.issue_token(self.home, self.uid, name) if issue else None
        return {"agent": name, "token": token, **record}

    @serialized_write
    def join(
        self,
        caller,
        agent=None,
        team=None,
        role="worker",
        runtime="shell",
        provider=None,
        model_id=None,
        flag_model=None,
        reported_model=None,
        effort=None,
        session="",
        card="",
        billing=None,
    ):
        """Join (or update) an agent. Returns the record plus `token` when one was issued or rotated."""
        if role not in model.ROLES:
            raise Precondition(f"role must be one of {', '.join(model.ROLES)}", role=role)
        with self.lock():
            agents = self.agents()
            own = caller.name if caller.name and not caller.is_lead else ""
            name = agent or own or I.default_name(runtime, role, card, set(agents))
            if caller.subagent and name != caller.name:
                raise IdentityError(f"RAIDLOCK_AGENT_ID={caller.name} cannot join as {name}", agent=name)
            model.agent_name(name)
            if role == "lead" or name == self.lead:
                self._require_lead(caller, "join as lead")
                if name != self.lead:
                    raise Precondition(f"the lead of this board is {self.lead}", agent=name)
            if team:
                model.team_name(team)
                if team not in self.teams():
                    raise Precondition(
                        f"no team {team}; the lead creates it with `raidlock team add {team}`", team=team
                    )
            if caller.scope_card:
                self._auth(caller)
            keep_token = self._may_rejoin(caller, name, agents)
            result = self._enroll(
                name,
                role,
                team,
                runtime,
                provider,
                model_id,
                flag_model,
                reported_model,
                effort,
                session or LD.session_of(self.env)[0],
                issue=not keep_token and name != self.lead,
                billing=billing,
            )
            self._record("status", name, f"{name} joined as {role}" + (f" on team {team}" if team else ""))
        self.register()
        return result

    def _may_rejoin(self, caller, name, agents):
        if name not in agents:
            return False
        if caller.name == name and caller.authenticated:
            return True
        if caller.is_lead or not I.has_token(self.home, self.uid, name):
            return False
        raise IdentityError(f"{name} already joined; present its token or ask the lead to re-issue it", agent=name)

    @serialized_write
    def add_team(self, caller, name, max_active=2, mission="", repos_list=None):
        self._require_lead(caller, "manage teams")
        model.team_name(name)
        mission = model.line_text(mission, "mission", 500)
        with self.lock():
            teams = self.teams()
            old = teams.get(name, {})
            teams[name] = {
                "max": int(max_active if max_active is not None else old.get("max", 2)),
                "mission": mission or old.get("mission", ""),
                "repos": parse_list(repos_list) or old.get("repos", []),
            }
            store.write_doc(self._p("teams.json"), "teams", teams)
            self._cache.pop("teams.json", None)
            self.index.bump(self.uid)
        return {"team": name, **teams[name]}

    @serialized_write
    def remove_team(self, caller, name):
        self._require_lead(caller, "manage teams")
        with self.lock():
            teams = self.teams()
            if teams.pop(name, None) is None:
                raise Precondition(f"no team {name}", team=name)
            store.write_doc(self._p("teams.json"), "teams", teams)
            self._cache.pop("teams.json", None)
            self.index.bump(self.uid)
        return {"team": name, "removed": True}

    def team_list(self):
        agents = self.agents()
        return {
            n: {**t, "members": sorted(a for a, x in agents.items() if x.get("team") == n)}
            for n, t in sorted(self.teams().items())
        }

    def _move_lead_secret(self, old, new):
        path = I.token_path(self.home, self.uid, self.lead)
        src = LD.SecretStore(path, f"{self.uid}/{self.lead}", old, self.keychain)
        secret = src.get()
        if not secret:
            return
        dst = LD.SecretStore(path, f"{self.uid}/{self.lead}", new, self.keychain)
        where = dst.put(secret)
        if old == "keychain" and where == "file":
            src.keychain.delete()

    def get_setting(self, key=None):
        s = self.live_settings()
        if key is None:
            return s
        if key in PR_SETTING_KEYS:
            return s.get(key, "")
        if key not in SETTING_KEYS and not key.startswith("require_review."):
            raise Precondition(f"unknown setting {key}; known: {', '.join(SETTING_KEYS)}", key=key)
        if key.startswith("require_review."):
            return s["require_review"].get(key.split(".", 1)[1])
        return s[key]

    @serialized_write
    def set_setting(self, caller, key, value):
        """Lead-only. Keys: auth (token|off), ttl_minutes (number), require_review (N), require_review.count,
        require_review.other_provider."""
        self._require_lead(caller, "change settings")
        with self.lock():
            meta = store.read_json(self._p("board.json"))
            settings = dict(DEFAULT_SETTINGS, **meta.get("settings", {}))
            settings["require_review"] = normalize_review_setting(settings.get("require_review"))
            old_store = settings.get("lead_store", "file")
            previous = copy.deepcopy(settings)
            _apply_setting(settings, key, value)
            explicit = set(meta.get("explicit_settings", []))
            explicit.add("require_review.count" if key == "require_review" else key)
            meta["explicit_settings"] = sorted(explicit)
            if settings.get("lead_store", "file") != old_store:
                self._move_lead_secret(old_store, settings["lead_store"])
            meta["settings"] = settings
            store.write_json(self._p("board.json"), meta)
            self.meta = meta
            self.index.bump(self.uid)
            self._record(
                "status",
                caller.name,
                f"setting changed: {key}",
                data={"event": "setting_changed", "key": key, "before": previous, "after": settings},
            )
        return self.settings

    @serialized_write
    def new_card(
        self,
        caller,
        title,
        card_id=None,
        team="",
        locks=None,
        depends=None,
        scope="",
        verify="",
        verify_cmd="",
        output="",
        ticket="",
        reviewers=None,
        repo=None,
        allow_protected=None,
    ):
        """Create a card in todo. `verify_cmd` (a runnable check) may only be set by the lead."""
        self._auth(caller)
        if verify_cmd:
            self._require_lead(caller, "set a verify command")
        allowed = self._protected_paths(caller, allow_protected) if allow_protected else []
        if repo is not None and repo not in self._resolver().repos:
            raise Precondition(f"unknown workspace repo {repo!r}")
        cid = model.card_id(card_id or model.slug(title))
        lock_list = parse_list(locks)
        resolver = self._resolver()
        for p in lock_list:
            resolver.resolve(p)
        with self.lock():
            if os.path.exists(self._p("cards", cid + ".json")):
                raise Precondition(f"card {cid} exists", card=cid)
            if team and team not in self.teams():
                raise Precondition(f"no team {team}", team=team)
            card = model.new_card(
                cid,
                title,
                clock.iso(),
                team=team,
                locks=lock_list,
                repo=repo or "",
                allow_protected=allowed,
                ticket=ticket,
                scope=scope,
                output=output,
                verify=verify,
                verify_cmd=verify_cmd,
                depends=[model.card_id(d) for d in parse_words(depends)],
                reviewers=parse_words(reviewers),
            )
            if repo:
                self._card_repo(card)
            self._note(card, caller.name, "created")
            self._save_card(card)
            self._record(
                "status",
                caller.name,
                f"created {cid}: {card['title']}",
                task=cid,
                data={"locks": card["locks"], "repo": card["repo"], "allow_protected": allowed},
            )
        self.register(refresh=False)
        return card

    def import_plan(self, caller, items):
        """Create several cards from dicts (id, title, team, locks, depends, scope, verify, output, ticket).
        Validates every item before creating any. Returns the created cards."""
        self._auth(caller)
        existing = {c["id"] for c in self.cards()}
        prepared = []
        for item in items:
            if not isinstance(item, dict) or not item.get("title"):
                raise Precondition("every plan item needs a title", item=str(item)[:200])
            cid = model.card_id(item.get("id") or model.slug(item["title"]))
            if cid in existing:
                raise Precondition(f"card {cid} exists", card=cid, hint="raidlock show " + cid)
            existing.add(cid)
            prepared.append({**item, "id": cid})
        fields = ("team", "locks", "depends", "scope", "verify", "verify_cmd", "output", "ticket", "reviewers")
        return [
            self.new_card(caller, p["title"], card_id=p["id"], **{k: p.get(k) or "" for k in fields}) for p in prepared
        ]

    def _verify_hint(self, card):
        if card.get("verify_cmd"):
            return lead_cmd(self, f"verify {card['id']}")
        return (
            lead_cmd(self, f'edit {card["id"]} --verify-cmd "<command>"')
            + f", then verify {card['id']}; or turn the gate off: "
            + lead_cmd(self, "config set require_verify false")
        )

    def _protected_paths(self, caller, paths):
        self._require_lead(caller, "allow protected paths")
        allowed = parse_list(paths)
        if any(path not in (".github/workflows", ".claude") for path in allowed):
            raise Precondition("only .github/workflows and .claude can be allowed; .board is always denied")
        return allowed

    @serialized_write
    def edit_card(
        self,
        caller,
        card_id,
        verify_cmd=None,
        verify=None,
        repo=None,
        locks=None,
        depends=None,
        scope=None,
        allow_protected=None,
        exclude_locks=None,
        boss=None,
    ):
        """Lead-only: set or change a card's verify command or verify description. A changed command invalidates
        earlier receipts, because each receipt carries the hash of the command it ran."""
        self._auth(caller)
        self._require_lead(caller, "edit a card")
        changes = {}
        if exclude_locks is not None:
            changes["lock_exclusions"] = parse_list(exclude_locks)
            self._resolver_check(changes["lock_exclusions"])
        if allow_protected is not None:
            changes["allow_protected"] = self._protected_paths(caller, allow_protected)
        if repo is not None:
            if repo not in self._resolver().repos:
                raise Precondition(f"unknown workspace repo {repo!r}")
            changes["repo"] = repo
        if locks is not None:
            changes["locks"] = parse_list(locks)
            self._resolver_check(changes["locks"])
        if depends is not None:
            changes["depends"] = [model.card_id(d) for d in parse_words(depends)]
            if card_id in changes["depends"]:
                raise Precondition("a card cannot depend on itself")
            for dependency in changes["depends"]:
                self.card(dependency)
        if scope is not None:
            changes["scope"] = model.line_text(scope, "scope")
        if boss is not None:
            changes["boss"] = bool(boss)
        if verify_cmd is not None:
            changes["verify_cmd"] = model.line_text(verify_cmd, "verify_cmd")
        if verify is not None:
            changes["verify"] = model.line_text(verify, "verify")
        if not changes:
            raise Precondition(
                "nothing to change", card=card_id, hint=f'raidlock edit {card_id} --verify-cmd "<command>"'
            )
        with self.lock():
            card = self.card(card_id)
            if card["lane"] in ("done", "cancelled"):
                raise Precondition(f"{card_id} is {card['lane']}", card=card_id, hint=f"raidlock reopen {card_id}")
            candidate = {**card, **changes}
            if "repo" in changes or "locks" in changes:
                self._card_repo(candidate)
            self._card_keys(candidate)
            before = {k: card.get(k, "") for k in changes}
            lease = self.index.lease(self.uid, card_id)
            if ("locks" in changes or "lock_exclusions" in changes) and lease:
                self._grant(
                    candidate,
                    card["owner"],
                    candidate["locks"],
                    card["fence"],
                    self.ttl_seconds(),
                    checkout=lease.get("checkout"),
                    held=card["lane"] in ("review", "blocked"),
                    since=lease["since"],
                )
                self._update_lease(card_id, expires=lease["expires"])
            card.update(changes)
            if set(changes) != {"boss"}:
                card["rev"] = card.get("rev", 0) + 1
            self._note(card, caller.name, "edited " + ", ".join(sorted(changes)))
            self._save_card(card)
            self._record(
                "status",
                caller.name,
                f"edited {card_id}: " + ", ".join(sorted(changes)),
                task=card_id,
                data={"event": "card_edited", "before": before, "after": changes},
            )
        return {"card": card_id, **changes}

    def _owner_or_lead(self, card, caller):
        self._auth(caller, card["id"])
        # The lead may act on any card, so a lead that owns one needs no fence unless it passes one.
        if card["owner"] and caller.name == card["owner"] and (caller.fence or not caller.is_lead):
            if not caller.fence:
                raise Precondition(
                    f"no fence given for {card['id']}; your claim's fence is {card['fence']}",
                    card=card["id"],
                    hint=f"add --fence {card['fence']}, or export RAIDLOCK_FENCE={card['fence']}",
                )
            if caller.fence != card["fence"]:
                self._record(
                    "status",
                    caller.name,
                    f"stale fence {caller.fence} rejected for {card['id']}",
                    task=card["id"],
                    data={"event": "stale_fence", "fence": caller.fence, "current_fence": card["fence"]},
                )
                raise Denied(
                    f"fence {caller.fence} for {card['id']} is stale; someone claimed it after you",
                    card=card["id"],
                    fence=caller.fence,
                    hint=f"raidlock claim {card['id']}",
                )
            return "owner"
        if caller.is_lead:
            return "lead"
        raise Denied(f"{card['id']} is owned by {card['owner'] or 'nobody'}", card=card["id"], owner=card["owner"])

    def _keys(self, paths):
        resolver = self._resolver()
        return [resolver.resolve(p) for p in paths]

    def _card_keys(self, card, paths=None):
        keys = self._keys(card.get("locks", []) if paths is None else paths)
        excluded = self._keys(card.get("lock_exclusions", []))
        for exclusion in excluded:
            if not any(repos.key_contains(key, exclusion) and key["rel"] != exclusion["rel"] for key in keys):
                raise Precondition("excluded paths must be inside a held directory lock")
        return [
            {**key, "exclude": [item["rel"] for item in excluded if repos.key_contains(key, item)]} if excluded else key
            for key in keys
        ]

    def _grant(self, card, agent, paths, fence, ttl_s, checkout=None, held=False, since=None):
        return self.index.grant(
            self.uid,
            card["id"],
            agent,
            paths,
            [{**key, **({"advisory": True} if held else {})} for key in self._card_keys(card, paths)],
            fence,
            float(self.settings["hold_minutes"]) * 60 if held else ttl_s,
            held=False,
            since=since,
            checkout=checkout,
        )

    def _update_lease(self, card_id, **fields):
        return self.index.update_lease(self.uid, card_id, **fields)

    def _drop_lease(self, card_id):
        return self.index.drop_lease(self.uid, card_id)

    def _claim_reasons(self, card, agent, agents, teams, lease=None):
        resolver = self._resolver()
        for path in card.get("locks", []):
            if resolver.repos and resolver._match(path.split("/"))[0] is None:
                raise Precondition(
                    f"lock {path} matches no repo in workspace {self.workspace}",
                    hint="prefix the lock with a workspace repo: "
                    + (", ".join(sorted(resolver.repos)) or "initialize or declare a git repo in this workspace"),
                )
        out = []
        takeover = ""
        lane, owner = card["lane"], card["owner"]
        if lane in ("done", "cancelled"):
            out.append((3, f"{lane} is terminal; the lead can reopen it"))
        elif lane == "review":
            out.append((3, "in review; a `changes` review or the lead sends it back"))
        if owner and agent and owner != agent:
            takeover = owner
            lease = lease if lease is not None else self.index.lease(self.uid, card["id"])
            if lane == "doing" and lease and L.is_live(lease):
                out.append((2, f"owned by {owner} with a live lease until {lease['expires']}"))
        if agent:
            out.extend(self._member_reasons(card, agent, agents))
        lanes = self.index.lanes(self.uid, card.get("depends", []))
        for dep in card.get("depends", []):
            state = lanes.get(dep, "missing")
            if state != "done":
                out.append((3, f"waits on {dep} ({state})"))
        out.extend(self._cap_reasons(card, teams))
        clash = self.index.conflicts(self._card_keys(card), self.uid, card["id"])
        out.extend((2, L.describe(c)) for c in clash)
        return out, takeover, clash

    def _member_reasons(self, card, agent, agents):
        rec = agents.get(agent)
        if rec is None:
            return [(5, f"{agent} has not joined")]
        if rec.get("role") == "reviewer":
            return [(5, f"{agent} is a reviewer; reviewers cannot claim")]
        if card.get("team") and rec.get("team") != card["team"] and agent != self.lead:
            return [(5, f"{agent} is on team {rec.get('team') or '(none)'}; {card['id']} is for {card['team']}")]
        return []

    def _cap_reasons(self, card, teams):
        team = card.get("team")
        cap = teams.get(team, {}).get("max") if team else None
        if cap is None or card["lane"] == "doing":
            return []
        n = self.index.doing_in_team(self.uid, team, card["id"])
        return [(2, f"team {team} at its limit ({n}/{cap} in doing)")] if n >= cap else []

    @serialized_write
    def claim(
        self, card_id, caller, take_over=False, ttl_minutes=None, for_agent=None, checkout=None, runner_base=None
    ):
        """Grant every lock of the card atomically (across all registered workspaces) and move it to doing.

        Mints a new global fence; returns {card, agent, fence, expires, paths, checkout, taken_over_from}."""
        self._auth(caller)
        owner = for_agent or caller.name
        if owner != caller.name or runner_base:
            self._require_lead(caller, "claim for another agent")
        if for_agent and owner not in self.agents():
            self.join(caller, agent=owner, team=self.card(card_id).get("team") or None)
        warnings = advisories.pre_claim_warnings(self, card_id, checkout=checkout)
        with self.lock():
            card = self.card(card_id)
            warnings.extend(
                "Advisory hold: " + L.describe(c)
                for c in self.index.conflicts(self._keys(card["locks"]), self.uid, card_id, advisory=True)
            )
            reasons, prev, clash = self._claim_reasons(card, owner, self.agents(), self.teams())
            if prev and not take_over:
                reasons.append((2, f"owned by {prev}; --take-over needed"))
            if reasons and {c for c, _ in reasons} <= {2}:
                self._record(
                    "status",
                    owner,
                    f"lease denied on {card_id}: " + "; ".join(r for _, r in reasons),
                    task=card_id,
                    data={
                        "event": "lock_denied",
                        "agent": owner,
                        "reasons": [r for _, r in reasons],
                        "paths": [c.get("path", "") for c in clash or []],
                    },
                )
            _raise_reasons(card_id, reasons, clash)
            if checkout:
                self._verification_repo(card, checkout)
            info = repos.checkout_info(checkout) if checkout else None
            if info is not None and runner_base:
                info["runner_base"] = runner_base
            lease = self._grant(card, owner, card["locks"], None, self.ttl_seconds(ttl_minutes), checkout=info)
            card.update(owner=owner, lane="doing", fence=lease["fence"], released=False, rev=card.get("rev", 0) + 1)
            text = f"claimed by {owner} (fence {lease['fence']}); lease until {lease['expires']}"
            text += f"; taken over from {prev}" if prev else ""
            self._note(card, caller.name, text)
            self._save_card(card)
            self._touch(owner)
            self._record(
                "status",
                owner,
                text,
                task=card_id,
                data={"a2a_state": "working", "fence": lease["fence"], "locks": card["locks"]},
            )
        self.register(refresh=False)
        token = I.read_token(self.home, self.uid, owner) if owner != self.lead else ""
        cap = I.make_cap(token, owner, card_id, lease["fence"]) if token else ""
        return {
            "card": card_id,
            "agent": owner,
            "fence": lease["fence"],
            "expires": lease["expires"],
            "paths": lease["paths"],
            "checkout": lease["checkout"],
            "branch": lease["branch"],
            "taken_over_from": prev,
            "cap": cap,
            **({"warnings": warnings} if warnings else {}),
        }

    @serialized_write
    def heartbeat(self, card_id, caller, ttl_minutes=None):
        """Renew the card's lease (owner with fence, or lead). A doing lease renewed after expiry is re-granted and
        logged as resumed."""
        with self.lock():
            card = self.card(card_id)
            self._owner_or_lead(card, caller)
            if card["lane"] not in model.HOLD_LANES:
                raise Precondition(f"{card_id} is in {card['lane']}; nothing to renew", card=card_id)
            lease, resumed = self._renew(card, ttl_minutes)
            self._touch(caller.name)
        return {"card": card_id, "fence": card["fence"], "expires": lease["expires"], "resumed": resumed}

    def _renew(self, card, ttl_minutes=None):
        lease = self.index.lease(self.uid, card["id"])
        if card["lane"] != "doing":
            if not lease:
                raise Precondition(f"{card['id']} holds no lease; claim it again", card=card["id"])
            return self._update_lease(card["id"], renewed=clock.iso()), False
        if lease and L.is_live(lease):
            return self._update_lease(
                card["id"], renewed=clock.iso(), expires=clock.later(self.ttl_seconds(ttl_minutes))
            ), False
        lease = self._grant(
            card,
            card["owner"],
            card["locks"],
            card["fence"],
            self.ttl_seconds(ttl_minutes),
            since=(lease or {}).get("since"),
        )
        self._note(card, card["owner"], "lease resumed after expiry")
        self._save_card(card)
        self._record("status", card["owner"], "lease resumed after expiry", task=card["id"])
        return lease, True

    @serialized_write
    def note(self, card_id, caller, text):
        """Append a note. The owner must pass its fence; an owner note renews the lease. The lead secret alone
        notes on any card, its own included, without renewing a lease."""
        text = model.line_text(T.redact(text), "note")
        if not text:
            raise Precondition("note text is empty")
        with self.lock():
            card = self.card(card_id)
            self._auth(caller, card_id)
            is_owner = bool(card["owner"]) and caller.name == card["owner"]
            if is_owner and caller.is_lead and not caller.fence:
                is_owner = False
            if is_owner:
                self._owner_or_lead(card, caller)
                card["rev"] = card.get("rev", 0) + 1
            self._note(card, caller.name, text)
            self._save_card(card)
            if is_owner and card["lane"] in model.HOLD_LANES:
                self._renew(card)
            self._touch(caller.name)
            self._record("note", caller.name, text, task=card_id)
            delivered = self._lead_note_to_owner(card, caller, text)
        return {"card": card_id, "note": text, "delivered_to": delivered}

    def _lead_note_to_owner(self, card, caller, text):
        owner = card.get("owner") or ""
        if not caller.is_lead or not owner or owner == caller.name or owner not in self.agents():
            return ""
        msg = M.make(
            caller.name,
            owner,
            f"lead note on {card['id']}: {text}",
            card["id"],
            "ready",
            True,
            self.agents().get(caller.name, {}).get("model", ""),
        )
        msg["source"] = "note"
        store.append_line(M.inbox_path(self.dir, owner), _dumps(msg))
        self.index.bump(self.uid)
        self._record(
            "msg",
            caller.name,
            msg["text"],
            task=card["id"],
            to=owner,
            message_id=msg["id"],
            data={"status": "ready", "urgent": True, "source": "note"},
        )
        return owner

    @serialized_write
    def lock_paths(self, card_id, caller, paths, replace=False, remove=False):
        """Add lock paths to a card; re-grants the lease atomically when the card holds one."""
        add = parse_list(paths)
        if not add:
            raise Precondition("no paths given")
        with self.lock():
            card = self.card(card_id)
            if replace or remove:
                self._auth(caller)
                self._require_lead(caller, "replace or remove locks")
            elif card["lane"] == "todo" and not card["owner"]:
                self._auth(caller)
                self._require_lead(caller, "change locks of an unclaimed card")
            else:
                self._owner_or_lead(card, caller)
            merged = sorted(
                set(add) if replace else set(card["locks"]) - set(add) if remove else set(card["locks"]) | set(add)
            )
            lease = self.index.lease(self.uid, card_id)
            if lease and card["lane"] in model.HOLD_LANES:
                self._grant(
                    card,
                    card["owner"],
                    merged,
                    card["fence"],
                    self.ttl_seconds(),
                    held=card["lane"] in ("review", "blocked"),
                    since=lease["since"],
                )
                self._update_lease(card_id, expires=lease["expires"])
            else:
                self._resolver_check(merged)
            card["locks"] = merged
            card["rev"] = card.get("rev", 0) + 1
            self._note(card, caller.name, "locks now " + ", ".join(merged))
            self._record(
                "status",
                caller.name,
                "locks updated",
                task=card_id,
                data={"locks": merged, "replace": replace, "remove": remove, "fence": card["fence"]},
            )
            self._save_card(card)
        return {"card": card_id, "locks": merged}

    def _resolver_check(self, paths):
        resolver = self._resolver()
        for p in paths:
            resolver.resolve(p)

    @serialized_write
    def release(self, card_id, caller, note=None):
        """Drop the card's lease. A doing card goes back to todo with no owner (A2A canceled)."""
        with self.lock():
            card = self.card(card_id)
            if caller.is_lead:
                self._auth(caller)
            else:
                self._owner_or_lead(card, caller)
            dropped = self._drop_lease(card_id)
            if card["lane"] == "doing":
                card.update(lane="todo", owner="", released=True)
            self._note(card, caller.name, "lease released" + (f" ({_line(note)})" if note else ""))
            self._save_card(card)
            self._record(
                "status", caller.name, "lease released", task=card_id, data={"a2a_state": model.a2a_state(card)}
            )
        return {"card": card_id, "released": dropped, "lane": card["lane"]}

    @serialized_write
    def close_with_evidence(self, card_id, caller, evidence, note):
        self._auth(caller)
        self._require_lead(caller, "close a card with evidence")
        evidence = model.line_text(evidence, "evidence")
        sha = re.fullmatch(r"[0-9a-f]{7,40}", evidence)
        try:
            url = urlsplit(evidence)
            valid = sha or (url.scheme == "https" and bool(url.hostname) and not any(c.isspace() for c in evidence))
        except ValueError:
            valid = False
        if not valid:
            raise Precondition(
                "evidence must be an https URL or a commit in the card's repo; use --evidence https://host/path or SHA"
            )
        note = model.line_text(note, "note")
        if not note:
            raise Precondition("closing with evidence requires a note; use --note <text>")
        with self.lock():
            card = self.card(card_id)
            if card["lane"] not in ("todo", "doing", "review", "blocked"):
                raise Precondition(f"{card_id} is already {card['lane']}")
            if sha:
                evidence = "commit:" + self._evidence_commit(card, evidence)
            self._drop_lease(card_id)
            card.update(lane="done", evidence=evidence, rev=card.get("rev", 0) + 1)
            text = f"closed with evidence {evidence}: {note}"
            self._note(card, caller.name, text)
            self._save_card(card)
            self._record(
                "status",
                caller.name,
                text,
                task=card_id,
                data={"lane": "done", "a2a_state": "completed", "evidence": evidence, "note": note},
            )
        return {"card": card_id, "lane": "done", "a2a_state": "completed", "evidence": evidence}

    def _evidence_commit(self, card, ref):
        repo = self._card_repo(card)
        if not repo:
            raise Precondition(f"{card['id']} has no repo to find commit {ref} in; set one with raid edit --repo")
        from ..runners import workspace

        found = workspace.git_run(str(repo), "rev-parse", "--verify", "--quiet", ref + "^{commit}", timeout=30)
        if found.returncode:
            raise Precondition(f"commit {ref} is not in {repo}; use a commit from the card's repo or an https URL")
        return found.stdout.strip()

    @serialized_write
    def cancel(self, card_id, caller, reason):
        self._auth(caller)
        self._require_lead(caller, "cancel a card")
        reason = model.line_text(reason, "reason")
        if not reason:
            raise Precondition("cancellation requires a reason")
        with self.lock():
            card = self.card(card_id)
            if card["lane"] in ("done", "cancelled"):
                raise Precondition(f"{card_id} is already {card['lane']}")
            self._drop_lease(card_id)
            card.update(lane="cancelled", cancel_reason=reason, rev=card.get("rev", 0) + 1)
            self._note(card, caller.name, "cancelled: " + reason)
            self._save_card(card)
            self._record(
                "status",
                caller.name,
                "cancelled: " + reason,
                task=card_id,
                data={"lane": "cancelled", "a2a_state": "canceled", "reason": reason},
            )
        return {"card": card_id, "lane": "cancelled", "reason": reason}

    @serialized_write
    def move(self, card_id, lane, caller, note=None):
        """Move a card. doing is reachable only through claim; done is lead-only and terminal."""
        model.lane(lane)
        note = _line(note) if note else ""
        if lane == "cancelled":
            raise Precondition(f"use raidlock cancel {card_id} --reason <reason>")
        if lane == "doing":
            raise Denied("only claim moves a card into doing", card=card_id, hint=f"raidlock claim {card_id}")
        with self.lock():
            card = self.card(card_id)
            if card["lane"] in ("done", "cancelled"):
                raise Precondition(
                    f"{card_id} is {card['lane']}", card=card_id, hint=f"raidlock reopen {card_id}   (lead)"
                )
            if card["lane"] == lane:
                raise Precondition(f"{card_id} is already in {lane}", card=card_id)
            self._gates_changed = ""
            role = self._move_checks(card, lane, caller)
            if not caller.is_lead:
                self._touch(caller.name)
            self._apply_move(card, lane, role)
            self._note(card, caller.name, f"-> {lane}" + (f" ({note})" if note else ""))
            self._save_card(card)
            self._record(
                "status",
                caller.name,
                f"{card_id} -> {lane}" + (f": {note}" if note else ""),
                task=card_id,
                data={"a2a_state": model.a2a_state(card), "lane": lane},
            )
        out = {"card": card_id, "lane": lane, "a2a_state": model.a2a_state(card), "owner": card["owner"]}
        if self._gates_changed == "relaxed":
            out["gates"] = "relaxed"
        return out

    def _move_checks(self, card, lane, caller):
        if lane == "done":
            self._auth(caller)
            self._require_lead(caller, "move a card to done")
            self._gates_changed = self._sync_gate_defaults(caller.name)
            self._done_gate(card)
            return "lead"
        if lane in ("review", "blocked") and card["lane"] == "todo":
            raise Precondition(
                f"{card['id']} is in todo; run raidlock claim {card['id']} before moving to {lane}",
                card=card["id"],
                hint=f"raidlock claim {card['id']}",
            )
        return self._owner_or_lead(card, caller)

    def _done_gate(self, card):
        from ..runners import workspace
        from . import receipts

        settings = self.settings
        explicit_off = "require_verify" in self.meta.get("explicit_settings", []) and not settings["require_verify"]
        if not explicit_off and (settings["require_verify"] or card.get("verify_cmd")):
            last = model.last_verification(card)
            if not last or not receipts.valid(self, card, last):
                raise Precondition(
                    f"{card['id']} needs a valid verification receipt", card=card["id"], hint=self._verify_hint(card)
                )
            if not last.get("passed") or last.get("exit") != 0:
                raise Precondition(
                    f"the last verify run of {card['id']} failed",
                    card=card["id"],
                    hint=f"fix the work, commit, then raidlock verify {card['id']}",
                )
            where = last.get("cwd")
            if not where:
                raise Precondition(f"{card['id']} verification receipt has no checkout")
            actual = self._verification_repo(card, where)
            leased_info = (self.index.lease(self.uid, card["id"]) or {}).get("checkout") or {}
            leased = leased_info.get("path")
            base = leased_info.get("runner_base")
            landed = bool(base) and workspace.is_ancestor(where, base, last.get("sha", ""))
            if leased and (
                self._verification_repo(card, leased) != actual
                or (not landed and workspace.head(leased) != last.get("sha"))
            ):
                raise Precondition(f"{card['id']} verification receipt does not match the leased repo commit")
            if last.get("repo") != actual:
                raise Precondition(f"{card['id']} verification receipt repo does not match the card")
            if last.get("sha") != workspace.head(where):
                raise Precondition(f"{card['id']} verification receipt SHA does not match current HEAD")
        need = dict(self.settings["require_review"])
        if card.get("allow_protected"):
            need.update(count=max(1, need["count"]), other_provider=True)
        got = self.approvals(card)
        if len(got) < need["count"]:
            raise Precondition(
                f"{card['id']} needs {need['count']} approving review(s)"
                + (" from another provider" if need["other_provider"] else "")
                + f"; has {len(got)}",
                card=card["id"],
                approvals=got,
                hint=f"a reviewer on {'another' if need['other_provider'] else 'any'} provider runs raidlock review "
                f"{card['id']} --verdict approve; to request one: "
                + lead_cmd(self, f"review {card['id']} --assign <reviewer>")
                + "; for work already merged: "
                + lead_cmd(self, f"close {card['id']} --evidence <PR URL or SHA> --note <text>")
                + (
                    ""
                    if card.get("allow_protected")
                    else "; to never require reviews on this board: " + lead_cmd(self, "config set require_review 0")
                ),
            )

    def _apply_move(self, card, lane, role):
        if lane in ("done", "todo"):
            self._drop_lease(card["id"])
        if lane == "todo":
            card.update(owner="", released=True)
        if lane in ("review", "blocked"):
            lease = self.index.lease(self.uid, card["id"])
            if lease and card["lane"] not in ("review", "blocked"):
                lease["keys"] = [{**key, "advisory": True} for key in lease["keys"]]
                lease.update(expires=clock.later(float(self.settings["hold_minutes"]) * 60), renewed=clock.iso())
                self.index.put_lease(self.uid, lease)
        if role == "owner":
            card["rev"] = card.get("rev", 0) + 1
        card["lane"] = lane

    def approvals(self, card, agents=None):
        """Approving reviewers that count toward `require_review`: latest verdict per reviewer, after the owner's
        latest change."""
        need = dict(self.settings["require_review"])
        if card.get("allow_protected"):
            need["other_provider"] = True
        agents = self.agents() if agents is None else agents
        owner_provider = agents.get(card.get("owner"), {}).get("provider", "")
        latest = {}
        for r in card.get("reviews", []):
            if r.get("rev", -1) == card.get("rev", 0) and r["verdict"] != "comment":
                latest[r["by"]] = r
        out = []
        for name, r in sorted(latest.items()):
            if r["verdict"] != "approve" or name == card.get("owner"):
                continue
            if card.get("allow_protected") and (not owner_provider or not r.get("provider")):
                continue
            if need["other_provider"] and (
                r.get("provider") not in (*MODEL_PROVIDERS, "human") or r.get("provider") == owner_provider
            ):
                continue
            out.append(name)
        return out

    @serialized_write
    def reopen(self, card_id, caller, note=None):
        """Lead-only: move a done card back to todo."""
        self._auth(caller)
        self._require_lead(caller, "reopen a card")
        with self.lock():
            card = self.card(card_id)
            if card["lane"] not in ("done", "cancelled"):
                raise Precondition(f"{card_id} is in {card['lane']}, not done", card=card_id)
            card.update(lane="todo", owner="", released=False, rev=card.get("rev", 0) + 1)
            card.pop("cancel_reason", None)
            card.pop("evidence", None)
            self._note(card, caller.name, "reopened" + (f" ({_line(note)})" if note else ""))
            self._save_card(card)
            self._record("status", caller.name, f"{card_id} reopened", task=card_id, data={"a2a_state": "submitted"})
        return {"card": card_id, "lane": "todo"}

    @serialized_write
    def review(self, card_id, caller, verdict, note=""):
        """Record a review of a card in review. `changes` sends it back to doing (owner and fence unchanged)."""
        if verdict not in model.VERDICTS:
            raise Precondition(f"verdict must be one of {', '.join(model.VERDICTS)}", verdict=verdict)
        note = model.line_text(T.redact(note or ""), "note")
        with self.lock():
            self._auth(caller)
            self._touch(caller.name)
            card = self.card(card_id)
            if card["lane"] != "review":
                raise Precondition(f"{card_id} is in {card['lane']}, not review", card=card_id)
            if caller.name == card["owner"]:
                raise Denied("owners cannot review their own card", card=card_id)
            if card.get("reviewers") and caller.name not in card["reviewers"] and not caller.is_lead:
                raise Denied(f"{card_id} is assigned to reviewers {', '.join(card['reviewers'])}", card=card_id)
            agent = self.agents().get(caller.name, {})
            rec = {
                "ts": clock.iso(),
                "by": caller.name,
                "verdict": verdict,
                "note": note,
                "rev": card.get("rev", 0),
                "provider": agent.get("provider", ""),
                "model": agent.get("model", ""),
            }
            card.setdefault("reviews", []).append(rec)
            self._note(card, caller.name, f"review {verdict}" + (f": {note}" if note else ""))
            if verdict == "changes":
                self._back_to_doing(card)
            self._save_card(card)
            self._record(
                "review",
                caller.name,
                note,
                task=card_id,
                to=card["owner"],
                artifacts=[T.artifact("review", note, data={"verdict": verdict, "rev": rec["rev"]}, kind="review")],
            )
        return {"card": card_id, "verdict": verdict, "lane": card["lane"], "approvals": self.approvals(card)}

    def _back_to_doing(self, card):
        if not card["owner"]:
            card["lane"] = "todo"
            return
        if not self.index.lease(self.uid, card["id"]):
            card["lane"] = "blocked"
            return
        card["lane"] = "doing"
        try:
            self._grant(card, card["owner"], card["locks"], card["fence"], self.ttl_seconds())
        except Denied:
            card["lane"] = "blocked"

    @serialized_write
    def assign_reviewers(self, card_id, caller, names):
        """Owner (with fence) or lead sets the card's reviewers. Returns warnings for same-provider reviewers."""
        names = parse_words(names)
        with self.lock():
            card = self.card(card_id)
            self._owner_or_lead(card, caller)
            agents = self.agents()
            for n in names:
                if n not in agents:
                    raise Precondition(f"{n} has not joined", agent=n)
                if n == card["owner"]:
                    raise Precondition("the owner cannot review its own card", agent=n)
            owner_provider = agents.get(card["owner"], {}).get("provider", "")
            warnings = [
                f"{n} has the same provider as the owner ({owner_provider})"
                for n in names
                if owner_provider and agents[n].get("provider") == owner_provider
            ]
            card["reviewers"] = names
            self._note(card, caller.name, "reviewers: " + (", ".join(names) or "-"))
            self._save_card(card)
        return {"card": card_id, "reviewers": names, "warnings": warnings}

    def _card_repo(self, card):
        from ..runners import policy

        if card.get("repo"):
            target = self._resolver().repos.get(card["repo"])
            if not target:
                raise Precondition(f"unknown workspace repo {card['repo']!r}")
            if card.get("locks") and policy.target(self.workspace, self.dir, card["locks"])["path"] != target:
                raise Precondition("card repo and locks name different repositories")
            return target
        if card.get("locks"):
            return policy.target(self.workspace, self.dir, card["locks"])["path"]
        found = self._resolver().repos
        return repos.find_root(self.workspace) or (next(iter(found.values())) if len(found) == 1 else None)

    def _verification_source(self, card):
        lease = self.index.lease(self.uid, card["id"]) or {}
        target = self._card_repo(card)
        root = repos.find_root(self.workspace)
        default = root if root and target and repos.identity(root) == repos.identity(target) else target
        return (
            (lease.get("checkout") or {}).get("path") or card.get("verification_checkout") or default or self.workspace
        )

    def trust_checkout(self, caller, card_id, checkout, source):
        with self.lead_write(caller, "record checkout provenance"):
            expected = self._verification_repo(self.card(card_id), source)
            root = repos.find_root(checkout)
            if not root:
                raise Precondition("trusted checkout must be a repository")
            info = os.stat(root)
            path = self._p("checkout-provenance.json")
            records = store.read_json(path, {}) or {}
            records[os.path.realpath(root)] = {
                "repo": expected,
                "identity": repos.identity(root),
                "device": info.st_dev,
                "inode": info.st_ino,
            }
            store.write_json(path, records)

    def _verification_repo(self, card, where):
        target = self._card_repo(card)
        root = repos.find_root(where)
        if not target:
            raise Precondition(
                "the card has no repo to verify",
                hint=f"raidlock edit {card['id']} --repo <workspace-repo>; then verify with --cwd <checkout>",
            )
        expected = repos.identity(target)
        if root and repos.identity(root) == expected:
            return expected
        if root:
            provenance = store.read_json(os.path.join(self.dir, "checkout-provenance.json"), {}) or {}
            record = provenance.get(os.path.realpath(root), {})
            info = os.stat(root)
            if record == {
                "repo": expected,
                "identity": repos.identity(root),
                "device": info.st_dev,
                "inode": info.st_ino,
            }:
                return expected
            common = os.path.realpath(repos.common_dir(repos.git_dir(root)))
            target_common = os.path.realpath(repos.common_dir(repos.git_dir(target)))
            if common == target_common:
                return expected
            url = repos._read_remotes(os.path.join(common, "config")).get("origin", "")
            parts = urlsplit(url)
            from urllib.request import url2pathname

            local = url2pathname(parts.path) if parts.scheme == "file" and parts.netloc in ("", "localhost") else url
            if local and not os.path.isabs(local):
                local = os.path.join(root, local)
            if (
                not repos.remote_url(target)
                and local
                and os.path.isdir(local)
                and os.path.realpath(local) == os.path.realpath(target)
            ):
                return expected
        raise Precondition(
            f"verification checkout {where} does not belong to the card's repo ({target})",
            hint=f"use --checkout {target} for claim, or --cwd {target} for verify",
        )

    def verify(self, card_id, caller, timeout=600, cwd=None):
        from ..runners import isolation, verifycache, workspace
        from . import receipts

        self._auth(caller)
        self._require_lead(caller, "run verify")
        card = self.card(card_id)
        cmd = card.get("verify_cmd")
        if not cmd:
            raise Precondition(
                f"{card_id} has no verify command",
                card=card_id,
                hint=lead_cmd(self, f'edit {card_id} --verify-cmd "<command>"'),
            )
        kind = isolation.mode()
        if kind is None:
            raise Precondition("verification requires an isolated verification environment")
        where = os.path.realpath(cwd or self._verification_source(card))
        if workspace.git(where, "status", "--porcelain", "--untracked-files=all").strip():
            raise Precondition(
                "verification checkout is dirty; receipts are bound to a commit",
                hint=f"raidlock verify {card_id} --cwd <clean checkout of the card branch>",
            )
        repo = self._verification_repo(card, where)
        sha = workspace.head(where)
        with verifycache.checkout(where, sha, os.path.join(self.home, "verify"), timeout=timeout + 60) as (
            clone,
            gitdir,
        ):
            exit_code, tail, ms = isolation.run(
                cmd,
                clone,
                self.env,
                timeout,
                kind=kind,
                deny_read=isolation.hidden(self.env, [self.home, self.dir]),
                readable=[gitdir],
                redact=T.redact,
            )
        rec = {
            "ts": clock.iso(),
            "by": caller.name,
            "cmd": cmd,
            "cwd": where,
            "repo": repo,
            "exit": exit_code,
            "passed": exit_code == 0,
            "tail": tail,
            "duration_ms": ms,
            "rev": card.get("rev", 0),
            "card": card_id,
            "board": self.uid,
            "sha": sha,
            "command_hash": receipts.command_hash(cmd),
            "runner": "raidlock",
            "isolation": kind,
            "owner": card.get("owner", ""),
        }
        with self.lock():
            self._require_lead(caller, "record verify result")
            current = self.card(card_id)
            if current.get("verify_cmd") != cmd:
                raise Precondition("verify command changed during verification")
            rec = receipts.sign(self, rec)
            current["verification_checkout"] = where
            current.setdefault("verifications", []).append(rec)
            self._note(current, caller.name, f"verify {'passed' if rec['passed'] else 'failed'} (exit {exit_code})")
            self._save_card(current)
            self._record(
                "verify",
                caller.name,
                f"verify {'passed' if rec['passed'] else 'failed'}: {cmd}",
                task=card_id,
                artifacts=[T.artifact("verify", tail, data=rec, kind="verify")],
            )
        return {"card": card_id, **rec}

    def reap(self, caller=None):
        """Drop expired leases; doing cards become blocked, review/blocked cards keep their lane."""
        reaped = []
        with self.lock():
            leases = self.leases()
            for card_id, lease in sorted(leases.items()):
                try:
                    card = self.card(card_id)
                except Precondition:
                    self._drop_lease(card_id)
                    continue
                if card["lane"] in ("doing", "review", "blocked") and not L.is_live(lease):
                    self._drop_lease(card_id)
                    if card["lane"] == "doing":
                        card["lane"] = "blocked"
                    self._note(
                        card,
                        "raidlock",
                        f"lease expired; lane {card['lane']} ({card['owner']} may claim again, "
                        "a teammate may claim --take-over)",
                    )
                    self._save_card(card)
                    self._record(
                        "status",
                        card["owner"],
                        f"lease expired; reaped in {card['lane']}",
                        task=card_id,
                        data={"a2a_state": "input-required"},
                    )
                    reaped.append(card_id)
        return {"reaped": reaped}

    def claimable(self, agent=None, for_review=False, cards=None):
        """{card_id: {ok, reasons, takeover}} for open cards, or cards awaiting review when `for_review`."""
        cards = self.cards() if cards is None else cards
        if for_review:
            return self._review_queue(cards, agent)
        agents, teams, leases = self.agents(), self.teams(), self.leases()
        out = {}
        for card in cards:
            if card["lane"] in ("done", "review"):
                continue
            lease = leases.get(card["id"])
            if card["lane"] == "doing" and lease and L.is_live(lease) and card["owner"] != agent:
                continue
            try:
                reasons, takeover, _ = self._claim_reasons(card, agent, agents, teams, lease=lease or {})
            except Precondition as e:
                reasons, takeover = [(3, str(e))], ""
            out[card["id"]] = {
                "ok": not reasons,
                "reasons": [r for _, r in reasons],
                "takeover": takeover,
                "title": card["title"],
                "lane": card["lane"],
            }
        return out

    def _review_queue(self, cards, agent):
        out = {}
        for card in cards:
            if card["lane"] != "review" or (agent and agent == card["owner"]):
                continue
            if agent and card.get("reviewers") and agent not in card["reviewers"]:
                continue
            out[card["id"]] = {
                "ok": True,
                "reasons": [],
                "takeover": "",
                "title": card["title"],
                "lane": "review",
                "owner": card["owner"],
                "reviewers": card.get("reviewers", []),
                "approvals": self.approvals(card),
            }
        return out

    def check_paths(self, paths, card_id=None):
        """Conflicts for lock paths against every live lease in every registered workspace."""
        return self.index.conflicts(self._keys(parse_list(paths)), self.uid, card_id or "")

    @serialized_write
    def check_write(self, path, caller):
        """Enforcement decision for writing `path` (absolute or relative to cwd): {allowed, reason, card, holder}.

        A denial for a named caller is recorded as a transcript status event with data.event lock_violation."""
        decision = self._write_decision(path, caller)
        if not decision["allowed"] and caller.name:
            with self.lock():
                self._record(
                    "status",
                    caller.name,
                    f"write denied: {decision['reason']}",
                    task=decision["card"],
                    data={
                        "event": "lock_violation",
                        "agent": caller.name,
                        "path": os.path.abspath(path),
                        "holder": decision["holder"],
                        "reason": decision["reason"],
                    },
                )
        return decision

    def _write_decision(self, path, caller):
        try:
            self._auth(caller, scoped_ok=True)
        except (IdentityError, Denied, Precondition, StaleLead) as exc:
            return _decision(False, exc.message)
        if not caller.authenticated:
            return _decision(False, "writes require an authenticated caller")
        full = os.path.abspath(path)
        if ".board" in full.replace("\\", "/").split("/"):
            return _decision(False, "board files are written only by raidlock")
        loc = repos.locate(full)
        if not loc:
            return _decision(True, "outside any git repo")
        mine, others = [], []
        for workspace, ws, lease in self.index.holders_of(loc):
            own = lease.get("agent") == caller.name and ws == self.uid
            (mine if own else others).append((workspace, lease["card"], lease))
        for _, card_id, lease in mine:
            if caller.scope_card and caller.scope_card != card_id:
                return _decision(False, f"capability is scoped to {caller.scope_card}", card=card_id)
            if caller.is_lead or (caller.fence and caller.fence == lease.get("fence")):
                return _decision(True, f"inside the lease of {card_id}", card=card_id, holder=caller.name)
            return _decision(False, f"stale fence {caller.fence} for {card_id}", card=card_id, holder=caller.name)
        if others:
            ws, card_id, lease = others[0]
            return _decision(
                False, f"held by {lease['agent']} on {card_id} in workspace {ws}", card=card_id, holder=lease["agent"]
            )
        if caller.is_lead:
            return _decision(True, "lead; not held by anyone")
        return _decision(False, f"{caller.name or 'caller'} holds no lease covering this path")

    def _busy(self, name):
        return next(
            (c["id"] for c in self.index.card_rows(self.uid) if c["owner"] == name and c["lane"] == "doing"), ""
        )

    @serialized_write
    def send(self, caller, to, text, task=None, urgent=False):
        """Send a message. Queued while the recipient is in doing on another card; ready when about its doing card,
        when urgent (lead only) or when the recipient is idle. Returns the message; `dropped` marks a repeat."""
        self._auth(caller, scoped_ok=True)
        text = model.block_text(T.redact(text), "message", M.MAX_BYTES)
        if not text.strip():
            raise Precondition("message text is empty")
        if urgent:
            self._require_lead(caller, "send urgent messages")
        with self.lock():
            if to not in self.agents():
                raise Precondition(f"no agent {to} on this board", to=to)
            if task:
                self.card(task)
            dup = M.check_limits(self.dir, caller.name, to, task or "", text, clock.now())
            if dup:
                return {**dup, "dropped": True}
            status = M.initial_status(task, urgent, self._busy(to))
            msg = M.make(
                caller.name, to, text, task, status, urgent, self.agents().get(caller.name, {}).get("model", "")
            )
            store.append_line(M.inbox_path(self.dir, to), _dumps(msg))
            self.index.bump(self.uid)
            self._touch(caller.name)
            self._record(
                "msg",
                caller.name,
                text,
                task=task or "",
                to=to,
                message_id=msg["id"],
                data={"status": status, "urgent": bool(urgent)},
            )
        return {**msg, "dropped": False}

    def inbox(self, caller, show_all=False, wait=0.0, interval=1.0):
        """Messages for the caller: ready ones are returned and marked read; queued ones stay until it leaves doing.
        With `wait` > 0, blocks in-process until a message is ready (raises Timeout after `wait` seconds)."""
        self._auth(caller, scoped_ok=True)
        deadline = time.monotonic() + wait
        while True:
            result = self._read_inbox(caller, show_all)
            if result["messages"] or wait <= 0:
                return result
            if time.monotonic() >= deadline:
                raise Timeout(f"no message for {caller.name} after {wait:g}s", agent=caller.name)
            db.close_all()
            time.sleep(max(0.01, min(interval, deadline - time.monotonic())))

    def _read_inbox(self, caller, show_all):
        with self.lock():
            self._auth(caller, scoped_ok=True)
            rows = M.load(self.dir, caller.name)
            busy = self._busy(caller.name)
            changed = M.promote(rows, busy)
            shown = [dict(m) for m in rows if show_all or m.get("status") == "ready"]
            for m in rows:
                if m.get("status") == "ready":
                    m.update(status="read", read=clock.iso())
                    changed = True
            if changed:
                M.save(self.dir, caller.name, rows)
                self.index.bump(self.uid)
            queued = sum(1 for m in rows if m.get("status") == "queued")
        return {"agent": caller.name, "messages": shown, "queued": queued, "busy": busy}

    def take_urgent(self, agent):
        """Urgent messages for `agent` not yet read, marked read as delivered by the hook. Queued (non-urgent)
        messages stay queued. Used by the PreToolUse hook at the agent's next tool call, even in doing."""
        if not agent or agent not in self.agents():
            return []
        with self.lock():
            rows = M.load(self.dir, agent)
            out = []
            for m in rows:
                if m.get("urgent") and m.get("status") in ("ready", "queued"):
                    m.update(status="read", read=clock.iso(), delivered="hook")
                    out.append(dict(m))
            if out:
                M.save(self.dir, agent, rows)
                self.index.bump(self.uid)
                for m in out:
                    self._record(
                        "status",
                        agent,
                        f"urgent message {m['id']} delivered by hook",
                        task=m.get("task", ""),
                        data={"event": "urgent_delivered", "message_id": m["id"], "from": m.get("from", "")},
                    )
        return out

    def agent_for_session(self, session):
        """The agent whose recorded session is `session` (from join), or ""."""
        if not session:
            return ""
        hits = sorted(n for n, a in self.agents().items() if a.get("session") == session)
        return hits[0] if len(hits) == 1 else ""

    def wait(self, card_id, timeout=1800.0, interval=5.0):
        """Block until the card leaves doing; returns its lane. Raises Timeout (exit 4)."""
        deadline = time.monotonic() + timeout
        while True:
            lane = self.card(card_id)["lane"]
            if lane != "doing":
                return {"card": card_id, "lane": lane}
            if time.monotonic() >= deadline:
                raise Timeout(f"{card_id} still in doing after {timeout:g}s", card=card_id)
            db.close_all()
            time.sleep(max(0.01, min(interval, deadline - time.monotonic())))

    @serialized_write
    def record_cost(
        self, card_id, caller, model_id=None, usage=None, usd=None, duration_ms=0, billing=None, agent=None
    ):
        """Record one run's usage on a card; runs accumulate. `agent` (lead only when not the caller) attributes the
        run.
        Billing comes from the agent (api, plan, unknown) unless given."""
        self._auth(caller, card_id)
        name = agent or caller.name
        if name != caller.name:
            self._require_lead(caller, "record cost for another agent")
        usage = usage or C.Usage()
        if min(usage.as_dict().values()) < 0 or (usd is not None and (not math.isfinite(usd) or usd < 0)):
            raise Precondition("usage and usd must be finite and not negative")
        if billing is not None and billing not in C.BILLING:
            raise Precondition(f"billing must be one of {', '.join(C.BILLING)}")
        rec = self.agents().get(name, {})
        model_id = model_id or rec.get("model", "")
        prices = C.pricing(self.home)
        with self.lock():
            card = self.card(card_id)
            row = C.entry(
                model_id,
                usage,
                usd,
                duration_ms,
                prices,
                name,
                clock.iso(),
                billing=billing or rec.get("billing", "unknown"),
                provider=C.provider_for(model_id, prices, rec.get("provider", "")),
            )
            if any(v is not None and not math.isfinite(v) for v in (row.get("usd"), row.get("usd_equiv"))):
                raise Precondition("money must be finite")
            card.setdefault("costs", []).append(row)
            self._save_card(card)
            self.index.add_cost(self.uid, card_id, card.get("team", ""), name, row)
            self._record("cost", name, task=card_id, data=row)
        return {"card": card_id, "entry": row, "total": C.card_total(card)}

    def spend(self, by="provider", since=None, until=None, everywhere=False):
        """Spend grouped by provider, model, agent, team, card, billing, workspace, day or week; this workspace or
        all."""
        for card in self.cards():
            for row in card.get("costs", []):
                for key in ("usd", "usd_equiv"):
                    value = row.get(key)
                    if value is not None and (not math.isfinite(value) or value < 0):
                        raise Precondition("invalid-data: nonfinite or negative money")
        return self.index.spend(by=by, ws=None if everywhere else self.uid, since=since, until=until)

    def costs(self):
        """Cost totals per card, per team and for the workspace."""
        return C.totals(self.cards())

    def statuses(self, cards=None):
        cards = self.index.card_rows(self.uid) if cards is None else cards
        return S.statuses(self.agents(), cards, self.leases())

    def who(self, cards=None):
        agents, st = self.agents(), self.statuses(cards)
        seen = self.index.last_seen(self.uid)
        spend = {r["key"]: r["usd"] for r in self.index.spend(by="agent", ws=self.uid)["rows"]}
        out = {
            name: {
                **agents.get(name, {}),
                **({"last_seen": seen[name]} if name in seen else {}),
                **s,
                "spend_usd": spend.get(name, 0.0),
            }
            for name, s in st.items()
        }
        if self.lead in out:
            lease = self.leadlease().summary()
            out[self.lead].update(
                session=lease.get("session", ""),
                lead_fence=lease.get("fence", 0),
                lead_live=lease.get("live", False),
                lead_expires=lease.get("expires", ""),
            )
        return out

    def card_view(self, card, leases=None, agents=None):
        leases = self.leases() if leases is None else leases
        return {
            **card,
            "a2a_state": model.a2a_state(card),
            "approvals": self.approvals(card, agents),
            "cost": C.card_total(card),
            "lease": leases.get(card["id"]),
        }

    def snapshot(self, transcript_limit=200, messages_limit=50, transcript_since=None):
        """Everything a UI needs in one read-only dict. `version` changes on every board write (use it as an ETag);
        pass the last transcript `seq` as `transcript_since` to page the transcript."""
        version = self.version()
        cards = self.cards()
        leases, agents = self.leases(), self.agents()
        busy = {c["owner"]: c["id"] for c in cards if c["lane"] == "doing" and c.get("owner")}
        msgs = sorted(M.all_messages(self.dir), key=lambda m: (m.get("sent", ""), m.get("id", "")))[-messages_limit:]
        return {
            "schema": store.SCHEMA,
            "version": version,
            "etag": f'"{version["workspace"]}-{version["global"]}"',
            "workspace": self.name,
            "board": self.dir,
            "lead": self.lead,
            "settings": self.live_settings(),
            "warnings": self.warnings(),
            "gate_event": store.read_json(self._p("board.json")).get("gate_event") or {},
            "now": clock.iso(),
            "lead_lease": self.leadlease().summary(),
            "cards": [self.card_view(c, leases, agents) for c in cards],
            "teams": self.team_list(),
            "agents": self.who(cards),
            "leases": {k: {**v, "live": L.is_live(v)} for k, v in leases.items()},
            "claimable": self.claimable(cards=cards),
            "review_queue": self.claimable(for_review=True, cards=cards),
            "inbox": M.counts(self.dir, busy),
            "messages": msgs,
            "costs": C.totals(cards),
            "transcript": self.transcript.read(limit=transcript_limit, since=transcript_since),
        }


def parse_words(value):
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else value.replace(",", " ").split()
    return [x.strip() for x in items if x and x.strip()]


def _line(text):
    return model.line_text(T.redact(text), "note")


def _dumps(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _decision(allowed, reason, card="", holder=""):
    return {"allowed": allowed, "reason": reason, "card": card, "holder": holder}


def _raise_reasons(card_id, reasons, clash):
    if not reasons:
        return
    text = f"cannot claim {card_id}: " + "; ".join(r for _, r in reasons)
    codes = {c for c, _ in reasons}
    cls = IdentityError if 5 in codes else Precondition if 3 in codes else Denied
    hint = (
        f"raidlock claim {card_id} --take-over"
        if any("--take-over" in r for _, r in reasons)
        else f"raidlock check {' '.join(c['path'] for c in clash)}"
        if clash
        else f"raidlock up --join --card {card_id}"
        if 5 in codes
        else "raidlock claimable"
    )
    if clash:
        hint += "; ask the lead to narrow the held lock with raidlock edit " + clash[0]["card"] + " --locks <files>"
    raise cls(text, card=card_id, reasons=[r for _, r in reasons], conflicts=clash, hint=hint)


def _apply_setting(settings, key, value):
    text = str(value).strip().lower()
    if key == "lead_store":
        if text not in LD.STORES:
            raise Precondition(f"lead_store must be one of {', '.join(LD.STORES)}")
        settings["lead_store"] = text
    elif key == "auth":
        if text not in ("token", "off"):
            raise Precondition("auth must be token or off")
        settings["auth"] = text
    elif key in ("ttl_minutes", "hold_minutes"):
        settings[key] = _positive(value, key)
    elif key == "brief_max_tokens":
        settings["brief_max_tokens"] = int(_positive(value, "brief_max_tokens"))
    elif key.startswith("budget."):
        from ..budgets import _value

        value = _value(key, value)
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
    elif key in PR_SETTING_KEYS:
        from ..pr import setting_value

        value = setting_value(key, value)
        if value in ("", []):
            settings.pop(key, None)
        else:
            settings[key] = value
    elif key in ("require_review", "require_review.count"):
        settings["require_review"]["count"] = int(_positive(value, key, allow_zero=True))
    elif key in ("require_review.other_provider", "require_verify", *ENFORCE_KEYS):
        if text not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise Precondition(f"{key} must be true or false")
        if key != "require_review.other_provider":
            settings[key] = text in ("true", "1", "yes", "on")
        else:
            settings["require_review"]["other_provider"] = text in ("true", "1", "yes", "on")
    else:
        raise Precondition(f"unknown setting {key}", key=key)


def _positive(value, key, allow_zero=False):
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise Precondition(f"{key} must be a number", key=key) from None
    if not math.isfinite(n) or n < 0 or (n == 0 and not allow_zero):
        raise Precondition(f"{key} must be positive", key=key)
    return int(n) if n == int(n) else n
