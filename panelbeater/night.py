# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""Letting the panel go dark when nothing is happening.

A scanner panel that glows all night is a nightlight nobody asked for. Set
`dim_after` and the daemon stops registering once nothing has happened for that
many minutes; the panel then goes dark on its own. Touching it clears the sleep
bit, the daemon sees that within a poll and registers again, and the Scan button
works by the time the screen has settled.

    LIT     registering, Scan button usable
    (idle)  nothing for `dim_after` minutes
    DARK    not registering                              panel dark
    (touch) sleep bit clears -> back to LIT

**Registration is the whole mechanism.** Stop registering and the panel dims
after about thirteen minutes; keep registering and it cannot dim, because each
registration relights it (dark at 84.5s, a registration 30s later, relit 2s
after that). `dim_after` therefore controls when we stop registering, not when
the screen goes off.

This file used to arm a sleep timer and keep a slow poll going. Both were
removed after being measured, because each rested on an experiment that could
not have shown what it claimed. Recorded so nobody adds them back:

  * *"A sleep timer must be armed (MODE SELECT page 0x34) or it never dims."*
    False. With the timer at 0, confirmed by MODE SENSE over USB, the panel
    dimmed at 777s (polled) and 776s (unpolled). Setting the timer to THIRTY
    MINUTES over the network changed nothing: it dimmed at 883s, not 1800s.
  * *"The scanner must be polled or it never dims."* False. 776s with nothing
    whatsoever talking to the scanner.

Both original claims came from runs with the registering daemon left up, which
relights the panel, so they could not distinguish "no timer" or "no polling"
from "something keeps waking it". Their negative windows were 480s and 540s --
shorter than the delay actually observed.

The delay is closer to fixed than the old notes suggest: 776s, 777s and 883s.
The 93-771s spread once recorded was registration resetting the clock at
different points, not the scanner being erratic.

One caveat for anyone revisiting page 0x34: what was shown is that setting it
*over the network* does not affect the dim. MODE SENSE is unavailable on that
transport -- status 0 and an empty payload -- so whether the write is silently
dropped or genuinely does nothing was not separated. That needs the USB cable.

Over USB there is no registration at all, so an idle scanner dims by itself and
nothing the host can send wakes it again -- only a touch.
"""

from __future__ import annotations

SLEEP_BIT = 0x80  # GET_HW_STATUS byte 4


def is_asleep(ghs: bytes) -> bool:
    return len(ghs) > 4 and bool(ghs[4] & SLEEP_BIT)
