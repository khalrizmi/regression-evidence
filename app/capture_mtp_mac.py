"""Capture over USB "File transfer" (MTP) on macOS via ImageCaptureCore — no Developer options needed.

ImageCaptureCore only delivers callbacks on the main thread's run loop, so on macOS
server.main serves HTTP from a background thread and runs that loop on the main thread.
All ImageCaptureCore calls below are scheduled onto the main thread with AppHelper.callAfter.

Two paths deliver new screenshots, deduplicated by folder/name in MtpTracker:
- fast: the phone's PTP "object added" events plus direct GetObjectInfo/GetObject commands, which work
  long before macOS has finished reading the phone's file list after a (re)connect;
- fallback: ImageCaptureCore's content catalog (didAddItems), complete but slow after a reconnect.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from app import ptp

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
REOPEN_AFTER = 3.0  # seconds before retrying a session that came up with an empty catalog
PROGRESS_EVERY = 5.0  # seconds between progress lines in the terminal while reading the file list
STALL_AFTER = 15.0  # seconds without progress while connecting before reopening the session
DEVICE_ID = "mtp"
DEVICE_LABEL = "USB File transfer (tanpa Developer options)"
HINT = "colok USB, buka kunci HP, pilih mode USB 'File transfer'"
NO_STORAGE = ("{name} terhubung tapi isi HP tidak terlihat — buka kunci HP, tarik notifikasi USB, "
              "pilih 'File transfer' (bila sudah, pilih 'Charging' lalu 'File transfer' lagi)")


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import ImageCaptureCore  # noqa: F401
        from PyObjCTools import AppHelper  # noqa: F401
    except ImportError:
        return False
    return True


class MtpTracker:
    """Decides which reported files are new screenshots.

    The device reports every file it has while building its content catalog, also after a
    reconnect. When the phone is locked at connect time the catalog is reported "complete"
    while still empty and the real listing floods in after unlocking, so the catalog signal
    alone cannot separate old files from new ones. A file counts as new when it was created
    at or after the moment the phone (re)connected; without a creation date, fall back to
    "first reported after the catalog was complete".
    """

    def __init__(self, now: float | None = None):
        self.ready = False
        self.since = time.time() if now is None else now
        self._seen: set[str] = set()

    def reset(self, now: float | None = None) -> None:
        self.ready = False
        self.since = time.time() if now is None else now

    def catalog_ready(self) -> None:
        self.ready = True

    def wants(self, folder: str, name: str, created: float | None = None) -> bool:
        key = f"{folder}/{name}"
        if key in self._seen:
            return False
        self._seen.add(key)
        if ("screenshot" not in folder.lower()
                or name.startswith(".")  # ".pending-…" while the phone is still writing
                or not name.lower().endswith(IMG_EXT)):
            return False
        if created is not None:
            return created >= self.since
        return self.ready


_DELEGATE_CLASS = None


def _delegate_class():
    """ObjC classes can be registered only once per process, so build it lazily and cache it."""
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is None:
        import objc
        from Foundation import NSObject

        class REMtpDelegate(NSObject):
            def initWithWatcher_(self, watcher):
                self = objc.super(REMtpDelegate, self).init()
                if self is None:
                    return None
                self.watcher = watcher
                return self

            def deviceBrowser_didAddDevice_moreComing_(self, browser, device, more):
                self.watcher.device_added(device)

            def deviceBrowser_didRemoveDevice_moreGoing_(self, browser, device, more):
                self.watcher.device_removed(device)

            def device_didOpenSessionWithError_(self, device, error):
                self.watcher.session_opened(error)

            def deviceDidBecomeReadyWithCompleteContentCatalog_(self, device):
                self.watcher.catalog_ready(device)

            def cameraDevice_didAddItems_(self, camera, items):
                self.watcher.items_added(items)

            def cameraDevice_didRemoveItems_(self, camera, items):
                pass

            def didRemoveDevice_(self, device):
                pass

            def device_didCloseSessionWithError_(self, device, error):
                pass

        _DELEGATE_CLASS = REMtpDelegate
    return _DELEGATE_CLASS


class MtpWatcher:
    """Same interface as capture_adb.Watcher: start(), stop(), status(), on_image(data, ext)."""

    def __init__(self, on_image: Callable[[bytes, str], None], on_error: Callable[[str], None]):
        self.on_image = on_image
        self.on_error = on_error
        self.tracker = MtpTracker()
        self.state = "starting"
        self.message = f"Mencari HP… {HINT}"
        self.serial = ""
        self._browser = None
        self._delegate = None
        self._device = None
        self._items = 0  # items reported in the current session
        self._connected_at = time.time()
        self._ptp_live = False  # phone events arrive: new screenshots are fetched directly
        self._folders: dict[int, str] = {}  # PTP folder handle -> name
        self._tmp = Path(tempfile.mkdtemp(prefix="re-mtp-"))

    def _log(self, msg: str) -> None:
        print(f"[mtp {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

    def _later(self, delay: float, fn, *args) -> None:
        from PyObjCTools import AppHelper
        AppHelper.callLater(delay, fn, *args)

    def status(self) -> dict:
        message = self.message
        if self.state == "connecting":
            # macOS re-reads the phone's whole file list after every USB (re)connect before reporting
            # anything; with ~20k files that takes about a minute.
            waited = int(time.time() - self._connected_at)
            message = (f"macOS sedang membaca daftar file {self.serial}… {waited} dtk "
                       "(biasanya ±1 menit; jaga layar HP tetap tidak terkunci)")
        elif self.state == "loading":
            # The ready signal often comes before the listing; new screenshots queue behind it.
            waited = int(time.time() - self._connected_at)
            message = (f"Menerima daftar file {self.serial}… {self._items} file, {waited} dtk — "
                       "screenshot yang diambil sekarang masuk setelah daftar selesai")
        return {"state": self.state, "message": message, "serial": self.serial}

    def start(self) -> None:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self._start_main)

    def stop(self) -> None:
        if threading.current_thread() is threading.main_thread():
            # Shutdown: the event loop is gone, so close right here and give the request time to go
            # out. A session left open makes macOS hide the phone until it is replugged.
            from Foundation import NSDate, NSRunLoop
            self._stop_main()
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))
            return
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self._stop_main)

    # ---- main thread ------------------------------------------------------
    def _start_main(self) -> None:
        import ImageCaptureCore as IC
        self._delegate = _delegate_class().alloc().initWithWatcher_(self)
        browser = IC.ICDeviceBrowser.alloc().init()
        browser.setDelegate_(self._delegate)
        browser.setBrowsedDeviceTypeMask_(IC.ICDeviceTypeMaskCamera | IC.ICDeviceLocationTypeMaskLocal)
        browser.start()
        self._browser = browser

    def _stop_main(self) -> None:
        if self._device is not None:
            self._device.setDelegate_(None)
            self._set_event_handler(self._device, None)
            self._device.requestCloseSession()
            self._device = None
        if self._browser is not None:
            self._browser.stop()
            self._browser.setDelegate_(None)
            self._browser = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def device_added(self, device) -> None:
        if self._device is not None:
            return  # one phone at a time
        self._device = device
        self.serial = str(device.name() or "HP")
        self.tracker.reset()
        self._items = 0
        self._connected_at = time.time()
        self._ptp_live = False
        self._folders = {}
        self.state, self.message = "connecting", ""
        device.setDelegate_(self._delegate)
        self._set_event_handler(device, lambda data, d=device: self._on_ptp_event(d, data))
        self._log(f"HP terdeteksi: {self.serial}")
        device.requestOpenSession()
        self._watch(device)
        self._later(PROGRESS_EVERY, self._progress, device, 0)

    def _progress(self, device, items_before: int) -> None:
        """Log a progress line while macOS reads the file list or files are still streaming in."""
        if device is not self._device:
            return
        waited = int(time.time() - self._connected_at)
        if not self.tracker.ready:
            self._log(f"macOS membaca daftar file… {waited} dtk, {self._items} file")
        elif self._items != items_before:
            self._log(f"menerima daftar file… {waited} dtk, {self._items} file")
        elif self._items:
            self._log(f"daftar file lengkap: {self._items} file dalam {waited} dtk")
            if self.state == "loading":
                self.state, self.message = "connected", ""
            return
        self._later(PROGRESS_EVERY, self._progress, device, self._items)

    def _watch(self, device) -> None:
        self._later(STALL_AFTER, self._check_stall, device, self._items)

    def _check_stall(self, device, items_before: int) -> None:
        # Sometimes the session never reports ready; reopen unless files are still streaming in.
        if device is not self._device or self.state != "connecting":
            return
        if self._items != items_before:
            self._watch(device)
            return
        self._log(f"tidak ada respons {STALL_AFTER:.0f} dtk, membuka ulang koneksi")
        device.requestCloseSession()
        self._later(1.0, self._open_again, device)

    def device_removed(self, device) -> None:
        if self._device is None or device != self._device:
            return
        self._set_event_handler(device, None)
        self._device = None
        self._log(f"HP terputus: {self.serial}")
        self.serial = ""
        self.tracker.reset()
        self.state, self.message = "disconnected", f"HP tidak terhubung — {HINT}"

    def session_opened(self, error) -> None:
        self._log("koneksi dibuka" if error is None else f"gagal membuka koneksi: {error.localizedDescription()}")
        if error is not None:
            self.state = "error"
            self.message = f"Tidak bisa membuka HP: {error.localizedDescription()} — {HINT}"

    def catalog_ready(self, device=None) -> None:
        self.tracker.catalog_ready()
        self._log(f"macOS siap ({self._items} file sejauh ini)")
        if self._items == 0 and device is not None:
            # Empty catalog: USB mode is "Charging"/"No data transfer" (Android exposes MTP without
            # storage), or the session opened mid mode-switch and stays empty. Reopening re-reports the
            # whole catalog, so retry until files show up.
            self.state, self.message = "waiting", NO_STORAGE.format(name=self.serial)
            self._later(REOPEN_AFTER, self._reopen, device)
            return
        if self.state == "connected":
            return  # already capturing via phone events
        # Files may keep streaming in after the ready signal; _progress flips to "connected" once quiet.
        self.state, self.message = ("loading", "") if device is not None else ("connected", "")

    def _reopen(self, device) -> None:
        if device is not self._device or self._items:
            return
        self._log("isi HP tidak terlihat (mode USB Charging?) — pilih File transfer; membuka ulang koneksi")
        device.requestCloseSession()
        self._later(1.0, self._open_again, device)

    def _open_again(self, device) -> None:
        if device is self._device:
            device.requestOpenSession()
            self._watch(device)

    def items_added(self, items) -> None:
        self._items += len(items)
        if self.state == "waiting" and self._device is not None:
            self.state, self.message = "loading", ""
        for item in items:
            folder = item.parentFolder()
            name = str(item.name() or "")
            date = item.creationDate()
            created = float(date.timeIntervalSince1970()) if date is not None else None
            if self.tracker.wants(str(folder.name() or "") if folder is not None else "", name, created):
                self._log(f"screenshot baru: {name}")
                self._download(item, name)

    # ---- fast path: phone events + direct PTP commands ---------------------
    @staticmethod
    def _set_event_handler(device, handler) -> None:
        try:
            device.setPtpEventHandler_(handler)
        except AttributeError:
            pass  # macOS < 12: only the catalog path

    def _on_ptp_event(self, device, data) -> None:  # any thread
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self._ptp_event, device, bytes(data or b""))

    def _send(self, device, packet: bytes, callback) -> None:
        """Send a PTP command; callback(data: bytes | None) runs on the main thread."""
        from Foundation import NSData
        from PyObjCTools import AppHelper

        def done(data, response, error):  # any thread
            ok = error is None and ptp.response_ok(bytes(response) if response is not None else None)
            AppHelper.callAfter(callback, bytes(data) if ok and data is not None else None)

        device.requestSendPTPCommand_outData_completion_(NSData.dataWithBytes_length_(packet, len(packet)),
                                                         None, done)

    def _ptp_event(self, device, data: bytes) -> None:
        if device is not self._device:
            return
        event = ptp.parse_event(data)
        if event is None:
            return
        code, params = event
        if code not in ptp.OBJECT_EVENTS:
            return  # e.g. DevicePropChanged, also sent in USB mode Charging
        if not self._ptp_live:
            self._ptp_live = True
            waited = int(time.time() - self._connected_at)
            self._log(f"HP mengirim sinyal file baru ({waited} dtk) — screenshot langsung ditangkap")
            if self.state in ("connecting", "loading"):
                self.state, self.message = "connected", ""
        if code == ptp.OBJECT_ADDED and params:
            handle = params[0]
            self._send(device, ptp.command(ptp.GET_OBJECT_INFO, handle),
                       lambda raw: self._object_info(device, handle, raw))

    def _object_info(self, device, handle: int, raw: bytes | None) -> None:
        if raw is None or device is not self._device:
            return  # e.g. ".pending-…" already renamed: invalid handle
        try:
            info = ptp.parse_object_info(raw)
        except (IndexError, ValueError, UnicodeDecodeError):
            return
        if info.name.startswith(".") or not info.name.lower().endswith(IMG_EXT):
            return
        folder = self._folders.get(info.parent)
        if folder is None:
            self._send(device, ptp.command(ptp.GET_OBJECT_INFO, info.parent),
                       lambda raw_parent: self._folder_info(device, handle, info, raw_parent))
            return
        self._consider(device, handle, info, folder)

    def _folder_info(self, device, handle: int, info: ptp.ObjectInfo, raw: bytes | None) -> None:
        if raw is None or device is not self._device:
            return
        try:
            self._folders[info.parent] = ptp.parse_object_info(raw).name
        except (IndexError, ValueError, UnicodeDecodeError):
            return
        self._consider(device, handle, info, self._folders[info.parent])

    def _consider(self, device, handle: int, info: ptp.ObjectInfo, folder: str) -> None:
        if not self.tracker.wants(folder, info.name, info.captured):
            return
        self._log(f"screenshot baru: {info.name} (langsung, tanggal HP {info.captured_raw or '-'})")
        self._fetch(device, handle, info, retry=True)

    def _fetch(self, device, handle: int, info: ptp.ObjectInfo, retry: bool) -> None:
        def got(data: bytes | None) -> None:
            complete = data is not None and (info.size in (0, ptp.FULL_SIZE_UNKNOWN) or len(data) == info.size)
            if complete:
                self.on_image(data, Path(info.name).suffix.lower())
            elif retry and device is self._device:
                self._later(1.0, self._fetch, device, handle, info, False)  # phone may still be writing
            else:
                self.on_error(f"Gagal menarik screenshot {info.name}")

        self._send(device, ptp.command(ptp.GET_OBJECT, handle), got)

    # ---- fallback path: ImageCaptureCore catalog ----------------------------
    def _download(self, item, name: str) -> None:
        import ImageCaptureCore as IC
        from Foundation import NSURL

        options = {IC.ICDownloadsDirectoryURL: NSURL.fileURLWithPath_(str(self._tmp)), IC.ICOverwrite: True}

        def done(filename, error):  # runs on an ImageCaptureCore worker thread
            path = self._tmp / str(filename or name)
            try:
                if error is not None or not path.is_file():
                    self.on_error(f"Gagal menarik screenshot {name}")
                    return
                data = path.read_bytes()
            finally:
                path.unlink(missing_ok=True)
            self.on_image(data, Path(name).suffix.lower() or ".png")

        item.requestDownloadWithOptions_completion_(options, done)
