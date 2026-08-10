# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""The iX1500 touch-panel protocol: SETUP PROF INFO over SCSI diagnostics.

See doc/panel_protocol.md. In short, SEND_DIAGNOSTIC (0x1D) and READ_DIAGNOSTIC
(0x1C) carry an ASCII command language, and `SETUP PROF INFO` exchanges JSON
describing registered hosts and the profiles shown on the touch panel.

A transaction is five sub-operations, each a SEND_DIAGNOSTIC followed by a
READ_DIAGNOSTIC that acknowledges it:

    op 00  begin          announces the byte count that op 01 will carry
    op 01  payload        16-byte sub-header + JSON
    op 02  commit         its ack reports how many bytes are available to read
    op 03  request read   asks for available+8
    op 04  end

Usage:
    panel_protocol.py users            # read registered hosts and profiles
    panel_protocol.py raw 01           # arbitrary request-type byte, no payload
"""

from __future__ import annotations

import contextlib
import json

from .transport import Ix1500

SEND_DIAGNOSTIC = 0x1D
READ_DIAGNOSTIC = 0x1C
CMD_NAME = b"SETUP PROF INFO "  # exactly 16 bytes, space padded

# Sub-header request types seen in the ScanSnap Home capture.
# Sub-header byte 0 selects the subject; byte 1 is the direction (0x10 write,
# 0x00 read). Reads and writes of the conn_user list only work INSIDE a
# session -- open with 0x02 and close with 0x04, both carrying the same
# version:1 host document. Outside a session the scanner accepts writes and
# discards them, and returns correctly sized but zero-filled reads.
REQ_CONN_USER = 0x01  # the registered-host list and profiles
REQ_SESSION_OPEN = 0x02
REQ_SESSION_CLOSE = 0x04
REQ_READ_INFO = REQ_CONN_USER  # backwards-compatible alias
REQ_WRITE_HOST = REQ_SESSION_OPEN


class PanelError(RuntimeError):
    pass


class Panel:
    def __init__(self, dev: Ix1500):
        self.dev = dev

    def _send(self, body: bytes, ack: bool = True) -> bytes:
        """One SEND_DIAGNOSTIC, optionally followed by its 8-byte ack read.

        Ops 00, 01, 02 and 04 are each acknowledged by an 8-byte
        READ_DIAGNOSTIC. Op 03 is NOT: it arms the data read, and the very next
        READ_DIAGNOSTIC must be the full-length one. Issuing an 8-byte read
        first consumes the head of the reply and leaves the real read
        misaligned, which returns a correctly sized but zero-filled buffer.
        """
        payload = CMD_NAME + body
        cdb = bytes([SEND_DIAGNOSTIC, 0, 0, len(payload) >> 8, len(payload) & 0xFF, 0])
        _, status = self.dev.command(cdb, 0, payload)
        if status[9] != 0:
            raise PanelError(f"SEND_DIAGNOSTIC rejected, status 0x{status[9]:02x}")
        return self._read(8) if ack else b""

    def _read(self, length: int) -> bytes:
        cdb = bytes([READ_DIAGNOSTIC, 0, 0, length >> 8, length & 0xFF, 0])
        data, status = self.dev.command(cdb, length)
        if status[9] != 0:
            raise PanelError(f"READ_DIAGNOSTIC rejected, status 0x{status[9]:02x}")
        return data

    @staticmethod
    def _op(op: int, total: int = 0) -> bytes:
        """32-byte sub-operation header. Byte 0 is the op, bytes 8..11 a
        big-endian count of what op 01 will carry."""
        b = bytearray(16)
        b[0] = op
        b[8:12] = total.to_bytes(4, "big")
        return bytes(b)

    @staticmethod
    def _subheader(req_type: int, payload_len: int, write: bool = False) -> bytes:
        """16-byte block preceding the JSON.

        Byte 0 selects the subject (0x01 = the conn_user list, 0x02 = host
        session info). Byte 1 is the direction: 0x10 writes, 0x00 reads. The
        length at offset 12 is little-endian, unlike the big-endian count in
        the op header.
        """
        b = bytearray(16)
        b[0] = req_type
        b[1] = 0x10 if write else 0x00
        b[12:16] = payload_len.to_bytes(4, "little")
        return bytes(b)

    def transact(
        self, req_type: int, payload: bytes = b"", write: bool = False
    ) -> bytes:
        """Run a full five-step transaction and return the reply payload."""
        block = self._subheader(req_type, len(payload), write) + payload
        total = len(block)

        self._send(self._op(0x00, total))
        self._send(self._op(0x01, total) + block)
        ack = self._send(self._op(0x02))

        # The commit ack reports how many bytes are waiting. Each stage adds
        # its own 8-byte header, hence the two +8s.
        available = int.from_bytes(ack[4:8], "big")
        reply = b""
        if available:
            self._send(self._op(0x03, available + 8), ack=False)
            raw = self._read(available + 16)
            # 8-byte outer header, then a 16-byte sub-header whose offset-12
            # little-endian field gives the JSON length.
            if len(raw) >= 24:
                declared = int.from_bytes(raw[20:24], "little")
                reply = raw[24 : 24 + declared] if declared else raw[24:]
        self._send(self._op(0x04))
        return reply

    # -- session bracketing ------------------------------------------------

    def session_doc(self, user_id: str) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "function_level": 2,
                "current_user_id": user_id,
                "host_address": "",  # empty over USB; an IP over Wi-Fi
                "host_port": 53220,
            },
            separators=(",", ":"),
        ).encode()

    def open_session(self, user_id: str) -> bytes:
        return self.transact(REQ_SESSION_OPEN, self.session_doc(user_id), write=True)

    def close_session(self, user_id: str) -> bytes:
        return self.transact(REQ_SESSION_CLOSE, self.session_doc(user_id), write=True)

    @contextlib.contextmanager
    def session(self, user_id: str):
        self.open_session(user_id)
        try:
            yield self
        finally:
            self.close_session(user_id)

    def read_conn_user(self) -> bytes:
        return self.transact(REQ_CONN_USER, b"", write=False)

    def write_conn_user(self, payload: bytes) -> bytes:
        return self.transact(REQ_CONN_USER, payload, write=True)
