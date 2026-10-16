# HubGPT project map

Last updated: 16 Oct 2026. Start here when you forget where something is.
Companion files in the same folder: `CHANGELOG.md` (what changed when), `KNOWN_PROBLEMS.md` (what is wrong,
what is right, what was decided), `CLEANUP_PLAN.md` (proposed tidy-up, not done yet).
Older long notes: `README.txt` (how to use the app) and `HANDOFF_HUB.txt` (history v1 to v4.3).

## In one sentence

HubGPT is one browser app on your PC (`hub.bat`, http://127.0.0.1:8781) that writes a script, reads it in
your own voice, adds music and sound effects, paints pictures with ComfyUI and renders a video and reels.

## Two modes

| | Working mode (the "stable" site) | Experimental mode |
|---|---|---|
| Address | http://127.0.0.1:8781/ | http://127.0.0.1:8781/experimental |
| Page file | `app/ui/index.html` | `app/ui/experimental/index.html` |
| Where outputs go | `D:\MukhtalifGPT` (+ `\Episodes`) | `D:\HubGPT` (Library, Series, Activity, Tinker) |
| Job list | `D:\MukhtalifGPT\jobs.json` | `D:\HubGPT\Activity\jobs.json` |
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
  _old\, app\_old\, _backup_*\, *_old_<date>   backups of earlier versions (nothing is ever deleted)
  _tmp\              test frames and patches from AI sessions (safe to ignore)
  logs\              hub logs
```

Media stays on D: (`D:\MukhtalifGPT`, `D:\HubGPT`, `D:\SukoonVoice`, `D:\Audio asset`); code on E:.

## Files in `app\` (what each one does)

### Core (used by both modes)
| File | Size | What it does |
|---|---|---|
| `server.py` | ~1000 lines | The web server. Serves both pages, all `/api/...` routes, the job queue (one GPU job at a time + a light lane), restart/resume, git snapshot on restart. Runs each job step by calling `tools.<fn>` (or `experimental_workspace.exp_*`). |
| `tools.py` | ~3700 lines, 210 KB | Every job step. `plan(kind, body)` turns a request into a list of steps; each step is one function below it. Also holds the series tables `GENRES`, `STYLES`, `DIRECTION`, `VOICES`. See the section map further down. |
| `story.py` | ~1200 lines | Script writing: Sukoon (Hindi/English), the six genres, recipes, topic bank, ledger (no repeats), Gemini with Ollama fallback, check pass. Holds `GENRE_TEXT`, `GENRE_TELLINGS`, `LEVELS`. |
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
| `experimental_workspace.py` | Sends Experimental jobs to `D:\HubGPT`, sorts files into `00_Input / 01_Script / 02_Narration / 03_Artwork / Output`, adds Experimental steps (`exp_audio_review`, `exp_cue_times`, `exp_after_video`, sound top-ups). | 14 Oct |
| `tinker.py` | Tinker page: change models, prompts, series tables, writing rules for Experimental runs; "Install into Working". | 16 Oct |
| `feedback.py` | Ratings (1-5 + notes) on everything, stored in `D:\HubGPT\Activity\ratings.sqlite`; uses them to skip bad music/effects. | 16 Oct |
| `self_review.py` | The AI reviews its own video (effect timing, voice vs script, subtitles, pictures, clarity) and pre-fills ratings. | 16 Oct |
| `word_times.py` | Exact time of each spoken word (Whisper) so effects land on the right word. | 16 Oct |
| `sound_library.py` | Sounds page: browse/rate/import BGM and SFX, search Openverse/Freesound (CC0). | 16 Oct |
| `audio_screen.py` | Checks if a music track or effect would cover the voice; ok / risky (treated copy) / reject. | 16 Oct |
| `git_save.py` | Save / Git page: commit, push, pull with fixed commands. | 16 Oct |
| `narrator.py`, `narrator_render.py` | Corner narrator with lip sync (being built in another thread on 16 Oct; not finished at time of writing). | 16 Oct (in progress) |

### Pages
| File | What |
|---|---|
| `ui/index.html` | Working site (one file: HTML + CSS + JS). |
| `ui/experimental/index.html` | Experimental site (separate full copy, ~218 KB). Has Tinker, Ratings/Review, Sounds, Save/Git, Help, playback speed. |
| `ui/experimental/EXPERIMENT_NOTES.md` | What each Experimental feature does and which shared files it touched. Keep it updated. |
| `ui/experimental/AGENTS.md` | Rules for AI helpers: change Experimental first, keep Working intact. |
| `ui/motions/` | Preview clips for the 8 reel camera motions. |

## Map of `tools.py` (by line, 16 Oct)

| Lines | Section | Main functions |
|---|---|---|
| 1-64 | paths and model call | `narration_dir`, `studio_work_dir`, `model_chat` |
| 65-294 | presets and tables | `STYLES`, `GENRES`, `VOICES`, `voice_sets`, `studio_opts`, `translate` |
| 295-403 | thumbnail look index | `style_index`, `look_text` |
| 404-580 | music index and mix | `audio_index`, `shape_mix` (voice + music like your episodes) |
| 581-617 | genre names | `unique_genre_name` |
| 618-1120 | **job planner** | `plan(kind, body, cfg)` - every job kind lives here |
| 1121-1492 | scripts, repair, reels, elements | `llm_story`, `fit_script`, `repair_*`, `studio_reels`, `write_stories` |
| 1493-1700 | script prep | `write_script`, `DIRECTION`, `mark_emotions`, `avoid_hard_words` |
| 1700-1875 | GPU and voices | `free_gpu`, `free_llm`, `sv_speak` (Hindi voice), `en_speak` (English) |
| 1876-2312 | genre (भय etc.) | `horror_story`, `sfx_fetch`, `horror_scenes`, `horror_sound`, `horror_video` |
| 2313-2553 | voice per series, training | `genre_voice_sets`, `voice_scan`, `sv_dataset`, `train_all_series`, `sv_train` |
| 2554-2716 | model settings, reels, English setup | `set_llm`, `genre_reels`, `en_setup`, `llm_prompts` |
| 2717-3146 | artwork and pictures | `art_prompt`, `pick_art`, `comfy_images`, `pick_scenes`, `repaint` |
| 3147-3405 | Sukoon video and slideshow | `studio_prepare`, `studio_video`, `slideshow`, `make_ass` |
| 3406-3717 | status, suggestions, library | `status`, `suggestions`, `library`, `episodes`, `remove_item`, `system_action` |

## How a job flows (from clicking Create to the final video)

1. The page sends `POST /api/jobs` with a `kind` and the form values.
2. `server.py`: Working mode calls `tools.plan(kind, body)`. Experimental mode (header `X-Hub-Experimental: 1`)
   calls `experimental_workspace.prepare()`, which calls `tools.plan()` and then adds Experimental steps,
   Tinker settings and the `D:\HubGPT` paths.
3. The job is queued. GPU jobs run one at a time; light jobs (script batches, SFX downloads, checks) run beside.
4. Each step runs `tools.<fn>(ctx, **args)`; the log lands in Activity. A hub restart resumes from the same step.

### Sukoon episode (`kind: "episode"`, Hindi or English)
`write_script` or `llm_story` (AI writes) -> `fit_script` (shorten to video length) -> [`llm_translate`] ->
`mark_emotions` -> `free_gpu` -> `sv_speak` (your Hindi voice) or `en_speak` (English narrator) ->
`shape_mix` (music under the voice) -> `art_prompt` -> `comfy_images` -> `pick_art` -> `elements_check` ->
`studio_prepare` -> `studio_video` (engine `engines\video\engine\sleep_pipeline.py`: living art, subtitles, reels).

### Genre episode (`kind: "genre"`, series bhay / rahasya / kisse / romanch / kareeb / gyaan, Hindi only)
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
| Experimental overrides | `D:\HubGPT\Tinker\settings.json` | Tinker |
| Installed into Working | `working_tinker.json` (hub folder) + the files above | Tinker > Install into Working |
| Words your voice says wrong | `engines\voice\say_as.json` | Voice page / edit file |
| Series voice models | `D:\SukoonVoice\series.json`, `engines\voice\models\` | Settings > voice training |
| Ratings | `D:\HubGPT\Activity\ratings.sqlite` | Review / Ratings pages |
| Sound screening limits | `audio_screen.LIMITS` | Tinker |

Adding a new genre today touches about 7 places (GENRES, STYLES, GENRE_TEXT, unique_genre_name, horror_sound
cue_db, sfx_lib.SERIES, horror_video MOODS) plus music folders. See `KNOWN_PROBLEMS.md`.

## Engines (outside app\)

| Folder | What | Key files |
|---|---|---|
| `engines\voice` | Your cloned Hindi voice; training per series | `run.py`, `voice\speak.py`, `voice\textsplit.py`, `voice\asr.py`, `say_as.json`, `models\`, `tests\test_hindi_narration.py` |
| `engines\video` | Rendering | `engine\sleep_pipeline.py` (Sukoon), `engine\horror_video.py` (genres), `engine\reels.py`, `engine\kinetic.py` (subtitles), `engine\hbtext.py` (Hindi letters), `engine\artloop.py` (living art) |
| `engines\english` | English narrator Qwen3-TTS | `run.py`, `en\speak.py`, `en\engines\qwen*` |

## Before you change anything (checklist)

1. Read `KNOWN_PROBLEMS.md` "Decisions" so you don't undo something on purpose.
2. Change Experimental first; note it in `app\ui\experimental\EXPERIMENT_NOTES.md` and `CHANGELOG.md`.
3. Make a backup (the hub's habit is `file_old_<date>_<what>`).
4. Restart the hub with `restart_hub.bat` while no job runs; the in-app Restart button also makes a git snapshot.
5. Run a short test episode and write "verified on PC" next to the change in `CHANGELOG.md`.
