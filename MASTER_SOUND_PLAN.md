# Master sound plan: music, ambience and sound effects (17 Oct 2026)

One ordered plan that merges every sound plan in the project. **Plan only: no app code, settings or library file
was changed for it.** Sources merged:

| Source | What it holds |
|---|---|
| `EMOTION_MUSIC_PLAN.md` | music that follows the story's emotion: emotion map, sections, intensity curve, stingers. Built 17 Oct (commits `310256e`..`2bf2571`), tested on copies only |
| `sound_code_crosscheck_20261017.md` | what the sound code really did in 32 finished episodes, root causes, fix order |
| Ambience/SFX fix thread, test copy in `E:\Projects\hub_chatgpt\_tmp\soundfix_20261017` | fixes to `steps\scenes.py`, `steps\genre.py`, `self_review.py` (not live) |
| `sound_needs_20261017.md` + crosscheck section 8 | what the library lacks |
| Section 3 below (new) | measuring each music track once and matching tracks to story sections by fit |

## Status on 17 Oct ~15:50 (sections 2 and 4 below are as written at ~13:20)

| Build step | State now |
|---|---|
| 1 ambience rules (run limit 150 s, no bed in inner-thought scenes) | built into `steps\scenes.py`; no real run has had a bed yet |
| 2 ambience / SFX fixes into the live app | built, live since the restart; Blue Ink Diary still got no effects and no bed, two more fixes written after it (category pick, second effects ask), not live yet |
| 3 emotion mixer clarity guard | **not built** |
| 4 mood folder, per-series counter, -18 dB | built, **ran live** in Blue Ink Diary |
| 5 restart + first live runs | **one Kisse run** (Blue Ink Diary); no Bhay yet. Lighter-return and silence fix from it not live yet |
| 6 music analysis table | plan only |
| 7 library cleanup | script `tests\soundscape_move.py` ready, **not run**; 'atmo' category added in `sfx_lib.py`, `SFX\atmo` not there yet |
| 8-10 | not started |

## 1. Your decisions so far (fixed, the plan follows them)

| Decision | Date |
|---|---|
| A track is chosen by **fit first**; "least used" only breaks ties among good fits | 17 Oct |
| Your ratings stay on top: a rejected or low-rated track is never picked, a liked one is preferred | 16-17 Oct |
| At most **3 tracks** per story, a change only for a mood lasting 90 s or more, never back to an earlier track | 17 Oct |
| **Gyaan, Kareeb, Sukoon: one good track**, no changes inside the story | 17 Oct |
| Nothing is edited during a run: the AI decides within code limits, you fix by hand afterwards | 17 Oct |
| A heavy (weight 3) line: music, ambience and effects stop 2-3 s, then one soft low note | 17 Oct |
| **Everyday sound effects are shared across all series**; only strong mood sounds (stingers, scare hits, whispers, screams...) stay with the series that suit them | 17 Oct |
| `audio_screen` voice-clarity checks (rumble-only, covers the voice, piercing) still apply to every pick | 16 Oct |

## 2. Where each piece stands today

| Piece | State | In the live app? | Needs hub restart? |
|---|---|---|---|
| Emotion map (`emotion_music.py`, step `exp_emotion_map`) | built, committed `310256e` | yes, file is there | **yes**, not loaded yet |
| Missing-sounds report (`sound_needs.py`) | built, committed `0459ae8`, ran once | yes | no (run on its own) |
| Emotion mixer: sections, intensity, stingers, Tinker table | built, committed `31feb68`; 28/28 synthetic checks, copies of Kisse and Bhay | yes | **yes**, never ran in a hub job |
| Director picks checked against the series' own lists, dropped names logged | **test copy only** | no | yes, once copied in |
| One ambience bed per place, no fade dip at each scene, folder check | **test copy only** | no | yes |
| Shared everyday SFX vs series-only mood sounds | **test copy only** | no | yes |
| Effect words matched across lines + sense check before the mix | **test copy only** | no | yes |
| Hits only real stingers (braam, riser, impact, whoosh; Bhay and Rahasya) | **test copy only** | no | yes |
| Ambience: limit on how long one bed runs; no bed in inner-thought scenes | **not built** | no | yes |
| Emotion mixer: clarity guard; keep an effect that *is* the heavy line's sound | **not built** (crosscheck found both missing) | no | yes |
| Per-series music use counter | **not built** (one shared `bhay_music_used.json`) | no | yes |
| Music analysis table and fit-based pick (section 3) | **plan only** | no | the pick yes; the table no |

## 3. Music analysis: measure each track once, then match by fit

### 3.1 What the library has, and what the stories need

**Library now** (`D:\Audio asset\BGM`, 80 files): Calm 14, Dark 25, Eerie 1, Happy 7, Love 10, Sad 18, Tension 3,
angry 2. Screening cache: 46 ok, 20 risky (treated copy), 9 rejected. `audio_index.json` was last built 14 Oct and
lists only 59 music files, so it is out of date.

**What the 32 episodes need** (from `sound_needs` and crosscheck section 8):

| Series | Minutes of each mood the stories need | What they got | Short |
|---|---|---|---|
| Bhay | Dark 8.3, Tension 3.6, Eerie for whisper lines | Dark 7 of 11 stories (biggest folder wins), Tension 3, Eerie 1 | Tension, Eerie |
| Rahasya | Tension 12.4, Dark 0.8 | Dark in 3 of 5; Silent Killer got a soundscape as its only music (so did Bhay's Incomplete Tea) | **Tension: 3 usable, need ~10** |
| Kisse | Calm 10.9, Sad 5.0, Love 1.6; a warm return after a heavy line | Sad / Love, one track per story | Warm/hope folder (or keep Calm) |
| Romanch | Love 9.2 | Calm | Love: 6 usable, need ~10 |
| Gyaan | one calm or bright track | Happy | enough |

So the gap is mostly **Tension (need 7 more), Eerie (4-5), Love (4)**, plus about 4-6 Dark "tracks" that are really
soundscapes or rumble.

### 3.2 Are the numbers we already have good enough? No.

`audio_index.py` already measures loudness, dynamics, brightness, busyness, tempo, beat strength, loop-ability and a
"calm" score, but only on the first 180 s, and the numbers barely separate the moods: the calm score is 0.76 in the
Calm, Dark *and* Sad folders, and the tempo often lands on double time (Calm median "156 bpm"). Nothing in the pick
uses them anyway (`emotion_music.pick_tracks` calls the least-used pick per folder).

**Test I ran today (read-only, 73 tracks, numpy only, 90 s from each, about 5 s in total):**

| Measurement | Separates the folders? (F ratio, higher = better) |
|---|---|
| busyness (note onsets per second) | **11.4, strong** |
| low-end share (energy below 150 Hz) | **4.5** |
| key strength (how tonal vs drone/noise) | **4.4** |
| tonal focus, loudness | 2.4, 2.9 |
| major/minor, tempo, brightness, loudness swell | under 2, weak with this simple method |

With those numbers alone, a track lands in its own folder only **24 of 73** times (8 folders), and in its family
(soft / warm / dark / tense) **40 of 73** times. **Conclusion: numbers cannot replace your folders.** The folder stays
the mood; the numbers give each track an intensity, a "does it fit its folder" check, and a soundscape flag.

### 3.3 What to measure (free libraries already on the PC; nothing to install)

Already on the PC:
- **Hub Python** (`E:\ComfyUI_windows_portable\python_embeded`): numpy 2.4.1, scipy 1.17.0, torch + torchaudio 2.10,
  transformers 5.0. No librosa, pyloudnorm or soundfile inside it.
- **Voice engine's side folder** `engines\voice\libs\cb` (put on the path by `voice\cfg.py`, Python 3.13 wheels):
  **librosa 1.0.0**, numba 0.68, scikit-learn 1.9.1, soundfile 0.14, soxr, audioread, **pyloudnorm 0.2.0**.
- `engines\english\libs\common`: scipy 1.18.1, pyloudnorm, soundfile.
- ffmpeg (`E:\Program_Files\ffmpeg`): its `ebur128` filter gives loudness and loudness range for free.

The script tries librosa from `libs\cb` first (better chroma and beat tracking) and falls back to plain numpy/scipy,
which the test above showed is enough and fast. The first build step checks the import with the hub's Python.

Per track, over the **whole track** in 10 s windows (not just the first 180 s):

| Number | Used for |
|---|---|
| loudness (LUFS) and loudness range | intensity; how much it swells |
| swell: slope and biggest quiet-to-loud rise | intensity; a calm opening for the first section |
| busyness (onsets/s), pulse strength, tempo class (slow / mid / fast; halving/doubling folded) | intensity; smooth crossfades (similar tempo class) |
| low-end share, brightness (energy-weighted, audible frames only) | dark vs warm; matches the existing low-pass curve |
| key strength, major/minor with a confidence | soundscape flag; a weak "sad vs bright" hint used only when confident |
| intensity curve (one number per 10 s) | later: start a section at a quiet part |

### 3.4 How the mood tag and intensity are derived

1. **Mood = the folder** (you sorted them, or they came from a mood search). The script never moves a file.
2. **Soundscape flag**: low key strength + few onsets + weak pulse, or a name with atmo / drone / rain / ambience.
   Today's test flags 16: Rain On Rooftop and an air tone in Calm, 13 in Dark (Haunted rainy street, Dark London
   Night Atmo, Dark Analog Drone, City Rain Dark, Space dark atmo, drone0019...), Deified in Tension. Flagged files
   are not picked as music; cleanup step 6 moves them to ambience with your OK.
3. **Intensity 0-3**: one arousal score (busyness, pulse, loudness range, swell, low end) ranked across the library,
   cut into quarters. 0 = still pad, 3 = driving, swelling track. This matches the emotion map's weight 0-3.
4. **Fit check**: distance from the folder's typical numbers. A track far from its folder and close to another gets
   `check: "sounds like Love"` in the table and on the Sounds page; nothing changes until you say.

**Accuracy check by ear** (12 tracks, the ones the numbers disagree with or that were used as music):

| Track (folder) | Why |
|---|---|
| Rain On Rooftop (Calm) | soundscape in a music folder |
| Haunted rainy street (Dark) | Incomplete Tea's only music |
| Dark London Night Atmo (Dark) | Silent Killer's only music |
| Échappé - Dan Bodan (Dark) | used 3 times; numbers say soft |
| Yacoby - Schwartzy (Dark) | used twice; numbers say warm |
| Deified - The Grey Room (Tension) | numbers say drone |
| Lonely Troutman II (Tension) | numbers say warm |
| Absention - Odonis Odonis (Eerie) | the only Eerie track |
| Torn - Patrick Jordan Patrikios (Love) | numbers say tense |
| Marigold - Quincas Moreira (Sad) | Tin Box music; numbers say warm |
| I'm Giving Up - Everet Almond (Sad) | numbers say tense |
| Spirit of Fire - Jesse Gallagher (Calm) | numbers say dark |

You listen and give each a 0-3 intensity and "fits folder yes/no". Target: intensity within 1 on at least 9 of 12,
and every soundscape flag right. If it misses, the weights are tuned on these 12, then 12 others are checked.

### 3.5 Where the table lives

`D:\MukhtalifGPT\music_profile.json` next to `audio_index.json` (a separate file, so the Sukoon music shape in
`audio_index.json` is never touched), plus `music_profile.csv` to open in Excel. One row per file, keyed by path +
size + date, so only new or changed files are measured again. Row: folder, kind (music / soundscape), the numbers in
3.3, intensity, fit check, measured date, method (librosa or numpy).

### 3.6 How it plugs into today's pick

One new function, `music_match.pick(cfg, series, mood, weight, avoid)`, used by `emotion_music.pick_tracks` (per
section) and by the Experimental `feedback.pick_track` (one-track series), in this order:

1. **Hard filters, as today**: blocked on the Sounds page, rated as rejected, `audio_screen` reject, harshness at or
   over `music_harsh_skip`, plus new: soundscape-flagged files.
2. **Fit**: `|track intensity - section weight|` plus the fit-check distance, and for a 2nd or 3rd section a small
   bonus for a tempo class close to the track before (smoother crossfade).
3. **Good fits** = within 0.5 of the best fit. A track you rated 4-5 joins this set if it is within 1.0.
4. **Ties**: your rating bonus, then **least used in this series**, then random.
5. Risky tracks still get the treated copy; `_usable_music` still swaps a rumble-only track.

**Per-series counter**: `D:\MukhtalifGPT\music_used.json` = `{series: {file: {count, last}}}`, started from
`bhay_music_used.json` (there are two copies, in `D:\MukhtalifGPT` and `Library`; the one in `media_dir` wins).
A track is also skipped if it was used in the last 3 episodes of that series when another good fit exists.

**One-track series**: Gyaan and Kareeb pick the single best fit for the whole story's average weight from their own
folders. Sukoon stays on its fixed `voice_music_default` track (Working mode, unchanged).

Without an emotion map (the map step failed, or a single-track series), the story's mood comes from the emotion
marks already in `script.txt` (`emotion_music.TAG_MOOD`, most common tag), so the folder is never "the biggest".

## 4. Build order (each step Experimental only, own backup, smoke test, one commit, one CHANGELOG row)

| # | Step | From | State now | Restart? |
|---|---|---|---|---|
| 1 | **Finish ambience rules** in the test copy: limit how long one bed runs without a break, no bed in inner-thought scenes | fix thread | not built | — |
| 2 | **Copy the ambience/SFX fixes into the live app**: series lists checked + dropped names logged + raw director reply saved; one bed per place + folder check; shared everyday SFX; cue words across lines + sense check; hits only stingers | crosscheck fixes 1-4 | test copy | yes |
| 3 | **Emotion mixer fixes**: clarity guard (music never above the `audio_screen` clarity limit), keep an effect that is the heavy line's own sound | crosscheck section 6 | not built | yes |
| 4 | **Music basics** (decision C: now): story-mood folder pick, so the biggest folder no longer wins; per-series counter; music level to -18 dB (decision A, settled) | crosscheck fix 5-6 | not built | yes |
| 5 | **Restart the hub, first live Experimental runs**: one Kisse, one Bhay. You listen and rate bgm, ambience, effects | emotion plan step "Next" | — | **this is the restart** |
| 6 | **Music analysis table** (section 3), run as its own script, read-only; you do the 12-track ear check | this plan | plan | no |
| 7 | **Library cleanup** (decision B, yes): soundscapes from `SOUNDSCAPE_MOVE_LIST.md` out of BGM into ambience once you confirm the list; then fetch Tension 7, Eerie 4-5, Love 4, (Warm 4), and the place beds (room tone, village night, street/market, temple, clean rain) on the Sounds page; re-run step 6 for the new files | sound needs + crosscheck 8 | not done | no |
| 8 | **Fit-based pick** (3.6) in Experimental, replacing least-used-first | this plan | plan | yes |
| 9 | Restart, one live run each for Kisse, Bhay, Rahasya, Romanch, Gyaan; rate; tune the Tinker table | emotion plan step 6 | — | yes |
| 10 | Decide what goes into Working | — | — | yes |

Why this order: step 2 must come before the first emotion run, or that run will put horror pads under a Kisse story
(crosscheck). The analysis (6) comes before cleanup (7) because its soundscape flags tell us what to move. The fit
pick (8) waits for the cleanup, so it does not rank soundscapes or choose from a 3-track Tension folder.

## 5. Risks

| Risk | What to do |
|---|---|
| Numbers misjudge a track's mood (test: only 24/73 match folders) | folder stays the mood; numbers only rank inside it; the 12-track ear check before the pick uses them |
| librosa from `libs\cb` fails under the hub's Python, or numba is slow on first import | numpy/scipy fallback (tested, ~5 s for 73 tracks); the analysis runs as its own job, never inside an episode |
| Thin folders: fit has nothing to choose from | fetch step 7 first; with one good fit, least-used cannot help and repeats continue, so the job log says so |
| A good fit is always the same track | per-series counter + "not in the last 3 episodes" rule |
| Two copies of the use counter | migrate once, keep the old files as backup |
| Restart while jobs are queued | restart only when the queue is empty (the split needed the same) |
| Sukoon or Working changes by accident | all new code checks `experimental_mode`; Sukoon path untouched; smoke test before and after |

## 6. Decisions

**A. Music level: settled 17 Oct at -18 dB** (your Sukoon level, 4 dB quieter than the -14 form default). It is applied
in build step 4 by changing the Create form default `music_db` from 14 to 18 in `create_form.DEFAULTS` (the Tinker
table) and the matching fallbacks in `ui\experimental\index.html` (Create form and sound re-run slider). The emotion
mixer's intensity curve still lifts the music in speech gaps. Episodes already made keep their level.

**B. Library cleanup: settled 17 Oct, yes, once you have seen the list.** The list is
`reviews/SOUNDSCAPE_MOVE_LIST.md` (12 sure, 5 already blocked, 5 maybe). Nothing moves until you confirm it; applied
in build step 7.

**C. Story-mood folder pick: settled 17 Oct, build it now**, before the analysis. It is part of build step 4 (with the
-18 dB default), which comes before the analysis in step 6. The ambience/SFX fix thread builds steps 1 to 4 in that order.

No decisions are open.
