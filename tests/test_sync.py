import pytest

from app import sync
from app.session import SessionError
from app.tcsource import load_sheet
from helpers import default_rows, make_xlsx, new_session, png_bytes


def v2_rows():
    rows = default_rows()
    rows[0][3] = "User input username VALID"
    del rows[1]  # TC02 hilang
    rows.append(["TC04", "Mobile", "Transfer", "New case", "Happy Flow", "", "", "Pass", "", "", ""])
    return rows


def test_diff_and_apply(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_active("TC02")
    s.add_image(png_bytes())
    path = make_xlsx(tmp_path / "v2.xlsx", v2_rows())
    sheet = load_sheet(path, "Regresi")

    assert sync.diff(s, sheet) == {
        "added": ["TC04"],
        "changed": [{"tc_no": "TC01", "fields": ["title"]}],
        "removed": ["TC02"],
    }

    sync.apply(s, sheet, path)
    tc02 = s.case("TC02")
    assert tc02["removed"] is True and tc02["images"] == ["login_tc02_1.png"]
    assert s.data["active"] is None
    assert s.case("TC01")["title"] == "User input username VALID"
    assert s.case("TC01")["status"] == "Pass"
    assert s.case("TC04")["status"] == "Pass"
    assert s.case("TC03")["row"] == 4
    assert "TC02" not in s.headings()
    assert s.headings()["TC04"] == "TC002 - New case"


def test_returning_tc_is_restored(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    v2 = make_xlsx(tmp_path / "v2.xlsx", v2_rows())
    sync.apply(s, load_sheet(v2, "Regresi"), v2)
    v3 = make_xlsx(tmp_path / "v3.xlsx", default_rows())
    result = sync.apply(s, load_sheet(v3, "Regresi"), v3)
    assert "TC02" in result["added"]
    assert s.case("TC02")["removed"] is False


def test_sheet_name_must_match(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    other = make_xlsx(tmp_path / "o.xlsx", default_rows(), sheet="Lain")
    with pytest.raises(SessionError, match="Sheet"):
        sync.diff(s, load_sheet(other, "Lain"))
