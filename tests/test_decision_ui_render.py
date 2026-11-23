"""decision_ui renderer: the four hard constraints — deterministic, no external references,
no I/O, and with ``allow_bulk_adopt=False`` the whole bulk path disappears. Small tests:
no subprocess, no network."""
from __future__ import annotations

import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import _workspace  # noqa: F401  (puts src/ on sys.path)
from exocortex_engine.decision_ui import render_decision_page

ITEMS = [
    {"eid": "1", "label": "ITEM-123", "sublabel": "src: mail",
     "body": "第一行\n第二行", "bucket": "jira", "stars": 12, "sug": "approve",
     "scope": {"issue_key": "ITEM-123"}},
    {"eid": "2", "label": "ITEM-9", "body": "x", "bucket": "mail",
     "stars": 3, "sug": "reject", "scope": {"issue_key": "ITEM-9"}},
]
VOCAB = [
    {"action": "approve", "label": "核准"},
    {"action": "reject", "label": "駁回"},
    {"action": "skip", "label": "跳過", "requires_note": True},
]


def _render(**over):
    kw = dict(kind="test_kind", title="測試頁", id_key="eid",
              generated_on=date(2026, 8, 15), suggested_key="sug",
              filters=[{"key": "bucket", "label": "bucket"},
                       {"key": "stars", "label": "stars", "sort": True}])
    kw.update(over)
    return render_decision_page(ITEMS, VOCAB, **kw)


class RenderTests(unittest.TestCase):
    def test_deterministic_byte_identical(self):
        self.assertEqual(_render(), _render())

    def test_no_external_references(self):
        html = _render()
        # any reference to an external host breaks the page offline / on another machine
        self.assertIsNone(re.search(r'(src|href)\s*=\s*"(https?:)?//', html))
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "cdn.", "<link"):
            self.assertNotIn(forbidden, html, forbidden)

    def test_no_io_performed(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(Path(tmp).iterdir())

            def boom(*a, **k):  # only called when the contract is broken
                raise AssertionError("renderer must not open files")

            with mock.patch("builtins.open", boom):
                _render()
            self.assertEqual(set(Path(tmp).iterdir()), before)

    def test_bulk_adopt_disabled_removes_control_and_js(self):
        html = _render(allow_bulk_adopt=False)
        self.assertNotIn("bulkAdopt", html)
        self.assertNotIn("採用可見項的建議", html)
        self.assertIn("刻意不提供批次套用", html)

    def test_bulk_adopt_enabled_emits_control(self):
        html = _render(allow_bulk_adopt=True)
        self.assertIn("bulkAdopt", html)
        self.assertIn("採用可見項的建議", html)

    def test_bulk_adopt_needs_suggestions(self):
        # without a suggested_key there is nothing to adopt, so no control
        self.assertNotIn("bulkAdopt", _render(allow_bulk_adopt=True, suggested_key=None))

    def test_script_injection_is_neutralised(self):
        items = [{"eid": "x", "label": "</script><script>alert(1)</script>"}]
        html = render_decision_page(items, VOCAB, kind="k", title="t", id_key="eid",
                                    generated_on="2026-08-15")
        self.assertEqual(html.count("</script>"), 1)  # only the page's own closing tag
        self.assertTrue("alert(1)" not in html or "\\u003c" in html)

    def test_localstorage_key_scoped_by_kind_and_date(self):
        self.assertIn("decision_ui:test_kind:2026-08-15", _render())

    def test_missing_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "缺少 id 欄位"):
            render_decision_page([{"label": "no id"}], VOCAB, kind="k", title="t",
                                 id_key="eid", generated_on="2026-08-15")

    def test_empty_vocabulary_rejected(self):
        with self.assertRaisesRegex(ValueError, "vocabulary 不可為空"):
            render_decision_page(ITEMS, [], kind="k", title="t", id_key="eid",
                                 generated_on="2026-08-15")

    def test_empty_kind_rejected(self):
        with self.assertRaisesRegex(ValueError, "kind 不可為空"):
            render_decision_page(ITEMS, VOCAB, kind="", title="t", id_key="eid",
                                 generated_on="2026-08-15")

    def test_scope_is_carried_into_page_data(self):
        self.assertIn('"issue_key":"ITEM-123"', _render())

    def test_body_newlines_survive_as_prewrap(self):
        html = _render()
        self.assertIn("white-space:pre-wrap", html)
        self.assertIn("第一行\\n第二行", html)


if __name__ == "__main__":
    unittest.main()
