from datetime import date

import pytest

from app.session import Session, SessionError, list_sessions, slugify
from helpers import make_xlsx, new_session, png_bytes


def test_create_initial_status_and_files(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    assert [c["status"] for c in s.data["cases"]] == ["Pass", "Skip", "Fail"]
    assert s.data["cases"][1]["remark"] == "Bypass"
    assert (s.root / "source.xlsx").exists()
    assert (s.root / "state.json").exists()
    assert s.root.name == f"{date.today():%Y-%m-%d}_android_1-2-4_regresi"
    assert s.data["id"] == s.root.name


def test_second_session_same_name_gets_suffix(tmp_path, xlsx):
    a = new_session(tmp_path, xlsx)
    b = new_session(tmp_path, xlsx)
    assert b.root.name == a.root.name + "-2"


def test_headings_restart_per_module(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    assert s.headings() == {
        "TC01": "TC001 - User input username valid",
        "TC02": "TC002 - User input username invalid",
        "TC03": "TC001 - User Transfer Bank A to Bank A",
    }


def test_add_image_requires_active(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    assert s.add_image(png_bytes()) is None
    s.set_active("TC02")
    assert s.add_image(png_bytes()) == "login_tc02_1.png"
    assert s.add_image(png_bytes(), ".JPG") == "login_tc02_2.jpg"
    assert s.case("TC02")["images"] == ["login_tc02_1.png", "login_tc02_2.jpg"]
    assert (s.shots / "login_tc02_1.png").exists()


def test_delete_move_reorder(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_active("TC01")
    a = s.add_image(png_bytes())
    b = s.add_image(png_bytes())
    s.reorder_images("TC01", [b, a])
    assert s.case("TC01")["images"] == [b, a]

    moved = s.move_image("TC01", a, "TC03")
    assert moved == "transfer_tc03_1.png"
    assert (s.shots / moved).exists() and not (s.shots / a).exists()
    assert s.case("TC03")["images"] == [moved]

    s.delete_image("TC01", b)
    assert not (s.shots / b).exists()
    assert (s.root / "trash" / b).exists()
    assert s.case("TC01")["images"] == []

    with pytest.raises(SessionError):
        s.reorder_images("TC03", ["nope.png"])
    with pytest.raises(SessionError):
        s.delete_image("TC01", "nope.png")


def test_status_remark_validation(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    with pytest.raises(SessionError):
        s.set_status("TC01", "Done")
    with pytest.raises(SessionError):
        s.set_active("TC99")


def test_persist_and_reload(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_status("TC01", "Fail")
    s.set_remark("TC01", "server error")
    s.set_active("TC03")
    again = Session.open(s.root)
    assert again.case("TC01")["status"] == "Fail"
    assert again.case("TC01")["remark"] == "server error"
    assert again.data["active"] == "TC03"


def test_corrupt_state_and_restore(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_status("TC01", "Fail")
    (s.root / "state.json").write_text("{broken", "utf-8")
    with pytest.raises(SessionError, match="rusak"):
        Session.open(s.root)
    restored = Session.restore_backup(s.root)
    assert restored.case("TC01")["status"] in ("Pass", "Fail")


def test_progress(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    assert s.progress() == {"done": 3, "total": 3}
    s.set_status("TC01", "Todo")
    assert s.progress() == {"done": 2, "total": 3}


def test_list_sessions(tmp_path, xlsx):
    a = new_session(tmp_path, xlsx)
    broken = tmp_path / "sessions" / "broken"
    broken.mkdir()
    (broken / "state.json").write_text("{", "utf-8")
    (tmp_path / "sessions" / "_uploads").mkdir()
    items = {i["id"]: i for i in list_sessions(tmp_path / "sessions")}
    assert set(items) == {a.root.name, "broken"}
    assert items["broken"]["broken"] is True
    assert items[a.root.name]["done"] == 3


def test_slugify():
    assert slugify("Sender dan Receiver/QR") == "sender-dan-receiver-qr"
    assert slugify("台灣") == "x"
    assert slugify("台灣", "module") == "module"


def test_image_names_safe_for_non_ascii_modules(tmp_path):
    rows = [["TC01", "M", "台灣", "A"], ["TC02", "M", "QR/Merchant 台灣", "B"]]
    path = make_xlsx(tmp_path / "u.xlsx", rows)
    s = new_session(tmp_path, path)
    s.set_active("TC01")
    assert s.add_image(png_bytes()) == "module_tc01_1.png"
    s.set_active("TC02")
    assert s.add_image(png_bytes()) == "qr-merchant_tc02_1.png"


def test_deleted_or_moved_image_names_are_never_reused(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_active("TC01")
    first = s.add_image(png_bytes(), ".jpg")
    second = s.add_image(png_bytes(), ".jpg")
    s.delete_image("TC01", first)
    s.move_image("TC01", second, "TC02")
    third = s.add_image(png_bytes(), ".jpg")
    assert third not in (first, second)
    assert third.endswith("_3.jpg")
    reopened = Session.open(s.root)  # survives a restart
    reopened.set_active("TC01")
    assert reopened.add_image(png_bytes(), ".jpg").endswith("_4.jpg")
