import shlex

from app.capture_adb import Adb, Result, Watcher, sh_quote

DIR = "/sdcard/Pictures/Screenshots"


class FakeDevice:
    def __init__(self):
        self.files = {}
        self.connected = True
        self.fail_pull = set()
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[1:] == ["devices"]:
            return Result(0, b"List of devices attached\nSER1\tdevice\nSER2\tunauthorized\n\n")
        if not self.connected:
            return Result(1, b"", b"error: device 'SER1' not found")
        script = args[-1]
        if "exec-out" in args:
            path = shlex.split(script)[1]
            if path in self.fail_pull or path not in self.files:
                return Result(1, b"")
            return Result(0, self.files[path])
        if script.startswith("dumpsys"):
            return Result(0, b"Packages:\n    versionCode=12 minSdk=24\n    versionName=1.2.4\n")
        lines = [f"{len(data)}|{path}" for path, data in self.files.items()]
        return Result(0, "\n".join(lines).encode())


def make(dev, **kw):
    got, errs = [], []
    w = Watcher(Adb("adb", dev), "SER1", [DIR], lambda d, e: got.append((d, e)), errs.append, **kw)
    return w, got, errs


def test_devices_and_version():
    adb = Adb("adb", FakeDevice())
    assert adb.devices() == ["SER1"]
    assert adb.version_name("SER1", "com.example.app") == "1.2.4"
    assert adb.version_name("SER1", "") == ""


def test_sh_quote():
    assert sh_quote("a b") == "'a b'"
    assert sh_quote("it's") == "'it'\\''s'"


def test_ignores_existing_and_waits_for_stable_size():
    dev = FakeDevice()
    dev.files[f"{DIR}/old.png"] = b"old"
    w, got, _ = make(dev)
    w.poll_once()  # baseline
    dev.files[f"{DIR}/new.png"] = b"abc"
    dev.files[f"{DIR}/clip.mp4"] = b"video"
    w.poll_once()  # first sight: size recorded only
    assert got == []
    w.poll_once()
    assert got == [(b"abc", ".png")]
    w.poll_once()
    assert len(got) == 1
    assert w.status()["state"] == "connected"


def test_pull_quotes_paths_with_spaces_and_quotes():
    dev = FakeDevice()
    w, got, _ = make(dev)
    w.poll_once()
    for name in ("Screenshot (1).JPG", "it's.png"):
        dev.files[f"{DIR}/{name}"] = name.encode()
    w.poll_once()
    w.poll_once()
    assert sorted(got) == sorted([(b"Screenshot (1).JPG", ".jpg"), (b"it's.png", ".png")])


def test_disconnect_and_recover():
    dev = FakeDevice()
    w, got, _ = make(dev)
    dev.connected = False
    w.poll_once()
    assert w.status()["state"] == "disconnected"
    dev.connected = True
    dev.files[f"{DIR}/before-connect.png"] = b"x"
    w.poll_once()  # baseline happens on first successful listing
    w.poll_once()
    assert got == [] and w.status()["state"] == "connected"


def test_pull_failure_retries_then_reports():
    dev = FakeDevice()
    w, got, errs = make(dev, max_attempts=3)
    w.poll_once()
    path = f"{DIR}/bad.png"
    dev.files[path] = b"x"
    dev.fail_pull.add(path)
    for _ in range(6):
        w.poll_once()
    assert got == []
    assert len(errs) == 1 and "bad.png" in errs[0]
