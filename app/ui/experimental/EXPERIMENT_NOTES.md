# HubGPT Experimental Mode

This is a separate full-site UI copy, opened at `http://127.0.0.1:8781/experimental`.

## Current experiment: isolated workspace and visual refresh

- Warmer, higher-contrast surface colors with a readable light default and a working dark theme.
- Clearer title hierarchy, spacing, cards, form controls, buttons, and focus indicators.
- Responsive horizontal navigation on small screens, skip-to-content link, and reduced-motion support.
- Persistent mode banner with a return link and a note describing the experiment.

The Experimental UI is served from `app/ui/experimental/index.html`; stable mode remains at `/`. Shared server changes are limited to dispatching Experimental requests and routing their files and jobs to the isolated workspace. Job ordering, model selection, and media-generation functions stay the same.

Experimental outputs go under the Experimental root, `config.json` `paths.experimental_root` (`D:\MukhtalifGPT` since 16 Oct ~16:40; before that `D:\HubGPT`, which the hub still reads for old jobs and files): scripts and uploads in `Library`, per-run files in `Series/<series>/Episodes/<episode>/` (`00_Input`, `01_Script`, `02_Narration`, `03_Artwork`, and `Output`), and job logs and history in `Activity`. Existing configured output folders remain in use by the regular HubGPT page. Voice model and training files remain shared; Experimental narration audio goes into each episode workspace.

The Activity log view wraps long lines, keeps a bounded display history, requests smaller log chunks, and avoids redrawing an unchanged job list on every status poll. These changes are intended to stop long logs from expanding the page and stalling its repaint.

## Experimental narration stack trial

The Experimental workflow can append a `exp_audio_review` step immediately after the existing Chatterbox narration stage. It writes `experimental_audio_review.json` in that run's narration folder and shows the measured per-WAV values when the run is loaded: duration, peak and RMS levels, clipped-sample percentage, near-silence percentage, and start/end edge levels. These are diagnostic signals only. The step does not label a take as natural, change audio, or trigger a regeneration.

The report records which pieces are actually active: the current HubGPT Voice / Chatterbox synthesizer, the Hindi Whisper checks and pronunciation/stutter retakes already performed by that voice pipeline, the configured local story director, and the deterministic signal review. IndicF5 is listed as a candidate because there is no IndicF5 runtime or adapter in this checkout. faster-whisper remains available to the video engine and is not run as an additional narration verdict. This trial and its job-dispatch hook are Experimental-only; the regular site and shared voice behavior are unchanged.

## Review / promotion

Compare the same pages and settings in regular and Experimental mode. Keep this copy experimental until the user explicitly asks to promote the design.


## Shared HubGPT updates (15 Oct 2026)

The shared genre planner now avoids reusing names when a new script stage is requested. Blank-name AI stories are
renamed to `Genre - generated title` once the story title is available; later job paths and outputs are updated with
the final name. Existing-episode jobs without a script stage retain their selected episode. Genre video jobs expose
the episode `Output` folder as their first result. The stable UI's Open result action may request creation of that
allowed output directory before opening it in Explorer.

Hindi genre scene prompts now keep a compact character anchor while varying viewpoint and can request a targeted
image correction after review. They do not require a rear-view boy or silhouette in every scene. The narration cadence
changes live in shared `app/story.py` and the HubGPT voice copy; the experimental UI is still a separate visual copy.
No live-browser verification has been done for these changes; restart the hub while idle before reviewing them.


## Tinker panel and installing into Working mode (16 Oct 2026)

Experimental mode has a **Tinker** page (sidebar). It changes settings for Experimental runs only:

- **Models**: script writer, script engine (auto / local), Gemini model, general model, image prompt model, picture judge, ComfyUI image engine.
- **Prompts**: the long prompts in `story.py` and `tools.py` (Sukoon rules and checklist, Bhay rules, shorten, scene planner, artwork, picture picker, translate). Placeholders the code fills in, such as `{want}`, are checked on save.
- **Series settings**: `tools.GENRES` (music folders, voice, pause, expressiveness, temperature, drama), `tools.STYLES`, `tools.DIRECTION`, `sfx_lib.SERIES`.
- **Writing rules**: per-series `genre_rules.json` in the Experimental Library.

Settings are stored in `<Experimental root>\Tinker\settings.json` (now `D:\MukhtalifGPT\Tinker\settings.json`). Every Experimental job takes a snapshot when it is queued (`params._tinker`) and the job log lists what was changed. Light jobs (sound-effect fetches, checks) do not use the snapshot.

**Install into Working** copies chosen changes into the files Working mode already reads, after an `_old_<timestamp>` backup of each: `hub_settings.json` (llm, prompt_model, vision_llm), `secrets.json` (local_model, story_engine, gemini_model), `config.json` (image_engine), `working_tinker.json` in the hub folder (prompts and tables, applied at hub start and immediately), and the working `genre_rules.json`. Installing is refused while a job runs. The Installed tab lists every install with its backups; "Working: back to code" drops an installed prompt or table.

Code boundary: `app/tinker.py` (new), `experimental_workspace.prepare/job_config` (snapshot and models), and narrow `server.py` hooks (`/api/tinker*` routes, `tinker.enter/leave` around Experimental job steps, `tinker.install` at start). Prompts and tables are module values, so while an Experimental job runs, a script or prompt preview requested from the Working page at the same moment would see the Experimental values; jobs never overlap because the GPU queue runs one at a time.


## Ratings, AI self-review and Sounds (16 Oct 2026)

- **Ratings** (very bad, bad, avg, good, very good + optional note) on videos and their parts (script, voice, BGM,
  SFX, SFX timing, picture theme, picture quality, subtitles), reels, narrations, scripts, pictures, music tracks,
  effect files and single SFX cues (on time / too early / too late / wrong sound / not needed). One SQLite file:
  `<Experimental root>\Activity\ratings.sqlite`, with series, settings, models and prompt versions of the run
  (prompt texts kept once per version under `Activity\PromptVersions`). Code: `app/feedback.py`.
- **Review page** (Library → Review): play from just before each effect, mark it, rate every part and picture.
- **Word-timed SFX**: Experimental genre runs get a step `exp_cue_times` before the sound mix. Whisper word times
  (`app/word_times.py`, run in the video engine's Python, cached as `words.json` next to the narration) place each
  effect and close-up at the spoken word; before, the time was guessed from the word count inside the sentence.
  Shared change: `tools.horror_sound` uses a cue's `at` when the plan has one (Working plans never do).
- **AI self-review** (`app/self_review.py`, step `exp_after_video`, or the Review page button): effect timing and
  sense, voice vs script, subtitle sync, pictures (vision model), script (story model); music/effects only from your
  own earlier ratings. Its ratings show as "AI" until you change them. Writes `Output/self_review.json`,
  `Output/review_sources.json` and, when needed, `Output/credits.txt`.
- **Uses**: Experimental music and effect picking skips files rated (very) bad or with a licence that is not allowed,
  and prefers well-rated ones (wraps `tools._horror_track` / `tools._sfx_names` only when `experimental_mode`).
  Ratings page: weak spots per series with links into Tinker, "Ask the AI to revise" a prompt or writing rules from
  your notes (saved for Experimental only), prompt/model versions compared, AI-vs-you gap, training export (JSONL).
- **Sounds page** (`app/sound_library.py`): browse and rate BGM/SFX, import with licence, search Openverse (no key)
  or Freesound (key) for CC0 / public domain (CC BY when allowed, credit recorded), download in the light job lane,
  licence re-checked with the source. Registers: `<BGM>\BGM_INDEX.csv`, `<SFX>\SFX_INDEX.csv` (two new columns).
- Server: narrow dispatch of `/api/rate/*` and `/api/sounds*`, `feedback.install(CFG)` at start.

## Music downloads, audio screening and speech clarity (16 Oct 2026)

- **Music downloads**: Sounds > Get more has mood buttons; "More music for a series" searches Freesound (CC0) per mood folder (step exp_music_topup).
- **Screening** (app/audio_screen.py): every music track / effect is measured (speech-band share 300 Hz-3 kHz, highs above 3 kHz, loudness range, peaks). Verdict ok / risky (a treated FLAC copy in Library/_treated_audio is used, originals untouched; music also ducked 4 dB more) / reject (never picked; rejected downloads go to Library/_rejected_downloads). Cache: Activity/audio_screen.json. Your choice per file on the Sounds page wins. Limits: Tinker table audio_screen.LIMITS.
- **Speech clarity**: AI self-review aspect "clarity" compares narration.wav with narration_music.wav per line; the Review page lists the hardest-to-hear and piercing moments.
- YouTube Audio Library has no API and its terms forbid automated downloads: download by hand, then Import with that licence.
- Backups: *_old_20261016_063425_screening.

## Save / Git page and How to use (16 Oct 2026)
- Sidebar "Save / Git" -> app/git_save.py (routes /api/git/*: status, log, diff, commit, push, pull, fetch, remote, identity, init). Fixed git argument lists only, no shell; user text only as commit message, remote URL, name/e-mail, or a path git itself listed.
- Commit = `git add` for ticked new files, then `git commit --only -- <ticked paths>`; other staged changes stay staged. Secrets, model/media files, backups (_old_), logs, files over 3 MB and files that look like they hold an API key are never offered.
- Pull (`--ff-only`) and init are refused while a job runs. Shared server change is two route dispatches in server.py.
- Help page rewritten to cover Tinker, ratings/Review, AI self-review, Sounds, word-timed SFX, export, Save / Git.

## Playback speed (16 Oct 2026)

UI-only, in `index.html` (search `playSpeed`). Every `<audio controls>` / `<video controls>` gets a Speed row (1x, 1.5x, 2x, 3x, custom 0.25-4) inserted after it by a MutationObserver; `HTMLMediaElement.play` is wrapped so script-driven playback (SFX cue jumps, Review seek buttons, `new Audio`) uses the same speed. One shared speed, `preservesPitch` on, stored in localStorage `hub_playSpeed`; keys `<` / `>` step it. Muted looping motion previews and the hidden music audition stay at 1x. No server change. Backup `index.html_old_20261016_065837_speed`.

## Corner narrator (16 Oct 2026)

- **Narrator page** (sidebar): upload a transparent half-body PNG (male / female, series tags) or paint original ones with ComfyUI (kind `narrator_art`, flat green background keyed out; `rembg` is used if installed). Mouth and eye points are guessed (neck = narrowest row, then darkest lip line / eye spots); click the picture to correct. Optional `mouth_open.png`, `mouth_closed.png`, `eyes_closed.png` of the same size. Store: `<Experimental root>\Library\Narrators\<id>\` (+ `_removed`, `_old`, `_frames` preview cache, `_jobs`).
- **Settings per series** in the Tinker table `narrator.SERIES` (on, narrator, corner / size / margin / dock / shadow / blink / appears-after, separately for 16:9 and reels), saved through `tinker.save`, carried in each run's Tinker snapshot, and installed into Working through Tinker like any table. Code value is `{}`, so Working mode has no narrator until installed. `narrator.THEMES` = ComfyUI look per series. Per run: Create pages -> Corner narrator (series setting / on / off / narrator).
- **Steps**: `tools.plan` is wrapped at start (`narrator.install`) to add `narrator_overlay` after `horror_video`, `genre_reels` and `studio_video` when the series (or run) has it on; kinds `narrator_art` and `narrator_apply` (finished video). The clean video is kept in `Output\_clean` (`reels\_clean`), so redoing it needs no render; `narrated.json` there stops double overlays.
- **Lip sync** (`app/narrator_render.py`, hub Python: numpy + Pillow + FFmpeg): pre-emphasised loudness of the clean `narration.wav` at 100/s, gated by Whisper `words.json`, attack/release smoothed, 6 mouth levels. Genre timing is exact (music lead, reel window); Sukoon/studio videos find the voice by cross-correlation. Pair mode blends closed/open PNGs; anchor mode pushes lower lip and chin down and draws the gap. Breathing (1 % stretch from the bottom), blinks (picture or drawn lids), soft shadow, fade in after "appears after". One re-encode per video: h264_nvenc (cq 20) else libx264 (crf 19), audio copied.
- **Subtitles**: with a bottom corner, `tools.horror_video` / `tools.genre_reels` are wrapped so the plan JSON gets `sub_room` (share of width kept free each side); `engines/video/engine/horror_video.py` narrows the subtitle wrap width by it (2 lines, ignored when absent, so Working is unchanged).
- Model-based lip sync (Wav2Lip, SadTalker, MuseTalk, LivePortrait) is not installed: see the note on the Narrator page.
- Backups: `*_old_<timestamp>_narrator`.

## Same Create settings for every series, and the build queue (16 Oct 2026)

- Sukoon and all six genres now open one Create form (`R.make` in `index.html`), laid out like the genre form, with a Series row on top. Every setting shows for every series; a setting a series cannot use is greyed out with a one-line reason (for example English, Kind of episode and Artwork are Sukoon-only; Scenes, Reel length, Content level and Acoustic context are genre-only). The old forms remain as `R.episodeClassic` / `R.genreClassic`. Jobs are built with the same job kinds and parameters as before (`episode` for Sukoon, `genre` for the others). One addition: choosing a specific voice tone for an AI-written genre story now sends `voice_style_manual`, so the tone is used instead of the automatic one.
- Each series' starting values: `app/create_form.py` `DEFAULTS`, a Tinker table (`create_form.DEFAULTS`, Tinker > Series settings). "Save as the <series> defaults" on the form writes the Experimental Tinker settings; install into Working like any other table.
- Build queue: each Make click adds one job to the existing graphics-card line; the list under the form (and the Build queue page) shows what runs and what waits, with move up/down, Run next and Remove (Remove = stopped, Run again in Activity puts it back). Order is the job's `queue_pos` (default: time added), kept in the job record, so it survives a hub restart; a job cut short by a restart carries on first. Working-mode jobs share this line.
- Server: `Jobs.queue_key` (worker picks by it) and two narrow dispatches (`/api/create/*`, `/api/queue*`) in `server.py`; `create_form.DEFAULTS` added to `tinker.TABLES`. Backups: `*_old_20261016_075218_queue`.

## Scene director rework (16 Oct 2026)
- Genre scenes end at a sentence end about every 30 s; the director writes a 'what happens' line per scene, a second step turns it into the picture prompt, and invented weather or props are rewritten or cut. Gemma 4 replies are parsed safely; Whisper re-times lines it skipped.
- Video page switch "Plan the scenes and paint the pictures again": the old scenes go to `<scenes>\_old_<time>`, the old video to `<name>_old_<time>.mp4`, then a fresh plan, pictures and video (`tools._set_aside_scenes`, `horror_scenes(replan, video)`, set in `prepare()`). The director rework now also runs in real hub jobs (`experimental_mode` check). Backups `*_old_20261016_185351_replan`.
- Short episode names (Experimental genre runs): `<Series>-<typed name or topic>` else `<Series>-<MMDD-HHMM>`, set at queue time; one folder per episode. (Later the same day: an AI-written story with no typed name is renamed once from its English title, see below.) Tool runs on an existing voice reuse its episode folder. Files: `experimental_workspace.py` (prepare, `_short_name`, `_episode_of_narration`), `tools.unique_genre_name` (accepts `Bhay-x`), `index.html` (no dated default name). Backups `*_old_20261016_181424_names`.
- Experimental default models at first: gemma4:12b-it-qat for script, director and picture prompts (Working stays gemma3:12b). Later the same day the script writer became gemma4:26b-a4b-it-qat, see below. Files touched: `tools.py`, `story.py`, `tinker.py`, `word_times.py`. Backups: `*_old_20261016_103740_director`, `*_old_20261016_174116_json`.

## Evening of 16 Oct 2026: models, picture check, ending, names, emotion marks

Everything here is Experimental only unless it says "shared". All of it is loaded since the hub restart at 23:31.
How far each part was tested is in `CHANGELOG.md`; open problems are in `KNOWN_PROBLEMS.md`.

- **Models** (Tinker, `<Experimental root>\Tinker\settings.json`): script writer `local_model` = gemma4:26b-a4b-it-qat
  (story engine local); `director_model` and `scene_picture_model` = gemma4:12b-it-qat; picture judge gemma3:12b
  (`picture_check.CHECK_MODEL`, or `picture_check_model` in the hub config; not on the Tinker page). Shared: every
  `free_gpu` step now unloads all Ollama models the job used, director and picture models included, so the 26B
  (~16 GB, part in RAM) is out before the voice and ComfyUI.
- **Thinking**: Gemma 4 thinks by default in Ollama and wrote ~2900 hidden tokens for an 80-word answer (1-2 min each,
  model loads were only 18-27 s). Picture checklists and repaint prompts now ask with `think: false`
  (`story.local_chat` when cfg `_no_think` is set; `picture_check._ask_quick`). Script, director, picture prompts and
  emotion marks keep thinking on, by your choice. Ollama's own log: `C:\Users\Shail\AppData\Local\Ollama\server.log`.
- **Picture check** (`app/picture_check.py`, called from `tools.pick_scenes`): checklist per scene from the director's
  'what happens' line; the judge answers plain questions (people count, subject shown, unwanted things, text, broken
  bodies, shot, scary); the code decides. Fails only on a wrong subject, too many people, a 'must not show' item,
  readable words/logo/watermark or broken bodies; the rest lowers the score. A failing scene gets a positive-worded new
  prompt and is repainted up to 2 rounds, then flagged (shots.json `flag`, Ratings page); the video is still made.
  Images go to Ollama with an 8k context.
- **Ending**: 'Whole video' 0 now means the voice + 10 s of music (was 1 min; first set to 7 s), then the picture fades
  into a blurred, series-tinted copy, a SUBSCRIBE button is clicked and the bell rings (`engines\video\engine\subscribe_end.py`,
  `tools._subscribe_end`, audio in `narration_music_end.wav`). Not on reels or Sukoon. Tinker > Models: 'Ending after
  the voice' and 'Subscribe + bell animation'.
- **Names from the title**: with no typed name, the episode is renamed right after the script step to
  `<Series>-<English title words>` (e.g. `Kisse-Clink-Steel-Tiffin`, `Bhay-Incomplete-Tea`), before anything else is
  saved in its folder (`experimental_workspace.latin_title` / `title_name`, `tools.horror_story`).
- **Emotion marks**: 15 lines per request with a 16k context, and a cut or empty reply keeps its complete lines
  (`tools.mark_emotions`, `EMO_BATCH`). Working still sends 60 lines.
- **Picture prompts, all series** (shared `tools.py` / `story.py`): styles say what to paint, never "no X";
  `PICTURE_RULES` in the director asks; mature stories may show blood and wounds; Kisse character anchor no longer
  pasted as Python code.
- **Plan the scenes again**: Video page switch; the old scenes and video are moved aside, never deleted.
- Backups: `*_old_20261016_picturecheck`, `_gemma3`, `_191309_26b`, `_162952_subend` (+ `_subend2`, `_subend3`),
  `_163735_emo`, `_222253_titlename`, `_232000_think`, `_142552_prompts`, `_185351_replan`.
- **Emotion map** (17 Oct, `app\emotion_music.py`, step `exp_emotion_map` before the sound; plan `EMOTION_MUSIC_PLAN.md`): mood + weight 0-3 per scene, music sections (max 3, no jumping back) and turn lines, in `emotion_map.json` next to `shots.json`. Bhay, Rahasya, Kisse, Romanch only. The sound does not use it yet.
