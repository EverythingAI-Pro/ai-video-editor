# Captions craft (PIL overlay — no libass)

This machine's ffmpeg has NO libass, so captions are rendered as transparent PIL PNGs and overlaid,
timed to the words. `scripts/ve_captions.py` does this. Do NOT switch to the ffmpeg `subtitles=` filter.

## The three dimensions
- **Chunking** (`taste.yaml: captions.words_per_line`): 2-3 words = punchy ad cadence; 4-5 = calmer narrative.
- **Case** (`captions.case`): `upper` for LOUD ad style; `sentence` for narrative; `lower` for soft/minimal.
- **Placement** (`captions.position`): `lower` (~74-78% down — safe above the Shorts UI), `center`, `upper`.

## Emotion coloring (the ad move)
When `captions.highlight: true`, pain words render in `pain_color` (default orange) and money/proof words
in `money_color` (default green). This is what makes ad captions pop. Pain = lost/fail/struggle/broken/slow/
manual/dead. Money = any `$`, `%`, numbers, scale/saved/doubled/free/growth/revenue. Turn it off for a clean,
single-color narrative look.

## Font + size
- `captions.font` accepts a friendly name (`HelveticaNeue-Bold`, `Impact`, `Menlo`, `Helvetica`) or a path to
  a `.ttf`/`.ttc`. macOS `.ttc` files are collections — the Bold face is NOT index 0; the resolver probes the
  collection by face name, so "HelveticaNeue-Bold" picks the real Bold.
- The renderer **auto-shrinks the font to fit the frame width** so captions never clip on narrow/portrait
  (Shorts) clips. Set `captions.size` as your max; it only shrinks if a line is too wide.

## Spelling enforcement (always on)
ASR mishears brand terms. `captions.force_spelling` force-corrects them case-insensitively. The default fixes
`RizzDial` (from "ris dial"/"rizz dial") and `Skool` (from "school"). **Add the founder's brand terms** as you
learn them — a misspelled brand in a burned caption is a failure that can't be undone after posting.

## Timing
Captions are built on the OUTPUT timeline (after cuts), so they stay in sync:
`out_time = word.start − segment_source_start + segment_output_offset`. `ve_captions.py build` does this from
the transcript + the EDL. Always pass the same EDL you rendered with.

## Order
Captions are burned LAST, after b-roll overlays (so overlays never cover them). `ve_render.py` enforces this.
