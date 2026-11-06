"""Session engine: all state for one event, independent of transport.

The live server and the simulator both drive this object. Times are seconds
since session start; the server passes wall-clock offsets, the simulator
passes its own simulated clock.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from .coach import run_coach
from .config import EventConfig
from .llm import LLM
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


class SessionState(BaseModel):
    utterances: list[Utterance] = Field(default_factory=list)
    items: dict[str, MapItem] = Field(default_factory=dict)
    groups: dict[str, GroupState] = Field(default_factory=dict)
    message: PhoneMessage | None = None
    closed: bool = False
    synthesis: Synthesis | None = None
    started_at: float = 0.0  # wall-clock epoch for live sessions; 0 in simulations


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

    def ingest(self, code: str, text: str, t: float, speaker: str | None = None, source: str = "asr") -> Utterance | None:
        text = text.strip()
        if not text:
            return None
        with self.lock:
            g = self.state.groups[code]
            if self.state.closed or g.paused:
                return None
            u = Utterance(id=f"u{len(self.state.utterances) + 1}", group=code, t=t, text=text, speaker=speaker, source=source)
            self.state.utterances.append(u)
            return u

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

    def strike(self, item_id: str) -> None:
        with self.lock:
            self.state.items[item_id].struck = True

    def set_message(self, markdown: str | None, t: float) -> None:
        with self.lock:
            self.state.message = PhoneMessage(markdown=markdown, t=t) if markdown else None

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

    def coach(self, code: str, now: float) -> None:
        """One coach pass for a group. The LLM call runs outside the lock."""
        with self.lock:
            if code in self._coaching:
                return
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
                return
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
        with self.lock:
            g = self.state.groups[code]
            g.coached_through += len(new)
            g.last_coach_t = now
            # Assign ids, resolving 'new:N' links to the ids just assigned.
            base = len(self.state.items)
            new_ids = [f"{code.split('-')[0]}{base + k + 1}" for k in range(len(upd.new_items))]
            valid_utt = {u.id for u in new} | {u.id for u in context}
            for k, ni in enumerate(upd.new_items):
                links = []
                for l in ni.links:
                    target = l.target_id
                    if target.startswith("new:"):
                        idx = int(target.split(":", 1)[1])
                        if not 0 <= idx < len(new_ids):
                            continue
                        target = new_ids[idx]
                    elif target not in self.state.items:
                        continue
                    links.append(Link(target_id=target, relation=l.relation))
                self.state.items[new_ids[k]] = MapItem(
                    id=new_ids[k],
                    group=code,
                    text=ni.text,
                    kind=ni.kind,
                    specific=ni.specific,
                    links=links,
                    utterance_ids=[i for i in ni.utterance_ids if i in valid_utt],
                )
            qs = upd.questions
            if not upd.replace_current:
                # The current question changes only when the group picks one or
                # the coach judges it answered; the rest of the set refreshes.
                qs = qs.model_copy(update={"current": g.questions.current})
            g.questions = qs
            g.note = upd.note
            if upd.new_turn:
                g.turn_started_t = now
            self.save()

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

    def save(self) -> None:
        with self.lock:
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
