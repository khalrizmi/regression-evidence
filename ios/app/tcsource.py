"""Baca daftar TC dari .xlsx (kolom: No TC, Function, Scenario, ...).

Menyediakan SheetData / TestCase yang dipakai session.py dan sync.py (sama dengan versi Android).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import openpyxl
from openpyxl.utils import get_column_letter


class TCSourceError(Exception):
    pass


@dataclass
class TestCase:
    __test__ = False  # bukan test pytest
    tc_no: str
    row: int
    module: str
    title: str
    type: str = ""
    expected: str = ""
    steps: str = ""
    results: dict = field(default_factory=dict)  # nama kolom Result -> isi sel
    remark: str = ""


@dataclass
class SheetData:
    sheet: str
    header_row: int
    result_columns: dict  # nama kolom Result -> huruf kolom Excel
    remark_letter: str | None
    cases: list


def _n(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


FUNCTION_NAMES = ("function", "function module")


def _header(ws):
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), start=1):
        names = [_n(c).lower() for c in row]
        if "no tc" in names:
            return i, list(row)
        # A1 kadang tertimpa teks lain: header tetap dikenali dari kolom Scenario + Function (Module)
        if "scenario" in names and any(f in names for f in FUNCTION_NAMES):
            return i, list(row)
    raise TCSourceError("Baris header tidak ditemukan (harus ada kolom 'No TC' di 10 baris pertama).")


def _norm_result(v: str) -> str:
    """Excel memakai 'Passed'/'Failed'; session.py memahami 'Pass'/'Fail'/'No Run'."""
    return {"passed": "Pass", "failed": "Fail"}.get(v.strip().lower(), v)


def _disambiguate(cases) -> None:
    """Sebagian sheet menomori ulang TC001 di tiap bagian. Kunci session harus unik dan stabil
    (Sync Excel mencocokkan lewat No TC), jadi No TC yang kembar diberi nama modulnya."""
    from collections import Counter
    count = Counter(c.tc_no for c in cases)
    dups = {n for n, k in count.items() if k > 1}
    if not dups:
        return
    for c in cases:
        if c.tc_no in dups:
            c.tc_no = f"{c.tc_no} · {c.module}"
    again = Counter(c.tc_no for c in cases)
    nth: Counter = Counter()
    for c in cases:  # modul yang sama dipakai dua kali: beri nomor urut
        if again[c.tc_no] > 1:
            nth[c.tc_no] += 1
            c.tc_no = f"{c.tc_no} #{nth[c.tc_no]}"


def list_sheets(path) -> dict:
    """{sheet: [nama kolom Result]} untuk sheet yang memuat header 'No TC'."""
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {}
    for ws in wb.worksheets:
        try:
            _, raw = _header(ws)
        except TCSourceError:
            continue
        out[ws.title] = [_n(c) for c in raw if _n(c).lower().startswith("result")]
    if not out:
        raise TCSourceError("Tidak ada sheet dengan kolom 'No TC'.")
    return out


def read_sheet(path, sheet: str) -> SheetData:
    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet not in wb.sheetnames:
        raise TCSourceError(f"Sheet '{sheet}' tidak ada di file ini.")
    ws = wb[sheet]
    hrow, raw = _header(ws)
    names = [_n(c).lower() for c in raw]

    def col(*cands):
        return next((names.index(c) for c in cands if c in names), None)

    c_no, c_fn, c_sc = col("no tc"), col(*FUNCTION_NAMES), col("scenario")
    if c_no is None:
        c_no = 0  # header "No TC" tertimpa; kolom pertama dianggap No TC
    missing = [lbl for lbl, c in (("Function", c_fn), ("Scenario", c_sc)) if c is None]
    if missing:
        raise TCSourceError("Kolom wajib hilang: " + ", ".join(missing))
    c_type, c_exp, c_step = col("type", "category"), col("expected result", "expected"), col("steps", "step")
    c_rem = next((i for i, n in enumerate(names) if n.startswith("remark")), None)
    res_cols = {_n(raw[i]): i for i, n in enumerate(names) if n.startswith("result")}

    def cell(row, c):
        if c is None or c >= len(row) or row[c] is None:
            return ""
        return str(row[c]).strip()

    cases, module = [], ""
    for rno, row in enumerate(ws.iter_rows(min_row=hrow + 1, values_only=True), start=hrow + 1):
        if cell(row, c_fn):
            module = cell(row, c_fn)
        no = cell(row, c_no)
        if not no or not (cell(row, c_sc) or cell(row, c_step)):
            continue  # baris kosong atau baris judul bagian (mis. "Display & Autofill")
        cases.append(TestCase(
            tc_no=no, row=rno, module=module or "-", title=cell(row, c_sc), type=cell(row, c_type),
            expected=cell(row, c_exp), steps=cell(row, c_step),
            results={n: _norm_result(cell(row, i)) for n, i in res_cols.items()}, remark=cell(row, c_rem),
        ))
    if not cases:
        raise TCSourceError("Tidak ada TC yang terbaca.")
    _disambiguate(cases)
    return SheetData(
        sheet=sheet, header_row=hrow,
        result_columns={n: get_column_letter(i + 1) for n, i in res_cols.items()},
        remark_letter=get_column_letter(c_rem + 1) if c_rem is not None else None,
        cases=cases,
    )
