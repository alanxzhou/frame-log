"""Optional S3 backup of the data folder.

Disabled unless S3_BUCKET is set. Uploads run on one background thread, so a sync request never
waits on S3 or fails because of it. A failed upload is recorded (shown by /api/status) and the
next upload does a full catch-up pass instead: list what is in the bucket under the prefix and
upload every local file that is missing there or (for the per-roll files, which change) differs.
The same catch-up runs once at startup and from `framelog-sync s3-catchup`.

Nothing is ever deleted from the bucket, except the small test object `s3-verify` writes.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from .config import Settings

log = logging.getLogger("framelog_sync.s3")

VERIFY_FOLDER = "_verify/"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_client(settings: Settings):
    """A boto3 S3 client. Credentials come from boto3's usual chain: AWS_ACCESS_KEY_ID /
    AWS_SECRET_ACCESS_KEY in the environment (server/.env is loaded into it), then AWS_PROFILE or
    the default profile in ~/.aws/credentials."""
    import boto3
    from botocore.config import Config

    session = boto3.session.Session(profile_name=settings.aws_profile or None)
    return session.client(
        "s3",
        region_name=settings.s3_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=60),
    )


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()


class S3Backup:
    def __init__(self, settings: Settings, data_dir: Path,
                 client_factory: Optional[Callable[[Settings], object]] = None):
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.enabled = settings.s3_enabled
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix
        self._client_factory = client_factory or make_client
        self._client = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: list[Future] = []
        self._lock = threading.Lock()
        self.needs_catch_up = True  # until a catch-up pass has succeeded
        self.state = "disabled" if not self.enabled else "pending"
        self.last_ok_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[str] = None
        self.uploaded = 0

    @property
    def target(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"

    def describe(self) -> str:
        if not self.enabled:
            return "S3 backup disabled: S3_BUCKET is not set in server/.env"
        return f"S3 backup enabled: {self.target} (region {self.settings.s3_region})"

    def status(self) -> dict:
        return {"state": self.state, "last_ok_at": self.last_ok_at,
                "last_error": self.last_error, "last_error_at": self.last_error_at}

    @property
    def client(self):
        if self._client is None:
            self._client = self._client_factory(self.settings)
        return self._client

    def key_for(self, path: Path) -> str:
        return self.prefix + Path(path).relative_to(self.data_dir).as_posix()

    # -- the work itself (run on the background thread, or directly from the CLI) --

    def upload(self, paths: Iterable[Path]) -> int:
        n = 0
        for p in paths:
            p = Path(p)
            if not p.exists():  # e.g. a roll file replaced by a rename since it was queued
                continue
            self.client.put_object(Bucket=self.bucket, Key=self.key_for(p), Body=p.read_bytes(),
                                   ContentType="application/json")
            n += 1
        return n

    def remote_etags(self) -> dict[str, str]:
        etags = {}
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                etags[obj["Key"]] = obj.get("ETag", "").strip('"')
        return etags

    def catch_up(self, files: Iterable[Path]) -> int:
        """Upload every file not yet in the bucket. Snapshots never change, so being there is
        enough; a roll file is also re-uploaded when its content differs (single-part uploads have
        the content's MD5 as ETag; with SSE-KMS they don't, and roll files are re-sent each pass)."""
        remote = self.remote_etags()
        todo = []
        for p in files:
            key = self.key_for(p)
            if key not in remote:
                todo.append(p)
            elif Path(p).relative_to(self.data_dir).parts[0] == "rolls" and remote[key] != _md5(p):
                todo.append(p)
        return self.upload(todo)

    def verify(self) -> str:
        """Put, read back and delete a small object under the prefix. Raises on any failure."""
        key = f"{self.prefix}{VERIFY_FOLDER}{uuid.uuid4().hex}.txt"
        body = f"framelog-sync verify {_now()}".encode()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="text/plain")
        got = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        if got != body:
            raise RuntimeError(f"read back different content from {key}")
        self.remote_etags()  # ListBucket permission
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return key

    # -- background scheduling --

    def _run(self, paths: list[Path], all_files: Callable[[], list[Path]]) -> None:
        with self._lock:
            try:
                if self.needs_catch_up:
                    n = self.catch_up(all_files())
                    self.needs_catch_up = False
                else:
                    n = self.upload(paths)
            except Exception as exc:  # recorded for /api/status; retried as a catch-up next time
                self.needs_catch_up = True
                self.state = "error"
                self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                self.last_error_at = _now()
                log.warning("S3 backup failed (will catch up on the next sync): %s", self.last_error)
                return
            self.uploaded += n
            self.state = "ok"
            self.last_ok_at = _now()
            if n:
                log.info("S3 backup: uploaded %d file(s) to %s", n, self.target)

    def submit(self, paths: list[Path], all_files: Callable[[], list[Path]]) -> Optional[Future]:
        """Queue an upload of `paths` (or a full catch-up if one is due). Never blocks or raises."""
        if not self.enabled:
            return None
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="s3-backup")
        fut = self._executor.submit(self._run, list(paths), all_files)
        self._futures = [f for f in self._futures if not f.done()] + [fut]
        return fut

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until queued uploads finish (tests, shutdown)."""
        for f in list(self._futures):
            f.result(timeout=timeout)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
