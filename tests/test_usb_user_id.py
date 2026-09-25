# Copyright (C) 2026 Rob Wray
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which of the scanner's profiles the USB daemon should act as."""

from panelbeater.usb.daemon import user_id_from_profiles

CLOUD = {
    "prof_name": "Send to ScanSnap Cloud",
    "prof_type": 1,
    "host_address": "",
    "user_id": "020A2ED50E004C05B3A80A92006A2239",
}
HOME = {
    "prof_name": "Wray Scan",
    "prof_type": 0,
    "host_address": "192.168.1.106",
    "user_id": "8E028EF68A9048479970AA0C2DE5050B",
}


def test_prefers_a_host_profile_over_the_cloud_profile_listed_first():
    assert user_id_from_profiles([CLOUD, HOME]) == HOME["user_id"]


def test_falls_back_to_the_cloud_profile_when_it_is_the_only_one():
    assert user_id_from_profiles([CLOUD]) == CLOUD["user_id"]


def test_skips_profiles_without_a_user_id():
    assert (
        user_id_from_profiles([{"prof_name": "x", "prof_type": 0}, HOME])
        == HOME["user_id"]
    )


def test_empty_when_nothing_has_a_user_id():
    assert user_id_from_profiles([]) == ""
    assert user_id_from_profiles([{"prof_type": 0}]) == ""
