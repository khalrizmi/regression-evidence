import io, json, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer

import openpyxl
import pytest
from PIL import Image

from app import docx_export, results_export, server, sync, tcsource
from app.session import Session, list_sessions


def make_xlsx(path, extra=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Login"
    ws.append(["judul", "ignored"])
    ws.append(["No TC", "Function", "Type", "Scenario", "Step", "Expected Result", "Result Android 1.0", "Result IOS 1.0", "Remark"])
    ws.append(["A-1", "Login", "Positive", "Login sukses", "1. Buka\n2. Isi", "- Masuk beranda", "Pass", "", ""])
    ws.append(["A-2", None, "Negative", "Password salah", "1. Isi salah", "- Muncul error", "Fail", "No Run", "bug"])
    ws.append([None])
    ws.append(["B-1", "Profil", "Positive", "Ubah nama", "1. Edit", "- Tersimpan", "", "Pass", ""])
    if extra:
        ws.append(["B-2", "Profil", "Positive", "Ubah foto", "1. Pilih", "- OK", "", "", ""])
    wb.save(path)


def png(color="red"):
    b = io.BytesIO()
    Image.new("RGB", (400, 800), color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def sess(tmp_path):
    x = tmp_path / "tc.xlsx"
    make_xlsx(x)
    return Session.create(tmp_path / "s", tcsource.read_sheet(x, "Login"), x, "ios", "1.0", "Result IOS 1.0", "iPhone 15")


def test_tcsource(tmp_path):
    x = tmp_path / "tc.xlsx"
    make_xlsx(x)
    assert tcsource.list_sheets(x) == {"Login": ["Result Android 1.0", "Result IOS 1.0"]}
    sd = tcsource.read_sheet(x, "Login")
    assert [c.tc_no for c in sd.cases] == ["A-1", "A-2", "B-1"]
    assert sd.cases[1].module == "Login" and sd.result_columns["Result IOS 1.0"] == "H"


def test_session_initial_status_and_labels(sess):
    st = {c["tc_no"]: c["status"] for c in sess.data["cases"]}
    assert st == {"A-1": "Todo", "A-2": "Skip", "B-1": "Pass"}
    assert sess.labels() == {"A-1": "TC001", "A-2": "TC002", "B-1": "TC001"}  # regresi: per modul
    assert sess.root.name.endswith("_ios_1-0_login")


def test_results_alignment(sess):
    r = results_export.column_text(sess, "result")
    assert r["text"].split("\n") == ["Todo", "No Run", "", "Pass"] and "kolom H baris 3" in r["hint"]


@pytest.mark.parametrize("kind", ["regression", "sit"])
def test_docx(sess, kind):
    sess.data["doc_type"] = kind
    sess.set_active("A-1")
    sess.add_image(png(), ".png")
    sess.add_image(png("blue"), ".png")
    out = docx_export.build_docx(sess)
    assert out.exists() and ("iOS 1.0" in out.name)
    if kind == "sit":  # A-2 Skip tanpa gambar dikeluarkan, tapi penomoran tetap menghitungnya
        assert sess.labels()["B-1"] == "TC003"


def test_image_ops_and_sync(tmp_path, sess):
    sess.set_active("A-1")
    n1, n2 = sess.add_image(png(), ".png"), sess.add_image(png("blue"), ".png")
    sess.reorder_images("A-1", [n2, n1])
    new = sess.move_image("A-1", n1, "B-1")
    assert new.startswith("profil_b-1_") and sess.case("A-1")["images"] == [n2]
    x2 = tmp_path / "v2.xlsx"
    make_xlsx(x2, extra=True)
    sd = tcsource.read_sheet(x2, "Login")
    assert sync.diff(sess, sd)["added"] == ["B-2"]
    sync.apply(sess, sd, x2)
    assert len(sess.active_cases()) == 4 and list_sessions(tmp_path / "s")[0]["total"] == 4


def test_upload_flow(tmp_path):
    x = tmp_path / "tc.xlsx"
    make_xlsx(x)
    app = server.App(tmp_path / "s", {"port": 0, "token": "tok"})
    app.sessions_dir.mkdir()
    app.current = Session.create(app.sessions_dir, tcsource.read_sheet(x, "Login"), x, "ios", "1", "")
    server.Handler.app = app
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def post(path, data, ctype="image/png"):
        req = urllib.request.Request(base + path, data=data, method="POST", headers={"Content-Type": ctype})
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            return e.code

    assert post("/api/upload?token=bad", png()) == 401
    assert post("/api/upload?token=tok", png()) == 409  # belum ada TC aktif
    app.current.set_active("A-2")
    assert post("/api/upload?token=tok", png()) == 200
    assert post("/api/upload?token=tok", b"not an image") == 415
    body = b'--X\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\nContent-Type: image/png\r\n\r\n' + png("green") + b"\r\n--X--\r\n"
    assert post("/api/upload?token=tok", body, "multipart/form-data; boundary=X") == 200
    assert len(app.current.case("A-2")["images"]) == 2
    # UI & API manajemen: Host palsu ditolak
    req = urllib.request.Request(base + "/api/state", headers={"Host": "evil.com"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403
    state = json.load(urllib.request.urlopen(base + "/api/state"))
    assert state["session"]["headings"]["A-1"] == "TC001 - Login sukses"
    srv.shutdown()


def test_archive_roundtrip(tmp_path, sess):
    import zipfile
    from app import archive
    sess.set_active("A-1")
    sess.add_image(png(), ".png")
    dead = sess.add_image(png("blue"), ".png")
    sess.delete_image("A-1", dead)                      # trash/ tidak ikut zip
    z = archive.export_zip(sess.root, tmp_path / "exp")
    assert "trash" not in " ".join(zipfile.ZipFile(z).namelist())
    root = archive.import_zip(z, tmp_path / "imp")
    s2 = Session.open(root)
    assert s2.case("A-1")["images"] == sess.case("A-1")["images"] and (s2.shots / s2.case("A-1")["images"][0]).exists()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("state.json", "{}")
        zf.writestr("../evil.txt", "x")
    with pytest.raises(archive.ArchiveError):
        archive.import_zip(bad, tmp_path / "imp")
    (tmp_path / "x.zip").write_bytes(b"nope")
    with pytest.raises(archive.ArchiveError):
        archive.import_zip(tmp_path / "x.zip", tmp_path / "imp")


def test_real_world_layout(tmp_path):
    """Header 'Function Module', A1 tertimpa, nilai 'Passed', baris judul bagian, No TC dinomori ulang per bagian."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "E2E"
    ws.append(["ilhamma", "Service Name", "Function Module", "Scenario", "Type", "Step", "Expected Result", "Result \nAndroid", "Result\nIOS", "Remarks"])
    ws.append(["Bagian Satu"])
    ws.append(["TC001", "Mobile", "Mod A", "S1", "Happy Flow", "1. a", "- x", "Passed", "Failed", ""])
    ws.append(["TC002", "Mobile", "Mod A", "S2", "Happy Flow", "1. a", "- x", "Todo", "No Run", ""])
    ws.append(["Bagian Dua"])
    ws.append(["TC001", "Mobile", "Mod B", "S3", "Variation", "1. a", "- x", "Todo", "Todo", ""])
    x = tmp_path / "r.xlsx"
    wb.save(x)
    assert tcsource.list_sheets(x) == {"E2E": ["Result Android", "Result IOS"]}
    sd = tcsource.read_sheet(x, "E2E")
    assert [c.tc_no for c in sd.cases] == ["TC001 · Mod A", "TC002", "TC001 · Mod B"]  # judul bagian bukan TC
    s = Session.create(tmp_path / "s", sd, x, "ios", "1", "Result IOS")
    assert [c["status"] for c in s.active_cases()] == ["Fail", "Skip", "Todo"]
    assert sync.diff(s, tcsource.read_sheet(x, "E2E")) == {"added": [], "changed": [], "removed": []}
