# Copyright (C) 2026 Jenna Nelson
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep the panel alive and scan when the Scan button is pressed.

Two things happen in one loop, and they are the same thing:

  * The panel is only enabled while a host holds a registration. Stop
    registering and the Scan button greys out within a minute or so. So the
    keep-alive IS the registration.
  * A press is reported two ways -- a UDP notice to the host, and the scan_sw
    bit in GET_HW_STATUS. Both are watched, because the notice can be lost and
    the bit is only set for about half a second.

Registration happens BEFORE the first poll. The other order leaves the panel
awake but owned by nobody for a whole interval, and the first press after a
power cycle is lost.
"""

from __future__ import annotations

import os
import select
import socket
import tempfile
import threading
import time
from pathlib import Path

from . import output
from .config import Config
from .protocol import (
    OP_BUTTON_NOTICE,
    PORT_BUTTON_NOTIFY,
    PORT_HOST_NOTIFY,
    hw_status,
    local_ip_and_mac,
    parse_frame,
    registration_payload,
    signed,
    tcp_request,
)
from .protocol import OP_REGISTER, PORT_REQUEST
from .scanning import scan_to_dir


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def serve(cfg: Config, host: str, log=print) -> int:
    host_id = cfg.host_id
    hid = bytes.fromhex(host_id)
    ip, mac, _ = local_ip_and_mac(host)

    interval = cfg.num("interval", 15.0)
    poll_ms = cfg.num("poll", 200.0)

    reregister = threading.Event()
    busy = threading.Lock()
    last_done = [0.0]

    def do_scan(source: str, paper: bool | None = None) -> None:
        """Run one scan, from whichever path noticed the press first.

        Both paths can see the same press, so this has to be idempotent. `busy`
        stops them overlapping; the cooldown stops the poll re-firing on a
        scan_sw that is still set when the scan returns.

        Only the part that talks to the scanner is serialised here. Assembly,
        OCR and the rename hook go to a worker thread, because they can take
        minutes -- the hook is allowed 300s by default -- and this loop must get
        back to registering. The panel goes dead about a minute after the last
        registration, so post-processing inline would switch the scanner off
        while naming the document it had just produced.
        """
        if not busy.acquire(blocking=False):
            return
        try:
            if time.monotonic() - last_done[0] < 5.0:
                return
            detail = "" if paper is None else f" (paper_loaded={paper})"
            log(f"[{stamp()}] SCAN BUTTON PRESSED via {source}{detail}")
            pages, work = capture(cfg, host, host_id, log=log)
        finally:
            last_done[0] = time.monotonic()
            busy.release()
        if pages:
            # NOT a daemon thread: a restart should wait for naming to finish
            # rather than abandon it. The PDF is written to staging before the
            # hook runs, so even a hard kill leaves a recoverable document
            # there, and the next start files it.
            threading.Thread(
                target=postprocess, args=(cfg, pages, work), kwargs={"log": log}
            ).start()
        elif work:
            cleanup(work)

    def notice_server():
        """Listen for the scanner's UDP notices.

        The scanner announces both of these whether or not anyone is listening;
        with the ports unbound the kernel answers each with an ICMP rejection.

            UDP 53220  broadcast, op 0x21   "I have just booted"
            UDP 55265  unicast,   op 0x01   "the Scan button was pressed"

        Byte 0 of the payload is a counter, not an event type -- it increments
        per press. The port and the opcode identify the event.
        """
        socks = {}
        for port, what in ((PORT_HOST_NOTIFY, "boot"), (PORT_BUTTON_NOTIFY, "button")):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("", port))
            except OSError as exc:
                log(f"cannot listen on UDP {port}: {exc}")
                continue
            socks[s] = (port, what)
        if not socks:
            return
        log(
            f"listening for notices on UDP {', '.join(str(p) for p, _ in socks.values())}"
        )
        seen: dict[tuple, float] = {}
        while True:
            ready, _, _ = select.select(list(socks), [], [], 1.0)
            for s in ready:
                port, what = socks[s]
                try:
                    data, addr = s.recvfrom(4096)
                except OSError:
                    continue
                parsed = parse_frame(data)
                op = parsed[0] if parsed else -1
                # Each notice arrives three times. Dedup on the port alone --
                # the opcode and the counter both vary between the copies of
                # what is logically one event.
                now = time.monotonic()
                if now - seen.get((port,), 0.0) < 3.0:
                    continue
                seen[(port,)] = now
                log(f"[{stamp()}] notice {what} from {addr[0]} op=0x{op:02x}")
                if port == PORT_HOST_NOTIFY:
                    reregister.set()
                elif op == OP_BUTTON_NOTICE:
                    do_scan("UDP notice")
                else:
                    log(f"[{stamp()}] ignored: op=0x{op:02x} is not a button press")

    threading.Thread(target=notice_server, daemon=True).start()

    file_orphans(cfg, log=log)

    log(f"registering every {interval:.0f}s as host_id {host_id} ({ip})")
    intent_note = [False]
    was_pressed = False
    try:
        while True:
            # Register FIRST, then poll until the next one is due. Polling first
            # leaves the panel unowned for a whole interval after startup.
            intent = 0
            payload = registration_payload(ip, mac, hid, intent=intent)
            try:
                reply = tcp_request(host, PORT_REQUEST, OP_REGISTER, payload, timeout=5)
                parsed = parse_frame(reply) if reply else None
                status = signed(parsed[0]) if parsed else None
                if status == -7:
                    # Intent 0 is only accepted for the one host_id the scanner
                    # already treats as its own. Claim once; going back to 0 on
                    # the next cycle keeps the panel out of "Processing...",
                    # which is where it sits while claims keep arriving.
                    from .protocol import INTENT_CLAIM

                    payload = registration_payload(ip, mac, hid, intent=INTENT_CLAIM)
                    reply = tcp_request(
                        host, PORT_REQUEST, OP_REGISTER, payload, timeout=5
                    )
                    parsed = parse_frame(reply) if reply else None
                    status = signed(parsed[0]) if parsed else None
                    if status == 0 and not intent_note[0]:
                        intent_note[0] = True
                        log(f"[{stamp()}] claimed the scanner for this host")
                if status == 0:
                    pass  # quiet: this happens every interval
                elif status is not None:
                    log(f"[{stamp()}] registration refused (status {status})")
                else:
                    log(f"[{stamp()}] no reply to registration")
            except OSError as exc:
                log(f"[{stamp()}] cannot reach scanner: {exc}")

            # Poll for the button between registrations. Once a network host is
            # registered the scanner reports the press here and stops setting
            # scan_sw on USB, so a USB poller sees nothing.
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                try:
                    g = hw_status(host, mac)
                except OSError:
                    time.sleep(1.0)
                    continue
                pressed = bool(len(g) > 4 and g[4] & 0x01)
                if pressed and not was_pressed:
                    do_scan("button poll", paper=not (g[3] & 0x80))
                    was_pressed = False
                    break
                was_pressed = pressed
                # A boot notice means the scanner just came up and belongs to
                # nobody. Re-register at once; waiting out the interval is what
                # loses the first press after a power cycle.
                if reregister.is_set():
                    break
                time.sleep(poll_ms / 1000.0)
            reregister.clear()
    except KeyboardInterrupt:
        log("")
    return 0


def cleanup(work: Path) -> None:
    for p in work.glob("*"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        os.rmdir(work)
    except OSError:
        pass


def capture(
    cfg: Config, host: str, host_id: str, log=print
) -> tuple[list[str], Path | None]:
    """The scanner-bound half: pull the pages off the ADF and stop.

    Kept separate from post-processing so the daemon can go straight back to
    registering while the document is assembled and named.
    """
    work = Path(tempfile.mkdtemp(prefix="panelbeater-"))
    try:
        n = scan_to_dir(
            host,
            host_id,
            str(work / "page"),
            prof_id=cfg.get("prof_id"),
            max_sheets=int(cfg.num("max_sheets", 100)),
            skip_register=True,  # the daemon already holds the registration
            log=log,
        )
    except OSError as exc:
        log(f"  scan failed: {exc}")
        return [], work
    if not n:
        log("  nothing scanned")
        return [], work
    return sorted(str(p) for p in work.glob("page-*.jpg")), work


def postprocess(cfg: Config, pages: list[str], work: Path | None, log=print):
    """Assemble, OCR, name and file. Runs off the registration loop."""
    try:
        return output.finish(pages, time.strftime("%Y%m%d-%H%M%S"), cfg, log=log)
    except Exception as exc:  # noqa: BLE001 -- a worker must never die silently
        log(f"  post-processing failed: {type(exc).__name__}: {exc}")
        return None
    finally:
        if work:
            cleanup(work)


def file_orphans(cfg: Config, log=print) -> None:
    """File anything left in staging by a previous run.

    A restart during OCR or a rename hook leaves a finished PDF in staging that
    nothing is watching, so it would sit there unnoticed. It is already a
    complete document; file it under the name it has.
    """
    staging = cfg.staging_dir
    if not staging.is_dir():
        return
    for p in sorted(staging.glob("*.pdf")):
        log(f"[{stamp()}] filing {p.name}, left over from a previous run")
        try:
            output.deliver(p, cfg.output_dir, log=log)
        except OSError as exc:
            log(f"  could not file it: {exc}")
