# Copyright (C) 2026 Rob Wray
# SPDX-License-Identifier: GPL-2.0-or-later
"""scan_batch() must never hand back a partial stack as a finished one.

The fake below stands in for the USB transport at the command() / hw_status()
boundary, so the real read_sheet() and scan_batch() run. It is scripted per
sheet: each entry says what the scanner does when that sheet is fed. Sides
are random bytes so the real to_jpeg() produces a JPEG over the size
threshold that scan_batch() treats as "a usable image".
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from panelbeater.usb import daemon
from panelbeater.usb.scanner import (
    ASCQ_HOPPER_EMPTY,
    FILLER,
    WINDOW_BACK,
    WINDOW_FRONT,
    BatchAborted,
    UsbScanner,
)

REQUEST_SENSE = 0x03
READ = 0x28
SCAN_COMPLETE = (0xF1, 0x09)
SET_WINDOW = 0x24
FEED = (0x31, 0x01)

# A sheet: what the scanner reports for each side, in the order read_sheet()
# asks. "good" streams pixels; "tiny" answers with two bytes, the way a sheet
# that fed but never scanned does; an int is a sense ASCQ fault reported on the
# first poll of that side.
Good = "good"
Tiny = "tiny"


def sense_bytes(key: int = 0, asc: int = 0, ascq: int = 0, eom: bool = False) -> bytes:
    """An 18-byte REQUEST SENSE reply at the offsets sense() reads."""
    out = bytearray(0x12)
    out[2] = (key & 0x0F) | (0x40 if eom else 0)
    out[0x0C] = asc
    out[0x0D] = ascq
    return bytes(out)


class FakeDev:
    """Just enough of Ix1500 for UsbScanner: command() and hw_status()."""

    def __init__(
        self,
        sheets: list[dict[int, object]],
        width_px: int,
        still_scanning: bool = False,
    ):
        self.sheets = list(sheets)
        self.width_px = width_px
        self.still_scanning = still_scanning
        self.cdbs: list[bytes] = []
        self.current: dict[int, object] | None = None
        self.delivered: set[int] = set()
        self.pending_sense: bytes = sense_bytes()

    # -- what the scanner "does" ---------------------------------------------
    def hw_status(self, _page: int) -> tuple[bytes, int]:
        g = bytearray(8)
        if not self.sheets and self.current is None:
            g[3] |= 0x80  # hopper empty
        return bytes(g), 0

    def command(self, cdb: bytes, read_len: int = 0, payload: bytes | None = None):
        self.cdbs.append(bytes(cdb))
        op = cdb[0]
        if (op, cdb[1]) == FEED:
            self.current = self.sheets.pop(0)
            self.delivered = set()
        elif op == 0xF1 and cdb[1] == 0x10:  # poll a window before READ
            self.pending_sense = self._sense_for(cdb[2])
        elif op == REQUEST_SENSE:
            return self.pending_sense, 0
        elif op == READ:
            return self._read(cdb[5]), 0
        elif (op, cdb[1]) == SCAN_COMPLETE:
            self.current = None
        elif op == 0xC2:  # GET_HW_STATUS inside setup()
            return bytes(read_len), 0
        return b"", 0

    def _sense_for(self, window: int) -> bytes:
        if self.still_scanning:
            return sense_bytes(0x03, 0x80, 0x13)
        plan = self.current[window] if self.current else Good
        if isinstance(plan, int):
            return sense_bytes(0x03, 0x80, plan)
        if window in self.delivered:
            return sense_bytes(eom=True)  # side finished; next READ is empty
        return sense_bytes()

    def _read(self, window: int) -> bytes:
        if window in self.delivered:
            return b""
        self.delivered.add(window)
        plan = self.current[window]
        rows = 300
        stride = self.width_px * 3
        if plan == Tiny:
            return bytes([FILLER, FILLER])
        return random.Random(window).randbytes(stride * rows)

    # -- assertions ------------------------------------------------------------
    def count(self, op: int, sub: int | None = None) -> int:
        return sum(1 for c in self.cdbs if c[0] == op and (sub is None or c[1] == sub))


def make_scanner(sheets, tmp_path, **kw) -> tuple[UsbScanner, FakeDev, str]:
    # width_px is what setup() derives for 300 dpi; the fake needs it before
    # setup() runs so its raw sides have the right stride.
    dev = FakeDev(sheets, width_px=2612, **kw)
    scanner = UsbScanner(dev, resolution=300, mode="color", duplex=True)
    return scanner, dev, str(tmp_path / "page")


def duplex(front=Good, back=Good) -> dict[int, object]:
    return {WINDOW_FRONT: front, WINDOW_BACK: back}


def test_batch_ends_when_the_hopper_empties(tmp_path):
    scanner, dev, prefix = make_scanner([duplex(), duplex()], tmp_path)

    sides = scanner.scan_batch(prefix)

    assert sides == 4
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        f"page-{n:04d}.jpg" for n in (1, 2, 3, 4)
    ]
    assert dev.count(*SCAN_COMPLETE) == 2
    assert dev.count(0x31, 0x02) == 1  # finish()
    assert scanner.sheet_fault is None


def test_jam_after_a_good_sheet_aborts_and_publishes_nothing(tmp_path):
    scanner, dev, prefix = make_scanner([duplex(), duplex(front=0x01)], tmp_path)

    with pytest.raises(BatchAborted, match=r"sheet 2: paper jam on the front"):
        scanner.scan_batch(prefix)

    # The faulted sheet still got its "scan complete" and the batch its finish,
    # so the panel is not left on "Scanning...".
    assert dev.count(*SCAN_COMPLETE) == 2
    assert dev.count(0x31, 0x02) == 1
    # Sheet 1's two sides and sheet 2's back are on disk; discarding them is the
    # caller's job (the daemon deletes the work directory on any exception).
    assert len(list(tmp_path.iterdir())) == 3


@pytest.mark.parametrize(
    "ascq, name", [(0x02, "cover open"), (0x07, "double feed"), (0x42, "ascq 0x42")]
)
def test_every_non_empty_fault_aborts(tmp_path, ascq, name):
    scanner, _, prefix = make_scanner([duplex(back=ascq)], tmp_path)

    with pytest.raises(BatchAborted, match=rf"sheet 1: {name} on the back"):
        scanner.scan_batch(prefix)


def test_hopper_empty_fault_is_a_clean_end_not_an_abort(tmp_path):
    scanner, dev, prefix = make_scanner([], tmp_path)

    assert scanner.scan_batch(prefix) == 0
    assert dev.count(*SCAN_COMPLETE) == 0
    assert scanner.last_fault == ASCQ_HOPPER_EMPTY


def test_timeout_waiting_for_a_side_aborts(tmp_path):
    scanner, dev, prefix = make_scanner([duplex()], tmp_path, still_scanning=True)
    scanner.read_timeout_s = 0.05

    with pytest.raises(
        BatchAborted, match=r"sheet 1: timed out after 0s waiting for the front, back"
    ):
        scanner.scan_batch(prefix)

    assert dev.count(*SCAN_COMPLETE) == 1


def test_read_error_mid_sheet_aborts(tmp_path):
    scanner, dev, prefix = make_scanner([duplex()], tmp_path)
    real_read = dev._read

    def flaky_read(window):
        if window == WINDOW_BACK:
            raise OSError("pipe error")
        return real_read(window)

    dev._read = flaky_read

    with pytest.raises(
        BatchAborted, match=r"sheet 1: read failed on the back: pipe error"
    ):
        scanner.scan_batch(prefix)


def test_fed_sheet_with_no_image_aborts(tmp_path):
    scanner, dev, prefix = make_scanner([duplex(Tiny, Tiny)], tmp_path)

    with pytest.raises(
        BatchAborted, match=r"sheet 1: fed but produced no usable image"
    ):
        scanner.scan_batch(prefix)

    assert dev.count(*SCAN_COMPLETE) == 1


# -- the daemon must not file anything when the batch aborts -------------------


class _Cfg:
    def __init__(self):
        self.values = {
            "resolution": "300",
            "mode": "color",
            "simplex": "no",
            "max_sheets": "50",
        }

    def num(self, key, default=0.0):
        return float(self.values.get(key, default))

    def get(self, key, default=""):
        return self.values.get(key, default)

    def flag(self, key):
        return self.values.get(key, "") in ("1", "yes", "true", "on")


class _AbortingScanner:
    def __init__(self, dev, **_):
        pass

    def scan_batch(self, out_prefix, max_sheets=100):
        Path(f"{out_prefix}-0001.jpg").write_bytes(b"partial")
        raise BatchAborted("sheet 2: paper jam on the front")


class _GoodScanner:
    def __init__(self, dev, **_):
        pass

    def scan_batch(self, out_prefix, max_sheets=100):
        for n in (1, 2):
            Path(f"{out_prefix}-{n:04d}.jpg").write_bytes(b"jpeg")
        return 2


def _run_scan_once(monkeypatch, tmp_path, scanner_cls):
    filed: list[list[str]] = []
    logs: list[str] = []
    work_dirs: list[Path] = []

    def fake_mkdtemp(prefix=""):
        d = tmp_path / f"{prefix}work"
        d.mkdir()
        work_dirs.append(d)
        return str(d)

    monkeypatch.setattr(daemon, "UsbScanner", scanner_cls)
    monkeypatch.setattr(daemon.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        daemon.output,
        "finish",
        lambda pages, stamp, cfg, log=print: filed.append(list(pages)),
    )

    class _Thread:  # run post-processing inline so the test can observe it
        def __init__(self, target):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(daemon.threading, "Thread", _Thread)
    dev = FakeDev([], width_px=2612)
    sides = daemon.scan_once(_Cfg(), dev, log=logs.append)
    return sides, filed, logs, work_dirs, dev


def test_daemon_files_nothing_when_the_batch_aborts(monkeypatch, tmp_path):
    sides, filed, logs, work_dirs, dev = _run_scan_once(
        monkeypatch, tmp_path, _AbortingScanner
    )

    assert sides == 0
    assert filed == []
    assert not work_dirs[0].exists(), "captured sides must be discarded"
    assert any("scan failed: sheet 2: paper jam on the front" in line for line in logs)
    assert dev.count(0x31, 0x02) == 1  # finish_batch() returned the panel to ready


def test_daemon_files_the_pages_on_success(monkeypatch, tmp_path):
    sides, filed, logs, work_dirs, _ = _run_scan_once(
        monkeypatch, tmp_path, _GoodScanner
    )

    assert sides == 2
    assert len(filed) == 1 and [Path(p).name for p in filed[0]] == [
        "page-0001.jpg",
        "page-0002.jpg",
    ]
    assert not work_dirs[0].exists()
    assert any("2 side(s) scanned" in line for line in logs)
