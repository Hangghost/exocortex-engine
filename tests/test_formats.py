"""INDEX.md, PROJECT.md and work_log parsing/writing (pure text, no git)."""
from __future__ import annotations

import unittest

import _workspace  # noqa: F401  (puts src/ on sys.path)
from _workspace import INDEX7
from exocortex_engine import index_md, project_md, worklog

INDEX4_REORDERED = (
    "| description | name | last_updated | status |\n"
    "|---|---|---|---|\n"
    "| d1 | one | 2026-02-01 | active |\n"
)


class IndexTests(unittest.TestCase):
    def test_rows_by_column_name(self):
        rows = index_md.rows(INDEX7)
        self.assertEqual([r["name"] for r in rows], ["alpha", "beta"])  # roadmap table is not the index
        self.assertEqual(rows[0]["focus"], "focus")
        self.assertEqual(rows[0]["parent"], "")  # short row
        r = index_md.rows(INDEX4_REORDERED)[0]
        self.assertEqual((r["name"], r["status"], r["description"]), ("one", "active", "d1"))

    def test_update_touches_only_named_cells(self):
        new = index_md.upsert(INDEX7, "alpha", {"last_updated": "2026-03-10"})
        old_lines, new_lines = INDEX7.splitlines(), new.splitlines()
        changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
        self.assertEqual(len(old_lines), len(new_lines))
        self.assertEqual(changed, [(
            "| alpha | active | 2026-01-02 | first synthetic project | box-a | focus |",
            "| alpha | active | 2026-03-10 | first synthetic project | box-a | focus |",
        )])

    def test_update_by_name_in_reordered_table(self):
        new = index_md.upsert(INDEX4_REORDERED, "one", {"status": "paused"})
        self.assertIn("| d1 | one | 2026-02-01 | paused |", new)

    def test_new_row_appended_at_table_end_and_rest_kept(self):
        new = index_md.upsert(INDEX7, "delta", {"status": "active", "last_updated": "2026-03-10", "description": "a | b"})
        self.assertTrue(new.startswith(INDEX7.split("\n## Planned")[0].rsplit("\n", 2)[0]))
        self.assertIn("| delta | active | 2026-03-10 | a \\| b | | | |\n\n## Planned (Roadmap)", new)
        self.assertTrue(new.endswith(INDEX7.split("\n## Planned")[1]))
        self.assertEqual(index_md.get_row(new, "delta")["description"], "a | b")

    def test_crlf_preserved(self):
        crlf = INDEX7.replace("\n", "\r\n")
        new = index_md.upsert(crlf, "beta", {"status": "archived"})
        self.assertNotIn("\n", new.replace("\r\n", ""))
        self.assertIn("| beta | archived | 2026-01-03 |", new)

    def test_missing_required_column(self):
        with self.assertRaises(index_md.IndexError_):
            index_md.rows("| name | status |\n|---|---|\n| a | b |\n")

    def test_set_row_line(self):
        row = "| alpha | paused | 2026-03-09 | changed | box-a | focus |\n"
        new = index_md.set_row_line(INDEX7, "alpha", row)
        self.assertEqual(index_md.get_row(new, "alpha")["status"], "paused")
        self.assertEqual(index_md.get_row(new, "beta")["status"], "completed")


PROJECT = """---
last_updated: 2026-01-02
status: active
l0: synthetic
custom_key: keep me
---

# Alpha

## 目標 / 動機

goal text

## 自訂段落

custom body
```
## not a heading inside a fence
```

## 現況

<!-- 待填入 -->

## 下一步

### Phase 2
> blockquote first
<!-- comment
spanning lines -->
<!-- 待填入 -->

- [ ] the real next action is here
- second
"""


class ProjectMdTests(unittest.TestCase):
    def test_sections_by_title_and_unknown_kept(self):
        titles = [t for t, _ in project_md.sections(PROJECT)]
        self.assertEqual(titles, ["目標 / 動機", "自訂段落", "現況", "下一步"])
        self.assertIn("## not a heading inside a fence", project_md.section(PROJECT, "自訂段落"))
        self.assertTrue(project_md.is_empty(project_md.section(PROJECT, "現況")))

    def test_first_actionable_skips_noise(self):
        nxt = project_md.first_actionable(project_md.section(PROJECT, "下一步"))
        self.assertEqual(nxt, "the real next action is here")
        self.assertIsNone(project_md.first_actionable("<!-- 待填入 -->\n"))
        labels = "**Phase 2**\n短期（之後）：\n- ~~done already~~\n1. 真正的動作\n"
        self.assertEqual(project_md.first_actionable(labels), "真正的動作")

    def test_set_frontmatter_keeps_everything_else(self):
        new = project_md.set_frontmatter(PROJECT, "last_updated", "2026-03-10")
        self.assertEqual(new.replace("2026-03-10", "2026-01-02", 1), PROJECT)
        self.assertEqual(project_md.frontmatter(new).get("custom_key"), "keep me")

    def test_validate(self):
        self.assertEqual(project_md.validate(PROJECT), [])
        bad = PROJECT.replace("status: active", "status: busy").replace("## 現況", "## Now")
        errs = project_md.validate(bad)
        self.assertTrue(any("status" in e for e in errs))
        self.assertTrue(any("現況" in e for e in errs))
        self.assertTrue(project_md.validate("no frontmatter"))
        long_l0 = PROJECT.replace("l0: synthetic", "l0: " + "x" * 121)
        self.assertTrue(any("l0" in e for e in project_md.validate(long_l0)))

    def test_dropped_sections(self):
        self.assertEqual(project_md.dropped_sections(PROJECT, PROJECT.replace("## 自訂段落", "### 自訂段落")), ["自訂段落"])

    def test_render_new_is_valid(self):
        text = project_md.render_new("Delta", "build the delta", "2026-03-10", next_="first step")
        self.assertEqual(project_md.validate(text), [])
        self.assertEqual([t for t, _ in project_md.sections(text)], list(project_md.SECTIONS))
        self.assertEqual(project_md.first_actionable(project_md.section(text, "下一步")), "first step")
        self.assertTrue(project_md.is_empty(project_md.section(text, "現況")))


SECTION = "### 進展\n\n- did\n\n### 經驗與觀察\n\n- learned\n\n### 下一步\n\n- next\n"


class WorklogTests(unittest.TestCase):
    def test_new_then_append(self):
        first = worklog.append(None, name="alpha", date="2026-03-10", hhmm="09:00", body=SECTION, l0="first")
        fm = project_md.frontmatter(first)
        self.assertEqual((fm.get("project"), fm.get("date"), fm.get("type"), fm.get("l0")),
                         ("alpha", "2026-03-10", "update", "first"))
        second = worklog.append(first, name="alpha", date="2026-03-10", hhmm="15:00", body=SECTION, l0="day total")
        self.assertTrue(second.startswith(first.replace("l0: first", "l0: day total")))
        self.assertEqual(second.count("## 09:00"), 1)
        self.assertIn("\n## 15:00\n", second)

    def test_validate_section(self):
        self.assertEqual(worklog.validate_section(SECTION), [])
        self.assertTrue(worklog.validate_section("### 進展\n- x\n"))
        self.assertTrue(worklog.validate_section("## 10:00\n" + SECTION))

    def test_parse_name(self):
        self.assertEqual(worklog.parse_name("contexts/work_logs/2026-03-10_my-proj_update.md"), ("2026-03-10", "my-proj"))


if __name__ == "__main__":
    unittest.main()
