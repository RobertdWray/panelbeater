# panelbeater

Press the Scan button on a ScanSnap iX1500 and have the page arrive on your
Linux machine. No vendor software, no Windows VM.

The iX1500's touch panel is not a button — it is a client of whatever computer
currently owns the scanner, and it only works while that computer keeps telling
the scanner it is there. Without vendor software the panel greys out and the
scanner becomes a plain USB device you have to drive from the keyboard. This
implements the protocol the panel actually speaks, so the button works again.

```
$ panelbeater enrol
$ systemctl --user enable --now panelbeater
```

Then press Scan. A searchable PDF appears in `~/Documents/Scans`.

Tested on an iX1500 (`04c5:159f`) over Wi-Fi. The iX1600 and iX1400 share the
platform and will probably work — reports welcome.

## What works

- The Scan button on the panel starts a scan on your machine, over **Wi-Fi or
  USB**
- Multi-sheet batches from the ADF, duplex; 300 dpi colour over the
  network, adjustable over USB
- Blank reverse sides dropped automatically
- Combined into one PDF, with a text layer if `ocrmypdf` is installed
- Filed into a directory you choose, with an optional rename hook
- Enrolment: your machine appears on the panel by name, alongside any others

## What does not

- **Resolution is fixed at 300 dpi colour over the network.** The parameter
  block that sets it is only partly decoded; see `docs/PROTOCOL.md`. Over USB
  it is adjustable.
- **Wi-Fi has to be configured on the panel itself.** The credentials are typed
  on the scanner and never cross the wire, so no host tool can do it.
- **Which host the panel points at is chosen on the panel.** A host can add
  itself to the list but cannot select itself; those writes are accepted and
  ignored by the scanner.
- **USB and SANE cannot both have the scanner.** Only one process can claim the
  interface. See [Scanning over USB](#scanning-over-usb).

## Requirements

Python 3.9 or newer. **The scan path itself uses only the standard library**,
so it works on a fresh machine with nothing installed. Everything else is
optional and degrades to a clear message rather than a traceback:

| for | install | without it |
|---|---|---|
| one PDF instead of loose JPEGs | `img2pdf`, or Pillow | you get the JPEGs |
| dropping blank reverse sides | Pillow and numpy | every side is kept |
| a searchable text layer | `ocrmypdf` | the PDF has no text layer |
| scanning over USB | `pyusb`, Pillow, numpy | the network path still works |

That is true of the **network** path. Scanning over USB does need `pyusb` and
an image library, because the scanner sends raw data there rather than JPEG.

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
panelbeater config --write     # a config file to edit, at ~/.config/panelbeater/config
panelbeater discover           # find the scanner
panelbeater enrol              # add this machine to its host list
panelbeater status             # check what the scanner thinks
```

`enrol` puts your machine in the scanner's list under a name you choose. Then,
**on the scanner's panel, tap the host name at the top and choose your
machine** — that part cannot be done from the computer.

```sh
systemctl --user enable --now panelbeater
loginctl enable-linger $USER    # so it runs when you are not logged in
```

`panelbeater status` is the thing to run when something is wrong: it prints the
config it loaded, whether the scanner is reachable, whether there is paper, and
every host the scanner knows about with the selected one marked.

## Configuration

`~/.config/panelbeater/config`, or `/etc/panelbeater/config`, or `$PANELBEATER_CONFIG`.
Every key can also be set as `PANELBEATER_<KEY>` in the environment, which is how
to override things in the systemd unit without editing it.

```ini
[panelbeater]
transport = auto            # network, usb, or auto (USB if the cable is there)
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

Works too, and for a lot of people it is the better option:

```ini
[panelbeater]
transport = usb        # or auto, which uses USB when the cable is there
```

**USB needs no enrolment, no host list and no Wi-Fi.** `SEND DIAGNOSTIC` has no
registration concept, so it works on a scanner straight out of the box — no
`panelbeater enrol`, and nothing to tap on the panel. If you just want the
button to work, plug the cable in and set `transport = usb`.

It also does resolution and colour mode, which the network path cannot:

```ini
resolution = 300       # or 150, 600
mode = color           # or gray, lineart
simplex = no
```

Needs `pyusb`, plus Pillow and numpy to decode the image. Scanning is done
in-process rather than by shelling out to `scanimage`, because handing the
device over for the duration of a scan means being blind to the Stop button and
to errors.

**The catch: it is mutually exclusive with SANE.** Only one process can claim
the USB interface, so while panelbeater is running `scanimage` fails at open
with "Invalid argument", and while a SANE scan is in progress panelbeater
cannot poll the button. If you need both, use `transport = network` and leave
USB to SANE.

You will probably need a udev rule to open the device without root:

```
# /etc/udev/rules.d/60-panelbeater.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="04c5", ATTR{idProduct}=="159f", MODE="0664", TAG+="uaccess"
```

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
