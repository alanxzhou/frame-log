# Frame Log

One-tap time + GPS logging for film photography. A single-page PWA: no
accounts, all data lives on your phone in localStorage. It can be hosted
anywhere static (GitHub Pages), or by the small server in `server/`, which
also receives a copy of everything you log and can back it up to S3.

## Files

- `index.html` — the entire app (UI + logic)
- `manifest.json` — makes it installable to the home screen
- `sw.js` — service worker; caches the app so it works fully offline
- `icon-192.png`, `icon-512.png` — home screen icons
- `server/` — optional self-hosted server: serves the app and receives the
  phone's sync, keeps snapshots, backs up to S3; see
  [server/README.md](server/README.md). The app works without it.
- `tagger/` — desktop companion that writes a roll's log into the lab scans'
  EXIF/XMP; see [tagger/README.md](tagger/README.md). Not part of the PWA.

## Install on your phone

**iPhone:** open the URL in Safari → Share button → **Add to Home Screen**.
**Android:** open in Chrome → you'll get an install prompt, or menu (⋮) →
**Add to Home screen / Install app**.

Open it once while online so the service worker caches everything. After
that it works in airplane mode. On first log you'll be asked for location
permission — choose "Allow While Using App" and (iOS) enable Precise Location.

## Updating the app

Edit the files and **bump `CACHE_VERSION` in `sw.js`** (e.g. `framelog-v2`),
then push to GitHub (GitHub Pages) or just save them (self-hosted: the server
serves the repo's files as they are on disk, so `git pull` on the server
machine is the deploy; no restart needed). Installed phones pick up the new
version on the next launch with connectivity (sometimes the launch after
that — service worker updates activate once the old version is fully closed).
The server sends `index.html` and `sw.js` with `Cache-Control: no-cache`, so
the browser always checks for a new service worker.

## Moving from GitHub Pages to your own server

A home-screen app's storage belongs to its address, so the copy installed from
GitHub Pages and one installed from your own hostname are two separate apps
that can't see each other's rolls. To move:

1. Set up the server and its hostname ([server/README.md](server/README.md)).
2. In the **old** app, export every roll you want to keep: **JSON** or
   **Export ZIP** (either works), one per roll. Save them to Files.
3. In Safari, open the new hostname → Share → **Add to Home Screen**. Open it.
4. Tap **Import JSON / ZIP…** and select all the exported files at once.
5. Check the rolls are there, set the sync password (below), then delete the
   old home-screen icon. Keep the exported files until the first sync shows
   **synced**.

Importing the same files twice is harmless (see **Importing** below).

## Using it

- **Rolls.** Switch rolls from the dropdown, start a new one with **+ Roll**,
  rename the current one with **Rename**, or delete it (and all its frames)
  with **✕** after a confirmation. Deleting the last roll leaves a fresh,
  empty "Roll 1".
- **Logging a frame.** Tap the shutter button. The frame is saved right away
  with the time of the press, and its GPS fix is filled in when it arrives
  (the row shows "acquiring fix…" until then), so a slow fix doesn't skew
  your times, pocketing the phone mid-fix doesn't lose the frame, and you can
  log the next frame without waiting. The phone's current time zone is saved
  with the frame, so travel rolls tag correctly later. If GPS fails (deep
  canyon, building), the frame keeps the time and blank coordinates; a fix
  never overwrites coordinates you typed in by hand.
- **Shot details** (lens/aperture/shutter/frame #/notes) are optional and
  apply to the next frame you log. Lens/aperture/shutter are sticky across
  frames; frame # and notes clear after each log. Frame # auto-increments
  from the highest frame number on the roll if left blank.
- **Editing a frame.** Tap the pencil icon on any logged entry to fix a typo,
  correct the time, or fill in/clear coordinates by hand (e.g. if the GPS
  fix was bad or missing). Tap the ✕ to delete it.
- **Reordering frames.** Drag an entry by the ≡ handle on its left to move
  it, e.g. to slot a forgotten frame 5 in between 4 and 6 after logging it
  with a manual frame #. Only the order changes; frame numbers stay as you
  entered them. The handle hides while a frame is open for editing. The
  tagger orders frames by frame number, so the dragged order only decides
  between frames that share a number: if a frame is out of place, fix its
  frame number.
- **Exporting.** Export JSON (everything, including gear fields; this is
  what the tagger reads), GPX (for third-party geotagging tools that align
  waypoints to photos), or both at once as a ZIP.
- **Export when you finish each roll.** iOS can evict web-app storage after
  long periods of disuse; treating export-per-roll as part of the workflow
  makes that a non-issue (sync, below, is a second copy, not a replacement).

## Importing

**Import JSON / ZIP…** (under the export buttons) takes files this app made,
several at once:

- a roll's **JSON** export, from this version or older ones;
- an **Export ZIP** (its `.json` is read, the `.gpx` is ignored). The app's own
  ZIPs are always readable; a ZIP re-compressed on a computer needs a browser
  with `DecompressionStream` (iOS 16.4+);
- a **snapshot** from the sync server's `snapshots/` folder, which restores
  every roll in it.

Nothing already on the phone is ever overwritten:

- A roll that's already here (same frames, under the same roll id or the same
  name) is skipped, so importing a file twice changes nothing.
- A *different* roll with the same name or id comes in as a separate roll
  named `… (imported)`; compare the two and delete the one you don't want.
- Everything else comes in as it was exported, every field of every frame
  included. A fresh install's empty "Roll 1" is replaced by the import.

A summary lists what was imported, what was skipped and why.

Exports now also include `rollId`, the roll's id on the phone (still schema 2;
the tagger ignores it). It's what lets Import recognise a roll it already has
even after a rename; older exports without it are matched by name and frames.

## Sync (self-hosted only)

When the app is served by your own Frame Log server ([server/](server/README.md)),
it can push a copy of the whole log there. Open **Sync** at the bottom of the
page, enter the server's sync password and tap **Save**.

- The phone stays the source of truth and everything works offline. The app
  pushes a few seconds after each change, at launch, when the connection comes
  back and when you leave the app. Failed pushes are retried, backing off up
  to every 10 minutes while the app is open. iOS gives web apps no background
  sync, so changes made offline go up the next time the app is open online.
- The **Sync** line shows *synced* + time, *changes not synced yet*, *wrong
  password*, or *not synced (reason)*; opened, it also shows the server's
  snapshot count and whether its S3 backup is working.
- With no password set, or where there's no server (GitHub Pages), sync stays
  off (*no sync server here*) and nothing else changes.
- The password is stored on the phone separately from the log: it's never in
  an export or in what's synced. **Forget** removes it.
- Sync only ever adds copies on the server. Deleting a roll on the phone
  deletes nothing there, and the server never changes what's on the phone. To
  get a roll back, import its file from the server's `rolls/` or `snapshots/`.

## After the scans come back

Export the roll's JSON from the phone (or, with sync, take it from the
server's `data/rolls/` folder: same format), then on your computer run the
tagger:

```
framelog-tagger
```

It opens a browser page where you pick the folder of JPEGs from the lab and
the roll's export, pairs scans with logged frames (drag to fix mismatches,
one-click shift for a forgotten frame), and writes time, GPS, lens, aperture,
shutter and notes into tagged copies. Each frame is written in the time zone
the phone was in when you pressed the button, so a roll that starts in Tokyo
and ends in Boston comes out right. Details and install steps in
[tagger/README.md](tagger/README.md).

## Data & privacy

Everything you log is stored in the phone browser's `localStorage` for this
app's URL, and nothing you log is ever in this repo. It lives on the phone
in the specific browser (or installed home-screen icon) you logged it in, and
the app at another address can't see it.

Without sync (GitHub Pages, or no password set) nothing is sent anywhere, so
treat export as your backup, not just a convenience. With sync on, the whole
log (every roll, frame, position and note) goes over https to your own server
only, protected by the sync password; it's stored there unencrypted in
`server/data/` (git-ignored) and, if you set it up, in your own S3 bucket.
The app makes no other network requests.

Updating the app (editing these files, pushing to GitHub or pulling on the
server) only changes the code that's served — it never touches your stored
rolls. Your data and the app's code are two independent systems that happen to
share a screen. The server's copy is only ever added to; it never writes back
to the phone.
