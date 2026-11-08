"""Open Sound Control encoding for SuperCollider's non-realtime mode.

An NRT score is a file of OSC bundles, each prefixed by its length, with
time tags in seconds (32.32 fixed point). Only the four argument types
scsynth commands need are supported: int32, float32, string and blob.
``decode_score`` reads a score back, which tests and debugging use.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

Arg = int | float | str | bytes
FRACTION = 1 << 32


@dataclass(frozen=True, slots=True)
class Message:
    address: str
    args: tuple[Arg, ...] = ()


@dataclass(frozen=True, slots=True)
class Bundle:
    time_s: float
    messages: tuple[Message, ...]


def _pad(data: bytes) -> bytes:
    return data + b"\0" * (4 - len(data) % 4)


def _string(text: str) -> bytes:
    return _pad(text.encode("utf-8"))


def _blob(data: bytes) -> bytes:
    body = data + b"\0" * (-len(data) % 4)
    return struct.pack(">i", len(data)) + body


def encode_message(message: Message) -> bytes:
    tags, payload = ",", b""
    for arg in message.args:
        if isinstance(arg, bool):  # bool is an int subclass; refuse it explicitly
            raise TypeError("OSC has no bool here; pass 0 or 1")
        if isinstance(arg, int):
            tags, payload = tags + "i", payload + struct.pack(">i", arg)
        elif isinstance(arg, float):
            tags, payload = tags + "f", payload + struct.pack(">f", arg)
        elif isinstance(arg, str):
            tags, payload = tags + "s", payload + _string(arg)
        else:
            tags, payload = tags + "b", payload + _blob(arg)
    return _string(message.address) + _string(tags) + payload


def timetag(seconds: float) -> bytes:
    whole = int(seconds)
    return struct.pack(">II", whole, round((seconds - whole) * FRACTION))


def encode_bundle(bundle: Bundle) -> bytes:
    body = b"#bundle\0" + timetag(bundle.time_s)
    for message in bundle.messages:
        encoded = encode_message(message)
        body += struct.pack(">i", len(encoded)) + encoded
    return body


def encode_score(bundles: Sequence[Bundle]) -> bytes:
    out = bytearray()
    for bundle in sorted(bundles, key=lambda b: b.time_s):  # stable: keeps message order
        encoded = encode_bundle(bundle)
        out += struct.pack(">i", len(encoded)) + encoded
    return bytes(out)


# --- decoding (tests, debugging) ----------------------------------------------------------


def _read_string(data: bytes, at: int) -> tuple[str, int]:
    end = data.index(b"\0", at)
    return data[at:end].decode("utf-8"), at + (end - at) // 4 * 4 + 4


def decode_message(data: bytes) -> Message:
    address, at = _read_string(data, 0)
    tags, at = _read_string(data, at)
    args: list[Arg] = []
    for tag in tags[1:]:
        if tag == "i":
            args.append(struct.unpack_from(">i", data, at)[0])
            at += 4
        elif tag == "f":
            args.append(struct.unpack_from(">f", data, at)[0])
            at += 4
        elif tag == "s":
            text, at = _read_string(data, at)
            args.append(text)
        else:
            (size,) = struct.unpack_from(">i", data, at)
            args.append(data[at + 4 : at + 4 + size])
            at += 4 + size + (-size % 4)
    return Message(address, tuple(args))


def decode_bundle(data: bytes) -> Bundle:
    whole, fraction = struct.unpack_from(">II", data, 8)
    messages, at = [], 16
    while at < len(data):
        (size,) = struct.unpack_from(">i", data, at)
        messages.append(decode_message(data[at + 4 : at + 4 + size]))
        at += 4 + size
    return Bundle(whole + fraction / FRACTION, tuple(messages))


def decode_score(data: bytes) -> list[Bundle]:
    bundles, at = [], 0
    while at < len(data):
        (size,) = struct.unpack_from(">i", data, at)
        bundles.append(decode_bundle(data[at + 4 : at + 4 + size]))
        at += 4 + size
    return bundles
