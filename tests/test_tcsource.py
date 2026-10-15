import pytest

from app.tcsource import SheetError, list_sheets, load_sheet, normalize_header
from helpers import make_xlsx

ROW = ["TC01", "M", "Login", "Scenario A", "", "", "", "Pass", "", "", ""]


def test_normalize_header():
    assert normalize_header("Result \nAndroid (Existing)") == "result android (existing)"
    assert normalize_header("No. TC") == "no tc"
    assert normalize_header(None) == ""


def test_list_sheets(xlsx):
    assert list_sheets(xlsx) == ["Regresi"]


def test_load_sheet_maps_columns(xlsx):
    data = load_sheet(xlsx, "Regresi")
    assert [c.tc_no for c in data.cases] == ["TC01", "TC02", "TC03"]
    assert [c.row for c in data.cases] == [2, 3, 5]
    assert [c.module for c in data.cases] == ["Login", "Login", "Transfer"]
    assert data.cases[2].title == "User Transfer Bank A to Bank A"
    assert data.cases[0].steps == "1. Login"
    assert data.cases[0].expected == "Home page"
    assert data.cases[0].type == "Happy Flow"
    assert data.cases[1].remark == "Bypass"
    assert data.result_columns == {
        "android": ["Result Android (Existing)", "Result Android (New)"],
        "ios": ["Result IOS"],
    }
    assert data.cases[1].results["Result Android (Existing)"] == "No Run"


def test_header_not_on_first_row(tmp_path):
    path = make_xlsx(tmp_path / "a.xlsx", [ROW], pre_rows=[["Judul dokumen"], [None]])
    assert load_sheet(path, "Regresi").cases[0].row == 4


def test_missing_required_column(tmp_path):
    path = make_xlsx(tmp_path / "b.xlsx", [["TC01", "x"]], header=["No TC", "Scenario"])
    with pytest.raises(SheetError, match="function"):
        load_sheet(path, "Regresi")


def test_header_not_found(tmp_path):
    path = make_xlsx(tmp_path / "c.xlsx", [["a", "b"]], header=["A", "B"])
    with pytest.raises(SheetError, match="No TC"):
        load_sheet(path, "Regresi")


def test_duplicate_tc_no(tmp_path):
    path = make_xlsx(tmp_path / "d.xlsx", [ROW, ROW])
    with pytest.raises(SheetError, match="TC01"):
        load_sheet(path, "Regresi")


def test_title_without_tc_no(tmp_path):
    path = make_xlsx(tmp_path / "e.xlsx", [[None, "M", "Login", "Scenario A"]])
    with pytest.raises(SheetError, match="Baris 2"):
        load_sheet(path, "Regresi")


def test_unknown_sheet(xlsx):
    with pytest.raises(SheetError, match="Nope"):
        load_sheet(xlsx, "Nope")


def test_numeric_tc_no(tmp_path):
    rows = [[1, "M", "Login", "A"], [2.0, "M", "Login", "B"]]
    path = make_xlsx(tmp_path / "f.xlsx", rows)
    assert [c.tc_no for c in load_sheet(path, "Regresi").cases] == ["1", "2"]


def test_missing_function_gets_placeholder(tmp_path):
    path = make_xlsx(tmp_path / "g.xlsx", [["TC01", "M", None, "A"]])
    assert load_sheet(path, "Regresi").cases[0].module == "(Tanpa Function)"
