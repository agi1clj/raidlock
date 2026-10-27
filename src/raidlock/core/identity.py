import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field

from . import model, safeio
from .errors import IdentityError

PROVIDERS = ("anthropic", "openai", "google", "local", "human")
RUNTIMES = ("claude-code", "codex-cli", "gemini-cli", "aider", "shell", "human")
RUNTIME_PROVIDER = {
    "claude-code": "anthropic",
    "codex-cli": "openai",
    "gemini-cli": "google",
    "aider": "local",
    "shell": "local",
    "human": "human",
}
RUNTIME_SHORT = {
    "claude-code": "claude",
    "codex-cli": "codex",
    "gemini-cli": "gemini",
    "aider": "aider",
    "shell": "shell",
    "human": "human",
}
RUNTIME_LONG = {short: long for long, short in RUNTIME_SHORT.items()}
TRUE = ("1", "true", "yes", "on")


def long_runtime(value):
    """Runtime name as the board stores it: codex -> codex-cli, claude -> claude-code, gemini -> gemini-cli."""
    text = (value or "").strip().lower()
    return RUNTIME_LONG.get(text, text) if text else value


def short_runtime(value):
    """Runtime name as runners and routing use it: codex-cli -> codex, claude-code -> claude."""
    text = (value or "").strip().lower()
    return RUNTIME_SHORT.get(text, text)


@dataclass
class Caller:
    """Who is calling. `authenticated` means a matching agent token, card capability or lead secret was presented.
    `is_lead` is true only for a verified lead secret; `lead_fence` is the lead fence it carries. `scope_card` is set
    when the caller authenticated with a card-scoped capability and may act only on that card."""

    name: str
    authenticated: bool = False
    is_lead: bool = False
    subagent: bool = False
    fence: int = 0
    session: str = ""
    record: dict = field(default_factory=dict)
    lead_fence: int = 0
    scope_card: str = ""
    lead_secret: str = field(default="", repr=False)

    @property
    def model(self):
        return self.record.get("model", "")


def token_path(home, board_uid, agent):
    model.agent_name(agent)
    if (
        not isinstance(board_uid, str)
        or not board_uid
        or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in board_uid)
    ):
        raise IdentityError("invalid board UID")
    root = safeio.check(os.path.join(home, "tokens"))
    path = safeio.check(root / board_uid / (agent + ".token"))
    if not path.is_relative_to(root):
        raise IdentityError("token path escapes token directory")
    return str(path)


def issue_token(home, board_uid, agent):
    token = "t" + secrets.token_urlsafe(24)
    safeio.write_text(token_path(home, board_uid, agent), token + "\n")
    return token


def has_token(home, board_uid, agent):
    return os.path.exists(token_path(home, board_uid, agent))


def read_token(home, board_uid, agent):
    try:
        return safeio.read(token_path(home, board_uid, agent)).decode().strip()
    except OSError:
        return ""


def check_token(home, board_uid, agent, token):
    if not token or not agent:
        return False
    stored = read_token(home, board_uid, agent)
    return bool(stored) and hmac.compare_digest(stored.encode(), token.strip().encode())


def auth_required(settings, env):
    return settings.get("auth") == "token" or env.get("RAIDLOCK_REQUIRE_TOKEN", "").lower() in TRUE


def make_cap(token, agent, card, fence):
    """A card-scoped capability: valid for `agent` on `card` at `fence` only, derived from the agent token."""
    mac = hmac.new(token.encode(), f"{agent}|{card}|{int(fence)}".encode(), hashlib.sha256).hexdigest()[:40]
    return f"{card}:{int(fence)}:{mac}"


def check_cap(home, board_uid, agent, cap):
    """(card, fence) when `cap` was issued to `agent`, else None."""
    parts = (cap or "").strip().split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not agent:
        return None
    token = read_token(home, board_uid, agent)
    if not token:
        return None
    want = make_cap(token, agent, parts[0], int(parts[1]))
    return (parts[0], int(parts[1])) if hmac.compare_digest(want.encode(), cap.strip().encode()) else None


def _worker_token(home, board_uid, agents, lead_name, token):
    return bool(token) and any(check_token(home, board_uid, n, token) for n in agents if n != lead_name)


def presented_secret(lead_secret, env):
    """The lead secret this call presents: the --lead-secret value, RAIDLOCK_LEAD_SECRET, or the file named by
    RAIDLOCK_LEAD_SECRET_FILE. Nothing is read from the secret store on the caller's behalf."""
    if lead_secret:
        return lead_secret.strip()
    if env.get("RAIDLOCK_LEAD_SECRET", "").strip():
        return env["RAIDLOCK_LEAD_SECRET"].strip()
    if env.get("RAIDLOCK_LEAD_SECRET_FILE", "").strip():
        from .lead import read_presented

        return read_presented(env["RAIDLOCK_LEAD_SECRET_FILE"].strip(), None)
    return ""


def resolve(
    home,
    board_uid,
    settings,
    agents,
    lead_name,
    agent=None,
    token=None,
    fence=None,
    env=None,
    lead_secret=None,
    cap=None,
    lead_check=None,
):
    """Resolve the caller from flags and environment.

    Lead rights come only from presenting the lead secret, checked by `lead_check(secret) -> (fence, session)`;
    the lead's name, RAIDLOCK_ROLE or the secret file merely existing grant nothing. A caller with worker
    credentials (a pinned RAIDLOCK_AGENT_ID, a worker token or a card capability) that also holds the lead
    secret is refused."""
    env = os.environ if env is None else env
    if env.get("RAIDLOCK_ROLE", "") == "lead":
        raise IdentityError(
            "RAIDLOCK_ROLE=lead is refused; lead rights come only from presenting the lead secret",
            hint="unset RAIDLOCK_ROLE; the lead passes --lead-secret <file>",
        )
    pinned = env.get("RAIDLOCK_AGENT_ID", "")
    if pinned and agent and agent != pinned:
        raise IdentityError(f"--agent {agent} does not match RAIDLOCK_AGENT_ID={pinned}", agent=agent)
    name = pinned or agent or env.get("RAIDLOCK_AGENT", "")
    token = token or env.get("RAIDLOCK_TOKEN", "")
    cap = cap or env.get("RAIDLOCK_CAP", "")
    secret = presented_secret(lead_secret, env)
    worker = bool(pinned) or bool(cap) or _worker_token(home, board_uid, agents, lead_name, token)
    if secret and worker:
        raise IdentityError(
            "a worker must never hold the lead secret; refusing to run",
            agent=name,
            hint="scrub RAIDLOCK_LEAD_* from this environment",
        )
    fence_value = _int(fence if fence is not None else env.get("RAIDLOCK_FENCE", ""))
    common = dict(subagent=bool(pinned), session=env.get("RAIDLOCK_SESSION", ""), record=dict(agents.get(name, {})))
    if secret:
        if lead_check is None:
            raise IdentityError("no lead lease to check the secret against")
        lead_fence, session = lead_check(secret)
        name = lead_name
        common.update(session=session, record=dict(agents.get(name, {})))
        return Caller(
            name=name,
            authenticated=True,
            is_lead=True,
            fence=fence_value,
            lead_fence=lead_fence,
            lead_secret=secret,
            **common,
        )
    scope = ""
    if cap:
        found = check_cap(home, board_uid, name, cap)
        if not found:
            raise IdentityError(f"card capability is not valid for {name or 'this caller'}", agent=name)
        scope, cap_fence = found
        if fence_value and fence_value != cap_fence:
            raise IdentityError("card fence does not match the presented capability", agent=name)
        fence_value = cap_fence
        authenticated = True
    else:
        authenticated = bool(name) and name != lead_name and check_token(home, board_uid, name, token)
    return Caller(name=name, authenticated=authenticated, fence=fence_value, scope_card=scope, **common)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def require_name(caller):
    if not caller.name:
        raise IdentityError("no agent identity", hint="raidlock join   (or pass --agent / set RAIDLOCK_AGENT)")


LEAD_HINT = "lead rights need the lead secret: raidlock --lead-secret <file> ... from the lead session"


def require_auth(caller, settings, env, lead_name, known):
    """Refuse an unauthenticated caller when tokens are required; refuse the lead's name without the lead secret."""
    require_name(caller)
    if caller.name == lead_name and not caller.is_lead:
        raise IdentityError(
            f"{lead_name} is the lead's name and this caller presented no lead secret",
            agent=caller.name,
            hint=LEAD_HINT,
        )
    if auth_required(settings, env) and not caller.authenticated:
        raise IdentityError(
            f"{caller.name} gave no valid token",
            agent=caller.name,
            hint="export RAIDLOCK_TOKEN=<token printed by raidlock join>",
        )
    if caller.name not in known and not caller.is_lead:
        raise IdentityError(
            f"{caller.name} has not joined this board", agent=caller.name, hint=f"raidlock join --agent {caller.name}"
        )


def require_lead(caller, action):
    if not caller.is_lead:
        raise IdentityError(f"only the lead can {action}", agent=caller.name, action=action, hint=LEAD_HINT)


def default_name(runtime, role, card, taken):
    base = RUNTIME_SHORT.get(runtime, "agent") + "-" + role
    if role == "lead" and base not in taken:
        return base
    if card:
        candidate = (base + "-" + card)[:40].rstrip("-")
        if candidate not in taken:
            return candidate
    n = 1
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def detect_runtime(env=None, interactive=False):
    """Guess the calling runtime from its environment markers."""
    env = os.environ if env is None else env
    if env.get("RAIDLOCK_RUNTIME") in RUNTIMES:
        return env["RAIDLOCK_RUNTIME"]
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude-code"
    if any(k.startswith("CODEX_") and k != "CODEX_HOME" for k in env):
        return "codex-cli"
    if env.get("GEMINI_CLI"):
        return "gemini-cli"
    if env.get("AIDER_MODEL") or env.get("AIDER_CHAT_HISTORY_FILE"):
        return "aider"
    return "human" if interactive else "shell"
