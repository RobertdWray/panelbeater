# Rename hooks

By default a scan is filed as `scan-YYYYmmdd-HHMMSS.pdf`. That is deliberate:
it always works, needs nothing installed, and never loses a document.

If you want better names, point `hook` at a program:

```ini
[scansnap]
hook = ~/.config/scansnap/hooks/rename-by-date
```

**No LLM is required.** A hook is any executable. The examples here range from
three lines of shell to a local model, and the plain ones are the ones most
people should start with.

## The contract

* The hook is run with **one argument**: the absolute path of the finished PDF.
  It is also in `$SCANSNAP_PDF`.
* The file is in the **staging directory**, not the output directory. Nothing
  is watching it there, so you can take your time.
* The hook may **rename or move the file within that directory**.
* If it prints a path on stdout, that is taken as the new location.
  If it prints nothing but renamed the file, and exactly one PDF is left in
  staging, that one is used.
* Anything on stderr is logged.
* A non-zero exit, a timeout (`hook_timeout`, default 300s), or a path that
  does not exist is **ignored**, and the document keeps its timestamp name.

Then scansnap moves the result into `output_dir`.

A hook that *fails* cannot lose a scan: a non-zero exit, a crash, a timeout, or
a nonsense path all fall back to filing the document under the name it already
had. A hook that *deletes* the file obviously can — while it runs, the hook owns
the document. That case is reported rather than hidden, but nothing can bring
the file back, so do not `rm` in a hook.

A path printed by the hook is only believed if it is inside the staging
directory. Otherwise scansnap would file whatever the hook happened to name.

## Why the staging directory exists

`output_dir` is often watched by something that ingests documents —
paperless-ngx, Nextcloud, a Syncthing share. Those tools take the file within
seconds and usually delete the original. A hook that takes a minute to think
would be renaming a file that no longer exists. So naming happens where nothing
is watching, and the document is published only when it is finished.

Keep `staging_dir` on the same filesystem as `output_dir`. The handover is then
a rename, which is atomic, and a watcher can never see a half-written file.

## Examples in this directory

| hook | needs | what it does |
|---|---|---|
| `rename-by-date` | nothing | `2026-08-11 1423.pdf` — a readable date instead of a stamp |
| `rename-by-text` | `pdftotext` | first meaningful line of the OCR layer |
| `rename-with-ollama` | `ollama` | asks a local model for a short descriptive name |

Copy one, edit it, make it executable, and point `hook` at it.

## Writing your own

The whole interface is "take a path, maybe rename it". In shell:

```sh
#!/bin/sh
set -eu
pdf="$1"
new="$(dirname "$pdf")/Invoice $(date +%Y-%m-%d).pdf"
mv -n -- "$pdf" "$new"
printf '%s\n' "$new"
```

Two things worth doing in any hook that builds a name from document text:

* **Filter the name, do not just strip bad characters.** Text out of OCR or a
  model is untrusted and is about to become a filename. Allow a known-good set
  of characters rather than removing the ones you thought of; `/` and a leading
  `.` are the ones that matter.
* **Never overwrite.** Add ` (2)` rather than replacing a file that is already
  there. `rename-by-text` shows both.
