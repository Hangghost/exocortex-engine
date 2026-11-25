# `/exo:todo`

Todos of a single-machine, single-person workspace: one file, one line per entry, written without
switching branches, checked by the command itself. Same package and plugin as
[`/exo:project`](exo-project.md) and [`/exo:content`](exo-content.md); stdlib only, Python 3.10+,
macOS, Linux and native Windows. `contract` stays `1`: `inbox/todos.md` is a new optional file.

## File

`inbox/todos.md` (content layer: committed to `content/<today>`, landed on `main` with the day).
The first `add` creates the skeleton when the file is missing.

```markdown
# Todos

## Active

- [ ] [ready][project:<name>][ticket:<slug>][added:2026-11-24][due:2026-12-01] **Title** — one-line summary
  - free-form sub-items (suggested: symptom / evidence / next step)

## Done

### 2026-11-24

- [x] [project:<name>][ticket:<slug>][added:2026-11-20] **Title** — one-line summary
  - 完成：2026-11-24 note
```

- **Entry**: a line starting `- [ ]` (Active) or `- [x]` (Done). The run of `[...]` right after it,
  with no space between them, is the tags (in `[ready] [WIP]` only the first is a tag, and a
  `[...]` followed by `(` is a link, not a tag), the first `**...**` the title, the text after the next ` — ` the summary. Indented
  and blank lines belong to the entry up to the next line starting `- ` or a heading. Lines inside an
  HTML comment are skipped. Active ends at the next `## ` heading.
- **Tags**: `[<status>]`, `[project:<name>]`, `[ticket:<slug>]`, `[added:YYYY-MM-DD]`,
  `[due:YYYY-MM-DD]`, and `[dropped]` in Done. Rendered in that order. Tags that are not understood
  (`source:`, `stale-since:`, …) are kept, not interpreted, and move behind the known ones when the
  line is rewritten. A bare tag that is not `dropped` is the status; only `set`/`add` insist on the four
  values below.
- **status**: `needs-triage` (captured, not judged), `ready`, `discuss-first`, `needs-analysis`.
- **ticket**: `^[a-z0-9]+(-[a-z0-9]+)*$`, unique across Active and Done, never reused. The CLI does not
  derive it from the title (titles are often not ASCII): the command prose passes it in.
- **project**: optional; when given it must be a row `name` of `projects/INDEX.md` on `main` (any
  status). Entries without one report `project: null`.
- **due**: optional `YYYY-MM-DD`. Only data is provided; overdue or stale reminders are not rendered.
- **Done**: `### YYYY-MM-DD` batches, newest on top, same day reused; within a batch the entry
  moved last is on top. Moving an entry turns `[ ]` into
  `[x]`, drops the status, adds `完成：<date> <note>` (or `[dropped]` and `放棄：<date> <reason>`).
- `### ` headings inside Active stay where they are; new entries go to the end of Active. A file
  without `## Active` or `## Done` is refused for every sub-command and the heading is named.
- Text is LF; a CRLF file or a BOM is normalized when the file is next written (stored as an LF blob,
  no BOM).

## CLI

```
<python> -m exocortex_engine.todo version
<python> -m exocortex_engine.todo add --ticket SLUG --title TEXT [--summary TEXT] [--project NAME] [--status S] [--due DATE] [--body-file FILE]
<python> -m exocortex_engine.todo list [--project NAME] [--status S] [--all]
<python> -m exocortex_engine.todo set <ticket> [--status S] [--project NAME] [--due DATE] [--note TEXT]
<python> -m exocortex_engine.todo done <ticket> [--dropped] [--note TEXT]
```

Common options, first output line (`result: ok|noop|rejected|error|usage-error`), `--json` and exit
codes (0 / 1 / 2) are those of `exocortex_engine.project`. Multi-line text goes through `--body-file`
only (quoting and stdin are unreliable on native Windows); the CLI indents each line two spaces.

- `add`: `--status` defaults to `needs-triage`, `added` is today. An existing ticket (Done included)
  is refused naming its title, never renamed. The same call again is `noop` (compared on the
  entry's title line; sub-items are not compared).
- `list`: read only; Active by default, `--all` adds Done. `--project` and `--status` are AND filters.
- `set`: at least one option. `--note` appends `  - <date> 備註：<text>`. A Done ticket or an unknown
  one is refused. There is no syntax to clear `due` or `project`.
- A ticket that a hand edit left on more than one line is refused by `set` and `done`, naming
  the lines.
- `done`: Active → Done. `--dropped` needs a non-empty `--note`. A ticket already in Done is `noop`.
- Bad arguments (slug, empty title or `**`, status, date, missing reason or ticket) exit `2`;
  state problems (unknown or duplicate ticket, project not in the index, guards below) exit `1`.
  Either way nothing is written.

## Writes and reads go through refs

The base is the tip of `content/<today>`, else `origin/content/<today>` (the tracking ref), else
`main`. A write applies a pure transformation to that base's file, makes one commit
`content: todo <op> <ticket>`, moves `content/<today>` with a compare-and-swap (a branch checked
out in a worktree is fast-forwarded there, as in `/exo:content`) and pushes it. When the ref moved
meanwhile, the new tip is read and the transformation redone, up to three times. `list` reads the
same base, so it shows entries written today that have not landed. The working-tree copy may be
older until `land`; that is by design.

Guards, all before anything is written:

- **Earlier day**: a local `content/<d>` older than today that changed `inbox/todos.md` and has not
  landed is refused with `land content <d>`: landing it after today's would conflict on the single
  file. A branch that never touched the file does not count.
- **Hand edits**: the checkout's copy must equal its `HEAD` version or the base's version. Otherwise
  the write is refused: run `/exo:content commit --only inbox/todos.md` (it passes only while
  `/exo:todo` has not written today), or discard the edits.

Once `/exo:todo` has written on a day, change `inbox/todos.md` only through `/exo:todo` until that
day lands: the checkout's copy is older than `content/<today>` from then on, so a hand edit can no
longer be committed. To edit by hand, `land content` first (or discard the hand edit).

## `list --json`

Consumers call `list --json` (`--all` for Done) and never parse the file. Shared frame (`result`,
`command`, `messages`) plus:

```json
{ "ref": "content/2026-11-24", "exists": true, "count": 1, "warnings": [],
  "todos": [ { "ticket": "slug-a", "title": "Title", "project": "name-or-null", "status": "ready",
               "summary": "", "added": "2026-11-20", "due": null, "state": "open",
               "raw": "- [ ] [ready][ticket:slug-a][added:2026-11-20] **Title**\n  - sub-item" } ] }
```

`state` is `open`, `done` or `dropped` (status is `null` for the last two); `ref` is the ref
actually read; `exists` is whether `inbox/todos.md` is there at all on that ref (`false` with
`count: 0` is "no file yet", not "nothing to do"); `raw` is the entry's text, sub-items
included, LF, no trailing newline; `due` is an ISO date or `null` (an unreadable one is `null`
plus a warning). An entry without a ticket is listed
with `ticket: null` and a warning naming its line. Order is file order. In Python,
`todo_md.parse(text) -> Doc` and `todo_md.read(git, ref) -> Doc` (`Doc.todos(...)`, `Doc.warnings`)
are for other modules of this package.

## Not done

Row-level merge driver (multi-machine), reminders for overdue or stale entries, a no-description
capture mode, `reopen`, Done archiving, shipped-detection. `triage` is a prose mode of the command
(it composes `list`, `set` and `done --dropped`), not a sub-command.
