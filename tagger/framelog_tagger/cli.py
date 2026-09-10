"""framelog-tagger: opens the tagger UI in your browser. Everything else happens there."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from datetime import datetime

from . import config
from .exif import exiftool_version, find_exiftool
from .server import AppState, create_app


def local_tz_name() -> str:
    """IANA name for this machine's zone, or 'UTC' if it can't be determined."""
    try:
        from tzlocal import get_localzone_name
        name = get_localzone_name()
        if name:
            return name
    except Exception:  # noqa: BLE001
        pass
    return getattr(datetime.now().astimezone().tzinfo, "key", "") or "UTC"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="framelog-tagger",
        description="Pair Frame Log entries with lab scans and write EXIF/XMP. "
                    "Pick the scans folder and the export in the browser UI.",
    )
    p.add_argument("--exiftool", default=None, help="path to exiftool if it isn't on PATH")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="don't open the UI automatically")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    state = AppState(exiftool=exe, default_tz=local_tz_name(), remembered=config.load())

    import uvicorn
    app = create_app(state)
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"open {url}  (Ctrl+C to quit)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
