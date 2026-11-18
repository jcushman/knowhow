"""Operations around the pipeline: rehearsing with a recording, closing out
an event, and keeping the workers running."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from ..config import EventConfig
from ..llm import LLM
from . import extract
from .spool import Spool
from .status import spool_status

# ------------------------------------------------------------------ replay


def split_audio(src: Path, out_dir: Path, segment_s: int) -> list[Path]:
    """Cut a recording into self-contained mono 16 kHz WAV clips with ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-f", "segment", "-segment_time", str(segment_s), "-c:a", "pcm_s16le", str(out_dir / "%05d.wav")],
        check=True,
    )
    return sorted(out_dir.glob("*.wav"))


Post = Callable[[str, bytes, dict], int]


def httpx_post(url: str, data: bytes, headers: dict) -> int:
    return httpx.post(url, content=data, headers=headers, timeout=60).status_code


def replay(
    base_url: str, code: str, src: Path, segment_s: int = 15, speed: float = 1.0,
    device: str = "replay", post: Post = httpx_post, log=print,
) -> int:
    """Upload a recording to a running capture server as if a browser were
    recording it. speed 1 is real time; 0 uploads as fast as possible."""
    with tempfile.TemporaryDirectory() as tmp:
        clips = split_audio(src, Path(tmp), segment_s)
        t0 = int(time.time() * 1000)
        for i, clip in enumerate(clips):
            if speed > 0 and i:
                time.sleep(segment_s / speed)
            url = f"{base_url.rstrip('/')}/api/r/{code}/clip?device={device}&seq={i}&start={t0 + i * segment_s * 1000}&duration={segment_s * 1000}"
            status = post(url, clip.read_bytes(), {"content-type": "audio/wav"})
            log(f"clip {i + 1}/{len(clips)} → {status}")
            if status != 200:
                raise RuntimeError(f"server answered {status} for clip {i}")
    return len(clips)


# ------------------------------------------------------------------ closeout


def closeout(cfg: EventConfig, spool: Spool, llm: LLM, model: str, yes: bool = False, ask=input, log=print) -> bool:
    """End of event: extract everything still pending, then delete the audio.
    Transcripts and insights stay until `knowhow purge` after sign-off."""
    st = spool_status(cfg, spool)
    waiting = {c: g["waiting_for_transcription"] for c, g in st["groups"].items() if g["waiting_for_transcription"]}
    if waiting:
        log(f"Clips still waiting for transcription: {waiting}. Let the transcriber finish, then run closeout again.")
        return False
    while extract.run_once(llm, cfg, spool, model, force=True, log=log):
        pass
    extract.write_digest(cfg, spool)
    left = {c: len(extract.pending(spool, c)) for c in (g.code for g in cfg.groups)}
    left = {c: n for c, n in left.items() if n}
    if left:
        log(f"Some transcript could not be extracted: {left}. Fix the model and run closeout again; audio is kept until then.")
        return False
    audio = spool.root / "audio"
    if not audio.exists():
        log("No audio to delete.")
        return True
    n = sum(1 for p in audio.rglob("*") if p.is_file())
    log(f"All transcripts are extracted. Insights: {spool.digest}")
    if not yes and ask(f"Delete {n} audio files under {audio}? Type 'delete' to confirm: ").strip() != "delete":
        log("Audio kept.")
        return False
    shutil.rmtree(audio)
    log(f"Deleted {audio}. Transcripts remain in {spool.root / 'text'} until `knowhow purge --data {spool.root}`.")
    return True


# ------------------------------------------------------------------ supervisor


def supervise(commands: dict[str, list[str]], log=print) -> None:
    """Run each worker as its own process and restart any that exits, so a
    crash in one stage never stops the others."""
    procs: dict[str, subprocess.Popen] = {}
    restarts: dict[str, int] = {name: 0 for name in commands}
    next_start: dict[str, float] = {name: 0.0 for name in commands}
    try:
        while True:
            now = time.monotonic()
            for name, argv in commands.items():
                p = procs.get(name)
                if p is not None and p.poll() is None:
                    continue
                if p is not None:
                    restarts[name] += 1
                    delay = min(2 ** restarts[name], 30)
                    log(f"[run] {name} exited with {p.returncode}; restarting in {delay} s")
                    next_start[name] = now + delay
                    procs.pop(name)
                    continue
                if now >= next_start[name]:
                    log(f"[run] starting {name}")
                    procs[name] = subprocess.Popen(argv)
            time.sleep(1)
    except KeyboardInterrupt:
        log("[run] stopping")
        for p in procs.values():
            p.terminate()
        for p in procs.values():
            p.wait(timeout=10)


def worker_commands(args: list[str]) -> dict[str, list[str]]:
    base = [sys.executable, "-u", "-m", "knowhow.cli", "rec"]
    return {name: base + [name] + args for name in ("serve", "transcribe", "extract")}
