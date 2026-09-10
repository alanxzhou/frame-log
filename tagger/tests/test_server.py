"""API tests. ExifTool is replaced with a stub script so these run anywhere."""

import stat
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framelog_tagger.exif import TagContext
from framelog_tagger.log import LogEntry
from framelog_tagger.scans import list_scans, natural_key
from framelog_tagger.server import Session, create_app


def _stub_exiftool(tmp_path: Path) -> str:
    """A fake exiftool that prints what a real one prints on success."""
    script = tmp_path / "stub_exiftool.py"
    script.write_text(
        "import sys\n"
        "if '-ver' in sys.argv: print('0.0'); sys.exit(0)\n"
        "if any('FAIL' in a for a in sys.argv): print('Error: stubbed failure', file=sys.stderr); sys.exit(1)\n"
        "print('    1 image files updated')\n"
    )
    if sys.platform == "win32":
        bat = tmp_path / "exiftool.bat"
        bat.write_text(f'@"{sys.executable}" "{script}" %*\n')
        return str(bat)
    sh = tmp_path / "exiftool"
    sh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
    return str(sh)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    scans = tmp_path / "scans"
    scans.mkdir()
    for name in ("2.jpg", "10.jpg", "1.jpg"):
        Image.new("RGB", (64, 40), (90, 60, 30)).save(scans / name)
    (scans / "readme.txt").write_text("not a scan")
    entries = [
        LogEntry(frame=1, iso="2026-08-30T14:05:12Z", lat=40.5, lon=-73.9),
        LogEntry(frame=2, iso="2026-08-30T14:06:12Z", notes="FAIL me"),
    ]
    return Session(
        scans_dir=scans, out_dir=tmp_path / "out", in_place=False,
        scans=list_scans(scans), entries=entries,
        ctx=TagContext(tz=ZoneInfo("UTC"), roll="Test roll"), tz_name="UTC",
        exiftool=_stub_exiftool(tmp_path),
    )


def test_natural_sort():
    assert sorted(["10.jpg", "2.jpg", "1.jpg", "img_9.jpg", "img_10.jpg"], key=natural_key) == \
        ["1.jpg", "2.jpg", "10.jpg", "img_9.jpg", "img_10.jpg"]


def test_session_endpoint(session: Session):
    c = TestClient(create_app(session))
    j = c.get("/api/session").json()
    assert j["roll"] == "Test roll"
    assert [s["name"] for s in j["scans"]] == ["1.jpg", "2.jpg", "10.jpg"]
    assert [e["id"] for e in j["entries"]] == [0, 1]
    assert j["entries"][0]["f_number"] is None


def test_index_and_thumbnail(session: Session):
    c = TestClient(create_app(session))
    assert c.get("/").status_code == 200
    r = c.get("/api/thumb/2.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert c.get("/api/thumb/nope.jpg").status_code == 404


def test_preview(session: Session):
    c = TestClient(create_app(session))
    args = c.get("/api/preview/0").json()["args"]
    assert "-DateTimeOriginal=2026:08:30 14:05:12" in args
    assert c.get("/api/preview/5").status_code == 404


def test_write_copies_and_reports_per_file(session: Session):
    c = TestClient(create_app(session))
    r = c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 0}, {"scan": "2.jpg", "entry": 1}]})
    assert r.status_code == 200
    j = r.json()
    assert j["written"] == 1
    ok, bad = j["results"]
    assert ok["ok"] and Path(ok["path"]) == session.out_dir / "1.jpg" and (session.out_dir / "1.jpg").exists()
    assert not bad["ok"] and "stubbed failure" in bad["error"]
    # originals untouched
    assert (session.scans_dir / "1.jpg").exists()


def test_write_validates_input(session: Session):
    c = TestClient(create_app(session))
    assert c.post("/api/write", json={"pairs": [{"scan": "zzz.jpg", "entry": 0}]}).status_code == 400
    assert c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 42}]}).status_code == 400
    session.exiftool = None
    assert c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 0}]}).status_code == 400
