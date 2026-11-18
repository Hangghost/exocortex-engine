"""The identifier gate's hooks are installed in this clone and git will actually run them.

Two ways the gate goes silent without any error: `core.hooksPath` is not set (git never
looks in .githooks), or a hook lost its executable bit (git prints a hint and commits
anyway). Both are checked on this checkout, and each checker is proved on a scratch repo.

Medium tests: they run real git, because the index mode and the effective config are
git's own state and a fake would only test the fake. Scratch repos ignore global and
system git config; nothing here writes to this checkout's config."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ("pre-commit", "commit-msg", "pre-push")
FIX = "git config core.hooksPath .githooks"


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)


def is_work_tree(repo: Path) -> bool:
    return git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def non_executable_hooks(repo: Path, env: dict[str, str] | None = None) -> list[str]:
    """`.githooks` files whose index mode is not 100755 (git skips such a hook silently)."""
    listing = git(repo, "ls-files", "-s", "-z", "--", ".githooks", env=env).stdout
    records = [r for r in listing.split("\0") if r]
    if not records:
        return ["(no files tracked under .githooks)"]
    bad = []
    for rec in records:
        meta, _, path = rec.partition("\t")
        if meta.split()[0] != "100755":
            bad.append(f"{path} is mode {meta.split()[0]}")
    return bad


def hooks_path_problem(repo: Path, env: dict[str, str] | None = None) -> str | None:
    """Why git would not run the gate hooks in `repo`, or None when it would."""
    value = git(repo, "config", "--get", "core.hooksPath", env=env).stdout.strip()
    if not value:
        return f"core.hooksPath is not set, so git runs none of the gate hooks. Run once per clone: {FIX}"
    path = Path(value).expanduser()
    if not path.is_absolute():  # git resolves it against the top of the work tree
        path = Path(git(repo, "rev-parse", "--show-toplevel", env=env).stdout.strip()) / path
    if path.name != ".githooks" or not all((path / h).is_file() for h in HOOKS):
        return f"core.hooksPath is {value!r}, which is not a .githooks directory with {', '.join(HOOKS)}. Run: {FIX}"
    return None


class ThisCheckout(unittest.TestCase):
    def setUp(self) -> None:
        if not is_work_tree(ROOT):
            self.skipTest("not a git checkout")

    def test_hooks_are_executable(self) -> None:
        bad = non_executable_hooks(ROOT)
        fix = "git update-index --chmod=+x .githooks/<file>"
        self.assertEqual(bad, [], f"git would skip these hooks without an error: {bad}. Fix: {fix}")

    def test_hooks_path_is_set(self) -> None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            self.skipTest("CI clones never set core.hooksPath; the gate job scans there instead")
        problem = hooks_path_problem(ROOT)
        self.assertIsNone(problem, problem)


class Checkers(unittest.TestCase):
    """Known positives: each checker speaks up on a scratch repo with the defect."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="hooks-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "gitconfig").write_text("")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        self.repo = tmp / "repo"
        (self.repo / ".githooks").mkdir(parents=True)
        self.run_git("init", "-q")
        for name in (*HOOKS, "_gate.sh"):
            (self.repo / ".githooks" / name).write_text("#!/bin/sh\n", encoding="utf-8")
            self.run_git("update-index", "--add", "--chmod=+x", f".githooks/{name}")

    def run_git(self, *args: str) -> None:
        proc = git(self.repo, *args, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_executable_hooks_pass(self) -> None:
        self.assertEqual(non_executable_hooks(self.repo, self.env), [])

    def test_non_executable_hook_is_caught(self) -> None:
        self.run_git("update-index", "--chmod=-x", ".githooks/pre-commit")
        self.assertEqual(non_executable_hooks(self.repo, self.env), [".githooks/pre-commit is mode 100644"])

    def test_no_tracked_hooks_is_caught(self) -> None:
        self.run_git("rm", "-q", "--cached", "-r", "-f", ".githooks")
        self.assertEqual(len(non_executable_hooks(self.repo, self.env)), 1)

    def test_unset_hooks_path_is_caught(self) -> None:
        problem = hooks_path_problem(self.repo, self.env)
        self.assertIsNotNone(problem)
        self.assertIn(FIX, problem)

    def test_wrong_hooks_path_is_caught(self) -> None:
        self.run_git("config", "core.hooksPath", ".git/hooks")
        self.assertIn(FIX, hooks_path_problem(self.repo, self.env) or "")

    def test_relative_and_absolute_hooks_path_pass(self) -> None:
        for value in (".githooks", str(self.repo / ".githooks")):
            with self.subTest(value=value):
                self.run_git("config", "core.hooksPath", value)
                self.assertIsNone(hooks_path_problem(self.repo, self.env))


if __name__ == "__main__":
    unittest.main()
