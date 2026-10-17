"""audio_screen.py - does a music track or a sound effect get in the way of the voice?

Measured once per file (cache: <experimental_root>\\Activity\\audio_screen.json):
  voice band   share of the energy at 300 Hz - 3 kHz, where speech is understood. Music that fills it masks the words.
  bright       share at 3 kHz and above (bird chirps, whistles, bells: piercing over speech), and how spiky it is.
  range        loudness range of the music (quiet-to-loud swings that jump out under the voice).
  crest        peak against average level (sharp hits that jump out once the effect is levelled to the voice).
Verdicts: ok / risky (used, but a treated copy: voice-band dip, softer highs, peaks compressed; music also sits a few
dB lower under the voice) / reject (not picked). You can override any file on the Sounds page (use as is, treat,
never use). Limits are in LIMITS and can be changed for Experimental runs on the Tinker page.

speech_clarity(): after the mix, how far the voice stands above the music + effects in the voice band, line by line
(for the AI self-review).
"""
import hashlib
import json
import os
import subprocess
import threading
import time

LIMITS = {
    "music": {"voice_band_risky": 0.55, "voice_band_reject": 0.72, "bright_risky": 0.30, "range_risky_db": 14.0,
              "duck_db": 4.0, "voice_band_min": 0.03},
    "sfx": {"bright_risky": 0.45, "bright_peak_risky": 0.70, "bright_reject": 0.80, "crest_risky_db": 24.0},
    "clarity": {"good_db": 15.0, "ok_db": 10.0, "bad_db": 6.0},
}
TREAT = {
    # ffmpeg filters for the treated copy
    "music": "equalizer=f=1000:t=o:w=1.6:g=-6,equalizer=f=2600:t=o:w=1.2:g=-4",
    "music_bright": "highshelf=f=4500:g=-6",
    "music_range": "acompressor=threshold=0.1:ratio=3:attack=20:release=400:makeup=1.5",
    "sfx": "lowpass=f=5200,equalizer=f=3500:t=o:w=1.4:g=-6,acompressor=threshold=0.05:ratio=6:attack=1:release=120:makeup=2,alimiter=limit=0.7",
}
_LOCK = threading.Lock()
_MEM = {}


def _root(cfg):
    import experimental_workspace
    return experimental_workspace.root(cfg)


def cache_path(cfg):
    return os.path.join(_root(cfg), (cfg.get("layout") or {}).get("experimental_activity", "Activity"), "audio_screen.json")


def norm(p):
    return os.path.normcase(os.path.abspath(str(p))) if p else ""


def _load(cfg):
    p = cache_path(cfg)
    hit = _MEM.get(p)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {"files": {}, "overrides": {}, "treated": {}}
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    for k in ("files", "overrides", "treated"):
        d.setdefault(k, {})
    _MEM[p] = (mt, d)
    return d


def _save(cfg, d):
    p = cache_path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".part", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=0)
    os.replace(p + ".part", p)
    _MEM.pop(p, None)


def _decode(cfg, path, sr, seconds):
    import numpy as np
    p = subprocess.run([cfg["ffmpeg"], "-v", "error", "-i", path, "-t", str(seconds), "-ac", "1", "-ar", str(sr),
                        "-f", "f32le", "-"], capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
    return np.frombuffer(p.stdout, np.float32)


def measure(cfg, path, kind):
    """numbers that describe how a file sits under a voice"""
    import numpy as np
    sr = 32000
    a = _decode(cfg, path, sr, 300 if kind == "music" else 60).astype(np.float64)
    if len(a) < sr // 5:
        return {"error": "too short or unreadable"}
    n = 2048
    frames = a[: len(a) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(1)) + 1e-12
    db = 20 * np.log10(rms)
    act = db > max(np.percentile(db, 95) - 30, -70)
    if act.sum() < 2:
        return {"error": "silent"}
    spec = np.abs(np.fft.rfft(frames[act] * np.hanning(n), axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = spec[:, f >= 40].sum(1) + 1e-12
    voice = spec[:, (f >= 300) & (f < 3000)].sum(1) / tot
    bright = spec[:, f >= 3000].sum(1) / tot
    w = tot / tot.sum()                                    # louder frames count more
    st = db[act]
    k = max(1, int(0.4 * sr / n))                          # ~0.4 s short-term level for the loudness range
    short = np.array([st[i:i + k].mean() for i in range(0, len(st) - k + 1, k)]) if len(st) >= k else st
    peak = 20 * np.log10(np.abs(a).max() + 1e-12)
    return {"voice_band": round(float((voice * w).sum()), 3), "bright": round(float((bright * w).sum()), 3),
            "bright_p95": round(float(np.percentile(bright, 95)), 3),
            "range_db": round(float(np.percentile(short, 95) - np.percentile(short, 10)), 1) if len(short) > 3 else 0.0,
            "crest_db": round(float(peak - 10 * np.log10((rms[act] ** 2).mean())), 1),
            "seconds": round(len(a) / sr, 1)}


def verdict(m, kind):
    """(verdict, [reasons], treatment filters)"""
    if m.get("error"):
        return "ok", [], ""
    L = LIMITS.get(kind, {})
    why, filt, v = [], [], "ok"
    if kind == "music":
        if m.get("voice_band", 1) < L.get("voice_band_min", 0):
            v = "reject"                                  # 16 Oct: a Bhay episode got a pure sub-bass rumble as its "music"
            why.append(f"only {m['voice_band'] * 100:.1f}% of its energy is above 300 Hz: deep rumble that cannot be "
                       "heard under the voice (sounds like no music)")
        elif m["voice_band"] >= L.get("voice_band_reject", 9):
            v = "reject"
            why.append(f"{m['voice_band'] * 100:.0f}% of its energy sits in the speech band (300 Hz-3 kHz): it masks the words")
        elif m["voice_band"] >= L.get("voice_band_risky", 9):
            v = "risky"
            why.append(f"{m['voice_band'] * 100:.0f}% in the speech band: competes with the voice")
            filt.append(TREAT["music"])
        if m["bright"] >= L.get("bright_risky", 9):
            v = "risky" if v == "ok" else v
            why.append(f"bright/piercing highs ({m['bright'] * 100:.0f}% above 3 kHz)")
            filt.append(TREAT["music_bright"])
        if m["range_db"] >= L.get("range_risky_db", 99):
            v = "risky" if v == "ok" else v
            why.append(f"uneven loudness ({m['range_db']:.0f} dB from quiet to loud parts)")
            filt.append(TREAT["music_range"])
    else:
        if m["bright"] >= L.get("bright_reject", 9):
            v = "reject"
            why.append(f"almost all highs ({m['bright'] * 100:.0f}% above 3 kHz): piercing over speech")
        elif m["bright"] >= L.get("bright_risky", 9) or m["bright_p95"] >= L.get("bright_peak_risky", 9):
            v = "risky"
            why.append(f"piercing highs ({m['bright'] * 100:.0f}% above 3 kHz, spikes to {m['bright_p95'] * 100:.0f}%)")
        if m["crest_db"] >= L.get("crest_risky_db", 99):
            v = "risky" if v == "ok" else v
            why.append(f"sharp peaks ({m['crest_db']:.0f} dB above its average)")
        if v == "risky":
            filt.append(TREAT["sfx"])
    return v, why, ",".join(filt)


def _key(path):
    try:
        st = os.stat(path)
        return f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        return ""


def screen(cfg, path, kind, force=False):
    """the cached result for one file, measured now if needed: {verdict, why, filters, m, override}"""
    if not path or not os.path.isfile(path):
        return {"verdict": "ok", "why": [], "filters": "", "m": {}}
    d = _load(cfg)
    k = norm(path)
    row = d["files"].get(k)
    if force or not row or row.get("key") != _key(path) or row.get("limits") != _limits_tag(kind):
        try:
            m = measure(cfg, path, kind)
        except Exception as e:
            m = {"error": str(e)[:200]}
        v, why, filt = verdict(m, kind)
        row = {"key": _key(path), "kind": kind, "m": m, "verdict": v, "why": why, "filters": filt,
               "limits": _limits_tag(kind), "time": time.strftime("%Y-%m-%d %H:%M")}
        with _LOCK:
            d = _load(cfg)
            d["files"][k] = row
            _save(cfg, d)
    out = dict(row)
    out["override"] = d["overrides"].get(k, "")
    return _with_vocals(cfg, path, out) if kind == "music" else out


# sung music (17 Oct 2026): Kisse-Memories-Old-Atlas-Cycle got "Torn - Patrick Jordan Patrikios" (BGM\Love), a song
# whose singer talks over the narrator. The band numbers above cannot tell singing from instruments, so a vocal
# separator was run on 45 s of every track once (outside the hub) and the sung ones are listed in
# <experimental_root>\Library\music_vocals.json: {"limit_db": -10, "files": {"<file name>": {"vocals_db": -3.0}}}
# (vocals_db = the voice part's level against the instruments; near 0 = clearly sung). A listed track is a reject
# for music; "use as is" on the Sounds page still wins.
def _vocals(cfg):
    L = cfg.get("layout") or {}
    p = os.path.join(_root(cfg), L.get("experimental_library", "Library"), "music_vocals.json")
    try:
        mt = os.path.getmtime(p)
        hit = _MEM.get(p)
        if not hit or hit[0] != mt:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            _MEM[p] = hit = (mt, d)
        return hit[1]
    except (OSError, ValueError):
        return {}


def sung(cfg, path):
    """vocals_db when the music file is listed as sung (over the limit), else None"""
    d = _vocals(cfg)
    import unicodedata
    row = (d.get("files") or {}).get(unicodedata.normalize("NFC", os.path.basename(original_of(cfg, path) or path or "")))
    if not isinstance(row, dict):
        return None
    v = row.get("vocals_db")
    return v if v is not None and float(v) > float(d.get("limit_db", -10)) else None


def _with_vocals(cfg, path, row):
    v = sung(cfg, path) if row.get("kind", "music") == "music" else None
    if v is not None:
        row["verdict"] = "reject"
        row["why"] = list(row.get("why") or []) + [f"it is a song: the singing is only {abs(float(v)):.0f} dB under the "
                                                   "instruments and talks over the narrator"]
    return row


def _limits_tag(kind):
    return hashlib.sha1(json.dumps([LIMITS.get(kind), TREAT], sort_keys=True).encode()).hexdigest()[:8]


def cached(cfg, path):
    d = _load(cfg)
    k = norm(path)
    row = dict(d["files"].get(k) or {})
    if row and row.get("key") != _key(path):
        row["stale"] = True
    row["override"] = d["overrides"].get(k, "")
    return _with_vocals(cfg, path, row) if row.get("kind") == "music" else row


def set_override(cfg, path, value):
    if value not in ("", "ok", "treat", "reject"):
        raise ValueError("override must be ok, treat or reject")
    with _LOCK:
        d = _load(cfg)
        if value:
            d["overrides"][norm(path)] = value
        else:
            d["overrides"].pop(norm(path), None)
        _save(cfg, d)
    return {"ok": True}


def effective(row):
    """what the pipeline does with the file: ok / treat / reject"""
    o = row.get("override")
    if o in ("ok", "reject"):
        return o
    if o == "treat":
        return "treat"
    return {"risky": "treat", "reject": "reject"}.get(row.get("verdict"), "ok")


def treated(cfg, path, kind, row=None):
    """a treated copy (FLAC) of a risky file; the original is never changed"""
    row = row or screen(cfg, path, kind)
    filt = row.get("filters") or TREAT["sfx" if kind == "sfx" else "music"]
    L = cfg.get("layout") or {}
    h = hashlib.sha1((norm(path) + filt).encode("utf-8")).hexdigest()[:8]
    # effects keep their category folder name (the scene planner reads ambience / cue from it)
    d = os.path.join(_root(cfg), L.get("experimental_library", "Library"), "_treated_audio", kind, h,
                     os.path.basename(os.path.dirname(path)))
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, os.path.splitext(os.path.basename(path))[0] + ".flac")
    if not os.path.isfile(out) or os.path.getmtime(out) < os.path.getmtime(path):
        r = subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", path, "-af", filt, "-ar", "48000", "-c:a", "flac",
                            out + ".part.flac"], capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
        if r.returncode or not os.path.isfile(out + ".part.flac"):
            return path
        os.replace(out + ".part.flac", out)
        with _LOCK:
            dd = _load(cfg)
            dd["treated"][norm(out)] = path
            _save(cfg, dd)
    return out


def original_of(cfg, path):
    return _load(cfg)["treated"].get(norm(path), path) if path else path


# ------------------------------------------------------------------ after the mix: does the voice stand out?
def speech_clarity(cfg, narration_dir, lead, timing):
    """per spoken line: voice level over everything else in the 300 Hz-3 kHz band (dB), from narration.wav (the
    voice alone) and narration_music.wav (the final mix). -> {"median_db", "lines": [...], "worst": [...]}"""
    import numpy as np
    sr = 16000
    v = _decode(cfg, os.path.join(narration_dir, "narration.wav"), sr, 100000).astype(np.float64)
    mx = _decode(cfg, os.path.join(narration_dir, "narration_music.wav"), sr, 100000).astype(np.float64)
    if len(v) < sr or len(mx) < sr:
        return {"error": "narration.wav or narration_music.wav missing"}
    off = int(round(float(lead or 0) * sr))

    def band(x, lo=300, hi=3000):
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / sr)
        X[(f < lo) | (f > hi)] = 0
        return np.fft.irfft(X, len(x))

    lines = []
    for t in timing:
        a, b = int(float(t["start"]) * sr), int(float(t["end"]) * sr)
        if b - a < sr // 2 or off + b > len(mx):
            continue
        vv, mm = v[a:b], mx[off + a:off + b]
        # the voice's level inside the mix (the mix was levelled again afterwards): least-squares gain
        best = (0, 1e18, 0.0)
        for sh in range(-160, 161, 16):                    # +-10 ms, in case the lead is not sample exact
            m2 = mx[off + a + sh:off + b + sh] if off + a + sh >= 0 else None
            if m2 is None or len(m2) != len(vv):
                continue
            g = float(np.dot(m2, vv) / (np.dot(vv, vv) + 1e-12))
            r = float(((m2 - g * vv) ** 2).sum())
            if r < best[1]:
                best = (sh, r, g)
        sh, _, g = best
        mm = mx[off + a + sh:off + b + sh]
        rest = mm - g * vv
        ev, er = float((band(g * vv) ** 2).mean()), float((band(rest) ** 2).mean())
        snr = 10 * np.log10((ev + 1e-12) / (er + 1e-12))
        # piercing highs of music / effects against the voice's own highs (3-8 kHz)
        hv, hr = float((band(g * vv, 3000, 8000) ** 2).mean()), float((band(rest, 3000, 8000) ** 2).mean())
        hi = 10 * np.log10((hr + 1e-12) / (hv + 1e-12))
        lines.append({"n": t.get("n"), "start": round(float(t["start"]), 2), "text": str(t.get("text", ""))[:80],
                      "snr_db": round(float(snr), 1), "highs_over_voice_db": round(float(hi), 1)})
    if not lines:
        return {"error": "no spoken lines to measure"}
    s = np.array([x["snr_db"] for x in lines])
    worst = sorted(lines, key=lambda x: x["snr_db"])[:5]
    piercing = sorted([x for x in lines if x["highs_over_voice_db"] > 0], key=lambda x: -x["highs_over_voice_db"])[:5]
    return {"median_db": round(float(np.median(s)), 1), "p10_db": round(float(np.percentile(s, 10)), 1),
            "below_bad": int((s < LIMITS["clarity"]["bad_db"]).sum()), "lines": lines, "worst": worst,
            "piercing": piercing}


def clarity_score(c):
    L = LIMITS["clarity"]
    med, low = c["median_db"], c["p10_db"]
    sc = 5 if med >= L["good_db"] else 4 if med >= (L["good_db"] + L["ok_db"]) / 2 else 3 if med >= L["ok_db"] else 2 if med >= L["bad_db"] else 1
    if low < L["bad_db"] and sc > 2:
        sc -= 1
    return sc


# ------------------------------------------------------------------ the whole library (a light job)
def screen_all(ctx, kinds=("music", "sfx")):
    import sound_library
    cfg, say = ctx["cfg"], ctx["say"]
    lst = sound_library.listing(cfg)
    counts = {}
    for kind in kinds:
        files = [x["file"] for x in lst[kind]]
        say(f"   checking {len(files)} {'music track' if kind == 'music' else 'effect'}(s)")
        for i, f in enumerate(files):
            if ctx["stopped"]():
                return False
            r = screen(cfg, f, kind)
            counts[(kind, r["verdict"])] = counts.get((kind, r["verdict"]), 0) + 1
            if r["verdict"] != "ok":
                say(f"   {r['verdict']:6} {os.path.basename(f)}: {'; '.join(r['why'])}")
            if i and i % 50 == 0:
                say(f"   … {i}/{len(files)}")
    say("   " + ", ".join(f"{k[0]} {k[1]}: {v}" for k, v in sorted(counts.items())))
    say("   risky files get a treated copy when a run uses them; rejected ones are not picked (change either on the Sounds page)")
    return True
