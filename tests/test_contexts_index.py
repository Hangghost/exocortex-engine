"""contexts_index and its YAML subset, on synthetic files only."""
from __future__ import annotations

import datetime
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _workspace  # noqa: F401  (puts src/ on sys.path)
from _workspace import ROOT
from exocortex_engine import contexts_index as ci
from exocortex_engine import yaml_subset as ys


def fm(block: str) -> dict:
    return ci.parse_frontmatter_text("---\n" + block + "---\nbody\n")


def ent(block: str, name: str = "2026-03-04_note.md") -> ci.Entry:
    return ci.entry(name, "---\n" + block + "---\nbody\n")


class YamlSubsetTests(unittest.TestCase):
    """Expected values are what PyYAML's safe_load returns for the same input."""

    def test_scalars(self):
        cases = {
            "l0: plain text\n": "plain text",
            "l0: 'it''s'\n": "it's",
            'l0: "a \\" b \\t c \\u00e9"\n': 'a " b \t c ' + chr(0xE9),
            "l0: x #comment\n": "x",
            "l0: x#not-a-comment\n": "x#not-a-comment",
            "l0: first\n  second\n\n  third\n": "first second\nthird",
            "l0:\n  on the next line\n": "on the next line",
            "l0: |\n  line1\n  line2\n": "line1\nline2\n",
            "l0: >\n  fold a\n  fold b\n\n  para\n": "fold a fold b\npara\n",
            "l0: |-\n  a\n\n": "a",
            "l0: |+\n  a\n\n\n": "a\n\n\n",
            "l0: |+\n  a": "a",
            "l0: |+\n\n": "\n",
            "l0: >+\n  a\n  b\n\n": "a b\n\n",
            "l0: 'multi\n  line\n\n  q'\n": "multi line\nq",
            "l0: 中文：全形冒號 — ok, really\n": "中文：全形冒號 — ok, really",
            "l0: a:b\n": "a:b",
            "l0: x\r\nother: y\r\n": "x",
        }
        for block, want in cases.items():
            with self.subTest(block=block):
                self.assertEqual(ys.resolve(ys.load(block)["l0"]), want)

    def test_resolution(self):
        cases = {"~": None, "null": None, "": None, "yes": True, "Off": False, "12": 12, "2026-01-02":
                 datetime.date(2026, 1, 2), "2026-W05": "2026-W05", "'12'": "12"}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(ys.resolve(ys.load(f"v: {raw}\n")["v"]), want)

    def test_collections(self):
        doc = ys.resolve(ys.load(
            "tags: [a, 'b c', \"d\", 1]\nblock:\n- x\n- y\nnested:\n  k: v\n  list:\n    - p\n    - q: 1\n      r: 2\n"
            "empty: []\nnone:\n"
        ))
        self.assertEqual(doc, {"tags": ["a", "b c", "d", 1], "block": ["x", "y"],
                               "nested": {"k": "v", "list": ["p", {"q": 1, "r": 2}]}, "empty": [], "none": None})

    def test_not_a_mapping(self):
        for block in ("- a\n- b\n", "just text\n", "\n# only a comment\n", ""):
            self.assertIs(ys.load(block), ys.NOT_MAPPING, block)

    def test_invalid_like_pyyaml(self):
        for block in ("l0: a: b\n", "l0: first\n  bad: colon\n", "l0: \x01\n"):
            with self.subTest(block=block), self.assertRaises(ys.Invalid):
                ys.load(block)

    def test_unsupported_is_never_guessed(self):
        for block in ("l0: &a x\n", "l0: *a\n", "l0: !!str 1\n", "l0: {a: 1}\n", "l0:\tx\n", "l0: |2\n  a\n",
                      "l0: [x\n", '"q": v\n', "l0: x\n...\n", "1: x\n", "l0: @x\n", "l0: x # c\n  y\n"):
            with self.subTest(block=block), self.assertRaises(ys.Unsupported):
                ys.resolve(ys.load(block))
        for value in ("012", "0x1f", "1.5", "2026-01-02 10:00:00"):  # resolved only when asked
            with self.subTest(value=value), self.assertRaises(ys.Unsupported):
                ys.resolve(ys.load(f"v: {value}\n")["v"])


class EntryTests(unittest.TestCase):
    def test_fields(self):
        e = ent("l0: summary\ndate: 2026-01-02\ntags:\n  - x\n  - ' y '\ntype: code-review\n")
        self.assertEqual((e.date, e.l0, e.tags, e.type), ("2026-01-02", "summary", ["x", "y"], "code-review"))
        self.assertEqual(ent("tags: one\nl0: |\n  two\n  lines\n").tags, ["one"])
        self.assertEqual(ent("l0: |\n  two\n  lines\n").l0, "two lines")
        self.assertEqual(ent("l0: yes\n").l0, "True")  # what str() of PyYAML's value gives

    def test_date_fallbacks(self):
        self.assertEqual(ent("l0: x\n").date, "2026-03-04")  # from the file name
        self.assertEqual(ent("l0: x\n", "2026-W05_weekly.md").date, "2026-W05")
        self.assertEqual(ent("l0: x\n", "notes.md").date, "")
        self.assertEqual(ent("date: '2026-05-06 noon'\n").date, "2026-05-06")
        self.assertEqual(ent("date: soon\n").date, "soon")

    def test_unreadable_or_invalid(self):
        self.assertEqual(ent("l0: a: b\n").l0, "")  # PyYAML rejects it too: shown as missing
        self.assertEqual(ci.entry("x.md", "no frontmatter\n").l0, "")
        self.assertEqual(ci.entry("x.md", "---\nl0: never closed\n").l0, "")
        self.assertEqual(ci.entry("x.md", "---\nl0: a --- b\n---\n").l0, "a")  # cut at the first ---
        with self.assertRaises(ci.FrontmatterError) as cm:
            ent("l0: &anchor x\n", "bad.md")
        self.assertEqual(cm.exception.filename, "bad.md")


FILES = {
    "2026-01-05_a.md": "---\nl0: alpha | piped\ndate: 2026-01-05\ntags: [t1, t2]\n---\n",
    "2026-W02_week.md": "---\nl0: weekly\n---\n",
    "2026-01-07_b.md": "---\ndate: 2026-01-07\n---\n",
    "undated.md": "---\nl0: no date\n---\n",
    "2026-01-06_review.md": "---\nl0: a review\ntype: code-review\n---\n",
    "INDEX.md": "# old index, ignored\n",
    "notes.txt": "not markdown\n",
}

GOLDEN = """# contexts/demo/ — L0 導航索引

工具生成（`python -m exocortex_engine.contexts_index demo`），請勿手動編輯。
先掃此索引的 `l0` 一行摘要定位檔案，再按需讀全文。

共 4 筆（其中 1 筆缺 `l0`，標 ⚠️ 缺 l0；請在各檔 frontmatter 補上 `l0`）。

另有 1 筆 code review 記錄見 [`INDEX_reviews.md`](INDEX_reviews.md)。

| Date | l0 | Tags | File |
|------|----|------|------|
| 2026-01-07 | ⚠️ 缺 l0 |  | [2026-01-07_b.md](2026-01-07_b.md) |
| 2026-W02 | weekly |  | [2026-W02_week.md](2026-W02_week.md) |
| 2026-01-05 | alpha \\| piped | t1, t2 | [2026-01-05_a.md](2026-01-05_a.md) |
| — | no date |  | [undated.md](undated.md) |
"""


class RenderTests(unittest.TestCase):
    def test_golden_index(self):
        out = ci.index_texts("demo", FILES)
        self.assertEqual(out[ci.INDEX_NAME], GOLDEN)
        self.assertIsNone(out[ci.ARCHIVE_NAME])
        self.assertIn("| 2026-01-06 | a review |  | [2026-01-06_review.md](2026-01-06_review.md) |", out[ci.REVIEWS_NAME])
        self.assertEqual(ci.index_texts("demo", dict(reversed(list(FILES.items())))), out)  # deterministic

    def test_style_is_configurable(self):
        style = ci.Style(command="tool {folder}", missing_note="（{missing} missing, {marker}）")
        text = ci.index_texts("demo", FILES, style)[ci.INDEX_NAME]
        self.assertIn("工具生成（`tool demo`），請勿手動編輯。", text)
        self.assertIn("共 4 筆（1 missing, ⚠️ 缺 l0）。", text)

    def test_segmentation(self):
        files = {f"2026-01-{d:02d}_n.md": f"---\nl0: n{d}\n---\n" for d in range(1, 8)}
        old = dict(ci.SEGMENTATION_THRESHOLDS)
        ci.SEGMENTATION_THRESHOLDS["demo"] = 5
        try:
            out = ci.index_texts("demo", files)
        finally:
            ci.SEGMENTATION_THRESHOLDS.clear()
            ci.SEGMENTATION_THRESHOLDS.update(old)
        self.assertIn("共 5 筆。", out[ci.INDEX_NAME])
        self.assertIn("較舊 2 筆見 [`INDEX_archive.md`](INDEX_archive.md)。", out[ci.INDEX_NAME])
        self.assertIn("[2026-01-01_n.md]", out[ci.ARCHIVE_NAME])
        self.assertNotIn("[2026-01-01_n.md]", out[ci.INDEX_NAME])
        self.assertIsNone(out[ci.REVIEWS_NAME])
        self.assertEqual(ci.threshold_for("survey_sessions"), 60)
        self.assertEqual(ci.threshold_for("anything"), 150)


class CliTests(unittest.TestCase):
    def run_cli(self, root: Path, *args: str):
        env = dict(_workspace.os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        return subprocess.run([sys.executable, "-m", "exocortex_engine.contexts_index", *args, "--repo", str(root)],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    def test_writes_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "contexts" / "demo"
            d.mkdir(parents=True)
            for name, text in FILES.items():
                (d / name).write_text(text, encoding="utf-8")
            (d / ci.ARCHIVE_NAME).write_text("stale\n", encoding="utf-8")
            p = self.run_cli(root, "demo")
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertEqual(p.stdout.splitlines()[0], "result: ok")
            self.assertEqual((d / ci.INDEX_NAME).read_bytes(), GOLDEN.encode("utf-8"))  # LF on every platform
            self.assertFalse((d / ci.ARCHIVE_NAME).exists())  # no longer needed
            self.assertTrue((d / ci.REVIEWS_NAME).exists())
            (d / "2026-02-01_bad.md").write_text("---\nl0: *alias\n---\n", encoding="utf-8")
            p = self.run_cli(root, "demo")
            self.assertEqual(p.returncode, 1)
            self.assertIn("2026-02-01_bad.md", p.stdout)
            self.assertEqual(self.run_cli(root, "--all").returncode, 0)  # default folders absent: skipped
            self.assertEqual(self.run_cli(root, "../x").returncode, 2)


if __name__ == "__main__":
    unittest.main()
