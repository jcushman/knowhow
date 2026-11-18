"""Event configuration: the instance layer on top of the general engine."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class GroupConfig(BaseModel):
    code: str  # short join code printed on the handout, e.g. "C-4817"
    label: str  # "Group C"
    size: int | None = None  # expected number of people, for time-per-person pacing


class ModelConfig(BaseModel):
    coach: str = "claude-opus-5-5"
    synth: str = "claude-opus-5-5"
    coach_effort: str = "low"
    synth_effort: str = "high"
    fallbacks: bool = True  # server-side refusal fallbacks


class EventConfig(BaseModel):
    title: str
    scenario: str
    goal: str
    minutes: int = 45
    groups: list[GroupConfig]
    opening_questions: list[str] = Field(default_factory=list)
    fallback_questions: list[str] = Field(default_factory=list)
    # Record mode: the stages the room walks through, in order. Extracted
    # insights are sorted under these headings.
    steps: list[str] = Field(default_factory=list)
    closing_question: str = "What didn't I ask that I should have?"
    # Topic lenses feed only coverage hints for the coach; they are never the
    # playbook outline (to avoid premature convergence).
    lenses: list[str] = Field(default_factory=list)
    vocabulary: list[str] = Field(default_factory=list)  # ASR hint words
    # Extra context documents (problem statement, background pages, prior
    # playbooks). Paths are relative to the config file.
    documents: list[str] = Field(default_factory=list)
    # Group-level hints, never names; see README "Participant profiles".
    group_hints: dict[str, str] = Field(default_factory=dict)
    coach_interval_s: float = 60.0
    models: ModelConfig = Field(default_factory=ModelConfig)

    base_dir: Path = Path(".")

    def document_text(self) -> str:
        parts = []
        for rel in self.documents:
            p = self.base_dir / rel
            parts.append(f"<document name={p.name!r}>\n{p.read_text()}\n</document>")
        return "\n".join(parts)

    def group(self, code: str) -> GroupConfig:
        for g in self.groups:
            if g.code == code:
                return g
        raise KeyError(code)


def load_event(path: str | Path) -> EventConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    cfg = EventConfig.model_validate(data)
    cfg.base_dir = path.parent
    return cfg
