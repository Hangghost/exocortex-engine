# `/exo:roadmap`

A project's structure as one file: `projects/<name>/ROADMAP.yaml` on `project/<name>`, next to
its `PROJECT.md`. `/exo:roadmap <name> synthesize` drafts the first version, `revise` writes a
new one from the old one plus fresh material and shows what changed, `diff` compares without
writing. Same package and plugin as [`/exo:project`](exo-project.md); stdlib only, Python
3.10+, macOS, Linux and native Windows.

There is no new write path. The command writes the checked text into the working tree and
commits it with `/exo:content commit --only projects/<name>/ROADMAP.yaml`; branch routing,
compare-and-swap and push are those of [`/exo:content`](exo-content.md), and `commit` (and
`status`) refuse a `ROADMAP.yaml` that fails the validator below, whoever wrote it.
`/exo:project load` does not read the file, and the roadmap never rewrites `PROJECT.md`.

## Schema (v1)

```yaml
project: <name>          # must equal the directory name
revised: 2026-11-24
revised_by: what was read (free text)
since: 2026-11-24        # diff base date
now: [task-id, ...]      # ordered, all tasks, no repeats
phases:  [{id, title, state: done|now|later, done_when?, gate?: {id, condition, evaluated: null|date, note?}}]
lines:   [{id, title, clearer}]
tasks:   [{id, title, line, phase, status: open|done|cancelled, after?: [id], clears?: [id], due?: date, why_here?, done_note?}]
ledger:  [{id, title, line, why?, cleared_by?: id}]               # optional
decisions: [{id, text, superseded_by?: id, superseded_note?}]     # optional, no status
signals: [{id, date, text, informs?: [id]}]                       # optional, no status
```

Ids are kebab-case and unique across the whole file (a gate's id included). Fields not listed
are ignored. The file is read with `yaml_subset` (block mappings and sequences, plain, quoted
and block scalars, one-line flow sequences); anything else is a `parse-failed` finding with its
line, never a guess. In particular a folded scalar (`>`) whose lines are not all indented the
same is refused: write such text as separate fields or a plain string.

## Validation

`validate_text(text, name)` returns every finding; an empty list means the text may land. A
finding has a `rule`, the `id` it concerns (when there is one), the 1-based `line` in the text
and a `message`.

| Rule | Refused when |
|---|---|
| `parse-failed` | `yaml_subset` refuses the text, or the root is not a mapping |
| `missing-<key>` | a required top-level key is absent (`missing-since`, …) |
| `missing-<field>`, `missing-gate-<field>` | an entry lacks a required field (`missing-title`, `missing-clearer`, …) |
| `bad-<kind>`, `bad-gate`, `bad-now` | a list is not a list, an entry not a mapping, `now` not a list |
| `bad-id`, `duplicate-id` | an id is not kebab-case, or is used twice anywhere in the file |
| `bad-phase-state`, `bad-task-status` | `state` / `status` outside the allowed values |
| `bad-date`, `bad-gate-evaluated` | `revised`, `since`, `due`, `date`, `gate.evaluated` not a date |
| `project-mismatch` | `project` differs from the directory name |
| `dangling-line`, `dangling-phase` | a task's (or ledger entry's) `line`, a task's `phase` names nothing |
| `now-dangling`, `now-duplicate` | `now` names something that is not a task, or a task twice |
| `dangling-after`, `after-cycle` | `after` names something that is not a task, or the `after` links form a cycle |
| `dangling-clears`, `dangling-cleared-by`, `dangling-superseded-by` | the id exists nowhere in the file |
| `decision-has-status`, `signal-has-status` | a decision or signal carries `status` |
| `yaml-hash-truncation` | an unquoted value is followed by exactly one space and `#` |

The truncation check reads the text, not the loaded values: `title: foo #bar` loads as `foo`
and is refused, `title: "foo #bar"` passes, and a comment two or more spaces after a value is
a comment. Block scalars and quoted values are content and are not checked. Known blind spots:
a continuation line that starts with `#`, and content two or more spaces before a `#`.

## Diff

`diff(old, new)` compares two parsed files by id over phases, gates, lines, tasks, ledger,
decisions and signals: `added` and `removed` (`{id, kind, title}`), `moved` (a task's `line` or
`phase`, or a ledger entry's `line`, changed: `{id, kind, title, from, to}`), `changed` (any
other field: `{id, kind}`), `now` (`{before, after, changed}`) and `empty`. Values are compared
as loaded, so re-quoting is not a change; `revised`, `revised_by` and `since` are not compared.
A renamed id is one `removed` plus one `added`, never silent.

## CLI

```
<python> -m exocortex_engine.roadmap version
<python> -m exocortex_engine.roadmap validate <name> --file FILE [--new]
<python> -m exocortex_engine.roadmap diff <name> [--file FILE]
```

Common options, first output line and exit codes are those of `exocortex_engine.project`;
neither sub-command writes anything.

- `validate` checks the text of `FILE`. `--new` (synthesize) also refuses when
  `project/<name>` already has a `ROADMAP.yaml` (`already-exists`) or does not exist
  (`no-project-branch`). Findings mean `result: rejected`, exit 1. `--json` keys: `name`,
  `file`, `new`, `ok`, `findings` (`[{rule, id, line, message}]`), `summary` (`phases`,
  `lines`, `tasks`, `open_tasks`, `ledger`, `decisions`, `signals`, `now: [{id, title,
  status}]`; present whenever the text parsed).
- `diff` compares `FILE` (default: `projects/<name>/ROADMAP.yaml` in the working tree) with the
  version on `project/<name>`. `--json` keys: `name`, `file`, `old_source`, `old_status` and
  `new_status` (`ok`, `absent`, `failed`), the diff keys above. No difference is
  `result: noop`; no old version means everything is `added`. A side that does not parse is
  `result: rejected` with its findings under `old_findings` / `new_findings`.

## Python entry points

For other modules (`/exo:next` reads the roadmap through these, never through YAML of its own):

- `read_roadmap(git, name, rev=None) -> Loaded`: the file at `rev` (default `project/<name>`).
  `Loaded` has `status` (`ok`, `absent`, or `failed` when it does not parse or has findings),
  `source` (the ref, short), `raw` (text), `data` (resolved mapping, set whenever it parsed)
  and `findings`.
- `load_text(text, name=None, source="") -> Loaded`, `validate_text(text, name=None) -> list[Finding]`.
- `diff(old_data, new_data) -> dict`, `summary(data) -> dict` (shapes above).
- `item_raw(text) -> {id: text}`: each list entry as written (trailing blank and comment lines
  dropped), for quoting entries verbatim.
- `roadmap_path(name)`, `PHASE_STATES`, `TASK_STATUSES`.
