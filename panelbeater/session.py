# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""A session with the scanner: registration, the host list, profiles, SCSI."""

from __future__ import annotations

import json
import socket
import struct
import time

from .profiles import user_id_from_profiles
from .protocol import (
    INTENT_CLAIM,
    OP_PROF_INFO,
    OP_REGISTER,
    OP_SCSI,
    OP_UNREGISTER,
    PORT_CONTROL,
    PORT_REQUEST,
    frame,
    local_ip_and_mac,
    parse_frame,
    registration_payload,
    signed,
)

SUBJECT_CONN_USER = 0x01  # the host list
SUBJECT_SESSION = 0x02  # which user the panel is acting for
SUBJECT_PROFILE = 0x06  # pick the profile to scan with
DIR_READ = 0x00
DIR_WRITE = 0x10


class Session:
    """One host's view of the scanner.

    Cheap to create: nothing connects until register() or connect() is called,
    and the 53219 requests each use their own short-lived connection, which is
    what ScanSnap Home does too -- every request in the capture is its own TCP
    stream.
    """

    def __init__(self, host: str, host_id: str, timeout: float = 120.0):
        self.host = host
        self.host_id = bytes.fromhex(host_id)
        self.timeout = timeout
        self.ip, self.mac, _ = local_ip_and_mac(host)
        self.sock: socket.socket | None = None

    # -- 53219: one request per connection ---------------------------------

    def _req(self, op: int, body: bytes, t: float = 15) -> tuple[int, bytes]:
        with socket.create_connection((self.host, PORT_REQUEST), timeout=t) as s:
            s.settimeout(t)
            s.recv(4096)  # the scanner greets first
            s.sendall(frame(op, body))
            buf = b""
            while len(buf) < 16 or len(buf) < struct.unpack(">I", buf[0:4])[0]:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
        p = parse_frame(buf)
        return (signed(p[0]), p[2]) if p else (-1, b"")

    def _op41(
        self, sub0: int, sub1: int = 0, doc: dict | None = None, tail: int = 0
    ) -> tuple[int, bytes]:
        sub = bytearray(16)
        sub[0], sub[1] = sub0, sub1
        if doc is not None:
            payload = json.dumps(doc, separators=(",", ":")).encode()
            # Offset 12 counts the WHOLE block: the 16-byte sub-header plus the
            # JSON, not the JSON alone. The read path below passes tail=0x10 for
            # a header with no payload, which is the same rule.
            #
            # Declaring 16 short truncates the JSON at the scanner, so it fails
            # to parse and the write is refused -2. Small documents survive it,
            # which is why it stayed hidden until a 6396-byte host list made the
            # truncation fatal.
            #
            # This is the OPPOSITE of the USB carrier, where the same field
            # counts the JSON only.
            struct.pack_into("<I", sub, 12, len(payload) + 16)
            body_tail = bytes(sub) + payload
        else:
            struct.pack_into("<I", sub, 12, tail)
            body_tail = bytes(sub)
        head = bytearray(20)
        head[0:6] = self.mac
        struct.pack_into(">I", head, 16, len(body_tail))
        return self._req(OP_PROF_INFO, bytes(head) + body_tail)

    # -- registration ------------------------------------------------------

    def register(
        self, wait: int = 90, intent: int = 0, on_wait=None
    ) -> tuple[bool, int]:
        """Claim the scanner. Returns (ok, intent_used).

        Falls back to INTENT_CLAIM on -7. Intent 0 is only accepted for the one
        host_id the scanner already treats as its own, so every other host --
        including one that is properly enrolled -- has to claim. Trying 0 first
        keeps steady-state re-registration identical to ScanSnap Home's, which
        matters: a claim tells the panel a host is CONNECTING, and it sits on
        "Processing..." for as long as claims keep arriving.
        """
        deadline = time.monotonic() + wait
        while True:
            st, _ = self._req(
                OP_REGISTER,
                registration_payload(self.ip, self.mac, self.host_id, intent=intent),
            )
            if st == -7 and intent == 0:
                intent = INTENT_CLAIM
                continue
            if st == 0:
                return True, intent
            if st == -4 and time.monotonic() < deadline:
                # Another host holds it; it clears tens of seconds after that
                # host stops talking.
                if on_wait:
                    on_wait()
                time.sleep(8)
                continue
            return False, st

    def unregister(self) -> int:
        """Release the claim, so we do not lock everyone else out."""
        body = bytearray(16)
        body[0:6] = self.mac
        body[11] = 0x01
        st, _ = self._req(OP_UNREGISTER, bytes(body))
        return st

    # -- host list and profiles --------------------------------------------

    def _read_doc(self) -> dict:
        _, body = self._op41(SUBJECT_CONN_USER, DIR_READ, None, 0x10)
        i = body.find(b'{"')
        if i < 0:
            return {}
        try:
            return json.loads(body[i:].split(b"\x00")[0].decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return {}

    def host_list(self) -> dict:
        """The full conn_user document: users, profiles and the selection."""
        return self._read_doc()

    def profiles(self) -> list[dict]:
        return self._read_doc().get("profiles", [])

    def user_id(self) -> str:
        """The 32-hex id the panel acts as.

        Read from the profiles rather than hard-coded: it is per-scanner, and an
        earlier version shipped one particular scanner's value to everybody.
        A host profile's user, not the cloud profile's: see profiles.py.
        """
        return user_id_from_profiles(self.profiles())

    def open_session(self, user_id: str = "", port: int = 0) -> int:
        """Tell the scanner which user the panel is acting for."""
        from .protocol import PORT_BUTTON_NOTIFY

        return self._op41(
            SUBJECT_SESSION,
            DIR_WRITE,
            {
                "version": 1,
                "function_level": 2,
                "current_user_id": user_id or self.user_id(),
                "host_address": self.ip,
                "host_port": port or PORT_BUTTON_NOTIFY,
            },
        )[0]

    def select_profile(self, prof_id: str) -> int:
        return self._op41(
            SUBJECT_PROFILE, DIR_WRITE, {"version": 1, "prof_id": prof_id}
        )[0]

    def write_host_list(self, doc: dict) -> int:
        """Replace the host list. Requires an INTENT_CLAIM registration."""
        return self._op41(SUBJECT_CONN_USER, DIR_WRITE, doc)[0]

    # -- 53218: the SCSI transport -----------------------------------------

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.host, PORT_CONTROL), timeout=self.timeout
        )
        self.sock.settimeout(self.timeout)
        self.sock.recv(4096)

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def scsi(
        self,
        cdb: bytes,
        read_len: int = 0,
        out: bytes = b"",
        collect: bool = False,
        quiet: float = 6.0,
    ) -> tuple[int, bytes]:
        """Tunnel one SCSI CDB. Returns (status, data)."""
        if self.sock is None:
            raise OSError("not connected; call connect() first")
        body = bytearray(0x30)
        body[0:6] = self.mac
        struct.pack_into(">I", body, 0x10, len(cdb))
        struct.pack_into(">I", body, 0x14, read_len)
        if out:
            struct.pack_into(">I", body, 0x18, cdb[4])
        body[0x20 : 0x20 + len(cdb)] = cdb
        self.sock.sendall(frame(OP_SCSI, bytes(body) + out))

        data = b""
        buf = b""
        status = None
        self.sock.settimeout(quiet)
        try:
            while True:
                chunk = self.sock.recv(262144)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 16:
                    total = struct.unpack(">I", buf[0:4])[0]
                    if total < 16 or len(buf) < total:
                        break
                    fr, buf = buf[:total], buf[total:]
                    if status is None:
                        status = struct.unpack(">I", fr[8:12])[0]
                    if len(fr) > 16 + 0x18:
                        data += fr[16 + 0x18 :]
                if not collect and status is not None and not buf:
                    break
                # An image is complete at its JPEG EOI. Waiting out the quiet
                # period instead costs `quiet` seconds per read -- around 30s
                # across both sides -- long enough for the scanner to abandon
                # the job and leave the panel on "Scanning...".
                if collect and data.endswith(b"\xff\xd9"):
                    break
                # Status back, nothing buffered, no image at all: the batch is
                # over. Without this the read after the last sheet waits out the
                # full window for data that never comes.
                if collect and status is not None and not buf and not data:
                    break
        except socket.timeout:
            # Nothing for `quiet` seconds. An image read ends at its EOI and a
            # plain command ends at its status, so silence is a fault, not a
            # slow scanner: returning what arrived so far as status 0 let a
            # stalled side be filed as a page.
            raise OSError(
                f"scanner stopped answering for {quiet:g}s during CDB {cdb[0]:#04x}"
            ) from None
        return signed(status if status is not None else 0), data
