# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""Letting the panel go dark when nothing is happening.

A scanner panel that glows all night is a nightlight nobody asked for. Set
`dim_after` and the daemon lets the panel go dark once nothing has happened
for that many minutes; touching it brings the scanner straight back.

What actually keeps the panel lit is **registration**. Stop registering and it
goes dark on its own; keep registering and it cannot. Everything else that was
once believed about this turned out to be an artefact of how it was measured:

  * "A sleep timer must be armed (MODE SELECT page 0x34) or it never dims."
    **Wrong.** Measured with the timer confirmed at 0 by MODE SENSE, and
    nothing registering, the panel dimmed after 777s and stayed dark. The
    original experiments that concluded otherwise ran the registering daemon
    throughout, so they could not separate "no timer" from "something keeps
    relighting it".
  * "It never dims unless the scanner is polled." Same confound, and the
    negative runs were 480s and 540s -- shorter than the 777s delay actually
    observed. Unverified either way; treat it as unknown.
  * "Registration relights the panel." This one holds up: dark at 84.5s, a
    registration 30s later, relit 2s after that, reproduced repeatedly.

The timer is still armed when going dark, because that configuration is the one
verified end to end twice, and disarmed on waking so it cannot cause flicker
while registering. It is plausibly unnecessary. It is cheap, and removing it
would need another measurement rather than another assumption.

The delay from dropping the registration to a dark screen is the scanner's own
and is neither quick nor consistent: 454s, 634s and 777s across runs. Any test
shorter than about fifteen minutes can report a false negative -- several of
ours did, and that is how the two wrong conclusions above survived.

    LIT     registering, Scan button usable
    (idle)  nothing for `dim_after` minutes
    DARK    not registering, timer armed, slow poll     panel dark
    (touch) sleep bit clears -> back to LIT

`dim_after` controls when we stop registering, not when the screen goes off.

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
