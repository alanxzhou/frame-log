"""Can a generic image-embedding model tell your pets apart from their head crops?

Uses a dataset from pets_dataset.py. The gallery is the tagged heads in the train and val splits, the queries
are the tagged heads in the test split (cropped from your tag, i.e. perfect detection: this isolates the
recognition half). Matching is the same as for faces: each animal is scored by the mean cosine similarity
of its 3 closest gallery crops.

    python experiments/pets_identity_eval.py --data data/pets_rand --model dinov2-small
    python experiments/pets_identity_eval.py --data data/pets_rand --model clip-b32
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image

MODELS = {
    "dinov2-small": "facebook/dinov2-small",
    "dinov2-base": "facebook/dinov2-base",
    "clip-b32": "openai/clip-vit-base-patch32",
}


def load_model(name: str, device: str):
    from transformers import AutoImageProcessor, AutoModel, CLIPModel, CLIPProcessor
    repo = MODELS[name]
    if name.startswith("clip"):
        m = CLIPModel.from_pretrained(repo).to(device).eval()
        proc = CLIPProcessor.from_pretrained(repo)

        def embed(ims):
            x = proc(images=ims, return_tensors="pt")["pixel_values"].to(device)
            with torch.no_grad():
                out = m.get_image_features(pixel_values=x)
                return (out if torch.is_tensor(out) else out.pooler_output).float().cpu().numpy()
    else:
        m = AutoModel.from_pretrained(repo).to(device).eval()
        proc = AutoImageProcessor.from_pretrained(repo)

        def embed(ims):
            x = proc(images=ims, return_tensors="pt")["pixel_values"].to(device)
            with torch.no_grad():
                h = m(pixel_values=x).last_hidden_state
            return torch.cat([h[:, 0], h[:, 1:].mean(1)], dim=1).float().cpu().numpy()   # class token + mean patch
    return embed


def crop(im: Image.Image, box, margin: float) -> Image.Image:
    x, y, w, h = box
    W, H = im.size
    cx, cy, s = (x + w / 2) * W, (y + h / 2) * H, max(w * W, h * H) * (1 + margin) / 2
    return im.crop((int(cx - s), int(cy - s), int(cx + s), int(cy + s))).resize((224, 224), Image.BICUBIC)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", default="dinov2-small", choices=list(MODELS))
    ap.add_argument("--margin", type=float, default=0.3, help="context around the tagged box, as a fraction of its size")
    ap.add_argument("--min-gallery", type=int, default=3)
    ap.add_argument("--unknown", nargs="*", default=[], help="animals to leave out of the gallery and use as strangers: "
                    "how often are they wrongly named as someone in the gallery?")
    a = ap.parse_args()

    root = Path(a.data)
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    embed = load_model(a.model, "cuda" if torch.cuda.is_available() else "cpu")
    rows = {"gallery": [], "test": [], "stranger": []}
    for f, m in meta.items():
        split = "test" if f.startswith("test/") else "gallery"
        if not m["pets"]:
            continue
        im = Image.open(root / "images" / f).convert("RGB")
        for p in m["pets"]:
            rows["stranger" if p["name"] in a.unknown else split].append((p["name"], crop(im, p["box"], a.margin)))
    print(f"{a.model}: gallery {len(rows['gallery'])} crops, test {len(rows['test'])} crops")
    E = {}
    for split, items in rows.items():
        if not items:
            E[split] = np.zeros((0, 1)); continue
        vecs = np.concatenate([embed([c for _, c in items[i:i + 32]]) for i in range(0, len(items), 32)])
        E[split] = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    gal_names = np.array([n for n, _ in rows["gallery"]])
    test_names = [n for n, _ in rows["test"]]
    people = {n: np.where(gal_names == n)[0] for n in set(gal_names)}
    known = {n: ix for n, ix in people.items() if len(ix) >= a.min_gallery}
    print("gallery crops per animal:", dict(collections.Counter(gal_names)))

    res = []   # (true, predicted, score)
    for q, true in zip(E["test"], test_names):
        sims = E["gallery"] @ q
        sc = {n: float(np.sort(sims[ix])[-3:].mean()) for n, ix in known.items()}
        pred = max(sc, key=sc.get)
        res.append((true, pred, sc[pred], sc))
    inknown = [r for r in res if r[0] in known]
    print(f"\nanimals in the gallery: {len(inknown)} test crops; top-1 {np.mean([r[0] == r[1] for r in inknown]):.1%}")
    per = collections.defaultdict(list)
    for r in inknown:
        per[r[0]].append(r[0] == r[1])
    print("per animal:", ", ".join(f"{k} {sum(v)}/{len(v)}" for k, v in sorted(per.items(), key=lambda kv: -len(kv[1]))))
    conf = collections.Counter((r[0], r[1]) for r in inknown if r[0] != r[1])
    print("mix-ups (true -> guessed):", dict(conf.most_common(6)))
    for q, true in zip(E["stranger"], [n for n, _ in rows["stranger"]]):
        sims = E["gallery"] @ q
        sc = {n: float(np.sort(sims[ix])[-3:].mean()) for n, ix in known.items()}
        pred = max(sc, key=sc.get)
        res.append((true, pred, sc[pred], sc))
    unknown = [r for r in res if r[0] not in known]
    strangers = ", ".join(sorted({r[0] for r in unknown}))
    print(f"animals not in the gallery: {len(unknown)} crops" + (f" ({strangers})" if unknown else ""))
    print("\ncutoff   named    correct when named   unknown animals wrongly named")
    for t in np.arange(0.3, 0.95, 0.1):
        ans = [r for r in inknown if r[2] >= t]
        wrong = np.mean([r[2] >= t for r in unknown]) if unknown else float("nan")
        ok = np.mean([r[0] == r[1] for r in ans]) if ans else float("nan")
        print(f" {t:.2f}   {len(ans) / max(1, len(inknown)):6.1%}   {ok:8.1%}             {wrong:6.1%}")
    k = [r for r in inknown if r[0] == "Kepler"]
    print(f"\nKepler: {sum(r[1] == 'Kepler' for r in k)}/{len(k)} found as Kepler; other animals mistaken for Kepler: "
          f"{sum(r[1] == 'Kepler' for r in inknown if r[0] != 'Kepler')}/{sum(r[0] != 'Kepler' for r in inknown)}")


if __name__ == "__main__":
    main()
