"""Recordings in a folder → one transcript per file → insights.

For recordings made some other way than the live recording pages, such as
a phone at each table. Everything is written next to the recordings:

- NAME.txt: the whole-file transcript. A file that already has one is not
  transcribed again, so a transcript made with any other tool can be
  dropped in instead of the audio.
- NAME.insights.json: insights from each chunk of the transcript, in order.
  A re-run continues from the first chunk not yet done.
- insights.md: every file's insights, sorted under the event's steps.

The file name labels the insights ("A.m4a" → "A").
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from .config import EventConfig
from .llm import LLM, TRANSIENT_ERRORS
from .models import ItemKind
from .record.extract import OTHER
from .record.extract import SYSTEM as LIVE_SYSTEM
from .record.spool import AUDIO_EXTS, atomic_write, write_json

CHUNK_CHARS = 8000  # about 8 minutes of talk
CONTEXT_INSIGHTS = 30
DIGEST = "insights.md"

# The live prompt, minus the line about clip ids: a whole-file transcript has none.
SYSTEM = "\n".join(line for line in LIVE_SYSTEM.splitlines() if not line.startswith("- clips:"))


class Insight(BaseModel):
    text: str = Field(description="One sentence, close to the speaker's own words; no names")
    kind: ItemKind
    step: str = Field(description="The event step this belongs to, exactly as listed, or 'other'")
    specific: bool = Field(description="True if it comes with a concrete case, tool, number or cue")


class InsightBatch(BaseModel):
    insights: list[Insight]


# ------------------------------------------------------------------ files


def recordings(folder: Path) -> dict[str, Path | None]:
    """Each recording's name → its audio file, or None for a transcript
    with no audio beside it."""
    out: dict[str, Path | None] = {}
    for p in sorted(folder.iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        if p.suffix.lower() in AUDIO_EXTS:
            out[p.stem] = p
        elif p.suffix == ".txt":
            out.setdefault(p.stem, None)
    return out


def transcript_path(folder: Path, name: str) -> Path:
    return folder / f"{name}.txt"


def insights_path(folder: Path, name: str) -> Path:
    return folder / f"{name}.insights.json"


# ------------------------------------------------------------------ transcribe


def transcribe_all(folder: Path, transcribe, log=print) -> list[str]:
    """Transcribe each recording without a transcript, one at a time.
    transcribe(path) returns the text. Returns names that failed."""
    failed = []
    for name, audio in recordings(folder).items():
        out = transcript_path(folder, name)
        if out.exists() or audio is None:
            continue
        log(f"transcribing {audio.name}…")
        started = time.monotonic()
        try:
            text = transcribe(audio)
        except (OSError, RuntimeError) as e:  # unreadable file; ffmpeg can't decode it
            log(f"  {audio.name} failed: {e}")
            failed.append(name)
            continue
        atomic_write(out, text + "\n")
        log(f"  {len(text):,} characters in {time.monotonic() - started:.0f} s")
    return failed


# ------------------------------------------------------------------ extract


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split at sentence ends into pieces of at most about size characters."""
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        while len(sentence) > size:  # no sentence breaks for a long stretch
            if cur:
                out.append(cur)
                cur = ""
            out.append(sentence[:size])
            sentence = sentence[size:]
        if cur and len(cur) + 1 + len(sentence) > size:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sentence}".strip()
    if cur:
        out.append(cur)
    return out


def build_prompt(cfg: EventConfig, name: str, part: int, total: int, text: str, earlier: list[str]) -> str:
    steps = "\n".join(f"- {s}" for s in cfg.steps or cfg.lenses) or "(none listed)"
    prior = "\n".join(f"- {t}" for t in earlier[-CONTEXT_INSIGHTS:]) or "(none yet)"
    return f"""\
<event>
{cfg.title}
Scenario: {cfg.scenario}
Goal: {cfg.goal}
</event>
<steps>
{steps}
</steps>
<group>{name}, part {part} of {total}</group>
<earlier_insights>
{prior}
</earlier_insights>
<new_transcript>
{text}
</new_transcript>"""


def extract_file(llm: LLM, cfg: EventConfig, folder: Path, name: str, log=print) -> bool:
    """Extract every chunk of one transcript not yet done, in order.
    Returns True when all chunks are done."""
    text = transcript_path(folder, name).read_text()
    digest = hashlib.sha256(text.encode()).hexdigest()
    parts = chunks(text)
    out = insights_path(folder, name)
    rec = json.loads(out.read_text()) if out.exists() else {}
    if rec.get("transcript_sha256") != digest:
        rec = {"name": name, "transcript_sha256": digest, "chunks": []}  # new or edited transcript: start over
    steps = set(cfg.steps or cfg.lenses)
    while len(rec["chunks"]) < len(parts):
        i = len(rec["chunks"])
        earlier = [x["text"] for c in rec["chunks"] for x in c["insights"]]
        started = time.monotonic()
        try:
            batch = llm.parse(
                purpose="extract",
                system=SYSTEM,
                user=build_prompt(cfg, name, i + 1, len(parts), parts[i], earlier),
                schema=InsightBatch,
                effort=cfg.models.coach_effort,
            )
        except TRANSIENT_ERRORS as e:
            log(f"{name} part {i + 1}/{len(parts)} failed: {type(e).__name__}: {str(e)[:200]}")
            return False
        insights = []
        for x in batch.insights:
            d = x.model_dump()
            if d["step"] not in steps:
                d["step"] = OTHER
            insights.append(d)
        rec["chunks"].append({"chars": len(parts[i]), "seconds": round(time.monotonic() - started, 1), "insights": insights})
        write_json(out, rec)
        log(f"{name} part {i + 1}/{len(parts)}: {len(insights)} insights, {time.monotonic() - started:.0f} s")
    return True


def write_digest(cfg: EventConfig, folder: Path, incomplete: list[str]) -> Path:
    steps = list(cfg.steps or cfg.lenses) + [OTHER]
    by_step: dict[str, list[str]] = {s: [] for s in steps}
    total = 0
    for name in recordings(folder):
        p = insights_path(folder, name)
        if not p.exists():
            continue
        for c in json.loads(p.read_text())["chunks"]:
            for i in c["insights"]:
                mark = "" if i.get("specific") else " (general)"
                by_step.setdefault(i["step"], []).append(f"- {i['text']} _({name}, {i['kind'].replace('_', ' ')}{mark})_")
                total += 1
    out = [
        f"# Insights: {cfg.title}",
        "",
        f"_{total} insights. Updated {time.strftime('%Y-%m-%d %H:%M')}. Each line says which recording it came from and what kind of point it is._",
        "",
    ]
    for s in steps:
        if by_step.get(s):
            out += [f"## {s.capitalize() if s == OTHER else s}", ""] + by_step[s] + [""]
    if incomplete:
        out += ["## Not finished", "", "Run the same command again to continue these:", ""] + [f"- {n}" for n in incomplete] + [""]
    path = folder / DIGEST
    atomic_write(path, "\n".join(out))
    return path


def process(cfg: EventConfig, folder: Path, transcribe, llm: LLM, log=print) -> bool:
    """Transcribe, then extract every transcript, one thread per file.
    Returns True if everything is done."""
    names = list(recordings(folder))
    if not names:
        log(f"No recordings or transcripts in {folder}.")
        return False
    failed = transcribe_all(folder, transcribe, log)
    ready = [n for n in names if transcript_path(folder, n).exists()]
    with ThreadPoolExecutor(max_workers=max(1, len(ready))) as pool:
        done = dict(zip(ready, pool.map(lambda n: extract_file(llm, cfg, folder, n, log), ready)))
    incomplete = sorted(set(failed) | {n for n, ok in done.items() if not ok})
    path = write_digest(cfg, folder, incomplete)
    log(f"Insights: {path}")
    if incomplete:
        log(f"Not finished: {', '.join(incomplete)}. Run the same command again to continue.")
    return not incomplete
