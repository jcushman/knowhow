"""Capture server: serves the recording page and writes uploaded clips to
the audio spool. It does nothing else, so it has little reason to fail."""

from __future__ import annotations

import time
from importlib import resources

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ..asr import suffix_for
from ..config import EventConfig
from .spool import Spool, atomic_write, clip_name, write_json
from .status import spool_status

MAX_CLIP_BYTES = 16 * 1024 * 1024


def _page(name: str) -> str:
    return resources.files("knowhow").joinpath("static", name).read_text()


class CaptureApp:
    def __init__(self, cfg: EventConfig, spool: Spool):
        self.cfg = cfg
        self.spool = spool
        self.groups = {g.code: g for g in cfg.groups}
        self.starlette = Starlette(
            routes=[
                Route("/", self.index),
                Route("/r/{code}", self.record_page),
                Route("/api/r/{code}/info", self.info),
                Route("/api/r/{code}/clip", self.clip, methods=["POST"]),
                Route("/status", self.status_page),
                Route("/api/status", self.status),
            ]
        )

    def _group(self, request: Request) -> str | None:
        code = request.path_params["code"].upper()
        return code if code in self.groups else None

    async def index(self, request: Request) -> Response:
        links = "".join(
            f'<li><a href="/r/{g.code}">{g.label}</a> <span>{g.code}</span></li>' for g in self.cfg.groups
        )
        return HTMLResponse(
            f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{self.cfg.title}</title><body style='font:18px system-ui;padding:24px'>"
            f"<h1 style='font-size:22px'>{self.cfg.title}</h1><ul style='line-height:2'>{links}</ul>"
            f"<p><a href='/status'>Status</a></p></body>"
        )

    async def record_page(self, request: Request) -> Response:
        if self._group(request) is None:
            return HTMLResponse("Unknown group code.", status_code=404)
        return HTMLResponse(_page("record.html"))

    async def info(self, request: Request) -> Response:
        code = self._group(request)
        if code is None:
            return JSONResponse({"error": "unknown group"}, status_code=404)
        return JSONResponse({"code": code, "label": self.groups[code].label, "title": self.cfg.title})

    async def clip(self, request: Request) -> Response:
        """Store one clip. Safe to repeat: a clip already stored is
        acknowledged again, so the browser can retry freely."""
        code = self._group(request)
        if code is None:
            return JSONResponse({"error": "unknown group"}, status_code=404)
        q = request.query_params
        try:
            start_ms, seq = int(q["start"]), int(q["seq"])
        except (KeyError, ValueError):
            return JSONResponse({"error": "start and seq are required integers"}, status_code=400)
        name = clip_name(start_ms, q.get("device", ""), seq)
        ext = suffix_for(request.headers.get("content-type", ""))
        path = self.spool.audio_dir(code) / f"{name}{ext}"
        if path.exists():
            return JSONResponse({"ok": True, "stored": False, "clip": name})
        data = await request.body()
        if len(data) > MAX_CLIP_BYTES:
            return JSONResponse({"error": "clip too large"}, status_code=413)
        if not data:
            return JSONResponse({"error": "empty clip"}, status_code=400)
        # Metadata first: a clip file never appears without it.
        write_json(
            path.with_suffix(".meta.json"),
            {
                "clip": name,
                "group": code,
                "start_ms": start_ms,
                "seq": seq,
                "device": q.get("device", ""),
                "duration_ms": int(q.get("duration", 0) or 0),
                "content_type": request.headers.get("content-type", ""),
                "bytes": len(data),
                "received": time.time(),
            },
        )
        atomic_write(path, data)
        return JSONResponse({"ok": True, "stored": True, "clip": name})

    async def status_page(self, request: Request) -> Response:
        return HTMLResponse(_page("record-status.html"))

    async def status(self, request: Request) -> Response:
        return JSONResponse(spool_status(self.cfg, self.spool))
