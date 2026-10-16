# HubGPT changelog

Newest first. Each item says whether it was **verified on PC** (you ran it and it worked), **compiled only**
(the code loads, but no real run yet) or **untested**. When you test something, change its tag here.
Backups for each change sit next to the file with the suffix shown (backups made before 16 Oct ~19:25 were deleted in the cleanup). Older history (v1 to v4.3): `HANDOFF_HUB.txt`.

Git: 16 Oct work is committed up to `f5a0abd` (19:06); later edits (26b model, this cleanup) are not committed yet; the
in-app Restart button commits a "HubGPT snapshot before restart", or use the Save / Git page.

## 16 Oct 2026 (Experimental mode)

| Change | Files | Status | Backup suffix |
|---|---|---|---|
| Picture prompts for every series: the default painter (Z-Image Turbo) has no working negative prompt, so every "no X" in a style was painted as X (Bhay 'no blood, no letters' got blood and letters; Kisse 'no clear faces' got a full face). All `STYLES` now say what to paint, never "no ..." ("wordless image", "faces turned away", "fully clothed" for Romanch instead of "no nudity"); Bhay style now asks for dread, distorted shadows, pale lifeless skin and hollow eyes on ghostly figures; Rahasya lost the forced 'rain-wet surfaces', Romanch/Kareeb the forced rain. Director and picture-prompt asks (`SCENES_ASK`, `SCENE_VARY_ASK`, `SCENE_PICTURE_ASK`) get `PICTURE_RULES`: never write no/not/without or text in a prompt; mature stories (level saved in `<script>.story.json`) may show blood and wounds. Kisse bug: a character anchor answered as a dict was pasted as raw Python code into every prompt (`_anchor_text`). Rahasya/Kisse mature: the series' 'no gore' rule is dropped when the mature line is added (`story._rules_for`). Real negative prompt for Z-Image held until tested. Review: `/mnt/project-files/reviews/genre_prompt_review_20261016.md` | `tools.py`, `story.py` | compiled; prompt building tested (all asks format, anchor text, mature rules); needs hub restart; old episodes keep their prompts until re-planned | `_old_20261016_142552_prompts` |
| Cleanup: 124 backup items (all `*_old_*` / `.bak` copies, `_old\`, `app\_old\`, `app\ui\_old\`, `engines\voice\voice\_old\`, the two `_backup_*_20261015\` folders, old `_tmp` test files, `_tmp\partial_english_libs`, `Code\ALL_CODE.txt.bak_*`), 42 MB, first moved into `_backups\2026-10-16_cleanup\`, then deleted on your OK (16 Oct); git keeps the committed versions. `.gitignore` now ignores `/_backups/`. Kept in place: `secrets.json_old_*`, voice `config*.before_*` / `config - *.json`, `_tmp\model_test_20261016`, `logs`, `.cache\pip`. No code changed. | root, `app`, `app\ui`, `engines`, `Code`, `_tmp`, `.gitignore` | done; no code reads the removed paths (grep) | none (deleted) |
| Experimental Video page: switch "Plan the scenes and paint the pictures again" (off by default, sends `replan`). `prepare()` adds `replan` + the video path to the `horror_scenes` step; `horror_scenes` then moves everything in the scenes folder (shots.json, video_plan.json, PNGs, prompt files, `_alt`) into `<scenes>\_old_<time>` and renames the old Output mp4 to `<name>_old_<time>.mp4` before planning, so pictures are painted fresh and nothing is deleted; reels untouched. Also fixed: the new director was off in real hub jobs (`horror_scenes` now checks `experimental_mode`, which `job_config` sets). Scenes field hint: 0 = about every 30 s. | `tools.py`, `experimental_workspace.py`, `ui/experimental/index.html` | compiled; move tested on a copy of the Bhay scenes folder (22 files before and after, all in `_old_`), prepare() dry run shows replan + video on the step: `_tmp\model_test_20261016\dryrun_replan.txt`; needs hub restart | `_old_20261016_185351_replan`, `_old_20261016_185153_expflag` |
| Short Experimental genre episode names, set once at queue time: `<Series>-<typed name or story topic>`, else `<Series>-<MMDD-HHMM>`, `-2`/`-3` when taken (e.g. `Episodes\Bhay-Purani-Negative\03_Artwork\Bhay-Purani-Negative_scenes`); no job id or second date in folder names (job id stays in `00_Input\input.json` and the job record); the planner's narration, scenes and Output now sit in that one folder instead of a second `Bhay - <name>` folder. Video / Pictures pages on an existing voice now reuse its episode folder (before: "choose a narration" error). Existing episodes unchanged. Shared change: `tools.unique_genre_name` also accepts `Bhay-x` as already prefixed. | `experimental_workspace.py`, `tools.py`, `ui/experimental/index.html` | compiled; dry run of prepare() (no writes) for typed name, topic, blank and the existing Bhay voice: `_tmp\model_test_20261016\dryrun_after.txt`; needs hub restart | `_old_20261016_181424_names` |
| Director rework (from the director review): Experimental genre scenes now end at a sentence end about every 30 s (Tinker > Models > Scene length; Bhay: 13 scenes instead of 8); the director writes a one-line 'what happens' per scene and a second step turns it into the picture prompt; the 'bangle' examples are gone from the director's prompt; prompts that add weather or props the scene text never names (rain, moon, candle, bangle, mirror...) are rewritten by the AI or cut (`tools.INVENTED`); shots.json keeps the full scene text (no 300-letter cut) plus `happens`; new Tinker models: Scene director, Scene picture prompts; Gemma 4 replies parsed safely; Whisper re-times lines it skipped (Bhay 56-73 s gap: 38 words now). Experimental default models: gemma4:12b-it-qat for script, director and picture prompts (Working stays gemma3:12b). Experimental script writer: gemma4:26b-a4b-it-qat (best story in test; 12b stays director). `free_llm` now also unloads the Tinker director / picture-prompt models, and every `free_gpu` step unloads the AI models the job used (before only with `include_llm`), so the 26b (~16 GB, part in RAM) is out before the voice and ComfyUI (backup `tools.py_old_20261016_191309_26b`). Test: `_tmp\model_test_20261016`. | `tools.py`, `story.py`, `tinker.py`, `word_times.py`, Tinker settings.json | compiled; director + Whisper tested on a copy of Bhay 1016_142902_8128; needs hub restart | `_old_20261016_103740_director`, `_old_20261016_174116_json` |
| From your rating notes on Bhay 1016_142902_8128: (1) Experimental only: script length now uses the series' own measured voice pace from earlier Experimental episodes (Bhay 134.6 words/min, Kisse 131) instead of 104.4, which made the '8 min' story come out 5.9 min; (2) a line that trails off on एक/और/कि/जो/ने/सबसे... ('नेगेटिव थी एक...') loses the dangling word, so it no longer sounds like a missing line; (3) a scene's ambience bed whose name names a sound (birds, train, horn, river, sea, market...) is dropped when that sound is not in the scene text or picture prompt, keeping the previous bed if it fits (`tools.AMB_SOURCES`). (2) and (3) apply to Working too (shared `story.py`/`tools.py`). | `story.py`, `tools.py` | compiled only, needs hub restart | `_old_20261016_101445_notes` |
| Bhay sound + pictures fix: (1) music tracks that are almost only deep rumble (<3% energy above 300 Hz, e.g. `the_odds`) are now rejected by the audio screening and swapped, with a WARNING in the job log; no usable mood track -> a softened library track 4 dB lower instead of nothing; the create form's 'music under the voice' dB now reaches genre runs (was ignored, always -18). (2) SFX cues whose words the planner wrote in English for a Hindi story (never matched, so never placed) are re-copied from the story text by the AI or dropped; planner told to copy words in Devanagari and to pick ambience that fits the place; job log no longer says 'no cues' when cues failed to match. (3) Picture prompts: scene moment first, shared look after and stripped of people/props (the look named the woman in the red sari, so every picture painted her); planner asked for a different main subject per scene; prompts too similar to an earlier one are rewritten (`tools.SCENE_VARY_ASK`, `SCENE_SIMILAR_MAX`). Repair path passed tail as genre - fixed. | `tools.py`, `audio_screen.py`, `self_review.py`, `ui/experimental/index.html` | compiled only, needs hub restart | `_old_20261016_093203_bhayfix` |
| One data folder: everything is meant to go to `D:\MukhtalifGPT`. Code now also reads the old `D:\HubGPT` (`paths.legacy_roots`) so old jobs and files still show; new `app/migrate_data_root.py` (dry run by default, `--copy`, `--switch`). Switch itself NOT done yet; English engine temp moved from `D:\MukhtalifHub\_tmp` to `D:\MukhtalifGPT\_tmp` | `config.json`, `experimental_workspace.py`, `server.py`, `ui/experimental/index.html`, `git_save.py`, `engines/english/config.json` | compiled only, needs hub restart | `_old_20261016_092345` |
| Corner narrator with lip sync (in progress in its own thread) | `app/narrator.py`, `app/narrator_render.py`, `server.py`, `tinker.py` | in progress, untested | `_old_20261016_074722_narrator` |
| Fix for the `KeyError: 'steps'` crash: Experimental job list now saves steps; old step-less jobs are marked stopped at start | `server.py`, `tools.py`, `experimental_workspace.py` | compiled only, **needs hub restart** | `_old_20261016_070503_steps` |
| Playback speed row (1x-3x, custom, `<` `>` keys) on every player | `ui/experimental/index.html` | untested (page reload is enough) | `_old_20261016_065837_speed` |
| Save / Git page + rewritten Help page | `app/git_save.py`, `server.py`, `ui/experimental/index.html` | untested | `_old_20261016_065044_git` |
| Ratings export (training JSONL) | `feedback.py`, `index.html` | untested | `_old_20261016_063859_export` |
| Audio screening (speech clarity), music downloads by mood, AI "clarity" review | `app/audio_screen.py`, `feedback.py`, `self_review.py`, `sound_library.py`, `tinker.py` | untested | `_old_20261016_063425_screening` |
| Ratings, Review page, AI self-review, word-timed SFX, Sounds page | `app/feedback.py`, `self_review.py`, `word_times.py`, `sound_library.py`, `server.py`, `tools.py` (`horror_sound` honours a cue's `at`) | untested | `_old_20261016_045222_ratings` |
| Tinker page + "Install into Working" | `app/tinker.py`, `experimental_workspace.py`, `server.py` | untested | `_old_20261016_043829` |

Also on 16 Oct: pipeline review written (`reviews/pipeline_review.md`), no code change.

## 15 Oct 2026 (v4.3, shared by both modes)

| Change | Files | Status | Backup |
|---|---|---|---|
| Hindi narration splitting (dependent clauses, पर, dialogue, short sentences) with a unit test | `engines/voice/voice/speak.py`, `textsplit.py`, `engines/voice/tests/test_hindi_narration.py` | test file exists; not listened to on a full episode | `app/_old/*_before_hindi_narration_20261015_*.bak` |
| Script commentary fix (AI notes no longer leak into the spoken script) | `story.py`, voice `speak.py` | untested | `_backup_before_script_commentary_fix_20261015\` |
| Qwen / Gemma roles: Gemma writes stories, Qwen3 (thinking on) writes image prompts, qwen2.5vl reviews pictures | `story.py`, `tools.py`, video engine | untested | `_backup_qwen_gemma_thinking_20261015\` |
| Fix scene picker; git snapshot on restart | `server.py`, `tools.py`, `ui/index.html` | committed (`f06940e`); untested live | git history |
| Experimental narration review step (`exp_audio_review`: levels, clipping, silence per WAV) | `experimental_workspace.py` | untested | `_old_20261016_043829` |
| Hindi story cadence (fewer choppy lines) | `story.py`, voice `speak.py` | **untested** live | `story.py_old_20261015_143001` |
| Genre naming: blank name becomes "Genre - generated title"; duplicates numbered | `tools.py`, `server.py`, `ui/index.html` | **untested** live | `_old_20261015_145044` |
| "Open result" opens the episode Output folder | `server.py`, `ui/index.html` | untested | `_old_20261015_145044` |
| Genre artwork: one character anchor, varied camera views, targeted repaint | `tools.py` | untested | `tools.py_old_20261015_14*` |
| Bhay reel title crash (halo wider than frame) | `engines/video/engine/horror_video.py` | **verified on PC** (reel 1 re-rendered) | `horror_video.py_old_20261015_144314` |
| Pacing index | `app/pacing.py` | untested | `pacing.py_old_20261015_232` |
| Paths moved into `config.json` (`paths`, `layout`) | `path_config.py`, `config.json` | in use (hub starts) | `_old\before_v42_layout` |

## 14 Oct 2026 (v4.2)

| Change | Status |
|---|---|
| Experimental workspace in `D:\HubGPT` with `00_Input ... Output` folders | in use |
| Activity log no longer freezes the page on long logs | compiled only at the time |

## 13 Oct 2026 (v3 to v4.1, see HANDOFF_HUB.txt for detail)

| Change | Status |
|---|---|
| Bhay horror pipeline end to end ("Bhay - Test Night Shift", 4 min) | **verified on PC** |
| All six series work like Bhay (genre job) | untested for non-Bhay series at the time |
| Voice per series, overnight training from Audacity projects | untested |
| English Sukoon episodes (Qwen3-TTS narrator) | untested on PC at the time |
| Reels page, Repair page, quick voice repair, quality gate, HarfBuzz Hindi letters | in use |
| Scripts with Gemini + ledger + topic bank | in use |
| Living elements (steam, lamp glow), elements library | in use |

## How to add an entry

One row per change: what you or the AI changed, the files, the status tag, the backup suffix. Put it under
today's date. When you test it, change the status to **verified on PC** and add what you ran.
