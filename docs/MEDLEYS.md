# Making Karaoke Medleys

How to turn several songs into one continuous medley: a backing track plus a synced karaoke
video in the normal gen look. It's a manual, agent-run process for now. A self-serve
version is on the backlog.

First done in October 2026 for a 5-section classic country medley. Total time was about an
hour, mostly waiting on gen jobs. That medley is the worked example throughout this doc and
in `scripts/medley/example-spec.json`.

## The idea in one paragraph

**Don't** build the medley first and push it through gen as a new job. Gen would re-transcribe
it with no reference lyrics to check against, and someone would have to review it from scratch.
Instead, make **one ordinary gen job per original song**. Each job gives us the lossless source,
sample-aligned stems and reviewed word-level lyrics. Then the medley is just two things:

1. **Audio:** apply the same cuts, gains and fades to every variant (instrumental with backing
   vocals, clean instrumental, original mix). Stems are sample-aligned with their source, so all
   variants line up exactly.
2. **Video:** take each job's *reviewed* lyric lines that fall inside its cut, shift them by that
   section's medley offset, and render locally with gen's own `OutputGenerator`. That gives the
   same theme, countdown and title/end screens as a normal track.

Bonus: the per-song jobs are full karaoke tracks too. They can be published as community
versions or given to the customer as extras.

## Step by step

### 1. Make a gen job for each original song

One job per distinct song. A song used twice, like an opener that comes back as the finale,
needs only one job.

- Bulk submit works (`POST /api/bulk/submit` with the admin token; see `docs/API.md`
  § Bulk Mode). Expect jobs to stop at `awaiting_audio_selection`, then pick the source by hand
  with `GET /api/audio-search/{id}/results` + `POST /api/audio-search/{id}/select`.
- **Pick the original studio recording.** Avoid live versions, duets, remasters and "greatest
  hits" sets that might be re-recordings. The customer knows the original arrangement, and the
  cut points assume it. Album compilations from the original label (e.g. an "Essential" or
  "Songs of" collection) are usually the original masters.
- **Prefer CD FLAC torrent results.** They're lossless. Admin jobs get a
  `402 Insufficient credits` on Spotify results, because the admin account has 0 credits and
  Spotify results carry a duration-based charge; torrent results don't.
- Review the lyrics properly. These reviewed timings are what the medley video uses. Timing
  matters more than usual, because every line will be used.

### 2. Write the spec and fetch the jobs

Copy `scripts/medley/example-spec.json` and edit it. Each section names a job, a source
`start`/`end` (seconds; `end: null` = to the end of the song) and how it joins the previous
section:

| Field | Meaning |
|---|---|
| `join: "crossfade"` + `join_duration` | Overlap the previous section's tail (default 2.0s, equal-power) |
| `join: "gap"` + `join_duration` | Insert this much silence instead |
| `fade_out_start` | "Pot out": fader drops from this source second to the section `end` |
| `fade_in` | "Pot in": fade-in length at the start of the section |
| `theme_job_id` (top level) | Whose theme to render with (default: the first section's job) |

```bash
# from a karaoke-gen checkout, in an env where karaoke_gen imports
export PYTHONPATH=$PWD
export MEDLEY_GCLOUD_ACCOUNT=claude-readonly@nomadkaraoke.iam.gserviceaccount.com  # optional
python scripts/medley/make_medley.py fetch path/to/spec.json
```

`fetch` reads each job from Firestore and downloads the source audio, stems, `corrections.json`
and `corrections_updated.json` into `<spec dir>/work/<job_id>/`. It uses the `gcloud` CLI,
because the Python GCS client stalled for about an hour on large FLACs. Stems are deleted 30
days after a job completes (`docs/STORAGE-RETENTION.md`), so build medleys from recent jobs or
regenerate the stems first.

### 3. Find the cut points

This is where the craft is (see [Making it sound good](#making-it-sound-good)). Use data
rather than guesswork:

- **Word timings** from `corrections.json` / the job's `karaoke.lrc`: where lines start and end.
- **Lead-vocal activity** from `stems/lead_vocals.flac`. RMS in 100ms frames shows exactly when
  a held note dies away and when the next line starts. Use it to check the lyric timings and to
  place fades. In the example, "hand" starts at 80.66s and is held until 82.1s, so the pot-out
  runs 80.7 → 82.0.
- **Key and tempo:** `librosa` (`beat.beat_track` + chroma key-profile matching) is good enough
  to confirm a brief's BPM/key claims and spot modulations. librosa often reports double tempo,
  so halve it when it looks too high.

### 4. Build the audio

```bash
python scripts/medley/make_medley.py audio path/to/spec.json
```

This writes `out/medley_{instrumental_with_backing,instrumental_clean,guide_with_vocals}.{wav,mp3}`
and `out/timeline.json` (each section's source and medley times).

- Each section's gain is set so the section itself measures -18 LUFS. That's measured on the
  section, not the whole song, which matters for quiet intros. The full medley is then run
  through `loudnorm` to -14 LUFS with peaks no higher than -1 dBTP. In the example, all five
  sections ended up within 1.6 LU of each other.
- WAV is 24-bit / 44.1 kHz. MP3 is 320k.
- **Listen to the guide (`guide_with_vocals`) first.** Bad transitions are much easier to hear
  with vocals in.

### 5. Render the video

```bash
python scripts/medley/make_medley.py video path/to/spec.json --resolution 360p  # quick check
python scripts/medley/make_medley.py video path/to/spec.json                     # 4K final
```

This writes `out/<title> (Karaoke <res>).mp4` (instrumental with backing vocals),
`out/<title> (With Vocals <res>).mp4` (same picture over the original-vocals medley, for
checking sync) and `out/<title> (Karaoke).lrc`. A 4K render of a 5-minute medley takes a few
minutes on an M-series Mac.

What it does:
- Loads each job's reviewed lyrics the same way the render worker does: `CorrectionResult` +
  `CorrectionOperations.update_correction_result_with_data`.
- Keeps the lines that **start** inside each cut and shifts them by
  `medley_offset - source_start`. IDs get a section prefix so a song can appear twice.
- Runs `CountdownProcessor`, which adds gen's "3… 2… 1…" plus 3s of padding if the first word
  starts within 3s, and `VideoGenerator` title/end PNGs with the spec's title/artist.
- Renders with `OutputGenerator`, then joins title (5s) + karaoke + end (5s) with ffmpeg.

**Check sync:** the vocals in the guide video should sit exactly *title + countdown* seconds
(8.0s in the example) after their position in the medley WAV. Cross-correlating a 10s window
confirms it in seconds, and spot-checking a few frames confirms the right line is on screen.

### 6. Deliver

Give the customer the audio file they asked for (usually `medley_instrumental_with_backing.wav`
or `.mp3`; offer `instrumental_clean` as an alternative) plus the karaoke video. The per-song
gen jobs are a free extra: four full karaoke tracks.

### Guitar chords (optional)

The video can show guitar chords. Each chord name sits above the word where the chord
changes and lights up when played, and a "now / next" box in the top-right corner shows
the current chord all the time, including intros and instrumental breaks. First delivered
in October 2026 for the country medley (146 chord changes over 8 minutes). Detection and
rendering together took about 15 minutes of machine time once set up.

#### Checklist (from zero)

```bash
# 1. One-time: ChordMini in its own venv (pinned: the commit we evaluated)
git clone https://github.com/ptnghia-j/ChordMini ~/ChordMini
git -C ~/ChordMini checkout aa6e3a8
cd ~/ChordMini && uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt

# 2. Per medley (karaoke-gen checkout, env where karaoke_gen imports + audio-separator on PATH)
export PYTHONPATH=$PWD MEDLEY_CHORDMINI=~/ChordMini
export MEDLEY_GCLOUD_ACCOUNT=claude-readonly@nomadkaraoke.iam.gserviceaccount.com
#    add  "chords": true  to the spec, then:
python scripts/medley/make_medley.py fetch  spec.json   # if not already fetched
python scripts/medley/make_medley.py chords spec.json   # ~1-2 min/song: stems + 6 model runs
python scripts/medley/make_medley.py audio  spec.json   # if not already built
python scripts/medley/make_medley.py video  spec.json --resolution 1080p   # check frames first
python scripts/medley/make_medley.py video  spec.json   # 4K final
```

- **Check it before delivering.** Watch the `With Vocals` video for a verse or two of each
  song (chords should change with the band), and grab a few frames to check layout: one in a
  verse, one on an `INSTRUMENTAL` screen (the corner box should show chords there), one in a
  chorus.
- **Fixing a chord.** Edit `work/<job_id>/chords.json` (list of
  `{"start", "end", "label"}` in that song's own source seconds) and re-run `video`. If a
  customer sends their own chord chart, enter it the same way: their chords replace the
  detected ones, and nothing else changes.
- **Deliver both versions** (with and without chords), and say the chords are auto-detected.

#### A single song, not a medley

The same pipeline adds chords to any one gen track: write a one-section spec
(`{"job_id": "...", "start": 0, "end": null}`) and run `fetch`, `chords`, `audio`, `video`.
The output is the job's normal karaoke video plus chords (re-rendered locally from its
reviewed lyrics and theme).

#### How detection works (`scripts/medley/detect_chords.py`)

We compared the open chord-recognition models in October 2026.
[ChordMini](https://github.com/ptnghia-j/ChordMini) (MIT) had the best published accuracy
with downloadable weights: its continual-learning BTC checkpoint scores about 80%
majmin/MIREX on a 120-song test set (arXiv 2602.19778, Table 3). ChordFormer
(arXiv 2502.11840) reports similar numbers but has no released weights; autochord and
Chordino are older and weaker (25-class / ~67%). The script:

1. Runs **both** ChordMini checkpoints (BTC weight 1.1, 2E1D weight 1.0) on **three
   inputs**: the full mix, gen's clean instrumental (vocals removed, weight × 1.2) and a
   "harmonic" mix of htdemucs_6s guitar + piano + bass + other (drums removed too; cached
   as `work/<job>/harmonic.flac`). Removing vocals before chord recognition is a known
   accuracy win (APSIPA 2025).
2. Simplifies every label to a guitar chord (major, m, 7, maj7, m7, sus2/4, dim, aug,
   m7b5, dim7; no inversions), then takes a weighted vote on a 50ms frame grid.
3. Merges changes shorter than 0.6s into their longer neighbour, and snaps the remaining
   changes onto librosa beats when one is within 0.2s.
4. Spells roots for the local key, estimated over a 20s window: A♭ / D♭ / E♭7 in A-flat,
   F♯m in D, and borrowed ♭III / ♭VI / ♭VII stay flat in sharp keys.

**Accuracy evidence.** Run on the five country songs, each pair of model/input runs agreed
on 91–99% of major/minor labels (93–99% for the instrumental-vs-harmonic pairs), and the
output was the expected progressions (G–C–D7 then A♭–D♭–E♭7 after the half-step key
change; F–B♭–C with a II chord; D–F♯m7–G–A). That's a consistency check, not ground truth.

**Re-evaluating (if a better model appears).** Run each candidate on the same inputs, write
`.lab` files and compare pairwise with `mir_eval.chord` (`majmin` and `sevenths` weighted
accuracy). Better still, compare against a hand-checked chart for one song. Add a model by
extending `MODELS` in the script.

**Safety.** Load only `btc_model_best.pth` and `2e1d_model_best.pth`. Both load with
PyTorch's safe `weights_only=True`; the third bundled checkpoint
(`btc_model_large_voca.pt`) needs unsafe unpickling and isn't used.

#### How rendering works (in gen)

`karaoke_gen/lyrics_transcriber/output/ass/chords.py`, used via
`OutputGenerator.generate_outputs(chords=[ChordChange(start, end, label)])`. With no chords,
output is byte-identical to before.

- **Layout.** Each lyric slot gets a chord row above the line: the slot grows by 1.05 ×
  the chord font size, and lines per screen are capped at 4. It's the same mechanism as
  translated lyrics (which add a row below).
- **Which line.** A change belongs to the line being sung, from 0.6s before its first
  word to the end of its last word. The chord already ringing when a line starts is
  repeated over its first word, so every line shows what to play. Changes between lines
  appear only in the corner box.
- **Which word.** A change sits over the word sung at that moment, or the next word if it
  comes up to 0.25s early (a pickup strum). Quick changes on one word are pushed right so
  they never overlap. Positions use the renderer's libass-matched text measurement.
- **Highlight.** Each chord is a one-syllable karaoke event (`\k` wait, then `\kf`):
  upcoming colour, then the sung-lyric colour once played.
- **Medley handover.** In the medley script, each section's chords are clipped to the
  section, handing over to the next song halfway through each crossfade, then shifted for
  the 3s countdown.
- **Theme keys** (optional, `karaoke` block):
  - `chord_color`: upcoming chord colour, default gold `255, 223, 107`.
  - `chord_font_size`, or `chord_font_ratio` (default 0.55 × lyric size).
  - `chord_max_visible_lines` (default 4).

There's no gen job/UI option for chords yet: it's agent-run through this script. A product
version would need a job flag, detection in a worker (ChordMini is small and CPU-friendly),
and a chord editor in review.

## Making it sound good

- **Cut where the next vocal starts, not where the last one ends.** A section should run until
  just before the next line of its song, so the crossfade uses the instrumental tail (the last
  chord ringing out) rather than chopping the band mid-phrase.
- **Use the next song's intro as the bridge.** Starting the next section at 0:00 lets its
  intro (guitar pickup, fiddle intro) play under the crossfade, and the singer gets a few bars
  to find the new key.
- **Different keys and tempos are fine.** Medleys jump keys all the time (the example goes
  G → C → D → F → G/A♭). A 2s equal-power crossfade on a sustained chord sounds like a
  transition. Don't time-stretch to match tempos. It sounds digital, and briefs often ask you
  not to.
- **A pot-out/pot-in is the strongest transition.** A fast fader drop (about 1.3s) on a
  held word, a short silence (0.5–1s), then a quick fade-in (about 0.5s) on a distinctive
  instrumental phrase. It's the natural way to reset tempo or mood before a finale.
- **Check the brief against the recording.** Customers describe the arrangement from memory
  or from a karaoke track. In the example, the brief asked for an "instrumental intro → verse 1
  → chorus 1" on a song whose original starts singing at 0.4s and has no chorus until the third
  verse. Measure, then confirm the interpretation with the customer.
- **Gain-match per section, then normalise once.** Don't run `loudnorm` on each section
  separately (it pumps); set static per-section gains, then process the whole medley once.

## Gotchas

- Pre-review lyric timings are fine for *choosing* cut points (check them against the
  lead-vocal stem). Only the render needs the reviewed `corrections_updated.json`.
- Lines are kept by **start** time. A line that starts just before a cut belongs to the
  previous section, so place cuts in gaps between lines.
- The audio file has no title card or countdown; it starts straight into the song. Only the
  video gets the 5s title and 3s countdown. Mention this when delivering both.
- Bulk auto-select left every job (8 of 8 across two batches) at `awaiting_audio_selection`,
  even with exact lossless matches. Plan to pick sources by hand.

## Future: self-serve medleys

Tracked in the workspace `BACKLOG.md` ("Self-serve medley builder in gen"). The building blocks
already exist: per-song jobs, sample-aligned stems, reviewed lyrics, `OutputGenerator`. A
product version would mainly need a UI for picking sections (a waveform with lyric lines to
snap cut points to) and a backend job type that runs the `audio` + `video` steps.
