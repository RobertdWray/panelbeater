# scansnap-linux

Press the Scan button on a ScanSnap iX1500 and have the page arrive on your
Linux machine. No vendor software, no Windows VM.

The iX1500's touch panel is not a button — it is a client of whatever computer
currently owns the scanner, and it only works while that computer keeps telling
the scanner it is there. Without vendor software the panel greys out and the
scanner becomes a plain USB device you have to drive from the keyboard. This
implements the protocol the panel actually speaks, so the button works again.

```
$ scansnap enrol
$ systemctl --user enable --now scansnap
```

Then press Scan. A searchable PDF appears in `~/Documents/Scans`.

Tested on an iX1500 (`04c5:159f`) over Wi-Fi. The iX1600 and iX1400 share the
platform and will probably work — reports welcome.

## What works

- The Scan button on the panel starts a scan on your machine
- Multi-sheet batches from the ADF, duplex, 300 dpi colour
- Blank reverse sides dropped automatically
- Combined into one PDF, with a text layer if `ocrmypdf` is installed
- Filed into a directory you choose, with an optional rename hook
- Enrolment: your machine appears on the panel by name, alongside any others

## What does not

- **Resolution is fixed at 300 dpi colour** over the network. The parameter
  block that sets it is only partly decoded; see `docs/PROTOCOL.md`.
- **Wi-Fi has to be configured on the panel itself.** The credentials are typed
  on the scanner and never cross the wire, so no host tool can do it.
- **Which host the panel points at is chosen on the panel.** A host can add
  itself to the list but cannot select itself; those writes are accepted and
  ignored by the scanner.
- Scanning over USB is not part of this package. See
  [Scanning over USB](#scanning-over-usb).

## Requirements

Python 3.9 or newer. **The scan path itself uses only the standard library**,
so it works on a fresh machine with nothing installed. Everything else is
optional and degrades to a clear message rather than a traceback:

| for | install | without it |
|---|---|---|
| one PDF instead of loose JPEGs | `img2pdf`, or Pillow | you get the JPEGs |
| dropping blank reverse sides | Pillow and numpy | every side is kept |
| a searchable text layer | `ocrmypdf` | the PDF has no text layer |

## Install

```sh
./install.sh              # into ~/.local, plus a systemd --user unit
```

It copies a Python package and writes a launcher — nothing is compiled and no
distro packaging is involved. `./install.sh --prefix /usr/local` as root
installs system-wide; `./install.sh --uninstall` removes it and leaves your
config and scans alone.

There is a `pyproject.toml` if you prefer `pip install .`.

## Set up

```sh
scansnap config --write     # a config file to edit, at ~/.config/scansnap/config
scansnap discover           # find the scanner
scansnap enrol              # add this machine to its host list
scansnap status             # check what the scanner thinks
```

`enrol` puts your machine in the scanner's list under a name you choose. Then,
**on the scanner's panel, tap the host name at the top and choose your
machine** — that part cannot be done from the computer.

```sh
systemctl --user enable --now scansnap
loginctl enable-linger $USER    # so it runs when you are not logged in
```

`scansnap status` is the thing to run when something is wrong: it prints the
config it loaded, whether the scanner is reachable, whether there is paper, and
every host the scanner knows about with the selected one marked.

## Configuration

`~/.config/scansnap/config`, or `/etc/scansnap/config`, or `$SCANSNAP_CONFIG`.
Every key can also be set as `SCANSNAP_<KEY>` in the environment, which is how
to override things in the systemd unit without editing it.

```ini
[scansnap]
scanner = auto              # or an IP; auto discovers, which takes a few seconds
name =                      # how this host appears on the panel; default hostname
output_dir = ~/Documents/Scans
staging_dir =               # default: a .staging dir inside output_dir
hook =                      # optional rename program; see hooks/README.md
pdf = yes
blank_removal = yes
blank_threshold = 0.5
max_sheets = 100
interval = 15               # seconds between registrations; this is the keep-alive
```

Setting `scanner` to a fixed IP is worth doing: the iX1500 answers unicast
discovery but ignores broadcasts, so `auto` has to sweep the subnet.

## Renaming scans

Scans are filed as `scan-YYYYmmdd-HHMMSS.pdf` by default. Point `hook` at a
program to do better. **No LLM is required** — a hook is any executable that
takes a path:

| hook | needs | result |
|---|---|---|
| `hooks/rename-by-date` | nothing | `2026-08-11 1423.pdf` |
| `hooks/rename-by-text` | `pdftotext` | first real line of the text layer |
| `hooks/rename-with-ollama` | `ollama` | `Origin Electricity Bill January 2025` |

See `hooks/README.md` for the contract. A hook that fails, hangs or returns
nonsense cannot lose a scan — the document is filed under its timestamp name
instead.

### If something else watches your scans folder

paperless-ngx, Nextcloud and Syncthing take a new file within seconds and
usually delete the original. That is why documents are assembled and named in a
**staging directory** first and only moved into `output_dir` when finished —
otherwise a hook that takes a minute renames a file that is no longer there.
Keep both on the same filesystem so the handover is an atomic rename and the
watcher never sees a partial file.

## Scanning over USB

Not included here. The catch is that the two are mutually exclusive: while the
registration that keeps the panel alive is active, USB scanning fails with
`SANE_STATUS_IO_ERROR`. You can have the panel or you can have `scanimage`, and
this package chooses the panel.

If you want USB scanning, use the existing SANE `fujitsu` backend and do not
run this daemon. `docs/PROTOCOL.md` documents the USB side too, including the
`SET WINDOW` two-descriptor requirement that makes duplex work.

## For protocol implementers

`docs/PROTOCOL.md` is the real output of this project: framing, discovery,
registration, enrolment, button notices, the scan sequence, status codes, and
the USB transport. It is written for someone implementing this somewhere else,
and it is explicit about which findings are tested and which are inferred.

It is GPL-2.0-or-later specifically so a SANE developer can lift code and
constants into sane-backends without a licensing question. There is a short
section at the end on what that would involve, including a one-line fix to the
existing `fujitsu` backend (`f1 09` should be `f1 04`) that stops the panel
hanging on "Scanning…" after a SANE-driven scan.

## Security

There is no authentication in this protocol. No pairing code, no confirmation
prompt: anything that can reach the scanner's TCP port can add itself to the
host list, take ownership of the panel, and scan. That is the vendor's design,
not a choice made here. Treat the scanner as you would a network printer and
keep it off untrusted networks.

## Licence

GPL-2.0-or-later. See `LICENSE`.

Reverse-engineered from packet captures for interoperability. No vendor code
was read or used. "ScanSnap" and "Fujitsu" are trademarks of their owners; this
project is not affiliated with or endorsed by them.
