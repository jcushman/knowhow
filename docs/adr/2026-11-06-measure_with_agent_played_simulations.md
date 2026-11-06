# Measure with agent-played simulations

**Date:** 2026-11-06

**Status:** Accepted

## Context

Prompt, model and interface changes need evaluation before an event, and a
real session happens once. Whether knowledge reached the playbook can be
scored only against something known in advance to be there.

## Decision

`knowhow sim` runs the real engine with language-model players:

- A sim file defines fictional personas, each with hidden lessons (a lesson,
  the case behind it, how they knew, where it stops working).
- Groups advance in lockstep on a simulated clock. Each member is interviewed
  for an even share of the time; the others take turns asking.
- Askers look at the phone with a set probability and may use or ignore its
  suggestion.
- Lines reach the coach unlabeled, as from speech recognition.
- After synthesis, a judge model scores each lesson three times: conveyed in
  conversation, captured in the map, and kept faithfully in the playbook. It
  also lists playbook claims the transcript does not support.

Each run writes a report, the transcript, per-turn phone use, the judge's
verdicts and a `walkthrough.html` page.

## Alternatives considered

- **Pilot sessions with people only.** Real, but slow, and without ground
  truth to score against.
- **Fixed recorded transcripts.** Repeatable for the coach and synthesis, but
  they cannot test whether different questions draw out more.

## Consequences

- Results measure the system against model-played experts, which may reveal
  lessons more readily than people do. In the runs so far, recall was close to
  the ceiling, so the simulation cannot yet distinguish between good designs
  (see the [simulation results note](../research/2026-11-06-simulated-runs.md)).
- Real runs cost money; `--dry-run` runs the whole pipeline offline.
- Personas in the repository must be fictional.
