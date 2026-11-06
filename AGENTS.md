# Guidance for agents

Read `README.md`, `docs/commitments.md`, and the relevant ADRs in `docs/adr/`
before changing architecture, prompts, public behavior or project claims.

## Current scope

A working prototype tested only with simulated participants and the offline
fake model. The claim most likely to be overstated is that it works at a live
event: real speech recognition with several groups, phones over a tunnel, and
real participants are all untested. Simulation recall near 100% does not mean
the system works with people.

## Working rules

1. Keep the engine topic-neutral. Domain wording belongs in `events/` and
   `sims/` configs, not in code or prompts.
2. Never send speaker names or participant profiles to the coach or into map
   items, notes or the playbook. See
   `docs/adr/2026-11-06-keep_transcripts_unattributed.md`.
3. Commit only fictional personas and invented scenarios. Real event configs
   go in `local/` (gitignored). Session data (`sessions/`) and run outputs
   (`runs/`) are never committed.
4. Synthesis output must stay fully accounted: every map item either cited or
   listed as unused. Do not move accounting into the model.
5. Catch only expected exceptions with a stated recovery. A failed coach pass
   leaves lines pending for the next pass; keep that property.
6. Record consequential, revisitable choices in `docs/adr/` and run
   `just docs-adr-index`. Put measured results in `docs/research/` with the
   date and the commands used.
7. Follow `docs/editorial/writing-for-humans.md` for project prose. State
   counts and costs only after checking them.

## Verification

- `just test` after any code change (offline, no model calls).
- `just sim-dry` after changes to the simulator, synthesis, accounting or
  walkthrough.
- For page changes, `just serve-dry` and look at `/g/<code>` and
  `/facilitator?key=dev`.
- Real simulations (`just sim-mini`, `knowhow sim` without `--dry-run`) cost
  money and need an API key. Ask before running one, and use `--minutes` and
  `--groups` to keep them small.
