"""``python -m exocortex_engine.todo``: deterministic half of ``/exo:todo``.

Sub-commands: ``version``, ``add``, ``list``, ``set``, ``done``. They read and write
``inbox/todos.md`` through git refs only (the working tree is never touched): the base is
the tip of ``content/<today>``, else ``origin/content/<today>``, else ``main``; a write is
one commit ``content: todo <op> <ticket>`` on ``content/<today>``, moved with a
compare-and-swap (retried up to three times with the new tip), then pushed.

Output, ``--json`` and exit codes are those of :mod:`exocortex_engine.project`.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import index_md, todo_md
from .gitref import ZERO, CASConflict, Git, RefUpdate, Refused, short_ref
from .project import (
    Ctx,
    Out,
    UsageError,
    _Parser,
    check_date,
    content_ref,
    content_refs,
    push_refs,
    read_input,
    run,
    utf8_streams,
    write_refs,
)

ATTEMPTS = 3
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Base:
    label: str  # content/<today>, <remote>/content/<today> or main
    commit: str
    tip: Optional[str]  # tip of the local content/<today> (the CAS guard); None: it will be created


def resolve_base(ctx: Ctx) -> Base:
    cref = content_ref(ctx.today)
    tip = ctx.git.rev(cref)
    if tip:
        return Base(short_ref(cref), tip, tip)
    remote = f"{ctx.git.remote}/{short_ref(cref)}"
    tracked = ctx.git.rev(f"refs/remotes/{remote}")
    if tracked:
        return Base(remote, tracked, None)
    main = ctx.git.rev(ctx.main)
    if main:
        return Base(short_ref(ctx.main), main, None)
    raise Refused(f"nothing to read {todo_md.PATH} from: none of {short_ref(cref)}, {remote} and {short_ref(ctx.main)} exists")


def check_project(ctx: Ctx, name: str) -> None:
    """``name`` must be a row of ``projects/INDEX.md`` on main (any status)."""
    main = ctx.git.rev(ctx.main)
    text = ctx.git.read_text(main, index_md.INDEX_PATH) if main else None
    if text is None:
        raise Refused(f"no {index_md.INDEX_PATH} on {short_ref(ctx.main)}; cannot check project {name!r}")
    try:
        names = {row.get("name") for row in index_md.rows(text)}
    except index_md.IndexError_ as exc:
        raise Refused(f"{index_md.INDEX_PATH}: {exc}") from None
    if name not in names:
        raise Refused(f"project {name!r} has no row in {index_md.INDEX_PATH} on {short_ref(ctx.main)}")


def cross_day_guard(ctx: Ctx) -> None:
    """An earlier content/<d> that changed the file and has not landed would conflict at land time."""
    main = ctx.git.rev(ctx.main)
    for ref in content_refs(ctx):
        day = short_ref(ref).split("/", 1)[1]
        tip = ctx.git.rev(ref)
        if not _DAY.match(day) or day >= ctx.today or not tip or not main:
            continue
        mine = ctx.git.read_bytes(tip, todo_md.PATH)
        if mine == ctx.git.read_bytes(main, todo_md.PATH):
            continue
        base = ctx.git.merge_base(main, tip)
        if mine == (ctx.git.read_bytes(base, todo_md.PATH) if base else None):
            continue  # the branch did not change it; landing will not conflict
        raise Refused(f"{short_ref(ref)} changed {todo_md.PATH} and has not landed; run `land content {day}` "
                      "(/exo:content land) first, otherwise landing it later would conflict")


def worktree_guard(ctx: Ctx, base: Base) -> None:
    """Refuse when the checkout's copy has edits that neither HEAD nor the branch holds."""
    top = Path(ctx.git.run("rev-parse", "--show-toplevel").strip())
    git, path = Git(top, remote=ctx.git.remote), top / todo_md.PATH
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Refused(f"{todo_md.PATH} in your checkout is not a regular file")
    mine = git.hash_files(top, [todo_md.PATH])[0] if path.is_file() else None
    head = git.entries("HEAD", [todo_md.PATH]).get(todo_md.PATH) if git.rev("HEAD") else None
    branch = git.entries(base.commit, [todo_md.PATH]).get(todo_md.PATH)
    if mine not in (head[1] if head else None, branch[1] if branch else None):
        raise Refused(
            f"{todo_md.PATH} in your checkout has uncommitted edits; run `/exo:content commit --only {todo_md.PATH}` "
            "first (it passes only while /exo:todo has not written today), or discard the edits and use /exo:todo"
        )


def _convert(fn: Callable[[], todo_md.Outcome]) -> todo_md.Outcome:
    try:
        return fn()
    except todo_md.InputError as exc:
        raise UsageError(str(exc)) from None
    except todo_md.TodoError as exc:
        raise Refused(str(exc)) from None


def write_todo(ctx: Ctx, out: Out, op: str, ticket: str, transform: Callable[[str], todo_md.Outcome]) -> None:
    """Apply ``transform`` to the file on the base and commit the result to content/<today>."""
    cref = content_ref(ctx.today)
    for attempt in range(ATTEMPTS):
        base = resolve_base(ctx)
        cross_day_guard(ctx)
        worktree_guard(ctx, base)
        try:
            current = ctx.git.read_text(base.commit, todo_md.PATH)
        except UnicodeDecodeError:
            raise Refused(f"{todo_md.PATH} on {base.label} is not valid UTF-8") from None
        res = _convert(lambda: transform(todo_md.SKELETON if current is None else current))
        out.data.update(op=op, ticket=ticket, base=base.label, branch=short_ref(cref), commit=None)
        if not res.changed:
            out.result = "noop"
            out.say(f"{op} {ticket}: nothing to change on {base.label}")
            return
        tree = ctx.git.build_tree(base.commit, ctx.git.blob_changes({todo_md.PATH: res.text}))
        commit = ctx.git.commit_tree(tree, [base.commit], f"content: todo {op} {ticket}")
        try:
            write_refs(ctx, [RefUpdate(cref, commit, base.tip or ZERO)], out)
        except CASConflict:
            if attempt + 1 < ATTEMPTS:
                continue  # somebody moved the branch: read the new tip and redo
            raise
        out.data["commit"] = commit
        out.say(f"{short_ref(cref)}: {commit[:10]} content: todo {op} {ticket} (built on {base.label})")
        push_refs(ctx, out, cref)
        return


def _body(path: Optional[str]) -> list[str]:
    if not path:
        return []
    text = read_input(path).replace("\r\n", "\n")
    return text.rstrip("\n").split("\n") if text.strip() else []


def cmd_add(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    if a.due:
        check_date(a.due)
    body = _body(a.body_file)
    if a.project:
        check_project(ctx, a.project)
    write_todo(ctx, out, "add", a.ticket, lambda text: todo_md.add(
        text, ticket=a.ticket, title=a.title, today=ctx.today, summary=a.summary or "", project=a.project,
        status=a.status, due=a.due, body=body))


def cmd_set(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    if a.due:
        check_date(a.due)
    if a.project:
        check_project(ctx, a.project)
    write_todo(ctx, out, "set", a.ticket, lambda text: todo_md.set_fields(
        text, a.ticket, today=ctx.today, status=a.status, project=a.project, due=a.due, note=a.note))


def cmd_done(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    write_todo(ctx, out, "done", a.ticket, lambda text: todo_md.done(
        text, a.ticket, today=ctx.today, dropped=a.dropped, note=a.note or ""))


def cmd_list(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    base = resolve_base(ctx)
    try:
        doc = todo_md.read(ctx.git, base.commit)
    except todo_md.TodoError as exc:
        raise Refused(str(exc)) from None
    todos = doc.todos(project=a.project, status=a.status, include_done=a.all)
    out.data.update(ref=base.label, exists=doc.exists, count=len(todos), warnings=doc.warnings, todos=todos)
    out.say(f"# todos ({base.label}, {len(todos)} item(s)" + ("" if doc.exists else f"; no {todo_md.PATH} yet") + ")")
    for t in todos:
        tag = f"[{t['status']}]" if t["state"] == "open" else f"[{t['state']}]"
        extra = f" (due {t['due']})" if t["due"] else ""
        proj = f" ({t['project']})" if t["project"] else ""
        out.say(f"- {t['ticket'] or '?'} {tag}{proj} {t['title']}" + (f" — {t['summary']}" if t["summary"] else "") + extra)
    for w in doc.warnings:
        out.say(f"warning: {w}")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="workspace repository (default: current directory)")
    common.add_argument("--main", default="main", help="the main branch (default: main)")
    common.add_argument("--remote", default="origin", help="remote to push to (default: origin)")
    common.add_argument("--no-push", action="store_true", help="do not push after writing")
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    common.add_argument("--now", help="override the current time (HH:MM)")

    p = _Parser(prog="exocortex_engine.todo", description="Todos in inbox/todos.md (/exo:todo).")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("version", parents=[common], help="engine and contract version")
    s = sub.add_parser("add", parents=[common], help="capture a todo")
    s.add_argument("--ticket", required=True, help="kebab-case slug, unique across Active and Done")
    s.add_argument("--title", required=True)
    s.add_argument("--summary")
    s.add_argument("--project", help="a name in projects/INDEX.md")
    s.add_argument("--status", choices=todo_md.STATUSES, default=todo_md.DEFAULT_STATUS)
    s.add_argument("--due", metavar="YYYY-MM-DD")
    s.add_argument("--body-file", help="file with the sub-item lines (indented two spaces by the CLI)")
    s = sub.add_parser("list", parents=[common], help="list todos (read only)")
    s.add_argument("--project")
    s.add_argument("--status")
    s.add_argument("--all", action="store_true", help="include Done")
    s = sub.add_parser("set", parents=[common], help="change status, project or due; add a note")
    s.add_argument("ticket")
    s.add_argument("--status", choices=todo_md.STATUSES)
    s.add_argument("--project")
    s.add_argument("--due", metavar="YYYY-MM-DD")
    s.add_argument("--note")
    s = sub.add_parser("done", parents=[common], help="move to Done")
    s.add_argument("ticket")
    s.add_argument("--dropped", action="store_true", help="abandoned; needs --note (the reason)")
    s.add_argument("--note")
    return p


HANDLERS = {"add": cmd_add, "list": cmd_list, "set": cmd_set, "done": cmd_done}


def main(argv: Optional[Sequence[str]] = None) -> int:
    utf8_streams()
    args = build_parser().parse_args(argv)
    return run(args, args.cmd, HANDLERS)


if __name__ == "__main__":
    sys.exit(main())
