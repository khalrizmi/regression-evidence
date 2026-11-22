"""Team TC sheets: Result dropdown = Todo, Execute, Passed, Failed, No Run, Takeout, Blocked."""
import json

import pytest

from app.docx_export import doc_layout, pre_generate_warnings
from app.results_export import export_column
from app.session import STATUSES, Session, SessionError, initial_status, list_sessions
from helpers import new_session, png_bytes

TEAM = ["Todo", "Execute", "Passed", "Failed", "No Run", "Takeout", "Blocked"]


def test_statuses_match_the_sheet_dropdown():
    assert list(STATUSES) == TEAM


@pytest.mark.parametrize("cell, status", [
    ("Passed", "Passed"), ("failed", "Failed"), (" No Run ", "No Run"), ("Takeout", "Takeout"),
    ("BLOCKED", "Blocked"), ("Execute", "Execute"), ("Todo", "Todo"), ("", "Todo"), ("?", "Todo"),
    ("Pass", "Passed"), ("Fail", "Failed"), ("Skip", "No Run"),  # older sheets
])
def test_initial_status_from_excel(cell, status):
    assert initial_status(cell) == status


def test_set_status_accepts_only_team_statuses(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    for status in TEAM:
        s.set_status("TC01", status)
        assert s.case("TC01")["status"] == status
    with pytest.raises(SessionError):
        s.set_status("TC01", "Pass")


def test_progress_counts_finished_statuses(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    for tc, status in (("TC01", "Todo"), ("TC02", "Execute"), ("TC03", "Takeout")):
        s.set_status(tc, status)
    assert s.progress() == {"done": 1, "total": 3}
    for status in ("Passed", "Failed", "No Run", "Blocked"):
        s.set_status("TC01", status)
        assert s.progress()["done"] == 2


def test_first_screenshot_moves_todo_to_execute(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_status("TC01", "Todo")
    s.set_status("TC03", "Todo")
    s.set_active("TC01")
    s.add_image(png_bytes())
    assert s.case("TC01")["status"] == "Execute"
    s.set_status("TC01", "Passed")
    s.add_image(png_bytes())
    assert s.case("TC01")["status"] == "Passed"  # only Todo moves
    s.copy_image("TC01", s.case("TC01")["images"][0], "TC03")
    assert s.case("TC03")["status"] == "Execute"


def test_old_sessions_are_migrated(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    data = json.loads((s.root / "state.json").read_text())
    for case, old in zip(data["cases"], ("Pass", "Skip", "Fail")):
        case["status"] = old
    (s.root / "state.json").write_text(json.dumps(data))
    assert [c["status"] for c in Session.open(s.root).data["cases"]] == ["Passed", "No Run", "Failed"]
    assert list_sessions(tmp_path / "sessions")[0]["done"] == 3


def test_copy_result_uses_the_dropdown_text(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    for tc, status in (("TC01", "Execute"), ("TC02", "Takeout"), ("TC03", "Blocked")):
        s.set_status(tc, status)
    assert export_column(s.data, "result")["text"] == "Execute\nTakeout\n\nBlocked"


def test_document_content_per_status(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    included = {}
    for status in TEAM:
        s.set_status("TC01", status)
        included[status] = any(c["tc_no"] == "TC01" for m in doc_layout(s) for c in m["cases"])
    assert included == {"Todo": False, "Execute": True, "Passed": True, "Failed": True,
                        "No Run": False, "Takeout": False, "Blocked": False}
    s.set_active("TC01")
    s.add_image(png_bytes())
    for status in ("Todo", "No Run", "Blocked"):
        s.set_status("TC01", status)
        assert any(c["tc_no"] == "TC01" for m in doc_layout(s) for c in m["cases"]), status
    s.set_status("TC01", "Takeout")  # out of scope: never in the document, even with pictures
    assert not any(c["tc_no"] == "TC01" for m in doc_layout(s) for c in m["cases"])


def test_warnings_before_generate(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_status("TC01", "Execute")
    s.set_status("TC02", "Blocked")
    s.set_remark("TC02", "")
    s.set_status("TC03", "Takeout")
    w = pre_generate_warnings(s)
    assert w["no_image"] == ["Login · TC001 - User input username valid"]
    assert w["execute"] == ["Login · TC001 - User input username valid"]
    assert w["blocked_no_remark"] == ["Login · TC002 - User input username invalid"]
    assert w["todo"] == []
