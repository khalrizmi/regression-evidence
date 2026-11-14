"""Buat .venv, install dependency, jalankan server, buka browser."""
import hashlib, json, os, socket, subprocess, sys, threading, time, venv, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
HASH = ROOT / ".requirements.hash"


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def port():
    try:
        return int(json.loads((ROOT / "config.json").read_text())["port"])
    except Exception:
        return 8765


def is_running(p):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", p)) == 0


def main():
    p = port()
    url = f"http://127.0.0.1:{p}"
    if is_running(p):
        print("Server sudah jalan, membuka browser...")
        webbrowser.open(url)
        return
    if sys.version_info < (3, 10):
        sys.exit("Butuh Python 3.10 atau lebih baru.")
    if not venv_python().exists():
        print("Membuat .venv ...")
        venv.create(VENV, with_pip=True)
    req = (ROOT / "requirements.txt").read_bytes()
    digest = hashlib.sha256(req).hexdigest()
    if not HASH.exists() or HASH.read_text() != digest:
        print("Meng-install dependency (butuh internet) ...")
        subprocess.check_call([str(venv_python()), "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=ROOT)
        HASH.write_text(digest)

    def opener():
        for _ in range(50):
            if is_running(p):
                webbrowser.open(url)
                return
            time.sleep(0.2)

    threading.Thread(target=opener, daemon=True).start()
    try:
        subprocess.call([str(venv_python()), "-m", "app.server"], cwd=ROOT)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
