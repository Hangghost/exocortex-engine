"""Tests for tools/identifier_gate.py.

Every identifier list here is synthetic. The real list never enters this repository."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tools" / "identifier_gate.py"
sys.path.insert(0, str(GATE.parent))
import identifier_gate  # noqa: E402

FAKE = "ZZ-FAKE-ID-" + "4821"
FAKE_PREFIX = "ZZ-FAKE-ID"
LIST = "# synthetic list\npatterns:\n  - 'ZZ-FAKE-ID-\\d{4}'\n"
# Built from parts so this file never matches the built-in patterns itself.
HOME_PATH = "/" + "Users" + "/alice/notes"
TAILNET_ADDR = ".".join(["100", "100", "7", "9"])
ZERO = "0" * 40


class GateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        empty_cfg = self.tmp / "gitconfig"
        empty_cfg.write_text("")
        self.env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "EXO_GATE_REDACT")}
        self.env.update(
            GIT_CONFIG_GLOBAL=str(empty_cfg),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Test Author",
            GIT_AUTHOR_EMAIL="author@example.invalid",
            GIT_COMMITTER_NAME="Test Committer",
            GIT_COMMITTER_EMAIL="committer@example.invalid",
            PYTHONUTF8="1",
        )
        self.list_file = self.write_list(LIST)
        self.git("init", "-q")

    def write_list(self, text: str, name: str = "identifiers.yaml") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        self.env["EXO_IDENTIFIERS_FILE"] = str(path)
        return path

    def git(self, *args: str, check: bool = True, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.repo, env={**self.env, **env}, capture_output=True, text=True, check=check
        )

    def add(self, name: str, content: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git("add", "--", name)

    def commit(self, message: str, **env: str) -> None:
        self.git("commit", "-q", "--allow-empty", "-m", message, **env)

    def gate(self, *args: str, stdin: str | None = None, **env: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [sys.executable, str(GATE), *args],
            cwd=self.repo,
            env={**self.env, **env},
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        proc.all = proc.stdout + proc.stderr  # type: ignore[attr-defined]
        return proc


class ParseTests(unittest.TestCase):
    def test_quoting_forms_and_comments(self) -> None:
        text = (
            "# comment\n\npatterns:\n"
            "  - 'it''s'\n"
            '  - "a\\\\d+"\n'
            "  - bare-word\\b\n"
            "  # another comment\n"
        )
        self.assertEqual(identifier_gate.parse_patterns(text), ["it's", "a\\d+", "bare-word\\b"])

    def test_errors_never_echo_line_content(self) -> None:
        bad_inputs = {
            "patterns:\n  - ok\nsecret-word-xyz: 1\n": "line 3",
            "- secret-word-xyz\n": "line 1",
            "patterns:\n  - 'secret-word-xyz(['\n": "P1",
            "patterns:\n  - 'secret-word-xyz*|'\n": "empty string",
        }
        for text, expected in bad_inputs.items():
            with self.subTest(text=text):
                with self.assertRaises(identifier_gate.GateError) as ctx:
                    identifier_gate.parse_patterns(text)
                self.assertIn(expected, str(ctx.exception))
                self.assertNotIn("secret-word-xyz", str(ctx.exception))


class StagedTests(GateCase):
    def test_known_positive_is_blocked(self) -> None:
        self.add("docs/notes.txt", f"first line\nticket {FAKE} here\n")
        proc = self.gate("staged")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("docs/notes.txt:2: P1", proc.stdout)
        self.assertIn(FAKE, proc.stdout)  # local output shows the author what matched

    def test_clean_content_passes(self) -> None:
        self.add("docs/notes.txt", "nothing private here\n")
        proc = self.gate("staged")
        self.assertEqual(proc.returncode, 0, proc.all)
        self.assertIn("0 hit(s)", proc.stdout)

    def test_path_name_is_checked(self) -> None:
        self.add(f"{FAKE}.txt", "clean\n")
        proc = self.gate("staged")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("path: P1", proc.stdout)

    def test_builtin_patterns(self) -> None:
        self.add("a.txt", f"see {HOME_PATH}\nhost {TAILNET_ADDR}\nnot tailnet 100.63.1.1\n")
        proc = self.gate("staged")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("a.txt:1: B1", proc.stdout)
        self.assertIn("a.txt:2: B3", proc.stdout)
        self.assertNotIn("a.txt:3:", proc.stdout)

    def test_author_identity_is_checked(self) -> None:
        self.add("a.txt", "clean\n")
        proc = self.gate("staged", GIT_AUTHOR_EMAIL=f"{FAKE}@example.invalid")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("author-ident: P1", proc.stdout)


# Synthetic Chinese identifiers. FAKE_ZH is invalid UTF-8 once encoded as cp950; the cp950
# bytes of FAKE_ZH_AMBIGUOUS also decode as valid (wrong) UTF-8 text, which a UTF-8-first
# decoder would scan instead of the real characters.
FAKE_ZH = "虛構代號"
FAKE_ZH_AMBIGUOUS = "羅蘇簡"
ZH_LIST = f"patterns:\n  - 'ZZ-FAKE-ID-\\d{{4}}'\n  - '{FAKE_ZH}'\n  - '{FAKE_ZH_AMBIGUOUS}'\n"


class EncodingTests(GateCase):
    """Big5 / cp950 content, as a Traditional Chinese Windows machine may write it."""

    def setUp(self) -> None:
        super().setUp()
        self.write_list(ZH_LIST)

    def add_bytes(self, name: str, data: bytes) -> None:
        (self.repo / name).write_bytes(data)
        self.git("add", "--", name)

    def test_cp950_identifier_is_found(self) -> None:
        self.add_bytes("big5.txt", f"第一行\n代號 {FAKE_ZH} 在此\n".encode("cp950"))
        proc = self.gate("staged")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("big5.txt:2: P2", proc.stdout)

    def test_cp950_that_is_also_valid_utf8_is_found(self) -> None:
        data = f"name: {FAKE_ZH_AMBIGUOUS}\n".encode("cp950")
        self.assertNotEqual(data.decode("utf-8"), data.decode("cp950"))  # the trap is real
        self.add_bytes("ambiguous.txt", data)
        proc = self.gate("staged")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("ambiguous.txt:1: P3", proc.stdout)

    def test_utf8_chinese_still_found_and_shared_hits_reported_once(self) -> None:
        self.add("utf8.txt", f"代號 {FAKE_ZH}\n測試 {FAKE}\n")
        proc = self.gate("staged")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("utf8.txt:1: P2", proc.stdout)
        self.assertEqual(proc.stdout.count("utf8.txt:2: P1"), 1, proc.stdout)

    def test_cp950_commit_message(self) -> None:
        msg = self.tmp / "MSG"
        msg.write_bytes(f"feat: 修正\n\n參考 {FAKE_ZH}\n".encode("cp950"))
        proc = self.gate("message", str(msg))
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("commit message:3: P2", proc.stdout)

    def test_cp950_in_history_is_redacted(self) -> None:
        self.add_bytes("big5.txt", f"{FAKE_ZH}\n".encode("cp950"))
        self.commit("feat: big5")
        proc = self.gate("history", "HEAD", GITHUB_ACTIONS="true")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("big5.txt:1: P2", proc.stdout)
        self.assertNotIn(FAKE_ZH, proc.all)

    def test_decodings(self) -> None:
        decodings = identifier_gate.decodings
        self.assertEqual(decodings(b"plain ascii\n"), ["plain ascii\n"])
        self.assertEqual(decodings("中文".encode("utf-8")), ["中文"])  # not valid cp950
        self.assertEqual(decodings(FAKE_ZH.encode("cp950")), [FAKE_ZH])
        self.assertIn(FAKE_ZH_AMBIGUOUS, decodings(FAKE_ZH_AMBIGUOUS.encode("cp950")))
        self.assertEqual(decodings("x".encode("utf-16")), ["x"])
        self.assertEqual(decodings(b"\xff\x00\x80"), ["\ufffd\x00\ufffd"])  # binary: replace


class FailClosedTests(GateCase):
    def setUp(self) -> None:
        super().setUp()
        self.add("a.txt", "clean\n")

    def assert_fails_closed(self, proc: subprocess.CompletedProcess, reason: str) -> None:
        self.assertEqual(proc.returncode, 2, proc.all)
        self.assertIn(reason, proc.stderr)
        self.assertIn("FAILED CLOSED", proc.stderr)

    def test_missing_list(self) -> None:
        proc = self.gate("staged", EXO_IDENTIFIERS_FILE=str(self.tmp / "absent.yaml"))
        self.assert_fails_closed(proc, "not found")

    def test_malformed_list(self) -> None:
        self.write_list("patterns:\n  - ok\nsecret-word-xyz: 1\n")
        proc = self.gate("staged")
        self.assert_fails_closed(proc, "line 3")
        self.assertNotIn("secret-word-xyz", proc.all)

    def test_regex_that_does_not_compile(self) -> None:
        self.write_list("patterns:\n  - 'secret-word-xyz(['\n")
        proc = self.gate("staged")
        self.assert_fails_closed(proc, "does not compile")
        self.assertNotIn("secret-word-xyz", proc.all)

    def test_list_without_patterns(self) -> None:
        self.write_list("patterns:\n# nothing yet\n")
        self.assert_fails_closed(self.gate("staged"), "contains no patterns")
        self.write_list("\n")  # what an unset CI secret would produce
        self.assert_fails_closed(self.gate("staged"), "no `patterns:` header")


class MessageTests(GateCase):
    def test_commit_message(self) -> None:
        msg = self.tmp / "MSG"
        msg.write_text(f"feat: thing\n\nRefs {FAKE}\n", encoding="utf-8")
        proc = self.gate("message", str(msg))
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("commit message:3: P1", proc.stdout)
        msg.write_text("feat: thing\n", encoding="utf-8")
        self.assertEqual(self.gate("message", str(msg)).returncode, 0)


class HistoryTests(GateCase):
    def make_dirty_history(self) -> None:
        self.add("leak.txt", f"oops {FAKE}\n")
        self.add(f"dir/{FAKE}.md", "clean\n")
        self.commit(f"add {FAKE}", GIT_AUTHOR_EMAIL=f"{FAKE}@example.invalid")
        self.git("rm", "-q", "leak.txt")
        self.commit("remove the leak")  # gone from HEAD's tree, still in history

    def test_ci_output_is_redacted(self) -> None:
        self.make_dirty_history()
        proc = self.gate("history", "HEAD", GITHUB_ACTIONS="true")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertNotIn(FAKE_PREFIX, proc.all)  # neither the matched text nor the pattern
        self.assertNotIn("\\d{4}", proc.all)
        self.assertIn("leak.txt:1: P1", proc.stdout)  # deleted content is still found
        self.assertIn("message:1: P1", proc.stdout)
        self.assertIn("author-email: P1", proc.stdout)
        self.assertIn("<path redacted", proc.stdout)

    def test_redact_flag_without_ci(self) -> None:
        self.make_dirty_history()
        proc = self.gate("--redact", "history", "HEAD")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertNotIn(FAKE_PREFIX, proc.all)

    def test_redacted_config_errors_hide_git_details(self) -> None:
        self.commit("clean")
        proc = self.gate("history", f"no-such-rev-{FAKE}", GITHUB_ACTIONS="true")
        self.assertEqual(proc.returncode, 2, proc.all)
        self.assertNotIn(FAKE_PREFIX, proc.all)

    def test_clean_history_passes(self) -> None:
        self.add("a.txt", "clean\n")
        self.commit("feat: clean")
        proc = self.gate("history", "HEAD", GITHUB_ACTIONS="true")
        self.assertEqual(proc.returncode, 0, proc.all)


class PrePushTests(GateCase):
    def test_pushed_commits_are_scanned(self) -> None:
        self.add("a.txt", f"{FAKE}\n")
        self.commit("feat: thing")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        proc = self.gate("pre-push", stdin=f"refs/heads/main {sha} refs/heads/main {ZERO}\n")
        self.assertEqual(proc.returncode, 1, proc.all)
        self.assertIn("a.txt:1: P1", proc.stdout)

    def test_branch_deletion_only_checks_ref_names(self) -> None:
        proc = self.gate("pre-push", stdin=f"(delete) {ZERO} refs/heads/old {ZERO}\n")
        self.assertEqual(proc.returncode, 0, proc.all)


class HookTests(GateCase):
    """End to end through git itself, with the versioned hooks copied into a scratch repo."""

    def setUp(self) -> None:
        super().setUp()
        (self.repo / "tools").mkdir()
        shutil.copy2(GATE, self.repo / "tools" / GATE.name)
        shutil.copytree(ROOT / ".githooks", self.repo / ".githooks")
        self.git("add", "tools", ".githooks")

    def try_commit(self, message: str) -> subprocess.CompletedProcess:
        return self.git("-c", "core.hooksPath=.githooks", "commit", "-q", "-m", message, check=False)

    def has_commit(self) -> bool:
        return self.git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0

    def test_hooks_have_lf_line_endings(self) -> None:
        for hook in (ROOT / ".githooks").iterdir():
            with self.subTest(hook=hook.name):
                self.assertNotIn(b"\r", hook.read_bytes())

    def test_pre_commit_blocks_content(self) -> None:
        self.add("a.txt", f"{FAKE}\n")
        proc = self.try_commit("feat: thing")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(self.has_commit())

    def test_commit_msg_blocks_message(self) -> None:
        self.add("a.txt", "clean\n")
        proc = self.try_commit(f"feat: {FAKE}")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(self.has_commit())

    def test_clean_commit_goes_through(self) -> None:
        self.add("a.txt", "clean\n")
        proc = self.try_commit("feat: clean")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(self.has_commit())


if __name__ == "__main__":
    unittest.main()
