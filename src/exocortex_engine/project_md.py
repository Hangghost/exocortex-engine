"""``projects/<name>/PROJECT.md``: frontmatter plus ``## `` sections located by title.

Parsing is deliberately narrow (no YAML library): frontmatter is ``key: value``
lines between ``---`` fences; sections are split at lines starting with ``## ``
outside fenced code blocks. Unknown keys and unknown sections are kept verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .index_md import STATUSES

PLACEHOLDER = "<!-- 待填入 -->"

SEC_GOAL = "目標 / 動機"
SEC_CURRENT = "現況"
SEC_NEXT = "下一步"
SEC_DECISIONS = "關鍵決策（已定案，不再討論）"
SEC_MATERIALS = "材料地圖"
SEC_SOURCES = "資料來源"
SEC_ENV = "環境"

#: Order used by the template for a new project.
SECTIONS = (SEC_GOAL, SEC_CURRENT, SEC_NEXT, SEC_DECISIONS, SEC_MATERIALS, SEC_SOURCES, SEC_ENV)
#: Sections an update must keep. The rest are optional so that older files stay valid.
REQUIRED_SECTIONS = (SEC_GOAL, SEC_CURRENT, SEC_NEXT)
L0_MAX = 120

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def project_path(name: str) -> str:
    return f"projects/{name}/PROJECT.md"


@dataclass
class Frontmatter:
    raw: list[str]  # lines between the fences, endings kept
    start: int  # line index of the first line after the opening fence
    end: int  # line index of the closing fence

    def get(self, key: str) -> Optional[str]:
        for line in self.raw:
            k, sep, v = line.partition(":")
            if sep and k.strip() == key and not line[:1].isspace():
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                return v
        return None


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def frontmatter(text: str) -> Optional[Frontmatter]:
    lines = _lines(text)
    if not lines or lines[0].rstrip("\r\n").lstrip("﻿") != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return Frontmatter(lines[1:i], 1, i)
    return None


def set_frontmatter(text: str, key: str, value: str) -> str:
    """Set ``key`` in the frontmatter, keeping every other line as it is."""
    fm = frontmatter(text)
    if fm is None:
        raise ValueError("no frontmatter")
    lines = _lines(text)
    nl = "\r\n" if lines[0].endswith("\r\n") else "\n"
    for i in range(fm.start, fm.end):
        k, sep, _ = lines[i].partition(":")
        if sep and k.strip() == key and not lines[i][:1].isspace():
            ending = lines[i][len(lines[i].rstrip("\r\n")):] or nl
            lines[i] = f"{key}: {value}{ending}"
            return "".join(lines)
    lines.insert(fm.end, f"{key}: {value}{nl}")
    return "".join(lines)


def sections(text: str) -> list[tuple[str, str]]:
    """``[(title, body)]`` for every ``## `` section; body excludes the heading line."""
    lines = _lines(text)
    fm = frontmatter(text)
    start = fm.end + 1 if fm else 0
    out: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in lines[start:]:
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            out.append((line[3:].strip(), []))
        elif out:
            out[-1][1].append(line)
    return [(t, "".join(b)) for t, b in out]


def section(text: str, title: str) -> Optional[str]:
    for t, body in sections(text):
        if t == title:
            return body
    return None


def is_empty(body: Optional[str]) -> bool:
    if body is None:
        return True
    return body.replace(PLACEHOLDER, "").strip() == ""


def first_actionable(body: Optional[str], limit: int = 80) -> Optional[str]:
    """First line of a section that is an actual item: skips blank lines,
    sub-headings, blockquotes, HTML comments (incl. the placeholder), tables,
    rules and fenced code."""
    if body is None:
        return None
    in_comment = in_fence = False
    for raw in body.splitlines():
        line = raw.strip()
        if in_comment:
            if "-->" in line:
                in_comment = False
                line = line.split("-->", 1)[1].strip()
            else:
                continue
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not line:
            continue
        while line.startswith("<!--"):
            if "-->" in line:
                line = line.split("-->", 1)[1].strip()
            else:
                in_comment = True
                line = ""
        if not line or line.startswith(("#", ">", "|")) or re.fullmatch(r"[-*_]{3,}", line):
            continue
        line = re.sub(r"^(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", line).strip()
        if not line or _is_label(line):
            continue
        return line if len(line) <= limit else line[: limit - 1] + "…"
    return None


def _is_label(line: str) -> bool:
    """A line that only introduces what follows: it ends with a colon, or it is
    nothing but emphasis (``**Phase 2**``). Such lines act as headings."""
    if line.endswith((":", "：")) or line.startswith("~~"):  # label, or struck out (done)
        return True
    return re.fullmatch(r"(\*\*|__)[^*_]+(\*\*|__)", line) is not None


def validate(text: str) -> list[str]:
    """Problems that make ``text`` unacceptable as a PROJECT.md; empty when valid."""
    errors: list[str] = []
    fm = frontmatter(text)
    if fm is None:
        return ["missing frontmatter (--- fenced block at the top)"]
    lu = fm.get("last_updated")
    if not lu or not _DATE.match(lu):
        errors.append("frontmatter last_updated must be YYYY-MM-DD")
    st = fm.get("status")
    if st not in STATUSES:
        errors.append(f"frontmatter status must be one of {', '.join(STATUSES)}")
    l0 = fm.get("l0")
    if not l0:
        errors.append("frontmatter l0 is required")
    elif len(l0) > L0_MAX:
        errors.append(f"frontmatter l0 is {len(l0)} characters (max {L0_MAX})")
    titles = [t for t, _ in sections(text)]
    for req in REQUIRED_SECTIONS:
        if req not in titles:
            errors.append(f"missing section '## {req}'")
    dupes = sorted({t for t in titles if titles.count(t) > 1})
    if dupes:
        errors.append("duplicate sections: " + ", ".join(f"'## {t}'" for t in dupes))
    return errors


def check_update(old: str, new: str) -> list[str]:
    """Problems with replacing PROJECT.md ``old`` by ``new``: :func:`validate`, and no
    ``## `` section may disappear. Every write of a PROJECT.md goes through this."""
    errors = validate(new)
    dropped = dropped_sections(old, new)
    if dropped:
        errors.append("new PROJECT.md drops sections: " + ", ".join(f"'## {t}'" for t in dropped))
    return errors


def dropped_sections(old: str, new: str) -> list[str]:
    """Section titles present in ``old`` but gone from ``new``."""
    new_titles = {t for t, _ in sections(new)}
    return [t for t, _ in sections(old) if t not in new_titles]


def one_line(text: str, limit: int = L0_MAX) -> str:
    line = " ".join(text.strip().splitlines()[0].split()) if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def render_new(title: str, goal: str, today: str, current: str = "", next_: str = "") -> str:
    def body(v: str) -> str:
        v = v.strip()
        return v if v else PLACEHOLDER

    l0 = one_line(f"{title}：{one_line(goal)}")
    parts = [
        "---",
        f"last_updated: {today}",
        "status: active",
        f"l0: {l0}",
        "---",
        "",
        f"# {title.strip()}",
        "",
    ]
    content = {SEC_GOAL: goal, SEC_CURRENT: current, SEC_NEXT: next_}
    for sec in SECTIONS:
        parts += [f"## {sec}", "", body(content.get(sec, "")), ""]
    return "\n".join(parts)
