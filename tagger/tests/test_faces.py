"""The gallery and the import, with made-up fingerprints so no model is needed."""

import numpy as np
import pytest

from framelog_tagger.faces import ACCEPT, Detection, Gallery, import_tagged
from framelog_tagger.regions import FaceBox, TaggedImage

RNG = np.random.default_rng(7)
BASES = {n: RNG.normal(size=512) for n in ("Ana", "Ben", "Cy")}


def fp(name, noise=0.15):
    """A unit fingerprint near the person's, the way two photos of one face land close together."""
    v = BASES[name] / np.linalg.norm(BASES[name]) + RNG.normal(size=512) * noise / np.sqrt(512)
    return (v / np.linalg.norm(v)).astype(np.float32)


def box(i=0):
    return FaceBox(0.1 * i, 0.2, 0.1, 0.1)


@pytest.fixture
def gallery(tmp_path):
    g = Gallery(tmp_path / "faces.npz")
    for name in BASES:
        for i in range(4):
            g.add(name, fp(name), f"D:/p/{name}{i}.jpg", box())
    return g


def test_suggest_ranks_the_right_person_first(gallery):
    for name in BASES:
        best = gallery.suggest(fp(name))
        assert best[0][0] == name and best[0][1] > ACCEPT
        assert len(best) == 3 and best[0][1] > best[1][1]


def test_a_stranger_scores_low(gallery):
    stranger = RNG.normal(size=512).astype(np.float32)
    stranger /= np.linalg.norm(stranger)
    assert gallery.suggest(stranger)[0][1] < 0.25


def test_people_need_enough_examples_to_be_suggested(gallery):
    gallery.add("Dot", fp("Ana"), "D:/p/dot.jpg", box())   # one example, and it looks exactly like Ana
    assert "Dot" not in [n for n, _ in gallery.suggest(fp("Ana"), n=10)]
    assert ("Dot", 1) in gallery.people()
    assert "Dot" in [n for n, _ in gallery.suggest(fp("Ana"), n=10, min_examples=1)]


def test_naming_the_same_face_again_corrects_it(gallery):
    n = len(gallery)
    gallery.add("Ben", fp("Ben"), "D:\\p\\Ana0.jpg", box())   # same face as Ana's first, filed under Ben now; Windows spelling
    assert len(gallery) == n
    assert dict(gallery.people())["Ana"] == 3 and dict(gallery.people())["Ben"] == 5


def test_forget_removes_a_person(gallery):
    assert gallery.forget("ana") == 4
    assert "Ana" not in dict(gallery.people()) and len(gallery) == 8
    assert gallery.suggest(fp("Ana"))[0][0] != "Ana"


def test_save_and_load_round_trip(gallery, tmp_path):
    gallery.done.add("D:/p/seen.jpg")
    gallery.save()
    again = Gallery(tmp_path / "faces.npz")
    assert len(again) == len(gallery) and again.done == {"D:/p/seen.jpg"}
    assert again.suggest(fp("Cy"))[0][0] == "Cy"
    assert not (tmp_path / "faces.tmp.npz").exists()


def test_a_missing_or_broken_file_is_an_empty_gallery(tmp_path):
    assert len(Gallery(tmp_path / "none.npz")) == 0
    (tmp_path / "bad.npz").write_bytes(b"not an archive")
    assert len(Gallery(tmp_path / "bad.npz")) == 0


class FakeEngine:
    """Finds one face per image, always at the same place, with the fingerprint registered for the file."""

    def __init__(self, fps):
        self.fps, self.calls = fps, []

    def detect(self, path):
        self.calls.append(path)
        if path.endswith("broken.jpg"):
            raise OSError("unreadable")
        return (100, 100), [Detection(FaceBox(0.4, 0.4, 0.2, 0.2), 0.9, self.fps[path])]


def test_import_learns_from_tagged_boxes_skips_pets_and_resumes(tmp_path):
    fps = {"D:/p/1.jpg": fp("Ana"), "D:/p/2.jpg": fp("Ben"), "D:/p/3.jpg": fp("Cy"), "D:/p/4.jpg": fp("Cy")}
    tagged = lambda name: [FaceBox(0.35, 0.35, 0.3, 0.3, name)]
    images = [
        TaggedImage("D:/p/1.jpg", 1, tagged("Ana")),
        TaggedImage("D:/p/2.jpg", 1, tagged("Ben")),
        TaggedImage("D:/p/3.jpg", 1, tagged("Rex")),                              # a pet: excluded
        TaggedImage("D:/p/4.jpg", 1, [FaceBox(0.0, 0.0, 0.1, 0.1, "Cy")]),       # tagged somewhere the detector didn't look
        TaggedImage("D:/p/broken.jpg", 1, tagged("Ana")),
    ]
    g = Gallery(tmp_path / "faces.npz")
    engine = FakeEngine(fps)
    stats = import_tagged(engine, g, images, exclude=["rex"])
    assert stats == {"images": 5, "skipped": 0, "added": 2, "unmatched": 1}
    assert dict(g.people()) == {"Ana": 1, "Ben": 1}
    assert not any(c.endswith("3.jpg") for c in engine.calls)   # a pet's photo isn't even run through the model

    engine2 = FakeEngine(fps)
    stats = import_tagged(engine2, Gallery(tmp_path / "faces.npz"), images, exclude=["rex"])
    assert stats["skipped"] == 4 and stats["added"] == 0         # everything but the unreadable file is already done
    assert engine2.calls == ["D:/p/broken.jpg"]
    assert (tmp_path / "faces.npz").exists()
