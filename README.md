# knowhow

knowhow helps a room of practitioners write down what they know from
experience. People split into small groups and take turns interviewing each
other. A phone in the middle of each group listens and suggests the next
question. On the organizer's laptop, a coach model turns the conversation into
a concept map as it happens. When the session ends, a draft playbook is
written from the map, with every point either used in the playbook or listed
as left out.

**Status:** prototype. It has been exercised end to end with simulated
participants played by language models, but not yet at a live event.
Speech recognition with several groups in one room has not been tested. See
[docs/commitments.md](docs/commitments.md) for what is and isn't established.

## How a session works

1. The organizer runs `knowhow serve` on a laptop and exposes it over HTTPS
   (phones allow microphone access only over HTTPS).
2. Each group opens its join link. The first phone to tap Start listens; other
   phones show the same question, and any can take over if the listener stops.
   Pause stops capture.
3. Phones upload 15-second audio segments. The laptop transcribes each one
   and deletes the audio. Transcripts carry no speaker names.
4. About once a minute per group, the coach reads the new lines, adds map
   items, and refreshes the group's questions. Map items are typed as: a case,
   what to do, how to tell, why, what you need, what goes wrong, or an open
   question. The questions are the current one, the next step in the
   sequence, two follow-ups on things the expert mentioned, and a way to
   change direction.
5. The facilitator page shows each group's status without showing what anyone
   said:
   - whether a phone is listening, and when audio last arrived;
   - words transcribed per minute and how far transcription is behind;
   - when the coach last updated, or that its last update failed.

   From the same page the facilitator sends a message to every phone and ends
   listening.
6. Ending listening drafts the playbook. Three files land in the session's
   `output/` directory:
   - `playbook.md`;
   - `concept-map.md`, which lists every point and where it went;
   - `accounting.json`.
7. After the room signs off, `knowhow purge` deletes transcripts and map state
   and keeps `output/`.

## Quick start

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).
[just](https://just.systems/) runs the common tasks.

```bash
uv sync
just test        # offline tests, no model calls
just sim-dry     # a whole simulated session with the offline fake model
```

`just sim-dry` writes `runs/dry/`. Open `runs/dry/walkthrough.html` to step
through the setup, each group's conversation and the resulting playbook. With
the fake model the conversation is canned; a real run shows real
conversation.

To see the phone and facilitator pages without a model or microphone:

```bash
just serve-dry
```

Then open http://localhost:8000/g/A-2291 and
http://localhost:8000/facilitator?key=dev. With `--asr none` there is no
microphone input. Text can be posted to `/api/g/<code>/text` instead.

## Running a real session

Copy [events/bread-class.yaml](events/bread-class.yaml) and set:
- the scenario and goal;
- the groups and their join codes;
- the opening and fallback questions;
- any vocabulary that speech recognition should spell correctly.

Then:

```bash
uv sync --extra asr   # on-laptop speech recognition; needs Apple silicon and ffmpeg
uv run knowhow serve events/my-event.yaml --data sessions/my-event --public-url https://<tunnel-host>
```

The server prints the facilitator URL with its key and one join link per
group. The speech model (`whisper-large-v3-turbo`, downloaded on first use)
loads at startup; the facilitator page says when it is ready.

Re-draft from a saved session with `knowhow draft EVENT --data DIR`. Delete
everything but `output/` with `knowhow purge --data DIR`.

### Event config

The engine is topic-neutral; the event config carries everything specific to
one event.
- `lenses` are a weak hint for the coach when a thread runs out. They never
  become the playbook outline.
- `documents` lists background files that the coach and drafter read.
- `group_hints` holds group-level descriptions of expertise, without names.

If you have profiles of real participants, use them only for `group_hints`
or a prepared question bank. Never give them to the live coach: a coach that
knows who is in a group can attach names to what was said.

## Simulations

`knowhow sim EVENT SIM` runs the real engine with language-model players:
- Fictional personas carry hidden lessons, and groups interview each other on
  a simulated clock.
- The coach sees unlabeled lines, as it would from speech recognition.
- At the end, a judge scores each lesson three ways: was it said, was it
  mapped, and did the playbook keep it.

The example personas are in [sims/bread-class.yaml](sims/bread-class.yaml).

```bash
just sim-mini openrouter:openai/gpt-6.1-terra   # 2 groups, 6 simulated minutes
```

Each run directory contains:
- `report.md`: recall at each stage, unsupported playbook claims, phone use,
  model usage and cost;
- the transcript and per-turn phone use;
- the judge's verdicts;
- the session output;
- `walkthrough.html`.

Options:
- `--minutes` shortens a run.
- `--groups` limits which groups run.
- `--coach-model`, `--synth-model`, `--player-model` and `--judge-model` vary
  one role at a time.
- `--speaker-labels` gives the coach speaker names, to separate transcription
  effects from coaching effects.

In one test, a 45-minute, four-group run cost about $3 and a 6-minute,
two-group run about $0.16. Details and results are in
[docs/research/2026-11-06-simulated-runs.md](docs/research/2026-11-06-simulated-runs.md).

## Models

Model names come from the event config's `models` block or from `--model`.
- A plain model name uses the Anthropic SDK, with its usual credentials
  (`ANTHROPIC_API_KEY` or an `ant auth login` profile).
- A name of the form `openrouter:<slug>` uses OpenRouter with
  `OPENROUTER_API_KEY`.

Coach and synthesis calls send transcript text to the configured provider.
Audio stays on the laptop.

## Tasks

`just --list` is the authoritative list.

| Recipe | What it does |
|---|---|
| `just test` | Offline test suite |
| `just sim-dry` | Whole simulated session with the fake model |
| `just sim-mini MODEL` | Short real simulation; costs money |
| `just walkthrough RUN` | Rebuild a run's `walkthrough.html` |
| `just serve-dry` | Local server with the fake model and no microphone |
| `just adr "Title"` | Start a decision record |

## Layout

| Path | Contents |
|---|---|
| `src/knowhow/engine.py` | Session state and operations, shared by the server and the simulator |
| `src/knowhow/coach.py` | Live coach prompt and call |
| `src/knowhow/synth.py` | Playbook drafting, rendering and accounting of every map item |
| `src/knowhow/server.py`, `static/` | Phone, facilitator and join pages and their APIs |
| `src/knowhow/health.py` | Per-group pipeline status for the facilitator page |
| `src/knowhow/asr.py` | Speech recognition backends |
| `src/knowhow/llm.py` | Model clients (Anthropic SDK, OpenRouter) |
| `src/knowhow/sim/` | Simulated players, runner, judge, offline fake model, walkthrough page |
| `events/`, `sims/` | Example event and simulation configs |
| `docs/` | [Commitments](docs/commitments.md), [decision records](docs/adr/README.md), [research](docs/research/) |

## Known limits

- Speech recognition with several groups in one room, and phones on event
  wifi over a tunnel, have not been tested.
- Output is Markdown; there is no export to a shared document editor.
- State lives in one process. Restarting the laptop resumes from `state.json`,
  but phones need to reload.

## License

MIT. See [LICENSE](LICENSE).
