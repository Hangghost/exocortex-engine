"""``python -m exocortex_engine.roadmap``: deterministic half of ``/exo:roadmap``.

Sub-commands: ``version``, ``validate <name> --file FILE [--new]``,
``diff <name> [--file FILE]``. Output and exit codes follow
:mod:`exocortex_engine.project`; neither sub-command writes anything.

``projects/<name>/ROADMAP.yaml`` lives on ``project/<name>`` and is written with
``/exo:content commit``, which runs :func:`validate_text` on it (see
:mod:`exocortex_engine.content`). The file is read with :mod:`yaml_subset`; text that
reader refuses is a ``parse-failed`` finding with its line, never a guess. Nothing here
serialises YAML: the text the session wrote is what lands, comments and all.

Python entry points for other modules (``/exo:next``): :func:`read_roadmap`,
:func:`load_text`, :func:`validate_text`, :func:`diff`, :func:`item_raw`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from . import yaml_subset
from .gitref import Git, Refused, short_ref
from .project import Ctx, Out, UsageError, _Parser, check_name, project_ref, read_input, run, utf8_streams

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PHASE_STATES = ("done", "now", "later")
TASK_STATUSES = ("open", "done", "cancelled")
#: Top-level lists in file order, the kind each entry gets in a diff, and required fields.
LISTS = {
    "phases": ("phase", ("id", "title", "state")),
    "lines": ("line", ("id", "title", "clearer")),
    "tasks": ("task", ("id", "title", "line", "phase", "status")),
    "ledger": ("ledger", ("id", "title", "line")),
    "decisions": ("decision", ("id", "text")),
    "signals": ("signal", ("id", "date", "text")),
}
REQUIRED_TOP = ("project", "revised", "revised_by", "since", "now", "phases", "lines", "tasks")
OPTIONAL_LISTS = ("ledger", "decisions", "signals")
_SPLIT = re.compile(r"\r\n|\r|\n")
_LINE_NO = re.compile(r"\bline (\d+)")


def roadmap_path(name: str) -> str:
    return f"projects/{name}/ROADMAP.yaml"


@dataclass
class Finding:
    rule: str
    message: str
    id: Optional[str] = None
    line: Optional[int] = None  # 1-based line of the text

    def describe(self) -> str:
        where = f"line {self.line}" if self.line else "line -"
        return f"[{self.rule}] {where}{' ' + self.id if self.id else ''}: {self.message}"

    def as_dict(self) -> dict:
        return {"rule": self.rule, "id": self.id, "line": self.line, "message": self.message}


@dataclass
class Loaded:
    """A ROADMAP read from a ref or a file.

    ``status`` is ``ok`` (parsed and valid), ``absent`` (no file) or ``failed`` (does
    not parse, or parses with findings). ``data`` is the resolved mapping whenever the
    text parsed, also when ``failed`` because of findings."""

    status: str
    source: str
    raw: Optional[str] = None
    data: Optional[dict] = None
    findings: list[Finding] = field(default_factory=list)


# ---- locating entries in the text -------------------------------------------


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_entry(s: str) -> bool:
    return s[:1] == "-" and (len(s) == 1 or s[1] in " \t")


class _Map:
    """Line numbers of the top-level keys and of each block-sequence entry under them."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.keys: dict[str, int] = {}
        self.items: dict[str, list[tuple[int, int]]] = {}  # key -> [(start, end)] 0-based, end exclusive
        key, starts, col = None, [], None
        for i, line in enumerate(lines + [""]):
            s = line.lstrip(" ")
            top = i == len(lines) or (line[:1] not in (" ", "#", "") and not _is_entry(line))
            if top:
                if key is not None:
                    self.items[key] = self._spans(starts, i)
                m = re.match(r"([^\s:#][^:]*):(?:\s|$)", line)
                key = m.group(1).rstrip() if m else None
                if key is not None and key not in self.keys:
                    self.keys[key] = i
                starts, col = [], None
            elif key is not None and s and _is_entry(s) and (col is None or _indent(line) == col):
                col = _indent(line)
                starts.append(i)

    def _spans(self, starts: list[int], stop: int) -> list[tuple[int, int]]:
        out = []
        for k, start in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else stop
            while end > start + 1 and (not self.lines[end - 1].strip() or self.lines[end - 1].lstrip().startswith("#")):
                end -= 1
            out.append((start, end))
        return out

    def line(self, key: str, index: Optional[int] = None, fld: Optional[str] = None) -> Optional[int]:
        """1-based line of ``key``, of its ``index``-th entry, or of ``fld`` in that entry."""
        spans = self.items.get(key, [])
        if index is None or index >= len(spans):
            return self.keys[key] + 1 if key in self.keys else None
        start, end = spans[index]
        if fld:
            first = self.lines[start]
            rest = first[_indent(first) + 1:]
            col = _indent(first) + 1 + len(rest) - len(rest.lstrip(" "))
            for j in range(start, end):
                ln = self.lines[j]
                if j == start:
                    ln = " " * col + ln[col:]
                if _indent(ln) == col and re.match(re.escape(fld) + r":(?:\s|$)", ln[col:]):
                    return j + 1
        return start + 1


# ---- the '#' truncation check (on the text) ---------------------------------


def _close_quote(s: str, pos: int, q: str) -> Optional[int]:
    while pos < len(s):
        ch = s[pos]
        if q == '"' and ch == "\\":
            pos += 2
            continue
        if ch == q:
            if q == "'" and s[pos + 1:pos + 2] == "'":
                pos += 2
                continue
            return pos + 1
        pos += 1
    return None


def hash_truncations(text: str) -> list[Finding]:
    """Unquoted values followed by exactly one space and ``#``: YAML reads ``foo #bar``
    as ``foo`` and drops the rest as a comment. Quoted values and block scalars are
    content and are skipped; a comment two or more spaces after the value is a comment.
    Known blind spots: a continuation line that starts with ``#``, and content two or
    more spaces before a ``#``."""
    out: list[Finding] = []
    block: Optional[int] = None
    quote: Optional[str] = None
    for i, line in enumerate(_SPLIT.split(text)):
        if block is not None:
            if not line.strip() or _indent(line) > block:
                continue
            block = None
        if quote is not None:
            if _close_quote(line, 0, quote) is not None:
                quote = None
            continue
        body = line.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        col = _indent(line)
        while _is_entry(body):
            rest = body[1:].lstrip(" ")
            col += len(body) - len(rest)
            body = rest
        if not body:
            continue
        m = re.match(r"([^\s\"'\[{#][^:]*?):(\s+|$)", body)
        value = body[m.end():] if m else body
        if not value or value.startswith("#"):
            continue
        if value[0] in "|>" and re.fullmatch(r"[|>][-+]?(\s+#.*)?\s*", value):
            block = col
            continue
        if value[0] in "\"'":
            if _close_quote(value, 1, value[0]) is None:
                quote = value[0]
            continue
        if value[0] in "[{":
            continue
        hit = re.search(r"[ \t]#", value)  # the first one starts the comment
        if hit and value[hit.start()] == " " and value[hit.start() - 1] not in " \t":
            kept, lost = value[:hit.start()], value[hit.start() + 1:]
            out.append(Finding(
                "yaml-hash-truncation",
                f"unquoted value followed by ' #': YAML reads {kept[:40]!r} and drops {lost[:40]!r} as a comment; "
                "quote the whole value if it is content, or put two spaces before '#' if it is a comment",
                line=i + 1,
            ))
    return out


# ---- parsing and validation --------------------------------------------------


def _more_indented(lines: list[str], header: int) -> Optional[int]:
    """The first line of the folded scalar opened on line ``header`` that is indented
    deeper than the scalar's first line (yaml_subset names the header only)."""
    base = None
    for j in range(header, len(lines)):
        if not lines[j].strip():
            continue
        if base is None:
            base = _indent(lines[j])
        elif _indent(lines[j]) < base:
            return None
        elif _indent(lines[j]) > base:
            return j + 1
    return None


def _parse(text: str) -> tuple[Optional[dict], list[Finding]]:
    try:
        doc = yaml_subset.load(text)
    except (yaml_subset.Invalid, yaml_subset.Unsupported) as exc:
        msg = str(exc).replace("frontmatter line", "line").replace(" in the frontmatter", "")
        m = _LINE_NO.search(msg)
        line = int(m.group(1)) if m else None
        if line is not None and "more-indented line" in msg:
            line = _more_indented(_SPLIT.split(text), line) or line
        if line is None:
            for j, ln in enumerate(_SPLIT.split(text)):
                if "\t" in ln or "﻿" in ln:
                    line = j + 1
                    break
        msg = re.sub(r"^line \d+: ", "", msg)
        return None, [Finding("parse-failed", f"the YAML reader refused this text: {msg}", line=line)]
    if doc is yaml_subset.NOT_MAPPING:
        return None, [Finding("parse-failed", "the document is not a mapping", line=1)]
    try:
        return yaml_subset.resolve(doc), []
    except yaml_subset.Unsupported as exc:
        return None, [Finding("parse-failed", f"the YAML reader refused a value: {exc}")]


def _is_date(v: Any) -> bool:
    if isinstance(v, _dt.date):
        return True
    if isinstance(v, str):
        try:
            _dt.date.fromisoformat(v)
            return True
        except ValueError:
            return False
    return False


def _missing(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or v == [] or v == {}


def _check(data: dict, name: Optional[str], where: _Map) -> list[Finding]:
    f: list[Finding] = []

    def add(rule: str, msg: str, id_: Optional[str] = None, line: Optional[int] = None) -> None:
        f.append(Finding(rule, msg, id_, line))

    for key in REQUIRED_TOP:
        if key not in data or (data[key] is None and key not in ("now",)):
            add(f"missing-{key.replace('_', '-')}", f"top-level '{key}' is required", line=where.line(key))
    if name is not None and data.get("project") is not None and data.get("project") != name:
        add("project-mismatch", f"project is {data.get('project')!r}, the directory is {name!r}", line=where.line("project"))
    for key in ("revised", "since"):
        if data.get(key) is not None and not _is_date(data[key]):
            add("bad-date", f"{key} must be a YYYY-MM-DD date", line=where.line(key))

    ids: dict[str, str] = {}  # id -> kind
    entries: dict[str, list[tuple[int, dict]]] = {}
    for key, (kind, required) in LISTS.items():
        value = data.get(key)
        if value is None:
            entries[key] = []
            continue
        if not isinstance(value, list):
            add(f"bad-{kind}", f"'{key}' must be a list", line=where.line(key))
            entries[key] = []
            continue
        entries[key] = []
        for k, e in enumerate(value):
            ln = lambda fld=None, k=k, key=key: where.line(key, k, fld)  # noqa: E731
            if not isinstance(e, dict):
                add(f"bad-{kind}", f"{key} entry {k + 1} is not a mapping", line=ln())
                continue
            entries[key].append((k, e))
            eid = e.get("id")
            for fld in required:
                if _missing(e.get(fld)):
                    add(f"missing-{fld.replace('_', '-')}", f"{kind} entry {k + 1} has no '{fld}'",
                        eid if isinstance(eid, str) else None, ln(fld))
            gate = e.get("gate") if kind == "phase" else None
            own = [(eid, ln("id"))]
            if gate is not None:
                if not isinstance(gate, dict):
                    add("bad-gate", "gate must be a mapping", eid, ln("gate"))
                else:
                    own.append((gate.get("id"), ln("gate")))
                    for fld in ("id", "condition"):
                        if _missing(gate.get(fld)):
                            add(f"missing-gate-{fld}", f"the gate of {eid} has no '{fld}'", eid, ln("gate"))
                    if gate.get("evaluated") is not None and not _is_date(gate["evaluated"]):
                        add("bad-gate-evaluated", "gate.evaluated must be null or a date", eid, ln("gate"))
            for oid, oline in own:
                if oid is None:
                    continue
                if not isinstance(oid, str) or not ID_RE.match(oid):
                    add("bad-id", f"id {oid!r} is not kebab-case (a-z, 0-9, '-')", str(oid), oline)
                elif oid in ids:
                    add("duplicate-id", f"id {oid!r} is already used by a {ids[oid]}", oid, oline)
                else:
                    ids[oid] = kind if oid == eid else "gate"
            if kind == "phase" and e.get("state") is not None and e["state"] not in PHASE_STATES:
                add("bad-phase-state", f"state {e['state']!r} is not one of {', '.join(PHASE_STATES)}", eid, ln("state"))
            if kind == "task" and e.get("status") is not None and e["status"] not in TASK_STATUSES:
                add("bad-task-status", f"status {e['status']!r} is not one of {', '.join(TASK_STATUSES)}", eid,
                    ln("status"))
            if kind in ("decision", "signal") and "status" in e:
                add(f"{kind}-has-status", f"{key} entries carry no status (a {kind} is never done)", eid, ln("status"))
            for fld in ("due", "date"):
                if kind in ("task", "signal") and e.get(fld) is not None and not _is_date(e[fld]):
                    add("bad-date", f"{fld} must be a YYYY-MM-DD date", eid, ln(fld))

    kinds = {k: {e.get("id") for _, e in entries[k] if isinstance(e.get("id"), str)} for k in LISTS}
    tasks = kinds["tasks"]

    def refs(key: str, fld: str, targets: set, rule: str, what: str) -> None:
        for k, e in entries[key]:
            vals = e.get(fld)
            if vals is None:
                continue
            for v in vals if isinstance(vals, list) else [vals]:
                if not isinstance(v, str) or v not in targets:
                    add(rule, f"{fld} points to {v!r}, which is not a {what}", e.get("id"), where.line(key, k, fld))

    refs("tasks", "line", kinds["lines"], "dangling-line", "line")
    refs("tasks", "phase", kinds["phases"], "dangling-phase", "phase")
    refs("ledger", "line", kinds["lines"], "dangling-line", "line")
    refs("tasks", "after", tasks, "dangling-after", "task")
    # The schema notes these as task ids; real files also point them at decisions, so
    # (as the contract's rule says) only an id that exists nowhere is refused.
    refs("tasks", "clears", set(ids), "dangling-clears", "known id")
    refs("ledger", "cleared_by", set(ids), "dangling-cleared-by", "known id")
    refs("decisions", "superseded_by", set(ids), "dangling-superseded-by", "known id")

    now = data.get("now")
    if now is not None and not isinstance(now, list):
        add("bad-now", "now must be a list of task ids", line=where.line("now"))
    elif now:
        seen: set = set()
        for k, nid in enumerate(now):
            if not isinstance(nid, str):
                add("bad-now", "now entries must be task ids", line=where.line("now", k))
                continue
            if nid in seen:
                add("now-duplicate", f"now lists {nid!r} twice", nid, where.line("now", k))
            elif nid not in tasks:
                add("now-dangling", f"now points to {nid!r}, which is not a task", nid, where.line("now", k))
            seen.add(nid)

    after: dict[str, tuple[int, list[str]]] = {}
    for k, e in entries["tasks"]:
        links = e.get("after") if isinstance(e.get("after"), list) else []
        if isinstance(e.get("id"), str):
            after[e["id"]] = (k, [a for a in links if isinstance(a, str) and a in tasks])
    state: dict[str, int] = {}

    def visit(t: str, path: list[str]) -> Optional[list[str]]:
        state[t] = 1
        for nxt in after.get(t, (0, []))[1]:
            if state.get(nxt) == 1:
                return path + [t, nxt]
            if nxt not in state:
                cyc = visit(nxt, path + [t])
                if cyc:
                    return cyc
        state[t] = 2
        return None

    for t in after:
        if t not in state:
            cyc = visit(t, [])
            if cyc:
                start = cyc[-1]
                add("after-cycle", "after forms a cycle: " + " -> ".join(cyc[cyc.index(start):]), start,
                    where.line("tasks", after[start][0], "after"))
                break
    return f


def load_text(text: str, name: Optional[str] = None, source: str = "") -> Loaded:
    """Parse and validate ``text`` (the bytes that will land, decoded; a leading BOM is
    ignored, as Windows editors may write one)."""
    text = text[1:] if text.startswith("\ufeff") else text
    lines = _SPLIT.split(text)
    data, findings = _parse(text)
    if data is not None:
        findings.extend(_check(data, name, _Map(lines)))
    findings.extend(hash_truncations(text))
    findings.sort(key=lambda x: (x.line or 0, x.rule))
    return Loaded("failed" if findings else "ok", source, text, data, findings)


def validate_text(text: str, name: Optional[str] = None) -> list[Finding]:
    """Every finding for ``text``; empty means it may land. ``name`` checks ``project``."""
    return load_text(text, name).findings


def read_roadmap(git: Git, name: str, rev: Optional[str] = None) -> Loaded:
    """``projects/<name>/ROADMAP.yaml`` at ``rev`` (default: ``project/<name>``).
    ``absent`` when the ref or the file does not exist."""
    ref = rev or project_ref(name)
    label = short_ref(ref)
    if git.rev(ref) is None:
        return Loaded("absent", label)
    text = git.read_text(ref, roadmap_path(name))
    if text is None:
        return Loaded("absent", label)
    return load_text(text.lstrip("﻿"), name, label)


def item_raw(text: str) -> dict[str, str]:
    """``{id: original text}`` of every entry of the top-level lists (trailing blank and
    comment lines dropped). For ``/exo:next``, which quotes entries as written."""
    lines = _SPLIT.split(text)
    where = _Map(lines)
    out: dict[str, str] = {}
    for key in LISTS:
        for k, (start, end) in enumerate(where.items.get(key, [])):
            at = where.line(key, k, "id")
            m = re.search(r"\bid:\s*(['\"]?)([a-z0-9-]+)\1(?:\s|$)", lines[at - 1]) if at else None
            if m and m.group(2) not in out:
                out[m.group(2)] = "\n".join(lines[start:end])
    return out


# ---- diff ----------------------------------------------------------------------


def _entries(data: Optional[dict]) -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    for key, (kind, _req) in LISTS.items():
        for e in (data or {}).get(key) or []:
            if isinstance(e, dict) and isinstance(e.get("id"), str):
                out.setdefault(e["id"], (kind, e))
                gate = e.get("gate") if kind == "phase" else None
                if isinstance(gate, dict) and isinstance(gate.get("id"), str):
                    out.setdefault(gate["id"], ("gate", gate))
    return out


def _title(e: dict) -> str:
    v = e.get("title", e.get("text", e.get("condition", "")))
    return str(v) if v is not None else ""


def diff(old: Optional[dict], new: Optional[dict]) -> dict:
    """Compare two parsed ROADMAPs by id. ``moved``: a task's ``line`` or ``phase``, or a
    ledger entry's ``line``, changed; ``changed``: any other field. A gate is its own
    entry, so its phase only counts as changed when its other fields differ."""
    a, b = _entries(old), _entries(new)
    item = lambda i, kind, e: {"id": i, "kind": kind, "title": _title(e)}  # noqa: E731
    added = [item(i, *b[i]) for i in b if i not in a]
    removed = [item(i, *a[i]) for i in a if i not in b]
    moved, changed = [], []
    for i in b:
        if i not in a:
            continue
        (ka, ea), (kb, eb) = a[i], b[i]
        strip = (lambda e: {k: v for k, v in e.items() if k != "gate"}) if kb == "phase" else (lambda e: e)
        if ka == kb and kb in ("task", "ledger") and (ea.get("line"), ea.get("phase")) != (eb.get("line"), eb.get("phase")):
            moved.append({"id": i, "kind": kb, "title": _title(eb),
                          "from": {"line": ea.get("line"), "phase": ea.get("phase")},
                          "to": {"line": eb.get("line"), "phase": eb.get("phase")}})
        elif ka != kb or strip(ea) != strip(eb):
            changed.append({"id": i, "kind": kb})
    before = list((old or {}).get("now") or [])
    after = list((new or {}).get("now") or [])
    return {
        "added": added, "removed": removed, "moved": moved, "changed": changed,
        "now": {"before": before, "after": after, "changed": before != after},
        "empty": not (added or removed or moved or changed) and before == after,
    }


def summary(data: dict) -> dict:
    tasks = [e for e in data.get("tasks") or [] if isinstance(e, dict)]
    titles = {e.get("id"): e for e in tasks}
    count = lambda key: len([e for e in data.get(key) or [] if isinstance(e, dict)])  # noqa: E731
    return {
        "phases": count("phases"), "lines": count("lines"), "tasks": len(tasks),
        "open_tasks": len([e for e in tasks if e.get("status") == "open"]),
        "ledger": count("ledger"), "decisions": count("decisions"), "signals": count("signals"),
        "now": [{"id": n, "title": _title(titles.get(n, {})), "status": titles.get(n, {}).get("status")}
                for n in data.get("now") or []],
    }


# ---- sub-commands ----------------------------------------------------------------


def _say_findings(out: Out, findings: Sequence[Finding]) -> None:
    out.data["findings"] = [x.as_dict() for x in findings]
    for x in findings:
        out.say(f"- {x.describe()}")


def cmd_validate(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    check_name(a.name)
    text = read_input(a.file)
    out.data.update(name=a.name, file=a.file, new=bool(a.new))
    pre: list[Finding] = []
    if a.new:
        ref = project_ref(a.name)
        if ctx.git.rev(ref) is None:
            pre.append(Finding("no-project-branch", f"{short_ref(ref)} does not exist; create the project first"))
        elif ctx.git.read_bytes(ref, roadmap_path(a.name)) is not None:
            pre.append(Finding("already-exists", f"{short_ref(ref)} already has {roadmap_path(a.name)}; use revise"))
    loaded = load_text(text, a.name, a.file)
    findings = pre + loaded.findings
    out.data["ok"] = not findings
    out.say(f"# roadmap validate: {a.name} ({a.file})")
    if loaded.data is not None:
        s = summary(loaded.data)
        out.data["summary"] = s
        out.say(f"phases {s['phases']} · lines {s['lines']} · tasks {s['tasks']} (open {s['open_tasks']}) · "
                f"ledger {s['ledger']} · decisions {s['decisions']} · signals {s['signals']}")
        out.say("now: " + (" -> ".join(n["id"] for n in s["now"]) or "(empty)"))
    _say_findings(out, findings)
    if findings:
        raise Refused(f"{len(findings)} finding(s); this ROADMAP must not land")


def cmd_diff(ctx: Ctx, out: Out, a: argparse.Namespace) -> None:
    check_name(a.name)
    if a.file:
        path, text = a.file, read_input(a.file)
    else:
        top = Path(ctx.git.run("rev-parse", "--show-toplevel").strip())
        path = roadmap_path(a.name)
        if not (top / path).is_file():
            raise Refused(f"{path} is not in the working tree; pass --file")
        text = read_input(str(top / path))
    old = read_roadmap(ctx.git, a.name)
    new = load_text(text, a.name, path)
    out.data.update(name=a.name, file=path, old_source=old.source, old_status=old.status,
                    new_status=new.status)
    out.say(f"# roadmap diff: {a.name} ({old.source} -> {path})")
    for label, side in (("old", old), ("new", new)):
        if side.data is None and side.status == "failed":
            out.data[f"{label}_findings"] = [x.as_dict() for x in side.findings]
            for x in side.findings:
                out.say(f"- {label}: {x.describe()}")
            raise Refused(f"the {label} ROADMAP ({side.source}) does not parse; no diff")
    if old.status == "absent":
        out.say(f"note: {old.source} has no {roadmap_path(a.name)}; everything is added")
    if new.findings:
        out.say(f"note: the new version has {len(new.findings)} finding(s); run validate before it lands")
    d = diff(old.data, new.data)
    out.data.update(d)
    if d["empty"]:
        out.result = "noop"
        out.say("no changes")
        return
    for key in ("added", "removed"):
        if d[key]:
            out.say(f"\n## {key} ({len(d[key])})\n")
            out.say("\n".join(f"- {x['id']} [{x['kind']}] {x['title']}" for x in d[key]))
    if d["moved"]:
        out.say(f"\n## moved ({len(d['moved'])})\n")
        for x in d["moved"]:
            f, t = x["from"], x["to"]
            out.say(f"- {x['id']} [{x['kind']}] line {f['line']} -> {t['line']}, phase {f['phase']} -> {t['phase']}")
    if d["changed"]:
        out.say(f"\n## changed ({len(d['changed'])})\n")
        out.say(", ".join(x["id"] for x in d["changed"]))
    if d["now"]["changed"]:
        n = d["now"]
        out.say(f"\nnow: {' -> '.join(n['before']) or '(empty)'}  =>  {' -> '.join(n['after']) or '(empty)'}")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="workspace repository (default: current directory)")
    common.add_argument("--main", default="main", help="the main branch (default: main)")
    common.add_argument("--remote", default="origin", help="remote (default: origin)")
    common.add_argument("--no-push", action="store_true", help="accepted for uniformity; nothing is pushed")
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    common.add_argument("--now", help="override the current time (HH:MM)")

    p = _Parser(prog="exocortex_engine.roadmap", description="Project ROADMAP.yaml checks (/exo:roadmap).")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    sub.add_parser("version", parents=[common], help="engine and contract version")
    s = sub.add_parser("validate", parents=[common], help="validate the text of a ROADMAP.yaml before it lands")
    s.add_argument("name")
    s.add_argument("--file", required=True, help="the ROADMAP.yaml text to check")
    s.add_argument("--new", action="store_true", help="synthesize: also refuse when project/<name> has one")
    s = sub.add_parser("diff", parents=[common], help="compare a ROADMAP.yaml with the one on project/<name>")
    s.add_argument("name")
    s.add_argument("--file", help="the new version (default: projects/<name>/ROADMAP.yaml in the working tree)")
    return p


HANDLERS = {"validate": cmd_validate, "diff": cmd_diff}


def main(argv: Optional[Sequence[str]] = None) -> int:
    utf8_streams()
    args = build_parser().parse_args(argv)
    return run(args, args.cmd, HANDLERS)


if __name__ == "__main__":
    sys.exit(main())
