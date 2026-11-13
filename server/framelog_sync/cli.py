"""`framelog-sync` (serve), `framelog-sync s3-verify`, `framelog-sync s3-catchup`."""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from .config import ENV_FILE, ConfigError, load_settings

HOST = "127.0.0.1"  # only reachable from this computer, i.e. through the tunnel; never 0.0.0.0


def _settings():
    load_dotenv(ENV_FILE)
    try:
        return load_settings()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(2)


def serve(args) -> int:
    import uvicorn

    from .app import create_app

    settings = _settings()
    port = args.port or settings.port
    print(f"\n  Frame Log: http://{HOST}:{port}   (data: {settings.data_dir})\n")
    # proxy_headers is off here because create_app adds ProxyHeadersMiddleware itself, trusting
    # X-Forwarded-For only from FORWARDED_ALLOW_IPS.
    uvicorn.run(create_app(settings), host=HOST, port=port, proxy_headers=False,
                server_header=False, log_level="info")
    return 0


def _backup():
    from .s3 import S3Backup
    from .store import Store

    settings = _settings()
    backup = S3Backup(settings, settings.data_dir)
    print(backup.describe())
    if not backup.enabled:
        sys.exit(1)
    return backup, Store(settings.data_dir)


def s3_verify(args) -> int:
    backup, _ = _backup()
    try:
        key = backup.verify()
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: wrote, read back, listed and deleted s3://{backup.bucket}/{key}")
    return 0


def s3_catchup(args) -> int:
    backup, store = _backup()
    files = store.backup_files()
    try:
        n = backup.catch_up(files)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(files)} local file(s), uploaded {n} that the bucket was missing or had older.")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="framelog-sync", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("serve", help="run the server (the default)")
    p.add_argument("--port", type=int, help="override PORT from .env")
    sub.add_parser("s3-verify", help="check the S3 settings: put, get, list and delete a test object")
    sub.add_parser("s3-catchup", help="upload everything in the data folder that the bucket lacks")
    args = parser.parse_args(argv)
    if args.cmd in (None, "serve"):
        if args.cmd is None:
            args.port = None
        return serve(args)
    return {"s3-verify": s3_verify, "s3-catchup": s3_catchup}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
