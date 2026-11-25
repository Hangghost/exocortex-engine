"""``exocortex_engine.next_items``: the ``/exo:next`` inventory (synthetic data only: every
name and word below is made up)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from _workspace import WorkspaceCase

from exocortex_engine.next_items import SYNTHESIZE_AT, next_steps

ROADMAP = """\
project: alpha
revised: 2026-03-01
revised_by: synthetic
since: 2026-03-01
now:
  - build-core
  - old-step
phases:
  - id: p-zero
    title: Zero phase
    state: done
  - id: p-one
    title: First phase
    state: now
  - id: p-two
    title: Later phase
    state: later
lines:
  - id: impl
    title: Implementation
    clearer: me, by writing code
  - id: ask
    title: Decisions
    clearer: me, by deciding
tasks:
  - id: old-step
    title: Old step
    line: impl
    phase: p-zero
    status: done
  - id: build-core
    title: Build the core
    line: impl
    phase: p-one
    status: open
    due: 2026-03-05
  - id: pick-format
    title: Pick the format
    line: ask
    phase: p-one
    status: open
    after: [build-core, old-step]
  - id: ship-docs
    title: Ship the docs
    line: impl
    phase: p-two
    status: open
"""

PROJECT = (
    "---\nlast_updated: 2026-03-01\nstatus: active\nl0: alpha summary\n---\n\n# Alpha\n\n"
    "## 目標 / 動機\n\ngoal\n\n## 現況\n\nnow\n\n## 下一步\n\n{body}\n## 環境\n\nnone\n"
)

TODOS = (
    "# Todos\n\n## Active\n\n"
    "- [ ] [ready][project:alpha][ticket:build-core][added:2026-03-01] **Core from the inbox** — one line\n"
    "  - detail\n"
    "- [ ] [discuss-first][project:alpha][ticket:loose-end][due:2026-03-11] **A loose end**\n"
    "- [ ] [ready][project:other][ticket:not-ours] **Somebody else's**\n"
    "- [ ] [project:alpha] **No ticket here**\n"
    "\n## Done\n\n### 2026-03-02\n\n- [x] [project:alpha][ticket:gone] **Finished**\n"
)


class NextStepsParsing(unittest.TestCase):
    def test_what_counts(self):
        body = (
            "intro text\n> quote\n- [ ] open one\n  sub line\n- [x] checked\n- ~~struck~~ (done)\n"
            "- <!-- 待填入 -->\n<!--\n- in a comment\n-->\n### Later\n\n1. numbered build-core\n"
            "<details>\n<summary>old</summary>\n\n- hidden\n</details>\n```\n- in code\n```\n"
            "* star item\n  - nested is body\n"
        )
        got = next_steps(body)
        self.assertEqual([s["title"] for s in got], ["open one", "numbered build-core", "star item"])
        self.assertEqual(got[0]["raw"], "- [ ] open one\n  sub line")
        self.assertEqual([s["heading"] for s in got], [None, "Later", "Later"])
        self.assertEqual(got[2]["raw"], "* star item\n  - nested is body")
        self.assertEqual(next_steps(None), [])
        self.assertEqual(next_steps("<!-- 待填入 -->\n"), [])


class NextCli(WorkspaceCase):
    def nx(self, *args: str, today: str = "2026-03-10"):
        cmd = [sys.executable, "-m", "exocortex_engine.next_items", *args, "--today", today]
        proc = subprocess.run(cmd, cwd=self.ws, env=self.env, capture_output=True, text=True, encoding="utf-8")
        return proc

    def nx_json(self, *args: str, **kw) -> tuple[int, dict]:
        proc = self.nx(*args, "--json", **kw)
        return proc.returncode, json.loads(proc.stdout)

    def on_main(self, files: dict) -> None:
        for path, text in files.items():
            self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")

    def on_project(self, files: dict) -> None:
        self.git("switch", "-q", "project/alpha")
        self.on_main(files)
        self.git("switch", "-q", "main")

    def body(self, *items: str) -> str:
        return "".join(f"- {i}\n" for i in items)

    def check_counts(self, data: dict) -> None:
        total = sum(r["count"] or 0 for r in data["source_summary"]) - data["merged"]
        self.assertEqual(total, data["count"])
        self.assertEqual(data["count"], len(data["items"]))
        self.assertEqual(sum(g["count"] for g in data["groups"]), data["count"])
        self.assertEqual(len({i["key"] for i in data["items"]}), data["count"])

    # ---- show ----

    def test_with_roadmap(self):
        self.on_project({
            "projects/alpha/ROADMAP.yaml": ROADMAP,
            "projects/alpha/PROJECT.md": PROJECT.format(body=self.body("Wire build-core into CI", "Write a blog post")),
        })
        self.on_main({"inbox/todos.md": TODOS})
        before, status = self.refs(), self.status()
        code, d = self.nx_json("show", "alpha")
        self.assertEqual((code, d["result"]), (0, "ok"), d)
        self.assertEqual((self.refs(), self.status()), (before, status))
        self.assertTrue(d["has_roadmap"])
        self.assertFalse(d["any_failed"])
        self.assertEqual({s["source"]: s["state"] for s in d["sources"]},
                         {"roadmap": "ok", "next_step": "ok", "todo": "ok"})
        self.assertEqual([g["title"] for g in d["groups"]],
                         ["First phase", "Later phase", "待辦（未排入規劃）", "下一步（未排入 ROADMAP）"])
        items = {i["key"]: i for i in d["items"]}
        self.assertEqual(list(items), ["build-core", "pick-format", "ship-docs", "loose-end", "todo-3", "next-2"])
        core = items["build-core"]
        self.assertEqual(core["origin"], "roadmap+next_step+todo")
        self.assertEqual([a["source"] for a in core["also"]], ["next_step", "todo"])
        self.assertEqual((core["now_rank"], core["tag"], core["overdue"]), (1, "📅", True))
        self.assertEqual(core["clearer"], "me, by writing code")
        self.assertIn("due: 2026-03-05", core["raw"])
        self.assertEqual(items["pick-format"]["after_open"], ["build-core"])
        self.assertEqual(items["loose-end"]["tag"], "📅")
        self.assertFalse(items["loose-end"]["overdue"])
        self.assertEqual(d["merged"], 2)
        self.assertEqual(d["count"], 6)
        self.check_counts(d)
        self.assertEqual({h["rule"]: h["objects"] for h in d["hygiene"]},
                         {"now-not-open": ["old-step"], "item-overdue": ["build-core"]})
        summary = {r["source"]: r for r in d["source_summary"]}
        self.assertEqual(summary["roadmap"]["breakdown"], [{"label": "impl", "count": 2}, {"label": "ask", "count": 1}])
        self.assertEqual(summary["todo"]["breakdown"],
                         [{"label": "ready", "count": 1}, {"label": "discuss-first", "count": 1}, {"label": "未標", "count": 1}])
        self.assertFalse(d["suggest_synthesize"])
        self.assertEqual(d["now"], ["build-core", "old-step"])
        self.assertEqual([ln["id"] for ln in d["lines"]], ["impl", "ask"])
        # the loose-end todo is overdue a day later, and the human output says so
        _, later = self.nx_json("show", "alpha", today="2026-03-12")
        self.assertEqual({h["rule"]: h["objects"] for h in later["hygiene"]}["item-overdue"], ["build-core", "loose-end"])
        proc = self.nx("show", "alpha")
        self.assertEqual(proc.stdout.splitlines()[0], "result: ok")
        self.assertIn("- build-core [roadmap+next_step+todo] Build the core due 2026-03-05 (overdue)", proc.stdout)

    def test_without_roadmap(self):
        self.on_project({"projects/alpha/PROJECT.md": PROJECT.format(body=self.body("one", "two"))})
        self.on_main({"inbox/todos.md": TODOS})
        code, d = self.nx_json("show", "alpha")
        self.assertEqual(code, 0)
        self.assertFalse(d["has_roadmap"])
        self.assertEqual(d["roadmap_state"], "absent")
        self.assertEqual([g["title"] for g in d["groups"]], ["下一步", "待辦"])
        self.assertEqual([i["key"] for i in d["items"]], ["next-1", "next-2", "build-core", "loose-end", "todo-3"])
        self.assertEqual(d["merged"], 0)
        self.check_counts(d)
        self.assertFalse(d["suggest_synthesize"])
        many = self.body(*[f"step {n}" for n in range(SYNTHESIZE_AT - 3)])
        self.on_project({"projects/alpha/PROJECT.md": PROJECT.format(body=many)})
        _, d = self.nx_json("show", "alpha")
        self.assertEqual(d["count"], SYNTHESIZE_AT)
        self.assertTrue(d["suggest_synthesize"])
        self.assertIn("consider /exo:roadmap alpha synthesize", self.nx("show", "alpha").stdout)

    def test_broken_roadmap(self):
        many = self.body(*[f"step {n}" for n in range(SYNTHESIZE_AT + 2)])
        self.on_project({
            "projects/alpha/ROADMAP.yaml": ROADMAP.replace("title: Ship the docs", "title: Ship #2 docs"),
            "projects/alpha/PROJECT.md": PROJECT.format(body=many),
        })
        code, d = self.nx_json("show", "alpha")
        self.assertEqual(code, 0)
        src = {s["source"]: s for s in d["sources"]}
        self.assertEqual(src["roadmap"]["state"], "failed")
        self.assertIn("yaml-hash-truncation", src["roadmap"]["note"])
        self.assertTrue(d["any_failed"])
        self.assertFalse(d["has_roadmap"])
        self.assertFalse(d["suggest_synthesize"])
        self.assertEqual([g["title"] for g in d["groups"]], ["下一步"])
        self.assertIsNone({r["source"]: r for r in d["source_summary"]}["roadmap"]["count"])
        self.check_counts(d)

    def test_todo_absent_and_failed(self):
        code, d = self.nx_json("show", "alpha")
        self.assertEqual(code, 0)
        src = {s["source"]: s for s in d["sources"]}
        self.assertEqual((src["todo"]["state"], src["roadmap"]["state"]), ("absent", "absent"))
        self.assertFalse(d["any_failed"])
        self.assertEqual([i["title"] for i in d["items"]], ["do the alpha thing"])
        self.check_counts(d)
        self.on_main({"inbox/todos.md": "# Todos\n\n- [ ] [project:alpha] **no headings**\n"})
        _, d = self.nx_json("show", "alpha")
        self.assertEqual({s["source"]: s["state"] for s in d["sources"]}["todo"], "failed")
        self.assertTrue(d["any_failed"])
        self.check_counts(d)

    def test_todo_reads_todays_content_branch(self):
        self.on_main({"inbox/todos.md": TODOS})
        self.git("switch", "-q", "-c", "content/2026-03-10")
        self.on_main({"inbox/todos.md": TODOS.replace("**A loose end**", "**A loose end, renamed**")})
        self.git("switch", "-q", "main")
        _, d = self.nx_json("show", "alpha")
        self.assertEqual({s["source"]: s.get("ref") for s in d["sources"]}["todo"], "content/2026-03-10")
        self.assertIn("A loose end, renamed", [i["title"] for i in d["items"]])

    def test_unknown_project(self):
        for cmd in ("show", "target"):
            code, d = self.nx_json(cmd, "alpah")
            self.assertEqual((code, d["result"], d["name"]), (1, "rejected", None), d)
            self.assertEqual(d["suggestions"], ["alpha"])
        self.assertIn("did you mean: alpha?", self.nx("show", "alpah").stdout)
        self.assertEqual(self.nx("show", "Not_Kebab").returncode, 2)
        # /exo:onboard tells a refusal from a missing module by a JSON stdout with `result`
        code, d = self.nx_json("show", "Not_Kebab")
        self.assertEqual((code, d["result"]), (2, "usage-error"), d)

    # ---- target ----

    def test_target(self):
        code, d = self.nx_json("target")
        self.assertEqual((code, d["name"], d["defaulted"]), (0, "alpha", True))
        code, d = self.nx_json("target", "beta")
        self.assertEqual((code, d["name"], d["defaulted"], d["status"]), (0, "beta", False, "completed"))
        index = self.show("main", "projects/INDEX.md").replace(
            "| beta | completed |", "| beta | active |")
        self.on_main({"projects/INDEX.md": index})
        before = self.refs()
        code, d = self.nx_json("target")
        self.assertEqual((code, d["name"], d["active"]), (1, None, ["alpha", "beta"]))
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.nx("version").stdout.splitlines()[0][:9], "version: ")


if __name__ == "__main__":
    unittest.main()
