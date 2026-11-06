# Transcribe on the organizer's laptop and keep no audio

**Date:** 2026-11-06

**Status:** Accepted

## Context

Participants are told what happens to their words. Audio is more identifying
than text and costly to hold securely. The transcript has to reach the coach
within about a minute for the phone's suggestions to keep up.

## Decision

Phones upload audio segments to the organizer's laptop. The laptop
transcribes each segment locally with mlx-whisper (`whisper-large-v3-turbo`),
deletes the temporary audio file, and keeps only text. The model loads when
the server starts, so the first segment does not wait for a download. Event
`vocabulary` is passed to Whisper as a spelling hint. `--asr none` drops audio
for testing.

## Alternatives considered

- **A cloud speech API.** Less load on the laptop, but every segment goes to
  another service, and that service's retention terms join the promise made
  to participants.
- **Transcribe on each phone in the browser.** No upload of audio at all, but
  browser speech recognition support and quality vary across phones.

## Consequences

- Transcription runs one segment at a time on the laptop. With four groups
  each sending a segment every 15 seconds, it has to stay under about four
  seconds per segment to keep up; the facilitator page shows the queue and
  delay. This throughput has not been measured on event hardware.
- mlx-whisper requires Apple silicon.
- Model calls (coach and synthesis) still send transcript text to the
  configured model provider. Audio does not leave the laptop.
