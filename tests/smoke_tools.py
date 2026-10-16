"""smoke_tools.py - quick check that tools.py (and the app\\steps\\ modules split out of it later) still works.

No graphics card, no Ollama, no ComfyUI and no running hub needed (the hub may keep running; it is not touched).
Everything is written into a temporary folder, hub _tmp\\smoke_<time> (ignored by git). The real data folder,
music and sound libraries are only read.

    python tests\\smoke_tools.py --save-baseline   once, on the code BEFORE a split step -> tests\\tools_baseline.json
    python tests\\smoke_tools.py                   after each split step: the last line must say ALL PASSED
    python tests\\smoke_tools.py --quick           skip the step run on the episode copy (about 10 s instead of ~1-2 min)

Use the hub's own Python (the one hub.bat uses), e.g.
    E:\\ComfyUI_windows_portable\\python_embeded\\python.exe -s tests\\smoke_tools.py

Options
    --episode <folder>  a finished Experimental genre episode to copy (default: the newest one with a video)
    --track <file>      music file for the sound step (default: the track that episode used)
    --keep              keep the temporary folder (it is also kept when something fails)
    --map FROM=TO       rewrite a path prefix from config.json; only for running away from the PC (repeatable)
    --verbose           show what each step says

What it checks
 1 imports  every app\\*.py and app\\steps\\*.py imports; the hub start-up hooks run (tinker, feedback, narrator)
 2 names    every tools.X any app module uses, every step "fn" written in any module, every Tinker prompt / table
            key on tools, and every name in the baseline still resolves (exp_* steps on experimental_workspace)
 3 patches  the wrappers feedback.py and narrator.py put on tools are in place, and moved code (app\\steps\\*.py)
            never uses a swappable name directly (it must write tools.X, see TOOLS_SPLIT_PLAN.md)
 4 plans    tools.plan (Working) and experimental_workspace.prepare (Experimental) give the same steps as the baseline
 5 steps    on a copy of a finished episode: write_script, narration_dir, _beat_parts, _subtitle_text_for_timing,
            narration_lines, then horror_sound for real (music + ambience + effects, ffmpeg, CPU only). Probes prove
            horror_sound still reaches tools._horror_track, tools.GENRES and tools.shape_mix (what feedback / Tinker swap).
"""
import argparse, ast, copy, glob, importlib, inspect, json, os, py_compile, shutil, subprocess, sys, time, traceback, wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
BASELINE = os.path.join(HERE, "tools_baseline.json")
NOT_IMPORTABLE = {"audio_index"}      # a program the hub runs (reads sys.argv when imported): compiled only
TEXT = "एक छोटी सी कहानी शुरू होती है।\nदूसरी पंक्ति, थोड़ी लंबी, ताकि आवाज़ के लिए कुछ हो।\nऔर फिर अंत आता है।"
SAVED_FROM_DATA = ("hub_settings.json", "audio_index.json", "word_rack.json", "genre_rules.json", "music_harsh.json",
                   os.path.join("Tinker", "settings.json"))     # copied read-only so plans match the real hub

RESULTS = []
OPTS = None


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  - {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title):
    print(f"\n== {title}", flush=True)


class Probe(Exception):
    """raised by a stand-in function: proves the code under test called it through tools"""


# ------------------------------------------------------------------ a private config: writes go to the temp folder
def map_path(v, maps):
    if not isinstance(v, str):
        return v
    for a, b in maps:
        if v.lower().startswith(a.lower()):
            rest = v[len(a):]
            return b + (rest.replace("\\", "/") if "/" in b else rest)
    return v


def remap(obj, maps):
    if isinstance(obj, dict):
        return {k: remap(v, maps) for k, v in obj.items()}
    if isinstance(obj, list):
        return [remap(v, maps) for v in obj]
    return map_path(obj, maps)


def private_config(tmp, maps):
    raw = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8-sig"))
    raw = remap(raw, maps)
    p = raw.setdefault("paths", {})
    real = {"data": p.get("hub_output_root"), "exp": p.get("experimental_root") or p.get("hub_output_root")}
    data = os.path.join(tmp, "data")
    p.update({"hub_output_root": data, "experimental_root": data, "narrations_root": os.path.join(data, "narrations"),
              "episodes_root": os.path.join(data, "Episodes"), "hub_logs_root": os.path.join(tmp, "logs"),
              "voice_data_root": os.path.join(tmp, "voice")})
    os.makedirs(os.path.join(tmp, "voice"), exist_ok=True)       # a fixed word rack, so plans do not depend on yours
    json.dump({"smokeword": {"count": 3, "examples": [], "narrations": []}},
              open(os.path.join(tmp, "voice", "word_rack.json"), "w", encoding="utf-8"))
    for name in SAVED_FROM_DATA:
        src = os.path.join(real["data"] or "", name)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(data, name)), exist_ok=True)
            shutil.copy2(src, os.path.join(data, name))
    path = os.path.join(tmp, "config.json")
    json.dump(raw, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path, real


# ------------------------------------------------------------------ 1 imports
def app_modules():
    names = sorted(os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(APP, "*.py")))
    names += sorted("steps." + os.path.splitext(os.path.basename(f))[0]
                    for f in glob.glob(os.path.join(APP, "steps", "*.py")) if not f.endswith("__init__.py"))
    return names


def module_file(name):
    return os.path.join(APP, *name.split(".")) + ".py"


def do_imports(cfg_path):
    section("1 imports")
    sys.path.insert(0, APP)
    import path_config
    path_config.CONFIG_FILE = cfg_path                 # server.py's CFG now points at the temp folder
    mods = {}
    try:
        mods["server"] = importlib.import_module("server")
        check("import server", True)
    except Exception as e:
        check("import server", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return None
    for name in app_modules():
        if name == "server":
            continue
        if name in NOT_IMPORTABLE:
            try:
                py_compile.compile(module_file(name), doraise=True, cfile=os.path.join(OPTS.tmp, name + ".pyc"))
                check(f"compile {name}", True)
            except Exception as e:
                check(f"compile {name}", False, str(e))
            continue
        try:
            mods[name] = importlib.import_module(name)
            check(f"import {name}", True)
        except Exception as e:
            check(f"import {name}", False, f"{type(e).__name__}: {e}")
    server = mods["server"]
    tools = server.tools
    check("tools is the module server.py runs steps on", tools is sys.modules.get("tools"))
    check("server CFG writes into the temp folder", os.path.normcase(server.CFG["media_dir"]).startswith(
        os.path.normcase(OPTS.tmp)), server.CFG["media_dir"])
    # the hub's start-up hooks, in the order server.main() runs them
    for label, fn in (("tools.apply_settings", lambda: tools.apply_settings(server.CFG)),
                      ("tinker.install", lambda: importlib.import_module("tinker").install(server.CFG)),
                      ("feedback.install", lambda: importlib.import_module("feedback").install(server.CFG)),
                      ("narrator.install", lambda: importlib.import_module("narrator").install(server.CFG))):
        try:
            fn()
            check(f"start-up hook {label}", True)
        except Exception as e:
            check(f"start-up hook {label}", False, f"{type(e).__name__}: {e}")
    return mods


# ------------------------------------------------------------------ 2 names
def scan(path):
    """tools.X attributes, `from tools import X`, step "fn" strings and names set on tools, in one source file"""
    tree = ast.parse(open(path, encoding="utf-8-sig").read(), path)
    attrs, fns, sets = set(), set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "tools":
            attrs.add(n.attr)
            if isinstance(n.ctx, ast.Store):
                sets.add(n.attr)
        elif isinstance(n, ast.ImportFrom) and n.module == "tools":
            attrs.update(a.name for a in n.names if a.name != "*")
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == "fn" and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    fns.add(v.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "setattr" and n.args \
                and isinstance(n.args[0], ast.Name) and n.args[0].id == "tools":
            if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                sets.add(n.args[1].value)
    return attrs, fns, sets


def tools_names(tools):
    return sorted(n for n, v in vars(tools).items() if not n.startswith("__") and not inspect.ismodule(v))


def tinker_keys(mods):
    t = mods.get("tinker")
    if not t:
        return []
    keys = set(getattr(t, "TABLES", {})) | set(getattr(t, "PROMPT_LABELS", {}))
    try:
        keys |= set(t.prompt_keys())
    except Exception:
        pass
    return sorted(k[6:] for k in keys if k.startswith("tools."))


def do_names(mods, base):
    section("2 names")
    tools = mods["server"].tools
    ew = mods.get("experimental_workspace")
    used, fns, swapped = {}, {}, set()
    for name in app_modules():
        a, f, s = scan(module_file(name))
        for x in a:
            used.setdefault(x, []).append(name)
        for x in f:
            fns.setdefault(x, []).append(name)
        if name != "tools":
            swapped |= s
    missing = sorted(x for x in used if not hasattr(tools, x))
    check(f"{len(used)} tools.X names used by app modules resolve", not missing,
          "; ".join(f"{x} (used in {', '.join(used[x])})" for x in missing))
    bad = []
    for fn, where in sorted(fns.items()):
        home = ew if fn.startswith("exp_") else tools
        if home is None or not callable(getattr(home, fn, None)):
            bad.append(f"{fn} (step in {', '.join(where)})")
    check(f"{len(fns)} step functions resolve (getattr(tools, fn) / experimental_workspace.exp_*)", not bad, "; ".join(bad))
    tk = tinker_keys(mods)
    miss = [k for k in tk if not hasattr(tools, k)]
    check(f"{len(tk)} Tinker prompt / table keys on tools resolve", not miss, ", ".join(miss))
    if base:
        gone = [n for n in base.get("names", []) if not hasattr(tools, n)]
        check(f"{len(base.get('names', []))} baseline names still on tools", not gone, ", ".join(gone))
        if set(base.get("tinker_keys", [])) - set(tk):
            check("Tinker still offers every prompt / table it did", False,
                  ", ".join(sorted(set(base["tinker_keys"]) - set(tk))))
    return swapped, tk


# ------------------------------------------------------------------ 3 patches
FLAGS = {"_horror_track": "_feedback", "_sfx_names": "_feedback", "shape_mix": "_feedback",
         "plan": "_narrator", "horror_video": "_narrator", "genre_reels": "_narrator"}


def do_patches(mods, swapped, tk):
    section("3 patches")
    tools = mods["server"].tools
    for n, flag in FLAGS.items():
        check(f"tools.{n} is the {flag[1:]} wrapper", getattr(getattr(tools, n, None), flag, False))
    swappable = set(swapped) | set(FLAGS) | set(tk)
    files = [module_file(m) for m in app_modules() if m.startswith("steps.")]
    if not files:
        check("no app\\steps modules yet (nothing moved) - direct-use rule not needed", True)
    for path in files:
        tree = ast.parse(open(path, encoding="utf-8-sig").read(), path)
        hits = []
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
        inside = set()
        for f in funcs:                                 # a local variable called e.g. `plan` is fine
            a = f.args
            local = {x.arg for x in a.args + a.kwonlyargs + getattr(a, "posonlyargs", [])}
            local |= {x.arg for x in (a.vararg, a.kwarg) if x}
            local |= {x.id for x in ast.walk(f) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
            for x in ast.walk(f):
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                    inside.add(id(x))
                    if x.id in swappable and x.id not in local:
                        hits.append(f"{x.id} line {x.lineno}")
        for x in ast.walk(tree):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and id(x) not in inside and x.id in swappable:
                hits.append(f"{x.id} line {x.lineno}")
        hits = sorted(set(hits), key=lambda h: int(h.rsplit(" ", 1)[1]))
        for n in tree.body:
            if isinstance(n, ast.ImportFrom) and n.module == "tools":
                hits.append(f"module-level 'from tools import' line {n.lineno}")
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    if isinstance(t, ast.Name) and t.id in swappable:
                        hits.append(f"own copy of {t.id} line {n.lineno}")
        check(f"{os.path.relpath(path, ROOT)} reads swappable names only as tools.X", not hits, "; ".join(hits[:12]))
    return swappable


# ------------------------------------------------------------------ 4 plans
def samples(tools):
    out = []
    for g in sorted(tools.GENRES):
        out.append((f"genre/{g}/script", "genre", {"genre": g, "name": f"Smoke {g}", "script": TEXT, "speech_min": 1,
                                                   "total_min": 0, "reels": 2, "emotions": True, "dramatic": True}))
    out += [("genre/bhay/ai", "genre", {"genre": "bhay", "name": "", "speech_min": 8, "reels": 2}),
            ("genre/kisse/scenes-only", "genre", {"genre": "kisse", "name": "Smoke stages", "script": TEXT,
                                                  "stages": ["script", "voice", "scenes", "sound"], "reels": 0}),
            ("episode/script", "episode", {"name": "Smoke sukoon", "script": TEXT, "speech_min": 1, "total_min": 0,
                                           "reels": 1, "emotions": True, "dramatic": True}),
            ("episode/auto", "episode", {"name": "Smoke auto", "auto_topic": True, "speech_min": 12, "total_min": 30}),
            ("voice", "voice", {"name": "Smoke voice", "script": TEXT})]
    return out


def do_plans(mods, base):
    section("4 plans")
    server, ew = mods["server"], mods.get("experimental_workspace")
    tools = server.tools
    got = {}
    for i, (label, kind, body) in enumerate(samples(tools)):
        for mode in ("working", "experimental"):
            key = f"{mode}:{label}"
            try:
                if mode == "working":
                    p = tools.plan(kind, copy.deepcopy(body), dict(server.CFG))
                else:
                    p = ew.prepare(kind, copy.deepcopy(body), dict(server.CFG), f"smoke_{i:02d}")[0]
                got[key] = [s["fn"] for s in p.get("steps", [])]
            except Exception as e:
                got[key] = [f"ERROR {type(e).__name__}: {e}"]
            want = (base or {}).get("plans", {}).get(key)
            if want is None:
                check(f"plan {key}: {len(got[key])} steps", not str(got[key][:1]).startswith("['ERROR"),
                      "no baseline yet" if not base else "new sample, not in baseline")
            else:
                diff = "" if want == got[key] else f"baseline {want} / now {got[key]}"
                check(f"plan {key}: same {len(want)} steps as baseline", want == got[key], diff)
    return got


# ------------------------------------------------------------------ 5 steps on a copy of a finished episode
def find_episode(real_exp):
    best = None
    for ep in glob.glob(os.path.join(real_exp, "Series", "*", "Episodes", "*")):
        vids = glob.glob(os.path.join(ep, "Output", "*.mp4"))
        nars = [d for d in glob.glob(os.path.join(ep, "02_Narration", "narrations", "*"))
                if os.path.isfile(os.path.join(d, "timing.json")) and os.path.isfile(os.path.join(d, "narration.wav"))]
        shots = glob.glob(os.path.join(ep, "03_Artwork", "*_scenes", "shots.json"))
        if vids and nars and shots:
            t = max(os.path.getmtime(v) for v in vids)
            if not best or t > best[0]:
                best = (t, ep)
    return best[1] if best else ""


def copy_episode(src, data):
    series = os.path.basename(os.path.dirname(os.path.dirname(src)))
    dst = os.path.join(data, "Series", series, "Episodes", os.path.basename(src))
    nar_src = next(d for d in glob.glob(os.path.join(src, "02_Narration", "narrations", "*"))
                   if os.path.isfile(os.path.join(d, "timing.json")))
    name = os.path.basename(nar_src)
    nar = os.path.join(dst, "02_Narration", "narrations", name)
    os.makedirs(nar, exist_ok=True)
    for f in os.listdir(nar_src):                       # the voice and its notes; not the old mixes
        p = os.path.join(nar_src, f)
        if os.path.isfile(p) and not f.startswith("narration_music"):
            shutil.copy2(p, nar)
    pics_src = glob.glob(os.path.join(src, "03_Artwork", "*_scenes"))[0]
    pics = os.path.join(dst, "03_Artwork", os.path.basename(pics_src))
    os.makedirs(pics, exist_ok=True)
    for f in glob.glob(os.path.join(pics_src, "*.json")) + glob.glob(os.path.join(pics_src, "*.txt")):
        shutil.copy2(f, pics)                           # the scene plan, not the pictures
    for sub in ("00_Input", "01_Script"):
        if os.path.isdir(os.path.join(src, sub)):
            shutil.copytree(os.path.join(src, sub), os.path.join(dst, sub), dirs_exist_ok=True)
    genre = series
    try:
        genre = json.load(open(os.path.join(src, "00_Input", "input.json"), encoding="utf-8")).get("series") or series
    except Exception:
        pass
    return dst, name, nar, pics, genre, nar_src


def wav_seconds(p):
    with wave.open(p) as w:
        return w.getnframes() / float(w.getframerate())


def do_steps(mods, real):
    section("5 steps on a copy of a finished episode")
    server = mods["server"]
    tools = server.tools
    src = OPTS.episode or find_episode(real["exp"] or "")
    if not src or not os.path.isdir(src):
        check("a finished Experimental genre episode to copy", False, f"none found under {real['exp']}; use --episode")
        return
    data = os.path.join(OPTS.tmp, "data")
    ep, name, nar_copy, pics, genre, nar_src = copy_episode(src, data)
    check(f"copied {os.path.basename(src)} ({genre}) without pictures or videos", True, ep)
    cfg = dict(server.CFG)
    L = cfg.get("layout") or {}
    cfg.update({"experimental_mode": True, "experimental_root": data, "media_dir": os.path.join(data, "Library"),
                "sukoonvoice_media": os.path.join(ep, "02_Narration"),
                "episodes_dir": os.path.dirname(ep)})
    for alias, key in (("scripts_dir", "scripts"), ("pictures_dir", "pictures"), ("videos_dir", "videos"),
                       ("images_dir", "images"), ("temporary_dir", "temporary")):
        cfg[alias] = os.path.join(cfg["media_dir"], L.get(key, {"temporary": "_tmp"}.get(key, key)))
        os.makedirs(cfg[alias], exist_ok=True)
    said = []

    def say(*a):
        line = " ".join(str(x) for x in a)
        said.append(line)
        if OPTS.verbose:
            print("     | " + line.replace("\n", "\n     | "), flush=True)

    def run_cmd(cmd, cwd=None, env=None):
        r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            say(f"   command failed ({r.returncode}): {(r.stderr or '')[-400:]}")
        return r.returncode

    ctx = {"cfg": cfg, "say": say, "media": cfg["media_dir"], "hub_cfg": server.CFG, "stopped": lambda: False,
           "job": {"id": "smoke", "steps": [], "params": {}}, "run_cmd": run_cmd, "add_job": lambda *a, **k: None}

    def step(label, fn):
        t = time.time()
        try:
            r = fn()
            return r, time.time() - t, None
        except Probe as e:
            raise e
        except Exception as e:
            return None, time.time() - t, f"{type(e).__name__}: {e} | last said: {said[-1] if said else '-'}"

    r, t, err = step("write_script", lambda: tools.write_script(ctx, "smoke_script", TEXT))
    p = os.path.join(cfg["scripts_dir"], tools.slug("smoke_script") + ".txt")
    check("write_script saves the script", not err and os.path.isfile(p), err or p)
    nar, t, err = step("narration_dir", lambda: tools.narration_dir(cfg, name, existing=True))
    check("narration_dir finds the copied voice (Experimental layout)", not err and
          os.path.normcase(os.path.abspath(nar or "")) == os.path.normcase(os.path.abspath(nar_copy)), err or nar)
    nar = nar_copy
    timing = json.load(open(os.path.join(nar, "timing.json"), encoding="utf-8"))
    parts, t, err = step("_beat_parts", lambda: tools._beat_parts(nar, float(cfg.get("scene_seconds") or tools.SCENE_SECONDS)))
    ok = not err and parts and all(float(a["start"]) <= float(b["start"]) for a, b in zip(parts, parts[1:]))
    check("_beat_parts cuts the story into scenes in order", ok, err or f"{len(parts or [])} scenes")
    text = open(os.path.join(nar, "script.txt"), encoding="utf-8").read() if os.path.isfile(os.path.join(nar, "script.txt")) else TEXT
    subs, t, err = step("_subtitle_text_for_timing", lambda: tools._subtitle_text_for_timing(text, timing))
    check("_subtitle_text_for_timing gives one subtitle per timed line", not err and len(subs or []) == len(timing),
          err or f"{len(subs or [])} / {len(timing)}")
    nl, t, err = step("narration_lines", lambda: tools.narration_lines(cfg, nar))
    check("narration_lines lists every line", not err and len((nl or {}).get("lines", [])) == len(timing),
          err or f"{len((nl or {}).get('lines', []))} lines")

    track = OPTS.track
    if not track:
        try:
            track = json.load(open(os.path.join(nar_src, "hub_series.json"), encoding="utf-8")).get("track") or ""
            track = map_path(track, OPTS.maps)
        except Exception:
            track = ""
    if track and not os.path.isfile(track):
        say(f"(track {track} not found; the step picks one)")
        track = ""

    # probes: does horror_sound still go through the names feedback / Tinker swap?
    saved = {n: getattr(tools, n) for n in ("GENRES", "_horror_track", "shape_mix")}
    try:
        g = copy.deepcopy(tools.GENRES)
        g.setdefault(genre, copy.deepcopy(g.get("bhay", {})))["music"] = ["__smoke_probe__"]
        tools.GENRES = g

        def probe_track(c, folders=()):
            raise Probe(list(folders))
        tools._horror_track = probe_track
        try:
            tools.horror_sound(ctx, name, pics, track="", genre=genre, tail="1min")
            seen = None
        except Probe as e:
            seen = e.args[0]
        except Exception as e:
            seen = f"{type(e).__name__}: {e}"
        check("horror_sound picks music through tools._horror_track (feedback's ratings)", isinstance(seen, list), str(seen))
        check("horror_sound reads the series table through tools.GENRES (Tinker)", seen == ["__smoke_probe__"], str(seen))
        tools.GENRES, tools._horror_track = saved["GENRES"], saved["_horror_track"]
        if track:
            def probe_mix(*a, **k):
                raise Probe("shape_mix")
            tools.shape_mix = probe_mix
            try:
                tools.horror_sound(ctx, name, pics, track=track, genre=genre, tail="1min")
                seen = None
            except Probe as e:
                seen = e.args[0]
            except Exception as e:
                seen = f"{type(e).__name__}: {e}"
            check("horror_sound mixes through tools.shape_mix (feedback's ducking)", seen == "shape_mix", str(seen))
    finally:
        for n, v in saved.items():
            setattr(tools, n, v)

    # the real thing: music + ambience + effects under the voice
    import sfx_lib
    mark = sfx_lib.mark_used
    sfx_lib.mark_used = lambda *a, **k: None            # the real SFX register is not written by a test
    try:
        print("  ...  horror_sound on the copy (ffmpeg, CPU; up to a few minutes)", flush=True)
        r, t, err = step("horror_sound", lambda: tools.horror_sound(ctx, name, pics, track=track, genre=genre, tail="1min"))
    finally:
        sfx_lib.mark_used = mark
    mix = os.path.join(nar, "narration_music.wav")
    check(f"horror_sound makes narration_music.wav ({t:.0f} s)", not err and r is not False and os.path.isfile(mix),
          err or ("; ".join(said[-3:]) if r is False else ""))
    if os.path.isfile(mix):
        try:
            lead = float(json.load(open(os.path.join(nar, "music_shape.json"), encoding="utf-8")).get("lead_s", 0))
            want = lead + wav_seconds(os.path.join(nar, "narration.wav")) + tools._end_seconds(cfg)
            have = wav_seconds(mix)
            check("the mix is as long as lead + voice + ending", abs(have - want) < 3, f"{have:.1f} s, expected {want:.1f} s")
        except Exception as e:
            check("the mix is as long as lead + voice + ending", False, str(e))
    placed = [s for s in said if "placed" in s]
    print("  info " + ("; ".join(x.strip() for x in placed) or "no ambience / effects lines in the log"), flush=True)


# ------------------------------------------------------------------ main
def main():
    global OPTS
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--episode", default="")
    ap.add_argument("--track", default="")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--map", action="append", default=[])
    OPTS = ap.parse_args()
    OPTS.maps = [tuple(m.split("=", 1)) for m in OPTS.map if "=" in m]
    if os.name != "nt" and not OPTS.maps:
        print("  (not on Windows and no --map given: the D:\\ / E:\\ paths in config.json will not be found)")
    OPTS.tmp = os.path.join(ROOT, "_tmp", "smoke_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(OPTS.tmp, exist_ok=True)
    start = time.time()
    print(f"smoke test for tools.py  ({ROOT}, python {sys.version.split()[0]})\n  temp folder: {OPTS.tmp}")
    cfg_path, real = private_config(OPTS.tmp, OPTS.maps)
    here = os.getcwd()
    os.chdir(OPTS.tmp)                                  # a stray relative path lands in the temp folder
    base = None
    if os.path.isfile(BASELINE) and not OPTS.save_baseline:
        base = json.load(open(BASELINE, encoding="utf-8"))
        print(f"  baseline: {BASELINE} (saved {base.get('saved', '?')})")
    elif not OPTS.save_baseline:
        print("  no baseline yet: run once with --save-baseline on the code before a split step")
    try:
        mods = do_imports(cfg_path)
        if mods:
            swapped, tk = do_names(mods, base)
            do_patches(mods, swapped, tk)
            plans = do_plans(mods, base)
            if OPTS.save_baseline:
                tools = mods["server"].tools
                json.dump({"saved": time.strftime("%Y-%m-%d %H:%M:%S"), "tools_lines": sum(1 for _ in open(
                    module_file("tools"), encoding="utf-8")), "names": tools_names(tools), "tinker_keys": tk,
                    "plans": plans}, open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"\n  baseline written: {BASELINE} ({len(tools_names(tools))} names, {len(plans)} plans)")
            if not OPTS.quick:
                do_steps(mods, real)
    except Exception as e:
        check("smoke test itself", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        os.chdir(here)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} checks passed in {time.time() - start:.0f} s")
    if bad:
        print("FAILED:\n" + "\n".join(f"  - {n}: {d}" for n, _, d in bad))
    if bad or OPTS.keep:
        print(f"  temp folder kept: {OPTS.tmp}")
    else:
        shutil.rmtree(OPTS.tmp, ignore_errors=True)
    print("ALL PASSED" if not bad else "SOME CHECKS FAILED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
