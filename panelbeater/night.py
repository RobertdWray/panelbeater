# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""Letting the panel go dark when nothing is happening.

Two independent things decide whether the panel is lit:

  * **The scanner's own sleep timer** (MODE SELECT page 0x34, in minutes) is
    what turns the backlight off.
  * **Registration resets it.** A host that keeps registering keeps the panel
    lit for ever, whatever the timer says -- dark at 84.5s, a registration 30s
    later, relit 2s after that.

So `dim_after` stops the daemon registering after that many idle minutes, and
the panel then goes dark once the scanner's timer expires. Touching it clears
the sleep bit, the daemon notices within a poll and registers again, and the
Scan button works by the time the screen has settled.

    LIT     registering, Scan button usable
    (idle)  nothing for `dim_after` minutes
    DARK    not registering; scanner's timer expires   panel dark
    (touch) sleep bit clears -> back to LIT

Total delay is therefore `dim_after` plus the scanner's timer, which is around
15 minutes out of the box.

**The timer can only be set over USB.** On the network the write is accepted
with status 0 and does nothing, and MODE SENSE is not carried there either, so
nothing reveals it. See `usb.set_sleep_timer`.

Two mistakes were made here and both are worth remembering, because each
produced a confident wrong answer that lasted:

  * *"Polling is required for the dim."* False -- 776s with nothing at all
    talking to the scanner, against 777s polled. This one is settled.
  * *"The sleep timer does nothing."* Also false, and it was my correction of
    the first mistake. It came from reading the timer at the wrong offset: the
    MODE SENSE reply has an 8-byte block descriptor, so the page starts at byte
    12, and byte 6 is descriptor padding that is always zero. Every "timer
    confirmed at 0" was reading that padding. With the value read correctly, a
    2-minute timer dims the panel in 2 minutes.

The lesson both times was the same: an experiment that cannot distinguish two
explanations will happily produce one of them.
"""

from __future__ import annotations

SLEEP_BIT = 0x80  # GET_HW_STATUS byte 4


def is_asleep(ghs: bytes) -> bool:
    return len(ghs) > 4 and bool(ghs[4] & SLEEP_BIT)
