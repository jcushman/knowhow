"""Command line: serve a live session, run a simulation, re-draft, purge."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .config import load_event
from .llm import make_llm


MODEL_HELP = "model for every role, overriding the event config; 'openrouter:<slug>' uses OpenRouter"


def _llms(cfg, args):
    if args.dry_run:
        from .sim.fake import fake_llm

        return fake_llm(), fake_llm()
    coach = getattr(args, "coach_model", None) or args.model or cfg.models.coach
    synth = getattr(args, "synth_model", None) or args.model or cfg.models.synth
    return (
        make_llm(coach, fallbacks=cfg.models.fallbacks),
        make_llm(synth, fallbacks=cfg.models.fallbacks),
    )


def cmd_serve(args) -> None:
    import uvicorn

    from .asr import make_asr
    from .engine import Session
    from .server import App

    cfg = load_event(args.event)
    coach, synth = _llms(cfg, args)
    session = Session(cfg, coach, synth, Path(args.data))
    app = App(session, make_asr(args.asr, cfg.vocabulary), admin_key=args.admin_key)
    base = args.public_url or f"http://{args.host}:{args.port}"
    print(f"Facilitator: {base}/facilitator?key={app.admin_key}")
    for g in cfg.groups:
        print(f"{g.label}: {base}/g/{g.code}")
    uvicorn.run(app.starlette, host=args.host, port=args.port, log_level="warning")


def cmd_sim(args) -> None:
    from .sim.runner import load_sim, run_sim

    cfg = load_event(args.event)
    sim = load_sim(args.sim)
    out = Path(args.out or f"runs/{datetime.now():%Y%m%d-%H%M%S}")
    coach, synth = _llms(cfg, args)
    if args.dry_run:
        from .sim.fake import fake_llm

        player, judge_llm = fake_llm(), fake_llm()
    else:
        player = make_llm(args.player_model or args.model or cfg.models.coach, fallbacks=cfg.models.fallbacks)
        judge_llm = make_llm(args.judge_model or args.model or cfg.models.synth, fallbacks=cfg.models.fallbacks)
    run_sim(
        cfg,
        sim,
        coach_llm=coach,
        synth_llm=synth,
        player_llm=player,
        judge_llm=judge_llm,
        out_dir=out,
        minutes=args.minutes,
        seed=args.seed,
        speaker_labels=args.speaker_labels,
        groups=args.groups.split(",") if args.groups else None,
    )
    print(f"Report: {out / 'report.md'}")


def cmd_draft(args) -> None:
    from .engine import Session

    cfg = load_event(args.event)
    coach, synth = _llms(cfg, args)
    session = Session(cfg, coach, synth, Path(args.data))
    for name, path in session.synthesize().items():
        print(f"{name}: {path}")


def cmd_walkthrough(args) -> None:
    from .sim.runner import load_sim
    from .sim.walkthrough import write_walkthrough

    print(write_walkthrough(Path(args.run), load_event(args.event), load_sim(args.sim)))


def cmd_purge(args) -> None:
    data = Path(args.data)
    targets = [p for p in data.iterdir() if p.name != "output"] if data.exists() else []
    if not targets:
        print("Nothing to purge.")
        return
    print("Will delete:\n" + "\n".join(f"  {p}" for p in targets))
    if not args.yes and input("Type 'delete' to confirm: ").strip() != "delete":
        print("Cancelled.")
        return
    for p in targets:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    print(f"Deleted. Kept {data / 'output'}.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="knowhow")
    sub = ap.add_subparsers(required=True)

    s = sub.add_parser("serve", help="run a live session")
    s.add_argument("event")
    s.add_argument("--data", required=True, help="session directory (transcripts live here until purge)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--asr", default="mlx-whisper", choices=["mlx-whisper", "none"])
    s.add_argument("--admin-key")
    s.add_argument("--public-url", help="the tunnel URL, for printing join links")
    s.add_argument("--dry-run", action="store_true", help="use the offline fake LLM")
    s.add_argument("--model", help=MODEL_HELP)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("sim", help="run an agent-played session and score it")
    s.add_argument("event")
    s.add_argument("sim")
    s.add_argument("--out")
    s.add_argument("--minutes", type=int, help="override session length (shorter runs cost less)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--groups", help="comma-separated group codes to simulate (default: all in the sim file)")
    s.add_argument("--coach-model")
    s.add_argument("--synth-model")
    s.add_argument("--player-model")
    s.add_argument("--judge-model")
    s.add_argument("--speaker-labels", action="store_true", help="give the coach speaker labels (ASR won't)")
    s.add_argument("--dry-run", action="store_true", help="use the offline fake LLM everywhere")
    s.add_argument("--model", help=MODEL_HELP)
    s.set_defaults(fn=cmd_sim)

    s = sub.add_parser("draft", help="(re)draft the playbook from a session directory")
    s.add_argument("event")
    s.add_argument("--data", required=True)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--model", help=MODEL_HELP)
    s.set_defaults(fn=cmd_draft)

    s = sub.add_parser("walkthrough", help="write an HTML walkthrough of a simulated run")
    s.add_argument("event")
    s.add_argument("sim")
    s.add_argument("run", help="run directory from 'knowhow sim'")
    s.set_defaults(fn=cmd_walkthrough)

    s = sub.add_parser("purge", help="delete transcripts and map state, keeping output/")
    s.add_argument("--data", required=True)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_purge)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
