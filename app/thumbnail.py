"""thumbnail.py - Thumbnails page (Experimental): a 1920x1080 YouTube thumbnail per series episode.

Settings: Tinker table thumbnail.SERIES (per series: on = made after every video, layout, which picture, letters,
          colours, shade, badge, logo / channel name). The logo and channel name come from tools.CHANNEL.
Per episode: <episode>/thumbnail.json keeps what you chose on the page for that episode (picture, words, focus).
Output:   <episode>/<episode>_thumbnail.jpg (1920x1080, JPEG, well under YouTube's 2 MB).
Drawing:  engines/video/engine/thumbnail.py, run with the video engine's Python (it has HarfBuzz for Hindi).
Steps:    install() wraps tools.horror_video, so Experimental genre runs make the thumbnail after the video.
Painting: job kind "thumb_paint" (page button) has ComfyUI paint new pictures for one episode's thumbnail into
          <episode>/03_Artwork/_thumb_paint/; the newest becomes that episode's thumbnail picture.
"""
import base64
import copy
import glob
import inspect
import json
import os
import random
import re
import subprocess
import tempfile
import time

APP = os.path.dirname(os.path.abspath(__file__))
FLAGS = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
W, H = 1920, 1080
LAYOUTS = {"bottom": "Title at the bottom", "left": "Title on the left", "right": "Title on the right",
           "center": "Title in the middle"}
PICTURES = {"first": "First picture", "best": "Best checked picture", "closeup": "First close-up"}
GRADES = {"series": "Series colour grade", "pop": "Brighter colours", "none": "Picture as it is"}
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
PAINT_DIR = "_thumb_paint"                         # <episode>\03_Artwork\_thumb_paint: pictures painted for the thumbnail
PAINT_ASK = ("dramatic YouTube thumbnail picture: one strong subject close to the camera, bold light and contrast, "
             "clear expression, simple background, empty space on one side for big title letters, "
             "no text, no letters, no logo")
PAINT_NEG = "text, letters, words, caption, watermark, logo, signature, frame, border, collage, split screen, blurry"

# Tinker table (code values). Fonts are file names in engines\video\assets\fonts (the video's title fonts).
_BASE = {"on": True, "layout": "bottom", "picture": "first", "grade": "series", "split": True, "kicker": "",
         "font": "RozhaOne-Regular.ttf", "size": 1.1, "color": "#ffffff", "accent": "#d7261e", "outline": "#000000",
         "outline_w": 0.09, "stroke2": 0.03, "gradient": 0.18, "glow": 0.6, "shade": 0.7, "vignette": 0.4,
         "punch": 0.6, "sub_box": True, "frame": 0.012, "badge": True, "badge_text": "",
         "logo": True, "logo_corner": "top-right", "logo_size": 0.22, "name": True, "pos": {}}
# stroke2 = a 2nd outline ring in the accent colour, gradient = letters a little darker at their foot, punch = crisper,
# more colourful picture, sub_box = small rows on an accent box, frame = border width, pos = where you dragged pieces to
SERIES = {
    "bhay": dict(_BASE),
    "rahasya": dict(_BASE, layout="left", font="Biryani-Black.ttf", accent="#f0b23a", glow=0.25, vignette=0.3),
    "kisse": dict(_BASE, layout="center", grade="pop", font="Amita-Bold.ttf", color="#fff6e6", accent="#f5c46b",
                  outline_w=0.06, stroke2=0.0, glow=0.3, shade=0.6, vignette=0.35),
    "romanch": dict(_BASE, layout="center", grade="pop", font="Tillana-SemiBold.ttf", accent="#f08aa0", glow=0.45,
                    stroke2=0.0, shade=0.55, vignette=0.3),
    "kareeb": dict(_BASE, layout="right", font="Kalam-Bold.ttf", color="#f3eefc", accent="#c9b8f2", glow=0.45,
                   vignette=0.3, punch=0.4),
    "gyaan": dict(_BASE, layout="left", grade="pop", font="YatraOne-Regular.ttf", accent="#66b3fa", glow=0.2,
                  shade=0.65, vignette=0.25),
}
PIECES = ("title", "kicker", "badge", "logo", "name")


# ------------------------------------------------------------------ settings
def _clean(v):
    """a settings dict with every field in range (what the page sends is never trusted as is)"""
    s = dict(_BASE)
    s.update({k: v[k] for k in _BASE if k in (v or {})})
    s["on"], s["split"], s["badge"], s["logo"], s["name"], s["sub_box"] = (
        bool(s[k]) for k in ("on", "split", "badge", "logo", "name", "sub_box"))
    s["pos"] = clean_pos(s.get("pos"))
    s["layout"] = s["layout"] if s["layout"] in LAYOUTS else "bottom"
    s["picture"] = s["picture"] if s["picture"] in PICTURES else "first"
    s["grade"] = s["grade"] if s["grade"] in GRADES else "series"
    s["logo_corner"] = s["logo_corner"] if s["logo_corner"] in CORNERS else "top-right"
    for k, lo, hi in (("size", 0.4, 1.6), ("outline_w", 0.0, 0.2), ("glow", 0.0, 1.0), ("shade", 0.0, 1.0),
                      ("vignette", 0.0, 1.0), ("logo_size", 0.08, 0.4), ("stroke2", 0.0, 0.1),
                      ("gradient", 0.0, 0.5), ("punch", 0.0, 1.0), ("frame", 0.0, 0.04)):
        try:
            s[k] = round(min(hi, max(lo, float(s[k]))), 3)
        except (TypeError, ValueError):
            s[k] = _BASE[k]
    for k in ("color", "accent", "outline"):
        s[k] = s[k] if re.fullmatch(r"#[0-9a-fA-F]{6}", str(s[k])) else _BASE[k]
    s["font"] = os.path.basename(str(s["font"] or _BASE["font"]))
    s["kicker"], s["badge_text"] = str(s["kicker"] or "")[:60], str(s["badge_text"] or "")[:30]
    return s


def clean_pos(p):
    """{piece: {cx, cy, s}} with numbers in range; anything else dropped"""
    out = {}
    for k, v in (p or {}).items() if isinstance(p, dict) else ():
        if k not in PIECES or not isinstance(v, dict):
            continue
        try:
            e = {"cx": round(min(1.2, max(-0.2, float(v["cx"]))), 4), "cy": round(min(1.2, max(-0.2, float(v["cy"]))), 4)}
        except (KeyError, TypeError, ValueError):
            e = {}
        try:
            if v.get("s") not in (None, "", 1, 1.0):
                e["s"] = round(min(3.0, max(0.3, float(v["s"]))), 3)
        except (TypeError, ValueError):
            pass
        if e:
            out[k] = e
    return out


def _exp_value(cfg, key):
    """a Tinker table as Experimental runs see it now (Working value + your Experimental edits)"""
    import tinker
    if not tinker._ORIG:
        tinker.install(cfg)
    work = tinker._working_value("tables", key)
    return tinker.merge(work, (tinker.exp_settings(cfg).get("tables") or {}).get(key) or {})


def settings(cfg, series, in_job=False):
    t = SERIES if in_job else _exp_value(cfg, "thumbnail.SERIES")
    return _clean(dict(SERIES.get(series) or _BASE, **((t or {}).get(series) or {})))


def channel(cfg, series, in_job=False):
    """{name, logo} from tools.CHANNEL (logo '' when missing); {} when the series has no channel"""
    import tools
    c = ((tools.CHANNEL if in_job else _exp_value(cfg, "tools.CHANNEL")) or {}).get(series) or {}
    if not c.get("on"):
        return {}
    name, logo = str(c.get("name") or "").strip(), str(c.get("logo") or "").strip()
    if logo and not (os.path.isabs(logo) or (len(logo) > 2 and logo[1] == ":")):
        logo = os.path.join(cfg.get("experimental_root") or "", "Library", "Assets", logo)
    return {"name": name, "logo": logo if logo and os.path.exists(logo) else ""}


def fonts(cfg):
    d = os.path.join(cfg.get("studio_dir") or "", "assets", "fonts")
    skip = ("Montserrat", "Cormorant")             # Latin only: no Hindi letters
    return sorted(os.path.basename(f) for f in glob.glob(os.path.join(d, "*.tt[fc]"))
                  if not os.path.basename(f).startswith(skip))


# ------------------------------------------------------------------ episodes
def _series_dir(cfg, series):
    import experimental_workspace
    return os.path.join(experimental_workspace.root(cfg), "Series", series, "Episodes")


def _shots(ep):
    """(shots.json path, its data) of the episode's pictures, or (None, {})"""
    for sj in sorted(glob.glob(os.path.join(ep, "03_Artwork", "*", "shots.json"))):
        try:
            return sj, json.load(open(sj, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return None, {}


def _painted(ep):
    """pictures painted for the thumbnail on the page, newest first"""
    d = os.path.join(ep, "03_Artwork", PAINT_DIR)
    return [{"file": PAINT_DIR + "/" + os.path.basename(f), "path": f, "score": 0, "pass": True, "kind": "painted"}
            for f in sorted(glob.glob(os.path.join(d, "*.png")), reverse=True)]


def pictures(ep):
    sj, d = _shots(ep)
    out = _painted(ep)
    if not sj:
        return out
    base = os.path.dirname(sj)
    for s in (d.get("shots") or []):
        f = os.path.join(base, s.get("file") or "")
        if os.path.isfile(f):
            out.append({"file": s["file"], "path": f, "score": float(s.get("score") or (s.get("check") or {}).get("score") or 0),
                        "pass": bool((s.get("check") or {}).get("pass", True)), "kind": "shot"})
    for m in (d.get("moments") or []):
        f = os.path.join(base, m.get("file") or "")
        if os.path.isfile(f):
            out.append({"file": m["file"], "path": f, "score": 0, "pass": True, "kind": "closeup"})
    return out


def title_of(ep):
    t = ""
    try:
        t = open(os.path.join(ep, "title.txt"), encoding="utf-8-sig").read().strip()
    except OSError:
        pass
    if not t:
        try:
            sj = os.path.join(ep, "01_Script", "story.json")
            t = ((json.load(open(sj, encoding="utf-8")).get("summary") or {}).get("title_hi") or "").strip()
        except (OSError, ValueError):
            pass
    if not t:
        t = re.sub(r"^[A-Za-z]+-", "", os.path.basename(ep)).replace("-", " ")
    return t.splitlines()[0] if t else ""


def paint_prompt(series, ep, picture=""):
    """starting prompt for painting a thumbnail picture: series medium + a scene of the episode + thumbnail ask + look"""
    import tools
    L = (getattr(tools, "SERIES_LOOK", None) or {}).get(series) or {}
    _, d = _shots(ep)
    items = (d.get("shots") or []) + (d.get("moments") or [])
    s = next((x for x in items if x.get("file") == picture), None) or (items[0] if items else {})
    scene = " ".join(str(s.get("prompt") or s.get("scene_prompt") or title_of(ep)).split()).rstrip(". ")
    look = L.get("look", "") if L.get("on", True) else ""
    return ". ".join(x for x in (L.get("lead", "") if L.get("on", True) else "", scene, PAINT_ASK, look) if x) + "."


def out_path(ep):
    return os.path.join(ep, os.path.basename(os.path.normpath(ep)) + "_thumbnail.jpg")


def _override(ep):
    try:
        return json.load(open(os.path.join(ep, "thumbnail.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def episodes(cfg, series):
    d = _series_dir(cfg, series)
    rows, split = [], settings(cfg, series)["split"]
    for ep in glob.glob(os.path.join(d, "*")):
        if not os.path.isdir(ep):
            continue
        pics = pictures(ep)
        if not pics:
            continue
        th = out_path(ep)
        o = _override(ep)
        rows.append({"name": os.path.basename(ep), "dir": ep, "title": title_of(ep), "pictures": pics,
                     "rows": auto_rows(title_of(ep), split),
                     "thumb": th if os.path.isfile(th) else "", "override": o,
                     "paint_prompt": paint_prompt(series, ep, o.get("picture", "")),
                     "mtime": os.path.getmtime(ep)})
    rows.sort(key=lambda r: -r["mtime"])
    return rows


def _episode_dir(cfg, series, name):
    name = os.path.basename(str(name or ""))
    ep = os.path.join(_series_dir(cfg, series), name)
    if not name or not os.path.isdir(ep):
        raise ValueError("unknown episode " + name)
    return ep


# ------------------------------------------------------------------ drawing
def split_title(t, on):
    """'Big words: smaller words' -> main line + second line"""
    t = " ".join(str(t or "").split())
    if on:
        m = re.match(r"^(.{2,}?)\s*[:।|]\s*(.{2,})$", t) or re.match(r"^(.{2,}?)\s+[-–—]\s+(.{2,})$", t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return t, ""


def auto_rows(t, on):
    """the episode title as rows for the words box: main words, then '-' + the smaller 2nd line"""
    a, b = split_title(t, on)
    return [x for x in (a, ("-" + b) if b else "") if x]


def job(cfg, series, s, ep, override=None, out=None, size=(W, H), in_job=False):
    """the engine's job dict for one episode"""
    import tools
    o = dict(_override(ep), **(override or {}))
    pics = pictures(ep)
    if not pics:
        raise ValueError("this episode has no pictures yet")
    pic = next((p for p in pics if p["file"] == o.get("picture")), None)
    if not pic:
        shots = [p for p in pics if p["kind"] == "shot"] or pics
        if s["picture"] == "best":
            pic = max(shots, key=lambda p: (p["pass"], p["score"]))
        elif s["picture"] == "closeup":
            pic = next((p for p in pics if p["kind"] == "closeup"), shots[0])
        else:
            pic = shots[0]
    rows = o.get("rows") or []
    rows = [str(r)[:120] for r in (rows.splitlines() if isinstance(rows, str) else rows) if str(r).strip()][:6]
    if o.get("title") or o.get("sub"):
        title, sub = str(o.get("title") or ""), str(o.get("sub") or "")
    else:
        title, sub = split_title(title_of(ep), s["split"])
    G = tools.GENRES.get(series) or {}
    J = {"out": out or out_path(ep), "w": size[0], "h": size[1], "image": pic["path"],
         "focus": min(1.0, max(0.0, float(o.get("focus", 0.5) or 0.5))),
         "grade": {"series": G.get("mood", ""), "pop": "pop"}.get(s["grade"], ""),
         "layout": s["layout"], "title": title, "sub": sub, "rows": rows, "kicker": o.get("kicker") or s["kicker"],
         "font": s["font"], "size": s["size"], "color": s["color"], "accent": s["accent"], "outline": s["outline"],
         "outline_w": s["outline_w"], "glow": s["glow"], "shade": s["shade"], "vignette": s["vignette"],
         "stroke2": s["stroke2"], "gradient": s["gradient"], "punch": s["punch"], "sub_box": s["sub_box"],
         "frame": s["frame"], "pos": dict(s["pos"], **clean_pos(o.get("pos")))}
    if s["badge"]:
        J["badge"] = {"text": o.get("badge_text") or s["badge_text"] or G.get("label", "")}
    ch = channel(cfg, series, in_job)
    if ch.get("logo") and s["logo"]:
        J["logo"] = {"path": ch["logo"], "corner": s["logo_corner"], "size": s["logo_size"]}
    if ch.get("name") and s["name"]:
        latin = all(ord(c) < 0x250 for c in ch["name"])
        J["name"] = {"text": ch["name"], "font": "Montserrat-VariableFont_wght.ttf" if latin else s["font"]}
    if ch.get("name") and s["name"] and not J.get("logo"):
        J.setdefault("logo", {"corner": s["logo_corner"]})    # where the name label goes
    return J, pic["file"]


def run(cfg, jobs):
    """draw the jobs with the video engine's Python; returns the engine's answer per job"""
    py = cfg.get("studio_python") or "python"
    script = os.path.join(cfg.get("studio_dir") or "", "engine", "thumbnail.py")
    if not os.path.isfile(script):
        raise ValueError("the thumbnail drawer is missing: " + script)
    fd, spec = tempfile.mkstemp(suffix=".json", prefix="thumb_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False)
    try:
        r = subprocess.run([py, "-u", "-s", script, spec], capture_output=True, timeout=60 + 20 * len(jobs),
                           cwd=cfg.get("studio_dir") or None, creationflags=FLAGS,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    finally:
        try:
            os.remove(spec)
        except OSError:
            pass
    out = []
    for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    if not out:
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise ValueError("the thumbnail drawer stopped: " + (err[-1] if err else f"exit {r.returncode}"))
    return out


def _data_url(path):
    with open(path, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()


def preview(cfg, series, value, name, override=None):
    s = _clean(dict(settings(cfg, series), **(value or {})))
    ep = _episode_dir(cfg, series, name)
    cache = os.path.join(_series_dir(cfg, series), "..", "_thumb_preview")
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(os.path.abspath(cache), "preview_%d_%04x.jpg" % (os.getpid(), int(time.time() * 1000) % 65536))
    J, pic = job(cfg, series, s, ep, override, out, (960, 540))
    J["quality"] = 85
    try:
        r = run(cfg, [J])[0]
        if not r.get("ok"):
            raise ValueError(r.get("error") or "drawing failed")
        return {"img": _data_url(out), "picture": pic, "w": W, "h": H}
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def layers(cfg, series, value, name, override=None):
    """the page's drag-and-drop preview: background + every piece as its own picture, with where it sits"""
    import shutil
    s = _clean(dict(settings(cfg, series), **(value or {})))
    ep = _episode_dir(cfg, series, name)
    d = tempfile.mkdtemp(prefix="thumb_layers_")
    try:
        J, pic = job(cfg, series, s, ep, override, None, (960, 540))
        J.pop("out", None)
        J["layers"] = d
        r = run(cfg, [J])[0]
        if not r.get("ok"):
            raise ValueError(r.get("error") or "drawing failed")
        def url(f, kind):
            with open(os.path.join(d, f), "rb") as fh:
                return f"data:image/{kind};base64," + base64.b64encode(fh.read()).decode()
        els = [dict(e, img=url(e["name"] + ".png", "png")) for e in r.get("elements") or []]
        return {"bg": url("bg.jpg", "jpeg"), "elements": els, "picture": pic, "w": W, "h": H, "pos": J["pos"]}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def make(cfg, series, ep, override=None, value=None, in_job=False):
    """write <episode>_thumbnail.jpg; a page override is remembered in thumbnail.json"""
    s = settings(cfg, series, in_job) if value is None else _clean(dict(settings(cfg, series), **value))
    if override is not None:
        keep = {k: override[k] for k in ("picture", "title", "sub", "rows", "kicker", "badge_text", "focus", "pos",
                                                 "paint_prompt")
                if override.get(k) not in (None, "", [], {})}
        if keep.get("pos"):
            keep["pos"] = clean_pos(keep["pos"])
        p = os.path.join(ep, "thumbnail.json")
        if keep:
            json.dump(keep, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        elif os.path.isfile(p):
            os.replace(p, p + ".unused")
    J, pic = job(cfg, series, s, ep, None, None, (W, H), in_job)
    r = run(cfg, [J])[0]
    if not r.get("ok"):
        raise ValueError(r.get("error") or "drawing failed")
    return {"file": J["out"], "picture": pic, "kb": round(os.path.getsize(J["out"]) / 1024)}


def make_all(cfg, series, value=None, only_missing=False):
    s = settings(cfg, series) if value is None else _clean(dict(settings(cfg, series), **value))
    jobs = []
    for e in episodes(cfg, series):
        if only_missing and e["thumb"]:
            continue
        try:
            jobs.append(job(cfg, series, s, e["dir"])[0])
        except ValueError:
            pass
    if not jobs:
        return {"made": 0, "failed": []}
    res = run(cfg, jobs)
    return {"made": sum(1 for r in res if r.get("ok")),
            "failed": [os.path.basename(os.path.dirname(r.get("out") or "")) + ": " + str(r.get("error")) for r in res if not r.get("ok")]}


def _save_series(cfg, series, value):
    import tinker
    if series not in SERIES:
        raise ValueError("unknown series " + str(series))
    table = dict(_exp_value(cfg, "thumbnail.SERIES") or {})
    table[series] = _clean(dict(settings(cfg, series), **(value or {})))
    return tinker.save(cfg, "tables", "thumbnail.SERIES", table)


# ------------------------------------------------------------------ the page
def state(cfg, series=""):
    import tools
    series = series if series in SERIES else "bhay"
    return {"series": {g: settings(cfg, g) for g in SERIES}, "defaults": copy.deepcopy(SERIES),
            "labels": {g: (tools.GENRES.get(g) or {}).get("label", g) for g in SERIES},
            "channel": {g: channel(cfg, g) for g in SERIES}, "fonts": fonts(cfg), "layouts": LAYOUTS,
            "pictures": PICTURES, "grades": GRADES, "corners": list(CORNERS), "size": [W, H],
            "episodes": episodes(cfg, series), "for": series}


def handle_get(cfg, p, q):
    if p == "/api/thumb":
        return 200, state(cfg, q.get("series", ""))
    return 404, {"error": "unknown thumbnail request"}


def handle_post(cfg, p, b, jobs=None):
    series = str(b.get("series") or "")
    if series not in SERIES:
        raise ValueError("unknown series " + series)
    if p == "/api/thumb/preview":
        return 200, preview(cfg, series, b.get("value") or {}, b.get("episode"), b.get("override") or {})
    if p == "/api/thumb/layers":
        return 200, layers(cfg, series, b.get("value") or {}, b.get("episode"), b.get("override") or {})
    if p == "/api/thumb/save":
        return 200, _save_series(cfg, series, b.get("value") or {})
    if p == "/api/thumb/make":
        ep = _episode_dir(cfg, series, b.get("episode"))
        r = make(cfg, series, ep, b.get("override") or {}, b.get("value"))
        return 200, dict(r, img=_data_url(r["file"]))
    if p == "/api/thumb/make_all":
        return 200, make_all(cfg, series, b.get("value"), bool(b.get("only_missing")))
    return 404, {"error": "unknown thumbnail request"}


# ------------------------------------------------------------------ painting a picture (a hub job)
def paint_plan(b, cfg):
    series = str(b.get("series") or "")
    if series not in SERIES:
        raise ValueError("unknown series " + series)
    ep = _episode_dir(cfg, series, b.get("episode"))
    n = max(1, min(4, int(b.get("count") or 2)))
    prompt = " ".join(str(b.get("prompt") or "").split())[:2000] or paint_prompt(series, ep)
    return {"title": f"Thumbnail pictures: {os.path.basename(ep)}",
            "steps": [{"fn": "free_gpu", "label": "free the graphics card"},
                      {"fn": "thumb_paint", "label": f"ComfyUI paints {n} thumbnail picture(s)",
                       "args": {"ep": ep, "prompt": prompt, "count": n}},
                      {"fn": "free_gpu", "label": "free the graphics card"}],
            "outputs": [ep]}


def step_paint(ctx, ep, prompt, count=2):
    """paint `count` 16:9 pictures; the newest becomes the episode's thumbnail picture (thumbnail.json)"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    d = os.path.join(ep, "03_Artwork", PAINT_DIR)
    os.makedirs(d, exist_ok=True)
    w, h = tools.SIZES["16:9"][:2]
    if not tools.comfy_running(cfg):
        tools.start_comfy(cfg, say)
    engine = cfg.get("image_engine", "z_image_turbo")
    say(f"   prompt: {prompt[:300]}")
    made, log = [], os.path.join(d, "painted.json")
    try:
        info = json.load(open(log, encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    for i in range(int(count)):
        if ctx["stopped"]():
            break
        f = os.path.join(d, time.strftime("%Y%m%d_%H%M%S") + f"_{i + 1}.png")
        seed = random.randint(1, 2 ** 31)
        t0 = time.time()
        tools.comfy_generate(cfg, prompt, f, engine, seed, w, h, steps=8, negative=PAINT_NEG)
        if os.path.isfile(f):
            made.append(f)
            info[os.path.basename(f)] = {"prompt": prompt, "seed": seed, "engine": engine,
                                         "made": time.strftime("%Y-%m-%d %H:%M:%S")}
            say(f"   {os.path.basename(f)}: {time.time() - t0:.0f} s")
    if not made:
        return False
    json.dump(info, open(log, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    o = _override(ep)
    o.update(picture=PAINT_DIR + "/" + os.path.basename(made[-1]), paint_prompt=prompt)
    json.dump(o, open(os.path.join(ep, "thumbnail.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(f"   {len(made)} picture(s) in {d}; the last one is now this episode's thumbnail picture "
        "(pick another on the Thumbnails page)")
    return True


# ------------------------------------------------------------------ runs
def install(cfg):
    """at hub start: Experimental genre runs make the thumbnail right after the video"""
    import tools
    from steps import genre as G
    if not getattr(tools.plan, "_thumb", False):
        orig = tools.plan

        def plan(kind, b, c):
            if kind == "thumb_paint":
                return paint_plan(b or {}, c)
            return orig(kind, b, c)
        plan._thumb = True
        plan._orig = orig
        for k in ("_narrator",):                    # keep the other wrappers' "already installed" marks
            if getattr(orig, k, False):
                setattr(plan, k, True)
        tools.plan = plan
        tools.thumb_paint = step_paint
    o = getattr(tools, "horror_video", None)
    if not o or getattr(o, "_thumb", False):
        return
    sig = inspect.signature(G.horror_video)

    def horror_video(ctx, *a, **k):
        ok = o(ctx, *a, **k)
        try:
            cfg_, say = ctx["cfg"], ctx["say"]
            if not G._is_exp(cfg_):
                return ok
            args = sig.bind(ctx, *a, **k)
            args.apply_defaults()
            series = args.arguments.get("genre") or "bhay"
            if series in SERIES and settings(cfg_, series, True)["on"]:
                ep = os.path.dirname(os.path.dirname(args.arguments["out"]))
                t0 = time.time()
                r = make(cfg_, series, ep, in_job=True)
                say(f"   thumbnail: {r['file']} ({r['picture']}, {r['kb']} KB, {time.time() - t0:.0f} s)")
        except Exception as e:                      # never fail a run over the thumbnail
            ctx["say"](f"   (thumbnail skipped: {e})")
        return ok
    horror_video._thumb = True
    tools.horror_video = horror_video
