from app.capture_auto import AutoWatcher


class FakeInner:
    def __init__(self, kind, serial=""):
        self.kind, self.serial = kind, serial
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self):
        return {"state": "connected", "message": "", "serial": self.serial or "POCO"}


def make(adb_serials, with_mtp=True):
    made = []

    def make_adb(serial):
        made.append(FakeInner("adb", serial))
        return made[-1]

    def make_mtp():
        made.append(FakeInner("mtp"))
        return made[-1]

    w = AutoWatcher(lambda: list(adb_serials), make_adb, make_mtp if with_mtp else None)
    return w, made


def test_uses_file_transfer_without_adb_phone():
    w, made = make([])
    w.poll_once()
    assert [(m.kind, m.started) for m in made] == [("mtp", True)]
    assert w.status() == {"state": "connected", "message": "", "serial": "POCO", "via": "File transfer"}


def test_prefers_adb_and_runs_only_one_path():
    w, made = make(["RX8N"])
    w.poll_once()
    w.poll_once()  # unchanged: nothing restarted
    assert [(m.kind, m.serial) for m in made] == [("adb", "RX8N")]
    assert w.status()["via"] == "adb"


def test_switches_when_phone_is_swapped():
    serials = []
    w, made = make(serials)
    w.poll_once()  # phone without Developer options
    serials.append("RX8N")  # swapped for a USB-debugging phone
    w.poll_once()
    assert made[0].kind == "mtp" and made[0].stopped
    assert made[1].kind == "adb" and made[1].started and not made[1].stopped
    serials.clear()  # back to the phone without Developer options
    w.poll_once()
    assert made[1].stopped
    assert made[2].kind == "mtp" and made[2].started
    w.stop()
    assert made[2].stopped


def test_without_file_transfer_support_waits_for_adb():
    w, made = make([], with_mtp=False)
    w.poll_once()
    assert made == []
    st = w.status()
    assert st["state"] == "disconnected" and "USB debugging" in st["message"] and "File transfer" not in st["message"]
