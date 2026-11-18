"""Transcribe worker: turns each audio clip into <clip>.json in the text spool.

Any transcriber can stand in for this worker, as long as it writes
text/<group>/<clip>.txt (plain text) or <clip>.json (with a "text" field)
for each clip. This worker offers two built-in ways:

- an ASR backend from knowhow.asr (mlx-whisper on the organizer's Mac), or
- a shell command that takes {audio} and prints the transcript to stdout.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..asr import ASR
from .spool import MAX_ATTEMPTS, Spool, attempts, begin_attempt, record_error, write_json

COMMAND_TIMEOUT_S = 300

Transcribe = Callable[[Path], str]


def asr_transcriber(asr: ASR) -> Transcribe:
    return lambda path: asr.transcribe(path.read_bytes(), path.suffix)


def command_transcriber(template: str) -> Transcribe:
    """template example: "my-asr --file {audio}". stdout is the transcript."""

    def run(path: Path) -> str:
        args = [a.replace("{audio}", str(path)) for a in shlex.split(template)]
        res = subprocess.run(args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, check=True)
        return res.stdout.strip()

    return run


def fake_transcriber() -> Transcribe:
    """For dry runs: a fixed sentence per clip, so later stages have text."""
    return lambda path: (
        f"In clip {path.stem[-5:]} we talked about checking a copy against the list of pages we expected, "
        "and the time a crawl reported success while a whole section was missing."
    )


# A command that fails or hangs, a file that can't be read, and (from
# whisper-style backends) RuntimeError when ffmpeg can't decode a clip.
EXPECTED_ERRORS = (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError)


def run_once(spool: Spool, groups: list[str], transcribe: Transcribe, name: str, log=print) -> int:
    """Transcribe every clip that has no transcript yet. Returns how many
    were written. An attempt is recorded before each try, so a clip that
    crashes the worker is skipped after MAX_ATTEMPTS restarts."""
    done = 0
    for group in groups:
        for clip in spool.audio_clips(group):
            if spool.text_file(group, clip.stem) is not None:
                continue
            if attempts(spool, "transcribe", group, clip.stem) >= MAX_ATTEMPTS:
                continue
            begin_attempt(spool, "transcribe", group, clip.stem)
            started = time.monotonic()
            try:
                text = transcribe(clip)
            except EXPECTED_ERRORS as e:
                record_error(spool, "transcribe", group, clip.stem, f"{type(e).__name__}: {e}")
                log(f"transcribe {group}/{clip.name} failed: {e}")
                continue
            meta = spool.clip_meta(clip)
            write_json(
                spool.text_dir(group) / f"{clip.stem}.json",
                {
                    "clip": clip.stem,
                    "group": group,
                    "start_ms": meta.get("start_ms"),
                    "text": text,
                    "asr": name,
                    "seconds": round(time.monotonic() - started, 2),
                    "transcribed": time.time(),
                },
            )
            done += 1
            log(f"transcribed {group}/{clip.name} ({len(text)} chars, {time.monotonic() - started:.1f} s)")
    return done


def loop(spool: Spool, groups: list[str], transcribe: Transcribe, name: str, poll_s: float = 2.0, log=print) -> None:
    while True:
        if run_once(spool, groups, transcribe, name, log) == 0:
            time.sleep(poll_s)
