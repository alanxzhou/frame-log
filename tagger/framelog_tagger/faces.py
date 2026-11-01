"""Find faces in a scan, suggest who they are, and learn from the names you confirm.

Nothing is trained. A detector finds faces, a pretrained network turns each into a
512-number fingerprint, and a name is suggested by comparing it with the fingerprints of faces you
have already named (the gallery). Every name you confirm is added to the gallery, so suggestions
get better as you go. Measurements and the reasoning: docs/face-tagging-experiments.md.

InsightFace's pretrained models (buffalo_l) are licensed for non-commercial research use; the
library code is MIT. Fine for sorting your own photos; check before distributing anything built on it.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import threading
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import numpy as np
from PIL import Image, ImageOps

from . import config
from .keywords import merge
from .regions import FaceBox, TaggedImage, match_boxes, read_regions

def silence_insightface_warning() -> None:
    """InsightFace aligns every face with a scikit-image call that 0.26 deprecated (`tform.estimate`).

    It works, and stays until scikit-image 2.2 (pyproject keeps us below that), but it would print once per
    face. Silence only that. Called when the module loads and again when the model does, since code that
    resets warning filters (a test runner, say) would otherwise bring it back.
    """
    warnings.filterwarnings("ignore", message=r".*estimate.*deprecated.*", category=FutureWarning,
                            module=r"insightface\.utils\.face_align")


silence_insightface_warning()

DET_SIZE = 640        # detector input size; see the writeup for what larger sizes buy
DET_THRESH = 0.4      # detections below this confidence are dropped
ACCEPT = 0.40         # a suggestion at least this similar is filled in as the face's name...
AUTO_DET = 0.60       # ...if the detector is at least this sure it's a face. Below it, boxes are often not faces
                      # (hair, ears, a blur), and those match the biggest cluster in the gallery, so they only get suggestions
SUGGEST = 0.25        # a suggestion at least this similar is offered as a choice
MIN_EXAMPLES = 3      # reference faces a person needs before they're suggested
DEPENDENCIES = ("cv2", "onnxruntime", "insightface")


def missing_dependency() -> Optional[str]:
    """The first dependency that can't be found (a broken install), or None."""
    return next((m for m in DEPENDENCIES if importlib.util.find_spec(m) is None), None)


@dataclass
class Detection:
    box: FaceBox            # in the upright picture
    score: float            # detector confidence
    emb: np.ndarray         # unit-length fingerprint (512 numbers for a face, 768 for a pet)
    kind: str = "face"      # "face" or "pet"; each has its own gallery


class Engine:
    """InsightFace buffalo_l: SCRFD detector + ArcFace embedding. The model loads on first use."""

    def __init__(self, det_size: int = DET_SIZE, det_thresh: float = DET_THRESH):
        self.det_size = det_size
        self.det_thresh = det_thresh
        self._app = None
        self._lock = threading.Lock()

    def _load(self):
        silence_insightface_warning()
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"],
                           providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(self.det_size, self.det_size), det_thresh=self.det_thresh)
        return app

    def detect(self, path: Path | str) -> tuple[tuple[int, int], list[Detection]]:
        """((width, height) of the upright picture, faces left to right)."""
        with Image.open(path) as im:
            arr = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))[:, :, ::-1].copy()   # BGR for OpenCV
        h, w = arr.shape[:2]
        with self._lock:   # onnxruntime is fine with threads, but the model also loads lazily
            if self._app is None:
                self._app = self._load()
            found = self._app.get(arr)
        dets = []
        for f in found:
            x1, y1, x2, y2 = (float(v) for v in f.bbox)
            x1, y1, x2, y2 = max(0.0, x1), max(0.0, y1), min(float(w), x2), min(float(h), y2)
            if x2 <= x1 or y2 <= y1:
                continue
            dets.append(Detection(FaceBox(x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h),
                                  float(f.det_score), np.asarray(f.normed_embedding, dtype=np.float32)))
        dets.sort(key=lambda d: d.box.x)
        return (w, h), dets


def _locked(fn):
    """Run a Gallery method holding its lock: the background sync writes while the UI reads."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


def gallery_path() -> Path:
    return config.config_path().parent / "faces.npz"


class Gallery:
    """Every face you've named: its fingerprint, its name, and which image it came from.

    A face is identified by `source#x,y`, so naming the same face twice (writing a roll again, or
    correcting a name) updates its entry instead of adding a second one.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or gallery_path()
        self._lock = threading.RLock()
        self.names: list[str] = []
        self.keys: list[str] = []
        self.done: set[str] = set()   # images already imported, so a re-run skips them
        self._rows: list[np.ndarray] = []
        try:
            with np.load(self.path, allow_pickle=False) as z:
                self._rows = list(z["emb"].astype(np.float32))
                self.names = [str(n) for n in z["names"]]
                self.keys = [str(k) for k in z["keys"]]
                self.done = {str(d) for d in z["done"]}
        except (OSError, ValueError, KeyError):
            pass
        self._index = {k: i for i, k in enumerate(self.keys)}
        self._groups: dict[str, list[int]] = {}
        for i, n in enumerate(self.names):
            self._groups.setdefault(n, []).append(i)
        self._matrix: Optional[np.ndarray] = None            # built from _rows when first needed
        self._arrays: Optional[dict[str, np.ndarray]] = None  # each person's row numbers, as arrays

    @staticmethod
    def source(path: Path | str) -> str:
        """One spelling of a path, whether it came from ExifTool (D:/x/y.jpg) or from Windows (D:\\x\\y.jpg)."""
        return str(path).replace("\\", "/")

    @classmethod
    def key(cls, path: Path | str, box: FaceBox) -> str:
        return f"{cls.source(path)}#{box.key()}"

    @_locked
    def is_done(self, path: Path | str) -> bool:
        return self.source(path) in self.done

    @_locked
    def mark_done(self, path: Path | str) -> None:
        self.done.add(self.source(path))

    @_locked
    def clear_done(self) -> None:
        self.done.clear()

    @_locked
    def __len__(self) -> int:
        return len(self.names)

    @_locked
    def add(self, name: str, emb: np.ndarray, source: str, box: FaceBox) -> None:
        """Add a named face, or update the entry for this very face (a correction)."""
        k = self.key(source, box)
        emb = np.asarray(emb, dtype=np.float32).reshape(-1)
        i = self._index.get(k)
        if i is None:
            self._index[k] = len(self.names)
            self._groups.setdefault(name, []).append(len(self.names))
            self.names.append(name)
            self.keys.append(k)
            self._rows.append(emb)
        else:
            if self.names[i] != name:
                self._groups[self.names[i]].remove(i)
                if not self._groups[self.names[i]]:
                    del self._groups[self.names[i]]
                self._groups.setdefault(name, []).append(i)
                self.names[i] = name
            self._rows[i] = emb
        self._matrix = self._arrays = None

    @_locked
    def forget(self, name: str) -> int:
        """Remove a person and every face filed under them. Returns how many faces went."""
        keep = [i for i, n in enumerate(self.names) if n.lower() != name.lower()]
        gone = len(self.names) - len(keep)
        if gone:
            self._rows = [self._rows[i] for i in keep]
            self.names = [self.names[i] for i in keep]
            self.keys = [self.keys[i] for i in keep]
            self._index = {k: i for i, k in enumerate(self.keys)}
            self._groups = {}
            for i, n in enumerate(self.names):
                self._groups.setdefault(n, []).append(i)
            self._matrix = self._arrays = None
        return gone

    @_locked
    def people(self) -> list[tuple[str, int]]:
        """(name, faces on file), most faces first."""
        return sorted(((n, len(ix)) for n, ix in self._groups.items()), key=lambda p: (-p[1], p[0].lower()))

    @property
    def emb(self) -> np.ndarray:
        if self._matrix is None:
            self._matrix = np.stack(self._rows) if self._rows else np.zeros((0, 512), dtype=np.float32)
        return self._matrix

    @_locked
    def suggest(self, emb: np.ndarray, n: int = 3, min_examples: int = MIN_EXAMPLES, k: int = 3) -> list[tuple[str, float]]:
        """The `n` people this face most resembles, best first, each scored by the mean of their `k` closest faces."""
        if not self._rows:
            return []
        if self._arrays is None:
            self._arrays = {name: np.array(ix) for name, ix in self._groups.items()}
        sims = self.emb @ np.asarray(emb, dtype=np.float32)
        scored = [(name, float(np.sort(sims[ix])[-k:].mean()))
                  for name, ix in self._arrays.items() if len(ix) >= min_examples]
        scored.sort(key=lambda p: -p[1])
        return scored[:n]

    @_locked
    def save(self) -> None:
        tmp = self.path.with_name(self.path.stem + ".tmp.npz")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(tmp, emb=self.emb, names=np.array(self.names, dtype=str),
                                keys=np.array(self.keys, dtype=str), done=np.array(sorted(self.done), dtype=str))
            os.replace(tmp, self.path)
        except OSError:
            pass   # like the keyword library: remembering is a convenience, never a failure


def import_tagged(engine, gallery: Gallery, images: Iterable[TaggedImage], exclude: Iterable[str] = (),
                  progress: Optional[Callable[[int, int, int], None]] = None, save_every: int = 100,
                  include: Optional[Iterable[str]] = None) -> dict:
    """Learn from photos already tagged elsewhere (Lightroom's face regions).

    Each tagged box is matched to what the detector finds there; that fingerprint goes into the gallery under
    the tagged name. Images already imported are skipped, so it can be stopped and resumed.
    `exclude` names are skipped (the pets, when learning people). With `include`, only those names are learned
    (the pets, when learning pets) and an image is only recorded as done if it has one.
    """
    skip = {n.lower() for n in exclude}
    only = None if include is None else {n.lower() for n in include}
    images = list(images)
    added = unmatched = skipped = 0
    for i, img in enumerate(images, 1):
        if gallery.is_done(img.path):
            skipped += 1
        else:
            tagged = [f for f in img.faces if f.name and f.name.lower() not in skip
                      and (only is None or f.name.lower() in only)]
            if tagged:
                try:
                    _, dets = engine.detect(img.path)
                except (OSError, ValueError):   # unreadable file: leave it unmarked so a later run tries again
                    continue
                for t, di in zip(tagged, match_boxes(tagged, [d.box for d in dets])):
                    if di is None:
                        unmatched += 1
                    else:
                        gallery.add(t.name, dets[di].emb, img.path, dets[di].box)
                        added += 1
            if tagged or only is None:
                gallery.mark_done(img.path)
        if progress:
            progress(i, len(images), added)
        if i % save_every == 0:
            gallery.save()
    gallery.save()
    return {"images": len(images), "skipped": skipped, "added": added, "unmatched": unmatched}


def learn_library(engine: Engine, gallery: Gallery, exiftool: str, folders: Sequence[str], pets: Iterable[str] = (),
                  on_read: Optional[Callable[[], None]] = None,
                  progress: Optional[Callable[[int, int, int], None]] = None,
                  pet_engine=None, pet_gallery: Optional[Gallery] = None,
                  pet_progress: Optional[Callable[[int, int, int], None]] = None) -> dict:
    """Learn from every photo under `folders` that already has named face regions (Lightroom's). Resumable.

    Names in `pets` aren't people: with a pet engine they are learned into the pet gallery, otherwise left out.
    """
    if on_read:
        on_read()
    pets = list(pets)
    images = read_regions(exiftool, folders)
    stats = {**import_tagged(engine, gallery, images, exclude=pets, progress=progress), "tagged_images": len(images)}
    stats["pets_added"] = 0
    if pets and pet_engine is not None and pet_gallery is not None:
        stats["pets_added"] = import_tagged(pet_engine, pet_gallery, images, include=pets, progress=pet_progress)["added"]
    return stats


def update_settings(gallery: Gallery, settings: dict, folders: Sequence[str], pets: Iterable[str],
                    pet_gallery: Optional[Gallery] = None) -> int:
    """Change where the library is and which names are pets, and keep the galleries consistent with that.

    A name newly marked as a pet is removed from the people gallery, and the pet photos are read again for it
    (learning a photo twice doesn't duplicate anything). A name no longer marked is a person after
    all: its photos must be read again as people (the record of what's been imported is cleared) and it leaves
    the pet gallery. Returns how many faces were removed from the people gallery. Settings are saved.
    """
    old = {n.lower() for n in settings.get("pets", [])}
    names = merge(pets)
    now = {n.lower() for n in names}
    removed = sum(gallery.forget(n) for n in names if n.lower() not in old)
    if old - now:
        gallery.clear_done()
    if pet_gallery is not None and old != now:
        for n in old - now:
            pet_gallery.forget(n)
        if now - old:   # a photo already read for one pet may hold the new one too
            pet_gallery.clear_done()
        pet_gallery.save()
    if removed or old - now:
        gallery.save()
    settings.update(library=list(folders), pets=names)
    config.save_faces_settings(settings)
    return removed


class Sync:
    """Keeps the galleries in step with your Lightroom folder, in a background thread.

    Started on every launch (and when the folder setting changes). Reading the folder's tags takes a minute or
    two for a big library; after that only photos not seen before are run through the models, so a start with
    nothing new is quick. `status` is what the UI shows.
    """

    def __init__(self, engine: Engine, gallery: Gallery, exiftool: Optional[str], settings: dict,
                 pet_engine=None, pet_gallery: Optional[Gallery] = None):
        self.engine, self.gallery, self.exiftool, self.settings = engine, gallery, exiftool, settings
        self.pet_engine, self.pet_gallery = pet_engine, pet_gallery
        self._thread: Optional[threading.Thread] = None
        self.status = {"running": False, "phase": "idle", "done": 0, "total": 0, "added": 0, "error": ""}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        """Begin learning from the configured folders. False if there's nothing to do or it's already running."""
        if self.running or not self.settings.get("library"):
            return False
        if not self.exiftool:
            self.status = {**self.status, "phase": "error", "error": "exiftool isn't available, so face tags can't be read"}
            return False
        self.status = {"running": True, "phase": "reading", "done": 0, "total": 0, "added": 0, "error": ""}
        self._thread = threading.Thread(target=self._run, name="faces-sync", daemon=True)
        self._thread.start()
        return True

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        def progress(done: int, total: int, added: int) -> None:
            self.status.update(phase="learning", done=done, total=total, added=added)

        def pet_progress(done: int, total: int, added: int) -> None:
            self.status.update(phase="pets", done=done, total=total, added=added)
        try:
            stats = learn_library(self.engine, self.gallery, self.exiftool, list(self.settings["library"]),
                                  self.settings.get("pets", []), progress=progress, pet_engine=self.pet_engine,
                                  pet_gallery=self.pet_gallery, pet_progress=pet_progress)
            self.settings["synced"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            config.save_faces_settings(self.settings)
            self.status.update(running=False, phase="done", done=stats["images"], total=stats["images"],
                               added=stats["added"] + stats["pets_added"], error="")
        except Exception as e:  # noqa: BLE001  (an unreadable folder, a model that won't load, ...)
            self.status.update(running=False, phase="error", error=str(e) or e.__class__.__name__)
