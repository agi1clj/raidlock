import json
import math
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import clock, repos
from .errors import Denied, Precondition

SCHEMA_VERSION = 1
BUSY_MS = 15000
DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces (
  uid TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, board TEXT UNIQUE NOT NULL, last_seen TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS leases (
  ws TEXT NOT NULL, card TEXT NOT NULL, agent TEXT NOT NULL, fence INTEGER NOT NULL, paths TEXT NOT NULL,
  keys TEXT NOT NULL, since TEXT NOT NULL, renewed TEXT NOT NULL, expires TEXT, checkout TEXT, branch TEXT,
  PRIMARY KEY (ws, card));
CREATE TABLE IF NOT EXISTS lease_keys (
  ws TEXT NOT NULL, card TEXT NOT NULL, ident TEXT NOT NULL, frel TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lease_keys_path ON lease_keys (ident, frel);
CREATE INDEX IF NOT EXISTS lease_keys_card ON lease_keys (ws, card);
CREATE TABLE IF NOT EXISTS cards (
  ws TEXT NOT NULL, id TEXT NOT NULL, lane TEXT NOT NULL, owner TEXT NOT NULL, team TEXT NOT NULL,
  doc TEXT, mtime INTEGER, size INTEGER, PRIMARY KEY (ws, id));
CREATE INDEX IF NOT EXISTS cards_team ON cards (ws, team, lane);
CREATE TABLE IF NOT EXISTS agents (
  ws TEXT NOT NULL, name TEXT NOT NULL, provider TEXT, runtime TEXT, model TEXT, display TEXT, last_seen TEXT,
  PRIMARY KEY (ws, name));
CREATE TABLE IF NOT EXISTS transcript (
  ws TEXT NOT NULL, seq INTEGER NOT NULL, message_id TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
  task TEXT NOT NULL, agent TEXT NOT NULL, recipient TEXT NOT NULL, segment INTEGER NOT NULL,
  offset INTEGER NOT NULL, length INTEGER NOT NULL, PRIMARY KEY (ws, seq), UNIQUE (ws, message_id));
CREATE INDEX IF NOT EXISTS transcript_task ON transcript (ws, task, seq);
CREATE INDEX IF NOT EXISTS transcript_agent ON transcript (ws, agent, seq);
CREATE INDEX IF NOT EXISTS transcript_recipient ON transcript (ws, recipient, seq);
CREATE TABLE IF NOT EXISTS costs (
  ws TEXT NOT NULL, card TEXT NOT NULL, team TEXT NOT NULL, agent TEXT NOT NULL, provider TEXT NOT NULL,
  model TEXT NOT NULL, billing TEXT NOT NULL, ts TEXT NOT NULL, input INTEGER NOT NULL, cached_input INTEGER NOT NULL,
  cache_write INTEGER NOT NULL, output INTEGER NOT NULL, reasoning_output INTEGER NOT NULL, tokens INTEGER NOT NULL,
  usd REAL, usd_equiv REAL, duration_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS costs_ws_ts ON costs (ws, ts);
CREATE INDEX IF NOT EXISTS costs_ts ON costs (ts);
"""
SPEND_KEYS = ("provider", "model", "agent", "team", "card", "billing", "workspace", "day", "week")
SPEND_COLUMNS = (
    "ROUND(COALESCE(SUM(c.usd), 0), 6) AS usd, ROUND(COALESCE(SUM(c.usd_equiv), 0), 6) AS usd_equiv, "
    "SUM(c.tokens) AS tokens, SUM(c.input) AS input, SUM(c.cached_input) AS cached_input, "
    "SUM(c.cache_write) AS cache_write, SUM(c.output) AS output, "
    "SUM(c.reasoning_output) AS reasoning_output, COUNT(*) AS runs, "
    "SUM(CASE WHEN c.usd IS NULL THEN 1 ELSE 0 END) AS unpriced_runs "
)
SPEND_FROM = (
    "FROM costs c LEFT JOIN workspaces w ON w.uid = c.ws "
    "WHERE (? IS NULL OR c.ws = ?) AND (? IS NULL OR c.ts >= ?) AND (? IS NULL OR c.ts < ?) "
)
SPEND_GROUPED = (
    "SELECT CASE ? WHEN 'provider' THEN c.provider WHEN 'model' THEN c.model WHEN 'agent' THEN c.agent "
    "WHEN 'team' THEN c.team WHEN 'card' THEN c.card WHEN 'billing' THEN c.billing "
    "WHEN 'workspace' THEN COALESCE(w.name, c.ws) WHEN 'day' THEN substr(c.ts, 1, 10) "
    "WHEN 'week' THEN strftime('%Y-W%W', substr(c.ts, 1, 19)) END AS key, "
    + SPEND_COLUMNS
    + SPEND_FROM
    + "GROUP BY key ORDER BY usd DESC, key"
)
SPEND_TOTAL = "SELECT " + SPEND_COLUMNS + SPEND_FROM
WORKSPACE_TABLES = (
    "DELETE FROM leases WHERE ws = ?",
    "DELETE FROM lease_keys WHERE ws = ?",
    "DELETE FROM cards WHERE ws = ?",
    "DELETE FROM agents WHERE ws = ?",
    "DELETE FROM transcript WHERE ws = ?",
    "DELETE FROM costs WHERE ws = ?",
    "DELETE FROM workspaces WHERE uid = ?",
)
CANDIDATES = (
    "SELECT DISTINCT l.ws, l.card, l.agent, l.expires, l.keys, w.name, w.board FROM lease_keys k "
    "JOIN leases l ON l.ws = k.ws AND l.card = k.card LEFT JOIN workspaces w ON w.uid = l.ws "
    "WHERE k.ident = ? AND (k.frel IN (SELECT value FROM json_each(?)) "
    "OR (? = '' OR (k.frel >= ? AND k.frel < ?))) AND (l.expires IS NULL OR l.expires > ?)"
)
HOLDERS = (
    "SELECT DISTINCT l.*, w.name AS wsname FROM lease_keys k JOIN leases l ON l.ws = k.ws AND l.card = k.card "
    "LEFT JOIN workspaces w ON w.uid = l.ws WHERE k.ident = ? AND k.frel IN (SELECT value FROM json_each(?)) "
    "AND (l.expires IS NULL OR l.expires > ?)"
)

_local = threading.local()


def _connect(path):
    cache = getattr(_local, "conns", None)
    if cache is None:
        cache = _local.conns = {}
    conn = cache.get(path)
    if conn is None:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        conn = sqlite3.connect(path, timeout=BUSY_MS / 1000, isolation_level=None, check_same_thread=True)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout={BUSY_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=OFF")
            _migrate(conn)
        except BaseException:
            conn.close()
            raise
        cache[path] = conn
    return conn


def close_all():
    """Close this thread's cached connections; the next access reopens them."""
    cache = getattr(_local, "conns", None) or {}
    while cache:
        _, conn = cache.popitem()
        conn.close()


def open_count():
    """How many connections this thread holds open."""
    return len(getattr(_local, "conns", None) or {})


@contextmanager
def released():
    """Run a block and close this thread's cached connections afterwards, even on error."""
    try:
        yield
    finally:
        close_all()


def purge(conn, uid):
    """Delete every index row of one workspace."""
    for statement in WORKSPACE_TABLES:
        conn.execute(statement, (uid,))


def _migrate(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if row and row[0] > SCHEMA_VERSION:
            raise Precondition(f"raidlock.db has schema {row[0]}; upgrade raidlock")
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('fence', 0)")
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('version', 0)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def ancestors(rel):
    parts = [p for p in rel.split("/") if p]
    return [""] + ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _lease_row(row):
    return {
        "card": row["card"],
        "agent": row["agent"],
        "fence": row["fence"],
        "paths": json.loads(row["paths"]),
        "keys": json.loads(row["keys"]),
        "since": row["since"],
        "renewed": row["renewed"],
        "expires": row["expires"],
        "checkout": json.loads(row["checkout"]) if row["checkout"] else None,
        "branch": row["branch"] or "",
    }


class Index:
    """Global index in `<home>/raidlock.db`: workspaces, leases + path keys, fence counter, card and agent mirrors,
    transcript index, and version counters. SQLite WAL; grants run in BEGIN IMMEDIATE transactions."""

    def __init__(self, home):
        self.path = os.path.join(home, "raidlock.db")

    @property
    def conn(self):
        return _connect(self.path)

    @contextmanager
    def write(self):
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def _bump(self, conn, ws):
        conn.execute("UPDATE workspaces SET version = version + 1 WHERE uid = ?", (ws,))
        conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'version'")

    def bump(self, ws):
        with self.write() as conn:
            self._bump(conn, ws)

    def versions(self, ws):
        row = self.conn.execute("SELECT version FROM workspaces WHERE uid = ?", (ws,)).fetchone()
        glob = self.conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]
        return {"workspace": row[0] if row else 0, "global": glob}

    def register(self, ws, board, stem):
        board = os.path.realpath(board)
        with self.write() as conn:
            row = conn.execute("SELECT name FROM workspaces WHERE uid = ? OR board = ?", (ws, board)).fetchone()
            if row:
                name = row["name"]
                conn.execute("DELETE FROM workspaces WHERE board = ? AND uid != ?", (board, ws))
                conn.execute(
                    "INSERT INTO workspaces(uid, name, board, last_seen) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(uid) DO UPDATE SET board = excluded.board, last_seen = excluded.last_seen",
                    (ws, name, board, clock.iso()),
                )
                return name
            name, i = stem or "board", 2
            while conn.execute("SELECT 1 FROM workspaces WHERE name = ?", (name,)).fetchone():
                name, i = f"{stem}-{i}", i + 1
            conn.execute(
                "INSERT INTO workspaces(uid, name, board, last_seen) VALUES (?, ?, ?, ?)",
                (ws, name, board, clock.iso()),
            )
            return name

    def workspaces(self):
        rows = self.conn.execute("SELECT uid, name, board, last_seen, version FROM workspaces ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def unregister(self, names):
        gone = []
        with self.write() as conn:
            for name in names:
                row = conn.execute("SELECT uid FROM workspaces WHERE name = ?", (name,)).fetchone()
                if row:
                    purge(conn, row["uid"])
                    gone.append(name)
        return gone

    def registered_name(self, ws, board):
        row = self.conn.execute(
            "SELECT name FROM workspaces WHERE uid = ? AND board = ?", (ws, os.path.realpath(board))
        ).fetchone()
        return row["name"] if row else None

    def touch_agent(self, ws, name):
        with self.write() as conn:
            conn.execute("UPDATE agents SET last_seen = ? WHERE ws = ? AND name = ?", (clock.iso(), ws, name))

    def last_seen(self, ws):
        rows = self.conn.execute("SELECT name, last_seen FROM agents WHERE ws = ?", (ws,)).fetchall()
        return {r["name"]: r["last_seen"] for r in rows if r["last_seen"]}

    def workspace_name(self, ws):
        row = self.conn.execute("SELECT name FROM workspaces WHERE uid = ?", (ws,)).fetchone()
        return row["name"] if row else ws

    def lease(self, ws, card):
        row = self.conn.execute("SELECT * FROM leases WHERE ws = ? AND card = ?", (ws, card)).fetchone()
        return _lease_row(row) if row else None

    def leases(self, ws):
        rows = self.conn.execute("SELECT * FROM leases WHERE ws = ? ORDER BY card", (ws,)).fetchall()
        return {r["card"]: _lease_row(r) for r in rows}

    def conflicts(self, keys, exclude_ws="", exclude_card="", conn=None, now=None, advisory=False):
        """Live leases overlapping any key, via the (ident, frel) index: ancestors by equality, descendants by range."""
        conn = conn or self.conn
        now = now or clock.iso()
        out, seen = [], set()
        for key in keys:
            for row in self._candidates(conn, key, now):
                if (row["ws"], row["card"]) == (exclude_ws, exclude_card):
                    continue
                other = {"path": "", "id": key["id"], "rel": "", "ci": False}
                for k in json.loads(row["keys"]):
                    if bool(k.get("advisory")) == advisory and repos.keys_overlap(key, k):
                        other = k
                        break
                else:
                    continue
                marker = (row["ws"], row["card"], key["path"], other["path"])
                if marker in seen:
                    continue
                seen.add(marker)
                out.append(
                    {
                        "path": key["path"],
                        "held": other["path"],
                        "card": row["card"],
                        "agent": row["agent"],
                        "workspace": row["name"] or row["ws"],
                        "board": row["board"] or "",
                        "expires": row["expires"],
                    }
                )
        return out

    def _candidates(self, conn, key, now):
        frel = key["rel"].casefold()
        anc = json.dumps(ancestors(frel))
        return conn.execute(CANDIDATES, (key["id"], anc, frel, frel + "/", frel + "0", now)).fetchall()

    def grant(self, ws, card, agent, paths, keys, fence, ttl_seconds, held=False, since=None, checkout=None):
        """Atomically check conflicts and write the lease. Mints a fence when `fence` is None. Raises Denied."""
        with self.write() as conn:
            clash = self.conflicts([k for k in keys if not k.get("advisory")], ws, card, conn=conn)
            if clash:
                from .leases import describe

                raise Denied(
                    f"lease denied for {card}:\n" + "\n".join("  " + describe(c) for c in clash),
                    card=card,
                    conflicts=clash,
                )
            if fence is None:
                conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'fence'")
                fence = conn.execute("SELECT value FROM meta WHERE key = 'fence'").fetchone()[0]
            old = conn.execute("SELECT since, checkout FROM leases WHERE ws = ? AND card = ?", (ws, card)).fetchone()
            ts = clock.iso()
            lease = {
                "card": card,
                "agent": agent,
                "fence": fence,
                "paths": list(paths),
                "keys": list(keys),
                "since": since or (old["since"] if old else ts),
                "renewed": ts,
                "expires": None if held else clock.later(ttl_seconds),
                "checkout": checkout or (json.loads(old["checkout"]) if old and old["checkout"] else None),
            }
            lease["branch"] = (lease["checkout"] or {}).get("branch", "")
            self._put(conn, ws, lease)
            self._bump(conn, ws)
            return lease

    def _put(self, conn, ws, lease):
        conn.execute("DELETE FROM lease_keys WHERE ws = ? AND card = ?", (ws, lease["card"]))
        conn.execute(
            "INSERT OR REPLACE INTO leases VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                ws,
                lease["card"],
                lease["agent"],
                lease["fence"],
                json.dumps(lease["paths"]),
                json.dumps(lease["keys"]),
                lease["since"],
                lease["renewed"],
                lease["expires"],
                json.dumps(lease["checkout"]) if lease.get("checkout") else None,
                lease.get("branch", ""),
            ),
        )
        conn.executemany(
            "INSERT INTO lease_keys VALUES (?,?,?,?)",
            [(ws, lease["card"], k["id"], k["rel"].casefold()) for k in lease["keys"]],
        )

    def put_lease(self, ws, lease, mint=False):
        """Insert a lease without a conflict check (migration). `mint` assigns a fresh fence."""
        with self.write() as conn:
            if mint:
                conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'fence'")
                lease["fence"] = conn.execute("SELECT value FROM meta WHERE key = 'fence'").fetchone()[0]
            self._put(conn, ws, lease)
            self._bump(conn, ws)
        return lease

    def update_lease(self, ws, card, **fields):
        allowed = {"renewed", "expires"}
        with self.write() as conn:
            if not conn.execute("SELECT 1 FROM leases WHERE ws = ? AND card = ?", (ws, card)).fetchone():
                return None
            if not set(fields) & allowed:
                return self.lease(ws, card)
            conn.execute(
                "UPDATE leases SET renewed = CASE WHEN ? THEN ? ELSE renewed END, "
                "expires = CASE WHEN ? THEN ? ELSE expires END WHERE ws = ? AND card = ?",
                (
                    "renewed" in fields,
                    fields.get("renewed"),
                    "expires" in fields,
                    fields.get("expires"),
                    ws,
                    card,
                ),
            )
            self._bump(conn, ws)
        return self.lease(ws, card)

    def drop_lease(self, ws, card):
        with self.write() as conn:
            n = conn.execute("DELETE FROM leases WHERE ws = ? AND card = ?", (ws, card)).rowcount
            conn.execute("DELETE FROM lease_keys WHERE ws = ? AND card = ?", (ws, card))
            if n:
                self._bump(conn, ws)
        return bool(n)

    def holders_of(self, loc, now=None):
        """Live leases (any workspace) whose keys contain the located path `loc`."""
        frel = loc["rel"].casefold()
        rows = self.conn.execute(HOLDERS, (loc["id"], json.dumps(ancestors(frel)), now or clock.iso())).fetchall()
        out = []
        for r in rows:
            lease = _lease_row(r)
            if any(not k.get("advisory") and repos.key_contains(k, loc) for k in lease["keys"]):
                out.append((r["wsname"] or r["ws"], r["ws"], lease))
        return out

    @staticmethod
    def _card_row(ws, card, stat):
        return (
            ws,
            card["id"],
            card["lane"],
            card.get("owner", ""),
            card.get("team", ""),
            json.dumps(card, sort_keys=True, ensure_ascii=False) if stat else None,
            stat[0] if stat else None,
            stat[1] if stat else None,
        )

    def put_card(self, ws, card, stat=None):
        """Mirror a card; `stat` is (mtime_ns, size) of its file so reads can reuse the cached document."""
        with self.write() as conn:
            conn.execute("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?)", self._card_row(ws, card, stat))
            self._bump(conn, ws)

    def put_cards(self, ws, cards, stats=None, replace=True, bump=True):
        stats = stats or {}
        with self.write() as conn:
            if replace:
                conn.execute("DELETE FROM cards WHERE ws = ?", (ws,))
            conn.executemany(
                "INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?)",
                [self._card_row(ws, c, stats.get(c["id"])) for c in cards],
            )
            if bump:
                self._bump(conn, ws)

    def card_doc(self, ws, card_id):
        row = self.conn.execute("SELECT doc, mtime, size FROM cards WHERE ws = ? AND id = ?", (ws, card_id)).fetchone()
        return (row["doc"], row["mtime"], row["size"]) if row else (None, None, None)

    def card_docs(self, ws):
        rows = self.conn.execute("SELECT id, doc, mtime, size FROM cards WHERE ws = ?", (ws,)).fetchall()
        return {r["id"]: (r["doc"], r["mtime"], r["size"]) for r in rows}

    def card_count(self, ws):
        return self.conn.execute("SELECT COUNT(*) FROM cards WHERE ws = ?", (ws,)).fetchone()[0]

    def doing_in_team(self, ws, team, exclude):
        return self.conn.execute(
            "SELECT COUNT(*) FROM cards WHERE ws = ? AND team = ? AND lane = 'doing' AND id != ?", (ws, team, exclude)
        ).fetchone()[0]

    def lanes(self, ws, ids):
        if not ids:
            return {}
        rows = self.conn.execute(
            "SELECT id, lane FROM cards WHERE ws = ? AND id IN (SELECT value FROM json_each(?))", (ws, json.dumps(ids))
        ).fetchall()
        return {r["id"]: r["lane"] for r in rows}

    def card_rows(self, ws):
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT id, lane, owner, team FROM cards WHERE ws = ? ORDER BY id", (ws,)
            ).fetchall()
        ]

    def put_agent(self, ws, name, rec):
        with self.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agents VALUES (?,?,?,?,?,?,?)",
                (
                    ws,
                    name,
                    rec.get("provider", ""),
                    rec.get("runtime", ""),
                    rec.get("model", ""),
                    rec.get("display", ""),
                    rec.get("last_seen", ""),
                ),
            )
            self._bump(conn, ws)

    def next_seq(self, conn, ws):
        row = conn.execute("SELECT MAX(seq) FROM transcript WHERE ws = ?", (ws,)).fetchone()
        return (row[0] or 0) + 1

    def has_message(self, ws, message_id):
        return (
            self.conn.execute("SELECT 1 FROM transcript WHERE ws = ? AND message_id = ?", (ws, message_id)).fetchone()
            is not None
        )

    def add_transcript(self, ws, rows):
        """Index transcript records [(message_id, ts, kind, task, agent, recipient, segment, offset, length)]."""
        with self.write() as conn:
            seq = self.next_seq(conn, ws)
            out = []
            for r in rows:
                cur = conn.execute("INSERT OR IGNORE INTO transcript VALUES (?,?,?,?,?,?,?,?,?,?,?)", (ws, seq, *r))
                if cur.rowcount:
                    out.append(seq)
                    seq += 1
            self._bump(conn, ws)
        return out

    def query_transcript(self, ws, task=None, kind=None, agent=None, since=None, limit=None):
        sql, args = "SELECT * FROM transcript WHERE ws = ?", [ws]
        if task:
            sql, args = sql + " AND task = ?", args + [task]
        if kind:
            sql, args = sql + " AND kind = ?", args + [kind]
        if agent:
            sql, args = sql + " AND (agent = ? OR recipient = ?)", args + [agent, agent]
        if since is not None:
            sql, args = sql + " AND seq > ?", args + [int(since)]
            sql += " ORDER BY seq" + (f" LIMIT {int(limit)}" if limit else "")
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]
        sql += " ORDER BY seq DESC" + (f" LIMIT {int(limit)}" if limit else "")
        return [dict(r) for r in reversed(self.conn.execute(sql, args).fetchall())]

    def transcript_count(self, ws):
        return self.conn.execute("SELECT COUNT(*) FROM transcript WHERE ws = ?", (ws,)).fetchone()[0]

    def clear_transcript(self, ws):
        with self.write() as conn:
            conn.execute("DELETE FROM transcript WHERE ws = ?", (ws,))

    def add_cost(self, ws, card, team, agent, row):
        u = row["usage"]
        with self.write() as conn:
            conn.execute(
                "INSERT INTO costs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ws,
                    card,
                    team or "",
                    agent,
                    row.get("provider", ""),
                    row.get("model", ""),
                    row.get("billing", "unknown"),
                    row["ts"],
                    u.get("input", 0),
                    u.get("cached_input", 0),
                    u.get("cache_write", 0),
                    u.get("output", 0),
                    u.get("reasoning_output", 0),
                    row["tokens"],
                    row.get("usd"),
                    row.get("usd_equiv"),
                    row.get("duration_ms", 0),
                ),
            )
            self._bump(conn, ws)

    def rebuild_costs(self, ws, cards):
        with self.write() as conn:
            conn.execute("DELETE FROM costs WHERE ws = ?", (ws,))
        for c in cards:
            for row in c.get("costs", []):
                self.add_cost(
                    ws,
                    c["id"],
                    c.get("team", ""),
                    row.get("by", ""),
                    {"usage": {}, "tokens": 0, "billing": "unknown", **row},
                )

    def cost_count(self, ws):
        return self.conn.execute("SELECT COUNT(*) FROM costs WHERE ws = ?", (ws,)).fetchone()[0]

    def spend(self, by="provider", ws=None, since=None, until=None):
        """Grouped spend: [{key, usd, usd_equiv, tokens, input, cached_input, cache_write, output, reasoning_output,
        runs, unpriced_runs}] plus a total. `ws` None means every workspace."""
        if by not in SPEND_KEYS:
            raise Precondition(f"--by must be one of {', '.join(SPEND_KEYS)}", by=by)
        args = (ws, ws, since, since, until, until)
        values = self.conn.execute("SELECT c.usd, c.usd_equiv " + SPEND_FROM, args)
        for row in values:
            if any(v is not None and (not math.isfinite(v) or v < 0) for v in row):
                raise Precondition("invalid-data: nonfinite or negative money")
        rows = self.conn.execute(SPEND_GROUPED, (by, *args)).fetchall()
        total = self.conn.execute(SPEND_TOTAL, args).fetchone()
        zero = dict.fromkeys(total.keys(), 0)
        return {
            "by": by,
            "rows": [dict(r) for r in rows],
            "total": {k: (v if v is not None else zero[k]) for k, v in dict(total).items()},
        }
