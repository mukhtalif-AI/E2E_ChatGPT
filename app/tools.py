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




def _secs(t):
    t = t.strip()
    if not t:
        return None
    parts = [float(x) for x in t.split(":")]
    return parts[-1] + (parts[-2] * 60 if len(parts) > 1 else 0) + (parts[-3] * 3600 if len(parts) > 2 else 0)


def repair_check(ctx, video):
    import repair
    return repair.check(ctx, video)


def repair_keep_old(ctx, path):
    import repair
    m = repair.keep_old(path)
    ctx["say"](f"   kept: {m}")
    return True


def repair_save_voice(ctx, nar):
    import repair
    return repair.save_voice(nar)


def repair_patch(ctx, video, lines, loop_img=""):
    """quick fix: the remade lines go into the old places; if one doesn't fit, the whole video is drawn again"""
    import repair
    cfg, say = ctx["cfg"], ctx["say"]
    p = repair.find_parts(cfg, video)
    nums = [int(x) for x in re.findall(r"\d+", str(lines))]
    ser = series_of_video(cfg, p)
    if ser:                                           # a series video (भय, रहस्य …): its own sound + video maker
        ok = repair.patch_voice(cfg, p["narration"], nums, say)
        total = _duration(os.path.join(p["narration"], "narration_music.wav")) / 60
        if not horror_sound(ctx, p["name"], ser["pictures"], ser.get("track") or "", ser["genre"],
                            f"total:{total:.3f}" if total else ser.get("tail", "1min")):
            return False
        if ok is True:
            repair.new_soundtrack(cfg, video, _audio_of(p["narration"]), say)
            say("   done - the picture was kept, only the sound is new (scenes, music and effects as before)")
            return True
        say(f"   {ok} -> the {GENRES[ser['genre']]['label']} video is drawn again (the old one is kept)")
        repair.keep_old(video)
        return horror_video(ctx, p["name"], ser["pictures"], p["narration"], video, ser.get("title", ""), ser["genre"])
    ok = repair.patch_voice(cfg, p["narration"], nums, say)
    shp = repair._load(os.path.join(p["narration"], "music_shape.json"), {}) or {}
    total = _duration(os.path.join(p["narration"], "narration_music.wav")) / 60 if shp else 0
    if shp and not shape_mix(ctx, p["name"], shp.get("track", ""), f"total:{total:.3f}"):
        return False
    studio_prepare(ctx, p["episode"], p["narration"])
    if ok is True:
        repair.new_soundtrack(cfg, video, _audio_of(p["narration"]), say)
        say("   done - the picture was kept, only the sound is new (reels made earlier keep their old sound)")
        return True
    say(f"   {ok} -> drawing the whole video again instead")
    if not repair.prepare_rerender(ctx, video):
        return False
    free_gpu(ctx)
    return studio_video(ctx, p["episode"], 0, 0, 0, loop_img, ["--redo-transcript", "--redo-captions"])


def repair_series_subtitles(ctx, video, lines="", subtitle_text=""):
    """Redraw a genre video from its own narration/timing, optionally replacing visible text from a TXT."""
    import repair
    cfg, say = ctx["cfg"], ctx["say"]
    p = repair.find_parts(cfg, video)
    ser = series_of_video(cfg, p)
    if not ser or not p.get("narration"):
        raise RuntimeError("could not match this video to its genre narration")
    nums = [int(x) for x in re.findall(r"\d+", str(lines))]
    if nums:
        result = repair.patch_voice(cfg, p["narration"], nums, say)
        if result is not True:
            say(f"   voice line patch could not preserve the old timing ({result}); rendering with the updated narration timing")
    total = _duration(os.path.join(p["narration"], "narration_music.wav")) / 60
    if not horror_sound(ctx, p["name"], ser["pictures"], ser.get("track") or "", ser["genre"],
                        f"total:{total:.3f}" if total else ser.get("tail", "1min")):
        return False
    moved = repair.keep_old(video)
    if moved:
        say(f"   old video kept: {moved}")
    free_gpu(ctx)
    return horror_video(ctx, p["name"], ser["pictures"], p["narration"], video,
                        ser.get("title", ""), ser["genre"], subtitle_text)


def repair_write_subtitle_text(ctx, path, text):
    """Save a user's selected subtitle source for the queued Studio render."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        old = os.path.join(os.path.dirname(path), "_old")
        os.makedirs(old, exist_ok=True)
        shutil.move(path, os.path.join(old, time.strftime("%H%M%S_") + os.path.basename(path)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(text).strip() + "\n")
    ctx["say"](f"   subtitle source saved: {path}")
    return True


def series_of_video(cfg, p):
    """{genre, pictures, track, title} when this video was made by a series pipeline, else None"""
    nar = p.get("narration") or ""
    pics = os.path.join(cfg["pictures_dir"], p["name"] + "_scenes")
    mk = os.path.join(nar, "hub_series.json") if nar else ""
    d = {}
    if mk and os.path.exists(mk):
        try:
            d = json.load(open(mk, encoding="utf-8"))
        except Exception:
            d = {}
    if not d and not os.path.exists(os.path.join(pics, "shots.json")):
        return None
    if not d.get("genre"):
        low = p["name"].lower()
        d["genre"] = next((g for g in GENRES if low.startswith(g) or GENRES[g]["label"] in p["name"]), "bhay")
    d.setdefault("pictures", pics)
    if not d.get("track"):
        shp = os.path.join(nar, "music_shape.json")
        try:
            d["track"] = json.load(open(shp, encoding="utf-8")).get("track", "")
        except Exception:
            d["track"] = ""
    return d


def _series_note(cfg, name, **kv):
    """remember how a series video was made (so a repair redoes it the same way)"""
    f = os.path.join(narration_dir(cfg, name, existing=True), "hub_series.json")
    try:
        d = json.load(open(f, encoding="utf-8")) if os.path.exists(f) else {}
        d.update({k: v for k, v in kv.items() if v not in (None, "")})
        json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def remix_like_before(ctx, name):
    """after phrases were remade: the narration gets the same music bed (series: + ambience / effects) again"""
    cfg, say = ctx["cfg"], ctx["say"]
    nar = narration_dir(cfg, name, existing=True)
    total = _duration(os.path.join(nar, "narration_music.wav")) / 60
    ser = series_of_video(cfg, {"name": os.path.basename(nar), "narration": nar})
    if ser:
        return horror_sound(ctx, name, ser["pictures"], ser.get("track") or "", ser["genre"],
                            f"total:{total:.3f}" if total else "1min")
    shp = os.path.join(nar, "music_shape.json")
    if os.path.exists(shp) and total:
        tr = (json.load(open(shp, encoding="utf-8")) or {}).get("track", "")
        if tr:
            return shape_mix(ctx, name, tr, f"total:{total:.3f}")
    say("   (no music was under this voice before - left as it is)")
    return True


def narration_lines(cfg, d):
    """every phrase of a narration (number, words, where it is) + which ones sound off, and its video if any"""
    import repair
    if not d or not os.path.isdir(d):
        return {"lines": []}
    tim = repair._load(os.path.join(d, "timing.json"), []) or []
    bad = {x["line"]: x["why"] for x in repair.check_voice({"narration": d})}
    name = os.path.basename(d)
    vids = glob.glob(os.path.join(cfg["episodes_dir"], name, cfg.get("episode_output_name", "Output"), "*.mp4"))
    vids = [v for v in vids if "_preview" not in v]
    au = os.path.join(d, "narration.wav")
    return {"lines": [{"n": x["n"], "text": x["text"], "start": x["start"], "why": bad.get(x["n"], []),
                       "emotion": x.get("emo", "")} for x in tim],
            "audio": au if os.path.exists(au) else "", "video": vids[0] if vids else ""}


def repair_rerender(ctx, video, new_loop=False):
    import repair
    return repair.prepare_rerender(ctx, video, new_loop)


def _duration(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


_PREV = {}


def reel_motions(cfg, video):
    """previews of every camera motion on this episode's artwork (made once, ~20 s, no graphics card)"""
    d = os.path.join(os.path.dirname(video), cfg.get("layout", {}).get("episode_reels", "reels"), cfg.get("layout", {}).get("episode_motion_cache", "_motions"))
    mj = os.path.join(d, "motions.json")
    if os.path.exists(mj):
        names = json.load(open(mj, encoding="utf-8"))
        return {"ready": True, "motions": [{"id": k, "label": v, "file": os.path.join(d, k + ".mp4")} for k, v in names.items()]}
    info = os.path.splitext(video)[0] + ".reel_info.json"
    if not os.path.exists(info):
        return {"ready": False, "error": "this video has no .reel_info.json (made before the hub?)"}
    loop = json.load(open(info, encoding="utf-8")).get("loop", "")
    p = _PREV.get(video)
    if p is None or p.poll() is not None:
        flags = 0x08000000 if os.name == "nt" else 0
        _PREV[video] = subprocess.Popen([cfg["studio_python"], "-s", os.path.join(cfg["studio_dir"], "engine", "reels.py"),
                                         "--previews", loop, d], cwd=cfg["studio_dir"], creationflags=flags,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ready": False, "pending": True}


def studio_reels(ctx, video, work, count=3, length=24, mode="new", at="", motion="sentence"):
    cfg = ctx["cfg"]
    env = {"REELS_LENGTH": str(float(length)), "REELS_MOTION": motion or "sentence"}
    if mode == "at":
        secs = [x for x in (_secs(t) for t in re.split(r"[,;\s]+", at or "")) if x is not None]
        if not secs:
            raise RuntimeError("give the times, e.g. 2:03, 5:16")
        env["REELS_AT"] = ",".join(str(x) for x in secs)
        count = len(secs)
    elif mode == "new":
        env["REELS_NEW"] = "1"
    sd = cfg["studio_dir"]
    rc = ctx["run_cmd"]([cfg["studio_python"], "-u", "-s", os.path.join("engine", "reels.py"), work, video, str(count)],
                        cwd=sd, env=env)
    return rc == 0












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








































# ------------------------------------------------------------------ भय (Bhay) horror stories
def horror_story(ctx, name, topic="", minutes=15, notes="", engine="auto", recipe=None, genre="bhay", level="standard", auto_name=False):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    script, info = story.write_genre(cfg, genre, say, topic, float(minutes or 15), notes, engine, recipe, ctx["stopped"],
                                     level)
    if story.nwords(script) < 150:
        raise RuntimeError("the story came out too short - try again")
    summ = story.summarise(cfg, script, say)
    if auto_name and cfg.get("experimental_mode"):
        # Experimental: <Series>-<short English title>; the episode folder is renamed now, before anything is in it
        import experimental_workspace
        final_name = experimental_workspace.title_name(ctx, genre, name, [summ.get("title_en"),
                                                                          info["recipe"].get("topic"), topic])
        if final_name and final_name != name:
            _rename_job_values(ctx.get("job") or {}, name, final_name)
            for k, v in list(cfg.items()):   # this run's paths (narration folder) point into the episode folder
                if isinstance(v, str) and name in v:
                    cfg[k] = v.replace(name, final_name)
            name = final_name
            say(f"   episode named from the story title: {name}")
    elif auto_name:
        prefix = {"bhay": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse", "romanch": "Romanch",
                  "kareeb": "Kareeb", "gyaan": "Gyaan"}.get(genre, genre.title())
        title = summ.get("title_en") or summ.get("title_hi") or info["recipe"].get("topic") or topic or "Story"
        final_name = unique_genre_name(cfg, ctx["media"], genre, f"{prefix} - {slug(title, 50)}")
        old_name = name
        if final_name != old_name:
            _rename_job_values(ctx.get("job") or {}, old_name, final_name)
            name = final_name
            say(f"   episode folder named from the generated title: {name}")
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    open(p, "w", encoding="utf-8").write(script)
    entry = {"name": name, "date": time.strftime("%Y-%m-%d %H:%M"), "genre": "horror" if genre == "bhay" else genre,
             "topic": info["recipe"]["topic"],
             "recipe": info["recipe"], "model": info["model"], "words": story.nwords(script), "summary": summ,
             "level": info.get("level") or level}
    story.add_ledger(cfg, entry)
    json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if summ.get("title_hi"):
        say(f"   title: {summ.get('title_hi')}  /  {summ.get('title_en', '')}")
    for ln in [l for l in script.splitlines() if l != "(...)"][:4]:
        say("      " + ln)
    say(f"   story saved: {p}  ({story.nwords(script)} words)")
    return True


def write_horror_stories(ctx, n=3, topics=None, minutes=15, engine="auto", genre="bhay", level="standard"):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    made = 0
    for i in range(int(n)):
        if ctx["stopped"]():
            return False
        topic = (topics or [])[i] if topics and i < len(topics) else ""
        say(f"\n   --- story {i + 1}/{n}")
        try:
            script, info = story.write_genre(cfg, genre, say, topic, float(minutes), "", engine, None, ctx["stopped"], level)
        except Exception as e:
            say(f"   !! {e}"); continue
        if story.nwords(script) < 150:
            say("   (too short - skipped)"); continue
        summ = story.summarise(cfg, script, say)
        pre = {"bhay": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse", "romanch": "Romanch", "kareeb": "Kareeb",
               "gyaan": "Gyaan"}.get(genre, genre)
        name = f"{pre} - {slug(summ.get('title_en') or info['recipe']['topic'], 50)}"
        p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
        if os.path.exists(p):
            p = p[:-4] + time.strftime("_%H%M%S.txt")
        open(p, "w", encoding="utf-8").write(script)
        entry = {"name": os.path.basename(p)[:-4], "date": time.strftime("%Y-%m-%d %H:%M"),
                 "genre": "horror" if genre == "bhay" else genre,
                 "topic": info["recipe"]["topic"], "recipe": info["recipe"], "model": info["model"],
                 "words": story.nwords(script), "summary": summ, "status": "written (no voice yet)",
                 "level": level}
        story.add_ledger(cfg, entry)
        json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say(f"   saved: {os.path.basename(p)}  ({story.nwords(script)} words)  {summ.get('title_hi', '')}")
        made += 1
    say(f"\n   {made} stories in {os.path.join(ctx['media'], 'scripts')} - open one on the भय page")
    return made > 0


def _sfx_names(cfg):
    import sfx_lib
    return sfx_lib.library(cfg)


def _sfx_names_old(cfg):
    d = os.path.join(os.path.dirname(cfg.get("music_dir") or r"D:\Audio asset\BGM"), "SFX")
    out = {}
    for f in glob.glob(os.path.join(d, "*.mp3")) + glob.glob(os.path.join(d, "*.wav")):
        k = re.sub(r"^(freesound_community|dragon-studio|brvhrtz|bryansantosbreton|dreamwriter07|emg5991)-", "",
                   os.path.splitext(os.path.basename(f))[0].lower())
        k = re.sub(r"-\d{4,}$", "", k).replace("-", " ").strip()
        out[k] = f
    return out


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
AMB_WORDS = ("wind", "thunder", "rain", "cricket", "drips", "fan", "tubelight", "drone", "pads", "water", "heartbeat")
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
PICTURE_RULES = {     # what the picture-prompt AI may write; the painter (Z-Image) cannot read "no X" and paints X
    "standard": "Write only what IS visible: never put 'no', 'not' or 'without' in a prompt and never mention text, "
                "letters, signs or writing. Show the feeling through light, shadow, posture and wrong details; leave "
                "gore, wounds and blood out of the picture.",
    "mature": "Write only what IS visible: never put 'no', 'not' or 'without' in a prompt and never mention text, "
              "letters, signs or writing. This is for adults: blood, wounds and disturbing details may be shown "
              "plainly where the story has them.",
}


def _pic_rules(cfg):
    return PICTURE_RULES.get(str(cfg.get("_picture_level") or "standard"), PICTURE_RULES["standard"])


def _anchor_text(v):
    """a character anchor as plain words (some models answer {silhouette: .., clothing: .., prop: ..})"""
    if isinstance(v, dict):
        return ". ".join(str(x).strip() for x in v.values() if str(x or "").strip())
    if isinstance(v, (list, tuple)):
        return ". ".join(str(x).strip() for x in v if str(x or "").strip())
    return str(v or "").strip()


SCENE_SIMILAR_MAX = 0.42        # word overlap (0-1) above which a scene prompt counts as a repeat of an earlier one
SCENE_VARY_HINTS = ("wide establishing view of the place from outside, an empty scene",
                    "extreme close-up of a single object from this moment, shallow depth of field, the object alone",
                    "view through a half-open doorway into the space, nobody in the doorway",
                    "high angle top-down view", "low angle view looking up, empty foreground",
                    "a hand or footsteps only, the rest in darkness")
_PEOPLE = re.compile(r"\b(wom[ae]n|m[ae]n|boy|girl|child|character|figure|person|people|sari|saree|silhouette|"
                     r"he|she|his|her|face|eyes)\b", re.I)


def _look_for_prompt(look, max_words=40):
    """the shared look without people/props sentences (a look that names a person puts that person in every picture)"""
    out = []
    for x in re.split(r"(?<=[.!?])\s+", str(look or "")):
        x = x.strip()
        if not x or _PEOPLE.search(x):
            continue
        if out and len(" ".join(out + [x]).split()) > max_words:
            break
        out.append(x)
    return " ".join(out).rstrip(".")


def _overlap(a, b):
    stop = {"the", "and", "with", "from", "that", "this", "into", "over", "under", "their", "there", "only", "single",
            "dark", "dim", "light", "scene", "shot", "view", "close", "wide", "while", "which", "seems", "hangs"}
    wa = {w for w in re.findall(r"[a-z]{4,}", str(a).lower()) if w not in stop}
    wb = {w for w in re.findall(r"[a-z]{4,}", str(b).lower()) if w not in stop}
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def _vary_scene(cfg, say, desc, scene, earlier, text, i):
    """a rewrite with another subject when this scene repeats an earlier one (AI first, a fixed hint as fallback)"""
    import story
    try:
        t, _ = story.ask(cfg, SCENE_VARY_ASK.format(desc=desc, earlier=earlier[:500], prompt=scene[:500],
                                                    text=str(text)[:700], rules=_pic_rules(cfg)), "auto", 0.7, True, say=lambda *a: None)
        new = str(json.loads(t).get("prompt") or "").strip()
        if new and _overlap(new, earlier) < SCENE_SIMILAR_MAX:
            return new, "rewritten by the AI"
    except Exception:
        pass
    return f"{SCENE_VARY_HINTS[i % len(SCENE_VARY_HINTS)]}: {scene}", "given a different framing"


def _words_in(text, words):
    """True when these words are (nearly) spoken in this text"""
    import difflib
    norm = lambda t: re.sub(r"[^\w\s]", "", str(t)).split()
    w, tw = norm(words), norm(text)
    if not w or not tw:
        return False
    target = " ".join(w)
    return any(difflib.SequenceMatcher(None, target, " ".join(tw[j:j + len(w)])).ratio() >= 0.75
               for j in range(len(tw)))


# a bed whose file name names a sound source must have that source in the scene (story text or picture prompt):
# "crickets with birds" / "midnight train horn" / "river ambience" under a photo studio scene sounds wrong
AMB_SOURCES = {
    "bird": ("bird", "पक्षी", "चिड़िया", "चिडिया", "पंछी", "परिंद", "कौवा", "कौआ", "कोयल", "मोर", "dawn", "morning", "सुबह"),
    "train": ("train", "railway", "station", "ट्रेन", "रेल", "स्टेशन", "प्लेटफ़ॉर्म", "प्लेटफार्म"),
    "horn": ("horn", "हॉर्न", "train", "ट्रेन", "रेल", "traffic", "सड़क"),
    "river": ("river", "stream", "नदी", "धारा", "घाट", "नाला"),
    "sea": ("sea", "ocean", "beach", "समुद्र", "सागर", "लहर", "तट"),
    "wave": ("sea", "ocean", "beach", "wave", "समुद्र", "सागर", "लहर", "तट"),
    "dog": ("dog", "कुत्त"),
    "market": ("market", "bazaar", "crowd", "बाज़ार", "बाजार", "भीड़", "मेला", "मेले"),
    "crowd": ("market", "bazaar", "crowd", "बाज़ार", "बाजार", "भीड़", "मेला", "लोग"),
    "traffic": ("traffic", "road", "street", "सड़क", "गाड़ियों", "ट्रैफ़िक"),
    "temple": ("temple", "मंदिर", "आरती", "घंटी"),
    "church": ("church", "चर्च"),
    "kitchen": ("kitchen", "रसोई", "चूल्हा"),
    "hospital": ("hospital", "अस्पताल", "ward", "वार्ड"),
    "office": ("office", "दफ़्तर", "दफ्तर", "ऑफिस"),
    "cafe": ("cafe", "café", "कैफ़े", "कैफे", "ढाबा", "चाय की दुकान"),
    "village": ("village", "गाँव", "गांव", "खेत"),
    "forest": ("forest", "jungle", "woods", "जंगल"),
    "jungle": ("forest", "jungle", "woods", "जंगल"),
    "thunder": ("thunder", "storm", "lightning", "बिजली", "गरज", "तूफ़ान", "तूफान"),
    "rain": ("rain", "storm", "monsoon", "बारिश", "बरसात", "बूँद", "बूंद", "तूफ़ान", "तूफान"),
    "fire": ("fire", "flame", "bonfire", "आग", "अलाव", "लपट", "चिता", "चूल्हा"),
    "cricket": ("cricket", "night", "outside", "field", "झींगुर", "रात", "बाहर", "खेत"),
}


def _amb_fits(path, text):
    """False when the bed's file name names a sound source (bird, train, river...) that this scene never has"""
    n = os.path.basename(str(path or "")).lower()
    t = str(text or "").lower()
    return all(any(w in t for w in words) for src, words in AMB_SOURCES.items() if src in n)


def _fix_cue_words(cfg, text, words, sfx):
    """the planner wrote the cue words in another language (English for a Hindi story): ask for the exact words"""
    import story
    try:
        t, _ = story.ask(cfg, CUE_WORDS_ASK.format(sfx=os.path.splitext(os.path.basename(sfx))[0][:60], words=words,
                                                   text=str(text)[:900]), "auto", 0.2, True, say=lambda *a: None)
        w = str(json.loads(t).get("words") or "").strip()
        return w if w and _words_in(text, w) else ""
    except Exception:
        return ""


def sfx_fetch(ctx, genre=""):
    """top the sound effects library up for a series (Freesound, CC0 only, registered) - skipped without a key"""
    import sfx_lib, story
    key = story.secret("freesound_key")
    cats = sfx_lib.missing(ctx["cfg"], genre or None)
    if not cats:
        return ctx["say"]("   the sound effects library is complete for this series")
    if not key:
        return ctx["say"](f"   {len(cats)} kinds of sound effects missing - add a free Freesound key in Settings to fetch them")
    n = sfx_lib.fetch(ctx["cfg"], key, cats, ctx["say"])
    ctx["say"](f"   {n} new sound effects (CC0) - register: {os.path.join(sfx_lib.root(ctx['cfg']), 'SFX_INDEX.csv')}")
    return True


def _word_time(tim, words, a=None, b=None):
    """narration time (s) where these words are spoken (timing.json lines; position inside the line by word count)"""
    norm = lambda t: re.sub(r"[^\w\s]", "", str(t)).split()
    w = norm(words)
    if not w:
        return None
    for x in tim:
        if a is not None and (float(x["end"]) < float(a) - 1 or float(x["start"]) > float(b) + 1):
            continue
        tw = norm(x["text"])
        for j in range(len(tw)):
            if tw[j:j + len(w)] == w:
                return float(x["start"]) + (float(x["end"]) - float(x["start"])) * j / max(1, len(tw))
    return None


# ------------------------------------------------------------------ story-beat scenes + two-step director (Experimental, 16 Oct)
END_SECONDS = 10              # Experimental genre videos, 'Whole video' 0: music after the voice (Tinker: end_seconds)
SUBSCRIBE_END = {"dur": 6.5, "sub_click": 2.2, "bell_click": 3.8}   # Subscribe + bell ending (engine subscribe_end.py)
SCENE_SECONDS = 30            # Experimental genre videos: about one picture per this many seconds (Tinker: scene_seconds)
_SENT_END = re.compile(r"[।?!॥]\s*[\"'”’)]*\s*$")
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
# an English word in a picture prompt -> words that must be in the scene's own text (Hindi, or English loan words)
# for it to stay; weather may also come from the scene before or after
INVENTED = {
    "rain": ("बारिश", "बरसात", "बूँद", "बूंद", "भीग", "मानसून", "रेन"),
    "storm": ("तूफ़ान", "तूफान", "आंधी", "आँधी", "बारिश"),
    "lightning": ("बिजली", "कड़क", "तूफ़ान", "तूफान"),
    "thunder": ("बिजली", "गरज", "कड़क", "तूफ़ान", "तूफान"),
    "fog": ("कोहरा", "कोहरे", "धुंध", "कुहासा"), "mist": ("कोहरा", "कोहरे", "धुंध", "कुहासा"),
    "snow": ("बर्फ", "बर्फ़"),
    "moon": ("चाँद", "चांद", "चाँदनी", "चांदनी", "चंद्र", "पूर्णिमा"),
    "candle": ("मोमबत्ती", "दीया", "दीये", "दीपक", "दिया जल", "लौ"),
    "lantern": ("लालटेन", "दीया", "दीपक", "लैंप"),
    "bangle": ("चूड़ी", "चूड़ियाँ", "चूड़ियां", "कंगन"),
    "mirror": ("आईना", "आइना", "आईने", "शीशा", "शीशे", "दर्पण", "मिरर"),
    "doll": ("गुड़िया", "खिलौना", "खिलौने"),
    "clock": ("घड़ी", "घड़ियाँ", "टिक"),
    "letter": ("चिट्ठी", "पत्र", "ख़त", "खत", "लेटर"),
    "umbrella": ("छाता", "छतरी"),
    "knife": ("चाकू", "छुरी", "खंजर"),
    "blood": ("खून", "ख़ून", "रक्त", "लहू"),
    "skull": ("खोपड़ी", "कंकाल"), "skeleton": ("कंकाल", "हड्डी"),
    "crow": ("कौआ", "कौवा", "कौए"), "owl": ("उल्लू",), "cat": ("बिल्ली",), "dog": ("कुत्त",),
    "rose": ("गुलाब",), "flower": ("फूल", "गुलाब"), "flowers": ("फूल", "गुलाब"),
    "swing": ("झूला", "झूले"),
    "book": ("किताब", "पुस्तक", "डायरी", "कॉपी", "बुक"),
    "train": ("ट्रेन", "रेल", "स्टेशन"), "river": ("नदी", "घाट", "धारा"),
    "sea": ("समुद्र", "सागर", "लहर", "तट"), "ocean": ("समुद्र", "सागर"),
    "forest": ("जंगल", "वन"), "temple": ("मंदिर",),
    "graveyard": ("कब्र", "क़ब्र", "कब्रिस्तान", "श्मशान"), "cemetery": ("कब्र", "क़ब्र", "कब्रिस्तान", "श्मशान"),
}
WEATHER = {"rain", "storm", "lightning", "thunder", "fog", "mist", "snow", "moon"}


def _wre(w):
    return r"\b" + w + r"(s|es|y|light|lit|drops?)?\b"


def _beat_parts(narration, seconds=SCENE_SECONDS):
    """the story cut into scenes of about `seconds` that end where a sentence ends (।?!), never on a line that
    trails off with '...' -> [{text, start, end}]"""
    tm = json.load(open(os.path.join(narration, "timing.json"), encoding="utf-8"))
    if not tm:
        return []
    T = max(10.0, float(seconds))
    ends = [bool(_SENT_END.search(str(x["text"]).strip())) for x in tm]
    out, s, n = [], 0, len(tm)
    while s < n:
        best, cut = None, n - 1
        for i in range(s, n):
            dur = float(tm[i]["end"]) - float(tm[s]["start"])
            if ends[i] and dur >= 0.4 * T:
                best = i
            if ends[i] and dur >= 0.7 * T:
                cut = i
                break
            if dur >= 1.5 * T:                     # no sentence end for a long time: the last one, else this line
                cut = best if best is not None else i
                break
        out.append(tm[s:cut + 1])
        s = cut + 1
    if len(out) > 1 and float(out[-1][-1]["end"]) - float(out[-1][0]["start"]) < 0.4 * T:
        tail = out.pop()                           # a short tail joins the scene before it
        out[-1] += tail
    return [{"text": " ".join(str(c["text"]).strip() for c in p), "start": p[0]["start"], "end": p[-1]["end"]}
            for p in out]


def _invented(prompt, text, near=""):
    """the English things a picture prompt names that the scene's story text does not have (weather may come
    from the scene before or after)"""
    t, nb = str(text or "").lower(), str(near or "").lower()
    bad = []
    for w, ok in INVENTED.items():
        if not re.search(_wre(w), str(prompt or ""), re.I):
            continue
        hay = t + " " + nb if w in WEATHER else t
        if not any(k in hay for k in ok) and not re.search(r"\b" + w, t):
            bad.append(w)
    return bad


def _strip_words(prompt, bad):
    """drop the comma/sentence pieces of a prompt that name these words"""
    pieces = re.split(r"(?<=[,.;])\s+", str(prompt or ""))
    keep = [x for x in pieces if not any(re.search(_wre(w), x, re.I) for w in bad)]
    return " ".join(keep).strip(" ,;")


def _model_cfg(cfg, key):
    """(cfg for story.ask, engine): a model chosen on Tinker for this step always runs locally in Ollama"""
    m = str(cfg.get(key) or "").strip()
    return (dict(cfg, _llm_override=m), "local") if m else (cfg, "auto")


def _ask_step(cfg, key, prompt, temp, say=lambda *a: None):
    c, eng = _model_cfg(cfg, key)
    try:
        return story_ask(c, prompt, eng, temp, say)
    finally:
        if c is not cfg and c.get("_ollama_used"):
            cfg["_ollama_used"] = True


def story_ask(cfg, prompt, engine, temp, say):
    import story
    ctx_len = 16384 if len(prompt) > 9000 else 8192     # long stories cut into many scenes need a longer context
    try:
        t, m = story.ask(cfg, prompt, engine, temp, True, say=say, ctx_len=ctx_len)
    except TypeError:                                    # an older story.py without ctx_len
        t, m = story.ask(cfg, prompt, engine, temp, True, say=say)
    if not str(t or "").strip():                         # Gemma 4 sometimes answers nothing in JSON mode: ask once more as text
        try:
            t, m = story.ask(cfg, prompt, engine, temp, False, say=say, ctx_len=ctx_len)
        except TypeError:
            t, m = story.ask(cfg, prompt, engine, temp, False, say=say)
    return t, m


def _loads(t):
    """JSON from a model reply: plain, in ``` fences or with words around it (Gemma 4)"""
    t = re.sub(r"^```\w*\s*|\s*```$", "", str(t or "").strip())
    try:
        return json.loads(t)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("the model gave an empty reply" if not t else "no JSON in the model's reply: " + t[:120])


def _clean_scene(cfg, say, desc, i, prompt, happens, text, near):
    """a picture prompt with invented props or weather -> rewritten by the AI, else those pieces are cut out"""
    bad = _invented(prompt, text, near)
    if not bad:
        return prompt
    try:
        t, _ = _ask_step(cfg, "scene_picture_model", SCENE_FIX_ASK.format(
            desc=desc, bad=", ".join(bad), prompt=prompt[:600], happens=happens[:300], text=str(text)[:900]), 0.5)
        new = str(_loads(t).get("prompt") or "").strip()
        if new and not _invented(new, text, near):
            say(f"   scene {i + 1}: the picture added {', '.join(bad)} (not in this part of the story) - rewritten")
            return new
    except Exception:
        pass
    new = _strip_words(prompt, bad)
    if len(new.split()) < 8:
        hb = _invented(happens, text, near)
        new = (_strip_words(happens, hb) if hb else happens) or new
    say(f"   scene {i + 1}: the picture added {', '.join(bad)} (not in this part of the story) - removed")
    return new or prompt


def _picture_prompts(cfg, say, desc, parts, sc):
    """step 2 of the director: one picture prompt per scene, written from its 'what happens' line"""
    rows = []
    for i, p in enumerate(parts):
        x = sc[i] if i < len(sc) and isinstance(sc[i], dict) else {}
        hap = str(x.get("happens") or "").strip() or "(not given - read the text)"
        rows.append(f"{i + 1}. WHAT HAPPENS: {hap}\n   TEXT: {str(p['text'])[:900]}")
    try:
        t, model = _ask_step(cfg, "scene_picture_model",
                             SCENE_PICTURE_ASK.format(desc=desc, n=len(parts), scenes="\n".join(rows), rules=_pic_rules(cfg)), 0.6, say)
        pr = []
        for x in (_loads(t).get("prompts") or []):         # some models answer [{"prompt": "..."}] instead of ["..."]
            if isinstance(x, dict):
                x = x.get("prompt") or next((v for v in x.values() if isinstance(v, str)), "")
            pr.append(str(x or "").strip())
        say(f"   picture prompts written from each scene's 'what happens' line by {model}")
        return pr
    except Exception as e:
        say(f"   (picture prompts from the 'what happens' lines failed: {e} - kept the director's own prompts)")
        return []


def _set_aside_scenes(out, video, say):
    """'Plan the scenes again': the old plan, pictures, prompts and _alt go to <scenes>\\_old_<time> and the old
    video gets an _old_<time> name, so nothing is lost and the new run paints and renders fresh"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    keep = os.path.join(out, "_old_" + ts)
    moved = 0
    for f in os.listdir(out):
        if f.startswith("_old_"):
            continue
        os.makedirs(keep, exist_ok=True)
        shutil.move(os.path.join(out, f), os.path.join(keep, f))
        moved += 1
    if moved:
        say(f"   planning again: the old scenes ({moved} files/folders) moved to {keep}")
    if video and os.path.isfile(video):
        old = os.path.splitext(video)[0] + "_old_" + ts + ".mp4"
        os.replace(video, old)
        say(f"   the old video kept as {os.path.basename(old)}")


def horror_scenes(ctx, name, narration, out, count=0, genre="bhay", replan=False, video=""):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    if os.path.exists(os.path.join(out, "shots.json")):
        if not (ctx.get("replan") or replan):
            return say("   the scenes are already planned for this voice - kept (pictures that exist are kept too)")
        _set_aside_scenes(out, video, say)
    os.makedirs(out, exist_ok=True)
    tm = json.load(open(os.path.join(narration, "timing.json"), encoding="utf-8"))
    total = tm[-1]["end"] if tm else 600
    n = int(count) or max(8, min(18, round(total / 70)))
    # Experimental: scenes end at sentence ends, two-step director (hub jobs carry experimental_mode, tests the workspace)
    exp = bool(cfg.get("experimental_mode") or cfg.get("_experimental_workspace"))
    if exp and not int(count):
        try:
            secs = float(cfg.get("scene_seconds") or SCENE_SECONDS)
        except (TypeError, ValueError):
            secs = SCENE_SECONDS
        parts = _beat_parts(narration, secs)[:40] or _parts(narration, "", n)
        n = len(parts)
        say(f"   {n} scenes, each ending where a sentence ends (about {secs:g} s each)")
    else:
        parts = _parts(narration, "", n)
    import sfx_lib
    sfx = _sfx_names(cfg)
    amb = [k for k in sfx if sfx_lib.kind_of(cfg, sfx[k]) == "amb"]
    cue_names = [k for k in sfx if k not in amb]
    per_min = sfx_lib.SERIES.get(genre, ([], 0))[1]
    cpp = max(0, round(per_min * (total / max(1, n)) / 60))
    hits = [k for k in sfx if k not in amb and any(w in k for w in ("braam", "door", "drop", "whoosh", "sweep", "footsteps",
                                                                     "slam", "riser", "thunder", "bell", "sobbing"))]
    G = GENRES.get(genre, GENRES["bhay"])
    if not G.get("hits"):
        hits = []
    try:                                              # the story's level (mature allows blood in the pictures too)
        cfg["_picture_level"] = json.load(open(os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".story.json"),
                                               encoding="utf-8")).get("level") or "standard"
    except Exception:
        cfg["_picture_level"] = "standard"
    if cfg["_picture_level"] != "standard":
        say(f"   pictures follow the story's level: {cfg['_picture_level']}")
    ask = SCENES_ASK.format(desc=G["desc"], label=G["label"], amb=", ".join(amb) or "-", hits=", ".join(hits) or "-", n=len(parts),
                            cues=", ".join(cue_names) if cpp else "- (none for this series)", cpp=cpp,
                            mom=max(2, round(total / 150)), rules=_pic_rules(cfg),
                            parts="\n".join(f"{i + 1}. ({int(p['start'] // 60)}:{int(p['start'] % 60):02d}) {p['text'][:1200]}"
                                            for i, p in enumerate(parts)))
    cut = [i + 1 for i, p in enumerate(parts) if len(p["text"]) > 1200]
    if cut:
        say(f"   ⚠ scene(s) {', '.join(map(str, cut))} are longer than 1200 letters - the director only reads the start")
    d = {}
    try:
        t, model = _ask_step(cfg, "director_model", ask, 0.6, say)
        d = _loads(t)
        say(f"   planned by {model}")
    except Exception as e:
        say(f"   (the AI could not plan the scenes: {e} - simple pictures)")
    sc = d.get("scenes") or []
    if exp and len(sc) < len(parts):
        say(f"   ⚠ the director planned {len(sc)} of {len(parts)} scenes - the rest get pictures from their own text")
    two = _picture_prompts(cfg, say, G["desc"], parts, sc) if exp else []
    dcfg = _model_cfg(cfg, "director_model")[0]
    look = d.get("look", "")
    character_anchors = d.get("character_anchors") or {}
    if look:
        say(f"   the look of this story: {look}")
    shots, seen, fixed_cues, dropped_cues = [], [], 0, 0
    look_p = _look_for_prompt(look)
    if exp and look_p:
        whole = " ".join(p["text"] for p in parts)
        lb = [w for w in _invented(look_p, whole) if w in WEATHER]
        if lb:
            look_p = _strip_words(look_p, lb)
            say(f"   the look named {', '.join(lb)}, which this story never has - removed from the look")
    for i, p in enumerate(parts):
        x = sc[i] if i < len(sc) and isinstance(sc[i], dict) else {}
        happens = str(x.get("happens") or "").strip()
        own = x.get("prompt")
        if isinstance(own, dict):
            own = own.get("prompt") or next((v for v in own.values() if isinstance(v, str)), "")
        scene = str((two[i] if i < len(two) else "") or own or happens
                    or "a dark empty Indian village lane at night, one weak lamp, fog").strip()
        if exp:
            near = " ".join(parts[j]["text"] for j in (i - 1, i + 1) if 0 <= j < len(parts))
            scene = _clean_scene(cfg, say, G["desc"], i, scene, happens, p["text"], near)
        character = str(x.get("character") or "").strip()[:60]
        anchor = _anchor_text(character_anchors.get(character)) if character else ""
        sim = max(((_overlap(scene, e), e) for e in seen), default=(0, ""))
        if sim[0] >= SCENE_SIMILAR_MAX:                  # same subject as an earlier picture -> another subject
            scene, how = _vary_scene(dcfg, say, G["desc"], scene, sim[1], p["text"], i)
            say(f"   scene {i + 1} repeated an earlier picture ({sim[0]:.0%} the same words) - {how}")
        seen.append(scene)
        # the moment first (image models weigh the start most), the shared style after -> varied but coherent
        pr = f"{scene}. Setting and style: {look_p}" if look_p else scene
        if anchor:
            pr += f" Character continuity anchor: {anchor}. Keep this character design unchanged."
        cues = []
        for c in (x.get("cues") or []):
            if not (isinstance(c, dict) and str(c.get("sfx", "")).lower() in sfx and str(c.get("words", "")).strip()):
                continue
            w, f = str(c.get("words", "")), sfx[str(c.get("sfx", "")).lower()]
            if not _words_in(p["text"], w):              # e.g. "Rain" for a Hindi story: never found -> never placed
                w2 = _fix_cue_words(dcfg, p["text"], w, f)
                if not w2:
                    dropped_cues += 1
                    continue
                fixed_cues += 1
                w = w2
            cues.append({"words": w, "file": f})
            if len(cues) >= max(cpp, 0):
                break
        amb_f = sfx.get(str(x.get("ambience") or "").lower(), "")
        if amb_f and not _amb_fits(amb_f, p["text"] + " " + scene):
            prev = next((s["ambience"] for s in reversed(shots) if s.get("ambience")), "")
            keep = prev if prev and _amb_fits(prev, p["text"] + " " + scene) else ""
            say(f"   scene {i + 1}: bed {os.path.basename(amb_f)} names a sound this scene does not have - "
                + (f"kept the previous bed {os.path.basename(keep)}" if keep else "no bed (music only)"))
            amb_f = keep
        shots.append({"file": f"{i + 1:03d}.png", "prompt": pr, "scene_prompt": scene, "character_anchor": anchor,
                      "character": character, "happens": happens,
                      "text": p["text"], "start": p["start"],
                      "end": p["end"], "ambience": amb_f,
                      "hit": sfx.get(str(x.get("hit") or "").lower(), ""), "lightning": bool(x.get("lightning")),
                      "cues": cues})
        # Lead with the scene-specific text in logs; the shared look prefix made every
        # line appear identical when only the first 90 characters were printed.
        scene_preview = scene
        say(f"   {i + 1}: {scene_preview[:150]}{'…' if len(scene_preview) > 150 else ''}"
            f"{'  | ' + os.path.basename(shots[-1]['ambience']) if shots[-1]['ambience'] else ''}"
            f"{'  | ' + str(len(shots[-1]['cues'])) + ' cue(s)' if shots[-1]['cues'] else ''}"
            f"{'  | hit ' + os.path.basename(shots[-1]['hit']) if shots[-1]['hit'] else ''}")
    if fixed_cues or dropped_cues:
        say(f"   sound effect words checked against the narration: {fixed_cues} re-copied from the story text, "
            f"{dropped_cues} dropped (that sound does not happen there)")
    moments = []
    for i, p in enumerate(parts):                          # the object that carries the feeling, at its words
        x = sc[i] if i < len(sc) and isinstance(sc[i], dict) else {}
        mo = x.get("moment") if isinstance(x.get("moment"), dict) else {}
        if not mo.get("prompt") or not mo.get("words"):
            continue
        if exp:
            mb = _invented(mo["prompt"], p["text"])
            if mb:
                mo = dict(mo, prompt=_strip_words(mo["prompt"], mb))
                say(f"   close-up '{mo['words']}' named {', '.join(mb)} (not in this part) - removed")
                if len(str(mo["prompt"]).split()) < 6:
                    continue
        at = _word_time(tm, mo["words"], p["start"], p["end"])
        if at is None or at > total - 6:
            continue
        moments.append({"file": f"m{len(moments) + 1:02d}.png", "at": round(at, 2), "words": mo["words"],
                        "character": "",
                        "prompt": f"Close-up detail: {mo['prompt']}" + (f". Setting and style: {look_p}" if look_p else "")})
        say(f"   moment at {int(at // 60)}:{int(at % 60):02d} ('{mo['words']}'): {str(mo['prompt'])[:90]}")
    json.dump({"source": narration, "look": look, "shots": shots, "moments": moments},
              open(os.path.join(out, "shots.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True


def _horror_track(cfg, folders=("Dark", "Tension", "Eerie")):
    """a music bed from the series' mood folders in BGM, the least used one first"""
    md = cfg.get("music_dir") or ""
    cands = [f for sub in folders for f in glob.glob(os.path.join(md, sub, "*.mp3"))
             if "crate" not in f.lower()]
    up = os.path.join(cfg["media_dir"], "bhay_music_used.json")
    try:
        used = json.load(open(up, encoding="utf-8"))
    except Exception:
        used = {}
    if not cands:
        return ""
    skip = float(cfg.get("music_harsh_skip", 19))         # the most piercing tracks are never picked automatically
    ok = [f for f in cands if harshness(cfg, f) < skip] or cands
    t = min(ok, key=lambda f: (used.get(f, 0), random.random()))
    used[t] = used.get(t, 0) + 1
    json.dump(used, open(up, "w", encoding="utf-8"), indent=1)
    return t


def _usable_music(ctx, track, folders, music_db=None):
    """never a silent bed: a track you cannot hear under the voice is swapped; with none left, a softened library
    track (speech band dipped, 4 dB lower) with a clear warning in the job log"""
    cfg, say = ctx["cfg"], ctx["say"]

    def audible(t):
        try:
            import audio_screen
            r = audio_screen.screen(cfg, audio_screen.original_of(cfg, t), "music")
            vb = (r.get("m") or {}).get("voice_band")
            return (vb is None or vb >= float(audio_screen.LIMITS["music"].get("voice_band_min", 0))), vb
        except Exception:
            return True, None
    if track and os.path.exists(track):
        ok, vb = audible(track)
        if ok:
            return track, music_db
        say(f"   ⚠ WARNING: music '{os.path.basename(track)}' is almost only deep rumble ({(vb or 0) * 100:.1f}% above "
            "300 Hz) - it would sound like no music under your voice; picking another track")
        for _ in range(4):
            alt = _horror_track(cfg, folders)
            if alt and alt != track and os.path.exists(alt) and audible(alt)[0]:
                return alt, music_db
    say(f"   ⚠ WARNING: no usable music in the {', '.join(folders)} mood folders (add or rate tracks on the Sounds page)")
    fb = auto_track(cfg)
    if fb and os.path.exists(fb):
        try:
            import audio_screen
            fb = audio_screen.treated(cfg, fb, "music")
        except Exception:
            pass
        base = abs(float(music_db)) if music_db not in (None, "") else \
            abs(float((load_audio_index(cfg).get("shape") or {}).get("story_db", -18.0)))
        say(f"   ⚠ using '{os.path.basename(fb)}' from your library instead, softened and 4 dB lower")
        return fb, base + 4
    say("   ⚠ WARNING: the music library has no track at all - this episode has no background music")
    return track, music_db


def horror_sound(ctx, name, pictures, track="", genre="bhay", tail="1min", music_db=None):
    """music bed (shaped like your episodes, a bit lower) + per-scene ambience loops + rare hits -> narration_music.wav"""
    import numpy as np
    cfg, say = ctx["cfg"], ctx["say"]
    if _is_exp(cfg) and not str(tail).startswith("total:"):  # Experimental, 'Whole video' 0: the voice + a short ending
        tail = f"secs:{_end_seconds(cfg)}"
    folders = GENRES.get(genre, GENRES["bhay"])["music"]
    track = track or _horror_track(cfg, folders)
    track, music_db = _usable_music(ctx, track, folders, music_db)
    say(f"   music: {os.path.basename(track)}")
    _series_note(cfg, name, genre=genre, track=track, pictures=pictures)
    if not shape_mix(ctx, name, track, tail, music_db=music_db):
        return False
    d = narration_dir(cfg, name, existing=True)
    lead = float(json.load(open(os.path.join(d, "music_shape.json"), encoding="utf-8")).get("lead_s", 0))
    mix_p = os.path.join(d, "narration_music.wav")
    sr = 48000
    if not os.path.exists(os.path.join(pictures or "", "shots.json")):
        say("   (no scenes planned yet - music only; ambience + effects come with the video)")
        return True
    mix = _decode(cfg, mix_p, sr, 2).copy()
    shots = json.load(open(os.path.join(pictures, "shots.json"), encoding="utf-8"))["shots"]
    voice_db = 20 * np.log10(float(np.sqrt(np.mean(mix.astype(np.float64) ** 2))) + 1e-9)
    added = 0
    for i, s in enumerate(shots):
        a = int((lead + float(s["start"])) * sr)
        b = int((lead + float(shots[i + 1]["start"] if i + 1 < len(shots) else s["end"] + 4)) * sr)
        b = min(b, len(mix))
        if s.get("ambience") and os.path.exists(s["ambience"]) and b > a:
            amb = _decode(cfg, s["ambience"], sr, 2)
            if len(amb) > sr:
                need = b - a
                rep_ = np.concatenate([amb] * (need // len(amb) + 1))[:need]
                g = 10 ** ((voice_db - 22 - 20 * np.log10(float(np.sqrt(np.mean(amb ** 2))) + 1e-9)) / 20)
                env = np.minimum(1, np.minimum(np.arange(need) / (2 * sr), (need - np.arange(need)) / (2 * sr)))[:, None]
                mix[a:b] += (rep_ * g * env).astype(np.float32)
                added += 1
        if s.get("hit") and os.path.exists(s["hit"]) and i > 0:
            h = _decode(cfg, s["hit"], sr, 2)[: 6 * sr]
            g = 10 ** ((voice_db - 9 - 20 * np.log10(float(np.sqrt(np.mean(h ** 2))) + 1e-9)) / 20)
            st = max(0, a - int(0.3 * sr))
            e = min(len(mix), st + len(h))
            mix[st:e] += (h[:e - st] * g).astype(np.float32)
            added += 1
    tim = json.load(open(os.path.join(d, "timing.json"), encoding="utf-8")) if os.path.exists(os.path.join(d, "timing.json")) else []
    norm = lambda t: re.sub(r"[^\w\s]", "", str(t)).split()
    cue_db = {"bhay": -10, "rahasya": -11, "kisse": -13, "romanch": -15, "kareeb": -16}.get(genre, -14)
    used, ncue = [], 0
    for s in shots:
        for c in s.get("cues") or []:
            w = norm(c.get("words"))
            if not w or not os.path.exists(c.get("file", "")):
                continue
            at = c.get("at")                            # Experimental runs: already timed at the spoken word
            for x in ([] if at is not None else tim):   # the sentence that has these words, and where in it they are
                if float(x["end"]) < float(s["start"]) - 1 or float(x["start"]) > float(s["end"]) + 1:
                    continue
                tw = norm(x["text"])
                for j in range(len(tw)):
                    if tw[j:j + len(w)] == w:
                        at = float(x["start"]) + (float(x["end"]) - float(x["start"])) * j / max(1, len(tw))
                        break
                if at is not None:
                    break
            if at is None:
                continue
            h = _decode(cfg, c["file"], sr, 2)[: 8 * sr]
            if not len(h):
                continue
            g = 10 ** ((voice_db + cue_db - 20 * np.log10(float(np.sqrt(np.mean(h ** 2))) + 1e-9)) / 20)
            fade = np.minimum(1, (len(h) - np.arange(len(h))) / (0.4 * sr))[:, None]
            st = int((lead + at) * sr)
            e = min(len(mix), st + len(h))
            if e > st:
                mix[st:e] += (h[:e - st] * fade[:e - st] * g).astype(np.float32)
                used.append(c["file"]); ncue += 1
        used += [x for x in (s.get("ambience"), s.get("hit")) if x]
    if ncue:
        say(f"   {ncue} sound effects placed at the words where they happen")
    try:
        import sfx_lib
        sfx_lib.mark_used(cfg, used, name)
    except Exception:
        pass
    pk = float(np.abs(mix).max()) or 1
    raw = os.path.join(d, "_bhay.f32")
    open(raw, "wb").write((mix * min(1.0, 0.97 / pk)).astype("<f4").tobytes())
    os.replace(mix_p, os.path.join(d, "narration_music_plain.wav"))
    rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw,
                         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(sr), "-c:a", "pcm_s16le", mix_p])
    try:
        os.remove(raw)
    except OSError:
        pass
    say(f"   {added} ambience / sound effects placed -> {mix_p}")
    return rc == 0


def _subtitle_text_for_timing(text, timing):
    """Use one TXT line per timed phrase when counts match; otherwise split by original phrase size."""
    original = [str(x.get("text") or "") for x in timing]
    rows = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not rows:
        return original
    if len(rows) == len(timing):
        return rows
    words = str(text).split()
    weights = [max(1, len(str(x.get("text") or "").split())) for x in timing]
    if not words or not timing:
        return original
    result, offset = [], 0
    total_weight = sum(weights)
    for i, weight in enumerate(weights):
        remaining = len(words) - offset
        count = remaining if i == len(weights) - 1 else round(len(words) * weight / total_weight)
        if len(words) >= len(weights):
            count = max(1, count)
        count = min(remaining, count)
        result.append(" ".join(words[offset:offset + count]))
        offset += count
    if offset < len(words):
        result[-1] = (result[-1] + " " + " ".join(words[offset:])).strip()
    return result


def _is_exp(cfg):
    return bool(cfg.get("experimental_mode") or cfg.get("_experimental_workspace"))


def _end_seconds(cfg):
    try:
        return max(1.0, float(cfg.get("end_seconds") or END_SECONDS))
    except (TypeError, ValueError):
        return float(END_SECONDS)


def _subscribe_sounds(sr=48000):
    """a soft mouse click and a soft two-tone chime, made from sine waves (no sound file, nothing to license).
    Kept low and mellow (A5 then F5, few overtones, soft attack) so it is not piercing."""
    import numpy as np
    t = np.arange(int(0.04 * sr)) / sr
    click = np.random.default_rng(7).normal(0, 1, len(t)) * np.exp(-t / 0.002) * 0.25 \
        + np.sin(2 * np.pi * 1100 * t) * np.exp(-t / 0.006)
    t = np.arange(int(2.4 * sr)) / sr
    bell = np.zeros(len(t))
    for delay, f0, amp in ((0.0, 880.0, 1.0), (0.22, 698.5, 0.85)):           # A5 then F5: a soft ding-dong
        tt = t - delay
        on = tt >= 0
        for ratio, a, dec in ((1, 1, 1.3), (2.0, 0.18, 0.5), (3.0, 0.05, 0.25)):
            bell[on] += amp * a * np.sin(2 * np.pi * f0 * ratio * tt[on]) * np.exp(-tt[on] / dec) * np.minimum(1, tt[on] / 0.008)
    for _ in range(2):                                                          # gentle low-pass: rounds off the edge
        bell = np.convolve(bell, np.ones(5) / 5, mode="same")
    return (click / np.abs(click).max()).astype(np.float32), (bell / np.abs(bell).max()).astype(np.float32)


def _subscribe_end(ctx, narration, audio, total, lines):
    """Experimental: the Subscribe + bell ending over the last seconds of the video. Its click and bell sounds go into
    a copy of the audio (narration_music_end.wav) -> (plan entry, audio to use); (None, audio) when there is no room"""
    import wave
    import numpy as np
    cfg, say = ctx["cfg"], ctx["say"]
    S = dict(SUBSCRIBE_END)
    last = max([float(x["end"]) for x in lines] or [0.0])
    start = max(last + 2.4, total - S["dur"] - 0.6)          # near the very end; the picture fades out first (1.8 s)
    if start + S["dur"] > total + 0.05:
        say(f"   (no Subscribe ending: only {total - last:.1f} s after the voice; it needs about {S['dur'] + 1.2:.0f} s "
            f"- Tinker > Models > Ending after the voice)")
        return None, audio
    S["start"] = round(start, 3)
    sr = 48000
    try:
        a = _decode(cfg, audio, sr, 2).copy()
        click, bell = _subscribe_sounds(sr)
        for at, snd, gain in ((S["sub_click"], click, 0.12), (S["bell_click"], click, 0.12), (S["bell_click"] + 0.08, bell, 0.13)):
            i = int((start + at) * sr)
            j = min(len(a), i + len(snd))
            if j > i:
                a[i:j] += snd[:j - i, None] * gain
        pk = float(np.abs(a).max())
        if pk > 0.98:
            a *= 0.98 / pk
        outp = os.path.join(narration, "narration_music_end.wav")
        with wave.open(outp, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    except Exception as e:
        say(f"   (Subscribe sounds skipped: {e}; the animation still plays)")
        return S, audio
    say(f"   Subscribe + bell ending at {int(start // 60)}:{start % 60:04.1f} ({S['dur']:g} s)")
    return S, outp


def horror_video(ctx, name, pictures, narration, out, title="", genre="bhay", subtitle_text=""):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    _series_note(cfg, name, genre=genre, pictures=pictures, title=title)
    shots = json.load(open(os.path.join(pictures, "shots.json"), encoding="utf-8"))["shots"]
    lead = float((json.load(open(os.path.join(narration, "music_shape.json"), encoding="utf-8")) or {}).get("lead_s", 0)) \
        if os.path.exists(os.path.join(narration, "music_shape.json")) else 0.0
    audio = _audio_of(narration)
    total = _wav_len(audio)
    if not title:
        sj = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".story.json")
        title = ((json.load(open(sj, encoding="utf-8")).get("summary") or {}).get("title_hi", "")) if os.path.exists(sj) else ""
    scenes = []
    for i, s in enumerate(shots):
        f = os.path.join(pictures, s["file"])
        if not os.path.exists(f):
            continue
        st = 0.0 if not scenes else lead + float(s["start"])
        scenes.append({"image": f, "start": st, "end": (lead + float(shots[i + 1]["start"])) if i + 1 < len(shots) else total,
                       "flash": [lead + float(s["start"]) + 1.5] if s.get("lightning") else []})
    for mo in json.load(open(os.path.join(pictures, "shots.json"), encoding="utf-8")).get("moments") or []:
        f = os.path.join(pictures, mo["file"])
        t = lead + float(mo["at"]) - 0.4                   # the close-up comes just as the words are said
        host = next((x for x in scenes if x["start"] < t < x["end"] - 2), None)
        if not os.path.exists(f) or not host:
            continue
        dur = min(6.0, host["end"] - t)
        rest = {"image": host["image"], "start": t + dur, "end": host["end"], "flash": []}
        host["end"] = t
        scenes.append({"image": f, "start": t, "end": t + dur, "flash": [], "moment": True})
        if rest["end"] - rest["start"] >= 2.0:
            scenes.append(rest)
        else:
            scenes[-1]["end"] = rest["end"]
        say(f"   close-up {mo['file']} at {int(t // 60)}:{int(t % 60):02d}")
    scenes.sort(key=lambda x: x["start"])
    tm = json.load(open(os.path.join(narration, "timing.json"), encoding="utf-8"))
    texts = _subtitle_text_for_timing(subtitle_text, tm) if subtitle_text else [x["text"] for x in tm]
    lines = [{"start": x["start"] + lead, "end": x["end"] + lead, "text": texts[i]}
             for i, x in enumerate(tm) if i < len(texts) and texts[i].strip()]
    G = GENRES.get(genre, GENRES["bhay"])
    sub = None
    if _is_exp(cfg) and str(cfg.get("subscribe_end") or "on") != "off":
        sub, audio = _subscribe_end(ctx, narration, audio, total, lines)
    plan = {"audio": audio, "fps": 24, "title": title, "label": G["label"], "mood": G["mood"], "duration": total,
            "scenes": scenes, "lines": lines}
    if sub:
        plan["subscribe"] = sub
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pj = os.path.join(pictures, "video_plan.json")
    json.dump(plan, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ep = os.path.dirname(os.path.dirname(out))
    try:                                                   # the episode folder: script, title, thumbnail picture
        sp = os.path.join(narration, "script.txt")
        if os.path.exists(sp):
            shutil.copyfile(sp, os.path.join(ep, "script.txt"))
        open(os.path.join(ep, "title.txt"), "w", encoding="utf-8").write((title or name) + "\n")
        if scenes:
            shutil.copyfile(scenes[0]["image"], os.path.join(ep, os.path.basename(ep) + ".png"))
    except OSError:
        pass
    rc = ctx["run_cmd"]([cfg["studio_python"], "-u", "-s", os.path.join(cfg["studio_dir"], "engine", "horror_video.py"),
                         pj, out], cwd=cfg["studio_dir"])
    if rc == 0:
        say(f"   video: {out}")
    return rc == 0












































def genre_reels(ctx, name, pictures, out, count=2, length=30, genre="bhay"):
    """9:16 reels from a series video: the AI picks the strongest spoken lines, each reel starts just before one
    (same pictures, camera, sound and subtitles, drawn upright), the line itself is the hook on top"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    pj = os.path.join(pictures, "video_plan.json")
    if not os.path.exists(pj):
        raise RuntimeError("make the video first")
    plan = json.load(open(pj, encoding="utf-8"))
    lines = [l for l in plan["lines"] if 4 < len(l["text"].split()) <= 30 and l["start"] > 15
             and l["start"] < plan["duration"] - length - 5]
    if not lines:
        say("   the story is too short for reels"); return True
    n = max(1, min(6, int(count)))
    pick = []
    try:
        t, _ = story.ask(cfg, f"These are spoken lines of a {GENRES[genre]['desc']}. Pick the {n} lines that would "
                              "make someone scrolling at night stop and listen (strong, complete, intriguing, far apart). "
                              'Answer ONLY JSON: {"best": [line numbers]}\n\n'
                              + "\n".join(f"{i}. {l['text'][:200]}" for i, l in enumerate(lines)), "auto", 0.3, True, say=say)
        pick = [int(i) for i in json.loads(t).get("best", []) if 0 <= int(i) < len(lines)]
    except Exception as e:
        say(f"   (AI choice skipped: {e})")
    if len(pick) < n:
        pick += [round(len(lines) * (k + 1) / (n + 1)) for k in range(n)]
    chosen = []
    for i in pick:
        if all(abs(lines[i]["start"] - lines[j]["start"]) > length for j in chosen):
            chosen.append(i)
        if len(chosen) >= n:
            break
    os.makedirs(out, exist_ok=True)
    ok = True
    for k, i in enumerate(sorted(chosen, key=lambda i: lines[i]["start"])):
        l = lines[i]
        a = max(0.0, l["start"] - 1.0)
        hook = re.sub(r"\.{2,}|…", " ", l["text"]).strip(" .।")
        hook = " ".join(hook.split()[:9]) + ("…" if len(hook.split()) > 9 else "")
        rp = dict(plan, window=[a, min(plan["duration"], a + float(length))], size=[1080, 1920], title=hook)
        rj = os.path.join(pictures, f"reel_{k + 1}.json")
        json.dump(rp, open(rj, "w", encoding="utf-8"), ensure_ascii=False)
        f = os.path.join(out, f"reel{k + 1}_{slug(hook.split()[0] if hook else 'reel', 20)}.mp4")
        if os.path.exists(f):
            os.replace(f, os.path.join(out, time.strftime("_old_%H%M%S_") + os.path.basename(f)))
        say(f"   reel {k + 1}: {int(a // 60)}:{int(a % 60):02d}  {hook}")
        ok = ctx["run_cmd"]([cfg["studio_python"], "-u", "-s", os.path.join(cfg["studio_dir"], "engine", "horror_video.py"),
                             rj, f], cwd=cfg["studio_dir"]) == 0 and ok
    return ok












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
