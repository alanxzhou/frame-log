# Tagger UX backlog

Findings from a walkthrough on 2026-11-20: two dummy rolls driven through the real UI in a browser. ExifTool was stubbed
and the face detector was faked. The demo launcher and data live outside the repo (see "How to test" below).

Status: `todo`, `in progress`, `done`, `won't do`. Update the status and the notes when work lands.

## Not started

| # | Item | Status |
|---|------|--------|
| 4b | Default cell size ~170 px so a 36-scan roll fits 7 across at 1366 wide (the scrollbar takes the 7th column at 180) | todo |
| 1b | "Who's this?": add a "nobody I know" action so unnamed strangers stop counting; tune the grouping thresholds (`faces.GROUP = 0.40`, `pets.GROUP = 0.65` are guesses) on real photos | todo |
| 2b | Auto Find faces loads (and on first run downloads) the face model on every Load roll; check that's acceptable on a fresh install. Stop isn't remembered across a reload | todo |
| 12 | Manually test with a real mouse: Ctrl + wheel resize, drag-to-swap, Shift/Ctrl-click, the Auto-pair menu, a click just outside a face box (agents only sent some of these from JavaScript) | todo |
| 7a | Clear the log export on Change roll, or warn when the export's roll name doesn't match the scans folder | todo |
| 7b | Move Rename next to the scans folder; pre-fill the roll name from the export | todo |
| 7c | One roll name: UserComment uses the export's name while the folder uses the rename | todo |
| 7d | Find a `.json` export next to the scans folder automatically | todo |
| 8a | Keyword menu: drop keywords the whole selection already has (roll keywords); rank prefix matches first | todo |
| 8b | Suggest keywords from card notes | todo |
| 9a | New card: default time halfway between neighbours; offer "same place as #N" for GPS | todo |
| 9b | Edit a range of cards at once (lens change mid-roll) | todo |
| 9c | Ctrl+Enter saves the card editor from the notes field | todo |
| 11 | Tests leak into the real config: `%APPDATA%\framelog-tagger\faces.json` held a pytest temp path. Suspect: the background library read in `test_the_pet_names_are_saved_with_the_library` outlives the monkeypatched config path. Unverified (the 2026-11-20 polish pass ran pytest with `FRAMELOG_TAGGER_CONFIG` pointed at a scratch folder to stay clear of it) | todo |
| 13 | Face names come back as the scan's own keywords. After an in-place Write, a reload of a roll whose keywords were never edited in the browser reads the keywords from the files, and those now include the face names Write added, guesses too. They show as plain keywords (not green), so clearing a wrong guess later leaves the name on the scan. Fix: leave out names that match a face region in the file, or always persist the keyword state after Write | todo |
| 14 | After Write the grid doesn't show "tagged" on the scans it just wrote until a reload (the page's `S.scans[].tagged` isn't updated from the results) | todo |
| 15 | Undo (10c) covers keywords, swap/shift/reverse/Auto-pair and New/Paste cards. Not covered: deleting or editing a card, naming or removing faces | todo |
| 16 | Frame-number pairing falls back to "in order" for a lab that numbered the roll last frame first; detect a reversed constant (frame = k − N) too | todo |
| 17 | At 1100 px wide the header status is cut short (the roll tags Make/Camera/Film mostly hidden; full text in tooltips). Consider moving the roll tags to the keyword panel's "every scan" area, which already shows them | todo |
| 18 | The Auto-pair menu, Rename and Change roll still use the native `confirm()`; with undo for pairing, Auto-pair's could go | todo |

## Done

All on branch `tagger-ux` (not pushed, not merged to main). 181 tests pass.

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

## How to test

The walkthrough used a launcher that runs the real app with an isolated config (`FRAMELOG_TAGGER_CONFIG`), a fake face
detector and a stubbed ExifTool that logs Write's arguments to `write_log.txt`. It was in the session scratchpad:
`launch.py` (app) and `make_roll.py` (12 scans with drawn faces + an 11-frame export, frame 5 missing). The polish pass
added two arguments to `make_roll.py`, `<folder name>` and `<numbering offset>` (e.g. `-1` for a lab that numbers from
00), and the launcher looks faces up by file name, then by number. Recreate them from the tests' fixtures
(`tests/test_faces_server.py`) if the scratchpad is gone.
