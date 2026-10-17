"""soundfix_on_copy.py - test the 17 Oct ambience / sound-effect fixes on COPIES (Experimental code paths only).

    python3 tests/soundfix_on_copy.py app "<episode folder>" plan|mix|unit [FROM=TO ...]   (FROM=TO as in smoke_tools --map)

plan: copies the episode's narration into _tmp, runs steps.scenes.horror_scenes with a FAKE director answer (no Ollama)
      that does everything the 32-episode cross-check found: a bed per mood that flips back and forth, 5+ beds, an
      invented bed name, a hit on scene 1, a door knock as a 'hit', '*' round the cue words, English cue words, an
      invented effect name. The story model's sense check is faked too (it calls the first effect wrong).
mix:  runs the real horror_sound (ffmpeg, CPU) on that copy with the plan from 'plan'.
unit: synthetic checks: bed runs keep playing across scene changes, crossfade at a real change, the seam of a loop,
      words across two timing lines, the folder check of a bed.
Nothing outside the _tmp copy is written (ratings / use counts are copies too).
"""
import glob, json, os, re, shutil, subprocess, sys, time, wave

APP = os.path.abspath(sys.argv[1])
EP = os.path.abspath(sys.argv[2])
MODE = sys.argv[3]
MAPS = [m.split("=", 1) for m in sys.argv[4:] if "=" in m]
sys.path.insert(0, APP)
import path_config                                           # noqa: E402

if not os.path.exists(path_config.CONFIG_FILE):              # the copy has no config.json: use the hub's own
    path_config.CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(APP))), "config.json")
cfg = path_config.load_config()
for fr, to in MAPS:
    for k, v in list(cfg.items()):
        i = v.lower().find(fr.lower()) if isinstance(v, str) else -1
        if i >= 0:
            cfg[k] = to + v[i + len(fr):].replace("\\", "/")
real_root = cfg.get("experimental_root")
TMP = os.path.join(os.path.dirname(APP), "_tmp", "soundfix_copy_" + os.path.basename(EP).replace(" ", "_"))
data, lib = os.path.join(TMP, "data"), os.path.join(TMP, "data", "Library")
log = []
say = lambda *x: (print(*x, flush=True), log.append(" ".join(str(y) for y in x)))
results = {}


def check(name, ok, detail=""):
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def setup():
    os.makedirs(os.path.join(data, "Activity"), exist_ok=True)
    os.makedirs(os.path.join(lib, "_tmp"), exist_ok=True)
    for f in ("audio_screen.json", "ratings.sqlite"):
        p = os.path.join(real_root, "Activity", f)
        if os.path.exists(p) and not os.path.exists(os.path.join(data, "Activity", f)):
            shutil.copy2(p, os.path.join(data, "Activity", f))
    for f in ("bhay_music_used.json", "music_harsh.json", "audio_index.json", "sfx_used.json"):
        for src in (os.path.join(real_root, "Library", f), os.path.join(real_root, f)):
            if os.path.exists(src) and not os.path.exists(os.path.join(lib, f)):
                shutil.copy2(src, os.path.join(lib, f))
                break
    cfg.update(experimental_mode=True, experimental_root=data, media_dir=lib, temporary_dir=os.path.join(lib, "_tmp"),
               sukoonvoice_media=os.path.join(TMP, "02_Narration"))


def paths():
    nar0 = os.path.dirname(sorted(glob.glob(os.path.join(EP, "02_Narration", "narrations", "*", "timing.json")))[0])
    name = os.path.basename(nar0)
    return nar0, name, os.path.join(TMP, "02_Narration", "narrations", name), os.path.join(TMP, "03_Artwork", name + "_scenes")


def ctx_():
    run = lambda cmd, cwd=None: subprocess.run(cmd, cwd=cwd).returncode
    return {"cfg": cfg, "say": say, "run_cmd": run, "media": lib, "stopped": lambda: False, "job": {}}


def fake_director(ask):
    """a director answer with every fault the cross-check found, built from the lists the real ask offers"""
    line = lambda key: [x.strip() for x in (re.search(rf"\n{key}: (.*)", ask) or [None, ""])[1].split(",") if x.strip() not in ("", "-")]
    amb, hits, cues = line("AMBIENCE"), line("HITS"), [c for c in line("CUES") if not c.startswith("- (")]
    n = len(re.findall(r"\n\d+\. \(\d+:\d\d\) ", ask))
    texts = re.findall(r"\n\d+\. \(\d+:\d\d\) (.*)", ask)
    A = (amb + ["?"] * 6)
    pattern = [A[0], A[0], A[1], A[0], A[2], A[3], A[4], "spooky wind howl", A[0], A[1]]
    sc = []
    for i in range(n):
        words = texts[i].split()
        x = {"happens": f"scene {i + 1}", "prompt": f"test scene {i + 1}, a room in a house at night",
             "ambience": pattern[i % len(pattern)], "hit": "", "cues": []}
        if i == 0 and hits:
            x["hit"] = hits[0]                                   # never on scene 1
        if i == 3:
            x["hit"] = "door knock"                              # not a stinger: must be left out
        if i == 1 and cues:
            x["cues"].append({"words": "*" + " ".join(words[2:5]) + "*", "sfx": cues[0]})
        if i == 2 and cues:
            x["cues"].append({"words": "creaking door opens slowly", "sfx": cues[min(1, len(cues) - 1)]})
        if i == 4:
            x["cues"].append({"words": " ".join(words[1:4]), "sfx": "magic chimes sparkle"})
        if i == 5 and cues:
            x["cues"].append({"words": " ".join(words[0:3]), "sfx": cues[-1]})
        sc.append(x)
    return json.dumps({"look": "test look", "character_anchors": {}, "scenes": sc}, ensure_ascii=False)


def plan():
    setup()
    nar0, name, nar, pic = paths()
    if os.path.exists(nar):
        shutil.rmtree(nar)
    shutil.copytree(nar0, nar, ignore=shutil.ignore_patterns("narration_music*.wav", "_*.f32", "lines"))
    if os.path.exists(pic):
        shutil.rmtree(pic)
    import tools, feedback, story
    from steps import scenes
    feedback.install(cfg)
    asks = {}

    def ask_step(c, key, prompt, temp, say=lambda *a: None):
        asks["director"] = prompt
        return fake_director(prompt), "fake director"

    def story_ask(c, prompt, *a, **k):
        if "Read the meaning, not single words" in prompt:      # the sense check: first effect is wrong
            asks["sense"] = prompt
            n = len(re.findall(r"\n?\d+\. sentence:", prompt))
            return json.dumps({"cues": [{"i": i, "verdict": "wrong_sound" if i == 0 else "ok", "why": "test"}
                                        for i in range(n)]}), "fake"
        if "Copy 2-4 consecutive words EXACTLY" in prompt:       # the re-ask: copies the first words of the text
            t = prompt.split("TEXT:\n", 1)[1].split()
            asks["reask"] = asks.get("reask", 0) + 1
            return json.dumps({"words": " ".join(t[:3])}, ensure_ascii=False), "fake"
        raise RuntimeError("no model in this test")

    scenes._ask_step = ask_step
    story.ask = story_ask
    scenes._picture_prompts = lambda *a, **k: []
    scenes._clean_scene = lambda c, s, d, i, prompt, *a, **k: prompt
    scenes._vary_scene = lambda c, s, d, scene, *a, **k: (scene, "kept (test)")
    scenes.horror_scenes(ctx_(), name, nar, pic, 0, os.path.basename(os.path.dirname(os.path.dirname(EP))).lower())
    shots = json.load(open(os.path.join(pic, "shots.json"), encoding="utf-8"))["shots"]
    beds = [os.path.basename(s["ambience"]) for s in shots]
    print("beds per scene:", beds)
    print("hits:", [os.path.basename(s["hit"]) for s in shots])
    print("cues:", [[(c["words"], os.path.basename(c["file"])) for c in s["cues"]] for s in shots])
    check("the director ask carries the one-bed-per-place rule", "AMBIENCE RULE" in asks.get("director", ""))
    check("at most 3 different beds", len({b for b in beds if b}) <= 3, f"{len({b for b in beds if b})}")
    check("no A-B-A flicker on a short scene", not any(
        beds[i - 1] and beds[i - 1] == beds[i + 1] != beds[i] and shots[i]["end"] - shots[i]["start"] <= 45
        for i in range(1, len(beds) - 1)))
    check("no hit on scene 1", not shots[0]["hit"])
    check("door knock not used as a hit", not shots[3]["hit"] or "knock" not in shots[3]["hit"].lower())
    allc = [c for s in shots for c in s["cues"]]
    check("no '*' left in cue words", all("*" not in c["words"] for c in allc))
    check("invented effect name left out", not shots[4]["cues"] if len(shots) > 4 else True)
    check("the sense check ran before the mix and dropped the first effect", "sense" in asks
          and not shots[1]["cues"], f"{len(allc)} cue(s) left")
    check("invented bed name logged", any("not in this series' list" in l and "spooky wind howl" in l for l in log))
    check("bed summary logged", any(l.strip().startswith("ambience:") for l in log))
    check("director reply kept", os.path.exists(os.path.join(pic, "director_reply.txt")))
    check("no heartbeat as a bed", not any("heartbeat" in b.lower() for b in beds))
    longest, run = 0.0, 0.0
    for j, s in enumerate(shots):
        same = j > 0 and s["ambience"] and s["ambience"] == shots[j - 1]["ambience"]
        run = run + (s["end"] - s["start"]) if same else (s["end"] - s["start"] if s["ambience"] else 0.0)
        longest = max(longest, run)
    check("no bed runs longer than 150 s without a breather", longest <= 150, f"longest {longest:.0f} s")
    from steps import scenes as S2
    th = [i + 1 for i, s in enumerate(shots) if any(w in s["text"].lower() for w in S2.THOUGHT_WORDS)]
    print("scenes with thought words:", th)


def mix():
    setup()
    nar0, name, nar, pic = paths()
    import numpy as np
    import tools, feedback
    feedback.install(cfg)
    genre = os.path.basename(os.path.dirname(os.path.dirname(EP))).lower()
    t0 = time.time()
    ok = tools.horror_sound(ctx_(), name, pic, "", genre, f"secs:{cfg.get('end_seconds') or tools.END_SECONDS}", None)
    check("horror_sound ran on the copy", ok, f"{time.time() - t0:.0f} s")
    check("bed runs logged", any("bed run(s)" in l for l in log))
    shots = json.load(open(os.path.join(pic, "shots.json"), encoding="utf-8"))["shots"]
    sr = 48000
    lead = float(json.load(open(os.path.join(nar, "music_shape.json"), encoding="utf-8")).get("lead_s", 0))
    y = tools._decode(cfg, os.path.join(nar, "narration_music.wav"), sr, 2).mean(1)
    p = tools._decode(cfg, os.path.join(nar, "narration_music_plain.wav"), sr, 2).mean(1)
    n = min(len(y), len(p))
    print("mix:", os.path.join(nar, "narration_music.wav"), f"{n / sr:.0f} s", "peak", float(np.abs(y[:n]).max()))
    check("mix has no clipping", float(np.abs(y).max()) < 1.0)
    with open(os.path.join(TMP, "mix_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")


def unit():
    import numpy as np
    import tools
    from steps import genre as G, scenes as S
    import self_review as R
    sr = 48000
    d = os.path.join(TMP, "unit")
    os.makedirs(d, exist_ok=True)
    rng = np.random.default_rng(1)

    def wav(p, secs):
        x = (rng.standard_normal((int(secs * sr), 2)) * 0.1).astype(np.float32)
        with wave.open(p, "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(sr)
            w.writeframes((x * 32767).astype("<i2").tobytes())
        return p

    A, B = wav(os.path.join(d, "a.wav"), 7), wav(os.path.join(d, "b.wav"), 9)
    shots = [{"start": 0, "end": 20, "ambience": A}, {"start": 20, "end": 40, "ambience": A},
             {"start": 40, "end": 60, "ambience": A}, {"start": 60, "end": 80, "ambience": B},
             {"start": 80, "end": 100, "ambience": ""}]
    mix = np.zeros((int(110 * sr), 2), np.float32)
    tools._decode = lambda c, f, r, ch: np.frombuffer(wave.open(f).readframes(10 ** 9), "<i2").reshape(-1, 2).astype(np.float32) / 32767
    n = G._bed_runs({}, shots, mix, 0.0, sr, -20.0, lambda a, b: 1.0, lambda *x: print(*x))
    rms = lambda t0, t1: float(np.sqrt(np.mean(mix[int(t0 * sr):int(t1 * sr)] ** 2)))
    mid = rms(8, 12)
    check("one run per bed (A A A -> one loop, then B)", n == 2, f"{n}")
    check("no dip where scenes 1-2 and 2-3 meet (same bed keeps playing)",
          min(rms(19.75, 20.25), rms(39.75, 40.25)) > 0.85 * mid, f"{rms(19.75, 20.25) / mid:.2f} of the middle level")
    check("no dip at a loop seam (7 s file)", min(rms(t - 0.25, t + 0.25) for t in (6.5, 13, 19.5, 26)) > 0.8 * mid)
    check("crossfade A->B keeps the level (equal power)", rms(59.5, 60.5) > 0.7 * mid, f"{rms(59.5, 60.5) / mid:.2f}")
    check("bed fades out where no bed follows", rms(83, 85) < 0.01 * mid)
    tim = [{"text": "वह धीरे से दरवाज़े", "start": 10.0, "end": 12.0}, {"text": "की तरफ बढ़ी और रुक गई", "start": 12.2, "end": 15.0}]
    line, j = R._line_of(tim, "दरवाज़े की तरफ", 0, 30)
    check("cue words across two timing lines are found", line is not None, str(line and line.get("text")))
    line, j = R._line_of(tim, "बढ़ी और रुक", 0, 30)
    check("words inside one line still use that one line", line is not None and line["start"] == 12.2)
    check("folder check: Rain/soft_loop.wav is a rain bed", not S._amb_fits("X/Rain/soft_loop.wav", "a quiet room", True)
          and S._amb_fits("X/Rain/soft_loop.wav", "a quiet room", False))
    check("folder check: rain bed fits a rainy scene", S._amb_fits("X/Rain/soft_loop.wav", "बारिश हो रही थी", True))


{"plan": plan, "mix": mix, "unit": unit}[MODE]()
print(json.dumps(results, ensure_ascii=False))
print("ALL PASSED" if results and all(results.values()) else "SOME CHECKS FAILED")
