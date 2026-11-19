"""/exo:discuss: its only write is ``content commit --only <record>``; the skill prose stays
self-contained for a public repository. Synthetic workspaces only, no network."""
from __future__ import annotations

import re
import unittest

from _workspace import ROOT
from test_content_cli import CREF, DAY, ContentCase

SKILL = ROOT / "plugins/exo/skills/discuss/SKILL.md"
NEW = f"contexts/thought_review/{DAY}_synthetic-topic.md"
OLD = "contexts/thought_review/2026-03-01_earlier-topic.md"
T_INDEX = "contexts/thought_review/INDEX.md"


def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def record_from_template(date: str, topic: str, status: str = "handed-off", extra: str = "") -> str:
    """The record template in SKILL.md, filled the way the prose says: comments dropped,
    placeholders replaced."""
    block = re.search(r"```markdown\n(---\n.*?)```", skill_text(), re.S).group(1)
    lines = []
    for line in block.splitlines():
        line = re.sub(r"\s+#\s.*$", "", line)  # "the # comments are explanations, do not keep them"
        if re.match(r"(resumes|superseded_by|project):", line):
            continue  # optional keys
        lines.append(line)
    text = "\n".join(lines) + "\n"
    text = re.sub(r"^date: .*$", f"date: {date}", text, count=1, flags=re.M)
    text = re.sub(r"^status: .*$", f"status: {status}", text, count=1, flags=re.M)
    text = text.replace("tags: [...]", "tags: [discussion, synthetic]")
    text = text.replace("<一句話議題>", topic, 1).replace("<topic>", topic)
    text = text.replace("<一行結論：決定了什麼、還卡什麼>", f"decided {topic}", 1)
    if extra:
        text = text.replace("\n---\n", f"\n{extra}---\n", 1)
    return re.sub(r"<[^<>\n]+>", "synthetic", text)


class RecordWrite(ContentCase):
    def test_template_frontmatter_is_one_the_index_reader_accepts(self):
        rec = record_from_template(DAY, "synthetic topic")
        self.assertIn("handoff:\n  - to: project\n", rec)
        self.write(NEW, rec)
        self.write("inbox/unrelated.md", "not part of the discussion\n")
        before = self.read_bytes(NEW)
        p = self.ok("commit", "--only", NEW, "-m", "docs: add discussion record on synthetic topic")
        self.assertEqual(p.first, "result: ok", p.stdout)
        self.assertEqual(self.show(CREF, NEW), rec)
        self.assertIn("decided synthetic topic", self.show(CREF, T_INDEX) or "")
        self.assertIsNone(self.show(CREF, "inbox/unrelated.md"), "--only must leave other changes out")
        self.assertEqual(self.read_bytes(NEW), before, "the working tree is not changed")
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertEqual(self.rev(f"refs/heads/{CREF}"), self.rev(CREF, self.remote))

    def test_resume_supersedes_the_old_record_in_the_same_commit(self):
        self.write(OLD, record_from_template("2026-03-01", "earlier topic", status="open"))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed open record")
        self.git("push", "-q", "origin", "main")
        old = record_from_template("2026-03-01", "earlier topic", status="superseded",
                                   extra=f"superseded_by: {NEW}\n")
        self.write(OLD, old)
        self.write(NEW, record_from_template(DAY, "synthetic topic", extra=f"resumes: {OLD}\n"))
        self.ok("commit", "--only", NEW, OLD, "-m", "docs: resume discussion")
        self.assertEqual(self.show(CREF, OLD), old)
        changed = self.git("diff", "--name-only", f"{CREF}~1", CREF).split()
        self.assertEqual(sorted(changed), sorted([NEW, OLD, T_INDEX]))
        # Edited again before landing: this working tree's last commit is accepted.
        self.write(NEW, record_from_template(DAY, "synthetic topic, revised"))
        self.ok("commit", "--only", NEW)

    def test_a_different_version_on_the_branch_is_refused_with_zero_writes(self):
        self.write(NEW, record_from_template(DAY, "someone else"))
        self.ok("commit", "--only", NEW)
        # Without this working tree's record, the branch's version counts as someone else's.
        (self.ws / ".git/exocortex/content-commits.json").unlink()
        self.write(NEW, record_from_template(DAY, "mine"))
        before = self.snapshot()
        self.zero_writes(self.ccli("commit", "--only", NEW), *before)


class SkillProse(unittest.TestCase):
    """What would make the prose wrong in a public engine, checked mechanically."""

    def test_only_engine_commands_are_named(self):
        text = skill_text()
        plugin = ROOT / "plugins/exo"
        known = {p.stem for p in (plugin / "commands").glob("*.md")} | {
            p.parent.name for p in (plugin / "skills").glob("*/SKILL.md")}
        named = set(re.findall(r"/exo:([a-z-]+)", text))
        self.assertTrue(named <= known, f"unknown /exo: commands {sorted(named - known)}")
        others = re.findall(r"(?<![\w./`])/(?!exo:)[a-z]+:[a-z-]+", text)
        self.assertEqual(others, [], "slash commands outside this plugin")

    def test_no_environment_specific_paths_or_ids(self):
        text = skill_text()
        for pat in (r"\$EXO\b", r"EXOCORTEX_ROOT", r"~/", r"personal-skills", r"commit_record",
                    r"content_commit", r"\baxiom\b", r"`[atv]\d{2}`", r"\$CLAUDE_JOB_DIR", r"session-dispatch"):
            self.assertIsNone(re.search(pat, text), pat)

    def test_the_write_is_content_commit_with_only(self):
        text = skill_text()
        calls = re.findall(r"`EXO (commit[^`]*)`", text)
        self.assertTrue(calls)
        for call in calls:
            self.assertIn("--only", call)
        self.assertIn(".exocortex/instance.toml", text)
        self.assertRegex(text, r"(?m)^description: ")


if __name__ == "__main__":
    unittest.main()
