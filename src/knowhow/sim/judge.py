"""Scores a simulated run against the personas' hidden lessons."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..llm import LLM
from ..models import MapItem, Utterance
from .players import Persona

Level = Literal["yes", "partial", "no"]


class LessonVerdict(BaseModel):
    lesson_id: str
    in_transcript: Level
    in_map: Level
    in_playbook: Level
    note: str


class ClaimCheck(BaseModel):
    claim: str
    supported: Level
    note: str


class JudgeReport(BaseModel):
    lessons: list[LessonVerdict]
    weak_claims: list[ClaimCheck]
    observations: list[str]


JUDGE_SYSTEM = """\
You evaluate one simulated run of a knowledge-elicitation session. Simulated \
experts each held hidden lessons. You get the lessons, the full transcript, \
the concept-map items the live system captured, and the drafted playbook.

For EVERY lesson give three verdicts:
- in_transcript: did the expert actually convey it in conversation? \
'partial' if only the general point came out without the case or cue.
- in_map: is it represented in the map items?
- in_playbook: is it represented in the playbook, faithfully? A distorted \
version counts as 'partial'; explain in note.

weak_claims: list only playbook claims that the transcript does NOT fully \
support (overstated, merged from opposing views, or invented). Empty if \
none.

observations: up to six short, specific observations about where knowledge \
was lost or distorted (elicitation, capture, or synthesis) and what in the \
questions or process seems to have caused it.
"""


def judge(
    llm: LLM,
    personas: list[Persona],
    utterances: list[Utterance],
    items: list[MapItem],
    playbook_md: str,
    briefing: str = "",
) -> JudgeReport:
    lessons = "\n".join(
        f"[{l.id}] ({p.id}) {l.lesson} Case: {l.case}" + (f" Cue: {l.cue}" if l.cue else "")
        for p in personas
        for l in p.lessons
    )
    transcript = "\n".join(f"{u.group} {u.speaker or '?'}: {u.text}" for u in utterances)
    item_txt = "\n".join(f"{i.id} [{i.kind}] {i.text}" for i in items if not i.struck)
    user = f"""<event_briefing>
Facts the drafter was given before the session. Claims that restate these are supported.
{briefing}
</event_briefing>
<lessons>
{lessons}
</lessons>
<transcript>
{transcript}
</transcript>
<map_items>
{item_txt}
</map_items>
<playbook>
{playbook_md}
</playbook>"""
    return llm.parse(purpose="judge", system=JUDGE_SYSTEM, user=user, schema=JudgeReport, effort="high")
