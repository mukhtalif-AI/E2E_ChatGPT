"""steps/sukoon_video.py - Sukoon video and slideshow. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4e).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


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
            sj = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".story.json")
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
            sj = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".story.json")
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
            o[i] = os.path.join(tools.fonts_dir(cfg), o[i])
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
    tmp = os.path.join(tools.ctx_folder(ctx, "temporary"), ctx["job"]["id"])
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
            fp = c["font"] if os.path.isabs(c["font"]) else os.path.join(tools.fonts_dir(cfg), c["font"])
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
