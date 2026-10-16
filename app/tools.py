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


def presets(cfg):
    music = {}
    md = cfg.get("music_dir") or ""
    for f in sorted(glob.glob(os.path.join(md, "*", "*.mp3")) + glob.glob(os.path.join(md, "*", "*.wav"))):
        music.setdefault(os.path.basename(os.path.dirname(f)), []).append(f)
    fonts = sorted(f for f in glob.glob(os.path.join(fonts_dir(cfg), "*")) if f.lower().endswith((".ttf", ".otf")))
    sp = sv_config(cfg).get("speak", {})
    idx = load_index(cfg)
    styles = {k: v for k, v in THEMES.items() if idx.get("themes", {}).get(k.split("_")[0])}
    styles.update({k: v[0] for k, v in STYLES.items()})
    thumbs = [{"file": x["file"], "thumb": x.get("thumb") or x["file"], "series": x["series"], "name": x["name"],
               "look": x.get("style_prompt", ""), "subject": x.get("subject", "")} for x in idx.get("items", [])]
    ai = load_audio_index(cfg)
    audio = {"built": ai.get("built"), "tracks": len(ai.get("tracks", [])), "episodes": len(ai.get("episodes", [])),
             "shape": ai.get("shape"), "auto": os.path.basename(auto_track(cfg)) if ai else ""}
    import story
    return {"categories": {k: v[0] for k, v in story.CATEGORIES.items()},
            "styles": styles, "sizes": {k: v[2] for k, v in SIZES.items()}, "audio": audio,
            "index": {"count": len(thumbs), "built": idx.get("built"), "thumbs": thumbs,
                      "found": len(thumb_sources(cfg))},
            "voices": {k: v[0] for k, v in VOICES.items()}, "engines": list(ENGINES),
            "music": music, "music_default": sp.get("music", ""), "music_under_db": sp.get("music_under_db", 14),
            "fonts": fonts, "typos": TYPOS, "viz": VIZ, "llm": cfg.get("llm"),
            "vision_llm": cfg.get("vision_llm"), "prompt_model": cfg.get("prompt_model"),
            "locations": {"hub": cfg.get("media_dir", ""), "scripts": cfg.get("scripts_dir", ""),
                          "narrations": cfg.get("narrations_dir", ""), "episodes": cfg.get("episodes_dir", ""),
                          "voice_data": cfg.get("sukoonvoice_media", ""), "voice_models": voice_model_root(cfg),
                          "voice_model_copy": cfg.get("voice_model_backup_dir", ""),
                          "video_engine": cfg.get("studio_dir", ""),
                          "experimental_root": cfg.get("experimental_root", "")},
            "other_windows": cfg.get("other_windows") or {}}


def slug(s, n=60):
    s = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", " ", str(s)).strip()
    s = re.sub(r"\s+", " ", s)[:n].strip(" .")
    return s or time.strftime("untitled_%H%M%S")


def use_shape(b, cfg):
    """shaped music = on unless switched off / no music / a preview; needs the audio index (else the old way)"""
    return (b.get("music_shape", True) and not b.get("preview") and b.get("music") is not False
            and b.get("music_track") != "none" and bool(load_audio_index(cfg).get("shape")))


def voice_sets(b, cfg):
    """form -> HubGPT Voice --set values (this run only; its config.json is never changed)"""
    sets = list(VOICES.get(b.get("voice", "v3"), VOICES["v3"])[1])
    if b.get("voice", "v3") == "v3" and series_model(cfg, "sukoon"):     # your trained सुकून voice, once there is one
        sets.append("model=" + json.dumps(series_model(cfg, "sukoon")))
    if b.get("takes"):
        sets.append(f"takes={int(b['takes'])}")
    if b.get("exaggeration") not in (None, ""):
        sets.append(f"exaggeration={float(b['exaggeration'])}")
    if b.get("preview"):
        sets += ["with_music=false", "takes=1"]
    elif b.get("music") is False or b.get("music_track") == "none":
        sets.append("with_music=false")
    elif b.get("music_track"):
        sets += ["with_music=true", "music=" + json.dumps(b["music_track"])]
    if b.get("music_db") not in (None, ""):
        sets.append(f"music_under_db={float(b['music_db'])}")
    if b.get("dramatic"):
        sets.append("dramatic=true")
    if b.get("emotions") and not b.get("preview"):
        sets.append("emotions=true")
    k = float(b.get("pause_scale") or 1.0)
    if abs(k - 1.0) > 0.01:
        sp = sv_config(cfg).get("speak", {})
        base = {"pause_comma": .35, "pause_line": 1.0, "pause_sentence": 1.1, "pause_dots": 1.2,
                "pause_marker": 1.6, "pause_paragraph": 2.2}
        for p in PAUSES:
            sets.append(f"{p}={round(float(sp.get(p, base[p])) * k, 3)}")
    return sets


def studio_opts(b):
    """form -> HubGPT Video sleep_pipeline options (only what the form changed)"""
    o = []
    if b.get("typo") and b["typo"] in TYPOS:
        o += ["--typo", b["typo"]]
    if b.get("font"):
        o += ["--font", b["font"]]
    if b.get("cap_lang") in ("hi", "en"):
        o += ["--lang", b["cap_lang"]]
    if b.get("viz") in VIZ:
        o += ["--viz", b["viz"]]
    if b.get("brand") is False:
        o.append("--no-brand")
    if b.get("title_card") is False:
        o.append("--no-title")
    if b.get("dim") is False:
        o.append("--no-dim")
    if b.get("label"):
        o += ["--label", str(b["label"])]
    if b.get("max_lines"):
        o += ["--max-lines", str(int(b["max_lines"]))]
    return o


TRANSLATE_ASK = (
    "Translate this English sleep-story script into natural, warm, spoken Hindi (Devanagari) in the calm Sukoon "
    "narration style: simple everyday Hindi/Urdu words a person would say softly at bedtime, not formal or "
    "bookish Hindi; common English words that people say in Hindi (like blanket, phone, relax) may stay. "
    "KEEP THE FORMAT EXACTLY: one phrase per line as in the original, keep every '(...)' line, keep '...' "
    "where the original has it, keep blank lines. Answer with the Hindi script only, nothing else.\n\n")






def save_upload(cfg, name, data):
    import base64
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".txt"):
        raise ValueError("only pictures (png/jpg/webp) or .txt")
    raw = base64.b64decode(data.split(",", 1)[-1])
    d = os.path.join(cfg["images_dir"], "uploads")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, time.strftime("%Y%m%d_%H%M%S_") + slug(os.path.splitext(name)[0], 40) + ext)
    open(p, "wb").write(raw)
    return p


# ------------------------------------------------------------------ your thumbnails = the style guide
THEMES = {"sukoon_theme": "Sukoon thumbnails", "kisse_theme": "Kisse thumbnails"}
VISION_ASK = (
    "You are a visual style analyst. Look at this YouTube thumbnail and answer ONLY as JSON with keys: "
    '"palette" (main colours), "lighting", "mood", "setting", "composition", "art_style" (e.g. cinematic photo, '
    'digital painting), "subject" (who/what is the main subject), "style_prompt" (ONE English sentence of 25-40 '
    "words that describes ONLY the look - colours, light, atmosphere, rendering, camera feel - NOT the subject).")






























# ------------------------------------------------------------------ unique episode names and live job path updates
def unique_genre_name(cfg, media, genre, requested):
    """Reserve a non-colliding name across the episode's script, voice, pictures and episode folders."""
    base = slug(requested)
    prefix = {"bhay": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse", "romanch": "Romanch",
              "kareeb": "Kareeb", "gyaan": "Gyaan"}.get(genre, str(genre).title())
    if not re.match(re.escape(prefix) + r"\s*-", base, re.I):     # "Bhay - x" and Experimental's short "Bhay-x"
        base = f"{prefix} - {base}"
    scripts = os.path.join(media, (cfg.get("layout") or {}).get("scripts", "scripts"))
    pictures = os.path.join(media, (cfg.get("layout") or {}).get("pictures", "pictures"))
    episodes = cfg.get("episodes_dir", "")
    candidate, n = base, 2
    while (os.path.exists(os.path.join(scripts, slug(candidate) + ".txt"))
           or os.path.exists(narration_dir(cfg, candidate))
           or os.path.exists(os.path.join(pictures, candidate + "_scenes"))
           or os.path.exists(os.path.join(episodes, candidate))):
        candidate = f"{base} ({n})"
        n += 1
    return candidate


def _rename_job_values(value, old, new):
    """Update queued step paths, visible title and outputs after an AI title is known."""
    if isinstance(value, dict):
        for key in list(value):
            if key == "auto_name":
                value.pop(key, None)
            else:
                value[key] = _rename_job_values(value[key], old, new)
        return value
    if isinstance(value, list):
        return [_rename_job_values(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


# ------------------------------------------------------------------ job plans (form -> steps)
def plan(kind, b, cfg):
    media = cfg["media_dir"]
    name = slug(b.get("name") or b.get("title") or "")
    if kind == "experimental":
        # Reuse the existing genre pipeline under a unique EXP_ namespace.
        if not re.fullmatch(r"EXP_[A-Za-z0-9_-]{8,60}", name):
            raise ValueError("experimental runs need a unique EXP_ name")
        plan = globals()["plan"]("genre", dict(b, name=name), cfg)
        plan["title"] = "EXPERIMENTAL · " + plan["title"]
        plan["outputs"] = [os.path.join(media, "experimental_runs", name)] + plan.get("outputs", [])
        return plan
    if kind == "voice":
        text = (b.get("script") or "").strip()
        if not text and not b.get("redo"):
            raise ValueError("paste a script first")
        if b.get("preview"):                                  # one line, quick, own throw-away folder
            text = text.splitlines()[0] if text else ""
            name = "_preview_" + time.strftime("%H%M%S")
        sets = voice_sets(b, cfg)
        redo_only = bool(b.get("redo")) and not text
        if redo_only:                                         # remaking phrases: the narration's OWN saved voice settings
            sets = [f"takes={int(b['takes'])}"] if b.get("takes") else []
            _n = narration_dir(cfg, name, existing=True)
            if os.path.exists(os.path.join(_n, "music_shape.json")) or os.path.exists(os.path.join(_n, "hub_series.json")):
                sets.append("with_music=false")
        steps = []
        if text:
            steps.append({"fn": "write_script", "label": "save the script", "args": {"name": name, "text": text}})
            if b.get("lang") == "en":
                steps.append({"fn": "llm_translate", "label": "the AI translates English -> Hindi (Sukoon style)",
                              "args": {"name": name}})
        shaped = use_shape(b, cfg) and not redo_only
        if shaped:
            sets.append("with_music=false")
        if not text and b.get("redo"):                       # remake lines: the narration says whose voice it is
            import story as _st
            if _st.narration_lang(narration_dir(cfg, name, existing=True)) == "en":
                b = dict(b, language="en")
        if text and b.get("language") != "en" and not b.get("preview") and hard_words(cfg):
            steps.append({"fn": "avoid_hard_words", "label": "lines with words your voice stumbles on: said another way",
                          "args": {"name": name}})
        if text and b.get("language") != "en" and not b.get("preview") and (b.get("emotions") or b.get("dramatic")):
            steps.append({"fn": "mark_emotions", "label": "the AI marks the feeling of each line ([डर] [उदास] …)",
                          "args": {"name": name, "emotions": bool(b.get("emotions")),
                                   "pauses": bool(b.get("dramatic"))}})
        if b.get("language") == "en":
            steps += [{"fn": "free_gpu", "label": "free the graphics card"},
                      {"fn": "en_speak", "label": "the English narrator reads the script (Qwen3-TTS)",
                       "args": {"name": name, "redo": str(b.get("redo") or ""), "speaker": b.get("en_speaker") or ""}}]
        else:
            steps += [{"fn": "free_gpu", "label": "free the graphics card"},
                      {"fn": "sv_speak", "label": "your voice reads the script (HubGPT Voice)",
                       "args": {"name": name, "sets": sets, "redo": str(b.get("redo") or "")}}]
        if redo_only:
            steps.append({"fn": "remix_like_before", "label": "the same music / sound as before under the new voice",
                          "args": {"name": name}})
            shaped = False
        if shaped:
            steps.append({"fn": "shape_mix", "label": "music the way your Sukoon episodes do it",
                          "args": {"name": name, "track": b.get("music_track") or "", "tail": b.get("tail", "1min")}})
        return {"title": ("Preview: " + text[:40]) if b.get("preview") else f"Voice: {name}", "steps": steps,
                "outputs": [narration_dir(cfg, name)]}
    if kind == "audio_index":
        return {"title": "Index your music + Sukoon music shape", "steps": [
            {"fn": "audio_index", "label": "measure every track, read your Sukoon projects (read-only)",
             "args": {"rebuild": bool(b.get("rebuild"))}}], "outputs": [audio_index_path(cfg)]}
    if kind == "style_index":
        return {"title": "Index your thumbnails (style guide)", "steps": [
            {"fn": "free_gpu", "label": "free the graphics card"},
            {"fn": "style_index", "label": "the vision AI looks at every thumbnail once",
             "args": {"rebuild": bool(b.get("rebuild"))}}], "outputs": [index_path(cfg)]}
    if kind == "repaint":                                     # one picture of a set again
        d, f = b.get("set") or "", b.get("file") or ""
        if not d or not f:
            raise ValueError("choose a picture")
        return {"title": f"Repaint: {os.path.basename(d)} / {f}", "steps": [
            {"fn": "repaint", "label": "ComfyUI paints this picture again",
             "args": {"dir": d, "file": f, "prompt": b.get("prompt") or "", "engine": b.get("engine") or "",
                      "steps": b.get("steps"), "negative": b.get("negative", "")}}], "outputs": [d]}
    if kind == "pictures":                                   # pictures for what is said
        src = b.get("narration") or ""
        if not src and not (b.get("script") or "").strip():
            raise ValueError("choose a narration or paste a script")
        out = os.path.join(media, cfg.get("layout", {}).get("pictures", "pictures"), name)
        return {"title": f"Pictures: {name}", "steps": [
            {"fn": "llm_prompts", "label": "the AI writes one picture idea per part of the story",
             "args": {"narration": src, "script": b.get("script", ""), "n": int(b.get("count") or 6),
                      "out": out, "idea": b.get("idea", "")}},
            {"fn": "comfy_images", "label": "ComfyUI paints the pictures",
             "args": {"out": out, "style": b.get("style", "sukoon_night"), "size": b.get("size", "16:9"),
                      "engine": b.get("engine") or cfg.get("image_engine"), "seed": b.get("seed"),
                      "steps": b.get("steps"), "negative": b.get("negative", "")}},
            {"fn": "free_gpu", "label": "free the graphics card"}], "outputs": [out]}
    if kind == "image":                                      # image studio
        if b.get("batch"):
            prompts = [l.strip() for l in (b.get("prompt") or "").splitlines() if l.strip()][:40]
        else:
            p1 = (b.get("prompt") or "").strip()
            prompts = [p1] * max(1, min(8, int(b.get("count") or 1))) if p1 else []
        if b.get("variations_of"):                            # same prompt + settings as that picture, new seeds
            src = b["variations_of"]
            pt = src[:-4] + ".prompt.txt"
            base_prompt = open(pt, encoding="utf-8").read().split("\n\n")[0].strip() if os.path.exists(pt) else ""
            if not base_prompt:
                raise ValueError("that picture has no saved prompt")
            prompts = [base_prompt] * max(1, min(8, int(b.get("count") or 4)))
            b = dict(b, style="none")                         # its saved prompt already contains the style words
        if not prompts:
            raise ValueError("write what you want to see")
        out = os.path.join(media, cfg.get("layout", {}).get("images", "images"), time.strftime("%Y%m%d"))
        return {"title": f"Image: {prompts[0][:50]}" + (f" (+{len(prompts) - 1})" if len(prompts) > 1 else ""),
                "steps": [{"fn": "comfy_images", "label": "ComfyUI paints",
                           "args": {"out": out, "prompts": prompts, "style": b.get("style", "sukoon_night"),
                                    "size": b.get("size", "16:9"), "engine": b.get("engine") or cfg.get("image_engine"),
                                    "seed": b.get("seed"), "prefix": slug(prompts[0].split(".")[0], 30), "steps": b.get("steps"),
                                    "negative": b.get("negative", ""), "ref": b.get("ref", ""),
                                    "denoise": b.get("denoise"), "silhouette": bool(b.get("silhouette"))}}],
                "outputs": [out]}
    if kind == "slideshow":                                  # pictures + voice -> video
        pics, audio = b.get("pictures") or "", b.get("audio") or ""
        if not pics or not audio:
            raise ValueError("choose a picture set and a narration")
        out = os.path.join(media, cfg.get("layout", {}).get("videos", "videos"), f"{name}.mp4")
        return {"title": f"Video: {name}", "steps": [
            {"fn": "slideshow", "label": "pictures + voice -> video (gentle camera, fades, captions)",
             "args": {"pictures": pics, "audio": audio, "out": out, "size": b.get("size", "16:9"),
                      "order": b.get("order") or [], "captions": b.get("captions") or {}}}],
            "outputs": [out]}
    if kind == "studio":                                     # HubGPT Video Sukoon video (+ reels)
        audio = b.get("audio") or ""
        if not audio:
            raise ValueError("choose a narration")
        ep = os.path.join(cfg["episodes_dir"], name)
        return {"title": f"Sukoon video: {name}", "steps": [
            {"fn": "free_gpu", "label": "free the graphics card"},
            {"fn": "studio_prepare", "label": "episode folder for HubGPT Video",
             "args": {"ep": ep, "audio": audio, "title": b.get("title_text") or "", "artwork": b.get("artwork", "")}},
            {"fn": "studio_video", "label": "HubGPT Video makes the video (+ reels)",
             "args": {"ep": ep, "reels": int(b.get("reels") or 0), "clips": int(b.get("clips") or 0),
                      "preview": float(b.get("preview") or 0), "artwork": b.get("artwork", ""),
                      "opts": studio_opts(b)}}],
            "outputs": [os.path.join(ep, cfg.get("episode_output_name", "Output"))]}
    if kind == "check_video":                                 # the repair man looks (no graphics card)
        v = b.get("video") or ""
        if not os.path.exists(v):
            raise ValueError("pick a video")
        return {"title": f"Check: {os.path.basename(v)}", "steps": [
            {"fn": "repair_check", "label": "check voice, subtitles and motion", "args": {"video": v}}],
            "outputs": [os.path.dirname(v)]}
    if kind == "repair":                                      # ... and fixes what you ticked
        import repair
        v = b.get("video") or ""
        if not os.path.exists(v):
            raise ValueError("pick a video")
        p = repair.find_parts(cfg, v)
        steps = []
        if p["is_reel"]:
            used = repair._load(os.path.join(os.path.dirname(v), "reels_made.json"), []) or []
            rec = next((u for u in used if u.get("file") == os.path.basename(v)), None)
            if not rec or not p["work"]:
                raise ValueError("this reel was made before the hub kept notes on reels - make a new one on the Reels page")
            steps = [{"fn": "repair_keep_old", "label": "the old reel goes to reels\\_old", "args": {"path": v}},
                     {"fn": "free_gpu", "label": "free the graphics card"},
                     {"fn": "studio_reels", "label": "the reel again, same moment",
                      "args": {"video": p["main_video"], "work": p["work"], "count": 1, "mode": "at",
                               "at": str(rec["a"]), "length": max(15.0, float(rec["b"]) - float(rec["a"]) - 5.8),
                               "motion": b.get("motion") or "sentence"}}]
            return {"title": f"Repair reel: {os.path.basename(v)}", "steps": steps, "outputs": [os.path.dirname(v)]}
        lines = ",".join(str(int(x)) for x in (b.get("voice_lines") or []))
        ser = series_of_video(cfg, p)
        subtitle_text = str(b.get("subtitle_text") or "").strip()
        if len(subtitle_text) > 100_000:
            raise ValueError("subtitle text is too large (maximum 100,000 characters)")
        if ser and b.get("subtitles"):
            steps = []
            if lines:
                if not p["narration"]:
                    raise ValueError("this video has no narration folder for a subtitle/voice repair")
                import story as _st
                en = _st.narration_lang(p["narration"]) == "en"
                speak = {"fn": "en_speak", "label": f"English narrator: lines {lines} again",
                         "args": {"name": p["name"], "redo": lines}} if en else \
                        {"fn": "sv_speak", "label": f"your voice: lines {lines} again",
                         "args": {"name": p["name"], "sets": ["with_music=false"], "redo": lines}}
                steps += [{"fn": "repair_save_voice", "label": "keep the voice exactly as it is in the video",
                           "args": {"nar": p["narration"]}},
                          {"fn": "free_gpu", "label": "free the graphics card"}, speak]
            steps.append({"fn": "repair_series_subtitles", "label": "rebuild this video's subtitles from its own narration",
                          "args": {"video": v, "lines": lines, "subtitle_text": subtitle_text}})
            return {"title": f"Repair subtitles: {os.path.basename(v)}", "steps": steps,
                    "outputs": [p["output"]]}
        if ser:                                                # series videos: voice lines go the quick way
            if not lines:
                raise ValueError("for a series video only voice lines can be fixed here - tick a line")
            b = dict(b, subtitles=False, motion=False, quick=True)
        loop_img = (repair._load(p["loop"] + ".json", {}) or {}).get("image", "") if p["loop"] else ""
        opts = []
        if lines and not b.get("subtitles") and not b.get("motion") and b.get("quick", True) is not False \
                and p["narration"] and not p["is_reel"]:
            # quick: only the ticked lines are spoken again and put into the SAME places of the old voice;
            # the finished video keeps its picture and only gets the new sound (minutes instead of a whole render)
            import story as _st
            en = _st.narration_lang(p["narration"]) == "en"
            speak = {"fn": "en_speak", "label": f"English narrator: lines {lines} again", "args": {"name": p["name"], "redo": lines}} \
                if en else {"fn": "sv_speak", "label": f"your voice: lines {lines} again",
                            "args": {"name": p["name"], "sets": ["with_music=false"], "redo": lines}}
            return {"title": f"Quick fix: {os.path.basename(v)} (lines {lines})", "steps": [
                {"fn": "repair_save_voice", "label": "keep the voice exactly as it is in the video", "args": {"nar": p["narration"]}},
                {"fn": "free_gpu", "label": "free the graphics card"}, speak,
                {"fn": "repair_patch", "label": "new lines into their old places, music, new sound for the video (no new render)",
                 "args": {"video": v, "lines": lines, "loop_img": loop_img}}], "outputs": [p["output"]]}
        if b.get("subtitles"):
            opts += ["--redo-transcript", "--redo-captions"]
            if subtitle_text:
                # episode_inputs intentionally ignores transcript_*.txt unless --script explicitly selects it.
                script_path = os.path.join(p["episode"], "transcript_override.txt")
                steps.append({"fn": "repair_write_subtitle_text", "label": "save the selected subtitle text",
                              "args": {"path": script_path, "text": subtitle_text}})
                opts += ["--script", script_path]
            else:
                opts.append("--no-script")
        if lines:
            if not p["narration"]:
                raise ValueError("no narration of yours for this video - voice lines cannot be remade")
            shaped = os.path.exists(os.path.join(p["narration"], "music_shape.json"))
            total = _duration(os.path.join(p["narration"], "narration_music.wav")) / 60 if shaped else 0
            track = (repair._load(os.path.join(p["narration"], "music_shape.json"), {}) or {}).get("track", "")
            import story as _st
            if _st.narration_lang(p["narration"]) == "en":
                steps += [{"fn": "free_gpu", "label": "free the graphics card"},
                          {"fn": "en_speak", "label": f"English narrator: lines {lines} again",
                           "args": {"name": p["name"], "redo": lines}}]
            else:
                steps += [{"fn": "free_gpu", "label": "free the graphics card"},
                          {"fn": "sv_speak", "label": f"your voice: lines {lines} again",
                           "args": {"name": p["name"], "sets": ["with_music=false"] if shaped else [], "redo": lines}}]
            if shaped:
                steps.append({"fn": "shape_mix", "label": "music again (same track, same length)",
                              "args": {"name": p["name"], "track": track, "tail": f"total:{total:.3f}"}})
            steps.append({"fn": "studio_prepare", "label": "the new voice into the episode folder",
                          "args": {"ep": p["episode"], "audio": p["narration"]}})
            opts += ["--redo-transcript", "--redo-captions"]
        steps += [{"fn": "repair_rerender", "label": "old video aside" + (" + living loop again" if b.get("motion") else ""),
                   "args": {"video": v, "new_loop": bool(b.get("motion"))}},
                  {"fn": "free_gpu", "label": "free the graphics card"},
                  {"fn": "studio_video", "label": "HubGPT Video draws the video again (your reels are kept)",
                   "args": {"ep": p["episode"], "reels": 0, "clips": 0, "artwork": loop_img,
                            "opts": sorted(set(opts), key=opts.index)}}]
        return {"title": f"Repair: {os.path.basename(v)}", "steps": steps, "outputs": [p["output"]]}
    if kind == "reels":                                       # new reels from a finished episode video
        v = b.get("video") or ""
        if not v or not os.path.exists(v):
            raise ValueError("pick a finished episode video")
        ep_dir = os.path.dirname(os.path.dirname(os.path.dirname(v))) if os.path.basename(os.path.dirname(v)).lower() == cfg.get("layout", {}).get("episode_reels", "reels").lower() else os.path.dirname(os.path.dirname(v))
        work = studio_work_dir(cfg, ep_dir, os.path.splitext(os.path.basename(v))[0])
        if not os.path.isdir(work):
            raise ValueError("this video was not made by HubGPT Video here (no transcript / moments for it)")
        n = max(1, min(10, int(b.get("count") or 3)))
        return {"title": f"Reels: {os.path.basename(v)}", "steps": [
            {"fn": "free_gpu", "label": "free the graphics card"},
            {"fn": "studio_reels", "label": f"HubGPT Video makes {n} reel(s)",
             "args": {"video": v, "work": work, "count": n, "length": float(b.get("length") or 24),
                      "mode": b.get("mode", "new"), "at": b.get("at", ""), "motion": b.get("motion", "sentence")}}],
            "outputs": [os.path.join(os.path.dirname(v), cfg.get("layout", {}).get("episode_reels", "reels"))]}
    if kind in ("horror", "genre"):                           # a series story: script, your voice, scenes, sound, video
        gid = b.get("genre") if kind == "genre" else "bhay"
        if gid not in GENRES:
            raise ValueError("unknown series")
        G = GENRES[gid]
        gv = genre_voice_sets(cfg, gid, b)
        text = (b.get("script") or "").strip()
        minutes = float(b.get("speech_min") or 15)
        ST = set(b.get("stages") or ["script", "voice", "scenes", "sound", "pictures", "video", "reels"])
        if "script" in ST:
            name = unique_genre_name(cfg, media, gid, name)
        nar = narration_dir(cfg, name)
        pics = os.path.join(media, cfg.get("layout", {}).get("pictures", "pictures"), name + "_scenes")
        ep = os.path.join(cfg["episodes_dir"], name)
        out = os.path.join(ep, cfg.get("episode_output_name", "Output"), slug(name).replace(" ", "_") + ".mp4")
        if "script" not in ST and "voice" not in ST:             # a tool page working on an existing voice
            if not os.path.exists(os.path.join(nar, "timing.json")):
                raise ValueError("choose a narration (made with your voice)")
            steps = []
            if "scenes" in ST:
                steps.append({"fn": "sfx_fetch", "label": "sound effects library for this series (CC0, only what is missing)", "args": {"genre": gid}}); steps.append({"fn": "horror_scenes", "label": "the AI plans the scenes: pictures + sounds for every part",
                              "args": {"name": name, "narration": nar, "out": pics, "count": int(b.get("scenes") or 0),
                                       "genre": gid}})
            if "sound" in ST:
                steps.append({"fn": "horror_sound", "label": f"{G['label']} music" + (" + ambience + effects" if "scenes" in ST
                              or os.path.exists(os.path.join(pics, "shots.json")) else "") + " under your voice",
                              "args": {"name": name, "pictures": pics, "track": b.get("music_track") or "", "genre": gid,
                                       "tail": b.get("tail") or "1min", "music_db": b.get("music_db")}})
            if "pictures" in ST:
                steps += [{"fn": "comfy_images", "label": f"ComfyUI paints the scenes ({G['label']} look)",
                              "args": {"out": pics, "style": G["style"], "size": "16:9", "engine": cfg.get("image_engine"),
                              "seed": None, "silhouette": False, "variants": 2 if b.get("best_pictures") else 1}},
                  {"fn": "pick_scenes", "label": "the vision AI keeps the best version of every scene (bad ones painted again)", "args": {"out": pics, "genre": gid}}]
            if "video" in ST:
                steps += [{"fn": "free_gpu", "label": "free the graphics card"},
                          {"fn": "horror_video", "label": f"the {G['label']} video: camera moves, light, subtitles",
                           "args": {"name": name, "pictures": pics, "narration": nar, "out": out,
                                    "title": b.get("title_text") or "", "genre": gid}}]
            if "reels" in ST and int(b.get("reels", 2) or 0) > 0:
                steps.append({"fn": "genre_reels", "label": f"{int(b.get('reels', 2))} reel(s), 9:16",
                              "args": {"name": name, "pictures": pics, "out": os.path.join(os.path.dirname(out), cfg.get("layout", {}).get("episode_reels", "reels")),
                                       "count": int(b.get("reels", 2)), "length": float(b.get("reel_len") or 30), "genre": gid}})
            what = {"scenes": "scenes", "sound": "sound", "pictures": "pictures", "video": "video", "reels": "reels"}
            return {"title": f"{G['label']} {' + '.join(what[x] for x in what if x in ST)}: {name}", "steps": steps,
                    "outputs": [pics if "video" not in ST else os.path.dirname(out), nar]}
        steps = [{"fn": "write_script", "label": "save the script", "args": {"name": name, "text": text}}] if text else \
            [{"fn": "horror_story", "label": f"the AI writes a fresh {G['label']} script (no repeats)",
              "args": {"name": name, "topic": b.get("topic", ""), "minutes": minutes, "notes": b.get("notes", ""),
                       "engine": b.get("writer", "auto"), "recipe": b.get("recipe") or None, "genre": gid,
                       "level": b.get("level") or "standard", "auto_name": bool(b.get("auto_name"))}}]
        if float(b.get("total_min") or 0) and b.get("fit", True) is not False:
            steps.append({"fn": "fit_script", "label": "fit the script into the video length (AI shortens if needed)",
                          "args": {"name": name, "total_min": float(b["total_min"]), "engine": b.get("writer", "auto"),
                                   "story_mode": True}})
        if hard_words(cfg):
            steps.append({"fn": "avoid_hard_words", "label": "lines with words your voice stumbles on: said another way",
                          "args": {"name": name, "genre": gid}})
        if b.get("emotions", True) or b.get("dramatic", True):
            steps.append({"fn": "mark_emotions", "label": "the AI marks the feeling of each line ([डर] [उदास] …)",
                          "args": {"name": name, "genre": gid, "emotions": bool(b.get("emotions", True)),
                                   "pauses": bool(b.get("dramatic", True))}})
        auto_story_tone = not text and not b.get("voice_style_manual")
        voice_style = "__auto_story_theme__" if auto_story_tone else (b.get("voice_style") or G["voice"])
        voice_label = ("a tone chosen to fit the generated story" if auto_story_tone else
                       f"the tone of '{voice_style}'" + (f" (your {G['label']} voice)" if gv[1] else
                       f" (your plain voice - no {G['label']} training yet)"))
        steps += [{"fn": "free_gpu", "label": "free the graphics card"},
                  {"fn": "sv_speak", "label": f"your voice reads it in {voice_label}",
                   "args": {"name": name, "sets": gv[0], "redo": "", "style": voice_style}},
                  {"fn": "sfx_fetch", "label": "sound effects library for this series (CC0, only what is missing)", "args": {"genre": gid}},
                  {"fn": "horror_scenes", "label": "the AI plans the scenes: pictures + sounds for every part",
                   "args": {"name": name, "narration": nar, "out": pics, "count": int(b.get("scenes") or 0), "genre": gid}},
                  {"fn": "horror_sound", "label": "music + ambience + sound effects under your voice",
                   "args": {"name": name, "pictures": pics, "track": b.get("music_track") or "", "genre": gid,
                            "tail": f"total:{float(b['total_min'])}" if float(b.get("total_min") or 0) else (b.get("tail") or "1min"),
                            "music_db": b.get("music_db")}},
                  {"fn": "comfy_images", "label": f"ComfyUI paints the scenes ({G['label']} look)",
                   "args": {"out": pics, "style": G["style"], "size": "16:9", "engine": cfg.get("image_engine"), "seed": None,
                            "silhouette": False, "variants": 2 if b.get("best_pictures") else 1}},
                  {"fn": "pick_scenes", "label": "the vision AI keeps the best version of every scene (bad ones painted again)", "args": {"out": pics, "genre": gid}},
                  {"fn": "free_gpu", "label": "free the graphics card"},
                  {"fn": "horror_video", "label": f"the {G['label']} video: camera moves, light, subtitles",
                   "args": {"name": name, "pictures": pics, "narration": nar, "out": out,
                            "title": b.get("title_text") or "", "genre": gid}}]
        if int(b.get("reels", 2) or 0) > 0:
            steps.append({"fn": "genre_reels", "label": f"{int(b.get('reels', 2))} reel(s), 9:16",
                          "args": {"name": name, "pictures": pics, "out": os.path.join(os.path.dirname(out), cfg.get("layout", {}).get("episode_reels", "reels")),
                                   "count": int(b.get("reels", 2)), "length": float(b.get("reel_len") or 30), "genre": gid}})
        FN = {"sfx_fetch": "scenes", "pick_scenes": "pictures", "horror_scenes": "scenes", "horror_sound": "sound", "comfy_images": "pictures", "horror_video": "video",
              "genre_reels": "reels"}
        steps = [x for x in steps if FN.get(x["fn"], "voice") in ST or x["fn"] in ("free_gpu",)]
        while steps and steps[-1]["fn"] == "free_gpu":
            steps.pop()
        if "video" not in ST:
            return {"title": f"{G['label']} voice: {name}", "steps": steps, "outputs": [nar]}
        return {"title": f"{G['label']}: {name}", "steps": steps, "outputs": [os.path.dirname(out), nar]}
    if kind == "horror_stories":                              # several भय stories, no voice / video
        n = max(1, min(20, int(b.get("n") or 3)))
        topics = [t.strip() for t in re.split(r"[;\n]", b.get("topic") or "") if t.strip()]
        gid = b.get("genre") if b.get("genre") in GENRES else "bhay"
        return {"title": f"Write {n} {GENRES[gid]['label']} scripts", "steps": [
            {"fn": "write_horror_stories", "label": f"the AI writes {n} fresh {GENRES[gid]['label']} scripts (no repeats)",
             "args": {"n": n, "topics": topics, "minutes": float(b.get("speech_min") or 15), "engine": b.get("writer", "auto"),
                      "genre": gid, "level": b.get("level") or "standard"}}],
            "outputs": [os.path.join(media, cfg.get("layout", {}).get("scripts", "scripts"))]}
    if kind == "sound":                                       # music under any narration
        nm = slug(b.get("name") or "")
        if not os.path.exists(os.path.join(narration_dir(cfg, nm, existing=True), "narration.wav")):
            raise ValueError("choose a narration")
        track = b.get("music_track") or ""
        if b.get("kind_music") == "bhay" and not track:
            track = _horror_track(cfg)
        return {"title": f"Sound: {nm}", "steps": [
            {"fn": "shape_mix", "label": "music under your voice, shaped like your episodes",
             "args": {"name": nm, "track": track, "tail": b.get("tail") or "1min", "music_db": b.get("music_db")}}],
            "outputs": [narration_dir(cfg, nm, existing=True)]}
    if kind == "voice_overnight":                             # all recordings -> lines -> sorted -> one voice per series
        return {"title": "Overnight: your recordings -> a voice for every series", "steps": [
            {"fn": "free_gpu", "label": "free the graphics card"},
            {"fn": "sv_dataset", "label": "read every recording in your folders (Whisper hears the words, no script needed)"},
            {"fn": "classify_recordings", "label": "the AI sorts recordings without a series"},
            {"fn": "train_all_series", "label": "train a voice for every series with enough recordings (~45 min each)",
             "args": {"include_sukoon": bool(b.get("include_sukoon")), "min_minutes": float(b.get("min_minutes") or 10),
                      "force": bool(b.get("force"))}}],
            "outputs": [voice_model_root(cfg)]}
    if kind == "voice_train":                                 # your voice, trained for one series
        sid = b.get("series") or ""
        info = series_load(cfg)["series"].get(sid)
        if not info:
            raise ValueError("choose a series")
        return {"title": f"Train your {info['name']} voice", "steps": [
            {"fn": "free_gpu", "label": "free the graphics card (close ComfyUI pictures for now)"},
            {"fn": "sv_train", "label": f"learning your {info['name']} delivery from its recordings (~30-60 min)",
             "args": {"series": sid, "epochs": b.get("epochs")}}],
            "outputs": [voice_model_root(cfg)]}
    if kind == "english_setup":                               # English narrator: libraries (copied) + a 3-line test
        return {"title": "Set up + test the English narrator", "steps": [
            {"fn": "free_gpu", "label": "free the graphics card"},
            {"fn": "en_setup", "label": "libraries for the English narrator (copied from MukhtalifAudioBook if there)"},
            {"fn": "en_test", "label": "three test lines (speed + sound)", "args": {"engine": b.get("engine") or ""}}],
            "outputs": [os.path.join(cfg["temporary_dir"], "english_test")]}
    if kind == "practice_story":
        return {"title": "Practice story: the words your voice stumbles on", "steps": [
            {"fn": "practice_story", "label": "the AI writes a short story using every hard word",
             "args": {"minutes": float(b.get("minutes") or 3)}}], "outputs": [os.path.join(media, cfg.get("layout", {}).get("scripts", "scripts"))]}
    if kind == "sfx_fetch":
        import sfx_lib
        return {"title": "Sound effects library" + (f" ({b['genre']})" if b.get("genre") else ""),
                "steps": [{"fn": "sfx_fetch", "label": "find + download + register sound effects (CC0 only)",
                           "args": {"genre": b.get("genre") or ""}}], "outputs": [sfx_lib.root(cfg)]}
    if kind == "elements_fetch":                              # top the elements library up (Pixabay, no GPU)
        import elements_auto
        kinds = b.get("kinds") or elements_auto.missing(cfg, list(elements_auto.WORDS))
        return {"title": "Fetch elements: " + (", ".join(kinds) or "all complete"),
                "steps": [{"fn": "elements_fetch", "label": "find + download + register + loop new element clips",
                           "args": {"kinds": kinds}}], "outputs": [elements_auto.lib_dir(cfg)]}
    if kind in ("stories", "stories_local"):                 # several scripts, no voice / video
        n = max(1, min(20, int(b.get("n") or 5)))
        topics = [t.strip() for t in re.split(r"[;\n]", b.get("topic") or "") if t.strip()]
        lang = "en" if b.get("language") == "en" else "hi"
        return {"title": f"Write {n} Sukoon scripts" + (" (English)" if lang == "en" else ""), "steps": [
            {"fn": "write_stories", "label": f"the AI writes {n} fresh scripts (different topics, no repeats)",
             "args": {"n": n, "topics": topics, "minutes": float(b.get("speech_min") or 12),
                      "category": b.get("category", ""), "lang": lang,
                      "engine": "local" if kind == "stories_local" else b.get("writer", "auto")}}],
            "outputs": [os.path.join(media, cfg.get("layout", {}).get("scripts", "scripts"))]}
    if kind == "episode":                                    # everything in one go
        text = (b.get("script") or "").strip()
        topic = (b.get("topic") or "").strip()
        if not text and not topic and not b.get("auto_topic"):
            raise ValueError("paste a script, give a topic, or tick 'let the hub choose the topic'")
        sets = voice_sets(b, cfg)
        english = b.get("language") == "en"
        nar = narration_dir(cfg, name)
        art = os.path.join(media, cfg.get("layout", {}).get("pictures", "pictures"), name + "_artwork")
        ep = os.path.join(cfg["episodes_dir"], name)
        total = float(b.get("total_min") or 0)
        speech = float(b.get("speech_min") or 12)
        if total:
            speech = min(speech, max(2.0, total - MIN_TAIL))
        if text:
            steps = [{"fn": "write_script", "label": "save the script", "args": {"name": name, "text": text}}]
        else:
            steps = [{"fn": "llm_story", "label": "the AI writes a fresh Sukoon script (your style, no repeats)",
                      "args": {"name": name, "topic": topic, "minutes": speech, "category": b.get("category", ""),
                               "notes": b.get("notes", ""), "subscribe": b.get("subscribe", True) is not False,
                               "engine": b.get("writer", "auto"), "recipe": b.get("recipe") or None,
                               "lang": "en" if english else "hi"}}]
        if total and b.get("fit", True) is not False and b.get("lang") != "en":
            steps.append({"fn": "fit_script", "label": "fit the script into the video length (AI shortens if needed)",
                          "args": {"name": name, "total_min": total, "engine": b.get("writer", "auto")}})
        if text and b.get("lang") == "en" and not english:
            steps.append({"fn": "llm_translate", "label": "the AI translates English -> Hindi (Sukoon style)",
                          "args": {"name": name}})
        if not english and (b.get("emotions") or b.get("dramatic")):
            steps.append({"fn": "mark_emotions", "label": "the AI adds sparse Hindi performance cues",
                          "args": {"name": name, "emotions": bool(b.get("emotions")),
                                   "pauses": bool(b.get("dramatic"))}})
        shaped = use_shape(b, cfg)
        if shaped:
            sets.append("with_music=false")
        steps.append({"fn": "free_gpu", "label": "free the graphics card"})
        if english:
            steps.append({"fn": "en_speak", "label": "the English narrator reads the script (Qwen3-TTS)",
                          "args": {"name": name, "redo": "", "speaker": b.get("en_speaker") or ""}})
        else:
            steps.append({"fn": "sv_speak", "label": "your voice reads the script (HubGPT Voice)",
                          "args": {"name": name, "sets": sets, "redo": ""}})
        if shaped:
            steps.append({"fn": "shape_mix", "label": "music the way your Sukoon episodes do it",
                          "args": {"name": name, "track": b.get("music_track") or "",
                                   "tail": f"total:{float(b['total_min'])}" if b.get("total_min")
                                   else b.get("tail", "usual")}})
        artwork = b.get("artwork") or ""
        if not artwork:
            steps += [{"fn": "art_prompt", "label": "the AI reads the WHOLE script and designs one artwork for it",
                       "args": {"name": name, "narration": nar, "script": text, "out": art, "idea": b.get("idea", ""),
                                "count": int(b.get("art_choices") or 3)}},
                      {"fn": "comfy_images", "label": "ComfyUI paints the artwork (a few versions)",
                       "args": {"out": art, "style": b.get("style") or default_style(cfg), "size": "16:9",
                                "engine": cfg.get("image_engine"), "seed": None}},
                      {"fn": "free_gpu", "label": "free the graphics card"},
                      {"fn": "pick_art", "label": "the vision AI picks the version that fits the script best",
                       "args": {"out": art}},
                      {"fn": "free_gpu", "label": "free the graphics card"}]
            artwork = art
        steps.append({"fn": "elements_check", "label": "which moving elements this episode needs (fetched in the background)",
                      "args": {"name": name, "art": art}})
        steps += [{"fn": "studio_prepare", "label": "episode folder for HubGPT Video",
                   "args": {"ep": ep, "audio": nar, "title": b.get("title_text") or "", "artwork": artwork}},
                  {"fn": "studio_video", "label": "HubGPT Video makes the video (+ reels)",
                   "args": {"ep": ep, "reels": int(b.get("reels") or 3), "clips": 0,
                            "preview": float(b.get("preview") or 0), "artwork": artwork,
                            "opts": studio_opts(b) + (["--language", "english", "--lang", "en"] if english else [])}}]
        return {"title": f"Episode: {name}", "steps": steps, "outputs": [nar, os.path.join(ep, cfg.get("episode_output_name", "Output"))]}
    raise ValueError(f"unknown job kind {kind!r}")







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
