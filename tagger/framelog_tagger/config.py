"""Remembered setup-screen values, so the next roll starts where the last left off."""

from __future__ import annotations

import json
import os
from pathlib import Path

FIELDS = ("scans_dir", "log_path", "out_dir", "in_place", "tz", "camera", "make", "film", "iso")


def config_path() -> Path:
    if os.environ.get("FRAMELOG_TAGGER_CONFIG"):
        return Path(os.environ["FRAMELOG_TAGGER_CONFIG"])
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "framelog-tagger" / "config.json"


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
