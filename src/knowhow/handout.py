"""Printable table handouts: one page per group with its join code, a QR
code for the join link, how the session runs, and what happens to what
people say. Print the HTML to PDF from a browser."""

from __future__ import annotations

import html
from pathlib import Path

import segno

from .config import EventConfig
from .llm import LOCAL_PREFIX, OPENROUTER_PREFIX

PLACEHOLDER_URL = "https://your-tunnel-host"


def provider_phrase(model: str) -> str:
    """Where transcript text goes, in plain words, for the privacy note."""
    if model.startswith(LOCAL_PREFIX):
        return "an AI model on a server the organizers run"
    if model.startswith(OPENROUTER_PREFIX):
        return f"an AI model ({model.removeprefix(OPENROUTER_PREFIX)}) through OpenRouter"
    if model.startswith("claude"):
        return "Anthropic's Claude AI service"
    return f"an AI model service ({model})"


def _providers(cfg: EventConfig) -> str:
    phrases = list(dict.fromkeys(provider_phrase(m) for m in (cfg.models.coach, cfg.models.synth)))
    return " and ".join(phrases)


CSS = """
@page { size: letter; margin: 0.55in; }
* { box-sizing: border-box; }
body { margin: 0; color: #1B1F23; font: 11.5pt/1.45 -apple-system, "Helvetica Neue", Arial, sans-serif; }
.page { page-break-after: always; break-after: page; display: flex; flex-direction: column; gap: 14pt; min-height: 9.8in; }
.page:last-child { page-break-after: auto; break-after: auto; }
.eyebrow { font-size: 9pt; letter-spacing: .08em; text-transform: uppercase; color: #0F6E6E; font-weight: 700; }
h1 { font: 600 30pt/1.1 Georgia, "Times New Roman", serif; margin: 2pt 0 0; }
h2 { font: 600 13pt/1.2 Georgia, serif; margin: 0 0 6pt; }
.join { display: flex; gap: 22pt; align-items: center; border: 1.5pt solid #0F6E6E; border-radius: 12pt; padding: 16pt 18pt; }
.qr svg { width: 1.9in; height: 1.9in; display: block; }
.code { font: 700 30pt/1 ui-monospace, Menlo, monospace; letter-spacing: .04em; }
.url { font: 10.5pt ui-monospace, Menlo, monospace; color: #3A4148; word-break: break-all; }
.join ol { margin: 8pt 0 0; padding-left: 16pt; }
ol, ul { margin: 0; padding-left: 16pt; }
li + li { margin-top: 3pt; }
.cols { display: grid; grid-template-columns: 1.25fr 1fr; gap: 20pt; }
.q { font-family: Georgia, serif; }
.privacy { background: #F1F4F3; border-radius: 10pt; padding: 12pt 14pt; font-size: 10.5pt; }
.sample { margin-top: auto; font-size: 8.5pt; color: #8A5300; }
.foot { margin-top: auto; font-size: 8.5pt; color: #5A6169; display: flex; justify-content: space-between; }
"""


def render_handout(cfg: EventConfig, public_url: str | None) -> str:
    base = (public_url or PLACEHOLDER_URL).rstrip("/")
    providers = _providers(cfg)
    tips = [q for q in cfg.fallback_questions][:5]
    pages = []
    for g in cfg.groups:
        url = f"{base}/g/{g.code}"
        qr = segno.make(url, error="m").svg_inline(scale=10, border=0, omitsize=True)
        per = f"about {round(cfg.minutes / g.size)} minutes each" if g.size else "an even share of the time"
        sample = (
            '<div class="sample">Sample: this QR code points to a placeholder address. '
            "Re-run with the session's public URL before printing.</div>"
            if public_url is None
            else ""
        )
        pages.append(
            f"""<section class="page">
  <div>
    <div class="eyebrow">{html.escape(cfg.title)}</div>
    <h1>{html.escape(g.label)}</h1>
  </div>
  <div class="join">
    <div class="qr">{qr}</div>
    <div>
      <div class="eyebrow">Your group code</div>
      <div class="code">{html.escape(g.code)}</div>
      <div class="url">{html.escape(url)}</div>
      <ol>
        <li>One person scans the code and taps <b>Start</b>. That phone listens: put it in the middle of the table.</li>
        <li>Anyone else can open the same link to read along. Only one phone needs to tap Start.</li>
      </ol>
    </div>
  </div>
  <div class="cols">
    <div>
      <h2>How the session works</h2>
      <ol>
        <li>Choose who goes first. That person is the expert; the others ask the questions.</li>
        <li>The phone shows one suggested question. Ask it when there's a pause, or follow your own curiosity: the suggestion is optional.</li>
        <li>Tap <b>More questions</b> for follow-ups on what was just said, the next step in the story, or a change of direction.</li>
        <li>Switch so everyone gets a turn, {per}. The phone will suggest when.</li>
        <li>Tap <b>Pause</b> to talk off the record. Nothing said while paused is kept.</li>
      </ol>
    </div>
    <div>
      <h2>Questions that usually help</h2>
      <ul class="q">{"".join(f"<li>{html.escape(t)}</li>" for t in tips)}</ul>
    </div>
  </div>
  <div class="privacy">
    <h2>What happens to what you say</h2>
    <ul>
      <li>The listening phone sends audio to the organizer's laptop, which turns it into text and deletes the audio.</li>
      <li>The text has no names attached.</li>
      <li>The text is sent to {html.escape(providers, quote=False)} to suggest questions and draft a shared playbook.</li>
      <li>After the room reviews the draft, the transcripts are deleted. Only the playbook is kept.</li>
    </ul>
  </div>
  {sample}
  <div class="foot"><span>{html.escape(cfg.scenario.split(". ")[0])}.</span><span>{html.escape(g.label)} · {html.escape(g.code)}</span></div>
</section>"""
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(cfg.title)}: table handouts</title>
<style>{CSS}</style></head>
<body>
{"".join(pages)}
</body></html>
"""


def write_handout(cfg: EventConfig, out: Path, public_url: str | None) -> Path:
    out.write_text(render_handout(cfg, public_url))
    return out
