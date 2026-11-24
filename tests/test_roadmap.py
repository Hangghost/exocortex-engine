"""``exocortex_engine.roadmap``: validator, diff and CLI, and the ``/exo:content`` hook
(synthetic data only: every name and word below is made up)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from _workspace import WorkspaceCase

from exocortex_engine import roadmap, yaml_subset
from exocortex_engine.gitref import Git

BASE = """\
# synthetic roadmap for the tests
project: alpha
revised: 2026-03-10
revised_by: >-
  PROJECT.md and the last
  five work logs
since: 2026-03-01          # diff base
now:
  - build-core
  - ship-docs
phases:
  - id: p-one
    title: First phase
    state: now
    done_when: core ships
    gate:
      id: g-one
      condition: tests green
      evaluated: null
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
  - id: build-core
    title: Build the core
    line: impl
    phase: p-one
    status: open
    due: 2026-04-01
  - id: ship-docs
    title: "Docs #1 and more"
    line: impl
    phase: p-one
    status: open
    after: [build-core]
ledger:
  - id: slow-step
    title: one step is slow
    line: impl
    cleared_by: build-core
decisions:
  - id: d-stdlib
    text: stdlib only
signals:
  - id: s-green
    date: 2026-03-05
    text: CI green
    informs: [build-core]
"""

WORDS = ("amber", "basalt", "cobalt", "delta", "ember", "fjord", "garnet", "harbor", "indigo", "jasper",
         "kelp", "lantern", "meadow", "nimbus", "onyx", "prism", "quartz", "rivet", "saffron", "tundra")


def big_roadmap(tasks: int = 190) -> str:
    """A synthetic ROADMAP of the size and shapes of a long-lived real one: header
    comments, folded and multi-line values, two-space inline comments, quoted values
    with '#', block and flow lists, gates, an ``after`` chain, a ledger, decisions with
    ``superseded_by`` and signals."""
    w = lambda i, n=3: " ".join(WORDS[(i * 7 + k * 3) % len(WORDS)] for k in range(n))  # noqa: E731
    phases = [f"ph-{x}" for x in ("seed", "grow", "ripen", "harvest", "rest", "renew")]
    lines = ["code", "decide", "observe", "wait", "outside"]
    out = ["# synthetic roadmap: every word is made up", "# ── header ──", "project: alpha",
           "revised: 2026-03-10", "revised_by: >-", "  a long note on what was read, spread", "  over two lines",
           "since: 2026-01-01          # diff base", "now:  # the one path"]
    out += [f"  - t-{i:03d}" for i in range(tasks - 3, tasks)]
    out.append("phases:")
    for k, p in enumerate(phases):
        out += [f"  - id: {p}", f"    title: {w(k, 4)}", f"    state: {'done' if k < 2 else 'now' if k == 2 else 'later'}",
                "    done_when: >-", f"      {w(k + 1, 6)},", f"      then {w(k + 2, 5)}"]
        if k % 2:
            out += ["    gate:", f"      id: gate-{p}", f"      condition: {w(k, 3)}", "      evaluated: null",
                    f"      note: \"check #{k}\""]
    out.append("lines:")
    for k, ln in enumerate(lines):
        out += [f"  - id: {ln}", f"    title: {w(k + 5, 2)}", f"    clearer: {w(k + 6, 5)}"]
    out.append("tasks:")
    for i in range(tasks):
        if i % 40 == 0:
            out.append(f"  # ── block {i // 40} ──")
        status = "done" if i < tasks - 40 else ("cancelled" if i % 13 == 0 else "open")
        out += [f"  - id: t-{i:03d}", f"    title: {w(i, 4)}",
                f"    line: {lines[i % len(lines)]}", f"    phase: {phases[min(i * len(phases) // tasks, 5)]}",
                f"    status: {status}"]
        if i and i % 3 == 0:
            out.append(f"    after: [t-{i - 1:03d}, t-{i - 3:03d}]" if i % 2 else f"    after:\n      - t-{i - 1:03d}")
        if i % 5 == 0:
            out += ["    why_here: >-", f"      {w(i + 1, 7)}", f"      {w(i + 2, 6)}"]
        if i % 7 == 0:
            out.append(f"    due: 2026-{1 + i % 12:02d}-{1 + i % 28:02d}  # soft")
        if i % 11 == 0:
            out.append(f"    done_note: '{w(i, 3)} #{i}'")
    out.append("ledger:")
    for k in range(32):
        out += [f"  - id: led-{k:02d}", f"    title: {w(k + 3, 5)}", f"    line: {lines[k % 5]}",
                "    why: >-", f"      {w(k, 6)}"]
        if k % 2:
            out.append(f"    cleared_by: t-{k * 3:03d}")
    out.append("decisions:")
    for k in range(32):
        out += [f"  - id: dec-{k:02d}", f"    text: {w(k + 9, 8)}"]
        if k % 4 == 0:
            out += [f"    superseded_by: t-{k + 10:03d}", f"    superseded_note: {w(k, 3)}"]
    out.append("signals:")
    for k in range(34):
        out += [f"  - id: sig-{k:02d}", f"    date: 2026-02-{1 + k % 28:02d}", "    text: >-",
                f"      {w(k + 4, 9)}", f"    informs: [led-{k % 32:02d}, t-{k:03d}]"]
    return "\n".join(out) + "\n"


def mutate(old: str, new: str, text: str = BASE) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


class ValidatorTests(unittest.TestCase):
    def rules(self, text: str) -> list[roadmap.Finding]:
        return roadmap.validate_text(text, "alpha")

    def test_base_is_valid(self):
        loaded = roadmap.load_text(BASE, "alpha")
        self.assertEqual((loaded.status, loaded.findings), ("ok", []))
        self.assertEqual(loaded.data["tasks"][1]["title"], "Docs #1 and more")

    def test_big_synthetic_roadmap(self):
        text = big_roadmap()
        self.assertGreaterEqual(text.count("\n"), 1500)
        self.assertIsInstance(yaml_subset.load(text), dict)
        loaded = roadmap.load_text(text, "alpha")
        self.assertEqual(loaded.findings, [])
        self.assertTrue(roadmap.diff(loaded.data, loaded.data)["empty"])
        s = roadmap.summary(loaded.data)
        self.assertEqual((s["tasks"], s["ledger"], s["signals"]), (190, 32, 34))

    def assert_rule(self, text: str, rule: str, id_: str | None, marker: str) -> None:
        found = [f for f in self.rules(text) if f.rule == rule]
        self.assertTrue(found, f"{rule} not reported: {self.rules(text)}")
        f = found[0]
        self.assertEqual(f.id, id_)
        self.assertIsNotNone(f.line)
        self.assertIn(marker, text.splitlines()[f.line - 1], f.describe())
        self.assertIn(rule, f.describe())

    def test_each_rule(self):
        cases = [
            ("missing-title", "    title: Later phase\n", "", "p-two", "- id: p-two"),
            ("missing-clearer", "    clearer: me, by deciding\n", "", "ask", "- id: ask"),
            ("duplicate-id", "  - id: d-stdlib", "  - id: impl", "impl", "- id: impl"),
            ("bad-id", "  - id: d-stdlib", "  - id: D_Stdlib", "D_Stdlib", "D_Stdlib"),
            ("bad-phase-state", "state: later", "state: soon", "p-two", "state: soon"),
            ("bad-task-status", "    status: open\n    due", "    status: wip\n    due", "build-core", "status: wip"),
            ("dangling-line", "    line: impl\n    phase: p-one\n    status: open\n    due",
             "    line: nope\n    phase: p-one\n    status: open\n    due", "build-core", "line: nope"),
            ("dangling-phase", "    phase: p-one\n    status: open\n    due",
             "    phase: p-nine\n    status: open\n    due", "build-core", "phase: p-nine"),
            ("now-dangling", "  - ship-docs\nphases", "  - ghost\nphases", "ghost", "- ghost"),
            ("now-duplicate", "  - ship-docs\nphases", "  - build-core\nphases", "build-core", "- build-core"),
            ("dangling-after", "after: [build-core]", "after: [ghost]", "ship-docs", "after: [ghost]"),
            ("after-cycle", "    due: 2026-04-01\n", "    due: 2026-04-01\n    after: [ship-docs]\n", "build-core",
             "after: [ship-docs]"),
            ("dangling-clears", "    after: [build-core]\n", "    after: [build-core]\n    clears: [ghost]\n",
             "ship-docs", "clears: [ghost]"),
            ("dangling-cleared-by", "cleared_by: build-core", "cleared_by: ghost", "slow-step", "cleared_by: ghost"),
            ("dangling-superseded-by", "    text: stdlib only\n", "    text: stdlib only\n    superseded_by: ghost\n",
             "d-stdlib", "superseded_by"),
            ("decision-has-status", "    text: stdlib only\n", "    text: stdlib only\n    status: done\n", "d-stdlib",
             "status: done"),
            ("signal-has-status", "    text: CI green\n", "    text: CI green\n    status: open\n", "s-green",
             "status: open"),
            ("project-mismatch", "project: alpha", "project: other", None, "project: other"),
            ("bad-date", "due: 2026-04-01", "due: soon", "build-core", "due: soon"),
            ("missing-gate-condition", "      condition: tests green\n", "", "p-one", "gate:"),
            ("yaml-hash-truncation", "title: Build the core", "title: Build the core #2", None, "core #2"),
        ]
        for rule, old, new, id_, marker in cases:
            with self.subTest(rule=rule):
                self.assert_rule(mutate(old, new), rule, id_, marker)

    def test_odd_shapes_are_findings(self):
        text = mutate("  - ship-docs\nphases", "  - ship: docs\nphases")
        text = mutate("after: [build-core]", "after:\n      - a: b", text)
        self.assertEqual(sorted({f.rule for f in self.rules(text)}), ["bad-now", "dangling-after"])
        self.assertEqual([f.rule for f in self.rules("- a\n- b\n")], ["parse-failed"])

    def test_missing_top_level(self):
        f = self.rules(mutate("since: 2026-03-01          # diff base\n", ""))
        self.assertEqual([x.rule for x in f], ["missing-since"])

    def test_parse_failure_is_refused_with_line(self):
        # A more-indented line inside a folded scalar: yaml_subset does not read it.
        text = mutate("  five work logs\n", "  five work logs\n    indented further\n")
        f = self.rules(text)
        self.assertEqual([x.rule for x in f], ["parse-failed"])
        self.assertEqual(text.splitlines()[f[0].line - 1], "    indented further")
        self.assertEqual(f[0].describe(), "[parse-failed] line 7: the YAML reader refused this text: "
                                          "more-indented line in a folded scalar")
        self.assertEqual(roadmap.load_text(text).status, "failed")
        big = big_roadmap().replace("      then ", "        then ", 1)
        f = self.rules(big)
        self.assertEqual(f[0].rule, "parse-failed")
        self.assertIn("then", big.splitlines()[f[0].line - 1] if f[0].line else "", f[0].describe())

    def test_hash_rule_reads_the_text(self):
        ok = {
            "quoted": 'title: "Build #2"',
            "single-quoted": "title: 'Build #2'",
            "two-space comment": "title: Build the core  # a comment",
            "flow list comment": "after: [a, b] # c",
            "' #' inside a comment": "status: done   # note #2 here",
        }
        for label, line in ok.items():
            with self.subTest(label):
                self.assertEqual(roadmap.hash_truncations(f"tasks:\n  - id: x\n    {line}\n"), [])
        block = "why: >-\n  text #not a comment\n  more\nnext: v #cut\n"
        self.assertEqual([f.line for f in roadmap.hash_truncations(block)], [4])
        multi = 'title: "a\n  b #in quotes"\nnote: plain #cut\n'
        self.assertEqual([f.line for f in roadmap.hash_truncations(multi)], [3])
        cont = "title: first line\n  second #cut\n"
        self.assertEqual([f.line for f in roadmap.hash_truncations(cont)], [2])

    def test_bom_and_crlf(self):
        self.assertEqual(self.rules("﻿" + BASE.replace("\n", "\r\n")), [])

    def test_item_raw(self):
        raw = roadmap.item_raw(BASE)
        self.assertTrue(raw["ship-docs"].startswith("  - id: ship-docs"))
        self.assertTrue(raw["ship-docs"].endswith("after: [build-core]"))
        self.assertIn("gate:", raw["p-one"])
        self.assertEqual(len(roadmap.item_raw(big_roadmap())), 6 + 5 + 190 + 32 + 32 + 34)


class DiffTests(unittest.TestCase):
    def d(self, new: str, old: str = BASE) -> dict:
        return roadmap.diff(roadmap.load_text(old).data, roadmap.load_text(new).data)

    def test_self_is_empty(self):
        self.assertTrue(self.d(BASE)["empty"])
        # Quoting a value differently is not a change.
        self.assertTrue(self.d(mutate("title: Build the core", 'title: "Build the core"'))["empty"])

    def test_kinds(self):
        added = mutate("decisions:\n", "  - id: new-step\n    title: new\n    line: ask\n    cleared_by: ship-docs\n"
                                       "decisions:\n")
        d = self.d(added)
        self.assertEqual([(x["id"], x["kind"]) for x in d["added"]], [("new-step", "ledger")])
        d = self.d(mutate("  - id: s-green\n    date: 2026-03-05\n    text: CI green\n    informs: [build-core]\n", ""))
        self.assertEqual([x["id"] for x in d["removed"]], ["s-green"])
        d = self.d(mutate("    line: impl\n    phase: p-one\n    status: open\n    due",
                          "    line: ask\n    phase: p-one\n    status: open\n    due"))
        self.assertEqual(d["moved"], [{"id": "build-core", "kind": "task", "title": "Build the core",
                                       "from": {"line": "impl", "phase": "p-one"},
                                       "to": {"line": "ask", "phase": "p-one"}}])
        d = self.d(mutate("condition: tests green", "condition: tests and lint green"))
        self.assertEqual((d["changed"], d["moved"]), ([{"id": "g-one", "kind": "gate"}], []))
        d = self.d(mutate("  - build-core\n  - ship-docs\n", "  - ship-docs\n"))
        self.assertEqual(d["now"], {"before": ["build-core", "ship-docs"], "after": ["ship-docs"], "changed": True})
        self.assertFalse(d["empty"])

    def test_renamed_id_is_removed_and_added(self):
        d = self.d(mutate("  - id: build-core\n", "  - id: build-the-core\n").replace(
            "  - build-core\n", "  - build-the-core\n").replace("[build-core]", "[build-the-core]").replace(
            "cleared_by: build-core", "cleared_by: build-the-core"))
        self.assertEqual([x["id"] for x in d["removed"]], ["build-core"])
        self.assertEqual([(x["id"], x["title"]) for x in d["added"]], [("build-the-core", "Build the core")])


class RoadmapCliCase(WorkspaceCase):
    PATH = "projects/alpha/ROADMAP.yaml"

    def rcli(self, *args: str, module: str = "roadmap"):
        cmd = [sys.executable, "-m", f"exocortex_engine.{module}", *args, "--today", self.TODAY]
        proc = subprocess.run(cmd, cwd=self.ws, env=self.env, capture_output=True, text=True, encoding="utf-8")
        proc.first = proc.stdout.splitlines()[0] if proc.stdout else ""  # type: ignore[attr-defined]
        return proc

    def rjson(self, *args: str, module: str = "roadmap") -> tuple[int, dict]:
        p = self.rcli(*args, "--json", module=module)
        return p.returncode, json.loads(p.stdout)

    def land_on_branch(self, text: str) -> None:
        """Put a ROADMAP on project/alpha with plain git (test set-up only)."""
        self.git("switch", "-q", "project/alpha")
        self.write(self.PATH, text)
        self.git("add", self.PATH)
        self.git("commit", "-q", "-m", "seed roadmap")
        self.git("switch", "-q", "main")  # main has no ROADMAP: the file leaves the working tree


class CliTests(RoadmapCliCase):
    def test_validate(self):
        good = self.tmpfile("good.yaml", BASE)
        before, st = self.refs(), self.status()
        code, out = self.rjson("validate", "alpha", "--file", good, "--new")
        self.assertEqual((code, out["result"], out["ok"], out["findings"]), (0, "ok", True, []))
        self.assertEqual(out["summary"]["now"][0], {"id": "build-core", "title": "Build the core", "status": "open"})
        self.assertEqual(sorted(out["summary"]), ["decisions", "ledger", "lines", "now", "open_tasks", "phases",
                                                  "signals", "tasks"])
        bad = self.tmpfile("bad.yaml", mutate("after: [build-core]", "after: [ghost]"))
        p = self.rcli("validate", "alpha", "--file", bad)
        self.assertEqual((p.returncode, p.first), (1, "result: rejected"))
        self.assertIn("[dangling-after] line", p.stdout)
        code, out = self.rjson("validate", "beta", "--file", good)
        self.assertEqual([f["rule"] for f in out["findings"]], ["project-mismatch"])
        self.assertEqual(sorted(out["findings"][0]), ["id", "line", "message", "rule"])
        self.assertEqual(self.rcli("validate", "Alpha", "--file", good).returncode, 2)
        self.assertEqual((self.refs(), self.status()), (before, st))

    def test_validate_new_refuses_existing(self):
        self.land_on_branch(BASE)
        code, out = self.rjson("validate", "alpha", "--file", self.tmpfile("r.yaml", BASE), "--new")
        self.assertEqual((code, [f["rule"] for f in out["findings"]]), (1, ["already-exists"]))
        code, out = self.rjson("validate", "gamma", "--file", self.tmpfile("g.yaml", BASE.replace("alpha", "gamma")),
                               "--new")
        self.assertEqual([f["rule"] for f in out["findings"]], ["no-project-branch"])

    def test_diff(self):
        self.land_on_branch(BASE)
        self.write(self.PATH, BASE)
        before, st = self.refs(), self.status()
        code, out = self.rjson("diff", "alpha")
        self.assertEqual((code, out["result"], out["empty"]), (0, "noop", True))
        self.assertEqual(sorted(k for k in out if k not in ("messages", "command")),
                         ["added", "changed", "empty", "file", "moved", "name", "new_status", "now", "old_source",
                          "old_status", "removed", "result"])
        self.write(self.PATH, mutate("  - id: s-green\n    date: 2026-03-05\n    text: CI green\n"
                                     "    informs: [build-core]\n", ""))
        p = self.rcli("diff", "alpha")
        self.assertEqual(p.first, "result: ok")
        self.assertIn("## removed (1)", p.stdout)
        self.assertIn("- s-green [signal] CI green", p.stdout)
        self.assertEqual((self.refs(), self.status()), (before, st))

    def test_diff_old_absent_and_broken(self):
        self.write(self.PATH, BASE)
        code, out = self.rjson("diff", "alpha")
        self.assertEqual((code, out["old_status"], len(out["added"])), (0, "absent", 10))
        broken = mutate("  five work logs\n", "  five work logs\n    indented further\n")
        self.land_on_branch(broken)
        self.write(self.PATH, BASE)
        code, out = self.rjson("diff", "alpha")
        self.assertEqual((code, out["result"], out["old_status"]), (1, "rejected", "failed"))
        self.assertEqual(out["old_findings"][0]["rule"], "parse-failed")
        loaded = roadmap.read_roadmap(Git(self.ws), "alpha")
        self.assertEqual((loaded.status, loaded.data), ("failed", None))


class ContentHookTests(RoadmapCliCase):
    def test_bad_roadmap_does_not_land(self):
        self.write(self.PATH, mutate("  - ship-docs\nphases", "  - ghost\nphases"))
        before, remote = self.refs(), self.refs(self.remote)
        p = self.rcli("status", module="content")
        self.assertEqual((p.returncode, p.first), (1, "result: rejected"))
        self.assertIn("[now-dangling]", p.stdout)
        p = self.rcli("commit", "--only", self.PATH, module="content")
        self.assertEqual((p.returncode, p.first), (1, "result: rejected"), p.stdout)
        self.assertEqual((self.refs(), self.refs(self.remote)), (before, remote))

    def test_good_roadmap_lands_on_project_branch(self):
        self.write(self.PATH, BASE)
        st = self.status()
        p = self.rcli("commit", "--only", self.PATH, module="content")
        self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        self.assertEqual(self.show("project/alpha", self.PATH), BASE)
        self.assertEqual(self.status(), st)
        self.assertEqual(roadmap.read_roadmap(Git(self.ws), "alpha").status, "ok")


if __name__ == "__main__":
    unittest.main()
