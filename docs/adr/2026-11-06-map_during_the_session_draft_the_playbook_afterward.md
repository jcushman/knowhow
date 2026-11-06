# Map during the session, draft the playbook afterward

**Date:** 2026-11-06

**Status:** Accepted

## Context

A session produces several hours of transcript across groups. The organizers
need a draft playbook soon after the session ends, and the room needs to trust
that the draft reflects what was said. Live coaching also needs some running
understanding of each conversation to suggest follow-ups that build on what an
expert just mentioned.

## Decision

Keep two separate model passes:

- During the session, a coach pass per group (about once a minute) turns new
  transcript lines into small, typed map items close to the speaker's wording,
  and refreshes that group's suggested questions.
- After listening ends, one synthesis pass drafts the playbook from all map
  items and transcripts, choosing its structure from the material. Every claim
  cites map item ids.

Event `lenses` are only a weak hint for the coach's "change direction"
suggestion and are never the playbook outline.

## Alternatives considered

- **Draft the playbook live and keep revising it.** This repeats expensive
  whole-document rewrites all session and fixes a structure before the
  material is in. A fixed outline also pulls the questions toward filling
  sections rather than following the expert.
- **Transcribe only, and draft everything afterward.** Cheaper, but the phones
  could then offer only generic questions, and nothing could warn the room
  during the session that a thread had already come up in another group.
- **Use the event's lenses as the playbook outline.** Simpler for readers, but
  it would favor the organizers' expected categories over what the experts
  actually said.

## Consequences

- The map is the record of what was heard, and synthesis is checkable against
  it (see [account for every map item in code](2026-11-06-account_for_every_map_item_in_code.md)).
- The playbook is unavailable until synthesis finishes. In simulated runs
  synthesis took between about 30 seconds and 2½ minutes, depending on the
  model and session length.
- Revisit if organizers need a readable draft during the session itself.
