# Frame Log sync server

Serves the Frame Log PWA from this computer and keeps a copy of everything the phone logs,
optionally backed up to S3. It runs behind the Cloudflare Tunnel already on this machine, so the
phone reaches it at a hostname on your domain, over https, from anywhere.

- `GET /` and the app's files: the PWA itself (`index.html`, `sw.js`, `manifest.json`, the two
  icons from the repo root, and nothing else). No login: the app shell holds no data.
- `POST /api/sync`: the phone pushes its whole log (the same JSON it keeps in `localStorage`).
- `GET /api/status`: last sync, number of snapshots, S3 state; the app shows it under **Sync**.

Both API calls need the sync password, sent as `Authorization: Bearer <password>`.

The phone stays the source of truth. The app works offline as before and pushes when it can;
this server only ever adds copies, it never sends anything back to the phone.

## What ends up on disk

Under `DATA_DIR` (default `server/data/`, git-ignored):

```
snapshots/2026-11/20261113T180712.123456Z-<hash>.json   every distinct state the phone pushed
rolls/<roll name>--<roll id>.json                       latest version of each roll
state.json                                              last sync time, latest snapshot's hash
server.log                                              when run by start-server.ps1
```

- **Snapshots are append-only.** One is written only when the rolls differ from the previous
  snapshot (a hash compare; switching the current roll on the phone isn't a change). Nothing in
  `snapshots/` is ever overwritten or deleted. Each is `{"app": "framelog", "kind": "snapshot",
  "received", "hash", "state"}`; the app's Import accepts one to restore every roll in it.
- **`rolls/`** holds each roll in exactly the app's **Export JSON** format (plus `rollId`), so
  `framelog-tagger` reads it directly. A roll deleted on the phone keeps its file here. Renaming a
  roll on the phone moves its file to the new name (the old version stays in `snapshots/`).
- All writes are atomic (temp file, then rename).
- Size: a snapshot is the whole log, so expect roughly (log size) x (number of changes). A few
  hundred KB x a few thousand changes a year is a few hundred MB. Nothing prunes old snapshots;
  an S3 lifecycle rule can move old ones to a cheaper storage class if it ever matters.

## Install and run

Requires [uv](https://docs.astral.sh/uv/); it fetches Python 3.11+ if needed.

```
cd server
uv sync
copy .env.example .env      # then edit .env: at least SYNC_PASSWORD
uv run framelog-sync
```

Generate the password with `uv run python -c "import secrets; print(secrets.token_urlsafe(24))"`.
The server refuses to start without one, or with a weak one (under 16 characters, or fewer than
8 different characters).

It listens on `http://127.0.0.1:8766` (`PORT` in `.env`) and only on 127.0.0.1, so nothing on
your network can reach it except through the tunnel. Open that address on this computer to check
the app loads.

Every setting is documented in [.env.example](.env.example).

Tests: `uv run pytest` (S3 is replaced by an in-memory fake; nothing touches the network).

## Put it on your domain (Cloudflare Tunnel)

cloudflared already runs here as a service for another project. Add a second hostname to the same
tunnel rather than creating a new tunnel. Pick a hostname, e.g. `frames.example.com`
(placeholder: use your own domain).

**If the tunnel is managed in the Cloudflare dashboard** (a "remotely-managed" tunnel, installed
with `cloudflared service install <token>`): Zero Trust → Networks → Tunnels → your tunnel →
**Public Hostname** → **Add a public hostname**. Subdomain `frames`, domain `example.com`,
service type `HTTP`, URL `127.0.0.1:8766`. Save. The DNS record is created for you, and the
running service picks it up without a restart.

**If it's configured by a local `config.yml`** (usually `%USERPROFILE%\.cloudflared\config.yml`
or `C:\Windows\System32\config\systemprofile\.cloudflared\config.yml` for the service): add a rule
above the final catch-all:

```yaml
ingress:
  - hostname: frames.example.com
    service: http://127.0.0.1:8766
  # ... the existing project's rule(s) ...
  - service: http_status:404
```

then `cloudflared tunnel route dns <tunnel name> frames.example.com` and restart the
cloudflared service (Services → Cloudflared agent → Restart, or `Restart-Service cloudflared`
from an administrator PowerShell).

Check: `https://frames.example.com/` shows the app, and `https://frames.example.com/api/status`
answers `{"ok": false, "error": "password required"}`.

The server already trusts `X-Forwarded-For` only from this computer (`FORWARDED_ALLOW_IPS`), which
is where cloudflared connects from, so the wrong-password limit counts each visitor's real address.
After 5 wrong passwords from one address in 15 minutes that address is locked out until the oldest
attempt ages out, and after 100 from all addresses together everyone is, including you (a request
with no password at all doesn't count). Optional extra: Cloudflare Access in front of `/api/*` only
(the app shell must stay reachable without it for the home-screen app to load), or a WAF rate
limit on `/api/`.

## Run it at startup (Task Scheduler)

`start-server.ps1` runs the server in a loop (restarting it if it stops) and logs to
`data\server.log`. `install-startup-task.ps1` registers it as a scheduled task that starts at boot,
as you, whether or not you're logged in:

```
# PowerShell opened as administrator, in this server\ folder:
powershell -ExecutionPolicy Bypass -File .\install-startup-task.ps1
```

Stop it with `Stop-ScheduledTask -TaskName "Frame Log sync server"`, remove it with
`Unregister-ScheduledTask -TaskName "Frame Log sync server"`. Run `uv sync` once by hand before
installing it (the task runs `uv run --frozen`).

## S3 backup

Off until `S3_BUCKET` is set. With it off, the server logs one line at startup
(`S3 backup disabled: ...`) and everything else works.

When on, each new snapshot and the roll files it changed are uploaded in the background, so a
sync never waits on S3 or fails because of it. If an upload fails, the error shows under **Sync**
in the app (and in `/api/status`), and the next sync does a catch-up pass instead: it lists the
bucket under the prefix and uploads every local file that's missing there (or, for a roll file,
differs). The same pass runs once at every startup, which is how files from before S3 was set up
get there. Nothing is ever deleted from the bucket.

Commands (from `server/`):

```
uv run framelog-sync s3-verify    # put, get, list and delete a test object under the prefix
uv run framelog-sync s3-catchup   # upload everything the bucket lacks, now
```

### AWS setup

1. **Create the bucket** (S3 → Create bucket): any name, e.g. `framelog-backup-<something>`, in
   a region near you. Keep **Block all public access** on. Default encryption (SSE-S3) is fine.
2. **Turn on versioning** (bucket → Properties → Bucket Versioning → Enable). The server never
   deletes or overwrites a snapshot, but versioning means even a leaked key can't destroy old
   backups. Optionally add a lifecycle rule that expires noncurrent versions after, say, 365 days.
3. **Create an IAM user** for this machine only (IAM → Users → Create user, no console access),
   and attach this inline policy. Replace `BUCKET` with the bucket name and `framelog` with your
   `S3_PREFIX` if you changed it:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "ListThePrefix",
         "Effect": "Allow",
         "Action": "s3:ListBucket",
         "Resource": "arn:aws:s3:::BUCKET",
         "Condition": { "StringLike": { "s3:prefix": ["framelog/*"] } }
       },
       {
         "Sid": "ReadWriteBackups",
         "Effect": "Allow",
         "Action": ["s3:PutObject", "s3:GetObject"],
         "Resource": "arn:aws:s3:::BUCKET/framelog/*"
       },
       {
         "Sid": "DeleteOnlyTheVerifyTestObject",
         "Effect": "Allow",
         "Action": "s3:DeleteObject",
         "Resource": "arn:aws:s3:::BUCKET/framelog/_verify/*"
       }
     ]
   }
   ```

   Delete is allowed only on the `_verify/` objects that `s3-verify` writes and removes; the
   backups themselves can't be deleted with this key. (With versioning on, that delete leaves a
   delete marker and the tiny test object as a noncurrent version.)
4. **Create an access key** for that user (Security credentials → Create access key →
   "Application running outside AWS").
5. **Fill in `.env`**:

   ```
   S3_BUCKET=<bucket name>
   S3_REGION=<bucket region, e.g. us-east-1>
   S3_PREFIX=framelog
   AWS_ACCESS_KEY_ID=<key id>
   AWS_SECRET_ACCESS_KEY=<secret>
   ```

   Or keep the keys out of `.env`: put them in `%USERPROFILE%\.aws\credentials` under a profile
   (`aws configure --profile framelog-backup` if you have the AWS CLI) and set
   `AWS_PROFILE=framelog-backup`. The scheduled task runs as you, so it reads the same file.
6. **Check**: `uv run framelog-sync s3-verify` should print `OK`. Then restart the server (or run
   `uv run framelog-sync s3-catchup`) to upload what's already on disk. The app's **Sync** panel
   shows `S3 backup ok` after the next sync.

## Layout

```
server/
  pyproject.toml, uv.lock
  .env.example          every setting, documented
  framelog_sync/
    config.py           settings from .env; refuses a missing or weak password
    store.py            validation, snapshots, per-roll export files, atomic writes
    s3.py               background uploads, catch-up pass, verify
    app.py              FastAPI: static allow-list, /api/sync, /api/status, password + rate limit
    cli.py              framelog-sync [serve | s3-verify | s3-catchup]
  start-server.ps1, install-startup-task.ps1   run at boot via Task Scheduler
  tests/                pytest
```
