"""music_check.py - listen to the finished music mix of an episode and catch what you used to hear only afterwards.

Why (19 Oct 2026): every music fix so far was for one thing heard after a run (stingers back to back, a whisper over the
voice, a 1.5 s swell-in, music that stopped and carried on, a groovy or sung track, Sukoon music ending with the voice).
Nothing measured the finished mix before you heard it. This step runs by itself after the music step of every episode
(Experimental, all series), measures narration_music.wav against the voice alone (narration.wav), corrects what it can,
measures again and writes music_report.txt next to the mix. It never stops the run and never asks.

How it hears the parts: the voice is known (narration.wav, placed lead_s into the mix), so the mix minus the voice is
the music + ambience + effects. When narration_music_plain.wav (music + voice before ambience / effects) belongs to the
same mix, the effects are split from the music too.

Rules (thresholds: Tinker table music_check.RULES; stinger spacing per series: emotion_music.STINGERS):
  swell        the music's opening rise (30 dB under its level -> 3 dB under) takes at least swell_min_s
  jumps        the music level changes by jump_db or more within a quarter second (a cut, a sudden entry)
  dropouts     the music falls hole_db under its own running level for a moment (at most 3 s) and comes back
  gaps         no music for more than gap_max_s in the story (outside the silences planned before a heavy line)
  stingers     hits / risers / effects closer than the series' min_gap_s, or more than per_min a minute
  fx_speech    an effect as loud as the voice (within fx_speech_db, in the speech band) while a word is spoken
  clarity      the voice stands less than clarity_p10_db above everything else in the worst tenth of the lines
  audible      the music under the voice is quieter than audible_db (nobody hears it) or louder than loud_db
  ending       the music plays on tail_min_s after the last word and fades out over fade_min_s (not cut off)
  tracks       a track that does not fit its mood folder (audio_screen: misfit, song, rumble), a track change with no
               change of mood, a change back to an earlier track, more tracks than the series allows, a later track
               lighter than the story (emotion_music.BRIGHT)
  heavy        a weight-3 line of the emotion map really has its silence before it

Fixes (when a rule fails and RULES fix is on): a slower eased swell, slew-limited level changes (a jump becomes a short
ramp), the weaker of two stingers too close together turned down, effects ducked under the words, music ducked where the
voice is not clear, an eased fade at the end. A track that is itself the problem (misfit, song, drop-outs, inaudible) or
music that is missing is fixed by making the music again without that track (once; RULES repick). Taste - whether this
track is the right one for this story - is not something these numbers can judge.

Run on a finished episode (reads only; --fix writes a corrected copy where you say):
    python app\\music_check.py "D:\\MukhtalifGPT\\Series\\bhay\\Episodes\\Bhay-Attic-Doll" [--json out.json] [--fix out.wav]
"""
import glob, json, os, re, subprocess, sys, time

# Tinker: music_check.RULES (19 Oct 2026, calibrated on 24 finished episodes - see CHANGELOG)
RULES = {
    "on": True, "fix": True, "repick": True,
    "swell_min_s": 2.0,
    "jump_db": 12.0, "jumps_max": 2,
    "hole_db": 15.0, "dropouts_per_min_max": 1.0,
    "gap_max_s": 4.0,
    "sting_level_db": -15.0, "sting_rise_db": 10.0, "sting_close_max": 1, "sting_rate_x": 1.5,
    "fx_speech_db": -6.0, "fx_speech_s": 0.8, "fx_speech_max": 0,
    "clarity_p10_db": 3.0,
    "audible_db": -23.0, "loud_db": -7.0,
    "tail_min_s": {"sukoon": 20.0, "default": 5.0}, "fade_min_s": 1.0, "cut_db": -20.0,
    "heavy_drop_db": 10.0,
    "slew_db_s": 24.0,
}
SR = 16000                       # analysis rate (mono)
FPS = 20                         # level frames per second
HOP = SR // FPS


def _base(p):
    return re.split(r"[\\/]", str(p or ""))[-1]


def _q(x):
    return f"{int(x // 60)}:{int(x % 60):02d}"


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def decode(ffmpeg, path, sr=SR, ch=1):
    import numpy as np
    p = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-ac", str(ch), "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
    a = np.frombuffer(p.stdout, np.float32)
    return a.reshape(-1, ch) if ch > 1 else a


def levels(x, hop=HOP):
    """dB per frame (mean power)"""
    import numpy as np
    n = len(x) // hop
    return 10 * np.log10((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(1) + 1e-12)


def smooth_db(db, k):
    """power average of k frames"""
    import numpy as np
    if k <= 1:
        return db
    p = 10 ** (db / 10)
    return 10 * np.log10(np.convolve(np.pad(p, k // 2, mode="edge"), np.ones(k) / k, mode="valid")[: len(db)] + 1e-12)


def _place(v, off, n):
    """the voice alone in mix time (off samples late), n samples long"""
    import numpy as np
    out = np.zeros(n, np.float32)
    a, b = max(0, off), min(n, off + len(v))
    if b > a:
        out[a:b] = v[a - off:b - off]
    return out


def _best_offset(mx, v, off, search, step=1):
    """the sample offset of the voice in the mix (least-squares fit on the busiest 20 s of speech)"""
    import numpy as np
    w = 20 * SR
    if len(v) <= w:
        a = 0
    else:
        e = np.convolve(levels(v, SR) > -45, np.ones(20), mode="valid")
        a = int(np.argmax(e)) * SR if len(e) else 0
    vv = v[a:a + w].astype(np.float64)
    best = (1e30, off)
    for o in range(off - search, off + search + 1, step):
        s = o + a
        if s < 0 or s + len(vv) > len(mx):
            continue
        m = mx[s:s + len(vv)].astype(np.float64)
        g = float(np.dot(m, vv) / (np.dot(vv, vv) + 1e-12))
        r = float(((m - g * vv) ** 2).sum())
        if r < best[0]:
            best = (r, o)
    return best[1]


def _find_lead(mx, v):
    """Sukoon: the voice engine mixed its own music, no music_shape.json - find where the voice starts in the mix"""
    import numpy as np
    lv, lm = levels(v), levels(mx)
    ev = (lv > np.percentile(lv, 95) - 18).astype(float)
    ev -= ev.mean()
    seg = lm[: 60 * FPS + len(ev)]
    lm2 = seg - np.convolve(seg, np.ones(41) / 41, mode="same")
    best, lag = -1e30, 0
    for k in range(0, min(30 * FPS, len(lm2) - 30 * FPS)):
        n = min(len(ev), len(lm2) - k, 120 * FPS)
        c = float(np.dot(ev[:n], lm2[k:k + n]))
        if c > best:
            best, lag = c, k
    off = lag * HOP
    off = _best_offset(mx, v, off, HOP * 2, 8)
    return _best_offset(mx, v, off, 10, 1)


def gain_curve(y, x, mask_db=-40.0, block=SR // 2, k=9):
    """per-sample gain of x inside y (y = g x + something not like x): least squares per block where x is present,
    median over k blocks, linear in between"""
    import numpy as np
    n = min(len(y), len(x)) // block
    gs, cs = [], []
    xl = levels(x[: n * block], block)
    ref = np.percentile(xl, 95) if n else 0
    for i in range(n):
        if xl[i] < max(mask_db, ref - 20):
            continue
        xb = x[i * block:(i + 1) * block].astype(np.float64)
        yb = y[i * block:(i + 1) * block].astype(np.float64)
        gs.append(float(np.dot(yb, xb) / (np.dot(xb, xb) + 1e-12)))
        cs.append((i + 0.5) * block)
    if not gs:
        return np.ones(len(y), np.float32), 1.0, 0.0
    gs = np.array(gs)
    if len(gs) >= k:
        gs = np.array([np.median(gs[max(0, i - k // 2):i + k // 2 + 1]) for i in range(len(gs))])
    g = np.interp(np.arange(len(y)), cs, gs).astype(np.float32)
    return g, float(np.median(gs)), float(np.percentile(gs, 90) / max(1e-9, np.percentile(gs, 10)))


def band_db(x, lo=300, hi=3000, hop=HOP):
    """dB per frame inside lo..hi Hz"""
    import numpy as np
    n = len(x) // hop
    fr = x[: n * hop].reshape(n, hop).astype(np.float32) * np.hanning(hop).astype(np.float32)
    S = np.abs(np.fft.rfft(fr, 1024, axis=1)) ** 2
    f = np.fft.rfftfreq(1024, 1 / SR)
    return 10 * np.log10(S[:, (f >= lo) & (f < hi)].sum(1) + 1e-12)


def _runs(mask, min_len=1, bridge=0):
    """[(start, end)] frame runs of True, small holes of 'bridge' frames closed first"""
    import numpy as np
    m = np.asarray(mask, bool).copy()
    if bridge:
        i, n = 0, len(m)
        while i < n:
            if m[i]:
                i += 1
                continue
            j = i
            while j < n and not m[j]:
                j += 1
            if 0 < i and j < n and j - i <= bridge:
                m[i:j] = True
            i = j
    out, i, n = [], 0, len(m)
    while i < n:
        if not m[i]:
            i += 1
            continue
        j = i
        while j < n and m[j]:
            j += 1
        if j - i >= min_len:
            out.append((i, j))
        i = j
    return out


# ------------------------------------------------------------------ the parts of the mix
def paths(nar_dir, pictures=""):
    """the files of one episode's music step"""
    if not pictures:
        ep = os.path.dirname(os.path.dirname(os.path.dirname(nar_dir)))
        pics = sorted(glob.glob(os.path.join(ep, "03_Artwork", "*", "shots.json")))
        pictures = os.path.dirname(pics[0]) if pics else ""
    return {"voice": os.path.join(nar_dir, "narration.wav"), "mix": os.path.join(nar_dir, "narration_music.wav"),
            "plain": os.path.join(nar_dir, "narration_music_plain.wav"), "shape": os.path.join(nar_dir, "music_shape.json"),
            "map": os.path.join(pictures, "emotion_map.json") if pictures else "",
            "shots": os.path.join(pictures, "shots.json") if pictures else ""}


def split(ffmpeg, nar_dir, mix_path=None, pictures=""):
    """decode and take the mix apart -> dict with voice / music / effects (16 kHz mono, mix time) and how well it worked"""
    import numpy as np
    P = paths(nar_dir, pictures)
    mix_path = mix_path or P["mix"]
    v = decode(ffmpeg, P["voice"])
    mx = decode(ffmpeg, mix_path)
    if len(v) < SR or len(mx) < SR:
        raise RuntimeError("narration.wav or the mix is missing or empty")
    shape = _load(P["shape"], {}) or {}
    if shape.get("lead_s") is not None:
        off = int(round(float(shape["lead_s"]) * SR))
        off = _best_offset(mx, v, off, 24, 1)
    else:
        off = _find_lead(mx, v)
    vp = _place(v, off, len(mx))
    g, g_med, g_spread = gain_curve(mx, vp)
    voice = vp * g
    rest = mx - voice
    music, fx, how = rest, None, "music and effects together"
    if os.path.exists(P["plain"]) and os.path.abspath(mix_path) == os.path.abspath(P["mix"]):
        pl = decode(ffmpeg, P["plain"])
        if abs(len(pl) - len(mx)) < SR:
            pl = pl[: len(mx)] if len(pl) >= len(mx) else np.concatenate([pl, np.zeros(len(mx) - len(pl), np.float32)])
            k, k_med, k_spread = gain_curve(mx, pl, mask_db=-60)
            f_ = mx - k * pl
            if 10 * np.log10(float((f_.astype(np.float64) ** 2).mean()) + 1e-12) < \
                    10 * np.log10(float((mx.astype(np.float64) ** 2).mean()) + 1e-12) - 6 and k_spread < 1.5:
                fx = f_
                music = k * pl - voice
                how = "music and effects apart (narration_music_plain.wav)"
    return {"voice": voice, "music": music, "fx": fx, "mix": mx, "lead": off / SR, "voice_alone": vp,
            "gain": g_med, "gain_spread": g_spread, "how": how, "shape": shape, "paths": P, "mix_path": mix_path}


# ------------------------------------------------------------------ measuring
def _planned_silences(m, lead):
    """mix-time windows around weight-3 lines (music, ambience and effects stop there on purpose)"""
    out = []
    for t in (m or {}).get("turns") or []:
        if int(t.get("weight", 0)) >= 3:
            a = lead + float(t.get("at", 0))
            e = lead + float(t.get("line_end", t.get("at", 0)))
            out.append((a - 6.0, e + 8.0, int(t.get("weight", 0)), a))
    return out


def measure(parts, genre="", m=None):
    """all the numbers the rules use (no verdicts yet)"""
    import numpy as np
    R = RULES
    lead = parts["lead"]
    Lv = levels(parts["voice"])
    Lva = levels(parts["voice_alone"])
    n = len(Lv)
    speech = Lva > np.percentile(Lva, 95) - 18
    vref = float(np.median(Lv[speech])) if speech.any() else -20.0
    first = int(np.argmax(speech)) / FPS if speech.any() else 0.0
    last = (n - int(np.argmax(speech[::-1]))) / FPS if speech.any() else n / FPS
    Lm = levels(parts["music"])[:n]
    Lms = smooth_db(Lm, 5)
    bed = parts["music"] + (parts["fx"] if parts["fx"] is not None else 0)
    Lb = smooth_db(levels(bed)[:n], 5)
    out = {"voice_db": round(vref, 1), "lead_s": round(lead, 2), "voice_gain": round(parts["gain"], 3), "gain_spread": round(parts["gain_spread"], 2),
           "split": parts["how"], "minutes": round(n / FPS / 60, 2), "first_word_s": round(first, 2),
           "last_word_s": round(last, 2)}
    # music under the words, and in the pauses
    sp_m = Lm[speech] if speech.any() else Lm
    story = float(np.median(sp_m)) if len(sp_m) else -90.0
    out["music_under_voice_db"] = round(story - vref, 1)
    Bv = band_db(parts["voice"])[:n]
    Bm = band_db(parts["music"])[:n]
    Bm_wide = band_db(parts["music"], 150, 4000)[:n]
    Bv_wide = band_db(parts["voice"], 150, 4000)[:n]
    out["music_audible_db"] = round(float(np.median(Bm_wide[speech]) - np.median(Bv_wide[speech])), 1) if speech.any() else None
    out["music_band_db"] = round(float(np.median(Bm[speech]) - np.median(Bv[speech])), 1) if speech.any() else None
    # swell: from 30 dB under the opening level to 3 dB under it
    starts = np.where(Lms > vref - 50)[0]
    t0 = int(starts[0]) if len(starts) else 0
    plateau = float(np.percentile(Lms[t0:t0 + 15 * FPS], 90)) if n > t0 else -90.0
    a_ = next((i for i in range(t0, min(n, t0 + 30 * FPS)) if Lms[i] >= plateau - 30), t0)
    b_ = next((i for i in range(a_, min(n, t0 + 30 * FPS)) if Lms[i] >= plateau - 3), a_)
    out["swell_s"] = round((b_ - a_) / FPS, 2)
    out["music_starts_s"] = round(t0 / FPS, 2)
    # quarter-second levels of the music for jumps and drop-outs
    q = FPS // 4
    L4 = smooth_db(Lm, 1)[: n // q * q].reshape(-1, q)
    L4 = 10 * np.log10((10 ** (L4 / 10)).mean(1) + 1e-12)
    plan = _planned_silences(m, lead)
    in_plan = lambda t: any(a <= t <= b for a, b, _, _ in plan)
    mend_i = np.where(Lms > vref - 45)[0]
    mend = (int(mend_i[-1]) / FPS) if len(mend_i) else n / FPS
    lo_t, hi_t = max(b_ / FPS, first), min(mend - 3.0, last + 2.0)
    aud = story - 18
    jumps = []
    for i in range(len(L4) - 1):
        t = (i + 1) / 4
        if not lo_t <= t <= hi_t or in_plan(t):
            continue
        d = L4[i + 1] - L4[i]
        if abs(d) >= R["jump_db"] and max(L4[i], L4[i + 1]) >= aud:
            if not jumps or t - jumps[-1]["t"] > 1.0:
                jumps.append({"t": round(t, 2), "db": round(float(d), 1)})
    out["jumps"] = jumps
    # drop-outs: under the running 8 s median by hole_db for 0.25-3 s, music on both sides
    k = 33
    ref = np.median(np.lib.stride_tricks.sliding_window_view(np.pad(L4, k // 2, mode="edge"), k), axis=1)
    low = L4 < ref - R["hole_db"]
    holes, gaps = [], []
    turns = [(lead + float(t.get("at", 0)) - 4, lead + float(t.get("line_end", t.get("at", 0))) + 4)
             for t in (m or {}).get("turns") or [] if int(t.get("weight", 0)) >= 1]
    for a, b in _runs(low):
        ta, tb = a / 4, b / 4
        if ta < lo_t or tb > hi_t or in_plan((ta + tb) / 2) or any(x <= ta <= y for x, y in turns):
            continue                                       # a dip made on purpose for a turn line is not a drop-out
        if tb - ta <= 3.0:
            holes.append({"t": round(ta, 2), "s": round(tb - ta, 2), "db": round(float((ref[a:b] - L4[a:b]).max()), 1)})
    # gaps: no music at all for a while inside the story
    quiet = L4 < story - 25
    for a, b in _runs(quiet):
        ta, tb = a / 4, b / 4
        if tb - ta > R["gap_max_s"] and ta >= first and tb <= last and not in_plan((ta + tb) / 2):
            gaps.append({"t": round(ta, 2), "s": round(tb - ta, 2)})
    mins = max(0.5, (last - first) / 60)
    out["dropouts"] = holes
    out["dropouts_per_min"] = round(len(holes) / mins, 2)
    out["gaps"] = gaps
    # stingers: sudden rises of the effects (or of everything under the voice when they could not be split)
    src = parts["fx"] if parts["fx"] is not None else parts["music"]
    Lf = levels(src)[:n]
    kk = 3 * FPS
    base = np.array([np.median(Lf[max(0, i - kk):i]) if i > 4 else Lf[i] for i in range(n)])
    hit = (Lf - base >= R["sting_rise_db"]) & (Lf >= vref + R["sting_level_db"])
    sting = []
    for a, b in _runs(hit):
        t = a / FPS
        if t < lo_t - 1 or t > last + 1:
            continue
        if not sting or t - sting[-1]["t"] > 2.5:
            sting.append({"t": round(t, 2), "db": round(float(Lf[a:b].max() - vref), 1)})
    import emotion_music
    st = emotion_music.sting_settings(genre or "default")
    gap = float(st.get("min_gap_s", 30))
    close = [{"t": sting[i]["t"], "after": round(sting[i]["t"] - sting[i - 1]["t"], 1)}
             for i in range(1, len(sting)) if sting[i]["t"] - sting[i - 1]["t"] < gap]
    out["stingers"] = sting
    out["stingers_close"] = close
    out["stingers_per_min"] = round(len(sting) / mins, 2)
    out["sting_rule"] = {"min_gap_s": gap, "per_min": float(st.get("per_min", 1.0))}
    # effects over the words (needs the effects apart)
    clash = []
    if parts["fx"] is not None:
        Bf = smooth_db(band_db(parts["fx"])[:n], 5)
        Bvs = smooth_db(Bv, 5)
        over = speech & (Bf - Bvs >= R["fx_speech_db"])
        for a, b in _runs(over, min_len=int(R["fx_speech_s"] * FPS), bridge=3):
            clash.append({"t": round(a / FPS, 2), "s": round((b - a) / FPS, 2),
                          "db": round(float(np.median(Bf[a:b] - Bvs[a:b])), 1)})
    out["fx_over_speech"] = clash
    # clarity: voice over everything else in the speech band, per second of speech, worst tenth
    Bb = band_db(bed)[:n]
    diff = (Bv - Bb)[speech]
    if len(diff) > FPS:
        sec = np.array([np.median(diff[i:i + FPS]) for i in range(0, len(diff) - FPS + 1, FPS)])
        out["clarity_median_db"] = round(float(np.median(sec)), 1)
        out["clarity_p10_db"] = round(float(np.percentile(sec, 10)), 1)
    # ending
    end_lvl = float(np.percentile(Lb[int(last * FPS):], 90)) if int(last * FPS) < n else -90.0
    file_end = n / FPS
    tail = mend - last
    a_ = next((i for i in range(n - 1, int(last * FPS), -1) if Lb[i] >= end_lvl - 3), int(last * FPS))
    b_ = next((i for i in range(a_, n) if Lb[i] <= end_lvl - 30), None)
    out["tail_s"] = round(tail, 2)
    out["fade_out_s"] = round(((b_ if b_ is not None else n) - a_) / FPS, 2)
    out["end_level_db"] = round(float(Lb[-5:].max() - end_lvl), 1) if n > 5 else 0.0
    out["file_s"] = round(file_end, 2)
    # heavy lines: is there really a silence before them?
    heavy = []
    for a, b, w, at in plan:
        if w < 3:
            continue
        i0, i1 = int((at - 1.2) * FPS), int((at - 0.3) * FPS)
        if 0 <= i0 < i1 <= n:
            heavy.append({"t": round(at, 2), "drop_db": round(float(story - np.median(Lb[i0:i1])), 1)})
    out["heavy"] = heavy
    return out


def _bright_of(mood):
    import emotion_music
    return emotion_music.BRIGHT.get(mood, 2)


def tracks_check(cfg, parts, m, genre):
    """what the music files themselves say (music_shape.json + the emotion map + the library index)"""
    import emotion_music
    shape = parts["shape"] or {}
    tr = shape.get("tracks") or ([{"file": shape["track"], "mood": "", "start": 0, "end": 1e9, "cut": ""}]
                                 if shape.get("track") else [])
    s = emotion_music.settings(genre)
    lead = parts["lead"]
    found = []
    scenes = (m or {}).get("scenes") or []
    files = [x.get("file") for x in tr]
    if len(set(files)) > max(1, int(s.get("max_tracks", 3) if s.get("on") else 1)) and s.get("on"):
        found.append({"what": "too_many", "says": f"{len(set(files))} different tracks (the series allows {s.get('max_tracks')})"})
    for i, x in enumerate(tr):
        f = x.get("file") or ""
        folder = emotion_music.folder_of(f, cfg) if cfg else _base(re.split(r"[\\/][^\\/]*$", str(f or ""))[0]).lower()
        mood = x.get("mood") or folder
        row = {}
        try:
            import audio_screen
            if cfg and f:
                row = audio_screen.cached(cfg, f) if not os.path.exists(f) else audio_screen.screen(cfg, audio_screen.original_of(cfg, f), "music")
        except Exception:
            row = {}
        if row and audio_screen.effective(row) == "reject":
            found.append({"what": "misfit", "file": f, "at": x.get("start", 0),
                          "says": f"{_base(f)}: {(row.get('why') or ['rejected by the library check'])[-1]}"})
        if mood and folder and folder.lower() not in [z.lower() for z in emotion_music.near_moods(mood)]:
            found.append({"what": "wrong_folder", "file": f, "at": x.get("start", 0),
                          "says": f"{_base(f)} is from {folder} but the story there is {mood}"})
        if i:
            prev = tr[i - 1]
            pf = emotion_music.folder_of(prev.get("file"), cfg) if cfg else _base(re.split(r"[\\/][^\\/]*$", str(prev.get("file") or ""))[0]).lower()
            if f in files[:i - 1]:
                found.append({"what": "back", "file": f, "at": x.get("start", 0),
                              "says": f"{_q(x.get('start', 0))}: goes back to {_base(f)}, a track it already left"})
            elif pf == folder or (x.get("mood") and x.get("mood") == prev.get("mood")):
                found.append({"what": "no_reason", "file": f, "at": x.get("start", 0),
                              "says": f"{_q(x.get('start', 0))}: changes track ({_base(prev.get('file'))} -> "
                                      f"{_base(f)}) with no change of mood ({folder})"})
        if scenes and x.get("mood"):
            a, b = float(x.get("start", 0)) - lead, float(x.get("end", 1e9)) - lead
            inside = [sc.get("mood") for sc in scenes if float(sc.get("end", 0)) > a and float(sc.get("start", 0)) < b]
            if inside and _bright_of(x["mood"]) > max(_bright_of(z) for z in inside) + 0 and x.get("cut") == "return":
                found.append({"what": "lighter", "file": f, "at": x.get("start", 0),
                              "says": f"{_q(x.get('start', 0))}: {x['mood']} is lighter than the story there "
                                      f"({', '.join(sorted(set(inside)))})"})
    return found, tr


def judge(meas, tfound, genre):
    """-> [{rule, ok, says, fix}] one row per rule"""
    R = RULES
    rows = []
    add = lambda rule, ok, says, fix="": rows.append({"rule": rule, "ok": bool(ok), "says": says, "fix": fix})
    add("swell", meas["swell_s"] >= R["swell_min_s"],
        f"music swells in over {meas['swell_s']:g} s (at least {R['swell_min_s']:g} s)", "swell")
    j = meas["jumps"]
    add("jumps", len(j) <= R["jumps_max"],
        f"{len(j)} sudden level jumps in the music" + (": " + ", ".join(f"{_q(x['t'])} ({x['db']:+.0f} dB)" for x in j[:6]) if j else "")
        + f" (at most {R['jumps_max']})", "slew")
    h = meas["dropouts"]
    add("dropouts", meas["dropouts_per_min"] <= R["dropouts_per_min_max"],
        f"{len(h)} moments where the music drops out and comes back ({meas['dropouts_per_min']:g} a minute, at most "
        f"{R['dropouts_per_min_max']:g})" + (": " + ", ".join(_q(x["t"]) for x in h[:6]) if h else ""), "repick")
    g = meas["gaps"]
    add("gaps", not g, ("no music for " + ", ".join("%.0f s at %s" % (x["s"], _q(x["t"])) for x in g[:4]) if g else
                        "music is there all through the story"), "remake")
    sr_ = meas["sting_rule"]
    close, per = meas["stingers_close"], meas["stingers_per_min"]
    ok = len(close) <= R["sting_close_max"] and per <= sr_["per_min"] * R["sting_rate_x"]
    if genre != "sukoon":                                  # Sukoon has no effects: its rises are the music itself
        add("stingers", ok, f"{len(meas['stingers'])} stingers ({per:g} a minute, series allows {sr_['per_min']:g}); "
            f"{len(close)} closer than {sr_['min_gap_s']:g} s"
            + (": " + ", ".join(f"{_q(x['t'])} ({x['after']:g} s after)" for x in close[:5]) if close else ""), "thin")
    c = meas["fx_over_speech"]
    add("fx_speech", len(c) <= R["fx_speech_max"],
        (f"{len(c)} effect(s) as loud as the voice while it speaks: " + ", ".join(f"{_q(x['t'])} ({x['s']:g} s)" for x in c[:5]))
        if c else "no effect plays over the words at voice level", "duck_fx")
    if meas.get("clarity_p10_db") is not None:
        add("clarity", meas["clarity_p10_db"] >= R["clarity_p10_db"],
            f"voice {meas['clarity_median_db']:g} dB over the rest (worst tenth {meas['clarity_p10_db']:g} dB, at least "
            f"{R['clarity_p10_db']:g})", "duck_bed")
    au = meas.get("music_band_db")
    if au is not None:
        add("audible", R["audible_db"] <= au <= R["loud_db"],
            f"music {-au:g} dB under the voice in the speech band (heard: between {-R['loud_db']:g} and {-R['audible_db']:g} dB under)",
            "repick" if au < R["audible_db"] else "lower")
    tmin = float((R["tail_min_s"] or {}).get(genre, (R["tail_min_s"] or {}).get("default", 5.0))) \
        if isinstance(R["tail_min_s"], dict) else float(R["tail_min_s"])
    add("tail", meas["tail_s"] >= tmin, f"music plays {meas['tail_s']:g} s after the last word (at least {tmin:g} s)", "remake")
    cut = meas["end_level_db"] > R["cut_db"]
    add("fade", meas["fade_out_s"] >= R["fade_min_s"] and not cut,
        f"ends with a {meas['fade_out_s']:g} s fade" + (" and is cut off at the end" if cut else ""), "fade")
    hv = [x for x in meas["heavy"] if x["drop_db"] < R["heavy_drop_db"]]
    if meas["heavy"]:
        add("heavy", not hv, (f"heavy line at {', '.join(_q(x['t']) for x in hv)} has no real silence before it" if hv
                              else f"{len(meas['heavy'])} heavy line(s) have their silence"), "")
    by = {}
    for t in tfound:
        by.setdefault(t["what"], []).append(t["says"])
    for what, rule, fix in (("misfit", "track_fit", "repick"), ("wrong_folder", "track_mood", "repick"),
                            ("no_reason", "track_switch", "remake"), ("back", "track_switch", "remake"),
                            ("too_many", "track_switch", "remake"), ("lighter", "track_mood", "")):
        if what in by:
            add(rule, False, "; ".join(by[what]), fix)
    for rule in ("track_fit", "track_switch", "track_mood"):
        if not any(r["rule"] == rule for r in rows):
            add(rule, True, {"track_fit": "every track fits its mood folder", "track_switch": "track changes follow the story",
                             "track_mood": "the music follows the story's mood"}[rule])
    return rows


def check(cfg, nar_dir, genre="", pictures="", mix_path=None):
    """measure + judge one mix -> {"rows", "meas", "tracks", "parts"}"""
    ff = (cfg or {}).get("ffmpeg") or "ffmpeg"
    parts = split(ff, nar_dir, mix_path, pictures)
    m = _load(parts["paths"]["map"]) if parts["paths"]["map"] else None
    meas = measure(parts, genre, m)
    tf, tr = tracks_check(cfg, parts, m, genre)
    return {"rows": judge(meas, tf, genre), "meas": meas, "tracks": tr, "tfound": tf, "parts": parts, "map": m}



# ------------------------------------------------------------------ fixing what can be fixed on the mix itself
def _ramp_db(n, a, b, db, ramp_s=0.15):
    """0 dB outside frames a..b, 'db' inside, linear ramps of ramp_s (20 fps curve)"""
    import numpy as np
    t = np.arange(n)
    r = max(1, int(ramp_s * FPS))
    k = np.clip(np.minimum((t - (a - r)) / r, ((b + r) - t) / r), 0, 1)
    return db * k


def plan_fixes(res, genre):
    """failed rows -> gain curves (dB, 20 fps) for the music and the effects, and what each change does"""
    import numpy as np
    import emotion_music
    R, meas, parts = RULES, res["meas"], res["parts"]
    n = len(levels(parts["voice"]))
    rm, rf = np.zeros(n), np.zeros(n)
    done = []
    fail = {r["rule"] for r in res["rows"] if not r["ok"]}
    lead = parts["lead"]
    Lm = levels(parts["music"])[:n]
    if "swell" in fail:                                     # a slow eased swell on top of what is there
        S = float(emotion_music.EASE.get("fade_in_s", 8.0))
        t0 = meas["music_starts_s"]
        t = (np.arange(n) / FPS - t0) / S
        e = emotion_music.ease(np.clip(t, 0, 1))
        g = np.where(t < 1, 20 * np.log10(np.maximum(e, 1e-4)), 0)
        rm += g
        rf += g                                             # the opening ambience swells with it
        done.append(("swell", f"music now swells in over {S:g} s (eased)"))
    if fail & {"jumps", "gaps", "dropouts"}:               # sudden changes become ramps (only ever turned down)
        story = meas["music_under_voice_db"] + meas["voice_db"]
        L = np.maximum(smooth_db(Lm, 5), story - 40)
        T = L.copy()
        step = float(R["slew_db_s"]) / FPS
        for i in range(1, n):
            T[i] = min(T[i], T[i - 1] + step)
        for i in range(n - 2, -1, -1):
            T[i] = min(T[i], T[i + 1] + step)
        near = np.zeros(n, bool)
        for x in meas["jumps"] + meas["dropouts"] + meas["gaps"]:
            a = int((x["t"] - 4) * FPS)
            b = int((x["t"] + x.get("s", 0) + 4) * FPS)
            near[max(0, a):min(n, b)] = True
        cut = np.where(near, np.minimum(0, T - L), 0)
        rm += cut
        k = int(np.sum(near))
        if k:
            done.append(("jumps", f"{len(meas['jumps'])} jump(s) / {len(meas['dropouts'])} drop-out(s) eased into "
                                  f"ramps of at most {R['slew_db_s']:g} dB a second"))
    if "stingers" in fail and parts["fx"] is not None:     # the weaker of two stingers too close together goes down
        gap = meas["sting_rule"]["min_gap_s"]
        kept, thinned = [], []
        for s in sorted(meas["stingers"], key=lambda s: -s["db"]):
            if any(abs(s["t"] - k_["t"]) < gap for k_ in kept):
                thinned.append(s)
                a, b = int((s["t"] - 0.2) * FPS), int((s["t"] + 2.5) * FPS)
                rf += _ramp_db(n, a, b, -12.0, 0.1)
            else:
                kept.append(s)
        if thinned:
            done.append(("stingers", f"{len(thinned)} stinger(s) closer than {gap:g} s to a stronger one turned down 12 dB: "
                                     + ", ".join(_q(s["t"]) for s in sorted(thinned, key=lambda s: s["t"])[:8])))
    if "fx_speech" in fail and parts["fx"] is not None:   # effects ducked under the words they cover
        for x in meas["fx_over_speech"]:
            a, b = int(x["t"] * FPS), int((x["t"] + x["s"]) * FPS)
            rf += _ramp_db(n, a, b, -(x["db"] - R["fx_speech_db"] + 6.0), 0.15)
        done.append(("fx_speech", f"{len(meas['fx_over_speech'])} effect(s) over the words ducked under the voice"))
    if "clarity" in fail or ("audible" in fail and (meas.get("music_band_db") or -99) > R["loud_db"]):
        over = max(0.0, (meas.get("music_band_db") or -99) - R["loud_db"] + 1.0)
        over = max(over, R["clarity_p10_db"] - (meas.get("clarity_p10_db") or 99) + 1.0)
        over = min(over, 8.0)
        rm -= over
        rf -= over
        done.append(("clarity", f"music and effects {over:.0f} dB lower so the voice stands out"))
    if "fade" in fail:                                      # an eased fade at the very end
        fs = max(3.0, min(6.0, 0.6 * max(1.0, meas["tail_s"])))
        t = (n / FPS - np.arange(n) / FPS) / fs
        e = emotion_music.ease(np.clip(t, 0, 1))
        g = np.where(t < 1, 20 * np.log10(np.maximum(e, 1e-4)), 0)
        rm += g
        rf += g
        done.append(("fade", f"an eased {fs:g} s fade-out at the end"))
    return rm, rf, done


def apply_parts(parts, rm, rf):
    """the 16 kHz parts with the gain curves applied (for checking again without making the file first)"""
    import numpy as np
    n = len(parts["music"])
    t = np.arange(n) / SR * FPS
    gm = (10 ** (np.interp(t, np.arange(len(rm)), rm) / 20)).astype(np.float32)
    gf = (10 ** (np.interp(t, np.arange(len(rf)), rf) / 20)).astype(np.float32)
    out = dict(parts)
    out["music"] = parts["music"] * gm
    if parts["fx"] is not None:
        out["fx"] = parts["fx"] * gf
    else:
        out["music"] = parts["music"] * np.minimum(gm, gf)
    return out


def render_fixed(cfg, parts, rm, rf, outp, run_cmd=None):
    """the corrected mix at full quality (48 kHz stereo): voice untouched, music and effects with the gain curves,
    one gain for the whole file afterwards (two-pass loudnorm, linear: the shape stays)"""
    import numpy as np
    import emotion_music
    ff = (cfg or {}).get("ffmpeg") or "ffmpeg"
    sr = 48000
    P = parts["paths"]
    F = decode(ff, parts["mix_path"], sr, 2).astype(np.float32)
    V = decode(ff, P["voice"], sr, 1)
    n = len(F)
    off = int(round(parts["lead"] * sr))
    mono = F.mean(1)
    off = _best_offset(mono, V, off, 6, 1)
    vp = _place(V, off, n)
    g, _, _ = gain_curve(mono, vp, block=sr // 2)
    voice = (vp * g)[:, None]
    t = np.arange(n) / sr * FPS
    gm = (10 ** (np.interp(t, np.arange(len(rm)), rm) / 20)).astype(np.float32)[:, None]
    gf = (10 ** (np.interp(t, np.arange(len(rf)), rf) / 20)).astype(np.float32)[:, None]
    if parts["fx"] is not None and os.path.exists(P["plain"]):
        Pl = decode(ff, P["plain"], sr, 2).astype(np.float32)
        Pl = Pl[:n] if len(Pl) >= n else np.vstack([Pl, np.zeros((n - len(Pl), 2), np.float32)])
        k, _, _ = gain_curve(mono, Pl.mean(1), mask_db=-60, block=sr // 2)
        kP = Pl * k[:, None]
        out = voice + (kP - voice) * gm + (F - kP) * gf
    else:
        out = voice + (F - voice) * np.minimum(gm, gf)
    peak = float(np.abs(out).max()) or 1.0
    raw = outp + ".f32"
    with open(raw, "wb") as fo:
        fo.write((out * min(1.0, 0.98 / peak)).astype("<f4").tobytes())
    ok = emotion_music.loudnorm2({"ffmpeg": ff}, raw, sr, 2, outp + ".part.wav", run_cmd)
    try:
        os.remove(raw)
    except OSError:
        pass
    if ok and os.path.exists(outp + ".part.wav"):
        os.replace(outp + ".part.wav", outp)
        return True
    return False


# ------------------------------------------------------------------ the report
def report_text(name, first, final, done, remade, notes):
    """the short music_report.txt"""
    fails0 = [r for r in first["rows"] if not r["ok"]]
    fails1 = {r["rule"]: r for r in final["rows"] if not r["ok"]}
    fixed = [r for r in fails0 if r["rule"] not in fails1]
    lines = [f"Music check - {name} - {time.strftime('%Y-%m-%d %H:%M')}"]
    if not fails0:
        lines.append("Result: the music passed every check; nothing was changed.")
    else:
        lines.append(f"Result: {len(fails0)} problem(s) found, {len(fixed)} fixed, {len(fails1)} left. "
                     + ("The video uses the corrected mix." if (done or remade) else "Nothing could be changed automatically."))
    if remade:
        lines += ["", "Music made again: " + remade]
    if fixed or done:
        lines += ["", "Fixed"]
        for r in fixed:
            now = next((x for x in final["rows"] if x["rule"] == r["rule"]), None)
            lines.append(f"  - {r['rule']}: {r['says']}" + (f"  ->  now: {now['says']}" if now else ""))
        for rule, what in done:
            if not any(r["rule"] == rule for r in fixed):
                lines.append(f"  - {rule}: {what}")
    if fails1:
        lines += ["", "Still there (the checker could not fix these by itself)"]
        for r in fails1.values():
            lines.append(f"  - {r['rule']}: {r['says']}")
    lines += ["", "Passed"]
    for r in final["rows"]:
        if r["ok"] and not any(f["rule"] == r["rule"] for f in fails0):
            lines.append(f"  - {r['rule']}: {r['says']}")
    m = final["meas"]
    lines += ["", f"Numbers: voice starts {m['lead_s']:g} s in, music {-(m.get('music_band_db') or 0):g} dB under the voice "
                  f"(speech band), {m['minutes']:g} min, {m['split']}."]
    lines += [f"  {x}" for x in notes]
    if done or remade:
        lines.append("  The mix before the check is kept as narration_music_before_check.wav (to compare).")
    lines += ["", "What this check cannot judge: whether the track is the right one for this story and how it feels "
                  "(taste). Your ratings on the Review page are still the judge of that.",
              "Limits: Tinker table music_check.RULES; stinger spacing per series: emotion_music.STINGERS."]
    return "\n".join(lines) + "\n"


def _episode_dir(nar_dir):
    ep = os.path.dirname(os.path.dirname(os.path.dirname(nar_dir)))
    return ep if os.path.basename(os.path.dirname(os.path.dirname(nar_dir))).lower().endswith("narration") else nar_dir


def _avoid(res):
    """music files that are themselves the problem (misfit, song, drop-outs, inaudible, missing in places)"""
    rows = {r["rule"]: r for r in res["rows"] if not r["ok"]}
    tr = res["tracks"] or []
    at = lambda t: next((x.get("file") for x in tr if float(x.get("start", 0)) <= t < float(x.get("end", 1e9))),
                        tr[0].get("file") if tr else "")
    out = set()
    for y in res.get("tfound") or []:
        if y["what"] in ("misfit", "wrong_folder") and y.get("file"):
            out.add(y["file"])
    if "dropouts" in rows:
        for x in res["meas"]["dropouts"]:
            out.add(at(x["t"]))
    if "gaps" in rows:
        for x in res["meas"]["gaps"]:
            out.add(at(x["t"] + 1))
    if "audible" in rows and (res["meas"].get("music_band_db") or 0) < RULES["audible_db"]:
        out |= {x.get("file") for x in tr}
    return {_base(f) for f in out if f}


def step(ctx, name, pictures="", genre="", remake=None):
    """job step (Experimental, every series, after the music): check the finished mix, fix what can be fixed,
    write music_report.txt. Never stops the run."""
    cfg, say = ctx["cfg"], ctx["say"]
    if not RULES.get("on", True):
        say("   (music check is off - Tinker music_check.RULES)")
        return True
    import tools
    try:
        nar = tools.narration_dir(cfg, name, existing=True)
        if not os.path.exists(os.path.join(nar, "narration_music.wav")):
            say("   (music check: no narration_music.wav yet)")
            return True
        return _step(ctx, cfg, say, name, nar, pictures, genre, remake)
    except Exception as e:                                  # never stop a run over the check
        say(f"   (music check skipped: {e})")
        return True


def _step(ctx, cfg, say, name, nar, pictures, genre, remake):
    t0 = time.time()
    first = check(cfg, nar, genre, pictures)
    fails = [r for r in first["rows"] if not r["ok"]]
    say(f"   music check: {len(first['rows']) - len(fails)}/{len(first['rows'])} rules passed"
        + (": " + ", ".join(r["rule"] for r in fails) if fails else ""))
    for r in fails:
        say(f"     ✗ {r['rule']}: {r['says']}")
    res, remade, notes = first, "", []
    mix = os.path.join(nar, "narration_music.wav")
    before = os.path.join(nar, "narration_music_before_check.wav")
    if fails and RULES.get("fix", True) and RULES.get("repick", True) and remake and remake.get("fn"):
        avoid = _avoid(first)
        chosen = bool((remake.get("args") or {}).get("track"))
        if avoid and chosen:
            notes.append("the track was chosen on the form, so it was kept (" + ", ".join(sorted(avoid)) + ")")
        elif avoid or (genre == "sukoon" and any(r["rule"] == "tail" for r in fails)):
            import shutil
            import tools
            shutil.copyfile(mix, before)
            cfg["_music_avoid"] = sorted(set(cfg.get("_music_avoid") or []) | avoid)
            say("   music check: making the music again" + (" without " + ", ".join(sorted(avoid)) if avoid else ""))
            try:
                ok = getattr(tools, remake["fn"])(ctx, **(remake.get("args") or {}))
            except Exception as e:
                ok = False
                notes.append(f"making the music again failed: {e}")
            if ok:
                second = check(cfg, nar, genre, pictures)
                if sum(not r["ok"] for r in second["rows"]) <= len(fails):
                    res = second
                    remade = ("without " + ", ".join(sorted(avoid))) if avoid else "with the usual Sukoon shape"
                else:                                       # worse than before: keep the first mix
                    shutil.copyfile(before, mix)
                    notes.append("the music made again was not better - the first mix was kept")
            else:
                shutil.copyfile(before, mix)
            cfg.pop("_music_avoid", None)
    done = []
    if any(not r["ok"] for r in res["rows"]) and RULES.get("fix", True):
        rm, rf, done = plan_fixes(res, genre)
        if done:
            trial = dict(res)
            trial["meas"] = measure(apply_parts(res["parts"], rm, rf), genre, res["map"])
            trial["rows"] = judge(trial["meas"], res.get("tfound") or [], genre)
            if sum(not r["ok"] for r in trial["rows"]) <= sum(not r["ok"] for r in res["rows"]):
                import shutil
                if not os.path.exists(before) or not remade:
                    shutil.copyfile(mix, before)
                if render_fixed(cfg, res["parts"], rm, rf, mix, ctx.get("run_cmd")):
                    res = check(cfg, nar, genre, pictures) if res["parts"]["fx"] is None else trial
                    for rule, what in done:
                        say(f"     ✓ {rule}: {what}")
                else:
                    done = []
                    notes.append("writing the corrected mix failed - the mix is as it was")
            else:
                done = []
                notes.append("the corrections made the numbers worse - the mix is as it was")
    left = [r for r in res["rows"] if not r["ok"]]
    txt = report_text(name, first, res, done, remade, notes)
    ep = _episode_dir(nar)
    for p in {os.path.join(ep, "music_report.txt"), os.path.join(nar, "music_report.txt")}:
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt)
    with open(os.path.join(nar, "music_check.json"), "w", encoding="utf-8") as f:
        json.dump({"first": {"rows": first["rows"], "meas": first["meas"]}, "final": {"rows": res["rows"], "meas": res["meas"]},
                   "fixed": done, "remade": remade, "notes": notes, "seconds": round(time.time() - t0, 1)}, f,
                  ensure_ascii=False, indent=1)
    say(f"   music check: {len(fails)} problem(s) found, {len(fails) - len(left)} fixed, {len(left)} left "
        f"({time.time() - t0:.0f} s) -> {os.path.join(ep, 'music_report.txt')}")
    return True


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="check the music of a finished episode (reads only; --fix writes a copy)")
    ap.add_argument("episode", help="the episode folder or its narration folder")
    ap.add_argument("--genre", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--fix", default="", help="write a corrected mix here (the episode is not changed)")
    a = ap.parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    ep = os.path.abspath(a.episode)
    nars = sorted(glob.glob(os.path.join(ep, "02_Narration", "narrations", "*", "narration_music.wav")))
    nar = os.path.dirname(nars[0]) if nars else ep
    genre = a.genre or (os.path.basename(os.path.dirname(os.path.dirname(ep))).lower() if nars else "")
    try:
        import path_config
        cfg = path_config.load_config()
    except Exception:
        cfg = {"ffmpeg": "ffmpeg"}
    res = check(cfg, nar, genre)
    for r in res["rows"]:
        print(("  ok   " if r["ok"] else "  FAIL ") + f"{r['rule']}: {r['says']}")
    if a.json:
        json.dump({"rows": res["rows"], "meas": res["meas"]}, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.fix:
        rm, rf, done = plan_fixes(res, genre)
        for rule, what in done:
            print(f"  fix  {rule}: {what}")
        if done and render_fixed(cfg, res["parts"], rm, rf, os.path.abspath(a.fix)):
            res2 = check(cfg, nar, genre, mix_path=os.path.abspath(a.fix))
            print("after the fix:")
            for r in res2["rows"]:
                print(("  ok   " if r["ok"] else "  FAIL ") + f"{r['rule']}: {r['says']}")


if __name__ == "__main__":
    main(sys.argv[1:])
