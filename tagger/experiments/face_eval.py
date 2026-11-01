# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = ["insightface", "onnxruntime", "opencv-python-headless", "pillow", "numpy"]
# ///
"""Measure how well off-the-shelf face detection + recognition reproduces your Lightroom face tags.

Input is a JSON dump of the Lightroom face regions, made with:

    exiftool -r -ext jpg -j -struct -RegionInfo -Orientation# <photos folder> > regions.json

Run it with `uv run experiments/face_eval.py --regions regions.json --cache-dir <dir>`.

What it reports:
  1. Detection recall: share of your tagged faces that the detector finds, by face size.
  2. Recognition: a gallery is built from photos BEFORE --holdout-from; faces in the photos
     from then on are named by nearest neighbour against it. Top-1 / top-3 accuracy, and the
     accuracy-vs-coverage trade-off as the acceptance threshold moves.

Nothing here trains a model. Your tags are used as reference examples and as ground truth.
"""

from __future__ import annotations

import argparse
import collections
import json
import pickle
import random
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


# ---------------------------------------------------------------- loading the Lightroom tags

def to_upright(area: dict, orientation: int | None) -> dict:
    """Map a region from the file's stored (unrotated) pixel frame to the upright, as-displayed frame.

    EXIF orientation says how the stored pixels must be turned for display. Centre, then size, move with
    them; for the 90-degree cases (5-8) the box's normalised width and height trade places.
    """
    x, y, w, h = area["X"], area["Y"], area["W"], area["H"]
    o = orientation or 1
    nx, ny = {1: (x, y), 2: (1 - x, y), 3: (1 - x, 1 - y), 4: (x, 1 - y),
              5: (y, x), 6: (1 - y, x), 7: (1 - y, 1 - x), 8: (y, 1 - x)}.get(o, (x, y))
    if o >= 5:
        w, h = h, w
    return {"X": nx, "Y": ny, "W": w, "H": h}


def load_items(path: str, frame: str = "stored") -> list[dict]:
    """One item per image that has face regions: its path, year/month, and the tagged boxes.

    `frame` says how to read the region coordinates: "stored" = relative to the file's raw pixels, so the
    EXIF orientation is applied to them; "upright" = already relative to the displayed image.
    """
    data = json.load(open(path, encoding="utf-8-sig"))
    items = []
    for d in data:
        info = d.get("RegionInfo") or {}
        regions = info.get("RegionList") or []
        if isinstance(regions, dict):
            regions = [regions]
        faces = [r for r in regions if r.get("Type") == "Face" and r.get("Area")]
        if not faces:
            continue
        src = d["SourceFile"]
        ym = src.split("/raw/", 1)[1][:7] if "/raw/" in src else ""   # "2025/03"
        if frame == "stored":
            faces = [{**r, "Area": to_upright(r["Area"], d.get("Orientation"))} for r in faces]
        items.append({
            "path": src,
            "ym": ym,
            "orientation": d.get("Orientation"),
            "applied": info.get("AppliedToDimensions"),
            "regions": faces,
        })
    return items


def lr_box(area: dict, w: int, h: int) -> np.ndarray:
    """MWG regions give the box CENTRE and size as fractions of the (upright) image."""
    cx, cy, bw, bh = area["X"] * w, area["Y"] * h, area["W"] * w, area["H"] * h
    return np.array([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2])


def iou(a: np.ndarray, b: np.ndarray) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------- detection + embedding (cached)

def make_app(det_size: int, det_thresh: float):
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"],
                       providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(det_size, det_size), det_thresh=det_thresh)
    return app


def load_upright(path: str) -> np.ndarray:
    """Pixels as Lightroom shows them: EXIF rotation applied. Returns BGR."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.asarray(img)[:, :, ::-1].copy()


def detect_all(items: list[dict], app, cache_file: Path) -> dict:
    cache = pickle.loads(cache_file.read_bytes()) if cache_file.exists() else {}
    todo = [it for it in items if it["path"] not in cache]
    print(f"detecting: {len(todo)} images to do, {len(items) - len(todo)} cached")
    t0 = time.time()
    for i, it in enumerate(todo, 1):
        try:
            arr = load_upright(it["path"])
            faces = app.get(arr)
            cache[it["path"]] = {
                "size": (arr.shape[1], arr.shape[0]),
                "faces": [{"bbox": f.bbox.copy(), "kps": f.kps.copy(), "score": float(f.det_score),
                           "emb": f.normed_embedding.copy()} for f in faces],
            }
        except Exception as e:   # unreadable file: record it, move on
            cache[it["path"]] = {"error": str(e), "size": (0, 0), "faces": []}
        if i % 100 == 0 or i == len(todo):
            cache_file.write_bytes(pickle.dumps(cache))
            rate = i / (time.time() - t0)
            print(f"  {i}/{len(todo)}  {rate:.1f} img/s  eta {(len(todo) - i) / rate / 60:.1f} min", flush=True)
    return cache


# ---------------------------------------------------------------- matching detections to your tags

def match(item: dict, det: dict) -> list[tuple[int, int | None, float]]:
    """For each tagged region: (region index, matched detection index or None, IoU).

    A detection matches a region if its centre is inside the Lightroom box or the IoU is decent.
    Each detection is used at most once, best IoU first.
    """
    w, h = det["size"]
    boxes = [lr_box(r["Area"], w, h) for r in item["regions"]]
    pairs = []
    for ri, b in enumerate(boxes):
        for di, f in enumerate(det["faces"]):
            fb = f["bbox"]
            cx, cy = (fb[0] + fb[2]) / 2, (fb[1] + fb[3]) / 2
            inside = b[0] <= cx <= b[2] and b[1] <= cy <= b[3]
            v = iou(b, fb)
            if inside or v >= 0.3:
                pairs.append((v, ri, di))
    pairs.sort(reverse=True)
    used_r, used_d, out = set(), set(), {}
    for v, ri, di in pairs:
        if ri in used_r or di in used_d:
            continue
        used_r.add(ri)
        used_d.add(di)
        out[ri] = (di, v)
    return [(ri, *(out[ri] if ri in out else (None, 0.0))) for ri in range(len(boxes))]


# ---------------------------------------------------------------- reports

def report_geometry(items, cache):
    """Sanity checks on the coordinate handling before trusting anything else."""
    mism = swapped = 0
    rot = collections.Counter()
    for it in items:
        w, h = cache[it["path"]]["size"]
        a = it["applied"]
        if w and a and a.get("W") and a.get("H"):
            ar, ir = a["W"] / a["H"], w / h
            if abs(ar - ir) > 0.02:
                mism += 1
                if abs(ar - 1 / ir) < 0.02:
                    swapped += 1
        for r in it["regions"]:
            rot[round(float(r.get("Rotation", 0) or 0), 2)] += 1
    print(f"\n== geometry ==\nimages whose AppliedToDimensions aspect differs from the upright image: "
          f"{mism} (of which width/height swapped: {swapped})")
    print("region Rotation values (rounded):", dict(rot.most_common(6)))


def report_detection(items, cache, exclude=frozenset()):
    """Recall over tagged faces, skipping regions named in `exclude` (pets: a human-face detector isn't meant to find them)."""
    buckets = [(0, 40), (40, 80), (80, 160), (160, 10_000)]
    tot = collections.Counter()
    hit = collections.Counter()
    by_orient = collections.defaultdict(lambda: [0, 0])   # group -> [found, total]
    extra = images = 0
    for it in items:
        det = cache[it["path"]]
        if not det["size"][0]:
            continue
        images += 1
        w, h = det["size"]
        m = match(it, det)
        extra += len(det["faces"]) - sum(1 for _, di, _ in m if di is not None)
        o = it["orientation"] or 1
        group = "upright (1)" if o == 1 else "mirrored/180 (2-4)" if o < 5 else "rotated 90 (5-8)"
        m = [(ri, di, v) for ri, di, v in m if it["regions"][ri].get("Name") not in exclude]
        for _, di, _ in m:
            by_orient[group][0] += di is not None
            by_orient[group][1] += 1
        for ri, di, _ in m:
            b = lr_box(it["regions"][ri]["Area"], w, h)
            size = min(b[2] - b[0], b[3] - b[1])
            for lo, hi in buckets:
                if lo <= size < hi:
                    tot[(lo, hi)] += 1
                    hit[(lo, hi)] += di is not None
    print("\n== detection recall (your tagged faces that the detector finds) ==")
    for k in buckets:
        if tot[k]:
            print(f"  face {k[0]:>3}-{k[1] if k[1] < 10_000 else '+':<4}px: {hit[k]:5d}/{tot[k]:5d}  {hit[k] / tot[k]:6.1%}")
    T, H = sum(tot.values()), sum(hit.values())
    print(f"  overall           : {H:5d}/{T:5d}  {H / T:6.1%}")
    print(f"  detections with no tag behind them (strangers, or faces you didn't tag): {extra} in {images} images")
    print("  by EXIF orientation (a big gap between these means the coordinates are being read wrongly):")
    for g, (f, t) in sorted(by_orient.items()):
        print(f"    {g:<20} {f:5d}/{t:5d}  {f / t:6.1%}")


def build_samples(items, cache, exclude):
    """(name, ym, embedding) for every tagged face the detector found."""
    out = []
    for it in items:
        det = cache[it["path"]]
        if not det["size"][0]:
            continue
        for ri, di, _ in match(it, det):
            name = it["regions"][ri].get("Name")
            if di is None or not name or name in exclude:
                continue
            out.append((name, it["ym"], det["faces"][di]["emb"], det["faces"][di]["score"]))
    return out


def person_scores(q: np.ndarray, gallery: dict[str, np.ndarray], k: int = 3) -> dict[str, float]:
    """Similarity to each person: mean of the k closest of their reference faces."""
    return {p: float(np.sort(g @ q)[-k:].mean()) for p, g in gallery.items()}


def report_recognition(samples, holdout_from, min_gallery):
    train = [s for s in samples if s[1] < holdout_from]
    test = [s for s in samples if s[1] >= holdout_from]
    by = collections.defaultdict(list)
    for n, _, e, _ in train:
        by[n].append(e)
    gallery = {n: np.stack(v) for n, v in by.items() if len(v) >= min_gallery}
    known_q = [s for s in test if s[0] in gallery]
    unknown_q = [s for s in test if s[0] not in gallery]
    print(f"\n== recognition ==\ngallery: {len(train)} faces, {len(gallery)} people with >= {min_gallery} examples "
          f"(before {holdout_from})")
    print(f"held out: {len(test)} detected tagged faces from {holdout_from} on: "
          f"{len(known_q)} of people in the gallery, {len(unknown_q)} of people not in it")
    if not known_q:
        print("nothing to evaluate")
        return

    rows = []   # (true, ranked names, top score, detector confidence)
    for n, _, e, det in known_q:
        sc = person_scores(e, gallery)
        ranked = sorted(sc, key=sc.get, reverse=True)
        rows.append((n, ranked, sc[ranked[0]], det))
    top1 = np.mean([r[1][0] == r[0] for r in rows])
    top3 = np.mean([r[0] in r[1][:3] for r in rows])
    print(f"top-1 accuracy {top1:.1%}   top-3 accuracy {top3:.1%}   (no threshold: every face gets a guess)")

    unk_scores = [max(person_scores(e, gallery).values()) for _, _, e, _ in unknown_q]
    print("\nthreshold  answered   accuracy of answers   people-not-in-gallery wrongly named")
    for t in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        acc = [r for r in rows if r[2] >= t]
        ok = np.mean([r[1][0] == r[0] for r in acc]) if acc else float("nan")
        wrong = np.mean([s >= t for s in unk_scores]) if unk_scores else float("nan")
        print(f"  {t:.2f}     {len(acc) / len(rows):6.1%}      {ok:6.1%}                {wrong:6.1%}")

    print("\nby detector confidence, at threshold 0.40 (real tagged faces only; junk boxes aren't in this set):")
    for lo, hi in ((0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)):
        sub = [r for r in rows if lo <= r[3] < hi]
        acc = [r for r in sub if r[2] >= 0.40]
        if sub:
            ok = np.mean([r[1][0] == r[0] for r in acc]) if acc else float("nan")
            print(f"  det {lo:.1f}-{min(hi, 1):.1f}: {len(sub):4d} faces ({len(sub) / len(rows):5.1%} of held out)  "
                  f"answered {len(acc) / len(sub):6.1%}  accuracy of answers {ok:6.1%}")

    conf = collections.Counter((r[0], r[1][0]) for r in rows if r[1][0] != r[0])
    print("\nmost common mix-ups (true -> guessed):")
    for (a, b), c in conf.most_common(8):
        print(f"  {c:3d}  {a} -> {b}")
    per = collections.defaultdict(list)
    for r in rows:
        per[r[0]].append(r[1][0] == r[0])
    print("\nper person (held-out faces, top-1):")
    for n, v in sorted(per.items(), key=lambda kv: -len(kv[1]))[:12]:
        print(f"  {n:<14} {len(v):4d}  {np.mean(v):6.1%}")


# ---------------------------------------------------------------- eyeballing the alignment

def draw(items, cache, out_dir: Path, n: int, seed: int, misses: bool = False):
    """Write images with the Lightroom boxes (green) and detections (red).

    With `misses`, only images where a tagged face went undetected, and those boxes are yellow.
    """
    import cv2
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    if misses:
        pool = [i for i in items if cache[i["path"]]["size"][0]
                and any(di is None for _, di, _ in match(i, cache[i["path"]]))]
        pick = rng.sample(pool, min(n, len(pool)))
    else:
        rotated = [i for i in items if i["orientation"] not in (None, 1) and len(i["regions"]) >= 1]
        upright = [i for i in items if i["orientation"] in (None, 1) and len(i["regions"]) >= 2]
        pick = rng.sample(rotated, min(n // 2, len(rotated))) + rng.sample(upright, min(n - n // 2, len(upright)))
    for k, it in enumerate(pick):
        arr = load_upright(it["path"])
        h, w = arr.shape[:2]
        missed = {ri for ri, di, _ in match(it, cache[it["path"]]) if di is None}
        for ri, r in enumerate(it["regions"]):
            b = lr_box(r["Area"], w, h).astype(int)
            color = (0, 220, 255) if ri in missed else (0, 200, 0)
            cv2.rectangle(arr, tuple(b[:2]), tuple(b[2:]), color, max(2, w // 400))
            cv2.putText(arr, str(r.get("Name", "?")), (b[0], max(b[1] - 6, 20)), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.6, w / 1800), color, max(1, w // 800))
        for f in cache[it["path"]]["faces"]:
            b = f["bbox"].astype(int)
            cv2.rectangle(arr, tuple(b[:2]), tuple(b[2:]), (0, 0, 230), max(2, w // 400))
        scale = 1400 / max(w, h)
        arr = cv2.resize(arr, None, fx=scale, fy=scale)
        cv2.imwrite(str(out_dir / f"{k:02d}_orient{it['orientation']}.jpg"), arr)
    print(f"\nwrote {len(pick)} check images to {out_dir}  (green = Lightroom tag, red = detector"
          + (", yellow = tagged face the detector missed)" if misses else ")"))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", required=True, help="JSON dump of the Lightroom regions")
    ap.add_argument("--cache-dir", required=True, help="where detections are cached between runs")
    ap.add_argument("--det-size", type=int, default=640, help="detector input size; larger finds smaller faces")
    ap.add_argument("--det-thresh", type=float, default=0.4)
    ap.add_argument("--frame", choices=["stored", "upright"], default="stored",
                    help="coordinate frame of the Lightroom regions (see load_items)")
    ap.add_argument("--holdout-from", default="2026", help='"YYYY" or "YYYY/MM": photos from here on are the test set')
    ap.add_argument("--min-gallery", type=int, default=3, help="reference faces a person needs to be recognised")
    ap.add_argument("--exclude", nargs="*", default=[], help="names to skip, e.g. pets")
    ap.add_argument("--limit", type=int, help="random subset of images (quick smoke test)")
    ap.add_argument("--draw", type=int, default=0, help="write N images with both sets of boxes to check alignment")
    ap.add_argument("--draw-misses", type=int, default=0, help="like --draw, but only images with a missed tagged face")
    ap.add_argument("--draw-dir", default=None)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    items = load_items(a.regions, a.frame)
    if a.limit:
        items = random.Random(a.seed).sample(items, min(a.limit, len(items)))
    print(f"{len(items)} images with tagged faces, {sum(len(i['regions']) for i in items)} regions")

    cache_dir = Path(a.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"faces_{a.det_size}_{a.det_thresh}.pkl"
    cache = detect_all(items, make_app(a.det_size, a.det_thresh), cache_file)
    items = [i for i in items if i["path"] in cache]

    report_geometry(items, cache)
    report_detection(items, cache, set(a.exclude))
    report_recognition(build_samples(items, cache, set(a.exclude)), a.holdout_from, a.min_gallery)
    if a.draw:
        draw(items, cache, Path(a.draw_dir or cache_dir / "check"), a.draw, a.seed)
    if a.draw_misses:
        draw(items, cache, Path(a.draw_dir or cache_dir / "misses"), a.draw_misses, a.seed, misses=True)


if __name__ == "__main__":
    main()
