# Frame Log tagger

Desktop companion to the Frame Log phone app. Point it at a folder of lab
scans and the roll's JSON export, pair scans with logged frames in a browser
UI, then write the time, GPS, lens, aperture, shutter and notes into the
JPEGs as EXIF/XMP.

Python backend (FastAPI) + a single static HTML page. Metadata is written by
[ExifTool](https://exiftool.org), which must be installed separately.

## Install

Requires Python 3.11+ and ExifTool.

```
# ExifTool
brew install exiftool                     # mac
# windows: download the zip from exiftool.org, rename exiftool(-k).exe to
#          exiftool.exe and put it on PATH (or pass --exiftool <path>)

# the tagger, from this folder
uv tool install .                         # or: pip install .
# for hacking on it:
uv sync --extra dev                       # or: pip install -e ".[dev]"
```

## Use

```
framelog-tagger <scans-folder> <roll.json> [--tz America/New_York] [options]
```

That opens `http://127.0.0.1:8765/`. Scans and log entries start paired
positionally (first scan to first frame, and so on). Fix the exceptions:

- **Drag** a scan or a log card onto another cell in the same row to swap them.
- **▶ / ◀** shift that card and everything after it right or left. This is
  the one-click fix for "I forgot to log a frame" and "the lab skipped a blank".
- **Reverse scans** for a roll that was scanned tail-first.
- **Auto-pair** resets to positional order.
- Click a **frame number** to see exactly which ExifTool tags will be written.

**Write** copies each paired scan into `<scans>/tagged/` and writes the tags
into the copy. Originals are never touched unless you pass `--in-place`.
Pairing state is remembered in the browser so a reload doesn't lose your work.

Do this **before** importing the scans into Lightroom. Lightroom reads file
metadata at import; if the files are already in a catalog you'll need
Metadata → Read Metadata from File afterwards.

### Options

| flag | effect |
| --- | --- |
| `--tz ZONE` | IANA zone the roll was shot in. Log timestamps are UTC; EXIF wants local time. Defaults to this machine's zone. |
| `--out DIR` | where tagged copies go (default `<scans>/tagged`) |
| `--in-place` | write into the original files instead |
| `--roll NAME` | roll name recorded in UserComment (default: the name stored in the export) |
| `--camera`, `--make` | EXIF Model / Make |
| `--iso N` | film speed → EXIF ISO |
| `--film "Portra 400"` | film stock, recorded in UserComment |
| `--tag=-Artist=Name` | any extra ExifTool assignment applied to every frame (repeatable) |
| `--exiftool PATH` | ExifTool location if it isn't on PATH |
| `--port`, `--no-browser` | server options |

### What gets written

| log field | tags |
| --- | --- |
| time | DateTimeOriginal, CreateDate (local), OffsetTimeOriginal/Digitized, GPSDateStamp/GPSTimeStamp (UTC) |
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
  pyproject.toml
  framelog_tagger/
    cli.py        argument parsing, startup, opens the browser
    log.py        JSON export → Roll/LogEntry (the contract with ../index.html's buildJson)
    scans.py      JPEG listing (natural sort), thumbnails, "already tagged" check
    exif.py       LogEntry → ExifTool arguments; write_tags()
    server.py     FastAPI: /api/session, /api/thumb/{name}, /api/preview/{id}, /api/write
    static/index.html   the pairing UI
  tests/          pytest; ExifTool is stubbed so they run without it
```

Run the tests with `pytest` from this folder.
