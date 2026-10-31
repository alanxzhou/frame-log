"""Batch-rename a lab's scan folder to <roll>/<roll>-NN.ext.

Labs deliver e.g. 12345678/123456780001.jpg ... with the frame order often
reversed. Files are numbered in natural-sort order (or reversed), and files
sharing a stem (a scan delivered as both .jpg and .tif) keep sharing one.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .scans import natural_key

IMAGE_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}
# Characters Windows refuses in file names; also kept out on mac/linux so a folder copies anywhere.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class RenamePlan:
    src_dir: Path
    dst_dir: Path                       # == src_dir when the folder keeps its name
    items: list[tuple[str, str]]        # (old filename, new filename), in frame order

    def to_dict(self) -> dict:
        return {
            "src_dir": str(self.src_dir), "dst_dir": str(self.dst_dir),
            "items": [{"old": o, "new": n} for o, n in self.items],
        }


def validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("roll name is empty")
    if _BAD_CHARS.search(name):
        raise ValueError('roll name can\'t contain any of < > : " / \\ | ? *')
    if name.endswith("."):
        raise ValueError("roll name can't end with a dot")
    return name


_NAMED = re.compile(r"^(.+)-(\d{2,})$")


def _images(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]


def parse_named(stems: set[str]) -> tuple[str, dict[str, int]] | None:
    """If every stem is <name>-NN with the same name (a roll renamed before), return the name and each stem's number."""
    matches = [(_NAMED.match(s), s) for s in stems]
    if not matches or any(m is None for m, _ in matches):
        return None
    names = {m.group(1) for m, _ in matches}
    numbers = {s: int(m.group(2)) for m, s in matches}
    if len(names) != 1 or len(set(numbers.values())) != len(numbers):   # roll-1 and roll-01 would collide
        return None
    return names.pop(), numbers


def detect_name(directory: Path | str) -> str | None:
    """The roll name a folder was already renamed to, if it was."""
    src = Path(directory).expanduser()
    if not src.is_dir():
        return None
    parsed = parse_named({p.stem for p in _images(src)})
    return parsed[0] if parsed else None


def plan_rename(directory: Path | str, name: str, reverse: bool = False,
                rename_folder: bool = True) -> RenamePlan:
    """Work out the renames without touching anything. Raises ValueError with a user-facing message.

    A roll renamed before keeps its frame numbers (so a deleted blank doesn't
    shift everything after it); anything else is numbered 1..N in sort order.
    """
    src = Path(directory).expanduser()
    if not src.is_dir():
        raise ValueError(f"scans folder not found: {src}")
    name = validate_name(name)

    files = _images(src)
    if not files:
        raise ValueError(f"no images in {src}")
    parsed = parse_named({p.stem for p in files})
    if parsed:
        order = parsed[1]
        if reverse:
            lo, hi = min(order.values()), max(order.values())
            order = {s: lo + hi - n for s, n in order.items()}
    else:
        stems = sorted({p.stem for p in files}, key=natural_key, reverse=reverse)
        order = {stem: i for i, stem in enumerate(stems, 1)}
    width = max(2, len(str(max(order.values()))))
    items = [(p.name, f"{name}-{order[p.stem]:0{width}d}{p.suffix.lower()}")
             for p in sorted(files, key=lambda p: (order[p.stem], p.suffix.lower()))]

    # A non-image file already holding a target name would be clobbered.
    renamed = {o for o, _ in items}
    others = {p.name.lower() for p in src.iterdir() if p.name not in renamed}
    clash = next((n for _, n in items if n.lower() in others), None)
    if clash:
        raise ValueError(f"{clash} already exists in {src} and isn't one of the scans")

    dst = src.with_name(name) if rename_folder else src
    if dst != src and dst.exists() and not _same_path(dst, src):
        raise ValueError(f"{dst} already exists")
    return RenamePlan(src_dir=src, dst_dir=dst, items=items)


def _same_path(a: Path, b: Path) -> bool:
    # Changing only the case of a folder name on Windows/mac: the "existing" target is the source.
    try:
        return a.samefile(b)
    except OSError:
        return False


def apply_rename(plan: RenamePlan) -> Path:
    """Rename the files, then the folder. Returns the folder's new path.

    Goes through temporary names so swapping orders (re-running with reverse
    flipped) can't collide with itself.
    """
    moves = [(o, n) for o, n in plan.items if o != n]
    tag = uuid.uuid4().hex[:8]
    temps = []
    for i, (old, new) in enumerate(moves):
        tmp = f".framelog-rename-{tag}-{i}{Path(old).suffix}"
        (plan.src_dir / old).rename(plan.src_dir / tmp)
        temps.append((tmp, new))
    for tmp, new in temps:
        (plan.src_dir / tmp).rename(plan.src_dir / new)
    if plan.dst_dir != plan.src_dir:
        plan.src_dir.rename(plan.dst_dir)
    return plan.dst_dir
