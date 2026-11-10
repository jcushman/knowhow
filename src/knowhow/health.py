"""Live pipeline health for the facilitator page.

Each group's pipeline runs phone → audio upload → speech recognition → coach
→ questions back on the phones. This module counts what passes through each
stage and turns the counts into one status line per stage, so a facilitator
can see that a group is working without reading its transcript. Nothing
here is persisted; it describes the running server only.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field

AUDIO_STALE_S = 40.0  # phones send a segment every ~15 s
VIEWER_STALE_S = 10.0  # phones poll every 2.5 s
WORDS_WINDOW_S = 120.0
SLOW_ASR_S = 20.0
SPARK_MINUTES = 10

OK, WARN, BAD, IDLE = "ok", "warn", "bad", "idle"
RANK = {BAD: 3, WARN: 2, OK: 1, IDLE: 0}


@dataclass
class GroupHealth:
    viewers: dict[str, float] = field(default_factory=dict)  # device -> last poll
    segments: int = 0
    transcribed: int = 0
    failed: int = 0
    empty: int = 0
    last_segment_t: float | None = None
    last_text_t: float | None = None
    asr_delays: deque = field(default_factory=lambda: deque(maxlen=8))
    words: deque = field(default_factory=deque)  # (t, n) per transcribed segment or typed line
    segment_times: deque = field(default_factory=deque)
    coach_running: bool = False
    coach_passes: int = 0
    coach_last_ok_t: float | None = None
    coach_last_s: float | None = None
    coach_failures: int = 0
    coach_last_error: str | None = None  # cleared by the next good pass
    question_changed_t: float | None = None


class Health:
    def __init__(self, asr_kind: str, coach_model: str | None, coach_interval_s: float):
        self.asr_kind = asr_kind
        self.coach_model = coach_model
        self.coach_interval_s = coach_interval_s
        self.asr_ready = asr_kind == "none"
        self.asr_error: str | None = None
        self.asr_inflight = 0
        self.groups: dict[str, GroupHealth] = {}
        self.lock = threading.Lock()

    def g(self, code: str) -> GroupHealth:
        return self.groups.setdefault(code, GroupHealth())

    # ------------------------------------------------------------ events

    def viewer(self, code: str, device: str | None, t: float) -> None:
        with self.lock:
            self.g(code).viewers[device or "anonymous"] = t

    def segment_started(self, code: str, t: float) -> None:
        with self.lock:
            g = self.g(code)
            g.segments += 1
            g.last_segment_t = t
            g.segment_times.append(t)
            self.asr_inflight += 1

    def segment_finished(self, code: str, t_received: float, t_done: float, text: str | None) -> None:
        """text is None when transcription raised."""
        with self.lock:
            g = self.g(code)
            self.asr_inflight -= 1
            if text is None:
                g.failed += 1
                return
            self.asr_ready = True
            g.transcribed += 1
            g.asr_delays.append(t_done - t_received)
            n = len(text.split())
            if n:
                g.words.append((t_done, n))
                g.last_text_t = t_done
            else:
                g.empty += 1

    def typed(self, code: str, text: str, t: float) -> None:
        with self.lock:
            g = self.g(code)
            g.words.append((t, len(text.split())))
            g.last_text_t = t

    def coach_started(self, code: str) -> None:
        with self.lock:
            self.g(code).coach_running = True

    def coach_finished(self, code: str, started: float, t: float, error: str | None, question_changed: bool) -> None:
        with self.lock:
            g = self.g(code)
            g.coach_running = False
            g.coach_last_s = t - started
            if error:
                g.coach_failures += 1
                g.coach_last_error = error
                return
            g.coach_passes += 1
            g.coach_last_ok_t = t
            g.coach_last_error = None
            if question_changed:
                g.question_changed_t = t

    # ------------------------------------------------------------ report

    def report(self, session, now: float) -> dict:
        with session.lock, self.lock:
            groups = {}
            for code, gs in session.state.groups.items():
                h = self.g(code)
                pending_chars = sum(len(u.text) for u in session.pending(code))
                n_items = sum(1 for i in session.state.items.values() if i.group == code and not i.struck)
                dropped = sum(session.state.dropped.get(code, {}).values())
                groups[code] = self._group(h, gs, now, pending_chars, n_items, session.state.closed, dropped)
            delays = [d for h in self.groups.values() for d in h.asr_delays]
            return {
                "groups": groups,
                "asr": {
                    "kind": self.asr_kind,
                    "ready": self.asr_ready,
                    "error": self.asr_error,
                    "queue": self.asr_inflight,
                    "avg_delay": round(sum(delays) / len(delays), 1) if delays else None,
                },
                "coach_model": self.coach_model,
            }

    def _group(self, h: GroupHealth, gs, now: float, pending_chars: int, n_items: int, closed: bool, dropped: int = 0) -> dict:
        while h.words and now - h.words[0][0] > SPARK_MINUTES * 60:
            h.words.popleft()
        while h.segment_times and now - h.segment_times[0] > SPARK_MINUTES * 60:
            h.segment_times.popleft()
        viewers = sum(1 for t in h.viewers.values() if now - t <= VIEWER_STALE_S)
        recent_words = sum(n for t, n in h.words if now - t <= WORDS_WINDOW_S)
        recent_segments = sum(1 for t in h.segment_times if now - t <= WORDS_WINDOW_S)
        audio_age = None if h.last_segment_t is None else now - h.last_segment_t
        delay = h.asr_delays[-1] if h.asr_delays else None
        spark = [0] * SPARK_MINUTES
        for t, n in h.words:
            k = int((now - t) // 60)
            if 0 <= k < SPARK_MINUTES:
                spark[SPARK_MINUTES - 1 - k] += n

        def ago(t: float | None) -> str:
            if t is None:
                return "never"
            s = max(0, int(now - t))
            return f"{s} s ago" if s < 90 else f"{s // 60} min ago"

        phones = f"{viewers} phone{'s' if viewers != 1 else ''} open"
        if closed:
            phone = (IDLE, "Listening has ended")
        elif gs.paused:
            phone = (WARN, f"Paused by the group · {phones}")
        elif gs.listener_device is None:
            phone = (IDLE, f"No phone has tapped Start · {phones}")
        elif audio_age is not None and audio_age > AUDIO_STALE_S:
            phone = (BAD, f"Listening phone went quiet {int(audio_age)} s ago (locked screen, switched app or wifi) · {phones}")
        else:
            phone = (OK, f"One phone listening · {phones}")

        refused = f" · {dropped} refused while paused or closed" if dropped else ""
        if closed or gs.paused:
            audio = (IDLE, f"{h.segments} segments received{refused}")
        elif audio_age is None:
            audio = (IDLE, "No audio yet")
        elif audio_age > AUDIO_STALE_S:
            audio = (BAD, f"No audio for {int(audio_age)} s")
        else:
            audio = (OK, f"Last segment {ago(h.last_segment_t)} · {h.segments} received{refused}")

        if self.asr_kind == "none":
            asr = (IDLE, "Off (--asr none); only typed text arrives")
        elif self.asr_error:
            asr = (BAD, f"Speech recognition failed to start: {self.asr_error}")
        elif not self.asr_ready:
            asr = (WARN, "Speech model is loading; segments wait until it is ready")
        elif h.failed and h.failed >= h.transcribed:
            asr = (BAD, f"{h.failed} of {h.segments} segments failed to transcribe")
        elif recent_segments >= 3 and recent_words == 0:
            asr = (WARN, "Audio is arriving but no words in 2 min: muted mic, phone too far away, or a quiet group")
        elif delay is not None and delay > SLOW_ASR_S:
            asr = (WARN, f"Transcription is {int(delay)} s behind")
        elif h.transcribed == 0:
            asr = (IDLE, "Nothing transcribed yet")
        else:
            extra = f" · {h.failed} failed" if h.failed else ""
            asr = (OK, f"{recent_words} words in the last 2 min · {delay:.0f} s delay{extra}")

        interval = self.coach_interval_s
        # After a server restart, the saved session still knows when the coach last ran.
        last_ok = h.coach_last_ok_t if h.coach_last_ok_t is not None else (gs.last_coach_t or None)
        if h.coach_last_error:
            coach = (BAD, f"Last update failed; its lines wait for the next pass. {h.coach_last_error[:160]}")
        elif h.coach_running:
            coach = (OK, "Updating now")
        elif last_ok is None:
            coach = (IDLE, f"First update after about 200 characters of speech ({pending_chars} so far)")
        elif pending_chars >= 200 and now - last_ok > 3 * interval + 30:
            coach = (WARN, f"No update for {int(now - last_ok)} s with {pending_chars} characters waiting")
        else:
            took = f" ({h.coach_last_s:.0f} s)" if h.coach_last_ok_t is not None and h.coach_last_s is not None else ""
            changed = f" · question last changed {ago(h.question_changed_t)}" if h.question_changed_t else ""
            coach = (OK, f"Updated {ago(last_ok)}{took} · {n_items} map points{changed}")

        stages = [
            {"name": "Phone", "state": phone[0], "detail": phone[1]},
            {"name": "Audio", "state": audio[0], "detail": audio[1]},
            {"name": "Transcription", "state": asr[0], "detail": asr[1]},
            {"name": "Coach", "state": coach[0], "detail": coach[1]},
        ]
        worst = max(stages, key=lambda s: RANK[s["state"]])
        if closed:
            headline = (IDLE, "Listening ended")
        elif worst["state"] in (BAD, WARN):
            headline = (worst["state"], f"{worst['name']}: {worst['detail'].split(' · ')[0]}")
        elif all(s["state"] == IDLE for s in stages):
            headline = (IDLE, "Waiting to start")
        elif all(s["state"] == OK for s in stages):
            headline = (OK, "Working")
        elif phone[0] == OK:
            headline = (OK, "Listening")
        else:
            headline = (IDLE, "Waiting to start")
        return {"state": headline[0], "headline": headline[1], "stages": stages, "words_per_min": spark}
