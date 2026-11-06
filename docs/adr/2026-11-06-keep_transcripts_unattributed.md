# Keep transcripts unattributed

**Date:** 2026-11-06

**Status:** Accepted

## Context

People share failures and workarounds more freely when nothing is attributed
to them. Organizers may still have participant profiles and want the coach to
use them. A coach that knows which named person is in a group can attach names
to transcript content.

## Decision

- Transcript lines carry no speaker names. Speech recognition does not
  separate speakers, and the system does not try to.
- Participant profiles may inform group-level `group_hints` or a prepared
  question bank, never the live coach directly.
- The coach and synthesis prompts forbid names and identifying details. When
  the coach mentions another group's view on a phone, it paraphrases.
- `knowhow purge` deletes transcripts and map state after the room signs off,
  keeping only the drafted output.

## Alternatives considered

- **Speaker diarization.** It would let the coach tell who is the expert, but
  it creates exactly the attribution participants were told would not exist.
- **Named profiles in the coach prompt.** Better-targeted questions, at the
  cost of the same attribution risk.

## Consequences

- The coach infers who is answering from context. Simulations can pass
  `--speaker-labels` to measure how much this costs.
- The walkthrough page reconstructs simulated speakers from turn order for
  readers. That is possible only because the speakers are simulated.
