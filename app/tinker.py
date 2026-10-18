"""Tinker: settings you can change in Experimental mode, and promotion of them into Working mode.

Experimental settings live in <experimental_root>/Tinker/settings.json and only reach Experimental jobs:
each job takes a snapshot when it is queued (params["_tinker"]), so a queued or resumed run keeps the
settings it started with.

Promotion writes into the stores Working mode already reads, after an _old_<timestamp> backup of each file:
  models          llm / prompt_model / vision_llm -> hub_settings.json (same file as the Settings page)
                  image_engine                    -> config.json
                  local_model / story_engine / gemini_model -> secrets.json (same keys as Settings)
  prompts, tables -> working_tinker.json in the hub folder (applied when the hub starts)
  genre rules     -> genre_rules.json in the working media folder
"""
import copy
import importlib
import json
import os
import re
import shutil
import threading
import time

APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP)
WORKING_FILE = os.path.join(ROOT, "working_tinker.json")
CONFIG_FILE = os.path.join(ROOT, "config.json")

MODELS = [
    # key, label, where the choices come from, help
    ("local_model", "Script writer (local model)", "ollama", "Writes scripts when Gemini is off or fails."),
    ("story_engine", "Script engine", ["auto", "local"], "auto = Gemini first when a key is set; local = Ollama only."),
    ("gemini_model", "Gemini model", "gemini", "First Gemini model tried for scripts."),
    ("llm", "General model", "ollama", "Scene plans, emotion tags, translations, reels picks."),
    ("prompt_model", "Image prompt model", "ollama", "Turns scenes into image prompts."),
    ("director_model", "Scene director (genre videos)", "ollama",
     "Reads each scene and writes what happens, the look, sounds and close-ups. Empty = Script writer model."),
    ("scene_picture_model", "Scene picture prompts (genre videos)", "ollama",
     "Writes each picture prompt from the scene's 'what happens' line, and fixes invented props. Empty = Script writer model."),
    ("scene_seconds", "Scene length in seconds (genre videos)", ["20", "25", "30", "35", "40", "45", "60"],
     "Experimental: scenes end at a sentence end near this length; shorter = more pictures. Empty = 30."),
    ("end_seconds", "Ending after the voice in seconds (genre videos)", ["4", "5", "7", "10", "15", "30", "60"],
     "Experimental, when the Create page's 'Whole video' is 0: the music plays this long after the voice, then fades. "
     "The Subscribe + bell animation plays at its end and needs about 8. Empty = 10."),
    ("subscribe_end", "Subscribe + bell animation at the end (genre videos)", ["on", "off"],
     "Experimental: a Subscribe button is clicked and the bell rings over the last seconds (art and sounds made by "
     "the hub, nothing downloaded). Not on reels. Empty = on."),
    ("vision_llm", "Picture judge (vision)", "ollama", "Looks at painted pictures and picks or repaints."),
    ("image_engine", "Image engine (ComfyUI)", "engines", "ComfyUI workflow used to paint pictures."),
]
SECRET_KEYS = {"local_model", "story_engine", "gemini_model"}
CFG_KEYS = {"llm", "prompt_model", "vision_llm", "image_engine", "director_model", "scene_picture_model", "scene_seconds",
            "end_seconds", "subscribe_end"}

PROMPT_MODULES = ("story", "tools", "emotion_music")
PROMPT_LABELS = {
    "story.RULES": "Sukoon script rules (Hindi)",
    "story.CHECK": "Sukoon checklist pass (Hindi)",
    "story.RULES_EN": "Sukoon script rules (English)",
    "story.CHECK_EN": "Sukoon checklist pass (English)",
    "story.SPOKEN_HINDI_CADENCE": "Spoken Hindi cadence (already folded into the rules above)",
    "story.SHORTEN": "Shorten a Sukoon script",
    "story.SHORTEN_STORY": "Shorten a genre story",
    "story.RULES_HORROR": "Bhay (horror) script rules",
    "story.OUTLINE_ASK": "Experimental genre stories: the plan made before writing (facts, clues with answers, one twist, beats)",
    "story.LOGIC_ASK": "Experimental genre stories: logic check against the plan (rewrites only the lines that break it)",
    "story.QUOTE_RULE": "Experimental genre stories: how quotes are written (added with the plan)",
    "story.SOUND_WORD_RULE": "Experimental genre stories: no sound words read aloud (ठक... ट्रिंग...) - the video plays the sound",
    "tools.SCENES_ASK": "Scene planner (genre videos)",
    "tools.SCENE_ASK": "Single scene prompt",
    "tools.ART_ASK": "Sukoon artwork designer",
    "tools.PICK_ASK": "Picture picker (vision)",
    "tools.VISION_ASK": "Picture describer (vision)",
    "tools.TRANSLATE_ASK": "Translate to Hindi",
    "emotion_music.MAP_ASK": "Emotion map: mood and weight per scene, turn lines (music that follows the story)",
}
TABLES = {
    "tools.GENRES": "Series machine settings: music folders, voice, expressiveness (ex), pause, temperature, drama",
    "tools.STYLES": "Image styles: [label, style prompt]",
    "tools.DIRECTION": "Narration direction per series (where to pause and breathe)",
    "story.STORY_PLAN": "Experimental genre stories: plan first + logic check (on, outline_think, check_think, ctx, max_fixes, review = old editorial pass too)",
    "tools.SERIES_LOOK": "Experimental: fixed picture look per series (on, keep_era, look; lead = the medium put first in every shot) instead of the director's look",
    "sfx_lib.SERIES": "Sound effects per series: [effect list, effects per minute]",
    "sound_words.SOUND_WORDS": "Sound words taken out of the speech and played as effects: on, pause_s, kinds (words in Hindi / Roman, library file names best first)",
    "audio_screen.LIMITS": "Audio screening limits: speech band share, piercing highs, music ducking, clarity dB",
    "create_form.DEFAULTS": "Create page: each series' starting values (the same settings for every series)",
    "narrator.SERIES": "Corner narrator per series: on, narrator, corner / size / margin for the video and reels (Narrator page)",
    "narrator.THEMES": "Corner narrator look per series for ComfyUI pictures",
    "emotion_music.SERIES": "Music that follows the emotion, per series: on, sections, max_tracks (3 at most), min_section_s, "
                            "crossfade_s, curve, strength, stingers, budget of turns per weight, drop_s, soft_note, return_mood",
}

_ORIG = {}            # "module.NAME" -> the value written in the code (before any working override)
_ACTIVE = threading.local()   # .secrets: story.secret() answers for the Experimental job on this thread
_PH = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


# ------------------------------------------------------------------ files
def _load(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(default)


def _write(path, data, indent=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def backup(path):
    """file -> file_old_YYYYMMDD_HHMMSS next to it (the hub's usual backup name); '' when there was no file"""
    if not os.path.isfile(path):
        return ""
    out = f"{path}_old_{time.strftime('%Y%m%d_%H%M%S')}"
    n = 1
    while os.path.exists(out):
        n += 1
        out = f"{path}_old_{time.strftime('%Y%m%d_%H%M%S')}_{n}"
    shutil.copy2(path, out)
    return out


def exp_dir(cfg):
    import experimental_workspace
    return os.path.join(experimental_workspace.root(cfg), "Tinker")


def exp_file(cfg):
    return os.path.join(exp_dir(cfg), "settings.json")


def exp_settings(cfg):
    s = _load(exp_file(cfg), {})
    for k in ("models", "prompts", "tables"):
        s.setdefault(k, {})
    return s


def working_settings():
    s = _load(WORKING_FILE, {})
    for k in ("prompts", "tables"):
        s.setdefault(k, {})
    return s


def _exp_media(cfg):
    import experimental_workspace
    L = cfg.get("layout") or {}
    return os.path.join(experimental_workspace.root(cfg), L.get("experimental_library", "Library"))


# ------------------------------------------------------------------ module values
def _mod(name):
    return importlib.import_module(name)


def _get(key):
    m, n = key.split(".", 1)
    return getattr(_mod(m), n)


def _set(key, value):
    m, n = key.split(".", 1)
    setattr(_mod(m), n, value)


def _remember(key):
    if key not in _ORIG:
        _ORIG[key] = copy.deepcopy(_get(key))
    return _ORIG[key]


def merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def prompt_keys():
    keys = []
    for m in PROMPT_MODULES:
        mod = _mod(m)
        for n, v in vars(mod).items():
            key = f"{m}.{n}"
            if n.isupper() and not n.startswith("_") and isinstance(_ORIG.get(key, v), str) and len(_ORIG.get(key, v)) >= 150:
                keys.append(key)
    return sorted(keys, key=lambda k: (k not in PROMPT_LABELS, list(PROMPT_LABELS).index(k) if k in PROMPT_LABELS else 0, k))


def _working_value(section, key, ws=None):
    ws = ws if ws is not None else working_settings()
    base = _remember(key)
    if section == "prompts":
        return ws["prompts"].get(key, base)
    return merge(base, ws["tables"].get(key) or {})


def _apply_working_key(section, key, ws=None):
    _set(key, _working_value(section, key, ws))


def install(cfg):
    """at hub start: remember the code values, route story.secret() through overrides, apply working_tinker.json"""
    for key in prompt_keys():
        _remember(key)
    for key in TABLES:
        try:
            _remember(key)
        except (ImportError, AttributeError):
            pass
    story = _mod("story")
    if not getattr(story.secret, "_tinker", False):
        orig = story.secret

        def secret(key):
            over = getattr(_ACTIVE, "secrets", None)
            if over and over.get(key):
                return over[key]
            return orig(key)
        secret._tinker = True
        secret._orig = orig
        story.secret = secret
    ws = working_settings()
    for key in ws["prompts"]:
        if key in _ORIG:
            _apply_working_key("prompts", key, ws)
    for key in ws["tables"]:
        if key in _ORIG:
            _apply_working_key("tables", key, ws)


def _secret_raw(key):
    story = _mod("story")
    fn = getattr(story.secret, "_orig", story.secret)
    return fn(key)


# ------------------------------------------------------------------ Experimental jobs
def snapshot(cfg):
    """the Experimental settings a job carries with it"""
    s = exp_settings(cfg)
    snap = {k: s[k] for k in ("models", "prompts", "tables") if s.get(k)}
    if snap:
        snap["taken"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return snap or None


def apply_models(cfg, snap):
    out = dict(cfg)
    for k, v in ((snap or {}).get("models") or {}).items():
        if k in CFG_KEYS and v:
            out[k] = v
    return out


def enter(snap):
    """switch the shared modules to Experimental values for the running job; leave() switches back"""
    if not snap:
        return False
    install_needed = not _ORIG
    if install_needed:
        install({})
    ws = working_settings()
    for key, text in (snap.get("prompts") or {}).items():
        if key in _ORIG and isinstance(text, str):
            _set(key, text)
    for key, over in (snap.get("tables") or {}).items():
        if key in _ORIG and isinstance(over, dict):
            _set(key, merge(_working_value("tables", key, ws), over))
    _ACTIVE.secrets = {k: v for k, v in (snap.get("models") or {}).items() if k in SECRET_KEYS and v}
    return True


def leave():
    """back to Working values (re-read from working_tinker.json, so a promotion is never undone)"""
    _ACTIVE.secrets = None
    ws = working_settings()
    for key in list(_ORIG):
        section = "prompts" if isinstance(_ORIG[key], str) else "tables"
        _apply_working_key(section, key, ws)


def describe(snap):
    """one line per change, for the job log"""
    if not snap:
        return []
    out = [f"model {k} = {v}" for k, v in (snap.get("models") or {}).items() if v]
    out += [f"prompt {k} edited" for k in (snap.get("prompts") or {})]
    out += [f"table {k}: {', '.join(sorted((snap['tables'][k] or {}).keys()))}" for k in (snap.get("tables") or {})]
    return out


# ------------------------------------------------------------------ the panel
def _model_working(cfg, key):
    if key in SECRET_KEYS:
        v = _secret_raw(key)
        if key == "story_engine":
            return v or "auto"
        if key == "gemini_model":
            return v or _mod("story").GEMINI_MODELS[0]
        return v or cfg.get("story_model") or cfg.get("llm") or ""
    return cfg.get(key) or ""


def _choices(cfg, src, ollama):
    if isinstance(src, list):
        return src
    if src == "ollama":
        return ollama
    if src == "gemini":
        return list(_mod("story").GEMINI_MODELS)
    if src == "engines":
        return sorted(getattr(_mod("tools"), "ENGINES", {}).keys())
    return []


def _genre_ids():
    return list(getattr(_mod("story"), "GENRE_TEXT", {}).keys())


def state(cfg):
    if not _ORIG:
        install(cfg)
    tools = _mod("tools")
    es, ws = exp_settings(cfg), working_settings()
    ollama_info = tools.llm_models(cfg)
    ollama = [m["name"] for m in ollama_info.get("models", [])]
    models = []
    for key, label, src, hint in MODELS:
        models.append({"key": key, "label": label, "hint": hint, "choices": _choices(cfg, src, ollama),
                       "working": _model_working(cfg, key), "experimental": es["models"].get(key, "")})
    prompts = []
    for key in prompt_keys():
        default = _ORIG[key]
        prompts.append({"key": key, "label": PROMPT_LABELS.get(key, key), "default": default,
                        "working": _working_value("prompts", key, ws), "working_changed": key in ws["prompts"],
                        "experimental": es["prompts"].get(key), "placeholders": sorted(set(_PH.findall(default)))})
    tables = []
    for key, label in TABLES.items():
        if key not in _ORIG:
            continue
        working = _working_value("tables", key, ws)
        tables.append({"key": key, "label": label, "working": working, "working_changed": key in ws["tables"],
                       "experimental": es["tables"].get(key),
                       "effective": merge(working, es["tables"].get(key) or {})})
    story = _mod("story")
    exp_cfg = dict(cfg, media_dir=_exp_media(cfg))
    os.makedirs(exp_cfg["media_dir"], exist_ok=True)
    rules = []
    for gid in _genre_ids():
        w, e = story.genre_rules(cfg, gid), story.genre_rules(exp_cfg, gid)
        rules.append({"key": gid, "label": gid, "working": w, "experimental": e, "differs": w != e})
    return {"models": models, "prompts": prompts, "tables": tables, "genre_rules": rules,
            "ollama_error": ollama_info.get("error", ""),
            "files": {"experimental": exp_file(cfg), "working": WORKING_FILE,
                      "experimental_rules": os.path.join(exp_cfg["media_dir"], "genre_rules.json"),
                      "working_rules": os.path.join(cfg["media_dir"], "genre_rules.json")},
            "history": history(cfg)[-15:][::-1], "updated": es.get("updated", "")}


def _check_prompt(key, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the prompt is empty")
    default = _ORIG.get(key)
    if default is None:
        raise ValueError("unknown prompt " + key)
    known = set(_PH.findall(default))
    if known or "{{" in default:
        extra = set(_PH.findall(text)) - known
        if extra:
            raise ValueError("this prompt is filled in by the code; it only knows {" + "}, {".join(sorted(known)) +
                             "}. Remove {" + "}, {".join(sorted(extra)) + "} or write braces as {{ }}")
    return text


def save(cfg, section, key, value):
    """value None = back to the Working value"""
    if not _ORIG:
        install(cfg)
    if section == "genre_rules":
        if key not in _genre_ids():
            raise ValueError("unknown series " + str(key))
        path = os.path.join(_exp_media(cfg), "genre_rules.json")
        data = _load(path, {})
        if value is None:
            data.pop(key, None)
        elif isinstance(value, dict):
            data[key] = value
        else:
            raise ValueError("genre rules must be a JSON object")
        _write(path, data)
        return {"ok": True}
    s = exp_settings(cfg)
    if section == "models":
        if key not in {m[0] for m in MODELS}:
            raise ValueError("unknown model setting " + str(key))
        if value in (None, ""):
            s["models"].pop(key, None)
        else:
            s["models"][key] = str(value).strip()
    elif section == "prompts":
        if value is None or value == _working_value("prompts", key):
            s["prompts"].pop(key, None)
        else:
            s["prompts"][key] = _check_prompt(key, value)
    elif section == "tables":
        if key not in TABLES or key not in _ORIG:
            raise ValueError("unknown table " + str(key))
        if value is None:
            s["tables"].pop(key, None)
        else:
            if not isinstance(value, dict):
                raise ValueError("the table must be a JSON object")
            working = _working_value("tables", key)
            diff = {k: v for k, v in value.items() if json.dumps(working.get(k), ensure_ascii=False, sort_keys=True)
                    != json.dumps(v, ensure_ascii=False, sort_keys=True)}
            if diff:
                s["tables"][key] = diff
            else:
                s["tables"].pop(key, None)
    else:
        raise ValueError("unknown section " + str(section))
    s["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write(exp_file(cfg), s)
    return {"ok": True}


def history(cfg):
    return _load(os.path.join(exp_dir(cfg), "promotions.json"), [])


def pending(cfg):
    """every Experimental change that differs from Working: [(section, key)]"""
    st = state(cfg)
    out = [("models", m["key"]) for m in st["models"] if m["experimental"] and m["experimental"] != m["working"]]
    out += [("prompts", p["key"]) for p in st["prompts"] if p["experimental"] is not None and p["experimental"] != p["working"]]
    out += [("tables", t["key"]) for t in st["tables"] if t["experimental"] and t["effective"] != t["working"]]
    out += [("genre_rules", g["key"]) for g in st["genre_rules"] if g["differs"]]
    return out


def promote(cfg, items):
    """copy the chosen Experimental settings into Working mode. items: [{"section","key"}] or "all".
    Changes the live hub settings too, so the caller must make sure no job is running."""
    if not _ORIG:
        install(cfg)
    todo = pending(cfg) if items == "all" else [(str(i.get("section")), str(i.get("key"))) for i in items or []]
    if not todo:
        raise ValueError("nothing to promote: Experimental matches Working")
    es, ws = exp_settings(cfg), working_settings()
    backups, done = {}, []

    def bk(path):
        if path not in backups:
            backups[path] = backup(path)

    hub_settings = os.path.join(cfg["media_dir"], "hub_settings.json")
    secrets = _mod("story").SECRETS
    for section, key in todo:
        if section == "models":
            value = es["models"].get(key)
            if not value:
                continue
            if key in SECRET_KEYS:
                bk(secrets)
                data = _load(secrets, {})
                data[key] = value
                _write(secrets, data)
            elif key == "image_engine":
                bk(CONFIG_FILE)
                data = _load(CONFIG_FILE, {})
                data["image_engine"] = value
                _write(CONFIG_FILE, data, indent=2)
                cfg[key] = value
            else:
                bk(hub_settings)
                data = _load(hub_settings, {})
                data.pop("llm_backend", None)
                data[key] = value
                _write(hub_settings, data)
                cfg[key] = value
        elif section == "prompts":
            if key not in es["prompts"]:
                continue
            bk(WORKING_FILE)
            ws["prompts"][key] = _check_prompt(key, es["prompts"][key])
        elif section == "tables":
            if key not in es["tables"]:
                continue
            bk(WORKING_FILE)
            ws["tables"][key] = merge(ws["tables"].get(key) or {}, es["tables"][key])
        elif section == "genre_rules":
            src = _load(os.path.join(_exp_media(cfg), "genre_rules.json"), {})
            if key not in src:
                continue
            dst = os.path.join(cfg["media_dir"], "genre_rules.json")
            bk(dst)
            data = _load(dst, {})
            data[key] = src[key]
            _write(dst, data)
        else:
            continue
        done.append({"section": section, "key": key})
    if any(d["section"] in ("prompts", "tables") for d in done):
        ws["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(WORKING_FILE, ws)
        for d in done:
            if d["section"] in ("prompts", "tables"):
                _apply_working_key(d["section"], d["key"], ws)
    if not done:
        raise ValueError("nothing to promote for the chosen items")
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "items": done,
             "backups": [b for b in backups.values() if b]}
    log = history(cfg)
    log.append(entry)
    _write(os.path.join(exp_dir(cfg), "promotions.json"), log[-200:])
    return {"ok": True, **entry}


def working_reset(cfg, section, key):
    """drop a promoted prompt or table from Working mode (back to the value in the code)"""
    if not _ORIG:
        install(cfg)
    if section not in ("prompts", "tables"):
        raise ValueError("only promoted prompts and tables can be reset here")
    ws = working_settings()
    if key not in ws[section]:
        raise ValueError("Working mode already uses the code's value")
    b = backup(WORKING_FILE)
    ws[section].pop(key)
    _write(WORKING_FILE, ws)
    _apply_working_key(section, key, ws)
    log = history(cfg)
    log.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "items": [{"section": section, "key": key, "reset": True}],
                "backups": [b] if b else []})
    _write(os.path.join(exp_dir(cfg), "promotions.json"), log[-200:])
    return {"ok": True, "backup": b}
