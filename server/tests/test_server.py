import importlib.util
import json
import sys

import pytest

from conftest import AUTH, PASSWORD, entry, make_state
from framelog_sync.app import LoginThrottle
from framelog_sync.config import REPO_ROOT

# -- static app shell ------------------------------------------------------------------------


def test_serves_app_shell_without_auth(make_client):
    c = make_client()
    r = c.get("/")
    assert r.status_code == 200 and "Frame Log" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["cache-control"] == "no-cache"
    assert "javascript" in sw.headers["content-type"]
    assert c.get("/manifest.json").headers["content-type"].startswith("application/manifest+json")
    assert c.get("/icon-192.png").headers["content-type"] == "image/png"


@pytest.mark.parametrize("path", ["/README.md", "/server/.env", "/server/framelog_sync/config.py",
                                  "/.git/config", "/tagger/README.md", "/..%2fREADME.md", "/static/index.html"])
def test_serves_nothing_outside_the_allow_list(make_client, path):
    assert make_client().get(path).status_code == 404


# -- auth & rate limit -----------------------------------------------------------------------


def test_api_needs_the_password(make_client):
    c = make_client()
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"Authorization": "Bearer nope"}).json()["error"] == "wrong password"
    assert c.get("/api/status", headers={"Authorization": f"Basic {PASSWORD}"}).status_code == 401
    assert c.post("/api/sync", json=make_state()).status_code == 401
    r = c.get("/api/status", headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.headers["cache-control"] == "no-store"


def test_wrong_passwords_lock_out_that_address_only(make_client):
    throttle = LoginThrottle(per_ip=3, overall=100)
    c = make_client(throttle=throttle)
    bad = {"Authorization": "Bearer wrong"}
    for _ in range(3):
        assert c.get("/api/status", headers=bad).status_code == 401
    r = c.get("/api/status", headers=AUTH)  # even the right password waits out the lockout
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    # Requests with no password at all don't count as guesses.
    assert len(throttle.all) == 3
    other = make_client(throttle=throttle, client=("192.0.2.10", 1234))
    assert other.get("/api/status", headers=AUTH).status_code == 200


def test_success_resets_the_address_count(make_client):
    throttle = LoginThrottle(per_ip=3)
    c = make_client(throttle=throttle)
    for _ in range(2):
        c.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert c.get("/api/status", headers=AUTH).status_code == 200
    for _ in range(2):
        c.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert c.get("/api/status", headers=AUTH).status_code == 200


def test_x_forwarded_for_is_trusted_only_from_the_proxy(make_client):
    throttle = LoginThrottle(per_ip=1)
    bad = {"Authorization": "Bearer wrong"}
    # From the tunnel on this machine: the visitor's address comes from X-Forwarded-For.
    tunnel = make_client(throttle=throttle, client=("127.0.0.1", 40000))
    tunnel.get("/api/status", headers={**bad, "X-Forwarded-For": "198.51.100.7"})
    assert "198.51.100.7" in throttle.by_ip and "127.0.0.1" not in throttle.by_ip
    # A different visitor through the same tunnel isn't locked out.
    assert tunnel.get("/api/status", headers={**AUTH, "X-Forwarded-For": "198.51.100.8"}).status_code == 200
    # From anywhere else the header is ignored, so it can't be used to dodge the limit.
    direct = make_client(throttle=throttle, client=("203.0.113.5", 40000))
    direct.get("/api/status", headers={**bad, "X-Forwarded-For": "10.9.9.9"})
    assert "203.0.113.5" in throttle.by_ip and "10.9.9.9" not in throttle.by_ip
    r = direct.get("/api/status", headers={**AUTH, "X-Forwarded-For": "10.9.9.10"})
    assert r.status_code == 429


def test_throttle_window_expires():
    now = [0.0]
    t = LoginThrottle(per_ip=2, overall=3, window=60, clock=lambda: now[0])
    t.record_failure("a"); t.record_failure("a")
    assert t.retry_after("a") == pytest.approx(60) and t.retry_after("b") == 0
    t.record_failure("b")
    assert t.retry_after("c") > 0  # overall limit reached
    now[0] = 61
    assert t.retry_after("a") == 0 and t.retry_after("c") == 0


# -- sync validation -------------------------------------------------------------------------


@pytest.mark.parametrize("body,status", [
    (b"not json", 400),
    (b"[]", 422),
    (b'{"rolls": []}', 422),
    (json.dumps({"rolls": {"../evil": {"name": "x", "entries": []}}}).encode(), 422),
    (json.dumps({"rolls": {"r1": {"name": 5, "entries": []}}}).encode(), 422),
    (json.dumps({"rolls": {"r1": {"name": "x", "entries": {}}}}).encode(), 422),
    (json.dumps({"rolls": {"r1": {"name": "x", "entries": ["x"]}}}).encode(), 422),
    (json.dumps({"rolls": {"r1": {"name": "x", "entries": [{"frame": "1"}]}}}).encode(), 422),
    (json.dumps({"rolls": {"r1": {"name": "x", "entries": [{"lat": True}]}}}).encode(), 422),
    (json.dumps({"rolls": {"r1": {"name": "x", "entries": [{"notes": 3}]}}}).encode(), 422),
    (json.dumps({"currentRoll": 1, "rolls": {}}).encode(), 422),
])
def test_sync_rejects_bad_shapes(make_client, body, status):
    c = make_client()
    r = c.post("/api/sync", content=body, headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == status, r.text
    assert c.store.snapshot_count == 0


def test_sync_accepts_old_data_and_unknown_fields(make_client):
    # A frame # stored as NaN by an old version of the app arrives as null; schema-1 entries lack tz.
    state = make_state({"r1": {"name": "old", "entries": [
        {**entry(1), "frame": None}, {k: v for k, v in entry(2).items() if k != "tz"} | {"future": [1, 2]}]}})
    assert make_client().post("/api/sync", json=state, headers=AUTH).status_code == 200


def test_sync_enforces_the_body_size_limit(make_client):
    c = make_client(max_body_bytes=2000)
    big = make_state({"r1": {"name": "x", "entries": [entry(i, notes="n" * 100) for i in range(1, 30)]}})
    r = c.post("/api/sync", json=big, headers=AUTH)
    assert r.status_code == 413
    # Also when the client doesn't declare a length (chunked upload).
    data = json.dumps(big).encode()
    r = c.post("/api/sync", content=iter([data[:1000], data[1000:]]),
               headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert c.store.snapshot_count == 0


# -- storage ---------------------------------------------------------------------------------


def snapshots(c):
    return sorted(c.store.snapshots.glob("*/*.json"))


def test_sync_writes_a_snapshot_and_dedupes(make_client):
    c = make_client()
    state = make_state()
    r = c.post("/api/sync", json=state, headers=AUTH).json()
    assert r["stored"] is True and r["snapshot_count"] == 1
    snap = json.loads(snapshots(c)[0].read_text(encoding="utf-8"))
    assert snap["state"] == state and snap["kind"] == "snapshot"

    # Same rolls again, even with another current roll selected: no new snapshot.
    r = c.post("/api/sync", json={**state, "currentRoll": "r9"}, headers=AUTH).json()
    assert r["stored"] is False and r["snapshot_count"] == 1
    assert len(snapshots(c)) == 1

    state["rolls"]["r1"]["entries"].append(entry(3))
    assert c.post("/api/sync", json=state, headers=AUTH).json()["stored"] is True
    assert len(snapshots(c)) == 2

    status = c.get("/api/status", headers=AUTH).json()
    assert status["snapshot_count"] == 2 and status["last_sync_at"] and status["s3"]["state"] == "disabled"


def test_dedupe_survives_a_restart(make_client, tmp_path):
    c = make_client()
    c.post("/api/sync", json=make_state(), headers=AUTH)
    (c.settings.data_dir / "state.json").unlink()  # even with the bookkeeping file gone
    c2 = make_client()
    assert c2.post("/api/sync", json=make_state(), headers=AUTH).json()["stored"] is False
    assert c2.store.snapshot_count == 1


def test_deleting_a_roll_on_the_phone_deletes_nothing(make_client):
    c = make_client()
    two = make_state({"r1": {"name": "A", "entries": [entry(1)]}, "r2": {"name": "B", "entries": [entry(1)]}})
    c.post("/api/sync", json=two, headers=AUTH)
    before = {p: p.read_bytes() for p in c.store.backup_files()}
    c.post("/api/sync", json=make_state({"r1": {"name": "A", "entries": [entry(1)]}}), headers=AUTH)
    c.post("/api/sync", json=make_state({}), headers=AUTH)  # every roll deleted
    for p, data in before.items():
        assert p.exists() and p.read_bytes() == data
    assert (c.store.rolls / "b--r2.json").exists()
    assert len(snapshots(c)) == 3


def test_snapshots_are_never_overwritten(make_client):
    from datetime import datetime, timezone

    c = make_client()
    frozen = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    c.store.clock = lambda: frozen
    a = make_state({"r1": {"name": "A", "entries": []}})
    b = make_state({"r1": {"name": "B", "entries": []}})
    for s in (a, b, a):  # a, then b, then a again in the same instant
        assert c.post("/api/sync", json=s, headers=AUTH).json()["stored"] is True
    files = snapshots(c)
    assert len(files) == 3 and len({f.name for f in files}) == 3
    names = [json.loads(f.read_text(encoding="utf-8"))["state"]["rolls"]["r1"]["name"] for f in files]
    assert sorted(names) == ["A", "A", "B"]


def _tagger_log():
    path = REPO_ROOT / "tagger" / "framelog_tagger" / "log.py"
    spec = importlib.util.spec_from_file_location("tagger_log", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tagger_log"] = mod  # dataclasses look their module up there
    spec.loader.exec_module(mod)
    return mod


def test_per_roll_file_is_the_apps_export_format(make_client):
    c = make_client()
    entries = [entry(2, notes='Flatiron, "harsh" light · café'), entry(1, lat=None, lon=None, acc=None, alt=None)]
    c.post("/api/sync", json=make_state({"r1": {"name": "2026-08 Portra 400 #3", "entries": entries}}), headers=AUTH)
    f = c.store.rolls / "2026_08_portra_400_3--r1.json"
    text = f.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert list(doc) == ["app", "schema", "exported", "roll", "rollId", "entries"]
    assert doc["app"] == "framelog" and doc["schema"] == 2 and doc["rollId"] == "r1"
    assert doc["roll"] == "2026-08 Portra 400 #3" and doc["entries"] == entries  # verbatim, in stored order
    assert text.endswith("}\n") and '\n  "app": "framelog"' in text  # JSON.stringify(_, null, 2) + "\n"
    assert "café" in text
    # And the tagger reads it as-is.
    roll = _tagger_log().read_log(f)
    assert roll.name == "2026-08 Portra 400 #3" and [e.frame for e in roll.entries] == [1, 2]
    assert roll.entries[1].notes.startswith("Flatiron")


def test_roll_file_is_rewritten_only_when_the_roll_changes_and_follows_renames(make_client):
    c = make_client()
    state = make_state({"r1": {"name": "A", "entries": [entry(1)]}, "r2": {"name": "B", "entries": []}})
    c.post("/api/sync", json=state, headers=AUTH)
    state["rolls"]["r2"]["entries"].append(entry(1))
    r = c.store.save(state)
    assert [p.name for p in r.written[1:]] == ["b--r2.json"]  # r1 unchanged, not rewritten
    state["rolls"]["r2"]["name"] = "B renamed"
    c.store.save(state)
    assert sorted(p.name for p in c.store.rolls.glob("*.json")) == ["a--r1.json", "b_renamed--r2.json"]


def test_writes_leave_no_temp_files(make_client):
    c = make_client()
    c.post("/api/sync", json=make_state(), headers=AUTH)
    leftovers = [p for p in c.settings.data_dir.rglob("*") if p.name.endswith(".tmp")]
    assert leftovers == []


# -- S3 --------------------------------------------------------------------------------------


def test_s3_disabled_without_settings(make_client, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="framelog_sync")
    c = make_client()
    assert c.backup.enabled is False
    assert c.get("/api/status", headers=AUTH).json()["s3"]["state"] == "disabled"
    assert any("S3 backup disabled" in m for m in caplog.messages)
    assert c.post("/api/sync", json=make_state(), headers=AUTH).status_code == 200


def test_s3_uploads_after_each_new_snapshot(make_client, fake_s3):
    c = make_client(s3=fake_s3)
    c.backup.wait(5)
    assert c.backup.status()["state"] == "ok"  # startup catch-up on an empty folder
    c.post("/api/sync", json=make_state(), headers=AUTH)
    c.backup.wait(5)
    keys = sorted(fake_s3.objects)
    assert len(keys) == 2 and all(k.startswith("framelog/") for k in keys)
    assert any(k.startswith("framelog/snapshots/") for k in keys)
    assert "framelog/rolls/2026_08_portra_400_3--r1.json" in keys
    status = c.get("/api/status", headers=AUTH).json()["s3"]
    assert status["state"] == "ok" and status["last_ok_at"] and status["last_error"] is None
    # A duplicate sync uploads nothing.
    n = len(fake_s3.calls)
    c.post("/api/sync", json=make_state(), headers=AUTH)
    c.backup.wait(5)
    assert len(fake_s3.calls) == n


def test_s3_failure_never_fails_the_sync_and_is_retried(make_client, fake_s3):
    c = make_client(s3=fake_s3)
    c.backup.wait(5)
    fake_s3.fail = ConnectionError("network down")
    r = c.post("/api/sync", json=make_state(), headers=AUTH)
    assert r.status_code == 200 and r.json()["stored"] is True
    c.backup.wait(5)
    s3 = c.get("/api/status", headers=AUTH).json()["s3"]
    assert s3["state"] == "error" and "network down" in s3["last_error"]
    assert fake_s3.objects == {}

    fake_s3.fail = None
    state = make_state()
    state["rolls"]["r2"] = {"name": "Second", "entries": []}
    c.post("/api/sync", json=state, headers=AUTH)
    c.backup.wait(5)
    # The catch-up sends the earlier snapshot too, not just the new files.
    assert sorted(fake_s3.objects) == sorted(c.backup.key_for(p) for p in c.store.backup_files())
    assert len([k for k in fake_s3.objects if "/snapshots/" in k]) == 2
    assert c.backup.status()["state"] == "ok"


def test_s3_failure_when_the_client_cannot_be_created(make_client):
    from framelog_sync.s3 import S3Backup

    c = make_client()
    settings = c.settings.__class__(**{**c.settings.__dict__, "s3_bucket": "b", "s3_region": "us-east-1"})

    def no_creds(_):
        raise RuntimeError("Unable to locate credentials")

    backup = S3Backup(settings, settings.data_dir, client_factory=no_creds)
    backup.submit([], c.store.backup_files)
    backup.wait(5)
    assert backup.status()["state"] == "error" and "credentials" in backup.status()["last_error"]
    backup.shutdown()


def test_startup_catch_up_uploads_what_the_bucket_lacks(make_client, fake_s3):
    c = make_client()  # S3 off: files pile up locally
    c.post("/api/sync", json=make_state(), headers=AUTH)
    state = make_state()
    state["rolls"]["r1"]["entries"].append(entry(3))
    c.post("/api/sync", json=state, headers=AUTH)
    files = c.store.backup_files()
    assert len(files) == 3

    c2 = make_client(s3=fake_s3)  # restart with S3 configured
    c2.backup.wait(5)
    assert sorted(fake_s3.objects) == sorted(c2.backup.key_for(p) for p in files)
    for p in files:
        assert fake_s3.objects[c2.backup.key_for(p)] == p.read_bytes()

    # A second pass only re-sends a roll file whose content changed, never a snapshot already there.
    roll_file = next(p for p in files if p.parent.name == "rolls")
    fake_s3.objects[c2.backup.key_for(roll_file)] = b"stale"
    fake_s3.calls.clear()
    assert c2.backup.catch_up(c2.store.backup_files()) == 1
    assert [k for op, k in fake_s3.calls if op == "put"] == [c2.backup.key_for(roll_file)]


def test_s3_verify_puts_gets_and_deletes_under_the_prefix(make_client, fake_s3):
    c = make_client(s3=fake_s3)
    c.backup.wait(5)
    key = c.backup.verify()
    assert key.startswith("framelog/_verify/")
    ops = [op for op, _ in fake_s3.calls]
    assert ops[-4:] == ["put", "get", "list", "delete"]
    assert fake_s3.objects == {}


def test_s3_prefix_can_be_empty(make_client, fake_s3):
    c = make_client(s3=fake_s3, s3_prefix="")
    c.post("/api/sync", json=make_state(), headers=AUTH)
    c.backup.wait(5)
    assert all(k.startswith(("snapshots/", "rolls/")) for k in fake_s3.objects)
