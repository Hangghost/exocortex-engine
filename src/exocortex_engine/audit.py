"""Health-check findings for ``exocortex_engine.day survey``.

A rule is a function ``check(ctx) -> Iterable[Finding]`` registered under a kebab-case
name. ``run_all(ctx)`` runs every registered rule in registration order; a rule that
raises yields one ``severity: error`` finding named after it and the other rules still
run. This module registers **no** rules: an empty ``findings`` list is a valid result,
and ``rule_count() == 0`` ("no rules registered") must read differently from "rules ran,
nothing found". Rules are added as one ``register`` call plus one unit test each, without
changing ``survey`` or the command prose.

JSON form of a finding (``Finding.to_dict``)::

    {"rule": "kebab-case-name", "severity": "info|warn|error",
     "subject": {"kind": "path|branch|project|ref", "id": "..."},
     "message": "one line",
     "suggest": {"command": "copyable command", "mutates": true} | null}

A ``suggest`` with ``mutates: true`` is printed, never run automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

SEVERITIES = ("info", "warn", "error")
SUBJECT_KINDS = ("path", "branch", "project", "ref")
_RULE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Subject:
    kind: str  # path | branch | project | ref
    id: str

    def __post_init__(self) -> None:
        if self.kind not in SUBJECT_KINDS:
            raise ValueError(f"subject kind must be one of {', '.join(SUBJECT_KINDS)}: {self.kind!r}")


@dataclass(frozen=True)
class Suggest:
    command: str
    mutates: bool  # required: the presentation layer must not guess

    def __post_init__(self) -> None:
        if not isinstance(self.mutates, bool):
            raise ValueError("suggest.mutates must be a bool")
        if not self.command.strip():
            raise ValueError("suggest.command must not be empty")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # info | warn | error
    subject: Subject
    message: str
    suggest: Optional[Suggest] = None

    def __post_init__(self) -> None:
        if not _RULE_RE.match(self.rule):
            raise ValueError(f"rule name must be kebab-case: {self.rule!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}: {self.severity!r}")
        if not isinstance(self.subject, Subject):
            raise ValueError("subject must be a Subject")
        if "\n" in self.message or not self.message.strip():
            raise ValueError("message must be one non-empty line")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "subject": {"kind": self.subject.kind, "id": self.subject.id},
            "message": self.message,
            "suggest": (
                {"command": self.suggest.command, "mutates": self.suggest.mutates} if self.suggest else None
            ),
        }


Check = Callable[[Any], Iterable[Finding]]


class Registry:
    """Named rules in registration order."""

    def __init__(self) -> None:
        self._rules: dict[str, Check] = {}

    def register(self, rule_name: str, check: Check) -> None:
        if not _RULE_RE.match(rule_name):
            raise ValueError(f"rule name must be kebab-case: {rule_name!r}")
        if rule_name in self._rules:
            raise ValueError(f"rule already registered: {rule_name}")
        if not callable(check):
            raise ValueError(f"check for {rule_name} is not callable")
        self._rules[rule_name] = check

    def names(self) -> list[str]:
        return list(self._rules)

    def __len__(self) -> int:
        return len(self._rules)

    def run_all(self, ctx: Any) -> list[Finding]:
        findings: list[Finding] = []
        for name, check in self._rules.items():
            try:
                got = list(check(ctx) or ())
                bad = [f for f in got if not isinstance(f, Finding) or f.rule != name]
                if bad:
                    raise TypeError(f"returned {len(bad)} item(s) that are not Findings of rule {name}")
            except Exception as exc:  # isolate: one broken rule never stops the others
                first = (str(exc).strip().splitlines() or [""])[0]
                findings.append(Finding(
                    rule=name, severity="error", subject=Subject("ref", _subject_ref(ctx)),
                    message=f"rule failed: {type(exc).__name__}{': ' + first if first else ''}",
                ))
                continue
            findings.extend(got)
        return findings


def _subject_ref(ctx: Any) -> str:
    """The ref a crashed rule was measured against (the main branch), for its subject."""
    main = getattr(ctx, "main", None)
    if isinstance(main, str) and main:
        return main[len("refs/heads/"):] if main.startswith("refs/heads/") else main
    return "HEAD"


#: The registry ``survey`` reads. Empty in this release.
REGISTRY = Registry()


def register(rule_name: str, check: Check) -> None:
    REGISTRY.register(rule_name, check)


def run_all(ctx: Any) -> list[Finding]:
    return REGISTRY.run_all(ctx)


def rule_count() -> int:
    return len(REGISTRY)
