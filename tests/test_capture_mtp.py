from app.capture_mtp_mac import MtpTracker

T = 1_000_000.0  # moment the phone (re)connected


def test_items_before_catalog_ready_are_baseline():
    t = MtpTracker(now=T)
    assert t.wants("Screenshots", "Screenshot_old.jpg") is False
    t.catalog_ready()
    assert t.wants("Screenshots", "Screenshot_old.jpg") is False  # same name reported again
    assert t.wants("Screenshots", "Screenshot_new.jpg") is True
    assert t.wants("Screenshots", "Screenshot_new.jpg") is False  # never twice


def test_filters_pending_folders_and_types():
    t = MtpTracker(now=T)
    t.catalog_ready()
    assert t.wants("Screenshots", ".pending-1792674370-Screenshot_a.jpg", T + 1) is False
    assert t.wants("Camera", "IMG_001.jpg", T + 1) is False
    assert t.wants("Screenshots", "clip.mp4", T + 1) is False
    assert t.wants("screenshots", "Screenshot (1).PNG", T + 1) is True


def test_reset_on_reconnect_ignores_shots_taken_while_unplugged():
    t = MtpTracker(now=T)
    t.catalog_ready()
    t.reset(now=T + 100)
    assert t.ready is False
    assert t.wants("Screenshots", "Screenshot_while_unplugged.jpg", T + 50) is False
    t.catalog_ready()
    assert t.wants("Screenshots", "Screenshot_after.jpg", T + 120) is True


def test_old_files_flooding_in_after_catalog_ready_are_ignored():
    # phone locked at reconnect: catalog reported complete while empty, real listing arrives after unlock
    t = MtpTracker(now=T)
    t.catalog_ready()
    assert t.wants("Screenshots", "Screenshot_2026-03-30.jpg", T - 86400 * 200) is False
    assert t.wants("Screenshots", "Screenshot_earlier_today.jpg", T - 60) is False
    assert t.wants("Screenshots", "Screenshot_taken_during_flood.jpg", T + 30) is True


def test_dated_new_shot_counts_even_before_catalog_ready():
    t = MtpTracker(now=T)
    assert t.wants("Screenshots", "Screenshot_new.jpg", T + 5) is True


class FakeDevice:
    def __init__(self):
        self.calls = []

    def requestCloseSession(self):
        self.calls.append("close")

    def requestOpenSession(self):
        self.calls.append("open")

    def setDelegate_(self, delegate):
        pass

    def name(self):
        return "POCO"


def make_watcher():
    from app.capture_mtp_mac import MtpWatcher
    w = MtpWatcher(lambda data, ext: None, lambda msg: None)
    w.scheduled = []
    w._later = lambda delay, fn, *args: w.scheduled.append((fn, args))
    return w


def run_scheduled(w, limit=10):
    """Run scheduled callbacks in order; progress logging is ignored here."""
    while w.scheduled and limit:
        fn, args = w.scheduled.pop(0)
        if fn == w._progress:
            continue
        fn(*args)
        limit -= 1


def test_empty_catalog_warns_and_reopens_session():
    w = make_watcher()
    dev = FakeDevice()
    w.device_added(dev)
    assert dev.calls == ["open"]
    w.catalog_ready(dev)
    assert w.status()["state"] == "waiting"
    assert "File transfer" in w.status()["message"]
    run_scheduled(w)
    assert dev.calls == ["open", "close", "open"]
    w.items_added([])  # reopened session starts reporting files
    w._items = 1
    w.catalog_ready(dev)
    assert w.status()["state"] == "loading"
    w._stop_main()


def test_no_reopen_when_files_arrive_after_ready_or_device_gone():
    w = make_watcher()
    dev = FakeDevice()
    w.device_added(dev)
    w.catalog_ready(dev)
    w._items = 5  # flood arrived after the ready signal
    run_scheduled(w)
    assert dev.calls == ["open"]

    w2 = make_watcher()
    dev2 = FakeDevice()
    w2.device_added(dev2)
    w2.catalog_ready(dev2)
    w2.device_removed(dev2)
    run_scheduled(w2)
    assert dev2.calls == ["open"]
    w._stop_main()
    w2._stop_main()


def test_stalled_connect_reopens_but_not_while_files_stream_in():
    from app.capture_mtp_mac import MtpWatcher
    w = make_watcher()
    dev = FakeDevice()
    w.device_added(dev)
    run_scheduled(w, limit=2)  # stall check, then open again
    assert dev.calls == ["open", "close", "open"]
    assert w.status()["state"] == "connecting"

    w2 = make_watcher()
    dev2 = FakeDevice()
    w2.device_added(dev2)
    w2._items = 3000  # catalog still streaming in
    run_scheduled(w2, limit=1)
    assert dev2.calls == ["open"]
    assert [fn for fn, _ in w2.scheduled if fn == w2._check_stall]  # keeps watching

    w3 = make_watcher()
    dev3 = FakeDevice()
    w3.device_added(dev3)
    w3._items = 1
    w3.catalog_ready(dev3)
    run_scheduled(w3, limit=1)
    assert dev3.calls == ["open"]
    for x in (w, w2, w3):
        x._stop_main()


def test_connecting_status_shows_elapsed_wait():
    w = make_watcher()
    w.device_added(FakeDevice())
    w._connected_at -= 42
    st = w.status()
    assert st["state"] == "connecting"
    assert "42 dtk" in st["message"] and "POCO" in st["message"]
    w._stop_main()


def test_progress_logs_until_file_list_complete(capsys):
    from app.capture_mtp_mac import MtpWatcher
    w = make_watcher()
    dev = FakeDevice()
    w.device_added(dev)
    progress = [(fn, args) for fn, args in w.scheduled if fn == w._progress]
    assert progress
    w._progress(dev, 0)  # still connecting
    w._items = 8000
    w.catalog_ready(dev)
    assert w.status()["state"] == "loading"
    assert "8000 file" in w.status()["message"]
    w._progress(dev, 0)  # files streaming in
    assert w.status()["state"] == "loading"
    w._progress(dev, 8000)  # quiet: list complete
    assert w.status() == {"state": "connected", "message": "", "serial": "POCO"}
    err = capsys.readouterr().err
    assert "membaca daftar file" in err
    assert "menerima daftar file" in err
    assert "daftar file lengkap: 8000 file" in err
    w._stop_main()


# ---- fast path: phone events + direct PTP commands -------------------------
import struct  # noqa: E402

from app import ptp  # noqa: E402
from test_ptp import object_info  # noqa: E402

SHOT_DATE = "20330518T063320"  # phone local time


def local(text):
    return ptp.parse_date(text)


class FakePhone(FakeDevice):
    """Answers GetObjectInfo/GetObject from a table of handle -> (info bytes, file bytes)."""

    def __init__(self, objects):
        super().__init__()
        self.objects = objects
        self.sent = []

    def answer(self, packet):
        _len, _kind, code, _tid, handle = struct.unpack("<IHHII", packet)
        self.sent.append((code, handle))
        info, content = self.objects.get(handle, (None, None))
        return info if code == ptp.GET_OBJECT_INFO else content


def fast_watcher(phone, since):
    w = make_watcher()
    images, errors = [], []
    w.on_image = lambda data, ext: images.append((data, ext))
    w.on_error = errors.append
    w._send = lambda device, packet, callback: callback(device.answer(packet))
    w.device_added(phone)
    w.tracker.since = since
    return w, images, errors


def event(code, handle):
    return struct.pack("<IHHII", 16, 4, code, 1, handle)


def test_phone_event_fetches_new_screenshot_before_catalog():
    since = local(SHOT_DATE) - 10
    phone = FakePhone({
        10: (object_info("Screenshot_new.jpg", 5, 4, SHOT_DATE), b"JPEG"),
        5: (object_info("Screenshots", 1, 0, ""), None),
    })
    w, images, errors = fast_watcher(phone, since)
    assert w.status()["state"] == "connecting"
    w._ptp_event(phone, event(ptp.OBJECT_ADDED, 10))
    assert images == [(b"JPEG", ".jpg")]
    assert errors == []
    assert w.status()["state"] == "connected"
    # the same file reported later by the catalog is not captured twice
    assert w.tracker.wants("Screenshots", "Screenshot_new.jpg", since + 20) is False
    w._stop_main()


def test_phone_event_ignores_old_pending_other_folders_and_invalid_handles():
    since = local(SHOT_DATE) + 60  # every file below was created before the phone connected
    phone = FakePhone({
        10: (object_info("Screenshot_replayed_old.jpg", 5, 4, SHOT_DATE), b"JPEG"),
        11: (object_info(".pending-1792-Screenshot_x.jpg", 5, 4, "20330518T070000"), b"JPEG"),
        12: (object_info("IMG_001.jpg", 6, 4, "20330518T070000"), b"JPEG"),
        5: (object_info("Screenshots", 1, 0, ""), None),
        6: (object_info("Camera", 1, 0, ""), None),
    })
    w, images, errors = fast_watcher(phone, since)
    for handle in (10, 11, 12, 99):
        w._ptp_event(phone, event(ptp.OBJECT_ADDED, handle))
    w._ptp_event(phone, event(0x4007, 10))  # ObjectInfoChanged: no fetch
    assert images == [] and errors == []
    assert (ptp.GET_OBJECT_INFO, 5) in phone.sent and (ptp.GET_OBJECT_INFO, 6) in phone.sent
    assert not [s for s in phone.sent if s[0] == ptp.GET_OBJECT]
    w._stop_main()


def test_partial_download_is_retried_once():
    since = local(SHOT_DATE) - 10
    phone = FakePhone({
        10: (object_info("Screenshot_new.jpg", 5, 8, SHOT_DATE), b"JPE"),  # info says 8 bytes
        5: (object_info("Screenshots", 1, 0, ""), None),
    })
    w, images, errors = fast_watcher(phone, since)
    w._ptp_event(phone, event(ptp.OBJECT_ADDED, 10))
    retries = [(fn, args) for fn, args in w.scheduled if fn == w._fetch]
    assert retries and images == []
    phone.objects[10] = (phone.objects[10][0], b"JPEGJPEG")
    fn, args = retries[0]
    fn(*args)
    assert images == [(b"JPEGJPEG", ".jpg")]
    w._stop_main()


def test_device_property_events_do_not_mark_capture_live():
    phone = FakePhone({})
    w, images, errors = fast_watcher(phone, 0)
    w._ptp_event(phone, event(0x4006, 20481))  # DevicePropChanged, seen in USB mode Charging
    assert w.status()["state"] == "connecting" and phone.sent == []
    w._ptp_event(phone, event(ptp.OBJECT_INFO_CHANGED, 3253))
    assert w.status()["state"] == "connected"
    w._stop_main()
