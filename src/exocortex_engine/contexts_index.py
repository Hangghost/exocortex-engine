"""Per-folder L0 navigation index: ``contexts/<folder>/INDEX.md`` (and friends).

Reads the frontmatter (``date``, ``l0``, ``tags``, ``type``) of every ``*.md`` directly in
``contexts/<folder>/`` and renders one Markdown table, newest first, so that an agent can
find a file from its one-line ``l0`` summary instead of reading files one by one.

- **Recency split**: above a folder's threshold the main ``INDEX.md`` lists only the most
  recent entries and the rest go to ``INDEX_archive.md``. Content files are never moved.
- **Code reviews**: entries with ``type: code-review`` (written by an importer, the
  source of truth lives elsewhere) all go to ``INDEX_reviews.md`` and never take a place
  in the main index.
- **Deterministic**: no timestamps; the same input gives the same bytes.

The frontmatter is read with :mod:`exocortex_engine.yaml_subset`, which returns what
PyYAML's ``safe_load`` returns for the shapes it knows and refuses the rest, so the output
matches indexes written by earlier PyYAML-based generators byte for byte (apart from the
:class:`Style` texts). A frontmatter PyYAML would reject counts as missing, as before.

CLI::

    python -m exocortex_engine.contexts_index <folder>... [--repo DIR]
    python -m exocortex_engine.contexts_index --all [--repo DIR]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional, Sequence

from . import generated_files as _generated
from . import yaml_subset

if TYPE_CHECKING:
    from .gitref import Git

#: Folders indexed by default.
MANAGED_FOLDERS = ("survey_sessions", "work_logs", "thought_review", "blog")
#: Default recency threshold (content files) and per-folder overrides.
SEGMENTATION_THRESHOLD = 150
SEGMENTATION_THRESHOLDS: dict[str, int] = {"survey_sessions": 60}

INDEX_NAME = _generated.INDEX_NAME
ARCHIVE_NAME = _generated.ARCHIVE_NAME
REVIEWS_NAME = _generated.REVIEWS_NAME
GENERATED_FILES = _generated.CONTEXTS_INDEX_FILES
CODE_REVIEW_TYPE = "code-review"
MISSING_L0_MARKER = "⚠️ 缺 l0"

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_WEEK_RE = re.compile(r"(\d{4})-W(\d{2})")


class FrontmatterError(ValueError):
    """A content file's frontmatter uses YAML this engine cannot read."""

    def __init__(self, filename: str, reason: str) -> None:
        self.filename = filename
        self.reason = reason
        super().__init__(f"{filename}: {reason}")


@dataclass(frozen=True)
class Style:
    """The two sentences of the index that name tools."""

    #: How to regenerate; ``{folder}`` is replaced.
    command: str = "python -m exocortex_engine.contexts_index {folder}"
    #: Shown when entries lack ``l0``; ``{missing}`` and ``{marker}`` are replaced.
    missing_note: str = "（其中 {missing} 筆缺 `l0`，標 {marker}；請在各檔 frontmatter 補上 `l0`）"


DEFAULT_STYLE = Style()


@dataclass
class Entry:
    filename: str
    date: str  # "YYYY-MM-DD", "YYYY-Www" or "" (unknown)
    l0: str  # "" when missing
    tags: list[str] = field(default_factory=list)
    type: str = ""


def threshold_for(folder: str) -> int:
    return SEGMENTATION_THRESHOLDS.get(folder, SEGMENTATION_THRESHOLD)


def parse_frontmatter_text(text: str, filename: str = "?") -> dict:
    """The frontmatter mapping, ``{}`` when there is none or YAML would reject it.

    Raises :class:`FrontmatterError` for YAML outside the supported subset. The block is
    cut exactly as before (text between the first two ``---``), so files whose ``l0``
    contains ``---`` are read the same way as by earlier generators."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        fm = yaml_subset.load(parts[1])
    except yaml_subset.Invalid:
        return {}  # shows up as a missing l0 in the index, not as a dropped file
    except yaml_subset.Unsupported as exc:
        raise FrontmatterError(filename, str(exc)) from None
    return fm if isinstance(fm, dict) else {}


def _get(fm: Mapping[str, Any], key: str, filename: str) -> Any:
    try:
        return yaml_subset.resolve(fm.get(key))
    except yaml_subset.Unsupported as exc:
        raise FrontmatterError(filename, f"{key}: {exc}") from None


def _derive_date(raw: Any, filename: str) -> str:
    """Frontmatter ``date`` first; else a ``YYYY-MM-DD`` or ``YYYY-Www`` in the file name."""
    if raw:
        s = str(raw)
        m = _DATE_RE.search(s)
        return m.group(1) if m else s[:10]
    m = _DATE_RE.search(filename)
    if m:
        return m.group(1)
    m = _WEEK_RE.search(filename)
    if m:
        return f"{m.group(1)}-W{m.group(2)}"
    return ""


def _sort_date_key(date: str) -> str:
    """``YYYY-Www`` sorts as the Monday of that ISO week; displayed unchanged."""
    m = _WEEK_RE.fullmatch(date)
    if not m:
        return date
    try:
        return datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1).isoformat()
    except ValueError:
        return date


def _normalize_tags(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def entry(filename: str, text: str) -> Entry:
    fm = parse_frontmatter_text(text, filename)
    return Entry(
        filename=filename,
        date=_derive_date(_get(fm, "date", filename), filename),
        l0=str(_get(fm, "l0", filename) or "").strip().replace("\n", " "),
        tags=_normalize_tags(_get(fm, "tags", filename)),
        type=str(_get(fm, "type", filename) or "").strip(),
    )


def collect_entries_from_texts(files: Iterable[tuple[str, str]]) -> list[Entry]:
    """Entries for ``(basename, text)`` pairs, newest first. Names that are not ``.md``,
    contain ``/`` or are generated files are skipped (like a ``*.md`` glob would)."""
    entries = [
        entry(name, text)
        for name, text in files
        if name.endswith(".md") and "/" not in name and name not in GENERATED_FILES
    ]
    # Dated entries first, newest date first (weeks as their Monday), then file name, descending.
    entries.sort(key=lambda e: (e.date != "", _sort_date_key(e.date), e.filename), reverse=True)
    return entries


def _esc(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ")


def _render_rows(entries: list[Entry]) -> str:
    lines = ["| Date | l0 | Tags | File |", "|------|----|------|------|"]
    for e in entries:
        l0 = _esc(e.l0) if e.l0 else MISSING_L0_MARKER
        tags = _esc(", ".join(e.tags))
        lines.append(f"| {e.date or '—'} | {l0} | {tags} | [{e.filename}]({e.filename}) |")
    return "\n".join(lines)


def render_index(folder: str, entries: list[Entry], *, archived: int = 0, reviews: int = 0,
                 style: Style = DEFAULT_STYLE) -> str:
    missing = sum(1 for e in entries if not e.l0)
    note = style.missing_note.format(missing=missing, marker=MISSING_L0_MARKER) if missing else ""
    header = (
        f"# contexts/{folder}/ — L0 導航索引\n\n"
        f"工具生成（`{style.command.format(folder=folder)}`），請勿手動編輯。\n"
        f"先掃此索引的 `l0` 一行摘要定位檔案，再按需讀全文。\n\n"
        f"共 {len(entries)} 筆{note}。\n"
    )
    if archived:
        header += f"\n較舊 {archived} 筆見 [`INDEX_archive.md`](INDEX_archive.md)。\n"
    if reviews:
        header += f"\n另有 {reviews} 筆 code review 記錄見 [`INDEX_reviews.md`](INDEX_reviews.md)。\n"
    return header + "\n" + _render_rows(entries) + "\n"


def render_archive(folder: str, entries: list[Entry]) -> str:
    header = (
        f"# contexts/{folder}/ — L0 導航索引（archive）\n\n"
        f"工具生成，較舊條目。主索引見 [`INDEX.md`](INDEX.md)。\n\n"
        f"共 {len(entries)} 筆。\n"
    )
    return header + "\n" + _render_rows(entries) + "\n"


def render_reviews(folder: str, entries: list[Entry]) -> str:
    header = (
        f"# contexts/{folder}/ — Code Review 索引\n\n"
        f"工具生成，收錄 `type: code-review` 條目（機械匯入，SSOT 在來源 repo）。"
        f"主索引見 [`INDEX.md`](INDEX.md)。全量列出、不分段。\n\n"
        f"共 {len(entries)} 筆。\n"
    )
    return header + "\n" + _render_rows(entries) + "\n"


def build_index_texts(folder: str, entries: list[Entry], style: Style = DEFAULT_STYLE) -> dict[str, Optional[str]]:
    """``{generated name: text}``; ``None`` means the file must not exist."""
    reviews = [e for e in entries if e.type == CODE_REVIEW_TYPE]
    primary = [e for e in entries if e.type != CODE_REVIEW_TYPE]
    out: dict[str, Optional[str]] = {REVIEWS_NAME: render_reviews(folder, reviews) if reviews else None}
    threshold = threshold_for(folder)
    if len(primary) > threshold:
        older = primary[threshold:]
        out[INDEX_NAME] = render_index(folder, primary[:threshold], archived=len(older), reviews=len(reviews), style=style)
        out[ARCHIVE_NAME] = render_archive(folder, older)
    else:
        out[INDEX_NAME] = render_index(folder, primary, reviews=len(reviews), style=style)
        out[ARCHIVE_NAME] = None
    return out


def index_texts(folder: str, files: Mapping[str, str], style: Style = DEFAULT_STYLE) -> dict[str, Optional[str]]:
    """``build_index_texts`` for ``{basename: text}`` of one folder."""
    return build_index_texts(folder, collect_entries_from_texts(sorted(files.items())), style)


def is_generated_index(folder: str, text: Optional[str]) -> bool:
    """Whether ``text`` looks like an index this module wrote for ``folder``."""
    return text is not None and text.startswith(f"# contexts/{folder}/ — L0 導航索引\n")


# ---- reading a folder ----------------------------------------------------------------


def decode(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def folder_texts_at(git: "Git", rev: str, folder: str) -> dict[str, str]:
    """``{basename: text}`` of the ``*.md`` directly in ``contexts/<folder>/`` at ``rev``
    (one ``cat-file --batch`` for the whole folder)."""
    prefix = f"contexts/{folder}/"
    entries = {
        path[len(prefix):]: sha
        for path, (mode, sha) in git.entries(rev, [prefix]).items()
        if "/" not in path[len(prefix):] and path.endswith(".md") and mode in ("100644", "100755")
    }
    blobs = git.cat_blobs(list(entries.values()))
    return {name: decode(blobs[sha]) for name, sha in entries.items()}


def folder_texts_on_disk(root: Path, folder: str) -> dict[str, str]:
    d = root / "contexts" / folder
    if not d.is_dir():
        return {}
    # newline translation as text mode reads it; YAML does not care either way.
    return {p.name: p.read_text(encoding="utf-8", errors="replace") for p in sorted(d.glob("*.md")) if p.is_file()}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def generate_on_disk(root: Path, folder: str, style: Style = DEFAULT_STYLE) -> dict[str, Any]:
    """Write ``contexts/<folder>/INDEX*.md`` under ``root`` from the files on disk."""
    folder_path = root / "contexts" / folder
    if not folder_path.is_dir():
        return {"folder": folder, "skipped": "not-a-dir", "total": 0}
    files = folder_texts_on_disk(root, folder)
    entries = collect_entries_from_texts(sorted(files.items()))
    texts = build_index_texts(folder, entries, style)
    for name, content in texts.items():
        path = folder_path / name
        if content is not None:
            _atomic_write(path, content)
        elif path.exists():
            path.unlink()
    return {
        "folder": folder,
        "total": len(entries),
        "missing_l0": sum(1 for e in entries if not e.l0),
        "segmented": texts[ARCHIVE_NAME] is not None,
        "reviews": sum(1 for e in entries if e.type == CODE_REVIEW_TYPE),
    }


# ---- CLI ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="exocortex_engine.contexts_index",
                                description="Write contexts/<folder>/INDEX*.md from the files on disk.")
    p.add_argument("folders", nargs="*", help="folders under contexts/")
    p.add_argument("--all", action="store_true", help=f"every default folder ({', '.join(MANAGED_FOLDERS)})")
    p.add_argument("--repo", default=".", help="workspace root (default: current directory)")
    p.add_argument("--json", action="store_true", help="print one JSON object")
    a = p.parse_args(argv)
    if bool(a.folders) == a.all or any("/" in f or "\\" in f or f in ("", ".", "..") for f in a.folders):
        sys.stdout.write("result: usage-error\nusage: contexts_index <folder>... | --all\n")
        return 2
    root = Path(a.repo)
    rows, errors = [], []
    for folder in (MANAGED_FOLDERS if a.all else a.folders):
        try:
            rows.append(generate_on_disk(root, folder))
        except FrontmatterError as exc:
            errors.append(f"contexts/{folder}/{exc}")
    result = "error" if errors else "ok"
    if a.json:
        sys.stdout.write(json.dumps({"result": result, "folders": rows, "errors": errors}, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(f"result: {result}\n")
        for r in rows:
            if r.get("skipped"):
                sys.stdout.write(f"- {r['folder']}: skipped ({r['skipped']})\n")
                continue
            extra = (f", {r['missing_l0']} 缺 l0" if r["missing_l0"] else "") + (" +archive" if r["segmented"] else "")
            sys.stdout.write(f"- {r['folder']}: {r['total']} 筆{extra}\n")
        for e in errors:
            sys.stdout.write(f"error: unsupported frontmatter (not written): {e}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
