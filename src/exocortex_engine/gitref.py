"""Ref-anchored git writes: build commits without touching any working tree.

A write never checks anything out. It builds a tree in a throwaway index
(``GIT_INDEX_FILE``), turns it into a commit with ``commit-tree`` and moves the
target ref with a compare-and-swap (``update-ref --stdin``, all refs in one
atomic transaction). The caller's working tree, index and HEAD stay as they are.

The one exception is a branch that is checked out in some worktree (normally
``main``). Moving such a ref behind the worktree's back would leave its index
and files out of step with HEAD, so :meth:`Git.advance` fast-forwards it with an
ordinary ``git merge --ff-only`` inside that worktree. Uncommitted changes there
(staged, unstaged or untracked) are fine unless they overlap the paths the move
changes; then it refuses and lists the overlapping paths.

Only the standard library is used; every git call passes a list (``shell=False``),
adds ``-c core.quotepath=false`` and decodes output as UTF-8.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

ZERO = "0" * 40
FILE_MODE = "100644"

#: A tree change: path -> (mode, blob sha), or ``None`` to remove the path.
Changes = Mapping[str, Optional[tuple[str, str]]]


class GitError(RuntimeError):
    """A git command failed."""

    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        self.args_list = list(args)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {self.stderr}")


class Refused(RuntimeError):
    """A precondition does not hold; nothing was written."""


class CASConflict(Refused):
    """A ref moved between our read and our write; nothing was written."""


@dataclass(frozen=True)
class RefUpdate:
    ref: str  # full name, e.g. refs/heads/main
    new: str  # sha, or ZERO to delete
    old: str  # sha the ref must still point at, or ZERO for "must not exist"


@dataclass(frozen=True)
class Holder:
    """A worktree that has a branch checked out."""

    path: Path
    branch: str  # full ref name


def branch_ref(name: str) -> str:
    return name if name.startswith("refs/") else f"refs/heads/{name}"


def short_ref(ref: str) -> str:
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


class Git:
    """Thin wrapper over the git CLI for one repository."""

    def __init__(self, repo: Path | str, remote: str = "origin") -> None:
        self.repo = Path(repo)
        self.remote = remote

    # ---- process plumbing -------------------------------------------------

    def run_bytes(
        self,
        *args: str,
        input: Optional[bytes] = None,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[Path] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        cmd = ["git", "-c", "core.quotepath=false", *args]
        full_env = None
        if env:
            full_env = dict(os.environ)
            full_env.update(env)
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or self.repo),
            input=input,
            env=full_env,
            capture_output=True,
            shell=False,
        )
        if check and proc.returncode != 0:
            raise GitError(args, proc.returncode, proc.stderr.decode("utf-8", "replace"))
        return proc

    def run(self, *args: str, input: Optional[str] = None, **kw) -> str:
        data = input.encode("utf-8") if input is not None else None
        proc = self.run_bytes(*args, input=data, **kw)
        return proc.stdout.decode("utf-8", "replace")

    def ok(self, *args: str, **kw) -> bool:
        return self.run_bytes(*args, check=False, **kw).returncode == 0

    # ---- reads -------------------------------------------------------------

    def rev(self, rev: str) -> Optional[str]:
        """Commit sha of ``rev``, or None when it does not resolve."""
        proc = self.run_bytes("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout.decode().strip() or None

    def read_bytes(self, rev: str, path: str) -> Optional[bytes]:
        proc = self.run_bytes("cat-file", "blob", f"{rev}:{path}", check=False)
        return proc.stdout if proc.returncode == 0 else None

    def read_text(self, rev: str, path: str) -> Optional[str]:
        data = self.read_bytes(rev, path)
        return data.decode("utf-8") if data is not None else None

    def entries(self, rev: str, paths: Iterable[str] = ()) -> dict[str, tuple[str, str]]:
        """``{path: (mode, sha)}`` for blobs under ``paths`` (all when empty) at ``rev``."""
        paths = list(paths)
        out = self.run_bytes("--literal-pathspecs", "ls-tree", "-r", "-z", rev, "--", *paths).stdout
        result: dict[str, tuple[str, str]] = {}
        for rec in out.decode("utf-8").split("\0"):
            if not rec:
                continue
            meta, path = rec.split("\t", 1)
            mode, kind, sha = meta.split(" ")
            if kind == "blob":
                result[path] = (mode, sha)
        return result

    def changed_paths(self, a: str, b: str, pathspec: Sequence[str] = ()) -> list[str]:
        out = self.run_bytes(
            "diff", "--name-only", "-z", "--no-renames", a, b, "--", *pathspec
        ).stdout.decode("utf-8")
        return [p for p in out.split("\0") if p]

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", a, b)

    def merge_base(self, a: str, b: str) -> Optional[str]:
        proc = self.run_bytes("merge-base", a, b, check=False)
        return proc.stdout.decode().strip() or None if proc.returncode == 0 else None

    def git_dir(self) -> Path:
        return Path(self.run("rev-parse", "--absolute-git-dir").strip())

    def has_remote(self) -> bool:
        return self.remote in self.run("remote").split()

    def remote_has_branch(self, ref: str) -> tuple[Optional[bool], str]:
        """Ask the remote whether ``ref`` exists.

        Returns ``(exists, basis)``; basis is ``"remote"`` when the remote answered,
        ``"snapshot"`` when it could not be reached and the local remote-tracking
        ref was used instead, and ``"none"`` when there is no such remote."""
        if not self.has_remote():
            return None, "none"
        proc = self.run_bytes("ls-remote", "--heads", self.remote, ref, check=False)
        if proc.returncode == 0:
            return bool(proc.stdout.strip()), "remote"
        tracking = f"refs/remotes/{self.remote}/{short_ref(ref)}"
        return self.rev(tracking) is not None, "snapshot"

    # ---- object writes (no ref moves) --------------------------------------

    def hash_blob(self, data: bytes) -> str:
        # --no-filters: store exactly these bytes, whatever autocrlf/attributes say.
        return self.run_bytes("hash-object", "-w", "--no-filters", "--stdin", input=data).stdout.decode().strip()

    def blob_changes(self, files: Mapping[str, Optional[str | bytes]]) -> dict[str, Optional[tuple[str, str]]]:
        """Hash file contents into blobs; ``None`` marks a removal."""
        changes: dict[str, Optional[tuple[str, str]]] = {}
        for path, content in files.items():
            if content is None:
                changes[path] = None
            else:
                data = content.encode("utf-8") if isinstance(content, str) else content
                changes[path] = (FILE_MODE, self.hash_blob(data))
        return changes

    def build_tree(self, base: Optional[str], changes: Changes) -> str:
        """Tree of ``base`` (a commit, or empty when None) with ``changes`` applied.

        Uses a throwaway index file; the repository's own index is never read or
        written. The file is given as an absolute native path, which Git for
        Windows accepts."""
        with tempfile.TemporaryDirectory(prefix="exo-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if base:
                self.run_bytes("read-tree", base, env=env)
            else:
                self.run_bytes("read-tree", "--empty", env=env)
            if changes:
                recs = []
                for path, entry in changes.items():
                    if "\0" in path or "\n" in path or path.startswith("/"):
                        raise ValueError(f"bad path {path!r}")
                    if entry is None:
                        recs.append(f"0 {ZERO}\t{path}")
                    else:
                        recs.append(f"{entry[0]} {entry[1]}\t{path}")
                data = ("\0".join(recs) + "\0").encode("utf-8")
                self.run_bytes("update-index", "-z", "--index-info", input=data, env=env)
            return self.run_bytes("write-tree", env=env).stdout.decode().strip()

    def tree_of(self, commit: str) -> str:
        return self.run("rev-parse", f"{commit}^{{tree}}").strip()

    def commit_tree(self, tree: str, parents: Sequence[str], message: str) -> str:
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        # Message on stdin: no argv encoding issues on Windows.
        return self.run(*args, "-F", "-", input=message.rstrip("\n") + "\n").strip()

    # ---- ref moves ---------------------------------------------------------

    def update_refs(self, updates: Sequence[RefUpdate]) -> None:
        """Move all refs in one transaction, each guarded by its expected old value.

        Either every ref moves or none does. A mismatch raises :class:`CASConflict`."""
        if not updates:
            return
        lines = []
        for u in updates:
            if u.new == ZERO:
                lines.append(f"delete {u.ref} {u.old}")
            else:
                lines.append(f"update {u.ref} {u.new} {u.old}")
        proc = self.run_bytes("update-ref", "--stdin", input=("\n".join(lines) + "\n").encode(), check=False)
        if proc.returncode != 0:
            raise CASConflict(
                "ref moved while we were writing; nothing was written, run the command again: "
                + proc.stderr.decode("utf-8", "replace").strip()
            )

    # ---- worktree awareness -------------------------------------------------

    def holders(self) -> dict[str, Holder]:
        """Branches checked out in any worktree of this repository."""
        out = self.run("worktree", "list", "--porcelain")
        result: dict[str, Holder] = {}
        path: Optional[Path] = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                path = Path(line[len("worktree "):])
            elif line.startswith("branch ") and path is not None:
                ref = line[len("branch "):]
                result[ref] = Holder(path, ref)
        return result

    def dirty_paths(self, worktree: Path) -> list[str]:
        """Paths with uncommitted changes in ``worktree``: staged, unstaged and
        untracked (every file, not collapsed directories). A rename or copy
        contributes both its new and its original path."""
        recs = self.run_bytes(
            "status", "--porcelain", "-z", "--untracked-files=all", cwd=worktree
        ).stdout.decode("utf-8").split("\0")
        paths: list[str] = []
        i = 0
        while i < len(recs):
            rec = recs[i]
            if len(rec) > 3:
                paths.append(rec[3:].rstrip("/"))
                if "R" in rec[:2] or "C" in rec[:2]:
                    i += 1
                    if i < len(recs) and recs[i]:
                        paths.append(recs[i].rstrip("/"))
            i += 1
        return paths

    def overlap(self, worktree: Path, old: str, new: str) -> list[str]:
        """Paths where fast-forwarding ``worktree`` from ``old`` to ``new`` would meet
        local state: a dirty path equal to (or a parent/child directory of) a path the
        commits change, or any existing file where ``new`` adds a path (this also
        catches ignored files, which git itself would overwrite)."""
        changed = self.changed_paths(old, new)
        if not changed:
            return []
        hits: set[str] = set()
        for d in self.dirty_paths(worktree):
            for c in changed:
                if c == d or c.startswith(d + "/") or d.startswith(c + "/"):
                    hits.add(d)
        in_old = self.entries(old, changed)
        for c in changed:
            if c not in in_old:
                p = worktree / c
                if p.exists() or p.is_symlink():
                    hits.add(c)
        return sorted(hits)

    def check_advance(self, ref: str, new: Optional[str] = None, old: Optional[str] = None) -> Optional[Holder]:
        """Return the worktree holding ``ref`` (or None). With ``new``/``old``, raise
        :class:`Refused` listing every overlapping path when the holder's uncommitted
        changes meet what the move would change. Call before any ref moves."""
        holder = self.holders().get(ref)
        if holder is not None and new is not None and old is not None:
            hits = self.overlap(holder.path, old, new)
            if hits:
                raise Refused(
                    f"{short_ref(ref)} is checked out at {holder.path} and these paths have uncommitted "
                    f"changes that the write would touch: {', '.join(hits)}; commit or stash them, "
                    "then run the command again"
                )
        return holder

    def advance(self, ref: str, new: str, old: str) -> str:
        """Move a branch that may be checked out. ``new`` must descend from ``old``.

        Not checked out: CAS ``update-ref``. Checked out: ``git merge --ff-only`` in
        that worktree, so its index and files follow; refused when uncommitted local
        changes overlap the paths the move changes (git's own check is the second
        guard). Returns ``"update-ref"`` or ``"merge"``."""
        holder = self.check_advance(ref, new, old)
        if holder is None:
            self.update_refs([RefUpdate(ref, new, old)])
            return "update-ref"
        head = self.rev(ref)
        if head != old:
            raise CASConflict(f"{short_ref(ref)} moved (expected {old[:10]}, found {str(head)[:10]})")
        proc = self.run_bytes("merge", "--ff-only", "--quiet", new, cwd=holder.path, check=False)
        if proc.returncode != 0:
            raise Refused(
                f"could not fast-forward {short_ref(ref)} at {holder.path}: "
                + proc.stderr.decode("utf-8", "replace").strip()
            )
        return "merge"

    # ---- remote ------------------------------------------------------------

    def push(self, *refspecs: str) -> str:
        """Push; never raises. Returns ``pushed``, ``skipped: …`` or ``failed: …``."""
        if not self.has_remote():
            return f"skipped: no remote {self.remote!r}"
        proc = self.run_bytes("push", "--quiet", self.remote, *refspecs, check=False)
        if proc.returncode == 0:
            return "pushed"
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return "failed: " + (msg[-1] if msg else f"exit {proc.returncode}")


def three_way_changes(
    git: Git, base: Optional[str], ours: str, theirs: str, pathspec: Sequence[str] = ()
) -> tuple[dict[str, Optional[tuple[str, str]]], list[str]]:
    """Changes to bring ``theirs``' edits (since ``base``) onto ``ours``, path by path.

    For each path changed between base and theirs: unchanged on ours -> take theirs;
    already equal -> nothing; changed differently on both sides -> conflict.
    Returns ``(changes, conflicts)``. No content-level merging is attempted."""
    if base is None:
        changed = sorted(git.entries(theirs, pathspec))
        base_e: dict[str, tuple[str, str]] = {}
    else:
        changed = git.changed_paths(base, theirs, pathspec)
        base_e = git.entries(base, changed) if changed else {}
    if not changed:
        return {}, []
    ours_e = git.entries(ours, changed)
    theirs_e = git.entries(theirs, changed)
    changes: dict[str, Optional[tuple[str, str]]] = {}
    conflicts: list[str] = []
    for path in changed:
        b, o, t = base_e.get(path), ours_e.get(path), theirs_e.get(path)
        if o == t:
            continue
        if o == b:
            changes[path] = t
        else:
            conflicts.append(path)
    return changes, conflicts
