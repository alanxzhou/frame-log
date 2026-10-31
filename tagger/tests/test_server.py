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
    page = c.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"


def test_load_builds_session_and_resolves_zones(env):
    r = _load(env, camera="FM2", iso=400)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["loaded"] and j["roll"] == "Test roll" and j["tz"] == "America/New_York"
    assert (j["make"], j["camera"], j["film"], j["iso"]) == ("", "FM2", "", 400)
    assert [s["name"] for s in j["scans"]] == ["1.jpg", "2.jpg", "10.jpg"]
    assert Path(j["out_dir"]) == env["scans"] and j["in_place"]
    e0, e1 = j["entries"]
    assert e0["zone"] == "Asia/Tokyo" and e0["zone_source"] == "phone" and e0["offset"] == "+09:00"
    assert e0["local"] == "Aug 30, 23:05:12"
    assert e1["zone"] == "America/New_York" and e1["zone_source"] == "fallback" and e1["offset"] == "-04:00"
    assert e1["local"] == "Aug 30, 10:06:12"
    # the setup values are remembered for next time
    remembered = json.loads((env["tmp"] / "config.json").read_text())
    assert remembered["scans_dir"] == str(env["scans"]) and remembered["log_path"] == str(env["log"])
    # camera and film change roll to roll, so they start blank
    assert "camera" not in remembered and "iso" not in remembered
    assert "camera" not in env["client"].get("/api/session").json()["remembered"]


def test_load_without_log(env):
    c = env["client"]
    r = c.post("/api/load", json={"scans_dir": str(env["scans"]), "camera": "FM2", "film": "Portra 400"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["roll"] == "scans" and j["entries"] == [] and j["log_path"] == ""
    r = c.post("/api/write", json={"pairs": [{"scan": "1.jpg"}, {"scan": "2.jpg", "entry": None}]})
    assert r.status_code == 200 and r.json()["written"] == 2
    assert c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 0}]}).status_code == 400
    r = c.post("/api/load", json={"scans_dir": str(env["scans"]), "log_path": str(env["tmp"] / "nope.json")})
    assert r.status_code == 400 and "log file not found" in r.json()["detail"]


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
    out = env["tmp"] / "out"
    assert _load(env, out_dir=str(out)).status_code == 200
    r = c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 0}, {"scan": "2.jpg", "entry": 1}]})
    assert r.status_code == 200
    j = r.json()
    assert j["written"] == 1
    ok, bad = j["results"]
    assert ok["ok"] and Path(ok["path"]) == out / "1.jpg" and (out / "1.jpg").exists()
    assert not bad["ok"] and "stubbed failure" in bad["error"]
    assert (env["scans"] / "1.jpg").exists()  # originals untouched


def test_write_defaults_to_in_place(env):
    assert _load(env).status_code == 200
    j = env["client"].post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": 0}]}).json()
    assert j["written"] == 1 and Path(j["results"][0]["path"]) == env["scans"] / "1.jpg"
    assert not (env["scans"] / "tagged").exists()


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


def _card(j, entry_id):
    return next(e for e in j["entries"] if e["id"] == entry_id)


def test_edit_marks_card_edited_and_revert_restores(env):
    c = env["client"]
    j = _load(env).json()
    assert [e["status"] for e in j["entries"]] == ["original", "original"]
    e0 = _card(j, 0)
    fields = {"frame": 1, "local": e0["local_iso"], "tz": "Asia/Tokyo", "lat": 40.5, "lon": -73.9}
    # same values back (the editor saves whole seconds): still original
    assert _card(c.put("/api/entries/0", json=fields).json(), 0)["status"] == "original"
    j = c.put("/api/entries/0", json={**fields, "local": "2026-08-30T23:10:00", "lens": "50mm"}).json()
    e0 = _card(j, 0)
    assert e0["status"] == "edited" and e0["iso"] == "2026-08-30T14:10:00Z" and e0["lens"] == "50mm"
    assert "-LensModel=50mm" in c.get("/api/preview/0").json()["args"]
    assert _card(c.post("/api/entries/0/revert").json(), 0)["status"] == "original"
    assert c.delete("/api/entries/0").status_code == 400   # log cards can't be deleted


def test_edit_validates(env):
    c = env["client"]
    _load(env)
    assert c.put("/api/entries/0", json={"frame": 1, "tz": "Mars/Olympus"}).status_code == 400
    assert c.put("/api/entries/0", json={"frame": 1, "lat": 40.5}).status_code == 400
    assert c.put("/api/entries/0", json={"frame": 1, "local": "yesterday"}).status_code == 400
    assert c.put("/api/entries/9", json={"frame": 1}).status_code == 404


def test_paste_and_new_cards(env):
    c = env["client"]
    _load(env)
    j = c.post("/api/entries", json={"copy_of": 0}).json()
    copy = _card(j, j["created"])
    assert copy["status"] == "added" and copy["added"] == "copy of #1" and copy["lat"] == 40.5
    j = c.post("/api/entries", json={"fields": {"frame": 7, "lens": "28mm"}}).json()
    new = _card(j, j["created"])
    assert new["status"] == "added" and new["added"] == "new" and new["local"] == "" and new["iso"] == ""
    # no time: no date tags, but the rest is written
    args = c.get(f"/api/preview/{new['id']}").json()["args"]
    assert "-LensModel=28mm" in args and not any(a.startswith("-DateTimeOriginal") for a in args)
    r = c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": copy["id"]}, {"scan": "10.jpg", "entry": new["id"]}]})
    assert r.json()["written"] == 2
    assert c.post(f"/api/entries/{new['id']}/revert").status_code == 400
    j = c.delete(f"/api/entries/{new['id']}").json()
    assert new["id"] not in [e["id"] for e in j["entries"]]
    # ids are never reused
    assert c.post("/api/entries", json={"copy_of": 1}).json()["created"] == new["id"] + 1
    assert c.post("/api/entries", json={}).status_code == 400


def test_cards_without_a_log(env):
    c = env["client"]
    c.post("/api/load", json={"scans_dir": str(env["scans"])})
    j = c.post("/api/entries", json={"fields": {"frame": 1, "local": "2026-08-30T10:00:00"}}).json()
    assert _card(j, j["created"])["offset"] == "-04:00"   # fallback zone
    r = c.post("/api/write", json={"pairs": [{"scan": "1.jpg", "entry": j["created"]}, {"scan": "2.jpg"}]})
    assert r.json()["written"] == 2
