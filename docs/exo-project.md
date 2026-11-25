# `/exo:project` core

Project context management for a single-user, single-machine workspace repository:
`new`, `load`, `update`, `board` and `land`. The deterministic part is the Python package
`exocortex_engine` (stdlib only, Python 3.10+, macOS, Linux and native Windows); the
Claude Code command `/exo:project` (plugin `exo`) does the questions and the rewriting and
calls the CLI for everything else.

## Design

- **The working tree never switches branches.** You stay on `main` (or any branch). Writes
  to `project/*` and `content/*` are *ref-anchored commits*: a throwaway index
  (`GIT_INDEX_FILE`) → `write-tree` → `commit-tree` → `update-ref` with the expected old
  value (compare-and-swap, all refs of one command in one atomic transaction). Nothing is
  checked out, so no worktrees or background sessions are needed.
- **Reads go through git refs**, never through working-tree copies: the truth for
  `projects/<name>/` is the `project/<name>` branch.
- **Writing `main`.** When `main` is not checked out anywhere, it is moved with the same CAS
  `update-ref`. When it is checked out in a worktree, the command builds the commit the same
  way and then runs `git merge --ff-only <commit>` in that worktree, so its index and files
  follow. Uncommitted changes there (unstaged, staged or untracked) are left alone and do not
  block the write, **unless they overlap** the paths the new commit changes: a dirty path
  equal to a changed path, a dirty file where the commit adds a directory (or the reverse), or
  any existing file (untracked or ignored) where the commit adds a path. Then the command
  refuses, lists every overlapping path and writes nothing.
  **Same content is not an overlap** (v0.2): a path whose file already holds exactly what the
  commit brings (compared as `git add` would store it, so `core.autocrlf` working trees are
  not misjudged; a deletion counts when the file is gone too) and whose index entry is HEAD's
  or the landed one is normalized first (tracked: restored to HEAD; untracked: removed; each
  re-checked right before and after, everything put back on any surprise) and then the
  fast-forward writes it again, so it ends with the same bytes. This is what lets
  `/exo:content` leave committed files in your working tree all day (see
  [exo-content.md](exo-content.md)). `git merge --ff-only` refuses
  overlaps on its own as well; the command's check runs first so that a refusal is clean and
  covers every ref of the command (`new` moves `main` and creates `project/<name>`, and
  neither happens).
- **Push** after every write (`git push origin <ref>`). A failed push does not roll back the
  local write; it is reported as `push <ref>: failed: …`.

## Workspace contract (version 1)

### `projects/INDEX.md`

A Markdown table located by **column names**, not positions. Required columns: `name`,
`status`, `last_updated`, `description`; any other column is kept as is and not interpreted.
The index is the first table that has all four. Writes change only the target row's named
cells; new rows go at the end of that table; everything else in the file (other tables,
prose) is kept byte for byte. `status` is one of `active`, `paused`, `dormant`, `completed`,
`archived`, `planned` (v0.1 only creates `active`).

### `projects/<name>/PROJECT.md`

Frontmatter: `last_updated` (YYYY-MM-DD), `status`, `l0` (one-line summary, at most 120
characters), optional `parent`; other keys are kept. Sections are `## ` headings located by
their text; unknown sections are kept:

`## 目標 / 動機` (goal), `## 現況` (current state), `## 下一步` (next steps),
`## 關鍵決策（已定案，不再討論）` (settled decisions), `## 材料地圖` (material map),
`## 資料來源` (sources), `## 環境` (environment).

The first three are required by `update apply`; the rest are optional so that older files
stay valid. `<!-- 待填入 -->` is the placeholder for an empty section.

### work_log

`contexts/work_logs/YYYY-MM-DD_<name>_update.md`, frontmatter `l0`, `project`, `date`,
`type: update`. A second update on the same day appends a `## HH:MM` section
(`### 進展` / `### 經驗與觀察` / `### 下一步`) and rewrites `l0`; earlier sections stay.

### Branches

| Branch | Holds | Created | Lands on `main` |
|---|---|---|---|
| `project/<name>` | `projects/<name>/**` and that project's INDEX row | `new`, from the `main` tip | `land project <name> "<milestone>"`: merge commit (`--no-ff` shape), branch kept |
| `content/<YYYY-MM-DD>` | `contexts/**` and other event-type content | first content write of the day, from the `main` tip | `land content [<date>]`: one squash commit `content: <date> daily snapshot`, branch deleted locally and on the remote |

Landing does not use a textual merge. `land project` takes `projects/<name>/**` path by path
(a path changed on both sides since the merge base is a conflict, and the command refuses)
and replaces only that project's row in `main`'s INDEX, so rows added by other projects in
the meantime never conflict. `land content` does the same path-by-path squash for every path
the content branch changed.

## CLI

```
<python> -m exocortex_engine.project version
<python> -m exocortex_engine.project new <name> --title … --goal … [--current …] [--next …]
<python> -m exocortex_engine.project load <name>
<python> -m exocortex_engine.project update <name> gather
<python> -m exocortex_engine.project update <name> apply --project-md FILE --worklog-section FILE [--worklog-l0 TEXT]
<python> -m exocortex_engine.project board
<python> -m exocortex_engine.project land content [<date>]
<python> -m exocortex_engine.project land project <name> "<milestone>"
```

Common options (after the sub-command): `--repo PATH`, `--main BRANCH` (default `main`),
`--remote NAME` (default `origin`), `--no-push`, `--json`, `--today YYYY-MM-DD`,
`--now HH:MM`.

The first output line is machine-readable: `result: ok|noop|rejected|error|usage-error`
(`version` prints `version: x.y.z` and `contract: 1`). Exit codes: `0` success, `1` refused
(a precondition does not hold; nothing was written), `2` usage error.

- `new` refuses when `project/<name>` exists locally or on the remote. It writes
  `PROJECT.md` (sections not given get the placeholder), `context/.gitkeep` and a new INDEX
  row on the new branch, then merges it into `main`.
- `update <name> apply` validates the new `PROJECT.md` (frontmatter, required sections, no
  section dropped compared with the branch) and the work_log section (all three `###`
  sub-sections, no `## ` heading) **before** writing anything; then it writes `PROJECT.md`
  plus the INDEX `last_updated` to `project/<name>` and the work_log to `content/<today>` in
  one transaction. `last_updated` is set to today by the CLI.
- The command's `load` also lists the project's todos (`todo list --project <name> --json`,
  read-only), and `update`, after `apply` succeeds, offers the todos that look done and the
  `needs-triage` ones in one question; only entries the user ticks are marked done
  ([exo-todo.md](exo-todo.md)). An entry without a ticket is listed, never written. A todo
  failure never undoes or blocks the progress write.
- `board` reads `main`'s INDEX and, for every project that is not `completed` or `archived`,
  the `PROJECT.md` on its branch: status, days since `last_updated`, and the first actionable
  line of `## 下一步` (skipping sub-headings, blockquotes, comments, placeholders, label lines
  ending in a colon, bold-only lines and struck-out items).

## Versioning

`pyproject.toml` holds the one version. `plugins/exo/.claude-plugin/plugin.json`, the
version the command prose declares and `exocortex_engine.__version__` must be equal; a unit
test enforces it. The package and the plugin are updated separately, so the command runs
`version` first and stops when the two differ (exact match during v0.x), printing both
versions and how to update either side.

The workspace may pin the contract in `.exocortex/instance.toml` with a top-level
`contract = 1`; the CLI refuses to run when the value is higher than it supports.

## Installing in a workspace

1. Python dependency, pinned to a tag, in the workspace `pyproject.toml`:

   ```toml
   [project]
   dependencies = [
     "exocortex-engine @ git+https://github.com/Hangghost/exocortex-engine@v0.5.0",
   ]
   ```

   Create the virtual environment from the base Python you trust, then `uv sync --active`.

2. Executor for the command, `.exocortex/instance.toml` at the workspace root:

   ```toml
   # contract = 1   # optional

   [executor]
   python = "./.venv/Scripts/python.exe"   # Windows; macOS/Linux: "./.venv/bin/python"
   env = { PYTHONUTF8 = "1" }

   # [persona]                              # optional, see exo-persona.md
   # plugin = "<name>@<marketplace>"
   ```

   The optional `[persona]` table names the persona plugin the workspace expects; the
   plugin's SessionStart hook warns when this Claude Code configuration lacks it
   ([exo-persona.md](exo-persona.md)). Without the table the hook does nothing.

3. The Claude Code plugin, recorded in the workspace's `.claude/settings.json`:

   ```sh
   claude plugin marketplace add Hangghost/exocortex-engine
   claude plugin install exo@exocortex-engine --scope project
   ```

   The commands are `/exo:project`, `/exo:content`, `/exo:roadmap` ([exo-roadmap.md](exo-roadmap.md)),
   `/exo:todo` ([exo-todo.md](exo-todo.md)), `/exo:next` ([exo-next.md](exo-next.md)),
   `/exo:onboard` and `/exo:eod` ([exo-onboard-eod.md](exo-onboard-eod.md), with
   [exo-day.md](exo-day.md)), `/exo:discuss`, `/exo:decision-ui` and `/exo:doc-formatter`.
   The plugin also registers a SessionStart hook that checks the persona plugin
   ([exo-persona.md](exo-persona.md)); it runs on `node`. To update later:
   `claude plugin marketplace update exocortex-engine` then
   `claude plugin update exo@exocortex-engine`.

## Known limits

- One machine per workspace. Two machines writing the same `content/<date>` get a rejected
  (non-fast-forward) push; it is reported, not merged.
- No `resume`/`complete`/`pause`/`rename`, no multi-machine coordination, no scheduling.
