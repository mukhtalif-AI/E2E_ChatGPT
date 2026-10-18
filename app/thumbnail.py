"""thumbnail.py - Thumbnails page (Experimental): a 1920x1080 YouTube thumbnail per series episode.

Settings: Tinker table thumbnail.SERIES (per series: on = made after every video, layout, which picture, letters,
          colours, shade, badge, logo / channel name). The logo and channel name come from tools.CHANNEL.
Per episode: <episode>/thumbnail.json keeps what you chose on the page for that episode (picture, words, focus).
Output:   <episode>/<episode>_thumbnail.jpg (1920x1080, JPEG, well under YouTube's 2 MB).
Drawing:  engines/video/engine/thumbnail.py, run with the video engine's Python (it has HarfBuzz for Hindi).
Steps:    install() wraps tools.horror_video, so Experimental genre runs make the thumbnail after the video.
"""
import base64
import copy
import glob
import inspect
import json
import os
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

# Tinker table (code values). Fonts are file names in engines\video\assets\fonts (the video's title fonts).
_BASE = {"on": True, "layout": "bottom", "picture": "first", "grade": "series", "split": True, "kicker": "",
         "font": "RozhaOne-Regular.ttf", "size": 1.0, "color": "#ffffff", "accent": "#d7261e", "outline": "#000000",
         "outline_w": 0.08, "glow": 0.5, "shade": 0.7, "vignette": 0.4, "badge": True, "badge_text": "",
         "logo": True, "logo_corner": "top-right", "logo_size": 0.22, "name": True}
SERIES = {
    "bhay": dict(_BASE),
    "rahasya": dict(_BASE, layout="left", font="Biryani-Black.ttf", accent="#f0b23a", glow=0.0, vignette=0.3),
    "kisse": dict(_BASE, layout="center", grade="pop", font="Amita-Bold.ttf", color="#fff6e6", accent="#f5c46b",
                  glow=0.3, shade=0.6, vignette=0.35),
    "romanch": dict(_BASE, layout="center", grade="pop", font="Tillana-SemiBold.ttf", accent="#f08aa0", glow=0.35,
                    shade=0.55, vignette=0.3),
    "kareeb": dict(_BASE, layout="right", font="Kalam-Bold.ttf", color="#f3eefc", accent="#c9b8f2", glow=0.4,
                   vignette=0.3),
    "gyaan": dict(_BASE, layout="left", grade="pop", font="YatraOne-Regular.ttf", accent="#66b3fa", glow=0.0,
                  shade=0.65, vignette=0.25),
}


# ------------------------------------------------------------------ settings
def _clean(v):
    """a settings dict with every field in range (what the page sends is never trusted as is)"""
    s = dict(_BASE)
    s.update({k: v[k] for k in _BASE if k in (v or {})})
    s["on"], s["split"], s["badge"], s["logo"], s["name"] = (bool(s[k]) for k in ("on", "split", "badge", "logo", "name"))
    s["layout"] = s["layout"] if s["layout"] in LAYOUTS else "bottom"
    s["picture"] = s["picture"] if s["picture"] in PICTURES else "first"
    s["grade"] = s["grade"] if s["grade"] in GRADES else "series"
    s["logo_corner"] = s["logo_corner"] if s["logo_corner"] in CORNERS else "top-right"
    for k, lo, hi in (("size", 0.4, 1.6), ("outline_w", 0.0, 0.2), ("glow", 0.0, 1.0), ("shade", 0.0, 1.0),
                      ("vignette", 0.0, 1.0), ("logo_size", 0.08, 0.4)):
        try:
            s[k] = round(min(hi, max(lo, float(s[k]))), 3)
        except (TypeError, ValueError):
            s[k] = _BASE[k]
    for k in ("color", "accent", "outline"):
        s[k] = s[k] if re.fullmatch(r"#[0-9a-fA-F]{6}", str(s[k])) else _BASE[k]
    s["font"] = os.path.basename(str(s["font"] or _BASE["font"]))
    s["kicker"], s["badge_text"] = str(s["kicker"] or "")[:60], str(s["badge_text"] or "")[:30]
    return s


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


def pictures(ep):
    sj, d = _shots(ep)
    if not sj:
        return []
    base = os.path.dirname(sj)
    out = []
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
        rows.append({"name": os.path.basename(ep), "dir": ep, "title": title_of(ep), "pictures": pics,
                     "rows": auto_rows(title_of(ep), split),
                     "thumb": th if os.path.isfile(th) else "", "override": _override(ep),
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
         "outline_w": s["outline_w"], "glow": s["glow"], "shade": s["shade"], "vignette": s["vignette"]}
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


def make(cfg, series, ep, override=None, value=None, in_job=False):
    """write <episode>_thumbnail.jpg; a page override is remembered in thumbnail.json"""
    s = settings(cfg, series, in_job) if value is None else _clean(dict(settings(cfg, series), **value))
    if override is not None:
        keep = {k: override[k] for k in ("picture", "title", "sub", "rows", "kicker", "badge_text", "focus") if override.get(k) not in (None, "")}
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
    if p == "/api/thumb/save":
        return 200, _save_series(cfg, series, b.get("value") or {})
    if p == "/api/thumb/make":
        ep = _episode_dir(cfg, series, b.get("episode"))
        r = make(cfg, series, ep, b.get("override") or {}, b.get("value"))
        return 200, dict(r, img=_data_url(r["file"]))
    if p == "/api/thumb/make_all":
        return 200, make_all(cfg, series, b.get("value"), bool(b.get("only_missing")))
    return 404, {"error": "unknown thumbnail request"}


# ------------------------------------------------------------------ runs
def install(cfg):
    """at hub start: Experimental genre runs make the thumbnail right after the video"""
    import tools
    from steps import genre as G
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
