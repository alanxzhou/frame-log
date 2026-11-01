"""The gallery and the import, with made-up fingerprints so no model is needed."""

import threading
import warnings

import numpy as np
import pytest

from framelog_tagger import config, faces
from framelog_tagger.faces import ACCEPT, Detection, Gallery, Sync, import_tagged, update_settings
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


def test_insightface_alignment_warning_is_silenced():
    """InsightFace's face alignment calls a scikit-image function that is deprecated; it must not print per face."""
    from insightface.utils import face_align
    img = np.zeros((200, 200, 3), np.uint8)
    kps = np.array([[70, 80], [130, 80], [100, 110], [75, 140], [125, 140]], dtype=np.float32)
    with warnings.catch_warnings(record=True) as caught:   # keeps whatever filters are installed
        faces.silence_insightface_warning()                # (a test runner resets them, so apply ours as the model load does)
        face_align.norm_crop(img, kps)
    assert not [w for w in caught if issubclass(w.category, FutureWarning)]


def test_the_gallery_survives_a_background_writer(tmp_path):
    g = Gallery(tmp_path / "faces.npz")
    for i in range(3):
        g.add("Ana", fp("Ana"), f"D:/p/seed{i}.jpg", box())
    errors = []

    def writer():
        try:
            for i in range(300):
                g.add("Ben", fp("Ben"), f"D:/p/b{i}.jpg", box())
                g.mark_done(f"D:/p/b{i}.jpg")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=writer)
    t.start()
    for _ in range(300):   # the UI reading while the sync writes
        g.suggest(fp("Ana"))
        g.people()
        g.save()
    t.join()
    assert not errors and dict(g.people())["Ben"] == 300


def test_settings_round_trip_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    assert config.load_faces_settings() == {"library": [], "pets": [], "synced": ""}
    config.save_faces_settings({"library": ["D:\\photos"], "pets": ["Kepler"], "synced": "2026-11-01T00:00:00Z", "junk": 1})
    assert config.load_faces_settings() == {"library": ["D:\\photos"], "pets": ["Kepler"], "synced": "2026-11-01T00:00:00Z"}
    (tmp_path / "faces.json").write_text("not json")
    assert config.load_faces_settings()["library"] == []


def test_changing_who_is_not_a_person_prunes_and_rereads(tmp_path, monkeypatch, gallery):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    gallery.mark_done("D:/p/1.jpg")
    settings = {"library": [], "pets": [], "synced": ""}
    removed = update_settings(gallery, settings, ["D:/photos"], ["ana"])      # Ana turns out to be a dog
    assert removed == 4 and "Ana" not in dict(gallery.people())
    assert settings["library"] == ["D:/photos"] and settings["pets"] == ["ana"]
    assert gallery.is_done("D:/p/1.jpg")                                       # nothing to re-read yet
    update_settings(gallery, settings, ["D:/photos"], [])                      # ...no, a person: their photos need reading again
    assert not gallery.is_done("D:/p/1.jpg") and settings["pets"] == []
    assert config.load_faces_settings()["pets"] == []


def _library_images():
    fps = {"D:/lib/1.jpg": fp("Ana"), "D:/lib/2.jpg": fp("Ben")}
    imgs = [TaggedImage(p, 1, [FaceBox(0.35, 0.35, 0.3, 0.3, n)]) for p, n in (("D:/lib/1.jpg", "Ana"), ("D:/lib/2.jpg", "Ben"), ("D:/lib/3.jpg", "Rex"))]
    return fps, imgs


def test_sync_learns_in_the_background_and_only_new_photos_next_time(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    fps, imgs = _library_images()
    monkeypatch.setattr(faces, "read_regions", lambda exe, folders: imgs)
    g = Gallery(tmp_path / "faces.npz")
    engine = FakeEngine(fps)
    settings = {"library": ["D:/lib"], "pets": ["Rex"], "synced": ""}
    sync = Sync(engine, g, "exiftool", settings)
    assert sync.start() is True
    sync.join(10)
    assert not sync.running and sync.status["phase"] == "done" and sync.status["added"] == 2 and not sync.status["error"]
    assert dict(g.people()) == {"Ana": 1, "Ben": 1} and settings["synced"].endswith("Z")
    assert config.load_faces_settings()["synced"] == settings["synced"]
    first_calls = len(engine.calls)
    sync.start()
    sync.join(10)
    assert len(engine.calls) == first_calls and sync.status["added"] == 0     # a restart finds nothing new to read


def test_sync_reports_errors_and_missing_pieces(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))

    def boom(exe, folders):
        raise RuntimeError("exiftool exploded")
    monkeypatch.setattr(faces, "read_regions", boom)
    s = Sync(FakeEngine({}), Gallery(tmp_path / "f.npz"), "exiftool", {"library": ["D:/lib"], "pets": []})
    assert s.start() and (s.join(10) or True) and s.status["phase"] == "error" and "exploded" in s.status["error"] and not s.running
    assert Sync(FakeEngine({}), Gallery(tmp_path / "f.npz"), "exiftool", {"library": [], "pets": []}).start() is False   # nothing set
    no_exe = Sync(FakeEngine({}), Gallery(tmp_path / "f.npz"), None, {"library": ["D:/lib"], "pets": []})
    assert no_exe.start() is False and "exiftool" in no_exe.status["error"]


class FakePets:
    """Finds one animal head per image and registers its fingerprint by file, like FakeEngine does for faces."""

    def __init__(self, fps):
        self.fps, self.calls = fps, []

    def detect(self, path):
        self.calls.append(path)
        return (100, 100), [Detection(FaceBox(0.4, 0.4, 0.2, 0.2), 0.9, self.fps[path], kind="pet")]


def test_only_the_listed_names_are_learned_and_only_those_photos_are_recorded(tmp_path):
    fps = {"D:/p/1.jpg": fp("Ana"), "D:/p/2.jpg": fp("Cy")}
    tag = lambda n: [FaceBox(0.35, 0.35, 0.3, 0.3, n)]
    images = [TaggedImage("D:/p/1.jpg", 1, tag("Ana")), TaggedImage("D:/p/2.jpg", 1, tag("Rex"))]
    g = Gallery(tmp_path / "pets.npz")
    stats = import_tagged(FakeEngine(fps), g, images, include=["rex"])
    assert stats["added"] == 1 and dict(g.people()) == {"Rex": 1}
    assert g.is_done("D:/p/2.jpg") and not g.is_done("D:/p/1.jpg")     # the person's photo isn't this gallery's business


def test_learn_library_sends_pets_to_the_pet_gallery_and_people_to_the_people_one(tmp_path, monkeypatch):
    fps = {"D:/lib/1.jpg": fp("Ana"), "D:/lib/2.jpg": fp("Cy")}
    imgs = [TaggedImage("D:/lib/1.jpg", 1, [FaceBox(0.35, 0.35, 0.3, 0.3, "Ana")]),
            TaggedImage("D:/lib/2.jpg", 1, [FaceBox(0.35, 0.35, 0.3, 0.3, "Rex")])]
    monkeypatch.setattr(faces, "read_regions", lambda exe, folders: imgs)
    people, animals = Gallery(tmp_path / "faces.npz"), Gallery(tmp_path / "pets.npz")
    people_engine, pet_engine = FakeEngine(fps), FakePets(fps)
    stats = faces.learn_library(people_engine, people, "exiftool", ["D:/lib"], pets=["Rex"], pet_engine=pet_engine, pet_gallery=animals)
    assert dict(people.people()) == {"Ana": 1} and dict(animals.people()) == {"Rex": 1} and stats["pets_added"] == 1
    assert pet_engine.calls == ["D:/lib/2.jpg"] and not any(c.endswith("2.jpg") for c in people_engine.calls)
    # without a pet detector the pets are simply left out
    people2 = Gallery(tmp_path / "f2.npz")
    stats = faces.learn_library(FakeEngine(fps), people2, "exiftool", ["D:/lib"], pets=["Rex"])
    assert dict(people2.people()) == {"Ana": 1} and stats["pets_added"] == 0


def test_a_name_becoming_a_pet_leaves_the_people_gallery_and_a_pet_becoming_a_person_leaves_the_pets(tmp_path, monkeypatch, gallery):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    animals = Gallery(tmp_path / "pets.npz")
    animals.add("Rex", fp("Cy"), "D:/p/rex.jpg", box())
    settings = {"library": [], "pets": [], "synced": ""}
    assert update_settings(gallery, settings, [], ["Ana"], animals) == 4 and "Ana" not in dict(gallery.people())
    gallery.mark_done("D:/p/1.jpg")
    update_settings(gallery, settings, [], ["Ana", "Rex"], animals)
    assert gallery.is_done("D:/p/1.jpg")                                # adding a pet doesn't make people be re-read
    update_settings(gallery, settings, [], ["Ana"], animals)            # Rex is a person after all
    assert not gallery.is_done("D:/p/1.jpg") and "Rex" not in dict(animals.people())


def test_a_new_pet_is_looked_for_in_photos_already_read_for_another(tmp_path, monkeypatch, gallery):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    fps = {"D:/lib/1.jpg": fp("Cy")}
    imgs = [TaggedImage("D:/lib/1.jpg", 1, [FaceBox(0.05, 0.35, 0.3, 0.3, "Rex"), FaceBox(0.55, 0.35, 0.3, 0.3, "Tom")])]
    monkeypatch.setattr(faces, "read_regions", lambda exe, folders: imgs)

    class TwoPets(FakePets):
        def detect(self, path):
            self.calls.append(path)
            return (1000, 1000), [Detection(FaceBox(0.05, 0.35, 0.3, 0.3), 0.9, fp("Cy"), kind="pet"),
                                  Detection(FaceBox(0.55, 0.35, 0.3, 0.3), 0.9, fp("Ben"), kind="pet")]

    animals, settings = Gallery(tmp_path / "pets.npz"), {"library": ["D:/lib"], "pets": [], "synced": ""}
    update_settings(gallery, settings, ["D:/lib"], ["Rex"], animals)
    faces.learn_library(FakeEngine(fps), gallery, "exiftool", ["D:/lib"], settings["pets"], pet_engine=TwoPets(fps), pet_gallery=animals)
    assert dict(animals.people()) == {"Rex": 1} and animals.is_done("D:/lib/1.jpg")
    update_settings(gallery, settings, ["D:/lib"], ["Rex", "Tom"], animals)      # Tom is in the same photo
    faces.learn_library(FakeEngine(fps), gallery, "exiftool", ["D:/lib"], settings["pets"], pet_engine=TwoPets(fps), pet_gallery=animals)
    assert dict(animals.people()) == {"Rex": 1, "Tom": 1}                       # and Rex isn't learned twice


def test_sync_learns_pets_after_people_and_counts_both(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    fps = {"D:/lib/1.jpg": fp("Ana"), "D:/lib/2.jpg": fp("Cy")}
    imgs = [TaggedImage("D:/lib/1.jpg", 1, [FaceBox(0.35, 0.35, 0.3, 0.3, "Ana")]),
            TaggedImage("D:/lib/2.jpg", 1, [FaceBox(0.35, 0.35, 0.3, 0.3, "Rex")])]
    monkeypatch.setattr(faces, "read_regions", lambda exe, folders: imgs)
    animals = Gallery(tmp_path / "pets.npz")
    sync = Sync(FakeEngine(fps), Gallery(tmp_path / "faces.npz"), "exiftool", {"library": ["D:/lib"], "pets": ["Rex"], "synced": ""},
                pet_engine=FakePets(fps), pet_gallery=animals)
    assert sync.start()
    sync.join(10)
    assert sync.status["phase"] == "done" and sync.status["added"] == 2 and dict(animals.people()) == {"Rex": 1}
