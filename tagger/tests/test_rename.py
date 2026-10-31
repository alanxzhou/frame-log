"""Batch rename: planning, applying, and the API around it."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framelog_tagger.rename import apply_rename, detect_name, plan_rename
from framelog_tagger.server import AppState, create_app

LAB = ["123456780001.jpg", "123456780002.jpg", "123456780010.jpg"]


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    d = tmp_path / "12345678"
    d.mkdir()
    for n in LAB:
        (d / n).write_bytes(n.encode())   # content identifies the original file
    (d / "notes.txt").write_text("lab notes")
    return d


def test_plan_forward_and_reverse(lab):
    p = plan_rename(lab, "tokyo-fm2-portra400")
    assert p.items == [(LAB[0], "tokyo-fm2-portra400-01.jpg"), (LAB[1], "tokyo-fm2-portra400-02.jpg"),
                       (LAB[2], "tokyo-fm2-portra400-03.jpg")]
    assert p.dst_dir == lab.with_name("tokyo-fm2-portra400")
    r = plan_rename(lab, "roll", reverse=True, rename_folder=False)
    assert [n for _, n in r.items] == ["roll-01.jpg", "roll-02.jpg", "roll-03.jpg"]
    assert [o for o, _ in r.items] == [LAB[2], LAB[1], LAB[0]]
    assert r.dst_dir == lab


def test_plan_pads_past_99_and_groups_stems(tmp_path):
    for i in range(1, 101):
        (tmp_path / f"{i}.jpg").write_bytes(b"")
    (tmp_path / "1.TIF").write_bytes(b"")
    p = plan_rename(tmp_path, "r", rename_folder=False)
    assert p.items[:2] == [("1.jpg", "r-001.jpg"), ("1.TIF", "r-001.tif")]
    assert p.items[-1] == ("100.jpg", "r-100.jpg")


@pytest.mark.parametrize("name", ["", "  ", "a/b", "a:b", "trailing."])
def test_plan_rejects_bad_names(lab, name):
    with pytest.raises(ValueError):
        plan_rename(lab, name)


def test_plan_refuses_clobbering(lab):
    lab.with_name("roll").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        plan_rename(lab, "roll")
    plan_rename(lab, "roll", rename_folder=False)
    (lab / "roll-01.jpg").mkdir()                      # not a scan, so it would be clobbered
    with pytest.raises(ValueError, match="isn't one of the scans"):
        plan_rename(lab, "roll", rename_folder=False)
    with pytest.raises(ValueError, match="no images"):
        plan_rename(lab.with_name("roll"), "x")


def test_apply_then_reverse_in_place(lab):
    new = apply_rename(plan_rename(lab, "roll"))
    assert not lab.exists()
    assert sorted(p.name for p in new.iterdir()) == ["notes.txt", "roll-01.jpg", "roll-02.jpg", "roll-03.jpg"]
    assert (new / "roll-01.jpg").read_bytes() == LAB[0].encode()
    # Flipping the order of already-renamed files swaps names among themselves.
    apply_rename(plan_rename(new, "roll", reverse=True))
    assert (new / "roll-01.jpg").read_bytes() == LAB[2].encode()
    assert (new / "roll-03.jpg").read_bytes() == LAB[0].encode()
    assert (new / "roll-02.jpg").read_bytes() == LAB[1].encode()


def test_rerename_fixes_a_typo_and_keeps_frame_numbers(lab):
    new = apply_rename(plan_rename(lab, "tokyo-fm2-protra400"))
    (new / "tokyo-fm2-protra400-02.jpg").unlink()           # a blank, deleted after renaming
    assert detect_name(new) == "tokyo-fm2-protra400"
    p = plan_rename(new, "tokyo-fm2-portra400")
    assert [n for _, n in p.items] == ["tokyo-fm2-portra400-01.jpg", "tokyo-fm2-portra400-03.jpg"]
    fixed = apply_rename(p)
    assert fixed.name == "tokyo-fm2-portra400" and not new.exists()
    assert (fixed / "tokyo-fm2-portra400-03.jpg").read_bytes() == LAB[2].encode()
    # Reverse on a named roll mirrors the numbers within their range.
    r = plan_rename(fixed, "tokyo-fm2-portra400", reverse=True)
    assert r.items == [("tokyo-fm2-portra400-03.jpg", "tokyo-fm2-portra400-01.jpg"),
                       ("tokyo-fm2-portra400-01.jpg", "tokyo-fm2-portra400-03.jpg")]


def test_detect_needs_one_consistent_name(tmp_path):
    assert detect_name(tmp_path / "missing") is None
    (tmp_path / "123456780001.jpg").write_bytes(b"")
    assert detect_name(tmp_path) is None                    # straight from the lab
    (tmp_path / "123456780001.jpg").rename(tmp_path / "a-b-01.jpg")
    (tmp_path / "a-b-02.tif").write_bytes(b"")
    assert detect_name(tmp_path) == "a-b"
    (tmp_path / "other-03.jpg").write_bytes(b"")
    assert detect_name(tmp_path) is None
    (tmp_path / "other-03.jpg").rename(tmp_path / "a-b-1.jpg")   # not NN, so not part of the pattern
    assert detect_name(tmp_path) is None
    (tmp_path / "a-b-1.jpg").rename(tmp_path / "a-b-001.jpg")    # same number as a-b-01: renumber instead
    assert detect_name(tmp_path) is None
    assert [n for _, n in plan_rename(tmp_path, "c", rename_folder=False).items] == \
        ["c-01.jpg", "c-02.jpg", "c-03.tif"]


def test_api(lab, tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    state = AppState(exiftool=None, default_tz="UTC", remembered={"scans_dir": str(lab)})
    c = TestClient(create_app(state))
    body = {"scans_dir": str(lab), "name": "roll", "reverse": True}
    r = c.post("/api/rename/preview", json=body)
    assert r.status_code == 200 and r.json()["items"][0] == {"old": LAB[2], "new": "roll-01.jpg"}
    assert lab.exists()                                    # preview touches nothing
    assert c.post("/api/rename/preview", json={**body, "name": "a|b"}).status_code == 400
    r = c.post("/api/rename/apply", json=body)
    assert r.status_code == 200 and r.json()["renamed"] == 3
    new = lab.with_name("roll")
    assert (new / "roll-01.jpg").read_bytes() == LAB[2].encode()
    assert state.remembered["scans_dir"] == str(new)       # setup screen follows the folder
    assert c.post("/api/rename/detect", json={"scans_dir": str(new)}).json() == {"name": "roll"}
    state.session = object()
    assert c.post("/api/rename/apply", json={**body, "scans_dir": str(new)}).status_code == 409
