"""Offline overhead measurements using disposable board state and fresh hook processes."""

import argparse
import importlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHARS_PER_TOKEN = 4


def size(text):
    return {
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "estimated_tokens": math.ceil(len(text) / CHARS_PER_TOKEN),
    }


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def listing(path):
    text = path.read_text(encoding="utf-8").split("\n---", 1)[0]
    fields = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)
    return fields["name"] + ": " + fields["description"]


def check_budget(metrics, budget):
    if not isinstance(budget, dict) or not budget:
        raise ValueError("budget must be a nonempty object of metric names and numeric upper limits")
    failures = []
    for name, limit in budget.items():
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit < 0:
            raise ValueError(f"invalid budget for {name}")
        if name not in metrics:
            raise ValueError(f"unknown budget metric: {name}")
        if metrics[name] > limit:
            failures.append({"metric": name, "actual": metrics[name], "budget": limit})
    return failures


def summary(report):
    m = report["metrics"]
    return (
        f"Idle overhead: {m['idle.estimated_tokens']} estimated tokens per session "
        f"(skill and agent listings {m['idle.estimated_tokens'] - m['idle.hooks.estimated_tokens']}, "
        f"idle hook output {m['idle.hooks.estimated_tokens']}); with MCP tool schemas: "
        f"{m['idle.with_mcp.estimated_tokens']}. Tokenizer: {report['tokenizer']}, "
        f"{report['chars_per_token']} characters per estimated token."
    )


def markdown(report):
    lines = [summary(report), "", "| Metric | Value |", "| --- | ---: |"]
    lines.extend(f"| {name} | {value} |" for name, value in sorted(report["metrics"].items()))
    lines.append(f"\nBudget: {'FAIL' if report['failures'] else 'PASS'}")
    return "\n".join(lines)


def _invoke(args, env, cwd, payload=None):
    start = time.perf_counter_ns()
    result = subprocess.run(
        [sys.executable, "-m", "raidlock", *args],
        input=payload,
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=cwd,
        env=env,
        timeout=30,
        check=True,
    )
    elapsed = (time.perf_counter_ns() - start) / 1_000_000
    if result.stderr:
        raise RuntimeError(result.stderr)
    return result.stdout, elapsed


def _fixture(root, runs):
    from . import hooks
    from .core import Board, db
    from .core.identity import token_path
    from .mcp.server import Server, compact

    root = Path(root)
    workspace = root / "workspace"
    workspace.mkdir()
    board, secret = Board.init(workspace, lead="bench-lead")
    lead = board.caller(agent=board.lead, lead_secret=secret)
    board.new_card(lead, "Benchmark card", card_id="bench-card")
    texts, metrics = {}, {}
    env = dict(os.environ)
    base = {"cwd": str(workspace), "session_id": "bench-session", "tool_input": {}}
    cases = {f"{event}.allow": dict(base) for event in hooks.HANDLERS}
    cases["pre-write.allow"] = {**base, "tool_name": "Write", "tool_input": {"file_path": str(workspace / "free.txt")}}
    cases["pre-bash.allow"] = {**base, "tool_name": "Bash", "tool_input": {"command": "printf hello"}}
    cases["session-start.unbound"] = {**base, "session_id": "bench-other-session", "source": "startup"}
    cases["pre-write.deny"] = {
        **base,
        "tool_name": "Write",
        "tool_input": {"file_path": str(workspace / ".board/board.json")},
    }
    cases["pre-bash.deny"] = {**base, "tool_name": "Bash", "tool_input": {"command": "echo x > .board/board.json"}}
    cases["pre-read.deny"] = {
        **base,
        "agent_id": "bench-worker",
        "tool_name": "Read",
        "tool_input": {"file_path": token_path(board.home, board.uid, board.lead)},
    }
    for name, payload in cases.items():
        samples = []
        outputs = []
        _invoke(["hook", name.split(".")[0]], env, workspace, json.dumps(payload))
        for _ in range(runs):
            out, elapsed = _invoke(["hook", name.split(".")[0]], env, workspace, json.dumps(payload))
            if name.endswith(".deny"):
                decision = json.loads(out)["hookSpecificOutput"]["permissionDecision"]
                if decision != "deny" or "hook error" in out:
                    raise RuntimeError(f"invalid deny fixture: {name}: {out}")
            elif out:
                raise RuntimeError(f"allow fixture was not silent: {name}: {out}")
            samples.append(elapsed)
            outputs.append(out)
        texts[f"hook.{name}"] = max(outputs, key=len)
        metrics[f"hook.{name}.p50_ms"] = round(percentile(samples, 0.5), 3)
        metrics[f"hook.{name}.p95_ms"] = round(percentile(samples, 0.95), 3)
    commands = {
        "show": ["show"],
        "show_card": ["show", "bench-card"],
        "who": ["who"],
        "claimable": ["claimable"],
        "brief": ["brief", "bench-card"],
        "tokens": ["tokens"],
    }
    for name, args in commands.items():
        out, _ = _invoke([*args, "--json", "--board", str(workspace)], env, workspace)
        data = json.loads(out)
        if not data.get("ok"):
            raise RuntimeError(f"CLI fixture failed: {name}")
        texts[f"cli.{name}"] = out
    joined = board.join(lead, agent="bench-worker", runtime="claude-code")
    worker_args = ["--agent", "bench-worker", "--token", joined["token"]]
    for name, args in (
        ("claim", ["claim", "bench-card"]),
        ("note", ["note", "bench-card", "Benchmark evidence"]),
        ("move", ["move", "bench-card", "review", "--note", "Benchmark complete"]),
    ):
        out, _ = _invoke([*args, *worker_args, "--json", "--board", str(workspace)], env, workspace)
        data = json.loads(out)
        if not data.get("ok"):
            raise RuntimeError(f"CLI fixture failed: {name}")
        texts[f"cli.{name}"] = out
        if name == "claim":
            worker_args.extend(["--fence", str(data["fence"])])
    brief = board.brief("bench-card")
    texts["brief"] = brief["text"]
    metrics["brief.cap_tokens"] = brief["max_tokens"]
    metrics["brief.cap_ratio"] = brief["tokens"] / brief["max_tokens"]
    server = Server()
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
    texts["mcp.tools_list"] = compact(server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})) + "\n"
    db.close_all()
    return {"texts": texts, "metrics": metrics}


def measure(runs=20, plugin_root=None, tokenizer="auto"):
    if runs < 1:
        raise ValueError("runs must be positive")
    root = Path(plugin_root) if plugin_root else Path(__file__).resolve().parents[2]
    texts = {}
    for kind, pattern in (("skill", "skills/*/SKILL.md"), ("agent", "agents/*.md")):
        paths = sorted(root.glob(pattern))
        if not paths:
            raise ValueError(f"no {kind} listings under {root}; pass --plugin-root with a raidlock checkout")
        texts[f"listing.{kind}"] = "\n".join(listing(path) for path in paths)
    with tempfile.TemporaryDirectory(prefix="raid-bench-") as temporary:
        scratch = os.path.realpath(temporary)
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR") if key in os.environ}
        env.update(
            {
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PYTHONUTF8": "1",
                "RAIDLOCK_HOME": str(Path(scratch) / "state"),
                "CLAUDE_PLUGIN_DATA": str(Path(scratch) / "plugin"),
                "CLAUDE_CODE_SESSION_ID": "bench-session",
            }
        )
        code = (
            "import json,sys; from raidlock.bench import _fixture; "
            "print(json.dumps(_fixture(sys.argv[1], int(sys.argv[2]))))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, scratch, str(runs)],
            env=env,
            cwd=scratch,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60 + runs * 30,
            check=True,
        )
        fixture = json.loads(result.stdout)
    texts.update(fixture["texts"])
    metrics = fixture["metrics"]
    encoding = None
    if tokenizer == "auto":
        from unittest.mock import patch

        try:
            tiktoken = importlib.import_module("tiktoken")

            with patch("tiktoken.load.read_file", side_effect=OSError("benchmark is offline")):
                encoding = tiktoken.get_encoding("cl100k_base")
        except (ImportError, OSError):
            pass
    for name, text in texts.items():
        metrics.update({f"{name}.{unit}": value for unit, value in size(text).items()})
        if encoding:
            metrics[f"{name}.tiktoken_tokens"] = len(encoding.encode(text, disallowed_special=()))
    listings = sum(metrics[f"listing.{kind}.estimated_tokens"] for kind in ("skill", "agent"))
    idle_hooks = sum(
        metrics[f"{name}.estimated_tokens"] for name in texts if name.startswith("hook.") and not name.endswith(".deny")
    )
    metrics["idle.hooks.estimated_tokens"] = idle_hooks
    metrics["idle.estimated_tokens"] = listings + idle_hooks
    metrics["idle.with_mcp.estimated_tokens"] = listings + idle_hooks + metrics["mcp.tools_list.estimated_tokens"]
    return {
        "schema_version": 1,
        "runs": runs,
        "chars_per_token": CHARS_PER_TOKEN,
        "tokenizer": "cl100k_base" if encoding else "estimate",
        "python": platform.python_version(),
        "platform": platform.system(),
        "metrics": metrics,
        "failures": [],
    }


def setup(parser):
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--plugin-root", type=Path)
    parser.add_argument("--tokenizer", choices=("estimate", "auto"), default="auto")


def execute(args):
    report = measure(args.runs, args.plugin_root, args.tokenizer)
    if args.budget:
        report["failures"] = check_budget(report["metrics"], json.loads(args.budget.read_text(encoding="utf-8")))
    return report


def register_cli(add_command):
    def handler(ctx, args):
        from .core.errors import Precondition

        try:
            report = execute(args)
        except (ValueError, OSError) as exc:
            raise Precondition(str(exc)) from exc
        return {**report, "_exit": 1 if report["failures"] else 0}, json.dumps(report, indent=2) + "\n\n" + markdown(
            report
        )

    add_command("bench", "measure idle and per-action overhead offline", setup, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    setup(parser)
    args = parser.parse_args(argv)
    try:
        report = execute(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    print(markdown(report), file=sys.stderr)
    return 1 if report["failures"] else 0
