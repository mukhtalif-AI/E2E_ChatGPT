"""tools.py - what the hub can do. Each job = a list of steps; each step = one function below (fn(ctx, **args)).
The other projects are only CALLED (their own commands / local web APIs) - never changed."""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave
from comfy import ENGINES, _get, _post, comfy_running, start_comfy, comfy_generate, upload_image

WIN = os.name == "nt"


def ctx_folder(ctx, key):
    """Resolve a generated folder name from the root HubGPT layout config."""
    layout = (ctx.get("cfg") or {}).get("layout") or {}
    return os.path.join(ctx["media"], layout.get(key, key))


def narrations_root(cfg):
    """Canonical generated-voice output folder; experimental jobs stay inside their episode workspace."""
    leaf = (cfg.get("layout") or {}).get("narrations", "narrations")
    if cfg.get("experimental_mode"):
        return os.path.join(cfg["sukoonvoice_media"], leaf)
    return cfg.get("narrations_dir") or os.path.join(cfg["sukoonvoice_media"], leaf)


def narration_dir(cfg, name, existing=False):
    """Resolve a narration, preferring the canonical folder and retaining old HubGPT Voice outputs."""
    primary = os.path.join(narrations_root(cfg), slug(name))
    legacy = os.path.join(cfg.get("sukoonvoice_media", ""), "narrations", slug(name))
    if existing and not os.path.exists(primary) and os.path.exists(legacy):
        return legacy
    return primary


def voice_model_root(cfg):
    """Primary trained voice checkpoints live beside the HubGPT voice-engine copy."""
    return cfg.get("voice_models_dir") or os.path.join(cfg["sukoonvoice_dir"], "models")


def studio_work_dir(cfg, episode_dir, video_name=None):
    """Keep Studio's generated timing/transcript cache with its episode; fall back to the old engine cache."""
    name = video_name or os.path.basename(episode_dir.rstrip("\\/"))
    local = os.path.join(episode_dir, cfg.get("episode_work_name", "_work"))
    if os.path.isdir(local):
        return local
    legacy = os.path.join(cfg["studio_dir"], "work", re.sub(r"[^\w\-]+", "_", name).strip("_"))
    return legacy if os.path.isdir(legacy) else local


def model_chat(cfg, model, prompt, temperature=0.3, as_json=False, image_path=None, image_paths=None, timeout=600):
    """Call Ollama's local API, optionally with one image."""
    import base64
    cfg["_ollama_used"] = True
    message = {"role": "user", "content": prompt}
    paths = image_paths or ([image_path] if image_path else [])
    if paths:
        message["images"] = [base64.b64encode(open(path, "rb").read()).decode() for path in paths]
    body = {"model": model, "stream": False, "options": {"temperature": temperature}, "messages": [message]}
    # Without num_ctx Ollama loads vision models at their full 128k context: 13 GB, spills to CPU, checks time out.
    if paths:
        body["options"]["num_ctx"] = int(cfg.get("vision_num_ctx", 8192))
    # Native reasoning is supported by Qwen3 text models, but not the configured Gemma 3 model.
    if model.lower().startswith("qwen3") and not any(tag in model.lower() for tag in ("vl", "vision")):
        body["think"] = True
    if as_json:
        body["format"] = "json"
    result = json.loads(_post(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") +
                              "/api/chat", body, timeout))
    return result["message"]["content"]

# ------------------------------------------------------------------ presets
STYLES = {
    "sukoon_night": ("Sukoon night (dreamy, calm)",
                     "soft dreamy digital painting, calm night, deep indigo and navy blue tones with a warm amber "
                     "lamp glow, gentle moonlight, cozy and peaceful, soft focus, simple composition, wordless image"),
    "silhouette": ("Silhouette art",
                   "flat silhouette art illustration, every shape a solid pure-black silhouette with crisp clean edges "
                   "against a smooth glowing gradient sky, minimal, calm, figures as featureless black shapes, wordless image"),
    "watercolor": ("Soft watercolour",
                   "soft watercolor illustration, pastel colours, gentle paper texture, airy and calm, wordless image"),
    "cinematic": ("Cinematic still",
                  "cinematic film still, 35mm photograph, soft natural light, shallow depth of field, calm mood, wordless image"),
    "bhay": ("भय horror (dark, silhouettes)",
             "dark atmospheric Indian horror digital painting, cinematic, deep shadows, cold moonlight blue and dim "
             "sickly green tones, thin fog, a single weak warm light source, dread and wrongness, unnatural stillness, "
             "long distorted shadows, pale lifeless skin and hollow dark eyes on any ghostly figure, varied camera "
             "angles and shot sizes, unsettling and frightening, wordless image"),
    "rahasya": ("रहस्य thriller (noir)",
                "cinematic Indian thriller film still, moody noir lighting, deep shadows and one hard light source, "
                "muted teal and amber, mystery and tension, scene-specific framing and varied camera angles, "
                "wordless image"),
    "kisse": ("किस्से emotional (warm)",
              "warm cinematic Indian slice-of-life painting, soft golden evening light, nostalgic film colours, "
              "gentle and emotional, people seen from behind or far with faces turned away or in soft shadow, "
              "wordless image"),
    "romanch": ("रोमांच romance (warm, tasteful)",
                "romantic cinematic painting, warm candle and golden-hour light, soft bokeh, city lights or lamp glow, two "
                "fully clothed silhouettes in elegant clothes close together seen from behind, faces in soft shadow, "
                "tasteful and elegant, wordless image"),
    "kareeb": ("करीब ASMR (soft, close)",
               "soft dreamy close-up still life, cosy night, warm lamp, gentle textures (fabric, tea cup, a dark window, "
               "glass, pages), shallow depth of field, very calm, an empty quiet room, objects only, wordless image"),
    "gyaan": ("ज्ञान knowledge (clean illustration)",
              "clean modern editorial illustration, simple shapes, calm deep blue and warm accent colours, clear "
              "subject in the centre, informative, wordless pure imagery"),
    "none": ("Only my words", ""),
}
# every series' machine settings (the भय pipeline, tuned per series)
GENRES = {
    "bhay": {"label": "भय", "style": "bhay", "music": ["Dark", "Tension", "Eerie"], "mood": "horror",
             "voice": "burarikand", "desc": "Hindi horror story (slow-burn, eerie)", "hits": True,
             "ex": 0.5, "pause": 0.75, "temp": 0.75, "drama": ("horror", 1.7)},
    "rahasya": {"label": "रहस्य", "style": "rahasya", "music": ["Tension", "Dark"], "mood": "thriller",
                "voice": "walkingcorpse", "desc": "Hindi thriller / mystery story", "hits": True,
                "ex": 0.5, "pause": 0.6, "temp": 0.75, "drama": ("horror", 1.5)},
    "kisse": {"label": "किस्से", "style": "kisse", "music": ["Sad", "Love", "Calm"], "mood": "warm",
              "voice": "aarav", "desc": "Hindi emotional story", "hits": False,
              "ex": 0.55, "pause": 0.65, "temp": 0.75, "drama": ("story", 1.3)},
    "romanch": {"label": "रोमांच", "style": "romanch", "music": ["Love", "Calm"], "mood": "romance",
                "voice": "pehlapyar", "desc": "Hindi romance story (tasteful)", "hits": False,
                "ex": 0.5, "pause": 0.75, "temp": 0.75, "drama": ("soft", 1.2)},
    "kareeb": {"label": "करीब", "style": "kareeb", "music": ["Calm"], "mood": "soft",
               "voice": "hypnosis", "desc": "Hindi ASMR (soft, whispered)", "hits": False,
               "ex": 0.3, "pause": 1.0, "temp": 0.7, "drama": ("soft", 1.5)},
    "gyaan": {"label": "ज्ञान", "style": "gyaan", "music": ["Calm", "Happy"], "mood": "clean",
              "voice": "psychology", "desc": "Hindi knowledge video", "hits": False,
              "ex": 0.5, "pause": 0.55, "temp": 0.75, "drama": ("explain", 0.9)},
}
SIZES = {"16:9": (1664, 928, "16:9 YouTube"), "9:16": (928, 1664, "9:16 Reel / Short"),
         "1:1": (1216, 1216, "1:1 Square"), "4:5": (1088, 1360, "4:5 Post")}
VOICES = {
    "v3": ("Your Sukoon voice (V3 - calm, flow)", []),
    "v3_expressive": ("Your voice, a bit more expressive", ["exaggeration=0.5"]),
    "v0": ("Older Sukoon voice (V0 style, each phrase alone)",
           ["model=\"run_20261013_0110_sukoon_only_lr3e-05\"", "flow=false", "exaggeration=0.5"]),
}


TYPOS = {"mukhtalif": "Mukhtalif (default)", "verse": "Verse", "diary": "Diary", "shayari": "Shayari", "classic": "Classic"}
VIZ = {"line": "Light line", "bars": "Bars", "none": "None"}
PAUSES = ("pause_comma", "pause_line", "pause_sentence", "pause_dots", "pause_marker", "pause_paragraph")


def fonts_dir(cfg):
    return os.path.join(cfg["studio_dir"], "assets", "fonts")


def sv_config(cfg):
    try:
        voice_cfg = json.load(open(os.path.join(cfg["sukoonvoice_dir"], "config.json"), encoding="utf-8-sig"))
        voice_cfg.setdefault("speak", {})["music"] = (cfg.get("paths") or {}).get("voice_music_default", "")
        return voice_cfg
    except Exception:
        return {}




def slug(s, n=60):
    s = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", " ", str(s)).strip()
    s = re.sub(r"\s+", " ", s)[:n].strip(" .")
    return s or time.strftime("untitled_%H%M%S")








TRANSLATE_ASK = (
    "Translate this English sleep-story script into natural, warm, spoken Hindi (Devanagari) in the calm Sukoon "
    "narration style: simple everyday Hindi/Urdu words a person would say softly at bedtime, not formal or "
    "bookish Hindi; common English words that people say in Hindi (like blanket, phone, relax) may stay. "
    "KEEP THE FORMAT EXACTLY: one phrase per line as in the original, keep every '(...)' line, keep '...' "
    "where the original has it, keep blank lines. Answer with the Hindi script only, nothing else.\n\n")








# ------------------------------------------------------------------ your thumbnails = the style guide
# Experimental: a fixed picture look per series, used instead of the look the director writes for each story.
# Bhay = the look of "Bhay-Incomplete-Tea" (16 Oct, Varun's story): painted, not photo-real, so faces stay
# coherent. keep_era keeps the director's first words when they name a time or place ("1990s Indian rural aesthetic").
SERIES_LOOK = {
    "bhay": {"on": True, "keep_era": True,
             "look": "desaturated moody palette (deep blues, greys), dim tungsten lighting with heavy shadows, "
                     "hyper-realistic oil painting style"},
    # Kisse = gouache (18 Oct style test, _tmp/kisse_style_20261018): clearly painted, warm, faces hold across shots.
    # lead = the medium, put FIRST in every shot prompt (Z-Image ignored the medium at the end)
    "kisse": {"on": True, "keep_era": True, "lead": "Warm gouache storybook illustration",
              "look": "flat matte gouache paint, simplified soft faces, visible brush texture, warm ochre, "
                      "terracotta and muted teal palette"},
}
THEMES = {"sukoon_theme": "Sukoon thumbnails", "kisse_theme": "Kisse thumbnails"}
VISION_ASK = (
    "You are a visual style analyst. Look at this YouTube thumbnail and answer ONLY as JSON with keys: "
    '"palette" (main colours), "lighting", "mood", "setting", "composition", "art_style" (e.g. cinematic photo, '
    'digital painting), "subject" (who/what is the main subject), "style_prompt" (ONE English sentence of 25-40 '
    "words that describes ONLY the look - colours, light, atmosphere, rendering, camera feel - NOT the subject).")









































MIN_TAIL = 1.5                                                # minutes of music after the voice, at least














































# how each series wants its lines to land (the director's note for the pause / feeling pass)
DIRECTION = {
    "bhay": "Slow-burn horror: hold the breath before the scary noun or the reveal ('और वहाँ... कोई खड़ा था'), "
            "after turns (लेकिन, तभी, अचानक, उसके बाद); let silence do the work; short last lines land alone.",
    "rahasya": "Thriller: tight and suspicious; pause before a clue, a name, a number or a lie ('और कातिल था... उसका "
               "भाई'); a quick breath before a twist; keep the pace moving otherwise.",
    "kisse": "Emotional story: pause where the heart turns - before what was lost or never said ('और माँ... फिर कभी "
             "नहीं लौटी'), before memories and realisations; gentle, never theatrical.",
    "romanch": "Romance: tender and slow; pause before a touch, a glance, a confession ('उसने कहा... मुझे तुमसे'), "
               "linger on closeness; soft breaths, no drama.",
    "kareeb": "ASMR: very soft and close; small pauses that let the listener breathe; nothing sudden, few marks.",
    "gyaan": "Knowledge: clear and confident; one short pause before the key fact, the surprising number or the "
             "answer to a question ('और जवाब है... नींद'); no pauses inside definitions.",
    "": "Pause where the meaning needs the listener to feel the moment.",
}
















































SCENES_ASK = (
    "You are the director of a {desc} video (channel '{label}'). The story is cut into numbered parts "
    "below (with their times). For EACH part design ONE picture and its sound.\n"
    "First decide ONE visual look for the whole story: era, colour palette, light quality and painting style ONLY, "
    "at most 30 words - NO people, NO characters, NO clothing, NO props or objects and NO single room in the look "
    "(those belong in each scene's own prompt, otherwise every picture paints them). If a character recurs, define a compact "
    "visual anchor for them: a dark, readable silhouette with a stable build, hair outline, clothing shape and one "
    "distinctive prop. Keep those details consistent in every scene where that character appears, while varying the "
    "camera angle; do not show detailed facial features. Do not invent a boy or child when the story does not "
    "specify one.\n"
    "Every scene must show a different concrete moment, object, or action from its own numbered story part. Do not "
    "reuse the same subject, action, composition, or camera setup in consecutive scenes. The per-scene prompt should "
    "describe that moment; the shared look is added separately. Each prompt STARTS with its own main subject (a "
    "person, an object, a place or an action from that part) and names its own location and shot size; no two "
    "prompts in the story share the same main subject, and most scenes should NOT show the recurring character - "
    "show the places, objects, hands, doors, streets and rooms that this part itself names; switch between inside "
    "and outside, near and far, where the story allows. Never add weather, light sources or objects that the part "
    "does not name.\n"
    "For each part give: \"happens\" (write it FIRST: ONE short English sentence, at most 20 words, saying what "
    "happens in this part, with only the people, objects, places and weather that this part's own text names), "
    "\"prompt\" (35-60 English words built from \"happens\": the place, the light, what is visible, the mood; people "
    "only when relevant to that moment; preserve the recurring character's silhouette anchor and clothing, but "
    "deliberately vary wide establishing views, object details, point-of-view, profile, front, and over-shoulder shots "
    "as the story warrants; do not repeat a centered rear-view person in consecutive scenes. A recurring character "
    "stays a silhouette from every angle; a scene may focus on the environment or a story object with no character "
    "present. {rules}), \"ambience\" (ONE name from AMBIENCE or "
    "\"\" - it must fit the PLACE of that scene: indoors no river, crickets, birds or trains unless the story says so), \"hit\" (ONE name from HITS for a scary turn at the start of this part, or \"\" - use hits rarely, at "
    "most every third part), \"lightning\" (true only if the story has a storm there), \"cues\" (sound effects at "
    "the exact moment the story describes a sound or action: a list of {{\"words\": 2-4 words copied exactly from "
    "that part where it happens - in the story's own language and "
    "script (Devanagari for a Hindi story), NEVER translated to English, \"sfx\": ONE name from CUES that is that "
    "exact sound (the thing that makes the sound must be in the story: no car door for a face)}}; at most {cpp} per part; read the MEANING, not single "
    "words - 'खिड़की पर' is 'on the window' (no knock), 'पर' after a verb is 'but'; only sounds that really happen "
    "in the story; [] if none), \"moment\" (ONLY where an object or small detail that this part itself names carries strong "
    "emotion: {{\"words\": 2-4 words copied exactly "
    "from that part where it is mentioned (in the story's own script, never translated), \"prompt\": 25-40 English words, a close-up of that object or detail in "
    "this story's look and light, symbolic, the object alone}}; most parts have none - use it for at most {mom} "
    "parts in the whole story; {{}} if none).\n"
    "AMBIENCE: {amb}\nHITS: {hits}\nCUES: {cues}\n"
    'Return "character_anchors" as a JSON object mapping each recurring character ID to their stable silhouette, '
    'clothing shape, and distinctive prop. For each scene set "character" to that stable ID when visible, else "". '
    'Answer ONLY JSON: {{"look": "...", "character_anchors": {{}}, "scenes": [{{"happens": "...", "prompt": "...", '
    '"character": "", "ambience": "", "hit": "", "lightning": false, '
    '"cues": [{{"words": "...", "sfx": "..."}}], "moment": {{}}}}, ...]}} '
    "with exactly {n} scenes.\n\nSTORY PARTS:\n{parts}")
CUE_WORDS_ASK = (
    "Below is one part of a story that is read aloud. A sound effect ({sfx}) should play at the moment the story "
    "describes it (the planner's hint, maybe translated: \"{words}\"). Copy 2-4 consecutive words EXACTLY as they are "
    "written in the text below (same language, same script, same spelling) at the place where that sound happens. "
    "If that sound does not happen in this text, answer an empty string. Answer ONLY JSON: {{\"words\": \"...\"}}\n\n"
    "TEXT:\n{text}")
SCENE_VARY_ASK = (
    "You write one picture prompt for a scene of a {desc} video. The prompt below is too similar to an earlier "
    "scene of the same video, so the pictures would look the same.\n"
    "EARLIER PROMPT: {earlier}\nTOO-SIMILAR PROMPT: {prompt}\n"
    "STORY PART FOR THIS SCENE:\n{text}\n\n"
    "Write a NEW prompt (35-60 English words) for this story part with a DIFFERENT main subject (another object, "
    "place, action or detail this part mentions), a different location or viewpoint and a different shot size "
    "(wide establishing view, extreme close-up of an object, view through a doorway, high angle, low angle...). "
    "Start with the main subject. No recurring character unless this part is about them. {rules} "
    "Answer ONLY JSON: {{\"prompt\": \"...\"}}")


























# ------------------------------------------------------------------ story-beat scenes + two-step director (Experimental, 16 Oct)
END_SECONDS = 10              # Experimental genre videos, 'Whole video' 0: music after the voice (Tinker: end_seconds)
SUBSCRIBE_END = {"dur": 6.5, "sub_click": 2.2, "bell_click": 3.8}   # Subscribe + bell ending (engine subscribe_end.py)
SCENE_SECONDS = 30            # Experimental genre videos: about one picture per this many seconds (Tinker: scene_seconds)
VISUAL_ASK = (                # Experimental genre scenes: one fixed look per character and key object (steps/scenes.py)
    "You design the recurring visuals for a {desc} story told in pictures. Every picture is painted separately by an "
    "image model that remembers nothing, so each recurring character and key object needs ONE fixed description that "
    "is pasted word for word into every picture it appears in.\n"
    "The planner's rough notes on the characters: {anchors}\nThe story's look: {look}\n"
    "Answer ONLY JSON: {{\"characters\": {{\"<ID exactly as in the notes>\": {{\"look\": \"25-40 English words\", "
    "\"outfit\": \"6-12 English words: only the clothes with their colours\"}}}}, "
    "\"things\": {{\"<one English noun for it, e.g. doll>\": \"15-30 English words\"}}}}.\n"
    "A character: age in years, build, skin tone, face shape, hair (colour, length, style), facial hair, one "
    "distinctive face feature, clothing with exact colours and fabric, one accessory. Only what can be seen: no name, "
    "no feelings, no actions, no setting. A character shown at two ages keeps the two IDs from the notes. "
    "Things: only objects, vehicles or buildings the story returns to at least twice that must look the same each "
    "time (a doll, a house, a car); at most 4; colour, material, size, condition. {{}} if none.\n\nSTORY:\n{story}")
SCENE_PICTURE_ASK = (
    "You write the picture prompts for a {desc} video. For EACH numbered scene below you get WHAT HAPPENS in it "
    "and the story TEXT it covers (the text is read aloud while the picture is on screen).\n"
    "Write one English image prompt (35-60 words) per scene that shows exactly that moment: start with the main "
    "subject (a person, object, place or action from WHAT HAPPENS), then the location, the shot size and the light. "
    "Use ONLY people, objects, places, weather and light sources that the scene's own TEXT or WHAT HAPPENS names; "
    "never add anything else. A recurring character stays a dark silhouette, face hidden in shadow. Neighbouring "
    "scenes must not share the same main subject or camera setup. {rules}\n"
    'Answer ONLY JSON: {{"prompts": ["...", "..."]}} with exactly {n} prompts.\n\nSCENES:\n{scenes}')
SCENE_FIX_ASK = (
    "This picture prompt for a scene of a {desc} video adds things the story never mentions in this scene: {bad}.\n"
    "PROMPT: {prompt}\nWHAT HAPPENS: {happens}\nSTORY TEXT OF THE SCENE:\n{text}\n\n"
    "Rewrite the prompt (35-60 English words) for the same moment WITHOUT those things, using only what the story "
    "text names. Describe what the camera sees, not the story: start with the shot size and the main subject, "
    "then the place and the light. Answer ONLY JSON: {{\"prompt\": \"...\"}}")


































































































ART_ASK = (
    "You design the ONE artwork for a whole episode of 'Sukoon', a calm night-time sleep-story channel. It is shown "
    "behind the whole video and as the thumbnail, so it must say at a glance what THIS episode is about.\n"
    "Read the episode below{extra}. Then write ONE English image prompt (40-70 words) for a peaceful night scene that "
    "shows the episode's central image and setting: who is there (if anyone: seen from behind or far, no close-up "
    "faces), the place, the light (it is night: moonlight, a warm lamp, a diya...), the objects that carry the story, "
    "the mood. Concrete and visual, no abstract words, no text or letters in the picture. Also give a short "
    "'check' list: 3-5 things that MUST be visible for the picture to fit this episode.\n"
    'Answer ONLY as JSON: {{"prompt": "...", "check": ["...", "..."]}}\n\nEPISODE:\n{text}')






PICK_ASK = (
    "This picture should be the artwork of a calm night-time sleep-story episode. It should show: {want}.\n"
    "Score it 0-10: how well it shows these things, AND how clean it is (no text or letters, no distorted hands or "
    "faces, no strange objects, calm night light). Answer ONLY JSON: {{\"score\": 7, \"missing\": \"...\", "
    "\"flaws\": \"...\"}}")











SCENE_ASK = (
    "This picture is one scene of a {desc} video. It should show: {want}\nScore it 0-10: how well it shows the scene "
    "AND how clean it is - no text or letters, no distorted or extra hands / limbs / bodies, no melted or impossible "
    "objects. Judge whether the framing matches this beat and whether scenes are visually varied; do not prefer rear "
    "views or silhouettes unless the prompt calls for them. When a recurring character appears, check the story's "
    "silhouette/clothing anchor: {character_anchor}. {anchor_note} Fits this look: {look}. If score is below 7, give "
    "one short, concrete prompt "
    "revision that fixes the visible problem while preserving the character anchor and all correct details. If no fix is "
    "needed, revision must be empty. Answer ONLY JSON: "
    '{{"score": 7, "flaws": "...", "revision": "..."}}')




























# ---- moved out of tools.py (TOOLS_SPLIT_PLAN.md). tools stays the front door: keep this block at the END.
from steps.hub import (gpu_info, _gpu_cache, status, _dismissed_path, dismiss, suggestions, _mtime, library, episodes, library_stamp, remove_item, open_path, system_action, _settings_path, apply_settings, llm_models, set_llm, set_prompt_model)  # noqa: E402,F401  (4a)
from steps.thumbs import (index_path, load_index, series_of, SUKOON_WORDS, thumb_sources, look_text, style_index)  # noqa: E402,F401  (4b)
from steps.voice_series import (SERIES_DEFAULT, _series_file, series_load, _norm, episode_series_of, genre_voice_sets, series_model, voice_series, voice_series_save, _voice_cfg_path, voice_scan, voice_add_folders, sv_dataset, classify_recordings, train_all_series, sv_train)  # noqa: E402,F401  (4c)
from steps.mix import (audio_index_path, load_audio_index, audio_index, auto_track, harshness, tamed, _decode, shape_mix)  # noqa: E402,F401  (4d)
from steps.sukoon_video import (_first_image, _audio_of, studio_prepare, studio_video, _wav_len, font_family, _ass_color, _ass_time, make_ass, slideshow)  # noqa: E402,F401  (4e)
from steps.pictures import (_parts, llm_prompts, default_style, _script_sample, art_prompt, pick_art, HOT_DRINK, no_steam, comfy_images, _judge, pick_scenes, repaint)  # noqa: E402,F401  (4f)
from steps.voice import (rack_path, word_rack, hard_words, rack_save, avoid_hard_words, practice_story, free_gpu, free_llm, sv_python, _story_voice_style, sv_speak, en_dir, en_python, en_speak, en_setup, en_test)  # noqa: E402,F401  (4g)
from steps.script import (translate, llm_translate, _clean_script, llm_story, fit_script, _queue_fetch, elements_check, elements_fetch, write_stories, write_script, EMOTIONS, EMO_BATCH, _emo_salvage, _emo_reply, mark_emotions)  # noqa: E402,F401  (4h)
from steps.fixes import (_secs, repair_check, repair_keep_old, repair_save_voice, repair_patch, repair_series_subtitles, repair_write_subtitle_text, series_of_video, _series_note, remix_like_before, narration_lines, repair_rerender, _duration, _PREV, reel_motions, studio_reels)  # noqa: E402,F401  (4i)
from steps.scenes import (_sfx_names, _sfx_names_old, AMB_WORDS, PICTURE_RULES, _pic_rules, _anchor_text, SCENE_SIMILAR_MAX, SCENE_VARY_HINTS, _PEOPLE, _look_for_prompt, _overlap, _vary_scene, _words_in, AMB_SOURCES, _amb_fits, _fix_cue_words, sfx_fetch, _word_time, _SENT_END, INVENTED, WEATHER, _wre, _beat_parts, _invented, _strip_words, _model_cfg, _ask_step, story_ask, _loads, _clean_scene, _picture_prompts, _set_aside_scenes, horror_scenes)  # noqa: E402,F401  (4j)
from steps.genre import (horror_story, write_horror_stories, _horror_track, _usable_music, horror_sound, _subtitle_text_for_timing, _is_exp, _end_seconds, _subscribe_sounds, _subscribe_end, horror_video, genre_reels)  # noqa: E402,F401  (4k)
from steps.planner import (presets, use_shape, voice_sets, studio_opts, save_upload, unique_genre_name, _rename_job_values, plan)  # noqa: E402,F401  (4l)
