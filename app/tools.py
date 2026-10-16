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
                     "lamp glow, gentle moonlight, cozy and peaceful, soft focus, simple composition, no text, no letters"),
    "silhouette": ("Silhouette art",
                   "flat silhouette art illustration, every shape a solid pure-black silhouette with crisp clean edges "
                   "against a smooth glowing gradient sky, minimal, calm, no faces, no text"),
    "watercolor": ("Soft watercolour",
                   "soft watercolor illustration, pastel colours, gentle paper texture, airy and calm, no text"),
    "cinematic": ("Cinematic still",
                  "cinematic film still, 35mm photograph, soft natural light, shallow depth of field, calm mood, no text"),
    "bhay": ("भय horror (dark, silhouettes)",
             "dark atmospheric Indian horror digital painting, cinematic, deep shadows, cold moonlight blue and dim "
             "sickly green tones, thin fog, a single weak warm light source, figures obscured by darkness when the "
             "scene calls for them, varied camera angles and shot sizes, no repeated rear-view figure, no gore, no blood, "
             "no text, no letters, eerie and quiet"),
    "rahasya": ("रहस्य thriller (noir)",
                "cinematic Indian thriller film still, moody noir lighting, deep shadows and one hard light source, "
                "muted teal and amber, rain-wet surfaces, mystery, scene-specific framing and varied camera angles; "
                "people appear only when the story calls for them, not always from behind, no text"),
    "kisse": ("किस्से emotional (warm)",
              "warm cinematic Indian slice-of-life painting, soft golden evening light, nostalgic film colours, "
              "gentle and emotional, people seen from behind or far, no clear faces, no text"),
    "romanch": ("रोमांच romance (warm, tasteful)",
                "romantic cinematic painting, warm candle and golden-hour light, soft bokeh, rain or city lights, two "
                "silhouettes close together seen from behind, tasteful and elegant, no nudity, no faces, no text"),
    "kareeb": ("करीब ASMR (soft, close)",
               "soft dreamy close-up still life, cosy night, warm lamp, gentle textures (fabric, tea cup, rain on "
               "glass, pages), shallow depth of field, very calm, no people, no text"),
    "gyaan": ("ज्ञान knowledge (clean illustration)",
              "clean modern editorial illustration, simple shapes, calm deep blue and warm accent colours, clear "
              "subject in the centre, informative, no text, no letters"),
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


def translate(cfg, text):
    text = (text or "").strip()
    if not text:
        return ""
    out = model_chat(cfg, cfg["llm"], TRANSLATE_ASK + text, 0.3, timeout=900).strip()
    out = re.sub(r"^```\w*\s*|\s*```$", "", out).strip()
    if not re.search("[ऀ-ॿ]", out):
        raise RuntimeError("the AI did not answer in Hindi - try again")
    return out


def llm_translate(ctx, name):
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    en = open(p, encoding="utf-8").read()
    if len(re.findall("[ऀ-ॿ]", en)) > len(re.findall("[A-Za-z]", en)):
        ctx["say"]("   the script is already Hindi - nothing to translate"); return
    ctx["say"](f"   asking {ctx['cfg']['llm']} (Ollama) ...")
    hi = translate(ctx["cfg"], en)
    open(p[:-4] + ".en.txt", "w", encoding="utf-8").write(en)
    open(p, "w", encoding="utf-8").write(hi)
    for ln in hi.splitlines()[:12]:
        ctx["say"]("   " + ln)
    ctx["say"](f"   (English original kept: {os.path.basename(p[:-4])}.en.txt)")


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
SUKOON_WORDS = ("sukoon", "bachpan", "moonlit", "headphone")
VISION_ASK = (
    "You are a visual style analyst. Look at this YouTube thumbnail and answer ONLY as JSON with keys: "
    '"palette" (main colours), "lighting", "mood", "setting", "composition", "art_style" (e.g. cinematic photo, '
    'digital painting), "subject" (who/what is the main subject), "style_prompt" (ONE English sentence of 25-40 '
    "words that describes ONLY the look - colours, light, atmosphere, rendering, camera feel - NOT the subject).")


def index_path(cfg):
    return os.path.join(cfg["media_dir"], "style_index.json")


def load_index(cfg):
    try:
        return json.load(open(index_path(cfg), encoding="utf-8"))
    except Exception:
        return {"items": [], "themes": {}}


def series_of(path, cfg=None):
    n = os.path.basename(path).lower()
    ep = os.path.normcase(os.path.abspath((cfg or {}).get("episodes_dir") or "\0"))
    if os.path.normcase(os.path.abspath(path)).startswith(ep + os.sep):
        return "sukoon"                                    # HubGPT Video episodes = Sukoon
    return "sukoon" if any(w in n for w in SUKOON_WORDS) else "kisse"


def thumb_sources(cfg):
    out, seen = [], set()
    dirs = [cfg.get("artwork_dir") or ""] + glob.glob(os.path.join(cfg.get("episodes_dir") or "", "*"))
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*"))):
            if not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            n = os.path.basename(f).lower()
            if any(x in n for x in ("logo", "pfp", "banner", "certificate", "caption", "_test")):
                continue
            key = (n, os.path.getsize(f))
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
    return out


def look_text(cfg, style, ref=""):
    """style words for a picture: a preset, the common look of your Sukoon / Kisse thumbnails, or one thumbnail"""
    if ref:
        it = next((x for x in load_index(cfg).get("items", []) if x.get("file") == ref), None)
        if it and it.get("style_prompt"):
            return it["style_prompt"]
    if style in THEMES:
        t = load_index(cfg).get("themes", {}).get(style.split("_")[0])
        if t:
            return t
        style = "sukoon_night" if style == "sukoon_theme" else "silhouette"
    return STYLES.get(style, STYLES["sukoon_night"])[1]


def style_index(ctx, rebuild=False):
    """ONE time (later only new pictures): every thumbnail is looked at by the vision AI -> its colours, light,
    mood, setting, composition + a reusable style sentence; then one common look per series."""
    cfg, say = ctx["cfg"], ctx["say"]
    idx = {"items": [], "themes": {}} if rebuild else load_index(cfg)
    have = {x["file"]: x for x in idx.get("items", []) if os.path.exists(x["file"])}
    files = thumb_sources(cfg)
    todo = [f for f in files if f not in have]
    say(f"   {len(files)} thumbnails found, {len(todo)} new to look at (vision model {cfg.get('vision_llm')})")
    tdir = os.path.join(cfg["media_dir"], "style_index")
    os.makedirs(tdir, exist_ok=True)
    for k, f in enumerate(todo):
        if ctx["stopped"]():
            break
        small = os.path.join(tdir, f"{abs(hash(f)) % 10 ** 10}.jpg")
        rc = subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", f, "-vf", "scale=768:-2", "-q:v", "4", small],
                            capture_output=True, creationflags=0x08000000 if WIN else 0).returncode
        if rc or not os.path.exists(small):
            say(f"   (could not read {os.path.basename(f)})"); continue
        try:
            d = json.loads(model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), VISION_ASK,
                                           0.2, as_json=True, image_path=small, timeout=600) or "{}")
        except Exception as e:
            say(f"   (vision AI could not answer for {os.path.basename(f)}: {e})"); continue
        item = {"file": f, "thumb": small, "series": series_of(f, cfg), "name": os.path.splitext(os.path.basename(f))[0]}
        for key in ("palette", "lighting", "mood", "setting", "composition", "art_style", "subject", "style_prompt"):
            v = d.get(key, "")
            item[key] = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        have[f] = item
        say(f"   {k + 1}/{len(todo)}  {item['name']} [{item['series']}]: {item['style_prompt'][:110]}")
    idx["items"] = sorted(have.values(), key=lambda x: (x["series"], x["name"].lower()))
    for ser in ("sukoon", "kisse"):                       # one common look per series
        sp = [x["style_prompt"] for x in idx["items"] if x["series"] == ser and x.get("style_prompt")]
        if not sp:
            continue
        ask = ("Here are style descriptions of thumbnails from ONE YouTube series. Write ONE English sentence of "
               "35-55 words describing their COMMON look (colours, light, atmosphere, rendering) to use as an image "
               "prompt suffix. No subjects, no names. Answer with the sentence only.\n\n" + "\n".join("- " + x for x in sp[:40]))
        try:
            idx.setdefault("themes", {})[ser] = model_chat(cfg, cfg["llm"], ask, 0.3, timeout=600).strip().strip('"')
            say(f"   common {ser} look: {idx['themes'][ser]}")
        except Exception as e:
            say(f"   (could not sum up the {ser} look: {e})")
    idx["built"] = time.strftime("%Y-%m-%d %H:%M")
    json.dump(idx, open(index_path(cfg), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(f"   index: {len(idx['items'])} thumbnails -> {index_path(cfg)}")


# ------------------------------------------------------------------ your music: index + the Sukoon music shape
def audio_index_path(cfg):
    return os.path.join(cfg["media_dir"], "audio_index.json")


def load_audio_index(cfg):
    try:
        return json.load(open(audio_index_path(cfg), encoding="utf-8"))
    except Exception:
        return {}


def audio_index(ctx, rebuild=False):
    cfg = ctx["cfg"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [cfg["gpu_python"], "-s", "-u", os.path.join(root, "app", "audio_index.py"), os.path.join(root, "config.json")]
    if rebuild:
        cmd.append("--rebuild")
    return ctx["run_cmd"](cmd, cwd=os.path.join(root, "app")) == 0


def auto_track(cfg):
    """the music you used most in your Sukoon episodes (if it is in BGM), else the calmest Calm track"""
    ai = load_audio_index(cfg)
    used = (ai.get("shape") or {}).get("used_tracks") or {}
    best = [f for f, _ in sorted(used.items(), key=lambda kv: -kv[1]) if os.path.exists(f)]
    if best:
        return best[0]
    calm = sorted([t for t in ai.get("tracks", []) if t.get("kind") == "music" and t.get("mood", "").lower() == "calm"
                   and os.path.exists(t["file"])], key=lambda t: -t.get("calm", 0))
    return calm[0]["file"] if calm else ""


def harshness(cfg, track):
    """how piercing a track is: loudest short peaks in the 2-6 kHz band (sharp piano / bell notes) above their usual
    level, in dB. Measured once per track (music_harsh.json in the hub media folder)."""
    import numpy as np
    cp = os.path.join(cfg["media_dir"], "music_harsh.json")
    try:
        cache = json.load(open(cp, encoding="utf-8"))
    except Exception:
        cache = {}
    k = f"{track}|{int(os.path.getmtime(track)) if os.path.exists(track) else 0}"
    if k in cache:
        return cache[k]
    p = subprocess.run([cfg["ffmpeg"], "-v", "error", "-i", track, "-t", "600", "-ac", "1", "-ar", "16000", "-af",
                        "highpass=f=2000,lowpass=f=6000", "-f", "f32le", "-"], capture_output=True,
                       creationflags=0x08000000 if WIN else 0)
    a = np.frombuffer(p.stdout, np.float32)
    if len(a) < 16000:
        return 0.0
    fr = a[: len(a) // 800 * 800].reshape(-1, 800)                       # 50 ms frames
    e = 10 * np.log10(np.mean(fr ** 2, axis=1) + 1e-12)
    loud = e[e > np.percentile(e, 20)]
    v = round(float(np.percentile(loud, 99.5) - np.median(loud)), 1) if len(loud) > 20 else 0.0
    cache[k] = v
    json.dump(cache, open(cp, "w", encoding="utf-8"), indent=1)
    return v


def tamed(ctx, track):
    """a piercing track gets its sharp notes softened (dip at 2.5-5 kHz + a fast compressor on the peaks), the way
    you lower those envelopes by hand; the original is never changed (copy in the hub's _tmp\\tamed)"""
    cfg, say = ctx["cfg"], ctx["say"]
    h = harshness(cfg, track)
    if h < float(cfg.get("music_harsh_tame", 14)):
        return track
    d = os.path.join(cfg["temporary_dir"], "tamed")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(track))[0])[:60] + ".wav")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(track):
        subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", track, "-af",
                        "equalizer=f=3200:t=q:w=1.1:g=-5,equalizer=f=5500:t=q:w=1:g=-3,"
                        "acompressor=threshold=0.08:ratio=5:attack=1:release=90:makeup=1.6", "-ar", "48000", out],
                       capture_output=True, creationflags=0x08000000 if WIN else 0)
    say(f"   music has sharp notes (harshness {h:.0f} dB) - softened them for this video")
    return out if os.path.exists(out) else track


def _decode(cfg, path, sr=48000, ch=1):
    p = subprocess.run([cfg["ffmpeg"], "-v", "error", "-i", path, "-ac", str(ch), "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, creationflags=0x08000000 if WIN else 0)
    import numpy as np
    a = np.frombuffer(p.stdout, np.float32)
    return a.reshape(-1, ch) if ch > 1 else a


def shape_mix(ctx, name, track="", tail="1min", music_db=None):
    """your voice + music the way your Sukoon episodes do it: the music starts alone, sits under the story at your
    usual level, comes back up after you stop and plays on; smooth ramps. -> narration_music.wav (+ music_shape.json)"""
    import numpy as np
    cfg, say = ctx["cfg"], ctx["say"]
    d = narration_dir(cfg, name, existing=True)
    vp = os.path.join(d, "narration.wav")
    if not os.path.exists(vp):
        raise RuntimeError("no narration.wav to put music under")
    track = track if track and track not in ("auto", "none") else auto_track(cfg)
    if not track or not os.path.exists(track):
        raise RuntimeError("no music track found - build the audio index or choose a track")
    sh = dict(load_audio_index(cfg).get("shape") or {})
    lead = float(np.clip(sh.get("lead_s", 4.0), 2, 15))
    fade_in = float(np.clip(sh.get("fade_in_s", 2.0), 0.5, lead))
    story = float(sh.get("story_db", -18.0)) if music_db in (None, "") else -abs(float(music_db))
    intro = float(np.clip(sh.get("intro_db", story + 4), story - 4, story + 10))
    outro = float(np.clip(sh.get("outro_db", story + 3), story - 4, story + 10))
    tails = {"1min": 60.0, "5min": 300.0, "usual": float(np.clip(sh.get("tail_s", 60), 30, 3600))}
    tail_s = tails.get(tail, 60.0)
    fade_out = float(np.clip(sh.get("fade_out_s", 8.0), 4, tail_s * 0.6))
    sr = 48000
    v = _decode(cfg, vp, sr, 1)
    if str(tail).startswith("total:"):                     # fill up to a total length (e.g. a 30 min episode)
        tail_s = max(60.0, float(tail[6:]) * 60 - lead - len(v) / sr)
        fade_out = float(np.clip(sh.get("fade_out_s", 8.0), 4, tail_s * 0.6))
    say(f"   music: {os.path.basename(track)}  | starts {lead:.1f} s before you, {intro:.0f} dB -> {story:.0f} dB under "
        f"your voice, back to {outro:.0f} dB after you stop, {tail_s / 60:.0f} min more")
    n = int((lead + len(v) / sr + tail_s) * sr)
    m = _decode(cfg, tamed(ctx, track), sr, 2)
    if len(m) < sr * 4:
        raise RuntimeError("the music track could not be read")
    k = min(int(3 * sr), len(m) // 3)                                      # loop with 3 s crossfades
    P = len(m) - k
    w_in = np.linspace(0, 1, k, dtype=np.float32)[:, None]

    def music(a0, a1):                                                      # samples a0..a1 of the endless loop
        pos = np.arange(a0, a1)
        q, o = pos // P, pos % P
        out = m[o].copy()
        blend = (q > 0) & (o < k)
        if blend.any():
            ob = o[blend]
            out[blend] = m[ob] * w_in[ob] + m[P + ob] * (1 - w_in[ob])
        return out

    hop = sr // 2                                                          # levels in dB (0.5 s frames)
    fr = v[: len(v) // hop * hop].reshape(-1, hop)
    vdb = 10 * np.log10((fr.astype(np.float64) ** 2).mean(1) + 1e-12)
    vdb_act = float(np.median(vdb[vdb > np.percentile(vdb, 95) - 18]))
    mf = m[: len(m) // hop * hop].mean(1).reshape(-1, hop)
    mdb = 10 * np.log10((mf.astype(np.float64) ** 2).mean(1) + 1e-12)
    mdb_act = float(np.median(mdb[mdb > np.percentile(mdb, 95) - 25]))
    g = lambda rel: 10 ** ((vdb_act + rel - mdb_act) / 20)                  # gain that puts music at rel dB
    speech = vdb > np.percentile(vdb, 95) - 18
    vs = lead + float(np.argmax(speech)) * 0.5
    ve = lead + (len(speech) - float(np.argmax(speech[::-1]))) * 0.5
    end = n / sr
    pts = [(0, 0), (fade_in, g(intro)), (vs, g(intro)), (vs + 3, g(story)), (ve, g(story)), (ve + 4, g(outro)),
           (max(ve + 4, end - fade_out), g(outro)), (end, 0)]
    px, py = [p[0] for p in pts], [p[1] for p in pts]
    a_v = int(lead * sr)
    raw = os.path.join(d, "_shape_mix.f32")
    peak = 0.0
    with open(raw, "wb") as fo:                                             # one minute at a time (low memory)
        for c0 in range(0, n, 60 * sr):
            c1 = min(n, c0 + 60 * sr)
            ch = music(c0, c1) * np.interp(np.arange(c0, c1) / sr, px, py).astype(np.float32)[:, None]
            v0, v1 = max(c0, a_v), min(c1, a_v + len(v))
            if v1 > v0:
                ch[v0 - c0:v1 - c0] += v[v0 - a_v:v1 - a_v, None]
            peak = max(peak, float(np.abs(ch).max()))
            fo.write(ch.astype("<f4").tobytes())
    outp = os.path.join(d, "narration_music.wav")
    if os.path.exists(outp):
        os.replace(outp, os.path.join(d, time.strftime("narration_music_old_%H%M%S.wav")))
    vol = f"volume={0.98 / peak:.4f}," if peak > 0.98 else ""
    rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw,
                         "-af", vol + "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(sr), "-c:a", "pcm_s16le", outp])
    try:
        os.remove(raw)
    except OSError:
        pass
    json.dump({"lead_s": lead, "track": track, "intro_db": intro, "story_db": story, "outro_db": outro,
               "tail_s": tail_s}, open(os.path.join(d, "music_shape.json"), "w", encoding="utf-8"), indent=1)
    if rc == 0:
        say(f"   -> {outp}  ({end / 60:.1f} min)")
    return rc == 0


# ------------------------------------------------------------------ unique episode names and live job path updates
def unique_genre_name(cfg, media, genre, requested):
    """Reserve a non-colliding name across the episode's script, voice, pictures and episode folders."""
    base = slug(requested)
    prefix = {"bhay": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse", "romanch": "Romanch",
              "kareeb": "Kareeb", "gyaan": "Gyaan"}.get(genre, str(genre).title())
    if not base.lower().startswith(prefix.lower() + " - "):
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



# ------------------------------------------------------------------ the AI writes a Sukoon script (see story.py)
def _clean_script(text):
    import story
    return story.clean(text)


def llm_story(ctx, name, topic="", minutes=12, notes="", subscribe=True, engine="auto", recipe=None, category="",
              lang="hi"):
    """topic (or none: the hub picks one) -> a fresh Sukoon script; ledger keeps what it used."""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    script, info = story.write(cfg, say, topic, float(minutes or 12), notes, subscribe, engine, recipe,
                               stopped=ctx["stopped"], category=category, lang=lang)
    if ctx["stopped"]():
        return False
    if story.nwords(script) < 150:
        raise RuntimeError("the script came out too short - try again")
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        os.replace(p, p[:-4] + time.strftime("_old_%H%M%S.txt"))
    open(p, "w", encoding="utf-8").write(script)
    summ = story.summarise(cfg, script, say)
    entry = {"name": name, "date": time.strftime("%Y-%m-%d %H:%M"), "topic": info["recipe"]["topic"], "lang": lang,
             "recipe": info["recipe"], "model": info["model"], "words": story.nwords(script), "summary": summ}
    story.add_ledger(cfg, entry)
    json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if summ.get("title_hi"):
        say(f"   title: {summ.get('title_hi')}  /  {summ.get('title_en', '')}")
    for ln in [l for l in script.splitlines() if l != "(...)"][:4]:
        say("      " + ln)
    say(f"   script saved: {p}  ({story.nwords(script)} words, ~{story.estimate(cfg, script)['minutes']:.0f} min) "
        "- edit it any time in Library > Scripts")
    return True


MIN_TAIL = 1.5                                                # minutes of music after the voice, at least


def fit_script(ctx, name, total_min, engine="auto", story_mode=False):
    """the video is shorter than the speech -> the AI shortens the script (story kept); the full one is kept"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    text = open(p, encoding="utf-8").read()
    est = story.estimate(cfg, text)
    cap = float(total_min) - MIN_TAIL
    say(f"   script: {est['words']} words ≈ {est['minutes']} min of your voice ({est['wpm']} words/min) | video "
        f"{float(total_min):.0f} min -> speech up to {cap:.1f} min")
    if est["minutes"] <= cap + 0.2:
        say("   fits - nothing to shorten"); return True
    full = p[:-4] + "_full.txt"
    if not os.path.exists(full):
        open(full, "w", encoding="utf-8").write(text)
    new = story.shorten(cfg, text, cap, engine, say, story_mode)
    e2 = story.estimate(cfg, new)
    if e2["minutes"] > cap + 0.2:
        say(f"   still too long at ~{e2['minutes']} min; kept the original script and stopped before narration")
        return False
    open(p, "w", encoding="utf-8").write(new)
    say(f"   now {e2['words']} words ≈ {e2['minutes']} min (the full script is kept: {os.path.basename(full)})")
    return True


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


def _queue_fetch(ctx, kinds, why):
    import elements_auto
    miss = elements_auto.missing(ctx["cfg"], kinds)
    have = [k for k in kinds if k not in miss]
    if have:
        ctx["say"](f"   ready in the library: {', '.join(have)}")
    if not miss:
        return
    if not __import__("story").secret("pixabay_key"):
        ctx["say"](f"   missing: {', '.join(miss)} - add a Pixabay key in Settings so the hub can fetch them"); return
    if ctx.get("add_job"):
        ctx["add_job"]("elements_fetch", "Fetch elements: " + ", ".join(miss),
                       [{"fn": "elements_fetch", "label": "find + download + register + loop new element clips",
                         "args": {"kinds": miss}}])
        ctx["say"](f"   missing: {', '.join(miss)} -> fetching in the background ({why})")


def elements_check(ctx, name="", art=""):
    import elements_auto
    text = ""
    sp = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    if os.path.exists(sp):
        text += open(sp, encoding="utf-8").read()
    sj = os.path.join(art or "", "shots.json")
    if art and os.path.exists(sj):
        text += "\n" + " ".join(x.get("prompt", "") for x in json.load(open(sj, encoding="utf-8")).get("shots", []))
    kinds = elements_auto.needs(text)
    ctx["say"](f"   this episode mentions: {', '.join(kinds) or 'no moving elements'}")
    if kinds:
        _queue_fetch(ctx, kinds, "for this and the next episodes")
    return True


def elements_fetch(ctx, kinds=None):
    import elements_auto
    n = elements_auto.fetch(ctx["cfg"], kinds or [], ctx["say"], ctx["cfg"].get("studio_python"))
    ctx["say"](f"   {n} new clip(s); register: {os.path.join(elements_auto.lib_dir(ctx['cfg']), 'ELEMENTS_INDEX.csv')}")
    return True


def write_stories(ctx, n=5, topics=None, minutes=12, engine="auto", category="", lang="hi"):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    made = 0
    for i in range(int(n)):
        if ctx["stopped"]():
            return False
        topic = (topics or [])[i] if topics and i < len(topics) else ""
        say(f"\n   --- script {i + 1}/{n}")
        try:
            script, info = story.write(cfg, say, topic, float(minutes), "", True, engine, None, stopped=ctx["stopped"],
                                       category=category, lang=lang)
        except Exception as e:
            say(f"   !! {e}"); continue
        if story.nwords(script) < 150:
            say("   (too short - skipped)"); continue
        summ = story.summarise(cfg, script, say)
        title = summ.get("title_en") or info["recipe"]["topic"]
        name = f"Sukoon {'EN ' if lang == 'en' else ''}- {slug(title, 50)}"
        p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
        if os.path.exists(p):
            p = p[:-4] + time.strftime("_%H%M%S.txt")
        open(p, "w", encoding="utf-8").write(script)
        entry = {"name": os.path.basename(p)[:-4], "date": time.strftime("%Y-%m-%d %H:%M"), "topic": info["recipe"]["topic"],
                 "lang": lang,
                 "recipe": info["recipe"], "model": info["model"], "words": story.nwords(script), "summary": summ,
                 "status": "written (no voice yet)"}
        story.add_ledger(cfg, entry)
        json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say(f"   saved: {os.path.basename(p)}  ({story.nwords(script)} words)  {summ.get('title_hi', '')}")
        made += 1
    say(f"\n   {made} scripts in {os.path.join(ctx['media'], 'scripts')} - open one in Sukoon episode to make it")
    try:
        import elements_auto
        allk = set()
        for f in glob.glob(os.path.join(ctx_folder(ctx, "scripts"), "*.txt"))[-max(1, made):]:
            allk |= set(elements_auto.needs(open(f, encoding="utf-8").read()))
        if allk:
            _queue_fetch(ctx, sorted(allk), "for these scripts")
    except Exception as e:
        say(f"   (elements check skipped: {e})")
    return made > 0


# ------------------------------------------------------------------ steps
def write_script(ctx, name, text):
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    open(p, "w", encoding="utf-8").write(text.replace("\r\n", "\n"))
    ctx["say"](f"   {p}  ({len(text.split())} words)")


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


EMOTIONS = ["डर", "फुसफुसाहट", "रहस्य", "उदास", "प्यार", "गुस्सा", "उत्साह", "सामान्य"]


def mark_emotions(ctx, name, genre="", emotions=True, pauses=True):
    """Add sparse context-aware delivery tags and punctuation-only pause cues."""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    if not os.path.exists(p):
        return say("   (no script to mark)")
    lines = open(p, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    tag = re.compile(r"^\s*\[(" + "|".join(EMOTIONS) + r")\]")
    content = {i: tag.sub("", line, count=1).strip() for i, line in enumerate(lines)}
    idx = [i for i, line in content.items() if re.search("[\u0900-\u097F]", line)
           and not line.startswith(("(", "["))]
    if not idx:
        return say("   (no Hindi story lines to mark)")
    marks, paced = {}, {}
    for k in range(0, len(idx), 60):
        part = idx[k:k + 60]
        first, last = idx.index(part[0]), idx.index(part[-1])
        lo, hi = max(0, first - 2), min(len(idx), last + 3)
        context = "\n".join(f"{idx[j] + 1}. {content[idx[j]]}" for j in range(lo, hi))
        targets = "\n".join(f"{j + 1}. {content[i]}" for j, i in enumerate(part))
        prompt = ("You are a restrained Hindi narration director" +
                  (f" for {GENRES[genre]['desc']}" if genre in GENRES else "") + ". " +
                  DIRECTION.get(genre, DIRECTION[""]) + "\nRead the surrounding passage for meaning. For every TARGET line, " +
                  ("choose a justified delivery feeling from " + ", ".join(EMOTIONS) +
                   "; use सामान्य for no special feeling. " if emotions else "do not assign emotions. ") +
                  ("Only when genuinely useful, add '...' for a held beat or ',' for a short breath; most lines get none. "
                   if pauses else "Do not change punctuation. ") +
                  "Words such as पर, तो, कि, फिर, ही, भी, बस, अब, वो and कोई have no automatic cue: decide from the whole "
                  "sentence and nearby passage. Keep dependent clauses connected; do not create suspense by fragmenting text. "
                  "Never rewrite, add, delete, reorder, or inflect spoken words. Return JSON only, keyed by target number; "
                  "omit fields when unchanged. Example: {\"1\":{\"e\":\"डर\",\"t\":\"same words with punctuation only\"}}\n\n"
                  "SURROUNDING PASSAGE (context only):\n" + context + "\n\nTARGET LINES:\n" + targets)
        try:
            t, _ = story.ask(cfg, prompt, "auto", 0.2, as_json=True, say=say)
            d = json.loads(t[t.find("{"):t.rfind("}") + 1])
        except Exception as e:
            say(f"   (emotion marks skipped for lines {k + 1}-{k + len(part)}: {e})")
            continue
        for j, i in enumerate(part):
            v = d.get(str(j + 1), "")
            if not isinstance(v, dict):
                continue
            e = str(v.get("e", "")).strip()
            if emotions and e in EMOTIONS and e != "सामान्य" and not tag.match(lines[i]):
                marks[i] = e
            candidate = str(v.get("t", "")).strip()
            original_words = re.findall(r"[^\W_]+", content[i].casefold(), flags=re.UNICODE)
            candidate_words = re.findall(r"[^\W_]+", candidate.casefold(), flags=re.UNICODE)
            original_punctuation = re.findall(r"[^\w\s]", content[i], flags=re.UNICODE)
            candidate_punctuation = re.findall(r"[^\w\s]", candidate, flags=re.UNICODE)
            punctuation_pos, added_punctuation = 0, []
            for mark in candidate_punctuation:
                if punctuation_pos < len(original_punctuation) and mark == original_punctuation[punctuation_pos]:
                    punctuation_pos += 1
                else:
                    added_punctuation.append(mark)
            preserved = punctuation_pos == len(original_punctuation)
            allowed_additions = all(mark in (",", ".") for mark in added_punctuation)
            if pauses and candidate and candidate.count("...") <= 2 and candidate_words == original_words \
                    and preserved and allowed_additions and candidate != content[i]:
                paced[i] = candidate
    if not marks and not paced:
        return say("   no safe emotion or pause cues were returned; original script kept")
    old = os.path.join(os.path.dirname(p), "_old")
    os.makedirs(old, exist_ok=True)
    shutil.copy2(p, os.path.join(old, f"{slug(name)}_before_emotions_{time.strftime('%Y%m%d_%H%M%S')}.txt"))
    for i, text in paced.items():
        existing = tag.match(lines[i])
        prefix = f"[{existing.group(1)}] " if existing else ""
        lines[i] = prefix + text
    for i, emotion in marks.items():
        lines[i] = f"[{emotion}] " + lines[i].lstrip()
    open(p + ".part", "w", encoding="utf-8").write("\n".join(lines))
    os.replace(p + ".part", p)
    counts = {}
    for emotion in marks.values():
        counts[emotion] = counts.get(emotion, 0) + 1
    say(f"   {len(marks)} of {len(idx)} lines marked: " +
        (", ".join(f"{key} {value}" for key, value in counts.items()) or "no new emotion tags") +
        f"; {len(paced)} lines got a context-checked pause cue - saved in the script")


# ------------------------------------------------------------------ the word rack (words your voice stumbles on)
def rack_path(cfg):
    return os.path.join(cfg["sukoonvoice_media"], "word_rack.json")


def word_rack(cfg):
    """{word: {count, examples, narrations, last, alt, solved}} - written by the voice engine after every narration"""
    try:
        return json.load(open(rack_path(cfg), encoding="utf-8"))
    except Exception:
        return {}


def hard_words(cfg, min_count=2):
    rk = word_rack(cfg)
    return sorted([w for w, r in rk.items() if r.get("count", 0) >= min_count], key=lambda w: -rk[w]["count"])


def rack_save(cfg, body):
    """Settings: mark words solved after retraining (count back to 0, history kept) or set your own alternative"""
    rk = word_rack(cfg)
    for w in body.get("solved") or []:
        if w in rk:
            rk[w]["solved_on"] = time.strftime("%Y-%m-%d"); rk[w]["count"] = 0
    for w, alt in (body.get("alt") or {}).items():
        if w in rk:
            rk[w]["alt"] = alt.strip()
    json.dump(rk, open(rack_path(cfg), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"words": [dict(v, word=k) for k, v in sorted(rk.items(), key=lambda kv: -kv[1].get("count", 0))]}


def avoid_hard_words(ctx, name, genre=""):
    """lines with words your voice keeps stumbling on are rewritten with the same meaning and feeling but other
    words (your own alternative from the rack first); the script before is kept in _old"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    hard = hard_words(cfg)
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    if not hard or not os.path.exists(p):
        return True
    rk = word_rack(cfg)
    lines = open(p, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    has = lambda l: [w for w in hard if re.search(r"(?<![\u0900-\u097F])" + re.escape(w) + r"(?![\u0900-\u097F])", l)]
    idx = [i for i, l in enumerate(lines) if has(l)]
    if not idx:
        return True
    changed = 0
    for i in idx:                                         # your own alternative: a plain swap, no AI needed
        for w in has(lines[i]):
            if rk[w].get("alt"):
                lines[i] = re.sub(r"(?<![\u0900-\u097F])" + re.escape(w) + r"(?![\u0900-\u097F])", rk[w]["alt"], lines[i])
                changed += 1
    idx = [i for i in idx if has(lines[i])]
    if idx:
        ask = ("Rewrite each numbered Hindi line so it does NOT use these words: " + ", ".join(hard) + ". Keep the SAME "
               "meaning, feeling and length, the same '...' pauses and [marks]; use simple spoken Hindi words instead "
               "(a synonym or a slightly changed sentence). Answer JSON only: {\"1\": \"...\", ...}\n\n" +
               "\n".join(f"{j + 1}. {lines[i].strip()}" for j, i in enumerate(idx)))
        try:
            t, m = story.ask(cfg, ask, "auto", 0.3, as_json=True, say=say)
            d = json.loads(t[t.find("{"):t.rfind("}") + 1])
            for j, i in enumerate(idx):
                nl = str(d.get(str(j + 1), "")).strip()
                if nl and not has(nl) and 0.6 < len(nl) / max(1, len(lines[i].strip())) < 1.6:
                    lines[i] = nl; changed += 1
        except Exception as e:
            say(f"   (could not rewrite the hard words: {e})")
    if changed:
        old = os.path.join(os.path.dirname(p), "_old")
        os.makedirs(old, exist_ok=True)
        shutil.copy2(p, os.path.join(old, f"{slug(name)}_before_hardwords_{time.strftime('%Y%m%d_%H%M')}.txt"))
        open(p + ".part", "w", encoding="utf-8").write("\n".join(lines))
        os.replace(p + ".part", p)
    say(f"   words your voice stumbles on ({', '.join(hard[:8])}{'…' if len(hard) > 8 else ''}): {changed} place(s) said another way")
    return True


def practice_story(ctx, minutes=3):
    """a short story that uses every hard word several times - record it, tag it to its series, retrain"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    hard = hard_words(cfg, 1)
    if not hard:
        return say("   the word rack is empty - nothing to practise yet")
    ask = (f"Write a natural spoken Hindi story of about {int(float(minutes) * 140)} words (Devanagari only) that uses "
           "EACH of these words at least three times, in different sentences and positions: " + ", ".join(hard[:40]) +
           ". One phrase per line, simple and warm. Only the story.")
    t, m = story.ask(cfg, ask, "auto", 0.8, say=say)
    name = f"Practice - hard words - {time.strftime('%Y-%m-%d %H%M')}"
    p = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    open(p, "w", encoding="utf-8").write(t.strip() + "\n")
    miss = [w for w in hard[:40] if w not in t]
    say(f"   {p}\n   record it in your own voice (Audacity), save the project in a recordings folder, tag it to the "
        f"series it suits in Settings, and train that series again" + (f"\n   (not used by the AI: {', '.join(miss)})" if miss else ""))
    return True


def free_gpu(ctx):
    cfg, say = ctx["cfg"], ctx["say"]
    if comfy_running(cfg):
        try:
            _post(cfg["comfy_url"] + "/free", {"unload_models": True, "free_memory": True}, 30)
            say("   ComfyUI: pictures model unloaded")
        except Exception as e:
            say(f"   ComfyUI could not unload its models: {e}")
    if ctx.get("include_llm"):
        free_llm(ctx)


def free_llm(ctx):
    """Unload loaded Ollama role models so ComfyUI or TTS can use the GPU."""
    cfg, say = ctx["cfg"], ctx["say"]
    if not ctx.get("include_llm") and not cfg.pop("_ollama_used", False):
        return
    models = {cfg.get("llm"), cfg.get("prompt_model"), cfg.get("vision_llm"), cfg.get("story_model")}
    try:
        import story
        models.add(story.secret("local_model"))
    except Exception:
        pass
    models.discard(None)
    models.discard("")
    if not models:
        say("   Ollama: no selected models to unload")
        return
    errors = []
    for model in sorted(models):
        try:
            _post(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/generate",
                  {"model": model, "prompt": "", "keep_alive": 0}, 30)
            say(f"   Ollama: unloaded {model}; server stays running")
        except Exception as e:
            errors.append(f"{model}: {e}")
    if errors:
        say("   Ollama unload issue: " + "; ".join(errors))


def sv_python(cfg):
    f = os.path.join(cfg["sukoonvoice_dir"], "python.txt")
    if os.path.exists(f):
        p = open(f, encoding="utf-8-sig").read().strip().strip('"')
        if p:
            return p
    return cfg["gpu_python"]


def _story_voice_style(ctx, script_path, genre):
    """Choose a tone reference from the finished AI story; fall back to its selected series."""
    cfg, say = ctx["cfg"], ctx["say"]
    fallback = GENRES.get(genre, GENRES["bhay"])["voice"]
    ref_dir = cfg.get("voice_references_dir", os.path.join(cfg.get("sukoonvoice_media", ""), "dataset", "refs"))
    available = {os.path.splitext(f)[0].lower(): os.path.splitext(f)[0]
                 for f in os.listdir(ref_dir) if f.lower().endswith(".wav")
                 and not f.lower().startswith(("room_tone", "smile"))} if os.path.isdir(ref_dir) else {}
    choices = {}
    for gid, values in GENRES.items():
        reference = values["voice"]
        if reference.lower() in available:
            choices[gid] = {"reference": available[reference.lower()], "description": values["desc"]}
    if len(choices) < 2:
        say(f"   story-tone match skipped; keeping the {genre} voice reference ({fallback})")
        return fallback
    try:
        script = open(script_path, encoding="utf-8-sig", errors="replace").read()[:9000]
        prompt = ("Choose which existing voice reference best fits the emotional narration tone of this completed "
                  "Hindi story. Consider its actual events and emotional arc, not only its series label. Do not change "
                  "the story's genre or rewrite it. Choose one candidate id exactly. Return JSON only: "
                  '{"id":"...","reason":"short phrase"}\nCandidates: ' +
                  json.dumps(choices, ensure_ascii=False) + "\nSelected series: " + str(genre) +
                  "\nSTORY:\n" + script)
        model = cfg.get("prompt_model") or cfg.get("llm")
        answer = model_chat(cfg, model, prompt, temperature=0.1, as_json=True, timeout=120)
        data = json.loads(answer[answer.find("{"):answer.rfind("}") + 1])
        chosen = choices.get(str(data.get("id", "")).lower())
        if chosen:
            say(f"   story-tone match: {chosen['reference']} ({str(data.get('reason') or choices[str(data['id']).lower()]['description'])[:100]})")
            return chosen["reference"]
        say(f"   story-tone match returned no available reference; keeping {fallback}")
    except Exception as e:
        say(f"   story-tone match unavailable ({str(e)[:100]}); keeping {fallback}")
    finally:
        free_llm(ctx)
    return fallback


def sv_speak(ctx, name, sets, redo="", style=""):
    cfg = ctx["cfg"]
    script = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    if not os.path.exists(script):
        old = os.path.join(narration_dir(cfg, name, existing=True), "script.txt")
        if not os.path.exists(old):
            raise RuntimeError(f"no script for {name}")
        script = old
    if not os.path.isdir(os.path.join(cfg["sukoonvoice_dir"], "libs", "cb")):
        raise RuntimeError("the voice engine is not set up yet - run setup_hub.bat once")
    if style == "__auto_story_theme__":
        style = _story_voice_style(ctx, script, str((ctx.get("job") or {}).get("params", {}).get("genre") or "bhay"))
    hv = os.path.join(narration_dir(cfg, name, existing=bool(redo)), "hub_voice.json")
    if redo and os.path.exists(hv):                  # remaking lines: the SAME voice settings as the narration had
        try:
            saved = json.load(open(hv, encoding="utf-8"))
            keys = {x.split("=")[0] for x in sets}
            sets = [x for x in saved.get("sets", []) if x.split("=")[0] not in keys] + list(sets)
            style = style or saved.get("style", "")
        except Exception:
            pass
    elif not redo:
        os.makedirs(os.path.dirname(hv), exist_ok=True)
        json.dump({"sets": [x for x in sets if not x.startswith("takes=")], "style": style}, open(hv, "w", encoding="utf-8"),
                  ensure_ascii=False)
    cmd = [sv_python(cfg), "-s", "-u", "run.py", "speak", script, "--name", slug(name)]
    for s in sets:
        cmd += ["--set", s]
    if redo:
        cmd += ["--redo", redo]
    if style:
        cmd += ["--style", style]
    env = {"SV_LOGNAME": f"hub_{ctx['job']['id']}.txt"}
    # Keep generated audio in HubGPT's output tree. HubGPT Voice model/training data stays shared.
    output_root = os.path.dirname(narration_dir(cfg, name, existing=bool(redo)))
    env["SV_NARRATIONS"] = output_root
    rc = ctx["run_cmd"](cmd, cwd=cfg["sukoonvoice_dir"], env=env)
    out = narration_dir(cfg, name, existing=bool(redo))
    if rc == 0:
        rep = os.path.join(out, "report.txt")
        if os.path.exists(rep):
            for ln in open(rep, encoding="utf-8").read().splitlines()[3:]:
                if ln.strip():
                    ctx["say"]("   " + ln)
    return rc == 0


def en_dir(cfg):
    return cfg.get("english_dir") or os.path.join(os.path.dirname(cfg["studio_dir"]), "english")


def en_python(cfg):
    f = os.path.join(en_dir(cfg), "python.txt")
    if os.path.exists(f):
        p = open(f, encoding="utf-8-sig").read().strip().strip('"')
        if p:
            return p
    return cfg["gpu_python"]


def en_speak(ctx, name, redo="", speaker=""):
    """the English narrator (Qwen3-TTS, engines\\english) reads the script; same narration folder layout as yours"""
    cfg = ctx["cfg"]
    script = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt")
    if not os.path.exists(script):
        script = os.path.join(narration_dir(cfg, name, existing=True), "script.txt")
    if not os.path.exists(script):
        raise RuntimeError(f"no script for {name}")
    d = en_dir(cfg)
    if not os.path.isdir(os.path.join(d, "libs", "qwen")):
        raise RuntimeError("the English narrator is not set up yet - run setup_hub.bat once")
    output_dir = os.path.dirname(narration_dir(cfg, name, existing=bool(redo)))
    cmd = [en_python(cfg), "-s", "-u", "run.py", "speak", script, "--out",
           output_dir, "--name", slug(name), "--ffmpeg", cfg["ffmpeg"]]
    if redo:
        cmd += ["--redo", redo]
    if speaker:
        cmd += ["--speaker", speaker]
    rc = ctx["run_cmd"](cmd, cwd=d)
    out = narration_dir(cfg, name, existing=bool(redo))
    rep = os.path.join(out, "report.txt")
    if rc == 0 and os.path.exists(rep):
        for ln in open(rep, encoding="utf-8").read().splitlines()[3:]:
            if ln.strip():
                ctx["say"]("   " + ln)
    return rc == 0


# ------------------------------------------------------------------ भय (Bhay) horror stories
def horror_story(ctx, name, topic="", minutes=15, notes="", engine="auto", recipe=None, genre="bhay", level="standard", auto_name=False):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    script, info = story.write_genre(cfg, genre, say, topic, float(minutes or 15), notes, engine, recipe, ctx["stopped"],
                                     level)
    if story.nwords(script) < 150:
        raise RuntimeError("the story came out too short - try again")
    summ = story.summarise(cfg, script, say)
    if auto_name:
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
             "recipe": info["recipe"], "model": info["model"], "words": story.nwords(script), "summary": summ}
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
                 "words": story.nwords(script), "summary": summ, "status": "written (no voice yet)"}
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
    "present. No gore or text), \"ambience\" (ONE name from AMBIENCE or "
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
    "this story's look and light, symbolic, no faces, no text}}; most parts have none - use it for at most {mom} "
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
    "Start with the main subject. No recurring character unless this part is about them; no gore, no text. "
    "Answer ONLY JSON: {{\"prompt\": \"...\"}}")
SCENE_SIMILAR_MAX = 0.42        # word overlap (0-1) above which a scene prompt counts as a repeat of an earlier one
SCENE_VARY_HINTS = ("wide establishing view of the place from outside, no people in frame",
                    "extreme close-up of a single object from this moment, shallow depth of field, no people",
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
                                                    text=str(text)[:700]), "auto", 0.7, True, say=lambda *a: None)
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
SCENE_SECONDS = 30            # Experimental genre videos: about one picture per this many seconds (Tinker: scene_seconds)
_SENT_END = re.compile(r"[।?!॥]\s*[\"'”’)]*\s*$")
SCENE_PICTURE_ASK = (
    "You write the picture prompts for a {desc} video. For EACH numbered scene below you get WHAT HAPPENS in it "
    "and the story TEXT it covers (the text is read aloud while the picture is on screen).\n"
    "Write one English image prompt (35-60 words) per scene that shows exactly that moment: start with the main "
    "subject (a person, object, place or action from WHAT HAPPENS), then the location, the shot size and the light. "
    "Use ONLY people, objects, places, weather and light sources that the scene's own TEXT or WHAT HAPPENS names; "
    "never add anything else. A recurring character stays a dark silhouette with no detailed face. Neighbouring "
    "scenes must not share the same main subject or camera setup. No gore, no text or letters.\n"
    'Answer ONLY JSON: {{"prompts": ["...", "..."]}} with exactly {n} prompts.\n\nSCENES:\n{scenes}')
SCENE_FIX_ASK = (
    "This picture prompt for a scene of a {desc} video adds things the story never mentions in this scene: {bad}.\n"
    "PROMPT: {prompt}\nWHAT HAPPENS: {happens}\nSTORY TEXT OF THE SCENE:\n{text}\n\n"
    "Rewrite the prompt (35-60 English words) for the same moment WITHOUT those things, using only what the story "
    "text names. Start with the main subject. Answer ONLY JSON: {{\"prompt\": \"...\"}}")
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
        return story.ask(cfg, prompt, engine, temp, True, say=say, ctx_len=ctx_len)
    except TypeError:                                    # an older story.py without ctx_len
        return story.ask(cfg, prompt, engine, temp, True, say=say)


def _clean_scene(cfg, say, desc, i, prompt, happens, text, near):
    """a picture prompt with invented props or weather -> rewritten by the AI, else those pieces are cut out"""
    bad = _invented(prompt, text, near)
    if not bad:
        return prompt
    try:
        t, _ = _ask_step(cfg, "scene_picture_model", SCENE_FIX_ASK.format(
            desc=desc, bad=", ".join(bad), prompt=prompt[:600], happens=happens[:300], text=str(text)[:900]), 0.5)
        new = str(json.loads(t).get("prompt") or "").strip()
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
                             SCENE_PICTURE_ASK.format(desc=desc, n=len(parts), scenes="\n".join(rows)), 0.6, say)
        pr = [str(x or "").strip() for x in (json.loads(t).get("prompts") or [])]
        say(f"   picture prompts written from each scene's 'what happens' line by {model}")
        return pr
    except Exception as e:
        say(f"   (picture prompts from the 'what happens' lines failed: {e} - kept the director's own prompts)")
        return []


def horror_scenes(ctx, name, narration, out, count=0, genre="bhay"):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    if os.path.exists(os.path.join(out, "shots.json")) and not ctx.get("replan"):
        return say("   the scenes are already planned for this voice - kept (pictures that exist are kept too)")
    os.makedirs(out, exist_ok=True)
    tm = json.load(open(os.path.join(narration, "timing.json"), encoding="utf-8"))
    total = tm[-1]["end"] if tm else 600
    n = int(count) or max(8, min(18, round(total / 70)))
    exp = bool(cfg.get("_experimental_workspace"))     # Experimental: scenes end at sentence ends, two-step director
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
    ask = SCENES_ASK.format(desc=G["desc"], label=G["label"], amb=", ".join(amb) or "-", hits=", ".join(hits) or "-", n=len(parts),
                            cues=", ".join(cue_names) if cpp else "- (none for this series)", cpp=cpp,
                            mom=max(2, round(total / 150)),
                            parts="\n".join(f"{i + 1}. ({int(p['start'] // 60)}:{int(p['start'] % 60):02d}) {p['text'][:1200]}"
                                            for i, p in enumerate(parts)))
    cut = [i + 1 for i, p in enumerate(parts) if len(p["text"]) > 1200]
    if cut:
        say(f"   ⚠ scene(s) {', '.join(map(str, cut))} are longer than 1200 letters - the director only reads the start")
    d = {}
    try:
        t, model = _ask_step(cfg, "director_model", ask, 0.6, say)
        d = json.loads(t)
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
        scene = str((two[i] if i < len(two) else "") or x.get("prompt") or happens
                    or "a dark empty Indian village lane at night, one weak lamp, fog").strip()
        if exp:
            near = " ".join(parts[j]["text"] for j in (i - 1, i + 1) if 0 <= j < len(parts))
            scene = _clean_scene(cfg, say, G["desc"], i, scene, happens, p["text"], near)
        character = str(x.get("character") or "").strip()[:60]
        anchor = str(character_anchors.get(character) or "").strip() if character else ""
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
    plan = {"audio": audio, "fps": 24, "title": title, "label": G["label"], "mood": G["mood"], "duration": total,
            "scenes": scenes, "lines": lines}
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


# ------------------------------------------------------------------ your voice per series (train one per series)
SERIES_DEFAULT = {"sukoon": ("सुकून", "calm, sleep, counselling"), "kisse": ("किस्से", "emotional stories"),
                  "rahasya": ("रहस्य", "thriller, mystery"), "bhay": ("भय", "horror"),
                  "romanch": ("रोमांच", "romance / sensual"), "kareeb": ("करीब", "ASMR, close and soft"),
                  "gyaan": ("ज्ञान", "informational")}


def _series_file(cfg):
    return os.path.join(cfg["sukoonvoice_media"], "series.json")


def series_load(cfg):
    try:
        d = json.load(open(_series_file(cfg), encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("series", {})
    for k, (n, a) in SERIES_DEFAULT.items():
        d["series"].setdefault(k, {"name": n, "about": a})
    d.setdefault("episodes", {})
    return d


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(str(s)))[0].lower())


def episode_series_of(ep, d):
    e = {_norm(k): v for k, v in d["episodes"].items()}
    return e.get(_norm(ep)) or ("sukoon" if "sukoon" in ep.lower() else "")


def genre_voice_sets(cfg, gid, b):
    """a series' OWN delivery (never the सुकून settings): its trained voice (or your plain voice clone when it has
    none yet - not the सुकून add-on), its pace and pauses, its expressiveness"""
    G = GENRES[gid]
    run = series_model(cfg, gid) or (series_model(cfg, "rahasya") if gid == "bhay" else "")
    sets = ["with_music=false", "model=" + json.dumps(run or "base"), "match=false", "room_tone=false",
            "pace_style=" + json.dumps(gid),
            "acoustic_context=" + json.dumps(bool(b.get("acoustic_context", True))),
            f"exaggeration={float(b.get('exaggeration') or G['ex'])}", f"temperature={G['temp']}"]
    base = {"pause_comma": .35, "pause_line": 1.0, "pause_sentence": 1.1, "pause_dots": 1.2, "pause_marker": 1.6,
            "pause_paragraph": 2.2}
    k = float(b.get("pause_scale") or G["pause"])
    sets += [f"{p}={round(v * k, 3)}" for p, v in base.items()]
    if b.get("takes"):
        sets.append(f"takes={int(b['takes'])}")
    st, tn = G.get("drama", ("horror", 1.7))
    if b.get("dramatic", True):          # the भय way for every series: held silences in the series' own kind of tension
        sets += ["dramatic=true", f"drama_style={json.dumps(st)}",
                 f"pause_tension={round(tn * min(1.3, max(0.8, k / G['pause'])), 2)}"]
    if b.get("emotions", True):
        sets.append("emotions=true")
    return sets, run


def series_model(cfg, sid):
    """the newest finished voice trained for this series (run folder name), or '' """
    roots = (voice_model_root(cfg), cfg.get("voice_training_dir", os.path.join(cfg["sukoonvoice_media"], "training")))
    runs = sorted((d for tr in roots for d in glob.glob(os.path.join(tr, "run_*")) if f"_series_{sid}" in os.path.basename(d)
                   and "_smoke" not in d and os.path.exists(os.path.join(d, "best.pt"))), key=_mtime, reverse=True)
    return os.path.basename(runs[0]) if runs else ""


def voice_series(cfg):
    """recordings (minutes each) + series (minutes, trained voice) for the Settings card"""
    d = series_load(cfg)
    mins = {}
    mp = os.path.join(cfg.get("voice_dataset_dir", os.path.join(cfg["sukoonvoice_media"], "dataset")), "metadata.jsonl")
    if os.path.exists(mp):
        for ln in open(mp, encoding="utf-8"):
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            mins[m["episode"]] = mins.get(m["episode"], 0) + float(m.get("dur") or 0) / 60
    eps = [{"name": k, "minutes": round(v, 1), "series": episode_series_of(k, d)} for k, v in sorted(mins.items(), key=lambda kv: -kv[1])]
    out = []
    for sid, info in d["series"].items():
        m = sum(e["minutes"] for e in eps if e["series"] == sid)
        run = series_model(cfg, sid)
        if sid == "sukoon" and not run:
            run = "(your current Sukoon voice)"
        out.append({"id": sid, "name": info.get("name", sid), "about": info.get("about", ""), "minutes": round(m, 1),
                    "trained": run})
    return {"series": out, "recordings": eps}


def voice_series_save(cfg, episodes, add=None):
    d = series_load(cfg)
    d["episodes"] = {_norm(k): v for k, v in d["episodes"].items()}
    for ep, sid in (episodes or {}).items():
        if sid:
            d["episodes"][_norm(ep)] = sid
        else:
            d["episodes"].pop(_norm(ep), None)
    if add and add.get("id") and add.get("name"):                   # a new series of your own
        sid = re.sub(r"[^a-z0-9]+", "", add["id"].lower())[:20]
        if sid:
            d["series"][sid] = {"name": add["name"], "about": add.get("about", "")}
    p = _series_file(cfg)
    json.dump(d, open(p + ".part", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(p + ".part", p)
    return voice_series(cfg)


def _voice_cfg_path(cfg):
    return os.path.join(cfg["sukoonvoice_dir"], "config.json")


def voice_scan(cfg, folder):
    """every .aup3 under a folder (and its subfolders): name, size, imported for training yet, its series"""
    if not folder or not os.path.isdir(folder):
        raise ValueError("folder not found")
    d = series_load(cfg)
    proj = cfg.get("voice_projects_dir", os.path.join(cfg["sukoonvoice_media"], "dataset", "projects"))
    have = {f[:-6] for f in os.listdir(proj)} if os.path.isdir(proj) else set()
    mp = os.path.join(cfg.get("voice_dataset_dir", os.path.join(cfg["sukoonvoice_media"], "dataset")), "metadata.jsonl")
    if os.path.exists(mp):
        for ln in open(mp, encoding="utf-8"):
            try:
                have.add(_norm(json.loads(ln)["episode"]))
            except Exception:
                pass
    out = []
    for dp, dn, fn in os.walk(folder):
        dn[:] = [x for x in dn if not x.startswith(("_", "."))]
        for f in fn:
            if f.lower().endswith(".aup3"):
                p = os.path.join(dp, f)
                out.append({"name": f[:-5], "file": p, "mb": round(os.path.getsize(p) / 2 ** 20),
                            "imported": _norm(f) in have, "series": episode_series_of(f[:-5], d)})
        if len(out) > 1500:
            break
    vc = json.load(open(_voice_cfg_path(cfg), encoding="utf-8-sig"))
    return {"files": sorted(out, key=lambda x: x["name"].lower()),
            "folders": (vc.get("recordings") or {}).get("folders", [])}


def voice_add_folders(cfg, folder):
    """the voice engine imports .aup3 from these folders (only the folder itself - so every subfolder with
    projects is added too); its config.json keeps everything else"""
    p = _voice_cfg_path(cfg)
    vc = json.load(open(p, encoding="utf-8-sig"))
    R = vc.setdefault("recordings", {})
    fl = R.setdefault("folders", [])
    for dp, dn, fn in os.walk(folder):
        dn[:] = [x for x in dn if not x.startswith(("_", "."))]
        if any(f.lower().endswith(".aup3") for f in fn) and dp not in fl:
            fl.append(dp)
    shutil.copyfile(p, p + time.strftime(".before_%Y%m%d_%H%M%S.json"))
    json.dump(vc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return fl


def sv_dataset(ctx):
    """your recordings -> training lines (Whisper hears them; no script needed). Finished projects are skipped."""
    cfg = ctx["cfg"]
    return ctx["run_cmd"]([sv_python(cfg), "-s", "-u", "run.py", "dataset"], cwd=cfg["sukoonvoice_dir"],
                          env={"SV_LOGNAME": f"hub_{ctx['job']['id']}.txt"}) == 0


def classify_recordings(ctx):
    """recordings without a series: the AI reads what you say in them and picks the series (you can change it)"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    d = series_load(cfg)
    mp = os.path.join(cfg.get("voice_dataset_dir", os.path.join(cfg["sukoonvoice_media"], "dataset")), "metadata.jsonl")
    texts = {}
    for ln in open(mp, encoding="utf-8"):
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        texts.setdefault(m["episode"], []).append(m.get("text", ""))
    for pj in glob.glob(os.path.join(cfg.get("voice_projects_dir", os.path.join(cfg["sukoonvoice_media"], "dataset", "projects")), "*.jsonl")):
        for ln in open(pj, encoding="utf-8"):
            try:
                m = json.loads(ln)
                texts.setdefault(m["episode"], []).append(m.get("text", ""))
            except Exception:
                pass
    todo = [e for e in texts if not episode_series_of(e, d)]
    say(f"   {len(texts)} recordings, {len(todo)} without a series")
    opts = "; ".join(f"{k} = {v.get('name')} ({v.get('about')})" for k, v in d["series"].items())
    n = 0
    for e in todo:
        if ctx["stopped"]():
            return False
        sample = " ".join(texts[e])[:2500]
        try:
            t, _ = story.ask(cfg, f"Which series does this Hindi voice recording belong to? Series: {opts}.\n"
                                  f"Recording '{e}' says:\n{sample}\n\nAnswer ONLY JSON: {{\"series\": \"<id>\"}}",
                             "auto", 0.1, True, say=say)
            sid = str(json.loads(t).get("series", "")).strip()
        except Exception as ex:
            say(f"   (could not decide {e}: {ex})"); continue
        if sid in d["series"]:
            d["episodes"][_norm(e)] = sid
            n += 1
            say(f"   {e} -> {d['series'][sid]['name']}")
    p = _series_file(cfg)
    json.dump(d, open(p + ".part", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(p + ".part", p)
    say(f"   {n} recordings sorted (change any in Settings > Your voice per series)")
    return True


def train_all_series(ctx, include_sukoon=False, min_minutes=10, force=False):
    """one voice per series that has enough recordings; series trained in the last day are skipped (unless force)"""
    cfg, say = ctx["cfg"], ctx["say"]
    vs = voice_series(cfg)
    ok = True
    for x in sorted(vs["series"], key=lambda x: -x["minutes"]):
        if ctx["stopped"]():
            return False
        if x["id"] == "sukoon" and not include_sukoon:
            continue
        if x["minutes"] < min_minutes:
            say(f"   {x['name']}: only {x['minutes']} min of recordings - skipped (needs {min_minutes})"); continue
        run = series_model(cfg, x["id"])
        if run and not force:
            rd = os.path.join(voice_model_root(cfg), run)
            if time.time() - _mtime(rd) < 86400:
                say(f"   {x['name']}: trained today ({run}) - skipped"); continue
        say(f"\n   === training {x['name']} ({x['minutes']} min) ===")
        free_gpu(ctx)
        ok = sv_train(ctx, x["id"]) and ok
    return ok


def sv_train(ctx, series, epochs=None):
    cfg = ctx["cfg"]
    cmd = [sv_python(cfg), "-s", "-u", "run.py", "train", "--new", "--profile", f"series_{series}"]
    if epochs:
        cmd += ["--epochs", str(int(epochs))]
    env = {"SV_LOGNAME": f"hub_{ctx['job']['id']}.txt", "SV_MODEL_DIR": voice_model_root(cfg),
           "SV_MODEL_BACKUP_DIR": cfg.get("voice_model_backup_dir", r"D:\AI\Mukhtalif_model")}
    return ctx["run_cmd"](cmd, cwd=cfg["sukoonvoice_dir"], env=env) == 0


# ------------------------------------------------------------------ local model settings (Ollama)
def _settings_path(cfg):
    return os.path.join(cfg["media_dir"], "hub_settings.json")


def apply_settings(cfg):
    try:
        st = json.load(open(_settings_path(cfg), encoding="utf-8"))
    except Exception:
        st = {}
    for key in ("llm", "story_model", "vision_llm", "prompt_model", "director_model", "scene_picture_model",
                "scene_seconds"):
        if st.get(key):
            cfg[key] = st[key]
    return st


def llm_models(cfg):
    """Models installed in Ollama."""
    try:
        raw = json.loads(_get(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/tags", 5))
        out = [{"name": m.get("name", "")} for m in raw.get("models", []) if m.get("name")]
    except Exception as e:
        return {"current": cfg.get("llm"), "models": [], "error": f"Ollama not reachable ({e})"}
    return {"current": cfg.get("llm"), "prompt_model": cfg.get("prompt_model"),
            "models": out, "top": [x["name"] for x in out[:3]]}


def set_llm(cfg, name):
    st = apply_settings(cfg)
    st.pop("llm_backend", None)
    st["llm"] = name
    json.dump(st, open(_settings_path(cfg), "w", encoding="utf-8"), indent=1)
    cfg["llm"] = name
    return llm_models(cfg)


def set_prompt_model(cfg, name):
    st = apply_settings(cfg)
    st.pop("llm_backend", None)
    st["prompt_model"] = name
    json.dump(st, open(_settings_path(cfg), "w", encoding="utf-8"), indent=1)
    cfg["prompt_model"] = name
    return llm_models(cfg)


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


def en_setup(ctx):
    cfg = ctx["cfg"]
    return ctx["run_cmd"]([en_python(cfg), "-s", "-u", "run.py", "setup", "--yes"], cwd=en_dir(cfg)) == 0


def en_test(ctx, engine=""):
    cfg = ctx["cfg"]
    cmd = [en_python(cfg), "-s", "-u", "run.py", "test", "--ffmpeg", cfg["ffmpeg"]]
    if engine:
        cmd += ["--engine", engine]
    return ctx["run_cmd"](cmd, cwd=en_dir(cfg)) == 0


def _parts(narration, script, n):
    """the story cut into n parts (at phrase boundaries, equal time) -> [{text, start, end}]"""
    t = os.path.join(narration, "timing.json") if narration else ""
    if t and os.path.exists(t):
        tm = json.load(open(t, encoding="utf-8"))
        total = tm[-1]["end"] if tm else 1
        parts, cur = [], []
        for x in tm:
            cur.append(x)
            if x["end"] >= total * (len(parts) + 1) / n and len(parts) < n - 1:
                parts.append(cur); cur = []
        if cur:
            parts.append(cur)
        return [{"text": " ".join(c["text"] for c in p), "start": p[0]["start"], "end": p[-1]["end"]} for p in parts]
    if narration and not script:
        s = os.path.join(narration, "script.txt")
        script = open(s, encoding="utf-8").read() if os.path.exists(s) else ""
    lines = [l.strip() for l in script.splitlines() if l.strip() and not re.fullmatch(r"\(?\.*\)?", l.strip())]
    if not lines:
        return [{"text": "", "start": 0, "end": 0}] * n
    k = max(1, round(len(lines) / n))
    return [{"text": " ".join(lines[i:i + k]), "start": None, "end": None} for i in range(0, len(lines), k)][:n]


def llm_prompts(ctx, narration, script, n, out, idea=""):
    cfg, say = ctx["cfg"], ctx["say"]
    os.makedirs(out, exist_ok=True)
    if not script and narration:                                # the AI wrote it: read the saved script
        sp = os.path.join(ctx_folder(ctx, "scripts"), slug(os.path.basename(narration.rstrip("\\/"))) + ".txt")
        script = open(sp, encoding="utf-8").read() if os.path.exists(sp) else ""
    parts = _parts(narration, script, max(1, int(n)))
    ask = ("You design calm, beautiful pictures for a Hindi sleep-story channel (Sukoon). For EACH numbered part "
           "of the story below, write ONE English image prompt (25-45 words): a peaceful visual scene that matches "
           "what is said - place, light, objects, mood. No text or letters in the picture, no close-up faces. "
           + (f"The viewer's own idea for the pictures: {idea}. " if idea else "")
           + 'Answer ONLY as JSON: {"prompts": ["...", "..."]} with exactly ' + str(len(parts)) + " prompts.\n\n"
           + "\n".join(f"{i + 1}. {p['text'][:900]}" for i, p in enumerate(parts)))
    prompts = []
    try:
        model = cfg.get("prompt_model") or cfg.get("llm")
        say(f"   asking {model} (Ollama prompt model) ...")
        prompts = [str(x) for x in json.loads(model_chat(cfg, model, ask, 0.6, True, timeout=600)).get("prompts", [])]
    except Exception as e:
        say(f"   (the AI could not answer: {e} - using simple prompts)")
    while len(prompts) < len(parts):
        prompts.append((idea + ", " if idea else "") + "a peaceful quiet night scene, soft light, calm and cozy")
    shots = []
    for i, (p, pr) in enumerate(zip(parts, prompts)):
        f = f"{i + 1:03d}.png"
        shots.append({"file": f, "prompt": pr, "text": p["text"], "start": p["start"], "end": p["end"]})
        say(f"   {i + 1}: {pr}")
    json.dump({"source": narration or "script", "shots": shots}, open(os.path.join(out, "shots.json"), "w",
              encoding="utf-8"), ensure_ascii=False, indent=1)


def default_style(cfg):
    """your own Sukoon thumbnail look when the thumbnail index has it, else the Sukoon night preset"""
    return "sukoon_theme" if load_index(cfg).get("themes", {}).get("sukoon") else "sukoon_night"


ART_ASK = (
    "You design the ONE artwork for a whole episode of 'Sukoon', a calm night-time sleep-story channel. It is shown "
    "behind the whole video and as the thumbnail, so it must say at a glance what THIS episode is about.\n"
    "Read the episode below{extra}. Then write ONE English image prompt (40-70 words) for a peaceful night scene that "
    "shows the episode's central image and setting: who is there (if anyone: seen from behind or far, no close-up "
    "faces), the place, the light (it is night: moonlight, a warm lamp, a diya...), the objects that carry the story, "
    "the mood. Concrete and visual, no abstract words, no text or letters in the picture. Also give a short "
    "'check' list: 3-5 things that MUST be visible for the picture to fit this episode.\n"
    'Answer ONLY as JSON: {{"prompt": "...", "check": ["...", "..."]}}\n\nEPISODE:\n{text}')


def _script_sample(text, chars=6000):
    """beginning, middle and end of a script (the story usually lives in the middle)"""
    t = re.sub(r"\(\s*\.{2,}\s*\)\s*", " ", text or "")
    if len(t) <= chars:
        return t
    k = chars // 3
    m = len(t) // 2
    return t[:k] + "\n...\n" + t[m - k // 2:m + k // 2] + "\n...\n" + t[-k:]


def art_prompt(ctx, name, narration, script, out, idea="", count=3):
    """the episode's artwork from the WHOLE script (not just its first lines): central image, place, light, objects;
    `count` versions are painted, pick_art keeps the one that fits best"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    os.makedirs(out, exist_ok=True)
    if not script:
        for sp in (os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".txt"), os.path.join(narration or "", "script.txt")):
            if os.path.exists(sp):
                script = open(sp, encoding="utf-8").read(); break
    meta = {}
    sj = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".story.json")
    if os.path.exists(sj):
        try:
            meta = json.load(open(sj, encoding="utf-8"))
        except Exception:
            meta = {}
    r = meta.get("recipe") or {}
    extra = ""
    if r.get("image") or r.get("place"):
        extra = f" (its central image: {r.get('image', '')}; its healing place: {r.get('place', '')}; topic: {r.get('topic', '')})"
    if idea:
        extra += f" - the creator's own idea for the picture: {idea}"
    prompt, check = "", []
    try:
        say(f"   reading the whole script ({story.nwords(script or '')} words){extra and ' + its recipe'} ...")
        previous_model = cfg.get("_llm_override")
        cfg["_llm_override"] = cfg.get("prompt_model") or cfg.get("llm")
        try:
            t, model = story.ask(cfg, ART_ASK.format(extra=extra, text=_script_sample(script)), "local", 0.5, True, say=say)
        finally:
            if previous_model is None:
                cfg.pop("_llm_override", None)
            else:
                cfg["_llm_override"] = previous_model
        d = json.loads(t)
        prompt, check = str(d.get("prompt", "")).strip(), [str(x) for x in d.get("check", [])][:5]
        say(f"   designed by {model}")
    except Exception as e:
        say(f"   (the AI could not design it: {e} - using the recipe)")
    if not prompt:
        prompt = ", ".join(x for x in (idea, r.get("image"), r.get("place"), "a peaceful night scene, soft moonlight, "
                                       "warm lamp glow, calm and cozy") if x)
    say(f"   artwork: {prompt}")
    if check:
        say(f"   must show: {'; '.join(check)}")
    n = max(1, min(4, int(count or 3)))
    shots = [{"file": f"cand_{i + 1}.png", "prompt": prompt, "text": "", "start": None, "end": None} for i in range(n)]
    json.dump({"source": narration or "script", "shots": shots, "check": check, "art_prompt": prompt},
              open(os.path.join(out, "shots.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


PICK_ASK = (
    "This picture should be the artwork of a calm night-time sleep-story episode. It should show: {want}.\n"
    "Score it 0-10: how well it shows these things, AND how clean it is (no text or letters, no distorted hands or "
    "faces, no strange objects, calm night light). Answer ONLY JSON: {{\"score\": 7, \"missing\": \"...\", "
    "\"flaws\": \"...\"}}")


def pick_art(ctx, out):
    """the vision AI looks at every version and keeps the best as 001.png (the others stay in the folder)"""
    import base64
    cfg, say = ctx["cfg"], ctx["say"]
    # ComfyUI may still have its diffusion model loaded; unload it before the vision model needs VRAM.
    free_gpu(ctx)
    sj = os.path.join(out, "shots.json")
    meta = json.load(open(sj, encoding="utf-8"))
    cands = [os.path.join(out, s["file"]) for s in meta["shots"] if os.path.exists(os.path.join(out, s["file"]))]
    if not cands:
        raise RuntimeError("no artwork was painted")
    want = "; ".join(meta.get("check") or []) or meta.get("art_prompt", "")[:300]
    best, scores = cands[0], {}
    if cands:
        tdir = os.path.join(out, "_small")
        os.makedirs(tdir, exist_ok=True)
        for f in cands:
            small = os.path.join(tdir, os.path.basename(f)[:-4] + ".jpg")
            subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", f, "-vf", "scale=896:-2", "-q:v", "4", small],
                           capture_output=True, creationflags=0x08000000 if WIN else 0)
            try:
                d = json.loads(model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), PICK_ASK.format(want=want),
                                               0.1, True, image_path=small, timeout=300))
                scores[f] = float(d.get("score", 0))
                say(f"   {os.path.basename(f)}: {scores[f]:.0f}/10" + (f" - missing: {d.get('missing')}" if d.get("missing") else "")
                    + (f" - flaws: {d.get('flaws')}" if d.get("flaws") else ""))
            except Exception as e:
                say(f"   (vision AI could not judge {os.path.basename(f)}: {e})")
        if scores:
            best = max(scores, key=scores.get)
    if not scores:
        say("   no artwork candidate passed review; keeping all candidates for manual choice")
        free_llm(ctx)
        return False
    if scores[best] < 6:
        say(f"   best artwork scored {scores[best]:.0f}/10; keeping candidates for repaint or manual choice")
        free_llm(ctx)
        return False
    dst = os.path.join(out, "001.png")
    if os.path.exists(dst):
        os.replace(dst, os.path.join(out, time.strftime("_old_%H%M%S_001.png")))
    shutil.copyfile(best, dst)
    if os.path.exists(best[:-4] + ".prompt.txt"):
        shutil.copyfile(best[:-4] + ".prompt.txt", dst[:-4] + ".prompt.txt")
    json.dump({**meta, "chosen": os.path.basename(best), "scores": {os.path.basename(k): v for k, v in scores.items()}},
              open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(f"   artwork: {os.path.basename(best)} -> 001.png  (the other versions stay in {out} - use one instead any time)")
    free_llm(ctx)
    return True


HOT_DRINK = re.compile(r"\b(tea|chai|coffee|cup|mug|kettle|teapot|cocoa|soup)\b|चाय|कप|कॉफी|केतली", re.I)


def no_steam(prompt, negative=""):
    """Hot drinks are painted WITHOUT steam: the video adds real, moving steam later (elements library)."""
    if not HOT_DRINK.search(prompt or ""):
        return prompt, negative
    prompt += ". The air above the cup is clear: no steam, no smoke, no vapour"
    negative = ", ".join(x for x in (negative, "steam, smoke, vapour, mist above the cup") if x)
    return prompt, negative



def comfy_images(ctx, out, style="sukoon_night", size="16:9", engine=None, seed=None, prompts=None, prefix="",
                 steps=None, negative="", ref="", denoise=None, silhouette=False, variants=1):
    cfg, say = ctx["cfg"], ctx["say"]
    free_llm(ctx)                                     # graphics card to ComfyUI alone
    os.makedirs(out, exist_ok=True)
    w, h = SIZES.get(size, SIZES["16:9"])[:2]
    look = look_text(cfg, style, ref)
    if look and (ref or style in THEMES):
        say(f"   look of {'your thumbnail ' + os.path.basename(ref) if ref else 'your ' + THEMES[style]}: {look[:160]}...")
    init = None
    if ref and os.path.exists(ref) and denoise not in (None, "", 0):
        if not comfy_running(cfg):
            start_comfy(cfg, say)
        init = upload_image(cfg, ref)
        say(f"   starting from {os.path.basename(ref)} (keeps {int(round((1 - float(denoise)) * 100))}% of its look)")
    sj = os.path.join(out, "shots.json")
    if prompts:
        shots = []
        for i, pr in enumerate(prompts):
            base = f"{prefix or 'img'}_{time.strftime('%H%M%S')}_{i + 1}"
            shots.append({"file": base + ".png", "prompt": pr})
    else:
        _m = json.load(open(sj, encoding="utf-8"))
        shots = _m["shots"] + (_m.get("moments") or [])
    if not comfy_running(cfg):
        start_comfy(cfg, say)
    engine = engine or cfg.get("image_engine", "z_image_turbo")
    for i, s in enumerate(shots):
        if ctx["stopped"]():
            return False
        f = os.path.join(out, s["file"])
        if os.path.exists(f) and not prompts and (int(variants) < 2 or s.get("picked") or
                                                  os.path.exists(f[:-4] + "_b.png")):
            say(f"   {s['file']} already there"); continue
        subj = s["prompt"]
        if silhouette:
            subj = f"{subj}. The main subject is a dark silhouette (solid shape, no face details) against the glowing light"
        full = subj + (". " + look if look else "")
        full, neg = no_steam(full, negative)
        sd = int(seed) + i if seed not in (None, "") else random.randint(1, 2 ** 31)
        t0 = time.time()
        st = int(steps) if steps not in (None, "", 0) else 8
        if not os.path.exists(f) or prompts:
            comfy_generate(cfg, full, f, engine, sd, w, h, steps=st, negative=neg, init_image=init,
                           denoise=float(denoise or 0.8))
        for k in range(1, int(variants)):             # more versions: the vision AI keeps the best (pick_scenes)
            fb = f[:-4] + "_" + "bcd"[k - 1] + ".png"
            if not os.path.exists(fb):
                comfy_generate(cfg, full, fb, engine, sd + 1000 * k, w, h, steps=st, negative=neg)
        open(f[:-4] + ".prompt.txt", "w", encoding="utf-8").write(
            f"{full}\n\nseed {sd}  engine {engine}  {w}x{h}  steps {st}" + (f"\nnegative: {neg}" if neg else "") + "\n")
        say(f"   {s['file']}  ({time.time() - t0:.0f} s, seed {sd})")
    if not prompts:
        json.dump({**json.load(open(sj, encoding="utf-8")), "style": style, "size": size}, open(sj, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)


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


def _judge(cfg, img, ask, reference_path=None):
    small = img[:-4] + "_judge.jpg"
    subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", img, "-vf", "scale=768:-2", "-q:v", "4", small],
                   capture_output=True, creationflags=0x08000000 if WIN else 0)
    ref_small = None
    if reference_path and os.path.exists(reference_path):
        ref_small = reference_path[:-4] + "_refjudge.jpg"
        subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", reference_path, "-vf", "scale=768:-2",
                        "-q:v", "4", ref_small], capture_output=True, creationflags=0x08000000 if WIN else 0)
    try:
        paths = [ref_small, small] if ref_small and os.path.exists(ref_small) else [small]
        try:
            raw = model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), ask, 0.1,
                             True, image_paths=paths, timeout=300)
        except Exception:
            if len(paths) == 1:
                raise
            raw = model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), ask, 0.1,
                             True, image_path=small, timeout=300)
        d = json.loads(raw)
        return (float(d.get("score", 0)), str(d.get("flaws", ""))[:180],
                str(d.get("revision", ""))[:300].strip())
    finally:
        try:
            os.remove(small)
        except OSError:
            pass
        if ref_small:
            try:
                os.remove(ref_small)
            except OSError:
                pass


def pick_scenes(ctx, out, genre="bhay", redo=True):
    """every scene was painted in 2 versions: the vision AI keeps the one that shows the scene best and is clean
    (no text, no broken hands / faces); a scene where both are bad is painted twice more and judged again.
    The other versions go to _alt (nothing deleted)."""
    cfg, say = ctx["cfg"], ctx["say"]
    sj = os.path.join(out, "shots.json")
    meta = json.load(open(sj, encoding="utf-8"))
    # Older/partially written scene plans can contain numeric placeholders.
    # The picker expects each entry to be a scene record, so ignore malformed
    # rows explicitly instead of failing later on s["prompt"] / s.get(...).
    scene_records = meta.get("shots") or []
    moment_records = meta.get("moments") or []
    if not isinstance(scene_records, list):
        scene_records = []
        meta["shots"] = scene_records
    if not isinstance(moment_records, list):
        moment_records = []
        meta["moments"] = moment_records
    malformed = sum(not isinstance(s, dict) for s in scene_records + moment_records)
    if malformed:
        say(f"   (skipping {malformed} malformed scene record(s) in shots.json; expected objects)")
        scene_records = [s for s in scene_records if isinstance(s, dict)]
        moment_records = [s for s in moment_records if isinstance(s, dict)]
        meta["shots"], meta["moments"] = scene_records, moment_records
    records = scene_records + moment_records
    if not records:
        raise RuntimeError("shots.json has no valid scene records; regenerate the scene plan before picking images")
    G = GENRES.get(genre, GENRES["bhay"])
    look = meta.get("look", "")[:220]
    alt = os.path.join(out, "_alt")
    os.makedirs(alt, exist_ok=True)

    character_refs = {}

    def choose(s, files):
        character = s.get("character", "")
        reference = character_refs.get(character) if character else None
        anchor_note = ("Two images are attached: first is the previously accepted frame for this same recurring "
                       "character; second is the candidate. Compare only silhouette, clothing and distinctive prop "
                       "continuity, while allowing this scene's camera angle and action to differ."
                       if reference else "Only the candidate image is attached.")
        ask = SCENE_ASK.format(desc=G["desc"], want=s["prompt"][:350],
                               character_anchor=s.get("character_anchor") or "none for this scene",
                               look=look or "the same film look",
                               anchor_note=anchor_note)
        sc, reviews = {}, {}
        for f in files:
            try:
                sc[f], flaws, revision = _judge(cfg, f, ask, reference)
                reviews[f] = (flaws, revision)
            except Exception as e:
                say(f"   (vision AI could not judge {os.path.basename(f)}: {e})")
        if not sc:
            return None, 0.0, ""
        # _judge returns (score, flaws, revision), but sc stores only the
        # numeric score; review text lives separately in `reviews`.
        best = max(sc, key=sc.get)
        say(f"   {s['file']}: " + "  ".join(f"{os.path.basename(k)} {v:.0f}/10" for k, v in sc.items())
            + (f"  - flaws: {reviews[best][0]}" if reviews[best][0] and sc[best] < 7 else ""))
        return best, sc[best], reviews[best][1]

    free_gpu(ctx)
    bad = []
    for s in records:
        if ctx["stopped"]():
            return False
        f = os.path.join(out, s["file"])
        files = [x for x in [f] + [f[:-4] + "_" + c + ".png" for c in "bcd"] if os.path.exists(x)]
        if s.get("picked") or not files:
            continue
        best, score, revision = choose(s, files)      # reviewer returns a targeted prompt correction when needed
        if best and best != f:
            os.replace(f, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(f)))
            os.replace(best, f)
        for x in files:
            if x != f and os.path.exists(x):
                os.replace(x, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(x)))
        s["picked"], s["score"], s["repair_hint"] = True, score, revision
        if s.get("character") and best and score >= 5:
            character_refs.setdefault(s["character"], f)
        if score < 6:
            bad.append(s)
    if bad and redo:
        say(f"   {len(bad)} scene(s) below 6/10 - applying the review's targeted correction and repainting")
        free_llm(ctx)
        w, h = SIZES.get(meta.get("size", "16:9"), SIZES["16:9"])[:2]
        for s in bad:
            f = os.path.join(out, s["file"])
            pt = f[:-4] + ".prompt.txt"
            full = open(pt, encoding="utf-8").read().split("\n\n")[0] if os.path.exists(pt) else s["prompt"]
            revision = s.get("repair_hint") or "Use a clearly different, scene-appropriate camera angle and framing; keep the story's visual look and recurring character silhouette/clothing unchanged."
            repair_prompt = (full + ". Targeted visual correction: " + revision +
                             ". Preserve every correct subject, prop, palette, and character silhouette detail.")
            s["repair_prompt"] = repair_prompt
            for k, c in enumerate("b" if len(bad) > 3 else "bc"):
                comfy_generate(cfg, repair_prompt, f[:-4] + "_" + c + ".png", cfg.get("image_engine", "z_image_turbo"),
                               random.randint(1, 2 ** 31), w, h, steps=8)
        free_gpu(ctx)
        for s in bad:
            f = os.path.join(out, s["file"])
            files = [x for x in [f, f[:-4] + "_b.png", f[:-4] + "_c.png"] if os.path.exists(x)]
            best, score, revision = choose(s, files)
            if best and best != f:
                os.replace(f, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(f)))
                os.replace(best, f)
            for x in files:
                if x != f and os.path.exists(x):
                    os.replace(x, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(x)))
            s["score"], s["repair_hint"] = score, revision
            if s.get("character") and best and score >= 5:
                character_refs.setdefault(s["character"], f)
    unresolved = [s for s in records
                  if s.get("score") is None or s.get("score", 0) < 6]
    json.dump(meta, open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if unresolved:
        say(f"   {len(unresolved)} scene(s) still below 6/10 or unreviewed; stopped before video rendering")
        free_llm(ctx)
        free_gpu(ctx)
        return False
    free_llm(ctx)
    sc = [s.get("score") for s in records if s.get("score") is not None]
    if sc:
        say(f"   scenes judged: average {sum(sc) / len(sc):.1f}/10, lowest {min(sc):.0f}/10")
    free_gpu(ctx)
    return True


def repaint(ctx, dir, file, prompt="", engine="", steps=None, negative=""):
    """one picture of a set again: old one kept as _old_<time>_<file>, new seed, optionally a new prompt"""
    cfg, say = ctx["cfg"], ctx["say"]
    f = os.path.join(dir, file)
    pt = f[:-4] + ".prompt.txt"
    old_prompt = open(pt, encoding="utf-8").read().split("\n\n")[0].strip() if os.path.exists(pt) else ""
    meta = open(pt, encoding="utf-8").read() if os.path.exists(pt) else ""
    m = re.search(r"(\d+)x(\d+)", meta)
    w, h = (int(m.group(1)), int(m.group(2))) if m else SIZES["16:9"][:2]
    me = re.search(r"engine (\S+)", meta)
    engine = engine or (me.group(1) if me else cfg.get("image_engine"))
    full = prompt.strip() or old_prompt
    if not full:
        raise RuntimeError("no prompt for this picture - write one")
    if prompt.strip() and old_prompt and old_prompt not in prompt:         # keep the set's style words
        sj = os.path.join(dir, "shots.json")
        style = json.load(open(sj, encoding="utf-8")).get("style") if os.path.exists(sj) else None
        look = STYLES.get(style or "", ("", ""))[1]
        if look and look not in full:
            full = full + ". " + look
    if os.path.exists(f):
        os.replace(f, os.path.join(dir, time.strftime("_old_%H%M%S_") + file))
    if not comfy_running(cfg):
        start_comfy(cfg, say)
    sd = random.randint(1, 2 ** 31)
    st = int(steps) if steps not in (None, "", 0) else 8
    full, negative = no_steam(full, negative)
    comfy_generate(cfg, full, f, engine, sd, w, h, steps=st, negative=negative)
    open(pt, "w", encoding="utf-8").write(f"{full}\n\nseed {sd}  engine {engine}  {w}x{h}  steps {st}\n")
    say(f"   {file} painted again (seed {sd})")


def _first_image(p):
    if os.path.isfile(p):
        return p
    if os.path.isdir(p):
        ims = sorted(glob.glob(os.path.join(p, "*.png")) + glob.glob(os.path.join(p, "*.jpg")))
        return ims[0] if ims else ""
    return ""


def _audio_of(p):
    """narration folder -> its narration_music.wav (else narration.wav); a file stays a file"""
    if os.path.isdir(p):
        for n in ("narration_music.wav", "narration.wav"):
            if os.path.exists(os.path.join(p, n)):
                return os.path.join(p, n)
        return ""
    return p if os.path.exists(p) else ""


def studio_prepare(ctx, ep, audio, title="", artwork=""):
    cfg, say = ctx["cfg"], ctx["say"]
    a = _audio_of(audio)
    if not a:
        raise RuntimeError(f"no narration audio in {audio}")
    os.makedirs(ep, exist_ok=True)
    name = os.path.basename(ep.rstrip("\\/"))
    mp3 = os.path.join(ep, name + ".mp3")
    rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-i", a, "-b:a", "192k", mp3])
    if rc:
        return False
    src_dir = audio if os.path.isdir(audio) else os.path.dirname(audio)
    # faster + more exact word timing: give the video engine your clean voice (no music, no long music tail),
    # shifted by the music lead so it lines up with the mixed track
    vw, shp = os.path.join(src_dir, "narration.wav"), os.path.join(src_dir, "music_shape.json")
    mixed = os.path.exists(os.path.join(src_dir, "narration_music.wav"))
    if os.path.exists(vw) and (os.path.exists(shp) or not mixed):
        try:
            lead = float(json.load(open(shp, encoding="utf-8")).get("lead_s", 0)) if os.path.exists(shp) else 0.0
            work = os.path.join(ep, cfg.get("episode_work_name", "_work"))
            os.makedirs(work, exist_ok=True)
            ms = int(round(lead * 1000))
            rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-i", vw, "-af", f"adelay={ms}:all=1",
                                 "-ac", "1", "-ar", "16000", os.path.join(work, "voice.wav")])
            if rc == 0:
                say(f"   clean voice for the word timing: {os.path.join(work, 'voice.wav')} (+{lead:.1f} s lead)")
        except Exception as e:
            say(f"   (clean voice not prepared: {e})")
    src_script = os.path.join(src_dir, "script.txt")
    ep_script = os.path.join(ep, "script.txt")
    if os.path.isfile(src_script):
        shutil.copyfile(src_script, ep_script)
        if cfg.get("experimental_mode"):
            script_dir = os.path.join(ep, "01_Script")
            os.makedirs(script_dir, exist_ok=True)
            shutil.copyfile(src_script, os.path.join(script_dir, "script.txt"))
            sj = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".story.json")
            if os.path.isfile(sj):
                shutil.copyfile(sj, os.path.join(script_dir, "story.json"))
    elif os.path.isfile(ep_script):
        # This episode folder may be reused. An old script must never become the
        # subtitle source for narration that has no trusted script of its own.
        old = os.path.join(ep, "_old")
        os.makedirs(old, exist_ok=True)
        stale = os.path.join(old, time.strftime("%H%M%S_stale_script.txt"))
        shutil.move(ep_script, stale)
        say(f"   archived stale episode script: {stale}")
    tf = os.path.join(ep, "title.txt")
    if not title and not os.path.exists(tf):               # the title the AI gave the script (in its language)
        try:
            import story
            sj = os.path.join(ctx_folder(ctx, "scripts"), slug(name) + ".story.json")
            sm = (json.load(open(sj, encoding="utf-8")).get("summary") or {}) if os.path.exists(sj) else {}
            sc = os.path.join(src_dir, "script.txt")
            en = story.is_english(open(sc, encoding="utf-8").read()) if os.path.exists(sc) else False
            title = (sm.get("title_en") if en else sm.get("title_hi")) or ""
        except Exception:
            title = ""
        open(tf, "w", encoding="utf-8").write((title or "Sleep Hypnosis").strip() + "\n")
    img = _first_image(artwork) if artwork else ""
    if img:
        cover_dir = os.path.join(ep, "03_Artwork") if cfg.get("experimental_mode") else ep
        os.makedirs(cover_dir, exist_ok=True)
        shutil.copyfile(img, os.path.join(cover_dir, name + os.path.splitext(img)[1].lower()))
        say(f"   artwork: {img}")
    say(f"   episode folder: {ep}")


def studio_video(ctx, ep, reels=0, clips=0, preview=0, artwork="", opts=None):
    cfg = ctx["cfg"]
    if not os.path.exists(cfg["studio_python"]):
        raise RuntimeError("the video engine is not set up yet - run setup_hub.bat once")
    cmd = [cfg["studio_python"], "-u", "-s", os.path.join("engine", "sleep_pipeline.py"), ep, "--yes",
           "--reels", str(reels), "--clips", str(clips)] + (["--llm", cfg["llm"]] if cfg.get("llm") else [])
    img = _first_image(artwork) if artwork else ""
    if img:
        cmd += ["--image", img]
    if preview:
        cmd += ["--preview", str(preview)]
    o = list(opts or [])
    if "--font" in o:                                    # a font file name from the Studio's fonts -> full path
        i = o.index("--font") + 1
        if not os.path.isabs(o[i]):
            o[i] = os.path.join(fonts_dir(cfg), o[i])
    cmd += o
    env = {"SV_GPU_PYTHON": cfg["gpu_python"], "EPISODES": cfg["episodes_dir"], "STUDIO_WORK": os.path.join(ep, cfg.get("episode_work_name", "_work")), "PYTHONWARNINGS": "ignore",
           "HF_HUB_DISABLE_PROGRESS_BARS": "1", "TRANSFORMERS_VERBOSITY": "error"}
    return ctx["run_cmd"](cmd, cwd=cfg["studio_dir"], env=env) == 0


def _wav_len(p):
    try:
        with wave.open(p) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


def font_family(path):
    """family name inside a .ttf/.otf (the name the subtitle renderer looks for)"""
    import struct
    try:
        data = open(path, "rb").read()
        if data[:4] == b"ttcf":
            data_off = struct.unpack(">I", data[12:16])[0]
        else:
            data_off = 0
        n = struct.unpack(">H", data[data_off + 4:data_off + 6])[0]
        for i in range(n):
            tag, _, off, ln = struct.unpack(">4sIII", data[data_off + 12 + 16 * i:data_off + 28 + 16 * i])
            if tag == b"name":
                cnt, so = struct.unpack(">HH", data[off + 2:off + 6])
                best = None
                for j in range(cnt):
                    pid, eid, lid, nid, l, o = struct.unpack(">HHHHHH", data[off + 6 + 12 * j:off + 18 + 12 * j])
                    if nid in (1, 16):
                        raw = data[off + so + o:off + so + o + l]
                        name = raw.decode("utf-16-be", "ignore") if pid in (0, 3) else raw.decode("latin-1", "ignore")
                        if name and (best is None or nid == 1):
                            best = name
                return best
    except Exception:
        return None
    return None


def _ass_color(hexs, alpha=0):
    h = (hexs or "#ffffff").lstrip("#")
    if len(h) != 6:
        h = "ffffff"
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ass_time(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_ass(path, timing, W, H, c, font_name):
    size = int(float(c.get("size") or 54) * H / 1080)
    pos = c.get("position", "bottom")
    align = {"bottom": 2, "center": 5, "top": 8}.get(pos, 2)
    marg = int(H * (0.09 if pos != "center" else 0))
    words = [w.strip() for w in re.split(r"[,\n]", c.get("words") or "") if w.strip()]
    col, hi = _ass_color(c.get("color", "#ffffff")), _ass_color(c.get("highlight", "#e0a75e"))
    outline = 3 if c.get("outline", True) else 0
    hdr = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           "MarginR, MarginV, Encoding\n"
           f"Style: Cap,{font_name},{size},{col},{col},&H90000000,&H00000000,{1 if c.get('bold') else 0},0,0,0,100,100,"
           f"0,0,1,{outline},1,{align},{int(W * .08)},{int(W * .08)},{marg},1\n\n[Events]\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    for x in timing:
        t = re.sub(r"\.{2,}|…", " ", x["text"]).replace("\n", " ").strip(" .।")
        t = re.sub(r"[{}\\]", "", t)
        if not t:
            continue
        toks = t.split()
        if c.get("auto_emph") and toks:
            toks[-1] = "\x01" + toks[-1] + "\x02"
        out = []
        for w in toks:
            bare = w.strip("\x01\x02,.!?।")
            if "\x01" in w or any(bare == k or bare.startswith(k) for k in words):
                out.append("{\\c&H" + hi[4:] + "&\\b1}" + w.replace("\x01", "").replace("\x02", "") + "{\\r}")
            else:
                out.append(w)
        ev.append(f"Dialogue: 0,{_ass_time(x['start'])},{_ass_time(x['end'] + 0.25)},Cap,,0,0,0,,"
                  "{\\fad(300,300)}" + " ".join(out))
    open(path, "w", encoding="utf-8-sig").write(hdr + "\n".join(ev) + "\n")
    return len(ev)


def slideshow(ctx, pictures, audio, out, size="16:9", order=None, captions=None):
    cfg, say = ctx["cfg"], ctx["say"]
    a = _audio_of(audio)
    if not a:
        raise RuntimeError("no audio")
    sj = os.path.join(pictures, "shots.json")
    shots = json.load(open(sj, encoding="utf-8"))["shots"] if os.path.exists(sj) else \
        [{"file": os.path.basename(f), "start": None} for f in sorted(glob.glob(os.path.join(pictures, "*.png")))]
    by = {s["file"]: s for s in shots}
    total = _wav_len(a) or 60.0
    if order:                                                  # your order / removals / lengths from the editor
        plan = [{"file": o["file"], "secs": o.get("secs")} for o in order if os.path.exists(os.path.join(pictures, o["file"]))]
        fixed = sum(float(p["secs"]) for p in plan if p["secs"])
        free = [p for p in plan if not p["secs"]]
        rest = max(1.0, total - fixed) / max(1, len(free))
        for p in plan:
            p["d"] = float(p["secs"]) if p["secs"] else rest
    else:
        shots = [s for s in shots if os.path.exists(os.path.join(pictures, s["file"]))]
        starts = [s.get("start") for s in shots]
        if any(x is None for x in starts):
            starts = [total * i / len(shots) for i in range(len(shots))]
        starts[0] = 0.0
        plan = [{"file": s["file"], "d": (starts[i + 1] if i + 1 < len(shots) else total) - starts[i]}
                for i, s in enumerate(shots)]
    if not plan:
        raise RuntimeError("no pictures in the set")
    W, Hh = (1920, 1080) if size != "9:16" else (1080, 1920)
    fps = 25
    tmp = os.path.join(ctx_folder(ctx, "temporary"), ctx["job"]["id"])
    os.makedirs(tmp, exist_ok=True)
    lst = []
    for i, s in enumerate(plan):
        d = max(1.0, s["d"])
        n = int(round(d * fps))
        f = os.path.join(tmp, f"c{i:03d}.mp4")
        z = "min(1.0+0.12*on/{n},1.12)" if i % 2 == 0 else "max(1.12-0.12*on/{n},1.0)"   # gentle zoom in / out
        vf = (f"scale={W * 2}:{Hh * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{Hh * 2},"
              f"zoompan=z='{z.format(n=n)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={W}x{Hh}:fps={fps},"
              f"fade=t=in:st=0:d=0.8,fade=t=out:st={max(0.1, d - 0.8):.2f}:d=0.8,format=yuv420p")
        say(f"   picture {i + 1}/{len(plan)}  {s['file']}  ({d:.1f} s)")
        rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-i", os.path.join(pictures, s["file"]), "-vf", vf,
                             "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", f])
        if rc or ctx["stopped"]():
            return False
        lst.append(f)
    cl = os.path.join(tmp, "list.txt")
    open(cl, "w", encoding="utf-8").write("".join(f"file '{p.replace(os.sep, '/')}'\n" for p in lst))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    c = captions or {}
    vf_sub = []
    src_dir = audio if os.path.isdir(audio) else os.path.dirname(audio)
    tj = os.path.join(src_dir, "timing.json")
    if c.get("on") and os.path.exists(tj):
        fdir = os.path.join(tmp, "fonts")
        os.makedirs(fdir, exist_ok=True)
        fname = "Nirmala UI"
        if c.get("font"):
            fp = c["font"] if os.path.isabs(c["font"]) else os.path.join(fonts_dir(cfg), c["font"])
            if os.path.exists(fp):
                shutil.copyfile(fp, os.path.join(fdir, os.path.basename(fp)))
                fname = font_family(fp) or fname
        tim = json.load(open(tj, encoding="utf-8"))
        msj = os.path.join(src_dir, "music_shape.json")
        if os.path.exists(msj) and os.path.basename(a) == "narration_music.wav":   # the voice starts later there
            lead = float(json.load(open(msj, encoding="utf-8")).get("lead_s", 0))
            tim = [dict(x, start=x["start"] + lead, end=x["end"] + lead) for x in tim]
        n = make_ass(os.path.join(tmp, "subs.ass"), tim, W, Hh, c, fname)
        say(f"   captions: {n} lines, font {fname}" + (f", highlighted: {c.get('words')}" if c.get("words") else ""))
        vf_sub = ["-vf", "ass=subs.ass:fontsdir=fonts"]
    elif c.get("on"):
        say("   (no timing.json next to the narration - captions skipped)")
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"] if vf_sub else ["-c:v", "copy"]
    rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", cl, "-i", a] + vf_sub
                        + enc + ["-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], cwd=tmp)
    if rc and vf_sub:
        say("   captions could not be drawn (this FFmpeg has no subtitle support?) - making it without captions")
        rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", cl, "-i", a,
                             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out])
    if rc == 0:
        say(f"   video: {out}")
    return rc == 0


# ------------------------------------------------------------------ status / library / system
def gpu_info():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4,
                           creationflags=0x08000000 if WIN else 0)
        n, used, tot, util = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        return {"name": n, "mem_used": int(used), "mem_total": int(tot), "util": int(util)}
    except Exception:
        return None


_gpu_cache = {"t": 0, "v": None}


def status(cfg, jobs):
    if time.time() - _gpu_cache["t"] > 3:
        _gpu_cache.update(t=time.time(), v=gpu_info())
    ol = False
    try:
        _get(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/tags", 2); ol = True
    except OSError:
        pass
    js = jobs.jobs if jobs else []
    run = next((j for j in js if j["status"] == "running"), None)
    free = None
    try:
        free = round(shutil.disk_usage(cfg["media_dir"]).free / 2 ** 30, 1)
    except OSError:
        pass
    eng = {"voice": os.path.isdir(os.path.join(cfg["sukoonvoice_dir"], "libs", "cb")),
           "studio": os.path.exists(cfg["studio_python"])}
    return {"comfy": comfy_running(cfg), "ollama": ol,
            "gpu": _gpu_cache["v"], "disk_free_gb": free, "engines": eng,
            "running": run and {"id": run["id"], "title": run["title"], "step": run.get("step", 0),
                                "n_steps": len(run.get("steps") or []), "step_label": run.get("step_label", "")},
            "queued": sum(1 for j in js if j["status"] == "queued")}


# ------------------------------------------------------------------ suggestions (the hub ASKS - nothing runs by itself)
def _dismissed_path(cfg):
    return os.path.join(cfg["media_dir"], "suggestions_dismissed.json")


def dismiss(cfg, sid):
    p = _dismissed_path(cfg)
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        d = {}
    d[sid] = time.time()
    json.dump(d, open(p, "w", encoding="utf-8"), indent=1)


def suggestions(cfg, jobs):
    """small, useful next steps found by looking at what exists (no AI, no graphics card). Each one has a button;
    nothing happens until it is clicked. Dismissed ones stay away (30 days)."""
    try:
        gone = {k: v for k, v in json.load(open(_dismissed_path(cfg), encoding="utf-8")).items() if v > time.time() - 30 * 86400}
    except Exception:
        gone = {}
    js = jobs.jobs if jobs else []
    busy = {(j.get("params") or {}).get("video") for j in js if j["status"] in ("queued", "running")}
    out = []
    kinds = {}

    def add(sid, text, why, button, action, prio=5, kind="", most=9):
        if sid in gone or kinds.get(kind or sid, 0) >= most:
            return
        kinds[kind or sid] = kinds.get(kind or sid, 0) + 1
        out.append({"id": sid, "text": text, "why": why, "button": button, "action": action, "prio": prio})

    for e in episodes(cfg)[:8]:
        v = e["video"]
        if not v or v in busy:
            continue
        rep = os.path.splitext(v)[0] + ".check.json"
        if os.path.exists(rep):
            try:
                r = json.load(open(rep, encoding="utf-8"))
            except Exception:
                r = {}
            n = len(r.get("voice") or []) + len([m for m in r.get("motion") or [] if m.get("kind") != "abrupt camera move"])
            if n and not r.get("auto"):
                add("repair:" + v, f"{e['name']}: {n} thing(s) to fix", "found by the last check (voice lines / motion)",
                    "Open Repair", {"page": "repair", "pick": v}, 2, "repair", 2)
        elif time.time() - os.path.getmtime(v) < 3 * 86400:
            add("check:" + v, f"Check {e['name']} for glitches", "stutters, wrong words, jumps - no graphics card, runs beside other jobs",
                "Check it", {"kind": "check_video", "body": {"video": v}}, 6, "check", 1)
        if not e["reels"] and time.time() - os.path.getmtime(v) < 7 * 86400:
            add("reels:" + v, f"Make 3 reels from {e['name']}", "it has no reels yet", "Open Reels", {"page": "reels", "pick": v},
                5, "reels", 1)
    try:
        import story
        unused = 0
        for f in glob.glob(os.path.join(cfg["scripts_dir"], "*.txt")):
            if re.search(r"(_old_\d+|_full|\.en)\.txt$", f):
                continue
            nm = os.path.basename(f)[:-4]
            if not os.path.exists(os.path.join(narration_dir(cfg, nm, existing=True), "narration.wav")):
                unused += 1
        if unused == 0 and not any(j["kind"] in ("stories", "stories_local") and j["status"] in ("queued", "running") for j in js):
            add(time.strftime("stories:%Y%m%d"), "Write 5 fresh scripts", "every written script has been used",
                "Write them", {"kind": "stories", "body": {"n": 5, "speech_min": 12, "writer": "auto"}}, 4)
        t = story.pick_topic(cfg, seed=int(time.strftime("%Y%m%d")))
        add("topic:" + t, f"Next episode idea: {t}", "a topic your earlier episodes have not used yet",
            "Use it", {"page": "episode", "topic": t}, 7)
    except Exception:
        pass
    if not load_index(cfg).get("themes", {}).get("sukoon"):
        add("style_index", "Teach the hub your thumbnail look", "artwork then matches your Sukoon thumbnails",
            "Build it", {"kind": "style_index", "body": {}}, 3)
    if not os.path.isdir(os.path.join(en_dir(cfg), "libs", "qwen")):
        add("english_setup", "Set up the English narrator", "needed for English episodes: copies its libraries "
            "(~0.9 GB, from MukhtalifAudioBook) and speaks 3 test lines", "Set it up", {"kind": "english_setup", "body": {}}, 1)
    for j in sorted(js, key=lambda j: -(j.get("ended") or 0))[:10]:
        if j["status"] == "failed" and (j.get("ended") or 0) > time.time() - 86400:
            add("again:" + j["id"], f"Failed: {j['title']}", (j.get("step_label") or "") + " - see Activity",
                "Open Activity", {"page": "jobs", "job": j["id"]}, 2, "failed", 2)
    out.sort(key=lambda x: x["prio"])
    return out[:6]


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def library(cfg, kind="all"):
    out = {}
    if kind in ("all", "narrations"):
        L = []
        roots = {narrations_root(cfg), os.path.join(cfg["sukoonvoice_media"], "narrations")}
        for root in roots:
            for d in glob.glob(os.path.join(root, "*")):
                n = os.path.basename(d)
                if n.startswith("_") or not os.path.isdir(d):
                    continue
                v, m = os.path.join(d, "narration.wav"), os.path.join(d, "narration_music.wav")
                if not os.path.exists(v):
                    continue
                flagged = 0
                rp = os.path.join(d, "report.txt")
                if os.path.exists(rp):
                    flagged = sum(1 for l in open(rp, encoding="utf-8", errors="replace") if re.match(r"\s+\d+:", l))
                L.append({"name": n, "dir": d, "voice": v, "music": m if os.path.exists(m) else "",
                          "seconds": round(_wav_len(v)), "time": _mtime(v), "flagged": flagged,
                          "lang": __import__("story").narration_lang(d),
                          "script": os.path.join(d, "script.txt")})
        out["narrations"] = sorted(L, key=lambda x: -x["time"])[:200]
    if kind in ("all", "pictures"):
        sets = []
        for d in glob.glob(os.path.join(cfg["pictures_dir"], "*")):
            ims = sorted(f for f in glob.glob(os.path.join(d, "*.png")) if not os.path.basename(f).startswith("_old_"))
            if ims:
                sj = os.path.join(d, "shots.json")
                shots = json.load(open(sj, encoding="utf-8")).get("shots", []) if os.path.exists(sj) else []
                sets.append({"name": os.path.basename(d), "dir": d, "images": ims[:60], "time": max(map(_mtime, ims)),
                             "shots": [{k: x.get(k) for k in ("file", "start", "end", "text")} for x in shots]})
        singles = sorted([f for f in glob.glob(os.path.join(cfg["images_dir"], "*", "*"))
                          if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))], key=_mtime, reverse=True)
        art = []
        if cfg.get("artwork_dir") and os.path.isdir(cfg["artwork_dir"]):
            art = sorted([f for f in glob.glob(os.path.join(cfg["artwork_dir"], "*"))
                          if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))], key=_mtime, reverse=True)
        out["picture_sets"] = sorted(sets, key=lambda x: -x["time"])
        out["images"] = singles[:120]
        out["artwork"] = art[:120]
    if kind in ("all", "videos"):
        vids = []
        for f in glob.glob(os.path.join(cfg["videos_dir"], "*.mp4")):
            vids.append({"file": f, "kind": "video", "time": _mtime(f)})
        for f in glob.glob(os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), "*.mp4")):
            if not f.endswith(".part.mp4"):
                vids.append({"file": f, "kind": "episode", "time": _mtime(f)})
        for f in glob.glob(os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), cfg.get("layout", {}).get("episode_reels", "reels"), "*.mp4")):
            vids.append({"file": f, "kind": "reel", "time": _mtime(f)})
        out["videos"] = sorted(vids, key=lambda x: -x["time"])[:150]
    if kind in ("all", "scripts"):
        sc = sorted([f for f in glob.glob(os.path.join(cfg["scripts_dir"], "*.txt"))
                     if not re.search(r"(_old_\d+|_full|\.en)\.txt$", f)], key=_mtime, reverse=True)
        out["scripts"] = []
        for f in sc[:200]:                                   # a short preview only - the page loads the text when used
            with open(f, encoding="utf-8", errors="replace") as fh:
                head = fh.read(600)
            out["scripts"].append({"name": os.path.basename(f)[:-4], "file": f, "time": _mtime(f),
                                   "preview": " ".join(l for l in head.splitlines() if l.strip() and l.strip() != "(...)")[:160]})
    if kind in ("all", "episodes"):
        out["episodes"] = episodes(cfg)
        # Build the automatic narration cadence index as the user browses episodes.
        # Profiles come from script + timing metadata; this does not rate audio quality.
        try:
            import pacing
            pacing.refresh(cfg, out["episodes"])
        except Exception:
            pass
    return out


def episodes(cfg):
    """one entry per episode: everything that belongs to it, wherever it lives"""
    eps = []
    for d in glob.glob(os.path.join(cfg["episodes_dir"], "*")):
        n = os.path.basename(d)
        if n.startswith("_") or not os.path.isdir(d):
            continue
        o = os.path.join(d, cfg.get("episode_output_name", "Output"))
        vids = [f for f in glob.glob(os.path.join(o, "*.mp4")) if not f.endswith((".part.mp4",)) and "_preview" not in f
                and not os.path.basename(f).lower().startswith("preview")]
        vids.sort(key=_mtime, reverse=True)
        reels = sorted(f for f in glob.glob(os.path.join(o, cfg.get("layout", {}).get("episode_reels", "reels"), "*.mp4")) if not f.endswith(".part.mp4"))
        pics = [f for f in glob.glob(os.path.join(d, "*")) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
        nar = narration_dir(cfg, n, existing=True)
        sp = os.path.join(cfg["scripts_dir"], slug(n) + ".txt")
        times = [_mtime(f) for f in vids + reels + pics] or [_mtime(d)]
        eps.append({"name": n, "dir": d, "video": vids[0] if vids else "", "reels": reels,
                    "art": pics[0] if pics else "", "narration": nar if os.path.isdir(nar) else "",
                    "script": sp if os.path.exists(sp) else (os.path.join(d, "script.txt") if os.path.exists(os.path.join(d, "script.txt")) else ""),
                    "checked": os.path.exists(os.path.splitext(vids[0])[0] + ".check.json") if vids else False,
                    "lang": __import__("story").narration_lang(nar) if os.path.isdir(nar) else "hi",
                    "time": max(times)})
    return sorted(eps, key=lambda x: -x["time"])


def library_stamp(cfg):
    """a cheap fingerprint of what exists - the page asks for it every few seconds and reloads only when it changed"""
    pats = [os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), "*.mp4"),
            os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), cfg.get("layout", {}).get("episode_reels", "reels"), "*.mp4"),
            os.path.join(narrations_root(cfg), "*", "narration.wav"),
            os.path.join(cfg.get("narrations_dir") or os.path.join(cfg["sukoonvoice_media"], "narrations"), "*", "narration.wav"),
            os.path.join(cfg["scripts_dir"], "*.txt"), os.path.join(cfg["pictures_dir"], "*", "*.png"),
            os.path.join(cfg["images_dir"], "*", "*"),
            os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), "*.check.json"),
            os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), cfg.get("layout", {}).get("episode_reels", "reels"), "*.check.json")]
    n, t = 0, 0.0
    for p in pats:
        for f in glob.glob(p):
            n += 1
            t = max(t, _mtime(f))
    return {"stamp": f"{n}-{int(t)}"}


def remove_item(cfg, path):
    """'Remove' = moved to an _old folder next to it (never deleted); returns where it went"""
    if not path or not os.path.exists(path):
        raise ValueError("not found")
    parent = os.path.dirname(path.rstrip("\\/"))
    dst_dir = os.path.join(parent, "_old")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(path.rstrip("\\/")))
    shutil.move(path, dst)
    if not os.path.isdir(dst):                               # a file: its small companions go with it
        base, dbase = os.path.splitext(path)[0], os.path.splitext(dst)[0]
        for side in (".story.json", "_caption.txt", ".check.json", ".reel_info.json", ".prompt.txt"):
            if os.path.exists(base + side):
                shutil.move(base + side, dbase + side)
    return dst


def open_path(p):
    if WIN:
        if p.lower().endswith(".bat"):
            subprocess.Popen(["cmd", "/c", "start", "", p], cwd=os.path.dirname(p))
        else:
            os.startfile(p if os.path.isdir(p) else os.path.dirname(p) if not os.path.exists(p) else p)
    else:
        subprocess.Popen(["xdg-open", p])


def system_action(action, cfg, jobs, model_key=None, backend=None):
    if action == "start_comfy":
        if comfy_running(cfg):
            return {"ok": True, "msg": "ComfyUI is already running"}
        import threading
        threading.Thread(target=lambda: start_comfy(cfg, lambda s: None), daemon=True).start()
        return {"ok": True, "msg": "ComfyUI is starting (about 1 minute)"}
    if action == "free_gpu":
        if jobs and any(j["status"] == "running" for j in jobs.jobs):
            return {"ok": False, "msg": "A HubGPT job is running. Stop it and wait until it finishes before unloading GPU models."}
        log = []
        free_gpu({"cfg": cfg, "say": log.append, "include_llm": True})
        success = not any(("failed" in x.lower() or "could not" in x.lower()) for x in log)
        return {"ok": success, "msg": " ".join(x.strip() for x in log) or "No local models were loaded."}
    if action == "start_ollama":
        if jobs and any(j["status"] == "running" for j in jobs.jobs):
            return {"ok": False, "msg": "A HubGPT job is running. Wait for it to finish before changing local AI servers."}
        url = cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
        try:
            data = json.loads(_get(url + "/api/tags", 2)).get("models", [])
            return {"ok": True, "started": False,
                    "msg": f"Ollama is already running with {len(data)} model(s). Choose a model below."}
        except OSError:
            pass
        exe = cfg.get("ollama_exe") or shutil.which("ollama")
        if not exe or not os.path.isfile(exe):
            return {"ok": False, "msg": f"Ollama executable not found: {exe or 'ollama'}"}
        log_path = os.path.join(cfg["hub_logs_root"], "ollama.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        env = os.environ.copy()
        env["OLLAMA_MODELS"] = cfg.get("ollama_models", r"D:\AI\ollama_models")
        try:
            with open(log_path, "ab") as output:
                proc = subprocess.Popen([exe, "serve"], cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=subprocess.STDOUT, env=env,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            return {"ok": False, "msg": f"Could not start Ollama: {e}"}
        return {"ok": True, "started": True,
                "msg": f"Starting Ollama (PID {proc.pid}). HubGPT will list its installed models when the API is ready. Log: {log_path}"}
    return {"ok": False, "msg": f"unknown action {action}"}
