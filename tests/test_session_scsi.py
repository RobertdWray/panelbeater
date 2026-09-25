# Copyright (C) 2026 Rob Wray
# SPDX-License-Identifier: GPL-2.0-or-later
"""Session: the SCSI tunnel below scan_batch(), and which user it acts as.

The fake here stands in for the TCP socket, so the real framing, status and
collect logic in Session.scsi() runs. Each script entry is what one recv()
returns, or an exception it raises.
"""

from __future__ import annotations

import socket

import pytest

from panelbeater.protocol import frame
from panelbeater.session import Session

FRAME_LEAD = bytes(0x18)  # scsi() strips 0x18 bytes from each frame body

CLOUD = {"prof_type": 1, "user_id": "020A2ED50E004C05B3A80A92006A2239"}
HOME = {"prof_type": 0, "user_id": "8E028EF68A9048479970AA0C2DE5050B"}


class FakeSocket:
    def __init__(self, script: list[bytes | BaseException]):
        self.script = list(script)
        self.sent: list[bytes] = []
        self.timeout: float | None = None

    def sendall(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    def settimeout(self, t: float) -> None:
        self.timeout = t

    def recv(self, _n: int) -> bytes:
        item = self.script.pop(0) if self.script else b""
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        pass


def session_with(script) -> Session:
    # 192.0.2.1 is TEST-NET-1: never contacted, only used to pick a route.
    s = Session("192.0.2.1", "51c897c7ba596804")
    s.sock = FakeSocket(script)
    return s


def reply(status: int, data: bytes = b"") -> bytes:
    return frame(status & 0xFFFFFFFF, FRAME_LEAD + data)


READ_FRONT = bytes([0x28, 0, 0, 0x02, 0, 0x00, 0x30, 0, 0, 0, 0, 0])
JPEG = b"\xff\xd8\xff" + b"x" * 200 + b"\xff\xd9"


def test_scsi_returns_signed_status_and_the_payload_after_the_lead():
    s = session_with([reply(-1, b"abc")])
    assert s.scsi(bytes([0x03, 0, 0, 0, 0x12, 0]), 0x12) == (-1, b"abc")


def test_scsi_collects_an_image_across_frames_until_the_eoi():
    head, tail = JPEG[:100], JPEG[100:]
    s = session_with([reply(0, head), reply(0, tail)])
    assert s.scsi(READ_FRONT, 0x300000, collect=True, quiet=1) == (0, JPEG)


def test_scsi_raises_when_the_scanner_stops_answering():
    """A stalled read is a fault, not an image: today it is swallowed and the
    partial data comes back with status 0."""
    s = session_with([reply(0, JPEG[:100]), socket.timeout("timed out")])
    with pytest.raises(OSError, match=r"stopped answering"):
        s.scsi(READ_FRONT, 0x300000, collect=True, quiet=1)


def test_scsi_returns_a_partial_image_without_eoi_when_the_socket_closes():
    """The caller decides what an image without an EOI means; scsi() must not
    invent one. A closed socket ends the read with whatever arrived."""
    s = session_with([reply(0, JPEG[:100]), b""])
    st, data = s.scsi(READ_FRONT, 0x300000, collect=True, quiet=1)
    assert st == 0
    assert data == JPEG[:100]
    assert not data.endswith(b"\xff\xd9")


def test_user_id_is_the_host_profiles_user_not_the_cloud_ones(monkeypatch):
    s = session_with([])
    monkeypatch.setattr(Session, "profiles", lambda self: [CLOUD, HOME])
    assert s.user_id() == HOME["user_id"]
