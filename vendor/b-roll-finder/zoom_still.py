#!/usr/bin/env python3
"""Sub-pixel subtle Ken Burns: still image -> silent mp4. NO ffmpeg zoompan (integer-stepped = shaky).
Usage: zoom_still.py in.png out.mp4 DURATION [--rate 0.015] [--fps 30] [--size 1920x1080] [--blurfill]
--blurfill: fit the image at natural scale over a blurred copy (for portrait/odd aspect, e.g. tweets)."""
import subprocess, sys
from PIL import Image, ImageFilter

args = sys.argv[1:]
blur = "--blurfill" in args
args = [a for a in args if a != "--blurfill"]
inp, out, dur = args[0], args[1], float(args[2])
def opt(name, default):
    return type(default)(args[args.index(name)+1]) if name in args else default
rate, fps = opt("--rate", 0.015), opt("--fps", 30)
W, H = map(int, opt("--size", "1920x1080").split("x"))

def cover(im):
    sw, sh = im.size; sc = max(W/sw, H/sh)
    im = im.resize((round(sw*sc), round(sh*sc)), Image.LANCZOS)
    x0, y0 = (im.width-W)//2, (im.height-H)//2
    return im.crop((x0, y0, x0+W, y0+H))

src = Image.open(inp).convert("RGB")
if blur:
    bg = cover(src).filter(ImageFilter.GaussianBlur(45))
    sc = min(W*0.88/src.width, H*0.88/src.height)
    fg = src.resize((round(src.width*sc), round(src.height*sc)), Image.LANCZOS)
    bg.paste(fg, ((W-fg.width)//2, (H-fg.height)//2)); base = bg
else:
    base = cover(src)

n = round(dur*fps)
ff = subprocess.Popen(["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","rgb24",
    "-s",f"{W}x{H}","-r",str(fps),"-i","-","-an","-c:v","libx264","-crf","18",
    "-preset","fast","-pix_fmt","yuv420p",out], stdin=subprocess.PIPE)
for f in range(n):
    s = 1.0 + rate*(f/fps)
    bw, bh = W/s, H/s
    x0, y0 = (W-bw)/2, (H-bh)/2
    ff.stdin.write(base.resize((W,H), Image.LANCZOS, box=(x0,y0,x0+bw,y0+bh)).tobytes())
ff.stdin.close(); ff.wait()
print("done", out)
