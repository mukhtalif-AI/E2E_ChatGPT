"""abtest.py - does it sound like YOU? The kept-out lines (never trained on) are spoken by
  A = plain Chatterbox clone of your voice prompt (what Kisse used)   B = your trained voice
  C = B + match to you (pitch / pace, if praat is installed)
and compared with your real recording of the same line.
Output <media>\\tests\\<time>\\  compare.wav (each line: YOU -> A -> B), report.txt (numbers), lines\\"""
import json, os, subprocess, time
import numpy as np
from . import cfg
from .model import load_base, resolve_checkpoint, resolve_reference, use_checkpoint, set_voice, speak_sentence, \
    words_per_second, say


def load24(path):
    import librosa
    return librosa.load(path, sr=24000)[0].astype(np.float32)


def level(a, rms=0.08):
    r = float(np.sqrt(np.mean(a ** 2))) if len(a) else 0
    return a * min(rms / r, 0.95 / max(1e-6, float(np.abs(a).max()))) if r > 1e-5 else a


def main(model=None, n=None):
    import soundfile as sf, torch
    from . import asr
    S = dict(cfg.C["speak"])
    meta = [x for x in cfg.read_meta() if x["holdout"]]
    if not meta:
        raise SystemExit("no dataset yet - run menu 1 first")
    meta = meta[:int(n)] if n else meta
    ck_path = resolve_checkpoint(model or "best")
    out = os.path.join(cfg.TESTS, time.strftime("%Y%m%d_%H%M"))
    os.makedirs(os.path.join(out, "lines"), exist_ok=True)
    m = load_base()
    ref = resolve_reference()
    wps = words_per_second()
    S["max_retakes"] = 0                                 # fair test: first take only
    versions = [("A_clone", None)] + ([("B_trained", ck_path)] if ck_path else [])
    if not ck_path:
        say("   no trained voice yet - testing only the plain clone (A)")
    res = {x["id"]: {"text": x["text"], "real": x["wav"]} for x in meta}
    base_S = dict(S)
    for tag, ckp in versions:
        ck = use_checkpoint(m, ckp) if ckp else None
        S = dict(base_S)
        if ck:                                           # trained voice: pauses allowed (brakes.py)
            from .model import patient_brakes
            patient_brakes(S)
        else:
            S["repetition_penalty"] = 2.0                # plain clone = exactly what Chatterbox / Kisse used
        set_voice(m, ref, ck, float(S["exaggeration"]))
        say(f"\n   {tag}:")
        for k, x in enumerate(meta):
            a, info = speak_sentence(m, x["text"], 11 + k, S, wps)
            p = os.path.join(out, "lines", f"{k + 1:02d}_{tag}.wav")
            sf.write(p, a, m.sr, subtype="PCM_16")
            res[x["id"]][tag] = dict(info, wav=p)
            say(f"     {k + 1}/{len(meta)}  {info['dur']} s  letters wrong {info.get('letter_error')}")
    from . import match
    if ck_path and match.available() and cfg.C["speak"].get("match", True):
        versions.append(("C_matched", "match"))
        say("\n   C_matched = B + match to you (pitch / pace):")
        for k, x in enumerate(meta):
            a, sr_ = sf.read(res[x["id"]]["B_trained"]["wav"], dtype="float32")
            y, mi = match.apply(a, sr_, x["text"], "sukoon")
            p = os.path.join(out, "lines", f"{k + 1:02d}_C_matched.wav")
            sf.write(p, y, sr_, subtype="PCM_16")
            res[x["id"]]["C_matched"] = dict(res[x["id"]]["B_trained"], wav=p, dur=round(len(y) / sr_, 2), **mi)
            say(f"     {k + 1}/{len(meta)}  {mi}")
    # speaker likeness: voice-encoder similarity of every take to your REAL line
    ve = m.ve
    emb = lambda a: np.asarray(ve.embeds_from_wavs([np.interp(np.arange(0, len(a), 1.5), np.arange(len(a)), a)
                                                    .astype(np.float32)], sample_rate=16000)[0])
    rows, parts, gap = [], [], lambda s: np.zeros(int(24000 * s), np.float32)
    sums = {t: {"like": [], "le": [], "len": [], "sil": []} for t, _ in versions}

    def silence(a):
        h = 480
        f = a[: len(a) // h * h].reshape(-1, h)
        db = 10 * np.log10((f ** 2).mean(1) + 1e-10)
        return float((db < db.max() - 35).mean()) if len(db) else 0.0
    you_sil = []
    for k, x in enumerate(meta):
        r = res[x["id"]]
        real = load24(x["wav"])
        e_real = emb(real)
        real_dur = len(real) / 24000
        you_sil.append(silence(real))
        parts += [level(real), gap(0.9)]
        line = [f"{k + 1}. {x['text']}", f"   YOU   {real_dur:.1f} s"]
        for tag, _ in versions:
            a = load24(r[tag]["wav"])
            like = float(np.dot(e_real, emb(a)) / (np.linalg.norm(e_real) * np.linalg.norm(emb(a)) + 1e-9))
            sums[tag]["like"].append(like)
            if r[tag].get("letter_error") is not None:
                sums[tag]["le"].append(r[tag]["letter_error"])
            sums[tag]["len"].append(len(a) / 24000 / real_dur)
            sums[tag]["sil"].append(silence(a))
            line.append(f"   {tag:9s} {len(a) / 24000:.1f} s   sounds-like-you {like:.3f}   "
                        f"letters wrong {r[tag].get('letter_error')}")
            parts += [level(a), gap(0.9)]
        parts.append(gap(1.3))
        rows += line
    sf.write(os.path.join(out, "compare.wav"), np.concatenate(parts), 24000, subtype="PCM_16")
    head = ["A/B TEST - each line in compare.wav: YOU -> " + " -> ".join(t for t, _ in versions), "",
            f"voice prompt: {ref}", f"trained voice: {ck_path or '-'}", "",
            "AVERAGES   (sounds-like-you: 1.0 = identical voice;  length: 1.0 = your pace;  pauses = share of silence)",
            f"  YOU       pauses {np.mean(you_sil):.0%}"]
    for tag, _ in versions:
        s = sums[tag]
        head.append(f"  {tag:9s} sounds-like-you {np.mean(s['like']):.3f}   letters wrong "
                    f"{(np.mean(s['le']) if s['le'] else float('nan')):.1%}   length vs you {np.mean(s['len']):.2f}   "
                    f"pauses {np.mean(s['sil']):.0%}")
    open(os.path.join(out, "report.txt"), "w", encoding="utf-8").write("\n".join(head + [""] + rows) + "\n")
    say("\n" + "\n".join(head))
    say(f"\nLISTEN: {os.path.join(out, 'compare.wav')}")
