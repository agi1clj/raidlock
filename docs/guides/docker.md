# Docker image

An optional, hardened container image for `raid ui`, `raid mcp` and sandboxed `raid verify`. The CLI, the Claude Code plugin hooks and the git guards stay on the host: they act on your checkouts and your agent sessions, which live outside any container.

The image runs `raid` as a fixed non-root user (uid and gid 10001), with `RAIDLOCK_HOME=/home/raid/.raidlock`, working directory `/workspace` and `RAIDLOCK_SANDBOX=container`. It holds the raidlock wheel, `git` and `bubblewrap`, and nothing else: no compilers, no pip.

## Get the image

From a release:

```bash
docker pull ghcr.io/agi1clj/raidlock:0.1.0
cosign verify ghcr.io/agi1clj/raidlock:0.1.0 \
  --certificate-identity-regexp '^https://github.com/agi1clj/raidlock/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Release images are built for `linux/amd64` and `linux/arm64`, carry an SBOM and build provenance, and are signed keyless with cosign. Or build from a checkout:

```bash
docker build -t raidlock .
```

The examples below use the local tag `raidlock`; substitute `ghcr.io/agi1clj/raidlock:0.1.0` for a release image.

## Hardened run flags

Use these flags for every container:

```bash
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:uid=10001,gid=10001,mode=1777 \
  --tmpfs /home/raid:uid=10001,gid=10001,mode=0700 \
  ...
```

| Flag | Why |
|---|---|
| `--read-only` | the image filesystem cannot change; only the mounts below are writable |
| `--cap-drop ALL` | no Linux capabilities, even for the non-root user |
| `--security-opt no-new-privileges` | no setuid escalation |
| `--tmpfs /tmp` | scratch space; `raid ui` fails requests without it |
| `--tmpfs /home/raid` | a private, throwaway home; mount a volume at `/home/raid/.raidlock` to keep state |
| `--network none` | for verify and MCP; `raid ui` needs only its published port |

Keep Docker's default seccomp profile. Never add `--privileged`, `--cap-add` or `--security-opt seccomp=unconfined`; verify does not need them.

## Web UI

`raid ui` binds `127.0.0.1` by default, which is unreachable from the host when it runs in a container. Inside this image only, `--host 0.0.0.0` is allowed (it is refused anywhere `RAIDLOCK_SANDBOX=container` is not set). Publish the port on the host's loopback only:

```bash
cd ~/code/shop
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:uid=10001,gid=10001,mode=1777 \
  --tmpfs /home/raid:uid=10001,gid=10001,mode=0700 \
  -v "$PWD:$PWD" -w "$PWD" \
  -p 127.0.0.1:8765:8765 \
  raidlock ui --host 0.0.0.0 --only
```

Open `http://127.0.0.1:8765/`. Always publish as `-p 127.0.0.1:PORT:PORT`: a bare `-p 8765:8765` exposes the board to your network. The `Host` header allowlist stays on, and it checks the container's port, so the host and container ports must match; for another port use `--port 9000 -p 127.0.0.1:9000:9000`.

The UI container gets the workspace (mounted at the same path as on the host) and no `RAIDLOCK_HOME`, so no tokens or lead secret enter it. `--only` serves that one board. On Linux, add `--user "$(id -u):$(id -g)"` and use that uid in both `--tmpfs` options when your files are not owned by uid 10001; Docker Desktop maps file ownership for you.

## MCP server

An MCP client starts the server over stdio. Example for Claude Code:

```bash
claude mcp add raidlock -- docker run -i --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges --network none \
  --tmpfs /tmp:uid=10001,gid=10001,mode=1777 \
  --tmpfs /home/raid:uid=10001,gid=10001,mode=0700 \
  -v /abs/path/to/shop:/abs/path/to/shop \
  -v "$HOME/.raidlock:/home/raid/.raidlock" \
  -e RAIDLOCK_AGENT=worker-1 -e RAIDLOCK_TOKEN \
  raidlock mcp --workspace /abs/path/to/shop
```

Unlike the UI, MCP checks tokens and writes board state, so it needs `RAIDLOCK_HOME` mounted read-write. That mount holds every token and lead secret file in it; the container adds no network and no writes outside the two mounts, nothing more. See [MCP server](mcp.md) for the identity variables.

## Verify in a container

`raid verify` is lead-only, and the lead lease is bound to a session process (see [Install](install.md#lead-session-handover)). A container is a separate process tree, so a lead on the host cannot run `raid verify` through `docker run`. Containerized verify works when the lead session itself runs in the container: a throwaway, hardened shell that holds the board, the checkouts and `RAIDLOCK_HOME`:

```bash
docker run -it --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --network none \
  --tmpfs /tmp:uid=10001,gid=10001,mode=1777 \
  --tmpfs /home/raid:uid=10001,gid=10001,mode=0700 \
  --tmpfs /workspace:uid=10001,gid=10001,mode=0700 \
  --entrypoint sh raidlock
```

On a Linux host with AppArmor (Ubuntu, Debian), also add `--security-opt apparmor=unconfined`; on Ubuntu 23.10 and later the host must also allow unprivileged user namespaces (`sysctl kernel.apparmor_restrict_unprivileged_userns=0`). Docker Desktop needs neither. Inside, follow [Getting started](getting-started.md) as usual; replace the `/workspace` tmpfs with a volume to keep the work. [scripts/docker-smoke.sh](../../scripts/docker-smoke.sh) runs the whole flow (demo board, worker claim, commit, verify, done) and is what CI runs.

How the sandbox works: verify still runs under bubblewrap, so it keeps the same isolation as on a Linux host: a read-only root, a private network namespace, separate PID, IPC and UTS namespaces, no capabilities, and the board, `RAIDLOCK_HOME`, agent homes and credential stores hidden behind empty tmpfs mounts. In the image (`RAIDLOCK_SANDBOX=container`), bubblewrap creates its own user namespace and gives the command an empty `/proc`, because Docker does not allow mounting a new one without extra privileges; the container's own processes stay invisible, so the command cannot reach the signing key or tokens through them. The receipt records `isolation: bwrap-container`. Container mode turns on only when `RAIDLOCK_SANDBOX=container` is set and the process really runs in a container (`/.dockerenv` or `/run/.containerenv` exists), so setting the variable on a normal host does not weaken verify. Verify fails closed: when bubblewrap is missing or cannot start, or `RAIDLOCK_SANDBOX` has any other value, `raid verify` refuses or records a failed receipt.

`--network none` and the other run flags add a second layer around bubblewrap. Without them the verify command is still sandboxed, but the container itself is not hardened.

## Compose

[examples/docker/compose.yaml](../../examples/docker/compose.yaml) has the same flags: a `ui` service published on `127.0.0.1:8765` that mounts the workspace only, and a `verify` service, a lead shell with no network:

```bash
cd examples/docker
RAIDLOCK_WORKSPACE=~/code/shop docker compose up ui
docker compose run --rm verify
```

The `verify` service sets `apparmor=unconfined` so it also works on AppArmor hosts; seccomp stays at Docker's default.

## Windows

Use Docker Desktop with the WSL2 backend. The image runs `raid ui` and the containerized verify shell as above; keep the workspace in the WSL Linux filesystem (for example `\\wsl$\Ubuntu\home\you\code`) and run `docker` from a WSL shell. The `raid` CLI, the Claude Code plugin hooks and the git guards still need raidlock installed inside WSL2 ([Install](install.md)), because they run next to your checkouts and agent sessions.

## Base image

The image is based on `python:3.11-slim` (Debian), pinned by digest, rather than a distroless or Chainguard image. raidlock needs `git` for claims, guards and verify, a shell for verify commands, and `bubblewrap` for the sandbox; distroless images ship none of them. The cost is a larger attack surface: `git` pulls in perl and libcurl, and the Debian base carries util-linux, ncurses and systemd libraries. CI fails on any HIGH or CRITICAL finding that has a fixed version (`trivy image --ignore-unfixed`); findings Debian has not fixed yet are reported but do not fail the build. Rebuild after base image updates.

## Limits

- The host-side CLI, hooks and guards are not containerized. The image does not make a hostile agent on your host safe.
- A host lead cannot delegate `raid verify` to a container; the lead must run in the container.
- On AppArmor hosts, verify needs `apparmor=unconfined` for the verify container, because Docker's default AppArmor profile blocks the mounts bubblewrap makes. Seccomp, dropped capabilities, `no-new-privileges` and the read-only root stay in force.
- MCP needs the full `RAIDLOCK_HOME` mounted read-write.
- CI builds and smoke-tests `linux/amd64` only; `linux/arm64` is built at release and was tested locally on Docker Desktop.
