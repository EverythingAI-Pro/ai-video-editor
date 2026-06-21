<div align="center">

# 🎬 AI Video Editor

### Edit your videos by just talking to it. Auto-cut filler, auto-place B-roll, auto-caption, and turn one long video into ready-to-post Shorts.

**The open-source AI video editor for founders, creators, and marketers.**
Feed it a raw talking-head recording → get back a polished, captioned video **plus** vertical Shorts **plus** ready-to-post captions, hooks, and hashtags. No timeline. No menus. You talk, it edits.

[Quick Start](#-quick-start-2-minutes) · [What it does](#-what-it-does) · [Make it yours](#-make-it-edit-like-you-tasteyaml) · [How it works](#-how-it-works) · [FAQ](#-faq)

</div>

---

## Why this exists

Editing video is the #1 bottleneck between a founder and consistent content. Hiring an editor is slow and expensive; learning Premiere is a rabbit hole; the "one-click" apps give you generic slop you can't customize.

**This is different.** It's an AI video editor you run yourself (or inside [FounderOS](https://evrythingai.com)) that does the mechanical 90% of editing — the part that's identical every time — to a consistent, professional standard, and lets *you* keep the taste. You describe what you want in plain English, it shows you the plan, you approve, it renders. Then it hands you Shorts and ready-to-paste captions for every platform.

> **Honest promise:** this reliably produces solid, **B-level automated edits** — clean cuts, captions that pop, B-roll landed on the right word, color graded, loudness-normalized, with Shorts and post copy. It is **A-level consistent**: it nails the repeatable craft every time. The creative taste calls (which B-roll meme, the exact joke timing) stay yours — it shows you options and learns your style. It does not pretend to replace a senior editor's judgment; it replaces the 3 hours of tedium before judgment matters.

---

## ✨ What it does

| | |
|---|---|
| 🎙️ **Transcribes** | Word-level transcript so every cut, caption, and B-roll lands on the exact word. |
| ✂️ **Cuts filler + dead air** | Removes "um, uh, like, you know," false starts, and long pauses — snapped to word boundaries so nothing clips. |
| 🎞️ **Finds + places B-roll** | Classifies each moment (receipt / entity / concept), sources it from sources you trust, and lays it **on the word being spoken**. |
| 🎨 **Color grades** | Auto, subtle, punch, or cinematic — or your own look. |
| 💬 **Burns captions** | Bold, readable captions with **pain words in orange, money words in green** — the style that stops scroll. Auto-fits so text never clips on vertical. |
| 📱 **Finds the viral moments** | Scores every moment by **how it's delivered** — audio energy spikes, laughter, dramatic pauses — not just the words, then an LLM picks the clips that would actually perform (rejecting stumbles and mid-thoughts) and cuts them into Shorts. |
| 📝 **Writes the captions to post** | Per-platform caption + hook + hashtags for TikTok, Reels, YouTube Shorts, X, and LinkedIn. |
| 🌐 **Builds a review page** | A beautiful HTML page: every clip playable, copy-paste captions per platform, posting checklist. Double-click to open. |
| 🤖 **Runs on autopilot** | Drop videos in a folder → edited videos + Shorts + post copy come out. |
| 🎯 **Edits in *your* style** | One `taste.yaml` file. Change a value → every future edit matches you. |

---

## 🚀 Quick Start (2 minutes)

**Requirements:** macOS or Linux, Python 3.10+, [`ffmpeg`](https://ffmpeg.org/download.html). For best transcription, an [ElevenLabs](https://elevenlabs.io) API key (free tier works). For sourcing B-roll from the web, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp).

```bash
# 1. Clone it
git clone https://github.com/EverythingAI-Pro/ai-video-editor.git
cd ai-video-editor

# 2. One-time setup (creates a local Python env with Pillow, numpy, requests)
bash scripts/setup.sh

# 3. Add your transcription key (optional but recommended)
cp .env.example .env        # then paste your ELEVENLABS_API_KEY

# 4. Edit a video — cut, caption, B-roll, Shorts, AND a post-ready review page
.venv/bin/python scripts/edit.py /path/to/your-video.mp4
```

When it finishes you'll have, next to your video in an `edit/` folder:

```
edit/
├── your-video.edited.mp4      ← the polished main edit (cut + captions + color)
├── your-video.short0.mp4      ← vertical Shorts pulled from it
├── review.html                ← OPEN THIS: play everything, copy captions, post
├── your-video.postpack.json   ← captions + hooks + hashtags per platform
└── manifest.json              ← what happened
```

**Open `review.html`** and you've got a clip-by-clip dashboard with copy-paste captions for every platform and a posting checklist. That's the whole loop: raw recording in, content calendar out.

> Using this inside **FounderOS**? You don't touch the terminal at all — just say **"Edit my video"** in chat and attach a clip. See [SKILL.md](SKILL.md).

---

## 🎛️ Make it edit like *you* (`taste.yaml`)

This is the part that makes it *yours*. One commented file controls everything:

```yaml
captions:
  font: "HelveticaNeue-Bold"   # or "Impact", "Menlo", or a path to your .ttf
  case: "upper"                # UPPER (ad style) · sentence · lower
  words_per_line: 3            # 2 = punchy · 4-5 = calm
  position: "lower"            # lower (safe for Shorts UI) · center · upper
  highlight: true              # pain words orange, money words green
cut:
  aggressiveness: "medium"     # gentle · medium · tight
  remove_silences_over_s: 1.2
color:
  grade: "auto"                # auto · subtle · neutral_punch · warm_cinematic · none
broll:
  density: "medium"            # light ~10% · medium ~25% · heavy ~50%
  trusted_sources:             # B-roll search stays INSIDE this list
    - "@anthropic-ai [tech]"
    - "Lenny's Podcast [business]"
shorts:
  count: 3
  min_hook_score: 55           # only clips with strong hooks ship
```

**Change one value, save, re-run — your next edit reflects it.** Make your captions yellow and centered, cut tighter, switch to a cinematic look — it's all one file, no code.

---

## 🧠 How it works

```
your video ─▶ transcribe ─▶ cut filler/silence ─▶ find + place B-roll ─▶ color grade
                                                                              │
   review.html ◀── post pack ◀── make Shorts ◀── burn captions ◀────────────┘
   (open & post)   (per platform)  (hook rubric)   (last, over the b-roll)
```

Each step is a small, readable Python script in [`scripts/`](scripts/) that shells out to `ffmpeg`. Nothing is a black box:

- **`ve_transcribe.py`** — word-level transcript (ElevenLabs Scribe, with caption + provided-JSON fallbacks)
- **`ve_cut.py`** — filler + silence removal → an edit plan you can read
- **`ve_broll.py`** — route classification + trusted-source search + place-on-the-word
- **`ve_captions.py`** — caption rendering (works even where `ffmpeg` lacks libass)
- **`ve_render.py`** — assembles it all in the correct order
- **`ve_shorts.py`** — the 8-pattern viral-hook rubric
- **`ve_postpack.py`** — per-platform captions / hooks / hashtags
- **`ve_publish.py`** — the HTML review page
- **`edit.py`** — runs the whole thing in one command
- **`autopilot.py`** — folder in → edited videos out

See [`references/`](references/) for the editing craft (B-roll routing, caption craft, Shorts rubric) and [`SKILL.md`](SKILL.md) for the conversational flow.

---

## 🤖 Posting (optional)

The recommended way to post is **native upload** from the review page (better reach than API posts) — the page gives you copy-paste captions and a checklist. If you want true hands-off scheduling, [`ve_post.py`](scripts/ve_post.py) can push to **Buffer** or your own **Make/Zapier/n8n webhook** using *your* keys. It's off by default and never posts without `--confirm`.

---

## ❓ FAQ

**Is it actually good, or is it AI slop?** It does the mechanical work (cuts, captions, color, B-roll timing, Shorts, post copy) to a consistent professional bar, and keeps you in the loop on taste calls. The output is genuinely postable. It is not a magic "make me Casey Neistat" button — nothing is.

**Do I need to know how to code?** No. Run one command, or use it inside FounderOS by saying "Edit my video." To customize, you edit one plain-English config file.

**Do I need API keys?** Only for best-quality transcription (ElevenLabs, free tier fine) and web B-roll sourcing (`yt-dlp`, free, no key). Everything else — cutting, captioning, color, Shorts, the review page — runs locally with no keys.

**What platforms does it make content for?** Vertical Shorts for TikTok, Instagram Reels, and YouTube Shorts, plus caption packs for those + X and LinkedIn.

**Does it work on Windows?** The engine is `ffmpeg` + Python, which run on Windows, but setup is tuned for macOS/Linux. Windows via WSL works today; native Windows support is on the roadmap.

---

## 🛠️ Built on great open source

- The editing pipeline and production-correctness rules build on the proven [video-use](https://github.com/) methodology.
- B-roll sourcing/placement is adapted from [**b-roll-finder**](https://github.com/louisedesadeleer/b-roll-finder) by Louise de Sadeleer (MIT — carried in [`vendor/b-roll-finder/`](vendor/b-roll-finder/)).
- Captions, color, and concat run on [`ffmpeg`](https://ffmpeg.org/). Transcription via [ElevenLabs Scribe](https://elevenlabs.io). B-roll downloads via [`yt-dlp`](https://github.com/yt-dlp/yt-dlp).

## 📄 License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it.

---

<div align="center">

**Built by [EveryThingAi](https://evrythingai.com)** — the operating system for founders running an AI company.
If this saved you an afternoon, ⭐ the repo so other founders find it.

`ai video editor` · `auto captions` · `auto b-roll` · `make shorts from long video` · `ffmpeg video automation` · `content repurposing` · `faceless video editing`

</div>
