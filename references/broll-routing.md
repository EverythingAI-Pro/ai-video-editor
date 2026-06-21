# B-roll routing & placement (read before serious b-roll work)

Adapted from the b-roll-finder methodology (MIT, Louise de Sadeleer). The job is to **narrow the funnel**,
not to guess taste. The agent NEVER picks the final clip on a taste-route beat — the founder does.

## The one rule
B-roll illustrates the **point**, not the keyword. Read the whole transcript and understand the thesis
before sourcing anything. The proper noun in a line is often NOT the referent.

## Classify EVERY moment (litmus order)
1. **Happening now / a claim or stat / "people are saying"?** → **Receipts**. Source the tweet, headline,
   review, or stat-on-screen. Capture clean screenshots (`vendor/b-roll-finder/cdp_capture.py`).
2. **A person, a physical product, or a historical moment?** → **Entity**. Source the *canonical* clip from
   the official/authoritative channel — not a random upload. Channel-scoped search is the default.
3. **An abstract idea you'd have to draw (a process, a model, a stat)?** → **Concept**. On-brand motion
   graphic, or real footage from the authoritative source.
4. **A reaction / meme beat?** → **Cultural**. The agent NEVER sources this. Flag the moment + register
   ("deadpan after the punchline"); the founder supplies it from their library — or the beat is struck.

## Place ON the word, never before
- Anchor to when the keyword is spoken, then add ~+0.2s lead so the cut lands as/just after it.
- Word timing comes from the word-level transcript. Bias LATER when unsure — early reads as a mistake.
- Connect adjacent b-rolls: if two cutaways are closer than ~half a sentence, extend the first to the
  second's start. A <1s flash of the talking head between two cutaways reads as a glitch.

## Sourcing discipline (keeps quality high)
- **Trusted sources only.** Searches stay inside `taste.yaml: broll.trusted_sources` (scoped by topic tag).
  Open YouTube search is a flagged last resort.
- **No retries.** A method that fails once is dropped, not retried. Second method fails → drop the beat,
  flag it, move on. A beat is never worth a third attempt.
- **Per-beat time box ~5 min.** Place the best-available or drop + flag. One stubborn beat must not stall.
- **Source by SOURCE, not by beat.** One full-page capture or one official film often covers several beats —
  fetch once, crop/cut multiple windows. Cache raw downloads in `broll_assets/`.

## Cadence (restraint reads as taste)
- Beats run ~2-4s. Under ~1.2s is unreadable (except one deliberate burst montage in the intro).
- Front-loaded: dense in the hook, sparse + precise through the body.
- Coverage by density (`taste.yaml: broll.density`): light ~10%, medium ~25%, heavy ~50%.
- If a plan is >60% website screenshots, it's wrong. Mix the palette: receipts, real footage, faces in
  context (never a frozen headshot of someone the audience won't recognize), product UI, concept graphics.

## Composition
- Full-bleed cover-crop: `scale=W:H:force_original_aspect_ratio=increase,crop=W:H`. No agent-built
  split-screens. The one allowed extra treatment is **blurred-fill** for portrait/odd-aspect sources
  (`vendor/b-roll-finder/zoom_still.py --blurfill`).
- Stills get a subtle sub-pixel zoom (`zoom_still.py`, ~1.5%/sec). NEVER ffmpeg `zoompan` (integer-stepped = shaky).
- Strip b-roll audio when the founder talks over it (`taste.yaml: broll.audio: silent`).

## Self-verify before delivery
After a render, pull a frame at every b-roll midpoint and every joint, and LOOK. Auto-reject a frame with:
burned-in captions from the source, name-tags for strangers, watermarks/logo bugs, the speaker's own face
as b-roll in their own video, a generated card where a real filmable thing exists, template-looking
composites, letterboxing, or a <1s talking-head sliver between cutaways. Fix and re-verify.
