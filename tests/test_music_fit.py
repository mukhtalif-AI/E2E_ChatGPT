"""test_music_fit.py - music must sound like its mood folder (audio_screen.MOOD_FIT) and a folder with no fitting
track hands over to the nearest mood (emotion_music.MOOD_NEAR). Synthetic sounds, no library, no GPU:
    python tests/test_music_fit.py app"""
import os, sys, types
import numpy as np
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "app")
import audio_screen as A
import emotion_music as E
ok = 0
def check(c, what):
    global ok
    print(("PASS " if c else "FAIL ") + what)
    if not c:
        sys.exit(1)
    ok += 1
sr = 32000
t = np.arange(60 * sr) / sr
rng = np.random.default_rng(1)
pad = 0.2 * np.sin(2 * np.pi * 110 * t) * (1 + 0.3 * np.sin(2 * np.pi * 0.05 * t)) + 0.0005 * rng.standard_normal(len(t))
groove = 0.05 * np.sin(2 * np.pi * 220 * t) + 0.03 * rng.standard_normal(len(t))
for b in np.arange(0, 60, 60 / 120):                       # 120 bpm, bright clicks
    i = int(b * sr)
    groove[i:i + 800] += rng.standard_normal(800) * np.exp(-np.arange(800) / 150) * 0.8
rp, rg = A.rhythm(pad, sr), A.rhythm(groove, sr)
check(rp["pulse"] < 0.3, f"a slow pad has little pulse ({rp})")
check(rg["pulse"] > 0.6 and rg["bright_hz"] > 1400, f"bright 120 bpm clicks = steady bright beat ({rg})")
check(A.misfit(rg, "Tension").startswith("it does not sound Tension"), "groove rejected for Tension")
check(A.misfit(rg, "Happy") == "", "groove fine for Happy")
check(A.misfit(rp, "Dark") == "", "pad fine for Dark")
check(A.misfit({"pulse": 0.8, "bright_hz": 600}, "Sad").startswith("it does not sound Sad"), "strong dark beat too much for Sad")
check(A.misfit({}, "Dark") == "", "old cache rows without pulse are not judged")
A.MOOD_FIT["on"] = False
check(A.misfit(rg, "Tension") == "", "MOOD_FIT off in the table")
A.MOOD_FIT["on"] = True
row = A._with_fit({}, "/m/BGM/Tension/x.mp3", {"kind": "music", "m": rg, "verdict": "ok", "why": []})
check(row["verdict"] == "reject" and row["misfit"], "a misfit row is a reject")
row = A._index_fit({"kind": "music", "m": rg})
check(row["fits"] == ["angry", "Love", "Happy"] and row["fit"]["Tension"], f"the index stores the moods it fits {row['fits']}")
check(A._with_fit({}, "/m/BGM/Happy/x.mp3", dict(row, verdict="ok", why=[]))["verdict"] == "ok", "the stored verdict is used for its own folder")
old = A._fit_tag(); A.MOOD_FIT["moods"]["Happy"] = {"pulse_max": 0.5}
check(A._fit_tag() != old and A._with_fit({}, "/m/BGM/Happy/x.mp3", dict(row, verdict="ok", why=[]))["verdict"] == "reject",
      "a changed table re-judges without the stored answer")
A.MOOD_FIT["moods"]["Happy"] = {}
check(E.near_moods("Tension", ["Dark", "Tension", "Eerie"]) == ["Tension", "Dark", "Eerie"], "Tension -> Dark, Eerie")
check(E.near_moods("Sad", ["Sad", "Calm"]) == ["Sad", "Calm"], "neighbours limited to the series' folders")
# pick_tracks: Tension has nothing that fits -> the section uses Dark, the opening track when it is from Dark
fake = types.ModuleType("tools")
lib = {"Tension": [], "Dark": ["/m/Dark/a.mp3", "/m/Dark/b.mp3"], "Eerie": ["/m/Eerie/c.mp3"]}
used = []
def _ht(cfg, folders):
    for f in lib.get(folders[0], []):
        if f not in used:
            used.append(f); return f
    cfg.setdefault("_music_misfit", []).append(f"{folders[0]}: x.mp3 - it does not sound {folders[0]}")
    return ""
fake._horror_track = _ht
fake._usable_music = lambda ctx, tr, f, db=None: (tr, db)
fake.GENRES = {"bhay": {"music": ["Dark", "Tension", "Eerie"]}}
sys.modules["tools"] = fake
E.glob.glob = lambda p: ["x"]
E.os.path.exists = lambda p: True
said = []
ctx = {"cfg": {"music_dir": "/m", "_music_series": "bhay"}, "say": said.append}
r = E.pick_tracks(ctx, [{"mood": "Tension"}, {"mood": "Eerie"}], "/m/Dark/a.mp3", None)
check(r == {"Tension": "/m/Dark/a.mp3", "Eerie": "/m/Eerie/c.mp3"}, f"Tension section keeps the Dark opening track {r}")
check(any("using Dark" in s for s in said) and any("no usable track in Tension" in s for s in said), "the log says why")
# drop-outs (19 Oct 2026): a drone that falls 25 dB for 0.5 s every 8 s keeps stopping and starting
hole = pad.copy()
for s0 in np.arange(8, 58, 8):
    hole[int(s0 * sr):int((s0 + 0.5) * sr)] *= 0.05
c, pm = A.holes(hole, sr)
check(c >= 5 and pm > 3, f"drop-outs counted ({c}, {pm}/min)")
check(A.holes(pad, sr)[0] == 0, "a steady pad has no drop-outs")
check(A.misfit(dict(rp, holes_per_min=pm), "Dark").startswith("it keeps stopping and starting"), "drop-outs rejected for Dark")
check(A.misfit(dict(rp, holes_per_min=pm), "Love").startswith("it keeps stopping and starting"), "and for every mood")
check(A.misfit(dict(rp, holes_per_min=1.0), "Dark") == "", "a few drop-outs are fine")
# a section whose mood ends up in the folder already playing keeps that track (no switch to a sister track)
used.clear(); said.clear()
lib["Dark"] = ["/m/Dark/a.mp3", "/m/Dark/b.mp3"]
r = E.pick_tracks(ctx, [{"mood": "Tension"}, {"mood": "Dark"}], "", None)
check(r["Tension"] == r["Dark"] == "/m/Dark/a.mp3", f"Tension fell back to Dark, the Dark section keeps it {r}")
print(f"{ok} checks passed")
