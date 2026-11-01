# Face tagging: experiments and decisions

How the face tagging in the tagger was designed, what was measured along the way, what was decided and
why, and where to push next. Written so that whoever improves face detection or recognition later (most
likely the same person with a different model) can see what's been tried and not redo it.

All numbers come from one real library: 12,138 JPEGs of film scans, 2022 to 2026, organised
`year/month/roll`, tagged with faces in Lightroom Classic and saved to the files as XMP. They are measured
against *your tags*, so they show agreement with a person who corrected Lightroom's guesses, not agreement
with an objective truth. The scripts that produced them are in [`experiments/`](../experiments/).

## Summary

| question | answer |
| --- | --- |
| Fine-tune a model on the existing tags? | Not needed, and a poor fit. Use the tags as reference faces to match against, and as a test set. |
| Do the tags carry enough signal? | Yes: 8,454 named faces, 205 people, 57 with 20+ examples. |
| How often does the detector find a tagged person? | **92.5%** at the default setting (94.6% for faces over 160 px, 84% for 40 to 80 px, 42% under 40 px). |
| How often is a face named correctly? | **95.5%** top-1, **96.8%** top-3, on 547 faces from a later year it never saw. With the rule the tagger ships (similarity 0.40+ and detector 0.6+) it names **85.5%** of faces and is right **99.2%** of the time; the rest get suggestions to pick from. |
| Would a bigger or lower-threshold detector help? | No. Larger input: no gain and roughly 3x slower. Lower threshold: +2.5 points of recall but 2x the junk boxes, which then get matched to real people. |
| Biggest trap found | Lightroom stores face boxes in the file's **unrotated pixel frame**. Reading them as upright put 1 in 5 photos' boxes in the wrong place. |

## 1. Why not fine-tune

The first idea was to fine-tune an open model on the bounding boxes and names already entered in
Lightroom. Face tagging is three separate jobs, and the tags only help the last:

1. **Detection** finds faces (and five landmarks). A missed person is a detection failure.
2. **Embedding** aligns a face by its landmarks and turns it into a 512-number vector. A network trained on
   hundreds of thousands of identities does this, and it works on people it has never seen.
3. **Matching** compares a vector with those of faces you've named and suggests the nearest.

The case against fine-tuning is reasoning, not something that was tested. A classifier trained on your N people
has N fixed answers: it can't say "a stranger", and every new friend means retraining. Retraining the embedding
network on a few dozen people risks overfitting and losing the general ability that makes it work. Fine-tuning the
detector on your boxes would teach it that every untagged face (strangers, people you didn't bother with) is
"not a face", which is the opposite of what's wanted (section 6 shows there are thousands). The measurements
below are what justify skipping it: off-the-shelf matching already reaches 95.5%. So the tags are used two ways:
as **reference faces** (the gallery) and as a **test set**. Nothing is trained.

Models: InsightFace `buffalo_l`: SCRFD-10G detector and an ArcFace ResNet-50 trained on WebFace600K. On CPU
at 640 px it runs at about 2.4 photos per second. The GTX 1080 Ti in the test machine was not used. The
*pretrained weights are non-commercial*; the library code is MIT.

## 2. The data

`exiftool -r -ext jpg -j -struct -RegionInfo -Orientation#` over the library:

| | |
| --- | --- |
| JPEGs read | 12,138 |
| with at least one face region | 4,847 |
| face regions | 8,454 (284 unnamed) |
| distinct names | 205 (129 with 5+ faces, 57 with 20+) |
| busiest | Dean 790, Kepler 562, Alan 520, Minjea 386, Chloe 303 |
| EXIF orientation | 1: 9,118 · 6: 1,697 · 8: 703 · 3: 179 · 2: 95 · none: 335 |

Some names are **animals**: Kepler (562 regions), Elvis, Arthur, Ceviche, "Felix (dog)". They sit at 8 to 21%
detection recall while every person is at 75 to 90%+. A human face model can't be expected to find or tell them
apart, so they are excluded from every number below and from the gallery. (Note "Felix" is also a person: the dog
carries "(dog)".) Exclusion is by name, `--exclude`.

## 3. The coordinate trap

The first run measured **69%** detection recall, with rotated photos at **16%** against upright at 88%.
Drawing Lightroom's boxes (green) and the detector's (red) on the photos showed boxes sitting beside the faces on
every photo with an EXIF orientation of 5 to 8, and mirrored ones for 2 to 4.

Cause: MWG face regions in these files are stored as a **centre point plus size, as fractions of the file's
stored (unrotated) pixels**, with the EXIF orientation still to be applied. `AppliedToDimensions` holds the
upright size, which makes this easy to miss. Checked against detections, e.g. orientation 8 (turn 90° CCW):
Lightroom centre (0.611, 0.472) maps to (0.472, 0.389) and the detector found the face at (0.470, 0.393).

Applying the orientation to the regions (`regions.to_upright`):

| photos | recall before | recall after |
| --- | --- | --- |
| orientation 1 (upright) | 88.0% | 88.0% |
| 2 to 4 (mirrored / 180°) | 22.7% | 90.9% |
| 5 to 8 (turned 90°) | 16.0% | 84.0% |

(400-image sample, before pets were excluded.) The `Rotation` field in the files is ±1.57 or ±3.14 on the
rotated photos and is *not* needed once the orientation is applied.

**Consequence for writing.** The tagger writes regions in the upright frame, which is the file's frame only for
orientation 1. For rotated files it writes the keyword and *skips the region*. Writing them properly means
mapping boxes back into the stored frame, and nobody has confirmed what Lightroom does with the `Rotation` field
on files it did not write itself, so it was left out rather than guessed.

## 4. Detection

Detector confidence floor 0.4, input 640 px, on all 4,847 images with tags (`face_eval.py`), pets excluded,
7,839 people:

| face size (shorter side of Lightroom's box) | found |
| --- | --- |
| under 40 px | 45 / 107 (42%) |
| 40 to 80 px | 347 / 412 (84%) |
| 80 to 160 px | 1,303 / 1,443 (90%) |
| over 160 px | 5,557 / 5,877 (95%) |
| **all** | **7,252 / 7,839 (92.5%)** |

By orientation after the fix: upright 92.6%, mirrored 94.6%, turned 91.5%: no remaining gap.

**What the misses look like** (`--draw-misses`, 16 images): mostly the animals (before they were excluded),
faces under 40 px in the background, someone looking down, and a contact sheet scanned as one image. Hardly any
are plain, large, frontal faces.

### Detector settings, compared on one fixed 400-image sample (662 people)

| setting | recall | untagged detections (junk or real people nobody tagged) | cost |
| --- | --- | --- | --- |
| 640 px, conf 0.4 (**chosen**) | 93.1% | 223 | 0.4 s/photo |
| 1280 px, conf 0.4 | 92.6% | 320 | roughly 3x slower (0.9 vs 2.4 photos/s) |
| union of the two | 95.6% | n/a | both |
| 640 px, conf 0.2 | 95.6% | 474 | same as 640/0.4 |

- **1280 px does not help.** Small faces gain a little (40 to 80 px: 28 to 30 of 32; 80 to 160 px: 114 to 118 of
  124) but large faces *lose* (472 to 462 of 498): the detector is trained on a range of face scales and
  over-enlarging large faces hurts. Overall it is slightly worse and roughly 3x slower. (The 400-image sample
  has few small faces: 8 under 40 px, so the small-face gain is within noise.)
- **A lower threshold recovers 2.5 points of recall** (mostly large faces: 472 to 486 of 498), the same as the
  union of both sizes, but doubles the untagged boxes. Looking at the extra boxes (those scoring 0.2 to 0.4, 251
  of them) shows they are almost all not faces: ears, the back of a head, a cup, a bowl of popcorn, a blur. And
  because they carry no identity information their fingerprints sit near the middle of the gallery: 69 of the 73
  that scored as "someone you know" were matched to the same person. That is the failure to avoid: **confidently
  naming a non-face.** Rejected.

Not tried: tiling (running the detector on overlapping crops at native resolution), which targets the small-face
gap (under 40 px: 42%) directly. See "Next".

## 5. Recognition

Setup: gallery = every detected face that matches a tag in photos **before 2026**, one fingerprint per face,
people with at least 3 examples (6,442 faces, 131 people). Test = detected, tagged faces from **2026** (547
faces: 442 of people in the gallery, 105 of people who aren't). A person's score is the mean similarity of
their 3 closest gallery faces (cosine); the suggestion is the highest. This holds out a whole later period, so
it measures the realistic case of tagging a new roll against old ones. It isn't a random split, which would be
optimistic because frames from one roll are near-duplicates.

| | |
| --- | --- |
| top-1 | **95.5%** |
| top-3 | **96.8%** |

Accept only above a similarity cutoff (people-not-in-gallery wrongly named = a stranger given a friend's name):

| cutoff | faces named | correct when named | strangers wrongly named |
| --- | --- | --- | --- |
| 0.25 | 99.8% | 95.7% | 21.9% |
| 0.30 | 96.2% | 97.9% | 12.4% |
| 0.35 | 91.2% | 99.0% | 6.7% |
| **0.40** | **88.5%** | **99.2%** | **3.8%** |
| 0.45 | 79.4% | 99.4% | 1.0% |
| 0.50 | 70.6% | 99.7% | 0.0% |

Mix-ups are scattered one-offs (Egun to Dean twice, nothing systematic). Per person, the lowest of the
frequent ones is Egun at 86% (44 faces); Dean 98%, Alan 96%, Minjea 95%, Susanna 97%.

By detector confidence, at the 0.40 cutoff, real tagged faces only:

| detector confidence | share of faces | named | correct when named |
| --- | --- | --- | --- |
| 0.4 to 0.6 | 5% | about 57% | 100% |
| 0.6 to 0.7 | 9.5% | 62% | 92% |
| 0.7 to 0.8 | 26% | 87% | 99% |
| 0.8 and up | 59% | 96% | 100% |

Chewy, who is a person despite the name, was at first excluded along with the pets by mistake. Including him adds
about 120 faces to the gallery and leaves the headline figures (95.5% / 96.8%, and the 0.40 row) unchanged.

## 6. The faces nobody tagged

`untagged_faces.py`: every detection with no tag behind it (3,130, in the 4,847 images that have tags) was
compared with the gallery of tagged faces and a contact sheet of random ones was inspected.

| looks like someone you know (similarity) | untagged detections |
| --- | --- |
| 0.40 and up | 1,251 (40%) |
| 0.25 to 0.40 | 983 (31%) |
| under 0.25 | 896 (29%) |

That headline is inflated by junk. Detector confidence separates them:

- Among **0.6+ confidence** detections (1,748): 507 look like a known person at 0.40+, **274 at 0.60+, 132 at
  0.70+**. These are the **people Lightroom and you didn't tag**, which a better tool should add. Judged by
  eye on a contact sheet (24 random "known" crops, so a small sample): the faces were real people (background,
  profiles, blurry or grainy faces, a baby) and the 0.6+ matches looked right (e.g. Nicole 0.78, Alec 0.70),
  while several at 0.40 to 0.55 were wrong or junk (a baby named after an adult, a black patch).
- Among **0.4 to 0.6 confidence** detections (1,382): junk is common (a black patch, a white blur, a chair),
  and 54% still "look like someone you know". Many are junk matched to the largest cluster in the gallery
  (Dean, 391 of the 1,251). That fits the *hubness* effect, where low-quality fingerprints drift toward the
  densest part of the space. It wasn't tested directly, but the Tommy case in section 4 looks the same.

This is why a name is **only filled in automatically when both** the similarity is 0.40+ **and** the detector
is 0.6+ confident. It costs the 5% of real faces below 0.6 (they get suggestions instead) and removes the
dominant source of confident wrong names. A box under 0.6 is drawn dim.

Not measured: photos in which Lightroom found *no* face. Those have no region data and so aren't in this
analysis; they are where the largest absolute number of missed people probably is.

## 7. What was built from this

| decision | value | evidence |
| --- | --- | --- |
| detector | InsightFace buffalo_l, 640 px, confidence 0.4 | section 4 |
| similarity | mean of the 3 closest faces per person | section 5 |
| people suggested | at least 3 gallery faces each | the setting every recognition number was measured with (other values not swept) |
| auto-fill a name | similarity 0.40+ **and** detector 0.6+ | sections 5 and 6 |
| show as a choice | similarity 0.25+ | the sweep: at 0.20, 70% of strangers would be offered a name |
| pets | left out of the people gallery by name; a separate pet model learns them ([pet-tagging-experiments.md](pet-tagging-experiments.md)) | section 2 |
| gallery | one fingerprint per named face, keyed by file and box | corrections replace, never duplicate |
| output | face region **and** keyword (`XMP-dc:Subject`) | Lightroom shows a name from the region, and a keyword lets you search by it; a region alone doesn't make one |
| existing regions | carried over; only a face you name replaces its old box | found in the first end-to-end test: writing replaces the whole region list, so naming one face erased Lightroom's boxes for the other two people in the photo |
| rotated files | keyword only | section 3 |

**Checked against the shipped code.** After the tagger was built, its own pieces (the real `faces import`, the
gallery, `Gallery.suggest` and the auto-name rule) were run over the same 2026 photos, separately from the
experiment scripts:

- `faces import` over 2022 to 2025 produced 6,442 faces, the same count the experiment's gallery had (178
  people, 131 with 3+ faces).
- 2026: 579 tagged people, 547 found by the detector (94.5%), 442 of people in the gallery. Top-1 95.5%.
- Similarity 0.40+ alone: names 88.5%, correct 99.2% (identical to section 5).
- The shipped rule (also detector 0.6+): names **85.5%** (378 faces), correct **99.2%**. The detector rule
  gives up 3 points of coverage and no accuracy on real faces, in exchange for not naming junk (section 6).

The pieces: [`regions.py`](../framelog_tagger/regions.py) (no model: Lightroom's coordinate convention,
matching, XMP structure), [`faces.py`](../framelog_tagger/faces.py) (engine, gallery, import),
`/api/faces/detect` and the Write path in [`server.py`](../framelog_tagger/server.py), and the review UI in the
zoom dialog. Behaviour is described in the README.

## 8. Caveats

- **Not a head-to-head with Lightroom.** Nothing here measures Lightroom's own suggestions; it can't be done
  from the files. "Better than Lightroom" is a judgment to make on photos you know.
- **The test set is small and narrow:** 547 faces, one photographer's people, one year. The gallery's own tags
  are whatever was in Lightroom, errors you never fixed included.
- **Only faces the detector finds are scored for recognition.** The 7.5% it misses are in section 4, not in
  the 95.5%.
- **What the tagger writes was checked only down to ExifTool:** the structure is accepted, reads back
  identically (including a name containing `, [ ] { } |`), and follows the MWG regions spec. It differs a little
  from what Lightroom writes itself (Lightroom's files have no `Unit` on the area and carry a `Rotation` field).
  It has **not been opened in Lightroom**. Do that on a copy first: import a written file and look at the
  People view.
- **A region alone gives a face a name in Lightroom but not a keyword** (so the person can't be searched by
  name), per forum reports, not Adobe documentation. That's why both are written.
- **Hubness** (section 6) is mitigated by the confidence rule, not solved.
- The pretrained recognition models are licensed non-commercially.

## 9. Next

In rough order of expected payoff:

1. **Tiling for small faces.** Run the detector over overlapping crops at native resolution and merge. Targets
   the under-40-px gap (42% found). Needs the same junk screen as section 6, since small crops produce more
   non-faces. Not tried.
2. **Fix hubness properly.** Score normalisation (subtract each probe's mean similarity to a background set, as
   in AS-Norm / cohort normalisation), then re-sweep the cutoff. Should let the 0.6 detector rule loosen and
   recover the small-face names.
3. **Show the regions already in a file** as named boxes in the review view. Write now preserves them, but Find
   faces still starts from nothing, so it suggests names for faces that already have one.
4. **Draw a box by hand** for a face the detector missed. It needs its fingerprint computed from the box.
5. **Rotated files:** map boxes back into the stored frame, and find out what Lightroom wants in `Rotation`.
6. **Cluster the unnamed faces** across a roll so a stranger or new friend is named once for all their frames.
7. **Use context:** dates (kids change), and who was already confirmed on this roll.
8. **Pets** need their own model; a separate gallery per species is probably the simplest.
9. **GPU.** Detection is 0.4 s/photo on CPU; `onnxruntime-gpu` would make a full-library import minutes. The
   1080 Ti in the test machine is Pascal, which current CUDA builds of onnxruntime may no longer support.
10. A better test: hold out by roll rather than by year, with a second person's tags.

## 10. What it costs to run

Measured on the development machine (Windows 11, 16 cores, no GPU used).

| | |
| --- | --- |
| Python environment | about 410 MB in total, most of it the face packages (OpenCV 112, SciPy 88, NumPy 44, onnxruntime 42, scikit-image 21, onnx 19) |
| Model on disk | about 600 MB in `~/.insightface`: the 275 MB download zip stays next to the unpacked files, and only 2 of its 5 networks (16 MB detector, 166 MB recognition) are used. About 420 MB of that could be deleted. |
| Gallery | about 2 KB per face: 12 MB for 6,442 faces |
| Memory | about 450 MB resident while detecting, 700 MB peak. The first photo takes 2.8 s while the model loads. |
| Speed | about 0.13 s per photo on a decoded image; 0.4 s per photo in a real run, including reading the scan. CPU only. |
| First run | needs internet once, to download the 275 MB model from GitHub. Nothing is sent anywhere afterwards. |
| Python | the packages install and import on 3.11, 3.12, 3.13 and 3.14 as plain wheels (no compiler needed). Detection was run on 3.11 only. |

What would make it unusable: a machine too slow to want 0.4 s per photo, under about 2 GB of free memory, or no
internet on the very first run. A GPU would make a first pass over a big library minutes instead of an hour, and
`onnxruntime-gpu` is the way to try it (not done: the 1080 Ti in the test machine is Pascal, which current CUDA
builds may not support).

## 11. Reproducing

All from `tagger/`, Python 3.11 (the scripts declare their own dependencies):

```bash
exiftool -r -ext jpg -j -struct -RegionInfo -Orientation# <photos> > regions.json

# detection recall, recognition accuracy, check images (green = Lightroom, red = detector)
uv run experiments/face_eval.py --regions regions.json --cache-dir cache --holdout-from 2026 \
    --exclude Kepler "Felix (dog)" Elvis Arthur Ceviche --draw 12 --draw-misses 16

# compare detector settings on the same random sample (detections are cached per setting)
uv run experiments/face_eval.py --regions regions.json --cache-dir cmp --limit 400 --det-size 1280 --exclude Kepler ...
uv run experiments/face_eval.py --regions regions.json --cache-dir cmp --limit 400 --det-thresh 0.2 --exclude Kepler ...

# the untagged faces
uv run experiments/untagged_faces.py --regions regions.json --cache cache/faces_640_0.4.pkl \
    --exclude Kepler "Felix (dog)" Elvis Arthur Ceviche --det-min 0.6 --sheet sheet.jpg
```

The full-library detection pass takes about 35 minutes on CPU. `experiments/` is deliberately standalone: it
duplicates a few small helpers from `framelog_tagger/regions.py` so it keeps working however the package
changes.
