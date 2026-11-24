"""``python -m exocortex_engine.day`` against synthetic workspaces with a bare remote (no real data)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from _workspace import WorkspaceCase

from exocortex_engine import audit, day
from exocortex_engine.audit import Finding, Subject
from exocortex_engine.gitref import Git
from exocortex_engine.project import Ctx

DAY = WorkspaceCase.TODAY  # 2026-03-10
YESTERDAY = "2026-03-09"
SECTION = "### 進展\n\n- did a thing\n\n### 經驗與觀察\n\n- learned\n\n### 下一步\n\n- next thing\n"


class DayCase(WorkspaceCase):
    def mod(self, module: str, *args: str, today: str = DAY, env: dict | None = None):
        cmd = [sys.executable, "-m", f"exocortex_engine.{module}", *args]
        if args and args[0] != "version":
            cmd += ["--today", today, "--now", "18:00"]
        proc = subprocess.run(cmd, cwd=self.ws, env={**self.env, **(env or {})}, capture_output=True,
                              text=True, encoding="utf-8")
        proc.first = proc.stdout.splitlines()[0] if proc.stdout else ""  # type: ignore[attr-defined]
        return proc

    def day(self, *args: str, **kw):
        return self.mod("day", *args, **kw)

    def survey(self, *args: str, **kw) -> dict:
        p = self.day("survey", *args, "--json", **kw)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def commit_content(self, *paths: str, today: str = DAY, push: bool = True) -> None:
        for path in paths:
            self.write(path, f"---\nl0: {path}\n---\n\nsynthetic\n")
        p = self.mod("content", "commit", "--only", *paths, *([] if push else ["--no-push"]), today=today)
        self.assertEqual(p.returncode, 0, p.stdout)

    def other_clone(self) -> Path:
        other = self.tmp / "other"
        if not other.exists():
            self.run_git(self.tmp, "clone", "-q", str(self.remote), str(other))
        return other

    def push_from_other(self, rel: str = "OTHER.md", branch: str = "main") -> str:
        other = self.other_clone()
        self.run_git(other, "fetch", "-q", "origin")
        self.run_git(other, "switch", "-q", "-C", branch, f"origin/{branch}")
        (other / rel).write_text("from elsewhere\n", encoding="utf-8")
        self.run_git(other, "add", "-A")
        self.run_git(other, "commit", "-q", "-m", f"elsewhere {rel}")
        self.run_git(other, "push", "-q", "origin", branch)
        return self.rev(branch, self.remote) or ""

    def local_main_commit(self, rel: str = "LOCAL.md") -> None:
        self.write(rel, "local\n")
        self.git("add", rel)
        self.git("commit", "-q", "-m", f"local {rel}")

    def frozen(self):
        return self.refs(), self.refs(self.remote), self.status()


class SurveyTests(DayCase):
    def test_clean_baseline(self):
        s = self.survey()
        self.assertEqual(s["result"], "ok")
        self.assertEqual(s["fetch"], {"requested": False, "ok": None, "error": None})
        self.assertEqual({k: s["main"][k] for k in ("branch", "state", "ahead", "behind", "diverged")},
                         {"branch": "main", "state": "in-sync", "ahead": 0, "behind": 0, "diverged": False})
        self.assertTrue(s["worktree"]["clean"])
        self.assertEqual(s["worktree"]["branch"], "main")
        self.assertEqual(s["content_branches"], [])
        self.assertEqual([p["name"] for p in s["projects"]], ["alpha"])  # beta is completed
        alpha = s["projects"][0]
        self.assertEqual(alpha["progress"]["commits_since_project_md"], 0)
        self.assertFalse(alpha["progress"]["has_progress"])
        self.assertEqual(alpha["progress"]["today_worklog_on"], [])
        self.assertEqual((alpha["milestone"]["ahead"], alpha["milestone"]["recommend"]), (0, False))
        self.assertEqual((s["findings"], s["audit"]), ([], {"rules": 0}))
        text = self.day("survey")
        self.assertEqual(text.first, "result: ok")
        self.assertIn("audit: no rules registered", text.stdout)

    def test_survey_writes_nothing(self):
        self.write("contexts/notes/a.md", "---\nl0: a\n---\n")
        before, objects = self.frozen(), self.git("count-objects")
        self.survey("--fetch")
        self.assertEqual(self.frozen(), before)
        self.assertEqual(self.git("count-objects"), objects)

    def test_content_branches_past_today_remote_only_and_landed(self):
        self.commit_content("contexts/notes/y.md", today=YESTERDAY)
        self.commit_content("contexts/notes/t.md")
        self.git("branch", "content/2026-03-08", "main")  # nothing main lacks
        self.git("branch", "content/not-a-date", "main")  # ignored
        s = self.survey()
        by = {b["date"]: b for b in s["content_branches"]}
        self.assertEqual([b["date"] for b in s["content_branches"]], ["2026-03-08", YESTERDAY, DAY])
        self.assertEqual({k: by[YESTERDAY][k] for k in ("location", "tips_match", "commits", "landed", "past")},
                         {"location": "both", "tips_match": True, "commits": 1, "landed": False, "past": True})
        self.assertEqual((by[DAY]["past"], by[DAY]["landed"]), (False, False))
        self.assertEqual((by["2026-03-08"]["location"], by["2026-03-08"]["landed"]), ("local", True))
        self.assertEqual(s["worktree"]["waiting"], 2)  # committed files stay in the working tree
        self.assertEqual(s["findings"], [])
        # the local branch goes away, the remote one stays
        self.git("branch", "-D", f"content/{YESTERDAY}")
        s = self.survey()
        by = {b["date"]: b for b in s["content_branches"]}
        self.assertEqual((by[YESTERDAY]["location"], by[YESTERDAY]["tips_match"]), ("remote", None))
        self.assertEqual(s["findings"], [{
            "rule": "content-branch-remote-only", "severity": "warn",
            "subject": {"kind": "branch", "id": f"content/{YESTERDAY}"},
            "message": f"content/{YESTERDAY} exists on origin only; land content needs the local branch",
            "suggest": {"command": f"git branch content/{YESTERDAY} origin/content/{YESTERDAY}", "mutates": True},
        }])
        self.assertEqual(s["audit"], {"rules": 0})  # built-in finding, not a registered rule

    def test_main_relation_and_fetch(self):
        self.push_from_other()
        self.assertEqual(self.survey()["main"]["state"], "in-sync")  # last snapshot
        s = self.survey("--fetch")
        self.assertEqual(s["fetch"], {"requested": True, "ok": True, "error": None})
        self.assertEqual((s["main"]["state"], s["main"]["behind"]), ("behind", 1))
        self.local_main_commit()
        m = self.survey()["main"]
        self.assertEqual((m["state"], m["ahead"], m["behind"], m["diverged"]), ("diverged", 1, 1, True))
        self.git("remote", "set-url", "origin", str(self.tmp / "missing.git"))
        p = self.day("survey", "--fetch")
        self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        self.assertIn("warning: fetch failed, showing the last snapshot", p.stdout)
        s = self.survey("--fetch")
        self.assertFalse(s["fetch"]["ok"])
        self.assertTrue(s["fetch"]["error"])
        self.assertEqual(s["main"]["state"], "diverged")

    def test_dirty_worktree_classified(self):
        self.write("contexts/notes/old.md", "---\nl0: old\n---\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")
        self.git("push", "-q", "origin", "main")
        (self.ws / "contexts/notes/old.md").unlink()
        self.write("inbox/n.md", "n\n")
        self.write("rules/r.md", "r\n")
        self.write("projects/alpha/context/n.md", "n\n")
        w = self.survey()
        tree, alpha = w["worktree"], w["projects"][0]["progress"]
        self.assertFalse(tree["clean"])
        self.assertEqual((tree["to_commit"], tree["deletes"], tree["not_handled"], tree["refused"]),
                         (3, ["contexts/notes/old.md"], ["rules/r.md"], []))
        self.assertEqual({t["branch"] for t in tree["plan"]["targets"]}, {f"content/{DAY}", "project/alpha"})
        self.assertEqual((alpha["uncommitted"], alpha["has_progress"]), (1, True))

    def test_progress_and_milestone(self):
        self.commit_content("projects/alpha/context/n1.md")
        a = self.survey()["projects"][0]
        self.assertEqual((a["progress"]["commits_since_project_md"], a["progress"]["has_progress"]), (1, True))
        self.assertEqual((a["milestone"]["ahead"], a["milestone"]["substantive"]), (1, False))
        self.assertEqual([t["rule"] for t in a["milestone"]["triggers"]], ["never-landed"])
        self.assertTrue(a["milestone"]["recommend"])
        self.commit_content("projects/alpha/report.md")
        for i in range(3):
            self.commit_content(f"projects/alpha/context/m{i}.md")
        ms = self.survey()["projects"][0]["milestone"]
        self.assertEqual(ms["ahead"], 5)
        self.assertEqual(ms["substantive_paths"], ["projects/alpha/report.md"])
        self.assertEqual([t["rule"] for t in ms["triggers"]], ["ahead", "substantive", "never-landed"])
        self.assertEqual(ms["triggers"][0], {"rule": "ahead", "value": 5, "threshold": 5})
        self.assertIn("5 commit(s) ahead of main (>= 5)", ms["reasons"])
        # land a milestone dated nine days ago
        stamp = "2026-03-01T12:00:00+00:00"
        p = self.mod("project", "land", "project", "alpha", "first cut", env={"GIT_COMMITTER_DATE": stamp})
        self.assertEqual(p.returncode, 0, p.stdout)
        ms = self.survey()["projects"][0]["milestone"]
        self.assertEqual((ms["ahead"], ms["recommend"], ms["last_landed"], ms["days_since_landed"]),
                         (0, False, "2026-03-01", 9))
        self.commit_content("projects/alpha/context/later.md")
        ms = self.survey()["projects"][0]["milestone"]
        self.assertEqual(ms["triggers"], [{"rule": "days-since-landed", "value": 9, "threshold": 7}])
        self.assertEqual(self.survey(today="2026-03-05")["projects"][0]["milestone"]["triggers"], [])

    def test_progress_resets_after_update_and_sees_todays_worklog(self):
        self.commit_content("projects/alpha/context/n1.md")
        md = (
            "---\nlast_updated: 2026-01-02\nstatus: active\nl0: alpha after update\n---\n\n# Alpha\n\n"
            "## 目標 / 動機\n\ngoal\n\n## 現況\n\nnow better\n\n## 下一步\n\n- next\n"
        )
        p = self.mod("project", "update", "alpha", "apply", "--project-md", self.tmpfile("p.md", md),
                     "--worklog-section", self.tmpfile("s.md", SECTION))
        self.assertEqual(p.returncode, 0, p.stdout)
        pr = self.survey()["projects"][0]["progress"]
        self.assertEqual((pr["commits_since_project_md"], pr["has_progress"]), (0, False))
        self.assertEqual(pr["today_worklog"], f"contexts/work_logs/{DAY}_alpha_update.md")
        self.assertEqual(pr["today_worklog_on"], [f"content/{DAY}"])

    def test_registered_rules_in_process(self):
        reg = audit.Registry()
        reg.register("always-one", lambda ctx: [Finding("always-one", "info", Subject("project", "alpha"), "hi")])
        reg.register("broken-rule", lambda ctx: 1 / 0)
        ctx = Ctx(git=Git(self.ws), main="refs/heads/main", today=DAY, now="18:00", push=False)
        data = day.survey_data(ctx, registry=reg)
        self.assertEqual(data["audit"], {"rules": 2})
        self.assertEqual([(f["rule"], f["severity"]) for f in data["findings"]],
                         [("always-one", "info"), ("broken-rule", "error")])

    def test_missing_main_is_rejected(self):
        p = self.day("survey", "--main", "trunk")
        self.assertEqual((p.returncode, p.first), (1, "result: rejected"), p.stdout)


class SyncTests(DayCase):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.push_from_other()
        self.git("fetch", "-q", "origin")

    def assert_skipped(self, before, *needles: str):
        p = self.day("sync", "--json")
        out = json.loads(p.stdout)
        self.assertEqual((p.returncode, out["result"]), (0, "skipped"), p.stdout)
        for n in needles:
            self.assertTrue(any(n in r for r in out["skip"]), out["skip"])
        self.assertEqual(self.frozen(), before)

    def test_fast_forward(self):
        p = self.day("sync")
        self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        self.assertEqual(self.rev("main"), self.target)
        self.assertTrue((self.ws / "OTHER.md").is_file())
        self.assertEqual(self.status(), "")
        self.assertEqual(self.day("sync").first, "result: noop")

    def test_gate_dirty(self):
        self.write("scratch.md", "untracked\n")
        self.assert_skipped(self.frozen(), "not clean")

    def test_gate_not_on_main(self):
        self.git("switch", "-q", "-c", "side")
        self.assert_skipped(self.frozen(), "on side")

    def test_gate_diverged(self):
        self.local_main_commit()
        self.assert_skipped(self.frozen(), "diverged")

    def test_all_reasons_listed(self):
        self.local_main_commit()
        self.git("switch", "-q", "-c", "side")
        self.write("scratch.md", "untracked\n")
        self.assert_skipped(self.frozen(), "not clean", "on side", "diverged")

    def test_no_tracking_ref(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.assert_skipped(self.frozen(), "remote-tracking")

    def test_ahead_is_noop(self):
        self.day("sync")
        self.local_main_commit()
        before = self.frozen()
        self.assertEqual(self.day("sync").first, "result: noop")
        self.assertEqual(self.frozen(), before)


class PushTests(DayCase):
    def diverge_content(self, d: str) -> tuple[str, str]:
        """``content/<d>`` pushed at one commit, then moved locally to a sibling."""
        tree = self.git("rev-parse", "main^{tree}").strip()
        a = self.git("commit-tree", tree, "-p", "main", "-m", "a").strip()
        b = self.git("commit-tree", tree, "-p", "main", "-m", "b").strip()
        self.git("update-ref", f"refs/heads/content/{d}", a)
        self.git("push", "-q", "origin", f"content/{d}")
        self.git("update-ref", f"refs/heads/content/{d}", b)
        return a, b

    def test_pushes_ahead_and_new_never_forces(self):
        self.local_main_commit()
        self.commit_content("projects/alpha/context/x.md", "contexts/notes/x.md", push=False)
        a, _b = self.diverge_content(YESTERDAY)
        p = self.day("push", "--json")
        out = json.loads(p.stdout)
        self.assertEqual((p.returncode, out["result"]), (0, "ok"), p.stdout)
        self.assertEqual(sorted(out["pushed"]), ["content/2026-03-10", "main", "project/alpha"])
        self.assertEqual((out["diverged"], out["failed"]), ([f"content/{YESTERDAY}"], []))
        states = {e["branch"]: e["state"] for e in out["branches"]}
        self.assertEqual(states["content/2026-03-10"], "new")
        for b in ("main", "project/alpha", "content/2026-03-10"):
            self.assertEqual(self.rev(b, self.remote), self.rev(b), b)
        self.assertEqual(self.rev(f"content/{YESTERDAY}", self.remote), a)  # not forced
        p = self.day("push")
        self.assertEqual(p.first, "result: noop", p.stdout)
        self.assertIn(f"diverged, not pushed (handle by hand): content/{YESTERDAY}", p.stdout)

    def test_no_push_lists_only(self):
        self.local_main_commit()
        remote = self.refs(self.remote)
        out = json.loads(self.day("push", "--no-push", "--json").stdout)
        self.assertEqual(out["result"], "ok")
        self.assertEqual([(e["branch"], e["push"]) for e in out["branches"] if e["push"]], [("main", "disabled")])
        self.assertEqual(self.refs(self.remote), remote)

    def test_stale_tracking_ref_fails_without_forcing(self):
        theirs = self.push_from_other()
        self.local_main_commit()  # tracking ref still says origin/main is our parent
        p = self.day("push", "--json")
        out = json.loads(p.stdout)
        self.assertEqual((p.returncode, out["result"], out["failed"]), (1, "rejected", ["main"]), p.stdout)
        self.assertEqual(self.rev("main", self.remote), theirs)

    def test_behind_is_listed_not_pushed(self):
        self.push_from_other()
        self.git("fetch", "-q", "origin")
        out = json.loads(self.day("push", "--json").stdout)
        self.assertEqual((out["result"], out["behind"], out["pushed"]), ("noop", ["main"], []))


class CycleTests(DayCase):
    def test_survey_commit_land_push_then_noop(self):
        self.write(f"contexts/work_logs/{DAY}_alpha_update.md", "---\nproject: alpha\nl0: day\n---\n\nbody\n")
        self.write("projects/alpha/context/n.md", "n\n")
        s = self.survey("--fetch")
        self.assertEqual(s["worktree"]["to_commit"], 2)
        self.assertEqual(self.mod("content", "commit", "--no-push").returncode, 0)
        self.assertEqual(self.mod("project", "land", "content", "--no-push").returncode, 0)
        p = self.day("push")
        self.assertEqual((p.returncode, p.first), (0, "result: ok"), p.stdout)
        s = self.survey("--fetch")
        self.assertEqual(s["main"]["state"], "in-sync")
        self.assertEqual(s["content_branches"], [])
        self.assertEqual(s["worktree"]["to_commit"], 0)
        self.assertEqual(self.rev("project/alpha", self.remote), self.rev("project/alpha"))
        self.assertEqual(self.day("push").first, "result: noop")

    def test_version(self):
        p = self.day("version")
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.first.startswith("version: "), p.stdout)


if __name__ == "__main__":
    unittest.main()
