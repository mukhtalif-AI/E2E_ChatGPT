"""roomtone.py - the quiet 'air' of your real room under the whole narration, so pauses are not dead digital
silence (a real room never is).

ONE clean, continuous stretch of room tone is taken from a real pause in your Sukoon recording (no breath, no
words, steady), its end is crossfaded into its start so it loops without a seam, and that loop is laid end to
end under the narration at your real level. (One clean piece looped = no stitching artifacts.)
Built once -> dataset\\refs\\room_tone_loop.wav + room_tone_loop.json"""
import json, os, wave
import numpy as np
from . import cfg

PATH = os.path.join(cfg.REFS, "room_tone_loop.wav")
INFO = os.path.join(cfg.REFS, "room_tone_loop.json")


def _read(p):
    with wave.open(p) as w:
        n, sr, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(n), "<i2").astype(np.float32) / 32768.0
    return (x.reshape(-1, ch).mean(1) if ch > 1 else x), sr


def _write(p, x, sr):
    y = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(p + ".part", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(y.tobytes())
    os.replace(p + ".part", p)


def _rms(x):
    return float(np.sqrt(np.mean(x ** 2) + 1e-12))


def build(min_len=None, xfade=0.35):
    """best real pause of your Sukoon recordings -> seamless loop."""
    if min_len is None:                                         # longest clean loop we can find (fewer repeats)
        for ml in (4.0, 3.0, 2.2, 1.6):
            i = build(ml, xfade)
            if i:
                return i
        return None
    from .dataset import sources
    best = None
    for src in sources():
        if "sukoon" not in src["name"].lower():
            continue
        try:
            x, sr = _read(src["voice"])
            j = json.load(open(src["transcript"], encoding="utf-8"))
        except Exception:
            continue
        segs = j["segments"] if isinstance(j, dict) else j
        segs = sorted([s for s in segs if s.get("end") is not None], key=lambda s: s["start"])
        sp = np.concatenate([x[int(s["start"] * sr):int(s["end"] * sr)] for s in segs[:200]]) if segs else x
        speech = _rms(sp)
        for a, b in zip(segs, segs[1:]):
            g0, g1 = a["end"] + 0.35, b["start"] - 0.25           # stay clear of word tails / next breath-in
            if g1 - g0 < min_len:
                continue
            q = x[int(g0 * sr):int(g1 * sr)]
            hop = int(0.02 * sr)
            n = len(q) // hop
            e = np.sqrt((q[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
            if e.max() > 4 * np.median(e) or np.median(e) < 1e-5:  # a breath/click inside, or edited-out silence
                continue
            if 20 * np.log10(_rms(q) / speech) > -42:                # music / untranscribed words, not room air
                continue
            steady = float(np.std(20 * np.log10(e)))               # lower = steadier hiss
            score = steady + 0.3 * max(0.0, 3.0 - (g1 - g0))         # prefer long, steady stretches
            if best is None or score < best[0]:
                best = (score, q.copy(), sr, 20 * np.log10(_rms(q) / speech), src["name"], round(g0, 2))
    if best is None:
        return None
    _, q, sr, rel, name, at = best
    q = q - np.mean(q)
    c = int(xfade * sr)
    if len(q) < 3 * c:
        return None
    t = np.linspace(0, np.pi / 2, c)
    loop = q[:len(q) - c].copy()
    loop[:c] = q[:c] * np.sin(t) + q[len(q) - c:] * np.cos(t)  # equal-power: end flows into start
    os.makedirs(cfg.REFS, exist_ok=True)
    _write(PATH, loop / max(1e-6, np.abs(loop).max()) * 0.5, sr)
    info = {"rel_db": round(float(rel), 1), "sr": sr, "from": name, "at_s": at, "len_s": round(len(loop) / sr, 2)}
    json.dump(info, open(INFO, "w"), indent=1)
    return info


def lay_under(full, sr, extra_db=0.0):
    """the loop, end to end, under `full` (float32 mono) at your recordings' real level vs the speech."""
    if not os.path.exists(PATH) or not os.path.exists(INFO):
        if not build():
            return full, None
    info = json.load(open(INFO))
    t, tsr = _read(PATH)
    if tsr != sr:
        n = int(len(t) * sr / tsr)
        t = np.interp(np.linspace(0, len(t) - 1, n), np.arange(len(t)), t).astype(np.float32)
    hop = int(0.02 * sr)
    n = len(full) // hop
    e = np.sqrt((full[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
    voiced = full[:n * hop].reshape(n, hop)[e > np.percentile(e, 90) * 0.1]
    if not len(voiced):
        return full, None
    want = _rms(voiced) * 10 ** ((info["rel_db"] + extra_db) / 20)
    bed = np.tile(t, len(full) // len(t) + 1)[:len(full)] * (want / _rms(t))
    return (full + bed).astype(np.float32), info["rel_db"] + extra_db
