"""aup3_voice.py - pull the narration (voice) track out of an Audacity .aup3 project, READ-ONLY.
The project is opened with SQLite's immutable read-only mode, so it is never modified.

  python -s aup3_voice.py "D:\\Audacity\\sukoon 4.1.aup3" work\\sukoon_4_1\\voice.wav

Voice track = the mono track that is not a music file (name like "Audio 1", "voice", "vocal", "narration"),
preferring the one with the most speech-like on/off pattern. Writes 16 kHz mono WAV, aligned to time 0
of the project (same timeline as the exported mp3).
"""
import os, re, sqlite3, struct, subprocess, sys
import numpy as np
from aup3_doc import decode, tracks

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402
FFMPEG = paths.ffmpeg() or "ffmpeg"


def open_ro(path):
    uri = "file:" + os.path.abspath(path).replace("\\", "/")
    con = sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True)
    try:
        con.execute("select count(*) from project").fetchone()
        return con
    except sqlite3.DatabaseError:
        con.close()                               # e.g. saved with a journal: read it the normal read-only way
        return sqlite3.connect(uri + "?mode=ro", uri=True)


def track_audio(con, tr):
    """Float32 samples of one track on the project timeline (index 0 = time 0)."""
    rate = tr["rate"]
    pieces, end = [], 0
    for c in tr["clips"]:
        n = c["numsamples"] or 0
        buf = np.zeros(n, np.float32)
        bl = c["blocks"]
        for k, (start, bid) in enumerate(bl):
            ln = (bl[k + 1][0] if k + 1 < len(bl) else n) - start
            if bid is None or bid <= 0:
                continue                                   # silent block
            row = con.execute("select sampleformat, samples from sampleblocks where blockid=?", (bid,)).fetchone()
            if not row:
                continue
            fmt, blob = row
            if (fmt & 0xFFFF) == 0x000F:
                s = np.frombuffer(blob, "<f4")
            elif (fmt >> 16) == 4:                 # int24 stored in 4 bytes
                s = np.frombuffer(blob, "<i4").astype(np.float32) / 8388608.0
            else:                                          # int16
                s = np.frombuffer(blob, "<i2").astype(np.float32) / 32768.0
            m = min(ln, len(s), n - start)
            buf[start:start + m] = s[:m]
        tl = int(round((c.get("trimLeft") or 0) * rate))
        tr_ = int(round((c.get("trimRight") or 0) * rate))
        buf = buf[tl:n - tr_ if tr_ else n]
        off = int(round(((c.get("offset") or 0) + (c.get("trimLeft") or 0)) * rate))
        pieces.append((off, buf))
        end = max(end, off + len(buf))
    out = np.zeros(max(0, end), np.float32)
    for off, buf in pieces:
        a = max(0, off); b0 = a - off
        out[a:a + len(buf) - b0] += buf[b0:]
    return out * float(tr.get("gain") or 1.0)


def speechiness(x, rate):
    """Fraction of 50 ms frames in quiet pauses vs loud - voice has many gaps, music is continuous."""
    n = int(rate * 0.05)
    f = x[: len(x) // n * n].reshape(-1, n)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-10)
    if len(db) < 20:
        return 0.0
    lo, hi = np.percentile(db, 10), np.percentile(db, 90)
    return float(((db < lo + 0.3 * (hi - lo)).mean()) * min(1.0, (hi - lo) / 30))


def pick_voice(con, trs):
    musicish = re.compile(r" - |music|bgm|ambient|ambience|piano|rain|\.mp3|\.wav|instrumental", re.I)
    voiceish = re.compile(r"audio|voice|vocal|narr|record|mic|vo\b|आवाज़?", re.I)
    cands = []
    for i, t in enumerate(trs):
        if t["mute"] or not t["clips"]:
            continue
        mono = not t.get("linked") and not (i > 0 and trs[i - 1].get("linked"))
        score = (2 if mono else 0) + (2 if voiceish.search(t["name"] or "") else 0) - \
                (3 if musicish.search(t["name"] or "") else 0)
        cands.append((score, i))
    cands.sort(reverse=True)
    best, best_s = None, -1e9
    for score, i in cands[:3]:                             # break ties on how speech-like the audio is
        x = track_audio(con, trs[i])
        sp = speechiness(x[: int(trs[i]["rate"] * 300)], trs[i]["rate"])
        total = score + 4 * sp
        print(f"track '{trs[i]['name']}': looks like {'voice' if total > 2 else 'music'}", flush=True)
        if total > best_s:
            best, best_s = (i, x), total
    return best


def main():
    src, out = sys.argv[1], sys.argv[2]
    con = open_ro(src)
    d, doc = con.execute("select dict, doc from project").fetchone()
    trs = tracks(decode(bytes(d), bytes(doc)))
    print(f"{len(trs)} audio tracks in the project", flush=True)
    pick = pick_voice(con, trs)
    if not pick:
        sys.exit("no voice track found")
    i, x = pick
    rate = int(trs[i]["rate"])
    print(f"using '{trs[i]['name']}' as your voice ({len(x) / rate / 60:.1f} min)", flush=True)
    ff = FFMPEG if os.path.exists(FFMPEG) else "ffmpeg"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    p = subprocess.Popen([ff, "-y", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "-",
                          "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out], stdin=subprocess.PIPE)
    p.stdin.write(np.clip(x, -1, 1).astype("<f4").tobytes()); p.stdin.close(); p.wait()
    pass


if __name__ == "__main__":
    main()
