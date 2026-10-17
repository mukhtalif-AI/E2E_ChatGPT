HUBGPT - your creative workspace in one window                                (15 Oct 2026)
=====================================================================================================
Script -> your voice -> artwork -> living video -> reels, all from one light browser app.
Everything runs on this PC. Help inside the app: the "?" Help item in the left menu.

START
  setup_hub.bat            once: checks ComfyUI / selected local AI server / FFmpeg, puts the voice + video engines in place
                           (asks before every download)
  hub.bat                  starts the hub and opens http://127.0.0.1:8781 (keep its black window open)
  restart_hub.bat          after an update; asks first if a job is running (never cuts a job short)
  get_elements.bat         downloads the motion elements listed in app\elements_wanted.json
  install_text_shaping.bat (done) correct Hindi letters in videos (HarfBuzz) + a test picture in _tmp

LOCAL AI
  HubGPT uses Ollama at http://127.0.0.1:11434. Gemma is the default for story writing and Hindi; Qwen is used
  for image-prompt creation and reasoning-heavy video choices; Qwen vision handles picture review. Qwen3 native
  thinking is enabled for text-model calls; Gemma 3 (gemma3:12b) has no Ollama thinking control. Choose installed
  models on the System page. HubGPT unloads Ollama models after local-AI steps and before ComfyUI/video GPU work,
  while leaving the Ollama service running.
THE PAGES (left menu)
  Create
    Series           Bhay, Rahasya, Kisse, Romanch, Kareeb and Gyaan stories: script -> your voice -> scenes + sound
                     -> pictures -> video/reels. An AI story with a blank Story name is saved as Genre - generated
                     title; repeated names get a number so new runs do not reuse an existing episode folder. The
                     Activity "Open result" button opens the video Output folder for a full video job.
    Sukoon episode   Language: Hindi (your voice) or English (the English narrator, Qwen3-TTS "Ryan"/"Aiden").
                     "I have a script" (paste / open .txt / pick a written one; shows the speech time in YOUR voice)
                     or "Let the AI write it" (kind of episode, topic, 3 fresh ideas, copy prompt for a chat site,
                     only write scripts). Video length: if the speech is longer than the video, the AI shortens
                     the script and keeps the story (the full one is kept as *_full.txt).
                     Then: your voice -> music like your episodes -> artwork -> living video -> reels.
    Voice            your voice reads a script (best takes, calm <-> expressive, pauses, music, fix phrases)
    Pictures         paintings that follow a narration
    Video            a Sukoon video from a voice + picture(s) (typography, fonts, captions)
    Reels            new 9:16 reels from a finished episode: new moments / best moments / your own times,
                     length 20-60 s, camera motion (8 motions with previews, or Mix)
    Repair           check a video or reel (voice stutters & wrong words, subtitle rhythm, jumps / freezes /
                     loop pops) and fix what you tick; the old file is always kept in _old
    Image            any picture from words, or inspired by one of your thumbnails
  Library            Episodes (one card per episode: script / voice / video / reels / checked, English tag),
                     Scripts, Voices, Pictures, Videos & reels; search; Remove (moves to _old); new things
                     appear by themselves (no restart needed)
  Activity           live log of every job (each step shows how long it took), Stop, Run again, Clear finished
  Settings           Gemini key (script writing), Pixabay key (elements), music index, thumbnail style guide,
                     open the topic bank / ledger / elements library
  Experimental mode  the isolated full-site experiment at http://127.0.0.1:8781/experimental; it has its own
                     visual refresh and output workspace. Use the banner's return link to go back to regular mode.

HOME: SUGGESTIONS (the hub asks - nothing runs until you click)
  check a new video for glitches, open Repair when a check found something, make reels for an episode without any,
  write 5 scripts when all are used, a topic not used yet, teach the hub your thumbnail look, set up the English
  narrator, a job that failed. "Not now" hides one for 30 days.

WHAT HAPPENS AUTOMATICALLY
  Scripts      Gemini (or the local model) writes in your style; a recipe (kind, way of telling, one central
               image, healing place, closing) changes every time; the ledger remembers every script so nothing
               repeats; a second pass checks the script (night, one image, no repeats, natural Hindi).
  Voice        best of 3 takes per line, but an excellent first take is kept at once, and after two good takes
               the third is skipped; 1-3 word lines ("सो जाओ...") are not re-taken for the checker; counted numbers
               (चार... पांच...) are understood (faster, same quality); a fair checker
               (English words and same-sounding spellings are not "wrong"); say_as.json for words to respell.
  English      Gemini writes the script in English (same Sukoon rules, ledger, recipes); the English narrator reads
               it (same voice every line, calm bedtime direction, odd-length lines retaken); captions, title, reel
               hooks and branding ("Sukoon", "Good night") in English.
  Artwork      the AI reads the WHOLE script (not only its first lines) + its recipe, paints 3 versions in your
               thumbnail look, the vision AI keeps the one that fits best (others stay in the folder).
  Restarts     a job cut short by a hub restart carries on by itself (finished steps are skipped).
  Elements     lamps glow, tea steams (real Pixabay steam); missing elements are fetched in the background with
               the Pixabay key - commercial use, no credit, real names, every clip in ELEMENTS_INDEX.csv with
               where it came from and where it is used.
  Speed        the living artwork is drawn several frames at once (about 3x faster).
  Quality      every reel and every video is checked frame by frame after it is made; a reel or video that jumps,
               freezes or pops is made again once (the broken one goes to _old\broken_*).
  Subtitles    split where the meaning pauses, timed by the content; Hindi letters drawn with HarfBuzz.
  Layout       branding, light line and words stay inside the middle 80% (YouTube "zoom to fill" on long phones).

WORKSPACES
  Machine-specific paths are centralized in root config.json: roots in "paths" and folder names in "layout".
  update_all_code.bat rebuilds Code\ALL_CODE.txt and engines\video\ALL_CODE.txt; each previous snapshot is timestamp-backed-up.
  Secrets, local Python path files, environments, logs, backups, and bundled libraries are excluded from snapshots.
  Regular HubGPT     uses D:\MukhtalifGPT for hub media and D:\MukhtalifGPT\Episodes for episodes (defaults).
  Experimental mode  uses config.json paths.experimental_root (D:\MukhtalifGPT by default):
    Library\         scripts, uploads, and reusable Experimental assets
    Series\<series>\Episodes\<episode>\
      00_Input\      original request, activity metadata, and Experimental reports
      01_Script\     the episode script and story metadata
      02_Narration\  narration outputs and browser-recorded replacement takes
      03_Artwork\    generated artwork and scene images
      Output\        finished video, reels, and run reports
    Activity\       live job logs, jobs.json, and jobs_archive.json
  Voice code and trained checkpoints live in engines\voice and engines\voice\models in this project. Checkpoints
  are copied to configured voice_model_backup. Datasets/recordings remain in configured voice_data_root
  (default D:\SukoonVoice); Experimental narration files stay in each Experimental episode.

ACTIVITY LOG DISPLAY
  Long lines wrap inside the log panel. The browser requests smaller log chunks and keeps a bounded on-screen
  history; earlier text remains in the full job log under <experimental_root>\Activity\Logs. The job list updates only
  when a job's visible status changes, reducing redraws while live logs arrive.

WHERE THINGS ARE
  HubGPT project folder                 code, engines, settings: config.json; keys: secrets.json
     config.json                        machine-specific roots in paths; folder names in layout
     app\            server.py, tools.py (public step registry), steps\ (job steps), story.py, repair.py, elements_*.py
     app\ui\experimental\ isolated full-site UI copy; notes in EXPERIMENT_NOTES.md
     engines\voice   HubGPT Voice engine (say_as.json; trained checkpoints stay here)
     engines\video   HubGPT Video engine (loops\, work\; local Python environment)
     engines\english the English narrator (Qwen3-TTS, Apache 2.0; libraries copied from MukhtalifAudioBook)
     _old\           every earlier version of every changed file (nothing is deleted)
  configured hub_output_root             regular HubGPT media and data (default D:\MukhtalifGPT)
  configured episodes_root               regular episode folders (default D:\MukhtalifGPT\Episodes)
  configured experimental_root           Experimental Library, Series and Activity (default D:\MukhtalifGPT;
                                         D:\HubGPT is a legacy read-only source)
  configured voice_data_root             voice datasets/recordings (default D:\SukoonVoice)
  D:\Audio asset\Elements                downloaded elements + ELEMENTS_INDEX.csv (licence register) + ready\ loops

RULES THE HUB KEEPS
  Only D: and E:.  Nothing is ever deleted - old files go to _old.  Downloads only with a licence that allows
  commercial use without credit, saved under their real name, written into the register.  One graphics-card job
  at a time (script writing with Gemini, element fetching and checks run beside it).

RECENT CHANGES (15 Oct 2026)
  Story cadence     Hindi story prompts now discourage choppy conjunction starts and overly short, metronomic lines;
                    narration flow keeps phrase endings and joins related lines without adding automatic ellipses.
  Genre naming      AI-written stories with no manual name use the generated story title. Existing names are checked
                    across scripts, narrations, scene sets and episode folders; collisions receive a numbered suffix.
  Output button     Genre video jobs list the episode Output folder first. Open result creates that allowed folder if
                    needed, then opens it in Explorer.
  Genre artwork     Recurring characters keep a visual anchor while scene viewpoints vary. The image reviewer can
                    request a targeted repaint when a scene does not match the story/reference.
  Bhay reel render  The title halo now clips safely to the frame bounds; this fixes the broadcasting error that
                    stopped reel 1 when the title overlay extended outside the image crop.

CURRENT SOUND BEHAVIOR (17 Oct 2026)
  Genre sound      All six genre series use the shared horror_sound step. Its name is historical, not Bhay-only.
  Working mode    Keeps the established music mix and per-scene ambience behavior.
  Experimental    Adds place-based ambience continuity and, for Bhay/Rahasya/Kisse/Romanch, optional emotion-led
                   music sections. Experimental effects can be timed with Whisper word timestamps.
  Sound choices   Empty ambience is allowed when no bed fits; background music can carry the mood. The new
                   Experimental sound logic is in source but has only been verified on test copies, not live jobs.

TROUBLESHOOTING
  The page does not open        hub.bat is not running - start it (or restart_hub.bat)
  A new feature does not appear  restart_hub.bat (when no job runs), then reload the page (F5)
  Gemini times out              Gemma in Ollama takes over automatically; check the key and Ollama in Settings
  A voice line sounds wrong      Repair -> tick the line -> Repair; or add the word to say_as.json
  Wrong Hindi letters           run install_text_shaping.bat once (already done on this PC)
Notes for the next AI / developer: HANDOFF_HUB.txt (history of every version, how things fit together).
