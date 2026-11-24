"""``inbox/todos.md``: parse, render and transform (pure text, no git except :func:`read`).

One file, one entry per ``- [ ]`` line under ``## Active``; finished ones move to
``## Done`` under ``### YYYY-MM-DD``. Tags are the run of ``[...]`` right after the
checkbox: a bare status, ``[project:..]``, ``[ticket:..]``, ``[added:..]``, ``[due:..]``,
``[dropped]``. Tags this module does not know stay as they are. Every transformation takes
the file's text and returns the new text; lines it does not touch stay byte for byte
(CRLF and a BOM are normalized to LF and none when a file is parsed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, NamedTuple, Optional, Sequence

PATH = "inbox/todos.md"
STATUSES = ("needs-triage", "ready", "discuss-first", "needs-analysis")
DEFAULT_STATUS = "needs-triage"
SKELETON = "# Todos\n\n## Active\n\n## Done\n"
TICKET_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_ENTRY = re.compile(r"^- \[([ xX])\](?: (.*))?$")
_TAGS = re.compile(r"(?:\[[^\[\]]*\](?!\())*")
_TAG = re.compile(r"\[([^\[\]]*)\]")
_H2 = re.compile(r"##\s")
_HEADING = re.compile(r"#{1,6}\s")
_ACTIVE = re.compile(r"##\s+Active\s*")
_DONE = re.compile(r"##\s+Done\s*")
_KEYED = ("project", "ticket", "added", "due")


class TodoError(Exception):
    """The file or its state does not allow the operation (the CLI refuses)."""


class InputError(Exception):
    """An argument is not acceptable (the CLI reports a usage error)."""


@dataclass
class Entry:
    start: int  # index of the head line
    end: int  # one past the last line of the block (trailing blank lines included)
    body_end: int  # one past the last non-blank line
    window: str  # active | done
    lines: list[str]  # head line and body, trailing blank lines left out
    mark: str
    rest: str  # head text after the tags
    ticket: Optional[str] = None
    project: Optional[str] = None
    status: Optional[str] = None
    added: Optional[str] = None
    due_raw: Optional[str] = None
    dropped: bool = False
    extra: list[str] = field(default_factory=list)  # tags not understood, in order

    @property
    def line(self) -> int:
        return self.start + 1

    @property
    def raw(self) -> str:
        return "\n".join(self.lines)

    @property
    def state(self) -> str:
        return "open" if self.window == "active" else ("dropped" if self.dropped else "done")

    @property
    def due(self) -> Optional[str]:
        return self.due_raw if _is_date(self.due_raw) else None

    @property
    def title(self) -> str:
        return _title_summary(self.rest)[0]

    @property
    def summary(self) -> str:
        return _title_summary(self.rest)[1]


@dataclass
class Doc:
    lines: list[str]
    newline: bool
    masked: list[bool]
    active: tuple[int, int]  # window: first line after the heading, one past the last
    done: tuple[int, int]
    entries: list[Entry]
    warnings: list[str]
    exists: bool = True

    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.newline and self.lines else "")

    def find(self, ticket: str) -> list[Entry]:
        return [e for e in self.entries if e.ticket == ticket]

    def todos(self, project: Optional[str] = None, status: Optional[str] = None, include_done: bool = False) -> list[dict]:
        """The ``list --json`` items, in file order; ``project`` and ``status`` filter (AND)."""
        out = []
        for e in self.entries:
            if (e.state != "open" and not include_done) or (project and e.project != project):
                continue
            st = e.status if e.state == "open" else None
            if status and st != status:
                continue
            out.append({"ticket": e.ticket, "title": e.title, "project": e.project, "status": st,
                        "summary": e.summary, "added": e.added, "due": e.due, "state": e.state, "raw": e.raw})
        return out


class Outcome(NamedTuple):
    text: str
    changed: bool


def _is_date(value: Optional[str]) -> bool:
    try:
        return value is not None and date.fromisoformat(value) is not None
    except ValueError:
        return False


def _title_summary(rest: str) -> tuple[str, str]:
    """Title (the first ``**...**``, else the text up to `` — ``) and the summary after `` — ``."""
    m = re.search(r"\*\*(.+?)\*\*", rest)
    if m:
        i = rest.find(" — ", m.end())
        return m.group(1).strip(), rest[i + 3:].strip() if i >= 0 else ""
    head, _, tail = rest.partition(" — ")
    return head.strip(), tail.strip()


def _mask(lines: Sequence[str]) -> list[bool]:
    """Lines inside an HTML comment (one that starts a line); they are skipped when parsing."""
    out, inside = [], False
    for line in lines:
        text = line.lstrip()
        if inside:
            out.append(True)
            inside = "-->" not in line
        elif text.startswith("<!--"):
            out.append(True)
            inside = "-->" not in text[4:]
        else:
            out.append(False)
    return out


def _ends_block(line: str, masked: bool) -> bool:
    if masked:
        return not line.startswith((" ", "\t"))
    return line.startswith("- ") or bool(_HEADING.match(line))


def _read_entry(lines: list[str], masked: list[bool], i: int, hi: int, window: str) -> Optional[Entry]:
    m = _ENTRY.match(lines[i])
    if not m:
        return None
    text = m.group(2) or ""
    run = _TAGS.match(text).group(0)
    j = i + 1
    while j < hi and not _ends_block(lines[j], masked[j]):
        j += 1
    b = j
    while b > i + 1 and not lines[b - 1].strip():
        b -= 1
    e = Entry(i, j, b, window, lines[i:b], m.group(1), text[len(run):].lstrip(" "))
    for tag in _TAG.findall(run):
        key, sep, val = tag.partition(":")
        if sep and key in _KEYED and val.strip() and getattr(e, "due_raw" if key == "due" else key) is None:
            setattr(e, "due_raw" if key == "due" else key, val.strip())
        elif not sep and tag == "dropped" and not e.dropped:
            e.dropped = True
        elif not sep and tag.strip() and tag != "dropped" and e.status is None:
            e.status = tag
        else:
            e.extra.append(tag)
    return e


def parse(text: str) -> Doc:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    newline = text.endswith("\n")
    lines = text.split("\n") if text else []
    if newline:
        lines.pop()
    masked = _mask(lines)

    def heading(rx: re.Pattern, what: str) -> int:
        for i, line in enumerate(lines):
            if not masked[i] and rx.fullmatch(line):
                return i
        raise TodoError(f"{PATH} has no '## {what}' heading")

    a, d = heading(_ACTIVE, "Active"), heading(_DONE, "Done")
    if d < a:
        raise TodoError(f"{PATH}: '## Done' must come after '## Active'")

    def window(start: int) -> tuple[int, int]:
        end = next((i for i in range(start + 1, len(lines)) if not masked[i] and _H2.match(lines[i])), len(lines))
        return start + 1, end

    act, don = window(a), window(d)
    entries: list[Entry] = []
    for name, (lo, hi) in (("active", act), ("done", don)):
        i = lo
        while i < hi:
            e = None if masked[i] else _read_entry(lines, masked, i, hi, name)
            if e:
                entries.append(e)
            i = e.end if e else i + 1
    warnings = []
    for e in entries:
        if e.ticket is None:
            warnings.append(f"line {e.line}: entry has no [ticket:...] tag")
        if e.due_raw is not None and e.due is None:
            warnings.append(f"line {e.line}: [due:{e.due_raw}] is not a YYYY-MM-DD date; reported as null")
    return Doc(lines, newline, masked, act, don, entries, warnings)


def read(git: Any, ref: str) -> Doc:
    """Parse ``inbox/todos.md`` as of ``ref``. A missing file reads as the empty skeleton
    (``Doc.exists`` is False). Raises :class:`TodoError` when it cannot be read or parsed."""
    try:
        text = git.read_text(ref, PATH)
    except UnicodeDecodeError:
        raise TodoError(f"{PATH} on {ref} is not valid UTF-8") from None
    doc = parse(SKELETON if text is None else text)
    doc.exists = text is not None
    return doc


# ---- validation ---------------------------------------------------------------------


def check_ticket(ticket: str) -> str:
    if not TICKET_RE.match(ticket or ""):
        raise InputError(f"ticket must be kebab-case (a-z, 0-9, '-'): {ticket!r}")
    return ticket


def check_status(status: str) -> str:
    if status not in STATUSES:
        raise InputError(f"status must be one of {', '.join(STATUSES)}: {status!r}")
    return status


def _single_line(value: str, what: str, banned: str = "") -> str:
    value = (value or "").strip()
    if not value:
        raise InputError(f"{what} must not be empty")
    if any(c in value for c in "\r\n") or (banned and banned in value):
        raise InputError(f"{what} must be one line" + (f" without {banned}" if banned else ""))
    return value


def _check_project(project: Optional[str]) -> Optional[str]:
    if project is not None and not re.fullmatch(r"[^\[\]\s:]+", project):
        raise InputError(f"project must be a bare name without spaces, brackets or ':': {project!r}")
    return project


def _check_due(due: Optional[str]) -> Optional[str]:
    if due is not None and not _is_date(due):
        raise InputError(f"due must be a YYYY-MM-DD date: {due!r}")
    return due


# ---- rendering ----------------------------------------------------------------------


def _tags(e_or_fields: dict) -> str:
    f = e_or_fields
    parts = (["dropped"] if f.get("dropped") else []) + ([f["status"]] if f.get("status") else [])
    for key in _KEYED:
        if f.get(key):
            parts.append(f"{key}:{f[key]}")
    return "".join(f"[{p}]" for p in [*parts, *f.get("extra", [])])


def _head(mark: str, fields: dict, rest: str) -> str:
    tags = _tags(fields)
    return f"- [{mark}] " + " ".join(p for p in (tags, rest) if p)


def _fields(e: Entry, **over: Any) -> dict:
    f = {"dropped": e.dropped, "status": e.status, "project": e.project, "ticket": e.ticket,
         "added": e.added, "due": e.due_raw, "extra": e.extra}
    f.update(over)
    return f


def _splice(lines: list[str], idx: int, block: list[str], lead_blank: bool = False, sep_after: bool = False) -> None:
    """Insert ``block`` before ``lines[idx]``, keeping a blank line between it and a heading."""
    new = list(block)
    prev = lines[idx - 1] if idx > 0 else ""
    if prev.strip() and (lead_blank or _HEADING.match(prev)):
        new.insert(0, "")
    nxt = lines[idx] if idx < len(lines) else ""
    if nxt.strip() and (sep_after or _HEADING.match(nxt)):
        new.append("")
    lines[idx:idx] = new


def _finish(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _entry(doc: Doc, ticket: str, verb: str) -> Entry:
    found = doc.find(ticket)
    if not found:
        raise TodoError(f"no entry with ticket {ticket!r} in {PATH}")
    if len(found) > 1:
        raise TodoError(f"ticket {ticket!r} appears {len(found)} times in {PATH} (lines "
                        + ", ".join(str(e.line) for e in found) + f"); fix the file by hand before {verb}")
    return found[0]


# ---- transformations ----------------------------------------------------------------


def add(text: str, *, ticket: str, title: str, today: str, summary: str = "", project: Optional[str] = None,
        status: str = DEFAULT_STATUS, due: Optional[str] = None, body: Sequence[str] = ()) -> Outcome:
    """Append a new entry at the end of Active. The same entry again is a no-op."""
    check_ticket(ticket)
    check_status(status)
    title = _single_line(title, "title", "**")
    summary = _single_line(summary, "summary") if summary and summary.strip() else ""
    _check_due(due)
    _check_project(project)
    block_body = []
    for line in body:
        if _HEADING.match(line.lstrip()):
            raise InputError(f"body line looks like a heading: {line.strip()!r}")
        block_body.append(("  " + line) if line.strip() else "")
    while block_body and not block_body[-1]:
        block_body.pop()
    rest = f"**{title}**" + (f" — {summary}" if summary else "")
    head = _head(" ", {"status": status, "project": project, "ticket": ticket, "added": today, "due": due}, rest)
    doc = parse(text)
    for e in doc.find(ticket):
        if e.window == "active" and e.lines[0] == head:
            return Outcome(text, False)
        raise TodoError(f"ticket {ticket!r} already exists ({e.state}, line {e.line}): {e.title!r}")
    lo, hi = doc.active
    last = max((i for i in range(lo, hi) if doc.lines[i].strip()), default=lo - 1)
    lines = list(doc.lines)
    _splice(lines, last + 1, [head, *block_body])
    return Outcome(_finish(lines), True)


def set_fields(text: str, ticket: str, *, today: str, status: Optional[str] = None, project: Optional[str] = None,
               due: Optional[str] = None, note: Optional[str] = None) -> Outcome:
    """Change status, project or due of an Active entry; ``note`` appends a sub-item."""
    if status is None and project is None and due is None and note is None:
        raise InputError("give at least one of --status, --project, --due, --note")
    if status is not None:
        check_status(status)
    _check_due(due)
    _check_project(project)
    if note is not None:
        note = _single_line(note, "note")
    doc = parse(text)
    e = _entry(doc, ticket, "setting it")
    if e.window != "active":
        raise TodoError(f"ticket {ticket!r} is in Done (line {e.line}); not changing it")
    over = {k: v for k, v in (("status", status), ("project", project), ("due", due)) if v is not None}
    retag = any(getattr(e, "due_raw" if k == "due" else k) != v for k, v in over.items())
    if not retag and note is None:
        return Outcome(text, False)
    block = list(e.lines)
    if retag:
        block[0] = _head(e.mark, _fields(e, **over), e.rest)
    if note is not None:
        block.append(f"  - {today} 備註：{note}")
    lines = list(doc.lines)
    lines[e.start:e.body_end] = block
    return Outcome(_finish(lines), True)


def done(text: str, ticket: str, *, today: str, dropped: bool = False, note: str = "") -> Outcome:
    """Move an Active entry to Done under ``### today``; already in Done is a no-op."""
    note = (note or "").strip()
    if dropped and not note:
        raise InputError("--dropped needs a non-empty --note (the reason)")
    if note:
        note = _single_line(note, "note")
    doc = parse(text)
    e = _entry(doc, ticket, "closing it")
    if e.window == "done":
        return Outcome(text, False)
    block = [_head("x", _fields(e, status=None, dropped=dropped), e.rest), *e.lines[1:],
             f"  - {'放棄' if dropped else '完成'}：{today}" + (f" {note}" if note else "")]
    lines = list(doc.lines)
    del lines[e.start:e.end]
    if e.start < len(lines) and _HEADING.match(lines[e.start]) and e.start > 0 and lines[e.start - 1].strip():
        lines.insert(e.start, "")
    doc = parse(_finish(lines))  # Done's position moved
    lines, (lo, hi) = list(doc.lines), doc.done
    batch = next((i for i in range(lo, hi) if not doc.masked[i] and re.fullmatch(rf"###\s+{today}\s*", lines[i])), None)
    if batch is not None:
        idx = batch + 1
        while idx < hi and not lines[idx].strip():
            idx += 1
        _splice(lines, idx, block)
    else:
        idx = lo
        while idx < hi and (not lines[idx].strip() or doc.masked[idx]):
            idx += 1
        _splice(lines, idx, [f"### {today}", "", *block], lead_blank=True, sep_after=True)
    return Outcome(_finish(lines), True)
