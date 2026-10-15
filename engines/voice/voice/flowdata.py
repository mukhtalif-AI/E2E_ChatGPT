"""flowdata.py - 'flow clips': 2-4 of your Sukoon lines in a row, cut WITH the real pauses, breaths and
continuing tone between them.

Why: the normal dataset has one phrase per clip, so the model learned that every phrase ENDS (falling,
'instruction' tone, then stop). Flow clips teach it how you carry on from one phrase into the next.

    run.py flowdata          -> <media>\\dataset\\flow_clips\\*.wav + dataset\\metadata_flow.jsonl
Kept-out (holdout) lines are never used. Only reads the Studio transcripts + voice.wav (16 kHz is enough: the
speech model learns from 16 kHz speech tokens).
"""
import json, os, random, re, wave
import numpy as np
from . import cfg
from .dataset import sources, good_lines, norm_name, read_wav16

MAX_SPAN = 15.5       # seconds per flow clip
MAX_GAP = 3.0         # a longer silence ends the flow
SHORT_GAP = 0.45      # shorter gap = comma, longer = '...'


def say(*a):
    print(*a, flush=True)


def flow_meta_path():
    return os.path.join(os.path.dirname(cfg.META), "metadata_flow.jsonl")


def read_flow_meta():
    p = flow_meta_path()
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def join_text(parts, gaps):
    """same joining as speak.py's flow mode: short gap = comma, longer = '...'"""
    out = parts[0].strip().rstrip(" ,।.")
    for t, g in zip(parts[1:], gaps):
        out += (", " if g < SHORT_GAP else "... ") + t.strip().rstrip(" ,।.")
    return out + "."


def write16(path, x, rate):
    if rate != 16000:
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    r = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    if r > 1e-5:
        x = x * min(0.1 / r, 0.95 / max(1e-6, float(np.abs(x).max())))
    y = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(path + ".part", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(y.tobytes())
    os.replace(path + ".part", path)


def main(series="sukoon", dry=False):
    meta = {m["id"]: m for m in cfg.read_meta()}
    out_dir = os.path.join(os.path.dirname(cfg.META), "flow_clips")
    if not dry:
        os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(11)
    rows = []
    D = cfg.C["dataset"]
    for src in sources():
        if series not in src["name"].lower():
            continue
        ep = norm_name(src["name"])
        good = good_lines(src)
        hold = set()
        for k, l in enumerate(good):
            m = meta.get(f"{ep}_{k:04d}")
            if m is None or m.get("holdout") or m.get("text") != l["text"]:
                hold.add(round(l["speech"][0], 2))     # kept-out (or unknown) line: never inside a flow clip
        j = json.load(open(src["transcript"], encoding="utf-8"))
        segs = j["segments"] if isinstance(j, dict) else j
        segs = [s_ for s_ in segs if (s_.get("text") or "").strip() and s_.get("end") is not None]

        def usable(s_):
            if round(s_["start"], 2) in hold or not re.search("[\u0900-\u097F]", s_["text"]):
                return False
            if src["kind"] == "script" and s_.get("source") in (None, "script"):
                return True
            ps = [w.get("p") for w in s_.get("words") or [] if w.get("p") is not None]
            return bool(ps) and np.mean(ps) >= D["asr_min_avg_p"] and min(ps) >= D["asr_min_word_p"]

        if len(segs) < 2:
            continue
        x, rate = (None, None) if dry else read_wav16(src["voice"])
        made = 0
        for k in range(len(segs) - 1):
            n_want = rng.choice([2, 3, 3, 4, 4])
            if not usable(segs[k]):
                continue
            grp = [segs[k]]
            for jx in range(k + 1, min(len(segs), k + n_want)):
                gap = segs[jx]["start"] - grp[-1]["end"]
                if not usable(segs[jx]) or gap > MAX_GAP or gap < 0 or segs[jx]["end"] - grp[0]["start"] > MAX_SPAN:
                    break
                grp.append(segs[jx])
            if len(grp) < 2:
                continue
            gaps = [b_["start"] - a_["end"] for a_, b_ in zip(grp, grp[1:])]
            text = join_text([" ".join(g["text"].split()) for g in grp], gaps)
            prev_end = segs[k - 1]["end"] if k else 0.0
            nxt = segs[k + len(grp)]["start"] if k + len(grp) < len(segs) else grp[-1]["end"] + 1.0
            a = max(grp[0]["start"] - 0.12, (prev_end + grp[0]["start"]) / 2)
            b = min(grp[-1]["end"] + 0.25, (grp[-1]["end"] + nxt) / 2)
            fid = f"flow_{ep}_{k:04d}_{len(grp)}"
            p = os.path.join(out_dir, fid + ".wav")
            if not dry and not os.path.exists(p):
                write16(p, x[int(a * rate):int(b * rate)], rate)
            rows.append({"id": fid, "wav": p, "text": text, "dur": round(b - a, 3), "episode": src["name"],
                         "series": series, "source": "flow", "holdout": False, "lines": len(grp)})
            made += 1
        say(f"   {src['name']}: {made} flow clips")
    say(f"DONE flow clips: {len(rows)} clips, {sum(r['dur'] for r in rows) / 60:.0f} min")
    if dry:
        return rows
    with open(flow_meta_path() + ".part", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(flow_meta_path() + ".part", flow_meta_path())
    say(f"   -> {flow_meta_path()}")
    return rows
