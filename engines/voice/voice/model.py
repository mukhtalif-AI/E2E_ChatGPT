"""model.py - loading Chatterbox Multilingual (shared model in hf_models) + your trained add-on."""
import json, os, re, time
import numpy as np
from . import cfg


def say(*a):
    print(*a, flush=True)


def load_base():
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if dev != "cuda":
        say("   !! no graphics card found by PyTorch - this will be VERY slow")
    t0 = time.time()
    say(f"   loading Chatterbox Multilingual on {dev} (first time downloads ~3 GB into {cfg.C['hf_models']}) ...")
    m = ChatterboxMultilingualTTS.from_pretrained(device=dev)
    say(f"   loaded in {time.time() - t0:.0f} s")
    return m


def resolve_checkpoint(choice):
    """'best' -> newest run's best.pt | 'base' -> None | a path."""
    choice = (choice or "best").strip()
    if choice.lower() == "base":
        return None
    if choice.lower() == "best":
        return cfg.best_checkpoint()
    if choice.lower().startswith("latest:"):                       # newest finished run of that profile
        tag = choice.split(":", 1)[1]
        for _, d in cfg.runs():
            n = os.path.basename(d)
            if tag in n and "_smoke" not in n and not n.startswith("s3_") and os.path.exists(os.path.join(d, "best.pt")):
                return os.path.join(d, "best.pt")
        raise SystemExit(f"no finished training run with '{tag}' in its name")
    if os.path.exists(choice):
        return choice
    d = os.path.join(cfg.TRAINING, choice, "best.pt")              # a run name
    if os.path.exists(d):
        return d
    raise SystemExit(f"checkpoint not found: {choice}")


def resolve_s3gen(choice):
    """'base'/None -> no change | 'best' -> newest s3_* run's best.pt | run name | path"""
    if not choice or str(choice).lower() == "base":
        return None
    if str(choice).lower() == "best":
        rs = [d for _, d in cfg.runs() if os.path.basename(d).startswith("s3_") and "_smoke" not in d
              and os.path.exists(os.path.join(d, "best.pt"))]
        return os.path.join(rs[0], "best.pt") if rs else None
    if os.path.exists(choice):
        return choice
    p = os.path.join(cfg.TRAINING, choice, "best.pt")
    if os.path.exists(p):
        return p
    raise SystemExit(f"sound generator checkpoint not found: {choice}")


def use_s3gen(m, path):
    import torch
    ck = torch.load(path, map_location="cpu", weights_only=False)
    sd = {k: v.float() for k, v in ck["flow"].items()}
    missing = m.s3gen.flow.load_state_dict(sd, strict=False)
    if missing.unexpected_keys:
        raise SystemExit("sound generator checkpoint does not fit")
    say(f"   your trained sound generator: {os.path.relpath(path, cfg.TRAINING)} (epoch {ck.get('epoch')})")
    return ck


def resolve_reference(choice=None):
    choice = choice or cfg.C["speak"].get("reference") or "auto"
    if choice == "auto":
        p = os.path.join(cfg.REFS, "reference.wav")
        if not os.path.exists(p):
            raise SystemExit("no voice prompt yet - run 'build dataset' first (menu 1)")
        return p
    if os.path.exists(choice):
        return choice
    exact = os.path.join(cfg.REFS, choice if choice.lower().endswith(".wav") else choice + ".wav")
    if os.path.exists(exact):
        return exact
    import re as _re
    key = _re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(choice))[0].lower())
    p = os.path.join(cfg.REFS, key + ".wav")
    if os.path.exists(p):
        return p
    have = sorted(f[:-4] for f in os.listdir(cfg.REFS) if f.endswith(".wav")) if os.path.isdir(cfg.REFS) else []
    raise SystemExit(f"style / reference not found: {choice}  (available: {', '.join(have)})")


def use_checkpoint(m, path):
    """merge a trained add-on into the loaded model; returns the checkpoint info."""
    import torch
    from . import lora
    ck = torch.load(path, map_location="cpu", weights_only=False)
    lora.load_into(m.t3, ck)
    if ck.get("pace_emb") is not None:
        from . import pace
        pace.install(m.t3, ck["pace_emb"])
    m.t3.eval()
    say(f"   your trained voice: {os.path.relpath(path, cfg.TRAINING)}  (epoch {ck.get('epoch')}, "
        f"trained on {ck.get('minutes', '?')} min)")
    return ck


def patient_brakes(S):
    """let pauses happen (see brakes.py) - used for the trained voice"""
    from . import brakes
    brakes.install(S.get("max_same_token", 25), S.get("tail_seconds", 0.8))


def pace_class(ck, style="sukoon", setting="auto"):
    """the pace class to ask for: your style's own (auto), or 'slower' / 'faster' / 'more pauses' etc."""
    from . import pace
    if not ck or ck.get("style_pace") is None:
        return None
    sp = ck["style_pace"]
    k = sp.get(style) or sp.get("sukoon") or next(iter(sp.values()))
    s = str(setting or "auto").lower()
    k = pace.shift(k, faster=(s.count("faster") - s.count("slower")),
                   more_pauses=(s.count("more pauses") - s.count("fewer pauses")))
    return k


def set_voice(m, reference, ck=None, exaggeration=0.5, style="sukoon", pace_setting="auto"):
    """voice prompt from the reference clip; with a trained add-on, the speaker identity = the average of ALL
    your training lines (steadier than one clip) and the pace = your style's own (pace.py)."""
    import torch
    m.prepare_conditionals(reference, exaggeration=exaggeration)
    if ck is not None and ck.get("spk_emb") is not None:
        m.conds.t3.speaker_emb = ck["spk_emb"].to(m.device, torch.float32).view(1, -1)
    k = pace_class(ck, style, pace_setting)
    m._pace = k
    if k is not None:
        from . import pace
        m.conds.t3.pace = torch.tensor([k], device=m.device)
        say(f"   pace: {pace.describe(k)}")


SAY_AS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "say_as.json")
_SAY = {}


def say_as():
    """words the voice should read with a different spelling (captions keep the real one): say_as.json"""
    try:
        mt = os.path.getmtime(SAY_AS_FILE)
    except OSError:
        return {}
    if _SAY.get("mt") != mt:
        try:
            d = json.load(open(SAY_AS_FILE, encoding="utf-8-sig"))
            _SAY["map"] = {k: v for k, v in (d.get("words") or {}).items() if k and v and not k.startswith("_")}
        except Exception:
            _SAY["map"] = {}
        _SAY["mt"] = mt
    return _SAY["map"]


def for_model(s):
    for k, v in say_as().items():                        # whole words only
        s = re.sub(rf"(?<![\u0900-\u097F]){re.escape(k)}(?![\u0900-\u097F])", v, s)
    s = s.replace("।", ".").replace("॥", ".").replace("‘", "").replace("’", "").replace('"', "")
    s = s.replace("“", "").replace("”", "")
    s = re.sub(r"^\.\.\.\s*", "", s)
    return " ".join(s.split()).strip()


def trim(a, sr, rel=0.012, keep=0.06, keep_end=0.25, fade_in=0.012):
    peak = float(np.abs(a).max()) if len(a) else 0
    if peak <= 0:
        return a
    idx = np.where(np.abs(a) > peak * rel)[0]
    k = int(keep * sr)
    a = a[max(0, idx[0] - k):idx[-1] + int(keep_end * sr)].copy()     # soft endings / trailing breath kept
    f, g = min(len(a) // 4, int(fade_in * sr)), min(len(a) // 4, int(0.08 * sr))
    if f > 1:
        a[:f] *= np.linspace(0, 1, f)
    if g > 1:
        a[-g:] *= np.linspace(1, 0, g) ** 2                            # gentle fade, no click
    return a


def stretch_pauses(a, sr, min_pause=0.6, found=0.22):
    """inside one breath-group: a quiet gap of at least `found` s that is shorter than `min_pause` is made
    longer with its OWN quiet sound (room tone + breath tail), crossfaded - the speech itself is untouched."""
    hop = int(0.01 * sr)
    if len(a) < hop * 10:
        return a
    n = len(a) // hop
    e = np.sqrt(np.mean(a[:n * hop].reshape(n, hop) ** 2, 1) + 1e-12)
    thr = max(1e-5, np.percentile(e, 95) * 0.03)          # ~ -30 dB below the loud parts
    q = e < thr
    runs, i = [], 0
    while i < n:
        if q[i]:
            j = i
            while j < n and q[j]:
                j += 1
            if i > 5 and j < n - 5 and (j - i) * 0.01 >= found:      # inner gaps only
                runs.append((i * hop, j * hop))
            i = j
        else:
            i += 1
    if not runs:
        return a
    out, last = [], 0
    for s0, s1 in runs:
        gap = (s1 - s0) / sr
        out.append(a[last:s1])
        need = int((min_pause - gap) * sr)
        if need > 0:
            fill = a[s0:s1]
            k = int(np.ceil(need / max(1, len(fill)))) + 1
            fill = np.tile(fill, k)[:need].copy()
            f = min(len(fill) // 4, int(0.03 * sr))
            if f > 1:
                fill[:f] *= np.linspace(0, 1, f); fill[-f:] *= np.linspace(1, 0, f)
            out.append(fill)
        last = s1
    out.append(a[last:])
    return np.concatenate(out).astype(np.float32)


def words_per_second():
    meta = cfg.read_meta()
    w = sum(len(m["text"].split()) for m in meta)
    d = sum(max(0.5, m["dur"] - 0.37) for m in meta)
    return w / d if d > 30 else 2.3


def repeat_score(a, sr):
    """how strongly a 60-250 ms piece of sound is repeated right after itself (a 'stutter' glitch).
    Real speech of yours: almost always < 0.85; a glitch: ~0.9+."""
    a = np.asarray(a, np.float32)
    h, w, nb = int(0.01 * sr), int(0.025 * sr), 40
    n = 1 + (len(a) - w) // h if len(a) > w else 0
    if n < 40:
        return 0.0
    idx = np.arange(w)[None] + h * np.arange(n)[:, None]
    F = np.abs(np.fft.rfft(a[idx] * np.hanning(w).astype(np.float32), axis=1)) ** 2
    fr = np.fft.rfftfreq(w, 1 / sr)
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    hz = 700 * (10 ** (np.linspace(mel(60), mel(min(7600, sr / 2 - 1)), nb + 2) / 2595) - 1)
    B = np.zeros((nb, len(fr)), np.float32)
    for i in range(nb):
        l, c, r = hz[i], hz[i + 1], hz[i + 2]
        B[i] = np.clip(np.minimum((fr - l) / (c - l), (r - fr) / (r - c)), 0, None)
    M = np.log(F @ B.T + 1e-8)
    e = M.max(1)
    sp = e > np.percentile(e, 90) - 6
    Z = M - M.mean(1, keepdims=True)
    Z /= np.linalg.norm(Z, axis=1, keepdims=True) + 1e-8
    best = 0.0
    for L in range(6, 26):
        s_ = (Z[:-L] * Z[L:]).sum(1)
        hh = max(2, L // 2)
        s2 = (Z[:-hh] * Z[hh:]).sum(1)[:len(s_)]
        g = sp[:len(s_)] & sp[L:]
        k = np.convolve(np.where(g, s_ - s2, -1.0), np.ones(L) / L, "valid")
        if len(k):
            best = max(best, float(k.max()))
    return best


def _likeness(m, a):
    """how much a take sounds like him: cosine of its voice fingerprint vs the trained one (None if unavailable)."""
    try:
        import librosa, torch
        ref = getattr(m.conds.t3, "speaker_emb", None)
        if ref is None:
            return None
        w = librosa.resample(np.asarray(a, np.float32), orig_sr=m.sr, target_sr=16000)
        e = np.asarray(m.ve.embeds_from_wavs([w], sample_rate=16000), np.float32).reshape(-1)
        r = ref.detach().float().cpu().numpy().reshape(-1)
        if e.shape != r.shape:
            return None
        return float(e @ r / (np.linalg.norm(e) * np.linalg.norm(r) + 1e-9))
    except Exception:
        return None


def speak_sentence(m, text, seed, S, wps, label=""):
    """one sentence, best of several takes: every take is scored on wrong letters (Hindi checker), length,
    pitch closeness to his voice and voice likeness; the best one is kept. Returns (audio, info)."""
    import torch
    from . import asr
    from .pace import pitch as f0_of, semitones
    want = max(0.8, len(text.split()) / wps) + 0.7 * text.count("...") + 0.2 * text.count(",")
    best, best_err, info = None, 1e9, {}
    missed_all = None
    min_takes = max(1, int(S.get("takes", 1)))
    tries = max(min_takes, 1 + int(S.get("max_retakes", 3)))
    f_target = float(S.get("target_pitch_hz", 0) or 0)
    ok = False
    for attempt in range(tries):
        torch.manual_seed(seed * 1009 + attempt * 7919)
        if getattr(m, "_pace", None) is not None and getattr(m.conds.t3, "pace", None) is None:
            m.conds.t3.pace = torch.tensor([m._pace], device=m.device)    # generate() rebuilt the conditioning
        with torch.inference_mode():
            wav = m.generate(for_model(text), language_id="hi", exaggeration=float(S["exaggeration"]),
                             cfg_weight=float(S["cfg"]), temperature=float(S["temperature"]),
                             repetition_penalty=float(S.get("repetition_penalty", 2.0)))
        lk_ = float(S.get("lead_keep", 0.06))          # >0.06: the in-breath before a phrase is kept whole and
        a = trim(wav.squeeze(0).float().cpu().numpy().astype(np.float32), m.sr,   # faded in (not clipped = gasp)
                 keep=lk_, fade_in=max(0.012, 0.6 * lk_) if lk_ > 0.06 else 0.012)
        if S.get("flow") and float(S.get("flow_min_pause", 0) or 0) > 0 and ("..." in text or "," in text):
            a = stretch_pauses(a, m.sr, float(S["flow_min_pause"]))
        d = len(a) / m.sr
        le = asr.letter_error(a, m.sr, text)
        _mw = asr.missed_words(text)                    # words missed in EVERY take = words this voice stumbles on
        if _mw is not None:
            missed_all = set(_mw) if attempt == 0 or missed_all is None else missed_all & set(_mw)
        err = (le if le is not None else 0.0) + 0.3 * abs(np.log(max(d, 0.1) / want))
        st, lk = None, None
        if min_takes > 1:
            if f_target > 0:
                f = f0_of(a, m.sr)
                if f > 0:
                    st = abs(semitones(f, f_target))
                    err += 0.02 * st
            lk = _likeness(m, a)
            if lk is not None:
                err += 1.0 * (1.0 - lk)
        rp = repeat_score(a, m.sr)                      # stutter / repeated-chunk glitch
        err += 2.0 * max(0.0, rp - 0.7)
        short = len(text.split()) <= 3                  # "सो जाओ...", "गुड नाईट": the checker can't judge 1 s of voice
        if short and le is not None:
            err -= le * 0.8
        take_ok = (short or le is None or le <= float(S.get("max_letter_error", 0.12))) and 0.55 < d / want < 1.9 \
            and rp < float(S.get("max_repeat", 0.85))
        if err < best_err:
            best, best_err, ok = a, err, take_ok
            info = {"letter_error": None if le is None else round(le, 3), "dur": round(d, 2),
                    "want": round(want, 2), "pick": attempt + 1,
                    "pitch_off_st": None if st is None else round(st, 2),
                    "likeness": None if lk is None else round(lk, 3), "repeat": round(rp, 3)}
        if ok and attempt + 1 >= min_takes:
            break
        # smart takes: a first take that is clearly excellent is kept at once (no need to make 2 more and compare)
        if attempt == 0 and take_ok and S.get("smart_takes", True) and (le is not None and le <= 0.05) \
                and rp < 0.5 and 0.6 < d / want < 1.5 and (lk is None or lk >= 0.85):
            info["smart"] = True
            break
        # good after two: both takes fine and the better one clearly clean -> the third take rarely wins
        # (measured on "A Letter to Myself": 33 of 68 lines needed 3 takes although take 1 or 2 was fine)
        if attempt == 1 and ok and S.get("smart_takes", True) and info.get("letter_error") is not None \
                and info["letter_error"] <= 0.06 and info.get("repeat", 1) < 0.6:
            info["smart"] = True
            break
        if short and ok and attempt == 0:
            break
        if not take_ok and attempt + 1 < tries and attempt + 1 >= min_takes:
            say(f"      retake {label}({d:.1f} s for {len(text.split())} words"
                + (f", {le:.0%} letters heard wrong" if le is not None else "") + ")")
    info["takes"] = attempt + 1
    info["ok"] = ok
    if missed_all and not ok:
        info["missed"] = sorted(missed_all)
    return best, info
