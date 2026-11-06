"""A single-page HTML walkthrough of a simulated run: setup, each group's
conversation, and the drafted playbook traced back to what was said.

Speakers are reconstructed from the runner's turn order (one ask and one
answer per group per step; the interviewee changes at even time shares), so
older runs without `asks.json` render too.
"""

from __future__ import annotations

import json
import math
from importlib.resources import files
from pathlib import Path

from ..config import EventConfig
from ..engine import SessionState
from .players import SimConfig

SLOT_LABELS = {
    "current": "Current question",
    "next": "Next in the sequence",
    "offshoot": "Follow this thread",
    "redirect": "Change direction",
}


def _minutes(summary: dict, state: SessionState) -> int:
    if summary.get("minutes"):
        return int(summary["minutes"])
    # Every ask starts before the end of the session; answers may run past it.
    by_group: dict[str, list] = {}
    for u in state.utterances:
        by_group.setdefault(u.group, []).append(u)
    last_ask = max(lines[i].t for lines in by_group.values() for i in range(0, len(lines), 2))
    return max(1, math.ceil(last_ask / 60))


def build_data(run_dir: Path, cfg: EventConfig, sim: SimConfig) -> dict:
    state = SessionState.model_validate_json((run_dir / "session" / "state.json").read_text())
    summary = json.loads((run_dir / "summary.json").read_text())
    judge = json.loads((run_dir / "judge.json").read_text())
    acct = json.loads((run_dir / "session" / "output" / "accounting.json").read_text())
    asks_path = run_dir / "asks.json"
    asks = json.loads(asks_path.read_text()) if asks_path.exists() else {}
    minutes = _minutes(summary, state)

    first_line: dict[str, list[str]] = {}
    for item in state.items.values():
        if item.struck:
            continue
        anchor = item.utterance_ids[0] if item.utterance_ids else None
        first_line.setdefault(anchor or "", []).append(item.id)

    speaker_of: dict[str, str] = {}
    groups = []
    for sg in sim.groups:
        if sg.code not in state.groups:
            continue
        g = state.groups[sg.code]
        members = sg.members
        share = minutes * 60 / len(members)
        lines = [u for u in state.utterances if u.group == sg.code]
        if not lines:
            continue
        picks = {round(p["t"], 3): p for p in g.picks}
        group_asks = asks.get(sg.code, [])
        out = []
        for k in range(0, len(lines), 2):
            step = k // 2
            ask_u = lines[k]
            ei = min(int(ask_u.t // share), len(members) - 1)
            expert = members[ei]
            others = [m for i, m in enumerate(members) if i != ei]
            asker = others[step % len(others)]
            meta = group_asks[step] if step < len(group_asks) else None
            pick = picks.get(round(ask_u.t, 3))
            out.append(
                {
                    "id": ask_u.id,
                    "t": ask_u.t,
                    "role": "ask",
                    "speaker": asker,
                    "expert": expert,
                    "text": ask_u.text,
                    "items": first_line.get(ask_u.id, []),
                    "pick": {
                        "label": SLOT_LABELS.get(pick["slot"].split(":")[0], pick["slot"]),
                        "text": pick["text"],
                    }
                    if pick
                    else None,
                    "phone": {"used": meta["used_phone"], "shown": meta.get("shown")} if meta else None,
                }
            )
            speaker_of[ask_u.id] = asker
            if k + 1 < len(lines):
                ans = lines[k + 1]
                out.append(
                    {
                        "id": ans.id,
                        "t": ans.t,
                        "role": "answer",
                        "speaker": expert,
                        "expert": expert,
                        "text": ans.text,
                        "items": first_line.get(ans.id, []),
                        "lessons": meta.get("lessons_touched") if meta else None,
                    }
                )
                speaker_of[ans.id] = expert
        groups.append({"code": sg.code, "label": g.label, "members": members, "lines": out})

    items = {
        i.id: {
            "id": i.id,
            "group": i.group,
            "text": i.text,
            "kind": i.kind,
            "specific": i.specific,
            "utts": i.utterance_ids,
            "speaker": speaker_of.get(i.utterance_ids[0]) if i.utterance_ids else None,
            "used": list(dict.fromkeys(acct["used_in"].get(i.id, []))),
        }
        for i in state.items.values()
        if not i.struck
    }
    usage = summary.get("usage", {})
    cost = sum(u.get("cost_usd", 0) for role in usage.values() for u in role.values())
    return {
        "run": run_dir.name,
        "event": {
            "title": cfg.title,
            "scenario": cfg.scenario,
            "goal": cfg.goal,
            "opening": cfg.opening_questions,
            "fallback": cfg.fallback_questions,
            "closing": cfg.closing_question,
            "coach_interval_s": cfg.coach_interval_s,
        },
        "minutes": minutes,
        "sim": {"p_look": sim.p_look_at_phone, "p_more": sim.p_open_more},
        "personas": {p.id: p.model_dump() for p in sim.personas},
        "groups": groups,
        "items": items,
        "synthesis": state.synthesis.model_dump() if state.synthesis else None,
        "judge": judge,
        "summary": {k: v for k, v in summary.items() if k not in ("usage", "self_reported_lessons_touched")},
        "cost": round(cost, 2),
        "models": summary.get("models"),
    }


def write_walkthrough(run_dir: Path, cfg: EventConfig, sim: SimConfig) -> Path:
    data = json.dumps(build_data(run_dir, cfg, sim), ensure_ascii=False).replace("</", "<\\/")
    template = files("knowhow").joinpath("static/walkthrough.html").read_text()
    out = run_dir / "walkthrough.html"
    out.write_text(template.replace("/*__DATA__*/null", data, 1))
    return out
