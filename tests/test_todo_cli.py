"""``python -m exocortex_engine.todo`` against synthetic workspaces (no real data)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

import _workspace  # noqa: F401  (puts src/ on sys.path)
from _workspace import WorkspaceCase
from exocortex_engine import todo, todo_md
from exocortex_engine.gitref import CASConflict, Git

DAY = WorkspaceCase.TODAY
CREF = f"content/{DAY}"
PATH = "inbox/todos.md"


class TodoCase(WorkspaceCase):
    def tcli(self, *args: str, today: str = DAY):
        cmd = [sys.executable, "-m", "exocortex_engine.todo", *args]
        if args[0] != "version":
            cmd += ["--today", today]
        proc = subprocess.run(cmd, cwd=self.ws, env=self.env, capture_output=True, text=True, encoding="utf-8")
        proc.first = proc.stdout.splitlines()[0] if proc.stdout else ""  # type: ignore[attr-defined]
        return proc

    def tjson(self, *args: str, **kw) -> tuple[int, dict]:
        p = self.tcli(*args, "--json", **kw)
        return p.returncode, json.loads(p.stdout)

    def add(self, ticket="alpha-one", *extra: str, today: str = DAY):
        return self.tcli("add", "--ticket", ticket, "--title", f"標題 {ticket}", "--no-push", *extra, today=today)

    def expect_refused(self, p, fragment: str):
        self.assertEqual((p.returncode, p.first), (1, "result: rejected"), p.stdout)
        self.assertIn(fragment, p.stdout)

    def tickets(self, *args: str) -> list[str]:
        code, data = self.tjson("list", *args)
        self.assertEqual(code, 0, data)
        return [t["ticket"] for t in data["todos"]]


class WriteTests(TodoCase):
    def test_first_add_creates_the_skeleton_on_the_content_branch_only(self):
        main_before, status_before = self.rev("main"), self.status()
        p = self.add("alpha-one", "--project", "alpha", "--summary", "一句話")
        self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        text = self.show(CREF, PATH)
        self.assertEqual(text, "# Todos\n\n## Active\n\n- [ ] [needs-triage][project:alpha][ticket:alpha-one]"
                               f"[added:{DAY}] **標題 alpha-one** — 一句話\n\n## Done\n")
        self.assertEqual(self.git("log", "-1", "--format=%s", CREF).strip(), "content: todo add alpha-one")
        self.assertEqual(self.git("rev-parse", f"{CREF}^").strip(), main_before)  # built on main
        self.assertEqual((self.rev("main"), self.status()), (main_before, status_before))
        self.assertFalse((self.ws / PATH).exists())  # the working tree is not touched
        self.assertIsNone(self.show("main", PATH))

    def test_land_content_brings_the_file_to_main(self):
        self.add("alpha-one")
        p = self.run_git(self.ws, "status", "--porcelain")
        self.assertEqual(p.stdout, "")
        land = subprocess.run([sys.executable, "-m", "exocortex_engine.content", "land", "--today", DAY, "--no-push"],
                              cwd=self.ws, env=self.env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(land.returncode, 0, land.stdout)
        self.assertIn("alpha-one", self.show("main", PATH))

    def test_each_write_is_one_commit_and_list_reads_the_ref(self):
        self.add("one")
        self.add("two")
        self.assertEqual(self.tcli("set", "one", "--status", "ready", "--no-push").returncode, 0)
        self.assertEqual(self.tcli("done", "two", "--note", "ok", "--no-push").returncode, 0)
        self.assertEqual(self.git("log", "--format=%s", f"main..{CREF}").splitlines(),
                         ["content: todo done two", "content: todo set one", "content: todo add two",
                          "content: todo add one"])
        code, data = self.tjson("list", "--all")
        self.assertEqual((data["ref"], data["count"], data["warnings"]), (CREF, 2, []))
        self.assertEqual([(t["ticket"], t["state"], t["status"]) for t in data["todos"]],
                         [("one", "open", "ready"), ("two", "done", None)])
        self.assertEqual(self.tickets(), ["one"])
        self.assertEqual(self.tickets("--status", "ready"), ["one"])
        self.assertEqual(self.tickets("--status", "needs-triage"), [])

    def test_replay_and_done_twice_are_noops(self):
        self.add("one")
        before = self.refs()
        p = self.add("one")
        self.assertEqual((p.returncode, p.first), (0, "result: noop"))
        self.tcli("done", "one", "--no-push")
        p = self.tcli("done", "one", "--no-push")
        self.assertEqual((p.returncode, p.first), (0, "result: noop"))
        self.assertEqual(self.git("log", "--format=%s", f"main..{CREF}").splitlines()[0], "content: todo done one")
        self.assertNotEqual(self.refs(), before)

    def test_body_file_is_indented_and_crlf_safe(self):
        body = self.tmpfile("body.txt", "- 現象：a\r\n- 證據：b\r\n")
        self.assertEqual(self.add("one", "--body-file", body).returncode, 0)
        self.assertIn("\n  - 現象：a\n  - 證據：b\n\n## Done", self.show(CREF, PATH))
        self.assertNotIn("\r", self.show(CREF, PATH))

    def test_push_publishes_the_content_branch(self):
        p = self.tcli("add", "--ticket", "one", "--title", "t")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(f"push {CREF}: pushed", p.stdout)
        self.assertEqual(self.rev(CREF), self.rev(CREF, self.remote))

    def test_list_json_says_whether_the_file_exists(self):
        """``exists`` tells "no inbox/todos.md yet" from "a file with nothing open" without
        reading the human message."""
        code, data = self.tjson("list")
        self.assertEqual((code, data["exists"], data["count"], data["ref"]), (0, False, 0, "main"))
        self.add("one")
        self.assertEqual(self.tcli("done", "one", "--note", "ok", "--no-push").returncode, 0)
        code, data = self.tjson("list")
        self.assertEqual((code, data["exists"], data["count"], data["ref"]), (0, True, 0, CREF))

    def test_base_prefers_local_then_tracking_then_main(self):
        self.add("one")
        self.git("push", "-q", "origin", CREF)
        self.git("fetch", "-q", "origin")
        self.git("branch", "-D", CREF)  # only origin/content/<today> is left
        self.assertEqual(self.tjson("list")[1]["ref"], f"origin/{CREF}")
        self.assertEqual(self.add("two").returncode, 0)
        self.assertEqual(self.tickets(), ["one", "two"])  # built on the tracked tip
        self.git("branch", "-D", CREF)
        self.git("update-ref", "-d", f"refs/remotes/origin/{CREF}")
        self.assertEqual(self.tjson("list")[1]["ref"], "main")


class GuardTests(TodoCase):
    def test_validation_failures_write_nothing(self):
        refs = self.refs()
        cases = [
            (("add", "--ticket", "Bad", "--title", "t"), 2),
            (("add", "--ticket", "a", "--title", ""), 2),
            (("add", "--ticket", "a", "--title", "x**y"), 2),
            (("add", "--ticket", "a", "--title", "t", "--status", "bogus"), 2),
            (("add", "--ticket", "a", "--title", "t", "--due", "tomorrow"), 2),
            (("add", "--title", "t"), 2),
            (("add", "--ticket", "a", "--title", "t", "--project", "nope"), 1),
            (("set", "ghost", "--note", "x"), 1),
            (("set", "ghost"), 2),
            (("done", "ghost"), 1),
            (("done", "ghost", "--dropped"), 2),
        ]
        for args, code in cases:
            p = self.tcli(*args, "--no-push")
            self.assertEqual(p.returncode, code, (args, p.stdout))
            self.assertIn(p.first, ("result: rejected", "result: usage-error"))
        self.assertEqual(self.refs(), refs)

    def test_project_must_be_in_the_index_any_status(self):
        self.assertEqual(self.add("one", "--project", "beta").returncode, 0)  # beta is completed, still a row
        self.expect_refused(self.tcli("set", "one", "--project", "gamma", "--no-push"), "gamma")  # only in Planned table
        self.assertEqual(self.tcli("set", "one", "--project", "alpha", "--no-push").returncode, 0)
        self.assertEqual(self.tjson("list")[1]["todos"][0]["project"], "alpha")

    def test_dropped_needs_a_reason_and_ticket_is_unique(self):
        self.add("one")
        p = self.tcli("done", "one", "--dropped", "--no-push")
        self.assertEqual((p.returncode, p.first), (2, "result: usage-error"))
        self.assertEqual(self.tcli("done", "one", "--dropped", "--note", "dup", "--no-push").returncode, 0)
        self.assertEqual(self.tjson("list", "--all")[1]["todos"][0]["state"], "dropped")
        p = self.tcli("add", "--ticket", "one", "--title", "again", "--no-push")  # even though it is in Done
        self.expect_refused(p, "標題 one")
        self.expect_refused(self.tcli("set", "one", "--note", "x", "--no-push"), "in Done")

    def test_unlanded_earlier_day_blocks_writes_but_not_list(self):
        self.add("one", today="2026-03-09")
        before = self.refs()
        self.expect_refused(self.add("two"), "land content 2026-03-09")
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.tcli("list").returncode, 0)
        land = subprocess.run([sys.executable, "-m", "exocortex_engine.content", "land", "2026-03-09", "--no-push",
                               "--today", DAY], cwd=self.ws, env=self.env, capture_output=True, text=True)
        self.assertEqual(land.returncode, 0, land.stdout)
        self.assertEqual(self.add("two").returncode, 0)

    def test_earlier_day_that_never_touched_the_file_does_not_block(self):
        self.git("branch", "content/2026-03-09", "main")
        self.assertEqual(self.add("one").returncode, 0)

    def test_hand_edited_checkout_copy_is_refused_until_committed(self):
        self.write(PATH, "# Todos\n\n## Active\n\n- [ ] [ticket:hand] **by hand**\n\n## Done\n")
        refs = self.refs()
        self.expect_refused(self.add("one"), "/exo:content commit --only inbox/todos.md")
        self.assertEqual(self.refs(), refs)
        content = subprocess.run([sys.executable, "-m", "exocortex_engine.content", "commit", "--no-push",
                                  "--only", PATH, "--today", DAY], cwd=self.ws, env=self.env, capture_output=True, text=True)
        self.assertEqual(content.returncode, 0, content.stdout)
        self.assertEqual(self.add("one").returncode, 0)  # the copy now equals the branch's version
        self.assertEqual(self.tickets(), ["hand", "one"])

    def test_missing_sections_are_refused_and_named(self):
        self.write(PATH, "# Todos\n\n## Active\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "broken todos")
        self.expect_refused(self.add("one"), "## Done")
        self.expect_refused(self.tcli("list"), "## Done")


class CasTests(TodoCase):
    def test_conflict_is_retried_with_the_new_tip_and_exhaustion_is_reported(self):
        self.add("one")
        ctx = todo.Ctx(git=Git(self.ws), main="refs/heads/main", today=DAY, now="09:30", push=False)
        out = todo.Out(command="add")
        real, calls = todo.write_refs, []

        def racing(ctx_, updates, out_=None):
            calls.append(updates[0].old)
            if len(calls) == 1:  # another session moves the branch between our read and our write
                other = self.git("commit-tree", f"{CREF}^{{tree}}", "-p", CREF, "-m", "other").strip()
                self.git("update-ref", f"refs/heads/{CREF}", other)
            return real(ctx_, updates, out_)

        todo.write_refs = racing
        try:
            todo.write_todo(ctx, out, "add", "two", lambda t: todo_md.add(t, ticket="two", title="t", today=DAY))
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0], calls[1])
            self.assertEqual(self.tickets(), ["one", "two"])
            calls.clear()

            def always(ctx_, updates, out_=None):
                calls.append(1)
                raise CASConflict("moved")

            todo.write_refs = always
            with self.assertRaises(CASConflict):
                todo.write_todo(ctx, out, "add", "three", lambda t: todo_md.add(t, ticket="three", title="t", today=DAY))
            self.assertEqual(len(calls), todo.ATTEMPTS)
        finally:
            todo.write_refs = real
        self.assertEqual(self.tickets(), ["one", "two"])


class ListTests(TodoCase):
    def test_json_shape_order_and_warnings(self):
        self.git("checkout", "-q", "-b", "scratch")
        self.write(PATH, "# Todos\n\n## Active\n\n"
                         "- [ ] [ready][project:alpha][ticket:b-two][added:2026-03-01][due:2026-04-01] **B** — sb\n"
                         "  - sub\n"
                         "- [ ] hand written\n\n## Done\n\n### 2026-03-02\n\n"
                         "- [x] [ticket:a-one][added:2026-03-01] **A**\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed todos")
        self.git("branch", "-f", "main", "scratch")
        self.git("checkout", "-q", "main")
        code, data = self.tjson("list", "--all")
        self.assertEqual(code, 0)
        self.assertEqual(list(data), ["result", "command", "ref", "exists", "count", "warnings", "todos", "messages"])
        self.assertEqual((data["ref"], data["count"]), ("main", 3))
        b, hand, a = data["todos"]
        self.assertEqual(b, {"ticket": "b-two", "title": "B", "project": "alpha", "status": "ready", "summary": "sb",
                             "added": "2026-03-01", "due": "2026-04-01", "state": "open",
                             "raw": "- [ ] [ready][project:alpha][ticket:b-two][added:2026-03-01][due:2026-04-01] **B** — sb\n  - sub"})
        self.assertEqual((hand["ticket"], hand["project"], hand["due"]), (None, None, None))
        self.assertEqual((a["status"], a["state"], a["project"]), (None, "done", None))
        self.assertEqual(len(data["warnings"]), 1)
        self.assertIn("line 7", data["warnings"][0])
        self.assertEqual(self.tickets("--project", "alpha", "--all"), ["b-two"])
        human = self.tcli("list")
        self.assertEqual(human.first, "result: ok")
        self.assertIn("b-two [ready] (alpha) B — sb (due 2026-04-01)", human.stdout)

    def test_empty_base_and_version(self):
        code, data = self.tjson("list")
        self.assertEqual((code, data["count"], data["todos"]), (0, 0, []))
        v = self.tcli("version")
        self.assertEqual(v.first.split(": ")[0], "version")


if __name__ == "__main__":
    unittest.main()
