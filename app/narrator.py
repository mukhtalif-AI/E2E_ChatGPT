"""narrator.py - the corner narrator: your transparent half-body PNG in a corner of the video, lip-synced.

Pictures: <experimental_root>/Library/Narrators/<id>/  base.png (+ mouth_open.png, mouth_closed.png, eyes_closed.png)
          and narrator.json (name, male/female, series, mouth and eye points). Shared by Experimental and Working.
Settings: the Tinker table narrator.SERIES (per series: on, which narrator, corner / size / margin for the video
          and for reels). Experimental runs carry it in their Tinker snapshot; "Install into Working" copies it
          to Working mode. narrator.THEMES = the ComfyUI look per series. The code values are empty, so Working
          mode has no narrator until you install one.
Per run:  the job's "narrator" = "" (series setting) / "on" / "off", "narrator_id" = a different narrator.
Steps:    narrator_overlay after horror_video / genre_reels / studio_video (added by the tools.plan wrapper),
          narrator_art (ComfyUI, kind "narrator_art"), kind "narrator_apply" for a finished video.
The clean video is kept in <Output>/_clean (reels: reels/_clean), so the narrator can be redone without a render.
Drawing: app/narrator_render.py (numpy + Pillow + FFmpeg, no model).
"""
import base64
import copy
import glob
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time

APP = os.path.dirname(os.path.abspath(__file__))
RENDER = os.path.join(APP, "narrator_render.py")
SLOTS = ("base", "mouth_open", "mouth_closed", "eyes_closed")
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")

# Tinker tables (code values; Experimental edits and Working installs go through app/tinker.py)
SERIES = {}
THEMES = {
    "sukoon": "a serene, gentle storyteller in soft white and pale blue clothes, moonlit calm night mood",
    "bhay": "a mysterious storyteller wrapped in a dark woollen shawl, candle-lit, eerie gothic Indian village mood",
    "rahasya": "a sharp, thoughtful mystery storyteller in a dark coat, film-noir light",
    "kisse": "a warm, kind storyteller in a simple cotton kurta, cosy evening lamp light",
    "romanch": "an elegant storyteller in soft pastel clothes, warm golden-hour light",
    "kareeb": "a soft-spoken, calm storyteller in cosy knitwear, dim warm bedroom light",
    "gyaan": "a friendly, clever teacher-like presenter in smart casual clothes, bright study light",
}
DEFAULT = {"on": False, "narrator": "",
           "wide": {"corner": "bottom-right", "size": 0.40, "margin": 0.025, "dock": True, "shadow": True,
                    "blink": True, "start_s": 0.5},
           "reel": {"corner": "top-right", "size": 0.22, "margin": 0.035, "dock": True, "shadow": True,
                    "blink": True, "start_s": 4.6}}
# where the subtitles sit (fractions of the frame), as drawn by engines/video/engine/horror_video.py
SUB_BAND = {"wide": (0.75, 0.90), "reel": (0.69, 0.81)}


# ------------------------------------------------------------------ files
def _root(cfg):
    import experimental_workspace
    L = cfg.get("layout") or {}
    d = os.path.join(experimental_workspace.root(cfg), L.get("experimental_library", "Library"), "Narrators")
    os.makedirs(d, exist_ok=True)
    return d


def _load(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(default)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _safe_id(v):
    v = str(v or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", v):
        raise ValueError("unknown narrator")
    return v


def _dir(cfg, nid):
    return os.path.join(_root(cfg), _safe_id(nid))


def get(cfg, nid):
    """narrator.json + full paths of the pictures that exist; None when it is gone"""
    if not nid:
        return None
    try:
        d = _dir(cfg, nid)
    except ValueError:
        return None
    m = _load(os.path.join(d, "narrator.json"), None)
    if not m or not os.path.isfile(os.path.join(d, "base.png")):
        return None
    m["id"] = nid
    for s in SLOTS:
        p = os.path.join(d, s + ".png")
        m[s] = p if os.path.isfile(p) else ""
    m["dir"] = d
    return m


def all_narrators(cfg):
    out = []
    for d in sorted(glob.glob(os.path.join(_root(cfg), "*"))):
        nid = os.path.basename(d)
        if nid.startswith("_") or not os.path.isdir(d):
            continue
        m = get(cfg, nid)
        if m:
            m["mtime"] = os.path.getmtime(os.path.join(d, "base.png"))
            out.append(m)
    return sorted(out, key=lambda m: m.get("made", ""), reverse=True)


def _new_id(name):
    return time.strftime("%Y%m%d_%H%M%S_") + (re.sub(r"[^A-Za-z0-9]+", "_", name or "narrator").strip("_")[:24] or "narrator") \
        + "_" + "%04x" % random.getrandbits(16)


def _aspect(m):
    """width / height of the visible figure"""
    try:
        import narrator_render as R
        from PIL import Image
        x0, y0, x1, y1 = R.alpha_box(Image.open(m["base"]).convert("RGBA"))
        return max(0.2, (x1 - x0) / max(1, y1 - y0))
    except Exception:
        return 0.8


# ------------------------------------------------------------------ settings
def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else copy.deepcopy(v)
    return out


def series_settings(series, table=None):
    t = SERIES if table is None else table
    return _merge(DEFAULT, (t or {}).get(series) or {})


def experimental_table(cfg):
    """narrator.SERIES as Experimental runs see it now (Working value + your Experimental edits)"""
    import tinker
    if not tinker._ORIG:
        tinker.install(cfg)
    work = tinker._working_value("tables", "narrator.SERIES")
    return tinker.merge(work, (tinker.exp_settings(cfg).get("tables") or {}).get("narrator.SERIES") or {})


def experimental_themes(cfg):
    import tinker
    if not tinker._ORIG:
        tinker.install(cfg)
    work = tinker._working_value("tables", "narrator.THEMES")
    return tinker.merge(work, (tinker.exp_settings(cfg).get("tables") or {}).get("narrator.THEMES") or {})


def auto_pick(cfg, series):
    """the narrator a series uses when none is chosen: tagged for the series first, then one whose points you
    checked (not just guessed), then the newest"""
    rows = all_narrators(cfg)
    if not rows:
        return ""
    rows.sort(key=lambda m: (series not in (m.get("series") or []), bool(m.get("guessed", True)), -_made_key(m)))
    return rows[0]["id"]


def _made_key(m):
    try:
        return time.mktime(time.strptime(m.get("made") or "", "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return m.get("mtime", 0)


def resolve(cfg, series, body=None):
    """the settings this run uses, or None when the narrator is off / missing"""
    b = body or {}
    s = series_settings(series)
    choice = str(b.get("narrator") or "").lower()
    if choice == "off":
        return None
    if choice == "on":
        s["on"] = True
    if b.get("narrator_id"):
        s["narrator"] = str(b["narrator_id"])
    if not s.get("on"):
        return None
    if not get(cfg, s.get("narrator")):
        nid = auto_pick(cfg, series)
        if not nid:
            return {"missing": True, **s}
        s["narrator"] = nid
    for k in ("wide", "reel"):
        if s[k].get("corner") not in CORNERS:
            s[k]["corner"] = DEFAULT[k]["corner"]
        s[k]["size"] = min(0.9, max(0.08, float(s[k].get("size") or DEFAULT[k]["size"])))
        s[k]["margin"] = min(0.2, max(0.0, float(s[k].get("margin") or 0)))
    return s


def subtitle_room(cfg, s, kind, W, H):
    """fraction of the frame width each side the subtitles keep free so they do not run under the narrator"""
    p = s[kind]
    if not p["corner"].startswith("bottom"):
        return 0.0
    m = get(cfg, s["narrator"])
    if not m:
        return 0.0
    fig_w = p["size"] * H * _aspect(m)
    top = H - p["size"] * H - (0 if p.get("dock", True) else p["margin"] * H)
    if top > SUB_BAND[kind][1] * H:                   # the narrator stays below the subtitles
        return 0.0
    return round(min(0.42, (fig_w + p["margin"] * H + 0.012 * W) / W), 4)


# ------------------------------------------------------------------ plan hooks
def _series_of(kind, b):
    g = b.get("genre") or b.get("series") or ("sukoon" if kind in ("episode", "studio", "experimental") else "")
    return str(g or "").strip().lower()


def add_steps(kind, b, cfg, plan):
    series = _series_of(kind, b)
    if not series:
        return plan
    s = resolve(cfg, series, b)
    if not s:
        return plan
    steps = plan.get("steps") or []
    if s.get("missing"):
        steps.append({"fn": "narrator_overlay", "label": "corner narrator (none uploaded yet - skipped)",
                      "args": {"what": "none", "series": series, "settings": s}})
        return plan
    main = next((st for st in steps if st.get("fn") == "horror_video"), None)
    studio = next((st for st in steps if st.get("fn") == "studio_video"), None)
    prep = next((st for st in steps if st.get("fn") == "studio_prepare"), None)
    out = []
    for st in steps:
        out.append(st)
        a = st.get("args") or {}
        if st.get("fn") == "horror_video":
            out.append({"fn": "narrator_overlay", "label": "corner narrator, lips moving with your voice",
                        "args": {"what": "main", "series": series, "settings": s, "video": a.get("out", ""),
                                 "narration": a.get("narration", ""), "pictures": a.get("pictures", "")}})
        elif st.get("fn") == "genre_reels":
            out.append({"fn": "narrator_overlay", "label": "corner narrator on the reels",
                        "args": {"what": "reels", "series": series, "settings": s, "reels_dir": a.get("out", ""),
                                 "pictures": a.get("pictures", ""),
                                 "narration": ((main or {}).get("args") or {}).get("narration", "")}})
        elif st.get("fn") == "studio_video" and studio is st:
            ep = a.get("ep", "")
            out.append({"fn": "narrator_overlay", "label": "corner narrator on the video, clips and reels",
                        "args": {"what": "studio", "series": series, "settings": s,
                                 "output_dir": os.path.join(ep, cfg.get("episode_output_name", "Output")),
                                 "narration": ((prep or {}).get("args") or {}).get("audio", ""), "episode": ep}})
    plan["steps"] = out
    return plan


def apply_plan(b, cfg):
    """kind narrator_apply: the narrator on a finished video (or a reel)"""
    v = str(b.get("video") or "")
    if not v.lower().endswith(".mp4") or not os.path.isfile(v):
        raise ValueError("pick a finished video (.mp4)")
    series = str(b.get("series") or "").strip().lower()
    s = resolve(cfg, series, dict(b, narrator="on"))
    if not s or s.get("missing"):
        raise ValueError("upload a narrator first (Narrator page)")
    tall = _is_reel(cfg, v)
    return {"title": f"Narrator: {os.path.basename(v)}",
            "steps": [{"fn": "narrator_overlay", "label": "corner narrator, lips moving with the voice",
                       "args": {"what": "file", "series": series, "settings": s, "video": v,
                                "narration": str(b.get("narration") or ""), "kind": "reel" if tall else "wide"}}],
            "outputs": [os.path.dirname(v)]}


def art_plan(b, cfg):
    series = str(b.get("series") or "bhay").strip().lower()
    gender = "female" if str(b.get("gender")) == "female" else "male"
    n = max(1, min(6, int(b.get("count") or 4)))
    return {"title": f"Narrator pictures: {series}, {gender}",
            "steps": [{"fn": "free_gpu", "label": "free the graphics card"},
                      {"fn": "narrator_art", "label": f"ComfyUI paints {n} {gender} narrator(s) for {series}",
                       "args": {"series": series, "gender": gender, "idea": str(b.get("idea") or "")[:400], "count": n}},
                      {"fn": "free_gpu", "label": "free the graphics card"}],
            "outputs": [_root(cfg)]}


def install(cfg):
    """at hub start: the plan wrapper, the step functions, and the subtitle room for genre videos"""
    import tools
    if getattr(tools.plan, "_narrator", False):
        return
    orig = tools.plan

    def plan(kind, b, c):
        if kind == "narrator_art":
            return art_plan(b, c)
        if kind == "narrator_apply":
            return apply_plan(b, c)
        p = orig(kind, b, c)
        try:
            add_steps(kind, b or {}, c, p)
        except Exception as e:                      # never block a run over the narrator
            p.setdefault("steps", []).append({"fn": "narrator_overlay", "label": "corner narrator (settings problem - skipped)",
                                              "args": {"what": "none", "series": "", "settings": {"error": str(e)}}})
        return p
    plan._narrator = True
    plan._orig = orig
    tools.plan = plan
    tools.narrator_overlay = step_overlay
    tools.narrator_art = step_art
    for fn in ("horror_video", "genre_reels"):
        o = getattr(tools, fn, None)
        if o and not getattr(o, "_narrator", False):
            w = _with_subtitle_room(o, "wide" if fn == "horror_video" else "reel")
            setattr(tools, fn, w)


def _with_subtitle_room(orig, kind):
    def wrapped(ctx, *a, **k):
        steps = (ctx.get("job") or {}).get("steps") or []
        st = next((x for x in steps if x.get("fn") == "narrator_overlay" and (x.get("args") or {}).get("what") in
                   ("main", "reels")), None)
        s = ((st or {}).get("args") or {}).get("settings") or {}
        if not s.get("narrator"):
            return orig(ctx, *a, **k)
        try:
            room = {"wide": subtitle_room(ctx["cfg"], s, "wide", 1920, 1080),
                    "tall": subtitle_room(ctx["cfg"], s, "reel", 1080, 1920)}
        except Exception:
            room = {}
        if not any(room.values()):
            return orig(ctx, *a, **k)
        run = ctx["run_cmd"]

        def run_cmd(cmd, cwd=None, env=None):
            try:
                i = next(i for i, c in enumerate(cmd) if str(c).replace("\\", "/").endswith("engine/horror_video.py"))
                pj = cmd[i + 1]
                plan = _load(pj, None)
                if isinstance(plan, dict):
                    plan["sub_room"] = room
                    _write(pj, plan)
                    ctx["say"]("   subtitles kept clear of the corner narrator ({:.0f}% each side)".format(
                        100 * room["tall" if plan.get("size") else "wide"]))
            except StopIteration:
                pass
            except Exception as e:
                ctx["say"](f"   (subtitle room not set: {e})")
            return run(cmd, cwd=cwd, env=env)
        return orig(dict(ctx, run_cmd=run_cmd), *a, **k)
    wrapped._narrator = True
    wrapped._orig = orig
    return wrapped


# ------------------------------------------------------------------ steps
def _is_reel(cfg, path):
    try:
        import narrator_render as R
        W, H, _, _ = R.probe(_ffmpeg(cfg), path)
        return H > W
    except Exception:
        return False


def _ffmpeg(cfg):
    return (cfg or {}).get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"


def _narration_wav(narration):
    """the clean voice (narration.wav), its words.json and music lead"""
    if not narration:
        return "", "", 0.0
    d = narration if os.path.isdir(narration) else os.path.dirname(narration)
    wav = os.path.join(d, "narration.wav")
    if not os.path.isfile(wav):
        wav = narration if os.path.isfile(narration) else ""
    words = os.path.join(os.path.dirname(wav), "words.json") if wav else ""
    shp = _load(os.path.join(d, "music_shape.json"), {}) or {}
    return wav, words if words and os.path.isfile(words) else "", float(shp.get("lead_s") or 0)


def _keep_clean(video):
    """the clean copy in <dir>/_clean; a newer render replaces it. -> clean path"""
    d = os.path.join(os.path.dirname(video), "_clean")
    os.makedirs(d, exist_ok=True)
    clean = os.path.join(d, os.path.basename(video))
    notes = _load(os.path.join(d, "narrated.json"), {})
    done = notes.get(os.path.basename(video)) or {}
    cur = os.path.getmtime(video)
    if not (os.path.isfile(clean) and done and abs(float(done.get("mtime", 0)) - cur) < 1):
        shutil.copy2(video, clean)                 # a fresh render (or first time): this is the clean one
    return clean


def _mark_done(video, info):
    d = os.path.join(os.path.dirname(video), "_clean")
    p = os.path.join(d, "narrated.json")
    notes = _load(p, {})
    notes[os.path.basename(video)] = dict(info, mtime=os.path.getmtime(video), made=time.strftime("%Y-%m-%d %H:%M:%S"))
    _write(p, notes)


def _place(s, kind):
    p = dict(s[kind])
    return {k: p.get(k) for k in ("corner", "size", "margin", "dock", "shadow", "blink")}


def step_overlay(ctx, what, series, settings, video="", reels_dir="", pictures="", narration="", output_dir="",
                 episode="", kind="wide"):
    cfg, say = ctx["cfg"], ctx["say"]
    s = settings or {}
    if what == "none":
        say("   " + (("settings problem: " + s["error"]) if s.get("error") else
                     "the corner narrator is on for this series, but no narrator picture is uploaded yet (Narrator page)"))
        return True
    m = get(cfg, s.get("narrator"))
    if not m:
        say("   the chosen narrator is gone (Narrator page) - video left without one")
        return True
    say(f"   narrator: {m.get('name') or m['id']} ({m.get('gender', '?')}), mouth at {m.get('mouth')}, eyes at {m.get('eyes')}")
    if m.get("guessed", True):
        say("   !! this narrator's mouth / eye points are still the automatic guess - check them on the Narrator page")
    wav, words, lead = _narration_wav(narration)
    items = []
    if what == "main" and os.path.isfile(video):
        items.append({"video": _keep_clean(video), "out": video, "narration": wav, "words": words,
                      "shift": -lead if wav else None, "place": _place(s, "wide"), "start": s["wide"].get("start_s", 0.5)})
    elif what == "reels" and os.path.isdir(reels_dir):
        for f in sorted(glob.glob(os.path.join(reels_dir, "reel*.mp4"))):
            k = re.match(r"reel(\d+)_", os.path.basename(f))
            rj = _load(os.path.join(pictures, f"reel_{k.group(1)}.json"), {}) if k and pictures else {}
            if _already(f):
                continue
            win = (rj.get("window") or [None])[0]
            items.append({"video": _keep_clean(f), "out": f, "narration": wav, "words": words,
                          "shift": (float(win) - lead) if (win is not None and wav) else None,
                          "place": _place(s, "reel"), "start": s["reel"].get("start_s", 4.6)})
    elif what == "studio" and os.path.isdir(output_dir):
        if not wav and episode:
            v = os.path.join(episode, cfg.get("episode_work_name", "_work"), "voice.wav")
            wav = v if os.path.isfile(v) else ""
            words = ""
        for f in sorted(glob.glob(os.path.join(output_dir, "*.mp4"))):
            if not f.endswith(("_silent.mp4", ".part.mp4")) and not _already(f):
                items.append({"video": _keep_clean(f), "out": f, "narration": wav, "words": words, "shift": None,
                              "place": _place(s, "wide"), "start": s["wide"].get("start_s", 0.5)})
        for sub, kk in (("clips", "wide"), (cfg.get("layout", {}).get("episode_reels", "reels"), "reel")):
            for f in sorted(glob.glob(os.path.join(output_dir, sub, "*.mp4"))):
                if not os.path.basename(f).startswith("_") and not _already(f):
                    items.append({"video": _keep_clean(f), "out": f, "narration": wav, "words": words, "shift": None,
                                  "place": _place(s, kk), "start": s[kk].get("start_s", 0.5)})
    elif what == "file" and os.path.isfile(video):
        if not wav:                                   # an Experimental run remembers its narration
            src = _load(os.path.join(os.path.dirname(video), "review_sources.json"), {}) or \
                _load(os.path.join(os.path.dirname(os.path.dirname(video)), "review_sources.json"), {})
            wav, words, lead = _narration_wav(src.get("narration") or "")
        items.append({"video": _keep_clean(video), "out": video, "narration": wav,
                      "words": words, "shift": None, "place": _place(s, kind), "start": s[kind].get("start_s", 0.5)})
    if not items:
        say("   no new video to put the narrator on")
        return True
    job_dir = os.path.join(_root(cfg), "_jobs")
    os.makedirs(job_dir, exist_ok=True)
    tag = time.strftime("%Y%m%d_%H%M%S_") + "%04x" % random.getrandbits(16)
    jp, rp = os.path.join(job_dir, tag + ".json"), os.path.join(job_dir, tag + "_result.json")
    _write(jp, {"ffmpeg": _ffmpeg(cfg), "narrator": {k: m.get(k) for k in
               ("base", "mouth_open", "mouth_closed", "eyes_closed", "mouth", "mouth_w", "eyes", "eye_w", "open_h")},
                "items": items, "result": rp})
    rc = ctx["run_cmd"]([sys.executable, "-u", "-s", RENDER, jp], cwd=APP)
    for r in _load(rp, []):
        if r.get("ok"):
            _mark_done(r["video"], {"narrator": m["id"], "shift": r.get("shift"), "encoder": r.get("encoder")})
    if rc != 0:
        say("   (the narrator could not be added to every video - the clean ones are kept, see above)")
    return True                                        # a failed narrator never fails the run


def _already(f):
    notes = _load(os.path.join(os.path.dirname(f), "_clean", "narrated.json"), {})
    d = notes.get(os.path.basename(f))
    return bool(d) and abs(float(d.get("mtime", 0)) - os.path.getmtime(f)) < 1


def chroma_key(path):
    """ComfyUI painted the narrator on flat green: make that green transparent (soft edge, green spill removed)"""
    import numpy as np
    from PIL import Image, ImageFilter
    try:
        from rembg import remove                       # if you have it installed, it cuts better
        im = Image.open(path).convert("RGBA")
        out = remove(im)
        out.save(path)
        return "rembg"
    except Exception:
        pass
    im = Image.open(path).convert("RGB")
    a = np.asarray(im, np.float32)
    border = np.concatenate([a[:4].reshape(-1, 3), a[-4:].reshape(-1, 3), a[:, :4].reshape(-1, 3), a[:, -4:].reshape(-1, 3)])
    bg = np.median(border, 0)
    dist = np.sqrt(((a - bg) ** 2).sum(-1))
    g_dom = a[..., 1] - np.maximum(a[..., 0], a[..., 2])           # how much greener than red / blue
    alpha = np.clip((dist - 45) / 70, 0, 1)
    alpha = np.where(g_dom > 60, np.minimum(alpha, np.clip((110 - g_dom) / 50, 0, 1)), alpha)
    al = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    alpha = np.asarray(al, np.float32) / 255
    rgb = a.copy()
    spill = np.maximum(0, rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2]))
    rgb[..., 1] -= spill * np.clip(1.2 - alpha, 0, 1)                # green fringe on the edges
    out = np.dstack([np.clip(rgb, 0, 255), alpha * 255]).astype(np.uint8)
    Image.fromarray(out, "RGBA").save(path)
    return "green key"


def step_art(ctx, series, gender, idea="", count=4):
    import tools
    import narrator_render as R
    cfg, say = ctx["cfg"], ctx["say"]
    theme = (THEMES or {}).get(series) or "a calm storyteller"
    who = "a woman" if gender == "female" else "a man"
    prompt = (f"Original illustrated character, not a real person: {who}, {theme}"
              + (f", {idea}" if idea else "") +
              ". Half upper body from the waist up, facing the viewer and looking at the camera, mouth gently closed, "
              "eyes open, calm friendly expression, arms down, centred, the whole head, hair and shoulders inside the "
              "frame with empty space above the head. Digital painting, clean sharp outline, soft even studio light. "
              "Plain flat solid pure green background (#00FF00), nothing else in the background, no shadow, no text.")
    neg = "text, letters, watermark, frame, border, scenery, props, hands near the face, open mouth, cropped head, " \
          "extra limbs, green clothes, green hair, photograph of a real person, celebrity"
    if not tools.comfy_running(cfg):
        tools.start_comfy(cfg, say)
    engine = cfg.get("image_engine", "z_image_turbo")
    made = 0
    for i in range(int(count)):
        if ctx["stopped"]():
            return False
        name = f"{series} {gender} {time.strftime('%d %b')} #{i + 1}"
        nid = _new_id(f"{series}_{gender}")
        d = os.path.join(_root(cfg), nid)
        os.makedirs(d, exist_ok=True)
        raw = os.path.join(d, "painted.png")
        seed = random.randint(1, 2 ** 31)
        t0 = time.time()
        tools.comfy_generate(cfg, prompt, raw, engine, seed, 896, 1152, steps=8, negative=neg)
        base = os.path.join(d, "base.png")
        shutil.copyfile(raw, base)
        how = chroma_key(base)
        pts = R.guess_points(base)
        _write(os.path.join(d, "narrator.json"), {"name": name, "gender": gender, "series": [series], "source": "comfyui",
                                                  "prompt": prompt, "seed": seed, "engine": engine, "cut": how,
                                                  "made": time.strftime("%Y-%m-%d %H:%M:%S"), **pts})
        made += 1
        say(f"   {name}: {time.time() - t0:.0f} s, background removed ({how}); check the mouth point on the Narrator page")
    return made > 0


# ------------------------------------------------------------------ preview frames
def sample_frame(cfg, series, kind):
    """a frame from this series' newest Experimental video (or reel / picture) to preview on; '' = plain"""
    import experimental_workspace
    cache = os.path.join(_root(cfg), "_frames")
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, f"{re.sub(r'[^a-z0-9_]', '', series) or 'any'}_{kind}.jpg")
    if os.path.isfile(out) and time.time() - os.path.getmtime(out) < 6 * 3600:
        return out
    base = os.path.join(experimental_workspace.root(cfg), "Series")
    pats = [os.path.join(base, series or "*", "Episodes", "*", "Output", "reels", "_clean", "*.mp4"),
            os.path.join(base, series or "*", "Episodes", "*", "Output", "reels", "*.mp4")] if kind == "reel" else \
        [os.path.join(base, series or "*", "Episodes", "*", "Output", "_clean", "*.mp4"),
         os.path.join(base, series or "*", "Episodes", "*", "Output", "*.mp4")]
    vids = [v for p in pats for v in glob.glob(p) if not v.endswith(("_silent.mp4", ".part.mp4"))]
    if vids:
        v = max(vids, key=os.path.getmtime)
        try:
            import narrator_render as R
            _, _, _, dur = R.probe(_ffmpeg(cfg), v)
            subprocess.run([_ffmpeg(cfg), "-v", "error", "-y", "-ss", f"{max(0.0, dur * 0.4):.2f}", "-i", v,
                            "-frames:v", "1", "-q:v", "3", out], capture_output=True, timeout=60, creationflags=R.FLAGS)
            if os.path.isfile(out):
                return out
        except Exception:
            pass
    pics = glob.glob(os.path.join(base, series or "*", "Episodes", "*", "03_Artwork", "*.png"))
    if pics:
        from PIL import Image
        im = Image.open(max(pics, key=os.path.getmtime)).convert("RGB")
        if kind == "reel":
            w = int(im.height * 9 / 16)
            im = im.crop(((im.width - w) // 2, 0, (im.width - w) // 2 + w, im.height))
        im.save(out, quality=88)
        return out
    return ""


def preview(cfg, q):
    import narrator_render as R
    m = get(cfg, q.get("id"))
    if not m:
        raise ValueError("choose a narrator")
    kind = "reel" if q.get("kind") == "reel" else "wide"
    W, H = (960, 540) if kind == "wide" else (405, 720)
    pl = {"corner": q.get("corner") if q.get("corner") in CORNERS else DEFAULT[kind]["corner"],
          "size": float(q.get("size") or DEFAULT[kind]["size"]), "margin": float(q.get("margin") or 0),
          "dock": str(q.get("dock", "1")) != "0", "shadow": str(q.get("shadow", "1")) != "0"}
    series = str(q.get("series") or "")
    frame = sample_frame(cfg, series, kind)
    s = _merge(DEFAULT, {"narrator": m["id"], kind: pl})
    room = subtitle_room(cfg, s, kind, W, H)
    b0, b1 = SUB_BAND[kind]
    side = max(150 / (1920 if kind == "wide" else 1080), room)
    box = [int(W * side), int(H * b0), int(W * (1 - side)), int(H * b1)]
    im, info = R.preview(m, frame, W, H, pl, float(q.get("open") or 0), int(q.get("blink") or 0), True, box)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return {"img": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "w": W, "h": H,
            "figure": info["figure"], "mode": info["mode"], "frame": frame, "subtitle_room": room}


def mouth_test(cfg, q):
    """the narrator's face with the mouth at closed / half / open and a blink, side by side (checks the points)"""
    import narrator_render as R
    import numpy as np
    from PIL import Image
    m = get(cfg, q.get("id"))
    if not m:
        raise ValueError("choose a narrator")
    sp = R.Sprite(m, 640, False)
    tiles = [sp.state(0, 0, 0), sp.state(R.LEVELS // 2, 0, 0), sp.state(R.LEVELS - 1, 0, 0), sp.state(0, 2, 0)]
    if sp.mouth:
        cy = int(sp.mouth[1])
        y0 = max(0, cy - int(sp.mw * 2.6))
        tiles = [t[y0 + sp.pad: y0 + sp.pad + int(sp.mw * 4.2)] for t in tiles]
    h = max(t.shape[0] for t in tiles)
    w = sum(t.shape[1] for t in tiles)
    canvas = Image.new("RGBA", (w, h), (60, 64, 72, 255))
    x = 0
    for t in tiles:
        canvas.alpha_composite(Image.fromarray(np.ascontiguousarray(t), "RGBA"), (x, 0))
        x += t.shape[1]
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "JPEG", quality=85)
    return {"img": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "mode": sp.mode}


def test_render(cfg, q):
    """exactly what the video renderer draws (same code, same size in video pixels): the whole figure, then
    the face at 1:1 closed / half / open / blink"""
    import narrator_render as R
    import numpy as np
    from PIL import Image, ImageDraw
    m = get(cfg, q.get("id"))
    if not m:
        raise ValueError("choose a narrator")
    kind = "reel" if q.get("kind") == "reel" else "wide"
    W, H = (1920, 1080) if kind == "wide" else (1080, 1920)
    size = min(0.9, max(0.08, float(q.get("size") or DEFAULT[kind]["size"])))
    sp = R.Sprite(m, max(40, int(round(size * H))), str(q.get("shadow", "1")) != "0")
    states = [(0, 0, "closed"), (R.LEVELS // 2, 0, "half"), (R.LEVELS - 1, 0, "open"), (0, 2, "blink")]
    tiles = [Image.fromarray(sp.state(lv, bl, 0), "RGBA") for lv, bl, _ in states]
    if sp.mouth:
        mx, my = sp.mouth[0] + sp.pad, sp.mouth[1] + sp.pad
        half = max(30, int(sp.mw * 2.2), int(abs(my - (sp.eyes[0][1] + sp.pad if sp.eyes else my)) * 1.6))
        box = (int(mx - half), int(my - half * 1.25), int(mx + half), int(my + half * 0.75))
    else:
        box = (0, 0, sp.cw, min(sp.ch, sp.cw))
    faces = [t.crop(box) for t in tiles]
    zoom = max(1, min(6, int(220 / max(1, box[2] - box[0]))))
    fw, fh = (box[2] - box[0]) * zoom, (box[3] - box[1]) * zoom
    full_h = max(fh, 260)
    full = tiles[0].resize((max(1, int(sp.cw * full_h / sp.ch)), full_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (full.width + 4 * (fw + 8) + 8, full_h + 22), (52, 56, 64, 255))
    canvas.alpha_composite(full, (0, 0))
    d = ImageDraw.Draw(canvas)
    x = full.width + 8
    for f, (_, _, label) in zip(faces, states):
        canvas.alpha_composite(f.resize((fw, fh), Image.NEAREST), (x, 0))
        d.text((x + 4, fh + 4), f"{label}" + (f"  x{zoom}" if zoom > 1 else ""), fill=(235, 235, 235, 255))
        x += fw + 8
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "JPEG", quality=88)
    mouth_px = round(sp.mw, 1)
    note = ""
    if sp.mode == "anchor" and mouth_px < 14:
        note = f"The mouth is only {mouth_px:g} px wide in the video at this size, so its movement is hard to see. Make the narrator bigger, or set a wider mouth."
    if m.get("guessed", True):
        note = (note + " " if note else "") + "These points are still the automatic guess."
    return {"img": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "mode": sp.mode,
            "figure": [sp.w, sp.h], "mouth_px": mouth_px, "scale": round(sp.s, 4), "note": note, "name": m.get("name")}


# ------------------------------------------------------------------ HTTP (Experimental page)
def state(cfg):
    import tools
    table = experimental_table(cfg)
    gids = list(getattr(tools, "GENRES", {}).keys())
    ser = ["sukoon"] + [g for g in gids if g != "sukoon"]
    rows = []
    for m in all_narrators(cfg):
        row = {k: m.get(k) for k in ("id", "name", "gender", "series", "mouth", "mouth_w", "eyes", "eye_w", "open_h", "size",
                                     "source", "made", "guessed", "cut")}
        row.update({"files": {s: m[s] for s in SLOTS if m.get(s)}, "aspect": round(_aspect(m), 3)})
        rows.append(row)
    try:
        import tinker
        working = tinker.working_settings().get("tables", {}).get("narrator.SERIES") or {}
    except Exception:
        working = {}
    sets = {g: series_settings(g, table) for g in ser}
    used = {g: (sets[g]["narrator"] if get(cfg, sets[g]["narrator"]) else auto_pick(cfg, g)) for g in ser}
    return {"narrators": rows, "series": sets, "used": used, "working": working,
            "themes": experimental_themes(cfg), "corners": CORNERS, "root": _root(cfg), "defaults": DEFAULT}


def _save_series(cfg, series, value):
    import tinker
    table = experimental_table(cfg)
    cur = series_settings(series, table)
    new = _merge(cur, value or {})
    for k in ("wide", "reel"):
        new[k]["corner"] = new[k]["corner"] if new[k].get("corner") in CORNERS else DEFAULT[k]["corner"]
        for f, lo, hi in (("size", 0.08, 0.9), ("margin", 0.0, 0.2), ("start_s", 0.0, 30.0)):
            new[k][f] = round(min(hi, max(lo, float(new[k].get(f) or 0))), 4)
        for f in ("dock", "shadow", "blink"):
            new[k][f] = bool(new[k].get(f))
    new["on"] = bool(new.get("on"))
    new["narrator"] = str(new.get("narrator") or "")
    table = dict(table)
    table[series] = new
    return tinker.save(cfg, "tables", "narrator.SERIES", table)


def _decode_png(data):
    from PIL import Image
    raw = base64.b64decode(str(data).split(",", 1)[-1])
    if len(raw) > 40_000_000:
        raise ValueError("picture too big")
    im = Image.open(io.BytesIO(raw))
    im.load()
    if im.width * im.height > 36_000_000:
        raise ValueError("picture too big")
    return im.convert("RGBA")


def handle_get(cfg, p, q):
    if p == "/api/narrator":
        return 200, state(cfg)
    if p == "/api/narrator/preview":
        return 200, preview(cfg, q)
    if p == "/api/narrator/mouth_test":
        return 200, mouth_test(cfg, q)
    if p == "/api/narrator/test_render":
        return 200, test_render(cfg, q)
    return 404, {"error": "unknown narrator request"}


def handle_post(cfg, p, b, jobs=None):
    import narrator_render as R
    if p == "/api/narrator/upload":
        slot = str(b.get("slot") or "base")
        if slot not in SLOTS:
            raise ValueError("unknown picture slot")
        im = _decode_png(b.get("data"))
        if slot == "base":
            a = im.split()[-1]
            if a.getextrema()[0] > 250:
                raise ValueError("this picture has no transparent background - use a PNG with transparency")
        nid = b.get("id") or ""
        if nid:
            d = _dir(cfg, nid)
            if not os.path.isdir(d):
                raise ValueError("unknown narrator")
        elif slot == "base":
            nid = _new_id(b.get("name") or "narrator")
            d = _dir(cfg, nid)
            os.makedirs(d, exist_ok=True)
        else:
            raise ValueError("upload the main picture first")
        f = os.path.join(d, slot + ".png")
        if os.path.isfile(f):
            os.makedirs(os.path.join(d, "_old"), exist_ok=True)
            shutil.move(f, os.path.join(d, "_old", f"{slot}_{time.strftime('%Y%m%d_%H%M%S')}.png"))
        im.save(f)
        meta = _load(os.path.join(d, "narrator.json"), {})
        if slot == "base":
            pts = R.guess_points(f)
            meta.update(pts)
            meta.setdefault("made", time.strftime("%Y-%m-%d %H:%M:%S"))
            meta.setdefault("source", "upload")
            meta["name"] = str(b.get("name") or meta.get("name") or os.path.splitext(str(b.get("filename") or "narrator"))[0])[:80]
            meta["gender"] = "female" if b.get("gender") == "female" else ("male" if b.get("gender") == "male" else meta.get("gender", "male"))
            if b.get("series"):
                meta["series"] = [str(x) for x in (b["series"] if isinstance(b["series"], list) else [b["series"]])][:12]
        _write(os.path.join(d, "narrator.json"), meta)
        return 200, {"ok": True, "id": nid}
    if p == "/api/narrator/meta":
        nid = _safe_id(b.get("id"))
        d = _dir(cfg, nid)
        meta = _load(os.path.join(d, "narrator.json"), None)
        if meta is None:
            raise ValueError("unknown narrator")
        if "name" in b:
            meta["name"] = str(b["name"])[:80]
        if b.get("gender") in ("male", "female"):
            meta["gender"] = b["gender"]
        if isinstance(b.get("series"), list):
            meta["series"] = [str(x) for x in b["series"]][:12]
        for k in ("mouth",):
            if isinstance(b.get(k), list) and len(b[k]) == 2:
                meta[k] = [int(round(float(v))) for v in b[k]]
                meta["guessed"] = False
        if isinstance(b.get("eyes"), list):
            meta["eyes"] = [[int(round(float(v))) for v in e] for e in b["eyes"] if isinstance(e, list) and len(e) == 2][:2]
        for k in ("mouth_w", "eye_w"):
            if b.get(k) not in (None, ""):
                meta[k] = max(2, int(round(float(b[k]))))
        if b.get("open_h") not in (None, ""):
            meta["open_h"] = round(min(0.8, max(0.1, float(b["open_h"]))), 3)
        _write(os.path.join(d, "narrator.json"), meta)
        return 200, {"ok": True}
    if p == "/api/narrator/guess":
        m = get(cfg, b.get("id"))
        if not m:
            raise ValueError("unknown narrator")
        meta = _load(os.path.join(m["dir"], "narrator.json"), {})
        meta.update(R.guess_points(m["mouth_closed"] or m["base"]))
        _write(os.path.join(m["dir"], "narrator.json"), meta)
        return 200, {"ok": True, **{k: meta[k] for k in ("mouth", "mouth_w", "eyes", "eye_w")}}
    if p == "/api/narrator/remove_slot":
        m = get(cfg, b.get("id"))
        slot = str(b.get("slot"))
        if not m or slot not in SLOTS[1:] or not m.get(slot):
            raise ValueError("nothing to remove")
        os.makedirs(os.path.join(m["dir"], "_old"), exist_ok=True)
        shutil.move(m[slot], os.path.join(m["dir"], "_old", f"{slot}_{time.strftime('%Y%m%d_%H%M%S')}.png"))
        return 200, {"ok": True}
    if p == "/api/narrator/delete":
        d = _dir(cfg, b.get("id"))
        if not os.path.isdir(d):
            raise ValueError("unknown narrator")
        trash = os.path.join(_root(cfg), "_removed")
        os.makedirs(trash, exist_ok=True)
        shutil.move(d, os.path.join(trash, os.path.basename(d) + time.strftime("_%Y%m%d_%H%M%S")))
        return 200, {"ok": True, "moved_to": trash}
    if p == "/api/narrator/series":
        series = str(b.get("series") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{2,30}", series):
            raise ValueError("unknown series")
        return 200, _save_series(cfg, series, b.get("value") or {})
    return 404, {"error": "unknown narrator request"}
