"""steps/hub.py - Library / System / Settings pages. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4a).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools

WIN = os.name == "nt"


# ------------------------------------------------------------------ local model settings (Ollama)
def _settings_path(cfg):
    return os.path.join(cfg["media_dir"], "hub_settings.json")


def apply_settings(cfg):
    try:
        st = json.load(open(_settings_path(cfg), encoding="utf-8"))
    except Exception:
        st = {}
    for key in ("llm", "story_model", "vision_llm", "prompt_model", "director_model", "scene_picture_model",
                "scene_seconds", "end_seconds", "subscribe_end"):
        if st.get(key):
            cfg[key] = st[key]
    return st


def llm_models(cfg):
    """Models installed in Ollama."""
    try:
        raw = json.loads(tools._get(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/tags", 5))
        out = [{"name": m.get("name", "")} for m in raw.get("models", []) if m.get("name")]
    except Exception as e:
        return {"current": cfg.get("llm"), "models": [], "error": f"Ollama not reachable ({e})"}
    return {"current": cfg.get("llm"), "prompt_model": cfg.get("prompt_model"),
            "models": out, "top": [x["name"] for x in out[:3]]}


def set_llm(cfg, name):
    st = apply_settings(cfg)
    st.pop("llm_backend", None)
    st["llm"] = name
    json.dump(st, open(_settings_path(cfg), "w", encoding="utf-8"), indent=1)
    cfg["llm"] = name
    return llm_models(cfg)


def set_prompt_model(cfg, name):
    st = apply_settings(cfg)
    st.pop("llm_backend", None)
    st["prompt_model"] = name
    json.dump(st, open(_settings_path(cfg), "w", encoding="utf-8"), indent=1)
    cfg["prompt_model"] = name
    return llm_models(cfg)


# ------------------------------------------------------------------ status / library / system
def gpu_info():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4,
                           creationflags=0x08000000 if WIN else 0)
        n, used, tot, util = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        return {"name": n, "mem_used": int(used), "mem_total": int(tot), "util": int(util)}
    except Exception:
        return None


_gpu_cache = {"t": 0, "v": None}


def status(cfg, jobs):
    if time.time() - _gpu_cache["t"] > 3:
        _gpu_cache.update(t=time.time(), v=gpu_info())
    ol = False
    try:
        tools._get(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/tags", 2); ol = True
    except OSError:
        pass
    js = jobs.jobs if jobs else []
    run = next((j for j in js if j["status"] == "running"), None)
    free = None
    try:
        free = round(shutil.disk_usage(cfg["media_dir"]).free / 2 ** 30, 1)
    except OSError:
        pass
    eng = {"voice": os.path.isdir(os.path.join(cfg["sukoonvoice_dir"], "libs", "cb")),
           "studio": os.path.exists(cfg["studio_python"])}
    return {"comfy": tools.comfy_running(cfg), "ollama": ol,
            "gpu": _gpu_cache["v"], "disk_free_gb": free, "engines": eng,
            "running": run and {"id": run["id"], "title": run["title"], "step": run.get("step", 0),
                                "n_steps": len(run.get("steps") or []), "step_label": run.get("step_label", "")},
            "queued": sum(1 for j in js if j["status"] == "queued")}


# ------------------------------------------------------------------ suggestions (the hub ASKS - nothing runs by itself)
def _dismissed_path(cfg):
    return os.path.join(cfg["media_dir"], "suggestions_dismissed.json")


def dismiss(cfg, sid):
    p = _dismissed_path(cfg)
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        d = {}
    d[sid] = time.time()
    json.dump(d, open(p, "w", encoding="utf-8"), indent=1)


def suggestions(cfg, jobs):
    """small, useful next steps found by looking at what exists (no AI, no graphics card). Each one has a button;
    nothing happens until it is clicked. Dismissed ones stay away (30 days)."""
    try:
        gone = {k: v for k, v in json.load(open(_dismissed_path(cfg), encoding="utf-8")).items() if v > time.time() - 30 * 86400}
    except Exception:
        gone = {}
    js = jobs.jobs if jobs else []
    busy = {(j.get("params") or {}).get("video") for j in js if j["status"] in ("queued", "running")}
    out = []
    kinds = {}

    def add(sid, text, why, button, action, prio=5, kind="", most=9):
        if sid in gone or kinds.get(kind or sid, 0) >= most:
            return
        kinds[kind or sid] = kinds.get(kind or sid, 0) + 1
        out.append({"id": sid, "text": text, "why": why, "button": button, "action": action, "prio": prio})

    for e in episodes(cfg)[:8]:
        v = e["video"]
        if not v or v in busy:
            continue
        rep = os.path.splitext(v)[0] + ".check.json"
        if os.path.exists(rep):
            try:
                r = json.load(open(rep, encoding="utf-8"))
            except Exception:
                r = {}
            n = len(r.get("voice") or []) + len([m for m in r.get("motion") or [] if m.get("kind") != "abrupt camera move"])
            if n and not r.get("auto"):
                add("repair:" + v, f"{e['name']}: {n} thing(s) to fix", "found by the last check (voice lines / motion)",
                    "Open Repair", {"page": "repair", "pick": v}, 2, "repair", 2)
        elif time.time() - os.path.getmtime(v) < 3 * 86400:
            add("check:" + v, f"Check {e['name']} for glitches", "stutters, wrong words, jumps - no graphics card, runs beside other jobs",
                "Check it", {"kind": "check_video", "body": {"video": v}}, 6, "check", 1)
        if not e["reels"] and time.time() - os.path.getmtime(v) < 7 * 86400:
            add("reels:" + v, f"Make 3 reels from {e['name']}", "it has no reels yet", "Open Reels", {"page": "reels", "pick": v},
                5, "reels", 1)
    try:
        import story
        unused = 0
        for f in glob.glob(os.path.join(cfg["scripts_dir"], "*.txt")):
            if re.search(r"(_old_\d+|_full|\.en)\.txt$", f):
                continue
            nm = os.path.basename(f)[:-4]
            if not os.path.exists(os.path.join(tools.narration_dir(cfg, nm, existing=True), "narration.wav")):
                unused += 1
        if unused == 0 and not any(j["kind"] in ("stories", "stories_local") and j["status"] in ("queued", "running") for j in js):
            add(time.strftime("stories:%Y%m%d"), "Write 5 fresh scripts", "every written script has been used",
                "Write them", {"kind": "stories", "body": {"n": 5, "speech_min": 12, "writer": "auto"}}, 4)
        t = story.pick_topic(cfg, seed=int(time.strftime("%Y%m%d")))
        add("topic:" + t, f"Next episode idea: {t}", "a topic your earlier episodes have not used yet",
            "Use it", {"page": "episode", "topic": t}, 7)
    except Exception:
        pass
    if not tools.load_index(cfg).get("themes", {}).get("sukoon"):
        add("style_index", "Teach the hub your thumbnail look", "artwork then matches your Sukoon thumbnails",
            "Build it", {"kind": "style_index", "body": {}}, 3)
    if not os.path.isdir(os.path.join(tools.en_dir(cfg), "libs", "qwen")):
        add("english_setup", "Set up the English narrator", "needed for English episodes: copies its libraries "
            "(~0.9 GB, from MukhtalifAudioBook) and speaks 3 test lines", "Set it up", {"kind": "english_setup", "body": {}}, 1)
    for j in sorted(js, key=lambda j: -(j.get("ended") or 0))[:10]:
        if j["status"] == "failed" and (j.get("ended") or 0) > time.time() - 86400:
            add("again:" + j["id"], f"Failed: {j['title']}", (j.get("step_label") or "") + " - see Activity",
                "Open Activity", {"page": "jobs", "job": j["id"]}, 2, "failed", 2)
    out.sort(key=lambda x: x["prio"])
    return out[:6]


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def library(cfg, kind="all"):
    out = {}
    if kind in ("all", "narrations"):
        L = []
        roots = {tools.narrations_root(cfg), os.path.join(cfg["sukoonvoice_media"], "narrations")}
        for root in roots:
            for d in glob.glob(os.path.join(root, "*")):
                n = os.path.basename(d)
                if n.startswith("_") or not os.path.isdir(d):
                    continue
                v, m = os.path.join(d, "narration.wav"), os.path.join(d, "narration_music.wav")
                if not os.path.exists(v):
                    continue
                flagged = 0
                rp = os.path.join(d, "report.txt")
                if os.path.exists(rp):
                    flagged = sum(1 for l in open(rp, encoding="utf-8", errors="replace") if re.match(r"\s+\d+:", l))
                L.append({"name": n, "dir": d, "voice": v, "music": m if os.path.exists(m) else "",
                          "seconds": round(tools._wav_len(v)), "time": _mtime(v), "flagged": flagged,
                          "lang": __import__("story").narration_lang(d),
                          "script": os.path.join(d, "script.txt")})
        out["narrations"] = sorted(L, key=lambda x: -x["time"])[:200]
    if kind in ("all", "pictures"):
        sets = []
        for d in glob.glob(os.path.join(cfg["pictures_dir"], "*")):
            ims = sorted(f for f in glob.glob(os.path.join(d, "*.png")) if not os.path.basename(f).startswith("_old_"))
            if ims:
                sj = os.path.join(d, "shots.json")
                shots = json.load(open(sj, encoding="utf-8")).get("shots", []) if os.path.exists(sj) else []
                sets.append({"name": os.path.basename(d), "dir": d, "images": ims[:60], "time": max(map(_mtime, ims)),
                             "shots": [{k: x.get(k) for k in ("file", "start", "end", "text")} for x in shots]})
        singles = sorted([f for f in glob.glob(os.path.join(cfg["images_dir"], "*", "*"))
                          if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))], key=_mtime, reverse=True)
        art = []
        if cfg.get("artwork_dir") and os.path.isdir(cfg["artwork_dir"]):
            art = sorted([f for f in glob.glob(os.path.join(cfg["artwork_dir"], "*"))
                          if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))], key=_mtime, reverse=True)
        out["picture_sets"] = sorted(sets, key=lambda x: -x["time"])
        out["images"] = singles[:120]
        out["artwork"] = art[:120]
    if kind in ("all", "videos"):
        vids = []
        for f in glob.glob(os.path.join(cfg["videos_dir"], "*.mp4")):
            vids.append({"file": f, "kind": "video", "time": _mtime(f)})
        for f in glob.glob(os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), "*.mp4")):
            if not f.endswith(".part.mp4"):
                vids.append({"file": f, "kind": "episode", "time": _mtime(f)})
        for f in glob.glob(os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), cfg.get("layout", {}).get("episode_reels", "reels"), "*.mp4")):
            vids.append({"file": f, "kind": "reel", "time": _mtime(f)})
        out["videos"] = sorted(vids, key=lambda x: -x["time"])[:150]
    if kind in ("all", "scripts"):
        sc = sorted([f for f in glob.glob(os.path.join(cfg["scripts_dir"], "*.txt"))
                     if not re.search(r"(_old_\d+|_full|\.en)\.txt$", f)], key=_mtime, reverse=True)
        out["scripts"] = []
        for f in sc[:200]:                                   # a short preview only - the page loads the text when used
            with open(f, encoding="utf-8", errors="replace") as fh:
                head = fh.read(600)
            out["scripts"].append({"name": os.path.basename(f)[:-4], "file": f, "time": _mtime(f),
                                   "preview": " ".join(l for l in head.splitlines() if l.strip() and l.strip() != "(...)")[:160]})
    if kind in ("all", "episodes"):
        out["episodes"] = episodes(cfg)
        # Build the automatic narration cadence index as the user browses episodes.
        # Profiles come from script + timing metadata; this does not rate audio quality.
        try:
            import pacing
            pacing.refresh(cfg, out["episodes"])
        except Exception:
            pass
    return out


def episodes(cfg):
    """one entry per episode: everything that belongs to it, wherever it lives"""
    eps = []
    for d in glob.glob(os.path.join(cfg["episodes_dir"], "*")):
        n = os.path.basename(d)
        if n.startswith("_") or not os.path.isdir(d):
            continue
        o = os.path.join(d, cfg.get("episode_output_name", "Output"))
        vids = [f for f in glob.glob(os.path.join(o, "*.mp4")) if not f.endswith((".part.mp4",)) and "_preview" not in f
                and not os.path.basename(f).lower().startswith("preview")]
        vids.sort(key=_mtime, reverse=True)
        reels = sorted(f for f in glob.glob(os.path.join(o, cfg.get("layout", {}).get("episode_reels", "reels"), "*.mp4")) if not f.endswith(".part.mp4"))
        pics = [f for f in glob.glob(os.path.join(d, "*")) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
        nar = tools.narration_dir(cfg, n, existing=True)
        sp = os.path.join(cfg["scripts_dir"], tools.slug(n) + ".txt")
        times = [_mtime(f) for f in vids + reels + pics] or [_mtime(d)]
        eps.append({"name": n, "dir": d, "video": vids[0] if vids else "", "reels": reels,
                    "art": pics[0] if pics else "", "narration": nar if os.path.isdir(nar) else "",
                    "script": sp if os.path.exists(sp) else (os.path.join(d, "script.txt") if os.path.exists(os.path.join(d, "script.txt")) else ""),
                    "checked": os.path.exists(os.path.splitext(vids[0])[0] + ".check.json") if vids else False,
                    "lang": __import__("story").narration_lang(nar) if os.path.isdir(nar) else "hi",
                    "time": max(times)})
    return sorted(eps, key=lambda x: -x["time"])


def library_stamp(cfg):
    """a cheap fingerprint of what exists - the page asks for it every few seconds and reloads only when it changed"""
    pats = [os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), "*.mp4"),
            os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), cfg.get("layout", {}).get("episode_reels", "reels"), "*.mp4"),
            os.path.join(tools.narrations_root(cfg), "*", "narration.wav"),
            os.path.join(cfg.get("narrations_dir") or os.path.join(cfg["sukoonvoice_media"], "narrations"), "*", "narration.wav"),
            os.path.join(cfg["scripts_dir"], "*.txt"), os.path.join(cfg["pictures_dir"], "*", "*.png"),
            os.path.join(cfg["images_dir"], "*", "*"),
            os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), "*.check.json"),
            os.path.join(cfg["episodes_dir"], "*", cfg.get("episode_output_name", "Output"), cfg.get("layout", {}).get("episode_reels", "reels"), "*.check.json")]
    n, t = 0, 0.0
    for p in pats:
        for f in glob.glob(p):
            n += 1
            t = max(t, _mtime(f))
    return {"stamp": f"{n}-{int(t)}"}


def remove_item(cfg, path):
    """'Remove' = moved to an _old folder next to it (never deleted); returns where it went"""
    if not path or not os.path.exists(path):
        raise ValueError("not found")
    parent = os.path.dirname(path.rstrip("\\/"))
    dst_dir = os.path.join(parent, "_old")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(path.rstrip("\\/")))
    shutil.move(path, dst)
    if not os.path.isdir(dst):                               # a file: its small companions go with it
        base, dbase = os.path.splitext(path)[0], os.path.splitext(dst)[0]
        for side in (".story.json", "_caption.txt", ".check.json", ".reel_info.json", ".prompt.txt"):
            if os.path.exists(base + side):
                shutil.move(base + side, dbase + side)
    return dst


def open_path(p):
    if WIN:
        if p.lower().endswith(".bat"):
            subprocess.Popen(["cmd", "/c", "start", "", p], cwd=os.path.dirname(p))
        else:
            os.startfile(p if os.path.isdir(p) else os.path.dirname(p) if not os.path.exists(p) else p)
    else:
        subprocess.Popen(["xdg-open", p])


def system_action(action, cfg, jobs, model_key=None, backend=None):
    if action == "start_comfy":
        if tools.comfy_running(cfg):
            return {"ok": True, "msg": "ComfyUI is already running"}
        import threading
        threading.Thread(target=lambda: tools.start_comfy(cfg, lambda s: None), daemon=True).start()
        return {"ok": True, "msg": "ComfyUI is starting (about 1 minute)"}
    if action == "free_gpu":
        if jobs and any(j["status"] == "running" for j in jobs.jobs):
            return {"ok": False, "msg": "A HubGPT job is running. Stop it and wait until it finishes before unloading GPU models."}
        log = []
        tools.free_gpu({"cfg": cfg, "say": log.append, "include_llm": True})
        success = not any(("failed" in x.lower() or "could not" in x.lower()) for x in log)
        return {"ok": success, "msg": " ".join(x.strip() for x in log) or "No local models were loaded."}
    if action == "start_ollama":
        if jobs and any(j["status"] == "running" for j in jobs.jobs):
            return {"ok": False, "msg": "A HubGPT job is running. Wait for it to finish before changing local AI servers."}
        url = cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
        try:
            data = json.loads(tools._get(url + "/api/tags", 2)).get("models", [])
            return {"ok": True, "started": False,
                    "msg": f"Ollama is already running with {len(data)} model(s). Choose a model below."}
        except OSError:
            pass
        exe = cfg.get("ollama_exe") or shutil.which("ollama")
        if not exe or not os.path.isfile(exe):
            return {"ok": False, "msg": f"Ollama executable not found: {exe or 'ollama'}"}
        log_path = os.path.join(cfg["hub_logs_root"], "ollama.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        env = os.environ.copy()
        env["OLLAMA_MODELS"] = cfg.get("ollama_models", r"D:\AI\ollama_models")
        try:
            with open(log_path, "ab") as output:
                proc = subprocess.Popen([exe, "serve"], cwd=os.path.dirname(exe), stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=subprocess.STDOUT, env=env,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            return {"ok": False, "msg": f"Could not start Ollama: {e}"}
        return {"ok": True, "started": True,
                "msg": f"Starting Ollama (PID {proc.pid}). HubGPT will list its installed models when the API is ready. Log: {log_path}"}
    return {"ok": False, "msg": f"unknown action {action}"}
