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


class VersionConsistency(unittest.TestCase):
    def test_all_versions_match_pyproject(self):
        v = pyproject_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+$")
        plugin = json.loads((ROOT / "plugins/exo/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], v, "plugin.json")
        commands = sorted((ROOT / "plugins/exo/commands").glob("*.md"))
        self.assertEqual([c.name for c in commands], ["content.md", "project.md"])
        skills = sorted((ROOT / "plugins/exo/skills").glob("*/SKILL.md"))
        self.assertEqual([s.parent.name for s in skills], ["discuss"])
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
