from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from PIL import Image

HEADER = ["No TC", "Service Name", "Function", "Scenario", "Type", "Step", "Expected Result",
          "Result \nAndroid (Existing)", "Result Android (New)", "Result IOS", "Remark"]


def default_rows():
    return [
        ["TC01", "Mobile", "Login", "User input username valid", "Happy Flow", "1. Login", "Home page",
         "Pass", "Pass", "Pass", ""],
        ["TC02", "Mobile", None, "User input username invalid", "UnHappy Flow", "", "Error Login",
         "No Run", "", "Pass", "Bypass"],
        [None] * 11,
        ["TC03", "Mobile", "Transfer", "User Transfer\nBank A to Bank A", "Happy Flow", "", "Success",
         "Fail", "", "", "Terjadi kesalahan"],
    ]


def make_xlsx(path, rows, header=HEADER, sheet="Regresi", pre_rows=()):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in pre_rows:
        ws.append(list(row))
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    wb.save(path)
    return Path(path)


def png_bytes(width=100, height=200, color=(200, 30, 30), fmt="PNG", mode="RGB"):
    buf = BytesIO()
    Image.new(mode, (width, height), color).save(buf, fmt)
    return buf.getvalue()


def new_session(tmp_path, xlsx_path, result_column="Result Android (Existing)"):
    from app.session import Session
    from app.tcsource import load_sheet

    sheet = load_sheet(xlsx_path, "Regresi")
    return Session.create(tmp_path / "sessions", sheet, xlsx_path, "android", "1.2.4", result_column, "SER1")
