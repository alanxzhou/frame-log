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
    p.epilog = "Face tagging: `framelog-tagger faces --help` (the gallery of people used to suggest names)."
    return p


def load_faces():
    """(engine, gallery, why not): the face tools, or None and a reason if they can't load (a broken install)."""
    try:
        from . import faces
    except ImportError as e:
        return None, None, f"face tagging couldn't load ({e}); reinstall the tagger"
    missing = faces.missing_dependency()
    if missing:
        return None, None, f"face tagging couldn't load: {missing} isn't installed; reinstall the tagger"
    return faces.Engine(), faces.Gallery(), ""


def faces_main(argv: list[str]) -> int:
    """`framelog-tagger faces ...`: manage the gallery of faces you've named."""
    p = argparse.ArgumentParser(prog="framelog-tagger faces", description="Manage the gallery of faces used to suggest names.")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="learn from photos that already have named face regions (e.g. from Lightroom). "
                                        "The folder and --exclude are remembered, and the tagger repeats this on every start.")
    imp.add_argument("folders", nargs="*", help="folders searched recursively for JPEGs; default: the remembered ones")
    imp.add_argument("--exclude", nargs="*", default=None, metavar="NAME",
                     help="names that aren't people (pets); replaces the remembered list. A human-face model can't tell animals apart")
    imp.add_argument("--exiftool", default=None, help="path to exiftool if it isn't on PATH")
    imp.add_argument("--again", action="store_true",
                     help="read every photo again, not just new ones (after changing names in Lightroom)")
    ppl = sub.add_parser("people", help="list who is in the gallery and how many faces each has")
    ppl.add_argument("--min", type=int, default=1, help="only people with at least this many faces")
    fg = sub.add_parser("forget", help="remove a person, and every face filed under them, from the gallery")
    fg.add_argument("name")
    args = p.parse_args(argv)

    try:
        from . import faces
    except ImportError as e:
        print(f"face tagging couldn't load ({e}); reinstall the tagger", file=sys.stderr)
        return 2
    gallery = faces.Gallery()

    if args.cmd == "people":
        for name, n in gallery.people():
            if n >= args.min:
                print(f"{n:6d}  {name}")
        print(f"{len(gallery)} faces, {len(gallery.people())} people  ({gallery.path})")
        return 0
    if args.cmd == "forget":
        gone = gallery.forget(args.name)
        gallery.save()
        print(f"removed {gone} face(s) named {args.name!r}")
        return 0

    settings = config.load_faces_settings()
    folders = args.folders or settings["library"]
    if not folders:
        print("give the folder that holds your Lightroom-tagged photos; it's remembered after this", file=sys.stderr)
        return 2
    exe = find_exiftool(args.exiftool)
    if not exe:
        print("exiftool not found (it reads the face regions); pass --exiftool <path>", file=sys.stderr)
        return 2
    removed = faces.update_settings(gallery, settings, folders, settings["exclude"] if args.exclude is None else args.exclude)
    if removed:
        print(f"removed {removed} faces of names that aren't people")
    if args.again:
        gallery.clear_done()

    def progress(i: int, n: int, added: int) -> None:
        if i % 25 == 0 or i == n:
            print(f"\r  {i}/{n} images, {added} faces added", end="", flush=True)

    print("reading face regions with exiftool (a big library takes a minute or two)...", flush=True)
    stats = faces.learn_library(faces.Engine(), gallery, exe, folders, settings["exclude"], progress=progress)
    print(f"\ndone: {stats['added']} faces added from {stats['tagged_images']} tagged photos, {stats['unmatched']} tagged "
          f"faces the detector didn't find, {stats['skipped']} photos already learned. "
          f"Gallery: {len(gallery)} faces, {len(gallery.people())} people.")
    return 0

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "faces":
        return faces_main(argv[1:])
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

    engine, gallery, faces_note = load_faces()
    settings = config.load_faces_settings()
    sync = None
    if engine:
        from .faces import Sync
        sync = Sync(engine, gallery, exe, settings)
        print(f"face tagging on: {len(gallery)} faces, {len(gallery.people())} people in the gallery")
        if sync.start():
            where = ", ".join(settings["library"])
            print(f"learning in the background from {where} (new photos only, after the first time)")
    state = AppState(exiftool=exe, default_tz=local_tz_name(), remembered=config.load(), engine=engine,
                     gallery=gallery, faces_note=faces_note, sync=sync, face_settings=settings)

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
