#!/usr/bin/env python3
from PIL import Image, ImageDraw, ImageFont
O = "/Users/andrew/portrait-exp/out/"
FONT = "/Users/andrew/portrait-exp/piri/font.ttf"
panels = [
    ("p_letterbox.png", "1. LETTERBOX", "(pad finished video)"),
    ("p_crop.png", "2. CENTER-CROP", "(crop finished video)"),
    ("branded_still.png", "3. RE-RENDER", "(reflow lyrics)"),
]
PH = 1100  # panel image height
lab_h = 90
gap = 30
imgs = []
for fn, _, _ in panels:
    im = Image.open(O + fn).convert("RGB")
    w = int(im.width * PH / im.height)
    imgs.append(im.resize((w, PH), Image.LANCZOS))
W = sum(i.width for i in imgs) + gap * (len(imgs) + 1)
H = PH + lab_h + gap * 2
canvas = Image.new("RGB", (W, H), (20, 20, 28))
draw = ImageDraw.Draw(canvas)
f1 = ImageFont.truetype(FONT, 40)
f2 = ImageFont.truetype(FONT, 28)
x = gap
for (fn, t1, t2), im in zip(panels, imgs):
    canvas.paste(im, (x, lab_h + gap))
    cx = x + im.width / 2
    w1 = draw.textlength(t1, font=f1)
    good = t1.startswith("3")
    draw.text((cx - w1 / 2, 14), t1, font=f1, fill=(120, 230, 140) if good else (240, 120, 120))
    w2 = draw.textlength(t2, font=f2)
    draw.text((cx - w2 / 2, 58), t2, font=f2, fill=(180, 180, 190))
    x += im.width + gap
canvas.save(O + "comparison.png")
print(O + "comparison.png", canvas.size)
