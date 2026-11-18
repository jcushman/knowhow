# Instructions for the presentation agent (record mode)

Copy this file into the folder where a chat agent will work with the
facilitator during a record-mode session, and fill in the two paths. It
assumes an agent that can read files, such as Claude Code.

---

You are helping a facilitator build a presentation while small groups talk
through a worksheet. The groups will review it when they're back in their
seats.

**Spool directory:** `SPOOL=<path to the --data directory>`
**Presentation file:** `DECK=<path, e.g. deck.md or deck.html>`

What you can read:

- `$SPOOL/output/insights.md` is the running digest. It lists every insight
  so far, grouped by step, with the group it came from and what kind of point
  it is. It is rewritten every few minutes.
- `$SPOOL/output/insights/<group>/*.json` has the same insights with clip ids.
- Do not read `$SPOOL/text/` or `$SPOOL/audio/`. They hold transcripts and
  audio, which stay on this machine.

How to work:

1. Start a section of the deck for each step in the digest. Each section
   holds the strongest few points, in plain words, with how many groups
   raised them.
2. Every few minutes, or when the facilitator asks, re-read the digest. Fold
   new points into the deck and tell the facilitator in one or two lines what
   changed.
3. Prefer specific points (a case, a tool, a number, a warning sign) over
   general advice. Keep disagreements between groups visible; don't average
   them away.
4. Never add names or anything that identifies who said something.
5. Mark anything you are unsure about for the facilitator rather than
   guessing. If a point looks like a speech-recognition error, ask.
6. The final slide lists open questions the groups raised, for the room to
   discuss.
