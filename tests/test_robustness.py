"""Failures found in agent-played runs with a smaller model: malformed link
ids, lines left uncoached at close, lines refused without a signal, and state
lost between coach passes."""

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from knowhow.asr import NullASR
from knowhow.coach import build_coach_prompt
from knowhow.config import load_event
from knowhow.engine import Session
from knowhow.llm import LLMError, make_llm
from knowhow.models import CoachUpdate, NewItem, NewLink, Question, QuestionSet
from knowhow.server import App
from knowhow.sim.fake import ScriptedLLM, fake_llm

EVENT = Path(__file__).parent.parent / "events" / "bread-class.yaml"
LONG = "We weighed the flour and water for every loaf because cup measures varied by a hundred grams. " * 3


def questions() -> QuestionSet:
    return QuestionSet(
        current=Question(text="What happened next?"),
        next_in_sequence=Question(text="How did you know?"),
        offshoots=[Question(text="Who helped?"), Question(text="What did it cost?")],
        redirect=Question(text="So what's the lesson?"),
    )


def coach_with_links(links: list[str]) -> ScriptedLLM:
    def respond(purpose, system, user, schema):
        if schema is not CoachUpdate:
            return fake_llm().parse(purpose=purpose, system=system, user=user, schema=schema, effort="low")
        uid = user.split("<new_lines>", 1)[1].split()[0]
        items = [
            NewItem(text="Weigh everything.", kind="what_to_do", specific=True, links=[], utterance_ids=[uid]),
            NewItem(
                text="Cup measures varied by 100 g.",
                kind="case",
                specific=True,
                links=[NewLink(target_id=t, relation="supports") for t in links],
                utterance_ids=[uid],
            ),
        ]
        return CoachUpdate(new_items=items, questions=questions(), replace_current=False, note="", new_turn=False)

    return ScriptedLLM(respond)


def session(tmp_path, coach=None) -> Session:
    return Session(load_event(EVENT), coach or fake_llm(), fake_llm(), tmp_path / "s")


@pytest.mark.parametrize("target", ["new:N0", "new:#0", "new:0"])
def test_malformed_new_link_targets_resolve(tmp_path, target):
    s = session(tmp_path, coach_with_links([target]))
    s.ingest("A-2291", LONG, t=1)
    s.coach("A-2291", now=90)
    items = list(s.state.items.values())
    assert len(items) == 2
    assert [l.target_id for l in items[1].links] == [items[0].id]


def test_unresolvable_and_self_links_are_dropped(tmp_path):
    s = session(tmp_path, coach_with_links(["new:N", "new:9", "new:1", "Z99"]))
    s.ingest("A-2291", LONG, t=1)
    s.coach("A-2291", now=90)
    assert all(not i.links for i in s.state.items.values())


def test_failed_pass_changes_nothing(tmp_path):
    def refuse(purpose, system, user, schema):
        raise LLMError("bad output")

    s = session(tmp_path, ScriptedLLM(refuse))
    s.ingest("A-2291", LONG, t=1)
    with pytest.raises(LLMError):
        s.coach("A-2291", now=90)
    assert s.state.items == {} and len(s.pending("A-2291")) == 1


def test_final_pass_coaches_a_short_tail_and_reports_failures(tmp_path):
    s = session(tmp_path)
    s.ingest("A-2291", "Weigh the salt too.", t=1)  # under the 200-character threshold
    s.close()
    assert s.final_pass(now=100) == []
    assert s.pending("A-2291") == [] and s.state.items

    def refuse(purpose, system, user, schema):
        raise LLMError("bad output")

    s2 = Session(load_event(EVENT), ScriptedLLM(refuse), fake_llm(), tmp_path / "s2")
    s2.ingest("B-5530", "Store whole-grain flour cold.", t=1)
    s2.close()
    failed = s2.final_pass(now=100)
    assert len(failed) == 1 and failed[0].startswith("B-5530: 1 lines not coached")
    assert s2.state.final_pass_failures == failed
    log = [json.loads(l) for l in (tmp_path / "s2" / "coach-log.jsonl").read_text().splitlines()]
    assert log[-1]["group"] == "B-5530" and log[-1]["final"] and "bad output" in log[-1]["error"]


def test_refused_lines_get_409_and_are_counted(tmp_path):
    s = session(tmp_path)
    app = App(s, NullASR(), admin_key="k")
    with TestClient(app.starlette) as c:
        assert c.post("/api/g/C-4817/text", json={"text": "kept"}).json()["stored"]
        c.post("/api/g/C-4817/pause", json={"paused": True})
        r = c.post("/api/g/C-4817/text", json={"text": "not kept"})
        assert r.status_code == 409 and r.json()["dropped"] == "paused"
        r = c.post("/api/g/C-4817/audio?device=d", content=b"x" * 3000, headers={"content-type": "audio/webm"})
        assert r.status_code == 409
        audio = c.get("/api/admin/overview?key=k").json()["health"]["groups"]["C-4817"]["stages"][1]["detail"]
    assert s.state.dropped == {"C-4817": {"paused": 2}}
    assert "2 refused" in audio
    assert [u.text for u in s.state.utterances] == ["kept"]


def test_lines_are_saved_without_waiting_for_the_coach(tmp_path):
    s = session(tmp_path)
    s.ingest("A-2291", "First line.", t=1)
    saved = json.loads((tmp_path / "s" / "state.json").read_text())
    assert [u["text"] for u in saved["utterances"]] == ["First line."]
    s.ingest("A-2291", "Second line.", t=2)  # within the save interval
    s.flush()
    saved = json.loads((tmp_path / "s" / "state.json").read_text())
    assert len(saved["utterances"]) == 2


def test_coach_sees_questions_already_asked(tmp_path):
    s = session(tmp_path)
    s.pick("A-2291", "offshoot:0", t=5)
    g = s.state.groups["A-2291"]
    prompt = build_coach_prompt(s.cfg, group=g, group_size=3, now=60, context=[], new=[], own_items=[], other_items=[])
    asked = prompt.split("<already_asked>", 1)[1].split("</already_asked>", 1)[0]
    assert g.questions.current.text in asked


def test_local_backend_needs_a_base_url(monkeypatch):
    monkeypatch.delenv("KNOWHOW_MODEL_BASE_URL", raising=False)
    with pytest.raises(LLMError, match="KNOWHOW_MODEL_BASE_URL"):
        make_llm("local:qwen")
    monkeypatch.setenv("KNOWHOW_MODEL_BASE_URL", "http://127.0.0.1:9/v1/")
    monkeypatch.setenv("KNOWHOW_MODEL_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    llm = make_llm("local:qwen")
    assert llm.url == "http://127.0.0.1:9/v1/chat/completions"
    assert llm.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_handout_has_one_page_per_group_with_join_link():
    from knowhow.handout import render_handout

    cfg = load_event(EVENT)
    page = render_handout(cfg, "https://example.org/")
    assert page.count('<section class="page">') == len(cfg.groups)
    for g in cfg.groups:
        assert f"https://example.org/g/{g.code}" in page
    assert page.count("<svg") == len(cfg.groups)
    assert "Anthropic's Claude AI service" in page
    assert "placeholder" not in render_handout(cfg, "https://example.org")
    assert "placeholder" in render_handout(cfg, None)
