# Simulated runs with a smaller model, November 2026

**Checked:** 2026-11-10

**Status:** Research note

## Question and scope

What fails when knowhow runs with a smaller, self-hosted model in every role,
and which proposed changes help?

A separate team ran these sessions and reported them. Qwen3.6-35B-A3B, served
through an OpenAI-compatible API, played every participant and also ran the
coach and the drafter. The format was not knowhow's one-interview-at-a-time
format: three groups of four people talked freely for 40 turns per group.
Their test script posted the conversation to `/api/g/<code>/text` as one
unlabeled stream, with light transcription damage. Each participant carried
one private fact, 12 in all. One group paused at turn 24 for three turns.

There were two arms. The baseline ran knowhow as published, with only a local
model backend added. The other arm added the team's own changes: refused lines
answered with 409, a final coach pass at close, frequent saves, a fact-check
of each new map item, question rotation, cross-group linking, and naming quiet
people. The team ran 4 baseline sessions and 3 sessions with their changes,
plus one session with their changes that was damaged by a crash. Sessions in
the two arms shared seeds, not speech. No session produced a playbook, so
synthesis was not tested.

## Sources or commands

- The team's final report, scores, test script and run logs, received as
  `knowhow-results.zip`. This repository does not hold a copy.
- Their changed copy of the code, compared against this repository.

## Findings

Reported by the team, and in the three bug items confirmed by reading this
repository's code:

- **Malformed link ids crashed coach passes.** The model wrote targets such
  as `new:N3`, and `int('N3')` raised an error. This happened in 3 of 5
  sessions without the team's fix. In this repository, the pass had already
  marked its lines as coached when the crash happened, so the lines' points
  were lost without any error.
- **The live server had no final coach pass**, so up to a minute of each
  group's last speech never reached the map. The team's own final pass still
  left 1–2 lines uncoached in 2 of 3 sessions, without reporting it.
- **Lines sent during a pause were dropped while the server answered "ok".**
  All 4 baseline sessions lost the same 3 lines this way.
- **Recall was the same in both arms.** Facts correct out of 12: 11.4
  (range 10.5–12) in the baseline and 11.0 (10–11.5) with the changes.
- **The fact-check slowed every pass without improving recall.** Median pass
  time was about 85 s in the baseline and about 140 s with the changes. Some
  of its flags were wrong.
- **Rotation varied the questions.** Distinct main questions per group rose
  from 2.7 to 4.2, and the ranges did not overlap. Whether the questions were
  better was not assessed.
- **Cross-group links were mostly near-duplicates.** The arm with the changes
  made 5 correct links and 2 wrong ones. Neither arm ever linked the two
  themes the sessions were designed around.
- **Naming quiet people produced little.** Almost every named question used
  one fill-in-the-name template. The three quiet participants were named in
  3 of 27 named questions.
- **Map items included persona inventions.** 18–44 items per session held
  content that no participant's private fact contained.

## Changes adopted here

- Coach updates build every item before changing state, read `new:N`
  targets tolerantly, and drop links from an item to itself.
- Ending listening runs a final coach pass for every group. It waits for any
  pass already running and records groups left uncoached in
  `final_pass_failures`.
- The server answers refused lines with 409, and the facilitator page counts
  them. The phone shows that nothing is kept while paused.
- State is saved within a few seconds of each new line.
- Each pass is logged to `coach-log.jsonl`.
- The coach sees the questions already asked and is told not to repeat them.
  The team's rule that forces a new question after three passes was not
  adopted, because it could cut off a productive thread in a one-person
  interview.
- A `local:<model>` backend for OpenAI-compatible servers.
- Python 3.13 support. The tests pass on 3.13 and 3.14.

Not adopted: the fact-check call, cross-group linking through it, and naming
quiet people. Naming people conflicts with
[keep transcripts unattributed](../adr/2026-11-06-keep_transcripts_unattributed.md).

## Limitations and open questions

- Small samples: 3–4 sessions per arm, and one model playing every role.
- The free-discussion format differs from the interview format the coach is
  written for. Results about turn-taking may not carry over.
- Whether the question-history change improves question variety in
  knowhow's own format has not been measured yet.
- Whether the slow coach passes and the invented items come from the model
  size or from the prompts was not separated.
