# HubGPT project map

Last updated: 16 Oct 2026, ~23:55. Start here when you forget where something is.
Companion files in the same folder: `CHANGELOG.md` (what changed when), `KNOWN_PROBLEMS.md` (what is wrong,
what is right, what was decided), `CLEANUP_PLAN.md` (tidy-up plan; step 2 done 16 Oct, the rest still proposed).
Older long notes: `README.txt` (how to use the app) and `HANDOFF_HUB.txt` (history v1 to v4.3).

## In one sentence

HubGPT is one browser app on your PC (`hub.bat`, http://127.0.0.1:8781) that writes a script, reads it in
your own voice, adds music and sound effects, paints pictures with ComfyUI and renders a video and reels.

## Two modes

| | Working mode (the "stable" site) | Experimental mode |
|---|---|---|
| Address | http://127.0.0.1:8781/ | http://127.0.0.1:8781/experimental |
| Page file | `app/ui/index.html` | `app/ui/experimental/index.html` |
| Where outputs go | `D:\MukhtalifGPT` (+ `\Episodes`) | `D:\MukhtalifGPT` too (`Library`, `Series\<series>\Episodes\<episode>`, `Activity`, `Tinker`) since ~16:40 on 16 Oct; the old `D:\HubGPT` is only read |
| Job list | `D:\MukhtalifGPT\jobs.json` | `D:\MukhtalifGPT\Activity\jobs.json` (logs in `Activity\Logs`) |
| AI models | `config.json` / Settings (`llm` gemma3:12b, `vision_llm` qwen2.5vl:7b) | Tinker: 26B script writer, 12B director and picture prompts, gemma3:12b picture judge (see `CHANGELOG.md`) |
| New features | only when you "promote" or "Install into Working" | everything new from 16 Oct lands here first |

Rule kept so far: new features go into Experimental first; Working changes only when you say so
(`app/ui/experimental/AGENTS.md`).

## Folders

```
E:\Projects\hub_chatgpt
  hub.bat / restart_hub.bat / setup_hub.bat     start, restart (asks if a job runs), one-time setup
  config.json        all machine paths (paths) and folder names (layout); models (llm, prompt_model, vision_llm)
  secrets.json       Gemini / Pixabay / Freesound keys, script engine choice (never committed)
  README.txt         how to use the app (pages, folders, troubleshooting)
  HANDOFF_HUB.txt    developer history v1 -> v4.3 (13-15 Oct)
  PROJECT_MAP.md     this file
  app\               the hub itself (Python server + two web pages)  -> see "Files in app" below
  engines\voice\     your Hindi voice (Chatterbox Multilingual + your trained LoRA models in models\)
  engines\video\     the video renderer (Sukoon living art, genre videos, reels, subtitles), own .venv
  engines\english\   the English narrator (Qwen3-TTS) for English Sukoon episodes
  Code\              ALL_CODE.txt snapshots made by update_all_code.bat (for pasting into chats)
  *_old_<date>_<what>   backup of a file before a change, next to the file (old ones were cleaned on 16 Oct;
                     a `_backups\` folder, if present, is ignored by git)
  _tmp\              test scripts and results from AI sessions, e.g. model_test_20261016, vision_test_20261016
  logs\              hub logs
```

Media stays on D: (`D:\MukhtalifGPT`, `D:\SukoonVoice`, `D:\Audio asset`; old `D:\HubGPT` read only); code on E:.
Ollama's own log (useful when a step is slow): `C:\Users\Shail\AppData\Local\Ollama\server.log`.

## Files in `app\` (what each one does)

### Core (used by both modes)
| File | Size | What it does |
|---|---|---|
| `server.py` | ~1170 lines | The web server. Serves both pages, all `/api/...` routes, the job queue (one GPU job at a time + a light lane), restart/resume, git snapshot on restart. Runs each job step by calling `tools.<fn>` (or `experimental_workspace.exp_*`). |
| `tools.py` + `steps\` | ~625 + 12 files (split 17 Oct) | Every job step. `plan(kind, body)` turns a request into a list of steps; each step is one function. `tools.py` holds the series tables `GENRES`, `STYLES`, `DIRECTION`, `VOICES` and the prompts, and re-exports everything in `steps\`. See "Where the `tools.py` code lives" further down. |
| `story.py` | ~1260 lines | Script writing (`local_chat` sends `think: false` to Gemma 4 / Qwen3 when a step asks for it): Sukoon (Hindi/English), the six genres, recipes, topic bank, ledger (no repeats), Gemini with Ollama fallback, check pass. Holds `GENRE_TEXT`, `GENRE_TELLINGS`, `LEVELS`. |
| `path_config.py` | small | Reads `config.json`, works out every folder path. |
| `comfy.py` | small | Talks to ComfyUI (starts it if needed) to paint pictures. |
| `sfx_lib.py` | small | Sound-effect library by folder; `SERIES` = which effects each genre uses and how often. |
| `audio_index.py` | small | Measures your music/SFX and your real Audacity Sukoon episodes (music level shape). |
| `pacing.py` | small | Story and narration pacing index (words per minute from your narrations). |
| `repair.py` | small | Repair page: checks a finished video/reel (voice, subtitles, jumps) and fixes ticked items. |
| `elements_get.py`, `elements_auto.py`, `elements_wanted.json` | small | Downloads motion elements (steam, candle...) from Pixabay with a licence register. |
| `setup_hub.py` | small | One-time setup (`setup_hub.bat`). |
| `compare_story_models.py` | small | One-off tool: compares local Ollama models on one Hindi story prompt. |

### Experimental only (added 14-16 Oct)
| File | What it does | Added |
|---|---|---|
| `experimental_workspace.py` | Sends Experimental jobs to the Experimental root (`D:\MukhtalifGPT`), names episodes (`<Series>-<title words>`, `latin_title`, `title_name`), sorts files into `00_Input / 01_Script / 02_Narration / 03_Artwork / Output`, adds Experimental steps (`exp_audio_review`, `exp_cue_times`, `exp_after_video`, sound top-ups). | 14 Oct |
| `tinker.py` | Tinker page: change models, prompts, series tables, writing rules for Experimental runs; "Install into Working". | 16 Oct |
| `feedback.py` | Ratings (1-5 + notes) on everything, stored in `D:\MukhtalifGPT\Activity\ratings.sqlite`; uses them to skip bad music/effects. | 16 Oct |
| `self_review.py` | The AI reviews its own video (effect timing, voice vs script, subtitles, pictures, clarity) and pre-fills ratings. | 16 Oct |
| `word_times.py` | Exact time of each spoken word (Whisper) so effects land on the right word. | 16 Oct |
| `sound_library.py` | Sounds page: browse/rate/import BGM and SFX, search Openverse/Freesound (CC0). | 16 Oct |
| `audio_screen.py` | Checks if a music track or effect would cover the voice; ok / risky (treated copy) / reject. | 16 Oct |
| `git_save.py` | Save / Git page: commit, push, pull with fixed commands. | 16 Oct |
| `narrator.py`, `narrator_render.py` | Corner narrator with lip sync (Narrator page, `narrator.SERIES` table). Not run on the PC yet. | 16 Oct |
| `picture_check.py` | New picture check for genre scenes: checklist from 'what happens', judge answers plain questions, code decides, up to 2 repaints, then flag. `CHECK_MODEL` = gemma3:12b. | 16 Oct |
| `emotion_music.py` | Music that follows the story's emotion: the emotion map (mood + weight per scene, music sections, turn lines; step `exp_emotion_map`) and the mixer `horror_sound` uses for it (`EMOTION_MUSIC_PLAN.md`). | 17 Oct |
| `sound_needs.py` | Report of the music moods and effects the stories need and the library lacks (`Activity\Reports\sound_needs_*.md`); downloads nothing. | 17 Oct |
| `create_form.py` | One Create form for every series; `DEFAULTS` per series (Tinker table). | 16 Oct |
| `migrate_data_root.py` | One-off: moves Experimental data from `D:\HubGPT` to `D:\MukhtalifGPT` and switches `experimental_root` (dry run by default; the switch was made 16 Oct). | 16 Oct |

### Pages
| File | What |
|---|---|
| `ui/index.html` | Working site (one file: HTML + CSS + JS). |
| `ui/experimental/index.html` | Experimental site (separate full copy, ~270 KB). Has one Create form, Build queue, Tinker, Ratings/Review, Sounds, Narrator, Save/Git, Help, playback speed. |
| `ui/experimental/EXPERIMENT_NOTES.md` | What each Experimental feature does and which shared files it touched. Keep it updated. |
| `ui/experimental/AGENTS.md` | Rules for AI helpers: change Experimental first, keep Working intact. |
| `ui/motions/` | Preview clips for the 8 reel camera motions. |

## Where the `tools.py` code lives (split 17 Oct)

`tools.py` is still the front door: every name below is also `tools.<name>` (re-exported at the end of `tools.py`),
so the server, saved jobs, feedback, narrator and Tinker work as before. **Edit a function in the file listed here.**
Rules for code in `app\steps\` (write `tools.X` for tables, prompts and swapped functions): `TOOLS_SPLIT_PLAN.md`.

| File | What | Main functions |
|---|---|---|
| `tools.py` (~625 lines) | paths, model call, all tables and long prompts | `narration_dir`, `slug`, `model_chat`, `STYLES`, `GENRES`, `VOICES`, `DIRECTION`, `SCENES_ASK` and the other Tinker prompts, `END_SECONDS`, `SCENE_SECONDS` |
| `steps\planner.py` | **job planner** | `plan(kind, body, cfg)`, `presets`, `voice_sets`, `unique_genre_name`, `save_upload` |
| `steps\script.py` | scripts, translation, elements, emotion marks | `llm_story`, `fit_script`, `write_script`, `write_stories`, `translate`, `mark_emotions` (`EMO_BATCH`) |
| `steps\voice.py` | GPU and voices, word rack | `free_gpu`, `free_llm`, `sv_speak`, `en_speak`, `avoid_hard_words`, `rack_save` |
| `steps\scenes.py` | genre scene planning and sound cues | `horror_scenes`, `sfx_fetch`, `_sfx_names`, `_beat_parts`, `_invented`, `_ask_step`, `_loads`, `PICTURE_RULES`, `INVENTED` |
| `steps\genre.py` | genre story, sound, video, reels | `horror_story`, `horror_sound`, `_horror_track`, `horror_video`, `_subscribe_end`, `genre_reels` |
| `steps\mix.py` | music index and voice + music mix | `audio_index`, `shape_mix`, `harshness` |
| `steps\pictures.py` | artwork and pictures | `art_prompt`, `pick_art`, `comfy_images`, `pick_scenes` (Experimental: `picture_check.pick_scenes`), `repaint`, `no_steam` |
| `steps\sukoon_video.py` | Sukoon video and slideshow | `studio_prepare`, `studio_video`, `slideshow`, `make_ass` |
| `steps\fixes.py` | repair page, reels from a video | `repair_*`, `remix_like_before`, `narration_lines`, `studio_reels`, `reel_motions` |
| `steps\voice_series.py` | voice per series, training | `genre_voice_sets`, `voice_scan`, `sv_dataset`, `train_all_series`, `sv_train` |
| `steps\thumbs.py` | thumbnail look index | `style_index`, `look_text` |
| `steps\hub.py` | status, suggestions, library, model settings | `status`, `suggestions`, `library`, `episodes`, `remove_item`, `system_action`, `set_llm` |

## How a job flows (from clicking Create to the final video)

1. The page sends `POST /api/jobs` with a `kind` and the form values.
2. `server.py`: Working mode calls `tools.plan(kind, body)`. Experimental mode (header `X-Hub-Experimental: 1`)
   calls `experimental_workspace.prepare()`, which calls `tools.plan()` and then adds Experimental steps,
   the Tinker snapshot and the Experimental paths (`D:\MukhtalifGPT\Series\...`).
3. The job is queued. GPU jobs run one at a time; light jobs (script batches, SFX downloads, checks) run beside.
4. Each step runs `tools.<fn>(ctx, **args)`; the log lands in Activity. A hub restart resumes from the same step.

### Sukoon episode (`kind: "episode"`, Hindi or English)
`write_script` or `llm_story` (AI writes) -> `fit_script` (shorten to video length) -> [`llm_translate`] ->
`mark_emotions` -> `free_gpu` -> `sv_speak` (your Hindi voice) or `en_speak` (English narrator) ->
`shape_mix` (music under the voice) -> `art_prompt` -> `comfy_images` -> `pick_art` -> `elements_check` ->
`studio_prepare` -> `studio_video` (engine `engines\video\engine\sleep_pipeline.py`: living art, subtitles, reels).

### Genre episode (`kind: "genre"`, series bhay / rahasya / kisse / romanch / kareeb / gyaan, Hindi only)
Experimental run, as in the Kisse-1016-2155 log (14 steps): script (26B; episode renamed to `<Series>-<title words>`)
-> emotion marks -> free the card -> voice -> SFX library top-up -> scene plan (scenes end at a sentence end about
every 30 s; director writes a 'what happens' line per scene; a second ask turns it into the picture prompt; invented
props or weather are rewritten) -> SFX on Whisper word times -> music + ambience + SFX (10 s after the voice, plus the
subscribe click and chime) -> ComfyUI paints -> picture check (checklist, judge, up to 2 repaints, flag) -> free the
card -> video (with the blurred-backdrop subscribe and bell ending) -> AI self-review -> reels.
Generic step names:
`horror_story` (AI writes, via `story.write_genre` or `write_horror` for bhay) or `write_script` ->
`fit_script` -> `avoid_hard_words` -> `mark_emotions` -> `free_gpu` -> `sv_speak` (series voice) ->
`sfx_fetch` -> `horror_scenes` (scene plan + cues) -> [Experimental: `exp_cue_times`] -> `horror_sound` ->
`comfy_images` -> `pick_scenes` -> `free_gpu` -> `horror_video` (engine `horror_video.py`) ->
[Experimental: `exp_after_video`, AI self-review] -> `genre_reels`.
The `stages` list (script, voice, scenes, sound, pictures, video, reels) lets a page redo only part of it.

## Where each setting lives

| Setting | Where | Change it from |
|---|---|---|
| Folder paths, ports, Ollama/ComfyUI URLs | `config.json` (`paths`, `layout`) | edit the file |
| Which AI models (story, prompt, vision) | `config.json` + `D:\MukhtalifGPT\hub_settings.json` | Settings / System page; Tinker (Experimental) |
| Gemini / Pixabay / Freesound keys, script engine | `secrets.json` | Settings page |
| Series settings (music folders, voice, pause, drama) | `tools.GENRES` | Tinker > Series settings |
| Picture looks per series | `tools.STYLES` | Tinker |
| Voice direction per emotion | `tools.DIRECTION` | Tinker |
| Writing rules, story shapes, topics per genre | `story.GENRE_TEXT / GENRE_TELLINGS / LEVELS`, overridden by `genre_rules.json` | Tinker > Writing rules |
| Long prompts (scene planner, artwork, picker) | `story.py` / `tools.py` constants | Tinker > Prompts |
| Which sound effects per genre | `sfx_lib.SERIES` | Tinker |
| Video colour grade and fonts per genre | `engines\video\engine\horror_video.py` `MOODS` / `FONTS` | edit the file |
| Experimental overrides (models, scene length, ending seconds, subscribe on/off, tables, prompts) | `D:\MukhtalifGPT\Tinker\settings.json` | Tinker |
| Picture judge model | `picture_check.CHECK_MODEL` (gemma3:12b), or `picture_check_model` in the hub config | edit (not on Tinker yet) |
| Per-series Create form defaults | `create_form.DEFAULTS` | Tinker > Series settings, or "Save as the <series> defaults" |
| Installed into Working | `working_tinker.json` (hub folder) + the files above | Tinker > Install into Working |
| Words your voice says wrong | `engines\voice\say_as.json` | Voice page / edit file |
| Series voice models | `D:\SukoonVoice\series.json`, `engines\voice\models\` | Settings > voice training |
| Ratings | `D:\MukhtalifGPT\Activity\ratings.sqlite` | Review / Ratings pages |
| Sound screening limits | `audio_screen.LIMITS` | Tinker |

Adding a new genre today touches about 7 places (GENRES, STYLES, GENRE_TEXT, unique_genre_name, horror_sound
cue_db, sfx_lib.SERIES, horror_video MOODS) plus music folders. See `KNOWN_PROBLEMS.md`.

## Engines (outside app\)

| Folder | What | Key files |
|---|---|---|
| `engines\voice` | Your cloned Hindi voice; training per series | `run.py`, `voice\speak.py`, `voice\textsplit.py`, `voice\asr.py`, `say_as.json`, `models\`, `tests\test_hindi_narration.py` |
| `engines\video` | Rendering | `engine\sleep_pipeline.py` (Sukoon), `engine\horror_video.py` (genres), `engine\subscribe_end.py` (subscribe + bell ending art), `engine\reels.py`, `engine\kinetic.py` (subtitles), `engine\hbtext.py` (Hindi letters), `engine\artloop.py` (living art) |
| `engines\english` | English narrator Qwen3-TTS | `run.py`, `en\speak.py`, `en\engines\qwen*` |

## Before you change anything (checklist)

1. Read `KNOWN_PROBLEMS.md` "Decisions" so you don't undo something on purpose.
2. Change Experimental first; note it in `app\ui\experimental\EXPERIMENT_NOTES.md` and `CHANGELOG.md`.
3. Make a backup (the hub's habit is `file_old_<date>_<what>`).
4. Restart the hub with `restart_hub.bat` while no job runs; the in-app Restart button also makes a git snapshot.
5. Run a short test episode and write "verified on PC" next to the change in `CHANGELOG.md`.
