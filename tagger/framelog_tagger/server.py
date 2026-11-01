"""FastAPI app: serves the pairing UI and the JSON API behind it.

Lifecycle: the app starts with no roll loaded. The UI shows a setup screen
whose pickers call /api/pick (native dialogs) and whose Load button posts to
/api/load, which builds a Session. The log export is optional: without one the
strip starts with only scans, and Write gives each the roll-wide tags. The strip endpoints need a Session and
answer 409 until one exists.

Cards (log entries) can be edited, copied and made from scratch in the UI. Each
keeps a stable id; cards read from the export keep their original values so the
UI can mark them original/edited and revert them. None of this touches the export.

Keywords: each scan's own keywords live in the UI (like the pairing) and come in
with Write and preview. The roll's (make + camera, film) are added to every scan.
The library of every keyword used is kept on disk; /api/keywords adds to and
prunes it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import config
from .dialogs import pick as native_pick
from .exif import ExifToolError, TagContext, build_args, resolve_zone, write_tags
from .keywords import Library, clean, merge, roll_keywords
from .log import LogEntry, LogFormatError, read_log
from .regions import FaceBox, iou as regions_iou, merge_regions, read_regions, region_args
from .rename import apply_rename, detect_name, plan_rename
from .scans import ScanInfo, ThumbnailCache, list_scans

try:   # a broken install (numpy or onnxruntime missing) leaves the rest of the tagger working
    from . import faces as faces_mod
    from . import pets as pets_mod
except ImportError:
    faces_mod = pets_mod = None

STATIC = Path(__file__).parent / "static"


@dataclass
class Session:
    scans_dir: Path
    log_path: Optional[Path]             # None: no export, roll-wide tags only
    out_dir: Path
    in_place: bool
    scans: list[ScanInfo]
    entries: dict[int, LogEntry]         # card id -> current values
    ctx: TagContext
    tz_name: str
    originals: dict[int, LogEntry] = field(default_factory=dict)   # cards read from the export, as read
    added: dict[int, str] = field(default_factory=dict)            # cards made here -> "new" | "copy of #N"
    next_id: int = 0
    thumbs: ThumbnailCache = field(default_factory=ThumbnailCache)
    views: ThumbnailCache = field(default_factory=lambda: ThumbnailCache(2400))   # the zoomed-in view
    detections: dict = field(default_factory=dict)   # scan name -> ((w, h), [faces.Detection]): kept so Write can learn from them

    def __post_init__(self):
        self.next_id = max(self.entries, default=-1) + 1

    def scan_by_name(self, name: str) -> Optional[ScanInfo]:
        return next((s for s in self.scans if s.name == name), None)

    def entry(self, entry_id: int) -> LogEntry:
        if entry_id not in self.entries:
            raise HTTPException(404, "no such card")
        return self.entries[entry_id]

    def add(self, entry: LogEntry, how: str) -> int:
        i = self.next_id
        self.next_id += 1
        self.entries[i] = entry
        self.added[i] = how
        return i

    def status(self, entry_id: int) -> str:
        if entry_id in self.added:
            return "added"
        return "original" if self.entries[entry_id] == self.originals[entry_id] else "edited"


@dataclass
class AppState:
    exiftool: Optional[str]
    default_tz: str                      # this machine's zone, offered as the fallback
    session: Optional[Session] = None
    remembered: dict = field(default_factory=dict)
    library: Library = field(default_factory=Library)
    engine: Optional["faces_mod.Engine"] = None      # None: the face tools couldn't load (see faces_note)
    gallery: Optional["faces_mod.Gallery"] = None
    faces_note: str = ""
    sync: Optional["faces_mod.Sync"] = None          # learns from your Lightroom folder in the background
    face_settings: dict = field(default_factory=lambda: {"library": [], "pets": [], "synced": ""})
    pet_engine: Optional["pets_mod.PetEngine"] = None   # None: no pet detector, so pets are skipped
    pet_gallery: Optional["faces_mod.Gallery"] = None


class LibraryRequest(BaseModel):
    folders: list[str] = []   # where your Lightroom-tagged photos live; the tagger learns who people are from them
    pets: list[str] = []      # tagged names that are animals: learned into the pet gallery, not the people one


class FaceTag(BaseModel):
    id: Optional[int] = None     # the detection this came from (this session), whose fingerprint Write learns from
    box: list[float]             # x, y, w, h: top-left corner and size, fractions of the upright picture
    name: str = ""               # blank: not named, so not written


class Pair(BaseModel):
    scan: str                    # scan filename
    entry: Optional[int] = None  # index into session.entries; None writes roll-wide tags only
    keywords: list[str] = []     # this scan's own keywords, on top of the roll's
    faces: Optional[list[FaceTag]] = None   # None: faces weren't looked at for this scan, leave its regions alone


class WriteRequest(BaseModel):
    pairs: list[Pair]


class CardFields(BaseModel):
    """A card's values as the editor shows them: time is local wall-clock in `tz` (or the fallback zone)."""
    frame: int
    local: str = ""      # "YYYY-MM-DDTHH:MM[:SS]"; "" for no time
    tz: str = ""         # IANA zone; "" uses the roll's fallback zone
    lat: Optional[float] = None
    lon: Optional[float] = None
    acc: Optional[float] = None
    alt: Optional[float] = None
    lens: str = ""
    aperture: str = ""
    shutter: str = ""
    notes: str = ""


class NewCard(BaseModel):
    copy_of: Optional[int] = None        # paste: duplicate this card's current values
    fields: Optional[CardFields] = None  # from scratch (ignored with copy_of)


class KeywordsRequest(BaseModel):
    add: list[str] = []      # into the library, marked used now
    remove: list[str] = []   # out of the library (scans keep theirs)


class PickRequest(BaseModel):
    kind: str            # "folder" | "file"
    title: str = ""
    initial: str = ""


class LoadRequest(BaseModel):
    scans_dir: str
    log_path: str = ""   # optional
    out_dir: str = ""    # default: write into the original scans
    tz: str = ""         # fallback zone; default: the machine's
    roll: str = ""       # default: name stored in the export, else the scans folder's
    camera: str = ""
    make: str = ""
    film: str = ""
    iso: Optional[int] = None


class RenameRequest(BaseModel):
    scans_dir: str
    name: str = ""
    reverse: bool = False
    rename_folder: bool = True


def build_session(req: LoadRequest, default_tz: str) -> Session:
    """Validate a LoadRequest and turn it into a Session. Raises ValueError with a user-facing message."""
    scans_dir = Path(req.scans_dir).expanduser()
    log_path = Path(req.log_path).expanduser() if req.log_path.strip() else None
    if not scans_dir.is_dir():
        raise ValueError(f"scans folder not found: {scans_dir}")
    if log_path and not log_path.is_file():
        raise ValueError(f"log file not found: {log_path}")
    tz_name = req.tz.strip() or default_tz
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone {tz_name!r}; use an IANA name like America/New_York")
    try:
        log = read_log(log_path) if log_path else None
    except LogFormatError as e:
        raise ValueError(str(e))
    scans = list_scans(scans_dir)
    if not scans:
        raise ValueError(f"no JPEGs in {scans_dir}")
    out_dir = Path(req.out_dir).expanduser() if req.out_dir.strip() else scans_dir
    in_place = out_dir.resolve() == scans_dir.resolve()
    ctx = TagContext(tz=tz, roll=req.roll.strip() or (log.name if log else scans_dir.name),
                     camera=req.camera.strip(), make=req.make.strip(), film=req.film.strip(), iso=req.iso,
                     keywords=roll_keywords(req.make, req.camera, req.film))
    entries = dict(enumerate(log.entries)) if log else {}
    return Session(scans_dir=scans_dir, log_path=log_path, out_dir=out_dir, in_place=in_place,
                   scans=scans, entries=entries, originals=dict(entries), ctx=ctx, tz_name=tz_name)


def entry_from_fields(f: CardFields, ctx: TagContext, current: Optional[LogEntry] = None) -> LogEntry:
    """Turn editor values into a LogEntry. Raises ValueError with a user-facing message."""
    tz = f.tz.strip()
    try:
        zone = ZoneInfo(tz) if tz else ctx.tz
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone {tz!r}; use an IANA name like America/New_York")
    if (f.lat is None) != (f.lon is None):
        raise ValueError("give both latitude and longitude, or neither")
    if f.lat is not None and not (-90 <= f.lat <= 90 and -180 <= f.lon <= 180):
        raise ValueError("latitude must be within ±90 and longitude within ±180")
    iso = ""
    if f.local.strip():
        try:
            local = datetime.fromisoformat(f.local.strip())
        except ValueError:
            raise ValueError(f"bad time {f.local!r}")
        utc = local.replace(tzinfo=zone).astimezone(timezone.utc)
        # The editor works in whole seconds; keep the stored stamp when it's the same second.
        if current and current.utc and current.utc.replace(microsecond=0) == utc:
            iso = current.iso
        else:
            iso = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return LogEntry(frame=f.frame, iso=iso, tz=tz, lat=f.lat, lon=f.lon, acc=f.acc, alt=f.alt,
                    lens=f.lens.strip(), aperture=f.aperture.strip(), shutter=f.shutter.strip(),
                    notes=f.notes.strip())


def card_view(e: LogEntry, ctx: TagContext) -> dict:
    """An entry as the UI shows it: its fields plus the zone it'll be written in and its local time there."""
    zone, source = resolve_zone(e, ctx)
    card = {**e.to_dict(), "zone": zone.key, "zone_source": source, "local": "", "local_iso": "", "offset": ""}
    if e.utc is not None:
        local = e.utc.astimezone(zone)
        off = local.utcoffset()
        secs = int(off.total_seconds()) if off else 0
        card.update(local=local.strftime("%b %d, %H:%M:%S"), local_iso=local.strftime("%Y-%m-%dT%H:%M:%S"),
                    offset=f"{'+' if secs >= 0 else '-'}{abs(secs) // 3600:02d}:{(abs(secs) % 3600) // 60:02d}")
    return card


def gallery_for(state: AppState, det):
    """The gallery a detection belongs to: pets and people are fingerprinted by different models."""
    return state.pet_gallery if det.kind == "pet" else state.gallery


def learns(state: AppState, det, name: str) -> bool:
    """Whether naming this detection should teach its gallery. A face box given a pet's name (the face model
    fired on the animal) or a pet box given a person's name would teach the wrong model, so the region is
    written but nothing is learned."""
    gallery = gallery_for(state, det)
    if gallery is None:
        return False
    pets = {n.lower() for n in state.face_settings.get("pets", [])}
    if state.pet_gallery is not None:
        pets |= {n.lower() for n, _ in state.pet_gallery.people()}
    if det.kind == "pet":
        return name.lower() in pets or name.lower() not in {n.lower() for n, _ in state.gallery.people()}
    return name.lower() not in pets


def find_in_scan(state: AppState, path):
    """People and pets in one scan, left to right, and a note if the pets couldn't be looked for.

    A face box that lands on a pet's head is the face model misfiring on the animal (it does, now and then),
    so the pet wins. If the pet model fails (its first download needs the internet), the faces still come back.
    """
    size, dets = state.engine.detect(path)
    note = ""
    if state.pet_engine is not None:
        try:
            _, pets = state.pet_engine.detect(path)
        except Exception as e:  # noqa: BLE001
            return size, dets, f"pets skipped: {e}"
        dets = [d for d in dets if not any(regions_iou(d.box, p.box) >= 0.3 for p in pets)] + pets
        dets.sort(key=lambda d: d.box.x)
    return size, dets, note


def faces_payload(state: AppState) -> dict:
    """What the UI needs to offer face tagging: whether it's available, and the names to complete."""
    if state.engine is None or state.gallery is None:
        return {"available": False, "reason": state.faces_note, "names": [], "people": 0, "count": 0}
    people = state.gallery.people()
    animals = state.pet_gallery.people() if state.pet_gallery is not None else []
    return {"available": True, "reason": "", "names": [n for n, _ in people] + [n for n, _ in animals if n not in dict(people)],
            "people": len(people), "pets_available": state.pet_engine is not None, "pet_names": [n for n, _ in animals],
            "pet_count": len(state.pet_gallery) if state.pet_gallery is not None else 0,
            "count": len(state.gallery), "accept": faces_mod.ACCEPT, "suggest": faces_mod.SUGGEST,
            "min_examples": faces_mod.MIN_EXAMPLES, "gallery_file": str(state.gallery.path),
            "library": state.face_settings["library"], "pets": state.face_settings["pets"],
            "synced": state.face_settings["synced"],
            "sync": ({**state.sync.status, "running": state.sync.running} if state.sync else None)}


def session_payload(state: AppState) -> dict:
    s = state.session
    base = {
        "loaded": s is not None,
        "exiftool": state.exiftool,
        "default_tz": state.default_tz,
        "remembered": state.remembered,
        "keywords": state.library.to_list(),
        "keywords_file": str(state.library.path),
        "faces": faces_payload(state),
    }
    if s is None:
        return base
    entries = []
    for i, e in sorted(s.entries.items(), key=lambda kv: (kv[1].frame, kv[0])):
        status = s.status(i)
        entries.append({"id": i, **card_view(e, s.ctx), "status": status, "added": s.added.get(i, ""),
                        "orig": card_view(s.originals[i], s.ctx) if status == "edited" else None})
    return {
        **base,
        "roll": s.ctx.roll,
        "make": s.ctx.make,
        "camera": s.ctx.camera,
        "film": s.ctx.film,
        "iso": s.ctx.iso,
        "roll_keywords": s.ctx.keywords,
        "tz": s.tz_name,
        "scans_dir": str(s.scans_dir),
        "log_path": str(s.log_path) if s.log_path else "",
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
        # no-cache: revalidate on every load, so a pulled update shows without a hard refresh
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

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
        state.remembered = req.model_dump(include=set(config.FIELDS))
        config.save(state.remembered)
        # keywords already in the scans (an earlier Write, another app) join the library
        found = [kw for sc in state.session.scans for kw in sc.keywords]
        if found:
            state.library.add(found, touch=False)
            state.library.save()
        return session_payload(state)

    @app.post("/api/keywords")
    def keywords(req: KeywordsRequest):
        state.library.add(req.add)
        state.library.remove(req.remove)
        state.library.save()
        return {"keywords": state.library.to_list()}

    def rename_plan(req: RenameRequest):
        try:
            return plan_rename(req.scans_dir, req.name, req.reverse, req.rename_folder)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/rename/preview")
    def rename_preview(req: RenameRequest):
        return rename_plan(req).to_dict()

    @app.post("/api/rename/detect")
    def rename_detect(req: RenameRequest):
        """The name an already-renamed folder carries, so the UI can offer it for correcting."""
        return {"name": detect_name(req.scans_dir)}

    @app.post("/api/rename/apply")
    def rename_apply(req: RenameRequest):
        """Rename the scans folder and its images. Only from the setup screen: a loaded roll holds their paths."""
        if state.session is not None:
            raise HTTPException(409, "unload the roll before renaming its scans")
        plan = rename_plan(req)
        try:
            new_dir = apply_rename(plan)
        except OSError as e:
            raise HTTPException(500, f"rename failed partway: {e}")
        if state.remembered.get("scans_dir") and Path(state.remembered["scans_dir"]).expanduser() == plan.src_dir:
            state.remembered["scans_dir"] = str(new_dir)
            config.save(state.remembered)
        return {**plan.to_dict(), "renamed": len(plan.items)}

    @app.post("/api/unload")
    def unload():
        state.session = None
        return session_payload(state)

    def scaled(name: str, cache: ThumbnailCache) -> Response:
        s = need_session()
        scan = s.scan_by_name(name)
        if scan is None:
            raise HTTPException(404, "no such scan")
        try:
            data = cache.get(scan.path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"thumbnail failed: {e}")
        return Response(data, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/thumb/{name}")
    def thumb(name: str):
        return scaled(name, need_session().thumbs)

    @app.get("/api/view/{name}")
    def view(name: str):
        return scaled(name, need_session().views)

    @app.get("/api/full/{name}")
    def full(name: str):   # the scan itself, for the 100% view
        scan = need_session().scan_by_name(name)
        if scan is None:
            raise HTTPException(404, "no such scan")
        return FileResponse(scan.path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    @app.get("/api/faces/sync")
    def faces_sync():
        """Progress of learning from the library folder, for the UI to poll while it runs."""
        return faces_payload(state)

    @app.post("/api/faces/library")
    def faces_library(req: LibraryRequest):
        """Set (once; it's remembered) where your Lightroom photos are and which names aren't people, then learn from them."""
        if state.sync is None or state.gallery is None:
            raise HTTPException(409, state.faces_note or "the face tools aren't available")
        if state.sync.running:
            raise HTTPException(409, "still learning from the library; wait for it to finish")
        folders = [str(Path(f.strip()).expanduser()) for f in req.folders if f.strip()]
        for f in folders:
            if not Path(f).is_dir():
                raise HTTPException(400, f"folder not found: {f}")
        faces_mod.update_settings(state.gallery, state.face_settings, folders, req.pets, state.pet_gallery)
        state.sync.start()
        return faces_payload(state)

    @app.post("/api/faces/detect/{name}")
    def detect_faces(name: str):
        """Find the faces in one scan and suggest who they are. Detections are kept for Write to learn from."""
        s = need_session()
        if state.engine is None or state.gallery is None:
            raise HTTPException(409, state.faces_note or "the face tools aren't installed")
        scan = s.scan_by_name(name)
        if scan is None:
            raise HTTPException(404, "no such scan")
        note = ""
        if name not in s.detections:
            try:
                size, dets, note = find_in_scan(state, scan.path)
            except Exception as e:  # noqa: BLE001  (a bad file or a model that won't load)
                raise HTTPException(500, f"face detection failed: {e}")
            s.detections[name] = (size, dets)
        (w, h), dets = s.detections[name]
        out = []
        for i, d in enumerate(dets):
            gallery, t = (state.pet_gallery, pets_mod) if d.kind == "pet" else (state.gallery, faces_mod)
            sug = [{"name": n, "score": round(sc, 3)} for n, sc in gallery.suggest(d.emb) if sc >= t.SUGGEST]
            auto = bool(sug) and sug[0]["score"] >= t.ACCEPT and d.score >= t.AUTO_DET
            out.append({"id": i, "kind": d.kind, "box": [d.box.x, d.box.y, d.box.w, d.box.h], "score": round(d.score, 3),
                        "low": d.score < t.AUTO_DET, "suggestions": sug, "name": sug[0]["name"] if auto else ""})
        return {"scan": name, "width": w, "height": h, "faces": out, "note": note}

    def scan_args(s: Session, scan: Optional[ScanInfo], entry: Optional[LogEntry], kws: list[str],
                  face_args: list[str] = ()) -> list[str]:
        # A scan that has keywords and is given none (and no roll ones) gets them cleared, as you'd expect.
        return build_args(entry, s.ctx, kws, clear_keywords=bool(scan and scan.keywords), face_args=face_args)

    @app.get("/api/preview/{entry}")
    def preview(entry: int, scan: str = "", kw: list[str] = Query(default=[])):
        """The ExifTool assignments that Write would apply for one entry, paired with `scan` carrying keywords `kw`."""
        s = need_session()
        return {"args": scan_args(s, s.scan_by_name(scan), s.entry(entry), kw)}

    @app.post("/api/entries")
    def create_entry(req: NewCard):
        """A new card, pasted from another (copy_of) or from scratch. Answers the session plus the new id."""
        s = need_session()
        if req.copy_of is not None:
            src = s.entry(req.copy_of)
            new = s.add(replace(src), f"copy of #{src.frame}")
        else:
            if req.fields is None:
                raise HTTPException(400, "give copy_of or fields")
            try:
                new = s.add(entry_from_fields(req.fields, s.ctx), "new")
            except ValueError as e:
                raise HTTPException(400, str(e))
        return {**session_payload(state), "created": new}

    @app.put("/api/entries/{entry_id}")
    def update_entry(entry_id: int, f: CardFields):
        s = need_session()
        try:
            s.entries[entry_id] = entry_from_fields(f, s.ctx, s.entry(entry_id))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return session_payload(state)

    @app.post("/api/entries/{entry_id}/revert")
    def revert_entry(entry_id: int):
        s = need_session()
        s.entry(entry_id)
        if entry_id not in s.originals:
            raise HTTPException(400, "this card was made here; there is no original to revert to")
        s.entries[entry_id] = s.originals[entry_id]
        return session_payload(state)

    @app.delete("/api/entries/{entry_id}")
    def delete_entry(entry_id: int):
        """Only cards made here: a card from the export can be edited or left unpaired, not removed."""
        s = need_session()
        s.entry(entry_id)
        if entry_id not in s.added:
            raise HTTPException(400, "cards from the log export can't be deleted; leave it unpaired")
        del s.entries[entry_id], s.added[entry_id]
        return session_payload(state)

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
            if p.entry is not None and p.entry not in s.entries:
                raise HTTPException(400, f"unknown entry {p.entry}")
            if p.faces is not None and any(len(f.box) != 4 for f in p.faces):
                raise HTTPException(400, f"a face box needs x, y, w, h ({p.scan})")
            jobs.append((scan, p.entry, None if p.entry is None else s.entries[p.entry], p.keywords, p.faces))

        def run(job):
            scan, idx, entry, kws, faces = job
            dst = scan.path if s.in_place else s.out_dir / scan.name
            named, learn, note = [], [], ""
            for f in faces or []:   # named faces only; a name spelled like a keyword already in use takes its spelling
                name = state.library.canonical(clean(f.name)) if clean(f.name) else ""
                if name:
                    named.append(FaceBox(*f.box, name=name))
                    det = s.detections.get(scan.name)
                    if det and f.id is not None and 0 <= f.id < len(det[1]) and learns(state, det[1][f.id], name):
                        learn.append((name, det[1][f.id]))
            face_args = []
            if named:
                if scan.orientation != 1 or not (scan.width and scan.height):
                    # regions would have to be mapped back into the file's stored frame; not done yet
                    note = f"face regions not written: this scan is rotated (EXIF orientation {scan.orientation})"
                else:
                    # Write replaces the file's whole region list, so carry over the boxes already there
                    # (Lightroom's, or an earlier Write's) for faces that weren't just named.
                    try:
                        existing = [f for img in read_regions(state.exiftool, [scan.path]) for f in img.faces]
                    except (OSError, ValueError, RuntimeError) as e:
                        existing = None
                        note = f"face regions not written: couldn't read the ones already in the file ({e})"
                    if existing is not None:
                        face_args = region_args(merge_regions(existing, named), scan.width, scan.height, keep_unnamed=True)
            kws = merge(kws, [f.name for f in named])   # a named face is also a keyword, which is what Lightroom lists
            try:
                write_tags(state.exiftool, scan.path, dst, scan_args(s, scan, entry, kws, face_args))
                scan.tagged = True
                written = merge(s.ctx.keywords, kws)
                if s.in_place:   # copies start from the original each time, so only then has the file changed
                    scan.keywords = written
                return ({"scan": scan.name, "entry": idx, "ok": True, "path": str(dst), "keywords": written,
                         "faces": len(named) if face_args else 0, "note": note},
                        [(n, d, str(scan.path)) for n, d in learn])
            except (ExifToolError, OSError) as e:
                return {"scan": scan.name, "entry": idx, "ok": False, "error": str(e)}, []

        with ThreadPoolExecutor(max_workers=4) as pool:
            done = list(pool.map(run, jobs))
        results = [r for r, _ in done]
        for r in results:
            if r["ok"]:
                state.library.add(r.pop("keywords"), written=1)
        state.library.save()
        learned = 0
        for _, learn in done:   # every confirmed face or pet teaches its gallery; naming one again corrects it
            for name, det, source in learn:
                gallery_for(state, det).add(name, det.emb, source, det.box)
                learned += 1
        if learned:
            for g in (state.gallery, state.pet_gallery):
                if g is not None:
                    g.save()
        return {"results": results, "written": sum(r["ok"] for r in results), "keywords": state.library.to_list(),
                "learned": learned, "faces": faces_payload(state)}

    return app
