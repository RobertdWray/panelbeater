# Copyright (C) 2026 Rob Wray
# SPDX-License-Identifier: GPL-2.0-or-later
"""Choosing among the scanner's profiles, on either transport.

The scanner's own `profiles` list decides two things a host has to get right:
which `user_id` the panel acts as, and which profile a scan runs under. Both
transports read the same list, so the rule lives here, stdlib-only, where the
network path can import it without pulling in pyusb.

A scanner that was set up with ScanSnap Home lists "Send to ScanSnap Cloud"
first, under its own user_id. Acting as that user makes every scan a cloud
job: the scanner tries to reach ScanSnap Cloud after the batch, cannot, and the
panel shows an orange "!" -- "The device is not responding" -- that nothing the
host sends will clear. The profiles ScanSnap Home creates for a computer carry
prof_type 0 and a host_address; the cloud profile carries prof_type 1 and none.
"""

from __future__ import annotations

HOST_PROFILE = 0  # prof_type of a profile that scans to a computer


def default_profile(profiles: list[dict]) -> dict | None:
    """The profile to scan with when none is configured: a host profile,
    falling back to whatever is listed first."""
    for p in profiles:
        if p.get("prof_type") == HOST_PROFILE:
            return p
    return profiles[0] if profiles else None


def user_id_from_profiles(profiles: list[dict]) -> str:
    """The 32-hex id the panel acts as: a host profile's user, not the cloud
    profile's, falling back to any profile that has one."""
    for p in profiles:
        if p.get("user_id") and p.get("prof_type") == HOST_PROFILE:
            return str(p["user_id"])
    for p in profiles:
        if p.get("user_id"):
            return str(p["user_id"])
    return ""
