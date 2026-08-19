# Frame Log

One-tap time + GPS logging for film photography. A single-page PWA: no server,
no accounts, all data stays on your phone in localStorage until you export it.

## Files

- `index.html` — the entire app (UI + logic)
- `manifest.json` — makes it installable to the home screen
- `sw.js` — service worker; caches the app so it works fully offline
- `icon-192.png`, `icon-512.png` — home screen icons

## Install on your phone

**iPhone:** open the URL in Safari → Share button → **Add to Home Screen**.
**Android:** open in Chrome → you'll get an install prompt, or menu (⋮) →
**Add to Home screen / Install app**.

Open it once while online so the service worker caches everything. After
that it works in airplane mode. On first log you'll be asked for location
permission — choose "Allow While Using App" and (iOS) enable Precise Location.

## Updating the app

Edit the files, push to GitHub, and **bump `CACHE_VERSION` in `sw.js`**
(e.g. `framelog-v2`). Installed phones pick up the new version on the next
launch with connectivity (sometimes the launch after that — service worker
updates activate once the old version is fully closed).

## Using it

- **Rolls.** Switch rolls from the dropdown, start a new one with **+ Roll**,
  or rename the current one with **Rename**.
- **Logging a frame.** Tap the shutter button. It captures the time
  immediately and the GPS fix as soon as it arrives — the timestamp is the
  moment you pressed, not the moment the fix landed, so a slow fix doesn't
  skew your times. If GPS fails (deep canyon, building), the frame is still
  saved with the time and blank coordinates.
- **Shot details** (lens/aperture/shutter/frame #/notes) are optional and
  apply to the next frame you log. Lens/aperture/shutter are sticky across
  frames; frame # and notes clear after each log. Frame # auto-increments
  if left blank.
- **Editing a frame.** Tap the pencil icon on any logged entry to fix a typo,
  correct the time, or fill in/clear coordinates by hand (e.g. if the GPS
  fix was bad or missing). Tap the ✕ to delete it.
- **Exporting.** Export GPX (for geotagging tools that align tracks/
  waypoints to photos), CSV (everything, including gear fields), or both
  at once as a ZIP.
- **Export when you finish each roll.** iOS can evict web-app storage after
  long periods of disuse; treating export-per-roll as part of the workflow
  makes that a non-issue.

## Data & privacy

Everything you log is stored in the phone browser's `localStorage` for this
app's URL — nothing is sent anywhere, and nothing you log is ever in this
repo. That also means it lives in exactly one place: the specific browser
(or installed home-screen icon) you logged it in. It isn't synced across
devices or browsers, so treat export as your backup, not just a convenience.

Updating the app (editing these files, pushing to GitHub) only changes the
code GitHub Pages serves — it never touches your stored rolls. Your data and
the app's code are two independent systems that happen to share a screen.
