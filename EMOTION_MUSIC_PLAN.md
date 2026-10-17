# Music and effects that follow the story's emotion (plan only)

17 Oct 2026. Nothing in the code or settings was changed for this plan.
**Revised 17 Oct ~04:30 with your answers:** at most 3 tracks per story and as few track changes as possible;
no editing during a run (the AI decides within fixed limits; any fixing is done by hand after the episode);
Gyaan is like Sukoon (one good track, no changes); the death line gets silence, then one soft low note; before
fetching more music, mine the scripts already made to find which music and effects are missing.
Code read on the PC (E:\Projects\hub_chatgpt, after the 17 Oct tools.py split): `app\steps\mix.py` (`shape_mix`),
`app\steps\genre.py` (`horror_sound`, `_horror_track`, `_usable_music`), `app\steps\scenes.py` (`horror_scenes`,
`_beat_parts`, cue words), `app\steps\script.py` (`mark_emotions`), `tools.py` (`GENRES`, `DIRECTION`, `SCENES_ASK`),
`feedback.py` (ratings wrappers), `audio_screen.py`, `sound_library.py`, `word_times.py`, `tinker.py`, plus real files
from `D:\MukhtalifGPT\Series\kisse\Episodes\Kisse-Fathers-Old-Tin-Box` and `D:\MukhtalifGPT\audio_index.json`.

## 1. What you said, and how I read it

> "I like 1 and 3rd idea, we could use them in stories as story demands how heavy emotion is"

That fits two lists from the chat:

| | Idea 1 | Idea 3 |
|---|---|---|
| **List A** (every series) | a different track per story section, 3 to 4 s crossfades, sections made by merging neighbouring scenes with the same mood, at most 3 to 4 tracks | one track whose intensity follows the feeling (louder, lower, brighter, quieter) |
| **List B** (Kisse stingers) | music cuts to silence for about 1 s before a twist line | music comes back warmer, or in another mood, right after |

**What "both together" means:** in the same video, List A sets the music for the whole story (which track plays,
how loud and bright it is), and List B adds a short accent only at the two or three heaviest lines (music stops
just before the line, then comes back warmer). Your answer about tracks suggests you meant List A (1 = a track
per section, 3 = intensity changes). Confirmed 17 Oct: you want both lists (all four ideas).

**Best fit: both, as two layers driven by one "how heavy is it here" number.**
"As story demands how heavy emotion is" describes a strength dial, and both lists need that dial. List A is the
base that runs under the whole story (which track, how loud and bright). List B is the accent that fires only at
a few heavy lines (silence before, changed return after). A mild twist gets a small dip, a death line gets the full
drop-out of music and effects for 2 to 3 s, then silence or one low soft note. If you only meant one list, the
plan still works: each layer has its own on/off switch per series.

## 2. Where the emotion data comes from today

Nothing in the pipeline says "this part is heavy" yet. These are the pieces we can build that from:

| Source | Where | What it gives | Limits |
|---|---|---|---|
| Emotion marks per line | `steps\script.py mark_emotions`; tags like `[उदास]` at the start of lines in the episode's `script.txt` | one of डर, फुसफुसाहट, रहस्य, उदास, प्यार, गुस्सा, उत्साह per line | **no strength.** The model marks most lines: in Kisse-Fathers-Old-Tin-Box 46 of ~60 lines carry a tag (21 उदास, 14 प्यार, 8 रहस्य). `timing.json` drops the tags, so lines must be matched back by text. |
| Director scenes | `shots.json` from `horror_scenes` | per scene: `start`, `end` (scenes end at a sentence end, ~30 s), `happens` (one English line), `hit` (a scary turn), `moment` (an object carrying strong emotion), `lightning`, `cues` | **no mood or weight field.** `hit` and `moment` are the only "this is big" signals. |
| Word times | `words.json` next to `narration.wav` (Whisper, Experimental) | the exact time of every spoken word | Whisper sometimes skips chunks; `word_times.py` re-transcribes gaps. Fallback is the line start in `timing.json`. |
| Story summary | `01_Script\story.json` (`summary`, `recipe`, telling) | the story's shape (twist ending, loss, reveal) | one per story, not per moment |
| The voice itself | `narration.wav` | loudness and pauses per line; `shape_mix` already measures speech vs gaps in 0.5 s frames | weak signal for feeling, but exact for "where can music rise without covering the voice" |
| Series character | `tools.GENRES` (`music` folders, `mood`), `tools.DIRECTION` | which mood folders a series may use; where its lines land | per series, not per story |

**Today's sound path** (Experimental genre job): `horror_sound` picks ONE track from the series' mood folders
(`_horror_track`, with ratings via `feedback.pick_track`), checks it is audible (`_usable_music`), and `shape_mix`
lays it under the voice at one fixed level (`story_db`, about -18 dB, or the Create form's music level), with a
loudness rise before and after the voice and 3 s loop crossfades. Then per-scene ambience loops (fixed -22 dB
under the overall level), rare `hit`s, and word-timed cues are added, and the whole mix goes through a one-pass
`loudnorm` (I=-16, LRA=11). There is no ducking, no per-section track and no silence anywhere.

## 3. The plan in one picture

```
script + emotion marks ─┐
shots.json (scenes) ────┼─> [new] emotion map step ──> emotion_map.json ──> [new] emotion mixer ──> narration_music.wav
words.json (word times) ┘    (AI + rules + caps)       (log only, no edits) (sections, curve, stingers)
```

### 3.1 The emotion map (one new Experimental step, after the scene plan, before the sound)

New file `app\emotion_music.py`, step `exp_emotion_map`, output `emotion_map.json` in the episode's scenes folder.
It holds, for the whole story:

```json
{"sections": [{"from_scene": 0, "to_scene": 3, "start": 0.0, "end": 101.2, "mood": "Sad", "weight": 1}],
 "curve":    [{"scene": 0, "weight": 1}, {"scene": 1, "weight": 0}],
 "turns":    [{"words": "फिर कभी नहीं", "kind": "death", "weight": 3, "at": 312.4, "line_end": 315.0}],
 "source": "ai+rules"}
```

**Weight scale (the one dial everything uses):**

| Weight | Means | Example |
|---|---|---|
| 0 | calm, telling, background facts | "मेरे पिता एक साधारण इंसान थे" |
| 1 | mild feeling: a memory, unease, tenderness | rain on the roof as a child |
| 2 | strong: a reveal, a scare, a confession, a realisation | finding the notes in the tin box |
| 3 | heavy: death, betrayal, the final twist, a loss that lands | "और माँ... फिर कभी नहीं लौटी" |

**How it is decided (AI marks, then rules, then you):**

1. **AI marks.** One ask to the director model (`director_model`, gemma4:12b-it-qat) with the scene list (`happens` +
   text + the emotion tags already on those lines). For each scene it returns a mood from the series' own list and a
   weight 0 to 3; for the story it returns at most a few "turn" lines (`words` copied exactly in Devanagari, `kind`
   = twist / reveal / death / scare / relief, weight). The same safety used for cue words applies: `_words_in`
   checks the words exist in that scene's text, else one re-ask, else the turn is dropped.
2. **Rules on top** (in code, so the model cannot overdo it):
   - smooth the curve: weight can change by at most 1 per scene, except at a turn;
   - **budget per series**: e.g. Kisse at most 1 weight-3 turn and 3 weight-2 turns per story, Bhay 1 and 5;
   - weight 3 only if `kind` is death / final twist / betrayal, or the line sits in the last 20% of the story;
   - `hit` scenes get at least weight 2 (Bhay, Rahasya); a `moment` scene gets at least 1;
   - **fallback with no AI answer**: mood = the most common emotion tag in the scene mapped to a folder
     (उदास→Sad, प्यार→Love, डर→Dark/Tension, रहस्य→Tension, उत्साह→Happy, सामान्य/none→the series' calm folder),
     weight from tag density plus `hit` / `moment`. The video still gets made.
3. **Sections, with as few track changes as possible**: neighbouring scenes with the same mood merge. A track
   change is only allowed when the mood really changes AND the new mood lasts at least `min_section_s` (default
   90 s); a short mood swing stays on the current track and is handled by the intensity curve instead. **Never
   more than 3 tracks in a story** (hard cap, `max_tracks` = 3), so at most 2 changes, and a story never goes back
   to a track it already left (no A → B → A jumping). Fewer is better: a story whose mood barely moves keeps one track.
4. **No editing during a run.** The pipeline never stops to ask: the AI decides, the code limits above keep it in
   check, and the job goes on. `emotion_map.json` is only written down so the job log can show what was decided
   and why. Any fixing is done by you after the episode is made, by hand on the narration or the mix.

### 3.2 Track choice per mood (List A, idea 1)

- Each section's mood maps to a BGM folder, limited to that series' own folders in `tools.GENRES[...]["music"]`
  (Kisse: Sad, Love, Calm; Bhay: Dark, Tension, Eerie...). A mood a series does not allow is mapped to the nearest one.
- Inside a folder the pick goes through the **existing** `tools._horror_track` → `feedback.pick_track` path, so
  rejected tracks stay out, liked tracks get their bonus and least-used still wins ties. Every pick goes through
  `_usable_music` and `audio_screen` exactly as today (rumble-only tracks rejected, risky tracks treated).
- New: the heavier the section, the more the pick prefers tracks with higher `dynamics_db`, `brightness_hz` and
  `tempo_bpm` from `audio_index.json`; calm sections prefer high `calm`. This is a sort key, not a filter.
- No two sections share a track unless the folder has nothing else; then the same track continues and only the
  intensity curve changes (so a thin folder never stops a video).

### 3.3 Crossfades, the intensity curve, ducking, and silence

All of this happens in one new mixer function in `emotion_music.py`, called by `horror_sound` in Experimental runs
in place of `shape_mix`. `shape_mix` itself stays as it is, so Sukoon and Working do not change.

**Crossfades (A1).** At each section boundary, find the speech gap (from the 0.5 s voice frames or `words.json`)
nearest the boundary within ±5 s. The new track fades in under the old one over `crossfade_s` (default 3.5 s),
centred on that gap; the outgoing track also gets a gentle low-pass over the fade so two melodies don't clash.

**Intensity curve (A3).** The music gain follows the weight, smoothed over 2 to 4 s:

| Weight | Under speech | In speech gaps > 1 s | Brightness |
|---|---|---|---|
| 0 | story level −2 dB | +2 dB | low-pass ~3 kHz (warm, dull) |
| 1 | story level | +3 dB | ~6 kHz |
| 2 | story level +1 dB | +4 dB | open |
| 3 (before the drop) | story level +1.5 dB | +5 dB | open |

All values are multiplied by the series' `strength` (Tinker). **This also brings in real speech ducking**: today
the level is flat, so a louder heavy section would cover the voice. Music only rises in gaps and settles back
0.3 s before the next word. Intensity is never allowed to beat the `audio_screen` clarity limit.

**Stingers (List B) scaled by weight.** For each turn line:

| Turn weight | Before the line (B1) | After the line (B3) |
|---|---|---|
| 1 | music dips 6 dB for ~0.8 s, no silence | back over 1.5 s, same track |
| 2 | music fades to silence over 0.4 s, ending 0.3 s before the first word; ~1 s of silence; ambience stays at half | returns over 2 to 3 s, softer start; Kisse/Romanch may switch to the "return" mood (e.g. Sad → Calm warm) |
| 3 (death, final twist) | **music, ambience and effects all drop out** for 2 to 3 s; only the voice is heard | one low soft note (made in code from sine waves, like the subscribe chime, so nothing to license) or silence, then the music returns after `return_delay_s` in the return mood |

- The voice is never touched: drops act only on the music, ambience and effects.
- Cues and hits that fall inside a drop window are skipped, unless the cue is the sound the line itself describes.
- The `at` time comes from `words.json` (exact word); if Whisper skipped that word, the line start in
  `timing.json`; if neither matches, the turn is dropped (better no stinger than a silence in the wrong place).

**Loudness at the end.** Today's one-pass `loudnorm` (LRA=11) works like a slow compressor and would partly undo
the dips and lifts and lift the floor in a drop. The emotion mixer should use two-pass loudnorm (measure, then
`linear=true`) so the shape survives and the episode still lands at −16 LUFS.

### 3.4 Per-series defaults

| Series | Sections (A1) | Curve (A3) | Stingers (B) | Heavy drop (weight 3) | Return mood |
|---|---|---|---|---|---|
| **Bhay** | on, up to 3 (Dark / Tension / Eerie) | on, strength 1.0 | on: silence before the scare, the `hit` lands after | full drop 2 s, then a low drone note | Tension |
| **Rahasya** | on, up to 3 (Tension / Dark) | on, 0.8 | on: before a clue, a name, the twist | full drop 2 s | Dark |
| **Kisse** | on, up to 3 (Sad / Love / Calm, Happy if the folder exists) | on, 0.7 (gentle) | **main target**: silence before the twist, warmer return | full drop 2.5 s, one soft low note, then Calm | Calm / Love |
| **Romanch** | on, up to 2 (Love / Calm) | on, 0.5 | only weight 2 at a confession, ~1 s dip, no full drop | off | Love |
| **Gyaan** | **like Sukoon: one good track, no changes** | off | off | off | — |
| **Kareeb** (ASMR) | off | off | off | off ("nothing sudden") | — |
| **Sukoon** | **off: stays on today's `shape_mix`, one calm track, untouched** | off | off | off | — |

### 3.5 Tinker settings

A new Tinker table `emotion_music.SERIES` (same mechanism as `sfx_lib.SERIES`, snapshotted per job, can be
installed into Working later) with, per series:
`on`, `sections`, `max_tracks` (3, can only be lowered), `min_section_s` (90), `crossfade_s` (3.5), `curve`, `strength` (0 to 1.5),
`gap_lift_db`, `stingers`, `budget` (max turns per weight), `drop_s` per weight (0 / 1 / 2.5), `soft_note`
(on/off), `return_mood`, `return_delay_s`.
Plus one Tinker prompt `emotion_music.MAP_ASK` (the emotion-map question) and one model key `emotion_model`
(defaults to `director_model`) added to `tinker.CFG_KEYS`.

## 4. Testing without the GPU

Everything after the map is numpy + ffmpeg, which runs on the CPU. Only the voice, pictures and the map ask need models.

1. **Synthetic test** (`tests\test_emotion_music.py`, runs in seconds): a fake voice (tone bursts with gaps), two
   short fake tracks and a hand-written `emotion_map.json`. Checks: the voice in the output equals the input
   voice (subtract and measure); a drop window is below −60 dB apart from the voice; crossfades last the set time
   and sit in a gap; music never rises while a word is playing; no clipping; final loudness −16 ±1 LUFS.
2. **Real episodes, sound stage only**: run the map + mixer on existing episodes that already have
   `narration.wav`, `words.json` and `shots.json` (Kisse-Fathers-Old-Tin-Box, Kisse-1016-2155, the Bhay
   episodes, Rahasya-Silent-Killer-Dharkot). The map ask can run on gemma4:12b while ComfyUI is idle, or be
   replaced by a saved reply. Output goes to a side file (`narration_music_emotion.wav`) so the old mix stays.
3. **A report picture** per episode: the music gain over time with section names and turn markers on top of the
   voice waveform (PNG next to the mix), so you can see where it dips before you listen.
4. **Your ears and ratings**: play old vs new on the Ratings page and rate `bgm`; the AI self-review's existing
   `clarity` aspect checks voice-vs-music per line.
5. `tests\smoke_tools.py` still passes (the mixer is a new file; `shape_mix` and its feedback wrapper are unchanged).

## 5. Risks

| Risk | Why it matters | What to do |
|---|---|---|
| **Thin music library** | `audio_index.json` lists Calm 14, Sad 16, Dark 9 (some rejected as rumble), Happy 7, Love 7, Tension 3, angry 2, **Eerie 1**. No "warm / hope" folder for Kisse's return. Bhay with 3 sections would reuse Tension and Eerie tracks quickly. | Same track + curve when a folder is empty; step 2 below mines the existing scripts to list exactly which moods and effects are missing, then fetches them (Sounds page, CC0). The index may be older than recent downloads: rebuild it first. |
| **The AI marks everything heavy** | the marks already tag ~75% of Kisse lines | the code budget and weight rules decide, not the model; weight 3 needs a death/final-twist kind |
| **Silence in the wrong place** | a 2 s gap at a random spot sounds like a bug | turns need an exact word match in `words.json` / `timing.json`; no match = no stinger; drops at most 3 s and the voice always continues |
| **Track clash at a crossfade** | two keys or tempos at once | fade in a speech gap, low-pass the outgoing track, crossfade ≥3 s |
| **loudnorm flattening** | one-pass loudnorm squeezes the dynamics we just made | two-pass linear loudnorm in the new mixer |
| **Ratings and screening** | the Ratings page and `music_shape.json` assume one track; a `bgm` rating counts 0.5 to "the track used" | `music_shape.json` gets a `tracks` list with times; a video's bgm rating is split across its tracks; every track is screened by `audio_screen` like today |
| **Licence credits** | more tracks per video means more CC BY credits | credits list built from all section tracks; CC BY stays opt-in as now |
| **Reels** | `genre_reels` starts reels just before the strongest lines, which are often the turn lines, so a reel could open in a silence | reels get a short music lead-in, or the reel start moves 2 s earlier |
| **The feedback wrapper** | `feedback.install` wraps `shape_mix` with a fixed argument list | the new mixer is a separate function; `shape_mix` keeps its signature |
| **Runtime** | one extra director-model ask (~1 to 3 min on gemma4:12b with thinking on); mixing adds seconds | the ask is batched like `mark_emotions` (`EMO_BATCH`) so it does not overflow the context |
| **Old episodes** | their `shots.json` has no mood | the map step builds from what exists, so old episodes can be remixed without re-planning |

## 6. Suggested order of small steps

Each step is Experimental only, behind its own Tinker switch, with a backup, a CHANGELOG row and a listen
before the next one.

1. **Emotion map only** (no sound change): `exp_emotion_map` writes `emotion_map.json` and prints sections and
   turns in the job log. Run it on 3 existing episodes; you read the log and say if the heavy moments are right.
2. **Mine the scripts already made, then fetch what is missing** (your answer 6). A one-off script, no GPU, no
   change to the pipeline:
   - read every script and plan already made: `D:\MukhtalifGPT\Series\*\Episodes\*\01_Script\script.txt`,
     `story.json`, `shots.json` (ambience, hits, cues the director asked for), the `scripts` folder and
     `story_ledger.json` (stories written but not voiced);
   - run the emotion map ask on them (CPU-friendly, no pictures or voice) to count, per series, how many minutes
     of each mood the stories need and how many sections and turns they have;
   - count the effects and ambience the director asked for that were missing or rejected (cue names with no
     file, `sfx_lib.missing`, files blocked by ratings);
   - compare with the library (`audio_index.json` rebuilt first, ratings, `audio_screen` verdicts) and write a
     shopping list: e.g. "Tension: need 6 usable tracks, have 2", "Warm/Hope: new folder, need 5";
   - fetch that list through the existing Sounds page downloader (Openverse + Freesound, CC0/PDM only, by mood
     with `MOOD_QUERIES`; a new Warm/Hope query gets added), each new file screened by `audio_screen` as today.
     You see the list before anything is downloaded.
3. **Ducking + intensity curve on one track** (A3): new mixer, same single track, music lifts in gaps and follows
   the weight. Side-by-side file on old episodes.
4. **Stingers scaled by weight** (List B), if you want it after the question below: the dip / silence / full drop
   with ambience and effects, the soft note, the return. Kisse first, then Bhay and Rahasya.
5. **Sections + crossfades** (A1): per-mood track picks with ratings and screening, hard cap of 3 tracks, no
   jumping back, rating split across tracks.
6. **Tinker table** for the per-series settings, then **tune per series from your ratings**, then decide whether
   any of it goes into Working.

## 7. Your answers (17 Oct) and what is still open

| Question | Your answer | In the plan |
|---|---|---|
| Tracks per story | at most 3, and avoid jumping tracks | hard cap 3; a change needs a real mood change lasting ≥ 90 s; never back to an earlier track |
| Death line | ok (silence, then one soft low note) | as in 3.3 |
| Who sets heaviness | can't edit mid-run; fix by hand after the episode if needed | AI + code limits, no pause in the pipeline, no edit table |
| Gyaan / Kareeb | Gyaan is like Sukoon, one good track is enough | Gyaan, Kareeb, Sukoon: one track, no changes |
| More music | look back at the scripts already made, then get the music and SFX | step 2 |

**Settled (17 Oct ~04:33):** "1 and 3" means both lists, all four ideas: a track per section + intensity changes
(List A) and silence before a twist + a warmer return (List B). Romanch keeps its lighter setting from 3.4 (small dip
at a confession, no full drop). Nothing is open. First step: step 1 (emotion map only, no sound change).

## 8. Built (17 Oct 2026, Experimental only; tested on copies, not yet in a hub run)

| Step | Where | State |
|---|---|---|
| 1 Emotion map | `app\emotion_music.py` (`build`, `step`), step `exp_emotion_map` | commit `310256e`; marks-only and saved-answer maps on 4 episodes |
| 2 Missing sounds | `app\sound_needs.py` -> `Activity\Reports\sound_needs_*.md` | commit `0459ae8`; first report: Eerie short 5, Tension short 5, Love short 2 |
| 3-5 Mixer | `emotion_music.mix`, called by `steps\genre.py horror_sound` | commit `31feb68`; `tests\test_emotion_music.py` 28/28, `tests\emotion_on_copy.py` on Kisse and Bhay copies |
| 6 Tinker | table `emotion_music.SERIES`, prompt `emotion_music.MAP_ASK` | commit `31feb68`; override checked, cap of 3 holds |

Next: restart the hub, run one Experimental Kisse episode, listen, rate the bgm; fetch Eerie / Tension / Love tracks on the Sounds page.
