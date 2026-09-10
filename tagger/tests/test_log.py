import json
from datetime import timezone
from pathlib import Path

import pytest

from framelog_tagger.log import LogFormatError, parse_aperture, parse_shutter, read_log

EXPORT = {
    "app": "framelog",
    "schema": 1,
    "exported": "2026-09-01T02:00:00.000Z",
    "roll": "2026-08 Portra 400 #3",
    "entries": [
        {"frame": 2, "iso": "2026-08-30T14:07:40.000Z", "local": "8/30/2026, 10:07:40 AM",
         "lat": 40.742501, "lon": -73.9881, "acc": 15, "alt": None,
         "lens": "50mm f/1.4", "aperture": "f/5.6", "shutterSpeed": "1/125", "notes": ""},
        {"frame": 1, "iso": "2026-08-30T14:05:12.000Z", "local": "8/30/2026, 10:05:12 AM",
         "lat": 40.741895, "lon": -73.989308, "acc": 8, "alt": 12,
         "lens": "50mm f/1.4", "aperture": "f/2.8", "shutterSpeed": "1/250",
         "notes": 'Flatiron, "harsh" light', "futureField": "ignored"},
        {"frame": 3, "iso": "2026-08-30T14:20:01.000Z", "local": "8/30/2026, 10:20:01 AM",
         "lat": None, "lon": None, "acc": None, "alt": None,
         "lens": "50mm f/1.4", "aperture": "f/5.6", "shutterSpeed": "1/60s", "notes": "no fix"},
    ],
}


def _write(tmp_path: Path, data, name="roll.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_read_log_sorts_and_parses(tmp_path: Path):
    roll = read_log(_write(tmp_path, EXPORT))
    assert roll.name == "2026-08 Portra 400 #3"
    assert roll.exported == "2026-09-01T02:00:00.000Z"
    assert [e.frame for e in roll.entries] == [1, 2, 3]
    e = roll.entries[0]
    assert e.utc.tzinfo == timezone.utc and e.utc.hour == 14
    assert e.has_gps and e.lat == pytest.approx(40.741895)
    assert e.alt == 12 and e.acc == 8
    assert e.f_number == 2.8 and e.exposure_time == "1/250"
    assert e.notes == 'Flatiron, "harsh" light'
    assert roll.entries[1].alt is None
    assert not roll.entries[2].has_gps and roll.entries[2].exposure_time == "1/60"


def test_roll_name_falls_back_to_filename(tmp_path: Path):
    data = {**EXPORT, "roll": ""}
    assert read_log(_write(tmp_path, data, "my_roll.json")).name == "my_roll"


def test_rejects_non_framelog_json(tmp_path: Path):
    with pytest.raises(LogFormatError, match="not a Frame Log export"):
        read_log(_write(tmp_path, {"entries": []}))


def test_rejects_unknown_schema(tmp_path: Path):
    with pytest.raises(LogFormatError, match="schema 2"):
        read_log(_write(tmp_path, {**EXPORT, "schema": 2}))


def test_rejects_invalid_json_and_bad_entries(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(LogFormatError, match="not valid JSON"):
        read_log(p)
    with pytest.raises(LogFormatError, match="frame number"):
        read_log(_write(tmp_path, {**EXPORT, "entries": [{"iso": "2026-08-30T14:05:12Z"}]}))
    with pytest.raises(LogFormatError, match="timestamp"):
        read_log(_write(tmp_path, {**EXPORT, "entries": [{"frame": 1, "iso": "yesterday"}]}))


@pytest.mark.parametrize("raw,expected", [
    ("f/2.8", 2.8), ("F2.8", 2.8), ("2.8", 2.8), ("f/8", 8.0), ("", None), ("wide", None),
])
def test_parse_aperture(raw, expected):
    assert parse_aperture(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("1/250", "1/250"), ("1/250s", "1/250"), ("1/250 sec", "1/250"), ("2s", "2"), ('2"', "2"),
    ("0.5", "0.5"), ("", None), ("bulb", None), ("1/0", None),
])
def test_parse_shutter(raw, expected):
    assert parse_shutter(raw) == expected
