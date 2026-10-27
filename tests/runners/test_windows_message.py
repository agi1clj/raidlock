from raidlock import __version__, cli

MESSAGE = (
    "raidlock does not support native Windows yet; run it inside WSL2: "
    "https://github.com/agi1clj/raidlock/blob/main/docs/guides/install.md#on-windows\n"
)


def test_native_windows_points_to_wsl2(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli.main(["show"]) == 3
    out = capsys.readouterr()
    assert out.err == MESSAGE and out.out == ""


def test_native_windows_still_prints_the_version(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli.main(["--version"], prog="raid") == 0
    assert capsys.readouterr().out == f"raid {__version__}\n"
