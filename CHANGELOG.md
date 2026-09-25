# Changelog

## Unreleased

- **Network: only a complete batch is filed.** Over the network, a paper jam,
  cover open or double feed returned the sides captured so far and the daemon
  filed them as a finished document; a sheet missing its back, a `READ` that
  failed, an image cut off before its EOI, a stalled read (returned as
  status 0 with whatever had arrived) and a hopper check that failed or came
  back short (read as "hopper empty") all did the same. Each now raises
  `BatchAborted` from `scan_batch()`; `e0`/`d6` still go out, including
  after a refused setup command, which used to leave the job open. Nothing
  is published and the paper is still in the hopper. `panelbeater scan`
  prints the reason and exits 1.
- **Network: the session acts as a host profile's user and scans with a host
  profile.** The same "Send to ScanSnap Cloud"-listed-first problem as the
  USB fix below: blank `prof_id` selected `profiles[0]` and the session
  quoted its user. Both now prefer a `prof_type` 0 profile, and the config's
  `user_id` override is honoured on the network path too. The rule lives in
  `profiles.py`, shared by both transports.
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
- **USB: the panel is armed as a host profile's user, not the cloud
  profile's.** A scanner set up with ScanSnap Home lists "Send to ScanSnap
  Cloud" first; acting as its user made every scan a cloud job and left the
  panel on an orange "!" ("The device is not responding") after each batch.
  Profiles with `prof_type` 0 (a computer) are now preferred. `user_id` in
  the config still overrides.
- **USB: multi-sheet batches scan every sheet.** After the first sheet the
  iX1500's hopper sensor stays at "empty" with paper still loaded, so every
  batch stopped after one sheet and the rest fed through unscanned. Later
  sheets are now fed without asking the sensor; an empty hopper answers the
  feed with CHECK CONDITION and sense 03/80/03, which ends the batch. The
  first sheet still checks the sensor, so an empty-hopper press feeds nothing.
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
