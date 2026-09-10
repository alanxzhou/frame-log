"""API tests. ExifTool is replaced with a stub script so these run anywhere."""

import json
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from framelog_tagger import server
from framelog_tagger.scans import natural_key
from framelog_tagger.server import AppState, create_app


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


EXPORT = {
    "app": "framelog", "schema": 2, "roll": "Test roll",
    "entries": [
        {"frame": 1, "iso": "2026-08-30T14:05:12Z", "tz": "Asia/Tokyo", "lat": 40.5, "lon": -73.9},
        {"frame": 2, "iso": "2026-08-30T14:06:12Z", "notes": "FAIL me"},
    ],
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FRAMELOG_TAGGER_CONFIG", str(tmp_path / "config.json"))
    scans = tmp_path / "scans"
    scans.mkdir()
    for name in ("2.jpg", "10.jpg", "1.jpg"):
        Image.new("RGB", (64, 40), (90, 60, 30)).save(scans / name)
    (scans / "readme.txt").write_text("not a scan")
    log = tmp_path / "roll.json"
    log.write_text(json.dumps(EXPORT), encoding="utf-8")
    state = AppState(exiftool=_stub_exiftool(tmp_path), default_tz="America/New_York")
    return {"client": TestClient(create_app(state)), "state": state, "scans": scans, "log": log, "tmp": tmp_path}


def _load(env, **extra):
    body = {"scans_dir": str(env["scans"]), "log_path": str(env["log"]), **extra}
    return env["client"].post("/api/load", json=body)


def test_natural_sort():
    assert sorted(["10.jpg", "2.jpg", "1.jpg", "img_9.jpg", "img_10.jpg"], key=natural_key) == \
        ["1.jpg", "2.jpg", "10.jpg", "img_9.jpg", "img_10.jpg"]


def test_unloaded_session_and_409s(env):
    c = env["client"]
    j = c.get("/api/session").json()
    assert j["loaded"] is False and j["default_tz"] == "America/New_York" and j["exiftool"]
    assert c.get("/api/thumb/1.jpg").status_code == 409
    assert c.get("/api/preview/0").status_code == 409
    assert c.post("/api/write", json={"pairs": []}).status_code == 409
    assert c.get("/").status_code == 200


def test_load_builds_session_and_resolves_zones(env):
    r = _load(env, camera="FM2", iso=400)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["loaded"] and j["roll"] == "Test roll" and j["tz"] == "America/New_York"
    assert [s["name"] for s in j["scans"]] == ["1.jpg", "2.jpg", "10.jpg"]
    assert Path(j["out_dir"]) == env["scans"] / "tagged"
    e0, e1 = j["entries"]
    assert e0["zone"] == "Asia/Tokyo" and e0["zone_source"] == "phone" and e0["offset"] == "+09:00"
    assert e0["local"] == "Aug 30, 23:05:12"
    assert e1["zone"] == "America/New_York" and e1["zone_source"] == "fallback" and e1["offset"] == "-04:00"
    assert e1["local"] == "Aug 30, 10:06:12"
    # the setup values are remembered for next time
    remembered = json.loads((env["tmp"] / "config.json").read_text())
    assert remembered["scans_dir"] == str(env["scans"]) and remembered["camera"] == "FM2" and remembered["iso"] == 400
    assert env["client"].get("/api/session").json()["remembered"]["camera"] == "FM2"


def test_load_rejects_bad_input(env):
    c = env["client"]
    r = c.post("/api/load", json={"scans_dir": str(env["tmp"] / "nope"), "log_path": str(env["log"])})
    assert r.status_code == 400 and "scans folder not found" in r.json()["detail"]
    r = _load(env, tz="Mars/Olympus")
    assert r.status_code == 400 and "unknown time zone" in r.json()["detail"]
    bad = env["tmp"] / "bad.json"
    bad.write_text("{}")
    r = c.post("/api/load", json={"scans_dir": str(env["scans"]), "log_path": str(bad)})
    assert r.status_code == 400 and "not a Frame Log export" in r.json()["detail"]
    empty = env["tmp"] / "empty"
    empty.mkdir()
    r = c.post("/api/load", json={"scans_dir": str(empty), "log_path": str(env["log"])})
    assert r.status_code == 400 and "no JPEGs" in r.json()["detail"]


def test_thumbnail_and_preview(env):
    c = env["client"]
    assert _load(env).status_code == 200
    r = c.get("/api/thumb/2.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert c.get("/api/thumb/nope.jpg").status_code == 404
    args = c.get("/api/preview/0").json()["args"]
    assert "-DateTimeOriginal=2026:08:30 23:05:12" in args and "-OffsetTimeOriginal=+09:00" in args
    args = c.get("/api/preview/1").json()["args"]
    assert "-DateTimeOriginal=2026:08:30 10:06:12" in args and "-OffsetTimeOriginal=-04:00" in args
    assert c.get("/api/preview/5").status_code == 404


def test_write_copies_and_reports_per_file(env):
    c = env["client"]
    assert _load(env).status_code == 200
    r = c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 0}, {"scan": "2.jpg", "entry": 1}]})
    assert r.status_code == 200
    j = r.json()
    assert j["written"] == 1
    ok, bad = j["results"]
    out = env["scans"] / "tagged"
    assert ok["ok"] and Path(ok["path"]) == out / "1.jpg" and (out / "1.jpg").exists()
    assert not bad["ok"] and "stubbed failure" in bad["error"]
    assert (env["scans"] / "1.jpg").exists()  # originals untouched


def test_write_validates_input(env):
    c = env["client"]
    assert _load(env).status_code == 200
    assert c.post("/api/write", json={"pairs": [{"scan": "zzz.jpg", "entry": 0}]}).status_code == 400
    assert c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 42}]}).status_code == 400
    env["state"].exiftool = None
    assert c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 0}]}).status_code == 400


def test_unload_returns_to_setup(env):
    c = env["client"]
    assert _load(env).status_code == 200
    j = c.post("/api/unload").json()
    assert j["loaded"] is False and j["remembered"]["scans_dir"] == str(env["scans"])
    assert c.get("/api/preview/0").status_code == 409


def test_pick_endpoint_delegates_to_native_dialog(env, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "native_pick", lambda kind, title, initial: (calls.append((kind, title, initial)) or {"path": "/chosen"}))
    r = env["client"].post("/api/pick", json={"kind": "folder", "title": "T", "initial": "/start"})
    assert r.json() == {"path": "/chosen"} and calls == [("folder", "T", "/start")]
