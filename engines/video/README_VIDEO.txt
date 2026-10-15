HUBGPT VIDEO  -  video generation engine
====================================================
HubGPT Video is an engine within HubGPT; machine-specific paths are configured in the HubGPT root config.json.
The video engine code and its local Python environment live in engines\\video.

EASIEST: "New Sukoon Video.bat"  (in this folder; episode root is configured in root config.json)
  Double-click -> choose the Audacity project (.aup3) -> soundtrack (your exported mp3/wav next to it is
  found automatically; or it mixes the project itself) -> episode name ("Sukoon 9.0 - ...") -> script?
  Yes = paste it, No = recognised from your voice -> artwork (Cancel = automatic).
  It COPIES your files into the configured episodes_root\<name>\ and makes output in the configured episode_output folder:
  the video, clips\, reels\ (+ captions) and sample frames. The Output folder opens when it is done.

NEW EPISODE (by hand)
  Double-click  <configured episodes_root>\New Episode.bat  -> a new folder + empty script.txt open for you.
  Paste the Gemini script, save; put in the Audacity project (.aup3), exported audio (.mp3/.wav) and the
  artwork (.png/.jpg); rename only the outer folder (e.g. "Sukoon 9.0 - Trip To Bachpan").
  The number in the folder name becomes the on-screen badge "सुकून 9.0" (no number -> just "सुकून").

MAKE THE VIDEO
  Drag the EPISODE FOLDER onto make_video.bat   - or double-click make_video.bat and pick the folder.
  The window shows each step and when it will be ready (also in the window title).
  Result in <episode folder>\Output\  (older episodes: output\<episode>\ here) :
    <episode>.mp4      the full YouTube video (opening "सुकून 8.0" title, corner badge, breathing tagline)
    clips\            4 short 16:9 clips around the best moments (whole sentences, soft fades)
    reels\            3 Instagram teaser reels 9:16 + a ready-to-paste caption .txt next to each
    <episode>_qa\     sample frames to glance at

  With script.txt : your exact words + perfect spelling, and much faster (no heavy speech model).
  Without script  : the words are recognised from your voice (slower; a few may be mis-heard).
  Old way still works: drag a single .mp3/.wav (artwork from D:\Audio asset\Images by name).

WHAT HAPPENS (steps 1-6 in the window)
  with script : 1 voice  2 quick timing pass  3 script lined up with your voice  4 Qwen picks ~10 moments
                5 animated artwork (once per image)  6 GPU render + clips + reels   ~20 min for a 35-min episode
  no script   : 2 Hindi speech model (longest)  3 cross-check with a 2nd recogniser, rest the same
  Text is kept away from faces/people in the artwork (local vision model).

CLIPS + INSTAGRAM REELS AGAIN (no long render)
  Drag the episode folder (or its output folder) onto make_reels.bat - ~3 min.
  Each reel: hook question (first 3 s, written by the local AI from what you say there), your words on
  screen, full last sentence, then "पूरी वीडियो YouTube पे".  To change a hook: edit work\<name>\reels.json.

IF THE PC CRASHES / POWER CUT
  Just start it again the same way. Finished steps are skipped, the transcript resumes where it stopped,
  and the video continues from the last finished 2-minute chunk.

CHANGE TEXT (optional)
  work\<name>\moments.json  (shown lines)   work\<name>\title.json  (title card)
  then drag the audio onto make_video.bat again - only the video is rebuilt.

OPTIONS   --label "Sukoon 8.0"  --clips 4  --reels 3  --preview 120  --redo-captions  --redo-transcript  --image "..."  --typo mukhtalif|verse|diary|shayari|classic
          --llm Qwen3.6-35B-A3B-NVFP4  --viz line|bars|none  --no-title  --no-brand  --no-dim      (set SV_GPU=0 = CPU render)

FOLDERS
  make_video.bat   make an episode (video + clips + reels)
  make_reels.bat   only remake clips + reels of a finished episode
  engine\          program files (Python)
  assets\fonts\    Kalam, Amita, Aparajita, Tiro ... (Google fonts are OFL-licensed)
  loops\           animated artwork (reused per image)
  HubGPT work: <episode>\_work\  voice.wav, transcripts, moments.json (edit to change text), title.json
  Video work: configured video_work_root\<episode>\
  output\<episode>\  finished video + sample frames
Uses: the video engine Python venv in engines\video\.venv, ComfyUI Python for GPU steps, configured FFmpeg,
Ollama, and speech models in D:\AI\hf_models. Machine-specific paths are configured in the HubGPT root config.json (paths); folder names are in layout.
Gemma is used for Hindi story writing; Qwen is used for image prompts and video moment/title reasoning. Ollama unloads
the active model before ComfyUI and GPU rendering so the graphics workloads can use the available VRAM.
