"""Pipeline status from the spool alone: what has arrived, what has been
transcribed and extracted, and what is stuck. No transcript text."""

from __future__ import annotations

import json
import time

from ..config import EventConfig
from .spool import MAX_ATTEMPTS, Spool, errors

QUIET_S = 75.0  # browsers upload a clip every ~30 s while recording
STUCK_S = 300.0  # a waiting clip this old means a stage is not keeping up


def group_status(spool: Spool, code: str, now: float) -> dict:
    from .extract import covered_clips, pending, window_files

    clips = spool.audio_clips(code)
    metas = [spool.clip_meta(c) for c in clips]
    last_received = max((m.get("received", 0) for m in metas), default=None)
    text_names = set(spool.text_names(code))
    waiting = [c for c in clips if c.stem not in text_names and not spool.transcribe_failed(code, c.stem)]
    t_failed = [c.stem for c in clips if spool.transcribe_failed(code, c.stem)]
    covered = covered_clips(spool, code)
    windows = [json.loads(p.read_text()) for p in window_files(spool, code)]
    n_insights = sum(len(w.get("insights", [])) for w in windows)
    pend = pending(spool, code)
    oldest_wait = None
    if waiting:
        oldest_wait = now - min(spool.clip_meta(c).get("received", now) for c in waiting)
    ex_err = errors(spool, "extract", code)

    if not clips and not text_names:
        state, headline = "idle", "No audio yet"
    elif t_failed:
        state, headline = "bad", f"{len(t_failed)} clip(s) could not be transcribed"
    elif oldest_wait is not None and oldest_wait > STUCK_S:
        state, headline = "warn", f"Transcription is {int(oldest_wait)} s behind"
    elif ex_err and pend:
        state, headline = "warn", f"Extraction failing: {next(iter(ex_err.values()))['last_error'][:120]}"
    elif last_received is not None and now - last_received > QUIET_S:
        state, headline = "idle", f"No new audio for {int(now - last_received)} s"
    else:
        state, headline = "ok", "Recording and processing"
    return {
        "state": state,
        "headline": headline,
        "clips": len(clips),
        "seconds_since_last_clip": None if last_received is None else round(now - last_received),
        "devices": sorted({m.get("device", "") for m in metas if m.get("device")}),
        "transcribed": len(text_names),
        "waiting_for_transcription": len(waiting),
        "transcription_failed": t_failed,
        "extracted_clips": len(covered),
        "waiting_for_extraction": len(pend),
        "windows": len(windows),
        "failed_windows": sum(1 for w in windows if w.get("failed")),
        "insights": n_insights,
        "extract_errors": {k: v["last_error"] for k, v in ex_err.items() if v["attempts"] <= MAX_ATTEMPTS},
    }


def spool_status(cfg: EventConfig, spool: Spool) -> dict:
    now = time.time()
    return {
        "title": cfg.title,
        "time": now,
        "groups": {g.code: {"label": g.label, **group_status(spool, g.code, now)} for g in cfg.groups},
        "digest_updated": spool.digest.stat().st_mtime if spool.digest.exists() else None,
    }


def format_status(st: dict) -> str:
    lines = [st["title"]]
    for code, g in st["groups"].items():
        last = "never" if g["seconds_since_last_clip"] is None else f"{g['seconds_since_last_clip']} s ago"
        lines.append(
            f"  {g['label']} ({code}) [{g['state']}] {g['headline']}\n"
            f"    clips {g['clips']} (last {last}) · transcribed {g['transcribed']} (waiting {g['waiting_for_transcription']})"
            f" · extracted {g['extracted_clips']} (waiting {g['waiting_for_extraction']}) · insights {g['insights']}"
        )
    return "\n".join(lines)
