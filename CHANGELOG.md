# Changelog

## Unreleased

- **USB: a fault no longer files a partial stack.** A paper jam, open cover,
  double feed, failed read or timeout before the hopper empties now raises
  `BatchAborted` from `scan_batch()` instead of returning the sides captured
  so far, which the daemon then filed as a finished document. The commands
  sent to the scanner are unchanged; the sheet's "scan complete" still goes
  out so the panel does not stick on "Scanning…". Nothing is published, and
  the paper is still in the hopper for a rescan.
- **`panelbeater scan` over USB exits 1 when nothing was scanned**, matching
  the network path.
- **USB: pages end where the paper ends, at the right size.** The unit this
  was measured on streams 0x00 after the paper, not 0x55, so every side read
  to the 42 MB ceiling and was filed 17.8 in tall. A chunk that is nearly all
  0x00 now ends the side too, trailing constant rows of any value are trimmed,
  and the JPEG carries the scan resolution so img2pdf makes an 8.7 in wide
  page instead of a 27 in one.
- First tests: `tests/test_usb_scan_batch.py` drives the real `read_sheet()`
  and `scan_batch()` against a scripted fake of the USB transport.

## 0.1.0 — first release

Panel-button scanning for the ScanSnap iX1500 on Linux, over Wi-Fi or USB,
without vendor software.

- **The Scan button works.** The panel is a client of whatever host holds a
  registration; panelbeater holds it, watches for presses, and scans.
- **Enrolment.** Your machine joins the scanner's host list and appears on the
  panel by name, alongside any others.
- **Batches.** Multi-sheet duplex from the ADF, blank reverse sides dropped,
  assembled into one PDF with a text layer if `ocrmypdf` is present.
- **Rename hooks.** Any executable that takes a path. Three examples ship, from
  three lines of shell to a local LLM; no LLM is required, and a hook that
  fails cannot lose a scan.
- **Staging.** Documents are named before they are filed, so a folder watcher
  such as paperless-ngx cannot take the file mid-rename.
- **Idle dimming.** Optional `dim_after`, for a scanner that lives somewhere
  you sleep.
- **Two transports.** Network and USB; USB needs no enrolment and no Wi-Fi and
  supports resolution and colour mode, but is exclusive with SANE.
- **`docs/PROTOCOL.md`.** The protocol, written for someone implementing it
  elsewhere — including a section on claims that turned out to be wrong, and
  the measurements that refuted them.

Known limitations are listed in the README; the notable ones are that a USB
cable disables the network path entirely, and that resolution is fixed at 300
dpi colour over the network because the parameter block is only partly decoded.
