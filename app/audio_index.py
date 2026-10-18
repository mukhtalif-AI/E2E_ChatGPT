"""audio_index.py - ONE-time index of your music + sound effects, and what your real Sukoon episodes do with music.

    python -s audio_index.py <config.json> [--rebuild]      (the hub runs it as a job: Settings -> Audio index)

1 every file in BGM\\<mood>\\ and SFX\\: length, loudness, dynamics, brightness, busyness, tempo, loop-ability,
  calm score, kind (music / background / single sound)              -> tracks[]
2 every Sukoon .aup3 project (D:\\Audacity + episode folders, READ-ONLY): which tracks are voice / music / sfx,
  the music level over time (gain + drawn fades included) against your voice -> episodes[]
3 your music SHAPE = the median of the episodes: how long the music plays before you start, how loud it sits
  under the story, how it rises after you stop, fade lengths            -> shape{}
Result: <media_dir>\\audio_index.json   (re-run = only new files are measured)
"""
import glob, json, os, re, subprocess, sys, time
import numpy as np

# 18 Oct 2026: config.json keeps its paths under "paths" (hub_output_root, music_root ...); read it through path_config
# like the hub does, so media_dir / music_dir / ffmpeg exist here too (it stopped with KeyError: 'media_dir')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import path_config
CFG = path_config.load_config(sys.argv[1])
ROOT = os.path.dirname(os.path.abspath(sys.argv[1]))
STUDIO = os.path.join(ROOT, *CFG.get("studio_dir", "engines\\video").replace("/", "\\").split("\\")) \
    if not os.path.isabs(CFG.get("studio_dir", "")) else CFG["studio_dir"]
sys.path.insert(0, os.path.join(STUDIO, "engine"))
OUT = os.path.join(CFG["media_dir"], "audio_index.json")
SR = 16000
AUDIO = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")
FLAGS = 0x08000000 if os.name == "nt" else 0


def say(*a):
    print(*a, flush=True)


def decode(path, seconds=180):
    p = subprocess.run([CFG["ffmpeg"], "-v", "error", "-t", str(seconds), "-i", path, "-ac", "1", "-ar", str(SR),
                        "-f", "f32le", "-"], capture_output=True, creationflags=FLAGS)
    return np.frombuffer(p.stdout, np.float32)


def duration(path):
    p = subprocess.run([CFG["ffmpeg"], "-i", path], capture_output=True, text=True, errors="replace", creationflags=FLAGS)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def features(x):
    if len(x) < SR:
        return None
    hop, win = 512, 2048
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None] + hop * np.arange(n)[:, None]
    F = np.abs(np.fft.rfft(x[idx] * np.hanning(win).astype(np.float32), axis=1))
    fr = np.fft.rfftfreq(win, 1 / SR)
    pw = (F ** 2).sum(1) + 1e-12
    cent = float(np.median((F ** 2 * fr).sum(1) / pw))
    seg = x[: len(x) // (SR // 2) * (SR // 2)].reshape(-1, SR // 2)          # 0.5 s levels
    db = 10 * np.log10((seg ** 2).mean(1) + 1e-10)
    loud = float(10 * np.log10((x ** 2).mean() + 1e-10))
    dyn = float(np.percentile(db, 95) - np.percentile(db, 10))
    flux = np.maximum(np.diff(np.log1p(F), axis=0), 0).sum(1)
    thr = np.median(flux) + 2 * np.std(flux)
    onsets = float(((flux[1:-1] > thr) & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] >= flux[2:])).sum() / (len(x) / SR))
    o = flux - flux.mean()                                                    # tempo from the onset envelope
    ac = np.correlate(o, o, "full")[len(o) - 1:]
    fps = SR / hop
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 55)
    bpm = float(60 * fps / (lo + int(np.argmax(ac[lo:hi])))) if hi < len(ac) and ac[0] > 0 else 0.0
    beat = float(ac[lo:hi].max() / ac[0]) if hi < len(ac) and ac[0] > 0 else 0.0
    k = int(3 * fps)                                                          # loop: start vs end colour
    a_, b_ = np.log1p(F[:k]).mean(0), np.log1p(F[-k:]).mean(0)
    loop = float(a_ @ b_ / (np.linalg.norm(a_) * np.linalg.norm(b_) + 1e-9))
    calm = float(np.clip(1 - cent / 4000, 0, 1) * 0.4 + np.clip(1 - onsets / 4, 0, 1) * 0.4 + np.clip(1 - beat, 0, 1) * 0.2)
    return {"loudness_db": round(loud, 1), "dynamics_db": round(dyn, 1), "brightness_hz": round(cent),
            "busyness": round(onsets, 2), "tempo_bpm": round(bpm), "beat_strength": round(beat, 2),
            "loopable": round(loop, 3), "calm": round(calm, 2)}


def mood_profile(f, folder):
    """the same beat / brightness profile and mood verdicts as the Experimental library index (audio_screen.MOOD_FIT)"""
    try:
        import audio_screen
        m = audio_screen.rhythm(audio_screen._decode(CFG, f, 32000, 300).astype(np.float64), 32000)
        fit = {mood: audio_screen.misfit(m, mood) for mood in audio_screen.MOOD_FIT.get("moods") or {}}
        return {**m, "fits": [k for k, v in fit.items() if not v], "misfit": audio_screen.misfit(m, folder)}
    except Exception as e:
        return {"pulse": None, "profile_error": str(e)[:120]}


def index_tracks(old):
    root = CFG.get("music_dir") or ""
    sfx = os.path.join(os.path.dirname(root), "SFX")
    files = [f for f in glob.glob(os.path.join(root, "*", "*")) + glob.glob(os.path.join(sfx, "*"))
             if f.lower().endswith(AUDIO)]
    have = {t["file"]: t for t in old if os.path.exists(t["file"]) and t.get("size") == os.path.getsize(t["file"])
            and (t.get("kind") != "music" or "pulse" in t)}        # 18 Oct 2026: music rows get the mood profile once
    todo = [f for f in files if f not in have]
    say(f"== 1  your music + sounds: {len(files)} files, {len(todo)} new to measure")
    for k, f in enumerate(todo):
        d = duration(f)
        ft = features(decode(f))
        if not ft:
            say(f"   (could not read {os.path.basename(f)})"); continue
        is_sfx = os.path.normcase(os.path.dirname(f)) == os.path.normcase(sfx)
        kind = "single" if is_sfx and d < 12 else "background" if is_sfx else "music"
        t = {"file": f, "name": os.path.splitext(os.path.basename(f))[0], "size": os.path.getsize(f),
             "mood": "SFX" if is_sfx else os.path.basename(os.path.dirname(f)), "kind": kind,
             "seconds": round(d, 1), **ft}
        t.update(mood_profile(f, t["mood"]) if kind == "music" else {})
        have[f] = t
        say(f"   {k + 1}/{len(todo)}  [{t['mood']}] {t['name'][:50]}  {d / 60:.1f} min, calm {t['calm']}, "
            f"{t['tempo_bpm']} bpm, loop {t['loopable']}")
    return sorted(have.values(), key=lambda t: (t["mood"], t["name"].lower()))


# ------------------------------------------------------------------ your Sukoon projects
def projects():
    out = []
    for d in [CFG.get("audacity_dir") or r"D:\Audacity"] + glob.glob(os.path.join(CFG.get("episodes_dir") or "", "*")):
        for f in glob.glob(os.path.join(d, "*.aup3")):
            if "sukoon" in os.path.basename(f).lower():
                out.append(f)
    seen, res = set(), []
    for f in out:
        k = os.path.basename(f).lower()
        if k not in seen:
            seen.add(k); res.append(f)
    return res


def track_levels(con, wt, rate, step=0.5):
    """level (dB, every 0.5 s) of ONE track on the project timeline, with gain and drawn fades."""
    from aup3_doc import walk
    from aup3_mix import _samples, _envelope
    gain = float(wt["attrs"].get("gain", 1.0) or 1.0)
    hop = int(rate * step)
    pieces = []
    for wc in walk(wt, "waveclip"):
        ca = wc["attrs"]
        sq = next(iter(walk(wc, "sequence")), None)
        if sq is None:
            continue
        n = int(sq["attrs"].get("numsamples") or 0)
        if n <= 0:
            continue
        blocks = [(bl["attrs"].get("start"), bl["attrs"].get("blockid")) for bl in walk(sq, "waveblock")]
        buf = _samples(con, blocks, n)
        tl = int(round((ca.get("trimLeft") or 0) * rate)); trr = int(round((ca.get("trimRight") or 0) * rate))
        m = len(buf) // hop
        rms = np.sqrt((buf[: m * hop].reshape(m, hop).astype(np.float64) ** 2).mean(1) + 1e-12)
        env = _envelope(wc, m, 1 / step, 0.0)                                 # fades, sampled every 0.5 s
        if env is not None:
            rms = rms * env[:m]
        a, b = tl // hop, (n - trr) // hop if trr else m
        off = int(round(((ca.get("offset") or 0) + (ca.get("trimLeft") or 0)) / step))
        pieces.append((off, rms[a:b]))
        del buf
    if not pieces:
        return None
    end = max(o + len(r) for o, r in pieces)
    lv = np.zeros(end)
    for o, r in pieces:
        lv[max(0, o):o + len(r)] += r[max(0, -o):]
    return 20 * np.log10(lv * gain + 1e-6)


def analyse_project(path):
    from aup3_voice import open_ro
    from aup3_doc import decode as adecode, walk
    con = open_ro(path)
    d, doc = con.execute("select dict, doc from project").fetchone()
    root = adecode(bytes(d), bytes(doc))
    wts = [wt for wt in walk(root, "wavetrack") if not wt["attrs"].get("mute")]
    rate = int(next((wt["attrs"].get("rate") for wt in wts if wt["attrs"].get("rate")), 44100))
    musicish = re.compile(r" - |music|bgm|ambient|ambience|piano|rain|\.mp3|\.wav|instrumental|hz|lofi|sleep", re.I)
    tr = []
    for wt in wts:
        if wt["attrs"].get("channel") == 1:                                   # right half of an old stereo pair
            continue
        lv = track_levels(con, wt, rate)
        if lv is None:
            continue
        act = lv > (np.percentile(lv, 95) - 30)
        sw = np.abs(np.diff(act.astype(int))).sum() / max(1, len(act) / 120)   # on/off switches per minute
        tr.append({"name": wt["attrs"].get("name") or "", "lv": lv, "switch": sw,
                   "musicname": bool(musicish.search(wt["attrs"].get("name") or ""))})
    if not tr:
        return None
    voice = max(tr, key=lambda t: t["switch"] - (5 if t["musicname"] else 0))   # speech switches on/off a lot
    others = [t for t in tr if t is not voice]
    if not others:
        return None
    v = voice["lv"]
    speech = v > (np.percentile(v, 95) - 18)
    if speech.sum() < 20:
        return None
    vs, ve = np.argmax(speech), len(speech) - 1 - np.argmax(speech[::-1])
    vdb = float(np.median(v[speech]))
    L = max(len(t["lv"]) for t in others)                                       # all music / ambience tracks together
    pw = np.zeros(L)
    for t in others:
        pw[:len(t["lv"])] += 10 ** (t["lv"] / 10)
    m = 10 * np.log10(pw + 1e-12)
    mus = max(others, key=lambda t: (t["lv"] > -60).sum())
    on = np.where(m > -60)[0]
    if not len(on):
        return None
    ms, me = int(on[0]), int(on[-1])
    st = 0.5

    def med(a, b):
        seg = m[max(a, 0):max(min(b, len(m)), 0)]
        seg = seg[seg > -60]
        return float(np.median(seg)) - vdb if len(seg) else None

    intro = med(ms, vs)
    story = med(vs, ve)
    outro = med(ve + 4, me + 1)
    peak_in = float(m[ms:max(ms + 1, vs)].max()) if vs > ms else None          # fade-in length: to 90% of intro level
    fade_in = None
    if peak_in is not None:
        hit = np.where(m[ms:vs] >= peak_in - 3)[0]
        fade_in = round(float(hit[0]) * st, 1) if len(hit) else None
    drop = None                                                                 # how fast it goes down at the start
    if intro is not None and story is not None:
        tgt = vdb + story + 2
        hit = np.where(m[vs:] <= tgt)[0]
        drop = round(float(hit[0]) * st, 1) if len(hit) else None
    tail = round(float(me - ve) * st, 1)
    fade_out = None
    if me > ve:
        seg = m[ve:me + 1]
        top = seg.max()
        hit = np.where(seg[::-1] >= top - 3)[0]
        fade_out = round(float(hit[0]) * st, 1) if len(hit) else None
    others_names = sorted({t["name"] for t in others})
    return {"project": path, "name": os.path.splitext(os.path.basename(path))[0], "voice_track": voice["name"],
            "music_track": mus["name"], "other_tracks": others_names,
            "lead_s": round(float(vs - ms) * st, 1), "tail_s": tail,
            "intro_db": None if intro is None else round(intro, 1), "story_db": None if story is None else round(story, 1),
            "outro_db": None if outro is None else round(outro, 1), "fade_in_s": fade_in, "drop_s": drop,
            "fade_out_s": fade_out, "story_min": round(float(ve - vs) * st / 60, 1)}


def index_episodes(old):
    have = {e["project"]: e for e in old if os.path.exists(e["project"])}
    ps = projects()
    todo = [p for p in ps if p not in have]
    say(f"\n== 2  your Sukoon projects: {len(ps)} found, {len(todo)} new to read (read-only)")
    for k, p in enumerate(todo):
        t0 = time.time()
        try:
            e = analyse_project(p)
        except Exception as ex:
            say(f"   (could not read {os.path.basename(p)}: {ex})"); continue
        if not e:
            say(f"   {os.path.basename(p)}: no separate music track found"); continue
        have[p] = e
        say(f"   {k + 1}/{len(todo)}  {e['name']}: music '{e['music_track']}', starts {e['lead_s']} s before you, "
            f"intro {e['intro_db']} dB, under the story {e['story_db']} dB, after {e['outro_db']} dB (vs your voice), "
            f"tail {e['tail_s']} s  [{time.time() - t0:.0f} s]")
    return sorted(have.values(), key=lambda e: e["name"].lower())


def shape_of(eps, tracks):
    def med(k, default):
        v = [e[k] for e in eps if e.get(k) is not None]
        return round(float(np.median(v)), 1) if v else default
    used = {}
    for e in eps:
        nm = re.sub(r"[^a-z0-9]", "", e["music_track"].lower())
        for t in tracks:
            tn = re.sub(r"[^a-z0-9]", "", t["name"].lower())
            if nm and tn and (nm in tn or tn in nm):
                used[t["file"]] = used.get(t["file"], 0) + 1
    return {"from_episodes": len(eps), "lead_s": med("lead_s", 8.0), "tail_s": med("tail_s", 20.0),
            "intro_db": med("intro_db", -6.0), "story_db": med("story_db", -16.0), "outro_db": med("outro_db", -8.0),
            "fade_in_s": med("fade_in_s", 3.0), "drop_s": med("drop_s", 4.0), "fade_out_s": med("fade_out_s", 8.0),
            "used_tracks": used}


def main():
    old = {} if "--rebuild" in sys.argv or not os.path.exists(OUT) else json.load(open(OUT, encoding="utf-8"))
    tracks = index_tracks(old.get("tracks", []))
    eps = index_episodes(old.get("episodes", []))
    sh = shape_of(eps, tracks)
    say(f"\n== 3  your Sukoon music shape (median of {sh['from_episodes']} episodes, dB vs your voice):")
    say(f"   music alone for {sh['lead_s']} s before you speak (fade-in {sh['fade_in_s']} s) at {sh['intro_db']} dB,")
    say(f"   goes down to {sh['story_db']} dB within {sh['drop_s']} s, comes back to {sh['outro_db']} dB after you stop,")
    say(f"   plays {sh['tail_s']} s more (fade-out {sh['fade_out_s']} s). Tracks you used: "
        + (", ".join(os.path.basename(f) for f in sh["used_tracks"]) or "none matched in BGM"))
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "tracks": tracks, "episodes": eps, "shape": sh},
              open(OUT + ".part", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(OUT + ".part", OUT)
    say(f"\n   -> {OUT}")


if __name__ == "__main__":
    main()
