from html import escape

LANES = (
    ("review", "Review"),
    ("blocked", "Blocked"),
    ("doing", "In progress"),
    ("todo", "To do"),
    ("done", "Done"),
    ("cancelled", "Cancelled"),
)
STYLE = (
    "body{font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;"
    "color:#1d1d24;background:#fafafa}"
    "h1{font-size:22px}h2{font-size:16px;margin-top:24px}table{border-collapse:collapse;width:100%}"
    "td,th{border-bottom:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top;overflow-wrap:anywhere}"
    ".m{color:#666;font-size:12px}.wrap{overflow-x:auto}"
)


def _usd(v):
    return "-" if v is None else f"${float(v):.2f}"


def _rows(items, cols):
    head = "".join(f"<th>{escape(c)}</th>" for c, _ in cols)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(f(i)))}</td>" for _, f in cols) + "</tr>" for i in items)
    return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _trust(agent):
    inputs = agent.get("reputation_inputs") or {}
    measured = any(v for k, v in inputs.items() if not k.endswith("_rate") and isinstance(v, (int, float)))
    return agent.get("reputation") if measured and agent.get("reputation") is not None else ""


def _billing(agent):
    mode = agent.get("billing") or ""
    return "" if mode == "unknown" else mode


def _known(value):
    return "not reported" if value in (None, "", "unknown") else value


def _usage(p):
    parts = []
    ctx = p.get("context") or {}
    if ctx.get("used_percent") is not None:
        parts.append(f"context {ctx['used_percent']:g}%")
    parts += [f"{w.get('window') or 'window'} {w.get('used_percent')}%" for w in p.get("windows") or []]
    return "; ".join(parts)


def render(payload, records):
    ws = payload.get("workspace", "")
    out = [
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>raidlock snapshot: {escape(ws)}</title><style>{STYLE}</style></head><body>",
        f'<h1>{escape(ws)}</h1><p class="m">Snapshot taken {escape(payload.get("now", ""))}. '
        f"Secrets and local paths are redacted.</p>",
    ]
    total = payload.get("treasury", {}).get("total", {})
    out.append(
        f"<p>Spend {_usd(total.get('usd'))} over {total.get('tokens', 0)} tokens in {total.get('runs', 0)} runs.</p>"
    )
    out.append("<h2>Agents</h2>")
    out.append(
        _rows(
            sorted(payload.get("agents", {}).items()),
            [
                ("Agent", lambda a: a[0]),
                ("Status", lambda a: a[1].get("label", "")),
                ("Card", lambda a: a[1].get("card", "")),
                ("Model", lambda a: " · ".join(x for x in (a[1].get("model"), a[1].get("runtime")) if x)),
                ("Billing", lambda a: _billing(a[1])),
                ("Trust", lambda a: _trust(a[1])),
            ],
        )
    )
    limits = [p for p in (payload.get("limits") or {}).get("providers", []) if _usage(p)]
    if limits:
        out.append("<h2>Provider usage</h2>")
        out.append(
            _rows(
                limits,
                [
                    ("Provider", lambda p: p.get("provider", "")),
                    ("Plan", lambda p: p.get("plan") or ""),
                    ("Usage", _usage),
                    ("Updated", lambda p: p.get("observed_at") or ""),
                ],
            )
        )
    cards = payload.get("cards", [])
    for lane, label in LANES:
        mine = [c for c in cards if c.get("lane") == lane]
        if not mine:
            continue
        out.append(f"<h2>{escape(label)} ({len(mine)})</h2>")
        out.append(
            _rows(
                mine,
                [
                    ("Card", lambda c: c["id"]),
                    ("Title", lambda c: c.get("title", "")),
                    ("Owner", lambda c: c.get("owner", "")),
                    ("Team", lambda c: c.get("team", "")),
                    ("Cost", lambda c: _usd((c.get("cost") or {}).get("usd"))),
                ],
            )
        )
    out.append("<h2>By provider and model</h2>")
    out.append(
        _rows(
            payload.get("scorecard", []),
            [
                ("Provider", lambda g: _known(g["provider"])),
                ("Model", lambda g: _known(g["label"])),
                ("Done", lambda g: g["done"]),
                ("Review pass", lambda g: "-" if g["pass_rate"] is None else f"{round(g['pass_rate'] * 100)}%"),
                ("Cost", lambda g: _usd(g["usd"])),
            ],
        )
    )
    if payload.get("model_reputation"):
        out.append("<h2>Model trust scores</h2>")
        out.append(
            _rows(
                payload["model_reputation"],
                [
                    ("Workspace", lambda r: r["workspace"]),
                    ("Model", lambda r: _known(r.get("label", r["key"]))),
                    ("Score / 100", lambda r: r["score"]),
                    ("Inputs", lambda r: r["inputs"]),
                ],
            )
        )
    out.append("<h2>Recent activity</h2>")
    out.append(
        _rows(
            list(reversed(records)),
            [
                ("Time", lambda r: r["ts"]),
                ("From", lambda r: r["from"]["agent"]),
                ("Kind", lambda r: r["kind"]),
                ("Card", lambda r: r["task"]),
                ("Text", lambda r: r["text"][:500]),
            ],
        )
    )
    out.append("</body></html>")
    return "".join(out)
