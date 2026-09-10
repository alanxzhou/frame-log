"""framelog-tagger: pair a Frame Log CSV with lab scans and write the metadata."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .exif import TagContext, exiftool_version, find_exiftool
from .log import LogFormatError, read_log
from .scans import list_scans
from .server import Session, create_app


def _local_tz_name() -> str:
    """IANA name for this machine's zone, or '' if it can't be determined."""
    try:
        from tzlocal import get_localzone_name
        return get_localzone_name() or ""
    except Exception:  # noqa: BLE001
        pass
    tz = datetime.now().astimezone().tzinfo
    return getattr(tz, "key", "") or ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="framelog-tagger",
        description="Pair Frame Log CSV entries with lab scans in a browser UI, then write EXIF/XMP.",
    )
    p.add_argument("scans", type=Path, help="folder of JPEG scans from the lab")
    p.add_argument("log", type=Path, help="Frame Log CSV export for that roll")
    p.add_argument("--out", type=Path, default=None,
                   help="write tagged copies here (default: <scans>/tagged); originals untouched")
    p.add_argument("--in-place", action="store_true",
                   help="write into the scan files themselves instead of copies")
    p.add_argument("--tz", default="",
                   help="IANA zone the roll was shot in, e.g. America/New_York "
                        "(default: this machine's zone). Log timestamps are UTC.")
    p.add_argument("--roll", default="", help="roll name (default: CSV filename)")
    p.add_argument("--camera", default="", help="camera body -> EXIF Model")
    p.add_argument("--make", default="", help="camera maker -> EXIF Make")
    p.add_argument("--film", default="", help="film stock, recorded in UserComment")
    p.add_argument("--iso", type=int, default=None, help="film speed -> EXIF ISO")
    p.add_argument("--tag", action="append", default=[], metavar="ARG",
                   help="extra ExifTool assignment for every frame, e.g. --tag=-Artist=Alan")
    p.add_argument("--exiftool", default=None, help="path to exiftool if it isn't on PATH")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="don't open the UI automatically")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.scans.is_dir():
        print(f"error: scans folder not found: {args.scans}", file=sys.stderr)
        return 2
    if not args.log.is_file():
        print(f"error: log CSV not found: {args.log}", file=sys.stderr)
        return 2

    tz_name = args.tz or _local_tz_name()
    try:
        tz = ZoneInfo(tz_name) if tz_name else None
    except ZoneInfoNotFoundError:
        tz = None
    if tz is None:
        if args.tz:
            print(f"error: unknown timezone {args.tz!r}; use an IANA name like America/New_York",
                  file=sys.stderr)
            return 2
        print("warning: could not determine this machine's IANA timezone; falling back to UTC. "
              "Pass --tz America/New_York (or similar) for correct local times.", file=sys.stderr)
        tz, tz_name = ZoneInfo("UTC"), "UTC"

    try:
        entries = read_log(args.log)
    except LogFormatError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    scans = list_scans(args.scans)

    exe = find_exiftool(args.exiftool)
    if exe:
        try:
            print(f"exiftool {exiftool_version(exe)}: {exe}")
        except Exception as e:  # noqa: BLE001
            print(f"warning: exiftool at {exe} failed to run: {e}", file=sys.stderr)
            exe = None
    if not exe:
        print("warning: exiftool not found; you can pair frames but Write is disabled.\n"
              "  mac:     brew install exiftool\n"
              "  windows: download from https://exiftool.org, rename the exe to exiftool.exe, "
              "put it on PATH\n"
              "  or pass --exiftool <path>", file=sys.stderr)

    out_dir = args.scans if args.in_place else (args.out or args.scans / "tagged")
    ctx = TagContext(tz=tz, roll=args.roll or args.log.stem, camera=args.camera, make=args.make,
                     film=args.film, iso=args.iso, extra_args=list(args.tag))
    session = Session(scans_dir=args.scans, out_dir=out_dir, in_place=args.in_place,
                      scans=scans, entries=entries, ctx=ctx, tz_name=tz_name, exiftool=exe)

    print(f"{len(scans)} scans in {args.scans}")
    print(f"{len(entries)} logged frames in {args.log.name}  (tz {tz_name})")
    print(f"output: {'in place' if args.in_place else out_dir}")

    import uvicorn
    app = create_app(session)
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"open {url}  (Ctrl+C to quit)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
