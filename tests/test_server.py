import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app.server import App, make_handler
from helpers import png_bytes


@pytest.fixture
def server(tmp_path, monkeypatch):
    from app import capture_mtp_mac, capture_mtp_win
    monkeypatch.setattr(capture_mtp_mac, "available", lambda: False)  # tests opt in to File transfer
    monkeypatch.setattr(capture_mtp_win, "available", lambda: False)
    app = App(tmp_path / "sessions", {"package": ""}, adb=None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    app.close()


def call(base, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read(), res.headers
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers


def jcall(base, path, body=None, **kw):
    status, raw, _ = call(base, path, body, **kw)
    return status, json.loads(raw)


def create(base, xlsx):
    _, up = jcall(base, "/api/upload/xlsx", raw=xlsx.read_bytes(), headers={"X-Filename": "tc.xlsx"})
    _, st = jcall(base, "/api/sessions/create", {
        "upload_id": up["upload_id"], "sheet": "Regresi", "platform": "android",
        "result_column": "Result Android (Existing)", "build": "1.2.4", "device": ""})
    return up, st


def test_full_flow(server, xlsx):
    app, base = server
    status, up = jcall(base, "/api/upload/xlsx", raw=xlsx.read_bytes(), headers={"X-Filename": "tc.xlsx"})
    assert status == 200 and up["sheets"] == ["Regresi"]
    _, info = jcall(base, "/api/inspect", {"upload_id": up["upload_id"], "sheet": "Regresi"})
    assert info["count"] == 3 and info["modules"] == 2

    _, st = create(base, xlsx)
    assert st["session"]["sheet"] == "Regresi"
    assert st["adb"]["state"] == "no-adb"

    jcall(base, "/api/active", {"tc_no": "TC01"})
    app.on_image(app.session, png_bytes(), ".png")
    _, st = jcall(base, "/api/state?since=0")
    assert any(e["kind"] == "shot" and "Login TC001" in e["text"] for e in st["events"])
    assert st["session"]["cases"][0]["images"] == ["login_tc01_1.png"]

    status, body, headers = call(base, "/shots/login_tc01_1.png")
    assert status == 200 and headers["Content-Type"] == "image/png"

    jcall(base, "/api/status", {"tc_no": "TC01", "status": "Fail"})
    jcall(base, "/api/remark", {"tc_no": "TC01", "remark": "server error"})
    _, res = jcall(base, "/api/results?kind=result")
    assert res["text"] == "Fail\nNo Run\n\nFail" and res["first_row"] == 2

    _, layout = jcall(base, "/api/preview")
    assert layout["layout"][0]["cases"][0]["images"] == ["login_tc01_1.png"]
    _, gen = jcall(base, "/api/generate", {})
    assert gen["name"].endswith(".docx")

    status, zipped, headers = call(base, "/api/export")
    assert status == 200 and headers["Content-Type"] == "application/zip"
    _, imported = jcall(base, "/api/import", raw=zipped)
    _, listing = jcall(base, "/api/sessions")
    assert {s["id"] for s in listing["sessions"]} == {st["session"]["id"], imported["id"]}

    _, opened = jcall(base, "/api/sessions/open", {"id": imported["id"]})
    assert opened["session"]["id"] == imported["id"]
    jcall(base, "/api/sessions/close", {})
    _, st = jcall(base, "/api/state")
    assert st["session"] is None


def test_screenshot_without_active_tc_warns(server, xlsx):
    app, base = server
    create(base, xlsx)
    app.on_image(app.session, png_bytes(), ".png")
    _, st = jcall(base, "/api/state?since=0")
    assert any(e["kind"] == "warn" and "pilih TC" in e["text"] for e in st["events"])


def test_image_from_stale_session_ignored(server, xlsx):
    app, base = server
    create(base, xlsx)
    old = app.session
    create(base, xlsx)
    jcall(base, "/api/active", {"tc_no": "TC01"})
    app.on_image(old, png_bytes(), ".png")
    assert app.session.case("TC01")["images"] == []


def test_sync_endpoints(server, xlsx, tmp_path):
    from helpers import default_rows, make_xlsx
    app, base = server
    create(base, xlsx)
    rows = default_rows()
    del rows[1]
    v2 = make_xlsx(tmp_path / "v2.xlsx", rows)
    _, up = jcall(base, "/api/upload/xlsx", raw=v2.read_bytes(), headers={"X-Filename": "v2.xlsx"})
    _, d = jcall(base, "/api/sync/preview", {"upload_id": up["upload_id"]})
    assert d["removed"] == ["TC02"]
    _, st = jcall(base, "/api/sync/apply", {"upload_id": up["upload_id"]})
    assert st["progress"]["total"] == 2


def test_errors(server, xlsx):
    app, base = server
    status, body = jcall(base, "/api/active", {"tc_no": "TC01"})
    assert status == 409 and "session" in body["error"]
    status, body = jcall(base, "/api/sessions/create", {"upload_id": "nope", "sheet": "x", "build": "1"})
    assert status == 400 and "Upload" in body["error"]
    status, body = jcall(base, "/api/upload/xlsx", raw=b"x", headers={"X-Filename": "a.txt"})
    assert status == 400
    status, body = jcall(base, "/api/upload/xlsx", raw=b"not excel", headers={"X-Filename": "a.xlsx"})
    assert status == 400
    assert call(base, "/static/../server.py")[0] == 404
    status, body = jcall(base, "/api/sessions/open", {"id": "../x"})
    assert status == 400
    create(base, xlsx)
    assert call(base, "/shots/..%2Fstate.json")[0] == 404
    status, body = jcall(base, "/api/status", {"tc_no": "TC01", "status": "Done"})
    assert status == 400
    assert call(base, "/api/nope")[0] == 404


def test_rejects_foreign_host_and_origin(server, xlsx):
    app, base = server
    create(base, xlsx)
    port = base.rsplit(":", 1)[1]
    status, body = jcall(base, "/api/state", headers={"Host": f"evil.example:{port}"})
    assert status == 403
    status, body = jcall(base, "/api/import", raw=b"x", headers={"Origin": "http://evil.example"})
    assert status == 403
    status, body = jcall(base, "/api/state", headers={"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"})
    assert status == 200


class FakeMtpWatcher:
    instances = []

    def __init__(self, on_image, on_error):
        self.on_image, self.on_error, self.started, self.stopped = on_image, on_error, False, False
        FakeMtpWatcher.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self):
        return {"state": "connected", "message": "", "serial": "POCO X5 Pro 5G"}


def test_without_adb_or_file_transfer_reports_install_hint(server, xlsx):
    app, base = server
    _, up = jcall(base, "/api/upload/xlsx", raw=xlsx.read_bytes(), headers={"X-Filename": "tc.xlsx"})
    _, st = jcall(base, "/api/sessions/create", {
        "upload_id": up["upload_id"], "sheet": "Regresi", "platform": "android",
        "result_column": "", "build": "1.0", "device": "mtp"})
    assert st["adb"]["state"] == "no-adb" and "platform-tools" in st["adb"]["message"]


def test_capture_is_automatic_even_for_sessions_stored_with_a_fixed_device(server, xlsx, monkeypatch):
    from app import capture_auto, capture_mtp_mac
    app, base = server
    monkeypatch.setattr(capture_mtp_mac, "available", lambda: True)
    monkeypatch.setattr(capture_mtp_mac, "MtpWatcher", FakeMtpWatcher)
    FakeMtpWatcher.instances.clear()

    _, devs = jcall(base, "/api/devices")
    assert devs["devices"] == [{"serial": "auto", "label": capture_auto.DEVICE_LABEL, "build": ""}]

    _, up = jcall(base, "/api/upload/xlsx", raw=xlsx.read_bytes(), headers={"X-Filename": "tc.xlsx"})
    jcall(base, "/api/sessions/create", {
        "upload_id": up["upload_id"], "sheet": "Regresi", "platform": "android",
        "result_column": "", "build": "1.0", "device": "mtp"})  # stored by an older version
    assert isinstance(app.watcher, capture_auto.AutoWatcher)
    for _ in range(100):  # the watcher thread polls right away; no adb phone: File transfer
        if FakeMtpWatcher.instances and FakeMtpWatcher.instances[-1].started:
            break
        time.sleep(0.02)
    watcher = FakeMtpWatcher.instances[-1]
    assert watcher.started and len(FakeMtpWatcher.instances) == 1
    _, st = jcall(base, "/api/state")
    assert st["adb"]["via"] == "File transfer" and st["adb"]["state"] == "connected"

    jcall(base, "/api/active", {"tc_no": "TC01"})
    watcher.on_image(png_bytes(), ".jpg")
    assert app.session.case("TC01")["images"] == ["login_tc01_1.jpg"]
    jcall(base, "/api/sessions/close", {})
    assert watcher.stopped


def test_delete_session_removes_folder_and_closes_it(server, xlsx):
    app, base = server
    sid = create(base, xlsx)[1]["session"]["id"]
    root = app.sessions_dir / sid
    assert root.is_dir()
    _, out = jcall(base, "/api/sessions/delete", {"id": sid})
    assert not root.exists()
    assert app.session is None
    assert all(s["id"] != sid for s in out["sessions"])
    status, _ = jcall(base, "/api/sessions/delete", {"id": sid})
    assert status == 404
    status, _ = jcall(base, "/api/sessions/delete", {"id": "../outside"})
    assert status == 400
    assert app.sessions_dir.is_dir()


def test_sit_session_preview_and_generate(server, xlsx):
    app, base = server
    _, up = jcall(base, "/api/upload/xlsx", raw=xlsx.read_bytes(), headers={"X-Filename": "tc.xlsx"})
    body = {"upload_id": up["upload_id"], "sheet": "Regresi", "platform": "android",
            "result_column": "", "build": "1.0", "device": "", "doc_type": "sit"}
    status, st = jcall(base, "/api/sessions/create", body)
    assert status == 200 and st["session"]["doc_type"] == "sit"
    _, pv = jcall(base, "/api/preview")
    assert pv["doc_type"] == "sit"
    assert [c["label"] for m in pv["layout"] for c in m["cases"]] == ["TC001", "TC002", "TC003"]
    _, gen = jcall(base, "/api/generate", {})
    assert gen["name"] == "SIT _ Regresi - Android 1.0.docx"
    _, listing = jcall(base, "/api/sessions")
    assert listing["sessions"][0]["doc_type"] == "sit"
    status, err = jcall(base, "/api/sessions/create", {**body, "doc_type": "uat"})
    assert status == 400 and "Jenis dokumen" in err["error"]
