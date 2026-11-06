from pathlib import Path

from starlette.testclient import TestClient

from knowhow.config import load_event
from knowhow.engine import Session
from knowhow.llm import LLMError
from knowhow.server import App
from knowhow.sim.fake import ScriptedLLM, fake_llm

EVENT = Path(__file__).parent.parent / "events" / "bread-class.yaml"


class WordsASR:
    name = "test"

    def __init__(self, text: str):
        self.text = text

    def warm(self) -> None:
        pass

    def transcribe(self, audio: bytes, suffix: str) -> str:
        return self.text


def stages(health: dict, code: str) -> dict[str, str]:
    return {s["name"]: s["state"] for s in health["groups"][code]["stages"]}


def test_audio_and_transcription_show_as_working(tmp_path):
    session = Session(load_event(EVENT), fake_llm(), fake_llm(), tmp_path / "s")
    app = App(session, WordsASR("we weighed the flour every time"), admin_key="k")
    with TestClient(app.starlette) as c:
        c.get("/api/g/C-4817/state?device=d1")
        c.post("/api/g/C-4817/listen", json={"device": "d1"})
        c.post("/api/g/C-4817/audio?device=d1", content=b"x" * 3000, headers={"content-type": "audio/webm"})
        h = c.get("/api/admin/overview?key=k").json()["health"]
    st = stages(h, "C-4817")
    assert st["Phone"] == "ok" and st["Audio"] == "ok" and st["Transcription"] == "ok"
    assert h["groups"]["C-4817"]["words_per_min"][-1] == 6
    assert stages(h, "A-2291")["Phone"] == "idle"
    assert h["asr"]["ready"] and h["asr"]["queue"] == 0


def test_silent_transcripts_and_stale_audio_are_flagged(tmp_path):
    session = Session(load_event(EVENT), fake_llm(), fake_llm(), tmp_path / "s")
    app = App(session, WordsASR(""), admin_key="k")
    h = app.health
    session.claim_listener("B-5530", "d1", 0)
    for t in (10, 25, 40):
        h.segment_started("B-5530", t)
        h.segment_finished("B-5530", t, t + 2, "")
    assert stages(h.report(session, 45), "B-5530")["Transcription"] == "warn"
    later = h.report(session, 200)["groups"]["B-5530"]
    assert later["state"] == "bad" and later["headline"].startswith("Phone")


def test_failed_coach_pass_is_reported_and_lines_stay_pending(tmp_path):
    def refuse(purpose, system, user, schema):
        raise LLMError("coach: refused")

    session = Session(load_event(EVENT), ScriptedLLM(refuse), fake_llm(), tmp_path / "s")
    app = App(session, WordsASR(""), admin_key="k")
    session.ingest("C-4817", "We proofed the shaped loaves overnight in the fridge so baking fit the morning. " * 4, t=5)
    app._coach("C-4817", 90)
    st = app.health.report(session, 95)["groups"]["C-4817"]
    assert st["state"] == "bad" and "refused" in st["stages"][3]["detail"]
    assert session.pending("C-4817")
