"""``python -m exocortex_engine.project``: deterministic half of ``/exo:project``.

Sub-commands: ``version``, ``new``, ``load``, ``update gather``, ``update apply``,
``board``, ``land content``, ``land project``.

Output: the first line is machine-readable ``key: value`` (``result: ok`` and so
on; ``version: x.y.z`` for ``version``), the rest is for people. ``--json`` prints
one JSON object instead. Exit codes: 0 success, 1 refused (a precondition does
not hold; nothing was written), 2 usage error.

Every write is ref-anchored (see :mod:`exocortex_engine.gitref`): the working
tree is never switched to another branch.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from . import CONTRACT, __version__, contexts_index, generated_files, index_md, project_md, worklog
from .gitref import (
    ZERO,
    CASConflict,
    Git,
    GitError,
    RefUpdate,
    Refused,
    branch_ref,
    short_ref,
    three_way_changes,
)

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
INSTANCE_FILE = ".exocortex/instance.toml"
INACTIVE = ("completed", "archived")


class UsageError(Exception):
    pass


@dataclass
class Out:
    command: str
    result: str = "ok"
    lines: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def say(self, line: str = "") -> None:
        self.lines.append(line)


@dataclass
class Ctx:
    git: Git
    main: str  # full ref
    today: str
    now: str
    push: bool


# ---- helpers ----------------------------------------------------------------


def project_ref(name: str) -> str:
    return f"refs/heads/project/{name}"


def content_ref(day: str) -> str:
    return f"refs/heads/content/{day}"


def check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise UsageError(f"project name must be kebab-case (a-z, 0-9, '-'): {name!r}")


def check_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise UsageError(f"not a YYYY-MM-DD date: {value!r}") from None
    return value


def read_input(path: str) -> str:
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return fh.read()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from None


def require_tip(ctx: Ctx, ref: str, what: str) -> str:
    tip = ctx.git.rev(ref)
    if tip is None:
        raise Refused(f"{what} {short_ref(ref)} does not exist")
    return tip


def write_refs(ctx: Ctx, updates: Sequence[RefUpdate], out: Optional[Out] = None) -> dict[str, str]:
    """Apply ref moves. Checked-out branches are fast-forwarded first (in their
    worktree); every other ref moves in one atomic CAS transaction.

    Every checked-out target is checked for overlapping uncommitted changes before
    any ref moves, so a refusal means nothing was written. Local files that already
    hold what a move brings do not count as overlapping (see :meth:`Git.overlap`);
    they are listed in ``out`` as normalized."""
    holders = ctx.git.holders()
    held = [u for u in updates if u.ref in holders]
    rest = [u for u in updates if u.ref not in holders]
    plans = {}
    for u in held:
        if u.new == ZERO:
            raise Refused(f"{short_ref(u.ref)} is checked out at {holders[u.ref].path}; not deleting it")
        plans[u.ref] = ctx.git.check_advance(u.ref, u.new, u.old)
    how: dict[str, str] = {}
    for u in held:
        how[u.ref] = ctx.git.advance(u.ref, u.new, u.old, plans[u.ref])
    ctx.git.update_refs(rest)
    for u in rest:
        how[u.ref] = "deleted" if u.new == ZERO else "update-ref"
    normalized = sorted({s.path for plan in plans.values() if plan for s in plan.same})
    if out is not None and normalized:
        out.data["normalized"] = normalized
        out.say("already committed, left as they are: " + ", ".join(normalized))
    return how


def push_refs(ctx: Ctx, out: Out, *refs: str) -> None:
    pushes = out.data.setdefault("push", {})
    for ref in refs:
        res = ctx.git.push(f"{ref}:{ref}") if ctx.push else "disabled"
        pushes[short_ref(ref)] = res
        out.say(f"push {short_ref(ref)}: {res}")


def worklogs(ctx: Ctx, rev: str, name: str) -> list[str]:
    """Paths of work_logs at ``rev`` whose frontmatter says ``project: <name>``."""
    proc = ctx.git.run_bytes(
        "grep", "-l", "-E", f"^project:[[:space:]]*{name}[[:space:]]*$", rev, "--", worklog.WORKLOG_DIR + "/",
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise GitError(["grep"], proc.returncode, proc.stderr.decode("utf-8", "replace"))
    paths = []
    for line in proc.stdout.decode("utf-8").splitlines():
        _, _, path = line.partition(":")
        if path.endswith(".md"):
            paths.append(path)
    return sorted(paths)


def content_refs(ctx: Ctx) -> list[str]:
    out = ctx.git.run("for-each-ref", "--format=%(refname)", "refs/heads/content/")
    return sorted(r for r in out.splitlines() if r)


def recent_worklogs(ctx: Ctx, name: str, limit: int = 3) -> list[str]:
    """Most recent work_logs for ``name`` across main and local content branches."""
    seen: dict[str, str] = {}
    main_tip = ctx.git.rev(ctx.main)
    on_main = set(worklogs(ctx, ctx.main, name)) if main_tip else set()
    for p in on_main:
        seen[p] = short_ref(ctx.main)
    for rev in content_refs(ctx):
        for path in worklogs(ctx, rev, name):
            # A content branch carries main's files too; name it only for what main lacks or has older.
            if path not in on_main or ctx.git.read_bytes(rev, path) != ctx.git.read_bytes(ctx.main, path):
                seen[path] = short_ref(rev)
    paths = sorted(seen, key=lambda p: p.rsplit("/", 1)[-1], reverse=True)[:limit]
    return [f"{seen[p]}:{p}" for p in paths]


def instance_contract(git: Git) -> Optional[int]:
    """Top-level ``contract = N`` from ``.exocortex/instance.toml``, if any."""
    top = Path(git.run("rev-parse", "--show-toplevel").strip())
    path = top / INSTANCE_FILE
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("["):
            break
        m = re.fullmatch(r"contract\s*=\s*(\S+)", line)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                raise Refused(f"{INSTANCE_FILE}: contract must be an integer, got {m.group(1)!r}") from None
    return None


# ---- sub-commands -------------------------------------------------------------


def cmd_new(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    name = a.name
    check_name(name)
    if not a.title.strip() or not a.goal.strip():
        raise UsageError("--title and --goal must not be empty")
    pref = project_ref(name)
    main_tip = require_tip(ctx, ctx.main, "branch")
    if ctx.git.rev(pref):
        raise Refused(f"{short_ref(pref)} already exists locally")
    exists, basis = ctx.git.remote_has_branch(pref)
    if exists:
        raise Refused(f"{short_ref(pref)} already exists on {ctx.git.remote}")
    if basis == "snapshot":
        out.say(f"warning: {ctx.git.remote} unreachable; checked the local remote-tracking refs only")
    index_text = ctx.git.read_text(main_tip, index_md.INDEX_PATH) or index_md.DEFAULT_INDEX
    try:
        if index_md.get_row(index_text, name):
            raise Refused(f"{index_md.INDEX_PATH} on {short_ref(ctx.main)} already has a row for {name}")
        index_text = index_md.upsert(
            index_text,
            name,
            {"status": "active", "last_updated": ctx.today, "description": project_md.one_line(a.goal)},
        )
    except index_md.IndexError_ as exc:
        raise Refused(f"{index_md.INDEX_PATH}: {exc}") from None
    if ctx.git.read_bytes(main_tip, project_md.project_path(name)) is not None:
        raise Refused(f"{project_md.project_path(name)} already exists on {short_ref(ctx.main)}")
    md = project_md.render_new(a.title, a.goal, ctx.today, a.current or "", a.next or "")
    changes = ctx.git.blob_changes(
        {
            project_md.project_path(name): md,
            f"projects/{name}/context/.gitkeep": "",
            index_md.INDEX_PATH: index_text,
        }
    )
    tree = ctx.git.build_tree(main_tip, changes)
    c1 = ctx.git.commit_tree(tree, [main_tip], f"project({name}): new")
    merge = ctx.git.commit_tree(tree, [main_tip, c1], f"Merge project/{name}: new")
    how = write_refs(ctx, [RefUpdate(ctx.main, merge, main_tip), RefUpdate(pref, c1, ZERO)], out)
    out.data.update(name=name, branch=short_ref(pref), commit=c1, main=merge, main_write=how[ctx.main])
    out.say(f"created {short_ref(pref)} at {c1[:10]} and merged into {short_ref(ctx.main)} ({merge[:10]}, {how[ctx.main]})")
    push_refs(ctx, out, pref, ctx.main)


def _source(ctx: Ctx, name: str) -> tuple[str, str, str]:
    """``(rev label, commit, PROJECT.md text)`` from the project branch, else main."""
    path = project_md.project_path(name)
    for ref in (project_ref(name), ctx.main):
        tip = ctx.git.rev(ref)
        if tip:
            text = ctx.git.read_text(tip, path)
            if text is not None:
                return short_ref(ref), tip, text
    raise Refused(f"no {path} on {short_ref(project_ref(name))} or {short_ref(ctx.main)}")


def cmd_load(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    name = a.name
    check_name(name)
    label, tip, text = _source(ctx, name)
    fm = project_md.frontmatter(text)
    get = fm.get if fm else (lambda _k: None)
    logs = recent_worklogs(ctx, name)
    out.data.update(
        name=name, source=label, status=get("status"), last_updated=get("last_updated"), l0=get("l0"),
        goal=project_md.section(text, project_md.SEC_GOAL),
        current=project_md.section(text, project_md.SEC_CURRENT),
        next=project_md.section(text, project_md.SEC_NEXT),
        worklogs=logs,
    )
    if label != short_ref(project_ref(name)):
        out.say(f"note: {short_ref(project_ref(name))} does not exist; read from {label}")
    head = ctx.git.read_bytes("HEAD", project_md.project_path(name))
    if label == short_ref(project_ref(name)) and head is not None and head != text.encode("utf-8"):
        hint = f"hint: the PROJECT.md in your checkout (HEAD) differs from {label}; the branch is the truth until `land project`"
        out.data["head_hint"] = hint
        out.say(hint)
    out.say(f"# {name} ({label} {tip[:10]})")
    out.say(f"status: {get('status')}  last_updated: {get('last_updated')}")
    out.say(f"l0: {get('l0')}")
    for title in (project_md.SEC_GOAL, project_md.SEC_CURRENT, project_md.SEC_NEXT):
        body = project_md.section(text, title)
        out.say(f"\n## {title}\n")
        out.say((body or "").strip() or "(empty)")
    out.say("\n## recent work_logs\n")
    out.say("\n".join(f"- {p}" for p in logs) if logs else "(none)")


def cmd_gather(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    name = a.name
    check_name(name)
    pref = project_ref(name)
    tip = require_tip(ctx, pref, "branch")
    path = project_md.project_path(name)
    text = ctx.git.read_text(tip, path)
    if text is None:
        raise Refused(f"{path} is missing on {short_ref(pref)}")
    last = ctx.git.run("log", "-1", "--format=%H", tip, "--", path).strip()
    commits = []
    if last and last != tip:
        log = ctx.git.run("log", "--format=%h %ad %s", "--date=short", f"{last}..{tip}")
        commits = [ln for ln in log.splitlines() if ln]
    fm = project_md.frontmatter(text)
    since = (fm.get("last_updated") if fm else None) or "0000-00-00"
    main_tip = require_tip(ctx, ctx.main, "branch")
    logs = [p for p in worklogs(ctx, main_tip, name) if (worklog.parse_name(p) or ("",))[0] >= since]
    today_path = worklog.worklog_path(ctx.today, name)
    today_on = [short_ref(r) for r in (content_ref(ctx.today), ctx.main)
                if ctx.git.rev(r) and ctx.git.read_bytes(r, today_path) is not None]
    out.data.update(
        name=name, branch=short_ref(pref), last_project_md_commit=last, last_updated=since,
        commits_since=commits, worklogs_since=logs, today_worklog=today_path, today_worklog_on=today_on,
        project_md=text,
    )
    out.say(f"# update gather: {name} (today {ctx.today})")
    out.say(f"PROJECT.md last changed in {last[:10]} (last_updated {since})")
    out.say(f"\n## commits on {short_ref(pref)} since then\n")
    out.say("\n".join(f"- {c}" for c in commits) if commits else "(none)")
    out.say(f"\n## work_logs on {short_ref(ctx.main)} since {since}\n")
    out.say("\n".join(f"- {p}" for p in logs) if logs else "(none)")
    out.say(f"\n## today's work_log ({today_path})\n")
    out.say(f"exists on: {', '.join(today_on)}" if today_on else "not written yet")
    out.say(f"\n## current PROJECT.md ({short_ref(pref)})\n")
    out.say(text.rstrip("\n"))


def cmd_apply(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    name = a.name
    check_name(name)
    new_md = read_input(a.project_md)
    section = read_input(a.worklog_section)
    pref, cref = project_ref(name), content_ref(ctx.today)
    errors = worklog.validate_section(section)
    tip = ctx.git.rev(pref)
    if tip is None:
        errors = project_md.validate(new_md) + errors + [f"branch {short_ref(pref)} does not exist"]
    else:
        old_md = ctx.git.read_text(tip, project_md.project_path(name))
        if old_md is None:
            errors = project_md.validate(new_md) + errors
            errors.append(f"{project_md.project_path(name)} is missing on {short_ref(pref)}")
        else:
            errors = project_md.check_update(old_md, new_md) + errors
    if errors:
        out.data["errors"] = errors
        raise Refused("; ".join(errors))
    assert tip is not None
    new_md = project_md.set_frontmatter(new_md, "last_updated", ctx.today)
    files: dict[str, Optional[str]] = {project_md.project_path(name): new_md}
    index_text = ctx.git.read_text(tip, index_md.INDEX_PATH)
    index_note = None
    try:
        if index_text is not None and index_md.get_row(index_text, name):
            files[index_md.INDEX_PATH] = index_md.upsert(index_text, name, {"last_updated": ctx.today})
        else:
            index_note = f"warning: {index_md.INDEX_PATH} on {short_ref(pref)} has no row for {name}; INDEX not updated"
    except index_md.IndexError_ as exc:
        raise Refused(f"{index_md.INDEX_PATH} on {short_ref(pref)}: {exc}") from None
    p_tree = ctx.git.build_tree(tip, ctx.git.blob_changes(files))
    updates = []
    p_commit = None
    if p_tree != ctx.git.tree_of(tip):
        p_commit = ctx.git.commit_tree(p_tree, [tip], f"project({name}): update {ctx.today}")
        updates.append(RefUpdate(pref, p_commit, tip))

    c_tip = ctx.git.rev(cref)
    c_base = c_tip or ctx.git.rev(f"refs/remotes/{ctx.git.remote}/{short_ref(cref)}") or require_tip(ctx, ctx.main, "branch")
    wl_path = worklog.worklog_path(ctx.today, name)
    existing = ctx.git.read_text(c_base, wl_path)
    fm = project_md.frontmatter(new_md)
    summary = a.worklog_l0 or (fm.get("l0") if fm else "") or ""
    l0 = summary if a.worklog_l0 else f"{name} 進度更新（{ctx.today}）：{summary}"
    wl_text = worklog.append(existing, name=name, date=ctx.today, hhmm=ctx.now, body=section, l0=l0)
    c_changes = ctx.git.blob_changes({wl_path: wl_text})
    # The work_log is listed in contexts/work_logs/INDEX.md: recompute it in the same commit.
    idx_changes, idx_rejects = contexts_index.regen_for_changes(ctx.git, c_base, c_changes)
    c_changes.update(idx_changes)
    c_tree = ctx.git.build_tree(c_base, c_changes)
    c_commit = ctx.git.commit_tree(c_tree, [c_base], f"content: {name} update {ctx.today} {ctx.now}")
    updates.append(RefUpdate(cref, c_commit, c_tip or ZERO))
    write_refs(ctx, updates)
    out.data.update(
        name=name, project_commit=p_commit, content_branch=short_ref(cref), content_commit=c_commit,
        worklog=wl_path, worklog_appended=existing is not None,
    )
    if p_commit:
        out.say(f"{short_ref(pref)}: {p_commit[:10]} (PROJECT.md, INDEX last_updated {ctx.today})")
    else:
        out.say(f"{short_ref(pref)}: unchanged")
    if index_note:
        out.say(index_note)
    for msg in idx_rejects:
        out.say(f"warning: index not regenerated: {msg}")
    verb = "appended ## " + ctx.now + " to" if existing is not None else "created"
    out.say(f"{short_ref(cref)}: {c_commit[:10]} ({verb} {wl_path})")
    push_refs(ctx, out, *([pref] if p_commit else []), cref)


def cmd_board(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    main_tip = require_tip(ctx, ctx.main, "branch")
    text = ctx.git.read_text(main_tip, index_md.INDEX_PATH)
    if text is None:
        raise Refused(f"no {index_md.INDEX_PATH} on {short_ref(ctx.main)}")
    try:
        rows = index_md.rows(text)
    except index_md.IndexError_ as exc:
        raise Refused(f"{index_md.INDEX_PATH}: {exc}") from None
    today = date.fromisoformat(ctx.today)
    items = []
    for row in rows:
        name = row.get("name", "")
        if not name or row.get("status") in INACTIVE:
            continue
        status, updated, nxt, source = row.get("status"), row.get("last_updated"), None, "INDEX"
        try:
            source, _, md = _source(ctx, name) if NAME_RE.match(name) else ("INDEX", "", "")
        except Refused:
            md = ""
        fm = project_md.frontmatter(md) if md else None
        if fm:
            status = fm.get("status") or status
            updated = fm.get("last_updated") or updated
        if md:
            nxt = project_md.first_actionable(project_md.section(md, project_md.SEC_NEXT))
        try:
            days = (today - date.fromisoformat(updated or "")).days
        except ValueError:
            days = None
        items.append(dict(name=name, status=status, last_updated=updated, days=days, next=nxt, source=source))
    out.data["projects"] = items
    out.say(f"# board ({ctx.today}, {len(items)} projects)")
    for it in items:
        age = f"{it['days']}d" if it["days"] is not None else "?"
        out.say(f"- {it['name']} [{it['status']}] {it['last_updated']} ({age}) — {it['next'] or '(no next step)'}")


def cmd_land_content(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    day = check_date(a.date) if a.date else ctx.today
    cref = content_ref(day)
    tip = require_tip(ctx, cref, "branch")
    if cref in ctx.git.holders():
        raise Refused(f"{short_ref(cref)} is checked out at {ctx.git.holders()[cref].path}; not landing it")
    main_tip = require_tip(ctx, ctx.main, "branch")
    base = ctx.git.merge_base(main_tip, tip)
    changes, conflicts = three_way_changes(ctx.git, base, main_tip, tip)
    # Generated indexes are recomputed on main from the merged folder, never copied from the
    # branch: a stale one on the branch must not land, and a main-side edit is no conflict.
    def _is_generated(path: str) -> bool:
        m = generated_files.CONTEXTS_INDEX_PATH_RE.match(path)
        return bool(m) and contexts_index.folder_is_indexed(ctx.git, main_tip, m.group(1))
    changes = {p: e for p, e in changes.items() if not _is_generated(p)}
    conflicts = [p for p in conflicts if not _is_generated(p)]
    if conflicts:
        out.data["conflicts"] = conflicts
        raise Refused(f"changed on both {short_ref(ctx.main)} and {short_ref(cref)}: " + ", ".join(conflicts))
    idx_changes, idx_rejects = contexts_index.regen_for_changes(ctx.git, main_tip, changes)
    if idx_rejects:
        raise Refused("; ".join(idx_rejects))
    changes.update(idx_changes)
    updates = []
    commit = None
    if changes:
        tree = ctx.git.build_tree(main_tip, changes)
        commit = ctx.git.commit_tree(tree, [main_tip], f"content: {day} daily snapshot")
        updates.append(RefUpdate(ctx.main, commit, main_tip))
    updates.append(RefUpdate(cref, ZERO, tip))
    how = write_refs(ctx, updates, out)
    out.data.update(date=day, commit=commit, paths=sorted(changes), deleted=short_ref(cref),
                    main_write=how.get(ctx.main))
    if commit:
        out.say(f"squashed {len(changes)} path(s) from {short_ref(cref)} into {short_ref(ctx.main)}: {commit[:10]} ({how[ctx.main]})")
    else:
        out.result = "noop"
        out.say(f"{short_ref(cref)} had nothing that {short_ref(ctx.main)} lacks")
    out.say(f"deleted local branch {short_ref(cref)}")
    if commit:
        push_refs(ctx, out, ctx.main)
    main_pushed = not commit or out.data.get("push", {}).get(short_ref(ctx.main)) == "pushed"
    if ctx.push and main_pushed:
        exists, _ = ctx.git.remote_has_branch(cref)
        res = ctx.git.push(f":{cref}") if exists else "skipped: not on remote"
    else:
        res = "disabled" if not ctx.push else f"skipped: {short_ref(ctx.main)} was not pushed"
    out.data.setdefault("push", {})[f"delete {short_ref(cref)}"] = res
    out.say(f"push delete {short_ref(cref)}: {res}")


def cmd_land_project(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    name = a.name
    check_name(name)
    if not a.milestone.strip():
        raise UsageError("milestone must not be empty")
    pref = project_ref(name)
    tip = require_tip(ctx, pref, "branch")
    main_tip = require_tip(ctx, ctx.main, "branch")
    if ctx.git.is_ancestor(tip, main_tip):
        out.result = "noop"
        out.data.update(name=name, commit=None)
        out.say(f"{short_ref(pref)} is already in {short_ref(ctx.main)}")
        return
    base = ctx.git.merge_base(main_tip, tip)
    scope = f"projects/{name}/"
    changes, conflicts = three_way_changes(ctx.git, base, main_tip, tip, [scope])
    if conflicts:
        out.data["conflicts"] = conflicts
        raise Refused(f"changed on both {short_ref(ctx.main)} and {short_ref(pref)}: " + ", ".join(conflicts))
    row = None
    tip_index = ctx.git.read_text(tip, index_md.INDEX_PATH)
    main_index = ctx.git.read_text(main_tip, index_md.INDEX_PATH)
    try:
        row = index_md.get_row_line(tip_index, name) if tip_index else None
        if row is not None:
            merged = index_md.set_row_line(main_index or index_md.DEFAULT_INDEX, name, row)
            if merged != main_index:
                changes.update(ctx.git.blob_changes({index_md.INDEX_PATH: merged}))
    except index_md.IndexError_ as exc:
        raise Refused(f"{index_md.INDEX_PATH}: {exc}") from None
    ignored = [p for p in (ctx.git.changed_paths(base, tip) if base else [])
               if not p.startswith(scope) and p != index_md.INDEX_PATH]
    tree = ctx.git.build_tree(main_tip, changes)
    commit = ctx.git.commit_tree(tree, [main_tip, tip], f"Merge project/{name}: {a.milestone.strip()}")
    how = write_refs(ctx, [RefUpdate(ctx.main, commit, main_tip)], out)
    out.data.update(name=name, commit=commit, paths=sorted(changes), ignored=ignored, main_write=how[ctx.main])
    out.say(f"merged {short_ref(pref)} into {short_ref(ctx.main)}: {commit[:10]} ({len(changes)} path(s), {how[ctx.main]}); branch kept")
    if ignored:
        out.say("warning: outside the project's scope, not landed: " + ", ".join(ignored))
    push_refs(ctx, out, ctx.main)


# ---- entry point ----------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        sys.stdout.write(f"result: usage-error\n{self.prog}: {message}\n")
        self.print_usage(sys.stdout)
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="workspace repository (default: current directory)")
    common.add_argument("--main", default="main", help="the main branch (default: main)")
    common.add_argument("--remote", default="origin", help="remote to push to (default: origin)")
    common.add_argument("--no-push", action="store_true", help="do not push after writing")
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    common.add_argument("--now", help="override the current time (HH:MM)")

    p = _Parser(prog="exocortex_engine.project", description="Project context core (/exo:project).")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("version", parents=[common], help="engine and contract version")

    s = sub.add_parser("new", parents=[common], help="create a project")
    s.add_argument("name")
    s.add_argument("--title", required=True)
    s.add_argument("--goal", required=True)
    s.add_argument("--current")
    s.add_argument("--next")

    s = sub.add_parser("load", parents=[common], help="show a project's context")
    s.add_argument("name")

    s = sub.add_parser("update", help="update <name> gather | apply")
    s.add_argument("name")
    usub = s.add_subparsers(dest="step", required=True, parser_class=_Parser)
    usub.add_parser("gather", parents=[common])
    ap = usub.add_parser("apply", parents=[common])
    ap.add_argument("--project-md", required=True, help="file with the full new PROJECT.md")
    ap.add_argument("--worklog-section", required=True, help="file with the ### sections for today's work_log")
    ap.add_argument("--worklog-l0", help="one-line summary of the day for the work_log frontmatter")

    sub.add_parser("board", parents=[common], help="overview of open projects")

    s = sub.add_parser("land", help="land a content or project branch on main")
    lsub = s.add_subparsers(dest="kind", required=True, parser_class=_Parser)
    lc = lsub.add_parser("content", parents=[common])
    lc.add_argument("date", nargs="?")
    lp = lsub.add_parser("project", parents=[common])
    lp.add_argument("name")
    lp.add_argument("milestone")
    return p


HANDLERS = {
    "new": cmd_new,
    "load": cmd_load,
    "gather": cmd_gather,
    "apply": cmd_apply,
    "board": cmd_board,
    "land-content": cmd_land_content,
    "land-project": cmd_land_project,
}


def _emit(out: Out, as_json: bool) -> None:
    if as_json:
        payload = {"result": out.result, "command": out.command, **out.data, "messages": out.lines}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(f"result: {out.result}\n")
        if out.lines:
            sys.stdout.write("\n".join(out.lines) + "\n")


def utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    utf8_streams()
    args = build_parser().parse_args(argv)
    key = {"update": getattr(args, "step", None), "land": f"land-{getattr(args, 'kind', '')}"}.get(args.cmd, args.cmd)
    return run(args, key, HANDLERS)


def run(args: argparse.Namespace, key: str, handlers: dict) -> int:
    """Common driver: version, context set-up, dispatch to ``handlers[key]``, output and
    exit codes. Shared by the ``project`` and ``content`` CLIs."""
    out = Out(command=key)
    if not Path(args.repo).is_dir():
        out.result = "usage-error"
        out.say(f"error: not a directory: {args.repo}")
        _emit(out, getattr(args, "json", False))
        return 2
    git = Git(Path(args.repo), remote=args.remote)

    if args.cmd == "version":
        try:
            ws = instance_contract(git)
        except (GitError, Refused, OSError):
            ws = None
        if args.json:
            sys.stdout.write(json.dumps({"version": __version__, "contract": CONTRACT, "workspace_contract": ws}) + "\n")
        else:
            sys.stdout.write(f"version: {__version__}\ncontract: {CONTRACT}\n")
            if ws is not None:
                sys.stdout.write(f"workspace_contract: {ws}{' (unsupported)' if ws > CONTRACT else ''}\n")
        return 0

    try:
        today = check_date(args.today) if args.today else date.today().isoformat()
        now = args.now or datetime.now().strftime("%H:%M")
        if not re.fullmatch(r"\d{2}:\d{2}", now):
            raise UsageError(f"--now must be HH:MM: {now!r}")
        if not git.ok("rev-parse", "--git-dir"):
            raise UsageError(f"not a git repository: {args.repo}")
        ws = instance_contract(git)
        if ws is not None and ws > CONTRACT:
            raise Refused(
                f"{INSTANCE_FILE} asks for contract {ws}, this engine ({__version__}) supports up to {CONTRACT}; "
                "update the engine"
            )
        ctx = Ctx(git=git, main=branch_ref(args.main), today=today, now=now, push=not args.no_push)
        handlers[key](ctx, out, args)
    except UsageError as exc:
        out.result = "usage-error"
        out.say(f"error: {exc}")
        _emit(out, args.json)
        return 2
    except CASConflict as exc:
        out.result = "rejected"
        out.say(f"conflict: {exc}")
        _emit(out, args.json)
        return 1
    except Refused as exc:
        out.result = "rejected"
        out.say(f"refused: {exc}")
        _emit(out, args.json)
        return 1
    except GitError as exc:
        out.result = "error"
        out.say(f"git error: {exc}")
        _emit(out, args.json)
        return 1
    _emit(out, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
