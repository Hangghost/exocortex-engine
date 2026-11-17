# `/exo:content` (v0.2)

Commit what you wrote in the working tree, normally a checkout of `main`, to the branch it
belongs to, without switching branches and without touching the working tree, and land it
on `main` in the evening while the files stay where you left them. Same package and plugin as
[`/exo:project`](exo-project.md); stdlib only, Python 3.10+, macOS, Linux and native Windows.

## What goes where

| Working-tree path | Branch | Notes |
|---|---|---|
| `contexts/**`, `inbox/**`, `memory/**` | `content/<today>` | created from the `main` tip on the first commit of the day |
| `projects/<name>/**` | `project/<name>` | the branch must exist (`/exo:project new <name>`) |
| `projects/INDEX.md` | with `project/<name>` | only the rows of the projects committed in the same run |
| `contexts/<folder>/INDEX*.md` | — | generated: recomputed on the branch, never copied |
| anything else | — | listed and left alone; in a single-machine workspace architecture changes (`rules/`, `.claude/`, `docs/`, tooling) are committed to `main` directly |

## CLI

```
<python> -m exocortex_engine.content version
<python> -m exocortex_engine.content status [--only PATH...]
<python> -m exocortex_engine.content commit [--only PATH...] [-m MESSAGE] [--project-message NAME MESSAGE]...
<python> -m exocortex_engine.content land [<date>]            # = project land content
<python> -m exocortex_engine.contexts_index <folder>... | --all [--repo DIR]
```

Common options, first output line and exit codes are those of `exocortex_engine.project`.
`status` writes nothing; it runs every check `commit` runs and exits `1` (`result: rejected`)
when `commit` would be refused.

### `status`

Lists the dirty paths of the working tree by target branch (`write`, `DELETE`, `regen` for a
recomputed index), the paths that are **already committed and waiting to land** (their file
equals the branch's version), the paths `/exo:content` does not handle, and the
`content/<date>` branches that have not landed yet.

A `DELETE` line is a tracked file that is gone from the working tree; it is deleted on the
branch too. When `content/<today>` does not exist yet, that deletion is part of the new
branch's first commit, so the command prose shows these lines separately before asking.

### `commit`

One commit per target branch, all ref moves in one transaction. Before anything is
written, every path is checked against the target branch; the branch must

- not have the path, or
- have the version your checkout's `HEAD` has, or
- already have the new content (nothing to do for that path), or
- have the version **this working tree committed there last** (you edited the file again
  before it landed).

Anything else means the path was changed on the branch by someone else since; the whole
batch is refused and the path named. What a working tree committed is recorded in
`<git common dir>/exocortex/content-commits.json` (per working tree and branch); when the
record is missing, a second commit of the same file before landing is refused, never forced.

- **Files** are stored as `git add` would store them (clean filters, `core.autocrlf`), so
  the branch gets LF blobs from a CRLF working tree and the landing check compares the
  same thing. Files that are not UTF-8 are stored as they are.
- **Navigation index**: for every `contexts/<folder>/` with a changed `*.md` directly in it,
  `INDEX.md` (and `INDEX_archive.md`, `INDEX_reviews.md`) is regenerated from the target
  branch's tree plus this batch and written in the same commit. Folders indexed: the
  default list (`survey_sessions`, `work_logs`, `thought_review`, `blog`) and any folder whose
  `INDEX.md` on the branch is a generated one.
- **`PROJECT.md`** goes through the same validation as `/exo:project update apply`
  (frontmatter, required sections, no section dropped). Its `last_updated` is **not**
  rewritten: the branch must keep matching your file; a note says when it is not today.
- **`projects/INDEX.md`**: the row of each project committed in this run goes to that
  project's branch (a row on another project's branch would never land). Other edits of the
  file are not committed; change a project's status with `/exo:project`.
- The working tree is not changed. The committed files now equal their branch.

### `land`

`land content` squashes `content/<date>` onto `main` (see `exo-project.md`). Files in your
checkout that already hold what lands do not block it (see "Same content is not an overlap"
in `exo-project.md`). A file you edited again after committing differs and blocks the landing:
commit it first. A local copy of a generated `INDEX*.md` that differs from the branch's
blocks too: discard it.

## Navigation index format

`contexts_index` reads the frontmatter (`date`, `l0`, `tags`, `type`) of each `*.md` directly
in the folder and writes one table, newest first (an ISO week `YYYY-Www` sorts as its
Monday; undated entries last). Above the folder's threshold (150, `survey_sessions` 60) the
older entries go to `INDEX_archive.md`; `type: code-review` entries all go to
`INDEX_reviews.md`. No timestamps: the same files give the same bytes.

The frontmatter is read by a deliberately narrow YAML reader (`yaml_subset`): plain, quoted
and block scalars, flow sequences on one line, block sequences and nested mappings. It
returns what PyYAML's `safe_load` returns for those shapes, so existing indexes stay byte for
byte the same, and a frontmatter PyYAML would reject still shows as a missing `l0`. Anything
outside the subset (anchors, aliases, tags, flow mappings, tabs, explicit indentation
indicators, non-decimal numbers, timestamps in the four keys) is refused with the file and
line, never guessed; `commit` is then refused for that folder until the frontmatter is
simplified.
