# Commitments and current facts

This page separates rules the project has adopted from facts that the current
repository establishes, and from things it does not establish.

## Commitments

- **No attribution.** Transcripts, map items, phone notes and the playbook
  carry no participant names or identifying details. Participant profiles
  never go to the live coach. See [the ADR](adr/2026-11-06-keep_transcripts_unattributed.md).
- **No stored audio.** Audio segments are transcribed and deleted on the
  organizer's machine. See [the ADR](adr/2026-11-06-transcribe_on_the_organizer_s_laptop_and_keep_no_audio.md).
- **Pause means not kept.** Lines that arrive while a group is paused, or
  after the session closes, are refused and never stored.
- **Purge after sign-off.** `knowhow purge` removes everything in a session
  directory except `output/`.
- **Nothing dropped silently.** Every map item is either cited in the playbook
  or listed as unused in the concept map. See [the ADR](adr/2026-11-06-account_for_every_map_item_in_code.md).
- **Claims cite what was said.** Synthesis must not state anything the map
  items and transcripts do not support, and must keep disagreements as
  disagreements.
- **Topic-neutral engine.** Wording specific to a domain belongs in event and
  sim configs, not in code or prompts. Map item kinds and link relations stay
  general.
- **Fictional examples only.** Personas and scenarios committed to the
  repository are invented. Real event configs, real participant information,
  session data and run outputs stay out of git (`local/`, `sessions/` and
  `runs/` are ignored).

## Facts established by the current repository

- The offline tests (`just test`) cover:
  - coach item assignment and question handling;
  - phone picks and pausing;
  - accounting of every map item;
  - the phone and facilitator HTTP flows;
  - a dry-run simulation;
  - purge;
  - facilitator health reporting;
  - malformed link ids, the final coach pass at close, refused lines,
    frequent saves, the local model backend and the table handout.
- `just sim-dry` runs the whole simulation pipeline offline with the fake
  model: conversation, coaching, synthesis, judging and the walkthrough page.
- The simulation results in [the research note](research/2026-11-06-simulated-runs.md)
  were produced by `knowhow sim` with real models.

## Facts not established

- Speech recognition accuracy and throughput with several groups talking in
  one room, on the organizer's hardware.
- Phone behavior over an HTTPS tunnel on event wifi, including screen locks
  and app switching.
- Whether simulated experts reveal knowledge the way real people do; recall
  in simulations was close to the ceiling.
- Whether the judge's verdicts match human judgment.
- Any use at a live event.

## Potential directions

- Harder simulated personas, so simulations can tell good designs from weak
  ones.
- Export to a shared document editor; output is Markdown now.
- A replay mode that feeds a recorded transcript into a live server, for
  rehearsing the facilitator role.
