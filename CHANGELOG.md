# Changelog

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
