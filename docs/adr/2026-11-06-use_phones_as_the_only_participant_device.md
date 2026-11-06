# Use phones as the only participant device

**Date:** 2026-11-06

**Status:** Accepted

## Context

Groups sit together and interview each other. Each group needs something to
capture audio and something to show a suggested question. Participants
bring phones; dedicated hardware or laptops on every table would have to be
bought, set up and charged.

## Decision

- Each group opens one web page (`/g/<code>`) on its phones. The first phone
  to tap Start listens and uploads 15-second audio segments; other phones show
  the same question, and any of them can take over if the listener stops.
- The page shows one suggested question at a time. "More questions" opens the
  full set: the current question, the next in the sequence, two follow-ups on
  specifics the expert mentioned, and a way to change direction.
- Opening questions are generic and come from the event config.
- Phones poll the server every 2.5 seconds instead of holding a socket.

## Alternatives considered

- **A list of questions by default.** More choice, but it pulls attention
  from the person talking. The list is still one tap away.
- **WebSockets or server-sent events.** Faster updates, but connections break
  on phone sleep and crowded wifi. Questions change about once a minute, so
  polling delay does not matter.
- **Separate recorder hardware.** Better audio, at the cost of setup and
  purchase.

## Consequences

- Microphone access on phones requires HTTPS, so a live event needs a tunnel
  (`--public-url`).
- A locked screen or a switched app stops capture; the facilitator page flags
  a group whose audio has been silent for 40 seconds.
- Not yet tested: several groups talking in one room, and real phones over a
  tunnel.
