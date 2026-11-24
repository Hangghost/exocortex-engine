"""``python -m exocortex_engine.day``: deterministic half of ``/exo:onboard`` and ``/exo:eod``.

Sub-commands:

- ``version``
- ``survey [--fetch]``: one read-only snapshot of the workspace (``main`` against the
  remote, the working tree, ``content/*`` branches, projects, findings). ``--fetch``
  only updates remote-tracking refs; a failed fetch is reported and the last snapshot
  is used.
- ``sync``: guarded fast-forward of the local ``main`` to its remote-tracking ref. The
  working tree must be clean, on ``main``, and ``main`` must fast-forward; otherwise it
  reports ``result: skipped`` with every reason and writes nothing.
- ``push``: push ``main``, ``project/*`` and ``content/*`` where the local branch is
  ahead of (or missing from) the remote. Never forces; diverged branches are listed and
  left alone.

Output and exit codes follow :mod:`exocortex_engine.project`. This module never decides
for the user: unattended authorisation (``--allow``) is the command prose's job.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from typing import Any, Optional, Sequence

from . import audit, index_md, project_md, worklog
from .audit import Finding, Subject, Suggest
from .content import _plan_data, _toplevel, make_plan
from .generated_files import is_contexts_index_path
from .gitref import Refused, three_way_changes, short_ref
from .project import (
    INACTIVE,
    NAME_RE,
    Ctx,
    Out,
    _Parser,
    content_ref,
    project_ref,
    run,
    utf8_streams,
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: milestone recommendation thresholds (fixed in this release)
AHEAD_MIN = 5
DAYS_MIN = 7
PUSH_PREFIXES = ("refs/heads/project/", "refs/heads/content/")


# ---- helpers ----------------------------------------------------------------


def _tracking(ctx: Ctx, ref: str) -> str:
    return f"refs/remotes/{ctx.git.remote}/{short_ref(ref)}"


def _count(ctx: Ctx, a: str, b: str) -> int:
    """Commits reachable from ``b`` but not from ``a``."""
    return int(ctx.git.run("rev-list", "--count", f"{a}..{b}").strip() or 0)


def _relation(ctx: Ctx, local: Optional[str], remote: Optional[str]) -> dict[str, Any]:
    if local is None or remote is None:
        return {"ahead": None, "behind": None, "diverged": False,
                "state": "missing-local" if local is None else "no-remote"}
    ahead, behind = _count(ctx, remote, local), _count(ctx, local, remote)
    state = "in-sync" if not (ahead or behind) else "diverged" if ahead and behind else "ahead" if ahead else "behind"
    return {"ahead": ahead, "behind": behind, "diverged": bool(ahead and behind), "state": state}


def _refs(ctx: Ctx, prefix: str) -> list[str]:
    out = ctx.git.run("for-each-ref", "--format=%(refname)", prefix)
    return sorted(r for r in out.splitlines() if r)


def _head_branch(ctx: Ctx) -> Optional[str]:
    proc = ctx.git.run_bytes("symbolic-ref", "-q", "HEAD", check=False)
    return proc.stdout.decode("utf-8").strip() or None if proc.returncode == 0 else None


def _days(ctx: Ctx, day: str) -> Optional[int]:
    try:
        return (date.fromisoformat(ctx.today) - date.fromisoformat(day)).days
    except ValueError:
        return None


# ---- survey -----------------------------------------------------------------


def _fetch(ctx: Ctx, requested: bool) -> dict[str, Any]:
    if not requested:
        return {"requested": False, "ok": None, "error": None}
    if not ctx.git.has_remote():
        return {"requested": True, "ok": False, "error": f"no remote {ctx.git.remote!r}"}
    # --no-tags: only remote-tracking refs move, never a local ref
    proc = ctx.git.run_bytes("fetch", "--quiet", "--prune", "--no-tags", ctx.git.remote, check=False)
    if proc.returncode == 0:
        return {"requested": True, "ok": True, "error": None}
    lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return {"requested": True, "ok": False, "error": lines[-1] if lines else f"exit {proc.returncode}"}


def _main(ctx: Ctx) -> dict[str, Any]:
    local, remote = ctx.git.rev(ctx.main), ctx.git.rev(_tracking(ctx, ctx.main))
    return {"branch": short_ref(ctx.main), "local": local, "remote": remote, **_relation(ctx, local, remote)}


def _worktree(ctx: Ctx) -> tuple[dict[str, Any], dict[str, int]]:
    """Dirty summary, classified as ``content status`` does; plus uncommitted paths per project."""
    top = _toplevel(ctx)
    head = _head_branch(top)
    plan = make_plan(top, (), write=False)
    data = _plan_data(plan)
    deletes = [p for t in data["targets"] for p in t["deletes"]]
    to_commit = sum(len(t["writes"]) + len(t["deletes"]) for t in data["targets"])
    per_project: dict[str, int] = {}
    for t in data["targets"]:
        if t["branch"].startswith("project/"):
            per_project[t["branch"].split("/", 1)[1]] = len(t["writes"]) + len(t["deletes"])
    summary = {
        "branch": short_ref(head) if head else None,
        "clean": not top.git.dirty_paths(top.git.repo),
        "to_commit": to_commit,
        "deletes": deletes,
        "waiting": len(data["waiting"]),
        "not_handled": [o["path"] for o in data["other"]],
        "refused": [r for t in data["targets"] for r in t["rejects"]],
        "plan": data,
    }
    return summary, per_project


def _landed(ctx: Ctx, main_tip: Optional[str], tip: str) -> bool:
    """Whether landing ``tip`` would bring nothing main lacks (generated indexes ignored)."""
    if main_tip is None:
        return False
    if ctx.git.is_ancestor(tip, main_tip):
        return True
    base = ctx.git.merge_base(main_tip, tip)
    changes, conflicts = three_way_changes(ctx.git, base, main_tip, tip)
    return not [p for p in [*changes, *conflicts] if not is_contexts_index_path(p)]


def _content_branches(ctx: Ctx, findings: list[Finding]) -> list[dict[str, Any]]:
    main_tip = ctx.git.rev(ctx.main)
    local = {short_ref(r).split("/", 1)[1]: r for r in _refs(ctx, "refs/heads/content/")}
    rprefix = f"refs/remotes/{ctx.git.remote}/content/"
    remote = {r[len(rprefix):]: r for r in _refs(ctx, rprefix)}
    items = []
    for day in sorted(set(local) | set(remote)):
        if not DATE_RE.match(day):
            continue
        l_tip = ctx.git.rev(local[day]) if day in local else None
        r_tip = ctx.git.rev(remote[day]) if day in remote else None
        tip = l_tip or r_tip
        if tip is None:
            continue
        location = "both" if l_tip and r_tip else "local" if l_tip else "remote"
        items.append({
            "date": day,
            "branch": f"content/{day}",
            "location": location,
            "tips_match": (l_tip == r_tip) if location == "both" else None,
            "commits": _count(ctx, main_tip, tip) if main_tip else None,
            "landed": _landed(ctx, main_tip, tip),
            "past": day < ctx.today,
        })
        if location == "remote":
            findings.append(Finding(
                rule="content-branch-remote-only", severity="warn", subject=Subject("branch", f"content/{day}"),
                message=f"content/{day} exists on {ctx.git.remote} only; land content needs the local branch",
                suggest=Suggest(f"git branch content/{day} {ctx.git.remote}/content/{day}", mutates=True),
            ))
    return items


def _project_names(ctx: Ctx, main_tip: str) -> list[tuple[str, Optional[str]]]:
    """``(name, INDEX status)`` of the open projects: INDEX rows that are not completed or
    archived, plus local ``project/*`` branches without a row."""
    status: dict[str, Optional[str]] = {}
    text = ctx.git.read_text(main_tip, index_md.INDEX_PATH)
    if text is not None:
        try:
            for row in index_md.rows(text):
                name = row.get("name", "")
                if NAME_RE.match(name):
                    status[name] = row.get("status")
        except index_md.IndexError_ as exc:
            raise Refused(f"{index_md.INDEX_PATH}: {exc}") from None
    for ref in _refs(ctx, "refs/heads/project/"):
        name = short_ref(ref).split("/", 1)[1]
        if NAME_RE.match(name):
            status.setdefault(name, None)
    return [(n, s) for n, s in sorted(status.items()) if s not in INACTIVE]


def _last_landing(ctx: Ctx, name: str) -> tuple[Optional[str], Optional[str]]:
    """``(commit, YYYY-MM-DD)`` of the latest ``land project`` merge of ``name`` on main
    (the ``new`` merge does not count)."""
    out = ctx.git.run(
        "log", "--first-parent", "--merges", "--format=%H%x09%cs%x09%s", f"--grep=^Merge project/{name}: ", ctx.main
    )
    for line in out.splitlines():
        sha, day, subject = (line.split("\t", 2) + ["", ""])[:3]
        if subject.startswith(f"Merge project/{name}: ") and subject != f"Merge project/{name}: new":
            return sha, day
    return None, None


def _progress(ctx: Ctx, name: str, uncommitted: int) -> dict[str, Any]:
    pref = project_ref(name)
    tip = ctx.git.rev(pref)
    today_path = worklog.worklog_path(ctx.today, name)
    today_on = [short_ref(r) for r in (content_ref(ctx.today), ctx.main)
                if ctx.git.rev(r) and ctx.git.read_bytes(r, today_path) is not None]
    last, since = None, None
    if tip is not None:
        last = ctx.git.run("log", "-1", "--format=%H", tip, "--", project_md.project_path(name)).strip() or None
        since = _count(ctx, last, tip) if last else _count(ctx, ctx.main, tip)
    return {
        "branch": short_ref(pref) if tip else None,
        "last_project_md_commit": last,
        "commits_since_project_md": since,
        "uncommitted": uncommitted,
        "today_worklog": today_path,
        "today_worklog_on": today_on,
        "has_progress": bool(since) or uncommitted > 0,
    }


def _milestone(ctx: Ctx, name: str) -> dict[str, Any]:
    pref = project_ref(name)
    tip, main_tip = ctx.git.rev(pref), ctx.git.rev(ctx.main)
    if tip is None or main_tip is None:
        return {"ahead": None, "last_landed": None, "last_landed_commit": None, "days_since_landed": None,
                "substantive": False, "substantive_paths": [], "recommend": False, "triggers": [], "reasons": []}
    ahead = _count(ctx, main_tip, tip)
    landed_sha, landed_day = _last_landing(ctx, name)
    days = _days(ctx, landed_day) if landed_day else None
    base = ctx.git.merge_base(main_tip, tip)
    scope, notes = f"projects/{name}/", f"projects/{name}/context/"
    changed = ctx.git.changed_paths(base, tip, [scope]) if base and ahead else []
    material = [p for p in changed if not p.startswith(notes)]
    triggers: list[dict[str, Any]] = []
    if ahead:
        if ahead >= AHEAD_MIN:
            triggers.append({"rule": "ahead", "value": ahead, "threshold": AHEAD_MIN})
        if material:
            triggers.append({"rule": "substantive", "value": len(material), "threshold": 1})
        if landed_sha is None:
            triggers.append({"rule": "never-landed", "value": None, "threshold": None})
        elif days is not None and days >= DAYS_MIN:
            triggers.append({"rule": "days-since-landed", "value": days, "threshold": DAYS_MIN})
    reasons = []
    for t in triggers:
        if t["rule"] == "ahead":
            reasons.append(f"{ahead} commit(s) ahead of {short_ref(ctx.main)} (>= {AHEAD_MIN})")
        elif t["rule"] == "substantive":
            reasons.append(f"{len(material)} changed path(s) outside {notes}")
        elif t["rule"] == "never-landed":
            reasons.append("never landed")
        else:
            reasons.append(f"{days} day(s) since the last landing on {landed_day} (>= {DAYS_MIN})")
    return {
        "ahead": ahead, "last_landed": landed_day, "last_landed_commit": landed_sha, "days_since_landed": days,
        "substantive": bool(material), "substantive_paths": material[:10], "recommend": bool(triggers),
        "triggers": triggers, "reasons": reasons,
    }


def survey_data(ctx: Ctx, fetch: bool = False, registry: Optional[audit.Registry] = None) -> dict[str, Any]:
    registry = registry if registry is not None else audit.REGISTRY
    fetched = _fetch(ctx, fetch)
    main_tip = ctx.git.rev(ctx.main)
    if main_tip is None:
        raise Refused(f"branch {short_ref(ctx.main)} does not exist")
    main = _main(ctx)
    tree, per_project = _worktree(ctx)
    findings: list[Finding] = []
    branches = _content_branches(ctx, findings)
    projects = [
        {"name": name, "status": status, "progress": _progress(ctx, name, per_project.get(name, 0)),
         "milestone": _milestone(ctx, name)}
        for name, status in _project_names(ctx, main_tip)
    ]
    findings.extend(registry.run_all(ctx))
    return {
        "today": ctx.today,
        "fetch": fetched,
        "main": main,
        "worktree": tree,
        "content_branches": branches,
        "projects": projects,
        "findings": [f.to_dict() for f in findings],
        "audit": {"rules": len(registry)},
    }


def cmd_survey(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    data = survey_data(ctx, fetch=a.fetch)
    out.data.update(data)
    f, m, w = data["fetch"], data["main"], data["worktree"]
    if f["requested"] and not f["ok"]:
        out.say(f"warning: fetch failed, showing the last snapshot: {f['error']}")
    out.say(f"# survey ({ctx.today})")
    rel = m["state"] if m["ahead"] is None else f"{m['state']} (ahead {m['ahead']}, behind {m['behind']})"
    out.say(f"main: {rel} against {ctx.git.remote}")
    if w["clean"]:
        out.say(f"worktree: clean (on {w['branch'] or 'detached HEAD'})")
    else:
        out.say(f"worktree: {w['to_commit']} path(s) to commit ({len(w['deletes'])} DELETE), "
                f"{w['waiting']} waiting to land, {len(w['not_handled'])} not handled, {len(w['refused'])} refused")
    pending = [b for b in data["content_branches"] if not b["landed"]]
    out.say(f"content branches: {len(data['content_branches'])} ({len(pending)} not landed)")
    for b in data["content_branches"]:
        tag = "landed" if b["landed"] else f"{b['commits']} commit(s)"
        out.say(f"- {b['branch']} [{b['location']}] {tag}{' (past)' if b['past'] and not b['landed'] else ''}")
    out.say(f"projects: {len(data['projects'])}")
    for p in data["projects"]:
        pr, ms = p["progress"], p["milestone"]
        line = f"- {p['name']}: progress {pr['commits_since_project_md']} commit(s)"
        line += f", {pr['uncommitted']} uncommitted" if pr["uncommitted"] else ""
        line += f"; ahead {ms['ahead']}"
        if ms["recommend"]:
            line += "; milestone recommended: " + "; ".join(ms["reasons"])
        out.say(line)
    rules = data["audit"]["rules"]
    out.say(f"audit: {rules} rule(s) registered" if rules else "audit: no rules registered")
    for fd in data["findings"]:
        out.say(f"- [{fd['severity']}] {fd['rule']} {fd['subject']['kind']}:{fd['subject']['id']}: {fd['message']}")


# ---- sync -------------------------------------------------------------------


def cmd_sync(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    top = _toplevel(ctx)
    local, remote = ctx.git.rev(ctx.main), ctx.git.rev(_tracking(ctx, ctx.main))
    rel = _relation(ctx, local, remote)
    out.data.update(branch=short_ref(ctx.main), before=local, remote=remote, state=rel["state"])
    if local is None:
        raise Refused(f"branch {short_ref(ctx.main)} does not exist")
    if remote is None:
        out.result = "skipped"
        out.data["skip"] = [f"no {short_ref(_tracking(ctx, ctx.main))} remote-tracking ref (run survey --fetch)"]
        out.say(f"skipped: {out.data['skip'][0]}")
        return
    if rel["state"] in ("in-sync", "ahead"):
        out.result = "noop"
        out.say(f"{short_ref(ctx.main)} is {rel['state']} against {short_ref(_tracking(ctx, ctx.main))}; "
                "nothing to fast-forward")
        return
    reasons = []
    dirty = top.git.dirty_paths(top.git.repo)
    if dirty:
        reasons.append(f"working tree is not clean ({len(dirty)} path(s))")
    head = _head_branch(top)
    if head != ctx.main:
        reasons.append(f"working tree is on {short_ref(head) if head else 'a detached HEAD'}, "
                       f"not {short_ref(ctx.main)}")
    if rel["state"] == "diverged":
        reasons.append(f"{short_ref(ctx.main)} and {short_ref(_tracking(ctx, ctx.main))} have diverged "
                       f"(ahead {rel['ahead']}, behind {rel['behind']}); not a fast-forward")
    if reasons:
        out.result = "skipped"
        out.data["skip"] = reasons
        for r in reasons:
            out.say(f"skipped: {r}")
        return
    how = top.git.advance(ctx.main, remote, local)
    out.data.update(after=remote, main_write=how)
    out.say(f"fast-forwarded {short_ref(ctx.main)} {local[:10]}..{remote[:10]} ({rel['behind']} commit(s), {how})")


# ---- push -------------------------------------------------------------------


def cmd_push(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    if not ctx.git.has_remote():
        raise Refused(f"no remote {ctx.git.remote!r}")
    refs = ([ctx.main] if ctx.git.rev(ctx.main) else []) + [r for p in PUSH_PREFIXES for r in _refs(ctx, p)]
    entries = []
    for ref in refs:
        local, remote = ctx.git.rev(ref), ctx.git.rev(_tracking(ctx, ref))
        state = "new" if remote is None else _relation(ctx, local, remote)["state"]
        entries.append({"branch": short_ref(ref), "state": state, "local": local, "remote": remote, "push": None})
    candidates = [e for e in entries if e["state"] in ("ahead", "new")]
    for e in candidates:
        ref = f"refs/heads/{e['branch']}"
        e["push"] = ctx.git.push(f"{ref}:{ref}") if ctx.push else "disabled"  # no '+': never forced
    out.data.update(
        branches=entries,
        pushed=[e["branch"] for e in candidates if e["push"] == "pushed"],
        failed=[e["branch"] for e in candidates if (e["push"] or "").startswith("failed")],
        diverged=[e["branch"] for e in entries if e["state"] == "diverged"],
        behind=[e["branch"] for e in entries if e["state"] == "behind"],
    )
    for e in candidates:
        out.say(f"push {e['branch']} ({e['state']}): {e['push']}")
    for b in out.data["diverged"]:
        out.say(f"diverged, not pushed (handle by hand): {b}")
    for b in out.data["behind"]:
        out.say(f"behind {ctx.git.remote}, nothing to push: {b}")
    if not candidates:
        out.result = "noop"
        out.say(f"nothing ahead of {ctx.git.remote}")
    if out.data["failed"]:
        raise Refused("push failed for " + ", ".join(out.data["failed"]))


# ---- entry point ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="workspace repository (default: current directory)")
    common.add_argument("--main", default="main", help="the main branch (default: main)")
    common.add_argument("--remote", default="origin", help="remote to fetch from and push to (default: origin)")
    common.add_argument("--no-push", action="store_true", help="push: list what would be pushed, push nothing")
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    common.add_argument("--now", help="override the current time (HH:MM)")

    p = _Parser(prog="exocortex_engine.day", description="Start and end of the day (/exo:onboard, /exo:eod).")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("version", parents=[common], help="engine and contract version")
    s = sub.add_parser("survey", parents=[common], help="read-only snapshot of the workspace")
    s.add_argument("--fetch", action="store_true", help="fetch first (moves remote-tracking refs only)")
    sub.add_parser("sync", parents=[common], help="guarded fast-forward of main to its remote-tracking ref")
    sub.add_parser("push", parents=[common], help="push main, project/* and content/* that are ahead; never force")
    return p


HANDLERS = {"survey": cmd_survey, "sync": cmd_sync, "push": cmd_push}


def main(argv: Optional[Sequence[str]] = None) -> int:
    utf8_streams()
    args = build_parser().parse_args(argv)
    return run(args, args.cmd, HANDLERS)


if __name__ == "__main__":
    sys.exit(main())
