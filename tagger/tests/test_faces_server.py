"""Face endpoints and Write's face handling. The detector is a fake that "sees" two faces in every scan."""

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import threading

from framelog_tagger import config, faces as faces_mod, server
from framelog_tagger.faces import Detection, Gallery, Sync
from framelog_tagger.regions import FaceBox, TaggedImage
from framelog_tagger.server import AppState, create_app

RNG = np.random.default_rng(3)


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


ANA = unit(RNG.normal(size=512))
STRANGER = unit(RNG.normal(size=512))


class FakeEngine:
    """A familiar face on the left (Ana), a stranger on the right, and a doubtful box that looks like Ana."""

    def detect(self, path):
        with Image.open(path) as im:
            w, h = im.size
        return (w, h), [Detection(FaceBox(0.1, 0.2, 0.2, 0.3), 0.95, unit(ANA + RNG.normal(size=512) * 0.01)),
                        Detection(FaceBox(0.6, 0.2, 0.2, 0.3), 0.90, STRANGER),
                        Detection(FaceBox(0.4, 0.7, 0.1, 0.1), 0.45, unit(ANA + RNG.normal(size=512) * 0.01))]


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    scans = tmp_path / "scans"
    scans.mkdir()
    Image.new("RGB", (400, 300), (90, 60, 30)).save(scans / "a.jpg")
    rotated = Image.new("RGB", (400, 300), (90, 60, 30))
    exif = rotated.getexif()
    exif[0x0112] = 6
    rotated.save(scans / "r.jpg", exif=exif)
    gallery = Gallery(tmp_path / "faces.npz")
    for i in range(4):
        gallery.add("Ana", unit(ANA + RNG.normal(size=512) * 0.01), f"D:/old/{i}.jpg", FaceBox(0.1, 0.1, 0.1, 0.1))
    calls = []
    monkeypatch.setattr(server, "write_tags", lambda exe, src, dst, args: calls.append(list(args)))
    existing = []   # what ExifTool would find in a file already; tests append TaggedImage objects
    monkeypatch.setattr(server, "read_regions", lambda exe, paths: list(existing))
    monkeypatch.setattr(faces_mod, "read_regions", lambda exe, folders: [])   # the library folder holds nothing new
    settings = {"library": [], "pets": [], "synced": ""}
    engine = FakeEngine()
    state = AppState(exiftool="exiftool", default_tz="UTC", engine=engine, gallery=gallery,
                     sync=Sync(engine, gallery, "exiftool", settings), face_settings=settings)
    client = TestClient(create_app(state))
    assert client.post("/api/load", json={"scans_dir": str(scans)}).status_code == 200
    return {"client": client, "state": state, "gallery": gallery, "calls": calls, "scans": scans, "existing": existing}


def region_arg(args):
    return next((a for a in args if a.startswith("-XMP-mwg-rs:RegionInfo=")), None)


def test_session_reports_whether_faces_are_available(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    c = TestClient(create_app(AppState(exiftool=None, default_tz="UTC", faces_note="install the faces extra")))
    f = c.get("/api/session").json()["faces"]
    assert f["available"] is False and f["reason"] == "install the faces extra"
    assert c.post("/api/load", json={"scans_dir": str(tmp_path)}).status_code == 400   # (no scans there; just warming)


def test_unavailable_detect_is_a_409_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    Image.new("RGB", (10, 10)).save(tmp_path / "a.jpg")
    c = TestClient(create_app(AppState(exiftool=None, default_tz="UTC", faces_note="no faces extra")))
    c.post("/api/load", json={"scans_dir": str(tmp_path)})
    r = c.post("/api/faces/detect/a.jpg")
    assert r.status_code == 409 and "no faces extra" in r.json()["detail"]


def test_session_lists_the_names_to_complete(env):
    f = env["client"].get("/api/session").json()["faces"]
    assert f["available"] and f["names"] == ["Ana"] and f["count"] == 4 and f["accept"] == 0.4


def test_detect_names_the_familiar_face_and_leaves_the_stranger(env):
    j = env["client"].post("/api/faces/detect/a.jpg").json()
    assert (j["width"], j["height"]) == (400, 300)
    known, stranger, doubtful = j["faces"]
    assert known["name"] == "Ana" and known["suggestions"][0]["name"] == "Ana" and known["suggestions"][0]["score"] > 0.9
    assert known["box"] == [0.1, 0.2, 0.2, 0.3] and not known["low"]
    assert stranger["name"] == "" and stranger["suggestions"] == []
    # a box the detector isn't sure is a face is suggested but never named for you
    assert doubtful["name"] == "" and doubtful["low"] and doubtful["suggestions"][0]["name"] == "Ana"
    assert env["client"].post("/api/faces/detect/nope.jpg").status_code == 404


def test_write_stores_regions_and_keywords_and_teaches_the_gallery(env):
    c = env["client"]
    faces = c.post("/api/faces/detect/a.jpg").json()["faces"]
    body = {"pairs": [{"scan": "a.jpg", "keywords": ["party"], "faces": [
        {"id": 0, "box": faces[0]["box"], "name": "Ana"},
        {"id": 1, "box": faces[1]["box"], "name": "Zed"},
        {"box": [0.9, 0.9, 0.05, 0.05], "name": ""},          # left unnamed: not written
    ]}]}
    j = c.post("/api/write", json=body).json()
    assert j["written"] == 1 and j["learned"] == 2
    assert j["results"][0]["faces"] == 2 and j["results"][0]["note"] == ""
    args = env["calls"][0]
    region = region_arg(args)
    assert "W=400,H=300" in region and "Name=Ana,Type=Face" in region and "Name=Zed,Type=Face" in region
    assert "X=0.20000,Y=0.35000,W=0.20000,H=0.30000" in region      # centre of the (0.1, 0.2, 0.2, 0.3) box
    assert {"-XMP-dc:Subject=party", "-XMP-dc:Subject=Ana", "-XMP-dc:Subject=Zed"} <= set(args)
    assert dict(env["gallery"].people()) == {"Ana": 5, "Zed": 1}
    assert j["faces"]["names"][:2] == ["Ana", "Zed"]
    assert Gallery(env["gallery"].path).people()                      # saved to disk


def test_writing_the_same_roll_again_does_not_duplicate_faces(env):
    c = env["client"]
    faces = c.post("/api/faces/detect/a.jpg").json()["faces"]
    body = {"pairs": [{"scan": "a.jpg", "faces": [{"id": 1, "box": faces[1]["box"], "name": "Zed"}]}]}
    c.post("/api/write", json=body)
    body["pairs"][0]["faces"][0]["name"] = "Zoe"                      # a correction
    c.post("/api/write", json=body)
    assert dict(env["gallery"].people()) == {"Ana": 4, "Zoe": 1}


def test_naming_one_face_keeps_the_other_regions_already_in_the_file(env):
    # Lightroom boxed three people (the last unnamed). We name the left face only: its old, looser box is replaced,
    # the other two stay.
    env["existing"].append(TaggedImage("a.jpg", 1, [
        FaceBox(0.05, 0.15, 0.3, 0.4, "Old name"),    # around the detected left face, so it's the same face
        FaceBox(0.7, 0.6, 0.1, 0.1, "Sabine"),
        FaceBox(0.8, 0.1, 0.1, 0.1, ""),
    ]))
    c = env["client"]
    faces = c.post("/api/faces/detect/a.jpg").json()["faces"]
    c.post("/api/write", json={"pairs": [{"scan": "a.jpg", "faces": [{"id": 0, "box": faces[0]["box"], "name": "Ana"}]}]})
    region = region_arg(env["calls"][0])
    assert region.count("Type=Face") == 3
    assert "Name=Ana," in region and "Name=Sabine," in region and "Old name" not in region
    assert "X=0.85000,Y=0.15000" in region and region.count("Name=") == 2   # the unnamed box survives, still unnamed


def test_unreadable_existing_regions_means_none_are_written(env, monkeypatch):
    def boom(exe, paths):
        raise RuntimeError("exiftool exploded")
    monkeypatch.setattr(server, "read_regions", boom)
    c = env["client"]
    faces = c.post("/api/faces/detect/a.jpg").json()["faces"]
    j = c.post("/api/write", json={"pairs": [{"scan": "a.jpg", "faces": [
        {"id": 0, "box": faces[0]["box"], "name": "Ana"}]}]}).json()
    assert j["written"] == 1 and "couldn't read" in j["results"][0]["note"]
    assert region_arg(env["calls"][0]) is None and "-XMP-dc:Subject=Ana" in env["calls"][0]   # the keyword still goes in


def test_scans_whose_faces_were_not_looked_at_keep_their_regions(env):
    env["client"].post("/api/write", json={"pairs": [{"scan": "a.jpg"}]})
    assert region_arg(env["calls"][0]) is None
    env["client"].post("/api/write", json={"pairs": [{"scan": "a.jpg", "faces": []}]})
    assert region_arg(env["calls"][1]) is None                        # looked at, nobody named: nothing to write either


def test_rotated_scans_get_the_keyword_but_no_region(env):
    c = env["client"]
    faces = c.post("/api/faces/detect/r.jpg").json()["faces"]
    j = c.post("/api/write", json={"pairs": [{"scan": "r.jpg", "faces": [
        {"id": 0, "box": faces[0]["box"], "name": "Ana"}]}]}).json()
    assert j["written"] == 1 and "rotated" in j["results"][0]["note"] and j["results"][0]["faces"] == 0
    args = env["calls"][0]
    assert region_arg(args) is None and "-XMP-dc:Subject=Ana" in args
    assert j["learned"] == 1                                          # the face still teaches the gallery


def test_a_malformed_box_is_rejected_before_anything_is_written(env):
    r = env["client"].post("/api/write", json={"pairs": [{"scan": "a.jpg", "faces": [{"box": [0.1, 0.2], "name": "Ana"}]}]})
    assert r.status_code == 400 and not env["calls"]


def test_a_name_takes_the_spelling_already_used_as_a_keyword(env):
    env["state"].library.add(["Dean"])
    faces = env["client"].post("/api/faces/detect/a.jpg").json()["faces"]
    env["client"].post("/api/write", json={"pairs": [{"scan": "a.jpg", "faces": [
        {"id": 1, "box": faces[1]["box"], "name": "  dean "}]}]})
    assert "-XMP-dc:Subject=Dean" in env["calls"][0] and "Name=Dean,Type=Face" in region_arg(env["calls"][0])


def test_session_reports_the_library_settings_and_sync(env):
    f = env["client"].get("/api/session").json()["faces"]
    assert f["library"] == [] and f["pets"] == [] and f["sync"]["running"] is False and f["sync"]["phase"] == "idle"


def test_setting_the_library_is_remembered_and_starts_learning(env, tmp_path):
    lib = tmp_path / "lightroom"
    lib.mkdir()
    c = env["client"]
    j = c.post("/api/faces/library", json={"folders": [str(lib)], "pets": ["Kepler", " kepler ", "Elvis"]}).json()
    assert j["library"] == [str(lib)] and j["pets"] == ["Kepler", "Elvis"]
    env["state"].sync.join(10)
    s = c.get("/api/faces/sync").json()
    assert s["sync"]["phase"] == "done" and not s["sync"]["running"] and s["synced"].endswith("Z")
    saved = config.load_faces_settings()                   # kept on disk for the next launch
    assert saved["library"] == [str(lib)] and saved["pets"] == ["Kepler", "Elvis"] and saved["synced"] == s["synced"]


def test_a_missing_folder_is_rejected_and_nothing_is_saved(env, tmp_path):
    r = env["client"].post("/api/faces/library", json={"folders": [str(tmp_path / "nope")]})
    assert r.status_code == 400 and "not found" in r.json()["detail"]
    assert config.load_faces_settings()["library"] == [] and not env["state"].sync.running


def test_marking_a_name_as_not_a_person_removes_it_from_the_gallery(env, tmp_path):
    r = env["client"].post("/api/faces/library", json={"folders": [], "pets": ["Ana"]}).json()
    assert r["names"] == [] and r["count"] == 0            # the fixture's four Ana faces are gone


def test_cannot_change_the_library_while_it_is_learning(env, tmp_path, monkeypatch):
    lib = tmp_path / "lightroom"
    lib.mkdir()
    gate = threading.Event()
    monkeypatch.setattr(faces_mod, "read_regions", lambda exe, folders: gate.wait(10) and [])
    c = env["client"]
    assert c.post("/api/faces/library", json={"folders": [str(lib)]}).status_code == 200
    assert c.get("/api/faces/sync").json()["sync"]["running"] is True
    assert c.post("/api/faces/library", json={"folders": [str(lib)]}).status_code == 409
    gate.set()
    env["state"].sync.join(10)
    assert c.get("/api/faces/sync").json()["sync"]["phase"] == "done"


def test_without_the_face_tools_the_library_endpoint_explains(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    c = TestClient(create_app(AppState(exiftool=None, default_tz="UTC", faces_note="face tagging couldn't load")))
    r = c.post("/api/faces/library", json={"folders": []})
    assert r.status_code == 409 and "couldn't load" in r.json()["detail"]

class FakePetEngine:
    """One animal head in every scan, sitting on top of the doubtful 'face' the fake face detector reports."""

    def detect(self, path):
        with Image.open(path) as im:
            w, h = im.size
        return (w, h), [Detection(FaceBox(0.38, 0.68, 0.14, 0.14), 0.9, unit(DOG + RNG.normal(size=768) * 0.01), kind="pet")]


DOG = unit(RNG.normal(size=768))


@pytest.fixture
def pet_env(env, tmp_path):
    animals = Gallery(tmp_path / "pets.npz")
    for i in range(4):
        animals.add("Kepler", unit(DOG + RNG.normal(size=768) * 0.01), f"D:/old/dog{i}.jpg", FaceBox(0.1, 0.1, 0.1, 0.1))
    env["state"].pet_engine, env["state"].pet_gallery = FakePetEngine(), animals
    env["animals"] = animals
    return env


def test_pets_are_found_alongside_faces_and_a_face_box_on_a_pet_is_dropped(pet_env):
    j = pet_env["client"].post("/api/faces/detect/a.jpg").json()
    kinds = [(f["kind"], f["box"][0]) for f in j["faces"]]
    assert [k for k, _ in kinds] == ["face", "pet", "face"] and len(j["faces"]) == 3   # left to right; the face on the dog is gone
    pet = next(f for f in j["faces"] if f["kind"] == "pet")
    assert pet["name"] == "Kepler" and pet["suggestions"][0]["score"] > 0.9 and not pet["low"]   # named from the PET gallery
    assert next(f for f in j["faces"] if f["box"][0] == 0.1)["name"] == "Ana"                      # people still from theirs


def test_the_session_lists_pet_names_for_completion(pet_env):
    f = pet_env["client"].get("/api/session").json()["faces"]
    assert f["pets_available"] and f["pet_names"] == ["Kepler"] and "Kepler" in f["names"] and "Ana" in f["names"]


def test_a_named_pet_is_written_like_a_face_and_teaches_the_pet_gallery_only(pet_env):
    c = pet_env["client"]
    faces = c.post("/api/faces/detect/a.jpg").json()["faces"]
    pet = next(f for f in faces if f["kind"] == "pet")
    j = c.post("/api/write", json={"pairs": [{"scan": "a.jpg", "faces": [{"id": pet["id"], "box": pet["box"], "name": "Kepler"}]}]}).json()
    assert j["written"] == 1 and j["learned"] == 1
    args = pet_env["calls"][0]
    assert "Name=Kepler,Type=Face" in region_arg(args) and "-XMP-dc:Subject=Kepler" in args
    assert dict(pet_env["animals"].people()) == {"Kepler": 5} and dict(pet_env["gallery"].people()) == {"Ana": 4}


def test_without_a_pet_detector_nothing_changes(env):
    assert len(env["client"].post("/api/faces/detect/a.jpg").json()["faces"]) == 3
    assert env["client"].get("/api/session").json()["faces"]["pets_available"] is False


def test_the_pet_names_are_saved_with_the_library(env, tmp_path):
    lib = tmp_path / "lightroom"
    lib.mkdir()
    j = env["client"].post("/api/faces/library", json={"folders": [str(lib)], "pets": ["Kepler", " kepler ", "Elvis"]}).json()
    assert j["pets"] == ["Kepler", "Elvis"] and config.load_faces_settings()["pets"] == ["Kepler", "Elvis"]
