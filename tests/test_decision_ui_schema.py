"""decisions JSON schema v1 validator. "Any error, write nothing": the applier's fail-fast
duty rests on this layer, so every kind of error must return a non-empty list and a valid
document an empty one."""
from __future__ import annotations

import unittest

import _workspace  # noqa: F401  (puts src/ on sys.path)
from exocortex_engine.decision_ui import SCHEMA_VERSION, validate

VOCAB = ("approve", "reject", "skip")
KIND = "synthetic_queue"


def _doc(**over):
    doc = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "generated_at": "2026-08-15T10:00:00.000Z",
        "decisions": [
            {"id": "1", "scope": {"issue_key": "ITEM-1"}, "action": "approve", "note": None},
            {"id": "2", "scope": {"issue_key": "ITEM-2"}, "action": "reject", "note": None},
        ],
    }
    doc.update(over)
    return doc


class SchemaTests(unittest.TestCase):
    def check(self, doc, **kw):
        return validate(doc, expected_kind=KIND, **kw)

    def test_valid_document_has_no_errors(self):
        self.assertEqual(self.check(_doc(), vocabulary=VOCAB), [])

    def test_kind_mismatch_is_rejected(self):
        errs = self.check(_doc(kind="other_queue"), vocabulary=VOCAB)
        self.assertTrue(any("kind 不符" in e for e in errs))

    def test_unknown_action_is_rejected(self):
        doc = _doc()
        doc["decisions"][1]["action"] = "yolo"
        self.assertTrue(any("未知 action" in e for e in self.check(doc, vocabulary=VOCAB)))

    def test_missing_required_field_is_rejected(self):
        doc = _doc()
        del doc["decisions"][0]["action"]
        self.assertTrue(any("缺少必填欄位" in e for e in self.check(doc, vocabulary=VOCAB)))

    def test_missing_top_level_field_is_rejected(self):
        doc = _doc()
        del doc["kind"]
        self.assertTrue(any("缺少頂層欄位" in e for e in self.check(doc, vocabulary=VOCAB)))

    def test_note_required_action_without_note_is_rejected(self):
        doc = _doc()
        doc["decisions"][0]["action"] = "skip"
        doc["decisions"][0]["note"] = "   "
        errs = self.check(doc, vocabulary=VOCAB, note_required=("skip",))
        self.assertTrue(any("需要非空 note" in e for e in errs))

    def test_note_required_action_with_note_passes(self):
        doc = _doc()
        doc["decisions"][0]["action"] = "skip"
        doc["decisions"][0]["note"] = "等下一輪再看"
        self.assertEqual(self.check(doc, vocabulary=VOCAB, note_required=("skip",)), [])

    def test_schema_version_mismatch_is_rejected(self):
        errs = self.check(_doc(schema_version=2), vocabulary=VOCAB)
        self.assertTrue(any("schema_version 不符" in e for e in errs))

    def test_duplicate_ids_are_rejected(self):
        doc = _doc()
        doc["decisions"][1]["id"] = "1"
        self.assertTrue(any("id 重複" in e for e in self.check(doc, vocabulary=VOCAB)))

    def test_non_object_document_is_rejected(self):
        self.assertTrue(validate([], expected_kind="k"))
        self.assertTrue(validate("nope", expected_kind="k"))

    def test_decisions_not_a_list_is_rejected(self):
        errs = self.check(_doc(decisions="approve"))
        self.assertTrue(any("decisions 必須是 list" in e for e in errs))

    def test_bad_scope_type_is_rejected(self):
        doc = _doc()
        doc["decisions"][0]["scope"] = "ITEM-1"
        self.assertTrue(any("scope 必須是 object" in e for e in self.check(doc, vocabulary=VOCAB)))

    def test_vocabulary_omitted_skips_action_membership_check(self):
        doc = _doc()
        doc["decisions"][0]["action"] = "anything"
        self.assertEqual(self.check(doc), [])


if __name__ == "__main__":
    unittest.main()
