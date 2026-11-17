"""A deliberately narrow YAML reader for Markdown frontmatter (stdlib only).

The engine must not depend on third-party packages, but the ``contexts/`` navigation
index has to read the frontmatter exactly as PyYAML's ``safe_load`` would, so that the
generated ``INDEX.md`` files stay byte-for-byte the same as the ones the workspaces
already have.

Supported: a block mapping at the top; values that are plain scalars (also spread over
several lines), single- or double-quoted scalars (also multi-line), literal and folded
block scalars (``|``, ``>``, with ``-``/``+`` chomping), flow sequences of scalars on
one line, block sequences and nested block mappings.

Everything else raises :class:`Unsupported` naming the line: anchors, aliases, tags,
flow mappings, complex keys, tabs in indentation, explicit indentation indicators and
other shapes this reader does not know. It never guesses. Input that PyYAML itself
rejects in a way this reader can tell for certain (a plain scalar containing ``": "``,
characters YAML does not accept) raises :class:`Invalid`.

Plain scalars are returned as :class:`Plain` and resolved (null, bool, int, date) only
when a caller asks, with :func:`resolve`, because only a few keys are ever looked at.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["Invalid", "Unsupported", "Plain", "load", "resolve"]


class Invalid(ValueError):
    """PyYAML would reject this input as well."""


class Unsupported(ValueError):
    """The input uses YAML this reader does not handle."""


@dataclass(frozen=True)
class Plain:
    """A plain (unquoted) scalar, not yet resolved to a type."""

    text: str


#: The document's root is not a mapping (a scalar, a sequence or empty).
NOT_MAPPING = object()

_BREAK = re.compile(r"\r\n|\r|\n|\x85|\u2028|\u2029")
# PyYAML's reader refuses any of these characters anywhere in the stream.
_NON_PRINTABLE = re.compile("[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")
_BLOCK_HEADER = re.compile(r"^([|>])([-+]?)(\s+#.*)?\s*$")

# PyYAML's implicit resolvers (YAML 1.1).
_NULL = re.compile(r"^(?:~|null|Null|NULL|)$")
_BOOL_TRUE = {"yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON"}
_BOOL_FALSE = {"no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF"}
_INT = re.compile(
    r"^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+"
    r"|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$"
)
_DECIMAL = re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$")
_FLOAT = re.compile(
    r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9_]+(?:[eE][-+][0-9]+)?"
    r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
)
_DATE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
    r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?$"
)
# Characters that cannot start a plain scalar (PyYAML ``check_plain``).
_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"
_ESCAPES = {
    "0": "\0", "a": "\x07", "b": "\x08", "t": "\x09", "\t": "\x09", "n": "\x0a", "v": "\x0b",
    "f": "\x0c", "r": "\x0d", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
    "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029",
}
_ESCAPE_LEN = {"x": 2, "u": 4, "U": 8}


def load(text: str) -> Any:
    """Parse one YAML document. Returns a dict for a mapping root, or
    :data:`NOT_MAPPING` when the root is a scalar, a sequence or nothing at all."""
    if _NON_PRINTABLE.search(text):
        raise Invalid("character YAML does not accept")
    if "\ufeff" in text:
        raise Unsupported("byte order mark inside the frontmatter")
    if "\t" in text:
        raise Unsupported("tab character in the frontmatter")
    lines = _BREAK.split(text)
    final_break = len(lines) > 1 and lines[-1] == ""
    if final_break:
        lines.pop()  # the text after the last line break is not a line
    return _Parser(lines, final_break).document()


def resolve(value: Any) -> Any:
    """Resolve plain scalars (also inside lists and dicts) as PyYAML's safe loader would."""
    if isinstance(value, Plain):
        return _resolve_plain(value.text)
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    return value


def _resolve_plain(s: str) -> Any:
    if _NULL.match(s):
        return None
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    if _INT.match(s):
        if _DECIMAL.match(s):
            return int(s)
        raise Unsupported(f"non-decimal integer {s!r}")
    if _FLOAT.match(s):
        raise Unsupported(f"float {s!r}")
    m = _DATE.match(s)
    if m:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if _TIMESTAMP.match(s):
        raise Unsupported(f"timestamp {s!r}")
    return s


def _lint_plain(s: str, where: str) -> None:
    """Plain scalars PyYAML cannot construct (it raises, or crashes on a bad date)."""
    if s == "=":
        raise Unsupported(f"{where}: the value '='")
    m = _DATE.match(s)
    if m:
        try:
            _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            raise Unsupported(f"{where}: impossible date {s!r}") from None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank(line: str) -> bool:
    return line.strip(" \t") == ""


def _comment(line: str) -> bool:
    return line.lstrip(" \t").startswith("#")


def _key_split(content: str) -> Optional[tuple[str, str]]:
    """``(key, rest)`` when ``content`` starts a ``key: value`` pair with a plain key."""
    i = 0
    while True:
        i = content.find(":", i)
        if i < 0:
            return None
        if i + 1 == len(content) or content[i + 1] in " \t":
            break
        i += 1
    key = content[:i].rstrip(" ")
    rest = content[i + 1:]
    cut = re.search(r"[ \t]#", key)
    if cut:  # a comment came first: no key on this line
        return None
    return key, rest


def _strip_comment(s: str) -> str:
    """Drop a trailing `` #comment`` from a plain-scalar line."""
    m = re.search(r"[ \t]#", s)
    return s[: m.start()] if m else s


class _Parser:
    def __init__(self, lines: list[str], final_break: bool = True) -> None:
        self.lines = lines
        self.final_break = final_break
        self.i = 0

    # ---- line helpers -------------------------------------------------------

    def where(self, i: Optional[int] = None) -> str:
        return f"frontmatter line {(self.i if i is None else i) + 1}"

    def check_line(self, i: int) -> None:
        line = self.lines[i]
        lead = line[: len(line) - len(line.lstrip(" \t"))]
        if "\t" in lead and not _blank(line):
            raise Unsupported(f"{self.where(i)}: tab in indentation")
        if line.startswith("...") and (len(line) == 3 or line[3] in " \t"):
            raise Unsupported(f"{self.where(i)}: document end marker")

    def next_content(self, start: int) -> Optional[int]:
        """Index of the next line that is neither blank nor a comment."""
        j = start
        while j < len(self.lines):
            if not (_blank(self.lines[j]) or _comment(self.lines[j])):
                self.check_line(j)
                return j
            j += 1
        return None

    # ---- structure ----------------------------------------------------------

    def document(self) -> Any:
        j = self.next_content(0)
        if j is None:
            return NOT_MAPPING
        line = self.lines[j]
        if _indent(line) != 0:
            raise Unsupported(f"{self.where(j)}: indented first line")
        first = line[0]
        if first == "-" and (len(line) == 1 or line[1] in " \t"):
            return NOT_MAPPING  # a sequence (or an error): never a mapping
        if first in _INDICATORS and first not in "-":
            raise Unsupported(f"{self.where(j)}: line starts with {first!r}")
        if _key_split(line) is None:
            return NOT_MAPPING  # a scalar (or an error): never a mapping
        self.i = j
        result = self.mapping(0)
        k = self.next_content(self.i)
        if k is not None:
            raise Unsupported(f"{self.where(k)}: unexpected content")
        return result

    def mapping(self, n: int) -> dict:
        out: dict[str, Any] = {}
        while True:
            j = self.next_content(self.i)
            if j is None:
                self.i = len(self.lines)
                return out
            line = self.lines[j]
            ind = _indent(line)
            if ind < n:
                self.i = j
                return out
            if ind > n:
                raise Unsupported(f"{self.where(j)}: unexpected indentation")
            content = line[n:]
            if content[0] == "-" and (len(content) == 1 or content[1] in " \t"):
                raise Unsupported(f"{self.where(j)}: sequence entry inside a mapping")
            split = _key_split(content)
            if split is None:
                raise Unsupported(f"{self.where(j)}: expected 'key: value'")
            key, rest = split
            self.check_key(key, j)
            self.i = j + 1
            out[key] = self.value(rest, n, j)

    def check_key(self, key: str, j: int) -> None:
        if not key or key[0] in _INDICATORS or "\t" in key:
            raise Unsupported(f"{self.where(j)}: key {key!r}")
        if (key == "<<" or _NULL.match(key) or key in _BOOL_TRUE or key in _BOOL_FALSE or _INT.match(key)
                or _FLOAT.match(key) or _DATE.match(key) or _TIMESTAMP.match(key) or key == "="):
            raise Unsupported(f"{self.where(j)}: key {key!r} is not a plain string")

    def value(self, rest: str, n: int, j: int) -> Any:
        """The value after ``key:`` on line ``j`` of a collection at indent ``n``.
        ``self.i`` is the line after ``j``; on return it is the line after the value."""
        s = rest.strip(" \t")
        if s == "" or s.startswith("#"):
            k = self.next_content(self.i)
            if k is None:
                return None
            line = self.lines[k]
            ind = _indent(line)
            content = line[ind:]
            is_entry = content[0] == "-" and (len(content) == 1 or content[1] in " \t")
            if ind > n or (ind == n and is_entry):
                if is_entry:
                    self.i = k
                    return self.sequence(ind)
                if _key_split(content) is not None and content[0] not in "\"'[{":
                    self.i = k
                    return self.mapping(ind)
                self.i = k + 1
                return self.scalar(content, n, k)
            return None
        return self.scalar(s, n, j)

    def sequence(self, m: int) -> list:
        out: list[Any] = []
        while True:
            j = self.next_content(self.i)
            if j is None:
                self.i = len(self.lines)
                return out
            line = self.lines[j]
            ind = _indent(line)
            content = line[ind:]
            is_entry = content[0] == "-" and (len(content) == 1 or content[1] in " \t")
            if ind < m or (ind == m and not is_entry):
                self.i = j
                return out
            if ind > m or not is_entry:
                raise Unsupported(f"{self.where(j)}: unexpected line in a sequence")
            item = content[1:]
            body = item.lstrip(" ")
            if body == "" or body.startswith("#"):
                self.i = j + 1
                out.append(self.value("", m, j))
                continue
            if body[0] == "-" and (len(body) == 1 or body[1] in " \t"):
                raise Unsupported(f"{self.where(j)}: nested sequence on one line")
            col = m + 1 + (len(item) - len(body))
            if _key_split(body) is not None and body[0] not in "\"'[{":
                # "- key: value": a mapping whose indentation is the key's column.
                self.lines[j] = " " * col + body
                self.i = j
                out.append(self.mapping(col))
                continue
            self.i = j + 1
            out.append(self.scalar(body, m, j))

    # ---- scalars ------------------------------------------------------------

    def scalar(self, s: str, n: int, j: int) -> Any:
        """A scalar starting with ``s`` on line ``j``; continuation lines must be
        indented more than ``n``. ``self.i`` is the line after ``j``."""
        c = s[0]
        if c in "|>":
            return self.block_scalar(s, n, j)
        if c == '"':
            return self.quoted(s, j, double=True)
        if c == "'":
            return self.quoted(s, j, double=False)
        if c == "[":
            return self.flow_sequence(s, j)
        if c in "{&*!":
            raise Unsupported(f"{self.where(j)}: value starts with {c!r}")
        if c in "%@`" or (c in "-?:" and (len(s) == 1 or s[1] in " \t")):
            raise Unsupported(f"{self.where(j)}: value starts with {c!r}")
        return self.plain(s, n, j)

    def plain(self, s: str, n: int, j: int) -> Plain:
        parts: list[str] = []
        first = _strip_comment(s).rstrip(" \t")
        commented = first != s.rstrip(" \t")
        self.check_plain_line(first, j)
        parts.append(first)
        breaks = 0
        k = self.i
        while k < len(self.lines) and not commented:
            line = self.lines[k]
            if _blank(line):
                breaks += 1
                k += 1
                continue
            if _indent(line) <= n or _comment(line):
                break
            self.check_line(k)
            text = _strip_comment(line.strip(" \t"))
            commented = text != line.strip(" \t")
            text = text.rstrip(" \t")
            self.check_plain_line(text, k)
            parts.append("\n" * breaks if breaks else " ")
            parts.append(text)
            breaks = 0
            k += 1
            self.i = k
        if commented:
            nxt = self.next_content(self.i)
            if nxt is not None and _indent(self.lines[nxt]) > n:
                raise Unsupported(f"{self.where(nxt)}: text after a comment inside a value")
        value = "".join(parts)
        _lint_plain(value, self.where(j))
        return Plain(value)

    def check_plain_line(self, text: str, j: int) -> None:
        if re.search(r":(?:[ \t]|$)", text):
            raise Invalid(f"{self.where(j)}: mapping values are not allowed here")
        if text.startswith("#") or "\ufeff" in text:
            raise Unsupported(f"{self.where(j)}: plain scalar shape")

    def quoted(self, s: str, j: int, *, double: bool) -> str:
        """A single- or double-quoted scalar starting at ``s`` (line ``j``), possibly
        spread over several lines."""
        pieces: list[tuple[str, bool]] = []  # (text of one physical line, ends with an escaped break)
        buf: list[str] = []
        cur, pos, line_no = s, 1, j
        while True:
            escaped_break = False
            while pos < len(cur):
                ch = cur[pos]
                if ch == ("'" if not double else '"'):
                    if not double and cur[pos + 1: pos + 2] == "'":
                        buf.append("'")
                        pos += 2
                        continue
                    pieces.append(("".join(buf), False))
                    return self.fold_quoted(pieces, cur[pos + 1:], line_no)
                if double and ch == "\\":
                    if pos + 1 == len(cur):
                        escaped_break = True
                        break
                    pos = self.escape(cur, pos, buf, line_no)
                    continue
                buf.append(ch)
                pos += 1
            pieces.append(("".join(buf), escaped_break))
            buf = []
            line_no = self.i
            if line_no >= len(self.lines):
                raise Invalid(f"{self.where(j)}: unterminated quoted scalar")
            self.check_line(line_no)
            cur, pos = self.lines[line_no], 0
            self.i = line_no + 1

    def escape(self, cur: str, pos: int, buf: list[str], line_no: int) -> int:
        e = cur[pos + 1]
        if e in _ESCAPES:
            buf.append(_ESCAPES[e])
            return pos + 2
        if e in _ESCAPE_LEN:
            width = _ESCAPE_LEN[e]
            code = cur[pos + 2: pos + 2 + width]
            if len(code) != width or not re.fullmatch(r"[0-9A-Fa-f]+", code):
                raise Invalid(f"{self.where(line_no)}: bad escape")
            buf.append(chr(int(code, 16)))
            return pos + 2 + width
        raise Invalid(f"{self.where(line_no)}: unknown escape \\{e}")

    def fold_quoted(self, pieces: list[tuple[str, bool]], after: str, line_no: int) -> str:
        """Join the physical lines of a quoted scalar: trailing blanks of a line and leading
        blanks of the next are dropped, one line break becomes a space and each further
        empty line a newline; an escaped line break joins without anything."""
        tail = after.strip(" \t")
        if tail and not (tail.startswith("#") and after[:1] in " \t"):
            raise Unsupported(f"{self.where(line_no)}: text after a quoted scalar")
        if len(pieces) == 1:
            return pieces[0][0]
        first, joined = pieces[0]
        out = [first if joined else first.rstrip(" \t")]
        empties = 0
        for idx, (text, next_joined) in enumerate(pieces[1:], start=1):
            last = idx == len(pieces) - 1
            text = text.lstrip(" \t")
            if not last and not next_joined:
                text = text.rstrip(" \t")
            if text == "" and not last and not next_joined:
                empties += 1
                continue
            if not joined:
                out.append("\n" * empties if empties else " ")
            elif empties:
                out.append("\n" * empties)
            out.append(text)
            empties = 0
            joined = next_joined
        if empties:
            out.append("\n" * empties)
        return "".join(out)

    def flow_sequence(self, s: str, j: int) -> list:
        items: list[Any] = []
        pos = 1
        expect_item = True
        while True:
            while pos < len(s) and s[pos] in " \t":
                pos += 1
            if pos >= len(s):
                raise Unsupported(f"{self.where(j)}: flow sequence spans lines")
            ch = s[pos]
            if ch == "]":
                rest = s[pos + 1:].strip(" \t")
                if rest and not rest.startswith("#"):
                    raise Unsupported(f"{self.where(j)}: text after a flow sequence")
                if rest.startswith("#") and s[pos + 1:pos + 2] not in (" ", "\t"):
                    raise Unsupported(f"{self.where(j)}: text after a flow sequence")
                return items
            if ch == ",":
                if expect_item:
                    raise Unsupported(f"{self.where(j)}: empty flow sequence entry")
                expect_item = True
                pos += 1
                continue
            if not expect_item:
                raise Unsupported(f"{self.where(j)}: missing ',' in flow sequence")
            if ch in "[{&*!#%@`?" or (ch in "-:" and (pos + 1 >= len(s) or s[pos + 1] in " \t,[]{}")):
                raise Unsupported(f"{self.where(j)}: flow sequence entry starts with {ch!r}")
            if ch in "\"'":
                end, text = self.flow_quoted(s, pos, j)
                items.append(text)
                pos = end
            else:
                m = re.match(r"[^,\[\]{}]*", s[pos:])
                raw = m.group(0)
                cut = re.search(r"[ \t]#|:(?:[ \t,\[\]{}]|$)", raw)
                if cut:
                    raise Unsupported(f"{self.where(j)}: flow sequence entry shape")
                text = raw.rstrip(" \t")
                _lint_plain(text, self.where(j))
                items.append(Plain(text))
                pos += len(raw)
            expect_item = False

    def flow_quoted(self, s: str, pos: int, j: int) -> tuple[int, str]:
        q = s[pos]
        k = pos + 1
        buf: list[str] = []
        while k < len(s):
            ch = s[k]
            if q == "'" and ch == "'":
                if k + 1 < len(s) and s[k + 1] == "'":
                    buf.append("'")
                    k += 2
                    continue
                return k + 1, "".join(buf)
            if q == '"' and ch == '"':
                return k + 1, "".join(buf)
            if q == '"' and ch == "\\":
                e = s[k + 1: k + 2]
                if e in _ESCAPES:
                    buf.append(_ESCAPES[e])
                    k += 2
                    continue
                raise Unsupported(f"{self.where(j)}: escape in a flow sequence")
            buf.append(ch)
            k += 1
        raise Unsupported(f"{self.where(j)}: quoted entry spans lines")

    def block_scalar(self, s: str, n: int, j: int) -> str:
        m = _BLOCK_HEADER.match(s)
        if not m:
            raise Unsupported(f"{self.where(j)}: block scalar header {s!r}")
        style, chomp = m.group(1), m.group(2)
        lines: list[str] = []
        start = k = self.i
        indent: Optional[int] = None
        lead_max = 0
        while k < len(self.lines):
            line = self.lines[k]
            if _blank(line):
                if "\t" in line and indent is None:
                    raise Unsupported(f"{self.where(k)}: tab in a block scalar")
                if indent is None:
                    lead_max = max(lead_max, len(line))
                lines.append(line)
                k += 1
                continue
            ind = _indent(line)
            if indent is None:
                if ind <= n:
                    break
                if lead_max > ind:
                    raise Unsupported(f"{self.where(k)}: block scalar indentation")
                indent = ind
            elif ind < indent:
                break
            lines.append(line)
            k += 1
        # Trailing blank lines past the last content line belong to chomping only.
        self.i = k
        if indent is None:
            body: list[str] = []
            trailing = len(lines)
        else:
            last = max(i for i, ln in enumerate(lines) if not _blank(ln))
            trailing = len(lines) - last - 1
            body = [ln[indent:] if not _blank(ln) else "" for ln in lines[: last + 1]]
            for ln in lines[: last + 1]:
                if _blank(ln) and len(ln) > indent:
                    raise Unsupported(f"{self.where(j)}: whitespace-only line inside a block scalar")
        if style == "|":
            text = "\n".join(body)
        else:
            if any(b[:1] in (" ", "\t") for b in body):
                raise Unsupported(f"{self.where(j)}: more-indented line in a folded scalar")
            out: list[str] = []
            empties = 0
            for b in body:
                if b == "":
                    empties += 1
                    continue
                if out:
                    out.append("\n" * empties if empties else " ")
                elif empties:
                    out.append("\n" * empties)
                out.append(b)
                empties = 0
            text = "".join(out)
        if not body:
            return "\n" * trailing if chomp == "+" else ""
        if chomp == "-":
            return text
        # The last content line has a line break unless it ends the whole text.
        brk = "" if start + len(body) == len(self.lines) and not self.final_break else "\n"
        if chomp == "+":
            return text + brk + "\n" * trailing
        return text + brk
