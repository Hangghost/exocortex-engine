"""CLI for the decision page: ``render`` a spec file to a page, ``validate`` a decisions file.

    python -m exocortex_engine.decision_ui render <spec.json> --out <page.html>
    python -m exocortex_engine.decision_ui validate <decisions.json> --kind <kind>
                                              [--vocab a,b,c] [--note-required x,y]

Both are thin wrappers: ``render`` calls :func:`render_decision_page` with the spec's
fields and writes the page as UTF-8 bytes (no newline translation, so the same spec gives
the same bytes on every platform); ``validate`` calls :func:`validate`.

``render`` spec file (UTF-8 JSON object). Required: ``kind``, ``title``, ``id_key``,
``generated_on`` (a string such as ``"2026-11-23"``: the renderer never reads a clock, so
the date is an input), ``vocabulary``, ``items``. Optional: ``suggested_key``, ``filters``,
``allow_bulk_adopt`` (default true)::

    {"kind": "inbox_triage",
     "title": "Inbox triage",
     "id_key": "id",
     "generated_on": "2026-11-23",
     "vocabulary": ["keep", {"action": "drop", "label": "Drop"},
                    {"action": "later", "requires_note": true, "hint": "say when"}],
     "suggested_key": "suggested",
     "filters": ["bucket", {"key": "stars", "label": "stars", "sort": true}],
     "allow_bulk_adopt": false,
     "items": [{"id": "a1", "label": "First", "sublabel": "from X", "body": "text",
                "url": "https://example.com/a1", "scope": {"row": 1},
                "bucket": "docs", "stars": 12, "suggested": "keep"}]}

Vocabulary elements, filters and items follow :mod:`.render` (an unknown key in the spec
is refused, never ignored).

Output: first line ``result: ok|invalid|error``. Exit codes: ``0`` ok, ``1`` the input is
refused (invalid spec, or a decisions file with errors; one error per following line),
``2`` usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .render import render_decision_page
from .schema import validate

_REQUIRED = ("kind", "title", "id_key", "generated_on", "vocabulary", "items")
_OPTIONAL = ("suggested_key", "filters", "allow_bulk_adopt")


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _check_spec(spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return [f"spec must be a JSON object, got {type(spec).__name__}"]
    errors = [f"missing field {k!r}" for k in _REQUIRED if k not in spec]
    errors += [f"unknown field {k!r}" for k in spec if k not in _REQUIRED + _OPTIONAL]
    if isinstance(spec.get("items"), (str, bytes)) or not isinstance(spec.get("items", []), list):
        errors.append("'items' must be a list")
    if isinstance(spec.get("vocabulary"), (str, bytes)) or not isinstance(spec.get("vocabulary", []), list):
        errors.append("'vocabulary' must be a list")
    if "filters" in spec and not isinstance(spec["filters"], list):
        errors.append("'filters' must be a list")
    if "generated_on" in spec and not isinstance(spec["generated_on"], str):
        errors.append("'generated_on' must be a string")
    return errors


def _cmd_render(a: argparse.Namespace) -> int:
    try:
        spec = _load_json(a.spec)
    except (OSError, ValueError) as exc:
        _out("result: error")
        _out(f"cannot read spec {a.spec}: {exc}")
        return 1
    errors = _check_spec(spec)
    if errors:
        _out("result: invalid")
        for e in errors:
            _out(f"- {e}")
        return 1
    try:
        page = render_decision_page(
            spec["items"], spec["vocabulary"],
            kind=spec["kind"], title=spec["title"], id_key=spec["id_key"],
            generated_on=spec["generated_on"],
            suggested_key=spec.get("suggested_key"),
            filters=spec.get("filters", ()),
            allow_bulk_adopt=spec.get("allow_bulk_adopt", True),
        )
    except (ValueError, TypeError, KeyError) as exc:
        _out("result: invalid")
        _out(f"- {type(exc).__name__}: {exc}")
        return 1
    out = Path(a.out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(page.encode("utf-8"))
    except OSError as exc:
        _out("result: error")
        _out(f"cannot write {out}: {exc}")
        return 1
    _out("result: ok")
    _out(f"wrote: {out} ({len(spec['items'])} items)")
    return 0


def _split(value: Optional[str]) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _cmd_validate(a: argparse.Namespace) -> int:
    try:
        doc = _load_json(a.decisions)
    except (OSError, ValueError) as exc:
        _out("result: error")
        _out(f"cannot read decisions file {a.decisions}: {exc}")
        return 1
    vocab = _split(a.vocab) if a.vocab is not None else None
    errors = validate(doc, expected_kind=a.kind, vocabulary=vocab,
                      note_required=_split(a.note_required))
    if errors:
        _out("result: invalid")
        for e in errors:
            _out(f"- {e}")
        return 1
    _out("result: ok")
    n = len(doc["decisions"])
    _out(f"decisions: {n}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="exocortex_engine.decision_ui",
                                description="Render a decision page, or validate a decisions file.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="spec JSON -> single-file HTML page")
    r.add_argument("spec", help="spec JSON file (fields in the module docstring)")
    r.add_argument("--out", required=True, help="where to write the page")
    v = sub.add_parser("validate", help="check a decisions JSON (schema v1)")
    v.add_argument("decisions", help="the exported decisions JSON")
    v.add_argument("--kind", required=True, help="the kind this applier accepts")
    v.add_argument("--vocab", default=None, help="comma-separated allowed actions (omit: not checked)")
    v.add_argument("--note-required", default="", help="comma-separated actions that need a note")
    a = p.parse_args(argv)
    return _cmd_render(a) if a.cmd == "render" else _cmd_validate(a)


if __name__ == "__main__":
    sys.exit(main())
