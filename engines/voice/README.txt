SUKOON VOICE - your own voice for Sukoon, generated on this PC          (updated 13 Oct 2026, 04:30)
=========================================================================================
A voice model that has LEARNED your Sukoon narration and reads new Sukoon scripts in your voice.
HubGPT Voice engine: code and trained model checkpoints live inside this HubGPT codebase. Machine paths and folder layout are managed in the HubGPT root config.json (paths / layout). Voice datasets and recordings stay in the configured voice-data folder.
Shared only: ComfyUI's Python (no 2nd PyTorch), D:\AI\hf_models, FFmpeg (E:\Program_Files\ffmpeg).

CURRENT VOICE (default since 13 Oct 04:20) = "V3"
  speech model : run_20261013_0319_sukoon_flow_lr3e-05  (trained on your Sukoon lines + "flow clips")
  sound model  : base (your own trained sound generator made the pitch HIGHER -> not used)
  reading      : flow on (phrases that belong together in one go, YOUR script's endings kept exactly)
  delivery     : exaggeration 0.35 (calm), cfg 0.4, temperature 0.7
  quality      : best of 3 takes per phrase; a phrase is remade (up to 7 tries) if >10% letters heard wrong,
                 its length is off, or the stutter detector hears a repeated piece
  finishing    : whole in-breaths kept (lead_keep 0.2), de-click, your looped room tone under the pauses,
                 -18 LUFS, music bed mixed (narration_music.wav)
  Old settings: _old\config_before_V3_defaults.json (and _old\config_before_night.json)

HOW IT WORKS (short)
  Base model: Chatterbox Multilingual (Resemble AI, MIT, Hindi). A small add-on (LoRA ~45 MB) is trained on
  your lines so the speech model learns your pace, pauses, pronunciation and calm Sukoon delivery.
  Flow clips (13 Oct): 2-4 of your lines in a row WITH the real pauses/breaths between them - so the model
  learns how you carry on from one phrase to the next instead of ending every phrase like an instruction.

FIRST TIME / DAILY USE
  setup.bat            once: picks ComfyUI's Python, installs libs\cb, asks before any download
  sukoon_voice.bat     the menu: 1 dataset, 2 train, 3 A/B test, 4 speak a script, 5 open output, ...
  speak.bat            drag a script.txt / episode folder onto it -> uses the CURRENT VOICE above
  Commands (python run.py ...):
    speak <script> [--name N] [--model run|latest:sukoon_flow|best|base] [--flow on|off] [--redo 3,17]
          [--set key=value ...]   (--set changes one setting for this run only, e.g. --set exaggeration=0.5)
    flowdata                       build the flow clips (dataset\flow_clips + metadata_flow.jsonl)
    train --new --profile sukoon_flow|sukoon_only|sukoon_calm|all [--lr 3e-5] [--epochs N]
    score [--dir "night_*"]        compare narrations (likeness, wrong letters, pace, pitch) -> night_scores.txt
    dataset | s3train | grid | final <script> | test
  Redo one phrase: python run.py speak <script> --name <same name> --redo 12   (only phrase 12 is made again)

SCRIPT STYLE IT UNDERSTANDS
  one phrase per line; (...) = long pause; "..." = pause and a soft, trailing ending; blank line = paragraph;
  headings / (stage notes) skipped; a line of ----- ends the script (first version only).
  Your punctuation is kept exactly - it carries your style ("..." trails off, "।" ends a sentence).

OUTPUT LOCATIONS (roots and folder names are configured in the HubGPT root config.json)
  configured voice_data_root\<voice_dataset folder>\ clips, flow clips, refs, metadata*.jsonl (default root: D:\SukoonVoice)
  E:\Projects\hub_chatgpt\engines\voice\models\<run>\  best.pt (used), last.pt, train_log.txt, history.json
  configured narrations_root\<name>\  narration.wav, narration_music.wav, script.txt,
                                timing.json (start of every phrase), report.txt (phrases to listen to),
                                _lines.json (per phrase: letters wrong, likeness, pitch, repeat score, take)
  _send copies live under the configured narrations_root

SETTINGS  config.json -> "speak": model, flow, takes, max_retakes, max_letter_error, max_repeat, exaggeration,
  cfg, temperature, room_tone, declick, lead_keep, flow_min_pause (keep <= 0.6: higher = abrupt pauses),
  pauses, music, music_under_db, with_music.   "train": profile, lr, flow_weight, series_weight, patience.
LOG  logs\last_run.txt (or the SV_LOGNAME file) = everything the window showed - send it when something fails.

RULES  only D: and E:, never C:. Nothing deleted (old versions -> _old\ or *_old_*). Asks before installs.
