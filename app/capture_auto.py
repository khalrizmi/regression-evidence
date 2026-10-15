"""Device "Otomatis": use adb when a phone with USB debugging is plugged in, otherwise USB File transfer.

Testers swap phones within a session, some with Developer options and some without. Only one inner
watcher runs at a time, so a USB-debugging phone that is also in File transfer mode is not captured twice.
"""
from __future__ import annotations

import threading
from typing import Callable

DEVICE_ID = "auto"
DEVICE_LABEL = "Otomatis (adb bila USB debugging aktif, selain itu File transfer)"
VIA = {"adb": "adb", "mtp": "File transfer"}


class AutoWatcher:
    """Same interface as capture_adb.Watcher: start(), stop(), status()."""

    def __init__(self, list_adb: Callable[[], list[str]], make_adb: Callable[[str], object],
                 make_mtp: Callable[[], object] | None, log: Callable[[str], None] = lambda msg: None,
                 interval: float = 2.0):
        self.list_adb = list_adb
        self.make_adb = make_adb
        self.make_mtp = make_mtp
        self.log = log
        self.interval = interval
        self.target: tuple[str, str] | None = None  # ("adb", serial) | ("mtp", "")
        self.inner = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        if self.inner is None:
            hint = "colok HP: USB debugging aktif (adb)" + (", atau mode USB 'File transfer'" if self.make_mtp else "")
            return {"state": "disconnected", "message": f"HP tidak terhubung — {hint}", "serial": "", "via": ""}
        return {**self.inner.status(), "via": VIA[self.target[0]]}

    def poll_once(self) -> None:
        serials = self.list_adb()
        if serials:
            target = ("adb", serials[0])
        elif self.make_mtp is not None:
            target = ("mtp", "")
        else:
            target = None
        if target == self.target:
            return
        self._stop_inner()
        self.target = target
        if target is None:
            return
        kind, serial = target
        self.inner = self.make_adb(serial) if kind == "adb" else self.make_mtp()
        self.log(f"capture lewat {VIA[kind]}{' ' + serial if serial else ''}")
        self.inner.start()

    def _stop_inner(self) -> None:
        if self.inner is not None:
            self.inner.stop()
            self.inner = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # keep watching whatever a single poll throws
                self.log(f"capture otomatis error: {exc}")
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._stop_inner()
