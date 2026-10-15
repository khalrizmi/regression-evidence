from app.capture_mtp_win import Found, MtpWatcher

T = 1_000_000.0  # moment the phone was found


class FakeShell:
    """Phone as seen through the Explorer shell: folder key -> {name: (created, data)}."""

    def __init__(self, name="POCO", storages=1, folders=None):
        self.name = name
        self.storages = storages
        self.files = folders if folders is not None else {"DCIM/Screenshots": {}}
        self.gone = False
        self.dates_asked = []
        self.short_copies = 0

    def find(self):
        if self.gone:
            return None
        keys = list(self.files) if self.storages else []
        return Found(self.name, self.storages, [(k, k) for k in keys])

    def list(self, folder):
        if self.gone:
            raise OSError("device disconnected")
        return {name: (folder, name) for name in self.files[folder]}

    def date(self, item):
        folder, name = item
        self.dates_asked.append(name)
        return self.files[folder][name][0]

    def size(self, item):
        folder, name = item
        return len(self.files[folder][name][1])

    def copy(self, item, name, size):
        folder, _ = item
        data = self.files[folder][name][1]
        if self.short_copies:
            self.short_copies -= 1
            return data[:-1]
        return data


def make(shell):
    images, errors = [], []
    w = MtpWatcher(lambda data, ext: images.append((data, ext)), errors.append, make_shell=lambda: shell)
    w._now = lambda: T
    return w, images, errors


def test_files_already_on_phone_are_baseline_and_new_shots_are_captured():
    shell = FakeShell(folders={"DCIM/Screenshots": {"Screenshot_old.jpg": (T - 500, b"OLD")}})
    w, images, errors = make(shell)
    w.poll_once(shell)
    assert w.status() == {"state": "connected", "message": "", "serial": "POCO"}
    assert images == []
    shell.files["DCIM/Screenshots"]["Screenshot_new.jpg"] = (T + 5, b"NEW")
    w.poll_once(shell)
    w.poll_once(shell)  # never twice
    assert images == [(b"NEW", ".jpg")] and errors == []
    assert shell.dates_asked == ["Screenshot_old.jpg", "Screenshot_new.jpg"]  # each name once


def test_pending_files_and_old_files_flooding_in_are_ignored():
    shell = FakeShell()  # phone locked at connect: empty listing first
    w, images, errors = make(shell)
    w.poll_once(shell)
    files = shell.files["DCIM/Screenshots"]
    files["Screenshot_2026-03-30.jpg"] = (T - 86400 * 200, b"OLD")
    files[".pending-1792-Screenshot_x.jpg"] = (T + 5, b"PART")
    files["clip.mp4"] = (T + 5, b"MP4")
    w.poll_once(shell)
    assert images == []
    files["Screenshot_x.jpg"] = (T + 6, b"PNG")  # renamed when the phone is done writing
    w.poll_once(shell)
    assert images == [(b"PNG", ".jpg")]


def test_short_copy_is_retried_then_reported():
    shell = FakeShell()
    w, images, errors = make(shell)
    w.poll_once(shell)
    shell.files["DCIM/Screenshots"]["Screenshot_a.png"] = (T + 1, b"PNGDATA")
    shell.short_copies = 1
    w.poll_once(shell)
    assert images == [(b"PNGDATA", ".png")] and errors == []
    shell.files["DCIM/Screenshots"]["Screenshot_b.png"] = (T + 2, b"PNGDATA")
    shell.short_copies = 2
    w.poll_once(shell)
    assert len(images) == 1 and errors == ["Gagal menarik screenshot Screenshot_b.png"]


def test_no_phone_no_storage_and_no_screenshots_folder_messages():
    shell = FakeShell()
    shell.gone = True
    w, _, _ = make(shell)
    w.poll_once(shell)
    st = w.status()
    assert st["state"] == "disconnected" and "File transfer" in st["message"]

    shell = FakeShell(storages=0)  # USB mode Charging, or phone locked
    w, _, _ = make(shell)
    w.poll_once(shell)
    st = w.status()
    assert st["state"] == "waiting" and "File transfer" in st["message"] and "POCO" in st["message"]

    shell = FakeShell(folders={})
    w, _, _ = make(shell)
    w.poll_once(shell)
    st = w.status()
    assert st["state"] == "waiting" and "Screenshots" in st["message"]


def test_unplug_and_replug_rebaselines_and_skips_shots_taken_while_away():
    shell = FakeShell()
    w, images, errors = make(shell)
    w.poll_once(shell)
    shell.gone = True
    w.poll_once(shell)
    assert w.status()["state"] == "disconnected" and w.status()["serial"] == ""
    assert errors == []
    shell.files["DCIM/Screenshots"]["Screenshot_while_away.jpg"] = (T + 10, b"AWAY")
    shell.gone = False
    w._now = lambda: T + 100  # replugged later
    w.poll_once(shell)
    assert w.status()["state"] == "connected"
    shell.files["DCIM/Screenshots"]["Screenshot_after.jpg"] = (T + 120, b"AFTER")
    w.poll_once(shell)
    assert images == [(b"AFTER", ".jpg")]


def test_listing_that_empties_means_files_hidden():
    shell = FakeShell(folders={"DCIM/Screenshots": {"Screenshot_old.jpg": (T - 60, b"OLD")}})
    w, images, _ = make(shell)
    w.poll_once(shell)
    shell.files["DCIM/Screenshots"] = {}  # screen locked: Android hides the files
    w.poll_once(shell)
    assert w.status()["state"] == "waiting" and "kunci" in w.status()["message"]
    shell.files["DCIM/Screenshots"] = {"Screenshot_old.jpg": (T - 60, b"OLD")}
    w.poll_once(shell)
    assert w.status()["state"] == "connected" and images == []


def test_watches_every_screenshots_folder():
    shell = FakeShell(folders={"DCIM/Screenshots": {}, "Pictures/Screenshots": {}})
    w, images, _ = make(shell)
    w.poll_once(shell)
    shell.files["Pictures/Screenshots"]["Screenshot_p.jpg"] = (T + 1, b"P")
    w.poll_once(shell)
    assert images == [(b"P", ".jpg")]


def test_shot_taken_while_the_phone_was_being_found_is_captured():
    # replugged in mode Charging, then File transfer chosen and a screenshot taken during the first listing
    shell = FakeShell(storages=0)
    w, images, _ = make(shell)
    w.poll_once(shell)
    assert w.status()["state"] == "waiting"
    w._now = lambda: T + 20  # File transfer chosen later
    shell.storages = 1
    shell.files["DCIM/Screenshots"] = {"Screenshot_old.jpg": (T - 60, b"OLD"),
                                       "Screenshot_during_listing.jpg": (T + 15, b"NEW")}
    w.poll_once(shell)
    assert w.status()["state"] == "connected"
    assert images == [(b"NEW", ".jpg")]


def test_shot_taken_just_before_the_phone_reappeared_is_captured():
    # choosing File transfer makes Windows drop and re-add the phone for a few seconds
    shell = FakeShell()
    w, images, _ = make(shell)
    w.poll_once(shell)
    shell.gone = True
    w.poll_once(shell)
    shell.gone = False
    w._now = lambda: T + 100
    shell.files["DCIM/Screenshots"]["Screenshot_in_gap.jpg"] = (T + 97, b"GAP")
    w.poll_once(shell)
    assert images == [(b"GAP", ".jpg")]


def test_connecting_status_shows_elapsed_wait():
    shell = FakeShell()
    w, _, _ = make(shell)
    w.state, w.serial, w._connecting_at = "connecting", "POCO", T - 42
    st = w.status()
    assert "42 dtk" in st["message"] and "POCO" in st["message"]
