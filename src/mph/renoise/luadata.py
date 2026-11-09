"""Plain data as a Lua table literal, for the bridge tool's jobs (REQ-REN-03).

Renoise's Lua has no JSON reader, so jobs are written as Lua data. The tool
loads them with an empty environment, so a job can only hold values.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

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
    if value is None or isinstance(value, (bool, int, float, str)):
        return _scalar(value)
    pad, close = "  " * (indent + 1), "  " * indent
    if isinstance(value, Mapping):
        items = [f"{pad}{_key(k)} = {encode(v, indent + 1)}," for k, v in value.items()]
    elif all(isinstance(item, (int, float)) for item in value):
        return "{" + ", ".join(encode(item) for item in value) + "}"
    else:
        items = [f"{pad}{encode(item, indent + 1)}," for item in value]
    return "{\n" + "\n".join(items) + "\n" + close + "}" if items else "{}"


def document(value: Data) -> str:
    return "return " + encode(value) + "\n"
