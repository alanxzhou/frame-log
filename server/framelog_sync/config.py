"""All settings come from environment variables, normally read from server/.env.

`load_settings` takes the environment as a mapping so tests can pass their own and never
pick up a real .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

SERVER_DIR = Path(__file__).resolve().parent.parent  # server/
REPO_ROOT = SERVER_DIR.parent                        # where index.html, sw.js, ... live
ENV_FILE = SERVER_DIR / ".env"

MIN_PASSWORD_LENGTH = 16
MIN_DISTINCT_CHARS = 8
# Values from .env.example and other obvious placeholders that must never be accepted.
PLACEHOLDER_PASSWORDS = {"change-me-to-a-long-random-string"}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    password: str
    port: int
    data_dir: Path
    static_dir: Path
    forwarded_allow_ips: str
    max_body_bytes: int
    s3_bucket: str
    s3_region: str
    s3_prefix: str
    aws_profile: str

    @property
    def s3_enabled(self) -> bool:
        return bool(self.s3_bucket)


def _int(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number (got {raw!r}).")
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum} (got {value}).")
    return value


def check_password(password: str) -> None:
    """Refuse a missing or weak SYNC_PASSWORD: the API is reachable from the whole internet."""
    if not password:
        raise ConfigError(
            "Set SYNC_PASSWORD in server/.env. Generate one with: "
            "uv run python -c \"import secrets; print(secrets.token_urlsafe(24))\"")
    if password.strip() != password:
        raise ConfigError("SYNC_PASSWORD has leading or trailing spaces; remove them.")
    if password in PLACEHOLDER_PASSWORDS:
        raise ConfigError("SYNC_PASSWORD is still the example value; generate a real one.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ConfigError(f"SYNC_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(set(password)) < MIN_DISTINCT_CHARS:
        raise ConfigError(f"SYNC_PASSWORD needs at least {MIN_DISTINCT_CHARS} different characters.")


def normalize_prefix(prefix: str) -> str:
    """'' stays '' (bucket root); anything else becomes 'a/b/' with no leading slash."""
    prefix = prefix.strip().strip("/")
    return prefix + "/" if prefix else ""


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    password = env.get("SYNC_PASSWORD") or ""
    check_password(password)

    data_dir = Path((env.get("DATA_DIR") or "").strip() or "data").expanduser()
    if not data_dir.is_absolute():
        data_dir = SERVER_DIR / data_dir

    static_dir = Path((env.get("STATIC_DIR") or "").strip() or REPO_ROOT).expanduser()
    if not static_dir.is_absolute():
        static_dir = SERVER_DIR / static_dir

    bucket = (env.get("S3_BUCKET") or "").strip()
    region = (env.get("S3_REGION") or "").strip() or (env.get("AWS_REGION") or "").strip() \
        or (env.get("AWS_DEFAULT_REGION") or "").strip()
    if bucket and not region:
        raise ConfigError("S3_BUCKET is set, so set S3_REGION too (the bucket's region, e.g. us-east-1).")

    return Settings(
        password=password,
        port=_int(env, "PORT", 8766, 1),
        data_dir=data_dir,
        static_dir=static_dir,
        # Only these addresses may tell us the visitor's real address (X-Forwarded-For), which the
        # failed-password limit counts by. The default, this computer, fits cloudflared running here.
        forwarded_allow_ips=(env.get("FORWARDED_ALLOW_IPS") or "").strip() or "127.0.0.1,::1",
        max_body_bytes=_int(env, "MAX_BODY_BYTES", 10 * 1024 * 1024, 1024),
        s3_bucket=bucket,
        s3_region=region,
        s3_prefix=normalize_prefix(env.get("S3_PREFIX", "framelog")),
        aws_profile=(env.get("AWS_PROFILE") or "").strip(),
    )
