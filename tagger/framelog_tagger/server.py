"""FastAPI app: serves the pairing UI and the JSON API behind it."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .exif import ExifToolError, TagContext, build_args, write_tags
from .log import LogEntry
from .scans import ScanInfo, ThumbnailCache

STATIC = Path(__file__).parent / "static"


@dataclass
class Session:
    scans_dir: Path
    out_dir: Path
    in_place: bool
    scans: list[ScanInfo]
    entries: list[LogEntry]
    ctx: TagContext
    tz_name: str
    exiftool: Optional[str]
    thumbs: ThumbnailCache = field(default_factory=ThumbnailCache)

    def scan_by_name(self, name: str) -> Optional[ScanInfo]:
        return next((s for s in self.scans if s.name == name), None)


class Pair(BaseModel):
    scan: str    # scan filename
    entry: int   # index into session.entries


class WriteRequest(BaseModel):
    pairs: list[Pair]


def create_app(session: Session) -> FastAPI:
    app = FastAPI(title="Frame Log tagger")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/session")
    def get_session():
        return {
            "roll": session.ctx.roll,
            "tz": session.tz_name,
            "scans_dir": str(session.scans_dir),
            "out_dir": str(session.out_dir),
            "in_place": session.in_place,
            "exiftool": session.exiftool,
            "scans": [s.to_dict() for s in session.scans],
            "entries": [{"id": i, **e.to_dict()} for i, e in enumerate(session.entries)],
        }

    @app.get("/api/thumb/{name}")
    def thumb(name: str):
        scan = session.scan_by_name(name)
        if scan is None:
            raise HTTPException(404, "no such scan")
        try:
            data = session.thumbs.get(scan.path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"thumbnail failed: {e}")
        return Response(data, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/preview/{entry}")
    def preview(entry: int):
        """The ExifTool assignments that Write would apply for one entry."""
        if not 0 <= entry < len(session.entries):
            raise HTTPException(404, "no such entry")
        return {"args": build_args(session.entries[entry], session.ctx)}

    @app.post("/api/write")
    def write(req: WriteRequest):
        if not session.exiftool:
            raise HTTPException(400, "exiftool not available; restart with --exiftool <path>")
        jobs = []
        for p in req.pairs:
            scan = session.scan_by_name(p.scan)
            if scan is None:
                raise HTTPException(400, f"unknown scan {p.scan!r}")
            if not 0 <= p.entry < len(session.entries):
                raise HTTPException(400, f"unknown entry {p.entry}")
            jobs.append((scan, p.entry, session.entries[p.entry]))

        def run(job):
            scan, idx, entry = job
            dst = scan.path if session.in_place else session.out_dir / scan.name
            try:
                write_tags(session.exiftool, scan.path, dst, build_args(entry, session.ctx))
                scan.tagged = True
                return {"scan": scan.name, "entry": idx, "ok": True, "path": str(dst)}
            except (ExifToolError, OSError) as e:
                return {"scan": scan.name, "entry": idx, "ok": False, "error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, jobs))
        return {"results": results, "written": sum(r["ok"] for r in results)}

    return app
