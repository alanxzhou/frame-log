"""FastAPI app: serves the pairing UI and the JSON API behind it.

Lifecycle: the app starts with no roll loaded. The UI shows a setup screen
whose pickers call /api/pick (native dialogs) and whose Load button posts to
/api/load, which builds a Session. The strip endpoints need a Session and
answer 409 until one exists.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import config
from .dialogs import pick as native_pick
from .exif import ExifToolError, TagContext, build_args, resolve_zone, write_tags
from .log import LogEntry, LogFormatError, read_log
from .scans import ScanInfo, ThumbnailCache, list_scans

STATIC = Path(__file__).parent / "static"


@dataclass
class Session:
    scans_dir: Path
    log_path: Path
    out_dir: Path
    in_place: bool
    scans: list[ScanInfo]
    entries: list[LogEntry]
    ctx: TagContext
    tz_name: str
    thumbs: ThumbnailCache = field(default_factory=ThumbnailCache)

    def scan_by_name(self, name: str) -> Optional[ScanInfo]:
        return next((s for s in self.scans if s.name == name), None)


@dataclass
class AppState:
    exiftool: Optional[str]
    default_tz: str                      # this machine's zone, offered as the fallback
    session: Optional[Session] = None
    remembered: dict = field(default_factory=dict)


class Pair(BaseModel):
    scan: str    # scan filename
    entry: int   # index into session.entries


class WriteRequest(BaseModel):
    pairs: list[Pair]


class PickRequest(BaseModel):
    kind: str            # "folder" | "file"
    title: str = ""
    initial: str = ""


class LoadRequest(BaseModel):
    scans_dir: str
    log_path: str
    out_dir: str = ""    # default <scans_dir>/tagged
    in_place: bool = False
    tz: str = ""         # fallback zone; default: the machine's
    roll: str = ""       # default: name stored in the export
    camera: str = ""
    make: str = ""
    film: str = ""
    iso: Optional[int] = None


def build_session(req: LoadRequest, default_tz: str) -> Session:
    """Validate a LoadRequest and turn it into a Session. Raises ValueError with a user-facing message."""
    scans_dir = Path(req.scans_dir).expanduser()
    log_path = Path(req.log_path).expanduser()
    if not scans_dir.is_dir():
        raise ValueError(f"scans folder not found: {scans_dir}")
    if not log_path.is_file():
        raise ValueError(f"log file not found: {log_path}")
    tz_name = req.tz.strip() or default_tz
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone {tz_name!r}; use an IANA name like America/New_York")
    try:
        log = read_log(log_path)
    except LogFormatError as e:
        raise ValueError(str(e))
    scans = list_scans(scans_dir)
    if not scans:
        raise ValueError(f"no JPEGs in {scans_dir}")
    out_dir = scans_dir if req.in_place else (Path(req.out_dir).expanduser() if req.out_dir.strip()
                                              else scans_dir / "tagged")
    ctx = TagContext(tz=tz, roll=req.roll.strip() or log.name, camera=req.camera.strip(),
                     make=req.make.strip(), film=req.film.strip(), iso=req.iso)
    return Session(scans_dir=scans_dir, log_path=log_path, out_dir=out_dir, in_place=req.in_place,
                   scans=scans, entries=log.entries, ctx=ctx, tz_name=tz_name)


def session_payload(state: AppState) -> dict:
    s = state.session
    base = {
        "loaded": s is not None,
        "exiftool": state.exiftool,
        "default_tz": state.default_tz,
        "remembered": state.remembered,
    }
    if s is None:
        return base
    entries = []
    for i, e in enumerate(s.entries):
        zone, source = resolve_zone(e, s.ctx)
        local = e.utc.astimezone(zone)
        off = local.utcoffset()
        secs = int(off.total_seconds()) if off else 0
        entries.append({
            "id": i, **e.to_dict(),
            "zone": zone.key, "zone_source": source,
            "local": local.strftime("%b %d, %H:%M:%S"),
            "offset": f"{'+' if secs >= 0 else '-'}{abs(secs) // 3600:02d}:{(abs(secs) % 3600) // 60:02d}",
        })
    return {
        **base,
        "roll": s.ctx.roll,
        "tz": s.tz_name,
        "scans_dir": str(s.scans_dir),
        "log_path": str(s.log_path),
        "out_dir": str(s.out_dir),
        "in_place": s.in_place,
        "scans": [sc.to_dict() for sc in s.scans],
        "entries": entries,
    }


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="Frame Log tagger")

    def need_session() -> Session:
        if state.session is None:
            raise HTTPException(409, "no roll loaded")
        return state.session

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/session")
    def get_session():
        return session_payload(state)

    @app.post("/api/pick")
    def pick(req: PickRequest):
        return native_pick(req.kind, req.title, req.initial or None)

    @app.post("/api/load")
    def load(req: LoadRequest):
        try:
            state.session = build_session(req, state.default_tz)
        except ValueError as e:
            raise HTTPException(400, str(e))
        state.remembered = {**req.model_dump(exclude={"roll"}), "iso": req.iso}
        config.save(state.remembered)
        return session_payload(state)

    @app.post("/api/unload")
    def unload():
        state.session = None
        return session_payload(state)

    @app.get("/api/thumb/{name}")
    def thumb(name: str):
        s = need_session()
        scan = s.scan_by_name(name)
        if scan is None:
            raise HTTPException(404, "no such scan")
        try:
            data = s.thumbs.get(scan.path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"thumbnail failed: {e}")
        return Response(data, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/preview/{entry}")
    def preview(entry: int):
        """The ExifTool assignments that Write would apply for one entry."""
        s = need_session()
        if not 0 <= entry < len(s.entries):
            raise HTTPException(404, "no such entry")
        return {"args": build_args(s.entries[entry], s.ctx)}

    @app.post("/api/write")
    def write(req: WriteRequest):
        s = need_session()
        if not state.exiftool:
            raise HTTPException(400, "exiftool not available; restart with --exiftool <path>")
        jobs = []
        for p in req.pairs:
            scan = s.scan_by_name(p.scan)
            if scan is None:
                raise HTTPException(400, f"unknown scan {p.scan!r}")
            if not 0 <= p.entry < len(s.entries):
                raise HTTPException(400, f"unknown entry {p.entry}")
            jobs.append((scan, p.entry, s.entries[p.entry]))

        def run(job):
            scan, idx, entry = job
            dst = scan.path if s.in_place else s.out_dir / scan.name
            try:
                write_tags(state.exiftool, scan.path, dst, build_args(entry, s.ctx))
                scan.tagged = True
                return {"scan": scan.name, "entry": idx, "ok": True, "path": str(dst)}
            except (ExifToolError, OSError) as e:
                return {"scan": scan.name, "entry": idx, "ok": False, "error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, jobs))
        return {"results": results, "written": sum(r["ok"] for r in results)}

    return app
