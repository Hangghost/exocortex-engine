# exocortex-engine

The shared engine for Exocortex instances. Every push to this repository is a public
release, so the first thing in it is a gate that keeps private identifiers out.

## `/exo:project`

Project context management for a workspace repository (`new`, `load`, `update`, `board`,
`land`): the stdlib-only package `exocortex_engine` plus the Claude Code plugin `exo` in this
repository's marketplace. Contract, CLI and installation: [docs/exo-project.md](docs/exo-project.md).

## `/exo:content`

Commits what you wrote in the working tree to `content/<date>` and `project/<name>` without
switching branches, recomputes the `contexts/<folder>/INDEX.md` navigation index on the
branch, and lands it on `main` while the committed files stay in your working tree:
[docs/exo-content.md](docs/exo-content.md).

## `/exo:discuss`

A discussion partner: a decision tree asked one full round at a time, each question with a
recommended answer; it writes the outcome to `contexts/thought_review/` through
`/exo:content commit` and records where it was handed off. It does not implement anything:
[docs/exo-discuss.md](docs/exo-discuss.md).

## `/exo:decision-ui`

Offline batch decisions: a deterministic renderer turns a queue of items into one
self-contained HTML page, you mark them in a browser, export a decisions JSON, and an applier
you write for that queue applies it (dry-run, fail-fast, idempotent). The package part is
`python -m exocortex_engine.decision_ui render|validate`
(`exocortex_engine.decision_ui`); the method is in
[plugins/exo/skills/decision-ui/SKILL.md](plugins/exo/skills/decision-ui/SKILL.md).

## `/exo:doc-formatter`

Formats pasted or drafted content into a workspace document (`work_log`, `survey`,
`thought_review`, `blog`): type, `l0` frontmatter for the navigation index, path and file
name under `contexts/<folder>/`; after you confirm the preview it commits through
`/exo:content`:
[plugins/exo/skills/doc-formatter/SKILL.md](plugins/exo/skills/doc-formatter/SKILL.md).

```sh
claude plugin marketplace add Hangghost/exocortex-engine
claude plugin install exo@exocortex-engine --scope project
```

## Private identifier gate

`tools/identifier_gate.py` is a single stdlib-only Python script (3.10+, macOS, Linux and
Windows; no packages to install). It checks content against two sets of regular expressions:

- **Built-in patterns** (`B1`, `B2`, ...): home-directory absolute paths (macOS and Windows)
  and tailnet addresses in the 100.64/10 range. These are in the script.
- **Private patterns** (`P1`, `P2`, ...): read at run time from an identifier list that lives
  **outside** this repository. The list itself is private and is never committed.

It **fails closed**: if the list is missing, unreadable, malformed, has no patterns, or
contains a pattern that does not compile or matches the empty string, the gate exits non-zero
and says why. It never treats "could not check" as "nothing found".

Exit codes: `0` clean, `1` identifiers found, `2` the gate could not check.

### What is checked where

| Layer | Hook / job | Checks |
|---|---|---|
| Local | `pre-commit` | staged file contents and paths, author and committer identity |
| Local | `commit-msg` | the commit message |
| Local | `pre-push` | every file version, path and commit (message, names, emails) reachable from what is being pushed |
| CI | `identifier-gate / gate` | the same as `pre-push`, for everything reachable from `HEAD` |
| CI | `identifier-gate / gate` | every file in `.githooks` is mode `100755` (git silently skips a hook that is not executable) |
| CI | `identifier-gate / tests` | unit tests on `ubuntu-latest` and `windows-latest` |

`git commit --no-verify` skips the local commit hooks; `pre-push` and CI are the backstops.
CI runs after the push, so it detects rather than prevents: keep the local hooks installed.

### Install the hooks

Once per clone:

```sh
git config core.hooksPath .githooks
```

Without it git runs none of the hooks and says nothing. The unit tests check it: outside
GitHub Actions, `python -m unittest discover -s tests` fails with the command above until
`core.hooksPath` points at `.githooks`.

The hooks look for `python3`, then `python`, then `py`, and set `PYTHONUTF8=1` so output works
on a cp950 Windows console. They run the script with the base interpreter, so no virtual
environment or launcher executable is involved.

### Identifier list

Location, in order of precedence:

1. the path in the environment variable `EXO_IDENTIFIERS_FILE`
2. `~/.config/exocortex/identifiers.yaml` (the home directory as Python's `Path.home()` sees
   it, which also works on Windows)

Keep it readable only by you (`chmod 600`). Format, a deliberately narrow YAML subset:

```yaml
# Lines starting with # are comments.
patterns:
  - 'single-quoted regex, '' escapes a quote'
  - "double-quoted regex, JSON escapes such as \\d"
  - bare-regex-to-end-of-line
```

Anything else is a format error. Patterns are Python regular expressions, case-sensitive,
matched line by line.

### CI secret

The CI job reads the same list from the Actions secret `EXO_IDENTIFIERS`, writes it to a file
under `$RUNNER_TEMP`, and points `EXO_IDENTIFIERS_FILE` at it. When you change your local list,
update the secret (run inside this clone; `gh` picks the repository from the `origin` remote,
or pass `--repo <owner>/exocortex-engine`):

```sh
gh secret set EXO_IDENTIFIERS < ~/.config/exocortex/identifiers.yaml
```

If the secret is unavailable (for example on a pull request from a fork) the job fails closed.

### Output

Locally the gate prints `location: pattern-id: 'matched text'` so you can fix it.

Under GitHub Actions (or with `--redact` / `EXO_GATE_REDACT=1`) it prints only the location
(`path:line`, or a commit and field) and the pattern id. It never prints the matched text or
a pattern, because Actions logs of a public repository are public. A path that itself matches
a pattern is replaced with a placeholder.

### Run it by hand

```sh
python tools/identifier_gate.py staged            # what pre-commit runs
python tools/identifier_gate.py message FILE      # what commit-msg runs
python tools/identifier_gate.py history HEAD      # what CI runs
python -m unittest discover -s tests -v           # tests use synthetic lists only
```

## License

Apache License 2.0; see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
