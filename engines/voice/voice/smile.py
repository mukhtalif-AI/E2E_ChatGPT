"""smile.py - find the stretches where you sound like you are faintly smiling.
A smile spreads the lips -> the mouth's resonances (formants F2, F3) move UP, the tone gets a bit brighter, the
pitch a little livelier. Every Sukoon clip of 6-12 s is measured with Praat and compared with YOUR average
(z-scores): score mainly = how much F2/F3 RISE in the last 1.5 s (your smile comes at phrase endings) + overall F2/F3. Over 6-12 s the vowels average out, so the
score points at the tone, not the words. It is a pointer - your ears decide (menu 7 tests the winners).
Output: <media>\\my_prompts\\smile_01..08.wav  +  smile_scores.txt"""
import os, shutil
import numpy as np
from . import cfg


def say(*a):
    print(*a, flush=True)


def measure_end(snd, voiced, fm):
    """F2/F3 in the last 1.5 s of speech minus the rest = the smile at the END of a phrase"""
    if len(voiced) < 60:
        return np.nan, np.nan
    t_end = voiced[-1]
    tail = [t for t in voiced if t >= t_end - 1.5]
    head = [t for t in voiced if t < t_end - 1.5]
    def med(ts, n, lo, hi):
        v = [fm.get_value_at_time(n, t) for t in ts]
        v = [x for x in v if x == x and lo < x < hi]
        return float(np.median(v)) if len(v) >= 10 else np.nan
    return (med(tail, 2, 700, 2800) - med(head, 2, 700, 2800), med(tail, 3, 1800, 3800) - med(head, 3, 1800, 3800))


def measure(path):
    import parselmouth
    snd = parselmouth.Sound(path)
    pitch = snd.to_pitch(time_step=0.01, pitch_floor=65, pitch_ceiling=300)
    f0 = pitch.selected_array["frequency"]
    ts = pitch.xs()
    voiced = [t for t, f in zip(ts, f0) if f > 0]
    if len(voiced) < 50:
        return None
    fm = snd.to_formant_burg(time_step=0.01, max_number_of_formants=5, maximum_formant=5000)
    f2 = [fm.get_value_at_time(2, t) for t in voiced]
    f3 = [fm.get_value_at_time(3, t) for t in voiced]
    f2 = [v for v in f2 if v == v and 700 < v < 2800]
    f3 = [v for v in f3 if v == v and 1800 < v < 3800]
    a = snd.values[0]
    sp = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2
    fr = np.fft.rfftfreq(len(a), 1 / snd.sampling_frequency)
    band = (fr > 80) & (fr < 8000)
    cen = float((sp[band] * fr[band]).sum() / (sp[band].sum() + 1e-12))
    f0v = f0[f0 > 0]
    e2, e3 = measure_end(snd, voiced, fm)
    return {"end2": e2, "end3": e3, "end_t": voiced[-1], "dur": snd.duration,
            "f2": float(np.median(f2)) if f2 else np.nan, "f3": float(np.median(f3)) if f3 else np.nan,
            "bright": cen, "f0": float(np.median(f0v)), "f0range": float(np.percentile(f0v, 90) - np.percentile(f0v, 10))}


def main(top=8):
    try:
        import parselmouth  # noqa: F401
    except Exception:
        raise SystemExit("needs Praat (praat-parselmouth) - run setup.bat and answer Y")
    meta = [m for m in cfg.read_meta() if m.get("series") == "sukoon" and 6 <= m["dur"] <= 12]
    if not meta:
        raise SystemExit("no Sukoon clips of 6-12 s in the dataset (menu 1 first)")
    say(f"   measuring {len(meta)} Sukoon clips (formants, brightness, pitch) ...")
    rows = []
    for k, m in enumerate(meta):
        try:
            r = measure(m["wav"])
        except Exception:
            r = None
        if r and all(r[k] == r[k] for k in ("f2", "f3", "end2", "end3")):
            rows.append((m, r))
        if (k + 1) % 50 == 0:
            say(f"     {k + 1}/{len(meta)}")
    if len(rows) < top:
        raise SystemExit("too few measurable clips")
    keys = ["f2", "f3", "bright", "f0", "end2", "end3"]
    M = {k: np.array([r[k] for _, r in rows]) for k in keys}
    z = {k: (M[k] - np.median(M[k])) / (np.std(M[k]) + 1e-9) for k in keys}
    # your smile shows at the END of a phrase: the rise of F2/F3 at the end counts most
    score = 1.5 * z["end2"] + 1.5 * z["end3"] + 0.5 * z["f2"] + 0.5 * z["f3"] + 0.25 * z["bright"]
    order = np.argsort(-score)
    out = os.path.join(cfg.MEDIA, "my_prompts")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):                         # old smile picks are replaced (your own clips stay)
        if f.startswith("smile_") and f.endswith(".wav"):
            os.replace(os.path.join(out, f), os.path.join(cfg.TMP, "old_" + f))
    lines = ["smile score = how much your resonances RISE at the end of the phrase (F2, F3 in the last 1.5 s), plus overall F2/F3 + brightness, vs YOUR average; files = the last 6.5 s of each clip",
             f"your averages: F2 {np.median(M['f2']):.0f} Hz, F3 {np.median(M['f3']):.0f} Hz, pitch {np.median(M['f0']):.0f} Hz",
             ""]
    for rank, i in enumerate(order[:top], 1):
        m, r = rows[i]
        dst = os.path.join(out, f"smile_{rank:02d}.wav")
        import soundfile as sf                    # keep the LAST ~6.5 s: the model reads only the first 6 s of
        a, sr = sf.read(m["wav"], dtype="float32")   # a prompt, so the smiling ending must be inside them
        end = min(len(a), int((r["end_t"] + 0.35) * sr))
        a = a[max(0, end - int(6.5 * sr)):end]
        f = int(0.02 * sr)
        a[:f] *= np.linspace(0, 1, f); a[-f:] *= np.linspace(1, 0, f)
        sf.write(dst, a, sr, subtype="PCM_16")
        lines.append(f"smile_{rank:02d}  score {score[i]:+.2f}   F2 {r['f2']:.0f}  F3 {r['f3']:.0f}  pitch {r['f0']:.0f} Hz"
                     f"   {m['episode']}: {m['text']}")
    lines += ["", "lowest (most neutral) for comparison:"]
    for i in order[-3:]:
        m, r = rows[i]
        lines.append(f"  score {score[i]:+.2f}   {m['episode']}: {m['text']}   ({m['wav']})")
    open(os.path.join(out, "smile_scores.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    say("\n".join(lines[:top + 3]))
    say(f"\nsaved to {out} - listen to the smile_XX.wav files, then menu 7 to hear the trained voice with them")


def rank_files(files):
    """rank generated test files by the same smile measure (end rise of F2/F3 + overall F2/F3)."""
    rows = []
    for f in files:
        try:
            r = measure(f)
        except Exception:
            r = None
        if r and all(r[k] == r[k] for k in ("f2", "f3", "end2", "end3")):
            rows.append((f, r))
    if len(rows) < 2:
        return "too few files to rank"
    keys = ["f2", "f3", "end2", "end3", "bright", "f0"]
    M = {k: np.array([r[k] for _, r in rows]) for k in keys}
    z = {k: (M[k] - np.median(M[k])) / (np.std(M[k]) + 1e-9) for k in keys}
    score = 1.5 * z["end2"] + 1.5 * z["end3"] + 0.5 * z["f2"] + 0.5 * z["f3"] + 0.25 * z["bright"]
    out = ["SMILE RANKING of the trained voice with each prompt (higher = brighter / smiling endings):"]
    for i in np.argsort(-score):
        f, r = rows[i]
        out.append(f"  {score[i]:+.2f}  {os.path.basename(f)}   F2 {r['f2']:.0f}  F3 {r['f3']:.0f}  pitch {r['f0']:.0f} Hz"
                   f"  pitch range {r['f0range']:.0f}")
    return "\n".join(out) + "\n"
