# Changelog

## v0.2.0 — Tested on real footage, and made honest about what it does

This release came from running the editor on **real videos** (a 15-minute talking-head, a real
UGC take, a screen recording) instead of synthetic test clips — and fixing everything that broke.

### Added
- **Virality engine (`ve_virality.py`)** — the real "find the viral spot" upgrade. Scores each
  candidate clip on **audio delivery**, not just words: energy peaks (emphasis), dynamic range
  (animated vs monotone), laughter/applause (Scribe audio events), and a dramatic pause before the
  punch. Then an **LLM finalist ranker** reads the full text of the top candidates and picks the
  ones that would actually perform — rejecting stumbles, mid-thoughts, and no-payoff clips, with a
  one-line reason for each pick. Falls back to audio-only, then to the transcript rubric, so it
  always returns something. This is now the default Shorts picker in `edit.py`.
- **`captions: auto`** — captions short videos and all Shorts, but skips burning captions across a
  full long (>4 min) main edit (you post the Shorts; captioning a 15-min main is slow and rarely
  wanted). Force with `--captions on`.
- **Open-search fallback for B-roll** — when no trusted channel matches an entity, it does a flagged
  open YouTube search (`⚠️ outside trusted sources`) so you get a candidate instead of nothing.
  Toggle with `broll.allow_open_search` in `taste.yaml`.

### Fixed
- **B-roll entity extraction** no longer picks junk like "Last", "Three", "Offer". It now targets
  real brands/tools (GHL, YouTube, Verizon, CRM…), multi-word proper nouns, and genuinely unusual
  capitalized words — proven on a real UGC transcript.
- **Live B-roll sourcing now actually works** — went from 0/7 to 6/7 real clips downloaded via
  `yt-dlp` on a real video.
- **Caption burning scales to long videos** — batched the PNG-overlay passes so it no longer crashes
  with "Resource temporarily unavailable" on videos with hundreds of caption lines.
- **Stutter removal** — repeated-word stumbles ("it's, it's, it's over" → "it's over") are now cut.

### Honest status
The viral-spot finder went from "keyword rubric, miscalibrated, fooled by filler" to "audio-aware +
LLM-ranked, picks clips a human would defend." It's genuinely good now, not perfect: a brief stutter
buried inside a long clip can still slip past the ranker. The editor is a strong **B+/A-** automated
first-pass — it nails the repeatable craft and finds real clips; the final creative taste call stays
yours. See the README for the full honest breakdown.
