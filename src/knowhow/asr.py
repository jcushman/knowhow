"""Speech recognition on the organizer's machine.

Phones upload short self-contained audio segments (webm/opus or mp4/aac,
depending on the browser). Each segment is written to a temporary file,
transcribed, and deleted. Audio is never kept.

`MlxWhisperASR` needs the optional `asr` extra (`uv sync --extra asr`) and
ffmpeg on PATH. `NullASR` drops audio, for testing the rest of the system.
"""

from __future__ import annotations

import io
import tempfile
import threading
import wave
from typing import Protocol


class ASR(Protocol):
    name: str

    def transcribe(self, audio: bytes, suffix: str) -> str: ...

    def warm(self) -> None:
        """Load the model before the first real segment arrives."""


class NullASR:
    name = "none"

    def warm(self) -> None:
        pass

    def transcribe(self, audio: bytes, suffix: str) -> str:
        return ""


class MlxWhisperASR:
    name = "mlx-whisper"

    def __init__(self, model: str = "mlx-community/whisper-large-v3-turbo", vocabulary: list[str] | None = None):
        import mlx_whisper  # optional dependency

        self._mlx_whisper = mlx_whisper
        self.model = model
        self._lock = threading.Lock()
        # Whisper's initial prompt biases spelling of domain terms.
        self.prompt = ("Terms: " + ", ".join(vocabulary) + ".") if vocabulary else None

    def transcribe(self, audio: bytes, suffix: str) -> str:
        # One transcription at a time: segments from several groups share the GPU.
        with self._lock, tempfile.NamedTemporaryFile(suffix=suffix) as f:
            f.write(audio)
            f.flush()
            result = self._mlx_whisper.transcribe(
                f.name,
                path_or_hf_repo=self.model,
                initial_prompt=self.prompt,
                condition_on_previous_text=False,
            )
        return result["text"].strip()

    def warm(self) -> None:
        # One second of silence makes mlx-whisper download and load the model.
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        self.transcribe(buf.getvalue(), ".wav")


def suffix_for(content_type: str) -> str:
    ct = content_type.split(";")[0].strip().lower()
    return {"audio/webm": ".webm", "audio/mp4": ".mp4", "audio/ogg": ".ogg", "audio/wav": ".wav"}.get(ct, ".bin")


def make_asr(kind: str, vocabulary: list[str]) -> ASR:
    if kind == "none":
        return NullASR()
    if kind == "mlx-whisper":
        return MlxWhisperASR(vocabulary=vocabulary)
    raise ValueError(f"unknown ASR backend {kind!r}")

