"""Folder layout and file helpers shared by every record-mode stage."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = {".webm", ".mp4", ".m4a", ".ogg", ".wav", ".mp3", ".flac", ".bin"}
MAX_ATTEMPTS = 3
DEVICE_RE = re.compile(r"[^a-z0-9]")


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write to a temporary name in the same folder, then rename into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
    if isinstance(data, str):
        tmp.write_text(data)
    else:
        tmp.write_bytes(data)
    os.replace(tmp, path)


def write_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj, indent=1, ensure_ascii=False))


def clean_device(device: str) -> str:
    return DEVICE_RE.sub("", device.lower())[:16] or "unknown"


def clip_name(start_ms: int, device: str, seq: int) -> str:
    """Names sort by start time, then device, then sequence number."""
    return f"{start_ms:013d}-{clean_device(device)}-{seq:05d}"


@dataclass(frozen=True)
class Spool:
    root: Path

    def audio_dir(self, group: str) -> Path:
        return self.root / "audio" / group

    def text_dir(self, group: str) -> Path:
        return self.root / "text" / group

    def insights_dir(self, group: str) -> Path:
        return self.root / "output" / "insights" / group

    @property
    def digest(self) -> Path:
        return self.root / "output" / "insights.md"

    def attempts_dir(self, stage: str, group: str) -> Path:
        return self.root / "state" / stage / group

    # ---------------------------------------------------------------- audio

    def audio_clips(self, group: str) -> list[Path]:
        d = self.audio_dir(group)
        if not d.exists():
            return []
        return sorted(p for p in d.iterdir() if p.suffix in AUDIO_EXTS and not p.name.startswith("."))

    def clip_meta(self, clip: Path) -> dict:
        meta = clip.with_suffix(".meta.json")
        return json.loads(meta.read_text()) if meta.exists() else {}

    # ----------------------------------------------------------------- text

    def text_file(self, group: str, name: str) -> Path | None:
        """A transcript for a clip: <name>.json with a "text" field, or a
        plain <name>.txt from any other transcriber."""
        for ext in (".json", ".txt"):
            p = self.text_dir(group) / f"{name}{ext}"
            if p.exists():
                return p
        return None

    def read_text(self, path: Path) -> str:
        if path.suffix == ".txt":
            return path.read_text().strip()
        return str(json.loads(path.read_text()).get("text", "")).strip()

    def text_names(self, group: str) -> list[str]:
        """Clip names with transcripts, including transcripts for clips whose
        audio is gone or was never in this spool."""
        d = self.text_dir(group)
        if not d.exists():
            return []
        return sorted(
            {p.stem for p in d.iterdir() if p.suffix in (".json", ".txt") and not p.name.startswith(".") and not p.name.endswith(".error.json")}
        )

    def transcribe_failed(self, group: str, name: str) -> bool:
        return attempts(self, "transcribe", group, name) >= MAX_ATTEMPTS and self.text_file(group, name) is None


# ------------------------------------------------------------------ attempts
#
# A stage records an attempt before doing risky work, so if the work crashes
# the process, the restarted worker sees the attempt and eventually skips the
# item instead of crashing on it forever.


def attempts(spool: Spool, stage: str, group: str, name: str) -> int:
    p = spool.attempts_dir(stage, group) / f"{name}.json"
    return json.loads(p.read_text())["attempts"] if p.exists() else 0


def begin_attempt(spool: Spool, stage: str, group: str, name: str) -> int:
    n = attempts(spool, stage, group, name) + 1
    write_json(spool.attempts_dir(stage, group) / f"{name}.json", {"attempts": n, "last_error": None, "t": time.time()})
    return n


def forget_attempt(spool: Spool, stage: str, group: str, name: str) -> None:
    """Undo the last begin_attempt, for failures that say nothing about the item."""
    p = spool.attempts_dir(stage, group) / f"{name}.json"
    if p.exists():
        rec = json.loads(p.read_text())
        rec["attempts"] = max(0, rec["attempts"] - 1)
        write_json(p, rec)


def record_error(spool: Spool, stage: str, group: str, name: str, error: str) -> None:
    p = spool.attempts_dir(stage, group) / f"{name}.json"
    rec = json.loads(p.read_text()) if p.exists() else {"attempts": 1}
    rec["last_error"] = error[:500]
    rec["t"] = time.time()
    write_json(p, rec)


def errors(spool: Spool, stage: str, group: str) -> dict[str, dict]:
    d = spool.attempts_dir(stage, group)
    if not d.exists():
        return {}
    out = {}
    for p in d.glob("*.json"):
        rec = json.loads(p.read_text())
        if rec.get("last_error"):
            out[p.stem] = rec
    return out
