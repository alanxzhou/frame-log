# Frame Log tagger

Desktop companion to the Frame Log phone app. Point it at a folder of lab
scans and the roll's JSON export, pair scans with logged frames in a browser
UI, then write the time, GPS, lens, aperture, shutter and notes into the
JPEGs as EXIF/XMP.

Python backend (FastAPI) + a single static HTML page. Metadata is written by
[ExifTool](https://exiftool.org), which must be installed separately.

## Install

Requires [uv](https://docs.astral.sh/uv/) and ExifTool. uv fetches a suitable
Python (3.11+) if you don't have one. The folder pickers use Python's bundled
Tk; python.org and Windows Store builds include it, Homebrew needs
`brew install python-tk`. Without it you can still type paths. (uv's own
managed Pythons include Tk too.)

```
# ExifTool
brew install exiftool                     # mac
# windows: download the zip from exiftool.org, rename exiftool(-k).exe to
#          exiftool.exe and put it on PATH (or pass --exiftool <path>)

# the tagger, from this folder
uv tool install .
# or run it without installing:
uv run framelog-tagger
```

Face tagging is part of the install (see [Faces](#faces)): about 1 GB of disk (0.4 GB of packages and a 0.6 GB
face model, which downloads the first time it's needed, so that run needs internet), 0.5 to 0.7 GB of memory
while it runs, and no GPU. The dependencies install on Python 3.11 through 3.14 (detection itself has only been
run on 3.11).

## Use

```
framelog-tagger            # if installed with `uv tool install .`
uv run framelog-tagger     # from this folder, no install needed
```

That opens `http://127.0.0.1:8765/` on a setup screen. Browse to the folder
of JPEGs from the lab and, if you logged the roll, its `.json` export from the
phone. Optionally fill in camera, film and ISO, and click **Load roll**. The
folders and the fallback zone are remembered for next time; camera and film
start blank on every roll.

Without an export the strip shows only the scans, and **Write** gives every
scan the roll-wide tags (camera, make, ISO, roll and film).

### Renaming the lab's folder

Labs deliver something like `12345678/123456780001.jpg`. The **Rename scans**
panel under the setup form turns the scans folder into `<roll name>/` and its
images into `<roll name>-01.jpg`, `-02`, … in natural-sort order, in place.

- **Reverse numbering** gives the lab's last scan `-01`, for labs that number
  a roll last frame first. Applying it again to an already-renamed folder
  flips the order back.
- To fix a typo or change the name, point it at the renamed folder. The
  current name is filled in for you, and the frames keep their numbers, so
  deleting a blank doesn't shift the frames after it. This works whenever
  every image is `<name>-NN` with the same name.
- Untick *rename the folder too* to rename only the files.
- Files sharing a stem (a frame delivered as both `.jpg` and `.tif`) get the
  same number. Other files (lab notes and the like) are left alone.
- Frame numbers are two digits, or three on rolls with over 99 images.

The preview lists every rename before anything moves. After **Rename**, the
scans folder field points at the new folder, ready for **Load roll**. Rename
before loading. A loaded roll keeps its paths, so **Change roll** first.

### Pairing

Scans and log entries start paired positionally (first scan to first frame,
and so on). Fix the exceptions:

- **Drag** a scan or a log card onto another cell in the same row to swap them.
- **▶ / ◀** shift that card and everything after it right or left. This is
  the one-click fix for "I forgot to log a frame" and "the lab skipped a blank".
- **Reverse scans** for a roll that was scanned tail-first.
- **Auto-pair** resets to positional order.
- Click a **frame number** to see exactly which ExifTool tags will be written.

### Editing and adding cards

When there's no export, or the export is missing frames, make the cards here:

- **New** in an empty slot makes a card from scratch. The frame number, zone,
  time and gear start from the card before it, as on the phone.
- The **copy** key on a card, then **Paste** in an empty slot, adds a duplicate
  to adjust. **▶** opens an empty slot where you need one.
- The **pencil** key (or a double-click) edits any card: frame #, time (local,
  in the card's zone), zone, coordinates, lens, aperture, shutter and notes.
  Leave the time blank to leave a scan's date tags alone.

The chip at the bottom of each card says where it stands: **original** (as read
from the export), **edited** (changed here, with the old value shown under
each changed field in the editor and a *Revert to original* button), or
**new** / **copy of #N** (made here; these can be deleted). Edits live in the
running tagger only. The export file is never changed, and **Change roll** or
restarting the tagger drops them.

Without an export, a scan in a slot with no card still gets the roll-wide tags.

**Write** writes the tags into each paired scan in place. Fill in *write to*
on the setup screen to leave the originals alone and write tagged copies into
that folder instead.
Pairing state is remembered in the browser so a reload doesn't lose your work.

Do this **before** importing the scans into Lightroom. Lightroom reads file
metadata at import; if the files are already in a catalog you'll need
Metadata → Read Metadata from File afterwards.

### Keywords

Keywords are written to each scan's XMP-dc:Subject, which Lightroom reads as
the photo's keywords at import.

- **Every scan** gets the roll's keywords from the setup screen: make and
  camera as one (`Nikon` + `FE` → *Nikon FE*; a camera already named
  *Nikon FE* isn't doubled) and the film (*Portra 400*). ISO isn't a keyword.
  They show at the right of the keyword panel.
- **Per scan**: click a scan and start typing. Enter (or Tab) takes the
  highlighted suggestion; a comma adds exactly what you typed, so
  `beach, Mom, ` adds two. With the box empty, ← / → move to the next scan so
  you can keep going, and Backspace removes the scan's last keyword.
- **Several at once**: shift-click a range, ⌘/Ctrl-click to add one, ⌘/Ctrl-A
  for all. The box then shows each keyword as on all of them, or as *2/5* (on
  some): click it to give it to all, × to take it off all.
- **Suggestions** are the keywords used on this roll, then your library, most
  recent first. Press **1–9** (with the box not focused) to toggle one on the
  selected scans. ⌘/Ctrl-Z undoes keyword changes; Esc clears the selection.
- Keywords match case-insensitively; the spelling you used first is kept.

Each scan's keywords are remembered in the browser along with the pairing.
Keywords already in the files (an earlier Write, another app) are read at
**Load roll**, so re-writing a roll keeps them. Write replaces a scan's
keywords with what the strip shows, roll's first.

The **library** is every keyword you've added or written, stored on this
computer next to the remembered setup values (`keywords.json` in
`~/.config/framelog-tagger/`, `%APPDATA%\framelog-tagger\` on Windows).
**Library…** lists them with counts; removing one only stops suggesting it.

### Faces

Finds the faces in your scans, suggests who they are, and writes the names as face regions and
keywords that Lightroom reads at import. Nothing is trained: a detector finds faces, a pretrained
network turns each into a fingerprint, and a name is suggested by comparing it with faces you've
already named. Every name you confirm makes the next suggestion better. How well it works, and how
that was measured: [docs/face-tagging-experiments.md](docs/face-tagging-experiments.md).

**Teach it who people are, once.** On the setup screen, under **Faces**, give the folder that holds your
Lightroom-tagged photos (everything inside is searched) and, under **not people**, the names that are pets.
Click **Save and learn**. Both are remembered on this computer (`faces.json`, next to `keywords.json`), and
from then on the tagger checks the folder for new photos in the background every time it starts, so the
gallery follows what you tag in Lightroom with nothing to run. A header note shows its progress; you can
carry on working, and names improve as it goes. Lightroom has to have saved the tags into the files first
(select all, Metadata → Save Metadata to Files, or Ctrl/⌘-S).

It reads each file's face regions with ExifTool, finds the face under every named box and keeps its
fingerprint. About 0.4 s per photo on a CPU, so a first pass over ten thousand photos is an hour or so; after
that only new photos are read, and the folder scan itself takes a minute or two. Pets go under **not people**:
a model built for human faces can't tell animals apart, and a pet's fingerprints would just clutter the
gallery (adding a name there also removes it from a gallery that already has it).

The same thing from a terminal: `framelog-tagger faces import [FOLDER ...] [--exclude NAME ...]`. It also
remembers the folder and the names, so with no arguments it repeats the last one. `--again` reads every
photo again (a name changed in Lightroom after it was learned isn't picked up otherwise).
`framelog-tagger faces people` lists who's in the gallery; `faces forget NAME` removes someone.

**Use it.** On the strip, **Find faces** looks at every scan (about half a second each; the first one
loads the model). Each card shows how many faces are named. Double-click a scan to review:

- Boxes: **green** = named, **dashed green** = suggested by the gallery but not yet confirmed,
  **amber** = nobody yet, **dim** = the detector isn't sure it's a face.
- Click a box. The bar underneath offers the closest people with a score (about 40 and up is a likely
  match), a name field that completes from everyone in the gallery, **Confirm** for a dashed name
  and **Clear**. Enter confirms and moves to the next face.
- A name is only filled in on its own when it's a close match **and** the detector is confident (0.6+).
  Doubtful boxes, which are mostly hair, ears and blurs, get suggestions but never a name you didn't pick.
- Names you choose are remembered for the roll if you reload the page (you'll need to Find faces again).

**Write** stores every named face as a face region (`XMP-mwg-rs:RegionInfo`) and as a keyword
(`XMP-dc:Subject`). Lightroom wants both: the region puts the name on the face, and the keyword makes it
searchable. Things to know:

- Regions already in the file are kept. Write changes only the boxes of faces you named: a box you name
  replaces the old box on that same face, and every other box (Lightroom's, or an earlier Write's, named or
  not) stays as it was. A scan where Find faces wasn't run, or with no named face, isn't touched. (Find faces
  doesn't show those existing boxes yet, so it will suggest a name for a face that already has one.)
- Scans that are rotated in the file (EXIF orientation other than 1) get the keywords but no regions.
  Lightroom stores those regions in the file's unrotated pixels, and mapping back into that frame isn't done yet.
- Every confirmed face is added to the gallery when you Write. Naming the same face again corrects it
  rather than adding a second copy.
- Do this before importing into Lightroom, as with everything else here. Lightroom reads face regions at
  import; for photos already in a catalog use Metadata → Read Metadata from File.

The gallery is `faces.npz` in the same folder as `keywords.json` (about 2 KB per face, so roughly 17 MB for eight thousand).
InsightFace's pretrained models are licensed for non-commercial use, which covers sorting your own photos.

### Time zones

Log timestamps are UTC; EXIF wants local wall-clock time plus an offset. The
phone app records its own time zone with every frame, so a roll that crosses
zones (Tokyo for the first half, Boston for the rest) is written frame by
frame in the right zone. Each card shows the zone and offset it will use.

Frames logged before the app recorded zones (export schema 1) use the
**fallback zone** from the setup screen, which defaults to this machine's
zone. Those cards are marked in amber and counted in the header.

### Options

The setup screen covers everything per roll. The command itself takes only:

| flag | effect |
| --- | --- |
| `--exiftool PATH` | ExifTool location if it isn't on PATH |
| `--port N` | listen on a different port (default 8765) |
| `--no-browser` | don't open the UI automatically |

### What gets written

| log field | tags |
| --- | --- |
| time, zone | DateTimeOriginal, CreateDate (local to the frame's zone), OffsetTimeOriginal/Digitized, GPSDateStamp/GPSTimeStamp (UTC) |
| lat / lon / alt / accuracy | GPSLatitude/Longitude/Altitude (+Ref), GPSHPositioningError |
| lens | LensModel, XMP-aux:Lens |
| aperture, shutter | FNumber, ExposureTime |
| notes | ImageDescription, XMP-dc:Description (Lightroom's Caption) |
| roll, film, frame # | UserComment |
| keywords | XMP-dc:Subject (Lightroom's Keywords) |
| named faces | XMP-mwg-rs:RegionInfo (face regions) and XMP-dc:Subject (see [Faces](#faces)) |

Camera, make and ISO from the setup screen go to Model, Make and ISO on every
scan, with or without an export. Make + camera and film are also keywords.

A shutter value the tool can't parse (e.g. `bulb`) is left untagged rather
than written wrong; everything else for that frame is still written.

## Layout

```
tagger/
  pyproject.toml, uv.lock
  framelog_tagger/
    cli.py        startup, opens the browser
    log.py        JSON export → Roll/LogEntry (the contract with ../index.html's buildJson)
    scans.py      JPEG listing (natural sort), thumbnails, "already tagged" check
    rename.py     lab folder → <roll>/<roll>-NN.jpg (plan, then apply)
    exif.py       LogEntry → ExifTool arguments (per-frame zone); write_tags()
    dialogs.py    native folder/file pickers via a Tk helper process
    config.py     remembered setup-screen values
    keywords.py   roll keywords, reading keywords from a scan, the keyword library
    regions.py    face regions: Lightroom's coordinate convention, matching boxes, writing/reading XMP regions (no model needed)
    faces.py      detector + recognition model, the gallery of named faces, learning from your Lightroom folder
    server.py     FastAPI: /api/session, /api/pick, /api/load, /api/thumb, /api/preview, /api/entries, /api/keywords, /api/faces/detect, /api/write, /api/rename/*
    static/index.html   setup screen + the pairing UI
  experiments/    the scripts behind docs/face-tagging-experiments.md (standalone: `uv run experiments/face_eval.py --help`)
  docs/           face-tagging-experiments.md
  tests/          pytest; ExifTool and the face model are stubbed so they run without them
```

Run the tests with `uv run pytest` from this folder (the `dev` dependency
group is installed by default).
