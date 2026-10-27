from raidlock import cli
from raidlock.core import identity


def test_generated_credentials_are_cli_safe(world, monkeypatch):
    monkeypatch.setattr(identity.secrets, "token_urlsafe", lambda size: "-legacy_random")
    worker = world.agent("worker")
    world.card("c1")
    claim = world.claim("c1", worker)
    for value in (worker.token, claim["cap"], str(claim["fence"])):
        assert not value.startswith("-")
    args = cli.common().parse_args(["--token", worker.token, "--cap", claim["cap"], "--fence", str(claim["fence"])])
    assert args.token == worker.token and args.cap == claim["cap"] and args.fence == claim["fence"]


def test_legacy_credentials_accept_equals_form(world):
    token = "-legacy_random"
    identity.safeio.write_text(identity.token_path(world.board.home, world.board.uid, "worker"), token)
    args = cli.common().parse_args([f"--token={token}", "--cap=-legacy-cap", "--fence=1"])
    assert identity.check_token(world.board.home, world.board.uid, "worker", args.token)
    assert args.cap == "-legacy-cap" and args.fence == 1
