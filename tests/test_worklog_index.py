"""``update`` and ``land content`` keep ``contexts/work_logs/INDEX.md`` in step with the files.

Both write to a content branch (or main) directly instead of going through
``/exo:content commit``, so each has to recompute the generated index itself.
"""
from __future__ import annotations

import unittest

from _workspace import WorkspaceCase
from test_project_cli import SECTION, alpha_md

WL = "contexts/work_logs/2026-03-10_alpha_update.md"
INDEX = "contexts/work_logs/INDEX.md"
CREF = "content/2026-03-10"


class WorklogIndexTests(WorkspaceCase):
    def update(self, now="09:30", md=None):
        p = self.cli("update", "alpha", "apply", "--project-md", self.tmpfile("p.md", md or alpha_md()),
                     "--worklog-section", self.tmpfile("s.md", SECTION), now=now)
        self.assertEqual(p.returncode, 0, p.stdout)
        return p

    def test_update_regenerates_index_in_the_same_commit(self):
        self.update()
        index = self.show(CREF, INDEX)
        self.assertIsNotNone(index, "update must create the work_logs index")
        self.assertIn("共 1 筆", index)
        self.assertIn("2026-03-10_alpha_update.md", index)
        changed = self.git("show", "--name-only", "--format=", CREF).split()
        self.assertEqual(sorted(changed), sorted([INDEX, WL]))

    def test_second_update_same_day_keeps_one_entry(self):
        self.update()
        self.update(now="16:05", md=alpha_md("- later"))
        index = self.show(CREF, INDEX)
        self.assertIn("共 1 筆", index)
        rows = [ln for ln in index.splitlines() if ln.startswith("|") and "alpha_update.md" in ln]
        self.assertEqual(len(rows), 1)

    def test_existing_index_gets_the_new_row(self):
        self.write("contexts/work_logs/2026-01-01_old_update.md",
                   "---\nl0: an old log\nproject: old\ndate: 2026-01-01\ntype: update\n---\n\n# old\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed old work_log")
        self.git("push", "-q", "origin", "main")
        self.update()
        index = self.show(CREF, INDEX)
        self.assertIn("共 2 筆", index)
        self.assertIn("2026-01-01_old_update.md", index)
        self.assertIn("2026-03-10_alpha_update.md", index)

    def test_land_content_lands_the_regenerated_index(self):
        self.update()
        p = self.cli("land", "content")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("共 1 筆", self.show("main", INDEX))

    def test_land_recomputes_a_stale_index_from_the_branch(self):
        """A content branch written before this fix carries a work_log and a stale index."""
        self.update()
        tip = self.rev(CREF)
        stale = "# contexts/work_logs/ — L0 導航索引\n\n共 0 筆。\n"
        blob = self.git("hash-object", "-w", "--stdin", input=stale).strip() if False else None
        del blob
        # rebuild the branch tip without the index to mimic the old engine's output
        self.git("checkout", "-q", "--detach", CREF)
        self.git("rm", "-q", INDEX)
        self.git("commit", "-q", "-m", "drop index (simulate old engine)")
        self.git("branch", "-f", CREF, "HEAD")
        self.git("checkout", "-q", "main")
        self.assertNotEqual(self.rev(CREF), tip)
        self.assertIsNone(self.show(CREF, INDEX))
        p = self.cli("land", "content")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("共 1 筆", self.show("main", INDEX))


if __name__ == "__main__":
    unittest.main()
