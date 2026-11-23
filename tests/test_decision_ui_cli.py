"""decision_ui CLI: ``render`` writes exactly what ``render_decision_page`` returns for the
spec's fields, and ``validate`` mirrors :func:`validate`. Small tests: ``main()`` is called
in-process (no subprocess, no network); files live in a temporary directory."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import _workspace  # noqa: F401  (puts src/ on sys.path)
from exocortex_engine.decision_ui import SCHEMA_VERSION, render_decision_page
from exocortex_engine.decision_ui.__main__ import main

SPEC = {
    "kind": "synthetic_queue",
    "title": "測試頁",
    "id_key": "id",
    "generated_on": "2026-11-23",
    "vocabulary": ["keep", {"action": "drop", "label": "丟棄"},
                   {"action": "later", "requires_note": True, "hint": "寫明何時"}],
    "suggested_key": "suggested",
    "filters": ["bucket", {"key": "stars", "label": "stars", "sort": True}],
    "items": [
        {"id": "a1", "label": "第一筆", "sublabel": "來源 X", "body": "行一\n行二",
         "url": "https://example.com/a1", "scope": {"row": 1}, "bucket": "docs",
         "stars": 12, "suggested": "keep"},
        {"id": "a2", "label": "第二筆", "bucket": "mail", "stars": 3, "suggested": "drop"},
    ],
}


def run(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(list(argv))
    return code, buf.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write_json(self, name: str, value) -> str:
        path = self.dir / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_render_output_equals_direct_call(self):
        for variant in ({}, {"allow_bulk_adopt": False}):
            with self.subTest(variant=variant):
                spec = {**SPEC, **variant}
                out = self.dir / "page.html"
                code, text = run("render", self.write_json("spec.json", spec), "--out", str(out))
                self.assertEqual(code, 0, text)
                self.assertTrue(text.startswith("result: ok\n"))
                direct = render_decision_page(
                    spec["items"], spec["vocabulary"], kind=spec["kind"], title=spec["title"],
                    id_key=spec["id_key"], generated_on=spec["generated_on"],
                    suggested_key=spec["suggested_key"], filters=spec["filters"],
                    allow_bulk_adopt=spec.get("allow_bulk_adopt", True))
                self.assertEqual(out.read_bytes(), direct.encode("utf-8"))

    def test_render_rejects_bad_spec_without_writing(self):
        out = self.dir / "page.html"
        bad = {k: v for k, v in SPEC.items() if k != "kind"}
        bad["surprise"] = 1
        code, text = run("render", self.write_json("bad.json", bad), "--out", str(out))
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("result: invalid\n"))
        self.assertIn("missing field 'kind'", text)
        self.assertIn("unknown field 'surprise'", text)
        self.assertFalse(out.exists())

    def test_render_reports_renderer_errors(self):
        spec = {**SPEC, "items": [{"label": "no id"}]}
        code, text = run("render", self.write_json("s.json", spec), "--out", str(self.dir / "p.html"))
        self.assertEqual(code, 1)
        self.assertIn("缺少 id 欄位", text)

    def test_render_unreadable_spec(self):
        code, text = run("render", str(self.dir / "missing.json"), "--out", str(self.dir / "p.html"))
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("result: error\n"))

    def test_validate_ok_and_invalid(self):
        doc = {"schema_version": SCHEMA_VERSION, "kind": "synthetic_queue",
               "generated_at": "2026-11-23T00:00:00Z",
               "decisions": [{"id": "a1", "action": "keep"}, {"id": "a2", "action": "later", "note": "週五"}]}
        path = self.write_json("d.json", doc)
        code, text = run("validate", path, "--kind", "synthetic_queue",
                         "--vocab", "keep,drop,later", "--note-required", "later")
        self.assertEqual((code, text), (0, "result: ok\ndecisions: 2\n"))
        code, text = run("validate", path, "--kind", "another_queue")
        self.assertEqual(code, 1)
        self.assertIn("kind 不符", text)
        doc["decisions"][1]["note"] = None
        code, text = run("validate", self.write_json("d2.json", doc), "--kind", "synthetic_queue",
                         "--note-required", "later")
        self.assertEqual(code, 1)
        self.assertIn("需要非空 note", text)


if __name__ == "__main__":
    unittest.main()
