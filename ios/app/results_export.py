"""Teks Copy Result / Copy Remark: satu baris per baris Excel, sejajar untuk paste ke Google Sheet."""
from . import tcsource

RESULT = {"Pass": "Pass", "Fail": "Fail", "Skip": "No Run", "Todo": "Todo"}


def column_text(session, kind: str) -> dict:
    if kind not in ("result", "remark"):
        raise ValueError("kind harus result atau remark")
    cases = session.active_cases()
    by_row = {c["row"]: c for c in cases}
    lo, hi = min(by_row), max(by_row)
    lines = []
    for r in range(lo, hi + 1):
        c = by_row.get(r)
        if not c:
            lines.append("")  # baris non-TC: kosong supaya paste tetap sejajar
        elif kind == "result":
            lines.append(RESULT[c["status"]])
        else:
            lines.append(" ".join(c["remark"].split()))
    letter = None
    try:
        sd = tcsource.read_sheet(session.root / "source.xlsx", session.data["sheet"])
        letter = sd.result_columns.get(session.data["result_column"]) if kind == "result" else sd.remark_letter
    except Exception:
        pass
    where = f"kolom {letter}" if letter else "kolom yang sesuai"
    return {"text": "\n".join(lines), "hint": f"Klik sel {where} baris {lo} di Google Sheet, lalu Ctrl/Cmd+V."}
