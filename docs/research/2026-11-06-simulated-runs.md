# Simulated runs, November 2026

**Checked:** 2026-11-06

**Status:** Research note

## Question and scope

How much of the simulated experts' hidden knowledge reaches the playbook? What
do runs cost? Can a cheaper model replace a stronger one for the coach and
synthesis roles?

Most runs used a private scenario that is not in this repository: 11 fictional
personas with 26 hidden lessons in four groups. One run used the
`bread-class` example. All model calls went through OpenRouter, with costs as
OpenRouter reported them per call. The models were `openai/gpt-6.1-terra`
("Terra") and `deepseek/deepseek-v4.1-flash-1017` routed to its lowest-priced
provider ("DeepSeek").

## Sources or commands

`knowhow sim` with `--model`, `--coach-model`, `--synth-model`, `--minutes`
and `--groups`. The per-run `report.md` and `summary.json` files are kept
under `runs/`, which is not committed.

## Findings

**Full run, private scenario.** Terra in every role, 4 groups, 45 simulated
minutes:

| Measure | Result |
|---|---|
| Lessons fully conveyed in conversation | 92% (96% including partial) |
| Lessons fully captured in the map | 92% |
| Lessons fully in the playbook | 85% (96% including partial) |
| Unsupported playbook claims | 0 |
| Utterances / map items | 510 / 766 (343 used in the playbook) |
| Asking turns that used the phone | 87 of 255 |
| Cost | $2.92 (coach $1.60, players $1.10, synthesis $0.13, judge $0.10) |
| Coach latency | 4.8 s average, 8.5 s maximum, over 127 calls |
| Wall time | 745 s |

The judge's notes on what was lost:
- One lesson, about checking license restrictions, was never drawn out.
- Synthesis softened a "must" into a "should".
- Synthesis turned a concrete case into general advice.

**Controlled comparison, private scenario.** 2 groups, 6 minutes, two seeds
per arm. Players and judge were Terra in every run; only the coach and
synthesis models changed.

| Measure | Terra | DeepSeek |
|---|---|---|
| Lessons fully in the playbook | 64%, 46% | 55%, 64% |
| Unsupported playbook claims | 0, 1 | 4, 5 |
| Coach and synthesis cost per run | about $0.08 | about $0.017 |
| Coach latency, average / maximum | 4.5 s / 5.7 s | 18.7 s / 42 s |
| Synthesis time | 27 s | 103–137 s |

In both arms, synthesis sometimes labelled one expert's caveat, or two
complementary steps, as a disagreement.

**Example scenario.** One run of the `bread-class` example: Terra in every
role, groups A and C, 6 minutes. Of 9 lessons, 78% were fully conveyed and
56% fully reached the playbook (100% of each including partial). The playbook
had no unsupported claims. The run cost $0.16 and took 131 s.

## Inferences

- With these settings, cost and latency separate the two models more clearly
  than recall does. DeepSeek's coach latency of up to 42 s is long next to a
  60-second coaching interval. Its synthesis produced more unsupported claims.
- Two runs per arm cannot distinguish recall differences of this size.

## Limitations and open questions

- Recall in the full run was close to 100%. The simulated experts may reveal
  lessons more readily than people do, which would make the simulation too
  easy to tell good designs from weak ones. Harder personas could test this:
  lessons revealed only when asked about the case or cue, more tangents, and
  noise in the transcript.
- The judge is a model and was not checked against human scoring.
- No run used real speech recognition or real participants.
