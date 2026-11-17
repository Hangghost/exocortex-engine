"""Synthetic workspace repositories for the project-core tests (no real data)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

INDEX7 = (
    "---\ndescription: synthetic index\n---\n\n"
    "# Projects Index\n\n"
    "> intro line kept as is\n\n"
    "| name | status | last_updated | description | machine | focus | parent |\n"
    "|------|--------|--------------|-------------|---------|-------|--------|\n"
    "| alpha | active | 2026-01-02 | first synthetic project | box-a | focus |\n"
    "| beta | completed | 2026-01-03 | done one | box-b |  | alpha |\n"
    "\n## Planned (Roadmap)\n\n"
    "| Project | Parent | Last Updated | Description |\n"
    "|---|---|---|---|\n"
    "| gamma | alpha | 2026-01-04 | later |\n"
)


def _rm_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


class WorkspaceCase(unittest.TestCase):
    """A workspace clone on ``main`` with a bare ``origin``."""

    TODAY = "2026-03-10"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="exo-core-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, onerror=_rm_readonly)
        cfg = self.tmp / "gitconfig"
        cfg.write_text("[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_GLOBAL=str(cfg),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Test Author",
            GIT_AUTHOR_EMAIL="author@example.invalid",
            GIT_COMMITTER_NAME="Test Committer",
            GIT_COMMITTER_EMAIL="committer@example.invalid",
            PYTHONUTF8="1",
            PYTHONPATH=str(SRC),
        )
        self.remote = self.tmp / "remote.git"
        self.ws = self.tmp / "ws"
        self.run_git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.run_git(self.tmp, "init", "-q", "-b", "main", str(self.ws))
        self.write("README.md", "synthetic workspace\n")
        self.write("projects/INDEX.md", INDEX7)
        self.write(
            "projects/alpha/PROJECT.md",
            "---\nlast_updated: 2026-01-02\nstatus: active\nl0: alpha summary\n---\n\n# Alpha\n\n"
            "## 目標 / 動機\n\ngoal\n\n## 現況\n\nnow\n\n## 下一步\n\n### sub\n\n> quote\n\n- do the alpha thing\n",
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        # alpha's project branch
        self.git("branch", "project/alpha")
        self.git("push", "-q", "origin", "project/alpha")
        # Tests patch os.environ so in-process git calls see the same config.
        self._old_env = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.env)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._old_env)

    # ---- helpers ----

    def run_git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True, encoding="utf-8", check=check
        )

    def git(self, *args: str, check: bool = True) -> str:
        return self.run_git(self.ws, *args, check=check).stdout

    def rev(self, ref: str, repo: Path | None = None) -> str | None:
        p = self.run_git(repo or self.ws, "rev-parse", "--verify", "--quiet", ref, check=False)
        return p.stdout.strip() or None

    def show(self, ref: str, path: str, repo: Path | None = None) -> str | None:
        p = subprocess.run(
            ["git", "show", f"{ref}:{path}"], cwd=repo or self.ws, env=self.env, capture_output=True
        )
        return p.stdout.decode("utf-8") if p.returncode == 0 else None

    def write(self, rel: str, text: str) -> None:
        p = self.ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def refs(self, repo: Path | None = None) -> dict[str, str]:
        out = self.run_git(repo or self.ws, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads").stdout
        return dict(line.split(" ", 1) for line in out.splitlines() if line)

    def status(self) -> str:
        return self.git("status", "--porcelain")

    def cli(self, *args: str, today: str | None = None, now: str = "09:30", cwd: Path | None = None):
        cmd = [sys.executable, "-m", "exocortex_engine.project", *args, "--today", today or self.TODAY, "--now", now]
        if args and args[0] == "version":
            cmd = [sys.executable, "-m", "exocortex_engine.project", *args]
        proc = subprocess.run(
            cmd, cwd=cwd or self.ws, env=self.env, capture_output=True, text=True, encoding="utf-8"
        )
        proc.first = proc.stdout.splitlines()[0] if proc.stdout else ""  # type: ignore[attr-defined]
        return proc

    def cli_json(self, *args: str, **kw) -> tuple[int, dict]:
        proc = self.cli(*args, "--json", **kw)
        return proc.returncode, json.loads(proc.stdout)

    def tmpfile(self, name: str, text: str) -> str:
        p = self.tmp / name
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return str(p)
