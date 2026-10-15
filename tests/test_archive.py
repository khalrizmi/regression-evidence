import json
import zipfile

import pytest

from app.archive import ArchiveError, export_zip, import_zip
from app.session import Session
from helpers import new_session, png_bytes


def test_round_trip(tmp_path, xlsx):
    s = new_session(tmp_path, xlsx)
    s.set_active("TC01")
    keep = s.add_image(png_bytes())
    gone = s.add_image(png_bytes())
    s.delete_image("TC01", gone)

    z = export_zip(s.root, tmp_path / "exp")
    names = zipfile.ZipFile(z).namelist()
    assert "state.json" in names and "source.xlsx" in names and f"shots/{keep}" in names
    assert not any(n.startswith("trash/") or n.endswith(".bak") for n in names)

    root = import_zip(z, tmp_path / "sessions")
    assert root != s.root and root.name.startswith(s.root.name)
    again = Session.open(root)
    assert again.data["id"] == root.name
    assert again.case("TC01")["images"] == [keep]
    assert (root / "shots" / keep).exists()
    assert (root / "trash").is_dir()


def test_reject_traversal(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("state.json", json.dumps({"id": "x"}))
        zf.writestr("../evil.txt", "x")
    with pytest.raises(ArchiveError, match="tidak aman"):
        import_zip(bad, tmp_path / "sessions")
    assert not (tmp_path / "evil.txt").exists()


def test_reject_non_session(tmp_path):
    other = tmp_path / "o.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(ArchiveError, match="state.json"):
        import_zip(other, tmp_path / "sessions")
    notzip = tmp_path / "n.zip"
    notzip.write_text("hello")
    with pytest.raises(ArchiveError):
        import_zip(notzip, tmp_path / "sessions")


def test_malicious_id_is_sanitized(tmp_path):
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("state.json", json.dumps({"id": "../../_uploads"}))
    root = import_zip(z, tmp_path / "sessions")
    assert root.parent == tmp_path / "sessions"
    assert not root.name.startswith((".", "_"))
