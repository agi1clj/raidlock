import contextlib
import json
import os
import re
import uuid

from . import clock

KINDS = ("msg", "relay", "handback", "note", "status", "cost", "artifact", "review", "verify")
REDACTED = "[REDACTED]"
_PATTERNS = [
    re.compile(r"(?<![\w-])t[A-Za-z0-9_-]{32}(?![\w-])"),
    re.compile(r"(?<![\w.])\d+\.[A-Za-z0-9_-]{43}(?![\w-])"),
    re.compile(r"\b[a-z0-9-]{1,64}:\d+:[a-f0-9]{40}\b"),
    re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]+"),
    re.compile(r"\b(?:npm|hf)_[A-Za-z0-9]{10,}"),
    re.compile(r"\bSG\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql)://[^\s/@]+:[^\s/@]+@[^\s'\"]+"),
    re.compile(r"https://hooks\.slack\.com/services/[^\s'\"]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*"),
    re.compile(
        r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|AccountKey|RAIDLOCK_CAP)[A-Za-z0-9_]*)"
        r"([\"\x27]?\s*[:=]\s*)(['\"]?)[^\s'\",;]{4,}\3"
    ),
]


def redact(text):
    """Replace secret-looking substrings with [REDACTED]."""
    if not isinstance(text, str) or not text:
        return text
    if re.search(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", text):
        text = re.sub(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])", REDACTED, text)
    for pattern in _PATTERNS[:-1]:
        text = pattern.sub(REDACTED, text)
    return _PATTERNS[-1].sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)


def _clean(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def text_parts(text):
    return [{"kind": "text", "text": text}] if text else []


def make(
    kind,
    sender,
    text="",
    task="",
    context="",
    to="",
    src="board",
    parts=None,
    artifacts=None,
    data=None,
    message_id=None,
    role="agent",
):
    """Build an A2A-shaped transcript record."""
    record = {
        "messageId": message_id or uuid.uuid4().hex,
        "ts": clock.iso(),
        "kind": kind,
        "role": role,
        "taskId": task,
        "contextId": context,
        "from": sender,
        "to": to,
        "parts": list(parts or []) + text_parts(text),
        "artifacts": list(artifacts or []),
        "src": src,
    }
    if data is not None:
        record["parts"].append({"kind": "data", "data": data})
    return record


def artifact(name, text="", data=None, kind=None):
    parts = text_parts(text) + ([{"kind": "data", "data": data}] if data is not None else [])
    out = {"artifactId": uuid.uuid4().hex, "name": name, "parts": parts}
    if kind:
        out["metadata"] = {"kind": kind}
    return out


SEGMENT_BYTES = 10 * 1024 * 1024


class Transcript:
    """Append-only transcript in `.board/transcript/NNNNNN.jsonl` segments (rotated at SEGMENT_BYTES), indexed in the
    global DB by sequence number, task, kind and agent. Callers hold the board lock when appending."""

    def __init__(self, board_dir, index, ws):
        self.dir = os.path.join(board_dir, "transcript")
        self.index = index
        self.ws = ws
        legacy = os.path.join(board_dir, "transcript.jsonl")
        if os.path.exists(legacy) and not os.path.exists(self.dir):
            os.makedirs(self.dir)
            os.replace(legacy, self._segment_path(1))
        if os.path.isdir(self.dir) and not index.transcript_count(ws) and self._segments():
            self.reindex()

    def _segment_path(self, n):
        return os.path.join(self.dir, f"{n:06d}.jsonl")

    def _segments(self):
        if not os.path.isdir(self.dir):
            return []
        return sorted(int(f[:-6]) for f in os.listdir(self.dir) if f.endswith(".jsonl") and f[:-6].isdigit())

    def _current(self, size):
        segs = self._segments() or [1]
        n = segs[-1]
        path = self._segment_path(n)
        if os.path.exists(path) and os.path.getsize(path) + size > SEGMENT_BYTES and os.path.getsize(path) > 0:
            n += 1
        return n

    def append(self, record):
        """Append a record (redacted). Returns the stored record with its `seq`, or None for a duplicate messageId."""
        if record.get("kind") not in KINDS:
            raise ValueError(f"unknown transcript kind {record.get('kind')!r}")
        record = _clean(dict(record))
        record.setdefault("messageId", uuid.uuid4().hex)
        record.setdefault("ts", clock.iso())
        if self.index.has_message(self.ws, record["messageId"]):
            return None
        data = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        os.makedirs(self.dir, exist_ok=True)
        n = self._current(len(data))
        with open(self._segment_path(n), "ab") as f:
            offset = f.seek(0, os.SEEK_END)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        seqs = self.index.add_transcript(self.ws, [_index_row(record, n, offset, len(data))])
        return {**record, "seq": seqs[0]} if seqs else None

    def read(self, task=None, kind=None, agent=None, limit=None, since=None):
        """Records in order. Without `since`: the newest `limit`. With `since` (a seq cursor): the next `limit` after
        it.
        Each record carries `seq`; pass the last one back as `since` to continue."""
        rows = self.index.query_transcript(self.ws, task=task, kind=kind, agent=agent, since=since, limit=limit)
        return self._load(rows)

    def _load(self, rows):
        out, handles = [], {}
        with contextlib.ExitStack() as stack:
            for r in rows:
                f = handles.get(r["segment"])
                if f is None:
                    f = handles[r["segment"]] = stack.enter_context(open(self._segment_path(r["segment"]), "rb"))
                f.seek(r["offset"])
                try:
                    out.append(_clean({**json.loads(f.read(r["length"]).decode("utf-8")), "seq": r["seq"]}))
                except ValueError:
                    continue
        return out

    def reindex(self):
        """Rebuild this workspace's index from the segment files."""
        self.index.clear_transcript(self.ws)
        rows = []
        for n in self._segments():
            offset = 0
            with open(self._segment_path(n), "rb") as f:
                for line in f:
                    try:
                        rec = json.loads(line.decode("utf-8"))
                        rows.append(_index_row(rec, n, offset, len(line)))
                    except ValueError:
                        pass
                    offset += len(line)
        if rows:
            self.index.add_transcript(self.ws, rows)
        return len(rows)


def _index_row(record, segment, offset, length):
    return (
        record.get("messageId", ""),
        record.get("ts", ""),
        record.get("kind", ""),
        record.get("taskId", "") or "",
        (record.get("from") or {}).get("agent", "") or "",
        record.get("to", "") or "",
        segment,
        offset,
        length,
    )
