# Tagger UX backlog

Findings from a walkthrough on 2026-11-20: two dummy rolls driven through the real UI in a browser. ExifTool was stubbed
and the face detector was faked. The demo launcher and data live outside the repo (see "How to test" below).

Status: `todo`, `in progress`, `done`, `won't do`. Update the status and the notes when work lands.

## Not started

| # | Item | Status |
|---|------|--------|
| 12 | Manually test with a real mouse: Ctrl + wheel resize, drag-to-swap, Shift/Ctrl-click, the Auto-pair menu, a click just outside a face box (agents only sent some of these from JavaScript) | todo (needs a person) |
| 1c | Tune the Who's this? grouping thresholds (`faces.GROUP = 0.40`, `pets.GROUP = 0.65` are guesses) on real photos | todo (needs real photos) |

## Round 2 findings (not started, for a separate PR)

From a walkthrough on 2026-11-21 of the finished `tagger-ux` branch: a fresh lab folder through Rename, Load, Who's
this? and Write, at 1366 px, with the same demo harness.

| # | Where | What feels clunky | Suggested fix |
|---|-------|-------------------|---------------|
| R1 | Setup | The "set once" parts (ExifTool at the top, Lightroom library below) show on every roll's setup screen | Collapse each to one line once it's set ("ExifTool 13.0 ✓ · change") |
| R2 | Setup | Make, camera, film and ISO are typed from scratch on every roll | Offer recent combinations in a dropdown; fill ISO from a film name ending in a number (Portra 400 → 400) |
| R3 | Setup | The Rename preview is about 300 px tall for 12 frames and pushes Load roll below the fold | Show the folder row and three files, then "… and 9 more" to expand |
| R4 | Setup | After Rename the panel stays open on a preview where nothing changes, and Load roll is still a scroll away | Collapse it to "renamed to *Lisbon Oct* ✓" and move focus to Load roll (or offer "Rename and load") |
| R5 | Setup | The Rename question repeats what the preview already shows, and renaming is reversible | Drop the question; keep the toast |
| R6 | Setup | The fallback time zone shows even when every frame in the export carries its own zone | Hide it then, or fold it into a "details" line |
| R7 | Who's this? | No key removes a doubtful box: Enter skips it, Delete does nothing | Enter on a doubtful group = Not a face; Delete = × on the highlighted crop |
| R8 | Who's this? | A guess group shows "Confirm Ana (5)" and "Name all 5" side by side; the second does nothing with an empty field | Drop "Name all" on guess groups (typing another name and Enter covers it) |
| R9 | Who's this? | Every guess group needs its own Enter (3 here, many more on a big roll) | "Confirm all guesses" at the top when the guesses look right |
| R10 | Write summary | A scan without a card only links to its cell | Offer New card right there (it starts with the halfway time) |
| R11 | Write summary | Doesn't mention scans with no keywords of their own | Add "8 scans with no keywords" with links |
| R12 | Grid | After Write each cell shows "tagged" and "written" (plus the face count): two badges saying the same thing | Hide "tagged" while "written" shows |
| R13 | Grid | Nothing shows that a scan changed after Write (a keyword added, a face renamed) | Mark cells changed since the last Write; Write says "Write 3 changed scans" |
| R14 | Grid | The spare "empty" cell at the end (with New / Paste) is noise on a roll with an export | Show it only while dragging |
| R15 | Zoom view | Still no keys: reviewing one scan needs the mouse to move between faces and scans | ←/→ previous/next scan, Tab next face, X not a face |
| R16 | Header | "1 scan without log (roll-wide tags only)" is cut off | Shorten to "1 scan without a card"; the tooltip keeps the detail |

## Done

All on branch `tagger-ux`. 196 tests pass.

| # | Item | Notes |
|---|------|-------|
| 1 | "Who's this? (N)" grid | Header button before Write. Groups: gallery guesses per name (one-click Confirm), unnamed faces clustered by similarity (`POST /api/faces/groups`), drawn boxes, then doubtful boxes with "Not faces". Untick a crop to leave it out; × removes a box; click a crop to open it in the zoom view. Enter names a group and moves on. Crops from `GET /api/crop/{name}` |
| 2 | Auto Find faces after Load; restore after reload | Runs in the background with a "finding faces 3/12" chip and Stop. `GET /api/faces/detected` restores cached detections on reload, then finishes the rest |
| 4 | Contact sheet | Wrapping grid; slider and Ctrl/⌘ + wheel, 130–520 px, remembered per browser; 1600 px thumbs for big cells; ↑/↓ move by row; no empty card slots without an export |
| 5 | Face names on keyword lines and in the preview | Names Write will add show in green, guesses in italics with "?". `POST /api/preview` builds tags through the same `plan_scan()` Write uses, so the preview matches Write exactly |
| 10a | Header keys in fixed places | Header is title / status (two fixed lines that truncate, tooltips hold the full text) / one no-wrap row of keys. Who's this? is shown disabled from the start with a min-width for "(99)", Auto-pair is disabled instead of hidden, Write is sized for "Writing…". Button rects sampled every 150 ms through a whole face-finding run at 1366 and 1100 px: identical throughout |
| 3 | Pair by frame number | `pairing.by_frame()` (pure, `tests/test_pairing.py`), sent as `frame_pairing` with the session. Used when every scan name ends in a number, numbers are unique, and ≥60% of min(scans, frames) match at the best constant offset (0 wins ties, then the smallest), so lab names (`004123450005`) and labs numbering from 00 work. Cards with no scan keep a cell at their number; duplicate frame #s go last. Stats say "by frame #" / "in order"; Auto-pair is a menu with both. A saved pairing still wins on reload; its mode is saved under `#mode`. Tested on raw lab names, names from the app's Rename, and a roll numbered from 00 |
| 6a | Write learns only confirmed faces | Each face Write sends carries `confirmed` (`!!name && !auto`); the server writes every name but learns only confirmed ones (missing flag = guess). Toast: "N faces added to the gallery · M guessed names written but not learned" |
| 6b | In-app Write summary | Replaces `confirm()`: destination, roll keywords, named faces, then "May want a look first": guessed names, unnamed faces, doubtful boxes (scan links open that face; "Who's this? →"), scans without a card (link selects the cell), scans not looked at for faces. Write anyway / Review first; Enter writes only when nothing needs a look. Writes the pairs snapshotted when it opened |
| 10b | Click near a face box selects it | `.fbox::before` with `inset: -10px`. Real clicks 5 px outside select; 45 px outside still zoom |
| 10c | Ctrl/⌘+Z undoes pairing too | One stack, in order: keywords, swap, shift, reverse, Auto-pair, New/Paste card (undo deletes the card on the server). Toast names the step undone |
| 10d | "tagged" badge is neutral | Grey light, with a tooltip saying what it means; amber stays for problems |
| 10e | Lens apart from exposure | Lens truncates on the left, aperture · shutter sits at the right in mono |
| 10f | New roll starts at the top | `showStrip` resets the grid's scroll (it survived `hidden` before); `render()` still keeps the place |
| 13 | Face names in the file are faces, not keywords | Load reads each file's region names (`xmp_region_names`) and keeps them out of the scan's own keywords; they show green. Find faces matches the file's regions to detections (named, confirmed, *named in the file*); a named region with no detection is its own box. Write rewrites a region you cleared (unnamed box) or removed, and keeps every name left on a region in the keywords |
| 14 | "tagged" right after Write | Write's result carries `tagged` and `region_names`; only an in-place write marks the scan tagged |
| 11 | Tests can't write the real config | `tests/conftest.py` points `FRAMELOG_TAGGER_CONFIG` at a scratch folder for the whole run; the faces fixture waits for its library read. Confirmed: a run with no override left the real `faces.json` untouched |
| 16 | Lab numbered last frame first | `by_frame(..., direction)`: counting down is used when it matches more; often both match equally, so Auto-pair offers "By frame number, counting down" |
| 18 | No native confirm() | `ask()` in-app dialog for Rename, Change roll, Delete card; Auto-pair doesn't ask (Ctrl/⌘-Z undoes it) |
| 7a–7d | Setup screen | `POST /api/setup/look`: export found in or next to the scans folder (one is filled in, several listed); last roll's export cleared when the scans folder changes; Rename sits under the scans folder, opens for a folder not renamed, roll name pre-filled from the export; UserComment's roll name is the renamed folder's first |
| 8a, 8b | Keyword menu | Roll keywords out of the menu; prefix, then word-start, then substring; "add new" first when nothing starts with what you typed. Card notes feed suggestions (a short note whole, capitalised words of a longer one) |
| 9a–9c | Card editor | New card: time halfway between neighbours; "same place as #N" for a card with no GPS. Several selected scans: the pencil edits all their cards (shared fields shown, differing ones "varies"). Ctrl/⌘-Enter saves |
| 15 | Undo covers cards and faces | Card edits (one or several), revert, delete (re-made on undo), naming/removing/"nobody I know" faces; a Who's this? group is one step |
| 4b | 170 px default cells | 7 across at 1366 px |
| 1b | Nobody I know | Who's this? action for people and drawn groups: faces stay unnamed, stop counting (badge, Who's this?, Write summary); remembered per roll |
| 2b | No automatic model download; Stop stays stopped | `faces.Engine.model_ready()`; without the model the header says the first Find faces downloads it. Stop is remembered per roll until Find faces |
| 17 | Roll tags out of the header | Make/Camera/Film/ISO shown with the roll keywords in the keyword bar ("every scan"), with what isn't set |

## How to test

The walkthrough used a launcher that runs the real app with an isolated config (`FRAMELOG_TAGGER_CONFIG`), a fake face
detector and a stubbed ExifTool that logs Write's arguments to `write_log.txt`. It was in the session scratchpad:
`launch.py` (app) and `make_roll.py` (12 scans with drawn faces + an 11-frame export, frame 5 missing). The polish pass
added two arguments to `make_roll.py`, `<folder name>` and `<numbering offset>` (e.g. `-1` for a lab that numbers from
00), and the launcher looks faces up by file name, then by number. Recreate them from the tests' fixtures
(`tests/test_faces_server.py`) if the scratchpad is gone.
