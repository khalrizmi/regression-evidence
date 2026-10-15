"""Watch the phone's Screenshots folders over adb and hand new images to a callback."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


@dataclass
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes = b""


def default_runner(args: list[str]) -> Result:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(1, b"", str(exc).encode())
    return Result(proc.returncode, proc.stdout, proc.stderr)


def find_adb() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    candidates = [Path.home() / "Library/Android/sdk/platform-tools/adb"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Android/Sdk/platform-tools/adb.exe")
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(env)
        if sdk:
            candidates += [Path(sdk) / "platform-tools" / "adb", Path(sdk) / "platform-tools" / "adb.exe"]
    return next((str(p) for p in candidates if p.is_file()), None)


def sh_quote(s: str) -> str:
    """Quote for the device's POSIX shell (adb shell / exec-out run through sh)."""
    return "'" + s.replace("'", "'\\''") + "'"


class Adb:
    def __init__(self, path: str, runner: Callable[[list[str]], Result] = default_runner):
        self.path = path
        self.runner = runner

    def _run(self, *args: str, serial: str = "") -> Result:
        cmd = [self.path] + (["-s", serial] if serial else []) + list(args)
        return self.runner(cmd)

    def devices(self) -> list[str]:
        res = self._run("devices")
        if res.returncode:
            return []
        out = []
        for line in res.stdout.decode(errors="ignore").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                out.append(parts[0])
        return out

    def list_files(self, serial: str, dirs: list[str]) -> dict[str, int] | None:
        script = "; ".join(f"stat -c '%s|%n' {sh_quote(d)}/* 2>/dev/null" for d in dirs) + "; true"
        res = self._run("shell", script, serial=serial)
        if res.returncode:
            return None
        files = {}
        for line in res.stdout.decode("utf-8", errors="ignore").splitlines():
            size, sep, path = line.strip().partition("|")
            if sep and size.isdigit() and path.lower().endswith(IMG_EXT):
                files[path] = int(size)
        return files

    def pull(self, serial: str, path: str) -> bytes | None:
        res = self._run("exec-out", "cat " + sh_quote(path), serial=serial)
        return res.stdout if res.returncode == 0 and res.stdout else None

    def version_name(self, serial: str, package: str) -> str:
        if not package:
            return ""
        res = self._run("shell", "dumpsys package " + sh_quote(package), serial=serial)
        match = re.search(rb"versionName=(\S+)", res.stdout)
        return match.group(1).decode() if match else ""


class Watcher:
    def __init__(self, adb: Adb, serial: str, dirs: list[str], on_image: Callable[[bytes, str], None],
                 on_error: Callable[[str], None], interval: float = 1.0, max_attempts: int = 3):
        self.adb = adb
        self.serial = serial
        self.dirs = dirs
        self.on_image = on_image
        self.on_error = on_error
        self.interval = interval
        self.max_attempts = max_attempts
        self.state = "starting"
        self.message = ""
        self._seen: set[str] = set()
        self._pending: dict[str, int] = {}
        self._attempts: dict[str, int] = {}
        self._baselined = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        return {"state": self.state, "message": self.message, "serial": self.serial}

    def poll_once(self) -> None:
        files = self.adb.list_files(self.serial, self.dirs)
        if files is None:
            self.state = "disconnected"
            self.message = "Device tidak terhubung — cek kabel USB dan izin USB debugging"
            return
        self.state, self.message = "connected", ""
        if not self._baselined:
            self._seen = set(files)
            self._baselined = True
            return
        for path, size in files.items():
            if path in self._seen:
                continue
            if size == 0 or self._pending.get(path) != size:
                self._pending[path] = size  # still being written, or first sight
                continue
            data = self.adb.pull(self.serial, path)
            if data is None:
                self._attempts[path] = self._attempts.get(path, 0) + 1
                if self._attempts[path] >= self.max_attempts:
                    self._seen.add(path)
                    self.on_error(f"Gagal menarik screenshot {Path(path).name}")
                continue
            self._seen.add(path)
            self._pending.pop(path, None)
            self.on_image(data, Path(path).suffix.lower() or ".png")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # keep watching whatever a single poll throws
                self.state, self.message = "error", f"Capture error: {exc}"
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
