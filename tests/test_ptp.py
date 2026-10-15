import calendar
import struct
import time

from app import ptp


def ptp_string(text: str) -> bytes:
    if not text:
        return b"\x00"
    chars = text + "\x00"
    return bytes([len(chars)]) + chars.encode("utf-16-le")


def object_info(name: str, parent: int, size: int, captured: str, modified: str = "") -> bytes:
    head = bytearray(52)
    struct.pack_into("<I", head, 8, size)
    struct.pack_into("<I", head, 38, parent)
    return bytes(head) + ptp_string(name) + ptp_string(captured) + ptp_string(modified) + ptp_string("")


def test_command_packs_header_and_params():
    data = ptp.command(ptp.GET_OBJECT_INFO, 21454)
    assert data == struct.pack("<IHHII", 16, 1, 0x1008, 0, 21454)


def test_parse_event_object_added():
    raw = struct.pack("<IHHII", 16, 4, 0x4002, 7, 21454)
    assert ptp.parse_event(raw) == (ptp.OBJECT_ADDED, [21454])
    assert ptp.parse_event(b"\x00" * 4) is None


def test_response_ok():
    assert ptp.response_ok(struct.pack("<IHHI", 12, 3, 0x2001, 9))
    assert not ptp.response_ok(struct.pack("<IHHI", 12, 3, 0x2009, 9))  # invalid object handle
    assert not ptp.response_ok(None)


def test_parse_object_info_real_screenshot():
    raw = object_info("Screenshot_2026-10-15-23-30-50-592_com.example.app.jpg", 19866, 568181,
                      "20261015T233050")
    info = ptp.parse_object_info(raw)
    assert info.name == "Screenshot_2026-10-15-23-30-50-592_com.example.app.jpg"
    assert info.parent == 19866
    assert info.size == 568181
    assert info.captured == time.mktime((2026, 10, 15, 23, 30, 50, 0, 0, -1))
    assert info.captured_raw == "20261015T233050"


def test_parse_object_info_falls_back_to_modification_date():
    info = ptp.parse_object_info(object_info("a.jpg", 1, 10, "", "20261015T233050Z"))
    assert info.captured == calendar.timegm((2026, 10, 15, 23, 30, 50, 0, 0, 0))


def test_parse_date_variants():
    utc = calendar.timegm((2026, 10, 15, 16, 30, 50, 0, 0, 0))
    assert ptp.parse_date("20261015T163050Z") == utc
    assert ptp.parse_date("20261015T163050.5Z") == utc
    assert ptp.parse_date("20261015T233050+0700") == utc
    assert ptp.parse_date("") is None
    assert ptp.parse_date("garbage") is None
