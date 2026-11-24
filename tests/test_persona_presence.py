"""The exo SessionStart hook that warns when a workspace's declared persona plugin is not usable.

Medium tests: they start the real `node plugins/exo/hooks/check-persona.mjs` with a synthetic
SessionStart input, a synthetic workspace and a synthetic Claude Code config directory under a
temp dir (never the real ones; HOME and USERPROFILE are redirected too).

Silence and a broken detector look the same, so every non-"present" case first runs a control
that proves the same layout is judged `present`, and the "absent" case is a known positive.
`FORMAT_CANARY_*` mirror the shape Claude Code writes today (de-identified): if a Claude Code
upgrade changes that shape and these turn red, the hook's reader needs updating.

node is required. When it is missing the tests fail under CI and are skipped (with a visible
"node not found" reason) elsewhere, so a missing node can never look like a pass."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "plugins" / "exo" / "hooks" / "check-persona.mjs"
HOOKS_JSON = ROOT / "plugins" / "exo" / "hooks" / "hooks.json"
NODE = shutil.which("node")

PID = "sample-persona@sample-market"

FORMAT_CANARY_INSTALLED = {
    "version": 2,
    "plugins": {
        PID: [
            {
                "scope": "user",
                "installPath": "/sample/config/plugins/cache/sample-market/sample-persona/1.0.0",
                "version": "1.0.0",
                "installedAt": "2026-01-01T00:00:00.000Z",
                "lastUpdated": "2026-01-01T00:00:00.000Z",
                "gitCommitSha": "0000000000000000000000000000000000000000",
            }
        ],
        "other-plugin@sample-market": [{"scope": "user", "installPath": "/sample/x", "version": "0.1.0"}],
    },
}
FORMAT_CANARY_SETTINGS = {"enabledPlugins": {PID: True, "other-plugin@sample-market": True}}

TOML_OK = f'[executor]\npython = "./.venv/bin/python"\n\n[persona]\nplugin = "{PID}"\n'


def node_gate() -> None:
    if NODE:
        return
    if os.environ.get("CI"):
        raise AssertionError("node not found: the persona hook tests need node on PATH (CI must provide it)")
    raise unittest.SkipTest("node not found: persona hook tests NOT run (install node to run them)")


class PersonaPresence(unittest.TestCase):
    def setUp(self) -> None:
        node_gate()
        self.tmp = Path(tempfile.mkdtemp(prefix="exo-persona-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "workspace"
        self.cfg = self.tmp / "config-a"
        self.home = self.tmp / "home"
        for d in (self.ws / ".exocortex", self.ws / ".claude", self.cfg / "plugins", self.home):
            d.mkdir(parents=True)

    # ---- helpers -------------------------------------------------------------

    def toml(self, text: str | bytes = TOML_OK) -> None:
        data = text.encode("utf-8") if isinstance(text, str) else text
        (self.ws / ".exocortex" / "instance.toml").write_bytes(data)

    def installed(self, doc=FORMAT_CANARY_INSTALLED, raw: str | None = None, cfg: Path | None = None) -> None:
        target = (cfg or self.cfg) / "plugins" / "installed_plugins.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw if raw is not None else json.dumps(doc), encoding="utf-8")

    def settings(self, where: str, doc=None, raw: str | None = None) -> None:
        target = {
            "user": self.cfg / "settings.json",
            "project": self.ws / ".claude" / "settings.json",
            "local": self.ws / ".claude" / "settings.local.json",
        }[where]
        target.write_text(raw if raw is not None else json.dumps(doc), encoding="utf-8")

    def run_hook(self, *, config_env: bool = True, plugin_root: Path | None = None, extra: dict | None = None,
                 stdin: dict | None = None, project_dir: bool = True):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home))
        if config_env:
            env["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        if plugin_root is not None:
            env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        if project_dir:
            env["CLAUDE_PROJECT_DIR"] = str(self.ws)
        env.update(extra or {})
        payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(self.ws)}
        payload.update(stdin or {})
        return subprocess.run([NODE, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True,
                              encoding="utf-8", env=env, cwd=str(self.tmp), timeout=60)

    def assert_silent(self, proc) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "", proc.stdout)

    def assert_warns(self, proc, state: str):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip(), f"expected a {state} warning, got no output (stderr: {proc.stderr})")
        out = json.loads(proc.stdout)
        self.assertIn(f"（{state}）", out["systemMessage"])
        ctx = out["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "SessionStart")
        self.assertIn(out["systemMessage"], ctx["additionalContext"])
        return out

    def control_present(self) -> None:
        """Known-good layout: proves the detector can say `present` (silent) here."""
        for stale in (self.cfg / "settings.json", self.ws / ".claude" / "settings.json",
                      self.ws / ".claude" / "settings.local.json"):
            stale.unlink(missing_ok=True)
        self.toml()
        self.installed()
        self.settings("user", FORMAT_CANARY_SETTINGS)
        self.assert_silent(self.run_hook())

    # ---- fixture matrix ------------------------------------------------------

    def test_installed_and_enabled_is_silent(self) -> None:
        self.control_present()

    def test_absent_is_a_known_positive(self) -> None:
        self.control_present()
        doc = {"version": 2, "plugins": {"other-plugin@sample-market": FORMAT_CANARY_INSTALLED["plugins"]["other-plugin@sample-market"]}}
        self.installed(doc)
        out = self.assert_warns(self.run_hook(), "absent")
        self.assertIn(PID, out["systemMessage"])
        self.assertIn(str(self.cfg), out["systemMessage"])
        self.assertIn(f"claude plugin install {PID}", out["systemMessage"])

    def test_empty_entry_list_is_absent(self) -> None:
        self.control_present()
        self.installed({"version": 2, "plugins": {PID: []}})
        self.assert_warns(self.run_hook(), "absent")

    def test_disabled_in_each_scope(self) -> None:
        for where in ("user", "project", "local"):
            with self.subTest(scope=where):
                self.control_present()
                self.settings(where, {"enabledPlugins": {PID: False}})
                out = self.assert_warns(self.run_hook(), "disabled")
                self.assertIn(f"claude plugin enable {PID}", out["systemMessage"])

    def test_enabled_true_in_user_does_not_hide_project_false(self) -> None:
        self.control_present()
        self.settings("project", {"enabledPlugins": {PID: False}})
        self.assert_warns(self.run_hook(), "disabled")

    def test_installed_list_unreadable_is_unknown(self) -> None:
        cases = {
            "missing": None,
            "empty": "",
            "bad json": "{ not json",
            "version 3": json.dumps({"version": 3, "plugins": FORMAT_CANARY_INSTALLED["plugins"]}),
            "plugins not an object": json.dumps({"version": 2, "plugins": []}),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.control_present()
                target = self.cfg / "plugins" / "installed_plugins.json"
                if raw is None:
                    target.unlink()
                else:
                    target.write_text(raw, encoding="utf-8")
                self.assert_warns(self.run_hook(), "unknown")

    def test_broken_settings_file_is_unknown(self) -> None:
        self.control_present()
        self.settings("project", raw="{ broken")
        self.assert_warns(self.run_hook(), "unknown")

    def test_no_persona_section_is_silent(self) -> None:
        self.control_present()
        self.toml('[executor]\npython = "./.venv/bin/python"\n')
        self.installed({"version": 2, "plugins": {}})  # would be absent if it were declared
        self.assert_silent(self.run_hook())

    def test_no_instance_toml_is_silent(self) -> None:
        self.control_present()
        (self.ws / ".exocortex" / "instance.toml").unlink()
        self.installed({"version": 2, "plugins": {}})
        self.assert_silent(self.run_hook())

    def test_commented_out_persona_section_is_silent(self) -> None:
        self.control_present()
        self.toml(f'# [persona]\n# plugin = "{PID}"\n')
        self.installed({"version": 2, "plugins": {}})
        self.assert_silent(self.run_hook())

    def test_persona_section_without_plugin_is_unknown(self) -> None:
        self.control_present()
        for name, text in {
            "empty section": "[persona]\n",
            "other key": "[persona]\nname = \"x\"\n",
            "not a string": "[persona]\nplugin = 5\n",
            "no marketplace": '[persona]\nplugin = "just-a-name"\n',
            "table array": f'[[persona]]\nplugin = "{PID}"\n',
            "dotted table": f'[persona.extra]\nplugin = "{PID}"\n',
            "dotted key": f'persona.plugin = "{PID}"\n',
        }.items():
            with self.subTest(case=name):
                self.toml(text)
                self.assert_warns(self.run_hook(), "unknown")

    def test_toml_reading_tolerates_bom_crlf_comments_and_quote_styles(self) -> None:
        self.control_present()
        for name, text in {
            "bom+crlf": ("﻿[executor]\r\npython = \"x\"\r\n\r\n[persona]\r\nplugin = \"%s\"\r\n" % PID).encode("utf-8"),
            "trailing comment": f'[persona] # which persona\nplugin = "{PID}"  # sample\n',
            "literal string": f"[persona]\nplugin = '{PID}'\n",
            "section not last": f'[persona]\nplugin = "{PID}"\n\n[executor]\npython = "x"\n',
        }.items():
            with self.subTest(case=name):
                self.toml(text)
                self.assert_silent(self.run_hook())
                self.installed({"version": 2, "plugins": {}})
                self.assert_warns(self.run_hook(), "absent")
                self.installed()

    def test_plugin_key_outside_persona_section_is_ignored(self) -> None:
        self.control_present()
        self.toml(f'[executor]\nplugin = "{PID}"\n\n[persona]\nplugin = "other-plugin@sample-market"\n')
        self.assert_silent(self.run_hook())
        self.toml(f'[persona]\nplugin = "{PID}"\n\n[other]\nplugin = "x@y"\n')
        self.assert_silent(self.run_hook())

    # ---- config directory resolution ----------------------------------------

    def test_config_dir_env_and_plugin_root_disagree_is_unknown(self) -> None:
        self.control_present()
        other = self.tmp / "config-b"
        self.installed(cfg=other)
        proc = self.run_hook(plugin_root=other / "plugins" / "cache" / "sample-market" / "exo" / "1.0.0")
        self.assert_warns(proc, "unknown")

    def test_config_dir_env_and_plugin_root_agree_is_silent(self) -> None:
        self.control_present()
        proc = self.run_hook(plugin_root=self.cfg / "plugins" / "cache" / "sample-market" / "exo" / "1.0.0")
        self.assert_silent(proc)

    def test_config_dir_derived_from_plugin_root_when_env_missing(self) -> None:
        self.control_present()
        root = self.cfg / "plugins" / "cache" / "sample-market" / "exo" / "1.0.0"
        self.assert_silent(self.run_hook(config_env=False, plugin_root=root))
        self.installed({"version": 2, "plugins": {}})
        out = self.assert_warns(self.run_hook(config_env=False, plugin_root=root), "absent")
        self.assertIn(str(self.cfg), out["systemMessage"])

    def test_plugin_root_outside_a_cache_without_config_env_is_unknown(self) -> None:
        self.control_present()
        proc = self.run_hook(config_env=False, plugin_root=self.tmp / "dev-checkout" / "plugins" / "exo")
        self.assert_warns(proc, "unknown")

    def test_default_config_dir_is_home_dot_claude(self) -> None:
        self.control_present()
        default = self.home / ".claude"
        self.installed(cfg=default)
        (default / "settings.json").write_text(json.dumps(FORMAT_CANARY_SETTINGS), encoding="utf-8")
        self.assert_silent(self.run_hook(config_env=False))
        self.installed({"version": 2, "plugins": {}}, cfg=default)
        self.assert_warns(self.run_hook(config_env=False), "absent")

    def test_workspace_found_from_stdin_cwd_by_walking_up(self) -> None:
        self.control_present()
        sub = self.ws / "projects" / "demo"
        sub.mkdir(parents=True)
        self.assert_silent(self.run_hook(project_dir=False, stdin={"cwd": str(sub)}))
        self.installed({"version": 2, "plugins": {}})
        self.assert_warns(self.run_hook(project_dir=False, stdin={"cwd": str(sub)}), "absent")

    def test_garbage_stdin_does_not_break_the_hook(self) -> None:
        self.control_present()
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_CONFIG_DIR=str(self.cfg),
                   CLAUDE_PROJECT_DIR=str(self.ws))
        proc = subprocess.run([NODE, str(HOOK)], input="not json", capture_output=True, text=True,
                              encoding="utf-8", env=env, cwd=str(self.tmp), timeout=60)
        self.assert_silent(proc)

    # ---- wiring --------------------------------------------------------------

    def test_hooks_json_wires_the_script_for_startup_resume_clear(self) -> None:
        doc = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        groups = doc["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "startup|resume|clear")
        cmds = [h["command"] for h in groups[0]["hooks"]]
        self.assertEqual(cmds, ['node "${CLAUDE_PLUGIN_ROOT}/hooks/check-persona.mjs"'])
        self.assertTrue(HOOK.is_file())


if __name__ == "__main__":
    unittest.main()
