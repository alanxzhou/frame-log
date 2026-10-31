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

## Use

```
framelog-tagger            # if installed with `uv tool install .`
uv run framelog-tagger     # from this folder, no install needed
```

That opens `http://127.0.0.1:8765/` on a setup screen. Browse to the folder
of JPEGs from the lab and the roll's `.json` export from the phone, optionally
fill in camera, film and ISO, and click **Load roll**. The values are
remembered for next time.

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

**Write** copies each paired scan into `<scans>/tagged/` and writes the tags
into the copy. Originals are never touched unless you tick *in place*.
Pairing state is remembered in the browser so a reload doesn't lose your work.

Do this **before** importing the scans into Lightroom. Lightroom reads file
metadata at import; if the files are already in a catalog you'll need
Metadata → Read Metadata from File afterwards.

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
    server.py     FastAPI: /api/session, /api/pick, /api/load, /api/thumb, /api/preview, /api/write, /api/rename/*
    static/index.html   setup screen + the pairing UI
  tests/          pytest; ExifTool is stubbed so they run without it
```

Run the tests with `uv run pytest` from this folder (the `dev` dependency
group is installed by default).
