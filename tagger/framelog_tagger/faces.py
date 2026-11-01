"""Find faces in a scan, suggest who they are, and learn from the names you confirm.

Optional: needs the `faces` extra (`uv tool install ".[faces]"`): numpy, onnxruntime, OpenCV and
InsightFace. Nothing is trained. A detector finds faces, a pretrained network turns each into a
512-number fingerprint, and a name is suggested by comparing it with the fingerprints of faces you
have already named (the gallery). Every name you confirm is added to the gallery, so suggestions
get better as you go. Measurements and the reasoning: docs/face-tagging-experiments.md.

InsightFace's pretrained models (buffalo_l) are licensed for non-commercial research use; the
library code is MIT. Fine for sorting your own photos; check before distributing anything built on it.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import numpy as np
from PIL import Image, ImageOps

from . import config
from .regions import FaceBox, TaggedImage, match_boxes

DET_SIZE = 640        # detector input size; see the writeup for what larger sizes buy
DET_THRESH = 0.4      # detections below this confidence are dropped
ACCEPT = 0.40         # a suggestion at least this similar is filled in as the face's name...
AUTO_DET = 0.60       # ...if the detector is at least this sure it's a face. Below it, boxes are often not faces
                      # (hair, ears, a blur), and those match the biggest cluster in the gallery, so they only get suggestions
SUGGEST = 0.25        # a suggestion at least this similar is offered as a choice
MIN_EXAMPLES = 3      # reference faces a person needs before they're suggested
DEPENDENCIES = ("cv2", "onnxruntime", "insightface")


def missing_dependency() -> Optional[str]:
    """The first optional dependency that isn't installed, or None."""
    return next((m for m in DEPENDENCIES if importlib.util.find_spec(m) is None), None)


@dataclass
class Detection:
    box: FaceBox            # in the upright picture
    score: float            # detector confidence
    emb: np.ndarray         # unit-length 512-vector


class Engine:
    """InsightFace buffalo_l: SCRFD detector + ArcFace embedding. The model loads on first use."""

    def __init__(self, det_size: int = DET_SIZE, det_thresh: float = DET_THRESH):
        self.det_size = det_size
        self.det_thresh = det_thresh
        self._app = None
        self._lock = threading.Lock()

    def _load(self):
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


def gallery_path() -> Path:
    return config.config_path().parent / "faces.npz"


class Gallery:
    """Every face you've named: its fingerprint, its name, and which image it came from.

    A face is identified by `source#x,y`, so naming the same face twice (writing a roll again, or
    correcting a name) updates its entry instead of adding a second one.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or gallery_path()
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

    def __len__(self) -> int:
        return len(self.names)

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

    def people(self) -> list[tuple[str, int]]:
        """(name, faces on file), most faces first."""
        return sorted(((n, len(ix)) for n, ix in self._groups.items()), key=lambda p: (-p[1], p[0].lower()))

    @property
    def emb(self) -> np.ndarray:
        if self._matrix is None:
            self._matrix = np.stack(self._rows) if self._rows else np.zeros((0, 512), dtype=np.float32)
        return self._matrix

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

    def save(self) -> None:
        tmp = self.path.with_name(self.path.stem + ".tmp.npz")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(tmp, emb=self.emb, names=np.array(self.names, dtype=str),
                                keys=np.array(self.keys, dtype=str), done=np.array(sorted(self.done), dtype=str))
            os.replace(tmp, self.path)
        except OSError:
            pass   # like the keyword library: remembering is a convenience, never a failure


def import_tagged(engine: Engine, gallery: Gallery, images: Iterable[TaggedImage], exclude: Iterable[str] = (),
                  progress: Optional[Callable[[int, int, int], None]] = None, save_every: int = 100) -> dict:
    """Learn from photos already tagged elsewhere (Lightroom's face regions).

    Each tagged box is matched to the face the detector finds there; that face's fingerprint goes into
    the gallery under the tagged name. Images already imported are skipped, so it can be stopped and resumed.
    `exclude` names are skipped (pets: a human-face model can't tell them apart).
    """
    skip = {n.lower() for n in exclude}
    images = list(images)
    added = unmatched = skipped = 0
    for i, img in enumerate(images, 1):
        source = Gallery.source(img.path)
        if source in gallery.done:
            skipped += 1
        else:
            tagged = [f for f in img.faces if f.name and f.name.lower() not in skip]
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
            gallery.done.add(source)
        if progress:
            progress(i, len(images), added)
        if i % save_every == 0:
            gallery.save()
    gallery.save()
    return {"images": len(images), "skipped": skipped, "added": added, "unmatched": unmatched}
