"""Agent-played runthroughs of a whole session.

Groups advance in lockstep on a simulated clock: each step, every group
produces one ask/answer exchange; then the coach runs for groups that are
due, exactly as the live server's coach loop would. Utterances enter the
engine without speaker labels (as from ASR) unless `speaker_labels` is set.
"""

from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import EventConfig
from ..engine import Session
from ..llm import LLM, TRANSIENT_ERRORS
from .judge import judge
from .players import Persona, SimConfig, asker_turn, expert_turn
from .walkthrough import write_walkthrough

WORDS_PER_SECOND = 2.5
GAP_S = 2.0
HISTORY_LINES = 16


def load_sim(path: str | Path) -> SimConfig:
    return SimConfig.model_validate(yaml.safe_load(Path(path).read_text()))


@dataclass
class GroupRun:
    code: str
    members: list[Persona]
    clock: float = 0.0
    history: list[tuple[str, str]] = field(default_factory=list)
    turn_index: int = -1
    asks: list[dict] = field(default_factory=list)  # phone use per asking turn
    touched: dict[str, int] = field(default_factory=dict)  # lesson id -> times touched
    rng: random.Random = field(default_factory=random.Random)  # per group, so threads don't share one

    def expert_for(self, minutes: int) -> tuple[int, Persona]:
        share = minutes * 60 / len(self.members)
        k = min(int(self.clock // share), len(self.members) - 1)
        return k, self.members[k]

    def asker_for(self, k: int, step: int) -> Persona:
        others = [m for i, m in enumerate(self.members) if i != k]
        return others[step % len(others)]  # in triples the two non-experts alternate


def _advance(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND + GAP_S


def step_group(
    gr: GroupRun, session: Session, sim: SimConfig, player: LLM, minutes: int, step: int, labels: bool
) -> None:
    k, expert = gr.expert_for(minutes)
    new_turn = k != gr.turn_index
    if new_turn:
        gr.turn_index = k
        gr.history.append(("(facilitation)", f"--- {expert.id} is now being interviewed ---"))
    asker = gr.asker_for(k, step)
    looks = gr.rng.random() < sim.p_look_at_phone or new_turn
    phone = session.phone_state(gr.code, None, gr.clock) if looks else None
    show_more = phone is not None and gr.rng.random() < sim.p_open_more
    turn_note = (
        f"You are starting a new interview with {expert.id}. Begin the way the session asked people to begin."
        if new_turn
        else ""
    )
    a = asker_turn(player, sim, minutes, asker, expert, gr.history[-HISTORY_LINES:], phone, show_more, turn_note)
    if a.used_phone == "picked_from_more" and a.picked_slot:
        try:
            session.pick(gr.code, a.picked_slot, gr.clock)
        except (ValueError, IndexError):
            pass  # the player named a slot that doesn't exist; treat as not picked
    ask = {
        "t": gr.clock,
        "asker": asker.id,
        "expert": expert.id,
        "used_phone": a.used_phone,
        "slot": a.picked_slot,
        "looked": looks,
        "more_shown": show_more,
        "shown": phone["question"]["text"] if phone else None,
        "note_to_self": a.note_to_self,
    }
    gr.asks.append(ask)
    session.ingest(gr.code, a.utterance, t=gr.clock, speaker=asker.id if labels else None, source="sim")
    gr.history.append((asker.id, a.utterance))
    gr.clock += _advance(a.utterance)

    e = expert_turn(player, sim, minutes, expert, gr.history[-HISTORY_LINES:])
    ask["lessons_touched"] = e.lesson_ids_touched
    for lid in e.lesson_ids_touched:
        gr.touched[lid] = gr.touched.get(lid, 0) + 1
    session.ingest(gr.code, e.utterance, t=gr.clock, speaker=expert.id if labels else None, source="sim")
    gr.history.append((expert.id, e.utterance))
    gr.clock += _advance(e.utterance)


def coach_or_log(session: Session, gr: "GroupRun", failures: list[str], log) -> None:
    """A failed coach call leaves the group's lines pending for the next pass,
    as it would in a live session, so the run continues."""
    started = time.monotonic()
    try:
        info = session.coach(gr.code, gr.clock)
    except TRANSIENT_ERRORS as e:
        failures.append(f"{gr.code}@{gr.clock / 60:.1f}m: {e}")
        log(f"coach failed for {gr.code}: {str(e)[:300]}")
        session.log_pass(gr.code, gr.clock, started, None, f"{type(e).__name__}: {e}")
        return
    if info is not None:
        session.log_pass(gr.code, gr.clock, started, info, None)


def run_sim(
    cfg: EventConfig,
    sim: SimConfig,
    *,
    coach_llm: LLM,
    synth_llm: LLM,
    player_llm: LLM,
    judge_llm: LLM,
    out_dir: Path,
    minutes: int | None = None,
    seed: int = 0,
    speaker_labels: bool = False,
    groups: list[str] | None = None,
    log=print,
) -> Path:
    minutes = minutes or cfg.minutes
    cfg = cfg.model_copy(update={"minutes": minutes})
    out_dir.mkdir(parents=True, exist_ok=True)
    session = Session(cfg, coach_llm, synth_llm, out_dir / "session")
    runs = [
        GroupRun(code=g.code, members=[sim.persona(m) for m in g.members], rng=random.Random(f"{seed}-{g.code}"))
        for g in sim.groups
        if groups is None or g.code in groups
    ]
    if not runs:
        raise ValueError(f"no sim groups match {groups}")
    for gr in runs:
        if gr.code not in session.state.groups:
            raise KeyError(f"sim group {gr.code} is not in the event config")
    started = time.time()
    coach_failures: list[str] = []
    end = minutes * 60
    step = 0
    with ThreadPoolExecutor(max_workers=max(4, len(runs) * 2)) as pool:
        while any(gr.clock < end for gr in runs):
            active = [gr for gr in runs if gr.clock < end]
            list(pool.map(lambda gr: step_group(gr, session, sim, player_llm, minutes, step, speaker_labels), active))
            due = [gr for gr in runs if session.coach_due(gr.code, gr.clock)]
            list(pool.map(lambda gr: coach_or_log(session, gr, coach_failures, log), due))
            step += 1
            log(
                f"step {step}: "
                + " ".join(f"{gr.code}@{gr.clock / 60:.1f}m" for gr in runs)
                + f" items={len(session.state.items)}"
            )
    # Final coach pass so the tail of each conversation reaches the map.
    session.close()
    for failure in session.final_pass(max(gr.clock for gr in runs)):
        coach_failures.append(f"final pass {failure}")
        log(f"final pass: {failure}")
    log("synthesizing…")
    paths = session.synthesize()
    playbook = paths["playbook"].read_text()

    personas = [p for gr in runs for p in gr.members]
    log("judging…")
    briefing = f"Event: {cfg.title}\nScenario: {cfg.scenario}\nGoal: {cfg.goal}"
    report = judge(
        judge_llm, personas, session.state.utterances, list(session.state.items.values()), playbook, briefing
    )

    summary = summarize(runs, session, report, time.time() - started)
    summary["coach_failures"] = coach_failures
    summary["minutes"] = minutes
    summary["models"] = {
        role: getattr(llm, "model", None)
        for role, llm in (("coach", coach_llm), ("synth", synth_llm), ("player", player_llm), ("judge", judge_llm))
    }
    summary["usage"] = {
        "coach": coach_llm.usage_report(),
        "synth": synth_llm.usage_report(),
        "player": player_llm.usage_report(),
        "judge": judge_llm.usage_report(),
    }
    (out_dir / "transcript.jsonl").write_text("\n".join(u.model_dump_json() for u in session.state.utterances) + "\n")
    (out_dir / "judge.json").write_text(report.model_dump_json(indent=2))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "asks.json").write_text(json.dumps({gr.code: gr.asks for gr in runs}, indent=1))
    (out_dir / "report.md").write_text(render_report(summary, report))
    write_walkthrough(out_dir, cfg, sim)
    return out_dir


def summarize(runs: list[GroupRun], session: Session, report, wall_s: float) -> dict:
    def rate(field_: str, level: tuple[str, ...]) -> float:
        v = report.lessons
        return round(sum(getattr(x, field_) in level for x in v) / len(v), 3) if v else 0.0

    asks = [a for gr in runs for a in gr.asks]
    picks = [p for g in session.state.groups.values() for p in g.picks]
    slots: dict[str, int] = {}
    for p in picks:
        key = p["slot"].split(":")[0]
        slots[key] = slots.get(key, 0) + 1
    return {
        "wall_seconds": round(wall_s),
        "utterances": len(session.state.utterances),
        "map_items": len(session.state.items),
        "lessons": len(report.lessons),
        "elicited": rate("in_transcript", ("yes",)),
        "elicited_incl_partial": rate("in_transcript", ("yes", "partial")),
        "captured_in_map": rate("in_map", ("yes",)),
        "in_playbook": rate("in_playbook", ("yes",)),
        "in_playbook_incl_partial": rate("in_playbook", ("yes", "partial")),
        "weak_claims": len(report.weak_claims),
        "asking_turns": len(asks),
        "used_focused_question": sum(a["used_phone"] == "focused_question" for a in asks),
        "picked_from_more": sum(a["used_phone"] == "picked_from_more" for a in asks),
        "more_shown": sum(a["more_shown"] for a in asks),
        "picks_by_slot": slots,
        "self_reported_lessons_touched": {gr.code: gr.touched for gr in runs},
    }


def render_report(s: dict, report) -> str:
    lines = [
        "# Simulated run",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Lessons (hidden ground truth) | {s['lessons']} |",
        f"| Conveyed in conversation | {s['elicited']:.0%} ({s['elicited_incl_partial']:.0%} incl. partial) |",
        f"| Captured in map | {s['captured_in_map']:.0%} |",
        f"| In playbook | {s['in_playbook']:.0%} ({s['in_playbook_incl_partial']:.0%} incl. partial) |",
        f"| Weak or unsupported playbook claims | {s['weak_claims']} |",
        f"| Utterances / map items | {s['utterances']} / {s['map_items']} |",
        f"| Asking turns: used focused question / picked from More / More shown | "
        f"{s['used_focused_question']} / {s['picked_from_more']} / {s['more_shown']} of {s['asking_turns']} |",
        f"| Picks by slot | {s['picks_by_slot']} |",
        f"| Failed coach passes (lines retried next pass) | {len(s.get('coach_failures', []))} |",
        f"| Wall time | {s['wall_seconds']} s |",
        "",
        "## Lessons",
        "",
        "| Lesson | Transcript | Map | Playbook | Note |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {v.lesson_id} | {v.in_transcript} | {v.in_map} | {v.in_playbook} | {v.note} |" for v in report.lessons]
    if report.weak_claims:
        lines += ["", "## Weak claims", ""]
        lines += [f"- ({c.supported}) {c.claim} — {c.note}" for c in report.weak_claims]
    if report.observations:
        lines += ["", "## Observations", ""] + [f"- {o}" for o in report.observations]
    cost = sum(u.get("cost_usd", 0) for role in s.get("usage", {}).values() for u in role.values())
    if cost:
        lines += ["", f"Model cost: ${cost:.2f} (as reported by OpenRouter)"]
    lines += ["", "## Model usage", "", "```", json.dumps(s.get("usage", {}), indent=1), "```", ""]
    return "\n".join(lines)
