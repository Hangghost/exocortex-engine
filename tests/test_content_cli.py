"""``python -m exocortex_engine.content`` against synthetic workspaces (no real data)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from _workspace import INDEX7, WorkspaceCase

DAY = WorkspaceCase.TODAY
CREF = f"content/{DAY}"
SURVEY = "contexts/survey_sessions/2026-03-10_synthetic-survey.md"
OLD_SURVEY = "contexts/survey_sessions/2026-01-05_older.md"
WORKLOG = "contexts/work_logs/2026-03-10_alpha_update.md"
CTX_FILE = "projects/alpha/context/notes.md"
S_INDEX = "contexts/survey_sessions/INDEX.md"


def doc(l0: str, date: str = "", extra: str = "") -> str:
    fm = f"l0: {l0}\n" + (f"date: {date}\n" if date else "") + extra
    return f"---\n{fm}---\n\n# body\n\ntext\n"


class ContentCase(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(OLD_SURVEY, doc("an older survey", "2026-01-05", "tags: [a, b]\n"))
        self.write("contexts/MANIFEST.md", "manifest\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed contexts")
        self.git("push", "-q", "origin", "main")

    def ccli(self, *args: str, today: str = DAY):
        cmd = [sys.executable, "-m", "exocortex_engine.content", *args, "--today", today]
        proc = subprocess.run(cmd, cwd=self.ws, env=self.env, capture_output=True, text=True, encoding="utf-8")
        proc.first = proc.stdout.splitlines()[0] if proc.stdout else ""  # type: ignore[attr-defined]
        return proc

    def cjson(self, *args: str) -> tuple[int, dict]:
        p = self.ccli(*args, "--json")
        return p.returncode, json.loads(p.stdout)

    def ok(self, *args: str):
        p = self.ccli(*args)
        self.assertEqual(p.returncode, 0, p.stdout)
        return p

    def zero_writes(self, p, before, remote_before):
        self.assertEqual((p.returncode, p.first), (1, "result: rejected"), p.stdout)
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.refs(self.remote), remote_before)

    def snapshot(self):
        return self.refs(), self.refs(self.remote)

    def read_bytes(self, rel: str) -> bytes:
        return (self.ws / rel).read_bytes()


class StatusTests(ContentCase):
    def test_classification(self):
        self.write(SURVEY, doc("new survey"))
        self.write("inbox/todo.md", "todo\n")
        self.write("memory/fact.md", "fact\n")
        self.write(CTX_FILE, "ctx\n")
        self.write("rules/r.md", "architecture\n")
        self.write("projects/README.md", "not a project file\n")
        self.write(S_INDEX, "local index\n")
        code, st = self.cjson("status")
        self.assertEqual(code, 0, st)
        by = {t["branch"]: t for t in st["targets"]}
        self.assertEqual(sorted(by[CREF]["writes"]), ["contexts/survey_sessions/2026-03-10_synthetic-survey.md",
                                                     "inbox/todo.md", "memory/fact.md"])
        self.assertFalse(by[CREF]["exists"])
        self.assertEqual(by[CREF]["generated"], [S_INDEX])  # recomputed, not copied
        self.assertEqual(by["project/alpha"]["writes"], [CTX_FILE])
        self.assertEqual(sorted((o["path"], o["kind"]) for o in st["other"]),
                         [(S_INDEX, "generated"), ("projects/README.md", "other"), ("rules/r.md", "other")])
        before, objects = self.snapshot(), self.git("count-objects")
        self.ok("status")
        self.assertEqual((self.snapshot(), self.git("count-objects")), (before, objects))  # writes nothing at all

    def test_status_lists_waiting_paths_and_unlanded_dates(self):
        self.write(SURVEY, doc("new survey"))
        self.ok("commit", "--no-push")
        self.write("inbox/later.md", "later\n")
        code, st = self.cjson("status")
        self.assertEqual(code, 0)
        self.assertEqual(st["waiting"], {SURVEY: CREF})
        self.assertEqual(st["unlanded"], [{"branch": CREF, "commits": 1}])
        p = self.ok("status")
        self.assertIn("already committed, waiting to land", p.stdout)


class CommitTests(ContentCase):
    def test_commit_writes_branches_and_leaves_worktree(self):
        self.write(SURVEY, doc("new survey", "2026-03-10"))
        self.write(WORKLOG, doc("a log", "2026-03-10"))
        self.write(CTX_FILE, "ctx\n")
        head, status = self.rev("HEAD"), self.status()
        p = self.ok("commit", "-m", "docs: add synthetic survey", "--project-message", "alpha", "project(alpha): notes")
        self.assertEqual((self.rev("HEAD"), self.status()), (head, status))
        self.assertEqual(self.git("log", "-1", "--format=%s%n%P", CREF).split("\n")[:2],
                         ["docs: add synthetic survey", head])
        self.assertEqual(self.show(CREF, SURVEY), doc("new survey", "2026-03-10"))
        self.assertEqual(self.show("project/alpha", CTX_FILE), "ctx\n")
        self.assertIsNone(self.show(CREF, CTX_FILE))
        self.assertEqual(self.git("log", "-1", "--format=%s", "project/alpha").strip(), "project(alpha): notes")
        self.assertEqual(self.rev(CREF, self.remote), self.rev(CREF))
        self.assertIn("your working tree was not changed", p.stdout)
        # second run: nothing new
        p = self.ccli("commit")
        self.assertEqual((p.returncode, p.first), (0, "result: noop"), p.stdout)

    def test_index_recomputed_from_target_tree_in_same_commit(self):
        # content/<today> already has another survey (committed earlier today, not in the checkout)
        g = __import__("exocortex_engine.gitref", fromlist=["Git"]).Git(self.ws)
        main = self.rev("main")
        other = "contexts/survey_sessions/2026-03-09_from-elsewhere.md"
        tree = g.build_tree(main, g.blob_changes({other: doc("written elsewhere", "2026-03-09")}))
        c = g.commit_tree(tree, [main], "earlier")
        self.git("branch", CREF, c)
        self.write(SURVEY, doc("new survey", "2026-03-10"))
        self.write(S_INDEX, "a local index the CLI must not copy\n")
        self.ok("commit", "--no-push")
        self.assertEqual(self.git("rev-parse", f"{CREF}^").strip(), c)
        files = self.git("show", "--name-only", "--format=", CREF).split()
        self.assertEqual(sorted(files), sorted([S_INDEX, SURVEY]))  # one commit: content + its index
        index = self.show(CREF, S_INDEX)
        self.assertTrue(index.startswith("# contexts/survey_sessions/ — L0 導航索引\n"))
        self.assertIn("共 3 筆。", index)
        rows = [ln for ln in index.splitlines() if ln.startswith("| 2026")]
        self.assertEqual([r.split("|")[1].strip() for r in rows], ["2026-03-10", "2026-03-09", "2026-01-05"])
        self.assertIn("| 2026-01-05 | an older survey | a, b |", index)
        self.assertNotIn("must not copy", index)

    def test_precondition_refusal_writes_nothing(self):
        g = __import__("exocortex_engine.gitref", fromlist=["Git"]).Git(self.ws)
        main = self.rev("main")
        # Someone changed the old survey on content/<today> after our checkout's HEAD.
        tree = g.build_tree(main, g.blob_changes({OLD_SURVEY: doc("edited elsewhere", "2026-01-05")}))
        self.git("branch", CREF, g.commit_tree(tree, [main], "elsewhere"))
        self.git("push", "-q", "origin", CREF)
        self.write(OLD_SURVEY, doc("edited here", "2026-01-05"))
        self.write("inbox/fine.md", "fine\n")
        self.write(CTX_FILE, "fine too\n")
        before = self.snapshot()
        p = self.ccli("commit")
        self.zero_writes(p, *before)
        self.assertIn(OLD_SURVEY, p.stdout)
        self.assertIn("REFUSED", p.stdout)
        st = self.ccli("status")
        self.assertEqual((st.returncode, st.first), (1, "result: rejected"))
        self.assertIn(OLD_SURVEY, st.stdout)

    def test_second_commit_of_own_edit_passes_but_not_over_someone_elses(self):
        self.write(SURVEY, doc("v1"))
        self.write(CTX_FILE, "ctx v1\n")
        self.ok("commit", "--no-push")
        self.write(SURVEY, doc("v2"))
        self.write(CTX_FILE, "ctx v2\n")
        self.ok("commit", "--no-push")  # the branch holds this checkout's own v1
        self.assertEqual(self.show(CREF, SURVEY), doc("v2"))
        self.assertEqual(self.show("project/alpha", CTX_FILE), "ctx v2\n")
        # another writer changes the survey on the branch; our next edit must not overwrite it
        g = __import__("exocortex_engine.gitref", fromlist=["Git"]).Git(self.ws)
        tip = self.rev(CREF)
        c = g.commit_tree(g.build_tree(tip, g.blob_changes({SURVEY: doc("from another session")})), [tip], "x")
        self.git("update-ref", f"refs/heads/{CREF}", c, tip)
        self.write(SURVEY, doc("v3"))
        before = self.snapshot()
        p = self.ccli("commit")
        self.zero_writes(p, *before)
        self.assertIn(SURVEY, p.stdout)

    def test_project_branch_missing_refused(self):
        self.write("projects/nope/context/a.md", "a\n")
        self.write(SURVEY, doc("new survey"))
        before = self.snapshot()
        p = self.ccli("commit")
        self.zero_writes(p, *before)
        self.assertIn("project/nope does not exist", p.stdout)
        self.assertIn("/exo:project new nope", p.stdout)

    def test_delete_on_new_branch_is_first_commit(self):
        (self.ws / OLD_SURVEY).unlink()
        st = self.ccli("status")
        self.assertIn(f"DELETE {OLD_SURVEY}", st.stdout)
        self.assertIn("new branch from main", st.stdout)
        self.ok("commit", "--no-push")
        self.assertEqual(self.git("rev-parse", f"{CREF}^").strip(), self.rev("main"))
        self.assertIsNone(self.show(CREF, OLD_SURVEY))
        self.assertNotIn("older survey", self.show(CREF, S_INDEX))
        self.assertIn("共 0 筆。", self.show(CREF, S_INDEX))

    def test_binary_file_kept_byte_for_byte(self):
        data = bytes(range(256)) * 3
        (self.ws / "inbox").mkdir()
        (self.ws / "inbox/blob.bin").write_bytes(data)
        self.ok("commit", "--no-push")
        out = subprocess.run(["git", "cat-file", "blob", f"{CREF}:inbox/blob.bin"], cwd=self.ws, env=self.env,
                             capture_output=True).stdout
        self.assertEqual(out, data)

    def test_only_limits_paths(self):
        self.write(SURVEY, doc("new survey"))
        self.write("inbox/todo.md", "todo\n")
        self.ok("commit", "--only", "inbox", "--no-push")
        self.assertIsNotNone(self.show(CREF, "inbox/todo.md"))
        self.assertIsNone(self.show(CREF, SURVEY))

    def test_unreadable_frontmatter_refused(self):
        self.write(SURVEY, "---\nl0: &anchor x\n---\n")
        before = self.snapshot()
        p = self.ccli("commit")
        self.zero_writes(p, *before)
        self.assertIn("frontmatter uses YAML the index cannot read", p.stdout)


class ProjectFileTests(ContentCase):
    MD = ("---\nlast_updated: 2026-03-10\nstatus: active\nl0: alpha edited here\n---\n\n# Alpha\n\n"
          "## 目標 / 動機\n\ngoal\n\n## 現況\n\nnow edited\n\n## 下一步\n\n- next\n")

    def test_project_md_goes_through_update_validation(self):
        before = self.snapshot()
        self.write("projects/alpha/PROJECT.md", self.MD.replace("## 現況", "## Now"))
        p = self.ccli("commit")
        self.zero_writes(p, *before)
        self.assertIn("missing section '## 現況'", p.stdout)
        self.write("projects/alpha/PROJECT.md", self.MD.replace("2026-03-10", "2026-03-01"))
        p = self.ok("commit", "--no-push")
        self.assertIn("last_updated is 2026-03-01", p.stdout)  # not rewritten
        self.assertEqual(self.show("project/alpha", "projects/alpha/PROJECT.md"), self.MD.replace("2026-03-10", "2026-03-01"))

    def test_index_rider_carries_only_the_projects_row(self):
        row_old = "| alpha | active | 2026-01-02 | first synthetic project | box-a | focus |"
        row_new = "| alpha | paused | 2026-03-10 | first synthetic project | box-a | focus |"
        edited = INDEX7.replace(row_old, row_new)
        self.write("projects/INDEX.md", edited)
        self.write(CTX_FILE, "ctx\n")
        p = self.ok("commit", "--no-push")
        index = self.show("project/alpha", "projects/INDEX.md")
        self.assertIn(row_new, index)
        self.assertEqual(index, edited)
        self.assertEqual(sorted(self.git("show", "--name-only", "--format=", "project/alpha").split()),
                         sorted([CTX_FILE, "projects/INDEX.md"]))
        # land project: main's INDEX now equals the working tree's, so nothing blocks
        p = self.cli("land", "project", "alpha", "m1", "--no-push")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(), "")
        # rows of other projects and prose are not committed
        self.write("projects/INDEX.md", edited.replace("done one", "edited row of beta"))
        self.write(CTX_FILE, "ctx 2\n")
        p = self.ok("commit", "--no-push")
        self.assertIn("changes other than the rows of the projects committed here are not committed", p.stdout)
        self.assertNotIn("edited row of beta", self.show("project/alpha", "projects/INDEX.md"))

    def test_index_only_change_is_not_committed(self):
        self.write("projects/INDEX.md", INDEX7.replace("done one", "x"))
        p = self.ccli("commit")
        self.assertEqual((p.returncode, p.first), (0, "result: noop"), p.stdout)
        self.assertIn("change project status with /exo:project", p.stdout)


if __name__ == "__main__":
    unittest.main()
