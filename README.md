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
   Pause stops capture: lines that arrive while a group is paused, or after
   the session closes, are refused with HTTP 409 and counted on the
   facilitator page, not stored.
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
6. Ending listening gives every group one last coach pass over whatever it
   hasn't processed yet, then drafts the playbook. A group whose last lines
   could not be coached is listed in `final_pass_failures` in `state.json`.
   Three files land in the session's `output/` directory:
   - `playbook.md`;
   - `concept-map.md`, which lists every point and where it went;
   - `accounting.json`.
7. After the room signs off, `knowhow purge` deletes transcripts, map state
   and the coach log, and keeps `output/`.

State is saved to `state.json` within a few seconds of each new line, so a
server restart loses at most a few seconds. Every coach pass appends a line
to `coach-log.jsonl` in the session directory (timing, line and item counts,
any error; no transcript text).

## Quick start

Requires Python 3.13 or later and [uv](https://docs.astral.sh/uv/).
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

Print one handout per table with the join code, a QR code for the join
link, how the session runs and what happens to what people say:

```bash
uv run knowhow handout events/my-event.yaml --public-url https://<tunnel-host> --out handout.html
```

Open `handout.html` in a browser and print it. Without `--public-url` the QR
codes point to a placeholder address and each page says so.

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

## Record mode

A simpler setup for sessions without live coaching: groups talk, a
recording device on each table records, and a local model extracts insights
for a presentation the room reviews afterwards. Each stage reads one folder
and writes the next, and any stage can stop, restart or be swapped out
without losing work:

| Folder | Written by |
|---|---|
| `audio/<group>/` | the capture server, one 30-second clip per file |
| `text/<group>/` | any transcriber: `<clip>.txt`, or `<clip>.json` with a `text` field |
| `output/insights/<group>/` | the extract worker, one file per few minutes of talk |
| `output/insights.md` | the extract worker: a readable digest grouped by the event's `steps` |

```bash
uv run knowhow rec run local/events/my-event.yaml --data sessions/my-event --model local:<model> --model-base-url http://127.0.0.1:8080/v1
```

`rec run` starts the capture server, the transcriber and the extractor as
separate processes and restarts any that exits. It prints one recording link
per group and a status page (`/status`) that shows counts only, never text.

- **Recording page:** one Record button, a level meter, and upload status.
  Each clip is saved on the device before upload and retried until the
  server has it, so a dropped connection only delays audio. Closing the tab
  keeps unsent clips; reopening the page uploads them. "Download a backup"
  saves every clip on the device as a zip. Browsers need HTTPS for the
  microphone except on `localhost`.
- **Speech recognition:** `--asr mlx-whisper` (default; `uv sync --extra asr`),
  `--asr command --asr-command "my-asr {audio}"` for any tool that prints a
  transcript, or skip the built-in worker and have another tool write
  `text/<group>/<clip>.txt`.
- **Extraction** uses any model `--model` accepts; `local:<model>` keeps
  transcripts on your own hardware. The event config's `steps` are the
  headings insights are sorted under.
- **Presentation:** [docs/record-mode-presenter.md](docs/record-mode-presenter.md)
  has instructions for a chat agent that watches the digest and builds the
  deck with the facilitator. It reads insights only.
- **Rehearsal:** `knowhow rec replay http://127.0.0.1:8000 A-2291 talk.m4a`
  cuts any recording into clips and uploads them in real time (`--speed 0`
  for as fast as possible).
- **Recordings made some other way** (a phone per table, say):
  `knowhow transcripts EVENT FOLDER --model ...` transcribes each audio file
  whole, extracts insights from each transcript in chunks of about 8,000
  characters, and writes `NAME.txt`, `NAME.insights.json` and a combined
  `insights.md` beside the recordings, labelled by file name. A `NAME.txt`
  made with any other tool is used as is. Running it again continues
  whatever didn't finish.
- **After the session:** `knowhow rec status ...` shows what's left.
  `knowhow rec closeout ...` extracts any remaining transcript, then deletes
  the audio after you confirm. Transcripts and insights stay until
  `knowhow purge` once the room signs off. See
  [the decision record](docs/adr/2026-11-13-record_mode_keeps_audio_until_closeout.md).

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
- A name of the form `local:<model>` uses any server with an OpenAI-compatible
  chat completions API and strict JSON-schema output, such as vLLM. Set its
  base URL with `--model-base-url` or `KNOWHOW_MODEL_BASE_URL`, an optional
  key with `KNOWHOW_MODEL_API_KEY`, and extra request fields as JSON in
  `KNOWHOW_MODEL_EXTRA_BODY` (for Qwen models on vLLM,
  `{"chat_template_kwargs": {"enable_thinking": false}}`).

In simulations with Qwen3.6-35B-A3B as the coach, coach passes took a median
of about 1–3 minutes, against about 5 seconds with the hosted models tried;
see [the research note](docs/research/2026-11-10-small-model-runs.md).

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
| `just rec-dry` | Record mode with fake transcription and extraction, at http://127.0.0.1:8000 |
| `just adr "Title"` | Start a decision record |

## Layout

| Path | Contents |
|---|---|
| `src/knowhow/engine.py` | Session state and operations, shared by the server and the simulator |
| `src/knowhow/coach.py` | Live coach prompt and call |
| `src/knowhow/synth.py` | Playbook drafting, rendering and accounting of every map item |
| `src/knowhow/server.py`, `static/` | Phone, facilitator and join pages and their APIs |
| `src/knowhow/health.py` | Per-group pipeline status for the facilitator page |
| `src/knowhow/handout.py` | Printable table handouts |
| `src/knowhow/record/` | Record mode: capture, transcribe and extract workers, status, replay, closeout |
| `src/knowhow/transcripts.py` | A folder of recordings → whole-file transcripts → insights |
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
