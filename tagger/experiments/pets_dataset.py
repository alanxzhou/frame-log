"""Build a YOLO dataset of pet heads from the animals already tagged in Lightroom.

Lightroom saves a pet's face as an ordinary face region with the pet's name, so the boxes you drew are
head boxes. This turns them into a one-class ("pet") detection set, plus `meta.json` recording which
animal each box is, for the identity experiments.

Splits are by time, like the face experiments: photos from --test-from on are the test set, the rest is
split into train and a small validation set for the trainer. `--random-test 0.25` holds out a random quarter instead.

Negatives (photos with no pet) are sampled from tagged photos with no pet name and screened with a generic
COCO detector, so an untagged dog doesn't get trained on as "no pet".

Run with a Python that has ultralytics and torch (see docs/pet-tagging-experiments.md):

    python experiments/pets_dataset.py --regions regions.json --out data/pets \
        --pets Kepler Pippa "Felix (dog)" Ceviche Elvis Arthur
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageOps

import face_eval as fe

COCO_ANIMALS = {15: "cat", 16: "dog"}


def upright_image(path: str, max_side: int) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    k = max_side / max(im.size)
    return im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS) if k < 1 else im


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pets", nargs="+", required=True, help="names in the tags that are animals")
    ap.add_argument("--test-from", default="2025", help='photos from "YYYY" or "YYYY/MM" on are the test set')
    ap.add_argument("--random-test", type=float, default=0.0,
                    help="instead of splitting by time, hold out this fraction of photos at random (to measure rare pets, "
                         "whose photos a time split can leave almost entirely in the test set)")
    ap.add_argument("--negatives", type=int, default=300, help="pet-free photos to add, in total")
    ap.add_argument("--max-side", type=int, default=1280)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    pets = set(a.pets)
    items = fe.load_items(a.regions)   # boxes already mapped into the upright picture
    pos = [i for i in items if any(r.get("Name") in pets for r in i["regions"])]
    pool = [i for i in items if not any(r.get("Name") in pets for r in i["regions"])]
    rng.shuffle(pool)

    from ultralytics import YOLO
    coco = YOLO("yolov8n.pt")
    negs = []
    for it in pool:   # screen with a generic detector so untagged animals aren't taught as background
        if len(negs) >= a.negatives:
            break
        im = upright_image(it["path"], 640)
        res = coco.predict(im, verbose=False, conf=0.15, classes=list(COCO_ANIMALS))[0]
        if len(res.boxes) == 0:
            negs.append(it)
    print(f"{len(pos)} photos with a tagged pet, {len(negs)} pet-free photos")

    meta, counts = {}, {}
    val_every = 10
    for kind, group in (("pos", pos), ("neg", negs)):
        rng.shuffle(group)
        k = 0
        for it in group:
            if (a.random_test and rng.random() < a.random_test) or (not a.random_test and it["ym"] >= a.test_from):
                split = "test"
            else:
                k += 1
                split = "val" if k % val_every == 0 else "train"
            name = f"{split}_{kind}_{counts.get((split, kind), 0):04d}"
            counts[(split, kind)] = counts.get((split, kind), 0) + 1
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            im = upright_image(it["path"], a.max_side)
            im.save(out / "images" / split / f"{name}.jpg", quality=92)
            boxes = []
            lines = []
            for r in it["regions"]:
                if r.get("Name") in pets:
                    A = r["Area"]
                    cx, cy = min(max(A["X"], 0), 1), min(max(A["Y"], 0), 1)
                    w, h = min(A["W"], 1), min(A["H"], 1)
                    lines.append(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
                    boxes.append({"name": r["Name"], "box": [A["X"] - A["W"] / 2, A["Y"] - A["H"] / 2, A["W"], A["H"]]})
            (out / "labels" / split / f"{name}.txt").write_text("\n".join(lines), encoding="utf-8")
            meta[f"{split}/{name}.jpg"] = {"source": it["path"], "ym": it["ym"], "pets": boxes}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (out / "data.yaml").write_text(
        f"path: {out.resolve().as_posix()}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: pet\n", encoding="utf-8")
    for (split, kind), n in sorted(counts.items()):
        print(f"  {split:<5} {kind}: {n}")


if __name__ == "__main__":
    main()
