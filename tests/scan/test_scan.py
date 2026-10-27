import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from raidlock import scan
from raidlock.core.errors import Precondition
from raidlock.runners.workspace import git_env

FIXTURES = Path(__file__).parent / "fixtures"


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"RAIDLOCK_HOME": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)
        fallback = patch.object(scan.shutil, "which", return_value=None)
        fallback.start()
        self.addCleanup(fallback.stop)

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull, *args],
            cwd=self.root,
            env=git_env(),
            check=True,
            capture_output=True,
        ).stdout

    def repo(self):
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@localhost")
        (self.root / "file.txt").write_text('old = "AKIAABCDEFGHIJKLMNOP"\n')
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def test_secret_rules_and_redaction(self):
        findings = scan.scan_patch((FIXTURES / "secrets.patch").read_text())
        rules = {f["rule"] for f in findings}
        self.assertTrue(
            {
                "aws-access-key",
                "github-token",
                "openai-token",
                "anthropic-token",
                "private-key",
                "high-entropy-assignment",
            }
            <= rules
        )
        self.assertEqual({f["line"] for f in findings}, set(range(2, 8)))
        self.assertNotIn("AKIA", json.dumps(findings))
        self.assertTrue(all(f["snippet"] == "[REDACTED]" for f in findings))

    def test_context_and_removed_lines_are_ignored(self):
        text = (
            "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-AKIAABCDEFGHIJKLMNOP\n"
            " ghp_abcdefghijklmnopqrstuvwxyz0123456789\n+safe\n"
        )
        self.assertEqual(scan.scan_patch(text), [])

    def test_policy_and_rename_source(self):
        findings = scan.scan_patch((FIXTURES / "paths.patch").read_text())
        self.assertEqual({f["path"] for f in findings}, {".env", ".github/workflows/ci.yml"})

    def test_quoted_paths_and_added_header_text(self):
        text = (
            'diff --git "a/space name.py" "b/space name.py"\n--- "a/space name.py"\n+++ "b/space name.py"\n'
            "@@ -0,0 +1 @@\n+++ AKIAABCDEFGHIJKLMNOP\n"
        )
        finding = scan.scan_patch(text)[0]
        self.assertEqual((finding["path"], finding["line"]), ("space name.py", 1))

    def test_deleted_header_like_lines_are_not_paths(self):
        text = "--- a/file.txt\n+++ b/file.txt\n@@ -1,2 +0,0 @@\n--- a/.env\n-AKIAABCDEFGHIJKLMNOP\n"
        self.assertEqual(scan.scan_patch(text), [])

    def test_multiple_hunks_keep_destination_line_numbers(self):
        text = "--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+safe\n@@ -20 +21 @@\n-old\n+AKIAABCDEFGHIJKLMNOP\n"
        self.assertEqual(scan.scan_patch(text)[0]["line"], 21)

    def test_binary_threshold_and_reverse_blob(self):
        for size, count in [(1048576, 0), (1048577, 1)]:
            text = f"diff --git a/x b/x\nGIT binary patch\nliteral {size}\n\nliteral 9000000\n"
            self.assertEqual(len(scan.scan_patch(text)), count)

    def test_real_binary_delta_target_size(self):
        self.repo()
        data = bytes(range(256)) * 5000
        (self.root / "blob").write_bytes(data)
        self.git("add", "blob")
        self.git("commit", "-qm", "binary fixture")
        (self.root / "blob").write_bytes(data[:100] + b"changed" + data[107:])
        diff = self.git("diff", "--binary")
        self.assertIn(b"delta ", diff)
        findings = scan.scan_patch(diff)
        self.assertEqual(findings[0]["kind"], "binary")

    def test_clone_staged_unstaged_untracked_and_index_unchanged(self):
        self.repo()
        (self.root / "file.txt").write_text('old = "AKIAABCDEFGHIJKLMNOP"\nsafe\n')
        (self.root / "staged").write_text("AKIAABCDEFGHIJKLMNOP\n")
        self.git("add", "staged")
        (self.root / ".env").write_text("safe\n")
        (self.root / "big").write_bytes(b"\0" * (1048576 + 1))
        before = (self.root / ".git/index").read_bytes()
        findings = scan.scan_patch(self.root)
        self.assertEqual({f["path"] for f in findings}, {"staged", ".env", "big"})
        self.assertEqual(before, (self.root / ".git/index").read_bytes())
        self.assertEqual(scan.scan_patch(["file.txt"], self.root), [])
        self.assertEqual({f["path"] for f in scan.scan_patch(self.root, changed_files=["staged"])}, {"staged"})

    def test_clone_against_commit(self):
        self.repo()
        base = self.git("rev-parse", "HEAD").decode().strip()
        (self.root / "new").write_text("AKIAABCDEFGHIJKLMNOP\n")
        self.git("add", "new")
        self.git("commit", "-qm", "change")
        self.assertEqual(scan.scan_patch(self.root), [])
        self.assertEqual(scan.scan_patch(self.root, base)[0]["path"], "new")

    def test_gitleaks_only_receives_added_lines(self):
        def run(argv, **kwargs):
            source = Path(argv[argv.index("--source") + 1])
            files = list(source.iterdir())
            self.assertEqual(len(files), 1)
            content = files[0].read_text()
            self.assertNotIn("keep =", content)
            self.assertNotIn("old =", content)
            report = Path(argv[argv.index("--report-path") + 1])
            report.write_text(
                json.dumps([{"File": str(files[0]), "StartLine": 1, "RuleID": "aws", "Secret": "AKIAABCDEFGHIJKLMNOP"}])
            )
            return subprocess.CompletedProcess(argv, 1)

        with (
            patch.object(scan.shutil, "which", return_value="gitleaks"),
            patch.object(scan.subprocess, "run", side_effect=run),
        ):
            findings = scan.scan_patch((FIXTURES / "secrets.patch").read_text())
        self.assertEqual(
            findings, [{"kind": "secret", "path": "config.py", "line": 2, "rule": "aws", "snippet": "[REDACTED]"}]
        )

    def test_gitleaks_failure_is_not_clean(self):
        with (
            patch.object(scan.shutil, "which", return_value="gitleaks"),
            patch.object(scan.subprocess, "run", return_value=subprocess.CompletedProcess([], 2)),
            self.assertRaises(Precondition),
        ):
            scan.scan_patch((FIXTURES / "secrets.patch").read_text())

    def test_invalid_inputs(self):
        for text in [
            "not a patch",
            "--- a/x\n+++ b/x\n@@ -0,0 +1,2 @@\n+x\n",
            "--- a/x\n+++ b/../../.env\n",
            "diff --git a/x b/x\nBinary files a/x and b/x differ\n",
        ]:
            with self.subTest(text=text), self.assertRaises(Precondition):
                scan.scan_patch(text)

    def test_cli_json_exit_and_no_board(self):
        from raidlock import cli

        with (
            patch.dict(cli.COMMANDS),
            patch.object(cli, "load_extensions"),
            patch.object(
                cli.Context,
                "board",
                new_callable=unittest.mock.PropertyMock,
                side_effect=AssertionError("board accessed"),
            ),
        ):
            scan.register_cli(cli.add_command)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = cli.main(["scan", str(FIXTURES / "secrets.patch"), "--json"])
            self.assertEqual(result, 2)
            self.assertIn("findings", json.loads(output.getvalue()))
            clean = self.root / "clean.patch"
            clean.write_text("")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["scan", str(clean)]), 0)


if __name__ == "__main__":
    unittest.main()
