"""test_emotion_music.py - checks the emotion music rules and mixer on made-up sound (no GPU, no Ollama, no hub).

    python tests\\test_emotion_music.py          (the hub's Python, or any Python with numpy; ffmpeg optional)

It never touches your episodes or libraries: everything is made in memory (and in a temp folder for the loudness check).
"""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
import numpy as np                                      # noqa: E402
import emotion_music as em                              # noqa: E402

SR = 48000
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  - {detail}" if detail else ""), flush=True)


def db(x):
    return 20 * np.log10(float(np.sqrt(np.mean(np.asarray(x, np.float64) ** 2))) + 1e-12)


def fake_voice(seconds=80):
    """speech-like bursts: 2.5 s of sound, then 0.4 s or (every 4th) 1.6 s of silence"""
    rng = np.random.default_rng(1)
    v = np.zeros(int(seconds * SR), np.float32)
    t, k = 0.5, 0
    while t < seconds - 3:
        a, b = int(t * SR), int((t + 2.5) * SR)
        tt = np.arange(b - a) / SR
        v[a:b] = (0.2 * np.sin(2 * np.pi * 220 * tt) + 0.05 * rng.normal(size=b - a)).astype(np.float32)
        k += 1
        t += 2.5 + (1.6 if k % 4 == 0 else 0.4)
    return v


def fake_track(freq, seconds=40):
    tt = np.arange(int(seconds * SR)) / SR
    y = 0.3 * np.sin(2 * np.pi * freq * tt) + 0.1 * np.sin(2 * np.pi * 6000 * tt)    # a low note + some highs
    y = y.astype(np.float32)
    full = np.stack([y, y], 1)
    low = np.stack([0.3 * np.sin(2 * np.pi * freq * tt)] * 2, 1).astype(np.float32)
    return full, low, db(full)


def band(x, f, sr=SR):
    """level of one frequency in a stereo chunk (dB)"""
    mono = x.mean(1)
    sp = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    fr = np.fft.rfftfreq(len(mono), 1 / sr)
    i = int(np.argmin(np.abs(fr - f)))
    return 20 * np.log10(sp[max(0, i - 2):i + 3].max() + 1e-9)


def test_sections():
    print("== sections (track cap, no jumping back, minimum length)")
    s = em.settings("kisse")
    sc = lambda moods, dur=60: [{"start": i * dur, "end": (i + 1) * dur, "mood": m, "weight": 1} for i, m in enumerate(moods)]
    secs = em.make_sections(sc(["Sad", "Sad", "Love", "Love", "Sad", "Sad", "Calm", "Calm"]), s, 480)
    moods = [c["mood"] for c in secs]
    check("never back to a track it left (Sad, Love, Sad -> no second Sad)", len(moods) == len(set(moods)), str(moods))
    check("at most 3 tracks", len(secs) <= 3, str(moods))
    secs = em.make_sections(sc(["Sad", "Love", "Sad", "Calm", "Sad"], 40), s, 200)
    check("a 40 s mood swing does not get its own track", all(c["end"] - c["start"] >= 80 for c in secs) or len(secs) == 1,
          str([(c["mood"], c["start"], c["end"]) for c in secs]))
    secs = em.make_sections(sc(["Sad", "Love", "Calm", "Happy", "Sad"], 120), dict(s, max_tracks=3), 600)
    check("five 2-minute moods -> 3 tracks", len(secs) <= 3, str([c["mood"] for c in secs]))
    one = em.make_sections(sc(["Sad", "Love"], 200), dict(s, sections=False), 400)
    check("sections off -> one track", len(one) == 1)
    check("Gyaan and Kareeb are off", not em.settings("gyaan").get("on") and not em.settings("kareeb").get("on"))
    check("a series can not go above 3 tracks", em.settings("bhay")["max_tracks"] <= 3)


def make_map():
    sc = [{"n": 1, "start": 0.0, "end": 30.0, "mood": "Sad", "weight": 0},
          {"n": 2, "start": 30.0, "end": 52.0, "mood": "Sad", "weight": 1},
          {"n": 3, "start": 52.0, "end": 77.0, "mood": "Love", "weight": 2}]
    turns = [{"at": 21.5, "line_end": 24.0, "weight": 1, "words": "a", "kind": "reveal"},
             {"at": 39.4, "line_end": 41.9, "weight": 2, "words": "b", "kind": "twist"},
             {"at": 62.4, "line_end": 64.9, "weight": 3, "words": "c", "kind": "death"}]
    return {"scenes": sc, "sections": [{"start": 0.0, "end": 52.0, "mood": "Sad"}, {"start": 52.0, "end": 80.0, "mood": "Love"}],
            "turns": turns}


def test_mix():
    print("== mixer on made-up sound")
    s = dict(em.settings("kisse"), strength=1.0, return_mood="Calm")
    v = fake_voice(80)
    lead = 4.0
    end = lead + len(v) / SR + 10
    sp, _ = em.speech_frames(v, SR)
    pauses = [(lead + a, lead + b) for a, b in em.gaps(sp)]
    m = make_map()
    segs = em.plan_segments(m, s, lead, end, pauses)
    check("two sections + a return after the heavy line", [g["mood"] for g in segs] == ["Sad", "Love", "Calm"],
          str([(g["mood"], g["start"], g["cut"]) for g in segs]))
    sad = {"scenes": [dict(x, mood="Sad") for x in m["scenes"]], "sections": [{"start": 0.0, "end": 80.0, "mood": "Sad"}],
           "turns": [dict(t) for t in m["turns"]]}
    check("a sad story stays sad: no lighter Calm return (Blue Ink Diary, 17 Oct)",
          [g["mood"] for g in em.plan_segments(sad, s, lead, end, pauses)] == ["Sad"])
    b = segs[1]["start"]
    check("the track change sits in a pause", any(a - 0.01 <= b <= c + 0.01 for a, c in pauses), f"change at {b}")
    tracks = {"Sad": fake_track(110), "Love": fake_track(330), "Calm": fake_track(550)}
    base = [(0, -80.0), (2, -14.0), (lead + 0.5, -14.0), (lead + 3.5, -18.0), (end - 12, -18.0), (end - 10, -15.0),
            (end - 4, -15.0), (end, -80.0)]
    out, info = em.render(v, tracks, segs, base, m, s, lead, SR)
    check("output has the right length and no NaN", len(out) == int(round(end * SR)) and np.isfinite(out).all())
    vdb = info["vdb_act"]

    def seg(a, z):
        return out[int(a * SR):int(z * SR)]
    w2 = m["turns"][1]
    s0 = lead + w2["at"] - em.PRE_S - em.drop_len(s, 2, w2)
    quiet = seg(s0 + 0.05, lead + w2["line_end"])
    check("weight 2: silence before and through the line", db(quiet) < vdb - 60, f"{db(quiet) - vdb:.0f} dB under the voice")
    w3 = m["turns"][2]
    s3 = lead + w3["at"] - em.PRE_S - em.drop_len(s, 3, w3)
    check("weight 3: drop starts 1.5-4 s before the line (scaled by the line and the pause)", 1.5 <= (lead + w3["at"]) - s3 <= 4.3,
          f"{(lead + w3['at']) - s3:.2f} s")
    check("silence is longer with a pause before the line", em.drop_len(s, 3, dict(w3, pause_before=1.5)) > em.drop_len(s, 3, w3))
    check("a weight-3 silence is longer than a weight-2 one", em.drop_len(s, 3, w3) > em.drop_len(s, 2, w2))
    check("weight 3: silence before the line", db(seg(s3 + 0.05, lead + w3["at"])) < vdb - 60)
    bed = np.asarray(info["bed"])
    i3 = int((s3 + 0.1) * em.CR)
    check("weight 3: ambience and effects stop too", bed[i3] < 0.01 and bed[int((lead + w3["line_end"]) * em.CR)] < 0.01)
    check("weight 2: ambience at half", abs(bed[int((s0 + 0.2) * em.CR)] - 0.5) < 0.05)
    note = seg(lead + w3["line_end"] + em.PRE_S + 0.05, lead + w3["line_end"] + em.PRE_S + 1.0)
    check("weight 3: one soft low note after the line", band(note, 146.8) > band(seg(s3 + 0.1, s3 + 1.0), 146.8) + 30)
    w1 = m["turns"][0]
    dip = db(seg(lead + w1["at"] - 0.5, lead + w1["line_end"]))
    before = db(seg(lead + w1["at"] - 6, lead + w1["at"] - 3))
    check("weight 1: a dip, not a silence", -12 < dip - before < -2, f"{dip - before:.1f} dB")
    back = db(seg(lead + w3["line_end"] + em.PRE_S + s["return_delay_s"] + 4, lead + w3["line_end"] + 12))
    check("music comes back after the heavy line", back > vdb - 30, f"{back - vdb:.0f} dB")
    after = seg(lead + w3["line_end"] + em.PRE_S + s["return_delay_s"] + 4, lead + w3["line_end"] + 12)
    check("it comes back in the return mood (Calm track)", band(after, 550) > band(after, 330) + 20)
    x0 = segs[1]["start"]
    mid = seg(x0 - 0.3, x0 + 0.3)
    check("crossfade: both tracks heard in the middle", abs(band(mid, 110) - band(mid, 330)) < 12)
    early = seg(x0 - 6, x0 - 3)
    check("before the crossfade only the first track", band(early, 110) > band(early, 330) + 30)
    # pauses lift, words duck
    rel, gm = np.asarray(info["rel_db"]), np.asarray(info["gap"])
    sp_mix = np.zeros(len(rel), bool)
    o = int(lead * em.CR)
    sp_mix[o:o + len(sp)] = sp[:len(rel) - o]
    lifted = rel[(gm > 0.99)]
    talk = rel[sp_mix]
    check("music lifts in pauses and sits lower under words", len(lifted) and lifted.mean() > talk.mean() + 1.5,
          f"pauses {lifted.mean():+.1f} dB, words {talk.mean():+.1f} dB")
    check("never lifted while a word is spoken", float(gm[sp_mix].max()) < 0.05, f"max {float(gm[sp_mix].max()):.2f}")
    hi_calm = band(seg(lead + 5, lead + 9), 6000) - band(seg(lead + 5, lead + 9), 110)
    hi_heavy = band(seg(lead + 54, lead + 57), 6000) - band(seg(lead + 54, lead + 57), 330)
    check("calm parts are warmer (fewer highs) than heavy parts", hi_calm < hi_heavy - 3,
          f"highs vs low note: calm {hi_calm:.1f} dB, heavy {hi_heavy:.1f} dB")
    s_off = dict(s, curve=False, stingers=False)
    out2, info2 = em.render(v, tracks, segs[:1], base, {"scenes": m["scenes"], "turns": []}, s_off, lead, SR)
    check("curve and stingers off: a flat bed like before", float(np.abs(np.asarray(info2["rel_db"])).max()) < 1e-6 and
          float(np.asarray(info2["music_k"]).min()) == 1.0)
    return out, v, lead


def test_loudness(out, v, lead):
    print("== loudness (two-pass linear loudnorm keeps the silences)")
    ff = shutil.which("ffmpeg")
    if not ff:
        check("ffmpeg found", False, "skipped")
        return
    d = tempfile.mkdtemp(prefix="emo_test_")
    try:
        mixd = out.copy()
        a = int(lead * SR)
        mixd[a:a + len(v)] += v[:, None]
        raw = os.path.join(d, "m.f32")
        mixd.astype("<f4").tofile(raw)
        wav = os.path.join(d, "m.wav")
        ok = em.loudnorm2({"ffmpeg": ff}, raw, SR, 2, wav)
        p = subprocess.run([ff, "-v", "error", "-i", wav, "-f", "f32le", "-ac", "2", "-"], capture_output=True)
        y = np.frombuffer(p.stdout, np.float32).reshape(-1, 2)
        m = make_map()
        t = m["turns"][2]
        s3 = lead + t["at"] - em.PRE_S - 2.5
        i0, i1 = int((s3 - 4) * SR), int((lead + t["at"] - 0.1) * SR)       # music, then the drop (voice goes on)
        j0, j1 = int((lead + 10) * SR), int((lead + 14) * SR)
        check("loudnorm ran", ok and len(y) > 0)
        diff = (db(y[i0:i1]) - db(y[j0:j1])) - (db(mixd[i0:i1]) - db(mixd[j0:j1]))
        check("loudness is one gain for the whole file (the drop keeps its shape)", abs(diff) < 0.5, f"{diff:+.2f} dB")
        mz = subprocess.run([ff, "-hide_banner", "-nostats", "-i", wav, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                            capture_output=True, text=True).stderr
        j = json.loads(mz[mz.rindex("{"):mz.rindex("}") + 1])
        check("final loudness about -16 LUFS", -18.5 <= float(j["input_i"]) <= -15, j["input_i"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    test_sections()
    out, v, lead = test_mix()
    test_loudness(out, v, lead)
    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} checks passed")
    print("ALL PASSED" if not bad else "FAILED: " + "; ".join(bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
