"""Session engine: all state for one event, independent of transport.

The live server and the simulator both drive this object. Times are seconds
since session start; the server passes wall-clock offsets, the simulator
passes its own simulated clock.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .coach import run_coach
from .config import EventConfig
from .llm import LLM, TRANSIENT_ERRORS
from .models import (
    GroupState,
    Link,
    MapItem,
    PhoneMessage,
    Question,
    QuestionSet,
    Synthesis,
    Utterance,
)
from .synth import account, render_map, render_playbook, run_synthesis

LISTENER_STALE_S = 40.0  # phones send an audio segment every ~15 s
CONTEXT_LINES = 30  # earlier lines sent to the coach for context
MIN_NEW_CHARS = 200  # don't wake the coach for a sentence or two
SAVE_MIN_INTERVAL_S = 2.0  # new lines are saved at most this often; flush() catches the rest
NEW_REF = re.compile(r"\d+")


class SessionState(BaseModel):
    utterances: list[Utterance] = Field(default_factory=list)
    items: dict[str, MapItem] = Field(default_factory=dict)
    groups: dict[str, GroupState] = Field(default_factory=dict)
    message: PhoneMessage | None = None
    closed: bool = False
    synthesis: Synthesis | None = None
    started_at: float = 0.0  # wall-clock epoch for live sessions; 0 in simulations
    dropped: dict[str, dict[str, int]] = Field(default_factory=dict)  # group -> reason -> lines refused
    final_pass_failures: list[str] = Field(default_factory=list)


def initial_questions(cfg: EventConfig) -> QuestionSet:
    oq = cfg.opening_questions
    return QuestionSet(
        current=Question(text=oq[0]),
        next_in_sequence=Question(text=oq[1] if len(oq) > 1 else cfg.fallback_questions[0]),
        offshoots=[Question(text=q) for q in cfg.fallback_questions[1:3]],
        redirect=Question(text=cfg.closing_question, why="To close a turn"),
    )


class Session:
    def __init__(self, cfg: EventConfig, coach_llm: LLM, synth_llm: LLM, data_dir: Path):
        self.cfg = cfg
        self.coach_llm = coach_llm
        self.synth_llm = synth_llm
        self.data_dir = Path(data_dir)
        self.lock = threading.RLock()
        self._coaching: set[str] = set()
        self._idle = threading.Condition(self.lock)  # notified when a coach pass ends
        self._dirty = False
        self._last_save = 0.0
        state_file = self.data_dir / "state.json"
        if state_file.exists():
            self.state = SessionState.model_validate_json(state_file.read_text())
        else:
            self.state = SessionState(
                groups={
                    g.code: GroupState(code=g.code, label=g.label, questions=initial_questions(cfg)) for g in cfg.groups
                }
            )

    # ------------------------------------------------------------------ input

    def refusal(self, code: str) -> str | None:
        """Why a line for this group would be refused right now, or None."""
        with self.lock:
            if self.state.closed:
                return "closed"
            if self.state.groups[code].paused:
                return "paused"
            return None

    def ingest(self, code: str, text: str, t: float, speaker: str | None = None, source: str = "asr") -> Utterance | None:
        """Store one line. Returns None for an empty or refused line; refused
        lines are counted in state.dropped, and callers use refusal() to tell
        the sender."""
        text = text.strip()
        if not text:
            return None
        with self.lock:
            reason = self.refusal(code)
            if reason:
                self.note_dropped(code, reason)
                return None
            u = Utterance(id=f"u{len(self.state.utterances) + 1}", group=code, t=t, text=text, speaker=speaker, source=source)
            self.state.utterances.append(u)
            self._dirty = True
            self.flush(SAVE_MIN_INTERVAL_S)
            return u

    def note_dropped(self, code: str, reason: str) -> None:
        with self.lock:
            by = self.state.dropped.setdefault(code, {})
            by[reason] = by.get(reason, 0) + 1
            self._dirty = True

    def note_audio(self, code: str, device: str, t: float) -> None:
        with self.lock:
            g = self.state.groups[code]
            g.listener_device = device
            g.last_audio_t = t

    def claim_listener(self, code: str, device: str, t: float) -> None:
        with self.lock:
            g = self.state.groups[code]
            g.listener_device = device
            g.last_audio_t = t
            g.paused = False

    def set_paused(self, code: str, paused: bool) -> None:
        with self.lock:
            self.state.groups[code].paused = paused
            self.save()

    def pick(self, code: str, slot: str, t: float) -> None:
        """A group tapped a question in the More list. slot: next | offshoot:N | redirect | current."""
        with self.lock:
            g = self.state.groups[code]
            q = g.questions
            if slot == "current":
                chosen = q.current
            elif slot == "next":
                chosen = q.next_in_sequence
            elif slot == "redirect":
                chosen = q.redirect
            elif slot.startswith("offshoot:"):
                chosen = q.offshoots[int(slot.split(":", 1)[1])]
            else:
                raise ValueError(slot)
            g.picks.append({"t": t, "slot": slot, "text": chosen.text})
            g.questions = q.model_copy(update={"current": chosen})
            g.note = ""
            self._remember_current(g)
            self._dirty = True

    def strike(self, item_id: str) -> None:
        with self.lock:
            self.state.items[item_id].struck = True

    def set_message(self, markdown: str | None, t: float) -> None:
        with self.lock:
            self.state.message = PhoneMessage(markdown=markdown, t=t) if markdown else None
            self.save()

    # ------------------------------------------------------------------ coach

    def pending(self, code: str) -> list[Utterance]:
        g = self.state.groups[code]
        group_lines = [u for u in self.state.utterances if u.group == code]
        return group_lines[g.coached_through :]

    def coach_due(self, code: str, now: float) -> bool:
        with self.lock:
            if self.state.closed or code in self._coaching:
                return False
            g = self.state.groups[code]
            new = self.pending(code)
            chars = sum(len(u.text) for u in new)
            return chars >= MIN_NEW_CHARS and now - g.last_coach_t >= self.cfg.coach_interval_s

    def coach(self, code: str, now: float) -> dict | None:
        """One coach pass for a group. The LLM call runs outside the lock.

        State changes only after the whole update has been checked, so a
        failed pass leaves the group's lines pending for the next one.
        Returns counts for the coach log, or None if nothing ran."""
        with self.lock:
            if code in self._coaching:
                return None
            self._coaching.add(code)
            g = self.state.groups[code]
            group_lines = [u for u in self.state.utterances if u.group == code]
            new = group_lines[g.coached_through :]
            context = group_lines[max(0, g.coached_through - CONTEXT_LINES) : g.coached_through]
            own = [i for i in self.state.items.values() if i.group == code]
            other = [i for i in self.state.items.values() if i.group != code]
            snapshot = g.model_copy(deep=True)
            size = self.cfg.group(code).size
        try:
            if not new:
                return None
            upd = run_coach(
                self.coach_llm,
                self.cfg,
                group=snapshot,
                group_size=size,
                now=now,
                context=context,
                new=new,
                own_items=own,
                other_items=other,
            )
        finally:
            with self.lock:
                self._coaching.discard(code)
                self._idle.notify_all()
        with self.lock:
            g = self.state.groups[code]
            # Build every item first; nothing below the loop can fail halfway.
            base = len(self.state.items)
            new_ids = [f"{code.split('-')[0]}{base + k + 1}" for k in range(len(upd.new_items))]
            valid_utt = {u.id for u in new} | {u.id for u in context}
            built = []
            for k, ni in enumerate(upd.new_items):
                links = []
                for l in ni.links:
                    target = self._link_target(l.target_id, new_ids)
                    if target is None or target == new_ids[k]:
                        continue  # unknown id or a link to itself
                    links.append(Link(target_id=target, relation=l.relation))
                built.append(
                    MapItem(
                        id=new_ids[k],
                        group=code,
                        text=ni.text,
                        kind=ni.kind,
                        specific=ni.specific,
                        links=links,
                        utterance_ids=[i for i in ni.utterance_ids if i in valid_utt],
                    )
                )
            for item in built:
                self.state.items[item.id] = item
            g.coached_through += len(new)
            g.last_coach_t = now
            qs = upd.questions
            if not upd.replace_current:
                # The current question changes only when the group picks one or
                # the coach judges it answered; the rest of the set refreshes.
                qs = qs.model_copy(update={"current": g.questions.current})
            g.questions = qs
            g.note = upd.note
            self._remember_current(g)
            if upd.new_turn:
                g.turn_started_t = now
            self.save()
            return {"lines": len(new), "chars": sum(len(u.text) for u in new), "items": len(built)}

    def _link_target(self, target: str, new_ids: list[str]) -> str | None:
        """Resolve a link target: 'new:N' (models also write 'new:N3' or
        'new:#1') names an item from this update; anything else must be an
        existing item id."""
        if target.startswith("new:"):
            m = NEW_REF.search(target)
            idx = int(m.group()) if m else -1
            return new_ids[idx] if 0 <= idx < len(new_ids) else None
        return target if target in self.state.items else None

    @staticmethod
    def _remember_current(g: GroupState) -> None:
        text = g.questions.current.text
        if text not in g.asked:
            g.asked.append(text)

    def final_pass(self, now: float, log=None) -> list[str]:
        """After close: one coach pass per group over whatever is still
        pending, ignoring the 200-character threshold, so the end of each
        conversation reaches the map. Waits for any pass already running.
        Returns (and records) the groups whose lines could not be coached."""
        failed = []
        for code in list(self.state.groups):
            error = None
            for _ in range(3):  # a regular pass may start between our wait and our call
                with self.lock:
                    while code in self._coaching:
                        self._idle.wait()
                    if not self.pending(code):
                        break
                started = time.monotonic()
                try:
                    info = self.coach(code, now)
                except TRANSIENT_ERRORS as e:
                    error = f"{type(e).__name__}: {e}"
                    self.log_pass(code, now, started, None, error, final=True)
                    break
                if info is not None:
                    self.log_pass(code, now, started, info, None, final=True)
            left = len(self.pending(code))
            if left:
                failed.append(f"{code}: {left} lines not coached" + (f" ({error})" if error else ""))
        with self.lock:
            self.state.final_pass_failures = failed
            self.save()
        return failed

    def log_pass(self, code: str, now: float, started: float, info: dict | None, error: str | None, final: bool = False) -> None:
        """Append one line per coach pass to coach-log.jsonl in the session
        directory: counts and timing only, no transcript text. Purge deletes it."""
        rec = {
            "wall": round(time.time(), 1),
            "t": round(now, 1),
            "group": code,
            "seconds": round(time.monotonic() - started, 2),
            "final": final,
            **(info or {}),
            "error": error,
        }
        with self.lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with (self.data_dir / "coach-log.jsonl").open("a") as f:
                f.write(json.dumps(rec) + "\n")

    # ------------------------------------------------------------------ views

    def phone_state(self, code: str, device: str | None, now: float) -> dict:
        with self.lock:
            g = self.state.groups[code]
            s = self.state
            listener_stale = g.last_audio_t is not None and now - g.last_audio_t > LISTENER_STALE_S
            q = g.questions
            more = [
                {"slot": "current", "label": "Current", "text": q.current.text},
                {"slot": "next", "label": "Next in the sequence", "text": q.next_in_sequence.text},
                *[
                    {"slot": f"offshoot:{k}", "label": "Follow this thread", "text": o.text}
                    for k, o in enumerate(q.offshoots)
                ],
                {"slot": "redirect", "label": "Change direction", "text": q.redirect.text},
            ]
            return {
                "label": g.label,
                "mode": "message" if s.message else "question",
                "message": s.message.markdown if s.message else None,
                "closed": s.closed,
                "question": q.current.model_dump(),
                "eyebrow": "When there's a pause" if any(u.group == code for u in s.utterances) else "Pick someone to ask, then",
                "note": g.note,
                "more": more,
                "paused": g.paused,
                "is_listener": device is not None and device == g.listener_device and not listener_stale,
                "has_listener": g.listener_device is not None and not listener_stale,
                "listener_dropped": g.listener_device is not None and listener_stale and not g.paused,
                "minutes_left": max(0, round(self.cfg.minutes - now / 60)),
            }

    def overview(self, now: float) -> dict:
        with self.lock:
            out = []
            for code, g in self.state.groups.items():
                lines = [u for u in self.state.utterances if u.group == code]
                items = [i for i in self.state.items.values() if i.group == code and not i.struck]
                kinds: dict[str, int] = {}
                for i in items:
                    kinds[i.kind] = kinds.get(i.kind, 0) + 1
                out.append(
                    {
                        "code": code,
                        "label": g.label,
                        "lines": len(lines),
                        "items": len(items),
                        "kinds": kinds,
                        "current": g.questions.current.text,
                        "note": g.note,
                        "paused": g.paused,
                        "last_audio_ago": None if g.last_audio_t is None else round(now - g.last_audio_t),
                        "picks": len(g.picks),
                    }
                )
            return {
                "groups": out,
                "closed": self.state.closed,
                "message": self.state.message.markdown if self.state.message else None,
                "recent_items": [i.model_dump() for i in list(self.state.items.values())[-15:]],
                "usage": {"coach": self.coach_llm.usage_report(), "synth": self.synth_llm.usage_report()},
            }

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        with self.lock:
            self.state.closed = True
            self.save()

    def synthesize(self) -> dict[str, Path]:
        with self.lock:
            items = dict(self.state.items)
            utterances = list(self.state.utterances)
        syn = run_synthesis(self.synth_llm, self.cfg, list(items.values()), utterances)
        acct = account(syn, items)
        n_groups = len({u.group for u in self.state.utterances})
        note = f"Drafted from {n_groups} group conversation{'s' if n_groups != 1 else ''} at {self.cfg.title}."
        out_dir = self.data_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "playbook": out_dir / "playbook.md",
            "map": out_dir / "concept-map.md",
            "accounting": out_dir / "accounting.json",
        }
        paths["playbook"].write_text(render_playbook(syn, items, note))
        paths["map"].write_text(render_map(syn, items, acct))
        paths["accounting"].write_text(json.dumps(acct, indent=2))
        with self.lock:
            self.state.synthesis = syn
            self.save()
        return paths

    # ------------------------------------------------------------ persistence

    def flush(self, min_interval: float = 0.0) -> None:
        """Save if anything changed and at least min_interval seconds have
        passed since the last save. The server calls this every few seconds,
        so a crash loses at most a few seconds of lines."""
        with self.lock:
            if self._dirty and time.monotonic() - self._last_save >= min_interval:
                self.save()

    def save(self) -> None:
        with self.lock:
            self._dirty = False
            self._last_save = time.monotonic()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.data_dir / "state.json.tmp"
            tmp.write_text(self.state.model_dump_json(indent=1))
            tmp.replace(self.data_dir / "state.json")

    def purge_confidential(self) -> None:
        """Delete transcripts and map state; keep only the rendered output."""
        with self.lock:
            for p in self.data_dir.iterdir():
                if p.name == "output":
                    continue
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            self.state = SessionState()
