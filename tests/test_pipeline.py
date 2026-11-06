from pathlib import Path

from starlette.testclient import TestClient

from knowhow.asr import NullASR
from knowhow.config import load_event
from knowhow.engine import Session
from knowhow.server import App
from knowhow.sim.fake import fake_llm
from knowhow.sim.runner import load_sim, run_sim

ROOT = Path(__file__).parent.parent
EVENT = ROOT / "events" / "bread-class.yaml"
SIM = ROOT / "sims" / "bread-class.yaml"

LONG = "We weighed the flour and water for every loaf because cup measures varied by a hundred grams between students. " * 3


def make_session(tmp_path) -> Session:
    return Session(load_event(EVENT), fake_llm(), fake_llm(), tmp_path / "session")


def test_coach_assigns_items_and_keeps_current_question(tmp_path):
    s = make_session(tmp_path)
    s.ingest("C-4817", LONG, t=10)
    assert s.coach_due("C-4817", now=120)
    s.coach("C-4817", now=120)
    items = list(s.state.items.values())
    assert items and all(i.id.startswith("C") and i.utterance_ids == ["u1"] for i in items)
    assert s.pending("C-4817") == []
    # fake coach sets replace_current, so the focused question changed
    assert s.state.groups["C-4817"].questions.current.text == "What happened next?"


def test_pick_and_pause(tmp_path):
    s = make_session(tmp_path)
    s.pick("A-2291", "offshoot:0", t=5)
    g = s.state.groups["A-2291"]
    assert g.questions.current.text == g.questions.offshoots[0].text
    assert g.picks[0]["slot"] == "offshoot:0"
    s.set_paused("A-2291", True)
    assert s.ingest("A-2291", "secret", t=6) is None


def test_synthesis_accounts_for_every_item(tmp_path):
    s = make_session(tmp_path)
    for k in range(4):
        s.ingest("B-5530", LONG, t=k * 100)
        s.coach("B-5530", now=k * 100 + 90)
    paths = s.synthesize()
    acct = paths["accounting"].read_text()
    assert "unused" in acct
    playbook = paths["playbook"].read_text()
    mapmd = paths["map"].read_text()
    assert "1 group" in playbook
    assert "Didn't make the playbook" in mapmd  # fake uses half the items


def test_server_flow(tmp_path):
    app = App(make_session(tmp_path), NullASR(), admin_key="k")
    with TestClient(app.starlette) as c:
        assert c.get("/g/C-4817").status_code == 200
        assert c.get("/g/NOPE").status_code == 404
        st = c.get("/api/g/C-4817/state?device=d1").json()
        assert st["mode"] == "question" and not st["has_listener"]
        c.post("/api/g/C-4817/listen", json={"device": "d1"})
        assert c.get("/api/g/C-4817/state?device=d1").json()["is_listener"]
        assert c.get("/api/g/C-4817/state?device=d2").json()["has_listener"]
        c.post("/api/g/C-4817/text", json={"text": LONG})
        c.post("/api/g/C-4817/pick", json={"slot": "next"})
        assert c.get("/api/admin/overview").status_code == 403
        ov = c.get("/api/admin/overview?key=k").json()
        assert ov["groups"][2]["lines"] == 1
        c.post("/api/admin/message?key=k", json={"markdown": "# Lunch time."})
        st = c.get("/api/g/C-4817/state").json()
        assert st["mode"] == "message" and st["message"].startswith("# Lunch")


def test_dry_run_simulation(tmp_path):
    out = run_sim(
        load_event(EVENT),
        load_sim(SIM),
        coach_llm=fake_llm(),
        synth_llm=fake_llm(),
        player_llm=fake_llm(),
        judge_llm=fake_llm(),
        out_dir=tmp_path / "run",
        minutes=4,
        log=lambda *_: None,
    )
    report = (out / "report.md").read_text()
    assert "| Lessons (hidden ground truth) | 15 |" in report
    assert (out / "session" / "output" / "playbook.md").exists()


def test_purge_keeps_output(tmp_path):
    s = make_session(tmp_path)
    s.ingest("A-2291", LONG, t=1)
    s.coach("A-2291", now=100)
    s.synthesize()
    s.purge_confidential()
    left = {p.name for p in (tmp_path / "session").iterdir()}
    assert left == {"output"}
