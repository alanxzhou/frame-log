# Frame Log

One-tap time + GPS logging for film photography. A single-page PWA: no server,
no accounts, all data stays on your phone in localStorage until you export it.

## Files

- `index.html` — the entire app (UI + logic)
- `manifest.json` — makes it installable to the home screen
- `sw.js` — service worker; caches the app so it works fully offline
- `icon-192.png`, `icon-512.png` — home screen icons

## Deploy to GitHub Pages (free, ~5 minutes)

PWAs require HTTPS (geolocation and service workers won't run otherwise),
which GitHub Pages provides automatically.

1. Create a new repository on github.com, e.g. `frame-log`. Public is fine —
   the app contains no data of yours; your log entries only ever live on
   your phone.
2. Upload these five files to the repo root. Easiest way without git:
   on the repo page, **Add file → Upload files**, drag all five in, commit.
   Or with git:

   ```
   git init
   git add .
   git commit -m "Frame Log PWA"
   git branch -M main
   git remote add origin https://github.com/YOURNAME/frame-log.git
   git push -u origin main
   ```

3. In the repo: **Settings → Pages → Source: Deploy from a branch**,
   pick `main` and `/ (root)`, save.
4. After a minute or two your app is live at
   `https://YOURNAME.github.io/frame-log/`

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

## Field notes

- The timestamp recorded is the moment you press the button, not the moment
  the GPS fix arrives — so a slow fix doesn't skew your times.
- If GPS fails (deep canyon, building), the frame is still saved with the
  time and blank coordinates.
- Lens/aperture/shutter fields are sticky between frames; frame # and notes
  clear each log. Frame # auto-increments if left blank.
- Export GPX (for geotagging tools that align tracks/waypoints to photos)
  or CSV (everything, including gear fields) when you finish a roll.
- **Export when you finish each roll.** iOS can evict web-app storage after
  long periods of disuse; treating export-per-roll as part of the workflow
  makes that a non-issue.

## Tip: sync your camera's clock

Whatever time source you match against later, take one frame of your phone's
clock screen at the start of each roll. When the scans come back you'll know
the exact offset between frame order and log order — useful when you forget
to log a frame or log one twice.
