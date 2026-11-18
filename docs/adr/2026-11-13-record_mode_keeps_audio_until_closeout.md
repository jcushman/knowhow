# Record mode keeps audio until closeout

**Date:** 2026-11-13

**Status:** Accepted

## Context

Some sessions need a simpler setup than live coaching. Groups talk through a
worksheet end to end, and the room reviews a presentation built from what
was said. That setup has to be reliable on event wifi with whatever devices
are at the tables. It should also work with whichever speech recognition is
available on the day.

The coach mode transcribes each audio segment and deletes it immediately
([transcribe on the organizer's laptop and keep no audio](2026-11-06-transcribe_on_the_organizer_s_laptop_and_keep_no_audio.md)).
If transcription fails or turns out poor, the words are gone.

## Decision

Add a record mode (`knowhow rec`). It is a one-way pipeline in which each
stage reads one folder and writes the next:

- **Capture.** The browser saves each 15-second clip on the device before
  uploading it, and retries until the server confirms. The server stores each
  clip once, writing it to a temporary name and renaming it into place.
- **Transcription.** Any transcriber that writes `text/<group>/<clip>.txt`
  or `.json` will do. The built-in worker runs mlx-whisper or a shell command.
- **Extraction.** A local model turns each few minutes of transcript into
  insights tagged with the event's steps, and keeps a running digest.
- **Supervision.** A supervisor restarts any stage that exits. Each stage
  records an attempt before risky work, so an item that crashes a stage is
  skipped after three tries instead of crashing it forever.

Audio is kept in the spool until `knowhow rec closeout`. Closeout extracts
whatever is left and then deletes the audio. Transcripts and insights remain
until `knowhow purge` after the room signs off.

## Alternatives considered

- **Delete audio after transcription, as coach mode does.** This keeps less
  data, but a transcription failure or a better model on the day can't be
  recovered or re-run.
- **One process holding the session in memory, as coach mode does.** This is
  simpler to start, but a crash loses unsaved state, and one stage's failure
  stops the others.
- **Stream audio over a socket instead of uploading clips.** Updates would
  arrive sooner, but a dropped connection loses audio unless the client
  buffers anyway.

## Consequences

- Audio exists on the organizer's machine for the length of the event. The
  promise to participants has to say so ("audio is deleted at the end of the
  event") instead of "audio isn't kept".
- The coach mode and its decision are unchanged.
- Participant phones aren't required. Dedicated, plugged-in recording devices
  are more reliable, because a locked phone screen stops recording.
- A recording page that is reloaded while offline cannot load until the
  network returns. Clips already saved on the device upload when the page is
  reopened.
