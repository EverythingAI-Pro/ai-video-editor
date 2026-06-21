# Shorts & autopilot

## Shorts — vertical clips by viral-hook rubric
`scripts/ve_shorts.py find <transcript> <out.json>` scans the transcript for the windows most likely to stop
scroll, scores them, and returns the top `taste.yaml: shorts.count` that clear `shorts.min_hook_score`.

Each window scores on 8 hook patterns (each present adds ~9 points):
- **curiosity gap** — opens a loop ("the reason nobody tells you…")
- **named enemy** — calls out a resented thing ("stop using per-seat tools")
- **surprising specific** — a concrete number ("100k calls a day", "$2.4M")
- **contrarian** — opposite of conventional wisdom ("everyone thinks… actually…")
- **vulnerable confession** — admits failure/fear ("I failed three times")
- **aspirational reveal** — the result they want ("hours back, on autopilot")
- **ICP call-out** — "if you're a founder doing under $50k/mo…"
- **taboo truth** — "the dirty secret they don't want you to know"

Standalone-ness matters: a window starting with a dangling connector (and/but/so/because) is penalized — a
short must drop in with no setup. Default `min_hook_score` is 55 (only genuinely strong hooks ship). Lower it
in `taste.yaml` to surface more, weaker clips. Each short comes with a ready EDL; `ve_render.py` builds it into
a captioned 9:16 clip just like the main edit, using the same taste.

After finding shorts, render each: write its `edl` to a file, then
`ve_render.py <video> <short.edl> <short.mp4> --transcript <t>`. `edit.py --shorts on` does this loop for you.

## Autopilot — folder in, edited videos out
`scripts/autopilot.py` is the hands-off path James asked for.
- `autopilot.py once <inbox>` — edit everything in the folder now, then exit (good for a scheduled run).
- `autopilot.py watch <inbox>` — keep running; edit new files as they land (`--interval` seconds).

Each video runs the full `edit.py` pipeline with the founder's `taste.yaml`. Outputs go to `<inbox>/edited/`;
a `.done` marker prevents re-processing, a `.failed` marker (with the error tail) prevents infinite retries on
a bad file. A founder never runs this — FounderOS invokes it — but it's a plain script, so it also drops into
cron or a launch agent for true set-and-forget.

## Scripts (creating them for repeatable content)
When a founder posts a recurring format, capture the structure: the main edit's transcript IS the script.
For each short, `ve_shorts.py script <transcript>` prints the hook line + clip range so the founder (or you)
can write a title/caption around it. Keep these next to the output so the next batch is faster.
