import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from raidlock.bench import check_budget, measure, percentile, size

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def report():
    return measure(runs=3, plugin_root=ROOT, tokenizer="estimate")


SHARED_RUNNER_SLACK = 3


def test_ci_budget(report):
    budget = json.loads((Path(__file__).with_name("budget.json")).read_text())
    shared = {k: v * SHARED_RUNNER_SLACK if k.endswith("_ms") else v for k, v in budget.items()}
    assert check_budget(report["metrics"], shared) == []
    assert report["runs"] == 3
    assert report["metrics"]["idle.estimated_tokens"] > 0


def test_hook_cases_and_latency(report):
    from raidlock.hooks import HANDLERS

    metrics = report["metrics"]
    for event in HANDLERS:
        assert metrics[f"hook.{event}.allow.bytes"] == 0
        assert 0 < metrics[f"hook.{event}.allow.p50_ms"] <= metrics[f"hook.{event}.allow.p95_ms"]
    for event in ("pre-read", "pre-write", "pre-bash"):
        assert metrics[f"hook.{event}.deny.bytes"] > 0
        assert metrics[f"hook.{event}.deny.p50_ms"] <= metrics[f"hook.{event}.deny.p95_ms"]


def test_budget_boundaries_and_validation():
    assert check_budget({"x": 4}, {"x": 4}) == []
    assert check_budget({"x": 5}, {"x": 4}) == [{"metric": "x", "actual": 5, "budget": 4}]
    for invalid in ({}, [], {"missing": 1}, {"x": -1}, {"x": True}, {"x": float("nan")}):
        with pytest.raises(ValueError):
            check_budget({"x": 1}, invalid)


def test_size_and_nearest_rank():
    assert size("éabcd") == {"bytes": 6, "chars": 5, "estimated_tokens": 2}
    assert percentile(list(range(1, 21)), 0.5) == 10
    assert percentile(list(range(1, 21)), 0.95) == 19
    with pytest.raises(ValueError):
        measure(runs=0)


def test_cli_budget_failure_and_script(tmp_path):
    budget = tmp_path / "budget.json"
    budget.write_text('{"idle.estimated_tokens": 0}')
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for command in (["-m", "raidlock", "bench", "--json"], [str(ROOT / "scripts/bench_overhead.py")]):
        result = subprocess.run(
            [sys.executable, *command, "--runs", "1", "--budget", str(budget), "--tokenizer", "estimate"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 1, result.stderr
        assert json.loads(result.stdout)["failures"][0]["metric"] == "idle.estimated_tokens"
        if command[0] != "-m":
            assert "| Metric | Value |" in result.stderr


def test_idle_totals_add_listings_hooks_and_mcp(report):
    from raidlock.bench import markdown

    m = report["metrics"]
    listings = m["listing.skill.estimated_tokens"] + m["listing.agent.estimated_tokens"]
    assert m["idle.hooks.estimated_tokens"] == 0
    assert m["hook.session-start.unbound.bytes"] == 0
    assert m["idle.estimated_tokens"] == listings + m["idle.hooks.estimated_tokens"]
    assert m["idle.with_mcp.estimated_tokens"] == m["idle.estimated_tokens"] + m["mcp.tools_list.estimated_tokens"]
    first = markdown(report).splitlines()[0]
    assert first.startswith(f"Idle overhead: {m['idle.estimated_tokens']} estimated tokens per session")
    assert f"with MCP tool schemas: {m['idle.with_mcp.estimated_tokens']}" in first
