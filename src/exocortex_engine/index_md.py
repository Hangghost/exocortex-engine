"""``projects/INDEX.md``: a Markdown table located by column *names*, not positions.

Only the first table whose header carries every required column is the index.
Writes change only the target row's named cells (other cells keep their exact
text and spacing), new rows go at the end of that table, and everything outside
the table (prose, other tables such as a planned/roadmap list) is kept byte for
byte, line endings included.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional

INDEX_PATH = "projects/INDEX.md"
REQUIRED = ("name", "status", "last_updated", "description")
STATUSES = ("active", "paused", "dormant", "completed", "archived", "planned")

DEFAULT_INDEX = (
    "# Projects Index\n\n"
    "| name | status | last_updated | description |\n"
    "|---|---|---|---|\n"
)

_SEP_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")


class IndexError_(ValueError):
    """INDEX.md has no usable table."""


def split_cells(line: str) -> tuple[list[str], bool]:
    """Raw cells of a table line (text between unescaped pipes, spacing kept).

    Returns ``(cells, has_trailing_pipe)``."""
    body = line.rstrip("\r\n")
    stripped = body.strip()
    if not stripped.startswith("|"):
        raise ValueError("not a table line")
    inner = stripped[1:]
    trailing = inner.endswith("|") and not inner.endswith("\\|")
    if trailing:
        inner = inner[:-1]
    cells, cur, i = [], [], 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner) and inner[i + 1] == "|":
            cur.append("\\|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur))
    return cells, trailing


def cell_value(raw: str) -> str:
    return raw.strip().replace("\\|", "|")


def escape_value(value: str) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


@dataclass
class Table:
    lines: list[str]  # whole file, each with its own line ending
    header: int  # line index of the header
    columns: list[str]
    first_row: int  # line index after the separator
    end: int  # one past the last row

    @property
    def newline(self) -> str:
        line = self.lines[self.header]
        return "\r\n" if line.endswith("\r\n") else "\n"

    def col(self, name: str) -> int:
        return self.columns.index(name)

    def row_index(self, name: str) -> Optional[int]:
        c = self.col("name")
        for i in range(self.first_row, self.end):
            cells, _ = split_cells(self.lines[i])
            if c < len(cells) and cell_value(cells[c]) == name:
                return i
        return None

    def rows(self) -> list[dict[str, str]]:
        out = []
        for i in range(self.first_row, self.end):
            cells, _ = split_cells(self.lines[i])
            row = {col: (cell_value(cells[j]) if j < len(cells) else "") for j, col in enumerate(self.columns)}
            out.append(row)
        return out

    def text(self) -> str:
        return "".join(self.lines)


def parse(text: str) -> Table:
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|") or i + 1 >= len(lines):
            continue
        try:
            cols = [cell_value(c) for c in split_cells(line)[0]]
            sep = split_cells(lines[i + 1])[0]
        except ValueError:
            continue
        if not all(r in cols for r in REQUIRED):
            continue
        if not sep or not all(_SEP_CELL.match(c) for c in sep):
            continue
        end = i + 2
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            end += 1
        return Table(lines, i, cols, i + 2, end)
    raise IndexError_(f"no table with columns {', '.join(REQUIRED)}")


def rows(text: str) -> list[dict[str, str]]:
    return parse(text).rows()


def get_row(text: str, name: str) -> Optional[dict[str, str]]:
    for row in rows(text):
        if row.get("name") == name:
            return row
    return None


def get_row_line(text: str, name: str) -> Optional[str]:
    t = parse(text)
    i = t.row_index(name)
    return t.lines[i] if i is not None else None


def _ensure_terminated(t: Table) -> None:
    last = t.end - 1
    if not t.lines[last].endswith(("\n", "\r")):
        t.lines[last] += t.newline


def upsert(text: str, name: str, fields: Mapping[str, str]) -> str:
    """Set ``fields`` on the row for ``name``; append a row when there is none.

    Unknown columns in ``fields`` raise; untouched cells keep their exact text."""
    t = parse(text)
    for key in fields:
        if key not in t.columns:
            raise IndexError_(f"INDEX has no column {key!r}")
    i = t.row_index(name)
    if i is None:
        values = {"name": name, **fields}
        cells = [f" {escape_value(values[c])} " if values.get(c) else " " for c in t.columns]
        _ensure_terminated(t)
        t.lines.insert(t.end, "|" + "|".join(cells) + "|" + t.newline)
        return t.text()
    line = t.lines[i]
    ending = line[len(line.rstrip("\r\n")):] or ""
    cells, trailing = split_cells(line)
    for key, value in fields.items():
        j = t.col(key)
        while len(cells) <= j:
            cells.append(" ")
        cells[j] = f" {escape_value(value)} "
    indent = line[: len(line) - len(line.lstrip())]
    new = indent + "|" + "|".join(cells) + ("|" if trailing or len(cells) > len(split_cells(line)[0]) else "")
    if not ending and i == t.end - 1 and i == len(t.lines) - 1:
        t.lines[i] = new
    else:
        t.lines[i] = new + (ending or t.newline)
    return t.text()


def set_row_line(text: str, name: str, row_line: str) -> str:
    """Replace (or append) the row for ``name`` with ``row_line`` taken verbatim
    from another version of the file. Used when landing a project branch."""
    t = parse(text)
    i = t.row_index(name)
    body = row_line.rstrip("\r\n")
    if i is None:
        _ensure_terminated(t)
        t.lines.insert(t.end, body + t.newline)
    else:
        old = t.lines[i]
        ending = old[len(old.rstrip("\r\n")):]
        t.lines[i] = body + ending
    return t.text()
