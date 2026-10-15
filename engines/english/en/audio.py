"""audio.py - engine-independent clean-up of spoken audio: sentence chunks, silence trim (relative to the
clip, soft sounds kept) with fades, joining with natural pauses, loudness per line with the emotion kept."""
import re
import numpy as np

QUIET = re.compile(r"whisper|hush|soft|quiet|breath", re.I)
LOUD = re.compile(r"shout|angry|anger|yell|furious|scream", re.I)


def chunks(text, max_words=45):
    """Whole sentences, ~45 words per take: long enough to flow, short enough to stay stable."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+(?=[\"'“A-Z0-9])", re.sub(r"\s+", " ", text).strip()) if s.strip()]
    out, cur = [], ""
    for s in sents:
        if cur and len((cur + " " + s).split()) > max_words:
            out.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    return out + ([cur] if cur else [])


def trim(a, sr):
    if not len(a):
        return a
    n = int(0.01 * sr)
    env = np.sqrt(np.convolve(a.astype(np.float64) ** 2, np.ones(n) / n, mode="same"))
    idx = np.where(env > max(env.max() * 0.03, 1e-4))[0]
    if not len(idx):
        return a
    k = int(0.12 * sr)
    a = a[max(0, idx[0] - k):idx[-1] + k].copy()
    f = min(len(a) // 4, int(0.02 * sr))
    if f:
        a[:f] *= np.linspace(0, 1, f); a[-f:] *= np.linspace(1, 0, f)
    return a


def join(pieces, sr, gap=0.45):
    out = pieces[0]
    for p in pieces[1:]:
        out = np.concatenate([out, np.zeros(int(gap * sr), np.float32), p])
    return out


def level(a, instruct=""):
    """Same loudness for every line, then the emotion's offset (whisper -6 dB, shout +2 dB); peaks safe."""
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) or 1e-6
    target = 0.1 * (0.5 if QUIET.search(instruct or "") else 1.26 if LOUD.search(instruct or "") else 1)
    a = a * (target / rms)
    pk = float(np.max(np.abs(a)))
    return a * (0.95 / pk) if pk > 0.95 else a
