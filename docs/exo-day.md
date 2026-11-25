# `exocortex_engine.day`: start and end of the day

The deterministic half of `/exo:onboard` (morning digest) and `/exo:eod` (end of day) for a
single-user, single-machine workspace. Same package as [`/exo:project`](exo-project.md) and
[`/exo:content`](exo-content.md); stdlib only, Python 3.10+, macOS, Linux and native Windows.

- `survey` is the one read-only snapshot both commands read, so they never compute state
  twice and drift apart.
- `sync` and `push` are the only new writes. Everything else the end of the day writes goes
  through the existing primitives (`project update apply`, `content commit`, `project land`).
- The CLI never asks and never decides for the user. Whether someone is present, and the
  unattended authorisation `--allow commit,land,push`, are handled by the command prose: it
  stops **before its first write** when a needed `--allow` is missing. With or without
  `--allow`, nothing writes a work_log or picks a milestone on the user's behalf.

## CLI

```
<python> -m exocortex_engine.day version
<python> -m exocortex_engine.day survey [--fetch]
<python> -m exocortex_engine.day sync
<python> -m exocortex_engine.day push
```

`<python>` is the `[executor]` interpreter from `.exocortex/instance.toml`. Common options
(`--repo --main --remote --no-push --json --today --now`), the first output line
(`result: …`) and the exit codes (0 success, 1 refused, 2 usage error) are those of
`exocortex_engine.project`. `sync` adds `result: skipped` (exit 0): a gate did not hold and
nothing was written.

| Sub-command | Writes |
|---|---|
| `survey` | nothing; `--fetch` moves remote-tracking refs only (`git fetch --prune --no-tags`) |
| `sync` | moves the local `main` (fast-forward in the working tree) |
| `push` | remote refs; never forced |

## `survey`

Always computed from the live refs; nothing is cached. A failed `--fetch` does not stop it:
`fetch.ok` is `false`, `fetch.error` holds git's last stderr line, a `warning:` line is
printed, and the rest describes the last snapshot. `result: rejected` (exit 1) only when the
main branch does not exist or `projects/INDEX.md` cannot be parsed; `/exo:eod` stops then.

JSON keys (`--json`; also `result`, `command`, `messages`):

| Key | Content |
|---|---|
| `today` | the date used (`--today` or the local date) |
| `fetch` | `{requested, ok, error}`; `ok` is `null` without `--fetch` |
| `main` | `{branch, local, remote, ahead, behind, diverged, state}` against `<remote>/<main>`; `state` is `in-sync`, `ahead`, `behind`, `diverged`, `no-remote` (no remote-tracking ref) |
| `worktree` | `{branch, clean, to_commit, deletes[], waiting, not_handled[], refused[], plan}`: the dirty paths classified as `content status` does; `plan` is the same object `content status --json` prints (`targets`, `waiting`, `other`, `unlanded`). `clean` counts every dirty path, untracked and unhandled ones too |
| `content_branches[]` | oldest first, local ∪ remote `content/<YYYY-MM-DD>` (other names ignored): `{date, branch, location: local\|remote\|both, tips_match, commits, landed, past}`. `commits` is the number of commits main lacks; `landed` is true when landing would bring nothing main lacks (generated indexes ignored); `past` is `date < today`; `tips_match` is set only for `both` |
| `projects[]` | open projects: `projects/INDEX.md` rows on main that are not `completed`/`archived`, plus local `project/*` branches without a row; sorted by name |
| `projects[].progress` | `{branch, last_project_md_commit, commits_since_project_md, update_day, commits_since_update, uncommitted, today_worklog, today_worklog_on[], has_progress}` (below) |
| `projects[].milestone` | `{ahead, last_landed, last_landed_commit, days_since_landed, substantive, substantive_paths[], recommend, triggers[], reasons[]}` (below) |
| `findings[]` | health-check findings (below) |
| `audit` | `{rules: <number of registered rules>}` |

### Progress

- `commits_since_project_md`: commits on `project/<name>` since `PROJECT.md` last changed
  there (since it left main when it never did).
- `update_day`: when that last change is an `update apply` commit
  (`project(<name>): update <date>`), its committer date (`YYYY-MM-DD`); otherwise `null`.
- `commits_since_update`: `commits_since_project_md` without the commits made on
  `update_day`. An update covers the day it was made: `/exo:eod` updates `PROJECT.md` first
  and commits the project's files after, and those files are in that day's work_log, so the
  closing commit is not progress the next morning. Without an update (`new`, a hand edit of
  `PROJECT.md`) every commit counts. Dates are committer dates, so a commit made after
  midnight in the same eod run counts as the next day's.
- `uncommitted`: working-tree paths that would go to `project/<name>`.
- `today_worklog_on`: where today's work_log exists (`content/<today>`, `main`).
- `has_progress`: `commits_since_update > 0 or uncommitted > 0`. `/exo:eod` also skips a
  project whose `today_worklog_on` is not empty (updated today).

### Milestone recommendation

For a project whose branch is ahead of main (`ahead` = commits on `project/<name>` that main
lacks), `recommend` is true when **any** of these holds; each one that holds is listed in
`triggers` with its actual value, and as a sentence in `reasons`:

| `rule` | Condition |
|---|---|
| `ahead` | `ahead >= 5` |
| `substantive` | the branch changed a path under `projects/<name>/` outside `projects/<name>/context/` since it left main |
| `never-landed` | main has no `Merge project/<name>: <milestone>` commit (the `new` merge does not count) |
| `days-since-landed` | at least 7 days since the latest such merge (its committer date) |

The thresholds are fixed in this release. `recommend` is only a suggestion: the command asks
one multi-select question and runs `land project` for what the user picks, with the name the
user gives.

### Findings

Each finding:

```json
{"rule": "kebab-case-name", "severity": "info|warn|error",
 "subject": {"kind": "path|branch|project|ref", "id": "..."},
 "message": "one line",
 "suggest": {"command": "copyable command", "mutates": true}}
```

`suggest` is `null` when there is nothing to suggest; when present, `mutates` is always set.
A suggestion with `mutates: true` is printed, **never run automatically**. The presentation
does not repeat a finding whose `subject` is already shown in another section.

Two sources, in this order:

1. **Built in to `survey`** (not counted in `audit.rules`):
   `content-branch-remote-only` (`warn`): a `content/<date>` exists on the remote only; the
   suggestion is `git branch content/<date> <remote>/content/<date>`. `land content` does not
   fetch it by itself.
2. **Registered rules** (module `exocortex_engine.audit`). This release registers none, so
   `audit.rules` is `0` and the digest says "no rules registered", which is not the same as
   "rules ran and found nothing".

## `sync`

Fast-forwards the local main branch to `<remote>/<main>` as of the last fetch (run
`survey --fetch` first). `noop` when main is in sync with or ahead of it. Otherwise all three
gates must hold, and every one that does not is listed in `skip`:

1. the working tree is clean (no staged, unstaged or untracked path);
2. it is on the main branch;
3. main fast-forwards (not diverged).

Missing remote-tracking ref: `skipped`. The move is the same `git merge --ff-only` that the
other commands use for a checked-out main.

## `push`

Looks at the local `main`, `project/*` and `content/*` against their remote-tracking refs:

| `state` | Action |
|---|---|
| `ahead`, `new` (not on the remote) | pushed with `git push <remote> <ref>:<ref>` (no `+`, never forced) |
| `in-sync` | nothing |
| `behind` | listed in `behind`; nothing to push |
| `diverged` | listed in `diverged`, not pushed; for the user to resolve |

JSON: `branches[]` (`{branch, state, local, remote, push}`), `pushed[]`, `failed[]`,
`diverged[]`, `behind[]`. `noop` when nothing is ahead. A push the remote refuses (the
remote moved since the last fetch) fails without force: `result: rejected`, exit 1, the
branch in `failed`. `--no-push` lists the pushes as `disabled` and pushes nothing.

## Audit interface (`exocortex_engine.audit`)

```python
from exocortex_engine.audit import Finding, Subject, Suggest, register

def check(ctx):  # ctx: exocortex_engine.project.Ctx (git, main, today, now, push)
    yield Finding(rule="my-rule", severity="warn", subject=Subject("path", "contexts/x.md"),
                  message="one line", suggest=Suggest("copyable command", mutates=False))

register("my-rule", check)
```

- `register(rule_name, check)`: kebab-case name, unique; `check(ctx)` returns an iterable of
  `Finding` whose `rule` is its own name.
- `run_all(ctx) -> list[Finding]`: every rule in registration order. A rule that raises, or
  returns anything else, yields one `severity: error` finding with its name (subject: the
  main branch as a `ref`) and the other rules still run.
- `rule_count()`: the number `survey` prints as `audit.rules`.
- `Registry` is the same API on a private registry (tests).

A new rule is one `register` call plus one unit test; `survey` and the command prose do not
change.
