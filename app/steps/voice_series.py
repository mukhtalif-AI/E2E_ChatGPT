"""steps/voice_series.py - series voices and voice training. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4c).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


# ------------------------------------------------------------------ your voice per series (train one per series)
SERIES_DEFAULT = {"sukoon": ("सुकून", "calm, sleep, counselling"), "kisse": ("किस्से", "emotional stories"),
                  "rahasya": ("रहस्य", "thriller, mystery"), "bhay": ("भय", "horror"),
                  "romanch": ("रोमांच", "romance / sensual"), "kareeb": ("करीब", "ASMR, close and soft"),
                  "gyaan": ("ज्ञान", "informational")}


def _series_file(cfg):
    return os.path.join(cfg["sukoonvoice_media"], "series.json")


def series_load(cfg):
    try:
        d = json.load(open(_series_file(cfg), encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("series", {})
    for k, (n, a) in SERIES_DEFAULT.items():
        d["series"].setdefault(k, {"name": n, "about": a})
    d.setdefault("episodes", {})
    return d


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(str(s)))[0].lower())


def episode_series_of(ep, d):
    e = {_norm(k): v for k, v in d["episodes"].items()}
    return e.get(_norm(ep)) or ("sukoon" if "sukoon" in ep.lower() else "")


def genre_voice_sets(cfg, gid, b):
    """a series' OWN delivery (never the सुकून settings): its trained voice (or your plain voice clone when it has
    none yet - not the सुकून add-on), its pace and pauses, its expressiveness"""
    G = tools.GENRES[gid]
    run = series_model(cfg, gid) or (series_model(cfg, "rahasya") if gid == "bhay" else "")
    sets = ["with_music=false", "model=" + json.dumps(run or "base"), "match=false", "room_tone=false",
            "pace_style=" + json.dumps(gid),
            "acoustic_context=" + json.dumps(bool(b.get("acoustic_context", True))),
            f"exaggeration={float(b.get('exaggeration') or G['ex'])}", f"temperature={G['temp']}"]
    base = {"pause_comma": .35, "pause_line": 1.0, "pause_sentence": 1.1, "pause_dots": 1.2, "pause_marker": 1.6,
            "pause_paragraph": 2.2}
    k = float(b.get("pause_scale") or G["pause"])
    sets += [f"{p}={round(v * k, 3)}" for p, v in base.items()]
    if b.get("takes"):
        sets.append(f"takes={int(b['takes'])}")
    st, tn = G.get("drama", ("horror", 1.7))
    if b.get("dramatic", True):          # the भय way for every series: held silences in the series' own kind of tension
        sets += ["dramatic=true", f"drama_style={json.dumps(st)}",
                 f"pause_tension={round(tn * min(1.3, max(0.8, k / G['pause'])), 2)}"]
    if b.get("emotions", True):
        sets.append("emotions=true")
    return sets, run


def series_model(cfg, sid):
    """the newest finished voice trained for this series (run folder name), or '' """
    roots = (tools.voice_model_root(cfg), cfg.get("voice_training_dir", os.path.join(cfg["sukoonvoice_media"], "training")))
    runs = sorted((d for tr in roots for d in glob.glob(os.path.join(tr, "run_*")) if f"_series_{sid}" in os.path.basename(d)
                   and "_smoke" not in d and os.path.exists(os.path.join(d, "best.pt"))), key=tools._mtime, reverse=True)
    return os.path.basename(runs[0]) if runs else ""


def voice_series(cfg):
    """recordings (minutes each) + series (minutes, trained voice) for the Settings card"""
    d = series_load(cfg)
    mins = {}
    mp = os.path.join(cfg.get("voice_dataset_dir", os.path.join(cfg["sukoonvoice_media"], "dataset")), "metadata.jsonl")
    if os.path.exists(mp):
        for ln in open(mp, encoding="utf-8"):
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            mins[m["episode"]] = mins.get(m["episode"], 0) + float(m.get("dur") or 0) / 60
    eps = [{"name": k, "minutes": round(v, 1), "series": episode_series_of(k, d)} for k, v in sorted(mins.items(), key=lambda kv: -kv[1])]
    out = []
    for sid, info in d["series"].items():
        m = sum(e["minutes"] for e in eps if e["series"] == sid)
        run = series_model(cfg, sid)
        if sid == "sukoon" and not run:
            run = "(your current Sukoon voice)"
        out.append({"id": sid, "name": info.get("name", sid), "about": info.get("about", ""), "minutes": round(m, 1),
                    "trained": run})
    return {"series": out, "recordings": eps}


def voice_series_save(cfg, episodes, add=None):
    d = series_load(cfg)
    d["episodes"] = {_norm(k): v for k, v in d["episodes"].items()}
    for ep, sid in (episodes or {}).items():
        if sid:
            d["episodes"][_norm(ep)] = sid
        else:
            d["episodes"].pop(_norm(ep), None)
    if add and add.get("id") and add.get("name"):                   # a new series of your own
        sid = re.sub(r"[^a-z0-9]+", "", add["id"].lower())[:20]
        if sid:
            d["series"][sid] = {"name": add["name"], "about": add.get("about", "")}
    p = _series_file(cfg)
    json.dump(d, open(p + ".part", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(p + ".part", p)
    return voice_series(cfg)


def _voice_cfg_path(cfg):
    return os.path.join(cfg["sukoonvoice_dir"], "config.json")


def voice_scan(cfg, folder):
    """every .aup3 under a folder (and its subfolders): name, size, imported for training yet, its series"""
    if not folder or not os.path.isdir(folder):
        raise ValueError("folder not found")
    d = series_load(cfg)
    proj = cfg.get("voice_projects_dir", os.path.join(cfg["sukoonvoice_media"], "dataset", "projects"))
    have = {f[:-6] for f in os.listdir(proj)} if os.path.isdir(proj) else set()
    mp = os.path.join(cfg.get("voice_dataset_dir", os.path.join(cfg["sukoonvoice_media"], "dataset")), "metadata.jsonl")
    if os.path.exists(mp):
        for ln in open(mp, encoding="utf-8"):
            try:
                have.add(_norm(json.loads(ln)["episode"]))
            except Exception:
                pass
    out = []
    for dp, dn, fn in os.walk(folder):
        dn[:] = [x for x in dn if not x.startswith(("_", "."))]
        for f in fn:
            if f.lower().endswith(".aup3"):
                p = os.path.join(dp, f)
                out.append({"name": f[:-5], "file": p, "mb": round(os.path.getsize(p) / 2 ** 20),
                            "imported": _norm(f) in have, "series": episode_series_of(f[:-5], d)})
        if len(out) > 1500:
            break
    vc = json.load(open(_voice_cfg_path(cfg), encoding="utf-8-sig"))
    return {"files": sorted(out, key=lambda x: x["name"].lower()),
            "folders": (vc.get("recordings") or {}).get("folders", [])}


def voice_add_folders(cfg, folder):
    """the voice engine imports .aup3 from these folders (only the folder itself - so every subfolder with
    projects is added too); its config.json keeps everything else"""
    p = _voice_cfg_path(cfg)
    vc = json.load(open(p, encoding="utf-8-sig"))
    R = vc.setdefault("recordings", {})
    fl = R.setdefault("folders", [])
    for dp, dn, fn in os.walk(folder):
        dn[:] = [x for x in dn if not x.startswith(("_", "."))]
        if any(f.lower().endswith(".aup3") for f in fn) and dp not in fl:
            fl.append(dp)
    shutil.copyfile(p, p + time.strftime(".before_%Y%m%d_%H%M%S.json"))
    json.dump(vc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return fl


def sv_dataset(ctx):
    """your recordings -> training lines (Whisper hears them; no script needed). Finished projects are skipped."""
    cfg = ctx["cfg"]
    return ctx["run_cmd"]([tools.sv_python(cfg), "-s", "-u", "run.py", "dataset"], cwd=cfg["sukoonvoice_dir"],
                          env={"SV_LOGNAME": f"hub_{ctx['job']['id']}.txt"}) == 0


def classify_recordings(ctx):
    """recordings without a series: the AI reads what you say in them and picks the series (you can change it)"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    d = series_load(cfg)
    mp = os.path.join(cfg.get("voice_dataset_dir", os.path.join(cfg["sukoonvoice_media"], "dataset")), "metadata.jsonl")
    texts = {}
    for ln in open(mp, encoding="utf-8"):
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        texts.setdefault(m["episode"], []).append(m.get("text", ""))
    for pj in glob.glob(os.path.join(cfg.get("voice_projects_dir", os.path.join(cfg["sukoonvoice_media"], "dataset", "projects")), "*.jsonl")):
        for ln in open(pj, encoding="utf-8"):
            try:
                m = json.loads(ln)
                texts.setdefault(m["episode"], []).append(m.get("text", ""))
            except Exception:
                pass
    todo = [e for e in texts if not episode_series_of(e, d)]
    say(f"   {len(texts)} recordings, {len(todo)} without a series")
    opts = "; ".join(f"{k} = {v.get('name')} ({v.get('about')})" for k, v in d["series"].items())
    n = 0
    for e in todo:
        if ctx["stopped"]():
            return False
        sample = " ".join(texts[e])[:2500]
        try:
            t, _ = story.ask(cfg, f"Which series does this Hindi voice recording belong to? Series: {opts}.\n"
                                  f"Recording '{e}' says:\n{sample}\n\nAnswer ONLY JSON: {{\"series\": \"<id>\"}}",
                             "auto", 0.1, True, say=say)
            sid = str(json.loads(t).get("series", "")).strip()
        except Exception as ex:
            say(f"   (could not decide {e}: {ex})"); continue
        if sid in d["series"]:
            d["episodes"][_norm(e)] = sid
            n += 1
            say(f"   {e} -> {d['series'][sid]['name']}")
    p = _series_file(cfg)
    json.dump(d, open(p + ".part", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(p + ".part", p)
    say(f"   {n} recordings sorted (change any in Settings > Your voice per series)")
    return True


def train_all_series(ctx, include_sukoon=False, min_minutes=10, force=False):
    """one voice per series that has enough recordings; series trained in the last day are skipped (unless force)"""
    cfg, say = ctx["cfg"], ctx["say"]
    vs = voice_series(cfg)
    ok = True
    for x in sorted(vs["series"], key=lambda x: -x["minutes"]):
        if ctx["stopped"]():
            return False
        if x["id"] == "sukoon" and not include_sukoon:
            continue
        if x["minutes"] < min_minutes:
            say(f"   {x['name']}: only {x['minutes']} min of recordings - skipped (needs {min_minutes})"); continue
        run = series_model(cfg, x["id"])
        if run and not force:
            rd = os.path.join(tools.voice_model_root(cfg), run)
            if time.time() - tools._mtime(rd) < 86400:
                say(f"   {x['name']}: trained today ({run}) - skipped"); continue
        say(f"\n   === training {x['name']} ({x['minutes']} min) ===")
        tools.free_gpu(ctx)
        ok = sv_train(ctx, x["id"]) and ok
    return ok


def sv_train(ctx, series, epochs=None):
    cfg = ctx["cfg"]
    cmd = [tools.sv_python(cfg), "-s", "-u", "run.py", "train", "--new", "--profile", f"series_{series}"]
    if epochs:
        cmd += ["--epochs", str(int(epochs))]
    env = {"SV_LOGNAME": f"hub_{ctx['job']['id']}.txt", "SV_MODEL_DIR": tools.voice_model_root(cfg),
           "SV_MODEL_BACKUP_DIR": cfg.get("voice_model_backup_dir", r"D:\AI\Mukhtalif_model")}
    return ctx["run_cmd"](cmd, cwd=cfg["sukoonvoice_dir"], env=env) == 0
