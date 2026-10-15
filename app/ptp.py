"""Minimal PTP/MTP packet helpers for talking to the phone through ImageCaptureCore's PTP passthrough.

macOS reads the phone's whole file list (~20k files: ±1–2 minutes) after every USB (re)connect before
ImageCaptureCore reports any file. The phone's own "object added" events and direct GetObjectInfo /
GetObject commands work long before that, so new screenshots can be fetched without waiting.
"""
from __future__ import annotations

import calendar
import struct
import time
from dataclasses import dataclass

# Operation codes
GET_OBJECT_INFO = 0x1008
GET_OBJECT = 0x1009
# Event codes
OBJECT_ADDED = 0x4002
OBJECT_REMOVED = 0x4003
OBJECT_INFO_CHANGED = 0x4007
OBJECT_EVENTS = (OBJECT_ADDED, OBJECT_REMOVED, OBJECT_INFO_CHANGED)  # the phone exposes its files
# Response codes
OK = 0x2001

_HEADER = struct.Struct("<IHHI")  # length, container type, code, transaction id
COMMAND_BLOCK = 1
FULL_SIZE_UNKNOWN = 0xFFFFFFFF  # files ≥4 GB report this as their compressed size


def command(code: int, *params: int) -> bytes:
    """A PTP command container; ImageCaptureCore fills in the transaction id."""
    body = b"".join(struct.pack("<I", p) for p in params)
    return _HEADER.pack(_HEADER.size + len(body), COMMAND_BLOCK, code, 0) + body


def parse_event(data: bytes) -> tuple[int, list[int]] | None:
    if len(data) < _HEADER.size:
        return None
    _length, _kind, code, _tid = _HEADER.unpack_from(data)
    count = (len(data) - _HEADER.size) // 4
    return code, [struct.unpack_from("<I", data, _HEADER.size + 4 * i)[0] for i in range(count)]


def response_ok(data: bytes | None) -> bool:
    return data is not None and len(data) >= _HEADER.size and _HEADER.unpack_from(data)[2] == OK


@dataclass
class ObjectInfo:
    name: str
    parent: int
    size: int
    captured: float | None  # epoch seconds, None when the phone sent no usable date
    captured_raw: str


def _string(data: bytes, offset: int) -> tuple[str, int]:
    """PTP string: one byte with the character count (incl. NUL), then UTF-16LE characters."""
    count = data[offset]
    offset += 1
    text = data[offset:offset + 2 * count].decode("utf-16-le", errors="replace").rstrip("\x00")
    return text, offset + 2 * count


def parse_date(text: str) -> float | None:
    """PTP DateTime 'YYYYMMDDThhmmss[.s][Z|±hhmm]'; without a zone it is the phone's local time."""
    if len(text) < 15 or text[8] != "T":
        return None
    try:
        parts = time.strptime(text[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    rest = text[15:]
    if "." in rest[:1]:
        rest = rest[1:].lstrip("0123456789")
    if rest == "Z":
        return float(calendar.timegm(parts))
    if len(rest) == 5 and rest[0] in "+-" and rest[1:].isdigit():
        offset = (int(rest[1:3]) * 60 + int(rest[3:5])) * 60 * (1 if rest[0] == "+" else -1)
        return float(calendar.timegm(parts) - offset)
    return float(time.mktime(parts))


def parse_object_info(data: bytes) -> ObjectInfo:
    """ObjectInfo dataset: fixed 52-byte header, then Filename, CaptureDate, ModificationDate strings."""
    size = struct.unpack_from("<I", data, 8)[0]
    parent = struct.unpack_from("<I", data, 38)[0]
    name, offset = _string(data, 52)
    captured_raw, offset = _string(data, offset)
    if not captured_raw and offset < len(data):
        captured_raw, _ = _string(data, offset)  # fall back to ModificationDate
    return ObjectInfo(name, parent, size, parse_date(captured_raw), captured_raw)
