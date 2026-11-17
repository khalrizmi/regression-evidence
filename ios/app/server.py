"""Server lokal Regression Evidence (iOS).

- UI + API manajemen: hanya dari laptop ini (127.0.0.1).
- /api/upload: bisa dijangkau iPhone lewat Wi-Fi, wajib token.
"""
import argparse, hashlib, hmac, json, mimetypes, secrets, shutil, socket, sys, threading, time, uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import archive, docx_export, results_export, sync, tcsource
from .session import Session, SessionError, list_sessions

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 40 * 1024 * 1024
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # tidak mengirim paket; hanya memilih interface
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def sniff_ext(data: bytes):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def extract_body(ctype: str, body: bytes) -> bytes:
    """Shortcuts bisa kirim file mentah (Request Body: File) atau multipart (Form)."""
    if ctype.lower().startswith("multipart/"):
        msg = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        for part in msg.iter_parts():
            payload = part.get_payload(decode=True)
            if payload:
                return payload
        raise ApiError("Multipart tanpa file.")
    return body


class App:
    def __init__(self, sessions_dir: Path, config: dict):
        self.lock = threading.RLock()
        self.sessions_dir = sessions_dir
        self.tmp_dir = ROOT / ".tmp"
        self.export_dir = ROOT / "exports"
        self.cfg = config
        self.current = None
        self.events, self.eid = [], 0
        self.last_hash = (None, 0.0)

    def push(self, kind, msg):
        self.eid += 1
        self.events = (self.events + [{"id": self.eid, "kind": kind, "msg": msg}])[-40:]

    def need(self):
        if not self.current:
            raise ApiError("Belum ada session yang dibuka.", 409)
        return self.current

    def upload_url(self):
        return f"http://{lan_ip()}:{self.cfg['port']}/api/upload?token={self.cfg['token']}"

    # ---- iPhone upload ----
    def ingest(self, ctype, body):
        with self.lock:
            s = self.current
            if not s:
                raise ApiError("Belum ada session yang dibuka di laptop.", 409)
            tc_no = s.data["active"]
            if not tc_no:
                self.push("warn", "Screenshot diabaikan: pilih TC dulu")
                raise ApiError("Pilih TC dulu di laptop.", 409)
            data = extract_body(ctype, body)
            ext = sniff_ext(data)
            if not ext:
                raise ApiError("Format tidak didukung (pakai PNG/JPEG).", 415)
            h = hashlib.sha1(data).hexdigest()
            case = s.case(tc_no)
            if h == self.last_hash[0] and time.time() - self.last_hash[1] < 5:
                return {"ok": True, "dup": True, "tc": tc_no, "msg": "Duplikat diabaikan"}
            s.add_image(data, ext)
            self.last_hash = (h, time.time())
            msg = f"+1 → {case['module']} {tc_no}"
            self.push("ok", msg)
            return {"ok": True, "tc": tc_no, "count": len(case["images"]), "msg": msg}

    def public(self):
        s = self.current
        if not s:
            return None
        d = dict(s.data)
        d["headings"] = s.headings()
        d["progress"] = s.progress()
        return d


class Handler(BaseHTTPRequestHandler):
    server_version = "RegressionEvidenceIOS"
    app: App = None

    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _send(self, code, payload: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _local_ok(self):
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        host = urlparse("//" + (self.headers.get("Host") or "")).hostname
        if host not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).hostname in LOCAL_HOSTS

    def _token_ok(self, q):
        given = (q.get("token") or [self.headers.get("X-Token", "")])[0]
        return hmac.compare_digest(given.encode(), self.app.cfg["token"].encode())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ApiError("Body terlalu besar.", 413)
        return self.rfile.read(n) if n else b""

    def _jbody(self):
        raw = self._body()
        return json.loads(raw.decode() or "{}")

    # ---- dispatch ----
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        try:
            if path in ("/api/upload", "/api/ping"):
                if not self._token_ok(q):
                    raise ApiError("Token salah.", 401)
                if path == "/api/ping":
                    return self._json({"ok": True, "msg": "Terhubung"})
                if method != "POST":
                    raise ApiError("Gunakan POST.", 405)
                return self._json(self.app.ingest(self.headers.get("Content-Type", ""), self._body()))
            if not self._local_ok():
                return self._json({"error": "forbidden"}, 403)
            if path.startswith("/api/") or path.startswith("/shots/"):
                return self._api(method, path, q)
            return self._static(path)
        except ApiError as e:
            self._json({"error": str(e), "msg": str(e)}, e.code)
        except (SessionError, tcsource.TCSourceError, archive.ArchiveError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            raise ApiError("Not found", 404)
        self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

    def _api(self, method, path, q):
        a = self.app
        with a.lock:
            if path.startswith("/shots/"):
                shots = a.need().shots.resolve()
                f = (shots / unquote(path[len("/shots/"):])).resolve()
                if f.parent != shots or not f.is_file():
                    raise ApiError("Not found", 404)
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "image/png")
            if method == "GET":
                if path == "/api/state":
                    return self._json({"session": a.public(),
                                       "upload_url": a.upload_url(), "events": a.events})
                if path == "/api/sessions":
                    return self._json({"sessions": list_sessions(a.sessions_dir)})
                if path == "/api/download":
                    f = a.export_dir / Path((q.get("name") or [""])[0]).name
                    if not f.is_file():
                        raise ApiError("File tidak ditemukan", 404)
                    return self._send(200, f.read_bytes(), "application/zip",
                                      {"Content-Disposition": f'attachment; filename="{f.name}"'})
                if path == "/api/results":
                    return self._json(results_export.column_text(a.need(), (q.get("kind") or ["result"])[0]))
                if path == "/api/precheck":
                    return self._json(docx_export.pre_generate_warnings(a.need()))
                if path == "/api/preview":
                    s = a.need()
                    return self._json({"doc_type": s.doc_type, "layout": docx_export.doc_layout(s),
                                       "file": docx_export.output_path(s).name})
                raise ApiError("Not found", 404)

            # ---- POST ----
            if path in ("/api/xlsx/inspect", "/api/sync/inspect"):
                raw = self._body()
                a.tmp_dir.mkdir(exist_ok=True)
                tid = uuid.uuid4().hex
                f = a.tmp_dir / f"{tid}.xlsx"
                f.write_bytes(raw)
                try:
                    if path == "/api/xlsx/inspect":
                        return self._json({"id": tid, "sheets": tcsource.list_sheets(f)})
                    s = a.need()
                    sheet = tcsource.read_sheet(f, s.data["sheet"])
                    return self._json({"id": tid, **sync.diff(s, sheet)})
                except Exception:
                    f.unlink(missing_ok=True)
                    raise
            if path == "/api/import":
                raw = self._body()
                a.tmp_dir.mkdir(exist_ok=True)
                f = a.tmp_dir / f"{uuid.uuid4().hex}.zip"
                f.write_bytes(raw)
                try:
                    root = archive.import_zip(f, a.sessions_dir)
                finally:
                    f.unlink(missing_ok=True)
                return self._json({"ok": True, "id": root.name})
            b = self._jbody()
            if path == "/api/export":
                root = a.sessions_dir / Path(b.get("id") or a.need().root.name).name
                if not root.is_dir():
                    raise ApiError("Session tidak ditemukan", 404)
                z = archive.export_zip(root, a.export_dir)
                return self._json({"ok": True, "name": z.name, "path": str(z)})
            if path == "/api/sessions":
                f = a.tmp_dir / f"{Path(str(b.get('id', ''))).name}.xlsx"
                if not f.exists():
                    raise ApiError("File Excel belum di-upload.")
                sheet = tcsource.read_sheet(f, b["sheet"])
                rc = b.get("result_col") or ""
                if rc and rc not in sheet.result_columns:
                    raise ApiError(f"Kolom result '{rc}' tidak ditemukan.")
                a.current = Session.create(a.sessions_dir, sheet, f, "ios", (b.get("build") or "").strip(), rc,
                                           (b.get("device") or "").strip(), b.get("doc_type", "regression"))
                f.unlink(missing_ok=True)
                return self._json({"ok": True, "id": a.current.root.name})
            if path == "/api/sessions/open":
                d = a.sessions_dir / Path(b["id"]).name
                try:
                    a.current = Session.open(d)
                except SessionError:
                    a.current = Session.restore_backup(d)
                    a.push("warn", "state.json rusak, dipulihkan dari backup")
                return self._json({"ok": True})
            if path == "/api/sessions/close":
                a.current = None
                return self._json({"ok": True})
            if path == "/api/sessions/delete":
                d = a.sessions_dir / Path(b["id"]).name
                if a.current and a.current.root == d:
                    a.current = None
                if d.is_dir() and d.parent == a.sessions_dir:
                    shutil.rmtree(d)
                return self._json({"ok": True})
            s = a.need()
            if path == "/api/active":
                s.set_active(b.get("no"))
            elif path == "/api/status":
                s.set_status(b["no"], b["status"])
            elif path == "/api/remark":
                s.set_remark(b["no"], b.get("remark", ""))
            elif path == "/api/image/delete":
                s.delete_image(b["no"], b["name"])
            elif path == "/api/image/move":
                s.move_image(b["no"], b["name"], b["to"])
            elif path == "/api/image/reorder":
                s.reorder_images(b["no"], b["names"])
            elif path == "/api/sync/apply":
                f = a.tmp_dir / f"{Path(str(b.get('id', ''))).name}.xlsx"
                if not f.exists():
                    raise ApiError("File sinkron tidak ditemukan; pilih ulang.")
                res = sync.apply(s, tcsource.read_sheet(f, s.data["sheet"]), f)
                f.unlink(missing_ok=True)
                return self._json({"ok": True, **res})
            elif path == "/api/generate":
                out = docx_export.build_docx(s)
                return self._json({"ok": True, "path": str(out), "name": out.name})
            else:
                raise ApiError("Not found", 404)
            return self._json({"ok": True})


def load_config():
    p = ROOT / "config.json"
    cfg = {"port": 8765, "token": ""}
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(12)
        p.write_text(json.dumps(cfg, indent=2))
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int)
    ap.add_argument("--sessions", type=Path, default=ROOT / "sessions")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    args.sessions.mkdir(parents=True, exist_ok=True)
    Handler.app = App(args.sessions.resolve(), cfg)
    srv = ThreadingHTTPServer(("0.0.0.0", cfg["port"]), Handler)
    print(f"Berjalan di http://127.0.0.1:{cfg['port']}/")
    print(f"URL upload untuk iPhone: {Handler.app.upload_url()}")
    print("Jangan tutup jendela ini selama memakai tool. Ctrl+C untuk berhenti.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
