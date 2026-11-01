"""The whole pet pipeline as the app runs it: ONNX detector -> head crop -> DINOv2 fingerprint -> gallery match.

Unlike pets_detect_eval.py and pets_identity_eval.py, which score the halves separately (the detector with
ultralytics, the embeddings with PyTorch), this runs framelog_tagger.pets.PetEngine, so it checks the
export, the decoding and the preprocessing too. Use a dataset from pets_dataset.py with the detector that
was trained on its train split.

    python experiments/pets_product_check.py --data data/pets_rand --detector best.onnx [--embedder dino.onnx]

The gallery is every tagged head in the train and val splits (fingerprinted by the same engine). Each test
photo is then run through the engine and every detection is named by the app's own rule.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framelog_tagger import pets            # noqa: E402
from framelog_tagger.faces import Gallery   # noqa: E402
from framelog_tagger.regions import FaceBox, iou   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--detector", required=True, help="the ONNX detector trained on this dataset's train split")
    ap.add_argument("--embedder", help="an ONNX DINOv2 file; default is the one the app downloads")
    ap.add_argument("--gallery-file", default=None)
    a = ap.parse_args()

    root = Path(a.data)
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    engine = pets.PetEngine(det_file=Path(a.detector), embed_file=Path(a.embedder) if a.embedder else None)
    gallery_file = Path(a.gallery_file) if a.gallery_file else root / "gallery_check.npz"
    gallery_file.unlink(missing_ok=True)   # always start empty: the gallery is rebuilt from this dataset's train split
    gallery = Gallery(gallery_file)
    engine._load()
    for f, m in meta.items():
        if f.startswith("test/") or not m["pets"]:
            continue
        im = Image.open(root / "images" / f).convert("RGB")
        for p in m["pets"]:
            x, y, w, h = p["box"]
            box = FaceBox(x, y, w, h)
            gallery.add(p["name"], engine.embed(im, box), f, box)
    print("gallery:", dict(gallery.people()))

    pos = [f for f, m in meta.items() if f.startswith("test/") and m["pets"]]
    neg = [f for f, m in meta.items() if f.startswith("test/") and not m["pets"]]
    found = named_ok = named_wrong = named_none = 0
    per = collections.defaultdict(lambda: [0, 0, 0])      # animal -> [tagged heads, found, named correctly]
    kepler_fp = kepler_tp = 0
    cuts = (0.55, 0.6, 0.65, 0.7, 0.75)
    by_cut = {c: collections.Counter() for c in cuts}
    for f in pos:
        _, dets = engine.detect(root / "images" / f)
        tagged = [(p["name"], FaceBox(*p["box"])) for p in meta[f]["pets"]]
        for name, gt in tagged:
            per[name][0] += 1
            best = max(dets, key=lambda d: iou(gt, d.box), default=None)
            if best is None or iou(gt, best.box) < 0.5:
                continue
            per[name][1] += 1
            (top, sc), = gallery.suggest(best.emb, n=1) or [(None, 0.0)]
            for c in cuts:
                if best.score >= pets.AUTO_DET and sc >= c:
                    by_cut[c]["named"] += 1
                    by_cut[c]["right" if top == name else "wrong"] += 1
                    if name == "Kepler":
                        by_cut[c]["kepler_named_right"] += top == "Kepler"
                if name == "Kepler":
                    by_cut[c]["kepler_total"] += 1
            per[name][2] += top == name
    n_tagged = sum(v[0] for v in per.values())
    n_found = sum(v[1] for v in per.values())
    fa = sum(1 for f in neg for d in engine.detect(root / "images" / f)[1] if d.score >= pets.AUTO_DET)
    print(f"\ntest: {len(pos)} photos with a pet ({n_tagged} heads), {len(neg)} without")
    print(f"detected at IoU >= 0.5: {n_found}/{n_tagged} ({n_found / n_tagged:.1%}); confident (>= {pets.AUTO_DET}) detections on pet-free photos: {fa} in {len(neg)} photos")
    print("by animal (heads, found, named right by top match):", {k: tuple(v) for k, v in per.items()})
    print("\nauto-naming rule: detector >= %.2f and similarity >= cutoff   (of the detected heads)" % pets.AUTO_DET)
    for c in cuts:
        b = by_cut[c]
        print(f"  cutoff {c:.2f}: named {b['named']}/{n_found} ({b['named'] / max(1, n_found):.1%}), right {b['right'] / max(1, b['named']):.1%};  "
              f"Kepler named correctly {b['kepler_named_right']}/{b['kepler_total']}")


if __name__ == "__main__":
    main()
