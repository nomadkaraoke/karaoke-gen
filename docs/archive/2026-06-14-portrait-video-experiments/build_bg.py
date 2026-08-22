#!/usr/bin/env python3
"""Build a branded 1080x1920 portrait karaoke background for the experiment."""
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
OUT = "/Users/andrew/portrait-exp/out/bg_branded.png"
LOGO = "/Users/andrew/portrait-exp/out/logo_crop.png"
FONT = "/Users/andrew/portrait-exp/piri/font.ttf"

ARTIST = "PIRI"
TITLE = "DOG"

# Vertical gradient navy -> near black
img = Image.new("RGB", (W, H), (8, 10, 24))
top = (16, 18, 42)
bot = (4, 5, 12)
px = img.load()
for y in range(H):
    t = y / (H - 1)
    r = int(top[0] * (1 - t) + bot[0] * t)
    g = int(top[1] * (1 - t) + bot[1] * t)
    b = int(top[2] * (1 - t) + bot[2] * t)
    for x in range(W):
        px[x, y] = (r, g, b)

draw = ImageDraw.Draw(img, "RGBA")

# Subtle neon-pink rounded border
margin = 26
draw.rounded_rectangle([margin, margin, W - margin, H - margin],
                       radius=46, outline=(255, 60, 200, 90), width=4)
draw.rounded_rectangle([margin + 6, margin + 6, W - margin - 6, H - margin - 6],
                       radius=40, outline=(150, 80, 247, 50), width=2)

# Wordmark (NOMAD KARAOKE), neon pink on dark — paste with screen-ish blend by
# compositing over the gradient (dark areas blend, neon stays).
logo = Image.open(LOGO).convert("RGBA")
lw = 540
lh = int(logo.height * lw / logo.width)
logo = logo.resize((lw, lh), Image.LANCZOS)
# Knock out the dark brick background: make near-dark pixels transparent so only neon remains.
lpx = logo.load()
for y in range(logo.height):
    for x in range(logo.width):
        r, g, b, a = lpx[x, y]
        lum = max(r, g, b)
        if lum < 70:
            lpx[x, y] = (r, g, b, 0)
        elif lum < 130:
            lpx[x, y] = (r, g, b, int(a * (lum - 70) / 60))
img.paste(logo, ((W - lw) // 2, 70), logo)

# Artist · Title under the wordmark
f_title = ImageFont.truetype(FONT, 60)
f_artist = ImageFont.truetype(FONT, 44)
y0 = 70 + lh + 26
tw = draw.textlength(TITLE, font=f_title)
draw.text(((W - tw) / 2, y0), TITLE, font=f_title, fill=(255, 255, 255))
aw = draw.textlength(ARTIST, font=f_artist)
draw.text(((W - aw) / 2, y0 + 70), ARTIST, font=f_artist, fill=(255, 223, 107))

# Footer handle
f_foot = ImageFont.truetype(FONT, 38)
foot = "nomadkaraoke.com"
fw = draw.textlength(foot, font=f_foot)
draw.text(((W - fw) / 2, H - 110), foot, font=f_foot, fill=(255, 122, 204))

img.save(OUT)
print(OUT, img.size)
