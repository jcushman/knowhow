"""`knowhow rec ...`: command-line entry points for record mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import load_event
from .spool import Spool

ASR_CHOICES = ["mlx-whisper", "command", "fake"]


def _common() -> argparse.ArgumentParser:
    """Options every worker accepts, so `rec run` can pass one set to all."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("event")
    p.add_argument("--data", required=True, help="spool directory")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--asr", default="mlx-whisper", choices=ASR_CHOICES)
    p.add_argument("--asr-command", help="for --asr command: a shell command with {audio}; it prints the transcript")
    p.add_argument("--model", help="extraction model; 'local:<name>' for an OpenAI-compatible server")
    p.add_argument("--model-base-url", help="base URL for 'local:<model>' (or KNOWHOW_MODEL_BASE_URL)")
    p.add_argument("--dry-run", action="store_true", help="fake model for extraction")
    return p


def add_parser(sub) -> None:
    common = _common()
    rec = sub.add_parser("rec", help="record mode: browser audio → transcripts → extracted insights")
    rs = rec.add_subparsers(required=True)

    s = rs.add_parser("run", parents=[common], help="run capture, transcription and extraction, restarting any that stops")
    s.set_defaults(fn=cmd_run)
    s = rs.add_parser("serve", parents=[common], help="capture server only")
    s.set_defaults(fn=cmd_serve)
    s = rs.add_parser("transcribe", parents=[common], help="transcribe worker only")
    s.add_argument("--once", action="store_true")
    s.set_defaults(fn=cmd_transcribe)
    s = rs.add_parser("extract", parents=[common], help="extract worker only")
    s.add_argument("--once", action="store_true")
    s.add_argument("--force", action="store_true", help="extract short windows now instead of waiting")
    s.set_defaults(fn=cmd_extract)
    s = rs.add_parser("status", parents=[common], help="print pipeline status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = rs.add_parser("closeout", parents=[common], help="extract what's left, then delete the audio")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_closeout)
    s = rs.add_parser("replay", help="upload a recording to a running capture server, for rehearsal")
    s.add_argument("url", help="capture server, e.g. http://127.0.0.1:8000")
    s.add_argument("code", help="group code")
    s.add_argument("audio", help="any file ffmpeg can read")
    s.add_argument("--segment", type=int, default=30)
    s.add_argument("--speed", type=float, default=1.0, help="1 is real time; 0 is as fast as possible")
    s.set_defaults(fn=cmd_replay)


def _setup(args):
    cfg = load_event(args.event)
    return cfg, Spool(Path(args.data))


def _extract_llm(cfg, args):
    if args.dry_run:
        from ..sim.fake import fake_llm

        return fake_llm(), "fake"
    from ..llm import make_llm

    model = args.model or cfg.models.coach
    return make_llm(model, fallbacks=cfg.models.fallbacks), model


def _forward(args) -> list[str]:
    out = [args.event, "--data", args.data, "--host", args.host, "--port", str(args.port), "--asr", args.asr]
    for flag, val in (("--asr-command", args.asr_command), ("--model", args.model), ("--model-base-url", args.model_base_url)):
        if val:
            out += [flag, val]
    if args.dry_run:
        out.append("--dry-run")
    return out


def cmd_run(args) -> None:
    from .ops import supervise, worker_commands

    import os
    import shutil
    import subprocess

    _setup(args)  # fail fast on a bad config
    if shutil.which("caffeinate"):
        # macOS: no idle or system sleep while this process lives (lid closed still sleeps on battery).
        subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())])
        print("Keeping this Mac awake while the pipeline runs. Keep it plugged in.")
    print(f"Recording pages: http://{args.host}:{args.port}/   Status: http://{args.host}:{args.port}/status")
    supervise(worker_commands(_forward(args)))


def cmd_serve(args) -> None:
    import uvicorn

    from .capture import CaptureApp

    cfg, spool = _setup(args)
    app = CaptureApp(cfg, spool)
    for g in cfg.groups:
        print(f"{g.label}: http://{args.host}:{args.port}/r/{g.code}")
    uvicorn.run(app.starlette, host=args.host, port=args.port, log_level="warning")


def cmd_transcribe(args) -> None:
    from . import transcribe as t

    cfg, spool = _setup(args)
    if args.asr == "command":
        if not args.asr_command:
            sys.exit("--asr command needs --asr-command")
        fn, name = t.command_transcriber(args.asr_command), f"command: {args.asr_command}"
    elif args.asr == "fake":
        fn, name = t.fake_transcriber(), "fake"
    else:
        import os

        from ..asr import MlxWhisperASR

        cached = Path.home() / ".cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo"
        if cached.exists():
            # The model is on disk: don't let venue wifi stall or fail the load.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        asr = MlxWhisperASR(vocabulary=cfg.vocabulary)
        print("Loading the speech model…")
        asr.warm()
        fn, name = t.asr_transcriber(asr), f"mlx-whisper {asr.model}"
    groups = [g.code for g in cfg.groups]
    if args.once:
        t.run_once(spool, groups, fn, name)
    else:
        print(f"Transcribing with {name}")
        t.loop(spool, groups, fn, name)


def cmd_extract(args) -> None:
    from . import extract as x

    cfg, spool = _setup(args)
    llm, model = _extract_llm(cfg, args)
    if args.once or args.force:
        x.run_once(llm, cfg, spool, model, force=args.force)
        x.write_digest(cfg, spool)
    else:
        print(f"Extracting with {model}; digest at {spool.digest}")
        x.loop(llm, cfg, spool, model)


def cmd_status(args) -> None:
    from .status import format_status, spool_status

    cfg, spool = _setup(args)
    st = spool_status(cfg, spool)
    print(json.dumps(st, indent=1) if args.json else format_status(st))


def cmd_closeout(args) -> None:
    from .ops import closeout

    cfg, spool = _setup(args)
    llm, model = _extract_llm(cfg, args)
    if not closeout(cfg, spool, llm, model, yes=args.yes):
        sys.exit(1)


def cmd_replay(args) -> None:
    from .ops import replay

    replay(args.url, args.code.upper(), Path(args.audio), segment_s=args.segment, speed=args.speed)
