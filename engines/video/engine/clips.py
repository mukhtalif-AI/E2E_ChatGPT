"""
clips.py - short shareable clips cut from a finished episode, around its best key moments (not random).

Each clip starts at the beginning of a spoken sentence a few seconds before a key moment, runs ~30 s and ends
where a sentence ends, with a soft fade in/out of picture and sound.

    python clips.py <work\\episode folder> <final video.mp4> [count]
Clips are written next to the video in a "clips" folder.
"""
import json, os, re, subprocess, sys

ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
import paths


def _ff():
    import overlay
    return overlay.ffmpeg_exe()


def _load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def episode_segments(work):
    """The spoken lines of an episode, as the main video used them: the lined-up script PLUS the parts that were
    filled from your voice (not in the script), or - without a script - the cleaned Hindi transcript.
    (Before, the voice-filled parts were missing here, so a reel from such a part had no words.)"""
    def segs_of(p):
        d = _load(p)
        return (d.get("segments", []) if isinstance(d, dict) else d) or []
    try:
        from sleep_pipeline import clean_segments
    except Exception:
        clean_segments = lambda x: x
    script = segs_of(os.path.join(work, "transcript_script.json"))
    if script:
        spans = _load(os.path.join(work, "gapfill", "spans.json")) or []
        extra = segs_of(os.path.join(work, "gapfill", "transcript_hi.json")) if spans else []
        extra = [dict(x, source="voice") for x in clean_segments(extra)
                 if any(a - 1 <= float(x["start"]) <= b + 1 for a, b in spans)]
        return sorted(script + extra, key=lambda x: float(x["start"]))
    return clean_segments(segs_of(os.path.join(work, "transcript_hi.json")))


def sentence_bounds(segs, join=0.3):
    """Whole spoken sentences: pieces of one sentence follow each other with (almost) no gap.
    -> list of (start, end) of complete sentences."""
    segs = sorted(segs, key=lambda s: float(s["start"]))
    out = []
    for s in segs:
        a, b = float(s["start"]), float(s["end"])
        same = s.get("sent") is not None and out and out[-1][2] == s.get("sent")
        if out and (same or a - out[-1][1] <= join):
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b, s.get("sent")])
    return [(a, b) for a, b, _ in out]


def windows(work, count=4, length=30.0, skip_start=15.0, max_len=48.0):
    """Best moments as (start, speech_end, moment): start = beginning of a sentence, speech_end = the end of a
    COMPLETE sentence (never cut mid-thought; may run past `length` to finish it). Callers add their own tail."""
    moments = _load(os.path.join(work, "moments.json")) or []
    segs = episode_segments(work)
    sents = sentence_bounds(segs)
    starts = [a for a, _ in sents]
    ends = [b for _, b in sents]
    out = []
    for m in sorted(moments, key=lambda m: -float(m.get("score", 0))):
        ms, me = float(m["start"]), float(m["end"])
        if ms < skip_start:
            continue
        # begin at a sentence start 3-12 s before the moment (so the clip has context)
        cand = [x for x in starts if ms - 12 <= x <= ms - 3] or [x for x in starts if ms - 20 <= x <= ms + 0.3]
        a = min(cand) if cand else max(0.0, ms - 6)
        a = max(0.0, a - 0.5)
        # end where a whole sentence ends, close to a+length; finishing the thought beats the length target
        cand = [e for e in ends if e >= me - 0.05 and e >= a + length - 8 and e <= a + max_len]
        if cand:
            e = min(cand, key=lambda e: abs(e - (a + length)) + (0 if e >= a + length - 4 else 3))
        else:
            e = min([x for x in ends if x >= me - 0.05] or [me])
        if any(not (e + 4 <= x0 or a >= x1 + 4) for x0, x1, _ in out):
            continue                                         # overlaps a better clip
        out.append((a, e, m))
        if len(out) >= count:
            break
    return sorted(out, key=lambda w: w[0])


def _encoders(ff):
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        return "h264_nvenc" in r
    except Exception:
        return False


def make(work, video, count=4, length=30.0):
    ff = _ff()
    wins = windows(work, count, length)
    if not wins:
        print("      no key moments found - no clips made", flush=True)
        return []
    d = os.path.join(os.path.dirname(os.path.abspath(video)), paths.EPISODE_CLIPS_NAME)
    os.makedirs(d, exist_ok=True)
    venc = ["-c:v", "h264_nvenc", "-preset", "p6", "-cq", "20", "-b:v", "0"] if _encoders(ff) else \
           ["-c:v", "libx264", "-preset", "medium", "-crf", "19"]
    done = []
    for k, (a, e, m) in enumerate(wins, 1):
        b = e + 2.6                                          # let the last words dissolve before the fade
        dur = b - a
        kw = re.sub(r"[^\wऀ-ॿ]+", "", m.get("key_word", "")) or "clip"
        name = f"clip{k}_{int(a // 60)}m{int(a % 60):02d}s_{kw}.mp4"
        out = os.path.join(d, name)
        part = out + ".part.mp4"
        cmd = [ff, "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", video,
               "-vf", f"fade=t=in:st=0:d=0.8,fade=t=out:st={dur - 1.4:.2f}:d=1.4",
               "-af", f"afade=t=in:st=0:d=0.8,afade=t=out:st={dur - 1.6:.2f}:d=1.6",
               *venc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", part]
        r = subprocess.run(cmd)
        if r.returncode == 0:
            os.replace(part, out)
            done.append(out)
            print(f"      clip {k}: {int(a // 60)}:{int(a % 60):02d}  ({dur:.0f}s)  [{m.get('key_word', '')}]  {m['text'][:60]}",
                  flush=True)
        else:
            print(f"      clip {k} failed", flush=True)
    print(f"      clips folder: {d}", flush=True)
    return done


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    make(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4)
