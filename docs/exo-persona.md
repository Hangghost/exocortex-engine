# Persona presence check (SessionStart hook)

A workspace can rely on a persona plugin (soul, communication style, axioms) that Claude Code
loads at session start. When that plugin is missing from the config directory a window uses, the
session simply has no persona and nothing says so. The `exo` plugin ships one SessionStart hook
that notices and says so.

The hook is **opt-in**: it does nothing unless the workspace declares which persona plugin it
expects. The plugin-side hook is Node (`plugins/exo/hooks/check-persona.mjs`, built-in modules
only); the Python package still uses only the standard library.

## Declaring the expectation

`.exocortex/instance.toml` at the workspace root, optional table:

```toml
[persona]
plugin = "<name>@<marketplace>"
```

- `plugin` is a single-line string (double or single quotes), the plugin id as Claude Code
  records it in `installed_plugins.json`.
- No `[persona]` table: the hook prints nothing and exits 0. Comment the table out to switch
  the check off for a window you do not want a persona in.
- Only this form is recognised. `[[persona]]`, `[persona.x]`, a top-level `persona.plugin = …`,
  a `[persona]` without `plugin`, or a value that is not `<name>@<marketplace>` is reported as
  `unknown` rather than ignored.
- The file is read line by line (UTF-8, BOM and CRLF tolerated); no TOML library is involved.
  Other tables (`[executor]`, …) and the top-level `contract` are not touched.

## What it decides

The hook finds the workspace from `CLAUDE_PROJECT_DIR`, else the `cwd` in the SessionStart
input, else the process directory (walking up to the first `.exocortex/instance.toml`).

The config directory it checks, in order:

1. `CLAUDE_CONFIG_DIR`, when set.
2. The directory above `plugins/cache/` in `CLAUDE_PLUGIN_ROOT`.
3. If both exist and differ: `unknown`. If only `CLAUDE_PLUGIN_ROOT` exists and is not under a
   `plugins/cache/`: `unknown`.
4. Neither set: `~/.claude` (the user's home directory).

It then reads `<config dir>/plugins/installed_plugins.json` (must be `version: 2`) and the
`enabledPlugins` map of `<config dir>/settings.json`, `<workspace>/.claude/settings.json` and
`<workspace>/.claude/settings.local.json`.

| State | Meaning | Output |
|---|---|---|
| `present` | Installed in this config directory and no layer sets it to `false` | none |
| `absent` | `installed_plugins.json` has no entry (or an empty list) for the id | warning |
| `disabled` | Installed, but some layer has `enabledPlugins[id] = false` | warning |
| `unknown` | The check could not be made: `installed_plugins.json` missing, empty, invalid JSON or not `version: 2`; a settings file that exists but is not valid JSON; a malformed `[persona]`; the config directory cannot be decided; an internal error | warning |

`unknown` warns on purpose: a detector that cannot tell must not look like "all fine".
`present` means "installed and enabled", not "loaded": a persona hook that fails on its own is
not detected here (it prints its own failure message).

## Output

Only when the state is not `present`, one JSON object on stdout, exit code 0 (the hook never
blocks a session):

```json
{
  "systemMessage": "<warning>",
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "<ask the model to relay the warning first, then the warning>"
  }
}
```

Two channels because it is not documented where `systemMessage` shows for SessionStart:
`additionalContext` asks the model to open its first reply with the warning. Warning texts
(Traditional Chinese, defined in `check-persona.mjs`; each carries its state word in
full-width parentheses):

- `absent`: the plugin is not installed in this config directory; names the id, the directory
  and `claude plugin install <id>`.
- `disabled`: installed but disabled in the named layer; `claude plugin enable <id>`.
- `unknown`: cannot tell, and why.

## When it runs

`plugins/exo/hooks/hooks.json` registers it for SessionStart with matcher `startup|resume|clear`
(not `compact` or `fork`: a compaction would repeat the warning). Other hooks added to the same
file must keep their own matcher and output.

## Checking it on a real machine

Two behaviours cannot be settled from documentation and are the reason for these steps:
whether `CLAUDE_CONFIG_DIR` reaches the hook, and where `systemMessage` shows at SessionStart.

1. Install the exo plugin in a workspace that has `[persona] plugin = "<id>"`, in a config
   directory that does **not** have the persona plugin. Open a session there.
   Expected: a warning with state `absent` naming that config directory, both in the UI and
   (if the model relays it) in the first reply.
2. Note whether the warning is visible as a banner (`systemMessage` works) or only in the first
   reply (`additionalContext` only).
3. Install the persona plugin in that config directory and open a new window.
   Expected: no warning.
4. Disable the persona plugin (`enabledPlugins[id] = false` in any layer) and open a new window.
   Expected: `disabled`.
5. To see whether `CLAUDE_CONFIG_DIR` reaches the hook, start a window with a custom
   `CLAUDE_CONFIG_DIR` and check that the warning names that directory, not the default one.
   A warning that says "inconsistent" (`unknown`) means the two sources disagree.

## Tests

`tests/test_persona_presence.py` runs the real hook under `node` against synthetic workspaces
and config directories. Every non-`present` case first proves the same layout is judged
`present`; the `FORMAT_CANARY_*` fixtures mirror the files Claude Code writes today, so a format
change in a Claude Code upgrade turns them red. If `node` is missing the tests fail under CI
(`CI` set) and are skipped, with "node not found" in the reason, elsewhere.
