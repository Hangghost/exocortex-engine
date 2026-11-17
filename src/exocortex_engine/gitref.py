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
changes; then it refuses and lists the overlapping paths. A dirty path whose file
already holds exactly what the move brings is not an overlap: it is normalized first
and the fast-forward writes the same bytes back (see :meth:`Git.overlap`).

Only the standard library is used; every git call passes a list (``shell=False``),
adds ``-c core.quotepath=false`` and decodes output as UTF-8.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

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


@dataclass(frozen=True)
class SamePath:
    """A dirty path whose working-tree content already equals what a move brings."""

    path: str
    head: Optional[tuple[str, str]]  # entry in the checkout's HEAD (the old commit)
    index: Optional[tuple[str, str]]  # index entry before normalizing
    blob: Optional[str]  # landed blob id; None when the move removes the path
    worktree: Optional[str]  # clean-filter hash of the file; None when it is absent


@dataclass(frozen=True)
class Overlap:
    blocking: list[str]
    same: list[SamePath]


@dataclass(frozen=True)
class AdvancePlan:
    holder: Holder
    same: list[SamePath]


@dataclass(frozen=True)
class _Saved:
    same: SamePath
    data: Optional[bytes]


_REGULAR = ("100644", "100755")


def _same_path(
    worktree: Path,
    path: str,
    head: Optional[tuple[str, str]],
    new: Optional[tuple[str, str]],
    index: Any,
    wt_hash: Optional[str],
) -> Optional[SamePath]:
    """A :class:`SamePath` when ``path`` already holds what the move brings, else None.
    ``index`` is ``()`` when the path is not in the index, None when it is conflicted."""
    if index is None:
        return None
    idx = index or None
    for e in (head, new, idx):
        if e is not None and e[0] not in _REGULAR:
            return None  # symlinks, submodules: never treated as the same
    p = worktree / path
    new_sha = new[1] if new else None
    if (idx[1] if idx else None) not in {head[1] if head else None, new_sha}:
        return None  # staged content that is neither HEAD's nor the landed one
    if p.is_symlink():
        return None
    if p.exists():
        if wt_hash is None or wt_hash != new_sha:
            return None
    elif new_sha is not None:
        return None
    return SamePath(path, head, idx, new_sha, wt_hash)


def _not_restored(paths: Sequence[str]) -> str:
    if not paths:
        return ""
    return ("; these files were changed by someone else meanwhile and were not put back (their content "
            "is on the branch being landed): " + ", ".join(paths))


def _overlap_message(ref: str, holder: Holder, paths: Sequence[str]) -> str:
    from .generated_files import is_contexts_index_path

    generated = [p for p in paths if is_contexts_index_path(p)]
    msg = (
        f"{short_ref(ref)} is checked out at {holder.path} and these paths have uncommitted changes "
        f"that differ from what the write brings: {', '.join(paths)}; commit them first "
        "(content and project files: /exo:content commit), or stash them, then run the command again"
    )
    if generated:
        msg += (
            f". Generated index files ({', '.join(generated)}) are recomputed on the branch: "
            "discard your local copy (git checkout -- <path>, or delete it if untracked)"
        )
    return msg


def _chunks(paths: list[str], size: int = 200) -> list[list[str]]:
    """Pathspec batches: keeps each command line short (Windows caps it near 32k characters).
    An empty list stays one empty batch, which means "everything" to the callers."""
    return [paths[i:i + size] for i in range(0, len(paths), size)] or [[]]


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
        result: dict[str, tuple[str, str]] = {}
        for chunk in _chunks(list(paths)):
            out = self.run_bytes("--literal-pathspecs", "ls-tree", "-r", "-z", rev, "--", *chunk).stdout
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

    def hash_blob(self, data: bytes, write: bool = True) -> str:
        # --no-filters: store exactly these bytes, whatever autocrlf/attributes say.
        args = ["hash-object", *(["-w"] if write else []), "--no-filters", "--stdin"]
        return self.run_bytes(*args, input=data).stdout.decode().strip()

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

    def index_entries(self, worktree: Path, paths: Iterable[str]) -> dict[str, Optional[tuple[str, str]]]:
        """``{path: (mode, sha)}`` from ``worktree``'s index; ``None`` for a conflicted path."""
        paths = list(paths)
        result: dict[str, Optional[tuple[str, str]]] = {}
        for chunk in _chunks(paths) if paths else ():
            out = self.run_bytes("--literal-pathspecs", "ls-files", "-s", "-z", "--", *chunk, cwd=worktree).stdout
            for rec in out.decode("utf-8").split("\0"):
                if not rec:
                    continue
                meta, path = rec.split("\t", 1)
                mode, sha, stage = meta.split(" ")
                result[path] = (mode, sha) if stage == "0" and path not in result else None
        return result

    def hash_files(self, worktree: Path, paths: Sequence[str], write: bool = False) -> list[str]:
        """Blob ids of working-tree files as ``git add`` would store them: each path's
        clean filters apply (``core.autocrlf``, ``.gitattributes``). ``write`` stores them."""
        if not paths:
            return []
        for p in paths:
            if "\n" in p or "\r" in p:
                raise ValueError(f"bad path {p!r}")
        args = ["hash-object", *(["-w"] if write else []), "--stdin-paths"]
        out = self.run_bytes(*args, input=("\n".join(paths) + "\n").encode("utf-8"), cwd=worktree).stdout
        shas = out.decode().split()
        if len(shas) != len(paths):
            raise GitError(args, 0, "hash-object returned the wrong number of ids")
        return shas

    def cat_blobs(self, shas: Sequence[str]) -> dict[str, bytes]:
        """Contents of many blobs with one ``cat-file --batch``."""
        wanted = list(dict.fromkeys(shas))
        if not wanted:
            return {}
        out = self.run_bytes("cat-file", "--batch", input=("\n".join(wanted) + "\n").encode()).stdout
        result: dict[str, bytes] = {}
        pos = 0
        for sha in wanted:
            nl = out.index(b"\n", pos)
            header = out[pos:nl].split(b" ")
            if len(header) != 3:
                raise GitError(["cat-file", "--batch"], 0, f"missing object {sha}")
            size = int(header[2])
            result[sha] = out[nl + 1: nl + 1 + size]
            pos = nl + 1 + size + 1
        return result

    def overlap(self, worktree: Path, old: str, new: str) -> "Overlap":
        """How fast-forwarding ``worktree`` from ``old`` to ``new`` meets local state.

        A path meets local state when it is dirty (staged, unstaged or untracked) and
        the move changes it, or when the move adds it and a file is already there (this
        also catches ignored files, which git itself would overwrite). Such a path is
        **the same** when the working tree already holds what the move brings: its
        file, through the clean filters, is the landed blob (or both are absent) and its
        index entry is HEAD's or the landed one. Every other one blocks, and so does a
        dirty directory above or below a changed path."""
        changed = self.changed_paths(old, new)
        if not changed:
            return Overlap([], [])
        blocking: set[str] = set()
        candidates: set[str] = set()
        changed_set = set(changed)
        for d in self.dirty_paths(worktree):
            if d in changed_set:
                candidates.add(d)
            for c in changed:
                if c.startswith(d + "/") or d.startswith(c + "/"):
                    blocking.add(d)
        old_e = self.entries(old, changed)
        new_e = self.entries(new, changed)
        for c in changed:
            if c not in old_e:
                p = worktree / c
                if p.exists() or p.is_symlink():
                    candidates.add(c)
        candidates -= blocking
        index_e = self.index_entries(worktree, sorted(candidates))
        present = sorted(c for c in candidates if (worktree / c).is_file() and not (worktree / c).is_symlink())
        wt_hash = dict(zip(present, self.hash_files(worktree, present)))
        same: list[SamePath] = []
        for c in sorted(candidates):
            s = _same_path(worktree, c, old_e.get(c), new_e.get(c), index_e.get(c, ()), wt_hash.get(c))
            if s is None:
                blocking.add(c)
            else:
                same.append(s)
        return Overlap(sorted(blocking), same)

    def check_advance(self, ref: str, new: Optional[str] = None, old: Optional[str] = None) -> Optional["AdvancePlan"]:
        """The plan for moving a branch checked out in some worktree (None when it is not
        checked out). With ``new``/``old``, raise :class:`Refused` listing every blocking
        path. Call before any ref moves."""
        holder = self.holders().get(ref)
        if holder is None:
            return None
        if new is None or old is None:
            return AdvancePlan(holder, [])
        ov = self.overlap(holder.path, old, new)
        if ov.blocking:
            raise Refused(_overlap_message(ref, holder, ov.blocking))
        return AdvancePlan(holder, ov.same)

    def advance(self, ref: str, new: str, old: str, plan: Optional["AdvancePlan"] = None) -> str:
        """Move a branch that may be checked out. ``new`` must descend from ``old``.

        Not checked out: CAS ``update-ref``. Checked out: paths that already hold what the
        move brings are first normalized (tracked ones restored to HEAD, untracked ones
        removed, each checked before and after; on any surprise everything is put back and
        nothing moves), then ``git merge --ff-only`` runs in that worktree, so its index and
        files follow and those paths end up with the bytes they had. Refused when other
        local changes overlap the move (git's own check is the second guard). Returns
        ``"update-ref"`` or ``"merge"``."""
        if plan is None:
            plan = self.check_advance(ref, new, old)
        if plan is None:
            self.update_refs([RefUpdate(ref, new, old)])
            return "update-ref"
        holder = plan.holder
        head = self.rev(ref)
        if head != old:
            raise CASConflict(f"{short_ref(ref)} moved (expected {old[:10]}, found {str(head)[:10]})")
        done = self.normalize(holder.path, plan.same)
        proc = self.run_bytes("merge", "--ff-only", "--quiet", new, cwd=holder.path, check=False)
        if proc.returncode != 0:
            failed = self.restore(holder.path, done)
            raise Refused(
                f"could not fast-forward {short_ref(ref)} at {holder.path}: "
                + proc.stderr.decode("utf-8", "replace").strip() + _not_restored(failed)
            )
        return "merge"

    def normalize(self, worktree: Path, same: Sequence["SamePath"]) -> list["_Saved"]:
        """Bring each path back to HEAD (tracked) or remove it (untracked) so that a
        fast-forward can write the landed version. Each path is re-checked right before
        and after; on any mismatch the paths done so far are restored and
        :class:`Refused` is raised."""
        done: list[_Saved] = []
        try:
            for s in same:
                p = worktree / s.path
                if self._state(worktree, s.path) != (s.index, s.worktree):
                    raise Refused(f"{s.path} changed while landing; nothing was moved, run the command again")
                done.append(_Saved(s, p.read_bytes() if s.worktree is not None else None))
                if s.head is not None:
                    self.run_bytes("--literal-pathspecs", "checkout", "HEAD", "--", s.path, cwd=worktree)
                    want = (s.head, s.head[1])
                else:
                    if s.index is not None:
                        self.run_bytes("--literal-pathspecs", "update-index", "--force-remove", "--", s.path, cwd=worktree)
                    if p.exists():
                        p.unlink()
                    want = (None, None)
                if self._state(worktree, s.path) != want:
                    raise Refused(f"could not normalize {s.path} before landing; nothing was moved")
        except (Refused, GitError, OSError) as exc:
            reason = str(exc) if isinstance(exc, Refused) else f"could not normalize before landing ({exc}); nothing was moved"
            raise Refused(reason + _not_restored(self.restore(worktree, done))) from None
        except BaseException:
            self.restore(worktree, done)
            raise
        return done

    def restore(self, worktree: Path, done: Sequence["_Saved"]) -> list[str]:
        """Undo :meth:`normalize` for ``done`` (latest first): put back the saved bytes and
        index entries of paths that are still as normalization left them. Returns the
        paths that could not be put back (someone changed them meanwhile)."""
        failed: list[str] = []
        for saved in reversed(done):
            s = saved.same
            p = worktree / s.path
            left = (s.head, s.head[1]) if s.head is not None else (None, None)
            try:
                if self._state(worktree, s.path) != left:
                    failed.append(s.path)
                    continue
                if saved.data is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(saved.data)
                elif p.exists():
                    p.unlink()
                if s.index != left[0]:
                    if s.index is None:
                        self.run_bytes("--literal-pathspecs", "update-index", "--force-remove", "--", s.path, cwd=worktree)
                    else:
                        info = f"{s.index[0]},{s.index[1]},{s.path}"
                        self.run_bytes("update-index", "--add", "--cacheinfo", info, cwd=worktree)
            except (OSError, GitError):
                failed.append(s.path)
        return failed

    def _state(self, worktree: Path, path: str) -> tuple[Optional[tuple[str, str]], Optional[str]]:
        """``(index entry, clean-filter hash of the file)`` of one path (None when absent)."""
        p = worktree / path
        entries = self.index_entries(worktree, [path])
        index = entries.get(path) if path not in entries or entries[path] is not None else ("<conflict>", "")
        if p.is_file() and not p.is_symlink():
            return index, self.hash_files(worktree, [path])[0]
        return index, ("<not a file>" if p.exists() or p.is_symlink() else None)

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
