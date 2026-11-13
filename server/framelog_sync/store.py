"""Validate what the phone pushes and keep it on disk.

Layout under DATA_DIR:

    snapshots/YYYY-MM/<UTC time>-<hash>.json   every distinct state the phone has pushed (append-only)
    rolls/<slug>--<roll id>.json               latest version of each roll, in the app's "Export JSON"
                                               format, so the tagger reads it directly
    state.json                                 last sync time and the hash of the latest snapshot

A snapshot file is never overwritten or deleted. A roll deleted on the phone keeps its file in
rolls/. The one thing that is removed is a roll's previous rolls/ file when the roll is renamed
on the phone (the file name follows the roll name); every version is still in snapshots/.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROLL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_ROLLS = 5000
MAX_ENTRIES_PER_ROLL = 5000
MAX_NAME_LENGTH = 500

# Fields the app writes on every entry and their allowed JSON types. Unknown fields are kept
# as-is (the app can grow), and every known one may also be null or missing: data written by
# older versions of the app (a frame # stored as NaN became null) must still sync.
_NUMBER_FIELDS = ("frame", "lat", "lon", "acc", "alt")
_STRING_FIELDS = ("iso", "local", "tz", "lens", "aperture", "shutterSpeed", "notes")


class ValidationError(ValueError):
    pass


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_state(data: Any) -> dict:
    """Check the shape the app keeps in localStorage: {currentRoll, rolls: {id: {name, entries}}}."""
    if not isinstance(data, dict):
        raise ValidationError("body must be a JSON object")
    rolls = data.get("rolls")
    if not isinstance(rolls, dict):
        raise ValidationError('"rolls" must be an object')
    if len(rolls) > MAX_ROLLS:
        raise ValidationError(f"too many rolls ({len(rolls)} > {MAX_ROLLS})")
    current = data.get("currentRoll")
    if current is not None and not isinstance(current, str):
        raise ValidationError('"currentRoll" must be a string')
    for rid, roll in rolls.items():
        if not ROLL_ID_RE.match(rid):
            raise ValidationError(f"roll id {rid[:70]!r} is not valid")
        if not isinstance(roll, dict):
            raise ValidationError(f"roll {rid}: must be an object")
        name = roll.get("name")
        if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH:
            raise ValidationError(f"roll {rid}: \"name\" must be a string of at most {MAX_NAME_LENGTH} characters")
        entries = roll.get("entries")
        if not isinstance(entries, list):
            raise ValidationError(f"roll {rid}: \"entries\" must be a list")
        if len(entries) > MAX_ENTRIES_PER_ROLL:
            raise ValidationError(f"roll {rid}: too many entries")
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                raise ValidationError(f"roll {rid} entry {i}: must be an object")
            for k in _NUMBER_FIELDS:
                if e.get(k) is not None and not _is_number(e[k]):
                    raise ValidationError(f"roll {rid} entry {i}: \"{k}\" must be a number or null")
            for k in _STRING_FIELDS:
                if e.get(k) is not None and not isinstance(e[k], str):
                    raise ValidationError(f"roll {rid} entry {i}: \"{k}\" must be a string or null")
    return data


def content_hash(state: dict) -> str:
    """Hash of the rolls only: switching the current roll on the phone isn't a new snapshot."""
    canonical = json.dumps(state["rolls"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def slug(name: str) -> str:
    """Same as the app's slug() (used for export file names), capped in length."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name).lower()[:80]
    return s or "roll"


def roll_export(name: str, rid: str, entries: list, exported: str) -> dict:
    """A roll as the app's "Export JSON" writes it (buildJson in ../index.html)."""
    return {
        "app": "framelog",
        "schema": 2,
        "exported": exported,
        "roll": name,
        "rollId": rid,
        "entries": entries,
    }


def dump_export(doc: dict) -> bytes:
    # JSON.stringify(..., null, 2) + "\n", as the app writes it.
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    """Write to a temp file in the same folder, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows: the target can be briefly held open by a reader or a virus scanner.
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class SyncResult:
    stored: bool                      # False: identical to the latest snapshot, nothing written
    snapshot: Optional[Path]          # the new snapshot, if one was written
    hash: str
    written: list[Path] = field(default_factory=list)  # every file written (snapshot + rolls)


class Store:
    def __init__(self, data_dir: Path, clock=lambda: datetime.now(timezone.utc)):
        self.dir = Path(data_dir)
        self.snapshots = self.dir / "snapshots"
        self.rolls = self.dir / "rolls"
        self.state_file = self.dir / "state.json"
        self.clock = clock
        self.lock = threading.Lock()
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.rolls.mkdir(parents=True, exist_ok=True)
        self.meta = self._load_meta()
        self.snapshot_count = sum(1 for _ in self.snapshot_files())

    # -- bookkeeping --

    def _load_meta(self) -> dict:
        try:
            meta = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                return meta
        except (OSError, ValueError):
            pass
        # No state.json (first run, or it was deleted): rebuild it from the newest snapshot.
        meta: dict = {}
        files = sorted(self.snapshot_files(), key=lambda p: p.name)
        if files:
            try:
                doc = json.loads(files[-1].read_text(encoding="utf-8"))
                meta = {"last_hash": doc.get("hash"), "last_snapshot_at": doc.get("received"),
                        "last_snapshot": files[-1].relative_to(self.dir).as_posix()}
            except (OSError, ValueError):
                pass
        return meta

    def _save_meta(self) -> None:
        atomic_write(self.state_file, json.dumps(self.meta, indent=2).encode("utf-8"))

    def snapshot_files(self):
        return self.snapshots.glob("*/*.json")

    def backup_files(self) -> list[Path]:
        """Everything that belongs in the S3 backup (state.json is only bookkeeping)."""
        return sorted(self.snapshot_files()) + sorted(self.rolls.glob("*.json"))

    def status(self) -> dict:
        return {
            "last_sync_at": self.meta.get("last_sync_at"),
            "last_snapshot_at": self.meta.get("last_snapshot_at"),
            "snapshot_count": self.snapshot_count,
            "roll_files": sum(1 for _ in self.rolls.glob("*.json")),
        }

    # -- the sync itself --

    def save(self, state: dict) -> SyncResult:
        """Store a validated state. Thread-safe; writes nothing if it matches the latest snapshot."""
        with self.lock:
            now = self.clock()
            h = content_hash(state)
            if h == self.meta.get("last_hash"):
                self.meta["last_sync_at"] = _iso(now)
                self._save_meta()
                return SyncResult(stored=False, snapshot=None, hash=h)

            received = _iso(now)
            snap = self._new_snapshot_path(now, h)
            doc = {"app": "framelog", "kind": "snapshot", "received": received, "hash": h, "state": state}
            atomic_write(snap, json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self.snapshot_count += 1
            written = [snap] + self._write_rolls(state["rolls"], received)

            self.meta.update(last_hash=h, last_sync_at=received, last_snapshot_at=received,
                             last_snapshot=snap.relative_to(self.dir).as_posix())
            self._save_meta()
            return SyncResult(stored=True, snapshot=snap, hash=h, written=written)

    def _new_snapshot_path(self, now: datetime, h: str) -> Path:
        folder = self.snapshots / now.strftime("%Y-%m")
        stamp = now.strftime("%Y%m%dT%H%M%S") + f".{now.microsecond:06d}Z"
        path = folder / f"{stamp}-{h[:12]}.json"
        n = 1
        while path.exists():  # append-only: never replace an existing snapshot
            n += 1
            path = folder / f"{stamp}-{h[:12]}-{n}.json"
        return path

    def _write_rolls(self, rolls: dict, received: str) -> list[Path]:
        written = []
        existing: dict[str, list[Path]] = {}
        for p in self.rolls.glob("*.json"):
            rid = p.stem.rpartition("--")[2]
            existing.setdefault(rid, []).append(p)
        for rid, roll in rolls.items():
            target = self.rolls / f"{slug(roll['name'])}--{rid}.json"
            if target.exists() and self._same_roll(target, roll):
                continue
            atomic_write(target, dump_export(roll_export(roll["name"], rid, roll["entries"], received)))
            written.append(target)
            # Renamed on the phone: drop the file under the old name (the old version is in snapshots/).
            for old in existing.get(rid, []):
                if old != target:
                    old.unlink(missing_ok=True)
        return written

    @staticmethod
    def _same_roll(path: Path, roll: dict) -> bool:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return doc.get("roll") == roll["name"] and doc.get("entries") == roll["entries"]
