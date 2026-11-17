"""Ref-anchored commits: CAS, atomicity, and the working tree stays untouched."""
from __future__ import annotations

import unittest

from _workspace import WorkspaceCase
from exocortex_engine.gitref import ZERO, CASConflict, Git, RefUpdate, Refused, three_way_changes


class GitRefTests(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.g = Git(self.ws)

    def commit_on(self, base: str, files: dict, msg: str = "c") -> str:
        tree = self.g.build_tree(base, self.g.blob_changes(files))
        return self.g.commit_tree(tree, [base], msg)

    def test_commit_does_not_touch_worktree_or_index(self):
        head = self.rev("HEAD")
        index_before = (self.ws / ".git" / "index").read_bytes()
        c = self.commit_on(head, {"contexts/x.md": "hello\r\nworld\n", "README.md": None}, "ünïcode ✓ 訊息")
        self.g.update_refs([RefUpdate("refs/heads/content/2026-03-10", c, ZERO)])
        self.assertEqual(self.rev("HEAD"), head)
        self.assertEqual(self.status(), "")
        self.assertEqual((self.ws / ".git" / "index").read_bytes(), index_before)
        self.assertFalse((self.ws / "contexts").exists())
        self.assertEqual(self.show("content/2026-03-10", "contexts/x.md"), "hello\r\nworld\n")  # bytes kept
        self.assertIsNone(self.show("content/2026-03-10", "README.md"))
        self.assertEqual(self.git("log", "-1", "--format=%s", "content/2026-03-10").strip(), "ünïcode ✓ 訊息")

    def test_cas_conflict_rejected(self):
        head = self.rev("HEAD")
        a = self.commit_on(head, {"a.md": "a"})
        b = self.commit_on(head, {"b.md": "b"})
        self.g.update_refs([RefUpdate("refs/heads/side", a, ZERO)])
        with self.assertRaises(CASConflict):  # stale old value
            self.g.update_refs([RefUpdate("refs/heads/side", b, head)])
        with self.assertRaises(CASConflict):  # "must not exist" but it does
            self.g.update_refs([RefUpdate("refs/heads/side", b, ZERO)])
        self.assertEqual(self.rev("side"), a)

    def test_multi_ref_update_is_atomic(self):
        head = self.rev("HEAD")
        a = self.commit_on(head, {"a.md": "a"})
        with self.assertRaises(CASConflict):
            self.g.update_refs([
                RefUpdate("refs/heads/one", a, ZERO),
                RefUpdate("refs/heads/project/alpha", a, a),  # wrong old value
            ])
        self.assertIsNone(self.rev("one"))
        self.assertEqual(self.rev("project/alpha"), head)

    def test_advance_checked_out_branch(self):
        head = self.rev("HEAD")
        c = self.commit_on(head, {"new.md": "n\n"})
        self.assertEqual(self.g.advance("refs/heads/main", c, head), "merge")
        self.assertEqual(self.rev("HEAD"), c)
        self.assertEqual(self.status(), "")
        self.assertTrue((self.ws / "new.md").exists())  # worktree followed the ref

    def test_advance_refuses_dirty_checkout(self):
        head = self.rev("HEAD")
        c = self.commit_on(head, {"new.md": "n\n"})
        self.write("README.md", "edited\n")
        with self.assertRaises(Refused):
            self.g.advance("refs/heads/main", c, head)
        self.assertEqual(self.rev("main"), head)

    def test_advance_not_checked_out_uses_update_ref(self):
        self.git("switch", "-q", "-c", "scratch")
        head = self.rev("main")
        c = self.commit_on(head, {"new.md": "n\n"})
        self.write("README.md", "dirty but not on main\n")
        self.assertEqual(self.g.advance("refs/heads/main", c, head), "update-ref")
        self.assertEqual(self.rev("main"), c)
        self.assertEqual(self.git("branch", "--show-current").strip(), "scratch")
        self.assertFalse((self.ws / "new.md").exists())

    def test_three_way_changes(self):
        base = self.rev("HEAD")
        ours = self.commit_on(base, {"shared.md": "ours", "ours-only.md": "o"})
        theirs = self.commit_on(base, {"shared.md": "theirs", "theirs-only.md": "t"})
        changes, conflicts = three_way_changes(self.g, base, ours, theirs)
        self.assertEqual(conflicts, ["shared.md"])
        self.assertEqual(sorted(changes), ["theirs-only.md"])

    def test_push_reports_failure_without_raising(self):
        self.git("remote", "set-url", "origin", str(self.tmp / "missing.git"))
        self.assertTrue(self.g.push("refs/heads/main:refs/heads/main").startswith("failed: "))
        self.git("remote", "remove", "origin")
        self.assertTrue(self.g.push("refs/heads/main").startswith("skipped: "))


if __name__ == "__main__":
    unittest.main()
