"""``python -m exocortex_engine.project`` against synthetic workspaces with a bare remote."""
from __future__ import annotations

import os
import unittest

from _workspace import INDEX7, WorkspaceCase

SECTION = "### 進展\n\n- did a thing\n\n### 經驗與觀察\n\n- learned\n\n### 下一步\n\n- next thing\n"
INDEX_EDITED = INDEX7.replace("> intro line kept as is", "> local edit, not committed")


def alpha_md(next_line: str = "- updated next action") -> str:
    return (
        "---\nlast_updated: 2026-01-02\nstatus: active\nl0: alpha after update\n---\n\n# Alpha\n\n"
        "## 目標 / 動機\n\ngoal\n\n## 現況\n\nnow better\n\n## 下一步\n\n" + next_line + "\n"
    )


class NewTests(WorkspaceCase):
    def test_new_creates_branch_and_lands_on_main(self):
        main_before = self.rev("main")
        p = self.cli("new", "delta", "--title", "Delta 專案", "--goal", "做 delta", "--next", "first step")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(p.first, "result: ok")
        tip = self.rev("project/delta")
        main = self.rev("main")
        self.assertEqual(self.git("rev-parse", "main^1").strip(), main_before)
        self.assertEqual(self.git("rev-parse", "main^2").strip(), tip)  # --no-ff merge
        md = self.show("project/delta", "projects/delta/PROJECT.md")
        self.assertIn("last_updated: 2026-03-10", md)
        self.assertIn("<!-- 待填入 -->", md)
        self.assertEqual(self.show("main", "projects/delta/context/.gitkeep"), "")
        self.assertIn("| delta | active | 2026-03-10 | 做 delta | | | |", self.show("main", "projects/INDEX.md"))
        # main is checked out and was clean: the checkout followed via a fast-forward merge.
        self.assertEqual(self.status(), "")
        self.assertTrue((self.ws / "projects/delta/PROJECT.md").exists())
        self.assertEqual(self.rev("project/delta", self.remote), tip)
        self.assertEqual(self.rev("main", self.remote), main)

    def _assert_zero_writes(self, before, p):
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(p.first, "result: rejected")
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.refs(self.remote), self.remote_before)

    def test_new_refuses_existing_branch_local_or_remote(self):
        self.remote_before = self.refs(self.remote)
        before = self.refs()
        self._assert_zero_writes(before, self.cli("new", "alpha", "--title", "A", "--goal", "g"))
        # exists only on origin
        self.run_git(self.tmp, "--git-dir", str(self.remote), "branch", "project/epsilon", "main")
        self.remote_before = self.refs(self.remote)
        self._assert_zero_writes(before, self.cli("new", "epsilon", "--title", "E", "--goal", "g"))

    def test_new_refuses_overlapping_dirty_main_and_bad_name(self):
        self.remote_before = self.refs(self.remote)
        before = self.refs()
        self.write("projects/INDEX.md", INDEX_EDITED)  # new rewrites INDEX.md
        self.write("projects/zeta/notes.md", "untracked, inside the new project dir\n")
        p = self.cli("new", "zeta", "--title", "Z", "--goal", "g")
        self._assert_zero_writes(before, p)
        self.assertIn("projects/INDEX.md", p.stdout)
        self.assertNotIn("projects/zeta/notes.md", p.stdout)  # no path of the commit is under it
        p = self.cli("new", "Bad_Name", "--title", "Z", "--goal", "g")
        self.assertEqual((p.returncode, p.first), (2, "result: usage-error"))

    def test_new_with_unrelated_dirty_main(self):
        self.write("README.md", "uncommitted\n")
        self.write("staged.md", "staged\n")
        self.git("add", "staged.md")
        cached = self.git("diff", "--cached")
        p = self.cli("new", "zeta", "--title", "Z", "--goal", "g")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.git("rev-parse", "main^2").strip(), self.rev("project/zeta"))
        self.assertEqual(self.git("diff", "--cached"), cached)
        self.assertEqual((self.ws / "README.md").read_text(encoding="utf-8"), "uncommitted\n")
        self.assertTrue((self.ws / "projects/zeta/PROJECT.md").exists())


class UpdateTests(WorkspaceCase):
    def test_apply_writes_both_refs_without_touching_worktree(self):
        head, before_status = self.rev("HEAD"), self.status()
        p = self.cli("update", "alpha", "apply", "--project-md", self.tmpfile("p.md", alpha_md()),
                     "--worklog-section", self.tmpfile("s.md", SECTION), now="09:30")
        self.assertEqual(p.returncode, 0, p.stdout)
        md = self.show("project/alpha", "projects/alpha/PROJECT.md")
        self.assertIn("last_updated: 2026-03-10", md)  # set by the CLI
        self.assertIn("| alpha | active | 2026-03-10 |", self.show("project/alpha", "projects/INDEX.md"))
        wl = self.show("content/2026-03-10", "contexts/work_logs/2026-03-10_alpha_update.md")
        self.assertIn("project: alpha", wl)
        self.assertIn("l0: alpha 進度更新（2026-03-10）：alpha after update", wl)
        self.assertIn("## 09:30", wl)
        self.assertEqual((self.rev("HEAD"), self.status()), (head, before_status))
        self.assertEqual(self.rev("main"), head)
        # second update the same day appends
        p = self.cli("update", "alpha", "apply", "--project-md", self.tmpfile("p.md", alpha_md("- later")),
                     "--worklog-section", self.tmpfile("s.md", SECTION), "--worklog-l0", "整天的總結", now="16:05")
        self.assertEqual(p.returncode, 0, p.stdout)
        wl2 = self.show("content/2026-03-10", "contexts/work_logs/2026-03-10_alpha_update.md")
        self.assertIn("## 09:30", wl2)
        self.assertIn("## 16:05", wl2)
        self.assertIn("l0: 整天的總結", wl2)
        self.assertEqual(self.rev("content/2026-03-10", self.remote), self.rev("content/2026-03-10"))

    def test_apply_is_all_or_nothing(self):
        before, remote_before = self.refs(), self.refs(self.remote)
        bad_md = alpha_md().replace("## 現況", "## Now")
        cases = [
            (bad_md, SECTION),  # PROJECT.md invalid
            (alpha_md(), "### 進展\n\n- only one\n"),  # work_log section invalid
            (alpha_md().replace("status: active", "status: ???"), SECTION),
        ]
        for md, sec in cases:
            p = self.cli("update", "alpha", "apply", "--project-md", self.tmpfile("p.md", md),
                         "--worklog-section", self.tmpfile("s.md", sec))
            self.assertEqual((p.returncode, p.first), (1, "result: rejected"), p.stdout)
            self.assertEqual(self.refs(), before)
            self.assertEqual(self.refs(self.remote), remote_before)

    def test_apply_refuses_dropping_a_section(self):
        self.cli("update", "alpha", "apply", "--project-md",
                 self.tmpfile("p.md", alpha_md() + "\n## 自訂\n\nkeep\n"), "--worklog-section", self.tmpfile("s.md", SECTION))
        before = self.refs()
        p = self.cli("update", "alpha", "apply", "--project-md", self.tmpfile("p.md", alpha_md()),
                     "--worklog-section", self.tmpfile("s.md", SECTION))
        self.assertEqual(p.returncode, 1)
        self.assertIn("drops sections", p.stdout)
        self.assertEqual(self.refs(), before)

    def test_gather_and_load(self):
        self.cli("update", "alpha", "apply", "--project-md", self.tmpfile("p.md", alpha_md()),
                 "--worklog-section", self.tmpfile("s.md", SECTION))
        code, g = self.cli_json("update", "alpha", "gather")
        self.assertEqual(code, 0)
        self.assertEqual(g["today_worklog_on"], ["content/2026-03-10"])
        self.assertIn("now better", g["project_md"])
        code, ld = self.cli_json("load", "alpha")
        self.assertEqual(code, 0)
        self.assertEqual(ld["source"], "project/alpha")
        self.assertEqual(ld["worklogs"], ["content/2026-03-10:contexts/work_logs/2026-03-10_alpha_update.md"])
        self.assertIn("head_hint", ld)  # the checkout's PROJECT.md is behind the branch
        self.assertEqual(self.cli("load", "nope").returncode, 1)


class BoardTests(WorkspaceCase):
    def test_board(self):
        code, b = self.cli_json("board")
        self.assertEqual(code, 0)
        self.assertEqual([p["name"] for p in b["projects"]], ["alpha"])  # beta is completed
        alpha = b["projects"][0]
        self.assertEqual(alpha["next"], "do the alpha thing")  # sub-heading and blockquote skipped
        self.assertEqual(alpha["days"], 67)


class LandTests(WorkspaceCase):
    def _apply(self, now="09:30"):
        p = self.cli("update", "alpha", "apply", "--project-md", self.tmpfile("p.md", alpha_md()),
                     "--worklog-section", self.tmpfile("s.md", SECTION), now=now)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_land_content_squashes_and_deletes_branch(self):
        self._apply()
        main_before = self.rev("main")
        p = self.cli("land", "content")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.git("log", "-1", "--format=%s%n%P", "main").split(), [
            "content:", "2026-03-10", "daily", "snapshot", main_before])  # one parent: squash
        self.assertIsNotNone(self.show("main", "contexts/work_logs/2026-03-10_alpha_update.md"))
        self.assertIsNone(self.rev("content/2026-03-10"))
        self.assertIsNone(self.rev("content/2026-03-10", self.remote))
        self.assertEqual(self.rev("main", self.remote), self.rev("main"))
        self.assertEqual(self.status(), "")

    def test_land_refuses_overlapping_dirty_main(self):
        self._apply()
        wl = "contexts/work_logs/2026-03-10_alpha_update.md"
        cases = [
            # (command, how the checkout on main is dirty, path that must be listed)
            (("land", "content"), lambda: self.write(wl, "untracked file, same name\n"), wl),
            (("land", "project", "alpha", "m1"), lambda: self.write("projects/alpha/PROJECT.md", "edited\n"),
             "projects/alpha/PROJECT.md"),
            (("land", "project", "alpha", "m1"),
             lambda: (self.write("projects/INDEX.md", INDEX_EDITED), self.git("add", "projects/INDEX.md")),
             "projects/INDEX.md"),
        ]
        for args, make_dirty, path in cases:
            with self.subTest(args=args, path=path):
                make_dirty()
                self.write("README.md", "unrelated, does not matter\n")
                before, remote_before = self.refs(), self.refs(self.remote)
                p = self.cli(*args)
                self.assertEqual((p.returncode, p.first), (1, "result: rejected"), p.stdout)
                self.assertIn(path, p.stdout)
                self.assertNotIn("README.md", p.stdout)
                self.assertEqual(self.refs(), before)
                self.assertEqual(self.refs(self.remote), remote_before)
                self.git("reset", "-q", "--hard")
                self.git("clean", "-qfd")

    def test_land_with_unrelated_dirty_main(self):
        self._apply()
        self.write("README.md", "uncommitted\n")
        self.write("notes/staged.md", "staged\n")
        self.git("add", "notes/staged.md")
        self.write("scratch.txt", "untracked\n")
        cached, unstaged = self.git("diff", "--cached"), self.git("diff")
        for args in (("land", "content"), ("land", "project", "alpha", "m1")):
            p = self.cli(*args)
            self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        self.assertEqual(self.git("log", "-2", "--format=%s", "main").splitlines(),
                         ["Merge project/alpha: m1", "content: 2026-03-10 daily snapshot"])
        self.assertEqual((self.git("diff", "--cached"), self.git("diff")), (cached, unstaged))
        self.assertEqual((self.ws / "README.md").read_text(encoding="utf-8"), "uncommitted\n")
        self.assertEqual((self.ws / "scratch.txt").read_text(encoding="utf-8"), "untracked\n")
        self.assertTrue((self.ws / "contexts/work_logs/2026-03-10_alpha_update.md").exists())
        self.assertIn("now better", (self.ws / "projects/alpha/PROJECT.md").read_text(encoding="utf-8"))

    def test_land_project_merges_scope_and_keeps_branch(self):
        self._apply()
        # main moved on meanwhile: another row appended right after alpha's
        self.cli("new", "omega", "--title", "O", "--goal", "g")
        main_before, tip = self.rev("main"), self.rev("project/alpha")
        p = self.cli("land", "project", "alpha", "milestone one")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.git("rev-parse", "main^1").strip(), main_before)
        self.assertEqual(self.git("rev-parse", "main^2").strip(), tip)
        self.assertEqual(self.git("log", "-1", "--format=%s", "main").strip(), "Merge project/alpha: milestone one")
        index = self.show("main", "projects/INDEX.md")
        self.assertIn("| alpha | active | 2026-03-10 |", index)
        self.assertIn("| omega | active |", index)
        self.assertIn("now better", self.show("main", "projects/alpha/PROJECT.md"))
        self.assertEqual(self.rev("project/alpha"), tip)
        p = self.cli("land", "project", "alpha", "again")
        self.assertEqual((p.returncode, p.first), (0, "result: noop"))

    def test_land_with_checkout_on_another_branch_leaves_it_alone(self):
        self._apply()
        self.git("switch", "-q", "-c", "scratch")
        self.write("README.md", "dirty on scratch\n")
        status = self.status()
        p = self.cli("land", "content")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.git("branch", "--show-current").strip(), "scratch")
        self.assertEqual(self.status(), status)
        self.assertFalse((self.ws / "contexts").exists())
        self.assertIn("update-ref", p.stdout)

    def test_land_content_missing_branch(self):
        p = self.cli("land", "content", "2026-03-01")
        self.assertEqual(p.returncode, 1)


class VersionContractTests(WorkspaceCase):
    def test_version_lines(self):
        p = self.cli("version")
        self.assertEqual(p.returncode, 0)
        lines = p.stdout.splitlines()
        self.assertRegex(lines[0], r"^version: \d+\.\d+\.\d+$")
        self.assertEqual(lines[1], "contract: 1")

    def test_newer_workspace_contract_refused(self):
        (self.ws / ".exocortex").mkdir()
        (self.ws / ".exocortex/instance.toml").write_text(
            'contract = 2\n\n[executor]\npython = "x"\n', encoding="utf-8")
        p = self.cli("board")
        self.assertEqual((p.returncode, p.first), (1, "result: rejected"))
        self.assertIn("contract 2", p.stdout)
        (self.ws / ".exocortex/instance.toml").write_text("contract = 1  # ok\n", encoding="utf-8")
        self.assertEqual(self.cli("board").returncode, 0)


class EndToEndTests(WorkspaceCase):
    """new -> update gather -> update apply -> board -> land content -> land project."""

    def test_full_cycle(self):
        verbose = os.environ.get("EXO_E2E_VERBOSE") == "1"
        steps = []

        def step(*args, now="09:30"):
            p = self.cli(*args, now=now)
            steps.append((" ".join(args[:3]), p.first))
            self.assertEqual(p.returncode, 0, f"{args}: {p.stdout}")
            return p

        head0 = self.rev("HEAD")
        step("new", "e2e-proj", "--title", "E2E", "--goal", "端到端驗證", "--next", "write the first update")
        head1 = self.rev("HEAD")
        self.assertNotEqual(head0, head1)
        step("update", "e2e-proj", "gather")
        md = self.show("project/e2e-proj", "projects/e2e-proj/PROJECT.md")
        md = md.replace("## 現況\n\n<!-- 待填入 -->", "## 現況\n\nfirst update done").replace(
            "write the first update", "land it")
        step("update", "e2e-proj", "apply", "--project-md", self.tmpfile("p.md", md),
             "--worklog-section", self.tmpfile("s.md", SECTION))
        # ref-anchored: the checkout on main did not move and has no changes
        self.assertEqual((self.rev("HEAD"), self.status()), (head1, ""))
        board = step("board")
        self.assertIn("e2e-proj [active] 2026-03-10 (0d) — land it", board.stdout)
        step("land", "content")
        step("land", "project", "e2e-proj", "v0 done")

        refs, remote = self.refs(), self.refs(self.remote)
        self.assertEqual(sorted(refs), ["refs/heads/main", "refs/heads/project/alpha", "refs/heads/project/e2e-proj"])
        self.assertEqual(refs, remote)  # everything pushed, content branch deleted on both sides
        main_md = self.show("main", "projects/e2e-proj/PROJECT.md")
        self.assertEqual(main_md, self.show("project/e2e-proj", "projects/e2e-proj/PROJECT.md"))
        self.assertIn("first update done", main_md)
        self.assertIn("| e2e-proj | active | 2026-03-10 | 端到端驗證 |", self.show("main", "projects/INDEX.md"))
        self.assertIn("### 經驗與觀察", self.show("main", "contexts/work_logs/2026-03-10_e2e-proj_update.md"))
        subjects = self.git("log", "--first-parent", "--format=%s", "main").splitlines()
        self.assertEqual(subjects[:3], ["Merge project/e2e-proj: v0 done", "content: 2026-03-10 daily snapshot",
                                        "Merge project/e2e-proj: new"])
        self.assertEqual((self.git("branch", "--show-current").strip(), self.status()), ("main", ""))
        self.assertEqual(self.rev("HEAD"), refs["refs/heads/main"])
        if verbose:
            for s in steps:
                print("step:", *s)
            for name, sha in refs.items():
                print(f"local  {name} {sha[:10]} {self.git('log', '-1', '--format=%s', sha).strip()}")
            for name, sha in remote.items():
                print(f"remote {name} {sha[:10]}")
            print("worktree:", self.git("branch", "--show-current").strip(), "HEAD", self.rev("HEAD")[:10],
                  "status:", repr(self.status()))


if __name__ == "__main__":
    unittest.main()
