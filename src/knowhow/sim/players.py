"""Simulated participants for agent-played runthroughs.

Each persona carries hidden ground truth: lessons with a concrete case, how
they'd know, and where the advice stops working. The judge later checks how
much of that ground truth reached the transcript, the map and the playbook.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..llm import LLM


class Lesson(BaseModel):
    id: str
    lesson: str
    case: str  # the incident behind it
    cue: str = ""  # how they knew
    edges: str = ""  # when it doesn't apply


class Persona(BaseModel):
    id: str
    angle: str  # one line, the kind of thing a profile would say
    background: str
    style: str  # how they talk: terse, rambling, tangents, hedges...
    lessons: list[Lesson]


class SimGroup(BaseModel):
    code: str
    members: list[str]


class SimConfig(BaseModel):
    # Who is in the room, for the players' shared framing.
    room: str = "people with related practical expertise"
    personas: list[Persona]
    groups: list[SimGroup]
    # Probability, per asking turn, that the asker opens "More questions".
    p_open_more: float = 0.25
    # Probability that the asker glances at the focused question at all.
    p_look_at_phone: float = 0.6
    player_effort: str = "low"

    def persona(self, pid: str) -> Persona:
        return next(p for p in self.personas if p.id == pid)


class AskerTurn(BaseModel):
    utterance: str = Field(description="What you say aloud, in spoken style")
    used_phone: Literal["no", "focused_question", "picked_from_more"]
    picked_slot: str = Field(description="If picked_from_more: the slot id you picked, else empty")
    note_to_self: str = Field(description="One short line on why you asked this; not spoken")


class ExpertTurn(BaseModel):
    utterance: str = Field(description="What you say aloud, in spoken style")
    lesson_ids_touched: list[str] = Field(description="Ids of your lessons this answer conveys, even partly")


PLAYER_RULES = """\
You are playing a participant at an in-person working session of about a \
dozen people: {room}. The room splits \
into small groups that interview each other for {minutes} minutes so that \
practical lessons can be written into a shared playbook. A phone in the \
middle of the group shows a suggested question; using it is optional.

Speak the way people actually talk in a small group: conversational, \
sometimes fragmentary, one turn at a time. Never narrate actions or write \
stage directions. Never mention that you are simulated.
"""

ASKER_SYSTEM = PLAYER_RULES + """
Right now you are ASKING. Your aim is to draw out the lessons the other \
person has learned from experience. Behave like a reasonably good but \
ordinary interviewer: sometimes you use the phone's suggestion, sometimes \
you follow your own curiosity, sometimes you share a brief reaction from \
your own experience before asking. Keep each turn short (one to three \
sentences). Do not ask several questions at once.
"""

EXPERT_SYSTEM = PLAYER_RULES + """
Right now you are being INTERVIEWED. Answer from your persona's experience. \
Your hidden lessons are things you know but would not recite unprompted: \
reveal one when a question gives you a natural opening, and reveal more \
detail (the case, how you knew, where it stops working) when asked \
follow-ups. You may wander into related stories, hedge, or answer a \
different question than the one asked, as real experts do. Do not invent \
dramatic new facts that contradict your persona. Answers are usually 40 to \
150 words, occasionally longer for a good story.
"""


def _persona_block(p: Persona, hidden: bool) -> str:
    s = f"Your persona: {p.background}\nHow you talk: {p.style}\n"
    if hidden:
        s += "Your hidden lessons:\n" + "\n".join(
            f"- [{l.id}] {l.lesson} Case: {l.case}"
            + (f" How you knew: {l.cue}" if l.cue else "")
            + (f" Where it stops working: {l.edges}" if l.edges else "")
            for l in p.lessons
        )
    return s


def _history(lines: list[tuple[str, str]]) -> str:
    return "\n".join(f"{who}: {text}" for who, text in lines) or "(the conversation is just starting)"


def asker_turn(
    llm: LLM,
    sim: SimConfig,
    minutes: int,
    asker: Persona,
    expert: Persona,
    history: list[tuple[str, str]],
    phone: dict | None,
    show_more: bool,
    turn_note: str,
) -> AskerTurn:
    if phone is None:
        phone_txt = "You are not looking at the phone right now."
    else:
        phone_txt = f'The phone shows: "{phone["question"]["text"]}"'
        if phone.get("note"):
            phone_txt += f" (under it: {phone['note']})"
        if show_more:
            phone_txt += "\nYou tapped More questions. It lists:\n" + "\n".join(
                f'- slot "{m["slot"]}" ({m["label"]}): {m["text"]}' for m in phone["more"]
            )
    user = f"""{_persona_block(asker, hidden=False)}
You are interviewing: {expert.id}, who {expert.angle}.
{turn_note}

Recent conversation:
{_history(history)}

{phone_txt}

Say your next line."""
    return llm.parse(
        purpose="player",
        system=ASKER_SYSTEM.format(minutes=minutes, room=sim.room),
        user=user,
        schema=AskerTurn,
        effort=sim.player_effort,
    )


def expert_turn(llm: LLM, sim: SimConfig, minutes: int, expert: Persona, history: list[tuple[str, str]]) -> ExpertTurn:
    user = f"""{_persona_block(expert, hidden=True)}

Recent conversation:
{_history(history)}

Say your next line."""
    return llm.parse(
        purpose="player",
        system=EXPERT_SYSTEM.format(minutes=minutes, room=sim.room),
        user=user,
        schema=ExpertTurn,
        effort=sim.player_effort,
    )
