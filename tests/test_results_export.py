import pytest

from app.results_export import export_column
from helpers import new_session


def test_result_column_aligned_with_rows(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    assert export_column(s.data, "result") == {"text": "Pass\nNo Run\n\nFail", "first_row": 2, "count": 4}


def test_remark_column(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    assert export_column(s.data, "remark")["text"] == "\nBypass\n\nTerjadi kesalahan"


def test_remark_sanitized_for_paste(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_remark("TC03", 'Error\tserver\n"timeout"  ')
    assert export_column(s.data, "remark")["text"].split("\n")[-1] == "Error server 'timeout'"


def test_removed_cases_excluded(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.case("TC03")["removed"] = True
    assert export_column(s.data, "result")["text"] == "Pass\nNo Run"


def test_empty_and_bad_kind(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    for c in s.data["cases"]:
        c["removed"] = True
    assert export_column(s.data, "result") == {"text": "", "first_row": None, "count": 0}
    with pytest.raises(ValueError):
        export_column(s.data, "status")
