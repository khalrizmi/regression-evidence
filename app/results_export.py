"""Text for pasting the Result / Remark columns back into Google Sheet, aligned to Excel rows."""
from __future__ import annotations

import re

RESULT_TEXT = {"Pass": "Pass", "Fail": "Fail", "Skip": "No Run", "Todo": "Todo"}


def clean_remark(text) -> str:
    # Tabs/newlines would shift cells on paste; double quotes start a quoted TSV field in Sheets.
    return re.sub(r"\s+", " ", str(text).replace('"', "'")).strip()


def export_column(data: dict, kind: str) -> dict:
    if kind not in ("result", "remark"):
        raise ValueError(f"Kolom tidak dikenal: {kind}")
    by_row = {c["row"]: c for c in data["cases"] if not c["removed"]}
    if not by_row:
        return {"text": "", "first_row": None, "count": 0}
    first, last = min(by_row), max(by_row)
    lines = []
    for row in range(first, last + 1):
        case = by_row.get(row)
        if case is None:
            lines.append("")
        elif kind == "result":
            lines.append(RESULT_TEXT[case["status"]])
        else:
            lines.append(clean_remark(case["remark"]))
    return {"text": "\n".join(lines), "first_row": first, "count": len(lines)}
