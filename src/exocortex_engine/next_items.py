"""``python -m exocortex_engine.next_items``: deterministic half of ``/exo:next``.

Sub-commands: ``version``, ``target [<name>]``, ``show <name>``. Output, ``--json`` and
exit codes are those of :mod:`exocortex_engine.project`; nothing here writes.

``show`` reads three sources through git refs only and returns one inventory: the
ROADMAP (:func:`exocortex_engine.roadmap.read_roadmap`, open tasks), ``## 下一步`` of
``PROJECT.md`` (``project/<name>``, else main) and the project's open todos
(:mod:`exocortex_engine.todo_md` at the base ``/exo:todo`` reads). Each source is ``ok``,
``absent`` or ``failed``. The groups, the merges (a todo whose ticket is a task id, a
next step that names a task id), the counts and the ledger hygiene are decided here; the
command's prose only reads them. The module is not called ``next`` so as not to shadow
the builtin.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import re
import sys
from typing import Any, Optional, Sequence

from . import index_md, project_md, roadmap, todo, todo_md
from .gitref import Refused, short_ref
from .project import Ctx, Out, _Parser, check_name, project_ref, run, utf8_streams

#: Without a ROADMAP, ``/exo:next`` suggests ``/exo:roadmap <name> synthesize`` from this many items.
SYNTHESIZE_AT = 10
GROUP_TODO_UNPLANNED = "待辦（未排入規劃）"
GROUP_NEXT_UNPLANNED = "下一步（未排入 ROADMAP）"
GROUP_NEXT = "下一步"
GROUP_TODO = "待辦"
SOURCES = ("roadmap", "next_step", "todo")
DATED = "📅"

_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
_BOX = re.compile(r"^\[([ xX])\]\s*")
_FENCE = re.compile(r"^(```|~~~)")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


# ---- ## 下一步 ----------------------------------------------------------------------


def next_steps(body: Optional[str]) -> list[dict]:
    """Open top-level items of a ``## 下一步`` body: ``[{title, heading, raw}]``.

    An item is a line at column 0 starting with ``-``, ``*``, ``+``, ``N.`` or ``N)``; the
    indented lines after it are its body (``raw`` keeps them). Left out: checked items
    (``[x]``), struck ones (``~~…``), placeholders (nothing but an HTML comment), and
    anything inside a column-0 ``<details>`` block, fenced code or a multi-line comment.
    ``heading`` is the ``###`` title above the item, if any."""
    out: list[dict] = []
    cur: Optional[dict] = None
    heading: Optional[str] = None
    fence = comment = False
    details = 0

    def close() -> None:
        nonlocal cur
        if cur is not None:
            while cur["lines"] and not cur["lines"][-1].strip():
                cur["lines"].pop()
            if not cur["skip"]:
                out.append({"title": cur["title"], "heading": cur["heading"], "raw": "\n".join(cur["lines"])})
        cur = None

    for line in (body or "").splitlines():
        if fence:
            fence = not _FENCE.match(line)
            continue
        if comment:
            comment = "-->" not in line
            continue
        if details:
            details += len(re.findall(r"<details\b", line, re.I)) - len(re.findall(r"</details>", line, re.I))
            continue
        if cur is not None and (line[:1] in (" ", "\t") or not line.strip()):
            cur["lines"].append(line)
            continue
        close()
        if _FENCE.match(line):
            fence = True
        elif line.startswith("<!--") and "-->" not in line:
            comment = True
        elif re.match(r"<details\b", line, re.I):
            details = max(0, len(re.findall(r"<details\b", line, re.I)) - len(re.findall(r"</details>", line, re.I)))
        elif line.startswith("### "):
            heading = line[4:].strip()
        else:
            m = _ITEM.match(line)
            if m:
                text = m.group(1)
                box = _BOX.match(text)
                done = bool(box and box.group(1) in "xX")
                text = text[box.end():] if box else text
                bare = _COMMENT.sub("", text).strip()
                cur = {"title": bare[:120], "heading": heading, "lines": [line],
                       "skip": done or bare.startswith("~~") or not bare}
    close()
    return out


# ---- reading the sources ---------------------------------------------------------------


def _iso(v: Any) -> Optional[str]:
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, str):
        try:
            return _dt.date.fromisoformat(v).isoformat()
        except ValueError:
            return None
    return None


def _project_md(ctx: Ctx, name: str) -> tuple[str, Optional[str], Optional[str]]:
    """``(state, ref label, text)`` of PROJECT.md on project/<name>, else main."""
    for ref in (project_ref(name), ctx.main):
        if ctx.git.rev(ref) is None:
            continue
        try:
            text = ctx.git.read_text(ref, project_md.project_path(name))
        except UnicodeDecodeError:
            return "failed", short_ref(ref), None
        if text is not None:
            return "ok", short_ref(ref), text
    return "absent", None, None


def _names(ctx: Ctx) -> tuple[list[dict], set[str]]:
    """INDEX rows on main, and the names of every ``project/*`` branch."""
    rows: list[dict] = []
    tip = ctx.git.rev(ctx.main)
    text = ctx.git.read_text(tip, index_md.INDEX_PATH) if tip else None
    if text is not None:
        try:
            rows = [r for r in index_md.rows(text) if r.get("name")]
        except index_md.IndexError_ as exc:
            raise Refused(f"{index_md.INDEX_PATH}: {exc}") from None
    branches = ctx.git.run("for-each-ref", "--format=%(refname)", "refs/heads/project/").split()
    return rows, {b[len("refs/heads/project/"):] for b in branches}


def _require(ctx: Ctx, out: Out, name: str) -> dict:
    rows, branches = _names(ctx)
    row = next((r for r in rows if r["name"] == name), None)
    if row is None and name not in branches:
        known = sorted({r["name"] for r in rows} | branches)
        close = difflib.get_close_matches(name, known, n=3, cutoff=0.5)
        out.data.update(name=None, suggestions=close)
        if close:
            out.say("did you mean: " + ", ".join(close) + "?")
        raise Refused(f"no project {name!r} in {index_md.INDEX_PATH} on {short_ref(ctx.main)} or as project/{name}")
    return row or {"name": name, "status": None}


# ---- the inventory ------------------------------------------------------------------------


def _unique(key: str, taken: set) -> str:
    k, n = key, 2
    while k in taken:
        k, n = f"{key}~{n}", n + 1
    taken.add(k)
    return k


def inventory(ctx: Ctx, name: str) -> dict:
    """Everything ``show --json`` prints besides ``result``, ``command`` and ``messages``."""
    today = ctx.today
    sources: list[dict] = []
    rows: list[dict] = []
    items: list[dict] = []
    keys: set = set()
    by_task: dict[str, dict] = {}
    merged = 0

    # ROADMAP
    rm = roadmap.read_roadmap(ctx.git, name)
    rm_src = {"source": "roadmap", "state": rm.status, "ref": rm.source}
    use_roadmap = rm.status == "ok" and rm.data is not None
    phases, lines, now, groups = [], [], [], []
    if rm.status == "failed":
        rm_src["note"] = f"{len(rm.findings)} finding(s): " + "; ".join(f.describe() for f in rm.findings[:3])
        rm_src["findings"] = [f.as_dict() for f in rm.findings]
    if use_roadmap:
        data = rm.data or {}
        raws = roadmap.item_raw(rm.raw or "")
        lines = [{"id": e.get("id"), "title": e.get("title"), "clearer": e.get("clearer")}
                 for e in data.get("lines") or [] if isinstance(e, dict)]
        clearer = {ln["id"]: ln["clearer"] for ln in lines}
        phases = [{"id": e.get("id"), "title": e.get("title"), "state": e.get("state")}
                  for e in data.get("phases") or [] if isinstance(e, dict)]
        now = [n for n in data.get("now") or [] if isinstance(n, str)]
        tasks = [e for e in data.get("tasks") or [] if isinstance(e, dict)]
        status = {e.get("id"): e.get("status") for e in tasks}
        open_tasks = [e for e in tasks if e.get("status") == "open"]
        by_line: dict[str, int] = {}
        for p in phases:
            mine = [e for e in open_tasks if e.get("phase") == p["id"]]
            # A done phase is no group unless open tasks remain in it (they would vanish otherwise).
            if p["state"] == "done" and not mine:
                continue
            groups.append({"key": f"phase:{p['id']}", "title": p["title"], "kind": "phase", "state": p["state"]})
            for e in mine:
                after = [a for a in e.get("after") or [] if isinstance(a, str)]
                it = {"key": _unique(e["id"], keys), "kind": "task", "title": e.get("title"),
                      "group": f"phase:{p['id']}", "sources": ["roadmap"], "ticket": e["id"], "status": "open",
                      "line": e.get("line"), "clearer": clearer.get(e.get("line")), "phase": p["id"],
                      "after": after, "after_open": [a for a in after if status.get(a) == "open"],
                      "now_rank": now.index(e["id"]) + 1 if e["id"] in now else None,
                      "due": _iso(e.get("due")), "heading": None, "raw": raws.get(e["id"], ""), "also": []}
                items.append(it)
                by_task[e["id"]] = it
                by_line[e.get("line")] = by_line.get(e.get("line"), 0) + 1
        rows.append({"source": "roadmap", "state": "ok", "count": len(by_task),
                     "breakdown": [{"label": ln, "count": n} for ln, n in by_line.items()]})
        not_open = [n for n in now if status.get(n) in ("done", "cancelled")]
        hygiene = [{"rule": "now-not-open", "objects": not_open,
                    "detail": f"now lists task(s) that are done or cancelled; `/exo:roadmap {name} revise`"}] if not_open else []
    else:
        rows.append({"source": "roadmap", "state": rm.status, "count": None, "breakdown": []})
        hygiene = []
    sources.append(rm_src)

    # ## 下一步
    state, label, text = _project_md(ctx, name)
    ns_src: dict = {"source": "next_step", "state": state, "ref": label}
    steps: list[dict] = []
    if text is not None:
        body = project_md.section(text, project_md.SEC_NEXT)
        if body is None:
            ns_src.update(state="absent", note=f"PROJECT.md has no '## {project_md.SEC_NEXT}'")
        else:
            steps = next_steps(body)
    elif state == "failed":
        ns_src["note"] = "PROJECT.md is not valid UTF-8"
    sources.append(ns_src)

    # todos
    td_src: dict = {"source": "todo", "state": "ok"}
    todos: list[dict] = []
    try:
        base = todo.resolve_base(ctx)
        doc = todo_md.read(ctx.git, base.commit)
        td_src["ref"] = base.label
        if not doc.exists:
            td_src.update(state="absent", note=f"no {todo_md.PATH} on {base.label}")
        else:
            todos = doc.todos(project=name)
            if doc.warnings:
                td_src["warnings"] = list(doc.warnings)
    except (Refused, todo_md.TodoError) as exc:
        td_src.update(state="failed", note=str(exc))
    sources.append(td_src)

    unplanned_todo = GROUP_TODO_UNPLANNED if use_roadmap else GROUP_TODO
    unplanned_next = GROUP_NEXT_UNPLANNED if use_roadmap else GROUP_NEXT
    order = (("todo", unplanned_todo), ("next_step", unplanned_next)) if use_roadmap else \
        (("next_step", unplanned_next), ("todo", unplanned_todo))
    groups += [{"key": k, "title": t, "kind": k, "state": None} for k, t in order]

    def fold(task: dict, extra: dict) -> None:
        nonlocal merged
        merged += 1
        if extra["source"] not in task["sources"]:
            task["sources"].append(extra["source"])
        task["also"].append(extra)
        if extra.get("due") and (task["due"] is None or extra["due"] < task["due"]):
            task["due"] = extra["due"]

    for n, s in enumerate(steps, 1):
        hits = [(m.start(), tid) for tid in by_task
                for m in [re.search(rf"(?<![A-Za-z0-9_-]){re.escape(tid)}(?![A-Za-z0-9_-])", s["raw"])] if m]
        if hits:
            fold(by_task[min(hits)[1]], {"source": "next_step", "key": f"next-{n}", "title": s["title"],
                                         "status": None, "due": None, "raw": s["raw"]})
            continue
        items.append({"key": _unique(f"next-{n}", keys), "kind": "next_step", "title": s["title"],
                      "group": "next_step", "sources": ["next_step"], "ticket": None, "status": None,
                      "line": None, "clearer": None, "phase": None, "after": [], "after_open": [],
                      "now_rank": None, "due": None, "heading": s["heading"], "raw": s["raw"], "also": []})
    by_status: dict[str, int] = {}
    for n, t in enumerate(todos, 1):
        by_status[t["status"] or "未標"] = by_status.get(t["status"] or "未標", 0) + 1
        if t["ticket"] and t["ticket"] in by_task:
            fold(by_task[t["ticket"]], {"source": "todo", "key": t["ticket"], "title": t["title"],
                                        "status": t["status"], "due": t["due"], "raw": t["raw"]})
            continue
        items.append({"key": _unique(t["ticket"] or f"todo-{n}", keys), "kind": "todo", "title": t["title"],
                      "group": "todo", "sources": ["todo"], "ticket": t["ticket"], "status": t["status"],
                      "line": None, "clearer": None, "phase": None, "after": [], "after_open": [],
                      "now_rank": None, "due": t["due"], "heading": None, "raw": t["raw"], "also": []})

    rows.append({"source": "next_step", "state": ns_src["state"],
                 "count": len(steps) if ns_src["state"] == "ok" else None, "breakdown": []})
    rows.append({"source": "todo", "state": td_src["state"], "count": len(todos) if td_src["state"] == "ok" else None,
                 "breakdown": [{"label": k, "count": v} for k, v in by_status.items()]})

    # Items in group order; then the per-item facts the prose may not re-judge.
    gkeys = [g["key"] for g in groups]
    items.sort(key=lambda it: gkeys.index(it["group"]))
    for it in items:
        it["origin"] = "+".join(s for s in SOURCES if s in it["sources"])
        it["overdue"] = bool(it["due"] and it["due"] < today)
        it["tag"] = DATED if it["due"] else None
    for g in groups:
        g["items"] = [it["key"] for it in items if it["group"] == g["key"]]
        g["count"] = len(g["items"])
    groups = [g for g in groups if g["count"]]
    overdue = [it["key"] for it in items if it["overdue"]]
    if overdue:
        hygiene.append({"rule": "item-overdue", "objects": overdue,
                        "detail": f"due before {today} and still open"})
    count = len(items)
    assert sum(r["count"] or 0 for r in rows) - merged == count
    return {
        "name": name, "today": today, "has_roadmap": use_roadmap, "roadmap_state": rm.status,
        "count": count, "any_failed": any(s["state"] == "failed" for s in sources), "sources": sources,
        "source_summary": rows, "merged": merged, "hygiene": hygiene, "lines": lines, "phases": phases,
        "now": now, "groups": groups, "items": items, "synthesize_at": SYNTHESIZE_AT,
        "suggest_synthesize": rm.status == "absent" and count >= SYNTHESIZE_AT,
    }


# ---- sub-commands ---------------------------------------------------------------------------


def cmd_target(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    if a.name:
        check_name(a.name)
        row = _require(ctx, out, a.name)
        out.data.update(name=a.name, defaulted=False, status=row.get("status"))
        out.say(f"target: {a.name}")
        return
    rows, _ = _names(ctx)
    active = [r["name"] for r in rows if r.get("status") == "active"]
    out.data.update(active=active)
    if len(active) != 1:
        out.data.update(name=None, defaulted=False)
        for n in active:
            out.say(f"- {n}")
        raise Refused(f"{len(active)} active project(s) in {index_md.INDEX_PATH}; name one")
    out.data.update(name=active[0], defaulted=True, status="active")
    out.say(f"target: {active[0]} (the only active project)")


def cmd_show(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    check_name(a.name)
    _require(ctx, out, a.name)
    inv = inventory(ctx, a.name)
    out.data.update(inv)
    out.say(f"# next: {a.name} ({inv['today']}, {inv['count']} item(s))")
    for s in inv["sources"]:
        out.say(f"source {s['source']}: {s['state']}" + (f" ({s['ref']})" if s.get("ref") else "")
                + (f" — {s['note']}" if s.get("note") else ""))
    for h in inv["hygiene"]:
        out.say(f"hygiene {h['rule']}: {', '.join(h['objects'])} — {h['detail']}")
    for g in inv["groups"]:
        out.say(f"\n## {g['title']} ({g['count']})\n")
        for it in (x for x in inv["items"] if x["group"] == g["key"]):
            due = f" due {it['due']}{' (overdue)' if it['overdue'] else ''}" if it["due"] else ""
            out.say(f"- {it['key']} [{it['origin']}] {it['title']}{due}")
    if inv["suggest_synthesize"]:
        out.say(f"\nno ROADMAP and {inv['count']} item(s): consider /exo:roadmap {a.name} synthesize")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="workspace repository (default: current directory)")
    common.add_argument("--main", default="main", help="the main branch (default: main)")
    common.add_argument("--remote", default="origin", help="remote (default: origin)")
    common.add_argument("--no-push", action="store_true", help="accepted for uniformity; nothing is pushed")
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    common.add_argument("--now", help="override the current time (HH:MM)")

    p = _Parser(prog="exocortex_engine.next_items", description="Open items of one project (/exo:next).")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("version", parents=[common], help="engine and contract version")
    s = sub.add_parser("target", parents=[common], help="resolve the project /exo:next works on")
    s.add_argument("name", nargs="?")
    s = sub.add_parser("show", parents=[common], help="the open items of a project, grouped")
    s.add_argument("name")
    return p


HANDLERS = {"target": cmd_target, "show": cmd_show}


def main(argv: Optional[Sequence[str]] = None) -> int:
    utf8_streams()
    args = build_parser().parse_args(argv)
    return run(args, args.cmd, HANDLERS)


if __name__ == "__main__":
    sys.exit(main())
