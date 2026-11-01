# Pet tagging: experiments and decisions

How the pet detection in the tagger was built, what was measured, and where to push next. Same library and
method as [face-tagging-experiments.md](face-tagging-experiments.md): the tags already entered in Lightroom are
used as reference examples and as a test set, and everything is measured against them. Scripts are in
[`experiments/`](../experiments/) (`pets_*.py`).

## Summary

| question | answer |
| --- | --- |
| Do the tags carry enough signal? | For Kepler, yes: 562 tagged heads. The other five pets have 6 to 19 each, which is not. |
| Does an off-the-shelf dog and cat detector work? | It finds the animal but boxes the whole body. Against your head boxes: 1 of 146 Kepler photos matched at IoU 0.5. |
| Does fine-tuning on your own boxes fix that? | Yes. A small YOLOv8 fine-tuned on the tagged heads finds Kepler at a similar box in **81 to 83%** of held-out photos, with about 1 confident false detection per 75 photos without a pet. |
| Can it tell your pets apart? | Kepler vs everything else, yes: DINOv2-small fingerprints named **145 of 145** held-out Kepler heads correctly. With the app's rule, every head it names automatically is right. |
| End to end for Kepler | found 119 of 145 held-out heads (82%); of those found, **81% are named automatically at 100% accuracy**, the rest get suggestions. |
| The other pets | Weak (see below). They have too few photos to learn a detector or a fingerprint from. |

## 1. The data

Lightroom saves a pet's face as an ordinary face region with the pet's name, so the boxes are head boxes. In
this library:

| animal | tagged heads | photos |
| --- | --- | --- |
| Kepler | 562 (2022: 139, 2023: 179, 2024: 98, 2025: 129, 2026: 17) | 562 |
| Elvis | 19 | 19 |
| Ceviche | 14 | 14 |
| Felix (dog) | 12 | 12 |
| Arthur | 8 | 8 |
| Pippa | 6 | 6 |

616 photos in all. `pets_dataset.py` turns them into a one-class ("pet") detection set. Negatives are 300 photos
that have tags for people only, screened with a generic detector so that an untagged animal isn't taught as
"no pet". Boxes use the same stored-versus-upright frame handling as the faces (see the face doc, section 3).

Two splits, because they answer different questions:

* **By time** (2025 and later is the test set, as for faces): the realistic case, but it leaves the rarer pets with
  almost nothing to train on (Ceviche: 2 training photos, 11 test).
* **Random** (a quarter held out): the fairer measure of the rarer pets.

## 2. Zero-shot: a COCO dog and cat detector

YOLOv8n pretrained on COCO, classes "cat" and "dog", on the time-split test set (174 photos with a pet, 108 without):

| confidence | found at IoU 0.5 | found at IoU 0.3 | centre inside your box | mean IoU |
| --- | --- | --- | --- | --- |
| 0.25 | 0.6% | 10.3% | 21.8% | 0.11 |

It does find the animals (no false detections on pet-free photos), but as whole bodies, while your boxes are tight
on the head. That is a box-style mismatch, not a failure to see the dog, which is why the next step is to fine-tune.

## 3. Fine-tuning a head detector on your boxes

YOLOv8n starting from the COCO weights, 60 epochs, 960 px, on a GTX 1080 Ti (about 12 s per epoch).

**Time split** (2025 and later held out), found at IoU 0.5 (a "similar box"):

| confidence | found | mean IoU | false alarms per pet-free photo |
| --- | --- | --- | --- |
| 0.25 | 73.6% | 0.57 | 0.139 |
| 0.40 | 69.5% | 0.54 | 0.056 |
| 0.50 | 65.5% | 0.51 | 0.037 |

By animal at 0.25: **Kepler 121/146 (83%)**, Felix 4/12, Ceviche 1/12, Arthur 1/3, Pippa 1/1.

**Random split**, 0.25: found 77.7% overall; **Kepler 118/145 (81%)**; Elvis 1/6, Ceviche 1/3, Felix (dog) 1/2,
Arthur 1/1. Confident (0.5+) false detections on pet-free photos: 1 in 75.

**What the Kepler misses are** (random split, 27 of 145): 10 are heads under 40 px, far away or in a corner; the other
17 are larger (median 126 px) and look like poses where the face isn't visible, or tags that are looser than the
detector's box.

**Resolution.** The same model trained and tested at 1280 px finds 77.6% at confidence 0.25 against 73.6% at 960, which
looks like a gain, but the false alarms also double (0.278 vs 0.139 per pet-free photo). Compared at the same false
alarm rate they are identical (73.6% found at 0.139 for both), so it only moves along the same curve while costing
more time. 960 stays. A larger network (YOLOv8s at 1280) was started and stopped before finishing; see section 7.

## 4. Telling the animals apart

Given a head box, which animal? `pets_identity_eval.py`: crop the head with some context, fingerprint it with a
general image-embedding model, and match against labelled crops like the faces do (an animal is scored by the mean
similarity of its 3 closest gallery crops). Random split; the gallery is the train crops, the queries the held-out
ones, with perfect boxes so that this isolates recognition.

| embedding model | Kepler found as Kepler | strangers wrongly named Kepler at cutoff 0.6 / 0.7 |
| --- | --- | --- |
| **DINOv2-small** | 145 / 145 | 6.8% / 0.0% |
| CLIP ViT-B/32 | 145 / 145 | 100% / 100% |

"Strangers" are the other five animals, held out of the gallery and treated as unknown (59 crops). CLIP's
fingerprints are all close to one another, so any cutoff that keeps Kepler also names every stranger as Kepler.
DINOv2 separates them.

With the other animals left in the gallery, top-1 is 98.1% on the held-out crops, and the only errors are 3 of 6
Elvis crops taken for Kepler.

## 5. The whole thing as the app runs it

`pets_product_check.py` runs `framelog_tagger.pets.PetEngine` itself (ONNX detector, head crop, ONNX DINOv2,
gallery match), so it also checks the export and the preprocessing. Random split, detector trained on the train
split only:

* The export reproduces the study: **122 of 157** held-out heads found at IoU 0.5, the same as the scorer used in
  section 3.
* Automatic naming needs the detector at 0.5 or more and a similarity at or above the cutoff. At **0.70: 99 of the
  122 found heads (81%) are named, all correctly** (Kepler: 97 named right out of 119 found).

| similarity cutoff | named, of those found | right |
| --- | --- | --- |
| 0.55 | 91% | 100% |
| 0.65 | 87% | 100% |
| **0.70** | **81%** | **100%** |
| 0.75 | 61% | 100% |

The 24 MB quantized DINOv2 file gives the same result as the 88 MB full-size one (99 vs 100 named at 0.70), so the
app downloads the small one.

## 6. What shipped

| decision | value |
| --- | --- |
| detector | YOLOv8n fine-tuned on **all** the tagged pet heads (no holdout), exported to ONNX at 960 px: `framelog_tagger/models/pet_head.onnx`, about 12 MB |
| embedder | DINOv2-small, int8 (24 MB), downloaded from the onnx-community mirror on first use |
| detection confidence | 0.25 to show a box; 0.5 and up for a name to be filled in automatically |
| similarity | 0.55 to offer a name as a choice; 0.70 to fill it in |
| gallery | separate from the people gallery (`pets.npz`): fingerprints from different models can't be compared |
| from Lightroom | the names listed as pets on the setup screen are learned into it by the same background sync |
| overlap | a face-model box that lands on a detected pet head is dropped: the face model fires on dogs now and then |
| runtime | `onnxruntime` only. No PyTorch or ultralytics in the app. About 0.29 s per scan for pets on a CPU (face model: 0.13 s on the same scans), plus about 2 s the first time to load the models. |

Checked in the app (Chrome, a scratch config): giving the folder and the pet name once learned 82 Kepler faces from 90
Kepler photos plus 64 faces of 7 people, Kepler appeared only in the pet gallery, and Find faces on a roll of Kepler photos
put head boxes on him, named two automatically, and offered "Kepler 62" on a third. Two others in the roll were heads of
19 and 33 px in a 3,000 px photo, which the detector doesn't find: that is the tiny-head limit above, not a pipeline fault.

## 7. Caveats

* **Only Kepler is well measured.** The other pets have 6 to 19 photos each. Their numbers above (Elvis 1/6 and so
  on) are what a detector trained on 70 to 80% of that few photos does, and the shipped detector was trained on all
  of them, so it can't be scored. Expect Kepler to work and the others to mostly need a click until more of their
  photos are tagged.
* **A fingerprint needs examples.** Pets are only suggested after 3 confirmed photos (the same rule as people).
* **The held-out set is small and one animal dominates it** (145 of 157 heads are Kepler).
* **The detector file is derived from tags of your photos** (it contains weights, not images), and was trained with
  Ultralytics, whose licence (AGPL-3.0) covers models trained with it. Fine for use on your own photos; worth
  knowing before the repository is shared or the file redistributed.
* A larger network (YOLOv8s at 1280 px) was started twice and not completed. The first time `C:` filled up while
  it trained, and the second time the system ran short of commit memory and it was stopped by hand; in both
  cases stopping it gave the space back, so the Windows page file was the likely cause. A bigger network is
  untested, not shown to be no better.

## 8. Next

1. **Tag more of the other pets, then retrain** (below). This is the cheapest improvement for them.
2. **Tiny heads.** About a third of Kepler's misses are under 40 px. Running the detector on overlapping tiles is
   the usual answer; it would need the same junk check as the faces.
3. **A bigger network**, on a machine where it can finish (section 7).
4. **Cats versus dogs.** The model has a single "pet" class; a species class would let the gallery avoid cross-species
   comparisons altogether.
5. Draw-a-box-by-hand for the heads the detector misses (also on the face list).

## 9. Reproducing and retraining

The study uses PyTorch and Ultralytics in a throwaway environment. The app does not.

```bash
# a throwaway environment with CUDA PyTorch (a GTX 10-series needs the cu118 build) and ultralytics
uv venv petenv --python 3.11
uv pip install --python petenv torch torchvision --index-url https://download.pytorch.org/whl/cu118
uv pip install --python petenv ultralytics onnx onnxruntime pillow numpy transformers

# the dataset from regions.json (see the face doc), with your own pet names
python experiments/pets_dataset.py --regions regions.json --out data/pets --pets Kepler Pippa "Felix (dog)" Ceviche Elvis Arthur
python experiments/pets_dataset.py --regions regions.json --out data/pets_rand --random-test 0.25 --pets Kepler Pippa ...

# zero-shot baseline, then a trained detector
python experiments/pets_detect_eval.py --data data/pets --weights yolov8n.pt --coco
yolo detect train data=data/pets/data.yaml model=yolov8n.pt imgsz=960 epochs=60 batch=16 device=0 workers=0
python experiments/pets_detect_eval.py --data data/pets --weights runs/detect/train/weights/best.pt

# who is who, and the whole pipeline as the app runs it
python experiments/pets_identity_eval.py --data data/pets_rand --model dinov2-small --unknown Elvis Ceviche Arthur Pippa "Felix (dog)"
python experiments/pets_product_check.py --data data/pets_rand --detector runs/detect/train/weights/best.onnx
```

To ship a new detector: build a dataset with no test split (`--test-from 2100`), train as above, then

```bash
yolo export model=runs/detect/train/weights/best.pt format=onnx imgsz=960 opset=12 simplify=False
cp runs/detect/train/weights/best.onnx framelog_tagger/models/pet_head.onnx
```

Training on a machine with little free memory: set `workers=0`, run one training at a time, and keep an eye on the
Windows commit charge (`workers` and the CUDA allocator reserve a lot).
