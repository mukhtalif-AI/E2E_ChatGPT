"""breaths.py - YOUR real inhales, taken from your Sukoon recordings, placed in some of the longer pauses of
a narration so it breathes like you (not a sound effect).

build():  scans the Sukoon .aup3 projects (read-only) for inhales inside pauses: soft noise without pitch,
          0.2-1.0 s, not touching any word (>= 0.1 s gap both sides). Each is cut tightly with 30 ms fades.
          -> <media>\\breaths\\sukoon\\NN_<episode>.wav + index.json, and _preview.wav = all of them with
          their numbers spoken as beeps-free gaps (listen once, DELETE the .wav files that are not good).
place():  while joining a narration: in pauses >= breath_min_pause, with probability by "breaths" setting
          (light / normal), never two pauses in a row, never the same breath twice in a row; ends 0.15-0.35 s
          before the next phrase; loudness = how loud that breath was next to your speech in its episode."""
import glob, json, os, random, re
import numpy as np
from . import cfg

BDIR = os.path.join(cfg.MEDIA, "breaths", "sukoon")


def say(*a):
    print(*a, flush=True)


def _projects():
    out = []
    for folder in (cfg.C.get("recordings") or {}).get("folders", []) + [cfg.C.get("episodes_dir") or ""]:
        if os.path.isdir(folder):
            out += [f for f in glob.glob(os.path.join(folder, "**", "*.aup3"), recursive=True)
                    if "sukoon" in os.path.basename(f).lower()]
    seen, res = set(), []
    for f in out:                                      # same project in two folders -> once
        k = re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(f))[0].lower())
        if k not in seen:
            seen.add(k); res.append(f)
    ex = [e.lower() for e in (cfg.C.get("recordings") or {}).get("exclude", [])]
    import fnmatch
    return [f for f in sorted(res) if not any(fnmatch.fnmatch(os.path.basename(f).lower(), e) for e in ex)]


def detect(x16, runs):
    """breath events [(start_s, end_s, level_vs_speech_dB)] in the pauses between speech runs."""
    h = 320
    n = len(x16) // h
    f = x16[: n * h].reshape(n, h)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-12)
    floor, top = np.percentile(db, 10), np.percentile(db, 97)
    F = np.abs(np.fft.rfft(f * np.hanning(h), axis=1)) ** 2
    freqs = np.fft.rfftfreq(h, 1 / 16000)
    cen = (F * freqs).sum(1) / (F.sum(1) + 1e-12)
    ac = np.fft.irfft(F, axis=1)[:, :h]
    voic = ac[:, 16000 // 300:16000 // 70].max(1) / (ac[:, 0] + 1e-12)
    ev = []
    for (a0, a1), (b0, b1) in zip(runs, runs[1:]):
        if b0 - a1 < 0.6:
            continue
        lo, hi = a1 + 0.12, b0 - 0.12                  # never near a word
        i0, i1 = int(lo / 0.02), int(hi / 0.02)
        if i1 - i0 < 10:
            continue
        on = (db[i0:i1] > floor + 3) & (db[i0:i1] < top - 15) & (voic[i0:i1] < 0.5) & (cen[i0:i1] > 700)
        best, s = (0, 0, 0), None
        for k, v in enumerate(list(on) + [False]):
            if v and s is None:
                s = k
            if not v and s is not None:
                if k - s > best[0]:
                    best = (k - s, s, k)
                s = None
        L, s0, s1 = best
        if not 0.2 <= L * 0.02 <= 1.0:
            continue
        if s0 == 0 or s1 >= len(on):                   # touches the edge of the safe zone -> likely a word tail
            continue
        st, en = (i0 + s0) * 0.02, (i0 + s1) * 0.02
        ev.append((st, en, float(db[i0 + s0:i0 + s1].mean() - top)))
    return ev


def build():
    from .aup3 import voice_track
    from .recordings import to16, speech_runs
    import soundfile as sf, soxr
    os.makedirs(BDIR, exist_ok=True)
    projects = _projects()
    if not projects:
        raise SystemExit("no Sukoon .aup3 projects found in recordings folders / episodes_dir")
    index, n = [], 0
    for f in projects:
        name = os.path.splitext(os.path.basename(f))[0]
        try:
            x, r, t = voice_track(f)
        except Exception as e:
            say(f"   {name}: cannot read ({e})"); continue
        x16 = to16(x, r)
        ev = detect(x16, speech_runs(x16))
        say(f"   {name}: {len(ev)} breaths")
        for st, en, lvl in ev:
            n += 1
            a = x[int((st - 0.03) * r):int((en + 0.03) * r)].astype(np.float32)
            y = soxr.resample(a, r, 24000) if r != 24000 else a
            fd = int(0.03 * 24000)
            if len(y) > 3 * fd:
                y[:fd] *= np.linspace(0, 1, fd); y[-fd:] *= np.linspace(1, 0, fd)
            fn = f"{n:03d}_{re.sub(r'[^0-9A-Za-z]+', '_', name)}.wav"
            sf.write(os.path.join(BDIR, fn), y, 24000, subtype="PCM_16")
            index.append({"file": fn, "episode": name, "at": round(st, 2), "dur": round(len(y) / 24000, 2),
                          "level_db": round(lvl, 1)})
    json.dump(index, open(os.path.join(BDIR, "index.json"), "w", encoding="utf-8"), indent=1)
    parts = []                                         # preview: each breath, gain up so you can hear it
    for it in index:
        y, _ = sf.read(os.path.join(BDIR, it["file"]), dtype="float32")
        pk = float(np.abs(y).max()) or 1
        parts += [y / pk * 0.5, np.zeros(int(0.9 * 24000), np.float32)]
    if parts:
        sf.write(os.path.join(BDIR, "_preview.wav"), np.concatenate(parts), 24000, subtype="PCM_16")
    open(os.path.join(BDIR, "_preview_order.txt"), "w", encoding="utf-8").write(
        "in _preview.wav each breath is played louder than real, then 0.9 s silence - in this order:\n" +
        "\n".join(f"{k + 1:3d}. {it['file']}  ({it['dur']} s)" for k, it in enumerate(index)) +
        "\n\nDelete the .wav files that are not clean inhales; the rest are used.\n")
    say(f"\nBREATH LIBRARY: {len(index)} breaths in {BDIR}\n  listen: _preview.wav (order in _preview_order.txt),"
        f" delete the bad .wav files - only the remaining ones are used")


def library():
    p = os.path.join(BDIR, "index.json")
    if not os.path.exists(p):
        return []
    import soundfile as sf
    out = []
    for it in json.load(open(p, encoding="utf-8")):
        f = os.path.join(BDIR, it["file"])
        if os.path.exists(f):                          # deleted by you = not used
            it = dict(it, audio=sf.read(f, dtype="float32")[0])
            out.append(it)
    return out


def place(pieces_plan, sr, setting="light", seed=1, S=None):
    """pieces_plan: [(phrase_audio, pause_after_s)] -> list of audio pieces with breaths in some pauses.
    Returns (pieces, count)."""
    S = S or cfg.C["speak"]
    lib = library() if setting not in ("off", "", None, False) else []
    if not lib:
        out = []
        for a, p in pieces_plan:
            out += [a, np.zeros(int(p * sr), np.float32)]
        return out, 0
    rng = random.Random(seed)
    prob = {"light": 0.3, "normal": 0.55, "more": 0.8}.get(str(setting), 0.3)
    min_pause = float(S.get("breath_min_pause", 1.0))
    sp = [float(np.sqrt(np.mean(a ** 2))) for a, _ in pieces_plan if len(a)]
    speech_rms = float(np.median(sp)) if sp else 0.05
    out, last_used, last_breath, count = [], -9, None, 0
    for k, (a, p) in enumerate(pieces_plan):
        out.append(a)
        gap = np.zeros(int(p * sr), np.float32)
        if p >= min_pause and k - last_used > 1 and k + 1 < len(pieces_plan) and \
                rng.random() < prob * (1.4 if p >= float(S.get("pause_paragraph", 2.2)) else 1.0):
            cands = [b for b in lib if b["file"] != last_breath and b["dur"] < p - 0.3] or []
            if cands:
                b = rng.choice(cands)
                y = b["audio"].copy()
                rms = float(np.sqrt(np.mean(y ** 2))) or 1e-4
                target = speech_rms * 10 ** (max(-40.0, min(-12.0, b["level_db"] + 6)) / 20)
                y = y * (target / rms)
                end = len(gap) - int(rng.uniform(0.15, 0.35) * sr)
                st = max(0, end - len(y))
                gap[st:st + len(y)] += y[: len(gap) - st]
                last_used, last_breath, count = k, b["file"], count + 1
        out.append(gap)
    return out, count
