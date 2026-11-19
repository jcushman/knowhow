"""Folder of recordings → transcripts → insights, offline."""

import json
from pathlib import Path

from knowhow.config import load_event
from knowhow.llm import LLMError, ScriptedLLM
from knowhow import transcripts as t
from knowhow.sim.fake import fake_llm

EVENT = Path(__file__).parent.parent / "events" / "bread-class.yaml"
SENTENCE = "We weighed the flour for every loaf because cup measures varied by a hundred grams between students. "
quiet = lambda *_: None  # noqa: E731


def cfg():
    return load_event(EVENT).model_copy(update={"steps": ["Measuring", "Proofing"]})


def test_chunks_split_at_sentences_and_keep_all_text():
    text = SENTENCE * 200  # about 20,000 characters
    parts = t.chunks(text, size=8000)
    assert len(parts) == 3 and all(len(p) <= 8000 for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert " ".join(parts) == text.strip()
    assert [len(p) for p in t.chunks("x" * 250, size=100)] == [100, 100, 50]  # no sentence ends at all


def test_prompt_has_no_clip_ids():
    assert "clips" not in t.SYSTEM and "Never include names" in t.SYSTEM


def test_folder_is_transcribed_and_extracted_then_skipped_on_rerun(tmp_path):
    (tmp_path / "A.m4a").write_bytes(b"audio")
    (tmp_path / "B.txt").write_text(SENTENCE * 100)  # a transcript made some other way
    (tmp_path / ".DS_Store").write_bytes(b"x")
    heard = []
    ok = t.process(cfg(), tmp_path, lambda p: heard.append(p.name) or SENTENCE * 3, fake_llm(), log=quiet)
    assert ok and heard == ["A.m4a"]
    assert (tmp_path / "A.txt").read_text().startswith("We weighed")
    b = json.loads(t.insights_path(tmp_path, "B").read_text())
    assert len(b["chunks"]) == 2 and b["chunks"][0]["insights"][0]["step"] == "Measuring"
    digest = (tmp_path / "insights.md").read_text()
    assert "## Measuring" in digest and "_(A, case)_" in digest and "_(B, case)_" in digest
    assert "Not finished" not in digest

    def boom(*a):
        raise AssertionError("nothing left to do")

    assert t.process(cfg(), tmp_path, boom, ScriptedLLM(boom), log=quiet)


def test_failed_chunk_is_resumed_on_the_next_run(tmp_path):
    (tmp_path / "A.txt").write_text(SENTENCE * 200)
    calls = []

    def second_call_fails(purpose, system, user, schema):
        calls.append(user)
        if len(calls) == 2:
            raise LLMError("server error")
        return fake_llm().parse(purpose=purpose, system=system, user=user, schema=schema, effort="low")

    assert not t.process(cfg(), tmp_path, None, ScriptedLLM(second_call_fails), log=quiet)
    assert len(json.loads(t.insights_path(tmp_path, "A").read_text())["chunks"]) == 1
    assert "Not finished" in (tmp_path / "insights.md").read_text()
    assert t.process(cfg(), tmp_path, None, ScriptedLLM(second_call_fails), log=quiet)
    assert len(calls) == 4  # parts 2 and 3 on the second run; part 1 not repeated
    assert "part 2 of 3" in calls[2] and "We weighed" in calls[2].split("<earlier_insights>")[1]


def test_edited_transcript_is_extracted_again(tmp_path):
    (tmp_path / "A.txt").write_text(SENTENCE)
    t.process(cfg(), tmp_path, None, fake_llm(), log=quiet)
    (tmp_path / "A.txt").write_text("Proofing took two hours in the cold room. ")
    t.process(cfg(), tmp_path, None, fake_llm(), log=quiet)
    rec = json.loads(t.insights_path(tmp_path, "A").read_text())
    assert [i["text"] for c in rec["chunks"] for i in c["insights"]] == ["Proofing took two hours in the cold room."]


def test_unreadable_recording_is_reported(tmp_path):
    (tmp_path / "A.m4a").write_bytes(b"not audio")
    (tmp_path / "B.txt").write_text(SENTENCE)

    def cannot_decode(path):
        raise RuntimeError("ffmpeg failed")

    assert not t.process(cfg(), tmp_path, cannot_decode, fake_llm(), log=quiet)
    assert "- A" in (tmp_path / "insights.md").read_text() and t.insights_path(tmp_path, "B").exists()
