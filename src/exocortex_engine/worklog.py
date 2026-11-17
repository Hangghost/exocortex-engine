"""Daily work_log files: ``contexts/work_logs/YYYY-MM-DD_<name>_update.md``.

A second update on the same day appends a ``## HH:MM`` section and rewrites
``l0`` as the summary of the day; earlier sections are never touched.
"""

from __future__ import annotations

import re
from typing import Optional

from . import project_md

WORKLOG_DIR = "contexts/work_logs"
SUBSECTIONS = ("進展", "經驗與觀察", "下一步")
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)_update\.md$")


def worklog_path(date: str, name: str) -> str:
    return f"{WORKLOG_DIR}/{date}_{name}_update.md"


def parse_name(path: str) -> Optional[tuple[str, str]]:
    """``(date, project)`` from a work_log file name, or None."""
    m = _NAME_RE.match(path.rsplit("/", 1)[-1])
    return (m.group(1), m.group(2)) if m else None


def validate_section(body: str) -> list[str]:
    errors = []
    lines = [ln.strip() for ln in body.splitlines()]
    for sub in SUBSECTIONS:
        if f"### {sub}" not in lines:
            errors.append(f"work_log section is missing '### {sub}'")
    if any(ln.startswith("## ") for ln in body.splitlines()):
        errors.append("work_log section must not contain '## ' headings (the CLI adds '## HH:MM')")
    if body.lstrip().startswith("---"):
        errors.append("work_log section must not carry frontmatter")
    return errors


def append(existing: Optional[str], *, name: str, date: str, hhmm: str, body: str, l0: str) -> str:
    """New file text with the ``## HH:MM`` section appended (or a new file)."""
    l0 = project_md.one_line(l0)
    section = f"## {hhmm}\n\n{body.strip()}\n"
    if existing is None:
        return (
            "---\n"
            f"l0: {l0}\n"
            f"project: {name}\n"
            f"date: {date}\n"
            "type: update\n"
            "---\n\n"
            f"# {name} — 進度更新（{date}）\n\n"
            f"{section}"
        )
    text = existing
    if project_md.frontmatter(text) is not None:
        text = project_md.set_frontmatter(text, "l0", l0)
    nl = "\r\n" if "\r\n" in text else "\n"
    if not text.endswith(("\n", "\r")):
        text += nl
    return text + nl + section.replace("\n", nl)
