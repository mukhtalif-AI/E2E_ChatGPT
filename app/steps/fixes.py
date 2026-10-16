"""steps/fixes.py - repair page and reels from a finished video. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4i).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


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
        if not tools.horror_sound(ctx, p["name"], ser["pictures"], ser.get("track") or "", ser["genre"],
                            f"total:{total:.3f}" if total else ser.get("tail", "1min")):
            return False
        if ok is True:
            repair.new_soundtrack(cfg, video, tools._audio_of(p["narration"]), say)
            say("   done - the picture was kept, only the sound is new (scenes, music and effects as before)")
            return True
        say(f"   {ok} -> the {tools.GENRES[ser['genre']]['label']} video is drawn again (the old one is kept)")
        repair.keep_old(video)
        return tools.horror_video(ctx, p["name"], ser["pictures"], p["narration"], video, ser.get("title", ""), ser["genre"])
    ok = repair.patch_voice(cfg, p["narration"], nums, say)
    shp = repair._load(os.path.join(p["narration"], "music_shape.json"), {}) or {}
    total = _duration(os.path.join(p["narration"], "narration_music.wav")) / 60 if shp else 0
    if shp and not tools.shape_mix(ctx, p["name"], shp.get("track", ""), f"total:{total:.3f}"):
        return False
    tools.studio_prepare(ctx, p["episode"], p["narration"])
    if ok is True:
        repair.new_soundtrack(cfg, video, tools._audio_of(p["narration"]), say)
        say("   done - the picture was kept, only the sound is new (reels made earlier keep their old sound)")
        return True
    say(f"   {ok} -> drawing the whole video again instead")
    if not repair.prepare_rerender(ctx, video):
        return False
    tools.free_gpu(ctx)
    return tools.studio_video(ctx, p["episode"], 0, 0, 0, loop_img, ["--redo-transcript", "--redo-captions"])


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
    if not tools.horror_sound(ctx, p["name"], ser["pictures"], ser.get("track") or "", ser["genre"],
                        f"total:{total:.3f}" if total else ser.get("tail", "1min")):
        return False
    moved = repair.keep_old(video)
    if moved:
        say(f"   old video kept: {moved}")
    tools.free_gpu(ctx)
    return tools.horror_video(ctx, p["name"], ser["pictures"], p["narration"], video,
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
        d["genre"] = next((g for g in tools.GENRES if low.startswith(g) or tools.GENRES[g]["label"] in p["name"]), "bhay")
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
    f = os.path.join(tools.narration_dir(cfg, name, existing=True), "hub_series.json")
    try:
        d = json.load(open(f, encoding="utf-8")) if os.path.exists(f) else {}
        d.update({k: v for k, v in kv.items() if v not in (None, "")})
        json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def remix_like_before(ctx, name):
    """after phrases were remade: the narration gets the same music bed (series: + ambience / effects) again"""
    cfg, say = ctx["cfg"], ctx["say"]
    nar = tools.narration_dir(cfg, name, existing=True)
    total = _duration(os.path.join(nar, "narration_music.wav")) / 60
    ser = series_of_video(cfg, {"name": os.path.basename(nar), "narration": nar})
    if ser:
        return tools.horror_sound(ctx, name, ser["pictures"], ser.get("track") or "", ser["genre"],
                            f"total:{total:.3f}" if total else "1min")
    shp = os.path.join(nar, "music_shape.json")
    if os.path.exists(shp) and total:
        tr = (json.load(open(shp, encoding="utf-8")) or {}).get("track", "")
        if tr:
            return tools.shape_mix(ctx, name, tr, f"total:{total:.3f}")
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
