#!/usr/bin/env python3
"""Segment-concat cutaway renderer: replaces the base video's VISUAL during each beat window;
audio stays untouched. Edit BEATS, BASE, OUT below, then run.
Beat: (label, start_s, end_s, source_path, kind, src_in_s)  kind: "video" | "still"
Stills are rendered via zoom_still.py first (sub-pixel zoom) or held static per your profile."""
import subprocess, os, sys

BASE = "input.mp4"            # the talking-head base (1080p proxy recommended)
OUT  = "output_with_broll.mp4"
END  = None                   # seconds; None = full duration
W, H, FPS = 1920, 1080, 30
BEATS = [
    # ("b01", 2.0, 5.5, "assets/fmt/b01.mp4", "video", 0.0),
]

VF = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},format=yuv420p"
def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: print(r.stderr[-1200:]); sys.exit(1)

if END is None:
    END = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","csv=p=0",BASE],capture_output=True,text=True).stdout.strip())

os.makedirs("fmt", exist_ok=True)
for label, s, e, src, kind, src_in in BEATS:
    dur = round(e-s, 3); out = f"fmt/{label}.mp4"
    if os.path.exists(out): continue
    if kind == "still":
        run(["ffmpeg","-y","-v","error","-loop","1","-t",str(dur),"-i",src,"-vf",VF,
             "-an","-c:v","libx264","-crf","18","-preset","fast",out])
    else:
        run(["ffmpeg","-y","-v","error","-ss",str(src_in),"-t",str(dur),"-i",src,"-vf",VF,
             "-an","-c:v","libx264","-crf","18","-preset","fast",out])

inputs = ["-i", BASE] + [x for label,*_ in BEATS for x in ("-i", f"fmt/{label}.mp4")]
parts, fg, last, seg = [], [], 0.0, 0
for i,(label,s,e,*_) in enumerate(BEATS):
    if s > last:
        fg.append(f"[0:v]trim=start={last}:end={s},setpts=PTS-STARTPTS,{VF}[g{seg}]")
        parts.append(f"[g{seg}]"); seg += 1
    fg.append(f"[{i+1}:v]setpts=PTS-STARTPTS[c{i}]"); parts.append(f"[c{i}]"); last = e
if last < END:
    fg.append(f"[0:v]trim=start={last}:end={END},setpts=PTS-STARTPTS,{VF}[g{seg}]"); parts.append(f"[g{seg}]")
fg.append("".join(parts)+f"concat=n={len(parts)}:v=1:a=0[v]")
fg.append(f"[0:a]atrim=start=0:end={END},asetpts=PTS-STARTPTS[a]")
run(["ffmpeg","-y","-v","error"]+inputs+["-filter_complex",";".join(fg),
     "-map","[v]","-map","[a]","-c:v","libx264","-crf","18","-preset","fast",
     "-c:a","aac","-b:a","192k","-movflags","+faststart",OUT])
print("done", OUT)
