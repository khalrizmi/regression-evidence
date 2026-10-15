"""Session state for one regression run (sheet + platform + build), persisted as state.json."""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from .tcsource import SheetData, TestCase

STATE = "state.json"
BACKUP = "state.json.bak"
STATUSES = ("Todo", "Pass", "Fail", "Skip")


class SessionError(Exception):
    pass


def slugify(text: str, fallback: str = "x", max_len: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()[:max_len].strip("-")
    return slug or fallback


def unique_path(base: Path) -> Path:
    path, n = base, 2
    while path.exists():
        path = base.with_name(f"{base.stem}-{n}{base.suffix}")
        n += 1
    return path


def initial_status(value: str) -> str:
    return {"pass": "Pass", "fail": "Fail", "no run": "Skip"}.get((value or "").strip().lower(), "Todo")


def case_from_tc(tc: TestCase, result_column: str) -> dict:
    return {
        "tc_no": tc.tc_no,
        "row": tc.row,
        "module": tc.module,
        "title": tc.title,
        "type": tc.type,
        "expected": tc.expected,
        "steps": tc.steps,
        "status": initial_status(tc.results.get(result_column, "")),
        "remark": tc.remark,
        "images": [],
        "removed": False,
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Session:
    def __init__(self, root: Path, data: dict):
        self.root = Path(root)
        self.data = data

    @property
    def shots(self) -> Path:
        return self.root / "shots"

    @classmethod
    def create(cls, sessions_dir: Path, sheet: SheetData, xlsx_path: Path, platform: str,
               build: str, result_column: str, device: str = "") -> "Session":
        name = f"{datetime.now():%Y-%m-%d}_{platform}_{slugify(build, 'nobuild')}_{slugify(sheet.sheet, 'sheet')}"
        root = unique_path(Path(sessions_dir) / name)
        for sub in ("shots", "trash", "out"):
            (root / sub).mkdir(parents=True)
        shutil.copy2(xlsx_path, root / "source.xlsx")
        data = {
            "version": 1,
            "id": root.name,
            "sheet": sheet.sheet,
            "result_column": result_column,
            "platform": platform,
            "build": build,
            "device": device,
            "created": _now(),
            "opened": _now(),
            "active": None,
            "cases": [case_from_tc(tc, result_column) for tc in sheet.cases],
        }
        session = cls(root, data)
        session.save()
        return session

    @classmethod
    def open(cls, root: Path) -> "Session":
        root = Path(root)
        path = root / STATE
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise SessionError(f"state.json rusak atau hilang: {path} ({exc})") from exc
        session = cls(root, data)
        session.data["opened"] = _now()
        session.save()
        return session

    @classmethod
    def restore_backup(cls, root: Path) -> "Session":
        root = Path(root)
        backup = root / BACKUP
        if not backup.exists():
            raise SessionError(f"Backup tidak ada: {backup}")
        shutil.copy2(backup, root / STATE)
        return cls.open(root)

    def save(self) -> None:
        path = self.root / STATE
        if path.exists():
            shutil.copy2(path, self.root / BACKUP)
        tmp = self.root / (STATE + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, path)

    def case(self, tc_no) -> dict:
        for c in self.data["cases"]:
            if c["tc_no"] == tc_no:
                return c
        raise SessionError(f"TC tidak ditemukan: {tc_no}")

    def active_cases(self) -> list[dict]:
        return sorted((c for c in self.data["cases"] if not c["removed"]), key=lambda c: c["row"])

    def headings(self) -> dict[str, str]:
        counters: dict[str, int] = {}
        out: dict[str, str] = {}
        for c in self.active_cases():
            counters[c["module"]] = counters.get(c["module"], 0) + 1
            out[c["tc_no"]] = f"TC{counters[c['module']]:03d} - {c['title']}"
        return out

    def progress(self) -> dict:
        cases = self.active_cases()
        return {"done": sum(1 for c in cases if c["status"] != "Todo"), "total": len(cases)}

    def set_active(self, tc_no) -> None:
        if tc_no is not None and self.case(tc_no)["removed"]:
            raise SessionError(f"TC sudah dihapus dari Excel: {tc_no}")
        self.data["active"] = tc_no
        self.save()

    def set_status(self, tc_no, status) -> None:
        if status not in STATUSES:
            raise SessionError(f"Status tidak dikenal: {status}")
        self.case(tc_no)["status"] = status
        self.save()

    def set_remark(self, tc_no, remark) -> None:
        self.case(tc_no)["remark"] = str(remark)
        self.save()

    def _new_name(self, case: dict, ext: str) -> str:
        stem = f"{slugify(case['module'], 'module')}_{slugify(case['tc_no'], 'tc')}"
        # Never reuse a deleted or moved image's name: the browser keeps showing the old picture for
        # the same URL.
        retired = {Path(n).stem for n in self.data.get("retired_names", [])}
        k = 1
        while any(self.shots.glob(f"{stem}_{k}.*")) or f"{stem}_{k}" in retired:
            k += 1
        return f"{stem}_{k}{ext}"

    def add_image(self, data: bytes, ext: str = ".png") -> str | None:
        tc_no = self.data["active"]
        if not tc_no:
            return None
        case = self.case(tc_no)
        name = self._new_name(case, ext.lower())
        (self.shots / name).write_bytes(data)
        case["images"].append(name)
        self.save()
        return name

    def _owned(self, tc_no, name) -> dict:
        case = self.case(tc_no)
        if name not in case["images"]:
            raise SessionError(f"Gambar {name} bukan milik {tc_no}")
        return case

    def delete_image(self, tc_no, name) -> None:
        case = self._owned(tc_no, name)
        src = self.shots / name
        if src.exists():
            src.replace(unique_path(self.root / "trash" / name))
        case["images"].remove(name)
        self.data.setdefault("retired_names", []).append(name)
        self.save()

    def move_image(self, tc_no, name, to_tc) -> str:
        case = self._owned(tc_no, name)
        dest = self.case(to_tc)
        if dest["removed"]:
            raise SessionError(f"TC sudah dihapus dari Excel: {to_tc}")
        if to_tc == tc_no:
            return name
        new = self._new_name(dest, Path(name).suffix)
        (self.shots / name).replace(self.shots / new)
        case["images"].remove(name)
        self.data.setdefault("retired_names", []).append(name)
        dest["images"].append(new)
        self.save()
        return new

    def reorder_images(self, tc_no, names) -> None:
        case = self.case(tc_no)
        if sorted(names) != sorted(case["images"]):
            raise SessionError("Daftar gambar tidak cocok")
        case["images"] = list(names)
        self.save()


def list_sessions(sessions_dir: Path) -> list[dict]:
    base = Path(sessions_dir)
    out: list[dict] = []
    if not base.exists():
        return out
    for root in base.iterdir():
        if not root.is_dir() or not ((root / STATE).exists() or (root / BACKUP).exists()):
            continue
        try:
            data = json.loads((root / STATE).read_text("utf-8"))
            progress = Session(root, data).progress()
            out.append({
                "id": root.name,
                "sheet": data["sheet"],
                "platform": data["platform"],
                "build": data["build"],
                "opened": data.get("opened", ""),
                "done": progress["done"],
                "total": progress["total"],
                "images": sum(len(c["images"]) for c in data["cases"]),
                "broken": False,
            })
        except (OSError, ValueError, KeyError):
            out.append({"id": root.name, "broken": True, "opened": ""})
    return sorted(out, key=lambda s: s["opened"], reverse=True)
