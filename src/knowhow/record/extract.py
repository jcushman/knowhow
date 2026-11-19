"""Extract worker: reads each group's transcripts in order and, every few
minutes of new talk, asks a model for the insights in them.

A window of clips is "covered" once insights/<group>/<first clip>.json
exists. That file is written only after the model's answer is checked, so a
failed call leaves the window uncovered and the next pass retries it.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from ..config import EventConfig
from ..llm import LLM, TRANSIENT_ERRORS
from ..models import ItemKind
from .spool import MAX_ATTEMPTS, Spool, atomic_write, attempts, begin_attempt, forget_attempt, record_error, write_json

MIN_CHARS = 1200  # about a minute and a half of talk
MAX_WAIT_S = 150.0  # extract a short window anyway once its first line is this old
MAX_WINDOW_CHARS = 8000
CONTEXT_INSIGHTS = 30
OTHER = "other"


class Insight(BaseModel):
    text: str = Field(description="One sentence, close to the speaker's own words; no names")
    kind: ItemKind
    step: str = Field(description="The event step this belongs to, exactly as listed, or 'other'")
    specific: bool = Field(description="True if it comes with a concrete case, tool, number or cue")
    clips: list[str] = Field(description="Ids of the clips it comes from")


class InsightBatch(BaseModel):
    insights: list[Insight]


SYSTEM = """\
You read a live transcript from one small group at a working session and \
pull out the practical knowledge in it. People are talking through a series \
of steps together and sharing what they know from experience.

Return insights from the NEW transcript only:
- One insight per distinct point: a lesson, a case someone lived through, a \
cue that tells you something is wrong, a reason, something people needed, \
something that goes wrong, or an open question the group raised. Use kind \
for which of these it is.
- Keep each insight close to the speaker's own words, in one sentence. Do \
not add facts, advice or polish that nobody said. Keep concrete details: \
tools, numbers, file types, what happened.
- specific is true when the point comes with a concrete case, tool, number \
or cue; false for a general remark.
- step: the step of the session it belongs to, copied exactly from the list, \
or "other".
- clips: the ids of the clips the point comes from.
- Never include names of people or anything that identifies who said it. \
Names of tools, organizations, datasets and places are fine.
- Skip logistics, chit-chat and reading the worksheet aloud. If the new \
transcript has nothing substantive, return no insights.
- Speech recognition makes mistakes. Where a word is clearly misheard, use \
the likely word; where you cannot tell, leave the point out.
- Do not repeat an insight already listed under <earlier_insights>.
"""


# ------------------------------------------------------------------ coverage


def window_files(spool: Spool, group: str) -> list[Path]:
    d = spool.insights_dir(group)
    return sorted(d.glob("*.json")) if d.exists() else []


def covered_clips(spool: Spool, group: str) -> set[str]:
    out: set[str] = set()
    for p in window_files(spool, group):
        out.update(json.loads(p.read_text())["clips"])
    return out


def pending(spool: Spool, group: str) -> list[tuple[str, str, float]]:
    """Uncovered clips in order as (name, text, transcript mtime), stopping at
    the first clip that is still waiting for its transcript. A clip whose
    transcription failed for good counts as silence."""
    covered = covered_clips(spool, group)
    names = sorted({p.stem for p in spool.audio_clips(group)} | set(spool.text_names(group)))
    out = []
    for name in names:
        if name in covered:
            continue
        tf = spool.text_file(group, name)
        if tf is not None:
            out.append((name, spool.read_text(tf), tf.stat().st_mtime))
        elif spool.transcribe_failed(group, name):
            out.append((name, "", time.time()))
        else:
            break
    return out


def next_window(spool: Spool, group: str, now: float, force: bool) -> list[tuple[str, str, float]]:
    clips = pending(spool, group)
    if not clips:
        return []
    window, chars = [], 0
    for c in clips:
        if window and chars + len(c[1]) > MAX_WINDOW_CHARS:
            break
        window.append(c)
        chars += len(c[1])
    if force or chars >= MIN_CHARS or len(window) < len(clips):
        return window
    if now - window[0][2] >= MAX_WAIT_S:
        return window
    return []


# ------------------------------------------------------------------ extract


def earlier_insights(spool: Spool, group: str) -> list[str]:
    texts = []
    for p in window_files(spool, group):
        texts += [i["text"] for i in json.loads(p.read_text()).get("insights", [])]
    return texts[-CONTEXT_INSIGHTS:]


def build_prompt(cfg: EventConfig, group_label: str, window: list[tuple[str, str, float]], earlier: list[str]) -> str:
    steps = "\n".join(f"- {s}" for s in cfg.steps or cfg.lenses) or "(none listed)"
    lines = "\n".join(f"[{name}] {text}" for name, text, _ in window if text)
    prior = "\n".join(f"- {t}" for t in earlier) or "(none yet)"
    return f"""\
<event>
{cfg.title}
Scenario: {cfg.scenario}
Goal: {cfg.goal}
</event>
<steps>
{steps}
</steps>
<group>{group_label}</group>
<earlier_insights>
{prior}
</earlier_insights>
<new_transcript>
{lines}
</new_transcript>"""


def extract_window(
    llm: LLM, cfg: EventConfig, spool: Spool, group: str, window: list[tuple[str, str, float]], model: str, log=print
) -> bool:
    """Extract one window and write its file. Returns False if the model call
    failed and the window stays uncovered."""
    first = window[0][0]
    names = [n for n, _, _ in window]
    out_path = spool.insights_dir(group) / f"{first}.json"
    record = {"window": first, "group": group, "clips": names, "model": model, "extracted": None, "insights": []}
    if not any(t for _, t, _ in window):
        record.update(extracted=time.time(), note="no speech")
        write_json(out_path, record)
        return True
    n = begin_attempt(spool, "extract", group, first)
    if n > MAX_ATTEMPTS:
        record.update(extracted=time.time(), failed=True, note=f"gave up after {MAX_ATTEMPTS} attempts")
        write_json(out_path, record)
        log(f"extract {group}/{first}: gave up; transcripts are kept for a later re-run")
        return True
    label = next((g.label for g in cfg.groups if g.code == group), group)
    started = time.monotonic()
    try:
        batch = llm.parse(
            purpose="extract",
            system=SYSTEM,
            user=build_prompt(cfg, label, window, earlier_insights(spool, group)),
            schema=InsightBatch,
            effort=cfg.models.coach_effort,
        )
    except TRANSIENT_ERRORS as e:
        record_error(spool, "extract", group, first, f"{type(e).__name__}: {e}")
        if isinstance(e, httpx.TransportError):
            # The model server couldn't be reached: that says nothing about this
            # window, so it doesn't count toward giving up on it.
            forget_attempt(spool, "extract", group, first)
        log(f"extract {group}/{first} failed (attempt {n}): {str(e)[:200]}")
        return False
    steps = set(cfg.steps or cfg.lenses)
    valid = set(names)
    insights = []
    for i in batch.insights:
        d = i.model_dump()
        if d["step"] not in steps:
            d["step"] = OTHER
        d["clips"] = [c for c in d["clips"] if c in valid] or [first]
        insights.append(d)
    record.update(extracted=time.time(), seconds=round(time.monotonic() - started, 1), insights=insights)
    write_json(out_path, record)
    log(f"extracted {group}/{first}: {len(names)} clips, {len(insights)} insights, {time.monotonic() - started:.1f} s")
    return True


# ------------------------------------------------------------------ digest


def write_digest(cfg: EventConfig, spool: Spool) -> None:
    steps = list(cfg.steps or cfg.lenses) + [OTHER]
    by_step: dict[str, list[str]] = {s: [] for s in steps}
    failed, total = [], 0
    for g in cfg.groups:
        for p in window_files(spool, g.code):
            rec = json.loads(p.read_text())
            if rec.get("failed"):
                failed.append(f"{g.label} {rec['window']}")
            for i in rec.get("insights", []):
                mark = "" if i.get("specific") else " (general)"
                by_step.setdefault(i["step"], []).append(f"- {i['text']} _({g.label}, {i['kind'].replace('_', ' ')}{mark})_")
                total += 1
    out = [
        f"# Insights: {cfg.title}",
        "",
        f"_{total} insights. Updated {time.strftime('%H:%M:%S')}. Each line says which group it came from and what kind of point it is._",
        "",
    ]
    for s in steps:
        if not by_step.get(s):
            continue
        out += [f"## {s.capitalize() if s == OTHER else s}", ""] + by_step[s] + [""]
    if failed:
        out += ["## Windows not extracted", "", "Transcripts for these are kept; re-run extraction to retry.", ""]
        out += [f"- {f}" for f in failed] + [""]
    atomic_write(spool.digest, "\n".join(out))


# ------------------------------------------------------------------ loop


def run_once(llm: LLM, cfg: EventConfig, spool: Spool, model: str, force: bool = False, log=print) -> int:
    """Extract every window that is ready. Groups run in parallel (a model
    server such as vLLM handles several requests at nearly the cost of one);
    each group's windows go in order. Returns the number written."""

    def one_group(code: str) -> int:
        n = 0
        while True:
            window = next_window(spool, code, time.time(), force)
            if not window or not extract_window(llm, cfg, spool, code, window, model, log):
                return n
            n += 1

    with ThreadPoolExecutor(max_workers=max(1, len(cfg.groups))) as pool:
        done = sum(pool.map(one_group, [g.code for g in cfg.groups]))
    if done:
        write_digest(cfg, spool)
    return done


def loop(llm: LLM, cfg: EventConfig, spool: Spool, model: str, poll_s: float = 5.0, log=print) -> None:
    write_digest(cfg, spool)
    while True:
        run_once(llm, cfg, spool, model, log=log)
        time.sleep(poll_s)
