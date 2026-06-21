# Vendored from b-roll-finder

These scripts (`zoom_still.py`, `render_cutaways.py`, `cdp_capture.py`) and the b-roll
sourcing/placement methodology folded into this skill are adapted from:

**b-roll-finder** by Louise de Sadeleer — https://github.com/louisedesadeleer/b-roll-finder
Licensed under the MIT License (see `LICENSE` in this folder).

What we use them for in `video-editor`:
- `zoom_still.py` — sub-pixel Ken Burns on still b-roll (no shaky ffmpeg `zoompan`).
- `render_cutaways.py` — reference for the segment-concat cutaway approach (re-implemented
  in `scripts/ve_render.py: overlay_broll` so it reads the skill's placement plan directly).
- `cdp_capture.py` — headless-Chrome capture of receipts (tweets/headlines) for Receipts-route b-roll.

The routing model (Receipts / Entity / Concept / Cultural-Meme), the trusted-source scoping,
the "place on the word (+0.2s lead)" timing, and the TASTE curation model are all from this work.
