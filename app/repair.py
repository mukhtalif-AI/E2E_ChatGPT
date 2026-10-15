"""repair.py - the repair man: check a finished episode video or reel, list what is wrong, fix what you tick.

CHECK (no graphics card needed)
  voice      from the voice engine's own notes on every line (_lines.json): stutters / repeated sounds, words heard
             wrong, lines much shorter (swallowed words) or longer (dragged) than they should be
  motion     every frame of the video, tiny and grey: sudden jumps, frozen stretches, pops where the living loop
             starts again
  subtitles  how the on-screen lines are timed (an evenly spaced, clock-like rhythm = the old fixed timing)
FIX (one job, in order; the old video is kept in Output\\_old)
  voice lines   remake only the ticked lines with your voice, music again (same track, same total length)
  subtitles     choose the on-screen lines again: by meaning, timed by the content
  motion        make the living artwork loop again (if the loop popped), then draw the video again (reels kept)
  a reel        made again from the same moment (same start), the old one goes to reels\\_old
"""
import json, os, re, shutil, subprocess, time

import numpy as np


def _load(p, d=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def _ts(t):
    t = max(0.0, float(t)); return f"{int(t // 60)}:{int(t % 60):02d}"


def find_parts(cfg, video):
    """episode name, narration folder, studio work folder, reel info for a video (episode or reel)"""
    is_reel = os.path.basename(os.path.dirname(video)).lower() == "reels"
    out_dir = os.path.dirname(os.path.dirname(video)) if is_reel else os.path.dirname(video)
    ep_dir = os.path.dirname(out_dir)
    name = os.path.basename(ep_dir)
    main = [f for f in os.listdir(out_dir) if f.endswith(".mp4") and "_preview" not in f] if os.path.isdir(out_dir) else []
    # For an episode repair, honor the exact video selected in the UI. Picking the
    # first MP4 in the folder can associate its narration/cache with another render.
    main_video = video if not is_reel and os.path.isfile(video) else (os.path.join(out_dir, sorted(main)[-1]) if main else "")
    local_nar = os.path.join(ep_dir, "02_Narration", "narrations", name)
    primary_nar = os.path.join(cfg.get("narrations_dir") or os.path.join(cfg["sukoonvoice_media"], "narrations"), name)
    legacy_nar = os.path.join(cfg["sukoonvoice_media"], "narrations", name)
    nar = next((p for p in (local_nar, primary_nar, legacy_nar) if os.path.isdir(p)), primary_nar)
    local_work = os.path.join(ep_dir, "_work")
    legacy_work = os.path.join(cfg["studio_dir"], "work", os.path.splitext(os.path.basename(main_video or video))[0])
    work = local_work if os.path.isdir(local_work) else legacy_work
    info = _load(os.path.splitext(main_video)[0] + ".reel_info.json", {}) if main_video else {}
    genre = _load(os.path.splitext(main_video)[0] + ".genre.json", {}) if main_video else {}
    return {"is_reel": is_reel, "name": name, "episode": ep_dir, "output": out_dir, "main_video": main_video,
            "narration": nar if os.path.isdir(nar) else "", "work": work if os.path.isdir(work) else "",
            "loop": info.get("loop", ""), "genre": genre.get("genre", ""), "genre_meta": genre}


# ------------------------------------------------------------------ checks
def check_voice(parts):
    nar = parts["narration"]
    lines = _load(os.path.join(nar, "_lines.json"), {}) if nar else {}
    timing = {x["n"]: x for x in (_load(os.path.join(nar, "timing.json"), []) or [])} if nar else {}
    lead = float((_load(os.path.join(nar, "music_shape.json"), {}) or {}).get("lead_s", 0)) if nar else 0
    out = []
    for k, x in sorted(lines.items(), key=lambda kv: int(kv[0])):
        n = int(k)
        why = []
        if float(x.get("repeat") or 0) >= 0.85:
            why.append("stutter / repeated sound")
        le = x.get("letter_error")
        if le is not None and float(le) > 0.15:
            why.append(f"words heard wrong ({float(le):.0%} of letters)")
        dur, want = float(x.get("dur") or 0), float(x.get("want") or 0)
        if want and dur / want < 0.55:
            why.append("much too short - words may be swallowed")
        elif want and dur / want > 1.9:
            why.append("much too long - dragged or a strange pause")
        if not why and x.get("ok") is False:
            why.append("did not pass the checks after all retakes")
        if why:
            t = timing.get(n, {})
            out.append({"line": n, "text": x.get("text", ""), "why": why, "at": round(float(t.get("start", 0)) + lead, 2)})
    return out


def frame_diffs(ffmpeg, video, w=64, h=36):
    """mean change between neighbouring frames (0..255), every frame, tiny grey copy"""
    r = subprocess.run([ffmpeg, "-v", "error", "-i", video, "-an", "-vf", f"scale={w}:{h}:flags=area,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    n = len(a) // (w * h)
    f = a[:n * w * h].reshape(n, h, w).astype(np.int16)
    return np.abs(np.diff(f, axis=0)).mean(axis=(1, 2)) if n > 1 else np.zeros(0)


def _fps(ffmpeg, video):
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", video], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"([\d.]+) fps", r)
    return float(m.group(1)) if m else 24.0


def analyse_motion(d, fps, loop_len_s=0.0):
    """d = frame-to-frame change. -> problems: jump (one frame leaps), abrupt camera move (the view starts moving
    hard from standstill = 'stutter movement'), loop pop, frozen."""
    out = []
    if len(d) < 10:
        return out
    k = 48
    pad = np.pad(d, (k, k), mode="edge")
    loc = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(len(d))]) if len(d) < 60000 else \
        np.convolve(d, np.ones(2 * k + 1) / (2 * k + 1), "same")
    nb = np.maximum(np.r_[d[1:2], d[:-1]], np.r_[d[1:], d[-2:-1]])          # bigger of the two neighbours
    last = -1e9
    for i in np.where((d > np.maximum(5 * loc, loc + 1.0)) & (d > 0.8))[0]:
        t = (i + 1) / fps
        if t - last < 2:
            continue
        isolated = d[i] > 2.5 * nb[i]
        sustained = (d[i:i + 6] > 3 * loc[i]).all()
        before = d[max(0, i - 3):i]
        abrupt = sustained and len(before) and d[i] > 3 * max(float(before.max()), 0.05)
        if not (isolated or abrupt):
            continue                                          # a smooth, eased camera move: fine
        last = t
        seam = loop_len_s > 0 and min(t % loop_len_s, loop_len_s - t % loop_len_s) < 0.15
        kind = "loop pop" if seam and isolated else "jump" if isolated else "abrupt camera move"
        why = {"loop pop": "the living artwork jumps where its loop starts again",
               "jump": f"one frame leaps (change {d[i]:.1f} vs {loc[i]:.1f} around it)",
               "abrupt camera move": "the view starts moving hard from standstill (stutter movement)"}[kind]
        out.append({"at": round(float(t), 2), "kind": kind, "why": why})
    still, run = d < 0.02, 0
    for i, st in enumerate(np.append(still, False)):
        if st:
            run += 1; continue
        if run >= int(fps):
            a = (i - run + 1) / fps
            around = np.concatenate([d[max(0, i - run - 48):i - run], d[i:i + 48]])
            if len(around) and np.median(around) > 0.05:
                out.append({"at": round(float(a), 2), "kind": "frozen", "why": f"the picture stands still for {run / fps:.1f} s"})
        run = 0
    return out[:60]


def check_motion(cfg, video, loop_len_s=0.0, say=print):
    fps = _fps(cfg["ffmpeg"], video)
    say(f"   looking at every frame of {os.path.basename(video)} ...")
    t0 = time.time()
    d = frame_diffs(cfg["ffmpeg"], video)
    say(f"   {len(d) + 1} frames in {time.time() - t0:.0f} s")
    return analyse_motion(d, fps, loop_len_s)


def check_subtitles(parts):
    if parts.get("genre") and parts.get("narration"):
        timing = _load(os.path.join(parts["narration"], "timing.json"), []) or []
        if len(timing) >= 4:
            gaps = np.diff([float(x["start"]) for x in timing])
            cv = float(np.std(gaps) / max(1e-6, np.mean(gaps)))
            return {"count": len(timing), "mean_gap_s": round(float(np.mean(gaps)), 1), "even": cv < 0.35,
                    "note": "timing follows the spoken narration" if cv >= 0.35 else "lines come at an almost fixed rhythm"}
    m = _load(os.path.join(parts["work"], "moments.json"), []) if parts["work"] else []
    if len(m) < 4:
        return {"count": len(m), "even": False, "note": "few on-screen lines"}
    gaps = np.diff([float(x["start"]) for x in m])
    cv = float(np.std(gaps) / max(1e-6, np.mean(gaps)))
    long_lines = [x["text"] for x in m if len(x.get("words", [])) > 12]
    return {"count": len(m), "mean_gap_s": round(float(np.mean(gaps)), 1), "even": cv < 0.35,
            "note": ("lines come at an almost fixed rhythm (old timing)" if cv < 0.35 else "timing follows the content"),
            "long_lines": long_lines[:5]}


def check(ctx, video):
    cfg, say = ctx["cfg"], ctx["say"]
    p = find_parts(cfg, video)
    rep = {"video": video, "checked": time.strftime("%Y-%m-%d %H:%M:%S"), "parts": p}
    if not p["is_reel"]:
        rep["voice"] = check_voice(p)
        say(f"   voice: {len(rep['voice'])} line(s) to look at" if p["narration"] else "   voice: no narration notes for this video")
        for v in rep["voice"][:12]:
            say(f"      {_ts(v['at'])}  line {v['line']}: {v['text'][:50]}  - {', '.join(v['why'])}")
        rep["subtitles"] = check_subtitles(p)
        say(f"   subtitles: {rep['subtitles']['count']} lines, {rep['subtitles']['note']}")
    loop_len = 0.0
    if p["loop"] and os.path.exists(p["loop"]):
        try:
            r = subprocess.run([cfg["ffmpeg"], "-hide_banner", "-i", p["loop"]], capture_output=True, text=True,
                               errors="replace").stderr
            hh, mm, ss = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r).groups()
            loop_len = int(hh) * 3600 + int(mm) * 60 + float(ss)
        except Exception:
            pass
    rep["motion"] = check_motion(cfg, video, 0 if p["is_reel"] else loop_len, say)
    say(f"   motion: {len(rep['motion'])} place(s) to look at")
    for v in rep["motion"][:12]:
        say(f"      {_ts(v['at'])}  {v['kind']}: {v['why']}")
    json.dump(rep, open(os.path.splitext(video)[0] + ".check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True


# ------------------------------------------------------------------ fixes
def keep_old(path):
    """move a file to <its folder>\\_old\\HHMMSS_<name> (never deleted)"""
    if path and os.path.exists(path):
        d = os.path.join(os.path.dirname(path), "_old")
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, time.strftime("%H%M%S_") + os.path.basename(path))
        shutil.move(path, dst)
        return dst
    return ""


def prepare_rerender(ctx, video, new_loop=False):
    """old video (+ its QA frames) aside; the loop aside too when it has to be made again"""
    cfg, say = ctx["cfg"], ctx["say"]
    p = find_parts(cfg, video)
    moved = keep_old(p["main_video"])
    if moved:
        say(f"   old video kept: {moved}")
    if new_loop and p["loop"] and os.path.exists(p["loop"]):
        for ext in ("", ".json"):
            m = keep_old(p["loop"] + ext)
            if m:
                say(f"   old living loop kept: {m}")
    return True


# ------------------------------------------------------------------ quick voice fix (the video is NOT drawn again)
def save_voice(nar):
    """before remaking lines: keep the voice + its timing exactly as they are in the finished video"""
    for a, b in (("narration.wav", "_before_fix.wav"), ("timing.json", "_before_fix_timing.json")):
        src = os.path.join(nar, a)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(nar, b))
    return True


def _read_wav(ffmpeg, path, sr=48000):
    r = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, np.float32).copy()


def patch_voice(cfg, nar, lines, say=print):
    """put each remade line into the SAME place of the old voice (the time slot of the old line + its pause), so
    everything after it stays where it was -> the finished video only needs its sound track replaced.
    Returns True, or a reason why it can't be done this way (a new line much longer than its place)."""
    import json as _j
    sr = 48000
    ff = cfg["ffmpeg"]
    old_p, tim_p = os.path.join(nar, "_before_fix.wav"), os.path.join(nar, "_before_fix_timing.json")
    if not (os.path.exists(old_p) and os.path.exists(tim_p)):
        return "the voice before the fix was not kept"
    voice = _read_wav(ff, old_p, sr)
    timing = {x["n"]: x for x in _j.load(open(tim_p, encoding="utf-8"))}
    order = sorted(timing)
    for n in lines:
        if n not in timing:
            return f"line {n} is not in this narration"
        t = timing[n]
        a, b = int(t["start"] * sr), int(t["end"] * sr)
        nxt = timing[order[order.index(n) + 1]]["start"] if order.index(n) + 1 < len(order) else len(voice) / sr
        room = int((nxt - 0.25) * sr) - a                       # the line + most of its pause
        new = _read_wav(ff, os.path.join(nar, "lines", f"{n:04d}.wav"), sr)
        if len(new) == 0:
            return f"line {n}: no new audio"
        if len(new) > room:
            return f"line {n} came out {len(new) / sr - (b - a) / sr:+.1f} s longer than before - it does not fit its place"
        old_rms = float(np.sqrt(np.mean(voice[a:b] ** 2))) or 1e-4
        new = new * (old_rms / (float(np.sqrt(np.mean(new ** 2))) or 1e-4))
        f = int(0.015 * sr)
        voice[a:a + room] = 0.0                                 # old line out (its pause stays silent)
        new[:f] *= np.linspace(0, 1, f); new[-f:] *= np.linspace(1, 0, f)
        voice[a:a + len(new)] = new
        say(f"   line {n}: {len(new) / sr:.1f} s in the place of {(b - a) / sr:.1f} s (nothing after it moves)")
    pk = float(np.max(np.abs(voice))) or 1
    if pk > 0.99:
        voice *= 0.99 / pk
    out = os.path.join(nar, "narration.wav")
    p = subprocess.Popen([ff, "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                          "-c:a", "pcm_s16le", out + ".part.wav"], stdin=subprocess.PIPE)
    p.stdin.write(voice.astype("<f4").tobytes()); p.stdin.close(); p.wait()
    os.replace(out + ".part.wav", out)
    shutil.copyfile(tim_p, os.path.join(nar, "timing.json"))  # the timing is the old one again
    return True


def new_soundtrack(cfg, video, audio, say=print):
    """the finished video with a new sound track; the picture is copied as it is (seconds, not minutes)"""
    old = keep_old(video)
    say(f"   old video kept: {old}")
    r = subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", old, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                        video + ".part.mp4"], capture_output=True, text=True, errors="replace")
    if r.returncode:
        shutil.move(old, video)
        raise RuntimeError("could not put the new sound into the video: " + r.stderr[-400:])
    os.replace(video + ".part.mp4", video)
    return True
