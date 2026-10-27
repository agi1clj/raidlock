# Demo raid

A scripted, deterministic raid across two fictional workspaces, `acme-shop` and `weather-api`. No model is called: the "agents" are a Python script and shell commands, and their token usage is fixed.

```bash
bash examples/demo/run.sh            # or: bash examples/demo/run.sh /empty/folder
```

It needs `raidlock` and `git` on `PATH`, plus the verify sandbox: bubblewrap (`bwrap`) on Linux or `sandbox-exec` on macOS. Everything lands in one temporary folder with its own `RAIDLOCK_HOME`, so your real boards are untouched. Set `DEMO_PAUSE=2` to slow it down while `raid ui` is open.

What happens:

1. The lead creates a board in each workspace.
2. In `acme-shop`, the lead plans `cart-discount` with two lock paths and a verify command.
3. `raid delegate cart-discount --to shell` runs [builder.py](builder.py) in a remote-less clone. The runner diffs the clone against the locks, runs verify in the sandbox, records usage for `claude-sonnet-5-5` and moves the quest to review with a patch.
4. `astra-reviewer`, a Codex CLI agent on `gpt-6-astra`, approves it. The board's review gate wants one approval from another provider.
5. The lead applies the patch on `main`, releases the runner's lease, runs `raid verify` (a signed receipt bound to the commit SHA) and closes the quest with `raid done`.
6. In `weather-api`, `sonnet-builder` claims `forecast-cache` in its own clone.
7. It tries to write `src/forecast/api.py`, outside its lease: `raid check --write` refuses with exit 2.
8. The lead sends an urgent message; the worker reads it with `raid inbox`.
9. The worker commits, records its spend and moves the quest to review.
10. `astra-reviewer` approves.
11. The lead hands its session over (`raid lead handover --to demo-lead-session-2`). The new session presents the same secret file path, whose secret has rotated.
12. The new lead session verifies in the worker's clone and closes the quest.
13. `raid spend --by model --all` prints spend per model across both workspaces.

To browse the result, run the `raidlock ui` line the script prints at the end. [record.py](record.py) records the README animation from the same run; [docs/assets/README.md](../../docs/assets/README.md) has the command.
