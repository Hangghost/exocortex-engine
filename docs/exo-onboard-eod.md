# `/exo:onboard` and `/exo:eod`

The start and the end of the day in a single-user, single-machine workspace. Both are command
prose in the `exo` plugin; the deterministic half is
[`exocortex_engine.day`](exo-day.md) (`survey`, `sync`, `push`). Same package and plugin as
[`/exo:project`](exo-project.md) and [`/exo:content`](exo-content.md); the executor comes from
`.exocortex/instance.toml` and the engine version must match exactly, as for the other commands.

```
/exo:onboard [--unattended]
/exo:eod [--unattended] [--allow commit,land,push]
```

- **onboard** prints a one-page digest and is **read-only by default**.
- **eod** orchestrates existing primitives only: every write goes through
  `project update apply`, `content commit`, `project land` and `day push`. It adds no git
  write of its own, never forces, never checks out a branch and never works around a refusal.
- Both read the same `survey`, so the morning and the evening never compute state twice.

## Attended or unattended

Whether someone is present is a parameter, not a detection: `--unattended` (always passed by a
scheduler) means nobody; without it, a command the user ran in the current session is attended.

A decision with a default is never taken silently when nobody can answer:

| Step | Attended | Unattended, flag missing | Unattended, flag given |
|---|---|---|---|
| survey, digest, report | done | done | done |
| onboard: commit / sync questions | asked | listed in the status section | (onboard has no `--allow`) |
| eod 2, 7: land past dates, land today | after confirmation | **stop** (`land`) | done; stop on a refusal |
| eod 3: project updates | asked, then applied | skipped, reported | **still skipped** |
| eod 4: lesson | asked | not asked, nothing written | same |
| eod 5: commit the working tree | after confirmation | **stop** (`commit`) | stop on any `DELETE` or refusal; else `content: <date> eod (unattended)` |
| eod 6: milestones | asked, then landed | skipped, reported | **still skipped** |
| eod 8: push | done | **stop** (`push`) | done; divergence left for the user |

- **Pre-check before the first write.** Unattended, eod works out which flags the run needs
  from the first survey and a `day push --no-push --json` dry run: `commit` when the working
  tree has paths to commit; `land` when there is a past date to land, an unlanded local
  `content/<today>`, or today's content in the working tree; `push` when the dry run lists a
  branch `ahead` or `new`. A missing flag stops the run before anything is written, with a
  copyable command that has every needed flag (`/exo:eod --unattended --allow …`). A step that
  later finds itself unauthorised stops the same way.
- `--allow` authorises steps. `content commit` and `land` push the refs they write, as they
  always do; `push` authorises step 8. `--allow` is ignored when attended.
- **Stop** means no later step runs; the report starts with the step, the reason, the missing
  flags and the re-run command. Skipped steps go to the report's "unattended, not done"
  section; the next onboard shows them again (unlanded dates, `projects[].progress`).
- Unattended runs never write a work_log and never pick a milestone, with or without `--allow`.

## `/exo:onboard`

| # | Step | Reads | Degraded |
|---|---|---|---|
| 1 | state | `day survey --fetch --json` | fetch failed: first line "⚠️ fetch failed, last snapshot"; `rejected`: stop, no digest |
| 2 | unlanded past days | `content_branches[]` with `past` and not `landed`, oldest first | — |
| 3 | working tree and main | `worktree`, `main` | questions only when attended (step 9) |
| 4 | board | `project board` | the section shows the error's first line |
| 5 | todos | `todo list --json`: `count`; open entries with `due <= today`; first 3 | module missing, no `inbox/todos.md`, or refused: one line, never "no todos" |
| 6 | open items | `next_items show <name> --json` for each survey project: `count`, `any_failed`, `sources` | module missing: one line; a failing project: one line |
| 7 | health | `findings[]`, `audit.rules` | `rules == 0`: "no rules registered" |
| 8 | digest | — | over 40 lines: first 3 items per section plus "N more" |
| 9 | questions | attended only | commit via `/exo:content commit`; `day sync` only when main is behind and the tree is clean |

- **Order**: state → unlanded past days → health → board → todos → open items → suggested
  next steps. Empty sections are left out entirely; the state section always has main's line.
  "No rules registered" and "rules ran, nothing found" look different, and the digest never
  says "no findings".
- **Open items**: one line per project with `count > 0` or `any_failed`:
  "`<name>`: N open, `/exo:next <name>` to expand", plus "(some sources failed)" when
  `any_failed`. A smaller number is never shown in place of a failure.
- **Findings**: a finding whose `subject` is already shown (an unlanded past branch) is not
  repeated. A `suggest` is printed; one with `mutates: true` is marked as a write and is
  **never run**. Whether rules exist is read from `audit.rules`, not from an empty
  `findings`: `survey`'s built-in `content-branch-remote-only` is not a rule.
- **Suggested next steps** are commands only, never run.
- **Persona**: not shown. The plugin's SessionStart hook ([exo-persona.md](exo-persona.md))
  already warns at the start of the session; onboard does no detection of its own.
- **Questions** come after the digest, all in one message, so the whole page is seen first.
  `sync` needs a clean working tree (untracked files count, and committed files stay in the
  working tree until they land), so it is not offered while the tree is dirty.

## `/exo:eod`

| # | Step | Call | Notes |
|---|---|---|---|
| 0 | pre-check | executor, `version`, mode, `--allow` | unattended pre-check above |
| 1 | state | `day survey --fetch --json` | `rejected`: stop; main `behind` or `diverged`: stop before any write |
| 2 | land past dates | `project land content <date>`, oldest first | one confirmation for all dates; remote-only dates are left for the user |
| 3 | project updates | `project update <name> gather` / `apply` for projects with `progress.has_progress` and an empty `progress.today_worklog_on` | one round of questions for all projects; one failure does not stop the others; a project with today's work_log counts as updated, so a second run the same day does not ask again |
| 4 | lesson | asked in the same round as step 3 | into an updated project's `### 經驗與觀察`, else `contexts/thought_review/<date>_eod_lessons.md` (committed in step 5); blank: no-op |
| 5 | commit the working tree | `content status --json`, `content commit` | `DELETE` lines flagged; `rejected`: stop |
| 6 | milestones | `projects[].milestone` with `ahead > 0`; `project land project <name> "<milestone>"` | one multi-select; `recommend` and `reasons[]` shown as given |
| 7 | land today | `project land content` | after step 6, so main moves to its final shape once |
| 8 | push the rest | `day push --json` | never forced; `diverged`, `failed` left for the user |
| 9 | report | `day survey --json`, `day push --no-push --json` | sections only when non-empty |

- **Fresh state for every write**: each write step re-reads `survey` (without fetch) or the
  primitive's own read-only sub-command; nothing runs on an old snapshot.
- **Why stop on a behind main**: landing on a main that origin has moved past would make them
  diverge. `/exo:onboard` offers `sync` when the working tree is clean.
- **Attended questions**: past dates (one question), updates and the lesson (one round), the
  commit (one confirmation), milestones and landing today (one message).
- **Report sections**: landed (dates, commits, milestones), updated projects, skipped projects
  (no progress), unattended and not done, for the user to resolve (remote-only branches with
  the fetch command, divergence, failed pushes, paths `/exo:content` does not handle, failed
  projects), audit ("measured after the run"). The last line is "✅ remote 全部最新" when main
  is in sync, no content branch is unlanded, the dry run has nothing ahead, new or diverged,
  and nothing is left for the user; otherwise it counts what is left. A second run with
  nothing to do says so and ends with the same last line.

## Not done

Two-machine coordination (`--done`, `--final`, the other machine's state), cross-day recovery
beyond landing dates one by one, a stop guard for repeated no-op updates, upstream and tool
update summaries. Remote-only content branches are not fetched by `land content`; the finding
prints the command.
