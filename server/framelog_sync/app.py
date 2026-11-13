"""Web server: the PWA's static files at /, and the authenticated /api/sync and /api/status."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .config import Settings
from .s3 import S3Backup
from .store import Store, ValidationError, validate_state

log = logging.getLogger("framelog_sync")

# The only files served, path -> (file in the repo root, media type, Cache-Control).
# index.html and sw.js must never be cached by the browser's HTTP cache, or app updates stall;
# the service worker keeps its own offline copy of everything here.
NO_CACHE = "no-cache"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8", NO_CACHE),
    "/index.html": ("index.html", "text/html; charset=utf-8", NO_CACHE),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8", NO_CACHE),
    "/manifest.json": ("manifest.json", "application/manifest+json", NO_CACHE),
    "/icon-192.png": ("icon-192.png", "image/png", "public, max-age=86400"),
    "/icon-512.png": ("icon-512.png", "image/png", "public, max-age=86400"),
}


class LoginThrottle:
    """Counts failed passwords in a sliding window, per client address and across all of them.

    Once an address has `per_ip` failures in the window it can't try again until the oldest one
    ages out; `overall` does the same for everyone at once, against guessing spread over many
    addresses. (The same scheme as the kalshi-pnl dashboard's login.)
    """

    def __init__(self, per_ip: int = 5, overall: int = 100, window: float = 15 * 60, clock=time.monotonic):
        self.per_ip, self.overall, self.window, self.clock = per_ip, overall, window, clock
        self.by_ip: dict[str, deque] = {}
        self.all: deque = deque()

    def _prune(self, q: deque, now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def retry_after(self, ip: str) -> float:
        """Seconds until this address may try again; 0 if it may now."""
        now = self.clock()
        self._prune(self.all, now)
        waits = [0.0]
        if len(self.all) >= self.overall:
            waits.append(self.all[0] + self.window - now)
        q = self.by_ip.get(ip)
        if q is not None:
            self._prune(q, now)
            if not q:
                del self.by_ip[ip]
            elif len(q) >= self.per_ip:
                waits.append(q[0] + self.window - now)
        return max(waits)

    def record_failure(self, ip: str) -> None:
        now = self.clock()
        self.all.append(now)
        self.by_ip.setdefault(ip, deque()).append(now)
        if len(self.by_ip) > 10_000:  # keep memory bounded under a flood of distinct addresses
            for key in [k for k, q in self.by_ip.items() if not q or q[-1] <= now - self.window]:
                del self.by_ip[key]

    def record_success(self, ip: str) -> None:
        self.by_ip.pop(ip, None)


def password_matches(given: str, expected: str) -> bool:
    # Compare fixed-length digests, so neither the content nor the length leaks through timing.
    a = hashlib.sha256(given.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def client_ip(request: Request) -> str:
    # Behind the tunnel this is the visitor's address from X-Forwarded-For. ProxyHeadersMiddleware
    # only accepts that header from FORWARDED_ALLOW_IPS (by default this computer, where cloudflared
    # runs), so a visitor can't pick their own address to dodge the failed-password limit.
    return request.client.host if request.client else "unknown"


def _error(status: int, message: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status, headers=headers)


def create_app(settings: Settings, store: Optional[Store] = None, backup: Optional[S3Backup] = None,
               throttle: Optional[LoginThrottle] = None, fail_delay: float = 1.0) -> FastAPI:
    store = store or Store(settings.data_dir)
    backup = backup or S3Backup(settings, settings.data_dir)
    throttle = throttle or LoginThrottle()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Data folder: %s", settings.data_dir)
        log.info(backup.describe())
        backup.submit([], store.backup_files)  # startup catch-up pass (no-op when disabled)
        yield
        backup.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.backup, app.state.throttle = store, backup, throttle

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Outermost, so the address is rewritten before anything reads it.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.forwarded_allow_ips)

    async def require_password(request: Request) -> None:
        ip = client_ip(request)
        # Checked and recorded with no await in between, so parallel guesses can't slip past the limit.
        wait = throttle.retry_after(ip)
        if wait > 0:
            log.warning("API call from %s refused: too many wrong passwords", ip)
            raise HTTPException(429, "too many wrong passwords; try again later",
                                headers={"Retry-After": str(math.ceil(wait))})
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            # No password at all isn't a guess, so it doesn't count towards the limit.
            raise HTTPException(401, "password required", headers={"WWW-Authenticate": "Bearer"})
        if not password_matches(token.strip(), settings.password):
            throttle.record_failure(ip)
            log.warning("Wrong sync password from %s", ip)
            await asyncio.sleep(fail_delay)
            raise HTTPException(401, "wrong password", headers={"WWW-Authenticate": "Bearer"})
        throttle.record_success(ip)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return _error(exc.status_code, str(exc.detail), getattr(exc, "headers", None))

    async def read_body(request: Request) -> bytes:
        limit = settings.max_body_bytes
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"body larger than {limit} bytes")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"body larger than {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.post("/api/sync", dependencies=[Depends(require_password)])
    async def sync(request: Request):
        raw = await read_body(request)
        try:
            state = validate_state(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error(400, "body is not valid JSON")
        except ValidationError as exc:
            return _error(422, str(exc))
        result = await asyncio.to_thread(store.save, state)
        if result.stored:
            log.info("Sync from %s: new snapshot %s (%d file(s))", client_ip(request),
                     result.snapshot.name, len(result.written))
            backup.submit(result.written, store.backup_files)  # background; never awaited
        return {"ok": True, "stored": result.stored, "hash": result.hash, **store.status(),
                "s3": backup.status()}

    @app.get("/api/status", dependencies=[Depends(require_password)])
    async def status():
        return {"ok": True, **store.status(), "s3": backup.status()}

    def static_route(path: str, filename: str, media_type: str, cache: str):
        async def serve():
            return FileResponse(settings.static_dir / filename, media_type=media_type,
                                headers={"Cache-Control": cache})
        app.add_api_route(path, serve, methods=["GET", "HEAD"], include_in_schema=False)

    for path, (filename, media_type, cache) in STATIC_FILES.items():
        static_route(path, filename, media_type, cache)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    return app
