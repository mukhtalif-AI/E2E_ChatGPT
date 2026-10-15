"""music.py - your background music under a narration, balanced like your Sukoon episodes.
Measured from your projects: music sits ~6-9 dB under your voice -> default 8 dB under while you speak,
rising gently (+3 dB) in pauses, faded in/out, looped with a crossfade if shorter than the narration.
Track: config speak.music (path, or a folder -> one track picked from it). Output: narration_music.wav
(48 kHz stereo, -16 LUFS). Only ffmpeg + numpy (no extra installs)."""
import glob, os, random, subprocess, wave
import numpy as np
from . import cfg

SR = 48000


def _ff():
    return cfg.ffmpeg()


def decode(path, ch=2):
    raw = subprocess.run([_ff(), "-v", "error", "-i", path, "-ac", str(ch), "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch).copy()


def pick_track(setting=None, seed=0):
    s = setting or cfg.C["speak"].get("music") or ""
    if os.path.isfile(s):
        return s
    if os.path.isdir(s):
        fs = sorted(glob.glob(os.path.join(s, "*.mp3")) + glob.glob(os.path.join(s, "*.wav")))
        if fs:
            return random.Random(seed).choice(fs)
    raise SystemExit(f"music not found: {s!r} (config speak.music)")


def _loop(m, n, xf=SR * 3):
    if len(m) >= n:
        return m[:n]
    out = m.copy()
    while len(out) < n:
        k = min(xf, len(out), len(m))
        ramp = np.linspace(0, 1, k)[:, None]
        out[-k:] = out[-k:] * (1 - ramp) + m[:k] * ramp
        out = np.concatenate([out, m[k:]])
    return out[:n]


def mix(narration, out=None, track=None, under_db=None, duck_db=None, lufs=None):
    S = cfg.C["speak"]
    under_db = float(S.get("music_under_db", 8) if under_db is None else under_db)
    duck_db = float(S.get("music_pause_lift_db", 3) if duck_db is None else duck_db)
    lufs = float(S.get("music_lufs", -16) if lufs is None else lufs)
    track = track or pick_track(seed=len(narration))
    v = decode(narration, 1)[:, 0]
    tail = int(SR * 4)
    n = len(v) + tail
    m = _loop(decode(track, 2), n)
    h = int(SR * 0.05)                                   # 50 ms frames
    fr = np.pad(v, (0, (-len(v)) % h)).reshape(-1, h)
    db = 10 * np.log10((fr.astype(np.float64) ** 2).mean(1) + 1e-12)
    speaking = db > db.max() - 40
    v_lvl = 10 * np.log10((fr[speaking] ** 2).mean() + 1e-12)
    mf = np.pad(m.mean(1), (0, (-n) % h)).reshape(-1, h)
    m_lvl = 10 * np.log10((mf ** 2).mean() + 1e-12)
    base = 10 ** ((v_lvl - under_db - m_lvl) / 20)
    # ducking curve: speech (held 0.4 s) -> base, pause -> base + duck_db; smoothed
    hold = np.convolve(speaking.astype(float), np.ones(8), "same") > 0
    target = np.where(hold, 0.0, duck_db)
    target = np.concatenate([target, np.full(int(np.ceil(tail / h)) + 1, duck_db)])
    sm = np.zeros_like(target)
    for i in range(len(target)):
        a = 0.35 if target[i] < (sm[i - 1] if i else 0) else 0.06   # fall fast before speech, rise slowly
        sm[i] = (sm[i - 1] if i else target[i]) + a * (target[i] - (sm[i - 1] if i else target[i]))
    g = np.repeat(10 ** (sm / 20), h)[:n] * base
    fi, fo = int(SR * 3), int(SR * 5)
    env = np.ones(n); env[:fi] = np.linspace(0, 1, fi); env[-fo:] = np.linspace(1, 0, fo)
    y = m * (g * env)[:, None]
    y[: len(v)] += v[:, None]
    out = out or os.path.join(os.path.dirname(narration), "narration_music.wav")
    r = subprocess.run([_ff(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11", "-ar", str(SR), "-c:a", "pcm_s16le", out + ".part.wav"],
                       input=y.astype(np.float32).tobytes())
    if r.returncode:
        raise SystemExit("ffmpeg could not write the music mix")
    os.replace(out + ".part.wav", out)
    print(f"   music: {os.path.basename(track)}  ({under_db:.0f} dB under your voice, +{duck_db:.0f} dB in pauses) -> {out}",
          flush=True)
    return out
