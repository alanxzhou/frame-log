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

The setup screen looks at the folders as you fill them in:

- **The export is found for you** when it sits in the scans folder or next to
  it (in the folder that holds it). Under the field it says which roll it is,
  how many frames and when they were shot. If there are several, pick one from
  the list.
- **Last roll's export isn't carried over.** Point the scans folder at a
  different roll and the export remembered from the last one is cleared (or
  replaced by the one found next to the new folder), so this roll's scans are
  never paired with last roll's log.
- **One roll name.** UserComment records the roll's name: the name you renamed
  the folder to here, else the one in the export, else the folder's.

The roll opens as a contact sheet, like Lightroom's Grid view: a grid of cells
that wraps to the window's width and scrolls down. Each cell is a scan (its
whole picture, fitted in the cell) with its log card under it in short form:
frame #, time, zone, lens and exposure (the aperture and shutter at the right,
in their own type, so a lens name like *50mm f/1.8* isn't read as the
aperture), note, GPS and where the card came from. A grey *tagged* on a picture
means the file already has a date (DateTimeOriginal): written before, here or by
another app. Change the thumbnail size with the slider in the header, or Ctrl +
scroll wheel over the grid (⌘ + wheel, or a pinch, on a Mac); the size is
remembered on this computer. A new roll opens at the top of the grid.

The header keeps its keys in fixed places; the status beside them (counts,
*finding faces*) is cut short in a narrow window, and its tooltip has it all.

Without an export the cells show only the scans, and **Write** gives every
scan the roll-wide tags (camera, make, ISO, roll and film).

### Renaming the lab's folder

Labs deliver something like `12345678/123456780001.jpg`. **Rename scans**,
right under the scans folder, turns it into `<roll name>/` and its images into
`<roll name>-01.jpg`, `-02`, … in natural-sort order, in place. It opens by
itself for a folder that isn't renamed yet, with the roll's name from the
export filled in.

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

Scans and log cards start paired **by frame number** when the scans' names
carry it: every name ends in a number (`2026-10 Lisbon-05.jpg` after Rename,
or the lab's own `004123450005.jpg`) and most of those numbers match a frame
in the export. Scan 05 then gets the card for frame #5, so a frame you forgot
to log leaves just its own scan without a card instead of shifting every scan
after it one card off. A card with no scan gets a cell of its own where its
number falls (a second card with the same frame #, at the end), so nothing is
dropped. Labs that number from 00, or off by any constant, are allowed for:
the offset that lines up the most numbers is used. So is a lab that numbered
the roll last frame first, when its numbers fit better counting down; often
they fit equally well both ways, so pick **By frame number, counting down**
under Auto-pair for that roll.

Otherwise (names without numbers, or numbers that don't fit the log) they
start paired **in order**: first scan to first card, and so on. The stats in
the header say which (*paired by frame #* or *in order*; hover it for how).
Fix the exceptions:

- **Drag** a scan onto another cell to swap the two scans, or a log card onto
  another cell to swap the two cards.
- **▶ / ◀** shift a scan or a card and everything after it one cell on or
  back. They show when you point at a cell: on the picture for the scan, at
  the foot of the card for the card. This is the one-click fix for "I forgot
  to log a frame" and "the lab skipped a blank".
- **Reverse scans** for a roll that was scanned tail-first.
- **Auto-pair ▾** pairs everything again, **by frame number**, **by frame
  number, counting down** (when the names allow it) or **in order**, your
  choice. It replaces your swaps and shifts; ⌘/Ctrl-Z takes it back.
- **⌘/Ctrl-Z** undoes pairing changes (swap, shift, reverse, Auto-pair, a card
  made with New or Paste), card edits (one card or several, a revert, a
  delete), naming or removing faces (a group named in Who's this? is one step)
  and keyword changes, one step at a time, latest first.
- Click a **frame number** to see exactly which ExifTool tags will be written to the scan paired with it:
  the card's tags, the keywords (named faces included) and the face regions, worked out by the same code as Write,
  merged with the regions already in the file. A line starting with `#` says when regions would be left out.

A scan left in a slot with no card is still written, just without the card's
tags (see below). Its light (next to the cell number) and the picture's
outline are amber, and the stats count it as a scan without log, since that's
often a sign of a mispairing. A green light means the scan is written with its
card.

### Editing and adding cards

When there's no export, or the export is missing frames, make the cards here:

- **New** in a cell with no card makes a card from scratch. The frame number,
  zone and gear start from the card before it, as on the phone; the time
  starts halfway between the cards either side (a frame you forgot to log was
  shot between them). With an
  export, a cell with no card has a *no card* row with New and Paste; without
  one, they show on the picture when you point at it.
- The **copy** key on a card, then **Paste** in a cell with no card, adds a
  duplicate to adjust. **▶** opens an empty cell where you need one.
- The **pencil** key (or a double-click) edits any card: frame #, time (local,
  in the card's zone), zone, coordinates, lens, aperture, shutter and notes.
  Leave the time blank to leave a scan's date tags alone. A card with no GPS
  offers **same place as #N** for the cards either side. ⌘/Ctrl-Enter saves.
- **Several cards at once**: select their scans (shift-click a range), then
  the pencil on one of their cards edits them all: zone, coordinates, lens,
  aperture, shutter and notes. A field they share shows its value; one that
  differs shows *varies* and is left as it is unless you type in it. Frame #
  and time stay per card. Handy for a lens changed mid-roll.

The chip at the bottom of each card says where it stands: **original** (as read
from the export), **edited** (changed here, with the old value shown under
each changed field in the editor and a *Revert to original* button), or
**new** / **copy of #N** (made here; these can be deleted). Edits live in the
running tagger only. The export file is never changed, and **Change roll** or
restarting the tagger drops them.

A scan in a slot with no card, with or without an export, still gets the
roll-wide tags and its own keywords and named faces, but no date, GPS or gear
tags.

**Write** writes the tags into every scan in place. Fill in *write to*
on the setup screen to leave the originals alone and write tagged copies into
that folder instead.

Before it writes, Write shows a summary: where it writes (in place or copies),
the keywords every scan gets, and what may want a look first, each with a link
that goes there: names the gallery guessed that nobody confirmed, faces with no
name and boxes the detector doubts (the scan numbers open that face; *Who's
this?* opens them all), scans without a log card (the number selects the
cell), and scans not looked at for faces yet. **Write anyway** writes;
**Review first** opens Who's this? when faces need a look, else just closes.
Enter means Write only when nothing needs a look. It writes the scans as they
were when the summary opened.
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
  They show at the right of the keyword panel, under *every scan*, with the
  ISO and a note of what isn't set (the header has no room for them).
- **Per scan**: click a scan and start typing. Enter (or Tab) takes the
  highlighted suggestion; a comma adds exactly what you typed, so
  `beach, Mom, ` adds two. With the box empty, ← / → move to the next scan and
  ↑ / ↓ to the one above or below, so you can keep going (the arrows do the
  same with the box not focused, and Enter goes back to the box). Backspace
  removes the scan's last keyword.
- **Several at once**: shift-click a range, ⌘/Ctrl-click to add one, ⌘/Ctrl-A
  for all. The box then shows each keyword as on all of them, or as *2/5* (on
  some): click it to give it to all, × to take it off all.
- **Suggestions** follow the selected scans: first who the face gallery thinks
  is in them (faces you haven't named yet, after **Find faces**), then words
  from their cards' notes (a short note whole, like *tram 28*, and the
  capitalised words in a longer one, mostly names and places), then the
  keywords on the three scans either side, nearest first, then the rest of this
  roll's keywords and your library, most recent first. Hover one to see why it's
  there.
- **While you type**, the menu lists keywords that start with what you typed,
  then ones with a word that does (*28* finds *tram 28*), then ones that merely
  contain it. When nothing starts with it, *add new keyword* comes first, so
  Enter adds your word. The roll's keywords (camera, film) aren't offered:
  every scan gets them anyway. Press **1–9** (with the box not focused) to toggle one on the
  selected scans. ⌘/Ctrl-Z undoes keyword changes (and pairing changes, in the
  order you made them); Esc clears the selection.
- Keywords match case-insensitively; the spelling you used first is kept.

A scan's keyword line shows its own keywords, then in green the names of the faces in it, which Write adds as
keywords too (see [Faces](#faces)); a name the gallery guessed and you haven't confirmed is in italics with a `?`.

Each scan's keywords are remembered in the browser along with the pairing.
Keywords already in the files (an earlier Write, another app) are read at
**Load roll**, so re-writing a roll keeps them. Write replaces a scan's
keywords with what the grid shows, roll's first, on every scan, including one
with no log card.

The **library** is every keyword you've added or written, stored on this
computer next to the remembered setup values (`keywords.json` in
`~/.config/framelog-tagger/`, `%APPDATA%\framelog-tagger\` on Windows).
The keywords on your Lightroom photos join it too (see below). **Library…** lists them with
counts. Removing one stops suggesting it, even if it turns up on a photo again; use it on a scan
and it's back.

### Your Lightroom library

On the setup screen, under **Lightroom library**, give the folders of photos you've tagged in Lightroom
(subfolders are included) and click **Save and read**. The folders are remembered on this computer
(`faces.json`, next to `keywords.json`), and every time the tagger starts it reads them again in the
background, so it follows what you tag in Lightroom with nothing to run. One pass over the files gives two
things, shown as two parts with their own progress:

- **Keywords**: every keyword on those photos, and every name tagged on a face, joins the keyword library and
  is suggested while you keyword scans. They're ready as soon as the files have been read (a minute or two
  for a big library), long before the faces are learned. The part says how many came from how many photos.
- **Faces & pets**: the named faces are learned from, so they can be recognised in new scans (see below). This
  takes longer the first time; after that only new photos are learned from.

You can load a roll and keep working while it runs; a note in the header shows its progress. Lightroom has to
have saved the tags into the files first (select all, Metadata → Save Metadata to Files, or Ctrl/⌘-S).
Keywords are read even if the face tools didn't load.

### Faces

Finds the faces in your scans, suggests who they are, and writes the names as face regions and
keywords that Lightroom reads at import. Nothing is trained: a detector finds faces, a pretrained
network turns each into a fingerprint, and a name is suggested by comparing it with faces you've
already named. Every name you confirm makes the next suggestion better. How well it works, and how
that was measured: [docs/face-tagging-experiments.md](docs/face-tagging-experiments.md).

**Teach it who people are, once.** Set up your [Lightroom library](#your-lightroom-library) and, under
**pets**, say which of the tagged names are animals. It learns from every named face there, and names
improve as it goes.

It reads each file's face regions with ExifTool, finds the face under every named box and keeps its
fingerprint. About 0.4 s per photo on a CPU, so a first pass over ten thousand photos is an hour or so; after
that only new photos are read, and the folder scan itself takes a minute or two.

Pets are found and learned by a separate model (see **Pets** below), in a separate gallery (`pets.npz`):
the face model only knows people. Marking a name as a pet removes it from the people gallery if it was
learned there.

The same thing from a terminal: `framelog-tagger faces import [FOLDER ...] [--pets NAME ...]`. It also
remembers the folder and the names, so with no arguments it repeats the last one. `--again` reads every
photo again (a name changed in Lightroom after it was learned isn't picked up otherwise).
`framelog-tagger faces people` lists who's in the galleries; `faces forget NAME` removes someone.

**Use it.** After **Load roll** the faces are found on their own, one scan at a time in the background (about half
a second each; the first one loads the model). The header shows *finding faces 3/12* with a **Stop** button, and you
can pair and keyword while it runs. **Find faces** runs it again over every scan (the gallery may know more people by
then). What was found is kept by the running tagger, so a page reload restores it, with your names, removed boxes and
moved or drawn boxes, and only looks at the scans it hadn't reached. Each card shows how many faces are named.

Two exceptions. Until the face model is on this computer, nothing starts on its own: the header says so, and the first
**Find faces** downloads it (about 0.6 GB, once). And a run you **Stop** stays stopped for that roll, reloads
included, until you press **Find faces**.

Because this runs on every roll, the gallery's guesses (dashed names) are written by **Write** unless you confirm,
change or clear them, though only the names you confirm teach the gallery. **Who's this?** is the quick way to go over
them.

**Who's this? (7)** in the header counts the faces with no name, or only the gallery's guess. It opens every one of
them as a crop, grouped:

- the gallery's guesses, one group per name, each with **Confirm** (or type another name and press Enter).
  **Confirm all guesses** at the top confirms every one at once, when they look right;
- faces with no name, grouped by who they look like across the roll (their fingerprints are clustered, faces and
  pets apart), so a friend or stranger on six frames is named once. The grouping is untuned and errs towards
  splitting one person into two groups rather than joining two people;
- boxes you drew, one each;
- last, the boxes the detector isn't sure are faces, outlined in amber, with **Not faces** to remove them all at once
  (Enter, or Delete, with the name field empty does the same).

Each group has the gallery's suggestions as buttons and a name field that completes from the gallery and your keyword
library. Untick a crop that doesn't belong before naming the group; **×** on a crop removes that box (not a face).
**Nobody I know** leaves the ticked faces unnamed (a passer-by, a crowd): they stop being counted here, in the badges
and in Write's summary, and aren't written. That's remembered for the roll. Click
a crop to open its scan with that face selected. **Enter** names the highlighted group and moves to the next (on a
guessed group, an empty field confirms the guess; on others it just moves on); Esc closes. Names chosen here are the
same as names chosen in the zoom view.

Double-click a scan to review it:

- Boxes: **green** = named, **dashed green** = suggested by the gallery but not yet confirmed,
  **amber** = nobody yet, **dim** = the detector isn't sure it's a face.
- Click a box (or just outside it: a click elsewhere on the picture zooms to 100%). The bar underneath offers the closest people with a score (about 40 and up is a likely
  match), a name field that completes from everyone in the gallery, **Confirm** for a dashed name
  and **Clear**. Enter confirms and moves to the next face.
- A name is only filled in on its own when it's a close match **and** the detector is confident (0.6+).
  Doubtful boxes, which are mostly hair, ears and blurs, get suggestions but never a name you didn't pick.
- Names you choose are remembered for the roll in the browser. A reload restores the faces too; restarting the
  tagger or loading the roll again finds them again, and your names, removals and boxes are put back on them.

**Pets.** The same Find faces also finds pets, using a head detector trained on the pet boxes you drew in Lightroom, so
its boxes look like yours. Pets are named from a gallery of their own and appear like any other face (box, suggestions,
confirm). A name is only filled in on its own when the detector is 0.5+ sure and the match is 0.70+, which for Kepler
was right every time in testing and named about four in five of the heads it found. Pets need three confirmed photos
before they are suggested, so only Kepler, with hundreds of tagged photos, works well to begin with; the others get
better as you tag more of them. Very small heads (under about 40 px) are often missed. Details and how to retrain the
detector: [docs/pet-tagging-experiments.md](docs/pet-tagging-experiments.md).

**Write** stores every named face as a face region (`XMP-mwg-rs:RegionInfo`) and as a keyword
(`XMP-dc:Subject`), on a scan with no log card too. Lightroom wants both: the region puts the name on the face, and the keyword makes it
searchable. Things to know:

- Regions already in the file are kept. Find faces shows the named ones: a face the file already names
  (Lightroom's region, or an earlier Write's) comes up with that name, confirmed, and marked *named in the file*
  in the zoom view; a named region the detector found no face on shows as a box of its own. Write changes only
  the boxes you touched: a face you name replaces the old box on that same face, a name you clear leaves an
  unnamed box, a box you remove (not a face) is taken out, and every other box stays as it was. A scan with no
  changed face isn't touched, nor one Write reached before its faces were looked at (a run you stopped, say).
- The names on a file's regions are its face names, not keywords you typed. At **Load roll** they show in green
  on the scan's keyword line, with the face names, and Write keeps them in the keywords for as long as the region
  keeps its name. Clear the name on the face and the keyword goes too.
- Scans that are rotated in the file (EXIF orientation other than 1) get the keywords but no regions.
  Lightroom stores those regions in the file's unrotated pixels, and mapping back into that frame isn't done yet.
- Only faces whose name is yours are added to the gallery when you Write: ones you confirmed, typed, or picked
  (in the zoom view or Who's this?). A guess you left dashed is written into the file like any name, but the
  gallery doesn't learn from it, so a wrong guess can't make itself more likely next time; the Write message
  counts those. (A box you drew is written but has no fingerprint to learn from.) Naming the same face again
  corrects it rather than adding a second copy.
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
    pairing.py    pairing scans with cards by the frame number at the end of their names
    exif.py       LogEntry → ExifTool arguments (per-frame zone); write_tags()
    dialogs.py    native folder/file pickers via a Tk helper process
    config.py     remembered setup-screen values
    keywords.py   roll keywords, reading keywords from a scan, the keyword library
    regions.py    face regions: Lightroom's coordinate convention, matching boxes, writing/reading XMP regions (no model needed)
    faces.py      detector + recognition model, the gallery of named faces, learning from your Lightroom folder
    server.py     FastAPI: /api/session, /api/pick, /api/load, /api/thumb, /api/crop, /api/preview, /api/entries, /api/keywords, /api/faces/detect, /api/faces/detected, /api/faces/groups, /api/write, /api/rename/*
    static/index.html   setup screen + the pairing UI
  pets.py       pet head detector + fingerprints (ONNX, models/pet_head.onnx), the same flow as faces
  models/         pet_head.onnx, the detector trained on the pet boxes tagged in Lightroom
  experiments/    the scripts behind the docs below (standalone: `uv run experiments/face_eval.py --help`)
  docs/           face-tagging-experiments.md, pet-tagging-experiments.md
  tests/          pytest; ExifTool and the face model are stubbed so they run without them
```

Run the tests with `uv run pytest` from this folder (the `dev` dependency
group is installed by default).
