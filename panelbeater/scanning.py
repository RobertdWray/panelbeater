# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""Running a scan batch over the network transport.

The scanner returns JPEG directly, so there is no image decoding here -- the
bytes are written out as they arrive.
"""

from __future__ import annotations

import time
from typing import Callable

from .errors import BatchAborted
from .profiles import default_profile
from .protocol import hw_status
from .session import Session

# Captured verbatim: 300 dpi, A4. 0x012c twice is 300 dpi in each axis, and
# 0x28d0 x 0x45a4 is the page in 1/1200 inch. The block is not fully decoded,
# so resolution is not adjustable over the network yet -- what looks like
# padding holds geometry, and changing it blindly makes d4 fail.
D4_PARAMS = bytes.fromhex(
    "00030101d000c1808080908080008001000000000000000000000000000000300010012c012c0581"
    "0000000028d0000045a4000000000000000000000000000000000000000000000000000000000000"
)
assert len(D4_PARAMS) == 80, "d4 parameter block must be exactly 80 bytes"


JPEG_SOI = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"

# A side smaller than this is not a page. The scanner's own JPEG of a blank
# sheet at 300 dpi is well over 100 kB; a stray frame is a few bytes.
MIN_IMAGE_BYTES = 10_000

# Seconds between the setup commands and the first e0. A module constant so a
# test can zero it.
SETUP_SETTLE_S = 2.0


def as_jpeg(raw: bytes) -> bytes | None:
    """The complete JPEG in an image read, or None.

    Image data arrives with a couple of leading bytes before the SOI. A read is
    complete at its EOI: Session.scsi() stops collecting there, so data without
    one means the side was cut off (a closed socket, a scanner that gave up).
    Returning the fragment used to let a truncated side be filed as a page.
    """
    i = raw.find(JPEG_SOI)
    if i < 0:
        return None
    j = raw.rfind(JPEG_EOI)
    if j < i:
        return None
    return raw[i : j + 2]


def decode_sense(b: bytes) -> tuple[int, int, int, bool, bool] | None:
    """REQUEST SENSE, per sane-backends fujitsu-scsi.h.

    Byte 2 holds the sense key plus the EOM and ILI flags; ASC and ASCQ are at
    0x0c and 0x0d.
    """
    if len(b) < 0x0E:
        return None
    return (b[2] & 0x0F, b[0x0C], b[0x0D], bool(b[2] & 0x40), bool(b[2] & 0x20))


# (key, asc, ascq) -> what the scanner is reporting. Hopper empty is the one
# condition that can end a batch cleanly; everything else aborts it.
SENSE_HOPPER_EMPTY = (0x03, 0x80, 0x03)
SENSE_FAULTS = {
    (0x03, 0x80, 0x01): "paper jam",
    (0x03, 0x80, 0x02): "cover open",
    (0x03, 0x80, 0x04): "unusual paper",
    (0x03, 0x80, 0x07): "double feed",
    (0x03, 0x80, 0x08): "no paper picked",
}


def hopper_has_paper(host: str, mac: bytes) -> bool:
    """Is there paper in the hopper? Ask ONLY before the batch, at rest.

    Asked on a SEPARATE connection. The scan connection has to carry the
    d5/d8/e9/d4 sequence and nothing in front of it, or d4 fails with -1.

    Measured on an iX1500 (2026-09-25): the hopper-empty bit is right at
    rest, and for about two seconds after a sheet has been read, and then it
    reads "empty" until the job closes however much paper is loaded. Asking
    it between sheets therefore ended batches after whichever sheet happened
    to finish outside that window. Later sheets are fed blind and the sense
    after the feed decides; see feed_sheet().

    Raises OSError when the answer is unknown. A failed or short reply used to
    read as "empty", which ended the batch cleanly and filed what had been
    captured so far.
    """
    g = hw_status(host, mac)
    if len(g) < 5:
        raise OSError(f"GET_HW_STATUS reply too short ({len(g)} bytes)")
    return not (g[3] & 0x80)


SETUP = [
    ("d5 (01)", [0xD5, 0, 0, 0x01, 0x08, 0x08], 8, bytes(8)),
    ("d8 begin", [0xD8, 0, 0, 0, 0, 0], 0, b""),
    ("e9 config", [0xE9, 0, 0, 0, 0, 0, 0, 0x20, 0, 0], 0, b""),
    ("d4 params", [0xD4, 0, 0, 0, 0x50, 0], 0, D4_PARAMS),
    ("d5 (00)", [0xD5, 0, 0, 0x00, 0x08, 0x08], 8, bytes(8)),
]
# The e0 here starts a page with no paper behind it, which is how the scanner
# is told the batch is over; d6 then closes it.
TERMINATORS = [
    ("e0 end", [0xE0, 0, 0, 0, 0, 0]),
    ("d6 finish", [0xD6, 0, 0, 0, 0, 0]),
]
# Both sides of every sheet. The D4 block above is a duplex capture, so a sheet
# that yields only a front is a fault, not a one-sided page; if the block ever
# becomes adjustable this list has to follow it.
SIDES = ((0x00, "front", 0), (0x80, "back", 1))


def feed_sheet(s: Session, sheet: int, log: Callable[[str], None]) -> bool:
    """Feed the next sheet with e0. Returns False when the hopper was empty.

    e0 answers status 0 whether or not there was paper (in about 0.05 s with a
    sheet, about 0.9 s without), so the status says nothing. The REQUEST SENSE
    straight after it does: 03/80/03 "hopper empty" when nothing fed, all
    zeros when a sheet did, and it is one-shot -- a second sense a moment
    later reads zeros again. PROTOCOL.md said this sense never arrives on the
    network transport; it does, but only here, and only if asked before any
    READ. A READ after an empty feed is the speculative read that never
    answers and leaves the panel on "Scanning...". Reading sense between e0
    and the first READ does not disturb the sheet (measured).
    """
    st, _ = s.scsi(bytes([0xE0, 0, 0, 0, 0, 0]))
    log(f"  sheet {sheet} e0 START {st}")
    if st != 0:
        raise BatchAborted(f"sheet {sheet}: e0 refused (status {st})")
    _, sense = s.scsi(bytes([0x03, 0, 0, 0, 0x12, 0]), 0x12)
    d = decode_sense(sense)
    if not d:
        raise BatchAborted(
            f"sheet {sheet}: no sense after the feed ({len(sense)} bytes)"
        )
    key, asc, ascq, _eom, _ili = d
    if (key, asc, ascq) == SENSE_HOPPER_EMPTY:
        return False
    fault = SENSE_FAULTS.get((key, asc, ascq))
    if fault:
        raise BatchAborted(f"sheet {sheet}: {fault} on feed")
    if key != 0:
        raise BatchAborted(
            f"sheet {sheet}: sense key {key:#x} asc {asc:#04x} ascq {ascq:#04x} on feed"
        )
    return True


def read_sheet(
    s: Session,
    sheet: int,
    out_prefix: str,
    first_page: int,
    log: Callable[[str], None],
) -> int:
    """Read both sides of one fed sheet. Returns the number of sides written.

    0 means the scanner reported the hopper empty before anything was read:
    the sheet was never fed, and the batch can end cleanly. Any other shortfall
    raises BatchAborted -- a sense fault, a failed READ, a missing side, or an
    image without its EOI -- because the caller cannot tell a partial sheet
    from a whole one after the fact.
    """
    written = 0
    for side, tag, last in SIDES:
        st, raw = s.scsi(
            bytes([0x28, 0, 0, 0x02, 0, side, 0x30, 0, 0, 0, last, 0]),
            0x300000,
            collect=True,
            quiet=20,
        )
        _, sense = s.scsi(bytes([0x03, 0, 0, 0, 0x12, 0]), 0x12)
        d = decode_sense(sense)
        if d:
            key, asc, ascq, eom, ili = d
            # The hopper-empty sense that sane-backends documents never
            # actually arrives on this transport -- the key stays 0 through the
            # last sheet. The hopper check between sheets is what ends the
            # batch; this is here to catch real faults: jam, cover open, double
            # feed, and anything not on the list.
            log(
                f"  sheet {sheet} {tag:<6} sense key={key:#x} "
                f"asc={asc:#04x} ascq={ascq:#04x} "
                f"eom={int(eom)} ili={int(ili)}"
            )
            if (key, asc, ascq) == SENSE_HOPPER_EMPTY:
                if written:
                    raise BatchAborted(
                        f"sheet {sheet}: hopper empty reported after the front was read"
                    )
                return 0
            fault = SENSE_FAULTS.get((key, asc, ascq))
            if fault:
                raise BatchAborted(f"sheet {sheet}: {fault} on the {tag}")
            if key != 0:
                raise BatchAborted(
                    f"sheet {sheet}: sense key {key:#x} asc {asc:#04x} "
                    f"ascq {ascq:#04x} on the {tag}"
                )
        if st != 0:
            raise BatchAborted(f"sheet {sheet}: READ failed on the {tag} (status {st})")
        if JPEG_SOI not in raw:
            raise BatchAborted(f"sheet {sheet}: no image for the {tag}")
        jpg = as_jpeg(raw)
        if jpg is None:
            raise BatchAborted(
                f"sheet {sheet}: incomplete image for the {tag} (no EOI)"
            )
        if len(jpg) < MIN_IMAGE_BYTES:
            raise BatchAborted(
                f"sheet {sheet}: image for the {tag} is too small ({len(jpg)} bytes)"
            )
        # Sides are numbered sequentially rather than named, so a plain
        # lexicographic glob puts them in page order. "-back" sorts before
        # "-front", which silently reversed every sheet.
        path = f"{out_prefix}-{first_page + written + 1:04d}.jpg"
        with open(path, "wb") as fh:
            fh.write(jpg)
        written += 1
        log(f"  sheet {sheet} {tag:<6} {len(jpg):>9,} bytes -> {path}")
        s.scsi(bytes([0x28, 0, 0x80, 0, 0, side, 0, 0, 0x20, 0, 0, 0]), 0x20)
    return written


def scan_batch(
    s: Session,
    out_prefix: str,
    max_sheets: int = 100,
    log: Callable[[str], None] = print,
) -> int:
    """Scan every sheet in the hopper. Returns the number of sides written.

    The caller must already have registered, opened a session, selected a
    profile and called connect().

    Only a complete batch returns: every fed sheet read on both sides, each
    image ending in its EOI, and the batch ended by a feed that found the
    hopper empty or by max_sheets. Anything else raises BatchAborted, so the
    caller files nothing and the paper is still in the hopper for a rescan.
    The terminators go out either way.

    The hopper sensor is not consulted here; see hopper_has_paper().
    """
    pages = 0
    try:
        for label, cdb, rl, out in SETUP:
            st, _ = s.scsi(bytes(cdb), rl, out)
            log(f"  {label:<10} {st}")
            if st != 0:
                raise BatchAborted(f"setup: {label} refused (status {st})")

        time.sleep(SETUP_SETTLE_S)

        stopped = f"reached the {max_sheets}-sheet limit"
        for sheet in range(1, max_sheets + 1):
            # e0 starts THIS sheet, not the batch. The USB capture reissues the
            # equivalent (SET_WINDOW + OBJ_POS + SCAN) for every page. Sending
            # one e0 for the whole batch makes sheet 2 read back 2 bytes with
            # paper still in the hopper. Whether it fed anything is decided
            # from the sense right after it, never from a READ.
            if not feed_sheet(s, sheet, log):
                stopped = "hopper empty"
                break
            written = read_sheet(s, sheet, out_prefix, pages, log)
            if not written:
                stopped = "hopper empty"
                break
            pages += written
        log(f"  batch ended: {stopped}")
    finally:
        # Terminate even if the batch failed. Without these the panel sits on
        # "Scanning..." and eventually reports the connection was lost -- and a
        # crashed handler is exactly when that happens.
        for label, cdb in TERMINATORS:
            try:
                st, _ = s.scsi(bytes(cdb))
                log(f"  {label:<10} {st}")
            except OSError as exc:
                log(f"  {label:<10} could not send: {exc}")
    return pages


def scan_to_dir(
    host: str,
    host_id: str,
    out_prefix: str,
    prof_id: str = "",
    max_sheets: int = 100,
    skip_register: bool = False,
    user_id: str = "",
    log: Callable[[str], None] = print,
) -> int:
    """Full scan: register, select a profile, check for paper, scan.

    Returns the number of sides written, 0 when there was nothing to scan.
    Raises BatchAborted when a batch started and did not finish cleanly.
    `user_id` overrides the user the session acts as; blank reads it from the
    scanner's profiles. Blank `prof_id` scans with a host profile, not the
    cloud one that a ScanSnap Home setup lists first.
    """
    s = Session(host, host_id)
    if skip_register:
        log("using the existing registration")
    else:
        ok, why = s.register(on_wait=lambda: log("  scanner held by another host..."))
        if not ok:
            log(f"  registration failed: {why}")
            return 0
        log("registered")

    log(f"  session    {s.open_session(user_id)}")

    prof = prof_id
    if not prof:
        chosen = default_profile(s.profiles())
        if not chosen:
            log("  could not read the profile list")
            return 0
        prof = chosen["prof_id"]
        log(f"  profile    {chosen.get('prof_name')!r} ({prof})")
    log(f"  select     {s.select_profile(prof)}")

    if not hopper_has_paper(host, s.mac):
        log("no paper in the ADF")
        return 0

    s.connect()
    try:
        return scan_batch(s, out_prefix, max_sheets, log=log)
    finally:
        s.close()
