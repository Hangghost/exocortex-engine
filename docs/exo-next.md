# `/exo:next`

One project's open items as one inventory, and at most three waves to move them. Read-only:
nothing is written, no git write runs, no outside tracker is read. Same package and plugin as
[`/exo:project`](exo-project.md); stdlib only, Python 3.10+, macOS, Linux and native Windows.

The work is split in two. `python -m exocortex_engine.next_items` does what can be decided
deterministically: it reads the sources through git refs, groups, merges, counts and checks the
ledger. The command's prose ([`plugins/exo/commands/next.md`](../plugins/exo/commands/next.md))
only reads that JSON: it judges each item's label (a next step or todo whose text gives no
clue is ✋, done by hand), orders the waves and asks the one question, "採用第 1 波？". The module is named `next_items` so that it does not shadow the builtin `next`.

## CLI

```
<python> -m exocortex_engine.next_items version
<python> -m exocortex_engine.next_items target [<name>] [--json]
<python> -m exocortex_engine.next_items show <name> [--json]
```

Common options (`--repo`, `--main`, `--remote`, `--json`, `--today`, `--now`), the first line
`result: …` and the exit codes (0 ok, 1 refused, 2 usage error) are those of
`exocortex_engine.project`. Both sub-commands leave every ref and the working tree as they were.

A name is known when `projects/INDEX.md` on main has a row for it or a `project/<name>` branch
exists. An unknown name is exit 1 with `name: null` and `suggestions` (close names); the command
never runs on the closest one instead.

### `target`

With a name: `{name, defaulted: false, status}` (status from the INDEX row, `null` for a
branch-only project). Without one: the INDEX rows whose status is `active`; exactly one gives
`{name, defaulted: true, status: "active", active}`, any other number is exit 1 with
`name: null` and `active` listing them.

### `show`

Top-level keys of `--json` (besides `result`, `command`, `messages`):

| Key | Meaning |
|---|---|
| `name`, `today` | the project and the date the overdue check used |
| `count` | number of items, after merges; equals the sum of `source_summary` counts minus `merged` |
| `any_failed` | true when any source is `failed` |
| `sources` | `[{source, state, ref, note?, findings?, warnings?}]`, `source` ∈ `roadmap`, `next_step`, `todo`; `state` ∈ `ok`, `absent`, `failed` |
| `source_summary` | `[{source, state, count, breakdown: [{label, count}]}]`; `count` is `null` unless `ok`. ROADMAP is broken down by line, todos by status (`未標` for none) |
| `merged` | items folded into a ROADMAP task |
| `has_roadmap`, `roadmap_state` | whether the ROADMAP was used; its source state |
| `hygiene` | `[{rule, objects, detail}]` (below) |
| `lines`, `phases`, `now` | from the ROADMAP: `[{id, title, clearer}]`, `[{id, title, state}]`, the `now` ids |
| `groups` | `[{key, title, kind, state, count, items}]` in display order; empty groups are left out |
| `items` | in group order (below) |
| `synthesize_at`, `suggest_synthesize` | 10; true when there is no ROADMAP (`absent`, not `failed`) and `count` ≥ 10 |

Each item: `key`, `kind` (`task`, `next_step`, `todo`), `title`, `group`, `sources`, `origin`
(the sources joined with `+`, e.g. `roadmap+todo`), `ticket`, `status`, `line`, `clearer`,
`phase`, `after`, `after_open` (the `after` tasks still open), `now_rank` (1-based place in
`now`, or `null`), `due` (ISO date or `null`), `overdue`, `tag` (`📅` when `due` is set, else
`null`: the one label the prose may not change), `heading` (the `###` above a next step), `raw`
(the entry as written) and `also` (`[{source, key, title, status, due, raw}]` merged into it).
Keys are the task id, the todo ticket, `todo-<n>` for a todo without one and `next-<n>` for a
next step; a repeated key gets `~2`, `~3`.

## Sources

| Source | Read from | `absent` | `failed` |
|---|---|---|---|
| `roadmap` | `projects/<name>/ROADMAP.yaml` on `project/<name>` via `roadmap.read_roadmap` ([exo-roadmap.md](exo-roadmap.md)); open tasks only | no branch or no file | it does not parse or has any validator finding (`note` names the first three, `findings` has all) |
| `next_step` | `## 下一步` of `PROJECT.md` on `project/<name>`, else main | no PROJECT.md, or no such section | not UTF-8 |
| `todo` | `inbox/todos.md` at the base `/exo:todo` reads (`content/<today>`, else `origin/content/<today>`, else main); open entries with `[project:<name>]` | no such file | it does not parse |

A failed ROADMAP is not used: the inventory falls back to the shape without one, and no
`synthesize` is suggested (fixing it is `/exo:roadmap <name> revise`).

`## 下一步` items are column-0 list items (`-`, `*`, `+`, `N.`, `N)`) with their indented lines.
Checked (`[x]`) and struck (`~~…`) items, placeholders (only an HTML comment), and anything in a
column-0 `<details>` block, fenced code or multi-line comment are left out.

## Groups and merges

With a ROADMAP: one group per phase whose state is not `done`, in ROADMAP order, titled with the
phase title; then `待辦（未排入規劃）` and `下一步（未排入 ROADMAP）`. Without one: `下一步`, then `待辦`; `###` sub-headings in `## 下一步` do not split the group (an item
keeps its sub-heading in `heading`, for information only).
A `done` phase that still has open tasks keeps its group, so those tasks stay visible.

A todo whose ticket equals an open task's id, and a next step whose text contains an open task's
id as a whole word (not next to a letter, digit, `_` or `-`; the earliest one when several match),
are merged into that task: one item, the other source in `sources` and `also`, the earlier `due`
kept. No other merge happens; two items that only look alike stay two.

## Hygiene

| Rule | Objects |
|---|---|
| `now-not-open` | ids in `now` whose task is `done` or `cancelled` |
| `item-overdue` | items whose `due` is before `today` (tasks and todos alike) |

## Not in v1

Weekly commitments, upstream activity, in-flight changes and discussion records as sources; a
triage wave 0; `--until`; read-back dates. They are listed in the contract as deferred and would
be added as new sources without changing these keys.
