"""Data model shared by the live server, the coach, synthesis and simulations.

Two families of types live here:

* Session state (`Utterance`, `MapItem`, `GroupState`, ...) that the engine
  keeps in memory and persists to the session directory until purge.
* LLM output schemas (`CoachUpdate`, `Synthesis`, ...) passed to
  `messages.parse` as `output_format`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Role an item plays in a playbook. Deliberately topic-neutral so the same
# engine works for any domain; the event config never changes this list.
ItemKind = Literal[
    "case",  # a concrete past incident or example
    "what_to_do",
    "how_to_tell",  # cues, signs, thresholds, judgments
    "why",
    "what_you_need",  # people, tools, access
    "what_goes_wrong",
    "open_question",
]

LinkRelation = Literal["elaborates", "supports", "contradicts", "depends_on", "same_as", "questions"]


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


class Utterance(BaseModel):
    id: str
    group: str
    t: float  # seconds since session start (wall clock live, simulated clock in sims)
    text: str
    speaker: str | None = None  # anonymous label if known; ASR mode leaves this empty
    source: Literal["asr", "typed", "sim"] = "asr"


class Link(BaseModel):
    target_id: str
    relation: LinkRelation


class MapItem(BaseModel):
    id: str
    group: str
    text: str
    kind: ItemKind
    specific: bool  # grounded in a concrete case/detail rather than a general remark
    links: list[Link] = Field(default_factory=list)
    utterance_ids: list[str] = Field(default_factory=list)
    struck: bool = False


class Question(BaseModel):
    text: str
    why: str = ""  # one short line shown under the question, may be empty


class QuestionSet(BaseModel):
    current: Question
    next_in_sequence: Question
    offshoots: list[Question] = Field(default_factory=list)
    redirect: Question


class PhoneMessage(BaseModel):
    markdown: str
    t: float


class GroupState(BaseModel):
    code: str
    label: str
    questions: QuestionSet
    note: str = ""  # context line under the focused question
    paused: bool = False
    listener_device: str | None = None
    last_audio_t: float | None = None
    last_coach_t: float = 0.0
    coached_through: int = 0  # number of utterances already sent to the coach
    turn_started_t: float = 0.0
    picks: list[dict] = Field(default_factory=list)  # which suggestions were used, for evaluation
    asked: list[str] = Field(default_factory=list)  # every question that has been the current one


# ---------------------------------------------------------------------------
# LLM output schemas
# ---------------------------------------------------------------------------


class NewLink(BaseModel):
    target_id: str = Field(description="Id of an existing map item, or 'new:N' for the Nth new item in this update (0-based)")
    relation: LinkRelation


class NewItem(BaseModel):
    text: str = Field(description="One sentence, close to the speaker's own wording")
    kind: ItemKind
    specific: bool
    links: list[NewLink]
    utterance_ids: list[str]


class CoachUpdate(BaseModel):
    new_items: list[NewItem]
    questions: QuestionSet
    replace_current: bool = Field(
        description="True only if the current question has been answered or the conversation has clearly moved on"
    )
    note: str = Field(description="Optional one-line context for the group, e.g. 'This thread is new to the room.' Empty if none.")
    new_turn: bool = Field(description="True if the group has switched to interviewing a different person")


class Claim(BaseModel):
    text: str
    item_ids: list[str]
    caveat: str = ""


class Section(BaseModel):
    title: str
    intro: str = ""
    claims: list[Claim]


class Disagreement(BaseModel):
    summary: str
    item_ids: list[str]


class Thread(BaseModel):
    title: str = Field(description="Short label in the participants' own terms")
    item_ids: list[str]


class Synthesis(BaseModel):
    title: str
    structure_note: str = Field(description="One sentence on how the playbook is organized and one alternative organization")
    sections: list[Section]
    disagreements: list[Disagreement]
    open_questions: list[Claim]
    threads: list[Thread]
