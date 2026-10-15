HUBGPT ENGLISH NARRATOR (15 Oct 2026)
Reads English Sukoon scripts with Qwen3-TTS (Alibaba, Apache 2.0 - commercial use OK), built-in voice Ryan (or
Aiden): the same voice on every line, a calm bedtime-story direction, fixed seed. Lines that come out much too short
or too long are spoken again (best of up to 3). Output uses the configured narrations_root from the HubGPT root config.json, so the hub's music, video, reels and Repair work the same way (lang.txt = en marks it English).

  setup.bat      libraries -> libs\ (copied from E:\Projects\MukhtalifAudioBook\libs if present, else pip; asks)
  test.bat       three short lines -> D:\MukhtalifHub\_tmp\english_test\ (speed + sound), log in logs\test.txt
  run.py speak <script.txt> --out <narrations folder> [--name N] [--redo 3,7] [--speaker Ryan] [--engine qwen]
  config.json    engine (qwen | qwen_fast), speaker, batch, takes, loudness; the calm direction is in en\config.py
Python: ComfyUI's (python.txt). Models: D:\AI\hf_models (shared; Qwen3-TTS 1.7B CustomVoice already there).
