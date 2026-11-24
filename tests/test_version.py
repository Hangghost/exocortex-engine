"""One version everywhere: pyproject.toml is the source; plugin.json, the command
prose and the package must say the same thing."""
from __future__ import annotations

import json
import re
import unittest

from _workspace import ROOT
import exocortex_engine


def pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    return re.search(r'^version\s*=\s*"([^"]+)"', project, re.M).group(1)


# Commands and skills are discovered from the plugin directories, so adding one needs no edit
# here (parallel branches that each add a command would otherwise all touch the same list).
# Removing one still fails: these shipped ones must stay unless this floor is edited on purpose.
SHIPPED_COMMANDS = {"content", "project"}
SHIPPED_SKILLS = {"decision-ui", "discuss", "doc-formatter"}


def plugin_prose() -> tuple[list, list]:
    """Every command file and every skill's SKILL.md under plugins/exo, sorted."""
    plugin = ROOT / "plugins/exo"
    commands = sorted((plugin / "commands").glob("*.md"))
    skills = sorted((plugin / "skills").glob("*/SKILL.md"))
    return commands, skills


class PluginInventory(unittest.TestCase):
    def test_shipped_commands_and_skills_are_present(self):
        commands, skills = plugin_prose()
        self.assertLessEqual(SHIPPED_COMMANDS, {c.stem for c in commands}, "a shipped command is missing")
        self.assertLessEqual(SHIPPED_SKILLS, {s.parent.name for s in skills}, "a shipped skill is missing")

    def test_every_skill_directory_has_a_skill_md(self):
        # A skill directory without SKILL.md would be skipped by the glob above, and so by the
        # version check, without any failure.
        skills_dir = ROOT / "plugins/exo/skills"
        missing = sorted(d.name for d in skills_dir.iterdir() if d.is_dir() and not (d / "SKILL.md").is_file())
        self.assertEqual(missing, [], "skill directories without SKILL.md")

    def test_no_stray_entries_in_commands(self):
        # Only *.md files are commands; anything else there would escape the version check.
        commands_dir = ROOT / "plugins/exo/commands"
        stray = sorted(p.name for p in commands_dir.iterdir() if p.suffix != ".md" or not p.is_file())
        self.assertEqual(stray, [], "non-command entries in plugins/exo/commands")


class VersionConsistency(unittest.TestCase):
    def test_all_versions_match_pyproject(self):
        v = pyproject_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+$")
        plugin = json.loads((ROOT / "plugins/exo/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], v, "plugin.json")
        commands, skills = plugin_prose()
        for cmd in [*commands, *skills]:
            declared = re.findall(r"需要的引擎版本：`([^`]+)`", cmd.read_text(encoding="utf-8"))
            rel = cmd.relative_to(ROOT / "plugins/exo").as_posix()
            self.assertEqual(declared, [v], f"{rel} must declare exactly one required version")
        self.assertEqual(exocortex_engine.__version__, v, "src/exocortex_engine/__init__.py")

    def test_marketplace_points_at_plugin(self):
        market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        entry = {p["name"]: p for p in market["plugins"]}["exo"]
        self.assertTrue((ROOT / entry["source"] / ".claude-plugin/plugin.json").is_file())
        self.assertEqual(market["name"], "exocortex-engine")


if __name__ == "__main__":
    unittest.main()
