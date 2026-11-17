"""Merge a newer version of the Excel sheet into a session, matching cases by No TC."""
from __future__ import annotations

import shutil
from pathlib import Path

from .session import Session, SessionError, case_from_tc
from .tcsource import SheetData

FIELDS = ("module", "title", "type", "expected", "steps")


def diff(session: Session, sheet: SheetData) -> dict:
    if sheet.sheet != session.data["sheet"]:
        raise SessionError(f"Sheet berbeda: session memakai '{session.data['sheet']}', file berisi '{sheet.sheet}'")
    current = {c["tc_no"]: c for c in session.data["cases"]}
    incoming = {tc.tc_no: tc for tc in sheet.cases}
    added = [t for t in incoming if t not in current or current[t]["removed"]]
    changed = []
    for tc_no, tc in incoming.items():
        case = current.get(tc_no)
        if case and not case["removed"]:
            fields = [f for f in FIELDS if case[f] != getattr(tc, f)]
            if fields:
                changed.append({"tc_no": tc_no, "fields": fields})
    removed = [t for t, c in current.items() if t not in incoming and not c["removed"]]
    return {"added": added, "changed": changed, "removed": removed}


def apply(session: Session, sheet: SheetData, xlsx_path: Path) -> dict:
    result = diff(session, sheet)
    current = {c["tc_no"]: c for c in session.data["cases"]}
    for tc in sheet.cases:
        case = current.get(tc.tc_no)
        if case is None:
            session.data["cases"].append(case_from_tc(tc, session.data["result_column"]))
            continue
        for f in FIELDS:
            case[f] = getattr(tc, f)
        case["row"] = tc.row
        case["removed"] = False
    for tc_no in result["removed"]:
        current[tc_no]["removed"] = True
        if session.data["active"] == tc_no:
            session.data["active"] = None
    shutil.copy2(xlsx_path, session.root / "source.xlsx")
    session.save()
    return result
