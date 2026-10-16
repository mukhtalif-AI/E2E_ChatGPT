"""steps/thumbs.py - thumbnail look index. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4b).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools

WIN = os.name == "nt"


SUKOON_WORDS = ("sukoon", "bachpan", "moonlit", "headphone")


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
    if style in tools.THEMES:
        t = load_index(cfg).get("themes", {}).get(style.split("_")[0])
        if t:
            return t
        style = "sukoon_night" if style == "sukoon_theme" else "silhouette"
    return tools.STYLES.get(style, tools.STYLES["sukoon_night"])[1]


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
            d = json.loads(tools.model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), tools.VISION_ASK,
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
            idx.setdefault("themes", {})[ser] = tools.model_chat(cfg, cfg["llm"], ask, 0.3, timeout=600).strip().strip('"')
            say(f"   common {ser} look: {idx['themes'][ser]}")
        except Exception as e:
            say(f"   (could not sum up the {ser} look: {e})")
    idx["built"] = time.strftime("%Y-%m-%d %H:%M")
    json.dump(idx, open(index_path(cfg), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(f"   index: {len(idx['items'])} thumbnails -> {index_path(cfg)}")
