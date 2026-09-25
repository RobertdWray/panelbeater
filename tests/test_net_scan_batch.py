# Copyright (C) 2026 Rob Wray
# SPDX-License-Identifier: GPL-2.0-or-later
"""The network scan_batch() must never hand back an incomplete batch as a finished one.

The fake below stands in for Session at the scsi() boundary, so the real
scan_batch() runs its setup, per-sheet feeds, reads, sense decoding and
terminators. It is scripted per sheet: each entry says what the scanner
returns for the front and the back. Like the real unit, an e0 into an empty
hopper answers status 0 and the next REQUEST SENSE says 03/80/03, once.

The hopper sensor is deliberately unavailable inside scan_batch(): on the
iX1500 this was measured on, GET_HW_STATUS reports "empty" from about two
seconds after the first sheet is read until the job closes, whatever is
loaded. Only scan_to_dir() may ask it, once, at rest, before the batch.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from panelbeater import cli, daemon, scanning
from panelbeater.config import Config
from panelbeater.errors import BatchAborted
from panelbeater.scanning import scan_batch, scan_to_dir

E0, D6, D4, READ, REQUEST_SENSE = 0xE0, 0xD6, 0xD4, 0x28, 0x03
FRONT, BACK = 0x00, 0x80

# What a side "does": a complete JPEG, one cut off before its EOI, nothing at
# all, a READ that fails, or a sense fault (ascq under key 3 / asc 0x80)
# reported for that side.
Good = "good"
Truncated = "truncated"
Nothing = "nothing"
ReadFails = "readfails"
JAM, COVER_OPEN, DOUBLE_FEED, HOPPER_EMPTY = 0x01, 0x02, 0x07, 0x03

JPEG = b"\xff\xd8\xff" + bytes(range(256)) * 60 + b"\xff\xd9"  # > 10000 bytes


def sense_bytes(key: int = 0, asc: int = 0, ascq: int = 0) -> bytes:
    out = bytearray(0x12)
    out[2] = key & 0x0F
    out[0x0C] = asc
    out[0x0D] = ascq
    return bytes(out)


class FakeSession:
    """Just enough of Session for scan_batch(): scsi(), host and mac."""

    host = "192.0.2.1"
    mac = b"\x00" * 6

    def __init__(
        self, sheets, e0_status=None, e0_sense=None, setup_status=None, sense=None
    ):
        self.sheets = list(sheets)
        self.e0_status = dict(e0_status or {})  # sheet number -> status
        self.e0_sense = dict(e0_sense or {})  # sheet number -> sense after its e0
        self.setup_status = dict(setup_status or {})  # cdb[0] -> status
        self.sense_override = dict(sense or {})  # (sheet, side) -> sense bytes
        self.cdbs: list[bytes] = []
        self.sheet = 0
        self.current: dict | None = None
        self.pending_sense = sense_bytes()

    def scsi(self, cdb, read_len=0, out=b"", collect=False, quiet=6.0):
        self.cdbs.append(bytes(cdb))
        op = cdb[0]
        if op in self.setup_status:
            return self.setup_status[op], b""
        if op == E0:
            if self.sheets:
                self.sheet += 1
                self.current = self.sheets.pop(0)
                self.pending_sense = self.e0_sense.get(self.sheet, sense_bytes())
                return self.e0_status.get(self.sheet, 0), b""
            # No paper: the real unit still answers 0 (after ~0.9 s instead of
            # 0.05 s) and the next REQUEST SENSE reports hopper empty, once.
            self.current = None
            self.pending_sense = sense_bytes(0x03, 0x80, HOPPER_EMPTY)
            return 0, b""
        if op == READ and cdb[2] == 0:
            side = cdb[5]
            plan = self.current[side] if self.current else Nothing
            self.pending_sense = self._sense_for(side, plan)
            if plan == ReadFails:
                return -1, b""
            if plan == Truncated:
                return 0, JPEG[:5000]
            if plan == Good:
                return 0, JPEG
            return 0, b""  # Nothing, or a fault: no image came
        if op == REQUEST_SENSE:
            reply, self.pending_sense = self.pending_sense, sense_bytes()  # one-shot
            return 0, reply
        return 0, b""

    def _sense_for(self, side, plan) -> bytes:
        if (self.sheet, side) in self.sense_override:
            return self.sense_override[(self.sheet, side)]
        if isinstance(plan, int):
            return sense_bytes(0x03, 0x80, plan)
        return sense_bytes()

    def count(self, op: int) -> int:
        return sum(1 for c in self.cdbs if c[0] == op)


def duplex(front=Good, back=Good) -> dict[int, object]:
    return {FRONT: front, BACK: back}


@pytest.fixture
def hopper(monkeypatch):
    """Inside scan_batch() the hopper sensor must never be consulted: it lies
    once a sheet has fed. Any call is a test failure."""

    def never(host, mac, length=0x30):
        raise AssertionError("scan_batch() consulted the hopper sensor mid-batch")

    monkeypatch.setattr(scanning, "hw_status", never)
    monkeypatch.setattr(scanning, "SETUP_SETTLE_S", 0)
    return lambda s: s


def run(s: FakeSession, tmp_path: Path, max_sheets: int = 100) -> int:
    return scan_batch(s, str(tmp_path / "page"), max_sheets, log=lambda _m: None)


def pages_on_disk(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.glob("page-*.jpg"))


# -- clean batches -------------------------------------------------------------


def test_clean_two_sheet_batch_files_four_sides_and_terminates_once(tmp_path, hopper):
    s = hopper(FakeSession([duplex(), duplex()]))
    assert run(s, tmp_path) == 4
    assert pages_on_disk(tmp_path) == [f"page-000{i}.jpg" for i in range(1, 5)]
    assert s.count(E0) == 4  # two sheets, the feed that found the hopper empty, e0 end
    assert s.count(D6) == 1


def test_later_sheets_are_fed_without_consulting_the_hopper_sensor(tmp_path, hopper):
    """Three sheets, six sides, four feeds; the hopper fixture raises if asked."""
    s = hopper(FakeSession([duplex(), duplex(), duplex()]))
    assert run(s, tmp_path) == 6
    assert s.count(E0) == 5
    assert s.count(D6) == 1


def test_fault_reported_on_the_feed_aborts(tmp_path, hopper):
    s = hopper(
        FakeSession([duplex(), duplex()], e0_sense={2: sense_bytes(0x03, 0x80, JAM)})
    )
    with pytest.raises(BatchAborted, match=r"sheet 2: paper jam on feed"):
        run(s, tmp_path)
    assert s.count(D6) == 1


def test_hopper_empty_sense_on_an_unread_sheet_ends_the_batch_cleanly(tmp_path, hopper):
    """The scanner said paper was there, then reported empty before feeding:
    nothing was lost, so this is a clean end with what was scanned."""
    s = hopper(FakeSession([duplex(), duplex(HOPPER_EMPTY, Nothing)]))
    assert run(s, tmp_path) == 2
    assert s.count(D6) == 1


def test_max_sheets_ends_the_batch_cleanly_without_another_feed(tmp_path, hopper):
    s = hopper(FakeSession([duplex(), duplex(), duplex()]))
    assert run(s, tmp_path, max_sheets=2) == 4
    assert s.count(E0) == 3  # two sheets, then e0 end; no third feed


# -- faults abort, and the terminators still go out -----------------------------


def test_jam_after_a_good_sheet_aborts_and_still_terminates(tmp_path, hopper):
    s = hopper(FakeSession([duplex(), duplex(JAM, Nothing)]))
    with pytest.raises(BatchAborted, match=r"sheet 2: paper jam on the front"):
        run(s, tmp_path)
    assert s.count(E0) == 3
    assert s.count(D6) == 1


@pytest.mark.parametrize(
    "ascq, name",
    [
        (COVER_OPEN, "cover open"),
        (DOUBLE_FEED, "double feed"),
        (0x08, "no paper picked"),
    ],
)
def test_named_faults_on_the_back_abort(tmp_path, hopper, ascq, name):
    s = hopper(FakeSession([duplex(Good, ascq)]))
    with pytest.raises(BatchAborted, match=rf"sheet 1: {name} on the back"):
        run(s, tmp_path)


def test_unknown_sense_key_aborts(tmp_path, hopper):
    s = hopper(
        FakeSession([duplex()], sense={(1, FRONT): sense_bytes(0x04, 0x44, 0x00)})
    )
    with pytest.raises(
        BatchAborted, match=r"sheet 1: sense key 0x4 asc 0x44 ascq 0x00 on the front"
    ):
        run(s, tmp_path)


def test_hopper_empty_sense_after_the_front_was_read_aborts(tmp_path, hopper):
    s = hopper(FakeSession([duplex(Good, HOPPER_EMPTY)]))
    with pytest.raises(
        BatchAborted, match=r"sheet 1: hopper empty reported after the front was read"
    ):
        run(s, tmp_path)


def test_missing_back_side_aborts(tmp_path, hopper):
    """Duplex is fixed by the D4 block, so a sheet with only a front is a fault."""
    s = hopper(FakeSession([duplex(Good, Nothing)]))
    with pytest.raises(BatchAborted, match=r"sheet 1: no image for the back"):
        run(s, tmp_path)


def test_failed_read_status_aborts(tmp_path, hopper):
    s = hopper(FakeSession([duplex(ReadFails, Good)]))
    with pytest.raises(
        BatchAborted, match=r"sheet 1: READ failed on the front \(status -1\)"
    ):
        run(s, tmp_path)


def test_image_without_an_eoi_aborts(tmp_path, hopper):
    s = hopper(FakeSession([duplex(Good, Truncated)]))
    with pytest.raises(BatchAborted, match=r"sheet 1: incomplete image for the back"):
        run(s, tmp_path)
    assert pages_on_disk(tmp_path) == ["page-0001.jpg"]  # the good front was written


def test_e0_refused_aborts(tmp_path, hopper):
    s = hopper(FakeSession([duplex(), duplex()], e0_status={2: -1}))
    with pytest.raises(BatchAborted, match=r"sheet 2: e0 refused \(status -1\)"):
        run(s, tmp_path)
    assert s.count(D6) == 1


def test_setup_failure_aborts_and_still_terminates(tmp_path, hopper):
    """A refused d4 used to return with no e0/d6 at all, leaving the job open."""
    s = hopper(FakeSession([duplex()], setup_status={D4: -1}))
    with pytest.raises(BatchAborted, match=r"setup: d4 params refused \(status -1\)"):
        run(s, tmp_path)
    assert s.count(E0) == 1
    assert s.count(D6) == 1


# -- scan_to_dir(): session user and profile -----------------------------------

CLOUD = {
    "prof_id": "CLOUD",
    "prof_name": "Send to ScanSnap Cloud",
    "prof_type": 1,
    "user_id": "0202",
}
HOME = {"prof_id": "HOME", "prof_name": "Wray Scan", "prof_type": 0, "user_id": "8E02"}


class RecordingSession:
    instances: list[RecordingSession] = []

    def __init__(self, host, host_id):
        self.host, self.host_id = host, host_id
        self.mac = b"\x00" * 6
        self.opened_as: str | None = None
        self.selected: str | None = None
        RecordingSession.instances.append(self)

    def open_session(self, user_id="", port=0):
        self.opened_as = user_id
        return 0

    def profiles(self):
        return [CLOUD, HOME]

    def select_profile(self, prof_id):
        self.selected = prof_id
        return 0

    def connect(self):
        pass

    def close(self):
        pass


@pytest.fixture
def recording(monkeypatch, tmp_path):
    RecordingSession.instances.clear()
    monkeypatch.setattr(scanning, "Session", RecordingSession)
    monkeypatch.setattr(
        scanning, "hw_status", lambda *a, **k: bytes(0x30)
    )  # paper present
    monkeypatch.setattr(scanning, "scan_batch", lambda *a, **k: 0)
    return lambda **kw: (
        scan_to_dir("192.0.2.1", "51c897c7ba596804", str(tmp_path / "page"),
                    skip_register=True, log=lambda _m: None, **kw),
        RecordingSession.instances[-1],
    )  # fmt: skip


def test_scan_to_dir_opens_the_session_as_the_configured_user(recording):
    _, s = recording(user_id="CAFE")
    assert s.opened_as == "CAFE"


def test_scan_to_dir_defaults_to_a_host_profile_not_the_cloud_one(recording):
    _, s = recording()
    assert s.selected == "HOME"


def test_scan_to_dir_honours_an_explicit_profile(recording):
    _, s = recording(prof_id="CLOUD")
    assert s.selected == "CLOUD"


def test_scan_to_dir_refuses_to_start_when_the_hopper_status_is_unreadable(
    recording, monkeypatch
):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(scanning, "hw_status", boom)
    with pytest.raises(OSError, match="connection reset"):
        recording()


def test_scan_to_dir_treats_a_short_hopper_reply_as_unreadable_not_empty(
    recording, monkeypatch
):
    monkeypatch.setattr(scanning, "hw_status", lambda *a, **k: b"\x00\x00")
    with pytest.raises(OSError, match="too short"):
        recording()


# -- the callers publish nothing on an abort -------------------------------------


def test_daemon_capture_returns_no_pages_on_abort(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise BatchAborted("sheet 2: paper jam on the front")

    monkeypatch.setattr(daemon, "scan_to_dir", boom)
    logged: list[str] = []
    pages, work = daemon.capture(
        Config({}), "192.0.2.1", "51c897c7ba596804", log=logged.append
    )
    assert pages == []
    assert work is not None and work.is_dir()
    assert any("scan failed: sheet 2: paper jam on the front" in m for m in logged)


def test_cli_scan_exits_1_and_names_the_fault_on_abort(monkeypatch, capsys):
    def boom(*a, **k):
        raise BatchAborted("sheet 2: paper jam on the front")

    monkeypatch.setattr(cli, "scan_to_dir", boom)
    monkeypatch.setattr(cli, "pick_transport", lambda cfg, o="": "network")
    monkeypatch.setattr(cli, "resolve_scanner", lambda cfg, o="": "192.0.2.1")
    args = argparse.Namespace(
        transport="", scanner="", prof_id="", max_sheets=0, skip_register=False
    )
    assert cli.cmd_scan(args, Config({})) == 1
    assert "sheet 2: paper jam on the front" in capsys.readouterr().err
