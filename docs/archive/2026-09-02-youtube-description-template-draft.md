# YouTube Description Template — Working Draft

> Review scratchpad. Mark it up however you like (inline notes, strikethroughs,
> rewrites) and I'll fold your edits back into the real template
> (`backend/config.py` → `default_youtube_description`, rendered by
> `backend/services/youtube_description.py`).

**Applied so far:** removed AI framing · referral link `nomadkaraoke.com/r/youtube` ·
**50% off** · dropped the "not monetized" claim.

**Still pending your input:** Discord link, which community links stay, and the
open questions at the bottom.

---

## 1. Rendered example (what a viewer sees) — UPDATED with your edits

Rendered for **Coldplay – Yellow**, brand code `NOMAD-1603` (1329 chars; YT limit 5000):

```text
🎤 Sing along to "Yellow" by Coldplay — a high-quality karaoke version using the original song audio with the lead vocals removed and scrolling lyrics hand-synced to the music.

⬆️ REQUEST THE NEXT NOMAD RELEASE
Vote for the songs you want us to make next — the most-upvoted requests get made into karaoke videos and published free, right here on this channel. Nothing to buy, no password needed:
👉 https://requests.nomadkaraoke.com

▶️ MAKE A VIDEO LIKE THIS FOR ANY SONG
Create studio-quality karaoke videos of ANY song, ready in under an hour:
👉 https://nomadkaraoke.com/r/youtube
Follow that link for 50% off all credit purchases for your first 30 days.

🎶 KARAOKE LOVER LINKS
• Nomad Karaoke Community Discord: https://discord.nomadkaraoke.com
• Decide what to sing at karaoke: https://decide.nomadkaraoke.com
• Search all community karaoke tracks: https://karaokenerds.com

🔔 Subscribe for new karaoke tracks, and comment to show some love!

#karaoke #Coldplay #instrumental #lyrics #singalong
—
COPYRIGHT: I don't own the rights to the original music; all rights belong to the respective copyright holders. If you enjoy the song, please buy the original and support the artist. Under §107 of the U.S. Copyright Act 1976, allowance is made for "fair use" (criticism, comment, teaching, and research).

Brand Code: NOMAD-1603
```

> **One change I made to your draft:** your REQUEST section said *"once per day our
> system will generate a karaoke video for whatever is most upvoted, to be auto
> published to this channel."* Per `REQUESTS-BOARD.md`, that daily auto-generate +
> auto-publish is **Phase 2 (not built yet)**, and the doc explicitly says to avoid
> promising a *guaranteed daily* free track until it ships. So I softened it to
> *"the most-upvoted requests get made into karaoke videos and published free."*
> When Phase 2 lands, we flip this back to the "every day, automatically" wording —
> and the bulk tool can re-run to update all videos in one pass. **Push back if you
> want the aspirational wording now.**

---

## 2. Raw template (with placeholders)

`{title}`, `{artist}`, `{artist_hashtag}`, `{brand_code}` are filled per-video.
If `{artist_hashtag}` reduces to nothing (e.g. artist "!!!") that hashtag is
dropped; if there's no brand code, the whole `Brand Code:` line is removed.

```text
🎤 Sing along to "{title}" by {artist} — a high-quality karaoke version using the original song audio with the lead vocals removed and scrolling lyrics hand-synced to the music.

⬆️ REQUEST THE NEXT NOMAD RELEASE
Vote for a song you want a karaoke version of, and once per day our system will generate a karaoke video for whatever is most upvoted, to be auto published to this channel!
👉 https://requests.nomadkaraoke.com 

▶️ MAKE A VIDEO LIKE THIS FOR ANY SONG
Create studio-quality karaoke videos of ANY song, ready in under an hour:
👉 https://nomadkaraoke.com/r/youtube
Follow that link for 50% off all credit purchases for your first 30 days.

🎶 KARAOKE LOVER LINKS
• Nomad Karaoke Community Discord: https://discord.nomadkaraoke.com
• Decide what to sing at karaoke: https://decide.nomadkaraoke.com
• Search all community karaoke tracks: https://karaokenerds.com

🔔 Subscribe for new karaoke tracks, and comment to show some love!

#karaoke #{artist_hashtag} #instrumental #lyrics #singalong
—
COPYRIGHT: I don't own the rights to the original music; all rights belong to the respective copyright holders. If you enjoy the song, please buy the original and support the artist. Under §107 of the U.S. Copyright Act 1976, allowance is made for "fair use" (criticism, comment, teaching, and research).

Brand Code: {brand_code}
```

---

## 3. Tags (invisible to viewers; drive discovery)

Currently generated per-video:

```
karaoke, instrumental, lyrics, karaoke version, sing along, backing track,
{artist}, {title}, {artist} karaoke, {artist} {title} karaoke
```

---

## 4. Open questions — answer inline here

1. **Discord link + label.** Repo only has `discord.gg/diveBar`. What's the correct
   Nomad Karaoke invite URL, and label — "Nomad Karaoke Discord" or keep "Global
   Karaoke Community"?
   → **YOUR ANSWER:**

2. **Which community links stay?** (diveBar/Discord, KaraokeNerds, KaraokeHunt.)
   Keep all / drop any / add any (socials, subscribe link)?
   → **YOUR ANSWER:**

3. **"hand-synced to the music"** — you said AI is one component of many. Keep
   "hand-synced" (leans into human-craft, good vs. anti-AI sentiment), or soften
   ("carefully synced" / "precision-synced")? What framing are you honestly happy
   with?
   → **YOUR ANSWER:**

4. **First line** (SEO-critical — shows in search + above the fold). Happy with
   `Sing along to "{title}" by {artist} — …`?
   → **YOUR ANSWER:**

5. **Copyright block** — keep as-is, trim, or drop the legal boilerplate?
   → **YOUR ANSWER:**

6. **Tone / emoji / section order** — any changes?
   → **YOUR ANSWER:**

7. **Anything else** (the "bunch of other feedback"):
   → **YOUR ANSWER:**
