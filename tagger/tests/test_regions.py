"""Face region geometry and the ExifTool structure syntax. No face model needed."""

import json
import subprocess

import pytest

from framelog_tagger import regions
from framelog_tagger.regions import FaceBox, match_boxes, parse_regions, region_args, to_upright


def approx(t, *vals):
    return t == pytest.approx(vals, abs=1e-6)


def test_upright_file_is_unchanged():
    assert approx(to_upright(0.3, 0.6, 0.1, 0.2, 1), 0.3, 0.6, 0.1, 0.2)
    assert approx(to_upright(0.3, 0.6, 0.1, 0.2, None), 0.3, 0.6, 0.1, 0.2)


def test_orientations_match_measured_files():
    # Real Lightroom regions from the experiment: the stored-frame centre and where the detector found the face.
    # orientation 8 (turn 90 CCW to display): (0.611, 0.472) -> (0.472, 0.389); detector found it at (0.470, 0.393)
    x, y, w, h = to_upright(0.611, 0.472, 0.101, 0.097, 8)
    assert (x, y) == pytest.approx((0.472, 0.389), abs=1e-3)
    assert (w, h) == (0.097, 0.101)   # width and height trade places on a 90-degree turn
    # orientation 6 (turn 90 CW): (0.512, 0.497) -> (0.503, 0.512); detector found it at (0.499, 0.522)
    x, y, _, _ = to_upright(0.512, 0.497, 0.077, 0.115, 6)
    assert (x, y) == pytest.approx((0.503, 0.512), abs=1e-3)
    # orientation 2 (mirrored): x flips only; size is untouched
    assert approx(to_upright(0.585, 0.359, 0.046, 0.084, 2), 0.415, 0.359, 0.046, 0.084)
    assert approx(to_upright(0.2, 0.3, 0.1, 0.1, 3), 0.8, 0.7, 0.1, 0.1)


@pytest.mark.parametrize("o", range(1, 9))
def test_every_orientation_keeps_the_centre_in_the_picture(o):
    x, y, w, h = to_upright(0.25, 0.75, 0.1, 0.2, o)
    assert 0 <= x <= 1 and 0 <= y <= 1
    assert sorted((w, h)) == [0.1, 0.2]


def test_parse_regions_converts_to_top_left_upright_boxes():
    data = [
        {"SourceFile": "D:/p/a.jpg", "Orientation": 1, "RegionInfo": {"RegionList": [
            {"Type": "Face", "Name": "Dean", "Area": {"X": 0.5, "Y": 0.4, "W": 0.2, "H": 0.3}},
            {"Type": "Pet", "Name": "Rex", "Area": {"X": 0.1, "Y": 0.1, "W": 0.1, "H": 0.1}},
            {"Type": "Face", "Area": {"X": 0.9, "Y": 0.9, "W": 0.1, "H": 0.1}},
        ]}},
        {"SourceFile": "D:/p/none.jpg", "Orientation": 1},
        {"SourceFile": "D:/p/one.jpg", "RegionInfo": {"RegionList": {  # a lone region may arrive as an object
            "Type": "Face", "Name": "Ana", "Area": {"X": 0.5, "Y": 0.5, "W": 0.2, "H": 0.2}}}},
    ]
    out = parse_regions(data)
    assert [i.path for i in out] == ["D:/p/a.jpg", "D:/p/one.jpg"]
    a = out[0].faces
    assert [f.name for f in a] == ["Dean", ""]   # the pet is dropped; an unnamed face is kept
    assert (a[0].x, a[0].y, a[0].w, a[0].h) == pytest.approx((0.4, 0.25, 0.2, 0.3))
    assert out[1].orientation == 1 and out[1].faces[0].name == "Ana"


def test_match_boxes_by_centre_or_overlap_and_each_detection_once():
    tagged = [FaceBox(0.1, 0.1, 0.2, 0.2, "A"), FaceBox(0.6, 0.6, 0.2, 0.2, "B"), FaceBox(0.4, 0.0, 0.1, 0.1, "C")]
    found = [FaceBox(0.62, 0.62, 0.1, 0.1), FaceBox(0.15, 0.15, 0.1, 0.1)]
    assert match_boxes(tagged, found) == [1, 0, None]
    # one detection can't serve two tags
    assert match_boxes([FaceBox(0, 0, 0.5, 0.5), FaceBox(0.1, 0.1, 0.5, 0.5)], [FaceBox(0.2, 0.2, 0.1, 0.1)]).count(None) == 1


def test_region_args_writes_centres_and_only_named_faces():
    faces = [FaceBox(0.4, 0.25, 0.2, 0.3, "Dean"), FaceBox(0.0, 0.0, 0.1, 0.1, ""), FaceBox(0.7, 0.7, 0.1, 0.1, "  ")]
    (arg,) = region_args(faces, 3000, 2000)
    assert arg.startswith("-XMP-mwg-rs:RegionInfo={AppliedToDimensions={W=3000,H=2000,Unit=pixel},RegionList=[")
    assert "Area={X=0.50000,Y=0.40000,W=0.20000,H=0.30000,Unit=normalized},Name=Dean,Type=Face" in arg
    assert arg.count("Type=Face") == 1
    assert region_args([FaceBox(0, 0, 1, 1, "")], 10, 10) == []


def test_region_args_escapes_exiftool_structure_characters():
    (arg,) = region_args([FaceBox(0, 0, 0.1, 0.1, "Smith, J. [Jr] {x} a|b")], 10, 10)
    assert "Name=Smith|, J. [Jr|] {x|} a||b," in arg


def test_merge_keeps_regions_the_new_names_dont_cover():
    existing = [FaceBox(0.0, 0.0, 0.3, 0.3, "Left"), FaceBox(0.6, 0.6, 0.2, 0.2, "Right"), FaceBox(0.4, 0.0, 0.1, 0.1, "")]
    new = [FaceBox(0.05, 0.05, 0.2, 0.2, "Left (renamed)")]
    merged = regions.merge_regions(existing, new)
    assert [f.name for f in merged] == ["Right", "", "Left (renamed)"]
    assert regions.merge_regions([], new) == new and regions.merge_regions(existing, []) == existing


def test_region_args_can_carry_unnamed_faces_but_not_alone():
    faces = [FaceBox(0.1, 0.1, 0.2, 0.2, "Dean"), FaceBox(0.6, 0.6, 0.1, 0.1, "")]
    (arg,) = region_args(faces, 10, 10, keep_unnamed=True)
    assert arg.count("Type=Face") == 2 and arg.count("Name=") == 1
    assert "Area={X=0.65000,Y=0.65000,W=0.10000,H=0.10000,Unit=normalized},Type=Face}" in arg   # no Name field at all
    assert region_args([FaceBox(0, 0, 0.1, 0.1, "")], 10, 10, keep_unnamed=True) == []   # nothing named: nothing to write


def test_read_regions_runs_exiftool_and_parses(tmp_path, monkeypatch):
    payload = [{"SourceFile": "D:/p/a.jpg", "Orientation": 6, "RegionInfo": {"RegionList": [
        {"Type": "Face", "Name": "Ana", "Area": {"X": 0.5, "Y": 0.5, "W": 0.2, "H": 0.1}}]}}]
    seen = {}

    def fake_run(cmd, capture_output):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=("\ufeff" + json.dumps(payload)).encode("utf-8"), stderr=b"")

    monkeypatch.setattr(regions.subprocess, "run", fake_run)
    out = regions.read_regions("exiftool", [tmp_path])
    assert out[0].faces[0].name == "Ana" and out[0].orientation == 6
    assert "-RegionInfo" in seen["cmd"] and "-Orientation#" in seen["cmd"] and str(tmp_path) in seen["cmd"]
