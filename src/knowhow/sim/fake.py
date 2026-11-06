"""Offline stand-in for every LLM role, for tests and `--dry-run`.

Responses are mechanical but schema-valid, so the whole pipeline (server,
coach, synthesis, sim, judge, reports) can be exercised without API calls.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ..llm import ScriptedLLM
from ..models import Claim, CoachUpdate, NewItem, Question, QuestionSet, Section, Synthesis, Thread
from .judge import JudgeReport, LessonVerdict
from .players import AskerTurn, ExpertTurn


def _ids(pattern: str, text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(pattern, text)))


def respond(purpose: str, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema is CoachUpdate:
        new = user.split("<new_lines>", 1)[1]
        lines = re.findall(r"^(u\d+) @\s*[\d.]+m (.*)$", new, flags=re.M)
        items = [
            NewItem(text=text[:120], kind="what_to_do", specific=len(text) > 80, links=[], utterance_ids=[uid])
            for uid, text in lines[-2:]
        ]
        return CoachUpdate(
            new_items=items,
            questions=QuestionSet(
                current=Question(text="What happened next?"),
                next_in_sequence=Question(text="How did you know it was time to act?"),
                offshoots=[Question(text="Who did you call first?"), Question(text="What did that cost?")],
                redirect=Question(text="So what's the lesson for someone starting tomorrow?"),
            ),
            replace_current=bool(lines),
            note="",
            new_turn=False,
        )
    if schema is Synthesis:
        item_ids = _ids(r"(?m)^([A-Za-z]+\d+) \(group", user)
        half = len(item_ids) // 2
        return Synthesis(
            title="Draft playbook (dry run)",
            structure_note="Organized as a single list; could be organized by phase instead.",
            sections=[Section(title="Everything", claims=[Claim(text=f"Point {i}.", item_ids=[i]) for i in item_ids[:half]])],
            disagreements=[],
            open_questions=[],
            threads=[Thread(title="All", item_ids=item_ids)],
        )
    if schema is AskerTurn:
        return AskerTurn(utterance="Can you tell me about a time that came up?", used_phone="focused_question", picked_slot="", note_to_self="")
    if schema is ExpertTurn:
        lids = _ids(r"- \[([^\]]+)\]", user)
        return ExpertTurn(
            utterance="Sure. The first winter I baked, the dough took twice as long to rise, so I started watching the dough instead of the clock.",
            lesson_ids_touched=lids[:1],
        )
    if schema is JudgeReport:
        lids = _ids(r"(?m)^\[([^\]]+)\]", user)
        return JudgeReport(
            lessons=[LessonVerdict(lesson_id=l, in_transcript="partial", in_map="no", in_playbook="no", note="dry run") for l in lids],
            weak_claims=[],
            observations=["Dry run: responses are mechanical."],
        )
    raise TypeError(f"fake LLM has no response for {schema.__name__} ({purpose})")


def fake_llm() -> ScriptedLLM:
    return ScriptedLLM(respond)
