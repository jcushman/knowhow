# Account for every map item in code

**Date:** 2026-11-06

**Status:** Accepted

## Context

A drafted playbook is shorter than what was said. Readers cannot see what was
left out, and a model asked to summarize can drop points silently or cite ids
that do not exist. Participants need to be able to find their own point if it
did not make the draft.

## Decision

Synthesis returns structured output: playbook claims with the map item ids they
rest on, open questions, disagreements, and threads that group every item.
Plain code, not the model, then computes where each item was used, which items
were unused, which were in no thread, and which cited ids do not exist. The
results are written to `output/accounting.json`. `output/concept-map.md` lists
every item by thread with where it was used, and ends with the items that did
not make the playbook.

## Alternatives considered

- **Ask the model to report what it left out.** This depends on the same model
  whose omissions are being checked.
- **Publish only the playbook.** Simpler for readers, but omissions would be
  invisible and unrecoverable after transcripts are purged.

## Consequences

- Every map item appears in the concept map, used or not; tests check this
  with the offline fake model.
- The accounting proves only that ids were cited. It does not establish that a
  claim represents its cited items faithfully; the simulation judge checks
  that separately (see [simulations ADR](2026-11-06-measure_with_agent_played_simulations.md)).
