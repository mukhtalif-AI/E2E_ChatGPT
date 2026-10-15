HUBGPT VIDEO ENGINE - CODE REFERENCE
==========================================================================
This is the HubGPT Video engine code reference. The live program runs from engines\video\ inside this HubGPT project.
Machine-specific paths are configured in the HubGPT root config.json (paths); folder names are in layout.
ALL_CODE.txt is generated from the current video-engine source. From the HubGPT root, run
update_all_code.bat to refresh both the full HubGPT snapshot and this video-only snapshot. Each replaced snapshot
is backed up with a timestamp. secrets.json, Python path files, environments, logs, and bundled libraries are excluded.

LAUNCHERS (double-click)
  first_run.bat          NEW PC / health check: folders, fonts, FFmpeg, Pythons, selected local AI server and models
  New Sukoon Video.bat   pick .aup3 -> script in Notepad -> title -> everything automatic   (engine\new_video.ps1)
  make_video.bat         drag an episode folder (or audio file) -> video + clips + reels    (engine\sleep_pipeline.py)
  make_reels.bat         remake clips + reels of a finished episode                           (engine\extras.py)
  tidy_studio.bat        move old test files to the testing ground (deletes nothing)
  test_mix.bat           compare the automatic Audacity mix with your exported mp3/wav

THE PIPELINE  (engine\sleep_pipeline.py runs these steps)
  1 voice        aup3_voice.py (+ aup3_doc.py)   voice track out of the Audacity project, read-only
                 aup3_mix.py                     full mix from the project when no mp3/wav was exported
  2 timing       sleep_pipeline.transcribe       quick Whisper turbo (faster-whisper) word timings
  3 words        script_align.py                 your script(s) lined up with your voice; several versions -> best per sentence
                 sleep_pipeline.gap_fill + asr_hindi.py   parts not in the script: Hindi Whisper (GPU) on just those parts
                 (no script: asr_hindi.py on everything + cross-check)
  4 moments      sleep_pipeline.pick_moments     selected local AI model picks ~16 key lines + key words; make_title asks YOU
  5 artwork      artloop.py                      40 s seamless living loop of the picture (once per image)
                 subject.py                      vision model finds people -> text avoids them
  6 video        compose.py                      plan + CPU renderer; GPU path -> gpu_compose.py (ComfyUI python, NVENC)
                 overlay.py                      light line, title cards, silver corner badge, breathing tagline, fonts
                 kinetic.py                      word-by-word Hindi typography (key word in amber)
                 qa.py                           sample frames + checks
  extras         clips.py                        4 short 16:9 clips (whole sentences); episode_segments() = the
                                                 spoken lines incl. voice-filled parts (used by clips + reels)
                 reels.py                        3 Instagram reels 9:16: hook = your own best line, smooth GPU camera,
                                                 light specks, words, end card, -14 LUFS, captions;
                                                 branding = REEL_BRAND "corner" (the video's corner badge, top-left)
                 extras.py                       clips + reels again without re-rendering
  helpers        paths.py (reads machine-specific paths from the HubGPT root config.json), first_run.py (setup),
                 sv_av.py (paths, ffmpeg, Ollama), check_env.py (checks the PC), sleepgen.py (older generator),
                 compare_audio.py (used by test_mix.bat), new_video.ps1 (the New Sukoon Video dialogs)

WHAT IT NEEDS ON THE PC   (first_run.bat checks and installs all of this; these are the original PC's paths)
  Python venv   engines\video\.venv        (faster-whisper, PyAV, numpy)
  GPU Python    E:\ComfyUI_windows_portable\python_embeded\python.exe   (torch CUDA, PIL) - Hindi model, video, reels
  FFmpeg        E:\Program_Files\ffmpeg\bin
  Ollama        local model API at http://127.0.0.1:11434; Gemma writes stories, Qwen handles reasoning tasks
                Models used for video decisions are unloaded before GPU rendering; the Ollama service stays running
  Hindi model   vasista22/whisper-hindi-large-v2 in D:\AI\hf_models
  Fonts         engines\video\assets\fonts

FILES PER EPISODE
  D:\Mukhtalif\Episodes\<name>\   .aup3, .mp3/.wav, script.txt (versions split by -----), title.txt, artwork.txt, picture
                                 Output\  <name>.mp4, clips\, reels\ (+ _caption.txt), <name>_qa\
  configured video_work_root\<name>\   transcripts, moments.json (edit text), title.json, reels.json (edit hooks)
