from zoneinfo import ZoneInfo

from framelog_tagger.exif import TagContext, build_args, resolve_zone
from framelog_tagger.log import LogEntry

NY = ZoneInfo("America/New_York")


def _args(entry: LogEntry, **ctx) -> dict[str, str]:
    """build_args as {tag: value}; a tag repeated keeps the last value."""
    out = {}
    for a in build_args(entry, TagContext(tz=NY, **ctx)):
        k, _, v = a[1:].partition("=")
        out[k] = v
    return out


def test_local_time_and_offset_from_fallback_zone():
    e = LogEntry(frame=1, iso="2026-08-30T14:05:12.000Z")
    a = _args(e)
    assert a["DateTimeOriginal"] == "2026:08:30 10:05:12"
    assert a["CreateDate"] == "2026:08:30 10:05:12"
    assert a["OffsetTimeOriginal"] == "-04:00"
    # winter: EST
    a = _args(LogEntry(frame=1, iso="2026-01-15T14:05:12.000Z"))
    assert a["DateTimeOriginal"] == "2026:01:15 09:05:12"
    assert a["OffsetTimeOriginal"] == "-05:00"


def test_entry_zone_wins_over_fallback():
    """A roll that starts in Tokyo and ends in Boston gets each frame's own zone."""
    tokyo = LogEntry(frame=1, iso="2026-03-10T02:00:00Z", tz="Asia/Tokyo")
    boston = LogEntry(frame=30, iso="2026-03-15T18:00:00Z", tz="America/New_York")
    assert _args(tokyo)["DateTimeOriginal"] == "2026:03:10 11:00:00"
    assert _args(tokyo)["OffsetTimeOriginal"] == "+09:00"
    assert _args(boston)["DateTimeOriginal"] == "2026:03:15 14:00:00"
    assert _args(boston)["OffsetTimeOriginal"] == "-04:00"
    ctx = TagContext(tz=NY)
    assert resolve_zone(tokyo, ctx) == (ZoneInfo("Asia/Tokyo"), "phone")
    assert resolve_zone(LogEntry(frame=2, iso="2026-03-10T02:00:00Z"), ctx) == (NY, "fallback")


def test_bogus_entry_zone_falls_back():
    e = LogEntry(frame=1, iso="2026-08-30T14:05:12Z", tz="Not/AZone")
    assert resolve_zone(e, TagContext(tz=NY)) == (NY, "fallback")
    assert _args(e)["OffsetTimeOriginal"] == "-04:00"


def test_gps_written_only_when_present():
    with_gps = LogEntry(frame=1, iso="2026-08-30T14:05:12Z", lat=40.5, lon=-73.9, alt=12, acc=8)
    a = _args(with_gps)
    assert a["GPSLatitude"] == "40.5" and a["GPSLatitudeRef"] == "40.5"
    assert a["GPSLongitude"] == "-73.9" and a["GPSLongitudeRef"] == "-73.9"
    assert a["GPSAltitude"] == "12" and a["GPSHPositioningError"] == "8"
    assert a["GPSTimeStamp"] == "14:05:12"  # GPS stamps stay UTC
    without = _args(LogEntry(frame=1, iso="2026-08-30T14:05:12Z"))
    assert not any(k.startswith("GPS") for k in without)


def test_gear_notes_and_roll_context():
    e = LogEntry(frame=7, iso="2026-08-30T14:05:12Z", lens="50mm f/1.4", aperture="f/2.8",
                 shutter="1/250", notes="harsh light")
    a = _args(e, roll="Roll 3", camera="Nikon FM2", make="Nikon", film="Portra 400", iso=400,
              extra_args=["-Artist=Me"])
    assert a["FNumber"] == "2.8" and a["ExposureTime"] == "1/250"
    assert a["LensModel"] == "50mm f/1.4" and a["XMP-aux:Lens"] == "50mm f/1.4"
    assert a["ImageDescription"] == "harsh light" and a["XMP-dc:Description"] == "harsh light"
    assert a["Model"] == "Nikon FM2" and a["Make"] == "Nikon" and a["ISO"] == "400"
    assert a["UserComment"] == "Frame Log frame 7 | roll: Roll 3 | film: Portra 400"
    assert a["Artist"] == "Me"


def test_unparseable_shutter_is_skipped_not_written():
    a = _args(LogEntry(frame=1, iso="2026-08-30T14:05:12Z", shutter="bulb", aperture=""))
    assert "ExposureTime" not in a and "FNumber" not in a
