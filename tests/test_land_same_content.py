"""Section 4: a dirty checkout of main whose files already equal what lands does not block."""
from __future__ import annotations

import unittest

from test_content_cli import CREF, CTX_FILE, OLD_SURVEY, S_INDEX, SURVEY, WORKLOG, ContentCase, doc
from exocortex_engine.gitref import Git, Refused


class LandSameContentTests(ContentCase):
    def commit_and_land(self):
        self.ok("commit")
        return self.cli("land", "content")

    def test_same_content_tracked_and_untracked_lands_and_keeps_bytes(self):
        self.write(OLD_SURVEY, doc("edited here", "2026-01-05"))  # tracked, modified
        self.write(SURVEY, doc("new survey", "2026-03-10"))  # untracked
        self.write("README.md", "unrelated local edit\n")
        before = {p: self.read_bytes(p) for p in (OLD_SURVEY, SURVEY)}
        p = self.commit_and_land()
        self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        self.assertIn("already committed, left as they are", p.stdout)
        self.assertEqual({p_: self.read_bytes(p_) for p_ in before}, before)
        self.assertEqual(self.status(), " M README.md\n")  # only the unrelated edit is left
        self.assertEqual(self.show("main", SURVEY), doc("new survey", "2026-03-10"))
        self.assertIsNotNone(self.show("main", S_INDEX))
        self.assertTrue((self.ws / S_INDEX).exists())
        self.assertIsNone(self.rev(CREF))
        self.assertIsNone(self.rev(CREF, self.remote))

    def test_staged_same_content_lands(self):
        self.write(SURVEY, doc("new survey"))
        self.git("add", SURVEY)
        p = self.commit_and_land()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(), "")

    def test_changed_after_commit_refused_with_zero_writes(self):
        self.write(SURVEY, doc("new survey"))
        self.write(OLD_SURVEY, doc("edited here", "2026-01-05"))
        self.ok("commit")
        self.write(SURVEY, doc("edited again after commit"))
        before = self.snapshot()
        files = {p: self.read_bytes(p) for p in (SURVEY, OLD_SURVEY)}
        p = self.cli("land", "content")
        self.zero_writes(p, *before)
        self.assertIn(SURVEY, p.stdout)
        self.assertNotIn(OLD_SURVEY, p.stdout.split("refused:")[1].split("commit them first")[0])
        self.assertIn("/exo:content commit", p.stdout)
        self.assertEqual({p_: self.read_bytes(p_) for p_ in files}, files)  # nothing normalized

    def test_staged_other_version_is_not_the_same(self):
        self.write(SURVEY, doc("v1"))
        self.git("add", SURVEY)
        self.write(SURVEY, doc("v2"))
        self.ok("commit")  # commits the working-tree version, v2
        before = self.snapshot()
        p = self.cli("land", "content")
        self.zero_writes(p, *before)
        self.assertIn(SURVEY, p.stdout)
        self.assertIn("v1", self.git("show", f":{SURVEY}"))  # the staged version is still there

    def test_deleted_file_counts_as_same(self):
        (self.ws / OLD_SURVEY).unlink()
        p = self.commit_and_land()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertFalse((self.ws / OLD_SURVEY).exists())
        self.assertIsNone(self.show("main", OLD_SURVEY))
        self.assertEqual(self.status(), "")

    def test_crlf_working_tree_is_not_misjudged(self):
        self.git("config", "core.autocrlf", "true")
        crlf_new = doc("crlf survey").replace("\n", "\r\n")
        crlf_old = doc("edited with crlf", "2026-01-05").replace("\n", "\r\n")
        self.write(SURVEY, crlf_new)
        self.write(OLD_SURVEY, crlf_old)
        p = self.commit_and_land()
        self.assertEqual(p.returncode, 0, p.stdout)
        raw = Git(self.ws).read_bytes("main", SURVEY)
        self.assertNotIn(b"\r\n", raw)  # the blob is LF, as git add would store it
        self.assertEqual(self.read_bytes(SURVEY), crlf_new.encode())  # the file kept its CRLF bytes
        self.assertEqual(self.read_bytes(OLD_SURVEY), crlf_old.encode())
        self.assertEqual(self.status(), "")

    def test_generated_index_that_differs_is_refused(self):
        self.write(SURVEY, doc("new survey"))
        self.ok("commit")
        self.write(S_INDEX, "a stale local index\n")
        before = self.snapshot()
        p = self.cli("land", "content")
        self.zero_writes(p, *before)
        self.assertIn(S_INDEX, p.stdout)
        self.assertIn("Generated index files", p.stdout)
        # the same bytes as the branch's index are fine
        self.write(S_INDEX, self.show(CREF, S_INDEX))
        p = self.cli("land", "content")
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_land_project_with_same_content(self):
        self.write(CTX_FILE, "ctx\n")
        self.ok("commit")
        p = self.cli("land", "project", "alpha", "notes")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(CTX_FILE, p.stdout)
        self.assertEqual(self.read_bytes(CTX_FILE), b"ctx\n")
        self.assertEqual(self.status(), "")

    def test_new_shares_the_rule(self):
        self.write("projects/zeta/context/.gitkeep", "")  # exactly what `new` creates
        p = self.cli("new", "zeta", "--title", "Z", "--goal", "g")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("projects/zeta/context/.gitkeep", p.stdout)
        self.assertEqual(self.status(), "")


class NormalizeSafetyTests(ContentCase):
    """Normalization re-checks every file and puts everything back on any surprise."""

    def setUp(self) -> None:
        super().setUp()
        self.g = Git(self.ws)
        self.head = self.rev("main")
        changes = {"contexts/a.md": "a\n", "contexts/b.md": "b\n", OLD_SURVEY: "old edited\n"}
        self.new = self.g.commit_tree(self.g.build_tree(self.head, self.g.blob_changes(changes)), [self.head], "c")
        for path, text in changes.items():
            self.write(path, text)
        self.git("add", "contexts/a.md")  # staged, same as landed

    def test_change_during_landing_restores_everything(self):
        plan = self.g.check_advance("refs/heads/main", self.new, self.head)
        self.assertEqual([s.path for s in plan.same], ["contexts/a.md", "contexts/b.md", OLD_SURVEY])
        self.write(OLD_SURVEY, "edited between plan and move\n")  # the last one changes
        cached = self.git("diff", "--cached")
        with self.assertRaises(Refused) as cm:
            self.g.advance("refs/heads/main", self.new, self.head, plan)
        self.assertIn(OLD_SURVEY, str(cm.exception))
        self.assertEqual(self.rev("main"), self.head)
        self.assertEqual(self.read_bytes("contexts/a.md"), b"a\n")
        self.assertEqual(self.read_bytes("contexts/b.md"), b"b\n")
        self.assertEqual(self.read_bytes(OLD_SURVEY), b"edited between plan and move\n")
        self.assertEqual(self.git("diff", "--cached"), cached)  # staging put back too

    def test_locked_file_is_a_clean_refusal(self):
        from pathlib import Path
        from unittest import mock

        plan = self.g.check_advance("refs/heads/main", self.new, self.head)
        real_unlink = Path.unlink

        def unlink(path, *a, **kw):
            if path.name == "b.md":
                raise PermissionError("in use by another program")
            return real_unlink(path, *a, **kw)

        with mock.patch.object(Path, "unlink", unlink), self.assertRaises(Refused) as cm:
            self.g.advance("refs/heads/main", self.new, self.head, plan)
        self.assertIn("in use by another program", str(cm.exception))
        self.assertEqual(self.rev("main"), self.head)
        self.assertEqual(self.read_bytes("contexts/a.md"), b"a\n")
        self.assertEqual(self.read_bytes("contexts/b.md"), b"b\n")
        self.assertIn("contexts/a.md", self.git("diff", "--cached", "--name-only"))

    def test_failed_fast_forward_restores_everything(self):
        plan = self.g.check_advance("refs/heads/main", self.new, self.head)
        before = {p: self.read_bytes(p) for p in ("contexts/a.md", "contexts/b.md", OLD_SURVEY)}
        cached = self.git("diff", "--cached")
        # After planning, something appears that git's own check refuses: a local edit to a
        # tracked file the move changes, made outside what was planned.
        extra = self.g.commit_tree(
            self.g.build_tree(self.new, self.g.blob_changes({"README.md": "theirs\n"})), [self.new], "d")
        self.write("README.md", "mine\n")
        with self.assertRaises(Refused):
            self.g.advance("refs/heads/main", extra, self.head, plan)
        self.assertEqual(self.rev("main"), self.head)
        self.assertEqual({p: self.read_bytes(p) for p in before}, before)
        self.assertEqual(self.git("diff", "--cached"), cached)
        self.assertEqual(self.read_bytes("README.md"), b"mine\n")


class EndToEndContentTests(ContentCase):
    """Section 7.3: write on main -> status -> commit -> edit again -> land refused ->
    commit -> land succeeds; the checkout ends clean and every ref is where it should be."""

    def test_full_cycle(self):
        self.write(SURVEY, doc("survey on main", "2026-03-10"))
        self.write(WORKLOG, doc("work log", "2026-03-10", "project: alpha\ntype: update\n"))
        self.write(CTX_FILE, "project context\n")
        st = self.ok("status")
        self.assertIn("3 path(s) to commit", st.stdout)
        self.ok("commit")
        self.write(SURVEY, doc("survey on main, edited after commit", "2026-03-10"))
        before = self.snapshot()
        p = self.cli("land", "content")
        self.zero_writes(p, *before)
        self.assertIn(SURVEY, p.stdout)
        st = self.ok("status")
        self.assertIn(f"- {WORKLOG} ({CREF})", st.stdout)  # waiting to land
        self.assertIn(f"- write  {SURVEY}", st.stdout)
        self.ok("commit")
        p = self.ccli("land")
        self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        p = self.cli("land", "project", "alpha", "context notes")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.status(), "")
        self.assertEqual(self.git("branch", "--show-current").strip(), "main")
        refs, remote = self.refs(), self.refs(self.remote)
        self.assertEqual(sorted(refs), ["refs/heads/main", "refs/heads/project/alpha"])
        self.assertEqual(refs, remote)
        self.assertEqual(self.rev("HEAD"), refs["refs/heads/main"])
        self.assertIn("edited after commit", self.show("main", SURVEY))
        self.assertIsNotNone(self.show("main", WORKLOG))
        self.assertEqual(self.show("main", CTX_FILE), "project context\n")
        index = self.show("main", S_INDEX)
        self.assertIn("survey on main, edited after commit", index)
        self.assertIsNotNone(self.show("main", "contexts/work_logs/INDEX.md"))
        subjects = self.git("log", "--first-parent", "--format=%s", "-2", "main").splitlines()
        self.assertEqual(subjects, ["Merge project/alpha: context notes", f"content: {self.TODAY} daily snapshot"])


if __name__ == "__main__":
    unittest.main()
