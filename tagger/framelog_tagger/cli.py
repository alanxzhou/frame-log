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


FACES_HINT = 'install them with:  uv tool install ".[faces]"   (from the tagger folder)'


def load_faces():
    """(engine, gallery, why not): the face tools if their dependencies are installed, else None and a reason."""
    try:
        from . import faces
    except ImportError:
        return None, None, "face tagging needs numpy and the faces extra; " + FACES_HINT
    missing = faces.missing_dependency()
    if missing:
        return None, None, f"face tagging needs {missing}; " + FACES_HINT
    return faces.Engine(), faces.Gallery(), ""


def faces_main(argv: list[str]) -> int:
    """`framelog-tagger faces ...`: manage the gallery of faces you've named."""
    p = argparse.ArgumentParser(prog="framelog-tagger faces", description="Manage the gallery of faces used to suggest names.")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="learn from photos that already have named face regions (e.g. from Lightroom)")
    imp.add_argument("folders", nargs="+", help="folders searched recursively for JPEGs")
    imp.add_argument("--exclude", nargs="*", default=[], metavar="NAME",
                     help="names to skip: pets, or anyone who isn't a person (a human-face model can't tell animals apart)")
    imp.add_argument("--exiftool", default=None, help="path to exiftool if it isn't on PATH")
    ppl = sub.add_parser("people", help="list who is in the gallery and how many faces each has")
    ppl.add_argument("--min", type=int, default=1, help="only people with at least this many faces")
    fg = sub.add_parser("forget", help="remove a person, and every face filed under them, from the gallery")
    fg.add_argument("name")
    args = p.parse_args(argv)

    try:
        from . import faces
    except ImportError:
        print("face tagging needs numpy and the faces extra; " + FACES_HINT, file=sys.stderr)
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

    missing = faces.missing_dependency()
    if missing:
        print(f"face tagging needs {missing}; " + FACES_HINT, file=sys.stderr)
        return 2
    exe = find_exiftool(args.exiftool)
    if not exe:
        print("exiftool not found (it reads the face regions); pass --exiftool <path>", file=sys.stderr)
        return 2
    from .regions import read_regions
    print("reading face regions with exiftool (a big library takes a few minutes)...", flush=True)
    images = read_regions(exe, args.folders)
    named = sum(1 for im in images for f in im.faces if f.name)
    print(f"{len(images)} images with face regions, {named} named faces. Detecting and fingerprinting "
          f"(about 0.4 s per image on a CPU); safe to stop and resume.", flush=True)

    def progress(i: int, n: int, added: int) -> None:
        if i % 25 == 0 or i == n:
            print(f"\r  {i}/{n} images, {added} faces added", end="", flush=True)

    stats = faces.import_tagged(faces.Engine(), gallery, images, args.exclude, progress)
    print(f"\ndone: {stats['added']} faces added, {stats['unmatched']} tagged faces the detector didn't find, "
          f"{stats['skipped']} images already imported. Gallery: {len(gallery)} faces, {len(gallery.people())} people.")
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
    if engine:
        print(f"face tagging on: {len(gallery)} faces, {len(gallery.people())} people in the gallery")
    state = AppState(exiftool=exe, default_tz=local_tz_name(), remembered=config.load(),
                     engine=engine, gallery=gallery, faces_note=faces_note)

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
