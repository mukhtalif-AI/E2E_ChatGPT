"""aup3_mix.py - make the full stereo mix (voice + music + ambience) straight from an Audacity .aup3 project,
READ-ONLY, for when no exported mp3/wav is available.

    python -s aup3_mix.py "D:\\...\\episode.aup3" "D:\\...\\episode_mix.wav"

Honours: track mute/solo, gain, pan, clip positions/trims, stereo tracks, volume envelopes (fades drawn
with the envelope tool) and effects you APPLIED in Audacity (they are already in the audio).
Not included: live "real-time" effects added to a track (Audacity 3.2+ effects panel) - export from
Audacity instead if you use those.
"""
import os, subprocess, sys
import numpy as np
from aup3_doc import decode, walk
from aup3_voice import open_ro, FFMPEG


def _samples(con, blocks, n):
    buf = np.zeros(n, np.float32)
    for k, (start, bid) in enumerate(blocks):
        ln = (blocks[k + 1][0] if k + 1 < len(blocks) else n) - start
        if bid is None or bid <= 0:
            continue
        row = con.execute("select sampleformat, samples from sampleblocks where blockid=?", (bid,)).fetchone()
        if not row:
            continue
        fmt, blob = row
        if (fmt & 0xFFFF) == 0x000F:
            s = np.frombuffer(blob, "<f4")
        elif (fmt >> 16) == 4:
            s = np.frombuffer(blob, "<i4").astype(np.float32) / 8388608.0
        else:
            s = np.frombuffer(blob, "<i2").astype(np.float32) / 32768.0
        m = min(ln, len(s), n - start)
        if m > 0:
            buf[start:start + m] = s[:m]
    return buf


def _envelope(wc, n, rate, offset):
    pts = []
    for ev in walk(wc, "envelope"):
        for cp in walk(ev, "controlpoint"):
            a = cp["attrs"]
            if a.get("t") is not None and a.get("val") is not None:
                pts.append((float(a["t"]), float(a["val"])))
    if not pts:
        return None
    pts.sort()
    t = np.arange(n, dtype=np.float64) / rate
    return np.interp(t, [p[0] for p in pts], [p[1] for p in pts]).astype(np.float32)


def mix(src):
    con = open_ro(src)
    d, doc = con.execute("select dict, doc from project").fetchone()
    root = decode(bytes(d), bytes(doc))
    wts = list(walk(root, "wavetrack"))
    rate = int(next((wt["attrs"].get("rate") for wt in wts if wt["attrs"].get("rate")), 44100))
    solo = any(wt["attrs"].get("solo") for wt in wts)
    chans = []                                    # (L, R) float arrays on the project timeline
    i = 0
    while i < len(wts):
        wt = wts[i]
        a = wt["attrs"]
        group = [wt]
        if a.get("linked") and i + 1 < len(wts):  # old-style stereo: two linked tracks = left + right
            group.append(wts[i + 1]); i += 1
        i += 1
        if a.get("mute") or (solo and not a.get("solo")):
            continue
        gain, pan = float(a.get("gain", 1.0) or 1.0), float(a.get("pan", 0.0) or 0.0)
        per_ch = [[], []]
        for gi, t in enumerate(group):
            for wc in walk(t, "waveclip"):
                ca = wc["attrs"]
                seqs = list(walk(wc, "sequence"))
                for si, sq in enumerate(seqs[:2]):
                    ch = gi if len(group) == 2 else (si if len(seqs) >= 2 else -1)   # -1 = mono
                    n = int(sq["attrs"].get("numsamples") or 0)
                    if n <= 0:
                        continue
                    blocks = [(bl["attrs"].get("start"), bl["attrs"].get("blockid")) for bl in walk(sq, "waveblock")]
                    buf = _samples(con, blocks, n)
                    env = _envelope(wc, n, rate, ca.get("offset", 0.0))
                    if env is not None:
                        buf *= env
                    tl = int(round((ca.get("trimLeft") or 0) * rate))
                    trr = int(round((ca.get("trimRight") or 0) * rate))
                    buf = buf[tl:n - trr if trr else n]
                    off = int(round(((ca.get("offset") or 0) + (ca.get("trimLeft") or 0)) * rate))
                    if ch == -1:
                        per_ch[0].append((off, buf)); per_ch[1].append((off, buf))
                    else:
                        per_ch[ch].append((off, buf))
        end = max([o + len(b) for c in per_ch for o, b in c] or [0])
        if end <= 0:
            continue
        L, R = np.zeros(end, np.float32), np.zeros(end, np.float32)
        for arr, pieces in ((L, per_ch[0]), (R, per_ch[1])):
            for off, buf in pieces:
                a0 = max(0, off); b0 = a0 - off
                arr[a0:a0 + len(buf) - b0] += buf[b0:]
        gl, gr = gain * (1 - max(0.0, pan)), gain * (1 + min(0.0, pan))
        chans.append((L * gl, R * gr))
        print(f"   + '{a.get('name')}'" + (" (stereo)" if len(group) == 2 or any(
            len(list(walk(wc, 'sequence'))) >= 2 for wc in walk(wt, 'waveclip')) else ""), flush=True)
    if not chans:
        sys.exit("no audible tracks in the project")
    end = max(len(L) for L, _ in chans)
    out = np.zeros((end, 2), np.float32)
    for L, R in chans:
        out[:len(L), 0] += L; out[:len(R), 1] += R
    peak = float(np.abs(out).max() or 1)
    if peak > 0.98:
        out *= 0.98 / peak                       # never clip
    return out, rate


def main():
    src, dst = sys.argv[1], sys.argv[2]
    print(f"   mixing the tracks of {os.path.basename(src)} (read-only) ...", flush=True)
    out, rate = mix(src)
    ff = FFMPEG if os.path.exists(FFMPEG) else "ffmpeg"
    tmp = dst + ".part.wav"
    p = subprocess.Popen([ff, "-y", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", "-",
                          "-c:a", "pcm_s16le", tmp], stdin=subprocess.PIPE)
    p.stdin.write(out.astype("<f4").tobytes()); p.stdin.close()
    if p.wait() == 0:
        os.replace(tmp, dst)
        print(f"   mix ready: {os.path.basename(dst)} ({len(out) / rate / 60:.1f} min)", flush=True)
    else:
        sys.exit("could not write the mix")


if __name__ == "__main__":
    main()
