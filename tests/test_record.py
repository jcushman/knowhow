"""Record mode: capture, transcription, extraction, status and closeout,
all offline."""

import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from knowhow.config import load_event
from knowhow.llm import LLMError, ScriptedLLM
from knowhow.record import extract, transcribe
from knowhow.record.capture import CaptureApp
from knowhow.record.ops import closeout, replay
from knowhow.record.spool import MAX_ATTEMPTS, Spool, begin_attempt
from knowhow.record.status import spool_status
from knowhow.sim.fake import fake_llm

EVENT = Path(__file__).parent.parent / "events" / "bread-class.yaml"
SENTENCE = "We weighed the flour for every loaf because cup measures varied by a hundred grams between students. "


def setup(tmp_path):
    cfg = load_event(EVENT)
    cfg = cfg.model_copy(update={"steps": ["Measuring", "Proofing"]})
    return cfg, Spool(tmp_path / "spool")


def post_clip(c, code, seq, start=1_700_000_000_000, data=b"x" * 2000, ctype="audio/webm"):
    return c.post(f"/api/r/{code}/clip?device=Dev-1&seq={seq}&start={start + seq * 15000}&duration=15000", content=data, headers={"content-type": ctype})


def add_text(spool, group, name, text):
    (spool.text_dir(group)).mkdir(parents=True, exist_ok=True)
    (spool.text_dir(group) / f"{name}.txt").write_text(text)


# ------------------------------------------------------------------ capture


def test_capture_stores_clips_once_with_metadata(tmp_path):
    cfg, spool = setup(tmp_path)
    with TestClient(CaptureApp(cfg, spool).starlette) as c:
        r = post_clip(c, "A-2291", 0)
        assert r.status_code == 200 and r.json()["stored"]
        again = post_clip(c, "A-2291", 0)
        assert again.json() == {"ok": True, "stored": False, "clip": r.json()["clip"]}
        assert post_clip(c, "NOPE", 0).status_code == 404
        assert c.post("/api/r/A-2291/clip?seq=1", content=b"x").status_code == 400
        assert c.get("/r/A-2291").status_code == 200
        assert c.get("/api/status").json()["groups"]["A-2291"]["clips"] == 1
    clips = spool.audio_clips("A-2291")
    assert len(clips) == 1 and clips[0].suffix == ".webm"
    meta = spool.clip_meta(clips[0])
    assert meta["device"] == "Dev-1" and meta["seq"] == 0 and meta["bytes"] == 2000
    assert clips[0].stem.split("-")[1] == "dev1"  # device names are cleaned for file names
    assert not list(spool.audio_dir("A-2291").glob(".*"))  # no temporary files left


# --------------------------------------------------------------- transcribe


def test_transcribe_writes_one_transcript_per_clip(tmp_path):
    cfg, spool = setup(tmp_path)
    with TestClient(CaptureApp(cfg, spool).starlette) as c:
        for i in range(3):
            post_clip(c, "B-5530", i)
    n = transcribe.run_once(spool, ["B-5530"], lambda p: f"text of {p.stem}", "test", log=lambda *_: None)
    assert n == 3
    names = spool.text_names("B-5530")
    assert len(names) == 3
    rec = json.loads((spool.text_dir("B-5530") / f"{names[0]}.json").read_text())
    assert rec["text"] == f"text of {names[0]}" and rec["asr"] == "test"
    assert transcribe.run_once(spool, ["B-5530"], lambda p: "again", "test", log=lambda *_: None) == 0


def test_command_transcriber_reads_stdout(tmp_path):
    clip = tmp_path / "c.webm"
    clip.write_bytes(b"x")
    cmd = f'{sys.executable} -c "import sys; print(\'heard \' + sys.argv[1].split(\'/\')[-1])" {{audio}}'
    assert transcribe.command_transcriber(cmd)(clip) == "heard c.webm"


def test_failing_clip_is_retried_then_skipped(tmp_path):
    cfg, spool = setup(tmp_path)
    with TestClient(CaptureApp(cfg, spool).starlette) as c:
        post_clip(c, "A-2291", 0)

    def broken(path):
        raise subprocess.CalledProcessError(1, ["asr"])

    for _ in range(MAX_ATTEMPTS + 1):
        transcribe.run_once(spool, ["A-2291"], broken, "test", log=lambda *_: None)
    name = spool.audio_clips("A-2291")[0].stem
    assert spool.transcribe_failed("A-2291", name)
    g = spool_status(cfg, spool)["groups"]["A-2291"]
    assert g["state"] == "bad" and g["transcription_failed"] == [name]


def test_a_crash_mid_transcription_counts_as_an_attempt(tmp_path):
    cfg, spool = setup(tmp_path)
    with TestClient(CaptureApp(cfg, spool).starlette) as c:
        post_clip(c, "A-2291", 0)
    name = spool.audio_clips("A-2291")[0].stem
    for _ in range(MAX_ATTEMPTS):
        begin_attempt(spool, "transcribe", "A-2291", name)  # as if the worker died each time
    calls = []
    transcribe.run_once(spool, ["A-2291"], lambda p: calls.append(p) or "x", "test", log=lambda *_: None)
    assert calls == [] and spool.transcribe_failed("A-2291", name)


# ------------------------------------------------------------------ extract


def test_extract_waits_for_enough_text_then_writes_insights_and_digest(tmp_path):
    cfg, spool = setup(tmp_path)
    add_text(spool, "A-2291", "0000000000001-d-00000", "Short remark.")
    llm = fake_llm()
    assert extract.run_once(llm, cfg, spool, "fake", log=lambda *_: None) == 0  # too little text, too recent
    for i in range(1, 15):
        add_text(spool, "A-2291", f"0000000000001-d-{i:05d}", SENTENCE)
    assert extract.run_once(llm, cfg, spool, "fake", log=lambda *_: None) >= 1
    files = extract.window_files(spool, "A-2291")
    rec = json.loads(files[0].read_text())
    assert rec["clips"][0] == "0000000000001-d-00000"
    assert rec["insights"] and rec["insights"][0]["step"] == "Measuring"
    digest = spool.digest.read_text()
    assert "## Measuring" in digest and "Group A" in digest


def test_extract_stops_at_a_clip_still_being_transcribed(tmp_path):
    cfg, spool = setup(tmp_path)
    with TestClient(CaptureApp(cfg, spool).starlette) as c:
        for i in range(3):
            post_clip(c, "C-4817", i)
    clips = spool.audio_clips("C-4817")
    add_text(spool, "C-4817", clips[0].stem, SENTENCE * 20)
    add_text(spool, "C-4817", clips[2].stem, SENTENCE * 20)  # clip 1 has no transcript yet
    extract.run_once(fake_llm(), cfg, spool, "fake", force=True, log=lambda *_: None)
    assert extract.covered_clips(spool, "C-4817") == {clips[0].stem}


def test_failed_extraction_is_retried_and_finally_marked(tmp_path):
    cfg, spool = setup(tmp_path)
    add_text(spool, "B-5530", "0000000000001-d-00000", SENTENCE * 15)

    def refuse(purpose, system, user, schema):
        raise LLMError("bad output")

    llm = ScriptedLLM(refuse)
    assert extract.run_once(llm, cfg, spool, "x", log=lambda *_: None) == 0
    assert extract.covered_clips(spool, "B-5530") == set()
    assert spool_status(cfg, spool)["groups"]["B-5530"]["extract_errors"]
    for _ in range(MAX_ATTEMPTS):
        extract.run_once(llm, cfg, spool, "x", log=lambda *_: None)
    rec = json.loads(extract.window_files(spool, "B-5530")[0].read_text())
    assert rec["failed"] and rec["insights"] == []
    assert "Windows not extracted" in spool.digest.read_text()


def test_silence_is_covered_without_a_model_call(tmp_path):
    cfg, spool = setup(tmp_path)
    add_text(spool, "A-2291", "0000000000001-d-00000", "")

    def boom(*a):
        raise AssertionError("no model call expected")

    extract.run_once(ScriptedLLM(boom), cfg, spool, "x", force=True, log=lambda *_: None)
    assert extract.covered_clips(spool, "A-2291") == {"0000000000001-d-00000"}


# ----------------------------------------------------------------- closeout


def test_closeout_extracts_the_rest_then_deletes_only_audio(tmp_path):
    cfg, spool = setup(tmp_path)
    with TestClient(CaptureApp(cfg, spool).starlette) as c:
        post_clip(c, "A-2291", 0)
    assert not closeout(cfg, spool, fake_llm(), "fake", yes=True, log=lambda *_: None)  # not transcribed yet
    transcribe.run_once(spool, ["A-2291"], lambda p: SENTENCE, "test", log=lambda *_: None)
    assert closeout(cfg, spool, fake_llm(), "fake", yes=True, log=lambda *_: None)
    assert not (spool.root / "audio").exists()
    assert spool.text_names("A-2291") and extract.window_files(spool, "A-2291") and spool.digest.exists()


# ------------------------------------------------------------------- replay


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_replay_cuts_a_recording_into_uploaded_clips(tmp_path):
    cfg, spool = setup(tmp_path)
    src = tmp_path / "talk.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 33)  # 33 seconds
    with TestClient(CaptureApp(cfg, spool).starlette) as c:
        n = replay("http://testserver", "A-2291", src, segment_s=15, speed=0,
                   post=lambda url, data, headers: c.post(url, content=data, headers=headers).status_code, log=lambda *_: None)
    assert n == 3 and len(spool.audio_clips("A-2291")) == 3
    assert all(p.suffix == ".wav" for p in spool.audio_clips("A-2291"))
