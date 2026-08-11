# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""Letting the panel go dark when nothing is happening.

A scanner panel that glows all night is a nightlight nobody asked for. Set
`dim_after` and the daemon lets the panel go dark once nothing has happened
for that many minutes; touching it brings the scanner straight back.

Making it dark is harder than it sounds, because three measured facts pull
against each other (all reproduced with a webcam pointed at the panel):

  * A sleep timer must be armed -- MODE SELECT page 0x34, in minutes -- or the
    panel never dims at all. With the timer at 0 it stays lit indefinitely.
  * The scanner must also be POLLED for the dim to happen. Counter-intuitive,
    but reproduced three times: polled, it dims (85s at a 200ms poll, 402s at
    5s); with the timer armed and no polling at all it stayed lit through 480s
    and 540s windows.
  * Registration RELIGHTS the panel, and registration is what keeps the Scan
    button alive. Measured: dim at 84.5s, a registration 30s later, relit 2s
    after that. So while the daemon registers normally the panel cannot stay
    dark, and registration expires after ~46s so it cannot simply be slowed
    down.

So dimming means dropping the registration and keeping a slow poll. The panel
falls back to its "not responding" screen, dims a few minutes later, and stays
dim.

The saving grace is that touching the panel wakes it immediately, and the wake
shows up as the GET_HW_STATUS sleep bit clearing. That is visible within one
poll, so the daemon starts registering again the moment the panel is touched:
by the time the screen has settled, the scanner is registered and the button
works.

    LIT     registering, Scan button usable
    (idle)  nothing for `dim_after` minutes
    DARK    not registering, timer armed, slow poll     panel dark
    (touch) sleep bit clears -> back to LIT

The delay from arming to dark is the scanner's own, and it is not quick or
consistent: measured between 93s and 771s. `dim_after` controls when we stop
registering, not when the screen actually goes off.

The timer is disarmed on waking: while registering normally the panel is
relit every interval anyway, so an armed timer only produces flicker.
"""

from __future__ import annotations

SLEEP_BIT = 0x80  # GET_HW_STATUS byte 4


def set_sleep_timer(session, minutes: int) -> int:
    """MODE SELECT page 0x34: minutes of idleness before the panel sleeps.

    0 disables it. The session must already be connected.
    """
    page = bytes([0x34, 0x06, minutes & 0xFF, 0, 0, 0, 0, 0])
    out = bytes(4) + page
    st, _ = session.scsi(bytes([0x15, 0x10, 0, 0, len(out), 0]), 0, out)
    return st


def arm(host: str, host_id: str, minutes: int, log=print) -> bool:
    """Arm or disarm the scanner's sleep timer, on its own connection."""
    from .session import Session

    s = Session(host, host_id)
    try:
        s.connect()
    except OSError as exc:
        log(f"  could not reach the scanner to set the sleep timer: {exc}")
        return False
    try:
        st = set_sleep_timer(s, minutes)
    except OSError as exc:
        log(f"  setting the sleep timer failed: {exc}")
        return False
    finally:
        s.close()
    if st != 0:
        log(f"  sleep timer refused (status {st})")
        return False
    return True


def is_asleep(ghs: bytes) -> bool:
    return len(ghs) > 4 and bool(ghs[4] & SLEEP_BIT)
