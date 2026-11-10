"""HTTP server: phone pages, facilitator page, and their JSON APIs.

Phones poll `/api/g/{code}/state` every couple of seconds; polling survives
flaky event wifi and phone sleep better than a socket. The coach runs on a
background thread that checks each group every few seconds.
"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from importlib import resources

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .asr import ASR, suffix_for
from .engine import Session
from .health import Health
from .llm import TRANSIENT_ERRORS

MAX_AUDIO_BYTES = 8 * 1024 * 1024


def _page(name: str) -> str:
    return resources.files("knowhow").joinpath("static", name).read_text()


class App:
    def __init__(self, session: Session, asr: ASR, admin_key: str | None = None, coach_poll_s: float = 5.0):
        self.session = session
        self.asr = asr
        self.admin_key = admin_key or secrets.token_urlsafe(12)
        if not session.state.started_at:
            session.state.started_at = time.time()
            session.save()
        self._stop = threading.Event()
        self.coach_poll_s = coach_poll_s
        self.health = Health(asr.name, getattr(session.coach_llm, "model", None), session.cfg.coach_interval_s)
        self.starlette = Starlette(
            routes=[
                Route("/", self.index),
                Route("/g/{code}", self.phone_page),
                Route("/api/g/{code}/state", self.state),
                Route("/api/g/{code}/listen", self.listen, methods=["POST"]),
                Route("/api/g/{code}/audio", self.audio, methods=["POST"]),
                Route("/api/g/{code}/text", self.text, methods=["POST"]),
                Route("/api/g/{code}/pause", self.pause, methods=["POST"]),
                Route("/api/g/{code}/pick", self.pick, methods=["POST"]),
                Route("/facilitator", self.facilitator_page),
                Route("/api/admin/overview", self.overview),
                Route("/api/admin/message", self.message, methods=["POST"]),
                Route("/api/admin/close", self.close, methods=["POST"]),
                Route("/api/admin/strike", self.strike, methods=["POST"]),
            ],
            lifespan=self._lifespan,
        )

    # ------------------------------------------------------------ helpers

    def now(self) -> float:
        return time.time() - self.session.state.started_at

    def _code(self, request: Request) -> str:
        code = request.path_params["code"].upper()
        if code not in self.session.state.groups:
            raise KeyError(code)
        return code

    def _admin(self, request: Request) -> None:
        if not secrets.compare_digest(request.query_params.get("key", ""), self.admin_key):
            raise PermissionError

    @contextlib.asynccontextmanager
    async def _lifespan(self, app):
        threading.Thread(target=self._coach_loop, daemon=True, name="coach-loop").start()
        threading.Thread(target=self._warm_asr, daemon=True, name="asr-warm").start()
        yield
        self._stop.set()

    def _coach_loop(self) -> None:
        while not self._stop.wait(self.coach_poll_s):
            self.session.flush()
            now = self.now()
            for code in list(self.session.state.groups):
                if self.session.coach_due(code, now):
                    threading.Thread(target=self._coach, args=(code, now), daemon=True).start()

    def _coach(self, code: str, now: float) -> None:
        """One coach pass, reported to the health panel. A failed pass leaves
        the group's lines pending, so the next pass retries them."""
        before = self.session.state.groups[code].questions
        self.health.coach_started(code)
        started = self.now()
        t0 = time.monotonic()
        error = None
        info = None
        ok = False
        try:
            info = self.session.coach(code, now)
            ok = True
        except TRANSIENT_ERRORS as e:
            error = f"{type(e).__name__}: {e}"
        finally:
            if not ok and error is None:
                error = "unexpected error; see the server log"
            changed = self.session.state.groups[code].questions.current.text != before.current.text
            self.health.coach_finished(code, started, self.now(), error, changed)
            if info is not None or error is not None:
                self.session.log_pass(code, now, t0, info, error)

    def _warm_asr(self) -> None:
        ok = False
        try:
            self.asr.warm()
            ok = True
        finally:
            with self.health.lock:
                if ok:
                    self.health.asr_ready = True
                else:
                    self.health.asr_error = "the speech model did not load; see the server log"

    # ------------------------------------------------------------ pages

    async def index(self, request: Request) -> Response:
        return HTMLResponse(_page("index.html"))

    async def phone_page(self, request: Request) -> Response:
        try:
            self._code(request)
        except KeyError:
            return HTMLResponse("Unknown group code. Check your handout.", status_code=404)
        return HTMLResponse(_page("phone.html"))

    async def facilitator_page(self, request: Request) -> Response:
        try:
            self._admin(request)
        except PermissionError:
            return HTMLResponse("Forbidden", status_code=403)
        return HTMLResponse(_page("facilitator.html"))

    # ------------------------------------------------------------ phone API

    async def state(self, request: Request) -> Response:
        try:
            code = self._code(request)
        except KeyError:
            return JSONResponse({"error": "unknown group"}, status_code=404)
        device = request.query_params.get("device")
        self.health.viewer(code, device, self.now())
        return JSONResponse(self.session.phone_state(code, device, self.now()))

    async def listen(self, request: Request) -> Response:
        code = self._code(request)
        body = await request.json()
        self.session.claim_listener(code, body["device"], self.now())
        return JSONResponse({"ok": True})

    async def audio(self, request: Request) -> Response:
        code = self._code(request)
        device = request.query_params.get("device", "")
        data = await request.body()
        if len(data) > MAX_AUDIO_BYTES:
            return JSONResponse({"error": "segment too large"}, status_code=413)
        reason = self.session.refusal(code)
        if reason:
            self.session.note_dropped(code, reason)
            return JSONResponse({"ok": False, "dropped": reason}, status_code=409)
        received = self.now()
        self.session.note_audio(code, device, received)
        self.health.segment_started(code, received)
        text = None
        try:
            text = await run_in_threadpool(self.asr.transcribe, data, suffix_for(request.headers.get("content-type", "")))
        finally:
            self.health.segment_finished(code, received, self.now(), text)
        # The group may have paused, or the session closed, while this segment was transcribed.
        reason = self.session.refusal(code)
        self.session.ingest(code, text, t=received, source="asr")
        if reason:
            return JSONResponse({"ok": False, "dropped": reason}, status_code=409)
        return JSONResponse({"ok": True, "chars": len(text)})

    async def text(self, request: Request) -> Response:
        code = self._code(request)
        body = await request.json()
        reason = self.session.refusal(code)
        u = self.session.ingest(code, body["text"], t=self.now(), speaker=body.get("speaker"), source=body.get("source", "typed"))
        if reason:
            return JSONResponse({"ok": False, "dropped": reason}, status_code=409)
        if u is not None:
            self.health.typed(code, body["text"], self.now())
        return JSONResponse({"ok": True, "stored": u is not None})

    async def pause(self, request: Request) -> Response:
        code = self._code(request)
        body = await request.json()
        self.session.set_paused(code, bool(body["paused"]))
        return JSONResponse({"ok": True})

    async def pick(self, request: Request) -> Response:
        code = self._code(request)
        body = await request.json()
        self.session.pick(code, body["slot"], self.now())
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ admin API

    async def overview(self, request: Request) -> Response:
        try:
            self._admin(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        now = self.now()
        out = self.session.overview(now)
        out["health"] = self.health.report(self.session, now)
        return JSONResponse(out)

    async def message(self, request: Request) -> Response:
        try:
            self._admin(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        body = await request.json()
        self.session.set_message(body.get("markdown") or None, self.now())
        return JSONResponse({"ok": True})

    async def close(self, request: Request) -> Response:
        """Stop listening everywhere and start synthesis in the background."""
        try:
            self._admin(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        self.session.close()  # phones stop being heard at once

        def finish() -> None:
            self.session.final_pass(self.now())  # the last lines of each group reach the map
            self.session.synthesize()

        threading.Thread(target=finish, daemon=True, name="final-pass-and-synthesis").start()
        return JSONResponse({"ok": True})

    async def strike(self, request: Request) -> Response:
        try:
            self._admin(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        body = await request.json()
        self.session.strike(body["item_id"])
        return JSONResponse({"ok": True})
