"""inbox/todos.md parsing, rendering and transformations (pure text, synthetic data)."""
from __future__ import annotations

import unittest

import _workspace  # noqa: F401  (puts src/ on sys.path)
from exocortex_engine import todo_md
from exocortex_engine.todo_md import InputError, TodoError

DAY = "2026-03-10"
SAMPLE = (
    "# Todos\n\n"
    "## Active\n\n"
    "- [ ] [ready][project:alpha][ticket:fix-widget][added:2026-03-01][due:2026-03-20] **Fix the widget** — it squeaks\n"
    "  - symptom: squeak\n"
    "  - next: oil it\n"
    "\n"
    "<!-- a comment\n- [ ] [ready][ticket:hidden] **not an entry**\n-->\n"
    "### Misc\n\n"
    "- [ ] [needs-triage][ticket:second-one][added:2026-03-02][source:elsewhere] **Second** — s2\n"
    "- [ ] bare line without tags\n"
    "\n"
    "## Done\n\n"
    "### 2026-03-05\n\n"
    "- [x] [project:alpha][ticket:old-one][added:2026-03-01] **Old one** — was done\n"
    "  - 完成：2026-03-05 shipped\n"
    "- [x] [dropped][ticket:gone-one][added:2026-03-01] **Gone** — nope\n"
    "  - 放棄：2026-03-05 no longer needed\n"
)


class ParseTests(unittest.TestCase):
    def test_entries_and_fields(self):
        doc = todo_md.parse(SAMPLE)
        by = {e.ticket: e for e in doc.entries if e.ticket}
        self.assertEqual(sorted(by), ["fix-widget", "gone-one", "old-one", "second-one"])  # the comment hides one
        e = by["fix-widget"]
        self.assertEqual((e.title, e.summary, e.project, e.status, e.added, e.due, e.state),
                         ("Fix the widget", "it squeaks", "alpha", "ready", "2026-03-01", "2026-03-20", "open"))
        self.assertEqual(e.raw.splitlines()[1:], ["  - symptom: squeak", "  - next: oil it"])
        self.assertEqual(by["second-one"].extra, ["source:elsewhere"])  # unknown tag kept, not interpreted
        self.assertEqual((by["old-one"].state, by["gone-one"].state), ("done", "dropped"))
        self.assertEqual(len(doc.warnings), 1)  # the tagless bare line
        self.assertIn("no [ticket:", doc.warnings[0])

    def test_todos_filters_and_json_fields(self):
        doc = todo_md.parse(SAMPLE)
        self.assertEqual([t["ticket"] for t in doc.todos()], ["fix-widget", "second-one", None])
        self.assertEqual([t["ticket"] for t in doc.todos(include_done=True)],
                         ["fix-widget", "second-one", None, "old-one", "gone-one"])
        self.assertEqual([t["ticket"] for t in doc.todos(project="alpha", include_done=True)], ["fix-widget", "old-one"])
        self.assertEqual([t["ticket"] for t in doc.todos(status="needs-triage")], ["second-one"])
        done = [t for t in doc.todos(include_done=True) if t["ticket"] == "old-one"][0]
        self.assertEqual((done["status"], done["state"], done["due"]), (None, "done", None))
        self.assertEqual(list(doc.todos()[0]), ["ticket", "title", "project", "status", "summary", "added", "due", "state", "raw"])

    def test_invalid_due_reports_null_with_warning(self):
        doc = todo_md.parse(SAMPLE.replace("due:2026-03-20", "due:soon"))
        self.assertIsNone(doc.todos()[0]["due"])
        self.assertTrue(any("not a YYYY-MM-DD" in w for w in doc.warnings))

    def test_roundtrip_is_byte_for_byte_and_crlf_bom_normalize(self):
        for text in (SAMPLE, todo_md.SKELETON, SAMPLE.rstrip("\n"), SAMPLE + "\n", ""):
            if text:
                self.assertEqual(todo_md.parse(text).text(), text)
        crlf = "﻿" + SAMPLE.replace("\n", "\r\n")
        doc = todo_md.parse(crlf)
        self.assertEqual(doc.text(), SAMPLE)
        self.assertNotIn("\r", doc.todos()[0]["raw"])

    def test_missing_sections_are_named(self):
        for text, name in (("# T\n\n## Done\n", "Active"), ("# T\n\n## Active\n", "Done"), ("## Done\n## Active\n", "Done")):
            with self.assertRaises(TodoError) as cm:
                todo_md.parse(text)
            self.assertIn(name, str(cm.exception))


class AddTests(unittest.TestCase):
    def add(self, text=SAMPLE, **kw):
        kw.setdefault("today", DAY)
        return todo_md.add(text, ticket=kw.pop("ticket", "new-one"), title=kw.pop("title", "新的標題"), **kw)

    def test_appends_to_end_of_active_and_leaves_the_rest(self):
        res = self.add(summary="摘要", project="alpha", due="2026-04-01", body=["- 現象：x", "", "- 下一步：y"])
        self.assertTrue(res.changed)
        head = "- [ ] [needs-triage][project:alpha][ticket:new-one][added:2026-03-10][due:2026-04-01] **新的標題** — 摘要"
        lines = res.text.split("\n")
        i = lines.index(head)
        self.assertEqual(lines[i + 1:i + 4], ["  - 現象：x", "", "  - 下一步：y"])
        self.assertEqual(lines[i + 4:i + 6], ["", "## Done"])
        self.assertEqual(lines[i - 1], "- [ ] bare line without tags")  # appended after the last entry
        self.assertEqual(res.text.replace(head + "\n  - 現象：x\n\n  - 下一步：y\n", ""), SAMPLE)
        self.assertEqual(todo_md.parse(res.text).find("new-one")[0].title, "新的標題")

    def test_skeleton_gets_the_first_entry(self):
        res = self.add(todo_md.SKELETON, status="ready")
        self.assertEqual(res.text, "# Todos\n\n## Active\n\n- [ ] [ready][ticket:new-one][added:2026-03-10] **新的標題**\n\n## Done\n")

    def test_replay_is_noop_and_other_duplicates_are_refused(self):
        first = self.add().text
        self.assertFalse(self.add(first).changed)
        with self.assertRaises(TodoError) as cm:
            self.add(first, title="different")
        self.assertIn("新的標題", str(cm.exception))
        with self.assertRaises(TodoError):
            self.add(ticket="old-one")  # tickets are unique across Done too

    def test_bad_input(self):
        for kw in ({"ticket": "Bad Slug"}, {"ticket": "a--b"}, {"title": "  "}, {"title": "has **bold**"},
                   {"title": "two\nlines"}, {"status": "nope"}, {"due": "2026-13-01"}, {"summary": "a\nb"},
                   {"project": "has space"}, {"body": ["## sneaky heading"]}):
            with self.assertRaises(InputError, msg=kw):
                self.add(**kw)


class SetTests(unittest.TestCase):
    def test_changes_tags_in_fixed_order_and_keeps_body(self):
        res = todo_md.set_fields(SAMPLE, "second-one", today=DAY, status="ready", project="alpha", due="2026-05-01")
        self.assertIn("- [ ] [ready][project:alpha][ticket:second-one][added:2026-03-02][due:2026-05-01]"
                      "[source:elsewhere] **Second** — s2\n", res.text)
        res = todo_md.set_fields(SAMPLE, "fix-widget", today=DAY, status="discuss-first", note="checked")
        self.assertIn("[discuss-first][project:alpha]", res.text)
        self.assertIn("  - next: oil it\n  - 2026-03-10 備註：checked\n\n<!--", res.text)  # note after the last sub-item

    def test_noop_refusals_and_input_errors(self):
        self.assertFalse(todo_md.set_fields(SAMPLE, "fix-widget", today=DAY, status="ready", project="alpha").changed)
        with self.assertRaises(TodoError):
            todo_md.set_fields(SAMPLE, "old-one", today=DAY, note="x")  # in Done
        with self.assertRaises(TodoError):
            todo_md.set_fields(SAMPLE, "missing", today=DAY, note="x")
        with self.assertRaises(InputError):
            todo_md.set_fields(SAMPLE, "fix-widget", today=DAY)
        with self.assertRaises(InputError):
            todo_md.set_fields(SAMPLE, "fix-widget", today=DAY, note="  ")
        with self.assertRaises(InputError):
            todo_md.set_fields(SAMPLE, "fix-widget", today=DAY, status="bogus")

    def test_duplicate_tickets_are_ambiguous(self):
        dup = SAMPLE.replace("## Done\n", "- [ ] [ticket:second-one] again\n\n## Done\n", 1)
        with self.assertRaises(TodoError) as cm:
            todo_md.set_fields(dup, "second-one", today=DAY, note="x")
        self.assertIn("2 times", str(cm.exception))


class DoneTests(unittest.TestCase):
    def test_moves_into_new_batch_on_top(self):
        res = todo_md.done(SAMPLE, "fix-widget", today=DAY, note="oiled")
        lines = res.text.split("\n")
        done_at = lines.index("## Done")
        self.assertEqual(lines[done_at:done_at + 8], [
            "## Done", "", "### 2026-03-10", "",
            "- [x] [project:alpha][ticket:fix-widget][added:2026-03-01][due:2026-03-20] **Fix the widget** — it squeaks",
            "  - symptom: squeak", "  - next: oil it", "  - 完成：2026-03-10 oiled"])
        self.assertEqual(lines[done_at + 8:done_at + 10], ["", "### 2026-03-05"])
        self.assertNotIn("**Fix the widget**", "\n".join(lines[:done_at]))
        self.assertEqual(todo_md.parse(res.text).find("fix-widget")[0].state, "done")

    def test_same_day_batch_is_reused_and_dropped_needs_a_reason(self):
        first = todo_md.done(SAMPLE, "fix-widget", today=DAY).text
        second = todo_md.done(first, "second-one", today=DAY, dropped=True, note="duplicate").text
        self.assertEqual(second.count("### 2026-03-10"), 1)
        e = todo_md.parse(second).find("second-one")[0]
        self.assertEqual((e.state, e.status, e.extra), ("dropped", None, ["source:elsewhere"]))
        self.assertIn("- [x] [dropped][ticket:second-one][added:2026-03-02][source:elsewhere] **Second** — s2\n"
                      "  - 放棄：2026-03-10 duplicate", second)
        with self.assertRaises(InputError):
            todo_md.done(SAMPLE, "fix-widget", today=DAY, dropped=True)
        with self.assertRaises(InputError):
            todo_md.done(SAMPLE, "fix-widget", today=DAY, dropped=True, note=" ")

    def test_noop_and_refusal(self):
        self.assertFalse(todo_md.done(SAMPLE, "old-one", today=DAY).changed)
        with self.assertRaises(TodoError):
            todo_md.done(SAMPLE, "missing", today=DAY)

    def test_last_entry_leaves_a_clean_active_section(self):
        text = todo_md.add(todo_md.SKELETON, ticket="only", title="t", today=DAY).text
        res = todo_md.done(text, "only", today=DAY)
        self.assertEqual(res.text, "# Todos\n\n## Active\n\n## Done\n\n### 2026-03-10\n\n"
                                   "- [x] [ticket:only][added:2026-03-10] **t**\n  - 完成：2026-03-10\n")


if __name__ == "__main__":
    unittest.main()
