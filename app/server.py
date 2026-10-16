"""Local HTTP server for Regression Evidence: JSON API + static UI, bound to 127.0.0.1."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import archive, capture_auto, capture_mtp_mac, capture_mtp_win, docx_export, results_export, sync
from .capture_adb import Adb, Watcher, find_adb
from .session import Session, SessionError, list_sessions
from .tcsource import SheetError, list_sheets, load_sheet

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_CONFIG = {
    "port": 8765,
    "package": "",
    "screenshot_dirs": ["/sdcard/Pictures/Screenshots", "/sdcard/DCIM/Screenshots"],
}
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".zip": "application/zip",
}
NO_ADB_MESSAGE = ("adb tidak ditemukan — capture nonaktif. Install Android platform-tools: "
                  "https://developer.android.com/tools/releases/platform-tools "
                  "(Windows: install juga USB driver dari vendor HP).")


def mtp_watcher_class():
    """USB File transfer capture for this OS, or None when unsupported or its dependency is missing."""
    if capture_mtp_mac.available():
        return capture_mtp_mac.MtpWatcher
    if capture_mtp_win.available():
        return capture_mtp_win.MtpWatcher
    return None


class ApiError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:
    def __init__(self, sessions_dir: Path, config: dict, adb: Adb | None):
        self.sessions_dir = Path(sessions_dir)
        self.uploads = self.sessions_dir / "_uploads"
        self.exports = self.sessions_dir / "_exports"
        for d in (self.sessions_dir, self.uploads, self.exports):
            d.mkdir(parents=True, exist_ok=True)
        self.config = {**DEFAULT_CONFIG, **config}
        self.adb = adb
        self.lock = threading.RLock()
        self.session: Session | None = None
        self.watcher: Watcher | None = None
        self.events: list[dict] = []
        self.event_id = 0

    # ---- events -------------------------------------------------------
    def add_event(self, kind: str, text: str) -> None:
        with self.lock:
            self.event_id += 1
            self.events.append({"id": self.event_id, "kind": kind, "text": text})
            del self.events[:-50]

    # ---- Excel uploads ------------------------------------------------
    def save_upload(self, data: bytes, filename: str) -> dict:
        if not filename.lower().endswith(".xlsx"):
            raise ApiError("File harus berformat .xlsx")
        upload_id = uuid.uuid4().hex
        path = self.uploads / f"{upload_id}.xlsx"
        path.write_bytes(data)
        try:
            sheets = list_sheets(path)
        except Exception as exc:  # openpyxl raises many different types for bad files
            path.unlink(missing_ok=True)
            raise ApiError(f"Tidak bisa membaca file Excel: {exc}") from exc
        return {"upload_id": upload_id, "filename": filename, "sheets": sheets}

    def upload_path(self, upload_id) -> Path:
        path = self.uploads / f"{upload_id}.xlsx"
        if not UPLOAD_ID.fullmatch(str(upload_id)) or not path.exists():
            raise ApiError("Upload tidak ditemukan, pilih file Excel lagi")
        return path

    def inspect(self, body: dict) -> dict:
        sheet = load_sheet(self.upload_path(body.get("upload_id", "")), body.get("sheet", ""))
        return {
            "count": len(sheet.cases),
            "modules": len({c.module for c in sheet.cases}),
            "result_columns": sheet.result_columns,
        }

    def devices(self, _q=None) -> dict:
        # One laptop, one phone, phones swapped freely: capture is always automatic. The build is
        # prefilled from the phone currently on adb, if any.
        if not (self.adb or mtp_watcher_class()):
            return {"adb": False, "devices": []}
        serials = self.adb.devices() if self.adb else []
        build = self.adb.version_name(serials[0], self.config["package"]) if serials else ""
        return {"adb": bool(self.adb),
                "devices": [{"serial": capture_auto.DEVICE_ID, "label": capture_auto.DEVICE_LABEL, "build": build}]}

    # ---- session lifecycle --------------------------------------------
    def create_session(self, body: dict) -> dict:
        platform = body.get("platform", "android")
        if platform not in ("android", "ios"):
            raise ApiError("Platform tidak dikenal")
        build = str(body.get("build", "")).strip()
        xlsx = self.upload_path(body.get("upload_id", ""))
        if not build:
            raise ApiError("Versi build wajib diisi")
        sheet = load_sheet(xlsx, body.get("sheet", ""))
        column = body.get("result_column", "")
        if column and column not in sheet.result_columns.get(platform, []):
            raise ApiError(f"Kolom result tidak ada: {column}")
        self.close()
        with self.lock:
            session = Session.create(self.sessions_dir, sheet, xlsx, platform, build, column,
                                     str(body.get("device", "")), str(body.get("doc_type") or "regression"))
            self._attach(session)
        return self.state({})

    def _root(self, sid) -> Path:
        sid = str(sid)
        if not SAFE_ID.fullmatch(sid):
            raise ApiError("ID session tidak valid")
        root = self.sessions_dir / sid
        if not root.is_dir():
            raise ApiError("Session tidak ditemukan", 404)
        return root

    def open_session(self, body: dict) -> dict:
        root = self._root(body.get("id", ""))
        self.close()
        with self.lock:
            self._attach(Session.open(root))
        return self.state({})

    def restore_session(self, body: dict) -> dict:
        root = self._root(body.get("id", ""))
        self.close()
        with self.lock:
            self._attach(Session.restore_backup(root))
        return self.state({})

    def delete_session(self, body: dict) -> dict:
        """Permanently delete a session folder (screenshots hold customer data: no trash, no undo)."""
        root = self._root(body.get("id", ""))
        if self.session is not None and self.session.root.resolve() == root.resolve():
            self.close()
        shutil.rmtree(root)
        return {"sessions": list_sessions(self.sessions_dir)}

    def close(self, _body=None) -> dict:
        with self.lock:
            watcher, self.watcher, self.session = self.watcher, None, None
        if watcher:
            watcher.stop()  # outside the lock: the watcher thread may be waiting for it
        return {"ok": True}

    def _attach(self, session: Session) -> None:
        self.session = session
        if session.data["platform"] != "android":
            return
        on_image = lambda data, ext, s=session: self.on_image(s, data, ext)  # noqa: E731
        on_error = lambda msg: self.add_event("error", msg)  # noqa: E731
        # Every Android session captures automatically, whatever device older sessions stored
        # ("mtp" or an adb serial): testers swap phones, with and without USB debugging.
        mtp = mtp_watcher_class()
        if self.adb or mtp:
            dirs = self.config["screenshot_dirs"]
            self.watcher = capture_auto.AutoWatcher(
                self.adb.devices if self.adb else list,
                lambda serial: Watcher(self.adb, serial, dirs, on_image, on_error),
                (lambda: mtp(on_image, on_error)) if mtp else None,
                log=lambda msg: print(f"[capture] {msg}", file=sys.stderr, flush=True))
        if self.watcher:
            self.watcher.start()

    def require(self) -> Session:
        if not self.session:
            raise ApiError("Belum ada session terbuka", 409)
        return self.session

    # ---- capture ------------------------------------------------------
    def on_image(self, session: Session, data: bytes, ext: str) -> None:
        with self.lock:
            if self.session is not session:
                return  # watcher of a session that was already closed
            name = session.add_image(data, ext)
            if name is None:
                self.add_event("warn", "Screenshot diabaikan: pilih TC dulu")
                return
            tc_no = session.data["active"]
            number = session.headings()[tc_no].split(" - ")[0]
            self.add_event("shot", f"+1 → {session.case(tc_no)['module']} {number}")

    # ---- state & actions ----------------------------------------------
    def state(self, q: dict) -> dict:
        try:
            since = int((q.get("since") or ["0"])[0])
        except ValueError:
            since = 0
        with self.lock:
            base = {"events": [e for e in self.events if e["id"] > since], "last_event": self.event_id}
            if not self.session:
                return {**base, "session": None}
            s = self.session
            if self.watcher:
                adb = self.watcher.status()
            else:
                if s.data["platform"] == "android":
                    message = NO_ADB_MESSAGE
                else:
                    message = "Capture otomatis belum tersedia untuk platform ini"
                adb = {"state": "no-adb", "message": message, "serial": ""}
            return {**base, "session": s.data, "headings": s.headings(), "progress": s.progress(), "adb": adb}

    def act(self, fn) -> dict:
        with self.lock:
            fn(self.require())
        return self.state({})

    def preview(self, _q) -> dict:
        with self.lock:
            session = self.require()
            return {"layout": docx_export.doc_layout(session), "doc_type": session.doc_type}

    def warnings(self, _q) -> dict:
        with self.lock:
            return docx_export.pre_generate_warnings(self.require())

    def results(self, q) -> dict:
        kind = (q.get("kind") or ["result"])[0]
        if kind not in ("result", "remark"):
            raise ApiError("kind harus result atau remark")
        with self.lock:
            return results_export.export_column(self.require().data, kind)

    def generate(self, _body) -> dict:
        with self.lock:
            path = docx_export.build_docx(self.require())
        return {"path": str(path), "name": path.name}

    def open_out(self, _body) -> dict:
        open_folder(self.require().root / "out")
        return {"ok": True}

    def sync_preview(self, body) -> dict:
        path = self.upload_path(body.get("upload_id", ""))
        with self.lock:
            s = self.require()
            return sync.diff(s, load_sheet(path, s.data["sheet"]))

    def sync_apply(self, body) -> dict:
        path = self.upload_path(body.get("upload_id", ""))
        with self.lock:
            s = self.require()
            sync.apply(s, load_sheet(path, s.data["sheet"]), path)
        return self.state({})

    def export(self) -> Path:
        with self.lock:
            return archive.export_zip(self.require().root, self.exports)

    def import_zip(self, data: bytes) -> dict:
        tmp = self.exports / f"import-{uuid.uuid4().hex}.zip"
        tmp.write_bytes(data)
        try:
            root = archive.import_zip(tmp, self.sessions_dir)
        finally:
            tmp.unlink(missing_ok=True)
        return {"id": root.name}

    def shot_path(self, raw_name: str) -> Path:
        name = unquote(raw_name)
        session = self.require()
        path = session.shots / Path(name).name
        if Path(name).name != name or not path.is_file():
            raise ApiError("Gambar tidak ditemukan", 404)
        return path


GET_ROUTES = {
    "/api/sessions": lambda app, q: {"sessions": list_sessions(app.sessions_dir)},
    "/api/state": lambda app, q: app.state(q),
    "/api/devices": lambda app, q: app.devices(q),
    "/api/preview": lambda app, q: app.preview(q),
    "/api/warnings": lambda app, q: app.warnings(q),
    "/api/results": lambda app, q: app.results(q),
}

POST_ROUTES = {
    "/api/inspect": lambda app, b: app.inspect(b),
    "/api/sessions/create": lambda app, b: app.create_session(b),
    "/api/sessions/open": lambda app, b: app.open_session(b),
    "/api/sessions/restore": lambda app, b: app.restore_session(b),
    "/api/sessions/close": lambda app, b: app.close(b),
    "/api/sessions/delete": lambda app, b: app.delete_session(b),
    "/api/active": lambda app, b: app.act(lambda s: s.set_active(b.get("tc_no"))),
    "/api/status": lambda app, b: app.act(lambda s: s.set_status(b.get("tc_no"), b.get("status"))),
    "/api/remark": lambda app, b: app.act(lambda s: s.set_remark(b.get("tc_no"), b.get("remark", ""))),
    "/api/image/delete": lambda app, b: app.act(lambda s: s.delete_image(b.get("tc_no"), b.get("name"))),
    "/api/image/move": lambda app, b: app.act(lambda s: s.move_image(b.get("tc_no"), b.get("name"), b.get("to"))),
    "/api/image/reorder": lambda app, b: app.act(lambda s: s.reorder_images(b.get("tc_no"), list(b.get("names") or []))),
    "/api/generate": lambda app, b: app.generate(b),
    "/api/open-folder": lambda app, b: app.open_out(b),
    "/api/sync/preview": lambda app, b: app.sync_preview(b),
    "/api/sync/apply": lambda app, b: app.sync_apply(b),
}


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _file(self, path: Path, extra: dict | None = None) -> None:
            ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            self._send(200, path.read_bytes(), ctype, extra)

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def _local_request(self) -> bool:
            # Blocks DNS rebinding (foreign Host) and cross-site requests (foreign Origin):
            # session data contains customer screenshots.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in LOCAL_HOSTS:
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlparse(origin).hostname in LOCAL_HOSTS

        def _handle(self, fn) -> None:
            if not self._local_request():
                return self._json({"error": "Akses hanya dari 127.0.0.1 / localhost"}, 403)
            try:
                fn()
            except ApiError as exc:
                self._json({"error": str(exc)}, exc.code)
            except (SessionError, SheetError, archive.ArchiveError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:  # last resort: report instead of dropping the connection
                self._json({"error": f"Kesalahan internal: {exc}"}, 500)

        def do_GET(self) -> None:
            self._handle(self._get)

        def do_POST(self) -> None:
            self._handle(self._post)

        def _get(self) -> None:
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            if path in ("/", "/index.html"):
                return self._file(STATIC / "index.html")
            if path.startswith("/static/"):
                target = (STATIC / unquote(path[len("/static/"):])).resolve()
                if STATIC.resolve() not in target.parents or not target.is_file():
                    raise ApiError("Not found", 404)
                return self._file(target)
            if path.startswith("/shots/"):
                return self._file(app.shot_path(path[len("/shots/"):]))
            if path == "/api/export":
                zipped = app.export()
                return self._file(zipped, {"Content-Disposition": f'attachment; filename="{zipped.name}"'})
            route = GET_ROUTES.get(path)
            if not route:
                raise ApiError("Not found", 404)
            self._json(route(app, query))

        def _post(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/upload/xlsx":
                return self._json(app.save_upload(self._body(), unquote(self.headers.get("X-Filename", ""))))
            if path == "/api/import":
                return self._json(app.import_zip(self._body()))
            route = POST_ROUTES.get(path)
            if not route:
                raise ApiError("Not found", 404)
            try:
                body = json.loads(self._body() or b"{}")
            except json.JSONDecodeError as exc:
                raise ApiError("Body JSON tidak valid") from exc
            if not isinstance(body, dict):
                raise ApiError("Body JSON harus berupa object")
            self._json(route(app, body))

    return Handler


def load_config() -> dict:
    path = ROOT / "config.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Regression Evidence server")
    parser.add_argument("--port", type=int)
    parser.add_argument("--sessions", type=Path, default=ROOT / "sessions")
    args = parser.parse_args(argv)
    config = {**DEFAULT_CONFIG, **load_config()}
    port = args.port or config["port"]
    adb_path = find_adb()
    app = App(args.sessions, config, Adb(adb_path) if adb_path else None)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    print(f"Regression Evidence: http://127.0.0.1:{port}/  (adb: {adb_path or 'tidak ditemukan'}, "
          f"USB File transfer: {'ya' if mtp_watcher_class() else 'tidak'})", flush=True)
    try:
        if capture_mtp_mac.available():
            # ImageCaptureCore needs the main thread's run loop; HTTP moves to a background thread.
            from PyObjCTools import AppHelper, MachSignals
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            # Launcher Ctrl+C sends SIGTERM, closing the Terminal window SIGHUP: leave the loop so
            # `finally` closes the phone session (an unclosed one hides the phone until replugged).
            for sig in (signal.SIGTERM, signal.SIGHUP):
                MachSignals.signal(sig, lambda _sig: AppHelper.stopEventLoop())
            AppHelper.runConsoleEventLoop(installInterrupt=True)
        else:
            httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        httpd.server_close()


if __name__ == "__main__":
    main()
