"""pace.py - pace control: every training line is measured (speaking speed + how much it pauses) and gets a
pace class; the model learns one small vector per class, added to its conditioning next to your voice and
emotion. When speaking we ask for the class of YOUR style (median of your Sukoon lines, or of the stories)
- or slower / faster. 5 speed levels x 3 pause levels = 15 classes (class 0 = no pace given)."""
import numpy as np

N_CLASSES = 16


def measure(wav, sr, words):
    """(words per second of actual speech, share of pauses inside the line)"""
    a = np.asarray(wav, np.float32)
    if not len(a) or float(np.abs(a).max()) <= 0:
        return 0.0, 0.0
    idx = np.where(np.abs(a) > np.abs(a).max() * 0.03)[0]
    a = a[idx[0]:idx[-1] + 1]
    h = int(sr * 0.02)
    f = a[: len(a) // h * h].reshape(-1, h)
    if not len(f):
        return 0.0, 0.0
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-10)
    voiced = db >= db.max() - 35
    return words / max(0.3, voiced.sum() * 0.02), float(1 - voiced.mean())


def edges(rates, pauses):
    return {"rate": [float(np.percentile(rates, q)) for q in (20, 40, 60, 80)],
            "pause": [float(np.percentile(pauses, q)) for q in (33, 66)]}


def klass(rate, pause, E):
    p = int(np.searchsorted(E["rate"], rate))       # 0 slow .. 4 fast
    q = int(np.searchsorted(E["pause"], pause))     # 0 few pauses .. 2 many
    return 1 + p * 3 + q


def describe(k):
    if not k:
        return "none"
    p, q = divmod(k - 1, 3)
    return f"speed {['very slow', 'slow', 'medium', 'fast', 'very fast'][p]}, {['few', 'some', 'many'][q]} pauses"


def shift(k, faster=0, more_pauses=0):
    p, q = divmod(k - 1, 3)
    p = min(4, max(0, p + faster)); q = min(2, max(0, q + more_pauses))
    return 1 + p * 3 + q


def install(t3, emb=None):
    """add the pace vector to T3's conditioning (an extra conditioning token when cond.pace is set)."""
    import torch
    from torch import nn
    if getattr(t3, "_pace_emb", None) is None:
        e = nn.Embedding(N_CLASSES, t3.cfg.hidden_size).to(t3.device)
        nn.init.normal_(e.weight, std=0.02)
        with torch.no_grad():
            e.weight[0].zero_()
        t3._pace_emb = e
        orig = t3.cond_enc.forward

        def fwd(cond):
            out = orig(cond)
            k = getattr(cond, "pace", None)
            if k is None:
                return out
            k = torch.as_tensor(k, device=out.device).long().view(-1)
            tok = t3._pace_emb(k).to(out.dtype)[:, None, :]
            if tok.size(0) != out.size(0):
                tok = tok.expand(out.size(0), -1, -1)
            return torch.cat([out, tok], dim=1)
        t3.cond_enc.forward = fwd
    if emb is not None:
        t3._pace_emb.load_state_dict({"weight": emb.float()})
    return t3._pace_emb


def pitch(wav, sr):
    """median voice pitch (Hz) of a line - vectorised autocorrelation on 40 ms frames (0 if unvoiced)."""
    a = np.asarray(wav, np.float32)
    n, h = int(sr * 0.04), int(sr * 0.01)
    if len(a) < n * 2:
        return 0.0
    idx = np.arange(0, len(a) - n, h)[:, None] + np.arange(n)[None]
    fr = a[idx] * np.hanning(n).astype(np.float32)
    e = (fr ** 2).mean(1)
    fr = fr[e > max(1e-6, np.percentile(e, 50) * 0.5)]
    if not len(fr):
        return 0.0
    F = np.fft.rfft(fr, 2 * n)
    ac = np.fft.irfft(np.abs(F) ** 2)[:, :n]
    lo, hi = int(sr / 350), int(sr / 65)
    k = lo + ac[:, lo:hi].argmax(1)
    good = ac[np.arange(len(ac)), k] > 0.4 * ac[:, 0]
    return float(np.median(sr / k[good])) if good.sum() >= 5 else 0.0


def semitones(f, ref):
    return 12 * np.log2(max(f, 1) / max(ref, 1))
