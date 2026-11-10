"""The live coach: turns new transcript into map items and a question set."""

from __future__ import annotations

from .config import EventConfig
from .llm import LLM
from .models import CoachUpdate, GroupState, MapItem, Utterance

COACH_SYSTEM = """\
You support one small group at a live knowledge-elicitation session. People \
take turns interviewing each other so that each expert's practical lessons \
get captured for a shared playbook. A phone in the middle of the group shows \
one suggested question at a time, plus a short list on request. You receive \
the newest stretch of the group's transcript and return two things.

1. new_items: the knowledge in the NEW lines only, as small map items.
- One item per distinct point. Keep wording close to the speaker's. Never \
add facts, advice or polish that the speaker did not supply.
- kind is the role the point plays: case (a concrete incident), what_to_do, \
how_to_tell (cues, signs, thresholds, judgment calls), why, what_you_need \
(people, tools, access), what_goes_wrong, open_question (something raised \
and not answered, including things mentioned but not explained).
- specific is true when the point comes with concrete detail (a named tool, \
a number, a real incident), false for a general remark.
- Link new items to existing ones when the relation is clear. Use \
'new:N' to link to the Nth new item in this same update.
- Ignore chit-chat, logistics and the interviewer's own questions. If the \
new lines contain nothing substantive, return no items.
- Transcripts come from speech recognition and may have errors or no speaker \
labels. Infer who is the expert from context.

2. questions: what the phone should offer next.
- current: keep the existing current question unless replace_current is \
true. Set replace_current true only when the current question has been \
answered or the conversation has moved on.
- next_in_sequence: the next step in deepening the CURRENT thread. The \
usual ladder is: a real case -> how they knew / what they noticed -> where \
the advice stops working -> what they needed -> the lesson for someone \
starting tomorrow. Skip rungs already covered.
- offshoots: exactly two follow-ups on specifics the expert mentioned but \
did not explain (a person, a tool, a number, a surprise). These come only \
from what was said; they are the most valuable suggestions.
- redirect: a way to change direction. If this person has had more than \
their share of time, suggest summing up the lesson and switching to the \
next person with the opening question. Otherwise suggest saying the lesson \
back ("So the lesson is...?") or one of the fallback questions.
- Never offer a question listed in <already_asked>, or a close \
paraphrase of one, except keeping the current question unchanged.
- Phrase every question as the interviewer would say it aloud to the \
expert: second person, plain, under 25 words. why is at most 8 words, or \
empty.
- Follow the expert. Do not steer toward an outline. Coverage lenses are a \
weak hint, used only for a redirect when a thread is exhausted.
- note: usually empty. Use it for one short line that helps, e.g. "Keep \
going. This thread is new to the room." Never name people. If you mention \
another group's view, paraphrase it; never quote.
- new_turn: true if the group has switched to interviewing a different \
person in the new lines.
"""


def _fmt_items(items: list[MapItem], limit: int | None = None) -> str:
    rows = [i for i in items if not i.struck]
    if limit is not None:
        rows = rows[-limit:]
    if not rows:
        return "(none yet)"
    return "\n".join(f"{i.id} [{i.kind}{'' if i.specific else ', general'}] {i.text}" for i in rows)


def _fmt_lines(lines: list[Utterance]) -> str:
    return "\n".join(f"{u.id} @{u.t / 60:5.1f}m {u.speaker + ': ' if u.speaker else ''}{u.text}" for u in lines) or "(none)"


def build_coach_prompt(
    cfg: EventConfig,
    group: GroupState,
    group_size: int | None,
    now: float,
    context: list[Utterance],
    new: list[Utterance],
    own_items: list[MapItem],
    other_items: list[MapItem],
) -> str:
    q = group.questions
    turn_min = (now - group.turn_started_t) / 60
    share = f"{cfg.minutes / group_size:.0f}" if group_size else "unknown"
    hint = cfg.group_hints.get(group.code, "")
    return f"""\
<event>
{cfg.title}
Scenario: {cfg.scenario}
Goal: {cfg.goal}
Opening questions: {" | ".join(cfg.opening_questions)}
Fallback questions: {" | ".join(cfg.fallback_questions)}
Coverage lenses (weak hint only): {", ".join(cfg.lenses) or "none"}
</event>
{f"<group_hint>{hint}</group_hint>" if hint else ""}
<time>
Elapsed: {now / 60:.1f} of {cfg.minutes} minutes. This interviewee so far: {turn_min:.1f} minutes. Fair share per person: {share} minutes.
</time>
<documents>
{cfg.document_text() or "(none)"}
</documents>
<this_group_map>
{_fmt_items(own_items)}
</this_group_map>
<other_groups_map>
{_fmt_items(other_items, limit=80)}
</other_groups_map>
<phone_now>
current: {q.current.text}
next_in_sequence: {q.next_in_sequence.text}
offshoots: {" | ".join(o.text for o in q.offshoots) or "(none)"}
redirect: {q.redirect.text}
</phone_now>
<already_asked>
{chr(10).join(group.asked) or "(none)"}
</already_asked>
<earlier_lines>
{_fmt_lines(context)}
</earlier_lines>
<new_lines>
{_fmt_lines(new)}
</new_lines>"""


def run_coach(llm: LLM, cfg: EventConfig, **kwargs) -> CoachUpdate:
    prompt = build_coach_prompt(cfg, **kwargs)
    return llm.parse(purpose="coach", system=COACH_SYSTEM, user=prompt, schema=CoachUpdate, effort=cfg.models.coach_effort)
