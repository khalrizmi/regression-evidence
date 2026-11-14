"""Export / import a session folder as one .zip file."""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from .session import STATE, unique_path

SKIP_DIRS = {"trash"}
SKIP_SUFFIXES = (".bak", ".tmp")


class ArchiveError(Exception):
    pass


def export_zip(root: Path, dest_dir: Path) -> Path:
    root, dest_dir = Path(root), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{root.name}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if path.is_dir() or rel.parts[0] in SKIP_DIRS or path.name.endswith(SKIP_SUFFIXES):
                continue
            zf.write(path, rel.as_posix())
    return dest


def _check_name(name: str) -> None:
    parts = PurePosixPath(name).parts
    if name.startswith("/") or "\\" in name or ":" in name or ".." in parts:
        raise ArchiveError(f"Path tidak aman di zip: {name}")


def import_zip(zip_path: Path, sessions_dir: Path) -> Path:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("File bukan zip yang valid") from exc
    with zf:
        names = zf.namelist()
        for name in names:
            _check_name(name)
        if STATE not in names:
            raise ArchiveError("Bukan file session: state.json tidak ada")
        try:
            data = json.loads(zf.read(STATE))
        except ValueError as exc:
            raise ArchiveError("state.json di dalam zip rusak") from exc
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", str(data.get("id") or "session")).strip("._-") or "session"
        root = unique_path(Path(sessions_dir) / base)
        root.mkdir(parents=True)
        zf.extractall(root)
    for sub in ("shots", "trash", "out"):
        (root / sub).mkdir(exist_ok=True)
    data["id"] = root.name
    (root / STATE).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return root
