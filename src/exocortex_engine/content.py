"""``python -m exocortex_engine.content``: deterministic half of ``/exo:content``.

Commits what you wrote in the working tree (normally a checkout of ``main``) to the
branch it belongs to, without switching branches and without touching the working tree:

- ``contexts/**``, ``inbox/**``, ``memory/**`` -> ``content/<today>``
- ``projects/<name>/**`` -> ``project/<name>``; the project's row in ``projects/INDEX.md``
  goes with it
- anything else (rules, tooling, configuration) is listed and left alone

Sub-commands: ``version``, ``status``, ``commit``, ``land [<date>]`` (the same as
``exocortex_engine.project land content``). Output and exit codes follow
:mod:`exocortex_engine.project`.

Every path is checked against the target branch before anything is written: the
branch must not have it, have the version your checkout's HEAD has, or already have
the new content. Any other state means someone changed it on the branch since; the
whole batch is refused and the path named. The navigation index of each touched
``contexts/<folder>/`` is recomputed from the branch plus this batch, in the same commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from . import contexts_index, index_md, project_md, roadmap
from .generated_files import is_contexts_index_path
from .gitref import FILE_MODE, ZERO, Git, RefUpdate, Refused, short_ref
from .project import (
    NAME_RE,
    Ctx,
    Out,
    _Parser,
    cmd_land_content,
    content_ref,
    content_refs,
    project_ref,
    push_refs,
    require_tip,
    run,
    utf8_streams,
    write_refs,
)

CONTENT_ROOTS = ("contexts/", "inbox/", "memory/")
_PROJECT_PATH = re.compile(r"^projects/([^/]+)/.")
_FOLDER_MD = re.compile(r"^contexts/([^/]+)/[^/]+\.md$")

Entry = Optional[tuple[str, str]]  # (mode, blob id) or None


@dataclass
class Item:
    path: str
    kind: str  # content | project | index | generated | other
    op: str  # write | delete
    project: Optional[str] = None
    blob: Optional[str] = None  # clean-filter blob id of the working-tree file (write)
    head: Entry = None  # entry in the checkout's HEAD
    note: str = ""


@dataclass
class Target:
    ref: str
    base: Optional[str]  # commit the new one builds on
    tip: Optional[str]  # current value of ref (None: will be created)
    items: list[Item] = field(default_factory=list)
    changes: dict[str, Optional[tuple[str, str]]] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    noops: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    rejects: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: what this working tree last committed to ``ref``: path (or row key) -> blob id (or row)
    ours: dict[str, str] = field(default_factory=dict)
    #: what to record after a successful write
    record: dict[str, str] = field(default_factory=dict)


@dataclass
class Plan:
    items: list[Item]
    targets: list[Target]
    waiting: dict[str, str]  # path -> branch already holding the working-tree content
    other: list[Item]
    unlanded: list[tuple[str, int]]

    @property
    def rejects(self) -> list[str]:
        return [r for t in self.targets for r in t.rejects]


def classify(path: str) -> tuple[str, Optional[str]]:
    if is_contexts_index_path(path):
        return "generated", None
    if path.startswith(CONTENT_ROOTS):
        return "content", None
    if path == index_md.INDEX_PATH:
        return "index", None
    m = _PROJECT_PATH.match(path)
    if m and NAME_RE.match(m.group(1)):
        return "project", m.group(1)
    return "other", None


def _selected(path: str, only: Sequence[str]) -> bool:
    if not only:
        return True
    for o in only:
        o = o.replace("\\", "/").strip("/")
        if path == o or path.startswith(o + "/"):
            return True
    return False


def scan(git: Git, top: Path, only: Sequence[str], write: bool) -> list[Item]:
    """Dirty paths of the working tree at ``top``, classified, with the blob id each
    file would get from ``git add`` (stored when ``write``)."""
    paths = sorted({p for p in git.dirty_paths(top) if _selected(p, only)})
    head = git.entries("HEAD", paths) if paths and git.rev("HEAD") else {}
    items: list[Item] = []
    files: list[Item] = []
    for path in paths:
        kind, name = classify(path)
        p = top / path
        if p.is_symlink() or (p.exists() and not p.is_file()):
            items.append(Item(path, "other", "write", note="not a regular file"))
            continue
        if p.is_file():
            item = Item(path, kind, "write", name, head=head.get(path))
            files.append(item)
        elif path in head:
            item = Item(path, kind, "delete", name, head=head[path])
        else:
            continue  # added to the index, then removed from disk: nothing to commit
        items.append(item)
    for item, sha in zip(files, git.hash_files(top, [i.path for i in files], write=write)):
        item.blob = sha
    return items


def pending_content(ctx: Ctx) -> dict[str, tuple[str, str]]:
    """``{path: (branch, blob)}`` for every path a local ``content/*`` branch changed
    since it left ``main`` (latest date wins)."""
    result: dict[str, tuple[str, str]] = {}
    main_tip = ctx.git.rev(ctx.main)
    for ref in content_refs(ctx):
        tip = ctx.git.rev(ref)
        base = ctx.git.merge_base(main_tip, tip) if main_tip and tip else None
        if tip is None or base is None:
            continue
        changed = ctx.git.changed_paths(base, tip)
        for path, (_mode, sha) in ctx.git.entries(tip, changed).items():
            result[path] = (short_ref(ref), sha)
    return result


def _check(target: Target, item: Item, entry: Entry) -> Optional[str]:
    """``"change"``, ``"noop"`` or a rejection message for one path."""
    head_sha = item.head[1] if item.head else None
    have = entry[1] if entry else None
    if item.op == "write":
        if have == item.blob:
            return "noop"
        if have is None or have == head_sha or have == target.ours.get(item.path):
            return "change"
    else:
        if have is None:
            return "noop"
        if have == head_sha or have == target.ours.get(item.path):
            return "change"
    return None


def _apply_items(git: Git, target: Target) -> None:
    """Per-path preconditions; fills changes/writes/deletes/noops/rejects."""
    if not target.items:
        return
    entries = git.entries(target.base, [i.path for i in target.items]) if target.base else {}
    for item in target.items:
        entry = entries.get(item.path)
        verdict = _check(target, item, entry)
        if verdict is None:
            target.rejects.append(
                f"{item.path}: {short_ref(target.ref)} has a version that is neither your checkout's HEAD version nor "
                "your working-tree content (changed elsewhere since); not overwriting it"
            )
        elif verdict == "noop":
            target.noops.append(item.path)
        elif item.op == "write":
            mode = item.head[0] if item.head else (entry[0] if entry else FILE_MODE)
            target.changes[item.path] = (mode, item.blob or "")
            target.writes.append(item.path)
            target.record[item.path] = item.blob or ""
        else:
            target.changes[item.path] = None
            target.deletes.append(item.path)


def _texts(git: Git, top: Path, items: Sequence[Item], stored: bool) -> dict[str, str]:
    """Text of each item: from its stored blob, or (status) from the file on disk; the
    frontmatter reads the same either way."""
    items = [i for i in items if i.blob]
    if not stored:
        return {i.path: contexts_index.decode((top / i.path).read_bytes()) for i in items}
    blobs = git.cat_blobs([i.blob for i in items if i.blob])
    return {i.path: contexts_index.decode(blobs[i.blob]) for i in items if i.blob}


def _index_folders(git: Git, target: Target) -> list[str]:
    folders: list[str] = []
    for item in target.items:
        m = _FOLDER_MD.match(item.path)
        if not m or m.group(1) in folders:
            continue
        if contexts_index.folder_is_indexed(git, target.base, m.group(1)):
            folders.append(m.group(1))
    return sorted(folders)


def _regen_index(git: Git, target: Target, texts: dict[str, str], write: bool) -> None:
    """Recompute ``contexts/<folder>/INDEX*.md`` from the branch plus this batch."""
    for folder in _index_folders(git, target):
        prefix = f"contexts/{folder}/"
        direct = lambda p: p.startswith(prefix) and "/" not in p[len(prefix):]  # noqa: E731
        upserts = {p[len(prefix):]: texts[p] for p in target.writes if direct(p)}
        deletes = [p[len(prefix):] for p in target.deletes if p.startswith(prefix)]
        changes, generated, rejects = contexts_index.regen_folder(git, target.base, folder, upserts, deletes, write)
        target.changes.update(changes)
        target.generated.extend(generated)
        target.rejects.extend(rejects)


def _project_md(git: Git, target: Target, name: str, texts: dict[str, str], today: str) -> None:
    path = project_md.project_path(name)
    for item in target.items:
        if item.path != path:
            continue
        if item.op == "delete":
            target.rejects.append(f"{path}: a project's PROJECT.md is not deleted through /exo:content")
            continue
        if item.path not in target.writes:
            continue
        old = git.read_text(target.base, path) if target.base else None
        new = texts[item.path]
        errors = project_md.check_update(old, new) if old is not None else project_md.validate(new)
        target.rejects.extend(f"{path}: {e}" for e in errors)
        fm = project_md.frontmatter(new)
        lu = fm.get("last_updated") if fm else None
        if not errors and lu != today:
            target.notes.append(
                f"{path}: last_updated is {lu}, not today ({today}); committed as written "
                "(the CLI does not rewrite it, so that your copy keeps matching the branch)"
            )


def _roadmap(target: Target, name: str, texts: dict[str, str]) -> None:
    """``projects/<name>/ROADMAP.yaml`` lands only when the roadmap validator passes."""
    path = roadmap.roadmap_path(name)
    if path in target.writes:
        target.rejects.extend(f"{path}: {f.describe()}" for f in roadmap.validate_text(texts[path], name))


def _rider(git: Git, targets: dict[str, Target], index_item: Optional[Item], local: str, notes: list[str],
           write: bool) -> None:
    """Carry each committed project's own row of a locally edited ``projects/INDEX.md``."""
    if index_item is None:
        return
    if index_item.op == "delete" or not index_item.blob:
        notes.append(f"{index_md.INDEX_PATH}: deleted locally; not committed (use /exo:project)")
        return
    head_text = git.read_text("HEAD", index_md.INDEX_PATH) or ""
    try:
        rebuilt = head_text
        for name, target in sorted(targets.items()):
            row_head = index_md.get_row_line(head_text, name) if head_text else None
            row_local = index_md.get_row_line(local, name)
            if _row(row_local) == _row(row_head):
                continue
            if row_local is None:
                notes.append(f"{index_md.INDEX_PATH}: the row for {name} was removed locally; not committed")
                continue
            rebuilt = index_md.set_row_line(rebuilt or index_md.DEFAULT_INDEX, name, row_local)
            branch_text = git.read_text(target.base, index_md.INDEX_PATH) if target.base else None
            row_branch = index_md.get_row_line(branch_text, name) if branch_text else None
            if _row(row_branch) == _row(row_local):
                target.noops.append(f"{index_md.INDEX_PATH} (row {name})")
                continue
            key = f"{index_md.INDEX_PATH}#{name}"
            target.record[key] = _row(row_local) or ""
            if _row(row_branch) not in (_row(row_head), target.ours.get(key)):
                target.rejects.append(
                    f"{index_md.INDEX_PATH}: the row for {name} on {short_ref(target.ref)} was changed elsewhere; "
                    "not overwriting it"
                )
                continue
            merged = index_md.set_row_line(branch_text or index_md.DEFAULT_INDEX, name, row_local)
            target.changes[index_md.INDEX_PATH] = (FILE_MODE, git.hash_blob(merged.encode("utf-8"), write=write))
            target.writes.append(f"{index_md.INDEX_PATH} (row {name})")
    except index_md.IndexError_ as exc:
        notes.append(f"{index_md.INDEX_PATH}: {exc}; not committed")
        return
    if rebuilt != local:
        notes.append(
            f"{index_md.INDEX_PATH}: changes other than the rows of the projects committed here are not "
            "committed; change a project's status with /exo:project"
        )


def _row(line: Optional[str]) -> Optional[str]:
    return line.rstrip("\r\n") if line is not None else None


STATE_FILE = "exocortex/content-commits.json"


def _state_path(git: Git) -> Path:
    common = Path(git.run("rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = git.repo / common
    return common / STATE_FILE


def _worktree_key(git: Git) -> str:
    return git.repo.resolve().as_posix()


def load_state(git: Git) -> dict[str, dict[str, str]]:
    """What this working tree last committed to each branch (``{ref: {path: blob}}``).

    It lets a file be edited and committed again before it lands: the branch then holds
    this working tree's previous version, which is neither HEAD's nor the new one. A
    version anyone else wrote there does not match and is still refused. A missing or
    unreadable record only means such a second commit is refused."""
    try:
        data = json.loads(_state_path(git).read_text(encoding="utf-8"))
        mine = data.get("worktrees", {}).get(_worktree_key(git), {})
        return {ref: dict(paths) for ref, paths in mine.items() if isinstance(paths, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_state(git: Git, records: dict[str, dict[str, str]], fresh: Sequence[str] = ()) -> None:
    """Merge ``records`` into the record; refs in ``fresh`` were just created and start over."""
    path = _state_path(git)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("worktrees"), dict):
            raise ValueError
    except (OSError, ValueError, AttributeError):
        data = {"version": 1, "worktrees": {}}
    mine = data["worktrees"].setdefault(_worktree_key(git), {})
    for ref in [r for r in mine if git.rev(r) is None or r in fresh]:  # landed and deleted, or new
        del mine[ref]
    for ref, paths in records.items():
        mine.setdefault(ref, {}).update(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def make_plan(ctx: Ctx, only: Sequence[str], write: bool) -> Plan:
    git = ctx.git
    top = git.repo
    items = scan(git, top, only, write)
    main_tip = require_tip(ctx, ctx.main, "branch")
    pending = pending_content(ctx)
    cref = content_ref(ctx.today)
    c_tip = git.rev(cref)
    c_base = c_tip or git.rev(f"refs/remotes/{git.remote}/{short_ref(cref)}") or main_tip
    state = load_state(git)
    content = Target(cref, c_base, c_tip, ours=state.get(cref, {}) if c_tip else {})
    projects: dict[str, Target] = {}
    waiting: dict[str, str] = {}
    other: list[Item] = []
    index_item: Optional[Item] = None
    notes: list[str] = []
    for item in items:
        if item.kind == "content":
            held = pending.get(item.path)
            if item.op == "write" and held and held[1] == item.blob and held[0] != short_ref(cref):
                waiting[item.path] = held[0]  # committed on an earlier day, lands with it
                continue
            content.items.append(item)
        elif item.kind == "project":
            assert item.project
            if item.project not in projects:
                pref = project_ref(item.project)
                tip = git.rev(pref)
                projects[item.project] = Target(pref, tip, tip, ours=state.get(pref, {}))
            projects[item.project].items.append(item)
        elif item.kind == "index":
            index_item = item
        else:
            other.append(item)
    targets = ([content] if content.items else []) + [projects[n] for n in sorted(projects)]
    for t in targets:
        if t.base is None:
            name = short_ref(t.ref).split("/", 1)[1]
            t.rejects.append(f"{short_ref(t.ref)} does not exist; create the project first with /exo:project new {name}")
            continue
        _apply_items(git, t)
    texts = _texts(git, top, [i for t in targets for i in t.items if i.path in t.writes], write)
    if content.items and content.base:
        _regen_index(git, content, texts, write)
    for name, t in projects.items():
        if t.base:
            _project_md(git, t, name, texts, ctx.today)
            _roadmap(t, name, texts)
    if index_item is not None:
        local = _texts(git, top, [index_item], write).get(index_item.path, "")
        _rider(git, {n: t for n, t in projects.items() if t.base}, index_item, local, notes, write)
    if index_item is not None and not projects:
        notes.append(f"{index_md.INDEX_PATH}: changed locally; not committed (change project status with /exo:project)")
    by_path = {i.path: i for i in items}
    for t in targets:
        for p in t.noops:
            i = by_path.get(p)
            if i is not None and (i.op == "delete" or i.blob != (i.head[1] if i.head else None)):
                waiting[p] = short_ref(t.ref)
    if targets:
        targets[0].notes.extend(notes)
    elif notes:
        content.notes.extend(notes)
        targets = [content]
    unlanded = []
    for ref in content_refs(ctx):
        tip = git.rev(ref)
        if tip and main_tip and not git.is_ancestor(tip, main_tip):
            n = len(git.run("rev-list", f"{main_tip}..{tip}").split())
            unlanded.append((short_ref(ref), n))
    return Plan(items, targets, waiting, other, unlanded)


def _say_plan(out: Out, plan: Plan, ctx: Ctx) -> None:
    for t in plan.targets:
        where = short_ref(t.ref)
        if t.tip is None and t.base is not None:
            where += f" (new branch from {short_ref(ctx.main)})"
        if not (t.writes or t.deletes or t.generated or t.rejects or t.notes):
            continue
        out.say(f"\n## {where}\n")
        for p in t.writes:
            out.say(f"- write  {p}")
        for p in t.deletes:
            out.say(f"- DELETE {p}  (tracked file missing from your working tree; deleted on {short_ref(t.ref)})")
        for p in t.generated:
            out.say(f"- regen  {p}")
        for r in t.rejects:
            out.say(f"- REFUSED {r}")
        for n in t.notes:
            out.say(f"- note: {n}")
    if plan.waiting:
        out.say("\n## already committed, waiting to land\n")
        for p, b in sorted(plan.waiting.items()):
            out.say(f"- {p} ({b})")
    if plan.other:
        out.say("\n## not handled by /exo:content\n")
        for i in plan.other:
            if i.kind == "generated":
                why = "generated index, recomputed on the branch; discard your local copy before landing"
            else:
                why = i.note or "not content: commit it to main yourself"
            out.say(f"- {i.path} ({why})")
    if plan.unlanded:
        out.say("\n## content branches not landed yet\n")
        for b, n in plan.unlanded:
            out.say(f"- {b} ({n} commit(s))")


def plan_data(plan: Plan) -> dict:
    """The plan as ``content status --json`` prints it (also read by ``day survey``)."""
    return {
        "targets": [
            {
                "branch": short_ref(t.ref), "exists": t.tip is not None, "writes": t.writes, "deletes": t.deletes,
                "generated": t.generated, "noop": t.noops, "rejects": t.rejects, "notes": t.notes,
            }
            for t in plan.targets
        ],
        "waiting": plan.waiting,
        "other": [{"path": i.path, "kind": i.kind} for i in plan.other],
        "unlanded": [{"branch": b, "commits": n} for b, n in plan.unlanded],
    }


def toplevel(ctx: Ctx) -> Ctx:
    """``ctx`` re-rooted at the working tree's top level (``--repo`` may be a sub-directory)."""
    top = Path(ctx.git.run("rev-parse", "--show-toplevel").strip())
    return Ctx(git=Git(top, remote=ctx.git.remote), main=ctx.main, today=ctx.today, now=ctx.now, push=ctx.push)


def cmd_status(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    ctx = toplevel(ctx)
    plan = make_plan(ctx, a.only or (), write=False)
    out.data.update(plan_data(plan))
    todo = sum(len(t.writes) + len(t.deletes) for t in plan.targets)
    if not todo:
        out.result = "noop"
    out.say(f"# /exo:content status (today {ctx.today}): {todo} path(s) to commit, "
            f"{len(plan.waiting)} waiting to land, {len(plan.other)} not handled")
    _say_plan(out, plan, ctx)
    if plan.rejects:
        raise Refused(f"commit would be refused: {len(plan.rejects)} path(s) above are marked REFUSED")


def cmd_commit(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    ctx = toplevel(ctx)
    plan = make_plan(ctx, a.only or (), write=True)
    out.data.update(plan_data(plan))
    if plan.rejects:
        _say_plan(out, plan, ctx)
        raise Refused("nothing was written; " + "; ".join(plan.rejects))
    messages = dict(a.project_message or [])
    updates: list[RefUpdate] = []
    commits: dict[str, str] = {}
    for t in plan.targets:
        if not t.changes or t.base is None:
            continue
        tree = ctx.git.build_tree(t.base, t.changes)
        if tree == ctx.git.tree_of(t.base):
            continue
        n = len(t.writes) + len(t.deletes)
        if t.ref.startswith("refs/heads/project/"):
            name = short_ref(t.ref).split("/", 1)[1]
            msg = messages.get(name) or f"project({name}): {n} file(s)"
        else:
            msg = a.message or f"content: {ctx.today} {n} file(s)"
        c = ctx.git.commit_tree(tree, [t.base], msg)
        commits[t.ref] = c
        updates.append(RefUpdate(t.ref, c, t.tip or ZERO))
    unused = sorted(set(messages) - {short_ref(u.ref).split("/", 1)[1] for u in updates if "/project/" in u.ref})
    if unused:
        out.say("warning: --project-message for project(s) with nothing to commit: " + ", ".join(unused))
    _say_plan(out, plan, ctx)
    if not updates:
        out.result = "noop"
        out.say("\nnothing to commit")
        return
    write_refs(ctx, updates, out)
    try:
        save_state(ctx.git, {t.ref: t.record for t in plan.targets if t.ref in commits},
                   fresh=[t.ref for t in plan.targets if t.ref in commits and t.tip is None])
    except OSError as exc:
        out.say(f"warning: could not record what was committed ({exc}); editing these files again before "
                "landing will be refused until they land")
    out.data["commits"] = {short_ref(r): c for r, c in commits.items()}
    out.say("")
    for r, c in commits.items():
        out.say(f"{short_ref(r)}: {c[:10]} {ctx.git.run('log', '-1', '--format=%s', c).strip()}")
    out.say("your working tree was not changed; these files now match their branch and land with it")
    push_refs(ctx, out, *commits)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="workspace repository (default: current directory)")
    common.add_argument("--main", default="main", help="the main branch (default: main)")
    common.add_argument("--remote", default="origin", help="remote to push to (default: origin)")
    common.add_argument("--no-push", action="store_true", help="do not push after writing")
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    common.add_argument("--now", help="override the current time (HH:MM)")

    p = _Parser(prog="exocortex_engine.content", description="Commit working-tree content (/exo:content).")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("version", parents=[common], help="engine and contract version")
    s = sub.add_parser("status", parents=[common], help="classify local changes, show what commit would do")
    s.add_argument("--only", nargs="+", metavar="PATH", help="only these paths (files or directories)")
    s = sub.add_parser("commit", parents=[common], help="commit content and project files to their branches")
    s.add_argument("--only", nargs="+", metavar="PATH", help="only these paths (files or directories)")
    s.add_argument("--message", "-m", help="message of the content/<today> commit")
    s.add_argument("--project-message", nargs=2, action="append", metavar=("NAME", "MESSAGE"),
                   help="message of the project/<NAME> commit (repeatable)")
    s = sub.add_parser("land", parents=[common], help="land content/<date> on main (= project land content)")
    s.add_argument("date", nargs="?")
    return p


HANDLERS = {"status": cmd_status, "commit": cmd_commit, "land": cmd_land_content}


def main(argv: Optional[Sequence[str]] = None) -> int:
    utf8_streams()
    args = build_parser().parse_args(argv)
    return run(args, args.cmd, HANDLERS)


if __name__ == "__main__":
    sys.exit(main())
