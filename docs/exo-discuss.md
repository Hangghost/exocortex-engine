# `/exo:discuss` (v0.3)

A discussion partner for thinking a question through before anything is built. It breaks the
question into a decision tree, asks every question whose prerequisites are settled in one round
(each with a recommended answer, so "yes" adopts it), looks up facts itself and asks only for
decisions. When nothing is left to ask, or you stop, it writes the outcome as a discussion record
in `contexts/thought_review/` and asks where to hand it off. It does not implement anything and
does not create branches.

Same plugin as [`/exo:project`](exo-project.md) and [`/exo:content`](exo-content.md). It is a
plugin skill (`plugins/exo/skills/discuss/SKILL.md`), so it is invoked as `/exo:discuss`, and
Claude may also offer it when you say you want to talk something through first.

```
/exo:discuss [<topic>] [--redteam] [--resume <record path or slug>] [--project <name>]
```

- `--redteam` runs a pre-mortem round before closing (three likely causes of failure, each
  tied to a decision).
- `--resume` continues an `open` record: the new record points at it with `resumes:`, and the
  old one is marked `superseded` in the same commit.
- `--project` puts the project in the record's frontmatter and file name.

## Where the record goes

The only write happens after you confirm the summary, in two steps:

1. The record is written to the working tree:
   `contexts/thought_review/<YYYY-MM-DD>_<slug>.md` (with `--project`,
   `<YYYY-MM-DD>_<project>_<slug>.md`).
2. It is committed with
   `<python> -m exocortex_engine.content commit --only <record> -m "<message>"`, so it goes to
   `content/<today>` like any other content (see `exo-content.md`), the
   `contexts/thought_review/INDEX.md` navigation index is recomputed on that branch, and the
   file stays in your working tree until `land`. `--only` keeps your other uncommitted changes
   out of it.

When `commit` refuses (for example a different version of that path is already on the branch),
the refusal is passed on as it is; nothing is written another way. The executor comes from
`.exocortex/instance.toml` and the engine version must match exactly, as for the other
commands. Without them the discussion still works, and the record is printed in the
conversation instead of written.

The record's frontmatter (`date`, `type: discussion`, `topic`, `l0`, `status`, `handoff`, and
optionally `resumes`, `superseded_by`, `project`, `tags`) stays inside the YAML subset the index
reader accepts.

| `status` | Meaning |
|---|---|
| `open` | stopped with undecided questions; offered again next time a related topic comes up |
| `parked` | agreed, deliberately not handed off |
| `handed-off` | handed off; `handoff[]` lists where |
| `superseded` | continued by a later record |

There is no `done`: whether the downstream work finished is read from the downstream, not
written back into the record.

## Hand-off

The engine has one downstream command: `/exo:project update <name>` writes the decisions into a
project's next steps. Any other destination (an architecture change, a todo, another
repository, an article) is recorded in `handoff[]` as `to: other` with `state: pending`; no
command is called.

## Where it runs

`/exo:content commit` is built for the workspace's main checkout: it commits to
`content/<today>` without switching branches or changing the working tree. Running
`/exo:discuss` from a worktree that is not on `main` (for example `project/<name>`) has **not
been verified**; a refusal there is reported, not worked around.
