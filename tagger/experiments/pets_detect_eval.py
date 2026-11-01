"""Score a pet detector against the pet heads you tagged (a dataset from pets_dataset.py).

    python experiments/pets_detect_eval.py --data data/pets --weights yolov8n.pt --coco   # zero-shot COCO dog/cat
    python experiments/pets_detect_eval.py --data data/pets --weights runs/pets/weights/best.pt

Per tagged head: is there a detection that overlaps it by IoU >= 0.5 (a similar box), >= 0.3, or that at least
has its centre inside the tagged box? Plus the mean IoU of the best match, and false alarms on photos that
have no pet. A threshold sweep shows the trade-off.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import numpy as np
from PIL import Image


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--coco", action="store_true", help="a COCO model: keep only the cat and dog classes")
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--save-misses", type=int, default=0, help="write N images with predictions drawn, to look at")
    a = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(a.weights)
    root = Path(a.data)
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    files = sorted(k for k in meta if k.startswith(a.split + "/"))
    pos = [f for f in files if meta[f]["pets"]]
    neg = [f for f in files if not meta[f]["pets"]]
    preds = {}
    for f in files:
        res = model.predict(str(root / "images" / f), verbose=False, conf=0.05, imgsz=a.imgsz,
                            classes=[15, 16] if a.coco else None)[0]
        h, w = res.orig_shape
        preds[f] = [(*(b.xyxyn[0].tolist()), float(b.conf[0])) for b in res.boxes]   # x1 y1 x2 y2 (fractions), conf
    print(f"{a.split}: {len(pos)} photos with a pet ({sum(len(meta[f]['pets']) for f in pos)} tagged heads), {len(neg)} without")

    def gt_box(p):
        x, y, w, h = p["box"]
        return [x, y, x + w, y + h]

    print("\nconf   found IoU>=.5  found IoU>=.3  centre inside   mean IoU of best   false alarms per pet-free photo")
    for conf in (0.1, 0.25, 0.4, 0.5, 0.6):
        n = f5 = f3 = fc = 0
        ious = []
        for f in pos:
            ds = [d for d in preds[f] if d[4] >= conf]
            for p in meta[f]["pets"]:
                g = gt_box(p)
                n += 1
                best = max((iou(g, d[:4]) for d in ds), default=0.0)
                ious.append(best)
                f5 += best >= 0.5
                f3 += best >= 0.3
                fc += any(g[0] <= (d[0] + d[2]) / 2 <= g[2] and g[1] <= (d[1] + d[3]) / 2 <= g[3] for d in ds)
        fa = sum(1 for f in neg for d in preds[f] if d[4] >= conf) / max(1, len(neg))
        extra = sum(1 for f in pos for d in preds[f] if d[4] >= conf and all(iou(gt_box(p), d[:4]) < 0.1 for p in meta[f]["pets"])) / max(1, len(pos))
        print(f"{conf:.2f}   {f5/n:8.1%}      {f3/n:8.1%}      {fc/n:8.1%}        {np.mean(ious):.2f}               "
              f"{fa:.3f}  (and {extra:.3f} unmatched boxes per photo that has a pet)")

    by = collections.defaultdict(lambda: [0, 0])
    for f in pos:
        ds = [d for d in preds[f] if d[4] >= 0.25]
        for p in meta[f]["pets"]:
            by[p["name"]][1] += 1
            by[p["name"]][0] += max((iou(gt_box(p), d[:4]) for d in ds), default=0.0) >= 0.5
    print("\nfound at IoU >= 0.5, conf 0.25, by animal:", ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in by.items()))

    if a.save_misses:
        import cv2
        out = root / "viz" / Path(a.weights).parent.parent.name
        out.mkdir(parents=True, exist_ok=True)
        shown = 0
        for f in pos:
            ds = [d for d in preds[f] if d[4] >= 0.25]
            if all(max((iou(gt_box(p), d[:4]) for d in ds), default=0) >= 0.5 for p in meta[f]["pets"]) and shown >= a.save_misses // 2:
                continue
            im = cv2.imread(str(root / "images" / f))
            H, W = im.shape[:2]
            for p in meta[f]["pets"]:
                g = gt_box(p)
                cv2.rectangle(im, (int(g[0] * W), int(g[1] * H)), (int(g[2] * W), int(g[3] * H)), (0, 200, 0), 3)
            for d in ds:
                cv2.rectangle(im, (int(d[0] * W), int(d[1] * H)), (int(d[2] * W), int(d[3] * H)), (0, 0, 230), 3)
                cv2.putText(im, f"{d[4]:.2f}", (int(d[0] * W), max(20, int(d[1] * H) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 230), 2)
            im = cv2.resize(im, None, fx=900 / max(H, W), fy=900 / max(H, W))
            cv2.imwrite(str(out / f.replace("/", "_")), im)
            shown += 1
            if shown >= a.save_misses:
                break
        print(f"wrote {shown} images to {out} (green = your tag, red = detection)")


if __name__ == "__main__":
    main()
