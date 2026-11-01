"""Remembered setup-screen values, so the next roll starts where the last left off."""

from __future__ import annotations

import json
import os
from pathlib import Path

# Camera and film are left out on purpose: they change roll to roll, and a stale one would be written silently.
FIELDS = ("scans_dir", "log_path", "out_dir", "tz")


def config_path() -> Path:
    if os.environ.get("FRAMELOG_TAGGER_CONFIG"):
        return Path(os.environ["FRAMELOG_TAGGER_CONFIG"])
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "framelog-tagger" / "config.json"


def faces_settings_path() -> Path:
    return config_path().parent / "faces.json"


def load_faces_settings() -> dict:
    """The face-tagging settings kept between runs: where your tagged photos are, and which names are pets."""
    out = {"library": [], "pets": [], "synced": ""}
    try:
        data = json.loads(faces_settings_path().read_text(encoding="utf-8"))
        out["library"] = [str(p) for p in data.get("library", []) if str(p).strip()]
        out["pets"] = [str(n) for n in data.get("pets", data.get("exclude", [])) if str(n).strip()]   # "exclude" is what it was called first
        out["synced"] = str(data.get("synced", ""))
    except (OSError, ValueError, AttributeError):
        pass
    return out


def save_faces_settings(settings: dict) -> None:
    p = faces_settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({k: settings[k] for k in ("library", "pets", "synced") if k in settings},
                                indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # remembering is a convenience, never a failure


def load() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return {k: data[k] for k in FIELDS if k in data}
    except (OSError, ValueError):
        return {}


def save(values: dict) -> None:
    keep = {k: values[k] for k in FIELDS if k in values}
    p = config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    except OSError:
        pass  # remembering is a convenience, never a failure
