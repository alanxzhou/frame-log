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
| 12 | Manually test with a real mouse: Ctrl + wheel resize, drag-to-swap, Shift/Ctrl-click (agents only sent these from JavaScript) | todo |
| 3 | Pair by frame number when scans are named `<roll>-NN` and the export has frame numbers; positional pairing only as fallback | todo |
| 6a | Write learns only confirmed faces. Today it also learns names the gallery filled in that nobody confirmed (contradicts the README) | todo |
| 6b | Replace the native Write confirm with an in-app summary: unnamed faces, unconfirmed names, scans without a card, each with a jump link | todo |
| 7a | Clear the log export on Change roll, or warn when the export's roll name doesn't match the scans folder | todo |
| 7b | Move Rename next to the scans folder; pre-fill the roll name from the export | todo |
| 7c | One roll name: UserComment uses the export's name while the folder uses the rename | todo |
| 7d | Find a `.json` export next to the scans folder automatically | todo |
| 8a | Keyword menu: drop keywords the whole selection already has (roll keywords); rank prefix matches first | todo |
| 8b | Suggest keywords from card notes | todo |
| 9a | New card: default time halfway between neighbours; offer "same place as #N" for GPS | todo |
| 9b | Edit a range of cards at once (lens change mid-roll) | todo |
| 9c | Ctrl+Enter saves the card editor from the notes field | todo |
| 10a | Header buttons jump between rows as the status text changes length; give them fixed positions. Still happens on the grid: when face finding ended, a click meant for "Who's this?" hit Write. Fix this early | todo |
| 10b | A click just outside a face box zooms to 100% instead of selecting the box | todo |
| 10c | Ctrl+Z undoes pairing changes too, not only keywords | todo |
| 10d | "tagged" badge after a successful Write is amber; make it read as success | todo |
| 10e | Lens line "50mm f/1.8 · f/2.8 · 1/…": separate the lens name from the aperture | todo |
| 10f | Reset the strip's scroll position when a new roll loads | todo |
| 11 | Tests leak into the real config: `%APPDATA%\framelog-tagger\faces.json` held a pytest temp path. Suspect: the background library read in `test_the_pet_names_are_saved_with_the_library` outlives the monkeypatched config path. Unverified | todo |

## Done

All on branch `tagger-ux` (not pushed, not merged to main). 168 tests pass after the merge.

| # | Item | Notes |
|---|------|-------|
| 1 | "Who's this? (N)" grid | Header button before Write. Groups: gallery guesses per name (one-click Confirm), unnamed faces clustered by similarity (`POST /api/faces/groups`), drawn boxes, then doubtful boxes with "Not faces". Untick a crop to leave it out; × removes a box; click a crop to open it in the zoom view. Enter names a group and moves on. Crops from `GET /api/crop/{name}` |
| 2 | Auto Find faces after Load; restore after reload | Runs in the background with a "finding faces 3/12" chip and Stop. `GET /api/faces/detected` restores cached detections on reload, then finishes the rest |
| 4 | Contact sheet | Wrapping grid; slider and Ctrl/⌘ + wheel, 130–520 px, remembered per browser; 1600 px thumbs for big cells; ↑/↓ move by row; no empty card slots without an export |
| 5 | Face names on keyword lines and in the preview | Names Write will add show in green, guesses in italics with "?". `POST /api/preview` builds tags through the same `plan_scan()` Write uses, so the preview matches Write exactly |

## How to test

The walkthrough used a launcher that runs the real app with an isolated config (`FRAMELOG_TAGGER_CONFIG`), a fake face
detector and a stubbed ExifTool that logs Write's arguments to `write_log.txt`. It was in the session scratchpad:
`launch.py` (app) and `make_roll.py` (12 scans with drawn faces + an 11-frame export). Recreate them from the tests'
fixtures (`tests/test_faces_server.py`) if the scratchpad is gone.
