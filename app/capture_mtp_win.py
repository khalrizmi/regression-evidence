"""Capture over USB "File transfer" (MTP) on Windows via the Explorer shell — no Developer options needed.

The phone shows up under "This PC"; Shell.Application (pywin32 COM) lists its Screenshots folders
and copies new files out. Listing a folder of ~1.400 files takes ~2 s and sees new files without a
refresh (spike, POCO X5 Pro 5G). All COM calls stay on the watcher's own thread.

Every listed name is asked for its date once and handed to MtpTracker: a file counts as new when it
was made at or after the phone showed up, so files already on the phone (also ones that show up late)
are not captured, while a screenshot taken during the slow first listing is.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.capture_mtp_mac import IMG_EXT, MtpTracker

DIRS = (("DCIM", "Screenshots"), ("Pictures", "Screenshots"))
THIS_PC = 17  # ssfDRIVES
COPY_FLAGS = 4 | 16 | 512 | 1024  # no progress UI, yes to all, no confirmations, no error UI
COPY_TIMEOUT = 30.0
GRACE = 15.0  # seconds before the phone showed up that still count: picking File transfer drops and
# re-adds the phone for ~5 s, and the phone's clock may run a little behind the laptop's
HINT = "colok USB, buka kunci HP, pilih mode USB 'File transfer'"
NO_STORAGE = ("{name} terhubung tapi isi HP tidak terlihat — buka kunci HP, tarik notifikasi USB, "
              "pilih 'File transfer'")
NO_FOLDER = "{name} terhubung tapi folder Screenshots tidak ditemukan — ambil satu screenshot dulu di HP"
HIDDEN = "Isi {name} tidak terlihat lagi — buka kunci HP (saat terkunci Android menyembunyikan file)"


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class Found:
    name: str
    storages: int
    folders: list  # [(key such as "DCIM/Screenshots", shell folder)]


class ShellPhone:
    """The phone through Shell.Application. Create, use and close on one thread."""

    def __init__(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        self.shell = win32com.client.Dispatch("Shell.Application")
        self.tmp = Path(tempfile.mkdtemp(prefix="re-mtp-"))

    def close(self) -> None:
        import pythoncom
        self.shell = None
        shutil.rmtree(self.tmp, ignore_errors=True)
        pythoncom.CoUninitialize()

    @staticmethod
    def _child(folder, name: str):
        for item in folder.Items():
            if item.IsFolder and item.Name.lower() == name.lower():
                return item.GetFolder
        return None

    def find(self) -> Found | None:
        for dev in self.shell.Namespace(THIS_PC).Items():
            if dev.IsFileSystem or not dev.IsFolder:
                continue  # drives are file system folders; phones are not
            storages = list(dev.GetFolder.Items())
            folders = []
            for storage in storages:
                for path in DIRS:
                    folder = storage.GetFolder
                    for part in path:
                        folder = self._child(folder, part) if folder is not None else None
                    if folder is not None:
                        folders.append((f"{storage.Name}/{'/'.join(path)}", folder))
            return Found(str(dev.Name), len(storages), folders)  # one phone per laptop
        return None

    def list(self, folder) -> dict:
        return {str(item.Name): item for item in folder.Items()}

    def date(self, item) -> float | None:
        value = item.ExtendedProperty("System.DateModified")  # DateCreated is empty over MTP
        return value.timestamp() if value is not None else None

    def size(self, item) -> int | None:
        value = item.ExtendedProperty("System.Size")  # FolderItem.Size is 0 over MTP
        return int(value) if value is not None else None

    def copy(self, item, name: str, size: int | None) -> bytes | None:
        target = self.tmp / name
        target.unlink(missing_ok=True)
        self.shell.Namespace(str(self.tmp)).CopyHere(item, COPY_FLAGS)  # returns before the copy is done
        end, last = time.time() + COPY_TIMEOUT, -1
        while time.time() < end:
            current = target.stat().st_size if target.exists() else -1
            if current > 0 and (current == size or (size is None and current == last)):
                break
            last = current
            time.sleep(0.2)
        try:
            return target.read_bytes() if target.exists() else None
        except OSError:
            return None
        finally:
            target.unlink(missing_ok=True)


class MtpWatcher:
    """Same interface as capture_adb.Watcher: start(), stop(), status(), on_image(data, ext)."""

    def __init__(self, on_image: Callable[[bytes, str], None], on_error: Callable[[str], None],
                 make_shell: Callable[[], object] = ShellPhone, interval: float = 1.0):
        self.on_image = on_image
        self.on_error = on_error
        self.make_shell = make_shell
        self.interval = interval
        self.tracker = MtpTracker()
        self.state = "starting"
        self.message = f"Mencari HP… {HINT}"
        self.serial = ""
        self._folders: list = []
        self._listed: dict[str, set[str]] = {}  # folder key -> names already handled
        self._found_at: float | None = None  # phone first seen, maybe still in mode Charging
        self._connecting_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _now() -> float:
        return time.time()

    def _log(self, msg: str) -> None:
        print(f"[mtp {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

    def status(self) -> dict:
        message = self.message
        if self.state == "connecting":
            # The first listing after a replug takes 20-70 s; later ones ~2 s.
            waited = int(self._now() - self._connecting_at)
            message = (f"Membaca daftar screenshot {self.serial}… {waited} dtk (bisa sampai ±1 menit setelah "
                       "dicolok; screenshot yang diambil sekarang masuk setelahnya)")
        return {"state": self.state, "message": message, "serial": self.serial}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        shell = self.make_shell()
        try:
            while not self._stop.is_set():
                self.poll_once(shell)
                self._stop.wait(self.interval)
        finally:
            shell.close()

    def _lost(self, state: str, message: str) -> None:
        self._folders = []
        self._listed = {}
        if state == "disconnected":
            self.serial = ""
            self._found_at = None
        self.state, self.message = state, message

    def poll_once(self, shell) -> None:
        try:
            if self._folders:
                self._poll(shell)
            else:
                self._connect(shell)
        except Exception as exc:  # phone unplugged mid-call, COM hiccup: look for it again
            if self._folders:
                self._log(f"HP terputus: {self.serial} ({exc})")
            self._lost("disconnected", f"HP tidak terhubung — {HINT}")

    def _poll(self, shell) -> None:
        listings = [(key, shell.list(folder)) for key, folder in self._folders]
        if not any(items for _, items in listings) and any(self._listed.values()):
            self._log(f"isi {self.serial} tidak terlihat (layar terkunci?)")
            self._lost("waiting", HIDDEN.format(name=self.serial))
            return
        self._scan(shell, listings)

    def _scan(self, shell, listings: list) -> None:
        # Dates cost ~0.2 s per 1.400 files, so the first listing is filtered by date too: a screenshot
        # taken while it runs (up to ~30 s after a replug) is captured, older files are not.
        for key, items in listings:
            listed = self._listed[key]
            for name, item in items.items():
                if name in listed:
                    continue
                listed.add(name)
                folder = key.rsplit("/", 1)[-1]
                if name.startswith(".") or not name.lower().endswith(IMG_EXT):
                    continue  # no date lookup; ".pending-…" comes back under its final name
                if self.tracker.wants(folder, name, shell.date(item)):
                    self._log(f"screenshot baru: {name}")
                    self._fetch(shell, item, name)

    def _connect(self, shell) -> None:
        found = shell.find()
        if found is None:
            self._lost("disconnected", f"HP tidak terhubung — {HINT}")
            return
        if self._found_at is None:
            self._found_at = self._now()
        if not found.storages:
            self._lost("waiting", NO_STORAGE.format(name=found.name))
            self.serial = found.name
            return
        if not found.folders:
            self._lost("waiting", NO_FOLDER.format(name=found.name))
            self.serial = found.name
            return
        self.serial = found.name
        self.state, self.message, self._connecting_at = "connecting", "", self._now()
        started = time.time()
        self.tracker.reset(now=self._found_at - GRACE)
        listings = [(key, shell.list(folder)) for key, folder in found.folders]
        total = sum(len(items) for _, items in listings)
        self._log(f"HP terhubung: {found.name}, {total} file di {', '.join(k for k, _ in listings)} "
                  f"({time.time() - started:.1f} dtk)")
        self._folders, self._listed = found.folders, {key: set() for key, _ in listings}
        self._scan(shell, listings)
        self.tracker.catalog_ready()
        self.state, self.message = "connected", ""

    def _fetch(self, shell, item, name: str) -> None:
        size = shell.size(item)
        for _ in range(2):  # the phone may still be writing
            data = shell.copy(item, name, size)
            if data and (size is None or len(data) == size):
                self.on_image(data, Path(name).suffix.lower())
                return
        self.on_error(f"Gagal menarik screenshot {name}")
