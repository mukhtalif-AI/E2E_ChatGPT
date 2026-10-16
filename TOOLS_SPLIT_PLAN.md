# Plan: split `app\tools.py` (Cleanup step 4)

Written 17 Oct 2026, ~00:40. **Plan only: no code has moved.** `tools.py` today: 4351 lines, 245 KB, 213 names.
Companion: `tests\smoke_tools.py` (the check to run before and after every move) and `tests\tools_baseline.json`.
Line numbers below are from 17 Oct ~00:30 and will drift; search for the function name.

## The one rule: `tools` stays the front door

Other files change `tools` while the hub runs, and the hub finds every step by name:

| Who | What it does to `tools` | Names |
|---|---|---|
| `server.py` | runs every job step as `getattr(tools, fn)`; calls about 30 more names directly | all step names, `plan`, `status`, `library`, `free_llm` ... |
| `feedback.py` | replaces three functions with rating-aware wrappers at hub start | `_horror_track`, `_sfx_names`, `shape_mix` |
| `narrator.py` | wraps the planner and two video steps, adds two steps | `plan`, `horror_video`, `genre_reels`, (+`narrator_overlay`, `narrator_art`) |
| `tinker.py` | swaps tables and long prompts with `setattr(tools, ...)` for each Experimental job, and lists every long ALL-CAPS text in `vars(tools)` as an editable prompt | `GENRES`, `STYLES`, `DIRECTION`, `TRANSLATE_ASK`, `VISION_ASK`, `ART_ASK`, `PICK_ASK`, `SCENE_ASK`, `SCENES_ASK`, `CUE_WORDS_ASK`, `SCENE_VARY_ASK`, `SCENE_PICTURE_ASK`, `SCENE_FIX_ASK` |
| `picture_check.py`, `self_review.py`, `experimental_workspace.py`, `sound_library.py` | read helpers and tables as `tools.X` | `_ask_step`, `_invented`, `_loads`, `_strip_words`, `no_steam`, `model_chat`, `free_gpu`, `narration_dir`, `slug`, `GENRES` ... |
| `jobs.json` (saved jobs) | stores step names; a restart resumes a job by name | every step name |

Inside one file this all works today because a bare call like `_horror_track(...)` looks the name up in `tools`
at the moment it runs, so it sees feedback's wrapper and Tinker's tables. Code moved to another file would look
in its own file instead and silently miss them (Tinker edits and rating-based picking would just stop working,
with no error). So every move follows these rules:

1. **Re-export at the end of `tools.py`, by name.** One block at the very end of `tools.py`, one line per module:
   `from steps.genre import (horror_story, horror_sound, _horror_track, ...)  # noqa: E402,F401`.
   Never `from steps.genre import *`: that skips `_underscore` names, and `_horror_track`, `_beat_parts`, `_loads`
   ... are used from outside. Remaining code in `tools.py` keeps calling the bare names and keeps working.
2. **Tables and prompts stay in `tools.py`.** Everything Tinker swaps (the 13 names above), plus constants more
   than one module reads (`END_SECONDS`, `SUBSCRIBE_END`, `SCENE_SECONDS`, `MIN_TAIL`, `SIZES`, `VOICES`, `THEMES`,
   `TYPOS`, `VIZ`, `PAUSES`). Moved code reads them as `tools.GENRES`, `tools.SCENES_ASK`, never by copying.
3. **Swappable functions are always called as `tools.X` in moved code**, even from inside the same new file:
   `_horror_track`, `_sfx_names`, `shape_mix`, `plan`, `horror_video`, `genre_reels`.
4. **Anything that lives in another file is called as `tools.X`** (for example `tools.free_gpu`, `tools.slug`).
   A helper in the same new file that nobody patches may be called directly.
5. **New files start with `import tools`** and never `from tools import X` at the top (that copies the value once).
   A local `WIN = os.name == "nt"` is fine.
6. **Nothing imports `steps.*` except `tools.py`.** `tools.py` imports them at its end, when everything above is
   defined, so the `import tools` at the top of a step file finds a ready module. Importing `steps.genre` first
   would start `tools` half-way and fail. `steps\__init__.py` says this in one line.
7. **Names never change.** Saved jobs, the pages and the other modules use them. No renames during the split.
8. **`plan()` calls itself as `globals()["plan"]`** (the `experimental` kind). In `steps\planner.py` that must
   become `tools.plan`, or the narrator wrapper is skipped.
9. **Module state moves with its only user**: `_PREV` (reel motions) and `_gpu_cache` (status). Nobody else reads them.

`tests\smoke_tools.py` checks rules 1, 3, 5 and 7 automatically (section 3 flags any bare use of a swappable name
or a top-level `from tools import` in `app\steps\*.py`; tested on a deliberately bad file).

## What `tools.py` keeps (~450 lines + the re-export block)

Imports and `comfy` names (`comfy_generate`, `_post` ... are used as `tools.X`), `WIN`, the path helpers
(`ctx_folder`, `narrations_root`, `narration_dir`, `voice_model_root`, `studio_work_dir`, `slug`), `model_chat`,
`fonts_dir`, `sv_config`, all tables (`STYLES`, `GENRES`, `SIZES`, `VOICES`, `TYPOS`, `VIZ`, `PAUSES`, `THEMES`,
`DIRECTION`), all 10 long prompts, and the shared constants from rule 2.

## New modules, in the order to move them

Least connected first. "Patched" = names in the module that another file swaps; "Tinker" = tables/prompts it reads.

| Step | New file | What moves (lines today) | Size | Patched / Tinker / used from outside | Risk |
|---|---|---|---|---|---|
| 4a | `steps\hub.py` | `status`, `gpu_info`, `_gpu_cache`, `dismiss`, `suggestions`, `_mtime`, `library`, `episodes`, `library_stamp`, `remove_item`, `open_path`, `system_action` (4041-4351); `apply_settings`, `llm_models`, `set_llm`, `set_prompt_model`, `_settings_path` (3185-3227) | ~360 | server uses 13 of them, tinker `llm_models`; no job steps | low |
| 4b | `steps\thumbs.py` | `index_path`, `load_index`, `series_of`, `SUKOON_WORDS`, `thumb_sources`, `look_text`, `style_index` (310-406, not `VISION_ASK`/`THEMES`) | ~90 | step `style_index`; reads `tools.VISION_ASK`, `tools.STYLES` | low |
| 4c | `steps\voice_series.py` | `SERIES_DEFAULT` ... `sv_train`: series voices, scan, dataset, training (2944-3181) | ~240 | server uses 6; steps `sv_dataset`, `classify_recordings`, `train_all_series`, `sv_train`; reads `tools.GENRES` | low |
| 4d | `steps\mix.py` | `audio_index_path`, `load_audio_index`, `audio_index`, `auto_track`, `harshness`, `tamed`, `_decode`, `shape_mix` (410-587) | ~180 | **`shape_mix` wrapped by feedback**; feedback calls `harshness`, `load_audio_index`; reads `tools.END_SECONDS` | medium |
| 4e | `steps\sukoon_video.py` | `_first_image`, `_audio_of`, `studio_prepare`, `studio_video`, `_wav_len`, `font_family`, `_ass_*`, `make_ass`, `slideshow` (3762-4037) | ~275 | steps `studio_prepare`, `studio_video`, `slideshow` (Working's main product) | medium |
| 4f | `steps\pictures.py` | `_parts`, `llm_prompts`, `default_style`, `_script_sample`, `art_prompt`, `pick_art`, `HOT_DRINK`, `no_steam`, `comfy_images`, `_judge`, `pick_scenes`, `repaint` (3293-3759, not the prompts) | ~430 | Tinker `ART_ASK`, `PICK_ASK`, `SCENE_ASK`, `STYLES`, `GENRES`; picture_check uses `no_steam`; `pick_scenes` hands Experimental to `picture_check` | medium |
| 4g | `steps\voice.py` | word rack (`rack_path` ... `practice_story`), `free_gpu`, `free_llm`, `sv_python`, `_story_voice_style`, `sv_speak`, `en_dir`, `en_python`, `en_speak` (1658-1927); `en_setup`, `en_test` (3280-3290) | ~290 | every job calls `free_gpu`; server, picture_check, self_review use `free_llm`/`free_gpu`; reads `tools.GENRES` | medium |
| 4h | `steps\script.py` | `translate`, `llm_translate` (262-284); `_clean_script`, `llm_story`, `fit_script` (1132-1192); `_queue_fetch`, `elements_check`, `elements_fetch`, `write_stories`, `write_script` (1419-1507); `EMOTIONS`, `EMO_BATCH`, `_emo_*`, `mark_emotions` (1527-1654) | ~360 | Tinker `DIRECTION`, `TRANSLATE_ASK`, `GENRES`; uses `tools._loads`, `tools._is_exp` | medium |
| 4i | `steps\fixes.py` (not `repair.py`: `app\repair.py` already exists) | `_secs`, `repair_*`, `series_of_video`, `_series_note`, `remix_like_before`, `narration_lines`, `repair_rerender`, `_duration`, `_PREV`, `reel_motions`, `studio_reels` (1195-1416) | ~220 | calls **`tools.horror_video`** (narrator) and **`tools.shape_mix`** (feedback), `tools.horror_sound`; `plan` uses `_duration` | medium-high |
| 4j | `steps\scenes.py` | `_sfx_names(_old)`, the scene helpers and non-shared constants (`AMB_*`, `PICTURE_RULES`, `INVENTED`, `WEATHER`, `_SENT_END` ...), `sfx_fetch`, `_beat_parts`, `_invented`, `_ask_step`, `story_ask`, `_loads`, `_clean_scene`, `_picture_prompts`, `_set_aside_scenes`, `horror_scenes` (2013-2619, not the 5 prompts) | ~520 | **`_sfx_names` wrapped by feedback**; Tinker `SCENES_ASK`, `CUE_WORDS_ASK`, `SCENE_VARY_ASK`, `SCENE_PICTURE_ASK`, `SCENE_FIX_ASK`, `GENRES`; picture_check uses 4 helpers | high |
| 4k | `steps\genre.py` | `horror_story`, `write_horror_stories` (1931-2010); `_horror_track`, `_usable_music`, `horror_sound`, `_subtitle_text_for_timing`, `_is_exp`, `_end_seconds`, `_subscribe_*`, `horror_video` (2622-2940); `genre_reels` (3230-3277) | ~470 | **`_horror_track` (feedback), `horror_video` and `genre_reels` (narrator)**; Tinker `GENRES`; calls `tools.shape_mix` | high |
| 4l | `steps\planner.py` | `presets`, `use_shape`, `voice_sets`, `studio_opts` (154-251), `save_upload` (287-297), `unique_genre_name`, `_rename_job_values`, `plan` (591-1127) | ~640 | **`plan` wrapped by narrator**, called by server and experimental_workspace; `globals()["plan"]` -> `tools.plan`; reads `tools.GENRES` | highest, last |

## How each move is done and verified

Before:
1. The hub is idle (no job running or queued); git shows `app\tools.py` committed. Run
   `python tests\smoke_tools.py` with the hub's Python: it must say ALL PASSED. If it fails only because another
   session changed `tools.py` on purpose since the baseline, re-save the baseline now (`--save-baseline`), on the
   code before the move, and run it again.
2. Back up as usual: `tools.py_old_<YYYYMMDD_HHMMSS>_split4x`. Note the file's modified time.

The move (one module, one sitting):
3. Cut the functions verbatim into `app\steps\<module>.py` (create `app\steps\__init__.py` in 4a). Add the stdlib
   imports they need and `import tools`. Change only names, per rules 2-5 (`GENRES` -> `tools.GENRES`, a call to a
   function in another file -> `tools.X`). No other edits in the same move, not even small fixes.
4. Add the module's line to the re-export block at the end of `tools.py`, listing every moved name.
5. Right before saving `tools.py`, check its modified time is unchanged (another session may have edited it).

After:
6. `python -m py_compile app\tools.py app\steps\<module>.py`, then `python tests\smoke_tools.py` (full, ~1-2 min):
   ALL PASSED. Section 4 must show the same step lists as the baseline for both modes.
7. Restart the hub (`restart_hub.bat`). For 4a-4c open the pages that use the moved code (Library, System,
   Settings, voice training list) in Working and Experimental. From 4d on, also run one short real job that uses
   the moved steps, in Experimental (a 1-minute Bhay / Sukoon from the page, or the Cleanup step 1 job runner once
   it exists). After 4e, 4g, 4k and 4l also one short Working job, because both modes run this code.
8. Commit just this move ("split tools.py 4x: <module>"), add a CHANGELOG row with the honest status, and update the
   `tools.py` map in `PROJECT_MAP.md` (which functions now live where).

What the smoke test cannot see: steps that need the graphics card or an AI model (voice, ComfyUI, picture check,
video render, script and scene writing) and the web pages. That is why step 7 runs a real job.

## Risks and what handles them

| Risk | What could happen | Guard |
|---|---|---|
| A swappable name used directly in moved code | Rating-based music/effects picking, ducking, the corner narrator or Tinker edits stop working, with no error | Rules 2-5; smoke section 3 (static check); smoke section 5 probes prove `horror_sound` still goes through `tools._horror_track`, `tools.GENRES`, `tools.shape_mix` |
| `import *` or a forgotten name in the re-export | A page or a saved job fails with "module 'tools' has no attribute ..." | Rule 1; smoke section 2 checks all 213 baseline names, every `tools.X` used anywhere, every step name |
| Circular import | Hub does not start | Rule 6; smoke section 1 imports every module the way the hub does |
| Tinker prompt list changes | A prompt disappears from Tinker, or its edits are ignored | Prompts stay in `tools.py` (rule 2); smoke compares Tinker's keys with the baseline |
| Working and Experimental share the code | A move tested only in Experimental breaks Working (or the reverse) | Smoke section 4 plans every kind in both modes; real Working job after 4e, 4g, 4k, 4l |
| Another session edits `tools.py` during a move | Its change is lost, or lands in the old copy of a moved function | One move per sitting; ask the other threads to hold `tools.py` edits while it runs (the coordinator can relay this); modified-time check before saving (step 5); after the commit, PROJECT_MAP says where each function lives now. Add one line to `app\ui\experimental\AGENTS.md`: "functions may live in `app\steps\`; edit them there". |
| A job is running during a move | The running hub keeps the old code; a restart mid-job resumes by step name | Move only when idle (step 1); names never change (rule 7), so resume still works after a restart |
| `plan` calls itself through `globals()` | The narrator wrapper is skipped for `experimental` runs | Rule 8 |
| A name defined twice | A helper in a new file with the same name as one in `tools.py` overwrites it through the re-export | Explicit re-export lists; review the diff; the move is a cut, never a copy |

## Rollback

- **Not committed yet:** copy `tools.py_old_<stamp>_split4x` back over `tools.py`, delete the new
  `app\steps\<module>.py`, run the smoke test, restart the hub.
- **Committed:** revert that one commit (`git revert <commit>`, or on the Save / Git page), run the smoke test,
  restart the hub. Each move is its own commit, so moves undo one at a time, newest first.
- Saved jobs are not affected either way: they store only step names, and names never change.

## The smoke test (`tests\smoke_tools.py`)

Run with the hub's Python from the hub folder:
`E:\ComfyUI_windows_portable\python_embeded\python.exe -s tests\smoke_tools.py`

- `--save-baseline` once on the code before a split (writes `tests\tools_baseline.json`: the 213 names, Tinker's
  keys, and the step list of 11 sample jobs in both modes). `--quick` skips the episode part (~10 s).
- Writes only into `_tmp\smoke_<time>` (deleted when everything passes, kept when something fails). Its config is
  a copy of `config.json` with the data folders pointed there; the real data, music and SFX folders are only read
  (the SFX register update is switched off during the test). No GPU, Ollama or ComfyUI; the hub can keep running.
- Section 5 copies the newest finished Experimental genre episode (default `Kisse-1016-2155`; voice, timing, scene
  plan; no pictures or videos) and runs `write_script`, `narration_dir`, `_beat_parts`,
  `_subtitle_text_for_timing`, `narration_lines`, then `horror_sound` for real and checks the mix length.

Tested on the current, unsplit code (17 Oct): 76 of 76 checks pass (cloud copy of the hub folder, Linux Python
3.10, a stand-in music file); see CHANGELOG for the run on the PC. A deliberately bad `steps\genre.py` (bare
`_horror_track`, `from tools import GENRES`) and a missing name were both caught.
