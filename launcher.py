#!/usr/bin/env python3
"""Start Regression Evidence: create venv, install deps, run server, open browser."""
import json
import os
import socket
import subprocess
import sys
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_venv() -> Path:
    py = venv_python()
    requirements = (ROOT / "requirements.txt").read_text()
    stamp = VENV / ".requirements"
    if not py.exists():
        print("Membuat virtualenv (sekali saja)...")
        venv.create(VENV, with_pip=True)
    if not stamp.exists() or stamp.read_text() != requirements:
        print("Install dependency...")
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
        stamp.write_text(requirements)
    return py


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Butuh Python 3.10 atau lebih baru.")
    port = json.loads((ROOT / "config.json").read_text("utf-8")).get("port", 8765)
    url = f"http://127.0.0.1:{port}/"
    if port_open(port):
        print(f"Sudah berjalan, membuka {url}")
        webbrowser.open(url)
        return
    py = ensure_venv()
    proc = subprocess.Popen([str(py), "-m", "app.server", "--port", str(port)], cwd=ROOT)
    for _ in range(50):
        if port_open(port):
            break
        time.sleep(0.2)
    webbrowser.open(url)
    print(f"Berjalan di {url} — tutup jendela ini atau tekan Ctrl+C untuk berhenti.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
