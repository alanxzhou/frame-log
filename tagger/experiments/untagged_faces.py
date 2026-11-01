# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = ["insightface", "onnxruntime", "opencv-python-headless", "pillow", "numpy"]
# ///
"""What are the faces the detector finds that have no Lightroom tag behind them?

Reuses the detections cached by face_eval.py. Each untagged detection is compared with a gallery built
from the faces that ARE tagged, so a strong match to a known person means "a person you know, left
untagged" (a miss by Lightroom or by you). The rest is strangers, background faces, or detector false alarms.

    uv run experiments/untagged_faces.py --regions regions.json --cache <dir>/faces_640_0.4.pkl --sheet sheet.jpg

Only images that have at least one Lightroom face region are looked at: photos with no tags at all aren't in
the regions dump, so people missed in a photo where Lightroom found nobody aren't counted here.
"""

from __future__ import annotations

import argparse
import collections
import pickle
import random

import numpy as np
from PIL import Image, ImageDraw, ImageOps

import face_eval as fe


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", required=True)
    ap.add_argument("--cache", required=True, help="the faces_<size>_<thresh>.pkl written by face_eval.py")
    ap.add_argument("--exclude", nargs="*", default=[], help="names to skip (pets)")
    ap.add_argument("--det-min", type=float, default=0.0, help="only untagged detections at least this confident")
    ap.add_argument("--det-max", type=float, default=1.01, help="...and below this")
    ap.add_argument("--sheet", help="write a contact sheet of random untagged faces here")
    ap.add_argument("--per-group", type=int, default=24, help="faces per group on the sheet")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    items = fe.load_items(a.regions)
    cache = pickle.loads(open(a.cache, "rb").read())
    items = [i for i in items if i["path"] in cache and cache[i["path"]]["size"][0]]
    exclude = set(a.exclude)

    # gallery: every tagged face the detector found, by person
    by = collections.defaultdict(list)
    untagged = []   # (path, box, det score, embedding)
    for it in items:
        det = cache[it["path"]]
        m = fe.match(it, det)
        used = {di for _, di, _ in m if di is not None}
        for ri, di, _ in m:
            name = it["regions"][ri].get("Name")
            if di is not None and name and name not in exclude:
                by[name].append(det["faces"][di]["emb"])
        for di, f in enumerate(det["faces"]):
            if di not in used and a.det_min <= f["score"] < a.det_max:
                untagged.append((it["path"], f["bbox"], f["score"], f["emb"]))
    gallery = {n: np.stack(v) for n, v in by.items() if len(v) >= 3}
    print(f"gallery: {len(gallery)} people from {sum(len(v) for v in gallery.values())} tagged faces")
    print(f"untagged detections (det score {a.det_min}-{min(a.det_max, 1):.2f}): {len(untagged)} "
          f"in {len({u[0] for u in untagged})} images")

    scored = []
    for path, bbox, score, emb in untagged:
        sc = fe.person_scores(emb, gallery)
        name = max(sc, key=sc.get)
        scored.append((path, bbox, score, name, sc[name]))

    groups = {"known (>= 0.40)": [s for s in scored if s[4] >= 0.40],
              "maybe (0.25-0.40)": [s for s in scored if 0.25 <= s[4] < 0.40],
              "unfamiliar (< 0.25)": [s for s in scored if s[4] < 0.25]}
    print("\nwhat they look like, against the people you've tagged:")
    for g, v in groups.items():
        print(f"  {g:<22} {len(v):5d}  {len(v) / max(1, len(scored)):6.1%}")
    print("  closer look at the 'known' ones: " + ", ".join(
        f">= {t:.2f}: {sum(s[4] >= t for s in scored)}" for t in (0.5, 0.6, 0.7)))
    top = collections.Counter(s[3] for s in groups["known (>= 0.40)"])
    print("  most common among 'known':", ", ".join(f"{n} {c}" for n, c in top.most_common(8)))
    print("\nby detector confidence:")
    for lo, hi in ((0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
        v = [s for s in scored if lo <= s[2] < hi]
        if v:
            k = sum(s[4] >= 0.40 for s in v)
            print(f"  det {lo:.1f}-{min(hi, 1):.1f}: {len(v):5d} untagged, {k / len(v):6.1%} look like someone you know")

    if a.sheet:
        rng = random.Random(a.seed)
        tile, cols = 168, 8
        rows = sum(-(-min(a.per_group, len(v)) // cols) for v in groups.values() if v)
        sheet = Image.new("RGB", (tile * cols, (tile + 18) * rows + 22 * len(groups)), (24, 24, 24))
        d = ImageDraw.Draw(sheet)
        y = 0
        for g, v in groups.items():
            if not v:
                continue
            d.text((4, y + 4), f"{g}: {len(v)} untagged faces", fill=(255, 255, 255))
            y += 22
            for i, (path, bbox, score, name, sim) in enumerate(rng.sample(v, min(a.per_group, len(v)))):
                im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
                x1, y1, x2, y2 = bbox
                cx, cy, r = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 0.8
                crop = im.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r))).resize((tile, tile))
                x, yy = (i % cols) * tile, y + (i // cols) * (tile + 18)
                sheet.paste(crop, (x, yy))
                d.text((x + 2, yy + tile + 2), f"{name[:10]} {sim:.2f} d{score:.2f}", fill=(220, 220, 220))
            y += -(-min(a.per_group, len(v)) // cols) * (tile + 18)
        sheet.save(a.sheet, quality=88)
        print(f"\nwrote {a.sheet}  (label: nearest known person, similarity, detector confidence)")


if __name__ == "__main__":
    main()
