# Copyright (C) 2026 Rob Wray
# SPDX-License-Identifier: GPL-2.0-or-later
"""The network daemon must tell the panel which user it acts for, not just register.

A registration keeps the panel alive; a session (subject 0x02) decides whose
profiles it shows. Without one, a scanner set up with ScanSnap Home shows the
first user's profiles -- "Send to ScanSnap Cloud" -- and an orange "!" until the
first scan opens a session. Measured on 2026-09-25: enrol + register left the
"!" up; one open_session() cleared it. After a power cycle the panel came back
on the cloud profile for the same reason.
"""

from __future__ import annotations

import pytest

from panelbeater import daemon

HOST_USER = "8E028EF68A9048479970AA0C2DE5050B"


class RecordingSession:
    instances: list[RecordingSession] = []
    status = 0
    raise_on_open: Exception | None = None
    profiles_user = HOST_USER

    def __init__(self, host, host_id):
        self.host, self.host_id = host, host_id
        self.opened_as: str | None = None
        RecordingSession.instances.append(self)

    def user_id(self):
        return self.profiles_user

    def open_session(self, user_id="", port=0):
        if self.raise_on_open:
            raise self.raise_on_open
        self.opened_as = user_id
        return self.status


@pytest.fixture
def session(monkeypatch):
    RecordingSession.instances.clear()
    RecordingSession.status = 0
    RecordingSession.raise_on_open = None
    RecordingSession.profiles_user = HOST_USER
    monkeypatch.setattr(daemon, "Session", RecordingSession)
    return RecordingSession


def test_opens_the_session_as_the_configured_user(session):
    logged: list[str] = []
    assert daemon.open_panel_session(
        "192.0.2.1", "51c897c7ba596804", "CAFE", log=logged.append
    )
    assert session.instances[-1].opened_as == "CAFE"
    assert any("panel session opened as user CAFE" in m for m in logged)


def test_falls_back_to_the_host_profiles_user_when_none_is_configured(session):
    assert daemon.open_panel_session(
        "192.0.2.1", "51c897c7ba596804", "", log=lambda m: None
    )
    assert session.instances[-1].opened_as == HOST_USER


def test_reports_false_when_the_scanner_has_no_user_to_act_as(session):
    session.profiles_user = ""
    logged: list[str] = []
    assert not daemon.open_panel_session(
        "192.0.2.1", "51c897c7ba596804", "", log=logged.append
    )
    assert any("no user id" in m for m in logged)


def test_reports_false_and_logs_when_the_scanner_is_unreachable(session):
    session.raise_on_open = OSError("timed out")
    logged: list[str] = []
    assert not daemon.open_panel_session(
        "192.0.2.1", "51c897c7ba596804", "", log=logged.append
    )
    assert any("could not open the panel session: timed out" in m for m in logged)


def test_reports_false_and_logs_when_the_scanner_refuses(session):
    session.status = -2
    logged: list[str] = []
    assert not daemon.open_panel_session(
        "192.0.2.1", "51c897c7ba596804", "", log=logged.append
    )
    assert any("refused (status -2)" in m for m in logged)
