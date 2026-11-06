"""Post-session synthesis: playbook draft plus a full concept map.

The model writes the playbook and groups items into threads. Accounting
(which items were used where, group counts, unused items) happens in code so
nothing can disappear silently.
"""

from __future__ import annotations

import re

from collections import defaultdict

from .config import EventConfig
from .llm import LLM
from .models import MapItem, Synthesis, Utterance

SYNTH_SYSTEM = """\
You turn a knowledge-elicitation session into a draft playbook that the \
participants will read and edit together after the session. You get every \
map item captured during the session (with ids) and the full transcript of \
each group, from speech recognition, with no names.

Write a practical playbook for the stated goal.
- Choose the structure from the material itself. In structure_note, say \
how it is organized and name one alternative.
- Every claim cites the item ids it rests on. Do not state anything the \
items and transcripts do not support. Prefer specific procedures, cues, \
numbers and cases over general advice; when a claim rests only on general \
remarks, keep it but word it modestly.
- Keep disagreements as disagreements: list them, and add a caveat to any \
claim they affect. Do not average opposing views into one.
- Minority and one-group points that are specific belong in the playbook if \
they are useful; distinctive knowledge matters more than consensus.
- open_questions: unresolved questions worth the room's discussion time, \
most important first.
- threads: group ALL item ids into threads labelled in the participants' \
own terms, for a concept map. Threads may be small. An item may appear in \
at most one thread.
- No names or identifying details of participants. Plain prose; claims are \
one to three sentences each.
"""


def build_synth_prompt(cfg: EventConfig, items: list[MapItem], utterances: list[Utterance]) -> str:
    by_group: dict[str, list[Utterance]] = defaultdict(list)
    for u in utterances:
        by_group[u.group].append(u)
    transcripts = "\n\n".join(
        f'<transcript group="{g}">\n' + "\n".join(f"{u.id} {u.text}" for u in us) + "\n</transcript>"
        for g, us in sorted(by_group.items())
    )
    item_lines = "\n".join(
        f"{i.id} (group {i.group}) [{i.kind}{'' if i.specific else ', general'}] {i.text}"
        + (f" links: {', '.join(f'{l.relation} {l.target_id}' for l in i.links)}" if i.links else "")
        for i in items
        if not i.struck
    )
    return f"""\
<event>
{cfg.title}
Scenario: {cfg.scenario}
Goal: {cfg.goal}
</event>
<documents>
{cfg.document_text() or "(none)"}
</documents>
<map_items>
{item_lines}
</map_items>
{transcripts}"""


def run_synthesis(llm: LLM, cfg: EventConfig, items: list[MapItem], utterances: list[Utterance]) -> Synthesis:
    return llm.parse(
        purpose="synth",
        system=SYNTH_SYSTEM,
        user=build_synth_prompt(cfg, items, utterances),
        schema=Synthesis,
        effort=cfg.models.synth_effort,
    )


# ---------------------------------------------------------------------------
# Rendering and accounting
# ---------------------------------------------------------------------------


def _groups_label(ids: list[str], items: dict[str, MapItem]) -> str:
    groups = {items[i].group for i in ids if i in items}
    n = len(groups)
    return f"{n} group" + ("" if n == 1 else "s") if n else "no source"


def account(syn: Synthesis, items: dict[str, MapItem]) -> dict:
    """Where each item ended up, and ids the model cited that don't exist."""
    used_in: dict[str, list[str]] = defaultdict(list)
    unknown: set[str] = set()

    def note(ids: list[str], where: str) -> None:
        for i in ids:
            if i in items:
                used_in[i].append(where)
            else:
                unknown.add(i)

    for n, sec in enumerate(syn.sections, 1):
        for c in sec.claims:
            note(c.item_ids, f"§{n}")
    for d in syn.disagreements:
        note(d.item_ids, "disagreements")
    for q in syn.open_questions:
        note(q.item_ids, "open questions")
    threaded = {i for t in syn.threads for i in t.item_ids}
    live = [i for i in items.values() if not i.struck]
    return {
        "used_in": dict(used_in),
        "unused": [i.id for i in live if i.id not in used_in],
        "unthreaded": [i.id for i in live if i.id not in threaded],
        "unknown_ids": sorted(unknown),
    }


def render_playbook(syn: Synthesis, items: dict[str, MapItem], session_note: str) -> str:
    out = [f"# {syn.title}", "", f"_{session_note} {syn.structure_note} Counts show how many groups raised a point._", ""]
    for n, sec in enumerate(syn.sections, 1):
        out += [f"## {n}. {re.sub(r'^\s*\d+[.)]\s*', '', sec.title)}", ""]
        if sec.intro:
            out += [sec.intro, ""]
        for c in sec.claims:
            out.append(f"- {c.text} _({_groups_label(c.item_ids, items)})_")
            if c.caveat:
                out.append(f"  - **Caveat:** {c.caveat}")
        out.append("")
    if syn.disagreements:
        out += ["## Disagreements", ""]
        out += [f"- {d.summary} _({_groups_label(d.item_ids, items)})_" for d in syn.disagreements]
        out.append("")
    if syn.open_questions:
        out += ["## Open questions", ""]
        out += [f"{k}. {q.text}" for k, q in enumerate(syn.open_questions, 1)]
        out.append("")
    return "\n".join(out)


def render_map(syn: Synthesis, items: dict[str, MapItem], acct: dict) -> str:
    used_in = acct["used_in"]
    out = [
        "# Concept map",
        "",
        "_Every point heard, grouped into threads. 'Used' says where a point went in the playbook; "
        "'not used' points are candidates for the room to add._",
        "",
    ]

    def row(i: MapItem) -> str:
        links = "; ".join(f"{l.relation} {l.target_id}" for l in i.links) or ""
        used = ", ".join(dict.fromkeys(used_in.get(i.id, []))) or "**not used**"
        kind = i.kind.replace("_", " ") + ("" if i.specific else " (general)")
        return f"| {i.id} | {i.text} | {kind} | {links} | {used} |"

    header = ["| Id | Point | Kind | Links | Used |", "|---|---|---|---|---|"]
    for t in syn.threads:
        rows = [items[i] for i in t.item_ids if i in items and not items[i].struck]
        if not rows:
            continue
        groups = len({r.group for r in rows})
        out += [f"## {t.title}", "", f"_{groups} group{'s' if groups != 1 else ''} · {len(rows)} points_", ""]
        out += header + [row(r) for r in rows] + [""]
    loose = [items[i] for i in acct["unthreaded"]]
    if loose:
        out += ["## Not in any thread", ""] + header + [row(r) for r in loose] + [""]
    unused = [items[i] for i in acct["unused"]]
    if unused:
        out += ["## Didn't make the playbook", ""]
        out += [f"- {i.text} _({i.id})_" for i in unused]
        out.append("")
    return "\n".join(out)
