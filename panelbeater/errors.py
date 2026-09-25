# Copyright (C) 2026 Rob Wray
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exceptions shared by both transports.

Kept apart from the usb package on purpose: importing anything from
panelbeater.usb loads pyusb, and the network path must stay stdlib-only.
"""


class BatchAborted(RuntimeError):
    """The scanner stopped before the hopper was empty, or a side came back
    incomplete.

    A jam on sheet 4 of 10 used to return the six sides already captured, and
    they were filed as a finished document indistinguishable from a clean
    scan. Raising instead lets the caller discard the batch; the paper is still
    in the hopper, and a rescan is the only honest recovery.
    """
