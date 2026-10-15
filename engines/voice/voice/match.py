"""match.py - "match to you": brings each generated phrase to YOUR measured pitch and pace, with Praat's
PSOLA (pitch moved, voice colour / formants kept; time stretched without chipmunk effects).
Targets are measured once from your Sukoon lines in the dataset -> dataset\\voice_targets.json.
Small corrections only (limits in config: match_max_semitones, match_max_stretch) - beyond that PSOLA
starts to sound processed. Needs praat-parselmouth (setup.bat installs it into libs\\cb, asks first)."""
import json, os
import numpy as np
from . import cfg

_T = {}


def available():
    try:
        import parselmouth  # noqa: F401
        return True
    except Exception:
        return False


def _f0(snd):
    from parselmouth.praat import call
    p = snd.to_pitch(time_step=0.01, pitch_floor=65, pitch_ceiling=300)
    f = p.selected_array["frequency"]
    f = f[f > 0]
    return float(np.median(f)) if len(f) >= 5 else 0.0


def _rate(a, sr, words):
    from .pace import measure
    return measure(a, sr, words)[0]


def targets(style="sukoon"):
    """your median pitch + speech pace per style, measured once from the dataset."""
    if style in _T:
        return _T[style]
    p = os.path.join(cfg.DATASET, "voice_targets.json")
    allt = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    if style not in allt:
        import parselmouth, soundfile as sf
        meta = [m for m in cfg.read_meta() if m.get("series", "story") == style and not m["holdout"]]
        meta = [meta[k] for k in np.linspace(0, len(meta) - 1, min(200, len(meta))).astype(int)]
        f0s, rates = [], []
        for m in meta:
            a, sr = sf.read(m["wav"], dtype="float32")
            f = _f0(parselmouth.Sound(a, sr))
            if f > 0:
                f0s.append(f)
            rates.append(_rate(a, sr, len(m["text"].split())))
        allt[style] = {"f0": float(np.median(f0s)), "rate": float(np.median(rates)), "lines": len(meta)}
        json.dump(allt, open(p, "w", encoding="utf-8"), indent=1)
        print(f"      your {style} voice: pitch {allt[style]['f0']:.0f} Hz, {allt[style]['rate']:.1f} words/s "
              f"(measured on {len(meta)} lines)", flush=True)
    _T[style] = allt[style]
    return _T[style]


def apply(a, sr, text, style="sukoon", S=None):
    """returns (audio, info). Never raises - on any problem the phrase is returned unchanged."""
    S = S or cfg.C["speak"]
    try:
        import parselmouth
        from parselmouth.praat import call
        T = targets(style)
        snd = parselmouth.Sound(a.astype(np.float64), sr)
        f = _f0(snd)
        info = {}
        ms = float(S.get("match_max_semitones", 4))
        pf = 1.0
        if f > 0 and S.get("match_pitch", True):
            st = float(np.clip(12 * np.log2(T["f0"] / f), -ms, ms))
            pf = 2 ** (st / 12)
            info["pitch_semitones"] = round(st, 2)
        df = 1.0
        if S.get("match_pace", True):
            r = _rate(a, sr, len(text.split()))
            mx = float(S.get("match_max_stretch", 0.2))
            if r > 0:
                df = float(np.clip(r / T["rate"], 1 - mx, 1 + mx))       # >1 = slower (longer)
                info["stretch"] = round(df, 3)
        if abs(pf - 1) < 0.01 and abs(df - 1) < 0.02:
            return a, info
        man = call(snd, "To Manipulation", 0.01, 65, 300)
        if abs(pf - 1) >= 0.01:
            pt = call(man, "Extract pitch tier")
            call(pt, "Multiply frequencies", snd.xmin, snd.xmax, pf)
            call([pt, man], "Replace pitch tier")
        if abs(df - 1) >= 0.02:
            dt = call("Create DurationTier", "dur", snd.xmin, snd.xmax)
            call(dt, "Add point", snd.xmin, df)
            call([dt, man], "Replace duration tier")
        out = call(man, "Get resynthesis (overlap-add)")
        y = out.values[0].astype(np.float32)
        peak = float(np.abs(y).max()) or 1.0
        if peak > 0.99:
            y = y / peak * 0.95
        return y, info
    except Exception as e:
        return a, {"match_error": repr(e)[:120]}
