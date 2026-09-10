from datetime import timezone
from pathlib import Path

import pytest

from framelog_tagger.log import LogFormatError, parse_aperture, parse_shutter, read_log

CSV = """frame,iso_datetime,latitude,longitude,accuracy_m,altitude_m,lens,aperture,shutter,notes
2,2026-08-30T14:07:40.000Z,40.742501,-73.988100,15,,"50mm f/1.4","f/5.6","1/125",
1,2026-08-30T14:05:12.000Z,40.741895,-73.989308,8,12,"50mm f/1.4","f/2.8","1/250","Flatiron, ""harsh"" light"
3,2026-08-30T14:20:01.000Z,,,,,"50mm f/1.4","f/5.6","1/60s","no fix"
"""


def test_read_log_sorts_and_parses(tmp_path: Path):
    p = tmp_path / "roll.csv"
    p.write_text(CSV, encoding="utf-8")
    entries = read_log(p)
    assert [e.frame for e in entries] == [1, 2, 3]
    e = entries[0]
    assert e.utc.tzinfo == timezone.utc and e.utc.hour == 14
    assert e.has_gps and e.lat == pytest.approx(40.741895)
    assert e.alt == 12 and e.acc == 8
    assert e.f_number == 2.8 and e.exposure_time == "1/250"
    assert e.notes == 'Flatiron, "harsh" light'
    assert entries[1].alt is None
    assert not entries[2].has_gps and entries[2].exposure_time == "1/60"


def test_read_log_rejects_foreign_csv(tmp_path: Path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(LogFormatError):
        read_log(p)


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
