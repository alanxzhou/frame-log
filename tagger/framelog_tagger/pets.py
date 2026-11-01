"""Pets: find a pet's head in a scan and say which animal it is, the way faces.py does for people.

Two ONNX models, both run with onnxruntime (no PyTorch in the app):

* a head detector, YOLOv8 fine-tuned on the pet heads tagged in Lightroom, so its boxes look like yours.
  It ships with the package (`models/pet_head.onnx`); how it was made is in docs/pet-tagging-experiments.md.
* DINOv2-small, a general image-embedding model, downloaded the first time it's needed. Each head crop
  becomes a fingerprint and is matched against the animals you've already named, exactly like faces.

The pet gallery is separate from the people gallery (`pets.npz`) because the fingerprints come from a
different model.
"""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

from . import config
from .faces import Detection
from .regions import FaceBox

DET_FILE = Path(__file__).parent / "models" / "pet_head.onnx"
EMBED_VARIANT = "model_quantized"   # 24 MB; see the experiments doc for how it compares with the full-size model
EMBED_URL = "https://huggingface.co/onnx-community/dinov2-small/resolve/main/onnx/{variant}.onnx"
DET_SIZE = 960          # the size the detector was exported at
DET_CONF = 0.25         # detections below this are dropped
AUTO_DET = 0.5          # a name is only filled in on its own for a detection at least this sure
ACCEPT = 0.70           # ...and a fingerprint at least this similar to an animal in the gallery
SUGGEST = 0.55          # similar enough to offer as a choice
CROP_MARGIN = 0.3       # context kept around the head box, as a fraction of its size
MEAN, STD = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)


def gallery_path() -> Path:
    return config.config_path().parent / "pets.npz"


def embed_path() -> Path:
    return config.config_path().parent / "models" / f"dinov2-small-{EMBED_VARIANT}.onnx"


def available() -> bool:
    """Whether the detector that ships with the package is there."""
    return DET_FILE.is_file()


def letterbox(im: Image.Image, size: int) -> tuple[np.ndarray, float, int, int]:
    """Fit the picture inside size x size keeping its shape, padding with grey (what the detector was trained with).

    Returns the NCHW float tensor in 0..1, the scale applied, and the left and top padding in pixels.
    """
    k = min(size / im.width, size / im.height)
    w, h = round(im.width * k), round(im.height * k)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    left, top = (size - w) // 2, (size - h) // 2
    canvas.paste(im.resize((w, h), Image.BILINEAR), (left, top))
    return np.asarray(canvas, np.float32).transpose(2, 0, 1)[None] / 255.0, k, left, top


def nms(boxes: np.ndarray, scores: np.ndarray, iou_max: float = 0.5) -> list[int]:
    """Indices to keep, best first, dropping boxes that overlap an already kept one by more than iou_max."""
    order = np.argsort(-scores)
    keep: list[int] = []
    while len(order):
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        if not len(rest):
            break
        ix = np.maximum(0, np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]))
        iy = np.maximum(0, np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]))
        inter = ix * iy
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
        union = area(boxes[i]) + area(boxes[rest]) - inter
        order = rest[inter / np.maximum(union, 1e-9) <= iou_max]
    return keep


def decode(output: np.ndarray, k: float, left: int, top: int, size: tuple[int, int], conf: float) -> list[tuple[FaceBox, float]]:
    """Turn a YOLOv8 export's (1, 5, N) output into boxes in the original picture, as fractions of its size.

    Each of the N candidates is centre x, centre y, width, height (in letterboxed pixels) and the one class's score.
    """
    p = output[0].T if output.shape[1] == 5 else output[0]   # (5, N) as exported; (N, 5) also accepted
    p = p[p[:, 4] >= conf]
    if not len(p):
        return []
    xyxy = np.stack([p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2, p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], axis=1)
    xyxy = (xyxy - np.array([left, top, left, top])) / k
    W, H = size
    xyxy = np.clip(xyxy, 0, [W, H, W, H])
    out = []
    for i in nms(xyxy, p[:, 4]):
        x1, y1, x2, y2 = xyxy[i]
        if x2 - x1 > 1 and y2 - y1 > 1:
            out.append((FaceBox(x1 / W, y1 / H, (x2 - x1) / W, (y2 - y1) / H), float(p[i, 4])))
    return out


def head_crop(im: Image.Image, box: FaceBox, margin: float = CROP_MARGIN) -> np.ndarray:
    """The square around a head with some context, as the embedding model wants it: NCHW, ImageNet-normalised.

    The crop goes to 256 then its centre 224 is kept, which is how DINOv2's own preprocessing treats it.
    """
    W, H = im.size
    cx, cy = (box.x + box.w / 2) * W, (box.y + box.h / 2) * H
    s = max(box.w * W, box.h * H) * (1 + margin) / 2
    c = im.crop((int(cx - s), int(cy - s), int(cx + s), int(cy + s))).resize((256, 256), Image.BICUBIC).crop((16, 16, 240, 240))
    return ((np.asarray(c, np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)[None]


def download_embedder(dest: Path | None = None) -> Path:
    """Fetch the embedding model once (Apache-2.0, from the onnx-community mirror of facebook/dinov2-small)."""
    dest = dest or embed_path()
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(EMBED_URL.format(variant=EMBED_VARIANT), timeout=60) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


class PetEngine:
    """Detector + embedder, loaded on first use. Mirrors faces.Engine: `detect(path)` -> ((w, h), [Detection])."""

    def __init__(self, det_conf: float = DET_CONF, det_file: Path | None = None, embed_file: Path | None = None):
        """`det_file` and `embed_file` override the models (the experiments compare alternatives this way)."""
        self.det_conf = det_conf
        self.det_file, self.embed_file = det_file or DET_FILE, embed_file
        self._det = self._emb = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        self._det = ort.InferenceSession(str(self.det_file), opts, providers=providers)
        self._emb = ort.InferenceSession(str(self.embed_file or download_embedder()), opts, providers=providers)

    def embed(self, im: Image.Image, box: FaceBox) -> np.ndarray:
        h = self._emb.run(None, {self._emb.get_inputs()[0].name: head_crop(im, box)})[0]   # last_hidden_state
        v = np.concatenate([h[0, 0], h[0, 1:].mean(axis=0)]).astype(np.float32)             # class token + mean patch
        return v / np.linalg.norm(v)

    def detect(self, path: Path | str) -> tuple[tuple[int, int], list[Detection]]:
        with Image.open(path) as src:
            im = ImageOps.exif_transpose(src).convert("RGB")
        with self._lock:
            if self._det is None:
                self._load()
            x, k, left, top = letterbox(im, DET_SIZE)
            out = self._det.run(None, {self._det.get_inputs()[0].name: x})[0]
            found = decode(out, k, left, top, im.size, self.det_conf)
            dets = [Detection(box, score, self.embed(im, box), kind="pet") for box, score in found]
        dets.sort(key=lambda d: d.box.x)
        return im.size, dets
