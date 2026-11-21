"""Plain data as a Lua table literal, for the bridge tool's jobs (REQ-REN-03).

Renoise's Lua has no JSON reader, so jobs are written as Lua data. The tool
loads them with an empty environment, so a job can only hold values.

Renoise's Lua is LuaJIT, which won't load a function that holds more than
65,536 constants, and each table and value in a table literal can be one. A
whole track's job held more as one table, and Renoise refused it before
building anything (2026-11-21). So a big table's costliest parts each go in
a function of their own, ``(function() return {...} end)()``, and a list too
long for one function is put together from groups that each fit in one.
Neither needs anything from the environment.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
KEYWORDS = frozenset(
    [
        "and",
        "break",
        "do",
        "else",
        "elseif",
        "end",
        "false",
        "for",
        "function",
        "if",
        "in",
        "local",
        "nil",
        "not",
        "or",
        "repeat",
        "return",
        "then",
        "true",
        "until",
        "while",
    ]
)

type Data = bool | int | float | str | Sequence["Data"] | Mapping[str, "Data"] | None

LIMIT = 60_000  # constants one function may hold here; LuaJIT stops at 65,536
SMALL = 64  # a list's items this cheap are grouped, never put in functions one by one


@dataclass(frozen=True, slots=True)
class _Lua:
    """A value's Lua text, and how many constants it adds to the function it's
    in, counted generously: every table and every value counts once."""

    text: str
    cost: int


def quote(text: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if char in '"\\':
            out.append("\\" + char)
        elif code < 32 or code == 127:  # noqa: PLR2004 - control characters
            out.append(f"\\{code:03d}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _scalar(value: bool | int | float | str | None) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"can't write {value} as Lua data")
    if isinstance(value, (int, float)):
        return repr(value)
    return quote(value)


def _key(name: str) -> str:
    return name if NAME.match(name) and name not in KEYWORDS else f"[{quote(name)}]"


def encode(value: Data, indent: int = 0) -> str:
    return _encode(value, indent).text


def document(value: Data) -> str:
    return "return " + encode(value) + "\n"


def _encode(value: Data, indent: int) -> _Lua:
    if value is None or isinstance(value, (bool, int, float, str)):
        return _Lua(_scalar(value), 1)
    if isinstance(value, Mapping):
        keys = [f"{_key(k)} = " for k in value]
        room = LIMIT - 1 - len(keys)
        parts = _fit([_encode(v, indent + 1) for v in value.values()], room, 1)
        if _cost(parts) > room:
            raise ValueError(f"a table with {len(keys)} keys is too big to write as Lua data")
        return _Lua(_table(keys, parts, indent), 1 + len(keys) + _cost(parts))
    if value and len(value) < LIMIT and all(isinstance(item, (int, float)) for item in value):
        return _Lua("{" + ", ".join(encode(item) for item in value) + "}", 1 + len(value))
    parts = _fit([_encode(item, indent + 1) for item in value], LIMIT - 1, SMALL)
    if _cost(parts) > LIMIT - 1:
        return _grouped(parts, indent)
    return _Lua(_table([""] * len(parts), parts, indent), 1 + _cost(parts))


def _cost(parts: Sequence[_Lua]) -> int:
    return sum(part.cost for part in parts)


def _fit(parts: list[_Lua], room: int, small: int) -> list[_Lua]:
    """Puts the costliest parts in functions of their own until the rest fit in
    ``room``, leaving any that cost ``small`` or less where they are."""
    total = _cost(parts)
    for k in sorted(range(len(parts)), key=lambda k: parts[k].cost, reverse=True):
        if total <= room or parts[k].cost <= small:
            break
        total -= parts[k].cost - 1
        parts[k] = _Lua(f"(function() return {parts[k].text} end)()", 1)
    return parts


def _grouped(parts: list[_Lua], indent: int) -> _Lua:
    """A list too long for one function, put together from groups of its items
    that each fit in one."""
    groups: list[list[_Lua]] = [[]]
    used = 0
    for part in parts:
        if groups[-1] and used + part.cost > LIMIT - 1:
            groups.append([])
            used = 0
        groups[-1].append(part)
        used += part.cost
    inner = "  " * (indent + 1)
    lines, done = ["(function()", f"{inner}local p"], 0
    for group in groups:
        call = "(function() return " + _table([""] * len(group), group, indent + 1) + " end)()"
        if done == 0:
            lines.append(f"{inner}local t = {call}")
        else:
            lines.append(f"{inner}p = {call}")
            lines.append(f"{inner}for i = 1, {len(group)} do t[{done} + i] = p[i] end")
        done += len(group)
    return _Lua("\n".join([*lines, f"{inner}return t", "  " * indent + "end)()"]), 1)


def _table(keys: Sequence[str], parts: Sequence[_Lua], indent: int) -> str:
    if not parts:
        return "{}"
    pad, close = "  " * (indent + 1), "  " * indent
    items = [f"{pad}{key}{part.text}," for key, part in zip(keys, parts, strict=True)]
    return "{\n" + "\n".join(items) + "\n" + close + "}"
