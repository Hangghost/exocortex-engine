#!/usr/bin/env python3
"""Private identifier gate: refuse content that matches a private identifier list.

Stdlib only, Python 3.10+, runs the same on macOS, Linux and Windows.

The private list lives outside the repository (see README). The gate fails closed:
a missing, unreadable, malformed or empty list is an error, never "zero hits".

Modes:
  staged         staged file contents and paths, plus the author/committer ident (pre-commit)
  message FILE   a commit message file (commit-msg)
  history REV..  every blob, path and commit (message, names, emails) reachable from REV (CI)
  pre-push       read git's pre-push stdin and run `history` on each pushed commit

Exit codes: 0 clean, 1 identifiers found, 2 gate could not check (fail closed).

Redacted output (--redact, EXO_GATE_REDACT=1, or running under GitHub Actions) prints only
locations and pattern ids (B<n> built-in, P<n> private), never the matched text or the
patterns, because a public CI log would otherwise republish what the gate exists to block.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = "EXO_IDENTIFIERS_FILE"
ENV_REDACT = "EXO_GATE_REDACT"
ZERO_SHA = re.compile(r"^0+$")

# Generic patterns that are never legitimate in the engine: home-directory absolute paths
# (macOS, Windows) and tailnet addresses (the 100.64/10 CGNAT range).
BUILTIN_PATTERNS: tuple[str, ...] = (
    r"/Users/[\w.\-]+",
    r"\b[A-Za-z]:\\+Users\\+[\w.\-]+",
    r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
)

_ENTRY = re.compile(r"""^-\s+(?:'((?:[^']|'')*)'|"((?:[^"\\]|\\.)*)"|(\S.*?))\s*$""")


class GateError(Exception):
    """The gate could not perform its check. Always reported as a failure."""


def identifiers_path() -> Path:
    override = os.environ.get(ENV_FILE)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "exocortex" / "identifiers.yaml"


def parse_patterns(text: str) -> list[str]:
    """Parse the deliberately narrow YAML subset: `patterns:` then one `- <regex>` per line.

    Errors name the line number only, never the line's content (it is private)."""
    patterns: list[str] = []
    seen_header = False
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "patterns:" and not seen_header:
            seen_header = True
            continue
        m = _ENTRY.match(line)
        if not seen_header or not m:
            raise GateError(f"identifier list line {lineno}: not a `- <regex>` entry under `patterns:`")
        single, double, bare = m.groups()
        try:
            if single is not None:
                value = single.replace("''", "'")
            elif double is not None:
                value = json.loads(f'"{double}"')
            else:
                value = bare
        except json.JSONDecodeError:
            raise GateError(f"identifier list line {lineno}: bad escape in double-quoted entry") from None
        n = len(patterns) + 1
        try:
            compiled = re.compile(value)
        except re.error as exc:
            raise GateError(f"pattern P{n} (line {lineno}) does not compile (position {exc.pos})") from None
        if compiled.search("") is not None:
            raise GateError(f"pattern P{n} (line {lineno}) matches the empty string")
        patterns.append(value)
    if not seen_header:
        raise GateError("identifier list has no `patterns:` header")
    if not patterns:
        raise GateError("identifier list contains no patterns")
    return patterns


def load_patterns(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise GateError(f"identifier list not found: {path} (set {ENV_FILE} or create it)") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError(f"identifier list unreadable: {path} ({type(exc).__name__})") from None
    return parse_patterns(text)


@dataclass(frozen=True)
class Hit:
    subject: str  # a path, "commit <sha>", "next commit", "commit message" or "push"
    field: str  # "" for file content, else e.g. "path", "message", "author-email"
    line: int  # 0 when the field is a single value
    pattern_id: str
    text: str


class Scanner:
    def __init__(self, private: list[str]) -> None:
        self.rules = [(f"B{i}", re.compile(p)) for i, p in enumerate(BUILTIN_PATTERNS, 1)]
        self.rules += [(f"P{i}", re.compile(p)) for i, p in enumerate(private, 1)]
        self.private_count = len(private)
        self.hits: list[Hit] = []
        self.scanned = 0

    def matches(self, text: str) -> bool:
        return any(rx.search(text) for _, rx in self.rules)

    def scan(self, subject: str, field: str, text: str, multiline: bool = True) -> None:
        self.scanned += 1
        lines = text.splitlines() if multiline else [text]
        for lineno, line in enumerate(lines, 1):
            for pid, rx in self.rules:
                m = rx.search(line)
                if m:
                    self.hits.append(Hit(subject, field, lineno if multiline else 0, pid, m.group(0)))

    def scan_bytes(self, subject: str, data: bytes) -> None:
        """Scan every reading of `data` (see `decodings`) as one item; a hit that two
        readings share is reported once."""
        start, scanned = len(self.hits), self.scanned
        for text in decodings(data):
            self.scan(subject, "", text)
        self.hits[start:] = list(dict.fromkeys(self.hits[start:]))
        self.scanned = scanned + 1

    def scan_blob(self, path: str, data: bytes) -> None:
        self.scan(path, "path", path, multiline=False)
        self.scan_bytes(path, data)


def decodings(data: bytes) -> list[str]:
    """The readings of `data` to scan: UTF-16 with a BOM, else UTF-8 and/or cp950 (Big5).

    Short cp950 text is often also valid UTF-8 by accident (about 1 in 100 two-character
    strings), so when strict UTF-8 succeeds on non-ASCII data that strict cp950 also
    decodes, both readings are scanned: a missed identifier costs more than a second scan.
    Data that neither decodes strictly (binary, mixed encodings) falls back to UTF-8
    with replacement, as before."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return [data.decode("utf-16", errors="replace")]
    readings = []
    for codec in ("utf-8", "cp950"):
        try:
            readings.append(data.decode(codec))
        except UnicodeDecodeError:
            pass
        if readings and readings[0].isascii():
            return readings
    return readings or [data.decode("utf-8", errors="replace")]


def fsdecode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


class Git:
    def __init__(self, redact: bool) -> None:
        self.redact = redact

    def run(self, *args: str, stdin: bytes | None = None) -> bytes:
        try:
            proc = subprocess.run(["git", *args], input=stdin, capture_output=True)
        except OSError as exc:
            raise GateError(f"cannot run git ({type(exc).__name__})") from None
        if proc.returncode != 0:
            detail = "" if self.redact else ": " + proc.stderr.decode("utf-8", "replace").strip()
            raise GateError(f"git {args[0]} failed (exit {proc.returncode}){detail}")
        return proc.stdout

    def read_blobs(self, shas: list[str]) -> list[bytes]:
        if not shas:
            return []
        out = self.run("cat-file", "--batch", stdin="".join(f"{s}\n" for s in shas).encode())
        blobs, pos = [], 0
        for _ in shas:
            end = out.index(b"\n", pos)
            header = out[pos:end].split()
            if len(header) != 3:
                raise GateError("git cat-file reported a missing object")
            size = int(header[2])
            blobs.append(out[end + 1 : end + 1 + size])
            pos = end + 1 + size + 1
        return blobs


def scan_staged(git: Git, scanner: Scanner) -> None:
    names = [n for n in git.run("diff", "--cached", "--name-only", "-z", "--diff-filter=d").split(b"\0") if n]
    entries: list[tuple[str, str]] = []
    if names:
        listing = git.run("ls-files", "-s", "-z", "--", *[fsdecode(n) for n in names])
        for rec in filter(None, listing.split(b"\0")):
            meta, _, raw_path = rec.partition(b"\t")
            mode, sha, _stage = meta.decode().split()
            path = fsdecode(raw_path)
            if mode == "160000":  # submodule: only its path is ours to check
                scanner.scan(path, "path", path, multiline=False)
            else:
                entries.append((path, sha))
    for (path, _), data in zip(entries, git.read_blobs([sha for _, sha in entries])):
        scanner.scan_blob(path, data)
    for var, label in (("GIT_AUTHOR_IDENT", "author-ident"), ("GIT_COMMITTER_IDENT", "committer-ident")):
        ident = fsdecode(git.run("var", var)).strip()
        scanner.scan("next commit", label, ident.rsplit(">", 1)[0] + ">", multiline=False)


def scan_history(git: Git, scanner: Scanner, revs: list[str]) -> None:
    listing = git.run("rev-list", "--objects", "--filter=object:type=blob", *revs, "--")
    blobs: list[tuple[str, str]] = []
    for raw in filter(None, listing.split(b"\n")):
        sha, _, path = raw.partition(b" ")
        if path:  # commits and trees are filtered out; every blob carries a path
            blobs.append((fsdecode(path), sha.decode()))
    for (path, _), data in zip(blobs, git.read_blobs([sha for _, sha in blobs])):
        scanner.scan_blob(path, data)
    log = git.run("log", "-z", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B", *revs, "--")
    for rec in filter(None, log.split(b"\0")):
        sha, an, ae, cn, ce, body = fsdecode(rec).split("\x1f", 5)
        subject = f"commit {sha[:12]}"
        for label, value in (("author-name", an), ("author-email", ae), ("committer-name", cn), ("committer-email", ce)):
            scanner.scan(subject, label, value, multiline=False)
        scanner.scan(subject, "message", body)


def scan_pre_push(git: Git, scanner: Scanner, stdin: str) -> None:
    revs: list[str] = []
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, _ = parts
        scanner.scan("push", "ref-name", f"{local_ref} {remote_ref}", multiline=False)
        if not ZERO_SHA.match(local_sha):  # an all-zero sha is a branch deletion
            revs.append(local_sha)
    if revs:
        scan_history(git, scanner, revs)


def render(hit: Hit, scanner: Scanner, redact: bool) -> str:
    subject = hit.subject
    if redact and scanner.matches(subject):
        subject = "<path redacted: it matches a pattern>"
    loc = subject + (f" {hit.field}" if hit.field else "") + (f":{hit.line}" if hit.line else "")
    return f"{loc}: {hit.pattern_id}" if redact else f"{loc}: {hit.pattern_id}: {hit.text!r}"


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Block private identifiers (see README).")
    parser.add_argument("--redact", action="store_true", help="never print matched text (forced in CI)")
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("staged")
    modes.add_parser("message").add_argument("file")
    modes.add_parser("history").add_argument("revs", nargs="+")
    modes.add_parser("pre-push")
    args = parser.parse_args(argv)
    redact = args.redact or truthy(os.environ.get(ENV_REDACT)) or os.environ.get("GITHUB_ACTIONS") == "true"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    git = Git(redact)
    try:
        scanner = Scanner(load_patterns(identifiers_path()))
        if args.mode == "staged":
            scan_staged(git, scanner)
        elif args.mode == "message":
            try:
                text = Path(args.file).read_bytes()
            except OSError as exc:
                raise GateError(f"cannot read commit message file ({type(exc).__name__})") from None
            scanner.scan_bytes("commit message", text)
        elif args.mode == "history":
            scan_history(git, scanner, args.revs)
        else:
            scan_pre_push(git, scanner, sys.stdin.read())
    except GateError as exc:
        print(f"identifier-gate: ERROR: {exc}", file=sys.stderr)
        print("identifier-gate: FAILED CLOSED: nothing was checked, so nothing is let through.", file=sys.stderr)
        return 2
    except Exception as exc:  # never let a traceback echo scanned data into a public log
        if not redact:
            raise
        print(f"identifier-gate: ERROR: unexpected {type(exc).__name__}; failing closed.", file=sys.stderr)
        return 2

    for hit in scanner.hits:
        print(render(hit, scanner, redact))
    summary = (
        f"identifier-gate [{args.mode}]: {len(scanner.hits)} hit(s); {len(BUILTIN_PATTERNS)} built-in + "
        f"{scanner.private_count} private patterns over {scanner.scanned} item(s)"
    )
    if scanner.hits:
        print(summary + ". Remove the identifiers; do not bypass the gate.", file=sys.stderr)
        return 1
    print(summary + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
