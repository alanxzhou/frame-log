import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framelog_sync.app import LoginThrottle, create_app
from framelog_sync.config import REPO_ROOT, Settings
from framelog_sync.s3 import S3Backup
from framelog_sync.store import Store

PASSWORD = "test-password-Xq7v9Lm2Rt"  # a test value, not used anywhere real
AUTH = {"Authorization": f"Bearer {PASSWORD}"}


def make_settings(tmp_path: Path, **over) -> Settings:
    values = dict(password=PASSWORD, port=8766, data_dir=tmp_path / "data", static_dir=REPO_ROOT,
                  forwarded_allow_ips="127.0.0.1,::1", max_body_bytes=200_000,
                  s3_bucket="", s3_region="", s3_prefix="framelog/", aws_profile="")
    values.update(over)
    return Settings(**values)


class FakeS3:
    """Just enough of boto3's S3 client for the backup code. No network."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def _check(self, op, key=""):
        self.calls.append((op, key))
        if self.fail:
            raise self.fail

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self._check("put", Key)
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        self._check("get", Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self._check("delete", Key)
        self.objects.pop(Key, None)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                fake._check("list", Prefix)
                items = [{"Key": k, "ETag": '"' + hashlib.md5(v).hexdigest() + '"'}
                         for k, v in sorted(fake.objects.items()) if k.startswith(Prefix)]
                yield {"Contents": items} if items else {}

        return Paginator()


def make_state(rolls=None, current="r1"):
    if rolls is None:
        rolls = {"r1": {"name": "2026-08 Portra 400 #3", "entries": [entry(1), entry(2)]}}
    return {"currentRoll": current, "rolls": rolls}


def entry(frame, **over):
    e = {"frame": frame, "iso": f"2026-08-30T14:0{frame % 10}:12.000Z", "local": "8/30/2026, 10:05:12 AM",
         "tz": "America/New_York", "lat": 40.741895, "lon": -73.989308, "acc": 8, "alt": 12,
         "lens": "50mm f/1.4", "aperture": "f/2.8", "shutterSpeed": "1/250", "notes": ""}
    e.update(over)
    return e


@pytest.fixture
def fake_s3():
    return FakeS3()


@pytest.fixture
def make_client(tmp_path):
    """Build a TestClient; pass s3=FakeS3() to turn the backup on, client=(ip, port) to set the peer."""
    clients = []

    def build(s3=None, client=("127.0.0.1", 50000), throttle=None, **settings_over):
        if s3 is not None:
            settings_over.setdefault("s3_bucket", "test-bucket")
            settings_over.setdefault("s3_region", "us-east-1")
        settings = make_settings(tmp_path, **settings_over)
        store = Store(settings.data_dir)
        backup = S3Backup(settings, settings.data_dir, client_factory=(lambda s: s3) if s3 else None)
        app = create_app(settings, store=store, backup=backup, throttle=throttle or LoginThrottle(),
                         fail_delay=0)
        c = TestClient(app, client=client)
        c.__enter__()  # run the lifespan (startup catch-up)
        clients.append(c)
        c.store, c.backup, c.settings = store, backup, settings
        return c

    yield build
    for c in clients:
        c.__exit__(None, None, None)
